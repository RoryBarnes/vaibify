"""X11 display forwarding for Docker containers."""

import os
import platform
import subprocess


def flistConfigureX11Args():
    """Return docker run args for X11 forwarding on the current platform.

    Detects macOS vs Linux and delegates to the appropriate setup.

    Returns
    -------
    list of str
        Docker run arguments enabling X11 display forwarding.
    """
    saRunArgs = []
    sPlatform = platform.system()
    if sPlatform == "Darwin":
        fnConfigureMacX11(saRunArgs)
    elif sPlatform == "Linux":
        fnConfigureLinuxX11(saRunArgs)
    return saRunArgs


def fnConfigureMacX11(saRunArgs):
    """Configure X11 forwarding for macOS via XQuartz or Colima.

    Starts XQuartz if needed, disables X11 auth, and sets DISPLAY
    to host.docker.internal:0.

    Parameters
    ----------
    saRunArgs : list of str
        Docker run argument list to extend in place.
    """
    fnStartXquartz()
    fnDisableX11Auth()
    saRunArgs.extend(["-e", "DISPLAY=host.docker.internal:0"])


def fnConfigureLinuxX11(saRunArgs):
    """Configure X11 forwarding for Linux with safer xhost policy.

    Uses xhost +SI:localuser:$USER instead of the overly permissive
    xhost +local:docker, then mounts the X11 socket and passes DISPLAY.

    Parameters
    ----------
    saRunArgs : list of str
        Docker run argument list to extend in place.
    """
    _fnGrantLocalUserXhostAccess()
    sDisplay = os.environ.get("DISPLAY", ":0")
    saRunArgs.extend(["-e", f"DISPLAY={sDisplay}"])
    saRunArgs.extend(["-v", "/tmp/.X11-unix:/tmp/.X11-unix:ro"])


def _fnRunBestEffort(saArgs):
    """Run a subprocess, ignoring missing binaries and non-zero exits.

    X11 setup is optional: a host without xhost, XQuartz, or pgrep
    should not crash container start.
    """
    try:
        subprocess.run(saArgs, capture_output=True)
    except FileNotFoundError:
        return


def _fnGrantLocalUserXhostAccess():
    """Grant the current local user X11 access via xhost."""
    sUser = os.environ.get("USER", "")
    if not sUser:
        return
    _fnRunBestEffort(["xhost", "+SI:localuser:" + sUser])


def fnStartXquartz():
    """Launch XQuartz on macOS if it is not already running.

    Silently does nothing if XQuartz cannot be started.
    """
    if _fbProcessIsRunning("Xquartz"):
        return
    _fnRunBestEffort(["open", "-a", "XQuartz"])


def _fbProcessIsRunning(sProcessName):
    """Check whether a process with the given name is running."""
    try:
        resultProcess = subprocess.run(
            ["pgrep", "-x", sProcessName],
            capture_output=True,
        )
    except FileNotFoundError:
        return False
    return resultProcess.returncode == 0


def fnDisableX11Auth():
    """Allow Docker X11 connections on macOS by running xhost +localhost."""
    _fnRunBestEffort(["xhost", "+localhost"])
