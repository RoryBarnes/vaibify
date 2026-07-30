"""The /api/bootstrap capability exchange and middleware acceptance.

Slice 1b of the A1 identity boundary: a launch capability is exchanged
for a per-browser credential, and the middleware accepts that credential
(alongside the transitional shared token) on guarded routes. The agent
lane must never bootstrap.
"""

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from vaibify.gui import actionCatalog
from vaibify.gui import browserSession
from vaibify.gui import pipelineServer


@pytest.fixture
def app():
    """Build the viewer app over a mocked Docker, Host check disabled."""
    from tests.testPipelineServerRoutes import _fmockCreateDocker
    with patch.object(
        pipelineServer, "_fconnectionCreateDocker", _fmockCreateDocker,
    ):
        return pipelineServer.fappCreateApplication(
            sWorkspaceRoot="/workspace", sTerminalUserArg="testuser",
            iExpectedPort=0,
        )


def _fsMintCapability(app):
    return browserSession.fsMintBootstrapCapability(
        app.state.dictBrowserSessions,
    )


def test_bootstrap_exchanges_a_capability_for_a_credential(app):
    client = TestClient(app)
    sCapability = _fsMintCapability(app)
    response = client.post("/api/bootstrap", json={"sCapability": sCapability})
    assert response.status_code == 200, response.text
    dictBody = response.json()
    assert dictBody["sCredential"]
    assert dictBody["sSessionId"]


def test_bootstrap_rejects_an_invalid_capability(app):
    client = TestClient(app)
    response = client.post(
        "/api/bootstrap", json={"sCapability": "never-minted"},
    )
    assert response.status_code == 401


def test_bootstrap_refuses_the_agent_lane(app):
    client = TestClient(app)
    sCapability = _fsMintCapability(app)
    response = client.post(
        "/api/bootstrap",
        json={"sCapability": sCapability},
        headers={actionCatalog.S_SESSION_HEADER_NAME: "agent-token"},
    )
    assert response.status_code == 403


def test_bootstrap_retry_returns_the_same_credential(app):
    """A lost response is recoverable: the same capability replays."""
    client = TestClient(app)
    sCapability = _fsMintCapability(app)
    first = client.post("/api/bootstrap", json={"sCapability": sCapability})
    second = client.post("/api/bootstrap", json={"sCapability": sCapability})
    assert first.status_code == second.status_code == 200
    assert first.json()["sCredential"] == second.json()["sCredential"]


def test_a_bootstrapped_credential_authorizes_a_guarded_route(app):
    """The credential the exchange returns is accepted by the middleware."""
    client = TestClient(app)
    sCapability = _fsMintCapability(app)
    sCredential = client.post(
        "/api/bootstrap", json={"sCapability": sCapability},
    ).json()["sCredential"]

    # A guarded API route with the credential must NOT be a 401.
    response = client.get(
        "/api/session-status",
        headers={"X-Session-Token": sCredential},
    )
    assert response.status_code != 401, response.text


def test_an_invalid_credential_is_rejected_on_a_guarded_route(app):
    client = TestClient(app)
    response = client.get(
        "/api/session-status",
        headers={"X-Session-Token": "not-a-real-credential"},
    )
    assert response.status_code == 401


def test_the_shared_token_still_authorizes_during_transition(app):
    """Slice 1b keeps the shared token working so the GUI stays loadable."""
    client = TestClient(app)
    response = client.get(
        "/api/session-status",
        headers={"X-Session-Token": app.state.sSessionToken},
    )
    assert response.status_code != 401, response.text
