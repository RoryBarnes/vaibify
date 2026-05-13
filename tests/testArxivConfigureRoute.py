"""Tests for POST /api/sync/{id}/arxiv/configure.

The verify hop is mocked so these tests do not touch arXiv.
"""

import os
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from vaibify.gui.routes.syncRoutes import fnRegisterAll


S_CONTAINER_ID = "arxiv_cid"


def _fdictBuildWorkflow(sProjectRepo):
    """Return a workflow dict with an empty dictRemotes."""
    return {
        "sProjectRepoPath": sProjectRepo,
        "dictRemotes": {},
        "listSteps": [],
    }


@pytest.fixture
def fixtureProjectRepo(tmp_path):
    sRepo = str(tmp_path / "project")
    os.makedirs(sRepo, exist_ok=True)
    return sRepo


@pytest.fixture
def fixtureSaveLog():
    """Capture (sId, dictWorkflow) pairs each save call receives."""
    return []


@pytest.fixture
def fixtureWorkflow(fixtureProjectRepo):
    return _fdictBuildWorkflow(fixtureProjectRepo)


@pytest.fixture
def fixtureClient(fixtureWorkflow, fixtureSaveLog):
    app = FastAPI()
    app.state.listLifespanStartup = []
    app.state.listLifespanShutdown = []
    dictWorkflows = {S_CONTAINER_ID: fixtureWorkflow}

    def _fnSave(sId, dictWf):
        fixtureSaveLog.append((sId, dictWf))

    dictCtx = {
        "docker": None,
        "workflows": dictWorkflows,
        "paths": {},
        "pipelineTasks": {},
        "sourceCodeDeps": {},
        "setAllowedContainers": {S_CONTAINER_ID},
        "sSessionToken": "tok",
        "require": lambda: None,
        "save": _fnSave,
        "variables": lambda sId: {},
        "workflowDir": lambda sId: fixtureWorkflow["sProjectRepoPath"],
    }
    fnRegisterAll(app, dictCtx)
    return TestClient(app)


def _fdictMockVerifyOk(sProjectRepo, dictWorkflow, sService, sNowIso=None):
    """Stand-in for scheduledReverify.fdictVerifyRemoteService."""
    return {
        "sService": sService,
        "sLastVerified": "2026-05-13T12:00:00Z",
        "iTotalFiles": 0,
        "iMatching": 0,
        "listDiverged": [],
    }


def _fnMockWriteSyncStatus(sProjectRepo, dictStatus):
    """No-op stand-in for scheduledReverify.fnWriteSyncStatus."""


def _ftPatchVerifyPath():
    """Patch both verify entry points the route relies on."""
    return (
        patch(
            "vaibify.reproducibility.scheduledReverify."
            "fdictVerifyRemoteService",
            side_effect=_fdictMockVerifyOk,
        ),
        patch(
            "vaibify.reproducibility.scheduledReverify."
            "fnWriteSyncStatus",
            side_effect=_fnMockWriteSyncStatus,
        ),
    )


def test_configure_writes_id_and_triggers_verify(
    fixtureClient, fixtureWorkflow, fixtureSaveLog,
):
    """Happy path: valid ID lands in dictRemotes and verify runs."""
    patchVerify, patchWrite = _ftPatchVerifyPath()
    with patchVerify, patchWrite:
        response = fixtureClient.post(
            f"/api/sync/{S_CONTAINER_ID}/arxiv/configure",
            json={"sArxivId": "2401.12345"},
        )
    assert response.status_code == 200
    dictBody = response.json()
    assert dictBody["dictArxivConfig"] == {"sArxivId": "2401.12345"}
    assert dictBody["sVerifyError"] == ""
    assert (
        fixtureWorkflow["dictRemotes"]["arxiv"]["sArxivId"]
        == "2401.12345"
    )
    assert fixtureSaveLog and fixtureSaveLog[0][0] == S_CONTAINER_ID


def test_configure_accepts_legacy_id_format(fixtureClient, fixtureWorkflow):
    """Legacy IDs like astro-ph/0601001v1 are accepted by the regex."""
    patchVerify, patchWrite = _ftPatchVerifyPath()
    with patchVerify, patchWrite:
        response = fixtureClient.post(
            f"/api/sync/{S_CONTAINER_ID}/arxiv/configure",
            json={"sArxivId": "astro-ph/0601001v1"},
        )
    assert response.status_code == 200
    assert (
        fixtureWorkflow["dictRemotes"]["arxiv"]["sArxivId"]
        == "astro-ph/0601001v1"
    )


def test_configure_persists_path_map(fixtureClient, fixtureWorkflow):
    """dictPathMap entries are persisted alongside sArxivId."""
    patchVerify, patchWrite = _ftPatchVerifyPath()
    dictPathMap = {"figures/fig1.pdf": "paper/figs/fig1.pdf"}
    with patchVerify, patchWrite:
        response = fixtureClient.post(
            f"/api/sync/{S_CONTAINER_ID}/arxiv/configure",
            json={
                "sArxivId": "2401.12345",
                "dictPathMap": dictPathMap,
            },
        )
    assert response.status_code == 200
    assert (
        fixtureWorkflow["dictRemotes"]["arxiv"]["dictPathMap"]
        == dictPathMap
    )


def test_configure_remove_clears_arxiv_entry(
    fixtureClient, fixtureWorkflow,
):
    """bRemove deletes dictRemotes.arxiv and skips the verify hop."""
    fixtureWorkflow["dictRemotes"]["arxiv"] = {"sArxivId": "2401.12345"}
    response = fixtureClient.post(
        f"/api/sync/{S_CONTAINER_ID}/arxiv/configure",
        json={"bRemove": True},
    )
    assert response.status_code == 200
    assert response.json()["dictArxivConfig"] == {}
    assert "arxiv" not in fixtureWorkflow["dictRemotes"]


def test_configure_rejects_malformed_id(fixtureClient):
    """An ID that does not match either regex is rejected with 400."""
    response = fixtureClient.post(
        f"/api/sync/{S_CONTAINER_ID}/arxiv/configure",
        json={"sArxivId": "not-an-arxiv-id"},
    )
    assert response.status_code == 400


def test_configure_rejects_shell_metacharacters_in_id(fixtureClient):
    """Shell metacharacters in the ID are rejected by the regex."""
    response = fixtureClient.post(
        f"/api/sync/{S_CONTAINER_ID}/arxiv/configure",
        json={"sArxivId": "2401.12345; rm -rf /"},
    )
    assert response.status_code == 400


def test_configure_rejects_empty_id_without_remove(fixtureClient):
    """A request without sArxivId and without bRemove is invalid."""
    response = fixtureClient.post(
        f"/api/sync/{S_CONTAINER_ID}/arxiv/configure",
        json={},
    )
    assert response.status_code == 400


def test_configure_rejects_path_map_parent_escape(fixtureClient):
    """dictPathMap values with '..' segments are rejected with 400."""
    response = fixtureClient.post(
        f"/api/sync/{S_CONTAINER_ID}/arxiv/configure",
        json={
            "sArxivId": "2401.12345",
            "dictPathMap": {"fig.pdf": "../../etc/passwd"},
        },
    )
    assert response.status_code == 400


def test_configure_rejects_path_map_null_byte(fixtureClient):
    """dictPathMap entries with null bytes are rejected with 400."""
    response = fixtureClient.post(
        f"/api/sync/{S_CONTAINER_ID}/arxiv/configure",
        json={
            "sArxivId": "2401.12345",
            "dictPathMap": {"fig.pdf": "paper\x00.pdf"},
        },
    )
    assert response.status_code == 400


def test_configure_surfaces_verify_error_on_response(
    fixtureClient, fixtureWorkflow,
):
    """A verify-time exception is captured in sVerifyError, not 5xx."""
    with patch(
        "vaibify.reproducibility.scheduledReverify."
        "fdictVerifyRemoteService",
        side_effect=RuntimeError("network exploded"),
    ):
        response = fixtureClient.post(
            f"/api/sync/{S_CONTAINER_ID}/arxiv/configure",
            json={"sArxivId": "2401.12345"},
        )
    assert response.status_code == 200
    dictBody = response.json()
    assert "network exploded" in dictBody["sVerifyError"]
    assert (
        fixtureWorkflow["dictRemotes"]["arxiv"]["sArxivId"]
        == "2401.12345"
    )
