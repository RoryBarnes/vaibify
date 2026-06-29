"""Tests for the /api/registry/{sName}/claim and /release routes."""

import fcntl
import json
import multiprocessing
import os
import signal
import time

import pytest

from vaibify.config import containerLock


def _fnHoldLockInChildProcess(sTempDir, sProjectName, iPort, eventReady):
    """Child: acquire the lock and block until the parent sets event."""
    import vaibify.config.containerLock as childLockModule
    childLockModule._S_LOCK_DIRECTORY = sTempDir
    fileHandleChildLock = childLockModule.fnAcquireContainerLock(
        sProjectName, iPort,
    )
    eventReady.wait(timeout=10)
    fileHandleChildLock.close()


def _fnAwaitLockFile(tmp_path, sProjectName):
    """Block until the child has created its lock file."""
    for _ in range(100):
        if (tmp_path / f"{sProjectName}.lock").exists():
            return
        time.sleep(0.05)


def _fiSpawnDeadPid():
    """Return the PID of a forked child that has already exited."""
    contextFork = multiprocessing.get_context("fork")
    processChild = contextFork.Process(target=lambda: None)
    processChild.start()
    processChild.join(timeout=5)
    return processChild.pid


def _ffileHoldFlockWithDeadHolderPayload(tmp_path, sProjectName):
    """Hold a flock whose payload records a dead PID (orphaned claim)."""
    sPath = os.path.join(str(tmp_path), f"{sProjectName}.lock")
    fileHandleStuck = open(sPath, "a+")
    fileHandleStuck.write(json.dumps({
        "iPid": _fiSpawnDeadPid(), "iPort": 8099,
        "sProjectName": sProjectName,
    }))
    fileHandleStuck.flush()
    fcntl.flock(fileHandleStuck, fcntl.LOCK_EX | fcntl.LOCK_NB)
    return fileHandleStuck


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
    app.state.dictContainerOwners = {}
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
    dictBody = response.json()
    assert dictBody["sName"] == "demo"
    assert dictBody["bClaimed"] is True
    assert dictBody["sLeaseId"]
    assert "demo" in fixtureHubApp.state.dictContainerOwners


def testClaimReturns409WhenContainerLockedByOtherProcess(
    fixtureClient, tmp_path,
):
    contextFork = multiprocessing.get_context("fork")
    eventReady = contextFork.Event()
    processChild = contextFork.Process(
        target=_fnHoldLockInChildProcess,
        args=(str(tmp_path), "demo", 8099, eventReady),
    )
    processChild.start()
    try:
        _fnAwaitLockFile(tmp_path, "demo")
        response = fixtureClient.post("/api/registry/demo/claim")
        assert response.status_code == 409
        dictDetail = response.json()["detail"]
        assert dictDetail["sName"] == "demo"
        assert dictDetail["iLockedByPid"] == processChild.pid
        assert dictDetail["iLockedByPort"] == 8099
    finally:
        eventReady.set()
        processChild.join(timeout=5)


def testClaimSucceedsWhenRecordedHolderIsDead(
    fixtureClient, fixtureHubApp, tmp_path,
):
    """A flock that outlived its dead holder must be taken over silently."""
    fileHandleStuck = _ffileHoldFlockWithDeadHolderPayload(
        tmp_path, "demo",
    )
    try:
        response = fixtureClient.post("/api/registry/demo/claim")
        assert response.status_code == 200
        dictBody = response.json()
        assert dictBody["bClaimed"] is True
        assert dictBody["sLeaseId"]
        assert "demo" in fixtureHubApp.state.dictContainerOwners
    finally:
        fileHandleStuck.close()


def testClaimSucceedsAfterOwnerKilledWithoutHubRestart(
    fixtureClient, tmp_path,
):
    """SIGKILLing the claim owner frees the container for the next claim."""
    contextFork = multiprocessing.get_context("fork")
    eventReady = contextFork.Event()
    processChild = contextFork.Process(
        target=_fnHoldLockInChildProcess,
        args=(str(tmp_path), "demo", 8099, eventReady),
    )
    processChild.start()
    _fnAwaitLockFile(tmp_path, "demo")
    os.kill(processChild.pid, signal.SIGKILL)
    processChild.join(timeout=5)
    response = fixtureClient.post("/api/registry/demo/claim")
    assert response.status_code == 200
    dictBody = response.json()
    assert dictBody["bClaimed"] is True
    assert dictBody["sLeaseId"]


def testClaimIsIdempotentWhenSameLeaseRepresented(
    fixtureClient, fixtureHubApp,
):
    """A reload re-presenting its stored lease re-asserts ownership (200)."""
    sLeaseId = fixtureClient.post(
        "/api/registry/demo/claim",
    ).json()["sLeaseId"]
    response = fixtureClient.post(
        "/api/registry/demo/claim", params={"sLeaseId": sLeaseId},
    )
    assert response.status_code == 200
    assert response.json()["sLeaseId"] == sLeaseId
    assert len(fixtureHubApp.state.dictContainerOwners) == 1


def testClaimRejectsForeignSessionWith409(fixtureClient, fixtureHubApp):
    """A second tab with no matching lease is refused, never short-circuited."""
    fixtureClient.post("/api/registry/demo/claim")
    response = fixtureClient.post("/api/registry/demo/claim")
    assert response.status_code == 409
    dictDetail = response.json()["detail"]
    assert dictDetail["bClaimed"] is False
    assert "sLeaseId" not in dictDetail
    assert len(fixtureHubApp.state.dictContainerOwners) == 1


def testReleaseFreesTheLock(fixtureClient, fixtureHubApp):
    sLeaseId = fixtureClient.post(
        "/api/registry/demo/claim",
    ).json()["sLeaseId"]
    response = fixtureClient.post(
        "/api/registry/demo/release", params={"sLeaseId": sLeaseId},
    )
    assert response.status_code == 200
    assert response.json() == {"sName": "demo", "bReleased": True}
    assert "demo" not in fixtureHubApp.state.dictContainerOwners


def testReleaseRejectsNonOwnerLease(fixtureClient, fixtureHubApp):
    """A release with the wrong lease leaves ownership intact (no leak free)."""
    fixtureClient.post("/api/registry/demo/claim")
    response = fixtureClient.post(
        "/api/registry/demo/release", params={"sLeaseId": "not-the-owner"},
    )
    assert response.status_code == 200
    assert response.json()["bReleased"] is False
    assert "demo" in fixtureHubApp.state.dictContainerOwners


def testReleaseIsNoopWhenNothingToRelease(fixtureClient):
    response = fixtureClient.post("/api/registry/missing/release")
    assert response.status_code == 200
    assert response.json()["bReleased"] is False


def testClaimAfterReleaseSucceeds(fixtureClient):
    sLeaseId = fixtureClient.post(
        "/api/registry/demo/claim",
    ).json()["sLeaseId"]
    fixtureClient.post(
        "/api/registry/demo/release", params={"sLeaseId": sLeaseId},
    )
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
