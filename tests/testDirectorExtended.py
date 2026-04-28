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
        dictVars = {"sRepoRoot": sTmpDir}
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
        dictVars = {"sRepoRoot": sTmpDir}
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
        dictVars = {"sRepoRoot": sTmpDir}
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
                "bRunEnabled": False,
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
        dictVars = {"sRepoRoot": sTmpDir}
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


@patch("os.path.isdir", side_effect=lambda p: True)
@patch("vaibify.gui.director._fnCreateDirectorySilently")
def test_fnConfigureEnvironment_includes_workspace_bin(
    mockCreate, mockIsDir,
):
    sOriginalPath = os.environ.get("PATH", "")
    dictWorkflow = {"listBinaryDirectories": []}
    fnConfigureEnvironment(dictWorkflow, "/tmp")
    assert "/workspace/bin" in os.environ["PATH"]
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


# -----------------------------------------------------------------------
# fnConfigureEnvironment — /workspace/bin on PATH
# -----------------------------------------------------------------------


@patch("os.path.isdir", side_effect=lambda p: True)
@patch("vaibify.gui.director._fnCreateDirectorySilently")
def test_fnConfigureEnvironment_creates_workspace_bin(
    mockCreate, mockIsDir,
):
    """Verify /workspace/bin is created and added to PATH."""
    sOriginalPath = os.environ.get("PATH", "")
    dictWorkflow = {}
    fnConfigureEnvironment(dictWorkflow, "/tmp")
    assert "/workspace/bin" in os.environ["PATH"]
    mockCreate.assert_called_once_with("/workspace/bin")
    os.environ["PATH"] = sOriginalPath


@patch("os.path.isdir", side_effect=lambda p: True)
@patch("vaibify.gui.director._fnCreateDirectorySilently")
def test_fnConfigureEnvironment_no_duplicate_workspace_bin(
    mockCreate, mockIsDir,
):
    """/workspace/bin not duplicated when already in list."""
    sOriginalPath = os.environ.get("PATH", "")
    dictWorkflow = {
        "listBinaryDirectories": ["/workspace/bin"],
    }
    fnConfigureEnvironment(dictWorkflow, "/tmp")
    iCount = os.environ["PATH"].split(":").count("/workspace/bin")
    assert iCount == 1
    os.environ["PATH"] = sOriginalPath


# -----------------------------------------------------------------------
# fnDownloadDatasets (lines 443-459) — mocked, no network
# -----------------------------------------------------------------------


def test_fnDownloadDatasets_no_datasets():
    """Lines 445-447: no listDatasets key is a no-op."""
    from vaibify.gui.director import fnDownloadDatasets
    fnDownloadDatasets({}, "/tmp")


def test_fnDownloadDatasets_skips_incomplete_entries(capsys):
    """Lines 452-453: entries without sDoi or sFileName are skipped."""
    from vaibify.gui.director import fnDownloadDatasets
    dictWorkflow = {"listDatasets": [
        {"sDoi": "", "sFileName": "data.csv"},
        {"sDoi": "10.5281/zenodo.123", "sFileName": ""},
    ]}
    fnDownloadDatasets(dictWorkflow, "/tmp")
    sCaptured = capsys.readouterr().out
    assert "Downloading" not in sCaptured


def test_fnDownloadDatasets_skips_existing_file(capsys):
    """Lines 455-457: existing file prints 'Dataset exists'."""
    from vaibify.gui.director import fnDownloadDatasets
    with tempfile.TemporaryDirectory() as sTmpDir:
        sFilePath = os.path.join(sTmpDir, "data.csv")
        with open(sFilePath, "w") as fh:
            fh.write("x")
        dictWorkflow = {"listDatasets": [{
            "sDoi": "10.5281/zenodo.123",
            "sFileName": "data.csv",
            "sDestination": "",
        }]}
        fnDownloadDatasets(dictWorkflow, sTmpDir)
        sCaptured = capsys.readouterr().out
        assert "Dataset exists" in sCaptured


@patch("vaibify.gui.director._fnDownloadFromZenodo")
def test_fnDownloadDatasets_calls_download(mockDownload, capsys):
    """Lines 458-459: calls download for missing files."""
    from vaibify.gui.director import fnDownloadDatasets
    with tempfile.TemporaryDirectory() as sTmpDir:
        dictWorkflow = {"listDatasets": [{
            "sDoi": "10.5281/zenodo.999",
            "sFileName": "missing.csv",
            "sDestination": "",
        }]}
        fnDownloadDatasets(dictWorkflow, sTmpDir)
        mockDownload.assert_called_once()
        sCaptured = capsys.readouterr().out
        assert "Downloading" in sCaptured


# -----------------------------------------------------------------------
# _fnDownloadFromZenodo — mocked (lines 464-486)
# -----------------------------------------------------------------------


def test_fnDownloadFromZenodo_success(capsys):
    """Lines 464-483: successful download writes file."""
    import sys
    from vaibify.gui.director import _fnDownloadFromZenodo
    mockRequests = MagicMock()
    mockResponse = MagicMock()
    mockResponse.json.return_value = {
        "files": [{"key": "data.csv", "links": {"self": "http://f"}}],
    }
    mockDownloadResp = MagicMock()
    mockDownloadResp.iter_content.return_value = [b"data"]
    mockRequests.get.side_effect = [mockResponse, mockDownloadResp]
    with patch.dict(sys.modules, {"requests": mockRequests}):
        with tempfile.TemporaryDirectory() as sTmpDir:
            sDestPath = os.path.join(sTmpDir, "data.csv")
            _fnDownloadFromZenodo(
                "10.5281/zenodo.123", "data.csv", sDestPath,
            )
            assert os.path.isfile(sDestPath)
            sCaptured = capsys.readouterr().out
            assert "Downloaded" in sCaptured


def test_fnDownloadFromZenodo_file_not_found(capsys):
    """Line 484: file not found in record."""
    import sys
    from vaibify.gui.director import _fnDownloadFromZenodo
    mockRequests = MagicMock()
    mockResponse = MagicMock()
    mockResponse.json.return_value = {
        "files": [{"key": "other.csv", "links": {"self": "http://f"}}],
    }
    mockRequests.get.return_value = mockResponse
    with patch.dict(sys.modules, {"requests": mockRequests}):
        with tempfile.TemporaryDirectory() as sTmpDir:
            sDestPath = os.path.join(sTmpDir, "missing.csv")
            _fnDownloadFromZenodo(
                "10.5281/zenodo.123", "missing.csv", sDestPath,
            )
            sCaptured = capsys.readouterr().out
            assert "WARNING" in sCaptured


def test_fnDownloadFromZenodo_exception(capsys):
    """Lines 485-486: exception path prints warning."""
    import sys
    from vaibify.gui.director import _fnDownloadFromZenodo
    mockRequests = MagicMock()
    mockRequests.get.side_effect = RuntimeError("network error")
    with patch.dict(sys.modules, {"requests": mockRequests}):
        _fnDownloadFromZenodo(
            "10.5281/zenodo.123", "data.csv", "/tmp/x.csv",
        )
        sCaptured = capsys.readouterr().out
        assert "WARNING" in sCaptured
        assert "network error" in sCaptured


# -----------------------------------------------------------------------
# _fsResolveFigureType (line 380-385)
# -----------------------------------------------------------------------


def test_fsResolveFigureType_step_override():
    from vaibify.gui.director import _fsResolveFigureType
    dictStep = {"sFigureType": "PNG"}
    dictWorkflow = {"sFigureType": "pdf"}
    assert _fsResolveFigureType(dictStep, dictWorkflow) == "png"


def test_fsResolveFigureType_workflow_default():
    from vaibify.gui.director import _fsResolveFigureType
    dictStep = {}
    dictWorkflow = {"sFigureType": "SVG"}
    assert _fsResolveFigureType(dictStep, dictWorkflow) == "svg"
