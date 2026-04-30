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


_S_LOCK_DIRECTORY = os.path.expanduser("~/.vaibify/locks")
_RE_VALID_PROJECT_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


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
    or ``ContainerLockedError`` if another process holds the lock.
    The returned file handle must be kept open for the duration of
    the claim; passing it to ``fnReleaseContainerLock`` releases it.
    """
    _fnValidateProjectName(sProjectName)
    _fnEnsureLockDirectory()
    sPath = fsLockPathFor(sProjectName)
    fileHandle = _ffileOpenLockFileNoFollow(sPath)
    try:
        fcntl.flock(fileHandle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        dictHolder = _fdictReadHolderFromHandle(fileHandle)
        fileHandle.close()
        raise ContainerLockedError(
            sProjectName,
            dictHolder.get("iPid", 0),
            dictHolder.get("iPort", 0),
        )
    _fnWriteHolderPayload(
        fileHandle, _fdictBuildHolderPayload(sProjectName, iPort),
    )
    return fileHandle


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
    """Return holder info if another process holds the lock.

    Returns an empty dict when the lock file is absent, stale, held
    by the current process (comparing against ``os.getpid()``), or
    the name fails validation.
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
            dictHolder = _fdictReadHolderFromHandle(fileHandle)
            if dictHolder.get("iPid") == os.getpid():
                return {}
            return dictHolder
        fcntl.flock(fileHandle, fcntl.LOCK_UN)
        return {}
    finally:
        fileHandle.close()
