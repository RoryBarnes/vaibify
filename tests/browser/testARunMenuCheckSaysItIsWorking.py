"""A Run-menu check says it heard the click, and stops saying so when done.

Check Files Against Manifest and Verify Level 3 Reproducibility each
spend seconds on a round trip before anything can be shown: re-hashing
the pinned files, or asking whether the project is ready. The menu
closes on the click and has no button to hold busy, so a new researcher
met seconds of silence and could not tell anything had happened
(researcher-reported).

Both properties are asserted against a HELD response: the notice must
be on screen while the request is unanswered, and gone once the answer
is shown. A timed toast passes the first half and fails the second --
it outlives a fast answer, still claiming the work beside its outcome.
"""

import json

import pytest

from tests.browser.conftest import fnOpenTheSeededHostWorkflow


pytestmark = pytest.mark.browser

S_MANIFEST_NOTICE = "Checking files against the manifest"
S_READINESS_NOTICE = (
    "Checking whether this project is ready for Level 3 verification"
)


@pytest.fixture(autouse=True)
def fixtureDropClaimsBetweenTests(serverHub):
    """Give every claim back after each test (see the sibling files)."""
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


def _flistHoldEveryRequest(pageDashboard, sGlob):
    """Hold each matching request unanswered until the test releases it."""
    listHeld = []
    pageDashboard.route(sGlob, lambda route: listHeld.append(route))
    return listHeld


def _fnWaitUntil(pageDashboard, fbCondition, iTimeoutSeconds=15):
    for _ in range(iTimeoutSeconds * 10):
        if fbCondition():
            return
        pageDashboard.wait_for_timeout(100)


def _flistToastTexts(pageDashboard):
    return pageDashboard.evaluate(
        """() => Array.from(document.querySelectorAll(
            '#toastContainer .toast')).map(el => el.textContent)"""
    )


def _fbAnyToastContains(pageDashboard, sText):
    return any(sText in s for s in _flistToastTexts(pageDashboard))


def _fnChooseFromRunMenu(pageDashboard, sItemId):
    pageDashboard.click("#toolbarMenuRun .toolbar-menu-trigger")
    pageDashboard.click("#" + sItemId)


def _fsReadyReadinessBody():
    """The readiness route's real envelope, every gap closed."""
    from vaibify.reproducibility.levelGates import fdictL3ReadinessGaps
    dictGaps = {
        sKey: (True if isinstance(objValue, bool) else objValue)
        for sKey, objValue in fdictL3ReadinessGaps(
            {}, "/nonexistent-repo-for-shape",
        ).items()
    }
    return json.dumps({"iProofLevel": 3, "dictL3ReadinessGaps": dictGaps})


@pytest.mark.falsification
def test_the_manifest_check_says_it_started_until_it_answers(
    pageDashboard, serverHub,
):
    """The notice shows while the check runs and leaves with the answer.

    Kills: dropping the start notice's dismissal -- the notice outlives
    the answer and goes on claiming a check that has ended.
    """
    fnOpenTheSeededHostWorkflow(pageDashboard, serverHub)
    listHeld = _flistHoldEveryRequest(
        pageDashboard, "**/api/workflow/**/manifest/verify")
    _fnChooseFromRunMenu(pageDashboard, "btnVerifyManifest")
    _fnWaitUntil(pageDashboard, lambda: bool(listHeld))
    assert listHeld, "the manifest check never left the page"
    assert _fbAnyToastContains(pageDashboard, S_MANIFEST_NOTICE), (
        "nothing on screen says the check started while it runs: "
        f"{_flistToastTexts(pageDashboard)!r}"
    )

    listHeld[0].fulfill(
        status=200, content_type="application/json",
        body=json.dumps({"iTotal": 3, "listMismatches": []}),
    )
    _fnWaitUntil(pageDashboard, lambda: _fbAnyToastContains(
        pageDashboard, "manifest files match"))
    listTexts = _flistToastTexts(pageDashboard)
    assert any("All 3 manifest files match" in s for s in listTexts), (
        f"the check's own answer never arrived: {listTexts!r}"
    )
    assert not any(S_MANIFEST_NOTICE in s for s in listTexts), (
        "the notice still claims a check that has answered: "
        f"{listTexts!r}"
    )
    assert pageDashboard.listPageErrors == []


@pytest.mark.falsification
def test_the_level3_opener_says_it_is_checking_until_it_can_ask(
    pageDashboard, serverHub,
):
    """The readiness round trip is announced, and the dialog replaces it.

    Kills: never showing the readiness notice -- the Run menu closes on
    the click and seconds pass with nothing on screen.
    """
    fnOpenTheSeededHostWorkflow(pageDashboard, serverHub)
    listHeld = _flistHoldEveryRequest(
        pageDashboard, "**/api/workflow/**/level3/readiness")
    _fnChooseFromRunMenu(pageDashboard, "btnVerifyLevel3")
    _fnWaitUntil(pageDashboard, lambda: bool(listHeld))
    assert listHeld, "the readiness pre-flight never left the page"
    assert _fbAnyToastContains(pageDashboard, S_READINESS_NOTICE), (
        "nothing on screen says the readiness check started: "
        f"{_flistToastTexts(pageDashboard)!r}"
    )

    for route in listHeld:
        route.fulfill(status=200, content_type="application/json",
                      body=_fsReadyReadinessBody())
    pageDashboard.wait_for_selector("#modalConfirm", timeout=10000)
    assert not _fbAnyToastContains(pageDashboard, S_READINESS_NOTICE), (
        "the readiness notice outlived the answer, beside the dialog "
        "the answer opened"
    )
    pageDashboard.click("#btnConfirmCancel")
    assert pageDashboard.listPageErrors == []
