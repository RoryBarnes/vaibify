"""Tests for pipelineServer hunks added by the agent-action bridge.

Covers:

- ``_fnPushAgentSession`` swallowing bridge errors without aborting the
  container connect flow.
- ``iPort`` threaded through ``fappCreateApplication`` and
  ``fappCreateHubApplication`` into ``dictCtx``.
- ``fsGetOriginHeader`` and ``fbHasAgentToken`` helpers.
"""

from unittest.mock import MagicMock, patch

import pytest

from vaibify.gui import pipelineServer


# -----------------------------------------------------------------------
# _fnPushAgentSession exception swallow
# -----------------------------------------------------------------------


def test_fnPushAgentSession_swallows_bridge_errors(caplog):
    """Bridge failure logs a warning and does not propagate."""
    mockDocker = MagicMock()
    dictCtx = {
        "docker": mockDocker,
        "sSessionToken": "tok",
        "iPort": 8050,
    }
    with patch.object(
        pipelineServer.agentSessionBridge,
        "fnPushAgentSessionToContainer",
        side_effect=RuntimeError("docker down"),
    ):
        import logging
        with caplog.at_level(logging.WARNING, logger="vaibify"):
            pipelineServer._fnPushAgentSession(dictCtx, "c-id")
    assert any(
        "Agent session push failed" in record.message
        for record in caplog.records
    )


def test_fnAuthorizeContainer_still_adds_to_allowed_when_bridge_fails():
    """An agent-bridge failure must not block container authorization."""
    setAllowed = set()
    mockDocker = MagicMock()
    mockDocker.ftResultExecuteCommand.return_value = (0, "scientist\n")
    dictCtx = {
        "docker": mockDocker,
        "setAllowedContainers": setAllowed,
        "containerUsers": {},
        "sSessionToken": "tok",
        "iPort": 8050,
    }
    with patch.object(
        pipelineServer.agentSessionBridge,
        "fnPushAgentSessionToContainer",
        side_effect=RuntimeError("docker down"),
    ):
        pipelineServer._fnAuthorizeContainer(dictCtx, "c-id")
    assert "c-id" in setAllowed
    assert dictCtx["containerUsers"]["c-id"] == "scientist"


def test_fnAuthorizeContainer_invokes_bridge_with_session_and_port():
    mockDocker = MagicMock()
    mockDocker.ftResultExecuteCommand.return_value = (0, "researcher\n")
    dictCtx = {
        "docker": mockDocker,
        "setAllowedContainers": set(),
        "containerUsers": {},
        "sSessionToken": "tok-abc",
        "iPort": 9100,
    }
    with patch.object(
        pipelineServer.agentSessionBridge,
        "fnPushAgentSessionToContainer",
    ) as mockPush:
        pipelineServer._fnAuthorizeContainer(dictCtx, "c-id")
    mockPush.assert_called_once_with(
        mockDocker, "c-id", "tok-abc", 9100,
    )


def test_fnPushAgentSession_missing_iport_defaults_to_zero():
    """``dictCtx.get('iPort', 0)`` should not KeyError when absent."""
    mockDocker = MagicMock()
    dictCtx = {
        "docker": mockDocker,
        "sSessionToken": "tok",
    }
    with patch.object(
        pipelineServer.agentSessionBridge,
        "fnPushAgentSessionToContainer",
    ) as mockPush:
        pipelineServer._fnPushAgentSession(dictCtx, "c-id")
    mockPush.assert_called_once_with(mockDocker, "c-id", "tok", 0)


# -----------------------------------------------------------------------
# fappCreateApplication / fappCreateHubApplication thread iPort
# -----------------------------------------------------------------------


def test_fappCreateApplication_threads_iport_into_dictCtx():
    """The context used by routes must carry the server's bind port."""
    listCaptured = []
    with patch.object(
        pipelineServer, "_fconnectionCreateDocker",
        return_value=MagicMock(),
    ):
        def fnCapture(app, dictCtx, sWorkspaceRoot):
            listCaptured.append(dictCtx)

        with patch.object(
            pipelineServer, "_fnRegisterAllRoutes",
            side_effect=fnCapture,
        ):
            pipelineServer.fappCreateApplication(iExpectedPort=8123)
    assert listCaptured[0]["iPort"] == 8123


def test_fappCreateHubApplication_threads_iport_into_dictCtx():
    listCaptured = []
    with patch.object(
        pipelineServer, "_fconnectionCreateDocker",
        return_value=MagicMock(),
    ):
        def fnCapture(app, dictCtx, sWorkspaceRoot):
            listCaptured.append(dictCtx)

        with patch.object(
            pipelineServer, "_fnRegisterAllRoutes",
            side_effect=fnCapture,
        ):
            pipelineServer.fappCreateHubApplication(iExpectedPort=7777)
    assert listCaptured[0]["iPort"] == 7777


# -----------------------------------------------------------------------
# fsGetOriginHeader and fbHasAgentToken helpers
# -----------------------------------------------------------------------


class _MockWebSocket:
    def __init__(self, dictHeaders, dictQuery=None):
        self.headers = dictHeaders
        self.query_params = dictQuery or {}


def test_fsGetOriginHeader_returns_origin_value():
    ws = _MockWebSocket({"origin": "http://localhost:8080"})
    assert pipelineServer.fsGetOriginHeader(ws) == "http://localhost:8080"


def test_fsGetOriginHeader_case_insensitive():
    ws = _MockWebSocket({"Origin": "http://127.0.0.1:1234"})
    assert pipelineServer.fsGetOriginHeader(ws) == "http://127.0.0.1:1234"


def test_fsGetOriginHeader_missing_returns_empty_string():
    ws = _MockWebSocket({})
    assert pipelineServer.fsGetOriginHeader(ws) == ""


def test_fbHasAgentToken_header_wins_over_query():
    ws = _MockWebSocket(
        {"x-vaibify-session": "good"},
        {"sToken": "ignored"},
    )
    assert pipelineServer.fbHasAgentToken(ws, "good") is True


def test_fbHasAgentToken_falls_back_to_query_when_no_header():
    ws = _MockWebSocket({}, {"sToken": "good"})
    assert pipelineServer.fbHasAgentToken(ws, "good") is True


def test_fbHasAgentToken_empty_header_falls_through_to_query():
    ws = _MockWebSocket({"x-vaibify-session": ""}, {"sToken": "good"})
    assert pipelineServer.fbHasAgentToken(ws, "good") is True


def test_fbHasAgentToken_empty_query_and_empty_expected_is_false():
    ws = _MockWebSocket({}, {"sToken": ""})
    assert pipelineServer.fbHasAgentToken(ws, "") is False


def test_fbHasAgentToken_no_tokens_anywhere_is_false():
    ws = _MockWebSocket({}, {})
    assert pipelineServer.fbHasAgentToken(ws, "expected") is False


def test_fbHasAgentToken_wrong_header_and_wrong_query_is_false():
    ws = _MockWebSocket(
        {"x-vaibify-session": "bad"},
        {"sToken": "also-bad"},
    )
    assert pipelineServer.fbHasAgentToken(ws, "expected") is False
