"""The lock tier must compile from the file vaibify tells users to keep.

Tier 2 probed ``pyproject.toml``, ``requirements.in`` and a repo-root
``requirements.txt``. The file the container docs instruct researchers
to maintain -- and that the entrypoint installs on startup -- is
``<repo>/.vaibify/requirements.txt``, one directory away and in none of
those. So a project that followed the documented workflow exactly
could never turn the L3 dependency row green, and the tier reported
the miss only as a flag that stayed false.

The staging half matters as much as the probe: a candidate in a
subdirectory cannot be joined onto the staging root, because that
directory does not exist there.
"""

import pytest

from vaibify.reproducibility.dependencyPinning import (
    S_VAIBIFY_REQUIREMENTS_PATH,
    T_LOCK_INPUT_CANDIDATES,
    _fsResolveLockInput,
    flistResolveLockCompileCommand,
    fnGenerateRequirementsLock,
)
from vaibify.reproducibility.repoFiles import HostRepoFiles


def test_vaibify_requirements_is_a_recognised_lock_input(tmp_path):
    """The documented dependency file resolves as a compile source."""
    (tmp_path / ".vaibify").mkdir()
    (tmp_path / ".vaibify" / "requirements.txt").write_text("numpy>=1.26\n")
    filesRepo = HostRepoFiles(str(tmp_path))
    assert _fsResolveLockInput(filesRepo) == S_VAIBIFY_REQUIREMENTS_PATH


def test_repo_root_declarations_still_win(tmp_path):
    """Precedence is unchanged; the new candidate is last, not first.

    A repo carrying both must keep compiling from the root file --
    that is the declaration a Python packager reads, and silently
    preferring the vaibify one would change what a working project
    locks.
    """
    (tmp_path / ".vaibify").mkdir()
    (tmp_path / ".vaibify" / "requirements.txt").write_text("numpy>=1.26\n")
    (tmp_path / "requirements.in").write_text("scipy>=1.11\n")
    filesRepo = HostRepoFiles(str(tmp_path))
    assert _fsResolveLockInput(filesRepo) == "requirements.in"
    assert T_LOCK_INPUT_CANDIDATES[-1] == S_VAIBIFY_REQUIREMENTS_PATH


def test_missing_input_error_names_every_candidate(tmp_path):
    """The refusal must say what to create, including the vaibify path.

    This message was the only thing standing between a researcher and
    an unexplained false flag, and it did not name the file vaibify
    had told them to maintain.
    """
    filesRepo = HostRepoFiles(str(tmp_path))
    with pytest.raises(FileNotFoundError) as errorInfo:
        _fsResolveLockInput(filesRepo)
    for sCandidate in T_LOCK_INPUT_CANDIDATES:
        assert sCandidate in str(errorInfo.value)


class ContainerLikeRepoFiles:
    """A repo adapter with no host root, forcing the staging path.

    ``fsLocalRootOrNone`` returning None is what makes
    ``fnGenerateRequirementsLock`` stage into a host temp directory
    and write the result back through the adapter -- the container
    lane. Compiling in place would never exercise the subdirectory
    bug this test exists for.
    """

    def __init__(self, pathRoot):
        self.pathRoot = pathRoot
        self.dictWritten = {}

    def fsLocalRootOrNone(self):
        return None

    def fbIsFile(self, sRelPath):
        return (self.pathRoot / sRelPath).is_file()

    def fsReadText(self, sRelPath):
        return (self.pathRoot / sRelPath).read_text()

    def fnWriteTextAtomic(self, sRelPath, sText):
        self.dictWritten[sRelPath] = sText

    def ftRunCommand(self, saCommand, fTimeoutSeconds):
        """Answer the container probes from THIS host's interpreter.

        The staging compile asks the container what it runs; here the
        host stands in for the container, so the lock the real
        compiler writes must pin the versions this host has installed.
        """
        import subprocess
        import sys
        processResult = subprocess.run(
            [sys.executable, *saCommand[1:]], capture_output=True, text=True,
            timeout=fTimeoutSeconds,
        )
        return processResult.returncode, processResult.stdout, processResult.stderr


def _fsInstalledVersionOrNone(sDistribution):
    try:
        from importlib.metadata import version
        return version(sDistribution)
    except Exception:  # noqa: BLE001 -- absent is the only other answer
        return None


@pytest.mark.skipif(
    not flistResolveLockCompileCommand(),
    reason="no hashed-lockfile generator installed on this host",
)
def test_a_subdirectory_input_compiles_through_the_staging_path(tmp_path):
    """End-to-end: .vaibify/requirements.txt produces a hashed lock that
    pins the version the (stand-in) container has installed.

    Driven through the real compiler rather than a stub, because the
    defect being guarded is a filesystem-layout mistake in staging --
    a stubbed compiler would never open the file and would pass
    against the broken join. The pin assertion is the second guard:
    the resolver is constrained to the installed set, so the lock must
    name this host's version, not the newest on the index.

    The probe package is one the host actually PINS. The constraint
    lane can only hold a package that ``pip freeze`` reports as a pip
    pin (``name==version``); a package the host's own manager installed
    as a direct reference (conda records ``name @ file://.../work``)
    is correctly excluded from the constraints and so resolves to the
    newest on the index. Assuming ``packaging`` is always pinnable made
    this fail on a conda host, where ``packaging`` is conda-managed --
    so the probe is chosen from the host's pinnable set instead.
    """
    sName, sVersion = _tResolvePinnableProbePackage()
    if sName is None:
        pytest.skip("no pip-pinnable package is installed to constrain against")
    (tmp_path / ".vaibify").mkdir()
    (tmp_path / ".vaibify" / "requirements.txt").write_text(sName + "\n")
    filesRepo = ContainerLikeRepoFiles(tmp_path)
    fnGenerateRequirementsLock(filesRepo)
    sLock = filesRepo.dictWritten["requirements.lock"]
    assert "--hash=sha256:" in sLock
    assert (_fsCanonicalPackageName(sName), sVersion) in _flistLockPins(sLock), (
        f"the lock must pin the constrained {sName}=={sVersion}, not the "
        f"newest on the index: {sLock}")


def _tResolvePinnableProbePackage():
    """Return ``(name, version)`` for a package THIS host pins, or ``(None, None)``.

    Reads the host's freeze through the same filter the production code
    uses, so the probe is exactly a package the constraint lane can
    hold. Prefers dependency-light, pure-Python names so the real
    compile stays quick; ``packaging`` is first, keeping a pip-based
    host (CI) resolving the same package it always has.
    """
    import subprocess
    import sys
    from vaibify.reproducibility.dependencyPinning import (
        flistConstraintPinsFromFreeze,
    )
    processResult = subprocess.run(
        [sys.executable, "-m", "pip", "freeze", "--exclude-editable"],
        capture_output=True, text=True,
    )
    dictPins = {}
    for sPin in flistConstraintPinsFromFreeze(processResult.stdout or ""):
        sPinName, _, sPinVersion = sPin.partition("==")
        dictPins[_fsCanonicalPackageName(sPinName)] = (sPinName, sPinVersion)
    for sPreferred in (
        "packaging", "typing-extensions", "zstandard", "iniconfig",
        "six", "wheel", "toml",
    ):
        if sPreferred in dictPins:
            return dictPins[sPreferred]
    return (None, None)


def _fsCanonicalPackageName(sName):
    """Return a package name normalized for comparison (lowercase, hyphens)."""
    return sName.strip().lower().replace("_", "-")


def _flistLockPins(sLock):
    """Return ``(canonical-name, version)`` for every pin line in a lock."""
    listPins = []
    for sLine in sLock.splitlines():
        sStripped = sLine.strip()
        if sStripped.startswith("#") or "==" not in sStripped:
            continue
        sLeft, _, sRight = sStripped.partition("==")
        listPins.append(
            (_fsCanonicalPackageName(sLeft), sRight.split()[0].strip()))
    return listPins
