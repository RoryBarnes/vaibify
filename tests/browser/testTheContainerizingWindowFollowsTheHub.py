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


def _fnListTheHostTileAsContainerizing(page, sState):
    """Make the seeded host tile carry a pending conversion in ``sState``."""
    def fnListing(route):
        response = route.fetch()
        dictListing = json.loads(response.text())
        for dictProject in dictListing.get("listContainers") or []:
            if dictProject.get("sName") == S_HOST_PROJECT_READY:
                dictProject["dictPendingConversion"] = {
                    "sState": sState, "sNewName": S_NEW_NAME,
                    "bLive": sState == "obtaining",
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
    # The chip names the state; the bytes live in the window it opens.
    assert pageDashboard.text_content(_fsChipSelector()).strip() == "Containerizing…"
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


@pytest.mark.falsification
def testCheckConnectionShowsTheHubsObservationsInTheWindow(pageDashboard, serverHub):
    """Kills: the check's sentences shown nowhere, or the button missing where the rate is low."""
    dictFailure = {
        "sFailureClass": "budget-exhausted",
        "sSentence": "The download stopped after 6 attempts in a row.",
        "sHeld": "", "bPartialKept": True, "iBytesOnDisk": 507000000,
        "iExpectedBytes": 733000000, "bRetryable": True, "bDiagnosable": False,
        "sAlsoChecked": "", "sTechnicalDetail": "", "sAcquisitionId": "acquisition-abc",
    }
    _fnListTheHostTileAsContainerizing(pageDashboard, "waiting")
    _flistAnswerTheConversion(
        pageDashboard, fdictConversionView("waiting", False, dictFailure),
    )
    listChecks = []

    def fnAnswerTheCheck(route):
        listChecks.append(route.request.url.rsplit("/", 1)[-1])
        route.fulfill(
            status=200, content_type="application/json",
            body=json.dumps({
                "sServiceHost": "zenodo.org",
                "listObservations": [
                    "Reached zenodo.org.",
                    "It took 22 s to start sending and then sent at 0.4 MB/s.",
                    "Reached doi.org.",
                ],
                "listExplanations": ["Slow starts like this often mean Zenodo is busy."],
                "dictMeasured": {},
            }),
        )
    pageDashboard.route("**/api/connection-check/**", fnAnswerTheCheck)
    _fnWaitForPicker(pageDashboard, serverHub)
    pageDashboard.wait_for_selector(_fsChipSelector(), timeout=10000)
    pageDashboard.click(_fsChipSelector())
    pageDashboard.wait_for_selector(
        '#modalAcquisition [data-acquisition-action="check-connection"]', timeout=5000,
    )
    pageDashboard.click('#modalAcquisition [data-acquisition-action="check-connection"]')
    pageDashboard.wait_for_selector(
        '#modalAcquisition .acquisition-check[data-connection-checked]', timeout=5000,
    )
    sReport = pageDashboard.text_content("#modalAcquisition .acquisition-check")
    assert "Reached zenodo.org." in sReport
    assert "Zenodo is busy" in sReport
    assert listChecks == ["acquisition-abc"]
    assert pageDashboard.is_visible("#modalAcquisition")
    assert pageDashboard.listPageErrors == []


def testASlowLiveDownloadOffersTheCheckAndAHealthyOneDoesNot(pageDashboard, serverHub):
    dictView = fdictConversionView()
    dictView["dictDownload"]["bSlow"] = False
    _fnListTheHostTileAsContainerizing(pageDashboard, "obtaining")
    _flistAnswerTheConversion(pageDashboard, dictView)
    _fnWaitForPicker(pageDashboard, serverHub)
    pageDashboard.wait_for_selector(_fsChipSelector(), timeout=10000)
    pageDashboard.click(_fsChipSelector())
    pageDashboard.wait_for_selector(
        '#modalAcquisition [data-acquisition-action="cancel"]', timeout=5000,
    )
    assert pageDashboard.query_selector(
        '#modalAcquisition [data-acquisition-action="check-connection"]') is None
    dictView["dictDownload"]["bSlow"] = True
    pageDashboard.wait_for_selector(
        '#modalAcquisition [data-acquisition-action="check-connection"]', timeout=5000,
    )
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


def _fdictLocatingView():
    """The hub's view while it asks Zenodo where the deposit is, between tries."""
    return fdictConversionView(dictDownloadOverrides={
        "sStage": "locating", "iBytes": 0, "iTotalBytes": 0,
        "fBytesPerSecond": 0.0, "fSecondsRemaining": None,
        "fSecondsSinceLastByte": None, "iAttempt": 2,
        "fSecondsInStage": 95.0, "fSecondsUntilRetry": 37.0,
        "fSecondsUntilGiveUp": 1585.0,
        "sLastFailure": "zenodo.org did not accept a connection within 120 s",
        "sAcquisitionId": "acquisition-0123abcd",
    })


def _fsOpenTheWindow(pageDashboard, serverHub, dictView):
    """Open the containerizing window over ``dictView``; return its text."""
    _fnListTheHostTileAsContainerizing(pageDashboard, "obtaining")
    _flistAnswerTheConversion(pageDashboard, dictView)
    _fnWaitForPicker(pageDashboard, serverHub)
    pageDashboard.wait_for_selector(_fsChipSelector(), timeout=10000)
    pageDashboard.click(_fsChipSelector())
    pageDashboard.wait_for_selector(
        '#modalAcquisition [data-acquisition-action="cancel"]', timeout=5000,
    )
    return pageDashboard.text_content("#modalAcquisition")


@pytest.mark.falsification
def testTheWindowNamesTheLookupAndCountsItsTimers(pageDashboard, serverHub):
    """Kills: a window that infers its stage from the byte counts.

    Before the first byte the counts are zero, which the window once
    read as "the download finished" -- over a lookup that had not yet
    reached Zenodo. The hub's stamped stage is the authority, and the
    three timers it computes (how long, until the next try, until it
    gives up) are shown as the hub computed them.
    """
    sWindow = _fsOpenTheWindow(pageDashboard, serverHub, _fdictLocatingView())
    assert "Finding the author’s environment on Zenodo" in sWindow
    assert "finished" not in sWindow, sWindow
    assert "Trying for 1 min 35 s (try 2)." in sWindow
    assert "Zenodo.org did not accept a connection within 120 s. Next try in 37 s." in sWindow
    assert "stops trying in 26 min 25 s" in sWindow
    assert pageDashboard.get_attribute(
        "#modalAcquisition", "data-acquisition-stage") == "locating"
    assert pageDashboard.query_selector(
        '#modalAcquisition [data-acquisition-action="check-connection"]') is not None
    _fnAssertNoBannedWords(sWindow)
    assert pageDashboard.listPageErrors == []


@pytest.mark.parametrize("sStage,sTitle", [
    ("", "Starting to containerize"),
    ("registry", "Checking public image registries"),
    ("local", "Looking for a copy on this machine"),
])
def testNoStageBeforeTheDownloadSaysTheDownloadFinished(
    pageDashboard, serverHub, sStage, sTitle,
):
    dictView = fdictConversionView(dictDownloadOverrides={
        "sStage": sStage, "iBytes": 0, "iTotalBytes": 0, "fSecondsInStage": 4.0,
    })
    sWindow = _fsOpenTheWindow(pageDashboard, serverHub, dictView)
    assert sTitle in sWindow
    assert "finished" not in sWindow, sWindow
    assert pageDashboard.listPageErrors == []


def testTheLoadingStageSaysTheHashMatched(pageDashboard, serverHub):
    dictView = fdictConversionView(dictDownloadOverrides={
        "sStage": "loading", "iBytes": 733000000, "fSecondsInStage": 41.0,
    })
    sWindow = _fsOpenTheWindow(pageDashboard, serverHub, dictView)
    assert "Loading the author’s environment into Docker" in sWindow
    assert "matched the hash the project records" in sWindow
    assert "This step has run for 41 s." in sWindow
    assert pageDashboard.listPageErrors == []


def testADownloadBetweenTriesCountsDownAndSaysWhenItStops(pageDashboard, serverHub):
    dictView = fdictConversionView(dictDownloadOverrides={
        "iAttempt": 3, "fSecondsUntilRetry": 12.0, "iRetriesLeft": 4,
        "sLastFailure": "Zenodo stopped sending: no data for 120 s at 507 MB",
        "sAttemptState": "Zenodo stopped sending. Reconnecting in 12 s (attempt 4)",
    })
    sWindow = _fsOpenTheWindow(pageDashboard, serverHub, dictView)
    assert "Zenodo stopped sending: no data for 120 s at 507 MB. Next try in 12 s." in sWindow
    assert "If the next 4 tries make no progress" in sWindow
    assert pageDashboard.query_selector(
        '#modalAcquisition [data-acquisition-action="check-connection"]') is not None
    assert pageDashboard.listPageErrors == []


def testTheHeadingNamesTheDepositTheProjectPins(pageDashboard, serverHub):
    dictView = fdictConversionView(dictDownloadOverrides={"dictDeposit": {
        "sDoi": "10.5281/zenodo.5150", "sFileName": "environment-image.tar.zst",
        "sTitle": "Example environment image",
    }})
    sWindow = _fsOpenTheWindow(pageDashboard, serverHub, dictView)
    assert pageDashboard.text_content("#modalAcquisition .acquisition-title") == (
        "Downloading “Example environment image” from Zenodo"
    )
    assert ("Zenodo deposit 10.5281/zenodo.5150, file environment-image.tar.zst"
            in sWindow)
    assert pageDashboard.listPageErrors == []


def testBeforeTheRecordIsReadTheHeadingNamesTheDoi(pageDashboard, serverHub):
    dictView = _fdictLocatingView()
    dictView["dictDownload"]["dictDeposit"] = {
        "sDoi": "10.5281/zenodo.5150", "sFileName": "environment-image.tar.zst",
        "sTitle": "",
    }
    _fsOpenTheWindow(pageDashboard, serverHub, dictView)
    assert pageDashboard.text_content("#modalAcquisition .acquisition-title") == (
        "Finding deposit 10.5281/zenodo.5150 on Zenodo"
    )
    assert pageDashboard.listPageErrors == []


def _fiReadTailScroll(pageDashboard, sProperty):
    return pageDashboard.evaluate(
        "(sProperty) => document.querySelector('#modalAcquisition "
        ".acquisition-tail')[sProperty]", sProperty,
    )


@pytest.mark.falsification
def testTheLogFollowsNewLinesAndStaysWhereTheReaderScrolled(pageDashboard, serverHub):
    """Kills: setting the log's scroll before the render attaches it.

    Every poll rebuilds the window. A scroll set on the detached log is
    ignored, so the log snapped back to its first line once a second
    and nobody could follow the newest one. At the bottom it must
    follow new lines; scrolled back, it must stay where the reader is.
    """
    dictView = fdictConversionView()
    dictView["saTailLines"] = [f"line {iLine}" for iLine in range(80)]
    _fsOpenTheWindow(pageDashboard, serverHub, dictView)
    pageDashboard.wait_for_timeout(2000)
    iBottom = _fiReadTailScroll(pageDashboard, "scrollHeight") - _fiReadTailScroll(
        pageDashboard, "clientHeight")
    assert iBottom > 0
    assert _fiReadTailScroll(pageDashboard, "scrollTop") >= iBottom - 8
    dictView["saTailLines"] = dictView["saTailLines"] + ["line 80", "line 81"]
    pageDashboard.wait_for_timeout(2000)
    iNewBottom = _fiReadTailScroll(pageDashboard, "scrollHeight") - _fiReadTailScroll(
        pageDashboard, "clientHeight")
    assert iNewBottom > iBottom
    assert _fiReadTailScroll(pageDashboard, "scrollTop") >= iNewBottom - 8
    pageDashboard.evaluate(
        "() => { document.querySelector('#modalAcquisition .acquisition-tail')"
        ".scrollTop = 40; }"
    )
    dictView["saTailLines"] = dictView["saTailLines"] + ["line 82"]
    pageDashboard.wait_for_timeout(2000)
    assert _fiReadTailScroll(pageDashboard, "scrollTop") == 40
    assert pageDashboard.listPageErrors == []


@pytest.mark.falsification
def testAFinishedConversionLeavesTheNewContainerUnstartedAndUnheld(
    pageDashboard, serverHub,
):
    """Kills: starting the converted container on the researcher's behalf.

    A start claims the container for the starting tab, so the automatic
    start left the new tile "held by this tab" before the researcher had
    opened it. The window now finishes, says how to start, and starts
    nothing.
    """
    _fnListTheHostTileAsContainerizing(pageDashboard, "ready")
    dictView = fdictConversionView("ready", False)
    listStarts = []

    def fnAnswer(route):
        if route.request.method == "POST":
            route.fulfill(status=200, content_type="application/json",
                          body=json.dumps({"bCommitted": True}))
            return
        route.fulfill(status=200, content_type="application/json",
                      body=json.dumps(dictView))
    pageDashboard.route("**/api/conversions/**", fnAnswer)
    pageDashboard.route(
        "**/api/containers/**/start",
        lambda route: (listStarts.append(route.request.url), route.abort()),
    )
    _fnWaitForPicker(pageDashboard, serverHub)
    pageDashboard.wait_for_selector(_fsChipSelector(), timeout=10000)
    pageDashboard.click(_fsChipSelector())
    pageDashboard.wait_for_selector(
        '#modalAcquisition [data-acquisition-action="commit"]', timeout=5000,
    )
    pageDashboard.click('#modalAcquisition [data-acquisition-action="commit"]')
    pageDashboard.wait_for_function(
        "() => Array.from(document.querySelectorAll('.toast'))"
        ".some(el => el.textContent.includes('Click its tile to start'))",
        timeout=5000,
    )
    pageDashboard.wait_for_timeout(1500)
    assert listStarts == []
    assert pageDashboard.listPageErrors == []


@pytest.mark.falsification
def testAFinishedConversionIsAnnouncedOnceHoweverManyPollsWereWaiting(
    pageDashboard, serverHub,
):
    """Kills: every poll in flight when the record vanishes announcing it.

    The poll fires every 1.5 s whether or not the last answer has come
    back, and the hub is slow to answer while it commits, so several
    polls can be waiting when the record disappears. Each one that met
    the 404 showed "Converted ...", and the researcher saw three
    identical toasts (researcher-reported, 2026-10-16).
    """
    _fsOpenTheWindow(pageDashboard, serverHub, fdictConversionView())
    listHeld = []
    pageDashboard.route(
        "**/api/conversions/**",
        lambda route: listHeld.append(route) if route.request.method == "GET"
        else route.continue_(),
    )
    pageDashboard.wait_for_timeout(5000)
    assert len(listHeld) >= 3, "the poll must have several checks waiting"
    for routeHeld in listHeld:
        routeHeld.fulfill(status=404, content_type="application/json",
                          body=json.dumps({"detail": "no conversion"}))
    pageDashboard.wait_for_function(
        "() => Array.from(document.querySelectorAll('.toast'))"
        ".some(el => el.textContent.includes('Click its tile to start'))",
        timeout=5000,
    )
    pageDashboard.wait_for_timeout(1000)
    iAnnouncements = pageDashboard.evaluate(
        "() => Array.from(document.querySelectorAll('.toast'))"
        ".filter(el => el.textContent.includes('Click its tile to start'))"
        ".length")
    assert iAnnouncements == 1
    assert pageDashboard.listPageErrors == []
