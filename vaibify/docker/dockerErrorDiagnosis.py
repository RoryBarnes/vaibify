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


import re


__all__ = [
    "fdictDiagnoseDockerError", "fdictDiagnoseContainerOperationError",
    "fsExplainContainerOperationFailure", "flistDecisiveBuildLines",
    "fdictDiagnoseBuildFailure", "fsExplainBuildFailure",
]


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
        return _fdictSocketAbsentDiagnosis(
            _fdictResolveRuntime(dictRuntime),
        )
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


def _fdictSocketAbsentDiagnosis(dictRuntime):
    """Diagnosis for a connection that found no socket file to open.

    This must be answered separately from a missing binary, and the
    two are easy to confuse: docker-py reports it as ``Error while
    fetching server API version: ('Connection aborted.',
    FileNotFoundError(2, 'No such file or directory'))``, a message
    naming neither the socket path nor the word "docker". The
    binary-missing patterns below therefore matched it and told
    researchers with a working ``docker`` CLI to install Docker
    (researcher-reported on Ubuntu, 2026-09-04).

    When the classifier knows WHICH runtime owns the endpoint, an
    absent socket there has one meaning -- that runtime is not
    running -- and the answer is the same one the daemon-unreachable
    branch gives, so the dashboard and ``vaibify doctor`` cannot
    describe one stopped Colima two different ways (they did, on
    2026-09-18). Only an UNKNOWN runtime leaves the ambiguity real:
    the daemon is stopped, or it listens somewhere vaibify did not
    resolve -- a rootless or Docker Desktop context the researcher's
    shell inherits and the hub process did not -- and then the
    command is the one that tells them which.
    """
    from .dockerContext import S_RUNTIME_UNKNOWN
    from .runtimeRemedies import S_SITUATION_DAEMON_UNREACHABLE
    if dictRuntime.get("sRuntime", S_RUNTIME_UNKNOWN) != S_RUNTIME_UNKNOWN:
        return _fdictRuntimeRemedy(
            S_SITUATION_DAEMON_UNREACHABLE, dictRuntime,
        )
    return {
        "sHint": "No Docker socket exists at the endpoint vaibify "
                 "resolved. Compare the active endpoint the command "
                 "below prints with the one vaibify used: if they "
                 "match, the daemon is stopped; if they differ, "
                 "vaibify inherited a different Docker context than "
                 "your shell.",
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


_RE_ALLOCATED_PORT = re.compile(r"(?:0\.0\.0\.0|127\.0\.0\.1|\[::\]|:)(\d{2,5})\b")


def _fdictImageNotBuiltDiagnosis(sProjectName):
    """The daemon looked for an image nobody has built yet.

    Docker's own wording -- "pull access denied ... repository does not
    exist or may require 'docker login'" -- is about Docker Hub, and a
    researcher who had just created the project read it as vaibify
    denying that the project existed (2026-09-18).
    """
    return {
        "sHint": (
            f"The image for '{sProjectName}' has not been built yet, so "
            "there is nothing to start. Click its tile to build it, or "
            "choose Rebuild from its \u22ee menu."
        ),
        "sCommand": f"vaibify build -p {sProjectName}",
    }


def _fdictPortTakenDiagnosis(sLower):
    """A port the container publishes is held by another program."""
    matchPort = _RE_ALLOCATED_PORT.search(sLower)
    sPort = matchPort.group(1) if matchPort else ""
    sWhich = f"port {sPort}" if sPort else "a port this container publishes"
    return {
        "sHint": (
            f"Another program on this machine is already listening on "
            f"{sWhich}. Stop that program, or change the port in the "
            "project's settings (\u2699 on its tile), then start again."
        ),
        "sCommand": f"lsof -nP -iTCP:{sPort} -sTCP:LISTEN" if sPort else "",
    }


def _fdictNameTakenDiagnosis(sProjectName):
    """A container with this name survives from an earlier start."""
    return {
        "sHint": (
            f"A container named '{sProjectName}' already exists from an "
            "earlier start, so a new one cannot be created under that "
            "name. Remove the old one, then start again."
        ),
        "sCommand": f"docker rm -f {sProjectName}",
    }


def _fdictMountSourceMissingDiagnosis():
    """A host directory the container mounts is gone."""
    return {
        "sHint": (
            "A directory this container mounts from this machine no "
            "longer exists. Restore it, or remove that entry from the "
            "project's bind mounts, then start again."
        ),
        "sCommand": "",
    }


def fdictDiagnoseContainerOperationError(
    sError, sProjectName, dictRuntime=None,
):
    """Return ``{sHint, sCommand}`` for a failed create/start/stop/build.

    The daemon's stderr is precise and useless to a researcher: it
    names Docker Hub, port bindings and mount configs in Docker's own
    vocabulary. This branch of the catalog translates the failures
    vaibify has watched researchers hit into a sentence about THEIR
    project and the control that fixes it. ``None`` means the text
    matched nothing, and the caller shows it as it came -- a guess
    dressed as a diagnosis is the failure the whole catalog exists to
    prevent.
    """
    sLower = (sError or "").lower()
    if not sLower:
        return None
    if (
        "unable to find image" in sLower
        or "pull access denied" in sLower
        or "repository does not exist" in sLower
        or "no such image" in sLower
    ):
        return _fdictImageNotBuiltDiagnosis(sProjectName)
    if "port is already allocated" in sLower or "address already in use" in sLower:
        return _fdictPortTakenDiagnosis(sLower)
    if "is already in use by container" in sLower:
        return _fdictNameTakenDiagnosis(sProjectName)
    if "bind source path does not exist" in sLower or (
        "invalid mount config" in sLower
    ):
        return _fdictMountSourceMissingDiagnosis()
    if "no space left on device" in sLower:
        from .runtimeRemedies import S_SITUATION_RECLAIM_DISK
        return _fdictRuntimeRemedy(
            S_SITUATION_RECLAIM_DISK, _fdictResolveRuntime(dictRuntime),
        )
    if _fbErrorIsDaemonUnreachable(sLower):
        return _fdictDaemonUnreachableDiagnosis(
            _fdictResolveRuntime(dictRuntime),
        )
    if "permission denied" in sLower:
        return _fdictPermissionDeniedDiagnosis(
            _fdictResolveRuntime(dictRuntime),
        )
    return None


def fsExplainContainerOperationFailure(sOperation, sProjectName, sRawError):
    """Return the one sentence a researcher reads for a failed operation.

    Leads with the translated cause and what to do; keeps the daemon's
    own words, bounded, in parentheses, because a translation that hid
    its evidence could not be checked and a wrong one could not be
    caught.
    """
    sRaw = " ".join((sRawError or "").split())[:240]
    dictDiagnosis = fdictDiagnoseContainerOperationError(sRaw, sProjectName)
    if dictDiagnosis is None:
        return f"{sOperation} of '{sProjectName}' failed: {sRaw}"
    sSentence = f"{sOperation} of '{sProjectName}' failed. {dictDiagnosis['sHint']}"
    if dictDiagnosis["sCommand"]:
        sSentence += f" Command: {dictDiagnosis['sCommand']}"
    return f"{sSentence} (Docker said: {sRaw})"


_RE_BUILDKIT_LINE_PREFIX = re.compile(r"^#\d+\s+(?:\d+\.\d+\s+)?")
_T_BUILD_DECISIVE_MARKERS = (
    "this build stopped on purpose", "e: ", "error", "could not resolve",
    "temporary failure resolving", "failed to fetch", "no space left",
    "pull access denied", "not found", "unable to locate package",
    "no matching distribution", "could not find a version",
    "did not complete successfully", "returned a non-zero code",
    "permission denied", "connection refused", "timed out",
)


def flistDecisiveBuildLines(sStderrTail, iLimit=6):
    """Return the lines of a build's output that say why it failed.

    BuildKit ends a failed build by echoing the whole failing Dockerfile
    step, sixty lines of ``>>>``-prefixed source, which is what filled
    the tail a researcher was shown while the one apt line that named
    the cause had scrolled out of it. The echo and the separators are
    dropped, the ``#12 3.45`` prefixes stripped, and only lines carrying
    a failure marker survive, newest last.
    """
    listDecisive = []
    for sRawLine in (sStderrTail or "").splitlines():
        sLine = _RE_BUILDKIT_LINE_PREFIX.sub("", sRawLine).strip()
        if not sLine or ">>>" in sLine or set(sLine) <= {"-", "="}:
            continue
        if re.match(r"^\s*\d+\s*\|", sRawLine):
            continue
        sLower = sLine.lower()
        if any(sMarker in sLower for sMarker in _T_BUILD_DECISIVE_MARKERS):
            if sLine not in listDecisive:
                listDecisive.append(sLine)
    return listDecisive[-iLimit:]


_REGEX_PIP_MISSING_DISTRIBUTION = re.compile(
    r"No matching distribution found for (\S+)"
    r"|Could not find a version that satisfies the requirement (\S+)",
)


def _fsExplainMissingDistribution(sStderrTail):
    """Name the requirement pip refused, when its line survives the tail.

    The generic sentence sent a researcher to reread every name in
    vaibify.yml while pip's own line, three lines up, said "matplolib".
    """
    matchName = _REGEX_PIP_MISSING_DISTRIBUTION.search(sStderrTail or "")
    if matchName is None:
        return (
            "A name or version under pythonPackages in vaibify.yml does "
            "not exist on the package index. Fix it, then rebuild."
        )
    sRequirement = (matchName.group(1) or matchName.group(2)).strip("'\"")
    return (
        f"'{sRequirement}' under pythonPackages in vaibify.yml does not "
        "exist on the package index, or no release of it matches. Fix "
        "the name or version in vaibify.yml, then rebuild."
    )


def fdictDiagnoseBuildFailure(sStderrTail, sProjectName, dictRuntime=None):
    """Return ``{sHint, sCommand}`` for a failed image build, or None."""
    sLower = "\n".join(flistDecisiveBuildLines(sStderrTail, 200)).lower()
    if not sLower:
        return None
    if "no space left on device" in sLower:
        # Judged before every other cause: a step that could not write
        # fails in whatever way it fails, and its own banner may blame
        # the network (an overlay installer did, 2026-09-21).
        from .runtimeRemedies import S_SITUATION_RECLAIM_DISK
        dictRemedy = _fdictRuntimeRemedy(
            S_SITUATION_RECLAIM_DISK, _fdictResolveRuntime(dictRuntime),
        )
        return {
            "sHint": (
                "The Docker daemon's disk is full, so the failing step "
                "could not write; whatever else that step printed follows "
                f"from that. {dictRemedy['sHint']}"
            ),
            "sCommand": dictRemedy["sCommand"],
        }
    if "this build stopped on purpose" in sLower or "toolchain" in sLower:
        return {
            "sHint": (
                "vaibify's pinned compiler toolchain could not be installed "
                "from Ubuntu's archive snapshot. This is vaibify's own pin, "
                "not your project's packages: either the pin list and the "
                "snapshot date in vaibify's Dockerfile disagree, "
                "snapshot.ubuntu.com could not be reached, or the daemon's "
                "architecture has no pin list (amd64 and arm64 do). Update "
                "vaibify, or report the build output, which lists what "
                "the archive offers."
            ),
            "sCommand": "python tools/checkToolchainEpoch.py --verify",
        }
    if "unable to locate package" in sLower:
        return {
            "sHint": (
                "A name under systemPackages in vaibify.yml is not a "
                "package Ubuntu's archive knows. Fix the name, then "
                "rebuild."
            ),
            "sCommand": "",
        }
    if "no matching distribution" in sLower or "could not find a version" in sLower:
        return {"sHint": _fsExplainMissingDistribution(sStderrTail), "sCommand": ""}
    if (
        "could not resolve" in sLower or "temporary failure resolving" in sLower
        or "failed to fetch" in sLower or "timed out" in sLower
    ):
        return {
            "sHint": (
                "The build could not reach the package archives from "
                "inside Docker: name resolution or the network failed in "
                "the daemon, not on this machine. Check the daemon's "
                "network with a diagnosis; on Colima a restart usually "
                "clears a stale resolver."
            ),
            "sCommand": "vaibify doctor",
        }
    if "no space left" in sLower:
        from .runtimeRemedies import S_SITUATION_RECLAIM_DISK
        return _fdictRuntimeRemedy(
            S_SITUATION_RECLAIM_DISK, _fdictResolveRuntime(dictRuntime),
        )
    if "pull access denied" in sLower or "manifest unknown" in sLower:
        return {
            "sHint": (
                "The base image the build starts from could not be "
                "pulled. Check the baseImage in vaibify.yml and that "
                "this machine can reach the registry."
            ),
            "sCommand": "",
        }
    return None


def fsExplainBuildFailure(sProjectName, sRawError, sStderrTail):
    """Return the sentence a researcher reads for a failed image build.

    Leads with the translated cause when the output is recognised,
    otherwise with the decisive lines of the output itself; the bare
    "Docker command failed (exit 1): docker buildx build ..." that used
    to be the whole message names the command and never the reason.
    """
    listDecisive = flistDecisiveBuildLines(sStderrTail, 3)
    sEvidence = " | ".join(listDecisive) or " ".join(
        (sRawError or "").split())[:240]
    dictDiagnosis = fdictDiagnoseBuildFailure(sStderrTail, sProjectName)
    if dictDiagnosis is None:
        return f"Build of '{sProjectName}' failed: {sEvidence}"
    sSentence = f"Build of '{sProjectName}' failed. {dictDiagnosis['sHint']}"
    if dictDiagnosis["sCommand"]:
        sSentence += f" Command: {dictDiagnosis['sCommand']}"
    return f"{sSentence} (Docker said: {sEvidence})"
