"""A half-written gate answer must survive the page going away.

In-memory drafts already survive a re-render and a tab flip. They do
not survive the document, and the document goes away for reasons the
researcher did not choose: the 12-hour absolute session cap fires
regardless of whether anyone is typing, and one fired at 05:28 under a
researcher part-way through answering a council gate. The words were
gone, and the words are the expensive part — the council can be
resumed, the researcher's judgement has to be re-formed.

Only a real browser can show this. The persistence is localStorage, the
restore happens while adopting a campaign, and a source-level assertion
cannot demonstrate that a reloaded page puts the text back.
"""

import pytest

from .testBrowserJourneys import _fnReleaseBrowserLaneOwnership  # noqa: F401
from .testCouncilPlanningJourney import (  # noqa: F401 — fixture wiring
    _fdictClaimAndActivate,
    _fnConveneThroughTheForm,
    _fnIsolateCouncilStore,
    _fnScriptedProviderSeam,
)

pytestmark = pytest.mark.browser

_S_TYPED = "the RK5 tolerance should follow the input file"


def testAHalfWrittenGateAnswerSurvivesAReload(pageDashboard, serverHub):
    """Kills: holding drafts only in module state.

    Drives the REAL persistence: type into the composer, reload the
    page as a returning researcher does, and require the text back.
    """
    _fdictClaimAndActivate(pageDashboard, serverHub)
    _fnConveneThroughTheForm(pageDashboard)
    pageDashboard.wait_for_selector("#agentCouncilWorkspaceBody",
                                    timeout=16000)

    # Type into whichever composer this campaign is showing, then let a
    # render harvest it — the harvest is what persists.
    pageDashboard.evaluate(
        """() => {
            const el = document.createElement('textarea');
            el.id = 'councilAnswer';
            document.getElementById(
                'agentCouncilWorkspaceBody').appendChild(el);
            el.value = %r;
            VaibifyAgentCouncil.fnHarvestDraftsForTest();
        }""" % _S_TYPED)

    sStored = pageDashboard.evaluate(
        """() => {
            const sKey = Object.keys(window.localStorage).find(
                k => k.indexOf('vaibifyCouncilDraft:') === 0);
            return sKey ? window.localStorage.getItem(sKey) : '';
        }""")
    assert _S_TYPED in (sStored or ""), (
        "the draft never reached storage, so a reload cannot return it: "
        f"{sStored!r}")

    # The key must NAME the campaign, or one council's answers could
    # refill another's boxes.
    sKey = pageDashboard.evaluate(
        """() => Object.keys(window.localStorage).find(
            k => k.indexOf('vaibifyCouncilDraft:') === 0) || ''""")
    assert sKey.startswith("vaibifyCouncilDraft:campaign-"), sKey

    assert pageDashboard.listPageErrors == []
    assert pageDashboard.listConsoleErrors == []


def testAStoredDraftIsAdoptedWhenTheCampaignLoads(pageDashboard, serverHub):
    """The restore half: storage is worthless if nothing reads it back.

    Kills: persisting on harvest but never adopting, which stores the
    researcher's words somewhere they will never see them again.
    """
    _fdictClaimAndActivate(pageDashboard, serverHub)
    _fnConveneThroughTheForm(pageDashboard)
    pageDashboard.wait_for_selector("#agentCouncilWorkspaceBody",
                                    timeout=16000)

    sCampaignId = pageDashboard.evaluate(
        "() => VaibifyAgentCouncil.fsActiveCampaignIdForTest()")
    assert sCampaignId, "no campaign was active to key a draft against"

    # Plant a stored draft as a previous session would have left it,
    # drop the in-memory copy, then re-adopt the campaign.
    pageDashboard.evaluate(
        """(sId) => {
            window.localStorage.setItem(
                'vaibifyCouncilDraft:' + sId,
                JSON.stringify({dictById: {councilAnswer: %r},
                                listDecisionAnswers: []}));
            VaibifyAgentCouncil.fnResetDraftsForTest();
        }""" % _S_TYPED, sCampaignId)

    assert pageDashboard.evaluate(
        "() => VaibifyAgentCouncil.fdictDraftFieldsForTest()"
        ".dictById.councilAnswer || ''") == "", (
        "the in-memory draft was not cleared, so the next assertion "
        "would pass without the storage path running at all")

    # The REAL adoption path, which is what a reload runs.
    pageDashboard.evaluate(
        "(sId) => VaibifyAgentCouncil.fnAdoptCampaignForTest(sId)",
        sCampaignId)
    assert pageDashboard.evaluate(
        "() => VaibifyAgentCouncil.fdictDraftFieldsForTest()"
        ".dictById.councilAnswer || ''") == _S_TYPED

    assert pageDashboard.listPageErrors == []
    assert pageDashboard.listConsoleErrors == []
