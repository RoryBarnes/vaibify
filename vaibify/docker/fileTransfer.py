"""File transfer between host and container.

A PULL is ``docker cp``: bytes leaving a container land on the host
owned by whoever ran the command, which is right.

A PUSH is NOT. ``docker cp`` writes the destination owned by root, and
the container user is unprivileged with no sudo by design, so every
file this command deposited was one the in-container agent -- and the
researcher's own shell -- could not modify. The backend never had this
defect because its writes go through the gateway's confined writers in
``dockerConnection``, which run as the container user, so that user owns
what they create. Push now goes the same way -- through the gateway,
which owns both writers and the destination probe they need -- and
``docker cp`` is deliberately no longer reachable from this direction.
"""

from pathlib import PurePosixPath

from . import fnRunDockerCommand


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
                        bRecursive=False):
    """Copy a file or directory from a running container to the host.

    Parameters
    ----------
    sProjectName : str
        Name of the running container.
    sContainerSource : str
        Absolute path inside the container to copy from.
    sHostDest : str
        Path on the host to copy to.
    bRecursive : bool
        Unused; docker cp handles directories automatically.
        Retained for API consistency.
    """
    sSource = f"{sProjectName}:{sContainerSource}"
    saCommand = ["docker", "cp", sSource, sHostDest]
    _fnRunDockerCp(saCommand)


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


_fnRunDockerCp = fnRunDockerCommand
