"""Lazy fetch contract for the manifest body.

The poll snapshot deliberately excludes ``MANIFEST.sha256``'s text so
a 100-step workflow does not pay multi-KB body cost on every poll.
The viewer (or any other UI/agent path that needs the body) fetches
it on demand via ``GET /api/workflow/{id}/manifest/text``; the
poll-side path uses a sha-keyed cache that hydrates the snapshot
lazily, so subsequent polls observing the same manifest sha pay zero
extra docker round trips.
"""

from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from vaibify.gui.routes import pipelineRoutes


_S_CONTAINER_ID = "ctr-manifest-text"
_S_REPO = "/workspace/myrepo"


def _fdictBuildCtxWithWorkflow():
    """Return a minimal dictCtx threading a workflow with a repo path."""
    dictWorkflow = {
        "sProjectRepoPath": _S_REPO, "sWorkflowName": "demo",
        "listSteps": [],
    }
    return {
        "workflows": {_S_CONTAINER_ID: dictWorkflow},
        "paths": {_S_CONTAINER_ID: _S_REPO + "/.vaibify/d.json"},
        "require": lambda: None,
        "save": lambda sId, dictWf: None,
        "variables": lambda sId: {},
        "docker": object(),
        "files": lambda sId: None,
        "sessions": {},
        "sessionEpochs": {},
    }


@pytest.fixture
def clientManifest():
    """Bare app with pipelineRoutes registered against a stub ctx."""
    app = FastAPI()
    app.state.listLifespanStartup = []
    app.state.listLifespanShutdown = []
    dictCtx = _fdictBuildCtxWithWorkflow()
    pipelineRoutes._fnRegisterManifestText(app, dictCtx)
    yield TestClient(app)


def test_snapshot_no_longer_carries_manifest_text():
    """``TUPLE_SNAPSHOT_SKIP_TEXT_PATHS`` covers MANIFEST.sha256."""
    from vaibify.reproducibility.repoFiles import (
        TUPLE_SNAPSHOT_SKIP_TEXT_PATHS,
    )
    assert "MANIFEST.sha256" in TUPLE_SNAPSHOT_SKIP_TEXT_PATHS


def test_manifest_text_route_returns_body_on_demand(clientManifest):
    """The viewer endpoint returns the body even when poll snapshots omit it."""
    sBody = "abc  out/a.dat\ndef  out/b.dat\n"
    with patch.object(
        pipelineRoutes, "_fsFetchManifestTextFromContainer",
        return_value=sBody,
    ):
        responseHttp = clientManifest.get(
            "/api/workflow/" + _S_CONTAINER_ID + "/manifest/text",
        )
    assert responseHttp.status_code == 200
    dictResult = responseHttp.json()
    assert dictResult["sText"] == sBody
    assert dictResult["bTruncated"] is False
    assert dictResult["iBytes"] == len(sBody.encode("utf-8"))


def test_manifest_text_route_truncates_at_iMaxBytes(clientManifest):
    """A body larger than iMaxBytes returns bTruncated True with iBytes set."""
    sBody = "x" * 4096
    with patch.object(
        pipelineRoutes, "_fsFetchManifestTextFromContainer",
        return_value=sBody,
    ):
        responseHttp = clientManifest.get(
            "/api/workflow/" + _S_CONTAINER_ID
            + "/manifest/text?iMaxBytes=512",
        )
    dictResult = responseHttp.json()
    assert dictResult["bTruncated"] is True
    assert dictResult["iBytes"] == 4096
    assert len(dictResult["sText"]) == 512


def test_manifest_text_route_missing_manifest_returns_empty(clientManifest):
    """An absent manifest reports empty body / iBytes=0, never an HTTP error."""
    with patch.object(
        pipelineRoutes, "_fsFetchManifestTextFromContainer",
        return_value=None,
    ):
        responseHttp = clientManifest.get(
            "/api/workflow/" + _S_CONTAINER_ID + "/manifest/text",
        )
    assert responseHttp.status_code == 200
    dictResult = responseHttp.json()
    assert dictResult["sText"] == ""
    assert dictResult["iBytes"] == 0


def test_iMaxBytes_clamped_to_hard_cap():
    """iMaxBytes greater than the hard cap is silently clamped."""
    iClamped = pipelineRoutes._fiClampManifestMaxBytes(10**12)
    assert iClamped == pipelineRoutes._I_MANIFEST_TEXT_HARD_MAX_BYTES


def test_iMaxBytes_zero_falls_back_to_default():
    iClamped = pipelineRoutes._fiClampManifestMaxBytes(0)
    assert iClamped == pipelineRoutes._I_MANIFEST_TEXT_DEFAULT_MAX_BYTES


def test_manifest_text_cache_hydrates_snapshot_lazily():
    """The per-container manifest text cache is keyed by sha."""
    dictCtx = {}
    dictCache = pipelineRoutes._fdictManifestTextCache(dictCtx, "cid")
    # Inserting a sha → text pair and re-reading must return the same dict
    # instance: the cache is mutated in place per container.
    dictCache["abc"] = "manifest body\n"
    dictRe = pipelineRoutes._fdictManifestTextCache(dictCtx, "cid")
    assert dictRe is dictCache
    assert dictRe["abc"] == "manifest body\n"


def test_manifest_text_cache_evicts_stale_sha_on_new_entry():
    """Only the current sha survives once a new one is recorded."""
    dictCache = {"oldsha": "old body", "currentsha": "current body"}
    pipelineRoutes._fnEvictStaleManifestText(dictCache, "currentsha")
    assert "oldsha" not in dictCache
    assert dictCache["currentsha"] == "current body"
