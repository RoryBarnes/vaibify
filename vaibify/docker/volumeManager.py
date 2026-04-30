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
    _fnRunDockerCommand(saCommand)


def fnDestroyVolume(sVolumeName):
    """Remove a named Docker volume.

    Parameters
    ----------
    sVolumeName : str
        Name of the Docker volume to remove.
    """
    saCommand = ["docker", "volume", "rm", sVolumeName]
    _fnRunDockerCommand(saCommand)


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
    resultProcess = subprocess.run(
        ["docker", "volume", "inspect", sVolumeName],
        capture_output=True,
    )
    return resultProcess.returncode == 0


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
    return f"{config.sProjectName}-workspace"


def fsGetCredentialsVolumeName(config):
    """Return the credentials volume name for a project.

    The credentials volume persists the container user's keyring
    data directory across container recreations (``docker rm``
    followed by ``docker run`` or a GUI Rebuild) so that stored
    Zenodo, GitHub, and any other in-container-keyring tokens do
    not have to be re-entered. Host-keyring tokens (Overleaf) do
    not need this volume; they already persist host-side.

    Parameters
    ----------
    config : ProjectConfig
        Validated project configuration.

    Returns
    -------
    str
        Volume name in the form '{projectName}-credentials'.
    """
    return f"{config.sProjectName}-credentials"


_fnRunDockerCommand = fnRunDockerCommand
