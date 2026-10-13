"""A refused test run says why, and records no verdict.

Accepting a generated test posts it to save-and-run. The page read
every answer as a test result, so a refusal -- a test file the hub may
not write, or test-support files it could not restore, which would
leave the run unrecordable -- rendered as "Some tests failed" and
stamped the step's unit-test verdict failed, for tests that never ran.
The server's reason, which names the file to fix, was never shown.

Driven through the real Accept button in a real browser; only the
hub's answer to the save-and-run request is rewritten.
"""

import json

import pytest

from tests.browser.conftest import fnOpenTheSeededHostWorkflow
from tests.browser.testAVerificationIsFollowedOnTheProjectBlock import (
    fixtureDropClaimsBetweenTests,
)


pytestmark = pytest.mark.browser

S_REFUSAL = (
    "Could not update 1 test-support file(s). The tests were not run: "
    "their result could not have been recorded."
)


@pytest.mark.falsification
def test_a_refused_save_and_run_shows_the_reason_and_records_nothing(
    pageDashboard, serverHub,
):
    """Kills: rendering a refusal as a set of failed tests."""
    fnOpenTheSeededHostWorkflow(
        pageDashboard, serverHub, bAwaitProjectBlock=True)
    pageDashboard.route(
        "**/api/steps/**/save-and-run-test",
        lambda route: route.fulfill(
            status=409, content_type="application/json",
            body=json.dumps({"detail": S_REFUSAL})))
    sVerdictBefore = pageDashboard.evaluate("""() => {
        const dictStep = VaibifyApp.fdictGetWorkflow().listSteps[0];
        return (dictStep.dictVerification || {}).sUnitTest || "";
    }""")
    pageDashboard.evaluate("""() => VaibifyFigureViewer.fnDisplayGeneratedTest(
        'MakeNumbers/tests/test_qualitative_new.py',
        'def testOne():\\n    assert True\\n', 0)""")
    pageDashboard.click("#viewportA .editor-toolbar .btn-primary")
    pageDashboard.wait_for_function(
        "() => document.getElementById('viewportA')"
        ".textContent.includes('were not run')", timeout=10000)
    sShown = pageDashboard.locator("#viewportA").inner_text()
    assert "Some tests failed" not in sShown, sShown
    assert S_REFUSAL in sShown, sShown
    sVerdictAfter = pageDashboard.evaluate("""() => {
        const dictStep = VaibifyApp.fdictGetWorkflow().listSteps[0];
        return (dictStep.dictVerification || {}).sUnitTest || "";
    }""")
    assert sVerdictAfter == sVerdictBefore, (
        f"a refused run changed the verdict to {sVerdictAfter!r}")
    assert pageDashboard.listPageErrors == []
