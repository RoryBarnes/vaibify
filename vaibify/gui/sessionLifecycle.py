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
   primitives.** ``ftClaim``, ``fbReleaseOwnership``, and
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
    "F_TRANSFER_COMMIT_HEADROOM_SECONDS",
    "S_TRANSFER_TRANSFERRED",
    "S_TRANSFER_BUSY_RETRY",
    "S_TRANSFER_EXPIRED",
    "S_TRANSFER_UNOWNED",
    "S_TRANSFER_STALE_GENERATION",
    "S_TRANSFER_REFUSED",
    "fdictCreateLifecycleLockStore",
    "flockContainerMutationForAppState",
    "flockSessionCardinalityForAppState",
    "ftClaimWithCardinality",
    "ftReserveContainerForStart",
    "ftSettleFailedStartOwnership",
    "S_START_RESERVED",
    "S_START_ALREADY_RESERVED",
    "S_START_REFUSED",
    "fbReleaseExplicit",
    "ftReleaseExplicit",
    "S_RELEASE_RELEASED",
    "S_RELEASE_NOT_OWNER",
    "S_RELEASE_BUSY",
    "ftTransferOwnership",
    "fnScheduleConnectionFencing",
    "fnOrphanSession",
    "fnOrphanOwnersPastReconnectWindow",
    "fnExpireIdleBrowserSessions",
    "fnEvaluateSessionLifecycle",
    "fdictSessionExpiryView",
    "F_EXPIRY_WARNING_LEAD_SECONDS",
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


# A remote session's socket dies for a different reason than a local
# one's. Locally a closed socket means a human closed a window, and 15
# seconds is generous for a reload. Through a tunnel it usually means a
# network changed while the researcher sat there, so the same evidence
# carries a different meaning and the lane carries its own number. The
# client retries for exactly this long; see commandRemote.
F_REMOTE_RECONNECT_WINDOW_SECONDS = containerOwnership.\
    ffReadSecondsFromEnvironment(
        "VAIBIFY_REMOTE_RECONNECT_WINDOW_SECONDS", 900.0,
    )


def ffReconnectWindowSecondsForSession(
    sBrowserSessionId="", dictBrowserSessions=None,
):
    """Return the hold window this browser session may rely on.

    The client sizes its reconnect ladder from this number, so the two
    cannot disagree by construction. They used to: a 31-second ladder
    retried against a window that had already revoked the credential
    at ~20 seconds, so the last attempts were refused 4401 and the
    refusal was reported to the researcher as a server restart.

    One value today. This is the seam a longer-lived lane branches at,
    and the reason the client is TOLD the window rather than shipping
    its own copy of the constant.
    """
    if sBrowserSessionId and dictBrowserSessions:
        if browserSession.fbSessionIsRemote(
            dictBrowserSessions, sBrowserSessionId,
        ):
            return F_REMOTE_RECONNECT_WINDOW_SECONDS
    return F_RECONNECT_WINDOW_SECONDS
# How recently the owning browser must have spoken for a claim that has
# not opened its first socket yet to count as still attended. Measured
# against the hub screens' own poll, which is far faster, so the
# window's job is only to notice a browser that STOPPED.
F_CLAIM_PRESENCE_WINDOW_SECONDS = (
    containerOwnership.ffReadSecondsFromEnvironment(
        "VAIBIFY_CLAIM_PRESENCE_WINDOW_SECONDS", 30.0,
    )
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

# How long before the absolute cap the dashboard is warned (design
# §11). Generous by design: the warning exists so a researcher whose
# tab has been open all day can finish, or re-attach with
# 'vaibify open', rather than discover the cap by being logged out.
F_EXPIRY_WARNING_LEAD_SECONDS = (
    containerOwnership.ffReadSecondsFromEnvironment(
        "VAIBIFY_EXPIRY_WARNING_LEAD_SECONDS", 900.0,
    )
)

# Transfer timing (design §6.1). A transfer no longer WAITS for
# anything: a busy container is refused at once, so the only TTL a
# transfer needs is enough to commit in. The drain-wait knob is gone
# with the phase it bounded -- keeping it would have left the
# insufficient-TTL check demanding 50 seconds of window for an
# operation that needs 30, refusing capabilities that would have
# committed comfortably.
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
# Explicit-release outcomes (design §10). RELEASED committed; NOT_OWNER
# is the lease/session refusal; BUSY is the retained refusal — the
# record still exists and the container is still held, which is why it
# answers 409 rather than a bare "false".
S_RELEASE_RELEASED = "released"
S_RELEASE_NOT_OWNER = "notOwner"
S_RELEASE_BUSY = "busy"

# Start-reservation outcomes (design §10b). RESERVED minted a fresh
# reservation; ALREADY_RESERVED is the idempotent recovery for a start
# still in flight; REFUSED carries the message naming what to do next.
S_START_RESERVED = "reserved"
S_START_ALREADY_RESERVED = "alreadyReserved"
S_START_REFUSED = "refused"

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


def flockSessionCardinalityForAppState(appState):
    """Return the hub-wide cardinality lock (lock 2 of the hierarchy).

    The start-reservation authority (``startReservation``, design §10b)
    runs the same read-check-write against ``dictSessionOwner`` that a
    claim does, so it must contend on the SAME object — a second lock
    would let a claim on container A and a start on container B both see
    an empty index. Exposed here, beside lock 1, so no caller is tempted
    to reach into the private lock store and invert the canonical order.
    """
    return _flockObtainSessionCardinality(
        _fdictLockStoreForAppState(appState),
    )


async def ftClaimWithCardinality(
    appState, sName, sLeaseId, iPort, sContainerId="",
    fbPipelineRunning=None, sBrowserSessionId="", connectionDocker=None,
):
    """Commit a claim under the canonical lock order (design §9).

    The sole claim path for routes: acquires the container-mutation lock
    (the drain), then the hub-wide cardinality lock, and only then runs
    the synchronous :func:`containerOwnership.ftClaim`, whose
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
            tClaimVerdict = containerOwnership.ftClaim(
                dictContainerOwners, sName, sLeaseId, iPort,
                sContainerId=sContainerId,
                fbPipelineRunning=fbPipelineRunning,
                sBrowserSessionId=sBrowserSessionId,
                dictSessionOwner=dictSessionOwner,
                connectionDocker=connectionDocker,
            )
            if tClaimVerdict[0] == 200:
                # A fresh lease reopens what the previous release
                # closed: the council command gate refuses by resource
                # name, and this name has an owner again.
                _fnReopenCouncilAdmission(appState, sName)
            return tClaimVerdict


async def ftReserveContainerForStart(
    appState, sName, sBrowserSessionId, iPort, connectionDocker,
    fnMintReservation, fsRefusalForPriorOutcome=None,
):
    """Acquire a container for a start and mint its reservation (§10b).

    The start's creation path, committed here for the same reason every
    other one is: the ownership acquisition and the one-container-per-
    session read-check-write must be atomic against a concurrent claim on
    a DIFFERENT container, which only the hub-wide cardinality lock can
    make them. The claim primitive is reused deliberately — it is the
    single place that arbitrates the flock, the journal quarantine, the
    cross-hub refusal, and the reverse index together, so a start can
    never open a second door into ownership.

    ``fnMintReservation(recordOwner)`` runs synchronously inside the held
    locks: the start authority owns what a reservation IS, this module
    owns when a record may acquire one. The lease the claim mints is
    never returned — a start hands out no authority, because nothing is
    running yet for a lease to authorize.

    Returns ``(sOutcome, dictPayload, recordOwner)``.
    """
    dictLockStore = _fdictLockStoreForAppState(appState)
    async with _flockObtainContainerMutation(dictLockStore, sName):
        async with _flockObtainSessionCardinality(dictLockStore):
            return _ftReserveForStartUnderLocks(
                appState, sName, sBrowserSessionId, iPort, connectionDocker,
                fnMintReservation, fsRefusalForPriorOutcome,
            )


def _ftReserveForStartUnderLocks(
    appState, sName, sBrowserSessionId, iPort, connectionDocker,
    fnMintReservation, fsRefusalForPriorOutcome,
):
    """Arbitrate one start synchronously under both held locks."""
    dictOwners = getattr(appState, "dictContainerOwners", {})
    recordOwner = dictOwners.get(sName)
    if recordOwner is not None and getattr(
        recordOwner, "reservation", None,
    ) is not None:
        if recordOwner.sBrowserSessionId not in ("", sBrowserSessionId):
            return (S_START_REFUSED, _fdictStartInUse(sName), None)
        return (S_START_ALREADY_RESERVED, {}, recordOwner)
    sRefusal = (
        fsRefusalForPriorOutcome() if fsRefusalForPriorOutcome else ""
    )
    if sRefusal:
        return (S_START_REFUSED, {"sName": sName, "sMessage": sRefusal}, None)
    if recordOwner is None:
        iStatusCode, dictPayload = containerOwnership.ftClaim(
            dictOwners, sName, "", iPort,
            sBrowserSessionId=sBrowserSessionId,
            dictSessionOwner=getattr(appState, "dictSessionOwner", None),
            connectionDocker=connectionDocker,
        )
        dictPayload.pop("sLeaseId", None)
        if iStatusCode != 200:
            return (S_START_REFUSED, dictPayload, None)
        recordOwner = dictOwners[sName]
        sPriorOwnerLeaseId = containerOwnership.S_NO_PRIOR_OWNER
    elif recordOwner.sBrowserSessionId not in ("", sBrowserSessionId):
        return (S_START_REFUSED, _fdictStartInUse(sName), None)
    else:
        sPriorOwnerLeaseId = recordOwner.sLeaseId
    fnMintReservation(recordOwner, containerOwnership.fidentityRecordOwnership(
        recordOwner, sPriorOwnerLeaseId,
    ))
    # The OTHER door into ownership (§10b), and it must reopen council
    # admission exactly as a claim does: a container released, stopped
    # and freshly started under the same name would otherwise inherit
    # the previous era's closed admission and never convene a council
    # again.
    _fnReopenCouncilAdmission(appState, sName)
    return (S_START_RESERVED, {}, recordOwner)


def _fdictStartInUse(sName):
    """Return the refusal body for a container another session holds."""
    return {
        "sName": sName,
        "sMessage": (
            f"Container '{sName}' is in use in another browser session."
        ),
    }


async def ftSettleFailedStartOwnership(
    appState, sName, identityOwnership, fbCommitSettlement,
):
    """Commit a failed start's settlement, freeing the flock only if clean.

    ``fbCommitSettlement(recordOwner)`` runs synchronously inside the
    held locks and answers whether the container was proven clean. Only
    then is the flock freed: an inconclusive settlement keeps it, so
    neither this hub nor the next can hand the container to a second
    owner while a late create may still be landing.

    A clean settlement is necessary but NOT sufficient. The release is
    additionally conditional on ``identityOwnership`` — the ownership
    this start recorded when it began — both having been ESTABLISHED by
    the start and STILL being the live ownership. Two distinct failures
    hide behind that:

    * a start on a container the caller already owns reserves on the
      existing record, and releasing that drops a valid owner's lease
      and frees a flock nobody offered — the researcher clicks Start on
      a container they own that is already running, the start refuses,
      and their ownership silently disappears; and
    * the ownership a start DID establish can be replaced while the
      start runs. A host transfer rotates the lease, the generation, and
      the browser session on that record, so the successor's ownership
      is not the one this start created, and a Boolean "I created it"
      would happily free the successor's.
    """
    dictLockStore = _fdictLockStoreForAppState(appState)
    async with _flockObtainContainerMutation(dictLockStore, sName):
        async with _flockObtainSessionCardinality(dictLockStore):
            dictOwners = getattr(appState, "dictContainerOwners", {})
            recordOwner = dictOwners.get(sName)
            bMayRelease = _fbStartMayFreeOwnership(
                recordOwner, identityOwnership,
            )
            if not fbCommitSettlement(recordOwner) or recordOwner is None:
                return
            if not bMayRelease:
                return
            containerOwnership.fbReleaseOwnership(
                dictOwners, sName, recordOwner.sLeaseId,
                sBrowserSessionId=recordOwner.sBrowserSessionId,
                dictSessionOwner=getattr(appState, "dictSessionOwner", None),
            )


def _fbStartMayFreeOwnership(recordOwner, identityOwnership):
    """Return True when a failed start may release what it is sitting on."""
    if identityOwnership is None:
        return False
    return (
        identityOwnership.bEstablishedTheOwnership
        and containerOwnership.fbOwnershipIdentityStillHolds(
            recordOwner, identityOwnership,
        )
    )


async def fbReleaseExplicit(appState, sName, sLeaseId, sBrowserSessionId=""):
    """Return True when an explicit release COMMITS (design §10).

    The boolean face of :func:`ftReleaseExplicit`, for callers that
    only need to know whether the record went away. A foreign lease
    and a busy container both read False here; the route uses the
    outcome form so it can tell the researcher WHICH, and why.
    """
    sOutcome, _ = await ftReleaseExplicit(
        appState, sName, sLeaseId, sBrowserSessionId=sBrowserSessionId,
    )
    return sOutcome == S_RELEASE_RELEASED


async def ftReleaseExplicit(
    appState, sName, sLeaseId, sBrowserSessionId="", bForce=False,
):
    """Arbitrate and commit an explicit release; return its outcome.

    The sole release path for routes (design §3/§10), under the
    canonical lock order: the container-mutation lock (the drain),
    then the cardinality lock. Returns ``(sOutcome, dictPayload)``;
    every refusal carries an ``sMessage`` naming the recovery.

    Order, and why each step is where it is:

    1. **Authorization first.** ``fbReleaseWouldBePermitted`` alone
       decides whether the presenting session holds the session-bound
       lease, so an unauthorized attempt learns nothing about the
       container's business and never touches the true owner's
       terminals or channels.
    2. **Busy refusal (§10).** A live durable task or a live guarded
       mutation refuses outright — those can still commit, and freeing
       the flock over them would hand the container to a second owner
       a live worker is still writing. A live in-container agent
       refuses too, but ``bForce`` overrides THAT refusal and only
       that one.
    3. **Terminal drain.** Every live terminal execution is terminated
       and its recorded process group proven empty, or the record is
       retained-and-quarantined (case 44), so a signal-trapping shell
       cannot write after release any more than it can after transfer.
    4. **Channels close BEFORE the flock (§10).** The releasing
       session's own WebSockets are detached and closed while the
       record still exists; only then is the flock freed. Releasing
       first would leave a live socket pointed at a container the hub
       no longer owns.
    """
    dictLockStore = _fdictLockStoreForAppState(appState)
    dictContainerOwners = getattr(appState, "dictContainerOwners", {})
    dictSessionOwner = getattr(appState, "dictSessionOwner", None)
    async with _flockObtainContainerMutation(dictLockStore, sName):
        if not containerOwnership.fbReleaseWouldBePermitted(
            dictContainerOwners, sName, sLeaseId,
            sBrowserSessionId=sBrowserSessionId,
        ):
            return (S_RELEASE_NOT_OWNER, {
                "sMessage": f"Container '{sName}' is not held by this "
                            "browser session's lease, so it was not "
                            "released.",
            })
        sBusyMessage = _fsReleaseBusyReason(appState, sName, bForce)
        if sBusyMessage:
            return (S_RELEASE_BUSY, {"sMessage": sBusyMessage})
        # Council admission closes ATOMICALLY before anything awaits:
        # the busy check alone is check-then-act — a respond authorized
        # in the same tick could spawn a paid turn right after it. The
        # close-then-recheck runs in one synchronous stretch on the
        # loop, so a drive that slipped in is seen (release refuses)
        # and one arriving later is refused at the command gate.
        sCouncilAdmissionMessage = _fsCloseCouncilAdmissionBeforeRelease(
            appState, sName)
        if sCouncilAdmissionMessage:
            return (S_RELEASE_BUSY, {"sMessage": sCouncilAdmissionMessage})
        bReleased = False
        try:
            # Council state settles INSIDE the ownership transaction,
            # still under the container-mutation lock: a concurrent
            # claim serializes behind this lock, so it cannot reopen
            # admission while paused campaigns are mid-settlement —
            # the interleaving that let a second campaign continue
            # from the previous lease era. The ``finally`` reopens on
            # ANY exit that did not commit (a drain fault, a
            # cancellation, an ownership change), so a close can never
            # outlive an aborted release.
            sCouncilSettlementMessage = (
                await _fsSettleCouncilStateBeforeRelease(appState, sName))
            if sCouncilSettlementMessage:
                return (S_RELEASE_BUSY,
                        {"sMessage": sCouncilSettlementMessage})
            await _fnDrainAndCloseBeforeRelease(appState, sName)
            async with _flockObtainSessionCardinality(dictLockStore):
                bReleased = containerOwnership.fbReleaseOwnership(
                    dictContainerOwners, sName, sLeaseId,
                    sBrowserSessionId=sBrowserSessionId,
                    dictSessionOwner=dictSessionOwner,
                )
        finally:
            if not bReleased:
                _fnReopenCouncilAdmission(appState, sName)
    if not bReleased:
        return (S_RELEASE_NOT_OWNER, {
            "sMessage": f"Container '{sName}' was not released; its "
                        "ownership changed during the request.",
        })
    return (S_RELEASE_RELEASED, {})


async def _fsSettleCouncilStateBeforeRelease(appState, sName):
    """Settle every paused council runtime; return a refusal or ''.

    A drain that could not PROVE every egress boundary gone refuses the
    release: dropping the lease over an unproven proxy would hand the
    container to the next session while a council network may still be
    dialling out. The refusal returns BUSY, so ``bReleased`` stays
    False and the caller's ``finally`` reopens admission — the
    container keeps its owner, who can retry (the retry state is held
    on the runtime) or stop the council and release again.
    """
    from . import agentCouncilController
    dictCouncilControllerState = getattr(
        appState, agentCouncilController.S_COUNCIL_CONTROLLER_STATE_KEY,
        None)
    if not isinstance(dictCouncilControllerState, dict):
        return ""
    dictSettlement = (
        await agentCouncilController.fdictDrainControllerForResource(
            dictCouncilControllerState, sName))
    if dictSettlement["bAllSettled"]:
        return ""
    return (
        f"Container '{sName}' still has Agent Council network resources "
        "that could not be proven gone "
        f"({len(dictSettlement['listUnsettledCampaignIds'])} campaign(s)); "
        "it is retained rather than handed on with a council proxy that "
        "may still be running. Retry the release, or stop the council "
        "first."
    )


def _fsCloseCouncilAdmissionBeforeRelease(appState, sName):
    """Close council admission for a releasing container, atomically.

    Returns the refusal message when a live drive is found AFTER the
    close (the admission is reopened first — the container stays
    owned), or the empty string when the close is clean and the
    release may proceed.
    """
    from . import agentCouncilController
    dictCouncilControllerState = getattr(
        appState, agentCouncilController.S_COUNCIL_CONTROLLER_STATE_KEY,
        None)
    if not isinstance(dictCouncilControllerState, dict):
        return ""
    if agentCouncilController.fbCloseResourceAdmission(
            dictCouncilControllerState, sName):
        return ""
    agentCouncilController.fnReopenResourceAdmission(
        dictCouncilControllerState, sName)
    return (
        f"Container '{sName}' has an Agent Council still "
        "deliberating — paid provider work that no release should "
        "silently abandon. Stop the council first, then release."
    )


def _fnReopenCouncilAdmission(appState, sName):
    """Reopen council admission when a close outlived its purpose."""
    from . import agentCouncilController
    dictCouncilControllerState = getattr(
        appState, agentCouncilController.S_COUNCIL_CONTROLLER_STATE_KEY,
        None)
    if isinstance(dictCouncilControllerState, dict):
        agentCouncilController.fnReopenResourceAdmission(
            dictCouncilControllerState, sName)


def _fsReleaseBusyReason(appState, sName, bForce):
    """Return why a release is refused as busy, or '' when permitted.

    Design §10: permitted only with no live durable task, no in-flight
    guarded mutation, and no recent agent activity. **Force overrides
    ONLY the agent-liveness refusal** — never a live durable task and
    never a live guarded mutation, because those can still commit to
    the container, and no researcher's impatience makes that safe.
    """
    from . import commitCarrier
    if _frecordLiveDurableTask(appState, sName) is not None:
        return (
            f"Container '{sName}' has a run still in progress. "
            "Releasing it would hand the container away from live "
            "work, so it is retained; wait for the run to finish or "
            "stop it first."
        )
    if commitCarrier.fbContainerHasLiveMutationWork(appState, sName):
        return (
            f"Container '{sName}' has a guarded operation still "
            "running. It is retained until that operation settles."
        )
    from . import agentCouncilChat
    from . import agentCouncilController
    dictCouncilControllerState = getattr(
        appState, agentCouncilController.S_COUNCIL_CONTROLLER_STATE_KEY,
        None)
    if isinstance(dictCouncilControllerState, dict) and (
        agentCouncilController.fbControllerHasLiveDriveForResource(
            dictCouncilControllerState, sName,
        )
    ):
        return (
            f"Container '{sName}' has an Agent Council still "
            "deliberating — paid provider work that no release should "
            "silently abandon. Stop the council first, then release."
        )
    # A SEPARATE clause with its own words, not a widened one. Both are
    # paid provider work, but the remedy differs: nobody stops a
    # council to end a conversation, and a refusal that named the wrong
    # remedy would send the researcher to a button that does nothing.
    if isinstance(dictCouncilControllerState, dict) and (
        agentCouncilChat.fbResourceHasChatMessageInFlight(
            dictCouncilControllerState, sName,
        )
    ):
        return (
            f"Container '{sName}' has a council chairbot still answering "
            "a question — paid provider work that no release should "
            "silently abandon. Wait for the answer, or close the "
            "conversation, then release."
        )
    if bForce:
        return ""
    recordOwner = getattr(appState, "dictContainerOwners", {}).get(sName)
    if recordOwner is not None and containerOwnership.fbAgentIsLiveOnRecord(
        recordOwner,
    ):
        return (
            f"An in-container agent is working in '{sName}', so it was "
            "retained. Wait for the agent to go idle, or stop the "
            "container to take it back."
        )
    return ""


async def _fnDrainAndCloseBeforeRelease(appState, sName):
    """Prove the terminals dead and close the channels, flock still held."""
    from . import terminalContainment
    await asyncio.to_thread(
        terminalContainment.fdictDrainTerminalRecordsForContainer,
        appState, sName,
    )
    recordOwner = getattr(appState, "dictContainerOwners", {}).get(sName)
    if recordOwner is None:
        return
    await _fnCloseDetachedConnections(
        _flistDetachOldSessionConnections(
            appState, recordOwner, recordOwner.sBrowserSessionId,
        ),
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

    Shape (design §6.1): a busy container is refused AT ONCE, naming
    what holds it — the transfer never waits on the mutation lock, and
    there is no DRAINING phase. With the lock free it is taken first,
    holding no global lock; pre-commit refusals (unowned, stale
    generation, poison, cancel-requested task, unsettled journal, a
    live terminal record) run under it; everything reversible is
    pre-minted; and the linearization — final checks, lease rotation,
    session rebind, generation bump, task retag, old-credential
    revocation, stored-result write — commits SYNCHRONOUSLY with no
    ``await`` between the final check and the commit. Old sockets are
    actively closed only after the commit.

    What a transfer does about live work, exactly — the three cases are
    different and the difference is the whole safety story:

    * a **lock-held** mutation holds the container-mutation lock, so the
      transfer is REFUSED at once, naming it;
    * a **registered durable** (mode-(c)) task is ADOPTED — retagged to
      the successor generation and left running. Deliberate, and the
      point of the axis: a researcher whose browser died during a
      six-hour run re-attaches with ``vaibify open`` rather than waiting
      it out;
    * an **unregistered** mutation is INVISIBLE here. It holds no lock
      and appears in no registry, so the transfer cannot see it and
      commits straight over it, and the departed session's command goes
      on running in the successor's container.

    The third case is the open hole, not a covered one. Registration is
    what moves a mutation from the third case into the first or the
    second; until every mutating path registers, a transfer is a barrier
    against declared work only. See AGENTS.md, "Container mutations go
    through the commit-guard carrier".
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
    tPrecheck = _ftOutcomeForCapabilityState(dictStore, sCapability,
                                            dictInspect)
    if tPrecheck is not None:
        return tPrecheck
    sName = dictInspect["sContainerName"]
    dictLockStore = _fdictLockStoreForAppState(appState)
    lockMutation = _flockObtainContainerMutation(dictLockStore, sName)
    if lockMutation.locked():
        # REFUSE, do not wait. Waiting spends the capability's window on
        # an operation whose length nobody knows -- a two-second write
        # and a half-hour rebuild are the same locked lock -- and the
        # researcher is left staring at a command that has not answered.
        # An immediate, specific refusal lets them decide.
        return _ftOutcomeForBusyContainer(appState, sCapability, sName)
    # Uncontended, so this cannot yield: there is no await between the
    # check above and the acquisition, and a single-threaded event loop
    # cannot interleave another coroutine into that gap.
    await lockMutation.acquire()
    try:
        return await _ftTransferUnderDrain(
            appState, dictStore, dictLockStore, sCapability, sName,
            dictInspect["iExpectedOwnerGeneration"],
        )
    finally:
        lockMutation.release()


def _ftOutcomeForCapabilityState(dictStore, sCapability, dictInspect):
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
    if dictInspect["fRemainingTtlSeconds"] < (
        F_TRANSFER_COMMIT_HEADROOM_SECONDS
    ):
        browserSession.fnExpireCapability(dictStore, sCapability)
        return (S_TRANSFER_EXPIRED, {
            "sMessage": "Too little of the transfer capability's window "
                        "remains to attempt a transfer; mint a fresh one "
                        "with 'vaibify open'.",
        })
    return None


def _ftOutcomeForBusyContainer(appState, sCapability, sName):
    """Refuse a transfer into a busy container, naming what holds it.

    Nothing is minted, revoked, bumped, or CONSUMED: the capability
    stays ARMED, so the researcher retries with the same one once the
    named operation finishes. That is the whole difference from waiting
    -- a wait spends the window and then reports the same thing.
    """
    del sCapability
    from . import commitCarrier
    sLiveOperation = commitCarrier.fsDescribeLiveMutationWork(
        appState, sName,
    ) or "a guarded operation"
    return (S_TRANSFER_BUSY_RETRY, {
        "sMessage": (
            f"Container '{sName}' is busy: {sLiveOperation} is running "
            "and holds it. Retry when it finishes; this transfer "
            "capability stays valid."
        ),
        "sLiveOperation": sLiveOperation,
    })


async def _ftTransferUnderDrain(
    appState, dictStore, dictLockStore, sCapability, sName, iExpectedGen,
):
    """Run the pre-checks and commit under the held drain.

    There is no DRAINING phase. It existed to fence and terminate
    in-process terminal records before a hand-over, and it is gone for
    two reasons that reinforce each other: the terminal is disabled, so
    no such record can be created; and a legacy record from an earlier
    version is an unsettled journal record, which
    ``_ftRefusalBeforePremint`` already refuses over, naming 'vaibify
    reconcile'. Draining would also have made the transfer WAIT --
    inside the held lock, on a thread -- which is exactly what a
    hand-over must not do.
    """
    tRefusal = _ftRefusalBeforePremint(
        appState, dictStore, sCapability, sName, iExpectedGen,
    )
    if tRefusal is not None:
        return tRefusal
    # Pre-mint everything reversible before the commit (§6.1).
    sNewSessionId, sNewCredential = (
        browserSession.ftMintDetachedSessionRecord(dictStore)
    )
    async with _flockObtainSessionCardinality(dictLockStore):
        # Final check + commit: SYNCHRONOUS from here to the return —
        # no await may separate the generation check from the commit.
        tLateRefusal = _ftRefusalAtCommitPoint(
            appState, dictStore, sNewCredential, sName, iExpectedGen,
            sCapability,
        )
        if tLateRefusal is not None:
            return tLateRefusal
        dictPayload, listDetached = _ftCommitTransfer(
            appState, dictStore, sCapability, sName,
            sNewSessionId, sNewCredential,
        )
    await _fnCloseDetachedConnections(listDetached)
    return (S_TRANSFER_TRANSFERRED, dictPayload)


def _ftRefusalBeforePremint(
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
    adoption case). A live terminal record is NOT tolerated: there is
    no DRAINING phase any more, and the commit point refuses over one
    rather than settling it on a probe. Anything else — a quarantined
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
                f"({sOperationId}, kind {dictRecord['sKind']}, target "
                f"{dictRecord.get('sTarget', 'unrecorded')}); run "
                "'vaibify reconcile' before transferring."
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


def _ftRefusalAtCommitPoint(
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
        # A terminal execution whose process group nobody has proven
        # dead. With the terminal disabled this can only be a record
        # inherited from an earlier version, and a hand-over must not
        # carry one to a successor: the exit is reconciliation, which
        # stops the container or proves the group empty.
        browserSession.fnDiscardSessionRecord(dictStore, sNewCredential)
        return (S_TRANSFER_REFUSED, {
            "sMessage": f"Container '{sName}' has a terminal execution "
                        "whose process group has not been proven dead. "
                        "Restart the container or run 'vaibify "
                        "reconcile', then retry.",
        })
    return None


def _ftCommitTransfer(
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
    _fnRebindStartResultEntitlement(appState, sName, sNewSessionId)
    listDetached = _flistDetachOldSessionConnections(
        appState, recordOwner, sOldSessionId,
    )
    browserSession.fbRevokeSessionById(dictStore, sOldSessionId)
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


def _fnRebindStartResultEntitlement(appState, sName, sNewSessionId):
    """Rebind the start-result entitlement inside the same commit (§10b).

    A start requested by the predecessor may still be running, or may
    have finished without its outcome being collected. Success delivery
    needs no rebinding (it derives from the owner record, which this
    commit has just rebound), but the FAILURE entitlement is bound to a
    browser session — and this commit is about to revoke the old one. So
    the successor inherits the right to read the outcome, atomically,
    and the revoked session loses it.
    """
    from . import startResultStore
    startResultStore.fnRebindStartResultsForTransfer(
        appState, sName, sNewSessionId,
    )


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
            await recordConnection.websocket.close(code=4401)
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
    browserSession.fbRevokeSessionById(dictStore, sSessionId)
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


def fbOwningBrowserIsPresentBeforeFirstSocket(appState, recordOwner):
    """Return True when a socketless claim's browser is demonstrably here.

    The idle reaper asks "has this claim been abandoned?" and answers
    it from ``fLastSeenMonotonic``, which only a socket ever stamps. In
    the window between the claim and the first socket there is no
    socket by definition, so the record ages out on a clock nothing can
    advance — and the product PUTS work in that window. A container
    claim waits on readiness; a host claim waits on the researcher
    reading the uncontained-execution disclosure, which is a screen
    they are meant to spend time on. Thirty seconds later the record is
    reaped and their next click answers "Claim this container before
    connecting to it" for a project they hold.

    The evidence the reaper actually wants is whether the OWNING
    BROWSER is still there, and the hub already knows: every
    authenticated request refreshes the session's own last-seen stamp,
    and the hub screens poll while they are open. So a session seen
    within the window vetoes the reap, and a browser that closed stops
    refreshing and frees the claim exactly as before.

    Deliberately narrow. It answers only for an ACTIVE record that has
    never had a socket. A record whose socket existed and went away is
    the ORPHANED_SESSION path's business (design §4/§7), which has its
    own conditions and is untouched here; and a record with no bound
    session (the transitional and viewer records, which carry '')
    keeps today's behaviour, because there is no session to ask.
    """
    if recordOwner.sState != containerOwnership.S_OWNER_STATE_ACTIVE:
        return False
    if recordOwner.bSocketEverExisted:
        return False
    if not recordOwner.sBrowserSessionId:
        return False
    dictLifetime = browserSession.fdictActiveSessionLifetimes(
        getattr(appState, "dictBrowserSessions", {}) or {},
    ).get(recordOwner.sBrowserSessionId)
    if dictLifetime is None:
        return False
    return dictLifetime["fIdleSeconds"] < F_CLAIM_PRESENCE_WINDOW_SECONDS


# ---------------------------------------------------------------------
# Owner-aware session expiry and the lifecycle evaluator (design §11).
# ---------------------------------------------------------------------

async def fnEvaluateSessionLifecycle(appState):
    """Run one lifecycle-evaluator pass over every owner and session.

    The single body of the ~5 s evaluator loop (design §11), whose
    scheduling lives in ``serverLifespan``. Two passes, in this order:
    the §4 zero-sockets orphan trigger first, so an owner whose browser
    is already gone is orphaned by its own (short) reconnect window
    rather than waiting out the (long) session windows; then the
    owner-aware session sweep, which by then sees the orphan's
    revocation already committed and has nothing left to do for it.
    """
    await fnOrphanOwnersPastReconnectWindow(appState)
    await fnExpireIdleBrowserSessions(appState)


async def fnExpireIdleBrowserSessions(appState):
    """Expire every browser session past its window — owner-aware.

    The §11 session sweep. A session that bootstraps and browses the
    picker without ever claiming has a credential and no owner record,
    so owner-record orphaning (§5) alone would let it live forever;
    this pass is what bounds it. It must be OWNER-AWARE, though: an
    expired session that OWNS a container is committed through
    :func:`fnOrphanSession`, never a bare revoke, because a bare revoke
    would strand an ACTIVE record whose owner can no longer
    authenticate and which the orphan-only reaper conditions cannot
    release.

    Expiry is re-evaluated under the container-mutation lock through
    ``fbStillWarranted``, so a record that was transferred or already
    orphaned between detection and commit is left to its new owner.
    """
    dictStore = getattr(appState, "dictBrowserSessions", None)
    if not dictStore:
        return
    dictContainerOwners = getattr(appState, "dictContainerOwners", {})
    dictSessionOwner = getattr(appState, "dictSessionOwner", None) or {}
    dictLifetimes = browserSession.fdictActiveSessionLifetimes(dictStore)
    for sSessionId, dictLifetime in dictLifetimes.items():
        sName = dictSessionOwner.get(sSessionId, "")
        recordOwner = dictContainerOwners.get(sName) if sName else None
        if not _fbBrowserSessionHasExpired(dictLifetime, recordOwner):
            continue
        await _fnCommitSessionExpiry(
            appState, dictStore, sName, recordOwner, sSessionId,
        )


def _fbBrowserSessionHasExpired(dictLifetime, recordOwner):
    """Return True when a session's cap or sliding-idle window ran out.

    Two windows with deliberately different relationships to a live
    socket (design §11):

    The ABSOLUTE CAP, measured from the session's creation, fires
    REGARDLESS of socket liveness. Scoping the socket veto to sliding
    idle is the whole point: a forgotten-open tab — the sole case the
    cap exists to bound — holds a live socket by definition, so a veto
    generalized to all three triggers would make the cap unreachable
    in exactly its target case. The pre-expiry dashboard warning
    (:func:`fdictSessionExpiryView`) is the mitigation.

    SLIDING IDLE is vetoed by a live WebSocket: a quiet but connected
    pipeline socket is activity, and the socket layer never refreshes
    the credential's last-seen stamp, so without the veto a dashboard
    that only streams events would have its credential revoked under
    the researcher.
    """
    if dictLifetime["fAgeSeconds"] >= F_ABSOLUTE_SESSION_CAP_SECONDS:
        return True
    if recordOwner is not None and recordOwner.iLiveConnectionCount > 0:
        return False
    return dictLifetime["fIdleSeconds"] >= F_SLIDING_IDLE_SECONDS


def fdictSessionExpiryView(appState, sCredential):
    """Return the presenting browser session's own expiry truth (§11).

    The single input to the pre-expiry dashboard warning. Every field
    is derived HERE, from the session record's own monotonic stamps: a
    page that counted down on its own clock would drift, would keep
    counting after a hub restart replaced the session entirely, and
    could not see a cap the environment had tuned — three ways to show
    the researcher a deadline that is not the real one.

    The countdown reports the ABSOLUTE CAP, not sliding idle. Sliding
    idle is refreshed by every request and vetoed by a live socket, so
    a dashboard in use is never near it and a countdown toward it
    would be a deadline the veto forbids. The cap has no veto, so it
    is the one worth warning about.

    An unknown or revoked credential answers ``bSessionKnown`` False
    with a zero countdown, never another session's clocks.
    """
    dictStore = getattr(appState, "dictBrowserSessions", None) or {}
    dictLifetime = browserSession.fdictLifetimeForCredential(
        dictStore, sCredential,
    )
    if dictLifetime is None:
        return {
            "bSessionKnown": False,
            "fSecondsUntilSessionCap": 0.0,
            "fWarningLeadSeconds": F_EXPIRY_WARNING_LEAD_SECONDS,
            "bExpiringSoon": False,
        }
    fRemainingSeconds = max(
        0.0,
        F_ABSOLUTE_SESSION_CAP_SECONDS - dictLifetime["fAgeSeconds"],
    )
    return {
        "bSessionKnown": True,
        "fSecondsUntilSessionCap": fRemainingSeconds,
        "fWarningLeadSeconds": F_EXPIRY_WARNING_LEAD_SECONDS,
        "bExpiringSoon": fRemainingSeconds <= F_EXPIRY_WARNING_LEAD_SECONDS,
    }


async def _fnCommitSessionExpiry(
    appState, dictStore, sName, recordOwner, sSessionId,
):
    """Commit one expired session: orphan its owner, or revoke it bare."""
    if not _fbOwnerRecordIsOwnedByActiveSession(recordOwner, sSessionId):
        browserSession.fbRevokeSessionById(dictStore, sSessionId)
        return

    def fbStillOwnedByThisSession(recordAny):
        return _fbOwnerRecordIsOwnedByActiveSession(recordAny, sSessionId)

    await fnOrphanSession(
        appState, sName, fbStillWarranted=fbStillOwnedByThisSession,
    )


def _fbOwnerRecordIsOwnedByActiveSession(recordOwner, sSessionId):
    """Return True when an ACTIVE owner record is bound to this session.

    A record bound to a DIFFERENT session (a host transfer rebound it
    after the reverse index was read) or already ORPHANED must not be
    orphaned on this session's behalf: the first belongs to a
    successor, and the second already revoked this credential in its
    own commit.
    """
    if recordOwner is None:
        return False
    if recordOwner.sState != containerOwnership.S_OWNER_STATE_ACTIVE:
        return False
    return recordOwner.sBrowserSessionId == sSessionId


def fnScheduleConnectionFencing(listConnections):
    """Close a fenced connection set without awaiting it here.

    The poison commit runs synchronously inside the held locks (design
    §3.5: nothing may interleave on the event loop between a state read
    and its commit), but closing a WebSocket is an ``await``. Scheduling
    the closes keeps both properties: the commit stays atomic, and the
    sockets still go. A failure to close is tolerated -- the per-frame
    backstop already refuses a poisoned container's frames, so the close
    is what makes the refusal visible promptly, not what makes it
    correct.
    """
    if not listConnections:
        return
    try:
        loopRunning = asyncio.get_event_loop()
    except RuntimeError:
        return
    loopRunning.create_task(_fnCloseDetachedConnections(listConnections))
