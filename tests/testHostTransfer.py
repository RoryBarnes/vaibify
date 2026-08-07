"""Tests for the host-authorized transfer transaction (slice 5, §6).

Drives ``sessionLifecycle.ftTransferOwnership`` against real stores:
a real browser-session store (so revocation and the bounded replay are
observable), a real owner record, the real commit-guard carrier for
the mode-(c) adoption cases, and real journal records for the
DRAINING cases. The container NAME stays distinct from the Docker ID
throughout (repo epistemics rule).
"""

import asyncio
import logging
import threading
import time
from types import SimpleNamespace

import pytest

from vaibify.config import containerLock, operationJournal
from vaibify.gui import (
    browserSession,
    commitCarrier,
    containerOwnership,
    sessionLifecycle,
    terminalContainment,
)

S_PROJECT_NAME = "SampleProject"
S_CONTAINER_ID = "cid-0123456789ab"


@pytest.fixture(autouse=True)
def fixtureIsolateLockDirectory(tmp_path, monkeypatch):
    """Redirect the host flock directory to a per-test tmp_path."""
    monkeypatch.setattr(
        containerLock, "_S_LOCK_DIRECTORY", str(tmp_path / "locks"),
    )


@pytest.fixture(autouse=True)
def fixtureShortTransferWaits(monkeypatch):
    """Bound every transfer/terminal wait so no test sleeps for real.

    A transfer no longer waits on the mutation lock at all -- a busy
    container is refused at once -- so there is no drain wait left to
    shorten; only the terminal-drain waits below still bound anything.
    """
    monkeypatch.setattr(
        sessionLifecycle, "F_TRANSFER_COMMIT_HEADROOM_SECONDS", 5.0,
    )
    monkeypatch.setattr(
        terminalContainment, "F_TERMINATE_WAIT_SECONDS", 0.02,
    )
    monkeypatch.setattr(terminalContainment, "F_KILL_WAIT_SECONDS", 0.02)


def _fstateBuildAppState():
    """Return a bare app.state stand-in with every slice-5 store."""
    return SimpleNamespace(
        dictContainerOwners={},
        dictSessionOwner=containerOwnership.fdictCreateSessionOwnerIndex(),
        dictSessionSockets=(
            containerOwnership.fdictCreateSessionSocketIndex()
        ),
        dictBrowserSessions=browserSession.fdictCreateBrowserSessionStore(),
        dictDurableTaskRecords=(
            commitCarrier.fdictCreateDurableTaskRegistry()
        ),
        dictTerminalExecutionRecords=(
            terminalContainment.fdictCreateTerminalRecordRegistry()
        ),
    )


def _tSeedOwnedContainer(stateApp, sState=None):
    """Seed an owned container with a REAL old browser session.

    Returns ``(sOldSessionId, sOldCredential, sOldLease)``. The old
    session comes from a genuine bootstrap redemption so its
    credential exists in the store and its revocation is observable.
    """
    sCapability = browserSession.fsMintBootstrapCapability(
        stateApp.dictBrowserSessions,
    )
    sOldSessionId, sOldCredential = browserSession.ftRedeemCapability(
        stateApp.dictBrowserSessions, sCapability,
    )
    sOldLease = containerOwnership.fsMintLease()
    recordOwner = containerOwnership.OwnerRecord(
        sLeaseId=sOldLease,
        fileHandleLock=None,
        sAgentToken=containerOwnership.fsMintAgentToken(),
        sContainerId=S_CONTAINER_ID,
        sBrowserSessionId=sOldSessionId,
    )
    if sState is not None:
        recordOwner.sState = sState
        if sState == containerOwnership.S_OWNER_STATE_ORPHANED_SESSION:
            recordOwner.fOrphanedSinceMonotonic = time.monotonic()
    stateApp.dictContainerOwners[S_PROJECT_NAME] = recordOwner
    stateApp.dictSessionOwner[sOldSessionId] = S_PROJECT_NAME
    return (sOldSessionId, sOldCredential, sOldLease)


def _fsMintTransferCapability(stateApp, iExpectedOwnerGeneration=1):
    """Mint a transfer capability against the seeded container."""
    return browserSession.fsMintTransferCapability(
        stateApp.dictBrowserSessions, S_PROJECT_NAME,
        iExpectedOwnerGeneration,
    )


async def _tTransfer(stateApp, sCapability):
    """Run the transfer and return its ``(sOutcome, dictPayload)``."""
    return await sessionLifecycle.ftTransferOwnership(
        stateApp, sCapability,
    )


class _FakeWebSocketConnection:
    """A stand-in socket that records its active close."""

    def __init__(self):
        self.listCloseCodes = []

    async def close(self, code=1000):
        self.listCloseCodes.append(code)


class _FakeDockerForTerminals:
    """The Docker surface a terminal drain touches, provable or not."""

    def __init__(self, bProvable=True):
        self.bProvable = bProvable
        self.listSignals = []

    def fnSignalProcessGroupMembers(self, sContainerId, iGroup, sSignal):
        self.listSignals.append(sSignal)

    def fdictProbeProcessGroupMembers(self, sContainerId, iGroup):
        if self.bProvable:
            return {
                "bConclusive": True, "iMemberCount": 0,
                "sDetail": "group empty",
            }
        return {
            "bConclusive": False, "iMemberCount": -1,
            "sDetail": "probe inconclusive",
        }

    def fdictInspectExec(self, sExecId):
        return {"Running": False}


def _recordSeedTerminalRecord(stateApp, connectionDocker):
    """Journal and register one live terminal record for the container."""
    sOperationId = terminalContainment.fsPrepareTerminalOperation(
        S_PROJECT_NAME, S_CONTAINER_ID,
    )
    terminalContainment.fnPromoteTerminalOperation(
        S_PROJECT_NAME, sOperationId, "exec-feed", S_CONTAINER_ID, 1,
    )
    recordTerminal = terminalContainment.TerminalExecutionRecord(
        sOperationId=sOperationId,
        sContainerName=S_PROJECT_NAME,
        sContainerId=S_CONTAINER_ID,
        sDockerExecId="exec-feed",
        iOwnerGeneration=1,
        connectionDocker=connectionDocker,
        dictRegistry=None,
        iProcessGroup=4242,
    )
    terminalContainment.fnRegisterTerminalRecord(stateApp, recordTerminal)
    return recordTerminal


def _dictJournalOperations():
    return operationJournal.fdictReadJournalOutcome(S_PROJECT_NAME)[
        "dictOperations"
    ]


# -- the happy path and its invariants -------------------------------------


@pytest.mark.asyncio
async def testTransferRotatesLeaseSessionAndGeneration():
    """A correct-generation transfer rotates every browser principal."""
    stateApp = _fstateBuildAppState()
    sOldSessionId, sOldCredential, sOldLease = _tSeedOwnedContainer(
        stateApp,
    )
    sCapability = _fsMintTransferCapability(stateApp)
    sOutcome, dictPayload = await _tTransfer(stateApp, sCapability)
    assert sOutcome == sessionLifecycle.S_TRANSFER_TRANSFERRED
    recordOwner = stateApp.dictContainerOwners[S_PROJECT_NAME]
    assert dictPayload["sLeaseId"] == recordOwner.sLeaseId != sOldLease
    assert recordOwner.iOwnerGeneration == 2
    assert dictPayload["iOwnerGeneration"] == 2
    assert recordOwner.sBrowserSessionId == dictPayload["sSessionId"]
    assert recordOwner.sBrowserSessionId != sOldSessionId
    assert stateApp.dictSessionOwner == {
        dictPayload["sSessionId"]: S_PROJECT_NAME,
    }
    assert browserSession.fbValidateCredential(
        stateApp.dictBrowserSessions, dictPayload["sCredential"],
    ) is True
    assert browserSession.fbValidateCredential(
        stateApp.dictBrowserSessions, sOldCredential,
    ) is False, "the old session's credential must be revoked in-commit"


@pytest.mark.asyncio
@pytest.mark.falsification
async def testCorrectGenerationActiveTransferSucceedsAndRevokes():
    """Case 2 (slice-5 half): ACTIVE transfer succeeds and revokes.

    From ACTIVE the transfer revokes the old session in the SAME
    commit (§6.2): after a transferred outcome the old record is
    REVOKED and the new session-bound lease authorizes release while
    the old one is refused.

    Kills: dropping ``fbRevokeSessionById`` from ``_ftCommitTransfer``
    (the old credential would keep authorizing after the transfer).
    """
    stateApp = _fstateBuildAppState()
    sOldSessionId, sOldCredential, sOldLease = _tSeedOwnedContainer(
        stateApp,
    )
    sCapability = _fsMintTransferCapability(stateApp)
    sOutcome, dictPayload = await _tTransfer(stateApp, sCapability)
    assert sOutcome == sessionLifecycle.S_TRANSFER_TRANSFERRED
    recordOldSession = stateApp.dictBrowserSessions[
        "dictSessionsByCredential"
    ][sOldCredential]
    assert recordOldSession.sState == (
        browserSession.S_SESSION_STATE_REVOKED
    )
    assert browserSession.fbValidateCredential(
        stateApp.dictBrowserSessions, sOldCredential,
    ) is False


@pytest.mark.asyncio
@pytest.mark.falsification
async def testStaleGenerationTransferIsRefused():
    """Case 2 (slice-5 half): a stale-generation transfer is refused.

    A capability minted for generation 1 presented after the record
    reached generation 2 must refuse without touching the owner — the
    ABA guard that keeps a stale host request from displacing the
    successor — and it must refuse BEFORE the DRAINING phase, so a
    doomed transfer never fences or kills the successor's terminals.

    Kills: neutralizing the ``iOwnerGeneration != iExpectedGen``
    refusal in ``_ftRefusalBeforePremint`` (the commit-point backstop
    still refuses, but only after draining the successor's live
    terminal, which this test detects).
    """
    stateApp = _fstateBuildAppState()
    _tSeedOwnedContainer(stateApp)
    sStaleCapability = _fsMintTransferCapability(
        stateApp, iExpectedOwnerGeneration=1,
    )
    sFirstCapability = _fsMintTransferCapability(
        stateApp, iExpectedOwnerGeneration=1,
    )
    sOutcome, dictPayload = await _tTransfer(stateApp, sFirstCapability)
    assert sOutcome == sessionLifecycle.S_TRANSFER_TRANSFERRED
    sLeaseAfterFirst = stateApp.dictContainerOwners[
        S_PROJECT_NAME
    ].sLeaseId
    _recordSeedTerminalRecord(
        stateApp, _FakeDockerForTerminals(bProvable=True),
    )
    sStaleOutcome, dictRefusal = await _tTransfer(
        stateApp, sStaleCapability,
    )
    assert sStaleOutcome == sessionLifecycle.S_TRANSFER_STALE_GENERATION
    recordOwner = stateApp.dictContainerOwners[S_PROJECT_NAME]
    assert recordOwner.iOwnerGeneration == 2, (
        "a refused stale transfer must not bump the generation"
    )
    assert recordOwner.sLeaseId == sLeaseAfterFirst, (
        "a refused stale transfer must not rotate the lease"
    )
    assert "changed owners" in dictRefusal["sMessage"]
    assert terminalContainment.fbContainerHasLiveTerminalRecords(
        stateApp, S_PROJECT_NAME,
    ) is True, (
        "a stale transfer must refuse before touching the successor's "
        "terminals"
    )


@pytest.mark.asyncio
@pytest.mark.falsification
async def testLostTransferResponseReplaysTheStoredTuple():
    """Case 3: re-presenting a redeemed capability replays, not re-runs.

    A lost transfer response is recovered by presenting the SAME
    capability again within the replay window: the stored tuple comes
    back verbatim and the generation is NOT bumped a second time.

    Kills: making ``fnStoreTransferResult`` skip storing the issued
    lease, so the replay returns an empty lease instead of the
    committed one.
    """
    stateApp = _fstateBuildAppState()
    _tSeedOwnedContainer(stateApp)
    sCapability = _fsMintTransferCapability(stateApp)
    sFirstOutcome, dictFirst = await _tTransfer(stateApp, sCapability)
    assert sFirstOutcome == sessionLifecycle.S_TRANSFER_TRANSFERRED
    sReplayOutcome, dictReplay = await _tTransfer(stateApp, sCapability)
    assert sReplayOutcome == sessionLifecycle.S_TRANSFER_TRANSFERRED
    assert dictReplay["bReplayed"] is True
    for sKey in ("sSessionId", "sCredential", "sLeaseId",
                 "iOwnerGeneration"):
        assert dictReplay[sKey] == dictFirst[sKey], (
            f"the replay must return the stored {sKey}"
        )
    assert stateApp.dictContainerOwners[
        S_PROJECT_NAME
    ].iOwnerGeneration == 2, "a replay must never bump the generation"


@pytest.mark.asyncio
@pytest.mark.falsification
async def testReapedRecordYieldsClaimNormally():
    """Case 14: transfer vs a reaper-released record → claim normally.

    A capability minted while owned, redeemed after the record was
    released, must answer "container unowned — claim normally", never
    mint ownership out of thin air and never tell the client to retry.

    Kills: rewording ``_ftRefusalBeforePremint``'s unowned outcome to
    ``S_TRANSFER_BUSY_RETRY``, which would send the client into a
    hopeless retry loop instead of the claim path.
    """
    stateApp = _fstateBuildAppState()
    _tSeedOwnedContainer(stateApp)
    sCapability = _fsMintTransferCapability(stateApp)
    stateApp.dictContainerOwners.clear()
    stateApp.dictSessionOwner.clear()
    sOutcome, dictPayload = await _tTransfer(stateApp, sCapability)
    assert sOutcome == sessionLifecycle.S_TRANSFER_UNOWNED
    assert "claim it normally" in dictPayload["sMessage"]
    assert stateApp.dictContainerOwners == {}, (
        "a transfer of an unowned container must not mint an owner"
    )


@pytest.mark.asyncio
async def testBusyDrainLeavesCapabilityArmedForRetry():
    """A held drain answers busy-retry; the same capability then works."""
    stateApp = _fstateBuildAppState()
    _tSeedOwnedContainer(stateApp)
    sCapability = _fsMintTransferCapability(stateApp)
    lockMutation = sessionLifecycle.flockContainerMutationForAppState(
        stateApp, S_PROJECT_NAME,
    )
    await lockMutation.acquire()
    try:
        sOutcome, dictPayload = await _tTransfer(stateApp, sCapability)
    finally:
        lockMutation.release()
    assert sOutcome == sessionLifecycle.S_TRANSFER_BUSY_RETRY
    assert "busy" in dictPayload["sMessage"]
    recordOwner = stateApp.dictContainerOwners[S_PROJECT_NAME]
    assert recordOwner.iOwnerGeneration == 1, (
        "a busy-retry must mint, revoke, and bump nothing"
    )
    sRetryOutcome, dictRetry = await _tTransfer(stateApp, sCapability)
    assert sRetryOutcome == sessionLifecycle.S_TRANSFER_TRANSFERRED
    assert dictRetry["bReplayed"] is False


@pytest.mark.asyncio
async def testInsufficientTtlExpiresInsteadOfRetrying(monkeypatch):
    """Too little remaining TTL expires the capability (mint afresh)."""
    stateApp = _fstateBuildAppState()
    _tSeedOwnedContainer(stateApp)
    sCapability = _fsMintTransferCapability(stateApp)
    recordCap = stateApp.dictBrowserSessions["dictCapabilities"][
        sCapability
    ]
    recordCap.fMintedMonotonic = time.monotonic() - (
        browserSession.I_CAPABILITY_TTL_SECONDS - 1.0
    )
    sOutcome, dictPayload = await _tTransfer(stateApp, sCapability)
    assert sOutcome == sessionLifecycle.S_TRANSFER_EXPIRED
    assert recordCap.sState == "EXPIRED"
    assert stateApp.dictContainerOwners[
        S_PROJECT_NAME
    ].iOwnerGeneration == 1


@pytest.mark.asyncio
@pytest.mark.falsification
async def testPoisonedRecordRefusesTransfer():
    """Case 26b (slice-5 half): poison refuses transfer, retains all.

    A force-abandoned owner record keeps its flock and refuses every
    transfer until the worker is proven dead — never a silent rebind
    onto a container a zombie worker may still write — and it refuses
    BEFORE the DRAINING phase, leaving the terminals untouched.

    Kills: neutralizing the poison refusal in
    ``_ftRefusalBeforePremint`` (the commit-point backstop still
    refuses, but only after draining the live terminal, which this
    test detects).
    """
    stateApp = _fstateBuildAppState()
    _, _, sOldLease = _tSeedOwnedContainer(stateApp)
    recordOwner = stateApp.dictContainerOwners[S_PROJECT_NAME]
    recordOwner.poison = containerOwnership.PoisonRecord(
        sGuardedOperationId="op-wedged",
    )
    _recordSeedTerminalRecord(
        stateApp, _FakeDockerForTerminals(bProvable=True),
    )
    sCapability = _fsMintTransferCapability(stateApp)
    sOutcome, dictPayload = await _tTransfer(stateApp, sCapability)
    assert sOutcome == sessionLifecycle.S_TRANSFER_REFUSED
    assert "reconcile" in dictPayload["sMessage"]
    assert recordOwner.sLeaseId == sOldLease
    assert recordOwner.iOwnerGeneration == 1
    assert terminalContainment.fbContainerHasLiveTerminalRecords(
        stateApp, S_PROJECT_NAME,
    ) is True, (
        "a poisoned record must refuse before touching its terminals"
    )


@pytest.mark.asyncio
@pytest.mark.falsification
async def testCancelRequestedDurableTaskRefusesTransfer():
    """Case 31 (slice-5 half): a cancel that won the lock blocks transfer.

    Mode-(c) cancel and transfer compete for the brief mutation lock;
    when cancel wins it marks the task ``cancelRequested`` and a
    subsequent transfer must refuse rather than adopt a dying task —
    refusing BEFORE the DRAINING phase touches any terminal.

    Kills: weakening the live-task state refusal in
    ``_ftRefusalBeforePremint`` to ignore ``sState`` (the commit-point
    backstop still refuses, but only after draining the live
    terminal, which this test detects).
    """
    stateApp = _fstateBuildAppState()
    _tSeedOwnedContainer(stateApp)
    eventBarrier = asyncio.Event()
    taskHeld = asyncio.ensure_future(eventBarrier.wait())
    stateApp.dictDurableTaskRecords[S_PROJECT_NAME] = (
        commitCarrier.DurableTaskRecord(
            sTaskId="task-cancelling", sName=S_PROJECT_NAME,
            sContainerId=S_CONTAINER_ID, iOwnerGeneration=1,
            taskAsync=taskHeld, admission=None,
            sState="cancelRequested",
        )
    )
    _recordSeedTerminalRecord(
        stateApp, _FakeDockerForTerminals(bProvable=True),
    )
    sCapability = _fsMintTransferCapability(stateApp)
    sOutcome, dictPayload = await _tTransfer(stateApp, sCapability)
    assert sOutcome == sessionLifecycle.S_TRANSFER_REFUSED
    assert "cancellation is in progress" in dictPayload["sMessage"]
    assert stateApp.dictContainerOwners[
        S_PROJECT_NAME
    ].iOwnerGeneration == 1
    assert terminalContainment.fbContainerHasLiveTerminalRecords(
        stateApp, S_PROJECT_NAME,
    ) is True, (
        "a cancel-refused transfer must not touch the terminals"
    )
    eventBarrier.set()
    await taskHeld


# -- mode-(c) adoption: the preserved task carries over ---------------------


def _dictBuildBrowserLaneTuple(stateApp, sOldSessionId, sOldLease):
    """Return the pre-transfer browser lane tuple for the container."""
    return {
        "sLane": "browser",
        "iOwnerGeneration": 1,
        "sBrowserSessionId": sOldSessionId,
        "sLeaseId": sOldLease,
        "sContainerName": S_PROJECT_NAME,
    }


@pytest.mark.asyncio
@pytest.mark.falsification
async def testBarrierTransferAdoptsAStillRunningDurableTask(caplog):
    """Case 23 (slice-5 half): the barrier test — adopt, then finish.

    A durable task is held running behind a barrier; the transfer
    completes BEFORE the barrier releases (mode (c): the task executes
    outside the lock, so the briefly-held lock is free); the retagged
    task then finishes under the SUCCESSOR generation with the
    compare-matched cleanup evicting its registry entry.

    Kills: hardening the transfer's live-task tolerance into a blanket
    refusal (``recordTask is not None`` alone) — the exact
    "different operation ⇒ refuse" mistake the §8 adoption exception
    exists to prevent; the transfer would wait out every run.
    """
    from vaibify.gui.pipelineServer import _fnRegisterPipelineTask
    caplog.set_level(logging.DEBUG, logger="vaibify")
    stateApp = _fstateBuildAppState()
    sOldSessionId, _, sOldLease = _tSeedOwnedContainer(stateApp)
    dictLaneTuple = _dictBuildBrowserLaneTuple(
        stateApp, sOldSessionId, sOldLease,
    )
    eventBarrier = asyncio.Event()

    def fnStartHeldTask():
        return asyncio.ensure_future(eventBarrier.wait())

    dictLaunch = await commitCarrier.fdictLaunchDurableTask(
        stateApp, S_PROJECT_NAME, S_CONTAINER_ID, dictLaneTuple,
        fnStartHeldTask,
    )
    assert dictLaunch["bLaunched"] is True
    dictPipelineTasks = {}
    _fnRegisterPipelineTask(
        dictPipelineTasks, S_CONTAINER_ID, dictLaunch["taskAsync"],
        iOwnerGeneration=dictLaunch["iOwnerGeneration"],
    )
    sCapability = _fsMintTransferCapability(stateApp)
    sOutcome, dictPayload = await _tTransfer(stateApp, sCapability)
    assert sOutcome == sessionLifecycle.S_TRANSFER_TRANSFERRED, (
        "the transfer must complete WHILE the task is still running"
    )
    recordTask = stateApp.dictDurableTaskRecords[S_PROJECT_NAME]
    assert not recordTask.taskAsync.done(), (
        "the barrier must still hold: transfer may not wait the task out"
    )
    assert recordTask.iOwnerGeneration == 2
    assert recordTask.taskAsync.iOwnerGeneration == 2
    assert commitCarrier._fbDurableTaskStillCurrent(
        stateApp, recordTask,
    ) is True, "the retagged task must stay admitted under the successor"
    eventBarrier.set()
    await recordTask.taskAsync
    for _ in range(4):
        await asyncio.sleep(0)
    assert S_PROJECT_NAME not in stateApp.dictDurableTaskRecords, (
        "completion must compare-match its stable id and clean up"
    )
    assert any(
        "generation 2" in record.getMessage() for record in caplog.records
    ), "the completion must be attributed to the successor generation"


@pytest.mark.asyncio
@pytest.mark.falsification
async def testPreservedTaskCompletesAttributedToNewGeneration():
    """Case 5: the preserved task keeps committing as the successor.

    After the transfer the still-running task's commit-time
    revalidator must ADMIT it (the mutable record generation matches
    the bumped owner), while a snapshot of the OLD generation is
    refused — the task was adopted, not killed and not left stale.

    Kills: dropping the in-place task retag
    (``_fnRetagLiveDurableTask``) from the transfer commit.
    """
    stateApp = _fstateBuildAppState()
    sOldSessionId, _, sOldLease = _tSeedOwnedContainer(stateApp)
    dictLaneTuple = _dictBuildBrowserLaneTuple(
        stateApp, sOldSessionId, sOldLease,
    )
    eventBarrier = asyncio.Event()
    dictLaunch = await commitCarrier.fdictLaunchDurableTask(
        stateApp, S_PROJECT_NAME, S_CONTAINER_ID, dictLaneTuple,
        lambda: asyncio.ensure_future(eventBarrier.wait()),
    )
    assert dictLaunch["bLaunched"] is True
    sCapability = _fsMintTransferCapability(stateApp)
    sOutcome, _ = await _tTransfer(stateApp, sCapability)
    assert sOutcome == sessionLifecycle.S_TRANSFER_TRANSFERRED
    recordTask = stateApp.dictDurableTaskRecords[S_PROJECT_NAME]
    assert commitCarrier._fbDurableTaskStillCurrent(
        stateApp, recordTask,
    ) is True
    eventBarrier.set()
    await recordTask.taskAsync


@pytest.mark.asyncio
@pytest.mark.falsification
async def testOldLaneTupleCannotCommitAfterTransfer():
    """Case 4: an old-generation ordinary mutation fails at the carrier.

    Driven through the REAL transfer and the REAL mode-(a) carrier: a
    lane tuple captured before the transfer must be refused at the
    commit point afterwards, and the refused effect must never run.

    Kills: dropping the generation comparison from
    ``commitCarrier.fbLaneTupleStillCurrent``.
    """
    stateApp = _fstateBuildAppState()
    sOldSessionId, _, sOldLease = _tSeedOwnedContainer(stateApp)
    dictOldLaneTuple = _dictBuildBrowserLaneTuple(
        stateApp, sOldSessionId, sOldLease,
    )
    assert commitCarrier.fbLaneTupleStillCurrent(
        stateApp, dictOldLaneTuple,
    ) is True
    sCapability = _fsMintTransferCapability(stateApp)
    sOutcome, _ = await _tTransfer(stateApp, sCapability)
    assert sOutcome == sessionLifecycle.S_TRANSFER_TRANSFERRED
    listEffectRuns = []
    with pytest.raises(commitCarrier.CommitRefusedError):
        commitCarrier.fdictCommitSynchronousMutation(
            stateApp, S_PROJECT_NAME, S_CONTAINER_ID, dictOldLaneTuple,
            "file-write", "/tmp/target",
            lambda: listEffectRuns.append(True),
            {"iHolderPid": 1},
        )
    assert listEffectRuns == [], (
        "a refused old-generation mutation must never run its effect"
    )
    assert _dictJournalOperations() == {}, (
        "the refusal must precede the journal write"
    )


@pytest.mark.asyncio
@pytest.mark.falsification
async def testOldGenerationCleanupCannotTouchNewGenerationState():
    """Case 6: stale-generation cleanup is inert against the successor.

    The transfer detaches the old sockets and zeroes the counters; a
    detached record's late ``finally`` (generation 1 against owner
    generation 2) must decrement nothing and must not refresh the
    successor's liveness clock — and the detached sockets are actively
    closed after the commit.

    Kills: zeroing only the counters without detaching — dropping the
    ``dictSessionSockets.pop`` so the old session's records survive
    the transfer and its active close never happens.
    """
    stateApp = _fstateBuildAppState()
    sOldSessionId, _, _ = _tSeedOwnedContainer(stateApp)
    recordOwner = stateApp.dictContainerOwners[S_PROJECT_NAME]
    connectionOld = _FakeWebSocketConnection()
    recordConnection = containerOwnership.ConnectionRecord(
        websocket=connectionOld, sBrowserSessionId=sOldSessionId,
        iOwnerGeneration=1, sLane=containerOwnership.S_LANE_PIPELINE,
    )
    containerOwnership.fnRegisterSessionSocket(
        stateApp.dictSessionSockets, recordConnection,
    )
    recordOwner.iLiveConnectionCount = 1
    recordOwner.iLivePipelineConnectionCount = 1
    sCapability = _fsMintTransferCapability(stateApp)
    sOutcome, _ = await _tTransfer(stateApp, sCapability)
    assert sOutcome == sessionLifecycle.S_TRANSFER_TRANSFERRED
    assert recordOwner.iLiveConnectionCount == 0
    assert recordOwner.iLivePipelineConnectionCount == 0
    assert sOldSessionId not in stateApp.dictSessionSockets
    assert connectionOld.listCloseCodes == [4401], (
        "the detached socket must be actively closed after the commit"
    )
    fLastSeenAtCommit = recordOwner.fLastSeenMonotonic
    containerOwnership.fnDecrementLiveConnectionForRecord(
        stateApp.dictContainerOwners, S_PROJECT_NAME, recordConnection,
    )
    assert recordOwner.iLiveConnectionCount == 0
    assert recordOwner.fLastSeenMonotonic == fLastSeenAtCommit, (
        "a stale-generation finally must not refresh the successor"
    )


@pytest.mark.asyncio
@pytest.mark.falsification
async def testAgentAuthorizationSurvivesTransfer():
    """Case 8 (transfer half): the agent lane rides through unchanged.

    The per-container agent token and the host flock are deliberately
    untouched by a transfer (§6.2): the in-container agent keeps
    authorizing against the same container across the rotation.

    Kills: making ``_ftCommitTransfer`` also rotate
    ``recordOwner.sAgentToken``, which would cut off the working agent
    mid-session on every ``vaibify open``.
    """
    stateApp = _fstateBuildAppState()
    _tSeedOwnedContainer(stateApp)
    recordOwner = stateApp.dictContainerOwners[S_PROJECT_NAME]
    sAgentToken = recordOwner.sAgentToken
    fileHandleFlock = object()
    recordOwner.fileHandleLock = fileHandleFlock
    sCapability = _fsMintTransferCapability(stateApp)
    sOutcome, _ = await _tTransfer(stateApp, sCapability)
    assert sOutcome == sessionLifecycle.S_TRANSFER_TRANSFERRED
    assert recordOwner.sAgentToken == sAgentToken
    assert recordOwner.fileHandleLock is fileHandleFlock
    assert containerOwnership.fbAgentTokenAuthorizesContainerId(
        stateApp.dictContainerOwners, sAgentToken, S_CONTAINER_ID,
    ) is True


@pytest.mark.asyncio
@pytest.mark.falsification
async def testStaleGenerationReleaseIsRefusedAfterTransfer():
    """Case 12 (slice-5 half): the displaced owner cannot release.

    After a transfer the old session presenting its old lease must be
    refused release (the record is retained), while the successor's
    session-bound lease releases cleanly.

    Kills: dropping the lease rotation
    (``recordOwner.sLeaseId = sNewLease``) from the transfer commit,
    which would leave the displaced session able to drop the
    successor's record.
    """
    stateApp = _fstateBuildAppState()
    sOldSessionId, _, sOldLease = _tSeedOwnedContainer(stateApp)
    sCapability = _fsMintTransferCapability(stateApp)
    sOutcome, dictPayload = await _tTransfer(stateApp, sCapability)
    assert sOutcome == sessionLifecycle.S_TRANSFER_TRANSFERRED
    assert await sessionLifecycle.fbReleaseExplicit(
        stateApp, S_PROJECT_NAME, sOldLease,
        sBrowserSessionId=sOldSessionId,
    ) is False
    assert S_PROJECT_NAME in stateApp.dictContainerOwners, (
        "a stale release must not drop the successor's record"
    )
    assert await sessionLifecycle.fbReleaseExplicit(
        stateApp, S_PROJECT_NAME, dictPayload["sLeaseId"],
        sBrowserSessionId=dictPayload["sSessionId"],
    ) is True


@pytest.mark.asyncio
async def testTransferFromOrphanedRecordReactivates():
    """§6.2: a transfer works from ORPHANED and re-activates the record."""
    stateApp = _fstateBuildAppState()
    _tSeedOwnedContainer(
        stateApp,
        sState=containerOwnership.S_OWNER_STATE_ORPHANED_SESSION,
    )
    sCapability = _fsMintTransferCapability(stateApp)
    sOutcome, _ = await _tTransfer(stateApp, sCapability)
    assert sOutcome == sessionLifecycle.S_TRANSFER_TRANSFERRED
    recordOwner = stateApp.dictContainerOwners[S_PROJECT_NAME]
    assert recordOwner.sState == containerOwnership.S_OWNER_STATE_ACTIVE
    assert recordOwner.fOrphanedSinceMonotonic == 0.0


def testBootstrapRedemptionRefusesATransferCapability():
    """The plain bootstrap lane may never redeem a transfer capability."""
    dictStore = browserSession.fdictCreateBrowserSessionStore()
    sCapability = browserSession.fsMintTransferCapability(
        dictStore, S_PROJECT_NAME, 1,
    )
    assert browserSession.ftRedeemCapability(
        dictStore, sCapability,
    ) == (None, None)
    assert browserSession.fsCapabilityOperationKind(
        dictStore, sCapability,
    ) == browserSession.S_CAPABILITY_OPERATION_TRANSFER


# -- DRAINING: terminals are proven dead or the transfer refuses ------------


@pytest.mark.asyncio
@pytest.mark.falsification
async def testTransferRefusesOverALiveTerminalRecordAndSignalsNothing():
    """Case 44 (transfer half): the hand-over refuses, it does not drain.

    The transfer used to FENCE and TERMINATE a live terminal record
    before committing -- the DRAINING phase. That phase is gone, and
    with it the wait it forced inside the held lock. The property it
    protected survives and is stronger: a hand-over must never carry a
    terminal execution nobody has proven dead, so it refuses outright
    and names the recovery.

    With the terminal disabled such a record can only be inherited from
    an earlier version, and wave 0's rule for those is that they stay
    quarantined until reconciliation -- draining one during a transfer
    would have settled it on the strength of a probe rather than a stop.

    Asserted on the SIGNALS as well as the outcome: a refusal that had
    already signalled the group would have done the drain's work
    without the drain's proof.

    Kills: in sessionLifecycle, softening the live-terminal-record
    refusal at the commit point back to S_TRANSFER_BUSY_RETRY, which
    invites an immediate retry over a record that will still be there.
    """
    stateApp = _fstateBuildAppState()
    _, _sOldCredential, sOldLease = _tSeedOwnedContainer(stateApp)
    connectionDocker = _FakeDockerForTerminals(bProvable=True)
    recordTerminal = _recordSeedTerminalRecord(stateApp, connectionDocker)
    sCapability = _fsMintTransferCapability(stateApp)
    sOutcome, dictPayload = await _tTransfer(stateApp, sCapability)
    assert sOutcome == sessionLifecycle.S_TRANSFER_REFUSED, (
        f"a hand-over must not carry an unproven terminal: {dictPayload}"
    )
    assert "reconcile" in dictPayload["sMessage"]
    assert connectionDocker.listSignals == [], (
        "the refusal signalled the recorded group, doing the drain's "
        "work without the drain's proof"
    )
    recordOwner = stateApp.dictContainerOwners[S_PROJECT_NAME]
    assert recordOwner.sLeaseId == sOldLease
    assert recordOwner.iOwnerGeneration == 1
    assert terminalContainment.fbContainerHasLiveTerminalRecords(
        stateApp, S_PROJECT_NAME,
    ), "the record must be retained, not settled by a refused transfer"
    assert recordTerminal.sOperationId in _dictJournalOperations()


@pytest.mark.asyncio
@pytest.mark.falsification
async def testARefusedTransferRollsBackOnlyWhatItMinted():
    """Case 46 (transfer half): refuse, retain, and roll back the pre-mint.

    The refusal side of the same change. Everything the transfer minted
    speculatively is discarded; everything it did NOT create is left
    exactly as it was -- the sitting owner keeps its lease, generation
    and credential, the terminal record stays, and the capability stays
    ARMED so the researcher can retry once reconciliation clears it.

    Kills: dropping the fnDiscardSessionRecord rollback on the
    live-terminal-record refusal, which leaks a browser session record
    per refused attempt.
    """
    stateApp = _fstateBuildAppState()
    _, sOldCredential, sOldLease = _tSeedOwnedContainer(stateApp)
    connectionDocker = _FakeDockerForTerminals(bProvable=False)
    recordTerminal = _recordSeedTerminalRecord(stateApp, connectionDocker)
    sCapability = _fsMintTransferCapability(stateApp)
    sOutcome, dictPayload = await _tTransfer(stateApp, sCapability)
    assert sOutcome == sessionLifecycle.S_TRANSFER_REFUSED
    assert "reconcile" in dictPayload["sMessage"]
    recordOwner = stateApp.dictContainerOwners[S_PROJECT_NAME]
    assert recordOwner.sLeaseId == sOldLease
    assert recordOwner.iOwnerGeneration == 1
    assert browserSession.fbValidateCredential(
        stateApp.dictBrowserSessions, sOldCredential,
    ) is True, "a refused transfer must not revoke the sitting owner"
    assert recordTerminal.sOperationId in _dictJournalOperations(), (
        "the unproven terminal record must be retained"
    )
    assert len(
        stateApp.dictBrowserSessions["dictSessionsByCredential"],
    ) == 1, "the pre-minted session must be rolled back on refusal"
    recordCap = stateApp.dictBrowserSessions["dictCapabilities"][
        sCapability
    ]
    assert recordCap.sState == "ARMED", (
        "the capability survives for a retry after reconciliation"
    )


@pytest.mark.asyncio
async def testUnsettledForeignJournalRecordRefusesTransfer():
    """§8: an unsettled record that is not adoptable refuses transfer."""
    stateApp = _fstateBuildAppState()
    _tSeedOwnedContainer(stateApp)
    sOperationId = operationJournal.fsPrepareOperation(
        S_PROJECT_NAME, "helper", "orphaned-writer",
    )
    operationJournal.fnPromoteOperationToInFlight(
        S_PROJECT_NAME, sOperationId, {"iHolderPid": 999999},
    )
    sCapability = _fsMintTransferCapability(stateApp)
    sOutcome, dictPayload = await _tTransfer(stateApp, sCapability)
    assert sOutcome == sessionLifecycle.S_TRANSFER_REFUSED
    assert sOperationId in dictPayload["sMessage"]
    assert stateApp.dictContainerOwners[
        S_PROJECT_NAME
    ].iOwnerGeneration == 1


# -- the mode-(b) drain across a transfer (cases 16b and 4) ------------------


async def _fsupervisorAwaitRegistered(stateApp):
    """Wait until the mode-(b) supervisor exists and journaled its op."""
    for _ in range(500):
        for supervisor in getattr(
            stateApp, "dictMutationSupervisors", {},
        ).values():
            if supervisor.sOperationId:
                return supervisor
        await asyncio.sleep(0.01)
    raise AssertionError("the supervisor never registered its operation")


@pytest.mark.asyncio
@pytest.mark.falsification
async def testCancelledRequesterKeepsTransferBlockedUntilWorkerDies():
    """Case 16b (slice-5 half): the shielded-supervisor drain vs transfer.

    Cancelling a mode-(b) mutation's REQUESTING coroutine stops the
    awaiting, never the worker: the supervisor holds the drain until
    the worker thread actually terminates, so a transfer meanwhile is
    busy-refused with its capability left ARMED. Once the worker
    exits — its effect committed under the OLD generation, the
    completes-before contract — the SAME capability transfers, and no
    old-generation effect lands after the commit.

    Kills: un-shielding the requester's await in
    ``commitCarrier.fdictRunLockHeldMutation`` (``await asyncio.shield(
    taskSupervisor)`` -> ``await taskSupervisor``) — a cancelled
    request then tears down the supervisor under the live worker, the
    drain frees while the worker thread still runs, and the first
    transfer no longer answers busy-retry.
    """
    stateApp = _fstateBuildAppState()
    sOldSessionId, _, sOldLease = _tSeedOwnedContainer(stateApp)
    dictLaneTuple = _dictBuildBrowserLaneTuple(
        stateApp, sOldSessionId, sOldLease,
    )
    eventWorkerMayExit = threading.Event()
    dictWorkerLog = {}

    def fnWorker(supervisor):
        eventWorkerMayExit.wait(10)
        dictWorkerLog["iGenerationAtCommit"] = stateApp.dictContainerOwners[
            S_PROJECT_NAME
        ].iOwnerGeneration
        return "worker-finished"

    taskRequest = asyncio.get_running_loop().create_task(
        commitCarrier.fdictRunLockHeldMutation(
            stateApp, S_PROJECT_NAME, S_CONTAINER_ID, dictLaneTuple,
            "file-write", "a guarded write", fnWorker,
        ),
    )
    supervisor = await _fsupervisorAwaitRegistered(stateApp)
    taskRequest.cancel()
    with pytest.raises(asyncio.CancelledError):
        await taskRequest
    sCapability = _fsMintTransferCapability(stateApp)
    sOutcomeWhileWorkerLives, _ = await _tTransfer(stateApp, sCapability)
    assert sOutcomeWhileWorkerLives == (
        sessionLifecycle.S_TRANSFER_BUSY_RETRY
    ), (
        "the transfer must stay blocked while the cancelled request's "
        "worker thread is still alive"
    )
    assert stateApp.dictContainerOwners[
        S_PROJECT_NAME
    ].iOwnerGeneration == 1
    eventWorkerMayExit.set()
    await asyncio.wait_for(supervisor.taskSupervisor, 5)
    assert dictWorkerLog["iGenerationAtCommit"] == 1, (
        "the worker's effect must have committed under the OLD "
        "generation, before any transfer"
    )
    assert supervisor.dictOutcome["bCommitted"] is True
    sOutcome, dictPayload = await _tTransfer(stateApp, sCapability)
    assert sOutcome == sessionLifecycle.S_TRANSFER_TRANSFERRED
    assert dictPayload["iOwnerGeneration"] == 2
    assert _dictJournalOperations() == {}, (
        "the completed worker's journal record must be settled, not "
        "adopted or quarantined"
    )


@pytest.mark.asyncio
@pytest.mark.falsification
async def testOldTupleLockHeldMutationIsRefusedAfterTransfer():
    """Case 4 (3b half): the mode-(b) revalidation refuses a stale tuple.

    A lock-held mutation submitted with the pre-transfer lane tuple is
    refused by the supervisor's OWN revalidation under the drain — the
    3b twin of the mode-(a) commit-point check — so its worker never
    runs and no journal record is ever written.

    Kills: neutralizing the lane-tuple revalidation in
    ``commitCarrier._fdictSuperviseLockHeldWorker``.
    """
    stateApp = _fstateBuildAppState()
    sOldSessionId, _, sOldLease = _tSeedOwnedContainer(stateApp)
    dictOldLaneTuple = _dictBuildBrowserLaneTuple(
        stateApp, sOldSessionId, sOldLease,
    )
    sCapability = _fsMintTransferCapability(stateApp)
    sOutcome, _ = await _tTransfer(stateApp, sCapability)
    assert sOutcome == sessionLifecycle.S_TRANSFER_TRANSFERRED
    listEffectRuns = []

    def fnWorker(supervisor):
        listEffectRuns.append(True)
        return "never-committed"

    with pytest.raises(commitCarrier.CommitRefusedError):
        await commitCarrier.fdictRunLockHeldMutation(
            stateApp, S_PROJECT_NAME, S_CONTAINER_ID, dictOldLaneTuple,
            "file-write", "a stale write", fnWorker,
        )
    assert listEffectRuns == [], (
        "a stale-tuple mode-(b) worker must never run"
    )
    assert _dictJournalOperations() == {}, (
        "the refusal must precede the journal write"
    )


# ---------------------------------------------------------------------
# Slice 9 — the start axis crossing a transfer (cases 23 start half, 33).
# ---------------------------------------------------------------------

def _fnAttachRunningStartReservation(stateApp, sOperationId):
    """Attach a live start reservation with a real journal record."""
    from vaibify.gui import startReservation
    recordOwner = stateApp.dictContainerOwners[S_PROJECT_NAME]
    recordOwner.reservation = startReservation.StartReservation(
        sReservationId="a" * 32,
        recordStartTask=startReservation.StartTaskRecord(
            sStartTaskId="startTask", sJournalOperationId=sOperationId,
        ),
    )
    return recordOwner.reservation


@pytest.mark.asyncio
@pytest.mark.falsification
async def testBarrierTransferAdoptsAStillRunningStart():
    """Case 23 (slice-9 start half): a transfer adopts a running START.

    The slice-5 barrier test proved a generic durable task is adopted.
    A start additionally holds an UNSETTLED write-ahead journal record,
    which the §8 identity gate refuses a transfer over unless it is the
    registered task's own — the adoption exception. So this drives the
    real thing: a start held behind a barrier, its journal record
    IN_FLIGHT, and a transfer that must complete anyway, retag the task,
    and leave the start running under the successor generation.

    Kills: in startReservation._fnPublishActiveOperationId, stop
    publishing the start's journal id onto the durable task's admission
    — the transfer then reads an unsettled record it cannot attribute
    and refuses, so a container can never be re-attached while starting.
    """
    from vaibify.gui import startReservation
    stateApp = _fstateBuildAppState()
    sOldSessionId, _, sOldLease = _tSeedOwnedContainer(stateApp)
    dictLaneTuple = _dictBuildBrowserLaneTuple(
        stateApp, sOldSessionId, sOldLease,
    )
    sOperationId = operationJournal.fsPrepareOperation(
        S_PROJECT_NAME, "start", S_PROJECT_NAME,
    )
    operationJournal.fnPromoteOperationToInFlight(
        S_PROJECT_NAME, sOperationId,
        {"iHolderPid": 1, "sReservationLabel": "a" * 32},
    )
    reservation = _fnAttachRunningStartReservation(stateApp, sOperationId)
    eventBarrier = asyncio.Event()
    dictLaunch = await commitCarrier.fdictLaunchDurableTask(
        stateApp, S_PROJECT_NAME, S_CONTAINER_ID, dictLaneTuple,
        lambda: asyncio.ensure_future(eventBarrier.wait()),
    )
    assert dictLaunch["bLaunched"] is True
    startReservation._fnPublishActiveOperationId(
        stateApp, S_PROJECT_NAME, reservation,
    )
    sCapability = _fsMintTransferCapability(stateApp)
    sOutcome, dictPayload = await _tTransfer(stateApp, sCapability)
    assert sOutcome == sessionLifecycle.S_TRANSFER_TRANSFERRED, (
        f"a still-STARTING container must be adoptable: {dictPayload}"
    )
    recordOwner = stateApp.dictContainerOwners[S_PROJECT_NAME]
    assert recordOwner.reservation is reservation, (
        "the transfer must PRESERVE the reservation, never cancel it"
    )
    assert recordOwner.iOwnerGeneration == 2
    recordTask = stateApp.dictDurableTaskRecords[S_PROJECT_NAME]
    assert recordTask.iOwnerGeneration == 2, (
        "the running start must be retagged to the successor"
    )
    assert not recordTask.taskAsync.done(), (
        "the transfer must not have waited the start out"
    )
    eventBarrier.set()
    await recordTask.taskAsync
    operationJournal.fnSettleOperation(S_PROJECT_NAME, sOperationId)


@pytest.mark.asyncio
@pytest.mark.falsification
async def testTransferRebindsTheStartResultEntitlementToTheSuccessor():
    """Case 33: the successor collects a start result, the revoked cannot.

    A start succeeded (or failed) and its outcome was never collected;
    then ``vaibify open`` transfers the container. The successor must be
    able to retrieve it — with a lease derived FRESHLY from the owner
    record, never a stored one — and the revoked old session must not,
    on either delivery path.

    Kills: in sessionLifecycle._fnRebindStartResultEntitlement, drop the
    rebinding call — the successor is then refused its own container's
    failed-start outcome, and the revoked session keeps the entitlement.
    """
    from vaibify.gui import startReservation, startResultStore
    stateApp = _fstateBuildAppState()
    stateApp.dictStartResults = startResultStore.fdictCreateStartResultStore()
    sOldSessionId, _, _ = _tSeedOwnedContainer(stateApp)
    startResultStore.fnOpenStartResult(
        stateApp, "b" * 32, S_PROJECT_NAME, sOldSessionId,
    )
    startResultStore.fnCloseStartResult(
        stateApp, "b" * 32, startResultStore.S_RESULT_SUCCEEDED,
        sContainerId=S_CONTAINER_ID,
    )
    sCapability = _fsMintTransferCapability(stateApp)
    sOutcome, dictPayload = await _tTransfer(stateApp, sCapability)
    assert sOutcome == sessionLifecycle.S_TRANSFER_TRANSFERRED
    sNewSessionId = dictPayload["sSessionId"]

    iCodeSuccessor, dictSuccessor = startReservation.ftPollStartStatus(
        stateApp, S_PROJECT_NAME, sNewSessionId,
    )
    assert iCodeSuccessor == 200, dictSuccessor
    assert dictSuccessor["sState"] == "SUCCEEDED"
    assert dictSuccessor["sLeaseId"] == dictPayload["sLeaseId"], (
        "the delivered lease must be derived from the CURRENT owner "
        "record, never replayed from the result"
    )
    iCodeRevoked, _ = startReservation.ftPollStartStatus(
        stateApp, S_PROJECT_NAME, sOldSessionId,
    )
    assert iCodeRevoked == 403, (
        "the revoked predecessor must not retrieve the outcome"
    )
    startResultStore.fnCloseStartResult(
        stateApp, "b" * 32, startResultStore.S_RESULT_FAILED,
        sSafeError="image missing",
    )
    iCodeFailed, dictFailed = startReservation.ftPollStartStatus(
        stateApp, S_PROJECT_NAME, sNewSessionId,
    )
    assert iCodeFailed == 200 and dictFailed["sState"] == "FAILED"
    assert "sLeaseId" not in dictFailed, (
        "a failure entitlement must convey no container authority"
    )


# ---------------------------------------------------------------------
# Wave 2.4: a busy container refuses IMMEDIATELY, and says what is busy.
# ---------------------------------------------------------------------

@pytest.mark.asyncio
@pytest.mark.falsification
async def testABusyContainerRefusesTheTransferAtOnceAndNamesTheOperation():
    """Six steps, so implicit waiting cannot satisfy the test.

    Start and block a mutation; attempt the transfer; assert an
    IMMEDIATE busy refusal, unchanged owner and generation, no
    capability consumption, and a message naming the live operation;
    let the mutation finish; explicitly retry; assert the retry
    succeeds.

    The immediacy is measured, not assumed. The transfer used to wait
    up to twenty seconds on the mutation lock and then report "a
    guarded operation holds its mutation drain" -- true, unactionable,
    and identical whether the holder was a two-second write or a
    half-hour rebuild. Waiting also spends the capability's window on an
    operation of unknown length, so the researcher watches a command
    that has not answered.

    Naming the operation is only possible because the lock HOLDER
    registers what it is doing: an asyncio.Lock knows that it is held
    and nothing else.

    Kills: in sessionLifecycle.ftTransferOwnership, restoring the
    bounded wait (`await asyncio.wait_for(lockMutation.acquire(),
    F_TRANSFER_DRAIN_WAIT_SECONDS)`) in place of the immediate refusal.
    """
    import threading
    import time

    stateApp = _fstateBuildAppState()
    sOldSessionId, _sOldCredential, sOldLease = _tSeedOwnedContainer(
        stateApp,
    )
    dictLaneTuple = _dictBuildBrowserLaneTuple(
        stateApp, sOldSessionId, sOldLease,
    )
    # A SYNCHRONOUS worker blocked on a threading.Event, because the
    # carrier runs workers with asyncio.to_thread. An `async def` worker
    # would be called in that thread, hand back a coroutine object
    # nobody awaits, and return at once -- so the mutation this test
    # exists to block would never block, and the test would pass on
    # scheduling luck while Python warned that the coroutine was never
    # awaited. That is exactly what the first version of this test did.
    eventStarted = threading.Event()
    eventRelease = threading.Event()
    listCommitted = []

    def _fnHeldWorker(supervisor):
        del supervisor
        eventStarted.set()
        eventRelease.wait(10)
        listCommitted.append("committed")
        return "done"

    taskMutation = asyncio.ensure_future(
        commitCarrier.fdictRunLockHeldMutation(
            stateApp, S_PROJECT_NAME, S_CONTAINER_ID, dictLaneTuple,
            "file-write", "/workspace/project.json", _fnHeldWorker,
        ),
    )
    await asyncio.to_thread(eventStarted.wait, 10)
    assert sessionLifecycle._flockObtainContainerMutation(
        sessionLifecycle._fdictLockStoreForAppState(stateApp),
        S_PROJECT_NAME,
    ).locked(), "the mutation never took the drain"
    assert listCommitted == [], (
        "the worker finished before the transfer was attempted, so the "
        "container was not busy when it mattered"
    )

    sCapability = _fsMintTransferCapability(stateApp)
    fBefore = time.monotonic()
    sOutcome, dictPayload = await _tTransfer(stateApp, sCapability)
    fElapsed = time.monotonic() - fBefore

    assert sOutcome == sessionLifecycle.S_TRANSFER_BUSY_RETRY, dictPayload
    assert fElapsed < 1.0, (
        f"the transfer waited {fElapsed:.1f}s on the busy container "
        f"instead of refusing at once"
    )
    assert "file-write" in dictPayload["sMessage"], (
        f"the refusal must NAME the live operation: {dictPayload}"
    )
    assert "project.json" in dictPayload["sMessage"]
    recordOwner = stateApp.dictContainerOwners[S_PROJECT_NAME]
    assert recordOwner.sLeaseId == sOldLease
    assert recordOwner.iOwnerGeneration == 1
    assert stateApp.dictBrowserSessions["dictCapabilities"][
        sCapability
    ].sState == "ARMED", (
        "a busy refusal consumed the capability, so the retry it "
        "invites cannot use it"
    )

    assert listCommitted == [], (
        "the worker committed while the transfer was being refused; the "
        "refusal was not measured against a live mutation"
    )
    eventRelease.set()
    await taskMutation
    assert listCommitted == ["committed"]

    sOutcomeRetry, dictRetry = await _tTransfer(stateApp, sCapability)
    assert sOutcomeRetry == sessionLifecycle.S_TRANSFER_TRANSFERRED, (
        f"the explicit retry after the operation finished must "
        f"succeed: {dictRetry}"
    )
    assert stateApp.dictContainerOwners[
        S_PROJECT_NAME
    ].iOwnerGeneration == 2
