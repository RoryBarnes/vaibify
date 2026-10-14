"""Clean Outputs applies the blocks the route wrote, never a mirror of its own.

The clean route writes each automatic step a fresh verification block
and empty run stats, then saves the workflow. The client used to mirror
that by a rule of its own -- set every key of the old block to
"untested" -- which turned ``listModifiedFiles`` (a list) into a string
and broke the next render with ``listModified.map is not a function``,
while the route had written a block without that key at all. Two
authorities on one block, disagreeing on every key that is not an axis
(found by a browser probe, 2026-10-13).

Driven in HOST mode with the clean route answered by the test, so what
is under test is the client's half of the contract: the answer's blocks
replace the steps' blocks wholesale, and a step carrying a modified-
files list before the clean renders without error after it. The
route's half -- that its answer is exactly what it saved -- is in
``tests/testCoverageBoost.py``.

ONE test, because ``serverHub`` is module-scoped and a browser context
that opens a project holds its claim through the reconnect grace.
"""

import json

import pytest

from tests.browser.conftest import fnOpenTheSeededHostWorkflow


pytestmark = pytest.mark.browser

S_CLEAN_ROUTE_GLOB = "**/api/pipeline/*/clean"

DICT_CLEARED_BLOCK = {
    "sUnitTest": "untested",
    "sUser": "untested",
    "sIntegrity": "untested",
    "sQualitative": "untested",
    "sQuantitative": "untested",
}


def _fnAnswerTheCleanWith(page, listClearedSteps):
    """Answer the clean route with the blocks a route would have written."""
    def fnHandle(routeIntercepted):
        routeIntercepted.fulfill(
            status=200, content_type="application/json",
            body=json.dumps(
                {"bSuccess": True, "listClearedSteps": listClearedSteps},
            ),
        )
    page.route(S_CLEAN_ROUTE_GLOB, fnHandle)


def _fnGiveTheFirstStepAModifiedFilesList(page):
    """Put the client's first step in the state the old mirror broke on."""
    page.evaluate(
        "() => {"
        "  var dictStep = VaibifyApp.fdictGetWorkflow().listSteps[0];"
        "  dictStep.dictVerification = Object.assign({}, "
        "    dictStep.dictVerification || {}, "
        "    {sUser: 'verified', listModifiedFiles: ['results/table.csv']});"
        "  VaibifyApp.fnRenderStepList();"
        "}"
    )


@pytest.mark.falsification
def testCleanOutputsAppliesTheBlocksTheRouteWrote(pageDashboard, serverHub):
    """Kills: the client re-deriving the cleared block instead of applying it."""
    fnOpenTheSeededHostWorkflow(pageDashboard, serverHub)
    iSteps = pageDashboard.evaluate(
        "() => VaibifyApp.fdictGetWorkflow().listSteps.length")
    assert iSteps > 0
    listCleared = [
        {"iStepIndex": iIndex, "dictVerification": dict(DICT_CLEARED_BLOCK),
         "dictRunStats": {}}
        for iIndex in range(iSteps)
    ]
    _fnAnswerTheCleanWith(pageDashboard, listCleared)
    _fnGiveTheFirstStepAModifiedFilesList(pageDashboard)
    pageDashboard.listPageErrors.clear()

    pageDashboard.click("#toolbarMenuRun .toolbar-menu-trigger")
    pageDashboard.wait_for_selector(
        "#btnCleanOutputs", state="visible", timeout=5000)
    pageDashboard.click("#btnCleanOutputs")
    pageDashboard.wait_for_selector("#modalConfirm", timeout=5000)
    pageDashboard.click("#btnConfirmOk")
    pageDashboard.wait_for_selector(
        "text=every step is back to never-run", timeout=10000)

    listBlocks = pageDashboard.evaluate(
        "() => VaibifyApp.fdictGetWorkflow().listSteps"
        ".map(function (dictStep) { return dictStep.dictVerification; })")
    assert listBlocks == [DICT_CLEARED_BLOCK] * iSteps, listBlocks
    assert pageDashboard.listPageErrors == [], pageDashboard.listPageErrors
