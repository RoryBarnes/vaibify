"""Mutation-coverage tests for ``falsificationAttestation.py``.

Each test closes a specific hole a mutation would open: the
N/A-never-green applicability gate, the empty-digest freshness guard,
the kill-rate numerator, and the missing-file digest collapse. They
assert the guarantee, so they pass on the unmutated module and fail
under the corresponding mutation (recorded in
``tests/falsificationRegistry.py`` and re-confirmable via
``tools/reconfirmFalsification.py``).
"""

import json

import pytest

from vaibify.reproducibility.falsificationAttestation import (
    S_STATUS_ATTAINED,
    fbFalsificationRecordCurrent,
    fdictBuildFalsificationRecord,
    fdictBuildFalsificationStatus,
    fdictClassifyFalsificationApplicability,
    flistFalsificationDigestPaths,
    fnWriteFalsificationRecord,
    fsCurrentFalsificationDigest,
)

pytestmark = pytest.mark.falsification


S_STEP_DIRECTORY = "analysisStage"
S_SCRIPT_NAME = "computeSummary.py"


def _fdictBuildStepRepo(tmp_path, sClassification):
    """Create a realistic step layout on disk and return its step dict."""
    pathTests = tmp_path / S_STEP_DIRECTORY / "tests"
    pathTests.mkdir(parents=True)
    (tmp_path / S_STEP_DIRECTORY / S_SCRIPT_NAME).write_text(
        "print(2.0 + 3.0)\n",
    )
    (pathTests / "test_quantitative.py").write_text(
        "def test_summary_value():\n    assert True\n",
    )
    (pathTests / "quantitative_standards.json").write_text(json.dumps({
        "fDefaultRtol": 1.0e-6,
        "sStochasticityClassification": sClassification,
        "listStandards": [{"sName": "fMeanValue", "fValue": 5.0}],
    }))
    return {
        "sDirectory": S_STEP_DIRECTORY,
        "saDataCommands": [f"python {S_SCRIPT_NAME}"],
    }


def test_na_step_never_presents_current_record(tmp_path):
    """A non-applicable (stochastic) step must never present a current
    attestation, even when an attained record with a genuinely
    matching digest sits on disk — the live applicability gate is the
    only thing standing between that record and a green badge.

    Kills: In fdictBuildFalsificationStatus, replace the gate
    `if dictApplicability["bApplicable"]:` with `if True:` so the
    on-disk record's digest match alone decides bRecordCurrent.
    """
    dictStep = _fdictBuildStepRepo(tmp_path, "stochastic_unseeded")
    dictVerdict = fdictClassifyFalsificationApplicability(
        dictStep, str(tmp_path),
    )
    assert dictVerdict["bApplicable"] is False
    sDigest = fsCurrentFalsificationDigest(
        str(tmp_path), flistFalsificationDigestPaths(dictVerdict),
    )
    assert sDigest.startswith("sha256:")
    fnWriteFalsificationRecord(
        str(tmp_path), S_STEP_DIRECTORY,
        fdictBuildFalsificationRecord(
            S_STATUS_ATTAINED, sDigest, "deterministic", 12, 12, 0,
        ),
    )
    dictStatus = fdictBuildFalsificationStatus(dictStep, str(tmp_path))
    assert dictStatus["bRecordCurrent"] is False


def test_empty_digest_record_is_never_current(tmp_path):
    """An attained record with an empty digest, checked against files
    that do not exist (live digest also ``""``), must not read as
    fresh — ``"" == ""`` is an unverifiable identity, not a match.

    Kills: In fbFalsificationRecordCurrent, delete the empty-digest
    guard `if not sRecorded: return False`.
    """
    dictRecord = fdictBuildFalsificationRecord(
        S_STATUS_ATTAINED, "", "deterministic", 5, 5, 0,
    )
    assert fbFalsificationRecordCurrent(
        str(tmp_path), dictRecord, ["missingDirectory/missing.py"],
    ) is False


def test_kill_rate_numerator_is_the_killed_count():
    """The kill-rate is killed over graded total; with a record where
    killed and survived differ, a swapped numerator is observable.

    Kills: In fdictBuildFalsificationRecord, compute fKillRate from
    int(iMutantsSurvived) instead of int(iMutantsKilled).
    """
    dictRecord = fdictBuildFalsificationRecord(
        S_STATUS_ATTAINED, "sha256:abc", "deterministic", 10, 3, 7,
    )
    assert dictRecord["fKillRate"] == pytest.approx(0.3)
    assert dictRecord["iMutantsKilled"] == 3
    assert dictRecord["iMutantsSurvived"] == 7


def test_record_defaults_report_zero_duration():
    """An unmeasured run must record exactly 0.0 seconds, never a
    fabricated positive duration — the manuscript cites this number.

    Kills: In fdictBuildFalsificationRecord, replace the
    ``fDurationSeconds=0.0`` default with a nonzero constant.
    """
    dictRecord = fdictBuildFalsificationRecord(
        S_STATUS_ATTAINED, "sha256:abc", "deterministic", 10, 3, 7,
    )
    assert dictRecord["fDurationSeconds"] == 0.0


def test_digest_collapses_when_any_covered_file_is_missing(tmp_path):
    """The combined digest must fail closed to ``""`` when any covered
    file cannot be hashed; a partial digest over the surviving files
    would let a record stay 'fresh' after a script is deleted.

    Kills: In fsCurrentFalsificationDigest, replace the missing-hash
    `return ""` with `continue` so the digest is computed over only
    the files that still exist.
    """
    (tmp_path / "presentScript.py").write_text("print(1.0)\n")
    sDigest = fsCurrentFalsificationDigest(
        str(tmp_path), ["presentScript.py", "missingScript.py"],
    )
    assert sDigest == ""
