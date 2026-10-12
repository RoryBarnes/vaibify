"""The memory watch's chip, banner and toasts, in a real browser.

The hub's answer is stubbed at the network edge so each journey controls
exactly what the server says; everything between that answer and the
screen -- the poll, the render, the toast bookkeeping, localStorage --
is the production frontend. The container is opened in Blank Project
mode on purpose: that is the mode in which the file-status poll does
not run, which is why the memory watch has its own.

A kill must be told ONCE: one error toast per incident across polls and
across a reload, and the banner must carry the server's sentence
verbatim, never a sentence of the frontend's own.

The chip appears ONLY while it warns (ruled 2026-10-12): absent while
memory is measured and below the limit, and present -- read off the
live element's visibility, not its text -- for each of the four
warnings it alone carries once the banner and toasts are gone: the
dashboard failing to reach the memory watch, the server's "could not
measure", a held kill after its banner is dismissed, and "near" after
its toast is closed. Hiding is ``display: none``, so a hidden chip has
no box and leaves no gap in the toolbar.

Before the first measurement since opening, the chip reads "Checking
Memory" -- neutral and pulsing, not the gray "could not tell" -- and
the SERVER decides it, so those journeys are answered with the
server's own description of an opening, never a hand-written one.

WHAT THIS DOES NOT COVER: the sampler, the kernel counters and the
route, which ``testContainerMemoryWatch*.py`` drive (the live file
against a real daemon); and whether the chip is legible in every
browser's rendering of the toolbar, which is checked by hand.
"""

import json
from datetime import datetime, timedelta, timezone

import pytest

from tests.browser.fakeDockerAdapter import S_CONTAINER_ID, S_CONTAINER_NAME
from vaibify.gui import containerMemoryWatch
from tests.browser.filesLane import fixtureDropClaimsBetweenJourneys  # noqa: F401


pytestmark = [
    pytest.mark.browser,
    pytest.mark.usefixtures("fixtureDropClaimsBetweenJourneys"),
]

S_MEMORY_GLOB = "**/api/monitor/*/memory"
S_KILL_SENTENCE = (
    "Between 3:15:58 PM and 3:16:13 PM the kernel's out-of-memory killer "
    "killed 1 process in this container. The container also recorded "
    "reaching its 1 GB memory limit during that interval. Other "
    "processes may have depended on the one killed; check that running "
    "work is still healthy. If an AI agent was running in the terminal, "
    "its conversation can usually be resumed (Claude Code: claude "
    "--resume). To raise the limit, open Settings.")
S_NEAR_SENTENCE = (
    "Memory in use is near this container's limit: about 900 MB of 1 GB "
    "(an estimate). At the limit the kernel kills a process in the "
    "container. To raise the limit, open Settings.")


def _fdictAnswer(sLevel="ok", listIncidents=None, sNearSinceIso=""):
    listIncidents = listIncidents or []
    return {
        "sContainerName": S_CONTAINER_NAME,
        "iKillCount": sum(d["iKillCount"] for d in listIncidents),
        "listIncidents": listIncidents,
        "dictCurrent": {
            "sState": "measured", "sLevel": sLevel,
            "sSentence": S_NEAR_SENTENCE if sLevel == "near" else (
                "About 200 MB of this container's 1 GB memory limit is "
                "in use (an estimate of the working set)."),
            "sChipText": "Memory ~0.9 / 1 GB" if sLevel == "near" else (
                "Memory ~0.2 / 1 GB"),
            "sNearSinceIso": sNearSinceIso,
        },
    }


DT_OPENED = datetime(2026, 10, 12, 9, 0, 0, tzinfo=timezone.utc)


def _fdictServerAnswerAfterOpening(fSeconds):
    """The hub's own answer for an opening not yet measured, fSeconds in."""
    dictStore = containerMemoryWatch.fdictCreateMemoryStore()
    containerMemoryWatch.fnNoteWatchBegan(
        dictStore, S_CONTAINER_NAME, DT_OPENED)
    return containerMemoryWatch.fdictDescribeMemory(
        dictStore, S_CONTAINER_NAME, DT_OPENED + timedelta(seconds=fSeconds))


def _fsChipStyle(page, sProperty):
    return page.evaluate(
        "(sProperty) => getComputedStyle(document.getElementById("
        "'memoryWatchChip'))[sProperty]", sProperty)


def _fdictKill():
    return {
        "sIncidentId": f"oomKill:{S_CONTAINER_ID}:1", "sKind": "oomKill",
        "sContainerId": S_CONTAINER_ID, "iKillCount": 1,
        "sSentence": S_KILL_SENTENCE,
    }


def _flistStubMemory(page, dictAnswerHolder):
    """Answer the memory route from a holder the journey can change."""
    listRequests = []

    def fnHandle(routeIntercepted):
        listRequests.append(routeIntercepted.request.url)
        routeIntercepted.fulfill(
            status=200, content_type="application/json",
            body=json.dumps(dictAnswerHolder["dictAnswer"]))

    page.route(S_MEMORY_GLOB, fnHandle)
    return listRequests


def _fnEnterTheBlankProject(page, serverHub=None):
    if serverHub is not None:
        page.goto(serverHub.fsBootstrapUrl(), wait_until="load")
    page.wait_for_selector(
        f'.container-tile[data-name="{S_CONTAINER_NAME}"]', timeout=15000)
    page.evaluate(
        "(sId) => VaibifyContainerManager.fnConnectToContainer(sId)",
        S_CONTAINER_ID)
    page.wait_for_selector("#workflowPicker", state="visible")
    page.click("#btnNoWorkflow")
    page.wait_for_selector("#mainLayout.active", timeout=20000)


def _fnWaitForRequests(page, listRequests, iCount, fSeconds=25.0):
    iWaitedMilliseconds = 0
    while len(listRequests) < iCount:
        assert iWaitedMilliseconds < fSeconds * 1000, (
            f"only {len(listRequests)} memory polls arrived")
        page.wait_for_timeout(250)
        iWaitedMilliseconds += 250
    page.wait_for_timeout(300)


def _fnWaitForTheNextPoll(page, listRequests):
    """Wait for the dashboard's own interval poll, never a forced one.

    ``fnStart`` hides the chip before it polls, so forcing a poll
    would make a chip that failed to hide itself look hidden.
    """
    _fnWaitForRequests(page, listRequests, len(listRequests) + 1)


def _fbChipHasNoBox(page):
    """True when the chip occupies no space: hidden, and leaving no gap."""
    return page.query_selector("#memoryWatchChip").bounding_box() is None


def _fnAssertChipShows(page, sText):
    assert page.is_visible("#memoryWatchChip"), (
        f"the chip should warn with {sText!r} but is hidden")
    assert page.text_content("#memoryWatchChip") == sText


def _fiCountToasts(page, sType, sSentence):
    return page.evaluate(
        """([sType, sSentence]) => Array.from(
            document.querySelectorAll('#toastContainer .toast.' + sType))
            .filter(el => el.textContent.indexOf(sSentence) >= 0).length""",
        [sType, sSentence])


@pytest.mark.falsification
def testTheBannerCarriesTheServersSentenceVerbatim(pageDashboard, serverHub):
    """Kills: the banner composing a sentence of its own."""
    dictHolder = {"dictAnswer": _fdictAnswer(listIncidents=[_fdictKill()])}
    listRequests = _flistStubMemory(pageDashboard, dictHolder)
    _fnEnterTheBlankProject(pageDashboard, serverHub)
    _fnWaitForRequests(pageDashboard, listRequests, 1)
    sBanner = pageDashboard.text_content(
        f'.memory-incident-banner[data-incident-id="oomKill:{S_CONTAINER_ID}:1"]'
        " .memory-incident-sentence")
    assert sBanner == S_KILL_SENTENCE
    assert pageDashboard.listPageErrors == []


@pytest.mark.falsification
def testAKillIsToastedOnceAcrossPollsAndAReload(pageDashboard, serverHub):
    """Kills: deciding to toast per poll instead of per incident id."""
    dictHolder = {"dictAnswer": _fdictAnswer(listIncidents=[_fdictKill()])}
    listRequests = _flistStubMemory(pageDashboard, dictHolder)
    _fnEnterTheBlankProject(pageDashboard, serverHub)
    _fnWaitForRequests(pageDashboard, listRequests, 2)
    assert _fiCountToasts(pageDashboard, "error", S_KILL_SENTENCE) == 1
    pageDashboard.reload(wait_until="load")
    iBeforeReentry = len(listRequests)
    _fnEnterTheBlankProject(pageDashboard)
    _fnWaitForRequests(pageDashboard, listRequests, iBeforeReentry + 1)
    assert _fiCountToasts(pageDashboard, "error", S_KILL_SENTENCE) == 0, (
        "the same incident was told again after a reload")
    assert pageDashboard.is_visible(".memory-incident-banner")
    assert pageDashboard.listPageErrors == []


@pytest.mark.falsification
def testADismissedBannerStaysDismissedAndTheChipKeepsTheCount(
    pageDashboard, serverHub,
):
    """Kills: hiding the chip at level "ok" while a kill is still held.

    The level is "ok" on purpose: once the banner is dismissed, the
    chip is the only place the kill count survives.
    """
    dictAnswer = _fdictAnswer(listIncidents=[_fdictKill()])
    dictAnswer["dictCurrent"]["sChipText"] = "Memory ~0.2 / 1 GB · 1 process killed"
    dictHolder = {"dictAnswer": dictAnswer}
    listRequests = _flistStubMemory(pageDashboard, dictHolder)
    _fnEnterTheBlankProject(pageDashboard, serverHub)
    _fnWaitForRequests(pageDashboard, listRequests, 1)
    pageDashboard.click(
        ".memory-incident-banner .build-warnings-banner-dismiss")
    _fnWaitForRequests(pageDashboard, listRequests, 2)
    assert pageDashboard.query_selector(".memory-incident-banner") is None
    _fnAssertChipShows(pageDashboard, "Memory ~0.2 / 1 GB · 1 process killed")
    assert "memory-watch-chip-killed" in pageDashboard.get_attribute(
        "#memoryWatchChip", "class")


@pytest.mark.falsification
def testNearToastsOncePerEpisode(pageDashboard, serverHub):
    """Kills: keying the near toast by its level instead of its episode.

    Each call to fnStart is the immediate poll the dashboard makes on
    opening a container; driving it here keeps four polls inside a few
    seconds rather than forty.
    """
    dictHolder = {"dictAnswer": _fdictAnswer(
        sLevel="near", sNearSinceIso="2026-10-08T22:15:58+00:00")}
    listRequests = _flistStubMemory(pageDashboard, dictHolder)
    _fnEnterTheBlankProject(pageDashboard, serverHub)

    def fnPollNow():
        iBefore = len(listRequests)
        pageDashboard.evaluate(
            "(sId) => VaibifyMemoryWatch.fnStart(sId)", S_CONTAINER_ID)
        _fnWaitForRequests(pageDashboard, listRequests, iBefore + 1)

    _fnWaitForRequests(pageDashboard, listRequests, 1)
    fnPollNow()
    assert _fiCountToasts(pageDashboard, "warning", S_NEAR_SENTENCE) == 1
    dictHolder["dictAnswer"] = _fdictAnswer(sLevel="ok")
    fnPollNow()
    dictHolder["dictAnswer"] = _fdictAnswer(
        sLevel="near", sNearSinceIso="2026-10-08T22:20:13+00:00")
    fnPollNow()
    assert _fiCountToasts(pageDashboard, "warning", S_NEAR_SENTENCE) == 2
    assert pageDashboard.get_attribute(
        "#memoryWatchChip", "data-level") == "near"


@pytest.mark.falsification
def testAnUnknownChipIsGrayAndSaysWhy(pageDashboard, serverHub):
    """Kills: treating only "near" as a warning, which hides "unknown"."""
    dictAnswer = _fdictAnswer()
    dictAnswer["dictCurrent"].update({
        "sState": "timeout", "sLevel": "unknown",
        "sChipText": "Memory unknown",
        "sSentence": ("Memory could not be checked: the container did not "
                      "answer within 5 seconds."),
    })
    dictHolder = {"dictAnswer": dictAnswer}
    listRequests = _flistStubMemory(pageDashboard, dictHolder)
    _fnEnterTheBlankProject(pageDashboard, serverHub)
    _fnWaitForRequests(pageDashboard, listRequests, 1)
    _fnAssertChipShows(pageDashboard, "Memory unknown")
    assert pageDashboard.get_attribute(
        "#memoryWatchChip", "data-level") == "unknown"
    assert pageDashboard.get_attribute("#memoryWatchChip", "title") == (
        dictAnswer["dictCurrent"]["sSentence"])
    sColor = pageDashboard.evaluate(
        "() => getComputedStyle(document.getElementById("
        "'memoryWatchChip')).fontStyle")
    assert sColor == "italic"


@pytest.mark.falsification
def testAQuietMemoryStateShowsNoChip(pageDashboard, serverHub):
    """Kills: showing the chip whenever the server sends chip text.

    Measured, below the limit and with no kill held, the top row
    carries no memory chip at all -- not an empty or transparent one.
    """
    dictHolder = {"dictAnswer": _fdictAnswer(sLevel="ok")}
    listRequests = _flistStubMemory(pageDashboard, dictHolder)
    _fnEnterTheBlankProject(pageDashboard, serverHub)
    _fnWaitForRequests(pageDashboard, listRequests, 1)
    assert _fbChipHasNoBox(pageDashboard), (
        "a quiet memory state still shows the chip: " + repr(
            pageDashboard.text_content("#memoryWatchChip")))
    assert pageDashboard.listPageErrors == []


@pytest.mark.falsification
def testTheChipComesAndGoesWithTheWarningWithoutAReload(
    pageDashboard, serverHub,
):
    """Kills: a render that shows the chip but never hides it again.

    Every step is the dashboard's own interval poll on one page load:
    quiet, then near the limit, then quiet again.
    """
    dictHolder = {"dictAnswer": _fdictAnswer(sLevel="ok")}
    listRequests = _flistStubMemory(pageDashboard, dictHolder)
    _fnEnterTheBlankProject(pageDashboard, serverHub)
    _fnWaitForRequests(pageDashboard, listRequests, 1)
    assert _fbChipHasNoBox(pageDashboard)
    dictHolder["dictAnswer"] = _fdictAnswer(
        sLevel="near", sNearSinceIso="2026-10-08T22:15:58+00:00")
    _fnWaitForTheNextPoll(pageDashboard, listRequests)
    _fnAssertChipShows(pageDashboard, "Memory ~0.9 / 1 GB")
    dictHolder["dictAnswer"] = _fdictAnswer(sLevel="ok")
    _fnWaitForTheNextPoll(pageDashboard, listRequests)
    assert _fbChipHasNoBox(pageDashboard), (
        "the chip stayed on screen after memory fell back below the limit")
    assert pageDashboard.listPageErrors == []


@pytest.mark.falsification
def testNearKeepsTheChipAfterItsToastIsClosed(pageDashboard, serverHub):
    """Kills: treating only "unknown" as a warning, which hides "near".

    The near toast is told once per episode, so after it is closed the
    chip is the only thing on screen still saying memory is high.
    """
    dictHolder = {"dictAnswer": _fdictAnswer(
        sLevel="near", sNearSinceIso="2026-10-08T22:15:58+00:00")}
    listRequests = _flistStubMemory(pageDashboard, dictHolder)
    _fnEnterTheBlankProject(pageDashboard, serverHub)
    _fnWaitForRequests(pageDashboard, listRequests, 1)
    assert _fiCountToasts(pageDashboard, "warning", S_NEAR_SENTENCE) == 1
    pageDashboard.click("#toastContainer .toast.warning .toast-close")
    _fnWaitForTheNextPoll(pageDashboard, listRequests)
    assert _fiCountToasts(pageDashboard, "warning", S_NEAR_SENTENCE) == 0
    _fnAssertChipShows(pageDashboard, "Memory ~0.9 / 1 GB")
    assert pageDashboard.get_attribute(
        "#memoryWatchChip", "data-level") == "near"


@pytest.mark.falsification
def testAnUnreachableMemoryWatchShowsTheUnknownChip(pageDashboard, serverHub):
    """Kills: hiding the chip when the hub's answer does not arrive.

    No answer is not a quiet answer: the dashboard cannot tell, so the
    chip must say so rather than leave the top row reading as normal.
    """
    listRequests = []

    def fnRefuse(routeIntercepted):
        listRequests.append(routeIntercepted.request.url)
        routeIntercepted.fulfill(
            status=500, content_type="application/json",
            body='{"detail": "memory watch unavailable"}')

    pageDashboard.route(S_MEMORY_GLOB, fnRefuse)
    _fnEnterTheBlankProject(pageDashboard, serverHub)
    _fnWaitForRequests(pageDashboard, listRequests, 1)
    _fnAssertChipShows(pageDashboard, "Memory unknown")
    assert pageDashboard.get_attribute("#memoryWatchChip", "title") == (
        "Memory could not be checked: this dashboard could not reach the "
        "hub's memory watch.")
    assert pageDashboard.listPageErrors == []


@pytest.mark.falsification
def testAJustOpenedContainerReadsCheckingMemoryThenNothing(
    pageDashboard, serverHub,
):
    """Kills: treating the server's "pending" as a quiet state.

    Pending is shown, neutral and pulsing -- neither hidden (nothing is
    known yet) nor the gray italic "could not tell" (nothing failed).
    The first quiet measurement then takes the chip away, live.
    """
    dictHolder = {"dictAnswer": _fdictServerAnswerAfterOpening(5)}
    listRequests = _flistStubMemory(pageDashboard, dictHolder)
    _fnEnterTheBlankProject(pageDashboard, serverHub)
    _fnWaitForRequests(pageDashboard, listRequests, 1)
    _fnAssertChipShows(pageDashboard, "Checking Memory")
    assert pageDashboard.get_attribute(
        "#memoryWatchChip", "data-level") == "pending"
    assert _fsChipStyle(pageDashboard, "fontStyle") == "normal"
    assert _fsChipStyle(pageDashboard, "animationName") == "pulse"
    dictHolder["dictAnswer"] = _fdictAnswer(sLevel="ok")
    _fnWaitForTheNextPoll(pageDashboard, listRequests)
    assert _fbChipHasNoBox(pageDashboard), (
        "the chip stayed after the first quiet measurement")
    assert pageDashboard.listPageErrors == []


@pytest.mark.falsification
def testAPendingThatOutlastsItsWindowReadsMemoryUnknown(
    pageDashboard, serverHub,
):
    """Kills: the page deciding "Checking Memory" from the measurement state.

    Only the server's level says pending. Past its window the server
    says it could not measure, and the chip must show that warning even
    though nothing has been measured yet.
    """
    dictHolder = {"dictAnswer": _fdictServerAnswerAfterOpening(5)}
    listRequests = _flistStubMemory(pageDashboard, dictHolder)
    _fnEnterTheBlankProject(pageDashboard, serverHub)
    _fnWaitForRequests(pageDashboard, listRequests, 1)
    _fnAssertChipShows(pageDashboard, "Checking Memory")
    dictOverdue = _fdictServerAnswerAfterOpening(
        containerMemoryWatch.F_MEMORY_STALE_AFTER_SECONDS + 1)
    dictHolder["dictAnswer"] = dictOverdue
    _fnWaitForTheNextPoll(pageDashboard, listRequests)
    _fnAssertChipShows(pageDashboard, "Memory unknown")
    assert pageDashboard.get_attribute(
        "#memoryWatchChip", "data-level") == "unknown"
    assert pageDashboard.get_attribute("#memoryWatchChip", "title") == (
        dictOverdue["dictCurrent"]["sSentence"])
    assert _fsChipStyle(pageDashboard, "fontStyle") == "italic"
    assert pageDashboard.listPageErrors == []
