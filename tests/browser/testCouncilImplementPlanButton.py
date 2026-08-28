"""Implementing an accepted plan opens a SEEDED convene form.

The researcher's ask (2026-08-28): once a council completes, offer to
launch an implementation council with the plan. This drives that
button in a real browser — the accepted-plan surface, the button, and
the pre-seeded form are three cooperating pieces of frontend state,
and a contract test over the source can prove none of them render.

What it deliberately does NOT do is convene: the form is the consent
gate every council passes (disclosure, participants, settings, cost),
and a button that skipped it would spend paid provider work on one
click.
"""

import shutil
import tempfile

import pytest

from vaibify.gui import agentCouncilCampaign, agentCouncilStore

from .fakeDockerAdapter import S_CONTAINER_NAME, S_PROJECT_REPO
from .testBrowserJourneys import _fnReleaseBrowserLaneOwnership
from .testCouncilPlanningJourney import (  # noqa: F401 — fixture wiring
    _fdictClaimAndActivate,
    _fnScriptedProviderSeam,
)

pytestmark = pytest.mark.browser

S_PLAN_TEXT = "THE SEALED PLAN: replace the Euler step with RK5"
S_SOURCE_NAME = "Integrator plan"


@pytest.fixture(autouse=True)
def _fnIsolateStoreWithAnAcceptedPlan(serverHub):
    """Give the hub one ACCEPTED planning council with a sealed plan."""
    sTempRoot = tempfile.mkdtemp(prefix="councilImplementButtonLane")
    dictStore = agentCouncilStore.fdictCreateCampaignStore(
        sDurableStoreRoot=sTempRoot)
    dictCampaign = agentCouncilCampaign.fdictCreateCampaign(
        "How should the integrator be replaced?",
        [agentCouncilCampaign.fdictCreateParticipant("claude", "model-a"),
         agentCouncilCampaign.fdictCreateParticipant("claude", "model-b")],
        dictProjectIdentity={
            **agentCouncilCampaign.DICT_EMPTY_PROJECT_IDENTITY,
            "sResourceName": S_CONTAINER_NAME,
            "sProjectRepoPath": S_PROJECT_REPO,
            "sSnapshotIdentity": "sealed-content-identity-0001",
        },
        sCampaignName=S_SOURCE_NAME)
    dictCampaign["sState"] = agentCouncilCampaign.S_STATE_PLAN_ACCEPTED
    dictCampaign["dictCandidatePlan"] = {
        "iRoundNumber": 1,
        "sSynthesisAuthorId":
            dictCampaign["listParticipants"][0]["sParticipantId"],
        "bChairbotSubstituted": False,
        "dictResult": {"sSummary": "Replace Euler with RK5.",
                       "sVerdict": "accept",
                       "listPlanItems": ["Add the RK5 stage table"]},
        "listCouncilClearedObjections": [],
        "listResearcherOverriddenObjections": [],
        "listResearcherResolvedObjections": [],
    }
    agentCouncilStore.fdictRegisterStartedCampaign(dictStore, dictCampaign)
    agentCouncilStore.fsAcceptCampaignPlanLocally(
        dictStore, dictCampaign["sCampaignId"], S_PLAN_TEXT)
    setattr(serverHub.app.state,
            agentCouncilStore.S_COUNCIL_CAMPAIGN_STORE_STATE_KEY, dictStore)
    yield dictCampaign["sCampaignId"]
    _fnReleaseBrowserLaneOwnership(serverHub.app.state)
    shutil.rmtree(sTempRoot, ignore_errors=True)


# The spy: capture the convene body and REFUSE, so the assertion reads
# what the server would have received without spending a launch.
S_CAPTURE_SCRIPT = """
() => {
    window.__vaibifyCapturedConvene = null;
    VaibifyApi.fdictPost = function (sUrl, dictBody) {
        window.__vaibifyCapturedConvene = {sUrl: sUrl, dictBody: dictBody};
        return Promise.reject(new Error("captured by the test"));
    };
}
"""


def testTheImplementButtonOpensAFormSeededWithTheSourceCouncil(
        pageDashboard, serverHub, _fnIsolateStoreWithAnAcceptedPlan):
    """Open the accepted plan, click Implement, land on a seeded form.

    Kills: the button rendering nowhere, opening an unseeded form, or
    convening straight from the click.
    """
    _fdictClaimAndActivate(pageDashboard, serverHub)
    pageDashboard.click("#btnAgentCouncil")
    pageDashboard.wait_for_selector("#btnCouncilPlanChange", timeout=8000)
    pageDashboard.wait_for_selector(".council-open-row", timeout=8000)
    pageDashboard.click(".council-open-row")
    pageDashboard.wait_for_selector(
        "#agentCouncilWorkspace", state="visible", timeout=16000)

    pageDashboard.click('.council-tab[data-tab="plan"]')
    pageDashboard.wait_for_selector("#btnCouncilImplementPlan", timeout=8000)
    pageDashboard.click("#btnCouncilImplementPlan")

    # The convene form, pre-seeded and identifying its source council.
    pageDashboard.wait_for_selector("#btnCouncilConvene", timeout=8000)
    sForm = pageDashboard.inner_text("#agentCouncilModalBody")
    assert "Implement a plan" in sForm
    assert S_SOURCE_NAME in sForm
    sQuestion = pageDashboard.input_value("#councilQuestion")
    assert S_SOURCE_NAME in sQuestion
    # The plan text itself never reaches the browser form: the server
    # loads the sealed artifact from the source campaign.
    assert S_PLAN_TEXT not in sForm

    # What the SERVER would receive. The id is the load-bearing field —
    # the seed is looked up by it — so the assertion reads the real
    # body rather than trusting the form's prose.
    pageDashboard.evaluate(S_CAPTURE_SCRIPT)
    pageDashboard.click("#btnCouncilConvene")
    pageDashboard.wait_for_function(
        "window.__vaibifyCapturedConvene !== null", timeout=8000)
    dictCaptured = pageDashboard.evaluate(
        "window.__vaibifyCapturedConvene")
    assert dictCaptured["sUrl"].endswith("/start")
    assert dictCaptured["dictBody"]["sCampaignKind"] == "implementation"
    assert dictCaptured["dictBody"]["sSourceCampaignId"] == (
        _fnIsolateStoreWithAnAcceptedPlan)
    assert "sSeedPlanDocument" not in dictCaptured["dictBody"]

    assert pageDashboard.listPageErrors == []
    assert pageDashboard.listConsoleErrors == []
