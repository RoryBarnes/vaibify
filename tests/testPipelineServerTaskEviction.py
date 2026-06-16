"""Tests for the dictPipelineTasks done-callback eviction (audit MEDIUM #19).

Without the callback, every completed-normally task lingered in the
dict forever — a memory leak proportional to the number of pipeline
runs the GUI saw over its lifetime. The fix wires
``task.add_done_callback`` so an entry self-evicts when the task ends.
"""

import asyncio

import pytest

from vaibify.gui.pipelineServer import _fnRegisterPipelineTask


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
