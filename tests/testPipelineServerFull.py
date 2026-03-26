"""Tests for uncovered routes in vaibify.gui.pipelineServer."""

import json

import pytest
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient

from vaibify.gui import pipelineServer


S_CONTAINER_ID = "srv123container"
S_WORKFLOW_PATH = "/workspace/.vaibify/workflows/test.json"

DICT_WORKFLOW = {
    "sWorkflowName": "Test Pipeline",
    "sPlotDirectory": "Plot",
    "sFigureType": "pdf",
    "iNumberOfCores": 4,
    "sOverleafProjectId": "abc123proj",
    "sOverleafFigureDirectory": "figures",
    "sGithubBaseUrl": "https://github.com/test/repo",
    "sZenodoDoi": "",
    "sTexFilename": "main.tex",
    "dictSyncStatus": {},
    "listSteps": [
        {
            "sName": "Step A",
            "sDirectory": "/workspace/stepA",
            "bPlotOnly": True,
            "bEnabled": True,
            "bInteractive": False,
            "saDataCommands": ["python dataGenerate.py"],
            "saDataFiles": ["output.dat"],
            "saTestCommands": ["pytest test_step01.py"],
            "saPlotCommands": ["python plotResults.py"],
            "saPlotFiles": [
                "{sPlotDirectory}/fig.{sFigureType}",
            ],
            "dictRunStats": {},
            "dictVerification": {
                "sUnitTest": "untested",
                "sUser": "untested",
            },
        },
    ],
}


class MockDockerFull:
    """Extended mock Docker connection."""

    def __init__(self):
        self._dictFiles = {}

    def flistGetRunningContainers(self):
        return [{
            "sContainerId": S_CONTAINER_ID,
            "sShortId": "srv123",
            "sName": "test-container",
            "sImage": "ubuntu:24.04",
        }]

    def ftResultExecuteCommand(
        self, sContainerId, sCommand, sWorkdir=None,
    ):
        if "test -d" in sCommand and ".vaibify" in sCommand:
            return (0, "")
        if "find" in sCommand and "workflows" in sCommand:
            return (0, S_WORKFLOW_PATH + "\n")
        if "find" in sCommand and "*.py" in sCommand:
            return (0, "analyze.py\nplot.py\n")
        if "find" in sCommand and "logs" in sCommand:
            return (0, "/workspace/.vaibify/logs/run.log\n")
        if "find" in sCommand:
            return (0, "")
        if "stat -c" in sCommand:
            return (0, "/workspace/stepA/out.dat 1700000000")
        if "test -d" in sCommand:
            return (0, "f")
        if "cat" in sCommand and "pipeline_state" in sCommand:
            return (1, "")
        if "ps aux" in sCommand:
            return (0, "0\n")
        if "dot -Tsvg" in sCommand:
            return (0, "")
        if "which claude" in sCommand:
            return (0, "/usr/bin/claude")
        if "rm -f" in sCommand:
            return (0, "")
        return (0, "")

    def fbaFetchFile(self, sContainerId, sPath):
        if sPath in self._dictFiles:
            return self._dictFiles[sPath]
        if sPath.endswith(".json"):
            return json.dumps(DICT_WORKFLOW).encode("utf-8")
        if sPath.endswith(".svg"):
            return b"<svg></svg>"
        if sPath.endswith(".log"):
            return b"log content here"
        raise FileNotFoundError(f"Not found: {sPath}")

    def fnWriteFile(self, sContainerId, sPath, baContent):
        self._dictFiles[sPath] = baContent

    def fsExecCreate(
        self, sContainerId, sCommand=None, sUser=None,
    ):
        return "exec-id-mock"

    def fsocketExecStart(self, sExecId):
        return None

    def fnExecResize(self, sExecId, iRows, iColumns):
        pass


def _fmockCreateDocker():
    """Return a MockDockerFull instance."""
    return MockDockerFull()


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


# -----------------------------------------------------------------------
# Overleaf push
# -----------------------------------------------------------------------


def test_overleaf_push_success(clientHttp):
    _fnConnectToContainer(clientHttp)
    dictPayload = {
        "listFilePaths": ["Plot/fig.pdf"],
        "sCommitMessage": "Update figures",
    }
    responseHttp = clientHttp.post(
        f"/api/overleaf/{S_CONTAINER_ID}/push",
        json=dictPayload,
    )
    assert responseHttp.status_code == 200
    assert responseHttp.json()["bSuccess"] is True


# -----------------------------------------------------------------------
# Zenodo archive
# -----------------------------------------------------------------------


def test_zenodo_archive_success(clientHttp):
    _fnConnectToContainer(clientHttp)
    dictPayload = {
        "listFilePaths": ["data.npy"],
        "sCommitMessage": "Archive data",
    }
    responseHttp = clientHttp.post(
        f"/api/zenodo/{S_CONTAINER_ID}/archive",
        json=dictPayload,
    )
    assert responseHttp.status_code == 200
    assert responseHttp.json()["bSuccess"] is True


# -----------------------------------------------------------------------
# GitHub push
# -----------------------------------------------------------------------


def test_github_push_success(clientHttp):
    _fnConnectToContainer(clientHttp)
    dictPayload = {
        "listFilePaths": ["script.py"],
        "sCommitMessage": "Push script",
    }
    responseHttp = clientHttp.post(
        f"/api/github/{S_CONTAINER_ID}/push",
        json=dictPayload,
    )
    assert responseHttp.status_code == 200


# -----------------------------------------------------------------------
# GitHub add-file
# -----------------------------------------------------------------------


def test_github_add_file(clientHttp):
    _fnConnectToContainer(clientHttp)
    dictPayload = {
        "sFilePath": "data/result.npy",
        "sCommitMessage": "Add data",
    }
    responseHttp = clientHttp.post(
        f"/api/github/{S_CONTAINER_ID}/add-file",
        json=dictPayload,
    )
    assert responseHttp.status_code == 200


# -----------------------------------------------------------------------
# Scan scripts
# -----------------------------------------------------------------------


def test_scan_scripts(clientHttp):
    _fnConnectToContainer(clientHttp)
    responseHttp = clientHttp.post(
        f"/api/steps/{S_CONTAINER_ID}/0/scan-scripts",
    )
    assert responseHttp.status_code == 200


# -----------------------------------------------------------------------
# Sync setup
# -----------------------------------------------------------------------


def test_sync_setup_github(clientHttp):
    _fnConnectToContainer(clientHttp)
    dictPayload = {"sService": "github"}
    responseHttp = clientHttp.post(
        f"/api/sync/{S_CONTAINER_ID}/setup",
        json=dictPayload,
    )
    assert responseHttp.status_code == 200
    assert "bConnected" in responseHttp.json()


# -----------------------------------------------------------------------
# Sync check
# -----------------------------------------------------------------------


def test_sync_check_github(clientHttp):
    _fnConnectToContainer(clientHttp)
    responseHttp = clientHttp.get(
        f"/api/sync/{S_CONTAINER_ID}/check/github",
    )
    assert responseHttp.status_code == 200
    assert "bConnected" in responseHttp.json()


# -----------------------------------------------------------------------
# DAG endpoint
# -----------------------------------------------------------------------


def test_get_dag(clientHttp):
    _fnConnectToContainer(clientHttp)
    responseHttp = clientHttp.get(
        f"/api/workflow/{S_CONTAINER_ID}/dag",
    )
    assert responseHttp.status_code == 200


# -----------------------------------------------------------------------
# File status
# -----------------------------------------------------------------------


def test_get_file_status(clientHttp):
    _fnConnectToContainer(clientHttp)
    responseHttp = clientHttp.get(
        f"/api/pipeline/{S_CONTAINER_ID}/file-status",
    )
    assert responseHttp.status_code == 200
    assert "dictModTimes" in responseHttp.json()


# -----------------------------------------------------------------------
# Helper functions tested directly
# -----------------------------------------------------------------------


def test_fdictHandleConnect_error():
    mockDocker = MagicMock()
    mockDocker.ftResultExecuteCommand.side_effect = (
        RuntimeError("fail")
    )
    dictCtx = {
        "docker": mockDocker,
        "workflows": {},
        "setAllowedContainers": set(),
        "paths": {},
    }
    with pytest.raises(Exception):
        pipelineServer.fdictHandleConnect(
            dictCtx, "bad-id", "/wf.json"
        )


def test_flistCollectOutputPaths():
    dictWorkflow = {
        "sPlotDirectory": "Plot",
        "sFigureType": "pdf",
        "listSteps": [
            {
                "sDirectory": "/workspace/step1",
                "saDataFiles": ["data.npy"],
                "saPlotFiles": [
                    "{sPlotDirectory}/fig.{sFigureType}"
                ],
            },
        ],
    }
    listPaths = pipelineServer._flistCollectOutputPaths(
        dictWorkflow
    )
    assert "/workspace/step1/data.npy" in listPaths
    assert "/workspace/step1/Plot/fig.pdf" in listPaths


def test_fdictGetModTimes_empty():
    mockDocker = MagicMock()
    dictResult = pipelineServer._fdictGetModTimes(
        mockDocker, "cid", []
    )
    assert dictResult == {}


def test_fdictGetModTimes_parses_output():
    mockDocker = MagicMock()
    mockDocker.ftResultExecuteCommand.return_value = (
        0, "/workspace/a.npy 1700000000\n"
    )
    dictResult = pipelineServer._fdictGetModTimes(
        mockDocker, "cid", ["/workspace/a.npy"]
    )
    assert dictResult["/workspace/a.npy"] == "1700000000"


def test_fnRemoveTestFiles():
    mockDocker = MagicMock()
    mockDocker.ftResultExecuteCommand.return_value = (0, "")
    dictStep = {"sDirectory": "/workspace/step1"}
    pipelineServer._fnRemoveTestFiles(
        mockDocker, "cid", dictStep, 0
    )
    mockDocker.ftResultExecuteCommand.assert_called_once()


def test_fsDetectDockerRuntime_unknown():
    with patch("subprocess.run") as mockRun:
        mockRun.side_effect = Exception("no docker")
        dictResult = pipelineServer.fsDetectDockerRuntime()
    assert dictResult["sRuntime"] == "unknown"


def test_fsDetectDockerRuntime_colima():
    with patch("subprocess.run") as mockRun:
        mockResult = MagicMock()
        mockResult.stdout = "colima:true\ndefault:false\n"
        mockRun.return_value = mockResult
        dictResult = pipelineServer.fsDetectDockerRuntime()
    assert dictResult["sRuntime"] == "colima"


def test_fsDetectDockerRuntime_orbstack():
    with patch("subprocess.run") as mockRun:
        mockResult = MagicMock()
        mockResult.stdout = "orbstack:true\n"
        mockRun.return_value = mockResult
        dictResult = pipelineServer.fsDetectDockerRuntime()
    assert dictResult["sRuntime"] == "orbstack"


def test_fsRequireWorkflowPath_missing():
    with pytest.raises(Exception):
        pipelineServer.fsRequireWorkflowPath({}, "cid")


def test_fsRequireWorkflowPath_found():
    sResult = pipelineServer.fsRequireWorkflowPath(
        {"cid": "/path/wf.json"}, "cid"
    )
    assert sResult == "/path/wf.json"


def test_fdictResolveVariables_empty():
    dictResult = pipelineServer.fdictResolveVariables(
        {}, {}, "cid"
    )
    assert dictResult == {}


def test_fnRequireDocker_raises_on_none():
    with pytest.raises(Exception):
        pipelineServer._fnRequireDocker(None)


def test_fdictBuildContext_returns_all_keys():
    mockDocker = MagicMock()
    dictCtx = pipelineServer.fdictBuildContext(mockDocker)
    listRequired = [
        "docker", "workflows", "paths", "terminals",
        "require", "save", "variables", "workflowDir",
    ]
    for sKey in listRequired:
        assert sKey in dictCtx


def test_fnDispatchAction_verify():
    """Test that fnDispatchAction routes verify."""
    import asyncio

    listCaptured = []

    async def fnCallback(dictEvent):
        listCaptured.append(dictEvent)

    mockDocker = MagicMock()
    mockDocker.ftResultExecuteCommand.return_value = (0, "")
    mockDocker.fbaFetchFile.return_value = json.dumps({
        "sWorkflowName": "Test",
        "listSteps": [],
    }).encode("utf-8")
    mockDocker.fnWriteFile = MagicMock()

    with patch(
        "vaibify.gui.workflowManager."
        "flistFindWorkflowsInContainer",
        return_value=[{"sPath": "/wf.json"}],
    ), patch(
        "vaibify.gui.workflowManager."
        "fdictLoadWorkflowFromContainer",
        return_value={"sWorkflowName": "Test", "listSteps": []},
    ):
        asyncio.get_event_loop().run_until_complete(
            pipelineServer.fnDispatchAction(
                "verify", {},
                mockDocker, "cid", {}, {},
                "/workspace", fnCallback,
            )
        )
    assert any(
        d.get("sType") == "completed" for d in listCaptured
    )
