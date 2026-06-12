"""Tests that /api/registry annotates containers with lock state."""

import fcntl
import json
import multiprocessing
import os
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


@pytest.fixture(autouse=True)
def fixtureIsolateLockDir(tmp_path, monkeypatch):
    monkeypatch.setattr(
        containerLock, "_S_LOCK_DIRECTORY", str(tmp_path),
    )


@pytest.fixture
def fixtureHubApp():
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


def _fnPatchRegistrySources(monkeypatch, listProjects):
    """Short-circuit registry discovery to a canned project list."""
    monkeypatch.setattr(
        "vaibify.config.registryManager.flistGetAllProjectsWithStatus",
        lambda: listProjects,
    )
    monkeypatch.setattr(
        "vaibify.gui.registryRoutes._ftupleDiscoverAllContainers",
        lambda dictCtx: ([], []),
    )


def testRegistryReportsUnlockedWhenNoLockFiles(
    fixtureClient, monkeypatch,
):
    _fnPatchRegistrySources(
        monkeypatch,
        [{"sName": "demo", "sContainerName": "demo"}],
    )
    response = fixtureClient.get("/api/registry")
    listContainers = response.json()["listContainers"]
    assert len(listContainers) == 1
    assert listContainers[0]["bLocked"] is False


def testRegistryReportsLockedWhenOtherProcessHoldsLock(
    fixtureClient, monkeypatch, tmp_path,
):
    _fnPatchRegistrySources(
        monkeypatch,
        [{"sName": "demo", "sContainerName": "demo"}],
    )
    contextFork = multiprocessing.get_context("fork")
    eventReady = contextFork.Event()
    processChild = contextFork.Process(
        target=_fnHoldLockInChildProcess,
        args=(str(tmp_path), "demo", 8055, eventReady),
    )
    processChild.start()
    try:
        _fnAwaitLockFile(tmp_path, "demo")
        response = fixtureClient.get("/api/registry")
        dictContainer = response.json()["listContainers"][0]
        assert dictContainer["bLocked"] is True
        assert dictContainer["iLockedByPid"] == processChild.pid
        assert dictContainer["iLockedByPort"] == 8055
    finally:
        eventReady.set()
        processChild.join(timeout=5)


def testRegistryRefreshReapsClaimOfDeadHolder(
    fixtureClient, monkeypatch, tmp_path,
):
    """A held flock recorded against a dead PID unlocks on refresh."""
    _fnPatchRegistrySources(
        monkeypatch,
        [{"sName": "demo", "sContainerName": "demo"}],
    )
    sPath = os.path.join(str(tmp_path), "demo.lock")
    fileHandleStuck = open(sPath, "a+")
    fileHandleStuck.write(json.dumps({
        "iPid": _fiSpawnDeadPid(), "iPort": 8055,
        "sProjectName": "demo",
    }))
    fileHandleStuck.flush()
    fcntl.flock(fileHandleStuck, fcntl.LOCK_EX | fcntl.LOCK_NB)
    try:
        response = fixtureClient.get("/api/registry")
        dictContainer = response.json()["listContainers"][0]
        assert dictContainer["bLocked"] is False
        assert not os.path.isfile(sPath)
    finally:
        fileHandleStuck.close()


def testRegistryReportsUnlockedForSelfHeldClaim(
    fixtureClient, monkeypatch,
):
    _fnPatchRegistrySources(
        monkeypatch,
        [{"sName": "demo", "sContainerName": "demo"}],
    )
    fixtureClient.post("/api/registry/demo/claim")
    response = fixtureClient.get("/api/registry")
    dictContainer = response.json()["listContainers"][0]
    assert dictContainer["bLocked"] is False


def testRegistryMarksEntryUnlockedWhenNameIsMissing(
    fixtureClient, monkeypatch,
):
    """Containers without sName cannot be locked: bLocked=False, no pid/port."""
    _fnPatchRegistrySources(
        monkeypatch,
        [{"sContainerName": "orphan"}],
    )
    response = fixtureClient.get("/api/registry")
    dictContainer = response.json()["listContainers"][0]
    assert dictContainer["bLocked"] is False
    assert "iLockedByPid" not in dictContainer
    assert "iLockedByPort" not in dictContainer
