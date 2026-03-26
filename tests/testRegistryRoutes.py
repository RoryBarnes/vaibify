"""Tests for vaibify.gui.registryRoutes."""

import json
import os

import pytest

from vaibify.config import registryManager


@pytest.fixture(autouse=True)
def fixtureIsolateRegistry(tmp_path, monkeypatch):
    """Redirect registry to a temp directory for every test."""
    sRegistryDir = str(tmp_path / ".vaibify")
    sRegistryPath = os.path.join(sRegistryDir, "registry.json")
    monkeypatch.setattr(
        registryManager, "_S_REGISTRY_DIRECTORY", sRegistryDir,
    )
    monkeypatch.setattr(
        registryManager, "_S_REGISTRY_PATH", sRegistryPath,
    )


def _fnWriteMinimalConfig(tmp_path, sProjectName="test-project"):
    """Create a minimal vaibify.yml in a temp project directory."""
    sProjectDir = str(tmp_path / sProjectName)
    os.makedirs(sProjectDir, exist_ok=True)
    sConfigPath = os.path.join(sProjectDir, "vaibify.yml")
    with open(sConfigPath, "w") as fileHandle:
        fileHandle.write(f"projectName: {sProjectName}\n")
    return sProjectDir


@pytest.fixture
def fixtureApp():
    """Create a hub-mode app with Docker mocked out."""
    from fastapi import FastAPI
    from vaibify.gui.registryRoutes import fnRegisterRegistryRoutes

    app = FastAPI()
    dictCtx = {"require": lambda: None, "docker": None}
    fnRegisterRegistryRoutes(app, dictCtx)
    return app


@pytest.fixture
def fixtureClient(fixtureApp):
    """Create a test client for the hub app."""
    from starlette.testclient import TestClient
    return TestClient(fixtureApp)


# --- GET /api/registry ---

def testGetRegistryReturnsEmptyList(fixtureClient, monkeypatch):
    monkeypatch.setattr(
        "vaibify.config.registryManager.flistGetAllProjectsWithStatus",
        lambda: [],
    )
    response = fixtureClient.get("/api/registry")
    assert response.status_code == 200
    dictResult = response.json()
    assert dictResult["listContainers"] == []
    assert dictResult["listUnrecognized"] == []


def testGetRegistryReturnsProjects(fixtureClient, monkeypatch):
    listProjects = [
        {
            "sName": "proj", "sContainerName": "proj",
            "sStatus": "running", "bRunning": True,
        },
    ]
    monkeypatch.setattr(
        "vaibify.config.registryManager.flistGetAllProjectsWithStatus",
        lambda: listProjects,
    )
    response = fixtureClient.get("/api/registry")
    assert response.status_code == 200
    assert len(response.json()["listContainers"]) == 1


# --- POST /api/registry ---

def testAddProjectSuccess(fixtureClient, tmp_path):
    sProjectDir = _fnWriteMinimalConfig(tmp_path)
    response = fixtureClient.post(
        "/api/registry",
        json={"sDirectory": sProjectDir},
    )
    assert response.status_code == 200
    dictResult = response.json()
    assert dictResult["sName"] == "test-project"


def testAddProjectMissingConfig(fixtureClient, tmp_path):
    sEmptyDir = str(tmp_path / "empty")
    os.makedirs(sEmptyDir)
    response = fixtureClient.post(
        "/api/registry",
        json={"sDirectory": sEmptyDir},
    )
    assert response.status_code == 404


def testAddProjectDuplicate(fixtureClient, tmp_path):
    sProjectDir = _fnWriteMinimalConfig(tmp_path)
    fixtureClient.post(
        "/api/registry",
        json={"sDirectory": sProjectDir},
    )
    response = fixtureClient.post(
        "/api/registry",
        json={"sDirectory": sProjectDir},
    )
    assert response.status_code == 409


# --- DELETE /api/registry/{sName} ---

def testRemoveProjectSuccess(fixtureClient, tmp_path):
    sProjectDir = _fnWriteMinimalConfig(tmp_path)
    fixtureClient.post(
        "/api/registry",
        json={"sDirectory": sProjectDir},
    )
    response = fixtureClient.delete("/api/registry/test-project")
    assert response.status_code == 200
    assert response.json()["bSuccess"] is True


def testRemoveProjectNotFound(fixtureClient):
    response = fixtureClient.delete("/api/registry/ghost")
    assert response.status_code == 404


# --- POST /api/containers/{sName}/build ---

def testBuildContainerProjectNotFound(fixtureClient):
    response = fixtureClient.post(
        "/api/containers/ghost/build",
    )
    assert response.status_code == 404


# --- POST /api/containers/{sName}/start ---

def testStartContainerProjectNotFound(fixtureClient):
    response = fixtureClient.post(
        "/api/containers/ghost/start",
    )
    assert response.status_code == 404


# --- POST /api/containers/{sName}/stop ---

def testStopContainerProjectNotFound(fixtureClient):
    response = fixtureClient.post(
        "/api/containers/ghost/stop",
    )
    assert response.status_code == 404


# --- Merge: registry + auto-discovery ---

def _fMockDockerWithContainers(listContainers, bVaibify=True):
    """Create a mock Docker connection with given containers."""
    from unittest.mock import MagicMock
    mockDocker = MagicMock()
    mockDocker.flistGetRunningContainers.return_value = listContainers
    iExitCode = 0 if bVaibify else 1
    mockDocker.ftResultExecuteCommand.return_value = (iExitCode, "")
    return mockDocker


def _fClientWithDocker(mockDocker):
    """Create a test client with mock Docker context."""
    from fastapi import FastAPI
    from starlette.testclient import TestClient
    from vaibify.gui.registryRoutes import fnRegisterRegistryRoutes
    app = FastAPI()
    dictCtx = {"require": lambda: None, "docker": mockDocker}
    fnRegisterRegistryRoutes(app, dictCtx)
    return TestClient(app)


def testGetRegistryMergesDiscoveredContainers(tmp_path, monkeypatch):
    """Discovered running vaibify containers appear in listContainers."""
    mockDocker = _fMockDockerWithContainers([
        {
            "sContainerId": "abc123", "sShortId": "abc1",
            "sName": "discovered-proj",
            "sImage": "discovered-proj:latest",
        },
    ], bVaibify=True)
    client = _fClientWithDocker(mockDocker)
    monkeypatch.setattr(
        "vaibify.config.registryManager.flistGetAllProjectsWithStatus",
        lambda: [],
    )
    response = client.get("/api/registry")
    assert response.status_code == 200
    dictResult = response.json()
    assert len(dictResult["listContainers"]) == 1
    assert dictResult["listContainers"][0]["sName"] == "discovered-proj"
    assert dictResult["listContainers"][0]["bDiscovered"] is True
    assert dictResult["listUnrecognized"] == []


def testGetRegistryShowsUnrecognizedContainers(tmp_path, monkeypatch):
    """Non-vaibify containers appear in listUnrecognized."""
    mockDocker = _fMockDockerWithContainers([
        {
            "sContainerId": "def456", "sShortId": "def4",
            "sName": "random-nginx",
            "sImage": "nginx:latest",
        },
    ], bVaibify=False)
    client = _fClientWithDocker(mockDocker)
    monkeypatch.setattr(
        "vaibify.config.registryManager.flistGetAllProjectsWithStatus",
        lambda: [],
    )
    response = client.get("/api/registry")
    dictResult = response.json()
    assert dictResult["listContainers"] == []
    assert len(dictResult["listUnrecognized"]) == 1
    assert dictResult["listUnrecognized"][0]["sName"] == "random-nginx"


def testGetRegistryEnrichesRegisteredWithContainerId(
    tmp_path, monkeypatch,
):
    """Registry entries get sContainerId when container is running."""
    mockDocker = _fMockDockerWithContainers([
        {
            "sContainerId": "xyz789", "sShortId": "xyz7",
            "sName": "my-proj", "sImage": "my-proj:latest",
        },
    ], bVaibify=True)
    client = _fClientWithDocker(mockDocker)
    listRegistered = [{
        "sName": "my-proj",
        "sDirectory": "/some/path",
        "sConfigPath": "/some/path/vaibify.yml",
        "sContainerName": "my-proj",
        "bImageExists": True,
        "bRunning": True,
        "sStatus": "running",
    }]
    monkeypatch.setattr(
        "vaibify.config.registryManager.flistGetAllProjectsWithStatus",
        lambda: listRegistered,
    )
    response = client.get("/api/registry")
    dictResult = response.json()
    assert len(dictResult["listContainers"]) == 1
    assert dictResult["listContainers"][0]["sContainerId"] == "xyz789"


# --- GET /api/host-directories ---

def testHostDirectoriesReturnsEntries(
    fixtureClient, tmp_path, monkeypatch,
):
    monkeypatch.setattr(os.path, "expanduser", lambda s: str(tmp_path))
    sSubDir = str(tmp_path / "child")
    os.makedirs(sSubDir)
    response = fixtureClient.get(
        "/api/host-directories",
        params={"sPath": str(tmp_path)},
    )
    assert response.status_code == 200
    dictResult = response.json()
    assert dictResult["sCurrentPath"] == str(tmp_path)
    listNames = [e["sName"] for e in dictResult["listEntries"]]
    assert "child" in listNames


def testHostDirectoriesOnlyReturnsDirs(
    fixtureClient, tmp_path, monkeypatch,
):
    monkeypatch.setattr(os.path, "expanduser", lambda s: str(tmp_path))
    os.makedirs(str(tmp_path / "subdir"))
    with open(str(tmp_path / "file.txt"), "w") as f:
        f.write("hi")
    response = fixtureClient.get(
        "/api/host-directories",
        params={"sPath": str(tmp_path)},
    )
    dictResult = response.json()
    listNames = [e["sName"] for e in dictResult["listEntries"]]
    assert "subdir" in listNames
    assert "file.txt" not in listNames


def testHostDirectoriesDetectsConfig(
    fixtureClient, tmp_path, monkeypatch,
):
    monkeypatch.setattr(os.path, "expanduser", lambda s: str(tmp_path))
    sProjectDir = str(tmp_path / "myproject")
    os.makedirs(sProjectDir)
    with open(os.path.join(sProjectDir, "vaibify.yml"), "w") as f:
        f.write("projectName: myproject\n")
    response = fixtureClient.get(
        "/api/host-directories",
        params={"sPath": str(tmp_path)},
    )
    dictResult = response.json()
    dictEntry = [
        e for e in dictResult["listEntries"]
        if e["sName"] == "myproject"
    ][0]
    assert dictEntry["bHasConfig"] is True


def testHostDirectoriesReportsCurrentDirConfig(
    fixtureClient, tmp_path, monkeypatch,
):
    monkeypatch.setattr(os.path, "expanduser", lambda s: str(tmp_path))
    with open(str(tmp_path / "vaibify.yml"), "w") as f:
        f.write("projectName: test\n")
    response = fixtureClient.get(
        "/api/host-directories",
        params={"sPath": str(tmp_path)},
    )
    assert response.json()["bHasConfig"] is True


def testHostDirectoriesRejectsOutsideHome(fixtureClient):
    response = fixtureClient.get(
        "/api/host-directories",
        params={"sPath": "/etc"},
    )
    assert response.status_code == 403


def testHostDirectoriesRejectsRelativePath(fixtureClient):
    response = fixtureClient.get(
        "/api/host-directories",
        params={"sPath": "relative/path"},
    )
    assert response.status_code == 400


def testHostDirectoriesSortsHiddenLast(
    fixtureClient, tmp_path, monkeypatch,
):
    monkeypatch.setattr(os.path, "expanduser", lambda s: str(tmp_path))
    os.makedirs(str(tmp_path / ".hidden"))
    os.makedirs(str(tmp_path / "aardvark"))
    os.makedirs(str(tmp_path / "zebra"))
    response = fixtureClient.get(
        "/api/host-directories",
        params={"sPath": str(tmp_path)},
    )
    listNames = [
        e["sName"] for e in response.json()["listEntries"]
    ]
    assert listNames == ["aardvark", "zebra", ".hidden"]
