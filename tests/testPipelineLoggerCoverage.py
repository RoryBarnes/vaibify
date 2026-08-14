"""Tests for uncovered lines in vaibify.gui.pipelineLogger."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from vaibify.gui.pipelineLogger import (
    _ffBuildFlushingCallback,
    _fnFinalizeRun,
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
        fnCallback, fnLogging, mockDocker, listLogLines = (
            self._fnBuildCallback()
        )
        # Append a buffered line so the flush path has something to
        # write — the new fnWriteLogToContainer no-ops on an empty
        # buffer to avoid an unnecessary docker exec round-trip.
        listLogLines.append("pending line")
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
    """Cover the finalize-run path.

    Completion is state-only: the deeper guarantees (an external edit
    to project.json survives completion, the merge is by stable step
    id, a failed terminal flush is reported) are driven end-to-end in
    tests/testCompletionIsStateOnly.py; these cover the event shape.
    """

    @pytest.mark.asyncio
    async def test_completed_emits_event_without_writing_project(self):
        mockDocker = MagicMock()
        fnCallback = AsyncMock()
        dictWorkflow = {"sName": "test", "listSteps": []}
        sWorkflowPath = "/workspace/repo/.vaibify/projects/w.json"

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
            mockDocker.fnWriteFile.assert_not_called()
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
            # No workflow path means the results had nowhere
            # attributable to go, and the event says so.
            assert dictEmitted["bRunMetadataPersisted"] is False


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


# ──────────────────────────────────────────────────────────────────
# outputBatch contract — every coalesced event must reach the log
# capture and the state-write buffer, or the "dashboard reflects
# truth" invariant breaks for chatty subprocess runs.
# ──────────────────────────────────────────────────────────────────


class TestOutputBatchPropagation:
    def test_logging_callback_appends_every_line_in_batch(self):
        from vaibify.gui.pipelineLogger import ffBuildLoggingCallback
        listLogLines = []
        listForwarded = []

        async def fnOriginal(dictEvent):
            listForwarded.append(dictEvent)

        fnCallback = ffBuildLoggingCallback(fnOriginal, listLogLines)
        asyncio.run(fnCallback({
            "sType": "outputBatch",
            "listLines": ["alpha", "beta", "gamma"],
        }))
        assert listLogLines == ["alpha", "beta", "gamma"]
        assert listForwarded == [{
            "sType": "outputBatch",
            "listLines": ["alpha", "beta", "gamma"],
        }]

    def test_flist_extract_handles_single_output_event(self):
        from vaibify.gui.pipelineLogger import _flistExtractLogLines
        listLines = _flistExtractLogLines(
            {"sType": "output", "sLine": "solo"},
        )
        assert listLines == ["solo"]

    def test_flist_extract_returns_all_batch_lines(self):
        from vaibify.gui.pipelineLogger import _flistExtractLogLines
        listLines = _flistExtractLogLines({
            "sType": "outputBatch",
            "listLines": ["one", "two", "three"],
        })
        assert listLines == ["one", "two", "three"]

    def test_flist_extract_returns_empty_for_non_output_events(self):
        from vaibify.gui.pipelineLogger import _flistExtractLogLines
        assert _flistExtractLogLines({"sType": "stepStarted"}) == []
        assert _flistExtractLogLines({"sType": "wsHeartbeat"}) == []

    def test_dispatch_to_writer_enqueues_batch_lines_individually(self):
        """Each line in an outputBatch must reach the state writer."""
        from vaibify.gui.pipelineLogger import _fnDispatchEventToWriter
        mockWriter = MagicMock()
        _fnDispatchEventToWriter(mockWriter, {
            "sType": "outputBatch",
            "listLines": ["alpha", "beta"],
        })
        assert mockWriter.fnEnqueueOutputLine.call_count == 2
        listAppended = [
            tCall.args[0]
            for tCall in mockWriter.fnEnqueueOutputLine.call_args_list
        ]
        assert listAppended == ["alpha", "beta"]
