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

import pytest

from fastapi.testclient import TestClient

from vaibify.gui import pipelineServer
from tests.sessionTokenTestHelper import fsBootstrapCredential


def _fmockCreateDocker():
    """Return None to simulate an unavailable Docker daemon."""
    return None


def _fbuildAppWithoutDocker():
    """Build an app whose Docker probe returns None at startup."""
    pipelineServer._dictDockerStatus["sError"] = (
        "Cannot connect to the Docker daemon at "
        "unix:///home/researcher/.colima/default/docker.sock"
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
    """Reset the module-level holder to a known-good state.

    Delegates to the production reset rather than listing the keys.
    The hand-written list silently stopped clearing everything the
    moment the holder gained ``sEndpoint``, leaking a stale endpoint
    into every later test in the file -- a second authority on what a
    clean holder is, drifting the first time the first one changed.
    """
    pipelineServer._fnClearDockerError()


def _fclientOwningContainer(app, sContainerId):
    """Return a TestClient whose browser session owns ``sContainerId``.

    Container reads are lease-enforced, and with Docker down there is no
    claim path to mint a lease, so the owner record is installed directly
    — bound to this client's real browser session — to reach the
    endpoint's Docker-unavailable diagnosis through the gate.
    """
    from vaibify.gui import browserSession, containerOwnership
    sCredential = fsBootstrapCredential(app)
    sSessionId = browserSession.fsSessionIdForCredential(
        app.state.dictBrowserSessions, sCredential,
    )
    app.state.dictContainerOwners["docker-down-owned-name"] = (
        containerOwnership.OwnerRecord(
            sLeaseId="docker-status-lease", fileHandleLock=None,
            sAgentToken="", sContainerId=sContainerId,
            sBrowserSessionId=sSessionId,
        )
    )
    return TestClient(app, headers={
        "X-Session-Token": sCredential,
        "X-Vaibify-Lease": "docker-status-lease",
    })


def test_get_docker_status_returns_cached_diagnosis():
    """GET surfaces the cached error/hint/command for the banner."""
    app = _fbuildAppWithoutDocker()
    clientHttp = TestClient(
        app, headers={"X-Session-Token": fsBootstrapCredential(app)},
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
        app, headers={"X-Session-Token": fsBootstrapCredential(app)},
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
        app, headers={"X-Session-Token": fsBootstrapCredential(app)},
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
        app, headers={"X-Session-Token": fsBootstrapCredential(app)},
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
    clientHttp = _fclientOwningContainer(app, "anything")
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
    clientHttp = _fclientOwningContainer(app, "x")

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


# -----------------------------------------------------------------------
# The endpoint that was actually tried
# -----------------------------------------------------------------------


def _fnRecordFailureWithEndpoint(monkeypatch, sHost, sContextEndpoint=""):
    """Record a Docker failure with the endpoint resolution pinned.

    Both halves are pinned so the assertions do not depend on whether
    the machine running the suite happens to have a Docker context.
    """
    if sHost is None:
        monkeypatch.delenv("DOCKER_HOST", raising=False)
    else:
        monkeypatch.setenv("DOCKER_HOST", sHost)
    monkeypatch.setattr(
        "vaibify.docker.dockerContext.fsReadActiveContextEndpoint",
        lambda: sContextEndpoint,
    )
    monkeypatch.setattr(
        "vaibify.docker.dockerContext.fsActiveDockerContext",
        lambda: "default",
    )
    pipelineServer._fnRecordDockerError(
        "Error while fetching server API version: ('Connection "
        "aborted.', FileNotFoundError(2, 'No such file or directory'))"
    )


@pytest.mark.falsification
def test_the_503_names_the_endpoint_vaibify_tried(monkeypatch):
    """A researcher whose CLI works needs the path vaibify used.

    docker-py's socket-absent error names no path, so "the daemon is
    not running" and "vaibify resolved a different socket than your
    shell" are indistinguishable from the message alone -- which is
    exactly the pair a researcher hit on Ubuntu with a running engine
    and a context pointing elsewhere.

    Kills: dropping the sEndpoint line from
    ``_fsBuildDockerUnavailableDetail``.
    """
    _fnRecordFailureWithEndpoint(
        monkeypatch, "unix:///somewhere/else/docker.sock",
    )
    sDetail = pipelineServer._fsBuildDockerUnavailableDetail()
    assert "unix:///somewhere/else/docker.sock" in sDetail, (
        f"the endpoint that failed is not in the message: {sDetail}"
    )
    _fclearDockerStatusHolder()


def test_an_unset_docker_host_says_so_rather_than_naming_a_default(
    monkeypatch,
):
    """Reporting a default vaibify does not own would be a guess.

    docker-py picks the fallback socket; restating it here would make
    vaibify a second authority on it, and a wrong one the day
    docker-py changes it.
    """
    _fnRecordFailureWithEndpoint(monkeypatch, None, sContextEndpoint="")
    sDetail = pipelineServer._fsBuildDockerUnavailableDetail()
    assert "DOCKER_HOST unset" in sDetail, sDetail
    assert "/var/run/docker.sock" not in sDetail, (
        f"a default docker-py owns was restated as fact: {sDetail}"
    )
    _fclearDockerStatusHolder()


def test_the_endpoint_may_come_from_the_context_rather_than_the_env(
    monkeypatch,
):
    """The common macOS setup has an unset env var and a live context.

    Answering "DOCKER_HOST unset" there would be true about the
    variable and useless about the question the researcher is asking.
    """
    _fnRecordFailureWithEndpoint(
        monkeypatch, None,
        sContextEndpoint="unix:///home/researcher/.colima/docker.sock",
    )
    sDetail = pipelineServer._fsBuildDockerUnavailableDetail()
    assert "unix:///home/researcher/.colima/docker.sock" in sDetail, sDetail
    assert "from the active Docker context" in sDetail, sDetail
    _fclearDockerStatusHolder()


@pytest.mark.falsification
def test_the_status_probe_carries_the_endpoint_to_the_banner(monkeypatch):
    """The 503 is not the only surface; the hub banner renders it too.

    Kills: dropping the sEndpoint line from ``_fnRecordDockerError``,
    which empties the key the banner reads while the 503 detail --
    built from the same holder -- goes quiet in the same breath.
    """
    _fnRecordFailureWithEndpoint(
        monkeypatch, "unix:///another/docker.sock",
    )
    dictStatus = pipelineServer.fdictGetDockerStatus()
    assert "unix:///another/docker.sock" in dictStatus["sEndpoint"]
    assert "from DOCKER_HOST" in dictStatus["sEndpoint"], (
        "the endpoint arrived without its PROVENANCE; which of the env "
        "var and the context supplied it is half the diagnosis: "
        + dictStatus["sEndpoint"]
    )
    _fclearDockerStatusHolder()
    assert pipelineServer.fdictGetDockerStatus()["sEndpoint"] == "", (
        "a cleared holder still reported an endpoint, so a recovered "
        "daemon would be described by the failure that preceded it"
    )
