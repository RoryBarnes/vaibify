"""The hub's shutdown drains the council's runners before its egress network.

A network with a runner still attached cannot be proven removed: the
daemon refuses, and the removal reads as indeterminate. Releasing the
egress boundary first made that the answer on every shutdown, leaving
the network in the daemon's list for the next hub's sweep. The runners
drain first and the boundary is released after.
"""

import asyncio

import pytest

from vaibify.gui import agentCouncilController, appFactory, pipelineServer


def _fnFindShutdownHook(app, sName):
    for fnHook in app.state.listLifespanShutdown:
        if getattr(fnHook, "__name__", "") == sName:
            return fnHook
    raise AssertionError(f"no shutdown hook named {sName}")


def _fnRunShutdownRecordingOrder(monkeypatch, bControllerHeld=True):
    app = pipelineServer.fappCreateHubApplication()
    listOrder = []
    if bControllerHeld:
        dictControllerState = (
            agentCouncilController.fdictCreateCouncilControllerState())
        dictControllerState["dictCampaignRuntime"]["campaign-1"] = {
            "sCampaignId": "campaign-1",
            "dictRunnerAccess": {"dictEgress": {"sNetworkName": "net"}},
            "taskDrive": None,
        }
        setattr(
            app.state,
            agentCouncilController.S_COUNCIL_CONTROLLER_STATE_KEY,
            dictControllerState)
    monkeypatch.setattr(
        agentCouncilController, "_fbReleaseRunnerAccessResources",
        lambda dictRuntime: listOrder.append("release-egress") or True)
    monkeypatch.setattr(
        appFactory, "_fnDrainCouncilRunners",
        lambda appDrained: listOrder.append("drain-runners"))
    asyncio.run(_fnFindShutdownHook(app, "fnDrainCouncilOnShutdown")(app))
    return listOrder


@pytest.mark.falsification
def testTheEgressBoundaryIsReleasedAfterTheRunnersDrain(monkeypatch):
    """Kills: releasing the egress network while runners are attached."""
    listOrder = _fnRunShutdownRecordingOrder(monkeypatch)
    assert listOrder == ["drain-runners", "release-egress"]


def testAHubWithNoCouncilStillDrainsItsRunners(monkeypatch):
    app = pipelineServer.fappCreateHubApplication()
    listOrder = []
    setattr(
        app.state, agentCouncilController.S_COUNCIL_CONTROLLER_STATE_KEY, None)
    monkeypatch.setattr(
        appFactory, "_fnDrainCouncilRunners",
        lambda appDrained: listOrder.append("drain-runners"))
    asyncio.run(_fnFindShutdownHook(app, "fnDrainCouncilOnShutdown")(app))
    assert listOrder == ["drain-runners"]


def testTheDirectSettleStillReleasesByDefault(monkeypatch):
    """Other callers of the settle keep the release they always had."""
    listReleased = []
    monkeypatch.setattr(
        agentCouncilController, "_fbReleaseRunnerAccessResources",
        lambda dictRuntime: listReleased.append(dictRuntime) or True)
    dictControllerState = (
        agentCouncilController.fdictCreateCouncilControllerState())
    dictControllerState["dictCampaignRuntime"]["campaign-1"] = {
        "sCampaignId": "campaign-1", "taskDrive": None}
    asyncio.run(agentCouncilController.fnAwaitControllerSettleOnShutdown(
        dictControllerState, fDeadlineSeconds=0.1))
    assert len(listReleased) == 1
    asyncio.run(agentCouncilController.fnAwaitControllerSettleOnShutdown(
        dictControllerState, fDeadlineSeconds=0.1,
        bReleaseRunnerAccess=False))
    assert len(listReleased) == 1
