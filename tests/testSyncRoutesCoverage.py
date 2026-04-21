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
            "sDirectory": "step01",
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
    with patch(
        "vaibify.gui.syncDispatcher._fsFetchOverleafToken",
        return_value="test-tok",
    ):
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
    with patch(
        "vaibify.gui.syncDispatcher._fsFetchOverleafToken",
        return_value="test-tok",
    ):
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
        "vaibify.config.secretManager.fnStoreSecret",
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


# ── Overleaf setup: store + validate + cleanup on failure ───────


def test_setup_overleaf_validation_passes(clientHttp):
    """Valid Overleaf token: bConnected True, no cleanup call."""
    _fnConnectToContainer(clientHttp)
    _mockDockerInstance._iSyncExitCode = 0
    _mockDockerInstance._sSyncOutput = "ok"
    with patch(
        "vaibify.gui.syncDispatcher.fdictCheckConnectivity",
        return_value={"bConnected": True, "sMessage": "Connected"},
    ), patch(
        "vaibify.config.secretManager.fnStoreSecret",
        return_value=None,
    ) as mockStore, patch(
        "vaibify.gui.syncDispatcher.fbValidateOverleafCredentials",
        return_value=(True, ""),
    ), patch(
        "vaibify.config.secretManager.fnDeleteSecret",
        return_value=None,
    ) as mockDelete:
        responseHttp = clientHttp.post(
            f"/api/sync/{S_CONTAINER_ID}/setup",
            json={
                "sService": "overleaf",
                "sProjectId": "abc123proj",
                "sToken": "valid_git_token",
            },
        )
    assert responseHttp.status_code == 200
    dictResult = responseHttp.json()
    assert dictResult["bConnected"] is True
    assert dictResult["sMessage"] == "Connected"
    mockDelete.assert_not_called()
    mockStore.assert_called_once_with(
        "overleaf_token", "valid_git_token", "keyring",
    )


def test_setup_overleaf_validation_fails_cleans_up(clientHttp):
    """Bad Overleaf token: bConnected False, remediation, cleanup."""
    _fnConnectToContainer(clientHttp)
    _mockDockerInstance._iSyncExitCode = 0
    _mockDockerInstance._sSyncOutput = "ok"
    with patch(
        "vaibify.gui.syncDispatcher.fdictCheckConnectivity",
        return_value={"bConnected": True, "sMessage": "Connected"},
    ), patch(
        "vaibify.config.secretManager.fnStoreSecret",
        return_value=None,
    ), patch(
        "vaibify.gui.syncDispatcher.fbValidateOverleafCredentials",
        return_value=(False, ""),
    ), patch(
        "vaibify.config.secretManager.fnDeleteSecret",
        return_value=None,
    ) as mockDelete:
        responseHttp = clientHttp.post(
            f"/api/sync/{S_CONTAINER_ID}/setup",
            json={
                "sService": "overleaf",
                "sProjectId": "abc123proj",
                "sToken": "bad_token",
            },
        )
    assert responseHttp.status_code == 200
    dictResult = responseHttp.json()
    assert dictResult["bConnected"] is False
    assert "git authentication token" in dictResult["sMessage"]
    mockDelete.assert_called_once_with("overleaf_token", "keyring")


def test_setup_overleaf_validation_fails_embeds_stderr(clientHttp):
    """Remediation message embeds the git stderr fragment when available."""
    _fnConnectToContainer(clientHttp)
    _mockDockerInstance._iSyncExitCode = 0
    _mockDockerInstance._sSyncOutput = "ok"
    with patch(
        "vaibify.gui.syncDispatcher.fdictCheckConnectivity",
        return_value={"bConnected": True, "sMessage": "Connected"},
    ), patch(
        "vaibify.config.secretManager.fnStoreSecret",
        return_value=None,
    ), patch(
        "vaibify.gui.syncDispatcher.fbValidateOverleafCredentials",
        return_value=(False, "fatal: authentication failed for xyz"),
    ), patch(
        "vaibify.config.secretManager.fnDeleteSecret",
        return_value=None,
    ):
        responseHttp = clientHttp.post(
            f"/api/sync/{S_CONTAINER_ID}/setup",
            json={
                "sService": "overleaf",
                "sProjectId": "abc123proj",
                "sToken": "bad_token",
            },
        )
    dictResult = responseHttp.json()
    assert dictResult["bConnected"] is False
    assert "Overleaf rejected the token:" in dictResult["sMessage"]
    assert "authentication failed" in dictResult["sMessage"]
    assert "git authentication token" in dictResult["sMessage"]
    assert len(dictResult["sMessage"]) < 600


def test_setup_overleaf_store_failure_no_validation(clientHttp):
    """Store raises: bConnected False, no validation attempt."""
    _fnConnectToContainer(clientHttp)
    with patch(
        "vaibify.config.secretManager.fnStoreSecret",
        side_effect=RuntimeError("Keyring storage failed: NoKeyringError"),
    ), patch(
        "vaibify.gui.syncDispatcher.fbValidateOverleafCredentials",
        return_value=(True, ""),
    ) as mockValidate, patch(
        "vaibify.config.secretManager.fnDeleteSecret",
        return_value=None,
    ) as mockDelete:
        responseHttp = clientHttp.post(
            f"/api/sync/{S_CONTAINER_ID}/setup",
            json={
                "sService": "overleaf",
                "sProjectId": "abc123proj",
                "sToken": "any_token",
            },
        )
    assert responseHttp.status_code == 200
    dictResult = responseHttp.json()
    assert dictResult["bConnected"] is False
    assert "Failed to store" in dictResult["sMessage"]
    assert "NoKeyringError" in dictResult["sMessage"]
    mockValidate.assert_not_called()
    mockDelete.assert_not_called()


# ── Overleaf host-keyring migration tests ───────────────────────


def test_setup_overleaf_writes_host_keyring_not_container(clientHttp):
    """Overleaf setup with token should call host keyring, not container."""
    _fnConnectToContainer(clientHttp)
    with patch(
        "vaibify.config.secretManager.fnStoreSecret",
        return_value=None,
    ) as mockHostStore, patch(
        "vaibify.gui.syncDispatcher.fnStoreCredentialInContainer",
        return_value=None,
    ) as mockContainerStore, patch(
        "vaibify.gui.syncDispatcher.fdictCheckConnectivity",
        return_value={"bConnected": True, "sMessage": "Connected"},
    ), patch(
        "vaibify.gui.syncDispatcher.fbValidateOverleafCredentials",
        return_value=(True, ""),
    ):
        responseHttp = clientHttp.post(
            f"/api/sync/{S_CONTAINER_ID}/setup",
            json={
                "sService": "overleaf",
                "sProjectId": "abc123proj",
                "sToken": "t0kEn",
            },
        )
    assert responseHttp.status_code == 200
    dictResult = responseHttp.json()
    assert dictResult["bConnected"] is True
    mockHostStore.assert_called_once_with(
        "overleaf_token", "t0kEn", "keyring",
    )
    mockContainerStore.assert_not_called()


def test_setup_overleaf_no_token_uses_stored_credential(clientHttp):
    """No token + stored credential -> skip store, run validation."""
    _fnConnectToContainer(clientHttp)
    with patch(
        "vaibify.config.secretManager.fbSecretExists",
        return_value=True,
    ), patch(
        "vaibify.config.secretManager.fnStoreSecret",
        return_value=None,
    ) as mockHostStore, patch(
        "vaibify.gui.syncDispatcher.fdictCheckConnectivity",
        return_value={"bConnected": True, "sMessage": "Connected"},
    ), patch(
        "vaibify.gui.syncDispatcher.fbValidateOverleafCredentials",
        return_value=(True, ""),
    ) as mockValidate:
        responseHttp = clientHttp.post(
            f"/api/sync/{S_CONTAINER_ID}/setup",
            json={
                "sService": "overleaf",
                "sProjectId": "abc123proj",
            },
        )
    assert responseHttp.status_code == 200
    dictResult = responseHttp.json()
    assert dictResult["bConnected"] is True
    mockHostStore.assert_not_called()
    mockValidate.assert_called_once()


def test_check_overleaf_requires_saved_project_id(clientHttp):
    """check/overleaf returns bConnected=False when project ID missing."""
    _fnConnectToContainer(clientHttp)
    with patch(
        "vaibify.gui.syncDispatcher.fdictCheckConnectivity",
        return_value={"bConnected": True, "sMessage": "Connected"},
    ), patch(
        "vaibify.gui.routes.syncRoutes.fdictRequireWorkflow",
        return_value={"sOverleafProjectId": ""},
    ):
        responseHttp = clientHttp.get(
            f"/api/sync/{S_CONTAINER_ID}/check/overleaf"
        )
    assert responseHttp.status_code == 200
    dictResult = responseHttp.json()
    assert dictResult["bConnected"] is False
    assert "project id" in dictResult["sMessage"].lower()


def test_check_overleaf_passes_when_project_id_saved(clientHttp):
    """check/overleaf returns bConnected=True when token + project id both set."""
    _fnConnectToContainer(clientHttp)
    with patch(
        "vaibify.gui.syncDispatcher.fdictCheckConnectivity",
        return_value={"bConnected": True, "sMessage": "Connected"},
    ):
        responseHttp = clientHttp.get(
            f"/api/sync/{S_CONTAINER_ID}/check/overleaf"
        )
    assert responseHttp.status_code == 200
    assert responseHttp.json() == {
        "bConnected": True, "sMessage": "Connected",
    }


def test_has_credential_endpoint_true_when_stored(clientHttp):
    """has-credential reports True when host keyring has the token."""
    _fnConnectToContainer(clientHttp)
    with patch(
        "vaibify.config.secretManager.fbSecretExists",
        return_value=True,
    ):
        responseHttp = clientHttp.get(
            f"/api/sync/{S_CONTAINER_ID}/has-credential/overleaf"
        )
    assert responseHttp.status_code == 200
    assert responseHttp.json() == {"bHasCredential": True}


def test_has_credential_endpoint_false_when_absent(clientHttp):
    """has-credential reports False when host keyring is empty."""
    _fnConnectToContainer(clientHttp)
    with patch(
        "vaibify.config.secretManager.fbSecretExists",
        return_value=False,
    ):
        responseHttp = clientHttp.get(
            f"/api/sync/{S_CONTAINER_ID}/has-credential/overleaf"
        )
    assert responseHttp.status_code == 200
    assert responseHttp.json() == {"bHasCredential": False}


def test_has_credential_endpoint_rejects_unknown_service(clientHttp):
    """Invalid service name triggers a ValueError in the route."""
    _fnConnectToContainer(clientHttp)
    with pytest.raises(ValueError):
        clientHttp.get(
            f"/api/sync/{S_CONTAINER_ID}/has-credential/bogus"
        )


def test_setup_zenodo_validation_passes(clientHttp):
    """Regression: Zenodo setup via shared helper still works."""
    _fnConnectToContainer(clientHttp)
    with patch(
        "vaibify.gui.syncDispatcher.fdictCheckConnectivity",
        return_value={"bConnected": True, "sMessage": "Connected"},
    ), patch(
        "vaibify.gui.syncDispatcher.fnStoreCredentialInContainer",
        return_value=None,
    ), patch(
        "vaibify.gui.syncDispatcher.fbValidateZenodoToken",
        return_value=True,
    ), patch(
        "vaibify.gui.syncDispatcher.fnDeleteCredentialFromContainer",
        return_value=None,
    ) as mockDelete:
        responseHttp = clientHttp.post(
            f"/api/sync/{S_CONTAINER_ID}/setup",
            json={
                "sService": "zenodo",
                "sToken": "good_zenodo_token",
            },
        )
    assert responseHttp.status_code == 200
    dictResult = responseHttp.json()
    assert dictResult["bConnected"] is True
    mockDelete.assert_not_called()


def test_setup_zenodo_stores_token_in_sandbox_slot_by_default(
    clientHttp,
):
    """Default sZenodoInstance is sandbox -> zenodo_token_sandbox."""
    _fnConnectToContainer(clientHttp)
    with patch(
        "vaibify.gui.syncDispatcher.fdictCheckConnectivity",
        return_value={"bConnected": True, "sMessage": "Connected"},
    ), patch(
        "vaibify.gui.syncDispatcher.fnStoreCredentialInContainer",
        return_value=None,
    ) as mockStore, patch(
        "vaibify.gui.syncDispatcher.fbValidateZenodoToken",
        return_value=True,
    ):
        clientHttp.post(
            f"/api/sync/{S_CONTAINER_ID}/setup",
            json={
                "sService": "zenodo",
                "sToken": "good_zenodo_token",
            },
        )
    sStoredName = mockStore.call_args[0][2]
    assert sStoredName == "zenodo_token_sandbox"


def test_setup_zenodo_stores_production_token_in_production_slot(
    clientHttp,
):
    """sZenodoInstance=production -> zenodo_token_production slot."""
    _fnConnectToContainer(clientHttp)
    with patch(
        "vaibify.gui.syncDispatcher.fdictCheckConnectivity",
        return_value={"bConnected": True, "sMessage": "Connected"},
    ), patch(
        "vaibify.gui.syncDispatcher.fnStoreCredentialInContainer",
        return_value=None,
    ) as mockStore, patch(
        "vaibify.gui.syncDispatcher.fbValidateZenodoToken",
        return_value=True,
    ) as mockValidate:
        clientHttp.post(
            f"/api/sync/{S_CONTAINER_ID}/setup",
            json={
                "sService": "zenodo",
                "sToken": "prod_token",
                "sZenodoInstance": "production",
            },
        )
    sStoredName = mockStore.call_args[0][2]
    assert sStoredName == "zenodo_token_production"
    assert mockValidate.call_args[0][2] == "zenodo"


def test_setup_zenodo_persists_service_on_success(clientHttp):
    """Successful setup writes sZenodoService to the workflow."""
    _fnConnectToContainer(clientHttp)
    with patch(
        "vaibify.gui.syncDispatcher.fdictCheckConnectivity",
        return_value={"bConnected": True, "sMessage": "Connected"},
    ), patch(
        "vaibify.gui.syncDispatcher.fnStoreCredentialInContainer",
        return_value=None,
    ), patch(
        "vaibify.gui.syncDispatcher.fbValidateZenodoToken",
        return_value=True,
    ), patch(
        "vaibify.gui.workflowManager.fnSaveWorkflowToContainer",
    ) as mockSave:
        clientHttp.post(
            f"/api/sync/{S_CONTAINER_ID}/setup",
            json={
                "sService": "zenodo",
                "sToken": "prod_token",
                "sZenodoInstance": "production",
            },
        )
    assert mockSave.called
    dictWorkflow = mockSave.call_args[0][2]
    assert dictWorkflow.get("sZenodoService") == "zenodo"


def test_setup_zenodo_rejects_invalid_instance(clientHttp):
    """Unknown sZenodoInstance values are rejected with HTTP 400."""
    _fnConnectToContainer(clientHttp)
    responseHttp = clientHttp.post(
        f"/api/sync/{S_CONTAINER_ID}/setup",
        json={
            "sService": "zenodo",
            "sToken": "whatever",
            "sZenodoInstance": "devel",
        },
    )
    assert responseHttp.status_code == 400


# ── Overleaf mirror endpoints (refresh / tree / diff / delete) ──


def test_mirror_refresh_success(clientHttp):
    """POST /mirror/refresh returns bSuccess True with payload fields."""
    _fnConnectToContainer(clientHttp)
    with patch(
        "vaibify.gui.syncDispatcher.ftRefreshOverleafMirror",
        return_value=(True, {
            "sHeadSha": "abc123",
            "iFileCount": 3,
            "sRefreshedAt": "2026-04-17T00:00:00Z",
        }),
    ):
        responseHttp = clientHttp.post(
            f"/api/overleaf/{S_CONTAINER_ID}/mirror/refresh",
        )
    assert responseHttp.status_code == 200
    dictResult = responseHttp.json()
    assert dictResult["bSuccess"] is True
    assert dictResult["sHeadSha"] == "abc123"
    assert dictResult["iFileCount"] == 3


def test_mirror_refresh_auth_failure(clientHttp):
    """On refresh failure, bSuccess False with a message."""
    _fnConnectToContainer(clientHttp)
    with patch(
        "vaibify.gui.syncDispatcher.ftRefreshOverleafMirror",
        return_value=(False, "Mirror clone failed: authentication"),
    ):
        responseHttp = clientHttp.post(
            f"/api/overleaf/{S_CONTAINER_ID}/mirror/refresh",
        )
    assert responseHttp.status_code == 200
    dictResult = responseHttp.json()
    assert dictResult["bSuccess"] is False
    assert "authentication" in dictResult["sMessage"]


def test_mirror_refresh_missing_project_id_returns_400(clientHttp):
    """Project ID absent from workflow => HTTP 400 from refresh."""
    _fnConnectToContainer(clientHttp)
    with patch(
        "vaibify.gui.routes.syncRoutes.fdictRequireWorkflow",
        return_value={"sOverleafProjectId": ""},
    ):
        responseHttp = clientHttp.post(
            f"/api/overleaf/{S_CONTAINER_ID}/mirror/refresh",
        )
    assert responseHttp.status_code == 400


def test_mirror_tree_missing_project_id_returns_400(clientHttp):
    """Project ID absent => HTTP 400 from tree endpoint."""
    _fnConnectToContainer(clientHttp)
    with patch(
        "vaibify.gui.routes.syncRoutes.fdictRequireWorkflow",
        return_value={"sOverleafProjectId": ""},
    ):
        responseHttp = clientHttp.get(
            f"/api/overleaf/{S_CONTAINER_ID}/mirror/tree",
        )
    assert responseHttp.status_code == 400


def test_mirror_diff_missing_project_id_returns_400(clientHttp):
    """Project ID absent => HTTP 400 from diff endpoint."""
    _fnConnectToContainer(clientHttp)
    with patch(
        "vaibify.gui.routes.syncRoutes.fdictRequireWorkflow",
        return_value={"sOverleafProjectId": ""},
    ):
        responseHttp = clientHttp.post(
            f"/api/overleaf/{S_CONTAINER_ID}/diff",
            json={
                "listFilePaths": ["/workspace/Plot/fig.pdf"],
                "sTargetDirectory": "figures",
            },
        )
    assert responseHttp.status_code == 400


def test_mirror_delete_missing_project_id_returns_400(clientHttp):
    """Project ID absent => HTTP 400 from delete endpoint."""
    _fnConnectToContainer(clientHttp)
    with patch(
        "vaibify.gui.routes.syncRoutes.fdictRequireWorkflow",
        return_value={"sOverleafProjectId": ""},
    ):
        responseHttp = clientHttp.delete(
            f"/api/overleaf/{S_CONTAINER_ID}/mirror",
        )
    assert responseHttp.status_code == 400


def test_mirror_tree_includes_refreshed_at(clientHttp, tmp_path, monkeypatch):
    """GET /mirror/tree returns sRefreshedAt ISO timestamp."""
    _fnConnectToContainer(clientHttp)
    monkeypatch.setenv("HOME", str(tmp_path))
    import os
    from vaibify.reproducibility import overleafMirror
    sMirrorRoot = overleafMirror.fsGetMirrorRoot()
    sProjectDir = os.path.join(sMirrorRoot, "abc123proj")
    sGitDir = os.path.join(sProjectDir, ".git")
    os.makedirs(sGitDir)
    with open(os.path.join(sGitDir, "FETCH_HEAD"), "w") as handle:
        handle.write("")
    with patch(
        "vaibify.gui.syncDispatcher.flistListOverleafTree",
        return_value=[],
    ), patch(
        "vaibify.reproducibility.overleafMirror.fsReadMirrorHeadSha",
        return_value="headsha",
    ):
        responseHttp = clientHttp.get(
            f"/api/overleaf/{S_CONTAINER_ID}/mirror/tree",
        )
    assert responseHttp.status_code == 200
    dictResult = responseHttp.json()
    assert "sRefreshedAt" in dictResult
    assert dictResult["sRefreshedAt"].endswith("Z")


def test_mirror_tree_returns_entries(clientHttp):
    """GET /mirror/tree returns listEntries and sHeadSha."""
    _fnConnectToContainer(clientHttp)
    listEntries = [
        {"sPath": "figures/a.pdf", "sType": "blob",
         "iSize": 10, "sDigest": "sha1"},
    ]
    with patch(
        "vaibify.gui.syncDispatcher.flistListOverleafTree",
        return_value=listEntries,
    ), patch(
        "vaibify.reproducibility.overleafMirror.fsReadMirrorHeadSha",
        return_value="headsha",
    ):
        responseHttp = clientHttp.get(
            f"/api/overleaf/{S_CONTAINER_ID}/mirror/tree",
        )
    assert responseHttp.status_code == 200
    dictResult = responseHttp.json()
    assert dictResult["listEntries"] == listEntries
    assert dictResult["sHeadSha"] == "headsha"


def test_mirror_diff_implicit_refresh_and_classify(clientHttp):
    """POST /diff refreshes mirror then classifies."""
    _fnConnectToContainer(clientHttp)
    dictDiff = {
        "listNew": [], "listOverwrite": [], "listUnchanged": [],
    }
    with patch(
        "vaibify.gui.syncDispatcher.ftRefreshOverleafMirror",
        return_value=(True, {"sHeadSha": "h", "iFileCount": 0,
                              "sRefreshedAt": "t"}),
    ) as mockRefresh, patch(
        "vaibify.gui.syncDispatcher.fdictDiffOverleafPush",
        return_value=dictDiff,
    ), patch(
        "vaibify.gui.syncDispatcher.flistCheckOverleafConflicts",
        return_value=[],
    ), patch(
        "vaibify.reproducibility.overleafMirror.fsReadMirrorHeadSha",
        return_value="h",
    ):
        responseHttp = clientHttp.post(
            f"/api/overleaf/{S_CONTAINER_ID}/diff",
            json={
                "listFilePaths": ["/workspace/Plot/fig.pdf"],
                "sTargetDirectory": "figures",
            },
        )
    assert responseHttp.status_code == 200
    dictResult = responseHttp.json()
    assert "listConflicts" in dictResult
    assert dictResult["sMirrorHeadSha"] == "h"
    mockRefresh.assert_called_once()


def test_mirror_diff_surfaces_conflicts(clientHttp):
    """Diff result surfaces conflicts from the dispatcher."""
    _fnConnectToContainer(clientHttp)
    listConflicts = [{
        "sLocalPath": "/workspace/Plot/fig.pdf",
        "sRemotePath": "figures/fig.pdf",
        "sBaselineDigest": "oldsha",
        "sCurrentDigest": "newsha",
    }]
    with patch(
        "vaibify.gui.syncDispatcher.ftRefreshOverleafMirror",
        return_value=(True, {"sHeadSha": "h", "iFileCount": 1,
                              "sRefreshedAt": "t"}),
    ), patch(
        "vaibify.gui.syncDispatcher.fdictDiffOverleafPush",
        return_value={"listNew": [], "listOverwrite": [],
                       "listUnchanged": []},
    ), patch(
        "vaibify.gui.syncDispatcher.flistCheckOverleafConflicts",
        return_value=listConflicts,
    ), patch(
        "vaibify.reproducibility.overleafMirror.fsReadMirrorHeadSha",
        return_value="h",
    ):
        responseHttp = clientHttp.post(
            f"/api/overleaf/{S_CONTAINER_ID}/diff",
            json={
                "listFilePaths": ["/workspace/Plot/fig.pdf"],
                "sTargetDirectory": "figures",
            },
        )
    assert responseHttp.status_code == 200
    assert responseHttp.json()["listConflicts"] == listConflicts


def test_mirror_delete_idempotent(clientHttp):
    """DELETE /mirror calls fnDeleteMirror and returns bSuccess."""
    _fnConnectToContainer(clientHttp)
    with patch(
        "vaibify.reproducibility.overleafMirror.fnDeleteMirror",
        return_value=None,
    ) as mockDelete:
        responseHttp = clientHttp.delete(
            f"/api/overleaf/{S_CONTAINER_ID}/mirror",
        )
    assert responseHttp.status_code == 200
    assert responseHttp.json()["bSuccess"] is True
    mockDelete.assert_called_once_with("abc123proj")


# ── Overleaf push: target directory and digest persistence ──────


def test_overleaf_push_uses_request_target_directory(clientHttp):
    """When sTargetDirectory is provided, workflow is updated and used."""
    _fnConnectToContainer(clientHttp)
    _mockDockerInstance._iSyncExitCode = 0
    _mockDockerInstance._sSyncOutput = "ok"
    with patch(
        "vaibify.gui.syncDispatcher._fsFetchOverleafToken",
        return_value="tok",
    ), patch(
        "vaibify.gui.routes.syncRoutes._fsCapturePreMirrorSha",
        return_value="",
    ), patch(
        "vaibify.gui.routes.syncRoutes._fnPersistPostPushDigests",
        return_value=None,
    ):
        responseHttp = clientHttp.post(
            f"/api/overleaf/{S_CONTAINER_ID}/push",
            json={
                "listFilePaths": ["/workspace/Plot/fig.pdf"],
                "sCommitMessage": "push figs",
                "sTargetDirectory": "Figures/v2",
            },
        )
    assert responseHttp.status_code == 200
    assert responseHttp.json()["bSuccess"] is True


def test_overleaf_push_backward_compat_without_target(clientHttp):
    """Omitting sTargetDirectory falls back to dictWorkflow value."""
    _fnConnectToContainer(clientHttp)
    _mockDockerInstance._iSyncExitCode = 0
    _mockDockerInstance._sSyncOutput = "ok"
    with patch(
        "vaibify.gui.syncDispatcher._fsFetchOverleafToken",
        return_value="tok",
    ), patch(
        "vaibify.gui.routes.syncRoutes._fsCapturePreMirrorSha",
        return_value="",
    ), patch(
        "vaibify.gui.routes.syncRoutes._fnPersistPostPushDigests",
        return_value=None,
    ):
        responseHttp = clientHttp.post(
            f"/api/overleaf/{S_CONTAINER_ID}/push",
            json={
                "listFilePaths": ["/workspace/Plot/fig.pdf"],
                "sCommitMessage": "push figs",
            },
        )
    assert responseHttp.status_code == 200
    assert responseHttp.json()["bSuccess"] is True


def test_overleaf_push_persists_digests_on_success(clientHttp):
    """After a successful push, digest baseline is updated."""
    _fnConnectToContainer(clientHttp)
    _mockDockerInstance._iSyncExitCode = 0
    _mockDockerInstance._sSyncOutput = "HEAD_SHA=abcd1234\nok\n"
    with patch(
        "vaibify.gui.syncDispatcher._fsFetchOverleafToken",
        return_value="tok",
    ), patch(
        "vaibify.gui.routes.syncRoutes._fsCapturePreMirrorSha",
        return_value="preSha",
    ), patch(
        "vaibify.gui.routes.syncRoutes._fnPersistPostPushDigests",
        return_value=None,
    ) as mockPersist:
        responseHttp = clientHttp.post(
            f"/api/overleaf/{S_CONTAINER_ID}/push",
            json={
                "listFilePaths": ["/workspace/Plot/fig.pdf"],
                "sCommitMessage": "push figs",
            },
        )
    assert responseHttp.status_code == 200
    mockPersist.assert_called_once()


def test_mirror_diff_surfaces_case_collisions(clientHttp):
    """Diff response includes listCaseCollisions + canonical suggestion."""
    _fnConnectToContainer(clientHttp)
    listCollisions = [{
        "sLocalPath": "/workspace/Plot/fig.pdf",
        "sTypedRemotePath": "Figures/fig.pdf",
        "sCanonicalRemotePath": "figures/fig.pdf",
    }]
    with patch(
        "vaibify.gui.syncDispatcher.ftRefreshOverleafMirror",
        return_value=(True, {"sHeadSha": "h", "iFileCount": 0,
                              "sRefreshedAt": "t"}),
    ), patch(
        "vaibify.gui.syncDispatcher.fdictDiffOverleafPush",
        return_value={"listNew": [], "listOverwrite": [],
                       "listUnchanged": []},
    ), patch(
        "vaibify.gui.syncDispatcher.flistCheckOverleafConflicts",
        return_value=[],
    ), patch(
        "vaibify.gui.syncDispatcher.flistDetectOverleafCaseCollisions",
        return_value=listCollisions,
    ), patch(
        "vaibify.reproducibility.overleafMirror.fsReadMirrorHeadSha",
        return_value="h",
    ):
        responseHttp = clientHttp.post(
            f"/api/overleaf/{S_CONTAINER_ID}/diff",
            json={
                "listFilePaths": ["/workspace/Plot/fig.pdf"],
                "sTargetDirectory": "Figures",
            },
        )
    assert responseHttp.status_code == 200
    dictResult = responseHttp.json()
    assert dictResult["listCaseCollisions"] == listCollisions
    assert dictResult["sSuggestedTargetDirectory"] == "figures"


def test_mirror_diff_no_collisions_yields_empty_suggestion(clientHttp):
    """No collisions: listCaseCollisions empty, suggestion empty."""
    _fnConnectToContainer(clientHttp)
    with patch(
        "vaibify.gui.syncDispatcher.ftRefreshOverleafMirror",
        return_value=(True, {"sHeadSha": "h", "iFileCount": 0,
                              "sRefreshedAt": "t"}),
    ), patch(
        "vaibify.gui.syncDispatcher.fdictDiffOverleafPush",
        return_value={"listNew": [], "listOverwrite": [],
                       "listUnchanged": []},
    ), patch(
        "vaibify.gui.syncDispatcher.flistCheckOverleafConflicts",
        return_value=[],
    ), patch(
        "vaibify.gui.syncDispatcher.flistDetectOverleafCaseCollisions",
        return_value=[],
    ), patch(
        "vaibify.reproducibility.overleafMirror.fsReadMirrorHeadSha",
        return_value="h",
    ):
        responseHttp = clientHttp.post(
            f"/api/overleaf/{S_CONTAINER_ID}/diff",
            json={
                "listFilePaths": ["/workspace/Plot/fig.pdf"],
                "sTargetDirectory": "figures",
            },
        )
    assert responseHttp.status_code == 200
    dictResult = responseHttp.json()
    assert dictResult["listCaseCollisions"] == []
    assert dictResult["sSuggestedTargetDirectory"] == ""


def test_mirror_diff_ambiguous_canonical_yields_empty_suggestion(
    clientHttp,
):
    """When canonical dirs disagree across collisions, suggestion empty."""
    _fnConnectToContainer(clientHttp)
    listCollisions = [
        {
            "sLocalPath": "/workspace/Plot/a.pdf",
            "sTypedRemotePath": "Figures/a.pdf",
            "sCanonicalRemotePath": "figures/a.pdf",
        },
        {
            "sLocalPath": "/workspace/Plot/b.pdf",
            "sTypedRemotePath": "Figures/b.pdf",
            "sCanonicalRemotePath": "Figs/b.pdf",
        },
    ]
    with patch(
        "vaibify.gui.syncDispatcher.ftRefreshOverleafMirror",
        return_value=(True, {"sHeadSha": "h", "iFileCount": 0,
                              "sRefreshedAt": "t"}),
    ), patch(
        "vaibify.gui.syncDispatcher.fdictDiffOverleafPush",
        return_value={"listNew": [], "listOverwrite": [],
                       "listUnchanged": []},
    ), patch(
        "vaibify.gui.syncDispatcher.flistCheckOverleafConflicts",
        return_value=[],
    ), patch(
        "vaibify.gui.syncDispatcher.flistDetectOverleafCaseCollisions",
        return_value=listCollisions,
    ), patch(
        "vaibify.reproducibility.overleafMirror.fsReadMirrorHeadSha",
        return_value="h",
    ):
        responseHttp = clientHttp.post(
            f"/api/overleaf/{S_CONTAINER_ID}/diff",
            json={
                "listFilePaths": [
                    "/workspace/Plot/a.pdf",
                    "/workspace/Plot/b.pdf",
                ],
                "sTargetDirectory": "Figures",
            },
        )
    assert responseHttp.status_code == 200
    dictResult = responseHttp.json()
    assert dictResult["sSuggestedTargetDirectory"] == ""


def test_overleaf_push_failure_skips_digest_persist(clientHttp):
    """On push failure, the digest update must not fire."""
    _fnConnectToContainer(clientHttp)
    _mockDockerInstance._iSyncExitCode = 1
    _mockDockerInstance._sSyncOutput = "fatal: not found"
    with patch(
        "vaibify.gui.syncDispatcher._fsFetchOverleafToken",
        return_value="tok",
    ), patch(
        "vaibify.gui.routes.syncRoutes._fsCapturePreMirrorSha",
        return_value="",
    ), patch(
        "vaibify.gui.routes.syncRoutes._fnPersistPostPushDigests",
        return_value=None,
    ) as mockPersist:
        responseHttp = clientHttp.post(
            f"/api/overleaf/{S_CONTAINER_ID}/push",
            json={
                "listFilePaths": ["/workspace/Plot/fig.pdf"],
                "sCommitMessage": "push figs",
            },
        )
    assert responseHttp.status_code == 200
    assert responseHttp.json()["bSuccess"] is False
    mockPersist.assert_not_called()


# ── Security: validation of listFilePaths and sTargetDirectory ────


def test_overleaf_push_rejects_path_outside_workspace(clientHttp):
    """Push must 400 when listFilePaths contains a host path."""
    _fnConnectToContainer(clientHttp)
    with patch(
        "vaibify.gui.syncDispatcher._fsFetchOverleafToken",
        return_value="test-tok",
    ):
        responseHttp = clientHttp.post(
            f"/api/overleaf/{S_CONTAINER_ID}/push",
            json={
                "listFilePaths": ["/etc/passwd"],
                "sCommitMessage": "exploit",
            },
        )
    assert responseHttp.status_code == 400
    assert "workspace" in responseHttp.json()["detail"].lower()


def test_overleaf_push_rejects_dotdot_traversal(clientHttp):
    """Push must 400 for ``..`` traversal in listFilePaths."""
    _fnConnectToContainer(clientHttp)
    responseHttp = clientHttp.post(
        f"/api/overleaf/{S_CONTAINER_ID}/push",
        json={
            "listFilePaths": ["/workspace/../etc/passwd"],
            "sCommitMessage": "exploit",
        },
    )
    assert responseHttp.status_code == 400


def test_overleaf_push_rejects_null_byte_in_path(clientHttp):
    """Push must 400 when a file path contains a NUL byte."""
    _fnConnectToContainer(clientHttp)
    responseHttp = clientHttp.post(
        f"/api/overleaf/{S_CONTAINER_ID}/push",
        json={
            "listFilePaths": ["/workspace/a\x00b.pdf"],
            "sCommitMessage": "null",
        },
    )
    assert responseHttp.status_code == 400


def test_overleaf_push_rejects_absolute_target_directory(clientHttp):
    """Push must 400 when sTargetDirectory starts with a slash."""
    _fnConnectToContainer(clientHttp)
    responseHttp = clientHttp.post(
        f"/api/overleaf/{S_CONTAINER_ID}/push",
        json={
            "listFilePaths": ["/workspace/Plot/fig.pdf"],
            "sTargetDirectory": "/etc",
            "sCommitMessage": "bad target",
        },
    )
    assert responseHttp.status_code == 400


def test_overleaf_push_rejects_dotdot_target_directory(clientHttp):
    """Push must 400 when sTargetDirectory contains ``..``."""
    _fnConnectToContainer(clientHttp)
    responseHttp = clientHttp.post(
        f"/api/overleaf/{S_CONTAINER_ID}/push",
        json={
            "listFilePaths": ["/workspace/Plot/fig.pdf"],
            "sTargetDirectory": "figures/../../etc",
            "sCommitMessage": "escape",
        },
    )
    assert responseHttp.status_code == 400


def test_overleaf_diff_rejects_path_outside_workspace(clientHttp):
    """Diff must 400 when listFilePaths escapes the workspace."""
    _fnConnectToContainer(clientHttp)
    responseHttp = clientHttp.post(
        f"/api/overleaf/{S_CONTAINER_ID}/diff",
        json={
            "listFilePaths": ["/root/.ssh/id_rsa"],
            "sTargetDirectory": "figures",
        },
    )
    assert responseHttp.status_code == 400


def test_overleaf_diff_rejects_absolute_target_directory(clientHttp):
    """Diff must 400 when sTargetDirectory starts with a slash."""
    _fnConnectToContainer(clientHttp)
    responseHttp = clientHttp.post(
        f"/api/overleaf/{S_CONTAINER_ID}/diff",
        json={
            "listFilePaths": ["/workspace/Plot/fig.pdf"],
            "sTargetDirectory": "/etc",
        },
    )
    assert responseHttp.status_code == 400


def test_fsBuildZenodoTitle_prefers_project_title():
    from vaibify.gui.routes.syncRoutes import _fsBuildZenodoTitle
    sTitle = _fsBuildZenodoTitle({
        "sProjectTitle": "GJ1132 XUV evolution",
        "sWorkflowName": "run1",
    })
    assert sTitle == "GJ1132 XUV evolution"


def test_fsBuildZenodoTitle_falls_back_to_workflow_name():
    from vaibify.gui.routes.syncRoutes import _fsBuildZenodoTitle
    sTitle = _fsBuildZenodoTitle({"sWorkflowName": "run1"})
    assert sTitle == "run1"


def test_fsBuildZenodoTitle_falls_back_to_default():
    from vaibify.gui.routes.syncRoutes import _fsBuildZenodoTitle
    sTitle = _fsBuildZenodoTitle({})
    assert sTitle == "Vaibify archive"


def test_fsBuildZenodoTitle_preserves_quotes():
    """Phase 2 transport is base64, so titles need no sanitization."""
    from vaibify.gui.routes.syncRoutes import _fsBuildZenodoTitle
    sTitle = _fsBuildZenodoTitle(
        {"sProjectTitle": "Rory's pipeline"}
    )
    assert sTitle == "Rory's pipeline"


def test_fdictParseZenodoResult_extracts_fields():
    from vaibify.gui.routes.syncRoutes import _fdictParseZenodoResult
    sOut = (
        "Creating draft...\n"
        "ZENODO_RESULT={\"iDepositId\": 42, \"sDoi\": "
        "\"10.5281/zenodo.42\", \"sConceptDoi\": \"\", "
        "\"sHtmlUrl\": \"https://sandbox.zenodo.org/records/42\"}\n"
    )
    dictParsed = _fdictParseZenodoResult(sOut)
    assert dictParsed["iDepositId"] == 42
    assert dictParsed["sDoi"] == "10.5281/zenodo.42"
    assert dictParsed["sHtmlUrl"] == (
        "https://sandbox.zenodo.org/records/42"
    )


def test_fdictParseZenodoResult_missing_marker_returns_empty():
    from vaibify.gui.routes.syncRoutes import _fdictParseZenodoResult
    assert _fdictParseZenodoResult("no marker here\n") == {}


def test_fdictParseZenodoResult_malformed_json_returns_empty():
    from vaibify.gui.routes.syncRoutes import _fdictParseZenodoResult
    assert _fdictParseZenodoResult(
        "ZENODO_RESULT={not json}") == {}


def test_fnPersistZenodoPublishRecord_writes_fields():
    from vaibify.gui.routes.syncRoutes import (
        _fnPersistZenodoPublishRecord,
    )
    dictWorkflow = {}
    _fnPersistZenodoPublishRecord(dictWorkflow, {
        "iDepositId": 7,
        "sDoi": "10.5281/zenodo.7",
        "sConceptDoi": "10.5281/zenodo.6",
        "sHtmlUrl": "https://sandbox.zenodo.org/records/7",
    })
    assert dictWorkflow["sZenodoDepositionId"] == "7"
    assert dictWorkflow["sZenodoLatestDoi"] == "10.5281/zenodo.7"
    assert dictWorkflow["sZenodoConceptDoi"] == "10.5281/zenodo.6"
    assert dictWorkflow["sZenodoLatestUrl"] == (
        "https://sandbox.zenodo.org/records/7"
    )


def test_fnPersistZenodoPublishRecord_skips_empty_fields():
    from vaibify.gui.routes.syncRoutes import (
        _fnPersistZenodoPublishRecord,
    )
    dictWorkflow = {"sZenodoLatestDoi": "existing"}
    _fnPersistZenodoPublishRecord(dictWorkflow, {
        "sDoi": "", "sHtmlUrl": "", "iDepositId": 0,
    })
    assert dictWorkflow.get("sZenodoLatestDoi") == "existing"
    assert "sZenodoDepositionId" not in dictWorkflow


def test_fsReadHostGitUserName_returns_git_output():
    from vaibify.gui.routes import syncRoutes
    import subprocess
    mockResult = MagicMock()
    mockResult.stdout = "Jane Doe\n"
    with patch.object(subprocess, "run", return_value=mockResult):
        sName = syncRoutes._fsReadHostGitUserName()
    assert sName == "Jane Doe"


def test_fsReadHostGitUserName_falls_back_on_empty():
    from vaibify.gui.routes import syncRoutes
    import subprocess
    mockResult = MagicMock()
    mockResult.stdout = ""
    with patch.object(subprocess, "run", return_value=mockResult):
        sName = syncRoutes._fsReadHostGitUserName()
    assert sName == "Vaibify User"


def test_fsReadHostGitUserName_falls_back_on_exception():
    from vaibify.gui.routes import syncRoutes
    import subprocess
    with patch.object(
        subprocess, "run",
        side_effect=FileNotFoundError("git missing"),
    ):
        sName = syncRoutes._fsReadHostGitUserName()
    assert sName == "Vaibify User"


def test_fsReadHostGitUserName_strips_quote():
    from vaibify.gui.routes import syncRoutes
    import subprocess
    mockResult = MagicMock()
    mockResult.stdout = "O'Brien\n"
    with patch.object(subprocess, "run", return_value=mockResult):
        sName = syncRoutes._fsReadHostGitUserName()
    assert sName == "OBrien"


# ----------------------------------------------------------------------
# Zenodo metadata endpoints (Phase 2)
# ----------------------------------------------------------------------


def test_get_zenodo_metadata_returns_defaults(clientHttp):
    """Workflow with no metadata yields the initialized defaults."""
    _fnConnectToContainer(clientHttp)
    with patch(
        "vaibify.gui.routes.syncRoutes._fsReadHostGitUserName",
        return_value="Jane Doe",
    ):
        responseHttp = clientHttp.get(
            f"/api/zenodo/{S_CONTAINER_ID}/metadata"
        )
    assert responseHttp.status_code == 200
    dictResult = responseHttp.json()
    assert dictResult["sTitle"] == ""
    assert dictResult["sLicense"] == "CC-BY-4.0"
    assert dictResult["sDefaultCreatorName"] == "Jane Doe"


def test_post_zenodo_metadata_persists_fields(clientHttp):
    """POST persists normalized metadata into the workflow."""
    _fnConnectToContainer(clientHttp)
    with patch(
        "vaibify.gui.workflowManager.fnSaveWorkflowToContainer",
    ) as mockSave:
        responseHttp = clientHttp.post(
            f"/api/zenodo/{S_CONTAINER_ID}/metadata",
            json={
                "sTitle": "My Dataset",
                "sDescription": "Hello",
                "listCreators": [{
                    "sName": "Jane Doe",
                    "sAffiliation": "UW",
                    "sOrcid": "0000-0001-2345-6789",
                }],
                "sLicense": "MIT",
                "listKeywords": ["alpha", "beta"],
                "sRelatedGithubUrl": "https://github.com/u/r",
            },
        )
    assert responseHttp.status_code == 200
    dictSaved = mockSave.call_args[0][2]
    dictMeta = dictSaved["dictZenodoMetadata"]
    assert dictMeta["sTitle"] == "My Dataset"
    assert dictMeta["listCreators"][0]["sName"] == "Jane Doe"
    assert dictMeta["sLicense"] == "MIT"


def test_post_zenodo_metadata_rejects_empty_title(clientHttp):
    """Empty title returns HTTP 400."""
    _fnConnectToContainer(clientHttp)
    responseHttp = clientHttp.post(
        f"/api/zenodo/{S_CONTAINER_ID}/metadata",
        json={
            "sTitle": "   ",
            "listCreators": [{"sName": "Jane"}],
            "sLicense": "MIT",
        },
    )
    assert responseHttp.status_code == 400


def test_post_zenodo_metadata_rejects_missing_creator(clientHttp):
    """At least one creator with a name is required."""
    _fnConnectToContainer(clientHttp)
    responseHttp = clientHttp.post(
        f"/api/zenodo/{S_CONTAINER_ID}/metadata",
        json={
            "sTitle": "X",
            "listCreators": [{"sName": ""}],
            "sLicense": "MIT",
        },
    )
    assert responseHttp.status_code == 400


# ----------------------------------------------------------------------
# Archive-uses-metadata (Phase 2)
# ----------------------------------------------------------------------


def test_fdictResolveZenodoMetadataForArchive_uses_stored_metadata():
    from vaibify.gui.routes.syncRoutes import (
        _fdictResolveZenodoMetadataForArchive,
    )
    dictWf = {
        "sWorkflowName": "fallback",
        "dictZenodoMetadata": {
            "sTitle": "My Title",
            "listCreators": [{
                "sName": "Jane Doe",
                "sAffiliation": "", "sOrcid": "",
            }],
            "sLicense": "MIT",
            "sDescription": "", "listKeywords": [],
            "sRelatedGithubUrl": "",
        },
    }
    dictMeta = _fdictResolveZenodoMetadataForArchive(dictWf)
    assert dictMeta["sTitle"] == "My Title"
    assert dictMeta["listCreators"][0]["sName"] == "Jane Doe"


def test_fdictResolveZenodoMetadataForArchive_fills_missing_title():
    from vaibify.gui.routes.syncRoutes import (
        _fdictResolveZenodoMetadataForArchive,
    )
    dictWf = {"sWorkflowName": "fallback-name"}
    with patch(
        "vaibify.gui.routes.syncRoutes._fsReadHostGitUserName",
        return_value="Jane",
    ):
        dictMeta = _fdictResolveZenodoMetadataForArchive(dictWf)
    assert dictMeta["sTitle"] == "fallback-name"
    assert dictMeta["listCreators"][0]["sName"] == "Jane"


# ----------------------------------------------------------------------
# Zenodo deposit summary endpoint (Phase 3)
# ----------------------------------------------------------------------


def test_get_zenodo_deposit_empty_when_never_published(clientHttp):
    """Workflow with no deposit yields empty strings for all fields."""
    _fnConnectToContainer(clientHttp)
    responseHttp = clientHttp.get(
        f"/api/zenodo/{S_CONTAINER_ID}/deposit"
    )
    assert responseHttp.status_code == 200
    dictResult = responseHttp.json()
    assert dictResult["sDoi"] == ""
    assert dictResult["sHtmlUrl"] == ""
    assert dictResult["sDepositionId"] == ""


def test_get_zenodo_deposit_returns_stored_fields(clientHttp):
    """After a push writes deposit fields, GET surfaces them."""
    _fnConnectToContainer(clientHttp)
    dictWf = {
        "sZenodoDepositionId": "491655",
        "sZenodoLatestDoi": "10.5072/zenodo.491655",
        "sZenodoConceptDoi": "10.5072/zenodo.100000",
        "sZenodoLatestUrl": (
            "https://sandbox.zenodo.org/records/491655"
        ),
        "sZenodoService": "sandbox",
    }
    with patch(
        "vaibify.gui.routes.syncRoutes.fdictRequireWorkflow",
        return_value=dictWf,
    ):
        responseHttp = clientHttp.get(
            f"/api/zenodo/{S_CONTAINER_ID}/deposit"
        )
    assert responseHttp.status_code == 200
    dictResult = responseHttp.json()
    assert dictResult["sDoi"] == "10.5072/zenodo.491655"
    assert dictResult["sConceptDoi"] == "10.5072/zenodo.100000"
    assert dictResult["sHtmlUrl"] == (
        "https://sandbox.zenodo.org/records/491655"
    )
    assert dictResult["sService"] == "sandbox"


def test_fdictBuildDepositSummary_returns_empty_for_unpublished():
    from vaibify.gui.routes.syncRoutes import (
        _fdictBuildDepositSummary,
    )
    dictSummary = _fdictBuildDepositSummary({})
    assert dictSummary == {
        "sDepositionId": "",
        "sDoi": "",
        "sConceptDoi": "",
        "sHtmlUrl": "",
        "sService": "",
    }


def test_fdictBuildDepositSummary_reads_all_fields():
    from vaibify.gui.routes.syncRoutes import (
        _fdictBuildDepositSummary,
    )
    dictSummary = _fdictBuildDepositSummary({
        "sZenodoDepositionId": "42",
        "sZenodoLatestDoi": "10.5281/zenodo.42",
        "sZenodoConceptDoi": "10.5281/zenodo.1",
        "sZenodoLatestUrl": "https://zenodo.org/records/42",
        "sZenodoService": "zenodo",
    })
    assert dictSummary["sDoi"] == "10.5281/zenodo.42"
    assert dictSummary["sService"] == "zenodo"


# ----------------------------------------------------------------------
# Versioning: parent deposit id (Phase 5)
# ----------------------------------------------------------------------


def test_fiReadParentDepositId_returns_int():
    from vaibify.gui.routes.syncRoutes import _fiReadParentDepositId
    assert _fiReadParentDepositId(
        {"sZenodoDepositionId": "491655"}) == 491655


def test_fiReadParentDepositId_absent_returns_zero():
    from vaibify.gui.routes.syncRoutes import _fiReadParentDepositId
    assert _fiReadParentDepositId({}) == 0


def test_fiReadParentDepositId_empty_string_returns_zero():
    from vaibify.gui.routes.syncRoutes import _fiReadParentDepositId
    assert _fiReadParentDepositId(
        {"sZenodoDepositionId": ""}) == 0


def test_fiReadParentDepositId_non_numeric_returns_zero():
    from vaibify.gui.routes.syncRoutes import _fiReadParentDepositId
    assert _fiReadParentDepositId(
        {"sZenodoDepositionId": "not-a-number"}) == 0


def test_fiReadParentDepositId_negative_returns_zero():
    from vaibify.gui.routes.syncRoutes import _fiReadParentDepositId
    assert _fiReadParentDepositId(
        {"sZenodoDepositionId": "-5"}) == 0


def test_zenodo_archive_passes_parent_deposit_id_to_dispatcher(clientHttp):
    """When the workflow has a deposition id, the archive endpoint
    threads it to the dispatcher so the newversion flow fires."""
    _fnConnectToContainer(clientHttp)
    dictWf = {
        "sWorkflowName": "Test Pipeline",
        "sZenodoService": "sandbox",
        "sZenodoDepositionId": "491655",
    }
    with patch(
        "vaibify.gui.routes.syncRoutes.fdictRequireWorkflow",
        return_value=dictWf,
    ), patch(
        "vaibify.gui.syncDispatcher.ftResultArchiveToZenodo",
        return_value=(0, 'ZENODO_RESULT={"iDepositId": 999, '
                     '"sDoi": "10.5072/zenodo.999", '
                     '"sConceptDoi": "", "sHtmlUrl": ""}'),
    ) as mockArchive, patch(
        "vaibify.gui.routes.syncRoutes."
        "_fdictComputePostArchiveZenodoDigests",
        return_value={},
    ), patch(
        "vaibify.gui.workflowManager.fnSaveWorkflowToContainer",
    ):
        responseHttp = clientHttp.post(
            f"/api/zenodo/{S_CONTAINER_ID}/archive",
            json={"listFilePaths": ["/workspace/data.h5"]},
        )
    assert responseHttp.status_code == 200
    # Parent deposit id is the 6th positional arg (after docker,
    # cid, service, paths, metadata)
    listArgs = mockArchive.call_args[0]
    assert listArgs[5] == 491655


def test_zenodo_archive_passes_zero_when_no_prior_deposit(clientHttp):
    _fnConnectToContainer(clientHttp)
    dictWf = {"sWorkflowName": "Test", "sZenodoService": "sandbox"}
    with patch(
        "vaibify.gui.routes.syncRoutes.fdictRequireWorkflow",
        return_value=dictWf,
    ), patch(
        "vaibify.gui.syncDispatcher.ftResultArchiveToZenodo",
        return_value=(0, 'ZENODO_RESULT={"iDepositId": 1, '
                     '"sDoi": "10.5072/zenodo.1", '
                     '"sConceptDoi": "", "sHtmlUrl": ""}'),
    ) as mockArchive, patch(
        "vaibify.gui.routes.syncRoutes."
        "_fdictComputePostArchiveZenodoDigests",
        return_value={},
    ), patch(
        "vaibify.gui.workflowManager.fnSaveWorkflowToContainer",
    ):
        clientHttp.post(
            f"/api/zenodo/{S_CONTAINER_ID}/archive",
            json={"listFilePaths": ["/workspace/data.h5"]},
        )
    listArgs = mockArchive.call_args[0]
    assert listArgs[5] == 0
