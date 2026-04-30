"""Coverage tests for vaibify.gui.director.fnsParseArguments and main."""

import json
import os

import pytest
from unittest.mock import patch, MagicMock

from vaibify.gui import director


# ----------------------------------------------------------------------
# fnsParseArguments — argparse behavior
# ----------------------------------------------------------------------


def test_parseArguments_required_config(capsys):
    """Missing --config triggers argparse SystemExit(2)."""
    with patch("sys.argv", ["director"]):
        with pytest.raises(SystemExit) as excInfo:
            director.fnsParseArguments()
    assert excInfo.value.code == 2
    sErr = capsys.readouterr().err
    assert "config" in sErr.lower()


def test_parseArguments_help_exits_zero(capsys):
    """--help triggers SystemExit(0) after printing usage."""
    with patch("sys.argv", ["director", "--help"]):
        with pytest.raises(SystemExit) as excInfo:
            director.fnsParseArguments()
    assert excInfo.value.code == 0
    sOut = capsys.readouterr().out
    assert "--config" in sOut
    assert "--verify-only" in sOut


def test_parseArguments_minimal_valid():
    """With --config alone, defaults are applied."""
    with patch("sys.argv", ["director", "--config", "/tmp/wf.json"]):
        args = director.fnsParseArguments()
    assert args.config == "/tmp/wf.json"
    assert args.verify_only is False
    assert args.start_step == 1
    assert "logs" in args.log_dir


def test_parseArguments_all_flags():
    with patch(
        "sys.argv",
        [
            "director", "--config", "/tmp/wf.json",
            "--verify-only", "--start-step", "3",
            "--log-dir", "/custom/logs",
        ],
    ):
        args = director.fnsParseArguments()
    assert args.verify_only is True
    assert args.start_step == 3
    assert args.log_dir == "/custom/logs"


def test_parseArguments_start_step_must_be_int(capsys):
    with patch(
        "sys.argv",
        ["director", "--config", "/tmp/wf.json",
         "--start-step", "notanint"],
    ):
        with pytest.raises(SystemExit) as excInfo:
            director.fnsParseArguments()
    assert excInfo.value.code == 2


# ----------------------------------------------------------------------
# main — orchestration entrypoint
# ----------------------------------------------------------------------


def _fnsWriteWorkflow(tmp_path, dictWorkflow=None):
    """Write a minimal workflow JSON and return its path."""
    if dictWorkflow is None:
        dictWorkflow = {
            "sWorkflowName": "demo",
            "listSteps": [],
        }
    sPath = str(tmp_path / "workflow.json")
    with open(sPath, "w", encoding="utf-8") as fh:
        json.dump(dictWorkflow, fh)
    return sPath


def test_main_success_exits_zero(tmp_path):
    sConfig = _fnsWriteWorkflow(tmp_path)
    sLogDir = str(tmp_path / "logs")
    argsNamespace = MagicMock(
        config=sConfig, verify_only=False,
        start_step=1, log_dir=sLogDir,
    )
    with patch(
        "vaibify.gui.director.fnsParseArguments",
        return_value=argsNamespace,
    ), patch(
        "vaibify.gui.director.fnConfigureEnvironment",
    ), patch(
        "vaibify.gui.director.fnDownloadDatasets",
    ), patch(
        "vaibify.gui.director.fdictBuildGlobalVariables",
        return_value={},
    ), patch(
        "vaibify.gui.director.fnRunPipeline",
        return_value=True,
    ) as mockRun:
        with pytest.raises(SystemExit) as excInfo:
            director.main()
    assert excInfo.value.code == 0
    mockRun.assert_called_once()


def test_main_failure_exits_one(tmp_path):
    sConfig = _fnsWriteWorkflow(tmp_path)
    argsNamespace = MagicMock(
        config=sConfig, verify_only=False,
        start_step=1, log_dir=str(tmp_path / "logs"),
    )
    with patch(
        "vaibify.gui.director.fnsParseArguments",
        return_value=argsNamespace,
    ), patch("vaibify.gui.director.fnConfigureEnvironment"), patch(
        "vaibify.gui.director.fnDownloadDatasets",
    ), patch(
        "vaibify.gui.director.fdictBuildGlobalVariables",
        return_value={},
    ), patch(
        "vaibify.gui.director.fnRunPipeline",
        return_value=False,
    ):
        with pytest.raises(SystemExit) as excInfo:
            director.main()
    assert excInfo.value.code == 1


def test_main_verify_only_branch(tmp_path):
    sConfig = _fnsWriteWorkflow(tmp_path)
    argsNamespace = MagicMock(
        config=sConfig, verify_only=True,
        start_step=1, log_dir=str(tmp_path / "logs"),
    )
    with patch(
        "vaibify.gui.director.fnsParseArguments",
        return_value=argsNamespace,
    ), patch("vaibify.gui.director.fnConfigureEnvironment"), patch(
        "vaibify.gui.director.fnDownloadDatasets",
    ), patch(
        "vaibify.gui.director.fdictBuildGlobalVariables",
        return_value={},
    ), patch(
        "vaibify.gui.director.fnRunVerifyOnly",
        return_value=True,
    ) as mockVerify, patch(
        "vaibify.gui.director.fnRunPipeline",
    ) as mockRun:
        with pytest.raises(SystemExit) as excInfo:
            director.main()
    assert excInfo.value.code == 0
    mockVerify.assert_called_once()
    mockRun.assert_not_called()


def test_main_missing_workflow_exits(tmp_path):
    """fdictLoadWorkflow raises SystemExit for a missing file."""
    argsNamespace = MagicMock(
        config=str(tmp_path / "nonexistent.json"),
        verify_only=False, start_step=1,
        log_dir=str(tmp_path / "logs"),
    )
    with patch(
        "vaibify.gui.director.fnsParseArguments",
        return_value=argsNamespace,
    ):
        with pytest.raises(SystemExit):
            director.main()


def test_main_writes_log_file(tmp_path):
    sConfig = _fnsWriteWorkflow(
        tmp_path, {"sWorkflowName": "log_test", "listSteps": []},
    )
    sLogDir = str(tmp_path / "logs")
    argsNamespace = MagicMock(
        config=sConfig, verify_only=False,
        start_step=1, log_dir=sLogDir,
    )
    with patch(
        "vaibify.gui.director.fnsParseArguments",
        return_value=argsNamespace,
    ), patch("vaibify.gui.director.fnConfigureEnvironment"), patch(
        "vaibify.gui.director.fnDownloadDatasets",
    ), patch(
        "vaibify.gui.director.fdictBuildGlobalVariables",
        return_value={},
    ), patch(
        "vaibify.gui.director.fnRunPipeline",
        return_value=True,
    ):
        with pytest.raises(SystemExit):
            director.main()
    assert os.path.isdir(sLogDir)
    listFiles = os.listdir(sLogDir)
    assert any(s.startswith("log_test_") for s in listFiles)
