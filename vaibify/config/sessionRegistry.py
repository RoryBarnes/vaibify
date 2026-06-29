"""Global cap on concurrent vaibify GUI sessions on one host.

Each vaibify hub (``vaibify``) and workflow-viewer (``vaibify start
--gui``) acquires a session slot at startup and releases it on
shutdown. If the number of live slots already equals
``I_MAX_SESSIONS`` at acquire time, the new process exits with a
clear message instead of silently joining the crowd. This guards
against accidental fork-bombs from scripts that loop
``vaibify start`` or call ``/api/session/spawn`` in tight cycles.

Slot presence is represented by an open file descriptor under
``~/.vaibify/sessions/`` with a non-blocking ``fcntl.flock``, so
the kernel releases the slot automatically when the owning
process exits. The slot *files* of killed processes are not
removed by the kernel, so every acquisition first reaps files
whose name-encoded PID no longer exists.

The file mechanism (no-follow open, payload read/write, 0o700
directory, stale reaper) lives in ``pidFileRegistry``; this module is
a thin wrapper that owns only the slot's divergent payload schema
``{iPid, sRole, iPort, sStartedIso}`` and the host-wide session cap.
"""

import datetime
import fcntl
import os

from vaibify.config import pidFileRegistry
from vaibify.config.processLiveness import fbIsProcessAliveSince, fbIsUsablePid


I_MAX_SESSIONS = 99
_S_SESSION_DIRECTORY = os.path.expanduser("~/.vaibify/sessions")
_S_SLOT_SUFFIX = ".slot"


class SessionLimitExceededError(RuntimeError):
    """Raised when acquiring a slot would exceed I_MAX_SESSIONS."""

    def __init__(self, iActive, iLimit):
        self.iActive = iActive
        self.iLimit = iLimit
        super().__init__(
            f"Vaibify session limit reached: {iActive}/{iLimit} "
            f"already running. Close an existing session or raise "
            f"the limit."
        )


def _fnEnsureSessionDirectory():
    """Create ~/.vaibify/sessions/ with mode 0o700 if missing."""
    pidFileRegistry.fnEnsureDirectory(_S_SESSION_DIRECTORY)


def _fsSlotPathForCurrentProcess():
    """Return the slot-file path keyed on the current pid."""
    return os.path.join(
        _S_SESSION_DIRECTORY, f"{os.getpid()}{_S_SLOT_SUFFIX}",
    )


def fiCountActiveSessions():
    """Return the number of session slots currently held by live procs."""
    iCount = 0
    for sPath in pidFileRegistry.flistRegistryFiles(
        _S_SESSION_DIRECTORY, _S_SLOT_SUFFIX,
    ):
        if _fbSlotIsHeldByLiveProcess(sPath):
            iCount += 1
    return iCount


def _fbSlotIsHeldByLiveProcess(sPath):
    """Return True when the slot file's flock is currently held."""
    try:
        fileHandle = open(sPath, "r+")
    except OSError:
        return False
    try:
        try:
            fcntl.flock(fileHandle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return True
        fcntl.flock(fileHandle, fcntl.LOCK_UN)
        return False
    finally:
        fileHandle.close()


def fnAcquireSessionSlot(sRole, iPort):
    """Acquire a session slot and return its open file handle.

    Raises ``SessionLimitExceededError`` when the count of live slots
    is already at ``I_MAX_SESSIONS``. The caller must keep the
    returned handle open for the lifetime of the session; passing it
    to ``fnReleaseSessionSlot`` releases and removes the slot file.
    """
    _fnEnsureSessionDirectory()
    fnReapStaleSessionSlots()
    iActive = fiCountActiveSessions()
    if iActive >= I_MAX_SESSIONS:
        raise SessionLimitExceededError(iActive, I_MAX_SESSIONS)
    sPath = _fsSlotPathForCurrentProcess()
    fileHandle = pidFileRegistry.ffileOpenNoFollow(sPath)
    fcntl.flock(fileHandle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    _fnWriteSlotPayload(fileHandle, sRole, iPort)
    return fileHandle


def _fnWriteSlotPayload(fileHandle, sRole, iPort):
    """Serialize the slot-holder identity into the open slot file."""
    dictPayload = {
        "iPid": os.getpid(),
        "sRole": sRole,
        "iPort": iPort,
        "sStartedIso": datetime.datetime.now().isoformat(),
    }
    pidFileRegistry.fnWritePayload(fileHandle, dictPayload)


def fnReleaseSessionSlot(fileHandle):
    """Release the flock, close the fd, and remove the slot file."""
    sPath = _fsSlotPathForCurrentProcess()
    try:
        fcntl.flock(fileHandle, fcntl.LOCK_UN)
    finally:
        fileHandle.close()
    pidFileRegistry.fnUnlinkQuietly(sPath)


def fnReapStaleSessionSlots():
    """Remove slot files whose name-encoded PID no longer exists.

    Slot files are keyed on the owning PID, so a dead PID means no
    process can ever release the file; reaping is the only path
    that removes it. Files named for live PIDs are never touched,
    even when their flock looks free (the owner may be
    mid-acquisition).
    """
    pidFileRegistry.fnReapStaleFilesIn(
        _S_SESSION_DIRECTORY, _fbSlotFileIsStale, _S_SLOT_SUFFIX,
    )


def _fbSlotFileIsStale(sPath):
    """Return True when a slot file's recorded holder process has exited.

    The slot payload's ``iPid`` and ``sStartedIso`` drive a
    recycle-proof liveness check; a payload without a usable PID falls
    back to the PID encoded in the file name. A payload missing
    ``sStartedIso`` behaves exactly like the bare PID-existence check.
    """
    dictPayload = _fdictReadSlotPayload(sPath)
    iPid = dictPayload.get("iPid")
    if not fbIsUsablePid(iPid):
        iPid = _fiPidFromSlotPath(sPath)
    return not fbIsProcessAliveSince(iPid, dictPayload.get("sStartedIso"))


def _fdictReadSlotPayload(sPath):
    """Best-effort read of slot-holder JSON, or {} on any error."""
    return pidFileRegistry.fdictReadPayload(sPath)


def _fiPidFromSlotPath(sPath):
    """Return the PID encoded in a slot file name, or 0 when malformed."""
    sBaseName = os.path.basename(sPath)
    try:
        return int(sBaseName[: -len(_S_SLOT_SUFFIX)])
    except ValueError:
        return 0


def flistReadAllSlots():
    """Return a payload dict for every live session slot on the host.

    Each entry is ``{iPid, sRole, iPort, sStartedIso, bAlive}`` where
    ``bAlive`` reflects the flock truth from ``_fbSlotIsHeldByLiveProcess``.
    Used by ``vaibify sessions`` to list live hubs and viewers. Returns
    an empty list on any directory error so the CLI never crashes on a
    registry mishap.
    """
    listSlots = []
    for sPath in sorted(pidFileRegistry.flistRegistryFiles(
        _S_SESSION_DIRECTORY, _S_SLOT_SUFFIX,
    )):
        _fnAppendSlotRecord(listSlots, sPath)
    return listSlots


def _fnAppendSlotRecord(listSlots, sPath):
    """Append a live slot's normalized record to listSlots."""
    if not _fbSlotIsHeldByLiveProcess(sPath):
        return
    dictPayload = _fdictReadSlotPayload(sPath)
    iPid = dictPayload.get("iPid")
    if not fbIsUsablePid(iPid):
        iPid = _fiPidFromSlotPath(sPath)
    listSlots.append({
        "iPid": iPid,
        "sRole": dictPayload.get("sRole"),
        "iPort": dictPayload.get("iPort"),
        "sStartedIso": dictPayload.get("sStartedIso"),
        "bAlive": True,
    })


def fdictReadHubSlotByPort(iPort):
    """Return the slot payload of a live hub holding iPort, or {}.

    Used by the port allocator to distinguish "the port I want is held
    by my own dying hub" (worth a brief wait) from "it's held by an
    unrelated process" (must scan or fail). Scans every slot file,
    skips those whose flock has been released (dead process), and
    returns the first match where ``sRole == "hub"`` and
    ``iPort == iPort``. Returns ``{}`` on any error so the allocator
    never blocks the launch on a registry mishap.
    """
    for sPath in pidFileRegistry.flistRegistryFiles(
        _S_SESSION_DIRECTORY, _S_SLOT_SUFFIX,
    ):
        dictMatch = _fdictMatchingHubSlot(sPath, iPort)
        if dictMatch:
            return dictMatch
    return {}


def _fdictMatchingHubSlot(sPath, iPort):
    """Return the slot payload if alive, hub-role, and on iPort; else {}."""
    if not _fbSlotIsHeldByLiveProcess(sPath):
        return {}
    dictPayload = _fdictReadSlotPayload(sPath)
    if dictPayload.get("sRole") != "hub":
        return {}
    if dictPayload.get("iPort") != iPort:
        return {}
    return dictPayload
