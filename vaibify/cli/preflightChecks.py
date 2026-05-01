"""Pre-flight checks shared between vaibify build, start, and doctor.

Each helper returns a single ``PreflightResult`` (or ``None`` when the
check does not apply, such as Colima-only checks on a non-Colima host).
The shared module keeps daemon-reachability and Colima-version logic
in one place so build, start, and the standalone ``vaibify doctor``
sub-command emit consistent diagnostics.
"""

import subprocess

from .preflightResult import PreflightResult


__all__ = [
    "fpreflightDaemon",
    "fpreflightColimaVersion",
    "fpreflightDockerContextActive",
]


_S_SOCKET_PERMISSION_PATTERN = (
    "permission denied while trying to connect to the docker daemon socket"
)

_T_COLIMA_MIN_VERSION = (0, 5, 0)


def _fbDockerSocketPermissionDenied():
    """Return True iff `docker info` fails with a permission-denied stderr."""
    try:
        resultProcess = subprocess.run(
            ["docker", "info"],
            capture_output=True, text=True, timeout=10,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False
    if resultProcess.returncode == 0:
        return False
    sStderr = (resultProcess.stderr or "").lower()
    return _S_SOCKET_PERMISSION_PATTERN in sStderr


def _fsSocketPermissionRemediation():
    """Return remediation text for a permission-denied Docker socket."""
    return (
        "Docker socket unreadable. If you switched between Docker "
        "Desktop and Colima:\n"
        "  - run `unset DOCKER_HOST` and restart your shell.\n"
        "On Linux: ensure your user is in the docker group: "
        "`sudo usermod -aG docker $USER` (then re-login)."
    )


def _fsDaemonRemediation(sNextCommand):
    """Return remediation text appropriate for the active Docker context."""
    from vaibify.docker.dockerContext import fbColimaActive
    sRetryHint = f" Then retry `vaibify {sNextCommand}`." if sNextCommand else ""
    if fbColimaActive():
        return f"Run `colima start` to bring up the Docker daemon.{sRetryHint}"
    return f"Start Docker Desktop or your Docker daemon.{sRetryHint}"


def fpreflightDaemon(sNextCommand=""):
    """Check the Docker daemon is reachable, with Colima-aware remediation."""
    from vaibify.docker import fbDockerDaemonReachable
    if fbDockerDaemonReachable():
        return PreflightResult(
            sName="docker-daemon", sLevel="ok",
            sMessage="Docker daemon reachable.",
        )
    if _fbDockerSocketPermissionDenied():
        return PreflightResult(
            sName="docker-daemon", sLevel="fail",
            sMessage="Docker daemon socket permission denied.",
            sRemediation=_fsSocketPermissionRemediation(),
        )
    return PreflightResult(
        sName="docker-daemon", sLevel="fail",
        sMessage="Docker daemon not reachable.",
        sRemediation=_fsDaemonRemediation(sNextCommand),
    )


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
