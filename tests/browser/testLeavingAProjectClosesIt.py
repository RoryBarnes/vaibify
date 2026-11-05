"""Leaving a project for its Project Hub closes it.

Admin > Projects used to show the Project Hub with the project still
open behind it: its file poll kept running, the Level 3 class stayed on
the body (so the hub's tile names turned the Level 3 pink), and both
notice banners kept the last project's answer, which then showed on a
reopen until the new answer arrived. The pipeline socket deliberately
stays open there, because the claim is released by its closing, so a
run event can still arrive after the researcher left.

What these pin, against the real hub and a real browser:

* the hub's tile names wear the Level 0 pale blue after a Level 3
  project, and both banners are empty;
* a notice answer that arrives after the researcher left renders
  nothing;
* a run event that arrives after the researcher left repaints nothing
  and restarts no poll;
* a reopen shows the reopened project's answer, never the previous
  one's while the new one is on its way.

The notice reads are intercepted and answered from the test, so the
answer and its timing are the test's own; everything else is the real
page against the real hub.

WHAT THIS DOES NOT COVER: a real container behind the socket (the live
QuickStart journey drives the Project Hub round trip against one), and
what the binding and token routes decide on the server.
"""

import json
import re

import pytest

from tests.browser.conftest import (
    S_HOST_STEP_NAME,
    S_HOST_WORKFLOW_NAME,
    fnOpenTheSeededHostWorkflow,
)
from tests.browser.testALostClaimRecoversWhereverItIsMet import (
    _fnRouteThePipelineSocket,
    _fnStopTheDashboardPollers,
)
from tests.browser.testTheLevelColorHasOneHome import (
    DICT_LEVEL_COLORS,
    S_SET_LEVEL_THROUGH_THE_APP,
)


pytestmark = pytest.mark.browser

_RE_BINDING_NOTICE_READ = re.compile(
    r".*/api/bindings/[^/?]+\?bFetchEvidence=false$")
_RE_TOKEN_NOTICE_READ = re.compile(r".*/api/zenodo-tokens/[^/?]+$")
S_INVALID_RECORD_SENTENCE = (
    "The binding record for this project can't be read, so nothing is "
    "confirmed. Move it aside to start again.")
DICT_INVALID_RECORD = {
    "sProjectUuid": "f" * 32, "sWorkflowKey": "projects/study.json::study",
    "sWorkflowName": "study", "bEnvironmentArchiveInUse": False,
    "dictRecordState": {
        "bValid": False, "sMessage": S_INVALID_RECORD_SENTENCE},
    "listEntries": [],
}
DICT_VALID_RECORD = dict(
    DICT_INVALID_RECORD, dictRecordState={"bValid": True})
DICT_TOKEN_AWAITING = {
    "bApplicable": True, "bAwaitingDecision": True, "bRotationAdvised": False,
    "dictActive": {"sandbox": False, "production": False},
    "dictTokenPages": {},
    "listSlots": [{
        "sSlot": "zenodo_token_sandbox", "sState": "removed", "sReason": "",
        "sInstance": "sandbox", "sProposedInstance": "sandbox",
        "bPending": True, "bAwaitingDecision": True, "sConflictChoice": "",
        "dictValidation": {"sOutcome": "accepted", "sDetail": "", "sAt": "now"},
        "listDepositTitles": ["A deposit the server fetched"],
    }],
}
T_PALE_BLUE = "rgb(%d, %d, %d)" % DICT_LEVEL_COLORS[0]
_LIST_HELD_OF_THE_RUNNING_TEST = []

S_BANNERS_STATE = """() => {
    return ["zenodoTokenBanner", "remoteBindingBanner"].map(function (sId) {
        var elBanner = document.getElementById(sId);
        return {sId: sId, sDisplay: elBanner.style.display,
                sText: elBanner.textContent};
    });
}"""

S_COUNT_PAINTS = """() => {
    window.__dictPaints = {iRenders: 0, iPollStarts: 0, iToasts: 0};
    ["fnRenderStepList", "fnStartFileChangePolling", "fnShowToast"].forEach(
        function (sName, iIndex) {
            var fnOriginal = VaibifyApp[sName];
            var sCounter = ["iRenders", "iPollStarts", "iToasts"][iIndex];
            VaibifyApp[sName] = function () {
                window.__dictPaints[sCounter] += 1;
                return fnOriginal.apply(this, arguments);
            };
        });
}"""


@pytest.fixture(autouse=True)
def fnReleaseEveryHeldRoute(pageDashboard):
    """Abort every request a test held, before its page closes.

    A route still held when the page closes leaves the Playwright
    connection with a pending handler, and the next test on that worker
    fails at its first navigation. Depending on ``pageDashboard`` makes
    this teardown run before the page's.
    """
    yield
    from playwright.sync_api import Error as PlaywrightError
    while _LIST_HELD_OF_THE_RUNNING_TEST:
        try:
            _LIST_HELD_OF_THE_RUNNING_TEST.pop().abort()
        except PlaywrightError:
            pass  # already answered, or the request is already gone


class _NoticeReads:
    """Answer the page's notice reads in order, or hold the ones asked for."""

    def __init__(self, pageDashboard, rePattern, listAnswers, setHeld=()):
        self.listAnswers = list(listAnswers)
        self.setHeld = set(setHeld)
        self.listHeld = []
        self.iReads = 0
        pageDashboard.route(rePattern, self._fnRead)

    def _fnRead(self, routeIntercepted):
        if routeIntercepted.request.method != "GET":
            routeIntercepted.fallback()
            return
        iRead = self.iReads
        self.iReads += 1
        if iRead in self.setHeld:
            self.listHeld.append((routeIntercepted, self._fdictAnswer(iRead)))
            _LIST_HELD_OF_THE_RUNNING_TEST.append(routeIntercepted)
            return
        _fnFulfill(routeIntercepted, self._fdictAnswer(iRead))

    def _fdictAnswer(self, iRead):
        return self.listAnswers[min(iRead, len(self.listAnswers) - 1)]

    def fnReleaseHeld(self, pageDashboard, rePattern):
        routeHeld, dictAnswer = self.listHeld.pop(0)
        _LIST_HELD_OF_THE_RUNNING_TEST.remove(routeHeld)
        with pageDashboard.expect_response(rePattern):
            _fnFulfill(routeHeld, dictAnswer)
        # The page parses the answer and continues on a later task.
        pageDashboard.wait_for_timeout(500)


def _fnFulfill(routeIntercepted, dictAnswer):
    routeIntercepted.fulfill(
        status=200, content_type="application/json",
        body=json.dumps(dictAnswer))


def _fnWaitFor(pageDashboard, fbDone, sWhat):
    for _ in range(150):
        if fbDone():
            return
        pageDashboard.wait_for_timeout(100)
    raise AssertionError(f"timed out waiting for {sWhat}")


def _fnGoToTheProjectHub(pageDashboard):
    pageDashboard.evaluate(
        "() => document.getElementById('btnAdminWorkflows').click()")
    pageDashboard.wait_for_selector("#modalConfirm", timeout=10000)
    pageDashboard.click("#btnConfirmOk")
    pageDashboard.wait_for_selector(
        "#workflowPicker #listWorkflows .project-card .name",
        state="visible", timeout=20000)


def _fnReopenTheWorkflow(pageDashboard):
    pageDashboard.click(
        f"#listWorkflows .project-card:has-text('{S_HOST_WORKFLOW_NAME}')")
    pageDashboard.wait_for_selector(
        f"#listSteps >> text={S_HOST_STEP_NAME}", timeout=20000)


def _fnAwaitBothBannersFilled(pageDashboard):
    pageDashboard.wait_for_selector(
        "#remoteBindingBanner [data-record-invalid]", timeout=15000)
    pageDashboard.wait_for_selector(
        "#zenodoTokenBanner button[data-decision]", timeout=15000)


def _fnAssertBothBannersEmpty(pageDashboard):
    for dictBanner in pageDashboard.evaluate(S_BANNERS_STATE):
        assert dictBanner["sDisplay"] == "none", dictBanner
        assert dictBanner["sText"] == "", dictBanner


@pytest.mark.falsification
def test_the_project_hub_after_a_level_three_project_wears_pale_blue(
    pageDashboard, serverHub,
):
    """The hub's tile names and banners belong to no project.

    Kills: Admin > Projects showing the hub without closing the open
    project, which left the Level 3 class on the body and both notices
    filled.
    """
    _NoticeReads(pageDashboard, _RE_BINDING_NOTICE_READ, [DICT_INVALID_RECORD])
    _NoticeReads(pageDashboard, _RE_TOKEN_NOTICE_READ, [DICT_TOKEN_AWAITING])
    fnOpenTheSeededHostWorkflow(pageDashboard, serverHub)
    _fnAwaitBothBannersFilled(pageDashboard)
    # The seeded project is at Level 0, so its own poll would take the
    # class away again and hide the defect; stopped, nothing but the
    # close can remove it.
    _fnStopTheDashboardPollers(pageDashboard, serverHub)
    assert pageDashboard.evaluate(S_SET_LEVEL_THROUGH_THE_APP, 3) == [
        "proof-level-3"]
    _fnGoToTheProjectHub(pageDashboard)
    sNameColor = pageDashboard.evaluate("""() => getComputedStyle(
        document.querySelector('#listWorkflows .project-card .name')).color""")
    assert sNameColor == T_PALE_BLUE
    assert pageDashboard.evaluate("""() => Array.from(document.body.classList)
        .filter((s) => s.startsWith('proof-level-'))""") == []
    _fnAssertBothBannersEmpty(pageDashboard)


@pytest.mark.falsification
def test_a_binding_answer_that_lands_in_the_project_hub_renders_nothing(
    pageDashboard, serverHub,
):
    """Kills: the bindings refresh rendering an answer for a view that left."""
    readsBinding = _NoticeReads(
        pageDashboard, _RE_BINDING_NOTICE_READ, [DICT_INVALID_RECORD],
        setHeld={0})
    fnOpenTheSeededHostWorkflow(pageDashboard, serverHub)
    _fnWaitFor(pageDashboard, lambda: readsBinding.listHeld,
               "the page's binding notice read")
    _fnGoToTheProjectHub(pageDashboard)
    readsBinding.fnReleaseHeld(pageDashboard, _RE_BINDING_NOTICE_READ)
    _fnAssertBothBannersEmpty(pageDashboard)
    assert S_INVALID_RECORD_SENTENCE not in pageDashboard.inner_text("body")


@pytest.mark.falsification
def test_a_token_answer_that_lands_in_the_project_hub_renders_nothing(
    pageDashboard, serverHub,
):
    """Kills: the token refresh rendering an answer for a view that left."""
    readsToken = _NoticeReads(
        pageDashboard, _RE_TOKEN_NOTICE_READ, [DICT_TOKEN_AWAITING],
        setHeld={0})
    fnOpenTheSeededHostWorkflow(pageDashboard, serverHub)
    _fnWaitFor(pageDashboard, lambda: readsToken.listHeld,
               "the page's token notice read")
    _fnGoToTheProjectHub(pageDashboard)
    readsToken.fnReleaseHeld(pageDashboard, _RE_TOKEN_NOTICE_READ)
    _fnAssertBothBannersEmpty(pageDashboard)
    assert "A deposit the server fetched" not in pageDashboard.inner_text(
        "body")


@pytest.mark.falsification
def test_a_run_event_that_arrives_in_the_project_hub_repaints_nothing(
    pageDashboard, serverHub,
):
    """The socket stays open in the hub; what it carries there is dropped.

    Kills: the pipeline-event dispatch handling events with no project
    open, so a run completing after departure restarted the closed
    project's file poll and repainted its step list.
    """
    listRoutes = _fnRouteThePipelineSocket(pageDashboard)
    fnOpenTheSeededHostWorkflow(pageDashboard, serverHub)
    pageDashboard.evaluate(
        "() => VaibifyPipelineRunner.fnConnectPipelineWebSocket()")
    pageDashboard.wait_for_function(
        "() => VaibifyWebSocket.fbIsOpen()", timeout=15000)
    _fnGoToTheProjectHub(pageDashboard)
    assert pageDashboard.evaluate("() => VaibifyWebSocket.fbIsOpen()"), (
        "the Project Hub must keep the socket whose closing releases the claim")
    pageDashboard.evaluate(S_COUNT_PAINTS)
    listRoutes[-1].send(json.dumps({
        "sType": "completed", "sCommand": "runAll", "sLogPath": ""}))
    listRoutes[-1].send(json.dumps({"sType": "stepPass", "iStepNumber": 1}))
    pageDashboard.wait_for_timeout(1000)
    assert pageDashboard.evaluate("() => window.__dictPaints") == {
        "iRenders": 0, "iPollStarts": 0, "iToasts": 0}
    assert pageDashboard.evaluate("""() => Array.from(document.body.classList)
        .filter((s) => s.startsWith('proof-level-'))""") == []


@pytest.mark.falsification
def test_a_reopened_project_shows_its_own_binding_answer(
    pageDashboard, serverHub,
):
    """The first open reads an unreadable record, the reopen a valid one.

    Kills: the close leaving the binding banner as it was, so the reopen
    showed the previous answer while its own was on the way.
    """
    readsBinding = _NoticeReads(
        pageDashboard, _RE_BINDING_NOTICE_READ,
        [DICT_INVALID_RECORD, DICT_VALID_RECORD], setHeld={1})
    fnOpenTheSeededHostWorkflow(pageDashboard, serverHub)
    pageDashboard.wait_for_selector(
        "#remoteBindingBanner [data-record-invalid]", timeout=15000)
    _fnGoToTheProjectHub(pageDashboard)
    _fnReopenTheWorkflow(pageDashboard)
    _fnWaitFor(pageDashboard, lambda: readsBinding.listHeld,
               "the reopen's binding notice read")
    assert pageDashboard.locator(
        "#remoteBindingBanner [data-record-invalid]").count() == 0
    readsBinding.fnReleaseHeld(pageDashboard, _RE_BINDING_NOTICE_READ)
    dictBinding = pageDashboard.evaluate(S_BANNERS_STATE)[1]
    assert dictBinding == {
        "sId": "remoteBindingBanner", "sDisplay": "none", "sText": ""}
