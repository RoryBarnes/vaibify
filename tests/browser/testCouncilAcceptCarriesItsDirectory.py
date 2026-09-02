"""Accepting a plan must not ask which directory the council was about.

Reported live on 2026-08-29 against a container tracking nine
repositories: "Accept and save plan" answered "this project tracks
several directories (...), so a council needs to be told which one it is
about" for a council that had been deliberating about one of them.

The backend fix is the real one — a campaign-scoped route now reads the
repository off the campaign record — but the button was also the last
frontend call site that composed its URL without the directory, and a
Python suite executes no JavaScript at all. So this drives the REAL
button in a REAL browser and asserts on the URL the page actually sent.
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

S_CAMPAIGN_REPOSITORY = "/workspace/modelRepo"

_S_PLAN_READY_CAMPAIGN = """
    VaibifyAgentCouncil.fnSetCampaignForTest({
        sCampaignId: 'campaign-ready', sState: 'planReady',
        sQuestion: 'Replace the integrator?',
        sChairbotParticipantId: 'p-opus',
        dictProjectIdentity: {sProjectRepoPath: '%s'},
        listParticipants: [
            {sParticipantId: 'p-opus', sProvider: 'claude',
             sRequestedModel: 'opus'},
            {sParticipantId: 'p-sonn', sProvider: 'claude',
             sRequestedModel: 'sonnet'}],
        listRounds: [{iRoundNumber: 1, dictTurnsByPhase: {}}],
        dictCandidatePlan: {dictResult: {
            sSummary: 'Introduce a method registry.',
            listPlanItems: ['Step 1', 'Step 2']}},
    });
""" % S_CAMPAIGN_REPOSITORY


def testAcceptingAPlanSendsTheCampaignsOwnDirectory(
        pageDashboard, serverHub):
    """The URL the browser sends must name the campaign's repository.

    Asserted on the intercepted request rather than on the response,
    because the server-side fix makes the query optional: a test that
    only checked "the accept succeeded" would pass with the button
    still composing a bare URL, and the two halves would drift apart
    again the moment the backend fallback moved.

    Kills: composing the accept-plan URL without the directory query.
    """
    _fnOpenCouncilWorkspace(pageDashboard, serverHub)

    listSeenUrls = []

    def _fnCaptureAccept(routeIntercepted):
        listSeenUrls.append(routeIntercepted.request.url)
        routeIntercepted.fulfill(
            status=200, content_type="application/json",
            body='{"sLocalPlanPath": "/tmp/plan.md"}')

    pageDashboard.route("**/accept-plan*", _fnCaptureAccept)
    pageDashboard.evaluate("() => {" + _S_PLAN_READY_CAMPAIGN + "}")
    pageDashboard.click("#btnCouncilOpenPlanTab")
    pageDashboard.click("#btnCouncilAcceptPlan")
    pageDashboard.wait_for_function(
        "() => document.querySelectorAll('.toast').length > 0",
        timeout=8000)

    assert listSeenUrls, "the Accept button sent no accept-plan request"
    assert "sProjectDirectory=modelRepo" in listSeenUrls[0], (
        "Accept and save plan sent no directory, so a project tracking "
        f"several would be asked which one it meant: {listSeenUrls[0]}")
