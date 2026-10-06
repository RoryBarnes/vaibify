"""The symlink-safe, unprivileged file write that lands bytes in a container.

The backend used to write a file by handing the daemon a tarball
(``put_archive``). The daemon extracts as root and resolves every symlink
it meets inside the container, so a symlink planted by the in-container
agent redirected a backend write anywhere in the container filesystem,
and checking the real path first only moved the race to the gap between
two daemon round trips.

This module owns the replacement. A FIXED program runs as the container
user (never root) and reads the file's bytes from its standard input. The
program walks the path one component at a time with ``O_NOFOLLOW`` and
``O_DIRECTORY`` against the directory descriptor it already holds, so a
swap of any component for a symlink is refused rather than followed, and
a swap AFTER a component was opened cannot redirect the write because the
descriptor still names the original directory. Forbidden metadata names
are refused by name before they are opened, the file is created under a
private name, ``fchmod``-ed to the requested mode and renamed into place.

The bytes are copied from standard input to the private file in chunks,
so a file of any size is written in bounded memory. A caller may state
how many bytes it means to send: a stream that ends short (a dropped
connection) or runs long is refused and the old file is untouched,
because the rename never ran. A caller may also forbid replacement, in
which case an existing file is refused with its own exit status so a
route can answer "already there" rather than "refused".

The tar-entry builder this replaced (``_finfoBuildTarEntry``) stamped a
uid and gid onto an archive entry so the daemon would not create the file
root-owned. Ownership needs no stamp any more: the file is created by the
container user, so that user owns it.

A second fixed program receives a whole directory tree. It reads a tar
stream from its standard input and lands each member relative to
directory descriptors it holds, so the same refusal of a symlinked
component covers every member. It replaces the archive hand-off
(``put_archive``) the tree copy used, which had the same two defects as
the single-file one and a third: when an archive member's name matched an
existing directory the daemon reached through a planted symlink, it
changed that directory's owner, handing a root-owned directory to the
container user.

Like the typed reads, the program is module source text; a caller
supplies values that are embedded through ``repr`` as Python literals,
and only ``str`` and ``int`` values are admitted, so a path never becomes
program or shell syntax.
"""

import errno
import posixpath

__all__ = [
    "ContainerWriteExistsError",
    "ContainerWriteRefusedError",
    "I_EXISTS_EXIT_CODE",
    "I_FAILED_EXIT_CODE",
    "I_NOT_FOUND_EXIT_CODE",
    "I_NO_SPACE_EXIT_CODE",
    "I_REFUSED_EXIT_CODE",
    "S_SHARED_PROGRAM_HELPERS",
    "T_WRITE_DENYLISTED_NAMES",
    "fsRenderConfinedTreeProgram",
    "fsRenderConfinedWriteProgram",
    "fnRaiseWhenTreeWriteFailed",
    "fnRaiseWhenWriteFailed",
]

I_REFUSED_EXIT_CODE = 3
I_EXISTS_EXIT_CODE = 4
I_NOT_FOUND_EXIT_CODE = 5
I_NO_SPACE_EXIT_CODE = 6
I_FAILED_EXIT_CODE = 1
S_LANDED_LINE_PREFIX = "vaibify-landed="
I_DEFAULT_FILE_MODE = 0o644

# Names the backend never writes through when a caller-supplied path
# reaches a project: git internals, vaibify metadata and the project
# contract file. ``pipelineServer.fnRejectWriteDenylistedPath`` applies
# the same names lexically before a request reaches the container.
T_WRITE_DENYLISTED_NAMES = (".git", ".vaibify", "project.json")

_S_ROOT_SLOT = "@@AUTHORIZED_ROOT@@"
_S_PATH_SLOT = "@@FILE_PATH@@"
_S_MODE_SLOT = "@@FILE_MODE@@"
_S_FORBIDDEN_SLOT = "@@FORBIDDEN_NAMES@@"
_S_DESTINATION_SLOT = "@@DESTINATION@@"
_S_CREATE_SLOT = "@@CREATE_DESTINATION@@"
_S_REPLACE_SLOT = "@@REPLACE_ALLOWED@@"
_S_PARENTS_SLOT = "@@CREATE_PARENTS@@"
_S_EXPECTED_BYTES_SLOT = "@@EXPECTED_BYTES@@"

# The two helpers every confined program shares, the readers
# (``confinedRead``) included. They are one text, not several copies,
# because a fix to how a directory is opened has to land in every
# program together: each program defines ``fnRefuse`` itself (the tree
# program also reports how many members had landed) and these helpers
# call it.
S_SHARED_PROGRAM_HELPERS = '''def flistSplit(sPath):
    return [sPart for sPart in sPath.split("/") if sPart]
def fiOpenDirectoryWithoutFollowing(iParent, sName):
    iFlags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    try:
        return os.open(sName, iFlags, dir_fd=iParent)
    except OSError as error:
        if error.errno in (errno.ELOOP, errno.ENOTDIR):
            fnRefuse("refused: '" + sName + "' is a symlink or not a directory")
        raise
'''

_S_CONFINED_WRITE_PROGRAM = '''import errno, os, secrets, stat, sys
sAuthorizedRoot = @@AUTHORIZED_ROOT@@
sFilePath = @@FILE_PATH@@
iMode = @@FILE_MODE@@
listForbiddenNames = @@FORBIDDEN_NAMES@@
bReplaceAllowed = @@REPLACE_ALLOWED@@
iExpectedBytes = @@EXPECTED_BYTES@@
bCreateParents = @@CREATE_PARENTS@@
I_REFUSED = @@REFUSED_EXIT_CODE@@
I_EXISTS = @@EXISTS_EXIT_CODE@@
I_NOT_FOUND = @@NOT_FOUND_EXIT_CODE@@
I_NO_SPACE = @@NO_SPACE_EXIT_CODE@@
I_CHUNK_BYTES = 1048576
def fnStop(iExitCode, sReason):
    sys.stderr.write(sReason + "\\n")
    sys.exit(iExitCode)
def fnRefuse(sReason):
    fnStop(I_REFUSED, sReason)
''' + S_SHARED_PROGRAM_HELPERS + '''listRoot = flistSplit(sAuthorizedRoot)
listParts = flistSplit(sFilePath)
if listParts[:len(listRoot)] != listRoot or len(listParts) <= len(listRoot):
    fnRefuse("refused: the path is not below its authorized root")
for sName in listParts[len(listRoot):]:
    if sName in listForbiddenNames:
        fnRefuse("refused: writes through '" + sName + "' are not permitted")
def fiOpenParentDirectory(iParent, sName, iDepth):
    try:
        return fiOpenDirectoryWithoutFollowing(iParent, sName)
    except FileNotFoundError:
        if not bCreateParents or iDepth < len(listRoot):
            fnStop(I_NOT_FOUND, "not found: '" + sName + "'")
    try:
        os.mkdir(sName, 0o755, dir_fd=iParent)
    except FileExistsError:
        pass
    return fiOpenDirectoryWithoutFollowing(iParent, sName)
iDirectory = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
for iDepth, sName in enumerate(listParts[:-1]):
    iNext = fiOpenParentDirectory(iDirectory, sName, iDepth)
    os.close(iDirectory)
    iDirectory = iNext
sFinal = listParts[-1]
def fnRefuseWhenFinalIsUnwritable():
    try:
        iExisting = os.stat(sFinal, dir_fd=iDirectory, follow_symlinks=False).st_mode
    except FileNotFoundError:
        return
    if stat.S_ISLNK(iExisting) or stat.S_ISDIR(iExisting):
        fnRefuse("refused: '" + sFinal + "' is a symlink or a directory")
    if not bReplaceAllowed:
        fnStop(I_EXISTS, "refused: '" + sFinal + "' already exists")
def fnRefuseWhenSizeDiffers(iReceived):
    if iExpectedBytes is not None and iReceived != iExpectedBytes:
        fnRefuse("refused: " + str(iReceived) + " bytes arrived but "
                 + str(iExpectedBytes) + " were expected")
def fnCopyStdinTo(fileTemporary):
    iReceived = 0
    for baChunk in iter(lambda: sys.stdin.buffer.read(I_CHUNK_BYTES), b""):
        iReceived += len(baChunk)
        if iExpectedBytes is not None and iReceived > iExpectedBytes:
            fnRefuseWhenSizeDiffers(iReceived)
        fileTemporary.write(baChunk)
    fnRefuseWhenSizeDiffers(iReceived)
    fileTemporary.flush()
    os.fsync(fileTemporary.fileno())
def fnCommitTemporary():
    if bReplaceAllowed:
        os.rename(sTemporary, sFinal, src_dir_fd=iDirectory, dst_dir_fd=iDirectory)
        return
    try:
        os.link(sTemporary, sFinal, src_dir_fd=iDirectory,
                dst_dir_fd=iDirectory, follow_symlinks=False)
    except FileExistsError:
        fnStop(I_EXISTS, "refused: '" + sFinal + "' already exists")
    except (OSError, NotImplementedError):
        fnRefuseWhenFinalIsUnwritable()
        os.rename(sTemporary, sFinal, src_dir_fd=iDirectory, dst_dir_fd=iDirectory)
        return
    os.unlink(sTemporary, dir_fd=iDirectory)
fnRefuseWhenFinalIsUnwritable()
sTemporary = ".vaibify-write-" + secrets.token_hex(8)
iFile = os.open(
    sTemporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
    0o600, dir_fd=iDirectory,
)
try:
    os.fchmod(iFile, iMode)
    with os.fdopen(iFile, "wb") as fileTemporary:
        fnCopyStdinTo(fileTemporary)
    fnCommitTemporary()
except BaseException as error:
    try:
        os.unlink(sTemporary, dir_fd=iDirectory)
    except OSError:
        pass
    if isinstance(error, OSError) and error.errno in (errno.ENOSPC, errno.EDQUOT):
        fnStop(I_NO_SPACE, "failed: the container's disk is full")
    raise
'''


# The tree receiver. It runs as the container user and reads a tar stream.
# A member is never resolved by path string: every directory is opened
# with O_NOFOLLOW against the descriptor above it, files are created
# under a private name and renamed into place (a rename replaces a symlink
# at the target name and never follows it), and a symlink member is made
# with symlink() and renamed the same way. Hard links, devices and FIFOs
# are refused. The last stderr line of any failure reports how many
# members had landed, so a partial copy says so.
_S_CONFINED_TREE_PROGRAM = '''import errno, os, secrets, stat, sys, tarfile
sAuthorizedRoot = @@AUTHORIZED_ROOT@@
sDestination = @@DESTINATION@@
bCreateDestination = @@CREATE_DESTINATION@@
listForbiddenNames = @@FORBIDDEN_NAMES@@
I_REFUSED = @@REFUSED_EXIT_CODE@@
I_FAILED = @@FAILED_EXIT_CODE@@
I_CHUNK_BYTES = 1048576
iMembersLanded = 0
def fnStop(iExitCode, sReason):
    sys.stderr.write(sReason + "\\n")
    sys.stderr.write("@@LANDED_PREFIX@@" + str(iMembersLanded) + "\\n")
    sys.exit(iExitCode)
def fnRefuse(sReason):
    fnStop(I_REFUSED, sReason)
''' + S_SHARED_PROGRAM_HELPERS + '''def fiOpenDirectoryCreating(iParent, sName, iMode, bCreate):
    try:
        return fiOpenDirectoryWithoutFollowing(iParent, sName)
    except FileNotFoundError:
        if not bCreate:
            fnRefuse("refused: '" + sName + "' does not exist")
    try:
        os.mkdir(sName, 0o700, dir_fd=iParent)
        bMade = True
    except FileExistsError:
        bMade = False
    iNew = fiOpenDirectoryWithoutFollowing(iParent, sName)
    if bMade:
        os.fchmod(iNew, iMode)
    return iNew
def fiOpenDestination():
    listRoot = flistSplit(sAuthorizedRoot)
    listParts = flistSplit(sDestination)
    if listParts[:len(listRoot)] != listRoot:
        fnRefuse("refused: the destination is not below its authorized root")
    iDirectory = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
    for iDepth, sName in enumerate(listParts):
        bBelowRoot = iDepth >= len(listRoot)
        if bBelowRoot and sName in listForbiddenNames:
            fnRefuse("refused: writes through '" + sName + "' are not permitted")
        iNext = fiOpenDirectoryCreating(
            iDirectory, sName, 0o755, bCreateDestination and bBelowRoot)
        os.close(iDirectory)
        iDirectory = iNext
    return iDirectory
def fiOpenChain(listNames, iFinalMode):
    iCurrent = os.dup(iDestination)
    for iDepth, sName in enumerate(listNames):
        iMode = iFinalMode if iDepth == len(listNames) - 1 else 0o755
        iNext = fiOpenDirectoryCreating(iCurrent, sName, iMode, True)
        os.close(iCurrent)
        iCurrent = iNext
    return iCurrent
def fnRefuseWhenDirectory(iParent, sName):
    try:
        iExisting = os.stat(sName, dir_fd=iParent, follow_symlinks=False).st_mode
    except FileNotFoundError:
        return
    if stat.S_ISDIR(iExisting):
        fnRefuse("refused: '" + sName + "' is an existing directory, not a file")
def fnRemoveQuietly(iParent, sName):
    try:
        os.unlink(sName, dir_fd=iParent)
    except OSError:
        pass
def fnLandDirectory(listNames, infoMember):
    os.close(fiOpenChain(listNames, (infoMember.mode & 0o777) | 0o700))
def fnLandFile(listNames, infoMember, tarSource):
    iParent = fiOpenChain(listNames[:-1], 0o755)
    try:
        sFinal = listNames[-1]
        fnRefuseWhenDirectory(iParent, sFinal)
        sTemporary = ".vaibify-write-" + secrets.token_hex(8)
        iFlags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
        iFile = os.open(sTemporary, iFlags, 0o600, dir_fd=iParent)
        try:
            with os.fdopen(iFile, "wb") as fileTemporary:
                fileMember = tarSource.extractfile(infoMember)
                for baChunk in iter(lambda: fileMember.read(I_CHUNK_BYTES), b""):
                    fileTemporary.write(baChunk)
                fileTemporary.flush()
                os.fchmod(iFile, infoMember.mode & 0o777)
                os.utime(iFile, (infoMember.mtime, infoMember.mtime))
            os.rename(sTemporary, sFinal, src_dir_fd=iParent, dst_dir_fd=iParent)
        except BaseException:
            fnRemoveQuietly(iParent, sTemporary)
            raise
    finally:
        os.close(iParent)
def fnLandSymlink(listNames, infoMember):
    iParent = fiOpenChain(listNames[:-1], 0o755)
    try:
        sFinal = listNames[-1]
        fnRefuseWhenDirectory(iParent, sFinal)
        sTemporary = ".vaibify-write-" + secrets.token_hex(8)
        os.symlink(infoMember.linkname, sTemporary, dir_fd=iParent)
        try:
            os.rename(sTemporary, sFinal, src_dir_fd=iParent, dst_dir_fd=iParent)
        except BaseException:
            fnRemoveQuietly(iParent, sTemporary)
            raise
    finally:
        os.close(iParent)
def fnLandMember(infoMember, tarSource):
    sName = infoMember.name
    if infoMember.isdir() and sName in ("", "."):
        return
    listNames = sName.split("/")
    if sName.startswith("/") or any(sPart in ("", ".", "..") for sPart in listNames):
        fnRefuse("refused: member '" + sName + "' has an absolute or unsafe name")
    for sPart in listNames:
        if sPart in listForbiddenNames:
            fnRefuse("refused: writes through '" + sPart + "' are not permitted")
    if infoMember.isdir():
        fnLandDirectory(listNames, infoMember)
    elif infoMember.isreg():
        fnLandFile(listNames, infoMember, tarSource)
    elif infoMember.issym():
        fnLandSymlink(listNames, infoMember)
    else:
        fnRefuse("refused: member '" + sName + "' is a hard link or a special file")
try:
    iDestination = fiOpenDestination()
    with tarfile.open(fileobj=sys.stdin.buffer, mode="r|") as tarSource:
        for infoMember in tarSource:
            fnLandMember(infoMember, tarSource)
            iMembersLanded += 1
except SystemExit:
    raise
except BaseException as error:
    fnStop(I_FAILED, "failed: " + type(error).__name__ + ": " + str(error))
'''


class ContainerWriteRefusedError(OSError):
    """The confined write declined a path; nothing was written.

    Distinct from an I/O failure so a route can answer 403 for a path
    the contract forbids and 500 only for a write that genuinely failed.
    It is not a carrier refusal and says nothing about admission.
    """


class ContainerWriteExistsError(ContainerWriteRefusedError):
    """The target already exists and the caller forbade replacing it.

    A refusal like any other (nothing was written, the old file is
    untouched), but a route answers 409 for it rather than 403: the path
    is allowed, the name is taken.
    """


def _fsCheckedAbsolutePath(sPath, sLabel):
    """Return a normalized absolute path or raise ValueError naming why."""
    if not isinstance(sPath, str) or not sPath.startswith("/"):
        raise ValueError(f"{sLabel} must be an absolute path string")
    if any(ord(sCharacter) < 32 or ord(sCharacter) == 127
           for sCharacter in sPath):
        raise ValueError(f"{sLabel} carries a control character")
    return posixpath.normpath(sPath)


def _fnRequireStringTuple(tNames):
    if not isinstance(tNames, (list, tuple)) or not all(
        isinstance(sName, str) for sName in tNames
    ):
        raise TypeError(
            "forbidden names must be a flat sequence of strings; only "
            "str has a repr that is a string literal"
        )


def _fnRequireExpectedByteCount(iExpectedBytes):
    if iExpectedBytes is None:
        return
    if (isinstance(iExpectedBytes, bool)
            or not isinstance(iExpectedBytes, int) or iExpectedBytes < 0):
        raise ValueError("the expected byte count must be None or a "
                         "non-negative integer")


def fsRenderConfinedWriteProgram(
    sFilePath, iMode=None, sAuthorizedRoot=None, tForbiddenNames=(),
    bReplaceAllowed=True, iExpectedBytes=None, bCreateParents=False,
):
    """Return the program text that writes ``sFilePath`` from stdin.

    ``sAuthorizedRoot`` defaults to the filesystem root, which still
    makes the walk symlink-free end to end. Every component below the
    root is checked against ``tForbiddenNames`` by the program itself,
    in addition to whatever lexical check the caller ran first.

    ``bReplaceAllowed`` False refuses an existing target with
    ``I_EXISTS_EXIT_CODE``, atomically where the filesystem can hard
    link. ``iExpectedBytes`` refuses a stream whose length differs; in
    both cases the old file is untouched. ``bCreateParents`` makes any
    missing directory BELOW the authorized root, one component at a time
    with ``O_NOFOLLOW`` against the descriptor already held; a missing
    directory at or above the root is never created.
    """
    sRoot = _fsCheckedAbsolutePath(sAuthorizedRoot or "/", "the root")
    sPath = _fsCheckedAbsolutePath(sFilePath, "the file path")
    if not (sPath + "/").startswith(sRoot.rstrip("/") + "/") or sPath == sRoot:
        raise ValueError(f"{sFilePath!r} is not below {sRoot!r}")
    iResolvedMode = I_DEFAULT_FILE_MODE if iMode is None else iMode
    if not isinstance(iResolvedMode, int) or not 0 <= iResolvedMode <= 0o7777:
        raise ValueError("the file mode must be an integer permission mask")
    _fnRequireStringTuple(tForbiddenNames)
    if not isinstance(bReplaceAllowed, bool):
        raise TypeError("bReplaceAllowed must be a bool")
    if not isinstance(bCreateParents, bool):
        raise TypeError("bCreateParents must be a bool")
    _fnRequireExpectedByteCount(iExpectedBytes)
    return (
        _S_CONFINED_WRITE_PROGRAM
        .replace(_S_ROOT_SLOT, repr(sRoot))
        .replace(_S_PATH_SLOT, repr(sPath))
        .replace(_S_MODE_SLOT, repr(iResolvedMode))
        .replace(_S_FORBIDDEN_SLOT, repr(list(tForbiddenNames)))
        .replace(_S_REPLACE_SLOT, repr(bReplaceAllowed))
        .replace(_S_EXPECTED_BYTES_SLOT, repr(iExpectedBytes))
        .replace(_S_PARENTS_SLOT, repr(bCreateParents))
        .replace("@@REFUSED_EXIT_CODE@@", repr(I_REFUSED_EXIT_CODE))
        .replace("@@EXISTS_EXIT_CODE@@", repr(I_EXISTS_EXIT_CODE))
        .replace("@@NOT_FOUND_EXIT_CODE@@", repr(I_NOT_FOUND_EXIT_CODE))
        .replace("@@NO_SPACE_EXIT_CODE@@", repr(I_NO_SPACE_EXIT_CODE))
    )


def fsRenderConfinedTreeProgram(
    sDestination, sAuthorizedRoot=None, tForbiddenNames=(),
    bCreateDestination=False,
):
    """Return the program text that lands a tar stream under ``sDestination``.

    ``sDestination`` may equal ``sAuthorizedRoot`` (a whole project is
    seeded into its own root) but never lies outside it. The program
    walks from ``/`` with ``O_NOFOLLOW``; ``bCreateDestination`` lets it
    create the destination components below the root, which is what a
    seed into a not-yet-existing project directory needs. Every member
    name is checked against ``tForbiddenNames`` by the program itself.
    """
    sRoot = _fsCheckedAbsolutePath(sAuthorizedRoot or "/", "the root")
    sPath = _fsCheckedAbsolutePath(sDestination, "the destination")
    if not (sPath + "/").startswith(sRoot.rstrip("/") + "/"):
        raise ValueError(f"{sDestination!r} is not below {sRoot!r}")
    if not isinstance(bCreateDestination, bool):
        raise TypeError("bCreateDestination must be a bool")
    _fnRequireStringTuple(tForbiddenNames)
    return (
        _S_CONFINED_TREE_PROGRAM
        .replace(_S_ROOT_SLOT, repr(sRoot))
        .replace(_S_DESTINATION_SLOT, repr(sPath))
        .replace(_S_CREATE_SLOT, repr(bCreateDestination))
        .replace(_S_FORBIDDEN_SLOT, repr(list(tForbiddenNames)))
        .replace("@@REFUSED_EXIT_CODE@@", repr(I_REFUSED_EXIT_CODE))
        .replace("@@FAILED_EXIT_CODE@@", repr(I_FAILED_EXIT_CODE))
        .replace("@@LANDED_PREFIX@@", S_LANDED_LINE_PREFIX)
    )


def fnRaiseWhenWriteFailed(tExecResult, sFilePath):
    """Raise the right error for a failed confined write; else return.

    A full disk raises ``OSError`` with ``errno.ENOSPC``, the same error
    the host leg raises natively, so a route tests one thing for both.
    """
    if tExecResult.iExitCode == 0:
        return
    sReason = (tExecResult.sStderr or "").strip().splitlines()
    sLastLine = sReason[-1] if sReason else "no error text"
    if tExecResult.iExitCode == I_EXISTS_EXIT_CODE:
        raise ContainerWriteExistsError(
            f"Write to {sFilePath} refused: {sLastLine}"
        )
    if tExecResult.iExitCode == I_REFUSED_EXIT_CODE:
        raise ContainerWriteRefusedError(
            f"Write to {sFilePath} refused: {sLastLine}"
        )
    if tExecResult.iExitCode == I_NOT_FOUND_EXIT_CODE:
        raise FileNotFoundError(
            errno.ENOENT, f"Cannot write {sFilePath}: {sLastLine}",
        )
    if tExecResult.iExitCode == I_NO_SPACE_EXIT_CODE:
        raise OSError(
            errno.ENOSPC,
            f"Cannot write {sFilePath} in the container: {sLastLine}",
        )
    raise OSError(f"Cannot write {sFilePath} in the container: {sLastLine}")


def fnRaiseWhenTreeWriteFailed(tExecResult, sDestination):
    """Raise the right error for a failed tree write; else return.

    A refusal (exit 3) and an I/O failure are different errors, and both
    carry ``iMembersLanded`` -- how many archive members had already
    landed when the program stopped, or ``None`` when it never got far
    enough to say. Zero means the destination was left as it was; a
    positive count means a partial copy, which the caller must treat as
    a half-finished write rather than a clean refusal.
    """
    if tExecResult.iExitCode == 0:
        return
    listLines = (tExecResult.sStderr or "").strip().splitlines()
    iMembersLanded = _fiPopLandedCount(listLines)
    sReason = listLines[-1] if listLines else "no error text"
    sOutcome = {
        None: "",
        0: " Nothing was written.",
    }.get(iMembersLanded, f" {iMembersLanded} entries had already landed.")
    if tExecResult.iExitCode == I_REFUSED_EXIT_CODE:
        errorRaised = ContainerWriteRefusedError(
            f"Copy into {sDestination} refused: {sReason}.{sOutcome}"
        )
    else:
        errorRaised = OSError(
            f"Cannot copy into {sDestination} in the container: "
            f"{sReason}.{sOutcome}"
        )
    errorRaised.iMembersLanded = iMembersLanded
    raise errorRaised


def _fiPopLandedCount(listLines):
    """Remove and return the program's final ``landed`` line, or None."""
    if not listLines or not listLines[-1].startswith(S_LANDED_LINE_PREFIX):
        return None
    sCount = listLines.pop()[len(S_LANDED_LINE_PREFIX):]
    return int(sCount) if sCount.isdigit() else None
