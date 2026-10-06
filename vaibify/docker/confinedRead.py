"""The symlink-safe, unprivileged reads that stream container bytes out.

A download used to ask the daemon for an archive (``get_archive``) or run
``docker cp``. Both read as root through the daemon, and checking the real
path first only moves the race to the gap between two round trips: an
agent that swaps one component of the path for a symlink after the check
makes the daemon read a file the researcher never named.

This module owns the replacement, the read-side twin of
:mod:`vaibify.docker.confinedWrite`. FIXED programs run as the container
user (never root), walk the path one component at a time with
``O_NOFOLLOW`` against the directory descriptor they already hold, and
write the answer to standard output in bounded chunks, so a file of any
size streams in bounded memory and a swapped component is refused rather
than followed.

Two programs live here. The file reader streams one regular file. If the
final component is a symlink it is FOLLOWED, but only lexically and only
inside the authorized root: the link's target is resolved as text against
the root and walked again with the same ``O_NOFOLLOW`` walk, up to a
small number of links in a row. A target outside the root is refused with
the link and the target named. The directory reader streams a tar of a
directory tree, walking with held descriptors and never following a link
below the starting directory: links are stored in the archive as links,
and devices, sockets and FIFOs are skipped and counted. An archive
therefore never contains a member beneath a symlink member, which is what
makes extracting an agent-authored archive safe.

Like the writers, the programs are module source text; a caller supplies
values that are embedded through ``repr`` as Python literals, and only
``str`` values are admitted, so a path never becomes program or shell
syntax.
"""

import errno
import posixpath

from vaibify.docker.confinedWrite import (
    I_FAILED_EXIT_CODE,
    I_NOT_FOUND_EXIT_CODE,
    I_REFUSED_EXIT_CODE,
    S_SHARED_PROGRAM_HELPERS,
)

__all__ = [
    "ContainerReadRefusedError",
    "I_NOT_FOUND_EXIT_CODE",
    "S_SKIPPED_LINE_PREFIX",
    "fiParseSkippedCount",
    "fnRaiseWhenReadFailed",
    "fsRenderConfinedArchiveProgram",
    "fsRenderConfinedReadProgram",
]

I_MAX_LINK_HOPS = 8
I_MAX_ARCHIVE_DEPTH = 100
S_SKIPPED_LINE_PREFIX = "vaibify-skipped="

_S_ROOT_SLOT = "@@AUTHORIZED_ROOT@@"
_S_PATH_SLOT = "@@TARGET_PATH@@"

# Opening a path under the authorized root, shared by both programs. A
# final symlink is read as text and resolved lexically against the root,
# then the WHOLE target path is walked again with ``O_NOFOLLOW``, so a
# link to a link, or a link whose target has a symlinked directory in it,
# is refused at the component that is one. The programs define
# ``fnStop``, ``fnRefuse``, ``sAuthorizedRoot`` and the exit codes.
_S_READ_PROGRAM_HELPERS = '''listRoot = flistSplit(sAuthorizedRoot)
def fbBelowRoot(sPath, bAllowRoot):
    listParts = flistSplit(sPath)
    if listParts[:len(listRoot)] != listRoot:
        return False
    return bAllowRoot or len(listParts) > len(listRoot)
def fiOpenParentDirectory(listParts):
    iDirectory = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
    for sName in listParts[:-1]:
        try:
            iNext = fiOpenDirectoryWithoutFollowing(iDirectory, sName)
        except FileNotFoundError:
            fnStop(I_NOT_FOUND, "not found: '" + sName + "'")
        os.close(iDirectory)
        iDirectory = iNext
    return iDirectory
def fsReadLinkText(iParent, sName):
    try:
        return os.readlink(sName, dir_fd=iParent)
    except OSError:
        fnRefuse("refused: '" + sName + "' is not a "
                 + sExpectedKind + " I may read")
def fsResolveLinkLexically(listParts, sTarget):
    if sTarget.startswith("/"):
        return posixpath.normpath(sTarget)
    sParent = "/" + "/".join(listParts[:-1])
    return posixpath.normpath(sParent + "/" + sTarget)
def fiOpenFollowingLinksInsideRoot(sStartPath, iFlags, bAllowRoot):
    sCurrent = sStartPath
    for iHop in range(I_MAX_LINK_HOPS + 1):
        if not fbBelowRoot(sCurrent, bAllowRoot):
            fnRefuse("refused: the path is not below its authorized root")
        listParts = flistSplit(sCurrent)
        if not listParts:
            return os.open("/", iFlags)
        iParent = fiOpenParentDirectory(listParts)
        try:
            try:
                return os.open(listParts[-1], iFlags | os.O_NOFOLLOW,
                               dir_fd=iParent)
            except FileNotFoundError:
                fnStop(I_NOT_FOUND, "not found: '" + listParts[-1] + "'")
            except OSError as error:
                if error.errno not in (errno.ELOOP, errno.ENOTDIR):
                    raise
            sTarget = fsReadLinkText(iParent, listParts[-1])
        finally:
            os.close(iParent)
        sCurrent = fsResolveLinkLexically(listParts, sTarget)
        if not fbBelowRoot(sCurrent, bAllowRoot):
            fnRefuse("refused: '" + listParts[-1] + "' points to '"
                     + sTarget + "', which is outside the project")
    fnRefuse("refused: more than " + str(I_MAX_LINK_HOPS)
             + " links in a row at '" + sStartPath + "'")
'''

_S_CONFINED_READ_PROGRAM = '''import errno, os, posixpath, stat, sys
sAuthorizedRoot = @@AUTHORIZED_ROOT@@
sFilePath = @@TARGET_PATH@@
sExpectedKind = "regular file"
I_REFUSED = @@REFUSED_EXIT_CODE@@
I_NOT_FOUND = @@NOT_FOUND_EXIT_CODE@@
I_FAILED = @@FAILED_EXIT_CODE@@
I_MAX_LINK_HOPS = @@MAX_LINK_HOPS@@
I_CHUNK_BYTES = 1048576
def fnStop(iExitCode, sReason):
    sys.stderr.write(sReason + "\\n")
    sys.exit(iExitCode)
def fnRefuse(sReason):
    fnStop(I_REFUSED, sReason)
''' + S_SHARED_PROGRAM_HELPERS + _S_READ_PROGRAM_HELPERS + '''iFile = fiOpenFollowingLinksInsideRoot(
    sFilePath, os.O_RDONLY | os.O_NONBLOCK, False)
if not stat.S_ISREG(os.fstat(iFile).st_mode):
    fnRefuse("refused: '" + sFilePath + "' is not a regular file")
try:
    with os.fdopen(iFile, "rb") as fileSource:
        for baChunk in iter(lambda: fileSource.read(I_CHUNK_BYTES), b""):
            sys.stdout.buffer.write(baChunk)
    sys.stdout.buffer.flush()
except BrokenPipeError:
    sys.exit(I_FAILED)
'''

_S_CONFINED_ARCHIVE_PROGRAM = '''import errno, os, posixpath, stat, sys, tarfile
sAuthorizedRoot = @@AUTHORIZED_ROOT@@
sDirectoryPath = @@TARGET_PATH@@
sExpectedKind = "directory"
I_REFUSED = @@REFUSED_EXIT_CODE@@
I_NOT_FOUND = @@NOT_FOUND_EXIT_CODE@@
I_FAILED = @@FAILED_EXIT_CODE@@
I_MAX_LINK_HOPS = @@MAX_LINK_HOPS@@
I_MAX_DEPTH = @@MAX_ARCHIVE_DEPTH@@
iSkipped = 0
def fnStop(iExitCode, sReason):
    sys.stderr.write(sReason + "\\n")
    sys.exit(iExitCode)
def fnRefuse(sReason):
    fnStop(I_REFUSED, sReason)
''' + S_SHARED_PROGRAM_HELPERS + _S_READ_PROGRAM_HELPERS + '''def finfoDescribe(sArchivePath, statEntry, sType):
    infoMember = tarfile.TarInfo(sArchivePath)
    infoMember.mode = stat.S_IMODE(statEntry.st_mode)
    infoMember.mtime = int(statEntry.st_mtime)
    infoMember.type = sType
    return infoMember
def fnAddLink(tarOut, iParent, sArchivePath, sName, statEntry):
    infoMember = finfoDescribe(sArchivePath, statEntry, tarfile.SYMTYPE)
    infoMember.linkname = os.readlink(sName, dir_fd=iParent)
    tarOut.addfile(infoMember)
def fnAddFile(tarOut, iParent, sArchivePath, sName):
    global iSkipped
    try:
        iFile = os.open(sName, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                        dir_fd=iParent)
    except OSError:
        iSkipped += 1
        return
    with os.fdopen(iFile, "rb") as fileSource:
        statFile = os.fstat(iFile)
        if not stat.S_ISREG(statFile.st_mode):
            iSkipped += 1
            return
        infoMember = finfoDescribe(sArchivePath, statFile, tarfile.REGTYPE)
        infoMember.size = statFile.st_size
        tarOut.addfile(infoMember, fileSource)
def fnAddDirectory(tarOut, iDirectory, sArchivePath, iDepth):
    global iSkipped
    infoMember = finfoDescribe(sArchivePath, os.fstat(iDirectory), tarfile.DIRTYPE)
    tarOut.addfile(infoMember)
    if iDepth >= I_MAX_DEPTH:
        iSkipped += 1
        return
    for sName in sorted(os.listdir(iDirectory)):
        fnAddEntry(tarOut, iDirectory, sArchivePath + "/" + sName, sName, iDepth)
def fnAddEntry(tarOut, iParent, sArchivePath, sName, iDepth):
    global iSkipped
    try:
        statEntry = os.stat(sName, dir_fd=iParent, follow_symlinks=False)
    except FileNotFoundError:
        return
    if stat.S_ISLNK(statEntry.st_mode):
        fnAddLink(tarOut, iParent, sArchivePath, sName, statEntry)
    elif stat.S_ISDIR(statEntry.st_mode):
        iChild = fiOpenDirectoryWithoutFollowing(iParent, sName)
        try:
            fnAddDirectory(tarOut, iChild, sArchivePath, iDepth + 1)
        finally:
            os.close(iChild)
    elif stat.S_ISREG(statEntry.st_mode):
        fnAddFile(tarOut, iParent, sArchivePath, sName)
    else:
        iSkipped += 1
iRoot = fiOpenFollowingLinksInsideRoot(
    sDirectoryPath, os.O_RDONLY | os.O_DIRECTORY, True)
sArchiveName = posixpath.basename(sDirectoryPath.rstrip("/")) or "archive"
try:
    with tarfile.open(fileobj=sys.stdout.buffer, mode="w|",
                      format=tarfile.PAX_FORMAT) as tarOut:
        fnAddDirectory(tarOut, iRoot, sArchiveName, 0)
    sys.stdout.buffer.flush()
except BrokenPipeError:
    sys.exit(I_FAILED)
sys.stderr.write("@@SKIPPED_PREFIX@@" + str(iSkipped) + "\\n")
'''


class ContainerReadRefusedError(OSError):
    """The confined read declined a path; nothing was sent.

    Distinct from an I/O failure so a route can answer 403 for a path
    the contract forbids -- a link leading out of the project, a
    symlinked component -- and 500 only for a read that genuinely failed.
    A path that is simply absent raises ``FileNotFoundError`` instead.
    """


def _fsCheckedAbsolutePath(sPath, sLabel):
    if not isinstance(sPath, str) or not sPath.startswith("/"):
        raise ValueError(f"{sLabel} must be an absolute path string")
    if any(ord(sCharacter) < 32 or ord(sCharacter) == 127
           for sCharacter in sPath):
        raise ValueError(f"{sLabel} carries a control character")
    return posixpath.normpath(sPath)


def _fsRenderProgram(sTemplate, sTargetPath, sAuthorizedRoot, sLabel):
    sRoot = _fsCheckedAbsolutePath(sAuthorizedRoot or "/", "the root")
    sPath = _fsCheckedAbsolutePath(sTargetPath, sLabel)
    if not (sPath + "/").startswith(sRoot.rstrip("/") + "/"):
        raise ValueError(f"{sTargetPath!r} is not below {sRoot!r}")
    return (
        sTemplate
        .replace(_S_ROOT_SLOT, repr(sRoot))
        .replace(_S_PATH_SLOT, repr(sPath))
        .replace("@@REFUSED_EXIT_CODE@@", repr(I_REFUSED_EXIT_CODE))
        .replace("@@NOT_FOUND_EXIT_CODE@@", repr(I_NOT_FOUND_EXIT_CODE))
        .replace("@@FAILED_EXIT_CODE@@", repr(I_FAILED_EXIT_CODE))
        .replace("@@MAX_LINK_HOPS@@", repr(I_MAX_LINK_HOPS))
        .replace("@@MAX_ARCHIVE_DEPTH@@", repr(I_MAX_ARCHIVE_DEPTH))
        .replace("@@SKIPPED_PREFIX@@", S_SKIPPED_LINE_PREFIX)
    )


def fsRenderConfinedReadProgram(sFilePath, sAuthorizedRoot=None):
    """Return the program text that streams ``sFilePath`` to stdout.

    ``sAuthorizedRoot`` defaults to the filesystem root, which still
    makes the walk symlink-free end to end; a project passes its own
    root, which is also the boundary a followed link may not cross.
    """
    return _fsRenderProgram(
        _S_CONFINED_READ_PROGRAM, sFilePath, sAuthorizedRoot,
        "the file path",
    )


def fsRenderConfinedArchiveProgram(sDirectoryPath, sAuthorizedRoot=None):
    """Return the program text that streams a tar of a directory to stdout.

    The directory may be the authorized root itself. Archive member
    names are rooted at the directory's own name, so extracting the
    archive recreates one folder.
    """
    return _fsRenderProgram(
        _S_CONFINED_ARCHIVE_PROGRAM, sDirectoryPath, sAuthorizedRoot,
        "the directory path",
    )


def fnRaiseWhenReadFailed(iExitCode, sStderr, sPath):
    """Raise the right error for a failed confined read; else return.

    A path that is absent raises ``FileNotFoundError``, a refusal raises
    :class:`ContainerReadRefusedError` and anything else ``OSError``.
    """
    if iExitCode == 0:
        return
    listLines = (sStderr or "").strip().splitlines()
    sLastLine = listLines[-1] if listLines else "no error text"
    if iExitCode == I_NOT_FOUND_EXIT_CODE:
        raise FileNotFoundError(errno.ENOENT, f"{sPath}: {sLastLine}")
    if iExitCode == I_REFUSED_EXIT_CODE:
        raise ContainerReadRefusedError(
            f"Read of {sPath} refused: {sLastLine}"
        )
    raise OSError(f"Cannot read {sPath} in the container: {sLastLine}")


def fiParseSkippedCount(sStderr):
    """Return how many special files the archive program skipped, or 0."""
    for sLine in reversed((sStderr or "").strip().splitlines()):
        if sLine.startswith(S_SKIPPED_LINE_PREFIX):
            sCount = sLine[len(S_SKIPPED_LINE_PREFIX):]
            return int(sCount) if sCount.isdigit() else 0
    return 0
