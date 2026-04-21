"""Tests for the POST /api/steps/{id}/{idx}/scan-dependencies endpoint."""

import json
import pytest
from unittest.mock import patch
from fastapi.testclient import TestClient

from vaibify.gui import pipelineServer


S_CONTAINER_ID = "dep-scan-container"
S_WORKFLOW_PATH = "/workspace/.vaibify/workflows/test.json"

S_PYTHON_SCRIPT = (
    "import numpy as np\n"
    "import pandas as pd\n"
    "\n"
    'data = np.load("orbit_output.npy")\n'
    'df = pd.read_csv("unknown_file.csv")\n'
)

DICT_WORKFLOW = {
    "sWorkflowName": "Dependency Test",
    "sPlotDirectory": "Plot",
    "sFigureType": "pdf",
    "iNumberOfCores": 4,
    "listSteps": [
        {
            "sName": "Generate Orbits",
            "sDirectory": "orbits",
            "bPlotOnly": False,
            "bEnabled": True,
            "bInteractive": False,
            "saDataCommands": ["python generateOrbits.py"],
            "saDataFiles": ["orbit_output.npy"],
            "saTestCommands": [],
            "saPlotCommands": [],
            "saPlotFiles": [],
            "dictRunStats": {},
            "dictVerification": {
                "sUnitTest": "untested",
                "sUser": "untested",
            },
        },
        {
            "sName": "Analyze Results",
            "sDirectory": "analysis",
            "bPlotOnly": False,
            "bEnabled": True,
            "bInteractive": False,
            "saDataCommands": ["python analyzeResults.py"],
            "saDataFiles": [],
            "saTestCommands": [],
            "saPlotCommands": [],
            "saPlotFiles": [],
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
        self._dictFiles = {
            "analyzeResults.py": S_PYTHON_SCRIPT.encode("utf-8"),
        }

    def flistGetRunningContainers(self):
        return [
            {
                "sContainerId": S_CONTAINER_ID,
                "sShortId": "dep123",
                "sName": "dep-container",
                "sImage": "ubuntu:24.04",
            },
        ]

    def ftResultExecuteCommand(self, sContainerId, sCommand):
        if "find" in sCommand and ".vaibify/workflows" in sCommand:
            return (0, S_WORKFLOW_PATH + "\n")
        if "test -d" in sCommand:
            return (0, "")
        if "stat -c" in sCommand:
            return (0, "")
        if "ps aux" in sCommand:
            return (0, "0\n")
        return (0, "")

    def fbaFetchFile(self, sContainerId, sPath):
        sBasename = sPath.rsplit("/", 1)[-1] if "/" in sPath else sPath
        if sBasename in self._dictFiles:
            return self._dictFiles[sBasename]
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


def _fnConnectToContainer(clientHttp):
    """POST to /api/connect and return the response dict."""
    responseHttp = clientHttp.post(
        f"/api/connect/{S_CONTAINER_ID}",
        params={"sWorkflowPath": S_WORKFLOW_PATH},
    )
    assert responseHttp.status_code == 200
    return responseHttp.json()


# ── Endpoint tests ─────────────────────────────────────────────


def test_scan_dependencies_finds_upstream_match(clientHttp):
    _fnConnectToContainer(clientHttp)
    responseHttp = clientHttp.post(
        f"/api/steps/{S_CONTAINER_ID}/1/scan-dependencies",
        json={"saDataCommands": ["python analyzeResults.py"]},
    )
    assert responseHttp.status_code == 200
    dictResult = responseHttp.json()
    assert "listSuggestions" in dictResult
    assert "listUnmatchedFiles" in dictResult
    listSuggestions = dictResult["listSuggestions"]
    assert len(listSuggestions) >= 1
    dictMatch = listSuggestions[0]
    assert dictMatch["sFileName"] == "orbit_output.npy"
    assert dictMatch["iSourceStep"] == 1
    assert dictMatch["sSourceStepName"] == "Generate Orbits"
    assert "{Step01." in dictMatch["sTemplateVariable"]


def test_scan_dependencies_reports_unmatched(clientHttp):
    _fnConnectToContainer(clientHttp)
    responseHttp = clientHttp.post(
        f"/api/steps/{S_CONTAINER_ID}/1/scan-dependencies",
        json={"saDataCommands": ["python analyzeResults.py"]},
    )
    dictResult = responseHttp.json()
    listUnmatched = dictResult["listUnmatchedFiles"]
    assert len(listUnmatched) >= 1
    assert any(
        d["sFileName"] == "unknown_file.csv" for d in listUnmatched
    )


def test_scan_dependencies_empty_commands(clientHttp):
    _fnConnectToContainer(clientHttp)
    responseHttp = clientHttp.post(
        f"/api/steps/{S_CONTAINER_ID}/1/scan-dependencies",
        json={"saDataCommands": []},
    )
    assert responseHttp.status_code == 200
    dictResult = responseHttp.json()
    assert dictResult["listSuggestions"] == []
    assert dictResult["listUnmatchedFiles"] == []


def test_scan_dependencies_missing_script(clientHttp):
    _fnConnectToContainer(clientHttp)
    responseHttp = clientHttp.post(
        f"/api/steps/{S_CONTAINER_ID}/1/scan-dependencies",
        json={"saDataCommands": ["python nonexistent.py"]},
    )
    assert responseHttp.status_code == 200
    dictResult = responseHttp.json()
    assert dictResult["listSuggestions"] == []
    assert dictResult["listUnmatchedFiles"] == []


def test_scan_dependencies_non_python_command(clientHttp):
    _fnConnectToContainer(clientHttp)
    responseHttp = clientHttp.post(
        f"/api/steps/{S_CONTAINER_ID}/1/scan-dependencies",
        json={"saDataCommands": ["echo hello"]},
    )
    assert responseHttp.status_code == 200
    dictResult = responseHttp.json()
    assert dictResult["listSuggestions"] == []


def test_scan_dependencies_requires_connection(clientHttp):
    responseHttp = clientHttp.post(
        f"/api/steps/{S_CONTAINER_ID}/1/scan-dependencies",
        json={"saDataCommands": ["python test.py"]},
    )
    assert responseHttp.status_code == 404
