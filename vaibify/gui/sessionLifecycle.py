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
    "fdictCreateLifecycleLockStore",
    "ftdictClaimWithCardinality",
    "fbReleaseExplicit",
]

import asyncio
import threading

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
    the session's cardinality entry. Returns the primitive's verdict
    unchanged — this slice adds the authority, not new refusal
    conditions.
    """
    dictLockStore = _fdictLockStoreForAppState(appState)
    dictContainerOwners = getattr(appState, "dictContainerOwners", {})
    dictSessionOwner = getattr(appState, "dictSessionOwner", None)
    async with _flockObtainContainerMutation(dictLockStore, sName):
        async with _flockObtainSessionCardinality(dictLockStore):
            return containerOwnership.fnReleaseOwnership(
                dictContainerOwners, sName, sLeaseId,
                sBrowserSessionId=sBrowserSessionId,
                dictSessionOwner=dictSessionOwner,
            )
