"""A finished council must look finished, and read like a record.

Four defects found by a researcher on a real completed campaign
(2026-08-29), every one of them invisible to the Python suite because
none of this is Python:

- both agents reported "waiting" beside a banner saying a plan was
  ready, because the lifecycle fallback had no word for "done";
- the footer still offered to "stop after the current turn" for work
  that had already stopped;
- the recorded questions and answers printed as one unbroken block;
- the chairbot's own "DECISION 8" label collided with vaibify's
  "Decision N" heading.

These drive the REAL renderer through the campaign seam a poll uses,
and assert on rendered text — a source-level string check cannot show
that a branch was taken.
"""

import pytest

from .testBrowserJourneys import _fnReleaseBrowserLaneOwnership  # noqa: F401
from .testCouncilBlockedButtonExplainsItself import (  # noqa: F401
    _fdictActivateCouncilToolbar,
    _fnOpenCouncilWorkspace,
)
from .testCouncilPlanningJourney import (  # noqa: F401 — fixture wiring
    _fdictClaimAndActivate,
    _fnIsolateCouncilStore,
    _fnScriptedProviderSeam,
)

pytestmark = pytest.mark.browser

# One finished campaign, reused by the tests that only read it. The
# question text carries the chairbot's own label so the strip is
# exercised on the shape that actually reached a researcher.
_S_FINISHED_CAMPAIGN = """
    VaibifyAgentCouncil.fnSetCampaignForTest({
        sCampaignId: 'campaign-done', sState: 'planReady',
        sQuestion: 'Replace the integrator?',
        sChairbotParticipantId: 'p-opus',
        bDeliberationLive: true,
        listParticipants: [
            {sParticipantId: 'p-opus', sProvider: 'claude',
             sRequestedModel: 'opus'},
            {sParticipantId: 'p-sonn', sProvider: 'claude',
             sRequestedModel: 'sonnet'}],
        listRounds: [{iRoundNumber: 1, dictTurnsByPhase: {}}],
        listResearcherResponses: [{
            listAnsweredQuestions: [
                {sQuestionId: 'q-1', sRaisedByParticipantId: 'p-opus',
                 sQuestionText:
                     "DECISION 8 (anchored on plan item 'Step 1'): " +
                     'should the registry own the token parser?'},
                {sQuestionId: 'q-2', sRaisedByParticipantId: 'p-sonn',
                 sQuestionText: 'Which tolerance is authoritative?'}],
            listDecisionAnswers: [
                {listQuestionIds: ['q-1'],
                 sAnswerText: 'Yes, the registry owns it.'},
                {listQuestionIds: ['q-2'],
                 sAnswerText: 'The one recorded in the input file.'}],
            sText: 'ASKED: ... ANSWERED: ...'}],
        dictCandidatePlan: {dictResult: {
            sSummary: 'Introduce a method registry.',
            listPlanItems: ['Step 1', 'Step 2']}},
    });
"""


def _fsRenderFinished(page):
    """Render the finished campaign and return the panel's visible text."""
    return page.evaluate(
        "() => {" + _S_FINISHED_CAMPAIGN +
        "return document.getElementById("
        "'agentCouncilWorkspaceBody').innerText; }")


def testAFinishedCouncilSaysSoAndOffersThePlanNotAStop(
        pageDashboard, serverHub):
    """The footer must not offer to stop work that has finished.

    Kills: routing planReady into the deliberating composer, which put
    "The council is deliberating" and "A plan is ready for your review"
    on one screen with a Stop button between them.
    """
    _fnOpenCouncilWorkspace(pageDashboard, serverHub)
    sBody = _fsRenderFinished(pageDashboard)

    assert "finished deliberating" in sBody, sBody
    assert "is deliberating. It will pause here" not in sBody, sBody
    assert pageDashboard.locator("#btnCouncilStop").count() == 0, (
        "a finished council offered to stop the turn it is not running")

    # The button is the way out, so it must actually move the tab.
    pageDashboard.click("#btnCouncilOpenPlanTab")
    sPlan = pageDashboard.inner_text("#agentCouncilWorkspaceBody")
    assert "Introduce a method registry" in sPlan, sPlan


def testAFinishedCouncilsAgentsDoNotReportWaiting(
        pageDashboard, serverHub):
    """"Waiting" is the word for a peer still working.

    Kills: the lifecycle fallback returning "waiting" for planReady,
    which made two finished agents read as a stall.
    """
    _fnOpenCouncilWorkspace(pageDashboard, serverHub)
    sBody = _fsRenderFinished(pageDashboard)

    sChips = pageDashboard.inner_text(".council-participant-states")
    assert "finished" in sChips, sChips
    assert "waiting" not in sChips, (
        f"a finished council still reports its agents as waiting: {sChips!r}")
    assert sBody.count("waiting") == 0, sBody


def testAnsweredQuestionsCollapseIntoOpenableExchanges(
        pageDashboard, serverHub):
    """The Q&A history must be scannable, and readable on demand.

    Kills: rendering the pre-rendered ASKED/ANSWERED blob, which is one
    unbroken paragraph per exchange no matter how long the council ran.
    """
    _fnOpenCouncilWorkspace(pageDashboard, serverHub)
    _fsRenderFinished(pageDashboard)

    elExchange = pageDashboard.locator("details.council-exchange")
    assert elExchange.count() == 1
    assert "Questions to you (2)" in elExchange.inner_text()
    # Closed by default: the answer is in the DOM but not on screen.
    elAnswer = pageDashboard.locator(".council-exchange-answer").first
    assert elAnswer.is_visible() is False, (
        "the exchange is expanded by default, which is the wall of text")

    elExchange.locator("summary").click()
    assert elAnswer.is_visible() is True
    sOpen = elExchange.inner_text()
    assert "Yes, the registry owns it." in sOpen, sOpen
    assert "The one recorded in the input file." in sOpen, sOpen
    # Each answer sits with ITS question, mapped by id, not by position.
    assert sOpen.index("registry own the token parser") < sOpen.index(
        "Yes, the registry owns it."), sOpen
    assert sOpen.index("Which tolerance is authoritative") < sOpen.index(
        "The one recorded in the input file."), sOpen


def testTheChairbotsOwnDecisionLabelIsNotShown(pageDashboard, serverHub):
    """Vaibify numbers the decisions; the model must not also.

    Kills: passing the question text through unstripped, which produced
    "Decision 4" as a heading over a body opening "DECISION 2".
    """
    _fnOpenCouncilWorkspace(pageDashboard, serverHub)
    _fsRenderFinished(pageDashboard)
    pageDashboard.locator("details.council-exchange summary").click()

    sOpen = pageDashboard.inner_text("details.council-exchange")
    assert "DECISION 8" not in sOpen, sOpen
    # The rest of the question survives the strip intact.
    assert "should the registry own the token parser?" in sOpen, sOpen


def testThePlanTabSeparatesTheDecisionsFromTheCopies(
        pageDashboard, serverHub):
    """Accept/Reject decide; Copy/Download do not, and must not look it.

    Kills: the flat four-button row that made a researcher ask what the
    difference between the first three was.
    """
    _fnOpenCouncilWorkspace(pageDashboard, serverHub)
    _fsRenderFinished(pageDashboard)
    pageDashboard.click("#btnCouncilOpenPlanTab")

    sDecide = pageDashboard.inner_text(".council-plan-decide")
    assert "unlocks" in sDecide, (
        "nothing told the researcher that accepting is what reveals the "
        "implementation council")
    # The two exports live in their own row, labelled as inert.
    sExports = pageDashboard.inner_text(".council-plan-exports")
    assert "changes nothing" in sExports, sExports
    for sId in ("btnCouncilCopyBrief", "btnCouncilDownloadPlan"):
        assert pageDashboard.locator(
            ".council-plan-exports #" + sId).count() == 1, sId
    # ...and the decisions are NOT in that row.
    for sId in ("btnCouncilAcceptPlan", "btnCouncilRejectPlan"):
        assert pageDashboard.locator(
            ".council-plan-exports #" + sId).count() == 0, sId
        assert pageDashboard.locator(
            ".council-plan-decide #" + sId).count() == 1, sId

    assert pageDashboard.listPageErrors == []
    assert pageDashboard.listConsoleErrors == []
