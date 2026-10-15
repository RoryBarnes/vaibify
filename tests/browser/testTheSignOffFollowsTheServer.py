"""The page renders the server's sign-off and never re-derives it.

The dashboard used to reset a ``passed`` sign-off to ``untested`` in its
own copy whenever a plot's mtime was newer than the sign-off, on every
poll. The server now judges a sign-off by the bytes of the step's plots,
so a byte-identical redraw keeps it ``passed`` there while the page
showed it voided. The poll now ships every step's stored sign-off
(``dictSignOffByStep``) and the page renders that: a newer plot mtime
alone changes nothing on screen, and a sign-off the server voided
reaches a client that is on the current workflow epoch.

The real page loads the real poll, altered in flight to the case under
test, and the page's own state is read after it has applied two polls.
"""

import json

import pytest

from tests.browser.conftest import fnOpenTheSeededHostWorkflow
from tests.browser.testAVerificationIsFollowedOnTheProjectBlock import (
    fixtureDropClaimsBetweenTests,  # noqa: F401 -- autouse fixture
)

pytestmark = pytest.mark.browser

S_SIGNED_AT = "2026-01-01 00:00:00 UTC"
S_PLOT_MTIME_IN_2100 = "4102444800"
I_POLLS_TO_APPLY = 2

_S_SIGN_STEP_ZERO_OFF = """(sSignedAt) => {
    const dictStep = VaibifyApp.fdictGetWorkflow().listSteps[0];
    dictStep.dictVerification = Object.assign(
        {}, dictStep.dictVerification || {},
        {sUser: "passed", sLastUserUpdate: sSignedAt});
}"""

_S_STEP_ZERO_SIGN_OFF = """() => VaibifyApp.fdictGetWorkflow()
    .listSteps[0].dictVerification.sUser"""


@pytest.fixture(autouse=True)
def fixtureStopInterceptingThePoll(pageDashboard):
    """Drop the poll route before the page closes."""
    yield
    pageDashboard.unroute_all(behavior="ignoreErrors")


def _fsSignOffAfterPollsSaying(pageDashboard, serverHub, sServerUser):
    """Answer every poll with a newer plot and the server's sUser; read back."""
    listPolls = []

    def fnHandle(routePoll):
        responsePoll = routePoll.fetch()
        dictBody = responsePoll.json()
        dictBody["dictMaxPlotMtimeByStep"] = {"0": S_PLOT_MTIME_IN_2100}
        dictBody.setdefault("dictSignOffByStep", {})["0"] = {
            "sUser": sServerUser, "sLastUserUpdate": S_SIGNED_AT}
        listPolls.append(True)
        routePoll.fulfill(
            response=responsePoll, body=json.dumps(dictBody),
            headers={"content-type": "application/json"})

    fnOpenTheSeededHostWorkflow(pageDashboard, serverHub)
    pageDashboard.evaluate(_S_SIGN_STEP_ZERO_OFF, S_SIGNED_AT)
    pageDashboard.route("**/file-status*", fnHandle)
    iTarget = I_POLLS_TO_APPLY + 1
    for _ in range(160):
        if len(listPolls) >= iTarget:
            break
        pageDashboard.wait_for_timeout(250)
    assert len(listPolls) >= iTarget, "the page stopped polling"
    pageDashboard.wait_for_timeout(500)
    return pageDashboard.evaluate(_S_STEP_ZERO_SIGN_OFF)


@pytest.mark.falsification
def testANewerPlotAloneNeverVoidsTheSignOffOnScreen(pageDashboard, serverHub):
    """Kills: the page resetting a sign-off because a plot's mtime is newer."""
    assert _fsSignOffAfterPollsSaying(
        pageDashboard, serverHub, "passed") == "passed"
    assert pageDashboard.listPageErrors == []


@pytest.mark.falsification
def testASignOffTheServerVoidedReachesThePage(pageDashboard, serverHub):
    """Kills: the page not applying the server's shipped sign-off."""
    assert _fsSignOffAfterPollsSaying(
        pageDashboard, serverHub, "stale") == "stale"
    assert pageDashboard.listPageErrors == []
