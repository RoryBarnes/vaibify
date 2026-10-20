"""Check Files Against Manifest shows every differing file and an honest remedy.

The payload is the REAL backend's answer for the new-machine shape (a
JSON value moved in its 13th significant digit, a derived binary moved
with it, no provenance records), built by the route's own compose
function and served to the real page. The page must count, offer the
report, and lay out the server's sentences -- never its own remedy.
The old toast named one file and advised "Re-run the project or
regenerate the envelope", and the PROOF tab's button reached a second,
drifted copy of the verdict.
"""

import json

import pytest

from tests.browser.conftest import fnOpenTheSeededHostWorkflow
from tests.testManifestCheckReport import (
    S_DERIVED,
    S_JSON,
    _fdictCheckFilesAgainstManifest,
    _fdictPublishOnTheFirstComputer,
    _fnRegenerateOnTheSecondComputer,
)


pytestmark = pytest.mark.browser

S_TOAST = "#toastContainer .toast.warning"


@pytest.fixture(autouse=True)
def fixtureDropClaimsBetweenTests(serverHub):
    """Give every claim back after each test (the hub is module-scoped)."""
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


def _fnServeTheSecondComputersCheck(pageDashboard, tmp_path):
    sRoot = str(tmp_path / "repo")
    dictWorkflow = _fdictPublishOnTheFirstComputer(sRoot)
    _fnRegenerateOnTheSecondComputer(sRoot)
    dictResult = _fdictCheckFilesAgainstManifest(sRoot, dictWorkflow)
    pageDashboard.route(
        "**/api/workflow/**/manifest/verify",
        lambda route: route.fulfill(
            status=200, content_type="application/json",
            body=json.dumps(dictResult)))
    return dictResult


@pytest.mark.falsification
def testTheToastCountsAndTheReportListsEveryFile(
        pageDashboard, serverHub, tmp_path):
    """Kills: restoring the one-file "Re-run the project" advice."""
    fnOpenTheSeededHostWorkflow(pageDashboard, serverHub)
    dictResult = _fnServeTheSecondComputersCheck(pageDashboard, tmp_path)
    pageDashboard.evaluate(
        "() => VaibifyApp.fnRunProjectAction('verify-manifest', '', null)")
    pageDashboard.wait_for_selector(S_TOAST, state="visible", timeout=5000)
    sToast = pageDashboard.text_content(S_TOAST)
    assert "2 of " in sToast and "files differ from the manifest" in sToast
    assert "Re-run" not in sToast and "regenerate" not in sToast, sToast
    pageDashboard.click(S_TOAST)
    pageDashboard.wait_for_selector("#modalInfo", state="visible",
                                    timeout=5000)
    sReport = pageDashboard.inner_text("#modalInfo")
    for sPath in (S_JSON, S_DERIVED):
        assert sPath + ": production context unknown" in sReport, sReport
    dictReport = dictResult["dictMismatchReport"]
    assert dictReport["sScope"] in sReport
    assert dictReport["sRegenerateChoice"] in sReport
    assert pageDashboard.listPageErrors == []


def testTheProofTabsButtonReachesTheSameVerdict(
        pageDashboard, serverHub, tmp_path):
    fnOpenTheSeededHostWorkflow(pageDashboard, serverHub)
    _fnServeTheSecondComputersCheck(pageDashboard, tmp_path)
    pageDashboard.evaluate(
        "() => document.getElementById("
        "'btnVerifyReproducibilityManifest').click()")
    pageDashboard.wait_for_selector(S_TOAST, state="visible", timeout=5000)
    sToast = pageDashboard.text_content(S_TOAST)
    assert "files differ from the manifest" in sToast, sToast
    assert "Manifest mismatch" not in sToast, sToast
    assert pageDashboard.listPageErrors == []


def testAToastThatOffersAnActionStaysUntilTaken(pageDashboard, serverHub):
    fnOpenTheSeededHostWorkflow(pageDashboard, serverHub)
    pageDashboard.evaluate(
        "() => VaibifyApp.fnShowToast('offer', 'info', function () {})")
    pageDashboard.wait_for_timeout(4600)
    assert pageDashboard.locator(
        "#toastContainer .toast.toast-clickable").count() == 1
