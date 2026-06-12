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
import posixpath
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from vaibify.reproducibility.repoFiles import ffilesEnsureRepoFiles


__all__ = [
    "fbBinaryCaptured",
    "fbEnvironmentDigestPinned",
    "fdictCaptureContainerImageDigest",
    "fdictCaptureHostBinaryHashes",
    "fdictCaptureSingleBinary",
    "fdictCaptureSystemTools",
    "fdictReadEnvironmentJson",
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
    """Return the image digest for a running container.

    A container has no ``RepoDigests`` field of its own, so the
    capture resolves the container's image ID first (``{{.Image}}``)
    and then reads the *image's* ``RepoDigests``. A locally built
    image has no registry digest; its image ID — itself a sha256
    content digest — is recorded instead, with ``bLocalImageOnly``
    marking the local-only provenance honestly. Nothing is ever
    fabricated: when neither form is available the digest is None.
    """
    _fnEnsureDockerAvailable()
    sImageId = _fsInspectFormatValue(sContainerName, "{{.Image}}")
    sRepoDigest = None
    if sImageId:
        sRepoDigest = _fsParseRepoDigests(
            _fsInspectFormatValue(sImageId, "{{.RepoDigests}}"),
        )
    return _fdictBuildImageDigestEntry(
        sContainerName, sImageId, sRepoDigest,
    )


def _fsInspectFormatValue(sTarget, sFormat):
    """Run ``docker inspect --format sFormat sTarget``; return stdout."""
    return _fsRunCheckedCommand(
        ["docker", "inspect", "--format", sFormat, sTarget],
    )


def _fdictBuildImageDigestEntry(sContainerName, sImageId, sRepoDigest):
    """Assemble the digest entry, preferring the registry digest."""
    if sRepoDigest:
        return {
            "sContainerName": sContainerName,
            "sImageDigest": sRepoDigest,
            "bLocalImageOnly": False,
        }
    bImageIdUsable = _fbIsImageIdDigest(sImageId)
    return {
        "sContainerName": sContainerName,
        "sImageDigest": sImageId if bImageIdUsable else None,
        "bLocalImageOnly": bImageIdUsable,
    }


def _fbIsImageIdDigest(sImageId):
    """Return True iff sImageId is a ``sha256:<64 hex>`` content digest."""
    if not sImageId or not sImageId.startswith("sha256:"):
        return False
    sHexPart = sImageId[len("sha256:"):]
    return len(sHexPart) == 64 and all(
        sCharacter in "0123456789abcdef" for sCharacter in sHexPart
    )


def _fnEnsureDockerAvailable():
    """Raise FileNotFoundError with install hint if docker is absent."""
    if shutil.which("docker") is None:
        raise FileNotFoundError(_DOCKER_INSTALL_HINT)


def _fsRunCheckedCommand(saCommand):
    """Run a subprocess command and return stripped stdout or raise.

    A 30-second timeout prevents a hung docker daemon from blocking
    the snapshot indefinitely while still tolerating cold starts of
    Colima or Docker Desktop where ``docker inspect`` can take 3-8
    seconds before the VM is fully responsive;
    ``subprocess.TimeoutExpired`` is propagated so the caller (and
    ultimately the user) sees a clear actionable error rather than a
    silent hang.
    """
    resultProcess = subprocess.run(
        saCommand, capture_output=True, text=True, timeout=30.0,
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


def fdictCaptureHostBinaryHashes(filesRepo, listBinaryPaths=None):
    """Hash each declared binary and capture its --version output.

    Hashing and version capture run through the repo-file adapter so
    a container-rooted repo records the *container's* binaries — a
    host-side hash of a container path would silently capture the
    wrong (or no) file. The legacy single-argument call form
    ``fdictCaptureHostBinaryHashes(listBinaryPaths)`` maps to a host
    adapter so host-side callers keep their semantics.
    """
    if listBinaryPaths is None and isinstance(filesRepo, (list, tuple)):
        filesRepo, listBinaryPaths = None, filesRepo
    filesRepo = ffilesEnsureRepoFiles(filesRepo)
    dictHashes = filesRepo.fdictHashAbsolutePaths(list(listBinaryPaths))
    listBinaries = []
    for sPath in listBinaryPaths:
        listBinaries.append(_fdictBuildBinaryEntry(
            filesRepo, sPath, dictHashes.get(sPath),
        ))
    return {"listBinaries": listBinaries}


def fdictCaptureSingleBinary(filesRepo, sBinaryPath=None):
    """Return the hash and version metadata for one binary path.

    The legacy single-argument form
    ``fdictCaptureSingleBinary(sBinaryPath)`` maps to a host adapter.
    """
    if sBinaryPath is None and isinstance(filesRepo, str):
        filesRepo, sBinaryPath = None, filesRepo
    filesRepo = ffilesEnsureRepoFiles(filesRepo)
    dictHashes = filesRepo.fdictHashAbsolutePaths([sBinaryPath])
    return _fdictBuildBinaryEntry(
        filesRepo, sBinaryPath, dictHashes.get(sBinaryPath),
    )


def _fdictBuildBinaryEntry(filesRepo, sBinaryPath, sSha256):
    """Assemble one binary entry; version capture only when hashable."""
    if not sSha256:
        return {
            "sBinaryPath": sBinaryPath,
            "sSha256": None,
            "sVersion": None,
        }
    return {
        "sBinaryPath": sBinaryPath,
        "sSha256": sSha256,
        "sVersion": _fsExecBinaryVersion(filesRepo, sBinaryPath),
    }


def fbBinaryCaptured(dictEnv, sBinaryPath):
    """Return True iff ``dictEnv`` records ``sBinaryPath`` with a real hash.

    Looks at ``dictHostBinaries.listBinaries`` first (the canonical
    layout written by the L3 envelope) and falls back to the top-level
    ``listBinaries`` shape produced by ``fdictCaptureHostBinaryHashes``
    so callers that pass the raw capture dict still work. An entry
    whose ``sSha256`` is missing or empty does NOT count as captured:
    a null-hash record proves nothing about the binary's identity, so
    the ``binary-not-captured`` blocker stays up until a real hash
    exists.
    """
    if not isinstance(dictEnv, dict) or not sBinaryPath:
        return False
    listBinaries = _flistResolveCapturedBinaries(dictEnv)
    for dictEntry in listBinaries:
        if not isinstance(dictEntry, dict):
            continue
        if dictEntry.get("sBinaryPath") != sBinaryPath:
            continue
        sSha256 = dictEntry.get("sSha256")
        if isinstance(sSha256, str) and sSha256:
            return True
    return False


def _flistResolveCapturedBinaries(dictEnv):
    """Return the ``listBinaries`` list from either supported layout."""
    dictHost = dictEnv.get("dictHostBinaries")
    if isinstance(dictHost, dict):
        listNested = dictHost.get("listBinaries")
        if isinstance(listNested, list):
            return listNested
    listTop = dictEnv.get("listBinaries")
    if isinstance(listTop, list):
        return listTop
    return []


def _fsExecBinaryVersion(filesRepo, sPath):
    """Return the first line of ``<sPath> --version`` or None on failure.

    Runs through the adapter so a container-rooted repo probes the
    container's binary. The adapter's 5-second timeout protects
    against user-supplied binaries that open a TTY-prompt or wait on
    stdin; timeout is a soft failure (returns None) so the SHA-256 is
    still captured by the caller. ``sPath`` that is empty or ``None``
    is rejected up-front so a corrupt config never builds a command
    with a missing first argument.
    """
    if not sPath:
        return None
    iExitCode, sStdout, sStderr = filesRepo.ftRunCommand(
        [sPath, "--version"], 5.0,
    )
    if iExitCode != 0:
        return None
    return _fsFirstLine(sStdout) or _fsFirstLine(sStderr)


def _fsCaptureBinaryVersion(sPath):
    """Host-adapter shim preserving the historical version-probe API."""
    return _fsExecBinaryVersion(ffilesEnsureRepoFiles(None), sPath)


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


def fdictCaptureSystemTools(filesRepo=None):
    """Capture versions of system tools that affect reproducibility.

    When ``filesRepo`` is a container-rooted adapter, the python, gcc,
    and os-release probes run *inside* the container so the recorded
    values describe the environment that actually produced the
    outputs. Host-rooted adapters (and the legacy no-argument call)
    keep the historical host probes.
    """
    filesRepo = ffilesEnsureRepoFiles(filesRepo)
    bContainerRooted = (
        filesRepo.fsLocalRootOrNone() is None
        and bool(getattr(filesRepo, "sRootPath", ""))
    )
    if bContainerRooted:
        return _fdictCaptureContainerSystemTools(filesRepo)
    return {
        "sPython": _fsSinglePythonVersion(),
        "sGcc": _fsCaptureGccVersion(),
        "sLibc": _fsCaptureLibcVersion(),
        "sOsRelease": _fsReadOsRelease(),
    }


def _fdictCaptureContainerSystemTools(filesRepo):
    """Probe python/gcc/os-release through the adapter's command runner."""
    _iCode, sPython, _sErr = filesRepo.ftRunCommand(
        ["python3", "-c", "import sys;print(' '.join(sys.version.split()))"],
        5.0,
    )
    iGccCode, sGcc, _sGccErr = filesRepo.ftRunCommand(
        ["gcc", "--version"], 5.0,
    )
    iOsCode, sOsRelease, _sOsErr = filesRepo.ftRunCommand(
        ["cat", _OS_RELEASE_PATH], 5.0,
    )
    return {
        "sPython": _fsFirstLine(sPython),
        "sGcc": _fsFirstLine(sGcc) if iGccCode == 0 else None,
        "sLibc": None,
        "sOsRelease": sOsRelease if iOsCode == 0 else None,
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


def fnWriteEnvironmentJson(filesRepo, dictEnvironment):
    """Persist the environment manifest under ``<repo>/.vaibify/``.

    The adapter's atomic write (sibling temp file + rename) ensures a
    crash mid-write cannot leave a half-written ``environment.json``
    that downstream parsers would reject.
    """
    filesRepo = ffilesEnsureRepoFiles(filesRepo)
    dictPayload = _fdictAnnotateEnvironment(dictEnvironment)
    filesRepo.fnWriteJsonAtomic(
        _fsEnvironmentRelativePath(), dictPayload,
    )


def _fsEnvironmentRelativePath():
    """Return the repo-relative path of environment.json."""
    return posixpath.join(".vaibify", "environment.json")


def _fdictAnnotateEnvironment(dictEnvironment):
    """Return a copy of ``dictEnvironment`` with timestamp + schema."""
    dictPayload = dict(dictEnvironment)
    dictPayload["sTimestamp"] = _fsCurrentTimestamp()
    dictPayload["sSchemaVersion"] = _SCHEMA_VERSION
    return dictPayload


def _fsCurrentTimestamp():
    """Return the current UTC time as an ISO 8601 string."""
    return datetime.now(timezone.utc).isoformat()


# ------------------------------------------------------------------
# Digest-form validation (consumed by AICS L3 readiness gate)
# ------------------------------------------------------------------


def fdictReadEnvironmentJson(filesRepo):
    """Return the parsed ``.vaibify/environment.json`` or ``None``.

    A missing file, malformed JSON, or non-dict top-level all return
    ``None`` so the L3 readiness gate can treat all three as
    "envelope not coherent yet". Callers that need to distinguish
    causes should call this and then re-read the file themselves.
    """
    filesRepo = ffilesEnsureRepoFiles(filesRepo)
    sRelPath = _fsEnvironmentRelativePath()
    if not filesRepo.fbIsFile(sRelPath):
        return None
    try:
        dictPayload = json.loads(filesRepo.fsReadText(sRelPath))
    except (OSError, ValueError):
        return None
    if not isinstance(dictPayload, dict):
        return None
    return dictPayload


def fbEnvironmentDigestPinned(filesRepo):
    """Return True iff environment.json records a sha256 content digest.

    The schema places the digest at either the top-level
    ``sImageDigest`` (legacy layout) or at
    ``dictContainer.sImageDigest`` (the layout the AICS L3 envelope
    writes). Two forms pin honestly: a registry digest
    (``image@sha256:<hex>``) or a locally built image's ID
    (``sha256:<64 hex>``), which is itself a content digest. A
    floating tag (``image:latest``) fails the check honestly even
    though docker would accept it.
    """
    dictPayload = fdictReadEnvironmentJson(filesRepo)
    if dictPayload is None:
        return False
    sDigest = _fsExtractImageDigest(dictPayload)
    if not sDigest:
        return False
    return "@sha256:" in sDigest or _fbIsImageIdDigest(sDigest)


def _fsExtractImageDigest(dictPayload):
    """Return the image-digest string from either supported layout."""
    dictContainer = dictPayload.get("dictContainer")
    if isinstance(dictContainer, dict):
        sNested = dictContainer.get("sImageDigest") or ""
        if sNested:
            return sNested
    return dictPayload.get("sImageDigest") or ""
