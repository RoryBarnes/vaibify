"""Tests for vaibify.gui.interactiveSteps module."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from vaibify.gui.interactiveSteps import (
    F_INTERACTIVE_WAIT_HOURS,
    I_ABANDONED_EXIT_CODE,
    S_ABANDONED_SENTINEL,
    fdictCreateInteractiveContext,
    fnSetInteractiveResponse,
    fsBuildAbandonedReason,
    _fiHandleInteractiveStep,
    _fiRunInteractiveAndRecord,
    _fsAwaitInteractiveDecision,
    _fiAwaitInteractiveComplete,
)


@pytest.mark.asyncio
async def test_fdictCreateInteractiveContext_returns_event_and_response():
    dictContext = fdictCreateInteractiveContext()
    assert isinstance(dictContext["eventResume"], asyncio.Event)
    assert dictContext["sResponse"] == ""


@pytest.mark.asyncio
async def test_fnSetInteractiveResponse_sets_and_triggers():
    dictContext = fdictCreateInteractiveContext()
    fnSetInteractiveResponse(dictContext, "skip")
    assert dictContext["sResponse"] == "skip"
    assert dictContext["eventResume"].is_set()


# -- _fiHandleInteractiveStep tests --


@pytest.mark.asyncio
async def test_fiHandleInteractiveStep_returns_zero_when_no_interactive():
    iResult = await _fiHandleInteractiveStep(
        None, "cid", {"sName": "step1"}, 1, AsyncMock(), None,
    )
    assert iResult == 0


@pytest.mark.asyncio
async def test_fiHandleInteractiveStep_emits_pause_and_skips():
    dictInteractive = fdictCreateInteractiveContext()
    fnCallback = AsyncMock()

    async def _fnFakeDecision(dictCtx):
        return "skip"

    with patch(
        "vaibify.gui.interactiveSteps._fsAwaitInteractiveDecision",
        side_effect=_fnFakeDecision,
    ):
        iResult = await _fiHandleInteractiveStep(
            None, "cid", {"sName": "MyStep"}, 3, fnCallback, dictInteractive,
        )

    assert iResult == 0
    dictEmitted = fnCallback.call_args[0][0]
    assert dictEmitted["sType"] == "interactivePause"
    assert dictEmitted["iStepIndex"] == 2
    assert dictEmitted["iStepNumber"] == 3
    assert dictEmitted["sStepName"] == "MyStep"


@pytest.mark.asyncio
async def test_fiHandleInteractiveStep_uses_default_step_name():
    dictInteractive = fdictCreateInteractiveContext()
    fnCallback = AsyncMock()

    async def _fnFakeDecision(dictCtx):
        return "skip"

    with patch(
        "vaibify.gui.interactiveSteps._fsAwaitInteractiveDecision",
        side_effect=_fnFakeDecision,
    ):
        iResult = await _fiHandleInteractiveStep(
            None, "cid", {}, 5, fnCallback, dictInteractive,
        )

    assert iResult == 0
    dictEmitted = fnCallback.call_args[0][0]
    assert dictEmitted["sStepName"] == "Step 5"


@pytest.mark.asyncio
async def test_fiHandleInteractiveStep_runs_when_not_skip():
    dictInteractive = fdictCreateInteractiveContext()
    fnCallback = AsyncMock()

    async def _fnFakeDecision(dictCtx):
        return "continue"

    async def _fnFakeRun(*args, **kwargs):
        return 42

    with patch(
        "vaibify.gui.interactiveSteps._fsAwaitInteractiveDecision",
        side_effect=_fnFakeDecision,
    ), patch(
        "vaibify.gui.interactiveSteps._fiRunInteractiveAndRecord",
        side_effect=_fnFakeRun,
    ):
        iResult = await _fiHandleInteractiveStep(
            None, "cid", {"sName": "RunMe"}, 2, fnCallback, dictInteractive,
        )

    assert iResult == 42


# -- _fiRunInteractiveAndRecord tests --


@pytest.mark.asyncio
async def test_fiRunInteractiveAndRecord_records_and_emits():
    dictStep = {"sName": "InterStep", "dictRunStats": {"sDuration": "1s"}}
    fnCallback = AsyncMock()
    dictInteractive = fdictCreateInteractiveContext()

    async def _fnFakeComplete(dictCtx):
        return 0

    with patch(
        "vaibify.gui.interactiveSteps._fiAwaitInteractiveComplete",
        side_effect=_fnFakeComplete,
    ), patch(
        "vaibify.gui.pipelineUtils._fnRecordRunStats",
    ) as mockRecordStats, patch(
        "vaibify.gui.pipelineUtils._fnEmitStepResult",
        new_callable=AsyncMock,
    ) as mockEmitResult:
        iResult = await _fiRunInteractiveAndRecord(
            "dockerConn", "cid123", dictStep, 4, fnCallback, dictInteractive,
        )

    assert iResult == 0
    mockRecordStats.assert_called_once()
    mockEmitResult.assert_awaited_once_with(fnCallback, 4, 0)

    listCalls = fnCallback.call_args_list
    assert listCalls[0][0][0]["sType"] == "interactiveTerminalStart"
    assert listCalls[0][0][0]["iStepNumber"] == 4
    assert listCalls[1][0][0]["sType"] == "stepStats"
    assert listCalls[1][0][0]["iStepNumber"] == 4


@pytest.mark.asyncio
async def test_fiRunInteractiveAndRecord_nonzero_exit():
    dictStep = {"sName": "Fail"}
    fnCallback = AsyncMock()
    dictInteractive = fdictCreateInteractiveContext()

    async def _fnFakeComplete(dictCtx):
        return 7

    with patch(
        "vaibify.gui.interactiveSteps._fiAwaitInteractiveComplete",
        side_effect=_fnFakeComplete,
    ), patch(
        "vaibify.gui.pipelineUtils._fnRecordRunStats",
    ), patch(
        "vaibify.gui.pipelineUtils._fnEmitStepResult",
        new_callable=AsyncMock,
    ) as mockEmitResult:
        iResult = await _fiRunInteractiveAndRecord(
            "dc", "cid", dictStep, 1, fnCallback, dictInteractive,
        )

    assert iResult == 7
    mockEmitResult.assert_awaited_once_with(fnCallback, 1, 7)


# -- _fsAwaitInteractiveDecision tests --


@pytest.mark.asyncio
async def test_fsAwaitInteractiveDecision_returns_response():
    dictInteractive = fdictCreateInteractiveContext()

    async def _fnSetAfterDelay():
        await asyncio.sleep(0.01)
        fnSetInteractiveResponse(dictInteractive, "skip")

    asyncio.ensure_future(_fnSetAfterDelay())
    sResult = await _fsAwaitInteractiveDecision(dictInteractive)
    assert sResult == "skip"


@pytest.mark.asyncio
async def test_fsAwaitInteractiveDecision_clears_state_before_waiting():
    dictInteractive = fdictCreateInteractiveContext()
    dictInteractive["eventResume"].set()
    dictInteractive["sResponse"] = "old"

    async def _fnSetAfterDelay():
        await asyncio.sleep(0.01)
        fnSetInteractiveResponse(dictInteractive, "resume")

    asyncio.ensure_future(_fnSetAfterDelay())
    sResult = await _fsAwaitInteractiveDecision(dictInteractive)
    assert sResult == "resume"


# -- _fiAwaitInteractiveComplete tests --


@pytest.mark.asyncio
async def test_fiAwaitInteractiveComplete_parses_exit_code():
    dictInteractive = fdictCreateInteractiveContext()

    async def _fnSetAfterDelay():
        await asyncio.sleep(0.01)
        fnSetInteractiveResponse(dictInteractive, "complete:42")

    asyncio.ensure_future(_fnSetAfterDelay())
    iResult = await _fiAwaitInteractiveComplete(dictInteractive)
    assert iResult == 42


@pytest.mark.asyncio
async def test_fiAwaitInteractiveComplete_returns_zero_for_non_complete():
    dictInteractive = fdictCreateInteractiveContext()

    async def _fnSetAfterDelay():
        await asyncio.sleep(0.01)
        fnSetInteractiveResponse(dictInteractive, "done")

    asyncio.ensure_future(_fnSetAfterDelay())
    iResult = await _fiAwaitInteractiveComplete(dictInteractive)
    assert iResult == 0


@pytest.mark.asyncio
async def test_fiAwaitInteractiveComplete_complete_zero():
    dictInteractive = fdictCreateInteractiveContext()

    async def _fnSetAfterDelay():
        await asyncio.sleep(0.01)
        fnSetInteractiveResponse(dictInteractive, "complete:0")

    asyncio.ensure_future(_fnSetAfterDelay())
    iResult = await _fiAwaitInteractiveComplete(dictInteractive)
    assert iResult == 0


# -- timeout / abandonment tests --


def test_module_constants_for_abandonment():
    assert F_INTERACTIVE_WAIT_HOURS == 24.0
    assert I_ABANDONED_EXIT_CODE == 124
    assert S_ABANDONED_SENTINEL == "abandoned:124"


def test_fsBuildAbandonedReason_mentions_hours():
    sReason = fsBuildAbandonedReason(24.0)
    assert "24" in sReason
    assert "abandoned" in sReason
    assert "h" in sReason


@pytest.mark.asyncio
async def test_fsAwaitInteractiveDecision_returns_sentinel_on_timeout():
    dictInteractive = fdictCreateInteractiveContext()
    with patch(
        "vaibify.gui.interactiveSteps.F_INTERACTIVE_WAIT_HOURS",
        1e-6,
    ):
        sResult = await _fsAwaitInteractiveDecision(dictInteractive)
    assert sResult == S_ABANDONED_SENTINEL


@pytest.mark.asyncio
async def test_fiAwaitInteractiveComplete_returns_124_on_timeout():
    dictInteractive = fdictCreateInteractiveContext()
    with patch(
        "vaibify.gui.interactiveSteps.F_INTERACTIVE_WAIT_HOURS",
        1e-6,
    ):
        iResult = await _fiAwaitInteractiveComplete(dictInteractive)
    assert iResult == I_ABANDONED_EXIT_CODE


@pytest.mark.asyncio
async def test_fiHandleInteractiveStep_emits_failure_on_abandonment():
    dictInteractive = fdictCreateInteractiveContext()
    fnCallback = AsyncMock()

    async def _fnFakeDecision(dictCtx):
        return S_ABANDONED_SENTINEL

    with patch(
        "vaibify.gui.interactiveSteps._fsAwaitInteractiveDecision",
        side_effect=_fnFakeDecision,
    ):
        iResult = await _fiHandleInteractiveStep(
            None, "cid", {"sName": "S"}, 4, fnCallback, dictInteractive,
        )

    assert iResult == I_ABANDONED_EXIT_CODE
    listTypes = [c[0][0]["sType"] for c in fnCallback.call_args_list]
    assert "interactivePause" in listTypes
    assert "interactiveAbandoned" in listTypes
    assert "stepFail" in listTypes
    dictAbandoned = next(
        c[0][0] for c in fnCallback.call_args_list
        if c[0][0]["sType"] == "interactiveAbandoned"
    )
    assert dictAbandoned["sFailureReason"].startswith(
        "interactive step abandoned"
    )
    assert dictAbandoned["iExitCode"] == I_ABANDONED_EXIT_CODE


@pytest.mark.asyncio
async def test_fiRunInteractiveAndRecord_handles_abandonment():
    dictStep = {"sName": "T"}
    fnCallback = AsyncMock()
    dictInteractive = fdictCreateInteractiveContext()

    async def _fnFakeComplete(dictCtx):
        return I_ABANDONED_EXIT_CODE

    with patch(
        "vaibify.gui.interactiveSteps._fiAwaitInteractiveComplete",
        side_effect=_fnFakeComplete,
    ), patch(
        "vaibify.gui.pipelineUtils._fnRecordRunStats",
    ), patch(
        "vaibify.gui.pipelineUtils._fnEmitStepResult",
        new_callable=AsyncMock,
    ):
        iResult = await _fiRunInteractiveAndRecord(
            "dc", "cid", dictStep, 2, fnCallback, dictInteractive,
        )

    assert iResult == I_ABANDONED_EXIT_CODE
    listTypes = [c[0][0]["sType"] for c in fnCallback.call_args_list]
    assert "interactiveAbandoned" in listTypes
