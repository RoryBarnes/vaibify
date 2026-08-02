"""Tests for vaibify.gui.routes.terminalRoutes — the withdrawn terminal.

The interactive terminal is withdrawn for the alpha: its containment
could not be proven (a descendant that calls ``setsid`` leaves the
recorded process group), so no release, hand-over, or shutdown could
honestly report the container quiet.

These tests previously drove the serving handler — origin refusal,
token refusal, session start, the live-connection counters. Every one
of those paths is gone, so asserting them would assert nothing. What
replaces them is the property the withdrawal exists for: the refusal is
the FIRST thing the handler does, so the route creates no ownership,
increments no counter, builds no session or journal record, and reveals
nothing about whether the named container exists.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from vaibify.gui import webSocketAuthorization


def _flistRegisterAndCaptureHandlers(dictCtx):
    """Register the terminal route on a stub app; return its handlers."""
    from vaibify.gui.routes import terminalRoutes

    app = MagicMock()
    listRegistered = []

    def fnCaptureRoute(sPath):
        def fnDecorator(fnHandler):
            listRegistered.append(fnHandler)
            return fnHandler
        return fnDecorator

    app.websocket = fnCaptureRoute
    terminalRoutes._fnRegisterTerminalWs(app, dictCtx)
    return listRegistered


def _fmockWebSocket():
    """Build a mock WebSocket that records accept/close."""
    mockWs = AsyncMock()
    mockWs.headers = {"origin": "http://localhost:8000"}
    mockWs.query_params = {"sToken": "any-token", "sLeaseId": "any-lease"}
    mockWs.close = AsyncMock()
    mockWs.accept = AsyncMock()
    return mockWs


class TestTerminalWsIsWithdrawn:
    """The handler accepts, closes with the withdrawal code, and stops."""

    @pytest.mark.asyncio
    async def test_refuses_with_the_withdrawal_code(self):
        listHandlers = _flistRegisterAndCaptureHandlers({})
        assert len(listHandlers) == 1, (
            "exactly one handler may answer the terminal path"
        )
        mockWs = _fmockWebSocket()

        await listHandlers[0](mockWs, "container-1")

        mockWs.accept.assert_awaited_once()
        mockWs.close.assert_awaited_once_with(
            code=webSocketAuthorization.I_REJECT_TERMINAL_DISABLED,
        )

    @pytest.mark.asyncio
    async def test_accepts_before_closing_so_the_browser_sees_the_code(self):
        """A close before accept reads to a browser as an opaque 1006.

        The researcher would be told "cannot reach server" for a feature
        that was deliberately withdrawn — the withdrawal has to be
        legible or it becomes a support question about the network.
        """
        listCalls = []
        mockWs = _fmockWebSocket()
        mockWs.accept = AsyncMock(
            side_effect=lambda *a, **k: listCalls.append("accept"),
        )
        mockWs.close = AsyncMock(
            side_effect=lambda *a, **k: listCalls.append("close"),
        )

        await _flistRegisterAndCaptureHandlers({})[0](mockWs, "container-1")

        assert listCalls == ["accept", "close"]

    @pytest.mark.asyncio
    async def test_touches_no_route_context_at_all(self):
        """Nothing in the context is read, so nothing can be disturbed.

        The pre-withdrawal handler read ``docker`` (a container-existence
        oracle), ``dictContainerOwners`` (whose gate REFRESHES the
        owner's liveness stamp), ``require``, ``containerUsers`` and
        ``terminals``. A context that raises on ANY access proves the
        refusal reads none of them: an unauthenticated dial-in cannot
        learn what exists nor perturb a session it has no standing in.
        """
        class _RaisingContext(dict):
            def __getitem__(self, sKey):
                raise AssertionError(
                    f"the withdrawn terminal route read dictCtx[{sKey!r}]"
                )

            def get(self, sKey, *tDefault):
                raise AssertionError(
                    f"the withdrawn terminal route read dictCtx.get({sKey!r})"
                )

        mockWs = _fmockWebSocket()
        listHandlers = _flistRegisterAndCaptureHandlers(_RaisingContext())

        await listHandlers[0](mockWs, "container-1")

        mockWs.close.assert_awaited_once_with(
            code=webSocketAuthorization.I_REJECT_TERMINAL_DISABLED,
        )

    @pytest.mark.asyncio
    async def test_every_container_id_gets_the_same_answer(self):
        """A real id, a fabricated id, and an empty id are indistinguishable."""
        listCodes = []
        for sContainerId in ("container-1", "no-such-container", ""):
            mockWs = _fmockWebSocket()
            await _flistRegisterAndCaptureHandlers({})[0](
                mockWs, sContainerId,
            )
            listCodes.append(mockWs.close.await_args.kwargs["code"])
        iWithdrawn = webSocketAuthorization.I_REJECT_TERMINAL_DISABLED
        assert listCodes == [iWithdrawn] * 3


class TestWithdrawalCodeIsNotAnAuthorizationCode:
    """The withdrawal must be distinguishable from every refusal."""

    def test_code_is_distinct_from_every_authorization_refusal(self):
        iWithdrawn = webSocketAuthorization.I_REJECT_TERMINAL_DISABLED
        tAuthorizationCodes = (
            webSocketAuthorization.I_REJECT_AUTHORIZED,
            webSocketAuthorization.I_REJECT_BAD_ORIGIN,
            webSocketAuthorization.I_REJECT_BAD_TOKEN,
            webSocketAuthorization.I_REJECT_FOREIGN_LEASE,
            webSocketAuthorization.I_REJECT_DUPLICATE_SESSION,
        )
        assert iWithdrawn not in tAuthorizationCodes, (
            "a client that cannot tell a withdrawn feature from a "
            "rejected credential would tell the researcher to re-claim "
            "a container that is already theirs"
        )
