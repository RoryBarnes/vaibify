"""The host leg of the confined reads: bounded streams, no link followed unseen.

The container's reads are fixed programs (:mod:`vaibify.docker.confinedRead`).
A host project's files are read by this process, so the same contract is
implemented here in Python and the two are held to one another by running
the same scenarios against both (``tests/testConfinedReadParity.py``).

Everything is relative to a root that is already a real path. Each
component below it is opened with ``O_NOFOLLOW`` against the descriptor
above it, so a component swapped for a symlink after a check is refused
rather than followed. A final symlink is followed only lexically and only
inside the root, a small number of links in a row. A directory archive
never follows a link and never contains a member beneath a link.

Host-only module: it uses ``os.path`` deliberately.
"""

import errno
import logging
import os
import stat
import tarfile

from vaibify.docker.confinedRead import (
    ContainerReadRefusedError,
    I_MAX_ARCHIVE_DEPTH,
    I_MAX_LINK_HOPS,
)

__all__ = [
    "fiterStreamDirectoryAsTar",
    "fiterStreamFileInsideRoot",
]

I_CHUNK_BYTES = 1048576
_I_TAR_BLOCK_BYTES = 512
_I_TAR_RECORD_BYTES = 10240

logger = logging.getLogger("vaibify")


def _flistSplit(sPath):
    return [sPart for sPart in sPath.split(os.sep) if sPart]


def _fbBelowRoot(listRoot, listParts, bAllowRoot):
    if listParts[:len(listRoot)] != listRoot:
        return False
    return bAllowRoot or len(listParts) > len(listRoot)


def _fiOpenDirectoryWithoutFollowing(iParent, sName):
    iFlags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    try:
        return os.open(sName, iFlags, dir_fd=iParent)
    except OSError as error:
        if error.errno in (errno.ELOOP, errno.ENOTDIR):
            raise ContainerReadRefusedError(
                f"refused: '{sName}' is a symlink or not a directory"
            ) from error
        raise


def _fiOpenParentDirectory(sRealRoot, listBelowRoot):
    iDirectory = os.open(sRealRoot, os.O_RDONLY | os.O_DIRECTORY)
    for sName in listBelowRoot[:-1]:
        try:
            iNext = _fiOpenDirectoryWithoutFollowing(iDirectory, sName)
        finally:
            os.close(iDirectory)
        iDirectory = iNext
    return iDirectory


def _fsResolveLinkLexically(listParentParts, sTarget):
    if sTarget.startswith(os.sep):
        return os.path.normpath(sTarget)
    return os.path.normpath(
        os.sep + os.sep.join(listParentParts) + os.sep + sTarget)


def _fsReadLinkText(iParent, sName, sExpectedKind):
    try:
        return os.readlink(sName, dir_fd=iParent)
    except OSError as error:
        raise ContainerReadRefusedError(
            f"refused: '{sName}' is not a {sExpectedKind} I may read"
        ) from error


def _fiOpenOnce(iParent, sName, iFlags):
    """Open ``sName`` without following; return None when it is a link."""
    try:
        return os.open(sName, iFlags | os.O_NOFOLLOW, dir_fd=iParent)
    except FileNotFoundError as error:
        raise FileNotFoundError(
            errno.ENOENT, f"not found: '{sName}'") from error
    except OSError as error:
        if error.errno not in (errno.ELOOP, errno.ENOTDIR):
            raise
    return None


def _fiOpenFollowingLinksInsideRoot(
    sRealRoot, sStartPath, iFlags, bAllowRoot, sExpectedKind,
):
    listRoot = _flistSplit(sRealRoot)
    sCurrent = sStartPath
    for _ in range(I_MAX_LINK_HOPS + 1):
        listParts = _flistSplit(sCurrent)
        if not _fbBelowRoot(listRoot, listParts, bAllowRoot):
            raise ContainerReadRefusedError(
                "refused: the path is not below its authorized root")
        listBelowRoot = listParts[len(listRoot):]
        if not listBelowRoot:
            return os.open(sRealRoot, iFlags)
        iParent = _fiOpenParentDirectory(sRealRoot, listBelowRoot)
        try:
            iOpened = _fiOpenOnce(iParent, listBelowRoot[-1], iFlags)
            if iOpened is not None:
                return iOpened
            sTarget = _fsReadLinkText(iParent, listBelowRoot[-1], sExpectedKind)
        finally:
            os.close(iParent)
        sCurrent = _fsResolveLinkLexically(listParts[:-1], sTarget)
        if not _fbBelowRoot(listRoot, _flistSplit(sCurrent), bAllowRoot):
            raise ContainerReadRefusedError(
                f"refused: '{listBelowRoot[-1]}' points to '{sTarget}', "
                "which is outside the project")
    raise ContainerReadRefusedError(
        f"refused: more than {I_MAX_LINK_HOPS} links in a row at "
        f"'{sStartPath}'")


def fiterStreamFileInsideRoot(sRealRoot, sFilePath):
    """Yield the file's bytes in bounded chunks; refusals raise on the first pull.

    The descriptor is opened before the first chunk is returned, so a
    caller that pulls once can answer an HTTP error before committing to
    a 200.
    """
    iFile = _fiOpenFollowingLinksInsideRoot(
        sRealRoot, sFilePath, os.O_RDONLY | os.O_NONBLOCK, False,
        "regular file")
    if not stat.S_ISREG(os.fstat(iFile).st_mode):
        os.close(iFile)
        raise ContainerReadRefusedError(
            f"refused: '{sFilePath}' is not a regular file")
    with os.fdopen(iFile, "rb") as fileSource:
        for baChunk in iter(lambda: fileSource.read(I_CHUNK_BYTES), b""):
            yield baChunk


def _fdictNewArchiveProgress():
    """What one archive walk accumulates: bytes yielded and entries skipped."""
    return {"iBytesYielded": 0, "iSkipped": 0}


def _finfoDescribe(sArchivePath, infoStat, sType):
    infoMember = tarfile.TarInfo(sArchivePath)
    infoMember.mode = stat.S_IMODE(infoStat.st_mode)
    infoMember.mtime = int(infoStat.st_mtime)
    infoMember.type = sType
    return infoMember


def _fbaHeaderOf(infoMember):
    return infoMember.tobuf(tarfile.PAX_FORMAT, "utf-8", "surrogateescape")


def _fiterTarMember(dictProgress, baHeader, fileSource=None, iSize=0):
    """Yield one member's header, its content in chunks, then block padding."""
    yield baHeader
    dictProgress["iBytesYielded"] += len(baHeader)
    iRemaining = iSize
    while iRemaining > 0:
        baChunk = fileSource.read(min(I_CHUNK_BYTES, iRemaining))
        if not baChunk:
            raise OSError("a file shrank while it was being archived")
        iRemaining -= len(baChunk)
        dictProgress["iBytesYielded"] += len(baChunk)
        yield baChunk
    iPadding = -iSize % _I_TAR_BLOCK_BYTES
    if iPadding:
        yield bytes(iPadding)
        dictProgress["iBytesYielded"] += iPadding


def _fiterFileMember(dictProgress, iParent, sArchivePath, sName):
    try:
        iFile = os.open(sName, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                        dir_fd=iParent)
    except OSError:
        dictProgress["iSkipped"] += 1
        return
    with os.fdopen(iFile, "rb") as fileSource:
        infoFileStat = os.fstat(iFile)
        if not stat.S_ISREG(infoFileStat.st_mode):
            dictProgress["iSkipped"] += 1
            return
        infoMember = _finfoDescribe(sArchivePath, infoFileStat, tarfile.REGTYPE)
        infoMember.size = infoFileStat.st_size
        yield from _fiterTarMember(
            dictProgress, _fbaHeaderOf(infoMember), fileSource,
            infoFileStat.st_size)


def _fiterEntryMember(dictProgress, iParent, sArchivePath, sName, iDepth):
    try:
        infoEntryStat = os.stat(sName, dir_fd=iParent, follow_symlinks=False)
    except FileNotFoundError:
        return
    if stat.S_ISLNK(infoEntryStat.st_mode):
        infoMember = _finfoDescribe(sArchivePath, infoEntryStat, tarfile.SYMTYPE)
        infoMember.linkname = os.readlink(sName, dir_fd=iParent)
        yield from _fiterTarMember(dictProgress, _fbaHeaderOf(infoMember))
    elif stat.S_ISDIR(infoEntryStat.st_mode):
        iChild = _fiOpenDirectoryWithoutFollowing(iParent, sName)
        try:
            yield from _fiterDirectoryMembers(
                dictProgress, iChild, sArchivePath, iDepth + 1)
        finally:
            os.close(iChild)
    elif stat.S_ISREG(infoEntryStat.st_mode):
        yield from _fiterFileMember(dictProgress, iParent, sArchivePath, sName)
    else:
        dictProgress["iSkipped"] += 1


def _fiterDirectoryMembers(dictProgress, iDirectory, sArchivePath, iDepth):
    infoMember = _finfoDescribe(
        sArchivePath, os.fstat(iDirectory), tarfile.DIRTYPE)
    yield from _fiterTarMember(dictProgress, _fbaHeaderOf(infoMember))
    if iDepth >= I_MAX_ARCHIVE_DEPTH:
        dictProgress["iSkipped"] += 1
        return
    for sName in sorted(os.listdir(iDirectory)):
        yield from _fiterEntryMember(
            dictProgress, iDirectory, sArchivePath + "/" + sName, sName,
            iDepth)


def fiterStreamDirectoryAsTar(sRealRoot, sDirectoryPath):
    """Yield a tar of the directory in bounded chunks; refusals raise on pull one.

    Members are rooted at the directory's own name. Links are archived as
    links and never followed below the starting directory; special files
    are skipped and counted in the log.
    """
    iRoot = _fiOpenFollowingLinksInsideRoot(
        sRealRoot, sDirectoryPath, os.O_RDONLY | os.O_DIRECTORY, True,
        "directory")
    dictProgress = _fdictNewArchiveProgress()
    sArchiveName = os.path.basename(sDirectoryPath.rstrip(os.sep)) or "archive"
    try:
        yield from _fiterDirectoryMembers(dictProgress, iRoot, sArchiveName, 0)
    finally:
        os.close(iRoot)
    baEnd = bytes(2 * _I_TAR_BLOCK_BYTES)
    dictProgress["iBytesYielded"] += len(baEnd)
    yield baEnd + bytes(-dictProgress["iBytesYielded"] % _I_TAR_RECORD_BYTES)
    if dictProgress["iSkipped"]:
        logger.warning(
            "Archived %s without %d special or too-deep entries",
            sDirectoryPath, dictProgress["iSkipped"])
