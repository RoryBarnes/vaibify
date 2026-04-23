"""Tests for the /api/registry/{sName}/claim and /release routes."""

import os

import pytest

from vaibify.config import containerLock


@pytest.fixture(autouse=True)
def fixtureIsolateLockDir(tmp_path, monkeypatch):
    """Redirect ~/.vaibify/locks/ to a per-test tmp_path."""
    monkeypatch.setattr(
        containerLock, "_S_LOCK_DIRECTORY", str(tmp_path),
    )


@pytest.fixture
def fixtureHubApp():
    """Build a minimal hub app with claim/release routes."""
    from fastapi import FastAPI
    from vaibify.gui.registryRoutes import fnRegisterRegistryRoutes
    app = FastAPI()
    app.state.dictContainerLocks = {}
    app.state.iHubPort = 8050
    dictCtx = {"require": lambda: None, "docker": None}
    fnRegisterRegistryRoutes(app, dictCtx)
    return app


@pytest.fixture
def fixtureClient(fixtureHubApp):
    from starlette.testclient import TestClient
    return TestClient(fixtureHubApp)


def testClaimReturnsOkWhenContainerFree(fixtureClient, fixtureHubApp):
    response = fixtureClient.post("/api/registry/demo/claim")
    assert response.status_code == 200
    assert response.json() == {"sName": "demo", "bClaimed": True}
    assert "demo" in fixtureHubApp.state.dictContainerLocks


def testClaimReturns409WhenContainerLockedByOtherProcess(
    fixtureClient, tmp_path,
):
    import fcntl
    sPath = os.path.join(str(tmp_path), "demo.lock")
    fileHandleExternal = open(sPath, "a+")
    fileHandleExternal.write(
        '{"iPid": 99999, "iPort": 8099, "sProjectName": "demo"}'
    )
    fileHandleExternal.flush()
    fcntl.flock(fileHandleExternal, fcntl.LOCK_EX | fcntl.LOCK_NB)
    try:
        response = fixtureClient.post("/api/registry/demo/claim")
        assert response.status_code == 409
        dictDetail = response.json()["detail"]
        assert dictDetail["sName"] == "demo"
        assert dictDetail["iLockedByPid"] == 99999
        assert dictDetail["iLockedByPort"] == 8099
    finally:
        fcntl.flock(fileHandleExternal, fcntl.LOCK_UN)
        fileHandleExternal.close()


def testClaimIsIdempotentInSameHubProcess(fixtureClient, fixtureHubApp):
    fixtureClient.post("/api/registry/demo/claim")
    response = fixtureClient.post("/api/registry/demo/claim")
    assert response.status_code == 200
    assert len(fixtureHubApp.state.dictContainerLocks) == 1


def testReleaseFreesTheLock(fixtureClient, fixtureHubApp):
    fixtureClient.post("/api/registry/demo/claim")
    response = fixtureClient.post("/api/registry/demo/release")
    assert response.status_code == 200
    assert response.json() == {"sName": "demo", "bReleased": True}
    assert "demo" not in fixtureHubApp.state.dictContainerLocks


def testReleaseIsNoopWhenNothingToRelease(fixtureClient):
    response = fixtureClient.post("/api/registry/missing/release")
    assert response.status_code == 200


def testClaimAfterReleaseSucceeds(fixtureClient):
    fixtureClient.post("/api/registry/demo/claim")
    fixtureClient.post("/api/registry/demo/release")
    response = fixtureClient.post("/api/registry/demo/claim")
    assert response.status_code == 200


def testClaimRejectsDotDotName(fixtureClient, tmp_path):
    """The path segment '..' reaches the handler and must be rejected."""
    response = fixtureClient.post("/api/registry/../claim")
    assert response.status_code in (400, 404)
    response = fixtureClient.post("/api/registry/..%2E/claim")
    assert response.status_code == 400
    assert not list(tmp_path.glob("*"))


def testClaimRejectsLeadingDotName(fixtureClient):
    """Names starting with '.' are rejected to block hidden-file tricks."""
    response = fixtureClient.post("/api/registry/.hidden/claim")
    assert response.status_code == 400


def testReleaseRejectsLeadingDotName(fixtureClient):
    response = fixtureClient.post("/api/registry/.hidden/release")
    assert response.status_code == 400


def testClaimRejectsNamesWithSpecialCharacters(fixtureClient):
    response = fixtureClient.post("/api/registry/name%20with%20space/claim")
    assert response.status_code == 400
