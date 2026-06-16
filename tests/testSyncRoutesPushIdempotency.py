"""Idempotency cache for ``POST /api/github/{id}/push``.

A vaibify-do retry across a transient network flake used to re-run the
full pre-push validation pipeline (re-stage, re-push, bump
iSyncEpoch). git itself is idempotent at the underlying ref-update
layer, but the dashboard bookkeeping is not. The in-memory dedupe
cache keyed by ``(container, pre-push HEAD sha, file-list digest)``
collapses two rapid identical calls into one inner push; a different
payload bypasses the cache.
"""

from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from vaibify.gui import containerGit
from vaibify.gui.routes import syncRoutes


_S_CONTAINER_ID = "ctr-idempotency"
_S_REPO = "/workspace/myrepo"
_S_HEAD_SHA = "0123456789abcdef0123456789abcdef01234567"


def _fdictBuildPushContext():
    """Return a minimal dictCtx for the push route."""
    dictWorkflow = {
        "sProjectRepoPath": _S_REPO,
        "sWorkflowName": "demo",
        "listSteps": [],
    }
    return {
        "workflows": {_S_CONTAINER_ID: dictWorkflow},
        "paths": {
            _S_CONTAINER_ID: _S_REPO + "/.vaibify/workflows/d.json",
        },
        "require": lambda: None,
        "save": lambda sId, dictWf: None,
        "docker": object(),
    }


def _fclientBuildPushClient(dictCtx):
    """Register syncRoutes on a bare app and return a TestClient."""
    app = FastAPI()
    app.state.listLifespanStartup = []
    app.state.listLifespanShutdown = []
    syncRoutes.fnRegisterAll(app, dictCtx)
    return TestClient(app)


def _fdictRepoStatus():
    return {
        "bIsRepo": True, "sHeadSha": _S_HEAD_SHA, "sBranch": "main",
        "iAhead": 0, "iBehind": 0, "dictFileStates": {},
        "sRefreshedAt": "2026-06-09T00:00:00Z", "sReason": "",
    }


@pytest.fixture(autouse=True)
def _fnClearDedupeCache():
    """Drop any cached push results between tests."""
    syncRoutes._DICT_RECENT_PUSH_RESULTS.clear()
    yield
    syncRoutes._DICT_RECENT_PUSH_RESULTS.clear()


def _fctxNeutralizeGuards():
    """Stack the two guard patches used by every push test."""
    return (
        patch(
            "vaibify.gui.routes.syncRoutes._fnRequireNetworkAccess",
        ),
        patch(
            "vaibify.gui.routes.syncRoutes"
            "._fnAssertGithubTokenBoundToRemote",
        ),
    )


def _fnPostPush(clientHttp, listFilePaths):
    """POST the push route with the network/token guards no-oped."""
    listGuards = list(_fctxNeutralizeGuards())
    for ctxPatch in listGuards:
        ctxPatch.start()
    try:
        return clientHttp.post(
            f"/api/github/{_S_CONTAINER_ID}/push",
            json={
                "listFilePaths": listFilePaths,
                "sCommitMessage": "msg",
            },
        )
    finally:
        for ctxPatch in listGuards:
            ctxPatch.stop()


def test_two_rapid_identical_pushes_run_inner_push_once():
    """Two identical calls inside the TTL hit the cache for the second."""
    dictCtx = _fdictBuildPushContext()
    clientHttp = _fclientBuildPushClient(dictCtx)
    iCalls = {"i": 0}

    def fnFakePush(*args, **kwargs):
        iCalls["i"] += 1
        return (0, "pushed")

    with patch(
        "vaibify.gui.syncDispatcher.ftResultPushToGithub",
        side_effect=fnFakePush,
    ), patch.object(
        containerGit, "fsGitHeadShaInContainer",
        return_value=_S_HEAD_SHA,
    ), patch.object(
        containerGit, "fdictGitStatusInContainer",
        return_value=_fdictRepoStatus(),
    ):
        responseOne = _fnPostPush(clientHttp, ["step01/output.dat"])
        responseTwo = _fnPostPush(clientHttp, ["step01/output.dat"])
    assert responseOne.status_code == 200
    assert responseTwo.status_code == 200
    assert iCalls["i"] == 1, (
        f"expected inner push to run exactly once, ran {iCalls['i']}"
    )
    dictResultTwo = responseTwo.json()
    assert dictResultTwo["bSuccess"] is True
    assert dictResultTwo.get("bDedupedFromRecent") is True


def test_two_rapid_pushes_return_same_payload():
    """The cached call returns the same business fields as the original."""
    dictCtx = _fdictBuildPushContext()
    clientHttp = _fclientBuildPushClient(dictCtx)
    with patch(
        "vaibify.gui.syncDispatcher.ftResultPushToGithub",
        return_value=(0, "pushed"),
    ), patch.object(
        containerGit, "fsGitHeadShaInContainer",
        return_value=_S_HEAD_SHA,
    ), patch.object(
        containerGit, "fdictGitStatusInContainer",
        return_value=_fdictRepoStatus(),
    ):
        responseOne = _fnPostPush(clientHttp, ["step01/output.dat"])
        responseTwo = _fnPostPush(clientHttp, ["step01/output.dat"])
    dictOne = responseOne.json()
    dictTwo = responseTwo.json()
    assert dictOne["sCommitHash"] == dictTwo["sCommitHash"]
    assert dictOne["bSuccess"] == dictTwo["bSuccess"]


def test_different_payload_bypasses_cache_and_runs_again():
    """A different file list runs a fresh inner push, no dedupe."""
    dictCtx = _fdictBuildPushContext()
    clientHttp = _fclientBuildPushClient(dictCtx)
    iCalls = {"i": 0}

    def fnFakePush(*args, **kwargs):
        iCalls["i"] += 1
        return (0, "pushed")

    with patch(
        "vaibify.gui.syncDispatcher.ftResultPushToGithub",
        side_effect=fnFakePush,
    ), patch.object(
        containerGit, "fsGitHeadShaInContainer",
        return_value=_S_HEAD_SHA,
    ), patch.object(
        containerGit, "fdictGitStatusInContainer",
        return_value=_fdictRepoStatus(),
    ):
        _fnPostPush(clientHttp, ["step01/output.dat"])
        _fnPostPush(clientHttp, ["step02/another.dat"])
    assert iCalls["i"] == 2


def test_expired_cache_entry_runs_inner_push_again():
    """An expired TTL forces the push to run, not a cache hit."""
    dictCtx = _fdictBuildPushContext()
    clientHttp = _fclientBuildPushClient(dictCtx)
    iCalls = {"i": 0}

    def fnFakePush(*args, **kwargs):
        iCalls["i"] += 1
        return (0, "pushed")

    with patch(
        "vaibify.gui.syncDispatcher.ftResultPushToGithub",
        side_effect=fnFakePush,
    ), patch.object(
        containerGit, "fsGitHeadShaInContainer",
        return_value=_S_HEAD_SHA,
    ), patch.object(
        containerGit, "fdictGitStatusInContainer",
        return_value=_fdictRepoStatus(),
    ), patch.object(
        syncRoutes, "_F_RECENT_PUSH_TTL_SECONDS", 0.0,
    ):
        _fnPostPush(clientHttp, ["step01/output.dat"])
        _fnPostPush(clientHttp, ["step01/output.dat"])
    assert iCalls["i"] == 2


def test_failed_push_is_not_cached():
    """A push that did not succeed should not pollute the dedupe cache."""
    dictCtx = _fdictBuildPushContext()
    clientHttp = _fclientBuildPushClient(dictCtx)
    iCalls = {"i": 0}

    def fnFailingPush(*args, **kwargs):
        iCalls["i"] += 1
        return (1, "boom")

    with patch(
        "vaibify.gui.syncDispatcher.ftResultPushToGithub",
        side_effect=fnFailingPush,
    ), patch.object(
        containerGit, "fsGitHeadShaInContainer",
        return_value=_S_HEAD_SHA,
    ), patch.object(
        containerGit, "fdictGitStatusInContainer",
        return_value=_fdictRepoStatus(),
    ):
        _fnPostPush(clientHttp, ["step01/output.dat"])
        _fnPostPush(clientHttp, ["step01/output.dat"])
    assert iCalls["i"] == 2


def test_hash_payload_helper_is_order_independent():
    """The payload digest sorts inputs so any client ordering matches."""
    sOne = syncRoutes._fsHashPushPayload(["a.dat", "b.dat", "c.dat"])
    sTwo = syncRoutes._fsHashPushPayload(["c.dat", "a.dat", "b.dat"])
    assert sOne == sTwo
