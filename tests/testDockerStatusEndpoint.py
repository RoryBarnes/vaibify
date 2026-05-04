"""Tests for the /api/system/docker-status probe and retry endpoints.

The container hub renders a recovery banner from this endpoint when
Docker is unavailable. Failure modes covered:

- GET surfaces the cached error + hint so the banner can render
  without re-probing the daemon.
- POST .../retry re-runs the connection probe and updates the route
  context on success, so a recovered Docker daemon does not require
  a vaibify restart.
"""

from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from vaibify.gui import pipelineServer


def _fmockCreateDocker():
    """Return None to simulate an unavailable Docker daemon."""
    return None


def _fbuildAppWithoutDocker():
    """Build an app whose Docker probe returns None at startup."""
    pipelineServer._dictDockerStatus["sError"] = (
        "Cannot connect to the Docker daemon at "
        "unix:///Users/rory/.colima/default/docker.sock"
    )
    pipelineServer._dictDockerStatus["sHint"] = (
        "The Docker daemon is not reachable."
    )
    pipelineServer._dictDockerStatus["sCommand"] = "colima start"
    with patch.object(
        pipelineServer, "_fconnectionCreateDocker",
        _fmockCreateDocker,
    ):
        return pipelineServer.fappCreateApplication(
            sWorkspaceRoot="/workspace",
        )


def _fclearDockerStatusHolder():
    """Reset the module-level holder to a known-good state."""
    pipelineServer._dictDockerStatus["sError"] = ""
    pipelineServer._dictDockerStatus["sHint"] = ""
    pipelineServer._dictDockerStatus["sCommand"] = ""


def test_get_docker_status_returns_cached_diagnosis():
    """GET surfaces the cached error/hint/command for the banner."""
    app = _fbuildAppWithoutDocker()
    clientHttp = TestClient(
        app, headers={"X-Session-Token": app.state.sSessionToken},
    )
    response = clientHttp.get("/api/system/docker-status")
    assert response.status_code == 200
    dictBody = response.json()
    assert dictBody["bAvailable"] is False
    assert "daemon is not reachable" in dictBody["sHint"]
    assert dictBody["sCommand"] == "colima start"
    assert "Cannot connect" in dictBody["sError"]
    _fclearDockerStatusHolder()


def test_get_docker_status_when_available():
    """When Docker is available the probe reports bAvailable=True."""
    _fclearDockerStatusHolder()
    with patch.object(
        pipelineServer, "_fconnectionCreateDocker",
        return_value=MagicMock(),
    ):
        app = pipelineServer.fappCreateApplication(
            sWorkspaceRoot="/workspace",
        )
    clientHttp = TestClient(
        app, headers={"X-Session-Token": app.state.sSessionToken},
    )
    response = clientHttp.get("/api/system/docker-status")
    assert response.status_code == 200
    dictBody = response.json()
    assert dictBody["bAvailable"] is True
    assert dictBody["sError"] == ""
    _fclearDockerStatusHolder()


def test_retry_swaps_in_new_connection_on_success():
    """Retry replaces dictCtx['docker'] when probe succeeds."""
    app = _fbuildAppWithoutDocker()
    clientHttp = TestClient(
        app, headers={"X-Session-Token": app.state.sSessionToken},
    )
    mockConnection = MagicMock()

    def _fcreateNowSucceeds():
        pipelineServer._fnClearDockerError()
        return mockConnection

    with patch.object(
        pipelineServer, "_fconnectionCreateDocker",
        _fcreateNowSucceeds,
    ):
        response = clientHttp.post(
            "/api/system/docker-status/retry"
        )
    assert response.status_code == 200
    dictBody = response.json()
    assert dictBody["bAvailable"] is True
    _fclearDockerStatusHolder()


def test_retry_keeps_error_when_probe_still_fails():
    """A still-failing probe leaves the holder + 503 path intact."""
    app = _fbuildAppWithoutDocker()
    clientHttp = TestClient(
        app, headers={"X-Session-Token": app.state.sSessionToken},
    )

    def _fcreateStillFails():
        pipelineServer._fnRecordDockerError("daemon still down")
        return None

    with patch.object(
        pipelineServer, "_fconnectionCreateDocker",
        _fcreateStillFails,
    ):
        response = clientHttp.post(
            "/api/system/docker-status/retry"
        )
    assert response.status_code == 200
    dictBody = response.json()
    assert dictBody["bAvailable"] is False
    assert "daemon still down" in dictBody["sError"]
    _fclearDockerStatusHolder()


def test_503_includes_specific_diagnosis_not_generic_message():
    """The kebab Start path's 503 must carry the actionable hint."""
    app = _fbuildAppWithoutDocker()
    clientHttp = TestClient(
        app, headers={"X-Session-Token": app.state.sSessionToken},
    )
    response = clientHttp.get(
        "/api/containers/anything/ready"
    )
    assert response.status_code == 503
    sDetail = response.json().get("detail", "")
    assert "Docker support is not available" in sDetail
    assert "colima start" in sDetail
    _fclearDockerStatusHolder()


def test_route_swap_visible_to_other_routes():
    """After retry success, downstream routes see the new connection."""
    app = _fbuildAppWithoutDocker()
    clientHttp = TestClient(
        app, headers={"X-Session-Token": app.state.sSessionToken},
    )

    response503 = clientHttp.get("/api/containers/x/ready")
    assert response503.status_code == 503

    mockConnection = MagicMock()

    def _fcreateNowSucceeds():
        pipelineServer._fnClearDockerError()
        return mockConnection

    with patch.object(
        pipelineServer, "_fconnectionCreateDocker",
        _fcreateNowSucceeds,
    ):
        responseRetry = clientHttp.post(
            "/api/system/docker-status/retry"
        )
    assert responseRetry.status_code == 200
    assert responseRetry.json()["bAvailable"] is True

    response200 = clientHttp.get("/api/containers/x/ready")
    assert response200.status_code != 503
    _fclearDockerStatusHolder()
