"""The council entry screen offers four tasks and lists nothing itself.

Reported live (2026-08-30) after a researcher accepted a plan:

- the chooser printed the whole council history under its buttons,
  which is what "Continue a council" is for — the list both duplicated
  the button and buried it;
- rows carried the protocol's own vocabulary: "needsHuman",
  "awaitingImplementation", and phase names run together;
- Continue jumped straight into the most recently active resumable
  council, so a researcher who had just accepted one (which makes it
  terminal, and therefore not resumable) silently landed in a
  different council;
- there was no way to reach a finished council's plan at all.

Only a browser shows any of this: it is all rendered state.
"""

import pytest

from .testBrowserJourneys import _fnReleaseBrowserLaneOwnership  # noqa: F401
from .testCouncilPlanningJourney import (  # noqa: F401 — fixture wiring
    _fdictClaimAndActivate,
    _fnIsolateCouncilStore,
    _fnScriptedProviderSeam,
)

pytestmark = pytest.mark.browser

# One of each kind, so every task view has something to select and the
# selectors are proven to DISCRIMINATE rather than to pass everything.
_S_SEED_SUMMARIES = """
    VaibifyAgentCouncil.fnSetSummariesForTest([
        {sCampaignId: 'campaign-live', sCampaignName: 'A live gate',
         sState: 'needsHuman', fLastActivityEpoch: 3000,
         sAcceptedPlanPath: '',
         dictStoppingPoint: {bResumable: true, sAction: 'answer',
                             sNextPhase: 'independentProposals',
                             iRoundNumber: 2}},
        {sCampaignId: 'campaign-done', sCampaignName: 'An accepted plan',
         sState: 'planAccepted', fLastActivityEpoch: 2000,
         sAcceptedPlanPath: '/home/r/.vaibify/agentCouncils/x/plan.md',
         dictStoppingPoint: {bResumable: false, sBlockedReason:
                             'this council is finished'}},
        {sCampaignId: 'campaign-dead', sCampaignName: 'A failed council',
         sState: 'failed', fLastActivityEpoch: 1000,
         sAcceptedPlanPath: '',
         dictStoppingPoint: {bResumable: false, sBlockedReason:
                             'no recovery action'}},
    ]);
"""


def _fnOpenChooser(page, serverHub):
    _fdictClaimAndActivate(page, serverHub)
    page.click("#btnAgentCouncil")
    page.wait_for_selector("#btnCouncilPlanChange", timeout=8000)
    page.evaluate("() => {" + _S_SEED_SUMMARIES +
                  "VaibifyAgentCouncil.fnRenderChooserForTest(); }")


def testTheChooserOffersFourTasksAndListsNothing(pageDashboard, serverHub):
    """Kills: printing the history under the buttons that navigate it."""
    _fnOpenChooser(pageDashboard, serverHub)

    for sId in ("btnCouncilPlanChange", "btnCouncilImplementPlanChoice",
                "btnCouncilOpenExisting", "btnCouncilViewPast"):
        assert pageDashboard.locator("#" + sId).count() == 1, sId
    assert pageDashboard.locator(".council-open-row").count() == 0, (
        "the chooser is listing councils again — that is what the task "
        "views are for, and the list buried the buttons")

    # Each count is the promise its own button makes.
    assert "(1)" in pageDashboard.inner_text("#btnCouncilOpenExisting")
    assert "(1)" in pageDashboard.inner_text(
        "#btnCouncilImplementPlanChoice")
    assert "(2)" in pageDashboard.inner_text("#btnCouncilViewPast")

    assert pageDashboard.listPageErrors == []
    assert pageDashboard.listConsoleErrors == []


def testPlanAChangeDoesNotWaitForTheCouncilListing(pageDashboard,
                                                   serverHub):
    """Kills: gating every task on a listing three of them need.

    Convening a fresh council reads no history, so a researcher must
    not wait for one. I disabled all four while the listing loaded and
    caught it here.
    """
    _fdictClaimAndActivate(pageDashboard, serverHub)
    pageDashboard.click("#btnAgentCouncil")
    pageDashboard.wait_for_selector("#btnCouncilPlanChange", timeout=8000)
    pageDashboard.evaluate(
        "() => VaibifyAgentCouncil.fnRenderChooserForTest(true)")

    assert pageDashboard.is_disabled("#btnCouncilPlanChange") is False
    for sId in ("btnCouncilOpenExisting", "btnCouncilViewPast",
                "btnCouncilImplementPlanChoice"):
        assert pageDashboard.is_disabled("#" + sId) is True, sId


def testPastCouncilsReadInWordsAndShowWhereThePlanLanded(
        pageDashboard, serverHub):
    """Kills: rendering sState raw, and losing the plan's location.

    "needsHuman" and "awaitingImplementation" are the protocol's
    vocabulary, not the researcher's, and the plan path was previously
    announced only in a toast that cleared in five seconds.
    """
    _fnOpenChooser(pageDashboard, serverHub)
    pageDashboard.click("#btnCouncilViewPast")
    pageDashboard.wait_for_selector(".council-open-row", timeout=8000)

    sView = pageDashboard.inner_text("#agentCouncilModalBody")
    assert "completed — plan accepted" in sView, sView
    assert "failed" in sView, sView
    # The raw token must not reach the screen.
    assert "planAccepted" not in sView, sView
    # The path is on screen, not in a toast that has already gone.
    assert "/plan.md" in sView, sView

    # Only the two non-resumable councils are here; the live gate is not.
    assert pageDashboard.locator(".council-open-row").count() == 2
    assert "A live gate" not in sView, sView


def testContinueShowsOnlyWhatCanBeContinued(pageDashboard, serverHub):
    """Kills: Continue jumping into whichever council it guessed.

    The researcher accepted a council and then found themselves in a
    different one, because accepting makes a campaign terminal and the
    jump silently chose its neighbour.
    """
    _fnOpenChooser(pageDashboard, serverHub)
    pageDashboard.click("#btnCouncilOpenExisting")
    pageDashboard.wait_for_selector(".council-open-row", timeout=8000)

    sView = pageDashboard.inner_text("#agentCouncilModalBody")
    assert "A live gate" in sView, sView
    assert "An accepted plan" not in sView, sView
    assert "A failed council" not in sView, sView
    # Phase names read as words, not as identifiers.
    assert "independent proposals" in sView, sView
    assert "independentProposals" not in sView, sView


def testImplementAPlanOffersTheAcceptedPlanAndSeedsFromIt(
        pageDashboard, serverHub):
    """Kills: leaving an accepted plan reachable only from inside the
    council that accepting had already made terminal."""
    _fnOpenChooser(pageDashboard, serverHub)
    pageDashboard.click("#btnCouncilImplementPlanChoice")
    pageDashboard.wait_for_selector("[data-implement-campaign]",
                                    timeout=8000)

    sView = pageDashboard.inner_text("#agentCouncilModalBody")
    assert "An accepted plan" in sView, sView
    assert "A live gate" not in sView, sView

    pageDashboard.click("[data-implement-campaign='campaign-done']")
    assert pageDashboard.evaluate(
        "() => (VaibifyAgentCouncil.fdictImplementationSeedForTest()"
        " || {}).sSourceCampaignId") == "campaign-done"

    assert pageDashboard.listPageErrors == []
    assert pageDashboard.listConsoleErrors == []


def testPastCouncilsSplitCompletedFromFailedAndOfferDeletes(
        pageDashboard, serverHub):
    """Two branches, and a delete for each scope.

    Kills: one undifferentiated list, and delete controls that act
    without confirmation.
    """
    _fnOpenChooser(pageDashboard, serverHub)
    pageDashboard.click("#btnCouncilViewPast")
    pageDashboard.wait_for_selector(".council-open-row", timeout=8000)

    # inner_text returns RENDERED text, and the section headings are
    # uppercased by CSS — compare on content, not on letter case.
    sView = pageDashboard.inner_text("#agentCouncilModalBody").lower()
    assert "completed (1)" in sView, sView
    assert "failed (1)" in sView, sView

    # One delete per row, plus the two scoped ones.
    assert pageDashboard.locator("[data-delete-campaign]").count() == 2
    assert pageDashboard.locator("#btnCouncilDeleteFailed").count() == 1
    assert pageDashboard.locator("#btnCouncilDeleteAllPast").count() == 1

    # Nothing is deleted without a confirmation: intercept the request
    # so a stray one would be visible, then cancel out of the dialog.
    pageDashboard.evaluate("""
        () => {
            window.__deleted = [];
            const fnReal = VaibifyApi.fnDelete;
            VaibifyApi.fnDelete = function (sUrl) {
                window.__deleted.push(sUrl);
                return fnReal(sUrl);
            };
        }
    """)
    pageDashboard.click("#btnCouncilDeleteAllPast")
    assert pageDashboard.evaluate("window.__deleted").__len__() == 0, (
        "a delete fired before the researcher confirmed it")
    # The confirmation is the dashboard's own idiom, not window.confirm.
    assert pageDashboard.locator("#confirmModal, .modal-overlay").count() > 0

    assert pageDashboard.listPageErrors == []


def testTheDeleteRowButtonDoesNotOpenTheCouncil(pageDashboard, serverHub):
    """Kills: nesting the delete inside the row's open button.

    A click on Delete that also opened the council would put the
    researcher inside the thing they asked to remove.
    """
    _fnOpenChooser(pageDashboard, serverHub)
    pageDashboard.click("#btnCouncilViewPast")
    pageDashboard.wait_for_selector("[data-delete-campaign]", timeout=8000)

    pageDashboard.click("[data-delete-campaign='campaign-dead']")
    # Still on the past view, not in a workspace.
    assert pageDashboard.locator("#btnCouncilDeleteAllPast").count() == 1
    assert pageDashboard.evaluate(
        "() => VaibifyAgentCouncil.fsActiveCampaignIdForTest()") != (
        "campaign-dead")
