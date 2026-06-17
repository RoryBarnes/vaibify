"""Tests for the broad-except behaviour of the streaming chunk emitter.

Audit CRITICAL #4: a producer-side exception in the worker callback
(WS closed, MemoryError on an enormous line, asyncio.CancelledError,
back-pressure exception) must not tear down the whole run. The
emitter logs once, disables itself, and subsequent calls are no-ops.
"""

import asyncio
import logging
import threading

from vaibify.gui.pipelineRunner import _ffBuildStreamingChunkEmitter


def _ftupleLoopInBackground():
    """Spin a real event loop on a background thread so futures resolve."""
    loop = asyncio.new_event_loop()

    def fnRunLoop():
        asyncio.set_event_loop(loop)
        loop.run_forever()

    threadLoop = threading.Thread(target=fnRunLoop, daemon=True)
    threadLoop.start()
    return loop, threadLoop


def _fnStopLoop(loop, threadLoop):
    loop.call_soon_threadsafe(loop.stop)
    threadLoop.join(timeout=2)
    loop.close()


def _ftupleBuildEmitter(fnCallback):
    """Return (emitter, dictAccum, loop, thread) wired to a running loop."""
    loop, threadLoop = _ftupleLoopInBackground()
    dictAccum = {"fCpu": 0.0}
    fnEmit = _ffBuildStreamingChunkEmitter(fnCallback, loop, dictAccum)
    return fnEmit, dictAccum, loop, threadLoop


def test_emitter_absorbs_runtime_error_from_callback(caplog):
    """A RuntimeError (e.g. closed WS) is logged once; later chunks no-op."""
    iCalls = {"i": 0}

    async def fnCallback(dictEvent):
        iCalls["i"] += 1
        raise RuntimeError("ws closed")

    fnEmit, _, loop, threadLoop = _ftupleBuildEmitter(fnCallback)
    try:
        with caplog.at_level(logging.WARNING, logger="vaibify"):
            fnEmit("stdout", "first line")
            fnEmit("stdout", "second line")
            fnEmit("stdout", "third line")
    finally:
        _fnStopLoop(loop, threadLoop)

    assert iCalls["i"] == 1
    assert sum(
        "streaming chunk emitter disabled" in rec.message
        for rec in caplog.records
    ) == 1


def test_emitter_absorbs_memory_error(caplog):
    """Encoding a multi-megabyte scientific line must not raise to producer."""
    iCalls = {"i": 0}

    async def fnCallback(dictEvent):
        iCalls["i"] += 1
        raise MemoryError("out of memory")

    fnEmit, _, loop, threadLoop = _ftupleBuildEmitter(fnCallback)
    try:
        with caplog.at_level(logging.WARNING, logger="vaibify"):
            fnEmit("stdout", "X" * 1024)
    finally:
        _fnStopLoop(loop, threadLoop)

    assert iCalls["i"] == 1
    assert any(
        "streaming chunk emitter disabled" in rec.message
        for rec in caplog.records
    )


def test_emitter_absorbs_cancelled_error(caplog):
    """asyncio.CancelledError no longer escapes to the producer thread."""
    iCalls = {"i": 0}

    async def fnCallback(dictEvent):
        iCalls["i"] += 1
        raise asyncio.CancelledError()

    fnEmit, _, loop, threadLoop = _ftupleBuildEmitter(fnCallback)
    try:
        with caplog.at_level(logging.WARNING, logger="vaibify"):
            fnEmit("stdout", "data")
            fnEmit("stdout", "more")
    finally:
        _fnStopLoop(loop, threadLoop)

    assert iCalls["i"] == 1


def test_emitter_still_captures_cpu_line():
    """The CPU-marker absorber path is independent of the disabled flag."""
    listEvents = []

    async def fnCallback(dictEvent):
        listEvents.append(dictEvent)

    fnEmit, dictAccum, loop, threadLoop = _ftupleBuildEmitter(fnCallback)
    try:
        fnEmit("stdout", "__VAIBIFY_CPU__ 1.5 0.5")
    finally:
        _fnStopLoop(loop, threadLoop)
    assert dictAccum["fCpu"] == 2.0
    assert listEvents == []


def test_emitter_forwards_normal_line_when_callback_is_healthy():
    """A healthy callback receives every line via the batched event shape."""
    listEvents = []

    async def fnCallback(dictEvent):
        listEvents.append(dictEvent)

    fnEmit, _, loop, threadLoop = _ftupleBuildEmitter(fnCallback)
    try:
        fnEmit("stdout", "alpha")
        fnEmit("stdout", "beta")
    finally:
        _fnStopLoop(loop, threadLoop)

    assert all(d["sType"] == "outputBatch" for d in listEvents)
    listLinesAll = []
    for dictEvent in listEvents:
        listLinesAll.extend(dictEvent["listLines"])
    assert listLinesAll == ["alpha", "beta"]


def test_batching_emitter_coalesces_lines_within_window():
    """50+ lines arriving fast must collapse into one outputBatch event."""
    from vaibify.gui.pipelineRunner import _ftBuildBatchingEmitter

    listEvents = []

    async def fnCallback(dictEvent):
        listEvents.append(dictEvent)

    loop, threadLoop = _ftupleLoopInBackground()
    dictAccum = {"fCpu": 0.0}
    fnEmit, faDrain = _ftBuildBatchingEmitter(
        fnCallback, loop, dictAccum)
    try:
        for iLine in range(50):
            fnEmit("stdout", "line " + str(iLine))
        # Worker-thread path already flushed the size-threshold
        # batch; teardown drain has nothing pending to ship.
        asyncio.run_coroutine_threadsafe(
            faDrain(), loop,
        ).result()
    finally:
        _fnStopLoop(loop, threadLoop)

    assert len(listEvents) == 1
    assert listEvents[0]["sType"] == "outputBatch"
    assert len(listEvents[0]["listLines"]) == 50


def test_batching_emitter_drain_emits_partial_buffer():
    """faDrainPending must ship a sub-threshold buffer on teardown."""
    from vaibify.gui.pipelineRunner import _ftBuildBatchingEmitter

    listEvents = []

    async def fnCallback(dictEvent):
        listEvents.append(dictEvent)

    loop, threadLoop = _ftupleLoopInBackground()
    dictAccum = {"fCpu": 0.0}
    fnEmit, faDrain = _ftBuildBatchingEmitter(
        fnCallback, loop, dictAccum)
    try:
        fnEmit("stdout", "single")
        assert listEvents == []
        asyncio.run_coroutine_threadsafe(
            faDrain(), loop,
        ).result()
    finally:
        _fnStopLoop(loop, threadLoop)

    assert len(listEvents) == 1
    assert listEvents[0]["sType"] == "outputBatch"
    assert listEvents[0]["listLines"] == ["single"]


def test_batching_emitter_flushes_after_time_window():
    """A single idle line flushes on its own after F_BATCH_MAX_INTERVAL_SECONDS.

    The pre-timer implementation only checked the time window when a
    fresh line arrived, so a sporadic producer (one line then silence)
    sat in the buffer until either another line or the per-command
    teardown drain fired. The timer-driven path flushes proactively.
    """
    import time as timeModule
    from vaibify.gui.pipelineRunner import (
        _ftBuildBatchingEmitter, F_BATCH_MAX_INTERVAL_SECONDS,
    )

    listEvents = []

    async def fnCallback(dictEvent):
        listEvents.append(dictEvent)

    loop, threadLoop = _ftupleLoopInBackground()
    dictAccum = {"fCpu": 0.0}
    fnEmit, _faDrain = _ftBuildBatchingEmitter(
        fnCallback, loop, dictAccum)
    try:
        fnEmit("stdout", "first")
        # Allow the call_later timer to fire and the resulting task to
        # complete on the background loop before we tear down.
        timeModule.sleep(F_BATCH_MAX_INTERVAL_SECONDS + 0.05)
    finally:
        _fnStopLoop(loop, threadLoop)

    assert len(listEvents) == 1
    assert listEvents[0]["sType"] == "outputBatch"
    assert listEvents[0]["listLines"] == ["first"]
