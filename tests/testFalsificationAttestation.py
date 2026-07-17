"""Tests for the per-step falsification attestation (non-gating).

Covers the applicability scoping, the digest-keyed staleness
round-trip, the record read/write contract, the cosmic-ray config
builder, and the route-level helpers. The honesty invariant
("not applicable" can never present a current record) is asserted here
with a NON-degenerate adversarial fixture — a hand-written attained
record whose digest genuinely matches the live files — and
kill-confirmed in ``testFalsificationAttestationMutationCoverage.py``.
"""

import json
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI, HTTPException

from vaibify.docker.dockerConnection import ExecResult
from vaibify.reproducibility.falsificationAttestation import (
    S_STATUS_ATTAINED,
    S_STATUS_ERROR,
    fbFalsificationRecordCurrent,
    fdictBuildFalsificationRecord,
    fdictBuildFalsificationStatus,
    fdictClassifyFalsificationApplicability,
    fdictReadFalsificationRecord,
    flistFalsificationDigestPaths,
    fnWriteFalsificationRecord,
    fsBuildCosmicRayConfigToml,
    fsCurrentFalsificationDigest,
    fsFalsificationRecordRelativePath,
)
from vaibify.gui.routes.falsificationRoutes import (
    _fdictParseSummaryOutput,
    _fsBuildMutationTestCommand,
    _fsRequireCosmicRay,
    fnRegisterAll,
)


S_STEP_DIRECTORY = "analysisStage"
S_SCRIPT_NAME = "computeSummary.py"


def _fdictBuildStepRepo(
    tmp_path, sClassification="deterministic", bWithBenchmarks=True,
    listDataCommands=None,
):
    """Create a realistic step layout on disk and return its step dict."""
    pathTests = tmp_path / S_STEP_DIRECTORY / "tests"
    pathTests.mkdir(parents=True)
    (tmp_path / S_STEP_DIRECTORY / S_SCRIPT_NAME).write_text(
        "print(2.0 + 3.0)\n",
    )
    (pathTests / "test_quantitative.py").write_text(
        "def test_summary_value():\n    assert True\n",
    )
    listStandards = (
        [{"sName": "fMeanValue", "fValue": 5.0}]
        if bWithBenchmarks else []
    )
    (pathTests / "quantitative_standards.json").write_text(json.dumps({
        "fDefaultRtol": 1.0e-6,
        "sStochasticityClassification": sClassification,
        "listStandards": listStandards,
    }))
    return {
        "sDirectory": S_STEP_DIRECTORY,
        "saDataCommands": listDataCommands if listDataCommands is not None
        else [f"python {S_SCRIPT_NAME} --output summary.json"],
    }


# -------------------------------------------------------------------
# Applicability scoping
# -------------------------------------------------------------------


def test_applicable_for_deterministic_python_step(tmp_path):
    dictStep = _fdictBuildStepRepo(tmp_path)
    dictVerdict = fdictClassifyFalsificationApplicability(
        dictStep, str(tmp_path),
    )
    assert dictVerdict["bApplicable"] is True
    assert dictVerdict["sReason"] == ""
    assert dictVerdict["sClassification"] == "deterministic"
    assert dictVerdict["listScriptRelPaths"] == [
        f"{S_STEP_DIRECTORY}/{S_SCRIPT_NAME}",
    ]


def test_not_applicable_when_stochastic(tmp_path):
    dictStep = _fdictBuildStepRepo(
        tmp_path, sClassification="stochastic_seeded",
    )
    dictVerdict = fdictClassifyFalsificationApplicability(
        dictStep, str(tmp_path),
    )
    assert dictVerdict["bApplicable"] is False
    assert "stochastic_seeded" in dictVerdict["sReason"]


def test_not_applicable_when_not_python_source(tmp_path):
    dictStep = _fdictBuildStepRepo(
        tmp_path, listDataCommands=["Rscript computeSummary.R"],
    )
    dictVerdict = fdictClassifyFalsificationApplicability(
        dictStep, str(tmp_path),
    )
    assert dictVerdict["bApplicable"] is False
    assert "not Python source" in dictVerdict["sReason"]


def test_not_applicable_without_standards_file(tmp_path):
    dictStep = _fdictBuildStepRepo(tmp_path)
    (tmp_path / S_STEP_DIRECTORY / "tests"
     / "quantitative_standards.json").unlink()
    dictVerdict = fdictClassifyFalsificationApplicability(
        dictStep, str(tmp_path),
    )
    assert dictVerdict["bApplicable"] is False
    assert "no quantitative standards" in dictVerdict["sReason"]


def test_not_applicable_with_unreadable_standards(tmp_path):
    dictStep = _fdictBuildStepRepo(tmp_path)
    (tmp_path / S_STEP_DIRECTORY / "tests"
     / "quantitative_standards.json").write_text("{not json")
    dictVerdict = fdictClassifyFalsificationApplicability(
        dictStep, str(tmp_path),
    )
    assert dictVerdict["bApplicable"] is False
    assert "unreadable" in dictVerdict["sReason"]


def test_not_applicable_without_benchmarks(tmp_path):
    dictStep = _fdictBuildStepRepo(tmp_path, bWithBenchmarks=False)
    dictVerdict = fdictClassifyFalsificationApplicability(
        dictStep, str(tmp_path),
    )
    assert dictVerdict["bApplicable"] is False
    assert "no benchmarks" in dictVerdict["sReason"]


def test_not_applicable_without_quantitative_test_file(tmp_path):
    dictStep = _fdictBuildStepRepo(tmp_path)
    (tmp_path / S_STEP_DIRECTORY / "tests"
     / "test_quantitative.py").unlink()
    dictVerdict = fdictClassifyFalsificationApplicability(
        dictStep, str(tmp_path),
    )
    assert dictVerdict["bApplicable"] is False
    assert "test_quantitative.py" in dictVerdict["sReason"]


def test_token_bearing_script_path_is_excluded(tmp_path):
    dictStep = _fdictBuildStepRepo(
        tmp_path,
        listDataCommands=["python {Step01.generatedScript}"],
    )
    dictVerdict = fdictClassifyFalsificationApplicability(
        dictStep, str(tmp_path),
    )
    assert dictVerdict["listScriptRelPaths"] == []
    assert dictVerdict["bApplicable"] is False


# -------------------------------------------------------------------
# Digest-keyed staleness round-trip
# -------------------------------------------------------------------


def test_record_round_trip_and_digest_staleness(tmp_path):
    dictStep = _fdictBuildStepRepo(tmp_path)
    dictVerdict = fdictClassifyFalsificationApplicability(
        dictStep, str(tmp_path),
    )
    listDigestPaths = flistFalsificationDigestPaths(dictVerdict)
    sDigest = fsCurrentFalsificationDigest(str(tmp_path), listDigestPaths)
    assert sDigest.startswith("sha256:")
    dictRecord = fdictBuildFalsificationRecord(
        S_STATUS_ATTAINED, sDigest, "deterministic", 10, 9, 1,
        listSurvivors=[{"sModulePath": S_SCRIPT_NAME, "iLine": 1,
                        "sOperator": "core/NumberReplacer",
                        "sFunction": ""}],
        sCosmicRayVersion="8.4.6", fDurationSeconds=42.0,
    )
    fnWriteFalsificationRecord(str(tmp_path), S_STEP_DIRECTORY, dictRecord)
    dictRead = fdictReadFalsificationRecord(str(tmp_path), S_STEP_DIRECTORY)
    assert dictRead == dictRecord
    assert fbFalsificationRecordCurrent(
        str(tmp_path), dictRead, listDigestPaths,
    ) is True
    # Any edit to the step's script must invalidate the record.
    (tmp_path / S_STEP_DIRECTORY / S_SCRIPT_NAME).write_text(
        "print(2.0 - 3.0)\n",
    )
    assert fbFalsificationRecordCurrent(
        str(tmp_path), dictRead, listDigestPaths,
    ) is False


def test_standards_edit_invalidates_record(tmp_path):
    dictStep = _fdictBuildStepRepo(tmp_path)
    dictVerdict = fdictClassifyFalsificationApplicability(
        dictStep, str(tmp_path),
    )
    listDigestPaths = flistFalsificationDigestPaths(dictVerdict)
    sDigest = fsCurrentFalsificationDigest(str(tmp_path), listDigestPaths)
    dictRecord = fdictBuildFalsificationRecord(
        S_STATUS_ATTAINED, sDigest, "deterministic", 4, 4, 0,
    )
    (tmp_path / S_STEP_DIRECTORY / "tests"
     / "quantitative_standards.json").write_text(json.dumps({
        "fDefaultRtol": 0.5,
        "sStochasticityClassification": "deterministic",
        "listStandards": [{"sName": "fMeanValue", "fValue": 5.0}],
    }))
    assert fbFalsificationRecordCurrent(
        str(tmp_path), dictRecord, listDigestPaths,
    ) is False


def test_empty_recorded_digest_is_never_current(tmp_path):
    dictRecord = fdictBuildFalsificationRecord(
        S_STATUS_ATTAINED, "", "deterministic", 3, 3, 0,
    )
    # Live digest over a missing file is also "" — the empty-digest
    # guard must not let "" == "" read as fresh.
    assert fbFalsificationRecordCurrent(
        str(tmp_path), dictRecord, ["missingDirectory/missing.py"],
    ) is False


def test_error_status_record_is_never_current(tmp_path):
    dictStep = _fdictBuildStepRepo(tmp_path)
    dictVerdict = fdictClassifyFalsificationApplicability(
        dictStep, str(tmp_path),
    )
    listDigestPaths = flistFalsificationDigestPaths(dictVerdict)
    sDigest = fsCurrentFalsificationDigest(str(tmp_path), listDigestPaths)
    dictRecord = fdictBuildFalsificationRecord(
        S_STATUS_ERROR, sDigest, "deterministic", 0, 0, 0,
        sReason="cosmic-ray exited 1",
    )
    assert fbFalsificationRecordCurrent(
        str(tmp_path), dictRecord, listDigestPaths,
    ) is False


def test_malformed_record_reads_none(tmp_path):
    sRelPath = fsFalsificationRecordRelativePath(S_STEP_DIRECTORY)
    pathRecord = tmp_path / sRelPath
    pathRecord.parent.mkdir(parents=True)
    pathRecord.write_text("[not, a, dict]")
    assert fdictReadFalsificationRecord(
        str(tmp_path), S_STEP_DIRECTORY,
    ) is None


def test_record_path_slugs_nested_step_directory():
    assert fsFalsificationRecordRelativePath("alpha/beta") == (
        ".vaibify/falsification/alpha__beta.json"
    )
    assert fsFalsificationRecordRelativePath("") == (
        ".vaibify/falsification/workflowRoot.json"
    )


# -------------------------------------------------------------------
# The N/A-never-green honesty invariant
# -------------------------------------------------------------------


def test_status_never_green_when_not_applicable(tmp_path):
    """A non-applicable step must never present a current attestation.

    Adversarial, non-degenerate fixture: the on-disk record is
    'attained' AND its digest genuinely matches the live script +
    standards, so only the live applicability gate stands between a
    stochastic step and a green badge.
    """
    dictStep = _fdictBuildStepRepo(
        tmp_path, sClassification="stochastic_unseeded",
    )
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
    assert dictStatus["dictApplicability"]["bApplicable"] is False
    assert dictStatus["bRecordCurrent"] is False


def test_status_current_when_applicable_and_digest_matches(tmp_path):
    """Positive control: green is reachable exactly when honest."""
    dictStep = _fdictBuildStepRepo(tmp_path)
    dictVerdict = fdictClassifyFalsificationApplicability(
        dictStep, str(tmp_path),
    )
    sDigest = fsCurrentFalsificationDigest(
        str(tmp_path), flistFalsificationDigestPaths(dictVerdict),
    )
    fnWriteFalsificationRecord(
        str(tmp_path), S_STEP_DIRECTORY,
        fdictBuildFalsificationRecord(
            S_STATUS_ATTAINED, sDigest, "deterministic", 12, 11, 1,
        ),
    )
    dictStatus = fdictBuildFalsificationStatus(dictStep, str(tmp_path))
    assert dictStatus["dictApplicability"]["bApplicable"] is True
    assert dictStatus["bRecordCurrent"] is True
    assert dictStatus["dictRecord"]["fKillRate"] == pytest.approx(11 / 12)


# -------------------------------------------------------------------
# cosmic-ray config builder
# -------------------------------------------------------------------


def test_config_toml_escapes_paths_and_command():
    sToml = fsBuildCosmicRayConfigToml(
        ['/workspace/repo/step "odd" dir/computeSummary.py'],
        "bash -c 'cd /workspace && python computeSummary.py'",
        fTimeoutSeconds=120.0,
    )
    assert 'step \\"odd\\" dir/computeSummary.py' in sToml
    assert "timeout = 120.0" in sToml
    assert 'name = "local"' in sToml
    assert sToml.startswith("[cosmic-ray]\n")


def test_config_toml_defaults_to_per_mutant_timeout():
    sToml = fsBuildCosmicRayConfigToml(
        ["/workspace/repo/computeSummary.py"], "python -m pytest",
    )
    assert "timeout = 300.0" in sToml


# -------------------------------------------------------------------
# Route-level helpers
# -------------------------------------------------------------------


def test_require_cosmic_ray_returns_version():
    mockDocker = MagicMock()
    mockDocker.texecRunInContainerStreamed = MagicMock(
        return_value=ExecResult(
            iExitCode=0, sStdout="cosmic-ray, version 8.4.6\n",
            sStderr="",
        ),
    )
    assert _fsRequireCosmicRay(mockDocker, "cid-1") == (
        "cosmic-ray, version 8.4.6"
    )


def test_require_cosmic_ray_raises_409_when_absent():
    mockDocker = MagicMock()
    mockDocker.texecRunInContainerStreamed = MagicMock(
        return_value=ExecResult(
            iExitCode=127, sStdout="", sStderr="not found",
        ),
    )
    with pytest.raises(HTTPException) as excInfo:
        _fsRequireCosmicRay(mockDocker, "cid-1")
    assert excInfo.value.status_code == 409
    assert "rebuild the image" in excInfo.value.detail


def test_parse_summary_output_reads_last_json_line():
    resultExec = ExecResult(
        iExitCode=0,
        sStdout="noise line\n"
        + json.dumps({"iMutantsTotal": 7, "iMutantsKilled": 6,
                      "iMutantsSurvived": 1, "listSurvivors": []})
        + "\n",
        sStderr="",
    )
    dictSummary = _fdictParseSummaryOutput(resultExec)
    assert dictSummary["iMutantsTotal"] == 7
    assert dictSummary["iMutantsKilled"] == 6


def test_parse_summary_output_fails_closed():
    assert _fdictParseSummaryOutput(
        ExecResult(iExitCode=1, sStdout="{}", sStderr="boom"),
    ) is None
    assert _fdictParseSummaryOutput(
        ExecResult(iExitCode=0, sStdout="no json here", sStderr=""),
    ) is None


def test_mutation_test_command_resolves_cross_step_tokens():
    dictWorkflow = {
        "sProjectRepoPath": "/workspace/projectRepo",
        "listSteps": [
            {"sDirectory": "samplerStage",
             "saDataCommands": ["python drawSamples.py"],
             "saOutputDataFiles": ["samples.npy"], "saPlotFiles": []},
            {"sDirectory": S_STEP_DIRECTORY,
             "saDataCommands": [
                 "python computeSummary.py --input {Step01.samples}",
             ],
             "saOutputDataFiles": ["summary.json"], "saPlotFiles": []},
        ],
    }
    dictCtx = {
        "variables": MagicMock(return_value={
            "sRepoRoot": "/workspace/projectRepo",
        }),
    }
    sCommand = _fsBuildMutationTestCommand(
        dictCtx, "cid-1", dictWorkflow, dictWorkflow["listSteps"][1],
    )
    assert sCommand.startswith("bash -c ")
    assert "/workspace/projectRepo/samplerStage/samples.npy" in sCommand
    assert "{Step01.samples}" not in sCommand
    assert "cd " in sCommand
    assert "/workspace/projectRepo/analysisStage" in sCommand
    assert "python -m pytest -x -q tests/test_quantitative.py" in sCommand


def test_register_all_registers_both_routes():
    app = FastAPI()
    fnRegisterAll(app, {"require": MagicMock()})
    listPaths = sorted(
        route.path for route in app.routes if hasattr(route, "path")
    )
    assert (
        "/api/steps/{sContainerId}/{iStepIndex}/falsification"
        in listPaths
    )
    assert (
        "/api/steps/{sContainerId}/{iStepIndex}/run-falsification"
        in listPaths
    )
