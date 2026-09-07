"""The reproduction report is the reproducer's artefact, never the author's.

It has its own schema, lives apart from staging, is read by no Level
gate, and speaks the verdict vocabulary a stranger earned -- reproduced,
reproduced under emulation, diverged, no verdict -- never "attested".
"""

import json
import os
import time

import pytest

from vaibify.reproducibility import reproductionReport
from vaibify.reproducibility.reproductionReport import (
    S_VERDICT_DIVERGED,
    S_VERDICT_NO_VERDICT,
    S_VERDICT_REPRODUCED,
    fdictBuildReproductionReport,
    fdictReadReproductionReport,
    flistSweepExpiredReports,
    fsRenderVerdict,
    fsReportsDirectory,
    fsWriteReproductionReport,
)


REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _fdictSource():
    return {
        "sKind": "git-url", "sRepositoryName": "project",
        "sResolvedCommit": "a" * 40,
        "sRemoteUrl": "https://host.example/group/project.git",
        "sWorkflowName": "Demo",
        "sWorkflowPath": ".vaibify/projects/project.json",
        "sPinnedImageReference": "registry.example/demo@sha256:" + "b" * 64,
        "sRequiredArchitecture": "amd64", "bDepositOnRecord": False,
        "sDepositVersionDoi": "", "sManifestDigest": "sha256:" + "c" * 64,
    }


def _fdictAcquired(bEmulated=False):
    return {
        "sImageReference": "sha256:" + "e" * 64, "sObtainedFrom": "archive",
        "sRequiredPlatform": "linux/amd64", "sObtainedPlatform": "linux/amd64",
        "sDaemonArchitecture": "arm64" if bEmulated else "amd64",
        "bEmulated": bEmulated,
        "listAttempts": [{"sLink": "registry pull", "bSucceeded": False,
                          "sDetail": "manifest unknown"}],
    }


def _fdictOutcome(bPassed=True, bRerunAttempted=True):
    return {
        "bPassed": bPassed, "bRerunAttempted": bRerunAttempted,
        "iOutputHashesMatched": 3 if bPassed else 2, "iOutputHashesTotal": 3,
        "listDivergedHashes": [] if bPassed else ["MakeNumbers/numbers.txt"],
        "listCarriedPaths": ["AIDeclaration/declaration.md"],
        "dictRerunFailure": {}, "sManifestDigest": "sha256:" + "d" * 64,
        "sShadowTeardown": "destroyed",
    }


def test_the_verdict_vocabulary_never_says_attested():
    dictRecheck = {"sVerdict": "vacuous", "sReason": "loaded", "bVacuous": True}
    dictReproduced = fdictBuildReproductionReport(
        _fdictSource(), _fdictAcquired(), _fdictOutcome(), dictRecheck, 1.5,
    )
    assert dictReproduced["sVerdict"] == S_VERDICT_REPRODUCED
    assert fsRenderVerdict(dictReproduced) == "reproduced"
    dictEmulated = fdictBuildReproductionReport(
        _fdictSource(), _fdictAcquired(bEmulated=True), _fdictOutcome(),
        dictRecheck, 1.5,
    )
    assert fsRenderVerdict(dictEmulated) == (
        "reproduced under emulation (linux/amd64 image on a arm64 host)"
    )
    dictDiverged = fdictBuildReproductionReport(
        _fdictSource(), _fdictAcquired(), _fdictOutcome(bPassed=False),
        dictRecheck, 1.5,
    )
    assert dictDiverged["sVerdict"] == S_VERDICT_DIVERGED
    dictNoVerdict = fdictBuildReproductionReport(
        _fdictSource(), _fdictAcquired(),
        {**_fdictOutcome(bPassed=False, bRerunAttempted=False),
         "listDivergedHashes": ["the rerun never started: no image"]},
        dictRecheck, 1.5,
    )
    assert dictNoVerdict["sVerdict"] == S_VERDICT_NO_VERDICT
    assert fsRenderVerdict(dictNoVerdict).startswith("no verdict: the rerun")
    for dictReport in (dictReproduced, dictEmulated, dictDiverged, dictNoVerdict):
        assert "attest" not in json.dumps(dictReport).lower()


@pytest.mark.falsification
def test_the_carried_paths_ride_beside_the_counts():
    """Kills: dropping ``listCarriedPaths`` from the report."""
    dictReport = fdictBuildReproductionReport(
        _fdictSource(), _fdictAcquired(), _fdictOutcome(), {}, 0.1,
    )
    assert dictReport["iOutputHashesTotal"] == 3
    assert dictReport["listCarriedPaths"] == ["AIDeclaration/declaration.md"]
    assert dictReport["dictImageRecheck"]["bVacuous"] is False


def test_a_report_carries_only_the_redacted_source_facts():
    dictSource = {**_fdictSource(), "sStagingDirectory": "/home/someone/x"}
    dictReport = fdictBuildReproductionReport(
        dictSource, _fdictAcquired(), _fdictOutcome(), {}, 0.1,
    )
    assert "sStagingDirectory" not in dictReport["dictSource"]
    assert "/home/" not in json.dumps(dictReport)
    assert dictReport["dictPlatform"] == {
        "sRequiredPlatform": "linux/amd64", "sObtainedPlatform": "linux/amd64",
        "sDaemonArchitecture": "amd64", "bEmulated": False,
    }


def test_a_report_is_written_beside_staging_and_read_back_by_id():
    dictReport = fdictBuildReproductionReport(
        _fdictSource(), _fdictAcquired(), _fdictOutcome(), {}, 0.1,
    )
    sPath = fsWriteReproductionReport(dictReport)
    assert os.path.dirname(sPath) == fsReportsDirectory()
    assert os.path.basename(fsReportsDirectory()) == "reports"
    assert fdictReadReproductionReport(dictReport["sReportId"]) == dictReport
    with pytest.raises(LookupError):
        fdictReadReproductionReport("../" + dictReport["sReportId"])
    with pytest.raises(LookupError):
        fdictReadReproductionReport("nobody")


@pytest.mark.falsification
def test_retention_sweeps_only_reports_past_their_age():
    """Kills: sweeping a report younger than the retention."""
    dictOld = fdictBuildReproductionReport(
        _fdictSource(), _fdictAcquired(), _fdictOutcome(), {}, 0.1,
    )
    dictYoung = fdictBuildReproductionReport(
        _fdictSource(), _fdictAcquired(), _fdictOutcome(), {}, 0.1,
    )
    sOldPath = fsWriteReproductionReport(dictOld)
    fsWriteReproductionReport(dictYoung)
    fOld = time.time() - 2 * reproductionReport.F_REPORT_RETENTION_SECONDS
    os.utime(sOldPath, (fOld, fOld))
    assert flistSweepExpiredReports() == [dictOld["sReportId"]]
    assert fdictReadReproductionReport(dictYoung["sReportId"])


def test_no_level_gate_reads_the_report():
    """The report is evidence a stranger produced; the gates never read it."""
    sGates = open(
        os.path.join(REPO_ROOT, "vaibify", "reproducibility", "levelGates.py"),
        encoding="utf-8",
    ).read()
    assert "reproductionReport" not in sGates
    assert "reproductionSource" not in sGates
    assert "imageAcquisition" not in sGates
