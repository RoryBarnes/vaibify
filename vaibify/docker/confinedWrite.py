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

The tar-entry builder this replaced (``_finfoBuildTarEntry``) stamped a
uid and gid onto an archive entry so the daemon would not create the file
root-owned. Ownership needs no stamp any more: the file is created by the
container user, so that user owns it.

Like the typed reads, the program is module source text; a caller
supplies values that are embedded through ``repr`` as Python literals,
and only ``str`` and ``int`` values are admitted, so a path never becomes
program or shell syntax.
"""

import posixpath

__all__ = [
    "ContainerWriteRefusedError",
    "I_REFUSED_EXIT_CODE",
    "T_WRITE_DENYLISTED_NAMES",
    "fsRenderConfinedWriteProgram",
    "fnRaiseWhenWriteFailed",
]

I_REFUSED_EXIT_CODE = 3
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

_S_CONFINED_WRITE_PROGRAM = '''import errno, os, secrets, stat, sys
sAuthorizedRoot = @@AUTHORIZED_ROOT@@
sFilePath = @@FILE_PATH@@
iMode = @@FILE_MODE@@
listForbiddenNames = @@FORBIDDEN_NAMES@@
I_REFUSED = @@REFUSED_EXIT_CODE@@
def fnRefuse(sReason):
    sys.stderr.write(sReason + "\\n")
    sys.exit(I_REFUSED)
def flistSplit(sPath):
    return [sPart for sPart in sPath.split("/") if sPart]
def fiOpenDirectoryWithoutFollowing(iParent, sName):
    iFlags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    try:
        return os.open(sName, iFlags, dir_fd=iParent)
    except OSError as error:
        if error.errno in (errno.ELOOP, errno.ENOTDIR):
            fnRefuse("refused: '" + sName + "' is a symlink or not a directory")
        raise
listRoot = flistSplit(sAuthorizedRoot)
listParts = flistSplit(sFilePath)
if listParts[:len(listRoot)] != listRoot or len(listParts) <= len(listRoot):
    fnRefuse("refused: the path is not below its authorized root")
for sName in listParts[len(listRoot):]:
    if sName in listForbiddenNames:
        fnRefuse("refused: writes through '" + sName + "' are not permitted")
baContent = sys.stdin.buffer.read()
iDirectory = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
for sName in listParts[:-1]:
    iNext = fiOpenDirectoryWithoutFollowing(iDirectory, sName)
    os.close(iDirectory)
    iDirectory = iNext
sFinal = listParts[-1]
try:
    iExisting = os.stat(sFinal, dir_fd=iDirectory, follow_symlinks=False).st_mode
except FileNotFoundError:
    iExisting = None
if iExisting is not None and (stat.S_ISLNK(iExisting) or stat.S_ISDIR(iExisting)):
    fnRefuse("refused: '" + sFinal + "' is a symlink or a directory")
sTemporary = ".vaibify-write-" + secrets.token_hex(8)
iFile = os.open(
    sTemporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
    0o600, dir_fd=iDirectory,
)
try:
    os.fchmod(iFile, iMode)
    with os.fdopen(iFile, "wb") as fileTemporary:
        fileTemporary.write(baContent)
    os.rename(sTemporary, sFinal, src_dir_fd=iDirectory, dst_dir_fd=iDirectory)
except BaseException:
    try:
        os.unlink(sTemporary, dir_fd=iDirectory)
    except OSError:
        pass
    raise
'''


class ContainerWriteRefusedError(OSError):
    """The confined write declined a path; nothing was written.

    Distinct from an I/O failure so a route can answer 403 for a path
    the contract forbids and 500 only for a write that genuinely failed.
    It is not a carrier refusal and says nothing about admission.
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


def fsRenderConfinedWriteProgram(
    sFilePath, iMode=None, sAuthorizedRoot=None, tForbiddenNames=(),
):
    """Return the program text that writes ``sFilePath`` from stdin.

    ``sAuthorizedRoot`` defaults to the filesystem root, which still
    makes the walk symlink-free end to end. Every component below the
    root is checked against ``tForbiddenNames`` by the program itself,
    in addition to whatever lexical check the caller ran first.
    """
    sRoot = _fsCheckedAbsolutePath(sAuthorizedRoot or "/", "the root")
    sPath = _fsCheckedAbsolutePath(sFilePath, "the file path")
    if not (sPath + "/").startswith(sRoot.rstrip("/") + "/") or sPath == sRoot:
        raise ValueError(f"{sFilePath!r} is not below {sRoot!r}")
    iResolvedMode = I_DEFAULT_FILE_MODE if iMode is None else iMode
    if not isinstance(iResolvedMode, int) or not 0 <= iResolvedMode <= 0o7777:
        raise ValueError("the file mode must be an integer permission mask")
    _fnRequireStringTuple(tForbiddenNames)
    return (
        _S_CONFINED_WRITE_PROGRAM
        .replace(_S_ROOT_SLOT, repr(sRoot))
        .replace(_S_PATH_SLOT, repr(sPath))
        .replace(_S_MODE_SLOT, repr(iResolvedMode))
        .replace(_S_FORBIDDEN_SLOT, repr(list(tForbiddenNames)))
        .replace("@@REFUSED_EXIT_CODE@@", repr(I_REFUSED_EXIT_CODE))
    )


def fnRaiseWhenWriteFailed(tExecResult, sFilePath):
    """Raise the right error for a failed confined write; else return."""
    if tExecResult.iExitCode == 0:
        return
    sReason = (tExecResult.sStderr or "").strip().splitlines()
    sLastLine = sReason[-1] if sReason else "no error text"
    if tExecResult.iExitCode == I_REFUSED_EXIT_CODE:
        raise ContainerWriteRefusedError(
            f"Write to {sFilePath} refused: {sLastLine}"
        )
    raise OSError(f"Cannot write {sFilePath} in the container: {sLastLine}")
