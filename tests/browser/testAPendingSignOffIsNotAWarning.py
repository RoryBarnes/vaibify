"""A step awaiting the researcher's sign-off is pending work, not a warning.

Researcher-reported (QuickStart section 5, 2026-10-13): once a step's
tests passed, its warning column showed "Step has never been verified"
under the warning glyph. The sign-off is the step's expected next
action, and the Level 1 cell's tooltip already names it, so the warning
column stays empty for it. The same report found that running the unit
tests cleared a sign-off the researcher had already given; a test run
leaves the outputs as they were, so it must leave the sign-off too.

The first two are claims about the SCREEN: the real page loads the real
poll, altered in flight to the blocker the server sends for a step whose
tests passed and whose sign-off is missing. The last two drive the real
result-event handler the pipeline socket feeds.
"""

import json

import pytest

from tests.browser.conftest import fnOpenTheSeededHostWorkflow
from tests.browser.testAVerificationIsFollowedOnTheProjectBlock import (
    fixtureDropClaimsBetweenTests,  # noqa: F401 -- autouse fixture
)


pytestmark = pytest.mark.browser

S_SIGN_OFF_SENTENCE = (
    "Step has never been verified — click verify when satisfied")
S_FIRST_STEP_ROW = '.step-item[data-index="0"]'


@pytest.fixture(autouse=True)
def fixtureStopInterceptingThePoll(pageDashboard):
    """Drop the poll route before the page closes."""
    yield
    pageDashboard.unroute_all(behavior="ignoreErrors")


def _fnAlterThePollToAwaitSignOff(dictBody):
    """Rewrite the poll to what a tested, unsigned step 0 answers."""
    dictBody.setdefault("dictStepLevels", {}).setdefault("0", {})["s1"] = {
        "sState": "partial", "iSatisfied": 6, "iTotal": 7,
        "bRegression": False,
        "listRequirements": [{"sName": "user-attestation", "bMet": False}],
    }
    dictBody["listBlockers"] = [{
        "iLevel": 1, "iStepIndex": 0, "sStepLabel": "A01",
        "sScope": "step", "sCriterion": "user-not-approved",
        "listOffendingFiles": [], "listOffendingUpstreamSteps": [],
        "sRemediationHint": S_SIGN_OFF_SENTENCE,
    }]
    dictBody["iProofLevel"] = 0
    return dictBody


def _fnOpenWithTheStepAwaitingSignOff(pageDashboard, serverHub):
    def fnHandle(routePoll):
        responsePoll = routePoll.fetch()
        dictBody = _fnAlterThePollToAwaitSignOff(responsePoll.json())
        routePoll.fulfill(
            response=responsePoll, body=json.dumps(dictBody),
            headers={"content-type": "application/json"})
    pageDashboard.route("**/file-status*", fnHandle)
    fnOpenTheSeededHostWorkflow(pageDashboard, serverHub)
    pageDashboard.wait_for_function(
        "(sSentence) => (document.querySelector("
        "'.step-item[data-index=\"0\"] .step-level-cell[data-level=\"1\"]')"
        " || {getAttribute: () => ''}).getAttribute('title')"
        ".includes(sSentence)",
        arg=S_SIGN_OFF_SENTENCE, timeout=20000,
    )


@pytest.mark.falsification
def testAStepAwaitingSignOffShowsNoWarningGlyph(pageDashboard, serverHub):
    """Kills: the warning column voicing the sign-off blocker again."""
    _fnOpenWithTheStepAwaitingSignOff(pageDashboard, serverHub)
    elCell = pageDashboard.locator(
        S_FIRST_STEP_ROW + " .step-regression-cell").first
    sClasses = elCell.get_attribute("class") or ""
    assert "regression-warning" not in sClasses, sClasses
    assert S_SIGN_OFF_SENTENCE not in (elCell.get_attribute("title") or "")
    assert pageDashboard.listPageErrors == []


def testTheLevelCellStillSaysTheSignOffIsMissing(pageDashboard, serverHub):
    """The pending work is still on screen, where the level is."""
    _fnOpenWithTheStepAwaitingSignOff(pageDashboard, serverHub)
    elLevelCell = pageDashboard.locator(
        S_FIRST_STEP_ROW + ' .step-level-cell[data-level="1"]').first
    assert "level-cell-attained" not in elLevelCell.get_attribute("class")
    assert S_SIGN_OFF_SENTENCE in elLevelCell.get_attribute("title")


_S_SIGN_OFF_THEN_DELIVER = """(dictEvent) => {
    const dictStep = VaibifyApp.fdictGetWorkflow().listSteps[0];
    dictStep.dictVerification = Object.assign(
        {}, dictStep.dictVerification || {}, {sUser: "passed"});
    VaibifyPipelineRunner.fnHandlePipelineEvent(dictEvent);
    return VaibifyApp.fdictGetWorkflow().listSteps[0]
        .dictVerification.sUser;
}"""


@pytest.mark.falsification
@pytest.mark.parametrize("sType, iExitCode", [
    ("stepPass", 0), ("stepFail", 1),
])
def testATestRunsResultKeepsTheResearchersSignOff(
    pageDashboard, serverHub, sType, iExitCode,
):
    """Kills: a test run's result resetting the sign-off like a run's."""
    fnOpenTheSeededHostWorkflow(pageDashboard, serverHub)
    sUser = pageDashboard.evaluate(_S_SIGN_OFF_THEN_DELIVER, {
        "sType": sType, "iStepNumber": 1, "iExitCode": iExitCode,
        "bFromTestRun": True,
    })
    assert sUser == "passed"
    assert pageDashboard.listPageErrors == []


def testARunsResultStillClearsTheSignOff(pageDashboard, serverHub):
    """A run rewrites the outputs the sign-off was about."""
    fnOpenTheSeededHostWorkflow(pageDashboard, serverHub)
    sUser = pageDashboard.evaluate(_S_SIGN_OFF_THEN_DELIVER, {
        "sType": "stepPass", "iStepNumber": 1, "iExitCode": 0,
    })
    assert sUser == "untested"
    assert pageDashboard.listPageErrors == []


_S_LIST_A_MODIFIED_OUTPUT = """(sUser) => {
    const dictStep = VaibifyApp.fdictGetWorkflow().listSteps[0];
    dictStep.dictVerification = Object.assign(
        {}, dictStep.dictVerification || {},
        {sUser: sUser, listModifiedFiles: ["MakeNumbers/numbers.json"]});
    VaibifyApp.fnRenderStepList();
}"""

_S_WARNING_NAMES_THE_OUTPUT = """() => (document.querySelector(
    '.step-item[data-index="0"] .step-regression-cell') || {
        getAttribute: () => ""}).getAttribute("title").includes(
    "numbers.json")"""


def _fsTooltipAfterListingAModifiedOutput(pageDashboard, sUser):
    """List step 0's output as modified under sUser; return the tooltip."""
    pageDashboard.evaluate(_S_LIST_A_MODIFIED_OUTPUT, sUser)
    pageDashboard.wait_for_function(
        _S_WARNING_NAMES_THE_OUTPUT, timeout=10000)
    return pageDashboard.locator(
        '.step-item[data-index="0"] .step-regression-cell',
    ).first.get_attribute("title") or ""


@pytest.mark.falsification
def testAnUnverifiedStepsChangedOutputDoesNotClaimAVerification(
    pageDashboard, serverHub,
):
    """Kills: the tooltip saying "after you verified" for a step the
    researcher never verified."""
    fnOpenTheSeededHostWorkflow(pageDashboard, serverHub)
    sTooltip = _fsTooltipAfterListingAModifiedOutput(
        pageDashboard, "untested")
    assert "numbers.json" in sTooltip, sTooltip
    assert "you verified" not in sTooltip, sTooltip
    assert "since the step last ran or was tested" in sTooltip, sTooltip


@pytest.mark.parametrize("sUser", ["passed", "stale"])
def testAVerifiedStepsChangedOutputSaysItChangedAfterTheSignOff(
    pageDashboard, serverHub, sUser,
):
    """The sentence a researcher who signed off still needs."""
    fnOpenTheSeededHostWorkflow(pageDashboard, serverHub)
    sTooltip = _fsTooltipAfterListingAModifiedOutput(pageDashboard, sUser)
    assert "Output files changed after you verified: numbers.json" in (
        sTooltip), sTooltip
