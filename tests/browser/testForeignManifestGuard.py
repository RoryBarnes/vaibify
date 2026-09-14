"""The dashboard asks before replacing the author's manifest, and a
manifest check names the manifest it read.

Two surfaces of one rule (a manifest another identity committed is
never rewritten in silence), driven through the real page against a
faked hub: Regenerate meets the server's refusal, shows the server's
own sentence, and retries with consent -- the ORDER is what matters,
never the wording; and "Check Files Against Manifest" reports a clean
count against a rewritten manifest as a warning rather than as "all
match".
"""

import json

import pytest

from tests.browser.conftest import fnOpenTheSeededHostWorkflow


pytestmark = pytest.mark.browser

S_CONFIRM_MODAL = "#modalConfirm"


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


def _flistRefuseThenAcceptRegenerate(pageDashboard):
    """The first POST is the server's refusal; the second, with consent, succeeds."""
    listPosts = []

    def fnEnvelope(route):
        dictBody = json.loads(route.request.post_data or "{}")
        listPosts.append(dictBody)
        if len(listPosts) == 1:
            route.fulfill(
                status=409, content_type="application/json",
                body=json.dumps({"detail": {
                    "sMessage": "MANIFEST.sha256 at HEAD was committed by "
                                "another identity. Replace it anyway?",
                    "sAction": "confirm-replace-foreign-manifest",
                }}),
            )
            return
        route.fulfill(
            status=200, content_type="application/json",
            body=json.dumps({
                "dictTierResults": {}, "dictManifestDelta": {},
                "dictL3ReadinessGaps": {},
            }),
        )
    pageDashboard.route("**/api/workflow/**/level3/envelope", fnEnvelope)
    return listPosts


def _fnClickConfirmWhenItShows(pageDashboard, sExpectedFragment):
    pageDashboard.wait_for_selector(S_CONFIRM_MODAL, state="visible", timeout=5000)
    sText = pageDashboard.inner_text(S_CONFIRM_MODAL)
    assert sExpectedFragment in sText, sText
    pageDashboard.click("#btnConfirmOk")


@pytest.mark.falsification
def test_regenerate_asks_in_the_servers_words_and_retries_with_consent(
    pageDashboard, serverHub,
):
    """Kills: reporting the refusal as a failure instead of offering the retry."""
    fnOpenTheSeededHostWorkflow(pageDashboard, serverHub)
    listPosts = _flistRefuseThenAcceptRegenerate(pageDashboard)
    pageDashboard.evaluate(
        "() => VaibifyApp.fnRunProjectAction('regenerate-envelope', '', null)")
    # The action's own pre-warning first, then the server's question.
    _fnClickConfirmWhenItShows(pageDashboard, "Re-capture")
    _fnClickConfirmWhenItShows(pageDashboard, "another identity")
    pageDashboard.wait_for_function(
        "() => document.querySelectorAll('#toastContainer .toast').length > 0",
        timeout=5000,
    )
    assert len(listPosts) == 2, listPosts
    assert listPosts[0].get("bReplaceForeignManifest") is not True
    assert listPosts[1].get("bReplaceForeignManifest") is True
    assert pageDashboard.listPageErrors == []


@pytest.mark.falsification
def test_a_clean_count_against_a_rewritten_manifest_is_a_warning(
    pageDashboard, serverHub,
):
    """Kills: reporting "all match" for a manifest this machine wrote."""
    fnOpenTheSeededHostWorkflow(pageDashboard, serverHub)
    pageDashboard.route(
        "**/api/workflow/**/manifest/verify",
        lambda route: route.fulfill(
            status=200, content_type="application/json",
            body=json.dumps({
                "iTotal": 24, "iMatching": 24, "listMismatches": [],
                "saIncomplete": [], "sManifestOwnership": "foreign",
                "bManifestDiffersFromHead": True,
            }),
        ),
    )
    pageDashboard.evaluate(
        "() => VaibifyApp.fnRunProjectAction('verify-manifest', '', null)")
    pageDashboard.wait_for_selector(
        "#toastContainer .toast.warning", state="visible", timeout=5000,
    )
    sToast = pageDashboard.text_content("#toastContainer .toast.warning")
    assert "differs from the committed one" in sToast, sToast
    assert "git diff HEAD -- MANIFEST.sha256" in sToast, sToast
    assert pageDashboard.listPageErrors == []
