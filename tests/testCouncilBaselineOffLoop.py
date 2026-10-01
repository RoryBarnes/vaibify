"""The council's baseline-confirmation command never blocks the event loop.

A confirmed baseline claim makes the ENGINE re-run the model-supplied
command in a fresh sandbox. That is a container round trip of unbounded
length, awaited from inside an async turn; run inline it froze every
route and every project's polls until the command ended. It runs on a
worker thread under the campaign's own per-turn wall clock.
"""

import asyncio
import threading
from unittest.mock import patch

import pytest

from vaibify.gui import agentCouncilController as controller
from vaibify.gui import agentCouncilProviders, agentCouncilRunner
from tests.agentCouncilHarness import (
    fdictDecideCompleted,
    fdictMakeTurnResult,
    fixtureBuildCouncil,
)

LIST_TWO_SPECS = [
    {"sHandle": "A", "sProvider": "prov-a", "sRequestedModel": "model-a"},
    {"sHandle": "B", "sProvider": "prov-b", "sRequestedModel": "model-b"},
]
F_LOOP_TURN_WAIT_SECONDS = 10.0


def _fnDecideWithOneBaselineClaim(sHandle, dictRequest):
    listEvidence = [{"sStatus": "confirmed", "sStateForm": "baseline",
                     "sCommandText": "run the check"}]
    if sHandle == "A" and dictRequest["sPhase"] == "independentProposals":
        return fdictDecideCompleted(
            fdictMakeTurnResult("accept", listEvidence=listEvidence))
    return fdictDecideCompleted(fdictMakeTurnResult("accept"))


@pytest.mark.falsification
def testTheBaselineCommandRunsWhileTheEventLoopKeepsTurning():
    """Kills: running the baseline command inline in the async turn.

    The command blocks until a coroutine on the SAME loop has run. On the
    loop's own thread it can never see that coroutine run, so it times
    out; on a worker thread the loop turns and the event is set.
    """
    eventLoopTurned = threading.Event()
    dictOutcome = {}

    def fdictExecuteBaseline(dictRequest):
        dictOutcome["bLoopTurnedMeanwhile"] = eventLoopTurned.wait(
            F_LOOP_TURN_WAIT_SECONDS)
        return {"sSnapshotHash": "baseline-snapshot-hash-0001",
                "sExecutionImageIdentity": "image-sha256-abc",
                "iExitCode": 0, "sOutputDigest": "digest-0001"}

    fixture = fixtureBuildCouncil(
        LIST_TWO_SPECS, _fnDecideWithOneBaselineClaim,
        sChairbotHandle="A", ffnBaselineExecute=fdictExecuteBaseline)

    async def fnTurnTheLoop():
        await asyncio.sleep(0)
        eventLoopTurned.set()

    async def fnDriveAlongsideTheLoop():
        await asyncio.gather(
            fixture.engine.fdictRunUntilBlocked(), fnTurnTheLoop())

    asyncio.run(fnDriveAlongsideTheLoop())
    assert dictOutcome["bLoopTurnedMeanwhile"] is True


def testAFailingBaselineCommandStillRevertsTheClaim():
    def fdictExecuteBaseline(dictRequest):
        raise RuntimeError("sandbox lost")

    fixture = fixtureBuildCouncil(
        LIST_TWO_SPECS, _fnDecideWithOneBaselineClaim,
        sChairbotHandle="A", ffnBaselineExecute=fdictExecuteBaseline)
    fixture.fdictDrive()
    listReversions = [
        dictEvent for dictEvent in fixture.listEvents
        if dictEvent.get("sEventKind") == "confirmedClaimReverted"]
    assert len(listReversions) == 1
    assert listReversions[0]["sReason"].startswith("baselineExecutorFailed")


def _fdictRuntimeWithSettings(dictSettings):
    return {
        "sCampaignId": "campaignAlpha",
        "sImageReference": "imageAlpha",
        "sSnapshotIdentity": "snapshotAlpha",
        "baSnapshotTar": b"",
        "fdictExecuteBaselineEvidence": None,
        "dictGateway": {},
        "dictCampaign": {"dictSettings": dictSettings},
    }


def _fWallClockHandedToTheExecutorBuilder(dictRuntime):
    dictSeen = {}

    def ffnBuildExecutor(*tArguments, **dictKeywords):
        dictSeen.update(dictKeywords)
        return lambda dictRequest: {}

    with patch.object(
        agentCouncilProviders, "ffnBuildBaselineEvidenceExecutor",
        ffnBuildExecutor,
    ), patch.object(
        controller, "_fdictEnsureRuntimeGateway", lambda dictRuntimeArg: {},
    ):
        controller._fdictExecuteBaselineEvidenceLazily(
            dictRuntime, {"sCommandText": "true"})
    return dictSeen.get("fWallClockSeconds")


@pytest.mark.falsification
def testTheBaselineExecutorRunsUnderTheCampaignsTurnWallClock():
    """Kills: building the executor without the campaign's wall clock."""
    dictRuntime = _fdictRuntimeWithSettings({"iTurnWallClockSeconds": 1234})
    assert _fWallClockHandedToTheExecutorBuilder(dictRuntime) == 1234.0


def testWithoutACampaignSettingTheBaselineUsesTheModuleDefault():
    dictRuntime = _fdictRuntimeWithSettings({})
    assert _fWallClockHandedToTheExecutorBuilder(dictRuntime) == (
        agentCouncilRunner.F_DEFAULT_TURN_WALL_CLOCK_SECONDS)
