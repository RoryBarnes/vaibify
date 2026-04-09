"""Tests for uncovered lines in vaibify.gui.pipelineLogger."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from vaibify.gui.pipelineLogger import (
    _ffBuildFlushingCallback,
    _fnFinalizeRun,
    _fnSaveWorkflowStats,
    _fnUpdatePipelineState,
    fnWriteLogToContainer,
)


# ---------------------------------------------------------------------------
# _ffBuildFlushingCallback  (lines 83-94)
# ---------------------------------------------------------------------------

class TestFfBuildFlushingCallback:
    """Cover the inner fnLoggingWithFlush callback."""

    def _fnBuildCallback(self):
        """Return the callback and its collaborators."""
        fnLogging = AsyncMock()
        mockDocker = MagicMock()
        sContainerId = "abc123"
        dictState = {"sStatus": "running"}
        sLogPath = "/workspace/.vaibify/logs/test.log"
        listLogLines = []
        fnCallback = _ffBuildFlushingCallback(
            fnLogging, mockDocker, sContainerId,
            dictState, sLogPath, listLogLines,
        )
        return fnCallback, fnLogging, mockDocker, listLogLines

    @pytest.mark.asyncio
    async def test_forwards_event_and_updates_state(self):
        fnCallback, fnLogging, _, _ = self._fnBuildCallback()
        dictEvent = {"sType": "output", "sLine": "hello"}
        with patch(
            "vaibify.gui.pipelineLogger.pipelineState"
        ) as mockPS:
            await fnCallback(dictEvent)
            fnLogging.assert_awaited_once_with(dictEvent)
            mockPS.fnAppendOutput.assert_called_once()

    @pytest.mark.asyncio
    async def test_flushes_log_on_step_pass(self):
        fnCallback, fnLogging, mockDocker, _ = self._fnBuildCallback()
        dictEvent = {"sType": "stepPass", "iStepNumber": 0,
                     "iExitCode": 0}
        with patch(
            "vaibify.gui.pipelineLogger.pipelineState"
        ), patch(
            "vaibify.gui.pipelineLogger.asyncio.to_thread",
            new_callable=AsyncMock,
        ) as mockToThread:
            await fnCallback(dictEvent)
            fnLogging.assert_awaited_once()
            mockToThread.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_flushes_log_on_step_fail(self):
        fnCallback, fnLogging, _, _ = self._fnBuildCallback()
        dictEvent = {"sType": "stepFail", "iStepNumber": 0,
                     "iExitCode": 1}
        with patch(
            "vaibify.gui.pipelineLogger.pipelineState"
        ), patch(
            "vaibify.gui.pipelineLogger.asyncio.to_thread",
            new_callable=AsyncMock,
        ):
            await fnCallback(dictEvent)
            fnLogging.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_no_flush_on_started_event(self):
        fnCallback, fnLogging, _, _ = self._fnBuildCallback()
        dictEvent = {"sType": "stepStarted", "iStepNumber": 0}
        with patch(
            "vaibify.gui.pipelineLogger.pipelineState"
        ), patch(
            "vaibify.gui.pipelineLogger.asyncio.to_thread",
            new_callable=AsyncMock,
        ) as mockToThread:
            await fnCallback(dictEvent)
            mockToThread.assert_not_awaited()


# ---------------------------------------------------------------------------
# _fnFinalizeRun  (lines 144-156)
# ---------------------------------------------------------------------------

class TestFnFinalizeRun:
    """Cover the finalize-run path."""

    @pytest.mark.asyncio
    async def test_completed_with_workflow_save(self):
        mockDocker = MagicMock()
        fnCallback = AsyncMock()
        dictWorkflow = {"sName": "test"}
        sWorkflowPath = "/workspace/workflow.json"

        with patch(
            "vaibify.gui.pipelineLogger.pipelineState"
        ) as mockPS, patch(
            "vaibify.gui.pipelineLogger.asyncio.to_thread",
            new_callable=AsyncMock,
        ):
            mockPS.fdictBuildCompletedState.return_value = {
                "sStatus": "completed"
            }
            await _fnFinalizeRun(
                mockDocker, "cid", {}, 0,
                "/log", ["done"], dictWorkflow,
                sWorkflowPath, fnCallback,
            )
            mockPS.fnUpdateState.assert_called_once()
            mockDocker.fnWriteFile.assert_called_once()
            fnCallback.assert_awaited_once()
            dictEmitted = fnCallback.call_args[0][0]
            assert dictEmitted["sType"] == "completed"
            assert dictEmitted["iExitCode"] == 0

    @pytest.mark.asyncio
    async def test_failed_without_workflow_path(self):
        mockDocker = MagicMock()
        fnCallback = AsyncMock()

        with patch(
            "vaibify.gui.pipelineLogger.pipelineState"
        ) as mockPS, patch(
            "vaibify.gui.pipelineLogger.asyncio.to_thread",
            new_callable=AsyncMock,
        ):
            mockPS.fdictBuildCompletedState.return_value = {
                "sStatus": "failed"
            }
            await _fnFinalizeRun(
                mockDocker, "cid", {}, 1,
                "/log", [], {}, "",
                fnCallback,
            )
            mockDocker.fnWriteFile.assert_not_called()
            dictEmitted = fnCallback.call_args[0][0]
            assert dictEmitted["sType"] == "failed"
            assert dictEmitted["iExitCode"] == 1

    @pytest.mark.asyncio
    async def test_failed_with_workflow_save_error(self):
        mockDocker = MagicMock()
        mockDocker.fnWriteFile.side_effect = RuntimeError("disk full")
        fnCallback = AsyncMock()

        with patch(
            "vaibify.gui.pipelineLogger.pipelineState"
        ) as mockPS, patch(
            "vaibify.gui.pipelineLogger.asyncio.to_thread",
            new_callable=AsyncMock,
        ):
            mockPS.fdictBuildCompletedState.return_value = {}
            await _fnFinalizeRun(
                mockDocker, "cid", {}, 2,
                "/log", ["err"], {"sName": "w"}, "/wf.json",
                fnCallback,
            )
            fnCallback.assert_awaited_once()
            dictEmitted = fnCallback.call_args[0][0]
            assert dictEmitted["sType"] == "failed"


# ---------------------------------------------------------------------------
# _fnSaveWorkflowStats  (lines 127-134)
# ---------------------------------------------------------------------------

class TestFnSaveWorkflowStats:
    """Cover workflow stats persistence."""

    def test_writes_json_to_container(self):
        mockDocker = MagicMock()
        dictWorkflow = {"sName": "myWorkflow"}
        _fnSaveWorkflowStats(
            mockDocker, "cid", dictWorkflow, "/wf.json"
        )
        mockDocker.fnWriteFile.assert_called_once()

    def test_logs_error_on_failure(self):
        mockDocker = MagicMock()
        mockDocker.fnWriteFile.side_effect = OSError("fail")
        _fnSaveWorkflowStats(
            mockDocker, "cid", {}, "/wf.json"
        )


# ---------------------------------------------------------------------------
# _fnUpdatePipelineState  (lines 101-120)
# ---------------------------------------------------------------------------

class TestFnUpdatePipelineState:
    """Cover state update dispatch."""

    def test_output_event(self):
        mockDocker = MagicMock()
        dictState = {}
        dictEvent = {"sType": "output", "sLine": "data"}
        with patch(
            "vaibify.gui.pipelineLogger.pipelineState"
        ) as mockPS:
            _fnUpdatePipelineState(
                mockDocker, "cid", dictState, dictEvent
            )
            mockPS.fnAppendOutput.assert_called_once()

    def test_step_started_event(self):
        mockDocker = MagicMock()
        dictState = {}
        dictEvent = {"sType": "stepStarted", "iStepNumber": 0}
        with patch(
            "vaibify.gui.pipelineLogger.pipelineState"
        ) as mockPS:
            mockPS.fdictBuildStepStarted.return_value = {}
            _fnUpdatePipelineState(
                mockDocker, "cid", dictState, dictEvent
            )
            mockPS.fnUpdateState.assert_called_once()

    def test_step_pass_event(self):
        mockDocker = MagicMock()
        dictState = {}
        dictEvent = {"sType": "stepPass", "iStepNumber": 0,
                     "iExitCode": 0}
        with patch(
            "vaibify.gui.pipelineLogger.pipelineState"
        ) as mockPS:
            mockPS.fdictBuildStepResult.return_value = {}
            _fnUpdatePipelineState(
                mockDocker, "cid", dictState, dictEvent
            )
            mockPS.fnRecordStepResult.assert_called_once()

    def test_step_skipped_event(self):
        mockDocker = MagicMock()
        dictState = {}
        dictEvent = {"sType": "stepSkipped", "iStepNumber": 1,
                     "iExitCode": 0}
        with patch(
            "vaibify.gui.pipelineLogger.pipelineState"
        ) as mockPS:
            mockPS.fdictBuildStepResult.return_value = {}
            _fnUpdatePipelineState(
                mockDocker, "cid", dictState, dictEvent
            )
            mockPS.fnRecordStepResult.assert_called_once()

    def test_unknown_event_is_no_op(self):
        mockDocker = MagicMock()
        dictState = {}
        dictEvent = {"sType": "unknown"}
        with patch(
            "vaibify.gui.pipelineLogger.pipelineState"
        ) as mockPS:
            _fnUpdatePipelineState(
                mockDocker, "cid", dictState, dictEvent
            )
            mockPS.fnAppendOutput.assert_not_called()
            mockPS.fnUpdateState.assert_not_called()
            mockPS.fnRecordStepResult.assert_not_called()
