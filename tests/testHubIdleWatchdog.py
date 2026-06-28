"""Tests for the idle self-shutdown watchdog and WebSocket presence counter.

Covers the L1 "Idle self-shutdown" behavior in
``vaibify.gui.pipelineServer``: the activity middleware, the
live-WebSocket counter, the busy-veto, the self-exit decision, and the
watchdog loop that self-SIGTERMs only when genuinely abandoned.
"""

import asyncio
import signal
import time
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from vaibify.gui import pipelineServer


# ---------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------

def _fappBuildFakeApp(**kwargs):
    """Return a stand-in app whose state carries the given attributes."""
    dictState = {
        "iActiveWebSockets": 0,
        "fLastActivityMonotonic": time.monotonic(),
        "dictContainerLocks": {},
        "listLifespanStartup": [],
        "listLifespanShutdown": [],
    }
    dictState.update(kwargs)
    return SimpleNamespace(state=SimpleNamespace(**dictState))


class _FakeDocker:
    """Minimal docker stand-in mapping container names to ids."""

    def __init__(self, dictNameToId, bRaise=False):
        self.dictNameToId = dict(dictNameToId)
        self.bRaise = bRaise

    def flistGetRunningContainers(self):
        if self.bRaise:
            raise RuntimeError("docker unreachable")
        return [
            {"sName": sName, "sContainerId": sId}
            for sName, sId in self.dictNameToId.items()
        ]


# ---------------------------------------------------------------
# WebSocket presence counter
# ---------------------------------------------------------------

def test_increment_and_decrement_websocket_count():
    """The counter rises on increment and floors at zero on decrement."""
    app = _fappBuildFakeApp()
    pipelineServer.fnIncrementWebSocketCount(app)
    pipelineServer.fnIncrementWebSocketCount(app)
    assert app.state.iActiveWebSockets == 2
    pipelineServer.fnDecrementWebSocketCount(app)
    assert app.state.iActiveWebSockets == 1
    pipelineServer.fnDecrementWebSocketCount(app)
    pipelineServer.fnDecrementWebSocketCount(app)
    assert app.state.iActiveWebSockets == 0


def test_decrement_defaults_to_zero_when_unset():
    """A missing counter attribute decrements to a floored zero."""
    app = SimpleNamespace(state=SimpleNamespace())
    pipelineServer.fnDecrementWebSocketCount(app)
    assert app.state.iActiveWebSockets == 0


# ---------------------------------------------------------------
# Busy-veto
# ---------------------------------------------------------------

def test_no_held_locks_is_not_busy():
    """With no held container locks the hub is never considered busy."""
    app = _fappBuildFakeApp(dictContainerLocks={})
    assert pipelineServer._fbAnyHeldContainerBusy(app, {"docker": None}) is False


def test_held_container_running_is_busy():
    """A held container with a running pipeline vetoes self-exit."""
    app = _fappBuildFakeApp(dictContainerLocks={"projectA": object()})
    dictCtx = {"docker": _FakeDocker({"projectA": "id-a"})}
    with patch(
        "vaibify.gui.fileStatusManager._fbPipelineIsRunning",
        return_value=True,
    ):
        assert pipelineServer._fbAnyHeldContainerBusy(app, dictCtx) is True


def test_held_container_idle_is_not_busy():
    """A held container with no running pipeline does not veto self-exit."""
    app = _fappBuildFakeApp(dictContainerLocks={"projectA": object()})
    dictCtx = {"docker": _FakeDocker({"projectA": "id-a"})}
    with patch(
        "vaibify.gui.fileStatusManager._fbPipelineIsRunning",
        return_value=False,
    ):
        assert pipelineServer._fbAnyHeldContainerBusy(app, dictCtx) is False


def test_docker_error_treated_as_busy():
    """A Docker failure while probing held containers fails safe to busy."""
    app = _fappBuildFakeApp(dictContainerLocks={"projectA": object()})
    dictCtx = {"docker": _FakeDocker({}, bRaise=True)}
    assert pipelineServer._fbAnyHeldContainerBusy(app, dictCtx) is True


def test_none_docker_with_held_locks_is_busy():
    """A held lock with no Docker connection fails safe to busy."""
    app = _fappBuildFakeApp(dictContainerLocks={"projectA": object()})
    assert pipelineServer._fbAnyHeldContainerBusy(app, {"docker": None}) is True


# ---------------------------------------------------------------
# Self-exit decision
# ---------------------------------------------------------------

def test_connected_websocket_prevents_self_exit():
    """A live WebSocket forbids self-exit regardless of idleness."""
    app = _fappBuildFakeApp(
        iActiveWebSockets=1,
        fLastActivityMonotonic=time.monotonic() - 10_000,
    )
    assert pipelineServer._fbHubShouldSelfExit(app, {"docker": None}, 1.0) is False


def test_busy_container_prevents_self_exit():
    """A mid-run held container forbids self-exit even when idle."""
    app = _fappBuildFakeApp(
        dictContainerLocks={"projectA": object()},
        fLastActivityMonotonic=time.monotonic() - 10_000,
    )
    dictCtx = {"docker": _FakeDocker({"projectA": "id-a"})}
    with patch(
        "vaibify.gui.fileStatusManager._fbPipelineIsRunning",
        return_value=True,
    ):
        assert pipelineServer._fbHubShouldSelfExit(app, dictCtx, 1.0) is False


def test_idle_and_free_self_exits():
    """No tab, nothing running, idle past the timeout triggers self-exit."""
    app = _fappBuildFakeApp(
        fLastActivityMonotonic=time.monotonic() - 100.0,
    )
    assert pipelineServer._fbHubShouldSelfExit(app, {"docker": None}, 10.0) is True


def test_within_timeout_does_not_self_exit():
    """Recent activity inside the timeout window keeps the hub alive."""
    app = _fappBuildFakeApp(fLastActivityMonotonic=time.monotonic())
    assert pipelineServer._fbHubShouldSelfExit(
        app, {"docker": None}, 10_000.0,
    ) is False


# ---------------------------------------------------------------
# Watchdog loop
# ---------------------------------------------------------------

def test_watchdog_self_sigterms_once_then_returns():
    """An idle, free hub sends exactly one SIGTERM to itself and returns."""
    app = _fappBuildFakeApp(
        fLastActivityMonotonic=time.monotonic() - 100.0,
    )
    listKills = []

    def _fnRecordKill(iPid, iSignal):
        listKills.append((iPid, iSignal))

    async def fnDrive():
        with patch.object(pipelineServer.os, "kill", _fnRecordKill):
            await asyncio.wait_for(
                pipelineServer._fnIdleShutdownWatchdogLoop(
                    app, {"docker": None}, 0.01, 0.0,
                ),
                timeout=1.0,
            )

    asyncio.run(fnDrive())
    import os as _os
    assert listKills == [(_os.getpid(), signal.SIGTERM)]


def test_watchdog_rechecks_when_run_starts_between_ticks():
    """A hub busy on the first tick exits on a later tick once it frees up."""
    app = _fappBuildFakeApp()
    listDecisions = [False, True]
    listKills = []

    def _fbDecide(appArg, dictCtxArg, fTimeoutArg):
        return listDecisions.pop(0) if listDecisions else True

    async def fnDrive():
        with patch.object(
            pipelineServer, "_fbHubShouldSelfExit", _fbDecide,
        ), patch.object(
            pipelineServer.os, "kill",
            lambda iPid, iSignal: listKills.append(iPid),
        ):
            await asyncio.wait_for(
                pipelineServer._fnIdleShutdownWatchdogLoop(
                    app, {"docker": None}, 0.01, 0.0,
                ),
                timeout=1.0,
            )

    asyncio.run(fnDrive())
    assert len(listKills) == 1
    assert listDecisions == []


def test_watchdog_cancels_cleanly_at_shutdown():
    """Registering the watchdog yields a task cancelled at lifespan exit."""
    app = _fappBuildFakeApp()
    pipelineServer._fnRegisterIdleShutdownWatchdog(
        app, {"docker": None}, fInterval=10.0,
    )

    async def fnDrive():
        for fnStartup in app.state.listLifespanStartup:
            await fnStartup(app)
        for fnShutdown in app.state.listLifespanShutdown:
            await fnShutdown(app)
        return app.state.taskIdleWatchdog

    taskWatchdog = asyncio.run(fnDrive())
    assert taskWatchdog.done()


def test_watchdog_prunes_dead_spawn_children_each_tick():
    """Each watchdog tick prunes already-exited spawned children."""
    mockDead = MagicMock()
    mockDead.poll.return_value = 0
    mockAlive = MagicMock()
    mockAlive.poll.return_value = None
    app = _fappBuildFakeApp(
        listSpawnedChildren=[mockDead, mockAlive],
        fLastActivityMonotonic=time.monotonic() - 100.0,
    )

    async def fnDrive():
        with patch.object(pipelineServer.os, "kill", lambda iPid, iSignal: None):
            await asyncio.wait_for(
                pipelineServer._fnIdleShutdownWatchdogLoop(
                    app, {"docker": None}, 0.01, 0.0,
                ),
                timeout=1.0,
            )

    asyncio.run(fnDrive())
    assert app.state.listSpawnedChildren == [mockAlive]


# ---------------------------------------------------------------
# Activity middleware
# ---------------------------------------------------------------

def test_activity_middleware_advances_timestamp():
    """Each HTTP request refreshes the last-activity monotonic clock."""
    from fastapi import FastAPI
    from starlette.testclient import TestClient

    app = FastAPI()
    app.add_middleware(pipelineServer.ActivityTrackingMiddleware)

    @app.get("/ping")
    async def fnPing():
        return {"ok": True}

    app.state.fLastActivityMonotonic = 0.0
    client = TestClient(app)
    response = client.get("/ping")
    assert response.status_code == 200
    assert app.state.fLastActivityMonotonic > 0.0


# ---------------------------------------------------------------
# Caffeinate-on-death shutdown hook
# ---------------------------------------------------------------

def test_keepalive_shutdown_hook_stops_each_held_container():
    """The shutdown hook stops caffeinate for every held lock name."""
    app = _fappBuildFakeApp(
        dictContainerLocks={"projectA": object(), "projectB": object()},
    )
    pipelineServer._fnRegisterHubShutdownStopKeepAlive(app)
    listStopped = []

    async def fnDrive():
        with patch(
            "vaibify.docker.keepAliveManager.fnStopKeepAlive",
            side_effect=listStopped.append,
        ):
            for fnShutdown in app.state.listLifespanShutdown:
                await fnShutdown(app)

    asyncio.run(fnDrive())
    assert sorted(listStopped) == ["projectA", "projectB"]


def test_keepalive_stop_runs_before_locks_are_cleared():
    """In factory registration order, caffeinate is stopped for held names
    BEFORE the lock-release hook clears ``dictContainerLocks`` -- otherwise
    the keep-alive hook would iterate an empty dict and leak caffeinate."""
    app = _fappBuildFakeApp(
        dictContainerLocks={"projectA": object(), "projectB": object()},
    )
    pipelineServer._fnRegisterHubShutdownStopKeepAlive(app)
    pipelineServer._fnRegisterHubLockLifecycle(app)
    listStopped = []

    async def fnDrive():
        with patch(
            "vaibify.docker.keepAliveManager.fnStopKeepAlive",
            side_effect=listStopped.append,
        ), patch(
            "vaibify.config.containerLock.fnReleaseContainerLock",
        ):
            for fnShutdown in app.state.listLifespanShutdown:
                await fnShutdown(app)

    asyncio.run(fnDrive())
    assert sorted(listStopped) == ["projectA", "projectB"]
    assert app.state.dictContainerLocks == {}


# ---------------------------------------------------------------
# Viewer busy-veto (no container lock; served ids in setAllowedContainers)
# ---------------------------------------------------------------

def test_viewer_served_container_running_is_busy():
    """A viewer's served container (no lock) with a run vetoes self-exit."""
    app = _fappBuildFakeApp(
        dictContainerLocks={}, setAllowedContainers={"id-v"},
    )
    with patch(
        "vaibify.gui.fileStatusManager._fbPipelineIsRunning",
        return_value=True,
    ):
        assert pipelineServer._fbAnyHeldContainerBusy(
            app, {"docker": None}) is True


def test_viewer_busy_served_container_prevents_self_exit():
    """A viewer with a mid-run served container never self-exits when idle."""
    app = _fappBuildFakeApp(
        dictContainerLocks={}, setAllowedContainers={"id-v"},
        fLastActivityMonotonic=time.monotonic() - 10_000,
    )
    with patch(
        "vaibify.gui.fileStatusManager._fbPipelineIsRunning",
        return_value=True,
    ):
        assert pipelineServer._fbHubShouldSelfExit(
            app, {"docker": None}, 1.0) is False


def test_viewer_idle_served_container_self_exits():
    """A viewer whose served container is idle still self-exits when abandoned."""
    app = _fappBuildFakeApp(
        dictContainerLocks={}, setAllowedContainers={"id-v"},
        fLastActivityMonotonic=time.monotonic() - 100.0,
    )
    with patch(
        "vaibify.gui.fileStatusManager._fbPipelineIsRunning",
        return_value=False,
    ):
        assert pipelineServer._fbHubShouldSelfExit(
            app, {"docker": None}, 10.0) is True
