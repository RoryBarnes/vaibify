"""Generate and validate hash-pinned Python dependency lockfiles.

Wraps ``uv pip compile --generate-hashes`` (or, when ``uv`` is not
installed, ``python -m uv`` or pip-tools' ``python -m piptools
compile --generate-hashes``) so each L3 archive deposit ships a
``requirements.lock`` that pins every Python dependency by exact
version with SHA-256 hashes. Verifiers can then install the pinned
environment with ``pip install --require-hashes -r
requirements.lock`` without needing the generator themselves.

The module exposes four orthogonal helpers: a generator
(``fnGenerateRequirementsLock``), a structural validator
(``flistVerifyRequirementsLock``), a tooling probe
(``fbIsUvAvailable``), and a generator-command resolver
(``flistResolveLockCompileCommand``) whose empty result means no
hashed lock is producible on this host.
"""

import importlib.util
import os
import shutil
import subprocess
import sys
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
    "flistResolveLockCompileCommand",
    "S_LOCK_TOOL_INSTALL_HINT",
]


_S_UV_INSTALL_URL = (
    "https://docs.astral.sh/uv/getting-started/installation/"
)
S_LOCK_TOOL_INSTALL_HINT = (
    "Install uv (" + _S_UV_INSTALL_URL + ") or pip-tools "
    "(python3 -m pip install pip-tools), then regenerate the "
    "reproducibility envelope."
)
_S_LOCK_TOOL_MISSING_MESSAGE = (
    "No hashed-lockfile generator found: uv is not on PATH, the uv "
    "module is not importable, and pip-tools is not installed. "
    + S_LOCK_TOOL_INSTALL_HINT
)
_S_LOCK_FILENAME = "requirements.lock"


def fbIsUvAvailable():
    """Return True iff the ``uv`` executable is on PATH."""
    return shutil.which("uv") is not None


def _fbModuleAvailable(sModuleName):
    """Return True iff ``sModuleName`` is importable in this interpreter."""
    try:
        return importlib.util.find_spec(sModuleName) is not None
    except (ImportError, ValueError):
        return False


def flistResolveLockCompileCommand():
    """Return the argv prefix of the first available lock generator.

    Probes in priority order: the ``uv`` executable on PATH, the
    ``uv`` Python module (``python -m uv``), then pip-tools
    (``python -m piptools compile``). Every candidate produces a
    hash-pinned lockfile via ``--generate-hashes``. Returns an empty
    list when no generator is available, so callers can surface the
    gap instead of fabricating an unhashed lock.
    """
    if fbIsUvAvailable():
        return ["uv", "pip", "compile"]
    if _fbModuleAvailable("uv"):
        return [sys.executable, "-m", "uv", "pip", "compile"]
    if _fbModuleAvailable("piptools"):
        return [sys.executable, "-m", "piptools", "compile"]
    return []


def fnGenerateRequirementsLock(filesRepo):
    """Generate ``<repo>/requirements.lock`` with hash pins.

    Selects the input source in priority order: ``pyproject.toml``
    first, then ``requirements.in``. Raises ``FileNotFoundError`` if
    no lock generator is installed (the message names what to
    install) or neither input file exists. Surfaces compile failures
    as ``subprocess.CalledProcessError``.

    The generator runs on the host. When the repo is a host
    directory, it compiles in place. When the repo lives in a
    container, the input file is staged into a host temp directory,
    compiled there, and the resulting lockfile is written back
    through the adapter.
    """
    filesRepo = ffilesEnsureRepoFiles(filesRepo)
    listCompilePrefix = flistResolveLockCompileCommand()
    if not listCompilePrefix:
        raise FileNotFoundError(_S_LOCK_TOOL_MISSING_MESSAGE)
    sInput = _fsResolveLockInput(filesRepo)
    sLocalRoot = filesRepo.fsLocalRootOrNone()
    if sLocalRoot is not None:
        _fnRunLockCompile(Path(sLocalRoot), sInput, listCompilePrefix)
        return
    _fnCompileLockViaStaging(filesRepo, sInput, listCompilePrefix)


def _fnCompileLockViaStaging(filesRepo, sInput, listCompilePrefix):
    """Compile the lock in a host temp directory; write back via adapter."""
    sInputContents = filesRepo.fsReadText(sInput)
    with tempfile.TemporaryDirectory() as sStagingDir:
        sStagedInput = os.path.join(sStagingDir, sInput)
        with open(sStagedInput, "w", encoding="utf-8") as fileHandle:
            fileHandle.write(sInputContents)
        _fnRunLockCompile(Path(sStagingDir), sInput, listCompilePrefix)
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


def _flistBuildLockCompileCommand(listCompilePrefix, sInput):
    """Return the full hash-pinning compile argv for sInput."""
    return list(listCompilePrefix) + [
        "--generate-hashes",
        sInput,
        "-o",
        _S_LOCK_FILENAME,
    ]


def _fnRaiseLockCompileTimeout(listCommand, errorTimeout):
    """Re-raise a compile timeout as a CalledProcessError."""
    raise subprocess.CalledProcessError(
        124, listCommand,
        output="",
        stderr="lockfile compile timed out after "
        + f"{int(errorTimeout.timeout)}s",
    ) from None


def _fnRunLockCompile(pathRepo, sInput, listCompilePrefix):
    """Invoke the resolved hash-pinning compiler in pathRepo.

    Surfaces compile failures as ``CalledProcessError`` with the
    captured stderr scrubbed of credentials so an index URL with
    embedded ``user:token@`` cannot leak. The ``FileNotFoundError``
    arm guards against the rare race where the generator disappears
    between :func:`flistResolveLockCompileCommand` and the
    subprocess invocation.
    """
    listCommand = _flistBuildLockCompileCommand(listCompilePrefix, sInput)
    try:
        completed = subprocess.run(
            listCommand,
            cwd=str(pathRepo),
            capture_output=True,
            text=True,
            timeout=120.0,
        )
    except FileNotFoundError:
        raise FileNotFoundError(_S_LOCK_TOOL_MISSING_MESSAGE) from None
    except subprocess.TimeoutExpired as errorTimeout:
        _fnRaiseLockCompileTimeout(listCommand, errorTimeout)
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
