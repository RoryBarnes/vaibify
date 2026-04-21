"""Tests for file transfer endpoints, session middleware, and uncovered helpers."""

import base64
import json
import os
import pytest
from unittest.mock import patch, MagicMock

from fastapi.testclient import TestClient

from vaibify.gui import pipelineServer


S_CONTAINER_ID = "file123container"
S_WORKFLOW_PATH = "/workspace/.vaibify/workflows/test.json"

DICT_WORKFLOW = {
    "sWorkflowName": "File Test Pipeline",
    "sPlotDirectory": "Plot",
    "sFigureType": "pdf",
    "iNumberOfCores": 4,
    "listSteps": [
        {
            "sName": "Step A",
            "sDirectory": "stepA",
            "bPlotOnly": True,
            "bEnabled": True,
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


class MockDockerTransfer:
    """Mock Docker connection with file transfer support."""

    def __init__(self):
        self._dictFiles = {}

    def flistGetRunningContainers(self):
        return [{
            "sContainerId": S_CONTAINER_ID,
            "sShortId": "file12",
            "sName": "test-container",
            "sImage": "ubuntu:24.04",
        }]

    def ftResultExecuteCommand(self, sContainerId, sCommand,
                               sWorkdir=None):
        if "test -d" in sCommand and ".vaibify" in sCommand:
            return (0, "")
        if "find" in sCommand and "workflows" in sCommand:
            return (0, S_WORKFLOW_PATH + "\n")
        if "find" in sCommand:
            return (0, "")
        if "stat -c" in sCommand:
            return (0, "")
        if "test -d" in sCommand:
            return (0, "f")
        if "cat" in sCommand and "pipeline_state" in sCommand:
            return (1, "")
        if "ps aux" in sCommand:
            return (0, "0\n")
        if "printenv CONTAINER_USER" in sCommand:
            return (0, "researcher\n")
        return (0, "")

    def fbaFetchFile(self, sContainerId, sPath):
        if sPath in self._dictFiles:
            return self._dictFiles[sPath]
        if sPath.endswith(".json"):
            return json.dumps(DICT_WORKFLOW).encode("utf-8")
        raise FileNotFoundError(f"Not found: {sPath}")

    def fnWriteFile(self, sContainerId, sPath, baContent):
        self._dictFiles[sPath] = baContent

    def fsExecCreate(self, sContainerId, sCommand=None,
                     sUser=None):
        return "exec-id-mock"

    def fsocketExecStart(self, sExecId):
        return None

    def fnExecResize(self, sExecId, iRows, iColumns):
        pass


def _fmockCreateDocker():
    return MockDockerTransfer()


@pytest.fixture
def clientHttp():
    """Create a TestClient with mocked Docker."""
    with patch.object(
        pipelineServer, "_fconnectionCreateDocker",
        _fmockCreateDocker,
    ):
        app = pipelineServer.fappCreateApplication(
            sWorkspaceRoot="/workspace",
            sTerminalUserArg="testuser",
        )
    return TestClient(
        app, headers={"X-Session-Token": app.state.sSessionToken},
    )


def _fnConnectToContainer(clientHttp):
    """Connect to the test container."""
    responseHttp = clientHttp.post(
        f"/api/connect/{S_CONTAINER_ID}",
        params={"sWorkflowPath": S_WORKFLOW_PATH},
    )
    assert responseHttp.status_code == 200
    return responseHttp.json()


# ── File upload endpoint ──────────────────────────────────────


def test_file_upload_success(clientHttp):
    _fnConnectToContainer(clientHttp)
    sContent = base64.b64encode(b"hello world").decode("ascii")
    dictPayload = {
        "sFilename": "test.txt",
        "sDestination": "/workspace",
        "sContentBase64": sContent,
    }
    responseHttp = clientHttp.post(
        f"/api/files/{S_CONTAINER_ID}/upload",
        json=dictPayload,
    )
    assert responseHttp.status_code == 200
    dictResult = responseHttp.json()
    assert dictResult["bSuccess"] is True
    assert dictResult["sPath"] == "/workspace/test.txt"


def test_file_upload_path_traversal_sanitized(clientHttp):
    """Traversal in filename is stripped by basename — file lands safely."""
    _fnConnectToContainer(clientHttp)
    sContent = base64.b64encode(b"safe").decode("ascii")
    dictPayload = {
        "sFilename": "../../etc/passwd",
        "sDestination": "/workspace",
        "sContentBase64": sContent,
    }
    responseHttp = clientHttp.post(
        f"/api/files/{S_CONTAINER_ID}/upload",
        json=dictPayload,
    )
    assert responseHttp.status_code == 200
    assert responseHttp.json()["sPath"] == "/workspace/passwd"


def test_file_upload_outside_workspace_rejected(clientHttp):
    _fnConnectToContainer(clientHttp)
    sContent = base64.b64encode(b"data").decode("ascii")
    dictPayload = {
        "sFilename": "secret.txt",
        "sDestination": "/etc",
        "sContentBase64": sContent,
    }
    responseHttp = clientHttp.post(
        f"/api/files/{S_CONTAINER_ID}/upload",
        json=dictPayload,
    )
    assert responseHttp.status_code == 403


def test_file_upload_write_error(clientHttp):
    _fnConnectToContainer(clientHttp)
    sContent = base64.b64encode(b"data").decode("ascii")
    dictPayload = {
        "sFilename": "fail.txt",
        "sDestination": "/workspace",
        "sContentBase64": sContent,
    }
    with patch.object(
        MockDockerTransfer, "fnWriteFile",
        side_effect=RuntimeError("disk full"),
    ):
        responseHttp = clientHttp.post(
            f"/api/files/{S_CONTAINER_ID}/upload",
            json=dictPayload,
        )
    assert responseHttp.status_code == 500


# ── File download endpoint ────────────────────────────────────


def test_file_download_success(clientHttp):
    _fnConnectToContainer(clientHttp)
    with patch.object(
        MockDockerTransfer, "fbaFetchFile",
        return_value=b"binary-data",
    ):
        responseHttp = clientHttp.get(
            f"/api/files/{S_CONTAINER_ID}/download/"
            f"workspace/stepA/output.dat",
        )
    assert responseHttp.status_code == 200
    assert responseHttp.content == b"binary-data"
    assert "attachment" in responseHttp.headers.get(
        "content-disposition", "")


def test_file_download_path_traversal_rejected(clientHttp):
    _fnConnectToContainer(clientHttp)
    responseHttp = clientHttp.get(
        f"/api/files/{S_CONTAINER_ID}/download/"
        f"workspace/../../etc/passwd",
    )
    assert responseHttp.status_code == 403


def test_file_download_fetch_error(clientHttp):
    _fnConnectToContainer(clientHttp)
    with patch.object(
        MockDockerTransfer, "fbaFetchFile",
        side_effect=RuntimeError("not found"),
    ):
        responseHttp = clientHttp.get(
            f"/api/files/{S_CONTAINER_ID}/download/"
            f"workspace/missing.txt",
        )
    assert responseHttp.status_code == 500


# ── File pull endpoint ────────────────────────────────────────


def test_file_pull_success(clientHttp):
    _fnConnectToContainer(clientHttp)
    sHomeDest = os.path.expanduser("~/Downloads/data.npy")
    dictPayload = {
        "sContainerPath": "/workspace/data.npy",
        "sHostDestination": "~/Downloads/data.npy",
    }
    with patch.object(
        pipelineServer, "_fnDockerCopy",
    ) as mockCopy:
        responseHttp = clientHttp.post(
            f"/api/files/{S_CONTAINER_ID}/pull",
            json=dictPayload,
        )
    assert responseHttp.status_code == 200
    dictResult = responseHttp.json()
    assert dictResult["bSuccess"] is True
    assert dictResult["sHostPath"] == sHomeDest
    mockCopy.assert_called_once()


def test_file_pull_path_traversal_rejected(clientHttp):
    _fnConnectToContainer(clientHttp)
    dictPayload = {
        "sContainerPath": "/etc/passwd",
        "sHostDestination": "/tmp/stolen",
    }
    responseHttp = clientHttp.post(
        f"/api/files/{S_CONTAINER_ID}/pull",
        json=dictPayload,
    )
    assert responseHttp.status_code == 403


def test_file_pull_docker_copy_error(clientHttp):
    _fnConnectToContainer(clientHttp)
    dictPayload = {
        "sContainerPath": "/workspace/missing.npy",
        "sHostDestination": "~/Downloads/missing.npy",
    }
    with patch.object(
        pipelineServer, "_fnDockerCopy",
        side_effect=RuntimeError("copy failed"),
    ):
        responseHttp = clientHttp.post(
            f"/api/files/{S_CONTAINER_ID}/pull",
            json=dictPayload,
        )
    assert responseHttp.status_code == 500


def test_file_pull_tilde_expansion(clientHttp):
    _fnConnectToContainer(clientHttp)
    dictPayload = {
        "sContainerPath": "/workspace/data.npy",
        "sHostDestination": "~/Downloads/data.npy",
    }
    with patch.object(
        pipelineServer, "_fnDockerCopy",
    ) as mockCopy:
        responseHttp = clientHttp.post(
            f"/api/files/{S_CONTAINER_ID}/pull",
            json=dictPayload,
        )
    assert responseHttp.status_code == 200
    dictResult = responseHttp.json()
    assert "~" not in dictResult["sHostPath"]


def test_file_pull_outside_home_rejected(clientHttp):
    _fnConnectToContainer(clientHttp)
    dictPayload = {
        "sContainerPath": "/workspace/data.npy",
        "sHostDestination": "/tmp/data.npy",
    }
    responseHttp = clientHttp.post(
        f"/api/files/{S_CONTAINER_ID}/pull",
        json=dictPayload,
    )
    assert responseHttp.status_code == 403


# ── _fnDockerCopy ─────────────────────────────────────────────


def test_fnDockerCopy_calls_subprocess():
    with patch("subprocess.run") as mockRun:
        pipelineServer._fnDockerCopy(
            "cid123", "/workspace/file.txt", "/tmp/file.txt"
        )
    mockRun.assert_called_once_with(
        ["docker", "cp", "cid123:/workspace/file.txt", "/tmp/file.txt"],
        check=True, capture_output=True,
    )


def test_fnDockerCopy_raises_on_failure():
    import subprocess
    with patch("subprocess.run",
               side_effect=subprocess.CalledProcessError(1, "docker")):
        with pytest.raises(subprocess.CalledProcessError):
            pipelineServer._fnDockerCopy(
                "cid123", "/workspace/x.txt", "/tmp/x.txt"
            )


# ── SessionTokenMiddleware query param fallback ──────────────


def test_session_token_via_query_param_on_download(clientHttp):
    """Query-param tokens only accepted for download and WebSocket."""
    _fnConnectToContainer(clientHttp)
    sToken = clientHttp.app.state.sSessionToken
    clientNoHeader = TestClient(clientHttp.app)
    responseHttp = clientNoHeader.get(
        f"/api/files/{S_CONTAINER_ID}/download/"
        f"workspace/stepA/output.dat?sToken={sToken}",
    )
    assert responseHttp.status_code in (200, 500)


def test_session_token_query_param_rejected_non_download(clientHttp):
    """Query-param tokens rejected on regular API endpoints."""
    sToken = clientHttp.app.state.sSessionToken
    clientNoHeader = TestClient(clientHttp.app)
    responseHttp = clientNoHeader.get(
        f"/api/user?sToken={sToken}",
    )
    assert responseHttp.status_code == 401


def test_session_token_missing_rejected():
    with patch.object(
        pipelineServer, "_fconnectionCreateDocker",
        _fmockCreateDocker,
    ):
        app = pipelineServer.fappCreateApplication(
            sWorkspaceRoot="/workspace",
        )
    clientNoAuth = TestClient(app)
    responseHttp = clientNoAuth.get("/api/user")
    assert responseHttp.status_code == 401


def test_session_token_wrong_rejected(clientHttp):
    clientBadToken = TestClient(
        clientHttp.app,
        headers={"X-Session-Token": "wrong-token-value"},
    )
    responseHttp = clientBadToken.get("/api/user")
    assert responseHttp.status_code == 401


def test_session_token_endpoint_exempt(clientHttp):
    clientNoAuth = TestClient(clientHttp.app)
    responseHttp = clientNoAuth.get("/api/session-token")
    assert responseHttp.status_code == 200


# ── Hub application creation ─────────────────────────────────


def test_hub_application_creates():
    with patch.object(
        pipelineServer, "_fconnectionCreateDocker",
        _fmockCreateDocker,
    ):
        app = pipelineServer.fappCreateHubApplication()
    assert app.title == "Vaibify Hub"
    assert hasattr(app.state, "sSessionToken")
    assert hasattr(app.state, "setAllowedContainers")


# ── Connect without workflow (no-workflow mode) ───────────────


def test_connect_without_workflow(clientHttp):
    responseHttp = clientHttp.post(
        f"/api/connect/{S_CONTAINER_ID}",
    )
    assert responseHttp.status_code == 200
    dictResult = responseHttp.json()
    assert dictResult["sWorkflowPath"] is None
    assert dictResult["dictWorkflow"] is None


# ── Pure helper functions ─────────────────────────────────────


def test_flistParseDirectoryOutput_normal():
    sOutput = "f /workspace/file.txt\nd /workspace/subdir\n"
    listResult = pipelineServer._flistParseDirectoryOutput(sOutput)
    assert len(listResult) == 2
    assert listResult[0]["sName"] == "file.txt"
    assert listResult[0]["bIsDirectory"] is False
    assert listResult[1]["sName"] == "subdir"
    assert listResult[1]["bIsDirectory"] is True


def test_flistParseDirectoryOutput_empty():
    listResult = pipelineServer._flistParseDirectoryOutput("")
    assert listResult == []


def test_flistParseDirectoryOutput_short_lines():
    sOutput = "x\n\n  \nf /workspace/ok.py\n"
    listResult = pipelineServer._flistParseDirectoryOutput(sOutput)
    assert len(listResult) == 1
    assert listResult[0]["sName"] == "ok.py"


def test_fsResolveFigurePath_absolute():
    sResult = pipelineServer.fsResolveFigurePath(
        "/workspace/step1", "/workspace/other/fig.pdf"
    )
    assert sResult == "/workspace/other/fig.pdf"


def test_fsResolveFigurePath_workspace_prefix():
    sResult = pipelineServer.fsResolveFigurePath(
        "/workspace/step1", "workspace/images/fig.png"
    )
    assert sResult == "/workspace/images/fig.png"


def test_fsResolveFigurePath_relative():
    sResult = pipelineServer.fsResolveFigurePath(
        "/workspace/step1", "Plot/fig.pdf"
    )
    assert sResult == "/workspace/step1/Plot/fig.pdf"


def test_fnValidatePathWithinRoot_valid():
    sResult = pipelineServer.fnValidatePathWithinRoot(
        "/workspace/step1/data.npy", "/workspace"
    )
    assert sResult == "/workspace/step1/data.npy"


def test_fnValidatePathWithinRoot_root_exact():
    sResult = pipelineServer.fnValidatePathWithinRoot(
        "/workspace", "/workspace"
    )
    assert sResult == "/workspace"


def test_fnValidatePathWithinRoot_traversal():
    with pytest.raises(Exception) as excInfo:
        pipelineServer.fnValidatePathWithinRoot(
            "/workspace/../etc/passwd", "/workspace"
        )
    assert excInfo.value.status_code == 403


def test_fnValidatePathWithinRoot_outside():
    with pytest.raises(Exception) as excInfo:
        pipelineServer.fnValidatePathWithinRoot(
            "/etc/hosts", "/workspace"
        )
    assert excInfo.value.status_code == 403


def test_flistExtractKillPatterns_python_commands():
    dictWorkflow = {
        "listSteps": [
            {
                "saDataCommands": ["python generate.py"],
                "saPlotCommands": ["python3 plotFigures.py"],
            },
        ],
    }
    listPatterns = pipelineServer._flistExtractKillPatterns(
        dictWorkflow
    )
    assert "generate.py" in listPatterns
    assert "plotFigures.py" in listPatterns


def test_flistExtractKillPatterns_non_python():
    dictWorkflow = {
        "listSteps": [
            {
                "saDataCommands": ["vplanet vpl.in"],
                "saPlotCommands": [],
            },
        ],
    }
    listPatterns = pipelineServer._flistExtractKillPatterns(
        dictWorkflow
    )
    assert "vplanet" in listPatterns


def test_flistExtractKillPatterns_skips_builtins():
    dictWorkflow = {
        "listSteps": [
            {
                "saDataCommands": ["cp src dest", "mkdir -p dir"],
                "saPlotCommands": ["echo done"],
            },
        ],
    }
    listPatterns = pipelineServer._flistExtractKillPatterns(
        dictWorkflow
    )
    assert listPatterns == []


def test_flistExtractKillPatterns_empty_workflow():
    listPatterns = pipelineServer._flistExtractKillPatterns(
        {"listSteps": []}
    )
    assert listPatterns == []


def test_flistExtractKillPatterns_empty_command():
    dictWorkflow = {
        "listSteps": [
            {
                "saDataCommands": ["", "  "],
                "saPlotCommands": [],
            },
        ],
    }
    listPatterns = pipelineServer._flistExtractKillPatterns(
        dictWorkflow
    )
    assert listPatterns == []


# ── fnHandleTerminalText ──────────────────────────────────────


def test_fnHandleTerminalText_resize():
    mockSession = MagicMock()
    sMessage = json.dumps(
        {"sType": "resize", "iRows": 30, "iColumns": 120}
    )
    pipelineServer._fnHandleTerminalText(mockSession, sMessage)
    mockSession.fnResize.assert_called_once_with(30, 120)


def test_fnHandleTerminalText_kill():
    mockSession = MagicMock()
    sMessage = json.dumps({"sType": "kill"})
    pipelineServer._fnHandleTerminalText(mockSession, sMessage)
    mockSession.fnKillForeground.assert_called_once()


def test_fnHandleTerminalText_invalid_json():
    mockSession = MagicMock()
    pipelineServer._fnHandleTerminalText(mockSession, "not-json")
    mockSession.fnResize.assert_not_called()
    mockSession.fnKillForeground.assert_not_called()


def test_fnHandleTerminalText_resize_clamped():
    mockSession = MagicMock()
    sMessage = json.dumps(
        {"sType": "resize", "iRows": 9999, "iColumns": -5}
    )
    pipelineServer._fnHandleTerminalText(mockSession, sMessage)
    mockSession.fnResize.assert_called_once_with(500, 1)


# ── Invalidation helpers ─────────────────────────────────────


def test_fnInvalidateStepFiles_marks_modified():
    dictStep = {
        "dictVerification": {"sUnitTest": "passed"},
        "saDataFiles": ["a.npy"],
    }
    pipelineServer._fnInvalidateStepFiles(
        dictStep, ["/workspace/a.npy"]
    )
    assert dictStep["dictVerification"]["sUnitTest"] == "untested"
    assert "/workspace/a.npy" in (
        dictStep["dictVerification"]["listModifiedFiles"]
    )


def test_fnInvalidateStepFiles_preserves_existing():
    dictStep = {
        "dictVerification": {
            "sUnitTest": "untested",
            "listModifiedFiles": ["/workspace/b.npy"],
        },
    }
    pipelineServer._fnInvalidateStepFiles(
        dictStep, ["/workspace/a.npy"]
    )
    listModified = dictStep["dictVerification"]["listModifiedFiles"]
    assert "/workspace/a.npy" in listModified
    assert "/workspace/b.npy" in listModified


def test_fnInvalidateDownstreamStep_marks_upstream():
    dictStep = {
        "dictVerification": {"sUnitTest": "passed"},
    }
    pipelineServer._fnInvalidateDownstreamStep(dictStep)
    assert dictStep["dictVerification"]["sUnitTest"] == "untested"
    assert dictStep["dictVerification"]["bUpstreamModified"] is True


def test_fdictFindChangedFiles_detects_changes():
    dictPathsByStep = {
        0: ["step1/data.npy"],
        1: ["step2/result.dat"],
    }
    dictOldModTimes = {
        "step1/data.npy": "1700000000",
        "step2/result.dat": "1700000000",
    }
    dictNewModTimes = {
        "step1/data.npy": "1700000001",
        "step2/result.dat": "1700000000",
    }
    dictChanged = pipelineServer._fdictFindChangedFiles(
        dictPathsByStep, dictOldModTimes, dictNewModTimes
    )
    assert 0 in dictChanged
    assert "step1/data.npy" in dictChanged[0]
    assert 1 not in dictChanged


def test_fdictFindChangedFiles_no_changes():
    dictPathsByStep = {
        0: ["/workspace/data.npy"],
    }
    dictModTimes = {"/workspace/data.npy": "1700000000"}
    dictChanged = pipelineServer._fdictFindChangedFiles(
        dictPathsByStep, dictModTimes, dictModTimes
    )
    assert dictChanged == {}


# ── Sleep warning helper ─────────────────────────────────────


@pytest.fixture(autouse=True)
def _fnNoCaffeinate(request, monkeypatch):
    """Force caffeinate-not-running for sleep warning tests."""
    if "SleepWarning" not in request.node.name:
        return
    monkeypatch.setattr(
        pipelineServer, "_fbCaffeinateRunning", lambda: False,
    )


def test_fdictSleepWarningForContext_colima():
    dictResult = pipelineServer._fdictSleepWarningForContext("colima")
    assert dictResult["sRuntime"] == "colima"
    assert "Colima" in dictResult["sSleepWarning"]


def test_fdictSleepWarningForContext_orbstack():
    dictResult = pipelineServer._fdictSleepWarningForContext(
        "orbstack"
    )
    assert dictResult["sRuntime"] == "orbstack"
    assert "OrbStack" in dictResult["sSleepWarning"]


def test_fdictSleepWarningForContext_desktop():
    dictResult = pipelineServer._fdictSleepWarningForContext("desktop")
    assert dictResult["sRuntime"] == "desktop"
    assert "Docker Desktop" in dictResult["sSleepWarning"]


def test_fdictSleepWarningForContext_default():
    dictResult = pipelineServer._fdictSleepWarningForContext("default")
    assert dictResult["sRuntime"] == "desktop"


def test_fdictSleepWarningForContext_unknown():
    dictResult = pipelineServer._fdictSleepWarningForContext(
        "podman-remote"
    )
    assert dictResult["sRuntime"] == "podman-remote"
    assert "caffeinate" in dictResult["sSleepWarning"]


# ── _fnUpdateAggregateTestState ──────────────────────────────


def test_fnUpdateAggregateTestState_all_passed():
    dictStep = {
        "dictTests": {
            "dictIntegrity": {"saCommands": ["pytest"]},
            "dictQualitative": {"saCommands": ["pytest"]},
        },
        "dictVerification": {
            "sIntegrity": "passed",
            "sQualitative": "passed",
        },
    }
    pipelineServer._fnUpdateAggregateTestState(dictStep)
    assert dictStep["dictVerification"]["sUnitTest"] == "passed"


def test_fnUpdateAggregateTestState_one_failed():
    dictStep = {
        "dictTests": {
            "dictIntegrity": {"saCommands": ["pytest"]},
            "dictQualitative": {"saCommands": ["pytest"]},
        },
        "dictVerification": {
            "sIntegrity": "passed",
            "sQualitative": "failed",
        },
    }
    pipelineServer._fnUpdateAggregateTestState(dictStep)
    assert dictStep["dictVerification"]["sUnitTest"] == "failed"


def test_fnUpdateAggregateTestState_no_commands():
    dictStep = {
        "dictTests": {},
        "dictVerification": {},
    }
    pipelineServer._fnUpdateAggregateTestState(dictStep)
    assert dictStep["dictVerification"].get("sUnitTest") != "passed"


# ── _fbPipelineIsRunning ─────────────────────────────────────


def test_fbPipelineIsRunning_no_state():
    mockDocker = MagicMock()
    dictCtx = {"docker": mockDocker}
    with patch(
        "vaibify.gui.pipelineState.fdictReadState",
        return_value=None,
    ):
        bResult = pipelineServer._fbPipelineIsRunning(
            dictCtx, "cid"
        )
    assert bResult is False


def test_fbPipelineIsRunning_running():
    mockDocker = MagicMock()
    dictCtx = {"docker": mockDocker}
    with patch(
        "vaibify.gui.pipelineState.fdictReadState",
        return_value={"bRunning": True},
    ):
        bResult = pipelineServer._fbPipelineIsRunning(
            dictCtx, "cid"
        )
    assert bResult is True


# ── _fsResolveContainerUser ──────────────────────────────────


def test_fsResolveContainerUser_found():
    mockDocker = MagicMock()
    mockDocker.ftResultExecuteCommand.return_value = (
        0, "developer\n"
    )
    dictCtx = {"docker": mockDocker}
    sUser = pipelineServer._fsResolveContainerUser(
        dictCtx, "cid"
    )
    assert sUser == "developer"


def test_fsResolveContainerUser_fallback():
    mockDocker = MagicMock()
    mockDocker.ftResultExecuteCommand.return_value = (1, "")
    dictCtx = {"docker": mockDocker}
    sUser = pipelineServer._fsResolveContainerUser(
        dictCtx, "cid"
    )
    assert sUser == "researcher"


def test_fsResolveContainerUser_exception():
    mockDocker = MagicMock()
    mockDocker.ftResultExecuteCommand.side_effect = (
        RuntimeError("fail")
    )
    dictCtx = {"docker": mockDocker}
    sUser = pipelineServer._fsResolveContainerUser(
        dictCtx, "cid"
    )
    assert sUser == "researcher"


# ── fbaFetchFigureWithFallback ────────────────────────────────


def test_fbaFetchFigureWithFallback_primary_success():
    mockDocker = MagicMock()
    mockDocker.fbaFetchFile.return_value = b"png-data"
    baResult = pipelineServer.fbaFetchFigureWithFallback(
        mockDocker, "cid", "/workspace/fig.png",
        "/workspace", "", "fig.png",
    )
    assert baResult == b"png-data"


def test_fbaFetchFigureWithFallback_fallback_absolute_workdir():
    mockDocker = MagicMock()
    mockDocker.fbaFetchFile.side_effect = [
        FileNotFoundError("miss"),
        b"fallback-data",
    ]
    baResult = pipelineServer.fbaFetchFigureWithFallback(
        mockDocker, "cid", "/workspace/fig.png",
        "/workspace", "/workspace/build", "fig.png",
    )
    assert baResult == b"fallback-data"


def test_fbaFetchFigureWithFallback_fallback_relative_workdir():
    mockDocker = MagicMock()
    mockDocker.fbaFetchFile.side_effect = [
        FileNotFoundError("miss"),
        b"fallback-relative",
    ]
    baResult = pipelineServer.fbaFetchFigureWithFallback(
        mockDocker, "cid", "step1/fig.png",
        "/workspace/step1", "build", "fig.png",
    )
    assert baResult == b"fallback-relative"


def test_fbaFetchFigureWithFallback_no_workdir_raises():
    mockDocker = MagicMock()
    mockDocker.fbaFetchFile.side_effect = FileNotFoundError("miss")
    with pytest.raises(Exception) as excInfo:
        pipelineServer.fbaFetchFigureWithFallback(
            mockDocker, "cid", "/workspace/fig.png",
            "/workspace", "", "fig.png",
        )
    assert excInfo.value.status_code == 404


# ── fsResolveWorkflowPath ────────────────────────────────────


def test_fsResolveWorkflowPath_explicit():
    sResult = pipelineServer.fsResolveWorkflowPath(
        None, "cid", "/workspace/.vaibify/wf.json"
    )
    assert sResult == "/workspace/.vaibify/wf.json"


def test_fsResolveWorkflowPath_discovery_found():
    mockDocker = MagicMock()
    with patch.object(
        pipelineServer.workflowManager,
        "flistFindWorkflowsInContainer",
        return_value=[{"sPath": "/workspace/.vaibify/found.json"}],
    ):
        sResult = pipelineServer.fsResolveWorkflowPath(
            mockDocker, "cid", None
        )
    assert sResult == "/workspace/.vaibify/found.json"


def test_fsResolveWorkflowPath_discovery_empty():
    mockDocker = MagicMock()
    with patch.object(
        pipelineServer.workflowManager,
        "flistFindWorkflowsInContainer",
        return_value=[],
    ):
        sResult = pipelineServer.fsResolveWorkflowPath(
            mockDocker, "cid", None
        )
    assert sResult is None


# ── fdictExtractSettings ──────────────────────────────────────


def test_fdictExtractSettings_defaults():
    dictResult = pipelineServer.fdictExtractSettings({})
    assert dictResult["sPlotDirectory"] == "Plot"
    assert dictResult["sFigureType"] == "pdf"


def test_fdictExtractSettings_custom():
    dictWorkflow = {
        "sPlotDirectory": "Figures",
        "sFigureType": "png",
        "iNumberOfCores": 8,
    }
    dictResult = pipelineServer.fdictExtractSettings(dictWorkflow)
    assert dictResult["sPlotDirectory"] == "Figures"
    assert dictResult["sFigureType"] == "png"
    assert dictResult["iNumberOfCores"] == 8


# ── fdictRequireWorkflow ──────────────────────────────────────


def test_fdictRequireWorkflow_found():
    dictCache = {"cid": {"sWorkflowName": "Test"}}
    dictResult = pipelineServer.fdictRequireWorkflow(
        dictCache, "cid"
    )
    assert dictResult["sWorkflowName"] == "Test"


def test_fdictRequireWorkflow_missing():
    with pytest.raises(Exception) as excInfo:
        pipelineServer.fdictRequireWorkflow({}, "cid")
    assert excInfo.value.status_code == 404
