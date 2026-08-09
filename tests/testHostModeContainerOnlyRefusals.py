"""Container-only lifecycle capabilities, refused for host projects.

Build, start, cancel-a-start and stop all drive Docker machinery at a
container that a host project does not have. The dashboard hides them
from a host tile, but hiding is courtesy: the routes are reachable by
``curl``, by the CLI, and by any panel that forgets, so the refusal
lives on the server.

WHY THE STATUS CODE IS ITS OWN CONCERN
--------------------------------------

The withdrawn terminal taught this repository that a refusal a client
cannot classify is worse than none: a caller that reads "unavailable"
as "your credential was rejected" tells the researcher to re-claim a
project that is already theirs, and they re-claim it forever. So these
tests assert the refusal is NOT the authorization code, carries the
machine-readable ``sUnavailableIn`` marker, and names the capability
in words a researcher can act on.

BOTH DIRECTIONS, because they fail identically in a report and
oppositely in the product: a refusal that never fires drives Docker at
a project with no container, and one that always fires makes every
containerized project unstartable.
"""

import os

import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient

from vaibify.config import registryManager
from vaibify.gui import routeContext


S_HOST_PROJECT = "host-only-project"
S_CONTAINER_PROJECT = "containerized-project"


@pytest.fixture(autouse=True)
def fixtureIsolateContainerLocks(tmp_path, monkeypatch):
    """Keep the start path's host flock inside tmp_path."""
    from vaibify.config import containerLock
    monkeypatch.setattr(
        containerLock, "_S_LOCK_DIRECTORY", str(tmp_path / "locks"),
    )


@pytest.fixture(autouse=True)
def fixtureIsolateRegistry(tmp_path, monkeypatch):
    """Redirect the registry to a temp directory for every test."""
    sRegistryDirectory = str(tmp_path / ".vaibify")
    monkeypatch.setattr(
        registryManager, "_S_REGISTRY_DIRECTORY", sRegistryDirectory,
    )
    monkeypatch.setattr(
        registryManager, "_S_REGISTRY_PATH",
        os.path.join(sRegistryDirectory, "registry.json"),
    )


@pytest.fixture(autouse=True)
def fixtureNoRealContainerWork(monkeypatch):
    """Stub the three executors these routes hand their work to.

    Not tidiness — necessity, and measured. Without the build stub a
    test that reaches the build route runs a REAL ``docker build``
    against the developer's daemon: the first run took 47 seconds and
    got as far as layer 37 before failing.

    AUTOUSE, and that is the load-bearing part. The obvious scoping is
    "only the container-direction test needs it", and it is wrong: the
    MUTANT that direction exists to catch is a refusal that never
    fires, under which the host-direction test walks straight into the
    same real build. A falsification harness must be able to apply
    every registered mutation without launching container work.
    """
    monkeypatch.setattr(
        "vaibify.gui.buildRoutes._fnExecuteBuild",
        lambda dictProject, bNoCache, dictProgress: None,
    )
    monkeypatch.setattr(
        "vaibify.gui.registryRoutes._fnExecuteStop",
        lambda sContainerName: None,
    )
    monkeypatch.setattr(
        "vaibify.gui.startReservation._fsExecuteReservedStart",
        lambda sName, reservation, configProject: "abc123",
    )


def _fsRegisterProject(tmp_path, sProjectName, sMode):
    """Create a project directory and register it in the given mode.

    Registered through ``fnAddProject`` rather than through
    ``POST /api/registry``, because the HTTP add route does not carry
    ``sMode`` yet — threading it is the frontend chunk's work, and a
    test that waited for it would leave this refusal unexercised in
    the meantime.
    """
    sProjectDirectory = str(tmp_path / sProjectName)
    os.makedirs(sProjectDirectory, exist_ok=True)
    with open(
        os.path.join(sProjectDirectory, "vaibify.yml"), "w",
    ) as fileConfig:
        fileConfig.write(f"projectName: {sProjectName}\n")
    registryManager.fnAddProject(sProjectDirectory, sMode=sMode)
    return sProjectDirectory


@pytest.fixture
def tclientBothModes(tmp_path):
    """Return ``(client, fnSetDockerRequirement)`` over both projects.

    One hub serving one host project and one container project, so
    every assertion below is about the MODE rather than about two
    differently-built fixtures.
    """
    from vaibify.gui.registryRoutes import fnRegisterRegistryRoutes
    _fsRegisterProject(tmp_path, S_HOST_PROJECT, "host")
    _fsRegisterProject(tmp_path, S_CONTAINER_PROJECT, "container")
    app = FastAPI()
    app.state.dictContainerOwners = {}
    app.state.iHubPort = 8050
    dictRequirement = {"fnRequire": lambda: None}
    dictCtx = {
        "require": lambda *aArgs: dictRequirement["fnRequire"](),
        "docker": None,
    }
    fnRegisterRegistryRoutes(app, dictCtx)
    return TestClient(app), dictRequirement


T_CONTAINER_ONLY_ROUTES = (
    ("build", "Building an image"),
    ("start", "Starting a container"),
    ("start/cancel", "Cancelling a container start"),
    ("stop", "Stopping a container"),
)


def _fdictDetailOf(response):
    """Return the response's detail object, or {} when it carries none."""
    try:
        dictBody = response.json()
    except ValueError:
        return {}
    detail = (dictBody or {}).get("detail")
    return detail if isinstance(detail, dict) else {}


@pytest.mark.falsification
@pytest.mark.parametrize("sSuffix,sCapability", T_CONTAINER_ONLY_ROUTES)
def testEveryContainerOnlyLifecycleRouteRefusesAHostProject(
    tclientBothModes, sSuffix, sCapability,
):
    """A host project is refused, classifiably, before anything runs.

    Kills: removing the host branch from
    ``fnRefuseContainerOnlyForHostProject``, which lets every one of
    these routes drive container machinery at a project that has none.
    """
    client, _ = tclientBothModes
    response = client.post(
        f"/api/containers/{S_HOST_PROJECT}/{sSuffix}",
    )
    assert response.status_code == routeContext.I_REJECT_CONTAINER_ONLY
    assert response.status_code != 403, (
        "the refusal must not be the authorization code: a client that "
        "cannot tell 'this project has no container' from 'your "
        "credential was rejected' tells the researcher to re-claim a "
        "project that is already theirs"
    )
    dictDetail = _fdictDetailOf(response)
    assert dictDetail.get("sUnavailableIn") == (
        routeContext.S_UNAVAILABLE_IN_HOST_MODE
    ), (
        f"the refusal carried no machine-readable marker: {dictDetail}. "
        "A panel deciding whether to hide a control must not have to "
        "parse prose."
    )
    assert sCapability in dictDetail.get("sMessage", ""), (
        f"the refusal does not name what was refused: {dictDetail}"
    )
    assert S_HOST_PROJECT in dictDetail.get("sMessage", "")


@pytest.mark.falsification
@pytest.mark.parametrize("sSuffix,sCapability", T_CONTAINER_ONLY_ROUTES)
def testTheSameRoutesNeverRefuseAContainerProject(
    tclientBothModes, sSuffix, sCapability,
):
    """The other direction: a container project is never host-refused.

    Asserted on the MARKER rather than on a success status, because
    what these routes answer for a container project depends on a
    daemon this test has none of — and a refusal that fired for every
    project would still be a working Docker route away from being
    invisible here.

    Kills: making the host branch unconditional, which leaves every
    containerized project unbuildable, unstartable and unstoppable.
    """
    del sCapability
    client, _ = tclientBothModes
    response = client.post(
        f"/api/containers/{S_CONTAINER_PROJECT}/{sSuffix}",
    )
    assert "sUnavailableIn" not in _fdictDetailOf(response), (
        "a containerized project was told it has no container: "
        f"{response.status_code} {response.text}"
    )


@pytest.mark.falsification
def testAHostProjectIsRefusedEvenWhenDockerIsUnreachable(
    tclientBothModes,
):
    """The refusal precedes the daemon check, and must.

    Host mode exists for the researcher who has no Docker. Asking the
    daemon first answers "Docker is unavailable" about a project that
    never wanted Docker — a diagnosis that sends them to install the
    very thing host mode let them skip.

    Kills: moving the refusal below ``dictCtx["require"]()`` in the
    stop route.
    """
    from fastapi import HTTPException

    client, dictRequirement = tclientBothModes

    def fnRefuseEveryDockerCall():
        raise HTTPException(503, "Docker support is not available.")

    dictRequirement["fnRequire"] = fnRefuseEveryDockerCall
    response = client.post(f"/api/containers/{S_HOST_PROJECT}/stop")
    assert response.status_code == (
        routeContext.I_REJECT_CONTAINER_ONLY
    ), (
        "a host project's stop reported the missing daemon rather than "
        f"the missing container: {response.status_code} {response.text}"
    )
