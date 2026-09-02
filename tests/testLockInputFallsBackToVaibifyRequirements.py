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


@pytest.mark.skipif(
    not flistResolveLockCompileCommand(),
    reason="no hashed-lockfile generator installed on this host",
)
def test_a_subdirectory_input_compiles_through_the_staging_path(tmp_path):
    """End-to-end: .vaibify/requirements.txt produces a hashed lock.

    Driven through the real compiler rather than a stub, because the
    defect being guarded is a filesystem-layout mistake in staging --
    a stubbed compiler would never open the file and would pass
    against the broken join.
    """
    (tmp_path / ".vaibify").mkdir()
    (tmp_path / ".vaibify" / "requirements.txt").write_text(
        "packaging>=23.0\n",
    )
    filesRepo = ContainerLikeRepoFiles(tmp_path)
    fnGenerateRequirementsLock(filesRepo)
    sLock = filesRepo.dictWritten["requirements.lock"]
    assert "--hash=sha256:" in sLock
    assert "packaging" in sLock.lower()
