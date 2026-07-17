"""Tests for the per-step wall-clock budget feature.

A step's wall-clock budget is an opt-in, advisory ceiling on how long
the active step may run before the dashboard flags it as possibly hung.
It exists because the runner heartbeat only proves the *runner process*
is alive — a step stuck in an infinite loop keeps the daemon heartbeat
beating and, without a budget, is indistinguishable from a legitimately
long forward-model run.

Per the repo's epistemics rules these tests are adversarial: each
guarantee is asserted with the condition that would *break* it (budget
just under vs. just over elapsed, running vs. not, budget present vs.
absent), not merely a single confirming case.
"""

import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

from vaibify.gui import workflowManager
from vaibify.gui.pipelineState import (
    fdictActiveStepBudgetStatus,
    fdictBuildStepStarted,
    fdictBuildInitialState,
    fdictBuildCompletedState,
    fdictBuildInteractivePauseState,
)


def _fsIsoSecondsAgo(fSeconds):
    """Return an ISO-8601 UTC timestamp ``fSeconds`` in the past."""
    dt = datetime.now(timezone.utc) - timedelta(seconds=fSeconds)
    return dt.isoformat()


# --------------------------------------------------------------------------
# Resolver: step budget > workflow default > none. Opt-in, no built-in
# default.
# --------------------------------------------------------------------------


def test_step_budget_wins_over_workflow_default():
    dictWorkflow = {"fDefaultWallClockBudgetSeconds": 100}
    dictStep = {"fWallClockBudgetSeconds": 30}
    assert workflowManager.ffResolveStepWallClockBudget(
        dictWorkflow, dictStep,
    ) == 30.0


def test_workflow_default_applies_when_step_has_none():
    dictWorkflow = {"fDefaultWallClockBudgetSeconds": 100}
    assert workflowManager.ffResolveStepWallClockBudget(
        dictWorkflow, {},
    ) == 100.0


def test_no_budget_when_neither_declared():
    # The feature is opt-in: a workflow with no budgets resolves to 0,
    # so no step is ever flagged over budget.
    assert workflowManager.ffResolveStepWallClockBudget({}, {}) == 0.0


def test_non_positive_step_budget_falls_through_to_default():
    dictWorkflow = {"fDefaultWallClockBudgetSeconds": 100}
    for badValue in (0, -5, None, "", "abc"):
        dictStep = {"fWallClockBudgetSeconds": badValue}
        assert workflowManager.ffResolveStepWallClockBudget(
            dictWorkflow, dictStep,
        ) == 100.0, badValue


def test_garbage_default_coerces_to_zero():
    dictWorkflow = {"fDefaultWallClockBudgetSeconds": "not-a-number"}
    assert workflowManager.ffResolveStepWallClockBudget(
        dictWorkflow, {},
    ) == 0.0


# --------------------------------------------------------------------------
# Live over-budget status: the boundary is elapsed > budget, only while
# running, only with a budget and a start stamp.
# --------------------------------------------------------------------------


def _dictRunningState(fBudget, fElapsed):
    return {
        "bRunning": True,
        "iActiveStep": 4,
        "fActiveStepBudgetSeconds": fBudget,
        "sActiveStepStartedIso": _fsIsoSecondsAgo(fElapsed),
    }


def test_over_budget_true_when_elapsed_exceeds_budget():
    dictStatus = fdictActiveStepBudgetStatus(
        _dictRunningState(60, 600),
    )
    assert dictStatus["bActiveStepOverBudget"] is True
    assert dictStatus["fActiveStepBudgetSeconds"] == 60.0
    assert dictStatus["fActiveStepElapsedSeconds"] > 590


def test_over_budget_false_just_under_the_boundary():
    # Adversarial: elapsed just below budget must NOT flag. Uses a
    # controlled fNowEpoch so the boundary is exact, not clock-racy.
    fStartEpoch = 1_000_000.0
    dictState = {
        "bRunning": True,
        "fActiveStepBudgetSeconds": 100,
        "sActiveStepStartedIso": datetime.fromtimestamp(
            fStartEpoch, timezone.utc,
        ).isoformat(),
    }
    dictUnder = fdictActiveStepBudgetStatus(
        dictState, fNowEpoch=fStartEpoch + 99,
    )
    dictOver = fdictActiveStepBudgetStatus(
        dictState, fNowEpoch=fStartEpoch + 101,
    )
    assert dictUnder["bActiveStepOverBudget"] is False
    assert dictOver["bActiveStepOverBudget"] is True


def test_elapsed_is_a_true_difference_not_a_modulo():
    """Elapsed must be now-minus-start, robust to any epoch scale.

    With realistic epochs ``now % start`` coincides with ``now - start``
    (elapsed is always smaller than the start epoch), so only a small
    synthetic epoch can tell subtraction from a modulo: started at
    epoch 1000 with now at 2500, true elapsed is 1500 while a modulo
    would report 500 and clear the over-budget flag.
    """
    dictState = {
        "bRunning": True,
        "fActiveStepBudgetSeconds": 1000,
        "sActiveStepStartedIso": datetime.fromtimestamp(
            1000.0, timezone.utc,
        ).isoformat(),
    }
    dictStatus = fdictActiveStepBudgetStatus(dictState, fNowEpoch=2500.0)
    assert dictStatus["fActiveStepElapsedSeconds"] == 1500.0
    assert dictStatus["bActiveStepOverBudget"] is True


def test_fractional_budget_resolves_exactly():
    """A sub-second budget is legal and must survive coercion intact."""
    assert workflowManager.ffResolveStepWallClockBudget(
        {}, {"fWallClockBudgetSeconds": 0.5},
    ) == 0.5


def test_negative_budget_resolves_to_exactly_zero():
    """Any non-positive budget means no budget: exactly 0.0, nothing else."""
    assert workflowManager.ffResolveStepWallClockBudget(
        {}, {"fWallClockBudgetSeconds": -0.5},
    ) == 0.0
    assert workflowManager.ffResolveStepWallClockBudget(
        {"fDefaultWallClockBudgetSeconds": -0.5}, {},
    ) == 0.0


def test_not_flagged_when_pipeline_not_running():
    dictState = _dictRunningState(60, 600)
    dictState["bRunning"] = False
    assert fdictActiveStepBudgetStatus(
        dictState,
    )["bActiveStepOverBudget"] is False


def test_not_flagged_without_a_budget_even_if_ancient():
    dictState = _dictRunningState(0, 100000)
    assert fdictActiveStepBudgetStatus(
        dictState,
    )["bActiveStepOverBudget"] is False


def test_not_flagged_without_a_start_stamp():
    dictState = {
        "bRunning": True,
        "fActiveStepBudgetSeconds": 60,
        "sActiveStepStartedIso": "",
    }
    assert fdictActiveStepBudgetStatus(
        dictState,
    )["bActiveStepOverBudget"] is False


def test_unparseable_start_stamp_is_safe():
    dictState = {
        "bRunning": True,
        "fActiveStepBudgetSeconds": 60,
        "sActiveStepStartedIso": "garbage",
    }
    dictStatus = fdictActiveStepBudgetStatus(dictState)
    assert dictStatus["bActiveStepOverBudget"] is False
    assert dictStatus["fActiveStepElapsedSeconds"] == 0.0


# --------------------------------------------------------------------------
# State stamping: the runner records start time + resolved budget; every
# non-running transition clears the stamp so a stale budget never lingers.
# --------------------------------------------------------------------------


def test_step_started_stamps_start_and_budget():
    dictUpdate = fdictBuildStepStarted(3, 45)
    assert dictUpdate["iActiveStep"] == 3
    assert dictUpdate["fActiveStepBudgetSeconds"] == 45.0
    assert dictUpdate["sActiveStepStartedIso"]
    # The stamped iso must parse and be very recent.
    dt = datetime.fromisoformat(dictUpdate["sActiveStepStartedIso"])
    assert (datetime.now(timezone.utc) - dt).total_seconds() < 5


def test_step_started_defaults_to_no_budget():
    dictUpdate = fdictBuildStepStarted(1)
    assert dictUpdate["fActiveStepBudgetSeconds"] == 0.0


def test_initial_state_has_empty_budget_fields():
    dictState = fdictBuildInitialState("runAll", "/log", 3)
    assert dictState["sActiveStepStartedIso"] == ""
    assert dictState["fActiveStepBudgetSeconds"] == 0.0


def test_completed_state_clears_budget_stamp():
    dictCompleted = fdictBuildCompletedState(0)
    assert dictCompleted["sActiveStepStartedIso"] == ""
    assert dictCompleted["fActiveStepBudgetSeconds"] == 0.0


def test_interactive_pause_clears_budget_stamp():
    # A human pause must never inherit a prior step's running budget.
    dictPause = fdictBuildInteractivePauseState(2, "Review")
    assert dictPause["sActiveStepStartedIso"] == ""
    assert dictPause["fActiveStepBudgetSeconds"] == 0.0


def test_completed_state_makes_a_stale_budget_dormant():
    # Composed guarantee: a step that was over budget, once the run
    # completes, is no longer flagged (the bRunning guard + the reset).
    dictState = _dictRunningState(60, 600)
    dictState.update(fdictBuildCompletedState(0))
    assert fdictActiveStepBudgetStatus(
        dictState,
    )["bActiveStepOverBudget"] is False


# --------------------------------------------------------------------------
# Poll wire: the /status payload surfaces the live over-budget status.
# --------------------------------------------------------------------------


def test_wire_payload_surfaces_over_budget():
    from vaibify.gui.routes.pipelineRoutes import _fdictRunStateForWire
    dictWire = _fdictRunStateForWire(_dictRunningState(30, 300))
    assert dictWire["bRunning"] is True
    assert dictWire["iActiveStep"] == 4
    assert dictWire["bActiveStepOverBudget"] is True
    assert dictWire["fActiveStepBudgetSeconds"] == 30.0
    assert dictWire["fActiveStepElapsedSeconds"] > 290


def test_wire_payload_no_budget_is_not_over_budget():
    from vaibify.gui.routes.pipelineRoutes import _fdictRunStateForWire
    dictWire = _fdictRunStateForWire(_dictRunningState(0, 300))
    assert dictWire["bActiveStepOverBudget"] is False


def test_wire_payload_handles_empty_state():
    from vaibify.gui.routes.pipelineRoutes import _fdictRunStateForWire
    dictWire = _fdictRunStateForWire({})
    assert dictWire["bRunning"] is False
    assert dictWire["bActiveStepOverBudget"] is False


# --------------------------------------------------------------------------
# Settability: the field survives the step-update merge and the
# workflow default appears in the settings subset.
# --------------------------------------------------------------------------


def test_step_update_merge_persists_budget_field():
    dictWorkflow = {"listSteps": [{"sName": "A"}]}
    workflowManager.fnUpdateStep(
        dictWorkflow, 0, {"fWallClockBudgetSeconds": 120},
    )
    assert dictWorkflow["listSteps"][0][
        "fWallClockBudgetSeconds"
    ] == 120
    # And the resolver then reads it back.
    assert workflowManager.ffResolveStepWallClockBudget(
        dictWorkflow, dictWorkflow["listSteps"][0],
    ) == 120.0


def test_settings_subset_includes_workflow_default():
    from vaibify.gui.pipelineServer import fdictExtractSettings
    dictSettings = fdictExtractSettings(
        {"fDefaultWallClockBudgetSeconds": 900},
    )
    assert dictSettings["fDefaultWallClockBudgetSeconds"] == 900


def test_settings_subset_default_is_zero_when_absent():
    from vaibify.gui.pipelineServer import fdictExtractSettings
    assert fdictExtractSettings({})[
        "fDefaultWallClockBudgetSeconds"
    ] == 0.0


def test_request_schemas_accept_the_budget_fields():
    from vaibify.gui.pipelineServer import (
        StepUpdateRequest,
        WorkflowSettingsRequest,
    )
    assert StepUpdateRequest(
        fWallClockBudgetSeconds=60,
    ).fWallClockBudgetSeconds == 60
    assert WorkflowSettingsRequest(
        fDefaultWallClockBudgetSeconds=120,
    ).fDefaultWallClockBudgetSeconds == 120


# --------------------------------------------------------------------------
# Runner integration: the resolved budget rides the stepStarted event and
# the run loop resolves it from the workflow. These prove the end-to-end
# wiring that per-function unit tests cannot.
# --------------------------------------------------------------------------


def _fnRunAsync(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


@patch("vaibify.gui.pipelineRunner._fiExecuteAndRecord",
       new_callable=AsyncMock, return_value=0)
@patch("vaibify.gui.pipelineRunner._fiCheckDependencies",
       new_callable=AsyncMock, return_value=0)
def test_step_started_event_carries_the_budget(mockDeps, mockExec):
    from vaibify.gui.pipelineRunner import _fnRunOneStep
    listEvents = []

    async def fnCallback(dictEvent):
        listEvents.append(dictEvent)

    _fnRunAsync(_fnRunOneStep(
        MagicMock(), "cid", {"sName": "Compute"}, 1,
        "/work", {}, fnCallback, fWallClockBudgetSeconds=45,
    ))
    dictStarted = next(
        d for d in listEvents if d["sType"] == "stepStarted"
    )
    assert dictStarted["fWallClockBudgetSeconds"] == 45


@patch("vaibify.gui.pipelineRunner._fnRunOneStep",
       new_callable=AsyncMock, return_value=0)
@patch("vaibify.gui.pipelineRunner._fbShouldRunStep", return_value=True)
def test_run_loop_resolves_and_passes_budget(mockShould, mockRunOne):
    from vaibify.gui.pipelineRunner import _fiRunStepList

    async def fnCallback(dictEvent):
        return None

    dictWorkflow = {
        "fDefaultWallClockBudgetSeconds": 90,
        "listSteps": [
            # Step's own budget wins over the workflow default.
            {"sName": "A", "bRunEnabled": True,
             "fWallClockBudgetSeconds": 30},
        ],
    }
    _fnRunAsync(_fiRunStepList(
        MagicMock(), "cid", dictWorkflow, "/work", {}, fnCallback,
    ))
    assert mockRunOne.call_args.kwargs[
        "fWallClockBudgetSeconds"
    ] == 30.0
