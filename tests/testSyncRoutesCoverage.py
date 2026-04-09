"""Tests targeting uncovered lines in vaibify.gui.routes.syncRoutes.

Covers:
- Line 60: Overleaf push returns failure result
- Line 85: Zenodo archive returns failure result
- Line 113: GitHub push returns failure result
- Lines 184-191: Setup connection with token that fails to store
- Lines 200-206: Setup connection for Zenodo with invalid token
- Line 237: DAG endpoint returns 500 on failure
- Lines 250-265: DAG export endpoint (new)
- Lines 284-294: Dataset download endpoint
"""

import json
import pytest
from unittest.mock import patch, MagicMock

from fastapi.testclient import TestClient

from vaibify.gui import pipelineServer


S_CONTAINER_ID = "sync_test_cid"
S_WORKFLOW_PATH = "/workspace/.vaibify/workflows/test.json"

DICT_WORKFLOW_SYNC = {
    "sWorkflowName": "Sync Test Pipeline",
    "sPlotDirectory": "Plot",
    "sFigureType": "pdf",
    "iNumberOfCores": 2,
    "sOverleafProjectId": "abc123proj",
    "sOverleafFigureDirectory": "figures",
    "sGithubBaseUrl": "",
    "sZenodoDoi": "",
    "sTexFilename": "main.tex",
    "listSteps": [
        {
            "sName": "Generate Data",
            "sDirectory": "/workspace/step01",
            "bPlotOnly": False,
            "bEnabled": True,
            "bInteractive": False,
            "saDataCommands": ["python run.py"],
            "saDataFiles": ["output.dat"],
            "saTestCommands": [],
            "saPlotCommands": ["python plot.py"],
            "saPlotFiles": ["{sPlotDirectory}/fig.{sFigureType}"],
            "saDependencies": [],
            "dictRunStats": {},
            "dictVerification": {
                "sUnitTest": "untested",
                "sUser": "untested",
            },
        },
    ],
}


class MockDockerSync:
    """Docker mock that simulates sync command results."""

    def __init__(self):
        self._dictFiles = {}
        self._iSyncExitCode = 0
        self._sSyncOutput = "ok"

    def flistGetRunningContainers(self):
        return [
            {
                "sContainerId": S_CONTAINER_ID,
                "sShortId": "sync01",
                "sName": "sync-container",
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
        if "cat" in sCommand and "pipeline_state" in sCommand:
            return (1, "")
        if "stat -c" in sCommand:
            return (0, "")
        if "ps aux" in sCommand:
            return (0, "0\n")
        if "python3 -c" in sCommand and "hashlib" in sCommand:
            return (0, "")
        if "which claude" in sCommand:
            return (1, "")
        if "test -f" in sCommand:
            return (0, "")
        return (self._iSyncExitCode, self._sSyncOutput)

    def fbaFetchFile(self, sContainerId, sPath):
        if sPath in self._dictFiles:
            return self._dictFiles[sPath]
        if sPath.endswith(".json"):
            return json.dumps(DICT_WORKFLOW_SYNC).encode("utf-8")
        if sPath.endswith(".svg"):
            return b"<svg>dag</svg>"
        if sPath.endswith(".png"):
            return b"\x89PNG"
        if sPath.endswith(".pdf"):
            return b"%PDF-1.4"
        raise FileNotFoundError(f"Not found: {sPath}")

    def fnWriteFile(self, sContainerId, sPath, baContent):
        self._dictFiles[sPath] = baContent

    def fsExecCreate(self, sContainerId, sCommand=None, sUser=None):
        return "exec-id-sync"

    def fsocketExecStart(self, sExecId):
        return None

    def fnExecResize(self, sExecId, iRows, iColumns):
        pass


_mockDockerInstance = None


def _fmockCreateDockerSync():
    global _mockDockerInstance
    _mockDockerInstance = MockDockerSync()
    return _mockDockerInstance


def _fnConnectToContainer(clientHttp):
    """POST to /api/connect and return the response dict."""
    responseHttp = clientHttp.post(
        f"/api/connect/{S_CONTAINER_ID}",
        params={"sWorkflowPath": S_WORKFLOW_PATH},
    )
    assert responseHttp.status_code == 200
    return responseHttp.json()


@pytest.fixture
def clientHttp():
    """Create a TestClient with mocked Docker for sync testing."""
    with patch.object(
        pipelineServer, "_fconnectionCreateDocker",
        _fmockCreateDockerSync,
    ):
        app = pipelineServer.fappCreateApplication(
            sWorkspaceRoot="/workspace",
            sTerminalUserArg="testuser",
        )
    return TestClient(
        app, headers={"X-Session-Token": app.state.sSessionToken},
    )


# ── Line 60: Overleaf push failure ──────────────────────────────


def test_overleaf_push_failure_returns_error(clientHttp):
    """When push fails, route returns error dict without saving."""
    _fnConnectToContainer(clientHttp)
    _mockDockerInstance._iSyncExitCode = 1
    _mockDockerInstance._sSyncOutput = "authentication failed"
    responseHttp = clientHttp.post(
        f"/api/overleaf/{S_CONTAINER_ID}/push",
        json={
            "listFilePaths": ["/workspace/Plot/fig.pdf"],
            "sCommitMessage": "push figs",
        },
    )
    assert responseHttp.status_code == 200
    dictResult = responseHttp.json()
    assert dictResult["bSuccess"] is False
    assert dictResult["sErrorType"] == "auth"


def test_overleaf_push_success(clientHttp):
    """Successful push returns bSuccess True."""
    _fnConnectToContainer(clientHttp)
    _mockDockerInstance._iSyncExitCode = 0
    _mockDockerInstance._sSyncOutput = "pushed"
    responseHttp = clientHttp.post(
        f"/api/overleaf/{S_CONTAINER_ID}/push",
        json={
            "listFilePaths": ["/workspace/Plot/fig.pdf"],
            "sCommitMessage": "push figs",
        },
    )
    assert responseHttp.status_code == 200
    dictResult = responseHttp.json()
    assert dictResult["bSuccess"] is True


# ── Line 85: Zenodo archive failure ─────────────────────────────


def test_zenodo_archive_failure_returns_error(clientHttp):
    """When archive fails, route returns error dict."""
    _fnConnectToContainer(clientHttp)
    _mockDockerInstance._iSyncExitCode = 1
    _mockDockerInstance._sSyncOutput = "rate limit exceeded"
    responseHttp = clientHttp.post(
        f"/api/zenodo/{S_CONTAINER_ID}/archive",
        json={
            "listFilePaths": ["/workspace/data.h5"],
            "sCommitMessage": "archive",
        },
    )
    assert responseHttp.status_code == 200
    dictResult = responseHttp.json()
    assert dictResult["bSuccess"] is False
    assert dictResult["sErrorType"] == "rateLimit"


# ── Line 113: GitHub push failure ────────────────────────────────


def test_github_push_failure_returns_error(clientHttp):
    """When GitHub push fails, route returns error dict."""
    _fnConnectToContainer(clientHttp)
    _mockDockerInstance._iSyncExitCode = 1
    _mockDockerInstance._sSyncOutput = "not found repo"
    responseHttp = clientHttp.post(
        f"/api/github/{S_CONTAINER_ID}/push",
        json={
            "listFilePaths": ["/workspace/run.py"],
            "sCommitMessage": "push code",
        },
    )
    assert responseHttp.status_code == 200
    dictResult = responseHttp.json()
    assert dictResult["bSuccess"] is False


def test_github_push_success_includes_commit_hash(clientHttp):
    """Successful push returns sCommitHash."""
    _fnConnectToContainer(clientHttp)
    _mockDockerInstance._iSyncExitCode = 0
    _mockDockerInstance._sSyncOutput = "abc1234"
    responseHttp = clientHttp.post(
        f"/api/github/{S_CONTAINER_ID}/push",
        json={
            "listFilePaths": ["/workspace/run.py"],
            "sCommitMessage": "push code",
        },
    )
    assert responseHttp.status_code == 200
    dictResult = responseHttp.json()
    assert dictResult["bSuccess"] is True
    assert dictResult["sCommitHash"] == "abc1234"


# ── Lines 184-191: Setup connection token store failure ──────────


def test_setup_connection_token_store_failure(clientHttp):
    """When credential storage raises, return bConnected=False."""
    _fnConnectToContainer(clientHttp)
    with patch(
        "vaibify.gui.syncDispatcher.fnStoreCredentialInContainer",
        side_effect=RuntimeError("keyring unavailable"),
    ):
        responseHttp = clientHttp.post(
            f"/api/sync/{S_CONTAINER_ID}/setup",
            json={
                "sService": "overleaf",
                "sToken": "secret_token_value",
            },
        )
    assert responseHttp.status_code == 200
    dictResult = responseHttp.json()
    assert dictResult["bConnected"] is False
    assert "Failed to store" in dictResult["sMessage"]


# ── Lines 200-206: Zenodo setup with token validation failure ────


def test_setup_zenodo_validation_fails(clientHttp):
    """Zenodo token stored but validation call fails."""
    _fnConnectToContainer(clientHttp)
    _mockDockerInstance._iSyncExitCode = 0
    _mockDockerInstance._sSyncOutput = "ok"
    with patch(
        "vaibify.gui.syncDispatcher.fbValidateZenodoToken",
        return_value=False,
    ):
        responseHttp = clientHttp.post(
            f"/api/sync/{S_CONTAINER_ID}/setup",
            json={
                "sService": "zenodo",
                "sToken": "my_zenodo_token",
            },
        )
    assert responseHttp.status_code == 200
    dictResult = responseHttp.json()
    assert dictResult["bConnected"] is False
    assert "validation failed" in dictResult["sMessage"]


# ── Line 237: DAG endpoint failure returns 500 ──────────────────


def test_dag_endpoint_failure_returns_500(clientHttp):
    """When DAG generation fails, return HTTP 500."""
    _fnConnectToContainer(clientHttp)
    _mockDockerInstance._iSyncExitCode = 1
    _mockDockerInstance._sSyncOutput = "dot not installed"
    responseHttp = clientHttp.get(
        f"/api/workflow/{S_CONTAINER_ID}/dag"
    )
    assert responseHttp.status_code == 500


def test_dag_endpoint_success_returns_svg(clientHttp):
    """Successful DAG returns SVG content."""
    _fnConnectToContainer(clientHttp)
    _mockDockerInstance._iSyncExitCode = 0
    _mockDockerInstance._sSyncOutput = ""
    responseHttp = clientHttp.get(
        f"/api/workflow/{S_CONTAINER_ID}/dag"
    )
    assert responseHttp.status_code == 200
    assert "svg" in responseHttp.headers.get("content-type", "")


# ── Lines 250-265: DAG export endpoint (new) ────────────────────


def test_dag_export_svg_success(clientHttp):
    """DAG export with svg format returns SVG with attachment header."""
    _fnConnectToContainer(clientHttp)
    _mockDockerInstance._iSyncExitCode = 0
    _mockDockerInstance._sSyncOutput = ""
    responseHttp = clientHttp.get(
        f"/api/workflow/{S_CONTAINER_ID}/dag/export",
        params={"sFormat": "svg"},
    )
    assert responseHttp.status_code == 200
    assert "svg" in responseHttp.headers.get("content-type", "")
    assert "dag.svg" in responseHttp.headers.get(
        "content-disposition", "")


def test_dag_export_png_success(clientHttp):
    """DAG export with png format returns PNG."""
    _fnConnectToContainer(clientHttp)
    _mockDockerInstance._iSyncExitCode = 0
    _mockDockerInstance._sSyncOutput = ""
    responseHttp = clientHttp.get(
        f"/api/workflow/{S_CONTAINER_ID}/dag/export",
        params={"sFormat": "png"},
    )
    assert responseHttp.status_code == 200
    assert "dag.png" in responseHttp.headers.get(
        "content-disposition", "")


def test_dag_export_failure_returns_500(clientHttp):
    """When DAG export fails, return HTTP 500."""
    _fnConnectToContainer(clientHttp)
    _mockDockerInstance._iSyncExitCode = 1
    _mockDockerInstance._sSyncOutput = "graphviz missing"
    responseHttp = clientHttp.get(
        f"/api/workflow/{S_CONTAINER_ID}/dag/export",
        params={"sFormat": "svg"},
    )
    assert responseHttp.status_code == 500


def test_dag_export_unsupported_format_returns_500(clientHttp):
    """Unsupported format triggers non-zero exit from dispatcher."""
    _fnConnectToContainer(clientHttp)
    responseHttp = clientHttp.get(
        f"/api/workflow/{S_CONTAINER_ID}/dag/export",
        params={"sFormat": "bmp"},
    )
    assert responseHttp.status_code == 500


# ── Lines 284-294: Dataset download endpoint ─────────────────────


def test_dataset_download_success(clientHttp):
    """Successful download returns bSuccess True."""
    _fnConnectToContainer(clientHttp)
    with patch(
        "vaibify.gui.syncDispatcher.ftResultDownloadDataset",
        return_value=(0, "downloaded"),
        create=True,
    ):
        responseHttp = clientHttp.post(
            f"/api/zenodo/{S_CONTAINER_ID}/download",
            json={
                "iRecordId": 12345,
                "sFileName": "data.h5",
                "sDestination": "/workspace/data",
            },
        )
    assert responseHttp.status_code == 200
    dictResult = responseHttp.json()
    assert dictResult["bSuccess"] is True


def test_dataset_download_failure_returns_500(clientHttp):
    """Failed download returns HTTP 500."""
    _fnConnectToContainer(clientHttp)
    with patch(
        "vaibify.gui.syncDispatcher.ftResultDownloadDataset",
        return_value=(1, "connection refused"),
        create=True,
    ):
        responseHttp = clientHttp.post(
            f"/api/zenodo/{S_CONTAINER_ID}/download",
            json={
                "iRecordId": 12345,
                "sFileName": "data.h5",
                "sDestination": "/workspace/data",
            },
        )
    assert responseHttp.status_code == 500
