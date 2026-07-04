"""Tests for the dictPipelineTasks done-callback eviction (audit MEDIUM #19)
and the one-live-pipeline-action dispatch guard.

Without the callback, every completed-normally task lingered in the
dict forever — a memory leak proportional to the number of pipeline
runs the GUI saw over its lifetime. The fix wires
``task.add_done_callback`` so an entry self-evicts when the task ends.

The dispatch guard is the lane-independent run-exclusivity guarantee:
before it, a second ``runSelected`` for a container whose run was still
live simply started a concurrent run and overwrote the kill switch —
the WebSocket connection budget was the only (and lane-skippable)
protection.
"""

import asyncio
from unittest.mock import patch

import pytest
from starlette.websockets import WebSocketDisconnect

from vaibify.gui.pipelineServer import (
    _fbRefuseWhilePipelineTaskLive,
    _fdictBusyRefusalEvent,
    _fnRegisterPipelineTask,
    fnPipelineMessageLoop,
)


@pytest.mark.asyncio
async def test_completed_task_self_evicts():
    """A task that finishes normally is removed from dictPipelineTasks."""
    dictPipelineTasks = {}

    async def fnTinyJob():
        return "done"

    task = asyncio.create_task(fnTinyJob())
    _fnRegisterPipelineTask(dictPipelineTasks, "cid-1", task)
    assert dictPipelineTasks["cid-1"] is task
    await task
    # The done-callback runs on the next event-loop tick.
    await asyncio.sleep(0)
    assert "cid-1" not in dictPipelineTasks


@pytest.mark.asyncio
async def test_failed_task_self_evicts():
    """A task that raises also self-evicts."""
    dictPipelineTasks = {}

    async def fnFailing():
        raise RuntimeError("simulated runner failure")

    task = asyncio.create_task(fnFailing())
    _fnRegisterPipelineTask(dictPipelineTasks, "cid-2", task)
    with pytest.raises(RuntimeError):
        await task
    await asyncio.sleep(0)
    assert "cid-2" not in dictPipelineTasks


@pytest.mark.asyncio
async def test_cancelled_task_self_evicts():
    """Even a cancelled task is reaped from the dict."""
    dictPipelineTasks = {}

    async def fnHangs():
        await asyncio.sleep(10)

    task = asyncio.create_task(fnHangs())
    _fnRegisterPipelineTask(dictPipelineTasks, "cid-3", task)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    await asyncio.sleep(0)
    assert "cid-3" not in dictPipelineTasks


@pytest.mark.asyncio
async def test_new_task_overwrites_then_callback_does_not_evict_it():
    """A second run for the same container must not be evicted by the first."""
    dictPipelineTasks = {}

    async def fnQuick():
        return None

    async def fnSlow():
        await asyncio.sleep(0.05)
        return None

    taskFirst = asyncio.create_task(fnQuick())
    _fnRegisterPipelineTask(dictPipelineTasks, "cid-4", taskFirst)
    await taskFirst
    # Before the done-callback for taskFirst fires, register a new task.
    taskSecond = asyncio.create_task(fnSlow())
    _fnRegisterPipelineTask(dictPipelineTasks, "cid-4", taskSecond)
    # Let the first task's done-callback run; it must see that
    # dictPipelineTasks["cid-4"] is no longer taskFirst and leave the
    # taskSecond entry intact.
    await asyncio.sleep(0)
    assert dictPipelineTasks.get("cid-4") is taskSecond
    await taskSecond
    await asyncio.sleep(0)
    assert "cid-4" not in dictPipelineTasks


# -- one-live-pipeline-action dispatch guard ------------------------------


@pytest.mark.asyncio
async def test_refuse_helper_is_true_only_for_a_live_task():
    """The guard fires for a live task, never for absent/done/None."""
    assert _fbRefuseWhilePipelineTaskLive(None, "cid-a") is False
    assert _fbRefuseWhilePipelineTaskLive({}, "cid-a") is False

    async def fnQuick():
        return None

    taskDone = asyncio.create_task(fnQuick())
    await taskDone
    assert _fbRefuseWhilePipelineTaskLive(
        {"cid-a": taskDone}, "cid-a",
    ) is False

    async def fnHangs():
        await asyncio.sleep(10)

    taskLive = asyncio.create_task(fnHangs())
    assert _fbRefuseWhilePipelineTaskLive(
        {"cid-a": taskLive}, "cid-a",
    ) is True
    taskLive.cancel()
    with pytest.raises(asyncio.CancelledError):
        await taskLive


def test_busy_refusal_event_names_the_action_and_steps():
    """The refusal event is honest: type, action, and the refused steps."""
    dictEvent = _fdictBusyRefusalEvent(
        "runSelected", {"listStepIndices": [10]},
    )
    assert dictEvent["sType"] == "runRefused"
    assert dictEvent["sAction"] == "runSelected"
    assert dictEvent["listStepIndices"] == [10]
    assert "already" in dictEvent["sMessage"]


class _FakeDispatchWebSocket:
    """Feed scripted client messages; record everything the server sends."""

    def __init__(self, listMessages):
        self._listMessages = list(listMessages)
        self.listSent = []

    async def receive_text(self):
        if self._listMessages:
            return self._listMessages.pop(0)
        raise WebSocketDisconnect(code=1000)

    async def send_json(self, dictEvent):
        self.listSent.append(dictEvent)


@pytest.mark.asyncio
@pytest.mark.falsification
async def test_second_run_while_first_is_live_is_refused_not_started():
    """A run dispatched while another is live gets runRefused, starts nothing.

    Drives the real message loop with two back-to-back runSelected
    messages while the first dispatch is still blocked mid-run: the
    second must produce a ``runRefused`` event, must not spawn a second
    task, and must not overwrite the first run's kill switch.

    Kills: neutering _fbRefuseWhilePipelineTaskLive to `return False`
    (the pre-guard behavior: concurrent runs race in one container and
    the kill switch points at the wrong task).
    """
    import json
    from vaibify.gui import pipelineServer

    eventRelease = asyncio.Event()
    listDispatched = []

    async def fnBlockedDispatch(sAction, *args, **kwargs):
        listDispatched.append(sAction)
        await eventRelease.wait()

    websocketFake = _FakeDispatchWebSocket([
        json.dumps({"sAction": "runSelected", "listStepIndices": [10]}),
        json.dumps({"sAction": "runSelected", "listStepIndices": [10]}),
    ])
    dictPipelineTasks = {}
    with patch.object(
        pipelineServer, "fnDispatchAction", fnBlockedDispatch,
    ):
        with pytest.raises(WebSocketDisconnect):
            await fnPipelineMessageLoop(
                websocketFake, None, "cid-busy",
                {}, {}, "/workspace",
                dictPipelineTasks=dictPipelineTasks,
            )
        taskFirst = dictPipelineTasks.get("cid-busy")
        assert taskFirst is not None and not taskFirst.done(), (
            "the first run must still be live and still be the "
            "registered kill switch"
        )
        listRefusals = [
            dictEvent for dictEvent in websocketFake.listSent
            if dictEvent.get("sType") == "runRefused"
        ]
        assert len(listRefusals) == 1, (
            "the second dispatch must be refused with a runRefused event"
        )
        assert listRefusals[0]["listStepIndices"] == [10]
        eventRelease.set()
        await taskFirst
        await asyncio.sleep(0)
        assert "cid-busy" not in dictPipelineTasks
    assert listDispatched == ["runSelected"], (
        "exactly one dispatch may reach the runner; the refused one "
        "must never start"
    )


@pytest.mark.asyncio
async def test_safedispatch_tags_log_with_container_id(caplog):
    """The error log record carries sContainerId so the ring captures it."""
    import logging
    from vaibify.gui import pipelineServer

    async def fnRaise(*args, **kwargs):
        raise RuntimeError("dispatch failure for diagnosis")

    listCallbacks = []

    async def fnFakeCallback(dictEvent):
        listCallbacks.append(dictEvent)

    with caplog.at_level(logging.ERROR, logger="vaibify"):
        from unittest.mock import patch
        with patch.object(
            pipelineServer, "fnDispatchAction", side_effect=fnRaise,
        ):
            await pipelineServer._fnSafeDispatch(
                "runAll", {}, None, "cid-tag-test",
                None, {}, "/workspace", fnFakeCallback, None,
            )
    listMatching = [
        record for record in caplog.records
        if "Pipeline action 'runAll' failed" in record.getMessage()
    ]
    assert listMatching, "expected error log not emitted"
    assert getattr(listMatching[0], "sContainerId", "") == "cid-tag-test"
