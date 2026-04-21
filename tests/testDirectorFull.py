"""Full coverage tests for vaibify.gui.director — execution and pipeline."""

import io
import os
import sys
import tempfile

import pytest
from unittest.mock import patch, MagicMock

from vaibify.gui.director import (
    fnExecuteCommand,
    fnStreamPrefixedOutput,
    fnStreamAndWait,
    _fnRegisterFiles,
    fnRegisterStepOutputs,
    fnRunPipeline,
    _fnRunTestsIfPresent,
    fnDownloadDatasets,
    fdictLoadWorkflow,
)


# -----------------------------------------------------------------------
# fnExecuteCommand — mocked subprocess
# -----------------------------------------------------------------------


@patch("vaibify.gui.director.subprocess.Popen")
def test_fnExecuteCommand_success(mockPopen, tmp_path):
    mockProcess = MagicMock()
    mockProcess.returncode = 0
    mockProcess.stdout = io.StringIO("")
    mockProcess.stderr = io.StringIO("")
    mockProcess.wait.return_value = 0
    mockPopen.return_value = mockProcess
    fnExecuteCommand("echo hello", str(tmp_path), "TestStep")


@patch("vaibify.gui.director.subprocess.Popen")
def test_fnExecuteCommand_failure_raises(mockPopen, tmp_path):
    mockProcess = MagicMock()
    mockProcess.returncode = 1
    mockProcess.stdout = io.StringIO("")
    mockProcess.stderr = io.StringIO("")
    mockProcess.wait.return_value = 1
    mockPopen.return_value = mockProcess
    with pytest.raises(RuntimeError, match="Exit code 1"):
        fnExecuteCommand(
            "false", str(tmp_path), "TestStep",
        )


def test_fnExecuteCommand_bad_directory_raises():
    with pytest.raises(FileNotFoundError, match="does not exist"):
        fnExecuteCommand(
            "echo hello", "/nonexistent_dir_xyz",
            "TestStep",
        )


# -----------------------------------------------------------------------
# fnStreamPrefixedOutput
# -----------------------------------------------------------------------


def test_fnStreamPrefixedOutput_prints_lines(capsys):
    stream = io.StringIO("line1\nline2\n")
    fnStreamPrefixedOutput(stream, "[P]")
    sCaptured = capsys.readouterr().out
    assert "[P] line1" in sCaptured
    assert "[P] line2" in sCaptured


# -----------------------------------------------------------------------
# fnStreamAndWait
# -----------------------------------------------------------------------


def test_fnStreamAndWait_waits_for_process():
    mockProcess = MagicMock()
    mockProcess.stdout = io.StringIO("")
    mockProcess.stderr = io.StringIO("")
    fnStreamAndWait(mockProcess, "[P]")
    mockProcess.wait.assert_called_once()


# -----------------------------------------------------------------------
# _fnRegisterFiles — additional cases
# -----------------------------------------------------------------------


def test_fnRegisterFiles_resolves_variables(tmp_path):
    sFilePath = str(tmp_path / "result.pdf")
    with open(sFilePath, "wb") as fh:
        fh.write(b"x" * 2048)
    dictVars = {"sDir": str(tmp_path)}
    _fnRegisterFiles(
        ["{sDir}/result.pdf"], dictVars,
        "Step01", str(tmp_path),
    )
    assert "Step01.result" in dictVars


def test_fnRegisterFiles_absolute_path(tmp_path):
    sFilePath = str(tmp_path / "abs.pdf")
    with open(sFilePath, "wb") as fh:
        fh.write(b"x" * 2048)
    dictVars = {}
    _fnRegisterFiles(
        [sFilePath], dictVars, "Step01", str(tmp_path),
    )
    assert "Step01.abs" in dictVars


# -----------------------------------------------------------------------
# fnRegisterStepOutputs — with data files
# -----------------------------------------------------------------------


def test_fnRegisterStepOutputs_both_types(tmp_path):
    for sName in ["data.npy", "plot.pdf"]:
        with open(str(tmp_path / sName), "wb") as fh:
            fh.write(b"x" * 2048)
    dictStep = {
        "sDirectory": ".",
        "saDataFiles": ["data.npy"],
        "saPlotFiles": ["plot.pdf"],
    }
    dictVars = {"sRepoRoot": str(tmp_path)}
    fnRegisterStepOutputs(
        dictStep, dictVars, "Step01", str(tmp_path),
    )
    assert "Step01.data" in dictVars
    assert "Step01.plot" in dictVars


# -----------------------------------------------------------------------
# _fnRunTestsIfPresent
# -----------------------------------------------------------------------


@patch("vaibify.gui.director.fnExecuteCommand")
def test_fnRunTestsIfPresent_passes(mockExec, tmp_path):
    dictStep = {
        "sName": "Test",
        "saTestCommands": ["echo test"],
    }
    bResult = _fnRunTestsIfPresent(
        dictStep, {}, str(tmp_path),
    )
    assert bResult is True


@patch(
    "vaibify.gui.director.fnExecuteCommand",
    side_effect=RuntimeError("test failed"),
)
def test_fnRunTestsIfPresent_fails(mockExec, tmp_path):
    dictStep = {
        "sName": "Test",
        "saTestCommands": ["make test"],
    }
    bResult = _fnRunTestsIfPresent(
        dictStep, {}, str(tmp_path),
    )
    assert bResult is False


def test_fnRunTestsIfPresent_no_tests(tmp_path):
    dictStep = {"sName": "Test"}
    bResult = _fnRunTestsIfPresent(dictStep, {}, str(tmp_path))
    assert bResult is True


# -----------------------------------------------------------------------
# fnRunPipeline — mocked execution
# -----------------------------------------------------------------------


@patch("vaibify.gui.director.fnExecuteStep")
@patch("vaibify.gui.director.fnRegisterStepOutputs")
def test_fnRunPipeline_all_pass(mockRegister, mockExec):
    dictWorkflow = {
        "listSteps": [{
            "sName": "Step",
            "sDirectory": ".",
            "saPlotCommands": [],
            "saPlotFiles": [],
            "bPlotOnly": True,
        }],
    }
    dictVars = {"sFigureType": "pdf"}
    bResult = fnRunPipeline(
        dictWorkflow, dictVars, "/tmp",
    )
    assert bResult is True


@patch(
    "vaibify.gui.director.fnExecuteStep",
    side_effect=RuntimeError("boom"),
)
def test_fnRunPipeline_halts_on_failure(mockExec):
    dictWorkflow = {
        "listSteps": [{
            "sName": "Step",
            "sDirectory": ".",
            "saPlotCommands": [],
            "saPlotFiles": [],
            "bPlotOnly": True,
        }],
    }
    dictVars = {"sFigureType": "pdf"}
    bResult = fnRunPipeline(
        dictWorkflow, dictVars, "/tmp",
    )
    assert bResult is False


@patch("vaibify.gui.director.fnExecuteStep")
@patch("vaibify.gui.director.fnRegisterStepOutputs")
def test_fnRunPipeline_skips_disabled(
    mockRegister, mockExec,
):
    dictWorkflow = {
        "listSteps": [{
            "sName": "Disabled",
            "sDirectory": ".",
            "saPlotCommands": [],
            "saPlotFiles": [],
            "bEnabled": False,
        }],
    }
    dictVars = {"sFigureType": "pdf"}
    bResult = fnRunPipeline(
        dictWorkflow, dictVars, "/tmp",
    )
    assert bResult is True
    mockExec.assert_not_called()


@patch("vaibify.gui.director.fnExecuteStep")
@patch("vaibify.gui.director.fnRegisterStepOutputs")
def test_fnRunPipeline_start_step(
    mockRegister, mockExec, tmp_path,
):
    sFilePath = str(tmp_path / "out.pdf")
    with open(sFilePath, "wb") as fh:
        fh.write(b"x" * 2048)
    dictWorkflow = {
        "listSteps": [
            {
                "sName": "First",
                "sDirectory": ".",
                "saPlotCommands": [],
                "saPlotFiles": ["out.pdf"],
                "bPlotOnly": True,
            },
            {
                "sName": "Second",
                "sDirectory": ".",
                "saPlotCommands": [],
                "saPlotFiles": [],
                "bPlotOnly": True,
            },
        ],
    }
    dictVars = {"sFigureType": "pdf"}
    bResult = fnRunPipeline(
        dictWorkflow, dictVars, str(tmp_path),
        iStartStep=2,
    )
    assert bResult is True


# -----------------------------------------------------------------------
# fnDownloadDatasets
# -----------------------------------------------------------------------


def test_fnDownloadDatasets_no_datasets(tmp_path):
    fnDownloadDatasets({}, str(tmp_path))


def test_fnDownloadDatasets_existing_file(tmp_path):
    sFilePath = str(tmp_path / "data.hdf5")
    with open(sFilePath, "wb") as fh:
        fh.write(b"data")
    dictWorkflow = {
        "listDatasets": [{
            "sDoi": "10.5281/zenodo.123",
            "sFileName": "data.hdf5",
            "sDestination": "",
        }],
    }
    fnDownloadDatasets(dictWorkflow, str(tmp_path))


def test_fnDownloadDatasets_skips_incomplete_entries(tmp_path):
    dictWorkflow = {
        "listDatasets": [
            {"sDoi": "", "sFileName": "data.hdf5"},
            {"sDoi": "10.5281/zenodo.123", "sFileName": ""},
        ],
    }
    fnDownloadDatasets(dictWorkflow, str(tmp_path))


# -----------------------------------------------------------------------
# fdictLoadWorkflow
# -----------------------------------------------------------------------


def test_fdictLoadWorkflow_valid(tmp_path):
    import json
    sPath = str(tmp_path / "workflow.json")
    dictWorkflow = {
        "listSteps": [{
            "sName": "Test",
            "sDirectory": "sub",
            "saPlotCommands": ["echo"],
            "saPlotFiles": [],
        }],
    }
    with open(sPath, "w") as fh:
        json.dump(dictWorkflow, fh)
    dictResult = fdictLoadWorkflow(sPath)
    assert "listSteps" in dictResult


def test_fdictLoadWorkflow_missing_exits():
    with pytest.raises(SystemExit):
        fdictLoadWorkflow("/nonexistent_workflow.json")
