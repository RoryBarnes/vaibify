"""Single owner-of-record authority for one browser session per container.

Each running hub keeps ``app.state.dictContainerOwners``, a map from a
container name to one ``OwnerRecord``. The record is the SOLE authority
that the claim route, the connect handler, and both WebSocket gates
consult; it replaces the two unreconciled gates of the old model
(a name-keyed host flock plus a process-global ``setAllowedContainers``).

The exclusivity principal is the lease: a per-claim, server-minted
``secrets.token_urlsafe`` value returned to the claiming tab, stored in
that tab's ``sessionStorage``, and re-presented on connect and on both
WebSockets. Two tabs of the same hub cannot both own a container because
only the first claim mints a lease and a foreign claim is refused, while
a duplicate tab that copied the lease is stopped at the WebSocket gate by
``iLiveConnectionCount`` (exactly one live connection per container).

The host flock from ``containerLock`` stays underneath as the
cross-process / cross-hub layer; this module layers same-hub two-tab
exclusivity above it and never weakens the flock's recycle-proof
staleness contract.
"""

__all__ = [
    "S_OWNER_STATE_ACTIVE",
    "S_OWNER_STATE_ORPHANED_SESSION",
    "S_LANE_PIPELINE",
    "S_LANE_TERMINAL",
    "OwnerRecord",
    "PoisonRecord",
    "ConnectionRecord",
    "S_NO_PRIOR_OWNER",
    "OwnershipIdentity",
    "fidentityRecordOwnership",
    "fbOwnershipIdentityStillHolds",
    "fdictCreateOwnerRegistry",
    "fdictCreateSessionOwnerIndex",
    "fdictCreateSessionSocketIndex",
    "ffReadSecondsFromEnvironment",
    "fsMintLease",
    "fsMintAgentToken",
    "fsAgentTokenForName",
    "fbAgentTokenAuthorizesContainerId",
    "frecordOwnerAuthorizedByAgentToken",
    "fsConflictingHeldContainer",
    "ftdictClaim",
    "fbBrowserSessionOwnsLease",
    "fbReleaseWouldBePermitted",
    "fnReleaseOwnership",
    "fbSessionOwnsContainer",
    "fiOwnerGenerationForName",
    "fnIncrementLiveConnection",
    "fnDecrementLiveConnection",
    "fnDecrementLiveConnectionForRecord",
    "flistPoisonAndFenceConnections",
    "fnClearPoison",
    "fnRegisterSessionSocket",
    "fnDeregisterSessionSocket",
    "fbAgentIsLiveOnRecord",
    "fbOwnerIsReapable",
    "flistReapIdleOwnerships",
]

import logging
import os
import secrets
import time
from typing import IO, Optional
from dataclasses import dataclass, field

from vaibify.config import pidFileRegistry
from vaibify.config.containerLock import (
    ContainerLockedError,
    ContainerQuarantinedError,
    fnAcquireContainerLock,
    fnReleaseContainerLock,
)
from vaibify.config.keepAliveManager import fnStopKeepAlive

logger = logging.getLogger("vaibify")

# The browser-authority axis holds exactly two values (design §1): a
# record is ACTIVE while a live browser session owns it, and
# ORPHANED_SESSION once the last browser socket is gone past the
# reconnect window. Orphaning retains the flock, keep-alive, agent
# token, and exclusivity; only a host transfer or the safe reaper ends
# it. Start-progress and poison are future orthogonal axes, never
# additional values here.
S_OWNER_STATE_ACTIVE = "ACTIVE"
S_OWNER_STATE_ORPHANED_SESSION = "ORPHANED_SESSION"

S_LANE_PIPELINE = "pipeline"
S_LANE_TERMINAL = "terminal"


def ffReadSecondsFromEnvironment(sVariableName, fDefaultSeconds):
    """Return a seconds knob from the environment, or its default.

    A malformed value is reported and ignored rather than crashing the
    hub at import time: a lifecycle tuning knob must never be able to
    prevent startup.
    """
    sRawValue = os.environ.get(sVariableName, "")
    if not sRawValue:
        return fDefaultSeconds
    try:
        return float(sRawValue)
    except ValueError:
        logger.warning(
            "Ignoring non-numeric %s=%r; using the default %s seconds",
            sVariableName, sRawValue, fDefaultSeconds,
        )
        return fDefaultSeconds


_F_GRACE_SECONDS = ffReadSecondsFromEnvironment(
    "VAIBIFY_REAP_GRACE_SECONDS", 30.0,
)


# The recorded identity of "there was no prior owner". A start that
# CREATED a container's ownership must be able to say so, and it must be
# a value, not the absence of one: an empty string is also what an
# unclaimed record's lease looks like, so distinguishing them by
# emptiness silently conflates "I made this ownership" with "I could not
# read the ownership I found".
S_NO_PRIOR_OWNER = "<no prior owner>"


@dataclass(frozen=True)
class OwnershipIdentity:
    """Exactly which ownership an in-flight operation is running under.

    A Boolean ("did this operation create the ownership?") cannot answer
    the question that matters at settlement time, because the ownership
    an operation created can be REPLACED while the operation runs: a
    host transfer rotates the lease, the generation, and the browser
    session on the same record, and the successor's ownership is not the
    one the operation established. Comparing the recorded identity
    against the live record makes that distinguishable, so a failing
    operation frees only what it actually created and still holds.

    ``sPriorOwnerLeaseId`` is :data:`S_NO_PRIOR_OWNER` when the operation
    established the ownership itself, and the pre-existing lease
    otherwise.
    """

    sPriorOwnerLeaseId: str
    sLeaseId: str
    iOwnerGeneration: int
    sBrowserSessionId: str

    @property
    def bEstablishedTheOwnership(self):
        """True when this operation created the ownership it runs under."""
        return self.sPriorOwnerLeaseId == S_NO_PRIOR_OWNER


def fidentityRecordOwnership(recordOwner, sPriorOwnerLeaseId):
    """Capture the ownership identity an operation is about to run under."""
    return OwnershipIdentity(
        sPriorOwnerLeaseId=sPriorOwnerLeaseId,
        sLeaseId=recordOwner.sLeaseId,
        iOwnerGeneration=recordOwner.iOwnerGeneration,
        sBrowserSessionId=recordOwner.sBrowserSessionId,
    )


def fbOwnershipIdentityStillHolds(recordOwner, identityOwnership):
    """Return True when the live record is STILL the recorded ownership.

    Every field must match. The generation alone would miss a release
    and re-claim that happened to land on the same generation; the lease
    alone would miss a rebinding to a different browser session.
    """
    if recordOwner is None or identityOwnership is None:
        return False
    return (
        recordOwner.sLeaseId == identityOwnership.sLeaseId
        and recordOwner.iOwnerGeneration == identityOwnership.iOwnerGeneration
        and recordOwner.sBrowserSessionId == (
            identityOwnership.sBrowserSessionId
        )
    )


@dataclass
class PoisonRecord:
    """A force-abandoned worker's identity — the third orthogonal axis.

    Design §2.1: set ONLY by a host-lane force-abandon over the
    peer-authenticated control socket, for a wedged guarded worker that
    cannot be terminated cleanly. While set, the owner record KEEPS its
    flock and refuses every mutation, claim, transfer, release, and
    reap — exclusivity is retained, never dropped, so a zombie worker
    can never be writing a container a second owner also holds. It
    clears only when the reconciliation transaction proves the worker
    dead (recycle-proof), which then clears the poison and the durable
    journal marker last. The durable mirror is the journal record
    marked ``NEEDS_RECONCILIATION`` at force-abandon time, so the
    quarantine survives a hub crash.
    """

    sGuardedOperationId: str
    sContainerId: str = ""
    sTaskHandleId: str = ""
    fAbandonedMonotonic: float = field(default_factory=time.monotonic)


@dataclass
class OwnerRecord:
    """One browser session's ownership of a single container.

    ``fileHandleLock`` is the held host flock from ``containerLock``;
    ``iLiveConnectionCount`` counts every live WebSocket (pipeline and
    terminal alike) so a crashed owner becomes reapable;
    ``iLivePipelineConnectionCount`` counts only the pipeline lane,
    whose budget is one — a session legitimately holds several terminal
    sockets, so only a second concurrent *pipeline* socket marks a
    duplicate tab; and ``fLastSeenMonotonic`` starts the grace window
    the moment the last live connection drops.

    The ORPHANED_SESSION foundation fields (design §2.1): ``sState`` is
    the two-valued browser-authority axis; ``iOwnerGeneration`` starts at
    1 and is bumped only by a host transfer, so a stale socket or task
    carrying an old generation can be told apart from the successor;
    ``reservation`` is the orthogonal start axis (§10b);
    ``fOrphanedSinceMonotonic`` stamps the ACTIVE→ORPHANED transition (0.0
    while never orphaned) so the reap grace measures from the orphan
    moment, not the last socket; ``fLastAgentActivityMonotonic`` and
    ``iInFlightAgentRequests`` let the safe reaper see a live in-container
    agent; and ``bSocketEverExisted`` gates the zero-sockets orphan
    trigger, so a claim that never opened a socket falls to the idle
    window instead of orphaning instantly.
    """

    sLeaseId: str
    fileHandleLock: Optional[IO]
    sAgentToken: str = ""
    sContainerId: str = ""
    sBrowserSessionId: str = ""
    iLiveConnectionCount: int = 0
    iLivePipelineConnectionCount: int = 0
    fLastSeenMonotonic: float = field(default_factory=time.monotonic)
    sState: str = S_OWNER_STATE_ACTIVE
    iOwnerGeneration: int = 1
    fOrphanedSinceMonotonic: float = 0.0
    fLastAgentActivityMonotonic: float = 0.0
    iInFlightAgentRequests: int = 0
    bSocketEverExisted: bool = False
    # The optional PoisonRecord (design §2.1): None while healthy. See
    # the PoisonRecord docstring for the refusal semantics while set.
    poison: object = None
    # The optional StartReservation (design §2.1/§10b): None unless a
    # server-owned start is in flight. Start-progress is a SECOND,
    # orthogonal axis, never a third ``sState`` value — a container can
    # be ACTIVE and starting, or ORPHANED_SESSION and starting, and a
    # compound state could express neither. The reservation holds only
    # live execution state; the outcome lives in the bounded
    # ``dictStartResults``, which is its sole delivery authority.
    reservation: object = None


@dataclass(eq=False)
class ConnectionRecord:
    """One live browser WebSocket, tagged with its session and generation.

    Stored in ``app.state.dictSessionSockets`` (design §2.3) so a revoked
    session's sockets can be actively closed, and matched by the
    count-decrement path so a socket that outlived a generation rotation
    decrements nothing on the successor's counters. Identity equality
    (``eq=False``) keeps each record hashable and distinct, exactly one
    per accepted socket.
    """

    connection: object
    sBrowserSessionId: str
    iOwnerGeneration: int
    sLane: str


def fdictCreateOwnerRegistry():
    """Return a fresh, empty owner-of-record map for ``app.state``."""
    return {}


def fdictCreateSessionOwnerIndex():
    """Return the empty ``{sBrowserSessionId: sName}`` reverse index.

    The cardinality authority (design §9): one container per browser
    session. This slice only keeps the index in sync on the claim and
    release paths; refusing a second claim lands with the cardinality
    slice.
    """
    return {}


def fdictCreateSessionSocketIndex():
    """Return the empty ``{sBrowserSessionId: set[ConnectionRecord]}`` map."""
    return {}


def fsMintLease():
    """Return a new, unguessable per-claim lease token."""
    return secrets.token_urlsafe(32)


def fsMintAgentToken():
    """Return a new per-container in-container-agent credential.

    Distinct from the hub-wide session token and from every other
    container's token, so a compromised agent in one container cannot
    authenticate against another container's session.
    """
    return secrets.token_urlsafe(32)


def fsAgentTokenForName(dictContainerOwners, sName):
    """Return the per-container agent token for an owned name, or ''."""
    recordOwner = dictContainerOwners.get(sName)
    return recordOwner.sAgentToken if recordOwner is not None else ""


def fbAgentTokenAuthorizesContainerId(
    dictContainerOwners, sPresentedToken, sContainerId,
):
    """Return True when a presented agent token owns ``sContainerId``."""
    return frecordOwnerAuthorizedByAgentToken(
        dictContainerOwners, sPresentedToken, sContainerId,
    ) is not None


def frecordOwnerAuthorizedByAgentToken(
    dictContainerOwners, sPresentedToken, sContainerId,
):
    """Return the OwnerRecord a presented agent token owns, or None.

    The per-container token is the proof of which container the agent
    speaks for; it authorizes only the container whose owner record both
    minted it and serves that Docker id. An empty token or id never
    matches, so a request that names no container fails closed. The
    record itself is returned so the middleware can stamp agent
    liveness (design §7) on the same authority it authorized with.
    """
    if not sPresentedToken or not sContainerId:
        return None
    for recordOwner in dictContainerOwners.values():
        if (
            recordOwner.sAgentToken
            and recordOwner.sAgentToken == sPresentedToken
            and recordOwner.sContainerId == sContainerId
        ):
            return recordOwner
    return None


def fsConflictingHeldContainer(dictSessionOwner, sBrowserSessionId, sName):
    """Return the DIFFERENT container this session already holds, or ''.

    The cardinality read (design §9): one container per browser session,
    answered from the ``dictSessionOwner`` reverse index. A transitional
    unbound caller (empty session id) or an absent index never conflicts,
    and the session's own container is never a conflict — the idempotent
    same-container reclaim path handles that case.
    """
    if dictSessionOwner is None or not sBrowserSessionId:
        return ""
    sHeldContainerName = dictSessionOwner.get(sBrowserSessionId, "")
    if sHeldContainerName and sHeldContainerName != sName:
        return sHeldContainerName
    return ""


def ftdictClaim(
    dictContainerOwners, sName, sLeaseId, iPort, sContainerId="",
    fbPipelineRunning=None, fGraceSeconds=_F_GRACE_SECONDS,
    sBrowserSessionId="", dictSessionOwner=None, connectionDocker=None,
):
    """Arbitrate a claim and return ``(iStatusCode, dictPayload)``.

    Unowned grants a fresh lease (200), binding the record to the claiming
    browser session (``sBrowserSessionId``). A same-lease re-claim is
    idempotent (200) so a reloaded tab re-asserting its
    ``sessionStorage`` lease never self-locks. A foreign or absent lease
    is refused with 409 unless the current owner is reapable, in which
    case the dead owner is released and the claim is granted fresh.

    Cardinality (design §9) is checked first: a bound session that
    already holds a different container is refused 409 before any
    arbitration, naming the held container. Routes reach this primitive
    through :func:`sessionLifecycle.ftdictClaimWithCardinality`, whose
    hub-wide cardinality lock makes this read-check-write atomic across
    concurrent claims on different containers.
    """
    sHeldElsewhereName = fsConflictingHeldContainer(
        dictSessionOwner, sBrowserSessionId, sName,
    )
    if sHeldElsewhereName:
        return (409, _fdictCardinalityRefused(sName, sHeldElsewhereName))
    recordOwner = dictContainerOwners.get(sName)
    if recordOwner is None:
        return _ftdictClaimUnowned(
            dictContainerOwners, sName, iPort, sContainerId,
            sBrowserSessionId, dictSessionOwner, connectionDocker,
        )
    # A poisoned record refuses EVERY claim — including the owner's own
    # same-lease reclaim and the reapable take-over — because a
    # force-abandoned worker's death is unproven and exclusivity must
    # be retained until reconciliation proves it (design §2.1).
    if getattr(recordOwner, "poison", None) is not None:
        return (409, _fdictPoisonRefused(sName))
    # The same-lease reclaim stays idempotent only when the owner is
    # unbound (a shared-token, transitional claim carries '') OR the
    # presented session matches the bound owner. A lease match against a
    # bound owner by a DIFFERENT session is a copied-lease replay: it
    # must fall through to the 409 refusal below and must NOT refresh the
    # owner's liveness, or session B would revive session A's grace clock.
    if sLeaseId and recordOwner.sLeaseId == sLeaseId and (
        recordOwner.sBrowserSessionId == ""
        or recordOwner.sBrowserSessionId == sBrowserSessionId
    ):
        recordOwner.fLastSeenMonotonic = time.monotonic()
        return (200, _fdictClaimGranted(sName, recordOwner.sLeaseId))
    if _fbOwnerIsReapableNow(
        recordOwner, sName, fbPipelineRunning, fGraceSeconds,
    ):
        _fnForceReleaseOwnership(
            dictContainerOwners, sName, dictSessionOwner,
        )
        return _ftdictClaimUnowned(
            dictContainerOwners, sName, iPort, sContainerId,
            sBrowserSessionId, dictSessionOwner, connectionDocker,
        )
    return (409, _fdictClaimRefused(sName, recordOwner))


def _ftdictClaimUnowned(
    dictContainerOwners, sName, iPort, sContainerId, sBrowserSessionId="",
    dictSessionOwner=None, connectionDocker=None,
):
    """Acquire the host flock for an unowned container and mint a lease.

    A container whose operation journal is quarantined is refused even
    though its flock is free: the write-ahead record of an unsettled
    past operation outlives the process that held the flock.
    """
    try:
        fileHandleLock = fnAcquireContainerLock(sName, iPort, connectionDocker)
    except ContainerQuarantinedError as error:
        return (409, _fdictQuarantineRefused(sName, error))
    except ContainerLockedError as error:
        return (409, _fdictCrossHubRefused(sName, error))
    sLeaseId = _fnRecordNewOwner(
        dictContainerOwners, sName, fileHandleLock, sContainerId,
        sBrowserSessionId, dictSessionOwner,
    )
    return (200, _fdictClaimGranted(sName, sLeaseId))


def _fnRecordNewOwner(
    dictContainerOwners, sName, fileHandleLock, sContainerId="",
    sBrowserSessionId="", dictSessionOwner=None,
):
    """Mint a lease plus a per-container agent token and store the owner."""
    sLeaseId = fsMintLease()
    dictContainerOwners[sName] = OwnerRecord(
        sLeaseId=sLeaseId,
        fileHandleLock=fileHandleLock,
        sAgentToken=fsMintAgentToken(),
        sContainerId=sContainerId,
        sBrowserSessionId=sBrowserSessionId,
    )
    if dictSessionOwner is not None and sBrowserSessionId:
        dictSessionOwner[sBrowserSessionId] = sName
    return sLeaseId


def fbBrowserSessionOwnsLease(
    dictContainerOwners, sName, sBrowserSessionId, sLeaseId,
):
    """Return True when a browser session owns a container's lease.

    The lease is bound to the session that claimed it, so authorization on
    the browser lane requires BOTH the session id and the lease to match
    the owner record — validating "some valid session" plus "some valid
    lease" independently would let session B replay session A's copied
    lease. An empty session id or lease never matches, so a request that
    presents neither fails closed.
    """
    if not sBrowserSessionId or not sLeaseId:
        return False
    recordOwner = dictContainerOwners.get(sName)
    if recordOwner is None:
        return False
    return (
        recordOwner.sBrowserSessionId == sBrowserSessionId
        and recordOwner.sLeaseId == sLeaseId
    )


def _fdictClaimGranted(sName, sLeaseId):
    """Return the success body carrying the owner's lease."""
    return {"sName": sName, "bClaimed": True, "sLeaseId": sLeaseId}


def _fdictClaimRefused(sName, recordOwner):
    """Return the 409 body for a same-hub claim by another session.

    The owner's lease is never echoed back; only the start time, used
    by the picker to explain how long the container has been in use.
    """
    return {
        "sName": sName,
        "bClaimed": False,
        "sMessage": "In use in another browser session",
        "sStartedIso": _fsReadStartedIso(recordOwner),
    }


def _fdictCardinalityRefused(sName, sHeldContainerName):
    """Return the 409 body for a session that already holds a container.

    One container per browser session (design §9). The refusal names the
    session's own held container so the message is actionable: release
    that container, then claim this one.
    """
    return {
        "sName": sName,
        "bClaimed": False,
        "sMessage": (
            "This browser session already holds container "
            f"'{sHeldContainerName}'; release it before claiming another"
        ),
        "sHeldContainerName": sHeldContainerName,
    }


def _fdictPoisonRefused(sName):
    """Return the 409 body for a poisoned (force-abandoned) container."""
    return {
        "sName": sName,
        "bClaimed": False,
        "bPoisoned": True,
        "sMessage": (
            f"Container '{sName}' carries a force-abandoned operation "
            "whose worker is not yet proven dead; run 'vaibify "
            "reconcile' once the worker has exited"
        ),
    }


def _fdictCrossHubRefused(sName, error):
    """Return the 409 body for a container held by another hub process."""
    return {
        "sName": sName,
        "bClaimed": False,
        "sMessage": str(error),
        "iLockedByPid": error.iHolderPid,
        "iLockedByPort": error.iHolderPort,
    }


def _fdictQuarantineRefused(sName, error):
    """Return the 409 body for a journal-quarantined container.

    ``bQuarantined`` lets the picker say WHY the container is refused:
    not "in use elsewhere" but "an earlier operation never settled".
    """
    return {
        "sName": sName,
        "bClaimed": False,
        "bQuarantined": True,
        "sMessage": str(error),
    }


def _fsReadStartedIso(recordOwner):
    """Read the held flock's recorded start time, or '' when unavailable."""
    dictHolder = pidFileRegistry.fdictReadPayloadFromHandle(
        recordOwner.fileHandleLock,
    )
    return dictHolder.get("sStartedIso", "")


def fbReleaseWouldBePermitted(
    dictContainerOwners, sName, sLeaseId, sBrowserSessionId="",
):
    """Return True when a release by this caller would be committed.

    The arbitration half of :func:`fnReleaseOwnership`, exposed so the
    release authority can run its terminal drain (design §10) only for
    a caller that will actually be permitted — an unauthorized release
    attempt must never terminate the true owner's terminals.
    """
    recordOwner = dictContainerOwners.get(sName)
    if recordOwner is None:
        return False
    if getattr(recordOwner, "poison", None) is not None:
        # A poisoned record refuses release even to its owner: dropping
        # the flock while a force-abandoned worker's death is unproven
        # would hand the container to a second owner a zombie may still
        # write (design §2.1). Reconciliation is the only exit.
        return False
    bBoundOwner = fbBrowserSessionOwnsLease(
        dictContainerOwners, sName, sBrowserSessionId, sLeaseId,
    )
    bUnboundOwner = (
        recordOwner.sBrowserSessionId == ""
        and bool(sLeaseId)
        and recordOwner.sLeaseId == sLeaseId
    )
    return bBoundOwner or bUnboundOwner


def fnReleaseOwnership(
    dictContainerOwners, sName, sLeaseId, sBrowserSessionId="",
    dictSessionOwner=None,
):
    """Release ownership only when the caller proves the session-bound lease.

    The lease alone is not sufficient: a second browser tab that copied the
    owning lease VALUE must not be able to drop the true owner's record. So
    release requires the same strong predicate the connect gate and the
    WebSocket gates use — the presenting browser session must own the lease
    (:func:`fbBrowserSessionOwnsLease`). A transitional owner record that
    recorded no session binding (``sBrowserSessionId == ""``) is released on
    the matching lease value alone, the same allowance the claim-reclaim
    branch makes, so a pre-binding claim is never locked out.

    Returns ``True`` after freeing the flock, dropping the record, and
    stopping the keep-alive; ``False`` for an unknown container or a
    caller that does not hold the session-bound lease, which closes both
    the old append-only authorization leak and the copied-lease replay.
    """
    if not fbReleaseWouldBePermitted(
        dictContainerOwners, sName, sLeaseId, sBrowserSessionId,
    ):
        return False
    _fnForceReleaseOwnership(dictContainerOwners, sName, dictSessionOwner)
    return True


def _fnForceReleaseOwnership(
    dictContainerOwners, sName, dictSessionOwner=None,
):
    """Drop a record and free its flock and keep-alive, lease unchecked.

    Used by the reaper and the take-over path, where the prior owner is
    already proven dead, so no lease is presented. Also drops the
    released session's entry from the ``dictSessionOwner`` reverse index
    when the caller maintains one, so the cardinality authority never
    reports a container the owner map no longer holds.
    """
    recordOwner = dictContainerOwners.pop(sName, None)
    if recordOwner is None:
        return
    if (
        dictSessionOwner is not None
        and recordOwner.sBrowserSessionId
        and dictSessionOwner.get(recordOwner.sBrowserSessionId) == sName
    ):
        dictSessionOwner.pop(recordOwner.sBrowserSessionId, None)
    if recordOwner.fileHandleLock is not None:
        fnReleaseContainerLock(recordOwner.fileHandleLock)
    fnStopKeepAlive(sName)


def fbSessionOwnsContainer(dictContainerOwners, sName, sLeaseId):
    """Return True when the presented lease owns the named container."""
    recordOwner = dictContainerOwners.get(sName)
    return (
        recordOwner is not None
        and bool(sLeaseId)
        and recordOwner.sLeaseId == sLeaseId
    )


def fiOwnerGenerationForName(dictContainerOwners, sName):
    """Return the owner record's current generation, or 0 when unowned.

    ``0`` is never a live generation (records start at 1), so a
    connection record stamped 0 — a socket accepted while the container
    had no owner — can never match a real owner and never decrements.
    """
    recordOwner = dictContainerOwners.get(sName)
    return recordOwner.iOwnerGeneration if recordOwner is not None else 0


def fnIncrementLiveConnection(dictContainerOwners, sName, bPipelineLane=False):
    """Record a new live connection for an owned container."""
    recordOwner = dictContainerOwners.get(sName)
    if recordOwner is None:
        return
    recordOwner.iLiveConnectionCount += 1
    recordOwner.bSocketEverExisted = True
    if bPipelineLane:
        recordOwner.iLivePipelineConnectionCount += 1
    recordOwner.fLastSeenMonotonic = time.monotonic()


def fnDecrementLiveConnection(dictContainerOwners, sName, bPipelineLane=False):
    """Drop a live connection, starting the grace clock at zero."""
    recordOwner = dictContainerOwners.get(sName)
    if recordOwner is None:
        return
    recordOwner.iLiveConnectionCount = max(
        0, recordOwner.iLiveConnectionCount - 1,
    )
    if bPipelineLane:
        recordOwner.iLivePipelineConnectionCount = max(
            0, recordOwner.iLivePipelineConnectionCount - 1,
        )
    recordOwner.fLastSeenMonotonic = time.monotonic()


def fnDecrementLiveConnectionForRecord(
    dictContainerOwners, sName, recordConnection,
):
    """Drop a live connection only when its generation is still current.

    The decrement is matched on the connection record (design §2.3): a
    socket whose ``finally`` fires after a host transfer carries the OLD
    generation, and letting it decrement would corrupt the successor's
    counters. A mismatched (or absent) record decrements nothing and
    does not refresh the owner's liveness stamp.
    """
    recordOwner = dictContainerOwners.get(sName)
    if recordOwner is None or recordConnection is None:
        return
    if recordConnection.iOwnerGeneration != recordOwner.iOwnerGeneration:
        return
    fnDecrementLiveConnection(
        dictContainerOwners, sName,
        bPipelineLane=recordConnection.sLane == S_LANE_PIPELINE,
    )


def flistPoisonAndFenceConnections(
    recordOwner, recordPoison, dictSessionSockets,
):
    """Poison an owner record and return the sockets that must be fenced.

    THE SINGLE WRITER of ``OwnerRecord.poison``. Poison and fencing are
    one act, not two: a record marked poisoned while its pipeline socket
    keeps dispatching frames is a container that refuses new mutations
    and permits the ones already in flight, which is the opposite of
    fail-closed. Returning the connections rather than closing them
    keeps this function synchronous, so it can run inside the
    lifecycle authority's held lock where the poison must be set;
    the caller awaits the closes afterwards.

    Only the MUTATION lane is fenced. Safe observability reads and the
    trusted host reconciliation lane stay open on purpose -- poison is
    cleared by ``vaibify reconcile``, which reaches the hub over the
    host control socket, and fencing every lane would fence off the
    poison's own cure.
    """
    recordOwner.poison = recordPoison
    setRecords = (dictSessionSockets or {}).get(
        recordOwner.sBrowserSessionId, set(),
    )
    return [
        recordConnection for recordConnection in setRecords
        if recordConnection.sLane == S_LANE_PIPELINE
    ]


def fnClearPoison(recordOwner):
    """THE SINGLE CLEARER of ``OwnerRecord.poison``.

    Called only from the reconciliation transaction, which has PROVEN
    the abandoned worker dead; nothing else may decide a container is
    healthy again.
    """
    if recordOwner is not None:
        recordOwner.poison = None


def fnRegisterSessionSocket(dictSessionSockets, recordConnection):
    """Add a connection record to its browser session's live-socket set."""
    if dictSessionSockets is None or recordConnection is None:
        return
    if not recordConnection.sBrowserSessionId:
        return
    dictSessionSockets.setdefault(
        recordConnection.sBrowserSessionId, set(),
    ).add(recordConnection)


def fnDeregisterSessionSocket(dictSessionSockets, recordConnection):
    """Remove a connection record, dropping its session's emptied set."""
    if dictSessionSockets is None or recordConnection is None:
        return
    setRecords = dictSessionSockets.get(recordConnection.sBrowserSessionId)
    if setRecords is None:
        return
    setRecords.discard(recordConnection)
    if not setRecords:
        dictSessionSockets.pop(recordConnection.sBrowserSessionId, None)


def fbOwnerIsReapable(recordOwner, fGraceSeconds=_F_GRACE_SECONDS):
    """Return True when a record has no live connection past the grace.

    The busy veto (no running pipeline) is applied by the caller, not
    here, so this predicate stays a pure function of the record. A
    poisoned record is never reapable: reaping would release the flock
    over a force-abandoned worker whose death is unproven (design §2.1).
    An ACTIVE record keeps the idle-grace reap measured from its last
    live socket; an ORPHANED_SESSION record answers the stricter §7
    conditions instead.
    """
    if getattr(recordOwner, "poison", None) is not None:
        return False
    if getattr(recordOwner, "reservation", None) is not None:
        # A record carrying a live start reservation is NEVER idle
        # (design §10b): the reservation is what makes the reaper — and
        # the idle watchdog that reads this map — treat a container with
        # no sockets yet as live work, so a hub can never self-SIGTERM
        # or release the flock out from under a running docker create.
        return False
    if recordOwner.iLiveConnectionCount > 0:
        return False
    if recordOwner.sState == S_OWNER_STATE_ORPHANED_SESSION:
        return _fbOrphanedRecordIsReapable(recordOwner, fGraceSeconds)
    fElapsedSeconds = time.monotonic() - recordOwner.fLastSeenMonotonic
    return fElapsedSeconds >= fGraceSeconds


def fbAgentIsLiveOnRecord(recordOwner, fGraceSeconds=_F_GRACE_SECONDS):
    """Return True when an in-container agent is live on this record.

    The §7 agent-liveness reading, named once because two authorities
    consult it and must agree: the safe reaper, which may not release
    a record an agent is still acting in, and the explicit-release
    arbitration (§10), which refuses a release under a live agent
    unless the caller forces. A request still in flight pins the
    record for its whole duration (``iInFlightAgentRequests``, case
    20); between calls, an activity stamp fresher than the grace pins
    it (``fLastAgentActivityMonotonic``, case 7).
    """
    if recordOwner.iInFlightAgentRequests > 0:
        return True
    return recordOwner.fLastAgentActivityMonotonic > 0.0 and (
        time.monotonic() - recordOwner.fLastAgentActivityMonotonic
        < fGraceSeconds
    )


def _fbOrphanedRecordIsReapable(recordOwner, fGraceSeconds):
    """Answer the record-local ORPHANED→RELEASED conditions (design §7).

    The reap grace runs from the ORPHAN stamp, never from the last
    socket, and a live in-container agent — in flight or recently
    active — pins the record outright. The caller-level vetoes (live
    guarded work, live terminal records, unsettled journal records)
    stay with the reaper loop, which can see the app state.
    """
    if fbAgentIsLiveOnRecord(recordOwner, fGraceSeconds):
        return False
    fOrphanedElapsedSeconds = (
        time.monotonic() - recordOwner.fOrphanedSinceMonotonic
    )
    return fOrphanedElapsedSeconds >= fGraceSeconds


def _fbOwnerIsReapableNow(
    recordOwner, sName, fbPipelineRunning, fGraceSeconds,
):
    """Return True when the record is reapable and no pipeline is running."""
    if not fbOwnerIsReapable(recordOwner, fGraceSeconds):
        return False
    if fbPipelineRunning is not None and fbPipelineRunning(sName):
        return False
    return True


def flistReapIdleOwnerships(
    dictContainerOwners, fbPipelineRunning=None,
    fGraceSeconds=_F_GRACE_SECONDS, dictSessionOwner=None,
):
    """Release every idle, past-grace ownership and return their names.

    A container whose pipeline is still running is never reaped, so the
    viewer idle watchdog can never tear down a session mid-run.
    """
    listReaped = []
    for sName in list(dictContainerOwners.keys()):
        recordOwner = dictContainerOwners.get(sName)
        if recordOwner is None:
            continue
        if _fbOwnerIsReapableNow(
            recordOwner, sName, fbPipelineRunning, fGraceSeconds,
        ):
            _fnForceReleaseOwnership(
                dictContainerOwners, sName, dictSessionOwner,
            )
            listReaped.append(sName)
    return listReaped
