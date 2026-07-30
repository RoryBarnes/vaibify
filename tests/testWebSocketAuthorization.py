"""Unit tests for the shared container-session authorization guard.

Covers ``vaibify.gui.webSocketAuthorization``: the browser lane is
authorized only when a loopback origin, a valid per-browser credential
(never the retired shared token), and the lease BOUND to that credential's
session all hold; each failure yields its own close code; and the
in-container agent lane is authorized only by the container's own
per-container agent token, never the hub-wide shared token and never
another container's token. The final pair of tests proves both WebSocket
route modules delegate to the single guard rather than inlining a gate.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from vaibify.gui import (
    browserSession,
    containerOwnership,
    webSocketAuthorization,
)
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


def _tStoreWithCredential():
    """Mint a browser-session store and one redeemed credential.

    Returns ``(dictStore, sSessionId, sCredential)`` -- exactly what the
    browser holds after redeeming its launch capability at ``/api/bootstrap``.
    """
    dictStore = browserSession.fdictCreateBrowserSessionStore()
    sCapability = browserSession.fsMintBootstrapCapability(dictStore)
    sSessionId, sCredential = browserSession.ftRedeemCapability(
        dictStore, sCapability,
    )
    return dictStore, sSessionId, sCredential


def _fdictOwnersBoundTo(
    sBrowserSessionId, sLeaseId=S_OWNING_LEASE, sAgentToken=S_AGENT_TOKEN,
):
    """Return an owner map with one record bound to a browser session."""
    return {
        S_CONTAINER: containerOwnership.OwnerRecord(
            sLeaseId=sLeaseId, fileHandleLock=MagicMock(),
            sAgentToken=sAgentToken, sContainerId="cid-1",
            sBrowserSessionId=sBrowserSessionId,
        ),
    }


def _fdictOwnersWithOwner(sLeaseId=S_OWNING_LEASE, sAgentToken=S_AGENT_TOKEN):
    """Return an owner map holding one unbound record for ``S_CONTAINER``.

    Used by the agent-lane and live-connection-counter tests, which do not
    exercise the browser credential/session binding.
    """
    return _fdictOwnersBoundTo(
        "", sLeaseId=sLeaseId, sAgentToken=sAgentToken,
    )


def _fdictContext(dictContainerOwners, dictBrowserSessions=None):
    """Return a dictCtx carrying the owner map and browser-session store."""
    return {
        "sSessionToken": S_SHARED_TOKEN,
        "dictContainerOwners": dictContainerOwners,
        "dictBrowserSessions": dictBrowserSessions or {},
    }


def _fconnBrowser(sOrigin="http://localhost:8000",
                  sToken="", sLeaseId=S_OWNING_LEASE):
    """Return a loopback browser connection with credential and lease query."""
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


def test_authorizes_when_origin_credential_and_bound_lease_all_hold():
    dictStore, sSessionId, sCredential = _tStoreWithCredential()
    dictCtx = _fdictContext(_fdictOwnersBoundTo(sSessionId), dictStore)
    conn = _fconnBrowser(sToken=sCredential)
    assert webSocketAuthorization.fbAuthorizeContainerSession(
        conn, dictCtx, S_CONTAINER,
    ) is True
    assert webSocketAuthorization.fiContainerSessionRejectionCode(
        conn, dictCtx, S_CONTAINER,
    ) == 0


def test_foreign_lease_rejected_4403():
    dictStore, sSessionId, sCredential = _tStoreWithCredential()
    dictCtx = _fdictContext(_fdictOwnersBoundTo(sSessionId), dictStore)
    conn = _fconnBrowser(
        sToken=sCredential, sLeaseId="some-other-sessions-lease",
    )
    assert webSocketAuthorization.fiContainerSessionRejectionCode(
        conn, dictCtx, S_CONTAINER,
    ) == 4403
    assert webSocketAuthorization.fbAuthorizeContainerSession(
        conn, dictCtx, S_CONTAINER,
    ) is False


def test_session_b_with_session_a_lease_rejected_4403():
    # Session A owns the container. Session B holds a genuine, valid
    # credential of its own but presents A's real lease value (copied from
    # sessionStorage). The bound-lease check ties the lease to A's session,
    # so B is refused even though both the credential and the lease value
    # are individually valid -- the copied-lease replay the strong
    # predicate exists to stop.
    dictStore, sSessionIdA, _sCredentialA = _tStoreWithCredential()
    sCapabilityB = browserSession.fsMintBootstrapCapability(dictStore)
    _sSessionIdB, sCredentialB = browserSession.ftRedeemCapability(
        dictStore, sCapabilityB,
    )
    dictCtx = _fdictContext(_fdictOwnersBoundTo(sSessionIdA), dictStore)
    conn = _fconnBrowser(sToken=sCredentialB, sLeaseId=S_OWNING_LEASE)
    assert webSocketAuthorization.fiContainerSessionRejectionCode(
        conn, dictCtx, S_CONTAINER,
    ) == 4403


def test_absent_lease_rejected_4403():
    dictStore, sSessionId, sCredential = _tStoreWithCredential()
    dictCtx = _fdictContext(_fdictOwnersBoundTo(sSessionId), dictStore)
    conn = _FakeConnection(
        dictHeaders={"origin": "http://localhost:8000"},
        dictQuery={"sToken": sCredential},
    )
    assert webSocketAuthorization.fiContainerSessionRejectionCode(
        conn, dictCtx, S_CONTAINER,
    ) == 4403


def test_unowned_container_rejected_4403():
    dictStore, _sSessionId, sCredential = _tStoreWithCredential()
    dictCtx = _fdictContext({}, dictStore)
    conn = _fconnBrowser(sToken=sCredential)
    assert webSocketAuthorization.fiContainerSessionRejectionCode(
        conn, dictCtx, S_CONTAINER,
    ) == 4403


def test_bad_origin_without_agent_token_rejected_4003():
    # A non-loopback origin is never a browser; with no valid agent token
    # it cannot reach the lease-exempt lane and is refused as bad origin.
    dictStore, sSessionId, sCredential = _tStoreWithCredential()
    dictCtx = _fdictContext(_fdictOwnersBoundTo(sSessionId), dictStore)
    conn = _fconnBrowser(
        sOrigin="http://evil.example.com", sToken=sCredential,
    )
    assert webSocketAuthorization.fiContainerSessionRejectionCode(
        conn, dictCtx, S_CONTAINER,
    ) == 4003


def test_shared_token_cannot_use_agent_lane():
    # The hub-wide shared token is NOT a per-container agent credential.
    # A non-loopback connection presenting only the shared token (not the
    # container's own agent token) is refused as a bad origin, so a
    # compromised holder of the shared token cannot ride the agent lane.
    dictStore, sSessionId, _sCredential = _tStoreWithCredential()
    dictCtx = _fdictContext(_fdictOwnersBoundTo(sSessionId), dictStore)
    conn = _fconnAgent(sToken=S_SHARED_TOKEN)
    assert webSocketAuthorization.fiContainerSessionRejectionCode(
        conn, dictCtx, S_CONTAINER,
    ) == 4003


def test_shared_token_is_not_a_browser_credential_4401():
    # The retired shared session token must never clear the credential
    # gate: presented as the WS credential it is an unknown value, so the
    # browser lane fails closed at the token stage.
    dictStore, sSessionId, _sCredential = _tStoreWithCredential()
    dictCtx = _fdictContext(_fdictOwnersBoundTo(sSessionId), dictStore)
    conn = _fconnBrowser(sToken=S_SHARED_TOKEN)
    assert webSocketAuthorization.fiContainerSessionRejectionCode(
        conn, dictCtx, S_CONTAINER,
    ) == 4401


def test_invalid_credential_rejected_4401():
    dictStore, sSessionId, _sCredential = _tStoreWithCredential()
    dictCtx = _fdictContext(_fdictOwnersBoundTo(sSessionId), dictStore)
    conn = _fconnBrowser(sToken="not-a-real-credential")
    assert webSocketAuthorization.fiContainerSessionRejectionCode(
        conn, dictCtx, S_CONTAINER,
    ) == 4401


def test_bad_token_rejected_4401():
    dictStore, sSessionId, _sCredential = _tStoreWithCredential()
    dictCtx = _fdictContext(_fdictOwnersBoundTo(sSessionId), dictStore)
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
    dictStore, sSessionId, _sCredential = _tStoreWithCredential()
    dictCtx = _fdictContext(_fdictOwnersBoundTo(sSessionId), dictStore)
    conn = _fconnAgent(sToken="not-this-containers-agent-token")
    assert webSocketAuthorization.fbCheckAgentToken(
        conn, dictCtx["dictContainerOwners"], S_CONTAINER,
    ) is False


def test_present_but_invalid_agent_credential_never_falls_through():
    # A non-loopback connection is the agent lane and must be decided there.
    # Even when its sToken is a *valid browser credential*, a connection
    # with no loopback origin and no matching per-container agent token is
    # refused 4003 -- it must NEVER fall through to the browser credential
    # check and thereby skip the bound-lease gate.
    dictStore, sSessionId, sCredential = _tStoreWithCredential()
    dictCtx = _fdictContext(_fdictOwnersBoundTo(sSessionId), dictStore)
    conn = _FakeConnection(
        dictHeaders={},
        dictQuery={"sToken": sCredential, "sLeaseId": S_OWNING_LEASE},
    )
    assert webSocketAuthorization.fiContainerSessionRejectionCode(
        conn, dictCtx, S_CONTAINER,
    ) == 4003


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


# -- empty-shared-token fail-closed (M2) ----------------------------------


@pytest.mark.falsification
def test_empty_shared_token_fails_closed_4401():
    """An empty configured shared token must not clear the token gate.

    Kills: M2: drop bool(sSharedToken) guard in fbCheckSharedToken
    (line 56) -> 'return sPresented == sSharedToken'
    """
    # When the hub starts with an empty session token, the shared-token
    # gate must stay fail-closed: a loopback browser presenting the same
    # empty token ('' == '') must NOT clear the CSRF/trust check just
    # because both sides are empty. The bool(sSharedToken) guard is the
    # only fail-closed-when-unconfigured defense, so an owning-lease holder
    # presenting an empty token is rejected at the token gate (4401), never
    # admitted to the lease check.
    dictCtx = {
        "sSessionToken": "",
        "dictContainerOwners": _fdictOwnersWithOwner(),
    }
    conn = _fconnBrowser(sToken="")
    assert webSocketAuthorization.fbCheckSharedToken(conn, "") is False
    assert webSocketAuthorization.fiContainerSessionRejectionCode(
        conn, dictCtx, S_CONTAINER,
    ) == 4401


# -- agent lane is exempt from the per-container live budget (M9, M10) ----


@pytest.mark.asyncio
@pytest.mark.falsification
async def test_agent_lane_served_while_browser_session_live():
    """The agent lane is served even when a browser pipeline WS is live.

    Kills: M9: remove the 'bBrowser and' guard on the 4409 refusal in
    fnServeUnderLiveConnectionCounters
    """
    # The lease-exempt machine lane must keep working while a researcher's
    # browser session is live. An in-container agent (no loopback origin)
    # dialing the pipeline lane of a container whose owner already has one
    # live pipeline connection must be SERVED, never closed with 4409:
    # 'Claude, run unit tests on A09' cannot fail just because the
    # human's tab is open.
    dictContainerOwners = _fdictOwnersWithOwner()
    dictContainerOwners[S_CONTAINER].iLiveConnectionCount = 1
    dictContainerOwners[S_CONTAINER].iLivePipelineConnectionCount = 1
    conn = _fconnAgent()
    conn.accept = AsyncMock()
    conn.close = AsyncMock()
    fnServe = AsyncMock()
    await webSocketAuthorization.fnServeUnderLiveConnectionCounters(
        conn, dictContainerOwners, S_CONTAINER, fnServe,
        MagicMock(), MagicMock(), bExclusivePipelineLane=True,
    )
    fnServe.assert_awaited_once()
    conn.close.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.falsification
async def test_agent_lane_does_not_touch_per_container_counter():
    """The agent lane never touches the per-container live-connection counter.

    Kills: M10: per-container increment guard in
    fnServeUnderLiveConnectionCounters (line 145) 'if bBrowser:' ->
    'if True:'
    """
    # The per-container live-connection counter is a browser-lane budget.
    # The agent lane must never increment it (nor decrement it), otherwise
    # an agent connection leaks a phantom live connection that never clears
    # and the next real browser is refused 4409 forever. Assert the counter
    # is untouched and neither increment nor decrement was invoked.
    dictContainerOwners = _fdictOwnersWithOwner()
    assert dictContainerOwners[S_CONTAINER].iLiveConnectionCount == 0
    conn = _fconnAgent()
    conn.accept = AsyncMock()
    conn.close = AsyncMock()
    fnServe = AsyncMock()
    with patch.object(
        containerOwnership, "fnIncrementLiveConnection",
    ) as mockIncrement, patch.object(
        containerOwnership, "fnDecrementLiveConnection",
    ) as mockDecrement:
        await webSocketAuthorization.fnServeUnderLiveConnectionCounters(
            conn, dictContainerOwners, S_CONTAINER, fnServe,
            MagicMock(), MagicMock(), bExclusivePipelineLane=True,
        )
    mockIncrement.assert_not_called()
    mockDecrement.assert_not_called()
    assert dictContainerOwners[S_CONTAINER].iLiveConnectionCount == 0
    fnServe.assert_awaited_once()
