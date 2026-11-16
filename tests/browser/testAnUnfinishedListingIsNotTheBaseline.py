"""An unfinished test-file listing is not the page's baseline.

The page announces a test file as new only when an answer after its
first one names it: the first answer is the baseline. The server answers
``dictTestFileChanges: null`` for a listing that did not finish
(tests/testATestFileTheStepRunsIsNeverNew.py). If the page took that
answer as its baseline, the next answer would announce every existing
test file as new, which is what a second computer showed on opening a
project with nothing run (2026-11-04).

The real page opens the seeded workflow, with the open response and the
poll altered in flight: unfinished, then a listing naming one file no
command runs, then a listing naming that file and two more. Every toast
is recorded as it is raised, so one that fades is still counted.
"""

import json

import pytest

from tests.browser.conftest import fnOpenTheSeededHostWorkflow
from tests.browser.testAVerificationIsFollowedOnTheProjectBlock import (
    fixtureDropClaimsBetweenTests,  # noqa: F401 -- autouse fixture
)


pytestmark = pytest.mark.browser

# The test-file check raises its toast through VaibifyApp.fnShowToast,
# wrapped here as soon as the page defines it, before any poll. The
# module is a top-level const, so it is never a property of window.
S_RECORD_EVERY_TOAST = """
window.listToastTexts = [];
const iWrapTimer = setInterval(() => {
    if (typeof VaibifyApp === "undefined") return;
    clearInterval(iWrapTimer);
    const fnShowToastAsWritten = VaibifyApp.fnShowToast;
    VaibifyApp.fnShowToast = function (sMessage) {
        window.listToastTexts.push(String(sMessage));
        return fnShowToastAsWritten.apply(this, arguments);
    };
}, 5);
"""
S_ANNOUNCED = "new test file(s) discovered"
LIST_ANSWERS = [
    None,
    {"0": {"listNew": ["test_extra.py"], "listMissing": []}},
    {"0": {"listNew": ["test_extra.py", "test_second.py", "test_third.py"],
           "listMissing": []}},
]


@pytest.fixture(autouse=True)
def fixtureStopInterceptingThePoll(pageDashboard):
    """Drop the routes before the page closes."""
    yield
    pageDashboard.unroute_all(behavior="ignoreErrors")


def _fnAnswerWith(routeRequest, fnAlter):
    responseServed = routeRequest.fetch()
    routeRequest.fulfill(
        response=responseServed, body=json.dumps(fnAlter(responseServed.json())),
        headers={"content-type": "application/json"})


def _fnRouteTheListings(pageDashboard):
    """Open unfinished, then serve each later listing in turn, the last one after."""
    listServed = []

    def fdictAlterOpen(dictBody):
        if dictBody.get("dictFileStatus"):
            dictBody["dictFileStatus"]["dictTestFileChanges"] = None
        return dictBody

    def fdictAlterPoll(dictBody):
        dictBody["dictTestFileChanges"] = LIST_ANSWERS[
            min(len(listServed), len(LIST_ANSWERS) - 1)]
        listServed.append(dictBody["dictTestFileChanges"])
        return dictBody

    pageDashboard.route("**/api/connect/**", lambda routeRequest: _fnAnswerWith(
        routeRequest, fdictAlterOpen))
    pageDashboard.route("**/file-status*", lambda routeRequest: _fnAnswerWith(
        routeRequest, fdictAlterPoll))


@pytest.mark.falsification
def testAnUnfinishedListingIsNotTheBaseline(pageDashboard, serverHub):
    """Only the two files added after the first finished listing are announced.

    Kills: handing the page's test-file check an unfinished answer, which
    spends its baseline, so the first finished listing's file is
    announced too.
    """
    pageDashboard.add_init_script(S_RECORD_EVERY_TOAST)
    _fnRouteTheListings(pageDashboard)
    fnOpenTheSeededHostWorkflow(pageDashboard, serverHub)
    pageDashboard.wait_for_function(
        "(sAnnounced) => window.listToastTexts.some("
        "(sText) => sText.includes(sAnnounced))",
        arg=S_ANNOUNCED, timeout=30000,
    )
    listAnnounced = [
        sText for sText in pageDashboard.evaluate("() => window.listToastTexts")
        if S_ANNOUNCED in sText]
    assert len(listAnnounced) == 1
    assert ": 2 " + S_ANNOUNCED in listAnnounced[0]
    assert pageDashboard.listPageErrors == []
