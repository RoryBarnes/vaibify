"""Terminal WebSocket route handler."""

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
        if not fbValidateWebSocketOrigin(websocket):
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
        await websocket.accept()
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
        await fnRunTerminalSession(
            session, websocket, dictCtx["terminals"]
        )


def fnRegisterAll(app, dictCtx):
    """Register all terminal routes."""
    _fnRegisterTerminalWs(app, dictCtx)
