"""Tier 3 environment manifest capture for the AICS L3 envelope.

Captures the container image digest, host-binary hashes, and system
tool versions whose output bytes can affect bit-level reproducibility.
The resulting JSON document is written to
``<sProjectRepo>/.vaibify/environment.json`` and joins
``MANIFEST.sha256`` (Tier 1) and ``requirements.lock`` (Tier 2) to
form the AICS L3 verification envelope.
"""

import json
import platform
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from vaibify.reproducibility.provenanceTracker import fsComputeFileHash


__all__ = [
    "fdictCaptureContainerImageDigest",
    "fdictCaptureHostBinaryHashes",
    "fdictCaptureSystemTools",
    "fnWriteEnvironmentJson",
]


_SCHEMA_VERSION = "1"
_OS_RELEASE_PATH = "/etc/os-release"
_DOCKER_INSTALL_HINT = (
    "docker executable not found on PATH. Install Docker Desktop, "
    "Colima, or another Docker-compatible runtime."
)


# ------------------------------------------------------------------
# Container image digest
# ------------------------------------------------------------------


def fdictCaptureContainerImageDigest(sContainerName):
    """Return the image digest for a running container."""
    _fnEnsureDockerAvailable()
    saCommand = [
        "docker", "inspect",
        "--format", "{{.RepoDigests}}",
        sContainerName,
    ]
    sStdout = _fsRunCheckedCommand(saCommand)
    return {
        "sContainerName": sContainerName,
        "sImageDigest": _fsParseRepoDigests(sStdout),
    }


def _fnEnsureDockerAvailable():
    """Raise FileNotFoundError with install hint if docker is absent."""
    if shutil.which("docker") is None:
        raise FileNotFoundError(_DOCKER_INSTALL_HINT)


def _fsRunCheckedCommand(saCommand):
    """Run a subprocess command and return stripped stdout or raise.

    A 5-second timeout prevents a hung docker daemon from blocking
    the snapshot indefinitely; ``subprocess.TimeoutExpired`` is
    propagated so the caller (and ultimately the user) sees a clear
    actionable error rather than a silent hang.
    """
    resultProcess = subprocess.run(
        saCommand, capture_output=True, text=True, timeout=5.0,
    )
    if resultProcess.returncode != 0:
        raise subprocess.CalledProcessError(
            resultProcess.returncode,
            saCommand,
            output=resultProcess.stdout,
            stderr=resultProcess.stderr,
        )
    return resultProcess.stdout.strip()


def _fsParseRepoDigests(sRawOutput):
    """Extract the first ``image@sha256:...`` from docker's output."""
    sStripped = sRawOutput.strip().strip("[]").strip()
    if not sStripped:
        return None
    sFirst = sStripped.split()[0]
    if "@sha256:" not in sFirst:
        return None
    return sFirst


# ------------------------------------------------------------------
# Host binary hashes
# ------------------------------------------------------------------


def fdictCaptureHostBinaryHashes(listBinaryPaths):
    """Hash each host binary and capture its --version output."""
    listBinaries = []
    for sPath in listBinaryPaths:
        listBinaries.append(_fdictCaptureSingleBinary(sPath))
    return {"listBinaries": listBinaries}


def _fdictCaptureSingleBinary(sPath):
    """Return the hash and version metadata for one binary path."""
    if not Path(sPath).is_file():
        return {"sBinaryPath": sPath, "sSha256": None, "sVersion": None}
    return {
        "sBinaryPath": sPath,
        "sSha256": fsComputeFileHash(sPath),
        "sVersion": _fsCaptureBinaryVersion(sPath),
    }


def _fsCaptureBinaryVersion(sPath):
    """Return the first line of ``<sPath> --version`` or None on failure.

    A 5-second timeout protects against user-supplied binaries that
    open a TTY-prompt or wait on stdin — without it, the snapshot
    capture would hang forever on a misbehaving binary. Timeout is
    treated as a soft failure (returns None) so the SHA-256 is still
    captured by the caller.
    """
    try:
        resultProcess = subprocess.run(
            [sPath, "--version"], capture_output=True, text=True,
            timeout=5.0,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if resultProcess.returncode != 0:
        return None
    return _fsFirstLine(resultProcess.stdout) or _fsFirstLine(
        resultProcess.stderr
    )


def _fsFirstLine(sText):
    """Return the first non-empty stripped line of ``sText`` or None."""
    if not sText:
        return None
    for sLine in sText.splitlines():
        sStripped = sLine.strip()
        if sStripped:
            return sStripped
    return None


# ------------------------------------------------------------------
# System tools
# ------------------------------------------------------------------


def fdictCaptureSystemTools():
    """Capture versions of system tools that affect reproducibility."""
    return {
        "sPython": _fsSinglePythonVersion(),
        "sGcc": _fsCaptureGccVersion(),
        "sLibc": _fsCaptureLibcVersion(),
        "sOsRelease": _fsReadOsRelease(),
    }


def _fsSinglePythonVersion():
    """Return ``sys.version`` collapsed to a single line."""
    return " ".join(sys.version.split())


def _fsCaptureGccVersion():
    """Return the first line of ``gcc --version`` or None."""
    if shutil.which("gcc") is None:
        return None
    try:
        resultProcess = subprocess.run(
            ["gcc", "--version"], capture_output=True, text=True,
            timeout=5.0,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if resultProcess.returncode != 0:
        return None
    return _fsFirstLine(resultProcess.stdout)


def _fsCaptureLibcVersion():
    """Return ``platform.libc_ver()`` as ``"name version"`` or None."""
    try:
        tLibc = platform.libc_ver()
    except (OSError, ValueError):
        return None
    sName, sVersion = tLibc[0], tLibc[1]
    if not sName and not sVersion:
        return None
    return f"{sName} {sVersion}".strip()


def _fsReadOsRelease():
    """Return the contents of /etc/os-release or None if unavailable."""
    pathOsRelease = Path(_OS_RELEASE_PATH)
    if not pathOsRelease.is_file():
        return None
    try:
        return pathOsRelease.read_text()
    except OSError:
        return None


# ------------------------------------------------------------------
# Persistence
# ------------------------------------------------------------------


def fnWriteEnvironmentJson(sProjectRepo, dictEnvironment):
    """Persist the environment manifest under ``<repo>/.vaibify/``."""
    pathOutput = Path(sProjectRepo) / ".vaibify" / "environment.json"
    pathOutput.parent.mkdir(parents=True, exist_ok=True)
    dictPayload = _fdictAnnotateEnvironment(dictEnvironment)
    with open(pathOutput, "w") as fileHandle:
        json.dump(dictPayload, fileHandle, indent=2, sort_keys=True)


def _fdictAnnotateEnvironment(dictEnvironment):
    """Return a copy of ``dictEnvironment`` with timestamp + schema."""
    dictPayload = dict(dictEnvironment)
    dictPayload["sTimestamp"] = _fsCurrentTimestamp()
    dictPayload["sSchemaVersion"] = _SCHEMA_VERSION
    return dictPayload


def _fsCurrentTimestamp():
    """Return the current UTC time as an ISO 8601 string."""
    return datetime.now(timezone.utc).isoformat()
