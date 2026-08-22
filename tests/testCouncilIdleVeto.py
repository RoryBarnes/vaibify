"""The idle-watchdog veto and the appFactory/serverLifespan wiring.

The council is invisible to every existing idle-exit signal by
construction (design section 21): it polls over HTTP rather than holding
a WebSocket, and its runners are deliberately kept out of
``dictContainerOwners``. So without a dedicated veto a closed tab would
let the activity clock go stale and the hub would SIGTERM itself
mid-turn. These tests prove the veto is load-bearing — neutralize it and
the hub self-exits under live work — and that the registry, campaign
store and lifecycle hooks are wired onto the application.
"""

import time

from vaibify.gui import (
    agentCouncilRegistry,
    agentCouncilStore,
    appFactory,
    pipelineServer,
    serverLifespan,
)


def _appIdleHub():
    """Return a viewer app whose activity clock is long stale."""
    app = pipelineServer.fappCreateApplication(
        sWorkspaceRoot="/workspace", sTerminalUserArg="testuser")
    app.state.fLastActivityMonotonic = time.monotonic() - 100000
    return app


def test_registry_and_store_are_wired_onto_app_state():
    """The two app-owned council authorities live on ``app.state``."""
    app = _appIdleHub()
    assert isinstance(app.state.dictCouncilRegistry, dict)
    assert isinstance(app.state.dictCouncilCampaignStore, dict)


def test_an_idle_hub_with_no_council_work_would_self_exit():
    """Baseline: nothing live, clock stale, so the hub retires."""
    app = _appIdleHub()
    assert serverLifespan._fbHubShouldSelfExit(
        app, app.state.dictRouteContext, 1.0) is True


def test_a_live_council_turn_vetoes_self_exit():
    """A turn in flight keeps the hub from retiring mid-turn."""
    app = _appIdleHub()
    agentCouncilRegistry.fbRegisterTurnInFlight(
        app.state.dictCouncilRegistry, "campaign-1", "turn-1")
    assert serverLifespan._fbHubShouldSelfExit(
        app, app.state.dictRouteContext, 1.0) is False


def test_neutralizing_the_veto_lets_the_hub_self_exit(monkeypatch):
    """Falsification: with the predicate stubbed False, live work no longer
    holds the hub — which is exactly why the real predicate must be
    consulted. Kills a change that drops the veto call from
    ``_fbHubShouldSelfExit``.
    """
    app = _appIdleHub()
    agentCouncilRegistry.fbRegisterTurnInFlight(
        app.state.dictCouncilRegistry, "campaign-1", "turn-1")
    monkeypatch.setattr(
        agentCouncilRegistry, "fbHubHasLiveCouncilWork", lambda app: False)
    assert serverLifespan._fbHubShouldSelfExit(
        app, app.state.dictRouteContext, 1.0) is True


def test_council_drain_closes_admission():
    """The shutdown drain stops the registry admitting new turns."""
    app = _appIdleHub()
    appFactory._fnDrainCouncilRunners(app)
    assert app.state.dictCouncilRegistry["bAdmittingNewTurns"] is False


def test_startup_reconcile_is_a_no_op_without_a_daemon():
    """A host-only hub has no daemon, so reconcile skips without error."""
    app = _appIdleHub()
    # No exception, and the empty registry is left admitting.
    appFactory._fnReconcileCouncilRunners(app)
    assert app.state.dictCouncilRegistry["bAdmittingNewTurns"] is True


def test_the_council_drain_is_ordered_after_the_guarded_mutation_drain():
    """Shutdown ordering: the council drain hook follows the mutation drain.

    The flock-release and keep-alive hooks may only run after both drains
    (design section 21), so the council drain is registered after the
    guarded-mutation drain and before the hub lifecycle.
    """
    app = pipelineServer.fappCreateHubApplication()
    listNames = [getattr(fnHook, "__name__", "")
                 for fnHook in app.state.listLifespanShutdown]
    assert "fnDrainGuardedMutations" in listNames
    assert "fnDrainCouncilOnShutdown" in listNames
    assert listNames.index("fnDrainCouncilOnShutdown") > (
        listNames.index("fnDrainGuardedMutations"))
