"""Spool, check and describe an upload before it reaches a project.

A large upload is received into a private file on this machine BEFORE any
container write begins, for one reason the rest follows from: the
write-ahead journal records the sha256 of the bytes it is about to write,
and a crash can only be settled by comparing the target with that hash.
The hash of a stream is known only when the stream has ended, so the
bytes are spooled (and hashed as they arrive), the journal record is
opened, and only then are they copied to their destination. The spool also
decouples a slow browser from the container lock: the lock is held for the
copy, never for the time the researcher's network takes.

This module owns the spool, the lexical rules that say where an upload may
land (one function, so the upload route and the panel's verdict cannot
disagree), the free-space preflight, and the sweep for spools a crash left
behind. It never touches a container.
"""

import asyncio
import errno
import hashlib
import os
import posixpath
import re
import secrets
import shutil
import time

from fastapi import HTTPException

from vaibify.config.ephemeralStore import fsGetEphemeralRoot
from vaibify.config.processLiveness import fbIsProcessAlive
from .pipelineServer import (
    fnRejectWriteDenylistedPath,
    fsValidatePathWithinRoot,
)

__all__ = [
    "I_PER_FILE_SPACE_PROBE_MIN_BYTES",
    "fdictDescribeUploadVerdict",
    "fdictOpenSpool",
    "fdictSpoolBody",
    "fiSweepAbandonedSpools",
    "fnDiscardSpool",
    "fnRequireUploadAllowed",
    "fsComposeUploadTarget",
    "fsDescribeSpaceShortage",
    "fsDescribeUploadRefusal",
    "fsFormatByteCount",
    "fsValidateUploadName",
]

S_SPOOL_DIRECTORY_NAME = "uploads"
S_SPOOL_PREFIX = "upload-"
S_SPOOL_SUFFIX = ".part"
I_SPOOL_WRITE_BYTES = 1 << 20
I_NAME_MAX_BYTES = 255
# A file smaller than this is not worth a container round trip to read the
# free space: the batch's total was checked once before its first byte and
# a disk that fills anyway answers 507 at the write.
I_PER_FILE_SPACE_PROBE_MIN_BYTES = 8 << 20
F_ORPHANED_SPOOL_AGE_SECONDS = 7 * 24 * 60 * 60
_RE_SPOOL_NAME = re.compile(
    r"^" + re.escape(S_SPOOL_PREFIX) + r"(\d+)-[0-9a-f]+"
    + re.escape(S_SPOOL_SUFFIX) + r"$"
)


def fsFormatByteCount(iBytes):
    """Return a byte count a researcher reads at a glance (``1.5 GB``)."""
    fValue = float(iBytes)
    for sUnit in ("B", "KB", "MB", "GB", "TB"):
        if fValue < 1000.0 or sUnit == "TB":
            return f"{fValue:.0f} {sUnit}" if sUnit == "B" else (
                f"{fValue:.1f} {sUnit}")
        fValue /= 1000.0


def fsValidateUploadName(sName, sLabel):
    """Return ``sName`` or raise 400 when it cannot be one path component."""
    bControl = any(ord(sCharacter) < 32 or ord(sCharacter) == 127
                   for sCharacter in sName)
    if (not sName or sName in (".", "..") or "/" in sName or bControl
            or len(sName.encode("utf-8", "surrogatepass")) > I_NAME_MAX_BYTES):
        raise HTTPException(
            400, f"{sLabel} {sName!r} is not a valid file or folder name.")
    return sName


def fsComposeUploadTarget(sDestination, sRelativePath, sFilename):
    """Return the absolute path an upload names, after lexical checks.

    ``sRelativePath`` is the directory part below the destination (a
    dropped folder's own structure) and may be empty; it may not be
    absolute and no segment may be ``..`` or empty.
    """
    if not isinstance(sDestination, str) or not sDestination.startswith("/"):
        raise HTTPException(400, "The destination must be an absolute path.")
    if sRelativePath.startswith("/"):
        raise HTTPException(
            400, "The relative path of an upload may not be absolute.")
    listSegments = sRelativePath.split("/") if sRelativePath else []
    for sSegment in listSegments:
        fsValidateUploadName(sSegment, "The folder name")
    fsValidateUploadName(sFilename, "The file name")
    return posixpath.join(sDestination, *listSegments, sFilename)


def fsDescribeUploadRefusal(sTarget, sWritableRoot):
    """Return why ``sTarget`` may not be written, or ``""`` when it may.

    The one place the rule lives: the upload route turns the sentence into
    a 403 and the panel's verdict shows it as the reason the drop zone is
    disabled, so the two cannot drift.
    """
    try:
        sNormalized = fsValidatePathWithinRoot(sTarget, sWritableRoot)
    except HTTPException:
        return (f"That location is outside the writable root "
                f"{sWritableRoot}, so nothing can be uploaded there.")
    try:
        fnRejectWriteDenylistedPath(sNormalized, sWritableRoot)
    except HTTPException as errorDenied:
        return str(errorDenied.detail)
    return ""


def fnRequireUploadAllowed(sTarget, sWritableRoot):
    """Raise 403 with the specific reason when the target is refused."""
    sRefusal = fsDescribeUploadRefusal(sTarget, sWritableRoot)
    if sRefusal:
        raise HTTPException(403, sRefusal)


def fdictDescribeUploadVerdict(sDirectory, sWritableRoot):
    """Return the panel's verdict for dropping into ``sDirectory``."""
    sRefusal = fsDescribeUploadRefusal(sDirectory, sWritableRoot)
    return {
        "bUploadAllowed": not sRefusal,
        "sUploadRefusal": sRefusal,
        "sWritableRoot": sWritableRoot,
    }


def _fsSpoolDirectory():
    sDirectory = os.path.join(fsGetEphemeralRoot(), S_SPOOL_DIRECTORY_NAME)
    os.makedirs(sDirectory, mode=0o700, exist_ok=True)
    return sDirectory


def fdictOpenSpool():
    """Create a private spool file named for this process; return its record.

    The process id is in the name so a later hub can tell a spool a live
    peer is still filling from one a crash abandoned.
    """
    sPath = os.path.join(
        _fsSpoolDirectory(),
        f"{S_SPOOL_PREFIX}{os.getpid()}-{secrets.token_hex(8)}"
        f"{S_SPOOL_SUFFIX}",
    )
    iDescriptor = os.open(
        sPath, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    return {
        "sPath": sPath,
        "fileSpool": os.fdopen(iDescriptor, "wb"),
        "hasherContent": hashlib.sha256(),
        "iBytes": 0,
    }


def fnDiscardSpool(dictSpool):
    """Close and delete a spool; never raise (it runs in ``finally``)."""
    try:
        dictSpool["fileSpool"].close()
    except OSError:
        pass
    try:
        os.unlink(dictSpool["sPath"])
    except OSError:
        pass


def _fnWriteSpoolChunk(dictSpool, baChunk):
    try:
        dictSpool["fileSpool"].write(baChunk)
    except OSError as error:
        if error.errno in (errno.ENOSPC, errno.EDQUOT):
            raise HTTPException(
                507, "This computer's disk filled up while the upload was "
                "being received. Nothing was written to the project.",
            ) from error
        raise
    dictSpool["hasherContent"].update(baChunk)
    dictSpool["iBytes"] += len(baChunk)


async def _fnFlushPending(dictSpool, bufferPending):
    baChunk = bytes(bufferPending)
    bufferPending.clear()
    if baChunk:
        await asyncio.to_thread(_fnWriteSpoolChunk, dictSpool, baChunk)


def _fnRefuseWrongLength(iReceived, iSizeBytes, bFinished):
    if iReceived > iSizeBytes or (bFinished and iReceived != iSizeBytes):
        raise HTTPException(
            400,
            f"The upload carried {fsFormatByteCount(iReceived)}"
            f"{'' if bFinished else ' so far'} but declared "
            f"{fsFormatByteCount(iSizeBytes)}; nothing was written.",
        )


async def fdictSpoolBody(iterBody, iSizeBytes):
    """Receive a request body into a spool, hashing and counting as it arrives.

    Refuses at once a body longer than it declared, and at the end one
    that is shorter. A failure, a refusal or a disconnect removes the
    spool before the exception leaves.
    """
    dictSpool = fdictOpenSpool()
    bufferPending = bytearray()
    try:
        async for baChunk in iterBody:
            bufferPending.extend(baChunk)
            _fnRefuseWrongLength(
                dictSpool["iBytes"] + len(bufferPending), iSizeBytes, False)
            if len(bufferPending) >= I_SPOOL_WRITE_BYTES:
                await _fnFlushPending(dictSpool, bufferPending)
        await _fnFlushPending(dictSpool, bufferPending)
        _fnRefuseWrongLength(dictSpool["iBytes"], iSizeBytes, True)
        dictSpool["fileSpool"].close()
    except BaseException:
        fnDiscardSpool(dictSpool)
        raise
    return dictSpool


def _fiReadFreeBytes(connectionDocker, sContainerId, sPath):
    """Return the free bytes behind ``sPath``, or -1 when it cannot be told."""
    try:
        dictUsage = connectionDocker.fdictReadFilesystemUsage(
            sContainerId, sPath)
        return int(dictUsage["iFreeBytes"])
    except Exception:
        return -1


def _fbOnOneDevice(sFirst, sSecond):
    try:
        return os.stat(sFirst).st_dev == os.stat(sSecond).st_dev
    except OSError:
        return False


def fsDescribeSpaceShortage(
    connectionDocker, sContainerId, sDestination, iBytes, bHostProject,
):
    """Return a sentence naming the short disk, or ``""`` when there is room.

    The upload is held once on this machine while it is received and
    placed once at its destination. For a host project both are the same
    drive when ``st_dev`` says so, and then the drive must hold both. For
    a container project the destination is Docker's volume, which lives
    in a virtual machine whose disk sits on this machine's drive: whether
    they are one pool cannot be known, so each side is checked alone and
    the answer is only an upfront check, never a promise.
    """
    sSpool = _fsSpoolDirectory()
    iSpoolFree = shutil.disk_usage(sSpool).free
    iDestinationFree = _fiReadFreeBytes(
        connectionDocker, sContainerId, sDestination)
    if bHostProject and iDestinationFree >= 0 and _fbOnOneDevice(
        sSpool, sDestination,
    ):
        if iDestinationFree < 2 * iBytes:
            return _fsShortageSentence(
                "This computer's disk", iDestinationFree, 2 * iBytes,
                " (it is held once while received and once more where it "
                "is placed)")
        return ""
    if iSpoolFree < iBytes:
        return _fsShortageSentence(
            "This computer's disk", iSpoolFree, iBytes,
            " to hold the upload while it is received")
    if 0 <= iDestinationFree < iBytes:
        sSide = ("This computer's disk (the project's drive)" if bHostProject
                 else "The container's disk (Docker's virtual machine)")
        return _fsShortageSentence(sSide, iDestinationFree, iBytes, "")
    return ""


def _fsShortageSentence(sDisk, iFree, iNeeded, sNote):
    return (f"{sDisk} has {fsFormatByteCount(iFree)} free and the upload "
            f"needs {fsFormatByteCount(iNeeded)}{sNote}. Nothing was "
            "written.")


def _fbSpoolIsAbandoned(sName, sPath):
    """True for a spool no live process is filling."""
    matchName = _RE_SPOOL_NAME.match(sName)
    try:
        fAge = time.time() - os.path.getmtime(sPath)
    except OSError:
        return False
    if matchName is None:
        return fAge > F_ORPHANED_SPOOL_AGE_SECONDS
    return not fbIsProcessAlive(int(matchName.group(1)))


def fiSweepAbandonedSpools():
    """Delete spools whose owning process is gone; return how many.

    A ``finally`` does not survive a crash, so a hub that died mid-upload
    left a spool behind. The owner is read from the file name, which keeps
    a second hub that starts while the first is still receiving from
    deleting the first's live spool.
    """
    try:
        sDirectory = _fsSpoolDirectory()
        listNames = os.listdir(sDirectory)
    except OSError:
        return 0
    iRemoved = 0
    for sName in listNames:
        sPath = os.path.join(sDirectory, sName)
        if not _fbSpoolIsAbandoned(sName, sPath):
            continue
        try:
            os.unlink(sPath)
            iRemoved += 1
        except OSError:
            continue
    return iRemoved
