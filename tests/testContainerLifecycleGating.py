"""Slice 9: the name-keyed container-lifecycle routes are lease-enforced.

Design §12 slice 9. ``stop`` and ``settings`` carry the
``container-lifecycle`` scope: refused (403) for a browser session that
does not hold the container's lease, permitted when the container has no
owner record at all, and refused (409) while a start reservation is live.
``build`` deliberately stays ``browser-hub`` — it is an image operation
whose project may never have had an owner — and the gate below proves an
UNOWNED project still builds.

Everything here drives the REAL hub application over HTTP with genuine
per-session credentials, and the container's NAME is kept distinct from
its Docker ID: the owner map is name-keyed while the container routes are
id-keyed, and this repository has already shipped one fatal bug that a
``name == id`` fixture hid.
"""

import os
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from vaibify.config import containerLock, registryManager
from vaibify.gui import browserSession, pipelineServer
from tests.sessionTokenTestHelper import fsBootstrapCredential
from tests.testAgentLaneEnforcement import (
    MockDockerConnection,
    S_CONTAINER_NAME,
)


@pytest.fixture(autouse=True)
def fixtureIsolateHostState(tmp_path, monkeypatch):
    """Point the flock directory and the project registry at tmp_path."""
    monkeypatch.setattr(
        containerLock, "_S_LOCK_DIRECTORY", str(tmp_path / "locks"),
    )
    sRegistryDirectory = str(tmp_path / ".vaibify")
    monkeypatch.setattr(
        registryManager, "_S_REGISTRY_DIRECTORY", sRegistryDirectory,
    )
    monkeypatch.setattr(
        registryManager, "_S_REGISTRY_PATH",
        os.path.join(sRegistryDirectory, "registry.json"),
    )


@pytest.fixture
def appHub():
    """Build the real hub application over a mocked Docker connection."""
    with patch.object(
        pipelineServer, "_fconnectionCreateDocker", MockDockerConnection,
    ):
        return pipelineServer.fappCreateHubApplication(iExpectedPort=0)


def fnRegisterProject(client, tmp_path, sProjectName):
    """Register a minimal project directory so the routes can find it."""
    sProjectDirectory = str(tmp_path / sProjectName)
    os.makedirs(sProjectDirectory, exist_ok=True)
    with open(
        os.path.join(sProjectDirectory, "vaibify.yml"), "w",
    ) as fileHandle:
        fileHandle.write(f"projectName: {sProjectName}\n")
    responseAdd = client.post(
        "/api/registry", json={"sDirectory": sProjectDirectory},
    )
    assert responseAdd.status_code == 200, responseAdd.text
    return sProjectDirectory


def fclientAuthenticated(app):
    """Return a client carrying its own fresh browser credential."""
    return TestClient(app, headers={"X-Session-Token": fsBootstrapCredential(
        app,
    )})


def fsSessionIdOnApp(app, sCredential):
    """Resolve a minted credential to its browser session id."""
    return browserSession.fsSessionIdForCredential(
        app.state.dictBrowserSessions, sCredential,
    )


@pytest.mark.falsification
def test_stop_by_a_session_that_does_not_hold_the_lease_is_refused(
    appHub, tmp_path, monkeypatch,
):
    """A foreign browser session may not stop an owned container.

    THE SLICE-9 GATE. Session A claims the container and holds its
    lease. Session B — a genuinely distinct ``BrowserSessionRecord``
    credential — posts stop three ways: with no lease, with a forged
    lease, and with session A's REAL lease value copied. All three must
    be 403, and the stop executor must never run: a refusal that still
    tears the container down would be no boundary at all. The owner,
    presenting its own credential AND lease, is the positive control.

    Kills: in routeScope.DICT_CONTROL_PLANE_SCOPES, classify
    ``POST /api/containers/{sName}/stop`` as ``browser-hub`` again — the
    pre-slice-9 residual — and the foreign session's stop succeeds.
    """
    clientOwner = fclientAuthenticated(appHub)
    fnRegisterProject(clientOwner, tmp_path, S_CONTAINER_NAME)
    listStopped = []
    monkeypatch.setattr(
        "vaibify.gui.registryRoutes._fnExecuteStop",
        lambda sContainerName: listStopped.append(sContainerName),
    )
    responseClaim = clientOwner.post(
        f"/api/registry/{S_CONTAINER_NAME}/claim",
    )
    assert responseClaim.status_code == 200, responseClaim.text
    sOwningLease = responseClaim.json()["sLeaseId"]

    clientIntruder = fclientAuthenticated(appHub)
    for dictLeaseHeader in (
        {},
        {"X-Vaibify-Lease": "forged-lease-value"},
        {"X-Vaibify-Lease": sOwningLease},
    ):
        responseForeign = clientIntruder.post(
            f"/api/containers/{S_CONTAINER_NAME}/stop",
            headers=dictLeaseHeader,
        )
        assert responseForeign.status_code == 403, (
            "a session that does not hold the lease stopped an owned "
            f"container ({dictLeaseHeader}): {responseForeign.text}"
        )
    assert listStopped == [], (
        "a refused stop still ran the container teardown"
    )

    responseOwner = clientOwner.post(
        f"/api/containers/{S_CONTAINER_NAME}/stop",
        headers={"X-Vaibify-Lease": sOwningLease},
    )
    assert responseOwner.status_code == 200, responseOwner.text
    assert listStopped == [S_CONTAINER_NAME]


def test_stop_of_an_unowned_container_is_permitted(
    appHub, tmp_path, monkeypatch,
):
    """A container nobody holds stays stoppable from the dashboard.

    The asymmetry that makes ``container-lifecycle`` a distinct scope: a
    container with no owner record has no live session to protect, and
    the picker shows it running. Refusing here (as ``container-owner``
    would) makes a running container unstoppable from the page reporting
    it.
    """
    clientBrowser = fclientAuthenticated(appHub)
    fnRegisterProject(clientBrowser, tmp_path, S_CONTAINER_NAME)
    listStopped = []
    monkeypatch.setattr(
        "vaibify.gui.registryRoutes._fnExecuteStop",
        lambda sContainerName: listStopped.append(sContainerName),
    )
    assert S_CONTAINER_NAME not in appHub.state.dictContainerOwners
    responseStop = clientBrowser.post(
        f"/api/containers/{S_CONTAINER_NAME}/stop",
    )
    assert responseStop.status_code == 200, responseStop.text
    assert listStopped == [S_CONTAINER_NAME]


def test_settings_by_a_foreign_session_is_refused(
    appHub, tmp_path,
):
    """Reconfiguring an owned container needs its lease too."""
    clientOwner = fclientAuthenticated(appHub)
    fnRegisterProject(clientOwner, tmp_path, S_CONTAINER_NAME)
    clientOwner.post(f"/api/registry/{S_CONTAINER_NAME}/claim")
    clientIntruder = fclientAuthenticated(appHub)
    responseSettings = clientIntruder.post(
        f"/api/containers/{S_CONTAINER_NAME}/settings",
        json={"bNeverSleep": True},
    )
    assert responseSettings.status_code == 403, responseSettings.text


def test_build_still_works_for_an_unowned_project(
    appHub, tmp_path, monkeypatch,
):
    """THE OTHER HALF OF THE GATE: build stays browser-hub.

    An image build has no owner — a project whose container has never
    run has no owner record and no lease to present — so gating build on
    a lease would make building impossible for exactly the projects that
    need it most.
    """
    clientBrowser = fclientAuthenticated(appHub)
    fnRegisterProject(clientBrowser, tmp_path, "unowned-project")
    listBuilt = []
    monkeypatch.setattr(
        "vaibify.gui.buildRoutes._fnExecuteBuild",
        lambda dictProject, bNoCache, dictProgress: listBuilt.append(
            dictProject["sName"],
        ),
    )
    assert "unowned-project" not in appHub.state.dictContainerOwners
    responseBuild = clientBrowser.post(
        "/api/containers/unowned-project/build",
    )
    assert responseBuild.status_code == 200, responseBuild.text
    assert listBuilt == ["unowned-project"]
