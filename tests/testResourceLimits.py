"""Desired limits, running limits, the change planner, and limit drift.

Three facts measured on a real daemon (Docker 28.3.3, cgroup v2, no
swap; 2026-10-08) are the oracle for the planner, and each row below
cites the one it rests on:

1. A container started with no limits shows Memory=0, MemorySwap=0,
   NanoCpus=0, CpuQuota=0: zero means unlimited.
2. ``--memory 256m`` without ``--memory-swap`` gives MemorySwap = 2 x
   Memory.
3. ``docker update`` refuses a memory raise above the existing swap
   limit unless ``--memory-swap`` moves with it; ``--memory 0`` and
   ``--cpus 0`` are silently ignored; a memory limit cannot be
   introduced on an unlimited container live, but ``--cpus`` can.

And one rule the researcher set (D3): a change that could kill a
process -- lowering memory -- is never applied live.

Because of fact 2, vaibify creates a capped container with
``--memory-swap`` equal to ``--memory`` (2026-10-09): with Docker's
default, a 1 GiB allocation ran to completion under a 256 MB cap on a
CI runner that had swap, so the cap did not mean the same thing on
every computer.

The desired limits are pinned against ``flistBuildRunArgs`` itself: the
equivalence table asserts that the resolver produces exactly the
``--cpus`` and ``--memory`` a container is created with, so the planner
and the drift check compare against what ``docker run`` really asked
for.
"""

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from vaibify.config import resourceLimits as limits
from vaibify.docker.containerManager import flistBuildRunArgs
from tests.testContainerManager import _fConfigMinimal


I_GIGABYTE = 2 ** 30
I_NANO = 10 ** 9


def _fconfig(iCpuLimit=0, fMemoryLimitGigabytes=0.0):
    config = _fConfigMinimal()
    config.iCpuLimit = iCpuLimit
    config.fMemoryLimitGigabytes = fMemoryLimitGigabytes
    return config


def _fsArgumentAfter(saArgs, sFlag):
    return saArgs[saArgs.index(sFlag) + 1] if sFlag in saArgs else None


# ---------------------------------------------------------------------
# Desired limits: one authority, equal to what docker run is given
# ---------------------------------------------------------------------

@pytest.mark.parametrize("iCpuLimit,fMemory", [
    (0, 0.0), (1, 1.0), (4, 0.5), (999, 0.333333333), (2, 6.0),
    (0, 0.25), (3, 1.75),
])
@patch(
    "vaibify.docker.containerManager.flistConfigureX11Args",
    return_value=[],
)
def testTheResolverEqualsWhatDockerRunIsGiven(mockX11, iCpuLimit, fMemory):
    config = _fconfig(iCpuLimit, fMemory)
    saArgs = flistBuildRunArgs(config)
    assert _fsArgumentAfter(saArgs, "--cpus") == str(
        limits.fiResolveCpuCount(config))
    sMemory = _fsArgumentAfter(saArgs, "--memory")
    assert sMemory == limits.fsResolveMemoryArgument(config)
    assert _fsArgumentAfter(saArgs, "--memory-swap") == sMemory
    if sMemory is None:
        assert limits.fiResolveMemoryBytes(config) is None
    else:
        assert limits.fiResolveMemoryBytes(config) == int(
            float(sMemory[:-1]) * I_GIGABYTE)


def testZeroCpuInTheFileMeansAllCoresButOne():
    config = _fconfig(iCpuLimit=0)
    assert limits.fiResolveCpuCount(config, iHostCores=12) == 11
    assert limits.fiResolveCpuCount(config, iHostCores=1) == 1


def testACpuLimitIsClampedToTheHostsCores():
    assert limits.fiResolveCpuCount(_fconfig(iCpuLimit=64), iHostCores=8) == 8


# ---------------------------------------------------------------------
# Running limits: tagged, never a bare zero
# ---------------------------------------------------------------------

def testAnUnlimitedContainerReadsUnlimitedNotZero():
    dictRunning = limits.fdictParseRunningLimits({
        "Memory": 0, "MemorySwap": 0, "NanoCpus": 0, "CpuQuota": 0,
        "CpuPeriod": 0})
    assert dictRunning["dictMemory"]["sKind"] == limits.S_KIND_UNLIMITED
    assert dictRunning["dictSwap"]["sKind"] == limits.S_KIND_UNSET
    assert dictRunning["dictCpu"]["sKind"] == limits.S_KIND_UNLIMITED


def testFiniteLimitsCarryTheirValues():
    dictRunning = limits.fdictParseRunningLimits({
        "Memory": 256 * 2 ** 20, "MemorySwap": 512 * 2 ** 20,
        "NanoCpus": 2 * I_NANO})
    assert dictRunning["dictMemory"] == {
        "sKind": limits.S_KIND_FINITE, "iBytes": 256 * 2 ** 20}
    assert dictRunning["dictSwap"] == {
        "sKind": limits.S_KIND_FINITE, "iBytes": 512 * 2 ** 20}
    assert dictRunning["dictCpu"]["sKind"] == limits.S_KIND_FINITE
    assert dictRunning["dictCpu"]["iNanoCpus"] == 2 * I_NANO


def testUnlimitedSwapAndAQuotaFormAreTaggedAsSuch():
    dictRunning = limits.fdictParseRunningLimits({
        "Memory": I_GIGABYTE, "MemorySwap": -1, "NanoCpus": 0,
        "CpuQuota": 150000, "CpuPeriod": 100000})
    assert dictRunning["dictSwap"]["sKind"] == limits.S_KIND_UNLIMITED
    assert dictRunning["dictCpu"]["sKind"] == limits.S_KIND_CPU_QUOTA
    assert dictRunning["dictCpu"]["fCpus"] == 1.5


def testAnUnreadableContainerIsUnknownThroughout():
    dictRunning = limits.fdictUnknownRunningLimits()
    for sKey in ("dictMemory", "dictSwap", "dictCpu"):
        assert dictRunning[sKey]["sKind"] == limits.S_KIND_UNKNOWN


# ---------------------------------------------------------------------
# The planner: desired against RUNNING, one row per table line
# ---------------------------------------------------------------------

def _fdictRunning(iMemory=None, iSwap=None, iNanoCpus=None, bUnknown=False):
    if bUnknown:
        return limits.fdictUnknownRunningLimits()
    return limits.fdictParseRunningLimits({
        "Memory": iMemory or 0, "MemorySwap": iSwap or 0,
        "NanoCpus": iNanoCpus or 0})


def _fdictEntry(listPlan, sField):
    return next(d for d in listPlan if d["sField"] == sField)


def _fsMemoryAction(dictRunning, iDesiredBytes):
    listPlan = limits.flistPlanLimitChanges(
        dictRunning, {"iMemoryBytes": iDesiredBytes, "iCpuCount": 2})
    return _fdictEntry(listPlan, limits.S_FIELD_MEMORY)["sAction"]


def _fsCpuAction(dictRunning, iDesiredCpuCount):
    listPlan = limits.flistPlanLimitChanges(
        dictRunning, {"iMemoryBytes": None, "iCpuCount": iDesiredCpuCount})
    return _fdictEntry(listPlan, limits.S_FIELD_CPU)["sAction"]


def testMemoryUnknownWaitsForTheNextStart():
    assert _fsMemoryAction(
        _fdictRunning(bUnknown=True), 4 * I_GIGABYTE) == (
        limits.S_ACTION_NEXT_START)


def testMemoryUnlimitedToUnlimitedIsNoChange():
    assert _fsMemoryAction(_fdictRunning(), None) == limits.S_ACTION_NONE


@pytest.mark.falsification
def testIntroducingAMemoryLimitWaitsForTheNextStart():
    """Kills: classifying unlimited-to-finite as a live raise (fact 3)."""
    assert _fsMemoryAction(_fdictRunning(), 4 * I_GIGABYTE) == (
        limits.S_ACTION_NEXT_START)


def testRemovingAMemoryLimitWaitsForTheNextStart():
    assert _fsMemoryAction(
        _fdictRunning(iMemory=I_GIGABYTE, iSwap=2 * I_GIGABYTE), None) == (
        limits.S_ACTION_NEXT_START)


def testAMemoryRaiseWithKnownSwapAppliesLive():
    assert _fsMemoryAction(
        _fdictRunning(iMemory=I_GIGABYTE, iSwap=2 * I_GIGABYTE),
        4 * I_GIGABYTE) == limits.S_ACTION_APPLY_LIVE


def testTheSameMemoryLimitIsNoChange():
    assert _fsMemoryAction(
        _fdictRunning(iMemory=I_GIGABYTE, iSwap=2 * I_GIGABYTE),
        I_GIGABYTE) == limits.S_ACTION_NONE


def testAMemoryDecreaseWaitsForTheNextStart():
    assert _fsMemoryAction(
        _fdictRunning(iMemory=4 * I_GIGABYTE, iSwap=8 * I_GIGABYTE),
        I_GIGABYTE) == limits.S_ACTION_NEXT_START


@pytest.mark.falsification
def testARaiseInTheFileThatLowersTheRunningLimitIsADecrease():
    """Kills: treating a save below the running limit as a raise.

    The file says 1 GB, the container was raised by hand to 6 GB, and
    the researcher saves 4 GB. Against the file that is a raise; against
    what is running it is a DECREASE, which can kill a process.
    """
    dictRunning = _fdictRunning(iMemory=6 * I_GIGABYTE, iSwap=12 * I_GIGABYTE)
    listPlan = limits.flistPlanLimitChanges(
        dictRunning, {"iMemoryBytes": 4 * I_GIGABYTE, "iCpuCount": 2})
    dictMemory = _fdictEntry(listPlan, limits.S_FIELD_MEMORY)
    assert dictMemory["sAction"] == limits.S_ACTION_NEXT_START
    assert dictMemory["iRunningBytes"] == 6 * I_GIGABYTE


def testCpuUnknownOrQuotaFormWaitsForTheNextStart():
    assert _fsCpuAction(_fdictRunning(bUnknown=True), 4) == (
        limits.S_ACTION_NEXT_START)
    dictQuota = limits.fdictParseRunningLimits({
        "NanoCpus": 0, "CpuQuota": 200000, "CpuPeriod": 100000})
    assert _fsCpuAction(dictQuota, 4) == limits.S_ACTION_NEXT_START


def testACpuCapOnAnUncappedContainerAppliesLive():
    assert _fsCpuAction(_fdictRunning(), 4) == limits.S_ACTION_APPLY_LIVE


def testRemovingACpuCapWaitsForTheNextStart():
    assert _fsCpuAction(_fdictRunning(iNanoCpus=4 * I_NANO), None) == (
        limits.S_ACTION_NEXT_START)


def testACpuChangeEitherWayAppliesLive():
    dictRunning = _fdictRunning(iNanoCpus=4 * I_NANO)
    assert _fsCpuAction(dictRunning, 8) == limits.S_ACTION_APPLY_LIVE
    assert _fsCpuAction(dictRunning, 2) == limits.S_ACTION_APPLY_LIVE
    assert _fsCpuAction(dictRunning, 4) == limits.S_ACTION_NONE


# ---------------------------------------------------------------------
# The swap rule
# ---------------------------------------------------------------------

@pytest.mark.parametrize("iSwap,iExpected", [
    (1 * I_GIGABYTE, 4 * I_GIGABYTE),
    (2 * I_GIGABYTE, 4 * I_GIGABYTE),
    (-1, -1),
])
def testARaiseSetsTheSwapLimitToTheNewMemoryLimit(iSwap, iExpected):
    listPlan = limits.flistPlanLimitChanges(
        _fdictRunning(iMemory=I_GIGABYTE, iSwap=iSwap),
        {"iMemoryBytes": 4 * I_GIGABYTE, "iCpuCount": 2})
    dictMemory = _fdictEntry(listPlan, limits.S_FIELD_MEMORY)
    assert dictMemory["sAction"] == limits.S_ACTION_APPLY_LIVE
    assert dictMemory["iSwapBytes"] == iExpected


def testAnUnsetOrUnknownSwapIsNeverGuessed():
    for dictRunning in (
        _fdictRunning(iMemory=I_GIGABYTE, iSwap=0),
        {"dictMemory": {"sKind": limits.S_KIND_FINITE, "iBytes": I_GIGABYTE},
         "dictSwap": {"sKind": limits.S_KIND_UNKNOWN, "iBytes": None},
         "dictCpu": {"sKind": limits.S_KIND_UNKNOWN}},
    ):
        listPlan = limits.flistPlanLimitChanges(
            dictRunning, {"iMemoryBytes": 4 * I_GIGABYTE, "iCpuCount": 2})
        dictMemory = _fdictEntry(listPlan, limits.S_FIELD_MEMORY)
        assert dictMemory["sAction"] == limits.S_ACTION_NEXT_START
        assert "swap" in dictMemory["sReason"]


@pytest.mark.falsification
def testALiveRaiseNeverLowersTheSwapLimit():
    """Kills: setting the swap limit to the new memory limit outright.

    A container created before swap was pinned may allow more swap than
    the raised memory limit. Lowering a running container's swap limit
    can kill a process that is using it, so the larger limit stays
    until the next start.
    """
    listPlan = limits.flistPlanLimitChanges(
        _fdictRunning(iMemory=I_GIGABYTE, iSwap=8 * I_GIGABYTE),
        {"iMemoryBytes": 4 * I_GIGABYTE, "iCpuCount": 2})
    dictMemory = _fdictEntry(listPlan, limits.S_FIELD_MEMORY)
    assert dictMemory["sAction"] == limits.S_ACTION_APPLY_LIVE
    assert dictMemory["iSwapBytes"] == 8 * I_GIGABYTE


# ---------------------------------------------------------------------
# Limit drift: running against the file, three-state
# ---------------------------------------------------------------------

def _flistDrift(dictRunning, config, iHostCores=12):
    return limits.flistDescribeLimitDrift(
        dictRunning, limits.fdictResolveDesiredLimits(config, iHostCores))


def testARunningLimitAboveTheFileSaysTheNextRestartLowersIt():
    listLines = _flistDrift(
        _fdictRunning(iMemory=6 * I_GIGABYTE, iSwap=6 * I_GIGABYTE,
                      iNanoCpus=11 * I_NANO),
        _fconfig(fMemoryLimitGigabytes=1.0))
    assert listLines == [
        "This container runs with a 6 GB memory limit, but vaibify.yml "
        "says 1 GB; the next Restart will apply 1 GB."]


def testARunningLimitBelowTheFileSaysWhatSavingWillDo():
    listLines = _flistDrift(
        _fdictRunning(iMemory=I_GIGABYTE, iSwap=I_GIGABYTE,
                      iNanoCpus=11 * I_NANO),
        _fconfig(fMemoryLimitGigabytes=6.0))
    assert listLines == [
        "This container runs with a 1 GB memory limit, but vaibify.yml "
        "says 6 GB; the next Restart will apply 6 GB."]


def testAMissingOrExtraLimitIsDescribed():
    listAdded = _flistDrift(
        _fdictRunning(iNanoCpus=11 * I_NANO), _fconfig(fMemoryLimitGigabytes=2.0))
    assert listAdded == [
        "This container runs with no memory limit, but vaibify.yml says "
        "2 GB; the next Restart will apply 2 GB."]
    listRemoved = _flistDrift(
        _fdictRunning(iMemory=I_GIGABYTE, iSwap=2 * I_GIGABYTE,
                      iNanoCpus=11 * I_NANO), _fconfig())
    assert listRemoved == [
        "This container runs with a 1 GB memory limit, but vaibify.yml "
        "sets none; the next Restart will remove it."]


def testAContainerThatCanSwapPastItsCapIsDescribed():
    listLines = _flistDrift(
        _fdictRunning(iMemory=I_GIGABYTE, iSwap=2 * I_GIGABYTE,
                      iNanoCpus=11 * I_NANO),
        _fconfig(fMemoryLimitGigabytes=1.0))
    assert listLines == [
        "This container can also use swap beyond its 1 GB memory limit, "
        "where a process slows down before it is killed; the next "
        "Restart removes that allowance."]


@pytest.mark.parametrize("iSwap", [I_GIGABYTE, -1, 0])
def testAPinnedUnlimitedOrUnsetSwapIsNotDescribed(iSwap):
    """Pinned is what a start creates; unlimited or unset may be all this
    computer's Docker can do, which a Restart would not change."""
    assert _flistDrift(
        _fdictRunning(iMemory=I_GIGABYTE, iSwap=iSwap, iNanoCpus=11 * I_NANO),
        _fconfig(fMemoryLimitGigabytes=1.0)) == []


def testACpuDifferenceIsDescribed():
    listLines = _flistDrift(
        _fdictRunning(iNanoCpus=8 * I_NANO), _fconfig(iCpuLimit=4))
    assert listLines == [
        "This container runs with 8 CPUs, but vaibify.yml asks for 4; "
        "the next Restart will apply 4."]


@pytest.mark.falsification
def testZeroCpusInTheFileIsAllCoresButOneNotDrift():
    """Kills: reading cpuLimit 0 in the file literally, as "no cap".

    The container was created with --cpus (cores - 1), so it runs with
    NanoCpus = 11e9 on a 12-core host. Reading the file's 0 as "no cap"
    would report that as drift on every container ever created.
    """
    assert _flistDrift(
        _fdictRunning(iNanoCpus=11 * I_NANO), _fconfig(iCpuLimit=0),
        iHostCores=12) == []


@pytest.mark.falsification
def testAnUnknownRunningLimitDeterminesNothing():
    """Kills: claiming drift when the running limits could not be read."""
    assert _flistDrift(
        _fdictRunning(bUnknown=True),
        _fconfig(iCpuLimit=4, fMemoryLimitGigabytes=1.0)) == []


def testAQuotaFormCpuDeterminesNothingAboutCpu():
    dictRunning = limits.fdictParseRunningLimits({
        "Memory": 0, "MemorySwap": 0, "NanoCpus": 0,
        "CpuQuota": 200000, "CpuPeriod": 100000})
    assert _flistDrift(dictRunning, _fconfig(iCpuLimit=4)) == []


# ---------------------------------------------------------------------
# Which fields a save changed, and what each change will do
# ---------------------------------------------------------------------

def testOnlyFieldsThatDifferFromTheFileAreChanges():
    config = _fconfig(iCpuLimit=4, fMemoryLimitGigabytes=1.0)
    assert limits.flistChangedLimitFields(config, 4, 1) == []
    assert limits.flistChangedLimitFields(config, 4, 2.0) == [
        limits.S_FIELD_MEMORY]
    assert limits.flistChangedLimitFields(config, 0, 1.0) == [
        limits.S_FIELD_CPU]
    assert limits.flistChangedLimitFields(config, None, None) == []


def testEveryChangeSaysItWaitsForTheNextStart():
    config = _fconfig(iCpuLimit=0, fMemoryLimitGigabytes=4.0)
    listOutcomes = limits.flistDescribeNextStartOutcomes(
        [limits.S_FIELD_MEMORY, limits.S_FIELD_CPU], config, iHostCores=12)
    assert [d["sOutcome"] for d in listOutcomes] == [
        limits.S_ACTION_NEXT_START] * 2
    assert listOutcomes[0]["sSentence"] == (
        "The memory limit in vaibify.yml is now 4 GB. It applies the next "
        "time the container starts.")
    assert listOutcomes[1]["sSentence"] == (
        "vaibify.yml now sets no CPU limit, so the container gets all "
        "cores but one (11). It applies the next time the container "
        "starts.")


def testRemovingTheMemoryLimitSaysSo():
    listOutcomes = limits.flistDescribeNextStartOutcomes(
        [limits.S_FIELD_MEMORY], _fconfig(), iHostCores=12)
    assert listOutcomes[0]["sSentence"] == (
        "vaibify.yml now sets no memory limit. It applies the next time "
        "the container starts.")


# ---------------------------------------------------------------------
# The one reader of a running container's limits
# ---------------------------------------------------------------------

def _fdictReadFrom(dictHostConfig=None, errorRaised=None):
    from unittest.mock import MagicMock
    from vaibify.gui import pipelineRunSlots
    connectionDocker = MagicMock()
    if errorRaised is not None:
        connectionDocker.fcontainerGetById.side_effect = errorRaised
    else:
        connectionDocker.fcontainerGetById.return_value = SimpleNamespace(
            reload=lambda: None, attrs={"HostConfig": dictHostConfig})
    return (
        pipelineRunSlots.fdictReadRunningLimits(connectionDocker, "cid-9f3"),
        pipelineRunSlots.fdictReadContainerLimits(connectionDocker, "cid-9f3"),
    )


def testTheReaderTagsEachStateAndKeepsTheConcurrentRunView():
    dictTagged, dictView = _fdictReadFrom({
        "Memory": 5 * I_GIGABYTE, "MemorySwap": 10 * I_GIGABYTE,
        "NanoCpus": I_NANO})
    assert dictTagged["dictMemory"]["sKind"] == limits.S_KIND_FINITE
    assert dictView == {"fCpuLimit": 1.0, "fMemoryGigabytes": 5.0}
    dictTagged, dictView = _fdictReadFrom({})
    assert dictTagged["dictMemory"]["sKind"] == limits.S_KIND_UNLIMITED
    assert dictView == {"fCpuLimit": None, "fMemoryGigabytes": None}


def testAFailedInspectIsUnknownNotUnlimited():
    dictTagged, dictView = _fdictReadFrom(errorRaised=ConnectionError("down"))
    assert dictTagged["dictMemory"]["sKind"] == limits.S_KIND_UNKNOWN
    assert dictTagged["dictCpu"]["sKind"] == limits.S_KIND_UNKNOWN
    assert dictView == {"fCpuLimit": None, "fMemoryGigabytes": None}
