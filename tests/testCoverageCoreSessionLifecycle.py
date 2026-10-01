"""sessionLifecycle's synchronous refusals and orphan predicates.

The browser-session store and the operation journal are real (the
journal redirected into tmp_path). Each transfer refusal is asserted
together with its rollback: a refused hand-over that left the
pre-minted successor credential valid would authorize a browser that
was never given the container.
"""

import asyncio
import json
import os
import time
from types import SimpleNamespace

import pytest

from vaibify.config import containerLock, operationJournal
from vaibify.gui import browserSession, containerOwnership, sessionLifecycle


S_NAME = "containerAlpha"
S_CONTAINER_ID = "c0ffee00containerid"
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


def frecordOwnerBuild(**dictFields):
    """Return an owner record for S_NAME."""
    dictBase = {
        "sLeaseId": "leaseAlpha", "fileHandleLock": None,
        "sContainerId": S_CONTAINER_ID, "sBrowserSessionId": S_SESSION,
    }
    dictBase.update(dictFields)
    return containerOwnership.OwnerRecord(**dictBase)


def ftStoreWithPreMint():
    """Return (dictStore, sNewCredential, sCapability) for a transfer."""
    dictStore = browserSession.fdictCreateBrowserSessionStore()
    _sSessionId, sCredential = browserSession.ftMintDetachedSessionRecord(
        dictStore,
    )
    sCapability = browserSession.fsMintTransferCapability(dictStore, S_NAME, 1)
    return dictStore, sCredential, sCapability


@pytest.mark.parametrize(
    "fappStateBuild,sExpectedOutcome,sFragment,bCapabilityExpired",
    [
        (lambda: SimpleNamespace(dictContainerOwners={}),
         sessionLifecycle.S_TRANSFER_UNOWNED, "became unowned", True),
        (lambda: SimpleNamespace(dictContainerOwners={
            S_NAME: frecordOwnerBuild(iOwnerGeneration=2)}),
         sessionLifecycle.S_TRANSFER_STALE_GENERATION, "changed owners", True),
        (lambda: SimpleNamespace(dictContainerOwners={
            S_NAME: frecordOwnerBuild(poison=containerOwnership.PoisonRecord(
                sGuardedOperationId="o", sContainerId=S_CONTAINER_ID,
                sTaskHandleId="t"))}),
         sessionLifecycle.S_TRANSFER_REFUSED, "force-abandoned", False),
        (lambda: SimpleNamespace(
            dictContainerOwners={S_NAME: frecordOwnerBuild()},
            dictDurableTaskRecords={S_NAME: SimpleNamespace(
                taskAsync=None, sState="cancelRequested")}),
         sessionLifecycle.S_TRANSFER_REFUSED, "cancellation is in progress",
         False),
        (lambda: SimpleNamespace(
            dictContainerOwners={S_NAME: frecordOwnerBuild()},
            dictTerminalExecutionRecords={S_NAME: {"operationAlpha": object()}}),
         sessionLifecycle.S_TRANSFER_REFUSED, "has not been proven dead",
         False),
    ],
)
def testCommitPointRefusalsRollBackThePreMintedSession(
    fappStateBuild, sExpectedOutcome, sFragment, bCapabilityExpired,
):
    """Every late refusal discards the successor credential it minted."""
    dictStore, sNewCredential, sCapability = ftStoreWithPreMint()
    assert browserSession.fbValidateCredential(dictStore, sNewCredential)
    tRefusal = sessionLifecycle._ftRefusalAtCommitPoint(
        fappStateBuild(), dictStore, sNewCredential, S_NAME, 1, sCapability,
    )
    assert tRefusal[0] == sExpectedOutcome
    assert sFragment in tRefusal[1]["sMessage"]
    assert not browserSession.fbValidateCredential(dictStore, sNewCredential)
    sCapabilityState = browserSession.fdictInspectTransferCapability(
        dictStore, sCapability,
    )["sState"]
    assert (sCapabilityState == "EXPIRED") is bCapabilityExpired


def testCommitPointAdmitsAHealthyRecord():
    """With nothing blocking, the commit point answers None and keeps the mint."""
    dictStore, sNewCredential, sCapability = ftStoreWithPreMint()
    appState = SimpleNamespace(dictContainerOwners={S_NAME: frecordOwnerBuild()})
    assert sessionLifecycle._ftRefusalAtCommitPoint(
        appState, dictStore, sNewCredential, S_NAME, 1, sCapability,
    ) is None
    assert browserSession.fbValidateCredential(dictStore, sNewCredential)


def fnWriteJournalPayload(dictOperations):
    """Write a schema-current journal for S_NAME."""
    os.makedirs(operationJournal._S_JOURNAL_DIRECTORY, exist_ok=True)
    with open(operationJournal.fsJournalPathFor(S_NAME), "w") as fileHandle:
        json.dump({
            "iSchemaVersion": operationJournal._I_JOURNAL_SCHEMA_VERSION,
            "sContainerName": S_NAME, "dictOperations": dictOperations,
        }, fileHandle)


def testJournalBlocksTransferUnlessEveryRecordIsAdoptable():
    """Malformed, quarantined and orphaned records each refuse by name."""
    appState = SimpleNamespace()
    fsReason = sessionLifecycle._fsUnadoptableJournalReason
    assert fsReason(appState, S_NAME, None) == ""
    os.makedirs(operationJournal._S_JOURNAL_DIRECTORY, exist_ok=True)
    with open(operationJournal.fsJournalPathFor(S_NAME), "w") as fileHandle:
        fileHandle.write("{ torn")
    assert "reads as quarantined" in fsReason(appState, S_NAME, None)
    sOperationId = "operationAlpha"
    dictRecord = {
        "sState": operationJournal.S_OPERATION_STATE_NEEDS_RECONCILIATION,
        "sKind": "exec", "sTarget": "stepAlpha", "sPreparedIso": "p",
    }
    fnWriteJournalPayload({sOperationId: dictRecord})
    assert "quarantined journal record (operationAlpha, kind exec, target "\
        "stepAlpha)" in fsReason(appState, S_NAME, None)
    dictRecord["sState"] = operationJournal.S_OPERATION_STATE_PREPARED
    fnWriteJournalPayload({sOperationId: dictRecord})
    assert "neither the live durable task" in fsReason(appState, S_NAME, None)
    appState.dictTerminalExecutionRecords = {S_NAME: {sOperationId: object()}}
    assert fsReason(appState, S_NAME, None) == ""


def testLiveDurableTaskLookupIgnoresFinishedTasks():
    """Only an unfinished durable task is live."""
    appState = SimpleNamespace(dictDurableTaskRecords={})
    assert sessionLifecycle._frecordLiveDurableTask(appState, S_NAME) is None
    recordTask = SimpleNamespace(taskAsync=SimpleNamespace(done=lambda: True))
    appState.dictDurableTaskRecords[S_NAME] = recordTask
    assert sessionLifecycle._frecordLiveDurableTask(appState, S_NAME) is None
    recordTask.taskAsync = SimpleNamespace(done=lambda: False)
    assert sessionLifecycle._frecordLiveDurableTask(appState, S_NAME) is recordTask


def testFailedStartFreesOnlyOwnershipItEstablishedAndStillHolds():
    """No identity, an inherited ownership, or a rotated one never frees."""
    recordOwner = frecordOwnerBuild()
    fbMayFree = sessionLifecycle._fbStartMayFreeOwnership
    assert fbMayFree(recordOwner, None) is False
    identityOwn = containerOwnership.OwnershipIdentity(
        sPriorOwnerLeaseId=containerOwnership.S_NO_PRIOR_OWNER,
        sLeaseId="leaseAlpha", iOwnerGeneration=1, sBrowserSessionId=S_SESSION,
    )
    assert fbMayFree(recordOwner, identityOwn) is True
    recordOwner.sLeaseId = "leaseRotated"
    assert fbMayFree(recordOwner, identityOwn) is False


def testReconnectWindowPredicateNeedsAnActiveSocketlessStaleRecord():
    """Orphaned, never-connected, connected, or recent records do not orphan."""
    fbPast = sessionLifecycle._fbOwnerPastReconnectWindow
    fLongAgo = time.monotonic() - sessionLifecycle.F_RECONNECT_WINDOW_SECONDS - 5
    assert fbPast(frecordOwnerBuild(
        sState=containerOwnership.S_OWNER_STATE_ORPHANED_SESSION,
        bSocketEverExisted=True, fLastSeenMonotonic=fLongAgo)) is False
    assert fbPast(frecordOwnerBuild(fLastSeenMonotonic=fLongAgo)) is False
    assert fbPast(frecordOwnerBuild(
        bSocketEverExisted=True, iLiveConnectionCount=1,
        fLastSeenMonotonic=fLongAgo)) is False
    assert fbPast(frecordOwnerBuild(bSocketEverExisted=True)) is False
    assert fbPast(frecordOwnerBuild(
        bSocketEverExisted=True, fLastSeenMonotonic=fLongAgo)) is True


def testActiveSessionOwnershipCheckRefusesOrphanedAndMissingRecords():
    """Only an ACTIVE record bound to the same session counts."""
    fbOwned = sessionLifecycle._fbOwnerRecordIsOwnedByActiveSession
    assert fbOwned(None, S_SESSION) is False
    assert fbOwned(frecordOwnerBuild(
        sState=containerOwnership.S_OWNER_STATE_ORPHANED_SESSION), S_SESSION,
    ) is False
    assert fbOwned(frecordOwnerBuild(), "browserSessionBeta") is False
    assert fbOwned(frecordOwnerBuild(), S_SESSION) is True


def testExpirySweepWithoutAStoreDoesNothing():
    """A hub that never minted a session has nothing to expire."""
    appState = SimpleNamespace(dictBrowserSessions=None)
    asyncio.run(sessionLifecycle.fnExpireIdleBrowserSessions(appState))
    assert appState.dictBrowserSessions is None


class WebSocketRecorder:
    """A socket that records its close code, or fails to close."""

    def __init__(self, bFails=False):
        self.bFails = bFails
        self.listCodes = []

    async def close(self, code):
        if self.bFails:
            raise RuntimeError("socket already dead")
        self.listCodes.append(code)


def testFencingClosesEverySocketEvenWhenOneFails():
    """A dead socket does not stop the remaining ones being closed."""
    websocketDead = WebSocketRecorder(bFails=True)
    websocketLive = WebSocketRecorder()
    listConnections = [
        SimpleNamespace(websocket=websocketDead),
        SimpleNamespace(websocket=websocketLive),
    ]

    async def fnDrive():
        sessionLifecycle.fnScheduleConnectionFencing(listConnections)
        sessionLifecycle.fnScheduleConnectionFencing([])
        for _ in range(3):
            await asyncio.sleep(0)

    asyncio.run(fnDrive())
    assert websocketLive.listCodes == [4401]
