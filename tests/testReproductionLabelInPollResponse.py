"""The "Project (reproduced)" label, decided server-side from real evidence.

Every test builds a real git repository, has the real record writer
(``flistWriteVerificationOutcome``) leave a reproduction record in it,
and drives the real poll snapshot -- the program that runs inside the
container, here run by a local interpreter through the typed-read
transport's own argument encoding. The poll is judged by what it
answers, never by what a stub says it computed.

Identities are distinct: the author committed the manifest, the reader's
configured email is someone else's.
"""

import hashlib
import json
import os
import subprocess
import sys
from types import SimpleNamespace

import pytest

from tests.reproductionSourceFixtures import (
    S_FIXTURE_OUTPUT,
    S_FIXTURE_SCRIPT,
    S_FIXTURE_WORKFLOW_PATH,
    fnCommitEverything,
    fsBuildPublishedProject,
    fsRunGit,
)
from vaibify.docker import dockerConnection
from vaibify.gui.routes import pipelineRoutes
from vaibify.reproducibility import reproductionRecord
from vaibify.reproducibility.manifestWriter import flistParseManifestText
from vaibify.reproducibility.repoFiles import (
    SnapshotRepoFiles,
    ffilesEnsureRepoFiles,
)

S_READER_EMAIL = "reader@example.invalid"
S_CONTAINER_ID = "cid-label"


@pytest.fixture(autouse=True)
def fnIsolateGitIdentity(monkeypatch):
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    monkeypatch.setenv("GIT_COMMITTER_EMAIL", "author@example.invalid")
    monkeypatch.setenv("GIT_AUTHOR_EMAIL", "author@example.invalid")
    monkeypatch.setenv("GIT_COMMITTER_NAME", "Author")
    monkeypatch.setenv("GIT_AUTHOR_NAME", "Author")


class LocalSnapshotConnection:
    """Runs the REAL typed-read snapshot program against a local directory."""

    ftReadRepoSnapshot = dockerConnection.DockerConnection.ftReadRepoSnapshot

    def __init__(self, fnBetweenStats=None):
        self.iSnapshotReads = 0
        self.listLastArgs = []

    def _ftRunTypedRead(self, sContainerId, sOperation, listArgs):
        assert sOperation == dockerConnection.S_TYPED_READ_REPO_SNAPSHOT
        self.iSnapshotReads += 1
        self.listLastArgs = list(listArgs)
        sProgram = dockerConnection._DICT_TYPED_READ_PROGRAMS[
            sOperation
        ].replace(
            dockerConnection._S_TYPED_READ_PATH_SLOT,
            dockerConnection._fsTypedReadPathLiteral(listArgs),
        )
        processRun = subprocess.run(
            [sys.executable, "-c", sProgram], capture_output=True, text=True,
        )
        return SimpleNamespace(
            iExitCode=processRun.returncode, sStdout=processRun.stdout,
            sStderr=processRun.stderr,
        )

    def fbaFetchFile(self, sContainerId, sPath):
        with open(sPath, "rb") as fileHandle:
            return fileHandle.read()


def _fsBuildReaderClone(tmp_path, sReaderEmail=S_READER_EMAIL):
    sRepo = str(tmp_path / "clone")
    fsBuildPublishedProject(sRepo)
    fsRunGit(["config", "user.email", sReaderEmail], sRepo)
    return sRepo


def _fdictWorkflowFor(sRepo):
    return {"sProjectRepoPath": sRepo, "listSteps": []}


def _fsWorkflowPath(sRepo):
    return os.path.join(sRepo, S_FIXTURE_WORKFLOW_PATH)


def _fdictOutcomeFor(sRepo, sVerdictPassed=True, sManifestDigest=None):
    """A real outcome: per-entry observed hashes read from the files."""
    filesRepo = ffilesEnsureRepoFiles(sRepo)
    sManifestText = filesRepo.fsReadText("MANIFEST.sha256")
    listEntries = flistParseManifestText(sManifestText)
    listOutcomes = []
    for dictEntry in listEntries:
        dictHash = filesRepo.fdictHashFiles([dictEntry["sPath"]])[
            dictEntry["sPath"]]
        listOutcomes.append({
            "sPath": dictEntry["sPath"], "sExpected": dictEntry["sExpected"],
            "sObserved": dictHash["sSha256"],
            "sStatus": "matched" if sVerdictPassed else "diverged",
            "sRole": "output",
        })
    return {
        "bPassed": sVerdictPassed, "bRerunAttempted": True,
        "iOutputHashesMatched": len(listOutcomes),
        "iOutputHashesTotal": len(listOutcomes),
        "listDivergedHashes": [], "listFileOutcomes": listOutcomes,
        "sManifestDigest": sManifestDigest or hashlib.sha256(
            sManifestText.encode("utf-8")).hexdigest(),
        "sWorkflowRelativePath": S_FIXTURE_WORKFLOW_PATH,
        "sWorkflowDigest": hashlib.sha256(
            filesRepo.fbaReadBytes(S_FIXTURE_WORKFLOW_PATH)).hexdigest(),
        "dictReproductionProvenance": {"sPlatform": "linux/amd64"},
    }


def _fnWriteRecord(sRepo, dictOutcome, sTimestampNote=""):
    reproductionRecord.flistWriteVerificationOutcome(
        sRepo, reproductionRecord.S_RECORD_KIND_REPRODUCTION, dictOutcome,
        1.0, {"sWorkflowName": "Demo"}, lambda *aArgs: {},
    )


def _fdictPollLabel(sRepo, connection=None, dictCtxExtra=None,
                    dictLastNoVerdict=None):
    connection = connection or LocalSnapshotConnection()
    dictCtx = {"docker": connection, "files": object(),
               "dictManifestTextCache": {}}
    dictCtx.update(dictCtxExtra or {})
    filesPoll = pipelineRoutes._ffilesFetchPollSnapshot(
        dictCtx, S_CONTAINER_ID, _fdictWorkflowFor(sRepo), {},
        _fsWorkflowPath(sRepo),
    )
    from vaibify.reproducibility.reproductionLabel import (
        fdictBuildReproductionLabel, fsRelativeWorkflowPath,
    )
    return fdictBuildReproductionLabel(
        filesPoll, fsRelativeWorkflowPath(
            _fsWorkflowPath(sRepo), sRepo), dictLastNoVerdict,
    ), connection, dictCtx


@pytest.mark.falsification
def test_a_reproduced_record_shows_the_label(tmp_path):
    """Kills: accepting any verdict, or never showing the label."""
    sRepo = _fsBuildReaderClone(tmp_path)
    _fnWriteRecord(sRepo, _fdictOutcomeFor(sRepo))
    dictLabel, _connection, _ctx = _fdictPollLabel(sRepo)
    assert dictLabel["bShow"] is True, dictLabel
    assert dictLabel["sState"] == "reproduced"
    assert dictLabel["sPlatform"] == "linux/amd64"


def test_no_record_and_a_manual_clean_check_show_no_label(tmp_path):
    sRepo = _fsBuildReaderClone(tmp_path)
    dictLabel, _connection, _ctx = _fdictPollLabel(sRepo)
    assert dictLabel["bShow"] is False
    assert dictLabel["sState"] == "absent"


@pytest.mark.falsification
def test_a_diverged_record_shows_no_label(tmp_path):
    """Kills: treating every written record as a reproduction."""
    sRepo = _fsBuildReaderClone(tmp_path)
    _fnWriteRecord(sRepo, _fdictOutcomeFor(sRepo, sVerdictPassed=False))
    dictLabel, _connection, _ctx = _fdictPollLabel(sRepo)
    assert dictLabel["bShow"] is False
    assert dictLabel["sState"] == "diverged"


def test_the_label_survives_the_records_own_commit(tmp_path):
    """The record is committed, which moves HEAD; the label stays.

    The label is about the bytes of the entries, not about HEAD: the
    label's inputs carry no HEAD at all, and this pins that a commit
    of the record itself cannot take the label away.
    """
    sRepo = _fsBuildReaderClone(tmp_path)
    _fnWriteRecord(sRepo, _fdictOutcomeFor(sRepo))
    sHeadBefore = fsRunGit(["rev-parse", "HEAD"], sRepo)
    fnCommitEverything(sRepo, "Record a reproduction of the published result")
    assert fsRunGit(["rev-parse", "HEAD"], sRepo) != sHeadBefore
    dictLabel, _connection, _ctx = _fdictPollLabel(sRepo)
    assert dictLabel["bShow"] is True, dictLabel


def _fnWriteRecordAt(sRepo, sName, dictRecord):
    sDirectory = os.path.join(sRepo, ".vaibify", "reproductions")
    os.makedirs(sDirectory, exist_ok=True)
    with open(os.path.join(sDirectory, sName), "w") as fileOut:
        json.dump(dictRecord, fileOut)


def _fdictRecordAs(sRepo, sVerdict, sCreatedIso):
    _fnWriteRecord(sRepo, _fdictOutcomeFor(sRepo))
    sDirectory = os.path.join(sRepo, ".vaibify", "reproductions")
    sOnlyName = sorted(os.listdir(sDirectory))[-1]
    with open(os.path.join(sDirectory, sOnlyName)) as fileIn:
        dictRecord = json.load(fileIn)
    os.remove(os.path.join(sDirectory, sOnlyName))
    dictRecord["sVerdict"] = sVerdict
    dictRecord["sCreatedAtIso"] = sCreatedIso
    return dictRecord


@pytest.mark.falsification
def test_a_later_diverged_clears_and_a_later_no_verdict_keeps_with_both_named(
    tmp_path,
):
    """Kills: picking the newest `reproduced` record instead of the newest decisive."""
    sRepo = _fsBuildReaderClone(tmp_path)
    dictGood = _fdictRecordAs(sRepo, "reproduced", "2026-10-01T10:00:00+00:00")
    _fnWriteRecordAt(sRepo, "20261001T100000Z_reproduced.json", dictGood)
    dictLabel, _c, _x = _fdictPollLabel(sRepo)
    assert dictLabel["bShow"] is True
    dictLabel, _c, _x = _fdictPollLabel(sRepo, dictLastNoVerdict={
        "listReasons": ["the daemon was unreachable"],
        "sRecordedIso": "2026-10-02T09:00:00+00:00",
    })
    assert dictLabel["bShow"] is True
    assert dictLabel["sLatestAttemptVerdict"] == "no-verdict"
    assert dictLabel["sReason"] == "the daemon was unreachable"
    assert dictLabel["sRecordedIso"].startswith("2026-10-01")
    assert dictLabel["sLatestAttemptIso"].startswith("2026-10-02")
    dictBad = _fdictRecordAs(sRepo, "diverged", "2026-10-03T10:00:00+00:00")
    _fnWriteRecordAt(sRepo, "20261003T100000Z_diverged.json", dictBad)
    dictLabel, _c, _x = _fdictPollLabel(sRepo)
    assert dictLabel["bShow"] is False
    assert dictLabel["sState"] == "diverged"


@pytest.mark.falsification
def test_a_change_to_any_pinned_file_clears_the_label(tmp_path):
    """Kills: skipping the comparison of a manifest entry's live hash.

    Each of an output, a script, an envelope file and the workflow file
    is changed in a clone of its own; every one clears the label.
    """
    for iIndex, sRelativePath in enumerate((
        S_FIXTURE_SCRIPT, S_FIXTURE_OUTPUT, ".vaibify/environment.json",
        S_FIXTURE_WORKFLOW_PATH,
    )):
        sRepo = _fsBuildReaderClone(tmp_path / str(iIndex))
        _fnWriteRecord(sRepo, _fdictOutcomeFor(sRepo))
        dictLabel, _c, _x = _fdictPollLabel(sRepo)
        assert dictLabel["bShow"] is True
        with open(os.path.join(sRepo, sRelativePath), "a") as fileOut:
            fileOut.write(" ")
        dictLabel, _c, _x = _fdictPollLabel(sRepo)
        assert dictLabel["bShow"] is False, sRelativePath
        assert dictLabel["sState"] == "evidence-changed", sRelativePath


@pytest.mark.falsification
def test_a_rewrite_that_restores_the_mtime_is_rehashed_and_clears_the_label(
    tmp_path,
):
    """Kills: keying the cache on whole-second mtime (today's behaviour).

    The poll runs once (so the cache holds the file), then the file is
    rewritten with DIFFERENT bytes of the SAME size and its mtime put
    back to the nanosecond. Only the ctime moved, and the key holds it.
    """
    sRepo = _fsBuildReaderClone(tmp_path)
    _fnWriteRecord(sRepo, _fdictOutcomeFor(sRepo))
    connection = LocalSnapshotConnection()
    dictCtx = {"dictManifestShaCache": {}}
    dictLabel, _c, dictCtx = _fdictPollLabel(
        sRepo, connection, dictCtxExtra=dictCtx)
    assert dictLabel["bShow"] is True
    sTarget = os.path.join(sRepo, S_FIXTURE_OUTPUT)
    statBefore = os.stat(sTarget)
    with open(sTarget, "rb") as fileIn:
        baOriginal = fileIn.read()
    baTampered = bytes([baOriginal[0] ^ 1]) + baOriginal[1:]
    with open(sTarget, "wb") as fileOut:
        fileOut.write(baTampered)
    os.utime(sTarget, ns=(statBefore.st_atime_ns, statBefore.st_mtime_ns))
    assert int(os.stat(sTarget).st_mtime) == int(statBefore.st_mtime)
    dictLabelAfter, _c2, _x2 = _fdictPollLabel(
        sRepo, connection, dictCtxExtra=dictCtx)
    assert dictLabelAfter["bShow"] is False, dictLabelAfter


@pytest.mark.falsification
def test_a_file_changing_during_the_hash_is_neither_cached_nor_matched(
    tmp_path,
):
    """Kills: skipping the stat taken after the hash."""
    sRepo = _fsBuildReaderClone(tmp_path)
    sTarget = os.path.join(sRepo, S_FIXTURE_OUTPUT)
    sProgram = dockerConnection._DICT_TYPED_READ_PROGRAMS[
        dockerConnection.S_TYPED_READ_REPO_SNAPSHOT]
    # A hook between the hash and the after-stat: the program's own
    # reader is wrapped so every read rewrites the file's bytes.
    sHook = (
        "import os as _os\n"
        "_iReal = _os.read\n"
        "def _fiTorn(iFd, iSize):\n"
        "    iPosition = _os.lseek(iFd, 0, _os.SEEK_CUR)\n"
        "    baData = _iReal(iFd, iSize)\n"
        "    if baData and iPosition == 0 and _os.fstat(iFd).st_size == "
        + "len(baData) and _os.fstat(iFd).st_ino == "
        + str(os.stat(sTarget).st_ino) + ":\n"
        "        with open(" + repr(sTarget) + ", 'ab') as f:\n"
        "            f.write(b'+')\n"
        "    return baData\n"
        "_os.read = _fiTorn\n"
    )

    class TornConnection(LocalSnapshotConnection):
        def _ftRunTypedRead(self, sContainerId, sOperation, listArgs):
            sProgramHooked = sHook + sProgram.replace(
                dockerConnection._S_TYPED_READ_PATH_SLOT,
                dockerConnection._fsTypedReadPathLiteral(listArgs))
            processRun = subprocess.run(
                [sys.executable, "-c", sProgramHooked],
                capture_output=True, text=True)
            return SimpleNamespace(
                iExitCode=processRun.returncode,
                sStdout=processRun.stdout, sStderr=processRun.stderr)

    dictCtx = {"docker": TornConnection(), "files": object(),
               "dictManifestTextCache": {}}
    filesPoll = pipelineRoutes._ffilesFetchPollSnapshot(
        dictCtx, S_CONTAINER_ID, _fdictWorkflowFor(sRepo), {},
        _fsWorkflowPath(sRepo),
    )
    dictEntry = filesPoll.fdictAllHashEntries()[S_FIXTURE_OUTPUT]
    assert dictEntry["sSha256"] is None
    assert dictEntry["bTornRead"] is True
    assert S_FIXTURE_OUTPUT not in dictCtx["dictManifestShaCache"][
        S_CONTAINER_ID][sRepo]


@pytest.mark.falsification
def test_containment_is_checked_on_a_cache_hit(tmp_path):
    """Kills: restoring the hard-coded `bEscapesRoot: False` on cache hits."""
    sRepo = _fsBuildReaderClone(tmp_path)
    connection = LocalSnapshotConnection()
    dictCtx = {"dictManifestShaCache": {}}
    _label, _c, dictCtx = _fdictPollLabel(
        sRepo, connection, dictCtxExtra=dictCtx)
    sOutside = str(tmp_path / "outside")
    os.makedirs(sOutside)
    sTarget = os.path.join(sRepo, S_FIXTURE_OUTPUT)
    sDirectory = os.path.dirname(sTarget)
    os.rename(sDirectory, sDirectory + ".moved")
    os.symlink(sOutside, sDirectory)
    with open(os.path.join(sOutside, os.path.basename(sTarget)), "w") as f:
        f.write("1\n")
    filesPoll = pipelineRoutes._ffilesFetchPollSnapshot(
        dictCtx, S_CONTAINER_ID, _fdictWorkflowFor(sRepo), {},
        _fsWorkflowPath(sRepo),
    )
    dictEntry = filesPoll.fdictAllHashEntries()[S_FIXTURE_OUTPUT]
    assert dictEntry["bEscapesRoot"] is True
    assert filesPoll.fdictHashFiles([S_FIXTURE_OUTPUT])[
        S_FIXTURE_OUTPUT]["bEscapesRoot"] is True


@pytest.mark.falsification
def test_a_record_for_another_workflow_does_not_supersede(tmp_path):
    """Kills: selecting records across all workflows."""
    sRepo = _fsBuildReaderClone(tmp_path)
    dictGood = _fdictRecordAs(sRepo, "reproduced", "2026-10-01T10:00:00+00:00")
    _fnWriteRecordAt(sRepo, "20261001T100000Z_reproduced.json", dictGood)
    dictOther = _fdictRecordAs(sRepo, "diverged", "2026-10-05T10:00:00+00:00")
    dictOther["sWorkflowRelativePath"] = ".vaibify/projects/other.json"
    _fnWriteRecordAt(sRepo, "20261005T100000Z_diverged.json", dictOther)
    dictLabel, _c, _x = _fdictPollLabel(sRepo)
    assert dictLabel["bShow"] is True, dictLabel


@pytest.mark.falsification
def test_unreadable_evidence_is_neither_absent_nor_reproduced(tmp_path):
    """Kills: treating a failed read as 'no records'."""
    sRepo = _fsBuildReaderClone(tmp_path)
    _fnWriteRecord(sRepo, _fdictOutcomeFor(sRepo))
    sDirectory = os.path.join(sRepo, ".vaibify", "reproductions")
    with open(os.path.join(sDirectory, "zzz_broken.json"), "w") as fileOut:
        fileOut.write("{not json")
    dictLabel, _c, _x = _fdictPollLabel(sRepo)
    assert dictLabel["sState"] == "unreadable"
    assert dictLabel["bShow"] is False


def test_the_poll_still_makes_one_batched_snapshot_read(tmp_path):
    sRepo = _fsBuildReaderClone(tmp_path)
    _fnWriteRecord(sRepo, _fdictOutcomeFor(sRepo))
    _label, connection, _ctx = _fdictPollLabel(sRepo)
    assert connection.iSnapshotReads == 1


@pytest.mark.falsification
def test_the_label_never_shows_for_ones_own_manifest(tmp_path):
    """Kills: dropping the ownership check from the label."""
    sRepo = _fsBuildReaderClone(tmp_path, sReaderEmail="author@example.invalid")
    _fnWriteRecord(sRepo, _fdictOutcomeFor(sRepo))
    dictLabel, _c, _x = _fdictPollLabel(sRepo)
    assert dictLabel["bShow"] is False
