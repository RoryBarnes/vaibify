"""File transfer between host and container.

A PULL is ``docker cp``: bytes leaving a container land on the host
owned by whoever ran the command, which is right.

A PUSH is NOT. ``docker cp`` writes the destination owned by root, and
the container user is unprivileged with no sudo by design, so every
file this command deposited was one the in-container agent -- and the
researcher's own shell -- could not modify. The backend never had this
defect because its writes go through the tar writer in
``dockerConnection``, which stamps the container user onto every entry
rather than letting ``tarfile``'s native uid 0 through. Push now goes
the same way -- through the gateway, which owns both writers and the
destination probe they need -- and ``docker cp`` is deliberately no
longer reachable from this direction.
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
        Path relative to the workspace root.
    sWorkspaceRoot : str
        Absolute path of the workspace root inside the container.

    Returns
    -------
    str
        Absolute POSIX path inside the container.
    """
    pathWorkspace = PurePosixPath(sWorkspaceRoot)
    pathResolved = pathWorkspace / sRelativePath
    return str(pathResolved)


_fnRunDockerCp = fnRunDockerCommand
