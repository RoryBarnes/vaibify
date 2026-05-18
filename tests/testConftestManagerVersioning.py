"""Unit tests for the conftest versioning + flat-marker migration helpers.

The dashboard's connect-time refresh path detects stale conftest.py
copies on a researcher's host by reading the embedded
``# vaibify-conftest-version:`` sentinel and comparing it against
``S_CONFTEST_VERSION``. Older workspaces also wrote markers to a flat
``.vaibify/test_markers/<step>.json`` layout that the host reader no
longer scans; the migration helper moves those into the slug subdir.
These tests cover each helper in isolation.
"""

import json
from unittest.mock import MagicMock

from vaibify.gui import conftestManager


_S_CONTAINER_ID = "test-cid"
_S_PROJECT_REPO = "/workspace/myrepo"


class _FakeDocker:
    """Minimal stand-in for the dockerConnection surface the helpers use."""

    def __init__(self):
        self.dictFiles = {}
        self.listWrites = []
        self.listCommands = []
        self.tExecuteResult = (0, "")

    def fbaFetchFile(self, sContainerId, sPath):
        if sPath in self.dictFiles:
            return self.dictFiles[sPath]
        raise FileNotFoundError(sPath)

    def fnWriteFile(self, sContainerId, sPath, baContent):
        self.listWrites.append((sPath, baContent))
        self.dictFiles[sPath] = baContent

    def ftResultExecuteCommand(self, sContainerId, sCommand):
        self.listCommands.append(sCommand)
        return self.tExecuteResult


def test_version_stamp_is_present_in_generated_source():
    """fsBuildConftestSource embeds the version stamp at the top."""
    sSource = conftestManager.fsBuildConftestSource(_S_PROJECT_REPO)
    sExpected = (
        conftestManager.S_CONFTEST_VERSION_PREFIX
        + conftestManager.S_CONFTEST_VERSION
    )
    assert sExpected in sSource


def test_read_installed_version_parses_sentinel():
    """fsReadInstalledConftestVersion returns the embedded version string."""
    fakeDocker = _FakeDocker()
    sPath = "/x/tests/conftest.py"
    fakeDocker.dictFiles[sPath] = (
        b"# vaibify-conftest-version: 2\n"
        b"# rest of the file\n"
    )
    sResult = conftestManager.fsReadInstalledConftestVersion(
        fakeDocker, _S_CONTAINER_ID, sPath,
    )
    assert sResult == "2"


def test_read_installed_version_returns_empty_on_missing_sentinel():
    """A legacy conftest with no sentinel reports empty version."""
    fakeDocker = _FakeDocker()
    sPath = "/x/tests/conftest.py"
    fakeDocker.dictFiles[sPath] = b"# legacy conftest\nimport pytest\n"
    sResult = conftestManager.fsReadInstalledConftestVersion(
        fakeDocker, _S_CONTAINER_ID, sPath,
    )
    assert sResult == ""


def test_read_installed_version_returns_empty_on_missing_file():
    """An absent conftest reports empty version, not an exception."""
    fakeDocker = _FakeDocker()
    sResult = conftestManager.fsReadInstalledConftestVersion(
        fakeDocker, _S_CONTAINER_ID, "/missing/conftest.py",
    )
    assert sResult == ""


def test_ensure_current_rewrites_when_version_stale():
    """Outdated installed file gets overwritten with current source."""
    fakeDocker = _FakeDocker()
    sStepDir = "step01"
    sConftestPath = conftestManager.fsConftestPath(
        _S_PROJECT_REPO + "/" + sStepDir,
    )
    fakeDocker.dictFiles[sConftestPath] = (
        b"# vaibify-conftest-version: 1\n# old body\n"
    )
    conftestManager.fnEnsureConftestsCurrent(
        fakeDocker, _S_CONTAINER_ID, [sStepDir], _S_PROJECT_REPO,
    )
    assert fakeDocker.listWrites, (
        "stale conftest should have been rewritten"
    )
    _sWrittenPath, baWritten = fakeDocker.listWrites[-1]
    sWritten = baWritten.decode("utf-8")
    sCurrentStamp = (
        conftestManager.S_CONFTEST_VERSION_PREFIX
        + conftestManager.S_CONFTEST_VERSION
    )
    assert sCurrentStamp in sWritten


def test_ensure_current_skips_when_already_current():
    """Up-to-date installed file is left untouched (no rewrite)."""
    fakeDocker = _FakeDocker()
    sStepDir = "step01"
    sConftestPath = conftestManager.fsConftestPath(
        _S_PROJECT_REPO + "/" + sStepDir,
    )
    sCurrentSource = conftestManager.fsBuildConftestSource(_S_PROJECT_REPO)
    fakeDocker.dictFiles[sConftestPath] = sCurrentSource.encode("utf-8")
    conftestManager.fnEnsureConftestsCurrent(
        fakeDocker, _S_CONTAINER_ID, [sStepDir], _S_PROJECT_REPO,
    )
    assert fakeDocker.listWrites == []


def test_ensure_current_writes_when_file_missing():
    """A missing conftest counts as stale and gets written fresh."""
    fakeDocker = _FakeDocker()
    sStepDir = "step01"
    conftestManager.fnEnsureConftestsCurrent(
        fakeDocker, _S_CONTAINER_ID, [sStepDir], _S_PROJECT_REPO,
    )
    assert len(fakeDocker.listWrites) == 1


def test_ensure_current_short_circuits_on_empty_step_list():
    """An empty step-dir list does no work — connect stays cheap."""
    fakeDocker = _FakeDocker()
    conftestManager.fnEnsureConftestsCurrent(
        fakeDocker, _S_CONTAINER_ID, [], _S_PROJECT_REPO,
    )
    assert fakeDocker.listWrites == []
    assert fakeDocker.listCommands == []


def test_migrate_flat_markers_invokes_python_with_paths():
    """The migration command embeds the repo path and slug as argv."""
    fakeDocker = _FakeDocker()
    fakeDocker.tExecuteResult = (
        0, json.dumps({"iMoved": 0, "listMoved": []}),
    )
    conftestManager.fnMigrateFlatMarkers(
        fakeDocker, _S_CONTAINER_ID, _S_PROJECT_REPO, "demo-slug",
    )
    assert len(fakeDocker.listCommands) == 1
    sCommand = fakeDocker.listCommands[0]
    assert sCommand.startswith("python3 -c ")
    assert "'/workspace/myrepo'" in sCommand
    assert "'demo-slug'" in sCommand


def test_migrate_flat_markers_no_ops_when_repo_path_empty():
    """A workflow with no project repo path is left alone (no shell exec)."""
    fakeDocker = _FakeDocker()
    conftestManager.fnMigrateFlatMarkers(
        fakeDocker, _S_CONTAINER_ID, "", "demo-slug",
    )
    assert fakeDocker.listCommands == []


def test_migrate_flat_markers_no_ops_when_slug_empty():
    """A missing workflow slug is also a no-op."""
    fakeDocker = _FakeDocker()
    conftestManager.fnMigrateFlatMarkers(
        fakeDocker, _S_CONTAINER_ID, _S_PROJECT_REPO, "",
    )
    assert fakeDocker.listCommands == []
