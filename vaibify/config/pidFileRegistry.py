"""Shared primitives for the host's file-backed PID registries.

``containerLock.py`` and ``sessionRegistry.py`` are two
schema-divergent views of one pattern: a small file per resource
under ``~/.vaibify/<x>/``, guarded by an ``fcntl.flock``, carrying a
best-effort JSON holder payload, and reaped when the recorded holder
process is gone. This module is the single home for the mechanism
they share -- one ``O_NOFOLLOW`` opener, one payload read/write pair,
one ``0o700`` directory creator, one quiet unlink, and one stale-file
reaper parameterized by a caller-supplied staleness predicate.

The divergent payload schemas and the two public APIs stay in the
wrapper modules (load-bearing divergence per the project guide); only
the file mechanism lives here. The recycle-proof liveness contract
itself stays in ``processLiveness``; this module never decides
staleness, it only enumerates, opens, reads, writes, and unlinks.
"""

__all__ = [
    "fnEnsureDirectory",
    "ffileOpenNoFollow",
    "fnWritePayload",
    "fdictReadPayload",
    "fdictReadPayloadFromHandle",
    "flistRegistryFiles",
    "fnReapStaleFilesIn",
    "fnUnlinkQuietly",
]

import json
import os


def fnEnsureDirectory(sDirectory):
    """Create a registry directory with mode ``0o700`` if it is missing."""
    os.makedirs(sDirectory, mode=0o700, exist_ok=True)
    try:
        os.chmod(sDirectory, 0o700)
    except OSError:
        pass


def ffileOpenNoFollow(sPath):
    """Open a registry file with ``O_NOFOLLOW`` so symlinks are rejected."""
    iFileDescriptor = os.open(
        sPath,
        os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW,
        0o600,
    )
    return os.fdopen(iFileDescriptor, "r+")


def fnWritePayload(fileHandle, dictPayload):
    """Truncate the open file and rewrite it with the holder payload."""
    fileHandle.seek(0)
    fileHandle.truncate()
    fileHandle.write(json.dumps(dictPayload, indent=2))
    fileHandle.flush()


def fdictReadPayload(sPath):
    """Best-effort read of a registry file's JSON payload, or ``{}``."""
    try:
        with open(sPath, "r") as fileHandle:
            dictPayload = json.load(fileHandle)
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(dictPayload, dict):
        return {}
    return dictPayload


def fdictReadPayloadFromHandle(fileHandle):
    """Best-effort read of holder JSON from an already-open handle, or ``{}``."""
    try:
        fileHandle.seek(0)
        sContent = fileHandle.read()
        if not sContent:
            return {}
        dictPayload = json.loads(sContent)
    except (json.JSONDecodeError, OSError):
        return {}
    return dictPayload if isinstance(dictPayload, dict) else {}


def flistRegistryFiles(sDirectory, sSuffix):
    """Return absolute paths of files in ``sDirectory`` ending in ``sSuffix``.

    Returns an empty list when the directory is absent or unreadable so
    a registry mishap never crashes the caller. The order follows
    ``os.listdir``; callers that need determinism sort the result.
    """
    if not os.path.isdir(sDirectory):
        return []
    try:
        listEntries = os.listdir(sDirectory)
    except OSError:
        return []
    return [
        os.path.join(sDirectory, sEntry)
        for sEntry in listEntries
        if sEntry.endswith(sSuffix)
    ]


def fnReapStaleFilesIn(sDirectory, fbIsStale, sSuffix=""):
    """Unlink every ``sSuffix`` file in ``sDirectory`` that ``fbIsStale`` accepts.

    ``fbIsStale`` receives a file's absolute path and returns ``True``
    only when its recorded holder process is genuinely gone; live files
    are never touched. The empty default suffix matches every file.
    """
    for sPath in flistRegistryFiles(sDirectory, sSuffix):
        if fbIsStale(sPath):
            fnUnlinkQuietly(sPath)


def fnUnlinkQuietly(sPath):
    """Remove a registry file, ignoring races with other reapers."""
    try:
        os.unlink(sPath)
    except OSError:
        pass
