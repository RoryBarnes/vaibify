"""A poll that did not refresh what is on screen says so.

Two dashboard surfaces kept the last numbers they had when a poll let
them down, and drew them exactly as they had drawn the fresh ones:

- a file-status poll that shipped no reproducibility envelope left the
  Project block's rows from the previous poll, unmarked;
- a resource-monitor request that failed (a 500, or no answer at all)
  was swallowed without a trace, so the CPU and memory figures froze
  and still read as live.

Keeping the old rows or figures is right: a flicker to blank is worse.
Keeping them UNMARKED is a claim that nothing changed, and these tests
drive the real modules over canned server answers to pin the mark and
its removal when the next answer is good.
"""

import json

import pytest

from tests.browser.conftest import fnOpenTheSeededHostWorkflow

pytestmark = pytest.mark.browser


@pytest.fixture(autouse=True)
def fixtureDropClaimsBetweenTests(serverHub):
    yield
    from vaibify.config.containerLock import fnReleaseContainerLock
    dictContainerOwners = serverHub.app.state.dictContainerOwners
    for _sName, recordOwner in list(dictContainerOwners.items()):
        fileHandle = getattr(recordOwner, "fileHandleLock", None)
        if fileHandle is not None:
            try:
                fnReleaseContainerLock(fileHandle)
            except OSError:
                pass
    dictContainerOwners.clear()
    serverHub.app.state.dictSessionOwner.clear()


def _fnServeFileStatusPollOmittingTheEnvelopeWhenAsked(page, dictControl):
    def fnAnswer(route):
        responseReal = route.fetch()
        dictBody = responseReal.json()
        if dictControl.get("bOmit"):
            dictBody.pop("dictWorkflowEnvelopeDetail", None)
        route.fulfill(
            status=200, content_type="application/json",
            body=json.dumps(dictBody))
    page.route("**/api/pipeline/*/file-status*", fnAnswer)


@pytest.mark.falsification
def testAPollThatOmitsTheEnvelopeMarksTheProjectRowsStale(
    pageDashboard, serverHub,
):
    """Kills: keeping the previous poll's rows with nothing to say so."""
    dictControl = {}
    fnOpenTheSeededHostWorkflow(
        pageDashboard, serverHub, bAwaitProjectBlock=True)
    _fnServeFileStatusPollOmittingTheEnvelopeWhenAsked(pageDashboard, dictControl)
    # A good poll first: the block is fresh, so nothing is marked.
    pageDashboard.wait_for_function(
        "() => document.querySelector('#projectBlock"
        " .requirement-group') !== null", timeout=15000)
    assert pageDashboard.locator(".project-block-stale").count() == 0
    dictControl["bOmit"] = True
    pageDashboard.wait_for_selector(
        ".project-block-stale", state="attached", timeout=30000)
    sNotice = pageDashboard.locator(".project-block-stale").inner_text()
    assert "earlier" in sNotice, sNotice
    assert pageDashboard.locator(
        "#projectBlock .requirement-group").count() > 0, (
        "the earlier rows must stay; the mark is what is added")
    dictControl["bOmit"] = False
    pageDashboard.wait_for_selector(
        ".project-block-stale", state="detached", timeout=30000)
    assert pageDashboard.listPageErrors == []


_S_MONITOR_OK = {
    "bAvailable": True, "fCpuPercent": 12.5, "fMemoryPercent": 30.0,
    "sMemoryUsage": "1.2GiB / 4GiB",
    "dictDisk": {"bAvailable": True, "sUsedHuman": "1G",
                 "sTotalHuman": "10G", "fFreeFraction": 0.9},
    "bDiskWarning": False,
}


def _fnServeMonitor(page, dictControl):
    def fnAnswer(route):
        if dictControl.get("bFail"):
            route.fulfill(status=500, content_type="application/json",
                          body='{"detail": "docker stats failed"}')
            return
        route.fulfill(status=200, content_type="application/json",
                      body=json.dumps(_S_MONITOR_OK))
    page.route("**/api/monitor/*", fnAnswer)


@pytest.mark.falsification
def testAFailedMonitorPollMarksTheFiguresStale(pageDashboard, serverHub):
    """Kills: swallowing a failed monitor request and leaving the last
    CPU and memory figures drawn as live."""
    dictControl = {}
    fnOpenTheSeededHostWorkflow(pageDashboard, serverHub)
    _fnServeMonitor(pageDashboard, dictControl)
    pageDashboard.evaluate("() => VaibifyMonitor.fnTogglePanel()")
    pageDashboard.wait_for_function(
        "() => document.getElementById('monitorCpuText')"
        " && document.getElementById('monitorCpuText')"
        ".textContent === '12.5%'", timeout=10000)
    assert pageDashboard.get_attribute("#monitorPanel", "data-stale") is None
    dictControl["bFail"] = True
    pageDashboard.wait_for_function(
        "() => document.getElementById('monitorPanel')"
        ".getAttribute('data-stale') === 'true'", timeout=15000)
    sNotice = pageDashboard.inner_text("#monitorUnavailable")
    assert "poll failed" in sNotice, sNotice
    assert pageDashboard.inner_text("#monitorCpuText") == "12.5%", (
        "the last figure stays on screen; it is marked, not erased")
    dictControl["bFail"] = False
    pageDashboard.wait_for_function(
        "() => document.getElementById('monitorPanel')"
        ".getAttribute('data-stale') === null", timeout=15000)
    assert pageDashboard.inner_text("#monitorUnavailable").strip() == ""
    assert pageDashboard.listPageErrors == []
