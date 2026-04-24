"""Tests for uncovered lines in vaibify.gui.pipelineServer."""

import asyncio
import json

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from vaibify.gui.pipelineServer import (
    _fnDispatchRunFrom,
    _fnHandleInteractiveComplete,
    _fnHandleInteractiveResponse,
    _fnHandleTerminalText,
    _fnSafeDispatch,
    _fconnectionCreateDocker,
    _ftupleBuildHelpers,
    fnDispatchAction,
    fnHandlePipelineWs,
    fnPipelineMessageLoop,
    fnRejectNotConnected,
    fnRejectTerminalStart,
    fnRunTerminalSession,
    fnTerminalInputLoop,
    fnTerminalReadLoop,
    fsSanitizeExceptionForClient,
)


# ---------------------------------------------------------------
# _fnDispatchRunFrom (line 382)
# ---------------------------------------------------------------

class TestDispatchRunFrom:
    @pytest.mark.asyncio
    async def test_dispatches_with_start_step(self):
        mockDocker = MagicMock()
        sContainerId = "ctr1"
        dictRequest = {"iStartStep": 3}
        fnCallback = AsyncMock()
        dictWorkflow = {"listSteps": []}
        with patch(
            "vaibify.gui.pipelineServer.fnRunFromStep",
            new_callable=AsyncMock,
        ) as mockRunFrom:
            await _fnDispatchRunFrom(
                mockDocker, sContainerId, dictRequest,
                dictWorkflow, "/workspace", fnCallback,
            )
            mockRunFrom.assert_called_once_with(
                mockDocker, sContainerId, 3, "/workspace",
                fnCallback, dictInteractive=None,
            )

    @pytest.mark.asyncio
    async def test_defaults_to_step_one(self):
        mockDocker = MagicMock()
        fnCallback = AsyncMock()
        dictWorkflow = {"listSteps": []}
        with patch(
            "vaibify.gui.pipelineServer.fnRunFromStep",
            new_callable=AsyncMock,
        ) as mockRunFrom:
            await _fnDispatchRunFrom(
                mockDocker, "ctr1", {},
                dictWorkflow, "/workspace", fnCallback,
            )
            assert mockRunFrom.call_args[0][2] == 1

    @pytest.mark.asyncio
    async def test_resolves_start_step_label(self):
        """sStartStepLabel 'A01' resolves to iStartStep=2 (1-based)."""
        mockDocker = MagicMock()
        fnCallback = AsyncMock()
        dictWorkflow = {"listSteps": [
            {"sName": "Intro", "bInteractive": True},
            {"sName": "Auto1"},
        ]}
        dictRequest = {"sStartStepLabel": "A01"}
        with patch(
            "vaibify.gui.pipelineServer.fnRunFromStep",
            new_callable=AsyncMock,
        ) as mockRunFrom:
            await _fnDispatchRunFrom(
                mockDocker, "ctr1", dictRequest,
                dictWorkflow, "/workspace", fnCallback,
            )
            # A01 is listSteps[1] (index 1); 1-based = 2.
            assert mockRunFrom.call_args[0][2] == 2


# ---------------------------------------------------------------
# fnDispatchAction — all branches (lines 397-421)
# ---------------------------------------------------------------

class TestDispatchAction:
    async def _fnRun(self, sAction, dictRequest=None):
        if dictRequest is None:
            dictRequest = {}
        mockDocker = MagicMock()
        fnCallback = AsyncMock()
        dictWorkflow = {"listSteps": []}
        dictPathCache = {"ctr1": "/workspace/.vaibify/w.yml"}
        return await fnDispatchAction(
            sAction, dictRequest, mockDocker, "ctr1",
            dictWorkflow, dictPathCache,
            "/workspace", fnCallback,
        )

    @pytest.mark.asyncio
    @patch(
        "vaibify.gui.pipelineServer.fnRunAllSteps",
        new_callable=AsyncMock,
    )
    async def test_run_all(self, mockRun):
        await self._fnRun("runAll")
        mockRun.assert_called_once()
        assert mockRun.call_args.kwargs.get("bForceRun") is None or \
            not mockRun.call_args.kwargs.get("bForceRun")

    @pytest.mark.asyncio
    @patch(
        "vaibify.gui.pipelineServer.fnRunAllSteps",
        new_callable=AsyncMock,
    )
    async def test_force_run_all(self, mockRun):
        await self._fnRun("forceRunAll")
        mockRun.assert_called_once()
        assert mockRun.call_args.kwargs["bForceRun"] is True

    @pytest.mark.asyncio
    @patch(
        "vaibify.gui.pipelineServer._fnDispatchRunFrom",
        new_callable=AsyncMock,
    )
    async def test_run_from(self, mockRunFrom):
        await self._fnRun("runFrom", {"iStartStep": 2})
        mockRunFrom.assert_called_once()

    @pytest.mark.asyncio
    @patch(
        "vaibify.gui.pipelineServer.fnVerifyOnly",
        new_callable=AsyncMock,
    )
    async def test_verify(self, mockVerify):
        await self._fnRun("verify")
        mockVerify.assert_called_once()

    @pytest.mark.asyncio
    @patch(
        "vaibify.gui.pipelineServer.fnRunAllTests",
        new_callable=AsyncMock,
    )
    async def test_run_all_tests(self, mockTests):
        await self._fnRun("runAllTests")
        mockTests.assert_called_once()

    @pytest.mark.asyncio
    @patch(
        "vaibify.gui.pipelineServer._fnDispatchSelected",
        new_callable=AsyncMock,
    )
    async def test_run_selected(self, mockSelected):
        await self._fnRun("runSelected", {"listStepIndices": [0, 2]})
        mockSelected.assert_called_once()


# ---------------------------------------------------------------
# _fnDispatchSelected (line 430)
# ---------------------------------------------------------------

class TestDispatchSelected:
    @pytest.mark.asyncio
    @patch(
        "vaibify.gui.pipelineServer.fnRunSelectedSteps",
        new_callable=AsyncMock,
    )
    async def test_dispatches_selected_steps(self, mockRunSelected):
        dictRequest = {"listStepIndices": [1, 3]}
        dictWorkflow = {"listSteps": []}
        dictPathCache = {"ctr1": "/workspace/.vaibify/w.yml"}
        from vaibify.gui.pipelineServer import _fnDispatchSelected
        await _fnDispatchSelected(
            MagicMock(), "ctr1", dictRequest,
            dictWorkflow, dictPathCache,
            "/workspace", AsyncMock(),
        )
        mockRunSelected.assert_called_once()
        listIndices = mockRunSelected.call_args[0][2]
        assert listIndices == [1, 3]

    @pytest.mark.asyncio
    @patch(
        "vaibify.gui.pipelineServer.fnRunSelectedSteps",
        new_callable=AsyncMock,
    )
    async def test_resolves_step_labels(self, mockRunSelected):
        """listStepLabels translate via the canonical helper."""
        dictRequest = {"listStepLabels": ["A01", "A02"]}
        dictWorkflow = {"listSteps": [
            {"sName": "Intro", "bInteractive": True},
            {"sName": "Auto1"},
            {"sName": "Auto2"},
        ]}
        dictPathCache = {"ctr1": "/workspace/.vaibify/w.yml"}
        from vaibify.gui.pipelineServer import _fnDispatchSelected
        await _fnDispatchSelected(
            MagicMock(), "ctr1", dictRequest,
            dictWorkflow, dictPathCache,
            "/workspace", AsyncMock(),
        )
        listIndices = mockRunSelected.call_args[0][2]
        assert listIndices == [1, 2]

    @pytest.mark.asyncio
    @patch(
        "vaibify.gui.pipelineServer.fnRunSelectedSteps",
        new_callable=AsyncMock,
    )
    async def test_merges_indices_and_labels_deduplicated(
        self, mockRunSelected,
    ):
        """Indices first, labels second, duplicates dropped."""
        dictRequest = {
            "listStepIndices": [1],
            "listStepLabels": ["A01", "A02"],
        }
        dictWorkflow = {"listSteps": [
            {"sName": "Intro", "bInteractive": True},
            {"sName": "Auto1"},
            {"sName": "Auto2"},
        ]}
        dictPathCache = {"ctr1": "/workspace/.vaibify/w.yml"}
        from vaibify.gui.pipelineServer import _fnDispatchSelected
        await _fnDispatchSelected(
            MagicMock(), "ctr1", dictRequest,
            dictWorkflow, dictPathCache,
            "/workspace", AsyncMock(),
        )
        listIndices = mockRunSelected.call_args[0][2]
        # A01 -> 1 is a duplicate of listStepIndices[0]; A02 -> 2.
        assert listIndices == [1, 2]


# ---------------------------------------------------------------
# _fnSafeDispatch (lines 485-504)
# ---------------------------------------------------------------

class TestSafeDispatch:
    @pytest.mark.asyncio
    async def test_success_delegates_to_dispatch(self):
        fnCallback = AsyncMock()
        with patch(
            "vaibify.gui.pipelineServer.fnDispatchAction",
            new_callable=AsyncMock,
        ) as mockDispatch:
            await _fnSafeDispatch(
                "runAll", {}, MagicMock(), "ctr1",
                {}, {}, "/workspace", fnCallback, None,
            )
            mockDispatch.assert_called_once()

    @pytest.mark.asyncio
    async def test_error_sends_failed_event(self):
        fnCallback = AsyncMock()
        with patch(
            "vaibify.gui.pipelineServer.fnDispatchAction",
            new_callable=AsyncMock,
            side_effect=RuntimeError("boom"),
        ):
            await _fnSafeDispatch(
                "runAll", {}, MagicMock(), "ctr1",
                {}, {}, "/workspace", fnCallback, None,
            )
            fnCallback.assert_called_once()
            dictEvent = fnCallback.call_args[0][0]
            assert dictEvent["sType"] == "failed"
            assert dictEvent["iExitCode"] == 1

    @pytest.mark.asyncio
    async def test_error_callback_failure_is_swallowed(self):
        fnCallback = AsyncMock(side_effect=RuntimeError("ws closed"))
        with patch(
            "vaibify.gui.pipelineServer.fnDispatchAction",
            new_callable=AsyncMock,
            side_effect=RuntimeError("boom"),
        ):
            await _fnSafeDispatch(
                "runAll", {}, MagicMock(), "ctr1",
                {}, {}, "/workspace", fnCallback, None,
            )


# ---------------------------------------------------------------
# fnPipelineMessageLoop (lines 444-476)
# ---------------------------------------------------------------

class TestPipelineMessageLoop:
    @pytest.mark.asyncio
    async def test_dispatches_action_and_stores_task(self):
        mockWebsocket = AsyncMock()
        listMessages = [
            json.dumps({"sAction": "runAll"}),
        ]
        iCallCount = 0

        async def fnReceiveText():
            nonlocal iCallCount
            if iCallCount < len(listMessages):
                sMessage = listMessages[iCallCount]
                iCallCount += 1
                return sMessage
            raise Exception("disconnect")

        mockWebsocket.receive_text = fnReceiveText
        mockWebsocket.send_json = AsyncMock()
        dictPipelineTasks = {}

        with patch(
            "vaibify.gui.pipelineServer._fnSafeDispatch",
            new_callable=AsyncMock,
        ), patch(
            "vaibify.gui.pipelineRunner.fdictCreateInteractiveContext",
            return_value={},
        ):
            with pytest.raises(Exception, match="disconnect"):
                await fnPipelineMessageLoop(
                    mockWebsocket, MagicMock(), "ctr1",
                    {}, {}, "/workspace",
                    dictPipelineTasks=dictPipelineTasks,
                )

    @pytest.mark.asyncio
    async def test_interactive_resume_skips_dispatch(self):
        mockWebsocket = AsyncMock()
        listMessages = [
            json.dumps({"sAction": "interactiveResume"}),
        ]
        iCallCount = 0

        async def fnReceiveText():
            nonlocal iCallCount
            if iCallCount < len(listMessages):
                sMessage = listMessages[iCallCount]
                iCallCount += 1
                return sMessage
            raise Exception("disconnect")

        mockWebsocket.receive_text = fnReceiveText

        with patch(
            "vaibify.gui.pipelineRunner.fdictCreateInteractiveContext",
            return_value={},
        ), patch(
            "vaibify.gui.pipelineRunner.fnSetInteractiveResponse",
        ) as mockSet:
            with pytest.raises(Exception, match="disconnect"):
                await fnPipelineMessageLoop(
                    mockWebsocket, MagicMock(), "ctr1",
                    {}, {}, "/workspace",
                )
            mockSet.assert_called_once_with({}, "resume")

    @pytest.mark.asyncio
    async def test_interactive_complete_handled(self):
        mockWebsocket = AsyncMock()
        listMessages = [
            json.dumps({
                "sAction": "interactiveComplete",
                "iExitCode": 42,
            }),
        ]
        iCallCount = 0

        async def fnReceiveText():
            nonlocal iCallCount
            if iCallCount < len(listMessages):
                sMessage = listMessages[iCallCount]
                iCallCount += 1
                return sMessage
            raise Exception("disconnect")

        mockWebsocket.receive_text = fnReceiveText

        with patch(
            "vaibify.gui.pipelineRunner.fdictCreateInteractiveContext",
            return_value={},
        ), patch(
            "vaibify.gui.pipelineRunner.fnSetInteractiveResponse",
        ) as mockSet:
            with pytest.raises(Exception, match="disconnect"):
                await fnPipelineMessageLoop(
                    mockWebsocket, MagicMock(), "ctr1",
                    {}, {}, "/workspace",
                )
            mockSet.assert_called_once_with({}, "complete:42")

    @pytest.mark.asyncio
    async def test_interactive_skip_handled(self):
        mockWebsocket = AsyncMock()
        listMessages = [
            json.dumps({"sAction": "interactiveSkip"}),
        ]
        iCallCount = 0

        async def fnReceiveText():
            nonlocal iCallCount
            if iCallCount < len(listMessages):
                sMessage = listMessages[iCallCount]
                iCallCount += 1
                return sMessage
            raise Exception("disconnect")

        mockWebsocket.receive_text = fnReceiveText

        with patch(
            "vaibify.gui.pipelineRunner.fdictCreateInteractiveContext",
            return_value={},
        ), patch(
            "vaibify.gui.pipelineRunner.fnSetInteractiveResponse",
        ) as mockSet:
            with pytest.raises(Exception, match="disconnect"):
                await fnPipelineMessageLoop(
                    mockWebsocket, MagicMock(), "ctr1",
                    {}, {}, "/workspace",
                )
            mockSet.assert_called_once_with({}, "skip")


# ---------------------------------------------------------------
# fnTerminalReadLoop (lines 533-541)
# ---------------------------------------------------------------

class TestTerminalReadLoop:
    @pytest.mark.asyncio
    async def test_sends_output_bytes(self):
        mockSession = MagicMock()
        mockSession._bRunning = True
        iCallCount = 0

        def fnFakeRead():
            nonlocal iCallCount
            iCallCount += 1
            if iCallCount == 1:
                return b"hello"
            mockSession._bRunning = False
            return b""

        mockSession.fbaReadOutput = fnFakeRead
        mockWebsocket = AsyncMock()

        await fnTerminalReadLoop(mockSession, mockWebsocket)
        mockWebsocket.send_bytes.assert_called_with(b"hello")

    @pytest.mark.asyncio
    async def test_exception_breaks_loop(self):
        mockSession = MagicMock()
        mockSession._bRunning = True
        mockSession.fbaReadOutput.side_effect = RuntimeError("boom")
        mockWebsocket = AsyncMock()

        await fnTerminalReadLoop(mockSession, mockWebsocket)


# ---------------------------------------------------------------
# fnTerminalInputLoop (lines 547-553)
# ---------------------------------------------------------------

class TestTerminalInputLoop:
    @pytest.mark.asyncio
    async def test_sends_bytes_input(self):
        mockSession = MagicMock()
        mockWebsocket = AsyncMock()
        listMessages = [
            {"type": "websocket.text", "bytes": b"data"},
            {"type": "websocket.disconnect"},
        ]
        iCallCount = 0

        async def fnReceive():
            nonlocal iCallCount
            dictMsg = listMessages[iCallCount]
            iCallCount += 1
            return dictMsg

        mockWebsocket.receive = fnReceive

        await fnTerminalInputLoop(mockSession, mockWebsocket)
        mockSession.fnSendInput.assert_called_once_with(b"data")

    @pytest.mark.asyncio
    async def test_sends_text_as_json(self):
        mockSession = MagicMock()
        mockWebsocket = AsyncMock()
        sJsonText = json.dumps(
            {"sType": "resize", "iRows": 30, "iColumns": 100},
        )
        listMessages = [
            {"type": "websocket.text", "text": sJsonText},
            {"type": "websocket.disconnect"},
        ]
        iCallCount = 0

        async def fnReceive():
            nonlocal iCallCount
            dictMsg = listMessages[iCallCount]
            iCallCount += 1
            return dictMsg

        mockWebsocket.receive = fnReceive

        await fnTerminalInputLoop(mockSession, mockWebsocket)
        mockSession.fnResize.assert_called_once_with(30, 100)


# ---------------------------------------------------------------
# _fnHandleTerminalText (lines 556-567)
# ---------------------------------------------------------------

class TestHandleTerminalText:
    def test_resize_message(self):
        mockSession = MagicMock()
        sText = json.dumps(
            {"sType": "resize", "iRows": 50, "iColumns": 120},
        )
        _fnHandleTerminalText(mockSession, sText)
        mockSession.fnResize.assert_called_once_with(50, 120)

    def test_kill_message(self):
        mockSession = MagicMock()
        sText = json.dumps({"sType": "kill"})
        _fnHandleTerminalText(mockSession, sText)
        mockSession.fnKillForeground.assert_called_once()

    def test_invalid_json_ignored(self):
        mockSession = MagicMock()
        _fnHandleTerminalText(mockSession, "not json")
        mockSession.fnResize.assert_not_called()
        mockSession.fnKillForeground.assert_not_called()

    def test_resize_clamps_values(self):
        mockSession = MagicMock()
        sText = json.dumps(
            {"sType": "resize", "iRows": 9999, "iColumns": 9999},
        )
        _fnHandleTerminalText(mockSession, sText)
        mockSession.fnResize.assert_called_once_with(500, 1000)


# ---------------------------------------------------------------
# fnRejectTerminalStart (lines 572-575)
# ---------------------------------------------------------------

class TestRejectTerminalStart:
    @pytest.mark.asyncio
    async def test_sends_error_and_closes(self):
        mockWebsocket = AsyncMock()
        await fnRejectTerminalStart(mockWebsocket, "PTY failed")
        mockWebsocket.send_json.assert_called_once()
        dictMsg = mockWebsocket.send_json.call_args[0][0]
        assert dictMsg["sType"] == "error"
        assert "PTY failed" in dictMsg["sMessage"]
        mockWebsocket.close.assert_called_once()


# ---------------------------------------------------------------
# fnRejectNotConnected (lines 580-583)
# ---------------------------------------------------------------

class TestRejectNotConnected:
    @pytest.mark.asyncio
    async def test_sends_not_connected_and_closes(self):
        mockWebsocket = AsyncMock()
        await fnRejectNotConnected(mockWebsocket)
        mockWebsocket.send_json.assert_called_once()
        dictMsg = mockWebsocket.send_json.call_args[0][0]
        assert dictMsg["sType"] == "error"
        assert "Not connected" in dictMsg["sMessage"]
        mockWebsocket.close.assert_called_once()


# ---------------------------------------------------------------
# fnRunTerminalSession (lines 590-605)
# ---------------------------------------------------------------

class TestRunTerminalSession:
    @pytest.mark.asyncio
    async def test_session_lifecycle(self):
        mockSession = MagicMock()
        mockSession.sSessionId = "sess-1"
        mockWebsocket = AsyncMock()
        dictTerminals = {}

        with patch(
            "vaibify.gui.pipelineServer.fnTerminalReadLoop",
            new_callable=AsyncMock,
        ), patch(
            "vaibify.gui.pipelineServer.fnTerminalInputLoop",
            new_callable=AsyncMock,
        ):
            await fnRunTerminalSession(
                mockSession, mockWebsocket, dictTerminals,
            )

        mockWebsocket.send_json.assert_called_once()
        dictMsg = mockWebsocket.send_json.call_args[0][0]
        assert dictMsg["sType"] == "connected"
        assert dictMsg["sSessionId"] == "sess-1"
        mockSession.fnClose.assert_called_once()
        assert "sess-1" not in dictTerminals

    @pytest.mark.asyncio
    async def test_session_cleanup_on_disconnect(self):
        from fastapi import WebSocketDisconnect
        mockSession = MagicMock()
        mockSession.sSessionId = "sess-2"
        mockWebsocket = AsyncMock()
        dictTerminals = {}

        with patch(
            "vaibify.gui.pipelineServer.fnTerminalReadLoop",
            new_callable=AsyncMock,
        ), patch(
            "vaibify.gui.pipelineServer.fnTerminalInputLoop",
            new_callable=AsyncMock,
            side_effect=WebSocketDisconnect(),
        ):
            await fnRunTerminalSession(
                mockSession, mockWebsocket, dictTerminals,
            )

        mockSession.fnClose.assert_called_once()
        assert "sess-2" not in dictTerminals


# ---------------------------------------------------------------
# fnHandlePipelineWs (lines 614-627)
# ---------------------------------------------------------------

class TestHandlePipelineWs:
    @pytest.mark.asyncio
    async def test_no_workflow_rejects(self):
        mockWebsocket = AsyncMock()
        dictCtx = {
            "workflows": {},
            "paths": {},
            "docker": MagicMock(),
            "pipelineTasks": {},
        }
        await fnHandlePipelineWs(mockWebsocket, dictCtx, "ctr1")
        mockWebsocket.accept.assert_called_once()
        mockWebsocket.send_json.assert_called_once()
        dictMsg = mockWebsocket.send_json.call_args[0][0]
        assert dictMsg["sType"] == "error"
        mockWebsocket.close.assert_called_once()

    @pytest.mark.asyncio
    async def test_with_workflow_runs_loop(self):
        from fastapi import WebSocketDisconnect
        mockWebsocket = AsyncMock()
        dictCtx = {
            "workflows": {"ctr1": {"listSteps": []}},
            "paths": {"ctr1": "/workspace/.vaibify/w.yml"},
            "docker": MagicMock(),
            "pipelineTasks": {},
        }
        with patch(
            "vaibify.gui.pipelineServer.fnPipelineMessageLoop",
            new_callable=AsyncMock,
            side_effect=WebSocketDisconnect(),
        ):
            await fnHandlePipelineWs(mockWebsocket, dictCtx, "ctr1")
        mockWebsocket.accept.assert_called_once()


# ---------------------------------------------------------------
# _fconnectionCreateDocker (lines 1022-1026)
# ---------------------------------------------------------------

class TestCreateDocker:
    def test_returns_none_on_exception(self):
        with patch.dict("sys.modules", {
            "vaibify.docker": MagicMock(),
            "vaibify.docker.dockerConnection": MagicMock(
                DockerConnection=MagicMock(
                    side_effect=RuntimeError("no docker"),
                ),
            ),
        }):
            sResult = _fconnectionCreateDocker()
            assert sResult is None


# ---------------------------------------------------------------
# _ftupleBuildHelpers — fnWorkflowDir (lines 978-986)
# ---------------------------------------------------------------

class TestWorkflowDir:
    def test_returns_workspace_root_when_no_path(self):
        _, _, _, fnWorkflowDir = _ftupleBuildHelpers(
            MagicMock(), {}, {},
        )
        sResult = fnWorkflowDir("ctr1")
        assert sResult == "/workspace"

    def test_returns_parent_of_vaibify_dir(self):
        dictPaths = {"ctr1": "/workspace/project/.vaibify/w.yml"}
        _, _, _, fnWorkflowDir = _ftupleBuildHelpers(
            MagicMock(), {}, dictPaths,
        )
        sResult = fnWorkflowDir("ctr1")
        assert sResult == "/workspace/project"

    def test_returns_dirname_without_vaibify(self):
        dictPaths = {"ctr1": "/workspace/project/workflow.yml"}
        _, _, _, fnWorkflowDir = _ftupleBuildHelpers(
            MagicMock(), {}, dictPaths,
        )
        sResult = fnWorkflowDir("ctr1")
        assert sResult == "/workspace/project"


# ---------------------------------------------------------------
# _fbCaffeinateRunning exception (lines 703-704)
# ---------------------------------------------------------------

class TestCaffeinateRunning:
    def test_returns_false_on_file_not_found(self):
        from vaibify.gui.pipelineServer import _fbCaffeinateRunning
        with patch("subprocess.run", side_effect=FileNotFoundError):
            bResult = _fbCaffeinateRunning()
            assert bResult is False
