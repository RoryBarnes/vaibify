"""commitCarrier's lane tuples, commit refusals and liveness view.

The owner map is keyed by container NAME while requests address the
Docker container ID, and the two are always distinct here, so a lane
builder that resolved by the wrong key fails. The journal is real
(tmp_path); no Docker daemon is involved in anything below.
"""

import asyncio
import logging
import sys
from types import SimpleNamespace

import pytest

from vaibify.config import containerLock, operationJournal
from vaibify.gui import browserSession, commitCarrier, containerOwnership
from vaibify.gui.commitCarrier import CommitRefusedError, MutationSupervisor


S_NAME = "containerAlpha"
S_CONTAINER_ID = "0123abcdcontainerid"
S_LEASE = "leaseAlpha"
S_AGENT_TOKEN = "agentTokenAlpha"
S_SESSION = "browserSessionAlpha"


@pytest.fixture(autouse=True)
def fixtureIsolateJournal(tmp_path, monkeypatch):
    """Redirect the journal and locks into tmp_path."""
    monkeypatch.setattr(
        operationJournal, "_S_JOURNAL_DIRECTORY", str(tmp_path / "journal"),
    )
    monkeypatch.setattr(
        containerLock, "_S_LOCK_DIRECTORY", str(tmp_path / "locks"),
    )


def frecordOwnerBuild(sSession=S_SESSION):
    """Return an owner record for S_NAME holding S_CONTAINER_ID."""
    return containerOwnership.OwnerRecord(
        sLeaseId=S_LEASE, fileHandleLock=None, sAgentToken=S_AGENT_TOKEN,
        sContainerId=S_CONTAINER_ID, sBrowserSessionId=sSession,
    )


def fappStateBuild(recordOwner=None, dictBrowserSessions=None):
    """Return an app state with one owner record keyed by name."""
    return SimpleNamespace(
        dictContainerOwners={S_NAME: recordOwner} if recordOwner else {},
        dictBrowserSessions=dictBrowserSessions or {},
    )


def frequestBuild(dictHeaders, dictPathParams=None, dictQuery=None):
    """Return a request stand-in exposing headers, path and query params."""
    return SimpleNamespace(
        headers=dictHeaders, path_params=dictPathParams or {},
        query_params=dictQuery or {},
    )


def testRequestLaneTupleIsNoneForAnUnknownContainer():
    """A request naming no owned container cannot be bound."""
    assert commitCarrier.fdictBuildLaneTupleFromRequest(
        fappStateBuild(frecordOwnerBuild()), "unknownContainerId",
        frequestBuild({}),
    ) is None


def testAgentLaneIsBoundByContainerIdAndItsOwnToken():
    """The agent lane resolves the ID to the name and checks the token."""
    appState = fappStateBuild(frecordOwnerBuild())
    dictTuple = commitCarrier.fdictBuildLaneTupleFromRequest(
        appState, S_CONTAINER_ID,
        frequestBuild({"x-vaibify-session": S_AGENT_TOKEN}),
    )
    assert dictTuple == {
        "sLane": "agent", "iOwnerGeneration": 1,
        "sAgentToken": S_AGENT_TOKEN, "sContainerName": S_NAME,
    }
    assert commitCarrier.fdictBuildLaneTupleFromRequest(
        appState, S_CONTAINER_ID,
        frequestBuild({"x-vaibify-session": "anotherContainersToken"}),
    ) is None


def testWebSocketLaneTuplesForAgentAndUnknownName():
    """The socket builder binds by name and honours the agent header."""
    appState = fappStateBuild(frecordOwnerBuild())
    assert commitCarrier.fdictBuildLaneTupleFromWebSocket(
        appState, "unknownName", frequestBuild({}),
    ) is None
    dictTuple = commitCarrier.fdictBuildLaneTupleFromWebSocket(
        appState, S_NAME, frequestBuild({"x-vaibify-session": S_AGENT_TOKEN}),
    )
    assert dictTuple["sLane"] == "agent"
    assert dictTuple["sContainerName"] == S_NAME


def testBrowserLaneNeedsTheLeaseAndTheBoundSession(monkeypatch):
    """No lease, a wrong lease, or another session never binds."""
    monkeypatch.setattr(
        browserSession, "fsSessionIdForCredential",
        lambda dictSessions, sCredential: dictSessions.get(sCredential, ""),
    )
    appState = fappStateBuild(
        frecordOwnerBuild(),
        {"credentialAlpha": S_SESSION, "credentialBeta": "sessionBeta"},
    )
    fdictBuild = commitCarrier.fdictBuildLaneTupleFromRequest
    assert fdictBuild(appState, S_CONTAINER_ID, frequestBuild(
        {"x-session-token": "credentialAlpha"})) is None
    assert fdictBuild(appState, S_CONTAINER_ID, frequestBuild(
        {"x-session-token": "credentialBeta", "x-vaibify-lease": S_LEASE},
    )) is None
    dictTuple = fdictBuild(appState, S_CONTAINER_ID, frequestBuild(
        {"x-session-token": "credentialAlpha", "x-vaibify-lease": S_LEASE},
    ))
    assert dictTuple["sBrowserSessionId"] == S_SESSION
    assert dictTuple["sContainerName"] == S_NAME


def fdictBrowserTuple(**dictOverrides):
    """Return a browser lane tuple matching frecordOwnerBuild()."""
    dictTuple = {
        "sLane": "browser", "iOwnerGeneration": 1,
        "sBrowserSessionId": S_SESSION, "sLeaseId": S_LEASE,
        "sContainerName": S_NAME,
    }
    dictTuple.update(dictOverrides)
    return dictTuple


def testLaneTupleCurrencyFailsOnEveryKindOfDrift():
    """Missing owner, poison, rotated lease or token all fail the check."""
    recordOwner = frecordOwnerBuild()
    appState = fappStateBuild(recordOwner)
    fbCurrent = commitCarrier.fbLaneTupleStillCurrent
    assert fbCurrent(appState, None) is False
    assert fbCurrent(fappStateBuild(), fdictBrowserTuple()) is False
    assert fbCurrent(appState, fdictBrowserTuple()) is True
    assert fbCurrent(appState, fdictBrowserTuple(sLeaseId="leaseOld")) is False
    dictAgent = {"sLane": "agent", "iOwnerGeneration": 1,
                 "sAgentToken": S_AGENT_TOKEN, "sContainerName": S_NAME}
    assert fbCurrent(appState, dictAgent) is True
    assert fbCurrent(appState, dict(dictAgent, sAgentToken="stale")) is False
    recordOwner.poison = containerOwnership.PoisonRecord(
        sGuardedOperationId="operationAlpha", sContainerId=S_CONTAINER_ID,
        sTaskHandleId="taskAlpha",
    )
    assert fbCurrent(appState, fdictBrowserTuple()) is False


def testUnbindableRequestRunsMarkedButUnadmitted():
    """An authorized but unbindable request gets no admission at all."""
    tTokens = commitCarrier.ftOpenRequestAdmission(
        fappStateBuild(frecordOwnerBuild()), {"sTargetParam": "sContainerId"},
        frequestBuild({}, {"sContainerId": S_CONTAINER_ID}),
    )
    try:
        assert tTokens[1] is None
    finally:
        commitCarrier.fnResetEnforcedRequestLane(tTokens[0])


def testSynchronousCommitRefusesAStaleTupleBeforeJournaling():
    """A stale tuple is refused and nothing reaches the journal."""
    appState = fappStateBuild(frecordOwnerBuild())
    with pytest.raises(CommitRefusedError, match="no longer matches"):
        commitCarrier.fdictCommitSynchronousMutation(
            appState, S_NAME, S_CONTAINER_ID,
            fdictBrowserTuple(sLeaseId="leaseOld"), "helper", "targetAlpha",
            lambda: None, {"iHolderPid": 1, "iHolderProcessGroup": 1},
        )
    assert operationJournal.fdictReadJournalOutcome(S_NAME)["sReadState"] == (
        "absent"
    )


def testSynchronousCommitRefusesWhenOwnerChangesAtTheCommitPoint(monkeypatch):
    """A lease rotated after journaling refuses the effect."""
    recordOwner = frecordOwnerBuild()
    appState = fappStateBuild(recordOwner)
    fnRealGate = commitCarrier.fnAssertOperationAdmittedByIdentity
    listEffects = []

    def fnGateThenRotate(sName, sOperationId, dictIdentity):
        fnRealGate(sName, sOperationId, dictIdentity)
        recordOwner.sLeaseId = "leaseRotatedByTransfer"

    monkeypatch.setattr(
        commitCarrier, "fnAssertOperationAdmittedByIdentity", fnGateThenRotate,
    )
    with pytest.raises(CommitRefusedError, match="changed between admission"):
        commitCarrier.fdictCommitSynchronousMutation(
            appState, S_NAME, S_CONTAINER_ID, fdictBrowserTuple(), "helper",
            "targetAlpha", lambda: listEffects.append("ran"),
            {"iHolderPid": 1, "iHolderProcessGroup": 1},
        )
    assert listEffects == []


def testCommittedEffectThatCannotSettleIsReportedNotMasked(caplog):
    """A poisoned record after the effect is reported as unsettled."""
    appState = fappStateBuild(frecordOwnerBuild())

    def fnEffectThatPoisonsItsRecord():
        dictOperations = operationJournal.fdictReadJournalOutcome(S_NAME)[
            "dictOperations"
        ]
        for sOperationId in dictOperations:
            operationJournal.fnMarkOperationNeedsReconciliation(
                S_NAME, sOperationId, sNote="poisoned mid-commit",
            )
        return "effectResult"

    with caplog.at_level(logging.WARNING, logger="vaibify"):
        dictOutcome = commitCarrier.fdictCommitSynchronousMutation(
            appState, S_NAME, S_CONTAINER_ID, fdictBrowserTuple(), "helper",
            "targetAlpha", fnEffectThatPoisonsItsRecord,
            {"iHolderPid": 1, "iHolderProcessGroup": 1},
        )
    assert dictOutcome["bCommitted"] is True
    assert dictOutcome["result"] == "effectResult"
    assert dictOutcome["bJournalSettled"] is False
    assert any("could not settle journal record" in r.getMessage()
               for r in caplog.records)


class TaskStandIn:
    """A task handle answering only whether it is done."""

    def __init__(self, bDone):
        self.bDone = bDone

    def done(self):
        return self.bDone


def fsupervisorBuild(sName=S_NAME, bDone=False, **dictFields):
    """Return a supervisor for sName whose task is (not) done."""
    return MutationSupervisor(
        sSupervisorId=f"supervisor{sName}{bDone}{len(dictFields)}",
        sName=sName, sContainerId=S_CONTAINER_ID,
        dictLaneTuple=fdictBrowserTuple(),
        taskSupervisor=TaskStandIn(bDone), **dictFields,
    )


def testLockHeldCancelSignalsOnlyThisContainersLiveSupervisors(caplog):
    """Finished and foreign supervisors are skipped; live ones terminate."""
    listTerminated = []
    supervisorLive = fsupervisorBuild(
        fnTerminateWorker=lambda: listTerminated.append("live"),
        sOperationId="operationNeverJournaled",
    )
    supervisorDone = fsupervisorBuild(bDone=True)
    supervisorForeign = fsupervisorBuild(sName="containerBeta")
    appState = fappStateBuild(frecordOwnerBuild())
    appState.dictMutationSupervisors = {
        s.sSupervisorId: s
        for s in (supervisorLive, supervisorDone, supervisorForeign)
    }
    assert commitCarrier.fdictRequestLockHeldCancel(
        appState, S_NAME, fdictBrowserTuple(sLeaseId="leaseOld"),
    )["bCancelSignalled"] is False
    with caplog.at_level(logging.WARNING, logger="vaibify"):
        dictOutcome = commitCarrier.fdictRequestLockHeldCancel(
            appState, S_NAME, fdictBrowserTuple(),
        )
    assert dictOutcome["listSupervisorIds"] == [supervisorLive.sSupervisorId]
    assert listTerminated == ["live"]
    assert supervisorLive.eventCancelRequested.is_set()
    assert not supervisorForeign.eventCancelRequested.is_set()
    assert any("CANCEL_REQUESTED" in r.getMessage() for r in caplog.records)


def testFailedWorkerThatCannotBePoisonedIsLogged(caplog):
    """A missing journal record is logged; the outcome is still recorded."""
    supervisor = fsupervisorBuild(sOperationId="operationNeverJournaled")
    with caplog.at_level(logging.WARNING, logger="vaibify"):
        commitCarrier._fnSettleAfterFailedWorker(supervisor, RuntimeError("x"))
    assert supervisor.dictOutcome["bCommitted"] is False
    assert supervisor.dictOutcome["sError"] == "x"
    assert any("Could not poison journal record" in r.getMessage()
               for r in caplog.records)


def testSupervisorEvictionIgnoresACancelledTask():
    """A cancelled supervisor task is evicted without reading its error."""
    supervisor = fsupervisorBuild()
    dictRegistry = {supervisor.sSupervisorId: supervisor}
    fnEvict = commitCarrier._ffnBuildSupervisorEviction(dictRegistry, supervisor)

    async def fnDrive():
        taskCancelled = asyncio.ensure_future(asyncio.sleep(10))
        taskCancelled.cancel()
        try:
            await taskCancelled
        except asyncio.CancelledError:
            pass
        fnEvict(taskCancelled)

    asyncio.run(fnDrive())
    assert dictRegistry == {}


def testDurableLaunchWithStaleTupleIsRefused():
    """No durable task starts under an owner that has moved on."""
    appState = fappStateBuild(frecordOwnerBuild())
    listStarted = []

    async def fnDrive():
        await commitCarrier.fdictLaunchDurableTask(
            appState, S_NAME, S_CONTAINER_ID,
            fdictBrowserTuple(sLeaseId="leaseOld"),
            lambda: listStarted.append("started"),
        )

    with pytest.raises(CommitRefusedError, match="durable-task launch"):
        asyncio.run(fnDrive())
    assert listStarted == []


def testDurableTaskCurrencyNeedsRunningStateAndLiveOwner():
    """A cancelled record or a vanished owner refuses further writes."""
    recordTask = commitCarrier.DurableTaskRecord(
        sTaskId="taskAlpha", sName=S_NAME, sContainerId=S_CONTAINER_ID,
        iOwnerGeneration=1, taskAsync=None, admission=None,
    )
    appState = fappStateBuild(frecordOwnerBuild())
    assert commitCarrier._fbDurableTaskStillCurrent(appState, recordTask)
    recordTask.sState = "cancelRequested"
    assert not commitCarrier._fbDurableTaskStillCurrent(appState, recordTask)
    recordTask.sState = "running"
    assert not commitCarrier._fbDurableTaskStillCurrent(
        fappStateBuild(), recordTask,
    )


def testGatedHelperJournalsBeforeReleaseAndRunsTheCommand():
    """Each phase is reached in order and the holder identity is journaled."""
    listPhases = []
    dictLaunch = commitCarrier.fdictLaunchGatedHelperProcess(
        S_NAME, "helperTarget", [sys.executable, "-c", "pass"],
        fnPhaseCallback=listPhases.append,
    )
    processHelper = dictLaunch["processHelper"]
    try:
        assert processHelper.wait(timeout=20) == 0
    finally:
        if processHelper.poll() is None:
            processHelper.kill()
    assert listPhases == ["prepared", "spawned", "promoted"]
    dictRecord = operationJournal.fdictReadJournalOutcome(S_NAME)[
        "dictOperations"
    ][dictLaunch["sOperationId"]]
    assert dictRecord["iHolderPid"] == processHelper.pid
    assert dictRecord["sState"] == operationJournal.S_OPERATION_STATE_IN_FLIGHT


def testLiveWorkIsDescribedInTheResearchersWords():
    """Named supervisors, bare ones and durable tasks describe themselves."""
    appState = fappStateBuild()
    appState.dictMutationSupervisors = {
        "done": fsupervisorBuild(bDone=True),
        "foreign": fsupervisorBuild(sName="containerBeta"),
    }
    assert commitCarrier.fsDescribeLiveMutationWork(appState, S_NAME) == ""
    appState.dictDurableTaskRecords = {S_NAME: commitCarrier.DurableTaskRecord(
        sTaskId="t", sName=S_NAME, sContainerId=S_CONTAINER_ID,
        iOwnerGeneration=1, taskAsync=TaskStandIn(False), admission=None,
        sJoinableKind="pipelineRun",
    )}
    assert commitCarrier.fsDescribeLiveMutationWork(appState, S_NAME) == (
        commitCarrier.S_DESCRIBED_DURABLE_TASK
    )
    assert commitCarrier.fsDescribeWorkBlockingAJoin(
        appState, S_NAME, "pipelineRun",
    ) == ""
    appState.dictMutationSupervisors["bare"] = fsupervisorBuild()
    assert commitCarrier.fsDescribeWorkBlockingAJoin(
        appState, S_NAME, "pipelineRun",
    ) == commitCarrier.S_DESCRIBED_GUARDED_OPERATION
    assert commitCarrier._fsDescribeSupervisorWork(fsupervisorBuild(
        sOperationKind="file write",
    )) == "file write"


def testAutomaticReadSeesWorkBesidesItself():
    """Another live supervisor, a durable task, or a held lock is busy."""
    supervisorSelf = fsupervisorBuild(sOperationKind="repository read")
    appState = fappStateBuild()
    appState.dictMutationSupervisors = {
        "self": supervisorSelf,
        "done": fsupervisorBuild(bDone=True),
        "foreign": fsupervisorBuild(sName="containerBeta"),
    }
    lockFree = SimpleNamespace(locked=lambda: False)
    lockHeld = SimpleNamespace(locked=lambda: True)
    fsDescribe = commitCarrier._fsDescribeWorkBesidesThisSupervisor
    assert fsDescribe(appState, supervisorSelf, lockFree) == ""
    assert fsDescribe(appState, supervisorSelf, lockHeld) == (
        commitCarrier.S_DESCRIBED_GUARDED_OPERATION
    )
    appState.dictMutationSupervisors["other"] = fsupervisorBuild(
        sOperationKind="file write", sTarget="dataFile.csv",
    )
    assert fsDescribe(appState, supervisorSelf, lockFree) == (
        "file write on dataFile.csv"
    )
