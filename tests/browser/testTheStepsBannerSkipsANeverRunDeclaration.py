"""The Steps banner's Level 1 cell is not held at partial by a never-run AI Declaration.

Researcher-reported (walking the quickstart, 2026-11-04): both
automated steps were signed off, each step's Level 1 cell turned to a
check and the project reached Level 1, but the Steps banner's Level 1
cell stayed orange. The AI Declaration step, which the quickstart tells
a reader to skip, had never run; it has no Level 1 requirement (ruling
2026-07-02), so its cell is meant to read not-applicable, which the
banner's summarizer skips, but the server's precedence put the
inactivity override first and the cell read not-started, which the
summarizer counts as blocking.

This is the surface the researcher saw, so it is checked where it was
seen: the real page loads the real poll, with the three steps' level
states replaced by what the real gate (``fdictComputeStepLevelStates``)
computes for two attested, clean ordinary steps and one never-run
declaration step, and the banner and the declaration row are read off
the screen.
"""

import json

import pytest

from tests.browser.conftest import (
    fdictHostWorkflowDocument, fnOpenTheSeededHostWorkflow,
)
from tests.browser.testAVerificationIsFollowedOnTheProjectBlock import (
    fixtureDropClaimsBetweenTests,  # noqa: F401 -- autouse fixture
)
from vaibify.reproducibility.levelGates import fdictComputeStepLevelStates


pytestmark = pytest.mark.browser

S_DECLARATION_ROW = '.step-item[data-index="2"]'
S_BANNER_CELLS = ".steps-block-status .step-level-strip .step-level-cell"


@pytest.fixture(autouse=True)
def fixtureStopInterceptingThePoll(pageDashboard):
    """Drop the poll route before the page closes."""
    yield
    pageDashboard.unroute_all(behavior="ignoreErrors")


def fdictLevelStatesFromTheGate():
    """Return the gate's states for two attested steps and a never-run declaration."""
    dictWorkflow = fdictHostWorkflowDocument()
    dictWorkflow["sProjectRepoPath"] = "/repo"
    for dictStep in dictWorkflow["listSteps"][:2]:
        dictStep["bNoInputData"] = True
        dictStep["saOutputDataFiles"] = dictStep["saOutputDataFiles"] or [
            dictStep["sDirectory"] + "/output.json"]
        dictStep["dictVerification"] = {"sUser": "passed", "sUnitTest": "passed"}
    dictStates = fdictComputeStepLevelStates(dictWorkflow, [], [], [])
    return {str(iIndex): dictCells for iIndex, dictCells in dictStates.items()}


def _fnOpenWithTheGatesStates(pageDashboard, serverHub, dictStepLevels):
    def fnHandle(routePoll):
        responsePoll = routePoll.fetch()
        dictBody = responsePoll.json()
        dictBody["dictStepLevels"] = dictStepLevels
        dictBody["listBlockers"] = []
        routePoll.fulfill(
            response=responsePoll, body=json.dumps(dictBody),
            headers={"content-type": "application/json"})
    pageDashboard.route("**/file-status*", fnHandle)
    fnOpenTheSeededHostWorkflow(pageDashboard, serverHub)
    pageDashboard.wait_for_function(
        "() => (document.querySelector("
        "'.step-item[data-index=\"0\"] .step-level-cell[data-level=\"1\"]')"
        " || {className: ''}).className.includes('level-cell-attained')",
        timeout=20000)


@pytest.mark.falsification
def testTheBannerReadsAttainedAndTheDeclarationRowNotApplicable(
    pageDashboard, serverHub,
):
    """Kills: the banner's summarizer counting a not-applicable cell as
    blocking, which paints two attained steps and one never-run
    declaration as partial."""
    dictStepLevels = fdictLevelStatesFromTheGate()
    assert dictStepLevels["2"]["s1"]["sState"] == "not-applicable"
    assert dictStepLevels["0"]["s1"]["sState"] == "attained"
    _fnOpenWithTheGatesStates(pageDashboard, serverHub, dictStepLevels)
    sDeclarationClass = pageDashboard.locator(
        S_DECLARATION_ROW + ' .step-level-cell[data-level="1"]').first.get_attribute("class")
    assert "level-cell-not-applicable" in sDeclarationClass
    listBannerClasses = pageDashboard.locator(S_BANNER_CELLS).evaluate_all(
        "listCells => listCells.map(el => el.className)")
    assert len(listBannerClasses) == 3
    assert "level-cell-attained" in listBannerClasses[0], listBannerClasses
    assert pageDashboard.listPageErrors == []
