"""Tests for the verify/status remote routes in syncRoutes.py."""

import json
import os
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from vaibify.gui.routes.syncRoutes import (
    _fnRegisterRemoteVerify,
    _fnRegisterRemoteVerifyStatus,
)
from vaibify.reproducibility import scheduledReverify


S_CONTAINER_ID = "verify_remote_cid"


def _fdictBuildWorkflow(sProjectRepo):
    """Return a workflow dict with three configured remotes."""
    return {
        "sProjectRepoPath": sProjectRepo,
        "dictRemotes": {
            "github": {
                "sOwner": "owner",
                "sRepo": "repo",
                "sBranch": "main",
            },
            "overleaf": {"sProjectId": "project1234"},
            "zenodo": {"sRecordId": "98765", "sService": "sandbox"},
        },
        "listSteps": [
            {
                "sDirectory": "step01",
                "saDataFiles": ["step01/data.csv"],
                "saPlotFiles": [],
                "saOutputFiles": [],
            },
        ],
    }


def _fnWriteManifestForOneFile(sProjectRepo, sExpectedHash):
    """Write a single-entry MANIFEST.sha256 for the test repo."""
    sManifest = os.path.join(sProjectRepo, "MANIFEST.sha256")
    with open(sManifest, "w", encoding="utf-8") as fileHandle:
        fileHandle.write(
            "# SHA-256 manifest of workflow outputs\n"
            f"{sExpectedHash}  step01/data.csv\n"
        )


@pytest.fixture
def fixtureProjectRepo(tmp_path):
    """Create a temp project repo with a manifest entry."""
    sRepo = str(tmp_path / "project")
    os.makedirs(os.path.join(sRepo, "step01"), exist_ok=True)
    _fnWriteManifestForOneFile(sRepo, "a" * 64)
    return sRepo


@pytest.fixture
def fixtureCtxAndApp(fixtureProjectRepo):
    """Build a minimal FastAPI app with verify + status routes."""
    app = FastAPI()
    dictWorkflow = _fdictBuildWorkflow(fixtureProjectRepo)
    dictWorkflows = {S_CONTAINER_ID: dictWorkflow}
    dictCtx = {
        "docker": None,
        "workflows": dictWorkflows,
        "require": lambda: None,
    }
    _fnRegisterRemoteVerify(app, dictCtx)
    _fnRegisterRemoteVerifyStatus(app, dictCtx)
    return dictCtx, app, fixtureProjectRepo


@pytest.fixture
def fixtureClientNoNetworkBlock(fixtureCtxAndApp):
    """TestClient with the network-isolation guard stubbed to no-op."""
    _, app, _ = fixtureCtxAndApp
    with patch(
        "vaibify.gui.routes.syncRoutes._fnRequireNetworkAccess",
        lambda sId: None,
    ):
        yield TestClient(app)


@pytest.fixture
def fixtureClient(fixtureCtxAndApp):
    """TestClient with no isolation patch (status endpoint doesn't need it)."""
    _, app, _ = fixtureCtxAndApp
    return TestClient(app)


def _fnPatchService(sServiceModule, dictReturn):
    """Patch a mirror module's fdictFetchRemoteHashes to a fixed dict."""
    return patch(
        f"vaibify.reproducibility.{sServiceModule}."
        "fdictFetchRemoteHashes",
        return_value=dictReturn,
    )


# --------- POST verify happy paths ---------


def testVerifyGithubReturnsStatus(
    fixtureClientNoNetworkBlock, fixtureProjectRepo,
):
    """GitHub verify returns iMatching/iTotalFiles for a matching remote."""
    sExpected = "a" * 64
    with _fnPatchService(
        "githubMirror", {"step01/data.csv": sExpected},
    ):
        response = fixtureClientNoNetworkBlock.post(
            f"/api/sync/{S_CONTAINER_ID}/github/verify",
        )
    assert response.status_code == 200
    dictBody = response.json()
    assert dictBody["sService"] == "github"
    assert dictBody["iTotalFiles"] == 1
    assert dictBody["iMatching"] == 1
    assert dictBody["listDiverged"] == []


def testVerifyOverleafReturnsStatus(
    fixtureClientNoNetworkBlock, fixtureProjectRepo,
):
    """Overleaf verify returns the right service name."""
    sExpected = "a" * 64
    with _fnPatchService(
        "overleafMirror", {"step01/data.csv": sExpected},
    ):
        response = fixtureClientNoNetworkBlock.post(
            f"/api/sync/{S_CONTAINER_ID}/overleaf/verify",
        )
    assert response.status_code == 200
    assert response.json()["sService"] == "overleaf"


def testVerifyZenodoReturnsStatus(
    fixtureClientNoNetworkBlock, fixtureProjectRepo,
):
    """Zenodo verify routes through zenodoClient.fdictFetchRemoteHashes."""
    sExpected = "a" * 64
    with _fnPatchService(
        "zenodoClient", {"step01/data.csv": sExpected},
    ):
        response = fixtureClientNoNetworkBlock.post(
            f"/api/sync/{S_CONTAINER_ID}/zenodo/verify",
        )
    assert response.status_code == 200
    assert response.json()["sService"] == "zenodo"


# --------- syncStatus.json persistence ---------


def testVerifyPersistsStatusToSyncStatusJson(
    fixtureClientNoNetworkBlock, fixtureProjectRepo,
):
    """A successful verify writes <repo>/.vaibify/syncStatus.json."""
    sExpected = "a" * 64
    with _fnPatchService(
        "githubMirror", {"step01/data.csv": sExpected},
    ):
        fixtureClientNoNetworkBlock.post(
            f"/api/sync/{S_CONTAINER_ID}/github/verify",
        )
    sStatusPath = os.path.join(
        fixtureProjectRepo, ".vaibify", "syncStatus.json",
    )
    assert os.path.isfile(sStatusPath)
    with open(sStatusPath, "r", encoding="utf-8") as fileHandle:
        dictAll = json.load(fileHandle)
    assert "github" in dictAll
    assert dictAll["github"]["sService"] == "github"


# --------- POST verify error paths ---------


def testVerifyReturns502OnFetchError(
    fixtureClientNoNetworkBlock, fixtureProjectRepo,
):
    """Underlying fetch raises → route returns 502 with redacted detail."""
    from vaibify.reproducibility.githubMirror import GithubMirrorError
    with patch(
        "vaibify.reproducibility.githubMirror.fdictFetchRemoteHashes",
        side_effect=GithubMirrorError("rate limit hit"),
    ):
        response = fixtureClientNoNetworkBlock.post(
            f"/api/sync/{S_CONTAINER_ID}/github/verify",
        )
    assert response.status_code == 502
    assert "github" in response.json()["detail"]


def testVerifyReturns400OnBadService(
    fixtureClientNoNetworkBlock, fixtureProjectRepo,
):
    """An unknown sService is rejected with 400."""
    response = fixtureClientNoNetworkBlock.post(
        f"/api/sync/{S_CONTAINER_ID}/dropbox/verify",
    )
    assert response.status_code == 400


# --------- GET status ---------


def testGetStatusReturnsCachedEntry(
    fixtureClient, fixtureProjectRepo,
):
    """GET status returns the persisted entry after a verify."""
    dictPersisted = {
        "sService": "github",
        "sLastVerified": "2026-05-03T12:00:00Z",
        "iTotalFiles": 1,
        "iMatching": 1,
        "listDiverged": [],
    }
    scheduledReverify.fnWriteSyncStatus(fixtureProjectRepo, dictPersisted)
    response = fixtureClient.get(
        f"/api/sync/{S_CONTAINER_ID}/github/status",
    )
    assert response.status_code == 200
    dictBody = response.json()
    assert dictBody["sLastVerified"] == "2026-05-03T12:00:00Z"
    assert dictBody["iMatching"] == 1


def testGetStatusReturnsEmptyDefaultWhenNeverVerified(
    fixtureClient, fixtureProjectRepo,
):
    """GET status returns the empty default for an unseen service."""
    response = fixtureClient.get(
        f"/api/sync/{S_CONTAINER_ID}/zenodo/status",
    )
    assert response.status_code == 200
    dictBody = response.json()
    assert dictBody["sLastVerified"] is None
    assert dictBody["iTotalFiles"] == 0
    assert dictBody["listDiverged"] == []
