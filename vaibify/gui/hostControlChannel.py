"""Host-only control plane over a peer-authenticated Unix socket.

Design §6b/§14. Each hub serves one Unix domain socket at
``~/.vaibify/control/hub-<port>.controlSocket`` — a ``0700`` directory,
socket mode ``0600``, NEVER HTTP or TCP, and unreachable from any
container (the directory is on ``bindMountValidator``'s denied host
prefixes). Every connection is peer-authenticated to the hub's own uid
through one portability shim, :func:`ftPeerUidGid` — ``SO_PEERCRED``
on Linux, ``getpeereid``-equivalent ``LOCAL_PEERCRED`` on macOS — and a
foreign uid is closed without a byte of response.

The protocol is a CLOSED, allowlisted operation schema: one JSON line
in, one JSON line out. The operations shipped here are the ones with
consumers today — ``reconcile``, ``force-abandon``, ``break-glass``,
``abandon-host-journal`` (the ``vaibify reconcile`` CLI),
``mint-transfer`` (the ``vaibify open``
CLI, slice 5), and ``mint-bootstrap`` (the headless ``vaibify do``
credential, slice 8). Any unlisted opcode is rejected with a defined
error naming the allowlist.

Every destructive operation carries an ABA guard (design §6b, cases 42
and 46): ``reconcile`` and ``force-abandon`` require the container name
plus the expected journal/task operation id(s), so a stale request can
never act on a successor operation; ``break-glass`` carries the sha256
of the bounded raw marker bytes, because a malformed record has no
parseable operation id. ``mint-transfer`` carries the expected owner
generation, learned from the hub itself in a describe round trip, so a
stale CLI can never transfer a successor generation without seeing it.
``mint-bootstrap`` needs no such guard, and deliberately has no
container field at all: it mints an ORDINARY launch capability — the
same thing the hub puts in the browser's URL fragment — which names no
container, carries no ownership authority, and displaces no owner. A
host-lane caller redeems it into a plain browser session and must then
claim like any other browser.

Per-hub discovery mirrors the ``sessionRegistry`` slot pattern: the
socket path is keyed by hub port, stale sockets (no live hub slot for
the port) are unlinked on hub start and skipped by clients. The socket
handlers run ON the hub event loop (``asyncio.start_unix_server``), so
socket-driven transitions take the ``sessionLifecycle`` asyncio locks
on the same loop (design §3.5).
"""

__all__ = [
    "S_SOCKET_OPERATION_RECONCILE",
    "S_SOCKET_OPERATION_FORCE_ABANDON",
    "S_SOCKET_OPERATION_BREAK_GLASS",
    "S_SOCKET_OPERATION_MINT_TRANSFER",
    "S_SOCKET_OPERATION_MINT_BOOTSTRAP",
    "F_RECONCILE_DRAIN_WAIT_SECONDS",
    "HostControlError",
    "ftPeerUidGid",
    "fsControlSocketPathForPort",
    "fnUnlinkStaleControlSockets",
    "fnRegisterHostControlChannel",
    "fdictSendHostControlRequest",
    "fdictReconcileHeldContainer",
    "fbHubHoldsContainerFlockForName",
]

import asyncio
import functools
import json
import logging
import os
import socket
import stat
import struct
import time

from vaibify.config import containerLock, operationJournal, reconciliation
from vaibify.config.pidFileRegistry import fnEnsureDirectory, fnUnlinkQuietly
from . import browserSession
from . import commitCarrier
from . import containerOwnership
from . import sessionLifecycle

logger = logging.getLogger("vaibify")

S_SOCKET_OPERATION_RECONCILE = "reconcile"
S_SOCKET_OPERATION_FORCE_ABANDON = "force-abandon"
S_SOCKET_OPERATION_BREAK_GLASS = "break-glass"
S_SOCKET_OPERATION_ABANDON_HOST_JOURNAL = "abandon-host-journal"
S_SOCKET_OPERATION_MINT_TRANSFER = "mint-transfer"
S_SOCKET_OPERATION_MINT_BOOTSTRAP = "mint-bootstrap"
S_SOCKET_OPERATION_LIST_REATTACHABLE = "list-reattachable"

F_RECONCILE_DRAIN_WAIT_SECONDS = (
    containerOwnership.ffReadSecondsFromEnvironment(
        "VAIBIFY_RECONCILE_DRAIN_WAIT_SECONDS", 5.0,
    )
)

_S_CONTROL_DIRECTORY = os.path.expanduser("~/.vaibify/control")
_S_SOCKET_PREFIX = "hub-"
_S_SOCKET_SUFFIX = ".controlSocket"
_I_MAXIMUM_MESSAGE_BYTES = 65536
_F_CONNECTION_READ_TIMEOUT_SECONDS = 10.0

# Linux struct ucred: {pid_t, uid_t, gid_t}, three native ints.
_S_LINUX_UCRED_FORMAT = "3i"
# macOS struct xucred: {u_int cr_version; uid_t cr_uid; short
# cr_ngroups; gid_t cr_groups[16];} — version+uid+ngroups, 2 bytes of
# alignment padding, then the group array whose first entry is the
# effective gid. XUCRED_VERSION is 0.
_I_SOL_LOCAL_DARWIN = 0
_I_LOCAL_PEERCRED_DARWIN = 0x0001
_S_DARWIN_XUCRED_PREFIX_FORMAT = "IIh"
_I_DARWIN_XUCRED_GROUPS_OFFSET = 12
_I_DARWIN_XUCRED_BYTES = 76
_I_DARWIN_XUCRED_VERSION = 0


class HostControlError(RuntimeError):
    """The host control socket could not be served or reached."""


# ---------------------------------------------------------------------
# Peer credentials — the ONE portability shim (design §6b).
# ---------------------------------------------------------------------

def ftPeerUidGid(socketConnection):
    """Return ``(iUid, iGid)`` of a connected Unix-socket peer.

    ``SO_PEERCRED`` on Linux, ``LOCAL_PEERCRED`` (the ``getpeereid``
    mechanism) on macOS. Raises :class:`HostControlError` when the
    kernel answer cannot be parsed — the caller must treat that as an
    unauthorized peer, never as a pass.
    """
    if hasattr(socket, "SO_PEERCRED"):
        return _ftParseLinuxPeerCredentials(
            socketConnection.getsockopt(
                socket.SOL_SOCKET, socket.SO_PEERCRED,
                struct.calcsize(_S_LINUX_UCRED_FORMAT),
            )
        )
    return _ftParseDarwinPeerCredentials(
        socketConnection.getsockopt(
            _I_SOL_LOCAL_DARWIN, _I_LOCAL_PEERCRED_DARWIN,
            _I_DARWIN_XUCRED_BYTES,
        )
    )


def _ftParseLinuxPeerCredentials(byteCredentials):
    """Parse a Linux ``struct ucred`` into ``(iUid, iGid)``."""
    try:
        _, iPeerUid, iPeerGid = struct.unpack(
            _S_LINUX_UCRED_FORMAT, byteCredentials,
        )
    except struct.error as error:
        raise HostControlError(f"unparseable SO_PEERCRED answer: {error}")
    return (iPeerUid, iPeerGid)


def _ftParseDarwinPeerCredentials(byteCredentials):
    """Parse a macOS ``struct xucred`` into ``(iUid, iGid)``."""
    try:
        iVersion, iPeerUid, iGroupCount = struct.unpack_from(
            _S_DARWIN_XUCRED_PREFIX_FORMAT, byteCredentials,
        )
        if iVersion != _I_DARWIN_XUCRED_VERSION:
            raise HostControlError(
                "unsupported xucred structure version"
            )
        if iGroupCount < 1:
            raise HostControlError("xucred carries no effective gid")
        iPeerGid = struct.unpack_from(
            "I", byteCredentials, _I_DARWIN_XUCRED_GROUPS_OFFSET,
        )[0]
    except struct.error as error:
        raise HostControlError(f"unparseable LOCAL_PEERCRED answer: {error}")
    return (iPeerUid, iPeerGid)


# ---------------------------------------------------------------------
# Per-hub socket discovery, keyed by port (design §6b).
# ---------------------------------------------------------------------

def fsControlSocketPathForPort(iPort):
    """Return the control-socket path for a hub port."""
    if not isinstance(iPort, int) or isinstance(iPort, bool) or iPort <= 0:
        raise HostControlError(
            f"A host-control socket needs a positive hub port, not "
            f"{iPort!r}"
        )
    return os.path.join(
        _S_CONTROL_DIRECTORY,
        f"{_S_SOCKET_PREFIX}{iPort}{_S_SOCKET_SUFFIX}",
    )


def fnUnlinkStaleControlSockets():
    """Unlink control sockets whose port has no live hub slot.

    Mirrors the container-lock reaper: a hub that died (or was
    SIGKILLed) leaves its socket file behind; the ``sessionRegistry``
    slot — flock-held by a live process — is the liveness oracle, so a
    port with no live hub slot marks its socket stale. Non-socket
    entries are never touched (a symlink dropped here is an attack, not
    garbage).
    """
    from vaibify.config.sessionRegistry import fdictReadHubSlotByPort
    try:
        listEntries = os.listdir(_S_CONTROL_DIRECTORY)
    except OSError:
        return
    for sEntry in listEntries:
        iPort = _fiPortFromSocketName(sEntry)
        if iPort <= 0 or fdictReadHubSlotByPort(iPort):
            continue
        sPath = os.path.join(_S_CONTROL_DIRECTORY, sEntry)
        if _fbLstatIsSocket(sPath):
            fnUnlinkQuietly(sPath)


def _fiPortFromSocketName(sEntry):
    """Return the port a socket file name encodes, or 0 when foreign."""
    if not (
        sEntry.startswith(_S_SOCKET_PREFIX)
        and sEntry.endswith(_S_SOCKET_SUFFIX)
    ):
        return 0
    try:
        return int(sEntry[len(_S_SOCKET_PREFIX): -len(_S_SOCKET_SUFFIX)])
    except ValueError:
        return 0


def _fbLstatIsSocket(sPath):
    """Return True when sPath is a socket itself (never following links)."""
    try:
        return stat.S_ISSOCK(os.lstat(sPath).st_mode)
    except OSError:
        return False


def _fnAssertBindTargetSafe(sPath):
    """Remove a leftover socket at sPath; refuse any other file kind.

    ``O_NOFOLLOW``-safe by construction: the check is an ``lstat``, so
    a symlink planted at the bind path is refused rather than followed,
    and nothing that is not a socket is ever unlinked.
    """
    try:
        statResult = os.lstat(sPath)
    except FileNotFoundError:
        return
    if stat.S_ISSOCK(statResult.st_mode):
        fnUnlinkQuietly(sPath)
        return
    raise HostControlError(
        f"Refusing to bind the host-control socket: {sPath} exists and "
        "is not a socket. Remove it manually after inspecting it."
    )


# ---------------------------------------------------------------------
# The server: registered on the hub lifespan, served on the hub loop.
# ---------------------------------------------------------------------

def fnRegisterHostControlChannel(app, dictCtx):
    """Register the host-control server on a hub's lifespan hooks.

    A test-harness hub (``iHubPort == 0``, the documented sentinel) is
    skipped: the per-hub socket is keyed by a real port, and the suite
    must not bind sockets in the user's real ``~/.vaibify/control``.
    """

    async def fnStartHostControlServer(app):
        iPort = getattr(app.state, "iHubPort", 0)
        if not iPort:
            return
        fnEnsureDirectory(_S_CONTROL_DIRECTORY)
        fnUnlinkStaleControlSockets()
        sPath = fsControlSocketPathForPort(iPort)
        _fnAssertBindTargetSafe(sPath)
        serverControl = await asyncio.start_unix_server(
            functools.partial(_fnServeHostControlConnection, app, dictCtx),
            path=sPath, limit=_I_MAXIMUM_MESSAGE_BYTES,
        )
        os.chmod(sPath, 0o600)
        app.state.serverHostControl = serverControl

    async def fnStopHostControlServer(app):
        serverControl = getattr(app.state, "serverHostControl", None)
        if serverControl is None:
            return
        serverControl.close()
        await serverControl.wait_closed()
        iPort = getattr(app.state, "iHubPort", 0)
        if iPort:
            fnUnlinkQuietly(fsControlSocketPathForPort(iPort))

    app.state.listLifespanStartup.append(fnStartHostControlServer)
    app.state.listLifespanShutdown.append(fnStopHostControlServer)


async def _fnServeHostControlConnection(app, dictCtx, reader, writer):
    """Serve one connection: peer-auth, one request, one response."""
    try:
        if not _fbPeerIsThisUser(writer):
            # Closed without a byte: an unauthorized peer learns
            # nothing, and no credential detail is ever logged.
            logger.warning(
                "Rejected a host-control connection from a foreign peer"
            )
            return
        dictResponse = await _fdictAnswerOneRequest(app, dictCtx, reader)
        writer.write(
            json.dumps(dictResponse).encode("utf-8") + b"\n",
        )
        await writer.drain()
    except (OSError, asyncio.TimeoutError):
        pass
    finally:
        writer.close()


def _fbPeerIsThisUser(writer):
    """Return True only when the peer's uid is the hub's own uid."""
    socketPeer = writer.get_extra_info("socket")
    if socketPeer is None:
        return False
    try:
        iPeerUid, _ = ftPeerUidGid(socketPeer)
    except (HostControlError, OSError):
        return False
    return iPeerUid == os.getuid()


async def _fdictAnswerOneRequest(app, dictCtx, reader):
    """Read, validate, and dispatch one allowlisted request line."""
    try:
        byteRequest = await asyncio.wait_for(
            reader.readline(), _F_CONNECTION_READ_TIMEOUT_SECONDS,
        )
    except (asyncio.TimeoutError, ValueError, asyncio.LimitOverrunError):
        return _fdictRefusal("the request line was oversized or timed out")
    try:
        dictRequest = json.loads(byteRequest.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return _fdictRefusal("the request is not a JSON object")
    if not isinstance(dictRequest, dict):
        return _fdictRefusal("the request is not a JSON object")
    sOperation = dictRequest.get("sOperation", "")
    fnHandler = _DICT_SOCKET_OPERATION_HANDLERS.get(sOperation)
    if fnHandler is None:
        return _fdictRefusal(
            f"unknown host-control operation {sOperation!r}; the "
            "allowlisted operations are "
            f"{sorted(_DICT_SOCKET_OPERATION_HANDLERS)}"
        )
    return await fnHandler(app, dictCtx, dictRequest)


def _fdictRefusal(sError):
    """Return the uniform refusal response."""
    return {"bAccepted": False, "sError": sError}


def _fsValidatedContainerName(dictRequest):
    """Return the request's container name, or '' when unsafe."""
    sName = dictRequest.get("sContainerName", "")
    if isinstance(sName, str) and containerLock.fbIsValidProjectName(sName):
        return sName
    return ""


def _fbHubHoldsContainerFlock(appState, sName):
    """Return True when this hub's owner record holds the container flock.

    The socket handlers substitute the hub's container-mutation lock
    for a fresh flock, which is sound ONLY while this hub actually
    holds the container's flock through its owner record. A request
    routed here for a container this hub does not hold (a released
    owner racing the CLI's lock-holder read) must be refused, or the
    prove-and-clear would run with no cross-process arbitration at all.
    """
    recordOwner = appState.dictContainerOwners.get(sName)
    return (
        recordOwner is not None
        and getattr(recordOwner, "fileHandleLock", None) is not None
    )


def _fsNotHeldHereRefusal(sName):
    """Return the refusal text for a container this hub does not hold."""
    return (
        f"this hub does not hold container '{sName}'; if no live "
        "vaibify process holds its flock, run 'vaibify reconcile' "
        "again and it will take the crash-time path directly"
    )


# ---------------------------------------------------------------------
# Operation handlers. Each landed with its client, never as a dead stub.
# ---------------------------------------------------------------------

async def _fdictHandleReconcile(app, dictCtx, dictRequest):
    """Reconcile a container this live hub holds (design §8).

    Validation only; the transaction itself is
    :func:`fdictReconcileHeldContainer`, shared with the dashboard's
    quarantine route so the two entry points cannot diverge about what
    a reconcile proves.
    """
    sName = _fsValidatedContainerName(dictRequest)
    if not sName:
        return _fdictRefusal("a valid sContainerName is required")
    listExpectedIds = dictRequest.get("listExpectedOperationIds")
    if not _fbIsStringList(listExpectedIds):
        return _fdictRefusal(
            "reconcile requires listExpectedOperationIds — the expected "
            "journal operation id(s), so a stale request cannot act on "
            "a successor operation"
        )
    return await fdictReconcileHeldContainer(
        app, dictCtx, sName, listExpectedIds,
    )


async def fdictReconcileHeldContainer(app, dictCtx, sName, listExpectedIds):
    """Reconcile a container whose flock this live hub holds.

    Takes the container-mutation lock with a bounded wait — a wedged
    mode-(b) supervisor holds that lock for its worker's whole life, so
    an unbounded wait would hang the control plane on the very worker
    reconciliation exists to recover from. The proving core runs off
    the loop; the in-memory ``PoisonRecord`` is cleared after the proof
    and the durable marker is cleared LAST.

    Two callers, one transaction: the host-control socket (the
    ``vaibify reconcile`` CLI routed to a live hub) and the dashboard's
    quarantine route. Refusals come back as ``{"bAccepted": False,
    "sError": ...}`` values, never exceptions, so each caller renders
    them in its own lane's vocabulary.
    """
    if not _fbHubHoldsContainerFlock(app.state, sName):
        return _fdictRefusal(_fsNotHeldHereRefusal(sName))
    lockMutation = sessionLifecycle.flockContainerMutationForAppState(
        app.state, sName,
    )
    try:
        await asyncio.wait_for(
            lockMutation.acquire(), F_RECONCILE_DRAIN_WAIT_SECONDS,
        )
    except asyncio.TimeoutError:
        return _fdictRefusal(
            f"a guarded mutation still holds container '{sName}'s "
            "drain; if it is wedged, force-abandon it first, then "
            "reconcile once its worker has exited"
        )
    try:
        return await _fdictProveAndClearOnHub(
            app, dictCtx, sName, listExpectedIds,
        )
    finally:
        lockMutation.release()


def fbHubHoldsContainerFlockForName(appState, sName):
    """Public face of the flock question, for the dashboard route.

    The route needs it to choose between the held-hub transaction and
    the crash-time one; exposing the answer keeps that choice out of
    this module while keeping the flock reading in it.
    """
    return _fbHubHoldsContainerFlock(appState, sName)


async def _fdictProveAndClearOnHub(app, dictCtx, sName, listExpectedIds):
    """Prove under the held drain, then clear poison, then marker last."""
    dictProven = await asyncio.to_thread(
        reconciliation.fdictProveJournalRecordsSettled,
        sName, dictCtx.get("docker"), set(listExpectedIds),
        lambda: not commitCarrier.fbContainerHasLiveMutationWork(
            app.state, sName,
        ),
    )
    if not dictProven["bProven"]:
        return _fdictRefusal(dictProven["sRefusalReason"])
    recordOwner = app.state.dictContainerOwners.get(sName)
    containerOwnership.fnClearPoison(recordOwner)
    try:
        await asyncio.to_thread(
            reconciliation.fnCleanupAndClearProvenRecords,
            sName, dictProven["listClearableOperationIds"],
            dictCtx.get("docker"),
        )
    except (
        reconciliation.ReconciliationRefusedError,
        operationJournal.OperationJournalError,
    ) as error:
        return _fdictRefusal(str(error))
    return {
        "bAccepted": True,
        "bReconciled": True,
        "listReconciledOperationIds": (
            dictProven["listClearableOperationIds"]
        ),
        "listRecordNotes": dictProven["listRecordNotes"],
    }


async def _fdictHandleForceAbandon(app, dictCtx, dictRequest):
    """Poison a wedged guarded operation — never a silent rebind.

    Out-of-band by design (§8): NO mutation lock is taken, because the
    wedged supervisor holds it. The ABA guard requires the expected
    operation id to name a live supervisor's journal record, a live
    durable task, or an existing journal record — a stale id poisons
    nothing. Poison retains the flock and refuses all mutation, claim,
    release, and reap until reconciliation proves the worker dead; the
    journal record is marked ``NEEDS_RECONCILIATION`` as the durable
    mirror.
    """
    del dictCtx
    sName = _fsValidatedContainerName(dictRequest)
    if not sName:
        return _fdictRefusal("a valid sContainerName is required")
    sExpectedOperationId = dictRequest.get("sExpectedOperationId", "")
    if not sExpectedOperationId or not isinstance(sExpectedOperationId, str):
        return _fdictRefusal(
            "force-abandon requires sExpectedOperationId — the wedged "
            "operation's journal/task id, so a stale request cannot "
            "poison a successor operation"
        )
    recordOwner = app.state.dictContainerOwners.get(sName)
    if recordOwner is None:
        return _fdictRefusal(
            f"this hub holds no owner record for container '{sName}'"
        )
    sTaskHandleId = _fsMatchAbandonTarget(
        app.state, sName, sExpectedOperationId,
    )
    if sTaskHandleId is None:
        return _fdictRefusal(
            f"no operation {sExpectedOperationId!r} is live or "
            f"journaled in container '{sName}'; a stale force-abandon "
            "may not poison a successor operation"
        )
    listFenced = containerOwnership.flistPoisonAndFenceConnections(
        recordOwner,
        containerOwnership.PoisonRecord(
            sGuardedOperationId=sExpectedOperationId,
            sContainerId=recordOwner.sContainerId,
            sTaskHandleId=sTaskHandleId,
            fAbandonedMonotonic=time.monotonic(),
        ),
        getattr(app.state, "dictSessionSockets", None),
    )
    sessionLifecycle.fnScheduleConnectionFencing(listFenced)
    _fnSignalWedgedSupervisors(app.state, sName, sExpectedOperationId)
    bDurableMirrorWritten = _fbMirrorPoisonIntoJournal(
        sName, sExpectedOperationId,
    )
    return {
        "bAccepted": True,
        "bPoisoned": True,
        "bDurableMirrorWritten": bDurableMirrorWritten,
    }


def _fbIsStringList(listValue):
    """Return True for a non-empty list of non-empty strings."""
    return (
        isinstance(listValue, list)
        and len(listValue) > 0
        and all(
            isinstance(sEntry, str) and sEntry for sEntry in listValue
        )
    )


def _fsMatchAbandonTarget(appState, sName, sExpectedOperationId):
    """Return the matched task handle id, or None when the id is stale.

    The id matches a live mode-(b) supervisor's journal operation, a
    live mode-(c) durable task's stable id, or an existing journal
    record (a worker whose supervisor already exited but whose record
    stands). '' is a valid handle for the journal-only match.
    """
    for supervisor in getattr(
        appState, "dictMutationSupervisors", {},
    ).values():
        if supervisor.sName == sName and (
            supervisor.sOperationId == sExpectedOperationId
        ):
            return supervisor.sSupervisorId
    recordTask = getattr(appState, "dictDurableTaskRecords", {}).get(sName)
    if recordTask is not None and (
        recordTask.sTaskId == sExpectedOperationId
    ):
        return recordTask.sTaskId
    dictOutcomeRead = operationJournal.fdictReadJournalOutcome(sName)
    if sExpectedOperationId in dictOutcomeRead["dictOperations"]:
        return ""
    return None


def _fnSignalWedgedSupervisors(appState, sName, sExpectedOperationId):
    """Set the cancel flag and terminate hook on the matched supervisor."""
    for supervisor in list(getattr(
        appState, "dictMutationSupervisors", {},
    ).values()):
        if supervisor.sName != sName:
            continue
        if supervisor.sOperationId != sExpectedOperationId:
            continue
        supervisor.eventCancelRequested.set()
        if supervisor.fnTerminateWorker is not None:
            supervisor.fnTerminateWorker()


def _fbMirrorPoisonIntoJournal(sName, sExpectedOperationId):
    """Mark the journal record NEEDS_RECONCILIATION; report honestly."""
    try:
        operationJournal.fnMarkOperationNeedsReconciliation(
            sName, sExpectedOperationId,
            sNote="host force-abandon; worker termination unproven",
        )
        return True
    except operationJournal.OperationJournalError as error:
        logger.warning(
            "Force-abandon could not mirror poison into the journal "
            "for container '%s': %s", sName, error,
        )
        return False


async def _fdictHandleBreakGlass(app, dictCtx, dictRequest):
    """Clear a MALFORMED journal marker by its raw-bytes hash (case 46)."""
    sName = _fsValidatedContainerName(dictRequest)
    if not sName:
        return _fdictRefusal("a valid sContainerName is required")
    sMarkerSha256 = dictRequest.get("sMarkerSha256", "")
    if not sMarkerSha256 or not isinstance(sMarkerSha256, str):
        return _fdictRefusal(
            "break-glass requires sMarkerSha256 — the sha256 of the raw "
            "marker bytes, so a replacement record written since the "
            "inspection is never cleared"
        )
    if not _fbHubHoldsContainerFlock(app.state, sName):
        return _fdictRefusal(_fsNotHeldHereRefusal(sName))
    lockMutation = sessionLifecycle.flockContainerMutationForAppState(
        app.state, sName,
    )
    try:
        await asyncio.wait_for(
            lockMutation.acquire(), F_RECONCILE_DRAIN_WAIT_SECONDS,
        )
    except asyncio.TimeoutError:
        return _fdictRefusal(
            f"a guarded mutation still holds container '{sName}'s drain"
        )
    try:
        dictOutcome = await asyncio.to_thread(
            reconciliation.fdictExecuteBreakGlass,
            sName, sMarkerSha256, _fbStopContainerByNameProven,
            0, False,
        )
    except reconciliation.ReconciliationRefusedError as error:
        return _fdictRefusal(str(error))
    finally:
        lockMutation.release()
    return {"bAccepted": True, "bCleared": dictOutcome["bCleared"]}


async def _fdictHandleAbandonHostJournal(app, dictCtx, dictRequest):
    """Give up on proving a HOST project's malformed marker, recorded.

    The same shape as the break-glass and deliberately a separate
    opcode rather than a mode branch inside it: what the two do is not
    the same act. The break-glass stops a container and clears a marker
    it has therefore made safe; this one clears a marker on a
    researcher's assertion and writes down whose assertion it was. A
    single opcode that silently did one or the other depending on a
    registry lookup would make the audited path reachable by accident.
    """
    del dictCtx
    sName = _fsValidatedContainerName(dictRequest)
    if not sName:
        return _fdictRefusal("a valid sContainerName is required")
    sMarkerSha256 = dictRequest.get("sMarkerSha256", "")
    if not sMarkerSha256 or not isinstance(sMarkerSha256, str):
        return _fdictRefusal(
            "abandon-host-journal requires sMarkerSha256 — the sha256 of "
            "the raw marker bytes, so a replacement record written since "
            "the inspection is never cleared"
        )
    if not _fbHubHoldsContainerFlock(app.state, sName):
        return _fdictRefusal(_fsNotHeldHereRefusal(sName))
    lockMutation = sessionLifecycle.flockContainerMutationForAppState(
        app.state, sName,
    )
    try:
        await asyncio.wait_for(
            lockMutation.acquire(), F_RECONCILE_DRAIN_WAIT_SECONDS,
        )
    except asyncio.TimeoutError:
        return _fdictRefusal(
            f"a guarded mutation still holds container '{sName}'s drain"
        )
    try:
        dictOutcome = await asyncio.to_thread(
            reconciliation.fdictAbandonHostJournal,
            sName, sMarkerSha256, 0, False,
        )
    except reconciliation.ReconciliationRefusedError as error:
        return _fdictRefusal(str(error))
    finally:
        lockMutation.release()
    return {"bAccepted": True, "bCleared": dictOutcome["bCleared"]}


def _fbStopContainerByNameProven(sContainerName):
    """Stop the named container and PROVE it stopped or absent.

    Absence is a settlement; an unreachable daemon is not. The
    break-glass refuses on anything unproven rather than deleting a
    marker whose writer may still be running.
    """
    from vaibify.docker.containerManager import fbStopContainerProvenSettled
    return fbStopContainerProvenSettled(sContainerName)


async def _fdictHandleMintTransfer(app, dictCtx, dictRequest):
    """Describe or mint an ARMED transfer capability (design §6b, slice 5).

    The two-step handshake that keeps a stale CLI from ever
    transferring a successor generation without seeing it: a request
    WITHOUT ``iExpectedOwnerGeneration`` is a DESCRIBE — the hub
    answers with the current owner generation and mints nothing — and
    a request WITH it mints only while it still equals the live
    generation. The minted capability is itself bound to that
    generation, which :func:`sessionLifecycle.ftTransferOwnership`
    re-verifies under the drain and again at its synchronous commit
    point. The reply carries the capability only — never a lease or a
    browser credential.
    """
    del dictCtx
    sName = _fsValidatedContainerName(dictRequest)
    if not sName:
        return _fdictRefusal("a valid sContainerName is required")
    recordOwner = app.state.dictContainerOwners.get(sName)
    if recordOwner is None:
        return dict(
            _fdictRefusal(
                f"container '{sName}' is not owned by this hub; there "
                "is no session to transfer — claim it normally from "
                "the dashboard"
            ),
            bUnowned=True,
        )
    valueExpectedGeneration = dictRequest.get("iExpectedOwnerGeneration")
    if valueExpectedGeneration is None:
        return {
            "bAccepted": True,
            "bMinted": False,
            "iCurrentOwnerGeneration": recordOwner.iOwnerGeneration,
        }
    if (
        not isinstance(valueExpectedGeneration, int)
        or isinstance(valueExpectedGeneration, bool)
        or valueExpectedGeneration <= 0
    ):
        return _fdictRefusal(
            "iExpectedOwnerGeneration must be a positive integer; omit "
            "it to be told the current generation first"
        )
    if valueExpectedGeneration != recordOwner.iOwnerGeneration:
        return _fdictRefusal(
            f"container '{sName}' is at owner generation "
            f"{recordOwner.iOwnerGeneration}, not "
            f"{valueExpectedGeneration}; the owner changed since it was "
            "described — run 'vaibify open' again"
        )
    dictStore = getattr(app.state, "dictBrowserSessions", None)
    if dictStore is None:
        return _fdictRefusal(
            "this hub serves no browser-session store, so a transfer "
            "capability cannot be minted"
        )
    sCapability = browserSession.fsMintTransferCapability(
        dictStore, sName, valueExpectedGeneration,
    )
    return {
        "bAccepted": True,
        "bMinted": True,
        "sTransferCapability": sCapability,
        "iExpectedOwnerGeneration": valueExpectedGeneration,
    }


async def _fdictHandleMintBootstrap(app, dictCtx, dictRequest):
    """Mint an ORDINARY launch capability for a headless client (slice 8).

    The credential half of ``vaibify do``: the researcher's own
    terminal needs the same per-browser credential a dashboard tab
    holds, and the hub's only mint points are the browser launch and
    this socket. Peer authentication has already proved the caller is
    the hub's own uid, which is the whole authorization — a container
    cannot reach this socket, and a remote peer cannot either.

    What it deliberately is NOT: a transfer. The capability carries no
    container name and no owner generation, so redeeming it displaces
    nobody and bumps no generation. A host-lane caller that wants a
    container must claim it afterwards exactly as a browser does, and
    is refused 409 while a dashboard holds it.
    """
    del dictCtx
    # A remote helper says so when it mints. Nothing else may: the flag
    # widens a session's hold window, and the only process entitled to
    # claim it is the one serving a browser on another machine -- which
    # reaches this socket only by already being this uid on this host.
    bRemoteSession = bool(dictRequest.get("bRemoteSession"))
    dictStore = getattr(app.state, "dictBrowserSessions", None)
    if dictStore is None:
        return _fdictRefusal(
            "this hub serves no browser-session store, so a bootstrap "
            "capability cannot be minted"
        )
    return {
        "bAccepted": True,
        "bMinted": True,
        "sBootstrapCapability": browserSession.fsMintBootstrapCapability(
            dictStore, bRemoteSession,
        ),
    }

async def _fdictHandleListReattachable(app, dictCtx, dictRequest):
    """Name the sessions on this hub whose browser went away.

    A researcher whose tunnel dropped for longer than the hold window
    comes back to a project that is still THEIRS -- the record keeps
    its flock and its running work -- but to a credential that has been
    revoked. Reclaiming it is a TRANSFER, and a transfer has to name a
    container, which a returning client cannot know: it never sent one,
    because the project is chosen in the dashboard after the tunnel is
    up.

    So the hub is asked. Only ORPHANED records are listed: an ACTIVE
    one has a browser attending it right now, and handing that away on
    a reconnect would evict a live session rather than resume a dead
    one.

    Naming rather than choosing is deliberate. This answers "what is
    reattachable"; whether to reattach, and what to do about more than
    one candidate, belongs to the caller -- which can ask a human.
    """
    del dictCtx, dictRequest
    listReattachable = [
        {
            "sContainerName": sName,
            "iOwnerGeneration": recordOwner.iOwnerGeneration,
        }
        for sName, recordOwner in getattr(
            app.state, "dictContainerOwners", {},
        ).items()
        if recordOwner.sState == (
            containerOwnership.S_OWNER_STATE_ORPHANED_SESSION
        )
    ]
    return {"bAccepted": True, "listReattachable": listReattachable}



_DICT_SOCKET_OPERATION_HANDLERS = {
    S_SOCKET_OPERATION_RECONCILE: _fdictHandleReconcile,
    S_SOCKET_OPERATION_FORCE_ABANDON: _fdictHandleForceAbandon,
    S_SOCKET_OPERATION_BREAK_GLASS: _fdictHandleBreakGlass,
    S_SOCKET_OPERATION_ABANDON_HOST_JOURNAL:
        _fdictHandleAbandonHostJournal,
    S_SOCKET_OPERATION_MINT_TRANSFER: _fdictHandleMintTransfer,
    S_SOCKET_OPERATION_MINT_BOOTSTRAP: _fdictHandleMintBootstrap,
    S_SOCKET_OPERATION_LIST_REATTACHABLE:
        _fdictHandleListReattachable,
}


# ---------------------------------------------------------------------
# The host-side client (the vaibify reconcile CLI).
# ---------------------------------------------------------------------

def fdictSendHostControlRequest(iPort, dictRequest, fTimeoutSeconds=30.0):
    """Send one request to a hub's control socket; return its response.

    Skips (and names) a stale socket instead of hanging on it: the path
    must exist and be a socket, and a refused connection reports the
    hub as gone rather than retrying.
    """
    sPath = fsControlSocketPathForPort(iPort)
    if not _fbLstatIsSocket(sPath):
        raise HostControlError(
            f"No host-control socket exists for hub port {iPort}; is "
            "that hub still running?"
        )
    socketClient = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        socketClient.settimeout(fTimeoutSeconds)
        try:
            socketClient.connect(sPath)
        except (ConnectionRefusedError, FileNotFoundError, OSError):
            raise HostControlError(
                f"The host-control socket for hub port {iPort} is stale "
                "or unreachable; is that hub still running?"
            )
        try:
            socketClient.sendall(
                json.dumps(dictRequest).encode("utf-8") + b"\n",
            )
            byteResponse = _fbaReadResponseLine(socketClient)
        except (ConnectionResetError, BrokenPipeError):
            # The hub closed on us without answering -- which is exactly
            # what it does to a peer it will not serve. macOS reports
            # that as EOF, so the reader below returns b"" and the
            # unreadable-response branch handles it; Linux sends RST
            # when a socket is closed with unread data still in its
            # receive buffer, so the same refusal arrives here as an
            # exception instead. Both are the same event and must give
            # the researcher the same sentence, not a raw traceback on
            # one platform and a clean message on the other.
            raise HostControlError(
                "The hub's host-control response was unreadable."
            )
    finally:
        socketClient.close()
    try:
        dictResponse = json.loads(byteResponse.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        raise HostControlError(
            "The hub's host-control response was unreadable."
        )
    if not isinstance(dictResponse, dict):
        raise HostControlError(
            "The hub's host-control response was not an object."
        )
    return dictResponse


def _fbaReadResponseLine(socketClient):
    """Read one bounded response line (closed connection ends it)."""
    listChunks = []
    iTotalBytes = 0
    while iTotalBytes <= _I_MAXIMUM_MESSAGE_BYTES:
        try:
            byteChunk = socketClient.recv(4096)
        except socket.timeout:
            raise HostControlError(
                "Timed out waiting for the hub's host-control response."
            )
        if not byteChunk:
            break
        listChunks.append(byteChunk)
        iTotalBytes += len(byteChunk)
        if byteChunk.endswith(b"\n"):
            break
    return b"".join(listChunks)
