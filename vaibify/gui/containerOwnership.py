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
    "ConnectionRecord",
    "fdictCreateOwnerRegistry",
    "fdictCreateSessionOwnerIndex",
    "fdictCreateSessionSocketIndex",
    "ffReadSecondsFromEnvironment",
    "fsMintLease",
    "fsMintAgentToken",
    "fsAgentTokenForName",
    "fbAgentTokenAuthorizesContainerId",
    "ftdictClaim",
    "fbBrowserSessionOwnsLease",
    "fnReleaseOwnership",
    "fbSessionOwnsContainer",
    "fiOwnerGenerationForName",
    "fnIncrementLiveConnection",
    "fnDecrementLiveConnection",
    "fnDecrementLiveConnectionForRecord",
    "fnRegisterSessionSocket",
    "fnDeregisterSessionSocket",
    "fbOwnerIsReapable",
    "flistReapIdleOwnerships",
]

import logging
import os
import secrets
import time
from dataclasses import dataclass, field

from vaibify.config import pidFileRegistry
from vaibify.config.containerLock import (
    ContainerLockedError,
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
    ``fOrphanedSinceMonotonic`` stamps the ACTIVE→ORPHANED transition (0.0
    while never orphaned) so the reap grace measures from the orphan
    moment, not the last socket; ``fLastAgentActivityMonotonic`` and
    ``iInFlightAgentRequests`` let the safe reaper see a live in-container
    agent; and ``bSocketEverExisted`` gates the zero-sockets orphan
    trigger, so a claim that never opened a socket falls to the idle
    window instead of orphaning instantly.
    """

    sLeaseId: str
    fileHandleLock: object
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
    """Return True when a presented agent token owns ``sContainerId``.

    The per-container token is the proof of which container the agent
    speaks for; it authorizes only the container whose owner record both
    minted it and serves that Docker id. An empty token or id never
    matches, so a request that names no container fails closed.
    """
    if not sPresentedToken or not sContainerId:
        return False
    for recordOwner in dictContainerOwners.values():
        if (
            recordOwner.sAgentToken
            and recordOwner.sAgentToken == sPresentedToken
            and recordOwner.sContainerId == sContainerId
        ):
            return True
    return False


def ftdictClaim(
    dictContainerOwners, sName, sLeaseId, iPort, sContainerId="",
    fbPipelineRunning=None, fGraceSeconds=_F_GRACE_SECONDS,
    sBrowserSessionId="", dictSessionOwner=None,
):
    """Arbitrate a claim and return ``(iStatusCode, dictPayload)``.

    Unowned grants a fresh lease (200), binding the record to the claiming
    browser session (``sBrowserSessionId``). A same-lease re-claim is
    idempotent (200) so a reloaded tab re-asserting its
    ``sessionStorage`` lease never self-locks. A foreign or absent lease
    is refused with 409 unless the current owner is reapable, in which
    case the dead owner is released and the claim is granted fresh.
    """
    recordOwner = dictContainerOwners.get(sName)
    if recordOwner is None:
        return _ftdictClaimUnowned(
            dictContainerOwners, sName, iPort, sContainerId,
            sBrowserSessionId, dictSessionOwner,
        )
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
            sBrowserSessionId, dictSessionOwner,
        )
    return (409, _fdictClaimRefused(sName, recordOwner))


def _ftdictClaimUnowned(
    dictContainerOwners, sName, iPort, sContainerId, sBrowserSessionId="",
    dictSessionOwner=None,
):
    """Acquire the host flock for an unowned container and mint a lease."""
    try:
        fileHandleLock = fnAcquireContainerLock(sName, iPort)
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


def _fdictCrossHubRefused(sName, error):
    """Return the 409 body for a container held by another hub process."""
    return {
        "sName": sName,
        "bClaimed": False,
        "sMessage": str(error),
        "iLockedByPid": error.iHolderPid,
        "iLockedByPort": error.iHolderPort,
    }


def _fsReadStartedIso(recordOwner):
    """Read the held flock's recorded start time, or '' when unavailable."""
    dictHolder = pidFileRegistry.fdictReadPayloadFromHandle(
        recordOwner.fileHandleLock,
    )
    return dictHolder.get("sStartedIso", "")


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
    recordOwner = dictContainerOwners.get(sName)
    if recordOwner is None:
        return False
    bBoundOwner = fbBrowserSessionOwnsLease(
        dictContainerOwners, sName, sBrowserSessionId, sLeaseId,
    )
    bUnboundOwner = (
        recordOwner.sBrowserSessionId == ""
        and bool(sLeaseId)
        and recordOwner.sLeaseId == sLeaseId
    )
    if not (bBoundOwner or bUnboundOwner):
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
    here, so this predicate stays a pure function of the record.
    """
    if recordOwner.iLiveConnectionCount > 0:
        return False
    fElapsedSeconds = time.monotonic() - recordOwner.fLastSeenMonotonic
    return fElapsedSeconds >= fGraceSeconds


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
