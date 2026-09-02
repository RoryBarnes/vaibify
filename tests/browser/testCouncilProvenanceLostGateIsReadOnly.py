"""A provenance-lost council's gate is read-only, and says so upfront.

A researcher resumed a pre-sidecar campaign, answered its thirteen
questions, and only the final "Record decision" click revealed the
refusal (2026-08-27): the store had ruled the campaign unusable, but
the workspace still rendered live answer boxes, and the chooser's
"Continue a council" button had steered them into it. Only a real
browser proves the three surfaces now agree: the gate renders
read-only with the reason first, and the continue button neither
counts nor targets an unusable campaign.
"""

import os
import shutil
import tempfile

import pytest

from vaibify.gui import agentCouncilStore

from .testBrowserJourneys import _fnReleaseBrowserLaneOwnership
from .testCouncilPlanningJourney import (  # noqa: F401 — fixture wiring
    _fdictClaimAndActivate,
    _fnScriptedProviderSeam,
)
from .testCouncilRetryOffer import _fdictBuildInterruptedRetryableCampaign

pytestmark = pytest.mark.browser

S_LOST_QUESTION_TEXT = "Should the cache key include the compiler?"


def _fdictBuildProvenanceLostGateCampaign():
    """A needsHuman campaign whose record ran but whose sidecar is gone."""
    dictCampaign = _fdictBuildInterruptedRetryableCampaign()
    dictCampaign["sState"] = "needsHuman"
    dictCampaign["dictPendingHumanGate"] = {
        "sGateKind": "blockingQuestion",
        "sOriginPhase": "synthesis",
        "bPlanAvailable": True,
        "iRoundNumber": 1,
        "listQuestions": [{
            "sQuestionId": "question-lost-0001",
            "sQuestionText": S_LOST_QUESTION_TEXT,
            "sRaisedByParticipantId": dictCampaign[
                "listParticipants"][0]["sParticipantId"],
        }],
    }
    return dictCampaign


@pytest.fixture(autouse=True)
def _fnIsolateStoreWithAProvenanceLostCouncil(serverHub):
    """Register a campaign with turns, then reload it sidecar-less.

    Registration writes the campaign record but no provenance sidecar
    (that lands with the first persisted ledger activity), so a
    reload of a record that already carries turns discovers exactly
    the pre-sidecar shape a researcher's old campaign has — the real
    path, with nothing hand-deleted.
    """
    sTempRoot = tempfile.mkdtemp(prefix="councilProvenanceLostLane")
    dictSeedStore = agentCouncilStore.fdictCreateCampaignStore(
        sDurableStoreRoot=sTempRoot)
    dictCampaign = _fdictBuildProvenanceLostGateCampaign()
    agentCouncilStore.fdictRegisterStartedCampaign(
        dictSeedStore, dictCampaign)
    sSidecarPath = os.path.join(
        sTempRoot, dictCampaign["sCampaignId"],
        agentCouncilStore.S_PROVENANCE_SIDECAR_BASENAME)
    if os.path.exists(sSidecarPath):
        os.remove(sSidecarPath)
    dictStore = agentCouncilStore.fdictCreateCampaignStore(
        sDurableStoreRoot=sTempRoot)
    agentCouncilStore.fdictReloadDurableCampaigns(dictStore)
    assert agentCouncilStore.fbCampaignProvenanceUnavailable(
        dictStore, dictCampaign["sCampaignId"]), (
        "the premise failed: the reload did not mark provenance lost")
    setattr(serverHub.app.state,
            agentCouncilStore.S_COUNCIL_CAMPAIGN_STORE_STATE_KEY, dictStore)
    yield
    _fnReleaseBrowserLaneOwnership(serverHub.app.state)
    shutil.rmtree(sTempRoot, ignore_errors=True)


def testAProvenanceLostGateRendersReadOnlyAndUncounted(
        pageDashboard, serverHub):
    """The gate explains itself; the continue button excludes it.

    Kills: live answer boxes on a gate whose submission the store will
    refuse, and a continue button that counts or targets it.
    """
    _fdictClaimAndActivate(pageDashboard, serverHub)
    pageDashboard.click("#btnAgentCouncil")
    pageDashboard.wait_for_selector("#btnCouncilPlanChange", timeout=8000)
    # Continue still excludes it, and still says so with a count — that
    # is the half of this guarantee about what must NOT be offered.
    sContinueLabel = pageDashboard.inner_text("#btnCouncilOpenExisting")
    assert "(0)" in sContinueLabel
    assert pageDashboard.is_disabled("#btnCouncilOpenExisting")

    # It stays READABLE, under the task that exists for exactly that.
    # The chooser no longer lists rows itself (2026-08-30).
    # The task buttons that READ the listing stay disabled
    # until it arrives; only "Plan a change" is free of it.
    pageDashboard.wait_for_selector(
        "#btnCouncilViewPast:not([disabled])", timeout=8000)
    pageDashboard.click("#btnCouncilViewPast")
    pageDashboard.wait_for_selector(".council-open-row", timeout=8000)
    pageDashboard.click(".council-open-row")
    pageDashboard.wait_for_selector(
        "#agentCouncilWorkspace", state="visible", timeout=16000)
    sWorkspace = pageDashboard.inner_text("#agentCouncilWorkspaceBody")
    assert "can be read, not answered" in sWorkspace
    assert "provenance sidecar" in sWorkspace
    assert S_LOST_QUESTION_TEXT in sWorkspace
    assert pageDashboard.query_selector(".council-decision-answer") is None
    assert pageDashboard.query_selector("#councilAnswer") is None
    assert pageDashboard.query_selector("#btnCouncilAnswer") is None

    assert pageDashboard.listPageErrors == []
    assert pageDashboard.listConsoleErrors == []
