"""Prevent macOS sleep while specific containers are running."""

import os
import signal
import subprocess
import sys


_S_PID_DIRECTORY = os.path.expanduser("~/.vaibify/caffeinate")


def fnStartKeepAlive(sContainerName):
    """Spawn a caffeinate process tied to the given container.

    Parameters
    ----------
    sContainerName : str
        The Docker container name used to locate the PID file.
    """
    if sys.platform != "darwin":
        return
    fnStopKeepAlive(sContainerName)
    os.makedirs(_S_PID_DIRECTORY, exist_ok=True)
    iPid = _fiSpawnCaffeinate()
    if iPid:
        _fnWritePidFile(sContainerName, iPid)


def _fiSpawnCaffeinate():
    """Launch 'caffeinate -s' in the background and return its pid."""
    try:
        resultProcess = subprocess.Popen(
            ["caffeinate", "-s"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        return resultProcess.pid
    except FileNotFoundError:
        return 0


def _fnWritePidFile(sContainerName, iPid):
    """Record the caffeinate pid for a container."""
    sPath = _fsPidFilePath(sContainerName)
    with open(sPath, "w") as fileHandle:
        fileHandle.write(f"{iPid}\n")


def fnStopKeepAlive(sContainerName):
    """Kill the caffeinate process associated with a container."""
    sPath = _fsPidFilePath(sContainerName)
    if not os.path.isfile(sPath):
        return
    iPid = _fiReadPid(sPath)
    if iPid:
        _fnKillIfRunning(iPid)
    _fnRemovePidFile(sPath)


def _fiReadPid(sPath):
    """Read an integer pid from the PID file."""
    try:
        with open(sPath, "r") as fileHandle:
            return int(fileHandle.read().strip())
    except (OSError, ValueError):
        return 0


def _fnKillIfRunning(iPid):
    """Send SIGTERM to a process, ignoring missing or wrong-user pids."""
    try:
        os.kill(iPid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    except PermissionError:
        pass


def _fnRemovePidFile(sPath):
    """Remove a PID file, ignoring errors."""
    try:
        os.unlink(sPath)
    except OSError:
        pass


def _fsPidFilePath(sContainerName):
    """Return the PID file path for a container."""
    return os.path.join(_S_PID_DIRECTORY, f"{sContainerName}.pid")
