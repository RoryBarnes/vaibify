"""Tests that /api/registry annotates containers with lock state."""

import fcntl
import os

import pytest

from vaibify.config import containerLock


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
    sPath = os.path.join(str(tmp_path), "demo.lock")
    fileHandleExternal = open(sPath, "a+")
    fileHandleExternal.write(
        '{"iPid": 77777, "iPort": 8055, "sProjectName": "demo"}'
    )
    fileHandleExternal.flush()
    fcntl.flock(fileHandleExternal, fcntl.LOCK_EX | fcntl.LOCK_NB)
    try:
        response = fixtureClient.get("/api/registry")
        dictContainer = response.json()["listContainers"][0]
        assert dictContainer["bLocked"] is True
        assert dictContainer["iLockedByPid"] == 77777
        assert dictContainer["iLockedByPort"] == 8055
    finally:
        fcntl.flock(fileHandleExternal, fcntl.LOCK_UN)
        fileHandleExternal.close()


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
