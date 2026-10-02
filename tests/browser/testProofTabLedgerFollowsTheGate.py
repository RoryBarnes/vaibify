"""Every PROOF requirement row shows the verdict the gate gave, and only that.

The tab renders the backend's readiness answers; it must never derive a
verdict of its own. Two properties are driven here against the real
renderer with the real answer shapes:

- each row's state equals the boolean the gate returned for its key, in
  both directions, with the answers alternating so a renderer that
  painted every row alike (or inverted them) cannot pass;
- when a later readiness request fails, no verdict from the earlier
  answer stays on screen. A Level 2 failure replaces the ledger with the
  failure; a Level 3 failure turns the Level 3 rows into "no answer",
  rather than into a "not met" the gate never gave.
"""

import json

import pytest

from tests.browser.conftest import fnOpenTheSeededHostWorkflow

pytestmark = pytest.mark.browser

S_ROW = ".proof-req-entry[data-req-key]"


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


def _fdictAlternatingGaps(fdictFactory):
    """Return a real-shaped gap dict whose booleans alternate by position."""
    dictGaps = fdictFactory({}, "/nonexistent-repo-for-shape")
    iPosition = 0
    for sKey, objValue in list(dictGaps.items()):
        if isinstance(objValue, bool) and sKey.startswith("b"):
            dictGaps[sKey] = (iPosition % 2 == 0)
            iPosition += 1
    return dictGaps


def _fdictVerdicts():
    from vaibify.reproducibility.levelGates import (
        fdictL3ReadinessGaps, fdictLevel2Gaps,
    )
    return (_fdictAlternatingGaps(fdictLevel2Gaps),
            _fdictAlternatingGaps(fdictL3ReadinessGaps))


def _fnServe(page, sSuffix, dictBody, dictControl):
    """Answer a readiness route from ``dictControl``; 500 when asked to."""
    def fnAnswer(route):
        if dictControl.get(sSuffix) == 500:
            route.fulfill(
                status=500, content_type="application/json",
                body='{"detail": "the gate crashed"}')
            return
        route.fulfill(
            status=200, content_type="application/json",
            body=json.dumps(dictBody))
    page.route("**/api/workflow/**/" + sSuffix, fnAnswer)


def _fnServeLedger(page, dictControl):
    dictLevel2, dictLevel3 = _fdictVerdicts()
    _fnServe(page, "level2/readiness", {
        "iProofLevel": 1, "dictLevel2Gaps": dictLevel2}, dictControl)
    _fnServe(page, "level3/readiness", {
        "iProofLevel": 1, "dictL3ReadinessGaps": dictLevel3,
        "sRecordKind": "attestation"}, dictControl)
    page.route(
        "**/api/workflow/**/level3/attestation",
        lambda route: route.fulfill(
            status=200, content_type="application/json",
            body=json.dumps({
                "dictCurrentAttestation": None, "listHistory": [],
                "dictLatestReproduction": None,
                "listReproductionHistory": [], "dictInFlight": None,
                "dictLastNoVerdict": None, "dictUnsettledTeardown": None,
                "sLiveManifestDigest": ""})))
    return dictLevel2, dictLevel3


def _fnOpenTheLedger(page, serverHub, dictControl):
    fnOpenTheSeededHostWorkflow(page, serverHub)
    dictVerdicts = _fnServeLedger(page, dictControl)
    page.click('.left-tab[data-panel="proof"]')
    page.wait_for_selector(".proof-level-section-header", timeout=15000)
    for iLevel in (1, 2, 3):
        _fnExpandLevel(page, iLevel)
    page.wait_for_selector(S_ROW, timeout=15000)
    return dictVerdicts


def _fnExpandLevel(page, iLevel):
    sSection = f'.proof-level-section[data-level="{iLevel}"]'
    page.wait_for_selector(sSection, timeout=10000)
    if page.locator(sSection + ".collapsed").count():
        page.click(f'.proof-level-section-header[data-level="{iLevel}"]')


def _fdictRowStates(page):
    return page.evaluate(
        """() => Object.fromEntries(Array.from(
            document.querySelectorAll('.proof-req-entry[data-req-key]'))
            .map((el) => [el.dataset.reqKey,
                el.className.replace('proof-req-entry state-', '')]))""")


@pytest.mark.falsification
def testEveryRowShowsTheVerdictTheGateGave(pageDashboard, serverHub):
    """Kills: deriving a row's state from anything but the gate's answer
    (a renderer that painted every row alike, or inverted them)."""
    dictLevel2, dictLevel3 = _fnOpenTheLedger(pageDashboard, serverHub, {})
    dictStates = _fdictRowStates(pageDashboard)
    dictVerdicts = {**dictLevel2, **dictLevel3}
    listChecked = []
    for sKey, sState in dictStates.items():
        if sKey not in dictVerdicts or not isinstance(
            dictVerdicts[sKey], bool,
        ):
            continue
        listChecked.append(sKey)
        sExpected = "satisfied" if dictVerdicts[sKey] else "unsatisfied"
        assert sState == sExpected, (sKey, sState, dictVerdicts[sKey])
    assert len(listChecked) >= 10, (
        "too few rows carried a verdict to prove anything", listChecked)
    assert {dictStates[sKey] for sKey in listChecked} == {
        "satisfied", "unsatisfied"}, "the alternation never reached the page"
    assert pageDashboard.listPageErrors == []


@pytest.mark.falsification
def testAFailedLevelThreeReadinessLeavesNoVerdictOnScreen(
    pageDashboard, serverHub,
):
    """Kills: painting "not met" (or keeping the old "met") for a row
    whose readiness request failed."""
    dictControl = {}
    _dictLevel2, dictLevel3 = _fnOpenTheLedger(
        pageDashboard, serverHub, dictControl)
    dictBefore = _fdictRowStates(pageDashboard)
    sLevel3Key = next(
        sKey for sKey, bValue in dictLevel3.items()
        if sKey in dictBefore and bValue is True)
    assert dictBefore[sLevel3Key] == "satisfied"
    dictControl["level3/readiness"] = 500
    pageDashboard.evaluate("() => VaibifyProofTab.fnRender()")
    pageDashboard.wait_for_function(
        """(sKey) => {
            const el = document.querySelector(
                '.proof-req-entry[data-req-key="' + sKey + '"]');
            return el && el.classList.contains('state-unknown');
        }""", arg=sLevel3Key, timeout=10000)
    dictAfter = _fdictRowStates(pageDashboard)
    for sKey in dictLevel3:
        if sKey in dictAfter:
            assert dictAfter[sKey] == "unknown", (sKey, dictAfter[sKey])
    assert pageDashboard.listPageErrors == []


@pytest.mark.falsification
def testAFailedLevelTwoReadinessReplacesTheLedgerWithTheFailure(
    pageDashboard, serverHub,
):
    """Kills: keeping the earlier answer's rows after the request failed."""
    dictControl = {}
    _fnOpenTheLedger(pageDashboard, serverHub, dictControl)
    dictControl["level2/readiness"] = 500
    pageDashboard.evaluate("() => VaibifyProofTab.fnRender()")
    pageDashboard.wait_for_selector(
        ".proof-empty:has-text('Could not load readiness')", timeout=10000)
    assert pageDashboard.locator(S_ROW).count() == 0
    assert pageDashboard.listPageErrors == []
