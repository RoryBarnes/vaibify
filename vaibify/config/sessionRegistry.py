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
"""

import datetime
import fcntl
import json
import os

from vaibify.config.processLiveness import fbIsProcessAlive


I_MAX_SESSIONS = 99
_S_SESSION_DIRECTORY = os.path.expanduser("~/.vaibify/sessions")


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
    os.makedirs(_S_SESSION_DIRECTORY, mode=0o700, exist_ok=True)
    try:
        os.chmod(_S_SESSION_DIRECTORY, 0o700)
    except OSError:
        pass


def _fsSlotPathForCurrentProcess():
    """Return the slot-file path keyed on the current pid."""
    return os.path.join(
        _S_SESSION_DIRECTORY, f"{os.getpid()}.slot",
    )


def fiCountActiveSessions():
    """Return the number of session slots currently held by live procs."""
    if not os.path.isdir(_S_SESSION_DIRECTORY):
        return 0
    iCount = 0
    for sEntry in os.listdir(_S_SESSION_DIRECTORY):
        if not sEntry.endswith(".slot"):
            continue
        sPath = os.path.join(_S_SESSION_DIRECTORY, sEntry)
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
    fileHandle = _ffileOpenSlotNoFollow(sPath)
    fcntl.flock(fileHandle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    _fnWriteSlotPayload(fileHandle, sRole, iPort)
    return fileHandle


def _ffileOpenSlotNoFollow(sPath):
    """Open the slot path with O_NOFOLLOW to reject symlinks."""
    iFileDescriptor = os.open(
        sPath,
        os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW,
        0o600,
    )
    return os.fdopen(iFileDescriptor, "r+")


def _fnWriteSlotPayload(fileHandle, sRole, iPort):
    """Serialize the slot-holder identity into the open slot file."""
    dictPayload = {
        "iPid": os.getpid(),
        "sRole": sRole,
        "iPort": iPort,
        "sStartedIso": datetime.datetime.now().isoformat(),
    }
    fileHandle.seek(0)
    fileHandle.truncate()
    fileHandle.write(json.dumps(dictPayload, indent=2))
    fileHandle.flush()


def fnReleaseSessionSlot(fileHandle):
    """Release the flock, close the fd, and remove the slot file."""
    sPath = _fsSlotPathForCurrentProcess()
    try:
        fcntl.flock(fileHandle, fcntl.LOCK_UN)
    finally:
        fileHandle.close()
    try:
        os.unlink(sPath)
    except OSError:
        pass


def fnReapStaleSessionSlots():
    """Remove slot files whose name-encoded PID no longer exists.

    Slot files are keyed on the owning PID, so a dead PID means no
    process can ever release the file; reaping is the only path
    that removes it. Files named for live PIDs are never touched,
    even when their flock looks free (the owner may be
    mid-acquisition).
    """
    if not os.path.isdir(_S_SESSION_DIRECTORY):
        return
    try:
        listEntries = os.listdir(_S_SESSION_DIRECTORY)
    except OSError:
        return
    for sEntry in listEntries:
        if sEntry.endswith(".slot"):
            _fnReapSlotFileIfStale(
                os.path.join(_S_SESSION_DIRECTORY, sEntry),
            )


def _fnReapSlotFileIfStale(sPath):
    """Unlink one slot file when its name-encoded PID is dead."""
    if fbIsProcessAlive(_fiPidFromSlotPath(sPath)):
        return
    try:
        os.unlink(sPath)
    except OSError:
        pass


def _fiPidFromSlotPath(sPath):
    """Return the PID encoded in a slot file name, or 0 when malformed."""
    sBaseName = os.path.basename(sPath)
    try:
        return int(sBaseName[: -len(".slot")])
    except ValueError:
        return 0


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
    if not os.path.isdir(_S_SESSION_DIRECTORY):
        return {}
    try:
        listEntries = os.listdir(_S_SESSION_DIRECTORY)
    except OSError:
        return {}
    for sEntry in listEntries:
        if not sEntry.endswith(".slot"):
            continue
        sPath = os.path.join(_S_SESSION_DIRECTORY, sEntry)
        dictMatch = _fdictMatchingHubSlot(sPath, iPort)
        if dictMatch:
            return dictMatch
    return {}


def _fdictMatchingHubSlot(sPath, iPort):
    """Return the slot payload if alive, hub-role, and on iPort; else {}."""
    if not _fbSlotIsHeldByLiveProcess(sPath):
        return {}
    try:
        with open(sPath, "r") as fileHandle:
            dictPayload = json.load(fileHandle)
    except (OSError, json.JSONDecodeError):
        return {}
    if dictPayload.get("sRole") != "hub":
        return {}
    if dictPayload.get("iPort") != iPort:
        return {}
    return dictPayload
