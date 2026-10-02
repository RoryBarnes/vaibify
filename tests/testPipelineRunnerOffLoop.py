"""A pipeline run's container I/O never executes on the hub's event loop.

Each test records the thread a blocking call runs on and compares it
with the thread driving the event loop. A call on the loop's own thread
freezes every other route, every project's polls, and the WebSocket
heartbeat for as long as the container takes to answer.
"""

import asyncio
import threading
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from vaibify.gui import pipelineLogger, pipelineRunner


class RecordingThreads:
    """Remembers the thread each named blocking call ran on."""

    def __init__(self):
        self.dictThreadByCall = {}

    def fnRecord(self, sCallName):
        self.dictThreadByCall[sCallName] = threading.get_ident()

    def fbRanOffThe(self, iLoopThread, sCallName):
        return self.dictThreadByCall[sCallName] != iLoopThread


def _fnRunOnLoop(fcoroutineFactory):
    """Run a coroutine factory on a fresh loop; return (result, loop thread)."""
    async def fcoroutineRunAndNoteThread():
        return await fcoroutineFactory(), threading.get_ident()

    return asyncio.run(fcoroutineRunAndNoteThread())


@pytest.mark.falsification
def testPreflightChecksRunOffTheEventLoop():
    """Kills: running the preflight exec walk inline on the event loop."""
    recorder = RecordingThreads()
    dictWorkflow = {
        "sWorkflowName": "w",
        "listSteps": [{"sName": "A", "sStepId": "step-a", "sDirectory": "a"}],
    }

    def fnValidateDirectory(*tArguments):
        recorder.fnRecord("directory")

    def fnValidateCommands(*tArguments):
        recorder.fnRecord("commands")

    with patch.object(
        pipelineRunner, "_fnValidateStepDirectory", fnValidateDirectory,
    ), patch.object(
        pipelineRunner, "_fnValidateStepCommands", fnValidateCommands,
    ):
        listErrors, iLoopThread = _fnRunOnLoop(
            lambda: pipelineRunner._flistPreflightValidate(
                MagicMock(), "cid", dictWorkflow, {},
            )
        )
    assert listErrors == []
    assert recorder.fbRanOffThe(iLoopThread, "directory")
    assert recorder.fbRanOffThe(iLoopThread, "commands")


@pytest.mark.falsification
def testPreflightWarningsAreCollectedOffTheEventLoop():
    """Kills: the disk-space and conftest probes running on the loop."""
    recorder = RecordingThreads()
    listEvents = []

    def flistCollectWarnings(*tArguments):
        recorder.fnRecord("warnings")
        return ["low disk"]

    async def fnStatusCallback(dictEvent):
        listEvents.append(dictEvent)

    with patch.object(
        pipelineRunner, "_ftPrepareLogAndVariables",
        AsyncMock(return_value=("/log", [], AsyncMock(), {})),
    ), patch.object(
        pipelineRunner, "_flistPreflightValidate", AsyncMock(return_value=[]),
    ), patch.object(
        pipelineRunner, "_flistCollectPreflightWarnings", flistCollectWarnings,
    ), patch.object(
        pipelineRunner, "_fiRunStepsAndLog", AsyncMock(return_value=0),
    ):
        iResult, iLoopThread = _fnRunOnLoop(
            lambda: pipelineRunner._fiRunWithLogging(
                MagicMock(), "cid", {"listSteps": []}, "/work",
                fnStatusCallback, "runAll",
            )
        )
    assert iResult == 0
    assert recorder.fbRanOffThe(iLoopThread, "warnings")
    assert {"sType": "preflightWarning", "sMessage": "low disk"} in listEvents


def _fdictRunTeardown(recorder):
    """Drive ``_fiRunStepsAndLog`` with recording threads; return the bounds."""
    dictBounds = {}

    class FakeHeartbeatThread:
        def join(self, timeout=None):
            recorder.fnRecord("heartbeat")
            dictBounds["heartbeat"] = timeout

    class FakeWriter:
        def fnStop(self, fJoinTimeoutSeconds=None):
            recorder.fnRecord("writer")
            dictBounds["writer"] = fJoinTimeoutSeconds

    with patch.object(
        pipelineRunner, "_ftInitializeRunState",
        return_value=({}, FakeWriter()),
    ), patch.object(
        pipelineRunner, "_fthreadStartHeartbeat",
        return_value=FakeHeartbeatThread(),
    ), patch.object(
        pipelineRunner, "_ffBuildFlushingCallback",
        return_value=AsyncMock(),
    ), patch.object(
        pipelineRunner, "_fiRunStepList", AsyncMock(return_value=0),
    ), patch.object(
        pipelineRunner, "_fnFinalizeRun", AsyncMock(),
    ):
        iResult, iLoopThread = _fnRunOnLoop(
            lambda: pipelineRunner._fiRunStepsAndLog(
                MagicMock(), "cid", {"listSteps": []}, "/work", {},
                AsyncMock(), AsyncMock(), "/log", [], "runAll", 1,
            )
        )
    assert iResult == 0
    return dictBounds, iLoopThread


@pytest.mark.falsification
def testTheRunTeardownJoinsAreOffTheEventLoop():
    """Kills: joining the heartbeat and writer threads on the event loop."""
    recorder = RecordingThreads()
    _, iLoopThread = _fdictRunTeardown(recorder)
    assert recorder.fbRanOffThe(iLoopThread, "heartbeat")
    assert recorder.fbRanOffThe(iLoopThread, "writer")


@pytest.mark.falsification
def testTheRunTeardownJoinsAreBounded():
    """Kills: an unbounded wait on a writer a wedged container never drains."""
    dictBounds, _ = _fdictRunTeardown(RecordingThreads())
    assert dictBounds["heartbeat"] is not None
    assert dictBounds["writer"] is not None


def _fnRunFinalize(recorder, fbFlushLands=True):
    """Drive ``_fnFinalizeRun`` with a recording writer; return the events."""
    listEvents = []

    class FakeWriter:
        def fnEnqueueUpdate(self, dictUpdate):
            pass

        def fbFlushTerminalStateAcknowledged(self, dictTerminalUpdate):
            recorder.fnRecord("flush")
            return fbFlushLands

    def fdictPersist(*tArguments):
        recorder.fnRecord("persist")
        return {"bPersisted": True, "sDetail": ""}

    async def fnStatusCallback(dictEvent):
        listEvents.append(dictEvent)

    with patch.object(
        pipelineLogger, "fnWriteLogToContainer", AsyncMock(),
    ), patch.object(
        pipelineLogger, "_fdictPersistRunResultsToState", fdictPersist,
    ):
        _, iLoopThread = _fnRunOnLoop(
            lambda: pipelineLogger._fnFinalizeRun(
                MagicMock(), "cid", {}, 0, "/log", [], {"listSteps": []},
                "/workspace/p/.vaibify/workflows/w.json", fnStatusCallback,
                stateWriter=FakeWriter(),
            )
        )
    return listEvents, iLoopThread


@pytest.mark.falsification
def testTheCompletionMergeRunsOffTheEventLoop():
    """Kills: the run-result merge (a blocking flock) on the event loop."""
    recorder = RecordingThreads()
    listEvents, iLoopThread = _fnRunFinalize(recorder)
    assert recorder.fbRanOffThe(iLoopThread, "persist")
    assert listEvents[-1]["sType"] == "completed"


@pytest.mark.falsification
def testTheAcknowledgedTerminalFlushRunsOffTheEventLoop():
    """Kills: waiting up to 120 s for the terminal flush on the loop."""
    recorder = RecordingThreads()
    listEvents, iLoopThread = _fnRunFinalize(recorder)
    assert recorder.fbRanOffThe(iLoopThread, "flush")
    assert listEvents[-1]["bRunMetadataPersisted"] is True


def testAFailedTerminalFlushStillReportsTheRunUnrecorded():
    recorder = RecordingThreads()
    listEvents, _ = _fnRunFinalize(recorder, fbFlushLands=False)
    assert listEvents[-1]["bRunMetadataPersisted"] is False
