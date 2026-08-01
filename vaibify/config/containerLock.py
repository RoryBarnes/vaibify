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

The file mechanism (no-follow open, payload read/write, 0o700
directory, stale reaper) lives in ``pidFileRegistry``; this module is
a thin wrapper that owns only the lock's divergent holder schema
``{iPid, iPort, sStartedIso, sProjectName}`` and the flock-arbitration
policy.
"""

import datetime
import fcntl
import os

from vaibify.config import operationJournal, pidFileRegistry
from vaibify.config.processLiveness import fbIsProcessAliveSince, fbIsUsablePid


_S_LOCK_DIRECTORY = os.path.expanduser("~/.vaibify/locks")
_S_LOCK_SUFFIX = ".lock"
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


class ContainerQuarantinedError(ContainerLockedError):
    """Raised when the operation journal quarantines a container.

    Subclasses :class:`ContainerLockedError` so every existing refusal
    path fails closed on a quarantine, while carrying the journal's
    reason instead of a holder PID (there is no live holder — the
    hazard is a PAST operation that cannot be proven settled).
    """

    def __init__(self, sProjectName, sReason):
        self.sProjectName = sProjectName
        self.iHolderPid = 0
        self.iHolderPort = 0
        self.sReason = sReason
        RuntimeError.__init__(
            self,
            f"Container '{sProjectName}' is quarantined: {sReason}. A "
            "journaled operation from a previous session could not be "
            "proven settled; reconciliation is required before this "
            "container can be claimed.",
        )


class ContainerBusyOperationError(ContainerLockedError):
    """Raised when a journaled operation's holder is still alive.

    The flock was free (its hub died), but a recorded worker — a helper
    process or a Docker exec — is provably still running, so the
    container is in use, not quarantined.
    """

    def __init__(self, sProjectName, listBusyOperationIds):
        self.sProjectName = sProjectName
        self.iHolderPid = 0
        self.iHolderPort = 0
        self.listBusyOperationIds = list(listBusyOperationIds)
        RuntimeError.__init__(
            self,
            f"Container '{sProjectName}' still has "
            f"{len(self.listBusyOperationIds)} journaled operation(s) "
            "running; it is in use until they finish.",
        )


class InvalidProjectNameError(ValueError):
    """Raised when a project name fails validation for lock operations."""


def fbIsValidProjectName(sProjectName):
    """Return True if the name is safe to use in the lock file path."""
    return pidFileRegistry.fbIsSafeRegistryName(sProjectName)


def _fnValidateProjectName(sProjectName):
    """Raise InvalidProjectNameError when the name is unsafe."""
    if not fbIsValidProjectName(sProjectName):
        raise InvalidProjectNameError(
            f"Invalid project name for lock: {sProjectName!r}"
        )


def fsLockPathFor(sProjectName):
    """Return the lock file path for a given container name."""
    _fnValidateProjectName(sProjectName)
    return os.path.join(_S_LOCK_DIRECTORY, f"{sProjectName}{_S_LOCK_SUFFIX}")


def _fnEnsureLockDirectory():
    """Create ~/.vaibify/locks/ with mode 0o700 if missing."""
    pidFileRegistry.fnEnsureDirectory(_S_LOCK_DIRECTORY)


def _fdictBuildHolderPayload(sProjectName, iPort):
    """Return the JSON-serializable holder info for a new claim."""
    return {
        "iPid": os.getpid(),
        "iPort": iPort,
        "sStartedIso": datetime.datetime.now().isoformat(),
        "sProjectName": sProjectName,
    }


def _ffileOpenLockFileNoFollow(sPath):
    """Open the lock path with O_NOFOLLOW so symlinks are rejected."""
    return pidFileRegistry.ffileOpenNoFollow(sPath)


def fnAcquireContainerLock(sProjectName, iPort, connectionDocker=None):
    """Acquire an exclusive lock on the container and return the fd.

    Raises ``InvalidProjectNameError`` if ``sProjectName`` is unsafe,
    or ``ContainerLockedError`` if a live process holds the lock. A
    claim whose recorded holder PID no longer exists is reaped and
    taken over silently — UNLESS the container's operation journal is
    unsettled: the journal is examined while the fresh flock is held
    (atomically with it), and a quarantined container is refused with
    ``ContainerQuarantinedError`` despite the freed flock, overriding
    the silent dead-PID reap. A journaled operation whose holder is
    provably alive refuses with ``ContainerBusyOperationError``.
    ``connectionDocker``, when available, lets the journal's automatic
    tier verify Docker-side operations instead of failing closed.

    The returned file handle must be kept open for the duration of the
    claim; passing it to ``fnReleaseContainerLock`` releases it.
    """
    _fnValidateProjectName(sProjectName)
    _fnEnsureLockDirectory()
    sPath = fsLockPathFor(sProjectName)
    for _ in range(_I_MAX_ACQUIRE_ATTEMPTS):
        fileHandle = _ffileTryAcquireFlock(sPath, sProjectName, iPort)
        if fileHandle is not None:
            return _ffileRefuseUnsettledJournal(
                fileHandle, sProjectName, connectionDocker,
            )
    raise ContainerLockedError(sProjectName, 0, 0)


def _ffileRefuseUnsettledJournal(fileHandle, sProjectName, connectionDocker):
    """Refuse a freshly-flocked container whose journal is not settled.

    Runs while the flock is held, so it can never race a normal
    completion clearing its own record. On refusal the flock is
    released before raising, leaving the container for reconciliation.
    """
    try:
        dictResolution = operationJournal.fdictResolveContainerJournal(
            sProjectName, connectionDocker,
        )
    except Exception:
        fnReleaseContainerLock(fileHandle)
        raise
    if dictResolution["sResolution"] == operationJournal.S_RESOLUTION_QUARANTINED:
        fnReleaseContainerLock(fileHandle)
        raise ContainerQuarantinedError(
            sProjectName, dictResolution["sQuarantineReason"],
        )
    if dictResolution["sResolution"] == operationJournal.S_RESOLUTION_BUSY:
        fnReleaseContainerLock(fileHandle)
        raise ContainerBusyOperationError(
            sProjectName, dictResolution["listBusyOperationIds"],
        )
    return fileHandle


def ffileAcquireReconciliationLock(sProjectName, iPort):
    """Acquire the exclusive reconciliation lock and return the handle.

    The reconciliation lock IS the same cross-process arbitration
    primitive the claim path uses — this container's flock — taken in
    the same stated order: both paths acquire the flock FIRST, then act
    on the journal (a claim examines it and refuses an unsettled set; a
    reconciliation proves the set settled and clears the marker LAST,
    before releasing). Serializing on one flock is what makes
    reconcile-versus-claim atomic (design §8, case 35): while a
    reconciliation holds it, a concurrent claim is refused as locked;
    while a live hub holds it, this acquisition raises
    ``ContainerLockedError`` and the caller must route the
    reconciliation to that hub over its host control socket instead.

    Unlike :func:`fnAcquireContainerLock`, the journal gate is NOT
    applied — refusing a quarantined container here would make every
    quarantine permanently unclearable.
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
    pidFileRegistry.fnWritePayload(
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
    pidFileRegistry.fnUnlinkQuietly(sPath)


def _fbClaimIsStale(dictHolder):
    """Return True when the recorded holder process has exited.

    A payload without a positive integer PID is treated as live: it
    may belong to a holder that flocked but has not yet written its
    identity, and breaking that lock would race a real acquisition.
    The recorded ``sStartedIso`` lets a recycled PID be detected; a
    payload missing it falls back to the bare PID-existence check.
    """
    iPid = dictHolder.get("iPid", 0)
    if not fbIsUsablePid(iPid):
        return False
    return not fbIsProcessAliveSince(iPid, dictHolder.get("sStartedIso"))


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


def fnReapStaleContainerLocks(connectionDocker=None):
    """Remove lock files whose recorded holder process has exited.

    Called at hub startup and on every container-list refresh so a
    claim orphaned by a killed vaibify server (its flock leaked into
    a surviving file descriptor) never blocks a fresh session. Live
    claims are never touched.

    Also runs the operation journal's automatic probe tier over every
    journaled container. Reaping frees only the FLOCK of a dead
    holder; a journal record that cannot be proven settled keeps the
    container quarantined regardless, because acquisition consults the
    journal itself. This pass exists so provably-dead, provably-settled
    leftovers are cleared (with a logged note) on the same cadence the
    lock reaper already runs: startup and each container-list refresh.
    """
    pidFileRegistry.fnReapStaleFilesIn(
        _S_LOCK_DIRECTORY, _fbLockFileIsStale, _S_LOCK_SUFFIX,
    )
    _fnAutoProbeJournaledContainers(connectionDocker)


def _fnAutoProbeJournaledContainers(connectionDocker):
    """Run the journal's automatic resolution for every journaled name."""
    for sProjectName in operationJournal.flistJournaledContainerNames():
        if not fbIsValidProjectName(sProjectName):
            continue
        _fnAutoProbeOneJournaledContainer(sProjectName, connectionDocker)


def _fnAutoProbeOneJournaledContainer(sProjectName, connectionDocker):
    """Resolve one container's journal, under its flock when one exists.

    A held flock means a live vaibify process owns the container and
    manages its own journal, so the probe skips it; a free flock is
    taken for the duration of the resolution so the probe can never
    race an acquisition examining the same records. When no lock file
    exists at all there is no holder to race beyond the atomicity the
    journal's own writes provide, so the resolution runs directly
    rather than creating a lock file as a side effect.
    """
    sPath = fsLockPathFor(sProjectName)
    if not os.path.exists(sPath):
        operationJournal.fdictResolveContainerJournal(
            sProjectName, connectionDocker,
        )
        return
    try:
        fileHandle = _ffileOpenLockFileNoFollow(sPath)
    except OSError:
        return
    try:
        try:
            fcntl.flock(fileHandle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return
        try:
            operationJournal.fdictResolveContainerJournal(
                sProjectName, connectionDocker,
            )
        finally:
            fcntl.flock(fileHandle, fcntl.LOCK_UN)
    finally:
        fileHandle.close()


def _fbLockFileIsStale(sPath):
    """Return True when a lock file's recorded holder process has exited."""
    try:
        fileHandle = _ffileOpenLockFileNoFollow(sPath)
    except OSError:
        return False
    try:
        dictHolder = _fdictReadHolderFromHandle(fileHandle)
    finally:
        fileHandle.close()
    return _fbClaimIsStale(dictHolder)


def fnReleaseContainerLock(fileHandle):
    """Release a lock previously acquired via fnAcquireContainerLock."""
    try:
        fcntl.flock(fileHandle, fcntl.LOCK_UN)
    finally:
        fileHandle.close()


def _fdictReadHolderFromHandle(fileHandle):
    """Best-effort read of holder JSON from an open lock file."""
    return pidFileRegistry.fdictReadPayloadFromHandle(fileHandle)


def flistReadAllLockHolders():
    """Return holder info for every live container lock on the host.

    Each entry is ``{sProjectName, iPid, iPort, sStartedIso}`` for a
    lock file whose recorded holder passes the hardened
    ``_fbClaimIsStale`` check (stale claims are skipped, not reported).
    Used by ``vaibify sessions`` to show which containers each live
    session holds. Returns an empty list on any directory error.
    """
    listHolders = []
    for sPath in sorted(pidFileRegistry.flistRegistryFiles(
        _S_LOCK_DIRECTORY, _S_LOCK_SUFFIX,
    )):
        _fnAppendLockHolderRecord(listHolders, sPath)
    return listHolders


def _fnAppendLockHolderRecord(listHolders, sPath):
    """Append a record only for a lock genuinely held by a live process.

    Held-ness is decided by the flock (matching ``fdictReadLockHolder``
    and the GUI): a released lock whose file still lingers is not
    reported, so ``vaibify sessions`` never shows a freed container as
    held.
    """
    dictHolder = _fdictHeldLockHolder(sPath)
    if not dictHolder:
        return
    listHolders.append({
        "sProjectName": dictHolder.get("sProjectName"),
        "iPid": dictHolder.get("iPid"),
        "iPort": dictHolder.get("iPort"),
        "sStartedIso": dictHolder.get("sStartedIso"),
    })


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
    dictHolder = _fdictHeldLockHolder(sPath)
    if dictHolder.get("iPid") == os.getpid():
        return {}
    return dictHolder


def _fdictHeldLockHolder(sPath):
    """Return the holder payload only when a live process holds the flock.

    The flock is the source of truth for held-ness: acquiring it means
    the lock is free (released, or its file merely lingers) and the
    function returns ``{}``; a ``BlockingIOError`` means the lock is
    genuinely held, and the holder is returned unless its recorded PID
    is stale (then reaped).
    """
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
    """Return the holder payload, reaping it first when its PID is dead.

    Reports the holder regardless of identity; the GUI's
    ``fdictReadLockHolder`` applies the self-process exemption on top so
    a hub does not treat its own container as locked, while the
    host-wide ``vaibify sessions`` enumerator lists every held lock.
    """
    dictHolder = _fdictReadHolderFromHandle(fileHandle)
    if _fbClaimIsStale(dictHolder):
        pidFileRegistry.fnUnlinkQuietly(sPath)
        return {}
    return dictHolder
