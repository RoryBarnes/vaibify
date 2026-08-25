"""Hub shutdown must not skip a terminal's containment teardown.

A researcher pressed Ctrl-C on the backend with a terminal tab open
and got a full ``Exception in ASGI application`` traceback ending in
``CancelledError`` raised at ``fnRunTerminalSession``'s drain await.

Uvicorn cancels every still-running task once its graceful window
expires, and a terminal tab is exactly such a task. The natural guess
-- that a cancel skips a ``finally``'s await outright -- is WRONG, and
the first test below is the standing demonstration: with one cancel
the awaited cleanup completes and the ``CancelledError`` is re-raised
afterwards. What actually fails is narrower: a cancel that arrives
while the drain's blocking Docker probes are still in flight makes
that await raise, and then ``fnClose`` and the session-registry
removal never run at all.

The drain runs in a worker thread, which cannot be interrupted, so
abandoning the await never stopped it -- it only stopped the hub
learning whether it PROVED the process group empty, which is the one
fact the record's honesty rests on.

Scope, stated rather than implied: this covers the socket-close
teardown only. The authoritative settling path at shutdown is the
lifespan hook's ``fdictDrainAllTerminalRecords``, which uvicorn runs
AFTER cancelling tasks -- and which it SKIPS entirely on
``force_exit`` (a second Ctrl-C). Nothing here makes a double Ctrl-C
safe; an unproven record then retains-and-quarantines, which is the
designed fail-safe and still routes to ``vaibify reconcile``.

Kills (confirmed, not assumed): replacing the shielded teardown with
a plain ``await asyncio.to_thread(...)`` -> the slow-drain test fails
reporting that the session was never closed.
"""

import asyncio

import pytest


pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------
# The async semantics this fix rests on, pinned as a demonstration.
# ---------------------------------------------------------------------


async def test_one_cancel_does_not_skip_an_await_in_a_finally():
    """The natural guess is wrong, and the code comment says so.

    If this ever starts failing, the reasoning in
    ``_fnDrainAndCloseTerminalSession``'s docstring needs rereading --
    it explains why a plain await is *nearly* sufficient, and this is
    the evidence for "nearly".
    """
    listRan = []

    async def fnSession():
        try:
            await asyncio.sleep(3600)
        finally:
            await asyncio.to_thread(listRan.append, "cleanup")

    task = asyncio.create_task(fnSession())
    await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert listRan == ["cleanup"]


# ---------------------------------------------------------------------
# The real teardown.
# ---------------------------------------------------------------------


class _FakeSession:
    """A terminal session that records whether it was closed."""

    def __init__(self):
        self.sSessionId = "term-1"
        self.bClosed = False
        self.recordContainment = None

    def fnClose(self):
        self.bClosed = True


def _fnPatchDrain(monkeypatch, fSleepSeconds, listCalls):
    """Replace the containment drain with a timed stand-in."""
    from vaibify.gui import terminalContainment
    import time

    def fdictDrainSessionRecord(session):
        if fSleepSeconds:
            time.sleep(fSleepSeconds)
        listCalls.append(session.sSessionId)
        return {"bProvenEmpty": True}

    monkeypatch.setattr(
        terminalContainment, "fdictDrainSessionRecord",
        fdictDrainSessionRecord,
    )


async def _fnRunTeardownUnderCancel(
    monkeypatch, fSleepSeconds, iCancels,
):
    """Cancel the teardown mid-flight and report what survived."""
    from vaibify.gui import pipelineServer

    listCalls = []
    _fnPatchDrain(monkeypatch, fSleepSeconds, listCalls)
    session = _FakeSession()
    dictSessions = {session.sSessionId: session}

    task = asyncio.create_task(
        pipelineServer._fnDrainAndCloseTerminalSession(
            session, session.sSessionId, dictSessions,
        ),
    )
    await asyncio.sleep(0.05)
    for _ in range(iCancels):
        task.cancel(
            msg="Task cancelled, timeout graceful shutdown exceeded",
        )
        await asyncio.sleep(0)
    try:
        await task
    except asyncio.CancelledError:
        pass
    return session, dictSessions, listCalls


async def test_a_cancel_during_a_slow_drain_still_closes_the_session(
    monkeypatch,
):
    """The bug, in the shape the researcher hit it.

    The drain outlasts the cancel, which is what a real Docker probe
    does at shutdown. Before the fix the await raised and the session
    was left open and still in the registry.
    """
    session, dictSessions, listCalls = await _fnRunTeardownUnderCancel(
        monkeypatch, fSleepSeconds=0.4, iCancels=1,
    )
    assert listCalls == ["term-1"], (
        "the containment drain never completed, so the hub never "
        "learned whether the process group was proven empty"
    )
    assert session.bClosed is True, (
        "the terminal session was never closed: a cancel landing "
        "mid-drain skipped the rest of the teardown"
    )
    assert dictSessions == {}, (
        f"the session stayed in the live registry: {dictSessions}"
    )


async def test_a_fast_drain_is_unaffected(monkeypatch):
    """The ordinary close path must not regress."""
    session, dictSessions, listCalls = await _fnRunTeardownUnderCancel(
        monkeypatch, fSleepSeconds=0, iCancels=1,
    )
    assert listCalls == ["term-1"]
    assert session.bClosed is True
    assert dictSessions == {}


async def test_a_repeated_cancel_still_closes_the_session(monkeypatch):
    """Two cancels end the WAIT, never the teardown.

    The drain's thread cannot be interrupted and the lifespan hook is
    the backstop for the record, but ``fnClose`` and the registry
    removal are synchronous and must run on every path.
    """
    session, dictSessions, _listCalls = await _fnRunTeardownUnderCancel(
        monkeypatch, fSleepSeconds=0.4, iCancels=3,
    )
    assert session.bClosed is True
    assert dictSessions == {}


async def test_shutdown_cancellation_does_not_escape_the_handler(
    monkeypatch,
):
    """The traceback the researcher saw must not return.

    ``fnRunTerminalSession`` swallows a shutdown cancellation AFTER
    the teardown, matching how serverLifespan's sweepers treat it.
    Asserting the teardown ran too, because a handler that returned
    quietly without draining would pass a "no traceback" check while
    being strictly worse than the traceback.
    """
    from unittest.mock import AsyncMock
    from vaibify.gui import pipelineServer

    listCalls = []
    _fnPatchDrain(monkeypatch, 0, listCalls)
    session = _FakeSession()
    dictSessions = {}

    websocketFake = AsyncMock()

    async def fnHang(*tArgs, **dictKwargs):
        await asyncio.sleep(3600)

    monkeypatch.setattr(
        pipelineServer, "fnTerminalInputLoop", fnHang,
    )
    monkeypatch.setattr(
        pipelineServer, "fnTerminalReadLoop", fnHang,
    )

    task = asyncio.create_task(
        pipelineServer.fnRunTerminalSession(
            session, websocketFake, dictSessions,
        ),
    )
    await asyncio.sleep(0.05)
    task.cancel(
        msg="Task cancelled, timeout graceful shutdown exceeded",
    )
    try:
        await task
    except asyncio.CancelledError:
        pytest.fail(
            "the shutdown cancellation escaped fnRunTerminalSession — "
            "uvicorn logs that as 'Exception in ASGI application', "
            "which reads as a crash on every clean shutdown with a "
            "terminal open"
        )
    assert listCalls == ["term-1"], (
        "the handler returned quietly without draining the "
        "containment record, which is worse than the traceback"
    )
    assert session.bClosed is True
