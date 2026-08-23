"""The council's snapshot bounds scale with the machine, correctly.

The bounds used to be five constants chosen for the smallest machine
anyone might run on, which refused a repository a 64 GB workstation
could hold twenty times over. Scaling them introduces exactly one new
way to be wrong, and it is the reason this file exists: **the per-member
bound and the total bound scale from DIFFERENT machines**, and on Linux
— where the daemon shares the host kernel — the two readings are equal,
so a test written on Linux cannot tell a correct implementation from one
that conflates them. Every pair below drives host and daemon memory
apart deliberately.

Marked ``councilCapacity`` so conftest's pin is lifted: these are the
tests whose subject IS the scaling, and they supply both readings
explicitly rather than reading the developer's laptop.
"""

import pytest

from vaibify.gui import agentCouncilCapacity, agentCouncilRunner
from vaibify.gui.routes import councilRoutes

pytestmark = pytest.mark.councilCapacity

I_GIGABYTE = 1024 * 1024 * 1024


class _FakeDaemon:
    """Reports a fixed daemon memory; nothing else is asked of it."""

    def __init__(self, iMemoryBytes):
        self._iMemoryBytes = iMemoryBytes

    def fdictReadDaemonCapacity(self):
        return {"iMemoryBytes": self._iMemoryBytes, "iCpuCount": 4}


def _fdictResolve(monkeypatch, iHostBytes, iDaemonBytes):
    """Resolve a capacity from an explicit host/daemon pair."""
    monkeypatch.setattr(
        agentCouncilCapacity, "fiReadHostMemoryBytes", lambda: iHostBytes)
    return agentCouncilCapacity.fdictResolveCouncilCapacity(
        _FakeDaemon(iDaemonBytes))


def testASmallMachineGetsExactlyTheDeclaredFloors(monkeypatch):
    """Scaling may only ever RAISE a bound, never lower one.

    A repository that snapshotted yesterday must not refuse today
    because the researcher's machine is modest: the floors are what the
    design was reviewed against, and they are the answer for anything
    below them.
    """
    dictCapacity = _fdictResolve(monkeypatch, I_GIGABYTE, I_GIGABYTE)
    dictFloor = agentCouncilCapacity.fdictFloorCouncilCapacity()
    for sBoundName in (
        "iMaxSnapshotFileCount", "iMaxSnapshotMemberBytes",
        "iMaxSnapshotTotalBytes", "iRunnerMemoryBytes",
        "iRunnerWorkingTreeBytes", "iRunnerScratchBytes",
    ):
        assert dictCapacity[sBoundName] == dictFloor[sBoundName], sBoundName


def testAnUnmeasurableMachineGetsTheFloorsRatherThanZero(monkeypatch):
    """Unknown is not zero. A machine we cannot read is not a tiny one."""
    dictCapacity = _fdictResolve(monkeypatch, 0, 0)
    assert dictCapacity == {
        **agentCouncilCapacity.fdictFloorCouncilCapacity(),
        "iHostMemoryBytes": 0, "iDaemonMemoryBytes": 0, "bMeasured": False,
    }


def testTheMemberBoundFollowsTheHostNotTheDaemon(monkeypatch):
    """Falsification pair, half one: host RAM moves the per-member cap.

    Kills: sourcing the per-member bound from the daemon reading.

    The hub materialises each member in ITS OWN memory to hash and
    re-archive it, so the researcher's RAM is the bound. Driven with a
    large host behind a floor-sized daemon — the macOS shape, where a
    16 GB laptop runs a 7.7 GB Docker VM — so an implementation that
    read the daemon here would report the floor and fail.
    """
    dictSmallHost = _fdictResolve(monkeypatch, 8 * I_GIGABYTE, I_GIGABYTE)
    dictLargeHost = _fdictResolve(monkeypatch, 32 * I_GIGABYTE, I_GIGABYTE)
    assert dictLargeHost["iMaxSnapshotMemberBytes"] > dictSmallHost[
        "iMaxSnapshotMemberBytes"], (
        "quadrupling host RAM did not raise the per-member cap; the "
        "bound is not following the machine that holds the bytes")


def testTheMemberBoundIgnoresTheDaemonEntirely(monkeypatch):
    """Falsification pair, half two: daemon memory must NOT move it.

    Kills: sourcing the per-member bound from the daemon reading.

    The other half of the pair, and the one a Linux-only test cannot
    write: with the host fixed and the daemon multiplied 32-fold, an
    implementation that conflated the two would raise the member cap
    here. The total bound is asserted to move in the same breath, so
    this cannot pass by scaling nothing at all.
    """
    dictSmallDaemon = _fdictResolve(monkeypatch, 8 * I_GIGABYTE, I_GIGABYTE)
    dictLargeDaemon = _fdictResolve(
        monkeypatch, 8 * I_GIGABYTE, 32 * I_GIGABYTE)
    assert dictLargeDaemon["iMaxSnapshotMemberBytes"] == dictSmallDaemon[
        "iMaxSnapshotMemberBytes"], (
        "the per-member cap moved with the DAEMON's memory; a member is "
        "held in the hub process, and this bound is a host bound")
    assert dictLargeDaemon["iMaxSnapshotTotalBytes"] > dictSmallDaemon[
        "iMaxSnapshotTotalBytes"], (
        "nothing scaled with the daemon at all, so the assertion above "
        "proves nothing")


def testTheTotalBoundFollowsTheDaemonNotTheHost(monkeypatch):
    """Falsification pair: the snapshot total is a DAEMON bound.

    Kills: sourcing the total bound from the host reading.

    The snapshot is copied into a tmpfs inside each runner and tmpfs
    pages are charged to that container's memory cgroup, so what the
    daemon has is what bounds it. A host multiplied 32-fold must move
    nothing here.
    """
    dictSmallHost = _fdictResolve(monkeypatch, I_GIGABYTE, 32 * I_GIGABYTE)
    dictLargeHost = _fdictResolve(
        monkeypatch, 32 * I_GIGABYTE, 32 * I_GIGABYTE)
    assert dictLargeHost["iMaxSnapshotTotalBytes"] == dictSmallHost[
        "iMaxSnapshotTotalBytes"], (
        "the snapshot total moved with HOST memory; the bytes land in a "
        "runner's tmpfs, which the daemon pays for")


def testAMemberCanNeverExceedTheWholeSnapshot(monkeypatch):
    """A big host behind a small daemon must not admit an unshippable file.

    Kills: dropping the member-versus-total clamp.

    The two bounds scale from different machines, so this combination
    is reachable in production and not merely in a test: a researcher
    with a large laptop and a default Docker Desktop allocation. A
    member the runner could never receive must not be admitted by the
    hub.
    """
    dictCapacity = _fdictResolve(monkeypatch, 512 * I_GIGABYTE, I_GIGABYTE)
    assert dictCapacity["iMaxSnapshotMemberBytes"] <= dictCapacity[
        "iMaxSnapshotTotalBytes"]


def testAMemberBoundIsCappedEvenOnAnEnormousHost(monkeypatch):
    """Past the ceiling a file is a dataset, whatever the machine holds."""
    dictCapacity = _fdictResolve(
        monkeypatch, 4096 * I_GIGABYTE, 4096 * I_GIGABYTE)
    assert dictCapacity["iMaxSnapshotMemberBytes"] == (
        agentCouncilCapacity.I_CEILING_SNAPSHOT_MEMBER_BYTES)


@pytest.mark.parametrize("iDaemonGigabytes", [1, 4, 16, 64, 256])
def testTheRunnerDiskBoundStaysBelowItsMemoryBound(
        monkeypatch, iDaemonGigabytes):
    """The invariant the runner refuses to be built without.

    Kills: scaling the working tree without scaling the memory limit.

    tmpfs pages are charged to the memory cgroup, so a working tree at
    the memory limit replaces the disk bound with an out-of-memory
    kill. Scaling introduced a way to break this that the fixed
    constants could not, which is why it is checked across the range
    rather than at one point.
    """
    dictCapacity = _fdictResolve(
        monkeypatch, 16 * I_GIGABYTE, iDaemonGigabytes * I_GIGABYTE)
    dictLimits = agentCouncilRunner.fdictBuildDefaultRunnerLimits(
        dictCapacity)
    agentCouncilRunner._fnValidateRunnerLimits(dictLimits)
    assert dictLimits["iWorkingTreeBytes"] >= dictCapacity[
        "iMaxSnapshotTotalBytes"], (
        "the runner's working tree is smaller than the largest snapshot "
        "the bounds admit; the copy-in would overflow the tmpfs it was "
        "sized against")


def testTheRunnerDivisorMatchesTheParticipantCap():
    """The two copies of "how many runners at once" must agree.

    agentCouncilCapacity cannot import councilRoutes — a gui module
    importing a route module is the wrong direction — so the divisor is
    a second copy of the participant cap. This is the binding that keeps
    the copy honest: raise the cap without raising the divisor and every
    runner is sized for a council smaller than the one that can be
    convened.
    """
    assert agentCouncilCapacity.I_ASSUMED_CONCURRENT_RUNNERS == (
        councilRoutes.I_MAX_PARTICIPANTS)


def testTheHostReadingAnswersOnThisMachine():
    """The three-source host reading must actually work where it runs.

    Kills: a host reading that silently returns 0 everywhere.

    ``SC_PHYS_PAGES`` is an OPTIONAL sysconf name — present in Apple's
    system Python 3.9 and absent from an Anaconda Python 3.9 on the
    same machine — so a single-source implementation returns 0 for a
    real user and falls back to the floor while looking correct. This
    asserts a plausible physical figure on whatever interpreter and
    platform CI happens to use.
    """
    iHostMemoryBytes = agentCouncilCapacity.fiReadHostMemoryBytes()
    assert iHostMemoryBytes >= 256 * 1024 * 1024, (
        "the host memory reading failed on this interpreter/platform; "
        "every scaled bound silently collapses to its floor")
    assert iHostMemoryBytes < 1024 * I_GIGABYTE


def testTheProbePrunesExactlyWhatTheSnapshotExcludes():
    """The two copies of the exclusion policy must not drift.

    Kills: removing a component from the probe's pruned set.

    ``agentCouncilContext`` owns the policy; the probe carries a second
    copy because a program that runs INSIDE a container cannot import
    from the host environment — the same boundary that makes
    introspectionScript duplicate dataLoaders. Spelled twice, pinned
    here.

    Drift in either direction is a real defect and neither is
    hypothetical. Under-pruning was measured: before the probe pruned
    anything, a real research repository weighed 463 MB and its largest
    "file" was a 315 MB git pack, so a council was refused over an
    object store the snapshot never carries and the pack was offered to
    the researcher for exclusion. Over-pruning is the mirror: the
    pre-flight would promise a council that the capture then refuses.
    """
    from vaibify.docker import dockerConnection
    from vaibify.gui.agentCouncilContext import (
        DICT_EXCLUDED_COMPONENT_REASONS,
    )
    assert set(
        dockerConnection._TUPLE_REPOSITORY_WEIGHT_PRUNED_COMPONENTS,
    ) == set(DICT_EXCLUDED_COMPONENT_REASONS), (
        "the pre-flight probe and the snapshot capture disagree about "
        "what a snapshot contains; the pre-flight's answer is about a "
        "different repository than the one that would be captured")


def testThePrunedComponentsReachTheProbeProgram():
    """The pinned set must actually be IN the program that runs.

    Kills: pinning the constant while the program ignores it.

    The test above compares two Python constants, which stays green if
    the program text never mentions either — and the program is a
    string, so nothing else would notice. This asserts the walk really
    prunes.
    """
    from vaibify.docker import dockerConnection
    sProgram = dockerConnection._DICT_TYPED_READ_PROGRAMS[
        dockerConnection.S_TYPED_READ_REPOSITORY_WEIGHT]
    assert "dirnames[:]" in sProgram, (
        "the probe program does not prune directories at all")
    assert "'.git'" in sProgram or '".git"' in sProgram, (
        "the pruned set never reached the program text")
