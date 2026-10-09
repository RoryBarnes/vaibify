"""The memory watch: its record, its sampler, and the words it speaks.

Every fixture keeps the container NAME distinct from its Docker ID. The
record is keyed by name, because the owner map is; every incident is
labeled with an id, because a recreate under the same name is a
different container. A fixture where the two were equal once hid a
fatal bug in this codebase, so none of these do.

The expected values are the kernel's own counters as written into each
fixture (memory.events ``oom`` and ``oom_kill``) and the cAdvisor
working-set estimate (usage minus inactive_file); nothing here is read
back from the code under test.
"""

import asyncio
import logging
import threading
from datetime import datetime, timedelta, timezone

import pytest

from vaibify.docker import cgroupMemory
from vaibify.gui import containerMemorySampler as sampler
from vaibify.gui import containerMemoryWatch as watch
from vaibify.gui import hostIncidents
from vaibify.gui import pipelineState


S_NAME = "memory-lane-project"
S_ID_OLD = "0ld0000000000000000000000000000000000000000000000000000000000aa"
S_ID_NEW = "4e50000000000000000000000000000000000000000000000000000000000bb"
I_GIGABYTE = 2 ** 30
DT_START = datetime(2026, 10, 8, 22, 15, 58, tzinfo=timezone.utc)


def _fdictParsed(
    iKills=0, iOoms=0, iWorkingSet=I_GIGABYTE // 2,
    sLimitKind=cgroupMemory.S_LIMIT_FINITE, iLimit=I_GIGABYTE,
):
    """Return a parsed reading whose working set is exactly iWorkingSet."""
    iInactive = 100 * 2 ** 20
    return {
        "sCgroupVersion": "v2",
        "iUsageBytes": iWorkingSet + iInactive,
        "sLimitKind": sLimitKind,
        "iLimitBytes": iLimit if sLimitKind == cgroupMemory.S_LIMIT_FINITE
        else None,
        "iAnonBytes": iWorkingSet, "iFileBytes": iInactive,
        "iShmemBytes": 0, "iKernelBytes": 0,
        "iInactiveFileBytes": iInactive,
        "iWorkingSetBytes": iWorkingSet,
        "iOomCount": iOoms, "iOomKillCount": iKills,
    }


def _fdictRunningState(sStarted="2026-10-08T21:52:01.123456789Z"):
    return {"Running": True, "OOMKilled": False, "StartedAt": sStarted}


def _fnMeasure(dictStore, datetimeWhen, sContainerId=S_ID_OLD, **dictParsed):
    watch.fnRecordMeasurement(
        dictStore, S_NAME, sContainerId, _fdictParsed(**dictParsed),
        _fdictRunningState(), datetimeWhen,
    )


def _fdictDescribe(dictStore, datetimeWhen):
    return watch.fdictDescribeMemory(dictStore, S_NAME, datetimeWhen)


# ---------------------------------------------------------------------
# The incident rules
# ---------------------------------------------------------------------

def testTheFirstSampleSetsTheBaselineAndRecordsNoIncident():
    dictStore = watch.fdictCreateMemoryStore()
    _fnMeasure(dictStore, DT_START)
    dictRecord = dictStore[S_NAME]
    assert dictRecord["dictBaseline"]["iOomKillCount"] == 0
    assert dictRecord["dictBaseline"]["sContainerId"] == S_ID_OLD
    assert dictRecord["listIncidents"] == []


def testKillsAlreadyCountedAtTheFirstSampleAreBeforeObservation():
    dictStore = watch.fdictCreateMemoryStore()
    _fnMeasure(dictStore, DT_START, iKills=2, iOoms=14)
    listIncidents = dictStore[S_NAME]["listIncidents"]
    assert len(listIncidents) == 1
    dictIncident = listIncidents[0]
    assert dictIncident["sKind"] == watch.S_INCIDENT_BEFORE_OBSERVATION
    assert dictIncident["iKillCount"] == 2
    assert dictIncident["sContainerId"] == S_ID_OLD
    assert dictIncident["sWindowStartIso"].startswith("2026-10-08T21:52:01")
    assert dictIncident["bLimitEventInWindow"] is False


def testARiseBetweenSamplesIsAnOomKillOverThatWindow():
    dictStore = watch.fdictCreateMemoryStore()
    datetimeSecond = DT_START + timedelta(seconds=15)
    _fnMeasure(dictStore, DT_START, iKills=1)
    _fnMeasure(dictStore, datetimeSecond, iKills=3)
    listKills = [
        dictIncident for dictIncident in dictStore[S_NAME]["listIncidents"]
        if dictIncident["sKind"] == watch.S_INCIDENT_OOM_KILL
    ]
    assert len(listKills) == 1
    assert listKills[0]["iKillCount"] == 2
    assert listKills[0]["sWindowStartIso"] == DT_START.isoformat()
    assert listKills[0]["sWindowEndIso"] == datetimeSecond.isoformat()


def testAnUnchangedCountRecordsNothingMore():
    dictStore = watch.fdictCreateMemoryStore()
    _fnMeasure(dictStore, DT_START, iKills=0)
    _fnMeasure(dictStore, DT_START + timedelta(seconds=15), iKills=0)
    _fnMeasure(dictStore, DT_START + timedelta(seconds=30), iKills=0)
    assert dictStore[S_NAME]["listIncidents"] == []


def testTheLimitEventIsInTheWindowOnlyWhenOomRoseThere():
    dictStore = watch.fdictCreateMemoryStore()
    _fnMeasure(dictStore, DT_START, iKills=0, iOoms=4)
    _fnMeasure(dictStore, DT_START + timedelta(seconds=15), iKills=1, iOoms=4)
    _fnMeasure(dictStore, DT_START + timedelta(seconds=30), iKills=2, iOoms=5)
    listFlags = [
        dictIncident["bLimitEventInWindow"]
        for dictIncident in dictStore[S_NAME]["listIncidents"]
    ]
    assert listFlags == [False, True]


@pytest.mark.falsification
def testARecreateUnderTheSameNameResetsCountersAndKeepsHistory():
    """Kills: keeping the old baseline when the container id changes.

    The new container's kernel counters start from zero. Compared with
    the old container's baseline of 3, the new container's first
    reading of 2 kills would read as no rise at all, and those two
    kills would go unreported.
    """
    dictStore = watch.fdictCreateMemoryStore()
    _fnMeasure(dictStore, DT_START, sContainerId=S_ID_OLD, iKills=0)
    _fnMeasure(
        dictStore, DT_START + timedelta(seconds=15),
        sContainerId=S_ID_OLD, iKills=3)
    _fnMeasure(
        dictStore, DT_START + timedelta(seconds=30),
        sContainerId=S_ID_NEW, iKills=2)
    _fnMeasure(
        dictStore, DT_START + timedelta(seconds=45),
        sContainerId=S_ID_NEW, iKills=3)
    listIncidents = dictStore[S_NAME]["listIncidents"]
    assert [d["sContainerId"] for d in listIncidents] == [
        S_ID_OLD, S_ID_NEW, S_ID_NEW]
    assert [d["sKind"] for d in listIncidents] == [
        watch.S_INCIDENT_OOM_KILL, watch.S_INCIDENT_BEFORE_OBSERVATION,
        watch.S_INCIDENT_OOM_KILL]
    assert [d["iKillCount"] for d in listIncidents] == [3, 2, 1]
    assert dictStore[S_NAME]["dictBaseline"]["sContainerId"] == S_ID_NEW


def testAnIncidentIsRecordedOnceAcrossRepeatedEvidence():
    dictStore = watch.fdictCreateMemoryStore()
    dictEvidence = {
        "bAnswered": True, "sContainerId": S_ID_OLD, "bOomKilled": True,
        "iExitCode": 137, "sFinishedIso": "2026-10-08T22:16:13+00:00",
    }
    watch.fnRecordExitedOomKill(dictStore, S_NAME, dictEvidence)
    watch.fnRecordExitedOomKill(dictStore, S_NAME, dictEvidence)
    assert len(dictStore[S_NAME]["listIncidents"]) == 1


# ---------------------------------------------------------------------
# Unknown, stale, and near
# ---------------------------------------------------------------------

@pytest.mark.parametrize("sState", [
    watch.S_STATE_TIMEOUT, watch.S_STATE_UNREADABLE,
    watch.S_STATE_NOT_RUNNING, watch.S_STATE_GONE,
])
def testEveryFailedStateReadsUnknownWithNoNumber(sState):
    dictStore = watch.fdictCreateMemoryStore()
    watch.fnRecordFailure(
        dictStore, S_NAME, S_ID_OLD, sState, "the reason", DT_START)
    dictCurrent = _fdictDescribe(dictStore, DT_START)["dictCurrent"]
    assert dictCurrent["sLevel"] == watch.S_LEVEL_UNKNOWN
    assert dictCurrent["iWorkingSetBytes"] is None
    assert dictCurrent["sSentence"].startswith("Memory could not be checked:")


def testANameNeverSampledReadsNoSampleYet():
    dictCurrent = _fdictDescribe(
        watch.fdictCreateMemoryStore(), DT_START)["dictCurrent"]
    assert dictCurrent["sState"] == watch.S_STATE_NO_SAMPLE_YET
    assert dictCurrent["sLevel"] == watch.S_LEVEL_UNKNOWN


@pytest.mark.falsification
def testStalenessIsJudgedWhenTheRecordIsRead():
    """Kills: deciding staleness when the sample is taken.

    A sampler that has stopped cannot clear its own flag, so a reading
    judged fresh when it was taken stays "fresh" for ever. Only the
    reader knows how old it is now.
    """
    dictStore = watch.fdictCreateMemoryStore()
    _fnMeasure(dictStore, DT_START)
    dictFresh = _fdictDescribe(
        dictStore, DT_START + timedelta(seconds=5))["dictCurrent"]
    datetimeLater = DT_START + timedelta(
        seconds=watch.F_MEMORY_STALE_AFTER_SECONDS + 10)
    dictStale = _fdictDescribe(dictStore, datetimeLater)["dictCurrent"]
    assert dictFresh["sLevel"] == watch.S_LEVEL_OK
    assert dictStale["sLevel"] == watch.S_LEVEL_UNKNOWN
    assert "old" in dictStale["sSentence"]


@pytest.mark.falsification
def testAFailedReadKeepsEveryIncident():
    """Kills: clearing the incident history when a read fails."""
    dictStore = watch.fdictCreateMemoryStore()
    _fnMeasure(dictStore, DT_START, iKills=0)
    _fnMeasure(dictStore, DT_START + timedelta(seconds=15), iKills=1)
    watch.fnRecordFailure(
        dictStore, S_NAME, S_ID_OLD, watch.S_STATE_TIMEOUT, "",
        DT_START + timedelta(seconds=30))
    dictDescribed = _fdictDescribe(
        dictStore, DT_START + timedelta(seconds=31))
    assert dictDescribed["dictCurrent"]["sLevel"] == watch.S_LEVEL_UNKNOWN
    assert len(dictDescribed["listIncidents"]) == 1
    assert dictDescribed["iKillCount"] == 1


def testNearIsEnteredAtEightyFivePercentAndLeftBelowEighty():
    dictStore = watch.fdictCreateMemoryStore()
    listLevels = []
    for iSecond, iPercent in enumerate([84, 86, 82, 80, 79, 84]):
        datetimeWhen = DT_START + timedelta(seconds=15 * iSecond)
        iAtLeastThePercent = -(-I_GIGABYTE * iPercent // 100)
        _fnMeasure(dictStore, datetimeWhen, iWorkingSet=iAtLeastThePercent)
        listLevels.append(
            _fdictDescribe(dictStore, datetimeWhen)["dictCurrent"]["sLevel"])
    assert listLevels == [
        watch.S_LEVEL_OK, watch.S_LEVEL_NEAR, watch.S_LEVEL_NEAR,
        watch.S_LEVEL_NEAR, watch.S_LEVEL_OK, watch.S_LEVEL_OK,
    ]


def testANearEpisodeKeepsOneStartTime():
    dictStore = watch.fdictCreateMemoryStore()
    _fnMeasure(dictStore, DT_START, iWorkingSet=int(0.9 * I_GIGABYTE))
    datetimeLater = DT_START + timedelta(seconds=15)
    _fnMeasure(dictStore, datetimeLater, iWorkingSet=int(0.95 * I_GIGABYTE))
    dictCurrent = _fdictDescribe(dictStore, datetimeLater)["dictCurrent"]
    assert dictCurrent["sNearSinceIso"] == DT_START.isoformat()


def testAnUnlimitedContainerIsOkAndSaysSo():
    dictStore = watch.fdictCreateMemoryStore()
    _fnMeasure(
        dictStore, DT_START, sLimitKind=cgroupMemory.S_LIMIT_UNLIMITED,
        iWorkingSet=50 * I_GIGABYTE)
    dictCurrent = _fdictDescribe(dictStore, DT_START)["dictCurrent"]
    assert dictCurrent["sLevel"] == watch.S_LEVEL_OK
    assert dictCurrent["sSentence"].startswith("No memory limit.")


def testTheChipNamesTheEstimateAndTheLimit():
    dictStore = watch.fdictCreateMemoryStore()
    _fnMeasure(dictStore, DT_START, iWorkingSet=int(1.2 * I_GIGABYTE),
               iLimit=6 * I_GIGABYTE)
    dictCurrent = _fdictDescribe(dictStore, DT_START)["dictCurrent"]
    assert dictCurrent["sChipText"] == "Memory ~1.2 / 6 GB"


def testAHostProjectIsNotWatched():
    dictDescribed = watch.fdictDescribeMemory(
        watch.fdictCreateMemoryStore(), S_NAME, DT_START, bHostProject=True)
    assert dictDescribed["dictCurrent"]["sLevel"] == (
        watch.S_LEVEL_NOT_APPLICABLE)


# ---------------------------------------------------------------------
# The wording: one function, one row per sentence shape
# ---------------------------------------------------------------------

S_ENDING_DEPENDENTS = (
    "Other processes may have depended on the one killed; check that "
    "running work is still healthy.")
S_ENDING_RESUME = (
    "If an AI agent was running in the terminal, its conversation can "
    "usually be resumed (Claude Code: claude --resume).")
S_ENDING_SETTINGS = "To raise the limit, open Settings."


def _fdictIncident(**dictOverrides):
    dictIncident = {
        "sIncidentId": "oomKill:x:1", "sContainerId": S_ID_OLD,
        "sKind": watch.S_INCIDENT_OOM_KILL, "iKillCount": 1,
        "sWindowStartIso": DT_START.isoformat(),
        "sWindowEndIso": (DT_START + timedelta(seconds=15)).isoformat(),
        "bLimitEventInWindow": False,
        "sLimitKind": cgroupMemory.S_LIMIT_FINITE,
        "iLimitBytes": I_GIGABYTE,
    }
    dictIncident.update(dictOverrides)
    return dictIncident


def _fsLocalClock(datetimeWhen):
    return datetimeWhen.astimezone().strftime("%I:%M:%S %p").lstrip("0")


def _fnAssertEndsWithTheThreeEndings(sSentence, sRemedy=S_ENDING_SETTINGS):
    assert sSentence.endswith(
        " ".join([S_ENDING_DEPENDENTS, S_ENDING_RESUME, sRemedy]))


def testAnOomKillSentenceNamesItsWindowAndCount():
    sSentence = watch.fsDescribeMemoryIncident(
        _fdictIncident(iKillCount=2), DT_START)
    sWindow = (
        f"Between {_fsLocalClock(DT_START)} and "
        f"{_fsLocalClock(DT_START + timedelta(seconds=15))} the kernel's "
        "out-of-memory killer killed 2 processes in this container.")
    assert sSentence.startswith(sWindow)
    _fnAssertEndsWithTheThreeEndings(sSentence)


def testALimitEventAddsTheLimitSentence():
    sSentence = watch.fsDescribeMemoryIncident(
        _fdictIncident(bLimitEventInWindow=True), DT_START)
    assert (
        "The container also recorded reaching its 1 GB memory limit "
        "during that interval.") in sSentence
    _fnAssertEndsWithTheThreeEndings(sSentence)


def testAnUnlimitedContainerSaysItHasNoLimitOfItsOwn():
    sSentence = watch.fsDescribeMemoryIncident(
        _fdictIncident(
            sLimitKind=cgroupMemory.S_LIMIT_UNLIMITED, iLimitBytes=None),
        DT_START)
    assert "This container has no memory limit of its own." in sSentence
    _fnAssertEndsWithTheThreeEndings(
        sSentence, watch.S_REMEDY_GIVE_DOCKER_MEMORY)


def testAFiniteLimitWithoutALimitEventIsUndetermined():
    sSentence = watch.fsDescribeMemoryIncident(_fdictIncident(), DT_START)
    assert (
        "Whether this container's own limit was involved could not be "
        "established.") in sSentence


def testABeforeObservationSentenceSaysVaibifyDidNotSeeWhen():
    sSentence = watch.fsDescribeMemoryIncident(_fdictIncident(
        sKind=watch.S_INCIDENT_BEFORE_OBSERVATION, iKillCount=2,
        sWindowStartIso=DT_START.isoformat()), DT_START)
    assert sSentence.startswith(
        "The kernel's out-of-memory killer has killed 2 processes in this "
        f"container since it started at {_fsLocalClock(DT_START)}; "
        "vaibify did not observe when.")
    _fnAssertEndsWithTheThreeEndings(sSentence)


def testAnExitedSentenceNamesTheOldContainer():
    sSentence = watch.fsDescribeMemoryIncident(_fdictIncident(
        sKind=watch.S_INCIDENT_EXITED_OOM_KILLED,
        sWindowStartIso=DT_START.isoformat(),
        sWindowEndIso=DT_START.isoformat(),
        sLimitKind=cgroupMemory.S_LIMIT_UNKNOWN, iLimitBytes=None), DT_START)
    assert sSentence.startswith(
        "Docker reports that the previous container's main process "
        f"(id {S_ID_OLD[:12]}) was killed for lack of memory at "
        f"{_fsLocalClock(DT_START)}.")
    _fnAssertEndsWithTheThreeEndings(sSentence)


def testAnIncidentOnAnotherDayNamesTheDay():
    datetimeNextDay = DT_START + timedelta(days=2)
    sSentence = watch.fsDescribeMemoryIncident(_fdictIncident(), datetimeNextDay)
    assert DT_START.astimezone().strftime("%b") in sSentence


@pytest.mark.falsification
def testNoSentenceClaimsTheLimitWithoutALimitEvent():
    """Kills: the limit sentence emitted without a limit event.

    Counters are aggregate evidence: a kill and a limit event in the
    same window may be two events. Only a recorded limit event earns a
    sentence about the limit, and nothing may claim the limit was not
    involved.
    """
    for sKind in (
        watch.S_INCIDENT_OOM_KILL, watch.S_INCIDENT_BEFORE_OBSERVATION,
        watch.S_INCIDENT_EXITED_OOM_KILLED,
    ):
        for bLimitEvent in (False, True):
            for sLimitKind in (
                cgroupMemory.S_LIMIT_FINITE, cgroupMemory.S_LIMIT_UNLIMITED,
                cgroupMemory.S_LIMIT_UNKNOWN,
            ):
                sSentence = watch.fsDescribeMemoryIncident(_fdictIncident(
                    sKind=sKind, bLimitEventInWindow=bLimitEvent,
                    sLimitKind=sLimitKind), DT_START)
                bClaimsLimit = (
                    "reaching its" in sSentence or "reached its" in sSentence)
                assert bClaimsLimit == (
                    bLimitEvent and sKind == watch.S_INCIDENT_OOM_KILL), (
                    sSentence)
                assert "not affected" not in sSentence
                assert "not involved" not in sSentence


# ---------------------------------------------------------------------
# The host-incident ring must not receive these logs
# ---------------------------------------------------------------------

@pytest.mark.falsification
def testAnOomLogNeverReachesTheHostIncidentRing():
    """Kills: tagging the OOM log with the container id.

    The stale-heartbeat reconcile copies the ring's LATEST entry into
    sFailureCauseHost with no time correlation, so a kill logged with
    the container id would be reported as a dead runner's cause.
    """
    hostIncidents.fnResetHostIncidents()
    handlerRing = hostIncidents.HostIncidentHandler()
    loggerVaibify = logging.getLogger("vaibify")
    loggerVaibify.addHandler(handlerRing)
    try:
        dictStore = watch.fdictCreateMemoryStore()
        _fnMeasure(dictStore, DT_START, iKills=0)
        _fnMeasure(dictStore, DT_START + timedelta(seconds=15), iKills=1)
    finally:
        loggerVaibify.removeHandler(handlerRing)
    assert len(dictStore[S_NAME]["listIncidents"]) == 1
    for sKey in (S_ID_OLD, S_NAME):
        assert hostIncidents.flistIncidentsForContainer(sKey) == []
    dictReconciled = pipelineState._fdictReconcileStaleHeartbeat(
        {"bRunning": True, "iActiveStep": 2},
        dictIncident=pipelineState._fdictLookupHostIncident(S_ID_OLD))
    assert dictReconciled["sFailureCauseHost"] == ""


# ---------------------------------------------------------------------
# The sampler
# ---------------------------------------------------------------------

S_READ_TEXT = (
    "@@ cgroup v2\n@@ file memory.current\n600000000\n"
    "@@ file memory.max\n1073741824\n@@ file memory.events\n"
    "oom 0\noom_kill 0\n@@ file memory.stat\ninactive_file 100000000\n"
)


class _ConnectionDouble:
    """A gateway double answering the two calls the sampler makes."""

    def __init__(self, dictState=None, eventRelease=None):
        self.dictState = dictState if dictState is not None else (
            _fdictRunningState())
        self.eventRelease = eventRelease
        self.listReadIds = []

    def fdictReadContainerState(self, sContainerId):
        return self.dictState

    def fsReadCgroupMemory(self, sContainerId):
        self.listReadIds.append(sContainerId)
        if self.eventRelease is not None:
            self.eventRelease.wait(timeout=10)
        return S_READ_TEXT


def _fnSampleOnce(connectionDouble, dictStore, dictReadsInFlight):
    asyncio.run(sampler.fnSampleContainerMemory(
        dictStore, dictReadsInFlight, connectionDouble, S_NAME, S_ID_OLD))


def testASampleReadsTheIdNotTheName():
    connectionDouble = _ConnectionDouble()
    dictStore = watch.fdictCreateMemoryStore()
    _fnSampleOnce(connectionDouble, dictStore, {})
    assert connectionDouble.listReadIds == [S_ID_OLD]
    dictCurrent = dictStore[S_NAME]["dictCurrent"]
    assert dictCurrent["sState"] == watch.S_STATE_MEASURED
    assert dictCurrent["iWorkingSetBytes"] == 500000000


@pytest.mark.falsification
def testAHungReadIsATimeoutAndNeverANumber(monkeypatch):
    """Kills: reporting the previous reading's numbers on a timeout."""
    monkeypatch.setattr(watch, "F_MEMORY_READ_TIMEOUT_SECONDS", 0.2)
    dictStore = watch.fdictCreateMemoryStore()
    _fnSampleOnce(_ConnectionDouble(), dictStore, {})
    eventRelease = threading.Event()
    connectionHung = _ConnectionDouble(eventRelease=eventRelease)

    async def fnTwoSamplesOnOneLoop():
        dictReadsInFlight = {}
        listCurrents = []
        try:
            for _iSample in range(2):
                await sampler.fnSampleContainerMemory(
                    dictStore, dictReadsInFlight, connectionHung,
                    S_NAME, S_ID_OLD)
                listCurrents.append(dict(dictStore[S_NAME]["dictCurrent"]))
        finally:
            eventRelease.set()
        return listCurrents

    listCurrents = asyncio.run(fnTwoSamplesOnOneLoop())
    for dictCurrent in listCurrents:
        assert dictCurrent["sState"] == watch.S_STATE_TIMEOUT
        assert dictCurrent["iWorkingSetBytes"] is None
        assert dictCurrent["iUsageBytes"] is None
    assert len(connectionHung.listReadIds) == 1, (
        "a second read was launched while the first was still hung")


def testAStoppedContainerIsNotRunningAndItsKillIsRecorded():
    dictState = {
        "Running": False, "OOMKilled": True, "ExitCode": 137,
        "FinishedAt": "2026-10-08T22:16:13.5Z",
        "StartedAt": "2026-10-08T21:52:01Z",
    }
    dictStore = watch.fdictCreateMemoryStore()
    _fnSampleOnce(_ConnectionDouble(dictState=dictState), dictStore, {})
    dictRecord = dictStore[S_NAME]
    assert dictRecord["dictCurrent"]["sState"] == watch.S_STATE_NOT_RUNNING
    assert [d["sKind"] for d in dictRecord["listIncidents"]] == [
        watch.S_INCIDENT_EXITED_OOM_KILLED]


def testAContainerDockerNoLongerHasIsGone():
    class _ConnectionGone(_ConnectionDouble):
        def fdictReadContainerState(self, sContainerId):
            return None

    dictStore = watch.fdictCreateMemoryStore()
    _fnSampleOnce(_ConnectionGone(), dictStore, {})
    assert dictStore[S_NAME]["dictCurrent"]["sState"] == watch.S_STATE_GONE


def testADaemonThatWillNotAnswerIsUnreadable():
    class _ConnectionBroken(_ConnectionDouble):
        def fdictReadContainerState(self, sContainerId):
            raise ConnectionError("daemon down")

    dictStore = watch.fdictCreateMemoryStore()
    _fnSampleOnce(_ConnectionBroken(), dictStore, {})
    assert dictStore[S_NAME]["dictCurrent"]["sState"] == (
        watch.S_STATE_UNREADABLE)
