"""A tier with nothing to check reads N/A and says why; a refusal names files.

Test generation used to write a placeholder test that asserted True for
a tier whose outputs offered nothing to check, so the dashboard showed
it green "Passed" (alpha tester, 2026-10-23). Such a tier is now N/A
(ruling R3): no file, the grey "unnecessary" state, and the generator's
reason beside it -- because N/A alone could be read as a tier nobody got
round to. And generating before a step's outputs exist is refused; the
dashboard's toast is the hub's sentence, naming the missing files.
"""

import json

import pytest

from tests.browser.conftest import fnOpenTheSeededHostWorkflow
from tests.browser.testUndeclaredTestCategorySaysNotApplicable import (
    _fnExpandTheStepsTestCategories,
)


pytestmark = pytest.mark.browser

S_REASON = "The outputs have no column names or JSON keys to check."
S_GENERATE_ROUTE_GLOB = "**/api/steps/*/0/generate-test"


def _fnRenderTheFirstStepWithATierNotApplicable(pageDashboard):
    pageDashboard.evaluate(
        """(sReason) => {
            const dictStep = VaibifyApp.fdictGetWorkflow().listSteps[0];
            dictStep.dictTests = dictStep.dictTests || {};
            dictStep.dictTests.dictQualitative = {
                saCommands: [], sFilePath: "",
                sNotApplicableReason: sReason,
            };
            dictStep.dictVerification = Object.assign(
                {}, dictStep.dictVerification || {},
                {sQualitative: "unnecessary"});
            VaibifyApp.fnRenderStepList();
        }""",
        S_REASON,
    )


def testANotApplicableTierReadsGreyNotApplicableWithItsReason(
    pageDashboard, serverHub,
):
    fnOpenTheSeededHostWorkflow(pageDashboard, serverHub)
    _fnExpandTheStepsTestCategories(pageDashboard)
    _fnRenderTheFirstStepWithATierNotApplicable(pageDashboard)
    pageDashboard.wait_for_selector(
        ".test-not-applicable-note", timeout=10000,
    )
    elBadge = pageDashboard.locator(
        '.sub-test-row[data-approver="qualitative"] .verification-badge',
    ).first
    sBadge = elBadge.text_content().strip()
    assert "N/A" in sBadge, sBadge
    assert "Passed" not in sBadge, sBadge
    assert "state-unnecessary" in elBadge.get_attribute("class")
    assert pageDashboard.locator(
        ".test-not-applicable-note").first.text_content() == S_REASON
    assert pageDashboard.listPageErrors == []


def testGeneratingBeforeTheOutputsExistToastsTheMissingFiles(
    pageDashboard, serverHub,
):
    """The refusal is the hub's; the page shows its sentence as a toast."""
    fnOpenTheSeededHostWorkflow(pageDashboard, serverHub)
    sMessage = (
        "Tests were not generated for step A01: its declared outputs do "
        "not exist yet (numbers.json). Run the step first, then "
        "generate its tests."
    )
    pageDashboard.route(
        S_GENERATE_ROUTE_GLOB,
        lambda routeIntercepted: routeIntercepted.fulfill(
            status=409, content_type="application/json",
            body=json.dumps({"detail": {
                "sRefusal": "outputs-missing",
                "listMissingFiles": ["numbers.json"],
                "sMessage": sMessage,
            }}),
        ),
    )
    pageDashboard.evaluate("() => VaibifyTestManager.fnGenerateTests(0)")
    pageDashboard.wait_for_function(
        "(sMessage) => Array.from(document.querySelectorAll('.toast.error'))"
        ".some((el) => el.textContent.includes(sMessage))",
        arg=sMessage, timeout=10000,
    )
    assert pageDashboard.listPageErrors == []
