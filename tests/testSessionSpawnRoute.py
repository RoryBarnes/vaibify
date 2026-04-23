"""Tests for the /api/session/spawn route."""

import sys

import pytest
from unittest.mock import MagicMock, patch


def _fmockAlivePopen():
    """Return a MagicMock resembling a live subprocess.Popen handle."""
    mockPopen = MagicMock()
    mockPopen.poll.return_value = None
    return mockPopen


@pytest.fixture
def fixtureApp():
    """Build a bare FastAPI app with only the session route."""
    from fastapi import FastAPI
    from vaibify.gui.routes.sessionRoutes import fnRegisterAll
    app = FastAPI()
    fnRegisterAll(app, {})
    return app


@pytest.fixture
def fixtureClient(fixtureApp):
    from starlette.testclient import TestClient
    return TestClient(fixtureApp)


def testSpawnRouteReturnsUrlAndPort(fixtureClient):
    with patch(
        "vaibify.cli.portAllocator.fiPickFreePort", return_value=8055,
    ), patch(
        "vaibify.gui.routes.sessionRoutes._fnLaunchDetachedHub",
        return_value=_fmockAlivePopen(),
    ) as mockLaunch:
        response = fixtureClient.post("/api/session/spawn")
    assert response.status_code == 200
    dictResult = response.json()
    assert dictResult["iPort"] == 8055
    assert dictResult["sUrl"] == "http://127.0.0.1:8055"
    mockLaunch.assert_called_once_with(8055)


def testSpawnRouteLaunchesClosedShapeCommand():
    """_fnLaunchDetachedHub uses sys.executable -m vaibify --port N."""
    from vaibify.gui.routes.sessionRoutes import _fnLaunchDetachedHub
    with patch("subprocess.Popen") as mockPopen:
        _fnLaunchDetachedHub(8099)
    tArgs, dictKwargs = mockPopen.call_args
    saCommand = tArgs[0]
    assert saCommand == [
        sys.executable, "-m", "vaibify", "--port", "8099",
    ]
    assert dictKwargs["start_new_session"] is True
    assert "env" not in dictKwargs


def testSpawnRouteRejectsContainerAgentCaller(fixtureApp, fixtureClient):
    """Requests bearing the in-container agent token get 403."""
    with patch(
        "vaibify.cli.portAllocator.fiPickFreePort", return_value=8055,
    ), patch(
        "vaibify.gui.routes.sessionRoutes._fnLaunchDetachedHub",
    ) as mockLaunch:
        response = fixtureClient.post(
            "/api/session/spawn",
            headers={"X-Vaibify-Session": "any-value"},
        )
    assert response.status_code == 403
    mockLaunch.assert_not_called()


def testSpawnRouteRateLimitsAtFiveLiveChildren(fixtureApp, fixtureClient):
    """The sixth concurrent spawn request returns 429 until a child exits."""
    from vaibify.gui.routes.sessionRoutes import _I_MAX_LIVE_SPAWNS
    fixtureApp.state.listSpawnedChildren = [
        _fmockAlivePopen() for _ in range(_I_MAX_LIVE_SPAWNS)
    ]
    with patch(
        "vaibify.cli.portAllocator.fiPickFreePort", return_value=8055,
    ), patch(
        "vaibify.gui.routes.sessionRoutes._fnLaunchDetachedHub",
    ):
        response = fixtureClient.post("/api/session/spawn")
    assert response.status_code == 429


def testSpawnRoutePrunesDeadChildrenBeforeRateLimiting(
    fixtureApp, fixtureClient,
):
    """Dead children free up spawn slots on the next request."""
    from vaibify.gui.routes.sessionRoutes import _I_MAX_LIVE_SPAWNS
    mockDead = MagicMock()
    mockDead.poll.return_value = 0
    fixtureApp.state.listSpawnedChildren = [
        mockDead for _ in range(_I_MAX_LIVE_SPAWNS)
    ]
    with patch(
        "vaibify.cli.portAllocator.fiPickFreePort", return_value=8055,
    ), patch(
        "vaibify.gui.routes.sessionRoutes._fnLaunchDetachedHub",
        return_value=_fmockAlivePopen(),
    ):
        response = fixtureClient.post("/api/session/spawn")
    assert response.status_code == 200
    assert len(fixtureApp.state.listSpawnedChildren) == 1


def testSpawnRouteRegisteredOnHubApplication():
    """fappCreateHubApplication includes the spawn route."""
    from vaibify.gui.pipelineServer import fappCreateHubApplication
    with patch(
        "vaibify.gui.pipelineServer._fconnectionCreateDocker",
        return_value=MagicMock(),
    ):
        app = fappCreateHubApplication(iExpectedPort=0)
    listRoutes = [route.path for route in app.routes]
    assert "/api/session/spawn" in listRoutes


def testSpawnRouteRegisteredOnWorkflowViewerApplication():
    """fappCreateApplication also includes the spawn route."""
    from vaibify.gui.pipelineServer import fappCreateApplication
    with patch(
        "vaibify.gui.pipelineServer._fconnectionCreateDocker",
        return_value=MagicMock(),
    ):
        app = fappCreateApplication(iExpectedPort=0)
    listRoutes = [route.path for route in app.routes]
    assert "/api/session/spawn" in listRoutes
