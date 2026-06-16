"""Tests for the WebSocket-heartbeat side task in pipelineRunner.

The heartbeat lives inside ``_actxWebSocketHeartbeat`` and wraps every
``asyncio.to_thread`` call to the blocking docker exec, so the
in-container ``vaibify-do`` socket sees keepalive traffic at the
application layer during multi-minute commands.
"""

import asyncio
import time

import pytest
from unittest.mock import MagicMock

from vaibify.docker.dockerConnection import ExecResult
from vaibify.gui import pipelineRunner
from vaibify.gui.pipelineRunner import _ftRunSingleCommand


def _fMockCallback():
    listCaptured = []

    async def fnCallback(dictEvent):
        listCaptured.append(dictEvent)

    return fnCallback, listCaptured


def _fMockDockerSlow(fSleepSeconds, iExitCode=0, sOutput=""):
    """Mock the streaming exec to sleep before returning."""
    mockDocker = MagicMock()

    def fnSlowStream(
        sContainerId, sCommand, fnEmitChunk,
        sWorkdir=None, sUser=None,
    ):
        time.sleep(fSleepSeconds)
        for sLine in sOutput.splitlines():
            fnEmitChunk("stdout", sLine)
        return ExecResult(
            iExitCode=iExitCode, sStdout=sOutput, sStderr="",
        )

    mockDocker.texecRunInContainerStreamedWithChunks.side_effect = (
        fnSlowStream
    )
    return mockDocker


def test_heartbeat_emitted_while_command_runs(monkeypatch):
    """A long command produces multiple wsHeartbeat events.

    The blocking docker exec is simulated with ``time.sleep`` inside
    the worker thread; the event loop is free to fire the heartbeat
    task while the to_thread call is parked.
    """
    monkeypatch.setattr(
        pipelineRunner, "F_WS_HEARTBEAT_INTERVAL", 0.05,
    )
    mockDocker = _fMockDockerSlow(0.3)
    fnCallback, listCaptured = _fMockCallback()

    asyncio.run(_ftRunSingleCommand(
        mockDocker, "cid", "cmd", "cmd", "/work", fnCallback,
    ))

    listBeats = [
        d for d in listCaptured if d.get("sType") == "wsHeartbeat"
    ]
    assert len(listBeats) >= 2, (
        f"expected >=2 wsHeartbeat events; got {len(listBeats)}: "
        f"{listCaptured}"
    )
    for dictBeat in listBeats:
        assert "fEpoch" in dictBeat
        assert isinstance(dictBeat["fEpoch"], float)


def test_heartbeat_silent_for_instant_command(monkeypatch):
    """If the command finishes before the first interval, no beat fires."""
    monkeypatch.setattr(
        pipelineRunner, "F_WS_HEARTBEAT_INTERVAL", 10.0,
    )
    mockDocker = _fMockDockerSlow(0.0)
    fnCallback, listCaptured = _fMockCallback()

    asyncio.run(_ftRunSingleCommand(
        mockDocker, "cid", "cmd", "cmd", "/work", fnCallback,
    ))

    listBeats = [
        d for d in listCaptured if d.get("sType") == "wsHeartbeat"
    ]
    assert listBeats == []


def test_run_single_command_streams_output_progressively():
    """An output line arrives before the streaming exec returns.

    ``fnEmitChunk`` schedules each callback on the event loop via
    ``run_coroutine_threadsafe`` and blocks the worker thread until it
    finishes. So by the time the worker reaches its second
    ``fnEmitChunk`` call, the first line must already have been
    appended to ``listEvents`` by the loop's task queue.
    """
    listEvents = []
    dictWitness = {"iSeenWhenSecondQueued": -1}

    async def fnCallback(dictEvent):
        listEvents.append(dictEvent)

    mockDocker = MagicMock()

    def fnStreaming(
        sContainerId, sCommand, fnEmitChunk,
        sWorkdir=None, sUser=None,
    ):
        fnEmitChunk("stdout", "first")
        dictWitness["iSeenWhenSecondQueued"] = len(listEvents)
        fnEmitChunk("stdout", "second")
        return ExecResult(
            iExitCode=0, sStdout="first\nsecond", sStderr="",
        )

    mockDocker.texecRunInContainerStreamedWithChunks.side_effect = (
        fnStreaming
    )
    asyncio.run(_ftRunSingleCommand(
        mockDocker, "cid", "cmd", "cmd", "/work", fnCallback,
    ))
    listOutputs = [
        d["sLine"] for d in listEvents if d.get("sType") == "output"
    ]
    iIndexFirst = listOutputs.index("first")
    iIndexSecond = listOutputs.index("second")
    assert iIndexFirst < iIndexSecond
    # ``iSeenWhenSecondQueued`` is the size of the captured-event list
    # at the instant the worker thread queued the second chunk. It
    # must be strictly greater than the size before the first emit
    # call, which proves the first emit landed before the second was
    # queued — i.e. delivery was progressive rather than batched.
    assert (
        dictWitness["iSeenWhenSecondQueued"]
        > 0
        and any(
            d.get("sLine") == "first"
            for d in listEvents[:dictWitness["iSeenWhenSecondQueued"]]
        )
    ), (
        "first output event was not dispatched before the second "
        "fnEmitChunk call returned to the worker thread"
    )


def test_heartbeat_callback_exception_does_not_break_command(monkeypatch):
    """A failing callback inside the heartbeat must not kill the command.

    Post-R2 the heartbeat loop logs and continues on every send
    failure rather than returning on the first one, so an
    intermittently flaky WebSocket no longer permanently disables
    keep-alives for the rest of a multi-minute command.
    """
    monkeypatch.setattr(
        pipelineRunner, "F_WS_HEARTBEAT_INTERVAL", 0.05,
    )
    iBeatCallCount = {"i": 0}

    async def fnCallback(dictEvent):
        if dictEvent.get("sType") == "wsHeartbeat":
            iBeatCallCount["i"] += 1
            raise RuntimeError("transient send failure")

    mockDocker = _fMockDockerSlow(0.3, iExitCode=0, sOutput="done\n")

    iResult, fCpu = asyncio.run(_ftRunSingleCommand(
        mockDocker, "cid", "cmd", "cmd", "/work", fnCallback,
    ))
    assert iResult == 0
    # Log-and-continue: multiple beats fire even though every one
    # raises. (Pre-R2 the loop returned on the first failure, so
    # only one was observed.)
    assert iBeatCallCount["i"] >= 2, (
        f"expected log-and-continue, got {iBeatCallCount['i']} beats"
    )
