"""A hub that dies mid-phase leaves the campaign recoverable by Retry.

The startup classifier moved a campaign whose phase attempt was still
``running`` to ``interrupted`` and left the attempt itself ``running``.
Retry refuses any attempt that did not settle as the terminating
failure ("convene a fresh council"), and resume admits only a campaign
in planning, so the campaign was unrecoverable while the controller's
own refusal text promised "Run vaibify reconcile, then retry the phase".
The classifier now settles the dead attempt as an interruption, which is
the record Retry reads.
"""

import asyncio

import pytest

from tests.testCouncilResume import (
    S_IMAGE_IDENTITY,
    _RecordingAcceptConnection,
    _fdictCaptureMidWalkVersion,
    _tPlantCrashedCampaign,
)
from vaibify.gui import agentCouncilController
from vaibify.gui import agentCouncilRegistry
from vaibify.gui import agentCouncilResolution
from vaibify.gui import agentCouncilStore


def _fdictVersionKilledMidPhase():
    dictVersion = _fdictCaptureMidWalkVersion()
    dictAttempt = dictVersion["listRounds"][-1]["dictPhaseAttempt"]
    dictAttempt["sAttemptState"] = "running"
    dictAttempt["sOutcome"] = ""
    return dictVersion


def _fdictRestartedStore(tmp_path, dictVersion):
    """Plant the crash, then reload and classify exactly as startup does."""
    dictStore, _, _, sCampaignId = _tPlantCrashedCampaign(
        tmp_path, dictVersion)
    dictReloaded = agentCouncilStore.fdictCreateCampaignStore(
        sDurableStoreRoot=dictStore["sDurableStoreRoot"])
    agentCouncilStore.fdictReloadDurableCampaigns(dictReloaded)
    agentCouncilController.fiClassifyInterruptedCampaignsOnStartup(
        dictReloaded)
    return dictReloaded, sCampaignId


@pytest.mark.falsification
def testACampaignKilledMidPhaseIsRetryableAfterTheRestart(tmp_path):
    """Kills: interrupting the campaign but leaving its attempt running."""
    dictStore, sCampaignId = _fdictRestartedStore(
        tmp_path, _fdictVersionKilledMidPhase())
    dictRecord = agentCouncilStore.fjsonGetCampaignRecord(
        dictStore, sCampaignId)
    dictRound = dictRecord["listRounds"][-1]
    dictAttempt = dictRound["dictPhaseAttempt"]
    assert dictRecord["sState"] == "interrupted"
    assert dictAttempt["sAttemptState"] == "outcomeSettled"
    assert dictAttempt["sOutcome"] == "transitioned:interrupted"
    assert agentCouncilResolution.fsClassifyRetryEligibility(
        dictRound, dictAttempt) == ""


def testTheRetryOfAKilledPhaseReRunsItAndReachesAPlan(
    tmp_path, monkeypatch,
):
    """The recovery runs end to end: classify, then Retry, then a plan."""
    dictStore, sCampaignId = _fdictRestartedStore(
        tmp_path, _fdictVersionKilledMidPhase())
    listPhaseLog = []
    monkeypatch.setattr(
        agentCouncilController, "fconnectionBuildParticipantConnection",
        lambda dictRuntime, dictParticipant:
            _RecordingAcceptConnection(listPhaseLog))
    dictControllerState = (
        agentCouncilController.fdictCreateCouncilControllerState())

    async def fdictRetryToCompletion():
        dictRetried = await (
            agentCouncilController.fdictRetryCampaignFailedPhase(
                dictControllerState, dictStore,
                agentCouncilRegistry.fdictCreateCouncilRegistry(),
                sCampaignId, S_IMAGE_IDENTITY))
        dictRuntime = dictControllerState["dictCampaignRuntime"][sCampaignId]
        await dictRuntime["taskDrive"]
        return dictRetried

    dictRetried = asyncio.run(fdictRetryToCompletion())
    assert dictRetried["bRetried"] is True
    assert dictRetried["sRetriedPhase"] == "crossReview"
    assert agentCouncilStore.fjsonGetCampaignRecord(
        dictStore, sCampaignId)["sState"] == "planReady"
    assert "crossReview" in listPhaseLog


def testAPhaseThatHadNoRunningAttemptIsLeftAsItWas(tmp_path):
    """A campaign with no attempt record is still not made retryable."""
    dictVersion = _fdictCaptureMidWalkVersion()
    dictVersion["listRounds"][-1]["dictPhaseAttempt"] = None
    dictStore, sCampaignId = _fdictRestartedStore(tmp_path, dictVersion)
    dictRecord = agentCouncilStore.fjsonGetCampaignRecord(
        dictStore, sCampaignId)
    assert dictRecord["sState"] == "interrupted"
    assert dictRecord["listRounds"][-1]["dictPhaseAttempt"] is None
