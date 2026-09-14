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
    "T_LOCK_INPUT_CANDIDATES",
    "S_VAIBIFY_REQUIREMENTS_PATH",
    "fnGenerateRequirementsLock",
    "flistConstraintPinsFromFreeze",
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
# The per-repo dependency file vaibify's own container entrypoint
# installs on startup, and the one the container docs tell researchers
# to maintain.
S_VAIBIFY_REQUIREMENTS_PATH = ".vaibify/requirements.txt"


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
    """Compile the lock in a host temp directory; write back via adapter.

    The compile is CONSTRAINED to what the container runs. The
    resolver lives on the host, and left to itself it pins whatever
    the host's own interpreter would install today: a laptop with
    Python 3.10 beside a container with 3.12 wrote a lock naming
    numpy 2.2.6 for a container running 2.5.2, and the lock described
    neither the container nor the author's environment
    (researcher-measured, 2026-09-13). So the container is asked for
    its interpreter, its architecture and its installed set, and the
    resolver is told all three: the installed set as constraints (a
    constraint binds only the packages the input reaches, so the
    lock stays the declared closure rather than every tool in the
    image), the interpreter version and platform as the target. A
    container that cannot be asked refuses rather than compiling
    unconstrained, because an unconstrained lock is the defect.
    """
    sInputContents = filesRepo.fsReadText(sInput)
    dictInstalled = _fdictProbeInstalledEnvironment(filesRepo)
    # Staged FLAT, under the basename only. A candidate may live in a
    # subdirectory (``.vaibify/requirements.txt``), and joining the
    # relative path onto the staging root would write into a directory
    # that does not exist there. The basename is what every supported
    # compiler dispatches on -- ``pyproject.toml`` keeps its meaning,
    # and a requirements file is read the same way from any directory.
    sStagedName = os.path.basename(sInput)
    with tempfile.TemporaryDirectory() as sStagingDir:
        sStagedInput = os.path.join(sStagingDir, sStagedName)
        with open(sStagedInput, "w", encoding="utf-8") as fileHandle:
            fileHandle.write(sInputContents)
        with open(
            os.path.join(sStagingDir, _S_CONSTRAINTS_FILENAME),
            "w", encoding="utf-8",
        ) as fileHandle:
            fileHandle.write(
                "".join(sLine + "\n" for sLine in dictInstalled["listPins"]),
            )
        _fnRunLockCompile(
            Path(sStagingDir), sStagedName, listCompilePrefix, dictInstalled,
        )
        with open(
            os.path.join(sStagingDir, _S_LOCK_FILENAME),
            "r", encoding="utf-8",
        ) as fileHandle:
            sLockContents = fileHandle.read()
    filesRepo.fnWriteTextAtomic(_S_LOCK_FILENAME, sLockContents)


_S_CONSTRAINTS_FILENAME = "constraints.txt"
_F_PROBE_TIMEOUT_SECONDS = 60.0
_S_PROBE_INTERPRETER = (
    "import platform, sys; "
    "print(sys.version_info[0], sys.version_info[1], platform.machine())"
)
_DICT_UV_PLATFORM_BY_MACHINE = {
    "x86_64": "x86_64-unknown-linux-gnu",
    "amd64": "x86_64-unknown-linux-gnu",
    "aarch64": "aarch64-unknown-linux-gnu",
    "arm64": "aarch64-unknown-linux-gnu",
}


def _fdictProbeInstalledEnvironment(filesRepo):
    """Ask the container what runs there: interpreter, machine, installed set.

    Returns ``{"sPythonVersion", "sMachine", "listPins"}``. A probe that
    fails raises ``CalledProcessError`` naming it, so the lock tier
    reports the reason and writes nothing -- never a lock compiled
    against the host's own answer to a question about the container.
    """
    iExitCode, sVersionLine, sError = filesRepo.ftRunCommand(
        ["python3", "-c", _S_PROBE_INTERPRETER], _F_PROBE_TIMEOUT_SECONDS,
    )
    listFields = (sVersionLine or "").split()
    if iExitCode != 0 or len(listFields) != 3:
        raise subprocess.CalledProcessError(
            iExitCode or 1, ["python3", "-c", _S_PROBE_INTERPRETER],
            output=sVersionLine or "",
            stderr="the container's interpreter could not be asked its "
            "version and architecture: " + fsRedactCredentials(sError or ""),
        )
    iExitCode, sFreeze, sError = filesRepo.ftRunCommand(
        ["python3", "-m", "pip", "freeze", "--exclude-editable"],
        _F_PROBE_TIMEOUT_SECONDS,
    )
    if iExitCode != 0:
        raise subprocess.CalledProcessError(
            iExitCode, ["python3", "-m", "pip", "freeze"],
            output=sFreeze or "",
            stderr="the container's installed packages could not be "
            "listed: " + fsRedactCredentials(sError or ""),
        )
    return {
        "sPythonVersion": listFields[0] + "." + listFields[1],
        "sMachine": listFields[2],
        "listPins": flistConstraintPinsFromFreeze(sFreeze or ""),
    }


def flistConstraintPinsFromFreeze(sFreeze):
    """Return the ``name==version`` lines of a ``pip freeze``, nothing else.

    A freeze can carry ``-e`` editables, ``name @ file://`` direct
    references and comments; none of those is a version a resolver can
    hold a package to, and a constraints file that names one fails the
    whole compile.
    """
    listPins = []
    for sLine in sFreeze.splitlines():
        sStripped = sLine.strip()
        if not sStripped or sStripped.startswith(("#", "-")):
            continue
        if " @ " in sStripped or "==" not in sStripped:
            continue
        listPins.append(sStripped)
    return listPins


def _flistTargetFlagsForCompiler(listCompilePrefix, dictInstalled):
    """Return the interpreter/platform flags the compiler understands.

    Only uv takes a target; pip-tools resolves for the interpreter it
    runs under and gets the constraints alone. An architecture uv has
    no name for is left unnamed rather than guessed.
    """
    if "uv" not in listCompilePrefix:
        return []
    listFlags = ["--python-version", dictInstalled["sPythonVersion"]]
    sPlatform = _DICT_UV_PLATFORM_BY_MACHINE.get(
        (dictInstalled.get("sMachine") or "").lower(),
    )
    if sPlatform:
        listFlags.extend(["--python-platform", sPlatform])
    return listFlags


T_LOCK_INPUT_CANDIDATES = (
    "pyproject.toml",
    "requirements.in",
    "requirements.txt",
    S_VAIBIFY_REQUIREMENTS_PATH,
)


def _fsResolveLockInput(filesRepo):
    """Return the input filename uv should compile from.

    ``requirements.txt`` is a fallback because research repos commonly
    declare loose dependencies there without adopting pyproject.toml
    or the pip-tools ``.in`` convention, and every supported compiler
    accepts it as input. The lock output is always
    ``requirements.lock``, so compiling *from* requirements.txt is
    unambiguous.

    ``.vaibify/requirements.txt`` is last and is the one vaibify
    itself tells researchers to maintain -- the entrypoint installs it
    on container startup. Probing only the repo root meant the
    documented dependency file had no connection to the file this
    tier compiles, so a project that followed the documented workflow
    exactly could never turn the L3 dependency row green, and the
    tier said nothing about why.
    """
    for sCandidate in T_LOCK_INPUT_CANDIDATES:
        if filesRepo.fbIsFile(sCandidate):
            return sCandidate
    raise FileNotFoundError(
        "No dependency input found in '"
        + fsRepoRootOf(filesRepo)
        + "'; expected one of "
        + ", ".join(T_LOCK_INPUT_CANDIDATES)
    )


def _flistBuildLockCompileCommand(listCompilePrefix, sInput, dictInstalled=None):
    """Return the full hash-pinning compile argv for sInput.

    With ``dictInstalled`` (the container lane) the argv carries the
    constraints file and, for uv, the target interpreter and platform,
    so the resolver pins what the container runs. Without it (a host
    clone compiled in place) the resolver's own interpreter is the one
    the steps run under, and no target is named.
    """
    listCommand = list(listCompilePrefix) + ["--generate-hashes", sInput]
    if dictInstalled is not None:
        listCommand.extend(["-c", _S_CONSTRAINTS_FILENAME])
        listCommand.extend(
            _flistTargetFlagsForCompiler(listCompilePrefix, dictInstalled),
        )
    return listCommand + ["-o", _S_LOCK_FILENAME]


def _fnRaiseLockCompileTimeout(listCommand, errorTimeout):
    """Re-raise a compile timeout as a CalledProcessError."""
    raise subprocess.CalledProcessError(
        124, listCommand,
        output="",
        stderr="lockfile compile timed out after "
        + f"{int(errorTimeout.timeout)}s",
    ) from None


def _fnRunLockCompile(pathRepo, sInput, listCompilePrefix, dictInstalled=None):
    """Invoke the resolved hash-pinning compiler in pathRepo.

    Surfaces compile failures as ``CalledProcessError`` with the
    captured stderr scrubbed of credentials so an index URL with
    embedded ``user:token@`` cannot leak. The ``FileNotFoundError``
    arm guards against the rare race where the generator disappears
    between :func:`flistResolveLockCompileCommand` and the
    subprocess invocation.
    """
    listCommand = _flistBuildLockCompileCommand(
        listCompilePrefix, sInput, dictInstalled,
    )
    try:
        processCompleted = subprocess.run(
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
    if processCompleted.returncode != 0:
        raise subprocess.CalledProcessError(
            processCompleted.returncode,
            listCommand,
            output=processCompleted.stdout,
            stderr=fsRedactCredentials(processCompleted.stderr or ""),
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
