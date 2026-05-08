"""Unit tests for workflowReloadDetector.

Covers the four behaviours that matter for the dashboard's
ground-truth contract:

- self-write mtimes silence subsequent polls
- divergent mtimes trigger a reload
- malformed JSON / missing files surface as ``sError`` without crashing
- the project-repo path is re-derived via the in-container git probe
  on reload (mirroring connect-time semantics)
"""

import json
from unittest.mock import patch

import pytest

from vaibify.gui import workflowReloadDetector


_S_CONTAINER_ID = "test-container"
_S_WORKFLOW_PATH = "/workspace/proj/.vaibify/workflows/demo.json"
_S_REPO_PATH = "/workspace/proj"


class _FakeDocker:
    """Minimal docker-connection fake for the reload detector.

    Records stat calls and returns mtime strings keyed by path. Every
    other interaction (fbaFetchFile, ftResultExecuteCommand) is unused
    here because the loader is patched out of the units under test.
    """

    def __init__(self):
        self.dictMtimes = {}
        self.listStatCommands = []

    def fnSetMtime(self, sPath, sMtime):
        self.dictMtimes[sPath] = sMtime

    def ftResultExecuteCommand(self, sContainerId, sCommand):
        self.listStatCommands.append(sCommand)
        if not sCommand.startswith("stat -c '%n %Y' "):
            return (0, "")
        listLines = []
        for sPath, sMtime in self.dictMtimes.items():
            if "'" + sPath + "'" in sCommand:
                listLines.append(f"{sPath} {sMtime}")
        return (0, "\n".join(listLines))


def _fdictMakeContext(connectionDocker):
    return {
        "docker": connectionDocker,
        "workflows": {},
        "lastSelfWriteMtimes": {},
    }


def _fdictMakeWorkflow(sName="demo", listSteps=None):
    return {
        "sWorkflowName": sName,
        "sPath": _S_WORKFLOW_PATH,
        "sProjectRepoPath": _S_REPO_PATH,
        "listSteps": listSteps or [],
    }


# ---------- fnRecordSelfWriteMtime ----------


def test_record_self_write_mtime_stores_polled_value():
    fakeDocker = _FakeDocker()
    fakeDocker.fnSetMtime(_S_WORKFLOW_PATH, "1700000000")
    dictCtx = _fdictMakeContext(fakeDocker)
    workflowReloadDetector.fnRecordSelfWriteMtime(
        dictCtx, _S_CONTAINER_ID, _S_WORKFLOW_PATH,
    )
    assert (
        dictCtx["lastSelfWriteMtimes"][_S_CONTAINER_ID]
        == "1700000000"
    )


def test_record_self_write_mtime_handles_empty_path():
    fakeDocker = _FakeDocker()
    dictCtx = _fdictMakeContext(fakeDocker)
    workflowReloadDetector.fnRecordSelfWriteMtime(
        dictCtx, _S_CONTAINER_ID, "",
    )
    assert dictCtx["lastSelfWriteMtimes"] == {}
    assert fakeDocker.listStatCommands == []


def test_record_self_write_mtime_initializes_map_when_missing():
    fakeDocker = _FakeDocker()
    fakeDocker.fnSetMtime(_S_WORKFLOW_PATH, "1700000000")
    dictCtx = {"docker": fakeDocker, "workflows": {}}
    workflowReloadDetector.fnRecordSelfWriteMtime(
        dictCtx, _S_CONTAINER_ID, _S_WORKFLOW_PATH,
    )
    assert "lastSelfWriteMtimes" in dictCtx
    assert (
        dictCtx["lastSelfWriteMtimes"][_S_CONTAINER_ID]
        == "1700000000"
    )


# ---------- fdictMaybeReloadWorkflow ----------


def test_no_reload_when_polled_mtime_matches_self_write():
    fakeDocker = _FakeDocker()
    dictCtx = _fdictMakeContext(fakeDocker)
    dictCtx["lastSelfWriteMtimes"][_S_CONTAINER_ID] = "1700000000"
    dictReload = workflowReloadDetector.fdictMaybeReloadWorkflow(
        dictCtx, _S_CONTAINER_ID, _S_WORKFLOW_PATH,
        {_S_WORKFLOW_PATH: "1700000000"},
    )
    assert dictReload == {
        "bReplaced": False, "dictWorkflow": None, "sError": None,
    }


def test_reload_when_polled_mtime_diverges():
    fakeDocker = _FakeDocker()
    dictCtx = _fdictMakeContext(fakeDocker)
    dictCtx["lastSelfWriteMtimes"][_S_CONTAINER_ID] = "1700000000"
    dictNewWorkflow = _fdictMakeWorkflow(
        sName="updated", listSteps=[{"sDirectory": "stepA"}],
    )
    with patch(
        "vaibify.gui.workflowReloadDetector.workflowManager"
        ".fdictLoadWorkflowFromContainer",
        return_value=dictNewWorkflow,
    ), patch(
        "vaibify.gui.containerGit.fsDetectProjectRepoInContainer",
        return_value=_S_REPO_PATH,
    ):
        dictReload = workflowReloadDetector.fdictMaybeReloadWorkflow(
            dictCtx, _S_CONTAINER_ID, _S_WORKFLOW_PATH,
            {_S_WORKFLOW_PATH: "1700000099"},
        )
    assert dictReload["bReplaced"] is True
    assert dictReload["sError"] is None
    assert dictReload["dictWorkflow"]["sWorkflowName"] == "updated"
    assert (
        dictCtx["workflows"][_S_CONTAINER_ID]
        is dictReload["dictWorkflow"]
    )
    assert (
        dictCtx["lastSelfWriteMtimes"][_S_CONTAINER_ID]
        == "1700000099"
    )


def test_reload_handles_malformed_json():
    fakeDocker = _FakeDocker()
    dictCtx = _fdictMakeContext(fakeDocker)
    dictPriorWorkflow = _fdictMakeWorkflow(sName="prior")
    dictCtx["workflows"][_S_CONTAINER_ID] = dictPriorWorkflow
    dictCtx["lastSelfWriteMtimes"][_S_CONTAINER_ID] = "1700000000"
    with patch(
        "vaibify.gui.workflowReloadDetector.workflowManager"
        ".fdictLoadWorkflowFromContainer",
        side_effect=ValueError("Invalid workflow.json: bad shape"),
    ):
        dictReload = workflowReloadDetector.fdictMaybeReloadWorkflow(
            dictCtx, _S_CONTAINER_ID, _S_WORKFLOW_PATH,
            {_S_WORKFLOW_PATH: "1700000099"},
        )
    assert dictReload["bReplaced"] is False
    assert "Invalid workflow.json" in dictReload["sError"]
    assert dictCtx["workflows"][_S_CONTAINER_ID] is dictPriorWorkflow
    assert (
        dictCtx["lastSelfWriteMtimes"][_S_CONTAINER_ID]
        == "1700000000"
    )


def test_reload_handles_missing_file_in_modtimes():
    fakeDocker = _FakeDocker()
    dictCtx = _fdictMakeContext(fakeDocker)
    dictReload = workflowReloadDetector.fdictMaybeReloadWorkflow(
        dictCtx, _S_CONTAINER_ID, _S_WORKFLOW_PATH,
        {},
    )
    assert dictReload["bReplaced"] is False
    assert "missing" in dictReload["sError"].lower()


def test_reload_handles_empty_workflow_path():
    fakeDocker = _FakeDocker()
    dictCtx = _fdictMakeContext(fakeDocker)
    dictReload = workflowReloadDetector.fdictMaybeReloadWorkflow(
        dictCtx, _S_CONTAINER_ID, "",
        {_S_WORKFLOW_PATH: "1700000099"},
    )
    assert dictReload == {
        "bReplaced": False, "dictWorkflow": None, "sError": None,
    }


def test_reload_re_derives_project_repo_path_via_container_git():
    fakeDocker = _FakeDocker()
    dictCtx = _fdictMakeContext(fakeDocker)
    dictCtx["lastSelfWriteMtimes"][_S_CONTAINER_ID] = "1700000000"
    dictNewWorkflow = _fdictMakeWorkflow()
    dictNewWorkflow["sProjectRepoPath"] = "/will-be-overridden"
    with patch(
        "vaibify.gui.workflowReloadDetector.workflowManager"
        ".fdictLoadWorkflowFromContainer",
        return_value=dictNewWorkflow,
    ), patch(
        "vaibify.gui.containerGit.fsDetectProjectRepoInContainer",
        return_value="/workspace/probed-repo",
    ) as mockProbe:
        workflowReloadDetector.fdictMaybeReloadWorkflow(
            dictCtx, _S_CONTAINER_ID, _S_WORKFLOW_PATH,
            {_S_WORKFLOW_PATH: "1700000099"},
        )
    mockProbe.assert_called_once()
    assert (
        dictCtx["workflows"][_S_CONTAINER_ID]["sProjectRepoPath"]
        == "/workspace/probed-repo"
    )


def test_reload_does_not_re_trigger_on_next_poll():
    """Two divergent mtimes in succession only reload once each."""
    fakeDocker = _FakeDocker()
    dictCtx = _fdictMakeContext(fakeDocker)
    dictCtx["lastSelfWriteMtimes"][_S_CONTAINER_ID] = "1700000000"
    dictNewWorkflow = _fdictMakeWorkflow(sName="updated")
    with patch(
        "vaibify.gui.workflowReloadDetector.workflowManager"
        ".fdictLoadWorkflowFromContainer",
        return_value=dictNewWorkflow,
    ), patch(
        "vaibify.gui.containerGit.fsDetectProjectRepoInContainer",
        return_value=_S_REPO_PATH,
    ):
        dictFirst = workflowReloadDetector.fdictMaybeReloadWorkflow(
            dictCtx, _S_CONTAINER_ID, _S_WORKFLOW_PATH,
            {_S_WORKFLOW_PATH: "1700000099"},
        )
        dictSecond = workflowReloadDetector.fdictMaybeReloadWorkflow(
            dictCtx, _S_CONTAINER_ID, _S_WORKFLOW_PATH,
            {_S_WORKFLOW_PATH: "1700000099"},
        )
    assert dictFirst["bReplaced"] is True
    assert dictSecond["bReplaced"] is False
