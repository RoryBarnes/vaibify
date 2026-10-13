"""A verification that ends before any poll saw it running is reported.

The dashboard reported a settled Level 3 verification only when a
file-status poll had first seen it RUNNING. An attempt refused early --
an unreachable registry, a missing tool -- settles in about a second,
before the next poll, so it ended in silence: no toast, no reasons, no
manifests (found by external review). The server now mints an attempt
id at launch and returns it with the acceptance; the tab that clicked
awaits that id in ``dictLastSettled``.

The id is what keeps this honest in the other direction: a poll sent
before the click can answer after it, while the PREVIOUS attempt's
record is still the current one, and reporting that record would
present an earlier result as this run's.

Driven through the real file-status poll and the real Verify control;
only the hub's answers are rewritten.
"""

import pytest

from tests.browser.conftest import fnOpenTheSeededHostWorkflow
import json

from tests.browser.testAVerificationIsFollowedOnTheProjectBlock import (
    fixtureDropClaimsBetweenTests, _fdictInstallTheFakeHub, _fdictPayload,
    _fnAnswerReadinessReady, _flistToastTexts, _fnWaitUntil,
)


pytestmark = pytest.mark.browser

S_AWAITED_ATTEMPT = "attempt-launched-by-this-tab"
S_EARLIER_ATTEMPT = "attempt-that-ended-before-the-click"


def _fdictSettled(sAttemptId, sReason):
    return {
        "sPhase": "no-verdict", "listReasons": [sReason],
        "sRecordKind": "", "sReproducedManifestPath": "",
        "sAttemptId": sAttemptId,
    }


def _fnStartAVerificationThatNeverRuns(pageDashboard, serverHub,
                                       dictPayload):
    """Click Verify; the hub accepts, and no poll ever shows it running."""
    dictState = _fdictInstallTheFakeHub(
        pageDashboard, dictPayload, bRunning=False)
    _fnAnswerReadinessReady(pageDashboard)
    pageDashboard.route(
        "**/api/workflow/**/level3/verify",
        lambda route: route.fulfill(
            status=200, content_type="application/json",
            body='{"bAccepted": true, "sPhase": "starting", '
                 '"sAttemptId": "' + S_AWAITED_ATTEMPT + '"}'))
    fnOpenTheSeededHostWorkflow(
        pageDashboard, serverHub, bAwaitProjectBlock=True)
    pageDashboard.click("#toolbarMenuRun .toolbar-menu-trigger")
    pageDashboard.click("#btnVerifyLevel3")
    pageDashboard.wait_for_selector("#modalConfirm", timeout=10000)
    pageDashboard.click("#btnConfirmOk")
    return dictState


def _flistToastsNaming(pageDashboard, sText):
    return [sToast for sToast in _flistToastTexts(pageDashboard)
            if sText in sToast]


@pytest.mark.falsification
def test_a_verification_that_settles_before_any_poll_is_still_reported(
    pageDashboard, serverHub,
):
    """Kills: reporting only a run some poll saw running."""
    sReason = "the registry could not be reached"
    _fnStartAVerificationThatNeverRuns(
        pageDashboard, serverHub,
        _fdictPayload(_fdictSettled(S_AWAITED_ATTEMPT, sReason)))
    _fnWaitUntil(pageDashboard,
                 lambda: _flistToastsNaming(pageDashboard, sReason),
                 iTimeoutSeconds=15)
    listToasts = _flistToastsNaming(pageDashboard, sReason)
    assert listToasts, (
        "the verification settled before any poll saw it running, and "
        f"its failure was never told: {_flistToastTexts(pageDashboard)!r}")
    assert "failed without reaching a verdict" in listToasts[0]
    assert pageDashboard.listPageErrors == []


@pytest.mark.falsification
def test_an_earlier_attempts_record_is_never_reported_as_this_one(
    pageDashboard, serverHub,
):
    """Kills: accepting whatever record is current, whatever its id.

    The earlier record stays current for several polls first, which is
    the window a stale poll opens; then this attempt settles, and is
    told exactly once however many polls follow.
    """
    sEarlierReason = "a reason that belongs to the earlier attempt"
    sAwaitedReason = "a reason that belongs to this attempt"
    dictPayload = _fdictPayload(
        _fdictSettled(S_EARLIER_ATTEMPT, sEarlierReason))
    dictState = _fnStartAVerificationThatNeverRuns(
        pageDashboard, serverHub, dictPayload)
    iPollsAtClick = dictState["iPolls"]
    _fnWaitUntil(pageDashboard,
                 lambda: dictState["iPolls"] >= iPollsAtClick + 2,
                 iTimeoutSeconds=20)
    assert dictState["iPolls"] >= iPollsAtClick + 2, (
        "the poll never ran after the click, so nothing was exercised")
    assert not _flistToastsNaming(pageDashboard, sEarlierReason), (
        "an earlier attempt's record was reported as this run's result")

    dictPayload["dictLastSettled"] = _fdictSettled(
        S_AWAITED_ATTEMPT, sAwaitedReason)
    _fnWaitUntil(pageDashboard,
                 lambda: _flistToastsNaming(pageDashboard, sAwaitedReason),
                 iTimeoutSeconds=15)
    iPollsAtReport = dictState["iPolls"]
    _fnWaitUntil(pageDashboard,
                 lambda: dictState["iPolls"] >= iPollsAtReport + 2,
                 iTimeoutSeconds=20)
    assert len(_flistToastsNaming(pageDashboard, sAwaitedReason)) == 1, (
        "this attempt's result was told "
        f"{len(_flistToastsNaming(pageDashboard, sAwaitedReason))} times")
    assert pageDashboard.listPageErrors == []


@pytest.mark.falsification
def test_an_older_result_read_never_repaints_over_a_newer_result(
    pageDashboard, serverHub,
):
    """Kills: painting a result read that answers after a newer one painted.

    One verification is watched to its end and its result read is held;
    a second is started and settles at once, and its result paints.
    Released, the first read must not put the older attempt's manifest
    back over the newer attempt's outcome.
    """
    sNewReason = "the newer attempt was refused"
    dictOlder = _fdictPayload({
        "sAttemptId": S_EARLIER_ATTEMPT, "sPhase": "passed",
        "sReproducedManifestPath": "older.sha256", "sRecordKind": "author",
    })
    dictNewer = _fdictPayload(_fdictSettled(S_AWAITED_ATTEMPT, sNewReason))
    dictState = _fdictInstallTheFakeHub(pageDashboard, dictOlder,
                                        bRunning=True)
    _fnAnswerReadinessReady(pageDashboard)
    fnOpenTheSeededHostWorkflow(
        pageDashboard, serverHub, bAwaitProjectBlock=True)
    listHeld = []

    def fnAnswerTheResultRead(route):
        if not listHeld:
            listHeld.append(route)
            return
        route.fulfill(status=200, content_type="application/json",
                      body=json.dumps(dictNewer))

    pageDashboard.route("**/api/workflow/**/level3/attestation",
                        fnAnswerTheResultRead)
    pageDashboard.route(
        "**/api/figure/**/older.sha256",
        lambda route: route.fulfill(status=200, content_type="text/plain",
                                    body="THE OLDER ATTEMPT'S MANIFEST"))
    dictState["bRunning"] = False
    _fnWaitUntil(pageDashboard, lambda: bool(listHeld), iTimeoutSeconds=15)
    assert listHeld, "the watched run's end never read its result"
    pageDashboard.route(
        "**/api/workflow/**/level3/verify",
        lambda route: route.fulfill(
            status=200, content_type="application/json",
            body=json.dumps({"bAccepted": True, "sPhase": "starting",
                             "sAttemptId": S_AWAITED_ATTEMPT})))
    pageDashboard.click("#toolbarMenuRun .toolbar-menu-trigger")
    pageDashboard.click("#btnVerifyLevel3")
    pageDashboard.wait_for_selector("#modalConfirm", timeout=10000)
    pageDashboard.click("#btnConfirmOk")
    pageDashboard.wait_for_function(
        "(sText) => document.getElementById('viewportB')"
        ".textContent.includes(sText)", arg="no reproduced manifest",
        timeout=15000)
    listHeld[0].fulfill(status=200, content_type="application/json",
                        body=json.dumps(dictOlder))
    pageDashboard.wait_for_timeout(900)
    sShown = pageDashboard.locator("#viewportB").inner_text()
    assert "OLDER ATTEMPT" not in sShown, (
        f"an older result read repainted the viewer: {sShown!r}")
    assert pageDashboard.listPageErrors == []
