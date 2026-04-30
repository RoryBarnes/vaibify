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
process exits — no stale-slot maintenance is needed.
"""

import datetime
import fcntl
import json
import os


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
