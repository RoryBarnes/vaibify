"""The single state-transition authority for container-session lifecycle.

Every ownership state transition — today explicit release; orphaning,
host transfer, and the safe reaper in later slices — is committed
through this module, never by a route calling the ``containerOwnership``
primitives directly. Routes ask; this module decides and commits.

Locks — the named hierarchy (design §3.5)
-----------------------------------------
Three lock objects, one canonical acquisition order everywhere; never
acquire in any other order:

1. ``lock container-mutation [sName]`` — per container, an
   ``asyncio.Lock``. This lock IS the in-flight-mutation drain: a
   commit-guard mutation will hold it across its whole operation, so a
   transfer (or release) that acquires it waits the mutation out.
   Creating the per-name lock is itself synchronized (the store's
   creation guard), and a key is never deleted while any callback may
   still hold a reference to its lock.
2. ``lock session-cardinality`` — one per hub, guarding the
   ``dictSessionOwner`` reverse index. Cross-container cardinality is
   two different per-container locks both seeing an empty index, so
   only this global lock can enforce one-container-per-session. Held
   only briefly around the read-check-write.
3. ``lock browser-sessions`` — the existing module-level
   ``browserSession._lockBrowserSessions`` guarding the credential
   store. This module never replaces it; it is taken (inside the
   ``browserSession`` functions) only after the two locks above.

Canonical order: **container-mutation → cardinality → browser-store.**
The only long wait (the drain) lands on the FIRST lock, so no path ever
holds the hub-wide cardinality lock while waiting on unrelated container
work.

Two invariants (design §3.5), recorded while they are true:

1. **No operation acquires two different container-mutation locks.** A
   future "move my session to container B" must be release-A then
   claim-B, never a combined switch holding both.
2. **The locks live here, wrapping the SYNCHRONOUS ownership
   primitives.** ``ftdictClaim``, ``fnReleaseOwnership``, and
   ``flistReapIdleOwnerships`` stay synchronous; the ``asyncio.Lock``s
   are taken in this lifecycle layer around those calls, never pushed
   down into them. Corollary: build is browser-hub scoped and takes no
   container lock, so the worker-thread build path never touches these
   ``asyncio`` locks.

The lock store is held per application (``app.state``), created by
:func:`fdictCreateLifecycleLockStore`: an ``asyncio.Lock`` binds to the
first event loop that awaits it, so a process that builds several
applications (the test harness does) needs per-application locks, while
a real hub — one application per process — still has exactly one store.
"""

__all__ = [
    "F_RECONNECT_WINDOW_SECONDS",
    "F_SLIDING_IDLE_SECONDS",
    "F_ABSOLUTE_SESSION_CAP_SECONDS",
    "F_LIFECYCLE_EVALUATOR_CADENCE_SECONDS",
    "F_TRANSFER_DRAIN_WAIT_SECONDS",
    "F_TRANSFER_COMMIT_HEADROOM_SECONDS",
    "S_TRANSFER_TRANSFERRED",
    "S_TRANSFER_BUSY_RETRY",
    "S_TRANSFER_EXPIRED",
    "S_TRANSFER_UNOWNED",
    "S_TRANSFER_STALE_GENERATION",
    "S_TRANSFER_REFUSED",
    "fdictCreateLifecycleLockStore",
    "flockContainerMutationForAppState",
    "ftdictClaimWithCardinality",
    "fbReleaseExplicit",
    "ftTransferOwnership",
    "fnOrphanSession",
    "fnOrphanOwnersPastReconnectWindow",
]

import asyncio
import threading
import time

from vaibify.config import operationJournal
from . import browserSession
from . import containerOwnership

# Lifecycle timing knobs (design §2.5), env-overridable. Only the reap
# grace (containerOwnership._F_GRACE_SECONDS) is consumed today; the
# rest are declared here so the orphan trigger, session sweep, and
# evaluator slices consume named constants instead of inventing them.
F_RECONNECT_WINDOW_SECONDS = containerOwnership.ffReadSecondsFromEnvironment(
    "VAIBIFY_RECONNECT_WINDOW_SECONDS", 15.0,
)
F_SLIDING_IDLE_SECONDS = containerOwnership.ffReadSecondsFromEnvironment(
    "VAIBIFY_SLIDING_IDLE_SECONDS", 3600.0,
)
F_ABSOLUTE_SESSION_CAP_SECONDS = (
    containerOwnership.ffReadSecondsFromEnvironment(
        "VAIBIFY_ABSOLUTE_SESSION_CAP_SECONDS", 43200.0,
    )
)
F_LIFECYCLE_EVALUATOR_CADENCE_SECONDS = (
    containerOwnership.ffReadSecondsFromEnvironment(
        "VAIBIFY_LIFECYCLE_EVALUATOR_CADENCE_SECONDS", 5.0,
    )
)

# Transfer timing (design §6.1): the drain wait is bounded WELL BELOW
# the capability TTL (300 s) — never "wait until the capability
# expires" — and the commit headroom is the TTL margin a transfer
# needs beyond the drain wait before it is worth attempting at all.
F_TRANSFER_DRAIN_WAIT_SECONDS = (
    containerOwnership.ffReadSecondsFromEnvironment(
        "VAIBIFY_TRANSFER_DRAIN_WAIT_SECONDS", 20.0,
    )
)
F_TRANSFER_COMMIT_HEADROOM_SECONDS = (
    containerOwnership.ffReadSecondsFromEnvironment(
        "VAIBIFY_TRANSFER_COMMIT_HEADROOM_SECONDS", 30.0,
    )
)

# Transfer outcomes (design §6.1/§6.2). TRANSFERRED carries the result
# tuple; BUSY_RETRY leaves the capability ARMED for a client retry;
# EXPIRED tells the client to mint afresh; UNOWNED is the
# reaped-between-mint-and-redeem case ("claim normally");
# STALE_GENERATION is the ABA refusal; REFUSED covers the retained
# refusals (poison, cancel-requested task, unsettled journal, a
# quarantining drain) whose message names the recovery path.
S_TRANSFER_TRANSFERRED = "transferred"
S_TRANSFER_BUSY_RETRY = "busyRetry"
S_TRANSFER_EXPIRED = "expired"
S_TRANSFER_UNOWNED = "unowned"
S_TRANSFER_STALE_GENERATION = "staleGeneration"
S_TRANSFER_REFUSED = "refused"


def fdictCreateLifecycleLockStore():
    """Return a fresh lifecycle lock store for one application.

    ``dictContainerMutationLocks`` maps container name to its
    ``asyncio.Lock``; keys are never deleted (a callback may still hold
    a reference). ``lockCreationGuard`` synchronizes per-name lock
    creation. The cardinality lock is created lazily on first use so it
    is constructed under a running event loop on every supported Python
    version.
    """
    return {
        "lockCreationGuard": threading.Lock(),
        "dictContainerMutationLocks": {},
        "lockSessionCardinality": None,
    }


def _fdictLockStoreForAppState(appState):
    """Return the app's lifecycle lock store, creating it when absent.

    Minimal test applications construct their state by hand, so the
    authority provisions its own store rather than refusing them; a
    production app receives its store from the application factory.
    """
    dictLockStore = getattr(appState, "dictLifecycleLocks", None)
    if dictLockStore is None:
        dictLockStore = fdictCreateLifecycleLockStore()
        appState.dictLifecycleLocks = dictLockStore
    return dictLockStore


def _flockObtainContainerMutation(dictLockStore, sName):
    """Return the per-container mutation lock, creating it synchronized."""
    dictLocks = dictLockStore["dictContainerMutationLocks"]
    lockExisting = dictLocks.get(sName)
    if lockExisting is not None:
        return lockExisting
    with dictLockStore["lockCreationGuard"]:
        return dictLocks.setdefault(sName, asyncio.Lock())


def _flockObtainSessionCardinality(dictLockStore):
    """Return the hub-wide cardinality lock, creating it synchronized."""
    lockExisting = dictLockStore["lockSessionCardinality"]
    if lockExisting is not None:
        return lockExisting
    with dictLockStore["lockCreationGuard"]:
        if dictLockStore["lockSessionCardinality"] is None:
            dictLockStore["lockSessionCardinality"] = asyncio.Lock()
        return dictLockStore["lockSessionCardinality"]


def flockContainerMutationForAppState(appState, sName):
    """Return the per-container mutation lock (the drain) for an app.

    The commit-guard carrier's public handle on lock 1 of the hierarchy
    (design §3.5): a mode-(b) supervisor holds it for its worker's whole
    life, a mode-(c) durable task launches and finalizes under it, and a
    transfer (slice 5) will wait it out. Locks are created synchronized
    and never deleted, so every caller shares the same object per name.
    """
    return _flockObtainContainerMutation(
        _fdictLockStoreForAppState(appState), sName,
    )


async def ftdictClaimWithCardinality(
    appState, sName, sLeaseId, iPort, sContainerId="",
    fbPipelineRunning=None, sBrowserSessionId="", connectionDocker=None,
):
    """Commit a claim under the canonical lock order (design §9).

    The sole claim path for routes: acquires the container-mutation lock
    (the drain), then the hub-wide cardinality lock, and only then runs
    the synchronous :func:`containerOwnership.ftdictClaim`, whose
    cardinality read-check-write on ``dictSessionOwner`` therefore
    executes atomically against every other creation path. Two
    concurrent claims by one session on two DIFFERENT containers take
    two different container-mutation locks but serialize on the single
    cardinality lock, so they resolve to exactly one owner record.
    Returns the primitive's ``(iStatusCode, dictPayload)`` verdict
    unchanged.
    """
    dictLockStore = _fdictLockStoreForAppState(appState)
    dictContainerOwners = getattr(appState, "dictContainerOwners", {})
    dictSessionOwner = getattr(appState, "dictSessionOwner", None)
    async with _flockObtainContainerMutation(dictLockStore, sName):
        async with _flockObtainSessionCardinality(dictLockStore):
            return containerOwnership.ftdictClaim(
                dictContainerOwners, sName, sLeaseId, iPort,
                sContainerId=sContainerId,
                fbPipelineRunning=fbPipelineRunning,
                sBrowserSessionId=sBrowserSessionId,
                dictSessionOwner=dictSessionOwner,
                connectionDocker=connectionDocker,
            )


async def fbReleaseExplicit(appState, sName, sLeaseId, sBrowserSessionId=""):
    """Commit an explicit release under the canonical lock order.

    The sole release path for routes (design §3): acquires the
    container-mutation lock (the drain), then the cardinality lock, then
    delegates the arbitration to the synchronous
    :func:`containerOwnership.fnReleaseOwnership`, which alone decides
    whether the presenting session holds the session-bound lease. The
    reverse index is passed through so a committed release also drops
    the session's cardinality entry.

    A PERMITTED release first terminates every live terminal execution
    of the container and proves each recorded process group empty — or
    retains-and-quarantines the record (design §10, case 44) — so a
    signal-trapping shell cannot write after release just as it cannot
    after a transfer. The drain runs under the container-mutation lock
    but BEFORE the briefly-held cardinality lock, and only for a caller
    the arbitration will actually permit: an unauthorized release
    attempt never touches the true owner's terminals.
    """
    from . import terminalContainment
    dictLockStore = _fdictLockStoreForAppState(appState)
    dictContainerOwners = getattr(appState, "dictContainerOwners", {})
    dictSessionOwner = getattr(appState, "dictSessionOwner", None)
    async with _flockObtainContainerMutation(dictLockStore, sName):
        if containerOwnership.fbReleaseWouldBePermitted(
            dictContainerOwners, sName, sLeaseId,
            sBrowserSessionId=sBrowserSessionId,
        ):
            await asyncio.to_thread(
                terminalContainment.fdictDrainTerminalRecordsForContainer,
                appState, sName,
            )
        async with _flockObtainSessionCardinality(dictLockStore):
            return containerOwnership.fnReleaseOwnership(
                dictContainerOwners, sName, sLeaseId,
                sBrowserSessionId=sBrowserSessionId,
                dictSessionOwner=dictSessionOwner,
            )


# ---------------------------------------------------------------------
# Host-authorized transfer (design §6.1/§6.2, slice 5).
# ---------------------------------------------------------------------

async def ftTransferOwnership(appState, sCapability):
    """Commit a host-authorized ownership transfer; return its outcome.

    The sole writer that touches the browser-session store, the owner
    registry, the ``dictSessionOwner`` reverse index, and the durable
    task record together. Returns ``(sOutcome, dictPayload)``:
    ``S_TRANSFER_TRANSFERRED`` carries the new session's credential,
    lease, and generation; every other outcome carries ``sMessage``.

    Shape (design §6.1): locks in canonical order — the bounded drain
    wait lands on the container-mutation lock FIRST, holding no global
    lock; pre-commit refusals (unowned, stale generation, poison,
    cancel-requested task, unsettled journal) then run under the held
    drain; everything reversible is pre-minted; terminals are fenced
    and terminated-and-proven (DRAINING — a quarantining drain refuses,
    it never pretends to roll back); and the linearization — final
    checks, lease rotation, session rebind, generation bump, task
    retag, old-credential revocation, stored-result write — commits
    SYNCHRONOUSLY with no ``await`` between the final check and the
    commit. Old sockets are actively closed only after the commit.
    """
    dictStore = getattr(appState, "dictBrowserSessions", None)
    dictInspect = browserSession.fdictInspectTransferCapability(
        dictStore or {}, sCapability,
    )
    if dictInspect is None:
        return (S_TRANSFER_EXPIRED, {
            "sMessage": "Unknown transfer capability; mint a fresh one "
                        "with 'vaibify open'.",
        })
    tPrecheck = _tOutcomeForCapabilityState(dictStore, sCapability,
                                            dictInspect)
    if tPrecheck is not None:
        return tPrecheck
    sName = dictInspect["sContainerName"]
    dictLockStore = _fdictLockStoreForAppState(appState)
    lockMutation = _flockObtainContainerMutation(dictLockStore, sName)
    try:
        await asyncio.wait_for(
            lockMutation.acquire(), F_TRANSFER_DRAIN_WAIT_SECONDS,
        )
    except asyncio.TimeoutError:
        return _tOutcomeForDrainTimeout(dictStore, sCapability, sName)
    try:
        return await _tTransferUnderDrain(
            appState, dictStore, dictLockStore, sCapability, sName,
            dictInspect["iExpectedOwnerGeneration"],
        )
    finally:
        lockMutation.release()


def _tOutcomeForCapabilityState(dictStore, sCapability, dictInspect):
    """Return the pre-lock outcome, or None when the transfer may run.

    Handles bounded replay (a REDEEMED capability inside its window
    returns the stored tuple — case 3), the EXPIRED state, and the
    insufficient-TTL refusal: a drain wait that could outlast the
    capability is never started (design §6.1).
    """
    if dictInspect["sState"] == "REDEEMED":
        dictStored = dictInspect["dictStoredResult"] or {}
        return (S_TRANSFER_TRANSFERRED, dict(
            dictStored,
            sContainerName=dictInspect["sContainerName"],
            bReplayed=True,
        ))
    if dictInspect["sState"] != "ARMED":
        return (S_TRANSFER_EXPIRED, {
            "sMessage": "The transfer capability expired; mint a fresh "
                        "one with 'vaibify open'.",
        })
    fRequiredTtl = (
        F_TRANSFER_DRAIN_WAIT_SECONDS + F_TRANSFER_COMMIT_HEADROOM_SECONDS
    )
    if dictInspect["fRemainingTtlSeconds"] < fRequiredTtl:
        browserSession.fnExpireCapability(dictStore, sCapability)
        return (S_TRANSFER_EXPIRED, {
            "sMessage": "Too little of the transfer capability's window "
                        "remains to attempt a transfer; mint a fresh one "
                        "with 'vaibify open'.",
        })
    return None


def _tOutcomeForDrainTimeout(dictStore, sCapability, sName):
    """Map a drain-wait timeout to busy-retry or expired (design §6.1).

    Nothing was minted, revoked, or bumped. With TTL remaining the
    capability stays ARMED so the client retries with the same one;
    with too little left it is EXPIRED so the client mints afresh.
    """
    dictInspect = browserSession.fdictInspectTransferCapability(
        dictStore or {}, sCapability,
    )
    fRequiredTtl = (
        F_TRANSFER_DRAIN_WAIT_SECONDS + F_TRANSFER_COMMIT_HEADROOM_SECONDS
    )
    if dictInspect and dictInspect["fRemainingTtlSeconds"] >= fRequiredTtl:
        return (S_TRANSFER_BUSY_RETRY, {
            "sMessage": f"Container '{sName}' is busy: a guarded "
                        "operation holds its mutation drain. Retry "
                        "shortly; the capability remains valid.",
        })
    browserSession.fnExpireCapability(dictStore, sCapability)
    return (S_TRANSFER_EXPIRED, {
        "sMessage": f"Container '{sName}' stayed busy past the transfer "
                    "capability's window; mint a fresh one with "
                    "'vaibify open'.",
    })


async def _tTransferUnderDrain(
    appState, dictStore, dictLockStore, sCapability, sName, iExpectedGen,
):
    """Run the pre-checks, DRAINING, and commit under the held drain."""
    from . import terminalContainment
    tRefusal = _tRefusalBeforePremint(
        appState, dictStore, sCapability, sName, iExpectedGen,
    )
    if tRefusal is not None:
        return tRefusal
    # Pre-mint everything reversible BEFORE the terminal fence (§6.1).
    sNewSessionId, sNewCredential = (
        browserSession.ftMintDetachedSessionRecord(dictStore)
    )
    # DRAINING: fence terminal input (reversible), then terminate and
    # prove every recorded group empty. A quarantining drain REFUSES
    # the transfer and retains the quarantine (case 46) — the kills
    # are not pretended-rolled-back; only the pre-mint is.
    dictDrain = await asyncio.to_thread(
        terminalContainment.fdictDrainTerminalRecordsForContainer,
        appState, sName,
    )
    if dictDrain["listQuarantinedOperationIds"]:
        browserSession.fnDiscardSessionRecord(dictStore, sNewCredential)
        return (S_TRANSFER_REFUSED, {
            "sMessage": f"Container '{sName}' has a terminal whose "
                        "process group could not be proven dead; it is "
                        "retained and quarantined. Restart the container "
                        "or run 'vaibify reconcile', then retry.",
        })
    async with _flockObtainSessionCardinality(dictLockStore):
        # Final check + commit: SYNCHRONOUS from here to the return —
        # no await may separate the generation check from the commit.
        tLateRefusal = _tRefusalAtCommitPoint(
            appState, dictStore, sNewCredential, sName, iExpectedGen,
            sCapability,
        )
        if tLateRefusal is not None:
            return tLateRefusal
        dictPayload, listDetached = _tCommitTransfer(
            appState, dictStore, sCapability, sName,
            sNewSessionId, sNewCredential,
        )
    await _fnCloseDetachedConnections(listDetached)
    return (S_TRANSFER_TRANSFERRED, dictPayload)


def _tRefusalBeforePremint(
    appState, dictStore, sCapability, sName, iExpectedGen,
):
    """Return the pre-mint refusal outcome, or None to proceed.

    Runs under the held drain, before anything is minted: the record
    must exist at the expected generation (§6.2 — reaped means "claim
    normally", a bumped generation means a stale capability, and both
    expire the capability because neither can ever succeed later),
    must not be poisoned (case 26b), must not carry a
    cancel-requested durable task (case 31: cancel won the lock), and
    every unsettled journal record must be adoptable (§8).
    """
    recordOwner = getattr(appState, "dictContainerOwners", {}).get(sName)
    if recordOwner is None:
        browserSession.fnExpireCapability(dictStore, sCapability)
        return (S_TRANSFER_UNOWNED, {
            "sMessage": f"Container '{sName}' is unowned (its previous "
                        "session was released); claim it normally from "
                        "the dashboard.",
        })
    if recordOwner.iOwnerGeneration != iExpectedGen:
        browserSession.fnExpireCapability(dictStore, sCapability)
        return (S_TRANSFER_STALE_GENERATION, {
            "sMessage": f"Container '{sName}' changed owners after this "
                        "transfer capability was minted; a stale "
                        "transfer may not displace the successor. Run "
                        "'vaibify open' again.",
        })
    if getattr(recordOwner, "poison", None) is not None:
        return (S_TRANSFER_REFUSED, {
            "sMessage": f"Container '{sName}' carries a force-abandoned "
                        "operation whose worker is not proven dead; run "
                        "'vaibify reconcile' first.",
        })
    recordTask = _frecordLiveDurableTask(appState, sName)
    if recordTask is not None and recordTask.sState != "running":
        return (S_TRANSFER_REFUSED, {
            "sMessage": f"Container '{sName}' has a durable task whose "
                        "cancellation is in progress; retry once it has "
                        "settled.",
        })
    sJournalReason = _fsUnadoptableJournalReason(appState, sName, recordTask)
    if sJournalReason:
        return (S_TRANSFER_REFUSED, {"sMessage": sJournalReason})
    return None


def _frecordLiveDurableTask(appState, sName):
    """Return the container's LIVE durable task record, or None."""
    recordTask = getattr(appState, "dictDurableTaskRecords", {}).get(sName)
    if recordTask is None:
        return None
    if recordTask.taskAsync is not None and recordTask.taskAsync.done():
        return None
    return recordTask


def _fsUnadoptableJournalReason(appState, sName, recordTask):
    """Return why the journal blocks a transfer, or '' when adoptable.

    The §8 identity-gate exception applied to transfer: an unsettled
    record is tolerated only when it IS the registered mode-(c) task's
    live exec record (the journal id matches the task record — the
    adoption case) or a live terminal record the DRAINING phase is
    about to settle or quarantine. Anything else — a quarantined
    record, an unreadable journal, an orphaned unsettled operation —
    refuses, fail closed.
    """
    dictOutcomeRead = operationJournal.fdictReadJournalOutcome(sName)
    if dictOutcomeRead["sReadState"] not in ("absent", "valid"):
        return (
            f"Container '{sName}'s operation journal is "
            f"{dictOutcomeRead['sReadState']} and reads as quarantined; "
            "run 'vaibify reconcile' before transferring."
        )
    sAdoptableExecId = ""
    if recordTask is not None and recordTask.admission is not None:
        sAdoptableExecId = recordTask.admission.dictLiveState.get(
            "sActiveExecOperationId", "",
        )
    dictTerminalRecords = getattr(
        appState, "dictTerminalExecutionRecords", {},
    ).get(sName) or {}
    for sOperationId, dictRecord in dictOutcomeRead["dictOperations"].items():
        if dictRecord["sState"] == (
            operationJournal.S_OPERATION_STATE_NEEDS_RECONCILIATION
        ):
            return (
                f"Container '{sName}' has a quarantined journal record "
                f"({sOperationId}); run 'vaibify reconcile' before "
                "transferring."
            )
        if sOperationId == sAdoptableExecId:
            continue
        if sOperationId in dictTerminalRecords:
            continue
        return (
            f"Container '{sName}' has an unsettled journal record "
            f"({sOperationId}, kind {dictRecord['sKind']}) that is "
            "neither the live durable task nor a drainable terminal; "
            "run 'vaibify reconcile' before transferring."
        )
    return ""


def _tRefusalAtCommitPoint(
    appState, dictStore, sNewCredential, sName, iExpectedGen, sCapability,
):
    """Return the final synchronous refusal, or None to commit.

    Everything here is synchronous, so nothing can interleave between
    this check and the commit that follows it on the event loop. The
    pre-mint is rolled back on every refusal; the drain's kills stand.
    """
    from . import terminalContainment
    recordOwner = getattr(appState, "dictContainerOwners", {}).get(sName)
    if recordOwner is None:
        browserSession.fnDiscardSessionRecord(dictStore, sNewCredential)
        browserSession.fnExpireCapability(dictStore, sCapability)
        return (S_TRANSFER_UNOWNED, {
            "sMessage": f"Container '{sName}' became unowned during the "
                        "transfer; claim it normally from the dashboard.",
        })
    if recordOwner.iOwnerGeneration != iExpectedGen:
        browserSession.fnDiscardSessionRecord(dictStore, sNewCredential)
        browserSession.fnExpireCapability(dictStore, sCapability)
        return (S_TRANSFER_STALE_GENERATION, {
            "sMessage": f"Container '{sName}' changed owners during the "
                        "transfer; run 'vaibify open' again.",
        })
    if getattr(recordOwner, "poison", None) is not None:
        browserSession.fnDiscardSessionRecord(dictStore, sNewCredential)
        return (S_TRANSFER_REFUSED, {
            "sMessage": f"Container '{sName}' was force-abandoned during "
                        "the transfer; run 'vaibify reconcile' first.",
        })
    recordTask = _frecordLiveDurableTask(appState, sName)
    if recordTask is not None and recordTask.sState != "running":
        browserSession.fnDiscardSessionRecord(dictStore, sNewCredential)
        return (S_TRANSFER_REFUSED, {
            "sMessage": f"Container '{sName}' has a durable task whose "
                        "cancellation is in progress; retry once it has "
                        "settled.",
        })
    if terminalContainment.fbContainerHasLiveTerminalRecords(
        appState, sName,
    ):
        # A terminal opened between the drain and this commit point;
        # the retry's DRAINING phase will fence and drain it too.
        browserSession.fnDiscardSessionRecord(dictStore, sNewCredential)
        return (S_TRANSFER_BUSY_RETRY, {
            "sMessage": f"Container '{sName}' opened a new terminal "
                        "during the transfer; retry shortly.",
        })
    return None


def _tCommitTransfer(
    appState, dictStore, sCapability, sName, sNewSessionId, sNewCredential,
):
    """The synchronous linearization point (design §6.1).

    Rotates the lease, rebinds the browser session, bumps the owner
    generation, retags the live mode-(c) task in place, detaches the
    old session's connection records and their counter contributions,
    revokes the old session's credential, and stores the result tuple
    on the capability — all in one synchronous block. The agent token,
    the host flock, and the keep-alive are deliberately untouched
    (§6.2). Returns ``(dictPayload, listDetachedConnections)``.
    """
    recordOwner = appState.dictContainerOwners[sName]
    sOldSessionId = recordOwner.sBrowserSessionId
    sNewLease = containerOwnership.fsMintLease()
    iNewGeneration = recordOwner.iOwnerGeneration + 1
    recordOwner.sLeaseId = sNewLease
    recordOwner.sBrowserSessionId = sNewSessionId
    recordOwner.iOwnerGeneration = iNewGeneration
    recordOwner.sState = containerOwnership.S_OWNER_STATE_ACTIVE
    recordOwner.fOrphanedSinceMonotonic = 0.0
    recordOwner.fLastSeenMonotonic = time.monotonic()
    recordOwner.bSocketEverExisted = False
    _fnRebindSessionOwnerIndex(appState, sName, sOldSessionId,
                               sNewSessionId)
    _fnRetagLiveDurableTask(appState, sName, iNewGeneration)
    listDetached = _flistDetachOldSessionConnections(
        appState, recordOwner, sOldSessionId,
    )
    browserSession.fnRevokeSessionById(dictStore, sOldSessionId)
    browserSession.fnStoreTransferResult(
        dictStore, sCapability, sNewSessionId, sNewCredential, sNewLease,
        iNewGeneration,
    )
    return ({
        "sContainerName": sName,
        "sSessionId": sNewSessionId,
        "sCredential": sNewCredential,
        "sLeaseId": sNewLease,
        "iOwnerGeneration": iNewGeneration,
        "bReplayed": False,
    }, listDetached)


def _fnRebindSessionOwnerIndex(appState, sName, sOldSessionId,
                               sNewSessionId):
    """Move the container's cardinality entry to the new session."""
    dictSessionOwner = getattr(appState, "dictSessionOwner", None)
    if dictSessionOwner is None:
        return
    if sOldSessionId and dictSessionOwner.get(sOldSessionId) == sName:
        dictSessionOwner.pop(sOldSessionId, None)
    if sNewSessionId:
        dictSessionOwner[sNewSessionId] = sName


def _fnRetagLiveDurableTask(appState, sName, iNewGeneration):
    """Retag the live mode-(c) task record in place (design §2.3/§8).

    The mutable ``iOwnerGeneration`` on the durable task record — and
    on the raw pipeline task object, whose done-callback logs the
    completion attribution — is what lets the preserved task keep
    committing under the successor generation (cases 5 and 23).
    """
    recordTask = _frecordLiveDurableTask(appState, sName)
    if recordTask is None:
        return
    recordTask.iOwnerGeneration = iNewGeneration
    if recordTask.taskAsync is not None and hasattr(
        recordTask.taskAsync, "iOwnerGeneration",
    ):
        recordTask.taskAsync.iOwnerGeneration = iNewGeneration


def _flistDetachOldSessionConnections(appState, recordOwner, sOldSessionId):
    """Detach the old session's sockets and zero the owner's counters.

    Every live connection on the record belongs to the old generation
    (the new session has not connected yet), so the counters are
    zeroed atomically with the detach — the new pipeline socket can
    never be 4409'd by the old one's budget, and the old sockets'
    ``finally`` blocks carry a stale generation that decrements
    nothing (design §2.3, case 6).
    """
    recordOwner.iLiveConnectionCount = 0
    recordOwner.iLivePipelineConnectionCount = 0
    dictSessionSockets = getattr(appState, "dictSessionSockets", None)
    if dictSessionSockets is None or not sOldSessionId:
        return []
    return list(dictSessionSockets.pop(sOldSessionId, set()))


async def _fnCloseDetachedConnections(listDetached):
    """Actively close the detached old sockets (after the commit).

    By now the old credential authorizes nothing and the stale
    generation cannot touch the successor's counters, so the close is
    a courtesy to the old tab, not a correctness step; failures are
    tolerated.
    """
    for recordConnection in listDetached:
        try:
            await recordConnection.connection.close(code=4401)
        except Exception:  # noqa: BLE001 — a dead socket is already closed
            pass


# ---------------------------------------------------------------------
# The orphan transition (design §4/§5, slice 6).
# ---------------------------------------------------------------------

async def fnOrphanSession(appState, sName, fbStillWarranted=None):
    """Commit ACTIVE→ORPHANED_SESSION for the container's owning session.

    The §5 orphan transition, under the canonical lock order: the
    synchronous commit — credential revocation, unused-capability
    cancellation, the workflow-session hook, the ORPHANED stamp — runs
    under the container-mutation lock with no ``await`` inside it, and
    the active socket close runs only AFTER the commit (the close is an
    ``await``; by then the credential authorizes nothing, and the
    per-frame backstop covers a socket already mid-frame). The record
    RETAINS its flock, keep-alive, agent token, generation, and any
    live task — orphaning ends the browser session's authority, never
    the container's work; only a host transfer or the safe reaper ends
    the record itself.

    ``fbStillWarranted`` re-evaluates the caller's trigger against the
    owner record under the held lock — a socket may have reconnected,
    or a transfer may have rebound the record, between detection and
    commit — and a False answer skips the transition.
    """
    dictLockStore = _fdictLockStoreForAppState(appState)
    async with _flockObtainContainerMutation(dictLockStore, sName):
        listOrphanedConnections = _flistCommitOrphanSynchronously(
            appState, sName, fbStillWarranted,
        )
    await _fnCloseDetachedConnections(listOrphanedConnections)


def _flistCommitOrphanSynchronously(appState, sName, fbStillWarranted):
    """Run the synchronous orphan commit; return the sockets to close.

    Steps (a) through (d) of design §5 plus the ORPHANED stamp, all
    synchronous so nothing interleaves on the event loop between the
    revocation and the state change. The session's live connection
    records are returned — not detached — because the orphan keeps the
    owner generation, so each closed socket's ``finally`` decrements
    the counters and drops its ``dictSessionSockets`` entry itself.
    """
    recordOwner = getattr(appState, "dictContainerOwners", {}).get(sName)
    if recordOwner is None or recordOwner.sState == (
        containerOwnership.S_OWNER_STATE_ORPHANED_SESSION
    ):
        return []
    if fbStillWarranted is not None and not fbStillWarranted(recordOwner):
        return []
    dictStore = getattr(appState, "dictBrowserSessions", None) or {}
    sSessionId = recordOwner.sBrowserSessionId
    # (a) The credential authorizes nothing from this statement on.
    browserSession.fnRevokeSessionById(dictStore, sSessionId)
    # (d) Unused capabilities die with the session (tickets and
    # download capabilities join this call when their slices land).
    browserSession.fnExpireCapabilitiesForSession(dictStore, sSessionId)
    # (c) The workflow-session invalidation hook — a recorded no-op.
    _fnInvalidateWorkflowSessionsForOrphan(sSessionId)
    recordOwner.sState = containerOwnership.S_OWNER_STATE_ORPHANED_SESSION
    recordOwner.fOrphanedSinceMonotonic = time.monotonic()
    dictSessionSockets = getattr(appState, "dictSessionSockets", None) or {}
    # (b) collected here, closed by the caller after the commit.
    return list(dictSessionSockets.get(sSessionId, set()))


def _fnInvalidateWorkflowSessionsForOrphan(sBrowserSessionId):
    """The §5(c) workflow-session invalidation hook — deliberately empty.

    Deferred with the rationale recorded (design §5): the credential is
    revoked in the same synchronous commit, so no HTTP request can
    authorize as the orphaned session and a live workflow-session id
    can never be replayed by it, and the agent lane never authorizes
    through workflow-session ids at all. Real invalidation lands with
    the workflow-session mechanism slice, wired through this call site.
    """
    del sBrowserSessionId


async def fnOrphanOwnersPastReconnectWindow(appState):
    """Orphan every ACTIVE owner whose last socket is gone past the window.

    The §4 trigger: only matched-generation decrements can bring
    ``iLiveConnectionCount`` to zero; ``bSocketEverExisted`` gates out a
    claim that never opened a socket (a claim-then-crash falls to the
    idle reap window instead of orphaning instantly); and the reconnect
    window is measured from ``fLastSeenMonotonic``, which every socket
    close stamps, so a reload that reconnects within
    ``F_RECONNECT_WINDOW_SECONDS`` stays ACTIVE. Counting the terminal
    lane in the same live total means closing one terminal while the
    pipeline socket lives never orphans (case 18). The conditions are
    re-evaluated under the container-mutation lock, so a socket that
    reconnects between detection and commit cancels the transition. The
    frontend's ``pagehide`` handler deliberately emits no release
    signal today; were one added it could only SHORTEN this grace,
    never orphan authoritatively (design §4).
    """
    dictContainerOwners = getattr(appState, "dictContainerOwners", {})
    for sName in list(dictContainerOwners.keys()):
        recordOwner = dictContainerOwners.get(sName)
        if recordOwner is None or not _fbOwnerPastReconnectWindow(
            recordOwner,
        ):
            continue
        await fnOrphanSession(
            appState, sName, fbStillWarranted=_fbOwnerPastReconnectWindow,
        )


def _fbOwnerPastReconnectWindow(recordOwner):
    """Return True when the §4 zero-sockets orphan conditions all hold."""
    if recordOwner.sState != containerOwnership.S_OWNER_STATE_ACTIVE:
        return False
    if not recordOwner.bSocketEverExisted:
        return False
    if recordOwner.iLiveConnectionCount > 0:
        return False
    return (
        time.monotonic() - recordOwner.fLastSeenMonotonic
        >= F_RECONNECT_WINDOW_SECONDS
    )
