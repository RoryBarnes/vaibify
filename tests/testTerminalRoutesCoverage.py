"""Tests for vaibify.gui.routes.terminalRoutes — covers lines 23-46."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _fdictBuildContext(sSessionToken="valid-token"):
    """Build a minimal dictCtx for terminal route tests."""
    return {
        "sSessionToken": sSessionToken,
        "setAllowedContainers": {"container-1"},
        "require": MagicMock(),
        "docker": MagicMock(),
        "containerUsers": {},
        "terminals": {},
    }


def _fmockWebSocket(
    sOrigin="http://localhost:8000",
    sToken="valid-token",
):
    """Build a mock WebSocket with configurable origin and token."""
    mockWs = AsyncMock()
    mockWs.headers = {"origin": sOrigin}
    mockWs.query_params = {"sToken": sToken}
    mockWs.close = AsyncMock()
    mockWs.accept = AsyncMock()
    return mockWs


class TestTerminalWsOriginRejection:
    """Line 23-25: reject when origin validation fails."""

    @pytest.mark.asyncio
    async def test_reject_invalid_origin(self):
        from vaibify.gui.routes import terminalRoutes

        dictCtx = _fdictBuildContext()
        app = MagicMock()

        listRegistered = []

        def fnCaptureRoute(sPath):
            def fnDecorator(fnHandler):
                listRegistered.append(fnHandler)
                return fnHandler
            return fnDecorator

        app.websocket = fnCaptureRoute
        terminalRoutes._fnRegisterTerminalWs(app, dictCtx)
        fnHandler = listRegistered[0]

        mockWs = _fmockWebSocket(sOrigin="http://evil.com")

        with patch.object(
            terminalRoutes, "fbValidateWebSocketOrigin",
            return_value=False,
        ):
            await fnHandler(mockWs, "container-1")

        mockWs.close.assert_awaited_once_with(code=4003)


class TestTerminalWsTokenRejection:
    """Line 26-29: reject when session token is wrong."""

    @pytest.mark.asyncio
    async def test_reject_bad_token(self):
        from vaibify.gui.routes import terminalRoutes

        dictCtx = _fdictBuildContext(sSessionToken="correct")
        app = MagicMock()
        listRegistered = []

        def fnCaptureRoute(sPath):
            def fnDecorator(fnHandler):
                listRegistered.append(fnHandler)
                return fnHandler
            return fnDecorator

        app.websocket = fnCaptureRoute
        terminalRoutes._fnRegisterTerminalWs(app, dictCtx)
        fnHandler = listRegistered[0]

        mockWs = _fmockWebSocket(sToken="wrong-token")
        with patch.object(
            terminalRoutes, "fbValidateWebSocketOrigin",
            return_value=True,
        ):
            await fnHandler(mockWs, "container-1")

        mockWs.close.assert_awaited_once_with(code=4401)


class TestTerminalWsContainerRejection:
    """Line 30-32: reject when container not in allowed set."""

    @pytest.mark.asyncio
    async def test_reject_unknown_container(self):
        from vaibify.gui.routes import terminalRoutes

        dictCtx = _fdictBuildContext()
        app = MagicMock()
        listRegistered = []

        def fnCaptureRoute(sPath):
            def fnDecorator(fnHandler):
                listRegistered.append(fnHandler)
                return fnHandler
            return fnDecorator

        app.websocket = fnCaptureRoute
        terminalRoutes._fnRegisterTerminalWs(app, dictCtx)
        fnHandler = listRegistered[0]

        mockWs = _fmockWebSocket()
        with patch.object(
            terminalRoutes, "fbValidateWebSocketOrigin",
            return_value=True,
        ):
            await fnHandler(mockWs, "not-allowed-container")

        mockWs.close.assert_awaited_once_with(code=4403)


class TestTerminalWsStartFailure:
    """Lines 33-45: accept, create session, handle fnStart failure."""

    @pytest.mark.asyncio
    async def test_session_start_exception(self):
        from vaibify.gui.routes import terminalRoutes

        dictCtx = _fdictBuildContext()
        app = MagicMock()
        listRegistered = []

        def fnCaptureRoute(sPath):
            def fnDecorator(fnHandler):
                listRegistered.append(fnHandler)
                return fnHandler
            return fnDecorator

        app.websocket = fnCaptureRoute
        terminalRoutes._fnRegisterTerminalWs(app, dictCtx)
        fnHandler = listRegistered[0]

        mockWs = _fmockWebSocket()
        mockSession = MagicMock()
        mockSession.fnStart.side_effect = RuntimeError("pty failed")

        with patch.object(
            terminalRoutes, "fbValidateWebSocketOrigin",
            return_value=True,
        ), patch.object(
            terminalRoutes, "TerminalSession",
            return_value=mockSession,
        ), patch.object(
            terminalRoutes, "fnRejectTerminalStart",
            new_callable=AsyncMock,
        ) as mockReject:
            await fnHandler(mockWs, "container-1")

        mockWs.accept.assert_awaited_once()
        mockReject.assert_awaited_once()
        sErrorArg = mockReject.call_args[0][1]
        assert "pty failed" in str(sErrorArg)


class TestTerminalWsSuccessfulSession:
    """Lines 33-48: full happy path through fnRunTerminalSession."""

    @pytest.mark.asyncio
    async def test_successful_session(self):
        from vaibify.gui.routes import terminalRoutes

        dictCtx = _fdictBuildContext()
        dictCtx["containerUsers"] = {"container-1": "astro"}
        app = MagicMock()
        listRegistered = []

        def fnCaptureRoute(sPath):
            def fnDecorator(fnHandler):
                listRegistered.append(fnHandler)
                return fnHandler
            return fnDecorator

        app.websocket = fnCaptureRoute
        terminalRoutes._fnRegisterTerminalWs(app, dictCtx)
        fnHandler = listRegistered[0]

        mockWs = _fmockWebSocket()
        mockSession = MagicMock()

        with patch.object(
            terminalRoutes, "fbValidateWebSocketOrigin",
            return_value=True,
        ), patch.object(
            terminalRoutes, "TerminalSession",
            return_value=mockSession,
        ) as mockSessionCls, patch.object(
            terminalRoutes, "fnRunTerminalSession",
            new_callable=AsyncMock,
        ) as mockRun:
            await fnHandler(mockWs, "container-1")

        mockSessionCls.assert_called_once_with(
            dictCtx["docker"], "container-1", sUser="astro",
        )
        mockSession.fnStart.assert_called_once()
        mockRun.assert_awaited_once_with(
            mockSession, mockWs, dictCtx["terminals"],
        )


class TestTerminalWsDefaultUser:
    """Lines 37-39: fall back to sTerminalUser when no entry."""

    @pytest.mark.asyncio
    async def test_default_terminal_user(self):
        from vaibify.gui.routes import terminalRoutes

        dictCtx = _fdictBuildContext()
        app = MagicMock()
        listRegistered = []

        def fnCaptureRoute(sPath):
            def fnDecorator(fnHandler):
                listRegistered.append(fnHandler)
                return fnHandler
            return fnDecorator

        app.websocket = fnCaptureRoute
        terminalRoutes._fnRegisterTerminalWs(app, dictCtx)
        fnHandler = listRegistered[0]

        mockWs = _fmockWebSocket()
        mockSession = MagicMock()

        with patch.object(
            terminalRoutes, "fbValidateWebSocketOrigin",
            return_value=True,
        ), patch.object(
            terminalRoutes, "TerminalSession",
            return_value=mockSession,
        ) as mockSessionCls, patch.object(
            terminalRoutes, "fnRunTerminalSession",
            new_callable=AsyncMock,
        ), patch.object(
            terminalRoutes._pipelineServer,
            "sTerminalUser", "root",
        ):
            await fnHandler(mockWs, "container-1")

        mockSessionCls.assert_called_once_with(
            dictCtx["docker"], "container-1", sUser="root",
        )
