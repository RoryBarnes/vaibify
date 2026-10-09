"""The page applies the category states the server derived, and says so once per run.

The server reads a step's marker into one state per test category and
sends those states with the newest run's identity. The page applies
what it was told and never recomputes it: a category the marker does
not speak for keeps what the page already shows, and an external run is
announced once, however many polls carry it. These are claims about the
SCREEN's behaviour, and the Python suite never executes the page, so
the poll's answer is altered in flight to the shape a marker produces
and the assertions read what the page then holds and shows.
"""

import json

import pytest

from tests.browser.conftest import fnOpenTheSeededHostWorkflow


pytestmark = pytest.mark.browser

S_ANNOUNCEMENT = "external run detected"
I_POLLS_TO_SEE_A_RUN_REPEATED = 3


@pytest.fixture(autouse=True)
def fixtureDropClaimsBetweenTests(serverHub):
    """Give every claim back after each test (the hub outlives the page)."""
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


@pytest.fixture(autouse=True)
def fixtureStopInterceptingThePoll(pageDashboard):
    """Drop the poll route before the page closes (see the sibling tests)."""
    yield
    pageDashboard.unroute_all(behavior="ignoreErrors")


def _fdictState(sState, bHasMarkerInfo=True):
    return {
        "sState": sState, "iTotalTests": 2, "bLegacy": False,
        "iCurrentTests": 2 if sState == "passed" else 0,
        "iFailedTests": 1 if sState == "failed" else 0,
        "bHasMarkerInfo": bHasMarkerInfo, "sNotCurrentBecause": "",
    }


def _fdictMarkersOfRun(sRunId, dictStateByCategory, iExitStatus=0):
    return {"0": {
        "dictMarker": {
            "sRunId": sRunId, "fTimestamp": 1.0,
            "sRunAtUtc": "2026-10-07T00:00:00Z", "iExitStatus": iExitStatus},
        "dictCategoryStates": dictStateByCategory,
        "bStale": False,
    }}


def _fnAnswerEveryPollWith(pageDashboard, dictScript):
    """Alter each poll's answer to carry ``dictScript['dictMarkers']``."""
    def fnHandle(routePoll):
        responsePoll = routePoll.fetch()
        dictBody = responsePoll.json()
        if dictScript["dictMarkers"]:
            dictBody["dictTestMarkers"] = dictScript["dictMarkers"]
            dictScript["iPollsWithMarkers"] += 1
        routePoll.fulfill(
            response=responsePoll, body=json.dumps(dictBody),
            headers={"content-type": "application/json"})
    pageDashboard.route("**/file-status*", fnHandle)


def _fnWaitForPollsCarryingMarkers(pageDashboard, dictScript, iPolls):
    iTarget = dictScript["iPollsWithMarkers"] + iPolls
    for _iAttempt in range(60):
        if dictScript["iPollsWithMarkers"] >= iTarget:
            return
        pageDashboard.wait_for_timeout(1000)
    raise AssertionError(
        "the page did not poll %d more times within a minute" % iPolls)


def _fdictStepVerification(pageDashboard):
    return pageDashboard.evaluate(
        "() => VaibifyApp.fdictGetWorkflow().listSteps[0].dictVerification")


_S_RECORD_EVERY_TOAST = """() => {
    window.listToastMessages = [];
    const fnShowToast = VaibifyApp.fnShowToast;
    VaibifyApp.fnShowToast = function (sMessage) {
        window.listToastMessages.push(sMessage);
        return fnShowToast.apply(this, arguments);
    };
}"""


def _fiAnnouncements(pageDashboard):
    """Count announcements by what the page was asked to show.

    A toast dismisses itself after a few seconds, so counting the ones
    still on screen would count however many happen to be unexpired.
    """
    listMessages = pageDashboard.evaluate("() => window.listToastMessages")
    return len([s for s in listMessages if S_ANNOUNCEMENT in s])


def _fnOpenAndHoldTheStepAs(pageDashboard, serverHub, dictVerification):
    dictScript = {"dictMarkers": {}, "iPollsWithMarkers": 0}
    _fnAnswerEveryPollWith(pageDashboard, dictScript)
    fnOpenTheSeededHostWorkflow(pageDashboard, serverHub)
    pageDashboard.evaluate(_S_RECORD_EVERY_TOAST)
    pageDashboard.evaluate(
        "(dictVerification) => {"
        " VaibifyApp.fdictGetWorkflow().listSteps[0].dictVerification ="
        " dictVerification; }", dictVerification)
    return dictScript


@pytest.mark.falsification
def test_a_category_the_marker_does_not_speak_for_keeps_what_the_page_shows(
    pageDashboard, serverHub,
):
    """Passed and failed are applied; the unspoken category is left alone.

    Kills: applying a category state whose ``bHasMarkerInfo`` is false,
    which would overwrite a result the marker has no word about.
    """
    dictScript = _fnOpenAndHoldTheStepAs(pageDashboard, serverHub, {
        "sUnitTest": "untested", "sIntegrity": "untested",
        "sQualitative": "untested", "sQuantitative": "passed",
        "sUser": "untested"})
    dictScript["dictMarkers"] = _fdictMarkersOfRun("run-one", {
        "integrity": _fdictState("passed"),
        "qualitative": _fdictState("failed"),
        "quantitative": _fdictState("untested", bHasMarkerInfo=False)},
        iExitStatus=1)
    _fnWaitForPollsCarryingMarkers(pageDashboard, dictScript, 2)
    dictVerification = _fdictStepVerification(pageDashboard)
    assert dictVerification["sIntegrity"] == "passed", dictVerification
    assert dictVerification["sQualitative"] == "failed", dictVerification
    assert dictVerification["sQuantitative"] == "passed", dictVerification


@pytest.mark.falsification
def test_a_run_is_announced_once_however_many_polls_carry_it(
    pageDashboard, serverHub,
):
    """The same run seen again, with its result changed locally, is not news.

    A different path (a local edit, a stale reset) can put an axis back
    to untested between polls; the next poll then re-applies the very
    run already announced. A toast per application would announce one
    run every time it was re-applied.

    Kills: dropping the per-run memory, which toasts once per change
    instead of once per run.
    """
    dictScript = _fnOpenAndHoldTheStepAs(pageDashboard, serverHub, {
        "sUnitTest": "untested", "sIntegrity": "untested",
        "sQualitative": "untested", "sQuantitative": "untested",
        "sUser": "untested"})
    dictScript["dictMarkers"] = _fdictMarkersOfRun(
        "run-one", {"integrity": _fdictState("passed")})
    _fnWaitForPollsCarryingMarkers(pageDashboard, dictScript, 2)
    assert _fiAnnouncements(pageDashboard) == 1
    pageDashboard.evaluate(
        "() => { VaibifyApp.fdictGetWorkflow().listSteps[0]"
        ".dictVerification.sIntegrity = 'untested'; }")
    _fnWaitForPollsCarryingMarkers(
        pageDashboard, dictScript, I_POLLS_TO_SEE_A_RUN_REPEATED)
    assert _fdictStepVerification(pageDashboard)["sIntegrity"] == "passed"
    assert _fiAnnouncements(pageDashboard) == 1


def test_a_new_run_is_announced_again(pageDashboard, serverHub):
    dictScript = _fnOpenAndHoldTheStepAs(pageDashboard, serverHub, {
        "sUnitTest": "untested", "sIntegrity": "untested",
        "sQualitative": "untested", "sQuantitative": "untested",
        "sUser": "untested"})
    dictScript["dictMarkers"] = _fdictMarkersOfRun(
        "run-one", {"integrity": _fdictState("passed")})
    _fnWaitForPollsCarryingMarkers(pageDashboard, dictScript, 2)
    dictScript["dictMarkers"] = _fdictMarkersOfRun(
        "run-two", {"integrity": _fdictState("failed")}, iExitStatus=1)
    _fnWaitForPollsCarryingMarkers(pageDashboard, dictScript, 2)
    assert _fiAnnouncements(pageDashboard) == 2
