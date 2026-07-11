"""Tests for the verify/status remote routes in syncRoutes.py."""

import hashlib
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


_BA_DATA_CONTENT = b"canonical data bytes\n"
S_DATA_SHA = hashlib.sha256(_BA_DATA_CONTENT).hexdigest()


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
                "saDataFiles": ["data.csv"],
                "saPlotFiles": [],
                "saOutputFiles": [],
            },
        ],
    }


@pytest.fixture
def fixtureProjectRepo(tmp_path):
    """Create a temp project repo with the declared file on disk.

    The verifies hash the declared files live, so the fixture creates
    real content instead of a manifest entry.
    """
    sRepo = str(tmp_path / "project")
    os.makedirs(os.path.join(sRepo, "step01"), exist_ok=True)
    with open(
        os.path.join(sRepo, "step01", "data.csv"), "wb",
    ) as fileHandle:
        fileHandle.write(_BA_DATA_CONTENT)
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
    sExpected = S_DATA_SHA
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
    fixtureClientNoNetworkBlock, fixtureCtxAndApp,
):
    """Overleaf verify returns the right service name.

    The Overleaf comparison set is the pushed-figure list hashed at
    the remote paths the push flattened them to, so the test records
    a push and keys the mock by the remote path.
    """
    from vaibify.reproducibility import overleafSync
    dictCtx, _, sProjectRepo = fixtureCtxAndApp
    overleafSync.fnRecordOverleafPushManifest(
        sProjectRepo, "commit1", ["step01/data.csv"], "figures",
    )
    dictWorkflow = dictCtx["workflows"][S_CONTAINER_ID]
    dictWorkflow["dictRemotes"]["overleaf"][
        "sLastPushCommit"] = "commit1"
    with _fnPatchService(
        "overleafMirror", {"figures/data.csv": S_DATA_SHA},
    ):
        response = fixtureClientNoNetworkBlock.post(
            f"/api/sync/{S_CONTAINER_ID}/overleaf/verify",
        )
    assert response.status_code == 200
    assert response.json()["sService"] == "overleaf"


def testVerifyOverleafWithoutRecordedPushReturns409(
    fixtureClientNoNetworkBlock,
):
    """No recorded push maps to a 409 precondition, not a 500."""
    response = fixtureClientNoNetworkBlock.post(
        f"/api/sync/{S_CONTAINER_ID}/overleaf/verify",
    )
    assert response.status_code == 409
    assert "push" in response.json()["detail"].lower()


def testVerifyZenodoReturnsStatus(
    fixtureClientNoNetworkBlock, fixtureProjectRepo,
):
    """Zenodo verify routes through zenodoClient.fdictFetchRemoteHashes."""
    sExpected = S_DATA_SHA
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
    sExpected = S_DATA_SHA
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


def testVerifyReturns400OnPathTraversalServiceName(
    fixtureClientNoNetworkBlock, fixtureProjectRepo,
):
    """A traversal-shaped sService is rejected at the route layer.

    Even though sService is only used as a dict key inside the verify
    plumbing, defense-in-depth requires rejecting any value outside the
    canonical service-name whitelist before any subsequent work.
    """
    response = fixtureClientNoNetworkBlock.post(
        "/api/sync/" + S_CONTAINER_ID + "/..%2Fetc%2Fpasswd/verify",
    )
    assert response.status_code in (400, 404)


def testVerifySucceedsDespiteMalformedManifest(
    fixtureClientNoNetworkBlock, fixtureProjectRepo,
):
    """A corrupt MANIFEST.sha256 cannot block an L2 verify.

    Ladder separation (ruling 2026-07-10): the manifest is the L3
    reproducibility-envelope artifact; L2 verifies hash the declared
    files live and never read it. Under the old manifest-based verify
    this exact fixture 422'd — pinning success here is what keeps the
    L2 path decoupled.
    """
    sManifest = os.path.join(fixtureProjectRepo, "MANIFEST.sha256")
    with open(sManifest, "w", encoding="utf-8") as fileHandle:
        fileHandle.write("# malformed: no two-space separator below\n")
        fileHandle.write("garbage_line_no_separator\n")
    with _fnPatchService(
        "githubMirror", {"step01/data.csv": S_DATA_SHA},
    ):
        response = fixtureClientNoNetworkBlock.post(
            f"/api/sync/{S_CONTAINER_ID}/github/verify",
        )
    assert response.status_code == 200
    assert response.json()["iMatching"] == 1


def testVerifyReturns422OnInvalidGithubOwnerInWorkflow(
    fixtureClientNoNetworkBlock, fixtureProjectRepo, fixtureCtxAndApp,
):
    """A workflow with a malformed GitHub owner triggers 422, not 502.

    The owner ``../../etc`` is shape-invalid by GitHub's own naming
    rules; the mirror module raises ``ValueError`` from
    ``fnValidateOwnerRepo``. The verify route must surface that as a
    422 (input invalid) so the user knows to fix workflow.json, not as
    a 502 implying the remote is down.
    """
    dictCtx, _, _ = fixtureCtxAndApp
    dictWorkflow = dictCtx["workflows"][S_CONTAINER_ID]
    dictWorkflow["dictRemotes"]["github"]["sOwner"] = "../../etc"
    response = fixtureClientNoNetworkBlock.post(
        f"/api/sync/{S_CONTAINER_ID}/github/verify",
    )
    assert response.status_code == 422
    sDetail = response.json()["detail"]
    assert "github" in sDetail


def testVerifyDoesNotLeakProjectRepoPathOnRemoteError(
    fixtureClientNoNetworkBlock, fixtureProjectRepo,
):
    """A remote-side exception's redacted detail must not leak host paths."""
    from vaibify.reproducibility.githubMirror import GithubMirrorError
    with patch(
        "vaibify.reproducibility.githubMirror.fdictFetchRemoteHashes",
        side_effect=GithubMirrorError("fetch failed for /tmp/internal"),
    ):
        response = fixtureClientNoNetworkBlock.post(
            f"/api/sync/{S_CONTAINER_ID}/github/verify",
        )
    assert response.status_code == 502
    sDetail = response.json()["detail"]
    assert fixtureProjectRepo not in sDetail


def testVerifyReturns404ForUnknownContainer(
    fixtureClientNoNetworkBlock,
):
    """An unknown sContainerId triggers 404 from fdictRequireWorkflow."""
    response = fixtureClientNoNetworkBlock.post(
        "/api/sync/no_such_cid/github/verify",
    )
    assert response.status_code == 404


# --------- GET status endpoint is read-only and validates inputs ---------


def testStatusReturns400OnBadService(fixtureClient):
    """GET status validates sService against the supported whitelist."""
    response = fixtureClient.get(
        f"/api/sync/{S_CONTAINER_ID}/dropbox/status",
    )
    assert response.status_code == 400


def testStatusReturns404ForUnknownContainer(fixtureClient):
    """GET status returns 404 for an unknown sContainerId."""
    response = fixtureClient.get(
        "/api/sync/no_such_cid/github/status",
    )
    assert response.status_code == 404


def testStatusGetDoesNotMutateSyncStatusFile(
    fixtureClient, fixtureProjectRepo,
):
    """Calling GET status must not create or modify syncStatus.json.

    The status read path is documented as read-only; this guards
    against a future change accidentally introducing a write side
    effect that would race with the scheduled re-verify writer's lock.
    """
    sStatusPath = os.path.join(
        fixtureProjectRepo, ".vaibify", "syncStatus.json",
    )
    assert not os.path.exists(sStatusPath)
    response = fixtureClient.get(
        f"/api/sync/{S_CONTAINER_ID}/github/status",
    )
    assert response.status_code == 200
    assert not os.path.exists(sStatusPath)


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


# --------- Fix M5: network-isolation guard returns 409 ---------


def test_sync_verify_returns_409_when_network_isolated(
    fixtureCtxAndApp,
):
    """When the container is network-isolated, verify returns 409."""
    _, app, _ = fixtureCtxAndApp
    with patch(
        "vaibify.docker.containerManager.fbContainerIsNetworkIsolated",
        return_value=True,
    ):
        client = TestClient(app)
        response = client.post(
            f"/api/sync/{S_CONTAINER_ID}/github/verify",
        )
    assert response.status_code == 409
    sBody = response.text
    assert "network" in sBody.lower() or "isolat" in sBody.lower()
