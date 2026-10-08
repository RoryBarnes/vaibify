"""The marker writer's own bookkeeping must never reach a researcher's repository.

The marker is read-modify-written under a lock file beside it and lands
by renaming a per-writer temporary. Both are machine-local, and an
untracked file makes the reproduction export refuse, so the project's
auto-managed ``.vaibify/.gitignore`` has to name them. Every check here
asks real ``git`` after a real pytest session wrote the marker, because
a pattern that merely looks right (``test_markers/*.lock`` against a
file one directory deeper) ignores nothing.
"""

import os
import subprocess

import pytest

from tests import perTestMarkerHarness as harness
from vaibify.gui import stateManager


def _fsGit(sRepo, *listArguments):
    return subprocess.run(
        ["git", "-C", sRepo] + list(listArguments),
        capture_output=True, text=True, check=False,
    ).stdout


def _fsRepoWithIgnoreRulesAndOneMarkerRun(tmp_path):
    sRepo = harness.fsBuildProject(tmp_path, {"test_a.py": harness.S_PASS})
    subprocess.run(["git", "-C", sRepo, "init", "-q"], check=True)
    harness.fnWrite(
        os.path.join(sRepo, ".vaibify", ".gitignore"),
        stateManager.S_VAIBIFY_GITIGNORE_BODY)
    harness.fiRunPytest(sRepo, [])
    return sRepo


@pytest.mark.falsification
def testTheMarkerLockIsIgnoredByGit(tmp_path):
    """Kills: dropping ``test_markers/*/*.lock`` from the auto-managed ignore list."""
    sRepo = _fsRepoWithIgnoreRulesAndOneMarkerRun(tmp_path)
    sLockRelative = os.path.relpath(
        harness.fsMarkerPath(sRepo) + ".lock", sRepo)
    assert os.path.exists(os.path.join(sRepo, sLockRelative)), (
        "the session wrote no lock file, so this test checks nothing")
    assert _fsGit(sRepo, "check-ignore", sLockRelative).strip() == (
        sLockRelative)


@pytest.mark.falsification
def testTheMarkerTemporaryIsIgnoredByGit(tmp_path):
    """Kills: dropping ``test_markers/*/*.tmp`` from the auto-managed ignore list."""
    sRepo = _fsRepoWithIgnoreRulesAndOneMarkerRun(tmp_path)
    sTemporaryRelative = os.path.relpath(
        harness.fsMarkerPath(sRepo) + ".0123abcd.tmp", sRepo)
    assert _fsGit(sRepo, "check-ignore", sTemporaryRelative).strip() == (
        sTemporaryRelative)


def testOnlyTheMarkerItselfIsLeftUntracked(tmp_path):
    """The marker is the one canonical result, so it must stay visible to git."""
    sRepo = _fsRepoWithIgnoreRulesAndOneMarkerRun(tmp_path)
    sMarkerRelative = os.path.relpath(harness.fsMarkerPath(sRepo), sRepo)
    listUntracked = _fsGit(
        sRepo, "ls-files", "--others", "--exclude-standard",
    ).split()
    assert sMarkerRelative in listUntracked
    assert not [s for s in listUntracked if s.endswith((".lock", ".tmp"))]
