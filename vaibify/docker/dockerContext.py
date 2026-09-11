"""Docker context detection helpers.

These helpers identify the active Docker context so other vaibify
modules can gate Colima-specific advice and diagnostics. macOS
researchers commonly run Colima as a drop-in Docker daemon; many
failure modes (port forwarding quirks, VM filesystem semantics,
default-context drift) only apply when Colima is the active context.
Routing context-aware messaging through these helpers keeps that
detection in one place.
"""

import json
import os
import re
import subprocess
import sys


__all__ = [
    "fsActiveDockerContext", "fbColimaActive", "ftColimaVersion",
    "fsResolveDockerEndpoint", "fsReadActiveContextEndpoint",
    "fsColimaProfileName", "fdictClassifyDockerRuntime",
    "S_RUNTIME_DOCKER_DESKTOP", "S_RUNTIME_COLIMA",
    "S_RUNTIME_LINUX_ROOTFUL", "S_RUNTIME_LINUX_ROOTLESS",
    "S_RUNTIME_UNKNOWN", "fdictReadDaemonFacts",
]


def fsActiveDockerContext():
    """Return the active Docker context name, or '' on any error."""
    try:
        processResult = subprocess.run(
            ["docker", "context", "show"],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return ""
    if processResult.returncode != 0:
        return ""
    return (processResult.stdout or "").strip()


def fsReadActiveContextEndpoint():
    """Return the active context's docker endpoint, or '' on any error.

    The single authority on that read. ``DockerConnection`` used to run
    this command itself to seed ``DOCKER_HOST``; keeping the command in
    one module is what lets the ``doctor`` report and the connection
    agree about where vaibify is pointing, which is the entire value of
    reporting it.
    """
    try:
        processResult = subprocess.run(
            ["docker", "context", "inspect", "--format",
             "{{.Endpoints.docker.Host}}"],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return ""
    if processResult.returncode != 0:
        return ""
    return (processResult.stdout or "").strip()


def fsResolveDockerEndpoint():
    """Return the endpoint vaibify will talk to, as a readable phrase.

    Resolved exactly the way the connection resolves it: an explicit
    ``DOCKER_HOST`` wins, and otherwise the active context's endpoint
    does. Reporting the ENV VAR alone would answer "unset" on the
    common macOS setup, where the context does all the work -- an
    answer that is true about the variable and useless about the
    question.

    This is the fact that separates the two causes of an unreachable
    daemon: not running at all, versus running somewhere the
    researcher's shell reaches and vaibify does not. A researcher on
    Ubuntu had Docker Engine at the default socket while the current
    context pointed at a stopped Rancher Desktop, and identifying that
    took a round trip through ``docker context ls`` (2026-09-05).

    The last case says both were empty rather than naming docker-py's
    built-in default, which is docker-py's to define and which this
    module would be a second, eventually-wrong authority on.
    """
    sHost = os.environ.get("DOCKER_HOST")
    if sHost:
        return sHost + " (from DOCKER_HOST)"
    sContextHost = fsReadActiveContextEndpoint()
    if sContextHost:
        return sContextHost + " (from the active Docker context)"
    return "DOCKER_HOST unset and no context endpoint (docker-py default)"


# Colima names its default profile's context ``colima`` and every other
# profile's ``colima-<profile>``. Matching the bare name alone reported
# a researcher running ``colima start --profile gpu`` as not running
# Colima at all, so every piece of Colima-specific advice -- the arch
# remediation, the memory remediation, the hostagent log probe -- went
# silent on exactly the setup that needed it.
_RE_COLIMA_CONTEXT = re.compile(r"^colima(?:-(?P<profile>.+))?$")

S_COLIMA_DEFAULT_PROFILE = "default"


def fbColimaActive():
    """Return True iff the active Docker context is a Colima context."""
    return bool(_RE_COLIMA_CONTEXT.match(fsActiveDockerContext()))


def fsColimaProfileName():
    """Return the active Colima profile name, or '' when not Colima.

    The default profile answers ``"default"`` -- the name ``colima``
    itself uses for it -- so a caller can always print the profile it
    is talking about rather than printing nothing for the common case.
    """
    matchContext = _RE_COLIMA_CONTEXT.match(fsActiveDockerContext())
    if matchContext is None:
        return ""
    return matchContext.group("profile") or S_COLIMA_DEFAULT_PROFILE


S_RUNTIME_DOCKER_DESKTOP = "docker-desktop"
S_RUNTIME_COLIMA = "colima"
S_RUNTIME_LINUX_ROOTFUL = "linux-rootful"
S_RUNTIME_LINUX_ROOTLESS = "linux-rootless"
S_RUNTIME_UNKNOWN = "unknown"

# A rootless daemon's socket lives in the caller's own runtime
# directory. It is the one piece of evidence available without asking
# the daemon anything, which matters because the classification is
# consulted while diagnosing a daemon that may not answer.
_S_ROOTLESS_ENDPOINT_MARKER = "/run/user/"
_S_DESKTOP_ENDPOINT_MARKER = "/.docker/run/docker.sock"
_S_DESKTOP_CONTEXT_NAME = "desktop-linux"


def _fdictReadDockerInfoJson():
    """Return ``docker info`` as a decoded dict, or ``{}`` on any error."""
    try:
        processResult = subprocess.run(
            ["docker", "info", "--format", "{{json .}}"],
            capture_output=True, text=True, timeout=10,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return {}
    if processResult.returncode != 0:
        return {}
    try:
        jsonInfo = json.loads(processResult.stdout or "")
    except (ValueError, TypeError):
        return {}
    return jsonInfo if isinstance(jsonInfo, dict) else {}


def _fbInfoReportsRootless(jsonInfo):
    """Return True when ``docker info`` declares a rootless daemon."""
    listSecurity = jsonInfo.get("SecurityOptions") or []
    if any("rootless" in str(sOption) for sOption in listSecurity):
        return True
    return "rootless" in str(jsonInfo.get("Name") or "").lower()


def _fbInfoReportsDockerDesktop(jsonInfo):
    """Return True when ``docker info`` declares Docker Desktop."""
    sOperatingSystem = str(jsonInfo.get("OperatingSystem") or "")
    return "docker desktop" in sOperatingSystem.lower()


def _fsClassifyFromEvidence(sContext, sEndpoint, jsonInfo):
    """Return the runtime name implied by the three evidence sources."""
    if _RE_COLIMA_CONTEXT.match(sContext):
        return S_RUNTIME_COLIMA
    if _fbInfoReportsDockerDesktop(jsonInfo):
        return S_RUNTIME_DOCKER_DESKTOP
    if sContext == _S_DESKTOP_CONTEXT_NAME:
        return S_RUNTIME_DOCKER_DESKTOP
    if _S_DESKTOP_ENDPOINT_MARKER in sEndpoint:
        return S_RUNTIME_DOCKER_DESKTOP
    if _fbInfoReportsRootless(jsonInfo):
        return S_RUNTIME_LINUX_ROOTLESS
    if _S_ROOTLESS_ENDPOINT_MARKER in sEndpoint:
        return S_RUNTIME_LINUX_ROOTLESS
    # A LOCAL unix socket on Linux, and nothing weaker. A `tcp://` or
    # `ssh://` endpoint reaches a daemon on another machine, where no
    # command this host can print manages anything -- calling that
    # "rootful Linux Engine" would answer `sudo systemctl start
    # docker` about a service that is not here.
    if sys.platform.startswith("linux") and sEndpoint.startswith("unix://"):
        return S_RUNTIME_LINUX_ROOTFUL
    return S_RUNTIME_UNKNOWN


def fdictClassifyDockerRuntime():
    """Identify WHICH Docker runtime this host is talking to.

    Every remediation command a diagnostic can offer depends on the
    answer: ``sudo systemctl restart docker`` is wrong on Docker
    Desktop, wrong on Colima, and wrong for a rootless daemon, where
    the unit is a ``--user`` one and the ``sudo`` actively breaks it.

    Returns ``sRuntime`` plus the evidence it was decided from, so a
    report can say WHY it thinks what it thinks. ``S_RUNTIME_UNKNOWN``
    is a real answer and callers must render it as one: a diagnostic
    that guesses a runtime prints a command that does not apply, which
    is worse than saying it cannot tell.
    """
    sContext = fsActiveDockerContext()
    sEndpoint = os.environ.get("DOCKER_HOST") or fsReadActiveContextEndpoint()
    jsonInfo = _fdictReadDockerInfoJson()
    sRuntime = _fsClassifyFromEvidence(sContext, sEndpoint, jsonInfo)
    return {
        "sRuntime": sRuntime,
        "sContextName": sContext,
        "sColimaProfile": fsColimaProfileName(),
        "sEndpoint": sEndpoint,
        "bDaemonAnswered": bool(jsonInfo),
    }


_RE_COLIMA_VERSION = re.compile(r"(\d+)\.(\d+)(?:\.(\d+))?")


def ftColimaVersion():
    """Return the installed Colima version as a (major, minor, patch) tuple.

    Returns ``()`` when Colima is missing, the call times out, or the
    output cannot be parsed.
    """
    sOutput = _fsRunColimaVersion()
    if not sOutput:
        return ()
    return _ftParseColimaVersion(sOutput)


def _fsRunColimaVersion():
    """Run ``colima version`` and return its stdout, '' on failure."""
    try:
        processResult = subprocess.run(
            ["colima", "version"],
            capture_output=True, text=True, timeout=5,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return ""
    if processResult.returncode != 0:
        return ""
    return processResult.stdout or ""


def _ftParseColimaVersion(sOutput):
    """Parse a (major, minor, patch) tuple out of `colima version` output."""
    tFromJson = _ftTryParseColimaJsonVersion(sOutput)
    if tFromJson:
        return tFromJson
    matchVersion = _RE_COLIMA_VERSION.search(sOutput)
    if not matchVersion:
        return ()
    iMajor = int(matchVersion.group(1))
    iMinor = int(matchVersion.group(2))
    iPatch = int(matchVersion.group(3) or "0")
    return (iMajor, iMinor, iPatch)


def _ftTryParseColimaJsonVersion(sOutput):
    """Try to parse a `colima version --json`-style payload from sOutput."""
    sStripped = sOutput.strip()
    if not sStripped or not sStripped.startswith("{"):
        return ()
    try:
        dictPayload = json.loads(sStripped)
    except (ValueError, TypeError):
        return ()
    sVersion = dictPayload.get("version") or dictPayload.get("Version") or ""
    matchVersion = _RE_COLIMA_VERSION.search(sVersion)
    if not matchVersion:
        return ()
    iMajor = int(matchVersion.group(1))
    iMinor = int(matchVersion.group(2))
    iPatch = int(matchVersion.group(3) or "0")
    return (iMajor, iMinor, iPatch)


def fdictReadDaemonFacts():
    """Return what the DAEMON says about itself, as plain values.

    The CLI lane's authority on the daemon's own figures. It exists
    beside ``daemonCapacity`` rather than duplicating it: that module
    answers the hub, which holds a Docker connection and is sizing a
    container; this one answers a diagnostic that may be running
    because no connection can be made. The numbers are the same
    numbers, read the same way -- from the daemon, never from the host
    -- and on macOS the difference is not academic: the daemon is a
    virtual machine with its own allocation, measured at 8.3 GB under
    a 16 GB host.

    ``bAnswered`` is False when the daemon said nothing, and every
    figure is then zero. A caller must render that as unassessed, not
    as a daemon with no CPUs.
    """
    jsonInfo = _fdictReadDockerInfoJson()
    return {
        "bAnswered": bool(jsonInfo),
        "iCpuCount": int(jsonInfo.get("NCPU") or 0),
        "iMemoryBytes": int(jsonInfo.get("MemTotal") or 0),
        "sOperatingSystem": str(jsonInfo.get("OperatingSystem") or ""),
        "sDataRootDirectory": str(jsonInfo.get("DockerRootDir") or ""),
    }
