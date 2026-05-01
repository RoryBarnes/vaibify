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
import re
import subprocess


__all__ = [
    "fsActiveDockerContext", "fbColimaActive", "ftColimaVersion",
]


def fsActiveDockerContext():
    """Return the active Docker context name, or '' on any error."""
    try:
        resultProcess = subprocess.run(
            ["docker", "context", "show"],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return ""
    if resultProcess.returncode != 0:
        return ""
    return (resultProcess.stdout or "").strip()


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
        resultProcess = subprocess.run(
            ["colima", "version"],
            capture_output=True, text=True, timeout=5,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return ""
    if resultProcess.returncode != 0:
        return ""
    return resultProcess.stdout or ""


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
