"""Per-container exclusive lock at ~/.vaibify/locks/<name>.lock.

Each vaibify FastAPI process that attaches to a container holds an
exclusive ``fcntl.flock`` on a lock file whose contents identify the
holder (PID, port, project name). The kernel releases the flock
automatically if the process exits, so a crashed vaibify instance
does not permanently block its container. Consumers:

- ``fappCreateApplication`` (workflow viewer) acquires on startup,
  releases on shutdown — one container per process.
- ``fappCreateHubApplication`` (hub) acquires when a browser client
  opens a container, releases when the client navigates back or the
  hub exits.
- ``/api/registry`` reads holder info to report which containers are
  already being used elsewhere.
"""

import datetime
import fcntl
import json
import os
import re

from vaibify.config.processLiveness import fbIsProcessAlive


_S_LOCK_DIRECTORY = os.path.expanduser("~/.vaibify/locks")
_RE_VALID_PROJECT_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_I_MAX_ACQUIRE_ATTEMPTS = 3


class ContainerLockedError(RuntimeError):
    """Raised when another vaibify process already holds the lock."""

    def __init__(self, sProjectName, iHolderPid, iHolderPort):
        self.sProjectName = sProjectName
        self.iHolderPid = iHolderPid
        self.iHolderPort = iHolderPort
        sMessage = (
            f"Container '{sProjectName}' is already accessed by "
            f"vaibify pid={iHolderPid} on port={iHolderPort}."
        )
        super().__init__(sMessage)


class InvalidProjectNameError(ValueError):
    """Raised when a project name fails validation for lock operations."""


def fbIsValidProjectName(sProjectName):
    """Return True if the name is safe to use in the lock file path."""
    if not isinstance(sProjectName, str):
        return False
    if sProjectName in ("", ".", ".."):
        return False
    if _RE_VALID_PROJECT_NAME.match(sProjectName) is None:
        return False
    return True


def _fnValidateProjectName(sProjectName):
    """Raise InvalidProjectNameError when the name is unsafe."""
    if not fbIsValidProjectName(sProjectName):
        raise InvalidProjectNameError(
            f"Invalid project name for lock: {sProjectName!r}"
        )


def fsLockPathFor(sProjectName):
    """Return the lock file path for a given container name."""
    _fnValidateProjectName(sProjectName)
    return os.path.join(_S_LOCK_DIRECTORY, f"{sProjectName}.lock")


def _fnEnsureLockDirectory():
    """Create ~/.vaibify/locks/ with mode 0o700 if missing."""
    os.makedirs(_S_LOCK_DIRECTORY, mode=0o700, exist_ok=True)
    try:
        os.chmod(_S_LOCK_DIRECTORY, 0o700)
    except OSError:
        pass


def _fdictBuildHolderPayload(sProjectName, iPort):
    """Return the JSON-serializable holder info for a new claim."""
    return {
        "iPid": os.getpid(),
        "iPort": iPort,
        "sStartedIso": datetime.datetime.now().isoformat(),
        "sProjectName": sProjectName,
    }


def _fnWriteHolderPayload(fileHandle, dictPayload):
    """Truncate and rewrite the lock file with the holder payload."""
    fileHandle.seek(0)
    fileHandle.truncate()
    fileHandle.write(json.dumps(dictPayload, indent=2))
    fileHandle.flush()


def _ffileOpenLockFileNoFollow(sPath):
    """Open the lock path with O_NOFOLLOW so symlinks are rejected."""
    iFileDescriptor = os.open(
        sPath,
        os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW,
        0o600,
    )
    return os.fdopen(iFileDescriptor, "r+")


def fnAcquireContainerLock(sProjectName, iPort):
    """Acquire an exclusive lock on the container and return the fd.

    Raises ``InvalidProjectNameError`` if ``sProjectName`` is unsafe,
    or ``ContainerLockedError`` if a live process holds the lock. A
    claim whose recorded holder PID no longer exists is reaped and
    taken over silently. The returned file handle must be kept open
    for the duration of the claim; passing it to
    ``fnReleaseContainerLock`` releases it.
    """
    _fnValidateProjectName(sProjectName)
    _fnEnsureLockDirectory()
    sPath = fsLockPathFor(sProjectName)
    for _ in range(_I_MAX_ACQUIRE_ATTEMPTS):
        fileHandle = _ffileTryAcquireFlock(sPath, sProjectName, iPort)
        if fileHandle is not None:
            return fileHandle
    raise ContainerLockedError(sProjectName, 0, 0)


def _ffileTryAcquireFlock(sPath, sProjectName, iPort):
    """Attempt one flock acquisition; return None when a retry is due.

    Raises ``ContainerLockedError`` when a live process holds the
    flock. Returns None after reaping a dead holder's lock file, or
    when the locked inode was unlinked by a concurrent reaper. The
    holder payload is written before the inode check so a concurrent
    reaper sees a live PID as early as possible.
    """
    fileHandle = _ffileOpenLockFileNoFollow(sPath)
    try:
        fcntl.flock(fileHandle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        _fnReapDeadHolderOrRaise(fileHandle, sPath, sProjectName)
        return None
    _fnWriteHolderPayload(
        fileHandle, _fdictBuildHolderPayload(sProjectName, iPort),
    )
    if _fbHandleMatchesPath(fileHandle, sPath):
        return fileHandle
    fileHandle.close()
    return None


def _fnReapDeadHolderOrRaise(fileHandle, sPath, sProjectName):
    """Unlink a dead holder's lock file or raise for a live holder."""
    dictHolder = _fdictReadHolderFromHandle(fileHandle)
    fileHandle.close()
    if not _fbClaimIsStale(dictHolder):
        raise ContainerLockedError(
            sProjectName,
            dictHolder.get("iPid", 0),
            dictHolder.get("iPort", 0),
        )
    _fnUnlinkLockFileSafely(sPath)


def _fbClaimIsStale(dictHolder):
    """Return True when the recorded holder process has exited.

    A payload without a positive integer PID is treated as live: it
    may belong to a holder that flocked but has not yet written its
    identity, and breaking that lock would race a real acquisition.
    """
    iPid = dictHolder.get("iPid", 0)
    if not isinstance(iPid, int) or isinstance(iPid, bool) or iPid <= 0:
        return False
    return not fbIsProcessAlive(iPid)


def _fbHandleMatchesPath(fileHandle, sPath):
    """Return True when the open handle is still the file at sPath."""
    try:
        statHandle = os.fstat(fileHandle.fileno())
        statPath = os.stat(sPath)
    except OSError:
        return False
    return (statHandle.st_ino, statHandle.st_dev) == (
        statPath.st_ino, statPath.st_dev,
    )


def _fnUnlinkLockFileSafely(sPath):
    """Remove a stale lock file, ignoring races with other reapers."""
    try:
        os.unlink(sPath)
    except OSError:
        pass


def fnReapStaleContainerLocks():
    """Remove lock files whose recorded holder process has exited.

    Called at hub startup and on every container-list refresh so a
    claim orphaned by a killed vaibify server (its flock leaked into
    a surviving file descriptor) never blocks a fresh session. Live
    claims are never touched.
    """
    if not os.path.isdir(_S_LOCK_DIRECTORY):
        return
    try:
        listEntries = os.listdir(_S_LOCK_DIRECTORY)
    except OSError:
        return
    for sEntry in listEntries:
        if sEntry.endswith(".lock"):
            _fnReapLockFileIfStale(
                os.path.join(_S_LOCK_DIRECTORY, sEntry),
            )


def _fnReapLockFileIfStale(sPath):
    """Unlink one lock file when its recorded holder is dead."""
    try:
        fileHandle = _ffileOpenLockFileNoFollow(sPath)
    except OSError:
        return
    try:
        dictHolder = _fdictReadHolderFromHandle(fileHandle)
    finally:
        fileHandle.close()
    if _fbClaimIsStale(dictHolder):
        _fnUnlinkLockFileSafely(sPath)


def fnReleaseContainerLock(fileHandle):
    """Release a lock previously acquired via fnAcquireContainerLock."""
    try:
        fcntl.flock(fileHandle, fcntl.LOCK_UN)
    finally:
        fileHandle.close()


def _fdictReadHolderFromHandle(fileHandle):
    """Best-effort read of holder JSON from an open lock file."""
    try:
        fileHandle.seek(0)
        sContent = fileHandle.read()
        if not sContent:
            return {}
        return json.loads(sContent)
    except (json.JSONDecodeError, OSError):
        return {}


def fdictReadLockHolder(sProjectName):
    """Return holder info if another live process holds the lock.

    Returns an empty dict when the lock file is absent, stale, held
    by the current process (comparing against ``os.getpid()``), or
    the name fails validation. A held flock whose recorded holder
    PID is dead is reaped on the spot and reported as unheld, so a
    container-list refresh recovers orphaned claims by itself.
    """
    if not fbIsValidProjectName(sProjectName):
        return {}
    sPath = fsLockPathFor(sProjectName)
    if not os.path.isfile(sPath):
        return {}
    try:
        fileHandle = _ffileOpenLockFileNoFollow(sPath)
    except OSError:
        return {}
    try:
        try:
            fcntl.flock(fileHandle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return _fdictHolderUnlessStale(fileHandle, sPath)
        fcntl.flock(fileHandle, fcntl.LOCK_UN)
        return {}
    finally:
        fileHandle.close()


def _fdictHolderUnlessStale(fileHandle, sPath):
    """Return the holder payload, reaping it first when its PID is dead."""
    dictHolder = _fdictReadHolderFromHandle(fileHandle)
    if dictHolder.get("iPid") == os.getpid():
        return {}
    if _fbClaimIsStale(dictHolder):
        _fnUnlinkLockFileSafely(sPath)
        return {}
    return dictHolder
