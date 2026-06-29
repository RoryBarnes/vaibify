"""Docker availability detection and runtime diagnosis.

Owns the process-level Docker connection probe and the cached
availability diagnosis surfaced by the system-status routes and the
503 path. The connection creator is resolved through ``pipelineServer``
so a patched test double on that module is honoured by both the app
factory and the retry route.
"""

import logging
import os

from fastapi import HTTPException

from ..docker.dockerErrorDiagnosis import fdictDiagnoseDockerError

logger = logging.getLogger("vaibify")

__all__ = [
    "fdictGetDockerStatus",
    "fdictRetryDockerConnection",
    "fsDetectDockerRuntime",
]


def _fbCaffeinateRunning():
    """Return True if a caffeinate process is active for this user."""
    import subprocess
    try:
        resultProcess = subprocess.run(
            ["pgrep", "-u", str(os.getuid()), "-x", "caffeinate"],
            capture_output=True, timeout=2,
        )
        return resultProcess.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


def _fdictSleepWarningForContext(sContext):
    """Return runtime info dict with appropriate sleep warning."""
    from . import pipelineServer
    if pipelineServer._fbCaffeinateRunning():
        return {"sRuntime": sContext, "sSleepWarning": ""}
    sSleepDefault = (
        "Use 'caffeinate -s' to prevent macOS from "
        "sleeping during long pipeline runs."
    )
    if "colima" in sContext:
        return {"sRuntime": "colima", "sSleepWarning":
            "Your Docker runtime (Colima) does not "
            "sleep automatically. " + sSleepDefault}
    if "desktop" in sContext or "default" == sContext:
        return {"sRuntime": "desktop", "sSleepWarning":
            "Ensure Docker Desktop is configured to "
            "not sleep idle VMs (Settings > Resources "
            "> Advanced). Also consider running "
            "'caffeinate -s' to prevent macOS sleep."}
    if "orbstack" in sContext:
        return {"sRuntime": "orbstack", "sSleepWarning":
            "OrbStack VMs survive sleep. " + sSleepDefault}
    return {"sRuntime": sContext, "sSleepWarning": sSleepDefault}


def fsDetectDockerRuntime():
    """Detect the Docker runtime (colima, desktop, orbstack, etc.)."""
    import subprocess
    try:
        resultContext = subprocess.run(
            ["docker", "context", "ls", "--format",
             "{{.Name}}:{{.Current}}"],
            capture_output=True, text=True, timeout=5,
        )
        for sLine in resultContext.stdout.strip().split("\n"):
            if ":true" in sLine.lower():
                sContext = sLine.split(":")[0].strip().lower()
                return _fdictSleepWarningForContext(sContext)
    except Exception:
        pass
    return {"sRuntime": "unknown", "sSleepWarning":
        "Use 'caffeinate -s' to prevent your computer from "
        "sleeping during long pipeline runs."}


def _fnRequireDocker(connectionDocker):
    """Raise 503 if Docker is unavailable, with a specific diagnosis."""
    if connectionDocker is not None:
        return
    sDetail = _fsBuildDockerUnavailableDetail()
    raise HTTPException(503, sDetail)


def _fsBuildDockerUnavailableDetail():
    """Compose the 503 detail string from the cached diagnosis."""
    sError = _dictDockerStatus.get("sError", "")
    sHint = _dictDockerStatus.get("sHint", "")
    sCommand = _dictDockerStatus.get("sCommand", "")
    sDetail = "Docker support is not available."
    if sHint:
        sDetail += " " + sHint
    if sCommand:
        sDetail += " Try: " + sCommand
    if sError:
        sDetail += " (cause: " + sError + ")"
    return sDetail


_dictDockerStatus = {"sError": "", "sHint": "", "sCommand": ""}


def _fconnectionCreateDocker():
    """Lazily create a DockerConnection or return None.

    Failures are captured into ``_dictDockerStatus`` so the 503 path
    and the ``/api/system/docker-status`` probe can surface a specific
    diagnosis instead of a generic 'Docker support is not available'
    toast that leaves the user guessing whether the daemon, the
    runtime, or the binary is at fault.
    """
    try:
        from ..docker.dockerConnection import DockerConnection
        connection = DockerConnection()
    except Exception as error:
        _fnRecordDockerError(str(error) or repr(error))
        return None
    _fnClearDockerError()
    return connection


def _fnRecordDockerError(sError):
    """Store the most recent Docker init failure for surfacing in UI."""
    import sys
    from ..docker.dockerContext import fsActiveDockerContext
    dictDiagnosis = fdictDiagnoseDockerError(
        sError,
        sContext=fsActiveDockerContext(),
        sPlatform=sys.platform,
    )
    _dictDockerStatus["sError"] = sError
    _dictDockerStatus["sHint"] = dictDiagnosis["sHint"]
    _dictDockerStatus["sCommand"] = dictDiagnosis["sCommand"]


def _fnClearDockerError():
    """Reset the diagnosis holder when Docker is reachable."""
    _dictDockerStatus["sError"] = ""
    _dictDockerStatus["sHint"] = ""
    _dictDockerStatus["sCommand"] = ""


def fdictGetDockerStatus():
    """Return a snapshot of the current Docker availability state."""
    return {
        "bAvailable": not _dictDockerStatus["sError"],
        "sError": _dictDockerStatus["sError"],
        "sHint": _dictDockerStatus["sHint"],
        "sCommand": _dictDockerStatus["sCommand"],
    }


def fdictRetryDockerConnection(dictCtx):
    """Re-attempt the Docker connection and swap dictCtx on success.

    Mutating ``dictCtx['docker']`` lets every route closure pick up
    the new connection without a vaibify restart, because
    ``_ftupleBuildHelpers`` reads the connection from the shared
    raw-dict at call time rather than capturing it at build time.
    """
    from . import pipelineServer
    connectionNew = pipelineServer._fconnectionCreateDocker()
    dictCtx["docker"] = connectionNew
    return fdictGetDockerStatus()
