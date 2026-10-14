"""Write a file so that it, and its rename into place, survive a power loss.

``os.replace`` over a temporary file protects READERS: nobody ever sees
a half-written file. It protects nothing against a power cut, because
the bytes and the directory entry may both still be in the operating
system's caches when the lights go out. Surviving that takes three
more steps, in order: force the temporary file's bytes to the disk,
rename it, then force the DIRECTORY so the rename itself is recorded.
On macOS ``fsync`` only hands the bytes to the drive, which is free to
hold them in its own cache; ``F_FULLFSYNC`` is the call that flushes
that too.

Every record vaibify must be able to trust after a crash goes through
here: the download sidecar that says how many bytes of a partial file
are real, the registry, and the conversion journal. The cost is
milliseconds per write.
"""

__all__ = [
    "fnSyncDescriptor",
    "fnSyncDirectory",
    "fnWriteFileDurably",
]

import fcntl
import logging
import os
import tempfile


logger = logging.getLogger("vaibify")


def fnSyncDescriptor(iDescriptor):
    """Force one open file's bytes to the disk itself, not only to the OS.

    ``F_FULLFSYNC`` exists on macOS alone, and a filesystem may refuse
    it (a network mount, for one); ``fsync`` is the fallback everywhere.
    """
    if hasattr(fcntl, "F_FULLFSYNC"):
        try:
            fcntl.fcntl(iDescriptor, fcntl.F_FULLFSYNC)
            return
        except OSError:
            pass
    os.fsync(iDescriptor)


def fnSyncDirectory(sDirectory):
    """Force a directory's entries to disk; a failure here is logged, not raised.

    A directory that cannot be synced still holds the file: the rename
    happened, and refusing the write for the sake of the sync would
    throw away a record that is complete.
    """
    try:
        iDescriptor = os.open(sDirectory, os.O_RDONLY)
    except OSError as error:
        logger.debug("Could not open %s to fsync: %s", sDirectory, error)
        return
    try:
        os.fsync(iDescriptor)
    except OSError as error:
        logger.debug("Could not fsync %s: %s", sDirectory, error)
    finally:
        os.close(iDescriptor)


def fnWriteFileDurably(sPath, baContent, iMode=0o600):
    """Replace ``sPath`` with ``baContent`` so the result survives a power loss.

    Temporary file beside the target (a rename across filesystems is a
    copy, not an atomic replacement), bytes synced, mode set, renamed
    into place, directory synced. A failure anywhere leaves the target
    untouched and removes the temporary file.
    """
    sDirectory = os.path.dirname(os.path.abspath(sPath))
    iDescriptor, sTemporaryPath = tempfile.mkstemp(
        dir=sDirectory, prefix=".", suffix=".tmp",
    )
    bClosed = False
    try:
        os.write(iDescriptor, baContent)
        fnSyncDescriptor(iDescriptor)
        os.close(iDescriptor)
        bClosed = True
        os.chmod(sTemporaryPath, iMode)
        os.replace(sTemporaryPath, sPath)
    except BaseException:
        if not bClosed:
            os.close(iDescriptor)
        try:
            os.unlink(sTemporaryPath)
        except OSError:
            pass
        raise
    fnSyncDirectory(sDirectory)
