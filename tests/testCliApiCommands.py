"""Tests for the new CLI API commands: run, workflow, verify-step, ls, cat, test."""

import json

import pytest
from click.testing import CliRunner
from types import SimpleNamespace
from unittest.mock import patch, MagicMock

from vaibify.cli.commandRun import run, fnCliStatusCallback, _fnValidateStepOptions
from vaibify.cli.commandWorkflow import (
    workflow,
    _fnPrintStepRow,
    _fdictStepDetail,
    _fdictWorkflowSummary,
)
from vaibify.cli.commandVerifyStep import (
    verify_step,
    _fnValidateStatus,
    _fnValidateStepIndex,
    _fnSetUserVerification,
)
from vaibify.cli.commandLs import ls, _fsNormalizePath as _fsNormalizePathLs
from vaibify.cli.commandCat import cat, _fsNormalizePath as _fsNormalizePathCat
from vaibify.cli.commandTest import test, _flistCollectTestCommands


# -----------------------------------------------------------------------
# commandRun: fnCliStatusCallback
# -----------------------------------------------------------------------


def test_fnCliStatusCallback_output_event(capsys):
    fnCliStatusCallback({"sType": "output", "sLine": "hello"})
    assert "hello" in capsys.readouterr().out


def test_fnCliStatusCallback_step_started(capsys):
    fnCliStatusCallback({"sType": "stepStarted", "iStepNumber": 3})
    assert "step 03" in capsys.readouterr().out


def test_fnCliStatusCallback_step_pass(capsys):
    fnCliStatusCallback({"sType": "stepPass", "iStepNumber": 1})
    assert "passed" in capsys.readouterr().out


def test_fnCliStatusCallback_step_fail(capsys):
    fnCliStatusCallback({"sType": "stepFail", "iStepNumber": 2})
    assert "FAILED" in capsys.readouterr().out


def test_fnCliStatusCallback_completed(capsys):
    fnCliStatusCallback({"sType": "completed"})
    assert "successfully" in capsys.readouterr().out


def test_fnCliStatusCallback_failed(capsys):
    fnCliStatusCallback({"sType": "failed", "iExitCode": 1})
    assert "failed" in capsys.readouterr().out


def test_fnValidateStepOptions_both_set_exits():
    with pytest.raises(SystemExit) as excInfo:
        _fnValidateStepOptions(1, 2)
    assert excInfo.value.code == 2


def test_fnValidateStepOptions_neither_set_ok():
    _fnValidateStepOptions(None, None)


def test_fnValidateStepOptions_one_set_ok():
    _fnValidateStepOptions(1, None)
    _fnValidateStepOptions(None, 3)


# -----------------------------------------------------------------------
# commandWorkflow: pure helpers
# -----------------------------------------------------------------------


def _fdictSampleStep():
    return {
        "sName": "Build Data",
        "sDirectory": "step01",
        "bEnabled": True,
        "bPlotOnly": False,
        "bInteractive": False,
        "dictVerification": {"sUser": "passed"},
        "dictRunStats": {"sLastRun": "2025-01-01"},
        "saDataCommands": ["python data.py"],
        "saPlotCommands": ["python plot.py"],
        "saTestCommands": [],
    }


def test_fdictStepDetail_returns_expected_keys():
    dictStep = _fdictSampleStep()
    dictDetail = _fdictStepDetail(0, dictStep)
    assert dictDetail["iNumber"] == 1
    assert dictDetail["sName"] == "Build Data"
    assert dictDetail["bEnabled"] is True


def test_fdictWorkflowSummary_counts_steps():
    dictWorkflow = {
        "sWorkflowName": "test",
        "listSteps": [_fdictSampleStep(), _fdictSampleStep()],
    }
    dictSummary = _fdictWorkflowSummary(dictWorkflow)
    assert dictSummary["iStepCount"] == 2
    assert len(dictSummary["listSteps"]) == 2


def test_fnPrintStepRow_outputs_name(capsys):
    dictStep = _fdictSampleStep()
    _fnPrintStepRow(0, dictStep)
    sOutput = capsys.readouterr().out
    assert "Build Data" in sOutput
    assert "passed" in sOutput


# -----------------------------------------------------------------------
# commandVerifyStep: pure helpers
# -----------------------------------------------------------------------


def test_fnValidateStatus_accepts_valid():
    _fnValidateStatus("passed")
    _fnValidateStatus("failed")
    _fnValidateStatus("untested")


def test_fnValidateStatus_rejects_invalid():
    with pytest.raises(SystemExit) as excInfo:
        _fnValidateStatus("bogus")
    assert excInfo.value.code == 2


def test_fnValidateStepIndex_valid():
    _fnValidateStepIndex(1, 5)
    _fnValidateStepIndex(5, 5)


def test_fnValidateStepIndex_out_of_range():
    with pytest.raises(SystemExit):
        _fnValidateStepIndex(0, 5)
    with pytest.raises(SystemExit):
        _fnValidateStepIndex(6, 5)


def test_fnSetUserVerification_sets_status():
    dictWorkflow = {
        "listSteps": [
            {"sName": "step1", "dictVerification": {"sUser": "untested"}},
        ],
    }
    _fnSetUserVerification(dictWorkflow, 0, "passed")
    assert dictWorkflow["listSteps"][0]["dictVerification"]["sUser"] == "passed"


def test_fnSetUserVerification_creates_dict_if_missing():
    dictWorkflow = {"listSteps": [{"sName": "step1"}]}
    _fnSetUserVerification(dictWorkflow, 0, "failed")
    assert dictWorkflow["listSteps"][0]["dictVerification"]["sUser"] == "failed"


# -----------------------------------------------------------------------
# commandLs: path normalization
# -----------------------------------------------------------------------


def test_fsNormalizePathLs_relative_path():
    assert _fsNormalizePathLs("src/data") == "/workspace/src/data"


def test_fsNormalizePathLs_absolute_path():
    assert _fsNormalizePathLs("/tmp/data") == "/tmp/data"


def test_fsNormalizePathLs_empty_string():
    assert _fsNormalizePathLs("") == ""


# -----------------------------------------------------------------------
# commandCat: path normalization
# -----------------------------------------------------------------------


def test_fsNormalizePathCat_relative_path():
    assert _fsNormalizePathCat("README.md") == "/workspace/README.md"


def test_fsNormalizePathCat_absolute_path():
    assert _fsNormalizePathCat("/etc/hosts") == "/etc/hosts"


# -----------------------------------------------------------------------
# commandTest: test command collection
# -----------------------------------------------------------------------


def test_flistCollectTestCommands_from_legacy():
    dictStep = {
        "saTestCommands": ["pytest tests/"],
        "dictTests": {
            "dictQualitative": {"saCommands": [], "sFilePath": ""},
            "dictQuantitative": {
                "saCommands": [], "sFilePath": "", "sStandardsPath": "",
            },
            "dictIntegrity": {"saCommands": [], "sFilePath": ""},
        },
    }
    listCommands = _flistCollectTestCommands(dictStep)
    assert "pytest tests/" in listCommands


def test_flistCollectTestCommands_from_dict():
    dictStep = {
        "saTestCommands": [],
        "dictTests": {
            "dictQualitative": {
                "saCommands": ["pytest -k qualitative"], "sFilePath": "",
            },
            "dictQuantitative": {
                "saCommands": [], "sFilePath": "", "sStandardsPath": "",
            },
            "dictIntegrity": {
                "saCommands": ["pytest -k integrity"], "sFilePath": "",
            },
        },
    }
    listCommands = _flistCollectTestCommands(dictStep)
    assert "pytest -k qualitative" in listCommands
    assert "pytest -k integrity" in listCommands


def test_flistCollectTestCommands_deduplication():
    dictStep = {
        "saTestCommands": ["pytest tests/"],
        "dictTests": {
            "dictQualitative": {"saCommands": [], "sFilePath": ""},
            "dictQuantitative": {
                "saCommands": [], "sFilePath": "", "sStandardsPath": "",
            },
            "dictIntegrity": {
                "saCommands": ["pytest tests/"], "sFilePath": "",
            },
        },
    }
    listCommands = _flistCollectTestCommands(dictStep)
    assert listCommands.count("pytest tests/") == 1


# -----------------------------------------------------------------------
# CLI registration: commands appear in help
# -----------------------------------------------------------------------


def test_main_has_run_command():
    from vaibify.cli.main import main
    listCommandNames = list(main.commands.keys())
    assert "run" in listCommandNames


def test_main_has_workflow_command():
    from vaibify.cli.main import main
    assert "workflow" in main.commands


def test_main_has_verify_step_command():
    from vaibify.cli.main import main
    assert "verify-step" in main.commands


def test_main_has_ls_command():
    from vaibify.cli.main import main
    assert "ls" in main.commands


def test_main_has_cat_command():
    from vaibify.cli.main import main
    assert "cat" in main.commands


def test_main_has_test_command():
    from vaibify.cli.main import main
    assert "test" in main.commands
