"""Tests for editor-draft persistence routes.

The dashboard mirrors textarea edits to disk under
``<sProjectRepoPath>/.vaibify/drafts/<workflowSlug>/`` so unsaved
content survives browser crashes, accidental tab closure, and the
viewer-replacement bug these routes were introduced to neutralize.
"""

import hashlib
import json
import posixpath

import pytest
from fastapi.testclient import TestClient
from unittest.mock import patch

from vaibify.gui import draftManager, pipelineServer


S_CONTAINER_ID = "draftcontainer"
S_WORKFLOW_PATH = "/workspace/.vaibify/workflows/myFlow.json"
S_WORKFLOW_SLUG = "myFlow"
S_PROJECT_REPO = "/workspace"


DICT_WORKFLOW = {
    "sWorkflowName": "Draft Test",
    "sPlotDirectory": "Plot",
    "sFigureType": "pdf",
    "iNumberOfCores": 4,
    "listSteps": [
        {
            "sName": "Step A",
            "sDirectory": "stepA",
            "bPlotOnly": False,
            "bRunEnabled": True,
            "bInteractive": False,
            "saDataCommands": [],
            "saOutputDataFiles": [],
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


class MockDockerDraft:
    """Mock with in-memory filesystem suitable for draft round-trips."""

    def __init__(self):
        self._dictFiles = {}
        self._setDirs = {"/workspace", "/workspace/.vaibify"}

    def flistGetRunningContainers(self):
        return [{
            "sContainerId": S_CONTAINER_ID,
            "sShortId": "draft",
            "sName": "test-container",
            "sImage": "ubuntu:24.04",
        }]

    def ftResultExecuteCommand(
        self, sContainerId, sCommand, sWorkdir=None,
    ):
        if "mkdir -p" in sCommand:
            sPath = sCommand.split("mkdir -p ", 1)[1].strip().strip("'")
            self._setDirs.add(sPath)
            return (0, "")
        if sCommand.startswith("rm -f"):
            sPath = sCommand.split("rm -f ", 1)[1].strip().strip("'")
            self._dictFiles.pop(sPath, None)
            return (0, "")
        if sCommand.startswith("find ") and "drafts" in sCommand:
            sDir = sCommand.split("find ", 1)[1].split(" ", 1)[0]
            sDir = sDir.strip().strip("'")
            listMatches = [
                sPath for sPath in self._dictFiles
                if sPath.startswith(sDir + "/")
                and sPath.endswith(".json")
            ]
            return (0, "\n".join(listMatches))
        if sCommand.startswith("find "):
            return (0, "")
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
        if "git rev-parse --show-toplevel" in sCommand:
            return (0, S_PROJECT_REPO + "\n")
        return (0, "")

    def fbaFetchFile(self, sContainerId, sPath, iMaxBytes=None):
        if sPath in self._dictFiles:
            return self._dictFiles[sPath]
        if sPath == S_WORKFLOW_PATH:
            return json.dumps(DICT_WORKFLOW).encode("utf-8")
        raise FileNotFoundError(f"Not found: {sPath}")

    def fnWriteFile(
        self, sContainerId, sPath, baContent,
        iMode=None, iUid=None, iGid=None,
    ):
        self._dictFiles[sPath] = baContent

    def texecRunInContainerStreamed(
        self, sContainerId, sCommand, sWorkdir=None, sUser=None,
    ):
        from types import SimpleNamespace
        iExit, sOutput = self.ftResultExecuteCommand(
            sContainerId, sCommand,
        )
        return SimpleNamespace(
            iExitCode=iExit, sStdout=sOutput, sStderr="",
        )

    def fnWriteFileViaTar(
        self, sContainerId, sPath, baContent,
        iMode=None, iUid=None, iGid=None,
    ):
        self._dictFiles[sPath] = baContent

    def fsExecCreate(self, sContainerId, sCommand=None, sUser=None):
        return "exec-id-mock"

    def fsocketExecStart(self, sExecId):
        return None

    def fnExecResize(self, sExecId, iRows, iColumns):
        pass


def _fmockCreateDocker():
    return MockDockerDraft()


@pytest.fixture
def clientHttp():
    with patch.object(
        pipelineServer, "_fconnectionCreateDocker", _fmockCreateDocker,
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
        f"/api/connect/{S_CONTAINER_ID}",
        params={"sWorkflowPath": S_WORKFLOW_PATH},
    )
    assert response.status_code == 200, response.text


# ── draftManager unit tests ────────────────────────────────────


def test_fsHashContent_stable():
    assert (
        draftManager.fsHashContent("hello")
        == draftManager.fsHashContent("hello")
    )
    assert (
        draftManager.fsHashContent("hello")
        != draftManager.fsHashContent("hellp")
    )


def test_fsDraftFilename_unique_per_pair():
    sA = draftManager.fsDraftFilename("foo.py", "")
    sB = draftManager.fsDraftFilename("foo.py", "step1")
    sC = draftManager.fsDraftFilename("foo.py", "")
    assert sA == sC
    assert sA != sB
    assert sA.endswith(".json")


def test_fsDraftPath_namespaces_by_workflow_slug():
    sPath = draftManager.fsDraftPath(
        "/repo", "/repo/.vaibify/workflows/myFlow.json",
        "src/foo.py", "",
    )
    assert "/repo/.vaibify/drafts/myFlow/" in sPath
    assert sPath.endswith(".json")


def test_fsDraftDirectory_empty_when_inputs_missing():
    assert draftManager.fsDraftDirectory("", "/x.json") == ""
    assert draftManager.fsDraftDirectory("/r", "") == ""


def test_fdictParseDraftPayload_round_trips():
    sJson = draftManager.fjsonBuildDraftPayload(
        "src/x.py", "step1", "abc", "hash1",
    )
    dictParsed = draftManager.fdictParseDraftPayload(sJson)
    assert dictParsed["sFilePath"] == "src/x.py"
    assert dictParsed["sWorkdir"] == "step1"
    assert dictParsed["sContent"] == "abc"
    assert dictParsed["sBaseHash"] == "hash1"
    assert dictParsed["iTimestampMs"] > 0


# ── PUT /api/draft round-trips with GET ────────────────────────


def test_draft_write_then_read(clientHttp):
    _fnConnect(clientHttp)
    sContent = "x = 42\nprint(x)\n"
    sBaseHash = draftManager.fsHashContent("x = 0\n")
    responsePut = clientHttp.put(
        f"/api/draft/{S_CONTAINER_ID}/workspace/src/foo.py",
        json={
            "sContent": sContent,
            "sBaseHash": sBaseHash,
            "sWorkdir": "stepA",
        },
    )
    assert responsePut.status_code == 200, responsePut.text
    assert responsePut.json()["bSuccess"] is True

    responseGet = clientHttp.get(
        f"/api/draft/{S_CONTAINER_ID}/workspace/src/foo.py",
        params={"sWorkdir": "stepA"},
    )
    assert responseGet.status_code == 200
    dictDraft = responseGet.json()
    assert dictDraft["bExists"] is True
    assert dictDraft["sContent"] == sContent
    assert dictDraft["sBaseHash"] == sBaseHash
    assert dictDraft["sWorkdir"] == "stepA"


def test_draft_read_missing_returns_bExists_false(clientHttp):
    _fnConnect(clientHttp)
    response = clientHttp.get(
        f"/api/draft/{S_CONTAINER_ID}/workspace/nothing.py",
    )
    assert response.status_code == 200
    assert response.json() == {"bExists": False}


def test_draft_delete_clears_existing(clientHttp):
    _fnConnect(clientHttp)
    sUrl = f"/api/draft/{S_CONTAINER_ID}/workspace/src/foo.py"
    clientHttp.put(sUrl, json={"sContent": "abc", "sBaseHash": ""})
    responseDelete = clientHttp.delete(sUrl)
    assert responseDelete.status_code == 200
    responseGet = clientHttp.get(sUrl)
    assert responseGet.json()["bExists"] is False


def test_draft_delete_missing_succeeds(clientHttp):
    _fnConnect(clientHttp)
    response = clientHttp.delete(
        f"/api/draft/{S_CONTAINER_ID}/workspace/nope.py",
    )
    assert response.status_code == 200


def test_draft_write_rejected_when_no_workflow_path(clientHttp):
    response = clientHttp.put(
        f"/api/draft/{S_CONTAINER_ID}/workspace/src/foo.py",
        json={"sContent": "abc"},
    )
    assert response.status_code in (400, 503)


def test_draft_write_rejects_oversize_payload(clientHttp):
    _fnConnect(clientHttp)
    sHuge = "x" * (draftManager.I_MAX_DRAFT_CONTENT_BYTES + 1)
    response = clientHttp.put(
        f"/api/draft/{S_CONTAINER_ID}/workspace/src/foo.py",
        json={"sContent": sHuge},
    )
    assert response.status_code == 413


def test_draft_list_returns_all_drafts(clientHttp):
    _fnConnect(clientHttp)
    clientHttp.put(
        f"/api/draft/{S_CONTAINER_ID}/workspace/src/one.py",
        json={"sContent": "one"},
    )
    clientHttp.put(
        f"/api/draft/{S_CONTAINER_ID}/workspace/src/two.py",
        json={"sContent": "two"},
    )
    response = clientHttp.get(f"/api/drafts/{S_CONTAINER_ID}")
    assert response.status_code == 200
    listDrafts = response.json()["listDrafts"]
    setContents = {dictItem["sContent"] for dictItem in listDrafts}
    assert setContents == {"one", "two"}


def test_draft_path_lives_under_vaibify_drafts(clientHttp):
    _fnConnect(clientHttp)
    response = clientHttp.put(
        f"/api/draft/{S_CONTAINER_ID}/workspace/src/foo.py",
        json={"sContent": "abc"},
    )
    sPath = response.json()["sPath"]
    sExpectedDir = posixpath.join(
        S_PROJECT_REPO, ".vaibify", "drafts", S_WORKFLOW_SLUG,
    )
    assert sPath.startswith(sExpectedDir + "/")
    assert sPath.endswith(".json")


# ── PUT /api/file conflict detection ───────────────────────────


def test_file_write_succeeds_without_base_hash(clientHttp):
    _fnConnect(clientHttp)
    response = clientHttp.put(
        f"/api/file/{S_CONTAINER_ID}/workspace/src/conflict.py",
        json={"sContent": "fresh content"},
    )
    assert response.status_code == 200


def test_file_write_succeeds_when_base_hash_matches(clientHttp):
    _fnConnect(clientHttp)
    sBaseContent = "x = 1\n"
    sPath = "workspace/src/match.py"
    clientHttp.put(
        f"/api/file/{S_CONTAINER_ID}/{sPath}",
        json={"sContent": sBaseContent},
    )
    sBaseHash = hashlib.sha256(sBaseContent.encode("utf-8")).hexdigest()
    response = clientHttp.put(
        f"/api/file/{S_CONTAINER_ID}/{sPath}",
        json={"sContent": "x = 2\n", "sBaseHash": sBaseHash},
    )
    assert response.status_code == 200


def test_file_write_409_when_base_hash_diverges(clientHttp):
    _fnConnect(clientHttp)
    sPath = "workspace/src/diverged.py"
    clientHttp.put(
        f"/api/file/{S_CONTAINER_ID}/{sPath}",
        json={"sContent": "current\n"},
    )
    sStaleHash = hashlib.sha256(b"different\n").hexdigest()
    response = clientHttp.put(
        f"/api/file/{S_CONTAINER_ID}/{sPath}",
        json={
            "sContent": "user version\n",
            "sBaseHash": sStaleHash,
        },
    )
    assert response.status_code == 409
    dictDetail = response.json()["detail"]
    assert dictDetail["sCurrentContent"] == "current\n"
    assert (
        dictDetail["sCurrentHash"]
        == hashlib.sha256(b"current\n").hexdigest()
    )


def test_file_write_409_handles_missing_file_as_empty_base(clientHttp):
    _fnConnect(clientHttp)
    sPath = "workspace/src/brandnew.py"
    sStaleHash = hashlib.sha256(b"never existed").hexdigest()
    response = clientHttp.put(
        f"/api/file/{S_CONTAINER_ID}/{sPath}",
        json={
            "sContent": "user version\n",
            "sBaseHash": sStaleHash,
        },
    )
    assert response.status_code == 409
    assert response.json()["detail"]["sCurrentContent"] == ""
