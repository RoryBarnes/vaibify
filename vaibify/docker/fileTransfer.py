"""File transfer between host and container.

A PULL reads through the confined read programs, not ``docker cp``:
bytes leaving a container land on the host owned by whoever ran the
command, which is right, and the read cannot be redirected by a symlink
the in-container agent planted.

A PUSH is not ``docker cp`` either, for a different reason. ``docker cp`` writes the destination owned by root, and
the container user is unprivileged with no sudo by design, so every
file this command deposited was one the in-container agent -- and the
researcher's own shell -- could not modify. The backend never had this
defect because its writes go through the gateway's confined writers in
``dockerConnection``, which run as the container user, so that user owns
what they create. Push now goes the same way -- through the gateway,
which owns both writers and the destination probe they need -- and
``docker cp`` is deliberately no longer reachable from this direction.
"""

import os
import tempfile
from pathlib import PurePosixPath


def fnPushToContainer(sProjectName, sHostSource, sContainerDest,
                      bRecursive=False):
    """Copy a file or directory from the host into a running container.

    Ownership is the reason this is not ``docker cp``; see the module
    docstring. Destination semantics are preserved: a destination that
    names an existing directory receives the source under its own
    basename, and any other destination is the full path to write --
    the same two readings ``docker cp`` gives them.

    Parameters
    ----------
    sProjectName : str
        Name of the running container.
    sHostSource : str
        Path on the host to copy from.
    sContainerDest : str
        Absolute path inside the container to copy to.
    bRecursive : bool
        Unused; a directory source is archived whole.
        Retained for API consistency.
    """
    from vaibify.docker.dockerConnection import DockerConnection
    DockerConnection().fnCopyHostPathIntoContainer(
        sProjectName, sHostSource, sContainerDest,
    )


def fnPullFromContainer(sProjectName, sContainerSource, sHostDest,
                        bRecursive=False, sAuthorizedRoot="/workspace",
                        connectionDocker=None):
    """Copy a file or directory from a running container to the host.

    Reads through the confined read programs
    (:mod:`vaibify.docker.confinedRead`), never ``docker cp``: ``docker cp``
    reads as root through the daemon, so a component the in-container
    agent swapped for a symlink after the path was named redirected the
    read, and a symlinked file landed on the host as a dangling link.
    The confined read follows a link only when its target stays inside
    ``sAuthorizedRoot`` (the project's workspace) and refuses one that
    leaves it, naming it.

    ``docker cp``'s destination reading is kept: a file whose destination
    names an existing directory lands under its own name, otherwise the
    destination IS the file; a folder lands inside an existing destination
    folder under its own name, otherwise it is created AT the destination
    under the destination's name. The destination's parent must exist.

    Parameters
    ----------
    sProjectName : str
        Name of the running container.
    sContainerSource : str
        Absolute path inside the container to copy from.
    sHostDest : str
        Path on the host to copy to.
    bRecursive : bool
        Unused; a folder is recognized by what it is.
        Retained for API consistency.
    sAuthorizedRoot : str
        The container path no followed link may leave.
    connectionDocker : object, optional
        The gateway to read through; a real one is made when omitted.
    """
    del bRecursive
    if connectionDocker is None:
        from vaibify.docker.dockerConnection import DockerConnection
        connectionDocker = DockerConnection()
    if connectionDocker.fbContainerPathIsDirectory(
        sProjectName, sContainerSource,
    ):
        _fnPullFolder(
            connectionDocker, sProjectName, sContainerSource, sHostDest,
            sAuthorizedRoot)
    else:
        _fnPullFile(
            connectionDocker, sProjectName, sContainerSource, sHostDest,
            sAuthorizedRoot)


def _fsRequireParentDirectory(sHostPath):
    sParent = os.path.dirname(os.path.abspath(sHostPath))
    if not os.path.isdir(sParent):
        raise OSError(
            f"{sParent} does not exist. Create it first; this command "
            "copies, it does not build the folders above its destination.")
    return sParent


def _fnPullFile(
    connectionDocker, sProjectName, sContainerSource, sHostDest,
    sAuthorizedRoot,
):
    """Stream one file to a private name beside its destination, then rename."""
    sTarget = sHostDest
    if os.path.isdir(sTarget):
        sTarget = os.path.join(
            sTarget, PurePosixPath(sContainerSource).name)
    sParent = _fsRequireParentDirectory(sTarget)
    iterChunks = connectionDocker.fiterReadFileConfined(
        sProjectName, sContainerSource, sAuthorizedRoot=sAuthorizedRoot)
    iDescriptor, sTemporary = tempfile.mkstemp(
        dir=sParent, prefix=".vaibify-pull-")
    try:
        with os.fdopen(iDescriptor, "wb") as fileOut:
            for baChunk in iterChunks:
                fileOut.write(baChunk)
            fileOut.flush()
            os.fsync(fileOut.fileno())
        os.chmod(sTemporary, _fiDefaultFileMode())
        os.replace(sTemporary, sTarget)
    except BaseException:
        try:
            os.unlink(sTemporary)
        except OSError:
            pass
        raise


def _fiDefaultFileMode():
    """Return 0666 under the researcher's umask, as a new file would get."""
    iUmask = os.umask(0)
    os.umask(iUmask)
    return 0o666 & ~iUmask


def _fnPullFolder(
    connectionDocker, sProjectName, sContainerSource, sHostDest,
    sAuthorizedRoot,
):
    """Extract the folder's archive as ``docker cp`` would have landed it."""
    from vaibify.host.archiveExtraction import fiExtractTarStream
    bIntoExisting = os.path.isdir(sHostDest)
    if bIntoExisting:
        sDestinationDirectory, sRename = sHostDest, None
    else:
        sDestinationDirectory = _fsRequireParentDirectory(sHostDest)
        sRename = os.path.basename(os.path.abspath(sHostDest))
    iterChunks = connectionDocker.fiterReadDirectoryAsTar(
        sProjectName, sContainerSource, sAuthorizedRoot=sAuthorizedRoot)
    fiExtractTarStream(iterChunks, sDestinationDirectory, sRename)


def fsResolveContainerPath(sRelativePath, sWorkspaceRoot):
    """Map a user-provided relative path to a workspace-absolute path.

    Parameters
    ----------
    sRelativePath : str
        Path relative to the workspace root. An absolute path is
        returned unchanged. A trailing slash is kept, so that resolving
        a path never edits the text the researcher typed beyond making
        it absolute.
    sWorkspaceRoot : str
        Absolute path of the workspace root inside the container.

    Returns
    -------
    str
        Absolute POSIX path inside the container.
    """
    pathWorkspace = PurePosixPath(sWorkspaceRoot)
    sResolved = str(pathWorkspace / sRelativePath)
    if sRelativePath.endswith("/") and not sResolved.endswith("/"):
        return sResolved + "/"
    return sResolved

