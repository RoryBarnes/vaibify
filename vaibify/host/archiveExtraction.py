"""Land a streamed tar on the host without trusting it.

A folder pulled from a container arrives as a tar built by a program that
runs INSIDE the container. That program is fixed text, but the interpreter
that runs it can be shadowed by the in-container agent, so what comes back
is untrusted bytes. This extractor treats every member as hostile input:
it accepts only plain directories, regular files and symbolic links; it
refuses names that are absolute or contain ``..``; it never writes beneath
a symbolic link (one in the destination already, or one the archive itself
made); and it creates every file under a private name and renames it into
place, so a failure leaves no half-written file where a good one stood.

It does not use ``TarFile.extract``: the ``data`` filter that would stand
in for these checks does not exist on every Python this project supports.

Host-only module: it uses ``os.path`` deliberately.
"""

import os
import stat
import tarfile
import tempfile

from vaibify.docker.dockerConnection import _BytesGeneratorPipe

__all__ = [
    "ArchiveExtractionError",
    "fiExtractTarStream",
]

I_CHUNK_BYTES = 1048576
_I_PERMISSION_MASK = 0o777


class ArchiveExtractionError(OSError):
    """The archive held something the extractor refuses to land."""


def _flistSafeParts(sMemberName, sRenameTopTo):
    listParts = sMemberName.split("/")
    if sMemberName.startswith("/") or any(
        sPart in ("", ".", "..") for sPart in listParts
    ):
        raise ArchiveExtractionError(
            f"The archive holds a member named {sMemberName!r}, which is "
            "absolute or climbs out of its folder; nothing further was "
            "written.")
    if sRenameTopTo is not None:
        listParts[0] = sRenameTopTo
    return listParts


def _fsPathBelow(sRoot, listParts):
    return os.path.join(sRoot, *listParts)


def _fnRequireNoSymlinkOnTheWay(sRoot, listParentParts):
    """Refuse when any existing directory on the way is a symbolic link."""
    sCurrent = sRoot
    for sPart in listParentParts:
        sCurrent = os.path.join(sCurrent, sPart)
        try:
            infoCurrent = os.lstat(sCurrent)
        except FileNotFoundError:
            return
        if stat.S_ISLNK(infoCurrent.st_mode) or not stat.S_ISDIR(
            infoCurrent.st_mode,
        ):
            raise ArchiveExtractionError(
                f"{sCurrent} is a link or not a folder, so the archive "
                "cannot be landed through it; nothing further was written.")


def _fnMakeDirectories(sRoot, listParts):
    _fnRequireNoSymlinkOnTheWay(sRoot, listParts)
    os.makedirs(_fsPathBelow(sRoot, listParts), exist_ok=True)


def _fnReplaceWithTemporary(sTarget, fnFill, iMode, fMtime):
    """Write a file under a private name beside ``sTarget``, then rename."""
    iDescriptor, sTemporary = tempfile.mkstemp(
        dir=os.path.dirname(sTarget), prefix=".vaibify-pull-")
    try:
        with os.fdopen(iDescriptor, "wb") as fileOut:
            fnFill(fileOut)
            fileOut.flush()
            os.fsync(fileOut.fileno())
        os.chmod(sTemporary, iMode)
        os.utime(sTemporary, (fMtime, fMtime))
        if os.path.isdir(sTarget) and not os.path.islink(sTarget):
            raise ArchiveExtractionError(
                f"{sTarget} is a folder; the archive holds a file of that "
                "name.")
        os.replace(sTemporary, sTarget)
    except BaseException:
        try:
            os.unlink(sTemporary)
        except OSError:
            pass
        raise


def _fiModeUnderUmask(iArchiveMode):
    """Return the member's permission bits under the researcher's umask.

    The set-id and sticky bits are never carried: an archive built inside
    a container has no business making a file on the host run as someone.
    """
    iUmask = os.umask(0)
    os.umask(iUmask)
    return iArchiveMode & _I_PERMISSION_MASK & ~iUmask


def _fnLandFile(sRoot, listParts, fileTar, infoMember):
    _fnMakeDirectories(sRoot, listParts[:-1])
    sTarget = _fsPathBelow(sRoot, listParts)
    fileMember = fileTar.extractfile(infoMember)

    def fnFill(fileOut):
        for baChunk in iter(lambda: fileMember.read(I_CHUNK_BYTES), b""):
            fileOut.write(baChunk)

    _fnReplaceWithTemporary(
        sTarget, fnFill, _fiModeUnderUmask(infoMember.mode),
        infoMember.mtime)


def _fnLandSymlink(sRoot, listParts, infoMember):
    _fnMakeDirectories(sRoot, listParts[:-1])
    sTarget = _fsPathBelow(sRoot, listParts)
    if os.path.lexists(sTarget):
        if os.path.isdir(sTarget) and not os.path.islink(sTarget):
            raise ArchiveExtractionError(
                f"{sTarget} is a folder; the archive holds a link of that "
                "name.")
        os.unlink(sTarget)
    os.symlink(infoMember.linkname, sTarget)


def _fbLandMember(sRoot, infoMember, fileTar, sRenameTopTo):
    """Land one member; return False when its type is not accepted."""
    listParts = _flistSafeParts(infoMember.name, sRenameTopTo)
    if infoMember.isdir():
        _fnMakeDirectories(sRoot, listParts)
        return True
    if infoMember.isreg():
        _fnLandFile(sRoot, listParts, fileTar, infoMember)
        return True
    if infoMember.issym():
        _fnLandSymlink(sRoot, listParts, infoMember)
        return True
    return False


def fiExtractTarStream(iterChunks, sDestinationDirectory, sRenameTopTo=None):
    """Land the archive under ``sDestinationDirectory``; return members skipped.

    ``sRenameTopTo`` replaces the first path component of every member, which
    is how a folder is copied to a destination that does not exist yet and
    takes the name the researcher gave it. The destination directory must
    exist. Hard links, devices and FIFOs are skipped and counted.
    """
    sRoot = os.path.realpath(sDestinationDirectory)
    iSkipped = 0
    try:
        with tarfile.open(
            fileobj=_BytesGeneratorPipe(iterChunks), mode="r|",
        ) as fileTar:
            for infoMember in fileTar:
                if not _fbLandMember(
                    sRoot, infoMember, fileTar, sRenameTopTo,
                ):
                    iSkipped += 1
    except tarfile.TarError as error:
        raise ArchiveExtractionError(
            f"The archive from the container could not be read: {error}"
        ) from error
    return iSkipped
