"""Per-container sync epoch: bumps on sync-mutating routes, /state poll.

The epoch lets the existing 10 s state poll trigger exactly one badge
refresh after a push/pull/fetch/refresh changes remote-facing git
state — no new polling loops and no remote git queries on a timer.
"""

from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from tests.carrierStandDown import fnStandCarrierDown
from vaibify.gui import containerGit, pipelineServer
from vaibify.gui.routes import gitRoutes, pipelineRoutes, syncRoutes


@pytest.fixture
def fixtureCarrierStoodDown(monkeypatch):
    """Stand the carrier down for the migrated routes driven bare here.

    ``add-file``, ``verify`` and the whole git panel now do their
    container work through carrier mode (b); this module builds a bare
    ``FastAPI()`` with no owner record for any of them to bind to.
    Requested only by the tests that reach a carrier. See
    ``tests/carrierStandDown.py``.
    """
    fnStandCarrierDown(monkeypatch, gitRoutes, syncRoutes)


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


def test_add_file_bumps_sync_epoch(fixtureCarrierStoodDown):
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


def test_commit_canonical_bumps_sync_epoch(fixtureCarrierStoodDown):
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


def test_fetch_project_repo_bumps_epoch_only_when_fetching(
    fixtureCarrierStoodDown,
):
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


def test_pull_project_repo_bumps_sync_epoch(fixtureCarrierStoodDown):
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


def test_pull_dirty_refusal_does_not_bump_epoch(fixtureCarrierStoodDown):
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


def test_refresh_remotes_bumps_sync_epoch(fixtureCarrierStoodDown):
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


# ---------------------------------------------------------------------
# Producers added 2026-07-26 (plan items 3.5a and 3.5c). Before these,
# the two actions whose whole purpose is reconciling the dashboard with
# the remote were the two that left it un-repainted.
# ---------------------------------------------------------------------


S_VERIFIED_ISO = "2026-07-26T00:00:00Z"


def _fdictBuildVerifyStatus(listDiverged=None, iTotalFiles=1):
    return {
        "sService": "github",
        "sLastVerified": S_VERIFIED_ISO,
        "iTotalFiles": iTotalFiles,
        "iMatching": iTotalFiles - len(listDiverged or []),
        "listDiverged": listDiverged or [],
        "sCommittedShaVerified": S_HEAD_SHA,
    }


def _fiReadEpochFromStatePoll(clientHttp):
    """Read the epoch the way the browser does — off the state poll."""
    with patch(
        "vaibify.gui.pipelineState.fdictReadReconciledState",
        _fdictFakeReconciledNone,
    ):
        responseHttp = clientHttp.get(
            f"/api/pipeline/{S_CONTAINER_ID}/state",
        )
    return responseHttp.json()["iSyncEpoch"]


@pytest.mark.falsification
def test_verify_remote_bumps_sync_epoch(fixtureCarrierStoodDown):
    """A completed remote verify must invalidate the dashboard.

    Drives the real route through TestClient and reads the epoch back
    off the same ``/state`` poll the browser watches, so the assertion
    covers the whole producer-to-consumer path rather than an
    in-process counter.

    Kills: dropping the ``fnBumpSyncEpoch`` call from the verify route
    (``vaibify/gui/routes/syncRoutes.py``) leaves the poll reporting
    the pre-verify epoch, so no badge refresh is ever triggered.
    """
    dictCtx = _fdictBuildEpochContext()
    clientHttp = _fclientBuildEpochClient(dictCtx)
    assert _fiReadEpochFromStatePoll(clientHttp) == 0
    with patch(
        "vaibify.gui.routes.syncRoutes._fnRequireNetworkAccess",
    ), patch(
        "vaibify.gui.routes.syncRoutes.ffilesForWorkflow",
        return_value=object(),
    ), patch(
        "vaibify.gui.routes.syncRoutes.fdictRunRemoteVerifyBlocking",
        return_value=_fdictBuildVerifyStatus(),
    ):
        responseHttp = clientHttp.post(
            f"/api/sync/{S_CONTAINER_ID}/github/verify",
        )
    assert responseHttp.status_code == 200
    assert responseHttp.json()["sLastVerified"] == S_VERIFIED_ISO
    assert _fiReadEpochFromStatePoll(clientHttp) == 1


def test_failed_verify_does_not_bump_sync_epoch(fixtureCarrierStoodDown):
    """A verify that never reached the remote changed no cached state."""
    dictCtx = _fdictBuildEpochContext()
    clientHttp = _fclientBuildEpochClient(dictCtx)
    with patch(
        "vaibify.gui.routes.syncRoutes._fnRequireNetworkAccess",
    ), patch(
        "vaibify.gui.routes.syncRoutes.ffilesForWorkflow",
        return_value=object(),
    ), patch(
        "vaibify.gui.routes.syncRoutes.fdictRunRemoteVerifyBlocking",
        side_effect=RuntimeError("connection reset"),
    ):
        responseHttp = clientHttp.post(
            f"/api/sync/{S_CONTAINER_ID}/github/verify",
        )
    assert responseHttp.status_code == 502
    assert _fiEpochOf(dictCtx) == 0


async def _fsRefreshVerifyStub(
    dictCtx, sContainerId, dictWorkflow, sService, requestHttp=None,
):
    """Mirror the real signature, including the request the drain needs.

    Reconcile now threads its request through so the post-push verify
    runs under its own mode-(b) carrier rather than a bare
    ``to_thread``; a stub that stopped at four arguments turned that
    into a ``TypeError`` reported as a route failure.
    """
    return ""


def _flistBuildReconcilePatches(dictStatus):
    """Patch every container/network edge the reconcile route crosses."""
    return [
        patch.object(
            containerGit, "ftResultGitFetchInContainer",
            return_value=(0, "fetched"),
        ),
        patch.object(
            containerGit, "fdictRemoteHeadsInContainer",
            return_value={"bSuccess": True, "iAhead": 0, "iBehind": 0},
        ),
        patch.object(
            containerGit, "fdictGitStatusInContainer",
            return_value=_fdictRepoStatus(),
        ),
        patch.object(
            containerGit, "fsRemoteUrlInContainer",
            return_value="https://github.com/owner/repo.git",
        ),
        patch(
            "vaibify.gui.routes.gitRoutes.fsRefreshVerifyCacheAfterPush",
            _fsRefreshVerifyStub,
        ),
        patch(
            "vaibify.gui.routes.gitRoutes.ffilesForWorkflow",
            return_value=object(),
        ),
        patch(
            "vaibify.reproducibility.scheduledReverify."
            "fdictReadCachedSyncStatus",
            return_value=dictStatus,
        ),
    ]


def _fresponsePostReconcile(clientHttp, dictStatus):
    """POST the reconcile route with the container edges stubbed."""
    import contextlib
    with contextlib.ExitStack() as stackPatches:
        for contextPatch in _flistBuildReconcilePatches(dictStatus):
            stackPatches.enter_context(contextPatch)
        return clientHttp.post(
            f"/api/git/{S_CONTAINER_ID}/reconcile-remote-state",
        )


@pytest.mark.falsification
def test_reconcile_remote_state_bumps_sync_epoch(fixtureCarrierStoodDown):
    """The out-of-band-push repair action must repaint the dashboard.

    An agent or a researcher who runs ``git push`` in the container
    terminal produces no HTTP traffic at all, so this route is the
    only thing that can tell an open tab that the remote moved.

    Kills: dropping the ``fnBumpSyncEpoch`` call from the reconcile
    route (``vaibify/gui/routes/gitRoutes.py``) leaves the epoch
    unchanged, so the dashboard keeps rendering the pre-push state.
    """
    dictCtx = _fdictBuildEpochContext()
    clientHttp = _fclientBuildEpochClient(dictCtx)
    responseHttp = _fresponsePostReconcile(
        clientHttp, _fdictBuildVerifyStatus(iTotalFiles=0),
    )
    assert responseHttp.status_code == 200
    assert responseHttp.json()["bSuccess"] is True
    assert _fiReadEpochFromStatePoll(clientHttp) == 1


@pytest.mark.falsification
def test_reconcile_marks_only_paths_the_verify_actually_covered(
    fixtureCarrierStoodDown,
):
    """Sync status may only record what the verify proved.

    ``iTotalFiles`` counts the declared canonical paths that existed
    locally. When it falls short of the declared count the verify
    never looked at some of them, and a file nobody looked at must
    not be recorded as synced to GitHub.

    Kills: relaxing the coverage equality in
    ``_flistProvenGithubSyncedPaths`` (gitRoutes) to an inequality
    lets a partial verify mark unexamined files as GitHub-synced.
    """
    dictCtx = _fdictBuildEpochContext()
    dictWorkflow = dictCtx["workflows"][S_CONTAINER_ID]
    dictWorkflow["listSteps"] = [{
        "sName": "Alpha", "sDirectory": "Alpha",
        "saOutputDataFiles": ["out.csv"],
        "saPlotFiles": ["figure.pdf"],
    }]
    clientHttp = _fclientBuildEpochClient(dictCtx)
    responseHttp = _fresponsePostReconcile(
        clientHttp, _fdictBuildVerifyStatus(iTotalFiles=1),
    )
    assert responseHttp.status_code == 200
    assert dictWorkflow.get("dictSyncStatus", {}) == {}


def test_reconcile_records_the_files_the_verify_matched(
    fixtureCarrierStoodDown,
):
    """Full coverage marks the matching paths and skips the diverged one."""
    dictCtx = _fdictBuildEpochContext()
    dictWorkflow = dictCtx["workflows"][S_CONTAINER_ID]
    dictWorkflow["listSteps"] = [{
        "sName": "Alpha", "sDirectory": "Alpha",
        "saOutputDataFiles": ["out.csv"],
        "saPlotFiles": ["figure.pdf"],
    }]
    clientHttp = _fclientBuildEpochClient(dictCtx)
    responseHttp = _fresponsePostReconcile(
        clientHttp,
        _fdictBuildVerifyStatus(
            listDiverged=[{"sPath": "Alpha/figure.pdf"}], iTotalFiles=2,
        ),
    )
    assert responseHttp.status_code == 200
    dictSyncStatus = dictWorkflow["dictSyncStatus"]
    assert dictSyncStatus["Alpha/out.csv"]["bGithub"] is True
    assert "Alpha/figure.pdf" not in dictSyncStatus
