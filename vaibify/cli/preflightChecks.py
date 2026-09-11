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
    "fpreflightDockerEndpoint",
    "fpreflightCouncilCredentialEvidence",
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
        processResult = subprocess.run(
            ["docker", "info"],
            capture_output=True, text=True, timeout=10,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return -1, ""
    return processResult.returncode, (processResult.stderr or "")


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
    """Build the fail-level PreflightResult via the diagnosis catalog.

    The RUNTIME travels with the error, not just the context name.
    This is the oldest and most-seen finding vaibify produces, and
    until the classification reached it, it answered ``colima start``
    to a Docker Desktop user on macOS and ``sudo systemctl start
    docker`` to a rootless daemon.
    """
    from vaibify.docker.dockerContext import (
        fdictClassifyDockerRuntime, fsActiveDockerContext,
    )
    dictDiagnosis = fdictDiagnoseDockerError(
        sStderr,
        sContext=fsActiveDockerContext(),
        sPlatform=sys.platform,
        dictRuntime=fdictClassifyDockerRuntime(),
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


def fpreflightDockerEndpoint():
    """Report the endpoint vaibify would talk to, before it tries.

    The context NAME and the endpoint it resolves to are different
    facts, and only the second one identifies the failure a researcher
    actually hit: Docker Engine running at the default socket while
    the current context pointed at a stopped Rancher Desktop
    (2026-09-05). The daemon check next to this one answers reachable
    or not; this answers *where*, which is the half that says what to
    do about it.

    Always informational. A context pointing somewhere unusual is a
    legitimate configuration, not a fault, and grading it would put a
    warning in front of everyone running rootless Docker.
    """
    from vaibify.docker.dockerContext import fsResolveDockerEndpoint
    return PreflightResult(
        sName="docker-endpoint", sLevel="info",
        sMessage=(
            "vaibify will use the Docker endpoint "
            f"{fsResolveDockerEndpoint()}."
        ),
    )


def _fdictEvaluateCouncilProvider(sProvider):
    """Return the gate's verdict for one council provider, never raising.

    The gate refuses an unknown provider with ``ValueError``, and the
    set of council providers is not the set of API-key providers -- a
    readiness report is the wrong place to learn that by traceback.
    """
    from vaibify.gui.agentCouncilCredentialGate import (
        fdictEvaluateCredentialEnablement,
    )
    try:
        return fdictEvaluateCredentialEnablement(sProvider)
    except Exception as errorGate:
        return {
            "bEnabled": False,
            "sReason": f"the gate could not be asked ({errorGate})",
        }


def fpreflightCouncilCredentialEvidence():
    """Report whether the Agent Council's runner backend is enabled.

    A host-side prerequisite like the other two in this family: it is
    satisfied on the MACHINE, it does not travel with the repository,
    and until it is satisfied the researcher meets a greyed button with
    a paragraph attached to it. The gate already returns a
    display-ready reason, so this reports that reason rather than
    composing a second one.

    It REPORTS; it must never offer to satisfy the prerequisite. The
    evidence record is deliberately not writable by any agent or CI --
    that is the whole gate -- so a doctor that offered to write one
    would defeat it rather than describe it.
    """
    from vaibify.gui.agentCouncilProviderRegistry import (
        SET_COUNCIL_PROVIDERS,
    )
    listProviders = sorted(SET_COUNCIL_PROVIDERS)
    dictVerdicts = {
        sProvider: _fdictEvaluateCouncilProvider(sProvider)
        for sProvider in listProviders
    }
    listEnabled = [
        sProvider for sProvider in listProviders
        if dictVerdicts[sProvider].get("bEnabled")
    ]
    if listEnabled:
        return PreflightResult(
            sName="council-credentials", sLevel="ok",
            sMessage=(
                "the Agent Council runner backend is enabled for: "
                + ", ".join(listEnabled)
            ),
        )
    sReason = str(
        dictVerdicts[listProviders[0]].get("sReason", "")
    ) if listProviders else "no council providers are registered"
    return PreflightResult(
        sName="council-credentials", sLevel="info",
        sMessage="the Agent Council is unavailable on this machine: "
                 + sReason,
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


def _fsSystemDockerServiceStatus(bRootless=False):
    """Return ``systemctl is-active docker`` output, '' on missing tool.

    A ROOTLESS daemon is a ``--user`` unit, so the system-wide query
    answers ``inactive`` for a daemon that is running perfectly. That
    is not a cosmetic difference: the check below turns the answer
    into a ``fail`` telling the researcher to ``sudo systemctl start
    docker``, which starts a SECOND, rootful daemon their context does
    not point at.
    """
    saCommand = ["systemctl"]
    if bRootless:
        saCommand.append("--user")
    saCommand.extend(["is-active", "docker"])
    try:
        processResult = subprocess.run(
            saCommand, capture_output=True, text=True, timeout=5,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return ""
    return (processResult.stdout or "").strip()


def _fsRecentDockerJournalTail():
    """Return last 50 lines from journalctl, '' on failure."""
    try:
        processResult = subprocess.run(
            ["journalctl", "-u", "docker.service", "-n", "50", "--no-pager"],
            capture_output=True, text=True, timeout=5,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return ""
    return processResult.stdout or ""


def _fdictDefaultLinuxDockerStartDiagnosis(bRootless):
    """Default diagnosis for an inactive docker.service without log signal."""
    if bRootless:
        return {
            "sHint": (
                "the rootless docker.service is not running. Start it "
                "as your own user -- `sudo` would start the rootful "
                "daemon your context does not point at."
            ),
            "sCommand": "systemctl --user start docker",
        }
    return {
        "sHint": "systemd docker.service is not running. Start it.",
        "sCommand": "sudo systemctl start docker",
    }


def _fpreflightLinuxDockerServiceFail(sStatus, bRootless):
    """Build the fail-level PreflightResult for an inactive docker.service."""
    sJournalTail = _fsRecentDockerJournalTail()
    dictDiagnosis = fdictDiagnoseDockerError(
        sJournalTail, sContext="", sPlatform="linux",
    )
    if not _fbDiagnosisIsSpecific(dictDiagnosis) or bRootless:
        dictDiagnosis = _fdictDefaultLinuxDockerStartDiagnosis(bRootless)
    sUnit = "the rootless docker.service" if bRootless else "docker.service"
    return PreflightResult(
        sName="docker-service", sLevel="fail",
        sMessage=f"systemd {sUnit} is {sStatus}.",
        sRemediation=dictDiagnosis["sHint"],
        sCommand=dictDiagnosis["sCommand"],
    )


def fpreflightLinuxDockerService():
    """Surface systemd docker.service status on Linux.

    Asks the runtime classifier which daemon this host is talking to,
    rather than assuming a rootful unit unless the context is exactly
    ``colima``. Before that it reported every healthy ROOTLESS daemon
    as an inactive service, because the rootless unit is a ``--user``
    one and the system-wide query cannot see it.
    """
    if sys.platform != "linux":
        return None
    from vaibify.docker.dockerContext import (
        S_RUNTIME_COLIMA, S_RUNTIME_DOCKER_DESKTOP,
        S_RUNTIME_LINUX_ROOTLESS, fdictClassifyDockerRuntime,
    )
    dictRuntime = fdictClassifyDockerRuntime()
    if dictRuntime["sRuntime"] in (
        S_RUNTIME_COLIMA, S_RUNTIME_DOCKER_DESKTOP,
    ):
        return None
    bRootless = dictRuntime["sRuntime"] == S_RUNTIME_LINUX_ROOTLESS
    sStatus = _fsSystemDockerServiceStatus(bRootless)
    if not sStatus or sStatus == "active":
        return None
    return _fpreflightLinuxDockerServiceFail(sStatus, bRootless)
