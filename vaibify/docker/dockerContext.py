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


__all__ = [
    "fsActiveDockerContext", "fbColimaActive", "ftColimaVersion",
    "fsResolveDockerEndpoint", "fsReadActiveContextEndpoint",
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


def fbColimaActive():
    """Return True iff the active Docker context is 'colima'."""
    return fsActiveDockerContext() == "colima"


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
