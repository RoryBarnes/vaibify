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

WHAT THIS DOES NOT COVER: the sampler, the kernel counters and the
route, which ``testContainerMemoryWatch*.py`` drive (the live file
against a real daemon); and whether the chip is legible in every
browser's rendering of the toolbar, which is checked by hand.
"""

import json

import pytest

from tests.browser.fakeDockerAdapter import S_CONTAINER_ID, S_CONTAINER_NAME
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


def testADismissedBannerStaysDismissedAndTheChipKeepsTheCount(
    pageDashboard, serverHub,
):
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
    assert pageDashboard.text_content("#memoryWatchChip") == (
        "Memory ~0.2 / 1 GB · 1 process killed")
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


def testAnUnknownChipIsGrayAndSaysWhy(pageDashboard, serverHub):
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
    assert pageDashboard.get_attribute(
        "#memoryWatchChip", "data-level") == "unknown"
    assert pageDashboard.get_attribute("#memoryWatchChip", "title") == (
        dictAnswer["dictCurrent"]["sSentence"])
    sColor = pageDashboard.evaluate(
        "() => getComputedStyle(document.getElementById("
        "'memoryWatchChip')).fontStyle")
    assert sColor == "italic"
