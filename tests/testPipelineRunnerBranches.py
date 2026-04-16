"""Tests for uncovered branches in vaibify.gui.pipelineRunner."""

import asyncio

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from vaibify.gui.pipelineRunner import (
    _fiCheckDependencies,
    _fiRunSetupIfNeeded,
    _fnRunOneStep,
    _fsMissingDependencyFile,
    _ftRunSingleCommand,
    fiRunStepCommands,
)


def _fnRunAsync(coroutine):
    """Run an async coroutine synchronously."""
    return asyncio.run(coroutine)


def _fMockDocker(iExitCode=0, sOutput=""):
    mockDocker = MagicMock()
    mockDocker.ftResultExecuteCommand.return_value = (
        iExitCode, sOutput,
    )
    return mockDocker


def _fMockCallback():
    listCaptured = []

    async def fnCallback(dictEvent):
        listCaptured.append(dictEvent)

    return fnCallback, listCaptured


# ---------------------------------------------------------------
# _ftRunSingleCommand: __VAIBIFY_CPU__ line skipped (line 166)
# ---------------------------------------------------------------


def test_ftRunSingleCommand_cpu_line_not_emitted_as_output():
    sOutput = "before\n__VAIBIFY_CPU__ 1.5 0.5\nafter\n"
    mockDocker = _fMockDocker(0, sOutput)
    fnCallback, listCaptured = _fMockCallback()
    iResult, fCpu = _fnRunAsync(_ftRunSingleCommand(
        mockDocker, "cid", "cmd", "cmd", "/work", fnCallback,
    ))
    assert iResult == 0
    # The CPU line is extracted for timing but never forwarded as output.
    listOutputLines = [
        d["sLine"] for d in listCaptured if d.get("sType") == "output"
    ]
    assert "before" in listOutputLines
    assert "after" in listOutputLines
    for sLine in listOutputLines:
        assert "__VAIBIFY_CPU__" not in sLine
    # The parsed CPU time is user + system = 1.5 + 0.5 = 2.0.
    assert fCpu == pytest.approx(2.0)


# ---------------------------------------------------------------
# fiRunStepCommands: early return on setup failure (line 225)
# ---------------------------------------------------------------


def test_fiRunStepCommands_returns_on_setup_failure():
    """If data commands fail, plot commands should not run."""
    mockDocker = MagicMock()
    # First call: mkdir Plot directory succeeds.
    # Second call: data command fails.
    mockDocker.ftResultExecuteCommand.side_effect = [
        (0, ""),       # mkdir Plot
        (5, "oops"),   # first data command fails with exit=5
    ]
    fnCallback, listCaptured = _fMockCallback()
    dictStep = {
        "sDirectory": "/ws/step",
        "saDataCommands": ["python broken.py"],
        "saPlotCommands": ["python plot.py"],
        "bPlotOnly": False,
    }
    iExitCode, fCpu = _fnRunAsync(fiRunStepCommands(
        mockDocker, "cid", dictStep, "/ws", {}, fnCallback,
    ))
    assert iExitCode == 5
    # The plot command should never have been invoked.
    listCalls = [
        c.args[1] for c in mockDocker.ftResultExecuteCommand.call_args_list
    ]
    assert not any("plot.py" in sCmd for sCmd in listCalls)


def test_fiRunStepCommands_runs_plot_when_setup_succeeds():
    mockDocker = MagicMock()
    mockDocker.ftResultExecuteCommand.return_value = (0, "")
    fnCallback, _ = _fMockCallback()
    dictStep = {
        "sDirectory": "/ws/step",
        "saDataCommands": [],
        "saPlotCommands": ["python plot.py"],
        "bPlotOnly": True,
    }
    iExitCode, _fCpu = _fnRunAsync(fiRunStepCommands(
        mockDocker, "cid", dictStep, "/ws", {}, fnCallback,
    ))
    assert iExitCode == 0
    listCalls = [
        c.args[1] for c in mockDocker.ftResultExecuteCommand.call_args_list
    ]
    assert any("plot.py" in sCmd for sCmd in listCalls)


# ---------------------------------------------------------------
# _fsMissingDependencyFile: dedupe and empty-path continue
# (lines 396, 400)
# ---------------------------------------------------------------


def test_fsMissingDependencyFile_dedupes_repeated_refs():
    """Same ref used twice is only tested once (line 396)."""
    mockDocker = MagicMock()
    # Return exit=0 (file present) so it would need only a single
    # existence check when deduplication works.
    mockDocker.ftResultExecuteCommand.return_value = (0, "")
    dictStep = {
        "saDataCommands": [
            "python a.py {Step01.data}",
            "python b.py {Step01.data}",
        ],
        "saPlotCommands": [],
    }
    dictVars = {"Step01.data": "/ws/data.npy"}
    sResult = _fnRunAsync(_fsMissingDependencyFile(
        mockDocker, "cid", dictStep, dictVars,
    ))
    assert sResult == ""
    # Should check only once despite appearing twice.
    assert mockDocker.ftResultExecuteCommand.call_count == 1


def test_fsMissingDependencyFile_skips_ref_with_empty_path():
    """Empty resolved path triggers continue (line 400)."""
    mockDocker = MagicMock()
    mockDocker.ftResultExecuteCommand.return_value = (0, "")
    dictStep = {
        "saDataCommands": ["python a.py {Step01.data}"],
        "saPlotCommands": [],
    }
    # dictVars entry is present but empty -> skip existence check.
    dictVars = {"Step01.data": ""}
    sResult = _fnRunAsync(_fsMissingDependencyFile(
        mockDocker, "cid", dictStep, dictVars,
    ))
    assert sResult == ""
    mockDocker.ftResultExecuteCommand.assert_not_called()


# ---------------------------------------------------------------
# _fnRunOneStep: skip-on-missing-dependency short-circuit (line 458)
# ---------------------------------------------------------------


def test_fnRunOneStep_returns_one_when_dependency_missing():
    """Dependency missing -> emit skipped output, return 1, skip execution."""
    mockDocker = MagicMock()
    fnCallback, listCaptured = _fMockCallback()
    dictStep = {
        "sName": "Analyze",
        "sDirectory": "/ws/step",
        "saDataCommands": ["python plot.py {Step01.data}"],
        "saPlotCommands": [],
    }
    dictVars = {"Step01.data": "/ws/missing.npy"}
    # Mock dependency check: file does NOT exist.
    mockDocker.ftResultExecuteCommand.return_value = (1, "")
    with patch(
        "vaibify.gui.pipelineRunner._fiExecuteAndRecord",
        new=AsyncMock(return_value=0),
    ) as mockExecute:
        iResult = _fnRunAsync(_fnRunOneStep(
            mockDocker, "cid", dictStep, 2,
            "/ws", dictVars, fnCallback,
        ))
    assert iResult == 1
    # Execution should have been bypassed.
    mockExecute.assert_not_called()
    listTypes = [d.get("sType") for d in listCaptured]
    assert "stepFail" in listTypes
    listOutputs = [
        d["sLine"] for d in listCaptured if d.get("sType") == "output"
    ]
    assert any("SKIPPED" in sLine for sLine in listOutputs)
    assert any("dependency not found" in sLine for sLine in listOutputs)


def test_fnRunOneStep_proceeds_when_dependencies_present():
    mockDocker = MagicMock()
    mockDocker.ftResultExecuteCommand.return_value = (0, "")
    fnCallback, listCaptured = _fMockCallback()
    dictStep = {
        "sName": "Analyze",
        "sDirectory": "/ws/step",
        "saDataCommands": [],
        "saPlotCommands": [],
    }
    with patch(
        "vaibify.gui.pipelineRunner._fiExecuteAndRecord",
        new=AsyncMock(return_value=0),
    ) as mockExecute:
        iResult = _fnRunAsync(_fnRunOneStep(
            mockDocker, "cid", dictStep, 1,
            "/ws", {}, fnCallback,
        ))
    assert iResult == 0
    mockExecute.assert_awaited_once()


# ---------------------------------------------------------------
# _fiCheckDependencies: custom label used when provided
# ---------------------------------------------------------------


def test_fiCheckDependencies_uses_custom_step_label():
    mockDocker = MagicMock()
    mockDocker.ftResultExecuteCommand.return_value = (1, "")
    fnCallback, listCaptured = _fMockCallback()
    dictStep = {
        "sName": "Custom",
        "saDataCommands": ["python x.py {Step01.out}"],
        "saPlotCommands": [],
    }
    iResult = _fnRunAsync(_fiCheckDependencies(
        mockDocker, "cid", dictStep, {"Step01.out": "/missing.npy"},
        iStepNumber=3, fnStatusCallback=fnCallback,
        sStepLabel="03a",
    ))
    assert iResult == 1
    listOutputs = [
        d["sLine"] for d in listCaptured if d.get("sType") == "output"
    ]
    assert any("Step 03a" in sLine for sLine in listOutputs)
