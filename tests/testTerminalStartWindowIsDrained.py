"""A terminal whose socket closes while it is starting is still drained.

The session was registered, ``connected`` and the banner were sent, and
the read task was created, all BEFORE the ``try`` whose ``finally``
drains the containment record. A socket that closed in that window
raised out of ``send_json`` with the record registered and the exec
running, and nothing ended it: that is one way a shell outlives the
tab that opened it. The route had the same gap between ``fnStart``
and the hand-off. Both windows now sit inside the drain's reach, and
these tests close each one with a real runner, a real drain-and-close,
and only the containment prover replaced.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import WebSocketDisconnect

from vaibify.gui import pipelineServer, terminalContainment
from vaibify.gui.routes import terminalRoutes

pytestmark = pytest.mark.asyncio


class _SessionFake:
    """A started session that records whether it was drained and closed."""

    sSessionId = "term-start-window"

    def __init__(self):
        self.bClosed = False

    def fnStart(self):
        return None

    def fnClose(self):
        self.bClosed = True


@pytest.fixture
def listDrained(monkeypatch):
    listRecorded = []
    monkeypatch.setattr(
        terminalContainment, "fdictDrainSessionRecord",
        lambda session: listRecorded.append(session.sSessionId) or {})
    return listRecorded


async def test_a_socket_that_closes_before_connected_lands_is_still_drained(
    listDrained,
):
    session = _SessionFake()
    websocketFake = AsyncMock()
    websocketFake.send_json.side_effect = WebSocketDisconnect()
    dictSessions = {}
    await pipelineServer.fnRunTerminalSession(session, websocketFake, dictSessions)
    assert listDrained == [session.sSessionId], "the record was never drained"
    assert session.bClosed
    assert dictSessions == {}


async def test_a_socket_that_dies_during_the_banner_is_drained_and_the_error_kept(
    listDrained,
):
    session = _SessionFake()
    websocketFake = AsyncMock()
    websocketFake.send_bytes.side_effect = RuntimeError("socket already closed")
    dictSessions = {}
    with pytest.raises(RuntimeError):
        await pipelineServer.fnRunTerminalSession(
            session, websocketFake, dictSessions, sIntroductionBanner="hello")
    assert listDrained == [session.sSessionId]
    assert session.bClosed
    assert dictSessions == {}


async def test_a_failure_between_start_and_hand_off_drains_the_started_session(
    monkeypatch, listDrained,
):
    """The route's window: the exec is running, the runner never got it."""
    session = _SessionFake()
    monkeypatch.setattr(
        terminalRoutes, "TerminalSession",
        lambda *tArgs, **dictKeywords: session)
    monkeypatch.setattr(
        terminalRoutes, "_fsBannerForThisShell",
        lambda *tArgs: (_ for _ in ()).throw(RuntimeError("banner lookup broke")))
    dictCtx = {
        "docker": object(), "containerUsers": {}, "terminals": {},
        "dictContainerOwners": {},
    }
    websocketFake = AsyncMock()
    websocketFake.headers = {}
    with pytest.raises(RuntimeError, match="banner lookup broke"):
        await terminalRoutes._fnStartAndRunTerminal(
            MagicMock(), websocketFake, dictCtx, "c" * 64, "project")
    assert listDrained == [session.sSessionId]
    assert session.bClosed
    assert dictCtx["terminals"] == {}


async def test_a_start_that_fails_is_refused_and_never_drained(
    monkeypatch, listDrained,
):
    """Nothing started, so there is nothing to drain; the refusal stands."""
    class _SessionRefusing(_SessionFake):
        def fnStart(self):
            raise RuntimeError("exec create refused")

    monkeypatch.setattr(
        terminalRoutes, "TerminalSession",
        lambda *tArgs, **dictKeywords: _SessionRefusing())
    listRefused = []

    async def fnRecordRefusal(websocket, error):
        listRefused.append(str(error))

    monkeypatch.setattr(terminalRoutes, "fnRejectTerminalStart", fnRecordRefusal)
    dictCtx = {"docker": object(), "containerUsers": {}, "terminals": {},
               "dictContainerOwners": {}}
    await terminalRoutes._fnStartAndRunTerminal(
        MagicMock(), AsyncMock(), dictCtx, "c" * 64, "project")
    assert listRefused == ["exec create refused"]
    assert listDrained == []
