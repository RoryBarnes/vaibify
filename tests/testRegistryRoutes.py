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


# -----------------------------------------------------------------------
# Build/Start/Stop success paths (lines 107-180)
# -----------------------------------------------------------------------


def testBuildContainerSuccess(fixtureClient, tmp_path, monkeypatch):
    """Lines 107-112: successful build returns 200."""
    sProjectDir = _fnWriteMinimalConfig(tmp_path, "build-proj")
    fixtureClient.post(
        "/api/registry",
        json={"sDirectory": sProjectDir},
    )
    monkeypatch.setattr(
        "vaibify.gui.registryRoutes._fnExecuteBuild",
        lambda dictProject, bNoCache=False: None,
    )
    response = fixtureClient.post(
        "/api/containers/build-proj/build",
    )
    assert response.status_code == 200
    assert response.json()["bSuccess"] is True


def testBuildContainerFailure(fixtureClient, tmp_path, monkeypatch):
    """Lines 109-111: build failure returns 500."""
    sProjectDir = _fnWriteMinimalConfig(tmp_path, "fail-build")
    fixtureClient.post(
        "/api/registry",
        json={"sDirectory": sProjectDir},
    )
    monkeypatch.setattr(
        "vaibify.gui.registryRoutes._fnExecuteBuild",
        lambda dictProject, bNoCache=False: (_ for _ in ()).throw(
            RuntimeError("build error")
        ),
    )
    response = fixtureClient.post(
        "/api/containers/fail-build/build",
    )
    assert response.status_code == 500


def testStartContainerSuccess(fixtureClient, tmp_path, monkeypatch):
    """Lines 135-143: successful start returns container ID."""
    sProjectDir = _fnWriteMinimalConfig(tmp_path, "start-proj")
    fixtureClient.post(
        "/api/registry",
        json={"sDirectory": sProjectDir},
    )
    monkeypatch.setattr(
        "vaibify.gui.registryRoutes._fsExecuteStart",
        lambda dictProject: "abc123",
    )
    response = fixtureClient.post(
        "/api/containers/start-proj/start",
    )
    assert response.status_code == 200
    assert response.json()["sContainerId"] == "abc123"


def testStartContainerFailure(fixtureClient, tmp_path, monkeypatch):
    """Lines 137-139: start failure returns 500."""
    sProjectDir = _fnWriteMinimalConfig(tmp_path, "fail-start")
    fixtureClient.post(
        "/api/registry",
        json={"sDirectory": sProjectDir},
    )
    monkeypatch.setattr(
        "vaibify.gui.registryRoutes._fsExecuteStart",
        lambda dictProject: (_ for _ in ()).throw(
            RuntimeError("start error")
        ),
    )
    response = fixtureClient.post(
        "/api/containers/fail-start/start",
    )
    assert response.status_code == 500


def testStopContainerSuccess(fixtureClient, tmp_path, monkeypatch):
    """Lines 168-174: successful stop returns 200."""
    sProjectDir = _fnWriteMinimalConfig(tmp_path, "stop-proj")
    fixtureClient.post(
        "/api/registry",
        json={"sDirectory": sProjectDir},
    )
    monkeypatch.setattr(
        "vaibify.gui.registryRoutes._fnExecuteStop",
        lambda sContainerName: None,
    )
    response = fixtureClient.post(
        "/api/containers/stop-proj/stop",
    )
    assert response.status_code == 200
    assert response.json()["bSuccess"] is True


def testStopContainerFailure(fixtureClient, tmp_path, monkeypatch):
    """Lines 171-173: stop failure returns 500."""
    sProjectDir = _fnWriteMinimalConfig(tmp_path, "fail-stop")
    fixtureClient.post(
        "/api/registry",
        json={"sDirectory": sProjectDir},
    )
    monkeypatch.setattr(
        "vaibify.gui.registryRoutes._fnExecuteStop",
        lambda sContainerName: (_ for _ in ()).throw(
            RuntimeError("stop error")
        ),
    )
    response = fixtureClient.post(
        "/api/containers/fail-stop/stop",
    )
    assert response.status_code == 500


# -----------------------------------------------------------------------
# Docker discovery exception (lines 208-209)
# -----------------------------------------------------------------------


def testDiscoverContainersDockerException(tmp_path, monkeypatch):
    """Lines 208-209: Docker exception returns empty lists."""
    from unittest.mock import MagicMock
    mockDocker = MagicMock()
    mockDocker.flistGetRunningContainers.side_effect = RuntimeError("boom")
    client = _fClientWithDocker(mockDocker)
    monkeypatch.setattr(
        "vaibify.config.registryManager.flistGetAllProjectsWithStatus",
        lambda: [],
    )
    response = client.get("/api/registry")
    assert response.status_code == 200
    assert response.json()["listContainers"] == []


# -----------------------------------------------------------------------
# _fbIsVaibifyContainer exception (lines 237-238)
# -----------------------------------------------------------------------


def testIsVaibifyContainerExceptionReturnsFalse(monkeypatch):
    """Lines 237-238: exception in exec returns False."""
    from unittest.mock import MagicMock
    from vaibify.gui.registryRoutes import _fbIsVaibifyContainer
    mockDocker = MagicMock()
    mockDocker.ftResultExecuteCommand.side_effect = RuntimeError("err")
    bResult = _fbIsVaibifyContainer(
        mockDocker, {"sContainerId": "x"},
    )
    assert bResult is False


# -----------------------------------------------------------------------
# host-directories: nonexistent path (line 317)
# -----------------------------------------------------------------------


def testHostDirectoriesNonexistentPath(fixtureClient, tmp_path, monkeypatch):
    """Line 317: nonexistent directory returns 404."""
    monkeypatch.setattr(os.path, "expanduser", lambda s: str(tmp_path))
    sNonexistent = str(tmp_path / "does_not_exist")
    response = fixtureClient.get(
        "/api/host-directories",
        params={"sPath": sNonexistent},
    )
    assert response.status_code == 404


# -----------------------------------------------------------------------
# host-directories: permission error (lines 342-343)
# -----------------------------------------------------------------------


def testHostDirectoriesPermissionError(
    fixtureClient, tmp_path, monkeypatch,
):
    """Lines 342-343: PermissionError raises 403."""
    monkeypatch.setattr(os.path, "expanduser", lambda s: str(tmp_path))
    monkeypatch.setattr(
        os, "scandir",
        lambda sPath: (_ for _ in ()).throw(
            PermissionError("denied")
        ),
    )
    response = fixtureClient.get(
        "/api/host-directories",
        params={"sPath": str(tmp_path)},
    )
    assert response.status_code == 403


# -----------------------------------------------------------------------
# POST /api/host-directories/create
# -----------------------------------------------------------------------


def testCreateHostDirectoryHappyPath(
    fixtureClient, tmp_path, monkeypatch,
):
    monkeypatch.setattr(os.path, "expanduser", lambda s: str(tmp_path))
    response = fixtureClient.post(
        "/api/host-directories/create",
        json={"sParentPath": str(tmp_path),
              "sFolderName": "vaibify-vplanet"},
    )
    assert response.status_code == 200
    sNewPath = response.json()["sNewPath"]
    assert sNewPath == os.path.join(str(tmp_path), "vaibify-vplanet")
    assert os.path.isdir(sNewPath)


def testCreateHostDirectoryRejectsPathTraversal(
    fixtureClient, tmp_path, monkeypatch,
):
    monkeypatch.setattr(os.path, "expanduser", lambda s: str(tmp_path))
    response = fixtureClient.post(
        "/api/host-directories/create",
        json={"sParentPath": str(tmp_path),
              "sFolderName": "../escape"},
    )
    assert response.status_code == 400


def testCreateHostDirectoryRejectsExistingFolder(
    fixtureClient, tmp_path, monkeypatch,
):
    monkeypatch.setattr(os.path, "expanduser", lambda s: str(tmp_path))
    os.makedirs(str(tmp_path / "already-here"))
    response = fixtureClient.post(
        "/api/host-directories/create",
        json={"sParentPath": str(tmp_path),
              "sFolderName": "already-here"},
    )
    assert response.status_code == 409


def testCreateHostDirectoryRejectsOutsideHome(
    fixtureClient, tmp_path, monkeypatch,
):
    monkeypatch.setattr(os.path, "expanduser", lambda s: str(tmp_path))
    response = fixtureClient.post(
        "/api/host-directories/create",
        json={"sParentPath": "/etc",
              "sFolderName": "newdir"},
    )
    assert response.status_code == 403


def testCreateHostDirectoryRejectsShellMetacharacters(
    fixtureClient, tmp_path, monkeypatch,
):
    monkeypatch.setattr(os.path, "expanduser", lambda s: str(tmp_path))
    response = fixtureClient.post(
        "/api/host-directories/create",
        json={"sParentPath": str(tmp_path),
              "sFolderName": "foo;rm"},
    )
    assert response.status_code == 400


def testCreateHostDirectoryRejectsEmptyName(
    fixtureClient, tmp_path, monkeypatch,
):
    monkeypatch.setattr(os.path, "expanduser", lambda s: str(tmp_path))
    response = fixtureClient.post(
        "/api/host-directories/create",
        json={"sParentPath": str(tmp_path),
              "sFolderName": "   "},
    )
    assert response.status_code == 400


def testCreateHostDirectoryRejectsLeadingDot(
    fixtureClient, tmp_path, monkeypatch,
):
    monkeypatch.setattr(os.path, "expanduser", lambda s: str(tmp_path))
    response = fixtureClient.post(
        "/api/host-directories/create",
        json={"sParentPath": str(tmp_path),
              "sFolderName": ".hidden"},
    )
    assert response.status_code == 400


def testCreateHostDirectoryAcceptsSpacesAndDashes(
    fixtureClient, tmp_path, monkeypatch,
):
    monkeypatch.setattr(os.path, "expanduser", lambda s: str(tmp_path))
    response = fixtureClient.post(
        "/api/host-directories/create",
        json={"sParentPath": str(tmp_path),
              "sFolderName": "my new-folder"},
    )
    assert response.status_code == 200
    assert os.path.isdir(
        os.path.join(str(tmp_path), "my new-folder"))
