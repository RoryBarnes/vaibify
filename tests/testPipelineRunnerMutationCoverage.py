"""Mutation-coverage tests for vaibify.gui.pipelineRunner.

Each test closes a specific coverage hole found by mutation testing:
a failed step must never render a green per-step badge or return 0,
the plot section's exit code must survive a successful data section,
a missing-output step must light a stepFail badge during verify-only
runs, the batch coalescer must flush on exactly I_BATCH_MAX_LINES, and
a partial directory listing from a failed ``find`` must be discarded.
"""

import asyncio
import threading

import pytest
from unittest.mock import MagicMock, patch, AsyncMock

pytestmark = pytest.mark.falsification

from vaibify.gui.pipelineRunner import (
    fiRunStepCommands,
    fnVerifyOnly,
    _fiExecuteAndRecord,
    _fsetSnapshotDirectory,
    _flistAppendAndMaybeDrainBatch,
    I_BATCH_MAX_LINES,
)


def _fnRunAsync(coroutine):
    """Run an async coroutine synchronously."""
    return asyncio.run(coroutine)


def _fMockDocker(iExitCode=0, sOutput=""):
    """Return a mock Docker connection covering both exec paths."""
    mockDocker = MagicMock()
    mockDocker.ftResultExecuteCommand.return_value = (iExitCode, sOutput)
    _fnConfigureStreamingMock(mockDocker, [(iExitCode, sOutput)])
    mockDocker.fnWriteFile = MagicMock()
    mockDocker.fbaFetchFile.return_value = b"{}"
    return mockDocker


def _fnConfigureStreamingMock(mockDocker, listResults):
    """Stream each ``(iExitCode, sOutput)`` through one streaming exec."""
    from vaibify.docker.dockerConnection import ExecResult
    listPending = list(listResults)

    def fnStreamingSideEffect(
        sContainerId, sCommand, fnEmitChunk,
        sWorkdir=None, sUser=None,
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

    mockDocker.texecRunInContainerStreamedWithChunks.side_effect = (
        fnStreamingSideEffect
    )


def _fMockCallback():
    """Return an async callback that captures events."""
    listCaptured = []

    async def fnCallback(dictEvent):
        listCaptured.append(dictEvent)

    return fnCallback, listCaptured


# -----------------------------------------------------------------------
# _fiExecuteAndRecord: a failed automatic step must not render green.
# Hole: line 1054 exit-code hard-coded to 0 (stepPass badge);
#       line 1055 return 0 (swallows non-zero status before aggregation).
# -----------------------------------------------------------------------


def _ftRunExecuteAndRecordWithFailure(iFailureExit):
    """Drive _fiExecuteAndRecord with fiRunStepCommands -> (iFailureExit, 1.0).

    Returns ``(iReturned, listCaptured)``. All neighbouring container
    side effects are mocked so only the exit-code wiring is exercised.
    """
    fnCallback, listCaptured = _fMockCallback()
    dictStep = {"sDirectory": "/ws/step"}
    with patch(
        "vaibify.gui.pipelineRunner.fiRunStepCommands",
        new=AsyncMock(return_value=(iFailureExit, 1.0)),
    ), patch(
        "vaibify.gui.pipelineRunner._fsetSnapshotDirectory",
        new=AsyncMock(return_value=set()),
    ), patch(
        "vaibify.gui.pipelineRunner._fnEmitDiscoveredOutputs",
        new=AsyncMock(),
    ), patch(
        "vaibify.gui.workflowManager.fnCleanStepScratchDirs",
        new=MagicMock(),
    ):
        iReturned = _fnRunAsync(_fiExecuteAndRecord(
            _fMockDocker(), "cid", dictStep,
            1, "/ws", {}, fnCallback,
        ))
    return iReturned, listCaptured


def test_fiExecuteAndRecord_failed_step_emits_stepFail_not_stepPass():
    """A non-zero step must emit stepFail and never stepPass.

    Kills: _fnEmitStepResult exit-code argument hard-coded to 0 instead
    of iExitCode (line ~1054).
    """
    _iReturned, listCaptured = _ftRunExecuteAndRecordWithFailure(5)
    listResultEvents = [
        d for d in listCaptured
        if d.get("sType") in ("stepPass", "stepFail")
    ]
    assert {"sType": "stepFail", "iStepNumber": 1, "iExitCode": 5} in (
        listResultEvents
    )
    assert not any(d["sType"] == "stepPass" for d in listResultEvents)


def test_fiExecuteAndRecord_returns_real_exit_code():
    """The function must return the step's real non-zero exit code.

    Kills: _fiExecuteAndRecord returns 0 instead of iExitCode (line ~1055).
    """
    iReturned, _listCaptured = _ftRunExecuteAndRecordWithFailure(5)
    assert iReturned == 5


# -----------------------------------------------------------------------
# fiRunStepCommands: the plot section's exit code must survive a
# successful data section (full mode). Hole: line 648 returns the data
# exit instead of the plot exit.
# -----------------------------------------------------------------------


def test_fiRunStepCommands_full_returns_plot_exit_code():
    """Data succeeds (0) but the plot command fails (7): result is 7.

    Kills: fiRunStepCommands returns (iExitCode, ...) instead of
    (iPlotExit, ...) (line ~648).
    """
    mockDocker = MagicMock()
    mockDocker.ftResultExecuteCommand.return_value = (0, "")
    # First streaming call is the data command (exit 0); the second is
    # the plot command (exit 7). There are no test commands.
    _fnConfigureStreamingMock(mockDocker, [(0, ""), (7, "")])
    fnCallback, _listCaptured = _fMockCallback()
    dictStep = {
        "sDirectory": "/ws/step",
        "saDataCommands": ["python data.py"],
        "saPlotCommands": ["python plot.py"],
        "bPlotOnly": False,
    }
    iExitCode, _fCpu = _fnRunAsync(fiRunStepCommands(
        mockDocker, "cid", dictStep, "/ws", {}, fnCallback,
        sRunMode="full",
    ))
    assert iExitCode == 7


# -----------------------------------------------------------------------
# _fbVerifyStepList: a step with a MISSING declared output must light a
# stepFail badge (not stepPass) during a verify-only run. Hole: lines
# 893-894 emit stepPass for the missing-output direction.
# -----------------------------------------------------------------------


def test_fnVerifyOnly_missing_output_emits_stepFail_badge():
    """A missing-output step emits stepFail for its step number.

    Kills: _fbVerifyStepList per-step badge '0 if bStepOk else 1'
    mutated to 0 (line ~894).
    """
    dictWorkflow = {
        "sWorkflowName": "Test",
        "listSteps": [
            {"sDirectory": "/work", "saPlotFiles": ["a.pdf"]},
        ],
    }
    mockDocker = _fMockDocker(1, "")
    fnCallback, listCaptured = _fMockCallback()
    iResult = _fnRunAsync(fnVerifyOnly(
        mockDocker, "cid", dictWorkflow, "/w/test.json",
        "/work", fnCallback,
    ))
    assert iResult == 1
    listResultEvents = [
        d for d in listCaptured
        if d.get("sType") in ("stepPass", "stepFail")
    ]
    assert {"sType": "stepFail", "iStepNumber": 1, "iExitCode": 1} in (
        listResultEvents
    )
    assert not any(d["sType"] == "stepPass" for d in listResultEvents)


# -----------------------------------------------------------------------
# _flistAppendAndMaybeDrainBatch: the buffer must flush on size when it
# reaches exactly I_BATCH_MAX_LINES. Hole: line 441 weakens >= to >, so
# a buffer of exactly 50 lines never flushes on size.
# -----------------------------------------------------------------------


def test_appendAndMaybeDrainBatch_flushes_at_exactly_fifty():
    """The 50th append drains a full I_BATCH_MAX_LINES batch on size.

    Kills: size threshold weakened from >= I_BATCH_MAX_LINES to
    > I_BATCH_MAX_LINES (line ~441).
    """
    assert I_BATCH_MAX_LINES == 50
    dictBatch = {"listLines": [], "fFirstLineAt": 0.0}
    lockBuffer = threading.Lock()
    listDrainedFinal = []
    for iLine in range(I_BATCH_MAX_LINES):
        listDrained, _bFirst = _flistAppendAndMaybeDrainBatch(
            dictBatch, lockBuffer, f"line {iLine}",
        )
        if iLine < I_BATCH_MAX_LINES - 1:
            # No size flush should occur before the buffer is full; the
            # rapid loop keeps the elapsed time under the timer bound.
            assert listDrained == []
        else:
            listDrainedFinal = listDrained
    assert len(listDrainedFinal) == I_BATCH_MAX_LINES


# -----------------------------------------------------------------------
# _fsetSnapshotDirectory: a partial listing from a non-zero find must be
# discarded. Hole: line 691 changes the guard's 'or' to 'and', so a
# failed find that still printed files is treated as a valid snapshot.
# -----------------------------------------------------------------------


def test_fsetSnapshotDirectory_empty_on_partial_with_error():
    """find exits non-zero but prints a partial file: result is empty.

    Kills: snapshot guard 'if iExit != 0 or not sOutput.strip()'
    changed to 'and' (line ~691).
    """
    mockDocker = _fMockDocker(1, "/a/partial.txt\n")
    setFiles = _fnRunAsync(_fsetSnapshotDirectory(
        mockDocker, "cid", "/a", 1,
    ))
    assert setFiles == set()
