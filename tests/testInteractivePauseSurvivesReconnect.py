"""An interactive pause belongs to its run and survives a socket reconnect.

A run outlives the WebSocket that started it. The interactive context
used to be created per socket: a Resume or Skip sent on the socket a
reloaded dashboard opened set an event nobody awaited, the pause was
never shown to the new socket, and the run held its project for the full
day it waits before abandoning. A second socket (an agent's
``vaibify-do run-*``) also displaced the slot the terminal route reads,
so a dead terminal's abnormal-exit signal reached the wrong context.
Now the context is per run, replayed to a new socket, and answered by
whichever socket the researcher uses.
"""

import asyncio
from unittest.mock import MagicMock, patch

import pytest
from fastapi import WebSocketDisconnect

from vaibify.gui import interactiveSteps, pipelineServer
from tests.testProjectScopedAgentActions import _FakeRunSocket

S_CONTAINER_ID = "cid-interactive-0001"
S_PAUSED_STEP_NAME = "Inspect the instrument output"
F_SCENARIO_WAIT_SECONDS = 10.0


class PausedRunScenario:
    """One run paused on a first socket, then a second socket opening.

    Built inside the running loop: an ``asyncio.Event`` binds to the loop
    that is current when it is created, on every Python this supports.
    """

    def __init__(self):
        self.dictOutcome = {}
        self.eventPaused = asyncio.Event()
        self.eventRunFinished = asyncio.Event()

    async def fnPausingDispatch(self, sAction, *tArguments, **dictKeywords):
        fnCallback = tArguments[6]
        dictInteractive = dictKeywords["dictInteractive"]
        await interactiveSteps._fnEmitInteractivePause(
            fnCallback, 1, {"sName": S_PAUSED_STEP_NAME}, dictInteractive)
        self.eventPaused.set()
        try:
            self.dictOutcome["sResponse"] = (
                await interactiveSteps._fsAwaitInteractiveDecision(
                    dictInteractive))
        finally:
            interactiveSteps._fnClearAwaiting(dictInteractive)
            self.eventRunFinished.set()

    async def fnLoop(self, websocket):
        with pytest.raises(WebSocketDisconnect):
            await pipelineServer.fnPipelineMessageLoop(
                websocket, MagicMock(), S_CONTAINER_ID,
                {"sWorkflowName": "w", "listSteps": []},
                {S_CONTAINER_ID: "/workspace/p/.vaibify/workflows/w.json"},
                "/workspace/p",
            )

    async def fnStartThePausedRun(self):
        await self.fnLoop(_FakeRunSocket([{"sAction": "runAll"}]))
        await asyncio.wait_for(
            self.eventPaused.wait(), F_SCENARIO_WAIT_SECONDS)

    async def fnReleaseTheRun(self):
        dictWaiting = pipelineServer.fdictInteractiveContextForContainer(
            S_CONTAINER_ID)
        if dictWaiting is not None:
            interactiveSteps.fnSetInteractiveResponse(dictWaiting, "skip")
        await asyncio.wait_for(
            self.eventRunFinished.wait(), F_SCENARIO_WAIT_SECONDS)
        for _ in range(3):
            await asyncio.sleep(0)


def _fnRunScenario(fcoroutineBody):
    listScenarios = []

    async def fnDrive():
        scenario = PausedRunScenario()
        listScenarios.append(scenario)
        with patch.object(
            pipelineServer, "fnDispatchAction", scenario.fnPausingDispatch,
        ):
            await fcoroutineBody(scenario)

    asyncio.run(fnDrive())
    return listScenarios[0]


@pytest.mark.falsification
def testAResumeOnAReconnectedSocketReachesThePausedRun():
    """Kills: a context per socket, which a second socket's frames miss."""
    websocketSecond = _FakeRunSocket([{"sAction": "interactiveResume"}])

    async def fnBody(scenario):
        await scenario.fnStartThePausedRun()
        await scenario.fnLoop(websocketSecond)
        await asyncio.wait_for(
            scenario.eventRunFinished.wait(), F_SCENARIO_WAIT_SECONDS)
        for _ in range(3):
            await asyncio.sleep(0)

    scenario = _fnRunScenario(fnBody)
    assert scenario.dictOutcome["sResponse"] == "resume"


@pytest.mark.falsification
def testARunsContextIsDroppedWhenItsRunEnds():
    """Kills: leaving a finished run's context in the registry for good."""
    async def fnBody(scenario):
        await scenario.fnStartThePausedRun()
        assert S_CONTAINER_ID in (
            pipelineServer.DICT_INTERACTIVE_CONTEXTS_BY_CONTAINER)
        await scenario.fnReleaseTheRun()

    _fnRunScenario(fnBody)
    assert S_CONTAINER_ID not in (
        pipelineServer.DICT_INTERACTIVE_CONTEXTS_BY_CONTAINER)


@pytest.mark.falsification
def testAReconnectedSocketIsToldWhatTheRunIsWaitingFor():
    """Kills: never replaying a pending pause to a socket that opens later."""
    websocketSecond = _FakeRunSocket([])

    async def fnBody(scenario):
        await scenario.fnStartThePausedRun()
        await scenario.fnLoop(websocketSecond)
        await scenario.fnReleaseTheRun()

    _fnRunScenario(fnBody)
    [dictReplayed] = websocketSecond.listSent
    assert dictReplayed["sType"] == "interactivePause"
    assert dictReplayed["sStepName"] == S_PAUSED_STEP_NAME
    assert dictReplayed["bReplayed"] is True
    assert dictReplayed["sRunId"]


@pytest.mark.falsification
def testAPausedRunStaysReachableAfterEverySocketHasClosed():
    """Kills: a slot a later socket's end can empty, losing the context."""
    dictSeen = {}

    async def fnBody(scenario):
        await scenario.fnStartThePausedRun()
        await scenario.fnLoop(_FakeRunSocket([]))
        dictSeen["dictWaiting"] = (
            pipelineServer.fdictInteractiveContextForContainer(
                S_CONTAINER_ID))
        await scenario.fnReleaseTheRun()

    _fnRunScenario(fnBody)
    assert dictSeen["dictWaiting"] is not None
    assert dictSeen["dictWaiting"]["dictPendingEvent"] is None


@pytest.mark.asyncio
@pytest.mark.falsification
async def testTwoPausedRunsInOneContainerAreAnsweredByRunId():
    """Kills: one slot per container, which the second run overwrites."""
    dictFirst = interactiveSteps.fdictCreateInteractiveContext("run-first")
    dictSecond = interactiveSteps.fdictCreateInteractiveContext("run-second")
    for dictRun in (dictFirst, dictSecond):
        interactiveSteps._fnMarkAwaiting(dictRun, {"sType": "interactivePause"})
        pipelineServer._fnPublishInteractiveContext(S_CONTAINER_ID, dictRun)
    try:
        assert pipelineServer.fdictInteractiveContextForContainer(
            S_CONTAINER_ID, "run-first") is dictFirst
        assert pipelineServer.fdictInteractiveContextForContainer(
            S_CONTAINER_ID, "run-second") is dictSecond
        assert pipelineServer.fdictInteractiveContextForContainer(
            S_CONTAINER_ID) is dictSecond
    finally:
        pipelineServer._fnUnpublishInteractiveContext(
            S_CONTAINER_ID, dictFirst)
        pipelineServer._fnUnpublishInteractiveContext(
            S_CONTAINER_ID, dictSecond)
    assert S_CONTAINER_ID not in (
        pipelineServer.DICT_INTERACTIVE_CONTEXTS_BY_CONTAINER)


@pytest.mark.asyncio
@pytest.mark.falsification
async def testAContextWaitsOnlyWhileItsPauseIsUnanswered():
    """Kills: leaving the pause pending after the run was answered."""
    dictRun = interactiveSteps.fdictCreateInteractiveContext("run-x")
    interactiveSteps._fnMarkAwaiting(dictRun, {"sType": "interactivePause"})
    pipelineServer._fnPublishInteractiveContext(S_CONTAINER_ID, dictRun)
    try:
        assert pipelineServer.fdictInteractiveContextForContainer(
            S_CONTAINER_ID) is dictRun
        interactiveSteps._fnClearAwaiting(dictRun)
        assert pipelineServer.fdictInteractiveContextForContainer(
            S_CONTAINER_ID) is None
    finally:
        pipelineServer._fnUnpublishInteractiveContext(S_CONTAINER_ID, dictRun)


def testAFrameThatAnswersNoWaitingRunIsDroppedQuietly():
    websocketOnly = _FakeRunSocket([
        {"sAction": "interactiveResume"},
        {"sAction": "interactiveSkip"},
        {"sAction": "interactiveComplete", "iExitCode": 0},
    ])

    async def fnDrive():
        with pytest.raises(WebSocketDisconnect):
            await pipelineServer.fnPipelineMessageLoop(
                websocketOnly, MagicMock(), S_CONTAINER_ID,
                {"sWorkflowName": "w", "listSteps": []}, {}, "/workspace/p",
            )

    asyncio.run(fnDrive())
    assert websocketOnly.listSent == []
