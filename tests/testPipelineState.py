"""Tests for vaibify.gui.pipelineState pure functions."""

from vaibify.gui.pipelineState import (
    fdictBuildInitialState,
    fdictBuildStepStarted,
    fdictBuildStepResult,
    fdictBuildCompletedState,
    fnAppendOutput,
    I_MAX_OUTPUT_LINES,
)


def test_fdictBuildInitialState_sets_bRunning():
    dictState = fdictBuildInitialState("runAll", "/log.txt", 5)
    assert dictState["bRunning"] is True
    assert dictState["sAction"] == "runAll"
    assert dictState["sLogPath"] == "/log.txt"
    assert dictState["iStepCount"] == 5


def test_fdictBuildInitialState_empty_results():
    dictState = fdictBuildInitialState("run", "/x", 0)
    assert dictState["dictStepResults"] == {}
    assert dictState["listRecentOutput"] == []
    assert dictState["iActiveStep"] == -1


def test_fdictBuildStepStarted_returns_step():
    dictUpdate = fdictBuildStepStarted(3)
    assert dictUpdate["iActiveStep"] == 3


def test_fdictBuildStepResult_passed():
    dictResult = fdictBuildStepResult(2, "passed")
    assert dictResult["iStepNumber"] == 2
    assert dictResult["sStatus"] == "passed"
    assert dictResult["iExitCode"] == 0


def test_fdictBuildStepResult_failed_with_exit_code():
    dictResult = fdictBuildStepResult(5, "failed", 127)
    assert dictResult["sStatus"] == "failed"
    assert dictResult["iExitCode"] == 127


def test_fdictBuildCompletedState_sets_bRunning_false():
    dictUpdate = fdictBuildCompletedState(0)
    assert dictUpdate["bRunning"] is False
    assert dictUpdate["iExitCode"] == 0
    assert dictUpdate["iActiveStep"] == -1


def test_fdictBuildCompletedState_records_exit_code():
    dictUpdate = fdictBuildCompletedState(1)
    assert dictUpdate["iExitCode"] == 1
    assert "sEndTime" in dictUpdate


def test_fnAppendOutput_adds_line():
    dictState = {"listRecentOutput": ["line1"]}
    fnAppendOutput(dictState, "line2")
    assert dictState["listRecentOutput"] == ["line1", "line2"]


def test_fnAppendOutput_truncates_at_max():
    dictState = {"listRecentOutput": [f"L{i}" for i in range(
        I_MAX_OUTPUT_LINES)]}
    fnAppendOutput(dictState, "overflow")
    assert len(dictState["listRecentOutput"]) == I_MAX_OUTPUT_LINES
    assert dictState["listRecentOutput"][-1] == "overflow"
    assert dictState["listRecentOutput"][0] == "L1"


# -----------------------------------------------------------------------
# fdictBuildInteractivePauseState
# -----------------------------------------------------------------------


def test_fdictBuildInteractivePauseState_returns_expected_keys():
    from vaibify.gui.pipelineState import fdictBuildInteractivePauseState
    dictResult = fdictBuildInteractivePauseState(3, "RunMCMC")
    assert dictResult["bRunning"] is True
    assert dictResult["bInteractivePause"] is True
    assert dictResult["iActiveStep"] == 3
    assert dictResult["sActiveStepName"] == "RunMCMC"


def test_fdictBuildInteractivePauseState_step_zero():
    from vaibify.gui.pipelineState import fdictBuildInteractivePauseState
    dictResult = fdictBuildInteractivePauseState(0, "Initialize")
    assert dictResult["iActiveStep"] == 0
    assert dictResult["bInteractivePause"] is True
