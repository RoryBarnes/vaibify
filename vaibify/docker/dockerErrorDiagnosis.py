"""Pattern-match Docker init errors into actionable hints.

Common runtime failures (Colima stale disk lock, daemon not running,
docker binary missing, socket permission denied) get specific hints
and copy-pasteable shell commands. Unrecognized failures still produce
a generic fallback so the caller can pair the hint with the verbatim
error text.

The same catalog feeds the FastAPI lazy-init path
(``pipelineServer._fnRecordDockerError``), the CLI preflight
(``preflightChecks.fpreflightDaemon``), and the ``vaibify doctor``
diagnostic probes that tail the Colima hostagent log or the systemd
journal. Keeping one catalog avoids drift between those surfaces.
"""


__all__ = ["fdictDiagnoseDockerError"]


def fdictDiagnoseDockerError(sError, sContext="", sPlatform=""):
    """Return ``{sHint, sCommand}`` for a Docker init error string.

    ``sContext`` is the active Docker context name (``"colima"``,
    ``"desktop-linux"``, …). ``sPlatform`` is ``sys.platform``.
    Empty defaults preserve the legacy macOS+Colima behaviour.
    """
    sLower = (sError or "").lower()
    if "in use by instance" in sLower:
        return _fdictColimaStaleLockDiagnosis()
    if _fbErrorIsDaemonUnreachable(sLower):
        return _fdictDaemonUnreachableDiagnosis(sContext, sPlatform)
    if _fbErrorIsBinaryMissing(sLower):
        return _fdictBinaryMissingDiagnosis(sPlatform)
    if "permission denied" in sLower:
        return _fdictPermissionDeniedDiagnosis(sContext, sPlatform)
    return _fdictUnknownErrorDiagnosis()


def _fbUseLinuxSystemd(sContext, sPlatform):
    """True when Linux + system Docker daemon (not Colima) is implied."""
    if sPlatform != "linux":
        return False
    if sContext == "colima":
        return False
    return True


def _fdictColimaStaleLockDiagnosis():
    """Diagnosis for a Colima VM disk lock left from an unclean shutdown."""
    return {
        "sHint": "Colima's VM lock is stale, likely from an "
                 "unclean shutdown. Force-stop and restart Colima.",
        "sCommand": "colima stop --force && colima start",
    }


def _fdictDaemonUnreachableDiagnosis(sContext, sPlatform):
    """Diagnosis for a daemon-unreachable error, context/platform aware."""
    if _fbUseLinuxSystemd(sContext, sPlatform):
        return {
            "sHint": "The Docker daemon (docker.service) is not "
                     "running. Start it via systemd.",
            "sCommand": "sudo systemctl start docker",
        }
    if sContext == "colima":
        return {
            "sHint": "Colima is not running. Run `colima start` to "
                     "bring up the Docker daemon.",
            "sCommand": "colima start",
        }
    return {
        "sHint": "The Docker daemon is not reachable. Start your "
                 "Docker runtime (Colima or Docker Desktop).",
        "sCommand": "colima start",
    }


def _fdictBinaryMissingDiagnosis(sPlatform):
    """Diagnosis for a missing 'docker' binary on PATH."""
    if sPlatform == "linux":
        return {
            "sHint": "The 'docker' command was not found on PATH. "
                     "Install Docker Engine for your distribution.",
            "sCommand": "sudo apt-get install -y docker.io",
        }
    return {
        "sHint": "The 'docker' command was not found on PATH. "
                 "Install Docker Desktop or Colima.",
        "sCommand": "brew install colima docker",
    }


def _fdictPermissionDeniedDiagnosis(sContext, sPlatform):
    """Diagnosis for a permission-denied error on the Docker socket."""
    if _fbUseLinuxSystemd(sContext, sPlatform):
        return {
            "sHint": "Docker socket permission was denied. Add your "
                     "user to the 'docker' group and re-login.",
            "sCommand": "sudo usermod -aG docker $USER",
        }
    return {
        "sHint": "Docker socket permission was denied. Restart "
                 "your runtime so the socket is recreated with "
                 "the expected ownership.",
        "sCommand": "colima restart",
    }


def _fdictUnknownErrorDiagnosis():
    """Fallback diagnosis when no pattern matches the error string."""
    return {
        "sHint": "Docker is not reachable. Verify that your Docker "
                 "runtime is running and that 'docker info' succeeds.",
        "sCommand": "docker info",
    }


def _fbErrorIsDaemonUnreachable(sLower):
    """True if the error text suggests the daemon socket is down."""
    if "cannot connect" in sLower and (
        "daemon" in sLower or "docker.sock" in sLower
    ):
        return True
    if "connection refused" in sLower and "docker" in sLower:
        return True
    if "is the docker daemon running" in sLower:
        return True
    return False


def _fbErrorIsBinaryMissing(sLower):
    """True if the error text suggests the 'docker' binary is absent."""
    if ".sock" in sLower:
        return False
    if "filenotfounderror" in sLower:
        return True
    if "no such file or directory" in sLower and "docker" in sLower:
        return True
    if "[errno 2]" in sLower and "docker" in sLower:
        return True
    return False
