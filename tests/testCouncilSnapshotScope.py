"""The git-tracked snapshot scope, driven against REAL git repositories.

Contracts B1-B5 of the credential-consent plan. Nothing here fakes git:
the connection double runs the declared ``gitTrackedIdentities`` and
``gitUntrackedInventory`` programs — the exact program text the
container runs — against a scratch repository on this machine, and
serves ``get_archive`` from the same directory while recording every
path it was asked for. So the index join, the skip-worktree tag, the
worktree-byte identities, and the per-path capture all meet real git
output rather than a hand-written stand-in for it.

The container is modelled at ``/projects/<name>`` while the files live
under pytest's temporary directory, so no test can pass by accidentally
treating a host path as a container path.
"""

import gzip
import io
import json
import os
import pathlib
import shutil
import subprocess
import sys
import tarfile

import pytest

from vaibify.docker import dockerConnection
from vaibify.gui import (
    agentCouncilCapacity,
    agentCouncilCharter,
    agentCouncilContext,
    agentCouncilSnapshotScope,
)
from vaibify.gui.agentCouncilContext import SnapshotRefusedError

pytestmark = pytest.mark.skipif(
    shutil.which("git") is None, reason="needs git on this machine")

S_CONTAINER_ID = "cid-scope-test"
S_REPO_NAME = "scopeRepo"
S_REPO_ROOT = f"/projects/{S_REPO_NAME}"
DICT_TRACKED = agentCouncilSnapshotScope.fdictComposeSnapshotScope(
    "gitTracked")


def _fnGit(pathRepo, *listArguments):
    subprocess.run(["git", "-C", str(pathRepo), *listArguments],
                   check=True, capture_output=True)


def _fnWrite(pathRepo, sRelative, sText):
    pathFile = pathRepo / sRelative
    pathFile.parent.mkdir(parents=True, exist_ok=True)
    pathFile.write_text(sText)


@pytest.fixture
def pathRepo(tmp_path, monkeypatch):
    """A repository exercising every row of the B1 table."""
    monkeypatch.setattr(
        agentCouncilSnapshotScope, "fsResolveSnapshotScopeDirectory",
        lambda: str(tmp_path / "scopeStore"))
    pathRepo = tmp_path / S_REPO_NAME
    pathRepo.mkdir()
    _fnGit(pathRepo, "init", "-q")
    _fnGit(pathRepo, "config", "user.email", "t@example.org")
    _fnGit(pathRepo, "config", "user.name", "tester")
    for sRelative, sText in (
            ("kept.txt", "kept\n"), ("deleted.txt", "gone soon\n"),
            ("skipAbsent.txt", "sparse\n"), ("skipPresent.txt", "present\n"),
            (".gitignore", "output/\n*.log\n"), ("code/step.py", "print(1)\n"),
            ("forced.log", "tracked though ignored\n"),
            ("CLAUDE.md", "agent instructions\n")):
        _fnWrite(pathRepo, sRelative, sText)
    os.symlink("kept.txt", pathRepo / "link")
    _fnGit(pathRepo, "add", "-A")
    _fnGit(pathRepo, "add", "-f", "forced.log")
    _fnGit(pathRepo, "commit", "-qm", "initial")
    _fnWrite(pathRepo, "kept.txt", "kept, edited\n")
    _fnWrite(pathRepo, "staged.txt", "staged addition\n")
    _fnGit(pathRepo, "add", "staged.txt")
    (pathRepo / "deleted.txt").unlink()
    _fnGit(pathRepo, "update-index", "--skip-worktree", "skipAbsent.txt",
           "skipPresent.txt")
    (pathRepo / "skipAbsent.txt").unlink()
    for iIndex in range(3):
        _fnWrite(pathRepo, f"output/run{iIndex}/result.bin", "x" * 50)
    _fnWrite(pathRepo, "notes.txt", "untracked\n")
    _fnWrite(pathRepo, "stray.log", "ignored\n")
    return pathRepo


class LocalContainer:
    """``get_archive`` served from the host directory, every path recorded."""

    def __init__(self, pathRepo, dictBeforeFetch=None):
        self.pathRepo = pathRepo
        self.listRequestedPaths = []
        self.dictBeforeFetch = dictBeforeFetch or {}

    def get_archive(self, sContainerPath):
        self.listRequestedPaths.append(sContainerPath)
        sRelative = os.path.relpath(sContainerPath, S_REPO_ROOT)
        fnHook = self.dictBeforeFetch.pop(sRelative, None)
        if fnHook is not None:
            fnHook()
        pathHost = self.pathRepo / sRelative if sRelative != "." else (
            self.pathRepo)
        bufferArchive = io.BytesIO()
        with tarfile.open(fileobj=bufferArchive, mode="w") as fileTar:
            fileTar.add(str(pathHost), arcname=pathHost.name)
        return iter([bufferArchive.getvalue()]), {"name": pathHost.name}


class LocalRepoConnection:
    """Runs the REAL typed-read programs against the host repository."""

    def __init__(self, pathRepo, dictBeforeFetch=None, dictProgramEdits=None):
        self.pathRepo = pathRepo
        self.container = LocalContainer(pathRepo, dictBeforeFetch)
        self.dictProgramEdits = dictProgramEdits or {}

    def _fdictRunProgram(self, sOperation):
        sProgram = dockerConnection._DICT_TYPED_READ_PROGRAMS[
            sOperation].replace(dockerConnection._S_TYPED_READ_PATH_SLOT,
                                repr(str(self.pathRepo)))
        for sOld, sNew in self.dictProgramEdits.get(sOperation, []):
            sProgram = sProgram.replace(sOld, sNew)
        processRun = subprocess.run([sys.executable, "-c", sProgram],
                                    capture_output=True, text=True, check=True)
        return json.loads(processRun.stdout)

    def fdictFetchTrackedIdentities(self, sContainerId, sRepoPath):
        assert sRepoPath == S_REPO_ROOT
        return self._fdictRunProgram(
            dockerConnection.S_TYPED_READ_GIT_TRACKED_IDENTITIES)

    def fdictFetchUntrackedInventory(self, sContainerId, sRepoPath):
        assert sRepoPath == S_REPO_ROOT
        return self._fdictRunProgram(
            dockerConnection.S_TYPED_READ_GIT_UNTRACKED_INVENTORY)

    def fdictFetchWorktreeIdentities(self, sContainerId, sRepoPath):
        assert sRepoPath == S_REPO_ROOT
        return self._fdictRunProgram(
            dockerConnection.S_TYPED_READ_GIT_WORKTREE_IDENTITIES)

    def fcontainerGetById(self, sContainerId):
        return self.container

    def ftResultExecuteCommand(self, sContainerId, sCommand):
        if "rev-parse --show-toplevel" in sCommand:
            return (0, S_REPO_ROOT + "\n")
        raise AssertionError(f"unmodelled container command: {sCommand!r}")


def _fdictObserve(connection):
    return agentCouncilSnapshotScope.fdictObserveTrackedScope(
        connection, S_CONTAINER_ID, S_REPO_ROOT,
        agentCouncilContext.ftFindExcludedComponent,
        agentCouncilContext._fnRaiseSnapshotRefusal)


# ----- B1: the tracked set, row by row ------------------------------------------------


def test_every_row_of_the_tracked_scope_table(pathRepo):
    dictIdentity = _fdictObserve(LocalRepoConnection(pathRepo))
    assert sorted(dictIdentity["dictEligible"]) == [
        ".gitignore", "code/step.py", "forced.log", "kept.txt", "link",
        "skipPresent.txt", "staged.txt"]
    assert dictIdentity["dictTrackedOmissions"] == {
        "CLAUDE.md": ["policyExcluded", 19],
        "deleted.txt": ["deletedInWorktree", 0],
        "skipAbsent.txt": ["notCheckedOut", 0]}
    assert dictIdentity["listSkipWorktreePaths"] == ["skipPresent.txt"]
    assert dictIdentity["dictEligible"]["link"]["sType"] == "symlink"
    assert dictIdentity["dictEligible"]["link"]["sIdentity"] == "kept.txt"


def test_identity_is_the_worktree_bytes_not_the_commit(pathRepo):
    dictIdentity = _fdictObserve(LocalRepoConnection(pathRepo))
    sExpected = subprocess.run(
        ["git", "-C", str(pathRepo), "hash-object", "--no-filters",
         "kept.txt"], capture_output=True, text=True).stdout.strip()
    assert dictIdentity["dictEligible"]["kept.txt"]["sIdentity"] == sExpected
    assert dictIdentity["iChangedCount"] == 3


@pytest.mark.falsification
def test_a_merge_conflict_refuses_naming_the_path(tmp_path):
    """A conflicted index has no single content to copy.

    Kills: the classifier accepting any merge stage as stage 0.
    """
    pathRepo = tmp_path / S_REPO_NAME
    pathRepo.mkdir()
    _fnGit(pathRepo, "init", "-q", "-b", "main")
    _fnGit(pathRepo, "config", "user.email", "t@example.org")
    _fnGit(pathRepo, "config", "user.name", "tester")
    _fnWrite(pathRepo, "clash.txt", "base\n")
    _fnGit(pathRepo, "add", ".")
    _fnGit(pathRepo, "commit", "-qm", "base")
    _fnGit(pathRepo, "checkout", "-qb", "other")
    _fnWrite(pathRepo, "clash.txt", "theirs\n")
    _fnGit(pathRepo, "commit", "-qam", "theirs")
    _fnGit(pathRepo, "checkout", "-q", "main")
    _fnWrite(pathRepo, "clash.txt", "ours\n")
    _fnGit(pathRepo, "commit", "-qam", "ours")
    subprocess.run(["git", "-C", str(pathRepo), "merge", "other"],
                   capture_output=True)
    with pytest.raises(SnapshotRefusedError, match="clash.txt"):
        _fdictObserve(LocalRepoConnection(pathRepo))


def test_a_gitlink_refuses(pathRepo):
    _fnGit(pathRepo, "update-index", "--add", "--cacheinfo",
           "160000," + "ab" * 20 + ",vendored")
    with pytest.raises(SnapshotRefusedError, match="vendored"):
        _fdictObserve(LocalRepoConnection(pathRepo))


def test_a_tracked_file_turned_into_a_directory_refuses(pathRepo):
    (pathRepo / "kept.txt").unlink()
    _fnWrite(pathRepo, "kept.txt/inner.txt", "now a directory\n")
    with pytest.raises(SnapshotRefusedError, match="kept.txt"):
        _fdictObserve(LocalRepoConnection(pathRepo))


# ----- B4: per-path capture ------------------------------------------------------------


def _fdictCaptureTracked(connection, tmp_path, dictBounds=None):
    return agentCouncilContext.fdictCaptureProjectContextSnapshot(
        connection, S_CONTAINER_ID, S_REPO_ROOT, "campaign-scope",
        sSnapshotStoreRoot=str(tmp_path / "councils"),
        dictBounds=dictBounds, dictSnapshotScope=DICT_TRACKED)


@pytest.mark.falsification
def test_a_tracked_capture_never_fetches_the_repository_root(
        pathRepo, tmp_path):
    """The whole point of the scope: omitted bulk is never streamed.

    Kills: the tracked scope falling back to the whole-directory stream.
    """
    connection = LocalRepoConnection(pathRepo)
    dictManifest = _fdictCaptureTracked(connection, tmp_path)
    listRequested = connection.container.listRequestedPaths
    assert S_REPO_ROOT not in listRequested
    assert sorted(listRequested) == sorted(
        f"{S_REPO_ROOT}/{sPath}" for sPath in (
            ".gitignore", "code/step.py", "forced.log", "kept.txt", "link",
            "skipPresent.txt", "staged.txt"))
    assert dictManifest["dictSnapshotScope"] == DICT_TRACKED
    assert dictManifest["listSkipWorktreePaths"] == ["skipPresent.txt"]
    assert dictManifest["bOmissionInventoryComplete"] is True


def test_the_tracked_archive_holds_the_worktree_bytes(pathRepo, tmp_path):
    _fdictCaptureTracked(LocalRepoConnection(pathRepo), tmp_path)
    sArchive = tmp_path / "councils" / "campaign-scope" / "snapshot" / (
        "snapshot.tar")
    with tarfile.open(sArchive) as fileTar:
        dictMembers = {infoMember.name: infoMember
                       for infoMember in fileTar.getmembers()}
        assert fileTar.extractfile("kept.txt").read() == b"kept, edited\n"
        assert dictMembers["link"].issym()
        assert dictMembers["code"].isdir()
    assert "output" not in dictMembers
    assert "notes.txt" not in dictMembers
    assert "CLAUDE.md" not in dictMembers


def test_the_inventory_beside_the_manifest_matches_its_recorded_sha(
        pathRepo, tmp_path):
    import hashlib
    dictManifest = _fdictCaptureTracked(LocalRepoConnection(pathRepo),
                                        tmp_path)
    pathInventory = (tmp_path / "councils" / "campaign-scope" / "snapshot"
                     / "omissions.jsonl.gz")
    assert hashlib.sha256(pathInventory.read_bytes()).hexdigest() == (
        dictManifest["sOmissionInventorySha256"])
    assert oct(pathInventory.stat().st_mode & 0o777) == "0o600"
    with gzip.open(pathInventory, "rt") as fileIn:
        listRows = [json.loads(sLine) for sLine in fileIn][1:]
    assert sorted(listRow[0] for listRow in listRows) == sorted([
        "CLAUDE.md", "deleted.txt", "notes.txt", "output/run0/result.bin",
        "output/run1/result.bin", "output/run2/result.bin",
        "skipAbsent.txt", "stray.log"])


@pytest.mark.falsification
def test_an_eligible_file_turned_directory_before_its_fetch_refuses(
        pathRepo, tmp_path):
    """The race after observation: refused at the first header.

    Kills: the per-path fetch accepting whatever the archive contains.
    """
    def _fnTurnIntoDirectory():
        (pathRepo / "kept.txt").unlink()
        _fnWrite(pathRepo, "kept.txt/big.bin", "y" * 100000)

    connection = LocalRepoConnection(
        pathRepo, dictBeforeFetch={"kept.txt": _fnTurnIntoDirectory})
    with pytest.raises(SnapshotRefusedError,
                       match="'kept.txt' no longer fetches as the single"):
        _fdictCaptureTracked(connection, tmp_path)
    assert not (tmp_path / "councils" / "campaign-scope").exists()


def test_an_eligible_file_grown_past_the_bound_refuses(pathRepo, tmp_path):
    dictBounds = agentCouncilCapacity.fdictFloorCouncilCapacity()
    dictBounds["iMaxSnapshotMemberBytes"] = 1000
    connection = LocalRepoConnection(pathRepo, dictBeforeFetch={
        "code/step.py": lambda: _fnWrite(pathRepo, "code/step.py",
                                         "z" * 5000)})
    with pytest.raises(SnapshotRefusedError, match="per-file limit"):
        _fdictCaptureTracked(connection, tmp_path, dictBounds)


def test_untracked_churn_during_capture_does_not_tear_it(pathRepo, tmp_path):
    connection = LocalRepoConnection(pathRepo, dictBeforeFetch={
        "kept.txt": lambda: _fnWrite(pathRepo, "output/new.bin", "late\n")})
    dictManifest = _fdictCaptureTracked(connection, tmp_path)
    assert dictManifest["iIncludedMemberCount"] == 7


def test_a_tracked_edit_during_capture_tears_it(pathRepo, tmp_path):
    connection = LocalRepoConnection(pathRepo, dictBeforeFetch={
        "staged.txt": lambda: _fnWrite(pathRepo, "kept.txt", "changed\n")})
    with pytest.raises(SnapshotRefusedError):
        _fdictCaptureTracked(connection, tmp_path)


# ----- B2: the scope travels ------------------------------------------------------------


def _fdictStalenessObservation(connection, dictScope):
    return agentCouncilSnapshotScope.fdictObserveForStaleness(
        connection, S_CONTAINER_ID, S_REPO_ROOT, dictScope,
        agentCouncilContext.ftFindExcludedComponent)


def _ftComparable(dictObservation):
    return (dictObservation["sHeadSha"], dictObservation["sPorcelainDigest"],
            agentCouncilContext.fsComputePathIdentitiesDigest(
                dictObservation["dictPathIdentities"]))


@pytest.mark.falsification
def test_a_new_untracked_file_leaves_a_tracked_council_fresh(pathRepo):
    """Kills: the tracked read counting untracked files in its status."""
    connection = LocalRepoConnection(pathRepo)
    tBefore = _ftComparable(_fdictStalenessObservation(
        connection, DICT_TRACKED))
    _fnWrite(pathRepo, "fresh_output.csv", "1,2,3\n")
    assert _ftComparable(_fdictStalenessObservation(
        connection, DICT_TRACKED)) == tBefore
    tWholeBefore = tBefore
    assert _ftComparable(_fdictStalenessObservation(
        connection, None)) != tWholeBefore


def test_a_tracked_edit_marks_a_tracked_council_stale(pathRepo):
    connection = LocalRepoConnection(pathRepo)
    tBefore = _ftComparable(_fdictStalenessObservation(
        connection, DICT_TRACKED))
    _fnWrite(pathRepo, "code/step.py", "print(2)\n")
    assert _ftComparable(_fdictStalenessObservation(
        connection, DICT_TRACKED)) != tBefore


def test_a_missing_scope_is_the_whole_directory_and_an_unknown_one_refuses():
    assert agentCouncilSnapshotScope.fdictNormaliseSnapshotScope(None) == {
        "sScope": "wholeDirectory", "iScopeVersion": 1}
    for dictScope in ({"sScope": "gitTracked", "iScopeVersion": 2},
                      {"sScope": "everything", "iScopeVersion": 1},
                      "gitTracked"):
        with pytest.raises(agentCouncilSnapshotScope.SnapshotScopeError):
            agentCouncilSnapshotScope.fdictNormaliseSnapshotScope(dictScope)


def test_the_staleness_poll_reads_in_the_manifests_scope(
        pathRepo, tmp_path):
    """The route's own producer, over a sealed tracked manifest."""
    from vaibify.gui.routes import councilRoutes
    connection = LocalRepoConnection(pathRepo)
    _fdictCaptureTracked(connection, tmp_path)
    dictCtx = {"docker": connection}
    dictStore = {"sDurableStoreRoot": str(tmp_path / "councils")}

    def _fdictStale():
        return councilRoutes._fdictComputeBaselineStaleness(
            dictCtx, dictStore, S_CONTAINER_ID, S_REPO_ROOT,
            "campaign-scope")

    assert _fdictStale()["bPlanningBaselineStale"] is False
    _fnWrite(pathRepo, "output/another.bin", "new output\n")
    assert _fdictStale()["bPlanningBaselineStale"] is False
    _fnWrite(pathRepo, "kept.txt", "edited again\n")
    assert _fdictStale()["bPlanningBaselineStale"] is True


# ----- B3: the omission inventory -------------------------------------------------------


def _fdictProbe(connection):
    return agentCouncilSnapshotScope.fdictProbeTrackedScope(
        connection, S_CONTAINER_ID, S_REPO_ROOT,
        agentCouncilCapacity.fdictFloorCouncilCapacity(),
        agentCouncilContext.ftFindExcludedComponent)


@pytest.mark.falsification
def test_group_totals_equal_the_omitted_total(pathRepo):
    """Every file is counted once, under one (directory, reason) group.

    Kills: a group key other than the top-level directory, which
    scatters one directory's files across many groups.
    """
    dictSummary = _fdictProbe(LocalRepoConnection(pathRepo))[
        "dictOmissionSummary"]
    assert dictSummary["iOmittedCount"] == 8
    assert sum(dictGroup["iCount"] for dictGroup
               in dictSummary["listGroups"]) == 8
    assert sum(dictGroup["iBytes"] for dictGroup
               in dictSummary["listGroups"]) == dictSummary["iOmittedBytes"]
    dictOutput = next(dictGroup for dictGroup in dictSummary["listGroups"]
                      if dictGroup["sDirectory"] == "output")
    assert (dictOutput["sReason"], dictOutput["iCount"]) == ("ignored", 3)


def _flistPageEverything(dictOffer):
    listSeen = []
    for dictGroup in dictOffer["dictOmissionSummary"]["listGroups"]:
        iOffset = 0
        while True:
            dictPage = agentCouncilSnapshotScope.fdictReadOmissionPage(
                dictOffer["sObservationId"], S_CONTAINER_ID,
                dictGroup["sDirectory"], dictGroup["sReason"], iOffset, 2)
            listSeen.extend(dictPath["sPath"]
                            for dictPath in dictPage["listPaths"])
            iOffset += 2
            if iOffset >= dictPage["iTotal"]:
                break
    return listSeen


def test_paging_serves_every_path_once_while_the_tree_changes(pathRepo):
    dictOffer = _fdictProbe(LocalRepoConnection(pathRepo))
    _fnWrite(pathRepo, "output/run9/late.bin", "late")
    (pathRepo / "notes.txt").unlink()
    listSeen = _flistPageEverything(dictOffer)
    assert len(listSeen) == len(set(listSeen)) == 8
    assert "notes.txt" in listSeen
    assert "output/run9/late.bin" not in listSeen


@pytest.mark.falsification
def test_a_superseded_or_expired_observation_is_refused(pathRepo,
                                                        monkeypatch):
    """Kills: pages served from an inventory a newer look replaced."""
    connection = LocalRepoConnection(pathRepo)
    dictFirst = _fdictProbe(connection)
    dictSecond = _fdictProbe(connection)
    with pytest.raises(agentCouncilSnapshotScope.OmissionInventoryExpiredError):
        agentCouncilSnapshotScope.fdictReadOmissionPage(
            dictFirst["sObservationId"], S_CONTAINER_ID, "output", "ignored")
    agentCouncilSnapshotScope.fdictReadOmissionPage(
        dictSecond["sObservationId"], S_CONTAINER_ID, "output", "ignored")
    with pytest.raises(agentCouncilSnapshotScope.OmissionInventoryExpiredError):
        agentCouncilSnapshotScope.fdictReadOmissionPage(
            dictSecond["sObservationId"], "another-container", "output",
            "ignored")
    monkeypatch.setattr(agentCouncilSnapshotScope,
                        "F_INVENTORY_LIFETIME_SECONDS", -1.0)
    with pytest.raises(agentCouncilSnapshotScope.OmissionInventoryExpiredError):
        agentCouncilSnapshotScope.fdictReadOmissionPage(
            dictSecond["sObservationId"], S_CONTAINER_ID, "output", "ignored")


def test_an_over_limit_inventory_is_incomplete_and_says_at_least(pathRepo):
    connection = LocalRepoConnection(pathRepo, dictProgramEdits={
        dockerConnection.S_TYPED_READ_GIT_UNTRACKED_INVENTORY: [(
            f"iMaxPaths={dockerConnection.I_MAX_OMISSION_INVENTORY_PATHS}",
            "iMaxPaths=2")]})
    dictSummary = _fdictProbe(connection)["dictOmissionSummary"]
    assert dictSummary["bComplete"] is False
    sNote = agentCouncilCharter.fsDescribeSnapshotScope([], {
        "dictSnapshotScope": DICT_TRACKED, "dictOmissionSummary": dictSummary,
        "bOmissionInventoryComplete": dictSummary["bComplete"]})
    assert "or more files" in sNote


def test_the_inventory_names_never_reach_the_participant(pathRepo, tmp_path):
    dictManifest = _fdictCaptureTracked(LocalRepoConnection(pathRepo),
                                        tmp_path)
    sNote = agentCouncilCharter.fsDescribeSnapshotScope([], dictManifest)
    for sName in ("notes.txt", "stray.log", "result.bin", "deleted.txt"):
        assert sName not in sNote


# ----- B5: the approved wording --------------------------------------------------------


@pytest.mark.falsification
def test_the_scope_note_is_the_approved_wording():
    """Ruling 4: the paragraph exactly, zero-count reasons left out.

    Kills: a zero-count reason printed as "…: 0".
    """
    dictManifest = {
        "dictSnapshotScope": DICT_TRACKED,
        "bOmissionInventoryComplete": True,
        "dictOmissionSummary": {
            "iOmittedCount": 12, "iOmittedBytes": 2500000,
            "dictByReason": {"ignored": {"iCount": 10, "iBytes": 2000000},
                             "untracked": {"iCount": 2, "iBytes": 500000}},
            "listGroups": [{"sDirectory": "output", "sReason": "ignored",
                            "iCount": 10, "iBytes": 2000000}]}}
    assert agentCouncilCharter.fsDescribeSnapshotScope([], dictManifest) == (
        "This copy of the project contains the eligible files git tracks, "
        "as they are in the working tree (including uncommitted edits to "
        "tracked files), subject to the omissions listed below. Untracked "
        "and git-ignored files are not included. Omitted: 12 files "
        "(2.5 MB), by reason — untracked: 2; ignored: 10. Largest omitted "
        "groups: output (ignored, 10 files, 2.0 MB). Results that would "
        "have come from omitted files are not available to you. Do not "
        "assume their contents. Say what you would need regenerated or "
        "provided.")


def test_a_whole_directory_snapshot_adds_no_scope_note():
    assert agentCouncilCharter.fsDescribeSnapshotScope(
        [], {"dictSnapshotScope": {"sScope": "wholeDirectory",
                                   "iScopeVersion": 1}}) == ""


# ----- the remembered scope -------------------------------------------------------------


def test_a_remembered_scope_is_per_project(tmp_path, monkeypatch):
    monkeypatch.setattr(
        agentCouncilSnapshotScope, "fsResolveSnapshotScopeDirectory",
        lambda: str(tmp_path / "scopeStore"))
    agentCouncilSnapshotScope.fnRememberScope("projectA", "/w/a",
                                              DICT_TRACKED)
    assert agentCouncilSnapshotScope.fdictReadRememberedScope(
        "projectA", "/w/a") == DICT_TRACKED
    assert agentCouncilSnapshotScope.fdictReadRememberedScope(
        "projectB", "/w/a") is None


@pytest.mark.falsification
def test_a_superseded_inventory_file_is_deleted(pathRepo, tmp_path):
    """Kills: inventories kept forever, one per capabilities read."""
    connection = LocalRepoConnection(pathRepo)
    for _ in range(3):
        _fdictProbe(connection)
    listInventories = [pathFile for pathFile in (
        tmp_path / "scopeStore" / "inventories").iterdir()
        if pathFile.name.endswith(".jsonl.gz")]
    assert len(listInventories) == 1
