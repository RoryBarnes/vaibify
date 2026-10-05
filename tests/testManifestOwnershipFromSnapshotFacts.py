"""The poll's ownership answer is the writers' answer, from the same git.

The file-status poll cannot run git, so the snapshot program asks the
fixed ownership questions inside the container and the host answers the
ONE predicate from the replies. These tests run that whole path -- the
real program, the real transport encoding -- against real repositories
in every state the predicate distinguishes, and demand the same answer
the live predicate gives. A question the predicate comes to ask that the
program does not is undetermined here, so drift fails loudly.
"""

import json
import os

import pytest

from tests.reproductionSourceFixtures import (
    fnCommitEverything,
    fnWriteManifest,
    fnWriteText,
    fsRunGit,
)
from tests.testReproductionLabelInPollResponse import (
    LocalSnapshotConnection,
)
from vaibify.reproducibility import gitEvidence
from vaibify.reproducibility.repoFiles import SnapshotRepoFiles

S_CONTAINER_ID = "cid-facts"


@pytest.fixture(autouse=True)
def fnIsolateGitIdentity(monkeypatch):
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    monkeypatch.setenv("GIT_COMMITTER_EMAIL", "author@example.invalid")
    monkeypatch.setenv("GIT_AUTHOR_EMAIL", "author@example.invalid")
    monkeypatch.setenv("GIT_COMMITTER_NAME", "Author")
    monkeypatch.setenv("GIT_AUTHOR_NAME", "Author")


def _fsRepo(tmp_path, sName, sReaderEmail=None, bManifest=True, bCommit=True):
    sRepo = str(tmp_path / sName)
    os.makedirs(sRepo)
    fsRunGit(["init", "-q"], sRepo)
    fnWriteText(sRepo, "data.txt", "1\n")
    if bManifest:
        fnWriteManifest(sRepo, ["data.txt"])
    if bCommit:
        fnCommitEverything(sRepo, "author publishes")
    if sReaderEmail:
        fsRunGit(["config", "user.email", sReaderEmail], sRepo)
    return sRepo


def _fdictFactsFromTheProgram(sRepo):
    """Run the real snapshot program; a decisive record makes it ask git."""
    sDirectory = os.path.join(sRepo, ".vaibify", "reproductions")
    os.makedirs(sDirectory, exist_ok=True)
    with open(os.path.join(sDirectory, "1_reproduced.json"), "w") as fileOut:
        json.dump({"sVerdict": "reproduced",
                   "sWorkflowRelativePath": "w.json"}, fileOut)
    filesPoll = SnapshotRepoFiles.ffilesFetch(
        LocalSnapshotConnection(), S_CONTAINER_ID, sRepo,
        bReadReproductions=True,
    )
    return filesPoll.dictOwnershipFacts


@pytest.mark.falsification
def test_the_snapshot_facts_give_the_live_predicates_answer(tmp_path):
    """Kills: answering every question from the snapshot as 'exit 0, empty'."""
    dictRepos = {
        "foreign": _fsRepo(tmp_path, "foreign", "reader@example.invalid"),
        "own": _fsRepo(tmp_path, "own", "author@example.invalid"),
        "unconfigured": _fsRepo(tmp_path, "unconfigured"),
        "untracked": _fsRepo(tmp_path, "untracked", "reader@example.invalid",
                             bCommit=False),
    }
    dictExpected = {"foreign": "foreign", "own": "own",
                    "unconfigured": "foreign", "untracked": "own"}
    for sName, sRepo in dictRepos.items():
        sLive = gitEvidence.fsManifestOwnershipForRepoFiles(sRepo)
        sFromFacts = gitEvidence.fsManifestOwnershipFromSnapshotFacts(
            _fdictFactsFromTheProgram(sRepo))
        assert sFromFacts == sLive == dictExpected[sName], sName


def test_a_directory_that_is_not_a_repository_is_undetermined(tmp_path):
    sPlain = str(tmp_path / "plain")
    os.makedirs(sPlain)
    assert gitEvidence.fsManifestOwnershipFromSnapshotFacts(
        _fdictFactsFromTheProgram(sPlain)
    ) == gitEvidence.S_MANIFEST_OWNERSHIP_UNDETERMINED


def test_a_question_the_snapshot_did_not_ask_is_undetermined():
    assert gitEvidence.fsManifestOwnershipFromSnapshotFacts({}) == (
        gitEvidence.S_MANIFEST_OWNERSHIP_UNDETERMINED)
    assert gitEvidence.fsManifestOwnershipFromSnapshotFacts(None) == (
        gitEvidence.S_MANIFEST_OWNERSHIP_UNDETERMINED)
    assert gitEvidence.fsManifestOwnershipFromSnapshotFacts({
        "rev-parse --verify --quiet HEAD": [None, ""],
    }) == gitEvidence.S_MANIFEST_OWNERSHIP_UNDETERMINED


def test_the_legacy_transport_asks_the_same_questions(tmp_path):
    """The embedded transport %-formats the program; a stray percent breaks it."""
    import subprocess
    from types import SimpleNamespace

    class LegacyConnection:
        def ftRunInContainerStreamed(self, sContainerId, sCommand, **dictArgs):
            processRun = subprocess.run(
                ["bash", "-c", sCommand], capture_output=True, text=True)
            return SimpleNamespace(
                iExitCode=processRun.returncode, sStdout=processRun.stdout,
                sStderr=processRun.stderr)

    sRepo = _fsRepo(tmp_path, "legacy", "reader@example.invalid")
    sDirectory = os.path.join(sRepo, ".vaibify", "reproductions")
    os.makedirs(sDirectory)
    with open(os.path.join(sDirectory, "1_reproduced.json"), "w") as fileOut:
        json.dump({"sVerdict": "reproduced",
                   "sWorkflowRelativePath": "w.json"}, fileOut)
    filesPoll = SnapshotRepoFiles.ffilesFetch(
        LegacyConnection(), S_CONTAINER_ID, sRepo, bReadReproductions=True)
    assert gitEvidence.fsManifestOwnershipFromSnapshotFacts(
        filesPoll.dictOwnershipFacts) == "foreign"
