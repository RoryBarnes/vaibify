"""Mutation-coverage tests for vaibify.reproducibility.repoFiles.

These close a single coverage hole: the symlink-containment guard in all
three RepoFiles adapters compares the resolved target against the repo
root with an ``os.sep`` boundary
(``not sCandidateReal.startswith(sRepoReal + os.sep)``). Dropping the
``+ os.sep`` turns the check into a bare string-prefix match, so a
symlink resolving into a SIBLING directory whose absolute path merely
shares the repo-root string (root ``.../repo``, target
``.../repo-evil/loot.txt``) would be treated as in-root, opened,
hashed, and pinned into the manifest -- a CWE-22 prefix-match defect on
an integrity boundary.

The existing suite only covers a sibling named ``outside.txt`` (which
does not share the ``repo`` prefix), so it cannot catch the mutation.
Each test here uses a sibling whose basename begins with the root's
basename. The same boundary is duplicated in ``HostRepoFiles``, the
container ``_S_HASH_SCRIPT``, and the snapshot ``_S_SNAPSHOT_SCRIPT``;
one test per copy guards all three.
"""

import os
import subprocess
from types import SimpleNamespace

import pytest

from vaibify.reproducibility.repoFiles import (
    ContainerRepoFiles,
    HostRepoFiles,
    SnapshotRepoFiles,
)

pytestmark = pytest.mark.falsification


class _FakeExecDockerConnection:
    """Docker connection that runs embedded scripts in a real host shell.

    The "container" filesystem is the host tmp tree the adapter is
    rooted at, so the embedded python hash/snapshot scripts execute for
    real against the symlink the test created.
    """

    def texecRunInContainerStreamed(
        self, sContainerId, sCommand, sWorkdir=None, sUser=None,
    ):
        resultProcess = subprocess.run(
            ["bash", "-c", sCommand], capture_output=True, text=True,
        )
        return SimpleNamespace(
            iExitCode=resultProcess.returncode,
            sStdout=resultProcess.stdout,
            sStderr=resultProcess.stderr,
        )


def _tPrefixCollidingSiblingSymlink(tmp_path):
    """Create root ``repo`` + sibling ``repo-evil`` with an escaping link.

    Returns the repo root path. The sibling's basename shares the
    root's basename as a string prefix, so a bare ``startswith`` check
    (no ``os.sep``) would wrongly treat the link target as in-root.
    """
    pathRepo = tmp_path / "repo"
    pathRepo.mkdir()
    pathSibling = tmp_path / "repo-evil"
    pathSibling.mkdir()
    (pathSibling / "loot.txt").write_text("loot")
    (pathRepo / "link.txt").symlink_to(pathSibling / "loot.txt")
    return pathRepo


def test_host_hash_refuses_sibling_dir_sharing_root_prefix(tmp_path):
    """Kills: In _fbEscapesRoot drop `+ os.sep` from `not sCandidateReal.startswith(sRepoReal + os.sep)` (line 280: `sRepoReal + os.sep,` -> `sRepoReal,`)"""
    pathRepo = _tPrefixCollidingSiblingSymlink(tmp_path)
    filesHost = HostRepoFiles(str(pathRepo))
    dictEntry = filesHost.fdictHashFiles(["link.txt"])["link.txt"]
    assert dictEntry["bEscapesRoot"] is True
    assert dictEntry["sSha256"] is None


def test_container_hash_refuses_sibling_dir_sharing_root_prefix(tmp_path):
    """Kills: In _S_HASH_SCRIPT (line 406) drop `+ os.sep` from `not sReal.startswith(sRootReal + os.sep)`"""
    pathRepo = _tPrefixCollidingSiblingSymlink(tmp_path)
    filesContainer = ContainerRepoFiles(
        _FakeExecDockerConnection(), "cid", str(pathRepo),
    )
    dictEntry = filesContainer.fdictHashFiles(["link.txt"])["link.txt"]
    assert dictEntry["bEscapesRoot"] is True
    assert dictEntry["sSha256"] is None


def test_snapshot_hash_refuses_sibling_dir_sharing_root_prefix(tmp_path):
    """Kills: In _S_SNAPSHOT_SCRIPT (line 749) drop `+ os.sep` from `not sReal.startswith(sRootReal + os.sep)`"""
    pathRepo = _tPrefixCollidingSiblingSymlink(tmp_path)
    filesSnapshot = SnapshotRepoFiles.ffilesFetch(
        _FakeExecDockerConnection(), "cid", str(pathRepo),
        listHashRelPaths=["link.txt"],
    )
    dictEntry = filesSnapshot.fdictHashFiles(["link.txt"])["link.txt"]
    assert dictEntry["bEscapesRoot"] is True
    assert dictEntry["sSha256"] is None
