"""The durable record of a containerization that has not happened yet.

"Convert and obtain" used to convert first: the registry entry became a
container, the clone's ``vaibify.yml`` was rewritten, and only then did
the download start. A download that failed left a project that was a
container in name and nothing else -- it could not start, and nothing
could turn it back (2026-10-13). The ruling: a host project is not
converted until the image is in hand.

So the wizard's answers live HERE until the commit, in
``~/.vaibify/pendingConversions/<hostName>.json``: the new name, the
image source the clone's envelope implied, the trust answer, the seed
paths, every field the ``vaibify.yml`` rewrite needs, and where the
acquisition has got to. The registry entry and the clone's files are
untouched while the record exists; the host project stays usable.

Two locks guard the commit, taken in a FIXED order so two commits can
never deadlock: the SOURCE lock, ``<hostName>.lock``, then the
DESTINATION lock, ``destination-<newName>.lock``. The second exists
because two different host projects can each hold their own source lock,
both find the same container name free, and overwrite each other's tag
and origin record; with the same pinned image they would even share an
image ID, so "untag only what points at our image" could not tell them
apart. Both are ``flock``s, released by the operating system with the
process, and taken NON-BLOCKING: a held lock is a commit in progress,
possibly in another hub, and the honest answer is a refusal by name,
not a wait.

The JOURNAL inside the record carries a random transaction id, the
image ids, and the ``vaibify.yml`` bytes as they were when the commit
began, so a commit cut short by a crash can be rolled back to exactly
what it found -- removing ONLY this transaction's artifacts -- or rolled
forward once the registry's single atomic switch has landed.

Every write is durable (:mod:`vaibify.config.durableFile`): a record
that survives a power cut only as far as the page cache would promise a
recovery it cannot deliver.
"""

__all__ = [
    "ConversionLockHeldError",
    "S_STATE_COMMITTING",
    "S_STATE_OBTAINING",
    "S_STATE_READY",
    "S_STATE_WAITING",
    "fdictBuildConversionRecord",
    "fdictReadConversionRecord",
    "ffileTakeDestinationLock",
    "ffileTakeSourceLock",
    "flistReadConversionRecords",
    "fnReleaseLock",
    "fnRemoveConversionRecord",
    "fnWriteConversionRecord",
    "fsConversionsDirectory",
]

import fcntl
import json
import os
import re
from datetime import datetime, timezone

from vaibify.config.durableFile import fnSyncDirectory, fnWriteFileDurably


class ConversionLockHeldError(Exception):
    """A commit lock is held: another commit of this project is under way."""


S_STATE_OBTAINING = "obtaining"
S_STATE_WAITING = "waiting"
S_STATE_READY = "ready"
S_STATE_COMMITTING = "committing"

S_SCHEMA_VERSION = "1"
_S_CONVERSIONS_DIRECTORY = os.path.join(
    os.path.expanduser("~"), ".vaibify", "pendingConversions",
)
_REGEX_SAFE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ._-]{0,63}$")
_S_RECORD_SUFFIX = ".json"
_S_LOCK_SUFFIX = ".lock"
_S_DESTINATION_PREFIX = "destination-"


def fsConversionsDirectory():
    """Create (0700) and return the directory the records live in."""
    os.makedirs(_S_CONVERSIONS_DIRECTORY, mode=0o700, exist_ok=True)
    return _S_CONVERSIONS_DIRECTORY


def _fsRequireSafeName(sName):
    if not _REGEX_SAFE_NAME.match(str(sName or "")):
        raise ValueError(f"{sName!r} is not a registry-safe project name")
    return sName


def _fsRecordPath(sHostName):
    return os.path.join(
        fsConversionsDirectory(), _fsRequireSafeName(sHostName) + _S_RECORD_SUFFIX,
    )


def _fsNowIso():
    return datetime.now(timezone.utc).isoformat()


def fdictBuildConversionRecord(
    sHostName, sNewName, dictProject, dictRequest, dictImageSource,
    dictImageTrust, dictPendingSeed,
):
    """Return a fresh record: the wizard's answers and nothing decided yet."""
    return {
        "sSchemaVersion": S_SCHEMA_VERSION,
        "sHostName": str(sHostName),
        "sNewName": str(sNewName),
        "sDirectory": str(dictProject.get("sDirectory") or ""),
        "sConfigPath": str(dictProject.get("sConfigPath") or ""),
        "dictRequest": dict(dictRequest or {}),
        "dictImageSource": dict(dictImageSource or {}),
        "dictImageTrust": dict(dictImageTrust) if dictImageTrust else None,
        "dictPendingSeed": dict(dictPendingSeed) if dictPendingSeed else None,
        "sState": S_STATE_OBTAINING,
        "sAcquisitionId": "",
        "dictFailure": None,
        "dictStaged": None,
        "dictJournal": None,
        "sStartedIso": _fsNowIso(),
        "sUpdatedIso": _fsNowIso(),
    }


def fnWriteConversionRecord(dictRecord):
    """Write the record durably under its host project's name."""
    dictRecord["sUpdatedIso"] = _fsNowIso()
    fnWriteFileDurably(
        _fsRecordPath(dictRecord["sHostName"]),
        json.dumps(dictRecord, indent=2, sort_keys=True).encode("utf-8"),
    )


def fdictReadConversionRecord(sHostName):
    """Return the record, or ``None`` when there is none or it is unreadable."""
    try:
        with open(_fsRecordPath(sHostName), "r", encoding="utf-8") as fileRecord:
            jsonRecord = json.load(fileRecord)
    except (OSError, ValueError):
        return None
    if not isinstance(jsonRecord, dict):
        return None
    if jsonRecord.get("sSchemaVersion") != S_SCHEMA_VERSION:
        return None
    return jsonRecord


def flistReadConversionRecords():
    """Return every readable record, in name order."""
    try:
        listNames = sorted(os.listdir(fsConversionsDirectory()))
    except OSError:
        return []
    listRecords = []
    for sName in listNames:
        if not sName.endswith(_S_RECORD_SUFFIX):
            continue
        dictRecord = fdictReadConversionRecord(sName[: -len(_S_RECORD_SUFFIX)])
        if dictRecord is not None:
            listRecords.append(dictRecord)
    return listRecords


def fnRemoveConversionRecord(sHostName):
    """Delete the record and force the directory to disk; absence is fine."""
    try:
        os.unlink(_fsRecordPath(sHostName))
    except FileNotFoundError:
        return
    fnSyncDirectory(fsConversionsDirectory())


# ---------------------------------------------------------------------
# The two commit locks.
# ---------------------------------------------------------------------


def _ffileTakeLock(sLockPath, sWhat):
    """Take one flock non-blocking, or raise naming what is held."""
    fileLock = open(sLockPath, "ab+")
    try:
        fcntl.flock(fileLock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as error:
        fileLock.close()
        raise ConversionLockHeldError(
            f"{sWhat} is being committed by another vaibify process right "
            "now; wait for it to finish."
        ) from error
    return fileLock


def ffileTakeSourceLock(sHostName):
    """Take the SOURCE lock: the host project being converted."""
    return _ffileTakeLock(
        os.path.join(
            fsConversionsDirectory(), _fsRequireSafeName(sHostName) + _S_LOCK_SUFFIX,
        ),
        f"'{sHostName}'",
    )


def ffileTakeDestinationLock(sNewName):
    """Take the DESTINATION lock: the container name being created.

    Always after the source lock, never before: the fixed order is what
    makes two commits unable to deadlock.
    """
    return _ffileTakeLock(
        os.path.join(
            fsConversionsDirectory(),
            _S_DESTINATION_PREFIX + _fsRequireSafeName(sNewName) + _S_LOCK_SUFFIX,
        ),
        f"the container name '{sNewName}'",
    )


def fnReleaseLock(fileLock):
    """Release one lock handle; a handle already closed is left alone."""
    if fileLock is None or fileLock.closed:
        return
    try:
        fcntl.flock(fileLock, fcntl.LOCK_UN)
    except OSError:
        pass
    fileLock.close()
