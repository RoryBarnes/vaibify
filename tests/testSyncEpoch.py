"""Per-container sync epoch: bumps on sync-mutating routes, /state poll.

The epoch lets the existing 10 s state poll trigger exactly one badge
refresh after a push/pull/fetch/refresh changes remote-facing git
state — no new polling loops and no remote git queries on a timer.
"""

from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from vaibify.gui import containerGit, pipelineServer
from vaibify.gui.routes import gitRoutes, pipelineRoutes, syncRoutes


S_CONTAINER_ID = "cid"
S_REPO = "/workspace/myrepo"
S_HEAD_SHA = "a1b2c3d4e5f60718293a4b5c6d7e8f9012345678"


@pytest.fixture(autouse=True)
def fixtureClearFetchCache():
    gitRoutes._DICT_LAST_FETCH.clear()
    yield
    gitRoutes._DICT_LAST_FETCH.clear()


def _fdictBuildEpochContext():
    dictWorkflow = {
        "sProjectRepoPath": S_REPO,
        "sWorkflowName": "demo",
        "listSteps": [],
    }
    return {
        "workflows": {S_CONTAINER_ID: dictWorkflow},
        "paths": {S_CONTAINER_ID: S_REPO + "/.vaibify/workflows/d.json"},
        "require": lambda: None,
        "save": lambda sId, dictWf: None,
        "docker": object(),
        "dictSyncEpochs": {},
    }


def _fclientBuildEpochClient(dictCtx):
    app = FastAPI()
    app.state.listLifespanStartup = []
    app.state.listLifespanShutdown = []
    syncRoutes.fnRegisterAll(app, dictCtx)
    gitRoutes.fnRegisterAll(app, dictCtx)
    pipelineRoutes.fnRegisterAll(app, dictCtx)
    return TestClient(app)


def _fiEpochOf(dictCtx):
    return pipelineServer.fiGetSyncEpoch(dictCtx, S_CONTAINER_ID)


def _fdictRepoStatus(bClean=True):
    return {
        "bIsRepo": True, "sHeadSha": S_HEAD_SHA, "sBranch": "main",
        "iAhead": 0, "iBehind": 0,
        "dictFileStates": {} if bClean else {"a.py": "dirty"},
        "sRefreshedAt": "2026-06-09T00:00:00Z", "sReason": "",
    }


def test_helpers_count_from_zero_per_container():
    dictCtx = _fdictBuildEpochContext()
    assert _fiEpochOf(dictCtx) == 0
    pipelineServer.fnBumpSyncEpoch(dictCtx, S_CONTAINER_ID)
    pipelineServer.fnBumpSyncEpoch(dictCtx, S_CONTAINER_ID)
    assert _fiEpochOf(dictCtx) == 2
    assert pipelineServer.fiGetSyncEpoch(dictCtx, "other") == 0


def test_push_bumps_sync_epoch():
    dictCtx = _fdictBuildEpochContext()
    clientHttp = _fclientBuildEpochClient(dictCtx)
    with patch(
        "vaibify.gui.routes.syncRoutes._fnRequireNetworkAccess",
    ), patch(
        "vaibify.gui.routes.syncRoutes._fnAssertGithubTokenBoundToRemote",
    ), patch(
        "vaibify.gui.syncDispatcher.ftResultPushToGithub",
        return_value=(0, "pushed"),
    ), patch.object(
        containerGit, "fsGitHeadShaInContainer",
        return_value=S_HEAD_SHA,
    ), patch.object(
        containerGit, "fdictGitStatusInContainer",
        return_value=_fdictRepoStatus(),
    ):
        responseHttp = clientHttp.post(
            f"/api/github/{S_CONTAINER_ID}/push",
            json={"listFilePaths": ["a.dat"], "sCommitMessage": "m"},
        )
    assert responseHttp.status_code == 200
    assert _fiEpochOf(dictCtx) == 1


def test_push_bumps_epoch_even_on_failure():
    """A failed push may still have created a local commit."""
    dictCtx = _fdictBuildEpochContext()
    clientHttp = _fclientBuildEpochClient(dictCtx)
    with patch(
        "vaibify.gui.routes.syncRoutes._fnRequireNetworkAccess",
    ), patch(
        "vaibify.gui.routes.syncRoutes._fnAssertGithubTokenBoundToRemote",
    ), patch(
        "vaibify.gui.syncDispatcher.ftResultPushToGithub",
        return_value=(1, "remote: permission denied"),
    ):
        responseHttp = clientHttp.post(
            f"/api/github/{S_CONTAINER_ID}/push",
            json={"listFilePaths": ["a.dat"], "sCommitMessage": "m"},
        )
    assert responseHttp.status_code == 200
    assert responseHttp.json()["bSuccess"] is False
    assert _fiEpochOf(dictCtx) == 1


def test_add_file_bumps_sync_epoch():
    dictCtx = _fdictBuildEpochContext()
    clientHttp = _fclientBuildEpochClient(dictCtx)
    with patch(
        "vaibify.gui.syncDispatcher.ftResultAddFileToGithub",
        return_value=(0, "pushed"),
    ), patch.object(
        containerGit, "fsGitHeadShaInContainer",
        return_value=S_HEAD_SHA,
    ), patch.object(
        containerGit, "fdictGitStatusInContainer",
        return_value=_fdictRepoStatus(),
    ):
        responseHttp = clientHttp.post(
            f"/api/github/{S_CONTAINER_ID}/add-file",
            json={"sFilePath": "a.dat", "sCommitMessage": "m"},
        )
    assert responseHttp.status_code == 200
    assert _fiEpochOf(dictCtx) == 1


def test_commit_canonical_bumps_sync_epoch():
    dictCtx = _fdictBuildEpochContext()
    clientHttp = _fclientBuildEpochClient(dictCtx)
    dictReport = {
        "listNeedsCommit": [{"sPath": "workflow.json"}],
        "sHeadSha": S_HEAD_SHA,
    }
    with patch.object(
        containerGit, "fdictGitStatusInContainer",
        return_value=_fdictRepoStatus(),
    ), patch.object(
        gitRoutes, "_flistCanonicalFromContainer",
        return_value=["workflow.json"],
    ), patch.object(
        gitRoutes.manifestCheck, "fdictBuildManifestReportFromStatus",
        return_value=dictReport,
    ), patch.object(
        containerGit, "ftResultGitAddInContainer",
        return_value=(0, ""),
    ), patch.object(
        containerGit, "ftResultGitCommitInContainer",
        return_value=(0, ""),
    ), patch.object(
        containerGit, "fsGitHeadShaInContainer",
        return_value=S_HEAD_SHA,
    ):
        responseHttp = clientHttp.post(
            f"/api/git/{S_CONTAINER_ID}/commit-canonical",
            json={"sCommitMessage": "m"},
        )
    assert responseHttp.status_code == 200
    assert responseHttp.json()["bSuccess"] is True
    assert _fiEpochOf(dictCtx) == 1


def test_fetch_project_repo_bumps_epoch_only_when_fetching():
    dictCtx = _fdictBuildEpochContext()
    clientHttp = _fclientBuildEpochClient(dictCtx)
    with patch.object(
        containerGit, "ftResultGitFetchInContainer",
        return_value=(0, "fetched"),
    ), patch.object(
        containerGit, "fdictGitStatusInContainer",
        return_value=_fdictRepoStatus(),
    ):
        clientHttp.post(
            f"/api/git/{S_CONTAINER_ID}/fetch-project-repo",
            json={"bForce": True},
        )
        assert _fiEpochOf(dictCtx) == 1
        clientHttp.post(
            f"/api/git/{S_CONTAINER_ID}/fetch-project-repo",
            json={"bForce": False},
        )
    assert _fiEpochOf(dictCtx) == 1


def test_pull_project_repo_bumps_sync_epoch():
    dictCtx = _fdictBuildEpochContext()
    clientHttp = _fclientBuildEpochClient(dictCtx)
    with patch.object(
        containerGit, "fdictGitStatusInContainer",
        return_value=_fdictRepoStatus(bClean=True),
    ), patch.object(
        containerGit, "ftResultGitPullFastForwardInContainer",
        return_value=(0, "Fast-forward"),
    ), patch.object(
        containerGit, "fsGitHeadShaInContainer",
        return_value=S_HEAD_SHA,
    ):
        responseHttp = clientHttp.post(
            f"/api/git/{S_CONTAINER_ID}/pull-project-repo",
        )
    assert responseHttp.status_code == 200
    assert _fiEpochOf(dictCtx) == 1


def test_pull_dirty_refusal_does_not_bump_epoch():
    """A refused pull changed nothing, so the epoch must hold still."""
    dictCtx = _fdictBuildEpochContext()
    clientHttp = _fclientBuildEpochClient(dictCtx)
    with patch.object(
        containerGit, "fdictGitStatusInContainer",
        return_value=_fdictRepoStatus(bClean=False),
    ):
        responseHttp = clientHttp.post(
            f"/api/git/{S_CONTAINER_ID}/pull-project-repo",
        )
    assert responseHttp.json()["bSuccess"] is False
    assert _fiEpochOf(dictCtx) == 0


def test_refresh_remotes_bumps_sync_epoch():
    dictCtx = _fdictBuildEpochContext()
    clientHttp = _fclientBuildEpochClient(dictCtx)
    with patch.object(
        containerGit, "ftResultGitFetchInContainer",
        return_value=(0, "fetched"),
    ), patch.object(
        containerGit, "fdictRemoteHeadsInContainer",
        return_value={"bSuccess": True, "iAhead": 0, "iBehind": 0},
    ), patch.object(
        containerGit, "fdictGitStatusInContainer",
        return_value=_fdictRepoStatus(),
    ), patch.object(
        containerGit, "fsRemoteUrlInContainer",
        return_value="https://github.com/owner/repo.git",
    ):
        responseHttp = clientHttp.post(
            f"/api/git/{S_CONTAINER_ID}/refresh-remotes",
            json={"bForce": True},
        )
    assert responseHttp.status_code == 200
    assert _fiEpochOf(dictCtx) == 1


async def _fdictFakeReconciledState(dictCtx, sContainerId, fNow=None):
    return {"bRunning": True, "iCurrentStep": 2}


async def _fdictFakeReconciledNone(dictCtx, sContainerId, fNow=None):
    return None


def test_state_endpoint_surfaces_sync_epoch():
    dictCtx = _fdictBuildEpochContext()
    clientHttp = _fclientBuildEpochClient(dictCtx)
    pipelineServer.fnBumpSyncEpoch(dictCtx, S_CONTAINER_ID)
    pipelineServer.fnBumpSyncEpoch(dictCtx, S_CONTAINER_ID)
    with patch(
        "vaibify.gui.pipelineState.fdictReadReconciledState",
        _fdictFakeReconciledState,
    ):
        responseHttp = clientHttp.get(
            f"/api/pipeline/{S_CONTAINER_ID}/state",
        )
    dictState = responseHttp.json()
    assert dictState["bRunning"] is True
    assert dictState["iSyncEpoch"] == 2


def test_state_endpoint_includes_epoch_when_not_running():
    dictCtx = _fdictBuildEpochContext()
    clientHttp = _fclientBuildEpochClient(dictCtx)
    with patch(
        "vaibify.gui.pipelineState.fdictReadReconciledState",
        _fdictFakeReconciledNone,
    ):
        responseHttp = clientHttp.get(
            f"/api/pipeline/{S_CONTAINER_ID}/state",
        )
    dictState = responseHttp.json()
    assert dictState == {"bRunning": False, "iSyncEpoch": 0}
