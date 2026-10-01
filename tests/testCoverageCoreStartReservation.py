"""startReservation's execution, settlement, cancel and poll branches.

The operation journal is REAL (redirected into tmp_path); the Docker
CLI wrappers in ``containerManager`` are recorders, because the daemon
is the external boundary. The container NAME and the created container
ID are deliberately distinct so a gateway asked about the wrong one
fails the assertion.
"""

import asyncio
import subprocess
import sys
import time
from types import SimpleNamespace

import pytest

from vaibify.config import containerLock, operationJournal, registryManager
from vaibify.gui import containerOwnership, startReservation, startResultStore
from vaibify.gui.startReservation import (
    StartCancelledError,
    StartReservation,
    StartTaskRecord,
)


S_NAME = "containerAlpha"
S_CREATED_ID = "f00dcafe0000createdid"
S_SESSION = "browserSessionAlpha"
S_OTHER_SESSION = "browserSessionBeta"


@pytest.fixture(autouse=True)
def fixtureIsolateJournal(tmp_path, monkeypatch):
    """Redirect the journal, locks and registry into tmp_path."""
    monkeypatch.setattr(
        operationJournal, "_S_JOURNAL_DIRECTORY", str(tmp_path / "journal"),
    )
    monkeypatch.setattr(
        containerLock, "_S_LOCK_DIRECTORY", str(tmp_path / "locks"),
    )
    monkeypatch.setattr(
        registryManager, "_S_REGISTRY_PATH", str(tmp_path / "registry.json"),
    )
    monkeypatch.setattr(
        registryManager, "_S_LOCK_PATH", str(tmp_path / "registry.lock"),
    )


@pytest.fixture
def dictDocker(monkeypatch):
    """Substitute the Docker CLI wrappers; record every call."""
    dictState = {
        "dictStatus": {"bRunning": False, "bExists": True},
        "bAnswered": True, "bRunningAfterStart": True,
        "listCalls": [],
    }
    containerManager = startReservation.containerManager

    def fsCreate(configProject, sReservationId, fnRegisterProcess=None):
        dictState["listCalls"].append(("create", sReservationId))
        return S_CREATED_ID

    monkeypatch.setattr(
        containerManager, "fdictGetContainerStatus",
        lambda sName: dictState["listCalls"].append(("status", sName))
        or dict(dictState["dictStatus"]))
    monkeypatch.setattr(
        containerManager, "fnRemoveStopped",
        lambda sName: dictState["listCalls"].append(("remove", sName)))
    monkeypatch.setattr(
        containerManager, "fsCreateContainerForReservation", fsCreate)
    monkeypatch.setattr(
        containerManager, "fnStartCreatedContainer",
        lambda sId, fnRegisterProcess=None:
        dictState["listCalls"].append(("start", sId)))
    monkeypatch.setattr(
        containerManager, "fdictProbeContainerPresence",
        lambda sName: {"bAnswered": dictState["bAnswered"], "bPresent": True})
    monkeypatch.setattr(
        containerManager, "fbContainerIsRunning",
        lambda sId: dictState["listCalls"].append(("isRunning", sId))
        or dictState["bRunningAfterStart"])
    return dictState


def freservationBuild(bJournaled=True):
    """Return a reservation whose task holds a prepared journal record."""
    sOperationId = (
        operationJournal.fsPrepareOperation(S_NAME, "start", S_NAME)
        if bJournaled else "operationNeverJournaled"
    )
    return StartReservation(
        sReservationId="reservationAlpha",
        recordStartTask=StartTaskRecord(
            sStartTaskId="taskAlpha", sJournalOperationId=sOperationId,
        ),
    )


def fdictJournalOperations():
    """Return the container's journal records."""
    return operationJournal.fdictReadJournalOutcome(S_NAME)["dictOperations"]


def testReservedStartCreatesJournalsThenStartsTheCreatedId(dictDocker):
    """The created id is journaled and started; a stopped name is cleared."""
    reservation = freservationBuild()
    reservation.fHeartbeatMonotonic = 0.0
    sContainerId = startReservation._fsExecuteReservedStart(
        S_NAME, reservation, SimpleNamespace(),
    )
    assert sContainerId == S_CREATED_ID
    assert dictDocker["listCalls"] == [
        ("status", S_NAME), ("remove", S_NAME),
        ("create", "reservationAlpha"), ("start", S_CREATED_ID),
        ("isRunning", S_CREATED_ID),
    ]
    dictRecord = fdictJournalOperations()[
        reservation.recordStartTask.sJournalOperationId
    ]
    assert dictRecord["sDockerContainerId"] == S_CREATED_ID
    assert dictRecord["sState"] == operationJournal.S_OPERATION_STATE_IN_FLIGHT
    assert reservation.recordStartTask.sCreatedContainerId == S_CREATED_ID
    assert reservation.fHeartbeatMonotonic > 0.0


def testReservedStartRefusesAContainerAlreadyRunning(dictDocker):
    """A running incarnation is never started a second time."""
    dictDocker["dictStatus"] = {"bRunning": True, "bExists": True}
    with pytest.raises(RuntimeError, match="is already running"):
        startReservation._fsExecuteReservedStart(
            S_NAME, freservationBuild(), SimpleNamespace(),
        )
    assert ("create", "reservationAlpha") not in dictDocker["listCalls"]


def testCancelledStartStopsAtTheFirstStepBoundary(dictDocker):
    """A cancel that arrived first means nothing is created."""
    reservation = freservationBuild()
    reservation.recordStartTask.bCancelRequested = True
    with pytest.raises(StartCancelledError):
        startReservation._fsExecuteReservedStart(
            S_NAME, reservation, SimpleNamespace(),
        )
    assert dictDocker["listCalls"] == []


def testEntrypointThatExitsImmediatelyIsAFailedStart(dictDocker):
    """Created and started but not running is never called started."""
    dictDocker["bRunningAfterStart"] = False
    with pytest.raises(RuntimeError, match="entrypoint exited immediately"):
        startReservation._fsExecuteReservedStart(
            S_NAME, freservationBuild(), SimpleNamespace(),
        )


def testUnansweredPresenceProbeDoesNotFailTheStart(dictDocker):
    """A busy daemon is not evidence the container died."""
    dictDocker["bAnswered"] = False
    dictDocker["bRunningAfterStart"] = False
    assert startReservation._fsExecuteReservedStart(
        S_NAME, freservationBuild(), SimpleNamespace(),
    ) == S_CREATED_ID


def testAdoptingAProcessAfterCancelTerminatesItAtOnce():
    """The spawn-then-register window cannot leak a live launch."""
    recordTask = StartTaskRecord(
        sStartTaskId="taskAlpha", sJournalOperationId="operationAlpha",
    )
    recordTask.bCancelRequested = True
    processChild = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(30)"],
    )
    try:
        recordTask.fnAdoptProcess(processChild)
        assert processChild.poll() is not None
        assert recordTask.bProcessWasSignalled is True
    finally:
        if processChild.poll() is None:
            processChild.kill()
            processChild.wait()


def testHeartbeatOfNoReservationIsNeverStale():
    """No reservation means nothing can be stuck."""
    assert startReservation.fbReservationHeartbeatIsStale(None) is False
    reservation = freservationBuild(bJournaled=False)
    reservation.fHeartbeatMonotonic = time.monotonic() - 10.0
    assert startReservation.fbReservationHeartbeatIsStale(
        reservation, fStaleSeconds=5.0,
    ) is True


class DockerConnectionRecorder:
    """The route's Docker connection: running list and image state."""

    def __init__(self, exceptionList=None, exceptionImage=None,
                 sImageState="built"):
        self.exceptionList = exceptionList
        self.exceptionImage = exceptionImage
        self.sImageState = sImageState

    def flistGetRunningContainers(self):
        if self.exceptionList:
            raise self.exceptionList
        return []

    def fsImageState(self, sReference):
        if self.exceptionImage:
            raise self.exceptionImage
        return self.sImageState


def testUnansweredProbesNeverRefuseAStart():
    """A daemon that errs on either probe falls through to the launch."""
    connectionDocker = DockerConnectionRecorder(
        exceptionList=RuntimeError("busy"), exceptionImage=RuntimeError("busy"),
    )
    assert startReservation._fsRefusalForAlreadyRunningContainer(
        connectionDocker, S_NAME,
    ) == ""
    assert startReservation._fsImageBuildState(connectionDocker, S_NAME) == (
        startReservation.S_IMAGE_STATE_UNANSWERED
    )
    assert startReservation._fsImageBuildState(object(), S_NAME) == (
        startReservation.S_IMAGE_STATE_UNANSWERED
    )


def testMissingImageRefusesUnlessTheImageIsObtained(monkeypatch):
    """Only a built project with a positively missing image is refused."""
    connectionDocker = DockerConnectionRecorder(
        sImageState=startReservation.S_IMAGE_STATE_MISSING,
    )
    assert "has not been built yet" in (
        startReservation._fsRefusalForUnbuiltImage(connectionDocker, S_NAME)
    )
    monkeypatch.setattr(
        registryManager, "fdictGetProject",
        lambda sName: {"dictImageSource": {"sSource": "archive"}},
    )
    assert startReservation._fsRefusalForUnbuiltImage(
        connectionDocker, S_NAME,
    ) == ""


def fappStateBuild(recordOwner=None):
    """Return an app state holding at most one owner record."""
    dictOwners = {S_NAME: recordOwner} if recordOwner is not None else {}
    return SimpleNamespace(dictContainerOwners=dictOwners)


def frecordOwnerBuild(sSession=S_SESSION, reservation=None):
    """Return an owner record for S_NAME bound to sSession."""
    recordOwner = containerOwnership.OwnerRecord(
        sLeaseId="leaseAlpha", fileHandleLock=None,
        sContainerId="ownedContainerIdentifier", sBrowserSessionId=sSession,
    )
    recordOwner.reservation = reservation
    return recordOwner


def testInconclusiveFailureQuarantinesJournalOwnerAndResult():
    """A launch not proven exited keeps the flock and poisons the record."""
    reservation = freservationBuild()
    reservation.recordStartTask.sCreatedContainerId = S_CREATED_ID
    recordOwner = frecordOwnerBuild(reservation=reservation)
    appState = fappStateBuild(recordOwner)
    startResultStore.fnOpenStartResult(
        appState, reservation.sReservationId, S_NAME, S_SESSION,
    )
    bFreed = startReservation._fbCommitFailedStart(
        appState, S_NAME, recordOwner, reservation, RuntimeError("boom"),
        {"bConclusive": True, "sDetail": "gone"},
        {"bExited": False},
    )
    assert bFreed is False
    assert recordOwner.reservation is None
    assert recordOwner.poison.sContainerId == S_CREATED_ID
    assert fdictJournalOperations()[
        reservation.recordStartTask.sJournalOperationId
    ]["sState"] == operationJournal.S_OPERATION_STATE_NEEDS_RECONCILIATION
    recordResult = startResultStore.frecordStartResultById(
        appState, reservation.sReservationId,
    )
    assert recordResult.sState == startResultStore.S_RESULT_FAILED
    assert recordResult.bQuarantined is True
    assert "vaibify reconcile" in recordResult.sSafeError


def testQuarantineWithoutJournalOrOwnerStillClosesTheResult(caplog):
    """No journal record and no owner: logged, and the result still fails."""
    reservation = freservationBuild(bJournaled=False)
    appState = fappStateBuild()
    startResultStore.fnOpenStartResult(
        appState, reservation.sReservationId, S_NAME, S_SESSION,
    )
    bFreed = startReservation._fbCommitFailedStart(
        appState, S_NAME, None, reservation,
        StartCancelledError("cancelled"),
        {"bConclusive": False, "sDetail": "daemon silent"},
        {"bExited": True},
    )
    assert bFreed is False
    recordResult = startResultStore.frecordStartResultById(
        appState, reservation.sReservationId,
    )
    assert recordResult.sSafeError.startswith("The start was cancelled.")
    assert any("Could not quarantine the start record" in r.getMessage()
               for r in caplog.records)


def testConclusiveFailureOfAnUnjournaledStartIsLoggedAndFreed(caplog):
    """A settle that cannot find its record is reported, not masked."""
    reservation = freservationBuild(bJournaled=False)
    recordOwner = frecordOwnerBuild(reservation=reservation)
    appState = fappStateBuild(recordOwner)
    bFreed = startReservation._fbCommitFailedStart(
        appState, S_NAME, recordOwner, reservation, RuntimeError("x"),
        {"bConclusive": True, "sDetail": ""}, {"bExited": True},
    )
    assert bFreed is True
    assert any("Could not settle the start journal record" in r.getMessage()
               for r in caplog.records)


def testCancelOfAFinishedStartIsRefusedAsStale():
    """A cancel naming an older reservation must not touch the new one."""
    reservation = freservationBuild()
    appState = fappStateBuild(frecordOwnerBuild(reservation=reservation))
    iCode, dictBody, reservationFound = startReservation._ftMarkCancelRequested(
        appState, S_NAME, S_SESSION, "reservationOlder",
    )
    assert iCode == 409
    assert "stale cancel" in dictBody["sMessage"]
    assert reservationFound is None
    assert reservation.recordStartTask.bCancelRequested is False


def testCancelSettlementWaitReportsTheTaskOutcomeHonestly(monkeypatch):
    """A finished task settles; a task that outlives the wait does not."""
    monkeypatch.setattr(startReservation, "_F_CANCEL_SETTLE_WAIT_SECONDS", 0.05)

    async def fnDrive():
        async def fnFails():
            raise RuntimeError("start failed")

        async def fnHangs():
            await asyncio.sleep(10)

        taskFailed = asyncio.ensure_future(fnFails())
        await asyncio.sleep(0)
        appState = SimpleNamespace(dictDurableTaskRecords={
            S_NAME: SimpleNamespace(taskAsync=taskFailed),
        })
        bFailedSettled = await startReservation._fbAwaitStartTaskSettlement(
            appState, S_NAME,
        )
        taskHanging = asyncio.ensure_future(fnHangs())
        appState.dictDurableTaskRecords[S_NAME].taskAsync = taskHanging
        bHangingSettled = await startReservation._fbAwaitStartTaskSettlement(
            appState, S_NAME,
        )
        taskHanging.cancel()
        return bFailedSettled, bHangingSettled

    assert asyncio.run(fnDrive()) == (True, False)


def testPollWithoutSessionIsForbidden():
    """An anonymous poll learns nothing."""
    assert startReservation.ftPollStartStatus(fappStateBuild(), S_NAME, "")[0] == 403


def testPendingStartIsVisibleToTheCurrentOwnerOnly():
    """The owner who inherited a start may watch it; a stranger may not."""
    reservation = freservationBuild(bJournaled=False)
    appState = fappStateBuild(frecordOwnerBuild(
        sSession=S_OTHER_SESSION, reservation=reservation,
    ))
    startResultStore.fnOpenStartResult(
        appState, reservation.sReservationId, S_NAME, S_SESSION,
    )
    iCode, dictBody = startReservation.ftPollStartStatus(
        appState, S_NAME, S_OTHER_SESSION,
    )
    assert iCode == 200
    assert dictBody["sState"] == startResultStore.S_RESULT_PENDING
    iCode, _ = startReservation.ftPollStartStatus(
        appState, S_NAME, "browserSessionStranger",
    )
    assert iCode == 403


def testSucceededStartAfterReleaseGivesNoLease():
    """With the ownership released, the initiator gets the outcome only."""
    appState = fappStateBuild()
    startResultStore.fnOpenStartResult(
        appState, "reservationAlpha", S_NAME, S_SESSION,
    )
    startResultStore.fnCloseStartResult(
        appState, "reservationAlpha", startResultStore.S_RESULT_SUCCEEDED,
        sContainerId=S_CREATED_ID,
    )
    iCode, dictBody = startReservation.ftPollStartStatus(
        appState, S_NAME, S_SESSION,
    )
    assert iCode == 200
    assert dictBody["sLeaseId"] == ""
    assert dictBody["sContainerId"] == S_CREATED_ID
    assert "claim it from the dashboard" in dictBody["sMessage"]
    assert startReservation.ftPollStartStatus(
        appState, S_NAME, S_OTHER_SESSION,
    )[0] == 403


def testAFailureSeenByAnotherSessionDoesNotBlockItsStart():
    """Only the initiator's own unacknowledged failure blocks a relaunch."""
    appState = fappStateBuild()
    startResultStore.fnOpenStartResult(
        appState, "reservationAlpha", S_NAME, S_SESSION,
    )
    startResultStore.fnCloseStartResult(
        appState, "reservationAlpha", startResultStore.S_RESULT_FAILED,
        sSafeError="boom",
    )
    assert startReservation._fsUnacknowledgedFailureReason(
        appState, S_NAME, S_OTHER_SESSION,
    ) == ""
    assert "has not been acknowledged" in (
        startReservation._fsUnacknowledgedFailureReason(
            appState, S_NAME, S_SESSION,
        )
    )


def testSuccessSettlementStartsKeepAliveForNeverSleepProjects(monkeypatch):
    """A never-sleep project's keep-alive starts once the start succeeds."""
    from vaibify.config import keepAliveManager
    listKeptAlive = []
    monkeypatch.setattr(
        keepAliveManager, "fnStartKeepAlive", listKeptAlive.append,
    )
    reservation = freservationBuild()
    recordOwner = frecordOwnerBuild(reservation=reservation)
    appState = fappStateBuild(recordOwner)
    startResultStore.fnOpenStartResult(
        appState, reservation.sReservationId, S_NAME, S_SESSION,
    )
    asyncio.run(startReservation._fnSettleStartSuccess(
        appState, S_NAME, reservation, S_CREATED_ID,
        SimpleNamespace(bNeverSleep=True),
    ))
    assert listKeptAlive == [S_NAME]
    assert recordOwner.reservation is None
    assert recordOwner.sContainerId == S_CREATED_ID
    assert fdictJournalOperations() == {}
