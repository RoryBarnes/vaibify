"""Tests for uncovered branches in vaibify.gui.pipelineRunner."""

import asyncio

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from vaibify.gui.pipelineRunner import (
    _fiCheckDependencies,
    _ftRunSetupIfNeeded,
    _fiRunOneStep,
    _fsMissingDependencyFile,
    _ftRunSingleCommand,
    ftRunStepCommands,
)


def _fnRunAsync(coroutine):
    """Run an async coroutine synchronously."""
    return asyncio.run(coroutine)


def _fMockDocker(iExitCode=0, sOutput=""):
    mockDocker = MagicMock()
    mockDocker.ftResultExecuteCommand.return_value = (
        iExitCode, sOutput,
    )
    _fnConfigureStreamingMock(mockDocker, [(iExitCode, sOutput)])
    return mockDocker


def _fnConfigureStreamingMock(mockDocker, listResults):
    """Mirror the legacy mock onto ftRunInContainerStreamedWithChunks."""
    from vaibify.docker.dockerConnection import ExecResult
    listPending = list(listResults)

    def fnStreamingSideEffect(
        sContainerId, sCommand, fnEmitChunk,
        sWorkdir=None, sUser=None, sLane=None
    ):
        if listPending:
            iExitCode, sOutput = listPending.pop(0)
        else:
            iExitCode, sOutput = 0, ""
        for sLine in sOutput.splitlines():
            fnEmitChunk("stdout", sLine)
        return ExecResult(
            iExitCode=iExitCode, sStdout=sOutput, sStderr="",
        )

    mockDocker.ftRunInContainerStreamedWithChunks.side_effect = (
        fnStreamingSideEffect
    )


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
    # Output is now delivered as coalesced ``outputBatch`` events
    # carrying ``listLines``; legacy single-line ``output`` events
    # remain a backward-compat fallback path.
    listOutputLines = []
    for dictEvent in listCaptured:
        if dictEvent.get("sType") == "outputBatch":
            listOutputLines.extend(dictEvent.get("listLines", []))
        elif dictEvent.get("sType") == "output":
            listOutputLines.append(dictEvent["sLine"])
    assert "before" in listOutputLines
    assert "after" in listOutputLines
    for sLine in listOutputLines:
        assert "__VAIBIFY_CPU__" not in sLine
    # The parsed CPU time is user + system = 1.5 + 0.5 = 2.0.
    assert fCpu == pytest.approx(2.0)


# ---------------------------------------------------------------
# ftRunStepCommands: early return on setup failure (line 225)
# ---------------------------------------------------------------


def test_ftRunStepCommands_returns_on_setup_failure():
    """If data commands fail, plot commands should not run."""
    mockDocker = MagicMock()
    # mkdir Plot still uses the blocking exec; data/plot commands
    # now use the streaming exec, so the failure goes there.
    mockDocker.ftResultExecuteCommand.return_value = (0, "")
    _fnConfigureStreamingMock(mockDocker, [(5, "oops")])
    fnCallback, listCaptured = _fMockCallback()
    dictStep = {
        "sDirectory": "/ws/step",
        "saDataCommands": ["python broken.py"],
        "saPlotCommands": ["python plot.py"],
        "bPlotOnly": False,
    }
    iExitCode, fCpu = _fnRunAsync(ftRunStepCommands(
        mockDocker, "cid", dictStep, "/ws", {}, fnCallback,
    ))
    assert iExitCode == 5
    # The plot command should never have been invoked.
    listCalls = _flistAllExecutedCommands(mockDocker)
    assert not any("plot.py" in sCmd for sCmd in listCalls)


def test_ftRunStepCommands_runs_plot_when_setup_succeeds():
    mockDocker = _fMockDocker()
    fnCallback, _ = _fMockCallback()
    dictStep = {
        "sDirectory": "/ws/step",
        "saDataCommands": [],
        "saPlotCommands": ["python plot.py"],
        "bPlotOnly": True,
    }
    iExitCode, _fCpu = _fnRunAsync(ftRunStepCommands(
        mockDocker, "cid", dictStep, "/ws", {}, fnCallback,
    ))
    assert iExitCode == 0
    listCalls = _flistAllExecutedCommands(mockDocker)
    assert any("plot.py" in sCmd for sCmd in listCalls)


def _flistAllExecutedCommands(mockDocker):
    """Collect commands from both the legacy and the streaming exec mocks."""
    listLegacy = [
        c.args[1]
        for c in mockDocker.ftResultExecuteCommand.call_args_list
    ]
    listStreamed = [
        c.args[1]
        for c in
        mockDocker.ftRunInContainerStreamedWithChunks.call_args_list
    ]
    return listLegacy + listStreamed


# ---------------------------------------------------------------
# ftRunStepCommands: sRunMode gates data vs plot sections.
# ---------------------------------------------------------------


def _fdictStepForRunMode():
    return {
        "sDirectory": "/ws/step",
        "saDataCommands": ["python data.py"],
        "saPlotCommands": ["python plot.py"],
        "bPlotOnly": False,
    }


def _flistExecutedCommands(mockDocker):
    return _flistAllExecutedCommands(mockDocker)


def test_ftRunStepCommands_plotsOnly_skips_data_and_tests():
    mockDocker = _fMockDocker()
    fnCallback, _ = _fMockCallback()
    iExitCode, _fCpu = _fnRunAsync(ftRunStepCommands(
        mockDocker, "cid", _fdictStepForRunMode(),
        "/ws", {}, fnCallback, sRunMode="plotsOnly",
    ))
    assert iExitCode == 0
    listCalls = _flistExecutedCommands(mockDocker)
    assert not any("data.py" in sCmd for sCmd in listCalls)
    assert any("plot.py" in sCmd for sCmd in listCalls)


def test_ftRunStepCommands_dataOnly_skips_plots():
    mockDocker = _fMockDocker()
    fnCallback, _ = _fMockCallback()
    iExitCode, _fCpu = _fnRunAsync(ftRunStepCommands(
        mockDocker, "cid", _fdictStepForRunMode(),
        "/ws", {}, fnCallback, sRunMode="dataOnly",
    ))
    assert iExitCode == 0
    listCalls = _flistExecutedCommands(mockDocker)
    assert any("data.py" in sCmd for sCmd in listCalls)
    assert not any("plot.py" in sCmd for sCmd in listCalls)


def test_ftRunStepCommands_full_runs_both_sections():
    mockDocker = _fMockDocker()
    fnCallback, _ = _fMockCallback()
    iExitCode, _fCpu = _fnRunAsync(ftRunStepCommands(
        mockDocker, "cid", _fdictStepForRunMode(),
        "/ws", {}, fnCallback, sRunMode="full",
    ))
    assert iExitCode == 0
    listCalls = _flistExecutedCommands(mockDocker)
    assert any("data.py" in sCmd for sCmd in listCalls)
    assert any("plot.py" in sCmd for sCmd in listCalls)


# ---------------------------------------------------------------
# ftRunStepCommands: the order of a step's sections. Data, then
# plots, then tests, so the tests judge the plots the step has just
# drawn; a plot drawn after the tests' run reads as newer than it.
# ---------------------------------------------------------------


_T_SECTION_SCRIPTS = (
    ("data", "data.py"), ("plot", "plot.py"), ("test", "test_integrity_step.py"),
)


def _fdictStepWithEverySection(bPlotOnly=False):
    return {
        "sDirectory": "/ws/step",
        "saDataCommands": ["python data.py"],
        "saPlotCommands": ["python plot.py"],
        "saTestCommands": ["python -m pytest tests/test_integrity_step.py"],
        "bPlotOnly": bPlotOnly,
    }


def _ftRunStepAndRecordSections(
    dictExitBySection, sRunMode="full", bPlotOnly=False,
):
    """Run one step through a fake exec; return (sections, exit, events).

    The fake answers each command with the exit code its section is
    given (0 when unnamed) and records which section each command
    belonged to, in the order the runner executed them.
    """
    from vaibify.docker.dockerConnection import ExecResult
    listSections = []

    def fnStreamingSideEffect(
        sContainerId, sCommand, fnEmitChunk, sWorkdir=None, sUser=None, sLane=None
    ):
        for sSection, sScript in _T_SECTION_SCRIPTS:
            if sScript in sCommand:
                listSections.append(sSection)
                return ExecResult(
                    iExitCode=dictExitBySection.get(sSection, 0),
                    sStdout="", sStderr="")
        return ExecResult(iExitCode=0, sStdout="", sStderr="")

    mockDocker = MagicMock()
    mockDocker.ftResultExecuteCommand.return_value = (0, "")
    mockDocker.ftRunInContainerStreamedWithChunks.side_effect = (
        fnStreamingSideEffect)
    fnCallback, listEvents = _fMockCallback()
    iExitCode, _fCpu = _fnRunAsync(ftRunStepCommands(
        mockDocker, "cid", _fdictStepWithEverySection(bPlotOnly),
        "/ws", {}, fnCallback, iStepNumber=1, sRunMode=sRunMode,
    ))
    return listSections, iExitCode, listEvents


def _flistTestResults(listEvents):
    return [
        dictEvent["sResult"] for dictEvent in listEvents
        if dictEvent.get("sType") == "testResult"]


@pytest.mark.falsification
def test_ftRunStepCommands_full_runs_data_then_plots_then_tests():
    """Kills: the tests moved back before the plots."""
    listSections, iExitCode, listEvents = _ftRunStepAndRecordSections({})
    assert listSections == ["data", "plot", "test"]
    assert iExitCode == 0
    assert _flistTestResults(listEvents) == ["passed"]


@pytest.mark.falsification
def test_ftRunStepCommands_failing_tests_leave_the_plot_exit_code():
    """Kills: the step returning the tests' exit code."""
    listSections, iExitCode, listEvents = _ftRunStepAndRecordSections(
        {"test": 1})
    assert listSections == ["data", "plot", "test"]
    assert iExitCode == 0
    assert _flistTestResults(listEvents) == ["failed"]


@pytest.mark.falsification
def test_ftRunStepCommands_plot_failure_leaves_the_tests_unrun():
    """Kills: the tests running after the plot commands failed."""
    listSections, iExitCode, listEvents = _ftRunStepAndRecordSections(
        {"plot": 4})
    assert listSections == ["data", "plot"]
    assert iExitCode == 4
    assert _flistTestResults(listEvents) == []
    assert any(
        dictEvent.get("sLine") == "Tests not run: the plot commands failed."
        for dictEvent in listEvents)


def test_ftRunStepCommands_data_failure_ends_the_step():
    listSections, iExitCode, listEvents = _ftRunStepAndRecordSections(
        {"data": 3})
    assert listSections == ["data"]
    assert iExitCode == 3
    assert _flistTestResults(listEvents) == []


def test_ftRunStepCommands_dataOnly_runs_data_then_tests():
    listSections, iExitCode, listEvents = _ftRunStepAndRecordSections(
        {}, sRunMode="dataOnly")
    assert listSections == ["data", "test"]
    assert iExitCode == 0
    assert _flistTestResults(listEvents) == ["passed"]


def test_ftRunStepCommands_plotsOnly_runs_plots_alone():
    listSections, iExitCode, listEvents = _ftRunStepAndRecordSections(
        {"plot": 2}, sRunMode="plotsOnly")
    assert listSections == ["plot"]
    assert iExitCode == 2
    assert _flistTestResults(listEvents) == []
    assert not any(
        "Tests not run" in str(dictEvent.get("sLine", ""))
        for dictEvent in listEvents)


def test_ftRunStepCommands_plot_only_step_runs_plots_then_tests():
    listSections, iExitCode, listEvents = _ftRunStepAndRecordSections(
        {}, bPlotOnly=True)
    assert listSections == ["plot", "test"]
    assert iExitCode == 0
    assert _flistTestResults(listEvents) == ["passed"]


def test_ftRunStepCommands_rerun_of_plot_only_step_runs_every_section():
    from vaibify.gui.pipelineRunner import S_RUN_MODE_RERUN
    listSections, iExitCode, listEvents = _ftRunStepAndRecordSections(
        {}, sRunMode=S_RUN_MODE_RERUN, bPlotOnly=True)
    assert listSections == ["data", "plot", "test"]
    assert iExitCode == 0
    assert _flistTestResults(listEvents) == ["passed"]


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
# _fiRunOneStep: skip-on-missing-dependency short-circuit (line 458)
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
        iResult = _fnRunAsync(_fiRunOneStep(
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
        iResult = _fnRunAsync(_fiRunOneStep(
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
