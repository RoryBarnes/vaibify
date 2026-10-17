"""A reader's reproduction record is saved in the clone and never committed.

Ruled 2026-10-12: when Verify Level 3 writes a READER's reproduction
record -- the clone's attestation was last committed by somebody else
-- the verification performs no git operation on the clone at all. No
``git add``, no commit. The record and the reproduced manifest are
plain files in the working tree, and the author's own lane (the
attestation kind) is unchanged.

Every test here drives the real verification worker against a real git
repository through a connection that runs its commands in a real shell,
so "no git operation" is judged by what git itself reports afterwards,
never by what a double says it was asked. The identities are distinct:
the author committed the attestation, the reader's configured email is
someone else's, so the real record-kind predicate answers
"reproduction" by itself.
"""

import asyncio
import json
import os
import subprocess

import pytest

from tests.reproductionSourceFixtures import (
    fnCommitEverything,
    fnWriteJson,
    fsRunGit,
)
from tests.testReproductionLabelInPollResponse import (
    S_READER_EMAIL,
    _fdictOutcomeFor,
    _fdictPollLabel,
    _fsBuildReaderClone,
    _fsWorkflowPath,
)
from tests.testReproductionRecordPlatform import (
    _DaemonThatAnswersItsArchitecture,
)
from tests.testRerunVerifiesWhatItRan import (
    S_OUTPUT_FILENAME,
    LocalShellContainer,
    _fdictWorkflowFor,
    _fnEnterAll,
    _fnPatchContainerLane,
    _fnSeedEnvelope,
    _fpathShadowCopyOf,
)
from vaibify.gui.routes import reproducibilityRoutes
from vaibify.reproducibility import gitEvidence
from vaibify.reproducibility import levelGates
from vaibify.reproducibility import reproductionRecord
from vaibify.reproducibility import shadowRerun
from vaibify.reproducibility.repoFiles import (
    ContainerRepoFiles,
    ffilesEnsureRepoFiles,
)

S_CONTAINER_ID = "cid-reader-record"


@pytest.fixture(autouse=True)
def fnIsolateGitIdentity(monkeypatch):
    """Keep the developer's own git identity out of every git call here."""
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    monkeypatch.setenv("GIT_COMMITTER_EMAIL", "author@example.invalid")
    monkeypatch.setenv("GIT_AUTHOR_EMAIL", "author@example.invalid")
    monkeypatch.setenv("GIT_COMMITTER_NAME", "Author")
    monkeypatch.setenv("GIT_AUTHOR_NAME", "Author")


def _fsBuildCloneWithTheAuthorsAttestation(tmp_path):
    """Return a reader's clone whose attestation the AUTHOR committed."""
    sRepo = _fsBuildReaderClone(tmp_path)
    fnWriteJson(sRepo, ".vaibify/l3_attestation.json", {"sStatus": "passed"})
    fnCommitEverything(sRepo, "author attests")
    assert subprocess.run(
        ["git", "config", "user.email"], cwd=sRepo, capture_output=True,
        text=True, check=True,
    ).stdout.strip() == S_READER_EMAIL
    return sRepo


def _fdictRunTheVerificationWorker(sRepo, monkeypatch, dictOutcome=None):
    """Drive the real worker once; return the settled status record."""
    dictOutcome = dictOutcome or _fdictOutcomeFor(sRepo)

    def _fdictRerunReturningTheOutcome(*args, **kwargs):
        return dict(dictOutcome)

    async def _fdictNothingCaptured(*args, **kwargs):
        return None

    monkeypatch.setattr(
        reproducibilityRoutes, "_fdictRunReproductionSync",
        _fdictRerunReturningTheOutcome)
    monkeypatch.setattr(
        reproducibilityRoutes, "_fdictImageOriginForContainer",
        lambda *args: None)
    monkeypatch.setattr(
        reproducibilityRoutes, "_fdictCaptureProvenanceOrNone",
        _fdictNothingCaptured)
    monkeypatch.setattr(
        reproducibilityRoutes, "_fdictRecheckImageArchiveOrNone",
        _fdictNothingCaptured)
    dictStatus = {}
    monkeypatch.setitem(
        reproducibilityRoutes._DICT_VERIFY_TASKS, S_CONTAINER_ID,
        {"dictStatus": dictStatus})
    asyncio.run(reproducibilityRoutes._fnRunVerificationWorker(
        S_CONTAINER_ID, ffilesEnsureRepoFiles(sRepo), "",
        {"sProjectRepoPath": sRepo, "sWorkflowName": "Demo"},
        LocalShellContainer(), _fsWorkflowPath(sRepo),
    ))
    return dictStatus


def _flistGitStatusLines(sRepo):
    """Return git's own porcelain status lines for the clone."""
    return subprocess.run(
        ["git", "status", "--porcelain", "--untracked-files=all"],
        cwd=sRepo, capture_output=True, text=True, check=True,
    ).stdout.splitlines()


@pytest.mark.falsification
def test_a_readers_record_is_saved_untracked_and_still_labelled(
    tmp_path, monkeypatch,
):
    """The reader's files stay untracked, HEAD stays put, the label shows.

    Kills: the verification staging and committing a reader's
    reproduction record into the clone, as it did before the
    2026-10-12 ruling.
    """
    sRepo = _fsBuildCloneWithTheAuthorsAttestation(tmp_path)
    sHeadBefore = fsRunGit(["rev-parse", "HEAD"], sRepo)

    dictStatus = _fdictRunTheVerificationWorker(sRepo, monkeypatch)

    assert dictStatus["sRecordKind"] == "reproduction"
    assert dictStatus["sPhase"] == "passed"
    assert fsRunGit(["rev-parse", "HEAD"], sRepo) == sHeadBefore, (
        "a reader's verification committed into the clone"
    )
    listStatus = _flistGitStatusLines(sRepo)
    listReproductionLines = [
        sLine for sLine in listStatus if ".vaibify/reproductions/" in sLine
    ]
    assert len(listReproductionLines) == 1, listStatus
    assert listReproductionLines[0].startswith("?? "), (
        "a reader's record must be an untracked file, never staged: "
        f"{listStatus!r}"
    )
    assert not any(sLine[0] not in " ?" for sLine in listStatus), (
        f"a reader's verification staged something: {listStatus!r}"
    )
    dictLabel, _connection, _ctx = _fdictPollLabel(sRepo)
    assert dictLabel["bShow"] is True, dictLabel
    assert dictLabel["sState"] == "reproduced"


@pytest.mark.falsification
def test_the_authors_attestation_is_still_committed(tmp_path, monkeypatch):
    """The author's own lane is unchanged: its record and root manifest commit.

    Kills: the reproduced-manifest writer no longer writing the root
    ``REPRODUCED.sha256`` for anyone, which would take the author's
    published comparison file away with the reader's.
    """
    sRepo = _fsBuildReaderClone(tmp_path)
    fsRunGit(["config", "user.email", "author@example.invalid"], sRepo)
    sHeadBefore = fsRunGit(["rev-parse", "HEAD"], sRepo)

    dictStatus = _fdictRunTheVerificationWorker(sRepo, monkeypatch)

    assert dictStatus["sRecordKind"] == "attestation"
    assert fsRunGit(["rev-parse", "HEAD"], sRepo) != sHeadBefore
    sCommitted = fsRunGit(
        ["show", "--name-only", "--format=", "HEAD"], sRepo)
    assert ".vaibify/l3_attestation.json" in sCommitted.splitlines()
    assert "REPRODUCED.sha256" in sCommitted.splitlines()
    assert dictStatus["sReproducedManifestPath"].startswith(
        ".vaibify/reproducedManifests/")
    assert json.loads(
        fsRunGit(["show", "HEAD:.vaibify/l3_attestation.json"], sRepo),
    )["sStatus"] == "passed"


def _fnPatchTheWorkersSideChannels(monkeypatch):
    """Stub what the worker asks besides the rerun: origin, AI stamp, re-check."""
    async def _fdictNothingCaptured(*args, **kwargs):
        return None

    monkeypatch.setattr(
        reproducibilityRoutes, "_fdictImageOriginForContainer",
        lambda *args: None)
    monkeypatch.setattr(
        reproducibilityRoutes, "_fdictCaptureProvenanceOrNone",
        _fdictNothingCaptured)
    monkeypatch.setattr(
        reproducibilityRoutes, "_fdictRecheckImageArchiveOrNone",
        _fdictNothingCaptured)


def _fdictVerifyThroughTheRealShadowLane(
    pathRepo, connectionContainer, monkeypatch, pathShadowRoot,
):
    """Run the real worker over the real shadow lane once; return its status.

    Each attempt gets its own shadow root, because each real attempt
    gets a fresh container: the fake daemon's containers are
    directories, and a second extraction over the first one's
    read-only git objects is a property of the fake, not of Docker.
    """
    from unittest.mock import patch

    pathShadowRoot.mkdir(parents=True)
    monkeypatch.setattr(
        shadowRerun, "S_SHADOW_WORKSPACE_ROOT", str(pathShadowRoot))

    async def _fiRunAllSteps(*args, **kwargs):
        (_fpathShadowCopyOf(pathRepo) / S_OUTPUT_FILENAME
         ).write_text("answer = 42\n")
        return 0

    dictStatus = {}
    monkeypatch.setitem(
        reproducibilityRoutes._DICT_VERIFY_TASKS, S_CONTAINER_ID,
        {"dictStatus": dictStatus})
    listPatches, _listRan = _fnPatchContainerLane(
        connectionContainer, [], {}, lambda: None,
        _DaemonThatAnswersItsArchitecture(),
    )
    fnStop = _fnEnterAll(listPatches)
    try:
        with patch(
            "vaibify.gui.pipelineRunner.fiRunAllSteps",
            side_effect=_fiRunAllSteps,
        ):
            asyncio.run(reproducibilityRoutes._fnRunVerificationWorker(
                S_CONTAINER_ID,
                ContainerRepoFiles(
                    connectionContainer, S_CONTAINER_ID, str(pathRepo)),
                "", _fdictWorkflowFor(pathRepo, "Project"),
                connectionContainer,
                str(pathRepo / ".vaibify" / "workflows" / "project.json"),
            ))
    finally:
        fnStop()
    return dictStatus


def _fdictReadRecords(pathRepo):
    """Return every reproduction record on disk, oldest first."""
    return [
        json.loads(pathRecord.read_text())
        for pathRecord in sorted(
            (pathRepo / ".vaibify" / "reproductions").glob("*.json"))
    ]


def _fnStampEachAttemptInItsOwnSecond(monkeypatch):
    """Give each attempt a record timestamp later than the last one's.

    A record's filename carries its timestamp to the second, so two
    attempts finishing within one second are named alike. This test is
    about what the first attempt leaves behind, not about that, and a
    fast runner finished both inside one second.
    """
    from datetime import datetime, timedelta

    listIssued = []
    fsRealTimestamp = reproductionRecord.fsCurrentTimestampUtc

    def fsLaterThanTheLastAttempt():
        sFormat = "%Y-%m-%dT%H:%M:%SZ"
        datetimeNext = datetime.strptime(fsRealTimestamp(), sFormat)
        if listIssued:
            datetimeNext = max(
                datetimeNext, listIssued[-1] + timedelta(seconds=1))
        listIssued.append(datetimeNext)
        return datetimeNext.strftime(sFormat)

    monkeypatch.setattr(
        reproductionRecord, "fsCurrentTimestampUtc",
        fsLaterThanTheLastAttempt)


def test_a_second_verification_by_the_reader_is_neither_refused_nor_degraded(
    tmp_path, monkeypatch,
):
    """The first attempt's untracked files do not refuse or degrade the next.

    Both attempts run the real worker over the real shadow lane: the
    coherent export (which enumerates untracked files too), the copy
    into the shadow, the frozen manifest, the comparison and the
    record writer. Readiness is computed by the real gate before and
    after the first attempt and must not move; the record kind is
    asked of real git each time; the second record's baseline names
    the first attempt's files as differing -- recorded, never refused.
    """
    monkeypatch.setenv("HOME", str(tmp_path / "isolatedHome"))
    _fnStampEachAttemptInItsOwnSecond(monkeypatch)
    pathRepo = _fnSeedEnvelope(
        tmp_path / "containerVolume" / "workspace" / "ProjectRepo")
    fnWriteJson(
        str(pathRepo), ".vaibify/l3_attestation.json", {"sStatus": "passed"})
    fnCommitEverything(str(pathRepo), "author attests")
    _fnPatchTheWorkersSideChannels(monkeypatch)
    connectionContainer = LocalShellContainer()
    filesRepo = ContainerRepoFiles(
        connectionContainer, S_CONTAINER_ID, str(pathRepo))
    dictWorkflow = _fdictWorkflowFor(pathRepo, "Project")
    ftRunGit = gitEvidence.ffnBuildGitRunnerForRepoFiles(filesRepo)
    dictGapsBefore = levelGates.fdictL3ReadinessGaps(dictWorkflow, filesRepo)

    dictFirst = _fdictVerifyThroughTheRealShadowLane(
        pathRepo, connectionContainer, monkeypatch,
        tmp_path / "firstShadowRoot")
    listUntracked = sorted(
        sLine[3:] for sLine in _flistGitStatusLines(str(pathRepo))
        if sLine.startswith("?? "))

    assert dictFirst["sRecordKind"] == "reproduction", dictFirst
    assert dictFirst["sPhase"] == "passed", dictFirst
    assert levelGates.fdictL3ReadinessGaps(
        dictWorkflow, filesRepo) == dictGapsBefore
    assert reproductionRecord.fsRecordKindForRepository(ftRunGit) == (
        reproductionRecord.S_RECORD_KIND_REPRODUCTION)
    assert gitEvidence.fdictReadBaselineEvidence(filesRepo)[
        "listPathsDifferingFromBaseline"] == listUntracked

    dictSecond = _fdictVerifyThroughTheRealShadowLane(
        pathRepo, connectionContainer, monkeypatch,
        tmp_path / "secondShadowRoot")

    assert dictSecond["sRecordKind"] == "reproduction"
    assert dictSecond["sPhase"] == "passed", dictSecond
    listRecords = _fdictReadRecords(pathRepo)
    assert len(listRecords) == 2, "the second attempt wrote no record"
    dictSecondRecord = listRecords[-1]
    # The assessed rerun's verdict: every regenerated file matched.
    assert dictSecondRecord["sVerdict"] == "byte-identical"
    assert dictSecondRecord["bBaselineKnown"] is True
    assert dictSecondRecord["listPathsDifferingFromBaseline"] == (
        listUntracked)


S_AUTHORS_REPRODUCED_TEXT = (
    "# the author's committed REPRODUCED.sha256, as published\n"
)


def _fsBuildCloneWithTheAuthorsReproducedManifest(tmp_path):
    """Return a reader's clone in which the author committed REPRODUCED.sha256."""
    sRepo = _fsBuildCloneWithTheAuthorsAttestation(tmp_path)
    with open(os.path.join(sRepo, "REPRODUCED.sha256"), "w") as fileHandle:
        fileHandle.write(S_AUTHORS_REPRODUCED_TEXT)
    fnCommitEverything(sRepo, "author publishes the reproduced manifest")
    return sRepo


def _fsReadRepositoryText(sRepo, sRelativePath):
    with open(os.path.join(sRepo, sRelativePath)) as fileHandle:
        return fileHandle.read()


def _flistDatedManifests(sRepo, sStatus):
    """Return the dated reproduced manifests of one status, repo-relative."""
    sDirectory = os.path.join(sRepo, ".vaibify", "reproducedManifests")
    return sorted(
        ".vaibify/reproducedManifests/" + sName
        for sName in os.listdir(sDirectory)
        if sName.endswith(f"_{sStatus}.sha256")
    )


@pytest.mark.falsification
def test_a_readers_pass_leaves_the_authors_root_manifest_untouched(
    tmp_path, monkeypatch,
):
    """A reader's PASS writes only the dated copy; the root stays the author's.

    Kills: a reader's verification writing the root REPRODUCED.sha256,
    which is the author's committed file.
    """
    sRepo = _fsBuildCloneWithTheAuthorsReproducedManifest(tmp_path)

    dictStatus = _fdictRunTheVerificationWorker(sRepo, monkeypatch)

    assert dictStatus["sRecordKind"] == "reproduction"
    assert _fsReadRepositoryText(sRepo, "REPRODUCED.sha256") == (
        S_AUTHORS_REPRODUCED_TEXT)
    assert subprocess.run(
        ["git", "status", "--porcelain", "--", "REPRODUCED.sha256"],
        cwd=sRepo, capture_output=True, text=True, check=True,
    ).stdout == ""
    assert len(_flistDatedManifests(sRepo, "passed")) == 1


@pytest.mark.falsification
def test_a_readers_divergence_lands_only_in_the_dated_copy(
    tmp_path, monkeypatch,
):
    """A diverged reader attempt leaves the root alone; the dated copy holds it.

    Kills: the reproduced-manifest writer ignoring the instruction not
    to write the root copy, so a reader's diverged hashes replace the
    author's committed file.
    """
    sRepo = _fsBuildCloneWithTheAuthorsReproducedManifest(tmp_path)
    dictOutcome = _fdictOutcomeFor(sRepo, sVerdictPassed=False)
    sDivergedHash = "f" * 64
    dictOutcome["listFileOutcomes"][0]["sObserved"] = sDivergedHash

    dictStatus = _fdictRunTheVerificationWorker(
        sRepo, monkeypatch, dictOutcome)

    assert dictStatus["sPhase"] == "failed"
    assert " M REPRODUCED.sha256" not in _flistGitStatusLines(sRepo)
    assert _fsReadRepositoryText(sRepo, "REPRODUCED.sha256") == (
        S_AUTHORS_REPRODUCED_TEXT)
    listFailed = _flistDatedManifests(sRepo, "failed")
    assert len(listFailed) == 1
    assert sDivergedHash in _fsReadRepositoryText(sRepo, listFailed[0])


@pytest.mark.falsification
def test_a_readers_settled_card_names_the_dated_copy(tmp_path, monkeypatch):
    """The settled status names the reader's dated copy, never the root file.

    Kills: the status naming no reproduced manifest (or the author's
    root file) for a reader, so the card would point at the wrong file
    or at nothing.
    """
    sRepo = _fsBuildCloneWithTheAuthorsReproducedManifest(tmp_path)

    dictStatus = _fdictRunTheVerificationWorker(sRepo, monkeypatch)

    listPassed = _flistDatedManifests(sRepo, "passed")
    assert dictStatus["sReproducedManifestPath"] == listPassed[0]
    dictRecord = json.loads(_fsReadRepositoryText(sRepo, [
        sLine[3:] for sLine in _flistGitStatusLines(sRepo)
        if ".vaibify/reproductions/" in sLine
    ][0]))
    assert dictRecord["sReproducedManifestPath"] == listPassed[0]
