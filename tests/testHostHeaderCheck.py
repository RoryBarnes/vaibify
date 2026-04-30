"""Tests for DNS rebinding defense in the SessionTokenMiddleware.

When ``iExpectedPort`` is configured, the middleware must reject any
request whose ``Host:`` header does not match a loopback name bound to
the expected port. This prevents a remote page (whose DNS has been
re-pointed at 127.0.0.1) from driving state-changing API calls.
"""

from unittest.mock import patch

from fastapi.testclient import TestClient

from vaibify.gui import pipelineServer
from vaibify.gui.pipelineServer import fbIsAllowedHostHeader


def test_fbIsAllowedHostHeader_accepts_loopback_with_port():
    assert fbIsAllowedHostHeader("127.0.0.1:8050", 8050) is True
    assert fbIsAllowedHostHeader("localhost:8050", 8050) is True
    assert fbIsAllowedHostHeader("[::1]:8050", 8050) is True


def test_fbIsAllowedHostHeader_accepts_host_without_port():
    assert fbIsAllowedHostHeader("127.0.0.1", 8050) is True
    assert fbIsAllowedHostHeader("localhost", 8050) is True


def test_fbIsAllowedHostHeader_rejects_wrong_port():
    assert fbIsAllowedHostHeader("127.0.0.1:9999", 8050) is False
    assert fbIsAllowedHostHeader("localhost:9999", 8050) is False


def test_fbIsAllowedHostHeader_rejects_remote_host():
    assert fbIsAllowedHostHeader("evil.com:8050", 8050) is False
    assert fbIsAllowedHostHeader("attacker.example:8050", 8050) is False
    assert fbIsAllowedHostHeader("127.0.0.1.evil.com", 8050) is False


def test_fbIsAllowedHostHeader_rejects_empty():
    assert fbIsAllowedHostHeader("", 8050) is False
    assert fbIsAllowedHostHeader(None, 8050) is False


def test_fbIsAllowedHostHeader_rejects_bogus_port_text():
    assert fbIsAllowedHostHeader("127.0.0.1:abc", 8050) is False


def _fmockCreateDocker():
    """Return None — tests here never hit Docker."""
    return None


def _fnBuildAppWithPortCheck():
    """Build an app with DNS-rebinding check enabled on port 8050."""
    with patch.object(
        pipelineServer, "_fconnectionCreateDocker",
        _fmockCreateDocker,
    ):
        return pipelineServer.fappCreateApplication(
            sWorkspaceRoot="/workspace",
            iExpectedPort=8050,
        )


def test_middleware_accepts_loopback_host_header():
    """Localhost:8050 Host header is accepted."""
    app = _fnBuildAppWithPortCheck()
    clientHttp = TestClient(app)
    responseHttp = clientHttp.get(
        "/api/session-token",
        headers={"Host": "127.0.0.1:8050"},
    )
    assert responseHttp.status_code == 200


def test_middleware_rejects_evil_host_header():
    """Attacker-controlled hostname is rejected with HTTP 400."""
    app = _fnBuildAppWithPortCheck()
    clientHttp = TestClient(app)
    responseHttp = clientHttp.get(
        "/api/session-token",
        headers={"Host": "evil.com"},
    )
    assert responseHttp.status_code == 400
    assert "Invalid Host header" in responseHttp.text


def test_middleware_rejects_wrong_port_host_header():
    """A loopback name on a different port is rejected."""
    app = _fnBuildAppWithPortCheck()
    clientHttp = TestClient(app)
    responseHttp = clientHttp.get(
        "/api/session-token",
        headers={"Host": "127.0.0.1:7777"},
    )
    assert responseHttp.status_code == 400


def test_middleware_accepts_testserver_when_port_check_disabled():
    """Tests using the default ``iExpectedPort=0`` still run unhindered."""
    with patch.object(
        pipelineServer, "_fconnectionCreateDocker",
        _fmockCreateDocker,
    ):
        app = pipelineServer.fappCreateApplication(
            sWorkspaceRoot="/workspace",
        )
    clientHttp = TestClient(app)
    responseHttp = clientHttp.get("/api/session-token")
    assert responseHttp.status_code == 200
