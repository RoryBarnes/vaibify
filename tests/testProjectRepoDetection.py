"""Tests for project-repo auto-detection and badge-state integration.

Covers two related concerns:

1. ``containerGit.fsDetectProjectRepoInContainer`` — unit behavior of
   the detector that locates the git work tree enclosing an active
   workflow's ``workflow.json``.
2. The badges route end-to-end — when ``sProjectRepoPath`` is
   populated, ``/api/git/{id}/badges`` hydrates real state; when it
   is empty, the route returns the explicit "not in a git
   repository" payload rather than silently reporting ``bIsRepo:
   False`` against the wrong root.
"""

import json
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient

from vaibify.gui import containerGit
from vaibify.gui.routes import gitRoutes


# ----------------------------------------------------------------------
# fsDetectProjectRepoInContainer — unit level
# ----------------------------------------------------------------------


class _FakeDocker:
    """Minimal docker stub that serves canned (rc, stdout) by marker."""

    def __init__(self, listRules=None):
        self.listCommands = []
        self._listRules = listRules or []

    def ftResultExecuteCommand(self, sContainerId, sCommand, **_kwargs):
        self.listCommands.append(sCommand)
        for sMarker, iRc, sOut in self._listRules:
            if sMarker in sCommand:
                return (iRc, sOut)
        return (0, "")


def test_detect_returns_repo_path_for_workflow_in_repo():
    docker = _FakeDocker([
        ("rev-parse --show-toplevel", 0, "/workspace/DemoRepo\n"),
    ])
    sResult = containerGit.fsDetectProjectRepoInContainer(
        docker, "cid",
        "/workspace/DemoRepo/.vaibify/workflows/demo.json",
    )
    assert sResult == "/workspace/DemoRepo"


def test_detect_returns_empty_when_not_in_repo():
    docker = _FakeDocker([
        ("rev-parse --show-toplevel", 128, ""),
    ])
    sResult = containerGit.fsDetectProjectRepoInContainer(
        docker, "cid", "/workspace/workflow.json",
    )
    assert sResult == ""


# ----------------------------------------------------------------------
# gitRoutes /api/git/{id}/badges — scoped by sProjectRepoPath
# ----------------------------------------------------------------------


def _fdictBuildRoutesApp(mockDocker, dictWorkflow):
    """Build a minimal FastAPI app wired to gitRoutes handlers."""
    app = FastAPI()
    dictCtx = {
        "require": MagicMock(),
        "docker": mockDocker,
        "workflows": {"cid-demo": dictWorkflow},
    }
    gitRoutes.fnRegisterAll(app, dictCtx)
    return app


def test_badges_returns_not_a_repo_when_project_repo_missing():
    dictWorkflow = {
        "sPlotDirectory": "Plot",
        "listSteps": [],
        "sProjectRepoPath": "",
    }
    mockDocker = MagicMock()
    app = _fdictBuildRoutesApp(mockDocker, dictWorkflow)
    client = TestClient(app)

    response = client.get("/api/git/cid-demo/badges")

    assert response.status_code == 200
    dictBody = response.json()
    assert dictBody["dictGit"]["bIsRepo"] is False
    assert "not in a git repository" in dictBody["dictGit"]["sReason"]
    assert dictBody["dictBadges"] == {}
    assert dictBody["listTracked"] == []


def test_badges_hydrates_when_project_repo_is_set():
    sRepo = "/workspace/DemoRepo"
    dictWorkflow = {
        "sPlotDirectory": "Plot",
        "listSteps": [{
            "sName": "Step 1",
            "sDirectory": "analysis",
            "saPlotCommands": [],
            "saPlotFiles": ["output.pdf"],
        }],
        "sProjectRepoPath": sRepo,
        "dictSyncStatus": {},
    }

    sPorcelain = "# branch.head main\n# branch.ab +0 -0\n"
    sTrackedJson = json.dumps([
        ".vaibify/workflows/demo.json",
    ]) + "\n"
    sHashesJson = json.dumps({
        ".vaibify/workflows/demo.json": "a" * 40,
    }) + "\n"

    def _fExec(sContainerId, sCommand, **_kw):
        if "rev-parse --is-inside-work-tree" in sCommand:
            return (0, "true\n")
        if "status --porcelain=v2" in sCommand:
            return (0, sPorcelain)
        if "rev-parse HEAD" in sCommand:
            return (0, "b" * 40 + "\n")
        if "python3 -c" in sCommand and "glob" in sCommand:
            return (0, sTrackedJson)
        if "python3 -c" in sCommand and "hashlib" in sCommand:
            return (0, sHashesJson)
        return (0, "")

    mockDocker = MagicMock()
    mockDocker.ftResultExecuteCommand.side_effect = _fExec

    app = _fdictBuildRoutesApp(mockDocker, dictWorkflow)
    client = TestClient(app)

    response = client.get("/api/git/cid-demo/badges")

    assert response.status_code == 200
    dictBody = response.json()
    assert dictBody["dictGit"]["bIsRepo"] is True
    assert dictBody["dictGit"]["sBranch"] == "main"
    assert ".vaibify/workflows/demo.json" in dictBody["listTracked"]

    # Every containerGit call ran against the project repo, not /workspace.
    listContainerGitCalls = [
        c for c in mockDocker.ftResultExecuteCommand.call_args_list
        if "git" in str(c) or "python3 -c" in str(c)
    ]
    assert listContainerGitCalls, "expected at least one containerGit call"
    for call in listContainerGitCalls:
        sCmd = call.args[1]
        # No /workspace/... path should appear bare — only the
        # project-repo subdirectory should be used as workspace.
        assert "cd /workspace\n" not in sCmd
        assert "cd /workspace &&" not in sCmd


def test_status_returns_empty_status_when_project_repo_missing():
    dictWorkflow = {
        "sPlotDirectory": "Plot",
        "listSteps": [],
        "sProjectRepoPath": "",
    }
    mockDocker = MagicMock()
    app = _fdictBuildRoutesApp(mockDocker, dictWorkflow)
    client = TestClient(app)

    response = client.get("/api/git/cid-demo/status")

    assert response.status_code == 200
    dictBody = response.json()
    assert dictBody["bIsRepo"] is False
    assert "not in a git repository" in dictBody["sReason"]


def test_commit_canonical_409_when_project_repo_missing():
    dictWorkflow = {
        "sPlotDirectory": "Plot",
        "listSteps": [],
        "sProjectRepoPath": "",
    }
    mockDocker = MagicMock()
    app = _fdictBuildRoutesApp(mockDocker, dictWorkflow)
    client = TestClient(app)

    response = client.post(
        "/api/git/cid-demo/commit-canonical",
        json={"sCommitMessage": ""},
    )

    assert response.status_code == 409
    assert "Project repo not detected" in response.json()["detail"]
