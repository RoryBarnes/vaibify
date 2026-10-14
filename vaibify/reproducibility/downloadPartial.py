"""The partly downloaded deposit on disk: one directory per acquisition.

A download of the archived image can take an hour on a slow link, and
until 2026-10-13 a connection that dropped at 507 of 733 MB deleted the
bytes and started again from zero. This module is what lets the bytes
survive. Every acquisition owns a directory under
``~/.vaibify/imageArchive/`` holding three things:

* ``<tarball>.part`` -- the bytes received so far;
* ``download.json`` -- the SIDECAR: which deposit this is, how many
  bytes are DURABLE (synced to disk at the last checkpoint), the
  high-water mark, when the last byte arrived, and what the download
  was for;
* ``lock`` -- held with ``flock`` by the owning process for the whole
  life of the acquisition.

**The lock tells a download somebody gave up on from one that was
interrupted.** Giving up (Cancel, Stay in host mode, Ctrl-C) deletes the
directory while the lock is still held, so nothing is left. A power
failure, a crash, a hub that was quit or killed releases the lock
without deleting anything -- the operating system drops ``flock`` with
the process -- so any partial still on disk whose lock is FREE was
interrupted, and is offered for resume. A partial whose lock is HELD
belongs to a live download (a CLI run beside the hub, a second hub) and
is never touched by anyone else.

**A new acquisition of the same tarball ADOPTS an interrupted partial**,
matched by the sha256 the envelope records: it takes the lock, truncates
the ``.part`` to the sidecar's durable count (a torn tail from a power
cut is thrown away, at most one checkpoint's worth), and the downloader
continues with a Range request. The final sha256 check against the
envelope is unchanged, so adoption can cost time, never correctness.

**The sidecar is written durably** (:mod:`vaibify.config.durableFile`),
because a sidecar that claims more durable bytes than the disk holds
would resume from garbage. A missing or unparseable sidecar means the
partial cannot be trusted and is listed for deletion only.

Directories named ``tmp*`` are v0.1.0 scratch: no lock, no sidecar.
They cannot be resumed and their ownership cannot be proven, so they
are only ever listed, never deleted on vaibify's own initiative.
"""

__all__ = [
    "PartialHeldElsewhereError",
    "S_LOCK_NAME",
    "S_PART_SUFFIX",
    "S_SIDECAR_NAME",
    "fbIsComplete",
    "fbLockIsFree",
    "fdictOpenAcquisition",
    "fdictReadSidecar",
    "fiBytesOnDisk",
    "flistDescribePartials",
    "fnCheckpoint",
    "fnDeletePartial",
    "fnDiscardAcquisition",
    "fnMarkComplete",
    "fnRecordSidecar",
    "fnReleaseAcquisition",
    "fnTruncatePart",
    "fsCompletePath",
    "fsResolveDownloadRoot",
    "fsValidatedTarballName",
]

import fcntl
import json
import os
import re
import shutil
import tempfile
from datetime import datetime, timezone

from vaibify.config.durableFile import fnSyncDirectory, fnWriteFileDurably


class PartialHeldElsewhereError(Exception):
    """The partial's lock is held: a live download owns it."""


_S_DOWNLOAD_ROOT = os.path.join(
    os.path.expanduser("~"), ".vaibify", "imageArchive",
)
_S_ACQUISITION_PREFIX = "acquisition-"
_S_LEGACY_PREFIX = "tmp"
S_SIDECAR_NAME = "download.json"
S_LOCK_NAME = "lock"
S_PART_SUFFIX = ".part"
S_SIDECAR_SCHEMA_VERSION = "1"


def fsResolveDownloadRoot():
    """Create (0700) and return the directory every partial lives under."""
    os.makedirs(_S_DOWNLOAD_ROOT, mode=0o700, exist_ok=True)
    return _S_DOWNLOAD_ROOT


def _fsNowIso():
    return datetime.now(timezone.utc).isoformat()


def _fsTarballSha256Of(dictRecord):
    return str(dictRecord.get("sTarballSha256") or "")


# ---------------------------------------------------------------------
# Opening: a fresh directory, or an interrupted partial adopted.
# ---------------------------------------------------------------------


def fdictOpenAcquisition(dictRecord, dictPurpose=None):
    """Return the acquisition that will hold this deposit's bytes, lock held.

    An interrupted partial of the SAME tarball (sidecar sha256 equal,
    lock free) is adopted, truncated to its durable count; otherwise a
    fresh directory is created. Either way the returned dict holds the
    open lock handle, and the caller ends the acquisition with
    :func:`fnDiscardAcquisition` (delete) or
    :func:`fnReleaseAcquisition` (keep for a later resume).
    """
    # Validated before any directory exists, so a refused name leaves
    # nothing behind.
    sStoredName = fsValidatedTarballName(dictRecord.get("sTarballName"))
    sRoot = fsResolveDownloadRoot()
    dictAdopted = _fdictAdoptInterruptedPartial(
        sRoot, _fsTarballSha256Of(dictRecord),
    )
    if dictAdopted is not None:
        return dictAdopted
    sDirectory = tempfile.mkdtemp(prefix=_S_ACQUISITION_PREFIX, dir=sRoot)
    dictAcquisition = _fdictDescribeDirectory(sDirectory)
    dictAcquisition["fileLock"] = _ffileTakeLock(sDirectory)
    dictAcquisition["bAdopted"] = False
    dictAcquisition["dictSidecar"] = _fdictNewSidecar(
        dictAcquisition["sAcquisitionId"], dictRecord, dictPurpose,
    )
    dictAcquisition["sPartPath"] = os.path.join(
        sDirectory, sStoredName + S_PART_SUFFIX,
    )
    fnRecordSidecar(dictAcquisition, {})
    return dictAcquisition


def _fdictDescribeDirectory(sDirectory):
    """Return the paths one acquisition directory implies."""
    return {
        "sAcquisitionId": os.path.basename(sDirectory),
        "sDirectory": sDirectory,
        "sSidecarPath": os.path.join(sDirectory, S_SIDECAR_NAME),
        "sLockPath": os.path.join(sDirectory, S_LOCK_NAME),
    }


def _fdictNewSidecar(sAcquisitionId, dictRecord, dictPurpose):
    """The sidecar of a download that has received no byte yet."""
    return {
        "sSchemaVersion": S_SIDECAR_SCHEMA_VERSION,
        "sAcquisitionId": sAcquisitionId,
        "sTarballName": str(dictRecord.get("sTarballName") or ""),
        "sTarballSha256": _fsTarballSha256Of(dictRecord),
        "iExpectedBytes": int(dictRecord.get("iTarballBytes") or 0),
        "sVersionDoi": str(dictRecord.get("sVersionDoi") or ""),
        "sZenodoService": str(dictRecord.get("sZenodoService") or ""),
        "sFileUrl": "",
        "iDurableBytes": 0,
        "iHighWaterBytes": 0,
        "sStartedIso": _fsNowIso(),
        "sLastByteIso": "",
        "sLastFailure": "",
        "dictPurpose": dict(dictPurpose or {}),
    }


_REGEX_STORABLE_TARBALL_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,254}$")


def fsValidatedTarballName(sName):
    """Return the name the download is stored under, or raise ``ValueError``.

    A deposit record names its file, and that name becomes a path in
    the acquisition directory beside the lock and the sidecar. A record
    naming its archive ``lock`` passed its hash check and replaced the
    lock file, another process then took the replacement, and the
    owner's directory could be deleted under it. The name must be a
    plain basename, must not be one of the acquisition's own files, and
    must not end in the part suffix; an absent name stores as
    ``image.tar`` as it always did.
    """
    sBasename = str(sName or "")
    if not sBasename:
        return "image.tar"
    if (
        os.path.basename(sBasename) != sBasename
        or not _REGEX_STORABLE_TARBALL_NAME.match(sBasename)
        or sBasename in (S_LOCK_NAME, S_SIDECAR_NAME)
        or sBasename.endswith(S_PART_SUFFIX)
    ):
        raise ValueError(
            f"the deposit names its archive {sBasename!r}, which is not a name "
            "a download can be stored under; the record must be corrected"
        )
    return sBasename


def _ffileTakeLock(sDirectory):
    """Open the directory's lock file and take it exclusively, or raise.

    Non-blocking on purpose: a held lock means a live download, and
    waiting for it would mean waiting for somebody else's hour.
    """
    fileLock = open(os.path.join(sDirectory, S_LOCK_NAME), "ab+")
    try:
        fcntl.flock(fileLock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as error:
        fileLock.close()
        raise PartialHeldElsewhereError(
            "another vaibify process is downloading into this directory"
        ) from error
    return fileLock


def _fdictAdoptInterruptedPartial(sRoot, sTarballSha256):
    """Take over an interrupted partial of this tarball, or return None."""
    if not sTarballSha256:
        return None
    for sName in sorted(os.listdir(sRoot)):
        sDirectory = os.path.join(sRoot, sName)
        if not sName.startswith(_S_ACQUISITION_PREFIX):
            continue
        if not os.path.isfile(os.path.join(sDirectory, S_SIDECAR_NAME)):
            continue
        dictAdopted = _fdictTryAdopt(sDirectory, sTarballSha256)
        if dictAdopted is not None:
            return dictAdopted
    return None


def _fdictTryAdopt(sDirectory, sTarballSha256):
    """Lock first, read second: a sidecar is only trusted under its lock."""
    try:
        fileLock = _ffileTakeLock(sDirectory)
    except (PartialHeldElsewhereError, OSError):
        return None
    dictSidecar = fdictReadSidecar(os.path.join(sDirectory, S_SIDECAR_NAME))
    if dictSidecar is None or dictSidecar.get("sTarballSha256") != sTarballSha256:
        fileLock.close()
        return None
    try:
        sStoredName = fsValidatedTarballName(dictSidecar.get("sTarballName"))
    except ValueError:
        # A sidecar edited by hand to name one of the acquisition's own
        # files is not adopted; a fresh directory is made instead.
        fileLock.close()
        return None
    dictAcquisition = _fdictDescribeDirectory(sDirectory)
    dictAcquisition["fileLock"] = fileLock
    dictAcquisition["bAdopted"] = True
    dictAcquisition["dictSidecar"] = dictSidecar
    dictAcquisition["sPartPath"] = os.path.join(
        sDirectory, sStoredName + S_PART_SUFFIX,
    )
    if not fbIsComplete(dictAcquisition):
        fnTruncatePart(dictAcquisition, int(dictSidecar.get("iDurableBytes") or 0))
    return dictAcquisition


# ---------------------------------------------------------------------
# The sidecar and the checkpoints.
# ---------------------------------------------------------------------


def fdictReadSidecar(sSidecarPath):
    """Return the parsed sidecar, or None when it is missing or unreadable."""
    try:
        with open(sSidecarPath, "r", encoding="utf-8") as fileSidecar:
            jsonSidecar = json.load(fileSidecar)
    except (OSError, ValueError):
        return None
    if not isinstance(jsonSidecar, dict):
        return None
    if jsonSidecar.get("sSchemaVersion") != S_SIDECAR_SCHEMA_VERSION:
        return None
    return jsonSidecar


def fnRecordSidecar(dictAcquisition, dictUpdate):
    """Merge ``dictUpdate`` into the sidecar and write it durably."""
    dictAcquisition["dictSidecar"].update(dictUpdate)
    fnWriteFileDurably(
        dictAcquisition["sSidecarPath"],
        json.dumps(
            dictAcquisition["dictSidecar"], indent=2, sort_keys=True,
        ).encode("utf-8"),
    )


def fnCheckpoint(dictAcquisition, filePart, iBytesWritten):
    """Make ``iBytesWritten`` bytes of the part durable, then say so.

    The ORDER is the guarantee: the bytes reach the disk before the
    sidecar claims them. A sidecar written first could name bytes a
    power cut never let land.
    """
    from vaibify.config.durableFile import fnSyncDescriptor
    filePart.flush()
    fnSyncDescriptor(filePart.fileno())
    fnRecordSidecar(dictAcquisition, {
        "iDurableBytes": int(iBytesWritten),
        "iHighWaterBytes": max(
            int(iBytesWritten),
            int(dictAcquisition["dictSidecar"].get("iHighWaterBytes") or 0),
        ),
        "sLastByteIso": _fsNowIso(),
    })


def fiBytesOnDisk(dictAcquisition):
    """Return the bytes held: the complete file's, else the part's, else 0."""
    for sPath in (fsCompletePath(dictAcquisition), dictAcquisition["sPartPath"]):
        try:
            return os.path.getsize(sPath)
        except OSError:
            continue
    return 0


def fnTruncatePart(dictAcquisition, iBytes):
    """Cut the part file to ``iBytes``; create it empty when absent.

    A complete file is thrown away too: truncating is how a download
    starts over, and a file that passed verification is never cut.
    """
    try:
        os.unlink(fsCompletePath(dictAcquisition))
    except OSError:
        pass
    with open(dictAcquisition["sPartPath"], "ab") as filePart:
        filePart.truncate(max(0, int(iBytes)))
        filePart.flush()
        os.fsync(filePart.fileno())


def fsCompletePath(dictAcquisition):
    """The verified file's path: the part's name without its suffix.

    The name matters beyond tidiness: the codec is read from the
    extension, and a promotion names the uploaded file after it.
    """
    return dictAcquisition["sPartPath"][: -len(S_PART_SUFFIX)]


def fbIsComplete(dictAcquisition):
    """Return True when the verified file is on disk under its final name."""
    return os.path.isfile(fsCompletePath(dictAcquisition))


def fnMarkComplete(dictAcquisition):
    """Rename the verified part to its final name, durably."""
    os.replace(dictAcquisition["sPartPath"], fsCompletePath(dictAcquisition))
    fnSyncDirectory(dictAcquisition["sDirectory"])
    fnRecordSidecar(dictAcquisition, {
        "bComplete": True,
        "iDurableBytes": int(dictAcquisition["dictSidecar"].get("iExpectedBytes") or 0),
    })


# ---------------------------------------------------------------------
# Ending an acquisition.
# ---------------------------------------------------------------------


def fnDiscardAcquisition(dictAcquisition):
    """Delete the directory WHILE the lock is held, then release it.

    The order is what makes a give-up distinguishable from an
    interruption: nothing is ever left on disk with a free lock by
    this path. The directory sync records the removal.
    """
    sDirectory = dictAcquisition["sDirectory"]
    shutil.rmtree(sDirectory, ignore_errors=True)
    fnSyncDirectory(os.path.dirname(sDirectory))
    fnReleaseAcquisition(dictAcquisition)


def fnReleaseAcquisition(dictAcquisition):
    """Release the lock and keep the partial for a later resume."""
    fileLock = dictAcquisition.pop("fileLock", None)
    if fileLock is None:
        return
    try:
        fcntl.flock(fileLock, fcntl.LOCK_UN)
    except OSError:
        pass
    fileLock.close()


# ---------------------------------------------------------------------
# Listing and deleting from outside the owning download.
# ---------------------------------------------------------------------


def fbLockIsFree(sDirectory):
    """Return True when nobody holds the directory's lock.

    Probing takes and releases the lock, which is harmless: a live
    owner holds it and the probe fails; a free lock stays free.
    """
    sLockPath = os.path.join(sDirectory, S_LOCK_NAME)
    if not os.path.isfile(sLockPath):
        return False
    try:
        fileLock = _ffileTakeLock(sDirectory)
    except (PartialHeldElsewhereError, OSError):
        return False
    fcntl.flock(fileLock, fcntl.LOCK_UN)
    fileLock.close()
    return True


def flistDescribePartials():
    """Describe every directory under the download root, with evidence.

    Each entry says whether the partial is ``live`` (lock held),
    ``interrupted`` (lock free, sidecar readable), ``unusable`` (no
    sidecar, or one that does not parse) or ``legacy`` (a v0.1.0
    ``tmp*`` scratch directory), plus the bytes on disk and the
    sidecar when there is one. Nothing is created and nothing is
    deleted by listing.
    """
    sRoot = _S_DOWNLOAD_ROOT
    try:
        listNames = sorted(os.listdir(sRoot))
    except OSError:
        return []
    return [
        _fdictDescribePartial(os.path.join(sRoot, sName))
        for sName in listNames
        if os.path.isdir(os.path.join(sRoot, sName))
    ]


def _fdictDescribePartial(sDirectory):
    """Classify one directory under the root."""
    sName = os.path.basename(sDirectory)
    dictSidecar = fdictReadSidecar(os.path.join(sDirectory, S_SIDECAR_NAME))
    iBytes = _fiLargestFileBytes(sDirectory)
    if sName.startswith(_S_LEGACY_PREFIX) or not os.path.isfile(
        os.path.join(sDirectory, S_LOCK_NAME),
    ):
        sState = "legacy"
    elif not fbLockIsFree(sDirectory):
        sState = "live"
    elif dictSidecar is None:
        sState = "unusable"
    else:
        sState = "interrupted"
    return {
        "sAcquisitionId": sName,
        "sDirectory": sDirectory,
        "sState": sState,
        "iBytesOnDisk": iBytes,
        "dictSidecar": dictSidecar,
    }


def _fiLargestFileBytes(sDirectory):
    """Return the size of the biggest file in a directory (the part)."""
    iLargest = 0
    try:
        for sEntry in os.listdir(sDirectory):
            sPath = os.path.join(sDirectory, sEntry)
            if os.path.isfile(sPath):
                iLargest = max(iLargest, os.path.getsize(sPath))
    except OSError:
        return 0
    return iLargest


def fnDeletePartial(sAcquisitionId):
    """Delete one partial by id, only when its lock is free.

    A legacy directory (no lock file) is deleted on request too: this
    is the researcher's explicit Delete, the one path the plan allows
    for it. A held lock refuses by name.
    """
    sDirectory = os.path.join(_S_DOWNLOAD_ROOT, os.path.basename(sAcquisitionId))
    if not os.path.isdir(sDirectory):
        return
    if os.path.isfile(os.path.join(sDirectory, S_LOCK_NAME)):
        fileLock = _ffileTakeLock(sDirectory)
        try:
            shutil.rmtree(sDirectory, ignore_errors=True)
        finally:
            fileLock.close()
    else:
        shutil.rmtree(sDirectory, ignore_errors=True)
    fnSyncDirectory(_S_DOWNLOAD_ROOT)
