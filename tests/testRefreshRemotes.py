"""POST /api/git/{id}/refresh-remotes: shape, cache, guards, catalog.

The endpoint gives the dashboard an on-demand reconciliation point
against GitHub: one fetch (respecting the 30 s cache unless forced)
plus a single remote-heads probe, returned together with the
``_fdictProjectGitView`` shape the badge dashboard already consumes.
"""

from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from vaibify.gui import containerGit
from vaibify.gui.routes import gitRoutes


S_CONTAINER_ID = "cid"
S_REPO = "/workspace/myrepo"
S_HEAD_SHA = "a1b2c3d4e5f60718293a4b5c6d7e8f9012345678"
S_UPSTREAM_SHA = "f0e1d2c3b4a5968778695a4b3c2d1e0f12345678"


@pytest.fixture(autouse=True)
def fixtureClearFetchCache():
    gitRoutes._DICT_LAST_FETCH.clear()
    yield
    gitRoutes._DICT_LAST_FETCH.clear()


def _fdictBuildGitContext(sProjectRepoPath=S_REPO):
    dictWorkflow = {
        "sProjectRepoPath": sProjectRepoPath,
        "sWorkflowName": "demo",
        "listSteps": [],
    }
    return {
        "workflows": {S_CONTAINER_ID: dictWorkflow},
        "paths": {S_CONTAINER_ID: S_REPO + "/.vaibify/workflows/d.json"},
        "require": lambda: None,
        "save": lambda sId, dictWf: None,
        "docker": object(),
    }


def _fclientBuildGitClient(dictCtx):
    app = FastAPI()
    app.state.listLifespanStartup = []
    app.state.listLifespanShutdown = []
    gitRoutes.fnRegisterAll(app, dictCtx)
    return TestClient(app)


def _fdictRemoteHeads():
    return {
        "bSuccess": True,
        "sHeadSha": S_HEAD_SHA,
        "sHeadCommittedAt": "2026-06-09T00:00:00+00:00",
        "sUpstreamSha": S_UPSTREAM_SHA,
        "sUpstreamCommittedAt": "2026-06-08T00:00:00+00:00",
        "iAhead": 1, "iBehind": 0,
        "sRefreshedAt": "2026-06-09T00:00:01Z",
    }


def _fdictRepoStatus():
    return {
        "bIsRepo": True, "sHeadSha": S_HEAD_SHA, "sBranch": "main",
        "iAhead": 1, "iBehind": 0, "dictFileStates": {},
        "sRefreshedAt": "2026-06-09T00:00:01Z", "sReason": "",
    }


def _fpatchesRefreshRemotes(dictCaptured=None):
    """Patch the containerGit calls behind refresh-remotes."""
    dictCaptured = dictCaptured if dictCaptured is not None else {}

    def _ftFakeFetch(docker, sContainerId, sWorkspace):
        dictCaptured.setdefault("listFetchWorkspaces", []).append(
            sWorkspace)
        return (0, "fetched")

    return (
        patch.object(
            containerGit, "ftResultGitFetchInContainer",
            side_effect=_ftFakeFetch,
        ),
        patch.object(
            containerGit, "fdictRemoteHeadsInContainer",
            return_value=_fdictRemoteHeads(),
        ),
        patch.object(
            containerGit, "fdictGitStatusInContainer",
            return_value=_fdictRepoStatus(),
        ),
        patch.object(
            containerGit, "fsRemoteUrlInContainer",
            return_value="https://github.com/owner/repo.git",
        ),
    )


def _fdictPostRefresh(clientHttp, dictBody=None):
    return clientHttp.post(
        f"/api/git/{S_CONTAINER_ID}/refresh-remotes",
        json=dictBody if dictBody is not None else {"bForce": True},
    )


def test_refresh_remotes_response_shape():
    """Response carries remote heads plus the project git view."""
    dictCtx = _fdictBuildGitContext()
    clientHttp = _fclientBuildGitClient(dictCtx)
    pFetch, pHeads, pStatus, pUrl = _fpatchesRefreshRemotes()
    with pFetch, pHeads, pStatus, pUrl:
        responseHttp = _fdictPostRefresh(clientHttp)
    assert responseHttp.status_code == 200
    dictResult = responseHttp.json()
    assert dictResult["bSuccess"] is True
    assert dictResult["bCacheUsed"] is False
    dictHeads = dictResult["dictRemoteHeads"]
    assert dictHeads["sHeadSha"] == S_HEAD_SHA
    assert dictHeads["sUpstreamSha"] == S_UPSTREAM_SHA
    assert dictHeads["iAhead"] == 1
    dictGit = dictResult["dictGit"]
    assert dictGit["sBranch"] == "main"
    assert dictGit["sHeadSha"] == S_HEAD_SHA
    assert dictGit["sRefreshedAt"]
    assert dictGit["sRemoteUrl"].startswith("https://github.com/")


def test_refresh_remotes_threads_project_repo_into_fetch():
    """The fetch must run against the project repo, never /workspace."""
    dictCtx = _fdictBuildGitContext()
    clientHttp = _fclientBuildGitClient(dictCtx)
    dictCaptured = {}
    pFetch, pHeads, pStatus, pUrl = _fpatchesRefreshRemotes(dictCaptured)
    with pFetch, pHeads, pStatus, pUrl:
        _fdictPostRefresh(clientHttp)
    assert dictCaptured["listFetchWorkspaces"] == [S_REPO]


def test_refresh_remotes_respects_fetch_cache_without_force():
    """A recent fetch is reused when bForce is false."""
    dictCtx = _fdictBuildGitContext()
    clientHttp = _fclientBuildGitClient(dictCtx)
    gitRoutes._fnRecordFetchTime(S_CONTAINER_ID)
    dictCaptured = {}
    pFetch, pHeads, pStatus, pUrl = _fpatchesRefreshRemotes(dictCaptured)
    with pFetch, pHeads, pStatus, pUrl:
        responseHttp = _fdictPostRefresh(clientHttp, {"bForce": False})
    dictResult = responseHttp.json()
    assert dictResult["bCacheUsed"] is True
    assert "listFetchWorkspaces" not in dictCaptured


def test_refresh_remotes_force_bypasses_fetch_cache():
    """bForce true refetches even inside the 30 s cache window."""
    dictCtx = _fdictBuildGitContext()
    clientHttp = _fclientBuildGitClient(dictCtx)
    gitRoutes._fnRecordFetchTime(S_CONTAINER_ID)
    dictCaptured = {}
    pFetch, pHeads, pStatus, pUrl = _fpatchesRefreshRemotes(dictCaptured)
    with pFetch, pHeads, pStatus, pUrl:
        responseHttp = _fdictPostRefresh(clientHttp, {"bForce": True})
    assert responseHttp.json()["bCacheUsed"] is False
    assert dictCaptured["listFetchWorkspaces"] == [S_REPO]


def test_refresh_remotes_409_without_project_repo():
    """A workflow outside a git work tree gets a clear 409."""
    dictCtx = _fdictBuildGitContext(sProjectRepoPath="")
    clientHttp = _fclientBuildGitClient(dictCtx)
    responseHttp = _fdictPostRefresh(clientHttp)
    assert responseHttp.status_code == 409
    assert "Project repo not detected" in responseHttp.text


def test_refresh_remotes_502_when_fetch_fails():
    """A failing git fetch surfaces as 502, not a silent success."""
    dictCtx = _fdictBuildGitContext()
    clientHttp = _fclientBuildGitClient(dictCtx)
    with patch.object(
        containerGit, "ftResultGitFetchInContainer",
        return_value=(1, "fatal: could not read from remote"),
    ):
        responseHttp = _fdictPostRefresh(clientHttp)
    assert responseHttp.status_code == 502
    assert "git fetch failed" in responseHttp.text


def test_refresh_remotes_registered_in_agent_catalog():
    """The route must be visible to the in-container agent."""
    from vaibify.gui import actionCatalog
    listMatches = [
        dictEntry for dictEntry in actionCatalog.LIST_AGENT_ACTIONS
        if dictEntry["sName"] == "refresh-remotes"
    ]
    assert len(listMatches) == 1
    dictEntry = listMatches[0]
    assert dictEntry["sMethod"] == "POST"
    assert dictEntry["sPath"] == (
        "/api/git/{sContainerId}/refresh-remotes"
    )
    assert dictEntry["bAgentSafe"] is True


class _FakeHeadsConnection:
    """Scripted exec returning the three-line remote-heads payload."""

    def __init__(self, iExit, sOutput):
        self.iExit = iExit
        self.sOutput = sOutput

    def ftResultExecuteCommand(self, sContainerId, sCommand):
        return (self.iExit, self.sOutput)


def test_remote_heads_parses_head_upstream_and_counts():
    """fdictRemoteHeadsInContainer parses the combined git output."""
    sOutput = (
        f"{S_HEAD_SHA} 2026-06-09T00:00:00+00:00\n"
        f"{S_UPSTREAM_SHA} 2026-06-08T00:00:00+00:00\n"
        "2\t1\n"
    )
    dictHeads = containerGit.fdictRemoteHeadsInContainer(
        _FakeHeadsConnection(0, sOutput),
        S_CONTAINER_ID, sWorkspace=S_REPO,
    )
    assert dictHeads["bSuccess"] is True
    assert dictHeads["sHeadSha"] == S_HEAD_SHA
    assert dictHeads["sUpstreamSha"] == S_UPSTREAM_SHA
    assert dictHeads["iAhead"] == 2
    assert dictHeads["iBehind"] == 1
    assert dictHeads["sRefreshedAt"]


def test_remote_heads_tolerates_missing_upstream():
    """The '-' placeholders for a missing upstream parse to empty."""
    sOutput = (
        f"{S_HEAD_SHA} 2026-06-09T00:00:00+00:00\n"
        "- -\n"
        "0\t0\n"
    )
    dictHeads = containerGit.fdictRemoteHeadsInContainer(
        _FakeHeadsConnection(0, sOutput),
        S_CONTAINER_ID, sWorkspace=S_REPO,
    )
    assert dictHeads["bSuccess"] is True
    assert dictHeads["sUpstreamSha"] == ""
    assert dictHeads["sUpstreamCommittedAt"] == ""


def test_remote_heads_reports_git_failure():
    """A non-zero git exit is surfaced, never masked as success."""
    dictHeads = containerGit.fdictRemoteHeadsInContainer(
        _FakeHeadsConnection(128, "fatal: not a git repository"),
        S_CONTAINER_ID, sWorkspace=S_REPO,
    )
    assert dictHeads["bSuccess"] is False
    assert "not a git repository" in dictHeads["sReason"]
