"""Tests for vaibify.gui.registryRoutes."""

import json
import os

import pytest

from vaibify.config import registryManager


@pytest.fixture(autouse=True)
def fixtureIsolateContainerLocks(tmp_path, monkeypatch):
    """Keep the start path's host flock inside tmp_path.

    The start route acquires the container flock (design §10b), so
    without this a test run would write into — and could contend with —
    the researcher's real ~/.vaibify/locks.
    """
    from vaibify.config import containerLock
    monkeypatch.setattr(
        containerLock, "_S_LOCK_DIRECTORY", str(tmp_path / "locks"),
    )


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
    app.state.dictContainerOwners = {}
    app.state.iHubPort = 8050
    dictCtx = {"require": lambda *aArgs: None, "docker": None}
    fnRegisterRegistryRoutes(app, dictCtx)
    return app


@pytest.fixture
def fixtureClient(fixtureApp):
    """Create a test client for the hub app."""
    from starlette.testclient import TestClient
    return TestClient(fixtureApp)


@pytest.fixture
def fixtureLiveClient(fixtureApp):
    """A context-managed client whose event loop OUTLIVES each request.

    ``TestClient`` used outside a ``with`` block spins a fresh event loop
    per request, so a background task started by one request is dropped
    before the next. The start reservation launches a durable task, so
    every test that watches one settle needs the long-lived loop a real
    uvicorn hub always has.
    """
    from starlette.testclient import TestClient
    with TestClient(fixtureApp) as clientLive:
        yield clientLive


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
    app.state.dictContainerOwners = {}
    app.state.iHubPort = 8050
    dictCtx = {"require": lambda *aArgs: None, "docker": mockDocker}
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
        "vaibify.gui.buildRoutes._fnExecuteBuild",
        lambda dictProject, bNoCache=False, dictProgress=None: None,
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
        "vaibify.gui.buildRoutes._fnExecuteBuild",
        lambda dictProject, bNoCache=False, dictProgress=None: (_ for _ in ()).throw(
            RuntimeError("build error")
        ),
    )
    response = fixtureClient.post(
        "/api/containers/fail-build/build",
    )
    assert response.status_code == 500


def testBuildFailureSurfacesStderrTail(
    fixtureClient, tmp_path, monkeypatch,
):
    """Without the stderr tail in the response, the next disk-full
    build looks identical to a network failure: the user is left
    guessing. The route must surface ``sStderrTail`` from the raised
    exception so the GUI can show the actual buildx output."""
    sProjectDir = _fnWriteMinimalConfig(tmp_path, "tail-build")
    fixtureClient.post(
        "/api/registry",
        json={"sDirectory": sProjectDir},
    )

    def _fnRaiseWithTail(dictProject, bNoCache=False, dictProgress=None):
        errorBuild = RuntimeError("Docker command failed (exit 1)")
        errorBuild.sStderrTail = (
            "E: You don't have enough free space in "
            "/var/cache/apt/archives/.\n"
        )
        raise errorBuild

    monkeypatch.setattr(
        "vaibify.gui.buildRoutes._fnExecuteBuild",
        _fnRaiseWithTail,
    )
    response = fixtureClient.post(
        "/api/containers/tail-build/build",
    )
    assert response.status_code == 500
    dictDetail = response.json()["detail"]
    assert dictDetail["sMessage"] == "Build failed"
    assert "Docker command failed" in dictDetail["sError"]
    assert "enough free space" in dictDetail["sStderrTail"]


def testBuildFailureWithoutTailStillStructured(
    fixtureClient, tmp_path, monkeypatch,
):
    """An exception without ``sStderrTail`` (legacy/path errors) must
    still produce a structured detail so the GUI's dict-detail
    handler does not break."""
    sProjectDir = _fnWriteMinimalConfig(tmp_path, "notail-build")
    fixtureClient.post(
        "/api/registry",
        json={"sDirectory": sProjectDir},
    )
    monkeypatch.setattr(
        "vaibify.gui.buildRoutes._fnExecuteBuild",
        lambda dictProject, bNoCache=False, dictProgress=None: (_ for _ in ()).throw(
            RuntimeError("config not found")
        ),
    )
    response = fixtureClient.post(
        "/api/containers/notail-build/build",
    )
    assert response.status_code == 500
    dictDetail = response.json()["detail"]
    assert dictDetail["sMessage"] == "Build failed"
    assert dictDetail["sStderrTail"] == ""


def testBuildRunsOffEventLoopThread(
    fixtureClient, tmp_path, monkeypatch,
):
    """The build executor must run on a worker thread so the event
    loop stays responsive. Without ``asyncio.to_thread`` the entire
    backend hangs for the duration of a real ``docker build``,
    leading to ``Network error`` toasts in the GUI.
    """
    import threading
    sProjectDir = _fnWriteMinimalConfig(tmp_path, "thread-build")
    fixtureClient.post(
        "/api/registry",
        json={"sDirectory": sProjectDir},
    )
    listExecutorThreads = []

    def _fnRecordThread(dictProject, bNoCache=False, dictProgress=None):
        listExecutorThreads.append(threading.get_ident())

    monkeypatch.setattr(
        "vaibify.gui.buildRoutes._fnExecuteBuild",
        _fnRecordThread,
    )
    response = fixtureClient.post(
        "/api/containers/thread-build/build",
    )
    assert response.status_code == 200
    assert listExecutorThreads, "executor was not invoked"
    assert listExecutorThreads[0] != threading.get_ident(), (
        "build executor ran on the test thread, meaning the route "
        "is not using asyncio.to_thread; the backend will hang for "
        "the duration of a real docker build"
    )


def testStartRunsOffEventLoopThread(
    fixtureLiveClient, fixtureApp, tmp_path, monkeypatch,
):
    """The reservation's Docker work runs off the event loop thread.

    The start is now a server-owned reservation (design §10b): the route
    answers 202 immediately and the create-then-start pair runs as a
    durable task on a worker thread, so a hung pull can never block the
    hub's loop.
    """
    import threading
    _fnRegisterProject(fixtureLiveClient, tmp_path, "thread-start")
    listExecutorThreads = []

    def _fnRecordThread(sName, reservation, configProject):
        listExecutorThreads.append(threading.get_ident())
        return "abc123"

    monkeypatch.setattr(
        "vaibify.gui.startReservation._fsExecuteReservedStart",
        _fnRecordThread,
    )
    response = fixtureLiveClient.post(
        "/api/containers/thread-start/start",
    )
    assert response.status_code == 202, response.text
    _fnAwaitStartSettled(
        fixtureLiveClient, fixtureApp, response.json()["sReservationId"],
    )
    assert listExecutorThreads[0] != threading.get_ident()


def _fnRegisterProject(fixtureLiveClient, tmp_path, sProjectName):
    """Register a minimal project and return its directory."""
    sProjectDir = _fnWriteMinimalConfig(tmp_path, sProjectName)
    fixtureLiveClient.post("/api/registry", json={"sDirectory": sProjectDir})
    return sProjectDir


def _frecordAwaitStartSettled(
    fixtureLiveClient, fixtureApp, sReservationId, iAttempts=200,
):
    """Wait for the durable start task to publish its outcome.

    Each iteration issues a cheap request, because ``TestClient`` only
    turns the event loop while one is in flight — under uvicorn the
    durable task advances on its own, and a real dashboard polls the
    start-status endpoint exactly like this.
    """
    for _ in range(iAttempts):
        recordResult = fixtureApp.state.dictStartResults.get(sReservationId)
        if recordResult is not None and recordResult.sState != "PENDING":
            return recordResult
        fixtureLiveClient.get("/api/registry")
    raise AssertionError("the start never settled its result record")


def _fnAwaitStartSettled(fixtureLiveClient, fixtureApp, sReservationId):
    """Wait for settlement, discarding the record."""
    _frecordAwaitStartSettled(fixtureLiveClient, fixtureApp, sReservationId)


def testStopRunsOffEventLoopThread(
    fixtureClient, tmp_path, monkeypatch,
):
    """Same async-safety guarantee for the stop endpoint."""
    import threading
    sProjectDir = _fnWriteMinimalConfig(tmp_path, "thread-stop")
    fixtureClient.post(
        "/api/registry",
        json={"sDirectory": sProjectDir},
    )
    listExecutorThreads = []

    def _fnRecordThread(sContainerName):
        listExecutorThreads.append(threading.get_ident())

    monkeypatch.setattr(
        "vaibify.gui.registryRoutes._fnExecuteStop",
        _fnRecordThread,
    )
    response = fixtureClient.post(
        "/api/containers/thread-stop/stop",
    )
    assert response.status_code == 200
    assert listExecutorThreads[0] != threading.get_ident()


def testStartContainerSuccess(
    fixtureLiveClient, fixtureApp, tmp_path, monkeypatch,
):
    """A started container settles SUCCEEDED and keeps its ownership.

    The 202 carries the reservation id and the status-poll location and
    NEVER a lease: nothing is running yet for a lease to authorize.
    """
    _fnRegisterProject(fixtureLiveClient, tmp_path, "start-proj")
    monkeypatch.setattr(
        "vaibify.gui.startReservation._fsExecuteReservedStart",
        lambda sName, reservation, configProject: "abc123",
    )
    response = fixtureLiveClient.post("/api/containers/start-proj/start")
    assert response.status_code == 202, response.text
    dictBody = response.json()
    assert dictBody["sStatusPath"] == (
        "/api/containers/start-proj/start-status"
    )
    assert "sLeaseId" not in dictBody
    recordResult = _frecordAwaitStartSettled(
        fixtureLiveClient, fixtureApp, dictBody["sReservationId"],
    )
    assert recordResult.sState == "SUCCEEDED"
    recordOwner = fixtureApp.state.dictContainerOwners["start-proj"]
    assert recordOwner.reservation is None, (
        "a settled start must clear its reservation"
    )
    assert recordOwner.sContainerId == "abc123"


def testStartContainerFailureReleasesOwnership(
    fixtureLiveClient, fixtureApp, tmp_path, monkeypatch,
):
    """A failed start publishes FAILED and frees the container again.

    The failure reaches the researcher through the result record, not
    through the POST: by the time it is known the request is long gone,
    which is exactly why the record outlives the reservation.
    """
    _fnRegisterProject(fixtureLiveClient, tmp_path, "fail-start")

    def _fnRaise(sName, reservation, configProject):
        raise RuntimeError("start error")

    monkeypatch.setattr(
        "vaibify.gui.startReservation._fsExecuteReservedStart", _fnRaise,
    )
    monkeypatch.setattr(
        "vaibify.docker.containerManager.fdictSettleReservationContainers",
        lambda sReservationId, bLaunchWasKilled: {
            "bConclusive": True, "listRemovedContainerIds": [],
            "sDetail": "nothing was created",
        },
    )
    response = fixtureLiveClient.post("/api/containers/fail-start/start")
    assert response.status_code == 202, response.text
    recordResult = _frecordAwaitStartSettled(
        fixtureLiveClient, fixtureApp, response.json()["sReservationId"],
    )
    assert recordResult.sState == "FAILED"
    assert "start error" in recordResult.sSafeError
    assert recordResult.bQuarantined is False
    assert "fail-start" not in fixtureApp.state.dictContainerOwners, (
        "a conclusively-clean failed start must free the container"
    )


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
