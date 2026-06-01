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

import pytest

from vaibify.gui import conftestManager


_S_CONTAINER_ID = "test-cid"
_S_PROJECT_REPO = "/workspace/myrepo"


@pytest.fixture(autouse=True)
def _fnClearRefreshCachesBetweenTests():
    """Reset the per-process refresh caches so each test runs deterministically."""
    conftestManager.fnClearRefreshCaches()
    yield
    conftestManager.fnClearRefreshCaches()


class _FakeDocker:
    """Minimal stand-in for the dockerConnection surface the helpers use.

    Simulates both the legacy single-file path
    (``fbaFetchFile`` + ``fnWriteFile``) AND the new batched-exec
    path (``ftResultExecuteCommand``). The batch probe is recognised
    by the ``vaibify-conftest-version`` regex literal embedded in
    the script; the batch write by the ``d['listPaths']`` literal.
    Both extract the JSON-over-stdin payload between the ``<<< '``
    delimiter and the closing ``'`` of the heredoc.
    """

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
        if "re.compile(" in sCommand and "vaibify-conftest-version" in sCommand:
            return self._tHandleBatchProbe(sCommand)
        if "os.makedirs(" in sCommand:
            return self._tHandleBatchWrite(sCommand)
        return self.tExecuteResult

    def _tHandleBatchProbe(self, sCommand):
        dictPayload = self._fdictParseHeredocPayload(sCommand)
        listPaths = dictPayload if isinstance(dictPayload, list) else []
        dictResult = {}
        for sPath in listPaths:
            baContent = self.dictFiles.get(sPath)
            if baContent is None:
                continue
            sText = baContent.decode("utf-8", errors="replace")
            import re as _re
            match = _re.search(
                r"^# vaibify-conftest-version:\s*(\S+)\s*$",
                sText, _re.M,
            )
            if match:
                dictResult[sPath] = match.group(1)
        return (0, json.dumps(dictResult))

    def _tHandleBatchWrite(self, sCommand):
        dictPayload = self._fdictParseHeredocPayload(sCommand)
        if not isinstance(dictPayload, dict):
            return (0, "")
        sContent = dictPayload.get("sContent", "")
        baContent = sContent.encode("utf-8")
        for sPath in dictPayload.get("listPaths", []):
            self.listWrites.append((sPath, baContent))
            self.dictFiles[sPath] = baContent
        return (0, "OK")

    def _fdictParseHeredocPayload(self, sCommand):
        """Extract the JSON payload from the trailing ``<<< 'PAYLOAD'`` block.

        ``fsShellQuote`` uses the backslash-escape idiom ``'\\''`` to
        embed single quotes inside the surrounding ``'...'`` wrapper,
        so the inverse replacement is the same sequence.
        """
        iMarker = sCommand.rfind("<<< ")
        if iMarker < 0:
            return None
        sTail = sCommand[iMarker + 4:].strip()
        if sTail.startswith("'") and sTail.endswith("'"):
            sTail = sTail[1:-1].replace("'\\''", "'")
        try:
            return json.loads(sTail)
        except (ValueError, TypeError):
            return None


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


def test_ensure_current_issues_at_most_two_execs_for_100_steps():
    """Switch-time invariant: probe + write = 2 execs regardless of N.

    Locks in the perf fix that replaced the per-step
    ``fsReadInstalledConftestVersion`` loop with one batched probe
    and one batched write. If any future change re-introduces a
    per-step exec, this test fails loud.
    """
    fakeDocker = _FakeDocker()
    listStepDirs = [f"step{i:03d}" for i in range(100)]
    conftestManager.fnEnsureConftestsCurrent(
        fakeDocker, _S_CONTAINER_ID, listStepDirs, _S_PROJECT_REPO,
    )
    assert len(fakeDocker.listCommands) <= 2, (
        f"Expected at most two docker execs for 100 steps, "
        f"got {len(fakeDocker.listCommands)}"
    )
    assert len(fakeDocker.listWrites) == 100, (
        "All 100 stale conftests should be rewritten in the batch"
    )


def test_ensure_current_only_probes_when_all_files_already_current():
    """No-write path: when every conftest is current, only the probe runs."""
    fakeDocker = _FakeDocker()
    listStepDirs = ["step01", "step02", "step03"]
    sCurrentSource = conftestManager.fsBuildConftestSource(_S_PROJECT_REPO)
    for sStepDir in listStepDirs:
        sPath = conftestManager.fsConftestPath(
            _S_PROJECT_REPO + "/" + sStepDir,
        )
        fakeDocker.dictFiles[sPath] = sCurrentSource.encode("utf-8")
    conftestManager.fnEnsureConftestsCurrent(
        fakeDocker, _S_CONTAINER_ID, listStepDirs, _S_PROJECT_REPO,
    )
    assert len(fakeDocker.listCommands) == 1
    assert fakeDocker.listWrites == []


def test_fdictReadInstalledConftestVersions_returns_per_path_versions():
    """The batched probe returns one entry per readable, stamped file."""
    fakeDocker = _FakeDocker()
    fakeDocker.dictFiles["/a/conftest.py"] = (
        b"# vaibify-conftest-version: 1\n"
    )
    fakeDocker.dictFiles["/b/conftest.py"] = (
        b"# vaibify-conftest-version: 7\n"
    )
    dictResult = conftestManager.fdictReadInstalledConftestVersions(
        fakeDocker, _S_CONTAINER_ID,
        ["/a/conftest.py", "/b/conftest.py", "/c/missing.py"],
    )
    assert dictResult == {
        "/a/conftest.py": "1", "/b/conftest.py": "7",
    }


def test_fdictReadInstalledConftestVersions_short_circuits_on_empty_list():
    """Empty path list returns an empty dict and does no docker work."""
    fakeDocker = _FakeDocker()
    dictResult = conftestManager.fdictReadInstalledConftestVersions(
        fakeDocker, _S_CONTAINER_ID, [],
    )
    assert dictResult == {}
    assert fakeDocker.listCommands == []


def test_fnWriteConftestMarkersBatch_writes_all_paths_in_one_exec():
    """Batched writer hits every path in a single docker exec."""
    fakeDocker = _FakeDocker()
    listPaths = [
        "/repo/step01/tests/conftest.py",
        "/repo/step02/tests/conftest.py",
        "/repo/step03/tests/conftest.py",
    ]
    sContent = "# vaibify-conftest-version: 2\n# body\n"
    bWritten = conftestManager.fnWriteConftestMarkersBatch(
        fakeDocker, _S_CONTAINER_ID, listPaths, sContent,
    )
    assert bWritten is True
    assert len(fakeDocker.listCommands) == 1
    assert sorted(p for p, _ in fakeDocker.listWrites) == sorted(listPaths)


def test_second_ensure_call_is_noop_after_successful_refresh():
    """Connect + first-poll de-dup: the second call does zero docker work.

    The connect path runs ``_fnRefreshConftestsAndMigrateMarkers`` and
    the first poll runs it again. Before this guard, both paid the
    full refresh sweep — now the second call short-circuits because
    the (container, repo, version) tuple is cached.
    """
    fakeDocker = _FakeDocker()
    listStepDirs = ["step01", "step02"]
    conftestManager.fnEnsureConftestsCurrent(
        fakeDocker, _S_CONTAINER_ID, listStepDirs, _S_PROJECT_REPO,
    )
    iCmdsAfterFirst = len(fakeDocker.listCommands)
    iWritesAfterFirst = len(fakeDocker.listWrites)
    assert iCmdsAfterFirst >= 1
    conftestManager.fnEnsureConftestsCurrent(
        fakeDocker, _S_CONTAINER_ID, listStepDirs, _S_PROJECT_REPO,
    )
    assert len(fakeDocker.listCommands) == iCmdsAfterFirst, (
        "Second call must not issue any docker exec"
    )
    assert len(fakeDocker.listWrites) == iWritesAfterFirst


def test_second_migrate_flat_markers_call_is_noop():
    """Same de-dup contract for flat-marker migration."""
    fakeDocker = _FakeDocker()
    fakeDocker.tExecuteResult = (
        0, json.dumps({"iMoved": 0, "listMoved": []}),
    )
    conftestManager.fnMigrateFlatMarkers(
        fakeDocker, _S_CONTAINER_ID, _S_PROJECT_REPO, "demo-slug",
    )
    assert len(fakeDocker.listCommands) == 1
    conftestManager.fnMigrateFlatMarkers(
        fakeDocker, _S_CONTAINER_ID, _S_PROJECT_REPO, "demo-slug",
    )
    assert len(fakeDocker.listCommands) == 1, (
        "Second migrate call must not issue a docker exec"
    )


def test_refresh_caches_can_be_cleared_for_tests():
    """The fnClearRefreshCaches helper resets both caches."""
    fakeDocker = _FakeDocker()
    conftestManager.fnEnsureConftestsCurrent(
        fakeDocker, _S_CONTAINER_ID, ["step01"], _S_PROJECT_REPO,
    )
    iCmds = len(fakeDocker.listCommands)
    conftestManager.fnClearRefreshCaches()
    conftestManager.fnEnsureConftestsCurrent(
        fakeDocker, _S_CONTAINER_ID, ["step01"], _S_PROJECT_REPO,
    )
    assert len(fakeDocker.listCommands) > iCmds, (
        "Clearing the cache must let the next call run again"
    )


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
