"""Tests for the idle self-shutdown watchdog and WebSocket presence counter.

Covers the L1 "Idle self-shutdown" behavior in
``vaibify.gui.pipelineServer``: the activity middleware, the
live-WebSocket counter, the busy-veto, the self-exit decision, and the
watchdog loop that self-SIGTERMs only when genuinely abandoned.
"""

import asyncio
import math
import os
import signal
import time
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from vaibify.gui import pipelineServer, serverLifespan
from vaibify.gui.routes.sessionRoutes import S_SUPPRESS_BROWSER_ENV


# ---------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------

def _fappBuildFakeApp(**kwargs):
    """Return a stand-in app whose state carries the given attributes."""
    dictState = {
        "iActiveWebSockets": 0,
        "fLastActivityMonotonic": time.monotonic(),
        "dictContainerOwners": {},
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
    """With no owned containers the hub is never considered busy."""
    app = _fappBuildFakeApp(dictContainerOwners={})
    assert pipelineServer._fbAnyHeldContainerBusy(app, {"docker": None}) is False


def test_held_container_running_is_busy():
    """An owned container with a running pipeline vetoes self-exit."""
    app = _fappBuildFakeApp(
        dictContainerOwners={"projectA": object()}, iHubPort=8050,
    )
    dictCtx = {"docker": _FakeDocker({"projectA": "id-a"})}
    with patch(
        "vaibify.gui.fileStatusManager._fbPipelineIsRunning",
        return_value=True,
    ):
        assert pipelineServer._fbAnyHeldContainerBusy(app, dictCtx) is True


def test_held_container_idle_is_not_busy():
    """An owned container with no running pipeline does not veto self-exit."""
    app = _fappBuildFakeApp(
        dictContainerOwners={"projectA": object()}, iHubPort=8050,
    )
    dictCtx = {"docker": _FakeDocker({"projectA": "id-a"})}
    with patch(
        "vaibify.gui.fileStatusManager._fbPipelineIsRunning",
        return_value=False,
    ):
        assert pipelineServer._fbAnyHeldContainerBusy(app, dictCtx) is False


def test_docker_error_treated_as_busy():
    """A Docker failure while probing owned containers fails safe to busy."""
    app = _fappBuildFakeApp(
        dictContainerOwners={"projectA": object()}, iHubPort=8050,
    )
    dictCtx = {"docker": _FakeDocker({}, bRaise=True)}
    assert pipelineServer._fbAnyHeldContainerBusy(app, dictCtx) is True


def test_none_docker_with_held_locks_is_busy():
    """An owned hub container with no Docker connection fails safe to busy."""
    app = _fappBuildFakeApp(
        dictContainerOwners={"projectA": object()}, iHubPort=8050,
    )
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
        dictContainerOwners={"projectA": object()}, iHubPort=8050,
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
# Never / disabled timeout
# ---------------------------------------------------------------

def test_never_timeout_prevents_self_exit_however_idle():
    """An infinite (never) timeout forbids self-exit no matter how idle."""
    app = _fappBuildFakeApp(
        fLastActivityMonotonic=time.monotonic() - 10_000_000.0,
    )
    assert pipelineServer._fbHubShouldSelfExit(
        app, {"docker": None}, math.inf,
    ) is False


def test_none_timeout_prevents_self_exit():
    """A ``None`` timeout is the disabled case and forbids self-exit."""
    app = _fappBuildFakeApp(
        fLastActivityMonotonic=time.monotonic() - 10_000.0,
    )
    assert pipelineServer._fbHubShouldSelfExit(
        app, {"docker": None}, None,
    ) is False


def test_zero_timeout_still_self_exits_when_idle():
    """Zero keeps its historical meaning: retire as soon as idle."""
    app = _fappBuildFakeApp(
        fLastActivityMonotonic=time.monotonic() - 0.001,
    )
    assert pipelineServer._fbHubShouldSelfExit(
        app, {"docker": None}, 0.0,
    ) is True


# ---------------------------------------------------------------
# Live timeout on app.state
# ---------------------------------------------------------------

def test_current_idle_timeout_prefers_app_state():
    """The live reader returns the app.state value over the fallback."""
    app = _fappBuildFakeApp(fIdleTimeoutSeconds=42.0)
    assert serverLifespan._ffCurrentIdleTimeout(app, 999.0) == 42.0


def test_current_idle_timeout_falls_back_when_unset():
    """The live reader falls back when app.state carries no timeout."""
    app = _fappBuildFakeApp()
    assert serverLifespan._ffCurrentIdleTimeout(app, 999.0) == 999.0


def test_watchdog_never_self_exits_while_timeout_is_never():
    """With never on app.state the loop keeps polling and never SIGTERMs."""
    app = _fappBuildFakeApp(
        fLastActivityMonotonic=time.monotonic() - 10_000.0,
        fIdleTimeoutSeconds=math.inf,
    )
    listKills = []

    async def fnDrive():
        with patch.object(
            pipelineServer.os, "kill",
            lambda iPid, iSignal: listKills.append(iPid),
        ):
            taskWatchdog = asyncio.create_task(
                pipelineServer._fnIdleShutdownWatchdogLoop(
                    app, {"docker": None}, 0.01, math.inf,
                ),
            )
            await asyncio.sleep(0.1)
            taskWatchdog.cancel()
            try:
                await taskWatchdog
            except asyncio.CancelledError:
                pass

    asyncio.run(fnDrive())
    assert listKills == []


def test_watchdog_fires_after_live_change_from_never_to_finite():
    """A Settings change to a finite timeout takes effect without relaunch."""
    app = _fappBuildFakeApp(
        fLastActivityMonotonic=time.monotonic() - 100.0,
        fIdleTimeoutSeconds=math.inf,
    )
    listKills = []

    async def fnDrive():
        async def fnEnableFiniteTimeout():
            await asyncio.sleep(0.05)
            app.state.fIdleTimeoutSeconds = 0.0

        with patch.object(
            pipelineServer.os, "kill",
            lambda iPid, iSignal: listKills.append(iPid),
        ):
            taskFlip = asyncio.create_task(fnEnableFiniteTimeout())
            await asyncio.wait_for(
                pipelineServer._fnIdleShutdownWatchdogLoop(
                    app, {"docker": None}, 0.01, math.inf,
                ),
                timeout=1.0,
            )
            await taskFlip

    asyncio.run(fnDrive())
    assert listKills, "watchdog never fired after the timeout went finite"


# ---------------------------------------------------------------
# Startup timeout resolution & precedence
# ---------------------------------------------------------------

def _fnClearIdleEnvironment():
    """Remove both idle-relevant env vars from the current environment."""
    os.environ.pop(serverLifespan.S_HUB_IDLE_TIMEOUT_ENV, None)
    os.environ.pop(S_SUPPRESS_BROWSER_ENV, None)


def test_env_override_beats_stored_preference_and_default():
    """The env override is the highest-precedence timeout source."""
    with patch.dict(os.environ, {}, clear=False), patch(
        "vaibify.config.preferencesStore.fsIdleTimeoutPreference",
        return_value="900",
    ):
        _fnClearIdleEnvironment()
        os.environ[serverLifespan.S_HUB_IDLE_TIMEOUT_ENV] = "60"
        assert serverLifespan._ffResolveIdleTimeoutSeconds() == 60.0


def test_env_never_token_resolves_to_infinity():
    """A ``never`` env override disables the reaper entirely."""
    with patch.dict(os.environ, {}, clear=False), patch(
        "vaibify.config.preferencesStore.fsIdleTimeoutPreference",
        return_value="",
    ):
        _fnClearIdleEnvironment()
        os.environ[serverLifespan.S_HUB_IDLE_TIMEOUT_ENV] = "never"
        assert math.isinf(serverLifespan._ffResolveIdleTimeoutSeconds())


def test_stored_preference_applies_when_no_env_override():
    """The stored Settings preference wins over the launch default."""
    with patch.dict(os.environ, {}, clear=False), patch(
        "vaibify.config.preferencesStore.fsIdleTimeoutPreference",
        return_value="120",
    ):
        _fnClearIdleEnvironment()
        assert serverLifespan._ffResolveIdleTimeoutSeconds() == 120.0


def test_browser_launch_defaults_to_never():
    """With no env and no preference a browser launch never self-retires."""
    with patch.dict(os.environ, {}, clear=False), patch(
        "vaibify.config.preferencesStore.fsIdleTimeoutPreference",
        return_value="",
    ):
        _fnClearIdleEnvironment()
        assert math.isinf(serverLifespan._ffResolveIdleTimeoutSeconds())


def test_headless_launch_defaults_to_finite_reaper():
    """A browser-suppressed (headless/remote) launch keeps the finite reaper."""
    with patch.dict(os.environ, {}, clear=False), patch(
        "vaibify.config.preferencesStore.fsIdleTimeoutPreference",
        return_value="",
    ):
        _fnClearIdleEnvironment()
        os.environ[S_SUPPRESS_BROWSER_ENV] = "1"
        assert (
            serverLifespan._ffResolveIdleTimeoutSeconds()
            == serverLifespan.F_HUB_IDLE_TIMEOUT_SECONDS
        )


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


def test_startup_publishes_effective_timeout_on_app_state():
    """Registration's startup hook resolves and publishes the live timeout."""
    app = _fappBuildFakeApp()
    serverLifespan._fnRegisterIdleShutdownWatchdog(
        app, {"docker": None}, fInterval=10.0,
    )

    async def fnDrive():
        with patch.object(
            serverLifespan, "_ffResolveIdleTimeoutSeconds",
            return_value=math.inf,
        ):
            for fnStartup in app.state.listLifespanStartup:
                await fnStartup(app)
        taskWatchdog = app.state.taskIdleWatchdog
        taskWatchdog.cancel()
        try:
            await taskWatchdog
        except asyncio.CancelledError:
            pass

    asyncio.run(fnDrive())
    assert app.state.fIdleTimeoutSeconds == math.inf


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
        dictContainerOwners={"projectA": object(), "projectB": object()},
    )
    pipelineServer._fnRegisterHubShutdownStopKeepAlive(app)
    listStopped = []

    async def fnDrive():
        with patch(
            "vaibify.config.keepAliveManager.fnStopKeepAlive",
            side_effect=listStopped.append,
        ):
            for fnShutdown in app.state.listLifespanShutdown:
                await fnShutdown(app)

    asyncio.run(fnDrive())
    assert sorted(listStopped) == ["projectA", "projectB"]


def test_keepalive_stop_runs_before_locks_are_cleared():
    """In factory registration order, caffeinate is stopped for owned names
    BEFORE the lock-release hook clears ``dictContainerOwners`` -- otherwise
    the keep-alive hook would iterate an empty dict and leak caffeinate."""
    app = _fappBuildFakeApp(
        dictContainerOwners={"projectA": object(), "projectB": object()},
    )
    pipelineServer._fnRegisterHubShutdownStopKeepAlive(app)
    pipelineServer._fnRegisterHubLockLifecycle(app)
    listStopped = []

    async def fnDrive():
        with patch(
            "vaibify.config.keepAliveManager.fnStopKeepAlive",
            side_effect=listStopped.append,
        ), patch(
            "vaibify.config.containerLock.fnReleaseContainerLock",
        ):
            for fnShutdown in app.state.listLifespanShutdown:
                await fnShutdown(app)

    asyncio.run(fnDrive())
    assert sorted(listStopped) == ["projectA", "projectB"]
    assert app.state.dictContainerOwners == {}


# ---------------------------------------------------------------
# Viewer busy-veto (no iHubPort; served ids keyed in dictContainerOwners)
# ---------------------------------------------------------------

def test_viewer_served_container_running_is_busy():
    """A viewer's served container (no lock) with a run vetoes self-exit."""
    app = _fappBuildFakeApp(
        dictContainerOwners={"id-v": object()},
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
        dictContainerOwners={"id-v": object()},
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
        dictContainerOwners={"id-v": object()},
        fLastActivityMonotonic=time.monotonic() - 100.0,
    )
    with patch(
        "vaibify.gui.fileStatusManager._fbPipelineIsRunning",
        return_value=False,
    ):
        assert pipelineServer._fbHubShouldSelfExit(
            app, {"docker": None}, 10.0) is True
