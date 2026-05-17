"""Regression tests: GitHub push and add-file cd into the project repo.

The old workspace-as-repo model stored workflow.json at
``/workspace/workflow.json`` and used its parent dir (``/workspace``)
as the git working directory. The workspace-as-git-repo migration
moved workflow.json into ``.vaibify/workflows/<name>.json`` inside
the project repo; the workflow file's parent is no longer a repo
root, so ``cd $(dirname workflow.json) && git add <relpath>`` now
fails with "no such directory" on every push. These tests pin the
routes to use ``dictWorkflow.sProjectRepoPath`` instead.
"""

import pytest
from fastapi import HTTPException
from unittest.mock import patch

from vaibify.gui.routes import syncRoutes


def test_helper_returns_project_repo_when_set():
    sResult = syncRoutes._fsRequireProjectRepoForGit(
        {"sProjectRepoPath": "/workspace/myrepo"},
    )
    assert sResult == "/workspace/myrepo"


def test_helper_raises_when_project_repo_missing():
    with pytest.raises(HTTPException) as excInfo:
        syncRoutes._fsRequireProjectRepoForGit({})
    assert excInfo.value.status_code == 409
    assert "git repository" in str(excInfo.value.detail).lower()


def test_helper_raises_on_empty_string_project_repo():
    """Empty string means workflow loaded but isn't under a git repo."""
    with pytest.raises(HTTPException) as excInfo:
        syncRoutes._fsRequireProjectRepoForGit({"sProjectRepoPath": ""})
    assert excInfo.value.status_code == 409


@pytest.fixture
def fixtureCapturedAddFileArgs():
    """Capture (sFilePath, sCommitMessage, sWorkdir) from the dispatcher."""
    return {}


def _fnPatchAddFileToGithub(fixtureCapturedAddFileArgs):
    """Patch the GitHub add-file dispatcher to capture args, not run git."""
    def _fnFakeAddFile(
        connectionDocker, sContainerId,
        sFilePath, sCommitMessage, sWorkdir,
    ):
        fixtureCapturedAddFileArgs["sFilePath"] = sFilePath
        fixtureCapturedAddFileArgs["sCommitMessage"] = sCommitMessage
        fixtureCapturedAddFileArgs["sWorkdir"] = sWorkdir
        return (0, "abc1234")
    return patch(
        "vaibify.gui.syncDispatcher.ftResultAddFileToGithub",
        side_effect=_fnFakeAddFile,
    )


def _fdictBuildContextWithRepoAt(sProjectRepoPath, sWorkflowPath):
    """Mimic the per-request dictCtx the route reads from."""
    dictWorkflow = {
        "sProjectRepoPath": sProjectRepoPath,
        "sWorkflowName": "demo",
        "listSteps": [],
    }
    return {
        "workflows": {"cid": dictWorkflow},
        "paths": {"cid": sWorkflowPath},
        "require": lambda: None,
        "save": lambda sId, dictWf: None,
        "docker": object(),
    }


def _fnRunAddFileRoute(
    dictCtx, sFilePathInRequest, fixtureCapturedAddFileArgs,
):
    """Wire up the add-file route handler against dictCtx and invoke it."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    app = FastAPI()
    app.state.listLifespanStartup = []
    app.state.listLifespanShutdown = []
    syncRoutes.fnRegisterAll(app, dictCtx)
    with _fnPatchAddFileToGithub(fixtureCapturedAddFileArgs), patch(
        "vaibify.gui.routes.syncRoutes.fnValidatePathWithinRoot",
    ):
        return TestClient(app).post(
            "/api/github/cid/add-file",
            json={"sFilePath": sFilePathInRequest},
        )


def test_add_file_uses_project_repo_path_not_workflow_dirname(
    fixtureCapturedAddFileArgs,
):
    """sWorkdir handed to the dispatcher must be the project repo root."""
    dictCtx = _fdictBuildContextWithRepoAt(
        "/workspace/myrepo",
        "/workspace/myrepo/.vaibify/workflows/demo.json",
    )
    responseHttp = _fnRunAddFileRoute(
        dictCtx, "step01/output.dat", fixtureCapturedAddFileArgs,
    )
    assert responseHttp.status_code == 200
    assert fixtureCapturedAddFileArgs["sWorkdir"] == "/workspace/myrepo"
    assert fixtureCapturedAddFileArgs["sFilePath"] == "step01/output.dat"


def test_add_file_returns_409_when_workflow_lacks_project_repo(
    fixtureCapturedAddFileArgs,
):
    """A workflow with empty sProjectRepoPath surfaces 409, not a git error."""
    dictCtx = _fdictBuildContextWithRepoAt(
        "",
        "/workspace/.vaibify/workflows/demo.json",
    )
    responseHttp = _fnRunAddFileRoute(
        dictCtx, "any.dat", fixtureCapturedAddFileArgs,
    )
    assert responseHttp.status_code == 409
    assert "sWorkdir" not in fixtureCapturedAddFileArgs


@pytest.fixture
def fixtureCapturedPushArgs():
    return {}


def _fnPatchPushToGithub(fixtureCapturedPushArgs):
    def _fnFakePush(
        connectionDocker, sContainerId,
        listFilePaths, sCommitMessage, sWorkdir,
    ):
        fixtureCapturedPushArgs["listFilePaths"] = listFilePaths
        fixtureCapturedPushArgs["sWorkdir"] = sWorkdir
        return (0, "abc1234")
    return patch(
        "vaibify.gui.syncDispatcher.ftResultPushToGithub",
        side_effect=_fnFakePush,
    )


def test_push_uses_project_repo_path_not_workflow_dirname(
    fixtureCapturedPushArgs,
):
    """The bulk push route shares the same cwd discipline."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    dictCtx = _fdictBuildContextWithRepoAt(
        "/workspace/myrepo",
        "/workspace/myrepo/.vaibify/workflows/demo.json",
    )
    app = FastAPI()
    app.state.listLifespanStartup = []
    app.state.listLifespanShutdown = []
    syncRoutes.fnRegisterAll(app, dictCtx)
    with _fnPatchPushToGithub(fixtureCapturedPushArgs), patch(
        "vaibify.gui.routes.syncRoutes._fnRequireNetworkAccess",
    ), patch(
        "vaibify.gui.routes.syncRoutes._fnValidateGithubPushPaths",
    ), patch(
        "vaibify.gui.routes.scriptRoutes._fnStoreCommitHash",
    ):
        responseHttp = TestClient(app).post(
            "/api/github/cid/push",
            json={
                "listFilePaths": ["step01/output.dat"],
                "sCommitMessage": "msg",
            },
        )
    assert responseHttp.status_code == 200
    assert fixtureCapturedPushArgs["sWorkdir"] == "/workspace/myrepo"
