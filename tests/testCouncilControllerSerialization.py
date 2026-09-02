"""Serialization proofs for the council controller substrate (R1).

The controller's per-campaign primitive must drain commands strictly in
submission order with no interleaving — that is the property the routes
lean on when they stop mutating campaign state themselves. These tests
drive the primitive directly with slow executors and prove:

- two commands on ONE campaign never interleave, and run in submission
  order even when the first is slow;
- commands on DIFFERENT campaigns are independent (no global lock);
- a command outside the bounded vocabulary is refused loudly;
- an executor that raises still settles in the command log, and the
  lock is released for the next command.
"""

import asyncio

import pytest

from vaibify.gui import agentCouncilController as controller


def _fnRunLoop(fnCoroutine):
    return asyncio.run(fnCoroutine)


def test_same_campaign_commands_never_interleave():
    """A slow first command fully settles before the second starts."""
    dictState = controller.fdictCreateCouncilControllerState()
    listTrace = []

    async def _fnSlowFirst():
        listTrace.append("first-enter")
        await asyncio.sleep(0.05)
        listTrace.append("first-exit")

    async def _fnSecond():
        listTrace.append("second-enter")
        listTrace.append("second-exit")

    async def _fnDriveBoth():
        taskFirst = asyncio.ensure_future(
            controller.fgenericSubmitCampaignCommand(
                dictState, "campaign-x", controller.S_COMMAND_START,
                _fnSlowFirst))
        await asyncio.sleep(0)
        taskSecond = asyncio.ensure_future(
            controller.fgenericSubmitCampaignCommand(
                dictState, "campaign-x", controller.S_COMMAND_RESPOND,
                _fnSecond))
        await asyncio.gather(taskFirst, taskSecond)

    _fnRunLoop(_fnDriveBoth())
    assert listTrace == [
        "first-enter", "first-exit", "second-enter", "second-exit"]
    listStages = [(dictEntry["sCommandKind"], dictEntry["sStage"])
                  for dictEntry in controller.flistReadCampaignCommandLog(
                      dictState, "campaign-x")]
    assert listStages.index(("start", "settled")) < listStages.index(
        ("respond", "started"))


def test_different_campaigns_run_independently():
    """A slow command on campaign A does not delay campaign B."""
    dictState = controller.fdictCreateCouncilControllerState()
    listTrace = []

    async def _fnSlowOnA():
        listTrace.append("a-enter")
        await asyncio.sleep(0.1)
        listTrace.append("a-exit")

    async def _fnFastOnB():
        listTrace.append("b-enter")
        listTrace.append("b-exit")

    async def _fnDriveBoth():
        taskSlow = asyncio.ensure_future(
            controller.fgenericSubmitCampaignCommand(
                dictState, "campaign-a", controller.S_COMMAND_START,
                _fnSlowOnA))
        await asyncio.sleep(0)
        taskFast = asyncio.ensure_future(
            controller.fgenericSubmitCampaignCommand(
                dictState, "campaign-b", controller.S_COMMAND_START,
                _fnFastOnB))
        await asyncio.gather(taskSlow, taskFast)

    _fnRunLoop(_fnDriveBoth())
    assert listTrace.index("b-exit") < listTrace.index("a-exit")


def test_unknown_command_kind_is_refused():
    """A command outside the bounded vocabulary raises, executes nothing."""
    dictState = controller.fdictCreateCouncilControllerState()

    async def _fnNeverRuns():
        raise AssertionError("the executor must not run")

    async def _fnSubmitUnknown():
        await controller.fgenericSubmitCampaignCommand(
            dictState, "campaign-x", "reformatDisk", _fnNeverRuns)

    with pytest.raises(controller.CouncilCommandError):
        _fnRunLoop(_fnSubmitUnknown())
    assert controller.flistReadCampaignCommandLog(
        dictState, "campaign-x") == []


def test_raising_executor_settles_and_releases_the_lock():
    """A failed command is logged settled and the next command runs."""
    dictState = controller.fdictCreateCouncilControllerState()

    async def _fnRaises():
        raise RuntimeError("scripted failure")

    async def _fnSucceeds():
        return {"bRan": True}

    async def _fnDriveBoth():
        with pytest.raises(RuntimeError):
            await controller.fgenericSubmitCampaignCommand(
                dictState, "campaign-x", controller.S_COMMAND_START,
                _fnRaises)
        return await controller.fgenericSubmitCampaignCommand(
            dictState, "campaign-x", controller.S_COMMAND_RESPOND,
            _fnSucceeds)

    dictResult = _fnRunLoop(_fnDriveBoth())
    assert dictResult == {"bRan": True}
    listStages = [(dictEntry["sCommandKind"], dictEntry["sStage"])
                  for dictEntry in controller.flistReadCampaignCommandLog(
                      dictState, "campaign-x")]
    assert ("start", "settled") in listStages
    assert listStages.index(("start", "settled")) < listStages.index(
        ("respond", "started"))
