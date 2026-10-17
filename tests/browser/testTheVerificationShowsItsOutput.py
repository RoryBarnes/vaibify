"""A running L3 verification shows what the throwaway container prints.

The rerun can take as long as the workflow; until the hub kept its
output, the PROOF card said one sentence and pulsed for all of it. These
tests drive the real PROOF tab against an attestation answer carrying a
live run and its output, and assert what the researcher sees: the step
under way, the newest lines, a log that follows new lines while read at
the bottom and stays put when scrolled back, and no output from any run
but the one in flight.
"""

import json

import pytest

from tests.browser.conftest import fnOpenTheSeededHostWorkflow

pytestmark = pytest.mark.browser

S_TAIL = ".proof-verify-progress .proof-verify-tail"


@pytest.fixture(autouse=True)
def fixtureDropClaimsBetweenTests(serverHub):
    yield
    from vaibify.config.containerLock import fnReleaseContainerLock
    dictContainerOwners = serverHub.app.state.dictContainerOwners
    for _sName, recordOwner in list(dictContainerOwners.items()):
        fileHandle = getattr(recordOwner, "fileHandleLock", None)
        if fileHandle is not None:
            try:
                fnReleaseContainerLock(fileHandle)
            except OSError:
                pass
    dictContainerOwners.clear()
    serverHub.app.state.dictSessionOwner.clear()


def _fdictAttestationAnswer(listLines, sLiveAttemptId="attempt1"):
    return {
        "dictCurrentAttestation": None, "listHistory": [],
        "dictLatestReproduction": None, "listReproductionHistory": [],
        "dictInFlight": {"sPhase": "running", "sAttemptId": "attempt1",
                         "sProjectRepoPath": "/workspace/project"},
        "dictLastNoVerdict": None, "dictLastSettled": None,
        "dictUnsettledTeardown": None, "sLiveManifestDigest": "",
        "dictLiveOutput": {
            "sAttemptId": sLiveAttemptId, "saTailLines": listLines,
            "iLineCount": len(listLines) + 300,
            "sStepLabel": "A02", "sStepName": "PlotHistogram",
        },
    }


def _ffnAnswerWith(dictBody):
    """Return a route handler answering ``dictBody`` (one argument: Playwright
    hands the request to any handler that accepts a second)."""
    def fnAnswer(route):
        route.fulfill(status=200, content_type="application/json",
                      body=json.dumps(dictBody))
    return fnAnswer


def _fnServe(page, dictAnswer):
    """Answer the readiness and attestation routes; the attestation from ``dictAnswer``."""
    from vaibify.reproducibility.levelGates import (
        fdictL3ReadinessGaps, fdictLevel2Gaps,
    )
    for sSuffix, dictBody in (
        ("level2/readiness", {"iProofLevel": 2, "dictLevel2Gaps": fdictLevel2Gaps(
            {}, "/nonexistent-repo-for-shape")}),
        ("level3/readiness", {"iProofLevel": 2, "sRecordKind": "attestation",
                              "dictL3ReadinessGaps": fdictL3ReadinessGaps(
                                  {}, "/nonexistent-repo-for-shape")}),
    ):
        page.route("**/api/workflow/**/" + sSuffix, _ffnAnswerWith(dictBody))
    page.route("**/api/workflow/**/level3/attestation", lambda route: route.fulfill(
        status=200, content_type="application/json", body=json.dumps(dictAnswer)))


def _fnOpenTheLevel3Section(page, serverHub, dictAnswer):
    fnOpenTheSeededHostWorkflow(page, serverHub)
    _fnServe(page, dictAnswer)
    page.click('.left-tab[data-panel="proof"]')
    sSection = '.proof-level-section[data-level="3"]'
    page.wait_for_selector(sSection, timeout=15000)
    if page.locator(sSection + ".collapsed").count():
        page.click('.proof-level-section-header[data-level="3"]')
    page.wait_for_selector(".proof-verify-progress", timeout=15000)


def _fiReadTail(page, sProperty):
    return page.evaluate(
        f"(sProperty) => document.querySelector('{S_TAIL}')[sProperty]", sProperty,
    )


@pytest.mark.falsification
def testTheCardShowsTheStepAndTheNewestLines(pageDashboard, serverHub):
    """Kills: a progress card that renders none of the hub's output record."""
    listLines = [f"sample {iLine}" for iLine in range(60)]
    _fnOpenTheLevel3Section(pageDashboard, serverHub, _fdictAttestationAnswer(listLines))
    pageDashboard.wait_for_selector(S_TAIL, timeout=10000)
    sCard = pageDashboard.text_content(".proof-verify-progress")
    assert "Step A02: PlotHistogram" in sCard
    assert "sample 59" in pageDashboard.text_content(S_TAIL)
    assert "The last 60 of 360 lines" in sCard
    assert pageDashboard.listPageErrors == []


def testAnotherRunsOutputIsNeverShownUnderThisOne(pageDashboard, serverHub):
    _fnOpenTheLevel3Section(pageDashboard, serverHub, _fdictAttestationAnswer(
        ["from the previous run"], sLiveAttemptId="attempt0"))
    pageDashboard.wait_for_timeout(1000)
    assert pageDashboard.query_selector(S_TAIL) is None
    assert "from the previous run" not in pageDashboard.text_content(".proof-verify-progress")
    assert pageDashboard.listPageErrors == []


def testTheLogFollowsNewLinesAndStaysWhereTheReaderScrolled(pageDashboard, serverHub):
    dictAnswer = _fdictAttestationAnswer([f"sample {iLine}" for iLine in range(80)])
    _fnOpenTheLevel3Section(pageDashboard, serverHub, dictAnswer)
    pageDashboard.wait_for_selector(S_TAIL, timeout=10000)
    dictAnswer["dictLiveOutput"]["saTailLines"] = (
        dictAnswer["dictLiveOutput"]["saTailLines"] + ["sample 80", "sample 81"])
    pageDashboard.wait_for_function(
        f"() => document.querySelector('{S_TAIL}').textContent.includes('sample 81')",
        timeout=15000,
    )
    iBottom = _fiReadTail(pageDashboard, "scrollHeight") - _fiReadTail(
        pageDashboard, "clientHeight")
    assert iBottom > 0
    assert _fiReadTail(pageDashboard, "scrollTop") >= iBottom - 8
    pageDashboard.evaluate(f"() => {{ document.querySelector('{S_TAIL}').scrollTop = 40; }}")
    dictAnswer["dictLiveOutput"]["saTailLines"] = (
        dictAnswer["dictLiveOutput"]["saTailLines"] + ["sample 82"])
    pageDashboard.wait_for_function(
        f"() => document.querySelector('{S_TAIL}').textContent.includes('sample 82')",
        timeout=15000,
    )
    assert _fiReadTail(pageDashboard, "scrollTop") == 40
    assert pageDashboard.listPageErrors == []
