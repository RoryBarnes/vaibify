"""File transfer between host and container using docker cp."""

from pathlib import PurePosixPath

from . import fnRunDockerCommand


def fnPushToContainer(sProjectName, sHostSource, sContainerDest,
                      bRecursive=False):
    """Copy a file or directory from the host into a running container.

    Parameters
    ----------
    sProjectName : str
        Name of the running container.
    sHostSource : str
        Path on the host to copy from.
    sContainerDest : str
        Absolute path inside the container to copy to.
    bRecursive : bool
        Unused; docker cp handles directories automatically.
        Retained for API consistency.
    """
    sTarget = f"{sProjectName}:{sContainerDest}"
    saCommand = ["docker", "cp", sHostSource, sTarget]
    _fnRunDockerCp(saCommand)


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
