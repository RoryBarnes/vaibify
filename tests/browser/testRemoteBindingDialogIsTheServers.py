"""The binding notices and the review dialog render the server's answer, and retry once.

A token-bearing action refused because nothing is confirmed for its
remote opens a review dialog: what ``project.json`` names, what is
confirmed, and what the host could see with the researcher's own token,
then Confirm posts, unchanged, the review body the server offered, and
the refused action runs again exactly once. A confirmation is tied to
the view it was reviewed in: a switch while the evidence loads, while
the confirm is pending or while a credential prompt is open retries
nothing. Driven through the module's real render path and through
``VaibifyApp.fnRunProjectAction`` over the real page, with the binding
routes and the refused action's route intercepted.

WHAT THIS DOES NOT COVER: what the server decides
(``tests/testRemoteBindings.py``, ``tests/testBindingConfirmation.py``),
and the real Zenodo and Overleaf fetches behind the evidence.
"""

import json
import re

import pytest

from tests.browser.conftest import fnOpenTheSeededHostWorkflow


pytestmark = pytest.mark.browser

S_PROMOTION = "promotionUnderReview"
S_REFUSAL_MESSAGE = (
    "Nothing is confirmed yet for this workflow's project deposits on "
    "zenodo.org (permanent). Review and confirm it from this project's "
    "binding notice in the dashboard."
)
S_ENVIRONMENT_AUTHORIZES = (
    "This lets vaibify deposit this workflow's container images on "
    "zenodo.org (permanent) with your token. Each deposit records its own "
    "DOI in .vaibify/environment.json."
)
S_READ_THE_SERVED_BINDINGS = """async () => VaibifyApi.fdictGet(
    '/api/bindings/' + VaibifyApp.fsGetContainerId() + '?bFetchEvidence=false')"""
_RE_READ = re.compile(r".*/api/bindings/[^/?]+(\?.*)?$")
_RE_CONFIRM = re.compile(r".*/api/bindings/[^/?]+/confirm$")
_RE_RECONCILE = re.compile(rf".*/promotions/{S_PROMOTION}/reconcile$")
_LIST_HUBS_OF_THE_RUNNING_TEST = []


@pytest.fixture(autouse=True)
def fnReleaseEveryHeldRoute(pageDashboard):
    """Abort every request a test held, before its page closes.

    A route still held when the page closes leaves the Playwright
    connection with a pending handler, and the next test on that worker
    then fails at its first navigation with every later test erroring
    behind it (seen in CI, never locally). Depending on ``pageDashboard``
    makes this teardown run before the page's, whether or not the test
    passed.
    """
    yield
    while _LIST_HUBS_OF_THE_RUNNING_TEST:
        _LIST_HUBS_OF_THE_RUNNING_TEST.pop().fnReleaseHeldRoutes()


def _fdictEntry(sService, sRecordKind, sInstance, **dictFields):
    dictTarget = dictFields.pop("dictTarget", {"sInstance": sInstance, "sConceptDoi": "", "sDepositionId": ""})
    dictEntry = {
        "sService": sService, "sRecordKind": sRecordKind, "sInstance": sInstance,
        "sLabel": "this workflow's project deposits on zenodo.org (permanent)",
        "bApplies": False, "dictProjectTarget": None, "dictConfirmed": None,
        "sState": "unconfirmed", "sNotice": "",
        "dictReview": {
            "sService": sService, "sRecordKind": sRecordKind,
            "sWorkflowKey": "projects/study.json::study", "dictTarget": dictTarget,
        },
    }
    dictEntry.update(dictFields)
    return dictEntry


def _fdictDescribed(listEntries, **dictFields):
    dictDescribed = {
        "sProjectUuid": "f" * 32, "sWorkflowKey": "projects/study.json::study",
        "sWorkflowName": "study", "bEnvironmentArchiveInUse": False,
        "dictRecordState": {"bValid": True}, "listEntries": listEntries,
    }
    dictDescribed.update(dictFields)
    return dictDescribed


_DICT_ACCEPTED = {"bSucceeded": True, "sState": "token-accepted", "listDepositTitles": []}
_DICT_MINT_ENTRY = _fdictEntry("zenodo", "project", "zenodo", dictEvidence=_DICT_ACCEPTED)


class _Hub:
    """The binding routes and the refused action, answered from the test."""

    def __init__(self, pageDashboard, listReadAnswers, bHoldReads=False, bHoldConfirms=False):
        self.listReadAnswers = list(listReadAnswers)
        self.listReads, self.listConfirms, self.listReconciles = [], [], []
        self.listHeldReads, self.listHeldConfirms = [], []
        self.bHoldReads, self.bHoldConfirms = bHoldReads, bHoldConfirms
        pageDashboard.route(_RE_READ, self._fnRead)
        pageDashboard.route(_RE_CONFIRM, self._fnConfirm)
        pageDashboard.route(_RE_RECONCILE, self._fnReconcile)
        _LIST_HUBS_OF_THE_RUNNING_TEST.append(self)

    def fnReleaseHeldRoutes(self):
        from playwright.sync_api import Error as PlaywrightError
        for routeHeld in self.listHeldReads + self.listHeldConfirms:
            try:
                routeHeld.abort()
            except PlaywrightError:
                pass  # already answered, or the request is already gone
        self.listHeldReads.clear()
        self.listHeldConfirms.clear()

    def _fnRead(self, routeIntercepted):
        # Only the dialog's reads, which name one entry; the page's own
        # notice refresh on project open reaches the hub as it would.
        if routeIntercepted.request.method != "GET" or (
            "sService=" not in routeIntercepted.request.url
        ):
            routeIntercepted.fallback()
            return
        self.listReads.append(routeIntercepted.request.url)
        if self.bHoldReads:
            self.listHeldReads.append(routeIntercepted)
            return
        self.fnAnswerRead(routeIntercepted)

    def fnAnswerRead(self, routeIntercepted):
        dictAnswer = self.listReadAnswers.pop(0) if len(self.listReadAnswers) > 1 else self.listReadAnswers[0]
        routeIntercepted.fulfill(status=200, content_type="application/json", body=json.dumps(dictAnswer))

    def _fnConfirm(self, routeIntercepted):
        self.listConfirms.append(json.loads(routeIntercepted.request.post_data))
        if self.bHoldConfirms:
            self.listHeldConfirms.append(routeIntercepted)
            return
        fnAnswerJson(routeIntercepted, 200, {"bConfirmed": True})

    def _fnReconcile(self, routeIntercepted):
        self.listReconciles.append(routeIntercepted.request.url)
        if len(self.listReconciles) == 1:
            fnAnswerJson(routeIntercepted, 409, {"detail": {
                "sError": "BINDING-REQUIRED", "sService": "zenodo",
                "sRecordKind": "project", "sInstance": "zenodo",
                "sMessage": S_REFUSAL_MESSAGE,
            }})
            return
        fnAnswerJson(routeIntercepted, 200, {"sMessage": "Zenodo answered", "listActions": []})


def fnAnswerJson(routeIntercepted, iStatus, dictBody):
    routeIntercepted.fulfill(status=iStatus, content_type="application/json", body=json.dumps(dictBody))


def _fnWaitFor(pageDashboard, fbDone, sWhat):
    for _ in range(100):
        if fbDone():
            return
        pageDashboard.wait_for_timeout(100)
    raise AssertionError(f"timed out waiting for {sWhat}")


def _fnRunTheRefusedAction(pageDashboard):
    pageDashboard.evaluate(
        "(sPromotion) => { VaibifyApp.fnRunProjectAction("
        "'reconcile-promotion', sPromotion, null); }", S_PROMOTION)


def _fnOpenTheDialogFor(pageDashboard, listAddress):
    pageDashboard.evaluate(
        "(listAddress) => { VaibifyRemoteBindings.fpromiseReviewAndConfirm("
        "listAddress[0], listAddress[1], listAddress[2], "
        "VaibifyRemoteBindings.fdictCaptureContext()); }", listAddress)


def _fsDialogText(pageDashboard):
    return pageDashboard.locator("#modalRemoteBinding").inner_text()


def _fbDialogShown(pageDashboard):
    return pageDashboard.evaluate(
        "() => document.getElementById('modalRemoteBinding').style.display === 'flex'")


def _fnAdvanceTheView(pageDashboard):
    pageDashboard.evaluate("() => VaibifyApp.fnAdvanceViewGeneration()")


def _fnWaitForConfirmEnabled(pageDashboard):
    pageDashboard.wait_for_selector("#btnRemoteBindingConfirm:not([disabled])", timeout=10000)


# ---------------------------------------------------------------------------
# The notices
# ---------------------------------------------------------------------------


def testTheNoticesRenderTheServersSentencesAndAnInvalidRecord(pageDashboard, serverHub):
    fnOpenTheSeededHostWorkflow(pageDashboard, serverHub)
    pageDashboard.evaluate("(dictDescribed) => VaibifyRemoteBindings.fnRenderNotices(dictDescribed)",
        _fdictDescribed([
            _fdictEntry("zenodo", "project", "zenodo", bApplies=True,
                        sNotice="Not confirmed: this workflow's project deposits on zenodo.org (permanent)."),
            _fdictEntry("zenodo", "environment", "zenodo", bApplies=False,
                        sNotice="Not confirmed: this workflow's environment archive on zenodo.org (permanent)."),
            _fdictEntry("overleaf", "", "", bApplies=True, sState="agrees", sNotice=""),
        ]))
    elBanner = pageDashboard.locator("#remoteBindingBanner")
    assert elBanner.is_visible()
    assert pageDashboard.locator("#remoteBindingBanner .remote-binding-row").count() == 1
    assert "project deposits on zenodo.org (permanent)" in elBanner.inner_text()
    assert "environment archive" not in elBanner.inner_text()
    pageDashboard.evaluate("(dictDescribed) => VaibifyRemoteBindings.fnRenderNotices(dictDescribed)",
        _fdictDescribed([], dictRecordState={
            "bValid": False, "sError": "BINDING-RECORD-INVALID",
            "sReason": "its iSchemaVersion is None, not 2",
            "sMessage": "The binding record ~/.vaibify/bindings/x.json can't be read "
                        "(its iSchemaVersion is None, not 2). Move it aside and confirm "
                        "this workflow's remotes again.",
        }))
    assert "can't be read" in elBanner.inner_text()
    assert pageDashboard.locator("#remoteBindingBanner [data-record-invalid]").count() == 1
    pageDashboard.evaluate("(dictDescribed) => VaibifyRemoteBindings.fnRenderNotices(dictDescribed)",
        _fdictDescribed([]))
    assert not elBanner.is_visible()


# ---------------------------------------------------------------------------
# A refusal, the review, and one retry
# ---------------------------------------------------------------------------


def testARefusalLeadsToTheDialogThenConfirmThenExactlyOneRetry(pageDashboard, serverHub):
    fnOpenTheSeededHostWorkflow(pageDashboard, serverHub)
    hubDouble = _Hub(pageDashboard, [_fdictDescribed([_DICT_MINT_ENTRY])])
    _fnRunTheRefusedAction(pageDashboard)
    _fnWaitForConfirmEnabled(pageDashboard)
    assert "Allow a NEW permanent record on zenodo.org for this workflow's project deposit." in (
        _fsDialogText(pageDashboard))
    assert "sService=zenodo" in hubDouble.listReads[-1]
    pageDashboard.click("#btnRemoteBindingConfirm")
    _fnWaitFor(pageDashboard, lambda: len(hubDouble.listReconciles) == 2, "the retry")
    pageDashboard.wait_for_timeout(500)
    assert hubDouble.listConfirms == [_DICT_MINT_ENTRY["dictReview"]]
    assert len(hubDouble.listReconciles) == 2
    assert not _fbDialogShown(pageDashboard)


def testCancelPostsNothingRetriesNothingAndReportsTheRefusalOnce(pageDashboard, serverHub):
    fnOpenTheSeededHostWorkflow(pageDashboard, serverHub)
    hubDouble = _Hub(pageDashboard, [_fdictDescribed([_DICT_MINT_ENTRY])])
    _fnRunTheRefusedAction(pageDashboard)
    _fnWaitForConfirmEnabled(pageDashboard)
    pageDashboard.click("#btnRemoteBindingCancel")
    pageDashboard.wait_for_selector(".toast.warning", timeout=10000)
    pageDashboard.wait_for_timeout(500)
    assert hubDouble.listConfirms == []
    assert len(hubDouble.listReconciles) == 1
    listToasts = pageDashboard.locator(".toast").all_inner_texts()
    assert sum(S_REFUSAL_MESSAGE in sToast for sToast in listToasts) == 1


def testADoubleClickOnConfirmPostsOnce(pageDashboard, serverHub):
    fnOpenTheSeededHostWorkflow(pageDashboard, serverHub)
    hubDouble = _Hub(pageDashboard, [_fdictDescribed([_DICT_MINT_ENTRY])], bHoldConfirms=True)
    _fnOpenTheDialogFor(pageDashboard, ["zenodo", "project", "zenodo"])
    _fnWaitForConfirmEnabled(pageDashboard)
    pageDashboard.dblclick("#btnRemoteBindingConfirm")
    pageDashboard.wait_for_timeout(800)
    assert len(hubDouble.listConfirms) == 1


# ---------------------------------------------------------------------------
# A changed view retries nothing
# ---------------------------------------------------------------------------


def testASwitchWhileTheEvidenceLoadsRendersNothingIntoTheNewView(pageDashboard, serverHub):
    fnOpenTheSeededHostWorkflow(pageDashboard, serverHub)
    hubDouble = _Hub(pageDashboard, [_fdictDescribed([_DICT_MINT_ENTRY])], bHoldReads=True)
    _fnRunTheRefusedAction(pageDashboard)
    _fnWaitFor(pageDashboard, lambda: hubDouble.listHeldReads, "the evidence request")
    _fnAdvanceTheView(pageDashboard)
    hubDouble.fnAnswerRead(hubDouble.listHeldReads.pop())
    pageDashboard.wait_for_selector(".toast.warning", timeout=10000)
    assert not _fbDialogShown(pageDashboard)
    assert "Allow a NEW" not in _fsDialogText(pageDashboard)
    assert "project view changed" in pageDashboard.locator(".toast.warning").last.inner_text()
    assert len(hubDouble.listReconciles) == 1


def testASwitchWhileTheConfirmIsPendingRetriesNothing(pageDashboard, serverHub):
    fnOpenTheSeededHostWorkflow(pageDashboard, serverHub)
    hubDouble = _Hub(pageDashboard, [_fdictDescribed([_DICT_MINT_ENTRY])], bHoldConfirms=True)
    _fnRunTheRefusedAction(pageDashboard)
    _fnWaitForConfirmEnabled(pageDashboard)
    pageDashboard.click("#btnRemoteBindingConfirm")
    _fnWaitFor(pageDashboard, lambda: hubDouble.listHeldConfirms, "the confirm request")
    _fnAdvanceTheView(pageDashboard)
    fnAnswerJson(hubDouble.listHeldConfirms.pop(), 200, {"bConfirmed": True})
    pageDashboard.wait_for_selector(".toast.warning", timeout=10000)
    pageDashboard.wait_for_timeout(500)
    assert len(hubDouble.listReconciles) == 1


def testASwitchWhileTheCredentialPromptIsOpenFetchesAndRetriesNothing(pageDashboard, serverHub):
    fnOpenTheSeededHostWorkflow(pageDashboard, serverHub)
    dictTokenMissing = dict(_DICT_MINT_ENTRY, dictEvidence={
        "bSucceeded": False, "sState": "token-missing",
        "sMessage": "No token for zenodo.org (permanent) is stored on this computer for this project.",
    })
    hubDouble = _Hub(pageDashboard, [_fdictDescribed([dictTokenMissing])])
    _fnRunTheRefusedAction(pageDashboard)
    pageDashboard.wait_for_selector("#btnRemoteBindingStoreToken", timeout=10000)
    iReadsBefore = len(hubDouble.listReads)
    pageDashboard.click("#btnRemoteBindingStoreToken")
    pageDashboard.wait_for_selector("#modalConnectionSetup", state="visible", timeout=10000)
    _fnAdvanceTheView(pageDashboard)
    pageDashboard.click("#btnSetupCancel")
    pageDashboard.wait_for_selector(".toast.warning", timeout=10000)
    pageDashboard.wait_for_timeout(500)
    assert len(hubDouble.listReads) == iReadsBefore
    assert len(hubDouble.listReconciles) == 1
    assert not _fbDialogShown(pageDashboard)


def testAConfirmTheServerFindsStaleIsReportedAndRetriesNothing(pageDashboard, serverHub):
    fnOpenTheSeededHostWorkflow(pageDashboard, serverHub)
    hubDouble = _Hub(pageDashboard, [_fdictDescribed([_DICT_MINT_ENTRY])], bHoldConfirms=True)
    _fnRunTheRefusedAction(pageDashboard)
    _fnWaitForConfirmEnabled(pageDashboard)
    pageDashboard.click("#btnRemoteBindingConfirm")
    _fnWaitFor(pageDashboard, lambda: hubDouble.listHeldConfirms, "the confirm request")
    fnAnswerJson(hubDouble.listHeldConfirms.pop(), 409, {"detail": {
        "sError": "BINDING-REVIEW-STALE", "sService": "zenodo", "sRecordKind": "project",
        "sInstance": "zenodo",
        "sMessage": "The workflow you reviewed is no longer the one loaded here, so nothing was confirmed.",
    }})
    pageDashboard.wait_for_selector("#modalRemoteBinding .remote-binding-refusal", timeout=10000)
    assert "no longer the one loaded" in _fsDialogText(pageDashboard)
    assert len(hubDouble.listReconciles) == 1


# ---------------------------------------------------------------------------
# The evidence, in the dashboard's words
# ---------------------------------------------------------------------------


def testAMissingTokenAsksForItThenFetchesTheEvidenceAgain(pageDashboard, serverHub):
    fnOpenTheSeededHostWorkflow(pageDashboard, serverHub)
    dictTokenMissing = dict(_DICT_MINT_ENTRY, dictEvidence={
        "bSucceeded": False, "sState": "token-missing",
        "sMessage": "No token for zenodo.org (permanent) is stored on this computer for this project.",
    })
    hubDouble = _Hub(pageDashboard, [
        _fdictDescribed([dictTokenMissing]), _fdictDescribed([_DICT_MINT_ENTRY]),
    ])
    _fnOpenTheDialogFor(pageDashboard, ["zenodo", "project", "zenodo"])
    pageDashboard.wait_for_selector("#btnRemoteBindingStoreToken", timeout=10000)
    assert pageDashboard.locator("#btnRemoteBindingConfirm").is_disabled()
    pageDashboard.click("#btnRemoteBindingStoreToken")
    pageDashboard.wait_for_selector("#modalConnectionSetup", state="visible", timeout=10000)
    pageDashboard.wait_for_function(
        "() => document.querySelector("
        "'input[name=zenodoInstance][value=production]').checked", timeout=10000)
    pageDashboard.click("#btnSetupCancel")
    _fnWaitForConfirmEnabled(pageDashboard)
    assert len(hubDouble.listReads) == 2
    assert pageDashboard.locator("#btnRemoteBindingStoreToken").count() == 0
    pageDashboard.click("#btnRemoteBindingConfirm")
    _fnWaitFor(pageDashboard, lambda: hubDouble.listConfirms, "the confirm")


def testUnavailableEvidenceIsNotRedAndConfirmStaysDisabled(pageDashboard, serverHub):
    fnOpenTheSeededHostWorkflow(pageDashboard, serverHub)
    _Hub(pageDashboard, [_fdictDescribed([dict(_DICT_MINT_ENTRY, dictEvidence={
        "bSucceeded": False, "sState": "unavailable",
        "sMessage": "ConnectionError: zenodo.org did not answer",
    })])])
    _fnOpenTheDialogFor(pageDashboard, ["zenodo", "project", "zenodo"])
    pageDashboard.wait_for_selector("#btnRemoteBindingRetry", timeout=10000)
    assert pageDashboard.locator("#btnRemoteBindingConfirm").is_disabled()
    elEvidence = pageDashboard.locator("#modalRemoteBinding .remote-binding-evidence--unchecked")
    assert "Couldn't check" in elEvidence.inner_text()
    sColor = elEvidence.evaluate("(el) => getComputedStyle(el).color")
    iRed, iGreen, iBlue = [int(s) for s in re.findall(r"\d+", sColor)[:3]]
    assert not (iRed > 180 and iGreen < 140 and iBlue < 140), sColor


@pytest.mark.falsification
def testAnEnvironmentArchiveDialogSaysWhatItAuthorizesAndComparesNoRecord(
    pageDashboard, serverHub,
):
    """The environment kind names no record, so the dialog compares none.

    The entry is the real hub's, with only the evidence added, so the
    sentence on screen is the one the server writes.

    Kills: rendering the record comparison for a kind with no target,
    which read "no record yet" for a binding that never names a record.
    """
    fnOpenTheSeededHostWorkflow(pageDashboard, serverHub)
    dictServed = pageDashboard.evaluate(S_READ_THE_SERVED_BINDINGS)
    dictEnvironment = next(
        dictEntry for dictEntry in dictServed["listEntries"]
        if (dictEntry["sRecordKind"], dictEntry["sInstance"]) == (
            "environment", "zenodo"))
    dictEnvironment["dictEvidence"] = _DICT_ACCEPTED
    _Hub(pageDashboard, [dict(dictServed, listEntries=[dictEnvironment])])
    _fnOpenTheDialogFor(pageDashboard, ["zenodo", "environment", "zenodo"])
    _fnWaitForConfirmEnabled(pageDashboard)
    sText = _fsDialogText(pageDashboard)
    assert S_ENVIRONMENT_AUTHORIZES in sText
    assert "Zenodo accepts your token for" in sText
    assert "no record yet" not in sText
    assert pageDashboard.locator(
        "#modalRemoteBinding .remote-binding-comparison").count() == 0


def testEachKindNamesWhatItAuthorizesInTheDashboardsWords(pageDashboard, serverHub):
    fnOpenTheSeededHostWorkflow(pageDashboard, serverHub)
    dictEnvironment = _fdictEntry(
        "zenodo", "environment", "zenodo", dictEvidence=_DICT_ACCEPTED,
        dictTarget={"sInstance": "zenodo"}, bHasTarget=False,
        sAuthorizes=S_ENVIRONMENT_AUTHORIZES,
    )
    dictRecord = _fdictEntry(
        "zenodo", "project", "sandbox", sState="differs",
        dictProjectTarget={"sInstance": "sandbox", "sConceptDoi": "10.5072/zenodo.9", "sDepositionId": ""},
        dictConfirmed={"dictTarget": {"sInstance": "sandbox", "sConceptDoi": "10.5072/zenodo.1", "sDepositionId": ""}},
        dictTarget={"sInstance": "sandbox", "sConceptDoi": "10.5072/zenodo.9", "sDepositionId": ""},
        dictEvidence={"bSucceeded": True, "sState": "published-record", "sTitle": "A study", "sOwner": "7"},
    )
    _Hub(pageDashboard, [_fdictDescribed([dictEnvironment, dictRecord])])
    _fnOpenTheDialogFor(pageDashboard, ["zenodo", "environment", "zenodo"])
    _fnWaitForConfirmEnabled(pageDashboard)
    sEnvironment = _fsDialogText(pageDashboard)
    assert S_ENVIRONMENT_AUTHORIZES in sEnvironment
    _fnOpenTheDialogFor(pageDashboard, ["zenodo", "project", "sandbox"])
    pageDashboard.wait_for_selector("#modalRemoteBinding .remote-binding-warning", timeout=10000)
    sRecord = _fsDialogText(pageDashboard)
    assert "project deposits on the Zenodo sandbox" in sRecord
    assert "“A study” (10.5072/zenodo.9)" in sRecord
    assert "an agent edited project.json" in sRecord
    assert "it does not prove the record is yours" in sRecord
    assert "production" not in sEnvironment + sRecord
