"""Tests for async functions in vaibify.gui.pipelineRunner with mocked Docker."""

import asyncio

import pytest
from unittest.mock import MagicMock, patch, AsyncMock


from vaibify.gui.pipelineRunner import (
    fiRunStepCommands,
    _fiRunSetupIfNeeded,
    _ftRunCommandList,
    _ftRunSingleCommand,
    fnRunAllSteps,
    fnRunFromStep,
    fnRunSelectedSteps,
    fnVerifyOnly,
    _fbVerifyStepOutputs,
    _fsetSnapshotDirectory,
    _fnRecordInputHashes,
    _fnEmitDiscoveredOutputs,
    _fsMissingDependencyFile,
    _fiReportPreflightFailure,
    _fnUpdatePipelineState,
    _fiRunStepList,
    _fnRunOneStep,
    _fiCheckDependencies,
    _fiExecuteAndRecord,
    _fnWriteTestLog,
    _fiRunTestCommands,
    _fdictLoadWorkflow,
)


def _fnRunAsync(coroutine):
    """Run an async coroutine synchronously."""
    return asyncio.get_event_loop().run_until_complete(coroutine)


def _fMockDocker(iExitCode=0, sOutput=""):
    """Return a mock Docker connection."""
    mockDocker = MagicMock()
    mockDocker.ftResultExecuteCommand.return_value = (
        iExitCode, sOutput
    )
    mockDocker.fnWriteFile = MagicMock()
    mockDocker.fbaFetchFile.return_value = b"{}"
    return mockDocker


def _fMockCallback():
    """Return an async callback that captures events."""
    listCaptured = []

    async def fnCallback(dictEvent):
        listCaptured.append(dictEvent)

    return fnCallback, listCaptured


# -----------------------------------------------------------------------
# _ftRunSingleCommand
# -----------------------------------------------------------------------


def test_ftRunSingleCommand_success():
    mockDocker = _fMockDocker(0, "line1\nline2")
    fnCallback, listCaptured = _fMockCallback()
    iResult, fCpu = _fnRunAsync(_ftRunSingleCommand(
        mockDocker, "cid", "cmd", "cmd", "/work", fnCallback,
    ))
    assert iResult == 0
    listTypes = [d["sType"] for d in listCaptured]
    assert "output" in listTypes


def test_ftRunSingleCommand_failure():
    mockDocker = _fMockDocker(1, "error msg")
    fnCallback, listCaptured = _fMockCallback()
    iResult, fCpu = _fnRunAsync(_ftRunSingleCommand(
        mockDocker, "cid", "badcmd", "badcmd",
        "/work", fnCallback,
    ))
    assert iResult == 1
    listTypes = [d["sType"] for d in listCaptured]
    assert "commandFailed" in listTypes


# -----------------------------------------------------------------------
# _ftRunCommandList
# -----------------------------------------------------------------------


def test_ftRunCommandList_empty():
    mockDocker = _fMockDocker()
    fnCallback, listCaptured = _fMockCallback()
    iResult, fCpu = _fnRunAsync(_ftRunCommandList(
        mockDocker, "cid", [], "/work", {}, fnCallback,
    ))
    assert iResult == 0


def test_ftRunCommandList_stops_on_failure():
    mockDocker = MagicMock()
    mockDocker.ftResultExecuteCommand.side_effect = [
        (1, "fail"),
    ]
    fnCallback, _ = _fMockCallback()
    iResult, fCpu = _fnRunAsync(_ftRunCommandList(
        mockDocker, "cid", ["cmd1", "cmd2"],
        "/work", {}, fnCallback,
    ))
    assert iResult == 1


def test_ftRunCommandList_runs_all():
    mockDocker = _fMockDocker(0, "ok")
    fnCallback, _ = _fMockCallback()
    iResult, fCpu = _fnRunAsync(_ftRunCommandList(
        mockDocker, "cid", ["c1", "c2"],
        "/work", {}, fnCallback,
    ))
    assert iResult == 0


# -----------------------------------------------------------------------
# _fiRunSetupIfNeeded
# -----------------------------------------------------------------------


def test_fiRunSetupIfNeeded_plot_only():
    mockDocker = _fMockDocker()
    fnCallback, _ = _fMockCallback()
    dictStep = {"bPlotOnly": True}
    iResult, fCpu = _fnRunAsync(_fiRunSetupIfNeeded(
        mockDocker, "cid", dictStep, "/work", {}, fnCallback,
    ))
    assert iResult == 0


def test_fiRunSetupIfNeeded_runs_data():
    mockDocker = _fMockDocker(0, "")
    fnCallback, _ = _fMockCallback()
    dictStep = {"bPlotOnly": False, "saDataCommands": ["cmd1"]}
    iResult, fCpu = _fnRunAsync(_fiRunSetupIfNeeded(
        mockDocker, "cid", dictStep, "/work", {}, fnCallback,
    ))
    assert iResult == 0


# -----------------------------------------------------------------------
# _fsetSnapshotDirectory
# -----------------------------------------------------------------------


def test_fsetSnapshotDirectory_returns_files():
    mockDocker = _fMockDocker(0, "/a/b.txt\n/a/c.py\n")
    setFiles = _fnRunAsync(_fsetSnapshotDirectory(
        mockDocker, "cid", "/a",
    ))
    assert "/a/b.txt" in setFiles
    assert "/a/c.py" in setFiles


def test_fsetSnapshotDirectory_empty_on_failure():
    mockDocker = _fMockDocker(1, "")
    setFiles = _fnRunAsync(_fsetSnapshotDirectory(
        mockDocker, "cid", "/missing",
    ))
    assert setFiles == set()


def test_fsetSnapshotDirectory_empty_output():
    mockDocker = _fMockDocker(0, "  \n  ")
    setFiles = _fnRunAsync(_fsetSnapshotDirectory(
        mockDocker, "cid", "/empty",
    ))
    assert setFiles == set()


# -----------------------------------------------------------------------
# _fnEmitDiscoveredOutputs
# -----------------------------------------------------------------------


def test_fnEmitDiscoveredOutputs_no_new_files():
    mockDocker = _fMockDocker(0, "/a/old.txt\n")
    fnCallback, listCaptured = _fMockCallback()
    setBefore = {"/a/old.txt"}
    dictStep = {}
    _fnRunAsync(_fnEmitDiscoveredOutputs(
        mockDocker, "cid", "/a",
        setBefore, dictStep, 1, fnCallback,
    ))
    listDiscovery = [
        d for d in listCaptured
        if d.get("sType") == "discoveredOutputs"
    ]
    assert len(listDiscovery) == 0


def test_fnEmitDiscoveredOutputs_unexpected():
    mockDocker = _fMockDocker(0, "/a/old.txt\n/a/new.dat\n")
    fnCallback, listCaptured = _fMockCallback()
    setBefore = {"/a/old.txt"}
    dictStep = {"saDataFiles": [], "saPlotFiles": []}
    _fnRunAsync(_fnEmitDiscoveredOutputs(
        mockDocker, "cid", "/a",
        setBefore, dictStep, 1, fnCallback,
    ))
    listDiscovery = [
        d for d in listCaptured
        if d.get("sType") == "discoveredOutputs"
    ]
    assert len(listDiscovery) == 1


def test_fnEmitDiscoveredOutputs_expected():
    mockDocker = _fMockDocker(0, "/a/data.npy\n")
    fnCallback, listCaptured = _fMockCallback()
    setBefore = set()
    dictStep = {"saDataFiles": ["data.npy"], "saPlotFiles": []}
    _fnRunAsync(_fnEmitDiscoveredOutputs(
        mockDocker, "cid", "/a",
        setBefore, dictStep, 1, fnCallback,
    ))
    listDiscovery = [
        d for d in listCaptured
        if d.get("sType") == "discoveredOutputs"
    ]
    assert len(listDiscovery) == 0


# -----------------------------------------------------------------------
# _fnRecordInputHashes
# -----------------------------------------------------------------------


@patch("vaibify.gui.syncDispatcher.fdictComputeInputHashes",
       return_value={"/workspace/script.py": "abc123"})
def test_fnRecordInputHashes_stores_hashes(mockCompute):
    mockDocker = _fMockDocker()
    dictStep = {"saDataCommands": ["python script.py"]}
    _fnRunAsync(_fnRecordInputHashes(
        mockDocker, "cid", dictStep,
    ))
    assert "dictInputHashes" in dictStep["dictRunStats"]


@patch("vaibify.gui.syncDispatcher.fdictComputeInputHashes",
       return_value={})
def test_fnRecordInputHashes_creates_run_stats(mockCompute):
    mockDocker = _fMockDocker()
    dictStep = {}
    _fnRunAsync(_fnRecordInputHashes(
        mockDocker, "cid", dictStep,
    ))
    assert "dictRunStats" in dictStep


# -----------------------------------------------------------------------
# _fsMissingDependencyFile
# -----------------------------------------------------------------------


def test_fsMissingDependencyFile_no_refs():
    mockDocker = _fMockDocker()
    sResult = _fnRunAsync(_fsMissingDependencyFile(
        mockDocker, "cid", {"saDataCommands": ["echo hi"]}, {},
    ))
    assert sResult == ""


def test_fsMissingDependencyFile_found():
    mockDocker = MagicMock()
    mockDocker.ftResultExecuteCommand.return_value = (0, "")
    dictStep = {
        "saDataCommands": ["python plot.py {Step01.data}"],
        "saPlotCommands": [],
    }
    dictVars = {"Step01.data": "/workspace/data.npy"}
    sResult = _fnRunAsync(_fsMissingDependencyFile(
        mockDocker, "cid", dictStep, dictVars,
    ))
    assert sResult == ""


def test_fsMissingDependencyFile_missing():
    mockDocker = MagicMock()
    mockDocker.ftResultExecuteCommand.return_value = (1, "")
    dictStep = {
        "saDataCommands": ["python plot.py {Step01.data}"],
        "saPlotCommands": [],
    }
    dictVars = {"Step01.data": "/workspace/data.npy"}
    sResult = _fnRunAsync(_fsMissingDependencyFile(
        mockDocker, "cid", dictStep, dictVars,
    ))
    assert sResult == "/workspace/data.npy"


# -----------------------------------------------------------------------
# _fbVerifyStepOutputs
# -----------------------------------------------------------------------


def test_fbVerifyStepOutputs_all_present():
    mockDocker = _fMockDocker(0, "")
    fnCallback, _ = _fMockCallback()
    dictStep = {"sDirectory": "/work", "saPlotFiles": ["a.pdf"]}
    bResult = _fnRunAsync(_fbVerifyStepOutputs(
        mockDocker, "cid", dictStep, "/work", fnCallback,
    ))
    assert bResult is True


def test_fbVerifyStepOutputs_missing():
    mockDocker = _fMockDocker(1, "")
    fnCallback, listCaptured = _fMockCallback()
    dictStep = {"sDirectory": "/work", "saPlotFiles": ["a.pdf"]}
    bResult = _fnRunAsync(_fbVerifyStepOutputs(
        mockDocker, "cid", dictStep, "/work", fnCallback,
    ))
    assert bResult is False
    assert any("Missing" in d.get("sLine", "") for d in listCaptured)


def test_fbVerifyStepOutputs_no_files():
    mockDocker = _fMockDocker()
    fnCallback, _ = _fMockCallback()
    dictStep = {"sDirectory": "/work"}
    bResult = _fnRunAsync(_fbVerifyStepOutputs(
        mockDocker, "cid", dictStep, "/work", fnCallback,
    ))
    assert bResult is True


# -----------------------------------------------------------------------
# fiRunStepCommands
# -----------------------------------------------------------------------


def test_fiRunStepCommands_plot_only():
    mockDocker = _fMockDocker(0, "")
    fnCallback, _ = _fMockCallback()
    dictStep = {
        "bPlotOnly": True,
        "sDirectory": "/work",
        "saPlotCommands": ["python plot.py"],
        "saPlotFiles": [],
    }
    iResult, fCpu = _fnRunAsync(fiRunStepCommands(
        mockDocker, "cid", dictStep, "/work",
        {"sPlotDirectory": "Plot"}, fnCallback,
    ))
    assert iResult == 0


# -----------------------------------------------------------------------
# fnRunAllSteps
# -----------------------------------------------------------------------


@patch("vaibify.gui.pipelineRunner._fiRunWithLogging",
       new_callable=AsyncMock)
@patch("vaibify.gui.pipelineRunner._fdictLoadWorkflow",
       new_callable=AsyncMock)
def test_fnRunAllSteps_no_workflow(mockLoad, mockRun):
    mockLoad.return_value = (None, "")
    fnCallback, _ = _fMockCallback()
    iResult = _fnRunAsync(fnRunAllSteps(
        _fMockDocker(), "cid", "/work", fnCallback,
    ))
    assert iResult == 1


@patch("vaibify.gui.pipelineRunner._fiRunWithLogging",
       new_callable=AsyncMock, return_value=0)
@patch("vaibify.gui.pipelineRunner._fdictLoadWorkflow",
       new_callable=AsyncMock)
def test_fnRunAllSteps_success(mockLoad, mockRun):
    mockLoad.return_value = ({
        "sWorkflowName": "Test",
        "listSteps": [{"sName": "A"}],
    }, "/workspace/.vaibify/workflows/test.json")
    fnCallback, _ = _fMockCallback()
    iResult = _fnRunAsync(fnRunAllSteps(
        _fMockDocker(), "cid", "/work", fnCallback,
    ))
    assert iResult == 0


@patch("vaibify.gui.pipelineRunner._fiRunWithLogging",
       new_callable=AsyncMock, return_value=0)
@patch("vaibify.gui.pipelineRunner._fdictLoadWorkflow",
       new_callable=AsyncMock)
def test_fnRunAllSteps_force_clears_stats(mockLoad, mockRun):
    dictStep = {"sName": "A", "dictRunStats": {"sLastRun": "x"}}
    dictWorkflow = {
        "sWorkflowName": "Test",
        "listSteps": [dictStep],
    }
    mockLoad.return_value = (dictWorkflow, "/w/test.json")
    fnCallback, _ = _fMockCallback()
    _fnRunAsync(fnRunAllSteps(
        _fMockDocker(), "cid", "/work", fnCallback,
        bForceRun=True,
    ))
    assert dictStep["dictRunStats"] == {}


# -----------------------------------------------------------------------
# fnRunFromStep
# -----------------------------------------------------------------------


@patch("vaibify.gui.pipelineRunner._fiRunWithLogging",
       new_callable=AsyncMock)
@patch("vaibify.gui.pipelineRunner._fdictLoadWorkflow",
       new_callable=AsyncMock)
def test_fnRunFromStep_no_workflow(mockLoad, mockRun):
    mockLoad.return_value = (None, "")
    fnCallback, _ = _fMockCallback()
    iResult = _fnRunAsync(fnRunFromStep(
        _fMockDocker(), "cid", 3, "/work", fnCallback,
    ))
    assert iResult == 1


@patch("vaibify.gui.pipelineRunner._fiRunWithLogging",
       new_callable=AsyncMock, return_value=0)
@patch("vaibify.gui.pipelineRunner._fdictLoadWorkflow",
       new_callable=AsyncMock)
def test_fnRunFromStep_success(mockLoad, mockRun):
    mockLoad.return_value = ({
        "sWorkflowName": "Test", "listSteps": [],
    }, "/w/test.json")
    fnCallback, _ = _fMockCallback()
    iResult = _fnRunAsync(fnRunFromStep(
        _fMockDocker(), "cid", 2, "/work", fnCallback,
    ))
    assert iResult == 0


# -----------------------------------------------------------------------
# fnVerifyOnly
# -----------------------------------------------------------------------


@patch("vaibify.gui.pipelineRunner._fdictLoadWorkflow",
       new_callable=AsyncMock)
def test_fnVerifyOnly_no_workflow(mockLoad):
    mockLoad.return_value = (None, "")
    fnCallback, _ = _fMockCallback()
    iResult = _fnRunAsync(fnVerifyOnly(
        _fMockDocker(), "cid", "/work", fnCallback,
    ))
    assert iResult == 1


@patch("vaibify.gui.pipelineRunner._fdictLoadWorkflow",
       new_callable=AsyncMock)
def test_fnVerifyOnly_all_present(mockLoad):
    mockLoad.return_value = ({
        "sWorkflowName": "Test",
        "listSteps": [
            {"sDirectory": "/work", "saPlotFiles": []},
        ],
    }, "/w/test.json")
    mockDocker = _fMockDocker(0, "")
    fnCallback, listCaptured = _fMockCallback()
    iResult = _fnRunAsync(fnVerifyOnly(
        mockDocker, "cid", "/work", fnCallback,
    ))
    assert iResult == 0
    listTypes = [d["sType"] for d in listCaptured]
    assert "completed" in listTypes


@patch("vaibify.gui.pipelineRunner._fdictLoadWorkflow",
       new_callable=AsyncMock)
def test_fnVerifyOnly_missing_files(mockLoad):
    mockLoad.return_value = ({
        "sWorkflowName": "Test",
        "listSteps": [
            {"sDirectory": "/work", "saPlotFiles": ["a.pdf"]},
        ],
    }, "/w/test.json")
    mockDocker = _fMockDocker(1, "")
    fnCallback, listCaptured = _fMockCallback()
    iResult = _fnRunAsync(fnVerifyOnly(
        mockDocker, "cid", "/work", fnCallback,
    ))
    assert iResult == 1


# -----------------------------------------------------------------------
# fnRunSelectedSteps
# -----------------------------------------------------------------------


@patch("vaibify.gui.pipelineRunner._fiRunWithLogging",
       new_callable=AsyncMock, return_value=0)
@patch("vaibify.gui.workflowManager.fnSaveWorkflowToContainer")
def test_fnRunSelectedSteps_restores(mockSave, mockRun):
    mockDocker = _fMockDocker()
    fnCallback, _ = _fMockCallback()
    dictWorkflow = {
        "sWorkflowName": "Test",
        "listSteps": [
            {"sName": "A", "bEnabled": True},
            {"sName": "B", "bEnabled": True},
        ],
    }
    iResult = _fnRunAsync(fnRunSelectedSteps(
        mockDocker, "cid", [0], dictWorkflow,
        "/wf.json", "/work", fnCallback,
    ))
    assert iResult == 0
    assert mockSave.call_count == 2


# -----------------------------------------------------------------------
# _fiReportPreflightFailure
# -----------------------------------------------------------------------


def test_fiReportPreflightFailure_returns_one():
    mockDocker = _fMockDocker()
    fnLogging, listLog = _fMockCallback()
    fnStatus, listStatus = _fMockCallback()
    iResult = _fnRunAsync(_fiReportPreflightFailure(
        fnLogging, fnStatus, mockDocker,
        "cid", "/log.txt", [],
        ["Error: directory missing"], "runAll",
    ))
    assert iResult == 1
    listTypes = [d["sType"] for d in listStatus]
    assert "preflightFailed" in listTypes
    assert "failed" in listTypes


# -----------------------------------------------------------------------
# _fnUpdatePipelineState
# -----------------------------------------------------------------------


@patch("vaibify.gui.pipelineState.fnAppendOutput")
def test_fnUpdatePipelineState_output(mockAppend):
    mockDocker = _fMockDocker()
    dictState = {"listRecentOutput": []}
    _fnUpdatePipelineState(
        mockDocker, "cid", dictState,
        {"sType": "output", "sLine": "hello"},
    )
    mockAppend.assert_called_once()


@patch("vaibify.gui.pipelineState.fnUpdateState")
@patch("vaibify.gui.pipelineState.fdictBuildStepStarted",
       return_value={"iActiveStep": 1})
def test_fnUpdatePipelineState_step_started(
    mockBuild, mockUpdate,
):
    mockDocker = _fMockDocker()
    dictState = {}
    _fnUpdatePipelineState(
        mockDocker, "cid", dictState,
        {"sType": "stepStarted", "iStepNumber": 1},
    )
    mockUpdate.assert_called_once()


@patch("vaibify.gui.pipelineState.fnRecordStepResult")
@patch("vaibify.gui.pipelineState.fdictBuildStepResult",
       return_value={"iStepNumber": 1, "sStatus": "passed",
                     "iExitCode": 0})
def test_fnUpdatePipelineState_step_pass(
    mockBuild, mockRecord,
):
    mockDocker = _fMockDocker()
    dictState = {}
    _fnUpdatePipelineState(
        mockDocker, "cid", dictState,
        {"sType": "stepPass", "iStepNumber": 1},
    )
    mockRecord.assert_called_once()


# -----------------------------------------------------------------------
# _fdictLoadWorkflow
# -----------------------------------------------------------------------


@patch("vaibify.gui.workflowManager.flistFindWorkflowsInContainer",
       return_value=[])
def test_fdictLoadWorkflow_no_workflows(mockFind):
    fnCallback, listCaptured = _fMockCallback()
    dictResult, sPath = _fnRunAsync(_fdictLoadWorkflow(
        _fMockDocker(), "cid", fnCallback,
    ))
    assert dictResult is None
    assert sPath == ""
    assert any(
        d.get("sType") == "error" for d in listCaptured
    )


@patch("vaibify.gui.workflowManager.fdictLoadWorkflowFromContainer",
       return_value={"sWorkflowName": "Test"})
@patch("vaibify.gui.workflowManager.flistFindWorkflowsInContainer",
       return_value=[{"sPath": "/wf.json"}])
def test_fdictLoadWorkflow_found(mockFind, mockLoad):
    fnCallback, _ = _fMockCallback()
    dictResult, sPath = _fnRunAsync(_fdictLoadWorkflow(
        _fMockDocker(), "cid", fnCallback,
    ))
    assert dictResult["sWorkflowName"] == "Test"
    assert sPath == "/wf.json"


# -----------------------------------------------------------------------
# _fiRunTestCommands
# -----------------------------------------------------------------------


def test_fiRunTestCommands_empty():
    mockDocker = _fMockDocker()
    fnCallback, _ = _fMockCallback()
    dictStep = {}
    iResult = _fnRunAsync(_fiRunTestCommands(
        mockDocker, "cid", dictStep, "/work",
        {}, fnCallback, 1,
    ))
    assert iResult == 0


def test_fiRunTestCommands_runs_tests():
    mockDocker = _fMockDocker(0, "all passed")
    fnCallback, listCaptured = _fMockCallback()
    dictStep = {"saTestCommands": ["pytest test.py"]}
    iResult = _fnRunAsync(_fiRunTestCommands(
        mockDocker, "cid", dictStep, "/work",
        {}, fnCallback, 1,
    ))
    assert iResult == 0
    listTypes = [d["sType"] for d in listCaptured]
    assert "testResult" in listTypes


# -----------------------------------------------------------------------
# _fiCheckDependencies
# -----------------------------------------------------------------------


def test_fiCheckDependencies_no_refs():
    mockDocker = _fMockDocker()
    fnCallback, _ = _fMockCallback()
    dictStep = {"saDataCommands": [], "saPlotCommands": []}
    iResult = _fnRunAsync(_fiCheckDependencies(
        mockDocker, "cid", dictStep, {}, 1, fnCallback,
    ))
    assert iResult == 0


def test_fiCheckDependencies_missing():
    mockDocker = MagicMock()
    mockDocker.ftResultExecuteCommand.return_value = (1, "")
    fnCallback, listCaptured = _fMockCallback()
    dictStep = {
        "sName": "Plot",
        "saDataCommands": ["python x.py {Step01.out}"],
        "saPlotCommands": [],
    }
    dictVars = {"Step01.out": "/workspace/out.npy"}
    iResult = _fnRunAsync(_fiCheckDependencies(
        mockDocker, "cid", dictStep, dictVars, 2, fnCallback,
    ))
    assert iResult == 1


# -----------------------------------------------------------------------
# _fnWriteTestLog
# -----------------------------------------------------------------------


def test_fnWriteTestLog_writes_file():
    mockDocker = _fMockDocker()
    _fnRunAsync(_fnWriteTestLog(
        mockDocker, "cid", 1, ["test passed"],
    ))
    mockDocker.fnWriteFile.assert_called_once()
