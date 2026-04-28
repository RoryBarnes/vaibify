"""Tests for vaibify.gui.pipelineServer REST routes."""

import json
import pytest
from unittest.mock import patch
from fastapi.testclient import TestClient

from vaibify.gui import pipelineServer


S_CONTAINER_ID = "abc123container"
S_WORKFLOW_PATH = "/workspace/.vaibify/workflows/test.json"

DICT_WORKFLOW = {
    "sWorkflowName": "Test Pipeline",
    "sPlotDirectory": "Plot",
    "sFigureType": "pdf",
    "iNumberOfCores": 4,
    "listSteps": [
        {
            "sName": "Step A",
            "sDirectory": "stepA",
            "bPlotOnly": True,
            "bRunEnabled": True,
            "bInteractive": False,
            "saDataCommands": ["python dataGenerate.py"],
            "saDataFiles": ["output.dat"],
            "saTestCommands": [],
            "saPlotCommands": ["python plotResults.py"],
            "saPlotFiles": ["{sPlotDirectory}/fig.{sFigureType}"],
            "dictRunStats": {},
            "dictVerification": {
                "sUnitTest": "untested",
                "sUser": "untested",
            },
        },
    ],
}


class MockDockerConnection:
    """Mimics the DockerConnection API for testing."""

    def __init__(self):
        self._dictFiles = {}
        self._dictCommands = {}

    def flistGetRunningContainers(self):
        return [
            {
                "sContainerId": S_CONTAINER_ID,
                "sShortId": "abc123",
                "sName": "test-container",
                "sImage": "ubuntu:24.04",
            },
        ]

    def ftResultExecuteCommand(self, sContainerId, sCommand):
        if "test -d" in sCommand and ".vaibify" in sCommand:
            return (0, "")
        if "find" in sCommand and ".vaibify/workflows" in sCommand:
            return (0, S_WORKFLOW_PATH + "\n")
        if "find" in sCommand:
            return (0, "")
        if "test -d" in sCommand:
            return (0, "f")
        if "cat" in sCommand and "pipeline_state" in sCommand:
            return (1, "")
        if "stat -c" in sCommand:
            return (0, "")
        if "ps aux" in sCommand:
            return (0, "0\n")
        return (0, "")

    def fbaFetchFile(self, sContainerId, sPath):
        if sPath in self._dictFiles:
            return self._dictFiles[sPath]
        if sPath.endswith(".json"):
            return json.dumps(DICT_WORKFLOW).encode("utf-8")
        raise FileNotFoundError(f"Not found: {sPath}")

    def fnWriteFile(self, sContainerId, sPath, baContent):
        self._dictFiles[sPath] = baContent


    def fsExecCreate(self, sContainerId, sCommand=None, sUser=None):
        return "exec-id-mock"

    def fsocketExecStart(self, sExecId):
        return None

    def fnExecResize(self, sExecId, iRows, iColumns):
        pass


def _fmockCreateDocker():
    """Return a MockDockerConnection instead of a real one."""
    return MockDockerConnection()


@pytest.fixture
def clientHttp():
    """Create a TestClient with a mocked Docker connection."""
    with patch.object(
        pipelineServer, "_fconnectionCreateDocker", _fmockCreateDocker
    ):
        app = pipelineServer.fappCreateApplication(
            sWorkspaceRoot="/workspace",
            sTerminalUserArg="testuser",
        )
    return TestClient(
        app, headers={"X-Session-Token": app.state.sSessionToken},
    )


@pytest.fixture
def sSessionToken(clientHttp):
    """Fetch the session token from the running app."""
    responseHttp = clientHttp.get("/api/session-token")
    return responseHttp.json()["sToken"]


def _fnConnectToContainer(clientHttp):
    """POST to /api/connect and return the response dict."""
    responseHttp = clientHttp.post(
        f"/api/connect/{S_CONTAINER_ID}",
        params={"sWorkflowPath": S_WORKFLOW_PATH},
    )
    assert responseHttp.status_code == 200
    return responseHttp.json()


# ── Index and session token ────────────────────────────────────


def test_get_index_returns_html(clientHttp):
    responseHttp = clientHttp.get("/")
    assert responseHttp.status_code == 200
    assert "text/html" in responseHttp.headers["content-type"]


def test_get_session_token(clientHttp, sSessionToken):
    assert isinstance(sSessionToken, str)
    assert len(sSessionToken) > 10


# ── Security headers ──────────────────────────────────────────


def test_security_headers_present(clientHttp):
    responseHttp = clientHttp.get("/api/session-token")
    assert responseHttp.headers["X-Content-Type-Options"] == "nosniff"
    assert responseHttp.headers["X-Frame-Options"] == "DENY"


# ── Containers ────────────────────────────────────────────────


# ── Connect ───────────────────────────────────────────────────


def test_connect_returns_workflow(clientHttp):
    dictResult = _fnConnectToContainer(clientHttp)
    assert dictResult["sContainerId"] == S_CONTAINER_ID
    assert "dictWorkflow" in dictResult
    assert dictResult["sWorkflowPath"] == S_WORKFLOW_PATH


def test_connect_steps_carry_slabel(clientHttp):
    """Every step in the connect response carries sLabel."""
    dictResult = _fnConnectToContainer(clientHttp)
    listSteps = dictResult["dictWorkflow"]["listSteps"]
    assert listSteps, "test fixture should have at least one step"
    for dictStep in listSteps:
        assert "sLabel" in dictStep
        assert dictStep["sLabel"]


def test_connect_caches_workflow(clientHttp):
    _fnConnectToContainer(clientHttp)
    responseHttp = clientHttp.get(
        f"/api/steps/{S_CONTAINER_ID}"
    )
    assert responseHttp.status_code == 200


def test_connect_includes_file_status(clientHttp):
    dictResult = _fnConnectToContainer(clientHttp)
    assert "dictFileStatus" in dictResult
    dictFileStatus = dictResult["dictFileStatus"]
    assert dictFileStatus is not None
    for sKey in (
        "dictModTimes",
        "dictMaxMtimeByStep",
        "dictMaxPlotMtimeByStep",
        "dictMaxDataMtimeByStep",
        "dictMarkerMtimeByStep",
        "dictInvalidatedSteps",
        "dictScriptStatus",
        "dictTestMarkers",
        "dictTestFileChanges",
    ):
        assert sKey in dictFileStatus, sKey


def test_connect_survives_file_status_failure(clientHttp):
    with patch(
        "vaibify.gui.routes.pipelineRoutes.fdictComputeFileStatus",
        side_effect=RuntimeError("boom"),
    ):
        responseHttp = clientHttp.post(
            f"/api/connect/{S_CONTAINER_ID}",
            params={"sWorkflowPath": S_WORKFLOW_PATH},
        )
    assert responseHttp.status_code == 200
    dictResult = responseHttp.json()
    assert dictResult["dictWorkflow"] is not None
    assert dictResult["dictFileStatus"] is None


# ── User info ─────────────────────────────────────────────────


def test_get_user_returns_name(clientHttp):
    responseHttp = clientHttp.get("/api/user")
    assert responseHttp.status_code == 200
    assert responseHttp.json()["sUserName"] == "testuser"


# ── Runtime info ──────────────────────────────────────────────


def test_get_runtime_returns_dict(clientHttp):
    responseHttp = clientHttp.get("/api/runtime")
    assert responseHttp.status_code == 200
    dictResult = responseHttp.json()
    assert "sRuntime" in dictResult
    assert "sSleepWarning" in dictResult


# ── Steps CRUD ────────────────────────────────────────────────


def test_get_steps_returns_list(clientHttp):
    _fnConnectToContainer(clientHttp)
    responseHttp = clientHttp.get(
        f"/api/steps/{S_CONTAINER_ID}"
    )
    assert responseHttp.status_code == 200
    listSteps = responseHttp.json()
    assert len(listSteps) == 1
    assert listSteps[0]["sName"] == "Step A"


def test_get_step_by_index(clientHttp):
    _fnConnectToContainer(clientHttp)
    responseHttp = clientHttp.get(
        f"/api/steps/{S_CONTAINER_ID}/0"
    )
    assert responseHttp.status_code == 200
    dictStep = responseHttp.json()
    assert dictStep["sName"] == "Step A"
    assert "saResolvedOutputFiles" in dictStep
    assert dictStep["sLabel"] == "A01"


def test_resolve_step_by_label(clientHttp):
    """GET /by-label/<sLabel> returns the 0-based index."""
    _fnConnectToContainer(clientHttp)
    responseHttp = clientHttp.get(
        f"/api/steps/{S_CONTAINER_ID}/by-label/A01"
    )
    assert responseHttp.status_code == 200
    dictResult = responseHttp.json()
    assert dictResult == {"iStepIndex": 0, "sLabel": "A01"}


def test_resolve_unknown_step_label(clientHttp):
    _fnConnectToContainer(clientHttp)
    responseHttp = clientHttp.get(
        f"/api/steps/{S_CONTAINER_ID}/by-label/A99"
    )
    assert responseHttp.status_code == 404
    assert "A99" in responseHttp.json()["detail"]


def test_get_step_invalid_index(clientHttp):
    _fnConnectToContainer(clientHttp)
    responseHttp = clientHttp.get(
        f"/api/steps/{S_CONTAINER_ID}/99"
    )
    assert responseHttp.status_code == 404


def test_create_step(clientHttp):
    _fnConnectToContainer(clientHttp)
    dictPayload = {
        "sName": "Step B",
        "sDirectory": "stepB",
        "bPlotOnly": False,
        "saPlotCommands": ["python plotB.py"],
        "saPlotFiles": ["figB.pdf"],
    }
    responseHttp = clientHttp.post(
        f"/api/steps/{S_CONTAINER_ID}/create",
        json=dictPayload,
    )
    assert responseHttp.status_code == 200
    dictResult = responseHttp.json()
    assert dictResult["iIndex"] == 1
    assert dictResult["dictStep"]["sName"] == "Step B"


def test_insert_step_at_position(clientHttp):
    _fnConnectToContainer(clientHttp)
    dictPayload = {
        "sName": "Inserted Step",
        "sDirectory": "/workspace/inserted",
        "saPlotCommands": [],
        "saPlotFiles": [],
    }
    responseHttp = clientHttp.post(
        f"/api/steps/{S_CONTAINER_ID}/insert/0",
        json=dictPayload,
    )
    assert responseHttp.status_code == 200
    dictResult = responseHttp.json()
    assert dictResult["iIndex"] == 0
    assert dictResult["dictStep"]["sName"] == "Inserted Step"


def test_update_step(clientHttp):
    _fnConnectToContainer(clientHttp)
    dictPayload = {"sName": "Renamed Step"}
    responseHttp = clientHttp.put(
        f"/api/steps/{S_CONTAINER_ID}/0",
        json=dictPayload,
    )
    assert responseHttp.status_code == 200
    dictResult = responseHttp.json()
    assert dictResult["sName"] == "Renamed Step"


def test_update_step_invalid_index(clientHttp):
    _fnConnectToContainer(clientHttp)
    dictPayload = {"sName": "Ghost Step"}
    responseHttp = clientHttp.put(
        f"/api/steps/{S_CONTAINER_ID}/99",
        json=dictPayload,
    )
    assert responseHttp.status_code == 404


def test_delete_step(clientHttp):
    _fnConnectToContainer(clientHttp)
    responseHttp = clientHttp.delete(
        f"/api/steps/{S_CONTAINER_ID}/0"
    )
    assert responseHttp.status_code == 200
    dictResult = responseHttp.json()
    assert dictResult["bSuccess"] is True
    assert len(dictResult["listSteps"]) == 0


def test_delete_step_invalid_index(clientHttp):
    _fnConnectToContainer(clientHttp)
    responseHttp = clientHttp.delete(
        f"/api/steps/{S_CONTAINER_ID}/99"
    )
    assert responseHttp.status_code == 404


def test_reorder_steps(clientHttp):
    _fnConnectToContainer(clientHttp)
    clientHttp.post(
        f"/api/steps/{S_CONTAINER_ID}/create",
        json={
            "sName": "Step B",
            "sDirectory": "stepB",
            "saPlotCommands": [],
            "saPlotFiles": [],
        },
    )
    dictPayload = {"iFromIndex": 0, "iToIndex": 1}
    responseHttp = clientHttp.post(
        f"/api/steps/{S_CONTAINER_ID}/reorder",
        json=dictPayload,
    )
    assert responseHttp.status_code == 200
    listSteps = responseHttp.json()["listSteps"]
    assert listSteps[0]["sName"] == "Step B"


# ── Validate references ───────────────────────────────────────


def test_validate_references(clientHttp):
    _fnConnectToContainer(clientHttp)
    responseHttp = clientHttp.get(
        f"/api/steps/{S_CONTAINER_ID}/validate"
    )
    assert responseHttp.status_code == 200
    assert "listWarnings" in responseHttp.json()


# ── Settings ──────────────────────────────────────────────────


def test_get_settings(clientHttp):
    _fnConnectToContainer(clientHttp)
    responseHttp = clientHttp.get(
        f"/api/settings/{S_CONTAINER_ID}"
    )
    assert responseHttp.status_code == 200
    dictSettings = responseHttp.json()
    assert dictSettings["sPlotDirectory"] == "Plot"
    assert dictSettings["sFigureType"] == "pdf"
    assert dictSettings["iNumberOfCores"] == 4


def test_update_settings(clientHttp):
    _fnConnectToContainer(clientHttp)
    dictPayload = {"sPlotDirectory": "Figures", "sFigureType": "png"}
    responseHttp = clientHttp.put(
        f"/api/settings/{S_CONTAINER_ID}",
        json=dictPayload,
    )
    assert responseHttp.status_code == 200
    dictResult = responseHttp.json()
    assert dictResult["sPlotDirectory"] == "Figures"
    assert dictResult["sFigureType"] == "png"


def test_update_settings_partial(clientHttp):
    _fnConnectToContainer(clientHttp)
    dictPayload = {"iNumberOfCores": 8}
    responseHttp = clientHttp.put(
        f"/api/settings/{S_CONTAINER_ID}",
        json=dictPayload,
    )
    assert responseHttp.status_code == 200
    assert responseHttp.json()["iNumberOfCores"] == 8


# ── Not connected errors ─────────────────────────────────────


def test_steps_without_connect_returns_404(clientHttp):
    responseHttp = clientHttp.get(
        f"/api/steps/{S_CONTAINER_ID}"
    )
    assert responseHttp.status_code == 404


def test_settings_without_connect_returns_404(clientHttp):
    responseHttp = clientHttp.get(
        f"/api/settings/{S_CONTAINER_ID}"
    )
    assert responseHttp.status_code == 404


# ── Monitor ───────────────────────────────────────────────────


def test_get_monitor_stats(clientHttp):
    responseHttp = clientHttp.get(
        f"/api/monitor/{S_CONTAINER_ID}"
    )
    assert responseHttp.status_code == 200


# ── Pipeline state ────────────────────────────────────────────


def test_get_pipeline_state_not_running(clientHttp):
    _fnConnectToContainer(clientHttp)
    responseHttp = clientHttp.get(
        f"/api/pipeline/{S_CONTAINER_ID}/state"
    )
    assert responseHttp.status_code == 200
    assert responseHttp.json()["bRunning"] is False


def _fdictBuildRunningState(sLastHeartbeat):
    """Return a synthetic 'running' state with a custom heartbeat."""
    return {
        "bRunning": True,
        "sAction": "runAll",
        "sLogPath": "/log",
        "sStartTime": "2026-04-28T18:50:05+00:00",
        "sEndTime": "",
        "iExitCode": -1,
        "iActiveStep": 9,
        "iStepCount": 13,
        "dictStepResults": {},
        "listRecentOutput": [],
        "iRunnerPid": 12345,
        "sLastHeartbeat": sLastHeartbeat,
        "sFailureReason": "",
    }


def test_get_pipeline_state_fresh_heartbeat_left_alone(clientHttp):
    """A recent heartbeat means the run is still alive — don't reconcile."""
    from datetime import datetime, timezone
    from vaibify.gui import pipelineState
    _fnConnectToContainer(clientHttp)
    sFresh = datetime.now(timezone.utc).isoformat()
    with patch.object(
        pipelineState, "fdictReadState",
        return_value=_fdictBuildRunningState(sFresh),
    ):
        responseHttp = clientHttp.get(
            f"/api/pipeline/{S_CONTAINER_ID}/state"
        )
    assert responseHttp.status_code == 200
    dictBody = responseHttp.json()
    assert dictBody["bRunning"] is True
    assert dictBody["sFailureReason"] == ""


def test_get_pipeline_state_stale_heartbeat_reconciles(clientHttp):
    """A stale heartbeat flips bRunning to False and stamps a reason."""
    from datetime import datetime, timedelta, timezone
    from vaibify.gui import pipelineState
    _fnConnectToContainer(clientHttp)
    sStale = (
        datetime.now(timezone.utc) - timedelta(seconds=120)
    ).isoformat()
    listWriteCalls = []

    def fnSpyWriteState(connectionDocker, sContainerId, dictState):
        listWriteCalls.append(dict(dictState))

    with patch.object(
        pipelineState, "fdictReadState",
        return_value=_fdictBuildRunningState(sStale),
    ), patch.object(
        pipelineState, "fnWriteState", side_effect=fnSpyWriteState,
    ):
        responseHttp = clientHttp.get(
            f"/api/pipeline/{S_CONTAINER_ID}/state"
        )
    assert responseHttp.status_code == 200
    dictBody = responseHttp.json()
    assert dictBody["bRunning"] is False
    assert dictBody["iExitCode"] == (
        pipelineState.I_EXIT_CODE_RUNNER_DISAPPEARED
    )
    assert "heartbeat_stale" in dictBody["sFailureReason"]
    assert dictBody["sEndTime"], "sEndTime must be stamped on reconcile"
    # Reconciled state must be persisted so subsequent polls are stable.
    assert listWriteCalls, "reconciliation must write back to the state file"
    assert listWriteCalls[-1]["bRunning"] is False


def test_get_pipeline_state_legacy_no_heartbeat_left_alone(clientHttp):
    """Legacy state files (no sLastHeartbeat field) are not reconciled."""
    from vaibify.gui import pipelineState
    _fnConnectToContainer(clientHttp)
    dictLegacyRunning = _fdictBuildRunningState("")
    del dictLegacyRunning["sLastHeartbeat"]
    with patch.object(
        pipelineState, "fdictReadState",
        return_value=dictLegacyRunning,
    ):
        responseHttp = clientHttp.get(
            f"/api/pipeline/{S_CONTAINER_ID}/state"
        )
    assert responseHttp.status_code == 200
    dictBody = responseHttp.json()
    assert dictBody["bRunning"] is True


# ── File status ───────────────────────────────────────────────


def test_get_file_status(clientHttp):
    _fnConnectToContainer(clientHttp)
    responseHttp = clientHttp.get(
        f"/api/pipeline/{S_CONTAINER_ID}/file-status"
    )
    assert responseHttp.status_code == 200
    assert "dictModTimes" in responseHttp.json()


# ── Pipeline kill ─────────────────────────────────────────────


def test_kill_returns_zero_when_no_processes(clientHttp):
    _fnConnectToContainer(clientHttp)
    responseHttp = clientHttp.post(
        f"/api/pipeline/{S_CONTAINER_ID}/kill"
    )
    assert responseHttp.status_code == 200
    dictResult = responseHttp.json()
    assert dictResult["bSuccess"] is True


# ── Pipeline clean ────────────────────────────────────────────


def test_clean_outputs(clientHttp):
    _fnConnectToContainer(clientHttp)
    responseHttp = clientHttp.post(
        f"/api/pipeline/{S_CONTAINER_ID}/clean"
    )
    assert responseHttp.status_code == 200
    assert responseHttp.json()["bSuccess"] is True


# ── Workflow search ───────────────────────────────────────────


def test_find_workflows(clientHttp):
    responseHttp = clientHttp.get(
        f"/api/workflows/{S_CONTAINER_ID}"
    )
    assert responseHttp.status_code == 200
    listWorkflows = responseHttp.json()
    assert isinstance(listWorkflows, list)


# ── Workflow create ───────────────────────────────────────────


def test_create_workflow(clientHttp):
    dictPayload = {
        "sWorkflowName": "New Pipeline",
        "sFileName": "newPipeline",
        "sRepoDirectory": "MyRepo",
    }
    responseHttp = clientHttp.post(
        f"/api/workflows/{S_CONTAINER_ID}/create",
        json=dictPayload,
    )
    assert responseHttp.status_code == 200
    dictResult = responseHttp.json()
    assert dictResult["sName"] == "New Pipeline"
    assert dictResult["sPath"].endswith(".json")


# ── File write ────────────────────────────────────────────────


def test_write_file(clientHttp):
    _fnConnectToContainer(clientHttp)
    dictPayload = {"sContent": "print('hello')"}
    responseHttp = clientHttp.put(
        f"/api/file/{S_CONTAINER_ID}/workspace/test.py",
        json=dictPayload,
    )
    assert responseHttp.status_code == 200
    assert responseHttp.json()["bSuccess"] is True


def test_write_file_path_traversal_blocked(clientHttp):
    _fnConnectToContainer(clientHttp)
    dictPayload = {"sContent": "evil"}
    responseHttp = clientHttp.put(
        f"/api/file/{S_CONTAINER_ID}/%2e%2e/etc/passwd",
        json=dictPayload,
    )
    assert responseHttp.status_code in (403, 404)


# ── Files listing ─────────────────────────────────────────────


def test_list_files(clientHttp):
    _fnConnectToContainer(clientHttp)
    responseHttp = clientHttp.get(
        f"/api/files/{S_CONTAINER_ID}/workspace"
    )
    assert responseHttp.status_code == 200
    assert isinstance(responseHttp.json(), list)


def test_list_files_path_traversal_blocked(clientHttp):
    _fnConnectToContainer(clientHttp)
    responseHttp = clientHttp.get(
        f"/api/files/{S_CONTAINER_ID}/workspace/../../etc"
    )
    assert responseHttp.status_code == 403


# ── Logs ──────────────────────────────────────────────────────


def test_list_logs(clientHttp):
    _fnConnectToContainer(clientHttp)
    responseHttp = clientHttp.get(
        f"/api/logs/{S_CONTAINER_ID}"
    )
    assert responseHttp.status_code == 200
    assert isinstance(responseHttp.json(), list)


def test_get_log_not_found(clientHttp):
    _fnConnectToContainer(clientHttp)
    responseHttp = clientHttp.get(
        f"/api/logs/{S_CONTAINER_ID}/nonexistent.log"
    )
    assert responseHttp.status_code == 404


# ── Figure endpoint ───────────────────────────────────────────


def test_figure_not_found(clientHttp):
    _fnConnectToContainer(clientHttp)
    responseHttp = clientHttp.get(
        f"/api/figure/{S_CONTAINER_ID}/nonexistent.png"
    )
    assert responseHttp.status_code == 404


# ── Sync status ───────────────────────────────────────────────


def test_sync_status(clientHttp):
    _fnConnectToContainer(clientHttp)
    responseHttp = clientHttp.get(
        f"/api/sync/{S_CONTAINER_ID}/status"
    )
    assert responseHttp.status_code == 200
    assert isinstance(responseHttp.json(), dict)


def test_sync_files(clientHttp):
    _fnConnectToContainer(clientHttp)
    responseHttp = clientHttp.get(
        f"/api/sync/{S_CONTAINER_ID}/files"
    )
    assert responseHttp.status_code == 200
    assert isinstance(responseHttp.json(), list)


# ── Sync scripts ──────────────────────────────────────────────


def test_sync_scripts(clientHttp):
    _fnConnectToContainer(clientHttp)
    responseHttp = clientHttp.get(
        f"/api/sync/{S_CONTAINER_ID}/scripts"
    )
    assert responseHttp.status_code == 200
    assert isinstance(responseHttp.json(), list)


# ── Helper function tests ─────────────────────────────────────


def test_validate_path_within_root_passes():
    sResult = pipelineServer.fnValidatePathWithinRoot(
        "/workspace/dir/file.txt", "/workspace"
    )
    assert sResult == "/workspace/dir/file.txt"


def test_validate_path_within_root_blocks_traversal():
    with pytest.raises(Exception):
        pipelineServer.fnValidatePathWithinRoot(
            "/workspace/../etc/passwd", "/workspace"
        )


def test_extract_settings_defaults():
    dictResult = pipelineServer.fdictExtractSettings({})
    assert dictResult["sPlotDirectory"] == "Plot"
    assert dictResult["sFigureType"] == "pdf"
    assert dictResult["iNumberOfCores"] == -1


def test_extract_settings_custom():
    dictWorkflow = {
        "sPlotDirectory": "Figures",
        "sFigureType": "png",
        "iNumberOfCores": 8,
    }
    dictResult = pipelineServer.fdictExtractSettings(dictWorkflow)
    assert dictResult["sPlotDirectory"] == "Figures"
    assert dictResult["iNumberOfCores"] == 8


def test_filter_non_none():
    dictResult = pipelineServer.fdictFilterNonNone(
        {"a": 1, "b": None, "c": "hello"}
    )
    assert dictResult == {"a": 1, "c": "hello"}


def test_resolve_figure_path_absolute():
    sResult = pipelineServer.fsResolveFigurePath(
        "/workspace/project", "/absolute/path/fig.png"
    )
    assert sResult == "/absolute/path/fig.png"


def test_resolve_figure_path_workspace_prefix():
    sResult = pipelineServer.fsResolveFigurePath(
        "/workspace/project", "workspace/Plot/fig.png"
    )
    assert sResult == "/workspace/Plot/fig.png"


def test_resolve_figure_path_relative():
    sResult = pipelineServer.fsResolveFigurePath(
        "/workspace/project", "Plot/fig.png"
    )
    assert sResult == "/workspace/project/Plot/fig.png"


def test_sanitize_server_error_disk_full():
    sResult = pipelineServer._fsSanitizeServerError(
        "no space left on device"
    )
    assert "prune" in sResult


def test_sanitize_server_error_no_container():
    sResult = pipelineServer._fsSanitizeServerError(
        "No such container: abc123"
    )
    assert "stopped" in sResult


def test_sanitize_server_error_connection_refused():
    sResult = pipelineServer._fsSanitizeServerError(
        "Connection refused to socket"
    )
    assert "Docker" in sResult


def test_sanitize_server_error_permission_denied():
    sResult = pipelineServer._fsSanitizeServerError(
        "Permission denied on /var/run/docker.sock"
    )
    assert "Permission" in sResult


def test_sanitize_server_error_truncates_long():
    sLong = "x" * 600
    sResult = pipelineServer._fsSanitizeServerError(sLong)
    assert len(sResult) <= 504
    assert sResult.endswith("...")


def test_sanitize_server_error_passthrough():
    sResult = pipelineServer._fsSanitizeServerError("some error")
    assert sResult == "some error"


def test_extract_kill_patterns():
    dictWorkflow = {
        "listSteps": [
            {
                "saDataCommands": ["python dataGenerate.py"],
                "saPlotCommands": ["python plotResults.py"],
            },
            {
                "saDataCommands": ["cp a b", "echo done"],
                "saPlotCommands": ["vplanet input.in"],
            },
        ],
    }
    listResult = pipelineServer._flistExtractKillPatterns(
        dictWorkflow
    )
    assert "dataGenerate.py" in listResult
    assert "plotResults.py" in listResult
    assert "vplanet" in listResult
    assert "cp" not in listResult
    assert "echo" not in listResult


def test_extract_kill_patterns_empty():
    listResult = pipelineServer._flistExtractKillPatterns(
        {"listSteps": []}
    )
    assert listResult == []


def test_validate_websocket_origin_rejects_bad():
    bResult = pipelineServer.fbValidateWebSocketOrigin(
        _MockWebSocket({"origin": "http://evil.com:8080"})
    )
    assert bResult is False


def test_validate_websocket_origin_accepts_localhost():
    bResult = pipelineServer.fbValidateWebSocketOrigin(
        _MockWebSocket({"origin": "http://127.0.0.1:8080"})
    )
    assert bResult is True


def test_validate_websocket_origin_rejects_missing():
    bResult = pipelineServer.fbValidateWebSocketOrigin(
        _MockWebSocket({})
    )
    assert bResult is False


class _MockWebSocket:
    """Minimal WebSocket mock for origin validation tests."""

    def __init__(self, dictHeaders):
        self.headers = dictHeaders
