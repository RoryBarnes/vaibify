"""Prevent macOS sleep while specific containers are running.

This is the canonical home for the keep-alive (``caffeinate``) PID
registry; ``vaibify/docker/keepAliveManager.py`` remains as a thin
re-export shim. Like ``containerLock`` and ``sessionRegistry`` it is a
schema-divergent view of the shared ``pidFileRegistry`` mechanism: its
directory creation and pid-file IO route through that module, so the
caffeinate directory is created at ``0o700`` and its file is opened
``O_NOFOLLOW`` exactly like every other host registry. The recycle-proof
kill (start-clock gated ``SIGTERM``) and the legacy bare-int payload
support stay here because they are this registry's own divergent schema.
"""

import datetime
import os
import signal
import subprocess
import sys
import json

from vaibify.config import pidFileRegistry
from vaibify.config.processLiveness import fbIsProcessAliveSince


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
    pidFileRegistry.fnEnsureDirectory(_S_PID_DIRECTORY)
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
    """Record the caffeinate pid and its claim time for a container."""
    sPath = _fsPidFilePath(sContainerName)
    dictPayload = {
        "iPid": iPid,
        "sStartedIso": datetime.datetime.now().isoformat(),
    }
    with pidFileRegistry.ffileOpenNoFollow(sPath) as fileHandle:
        pidFileRegistry.fnWritePayload(fileHandle, dictPayload)


def fnStopKeepAlive(sContainerName):
    """Kill the caffeinate process associated with a container."""
    sPath = _fsPidFilePath(sContainerName)
    if not os.path.isfile(sPath):
        return
    dictPayload = _fdictReadPidPayload(sPath)
    iPid = dictPayload.get("iPid", 0)
    if iPid:
        _fnKillIfRunning(iPid, dictPayload.get("sStartedIso"))
    _fnRemovePidFile(sPath)


def _fdictReadPidPayload(sPath):
    """Read the caffeinate pid payload, tolerating a legacy bare int.

    New pid files hold JSON with the pid and its claim time; older
    files held a single integer. Both map to a payload dict so the
    recycled-PID guard applies uniformly. Returns {} on any error.
    """
    try:
        with open(sPath, "r") as fileHandle:
            sContent = fileHandle.read().strip()
    except OSError:
        return {}
    return _fdictParsePidContent(sContent)


def _fdictParsePidContent(sContent):
    """Map pid-file text (JSON or a legacy bare int) to a payload dict."""
    if not sContent:
        return {}
    try:
        objParsed = json.loads(sContent)
    except json.JSONDecodeError:
        return {}
    if isinstance(objParsed, dict):
        return objParsed
    if isinstance(objParsed, int) and not isinstance(objParsed, bool):
        return {"iPid": objParsed}
    return {}


def _fnKillIfRunning(iPid, sStartedIso):
    """SIGTERM a process only when its start time matches the claim.

    The start-time gate keeps a recycled PID — one the kernel reused
    after caffeinate exited — from being killed. A legacy payload with
    no claim time falls back to the bare PID-existence check.
    """
    if not fbIsProcessAliveSince(iPid, sStartedIso):
        return
    try:
        os.kill(iPid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    except PermissionError:
        pass


def _fnRemovePidFile(sPath):
    """Remove a PID file, ignoring errors."""
    pidFileRegistry.fnUnlinkQuietly(sPath)


def _fsPidFilePath(sContainerName):
    """Return the PID file path for a container."""
    return os.path.join(_S_PID_DIRECTORY, f"{sContainerName}.pid")
