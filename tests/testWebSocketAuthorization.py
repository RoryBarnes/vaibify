"""Unit tests for the shared container-session authorization guard.

Covers ``vaibify.gui.webSocketAuthorization``: the browser lane is
authorized only when a loopback origin, the shared token, and the owning
lease all hold; each failure yields its own close code; and the
in-container agent lane is authorized only by the container's own
per-container agent token, never the hub-wide shared token and never
another container's token. The final pair of tests proves both WebSocket
route modules delegate to the single guard rather than inlining a gate.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from vaibify.gui import containerOwnership, webSocketAuthorization
from vaibify.gui.actionCatalog import S_SESSION_HEADER_NAME


S_SHARED_TOKEN = "shared-trust-token"
S_OWNING_LEASE = "owning-lease-abcdef"
S_CONTAINER = "container-1"
S_AGENT_TOKEN = "per-container-agent-token-1"


class _FakeConnection:
    """Minimal stand-in for a Starlette WebSocket / Request.

    Exposes the only two surfaces the guard reads: a header mapping with
    ``.items()`` and a ``query_params`` mapping with ``.get()``.
    """

    def __init__(self, dictHeaders=None, dictQuery=None):
        self.headers = dictHeaders or {}
        self.query_params = dictQuery or {}


def _fdictOwnersWithOwner(sLeaseId=S_OWNING_LEASE, sAgentToken=S_AGENT_TOKEN):
    """Return an owner map holding one record for ``S_CONTAINER``."""
    return {
        S_CONTAINER: containerOwnership.OwnerRecord(
            sLeaseId=sLeaseId, fileHandleLock=MagicMock(),
            sAgentToken=sAgentToken, sContainerId="cid-1",
        ),
    }


def _fdictContext(dictContainerOwners):
    """Return a dictCtx carrying the shared token and owner map."""
    return {
        "sSessionToken": S_SHARED_TOKEN,
        "dictContainerOwners": dictContainerOwners,
    }


def _fconnBrowser(sOrigin="http://localhost:8000",
                  sToken=S_SHARED_TOKEN, sLeaseId=S_OWNING_LEASE):
    """Return a loopback browser connection with token and lease query."""
    return _FakeConnection(
        dictHeaders={"origin": sOrigin},
        dictQuery={"sToken": sToken, "sLeaseId": sLeaseId},
    )


def _fconnAgent(sToken=S_AGENT_TOKEN):
    """Return an agent connection: per-container token header, no origin."""
    return _FakeConnection(
        dictHeaders={S_SESSION_HEADER_NAME.lower(): sToken},
        dictQuery={},
    )


# -- browser lane ---------------------------------------------------------


def test_authorizes_when_origin_token_and_lease_all_hold():
    dictCtx = _fdictContext(_fdictOwnersWithOwner())
    conn = _fconnBrowser()
    assert webSocketAuthorization.fbAuthorizeContainerSession(
        conn, dictCtx, S_CONTAINER,
    ) is True
    assert webSocketAuthorization.fiContainerSessionRejectionCode(
        conn, dictCtx, S_CONTAINER,
    ) == 0


def test_foreign_lease_rejected_4403():
    dictCtx = _fdictContext(_fdictOwnersWithOwner())
    conn = _fconnBrowser(sLeaseId="some-other-sessions-lease")
    assert webSocketAuthorization.fiContainerSessionRejectionCode(
        conn, dictCtx, S_CONTAINER,
    ) == 4403
    assert webSocketAuthorization.fbAuthorizeContainerSession(
        conn, dictCtx, S_CONTAINER,
    ) is False


def test_absent_lease_rejected_4403():
    dictCtx = _fdictContext(_fdictOwnersWithOwner())
    conn = _FakeConnection(
        dictHeaders={"origin": "http://localhost:8000"},
        dictQuery={"sToken": S_SHARED_TOKEN},
    )
    assert webSocketAuthorization.fiContainerSessionRejectionCode(
        conn, dictCtx, S_CONTAINER,
    ) == 4403


def test_unowned_container_rejected_4403():
    dictCtx = _fdictContext({})
    conn = _fconnBrowser()
    assert webSocketAuthorization.fiContainerSessionRejectionCode(
        conn, dictCtx, S_CONTAINER,
    ) == 4403


def test_bad_origin_without_agent_token_rejected_4003():
    # A non-loopback origin is never a browser; with no valid agent token
    # it cannot reach the lease-exempt lane and is refused as bad origin.
    dictCtx = _fdictContext(_fdictOwnersWithOwner())
    conn = _fconnBrowser(
        sOrigin="http://evil.example.com", sToken="wrong-token",
    )
    assert webSocketAuthorization.fiContainerSessionRejectionCode(
        conn, dictCtx, S_CONTAINER,
    ) == 4003


def test_shared_token_cannot_use_agent_lane():
    # The hub-wide shared token is NOT a per-container agent credential.
    # A non-loopback connection presenting only the shared token (not the
    # container's own agent token) is refused as a bad origin, so a
    # compromised holder of the shared token cannot ride the agent lane.
    dictCtx = _fdictContext(_fdictOwnersWithOwner())
    conn = _fconnAgent(sToken=S_SHARED_TOKEN)
    assert webSocketAuthorization.fiContainerSessionRejectionCode(
        conn, dictCtx, S_CONTAINER,
    ) == 4003


def test_bad_token_rejected_4401():
    dictCtx = _fdictContext(_fdictOwnersWithOwner())
    conn = _fconnBrowser(sToken="wrong-token")
    assert webSocketAuthorization.fiContainerSessionRejectionCode(
        conn, dictCtx, S_CONTAINER,
    ) == 4401


# -- agent lane -----------------------------------------------------------


def test_agent_token_authorizes_own_container():
    dictCtx = _fdictContext(_fdictOwnersWithOwner())
    conn = _fconnAgent()
    assert webSocketAuthorization.fbCheckAgentToken(
        conn, dictCtx["dictContainerOwners"], S_CONTAINER,
    ) is True
    assert webSocketAuthorization.fiContainerSessionRejectionCode(
        conn, dictCtx, S_CONTAINER,
    ) == 0


def test_agent_token_rejected_for_another_container():
    # The whole point of per-container tokens: an agent holding container
    # one's token must NOT authenticate against a second container that
    # has its own, different token. This closes the hub-wide-token hole.
    dictContainerOwners = _fdictOwnersWithOwner()
    dictContainerOwners["container-2"] = containerOwnership.OwnerRecord(
        sLeaseId="lease-2", fileHandleLock=MagicMock(),
        sAgentToken="per-container-agent-token-2", sContainerId="cid-2",
    )
    dictCtx = _fdictContext(dictContainerOwners)
    conn = _fconnAgent(sToken=S_AGENT_TOKEN)
    assert webSocketAuthorization.fbCheckAgentToken(
        conn, dictContainerOwners, "container-2",
    ) is False
    assert webSocketAuthorization.fiContainerSessionRejectionCode(
        conn, dictCtx, "container-2",
    ) == 4003


def test_agent_lane_rejects_unowned_container():
    dictCtx = _fdictContext(_fdictOwnersWithOwner())
    conn = _fconnAgent()
    assert webSocketAuthorization.fbCheckAgentToken(
        conn, dictCtx["dictContainerOwners"], "unowned-container",
    ) is False
    assert webSocketAuthorization.fiContainerSessionRejectionCode(
        conn, dictCtx, "unowned-container",
    ) == 4003


def test_agent_lane_rejects_wrong_token():
    dictCtx = _fdictContext(_fdictOwnersWithOwner())
    conn = _fconnAgent(sToken="not-this-containers-agent-token")
    assert webSocketAuthorization.fbCheckAgentToken(
        conn, dictCtx["dictContainerOwners"], S_CONTAINER,
    ) is False


# -- both WebSocket routes delegate to the one guard ----------------------


def _fnCaptureRegisteredHandler(fnRegister, dictCtx):
    """Register a WS route against a capturing app and return its handler."""
    app = MagicMock()
    listRegistered = []

    def fnCaptureRoute(sPath):
        def fnDecorator(fnHandler):
            listRegistered.append(fnHandler)
            return fnHandler
        return fnDecorator

    app.websocket = fnCaptureRoute
    fnRegister(app, dictCtx)
    return app, listRegistered[0]


@pytest.mark.asyncio
async def test_pipeline_ws_route_delegates_to_guard():
    from vaibify.gui.routes import pipelineRoutes

    dictCtx = {
        "require": MagicMock(),
        "sSessionToken": S_SHARED_TOKEN,
        "dictContainerOwners": {},
    }
    _app, fnHandler = _fnCaptureRegisteredHandler(
        pipelineRoutes._fnRegisterPipelineWs, dictCtx,
    )
    mockWs = AsyncMock()
    with patch.object(
        pipelineRoutes, "fiContainerSessionRejectionCode",
        return_value=4403,
    ) as mockGuard:
        await fnHandler(mockWs, S_CONTAINER)
    mockGuard.assert_called_once_with(mockWs, dictCtx, S_CONTAINER)
    mockWs.close.assert_awaited_once_with(code=4403)


@pytest.mark.asyncio
async def test_terminal_ws_route_delegates_to_guard():
    from vaibify.gui.routes import terminalRoutes

    dictCtx = {
        "require": MagicMock(),
        "sSessionToken": S_SHARED_TOKEN,
        "dictContainerOwners": {},
    }
    _app, fnHandler = _fnCaptureRegisteredHandler(
        terminalRoutes._fnRegisterTerminalWs, dictCtx,
    )
    mockWs = AsyncMock()
    with patch.object(
        terminalRoutes, "fiContainerSessionRejectionCode",
        return_value=4003,
    ) as mockGuard:
        await fnHandler(mockWs, S_CONTAINER)
    mockGuard.assert_called_once_with(mockWs, dictCtx, S_CONTAINER)
    mockWs.close.assert_awaited_once_with(code=4003)
