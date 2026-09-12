"""The reproduced manifest, the per-file outcomes, and who the record is for.

A verification used to end in a sentence: "N of N hashes matched".
The hashes themselves were computed and thrown away. Now the rerun
keeps every one, renders them into ``REPRODUCED.sha256`` in the pinned
manifest's own format so a person compares the two with their own
eyes, and records them in the attestation -- or, on a clone whose
attestation was committed by somebody else, in a reproduction record
that leaves the author's claim alone.

Every falsification test names the mutation it was proven to fail
against on a ``Kills:`` line; the git-evidence tests drive the real
``git`` on PATH.
"""

import ast
import hashlib
import json
import os
import shutil
import subprocess

import pytest

from tests.reproductionSourceFixtures import fnWriteManifest, fnWriteText
from vaibify.cli.commandReproduce import _ffnBuildHostGitRunner
from vaibify.reproducibility import l3Attestation
from vaibify.reproducibility import manifestWriter
from vaibify.reproducibility import reproductionRecord
from vaibify.reproducibility import reproductionReport
from vaibify.reproducibility.l3Attestation import (
    I_SCHEMA_VERSION,
    fdictBuildAttestation,
    fdictReadAttestation,
    fnWriteAttestation,
)
from vaibify.reproducibility.manifestWriter import (
    S_REPRODUCED_MANIFEST_FILENAME,
    S_REPRODUCED_MANIFEST_HISTORY_DIR,
    S_REPRODUCTIONS_DIR,
    flistCollectCanonicalRepoPaths,
    flistParseManifestLines,
    fsRenderReproducedManifest,
)
from vaibify.reproducibility.repoFiles import HostRepoFiles
from vaibify.reproducibility.rerunVerification import (
    S_FILE_CARRIED,
    S_FILE_DIVERGED,
    S_FILE_MATCHED,
    S_FILE_MISSING,
    fdictSnapshotExpectedManifest,
    fdictVerifyRerunOutputs,
)


T_FIXTURE_PATHS = ("Analyse/first.txt", "Analyse/second.txt", "Plot/third.txt")


def _fpathBuildTree(tmp_path):
    """A repository with three pinned files and a manifest over them."""
    pathRepo = tmp_path / "repo"
    for iIndex, sRelative in enumerate(T_FIXTURE_PATHS):
        fnWriteText(str(pathRepo), sRelative, f"content {iIndex}\n")
    fnWriteManifest(str(pathRepo), list(T_FIXTURE_PATHS))
    return pathRepo


def _fdictVerifyAfter(pathRepo, fnMutate, listCarriedPaths=()):
    """Freeze the manifest, apply a mutation, and grade the tree."""
    filesRepo = HostRepoFiles(str(pathRepo))
    dictExpected = fdictSnapshotExpectedManifest(filesRepo)
    fnMutate()
    return fdictVerifyRerunOutputs(
        filesRepo, True, dictExpected, listCarriedPaths,
    )


def _fsSha256Of(pathFile):
    return hashlib.sha256(pathFile.read_bytes()).hexdigest()


# ---------------------------------------------------------------------
# The per-file outcomes are the authority
# ---------------------------------------------------------------------


@pytest.mark.falsification
def test_the_outcomes_follow_the_frozen_manifest_order_line_for_line(tmp_path):
    """One record per pinned entry, in the manifest's order, whatever happened.

    Kills: building the outcomes in reverse order.
    """
    pathRepo = _fpathBuildTree(tmp_path)

    def fnMutate():
        (pathRepo / "Analyse" / "second.txt").write_text("changed\n")
        (pathRepo / "Plot" / "third.txt").unlink()

    dictOutcome = _fdictVerifyAfter(pathRepo, fnMutate)
    listPaths = [dictFile["sPath"] for dictFile in dictOutcome["listFileOutcomes"]]
    assert listPaths == list(T_FIXTURE_PATHS)
    assert [dictFile["sStatus"] for dictFile in dictOutcome["listFileOutcomes"]] == [
        S_FILE_MATCHED, S_FILE_DIVERGED, S_FILE_MISSING,
    ]
    dictSecond = dictOutcome["listFileOutcomes"][1]
    assert dictSecond["sObserved"] == _fsSha256Of(pathRepo / "Analyse" / "second.txt")
    assert dictSecond["sExpected"] != dictSecond["sObserved"]
    assert dictOutcome["listFileOutcomes"][2]["sObserved"] is None


@pytest.mark.falsification
def test_every_count_and_list_is_derived_from_the_outcomes(tmp_path):
    """Kills: counting the matched entries from something other than the outcomes."""
    pathRepo = _fpathBuildTree(tmp_path)
    dictOutcome = _fdictVerifyAfter(
        pathRepo,
        lambda: (pathRepo / "Analyse" / "second.txt").write_text("changed\n"),
    )
    listOutcomes = dictOutcome["listFileOutcomes"]
    assert dictOutcome["iOutputHashesTotal"] == len(listOutcomes)
    assert dictOutcome["iOutputHashesMatched"] == sum(
        1 for dictFile in listOutcomes if dictFile["sStatus"] == S_FILE_MATCHED
    )
    assert dictOutcome["listMatchedPaths"] == sorted(
        dictFile["sPath"] for dictFile in listOutcomes
        if dictFile["sStatus"] == S_FILE_MATCHED
    )
    assert dictOutcome["listDivergedHashes"] == ["Analyse/second.txt"]
    assert dictOutcome["bPassed"] is False


def test_a_carried_path_is_hashed_and_listed_but_never_graded(tmp_path):
    pathRepo = _fpathBuildTree(tmp_path)
    dictOutcome = _fdictVerifyAfter(
        pathRepo,
        lambda: (pathRepo / "Plot" / "third.txt").write_text("a human edit\n"),
        listCarriedPaths=["Plot/third.txt"],
    )
    dictCarried = dictOutcome["listFileOutcomes"][2]
    assert dictCarried["sStatus"] == S_FILE_CARRIED
    assert dictCarried["sObserved"] == _fsSha256Of(pathRepo / "Plot" / "third.txt")
    assert dictOutcome["bPassed"] is True
    assert dictOutcome["iOutputHashesTotal"] == 2
    assert dictOutcome["listCarriedPaths"] == ["Plot/third.txt"]


# ---------------------------------------------------------------------
# The rendered file
# ---------------------------------------------------------------------


def test_the_reproduced_manifest_has_one_header_and_aligns_with_the_pinned_one(
    tmp_path,
):
    pathRepo = _fpathBuildTree(tmp_path)
    listPinnedLines = (pathRepo / "MANIFEST.sha256").read_text().splitlines()

    def fnMutate():
        (pathRepo / "Analyse" / "second.txt").write_text("changed\n")
        (pathRepo / "Plot" / "third.txt").unlink()

    dictOutcome = _fdictVerifyAfter(pathRepo, fnMutate)
    sText = fsRenderReproducedManifest(dictOutcome["listFileOutcomes"])
    listLines = sText.splitlines()
    assert len(listLines) == len(listPinnedLines)
    assert listLines[0].startswith("#") and not listLines[1].startswith("#")
    assert sum(1 for sLine in listLines if sLine.startswith("# SHA-256")) == 1
    for sPinned, sReproduced in zip(listPinnedLines[1:], listLines[1:]):
        sPath = sPinned.split("  ", 1)[1]
        assert sReproduced.endswith("  " + sPath), (sPinned, sReproduced)
    assert listLines[3] == "# MISSING  Plot/third.txt"
    (pathRepo / S_REPRODUCED_MANIFEST_FILENAME).write_text(sText)
    listParsed = flistParseManifestLines(
        HostRepoFiles(str(pathRepo)),
    )
    assert [dictEntry["sPath"] for dictEntry in listParsed] == list(T_FIXTURE_PATHS)
    listReproducedParsed = _flistParseReproduced(pathRepo)
    assert [dictEntry["sPath"] for dictEntry in listReproducedParsed] == [
        "Analyse/first.txt", "Analyse/second.txt",
    ]


def _flistParseReproduced(pathRepo):
    """Parse REPRODUCED.sha256 through the manifest parser, by renaming."""
    pathParse = pathRepo.parent / "parse"
    pathParse.mkdir(exist_ok=True)
    shutil.copy(
        pathRepo / S_REPRODUCED_MANIFEST_FILENAME, pathParse / "MANIFEST.sha256",
    )
    return flistParseManifestLines(HostRepoFiles(str(pathParse)))


def test_a_path_with_a_newline_stays_on_one_line_when_missing():
    sText = fsRenderReproducedManifest([
        {"sPath": "odd\nname", "sExpected": "aa", "sObserved": None,
         "sStatus": S_FILE_MISSING},
        {"sPath": "next", "sExpected": "bb", "sObserved": "bb",
         "sStatus": S_FILE_MATCHED},
    ])
    assert sText.splitlines()[1:] == ["# MISSING  odd\\nname", "bb  next"]


def _fsCheckTool():
    if shutil.which("sha256sum"):
        return ["sha256sum", "-c"]
    if shutil.which("shasum"):
        return ["shasum", "-a", "256", "-c"]
    return None


@pytest.mark.skipif(_fsCheckTool() is None, reason="no sha256 check tool on PATH")
def test_a_reproduced_run_verifies_with_the_system_checksum_tool(tmp_path):
    """Every non-comment line is a line ``sha256sum -c`` accepts, on the tree."""
    pathRepo = _fpathBuildTree(tmp_path)
    dictOutcome = _fdictVerifyAfter(pathRepo, lambda: None)
    assert dictOutcome["bPassed"] is True
    (pathRepo / S_REPRODUCED_MANIFEST_FILENAME).write_text(
        fsRenderReproducedManifest(dictOutcome["listFileOutcomes"]),
    )
    tResult = subprocess.run(
        _fsCheckTool() + [S_REPRODUCED_MANIFEST_FILENAME], cwd=str(pathRepo),
        capture_output=True, text=True,
    )
    assert tResult.returncode == 0, tResult.stdout + tResult.stderr


def test_a_reproduced_manifest_carries_no_host_path_and_no_image_id(tmp_path):
    pathRepo = _fpathBuildTree(tmp_path)
    dictOutcome = _fdictVerifyAfter(pathRepo, lambda: None)
    sText = fsRenderReproducedManifest(dictOutcome["listFileOutcomes"])
    assert str(tmp_path) not in sText
    assert "sha256:" not in sText


@pytest.mark.falsification
def test_the_manifest_never_pins_what_a_rerun_writes():
    """Kills: dropping the reproduction-record filter from the collector."""
    dictWorkflow = {"listSteps": [{
        "sStepId": "s", "sName": "Step", "sDirectory": ".",
        "saOutputDataFiles": [
            "result.txt", S_REPRODUCED_MANIFEST_FILENAME,
            S_REPRODUCED_MANIFEST_HISTORY_DIR + "/x.sha256",
            S_REPRODUCTIONS_DIR + "/r.json",
        ],
    }]}
    listPaths = flistCollectCanonicalRepoPaths(dictWorkflow)
    assert "result.txt" in listPaths
    assert not any(
        manifestWriter.fbIsReproductionRecordPath(sPath) for sPath in listPaths
    )


# ---------------------------------------------------------------------
# The record
# ---------------------------------------------------------------------


def test_a_v4_attestation_reads_with_the_three_new_fields_none(tmp_path):
    pathRepo = tmp_path / "repo"
    (pathRepo / ".vaibify").mkdir(parents=True)
    (pathRepo / ".vaibify" / "l3_attestation.json").write_text(json.dumps({
        "iSchemaVersion": 4, "sStatus": "passed",
        "sManifestDigestAtAttestation": "sha256:x", "listDivergedHashes": [],
        "listCarriedPaths": [], "dictRerunFailure": {},
    }))
    dictRead = fdictReadAttestation(str(pathRepo))
    assert dictRead["iSchemaVersion"] == I_SCHEMA_VERSION == 5
    assert dictRead["listFileOutcomes"] is None
    assert dictRead["dictReproductionProvenance"] is None
    assert dictRead["sReproducedManifestPath"] is None


def test_a_fresh_attestation_carries_the_outcomes_the_provenance_and_the_path():
    dictAttestation = fdictBuildAttestation(
        "passed", "sha256:m", "img@sha256:i", 1.0, 1, 1,
        listFileOutcomes=[{"sPath": "a", "sExpected": "x", "sObserved": "x",
                           "sStatus": S_FILE_MATCHED}],
        dictReproductionProvenance={"sObtainedFrom": "local"},
        sReproducedManifestPath=S_REPRODUCED_MANIFEST_FILENAME,
        sAttestedAtUtc="2026-09-11T10:00:00Z",
    )
    assert dictAttestation["listFileOutcomes"][0]["sObserved"] == "x"
    assert dictAttestation["dictReproductionProvenance"] == {"sObtainedFrom": "local"}
    assert dictAttestation["sReproducedManifestPath"] == S_REPRODUCED_MANIFEST_FILENAME
    assert dictAttestation["sAttestedAtUtc"] == "2026-09-11T10:00:00Z"


def _fdictOutcomeOn(pathRepo, bPassed=True):
    """A graded outcome over the fixture tree, passed or diverged."""
    dictOutcome = _fdictVerifyAfter(
        pathRepo,
        (lambda: None) if bPassed
        else lambda: (pathRepo / "Analyse" / "second.txt").write_text("x\n"),
    )
    dictOutcome["sManifestDigest"] = "sha256:manifest"
    dictOutcome["sImageDigest"] = "img@sha256:pinned"
    dictOutcome["dictReproductionProvenance"] = {
        "sImageReference": "img@sha256:pinned", "sObtainedFrom": "local",
    }
    return dictOutcome


def _ffnAttestationBuilder(dictOutcome):
    def fdictBuildAttestationAt(sTimestampUtc, sReproducedManifestPath):
        return fdictBuildAttestation(
            "passed" if dictOutcome["bPassed"] else "failed",
            dictOutcome["sManifestDigest"], dictOutcome["sImageDigest"], 1.0,
            dictOutcome["iOutputHashesMatched"], dictOutcome["iOutputHashesTotal"],
            listFileOutcomes=dictOutcome["listFileOutcomes"],
            sReproducedManifestPath=sReproducedManifestPath,
            sAttestedAtUtc=sTimestampUtc,
        )
    return fdictBuildAttestationAt


def test_the_author_lane_writes_the_manifest_its_copy_and_then_the_record(tmp_path):
    pathRepo = _fpathBuildTree(tmp_path)
    dictOutcome = _fdictOutcomeOn(pathRepo)
    listWritten = reproductionRecord.flistWriteVerificationOutcome(
        str(pathRepo), reproductionRecord.S_RECORD_KIND_ATTESTATION,
        dictOutcome, 1.0, {}, _ffnAttestationBuilder(dictOutcome),
    )
    assert listWritten[1] == S_REPRODUCED_MANIFEST_FILENAME
    assert listWritten[0].startswith(S_REPRODUCED_MANIFEST_HISTORY_DIR + "/")
    assert listWritten[0].endswith("_passed.sha256")
    assert listWritten[2] == ".vaibify/l3_attestation.json"
    for sRelative in listWritten:
        assert (pathRepo / sRelative).is_file(), sRelative
    dictAttestation = fdictReadAttestation(str(pathRepo))
    assert dictAttestation["sReproducedManifestPath"] == S_REPRODUCED_MANIFEST_FILENAME
    assert (pathRepo / listWritten[0]).read_text() == (
        pathRepo / S_REPRODUCED_MANIFEST_FILENAME
    ).read_text()
    assert l3Attestation._fsSanitizeTimestamp(
        dictAttestation["sAttestedAtUtc"],
    ) in listWritten[0]


@pytest.mark.falsification
def test_a_record_never_points_at_a_manifest_that_was_not_written(
    tmp_path, monkeypatch,
):
    """The manifest lands first; a failed manifest write leaves no record.

    Kills: naming the manifest in the record without writing it.
    """
    pathRepo = _fpathBuildTree(tmp_path)
    dictOutcome = _fdictOutcomeOn(pathRepo)
    monkeypatch.setattr(
        reproductionRecord, "fsWriteReproducedManifest",
        lambda *aArgs, **kwargs: (_ for _ in ()).throw(OSError("disk full")),
    )
    with pytest.raises(OSError):
        reproductionRecord.flistWriteVerificationOutcome(
            str(pathRepo), reproductionRecord.S_RECORD_KIND_ATTESTATION,
            dictOutcome, 1.0, {}, _ffnAttestationBuilder(dictOutcome),
        )
    dictAttestation = fdictReadAttestation(str(pathRepo))
    if dictAttestation is not None:
        sNamed = dictAttestation.get("sReproducedManifestPath")
        assert sNamed is None or (pathRepo / sNamed).is_file()


def test_a_rerun_that_compared_nothing_writes_no_manifest_and_names_none(tmp_path):
    pathRepo = _fpathBuildTree(tmp_path)
    dictOutcome = _fdictOutcomeOn(pathRepo)
    dictOutcome["listFileOutcomes"] = []
    listWritten = reproductionRecord.flistWriteVerificationOutcome(
        str(pathRepo), reproductionRecord.S_RECORD_KIND_ATTESTATION,
        dictOutcome, 1.0, {}, _ffnAttestationBuilder(dictOutcome),
    )
    assert listWritten == [".vaibify/l3_attestation.json"]
    assert not (pathRepo / S_REPRODUCED_MANIFEST_FILENAME).exists()
    assert fdictReadAttestation(str(pathRepo))["sReproducedManifestPath"] is None


# ---------------------------------------------------------------------
# The stranger lane's companion file
# ---------------------------------------------------------------------


@pytest.fixture
def pathReports(tmp_path, monkeypatch):
    monkeypatch.setattr(
        reproductionReport, "fsReportsDirectory",
        lambda: str(tmp_path / "reports"),
    )
    return tmp_path / "reports"


def _fdictReport(tmp_path, bWithOutcomes=True):
    pathRepo = _fpathBuildTree(tmp_path)
    dictOutcome = _fdictOutcomeOn(pathRepo)
    if not bWithOutcomes:
        dictOutcome["listFileOutcomes"] = []
    return reproductionReport.fdictBuildReproductionReport(
        {"sKind": "url"}, {"sObtainedFrom": "archive"}, dictOutcome,
        {"sVerdict": "vacuous", "bVacuous": True}, 2.0,
    )


def test_a_report_writes_its_reproduced_manifest_beside_it_and_serves_it(
    tmp_path, pathReports,
):
    dictReport = _fdictReport(tmp_path)
    reproductionReport.fsWriteReproductionReport(dictReport)
    sId = dictReport["sReportId"]
    assert dictReport["sReproducedManifestPath"] == sId + ".sha256"
    assert (pathReports / (sId + ".sha256")).is_file()
    sText = reproductionReport.fsReadReproducedManifestText(sId)
    assert sText.splitlines()[1].endswith("  Analyse/first.txt")
    assert reproductionReport.fdictReadReproductionReport(sId)[
        "sReproducedManifestPath"
    ] == sId + ".sha256"


def test_a_report_with_no_outcomes_names_no_manifest(tmp_path, pathReports):
    dictReport = _fdictReport(tmp_path, bWithOutcomes=False)
    reproductionReport.fsWriteReproductionReport(dictReport)
    assert dictReport["sReproducedManifestPath"] is None
    with pytest.raises(LookupError):
        reproductionReport.fsReadReproducedManifestText(dictReport["sReportId"])


@pytest.mark.falsification
def test_the_sweep_removes_the_companion_with_the_report(tmp_path, pathReports):
    """Kills: sweeping the JSON and leaving the .sha256 behind."""
    dictReport = _fdictReport(tmp_path)
    reproductionReport.fsWriteReproductionReport(dictReport)
    sId = dictReport["sReportId"]
    listRemoved = reproductionReport.flistSweepExpiredReports(fMaxAgeSeconds=-1)
    assert listRemoved == [sId]
    assert sorted(os.listdir(pathReports)) == []


# ---------------------------------------------------------------------
# Whose record: git evidence, through the real git
# ---------------------------------------------------------------------


def _fnRunGit(pathRepo, listArguments, dictEnvironmentExtra=None):
    dictEnvironment = dict(os.environ)
    dictEnvironment["GIT_CONFIG_NOSYSTEM"] = "1"
    dictEnvironment["GIT_CONFIG_GLOBAL"] = os.devnull
    dictEnvironment.update(dictEnvironmentExtra or {})
    subprocess.run(
        ["git", *listArguments], cwd=str(pathRepo), env=dictEnvironment,
        check=True, capture_output=True, text=True,
    )


def _fpathRepositoryWithAttestation(tmp_path, sCommitterEmail, sOwnEmail):
    """A repository whose attestation was committed by ``sCommitterEmail``.

    The receiving identity is ``sOwnEmail``; empty leaves it
    unconfigured. The default branch is pinned so the fixture reads the
    same on a runner defaulting to ``master``.
    """
    pathRepo = _fpathBuildTree(tmp_path)
    _fnRunGit(pathRepo, ["init", "-q"])
    _fnRunGit(pathRepo, ["symbolic-ref", "HEAD", "refs/heads/main"])
    fnWriteAttestation(str(pathRepo), fdictBuildAttestation(
        "passed", "sha256:m", "img@sha256:author", 1.0, 3, 3,
    ))
    dictIdentity = {
        "GIT_AUTHOR_NAME": "Author", "GIT_AUTHOR_EMAIL": sCommitterEmail,
        "GIT_COMMITTER_NAME": "Author", "GIT_COMMITTER_EMAIL": sCommitterEmail,
    }
    _fnRunGit(pathRepo, ["add", "-A"], dictIdentity)
    _fnRunGit(pathRepo, ["commit", "-q", "-m", "author attests"], dictIdentity)
    if sOwnEmail:
        _fnRunGit(pathRepo, ["config", "user.email", sOwnEmail])
        _fnRunGit(pathRepo, ["config", "user.name", "Reproducer"])
    return pathRepo


@pytest.fixture
def fnIsolateGitIdentity(monkeypatch):
    """Keep the host runner from reading the developer's own git config."""
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    monkeypatch.delenv("GIT_COMMITTER_EMAIL", raising=False)
    monkeypatch.delenv("GIT_AUTHOR_EMAIL", raising=False)
    monkeypatch.delenv("EMAIL", raising=False)


@pytest.mark.falsification
def test_a_clone_carrying_another_identitys_attestation_records_a_reproduction(
    tmp_path, fnIsolateGitIdentity,
):
    """The attestation's bytes are untouched; a reproduction record appears.

    Kills: deciding every clone is the author's.
    """
    pathRepo = _fpathRepositoryWithAttestation(
        tmp_path, "author@example.org", "reproducer@example.org",
    )
    baAttestationBefore = (pathRepo / ".vaibify" / "l3_attestation.json").read_bytes()
    ftRunGit = _ffnBuildHostGitRunner(str(pathRepo))
    assert reproductionRecord.fbRepositoryCarriesForeignAttestation(ftRunGit)
    dictOutcome = _fdictOutcomeOn(pathRepo)
    listWritten = reproductionRecord.flistWriteVerificationOutcome(
        str(pathRepo), reproductionRecord.fsRecordKindForRepository(ftRunGit),
        dictOutcome, 1.0, {"sWorkflowName": "Project"},
        _ffnAttestationBuilder(dictOutcome),
    )
    assert (pathRepo / ".vaibify" / "l3_attestation.json").read_bytes() == (
        baAttestationBefore
    )
    sRecordPath = listWritten[-1]
    assert sRecordPath.startswith(S_REPRODUCTIONS_DIR + "/")
    assert sRecordPath.endswith("_reproduced.json")
    dictRecord = json.loads((pathRepo / sRecordPath).read_text())
    assert dictRecord["sVerdict"] == "reproduced"
    assert dictRecord["sReproducedManifestPath"] == S_REPRODUCED_MANIFEST_FILENAME
    assert dictRecord["sRecordedNote"] == reproductionRecord.S_REPRODUCTION_RECORD_NOTE
    assert dictRecord["dictSource"]["sRepositoryName"] == "repo"
    assert str(tmp_path) not in json.dumps(dictRecord)
    listRecords = reproductionRecord.flistReadReproductionRecords(str(pathRepo))
    assert len(listRecords) == 1 and listRecords[0]["sVerdict"] == "reproduced"


def test_the_authors_own_clone_writes_an_attestation_as_before(
    tmp_path, fnIsolateGitIdentity,
):
    pathRepo = _fpathRepositoryWithAttestation(
        tmp_path, "author@example.org", "Author@Example.org",
    )
    ftRunGit = _ffnBuildHostGitRunner(str(pathRepo))
    assert not reproductionRecord.fbRepositoryCarriesForeignAttestation(ftRunGit)
    dictOutcome = _fdictOutcomeOn(pathRepo)
    listWritten = reproductionRecord.flistWriteVerificationOutcome(
        str(pathRepo), reproductionRecord.fsRecordKindForRepository(ftRunGit),
        dictOutcome, 1.0, {}, _ffnAttestationBuilder(dictOutcome),
    )
    assert listWritten[-1] == ".vaibify/l3_attestation.json"
    assert fdictReadAttestation(str(pathRepo))["sImageDigest"] == "img@sha256:pinned"
    assert reproductionRecord.flistReadReproductionRecords(str(pathRepo)) == []


@pytest.mark.falsification
def test_an_unconfigured_identity_counts_as_foreign(tmp_path, fnIsolateGitIdentity):
    """Kills: treating a missing user.email as the author's own."""
    pathRepo = _fpathRepositoryWithAttestation(tmp_path, "author@example.org", "")
    ftRunGit = _ffnBuildHostGitRunner(str(pathRepo))
    assert reproductionRecord.fbRepositoryCarriesForeignAttestation(ftRunGit)


def test_an_attestation_not_tracked_at_head_is_nobodys(tmp_path, fnIsolateGitIdentity):
    pathRepo = _fpathBuildTree(tmp_path)
    _fnRunGit(pathRepo, ["init", "-q"])
    fnWriteAttestation(str(pathRepo), fdictBuildAttestation(
        "passed", "sha256:m", "", 1.0, 3, 3,
    ))
    ftRunGit = _ffnBuildHostGitRunner(str(pathRepo))
    assert not reproductionRecord.fbRepositoryCarriesForeignAttestation(ftRunGit)
    assert not reproductionRecord.fbRepositoryCarriesForeignAttestation(
        _ffnBuildHostGitRunner(str(tmp_path / "not-a-repo")),
    )


# ---------------------------------------------------------------------
# The records are read on the attestation GET only
# ---------------------------------------------------------------------


def _fsetModulesImporting(sName, pathPackage):
    setModules = set()
    for pathFile in pathPackage.rglob("*.py"):
        treeModule = ast.parse(pathFile.read_text(encoding="utf-8"))
        for node in ast.walk(treeModule):
            if isinstance(node, ast.ImportFrom) and sName in (
                node.module or ""
            ):
                setModules.add(str(pathFile))
            elif isinstance(node, ast.ImportFrom) and any(
                alias.name == sName for alias in node.names
            ):
                setModules.add(str(pathFile))
            elif isinstance(node, ast.Import) and any(
                sName in alias.name for alias in node.names
            ):
                setModules.add(str(pathFile))
    return setModules


def test_no_poll_path_gate_reads_the_reproduction_records():
    """The snapshot adapter cannot enumerate; a gate that tried would 500 the poll."""
    import pathlib

    import vaibify

    pathPackage = pathlib.Path(vaibify.__file__).parent
    setImporters = {
        os.path.relpath(sPath, str(pathPackage))
        for sPath in _fsetModulesImporting("reproductionRecord", pathPackage)
    }
    assert setImporters == {
        "gui/routes/reproducibilityRoutes.py", "cli/commandReproduce.py",
    }
    for sGate in ("reproducibility/levelGates.py", "gui/fileStatusManager.py",
                  "gui/pipelineServer.py"):
        assert S_REPRODUCTIONS_DIR not in (pathPackage / sGate).read_text()
