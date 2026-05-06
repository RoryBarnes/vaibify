"""Tests that ``/api/git/{id}/badges`` exposes ``sRemoteUrl``.

The remote-URL field powers the picklist's "View on GitHub" deep
link. When ``git remote get-url origin`` succeeds inside the
container, the badges endpoint must return the URL string under
``dictGit.sRemoteUrl``; when no remote is configured (or git fails),
the field must be the empty string. Validation of the returned URL
is the frontend's responsibility — the backend reports what git
says verbatim.
"""

import json
from unittest.mock import MagicMock

from fastapi import FastAPI
from starlette.testclient import TestClient

from vaibify.gui.routes import gitRoutes


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


def _fnMakeExec(sRemoteUrlOutput, iRemoteRc):
    """Return a side-effect function for ftResultExecuteCommand."""
    sPorcelain = "# branch.head main\n# branch.ab +0 -0\n"
    sTrackedJson = json.dumps([
        ".vaibify/workflows/demo.json",
    ]) + "\n"
    sHashesJson = json.dumps({
        ".vaibify/workflows/demo.json": "a" * 40,
    }) + "\n"

    def _fExec(sContainerId, sCommand, **_kw):
        if "remote get-url origin" in sCommand:
            return (iRemoteRc, sRemoteUrlOutput)
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

    return _fExec


def _fdictWorkflow():
    """Return a minimal workflow with a project repo set."""
    return {
        "sPlotDirectory": "Plot",
        "listSteps": [],
        "sProjectRepoPath": "/workspace/DemoRepo",
        "dictSyncStatus": {},
    }


def test_badges_returns_remote_url_when_origin_is_set():
    sRemoteUrl = "https://github.com/example/demo.git\n"
    mockDocker = MagicMock()
    mockDocker.ftResultExecuteCommand.side_effect = _fnMakeExec(
        sRemoteUrl, 0,
    )
    app = _fdictBuildRoutesApp(mockDocker, _fdictWorkflow())
    client = TestClient(app)

    response = client.get("/api/git/cid-demo/badges")

    assert response.status_code == 200
    dictBody = response.json()
    assert dictBody["dictGit"]["sRemoteUrl"] == (
        "https://github.com/example/demo.git"
    )


def test_badges_returns_empty_remote_url_when_no_origin():
    mockDocker = MagicMock()
    mockDocker.ftResultExecuteCommand.side_effect = _fnMakeExec(
        "", 128,
    )
    app = _fdictBuildRoutesApp(mockDocker, _fdictWorkflow())
    client = TestClient(app)

    response = client.get("/api/git/cid-demo/badges")

    assert response.status_code == 200
    dictBody = response.json()
    assert dictBody["dictGit"]["sRemoteUrl"] == ""


def test_badges_remote_url_empty_when_project_repo_missing():
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
    # No-project-repo path returns the explicit empty status that
    # does not include sRemoteUrl. The frontend treats missing or
    # empty identically.
    assert dictBody["dictGit"].get("sRemoteUrl", "") == ""
