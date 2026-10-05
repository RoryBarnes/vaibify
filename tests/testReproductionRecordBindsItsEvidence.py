"""A reader's reproduction record carries the evidence that makes it checkable.

The record binds HEAD as the BASELINE (read before the record's own
commit), the tracked and untracked paths that differ from it, the
digests of the manifest and the workflow as the exported snapshot held
them before any step ran, and a pointer to the timestamped history copy
of the reproduced manifest -- never the shared root file a later run
overwrites. Every repository is real git, and the files the writer
creates itself are never counted as differences from the baseline.
"""

import hashlib
import json
import os

import pytest

from tests.reproductionSourceFixtures import (
    S_FIXTURE_OUTPUT,
    S_FIXTURE_WORKFLOW_PATH,
    fsBuildPublishedProject,
    fsRunGit,
)
from vaibify.reproducibility import gitEvidence, reproductionRecord
from vaibify.reproducibility.manifestWriter import flistParseManifestText
from vaibify.reproducibility.rerunVerification import (
    fdictSnapshotWorkflowEvidence,
)
from vaibify.reproducibility.repoFiles import ffilesEnsureRepoFiles


@pytest.fixture(autouse=True)
def fnIsolateGitIdentity(monkeypatch):
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    monkeypatch.setenv("GIT_COMMITTER_EMAIL", "author@example.invalid")
    monkeypatch.setenv("GIT_AUTHOR_EMAIL", "author@example.invalid")
    monkeypatch.setenv("GIT_COMMITTER_NAME", "Author")
    monkeypatch.setenv("GIT_AUTHOR_NAME", "Author")


def _fsClone(tmp_path):
    sRepo = str(tmp_path / "clone")
    fsBuildPublishedProject(sRepo)
    fsRunGit(["config", "user.email", "reader@example.invalid"], sRepo)
    return sRepo


def _fdictOutcome(sRepo):
    filesRepo = ffilesEnsureRepoFiles(sRepo)
    sManifestText = filesRepo.fsReadText("MANIFEST.sha256")
    listOutcomes = [
        {"sPath": dictEntry["sPath"], "sExpected": dictEntry["sExpected"],
         "sObserved": dictEntry["sExpected"], "sStatus": "matched",
         "sRole": "output"}
        for dictEntry in flistParseManifestText(sManifestText)
    ]
    dictEvidence = fdictSnapshotWorkflowEvidence(
        filesRepo, os.path.join(sRepo, S_FIXTURE_WORKFLOW_PATH))
    return {
        "dictBaselineEvidence": gitEvidence.fdictReadBaselineEvidence(
            filesRepo),
        "bPassed": True, "bRerunAttempted": True, "listFileOutcomes": listOutcomes,
        "iOutputHashesMatched": len(listOutcomes),
        "iOutputHashesTotal": len(listOutcomes), "listDivergedHashes": [],
        "sManifestDigest": "sha256:" + hashlib.sha256(
            sManifestText.encode("utf-8")).hexdigest(),
        **dictEvidence,
    }


def _fdictWriteAndReadRecord(sRepo):
    listWritten = reproductionRecord.flistWriteVerificationOutcome(
        sRepo, reproductionRecord.S_RECORD_KIND_REPRODUCTION,
        _fdictOutcome(sRepo), 1.0, {"sWorkflowName": "Demo"},
        lambda *aArgs: {},
    )
    sRecordPath = [s for s in listWritten if s.endswith(".json")][0]
    with open(os.path.join(sRepo, sRecordPath)) as fileIn:
        return json.load(fileIn), listWritten


def test_the_workflow_evidence_is_the_file_as_the_snapshot_holds_it(tmp_path):
    sRepo = _fsClone(tmp_path)
    dictEvidence = fdictSnapshotWorkflowEvidence(
        ffilesEnsureRepoFiles(sRepo),
        os.path.join(sRepo, S_FIXTURE_WORKFLOW_PATH))
    assert dictEvidence["sWorkflowRelativePath"] == S_FIXTURE_WORKFLOW_PATH
    with open(os.path.join(sRepo, S_FIXTURE_WORKFLOW_PATH), "rb") as fileIn:
        assert dictEvidence["sWorkflowDigest"] == hashlib.sha256(
            fileIn.read()).hexdigest()


def test_a_reader_record_binds_the_baseline_and_the_snapshots_digests(
    tmp_path,
):
    sRepo = _fsClone(tmp_path)
    sHead = fsRunGit(["rev-parse", "HEAD"], sRepo)
    dictRecord, listWritten = _fdictWriteAndReadRecord(sRepo)
    assert dictRecord["dictSource"]["sResolvedCommit"] == sHead
    assert dictRecord["bBaselineKnown"] is True
    assert dictRecord["listPathsDifferingFromBaseline"] == []
    assert dictRecord["sWorkflowRelativePath"] == S_FIXTURE_WORKFLOW_PATH
    assert dictRecord["dictSource"]["sWorkflowPath"] == S_FIXTURE_WORKFLOW_PATH
    with open(os.path.join(sRepo, "MANIFEST.sha256"), "rb") as fileIn:
        assert dictRecord["sManifestDigest"] == "sha256:" + hashlib.sha256(
            fileIn.read()).hexdigest()
    assert dictRecord["sWorkflowDigest"]
    # The outcomes point at the timestamped copy, never the shared root
    # file that a later run overwrites.
    assert dictRecord["sReproducedManifestPath"].startswith(
        ".vaibify/reproducedManifests/")
    assert dictRecord["sReproducedManifestPath"] in listWritten


def test_a_dirty_export_lists_its_differing_paths(tmp_path):
    sRepo = _fsClone(tmp_path)
    with open(os.path.join(sRepo, S_FIXTURE_OUTPUT), "a") as fileOut:
        fileOut.write("edited\n")
    with open(os.path.join(sRepo, "scratch.txt"), "w") as fileOut:
        fileOut.write("untracked\n")
    dictRecord, _written = _fdictWriteAndReadRecord(sRepo)
    assert dictRecord["listPathsDifferingFromBaseline"] == sorted([
        S_FIXTURE_OUTPUT, "scratch.txt"])


def test_a_git_that_cannot_answer_leaves_the_baseline_unknown(tmp_path):
    sNotARepository = str(tmp_path / "plain")
    os.makedirs(sNotARepository)
    dictEvidence = gitEvidence.fdictReadBaselineEvidence(
        ffilesEnsureRepoFiles(sNotARepository))
    assert dictEvidence["bBaselineKnown"] is False
    assert dictEvidence["listPathsDifferingFromBaseline"] is None


@pytest.mark.falsification
def test_a_record_names_the_head_the_lane_read_not_the_one_at_write_time(
    tmp_path,
):
    """Kills: re-reading HEAD when the record is written.

    The lane read HEAD before it exported. If HEAD advances before the
    outcome is written, the record must still name the commit the
    exported tree was compared with, never the later one.
    """
    sRepo = _fsClone(tmp_path)
    sHeadAtExport = fsRunGit(["rev-parse", "HEAD"], sRepo)
    dictOutcome = _fdictOutcome(sRepo)
    fsRunGit(["commit", "--quiet", "--allow-empty", "-m", "later"], sRepo)
    sHeadLater = fsRunGit(["rev-parse", "HEAD"], sRepo)
    assert sHeadLater != sHeadAtExport
    listWritten = reproductionRecord.flistWriteVerificationOutcome(
        sRepo, reproductionRecord.S_RECORD_KIND_REPRODUCTION, dictOutcome,
        1.0, {"sWorkflowName": "Demo"}, lambda *aArgs: {},
    )
    sRecordPath = [s for s in listWritten if s.endswith(".json")][0]
    with open(os.path.join(sRepo, sRecordPath)) as fileIn:
        dictRecord = json.load(fileIn)
    assert dictRecord["dictSource"]["sResolvedCommit"] == sHeadAtExport


def test_an_outcome_with_no_baseline_leaves_the_record_unverified(tmp_path):
    sRepo = _fsClone(tmp_path)
    dictOutcome = _fdictOutcome(sRepo)
    del dictOutcome["dictBaselineEvidence"]
    listWritten = reproductionRecord.flistWriteVerificationOutcome(
        sRepo, reproductionRecord.S_RECORD_KIND_REPRODUCTION, dictOutcome,
        1.0, {"sWorkflowName": "Demo"}, lambda *aArgs: {},
    )
    sRecordPath = [s for s in listWritten if s.endswith(".json")][0]
    with open(os.path.join(sRepo, sRecordPath)) as fileIn:
        dictRecord = json.load(fileIn)
    assert dictRecord["bBaselineKnown"] is False
    assert dictRecord["dictSource"]["sResolvedCommit"] == ""


@pytest.mark.falsification
def test_a_head_that_moved_during_the_export_leaves_the_baseline_unknown():
    """Kills: naming the pre-export commit when HEAD moved while it ran."""
    from vaibify.reproducibility import shadowRerun
    dictBefore = {"sResolvedCommit": "a" * 40,
                  "listPathsDifferingFromBaseline": [], "bBaselineKnown": True}
    dictMoved = dict(dictBefore, sResolvedCommit="b" * 40)
    assert shadowRerun._fdictBaselineStillHolding(
        dictBefore, dictMoved)["bBaselineKnown"] is False
    assert shadowRerun._fdictBaselineStillHolding(
        dictBefore, dict(dictBefore)) == dictBefore


def test_a_reproduced_record_never_raises_the_proof_level(tmp_path):
    """The reader's record changes no level: Level 2 is the author's to hold."""
    from vaibify.reproducibility import levelGates
    sRepo = _fsClone(tmp_path)
    dictWorkflow = {"sProjectRepoPath": sRepo, "listSteps": []}
    iBefore = levelGates.fiProofLevel(
        dictWorkflow, sRepo, {}, bHostProject=False)
    _fdictWriteAndReadRecord(sRepo)
    assert levelGates.fiProofLevel(
        dictWorkflow, sRepo, {}, bHostProject=False) == iBefore
    assert levelGates.fbAtLeastLevel3(dictWorkflow, sRepo, False) is False
