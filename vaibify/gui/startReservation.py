"""The server-owned start reservation and its durable result record.

Design §10b. Starting a container is not a request-scoped action: the
pull can outlast any HTTP timeout, the response can be lost, and a naive
cleanup can ABA-delete a newer attempt. So a start is arbitrated into a
**reservation** — an optional ``StartReservation`` on the container's
``OwnerRecord``, orthogonal to the browser-authority axis (a record may
be ``ACTIVE`` and starting, or ``ORPHANED_SESSION`` and starting) — and
the Docker work runs as a commit-guard mode-(c) durable task, which a
host transfer adopts and retags rather than waiting out.

The reservation carries only LIVE EXECUTION state: its stable id (the
compare-and-delete key), the start task's process handle and task id, and
a heartbeat. It copies no session, lease, or generation — those live on
the enclosing ``OwnerRecord``, so a transfer rotates them in one place —
and it carries NO OUTCOME: the bounded, in-memory ``dictStartResults`` is
the sole delivery authority, so there is never a second outcome record to
drift.

Two delivery paths, because success and failure authorize differently:

* **SUCCEEDED** is bound to the LIVE owner record. The current owner —
  including a ``vaibify open`` successor — polls and receives a FRESHLY
  DERIVED lease. No lease is ever stored in the result, so a transfer
  cannot leave a stale one to replay.
* **FAILED** has no owner left to authorize it (the reservation is gone
  and the flock released — the very case the record exists for), so it is
  a bounded, session-bound retrieval entitlement: the initiating browser
  session, atomically rebound if a transfer preceded the failure, may
  retrieve the SAFE ERROR and nothing else. It conveys no lease, no agent
  token, and no container authority of any kind.

This module is the transition authority for the START axis, as
``sessionLifecycle`` is for the browser-authority axis. It takes the same
two locks in the same canonical order through ``sessionLifecycle``'s
public accessors — never its own — and commits through the
``containerOwnership`` primitives while holding them; routes ask it and
never touch either layer directly.
"""

__all__ = [
    "F_HEARTBEAT_STALE_SECONDS",
    "F_START_HARD_TIMEOUT_SECONDS",
    "StartCancelledError",
    "StartTaskRecord",
    "StartReservation",
    "fsMintReservationId",
    "fbReservationHeartbeatIsStale",
    "ftBeginStart",
    "ftCancelStart",
    "ftPollStartStatus",
    "fsStartStatusPathFor",
]

import asyncio
import logging
import os
import secrets
from typing import Optional
import threading
import time
from dataclasses import dataclass, field

from vaibify.config import operationJournal
from vaibify.docker import containerManager
from . import commitCarrier
from . import containerOwnership
from . import sessionLifecycle
from . import startResultStore

logger = logging.getLogger("vaibify")

# How long without progress before the dashboard may call a start stuck.
# This never cancels anything by itself — cancellation is a distinct,
# explicit operation (§10b) — it only tells the researcher the truth.
F_HEARTBEAT_STALE_SECONDS = containerOwnership.ffReadSecondsFromEnvironment(
    "VAIBIFY_START_HEARTBEAT_STALE_SECONDS", 120.0,
)

# The absolute ceiling on one start. Deliberately generous: a cold pull
# of a multi-gigabyte image is legitimately slow, and the heartbeat
# CANNOT tell that apart from a wedge (it is stamped around whole
# blocking primitives, with no progress callback inside them), so a
# stall timeout built on it would kill healthy pulls. This is a backstop
# against holding a container's ownership for the life of the hub, not a
# stuck-start remedy -- explicit cancellation is that.
F_START_HARD_TIMEOUT_SECONDS = containerOwnership.ffReadSecondsFromEnvironment(
    "VAIBIFY_START_HARD_TIMEOUT_SECONDS", 3600.0,
)

# How long a cancel waits for the start task to finish its own
# settlement before answering "still terminating". The task remains the
# single party that settles the journal and clears the reservation.
_F_CANCEL_SETTLE_WAIT_SECONDS = 30.0

S_JOURNAL_KIND_START = "start"


class StartCancelledError(RuntimeError):
    """The start was cancelled by its owner before it completed."""


@dataclass(eq=False)
class StartTaskRecord:
    """The live execution state of one server-owned start.

    ``processDocker`` is the handle the cancel path signals; it is
    adopted as each half of the create-then-start pair launches, and a
    cancel that arrived first terminates the process the moment it is
    adopted, so the window between spawn and registration cannot leak a
    live launch. ``sJournalOperationId`` is the write-ahead record
    written BEFORE anything launched.
    """

    sStartTaskId: str
    sJournalOperationId: str
    processDocker: object = None
    sCreatedContainerId: str = ""
    bCancelRequested: bool = False
    bProcessWasSignalled: bool = False
    lockProcess: threading.Lock = field(default_factory=threading.Lock)

    def fnAdoptProcess(self, processDocker):
        """Take the live launch process, terminating it if cancel won."""
        with self.lockProcess:
            self.processDocker = processDocker
            bTerminateNow = self.bCancelRequested
        if bTerminateNow:
            self.fdictTerminateLaunch()

    def fdictTerminateLaunch(self):
        """Signal the live launch to death and confirm it exited."""
        with self.lockProcess:
            self.bCancelRequested = True
            processDocker = self.processDocker
        if processDocker is None:
            return {"bExited": True, "bTerminated": False, "bKilled": False,
                    "iReturnCode": None}
        dictOutcome = containerManager.fdictTerminateDockerProcess(
            processDocker,
        )
        if dictOutcome["bTerminated"]:
            self.bProcessWasSignalled = True
        return dictOutcome


@dataclass(eq=False)
class StartReservation:
    """One container's in-flight start — the second, orthogonal axis."""

    sReservationId: str
    recordStartTask: StartTaskRecord
    fHeartbeatMonotonic: float = field(default_factory=time.monotonic)
    fStartedMonotonic: float = field(default_factory=time.monotonic)
    # WHICH ownership this start is running under, captured when the
    # reservation was minted: whether the start established it, and the
    # lease, generation, and browser session it established. A failure
    # frees ownership only when both still hold. See
    # containerOwnership.OwnershipIdentity for why a Boolean cannot
    # answer this.
    identityOwnership: Optional["OwnershipIdentity"] = None


def fsMintReservationId():
    """Return a new reservation id — the compare-and-delete key."""
    return secrets.token_hex(16)


def fsStartStatusPathFor(sContainerName):
    """Return the canonical status-poll location for a container."""
    return f"/api/containers/{sContainerName}/start-status"


def fbReservationHeartbeatIsStale(
    reservation, fStaleSeconds=F_HEARTBEAT_STALE_SECONDS,
):
    """Return True when a start has made no progress for too long."""
    if reservation is None:
        return False
    return (
        time.monotonic() - reservation.fHeartbeatMonotonic >= fStaleSeconds
    )


# ---------------------------------------------------------------------
# Beginning a start.
# ---------------------------------------------------------------------

async def ftBeginStart(
    appState, sName, sBrowserSessionId, configProject, iPort,
    connectionDocker=None, sAcknowledgeReservationId="",
):
    """Arbitrate a start and launch it; return ``(iStatusCode, dictBody)``.

    ``202`` carries the reservation id and the status-poll location and
    NEVER a lease: the container is not running yet, so there is nothing
    a lease could authorize. A repeated request by the initiating session
    is the idempotent recovery — the same 202, the same reservation, no
    second launch. Every refusal is a 409 naming what to do next.

    Ordering (design §10b, hardened): INSPECT, then take the lifecycle
    locks, then compare-and-reserve, then launch. The already-running
    refusal has to come before the reservation, because it is not a
    failure of the start — it is a request that should never have been
    accepted, and accepting it means arbitrating ownership and then
    unwinding it, which is where a valid owner's lease used to die. The
    inspection is unlocked ON PURPOSE: no daemon call may be made while
    the hub-wide cardinality lock is held. That leaves a race, so the
    check is repeated authoritatively inside the launch, where losing
    the race is an ordinary start failure rather than a lost lease.
    """
    sRunningRefusal = _fsRefusalForAlreadyRunningContainer(
        connectionDocker, sName,
    )
    if sRunningRefusal:
        return (409, {"sName": sName, "sMessage": sRunningRefusal})
    sOutcome, dictBody, recordOwner = (
        await sessionLifecycle.ftReserveContainerForStart(
            appState, sName, sBrowserSessionId, iPort, connectionDocker,
            lambda record, identity: _fnMintReservationOnRecord(
                appState, sName, record, sBrowserSessionId, identity,
            ),
            fsRefusalForPriorOutcome=lambda: _fsRefuseUnacknowledgedFailure(
                appState, sName, sBrowserSessionId,
                sAcknowledgeReservationId,
            ),
        )
    )
    if sOutcome == sessionLifecycle.S_START_REFUSED:
        return (409, dictBody)
    if sOutcome == sessionLifecycle.S_START_ALREADY_RESERVED:
        return (202, _fdictReservationBody(
            sName, recordOwner.reservation.sReservationId, True,
        ))
    reservation = recordOwner.reservation
    await _fnLaunchStartTask(appState, sName, reservation, configProject)
    return (202, _fdictReservationBody(
        sName, reservation.sReservationId, False,
    ))


def _fnMintReservationOnRecord(
    appState, sName, recordOwner, sSessionId, identityOwnership,
):
    """Write the journal record, attach the reservation, open the result.

    Runs inside the lifecycle authority's held locks, so the write-ahead
    record exists on disk before anything can launch and the result
    record exists before any poller can ask.
    """
    sReservationId = fsMintReservationId()
    sOperationId = operationJournal.fsPrepareOperation(
        sName, S_JOURNAL_KIND_START, sName,
    )
    recordOwner.reservation = StartReservation(
        sReservationId=sReservationId,
        recordStartTask=StartTaskRecord(
            sStartTaskId=secrets.token_hex(8),
            sJournalOperationId=sOperationId,
        ),
        identityOwnership=identityOwnership,
    )
    startResultStore.fnOpenStartResult(
        appState, sReservationId, sName,
        recordOwner.sBrowserSessionId or sSessionId,
    )


def _fsRefuseUnacknowledgedFailure(
    appState, sName, sBrowserSessionId, sAcknowledgeReservationId,
):
    """Acknowledge what the caller has read, then refuse a stale relaunch."""
    startResultStore.fnAcknowledgeStartResult(
        appState, sName, sBrowserSessionId, sAcknowledgeReservationId,
    )
    return _fsUnacknowledgedFailureReason(
        appState, sName, sBrowserSessionId,
    )


def _fsRefusalForAlreadyRunningContainer(connectionDocker, sName):
    """Return why a start must be refused outright, or ''.

    Read through the Docker CONNECTION the route already holds, not the
    CLI: this runs on the request path, before any lock, and it is a
    read, so it belongs on the cached gateway rather than in a fresh
    subprocess.

    Only a POSITIVE "it is running" refuses. A daemon that did not
    answer falls through to the ordinary start -- refusing on an
    unanswered probe would make the dashboard unable to start anything
    whenever Docker is briefly busy -- and the authoritative check still
    runs inside the launch, where losing the race is an ordinary start
    failure rather than a lost lease.
    """
    if connectionDocker is None:
        return ""
    try:
        listRunning = connectionDocker.flistGetRunningContainers()
    except Exception:  # noqa: BLE001 — an unanswered probe is not a refusal
        return ""
    if not any(
        dictContainer.get("sName") == sName
        for dictContainer in listRunning or ()
    ):
        return ""
    return (
        f"Container '{sName}' is already running, so there is nothing to "
        "start. Open it from the dashboard, or stop it first if you mean "
        "to start a fresh one."
    )


def _fdictReservationBody(sName, sReservationId, bAlreadyStarting):
    """Return the 202 body: the reservation and where to read its outcome."""
    return {
        "sName": sName,
        "sReservationId": sReservationId,
        "sStatusPath": fsStartStatusPathFor(sName),
        "bAlreadyStarting": bAlreadyStarting,
    }


def _fdictInUseElsewhere(sName):
    """Return the 409 body for a container another session is holding."""
    return {
        "sName": sName,
        "sMessage": (
            f"Container '{sName}' is in use in another browser session."
        ),
    }


# ---------------------------------------------------------------------
# The durable start task.
# ---------------------------------------------------------------------

async def _fnLaunchStartTask(appState, sName, reservation, configProject):
    """Register the start as a mode-(c) durable task and let it run.

    Mode (c) is what makes a running start ADOPTABLE: it launches and
    finalizes under the briefly-held mutation lock but executes outside
    it, so a host transfer retags it and it keeps going, exactly as a
    pipeline run does.
    """
    recordOwner = getattr(appState, "dictContainerOwners", {}).get(sName)
    if recordOwner is None or recordOwner.reservation is not reservation:
        return
    dictLaunch = await commitCarrier.fdictLaunchDurableTask(
        appState, sName, recordOwner.sContainerId,
        _fdictLaneTupleForOwner(sName, recordOwner),
        lambda: asyncio.ensure_future(
            _fnRunStartTask(appState, sName, reservation, configProject),
        ),
    )
    if not dictLaunch["bLaunched"]:
        await _fnSettleStartFailure(appState, sName, reservation, RuntimeError(
            dictLaunch["sReason"],
        ))
        return
    _fnPublishActiveOperationId(appState, sName, reservation)


def _fdictLaneTupleForOwner(sName, recordOwner):
    """Return the browser lane tuple the owner record itself proves."""
    return {
        "sLane": "browser",
        "iOwnerGeneration": recordOwner.iOwnerGeneration,
        "sBrowserSessionId": recordOwner.sBrowserSessionId,
        "sLeaseId": recordOwner.sLeaseId,
        "sContainerName": sName,
    }


def _fnPublishActiveOperationId(appState, sName, reservation):
    """Tell the carrier which journal record this durable task owns.

    The §8 identity gate refuses a transfer over an unsettled journal
    record UNLESS it is the registered durable task's own — the adoption
    exception. Publishing the id here is what makes a still-starting
    container transferable rather than quarantined.
    """
    recordTask = getattr(appState, "dictDurableTaskRecords", {}).get(sName)
    if recordTask is None or recordTask.admission is None:
        return
    recordTask.admission.dictLiveState["sActiveExecOperationId"] = (
        reservation.recordStartTask.sJournalOperationId
    )


async def _fnRunStartTask(appState, sName, reservation, configProject):
    """Run the create-then-start pair off the loop; settle either way.

    The hard ceiling is the ONLY timer: it exists so a launch that hangs
    forever cannot hold a container's ownership for the life of the hub.
    It is generous because the heartbeat does NOT track progress — it is
    stamped around whole blocking primitives, so a legitimate
    multi-gigabyte image pull is indistinguishable from a wedge — and a
    tighter timer would kill healthy pulls. Explicit cancellation, not a
    timer, is the remedy for a stuck start.
    """
    try:
        sContainerId = await asyncio.wait_for(
            asyncio.ensure_future(asyncio.to_thread(
                _fsExecuteReservedStart, sName, reservation, configProject,
            )),
            F_START_HARD_TIMEOUT_SECONDS,
        )
    except asyncio.TimeoutError:
        reservation.recordStartTask.bCancelRequested = True
        await _fnSettleStartFailure(appState, sName, reservation, RuntimeError(
            "The start exceeded the "
            f"{int(F_START_HARD_TIMEOUT_SECONDS)}-second hard ceiling and "
            "was terminated."
        ))
        return
    except BaseException as errorStart:  # noqa: BLE001 — settled below
        await _fnSettleStartFailure(appState, sName, reservation, errorStart)
        return
    await _fnSettleStartSuccess(
        appState, sName, reservation, sContainerId, configProject,
    )


def _fsExecuteReservedStart(sName, reservation, configProject):
    """Create the labelled container, journal its id, then start it.

    Runs on a worker thread. Every step is bounded by its own primitive
    (``Popen`` plus TERM/KILL), never by a wrapper ``await`` that would
    stop waiting while the thread kept working.
    """
    recordTask = reservation.recordStartTask
    operationJournal.fnPromoteOperationToInFlight(
        sName, recordTask.sJournalOperationId, {
            "iHolderPid": os.getpid(),
            "sReservationLabel": reservation.sReservationId,
        },
    )
    _fnRefuseIfCancelled(recordTask)
    _fnClearStoppedIncarnation(sName)
    _fnStampHeartbeat(reservation)
    sContainerId = containerManager.fsCreateContainerForReservation(
        configProject, reservation.sReservationId,
        fnRegisterProcess=recordTask.fnAdoptProcess,
    )
    recordTask.sCreatedContainerId = sContainerId
    operationJournal.fnAmendInFlightHolderIdentity(
        sName, recordTask.sJournalOperationId,
        {"sDockerContainerId": sContainerId},
    )
    _fnStampHeartbeat(reservation)
    _fnRefuseIfCancelled(recordTask)
    containerManager.fnStartCreatedContainer(
        sContainerId, fnRegisterProcess=recordTask.fnAdoptProcess,
    )
    _fnStampHeartbeat(reservation)
    _fnConfirmIncarnationIsRunning(sName, sContainerId)
    return sContainerId


def _fnConfirmIncarnationIsRunning(sName, sContainerId):
    """Re-inspect the EXACT incarnation before it can be called started.

    The launch commands exiting zero is not the same as a running
    container: an image whose entrypoint exits immediately produces a
    successful ``docker create`` and ``docker start`` and a container
    that is already gone. Committing SUCCEEDED there hands the
    researcher a lease for a dead container and a dashboard that says it
    is running -- the exact class of lie the dashboard must never tell.
    So the container just created is re-inspected BY ID, not by name: a
    name lookup could answer about a different incarnation entirely.

    An unanswered daemon is not a failure. It is not evidence the
    container died, and refusing there would turn a momentarily busy
    Docker into a failed start; the ordinary liveness surfaces report
    the truth either way.
    """
    dictPresence = containerManager.fdictProbeContainerPresence(sName)
    if not dictPresence["bAnswered"]:
        return
    if containerManager.fbContainerIsRunning(sContainerId):
        return
    raise RuntimeError(
        f"Container '{sName}' was created and started, but it is not "
        "running -- its entrypoint exited immediately. Check the image's "
        "command, then start it again."
    )


def _fnRefuseIfCancelled(recordTask):
    """Abort at a step boundary when the owner asked to cancel."""
    if recordTask.bCancelRequested:
        raise StartCancelledError(
            "the start was cancelled before this step could run"
        )


def _fnStampHeartbeat(reservation):
    """Record progress so a stuck start is distinguishable from a slow one."""
    reservation.fHeartbeatMonotonic = time.monotonic()


def _fnClearStoppedIncarnation(sName):
    """Remove a stopped container of this name so a fresh one can be made.

    A restarted container skips the secret mounts (they are volume args
    fixed at creation), so vaibify always creates rather than restarts. A
    RUNNING container is refused instead: starting it again would be a
    lie, and the honest answer belongs in the result record.
    """
    dictStatus = containerManager.fdictGetContainerStatus(sName)
    if dictStatus["bRunning"]:
        raise RuntimeError(f"Container '{sName}' is already running")
    if dictStatus["bExists"]:
        containerManager.fnRemoveStopped(sName)


# ---------------------------------------------------------------------
# Settlement — the single place a reservation is cleared.
# ---------------------------------------------------------------------

async def _fnSettleStartSuccess(
    appState, sName, reservation, sContainerId, configProject,
):
    """Clear the reservation, keep the ownership, publish SUCCEEDED."""
    await asyncio.to_thread(
        _fnSettleJournalQuietly, sName,
        reservation.recordStartTask.sJournalOperationId,
    )
    async with sessionLifecycle.flockContainerMutationForAppState(
        appState, sName,
    ):
        recordOwner = getattr(appState, "dictContainerOwners", {}).get(sName)
        if recordOwner is not None and (
            recordOwner.reservation is reservation
        ):
            recordOwner.reservation = None
            recordOwner.sContainerId = sContainerId
            recordOwner.fLastSeenMonotonic = time.monotonic()
        startResultStore.fnCloseStartResult(
            appState, reservation.sReservationId,
            startResultStore.S_RESULT_SUCCEEDED,
            sContainerId=sContainerId,
        )
    if getattr(configProject, "bNeverSleep", False):
        from vaibify.config.keepAliveManager import fnStartKeepAlive
        fnStartKeepAlive(sName)


async def _fnSettleStartFailure(appState, sName, reservation, errorStart):
    """Prove the launch dead, clean up by label, then release or quarantine.

    Ordering is the whole safety argument (§10b): the process is
    confirmed exited and the labelled container conclusively gone BEFORE
    the reservation is compare-and-deleted and the flock freed. When the
    daemon's answer stays uncertain the container is NOT made claimable —
    the journal record is poisoned and the record keeps its flock, so the
    next hub sees the quarantine even though the flock died with this one.
    """
    dictTermination = await asyncio.to_thread(
        reservation.recordStartTask.fdictTerminateLaunch,
    )
    dictSettlement = await asyncio.to_thread(
        containerManager.fdictSettleReservationContainers,
        reservation.sReservationId,
        reservation.recordStartTask.bProcessWasSignalled,
    )
    await sessionLifecycle.ftSettleFailedStartOwnership(
        appState, sName, reservation.identityOwnership,
        lambda recordOwner: _fbCommitFailedStart(
            appState, sName, recordOwner, reservation, errorStart,
            dictSettlement, dictTermination,
        ),
    )


def _fbCommitFailedStart(
    appState, sName, recordOwner, reservation, errorStart, dictSettlement,
    dictTermination,
):
    """Commit one failed start under the authority's held locks.

    Returns whether the container was proven clean, which is the ONLY
    condition under which the lifecycle authority frees its flock.
    """
    sSafeError = _fsSafeStartError(errorStart)
    if not dictTermination["bExited"]:
        dictSettlement = dict(dictSettlement, bConclusive=False, sDetail=(
            "the launch process could not be confirmed exited"
        ))
    if dictSettlement["bConclusive"]:
        _fnSettleJournalQuietly(
            sName, reservation.recordStartTask.sJournalOperationId,
        )
    else:
        _fnQuarantineFailedStart(
            appState, sName, recordOwner, reservation, dictSettlement,
        )
    bReservationStillOurs = recordOwner is not None and (
        recordOwner.reservation is reservation
    )
    if bReservationStillOurs:
        # Compare-and-delete on the reservation identity: a NEWER
        # reservation on this record belongs to a later attempt, and a
        # late callback from this one must never delete it (case 25).
        recordOwner.reservation = None
    startResultStore.fnCloseStartResult(
        appState, reservation.sReservationId,
        startResultStore.S_RESULT_FAILED,
        sSafeError=_fsCombineFailureDetail(sSafeError, dictSettlement),
        bQuarantined=not dictSettlement["bConclusive"],
    )
    return bReservationStillOurs and dictSettlement["bConclusive"]


def _fnQuarantineFailedStart(
    appState, sName, recordOwner, reservation, dictSettlement,
):
    """Poison the journal record and the owner record, retaining the flock.

    Fail closed: an uncertain daemon outcome must not become a claimable
    container. The journal record survives this process, and the poison
    refuses every claim, transfer, release, and reap in it, so neither
    this hub nor the next hands the container to a second owner while a
    late create may still be landing.
    """
    try:
        operationJournal.fnMarkOperationNeedsReconciliation(
            sName, reservation.recordStartTask.sJournalOperationId,
            sNote=(
                "start settlement inconclusive: "
                f"{dictSettlement['sDetail']}"
            ),
        )
    except operationJournal.OperationJournalError as errorJournal:
        logger.warning(
            "Could not quarantine the start record for container '%s': %s",
            sName, errorJournal,
        )
    if recordOwner is None:
        return
    listFenced = containerOwnership.flistPoisonAndFenceConnections(
        recordOwner,
        containerOwnership.PoisonRecord(
            sGuardedOperationId=(
                reservation.recordStartTask.sJournalOperationId
            ),
            sContainerId=reservation.recordStartTask.sCreatedContainerId,
            sTaskHandleId=reservation.recordStartTask.sStartTaskId,
        ),
        getattr(appState, "dictSessionSockets", None),
    )
    # Scheduled rather than awaited: this commit runs synchronously
    # inside the lifecycle authority's held locks, and awaiting a socket
    # close there would let the event loop interleave another
    # transition into the middle of a settlement.
    sessionLifecycle.fnScheduleConnectionFencing(listFenced)


def _fnSettleJournalQuietly(sName, sOperationId):
    """Settle a start's journal record; report, never mask, a failure."""
    try:
        operationJournal.fnSettleOperation(sName, sOperationId)
    except operationJournal.OperationJournalError as errorJournal:
        logger.warning(
            "Could not settle the start journal record for container "
            "'%s': %s", sName, errorJournal,
        )


def _fsSafeStartError(errorStart):
    """Return a user-facing failure reason carrying no credential."""
    if isinstance(errorStart, StartCancelledError):
        return "The start was cancelled."
    sMessage = str(errorStart).strip() or errorStart.__class__.__name__
    return sMessage[:400]


def _fsCombineFailureDetail(sSafeError, dictSettlement):
    """Append the quarantine instruction to an inconclusive settlement."""
    if dictSettlement["bConclusive"]:
        return sSafeError
    return (
        f"{sSafeError} The container could not be proven clean "
        f"({dictSettlement['sDetail']}), so it is quarantined; run "
        "'vaibify reconcile' before starting it again."
    )


# ---------------------------------------------------------------------
# Cancelling a start.
# ---------------------------------------------------------------------

async def ftCancelStart(
    appState, sName, sBrowserSessionId, sReservationId="",
):
    """Cancel an in-flight start; return ``(iStatusCode, dictBody)``.

    A DISTINCT explicit operation (§10b): a host transfer adopts a
    running start and never doubles as a cancel, so "hand this to my new
    session" and "kill this" can never be the same ambiguous call. The
    signal is delivered out of band — the start task itself remains the
    single party that settles the journal, clears the reservation, and
    frees the flock, after its process is confirmed exited.
    """
    async with sessionLifecycle.flockContainerMutationForAppState(
        appState, sName,
    ):
        iCode, dictBody, reservation = _tMarkCancelRequested(
            appState, sName, sBrowserSessionId, sReservationId,
        )
    if reservation is None:
        return (iCode, dictBody)
    await asyncio.to_thread(reservation.recordStartTask.fdictTerminateLaunch)
    bSettled = await _fbAwaitStartTaskSettlement(appState, sName)
    return (200, _fdictCancelOutcome(
        appState, sName, reservation, bSettled,
    ))


def _tMarkCancelRequested(
    appState, sName, sBrowserSessionId, sReservationId,
):
    """Authorize the cancel and flag the task, under the mutation lock."""
    recordOwner = getattr(appState, "dictContainerOwners", {}).get(sName)
    reservation = getattr(recordOwner, "reservation", None)
    if reservation is None:
        return (409, {
            "sName": sName,
            "sMessage": f"No start is in progress for '{sName}'.",
        }, None)
    if recordOwner.sBrowserSessionId not in ("", sBrowserSessionId):
        return (403, {
            "sName": sName,
            "sMessage": (
                "Only the session that started this container may cancel "
                "its start."
            ),
        }, None)
    if sReservationId and sReservationId != reservation.sReservationId:
        return (409, {
            "sName": sName,
            "sMessage": (
                "That start has already finished; a newer one is in "
                "progress and a stale cancel may not touch it."
            ),
        }, None)
    reservation.recordStartTask.bCancelRequested = True
    _fnRequestJournalCancelQuietly(
        sName, reservation.recordStartTask.sJournalOperationId,
    )
    return (200, {}, reservation)


def _fnRequestJournalCancelQuietly(sName, sOperationId):
    """Mark the start's journal record CANCEL_REQUESTED, tolerating races."""
    try:
        operationJournal.fnRequestOperationCancel(sName, sOperationId)
    except operationJournal.OperationJournalError:
        # The record may already have settled or been poisoned by the
        # task's own settlement; the task remains the single settler.
        pass


async def _fbAwaitStartTaskSettlement(appState, sName):
    """Wait, bounded, for the start task to finish settling itself."""
    recordTask = getattr(appState, "dictDurableTaskRecords", {}).get(sName)
    if recordTask is None or recordTask.taskAsync is None:
        return True
    try:
        await asyncio.wait_for(
            asyncio.shield(recordTask.taskAsync),
            _F_CANCEL_SETTLE_WAIT_SECONDS,
        )
    except (asyncio.TimeoutError, asyncio.CancelledError):
        return False
    except BaseException:  # noqa: BLE001 — the task reports via its result
        return True
    return True


def _fdictCancelOutcome(appState, sName, reservation, bSettled):
    """Return the honest cancel report — never "done" for still-running."""
    recordResult = startResultStore.frecordStartResultById(
        appState, reservation.sReservationId,
    )
    return {
        "sName": sName,
        "sReservationId": reservation.sReservationId,
        "bCancelled": bSettled,
        "sState": (
            recordResult.sState if recordResult
            else startResultStore.S_RESULT_PENDING
        ),
        "sMessage": (
            "" if bSettled else
            "The start is still terminating; poll its status for the "
            "outcome."
        ),
        "bQuarantined": bool(recordResult and recordResult.bQuarantined),
    }


# ---------------------------------------------------------------------
# The two delivery paths — who may read an outcome, and what it grants.
# ---------------------------------------------------------------------

def _fsUnacknowledgedFailureReason(appState, sName, sBrowserSessionId):
    """Return why a new start is blocked by an unseen failure, or ''."""
    recordResult = startResultStore.frecordLatestResultForContainer(
        appState, sName,
    )
    if recordResult is None or recordResult.sState != (
        startResultStore.S_RESULT_FAILED
    ):
        return ""
    if recordResult.sInitiatingSessionId != sBrowserSessionId:
        return ""
    return (
        f"The previous start of '{sName}' failed and has not been "
        "acknowledged. Read its outcome from the start-status poll, then "
        "start it again as an explicit new attempt."
    )


def ftPollStartStatus(appState, sName, sBrowserSessionId):
    """Answer the canonical start-status poll: ``(iStatusCode, dictBody)``.

    Exactly one idempotent delivery path for all three states, and the
    only place a start's lease is ever handed out. Polling never consumes
    the record, because the case the record exists for IS a lost
    response.
    """
    if not sBrowserSessionId:
        return (403, {"sMessage": "Unknown browser session."})
    recordResult = startResultStore.frecordLatestResultForContainer(
        appState, sName,
    )
    if recordResult is None:
        return _tRecoverLeaseWithoutAResult(
            appState, sName, sBrowserSessionId,
        )
    if recordResult.sState == startResultStore.S_RESULT_SUCCEEDED:
        return _tDeliverSucceededResult(
            appState, sName, sBrowserSessionId, recordResult,
        )
    if recordResult.sState == startResultStore.S_RESULT_FAILED:
        return _tDeliverFailedResult(sName, sBrowserSessionId, recordResult)
    return _tDeliverPendingResult(
        appState, sName, sBrowserSessionId, recordResult,
    )


def _tRecoverLeaseWithoutAResult(appState, sName, sBrowserSessionId):
    """Answer a poll with no result record left to read.

    The result ledger is transient by design. Ownership is not: a
    session that still owns the container is entitled to its CURRENT
    lease for as long as it owns it, whether or not the outcome record
    that once carried it has expired. Without this, a researcher whose
    browser reloaded after a long start reached a 404 and a dashboard
    that could not act on a container it demonstrably owned.

    The lease comes from the live owner record — never from a stored
    copy — so a transfer that rotated it hands out the successor's, and
    a session that no longer owns the container gets nothing at all. The
    state is reported honestly as its own value: no start outcome exists
    here, and calling it SUCCEEDED would invent one.
    """
    recordOwner = getattr(appState, "dictContainerOwners", {}).get(sName)
    if recordOwner is None or recordOwner.sBrowserSessionId != (
        sBrowserSessionId
    ):
        return (404, {
            "sName": sName, "sState": "NONE",
            "sMessage": f"No start has been requested for '{sName}'.",
        })
    return (200, {
        "sName": sName,
        "sState": startResultStore.S_RESULT_OWNED,
        "sReservationId": "",
        "sContainerId": recordOwner.sContainerId,
        "sLeaseId": recordOwner.sLeaseId,
    })


def _tDeliverPendingResult(
    appState, sName, sBrowserSessionId, recordResult,
):
    """Report an in-flight start to its initiator or the current owner."""
    recordOwner = getattr(appState, "dictContainerOwners", {}).get(sName)
    if not _fbSessionMayReadProgress(
        recordOwner, sBrowserSessionId, recordResult,
    ):
        return (403, {"sMessage": _fsNotYoursMessage(sName)})
    reservation = getattr(recordOwner, "reservation", None)
    return (200, {
        "sName": sName,
        "sState": startResultStore.S_RESULT_PENDING,
        "sReservationId": recordResult.sReservationId,
        "bHeartbeatStale": fbReservationHeartbeatIsStale(reservation),
    })


def _tDeliverSucceededResult(
    appState, sName, sBrowserSessionId, recordResult,
):
    """Deliver a success — bound to the LIVE owner record, never a copy.

    The lease is derived here, from whoever owns the container NOW, so a
    ``vaibify open`` successor collects the result of a start its
    predecessor requested and the revoked predecessor cannot. If the
    ownership has since been released, the honest answer is the outcome
    with no lease at all and an instruction to claim.
    """
    recordOwner = getattr(appState, "dictContainerOwners", {}).get(sName)
    if recordOwner is not None:
        if recordOwner.sBrowserSessionId not in ("", sBrowserSessionId):
            return (403, {"sMessage": _fsNotYoursMessage(sName)})
        return (200, {
            "sName": sName,
            "sState": startResultStore.S_RESULT_SUCCEEDED,
            "sReservationId": recordResult.sReservationId,
            "sContainerId": recordOwner.sContainerId,
            "sLeaseId": recordOwner.sLeaseId,
        })
    if recordResult.sInitiatingSessionId != sBrowserSessionId:
        return (403, {"sMessage": _fsNotYoursMessage(sName)})
    return (200, {
        "sName": sName,
        "sState": startResultStore.S_RESULT_SUCCEEDED,
        "sReservationId": recordResult.sReservationId,
        "sContainerId": recordResult.sContainerId,
        "sLeaseId": "",
        "sMessage": (
            f"Container '{sName}' started, but its ownership was released "
            "before you collected the result; claim it from the dashboard."
        ),
    })


def _tDeliverFailedResult(sName, sBrowserSessionId, recordResult):
    """Deliver a failure through the session-bound entitlement ONLY.

    This path grants no container authority whatsoever — no lease, no
    container id, no token — because by now there may be no owner record
    at all, and a failure is not a reason to hand anyone a container.
    """
    if recordResult.sInitiatingSessionId != sBrowserSessionId:
        return (403, {"sMessage": _fsNotYoursMessage(sName)})
    return (200, {
        "sName": sName,
        "sState": startResultStore.S_RESULT_FAILED,
        "sReservationId": recordResult.sReservationId,
        "sError": recordResult.sSafeError,
        "bQuarantined": recordResult.bQuarantined,
    })


def _fbSessionMayReadProgress(recordOwner, sBrowserSessionId, recordResult):
    """Return True when a session may watch this start's progress."""
    if recordResult.sInitiatingSessionId == sBrowserSessionId:
        return True
    return recordOwner is not None and (
        recordOwner.sBrowserSessionId == sBrowserSessionId
    )


def _fsNotYoursMessage(sName):
    """Return the uniform refusal for another session's start result."""
    return (
        f"The start of '{sName}' belongs to another browser session."
    )
