"""Generate and validate hash-pinned Python dependency lockfiles.

Wraps ``uv pip compile --generate-hashes`` so each L3 archive deposit
ships a ``requirements.lock`` that pins every Python dependency by
exact version with SHA-256 hashes. Verifiers can then install the
pinned environment with ``pip install --require-hashes -r
requirements.lock`` without needing ``uv`` themselves.

The module exposes three orthogonal helpers: a generator
(``fnGenerateRequirementsLock``), a structural validator
(``flistVerifyRequirementsLock``), and a tooling probe
(``fbIsUvAvailable``).
"""

import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from vaibify.reproducibility.credentialRedactor import (
    fsRedactCredentials,
)
from vaibify.reproducibility.repoFiles import (
    ffilesEnsureRepoFiles,
    fsRepoRootOf,
)


__all__ = [
    "fnGenerateRequirementsLock",
    "flistVerifyRequirementsLock",
    "fbIsUvAvailable",
]


_S_UV_INSTALL_URL = (
    "https://docs.astral.sh/uv/getting-started/installation/"
)
_S_UV_MISSING_MESSAGE = (
    "uv was not found on PATH; install uv: " + _S_UV_INSTALL_URL
)
_S_LOCK_FILENAME = "requirements.lock"


def fbIsUvAvailable():
    """Return True iff the ``uv`` executable is on PATH."""
    return shutil.which("uv") is not None


def fnGenerateRequirementsLock(filesRepo):
    """Generate ``<repo>/requirements.lock`` via ``uv``.

    Selects the input source in priority order: ``pyproject.toml``
    first, then ``requirements.in``. Raises ``FileNotFoundError`` if
    ``uv`` is missing or neither input file exists. Surfaces uv
    failures as ``subprocess.CalledProcessError``.

    ``uv`` runs on the host. When the repo is a host directory, uv
    compiles in place. When the repo lives in a container, the input
    file is staged into a host temp directory, compiled there, and
    the resulting lockfile is written back through the adapter.
    """
    filesRepo = ffilesEnsureRepoFiles(filesRepo)
    if not fbIsUvAvailable():
        raise FileNotFoundError(_S_UV_MISSING_MESSAGE)
    sInput = _fsResolveLockInput(filesRepo)
    sLocalRoot = filesRepo.fsLocalRootOrNone()
    if sLocalRoot is not None:
        _fnRunUvCompile(Path(sLocalRoot), sInput)
        return
    _fnCompileLockViaStaging(filesRepo, sInput)


def _fnCompileLockViaStaging(filesRepo, sInput):
    """Compile the lock in a host temp directory; write back via adapter."""
    sInputContents = filesRepo.fsReadText(sInput)
    with tempfile.TemporaryDirectory() as sStagingDir:
        sStagedInput = os.path.join(sStagingDir, sInput)
        with open(sStagedInput, "w", encoding="utf-8") as fileHandle:
            fileHandle.write(sInputContents)
        _fnRunUvCompile(Path(sStagingDir), sInput)
        with open(
            os.path.join(sStagingDir, _S_LOCK_FILENAME),
            "r", encoding="utf-8",
        ) as fileHandle:
            sLockContents = fileHandle.read()
    filesRepo.fnWriteTextAtomic(_S_LOCK_FILENAME, sLockContents)


def _fsResolveLockInput(filesRepo):
    """Return the input filename uv should compile from."""
    if filesRepo.fbIsFile("pyproject.toml"):
        return "pyproject.toml"
    if filesRepo.fbIsFile("requirements.in"):
        return "requirements.in"
    raise FileNotFoundError(
        "No dependency input found in '"
        + fsRepoRootOf(filesRepo)
        + "'; expected pyproject.toml or requirements.in"
    )


def _flistBuildUvCompileCommand(sInput):
    """Return the ``uv pip compile`` argv for sInput."""
    return [
        "uv",
        "pip",
        "compile",
        "--generate-hashes",
        sInput,
        "-o",
        _S_LOCK_FILENAME,
    ]


def _fnRaiseUvTimeout(listCommand, errorTimeout):
    """Re-raise an uv timeout as a CalledProcessError with redacted stderr."""
    raise subprocess.CalledProcessError(
        124, listCommand,
        output="",
        stderr="uv pip compile timed out after "
        + f"{int(errorTimeout.timeout)}s",
    ) from None


def _fnRunUvCompile(pathRepo, sInput):
    """Invoke ``uv pip compile --generate-hashes`` in pathRepo.

    Surfaces uv failures as ``CalledProcessError`` with the captured
    stderr scrubbed of credentials so an index URL with embedded
    ``user:token@`` cannot leak. The ``FileNotFoundError`` arm guards
    against the rare race where ``uv`` disappears between
    :func:`fbIsUvAvailable` and the subprocess invocation.
    """
    listCommand = _flistBuildUvCompileCommand(sInput)
    try:
        completed = subprocess.run(
            listCommand,
            cwd=str(pathRepo),
            capture_output=True,
            text=True,
            timeout=120.0,
        )
    except FileNotFoundError:
        raise FileNotFoundError(_S_UV_MISSING_MESSAGE) from None
    except subprocess.TimeoutExpired as errorTimeout:
        _fnRaiseUvTimeout(listCommand, errorTimeout)
    if completed.returncode != 0:
        raise subprocess.CalledProcessError(
            completed.returncode,
            listCommand,
            output=completed.stdout,
            stderr=fsRedactCredentials(completed.stderr or ""),
        )


def flistVerifyRequirementsLock(filesRepo):
    """Return a list of structural issues with the lockfile.

    An empty list means the lockfile exists, parses, and every
    dependency entry carries at least one ``--hash=sha256:...`` line.
    This is a format-only check; actual install verification is the
    user's call to ``pip install --require-hashes``.
    """
    filesRepo = ffilesEnsureRepoFiles(filesRepo)
    sDisplayPath = os.path.join(fsRepoRootOf(filesRepo), _S_LOCK_FILENAME)
    if not filesRepo.fbIsFile(_S_LOCK_FILENAME):
        return [
            "requirements.lock not found at '" + sDisplayPath + "'"
        ]
    sContents = filesRepo.fsReadText(_S_LOCK_FILENAME)
    listEntries = _flistParseLockEntries(sContents)
    if not listEntries:
        return [
            "requirements.lock at '"
            + sDisplayPath
            + "' contains no dependency entries"
        ]
    return _flistFindUnhashedEntries(listEntries)


def _flistParseLockEntries(sContents):
    """Group lockfile lines into one block per dependency.

    A dependency block starts with a non-comment, non-indented line
    naming the package and continues across continuation lines (those
    starting with whitespace, ``--hash=``, or a backslash from the
    previous line).
    """
    listEntries = []
    listCurrent = []
    for sLine in sContents.splitlines():
        if not sLine.strip() or sLine.lstrip().startswith("#"):
            continue
        if sLine[:1].isspace() or sLine.lstrip().startswith("--hash"):
            listCurrent.append(sLine)
            continue
        if listCurrent:
            listEntries.append(listCurrent)
        listCurrent = [sLine]
    if listCurrent:
        listEntries.append(listCurrent)
    return listEntries


def _flistFindUnhashedEntries(listEntries):
    """Return issue strings for entries lacking a sha256 hash line."""
    listIssues = []
    for listLines in listEntries:
        sJoined = "\n".join(listLines)
        if "--hash=sha256:" not in sJoined:
            sName = listLines[0].split()[0]
            listIssues.append(
                "Entry '" + sName + "' has no --hash=sha256: line"
            )
    return listIssues
