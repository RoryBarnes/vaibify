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
    "OwnerRecord",
    "fdictCreateOwnerRegistry",
    "fsMintLease",
    "fsMintAgentToken",
    "fsAgentTokenForName",
    "fbAgentTokenAuthorizesContainerId",
    "ftdictClaim",
    "fnReleaseOwnership",
    "fbSessionOwnsContainer",
    "fnIncrementLiveConnection",
    "fnDecrementLiveConnection",
    "fbOwnerIsReapable",
    "flistReapIdleOwnerships",
]

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


_F_GRACE_SECONDS = 30.0


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
    """

    sLeaseId: str
    fileHandleLock: object
    sAgentToken: str = ""
    sContainerId: str = ""
    iLiveConnectionCount: int = 0
    iLivePipelineConnectionCount: int = 0
    fLastSeenMonotonic: float = field(default_factory=time.monotonic)


def fdictCreateOwnerRegistry():
    """Return a fresh, empty owner-of-record map for ``app.state``."""
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
):
    """Arbitrate a claim and return ``(iStatusCode, dictPayload)``.

    Unowned grants a fresh lease (200). A same-lease re-claim is
    idempotent (200) so a reloaded tab re-asserting its
    ``sessionStorage`` lease never self-locks. A foreign or absent lease
    is refused with 409 unless the current owner is reapable, in which
    case the dead owner is released and the claim is granted fresh.
    """
    recordOwner = dictContainerOwners.get(sName)
    if recordOwner is None:
        return _ftdictClaimUnowned(
            dictContainerOwners, sName, iPort, sContainerId,
        )
    if sLeaseId and recordOwner.sLeaseId == sLeaseId:
        recordOwner.fLastSeenMonotonic = time.monotonic()
        return (200, _fdictClaimGranted(sName, recordOwner.sLeaseId))
    if _fbOwnerIsReapableNow(
        recordOwner, sName, fbPipelineRunning, fGraceSeconds,
    ):
        _fnForceReleaseOwnership(dictContainerOwners, sName)
        return _ftdictClaimUnowned(
            dictContainerOwners, sName, iPort, sContainerId,
        )
    return (409, _fdictClaimRefused(sName, recordOwner))


def _ftdictClaimUnowned(dictContainerOwners, sName, iPort, sContainerId):
    """Acquire the host flock for an unowned container and mint a lease."""
    try:
        fileHandleLock = fnAcquireContainerLock(sName, iPort)
    except ContainerLockedError as error:
        return (409, _fdictCrossHubRefused(sName, error))
    sLeaseId = _fnRecordNewOwner(
        dictContainerOwners, sName, fileHandleLock, sContainerId,
    )
    return (200, _fdictClaimGranted(sName, sLeaseId))


def _fnRecordNewOwner(
    dictContainerOwners, sName, fileHandleLock, sContainerId="",
):
    """Mint a lease plus a per-container agent token and store the owner."""
    sLeaseId = fsMintLease()
    dictContainerOwners[sName] = OwnerRecord(
        sLeaseId=sLeaseId,
        fileHandleLock=fileHandleLock,
        sAgentToken=fsMintAgentToken(),
        sContainerId=sContainerId,
    )
    return sLeaseId


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


def fnReleaseOwnership(dictContainerOwners, sName, sLeaseId):
    """Release ownership only when the caller proves the matching lease.

    Returns ``True`` after freeing the flock, dropping the record, and
    stopping the keep-alive; ``False`` for an unknown container or a
    non-owner, which closes the old append-only authorization leak.
    """
    recordOwner = dictContainerOwners.get(sName)
    if recordOwner is None or recordOwner.sLeaseId != sLeaseId:
        return False
    _fnForceReleaseOwnership(dictContainerOwners, sName)
    return True


def _fnForceReleaseOwnership(dictContainerOwners, sName):
    """Drop a record and free its flock and keep-alive, lease unchecked.

    Used by the reaper and the take-over path, where the prior owner is
    already proven dead, so no lease is presented.
    """
    recordOwner = dictContainerOwners.pop(sName, None)
    if recordOwner is None:
        return
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


def fnIncrementLiveConnection(dictContainerOwners, sName, bPipelineLane=False):
    """Record a new live connection for an owned container."""
    recordOwner = dictContainerOwners.get(sName)
    if recordOwner is None:
        return
    recordOwner.iLiveConnectionCount += 1
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
    fGraceSeconds=_F_GRACE_SECONDS,
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
            _fnForceReleaseOwnership(dictContainerOwners, sName)
            listReaped.append(sName)
    return listReaped
