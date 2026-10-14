"""The containerizing window shows the hub's record and nothing of its own.

A host project the hub is containerizing carries the conversion's state
on its tile, and the window a researcher watches and decides from is
built from ``GET /api/conversions/<host>`` on every poll. These tests
drive the real page against a hub whose conversion routes are answered
by the test, one state at a time, and assert what a researcher sees:
the tile re-attaches after a reload, Cancel warns with the megabytes it
deletes before anything is sent, a failed download is a window that
stays with Try again and Stay in host mode, an interrupted one offers
Resume, and a staged image offers Finish. No sentence shown names a
chain, a daemon, a Python type or a docker login.
"""

import json

import pytest

from tests.browser.conftest import S_HOST_PROJECT_READY
from tests.browser.testContainerizeFromPinnedImage import (
    S_NEW_NAME,
    _fnWaitForPicker,
    fdictConversionView,
)

pytestmark = pytest.mark.browser

T_BANNED_WORDS = (
    "link of the chain", "pinned reference", "daemon", "docker login",
    "Traceback", "Error:", "<class",
)


def _fnListTheHostTileAsContainerizing(page, sState, iBytes=507000000):
    """Make the seeded host tile carry a pending conversion in ``sState``."""
    def fnListing(route):
        response = route.fetch()
        dictListing = json.loads(response.text())
        for dictProject in dictListing.get("listContainers") or []:
            if dictProject.get("sName") == S_HOST_PROJECT_READY:
                dictProject["dictPendingConversion"] = {
                    "sState": sState, "sNewName": S_NEW_NAME,
                    "bLive": sState == "obtaining",
                    "iBytes": iBytes, "iTotalBytes": 733000000,
                }
        route.fulfill(
            status=200, content_type="application/json",
            body=json.dumps(dictListing),
        )
    page.route("**/api/registry", fnListing)


def _flistAnswerTheConversion(page, dictView):
    """Answer the conversion routes from one view; record the POSTs."""
    listRequests = []

    def fnAnswer(route):
        sMethod = route.request.method
        if sMethod == "POST":
            listRequests.append(route.request.url.rsplit("/", 1)[-1])
            route.fulfill(
                status=200, content_type="application/json",
                body=json.dumps({"bCancelled": True, "bCommitted": False,
                                 "dictConversion": dictView}),
            )
            return
        route.fulfill(
            status=200, content_type="application/json", body=json.dumps(dictView),
        )
    page.route("**/api/conversions/**", fnAnswer)
    return listRequests


def _fsChipSelector():
    return (
        f'.container-tile[data-name="{S_HOST_PROJECT_READY}"] '
        '.containment-chip--containerizing'
    )


def _fnAssertNoBannedWords(sText):
    for sWord in T_BANNED_WORDS:
        assert sWord not in sText, (sWord, sText)


@pytest.mark.falsification
def testTheTileCarriesTheHubsStateAndReopensTheWindowAfterAReload(
    pageDashboard, serverHub,
):
    """Kills: the tile not rendering the pending conversion the hub reports."""
    _fnListTheHostTileAsContainerizing(pageDashboard, "obtaining")
    _flistAnswerTheConversion(pageDashboard, fdictConversionView())
    _fnWaitForPicker(pageDashboard, serverHub)
    pageDashboard.wait_for_selector(_fsChipSelector(), timeout=10000)
    assert "507 of 733 MB" in pageDashboard.text_content(_fsChipSelector())
    pageDashboard.reload(wait_until="networkidle")
    pageDashboard.listPageErrors.clear()
    pageDashboard.wait_for_selector(_fsChipSelector(), timeout=10000)
    pageDashboard.click(_fsChipSelector())
    pageDashboard.wait_for_selector("#modalAcquisition", state="visible", timeout=5000)
    sWindow = pageDashboard.text_content("#modalAcquisition")
    assert "Downloading the author’s environment" in sWindow
    assert "507 MB of 733 MB" in sWindow
    assert "0.4 MB/s" in sWindow
    assert pageDashboard.listPageErrors == []


@pytest.mark.falsification
def testCancelWarnsWithTheMegabytesItDeletesBeforeAnythingIsSent(
    pageDashboard, serverHub,
):
    """Kills: Cancel posting without the warning that names the deleted bytes."""
    _fnListTheHostTileAsContainerizing(pageDashboard, "obtaining")
    listPosts = _flistAnswerTheConversion(pageDashboard, fdictConversionView())
    _fnWaitForPicker(pageDashboard, serverHub)
    pageDashboard.wait_for_selector(_fsChipSelector(), timeout=10000)
    pageDashboard.click(_fsChipSelector())
    pageDashboard.wait_for_selector(
        '#modalAcquisition [data-acquisition-action="cancel"]', timeout=5000,
    )
    pageDashboard.click('#modalAcquisition [data-acquisition-action="cancel"]')
    pageDashboard.wait_for_selector("#modalConfirm", state="visible", timeout=5000)
    sWarning = pageDashboard.text_content("#modalConfirm")
    assert "deletes the 507 MB" in sWarning, sWarning
    assert "Keep downloading" in sWarning
    assert listPosts == []
    pageDashboard.click("#btnConfirmCancel")
    pageDashboard.wait_for_timeout(300)
    assert listPosts == []
    pageDashboard.click('#modalAcquisition [data-acquisition-action="cancel"]')
    pageDashboard.wait_for_selector("#modalConfirm", state="visible", timeout=5000)
    pageDashboard.click("#btnConfirmOk")
    pageDashboard.wait_for_function(
        "() => document.querySelectorAll('#modalConfirm').length === 0 || "
        "!document.querySelector('#modalConfirm').offsetParent", timeout=5000,
    )
    pageDashboard.wait_for_timeout(500)
    assert listPosts == ["cancel"]
    assert pageDashboard.listPageErrors == []


@pytest.mark.falsification
def testAStoppedDownloadIsAWindowThatStaysWithTheDecisions(
    pageDashboard, serverHub,
):
    """Kills: rendering the hub's failure sentence nowhere the researcher can act on."""
    dictFailure = {
        "sFailureClass": "budget-exhausted",
        "sSentence": "The download stopped after 6 attempts in a row that got "
                     "no further than 507 MB of 733 MB.",
        "sHeld": "507 MB of 733 MB downloaded so far are kept; Try again "
                 "continues from there, and giving up deletes them.",
        "bPartialKept": True, "iBytesOnDisk": 507000000,
        "iExpectedBytes": 733000000, "bRetryable": True, "bDiagnosable": False,
        "sAlsoChecked": "Also checked: no public image registry has this "
                        "image (expected for an archived image).",
        "sTechnicalDetail": "ConnectionResetError: [Errno 54]",
    }
    _fnListTheHostTileAsContainerizing(pageDashboard, "waiting")
    listPosts = _flistAnswerTheConversion(
        pageDashboard, fdictConversionView("waiting", False, dictFailure),
    )
    _fnWaitForPicker(pageDashboard, serverHub)
    pageDashboard.wait_for_selector(_fsChipSelector(), timeout=10000)
    assert "your decision" in pageDashboard.text_content(_fsChipSelector())
    pageDashboard.click(_fsChipSelector())
    pageDashboard.wait_for_selector("#modalAcquisition", state="visible", timeout=5000)
    sWindow = pageDashboard.text_content("#modalAcquisition")
    assert dictFailure["sSentence"] in sWindow
    assert "Try again continues from there" in sWindow
    assert "Also checked" in sWindow
    _fnAssertNoBannedWords(
        pageDashboard.evaluate(
            "() => Array.from(document.querySelectorAll('#modalAcquisition p'))"
            ".map(el => el.textContent).join(' ')"
        )
    )
    # The technical text is there, collapsed, never in the sentence.
    assert pageDashboard.query_selector("#modalAcquisition details") is not None
    assert "[Errno 54]" not in pageDashboard.evaluate(
        "() => Array.from(document.querySelectorAll('#modalAcquisition p'))"
        ".map(el => el.textContent).join(' ')"
    )
    # Nothing was shown as a toast, and the window stays.
    pageDashboard.wait_for_timeout(2500)
    assert pageDashboard.is_visible("#modalAcquisition")
    assert pageDashboard.query_selector(".toast-error, .toast.error") is None
    pageDashboard.click('#modalAcquisition [data-acquisition-action="retry"]')
    pageDashboard.wait_for_timeout(500)
    assert listPosts == ["retry"]
    assert pageDashboard.listPageErrors == []


def testAnInterruptedContainerizationOffersResumeAndDiscard(pageDashboard, serverHub):
    _fnListTheHostTileAsContainerizing(pageDashboard, "interrupted")
    listPosts = _flistAnswerTheConversion(
        pageDashboard, fdictConversionView("interrupted", False),
    )
    _fnWaitForPicker(pageDashboard, serverHub)
    pageDashboard.wait_for_selector(_fsChipSelector(), timeout=10000)
    assert "interrupted" in pageDashboard.text_content(_fsChipSelector())
    pageDashboard.click(_fsChipSelector())
    pageDashboard.wait_for_selector("#modalAcquisition", state="visible", timeout=5000)
    sWindow = pageDashboard.text_content("#modalAcquisition")
    assert "interrupted at 507 MB of 733 MB" in sWindow
    assert pageDashboard.query_selector(
        '#modalAcquisition [data-acquisition-action="retry"]').text_content() == "Resume"
    pageDashboard.click('#modalAcquisition [data-acquisition-action="give-up"]')
    pageDashboard.wait_for_selector("#modalConfirm", state="visible", timeout=5000)
    assert "507 MB downloaded so far are deleted" in pageDashboard.text_content("#modalConfirm")
    pageDashboard.click("#btnConfirmOk")
    pageDashboard.wait_for_timeout(500)
    assert listPosts == ["cancel"]
    assert pageDashboard.listPageErrors == []


def testAStagedImageOffersFinishAndShowsWhyItWaits(pageDashboard, serverHub):
    _fnListTheHostTileAsContainerizing(pageDashboard, "ready")
    dictView = fdictConversionView("ready", False)
    dictView["dictDownload"]["iBytes"] = 733000000

    def fnAnswer(route):
        if route.request.method == "POST":
            route.fulfill(
                status=409, content_type="application/json",
                body=json.dumps({"detail": {
                    "sMessage": f"'{S_HOST_PROJECT_READY}' is open in a browser "
                                "session. Close the project to finish containerizing it.",
                    "sRefusal": "busy",
                }}),
            )
            return
        route.fulfill(status=200, content_type="application/json", body=json.dumps(dictView))
    pageDashboard.route("**/api/conversions/**", fnAnswer)
    _fnWaitForPicker(pageDashboard, serverHub)
    pageDashboard.wait_for_selector(_fsChipSelector(), timeout=10000)
    assert "Image ready" in pageDashboard.text_content(_fsChipSelector())
    pageDashboard.click(_fsChipSelector())
    pageDashboard.wait_for_selector(
        '#modalAcquisition [data-acquisition-action="commit"]', timeout=5000,
    )
    pageDashboard.click('#modalAcquisition [data-acquisition-action="commit"]')
    pageDashboard.wait_for_selector('#modalAcquisition [data-commit-refusal]', timeout=5000)
    assert "Close the project to finish" in pageDashboard.text_content("#modalAcquisition")
    assert pageDashboard.listPageErrors == []
