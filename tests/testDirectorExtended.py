"""Extended tests for vaibify.gui.director — TeeWriter, execution, banners."""

import io
import os
import sys
import tempfile

import pytest
from unittest.mock import MagicMock, patch

from vaibify.gui.director import (
    TeeWriter,
    fdictBuildGlobalVariables,
    fnExecuteStep,
    fnRegisterStepOutputs,
    _fnRegisterFiles,
    fnPrintStepBanner,
    fnPrintSummary,
    fnRunVerifyOnly,
    _fbSkipAndRegisterStep,
    _fbExecuteOneStep,
    fnConfigureEnvironment,
    fnSetupLogFile,
)


# -----------------------------------------------------------------------
# TeeWriter
# -----------------------------------------------------------------------


def test_TeeWriter_write_duplicates_to_both():
    streamTerminal = io.StringIO()
    fileLog = io.StringIO()
    tee = TeeWriter(streamTerminal, fileLog)
    iLength = tee.write("hello")
    assert iLength == 5
    assert streamTerminal.getvalue() == "hello"
    assert fileLog.getvalue() == "hello"


def test_TeeWriter_flush_calls_both():
    streamTerminal = MagicMock()
    fileLog = MagicMock()
    tee = TeeWriter(streamTerminal, fileLog)
    tee.flush()
    streamTerminal.flush.assert_called_once()
    fileLog.flush.assert_called_once()


# -----------------------------------------------------------------------
# fdictBuildGlobalVariables
# -----------------------------------------------------------------------


def test_fdictBuildGlobalVariables_defaults():
    dictWorkflow = {}
    with tempfile.TemporaryDirectory() as sTmpDir:
        dictVars = fdictBuildGlobalVariables(dictWorkflow, sTmpDir)
        assert "sPlotDirectory" in dictVars
        assert dictVars["sFigureType"] == "pdf"
        assert dictVars["iNumberOfCores"] >= 1


def test_fdictBuildGlobalVariables_custom_plot_dir():
    dictWorkflow = {"sPlotDirectory": "Figures"}
    with tempfile.TemporaryDirectory() as sTmpDir:
        dictVars = fdictBuildGlobalVariables(dictWorkflow, sTmpDir)
        assert dictVars["sPlotDirectory"].endswith("Figures")


def test_fdictBuildGlobalVariables_strips_vaibify_suffix():
    dictWorkflow = {}
    with tempfile.TemporaryDirectory() as sTmpDir:
        sRoot = os.path.join(sTmpDir, ".vaibify", "workflows")
        os.makedirs(sRoot, exist_ok=True)
        dictVars = fdictBuildGlobalVariables(dictWorkflow, sRoot)
        assert dictVars["sRepoRoot"] == sTmpDir


def test_fdictBuildGlobalVariables_strips_vaibify_only():
    dictWorkflow = {}
    with tempfile.TemporaryDirectory() as sTmpDir:
        sRoot = os.path.join(sTmpDir, ".vaibify")
        os.makedirs(sRoot, exist_ok=True)
        dictVars = fdictBuildGlobalVariables(dictWorkflow, sRoot)
        assert dictVars["sRepoRoot"] == sTmpDir


# -----------------------------------------------------------------------
# fnExecuteStep — mocked subprocess
# -----------------------------------------------------------------------


@patch("vaibify.gui.director.fnExecuteCommand")
def test_fnExecuteStep_plot_only(mockExecute):
    dictStep = {
        "sName": "Test",
        "sDirectory": ".",
        "saDataCommands": ["python data.py"],
        "saPlotCommands": ["python plot.py"],
        "saPlotFiles": [],
        "bPlotOnly": True,
    }
    with tempfile.TemporaryDirectory() as sTmpDir:
        dictVars = {"sFigureType": "pdf"}
        fnExecuteStep(dictStep, dictVars, sTmpDir)
        listCalls = [c[0][0] for c in mockExecute.call_args_list]
        assert "python data.py" not in listCalls
        assert "python plot.py" in listCalls


@patch("vaibify.gui.director.fnExecuteCommand")
def test_fnExecuteStep_runs_data_when_not_plot_only(mockExecute):
    dictStep = {
        "sName": "Test",
        "sDirectory": ".",
        "saDataCommands": ["python data.py"],
        "saPlotCommands": ["python plot.py"],
        "saPlotFiles": [],
        "bPlotOnly": False,
    }
    with tempfile.TemporaryDirectory() as sTmpDir:
        dictVars = {"sFigureType": "pdf"}
        fnExecuteStep(dictStep, dictVars, sTmpDir)
        listCalls = [c[0][0] for c in mockExecute.call_args_list]
        assert "python data.py" in listCalls
        assert "python plot.py" in listCalls


# -----------------------------------------------------------------------
# fnRegisterStepOutputs and _fnRegisterFiles
# -----------------------------------------------------------------------


def test_fnRegisterStepOutputs_registers_plot_files():
    with tempfile.TemporaryDirectory() as sTmpDir:
        sFilePath = os.path.join(sTmpDir, "plot.pdf")
        with open(sFilePath, "wb") as fh:
            fh.write(b"x" * 2048)
        dictStep = {
            "sDirectory": ".",
            "saPlotFiles": ["plot.pdf"],
        }
        dictVars = {}
        fnRegisterStepOutputs(
            dictStep, dictVars, "Step01", sTmpDir)
        assert "Step01.plot" in dictVars


def test_fnRegisterStepOutputs_registers_data_files():
    with tempfile.TemporaryDirectory() as sTmpDir:
        sFilePath = os.path.join(sTmpDir, "data.npy")
        with open(sFilePath, "wb") as fh:
            fh.write(b"x" * 2048)
        dictStep = {
            "sDirectory": ".",
            "saDataFiles": ["data.npy"],
            "saPlotFiles": [],
        }
        dictVars = {}
        fnRegisterStepOutputs(
            dictStep, dictVars, "Step01", sTmpDir)
        assert "Step01.data" in dictVars


def test_fnRegisterFiles_raises_on_missing_file():
    with tempfile.TemporaryDirectory() as sTmpDir:
        dictVars = {}
        with pytest.raises(FileNotFoundError):
            _fnRegisterFiles(
                ["missing.pdf"], dictVars, "Step01", sTmpDir)


def test_fnRegisterFiles_warns_on_small_file(capsys):
    with tempfile.TemporaryDirectory() as sTmpDir:
        sFilePath = os.path.join(sTmpDir, "tiny.pdf")
        with open(sFilePath, "wb") as fh:
            fh.write(b"x" * 10)
        dictVars = {}
        _fnRegisterFiles(
            ["tiny.pdf"], dictVars, "Step01", sTmpDir)
        sCaptured = capsys.readouterr().out
        assert "WARNING" in sCaptured


# -----------------------------------------------------------------------
# fnPrintStepBanner
# -----------------------------------------------------------------------


def test_fnPrintStepBanner_outputs_step_info(capsys):
    dictStep = {
        "sName": "Analysis",
        "sDirectory": "/workspace/analysis",
        "bPlotOnly": False,
    }
    dictVars = {"sFigureType": "png"}
    fnPrintStepBanner("Step01", dictStep, dictVars)
    sCaptured = capsys.readouterr().out
    assert "Step01" in sCaptured
    assert "Analysis" in sCaptured
    assert "png" in sCaptured


# -----------------------------------------------------------------------
# fnPrintSummary
# -----------------------------------------------------------------------


def test_fnPrintSummary_shows_pass_fail(capsys):
    listResults = [
        ("Step01", "A", True, ""),
        ("Step02", "B", False, "Error happened"),
    ]
    fnPrintSummary(listResults)
    sCaptured = capsys.readouterr().out
    assert "PASS" in sCaptured
    assert "FAIL" in sCaptured
    assert "1 passed" in sCaptured
    assert "1 failed" in sCaptured


def test_fnPrintSummary_all_pass(capsys):
    listResults = [("Step01", "A", True, "")]
    fnPrintSummary(listResults)
    sCaptured = capsys.readouterr().out
    assert "1 passed" in sCaptured
    assert "0 failed" in sCaptured


# -----------------------------------------------------------------------
# fnRunVerifyOnly
# -----------------------------------------------------------------------


def test_fnRunVerifyOnly_all_present():
    with tempfile.TemporaryDirectory() as sTmpDir:
        sFilePath = os.path.join(sTmpDir, "out.pdf")
        with open(sFilePath, "wb") as fh:
            fh.write(b"x" * 2048)
        dictWorkflow = {
            "listSteps": [{
                "sName": "Step",
                "sDirectory": ".",
                "saPlotFiles": ["out.pdf"],
                "saPlotCommands": [],
            }]
        }
        dictVars = {}
        bResult = fnRunVerifyOnly(
            dictWorkflow, dictVars, sTmpDir)
        assert bResult is True


def test_fnRunVerifyOnly_missing_file():
    with tempfile.TemporaryDirectory() as sTmpDir:
        dictWorkflow = {
            "listSteps": [{
                "sName": "Step",
                "sDirectory": ".",
                "saPlotFiles": ["missing.pdf"],
                "saPlotCommands": [],
            }]
        }
        dictVars = {}
        bResult = fnRunVerifyOnly(
            dictWorkflow, dictVars, sTmpDir)
        assert bResult is False


def test_fnRunVerifyOnly_skips_disabled():
    with tempfile.TemporaryDirectory() as sTmpDir:
        dictWorkflow = {
            "listSteps": [{
                "sName": "Step",
                "sDirectory": ".",
                "saPlotFiles": ["missing.pdf"],
                "saPlotCommands": [],
                "bEnabled": False,
            }]
        }
        dictVars = {}
        bResult = fnRunVerifyOnly(
            dictWorkflow, dictVars, sTmpDir)
        assert bResult is True


# -----------------------------------------------------------------------
# _fbSkipAndRegisterStep
# -----------------------------------------------------------------------


def test_fbSkipAndRegisterStep_success():
    with tempfile.TemporaryDirectory() as sTmpDir:
        sFilePath = os.path.join(sTmpDir, "out.pdf")
        with open(sFilePath, "wb") as fh:
            fh.write(b"x" * 2048)
        dictStep = {
            "sName": "Step",
            "sDirectory": ".",
            "saPlotFiles": ["out.pdf"],
        }
        listResults = []
        dictVars = {}
        bOk = _fbSkipAndRegisterStep(
            dictStep, dictVars, "Step01", sTmpDir, listResults)
        assert bOk is True
        assert len(listResults) == 1
        assert listResults[0][2] is True


def test_fbSkipAndRegisterStep_failure():
    with tempfile.TemporaryDirectory() as sTmpDir:
        dictStep = {
            "sName": "Step",
            "sDirectory": ".",
            "saPlotFiles": ["missing.pdf"],
        }
        listResults = []
        dictVars = {}
        bOk = _fbSkipAndRegisterStep(
            dictStep, dictVars, "Step01", sTmpDir, listResults)
        assert bOk is False
        assert listResults[0][2] is False


# -----------------------------------------------------------------------
# _fbExecuteOneStep
# -----------------------------------------------------------------------


@patch("vaibify.gui.director.fnExecuteStep")
@patch("vaibify.gui.director.fnRegisterStepOutputs")
def test_fbExecuteOneStep_success(mockRegister, mockExecute):
    dictStep = {
        "sName": "Step",
        "sDirectory": ".",
        "saPlotCommands": [],
        "saPlotFiles": [],
        "bPlotOnly": True,
    }
    listResults = []
    dictVars = {"sFigureType": "pdf"}
    bOk = _fbExecuteOneStep(
        dictStep, dictVars, "Step01", "/tmp", listResults)
    assert bOk is True
    assert listResults[0][2] is True


@patch("vaibify.gui.director.fnExecuteStep",
       side_effect=RuntimeError("boom"))
def test_fbExecuteOneStep_failure(mockExecute):
    dictStep = {
        "sName": "Step",
        "sDirectory": ".",
        "saPlotCommands": [],
        "saPlotFiles": [],
        "bPlotOnly": True,
    }
    listResults = []
    dictVars = {"sFigureType": "pdf"}
    bOk = _fbExecuteOneStep(
        dictStep, dictVars, "Step01", "/tmp", listResults)
    assert bOk is False
    assert "boom" in listResults[0][3]


# -----------------------------------------------------------------------
# fnConfigureEnvironment
# -----------------------------------------------------------------------


def test_fnConfigureEnvironment_prepends_path():
    with tempfile.TemporaryDirectory() as sTmpDir:
        dictWorkflow = {"listBinaryDirectories": [sTmpDir]}
        sOriginalPath = os.environ.get("PATH", "")
        fnConfigureEnvironment(dictWorkflow, sTmpDir)
        assert sTmpDir in os.environ["PATH"]
        os.environ["PATH"] = sOriginalPath


def test_fnConfigureEnvironment_ignores_missing_dirs():
    sOriginalPath = os.environ.get("PATH", "")
    dictWorkflow = {
        "listBinaryDirectories": ["/nonexistent_dir_xyz"]
    }
    fnConfigureEnvironment(dictWorkflow, "/tmp")
    assert "/nonexistent_dir_xyz" not in os.environ["PATH"]
    os.environ["PATH"] = sOriginalPath


def test_fnConfigureEnvironment_deduplicates_vpBinDir():
    with tempfile.TemporaryDirectory() as sTmpDir:
        dictWorkflow = {
            "listBinaryDirectories": [sTmpDir],
            "sVplanetBinaryDirectory": sTmpDir,
        }
        sOriginalPath = os.environ.get("PATH", "")
        fnConfigureEnvironment(dictWorkflow, sTmpDir)
        iCount = os.environ["PATH"].split(":").count(sTmpDir)
        assert iCount == 1
        os.environ["PATH"] = sOriginalPath


# -----------------------------------------------------------------------
# fnSetupLogFile
# -----------------------------------------------------------------------


def test_fnSetupLogFile_creates_log():
    with tempfile.TemporaryDirectory() as sTmpDir:
        sLogPath = os.path.join(sTmpDir, "logs", "test.log")
        fileLog = fnSetupLogFile(sLogPath)
        assert os.path.isfile(sLogPath)
        sys.stdout = sys.__stdout__
        sys.stderr = sys.__stderr__
        fileLog.close()
