"""Terminal WebSocket route handler."""

__all__ = ["fnRegisterAll"]

from fastapi import WebSocket

from .. import pipelineServer as _pipelineServer
from ..pipelineServer import (
    fbValidateWebSocketOrigin,
    fnRejectTerminalStart,
    fnRunTerminalSession,
)
from ..terminalSession import TerminalSession


def _fnRegisterTerminalWs(app, dictCtx):
    """Register terminal WebSocket endpoint."""

    @app.websocket("/ws/terminal/{sContainerId}")
    async def fnTerminalWs(
        websocket: WebSocket, sContainerId: str
    ):
        if not fbValidateWebSocketOrigin(
            websocket, dictCtx["sSessionToken"],
        ):
            await websocket.close(code=4003)
            return
        sToken = websocket.query_params.get("sToken", "")
        if sToken != dictCtx["sSessionToken"]:
            await websocket.close(code=4401)
            return
        if sContainerId not in dictCtx["setAllowedContainers"]:
            await websocket.close(code=4403)
            return
        dictCtx["require"]()
        await _fnTrackAndServeTerminal(app, websocket, dictCtx, sContainerId)


async def _fnTrackAndServeTerminal(app, websocket, dictCtx, sContainerId):
    """Accept and serve a terminal session under the live-WebSocket count.

    The increment precedes ``accept`` and the decrement runs in a
    ``finally`` so the idle-shutdown watchdog can never retire a hub
    while a terminal tab is attached, even briefly mid-handshake.
    """
    _pipelineServer.fnIncrementWebSocketCount(app)
    try:
        await websocket.accept()
        await _fnStartAndRunTerminal(websocket, dictCtx, sContainerId)
    finally:
        _pipelineServer.fnDecrementWebSocketCount(app)


async def _fnStartAndRunTerminal(websocket, dictCtx, sContainerId):
    """Start the terminal session and run it to completion."""
    session = TerminalSession(
        dictCtx["docker"], sContainerId,
        sUser=dictCtx["containerUsers"].get(
            sContainerId, _pipelineServer.sTerminalUser
        ),
    )
    try:
        session.fnStart()
    except Exception as error:
        await fnRejectTerminalStart(websocket, error)
        return
    dictInteractive = (
        _pipelineServer.fdictInteractiveContextForContainer(sContainerId)
    )
    await fnRunTerminalSession(
        session, websocket, dictCtx["terminals"],
        dictInteractive=dictInteractive,
    )


def fnRegisterAll(app, dictCtx):
    """Register all terminal routes."""
    _fnRegisterTerminalWs(app, dictCtx)
