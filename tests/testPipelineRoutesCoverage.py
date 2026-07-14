"""Tests for uncovered lines in vaibify.gui.routes.pipelineRoutes."""

import contextlib
import json

import pytest
from unittest.mock import MagicMock, patch, AsyncMock

from vaibify.gui.routes.pipelineRoutes import (
    _S_LEVEL_RATCHET_FLAG_KEY,
    _fnSaveIfLevelHighWaterChanged,
    _fbCancelPipelineTask,
    _fbMarkerStale,
    _fdictBuildTestFileChanges,
    _fdictBuildTestMarkerStatus,
    _fdictFetchTestMarkers,
    _flistBuildCleanCommands,
    _flistExtractKillPatterns,
    _flistExtractStepDirectories,
    _flistFindCustomTestFiles,
    _fnApplyAllMarkerCategories,
    _fnApplyExternalTestResults,
    _fnApplyMarkerCategory,
    _fnEnsureConftestTemplate,
    _fnMarkPipelineStopped,
    _fsetExtractRegisteredTestFiles,
    _fiCountMatchingProcesses,
    _fnKillMatchingProcesses,
    _fnRefreshConftestsAndMigrateMarkers,
    _fnDeleteLegacyMarkers,
    _fdictFetchOutputStatus,
    fdictComputeFileStatus,
)


# ── Line 73: _fnMarkPipelineStopped when dictState is running ─────

class TestFnMarkPipelineStopped:
    @pytest.mark.asyncio
    async def test_state_is_running_writes_completed(self):
        """A live pipeline gets flipped to bRunning=False on kill."""
        mockDocker = MagicMock()
        dictCtx = {"docker": mockDocker}
        with patch(
            "vaibify.gui.pipelineState.fdictReadReconciledState",
            new=AsyncMock(return_value={"bRunning": True}),
        ), patch(
            "vaibify.gui.pipelineState.fnUpdateState",
        ) as mockUpdate, patch(
            "vaibify.gui.pipelineState.fdictBuildCompletedState",
            return_value={"bRunning": False},
        ):
            await _fnMarkPipelineStopped(dictCtx, "cid1")
            mockUpdate.assert_called_once()

    @pytest.mark.asyncio
    async def test_state_none_returns_early(self):
        mockDocker = MagicMock()
        dictCtx = {"docker": mockDocker}
        with patch(
            "vaibify.gui.pipelineState.fdictReadReconciledState",
            new=AsyncMock(return_value=None),
        ), patch(
            "vaibify.gui.pipelineState.fnUpdateState",
        ) as mockUpdate:
            await _fnMarkPipelineStopped(dictCtx, "cid1")
            mockUpdate.assert_not_called()

    @pytest.mark.asyncio
    async def test_state_not_running_returns_early(self):
        mockDocker = MagicMock()
        dictCtx = {"docker": mockDocker}
        with patch(
            "vaibify.gui.pipelineState.fdictReadReconciledState",
            new=AsyncMock(return_value={"bRunning": False}),
        ), patch(
            "vaibify.gui.pipelineState.fnUpdateState",
        ) as mockUpdate:
            await _fnMarkPipelineStopped(dictCtx, "cid1")
            mockUpdate.assert_not_called()


# ── Lines 117-118: _fiCountMatchingProcesses ValueError branch ───

class TestFiCountMatchingProcesses:
    @pytest.mark.asyncio
    async def test_valid_count(self):
        mockDocker = MagicMock()
        mockDocker.ftResultExecuteCommand.return_value = (0, "5\n")
        iResult = await _fiCountMatchingProcesses(
            mockDocker, "cid", "pattern"
        )
        assert iResult == 5

    @pytest.mark.asyncio
    async def test_value_error_returns_zero(self):
        """Cover lines 117-118: ValueError -> return 0."""
        mockDocker = MagicMock()
        mockDocker.ftResultExecuteCommand.return_value = (
            0, "not-a-number\n"
        )
        iResult = await _fiCountMatchingProcesses(
            mockDocker, "cid", "pattern"
        )
        assert iResult == 0


# ── Lines 125-132: _fnKillMatchingProcesses ──────────────────────

class TestFnKillMatchingProcesses:
    @pytest.mark.asyncio
    async def test_kills_each_pattern(self):
        """Cover lines 125-132."""
        mockDocker = MagicMock()
        mockDocker.ftResultExecuteCommand.return_value = (0, "")
        listPatterns = ["myScript.py", "otherTool"]
        await _fnKillMatchingProcesses(
            mockDocker, "cid", listPatterns
        )
        assert mockDocker.ftResultExecuteCommand.call_count == 2


# ── Line 150: _fnRegisterPipelineState returns dictState ─────────

class TestPipelineStateRoute:
    def test_returns_state_when_present(self):
        """Cover line 150: return dictState."""
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        app = FastAPI()
        dictCtx = {
            "docker": MagicMock(),
            "require": MagicMock(),
        }
        dictExpected = {"bRunning": True, "iStep": 2}
        with patch(
            "vaibify.gui.pipelineState.fdictReadState",
            return_value=dictExpected,
        ):
            from vaibify.gui.routes.pipelineRoutes import (
                _fnRegisterPipelineState,
            )
            _fnRegisterPipelineState(app, dictCtx)
            client = TestClient(app)
            response = client.get("/api/pipeline/cid1/state")
            assert response.status_code == 200
            assert response.json()["bRunning"] is True

    def test_returns_not_running_when_none(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        app = FastAPI()
        dictCtx = {
            "docker": MagicMock(),
            "require": MagicMock(),
        }
        with patch(
            "vaibify.gui.pipelineState.fdictReadState",
            return_value=None,
        ):
            from vaibify.gui.routes.pipelineRoutes import (
                _fnRegisterPipelineState,
            )
            _fnRegisterPipelineState(app, dictCtx)
            client = TestClient(app)
            response = client.get("/api/pipeline/cid1/state")
            assert response.json() == {
                "bRunning": False, "iSyncEpoch": 0,
            }


# ── Line 172: kill route with processes > 0 ──────────────────────

class TestPipelineKillRoute:
    def test_kill_with_matching_processes(self):
        """Cover line 172: processes > 0 triggers kill."""
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        app = FastAPI()
        mockDocker = MagicMock()
        mockDocker.ftResultExecuteCommand.return_value = (0, "3\n")
        dictWorkflow = {
            "listSteps": [{
                "saDataCommands": ["python myScript.py"],
                "saPlotCommands": [],
            }]
        }
        dictCtx = {
            "docker": mockDocker,
            "require": MagicMock(),
            "workflows": {"cid1": dictWorkflow},
            "pipelineTasks": {},
        }
        with patch(
            "vaibify.gui.routes.pipelineRoutes.fdictRequireWorkflow",
            return_value=dictWorkflow,
        ), patch(
            "vaibify.gui.routes.pipelineRoutes._fnMarkPipelineStopped",
            new=AsyncMock(),
        ):
            from vaibify.gui.routes.pipelineRoutes import (
                _fnRegisterPipelineKill,
            )
            _fnRegisterPipelineKill(app, dictCtx)
            client = TestClient(app)
            response = client.post("/api/pipeline/cid1/kill")
            assert response.status_code == 200
            dictResult = response.json()
            assert dictResult["iProcessesKilled"] == 3


# ── Lines 211-222: pipeline WebSocket auth failures ──────────────

class TestPipelineWsRoute:
    """The route honors the guard's verdict, closing AFTER accept so
    the deliberate 4xxx code reaches a real browser (close-before-accept
    downgrades every refusal to an opaque 1006)."""

    def _fiObserveRejectCode(self, iRejectCode, sToken):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from starlette.websockets import WebSocketDisconnect

        app = FastAPI()
        dictCtx = {
            "require": MagicMock(),
            "sSessionToken": "tok",
            "dictContainerOwners": {},
        }
        with patch(
            "vaibify.gui.routes.pipelineRoutes"
            ".fiContainerSessionRejectionCode",
            return_value=iRejectCode,
        ):
            from vaibify.gui.routes.pipelineRoutes import (
                _fnRegisterPipelineWs,
            )
            _fnRegisterPipelineWs(app, dictCtx)
            client = TestClient(app)
            with client.websocket_connect(
                f"/ws/pipeline/cid1?sToken={sToken}"
            ) as websocketClient:
                with pytest.raises(WebSocketDisconnect) as excInfo:
                    websocketClient.receive_text()
        return excInfo.value.code

    def test_ws_invalid_origin_closes_4003(self):
        assert self._fiObserveRejectCode(4003, "tok") == 4003

    def test_ws_bad_token_closes_4401(self):
        assert self._fiObserveRejectCode(4401, "bad") == 4401

    def test_ws_foreign_lease_closes_4403(self):
        assert self._fiObserveRejectCode(4403, "tok") == 4403


# ── Lines 236-257: acknowledge step route ────────────────────────

class TestAcknowledgeStepRoute:
    def test_acknowledge_step_success(self):
        """Cover lines 236-257."""
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        app = FastAPI()
        dictWorkflow = {
            "listSteps": [{
                "sDirectory": "step1",
                "dictRunStats": {},
            }]
        }
        mockDocker = MagicMock()
        dictCtx = {
            "docker": mockDocker,
            "require": MagicMock(),
            "workflows": {"cid1": dictWorkflow},
            "save": MagicMock(),
            "variables": MagicMock(return_value={}),
        }
        with patch(
            "vaibify.gui.routes.pipelineRoutes"
            ".fdictRequireWorkflow",
            return_value=dictWorkflow,
        ), patch(
            "vaibify.gui.routes.pipelineRoutes"
            "._fnClearStepModificationState",
        ), patch(
            "vaibify.gui.routes.pipelineRoutes"
            "._flistCollectOutputPaths",
            return_value=["/workspace/out.dat"],
        ), patch(
            "vaibify.gui.routes.pipelineRoutes"
            "._fdictGetModTimes",
            return_value={"/workspace/out.dat": 1234.0},
        ), patch(
            "vaibify.gui.routes.pipelineRoutes"
            "._fnUpdateModTimeBaseline",
        ):
            from vaibify.gui.routes.pipelineRoutes import (
                _fnRegisterAcknowledgeStep,
            )
            _fnRegisterAcknowledgeStep(app, dictCtx)
            client = TestClient(app)
            response = client.post(
                "/api/pipeline/cid1/acknowledge-step/0"
            )
            assert response.status_code == 200
            assert response.json()["bSuccess"] is True


# ── Lines 314-318, 324-329: _fdictFetchOutputStatus branches ────

class TestFdictFetchOutputStatus:
    @pytest.mark.asyncio
    async def test_stale_reset_logs_and_saves(self):
        """Cover lines 313-318: bStaleReset is True."""
        dictWorkflow = {"listSteps": []}
        dictCtx = {
            "docker": MagicMock(),
            "save": MagicMock(),
            "variables": MagicMock(return_value={}),
            "paths": {},
        }
        with patch(
            "vaibify.gui.routes.pipelineRoutes"
            "._flistCollectOutputPaths",
            return_value=[],
        ), patch(
            "vaibify.gui.routes.pipelineRoutes"
            ".ftGetModTimesAndFingerprint",
            return_value=({}, ""),
        ), patch(
            "vaibify.gui.routes.pipelineRoutes"
            ".fdictCollectOutputPathsByStep",
            return_value={},
        ), patch(
            "vaibify.gui.routes.pipelineRoutes"
            ".fnCollectMarkerPathsByStep",
            return_value={},
        ), patch(
            "vaibify.gui.routes.pipelineRoutes"
            "._fbCheckStaleUserVerification",
            return_value=True,
        ), patch(
            "vaibify.gui.routes.pipelineRoutes"
            "._flistDetectAndInvalidate",
            return_value={},
        ), patch(
            "vaibify.gui.routes.pipelineRoutes"
            "._fdictComputeMaxMtimeByStep",
            return_value={},
        ), patch(
            "vaibify.gui.routes.pipelineRoutes"
            "._fdictComputeMaxPlotMtimeByStep",
            return_value={},
        ), patch(
            "vaibify.gui.routes.pipelineRoutes"
            "._fdictComputeMaxDataMtimeByStep",
            return_value={},
        ), patch(
            "vaibify.gui.routes.pipelineRoutes"
            "._fdictComputeMarkerMtimeByStep",
            return_value={},
        ), patch(
            "vaibify.gui.routes.pipelineRoutes"
            "._fdictBuildScriptStatus",
            return_value={},
        ):
            dictResult = await _fdictFetchOutputStatus(
                dictCtx, "cid1", dictWorkflow, {}
            )
            dictCtx["save"].assert_called_once()
            assert "dictModTimes" in dictResult

    @pytest.mark.asyncio
    async def test_invalidated_steps_logs(self):
        """Cover lines 323-329: listInvalidated is non-empty."""
        dictWorkflow = {"listSteps": []}
        dictCtx = {
            "docker": MagicMock(),
            "save": MagicMock(),
            "paths": {},
        }
        dictInvalidated = {
            "0": {
                "sUser": "untested",
                "listModifiedFiles": ["/workspace/f.dat"],
            }
        }
        with patch(
            "vaibify.gui.routes.pipelineRoutes"
            "._flistCollectOutputPaths",
            return_value=[],
        ), patch(
            "vaibify.gui.routes.pipelineRoutes"
            ".ftGetModTimesAndFingerprint",
            return_value=({}, ""),
        ), patch(
            "vaibify.gui.routes.pipelineRoutes"
            ".fdictCollectOutputPathsByStep",
            return_value={},
        ), patch(
            "vaibify.gui.routes.pipelineRoutes"
            ".fnCollectMarkerPathsByStep",
            return_value={},
        ), patch(
            "vaibify.gui.routes.pipelineRoutes"
            "._fbCheckStaleUserVerification",
            return_value=False,
        ), patch(
            "vaibify.gui.routes.pipelineRoutes"
            "._flistDetectAndInvalidate",
            return_value=dictInvalidated,
        ), patch(
            "vaibify.gui.routes.pipelineRoutes"
            "._fdictComputeMaxMtimeByStep",
            return_value={},
        ), patch(
            "vaibify.gui.routes.pipelineRoutes"
            "._fdictComputeMaxPlotMtimeByStep",
            return_value={},
        ), patch(
            "vaibify.gui.routes.pipelineRoutes"
            "._fdictComputeMaxDataMtimeByStep",
            return_value={},
        ), patch(
            "vaibify.gui.routes.pipelineRoutes"
            "._fdictComputeMarkerMtimeByStep",
            return_value={},
        ), patch(
            "vaibify.gui.routes.pipelineRoutes"
            "._fdictBuildScriptStatus",
            return_value={},
        ):
            dictResult = await _fdictFetchOutputStatus(
                dictCtx, "cid1", dictWorkflow, {}
            )
            assert dictResult["dictInvalidatedSteps"] == (
                dictInvalidated
            )
            assert "dictMaxDataMtimeByStep" in dictResult
            assert "dictMarkerMtimeByStep" in dictResult
            assert "dictTestSourceMtimeByStep" in dictResult
            assert "dictTestCategoryMtimes" in dictResult

    @pytest.mark.asyncio
    async def test_workflow_path_included_in_stat_batch(self):
        """workflow.json's path travels through _fdictGetModTimes."""
        dictWorkflow = {"listSteps": []}
        dictCtx = {
            "docker": MagicMock(),
            "save": MagicMock(),
            "paths": {
                "cid1":
                    "/workspace/proj/.vaibify/workflows/demo.json",
            },
        }
        with patch(
            "vaibify.gui.routes.pipelineRoutes"
            "._flistCollectOutputPaths",
            return_value=[],
        ), patch(
            "vaibify.gui.routes.pipelineRoutes"
            ".ftGetModTimesAndFingerprint",
            return_value=({}, ""),
        ) as mockModTimes, patch(
            "vaibify.gui.routes.pipelineRoutes"
            ".fdictCollectOutputPathsByStep",
            return_value={},
        ), patch(
            "vaibify.gui.routes.pipelineRoutes"
            ".fnCollectMarkerPathsByStep",
            return_value={},
        ), patch(
            "vaibify.gui.routes.pipelineRoutes"
            "._fbCheckStaleUserVerification",
            return_value=False,
        ), patch(
            "vaibify.gui.routes.pipelineRoutes"
            "._flistDetectAndInvalidate",
            return_value={},
        ), patch(
            "vaibify.gui.routes.pipelineRoutes"
            "._fdictMaybeReloadWorkflow",
            return_value={
                "bReplaced": False,
                "dictWorkflow": None,
                "sError": None,
            },
        ), patch(
            "vaibify.gui.routes.pipelineRoutes"
            "._fdictComputeMaxMtimeByStep",
            return_value={},
        ), patch(
            "vaibify.gui.routes.pipelineRoutes"
            "._fdictComputeMaxPlotMtimeByStep",
            return_value={},
        ), patch(
            "vaibify.gui.routes.pipelineRoutes"
            "._fdictComputeMaxDataMtimeByStep",
            return_value={},
        ), patch(
            "vaibify.gui.routes.pipelineRoutes"
            "._fdictComputeMarkerMtimeByStep",
            return_value={},
        ), patch(
            "vaibify.gui.routes.pipelineRoutes"
            "._fdictBuildScriptStatus",
            return_value={},
        ):
            await _fdictFetchOutputStatus(
                dictCtx, "cid1", dictWorkflow, {}
            )
            listBatchedPaths = mockModTimes.call_args[0][2]
            assert (
                "/workspace/proj/.vaibify/workflows/demo.json"
                in listBatchedPaths
            )

    @pytest.mark.asyncio
    async def test_response_carries_reload_flag_and_dict(self):
        """When the helper reports bReplaced, response includes the
        new workflow dict and the bWorkflowReloaded flag."""
        dictWorkflow = {
            "listSteps": [],
            "sPath":
                "/workspace/proj/.vaibify/workflows/demo.json",
        }
        dictNewWorkflow = {
            "listSteps": [{"sDirectory": "stepA", "sName": "A"}],
            "sPath":
                "/workspace/proj/.vaibify/workflows/demo.json",
        }
        dictCtx = {
            "docker": MagicMock(),
            "save": MagicMock(),
            "paths": {},
        }
        with patch(
            "vaibify.gui.routes.pipelineRoutes"
            "._flistCollectOutputPaths",
            return_value=[],
        ), patch(
            "vaibify.gui.routes.pipelineRoutes"
            ".ftGetModTimesAndFingerprint",
            return_value=({}, ""),
        ), patch(
            "vaibify.gui.routes.pipelineRoutes"
            ".fdictCollectOutputPathsByStep",
            return_value={},
        ), patch(
            "vaibify.gui.routes.pipelineRoutes"
            ".fnCollectMarkerPathsByStep",
            return_value={},
        ), patch(
            "vaibify.gui.routes.pipelineRoutes"
            "._fbCheckStaleUserVerification",
            return_value=False,
        ), patch(
            "vaibify.gui.routes.pipelineRoutes"
            "._flistDetectAndInvalidate",
            return_value={},
        ), patch(
            "vaibify.gui.routes.pipelineRoutes"
            "._fdictMaybeReloadWorkflow",
            return_value={
                "bReplaced": True,
                "dictWorkflow": dictNewWorkflow,
                "sError": None,
            },
        ), patch(
            "vaibify.gui.routes.pipelineRoutes"
            "._fdictComputeMaxMtimeByStep",
            return_value={},
        ), patch(
            "vaibify.gui.routes.pipelineRoutes"
            "._fdictComputeMaxPlotMtimeByStep",
            return_value={},
        ), patch(
            "vaibify.gui.routes.pipelineRoutes"
            "._fdictComputeMaxDataMtimeByStep",
            return_value={},
        ), patch(
            "vaibify.gui.routes.pipelineRoutes"
            "._fdictComputeMarkerMtimeByStep",
            return_value={},
        ), patch(
            "vaibify.gui.routes.pipelineRoutes"
            "._fdictBuildScriptStatus",
            return_value={},
        ):
            dictResult = await _fdictFetchOutputStatus(
                dictCtx, "cid1", dictWorkflow, {}
            )
            assert dictResult["bWorkflowReloaded"] is True
            assert dictResult["dictWorkflow"] is not None
            assert (
                dictResult["dictWorkflow"]["listSteps"][0]
                ["sName"] == "A"
            )
            assert dictResult["sWorkflowReloadError"] is None

    @pytest.mark.asyncio
    async def test_response_carries_reload_error(self):
        """A reload failure surfaces sWorkflowReloadError without
        replacing the workflow."""
        dictWorkflow = {
            "listSteps": [],
            "sPath":
                "/workspace/proj/.vaibify/workflows/demo.json",
        }
        dictCtx = {
            "docker": MagicMock(),
            "save": MagicMock(),
            "paths": {},
        }
        with patch(
            "vaibify.gui.routes.pipelineRoutes"
            "._flistCollectOutputPaths",
            return_value=[],
        ), patch(
            "vaibify.gui.routes.pipelineRoutes"
            ".ftGetModTimesAndFingerprint",
            return_value=({}, ""),
        ), patch(
            "vaibify.gui.routes.pipelineRoutes"
            ".fdictCollectOutputPathsByStep",
            return_value={},
        ), patch(
            "vaibify.gui.routes.pipelineRoutes"
            ".fnCollectMarkerPathsByStep",
            return_value={},
        ), patch(
            "vaibify.gui.routes.pipelineRoutes"
            "._fbCheckStaleUserVerification",
            return_value=False,
        ), patch(
            "vaibify.gui.routes.pipelineRoutes"
            "._flistDetectAndInvalidate",
            return_value={},
        ), patch(
            "vaibify.gui.routes.pipelineRoutes"
            "._fdictMaybeReloadWorkflow",
            return_value={
                "bReplaced": False,
                "dictWorkflow": None,
                "sError": "workflow.json missing from container",
            },
        ), patch(
            "vaibify.gui.routes.pipelineRoutes"
            "._fdictComputeMaxMtimeByStep",
            return_value={},
        ), patch(
            "vaibify.gui.routes.pipelineRoutes"
            "._fdictComputeMaxPlotMtimeByStep",
            return_value={},
        ), patch(
            "vaibify.gui.routes.pipelineRoutes"
            "._fdictComputeMaxDataMtimeByStep",
            return_value={},
        ), patch(
            "vaibify.gui.routes.pipelineRoutes"
            "._fdictComputeMarkerMtimeByStep",
            return_value={},
        ), patch(
            "vaibify.gui.routes.pipelineRoutes"
            "._fdictBuildScriptStatus",
            return_value={},
        ):
            dictResult = await _fdictFetchOutputStatus(
                dictCtx, "cid1", dictWorkflow, {}
            )
            assert dictResult["bWorkflowReloaded"] is False
            assert dictResult["dictWorkflow"] is None
            assert (
                dictResult["sWorkflowReloadError"]
                == "workflow.json missing from container"
            )

    @pytest.mark.asyncio
    async def test_marker_paths_batched_into_mod_times_call(self):
        """Marker file paths are included in the batched stat call."""
        dictWorkflow = {"listSteps": []}
        dictCtx = {
            "docker": MagicMock(),
            "save": MagicMock(),
            "paths": {},
        }
        sMarkerPath = (
            "/workspace/.vaibify/test_markers/"
            "workspace_step01.json"
        )
        with patch(
            "vaibify.gui.routes.pipelineRoutes"
            "._flistCollectOutputPaths",
            return_value=["step01/out.dat"],
        ), patch(
            "vaibify.gui.routes.pipelineRoutes"
            ".flistExtractAllScriptPaths",
            return_value=["step01/run.py"],
        ), patch(
            "vaibify.gui.routes.pipelineRoutes"
            ".fnCollectMarkerPathsByStep",
            return_value={0: sMarkerPath},
        ), patch(
            "vaibify.gui.routes.pipelineRoutes"
            ".ftGetModTimesAndFingerprint",
            return_value=({}, ""),
        ) as mockModTimes, patch(
            "vaibify.gui.routes.pipelineRoutes"
            ".fdictCollectOutputPathsByStep",
            return_value={},
        ), patch(
            "vaibify.gui.routes.pipelineRoutes"
            "._fbCheckStaleUserVerification",
            return_value=False,
        ), patch(
            "vaibify.gui.routes.pipelineRoutes"
            "._flistDetectAndInvalidate",
            return_value={},
        ), patch(
            "vaibify.gui.routes.pipelineRoutes"
            "._fdictComputeMaxMtimeByStep",
            return_value={},
        ), patch(
            "vaibify.gui.routes.pipelineRoutes"
            "._fdictComputeMaxPlotMtimeByStep",
            return_value={},
        ), patch(
            "vaibify.gui.routes.pipelineRoutes"
            "._fdictComputeMaxDataMtimeByStep",
            return_value={},
        ), patch(
            "vaibify.gui.routes.pipelineRoutes"
            "._fdictComputeMarkerMtimeByStep",
            return_value={},
        ), patch(
            "vaibify.gui.routes.pipelineRoutes"
            "._fdictBuildScriptStatus",
            return_value={},
        ):
            await _fdictFetchOutputStatus(
                dictCtx, "cid1", dictWorkflow, {},
            )
            listBatchedPaths = mockModTimes.call_args[0][2]
            assert sMarkerPath in listBatchedPaths


# ── Line 400: _fdictFetchTestMarkers non-zero exit ──────────────

class TestFdictFetchTestMarkers:
    def test_nonzero_exit_returns_empty(self):
        """Cover line 400: iExit != 0."""
        mockDocker = MagicMock()
        mockDocker.ftResultExecuteCommand.return_value = (
            1, "error"
        )
        with patch(
            "vaibify.gui.syncDispatcher"
            ".fsBuildTestMarkerCheckCommand",
            return_value="echo test",
        ):
            dictResult = _fdictFetchTestMarkers(
                mockDocker, "cid1", ["/workspace/step1"],
                "/workspace/DemoRepo", "demo",
            )
            assert dictResult == {
                "markers": {},
                "testFiles": {},
                "missingConftest": [],
            }


# ── _fnRefreshConftestsAndMigrateMarkers wiring ──────────────────

class TestFnRefreshConftestsAndMigrateMarkers:
    @pytest.mark.asyncio
    async def test_calls_both_helpers_with_correct_args(self):
        """Refresh + migration both fire with the expected arguments."""
        mockDocker = MagicMock()
        with patch(
            "vaibify.gui.conftestManager.fnEnsureConftestsCurrent",
        ) as mockRefresh, patch(
            "vaibify.gui.conftestManager.fnMigrateFlatMarkers",
        ) as mockMigrate:
            await _fnRefreshConftestsAndMigrateMarkers(
                mockDocker, "cid1",
                ["step1", "step2"],
                "/workspace/DemoRepo", "demo",
            )
            mockRefresh.assert_called_once_with(
                mockDocker, "cid1",
                ["step1", "step2"], "/workspace/DemoRepo",
            )
            mockMigrate.assert_called_once_with(
                mockDocker, "cid1",
                "/workspace/DemoRepo", "demo",
            )

    @pytest.mark.asyncio
    async def test_empty_step_list_short_circuits(self):
        """No step dirs → neither helper runs (connect stays cheap)."""
        mockDocker = MagicMock()
        with patch(
            "vaibify.gui.conftestManager.fnEnsureConftestsCurrent",
        ) as mockRefresh, patch(
            "vaibify.gui.conftestManager.fnMigrateFlatMarkers",
        ) as mockMigrate:
            await _fnRefreshConftestsAndMigrateMarkers(
                mockDocker, "cid1", [],
                "/workspace/DemoRepo", "demo",
            )
            mockRefresh.assert_not_called()
            mockMigrate.assert_not_called()


# ── _fnDeleteLegacyMarkers: stale-marker cleanup after backfill ──


class TestFnDeleteLegacyMarkers:
    def test_builds_marker_paths_under_project_repo(self):
        """The deletion script must target markers in the new path."""
        mockDocker = MagicMock()
        mockDocker.ftResultExecuteCommand.return_value = (0, "")
        _fnDeleteLegacyMarkers(
            mockDocker, "cid",
            ["BayesianPosteriors", "XuvEvolution"],
            "/workspace/proj",
        )
        sCmd = mockDocker.ftResultExecuteCommand.call_args[0][1]
        assert (
            "/workspace/proj/.vaibify/test_markers/"
            "BayesianPosteriors.json" in sCmd
        )
        assert (
            "/workspace/proj/.vaibify/test_markers/"
            "XuvEvolution.json" in sCmd
        )

    def test_no_op_for_empty_list(self):
        mockDocker = MagicMock()
        _fnDeleteLegacyMarkers(
            mockDocker, "cid", [], "/workspace/proj",
        )
        mockDocker.ftResultExecuteCommand.assert_not_called()

    def test_no_op_for_empty_repo_path(self):
        """Empty sProjectRepoPath skips deletion entirely."""
        mockDocker = MagicMock()
        _fnDeleteLegacyMarkers(
            mockDocker, "cid", ["step1"], "",
        )
        mockDocker.ftResultExecuteCommand.assert_not_called()

    def test_logs_deleted_paths(self, caplog):
        """The log line names the markers that were removed."""
        import logging
        mockDocker = MagicMock()
        mockDocker.ftResultExecuteCommand.return_value = (
            0,
            "/workspace/proj/.vaibify/test_markers/Step1.json\n",
        )
        with caplog.at_level(logging.INFO, logger="vaibify"):
            _fnDeleteLegacyMarkers(
                mockDocker, "cid", ["Step1"], "/workspace/proj",
            )
        assert any(
            "Deleted 1 legacy markers" in record.message
            for record in caplog.records
        )

    def test_legacy_marker_removed_modern_kept(self, tmp_path):
        """End-to-end: run the deletion script under a real python.

        Builds two markers in tmp_path, one legacy (no sRunAtUtc),
        one modern (with sRunAtUtc), then runs the inline deletion
        script directly. The legacy file disappears; the modern one
        survives.
        """
        import json
        import subprocess
        from vaibify.gui.routes.pipelineRoutes import (
            _fnDeleteLegacyMarkers,
        )
        sMarkerDir = tmp_path / ".vaibify" / "test_markers"
        sMarkerDir.mkdir(parents=True)
        sLegacy = sMarkerDir / "Legacy.json"
        sLegacy.write_text(json.dumps({"iCollected": 5}))
        sModern = sMarkerDir / "Modern.json"
        sModern.write_text(json.dumps({
            "sRunAtUtc": "2026-04-23T20:22:40Z",
            "iCollected": 5,
        }))
        # Capture the command the helper would issue and run it locally.
        captured = {}

        class _FakeDocker:
            def ftResultExecuteCommand(self, sContainerId, sCommand):
                captured["cmd"] = sCommand
                return (0, "")

        _fnDeleteLegacyMarkers(
            _FakeDocker(), "cid", ["Legacy", "Modern"], str(tmp_path),
        )
        # Execute the captured command in a real shell to validate
        # the deletion logic end-to-end.
        subprocess.check_call(
            ["bash", "-c", captured["cmd"]],
        )
        assert not sLegacy.exists(), (
            "legacy marker should have been deleted"
        )
        assert sModern.exists(), (
            "modern marker (has sRunAtUtc) must be preserved"
        )


# ── Lines 444-455: _fnEnsureConftestTemplate ─────────────────────

class TestFnEnsureConftestTemplate:
    def test_template_already_exists(self):
        """Cover lines 449-450: file already exists."""
        mockDocker = MagicMock()
        mockDocker.ftResultExecuteCommand.return_value = (0, "")
        _fnEnsureConftestTemplate(
            mockDocker, "cid1", "# content"
        )
        mockDocker.fnWriteFile.assert_not_called()

    def test_template_does_not_exist_writes(self):
        """Cover lines 444-458: file missing, write it."""
        mockDocker = MagicMock()
        mockDocker.ftResultExecuteCommand.side_effect = [
            (1, ""),
            (0, ""),
        ]
        _fnEnsureConftestTemplate(
            mockDocker, "cid1", "# content"
        )
        mockDocker.fnWriteFile.assert_called_once()
        sPath = mockDocker.fnWriteFile.call_args[0][1]
        assert "conftest_marker.py" in sPath


# ── Line 605: _fdictBuildTestFileChanges listCustom branch ───────

class TestFdictBuildTestFileChanges:
    def test_custom_files_included(self):
        """Cover line 605: listCustom non-empty."""
        dictWorkflow = {
            "listSteps": [{
                "sDirectory": "step1",
                "dictTests": {
                    "integrity": {
                        "saCommands": [
                            "pytest test_integrity.py"
                        ],
                    },
                },
            }]
        }
        dictTestInfo = {
            "testFiles": {
                "step1": {
                    "listFiles": ["test_integrity.py"],
                    "dictHashes": {
                        "test_integrity.py": "different_hash",
                    },
                    "dictMtimes": {},
                },
            },
        }
        with patch(
            "vaibify.gui.testGenerator"
            ".fsQuantitativeTemplateHash",
            return_value="tmpl_quant",
        ), patch(
            "vaibify.gui.testGenerator"
            ".fsIntegrityTemplateHash",
            return_value="tmpl_integrity",
        ), patch(
            "vaibify.gui.testGenerator"
            ".fsQualitativeTemplateHash",
            return_value="tmpl_qual",
        ):
            dictResult = _fdictBuildTestFileChanges(
                dictWorkflow, dictTestInfo
            )
            assert "0" in dictResult
            assert "test_integrity.py" in (
                dictResult["0"]["listCustom"]
            )


# ── fdictComputeFileStatus wrapper ───────────────────────────────

class TestFdictComputeFileStatus:
    @pytest.mark.asyncio
    async def test_merges_output_and_test_status(self):
        dictOutputStatus = {
            "dictModTimes": {"/workspace/a.dat": 10},
            "dictMaxMtimeByStep": {"0": 10},
            "dictMaxPlotMtimeByStep": {},
            "dictMaxDataMtimeByStep": {"0": 10},
            "dictMarkerMtimeByStep": {},
            "dictTestSourceMtimeByStep": {"0": "5"},
            "dictTestCategoryMtimes": {
                "0": {"integrity": "5"},
            },
            "dictInvalidatedSteps": {},
            "dictScriptStatus": {},
        }
        dictTestStatus = {
            "dictTestMarkers": {"0": {"sUnitTest": "passed"}},
            "dictTestFileChanges": {},
        }
        dictCtx = {"docker": MagicMock()}
        with patch(
            "vaibify.gui.routes.pipelineRoutes"
            "._fdictFetchOutputStatus",
            new=AsyncMock(return_value=dictOutputStatus),
        ), patch(
            "vaibify.gui.routes.pipelineRoutes"
            "._fdictFetchTestStatus",
            new=AsyncMock(return_value=dictTestStatus),
        ):
            dictResult = await fdictComputeFileStatus(
                dictCtx, "cid1", {"listSteps": []}, {},
            )
        for sKey in (
            "dictModTimes",
            "dictMaxMtimeByStep",
            "dictMaxPlotMtimeByStep",
            "dictMaxDataMtimeByStep",
            "dictMarkerMtimeByStep",
            "dictTestSourceMtimeByStep",
            "dictTestCategoryMtimes",
            "dictInvalidatedSteps",
            "dictScriptStatus",
            "dictTestMarkers",
            "dictTestFileChanges",
        ):
            assert sKey in dictResult, sKey
        assert dictResult["dictTestMarkers"] == (
            {"0": {"sUnitTest": "passed"}}
        )


# ── _fdictFetchTestStatus: aggregate self-heal on the poll path ──


from vaibify.gui.routes.pipelineRoutes import _fdictFetchTestStatus


def _fdictStuckAggregateStep(sIntegrityState):
    """Build a step whose aggregate is stuck at ``untested``."""
    return {
        "sDirectory": "A",
        "dictTests": {
            "dictIntegrity": {"saCommands": ["python -m pytest"]},
        },
        "dictVerification": {
            "sUnitTest": "untested",
            "sIntegrity": sIntegrityState,
        },
    }


class TestFdictFetchTestStatusAggregateSelfHeal:
    async def _fdictRunFetchTestStatus(
        self, dictWorkflow, dictTestMarkers, mockSave,
    ):
        dictCtx = {
            "docker": MagicMock(),
            "paths": {"cid1": "/repo/workflow.json"},
            "save": mockSave,
        }
        with patch(
            "vaibify.gui.routes.pipelineRoutes"
            "._fnRefreshConftestsAndMigrateMarkers",
            new=AsyncMock(),
        ), patch(
            "vaibify.gui.routes.pipelineRoutes"
            "._fdictFetchTestMarkers",
            return_value={"markers": {}, "testFiles": {}},
        ), patch(
            "vaibify.gui.routes.pipelineRoutes"
            "._fdictBuildTestMarkerStatus",
            return_value=dictTestMarkers,
        ):
            return await _fdictFetchTestStatus(
                dictCtx, "cid1", dictWorkflow,
            )

    @pytest.mark.asyncio
    async def test_green_marker_categories_heal_stuck_aggregate(self):
        """Applying a green marker category recomputes the stuck
        ``untested`` aggregate and persists the workflow."""
        dictStep = _fdictStuckAggregateStep("untested")
        dictWorkflow = {"listSteps": [dictStep]}
        dictTestMarkers = {
            "0": {
                "bStale": False,
                "dictMarker": {
                    "dictCategories": {
                        "integrity": {"iPassed": 3, "iFailed": 0},
                    },
                },
            },
        }
        mockSave = MagicMock()
        await self._fdictRunFetchTestStatus(
            dictWorkflow, dictTestMarkers, mockSave,
        )
        assert dictStep["dictVerification"]["sIntegrity"] == "passed"
        assert dictStep["dictVerification"]["sUnitTest"] == "passed"
        mockSave.assert_called_once_with("cid1", dictWorkflow)

    @pytest.mark.asyncio
    async def test_stuck_aggregate_heals_even_without_marker_change(self):
        """The live bug: marker-green axes under a persisted
        ``untested`` aggregate self-correct on poll even when the
        marker application itself changes nothing."""
        dictStep = _fdictStuckAggregateStep("passed-from-marker")
        dictWorkflow = {"listSteps": [dictStep]}
        mockSave = MagicMock()
        await self._fdictRunFetchTestStatus(dictWorkflow, {}, mockSave)
        assert dictStep["dictVerification"]["sUnitTest"] == (
            "passed-from-marker"
        )
        mockSave.assert_called_once_with("cid1", dictWorkflow)


# ── _fbApplyRandomnessLint ────────────────────────────────────────


from vaibify.gui.routes.pipelineRoutes import (
    _fbApplyRandomnessLint,
    _ffParseMtime,
)
from vaibify.gui.pipelineState import (
    fdictReadReconciledState,
    fsBuildHeartbeatStaleReason,
)


class TestFbApplyRandomnessLint:
    def test_returns_false_when_no_lint_block(self):
        """Workflows without dictRandomnessLint short-circuit (line 358)."""
        dictCtx = {"docker": MagicMock()}
        dictWorkflow = {"listSteps": [{"sName": "A"}]}
        bChanged = _fbApplyRandomnessLint(dictCtx, "cid", dictWorkflow)
        assert bChanged is False

    def test_returns_true_when_flag_changes(self):
        """Snapshot-vs-after diff flips True when lint adds the flag."""
        dictCtx = {"docker": MagicMock()}
        dictCtx["docker"].ftResultExecuteCommand.return_value = (
            0, "name foo\n",
        )
        dictWorkflow = {
            "sProjectRepoPath": "/repo",
            "dictRandomnessLint": {
                "sConfigGlob": "*.in",
                "sSeedRegex": r"^seed\s+\d+",
            },
            "listSteps": [{
                "sName": "A", "sDirectory": "sweep",
                "saSetupCommands": ["vspace vspace.in"],
                "saDataCommands": [], "saCommands": [],
            }],
        }
        with patch(
            "vaibify.gui.routes.pipelineRoutes.fsReadFileFromContainer",
            return_value="name foo\n",
        ):
            bChanged = _fbApplyRandomnessLint(
                dictCtx, "cid", dictWorkflow,
            )
        assert bChanged is True
        assert dictWorkflow["listSteps"][0]["dictVerification"][
            "bUnseededRandomnessWarning"] is True

    def test_returns_false_when_state_unchanged(self):
        """No flips means listAfter == listSnapshot (line 379)."""
        dictCtx = {"docker": MagicMock()}
        dictWorkflow = {
            "sProjectRepoPath": "/repo",
            "dictRandomnessLint": {
                "sConfigGlob": "*.in",
                "sSeedRegex": r"^seed\s+\d+",
            },
            "listSteps": [{
                "sName": "A", "sDirectory": "sweep",
                "saSetupCommands": ["vspace vspace.in"],
                "saDataCommands": [], "saCommands": [],
            }],
        }
        with patch(
            "vaibify.gui.routes.pipelineRoutes.fsReadFileFromContainer",
            return_value="seed 42\nname foo\n",
        ):
            bChanged = _fbApplyRandomnessLint(
                dictCtx, "cid", dictWorkflow,
            )
        assert bChanged is False


class TestFbApplyRandomnessLintAsync:
    """audit HIGH #14: lint must run off the event loop."""

    @pytest.mark.asyncio
    async def test_wrapper_dispatches_to_to_thread(self):
        from vaibify.gui.routes.pipelineRoutes import (
            _fbApplyRandomnessLintAsync,
        )
        dictCtx = {"docker": MagicMock()}
        dictWorkflow = {"listSteps": []}
        with patch(
            "vaibify.gui.routes.pipelineRoutes.asyncio.to_thread",
            new_callable=AsyncMock,
        ) as mockToThread:
            mockToThread.return_value = True
            bChanged = await _fbApplyRandomnessLintAsync(
                dictCtx, "cid", dictWorkflow,
            )
        assert bChanged is True
        mockToThread.assert_awaited_once()
        assert mockToThread.call_args[0][0] is _fbApplyRandomnessLint

    @pytest.mark.asyncio
    async def test_wrapper_returns_underlying_value(self):
        from vaibify.gui.routes.pipelineRoutes import (
            _fbApplyRandomnessLintAsync,
        )
        dictCtx = {"docker": MagicMock()}
        dictWorkflow = {"listSteps": []}
        bChanged = await _fbApplyRandomnessLintAsync(
            dictCtx, "cid", dictWorkflow,
        )
        assert bChanged is False


class TestPollSideEffectsDoesNotRunLint:
    """Lint must be invoked exclusively via the async wrapper."""

    def test_side_effects_no_longer_calls_lint(self):
        import inspect
        from vaibify.gui.routes import pipelineRoutes
        sSource = inspect.getsource(
            pipelineRoutes._flistRunPollSideEffects
        )
        assert "_fbApplyRandomnessLint(" not in sSource, (
            "audit HIGH #14: the sync side-effects helper must not "
            "invoke the docker-exec-blocking lint on the event loop."
        )


class TestFdictReadReconciledStateShortCircuit:
    def test_not_running_returns_state_unchanged(self):
        """A non-running state is returned through the reader untouched.

        The reconciler is the canonical liveness arbiter (after the
        pipeline-routes consolidation moved the body into
        ``pipelineState``). A state file already showing
        ``bRunning: False`` must not be rewritten — that would burn an
        atomic-write cycle on every poll for completed pipelines.
        """
        import asyncio
        import json
        connection = MagicMock()
        connection.ftResultExecuteCommand.return_value = (
            0, json.dumps({"bRunning": False}),
        )
        dictResult = asyncio.run(
            fdictReadReconciledState({"docker": connection}, "cid"),
        )
        assert dictResult == {"bRunning": False}
        connection.fnWriteFile.assert_not_called()


class TestFsBuildHeartbeatStaleReason:
    def test_unparseable_timestamp_returns_safe_string(self):
        """Bad isoformat falls back to a generic reason."""
        dictState = {"sLastHeartbeat": "not-a-timestamp"}
        sReason = fsBuildHeartbeatStaleReason(dictState)
        assert "unparseable" in sReason


class TestFfParseMtime:
    def test_returns_zero_for_unparseable_mtime(self):
        """Lines 706-707: float() failure returns 0.0."""
        assert _ffParseMtime("not-a-float") == 0.0

    def test_returns_zero_for_none(self):
        assert _ffParseMtime(None) == 0.0

    def test_parses_valid_string(self):
        assert _ffParseMtime("123.5") == 123.5


class TestWorkflowDiscoveryRoute:
    """GET /api/pipeline/{cid}/workflow-discovery — works in both modes
    and surfaces newly-appeared workflows for the toolkit-mode banner.
    """

    def _fdictBuildCtx(self):
        return {
            "docker": MagicMock(),
            "require": MagicMock(),
            "lastDiscoveredWorkflows": {},
        }

    def _fnRegister(self, app, dictCtx):
        from vaibify.gui.routes.pipelineRoutes import (
            _fnRegisterWorkflowDiscovery,
        )
        _fnRegisterWorkflowDiscovery(app, dictCtx)

    def test_first_poll_seeds_cache_silently(self):
        """First poll for a container: list returned, no change flag."""
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        app = FastAPI()
        dictCtx = self._fdictBuildCtx()
        listFound = [{
            "sPath": "/workspace/proj/.vaibify/workflows/demo.json",
            "sName": "demo",
            "sRepoName": "proj",
            "sProjectRepoPath": "/workspace/proj",
        }]
        with patch(
            "vaibify.gui.workflowReloadDetector.workflowManager"
            ".flistFindWorkflowsInContainer",
            return_value=listFound,
        ):
            self._fnRegister(app, dictCtx)
            client = TestClient(app)
            response = client.get(
                "/api/pipeline/cid1/workflow-discovery")
        assert response.status_code == 200
        dictBody = response.json()
        assert len(dictBody["listAvailableWorkflows"]) == 1
        assert dictBody["bWorkflowsChanged"] is False
        assert dictBody["listNewWorkflowPaths"] == []

    def test_second_poll_flags_newly_appeared_workflow(self):
        """A workflow that appears between polls is reported."""
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        app = FastAPI()
        dictCtx = self._fdictBuildCtx()
        sDemo = "/workspace/proj/.vaibify/workflows/demo.json"
        sOther = "/workspace/proj/.vaibify/workflows/other.json"
        listFirst = [{
            "sPath": sDemo, "sName": "demo",
            "sRepoName": "proj",
            "sProjectRepoPath": "/workspace/proj",
        }]
        listSecond = listFirst + [{
            "sPath": sOther, "sName": "other",
            "sRepoName": "proj",
            "sProjectRepoPath": "/workspace/proj",
        }]
        with patch(
            "vaibify.gui.workflowReloadDetector.workflowManager"
            ".flistFindWorkflowsInContainer",
            side_effect=[listFirst, listSecond],
        ):
            self._fnRegister(app, dictCtx)
            client = TestClient(app)
            client.get("/api/pipeline/cid1/workflow-discovery")
            response = client.get(
                "/api/pipeline/cid1/workflow-discovery")
        dictBody = response.json()
        assert dictBody["bWorkflowsChanged"] is True
        assert dictBody["listNewWorkflowPaths"] == [sOther]
        assert len(dictBody["listAvailableWorkflows"]) == 2

    def test_quiet_after_workflow_already_seen(self):
        """Two identical polls in succession: second is quiet."""
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        app = FastAPI()
        dictCtx = self._fdictBuildCtx()
        listFound = [{
            "sPath": "/workspace/proj/.vaibify/workflows/demo.json",
            "sName": "demo",
            "sRepoName": "proj",
            "sProjectRepoPath": "/workspace/proj",
        }]
        with patch(
            "vaibify.gui.workflowReloadDetector.workflowManager"
            ".flistFindWorkflowsInContainer",
            return_value=listFound,
        ):
            self._fnRegister(app, dictCtx)
            client = TestClient(app)
            client.get("/api/pipeline/cid1/workflow-discovery")
            response = client.get(
                "/api/pipeline/cid1/workflow-discovery")
        dictBody = response.json()
        assert dictBody["bWorkflowsChanged"] is False
        assert dictBody["listNewWorkflowPaths"] == []


_LIST_EMPTY_DICT_POLL_PATCH_NAMES = [
    "fdictCollectOutputPathsByStep",
    "fnCollectMarkerPathsByStep",
    "_flistDetectAndInvalidate",
    "_fdictLoadMarkersForPoll",
    "_fdictLoadMtimeCacheForPoll",
    "_fdictComputeMaxMtimeByStep",
    "_fdictComputeMaxPlotMtimeByStep",
    "_fdictComputeMaxDataMtimeByStep",
    "_fdictComputeMarkerMtimeByStep",
    "_fdictBuildScriptStatus",
]


def _fdictPollLevelPatchReturns(listLevel1, listLevel2, listLevel3):
    """Map patch targets to return values for poll level-state tests."""
    sModule = "vaibify.gui.routes.pipelineRoutes."
    sGates = "vaibify.reproducibility.levelGates."
    dictReturns = {
        sModule + sName: {}
        for sName in _LIST_EMPTY_DICT_POLL_PATCH_NAMES
    }
    dictReturns[sModule + "_flistCollectOutputPaths"] = []
    dictReturns[sModule + "ftGetModTimesAndFingerprint"] = ({}, "")
    dictReturns[sModule + "_fbCheckStaleUserVerification"] = False
    dictReturns[sGates + "fiAICSLevel"] = 1
    dictReturns[sGates + "flistLevel1Blockers"] = listLevel1
    dictReturns[sGates + "flistLevel2Blockers"] = listLevel2
    dictReturns[sGates + "flistLevel3Blockers"] = listLevel3
    return dictReturns


def _fstackEnterPollLevelPatches(listLevel1, listLevel2, listLevel3):
    """Return an entered ExitStack holding the poll mock battery."""
    stackPatches = contextlib.ExitStack()
    dictReturns = _fdictPollLevelPatchReturns(
        listLevel1, listLevel2, listLevel3,
    )
    for sTarget, returnValue in dictReturns.items():
        stackPatches.enter_context(
            patch(sTarget, return_value=returnValue),
        )
    return stackPatches


def _fdictBuildLevelWorkflow(listSteps):
    """Return a minimal merged workflow dict with a project repo."""
    return {
        "listSteps": listSteps,
        "sProjectRepoPath": "/workspace/proj",
    }


def _fdictBuildLevelPollContext():
    """Return the minimal poll context with a save spy."""
    return {"docker": MagicMock(), "save": MagicMock(), "paths": {}}


def _fdictActivePollStep():
    """Return a step with activity whose L1 requirements are all met.

    Declares one data file so exactly one L3 criterion applies — the
    expected "attained" L3 cell below is earned, not vacuous.
    """
    return {
        "sDirectory": "stepA", "sName": "A",
        "saDataFiles": ["stepA/output.json"],
        "dictVerification": {"sUser": "passed", "sUnitTest": "passed"},
    }


def _fdictAttainedCell(iSatisfied, iTotal):
    """Return one expected attained wire cell."""
    return {
        "sState": "attained", "iSatisfied": iSatisfied,
        "iTotal": iTotal, "bRegression": False,
    }


_DICT_ALL_ATTAINED_STEP_CELLS = {
    "s1": _fdictAttainedCell(3, 3),
    "s2": _fdictAttainedCell(2, 2),
    "s3": _fdictAttainedCell(1, 1),
}

_DICT_ALL_ATTAINED_SCOPE_CELLS = {
    "s1": _fdictAttainedCell(1, 1),
    "s2": _fdictAttainedCell(2, 2),
    "s3": _fdictAttainedCell(6, 6),
}

_DICT_NO_WARNING = {
    "iLowestNonAttainedLevel": 4,
    "iWarningLevel": None,
    "sWarningSeverity": None,
    "sWarningHint": "",
}

_DICT_PRIOR_HIGH_WATER = {
    "1": "2026-01-01T00:00:00Z",
    "2": "2026-01-02T00:00:00Z",
    "3": "2026-01-03T00:00:00Z",
}


class TestPollLevelStatePayload:
    @pytest.mark.asyncio
    async def test_response_carries_level_state_keys(self):
        """The level-state wire keys arrive with their documented shapes."""
        dictWorkflow = _fdictBuildLevelWorkflow([_fdictActivePollStep()])
        dictCtx = _fdictBuildLevelPollContext()
        with _fstackEnterPollLevelPatches([], [], []):
            dictResult = await _fdictFetchOutputStatus(
                dictCtx, "cid1", dictWorkflow, {},
            )
        assert dictResult["dictStepLevels"] == {
            "0": _DICT_ALL_ATTAINED_STEP_CELLS,
        }
        assert dictResult["dictWorkflowScopeLevels"] == (
            _DICT_ALL_ATTAINED_SCOPE_CELLS
        )
        assert dictResult["dictStepLevelWarnings"] == {
            "0": _DICT_NO_WARNING,
        }
        assert set(dictResult["dictStepLevelHighWater"]["0"]) == {
            "1", "2", "3",
        }
        assert set(dictResult["dictWorkflowLevelHighWater"]) == {
            "1", "2", "3",
        }

    @pytest.mark.asyncio
    async def test_inactive_step_with_outputs_on_disk_is_unassessed(self):
        """The poll must thread ``dictMaxMtimeByStep`` into the level
        projection: an inactive step whose declared outputs exist on
        disk arrives on the wire as ``unassessed``, never
        ``not-started``. A dropped argument silently reverts every
        such step to not-started while the unit suite stays green —
        this is the wire-level guard."""
        dictStep = {
            "sDirectory": "stepA", "sName": "A",
            "saDataFiles": ["stepA/output.json"],
            "dictVerification": {"sUser": "untested"},
        }
        dictWorkflow = _fdictBuildLevelWorkflow([dictStep])
        dictCtx = _fdictBuildLevelPollContext()
        with _fstackEnterPollLevelPatches([], [], []):
            with patch(
                "vaibify.gui.routes.pipelineRoutes."
                "_fdictComputeMaxMtimeByStep",
                return_value={"0": "1750000000"},
            ):
                dictResult = await _fdictFetchOutputStatus(
                    dictCtx, "cid1", dictWorkflow, {},
                )
        for sLevelKey in ("s1", "s2", "s3"):
            assert dictResult["dictStepLevels"]["0"][sLevelKey][
                "sState"] == "unassessed", sLevelKey

    @pytest.mark.asyncio
    async def test_response_carries_envelope_detail_keys(self):
        """The envelope-detail payload arrives with all its sections.

        Four render sections plus the four project-wide status
        booleans the Publication/Reproducibility rows consume.
        """
        dictWorkflow = _fdictBuildLevelWorkflow([_fdictActivePollStep()])
        dictCtx = _fdictBuildLevelPollContext()
        with _fstackEnterPollLevelPatches([], [], []):
            dictResult = await _fdictFetchOutputStatus(
                dictCtx, "cid1", dictWorkflow, {},
            )
        dictDetail = dictResult["dictWorkflowEnvelopeDetail"]
        assert set(dictDetail.keys()) == {
            "listBinaries", "dictArtifacts",
            "dictDeterminism", "dictRemoteSyncs",
            "bAiDeclarationAttested", "bRebuildAttestationCurrent",
            "bOverleafBound", "bArxivConfigured",
        }
        assert dictDetail["listBinaries"] == []
        assert dictDetail["dictDeterminism"] is None
        assert set(dictDetail["dictRemoteSyncs"].keys()) == {
            "github", "zenodo", "overleaf", "arxiv",
        }
        assert dictDetail["bOverleafBound"] is False
        assert dictDetail["bArxivConfigured"] is False
        assert dictDetail["bRebuildAttestationCurrent"] is False

    @pytest.mark.asyncio
    async def test_level_transition_triggers_exactly_one_save(self):
        """First attainment stamps high water and persists once."""
        dictWorkflow = _fdictBuildLevelWorkflow([_fdictActivePollStep()])
        dictCtx = _fdictBuildLevelPollContext()
        with _fstackEnterPollLevelPatches([], [], []):
            await _fdictFetchOutputStatus(
                dictCtx, "cid1", dictWorkflow, {},
            )
        dictCtx["save"].assert_called_once_with("cid1", dictWorkflow)

    @pytest.mark.asyncio
    async def test_steady_state_poll_triggers_zero_saves(self):
        """Already-stamped levels re-attaining persist nothing."""
        dictStep = _fdictActivePollStep()
        dictStep["dictLevelHighWater"] = dict(_DICT_PRIOR_HIGH_WATER)
        dictWorkflow = _fdictBuildLevelWorkflow([dictStep])
        dictWorkflow["dictWorkflowLevelHighWater"] = dict(
            _DICT_PRIOR_HIGH_WATER,
        )
        dictCtx = _fdictBuildLevelPollContext()
        with _fstackEnterPollLevelPatches([], [], []):
            await _fdictFetchOutputStatus(
                dictCtx, "cid1", dictWorkflow, {},
            )
        dictCtx["save"].assert_not_called()

    @pytest.mark.asyncio
    async def test_private_flag_absent_from_response(self):
        """The ratchet plumbing key never reaches the wire."""
        dictWorkflow = _fdictBuildLevelWorkflow([_fdictActivePollStep()])
        dictCtx = _fdictBuildLevelPollContext()
        with _fstackEnterPollLevelPatches([], [], []):
            dictResult = await _fdictFetchOutputStatus(
                dictCtx, "cid1", dictWorkflow, {},
            )
        assert _S_LEVEL_RATCHET_FLAG_KEY not in dictResult

    @pytest.mark.asyncio
    async def test_regressed_step_high_water_still_in_payload(self):
        """Regression memory: a regressed step keeps its stamps, no save,
        and the cell carries the bRegression flag."""
        dictStep = _fdictActivePollStep()
        dictStep["dictVerification"]["sUnitTest"] = "failed"
        dictStep["dictLevelHighWater"] = dict(_DICT_PRIOR_HIGH_WATER)
        dictWorkflow = _fdictBuildLevelWorkflow([dictStep])
        dictWorkflow["dictWorkflowLevelHighWater"] = dict(
            _DICT_PRIOR_HIGH_WATER,
        )
        dictCtx = _fdictBuildLevelPollContext()
        listLevel1 = [{"iStepIndex": 0, "sCriterion": "axis-not-green"}]
        with _fstackEnterPollLevelPatches(listLevel1, [], []):
            dictResult = await _fdictFetchOutputStatus(
                dictCtx, "cid1", dictWorkflow, {},
            )
        assert dictResult["dictStepLevels"]["0"]["s1"] == {
            "sState": "partial", "iSatisfied": 2, "iTotal": 3,
            "bRegression": True,
        }
        assert dictResult["dictStepLevelHighWater"]["0"] == (
            _DICT_PRIOR_HIGH_WATER
        )
        dictWarning = dictResult["dictStepLevelWarnings"]["0"]
        assert dictWarning["iWarningLevel"] == 1
        assert dictWarning["sWarningSeverity"] == "red"
        dictCtx["save"].assert_not_called()


class TestFnSaveIfLevelHighWaterChanged:
    def test_flag_true_saves_once_and_pops_key(self):
        dictCtx = {"save": MagicMock()}
        dictWorkflow = {"listSteps": []}
        dictRest = {_S_LEVEL_RATCHET_FLAG_KEY: True, "iAICSLevel": 1}
        _fnSaveIfLevelHighWaterChanged(
            dictCtx, "cid1", dictWorkflow, dictRest,
        )
        dictCtx["save"].assert_called_once_with("cid1", dictWorkflow)
        assert _S_LEVEL_RATCHET_FLAG_KEY not in dictRest

    def test_flag_false_pops_key_without_save(self):
        dictCtx = {"save": MagicMock()}
        dictRest = {_S_LEVEL_RATCHET_FLAG_KEY: False}
        _fnSaveIfLevelHighWaterChanged(
            dictCtx, "cid1", {"listSteps": []}, dictRest,
        )
        dictCtx["save"].assert_not_called()
        assert _S_LEVEL_RATCHET_FLAG_KEY not in dictRest

    def test_flag_absent_saves_nothing(self):
        dictCtx = {"save": MagicMock()}
        dictRest = {"iAICSLevel": 1}
        _fnSaveIfLevelHighWaterChanged(
            dictCtx, "cid1", {"listSteps": []}, dictRest,
        )
        dictCtx["save"].assert_not_called()


class TestBuildWorkflowEnvelopeDetail:
    """Unit tests for the expandable Workflow-row envelope payload."""

    def _fdictWorkflowWithBinary(self, sRepoPath):
        """Return a workflow declaring one standalone binary."""
        return {
            "sProjectRepoPath": sRepoPath,
            "listSteps": [],
            "listDeclaredBinaries": [{
                "sBinaryPath": "/usr/local/bin/toolx",
                "sPurpose": "core solver",
                "sExpectedVersion": "2.0",
            }],
        }

    def _fnWriteEnvironmentCapture(self, pathRepo, sVersion, sSha256):
        """Write an environment.json capturing the declared binary."""
        pathVaibify = pathRepo / ".vaibify"
        pathVaibify.mkdir(exist_ok=True)
        (pathVaibify / "environment.json").write_text(json.dumps({
            "dictHostBinaries": {"listBinaries": [{
                "sBinaryPath": "/usr/local/bin/toolx",
                "sVersion": sVersion,
                "sSha256": sSha256,
            }]},
        }))

    def test_binary_version_mismatch_reported_honestly(self, tmp_path):
        from vaibify.gui.routes.pipelineRoutes import (
            _fdictBuildWorkflowEnvelopeDetail,
        )
        dictWorkflow = self._fdictWorkflowWithBinary(str(tmp_path))
        self._fnWriteEnvironmentCapture(tmp_path, "1.9", "a" * 64)
        dictDetail = _fdictBuildWorkflowEnvelopeDetail(
            dictWorkflow, str(tmp_path),
        )
        dictEntry = dictDetail["listBinaries"][0]
        assert dictEntry["sExpectedVersion"] == "2.0"
        assert dictEntry["sCapturedVersion"] == "1.9"
        assert dictEntry["bVersionMatch"] is False
        assert dictEntry["bHashCurrent"] is True
        assert dictEntry["sCapturedSha256"] == "a" * 64

    def test_binary_version_match_when_both_known(self, tmp_path):
        from vaibify.gui.routes.pipelineRoutes import (
            _fdictBuildWorkflowEnvelopeDetail,
        )
        dictWorkflow = self._fdictWorkflowWithBinary(str(tmp_path))
        self._fnWriteEnvironmentCapture(tmp_path, "2.0", "b" * 64)
        dictEntry = _fdictBuildWorkflowEnvelopeDetail(
            dictWorkflow, str(tmp_path),
        )["listBinaries"][0]
        assert dictEntry["bVersionMatch"] is True

    def test_uncaptured_binary_keeps_nulls_never_fabricates(
        self, tmp_path,
    ):
        """NULL-CAPTURE HONESTY: no environment.json entry means null
        capture fields, an unknowable (None) version match, and a
        false bHashCurrent — never invented values."""
        from vaibify.gui.routes.pipelineRoutes import (
            _fdictBuildWorkflowEnvelopeDetail,
        )
        dictWorkflow = self._fdictWorkflowWithBinary(str(tmp_path))
        dictEntry = _fdictBuildWorkflowEnvelopeDetail(
            dictWorkflow, str(tmp_path),
        )["listBinaries"][0]
        assert dictEntry["sCapturedVersion"] is None
        assert dictEntry["sCapturedSha256"] is None
        assert dictEntry["bVersionMatch"] is None
        assert dictEntry["bHashCurrent"] is False

    def test_null_sha_capture_does_not_count_as_hash_current(
        self, tmp_path,
    ):
        from vaibify.gui.routes.pipelineRoutes import (
            _fdictBuildWorkflowEnvelopeDetail,
        )
        dictWorkflow = self._fdictWorkflowWithBinary(str(tmp_path))
        self._fnWriteEnvironmentCapture(tmp_path, "2.0", None)
        dictEntry = _fdictBuildWorkflowEnvelopeDetail(
            dictWorkflow, str(tmp_path),
        )["listBinaries"][0]
        assert dictEntry["bHashCurrent"] is False
        assert dictEntry["bVersionMatch"] is True

    def test_artifacts_pair_presence_with_satisfaction(self, tmp_path):
        """An unhashed requirements.lock is present but not satisfied;
        a missing manifest is neither."""
        from vaibify.gui.routes.pipelineRoutes import (
            _fdictBuildWorkflowEnvelopeDetail,
        )
        (tmp_path / "requirements.lock").write_text("packagex==1.0\n")
        dictDetail = _fdictBuildWorkflowEnvelopeDetail(
            {"sProjectRepoPath": str(tmp_path), "listSteps": []},
            str(tmp_path),
        )
        dictArtifacts = dictDetail["dictArtifacts"]
        assert set(dictArtifacts.keys()) == {
            "manifest", "dependencyLock", "environmentSnapshot",
            "dockerfile", "reproduceScript",
        }
        assert dictArtifacts["dependencyLock"] == {
            "bPresent": True, "bSatisfied": False,
        }
        assert dictArtifacts["manifest"] == {
            "bPresent": False, "bSatisfied": False,
        }

    def test_determinism_declaration_passes_through(self, tmp_path):
        from vaibify.gui.routes.pipelineRoutes import (
            _fdictBuildWorkflowEnvelopeDetail,
        )
        dictDeterminism = {"sBlasBackend": "openblas", "iGlobalSeed": 7}
        dictDetail = _fdictBuildWorkflowEnvelopeDetail(
            {
                "sProjectRepoPath": str(tmp_path),
                "listSteps": [],
                "dictDeterminism": dictDeterminism,
            },
            str(tmp_path),
        )
        assert dictDetail["dictDeterminism"] == dictDeterminism

    def test_remote_sync_summary_from_cache_counts_and_staleness(
        self, tmp_path,
    ):
        from vaibify.gui.routes.pipelineRoutes import (
            _fdictBuildWorkflowEnvelopeDetail,
        )
        pathVaibify = tmp_path / ".vaibify"
        pathVaibify.mkdir()
        (pathVaibify / "syncStatus.json").write_text(json.dumps({
            "github": {
                "sService": "github",
                "sLastVerified": "2020-01-01T00:00:00Z",
                "iTotalFiles": 3,
                "iMatching": 2,
                "listDiverged": [{"sPath": "data/output.json"}],
            },
        }))
        dictSyncs = _fdictBuildWorkflowEnvelopeDetail(
            {"sProjectRepoPath": str(tmp_path), "listSteps": []},
            str(tmp_path),
        )["dictRemoteSyncs"]
        assert dictSyncs["github"] == {
            "sLastVerified": "2020-01-01T00:00:00Z",
            "iTotalFiles": 3,
            "iMatching": 2,
            "iDivergedCount": 1,
            "bStale": True,
        }
        assert dictSyncs["zenodo"] is None
        assert dictSyncs["overleaf"] is None
        assert dictSyncs["arxiv"] is None

    def test_missing_repo_yields_empty_honest_payload(self):
        from vaibify.gui.routes.pipelineRoutes import (
            _fdictBuildWorkflowEnvelopeDetail,
        )
        dictDetail = _fdictBuildWorkflowEnvelopeDetail(
            {"sProjectRepoPath": "", "listSteps": []}, "",
        )
        assert dictDetail["dictArtifacts"] == {}
        assert dictDetail["listBinaries"] == []
        assert dictDetail["dictRemoteSyncs"] == {
            "github": None, "zenodo": None,
            "overleaf": None, "arxiv": None,
        }
