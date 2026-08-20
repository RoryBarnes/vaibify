"""POST /api/registry/{sName}/convert-to-container, over real HTTP.

Every guarantee here crosses the HTTP + registry boundary, so it is
asserted through a ``TestClient`` with the host name (basename) kept
DISTINCT from the new container name: a handler that read one where it
should read the other could not pass by coincidence. Because the route
declares ``separate-authority`` and writes no container, success is
verified against the registry entry and the rewritten vaibify.yml on
disk -- never a mutation double.
"""

import os

import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient

from vaibify.config import operationJournal, registryManager
from vaibify.config.projectConfig import fconfigLoadFromFile


S_HOST_NAME = "greenhouse sandbox"
S_NEW_NAME = "greenhouseBox"
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
    """A hub serving one host project and one container project."""
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
    return {"sProjectName": sProjectName, "sPythonVersion": "3.11"}


def _sConvertUrl(sName):
    return (
        "/api/registry/" + sName.replace(" ", "%20")
        + "/convert-to-container"
    )


def testHappyPathConvertsHostToContainerAndHandsOffTheBuild(tclient):
    """The registry flips, the config is rewritten, the build is handed off.

    Name != id throughout: the host basename carries a space and is not
    Docker-safe; the new container name is a different, Docker-safe
    string.
    """
    client, _ = tclient
    response = client.post(_sConvertUrl(S_HOST_NAME), json=_fdictBody())
    assert response.status_code == 200, response.text
    dictResult = response.json()
    assert dictResult["sName"] == S_NEW_NAME
    assert dictResult["sMode"] == "container"
    assert dictResult["sContainerName"] == S_NEW_NAME
    assert dictResult["bBuildRequired"] is True
    assert dictResult["sBuildPath"] == (
        f"/api/containers/{S_NEW_NAME}/build"
    )
    # The registry actually mutated: old name gone, new name is a
    # container, directory unchanged.
    assert registryManager.fdictGetProject(S_HOST_NAME) is None
    dictConverted = registryManager.fdictGetProject(S_NEW_NAME)
    assert dictConverted["sMode"] == "container"
    assert not registryManager.fbIsHostProject(S_NEW_NAME)
    # The vaibify.yml on disk carries the new Docker-safe projectName and
    # the requested container field.
    configReloaded = fconfigLoadFromFile(dictConverted["sConfigPath"])
    assert configReloaded.sProjectName == S_NEW_NAME
    assert configReloaded.sPythonVersion == "3.11"


def testRepeatCallAfterSuccessIsRefusedNotReRegistered(tclient):
    """Idempotency: the second call sees a container project and 409s."""
    client, _ = tclient
    assert client.post(
        _sConvertUrl(S_HOST_NAME), json=_fdictBody(),
    ).status_code == 200
    # The project is now a container under the NEW name; converting it
    # again is refused as host-only.
    response = client.post(
        _sConvertUrl(S_NEW_NAME), json=_fdictBody("greenhouseBoxTwo"),
    )
    assert response.status_code == 409
    assert "already a containerized project" in response.text


def testUnknownProjectIs404(tclient):
    client, _ = tclient
    response = client.post(
        _sConvertUrl("no-such-project"), json=_fdictBody(),
    )
    assert response.status_code == 404


def testAlreadyContainerProjectIs409(tclient):
    client, _ = tclient
    response = client.post(
        _sConvertUrl(S_OTHER_CONTAINER),
        json=_fdictBody("occupiedBox"),
    )
    assert response.status_code == 409
    assert "already a containerized project" in response.text


def testClaimedProjectIs409AndDoesNotMutate(tclient):
    """An owned project is refused, and nothing on disk changes.

    The owner map is keyed by the registry NAME (the host basename), the
    same key the lock/lease use, so the guard reads the same key the
    conversion would rename.
    """
    client, app = tclient
    app.state.dictContainerOwners[S_HOST_NAME] = object()
    response = client.post(_sConvertUrl(S_HOST_NAME), json=_fdictBody())
    assert response.status_code == 409
    assert "open in a browser session" in response.text
    # Still a host project, still under its original name.
    assert registryManager.fbIsHostProject(S_HOST_NAME)
    assert registryManager.fdictGetProject(S_NEW_NAME) is None


@pytest.mark.falsification
def testTheOwningBrowserSessionConvertsItsOwnOpenProject(tclient):
    """The owning tab converts its own open project in one request.

    The self-release helper is shared with the promote route, but the
    convert handler must actually CALL it -- this drives the convert
    leg with a real session-bound owner record holding a real flock,
    and asserts the release committed (owner map empty, flock freed)
    and the conversion proceeded.

    Kills: a convert handler that reinstates the unconditional
    owned-project refusal, or skips the self-release call.
    """
    from tests.testPromoteToHostProjectRoute import (
        _fbFlockIsStillHeld,
        _fdictOwnerHeaders,
        _fsInstallOwningBrowserSession,
    )
    client, app = tclient
    sCredential = _fsInstallOwningBrowserSession(app, S_HOST_NAME)
    response = client.post(
        _sConvertUrl(S_HOST_NAME), json=_fdictBody(),
        headers=_fdictOwnerHeaders(sCredential),
    )
    assert response.status_code == 200, response.text
    assert registryManager.fdictGetProject(S_NEW_NAME) is not None
    assert registryManager.fdictGetProject(S_HOST_NAME) is None
    assert app.state.dictContainerOwners == {}
    assert not _fbFlockIsStillHeld(S_HOST_NAME)


def testDuplicateNewNameIs409(tclient):
    """The new name must be free of every other registered project."""
    client, _ = tclient
    response = client.post(
        _sConvertUrl(S_HOST_NAME), json=_fdictBody(S_OTHER_CONTAINER),
    )
    assert response.status_code == 409
    assert registryManager.fbIsHostProject(S_HOST_NAME)


def testKeepingAnAlreadyDockerSafeNameIsPermitted(tmp_path):
    """A host sandbox whose basename is Docker-safe may keep its name.

    The route's own duplicate check must skip the project being
    converted, or the entry would collide with itself and 409.
    """
    from vaibify.gui.registryRoutes import fnRegisterRegistryRoutes
    _fsRegisterProject(tmp_path, "greenhouse", "host")
    app = FastAPI()
    app.state.dictContainerOwners = {}
    app.state.iHubPort = 8050
    fnRegisterRegistryRoutes(
        app, {"require": lambda *a: None, "docker": None},
    )
    client = TestClient(app)
    response = client.post(
        "/api/registry/greenhouse/convert-to-container",
        json={"sProjectName": "greenhouse"},
    )
    assert response.status_code == 200, response.text
    dictConverted = registryManager.fdictGetProject("greenhouse")
    assert dictConverted["sMode"] == "container"


def testNonDockerSafeNewNameIs400AndDoesNotMutate(tclient):
    """A container name with a space is refused before any write."""
    client, _ = tclient
    response = client.post(
        _sConvertUrl(S_HOST_NAME),
        json=_fdictBody("green house"),
    )
    assert response.status_code == 400
    # The config was NOT rewritten: the host projectName still stands.
    dictHost = registryManager.fdictGetProject(S_HOST_NAME)
    configHost = fconfigLoadFromFile(dictHost["sConfigPath"])
    assert configHost.sProjectName == S_HOST_NAME
    assert registryManager.fbIsHostProject(S_HOST_NAME)
