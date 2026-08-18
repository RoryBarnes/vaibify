"""POST /api/registry/{sName}/promote-to-host-project, over real HTTP.

Every guarantee here crosses the HTTP + registry boundary, so it is
asserted through a ``TestClient`` with the host name (basename) kept
DISTINCT from the new Project name: a handler that read one where it
should read the other could not pass by coincidence. Because the route
declares ``separate-authority`` and writes no container, success is
verified against the registry entry and the rewritten vaibify.yml on
disk -- never a mutation double. The whole point of this feature is that
the promoted project STAYS host mode with no build, so every happy-path
assertion pins ``sMode == 'host'`` and the absence of a build hand-off.
"""

import os

import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient

from vaibify.config import operationJournal, registryManager
from vaibify.config.projectConfig import fconfigLoadFromFile


S_HOST_NAME = "greenhouse sandbox"
S_NEW_NAME = "AI Greenhouse"
S_OTHER_CONTAINER = "occupied"


@pytest.fixture(autouse=True)
def fixtureIsolateHostState(tmp_path, monkeypatch):
    """Redirect registry, locks, and the journal into tmp_path."""
    from vaibify.config import containerLock
    sRegistryDirectory = str(tmp_path / ".vaibify")
    monkeypatch.setattr(
        registryManager, "_S_REGISTRY_DIRECTORY", sRegistryDirectory,
    )
    monkeypatch.setattr(
        registryManager, "_S_REGISTRY_PATH",
        os.path.join(sRegistryDirectory, "registry.json"),
    )
    monkeypatch.setattr(
        containerLock, "_S_LOCK_DIRECTORY", str(tmp_path / "locks"),
    )
    monkeypatch.setattr(
        operationJournal, "_S_JOURNAL_DIRECTORY",
        str(tmp_path / "journal"),
    )
    # Keep the duplicate-name check off the developer's real daemon.
    monkeypatch.setattr(
        "vaibify.gui.registryRoutes._fbDockerContainerExists",
        lambda sName: False,
    )


def _fsRegisterProject(tmp_path, sProjectName, sMode):
    """Create a project directory + vaibify.yml and register it."""
    sProjectDirectory = str(tmp_path / sProjectName)
    os.makedirs(sProjectDirectory, exist_ok=True)
    with open(
        os.path.join(sProjectDirectory, "vaibify.yml"), "w",
    ) as fileConfig:
        fileConfig.write(f"projectName: {sProjectName}\n")
    registryManager.fnAddProject(sProjectDirectory, sMode=sMode)
    return sProjectDirectory


@pytest.fixture
def tclient(tmp_path):
    """A hub serving one host sandbox and one container project."""
    from vaibify.gui.registryRoutes import fnRegisterRegistryRoutes
    _fsRegisterProject(tmp_path, S_HOST_NAME, "host")
    _fsRegisterProject(tmp_path, S_OTHER_CONTAINER, "container")
    app = FastAPI()
    app.state.dictContainerOwners = {}
    app.state.iHubPort = 8050
    dictCtx = {"require": lambda *a: None, "docker": None}
    fnRegisterRegistryRoutes(app, dictCtx)
    return TestClient(app), app


def _fdictBody(sProjectName=S_NEW_NAME):
    return {"sProjectName": sProjectName}


def _sPromoteUrl(sName):
    return (
        "/api/registry/" + sName.replace(" ", "%20")
        + "/promote-to-host-project"
    )


def testHappyPathPromotesHostSandboxWithNoBuild(tclient):
    """The flag flips, the config is renamed, the mode stays host.

    Name != id throughout: the sandbox basename and the new Project name
    are different strings, both carrying spaces. No build is handed off.
    """
    client, _ = tclient
    response = client.post(_sPromoteUrl(S_HOST_NAME), json=_fdictBody())
    assert response.status_code == 200, response.text
    dictResult = response.json()
    assert dictResult["sName"] == S_NEW_NAME
    assert dictResult["sMode"] == "host"
    assert dictResult["sContainerName"] == S_NEW_NAME
    assert dictResult["bIsProject"] is True
    assert dictResult["bBuildRequired"] is False
    assert "sBuildPath" not in dictResult
    # The registry actually mutated: old name gone, new name is a host
    # Project, directory unchanged.
    assert registryManager.fdictGetProject(S_HOST_NAME) is None
    dictPromoted = registryManager.fdictGetProject(S_NEW_NAME)
    assert dictPromoted["sMode"] == "host"
    assert registryManager.fbIsHostProject(S_NEW_NAME)
    assert registryManager.fbIsProject(dictPromoted)
    # The vaibify.yml on disk carries the new projectName and still loads.
    configReloaded = fconfigLoadFromFile(dictPromoted["sConfigPath"])
    assert configReloaded.sProjectName == S_NEW_NAME


def testRepeatCallAfterSuccessIsRefusedNotReRegistered(tclient):
    """Idempotency: the second call sees a host Project and 409s."""
    client, _ = tclient
    assert client.post(
        _sPromoteUrl(S_HOST_NAME), json=_fdictBody(),
    ).status_code == 200
    response = client.post(
        _sPromoteUrl(S_NEW_NAME), json=_fdictBody("AI Greenhouse Two"),
    )
    assert response.status_code == 409
    assert "already a Project" in response.text


def testUnknownProjectIs404(tclient):
    client, _ = tclient
    response = client.post(
        _sPromoteUrl("no-such-project"), json=_fdictBody(),
    )
    assert response.status_code == 404


def testContainerProjectIs409(tclient):
    """A container project cannot be promoted -- it is already a Project."""
    client, _ = tclient
    response = client.post(
        _sPromoteUrl(S_OTHER_CONTAINER),
        json=_fdictBody("Occupied Project"),
    )
    assert response.status_code == 409
    assert "already a containerized project" in response.text


def testClaimedProjectIs409AndDoesNotMutate(tclient):
    """An owned project is refused, and nothing on disk changes.

    The owner map is keyed by the registry NAME (the host basename), the
    same key the lock/lease use, so the guard reads the same key the
    promotion would rename.
    """
    client, app = tclient
    app.state.dictContainerOwners[S_HOST_NAME] = object()
    response = client.post(_sPromoteUrl(S_HOST_NAME), json=_fdictBody())
    assert response.status_code == 409
    assert "open in a browser session" in response.text
    # Still a host sandbox, still under its original name.
    dictHost = registryManager.fdictGetProject(S_HOST_NAME)
    assert registryManager.fbIsHostProject(S_HOST_NAME)
    assert not registryManager.fbIsProject(dictHost)
    assert registryManager.fdictGetProject(S_NEW_NAME) is None


def testDuplicateNewNameIs409(tclient):
    """The new name must be free of every other registered project."""
    client, _ = tclient
    response = client.post(
        _sPromoteUrl(S_HOST_NAME), json=_fdictBody(S_OTHER_CONTAINER),
    )
    assert response.status_code == 409
    assert not registryManager.fbIsProject(
        registryManager.fdictGetProject(S_HOST_NAME))


def testNonStorageSafeNameIs400AndDoesNotMutate(tclient):
    """A name with a path separator is refused before any write."""
    client, _ = tclient
    response = client.post(
        _sPromoteUrl(S_HOST_NAME),
        json=_fdictBody("bad/name"),
    )
    assert response.status_code == 400
    # The config was NOT rewritten: the host projectName still stands and
    # the project is still an un-promoted sandbox.
    dictHost = registryManager.fdictGetProject(S_HOST_NAME)
    configHost = fconfigLoadFromFile(dictHost["sConfigPath"])
    assert configHost.sProjectName == S_HOST_NAME
    assert not registryManager.fbIsProject(dictHost)


def testKeepingTheSameNameStillGraduatesToProject(tmp_path):
    """A sandbox may keep its basename and still become a Project.

    The route's own duplicate check must skip the project being
    promoted, or the entry would collide with itself and 409. A
    Docker-unsafe basename with a space is fine here -- host mode never
    turns the name into a Docker object.
    """
    from vaibify.gui.registryRoutes import fnRegisterRegistryRoutes
    _fsRegisterProject(tmp_path, S_HOST_NAME, "host")
    app = FastAPI()
    app.state.dictContainerOwners = {}
    app.state.iHubPort = 8050
    fnRegisterRegistryRoutes(
        app, {"require": lambda *a: None, "docker": None},
    )
    client = TestClient(app)
    response = client.post(
        _sPromoteUrl(S_HOST_NAME), json=_fdictBody(S_HOST_NAME),
    )
    assert response.status_code == 200, response.text
    dictPromoted = registryManager.fdictGetProject(S_HOST_NAME)
    assert dictPromoted["sMode"] == "host"
    assert dictPromoted["bIsProject"] is True


def testPromotedHostProjectCanStillBeContainerizedLater(tclient):
    """Promotion and containerization are independent axes.

    A host Project is still host mode, so the convert route's non-host
    guard (which reads sMode, not bIsProject) still admits it.
    """
    client, _ = tclient
    assert client.post(
        _sPromoteUrl(S_HOST_NAME), json=_fdictBody(),
    ).status_code == 200
    response = client.post(
        "/api/registry/" + S_NEW_NAME.replace(" ", "%20")
        + "/convert-to-container",
        json={"sProjectName": "aiGreenhouseBox"},
    )
    assert response.status_code == 200, response.text
    dictConverted = registryManager.fdictGetProject("aiGreenhouseBox")
    assert dictConverted["sMode"] == "container"
