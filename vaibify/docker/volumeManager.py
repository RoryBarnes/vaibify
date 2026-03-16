"""Docker volume management using subprocess CLI calls."""

import subprocess
import sys


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


def _fnRunDockerCommand(saCommand):
    """Execute a docker command, raising on failure."""
    resultProcess = subprocess.run(
        saCommand,
        stdout=sys.stdout,
        stderr=sys.stderr,
    )
    if resultProcess.returncode != 0:
        sCommandStr = " ".join(saCommand)
        raise RuntimeError(
            f"Docker command failed (exit {resultProcess.returncode}): "
            f"{sCommandStr}"
        )
