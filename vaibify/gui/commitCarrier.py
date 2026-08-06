"""The commit-guard carrier: three modes, one shielded supervisor.

Design §8. Every irreversible container-scoped mutation commits through
one of three carrier modes, each of which writes a two-phase
write-ahead record through :mod:`vaibify.config.operationJournal` and
checks its lane tuple at the linearization point:

- **Mode (a) synchronous-commit** (:func:`fdictCommitSynchronousMutation`)
  — no ``await`` between the final lane-tuple/identity check and the
  effect closure.
- **Mode (b) lock-held async** (:func:`fdictRunLockHeldMutation`) — the
  per-container mutation lock (the drain, design §3.5) is held for the
  WORKER's whole life by a shielded supervisor task, never released by
  the requesting coroutine's ``finally``: cancelling the awaiting route
  does not stop a ``to_thread`` worker (the hazard
  ``buildRoutes._fnExecuteBuild`` documents), so the lock is tied to
  worker-thread termination, and a transfer observes it held until the
  effect truly finishes.
- **Mode (c) registered durable task** (:func:`fdictLaunchDurableTask`)
  — launches and finalizes under the briefly-held lock but EXECUTES
  OUTSIDE it, storing a stable task id; completion reacquires the lock
  and compare-matches the stable id before cleanup, so a superseded
  completion cannot clobber a successor. A host transfer (slice 5)
  retags the mutable record under the same brief lock.

Cancellation is out-of-band (design §8): a mode-(b) cancel signals the
live supervisor WITHOUT acquiring the drain (acquiring it would
deadlock on the very worker it must terminate); the supervisor alone
settles the journal, publishes the outcome, compare-and-deletes its
registry entry, and releases the lock. A mode-(c) cancel competes for
the brief lock, atomically marks the task ``CANCEL_REQUESTED``,
terminates the worker outside the lock, then reacquires and
compare-matches the stable id before cleanup.

Worker-side bounded termination is per primitive, never a wrapper
``asyncio.wait_for`` around ``to_thread`` (which only stops awaiting
while the thread keeps writing): Docker-backed workers inherit the
client's socket deadline
(``dockerConnection.I_DOCKER_CLIENT_TIMEOUT_SECONDS``); subprocess
workers use the parent-gated helper
(:func:`fdictLaunchGatedHelperProcess`), whose child blocks on a gate
until its identity is journaled and exits without acting if the parent
dies first.

Shutdown is ordered (design §8): the drain hook registered by
``appFactory`` stops admitting new guarded mutations, drains the
supervisors bounded, and only then may the flock-release hook free
locks — skipping any container whose worker is still live
(:func:`fsetNamesWithLiveMutationWork`), because a worker that can
still commit must never lose its exclusivity underneath it.
"""

__all__ = [
    "F_SHUTDOWN_DRAIN_TIMEOUT_SECONDS",
    "CommitRefusedError",
    "MutationSupervisor",
    "DurableTaskRecord",
    "fdictCreateMutationSupervisorRegistry",
    "fdictCreateDurableTaskRegistry",
    "fdictBuildLaneTupleFromRequest",
    "fdictBuildLaneTupleFromWebSocket",
    "fbLaneTupleStillCurrent",
    "ftupleOpenRequestAdmission",
    "fnCloseRequestAdmission",
    "ftupleOpenEstablishingAdmission",
    "fdictCommitSynchronousMutation",
    "fdictRunLockHeldMutation",
    "fdictRequestLockHeldCancel",
    "fdictLaunchDurableTask",
    "fdictRequestDurableTaskCancel",
    "fdictLaunchGatedHelperProcess",
    "fnCloseNewMutationAdmissions",
    "fdictDrainMutationSupervisors",
    "fsetNamesWithLiveMutationWork",
    "fbContainerHasLiveMutationWork",
    "fsDescribeLiveMutationWork",
]

import asyncio
import inspect
import logging
import os
import secrets
from typing import Optional
import subprocess
import sys
from dataclasses import dataclass, field
import threading

from vaibify.config import operationJournal
from vaibify.config.mutationAdmission import (
    S_ADMISSION_MODE_DURABLE_TASK,
    S_ADMISSION_MODE_ESTABLISHING,
    S_ADMISSION_MODE_LOCK_HELD,
    S_ADMISSION_MODE_REQUEST,
    S_ADMISSION_MODE_SYNCHRONOUS,
    _fadmissionMintForCommitCarrier,
    fnAssertOperationAdmittedByIdentity,
    fnDeactivateAdmission,
    fnResetEnforcedLane,
    ftokenActivateAdmission,
    ftokenMarkEnforcedLane,
)
from . import browserSession
from . import containerOwnership
from . import sessionLifecycle

logger = logging.getLogger("vaibify")

F_SHUTDOWN_DRAIN_TIMEOUT_SECONDS = (
    containerOwnership.ffReadSecondsFromEnvironment(
        "VAIBIFY_MUTATION_DRAIN_TIMEOUT_SECONDS", 30.0,
    )
)

S_LANE_BROWSER = "browser"
S_LANE_AGENT = "agent"


class CommitRefusedError(PermissionError):
    """The carrier refused to commit: stale lane, closed hub, or busy."""


def fdictCreateMutationSupervisorRegistry():
    """Return the empty ``{sSupervisorId: MutationSupervisor}`` map."""
    return {}


def fdictCreateDurableTaskRegistry():
    """Return the empty ``{sContainerName: DurableTaskRecord}`` map."""
    return {}


def _fdictSupervisorRegistry(appState):
    """Return the app's supervisor registry, creating it when absent."""
    dictRegistry = getattr(appState, "dictMutationSupervisors", None)
    if dictRegistry is None:
        dictRegistry = fdictCreateMutationSupervisorRegistry()
        appState.dictMutationSupervisors = dictRegistry
    return dictRegistry


def _fdictDurableTaskRegistry(appState):
    """Return the app's durable-task registry, creating it when absent."""
    dictRegistry = getattr(appState, "dictDurableTaskRecords", None)
    if dictRegistry is None:
        dictRegistry = fdictCreateDurableTaskRegistry()
        appState.dictDurableTaskRecords = dictRegistry
    return dictRegistry


def _fnAssertAdmissionsOpen(appState):
    """Refuse a new guarded mutation once shutdown has begun."""
    if getattr(appState, "bMutationAdmissionsClosed", False):
        raise CommitRefusedError(
            "The hub is shutting down and admits no new guarded "
            "mutations; retry against the next hub."
        )


# ---------------------------------------------------------------------
# Lane tuples (design §8) and their commit-time revalidation.
# ---------------------------------------------------------------------

def _ftResolveOwnerByContainerId(dictContainerOwners, sContainerId):
    """Return ``(sName, recordOwner)`` for a Docker id, or (None, None)."""
    for sName, recordOwner in dictContainerOwners.items():
        if recordOwner.sContainerId == sContainerId or sName == sContainerId:
            return (sName, recordOwner)
    return (None, None)


def fdictBuildLaneTupleFromRequest(appState, sContainerId, request):
    """Build the browser or agent lane tuple for an HTTP request.

    Browser: ``{iOwnerGeneration, sBrowserSessionId, sLeaseId}``; agent:
    ``{iOwnerGeneration, sAgentToken, sContainerName}`` (design §8).
    Returns ``None`` when the request cannot be bound to the container's
    owner record — the caller must then refuse to mint an admission.
    """
    dictContainerOwners = getattr(appState, "dictContainerOwners", {}) or {}
    sName, recordOwner = _ftResolveOwnerByContainerId(
        dictContainerOwners, sContainerId,
    )
    if recordOwner is None:
        return None
    sAgentToken = request.headers.get("x-vaibify-session", "")
    if sAgentToken:
        if recordOwner.sAgentToken != sAgentToken:
            return None
        return _fdictAgentLaneTuple(sName, recordOwner)
    dictBrowserSessions = getattr(appState, "dictBrowserSessions", {}) or {}
    sBrowserSessionId = browserSession.fsSessionIdForCredential(
        dictBrowserSessions, request.headers.get("x-session-token", ""),
    )
    sLeaseId = request.headers.get("x-vaibify-lease", "")
    return _fdictBrowserLaneTuple(
        sName, recordOwner, sBrowserSessionId, sLeaseId,
    )


def fdictBuildLaneTupleFromWebSocket(appState, sName, connection):
    """Build the lane tuple for an authorized pipeline WebSocket.

    Mirrors the credential sources of ``webSocketAuthorization``: the
    browser lane presents ``sToken``/``sLeaseId`` query parameters; the
    agent lane presents its per-container token header.
    """
    dictContainerOwners = getattr(appState, "dictContainerOwners", {}) or {}
    recordOwner = dictContainerOwners.get(sName)
    if recordOwner is None:
        return None
    sAgentToken = connection.headers.get("x-vaibify-session", "")
    if sAgentToken and recordOwner.sAgentToken == sAgentToken:
        return _fdictAgentLaneTuple(sName, recordOwner)
    dictBrowserSessions = getattr(appState, "dictBrowserSessions", {}) or {}
    sBrowserSessionId = browserSession.fsSessionIdForCredential(
        dictBrowserSessions, connection.query_params.get("sToken", ""),
    )
    return _fdictBrowserLaneTuple(
        sName, recordOwner, sBrowserSessionId,
        connection.query_params.get("sLeaseId", ""),
    )


def _fdictAgentLaneTuple(sName, recordOwner):
    """Return the agent lane tuple for an owner record."""
    return {
        "sLane": S_LANE_AGENT,
        "iOwnerGeneration": recordOwner.iOwnerGeneration,
        "sAgentToken": recordOwner.sAgentToken,
        "sContainerName": sName,
    }


def _fdictBrowserLaneTuple(sName, recordOwner, sBrowserSessionId, sLeaseId):
    """Return the browser lane tuple, or None for an unbindable caller.

    A transitional unbound owner record (``sBrowserSessionId == ''``)
    is bound on the matching lease value alone, the same allowance the
    claim-reclaim and release paths make.
    """
    if not sLeaseId or recordOwner.sLeaseId != sLeaseId:
        return None
    if recordOwner.sBrowserSessionId not in ("", sBrowserSessionId):
        return None
    return {
        "sLane": S_LANE_BROWSER,
        "iOwnerGeneration": recordOwner.iOwnerGeneration,
        "sBrowserSessionId": sBrowserSessionId,
        "sLeaseId": sLeaseId,
        "sContainerName": sName,
    }


def fbLaneTupleStillCurrent(appState, dictLaneTuple):
    """Return True while a lane tuple still matches the live owner record.

    The commit-time linearization check: the owner record must still
    exist, carry the SAME generation, and still bind the same principal
    (session-bound lease, or per-container agent token). A transfer
    that rotated the lease or bumped the generation makes every
    pre-transfer ordinary mutation fail without committing.
    """
    if not dictLaneTuple:
        return False
    dictContainerOwners = getattr(appState, "dictContainerOwners", {}) or {}
    recordOwner = dictContainerOwners.get(dictLaneTuple["sContainerName"])
    if recordOwner is None:
        return False
    if getattr(recordOwner, "poison", None) is not None:
        # A poisoned owner record (host force-abandon, design §2.1)
        # admits NO further mutation on any lane — checked here so all
        # three carrier modes and the funnel revalidator refuse at once.
        return False
    if recordOwner.iOwnerGeneration != dictLaneTuple["iOwnerGeneration"]:
        return False
    if dictLaneTuple["sLane"] == S_LANE_AGENT:
        return recordOwner.sAgentToken == dictLaneTuple["sAgentToken"]
    if recordOwner.sLeaseId != dictLaneTuple["sLeaseId"]:
        return False
    return recordOwner.sBrowserSessionId in (
        "", dictLaneTuple["sBrowserSessionId"],
    )


# ---------------------------------------------------------------------
# Ambient request admissions, opened by routeScope.ContainerAwareRoute.
# ---------------------------------------------------------------------

def ftokenMarkEnforcedRequestLane():
    """Mark the request context as an enforced lane (no admission)."""
    return ftokenMarkEnforcedLane()


def fnResetEnforcedRequestLane(token):
    """Restore the enforced-lane mark at the end of the request."""
    fnResetEnforcedLane(token)


def ftupleOpenRequestAdmission(appState, dictScope, request):
    """Open the per-request admission for an authorized container route.

    Returns ``(tokenLane, tokenAdmission)``; ``tokenAdmission`` is
    ``None`` when the authorized request cannot be bound to a lane
    tuple, in which case the request runs marked-but-unadmitted and any
    container write it attempts is refused at the funnel (fail closed).
    """
    tokenLane = ftokenMarkEnforcedLane()
    sContainerId = request.path_params.get(
        dictScope.get("sTargetParam") or "", "",
    )
    dictLaneTuple = fdictBuildLaneTupleFromRequest(
        appState, sContainerId, request,
    )
    if dictLaneTuple is None:
        return (tokenLane, None)
    dictContainerOwners = getattr(appState, "dictContainerOwners", {}) or {}
    recordOwner = dictContainerOwners.get(dictLaneTuple["sContainerName"])
    admission = _fadmissionMintForCommitCarrier(
        dictLaneTuple["sContainerName"],
        recordOwner.sContainerId or sContainerId,
        S_ADMISSION_MODE_REQUEST,
        dictLaneTuple=dictLaneTuple,
        fnRevalidate=lambda: fbLaneTupleStillCurrent(
            appState, dictLaneTuple,
        ),
    )
    return (tokenLane, ftokenActivateAdmission(admission))


def fnCloseRequestAdmission(tTokens):
    """Close the request admission opened by the route class."""
    tokenLane, tokenAdmission = tTokens
    if tokenAdmission is not None:
        fnDeactivateAdmission(tokenAdmission)
    fnResetEnforcedLane(tokenLane)


def ftupleOpenEstablishingAdmission(sContainerName, sContainerId):
    """Open the owner-establishing admission for the connect handler.

    Connect arbitrates ownership itself (``S_SCOPE_OWNER_ESTABLISHING``
    is outside the lease-enforced set), and on a viewer the owner
    record is created DURING the handler, so this admission carries no
    commit-time revalidator: its authority is the arbitration at entry.
    The slice-5 transfer generation check will tighten it.
    """
    tokenLane = ftokenMarkEnforcedLane()
    admission = _fadmissionMintForCommitCarrier(
        sContainerName, sContainerId, S_ADMISSION_MODE_ESTABLISHING,
    )
    return (tokenLane, ftokenActivateAdmission(admission))


# ---------------------------------------------------------------------
# Mode (a): synchronous-commit.
# ---------------------------------------------------------------------

def fdictCommitSynchronousMutation(
    appState, sName, sContainerId, dictLaneTuple, sOperationKind,
    sTarget, fnEffect, dictHolderIdentity,
):
    """Commit one mutation with no ``await`` between check and effect.

    Journals ``PREPARED`` before anything runs, promotes to
    ``IN_FLIGHT`` with the holder identity, identity-gates against the
    record set, re-checks the lane tuple, and only then runs
    ``fnEffect()`` — all synchronously on the calling thread. On
    success the record settles; if the effect raises, the record is
    left ``IN_FLIGHT`` so the kind's probe (e.g. the file-write hash
    postcondition) can prove later whether the effect landed.
    """
    _fnAssertAdmissionsOpen(appState)
    if not fbLaneTupleStillCurrent(appState, dictLaneTuple):
        raise CommitRefusedError(
            f"Refusing to commit to container '{sName}': the presented "
            "lane tuple no longer matches the live owner record."
        )
    sOperationId = operationJournal.fsPrepareOperation(
        sName, sOperationKind, sTarget,
    )
    operationJournal.fnPromoteOperationToInFlight(
        sName, sOperationId, dictHolderIdentity,
    )
    fnAssertOperationAdmittedByIdentity(
        sName, sOperationId, dictHolderIdentity,
    )
    if not fbLaneTupleStillCurrent(appState, dictLaneTuple):
        raise CommitRefusedError(
            f"Refusing to commit to container '{sName}': the owner "
            "record changed between admission and the commit point."
        )
    resultEffect = _fRunEffectAdmitted(
        sName, sContainerId, S_ADMISSION_MODE_SYNCHRONOUS, fnEffect,
    )
    bJournalSettled = _fbSettleQuietly(sName, sOperationId)
    return {
        "bCommitted": True,
        "sOperationId": sOperationId,
        "bJournalSettled": bJournalSettled,
        "result": resultEffect,
    }


def _fRunEffectAdmitted(sName, sContainerId, sMode, fnEffect):
    """Run an effect closure under a freshly activated admission."""
    admission = _fadmissionMintForCommitCarrier(sName, sContainerId, sMode)
    tokenAdmission = ftokenActivateAdmission(admission)
    try:
        return fnEffect()
    finally:
        fnDeactivateAdmission(tokenAdmission)


def _fbSettleQuietly(sName, sOperationId):
    """Settle a record; report (never mask) a poisoned/failed settle."""
    try:
        operationJournal.fnSettleOperation(sName, sOperationId)
        return True
    except operationJournal.OperationJournalError as error:
        logger.warning(
            "Committed effect for container '%s' but could not settle "
            "journal record %s: %s", sName, sOperationId, error,
        )
        return False


# ---------------------------------------------------------------------
# Mode (b): lock-held async with a shielded supervisor.
# ---------------------------------------------------------------------

@dataclass(eq=False)
class MutationSupervisor:
    """One live lock-held mutation: the lock's sole releasing party."""

    sSupervisorId: str
    sName: str
    sContainerId: str
    dictLaneTuple: dict
    fnTerminateWorker: object = None
    sOperationId: str = ""
    # What the lock holder is DOING, recorded so a refusal can say so.
    # A bare locked asyncio.Lock cannot explain itself, which is why a
    # busy transfer used to answer "a guarded operation holds its
    # mutation drain" -- true, unactionable, and identical whether the
    # holder was a two-second write or a half-hour rebuild.
    sOperationKind: str = ""
    sTarget: str = ""
    taskSupervisor: object = None
    eventCancelRequested: threading.Event = field(
        default_factory=threading.Event,
    )
    dictOutcome: dict = field(default_factory=dict)


async def fdictRunLockHeldMutation(
    appState, sName, sContainerId, dictLaneTuple, sOperationKind,
    sTarget, fnWorker, dictHolderIdentity=None, fnTerminateWorker=None,
):
    """Run a worker under the drain, surviving requester cancellation.

    The requesting coroutine awaits an ``asyncio.shield`` of the
    supervisor task; cancelling the request stops the awaiting, never
    the supervisor, which holds the per-container mutation lock until
    the worker thread actually terminates (case 16b). The supervisor
    registry on ``app.state`` keeps a strong reference so the shielded
    task can neither be garbage-collected nor leak an unretrieved
    exception into the loop.

    ``fnWorker(supervisor)`` runs in a worker thread and must own its
    bounded termination (Docker socket deadline, subprocess TERM/KILL);
    ``fnTerminateWorker`` is the out-of-band cancel hook the worker's
    producer registers.

    A coroutine worker is refused HERE, before a supervisor exists and
    before anything is journaled. The thread cannot await one, so the
    work would never run -- but rejecting it downstream, after the
    journal record is in flight, sends a plain programming error
    through the failed-worker settlement and marks the container as
    NEEDING RECONCILIATION for work whose body never executed. A
    researcher would be told to run 'vaibify reconcile' because someone
    wrote ``async def``.
    """
    _fnAssertWorkerIsNotACoroutineFunction(fnWorker)
    _fnAssertAdmissionsOpen(appState)
    supervisor = MutationSupervisor(
        sSupervisorId=secrets.token_hex(8), sName=sName,
        sContainerId=sContainerId, dictLaneTuple=dict(dictLaneTuple or {}),
        fnTerminateWorker=fnTerminateWorker,
        sOperationKind=sOperationKind, sTarget=sTarget,
    )
    dictRegistry = _fdictSupervisorRegistry(appState)
    taskSupervisor = asyncio.get_running_loop().create_task(
        _fdictSuperviseLockHeldWorker(
            appState, supervisor, sOperationKind, sTarget, fnWorker,
            dictHolderIdentity,
        ),
    )
    supervisor.taskSupervisor = taskSupervisor
    dictRegistry[supervisor.sSupervisorId] = supervisor
    taskSupervisor.add_done_callback(
        _fnBuildSupervisorEviction(dictRegistry, supervisor),
    )
    return await asyncio.shield(taskSupervisor)


def _fnBuildSupervisorEviction(dictRegistry, supervisor):
    """Return the done-callback that compare-and-deletes the entry.

    Also consumes the task's exception: an abandoned (request-
    cancelled) supervisor whose worker failed must not crash the event
    loop with an unretrieved-exception warning.
    """
    def fnEvictOnDone(taskDone):
        if dictRegistry.get(supervisor.sSupervisorId) is supervisor:
            dictRegistry.pop(supervisor.sSupervisorId, None)
        if taskDone.cancelled():
            return
        errorUnretrieved = taskDone.exception()
        if errorUnretrieved is not None:
            logger.warning(
                "Lock-held mutation supervisor for container '%s' "
                "failed: %s", supervisor.sName, errorUnretrieved,
            )
    return fnEvictOnDone


async def _fdictSuperviseLockHeldWorker(
    appState, supervisor, sOperationKind, sTarget, fnWorker,
    dictHolderIdentity,
):
    """Hold the drain for the worker's whole life; settle; publish."""
    lockMutation = sessionLifecycle.flockContainerMutationForAppState(
        appState, supervisor.sName,
    )
    async with lockMutation:
        if not fbLaneTupleStillCurrent(appState, supervisor.dictLaneTuple):
            raise CommitRefusedError(
                f"Refusing the lock-held mutation on container "
                f"'{supervisor.sName}': the lane tuple went stale while "
                "waiting for the drain."
            )
        supervisor.sOperationId = operationJournal.fsPrepareOperation(
            supervisor.sName, sOperationKind, sTarget,
        )
        dictIdentity = dictHolderIdentity or {
            "iHolderPid": os.getpid(),
            "iHolderProcessGroup": os.getpgrp(),
        }
        operationJournal.fnPromoteOperationToInFlight(
            supervisor.sName, supervisor.sOperationId, dictIdentity,
        )
        fnAssertOperationAdmittedByIdentity(
            supervisor.sName, supervisor.sOperationId, dictIdentity,
        )
        return await _fdictRunAndSettleWorker(supervisor, fnWorker)


async def _fdictRunAndSettleWorker(supervisor, fnWorker):
    """Run the worker admitted in its thread; settle by its outcome.

    The admission is activated in the supervisor's context immediately
    before ``asyncio.to_thread`` (which copies the context), so the
    worker's container writes pass the funnel while the request that
    spawned it may already be gone.
    """
    admission = _fadmissionMintForCommitCarrier(
        supervisor.sName, supervisor.sContainerId,
        S_ADMISSION_MODE_LOCK_HELD,
        dictLaneTuple=supervisor.dictLaneTuple,
    )
    tokenLane = ftokenMarkEnforcedLane()
    tokenAdmission = ftokenActivateAdmission(admission)
    try:
        resultWorker = await asyncio.to_thread(
            _fnCallWorkerSynchronously, fnWorker, supervisor,
        )
    except BaseException as errorWorker:
        _fnSettleAfterFailedWorker(supervisor, errorWorker)
        raise
    finally:
        fnDeactivateAdmission(tokenAdmission)
        fnResetEnforcedLane(tokenLane)
    bJournalSettled = _fbSettleQuietly(
        supervisor.sName, supervisor.sOperationId,
    )
    supervisor.dictOutcome = {
        "bCommitted": True,
        "bCancelRequested": supervisor.eventCancelRequested.is_set(),
        "sOperationId": supervisor.sOperationId,
        "bJournalSettled": bJournalSettled,
        "result": resultWorker,
    }
    return supervisor.dictOutcome


def _fnAssertWorkerIsNotACoroutineFunction(fnWorker):
    """Refuse a declared coroutine worker before anything is committed."""
    if inspect.iscoroutinefunction(fnWorker):
        raise TypeError(
            f"{getattr(fnWorker, '__name__', fnWorker)!r} is a "
            "coroutine function, and the commit carrier runs workers in "
            "a thread: it would never execute. Pass a synchronous "
            "worker."
        )


def _fnCallWorkerSynchronously(fnWorker, supervisor):
    """Call a worker in the carrier's thread, refusing a coroutine.

    The carrier runs workers with ``asyncio.to_thread``, so an
    ``async def`` would be CALLED here, hand back a coroutine object
    nobody awaits, and return at once: the work never happens, the lock
    is released immediately, and the only symptom is a
    "coroutine ... was never awaited" warning that pytest does not fail
    on. A transfer test written that way once asserted a busy container
    refused a hand-over while no mutation was live at all.

    Checked twice, because the two checks catch different things and
    belong at different points. The declaration check also runs at the
    public entrance, before anything is journaled, so an ``async def``
    never becomes a quarantine; it is repeated here because this
    function is reachable directly. The AWAITABLE RESULT check can only
    run after the call, and must: it catches the shapes a declaration
    check cannot see -- a lambda returning a coroutine, a callable
    object whose ``__call__`` is async, a sync wrapper that forgot to
    await -- and by then the worker may already have had effects, which
    is exactly why that case still settles through the failure path.
    """
    _fnAssertWorkerIsNotACoroutineFunction(fnWorker)
    resultWorker = fnWorker(supervisor)
    if inspect.isawaitable(resultWorker):
        # A custom awaitable need not have close(); calling it blindly
        # would raise AttributeError and hide the real diagnosis.
        fnClose = getattr(resultWorker, "close", None)
        if callable(fnClose):
            fnClose()
        raise TypeError(
            f"{getattr(fnWorker, '__name__', fnWorker)!r} returned an "
            "awaitable, which the carrier's thread cannot await: the "
            "work would never run. Pass a synchronous worker."
        )
    return resultWorker


def _fnSettleAfterFailedWorker(supervisor, errorWorker):
    """Record the honest journal state for a worker that raised.

    A cancelled worker whose termination is unproven, or a failed
    worker whose effect state is unknown, must not silently settle:
    the record is poisoned so the container quarantines until the
    probe or a reconciliation proves what happened.
    """
    supervisor.dictOutcome = {
        "bCommitted": False,
        "bCancelRequested": supervisor.eventCancelRequested.is_set(),
        "sOperationId": supervisor.sOperationId,
        "sError": str(errorWorker),
    }
    try:
        operationJournal.fnMarkOperationNeedsReconciliation(
            supervisor.sName, supervisor.sOperationId,
            sNote=f"worker failed before settling: {errorWorker}",
        )
    except operationJournal.OperationJournalError as errorJournal:
        logger.warning(
            "Could not poison journal record %s for container '%s': %s",
            supervisor.sOperationId, supervisor.sName, errorJournal,
        )


def fdictRequestLockHeldCancel(appState, sName, dictLaneTuple):
    """Cancel live mode-(b) work out-of-band, never taking the drain.

    Authenticated against the CURRENT owner tuple; sets the cancel flag
    on each live supervisor for the container, marks its journal record
    ``CANCEL_REQUESTED``, and invokes the producer's terminate hook.
    The supervisor itself remains the single party that settles the
    journal and releases the lock, after the worker is truly dead.
    """
    if not fbLaneTupleStillCurrent(appState, dictLaneTuple):
        return {
            "bCancelSignalled": False,
            "sReason": "the presented lane tuple does not match the "
                       "container's current owner",
        }
    listSignalled = []
    for supervisor in list(_fdictSupervisorRegistry(appState).values()):
        if supervisor.sName != sName:
            continue
        if supervisor.taskSupervisor and supervisor.taskSupervisor.done():
            continue
        supervisor.eventCancelRequested.set()
        _fnMarkSupervisorCancelRequested(supervisor)
        if supervisor.fnTerminateWorker is not None:
            supervisor.fnTerminateWorker()
        listSignalled.append(supervisor.sSupervisorId)
    return {
        "bCancelSignalled": bool(listSignalled),
        "listSupervisorIds": listSignalled,
    }


def _fnMarkSupervisorCancelRequested(supervisor):
    """Mark the supervisor's journal record CANCEL_REQUESTED, if any."""
    if not supervisor.sOperationId:
        return
    try:
        operationJournal.fnRequestOperationCancel(
            supervisor.sName, supervisor.sOperationId,
        )
    except operationJournal.OperationJournalError as error:
        logger.warning(
            "Could not mark journal record %s CANCEL_REQUESTED for "
            "container '%s': %s",
            supervisor.sOperationId, supervisor.sName, error,
        )


# ---------------------------------------------------------------------
# Mode (c): registered durable task.
# ---------------------------------------------------------------------

@dataclass(eq=False)
class DurableTaskRecord:
    """One registered durable task, retaggable by a host transfer.

    ``iOwnerGeneration`` is MUTABLE on this record (design §2.3): a
    slice-5 transfer retags it in place under the brief lock, and the
    admission's revalidator reads it live, so the running task keeps
    committing under the successor generation instead of dying with
    the old one.
    """

    sTaskId: str
    sName: str
    sContainerId: str
    iOwnerGeneration: int
    taskAsync: object
    admission: Optional["MutationAdmission"]
    sState: str = "running"


async def fdictLaunchDurableTask(
    appState, sName, sContainerId, dictLaneTuple, fnStartTask,
):
    """Launch a durable task under the briefly-held mutation lock.

    ``fnStartTask()`` is invoked with the durable admission active in
    the current context (so the task it creates inherits it) and must
    return the started ``asyncio.Task``. The task executes OUTSIDE the
    lock; a second durable launch for the same container is refused
    while one is live; completion finalizes under the reacquired lock
    with a compare-match on the stable task id.
    """
    _fnAssertAdmissionsOpen(appState)
    lockMutation = sessionLifecycle.flockContainerMutationForAppState(
        appState, sName,
    )
    async with lockMutation:
        dictRegistry = _fdictDurableTaskRegistry(appState)
        recordLive = dictRegistry.get(sName)
        if recordLive is not None and not recordLive.taskAsync.done():
            return {
                "bLaunched": False,
                "sReason": "a durable task is already live in this "
                           "container",
                "sLiveTaskId": recordLive.sTaskId,
            }
        if not fbLaneTupleStillCurrent(appState, dictLaneTuple):
            raise CommitRefusedError(
                f"Refusing the durable-task launch on container "
                f"'{sName}': the lane tuple no longer matches the live "
                "owner record."
            )
        recordTask = _frecordStartDurableTask(
            appState, sName, sContainerId, dictLaneTuple, fnStartTask,
        )
        dictRegistry[sName] = recordTask
    return {
        "bLaunched": True,
        "sTaskId": recordTask.sTaskId,
        "iOwnerGeneration": recordTask.iOwnerGeneration,
        "taskAsync": recordTask.taskAsync,
    }


def _frecordStartDurableTask(
    appState, sName, sContainerId, dictLaneTuple, fnStartTask,
):
    """Mint the durable guard, start the task inside it, register."""
    recordTask = DurableTaskRecord(
        sTaskId=secrets.token_hex(16), sName=sName,
        sContainerId=sContainerId,
        iOwnerGeneration=dictLaneTuple["iOwnerGeneration"],
        taskAsync=None, admission=None,
    )
    admission = _fadmissionMintForCommitCarrier(
        sName, sContainerId, S_ADMISSION_MODE_DURABLE_TASK,
        dictLaneTuple=dictLaneTuple,
        fnRevalidate=lambda: _fbDurableTaskStillCurrent(
            appState, recordTask,
        ),
        bDurable=True,
    )
    recordTask.admission = admission
    tokenLane = ftokenMarkEnforcedLane()
    tokenAdmission = ftokenActivateAdmission(admission)
    try:
        recordTask.taskAsync = fnStartTask()
    finally:
        fnDeactivateAdmission(tokenAdmission)
        fnResetEnforcedLane(tokenLane)
    recordTask.taskAsync.add_done_callback(
        lambda taskDone: asyncio.ensure_future(
            _fnFinalizeDurableTask(appState, recordTask),
        ),
    )
    return recordTask


def _fbDurableTaskStillCurrent(appState, recordTask):
    """Commit-time check for a durable task's container writes.

    Reads the MUTABLE record generation (not a launch-time snapshot),
    so a transfer that retags the record keeps the task admitted while
    a cancel-requested or superseded task is refused.
    """
    if recordTask.sState != "running":
        return False
    dictContainerOwners = getattr(appState, "dictContainerOwners", {}) or {}
    recordOwner = dictContainerOwners.get(recordTask.sName)
    if recordOwner is None:
        return False
    return recordOwner.iOwnerGeneration == recordTask.iOwnerGeneration


async def _fnFinalizeDurableTask(appState, recordTask):
    """Finalize under the reacquired lock, compare-matching the id."""
    lockMutation = sessionLifecycle.flockContainerMutationForAppState(
        appState, recordTask.sName,
    )
    async with lockMutation:
        dictRegistry = _fdictDurableTaskRegistry(appState)
        recordRegistered = dictRegistry.get(recordTask.sName)
        if (
            recordRegistered is recordTask
            and recordRegistered.sTaskId == recordTask.sTaskId
        ):
            dictRegistry.pop(recordTask.sName, None)
    if recordTask.taskAsync.cancelled():
        return
    errorTask = recordTask.taskAsync.exception()
    if errorTask is not None:
        logger.warning(
            "Durable task %s for container '%s' failed: %s",
            recordTask.sTaskId, recordTask.sName, errorTask,
        )


async def fdictRequestDurableTaskCancel(
    appState, sName, sTaskId, dictLaneTuple,
):
    """Refuse: a durable task has no generic cancellation.

    Python cannot interrupt a worker running in ``asyncio.to_thread``.
    Cancelling the asyncio task only stops the AWAITING of it, and the
    previous implementation then removed the registry entry -- so
    release, transfer, and the reaper stopped seeing work that was
    still running, against a container they were now free to hand to
    somebody else. A cancel that reports success while the worker keeps
    writing is worse than no cancel at all.

    So this refuses explicitly, and refuses WITHOUT touching the task:
    no ``Task.cancel()``, no registry removal, no journal mark. The
    work runs to its natural end and settles itself, and the container
    stays exclusively held until it does.

    Cancellation that CAN be honest is kept and is separate: a start
    reservation owns a killable OS process and a labelled container, so
    ``startReservation.ftCancelStart`` terminates the process and proves
    the labelled container gone; and mode-(b) work has an out-of-band
    terminate hook whose supervisor remains the single settler
    (:func:`fdictRequestLockHeldCancel`).
    """
    del appState, sName, sTaskId, dictLaneTuple
    return {
        "bCancelled": False,
        "bSupported": False,
        "sReason": (
            "A durable task cannot be cancelled: its worker runs in a "
            "thread Python cannot interrupt, so cancelling would report "
            "a stop that did not happen. Wait for it to finish, or "
            "cancel the start that launched it."
        ),
    }


# ---------------------------------------------------------------------
# Parent-gated helper spawn for subprocess-shaped workers (design §8).
# ---------------------------------------------------------------------

# The helper blocks on its stdin gate until the parent has journaled
# its real PID/process-group; a parent that dies first closes the pipe,
# the read returns without the release line, and the helper exits
# WITHOUT acting — so no transition of the two-phase write can leave an
# unidentified writer (case 41).
S_GATED_HELPER_STUB = (
    "import os, sys, subprocess\n"
    "sGateLine = sys.stdin.readline()\n"
    "if sGateLine.strip() != 'GO':\n"
    "    sys.exit(3)\n"
    "sys.exit(subprocess.call(sys.argv[1:]))\n"
)


def fdictLaunchGatedHelperProcess(
    sContainerName, sTarget, listCommand, fnPhaseCallback=None,
):
    """Two-phase spawn: PREPARED -> gated spawn -> IN_FLIGHT -> release.

    ``fnPhaseCallback(sPhase)`` (phases ``prepared``, ``spawned``,
    ``promoted``) exists so the kill-at-every-transition test (case 41)
    can hold a REAL parent process at each boundary; production callers
    leave it ``None``.
    """
    sOperationId = operationJournal.fsPrepareOperation(
        sContainerName, "helper", sTarget,
    )
    _fnInvokePhaseCallback(fnPhaseCallback, "prepared")
    processHelper = subprocess.Popen(
        [sys.executable, "-c", S_GATED_HELPER_STUB] + list(listCommand),
        stdin=subprocess.PIPE, start_new_session=True,
    )
    _fnInvokePhaseCallback(fnPhaseCallback, "spawned")
    dictIdentity = {
        "iHolderPid": processHelper.pid,
        "iHolderProcessGroup": processHelper.pid,
    }
    operationJournal.fnPromoteOperationToInFlight(
        sContainerName, sOperationId, dictIdentity,
    )
    fnAssertOperationAdmittedByIdentity(
        sContainerName, sOperationId, dictIdentity,
    )
    _fnInvokePhaseCallback(fnPhaseCallback, "promoted")
    processHelper.stdin.write(b"GO\n")
    processHelper.stdin.flush()
    return {
        "sOperationId": sOperationId,
        "processHelper": processHelper,
        "dictHolderIdentity": dictIdentity,
    }


def _fnInvokePhaseCallback(fnPhaseCallback, sPhase):
    """Invoke the test-only phase hook when one was provided."""
    if fnPhaseCallback is not None:
        fnPhaseCallback(sPhase)


# ---------------------------------------------------------------------
# Ordered shutdown and the liveness view (design §8).
# ---------------------------------------------------------------------

def fnCloseNewMutationAdmissions(appState):
    """Stop admitting new guarded mutations (shutdown step one)."""
    appState.bMutationAdmissionsClosed = True


async def fdictDrainMutationSupervisors(
    appState, fTimeoutSeconds=None,
):
    """Bounded-drain the live supervisors; report what stayed live.

    Closes admissions first so no new supervisor can start behind the
    drain. A supervisor that outlives the bound keeps running — its
    container's flock must NOT be released (the caller consults
    :func:`fsetNamesWithLiveMutationWork`), because a worker that can
    still commit must never lose its exclusivity underneath it.
    """
    fnCloseNewMutationAdmissions(appState)
    fBound = (
        fTimeoutSeconds if fTimeoutSeconds is not None
        else F_SHUTDOWN_DRAIN_TIMEOUT_SECONDS
    )
    listLiveTasks = [
        supervisor.taskSupervisor
        for supervisor in _fdictSupervisorRegistry(appState).values()
        if supervisor.taskSupervisor is not None
        and not supervisor.taskSupervisor.done()
    ]
    if listLiveTasks:
        await asyncio.wait(listLiveTasks, timeout=fBound)
    setUndrained = fsetNamesWithLiveMutationWork(appState)
    if setUndrained:
        logger.warning(
            "Shutdown drain expired with live guarded work in %s; their "
            "container flocks are retained so no other hub can claim a "
            "container a worker may still write.", sorted(setUndrained),
        )
    return {"setUndrainedNames": setUndrained}


def fsetNamesWithLiveMutationWork(appState):
    """Return container names with a live supervisor or durable task."""
    setNames = set()
    for supervisor in _fdictSupervisorRegistry(appState).values():
        if supervisor.taskSupervisor is None or (
            not supervisor.taskSupervisor.done()
        ):
            setNames.add(supervisor.sName)
    for sName, recordTask in _fdictDurableTaskRegistry(appState).items():
        if recordTask.taskAsync is None or not recordTask.taskAsync.done():
            setNames.add(sName)
    return setNames


def fsDescribeLiveMutationWork(appState, sName):
    """Return what is holding a container's mutation lock, or ``""``.

    The metadata a busy refusal needs. Registering it is the whole
    reason a refusal can be specific: an ``asyncio.Lock`` knows only
    that it is held, so a caller that consulted the lock alone could
    only ever say "busy" -- and "busy" tells a researcher nothing about
    whether to wait two seconds or abandon the attempt.

    A durable task with no supervisor entry is described generically,
    which is honest: the registry records that one is live without
    recording what it runs.
    """
    for supervisor in _fdictSupervisorRegistry(appState).values():
        if supervisor.sName != sName:
            continue
        if supervisor.taskSupervisor is not None and (
            supervisor.taskSupervisor.done()
        ):
            continue
        if supervisor.sOperationKind:
            return (
                f"{supervisor.sOperationKind} on "
                f"{supervisor.sTarget or sName}"
                if supervisor.sTarget else supervisor.sOperationKind
            )
        return "a guarded operation"
    recordTask = _fdictDurableTaskRegistry(appState).get(sName)
    if recordTask is not None and (
        recordTask.taskAsync is None or not recordTask.taskAsync.done()
    ):
        return "a long-running task"
    return ""


def fbContainerHasLiveMutationWork(appState, sName):
    """Return True while guarded work is live in the named container.

    The reaper's veto (case 32): an idle-looking owner record whose
    container still hosts a live supervisor or durable task is never
    force-released — the supervisor is the single releasing party.
    """
    return sName in fsetNamesWithLiveMutationWork(appState)
