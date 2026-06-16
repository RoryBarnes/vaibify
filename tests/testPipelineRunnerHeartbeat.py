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


def test_run_single_command_drains_pending_batch_on_teardown():
    """All output lines reach the callback by the time the command returns.

    The coalescing emitter introduced in the 2026-06 network slice
    buffers small line bursts to collapse the per-line WS frame
    overhead. The wire contract still requires that the per-command
    teardown drains the buffer before the run progresses — otherwise
    log lines emitted by a short command would be silently dropped.
    """
    listEvents = []

    async def fnCallback(dictEvent):
        listEvents.append(dictEvent)

    mockDocker = MagicMock()

    def fnStreaming(
        sContainerId, sCommand, fnEmitChunk,
        sWorkdir=None, sUser=None,
    ):
        fnEmitChunk("stdout", "first")
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
    listAllLines = []
    for dictEvent in listEvents:
        if dictEvent.get("sType") == "outputBatch":
            listAllLines.extend(dictEvent.get("listLines", []))
        elif dictEvent.get("sType") == "output":
            listAllLines.append(dictEvent["sLine"])
    assert "first" in listAllLines
    assert "second" in listAllLines
    assert listAllLines.index("first") < listAllLines.index("second")


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
