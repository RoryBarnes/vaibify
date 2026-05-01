"""Tests for network-isolation gating on sync routes (audit F-R-08).

When a vaibify container is started with ``--network none``, host-side
routes that reach external services (Overleaf, Zenodo, GitHub) cannot
succeed. The audit fix:

- Each affected route raises HTTP 409 with a structured error body
  ``{"sError": "isolation-mode-blocks-network", ...}`` so the GUI can
  render an actionable message instead of a 30-second DNS timeout.
- ``GET /api/containers/{id}/isolation`` exposes the runtime flag so
  the GUI can disable buttons before the user clicks them.
"""

import json

import pytest
from unittest.mock import patch

from fastapi.testclient import TestClient

from vaibify.gui import pipelineServer


S_CONTAINER_ID = "isolation_test_cid"
S_WORKFLOW_PATH = "/workspace/.vaibify/workflows/test.json"

DICT_WORKFLOW_FIXTURE = {
    "sWorkflowName": "Isolation Test Pipeline",
    "sPlotDirectory": "Plot",
    "sFigureType": "pdf",
    "iNumberOfCores": 1,
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
            "bRunEnabled": True,
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


class _MockDockerIsolation:
    """Docker mock returning canned exec results.

    The real ``docker inspect`` call that powers
    ``fbContainerIsNetworkIsolated`` is patched directly in the tests
    that care about network mode; this mock only stands in for the
    docker-py operations used by the request lifecycle.
    """

    def __init__(self):
        self._dictFiles = {}

    def flistGetRunningContainers(self):
        return [
            {
                "sContainerId": S_CONTAINER_ID,
                "sShortId": "iso0001",
                "sName": "isolation-container",
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
        return (0, "ok")

    def fbaFetchFile(self, sContainerId, sPath):
        if sPath in self._dictFiles:
            return self._dictFiles[sPath]
        if sPath.endswith(".json"):
            return json.dumps(DICT_WORKFLOW_FIXTURE).encode("utf-8")
        raise FileNotFoundError(f"Not found: {sPath}")

    def fnWriteFile(self, sContainerId, sPath, baContent):
        self._dictFiles[sPath] = baContent

    def fsExecCreate(self, sContainerId, sCommand=None, sUser=None):
        return "exec-id-iso"

    def fsocketExecStart(self, sExecId):
        return None

    def fnExecResize(self, sExecId, iRows, iColumns):
        pass


def _fmockCreateDockerIsolation():
    return _MockDockerIsolation()


def _fnConnectToContainer(clientHttp):
    """POST to /api/connect and assert success."""
    responseHttp = clientHttp.post(
        f"/api/connect/{S_CONTAINER_ID}",
        params={"sWorkflowPath": S_WORKFLOW_PATH},
    )
    assert responseHttp.status_code == 200


@pytest.fixture
def clientHttp():
    """TestClient with mocked Docker for isolation testing."""
    with patch.object(
        pipelineServer, "_fconnectionCreateDocker",
        _fmockCreateDockerIsolation,
    ):
        app = pipelineServer.fappCreateApplication(
            sWorkspaceRoot="/workspace",
            sTerminalUserArg="testuser",
        )
    return TestClient(
        app, headers={"X-Session-Token": app.state.sSessionToken},
    )


# ─── F-R-08: blocked when isolated ──────────────────────────────


def _fbAssertBlocked(responseHttp):
    """Assert the response is the structured isolation-block error."""
    assert responseHttp.status_code == 409
    dictBody = responseHttp.json()
    dictDetail = dictBody.get("detail", {})
    assert dictDetail.get("sError") == "isolation-mode-blocks-network"
    assert "isolation mode" in dictDetail.get("sMessage", "")
    assert "networkIsolation: false" in dictDetail.get("sMessage", "")


def test_overleaf_push_blocked_when_isolated(clientHttp):
    """Overleaf push returns 409 when the container is isolated."""
    _fnConnectToContainer(clientHttp)
    with patch(
        "vaibify.docker.containerManager.fbContainerIsNetworkIsolated",
        return_value=True,
    ):
        responseHttp = clientHttp.post(
            f"/api/overleaf/{S_CONTAINER_ID}/push",
            json={
                "listFilePaths": ["/workspace/Plot/fig.pdf"],
                "sCommitMessage": "push figs",
            },
        )
    _fbAssertBlocked(responseHttp)


def test_zenodo_archive_blocked_when_isolated(clientHttp):
    """Zenodo archive returns 409 when the container is isolated."""
    _fnConnectToContainer(clientHttp)
    with patch(
        "vaibify.docker.containerManager.fbContainerIsNetworkIsolated",
        return_value=True,
    ):
        responseHttp = clientHttp.post(
            f"/api/zenodo/{S_CONTAINER_ID}/archive",
            json={
                "listFilePaths": ["/workspace/data.h5"],
                "sCommitMessage": "archive",
            },
        )
    _fbAssertBlocked(responseHttp)


def test_zenodo_download_blocked_when_isolated(clientHttp):
    """Zenodo dataset download returns 409 when the container is isolated."""
    _fnConnectToContainer(clientHttp)
    with patch(
        "vaibify.docker.containerManager.fbContainerIsNetworkIsolated",
        return_value=True,
    ):
        responseHttp = clientHttp.post(
            f"/api/zenodo/{S_CONTAINER_ID}/download",
            json={
                "iRecordId": 1234567,
                "sFileName": "data.h5",
                "sDestination": "/workspace/data.h5",
            },
        )
    _fbAssertBlocked(responseHttp)


def test_overleaf_mirror_refresh_blocked_when_isolated(clientHttp):
    """Overleaf mirror refresh returns 409 when the container is isolated."""
    _fnConnectToContainer(clientHttp)
    with patch(
        "vaibify.docker.containerManager.fbContainerIsNetworkIsolated",
        return_value=True,
    ):
        responseHttp = clientHttp.post(
            f"/api/overleaf/{S_CONTAINER_ID}/mirror/refresh",
        )
    _fbAssertBlocked(responseHttp)


def test_overleaf_diff_blocked_when_isolated(clientHttp):
    """Overleaf diff returns 409 when the container is isolated."""
    _fnConnectToContainer(clientHttp)
    with patch(
        "vaibify.docker.containerManager.fbContainerIsNetworkIsolated",
        return_value=True,
    ):
        responseHttp = clientHttp.post(
            f"/api/overleaf/{S_CONTAINER_ID}/diff",
            json={
                "listFilePaths": ["/workspace/Plot/fig.pdf"],
                "sTargetDirectory": "figures",
            },
        )
    _fbAssertBlocked(responseHttp)


def test_github_push_blocked_when_isolated(clientHttp):
    """GitHub push returns 409 when the container is isolated."""
    _fnConnectToContainer(clientHttp)
    with patch(
        "vaibify.docker.containerManager.fbContainerIsNetworkIsolated",
        return_value=True,
    ):
        responseHttp = clientHttp.post(
            f"/api/github/{S_CONTAINER_ID}/push",
            json={
                "listFilePaths": ["/workspace/run.py"],
                "sCommitMessage": "push code",
            },
        )
    _fbAssertBlocked(responseHttp)


# ─── F-R-08: unblocked when not isolated ────────────────────────


def test_overleaf_push_proceeds_when_not_isolated(clientHttp):
    """Overleaf push reaches the dispatcher when network is allowed."""
    _fnConnectToContainer(clientHttp)
    with patch(
        "vaibify.docker.containerManager.fbContainerIsNetworkIsolated",
        return_value=False,
    ), patch(
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
    assert responseHttp.json().get("bSuccess") is True


def test_zenodo_archive_proceeds_when_not_isolated(clientHttp):
    """Zenodo archive reaches the dispatcher when network is allowed."""
    _fnConnectToContainer(clientHttp)
    with patch(
        "vaibify.docker.containerManager.fbContainerIsNetworkIsolated",
        return_value=False,
    ):
        responseHttp = clientHttp.post(
            f"/api/zenodo/{S_CONTAINER_ID}/archive",
            json={
                "listFilePaths": ["/workspace/data.h5"],
                "sCommitMessage": "archive",
            },
        )
    assert responseHttp.status_code == 200
    assert responseHttp.json().get("bSuccess") is True


def test_github_push_proceeds_when_not_isolated(clientHttp):
    """GitHub push reaches the dispatcher when network is allowed."""
    _fnConnectToContainer(clientHttp)
    with patch(
        "vaibify.docker.containerManager.fbContainerIsNetworkIsolated",
        return_value=False,
    ):
        responseHttp = clientHttp.post(
            f"/api/github/{S_CONTAINER_ID}/push",
            json={
                "listFilePaths": ["/workspace/run.py"],
                "sCommitMessage": "push code",
            },
        )
    assert responseHttp.status_code == 200
    assert responseHttp.json().get("bSuccess") is True


# ─── /api/containers/{id}/isolation endpoint ────────────────────


def test_isolation_endpoint_reports_true(clientHttp):
    """Endpoint returns bNetworkIsolation True for isolated container."""
    _fnConnectToContainer(clientHttp)
    with patch(
        "vaibify.docker.containerManager.fbContainerIsNetworkIsolated",
        return_value=True,
    ):
        responseHttp = clientHttp.get(
            f"/api/containers/{S_CONTAINER_ID}/isolation",
        )
    assert responseHttp.status_code == 200
    assert responseHttp.json() == {"bNetworkIsolation": True}


def test_isolation_endpoint_reports_false(clientHttp):
    """Endpoint returns bNetworkIsolation False for normal container."""
    _fnConnectToContainer(clientHttp)
    with patch(
        "vaibify.docker.containerManager.fbContainerIsNetworkIsolated",
        return_value=False,
    ):
        responseHttp = clientHttp.get(
            f"/api/containers/{S_CONTAINER_ID}/isolation",
        )
    assert responseHttp.status_code == 200
    assert responseHttp.json() == {"bNetworkIsolation": False}


# ─── fbContainerIsNetworkIsolated unit ──────────────────────────


def test_fbContainerIsNetworkIsolated_returns_true_on_none():
    """When docker inspect reports NetworkMode=none, returns True."""
    from vaibify.docker import containerManager
    with patch(
        "vaibify.docker.containerManager.subprocess.run",
    ) as mockRun:
        mockRun.return_value = type(
            "Result", (), {"returncode": 0, "stdout": "none\n"},
        )()
        assert (
            containerManager.fbContainerIsNetworkIsolated("xyz")
            is True
        )


def test_fbContainerIsNetworkIsolated_returns_false_on_default():
    """When docker inspect reports NetworkMode=default, returns False."""
    from vaibify.docker import containerManager
    with patch(
        "vaibify.docker.containerManager.subprocess.run",
    ) as mockRun:
        mockRun.return_value = type(
            "Result", (), {"returncode": 0, "stdout": "default\n"},
        )()
        assert (
            containerManager.fbContainerIsNetworkIsolated("xyz")
            is False
        )


def test_fbContainerIsNetworkIsolated_returns_false_on_inspect_error():
    """When docker inspect fails, returns False (fail-open)."""
    from vaibify.docker import containerManager
    with patch(
        "vaibify.docker.containerManager.subprocess.run",
    ) as mockRun:
        mockRun.return_value = type(
            "Result", (), {"returncode": 1, "stdout": ""},
        )()
        assert (
            containerManager.fbContainerIsNetworkIsolated("xyz")
            is False
        )
