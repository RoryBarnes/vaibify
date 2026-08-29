"""Charter 1.7.0's two new surfaces, rendered by the real frontend.

A green Python suite executes zero JavaScript, and both of these are
claims about what a researcher SEES:

- a noted finding must arrive beside the decision gate and be tellable
  from a question at a glance — the defect it replaces is four findings
  raised AS questions, each opening "Emphasis, not a decision: ...";
- a council that never converged must present its closing summary and
  never present it as a plan.

A source-level string check cannot show that either branch was taken,
so these drive the real renderer through the campaign seam a poll uses
and assert on rendered text and rendered structure.
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

S_NOTE_TEXT = ("the tolerance constant is duplicated in two modules; "
               "resolved by inspection, recorded so it is not forgotten")
S_QUESTION_TEXT = "Which tolerance is authoritative?"

# A council sitting at a blocking-question gate that ALSO carries a
# noted finding. The note and the question are distinct strings so the
# test can prove which container each landed in.
_S_GATED_CAMPAIGN = """
    VaibifyAgentCouncil.fnSetCampaignForTest({
        sCampaignId: 'campaign-gated', sState: 'needsHuman',
        sQuestion: 'Replace the integrator?',
        sChairbotParticipantId: 'p-opus',
        bDeliberationLive: true,
        listParticipants: [
            {sParticipantId: 'p-opus', sProvider: 'claude',
             sRequestedModel: 'opus'},
            {sParticipantId: 'p-sonn', sProvider: 'claude',
             sRequestedModel: 'sonnet'}],
        listRounds: [{iRoundNumber: 1, dictTurnsByPhase: {}}],
        dictPendingHumanGate: {
            sGateKind: 'blockingQuestion', iRoundNumber: 1,
            bPlanAvailable: true,
            listQuestions: [{sQuestionId: 'q-1',
                             sRaisedByParticipantId: 'p-sonn',
                             sQuestionText: '%(sQuestion)s'}]},
        listGateNotes: [{sNoteText: '%(sNote)s',
                         sRaisedByParticipantId: 'p-opus',
                         sPhase: 'crossReview', iRoundNumber: 1}],
        dictCandidatePlan: {dictResult: {
            sSummary: 'Introduce a method registry.',
            listPlanItems: ['Step 1']}},
    });
""" % {"sQuestion": S_QUESTION_TEXT, "sNote": S_NOTE_TEXT}

# A council whose rounds ran out without consensus.
_S_EXHAUSTED_CAMPAIGN = """
    VaibifyAgentCouncil.fnSetCampaignForTest({
        sCampaignId: 'campaign-spent', sState: 'needsHuman',
        sQuestion: 'Replace the integrator?',
        sChairbotParticipantId: 'p-opus',
        bDeliberationLive: true,
        listParticipants: [
            {sParticipantId: 'p-opus', sProvider: 'claude',
             sRequestedModel: 'opus'},
            {sParticipantId: 'p-sonn', sProvider: 'claude',
             sRequestedModel: 'sonnet'}],
        listRounds: [{iRoundNumber: 1, dictTurnsByPhase: {}}],
        dictPendingHumanGate: {
            sGateKind: 'exhaustedRounds', iRoundNumber: 2,
            listUnresolvedObjections: [
                {sObjectionId: 'objection-1',
                 sObjectionText: 'the prior is unjustified'}]},
        dictDeliberationSummary: {
            iRoundNumber: 2, sAuthorParticipantId: 'p-opus',
            dictResult: {
                sSummary: 'The council split on the tolerance.',
                listPositionsProposed: ['keep the existing integrator'],
                listPointsOfDisagreement: ['whether the cost is bounded'],
                listEvidenceBehindEachPosition: ['asserted: one benchmark']}},
        dictCandidatePlan: {dictResult: {
            sSummary: 'Introduce a method registry.',
            listPlanItems: ['Step 1']}},
    });
"""


def _fsRender(page, sCampaignScript):
    return page.evaluate(
        "() => {" + sCampaignScript +
        "return document.getElementById("
        "'agentCouncilWorkspaceBody').innerText; }")


def testANotedFindingIsShownWithoutBeingAsked(pageDashboard, serverHub):
    """The note reaches the screen, and asks the researcher nothing.

    Kills: dropping _fsNotedFindingsPanel from the needs-human card,
    which loses the finding entirely — the state the channel was built
    to end.
    """
    _fnOpenCouncilWorkspace(pageDashboard, serverHub)
    sBody = _fsRender(pageDashboard, _S_GATED_CAMPAIGN)

    assert S_NOTE_TEXT in sBody, sBody
    # Case-folded: the heading is styled uppercase, and the assertion
    # is about the words reaching the screen, not their letterforms.
    assert "noted, not asked" in sBody.lower(), sBody
    assert "needs an answer" in sBody, sBody
    # It carries no control of its own.
    assert pageDashboard.locator(".council-notes textarea").count() == 0
    assert pageDashboard.locator(".council-notes button").count() == 0


def testTheNoteIsOutsideTheGateAndTheQuestionIsInsideIt(
        pageDashboard, serverHub):
    """"Beside it, not in it" — asserted on the rendered DOM.

    A note rendered inside the gate reads as one more thing to decide,
    which is exactly the confusion the channel exists to remove. The
    two strings are distinct, so this cannot pass by finding either one
    in the wrong container.

    Kills: composing the notes panel into the gate card instead of
    appending it as a sibling.
    """
    _fnOpenCouncilWorkspace(pageDashboard, serverHub)
    _fsRender(pageDashboard, _S_GATED_CAMPAIGN)

    sGate = pageDashboard.inner_text(".council-needs-human")
    sNotes = pageDashboard.inner_text(".council-notes")

    assert S_QUESTION_TEXT in sGate, sGate
    assert S_NOTE_TEXT not in sGate, (
        "a noted finding rendered inside the decision gate")
    assert S_NOTE_TEXT in sNotes, sNotes
    assert S_QUESTION_TEXT not in sNotes, sNotes
    # And they look different: the gate numbers its questions, the
    # notes panel does not.
    assert pageDashboard.locator(
        ".council-needs-human .council-notes").count() == 0
    assert pageDashboard.locator(".council-notes ol").count() == 0
    assert pageDashboard.locator(".council-notes-list li").count() == 1


def testAnExhaustedCouncilShowsASummaryAndNeverCallsItAPlan(
        pageDashboard, serverHub):
    """The word "plan" must not appear over an unagreed summary.

    Kills: rendering the deliberation summary through the candidate
    plan's own body renderer, whose heading is "Plan".
    """
    _fnOpenCouncilWorkspace(pageDashboard, serverHub)
    sBody = _fsRender(pageDashboard, _S_EXHAUSTED_CAMPAIGN)

    assert "Rounds exhausted without consensus" in sBody, sBody
    sSummary = pageDashboard.inner_text(".council-deliberation-summary")
    assert "Deliberation summary" in sSummary, sSummary
    assert "not a plan" in sSummary, sSummary
    assert "did not converge" in sSummary, sSummary
    assert "The council split on the tolerance." in sSummary, sSummary
    assert "whether the cost is bounded" in sSummary, sSummary
    assert "asserted: one benchmark" in sSummary, sSummary


def testTheExhaustedGateOffersTwoWaysForwardThenAnAbandon(
        pageDashboard, serverHub):
    """Two exits forward; rejecting is abandoning, and sits apart.

    Kills: leaving Reject and archive inside the exits row, where three
    peer-looking buttons read as three equivalent ways forward.
    """
    _fnOpenCouncilWorkspace(pageDashboard, serverHub)
    _fsRender(pageDashboard, _S_EXHAUSTED_CAMPAIGN)

    sExits = pageDashboard.inner_text(".council-exits")
    assert "Two ways forward" in sExits, sExits
    assert "Grant a bounded resolution round" in sExits, sExits
    assert "Implement as-is" in sExits, sExits
    assert "Reject" not in sExits, sExits

    assert pageDashboard.locator(
        ".council-exits #btnCouncilReject").count() == 0
    assert pageDashboard.locator(
        ".council-abandon #btnCouncilReject").count() == 1
    assert "abandon this council" in pageDashboard.inner_text(
        ".council-abandon").lower()
