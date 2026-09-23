"""The status poll's run state says how each step of a run ended.

A researcher watched an in-container agent run a workflow from its
third step, 2026-09-23. Each step's light began pulsing when the step
started and never stopped: by the end every step the run had passed
through was pulsing at once, and when the run finished none of them
turned blue. The continuously polled status payload carried only
WHICH step was active, so the dashboard had no way to learn that a
step had finished, or how. These tests hold the payload to carrying
the run's own per-step verdicts, and to carrying them only onto the
workflow the run belongs to.
"""

import contextlib
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from vaibify.gui.routes.pipelineRoutes import (
    _fdictFetchOutputStatus,
    _fdictRunStateForWire,
)


S_OPEN_WORKFLOW = "/workspace/proj/.vaibify/workflows/demo.json"
S_OTHER_WORKFLOW = "/workspace/proj/.vaibify/workflows/other.json"


def _fdictRunningState(sWorkflowPath):
    """Return a pipeline state three steps into a run of sWorkflowPath."""
    return {
        "bRunning": True,
        "iActiveStep": 3,
        "sWorkflowPath": sWorkflowPath,
        "dictStepResults": {
            "1": {"sStatus": "passed", "iExitCode": 0},
            "2": {"sStatus": "failed", "iExitCode": 1},
        },
    }


@pytest.mark.falsification
def test_run_state_carries_each_finished_steps_verdict():
    """Kills: dropping the step results from the wire.

    Without them the dashboard can only mark the active step, which is
    exactly how every finished step of an agent's run kept pulsing.
    """
    dictWire = _fdictRunStateForWire(
        _fdictRunningState(S_OPEN_WORKFLOW), S_OPEN_WORKFLOW,
    )
    assert dictWire["dictStepResults"] == {
        "1": {"sStatus": "passed", "iExitCode": 0},
        "2": {"sStatus": "failed", "iExitCode": 1},
    }
    assert dictWire["iActiveStep"] == 3


def test_a_finished_run_still_carries_its_verdicts():
    """The idle answer is where the last step's verdict first appears."""
    dictState = _fdictRunningState(S_OPEN_WORKFLOW)
    dictState.update({"bRunning": False, "iExitCode": 1})
    dictWire = _fdictRunStateForWire(dictState, S_OPEN_WORKFLOW)
    assert dictWire["bRunning"] is False
    assert dictWire["dictStepResults"]["2"]["sStatus"] == "failed"


def test_no_state_carries_no_verdicts():
    """An absent state is no run and no results, never a stale claim."""
    dictWire = _fdictRunStateForWire({}, S_OPEN_WORKFLOW)
    assert dictWire["bRunning"] is False
    assert dictWire["dictStepResults"] == {}


@pytest.mark.falsification
def test_another_workflows_run_is_not_painted_onto_this_one():
    """Kills: reporting a run whatever workflow it belongs to.

    A container can host several workflows, and step 3 of one is not
    step 3 of another. A run recorded against a different workflow says
    nothing about any step in the one open here.
    """
    dictWire = _fdictRunStateForWire(
        _fdictRunningState(S_OTHER_WORKFLOW), S_OPEN_WORKFLOW,
    )
    assert dictWire["bRunning"] is False
    assert dictWire["iActiveStep"] == -1
    assert dictWire["dictStepResults"] == {}


def test_a_state_that_names_no_workflow_is_still_reported():
    """A state written before the run recorded its workflow is not
    discarded: it cannot be shown to belong elsewhere, and hiding a
    live run would be the opposite lie."""
    dictState = _fdictRunningState("")
    dictWire = _fdictRunStateForWire(dictState, S_OPEN_WORKFLOW)
    assert dictWire["bRunning"] is True
    assert dictWire["iActiveStep"] == 3


_T_EMPTY_POLL_HELPERS = (
    "fdictCollectOutputPathsByStep",
    "fdictHandleCollectMarkerPathsByStep",
    "_fdictDetectAndInvalidate",
    "_fdictComputeMaxMtimeByStep",
    "_fdictComputeMaxPlotMtimeByStep",
    "_fdictComputeMaxDataMtimeByStep",
    "_fdictComputeMarkerMtimeByStep",
    "_fdictBuildScriptStatus",
)


async def _fdictPollWithPipelineState(dictPipelineState):
    """Build the status payload for S_OPEN_WORKFLOW over dictPipelineState."""
    dictCtx = {
        "docker": MagicMock(),
        "save": MagicMock(),
        "paths": {"cid1": S_OPEN_WORKFLOW},
    }
    sModule = "vaibify.gui.routes.pipelineRoutes."
    with contextlib.ExitStack() as stackPatches:
        for sHelper in _T_EMPTY_POLL_HELPERS:
            stackPatches.enter_context(
                patch(sModule + sHelper, return_value={}),
            )
        stackPatches.enter_context(patch(
            sModule + "_flistCollectOutputPaths", return_value=[],
        ))
        stackPatches.enter_context(patch(
            sModule + "ftGetModTimesAndFingerprint",
            return_value=({}, ""),
        ))
        stackPatches.enter_context(patch(
            sModule + "_fbCheckStaleUserVerification",
            return_value=False,
        ))
        stackPatches.enter_context(patch(
            sModule + "_fdictMaybeReloadWorkflow",
            return_value={
                "bReplaced": False, "dictWorkflow": None, "sError": None,
            },
        ))
        stackPatches.enter_context(patch(
            sModule + "_fdictReconcilePipelineState",
            new=AsyncMock(return_value=dictPipelineState),
        ))
        return await _fdictFetchOutputStatus(
            dictCtx, "cid1", {"listSteps": []}, {},
        )


@pytest.mark.falsification
@pytest.mark.asyncio
async def test_the_poll_compares_the_run_with_the_open_workflow():
    """Kills: the poll not telling the projection which workflow is open.

    The guard above is inert unless the payload builder hands it the
    open workflow's path; this drives the builder, not the projection.
    """
    dictOther = await _fdictPollWithPipelineState(
        _fdictRunningState(S_OTHER_WORKFLOW),
    )
    assert dictOther["dictRunState"]["bRunning"] is False
    dictOwn = await _fdictPollWithPipelineState(
        _fdictRunningState(S_OPEN_WORKFLOW),
    )
    assert dictOwn["dictRunState"]["bRunning"] is True
    assert dictOwn["dictRunState"]["dictStepResults"]["1"]["sStatus"] == (
        "passed"
    )
