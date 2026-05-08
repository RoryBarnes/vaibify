"""End-to-end integration test for the out-of-band workflow.json reload.

Drives the FastAPI app with a stable mock docker connection that
responds to stat (so mtimes flow through the real polling batch) and
fbaFetchFile (so the reload helper can re-read workflow.json). The
goal is to prove the file-status endpoint detects an out-of-band edit,
silences self-writes, and surfaces malformed JSON or deletion as a
warning rather than crashing the polling loop.
"""

import json
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from vaibify.gui import pipelineServer


_S_CONTAINER_ID = "test-container-reload"
_S_REPO_PATH = "/workspace/proj"
_S_WORKFLOW_PATH = "/workspace/proj/.vaibify/workflows/demo.json"


def _fdictBaseWorkflow(sName="demo", listSteps=None):
    return {
        "sWorkflowName": sName,
        "sPlotDirectory": "Plot",
        "sFigureType": "pdf",
        "iNumberOfCores": 4,
        "listSteps": listSteps if listSteps is not None else [],
    }


class _MockDocker:
    """Mock docker connection that supports the reload integration test.

    Tracks per-path mtimes that callers can bump to simulate writes,
    and stores workflow.json bytes that callers can mutate directly to
    simulate out-of-band edits.
    """

    def __init__(self):
        self.dictMtimes = {_S_WORKFLOW_PATH: "1700000000"}
        self.dictFiles = {
            _S_WORKFLOW_PATH: json.dumps(_fdictBaseWorkflow())
            .encode("utf-8"),
        }

    # --- helpers used by the test, not the route ---

    def fnSetWorkflowBytes(self, baContent, sMtime):
        """Simulate an out-of-band edit: new bytes, new mtime."""
        self.dictFiles[_S_WORKFLOW_PATH] = baContent
        self.dictMtimes[_S_WORKFLOW_PATH] = sMtime

    def fnDeleteWorkflow(self):
        self.dictFiles.pop(_S_WORKFLOW_PATH, None)
        self.dictMtimes.pop(_S_WORKFLOW_PATH, None)

    # --- DockerConnection-shaped methods ---

    def flistGetRunningContainers(self):
        return [
            {
                "sContainerId": _S_CONTAINER_ID,
                "sShortId": _S_CONTAINER_ID[:12],
                "sName": "test-container",
                "sImage": "ubuntu:24.04",
            },
        ]

    def fbaFetchFile(self, sContainerId, sPath):
        if sPath in self.dictFiles:
            return self.dictFiles[sPath]
        raise FileNotFoundError(sPath)

    def fnWriteFile(self, sContainerId, sPath, baContent):
        self.dictFiles[sPath] = baContent
        # Simulate a slightly-later mtime on every write
        sCurrent = self.dictMtimes.get(sPath, "1700000000")
        self.dictMtimes[sPath] = str(int(sCurrent) + 1)

    def ftResultExecuteCommand(self, sContainerId, sCommand):
        if sCommand.startswith("stat -c '%n %Y' "):
            return (0, self._fsBuildStatLines(sCommand))
        if "find" in sCommand and ".vaibify/workflows" in sCommand:
            return (0, _S_WORKFLOW_PATH + "\n")
        if sCommand.startswith("test -d"):
            return (0, "")
        if "git -C" in sCommand and "rev-parse" in sCommand:
            return (0, _S_REPO_PATH + "\n")
        if "ps aux" in sCommand:
            return (0, "0\n")
        if sCommand.startswith("cat ") or sCommand.startswith(
            "cat /"
        ):
            return (1, "")
        return (0, "")

    def _fsBuildStatLines(self, sCommand):
        listLines = []
        for sPath, sMtime in self.dictMtimes.items():
            if "'" + sPath + "'" in sCommand:
                listLines.append(f"{sPath} {sMtime}")
        return "\n".join(listLines)

    def fsExecCreate(self, sContainerId, sCommand=None, sUser=None):
        return "exec-id-mock"

    def fsocketExecStart(self, sExecId):
        return None

    def fnExecResize(self, sExecId, iRows, iColumns):
        pass


@pytest.fixture
def fixtureMock():
    mock = _MockDocker()
    return mock


@pytest.fixture
def clientHttp(fixtureMock):
    with patch.object(
        pipelineServer, "_fconnectionCreateDocker",
        lambda: fixtureMock,
    ):
        app = pipelineServer.fappCreateApplication(
            sWorkspaceRoot="/workspace",
            sTerminalUserArg="testuser",
        )
    return TestClient(
        app, headers={"X-Session-Token": app.state.sSessionToken},
    )


def _fnConnect(clientHttp):
    response = clientHttp.post(
        f"/api/connect/{_S_CONTAINER_ID}",
        params={"sWorkflowPath": _S_WORKFLOW_PATH},
    )
    assert response.status_code == 200
    return response.json()


def _fdictPollFileStatus(clientHttp):
    response = clientHttp.get(
        f"/api/pipeline/{_S_CONTAINER_ID}/file-status"
    )
    assert response.status_code == 200
    return response.json()


# ---------- tests ----------


def test_first_poll_after_connect_does_not_trigger_reload(
    clientHttp, fixtureMock,
):
    """After connect, the cached mtime equals the polled mtime, so
    the very first poll must not flag a reload."""
    _fnConnect(clientHttp)
    dictBody = _fdictPollFileStatus(clientHttp)
    assert dictBody["bWorkflowReloaded"] is False
    assert dictBody["sWorkflowReloadError"] is None


def test_out_of_band_edit_triggers_reload(clientHttp, fixtureMock):
    """A new mtime + new content reloads the cache and surfaces
    the new workflow in the file-status response."""
    _fnConnect(clientHttp)
    dictMutated = _fdictBaseWorkflow(
        sName="mutated",
        listSteps=[
            {
                "sName": "Mutated Step",
                "sDirectory": "stepM",
                "saPlotCommands": [],
                "saPlotFiles": [],
            },
        ],
    )
    fixtureMock.fnSetWorkflowBytes(
        json.dumps(dictMutated).encode("utf-8"),
        "1700000099",
    )
    dictBody = _fdictPollFileStatus(clientHttp)
    assert dictBody["bWorkflowReloaded"] is True
    assert dictBody["sWorkflowReloadError"] is None
    assert (
        dictBody["dictWorkflow"]["sWorkflowName"] == "mutated"
    )
    assert (
        dictBody["dictWorkflow"]["listSteps"][0]["sName"]
        == "Mutated Step"
    )


def test_subsequent_poll_does_not_re_trigger_reload(
    clientHttp, fixtureMock,
):
    """Once a reload absorbs an out-of-band edit, the next poll at the
    same mtime is a no-op."""
    _fnConnect(clientHttp)
    fixtureMock.fnSetWorkflowBytes(
        json.dumps(_fdictBaseWorkflow(sName="mutated"))
        .encode("utf-8"),
        "1700000099",
    )
    dictFirst = _fdictPollFileStatus(clientHttp)
    assert dictFirst["bWorkflowReloaded"] is True
    dictSecond = _fdictPollFileStatus(clientHttp)
    assert dictSecond["bWorkflowReloaded"] is False


def test_malformed_json_surfaces_warning_without_replacing(
    clientHttp, fixtureMock,
):
    """Garbage bytes at a new mtime: the response carries an error
    but does not replace the cache."""
    _fnConnect(clientHttp)
    fixtureMock.fnSetWorkflowBytes(b"not json at all", "1700000099")
    dictBody = _fdictPollFileStatus(clientHttp)
    assert dictBody["bWorkflowReloaded"] is False
    assert dictBody["sWorkflowReloadError"] is not None
    assert dictBody["dictWorkflow"] is None


def test_deleted_file_surfaces_warning(clientHttp, fixtureMock):
    """Deleting workflow.json out-of-band: error surfaces; no crash."""
    _fnConnect(clientHttp)
    fixtureMock.fnDeleteWorkflow()
    dictBody = _fdictPollFileStatus(clientHttp)
    assert dictBody["bWorkflowReloaded"] is False
    assert dictBody["sWorkflowReloadError"] is not None


# ---------- toolkit-mode workflow discovery ----------


def _fnConnectNoWorkflow(clientHttp):
    response = clientHttp.post(f"/api/connect/{_S_CONTAINER_ID}")
    assert response.status_code == 200
    return response.json()


def _fdictPollDiscovery(clientHttp):
    response = clientHttp.get(
        f"/api/pipeline/{_S_CONTAINER_ID}/workflow-discovery"
    )
    assert response.status_code == 200
    return response.json()


def _fdictMakeListing(sPath, sName):
    return {
        "sPath": sPath,
        "sName": sName,
        "sRepoName": "proj",
        "sProjectRepoPath": _S_REPO_PATH,
    }


def test_toolkit_first_discovery_seeds_cache_silently(clientHttp):
    """Toolkit-mode connect → first /workflow-discovery is silent."""
    _fnConnectNoWorkflow(clientHttp)
    listFound = [_fdictMakeListing(_S_WORKFLOW_PATH, "demo")]
    with patch(
        "vaibify.gui.workflowReloadDetector.workflowManager"
        ".flistFindWorkflowsInContainer",
        return_value=listFound,
    ):
        dictBody = _fdictPollDiscovery(clientHttp)
    assert dictBody["bWorkflowsChanged"] is False
    assert dictBody["listNewWorkflowPaths"] == []
    assert len(dictBody["listAvailableWorkflows"]) == 1


def test_toolkit_new_workflow_appears_after_seeding(clientHttp):
    """A workflow that appears between polls is reported."""
    _fnConnectNoWorkflow(clientHttp)
    sNewPath = "/workspace/proj/.vaibify/workflows/agent.json"
    listFirst = []
    listSecond = [_fdictMakeListing(sNewPath, "agent")]
    with patch(
        "vaibify.gui.workflowReloadDetector.workflowManager"
        ".flistFindWorkflowsInContainer",
        side_effect=[listFirst, listSecond],
    ):
        _fdictPollDiscovery(clientHttp)
        dictBody = _fdictPollDiscovery(clientHttp)
    assert dictBody["bWorkflowsChanged"] is True
    assert dictBody["listNewWorkflowPaths"] == [sNewPath]
    assert len(dictBody["listAvailableWorkflows"]) == 1
    assert (
        dictBody["listAvailableWorkflows"][0]["sName"] == "agent"
    )


def test_toolkit_steady_state_quiet_after_seen(clientHttp):
    """Two identical polls in a row: the second is quiet."""
    _fnConnectNoWorkflow(clientHttp)
    listFound = [_fdictMakeListing(_S_WORKFLOW_PATH, "demo")]
    with patch(
        "vaibify.gui.workflowReloadDetector.workflowManager"
        ".flistFindWorkflowsInContainer",
        return_value=listFound,
    ):
        _fdictPollDiscovery(clientHttp)
        dictBody = _fdictPollDiscovery(clientHttp)
    assert dictBody["bWorkflowsChanged"] is False
    assert dictBody["listNewWorkflowPaths"] == []
