"""Terminal WebSocket route handler."""

__all__ = ["fnRegisterAll"]

import asyncio

from fastapi import WebSocket

from .. import pipelineServer as _pipelineServer
from ..attributionLog import (
    S_TERMINAL_CHANNEL,
    S_TERMINAL_CLOSED_DETAIL,
    S_TERMINAL_OPENED_DETAIL,
)
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
        await _fnStartAndRunTerminal(
            app, websocket, dictCtx, sContainerId, sName,
        )

    await fnServeUnderLiveConnectionCounters(
        websocket, dictCtx.get("dictContainerOwners", {}), sName,
        fnServe, lambda: _pipelineServer.fnIncrementWebSocketCount(app),
        lambda: _pipelineServer.fnDecrementWebSocketCount(app),
        dictSessionSockets=dictCtx.get("dictSessionSockets"),
        dictBrowserSessions=dictCtx.get("dictBrowserSessions"),
    )


async def _fnStartAndRunTerminal(app, websocket, dictCtx, sContainerId, sName):
    """Start the terminal session and run it to completion.

    The session is containment-wired (slice 3d): its start is the
    journaled create → journal → start split, so the exec becomes a
    durable ``TerminalExecutionRecord`` that release, reaping, and
    shutdown terminate-and-prove. The start runs in a worker thread —
    group discovery polls the container — so the hub event loop is
    never blocked behind a slow probe.
    """
    recordOwner = (dictCtx.get("dictContainerOwners") or {}).get(sName)
    session = TerminalSession(
        dictCtx["docker"], sContainerId,
        sUser=dictCtx["containerUsers"].get(
            sContainerId, dictCtx.get("sTerminalUser")
        ),
        dictContainment={
            "appState": app.state,
            "sContainerName": sName,
            "iOwnerGeneration": (
                recordOwner.iOwnerGeneration if recordOwner is not None
                else 0
            ),
        },
    )
    try:
        await asyncio.to_thread(session.fnStart)
    except Exception as error:
        await fnRejectTerminalStart(websocket, error)
        return
    dictInteractive = (
        _pipelineServer.fdictInteractiveContextForContainer(sContainerId)
    )
    _fnRecordTerminalAttribution(
        dictCtx, sContainerId, S_TERMINAL_OPENED_DETAIL,
    )
    try:
        await fnRunTerminalSession(
            session, websocket, dictCtx["terminals"],
            dictInteractive=dictInteractive,
        )
    finally:
        _fnRecordTerminalAttribution(
            dictCtx, sContainerId, S_TERMINAL_CLOSED_DETAIL,
        )


def _fnRecordTerminalAttribution(dictCtx, sContainerId, sDetail):
    """Record a terminal open/close as a Supervised-mode event.

    The terminal is a recorded CHANNEL, not (yet) recorded content —
    a change made while a terminal session is open attributes to the
    session, but its keystrokes are not captured. The Supervised
    docs state this granularity.

    The channel and the two detail strings come from
    :mod:`vaibify.gui.attributionLog`, which pairs them into the open
    interval that makes a long session attributive. A literal here
    that drifted from the judge's spelling would silently reduce the
    interval back to two unpaired instants.
    """
    from ..routeContext import fnRecordAttributionEvent
    dictWorkflow = (
        dictCtx.get("workflows") or {}
    ).get(sContainerId) or {}
    fnRecordAttributionEvent(
        dictCtx, sContainerId, dictWorkflow, S_TERMINAL_CHANNEL,
        sDetail,
    )


def fnRegisterAll(app, dictCtx):
    """Register all terminal routes."""
    _fnRegisterTerminalWs(app, dictCtx)
