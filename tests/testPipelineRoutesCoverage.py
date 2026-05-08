"""Tests for uncovered lines in vaibify.gui.routes.pipelineRoutes."""

import pytest
from unittest.mock import MagicMock, patch, AsyncMock

from vaibify.gui.routes.pipelineRoutes import (
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
    _fnBackfillMissingConftest,
    _fnDeleteLegacyMarkers,
    _fdictFetchOutputStatus,
    fdictComputeFileStatus,
)


# ── Line 73: _fnMarkPipelineStopped when dictState is running ─────

class TestFnMarkPipelineStopped:
    def test_state_is_running_writes_completed(self):
        """Cover line 73: pipelineState.fnUpdateState called."""
        mockDocker = MagicMock()
        dictState = {"bRunning": True}
        with patch(
            "vaibify.gui.pipelineState.fdictReadState",
            return_value=dictState,
        ), patch(
            "vaibify.gui.pipelineState.fnUpdateState",
        ) as mockUpdate, patch(
            "vaibify.gui.pipelineState.fdictBuildCompletedState",
            return_value={"bRunning": False},
        ):
            _fnMarkPipelineStopped(mockDocker, "cid1")
            mockUpdate.assert_called_once()

    def test_state_none_returns_early(self):
        mockDocker = MagicMock()
        with patch(
            "vaibify.gui.pipelineState.fdictReadState",
            return_value=None,
        ), patch(
            "vaibify.gui.pipelineState.fnUpdateState",
        ) as mockUpdate:
            _fnMarkPipelineStopped(mockDocker, "cid1")
            mockUpdate.assert_not_called()

    def test_state_not_running_returns_early(self):
        mockDocker = MagicMock()
        with patch(
            "vaibify.gui.pipelineState.fdictReadState",
            return_value={"bRunning": False},
        ), patch(
            "vaibify.gui.pipelineState.fnUpdateState",
        ) as mockUpdate:
            _fnMarkPipelineStopped(mockDocker, "cid1")
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
            assert response.json() == {"bRunning": False}


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
            "vaibify.gui.routes.pipelineRoutes._fnMarkPipelineStopped"
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
    def test_ws_invalid_origin_closes_4003(self):
        """Cover lines 211-213."""
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        app = FastAPI()
        dictCtx = {
            "require": MagicMock(),
            "sSessionToken": "tok",
            "setAllowedContainers": set(),
        }
        with patch(
            "vaibify.gui.routes.pipelineRoutes"
            ".fbValidateWebSocketOrigin",
            return_value=False,
        ):
            from vaibify.gui.routes.pipelineRoutes import (
                _fnRegisterPipelineWs,
            )
            _fnRegisterPipelineWs(app, dictCtx)
            client = TestClient(app)
            with pytest.raises(Exception):
                with client.websocket_connect(
                    "/ws/pipeline/cid1?sToken=tok"
                ):
                    pass

    def test_ws_bad_token_closes_4401(self):
        """Cover lines 215-216."""
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        app = FastAPI()
        dictCtx = {
            "require": MagicMock(),
            "sSessionToken": "good",
            "setAllowedContainers": set(),
        }
        with patch(
            "vaibify.gui.routes.pipelineRoutes"
            ".fbValidateWebSocketOrigin",
            return_value=True,
        ):
            from vaibify.gui.routes.pipelineRoutes import (
                _fnRegisterPipelineWs,
            )
            _fnRegisterPipelineWs(app, dictCtx)
            client = TestClient(app)
            with pytest.raises(Exception):
                with client.websocket_connect(
                    "/ws/pipeline/cid1?sToken=bad"
                ):
                    pass

    def test_ws_container_not_allowed_closes_4403(self):
        """Cover lines 218-219."""
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        app = FastAPI()
        dictCtx = {
            "require": MagicMock(),
            "sSessionToken": "tok",
            "setAllowedContainers": {"other"},
        }
        with patch(
            "vaibify.gui.routes.pipelineRoutes"
            ".fbValidateWebSocketOrigin",
            return_value=True,
        ):
            from vaibify.gui.routes.pipelineRoutes import (
                _fnRegisterPipelineWs,
            )
            _fnRegisterPipelineWs(app, dictCtx)
            client = TestClient(app)
            with pytest.raises(Exception):
                with client.websocket_connect(
                    "/ws/pipeline/cid1?sToken=tok"
                ):
                    pass


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
            "vaibify.gui.routes.pipelineRoutes._fdictGetModTimes",
            return_value={},
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
            "vaibify.gui.routes.pipelineRoutes._fdictGetModTimes",
            return_value={},
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
            "vaibify.gui.routes.pipelineRoutes._fdictGetModTimes",
            return_value={},
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
            "vaibify.gui.routes.pipelineRoutes._fdictGetModTimes",
            return_value={},
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
            "vaibify.gui.routes.pipelineRoutes._fdictGetModTimes",
            return_value={},
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
            "vaibify.gui.routes.pipelineRoutes._fdictGetModTimes",
            return_value={},
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


# ── Lines 417-433: _fnBackfillMissingConftest ────────────────────

class TestFnBackfillMissingConftest:
    @pytest.mark.asyncio
    async def test_backfill_writes_conftest(self):
        """Cover lines 417-427."""
        mockDocker = MagicMock()
        with patch(
            "vaibify.gui.testGenerator.fnWriteConftestMarker",
        ), patch(
            "vaibify.gui.testGenerator.fsConftestContent",
            return_value="# conftest",
        ), patch(
            "vaibify.gui.routes.pipelineRoutes"
            "._fnDeleteLegacyMarkers",
        ), patch(
            "vaibify.gui.routes.pipelineRoutes"
            "._fnEnsureConftestTemplate",
        ) as mockEnsure:
            await _fnBackfillMissingConftest(
                mockDocker, "cid1",
                ["/workspace/step1", "/workspace/step2"],
                "/workspace/DemoRepo",
            )
            mockEnsure.assert_called_once()

    @pytest.mark.asyncio
    async def test_backfill_handles_exception(self):
        """Cover lines 428-432: exception during write."""
        mockDocker = MagicMock()
        with patch(
            "vaibify.gui.testGenerator.fnWriteConftestMarker",
            side_effect=RuntimeError("write failed"),
        ), patch(
            "vaibify.gui.testGenerator.fsConftestContent",
            return_value="# conftest",
        ), patch(
            "vaibify.gui.routes.pipelineRoutes"
            "._fnDeleteLegacyMarkers",
        ), patch(
            "vaibify.gui.routes.pipelineRoutes"
            "._fnEnsureConftestTemplate",
        ):
            await _fnBackfillMissingConftest(
                mockDocker, "cid1",
                ["/workspace/step1"],
                "/workspace/DemoRepo",
            )

    @pytest.mark.asyncio
    async def test_backfill_empty_list_returns_early(self):
        """Cover lines 415-416: empty list."""
        mockDocker = MagicMock()
        await _fnBackfillMissingConftest(
            mockDocker, "cid1", [], "/workspace/DemoRepo",
        )


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


# ── _fbApplyRandomnessLint ────────────────────────────────────────


from vaibify.gui.routes.pipelineRoutes import (
    _fbApplyRandomnessLint,
    _fdictReconcileLivenessIfNeeded,
    _fsBuildHeartbeatStaleReason,
    _ffParseMtime,
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


class TestFdictReconcileLivenessIfNeeded:
    def test_not_running_returns_state_unchanged(self):
        """Line 194: bRunning False short-circuits."""
        import asyncio
        dictState = {"bRunning": False}
        dictResult = asyncio.run(
            _fdictReconcileLivenessIfNeeded(
                MagicMock(), "cid", dictState,
            ),
        )
        assert dictResult is dictState


class TestFsBuildHeartbeatStaleReason:
    def test_unparseable_timestamp_returns_safe_string(self):
        """Line 222-223: bad isoformat falls back to a generic reason."""
        dictState = {"sLastHeartbeat": "not-a-timestamp"}
        sReason = _fsBuildHeartbeatStaleReason(dictState)
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
