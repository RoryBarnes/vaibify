"""The host leg of the confined write: a researcher's copy lands where it says.

The container's writes are fixed programs (:mod:`vaibify.docker.confinedWrite`)
that walk a path one component at a time against directory descriptors
and never follow a link. A file pulled OUT of a container is written by
this process onto the researcher's machine, and the same contract holds
here: a read-write bind mount can place agent-written directories and
links under a destination the researcher picked, so the final name is
refused when it is a link or a directory, every parent is opened with
``O_NOFOLLOW`` relative to the one above it, and the bytes land under a
private name that is renamed into place only after they are all on
disk. A ``validate-then-open`` of the string path, the shape this
replaced, left a parent-directory race: a parent swapped for a link
between the check and the open redirected the write anywhere.

The two legs are held to one another by
``tests/testConfinedWriteParity.py``, as the readers are by
``tests/testConfinedReadParity.py``. Host-only module: ``os.path``.
"""

import errno
import os
import secrets
import stat

from vaibify.docker.confinedRead import ContainerReadRefusedError
from vaibify.docker.confinedWrite import (
    ContainerWriteRefusedError,
    I_DEFAULT_FILE_MODE,
)
from vaibify.host.hostConfinedRead import (
    fbBelowRoot,
    fiOpenParentDirectoryBelowRoot,
    flistSplitPath,
    fnRequireDirectoryRelativeAccess,
)

__all__ = ["fsWriteFileInsideRoot"]

_S_TEMPORARY_PREFIX = ".vaibify-pull-"


def _fnRefuseWhenFinalIsUnwritable(iDirectory, sName):
    """Refuse a final name that exists as a link or a directory.

    Read without following, against the directory descriptor the walk
    holds, so the object judged is the object the rename will replace.
    A regular file is replaced; a researcher copying over an earlier
    pull expects that.
    """
    try:
        iMode = os.stat(sName, dir_fd=iDirectory, follow_symlinks=False).st_mode
    except FileNotFoundError:
        return
    if stat.S_ISLNK(iMode) or stat.S_ISDIR(iMode):
        raise ContainerWriteRefusedError(
            f"refused: '{sName}' is a symlink or a directory"
        )


def _fnCopyChunksInto(iFile, iterChunks):
    """Write every chunk to the open descriptor and flush it to disk."""
    with os.fdopen(iFile, "wb", closefd=False) as fileTemporary:
        for baChunk in iterChunks:
            fileTemporary.write(baChunk)
        fileTemporary.flush()
        os.fsync(fileTemporary.fileno())


def _fnLandUnderPrivateName(iDirectory, sName, iterChunks, iMode):
    """Create, fill and rename the file relative to one held directory."""
    sTemporary = _S_TEMPORARY_PREFIX + secrets.token_hex(8)
    iFile = os.open(
        sTemporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
        0o600, dir_fd=iDirectory,
    )
    try:
        os.fchmod(iFile, iMode)
        _fnCopyChunksInto(iFile, iterChunks)
        os.close(iFile)
        _fnRefuseWhenFinalIsUnwritable(iDirectory, sName)
        os.rename(
            sTemporary, sName, src_dir_fd=iDirectory, dst_dir_fd=iDirectory,
        )
    except BaseException:
        try:
            os.unlink(sTemporary, dir_fd=iDirectory)
        except OSError:
            pass
        raise


def fsWriteFileInsideRoot(
    sRealRoot, sFilePath, iterChunks, iMode=I_DEFAULT_FILE_MODE,
):
    """Write ``iterChunks`` to ``sFilePath`` below ``sRealRoot``; return the path.

    ``sRealRoot`` is the directory the researcher chose, already a real
    path, resolved once. Nothing below it is resolved by string again:
    the parents are opened descriptor by descriptor without following a
    link, and the final name is refused when it is a link or a
    directory. Refusals raise :class:`ContainerWriteRefusedError`, the
    same class the container leg raises, and nothing is left behind.
    It fails closed on a Python without directory-relative access.
    """
    fnRequireDirectoryRelativeAccess()
    listRoot = flistSplitPath(sRealRoot)
    listParts = flistSplitPath(sFilePath)
    if not fbBelowRoot(listRoot, listParts, False):
        raise ContainerWriteRefusedError(
            "refused: the destination is not below its authorized root"
        )
    listBelowRoot = listParts[len(listRoot):]
    try:
        iDirectory = fiOpenParentDirectoryBelowRoot(sRealRoot, listBelowRoot)
    except ContainerReadRefusedError as error:
        # The shared walk speaks in the reader's class; a write refusal
        # is what the caller handles.
        raise ContainerWriteRefusedError(str(error)) from error
    try:
        _fnRefuseWhenFinalIsUnwritable(iDirectory, listBelowRoot[-1])
        _fnLandUnderPrivateName(
            iDirectory, listBelowRoot[-1], iterChunks, iMode,
        )
    except OSError as error:
        if error.errno in (errno.ELOOP, errno.ENOTDIR):
            raise ContainerWriteRefusedError(
                "refused: a component of the destination is a symlink or "
                "not a directory"
            ) from error
        raise
    finally:
        os.close(iDirectory)
    return os.sep + os.sep.join(listParts)
