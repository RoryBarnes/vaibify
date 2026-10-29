"""Docker volume management using subprocess CLI calls."""

import subprocess

from . import fnRunDockerCommand


def fnCreateVolume(sVolumeName):
    """Create a named Docker volume if it does not already exist.

    Parameters
    ----------
    sVolumeName : str
        Name of the Docker volume to create.
    """
    if fbVolumeExists(sVolumeName):
        return
    saCommand = ["docker", "volume", "create", sVolumeName]
    _fnRunDockerCommand(saCommand, bCaptureOutput=True)


def fnDestroyVolume(sVolumeName):
    """Remove a named Docker volume.

    Parameters
    ----------
    sVolumeName : str
        Name of the Docker volume to remove.
    """
    saCommand = ["docker", "volume", "rm", sVolumeName]
    _fnRunDockerCommand(saCommand, bCaptureOutput=True)


def fbVolumeExists(sVolumeName):
    """Check whether a named Docker volume exists.

    Parameters
    ----------
    sVolumeName : str
        Name of the Docker volume to check.

    Returns
    -------
    bool
        True if the volume exists.
    """
    processResult = subprocess.run(
        ["docker", "volume", "inspect", sVolumeName],
        capture_output=True,
    )
    return processResult.returncode == 0


def fsWorkspaceVolumeNameForProject(sProjectName):
    """Return the workspace volume name derived from a project NAME.

    The naming rule lives here, in one place, because deletion has to
    name the same volumes a launch created without holding a validated
    config: an environment whose ``vaibify.yml`` was deleted still owns
    the volumes its runs filled, and refusing to delete it because the
    config is gone would strand exactly the environment a researcher
    most wants removed.
    """
    return f"{sProjectName}-workspace"


def fsCredentialsVolumeNameForProject(sProjectName):
    """Return the credentials volume name derived from a project NAME."""
    return f"{sProjectName}-credentials"


def fsGetVolumeName(config):
    """Return the workspace volume name for a project.

    Parameters
    ----------
    config : ProjectConfig
        Validated project configuration.

    Returns
    -------
    str
        Volume name in the form '{projectName}-workspace'.
    """
    return fsWorkspaceVolumeNameForProject(config.sProjectName)


def fsGetCredentialsVolumeName(config):
    """Return the credentials volume name for a project.

    The credentials volume persisted the container user's keyring
    data directory across container recreations (``docker rm``
    followed by ``docker run`` or a GUI Rebuild) while Zenodo tokens
    lived in the container. Since 2026-10-29 every token lives on the
    host, so no volume is created for a new container; an existing
    one is still mounted until the migration out of it has finished
    (see ``containerManager._fnAddCredentialsVolume``).

    Parameters
    ----------
    config : ProjectConfig
        Validated project configuration.

    Returns
    -------
    str
        Volume name in the form '{projectName}-credentials'.
    """
    return fsCredentialsVolumeNameForProject(config.sProjectName)


_fnRunDockerCommand = fnRunDockerCommand
