"""Pre-flight checks shared between vaibify build, start, and doctor.

Each helper returns a single ``PreflightResult`` (or ``None`` when the
check does not apply, such as Colima-only checks on a non-Colima host).
The shared module keeps daemon-reachability, Colima-version, hostagent
log, and Linux systemd logic in one place so build, start, and the
standalone ``vaibify doctor`` sub-command emit consistent diagnostics.
"""

import json
import subprocess
import sys
from pathlib import Path

from vaibify.docker.dockerErrorDiagnosis import fdictDiagnoseDockerError

from .preflightResult import PreflightResult


__all__ = [
    "fpreflightDaemon",
    "fpreflightColimaVersion",
    "fpreflightDockerContextActive",
    "fpreflightColimaHostagentLog",
    "fpreflightLinuxDockerService",
]


_S_SOCKET_PERMISSION_PATTERN = (
    "permission denied while trying to connect to the docker daemon socket"
)

_T_COLIMA_MIN_VERSION = (0, 5, 0)

_I_MAX_LOG_TAIL_LINES = 200

_S_GENERIC_FALLBACK_COMMAND = "docker info"


# -----------------------------------------------------------------------
# Shared docker info probe
# -----------------------------------------------------------------------


def _ftDockerInfoProbe():
    """Run ``docker info`` once, return ``(iReturnCode, sStderr)``.

    Returns ``(-1, "")`` when the docker binary is missing or the call
    times out so callers can distinguish that from a non-zero daemon
    response.
    """
    try:
        resultProcess = subprocess.run(
            ["docker", "info"],
            capture_output=True, text=True, timeout=10,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return -1, ""
    return resultProcess.returncode, (resultProcess.stderr or "")


# -----------------------------------------------------------------------
# Daemon-reachability check
# -----------------------------------------------------------------------


def _fsSocketPermissionRemediation():
    """Return remediation text for a permission-denied Docker socket."""
    return (
        "Docker socket unreadable. If you switched between Docker "
        "Desktop and Colima:\n"
        "  - run `unset DOCKER_HOST` and restart your shell.\n"
        "On Linux: ensure your user is in the docker group: "
        "`sudo usermod -aG docker $USER` (then re-login)."
    )


def _fpreflightDaemonOk():
    """Build the ok-level PreflightResult for a reachable daemon."""
    return PreflightResult(
        sName="docker-daemon", sLevel="ok",
        sMessage="Docker daemon reachable.",
    )


def _fpreflightDaemonSocketPermission():
    """Build the fail-level PreflightResult for a permission-denied socket."""
    return PreflightResult(
        sName="docker-daemon", sLevel="fail",
        sMessage="Docker daemon socket permission denied.",
        sRemediation=_fsSocketPermissionRemediation(),
    )


def _fsRetryHint(sNextCommand):
    """Return the trailing 'Then retry vaibify X.' fragment, or ''."""
    if not sNextCommand:
        return ""
    return f" Then retry `vaibify {sNextCommand}`."


def _fsBuildDaemonRemediation(dictDiagnosis, sStderr, sNextCommand):
    """Compose remediation lines: hint, raw error, retry hint."""
    sHint = dictDiagnosis["sHint"] + _fsRetryHint(sNextCommand)
    listLines = [sHint]
    sFirstLine = (sStderr or "").strip().splitlines()[:1]
    if sFirstLine:
        listLines.append(f"Raw error: {sFirstLine[0]}")
    return "\n".join(listLines)


def _fpreflightDaemonFromStderr(sStderr, sNextCommand):
    """Build the fail-level PreflightResult via the diagnosis catalog."""
    from vaibify.docker.dockerContext import fsActiveDockerContext
    dictDiagnosis = fdictDiagnoseDockerError(
        sStderr,
        sContext=fsActiveDockerContext(),
        sPlatform=sys.platform,
    )
    sRemediation = _fsBuildDaemonRemediation(
        dictDiagnosis, sStderr, sNextCommand,
    )
    return PreflightResult(
        sName="docker-daemon", sLevel="fail",
        sMessage="Docker daemon not reachable.",
        sRemediation=sRemediation,
        sCommand=dictDiagnosis["sCommand"],
    )


def fpreflightDaemon(sNextCommand=""):
    """Check the Docker daemon is reachable, with diagnosis-driven hints."""
    iReturnCode, sStderr = _ftDockerInfoProbe()
    if iReturnCode == 0:
        return _fpreflightDaemonOk()
    if _S_SOCKET_PERMISSION_PATTERN in sStderr.lower():
        return _fpreflightDaemonSocketPermission()
    return _fpreflightDaemonFromStderr(sStderr, sNextCommand)


# -----------------------------------------------------------------------
# Colima version check
# -----------------------------------------------------------------------


def _fpreflightColimaVersionWarn(tVersion):
    """Build the warn-level PreflightResult for an old Colima version."""
    sVersion = ".".join(str(i) for i in tVersion)
    return PreflightResult(
        sName="colima-version", sLevel="warn",
        sMessage=(
            f"Colima {sVersion} is older than the supported floor "
            f"0.5.0; some Docker features may misbehave."
        ),
        sRemediation="Upgrade Colima to >= 0.5.0.",
    )


def fpreflightColimaVersion():
    """Warn when the installed Colima is below the supported floor."""
    from vaibify.docker.dockerContext import (
        fbColimaActive, ftColimaVersion,
    )
    if not fbColimaActive():
        return None
    tVersion = ftColimaVersion()
    if not tVersion or tVersion >= _T_COLIMA_MIN_VERSION:
        return None
    return _fpreflightColimaVersionWarn(tVersion)


def fpreflightDockerContextActive():
    """Report which Docker context is currently active."""
    from vaibify.docker.dockerContext import fsActiveDockerContext
    sContext = fsActiveDockerContext()
    if not sContext:
        return PreflightResult(
            sName="docker-context", sLevel="info",
            sMessage="Active Docker context could not be determined.",
        )
    return PreflightResult(
        sName="docker-context", sLevel="ok",
        sMessage=f"Active Docker context: {sContext}.",
    )


# -----------------------------------------------------------------------
# Colima hostagent log probe
# -----------------------------------------------------------------------


def _fpathColimaHostagentLog():
    """Return the path to Colima's default hostagent stderr log."""
    return Path.home() / ".colima" / "_lima" / "colima" / "ha.stderr.log"


def _fsReadColimaHostagentLogTail():
    """Return up to the last 200 lines of the hostagent log, '' on miss."""
    pathLog = _fpathColimaHostagentLog()
    try:
        sContent = pathLog.read_text(errors="replace")
    except (FileNotFoundError, OSError):
        return ""
    saLines = sContent.splitlines()[-_I_MAX_LOG_TAIL_LINES:]
    return "\n".join(saLines)


def _fsExtractLastFatalLogLine(sLogTail):
    """Walk a structured-log tail backward, return the latest fatal msg."""
    for sLine in reversed(sLogTail.splitlines()):
        sStripped = sLine.strip()
        if not sStripped.startswith("{"):
            continue
        try:
            dictEntry = json.loads(sStripped)
        except (ValueError, TypeError):
            continue
        sLevel = (dictEntry.get("level") or "").lower()
        if sLevel in ("fatal", "error"):
            return dictEntry.get("msg") or ""
    return ""


def _fbDiagnosisIsSpecific(dictDiagnosis):
    """True when the diagnosis matched a known pattern (not the fallback)."""
    return dictDiagnosis.get("sCommand") != _S_GENERIC_FALLBACK_COMMAND


def _fpreflightColimaHostagentWarn(sLastError, dictDiagnosis):
    """Build the warn-level PreflightResult for a recent hostagent error."""
    return PreflightResult(
        sName="colima-hostagent-log", sLevel="warn",
        sMessage=(
            f"Colima hostagent log contains a recent error: "
            f"{sLastError}"
        ),
        sRemediation=dictDiagnosis["sHint"],
        sCommand=dictDiagnosis["sCommand"],
    )


def fpreflightColimaHostagentLog():
    """Surface known fatal/error patterns from Colima's hostagent log."""
    from vaibify.docker.dockerContext import fbColimaActive
    if not fbColimaActive():
        return None
    sLogTail = _fsReadColimaHostagentLogTail()
    if not sLogTail:
        return None
    sLastError = _fsExtractLastFatalLogLine(sLogTail)
    if not sLastError:
        return None
    dictDiagnosis = fdictDiagnoseDockerError(
        sLastError, sContext="colima", sPlatform=sys.platform,
    )
    if not _fbDiagnosisIsSpecific(dictDiagnosis):
        return None
    return _fpreflightColimaHostagentWarn(sLastError, dictDiagnosis)


# -----------------------------------------------------------------------
# Linux systemd docker.service probe
# -----------------------------------------------------------------------


def _fsSystemDockerServiceStatus():
    """Return ``systemctl is-active docker`` output, '' on missing tool."""
    try:
        resultProcess = subprocess.run(
            ["systemctl", "is-active", "docker"],
            capture_output=True, text=True, timeout=5,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return ""
    return (resultProcess.stdout or "").strip()


def _fsRecentDockerJournalTail():
    """Return last 50 lines from journalctl, '' on failure."""
    try:
        resultProcess = subprocess.run(
            ["journalctl", "-u", "docker.service", "-n", "50", "--no-pager"],
            capture_output=True, text=True, timeout=5,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return ""
    return resultProcess.stdout or ""


def _fdictDefaultLinuxDockerStartDiagnosis():
    """Default diagnosis for an inactive docker.service without log signal."""
    return {
        "sHint": "systemd docker.service is not running. Start it.",
        "sCommand": "sudo systemctl start docker",
    }


def _fpreflightLinuxDockerServiceFail(sStatus):
    """Build the fail-level PreflightResult for an inactive docker.service."""
    sJournalTail = _fsRecentDockerJournalTail()
    dictDiagnosis = fdictDiagnoseDockerError(
        sJournalTail, sContext="", sPlatform="linux",
    )
    if not _fbDiagnosisIsSpecific(dictDiagnosis):
        dictDiagnosis = _fdictDefaultLinuxDockerStartDiagnosis()
    return PreflightResult(
        sName="docker-service", sLevel="fail",
        sMessage=f"systemd docker.service is {sStatus}.",
        sRemediation=dictDiagnosis["sHint"],
        sCommand=dictDiagnosis["sCommand"],
    )


def fpreflightLinuxDockerService():
    """Surface system docker.service status on Linux (non-Colima only)."""
    if sys.platform != "linux":
        return None
    from vaibify.docker.dockerContext import fbColimaActive
    if fbColimaActive():
        return None
    sStatus = _fsSystemDockerServiceStatus()
    if not sStatus or sStatus == "active":
        return None
    return _fpreflightLinuxDockerServiceFail(sStatus)
