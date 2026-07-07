"""Terminal WebSocket route handler."""

__all__ = ["fnRegisterAll"]

from fastapi import WebSocket

from .. import pipelineServer as _pipelineServer
from ..pipelineServer import (
    fnRejectTerminalStart,
    fnRunTerminalSession,
    fsContainerNameForId,
)
from ..webSocketAuthorization import (
    fiContainerSessionRejectionCode,
    fnCloseWithCode,
    fnServeUnderLiveConnectionCounters,
)
from ..terminalSession import TerminalSession


def _fnRegisterTerminalWs(app, dictCtx):
    """Register terminal WebSocket endpoint."""

    @app.websocket("/ws/terminal/{sContainerId}")
    async def fnTerminalWs(
        websocket: WebSocket, sContainerId: str
    ):
        sName = fsContainerNameForId(
            dictCtx.get("docker"), sContainerId,
        )
        iRejectCode = fiContainerSessionRejectionCode(
            websocket, dictCtx, sName,
        )
        if iRejectCode:
            await fnCloseWithCode(websocket, iRejectCode)
            return
        dictCtx["require"]()
        await _fnTrackAndServeTerminal(
            app, websocket, dictCtx, sContainerId, sName,
        )


async def _fnTrackAndServeTerminal(
    app, websocket, dictCtx, sContainerId, sName,
):
    """Accept and serve a terminal session under the live-connection counters.

    Delegates to the shared counter wrapper so the per-container
    one-session budget (and its 4409 duplicate-tab refusal) plus the
    app-global live-WebSocket count are driven identically to the
    pipeline route; the idle-shutdown watchdog can never retire a hub
    while a terminal tab is attached, even briefly mid-handshake.
    """

    async def fnServe():
        await websocket.accept()
        await _fnStartAndRunTerminal(websocket, dictCtx, sContainerId)

    await fnServeUnderLiveConnectionCounters(
        websocket, dictCtx.get("dictContainerOwners", {}), sName,
        fnServe, lambda: _pipelineServer.fnIncrementWebSocketCount(app),
        lambda: _pipelineServer.fnDecrementWebSocketCount(app),
    )


async def _fnStartAndRunTerminal(websocket, dictCtx, sContainerId):
    """Start the terminal session and run it to completion."""
    session = TerminalSession(
        dictCtx["docker"], sContainerId,
        sUser=dictCtx["containerUsers"].get(
            sContainerId, dictCtx.get("sTerminalUser")
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
