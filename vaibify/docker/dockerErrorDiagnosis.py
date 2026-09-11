"""Pattern-match Docker init errors into actionable hints.

Common runtime failures (Colima stale disk lock, daemon not running,
absent socket, docker binary missing, socket permission denied) get
specific hints and copy-pasteable shell commands. Unrecognized
failures still produce a generic fallback so the caller can pair the
hint with the verbatim error text.

The same catalog feeds the FastAPI lazy-init path
(``pipelineServer._fnRecordDockerError``), the CLI preflight
(``preflightChecks.fpreflightDaemon``), and the ``vaibify doctor``
diagnostic probes that tail the Colima hostagent log or the systemd
journal. Keeping one catalog avoids drift between those surfaces.

**The runtime-dependent answers are not written here.** Three of them
-- what starts a stopped daemon, what restarts a stale Colima, and
what a permission-denied socket needs -- depend on WHICH runtime this
host talks to, and this module used to decide that from the context
NAME alone. It therefore answered ``colima start`` to a Docker Desktop
user, the default profile to somebody running ``colima --profile
gpu``, and ``sudo systemctl start docker`` to a rootless daemon whose
unit is a ``--user`` one. Those answers now come from
``runtimeRemedies``, which asks the classifier; this module keeps the
PATTERN MATCHING, which is what it is actually good at.
"""


__all__ = ["fdictDiagnoseDockerError"]


def fdictDiagnoseDockerError(
    sError, sContext="", sPlatform="", dictRuntime=None,
):
    """Return ``{sHint, sCommand}`` for a Docker init error string.

    ``sContext`` is the active Docker context name (``"colima"``,
    ``"desktop-linux"``, …). ``sPlatform`` is ``sys.platform``.

    ``dictRuntime`` is the answer from
    :func:`dockerContext.fdictClassifyDockerRuntime`, and it is what
    the three runtime-dependent branches are decided from. A caller
    that has already classified passes it; one that has not gets a
    classification made here, because the alternative -- falling back
    to the context NAME -- is exactly the guess that answered ``colima
    start`` to Docker Desktop users. The classification is made only
    on the branches that need it, so the common paths cost nothing.
    """
    sLower = (sError or "").lower()
    if "in use by instance" in sLower:
        return _fdictColimaStaleLockDiagnosis(
            _fdictResolveRuntime(dictRuntime),
        )
    if _fbErrorIsDaemonUnreachable(sLower):
        return _fdictDaemonUnreachableDiagnosis(
            _fdictResolveRuntime(dictRuntime),
        )
    if _fbErrorIsSocketAbsent(sLower):
        return _fdictSocketAbsentDiagnosis()
    if _fbErrorIsBinaryMissing(sLower):
        return _fdictBinaryMissingDiagnosis(sPlatform)
    if "permission denied" in sLower:
        return _fdictPermissionDeniedDiagnosis(
            _fdictResolveRuntime(dictRuntime),
        )
    return _fdictUnknownErrorDiagnosis()


def _fdictResolveRuntime(dictRuntime):
    """Return the caller's classification, or make one."""
    if dictRuntime:
        return dictRuntime
    from .dockerContext import fdictClassifyDockerRuntime
    return fdictClassifyDockerRuntime()


def _fdictRuntimeRemedy(sSituation, dictRuntime):
    """Return the catalog shape for one runtime-dependent situation."""
    from .runtimeRemedies import ftRemedyForSituation
    sHint, sCommand = ftRemedyForSituation(sSituation, dictRuntime)
    return {"sHint": sHint, "sCommand": sCommand}


def _fdictColimaStaleLockDiagnosis(dictRuntime):
    """Diagnosis for a Colima VM disk lock left from an unclean shutdown."""
    from .runtimeRemedies import S_SITUATION_STALE_COLIMA_LOCK
    return _fdictRuntimeRemedy(S_SITUATION_STALE_COLIMA_LOCK, dictRuntime)


def _fdictDaemonUnreachableDiagnosis(dictRuntime):
    """Diagnosis for a daemon-unreachable error, from the RUNTIME."""
    from .runtimeRemedies import S_SITUATION_DAEMON_UNREACHABLE
    return _fdictRuntimeRemedy(S_SITUATION_DAEMON_UNREACHABLE, dictRuntime)


def _fdictSocketAbsentDiagnosis():
    """Diagnosis for a connection that found no socket file to open.

    This must be answered separately from a missing binary, and the
    two are easy to confuse: docker-py reports it as ``Error while
    fetching server API version: ('Connection aborted.',
    FileNotFoundError(2, 'No such file or directory'))``, a message
    naming neither the socket path nor the word "docker". The
    binary-missing patterns below therefore matched it and told
    researchers with a working ``docker`` CLI to install Docker
    (researcher-reported on Ubuntu, 2026-09-04).

    The remaining ambiguity is real and is stated rather than
    guessed at: no socket at the resolved endpoint means either the
    daemon is stopped or it is listening somewhere vaibify did not
    resolve -- a rootless or Docker Desktop context the researcher's
    shell inherits and the hub process did not. Naming one cause
    would be the same wrong-remedy failure one step further on, so
    the command is the one that tells them which.
    """
    return {
        "sHint": "No Docker socket exists at the endpoint vaibify "
                 "resolved. Either the daemon is not running, or it "
                 "listens on a socket your shell reaches and vaibify "
                 "did not (a rootless or Docker Desktop context). "
                 "Compare the endpoint below with the one vaibify "
                 "used; if they agree, start the daemon.",
        "sCommand": "docker context ls",
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


def _fdictPermissionDeniedDiagnosis(dictRuntime):
    """Diagnosis for a permission-denied error on the Docker socket.

    The ``docker`` group answer belongs to a ROOTFUL Linux daemon and
    nowhere else. A rootless daemon's socket is the caller's own and
    the group does not govern it, so that advice is not merely
    unhelpful there -- it sends a researcher to add themselves to a
    group that changes nothing.
    """
    from .dockerContext import S_RUNTIME_LINUX_ROOTFUL
    from .runtimeRemedies import S_SITUATION_RESTART_RUNTIME
    if dictRuntime.get("sRuntime") == S_RUNTIME_LINUX_ROOTFUL:
        return {
            "sHint": "Docker socket permission was denied. Add your "
                     "user to the 'docker' group and re-login.",
            "sCommand": "sudo usermod -aG docker $USER",
        }
    dictRemedy = _fdictRuntimeRemedy(
        S_SITUATION_RESTART_RUNTIME, dictRuntime,
    )
    return {
        "sHint": (
            "Docker socket permission was denied, so the socket is "
            "not owned the way this host expects. " + dictRemedy["sHint"]
        ),
        "sCommand": dictRemedy["sCommand"],
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


def _fbErrorIsConnectionFailure(sLower):
    """True if the error text came from a daemon CONNECTION attempt."""
    if "error while fetching server api version" in sLower:
        return True
    return "connection aborted" in sLower


def _fbErrorIsSocketAbsent(sLower):
    """True if a connection attempt found no socket file at its endpoint."""
    if not _fbErrorIsConnectionFailure(sLower):
        return False
    return "filenotfounderror" in sLower or "[errno 2]" in sLower


def _fbErrorIsBinaryMissing(sLower):
    """True if the error text suggests the 'docker' binary is absent.

    Every clause requires the word "docker": a bare
    ``FileNotFoundError`` says only that SOMETHING was absent, and
    claiming it was the CLI is a statement the text does not support.
    """
    if ".sock" in sLower:
        return False
    if "docker" not in sLower:
        return False
    if "filenotfounderror" in sLower:
        return True
    if "no such file or directory" in sLower:
        return True
    return "[errno 2]" in sLower
