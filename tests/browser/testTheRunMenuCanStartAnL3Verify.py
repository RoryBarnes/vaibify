"""The Level 3 verify is reachable from the Run menu, and it refuses aloud.

``verify-l3-reproducibility`` is not adjacent to the reproducibility
ladder — the action catalog calls it "the only L3 promotion path", and
it writes ``.vaibify/l3_attestation.json``. PROOF is the right home for
the resulting STATE and the wrong home for the BUTTON: a deliberate,
multi-hour job a researcher chooses to start belongs beside the other
things they start, and a researcher reported it as buried
(ruled 2026-09-05).

Two things make this worth a browser test rather than a diff read.

The entry has to be reachable BY A PERSON. "Present in the DOM" is a
different claim from "a researcher can click it", and this repository
has already shipped a control that half-worked because a programmatic
``.click()`` fires on a ``display:none`` element. So the menu is opened
the way a researcher opens it and the item is clicked, not evaluated.

And it must never be a grey control that explains nothing. The
constraint on this entry was explicit: a disabled entry must NAME ITS
CAUSE, because a refusal that points at a tab instead of naming what is
wrong is the exact defect the determinism row shipped. The entry is
therefore always enabled and routes through the shared opener, which
answers an unready project with the checklist. A regression to a
``disabled`` attribute would recreate the burial one layer down, so
that is asserted directly.
"""

import json as jsonModule

import pytest

from tests.browser.conftest import fnOpenTheSeededHostWorkflow


pytestmark = pytest.mark.browser

S_MENU_ITEM = "#btnVerifyLevel3"
S_RUN_MENU_TRIGGER = "#toolbarMenuRun .toolbar-menu-trigger"


@pytest.fixture(autouse=True)
def fixtureDropClaimsBetweenTests(serverHub):
    """Give every claim back after each test.

    The hub is module-scoped and the page is not, so a test that claims
    the project and stops leaves it owned by a lease nobody holds, and
    the next test's claim is refused by a session that no longer
    exists. The symptom is not a 409 but a locked tile intercepting the
    click, which reads like a UI bug in the feature under test.
    """
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


def _flistInterceptVerifyPosts(pageDashboard):
    """Record every L3 verify POST and answer it without doing the work."""
    listPosts = []
    pageDashboard.route(
        "**/api/workflow/**/level3/verify",
        lambda route: (
            listPosts.append(route.request.url),
            route.fulfill(
                status=200,
                content_type="application/json",
                body='{"bStarted": true, "sPhase": "starting"}',
            ),
        ),
    )
    return listPosts


def _fnAnswerReadiness(pageDashboard, bReady):
    """Make the readiness pre-flight answer ready, or not ready.

    The gap dict is built from the real ``fdictL3ReadinessGaps`` and
    wrapped in the route's real envelope key rather than hand-written.
    A hand-written flat payload is what let the pre-flight ship reading
    the flags one level too high.
    """
    from vaibify.reproducibility.levelGates import fdictL3ReadinessGaps
    dictGaps = {
        sKey: (bReady if isinstance(objValue, bool) else objValue)
        for sKey, objValue in fdictL3ReadinessGaps(
            {}, "/nonexistent-repo-for-shape",
        ).items()
    }
    pageDashboard.route(
        "**/api/workflow/**/level3/readiness",
        lambda route: route.fulfill(
            status=200, content_type="application/json",
            body=jsonModule.dumps({
                "iProofLevel": 3 if bReady else 1,
                "dictL3ReadinessGaps": dictGaps,
            }),
        ),
    )


def _fnClickTheMenuEntry(pageDashboard):
    """Open the Run menu and click the entry, the way a researcher does."""
    pageDashboard.click(S_RUN_MENU_TRIGGER)
    pageDashboard.wait_for_selector(S_MENU_ITEM, state="visible",
                                    timeout=5000)
    pageDashboard.click(S_MENU_ITEM)


def test_the_entry_is_visible_and_enabled_in_the_run_menu(
    pageDashboard, serverHub,
):
    """A grey entry naming no cause would recreate the burial.

    ``disabled`` is asserted directly rather than inferred from
    behaviour: the tempting regression is to grey the entry when the
    project is not ready, which looks like care and tells the
    researcher nothing.
    """
    fnOpenTheSeededHostWorkflow(pageDashboard, serverHub)
    pageDashboard.click(S_RUN_MENU_TRIGGER)
    pageDashboard.wait_for_selector(S_MENU_ITEM, state="visible",
                                    timeout=5000)
    assert pageDashboard.is_visible(S_MENU_ITEM)
    bDisabled = pageDashboard.evaluate(
        "() => { const el = document.querySelector('"
        + S_MENU_ITEM + "'); return el.hasAttribute('disabled') "
        "|| el.classList.contains('disabled'); }"
    )
    assert bDisabled is False


def test_the_entry_starts_the_real_verify_after_the_warning(
    pageDashboard, serverHub,
):
    """It must reach the ACTION, not merely open a modal.

    A second entry point wired to its own hand-written handler would
    satisfy "a modal appeared" and drift from the Project block's and
    the PROOF tab's copies within a release. The evidence that it is
    the shared action is that the copy warning appears and confirming
    it sends the POST.
    """
    fnOpenTheSeededHostWorkflow(pageDashboard, serverHub)
    listPosts = _flistInterceptVerifyPosts(pageDashboard)
    _fnAnswerReadiness(pageDashboard, True)
    _fnClickTheMenuEntry(pageDashboard)
    pageDashboard.wait_for_selector("#modalConfirm", state="visible",
                                    timeout=10000)
    assert listPosts == [], (
        "the verification POST was sent before the researcher confirmed"
    )
    pageDashboard.click("#btnConfirmOk")
    pageDashboard.wait_for_function(
        "() => document.getElementById('modalConfirm') === null",
        timeout=5000,
    )
    pageDashboard.wait_for_timeout(500)
    assert len(listPosts) == 1


def test_an_unready_project_is_told_what_is_missing(
    pageDashboard, serverHub,
):
    """The refusal NAMES its cause instead of pointing at a tab.

    This is the whole reason the entry is not greyed. The researcher
    who reported the burial would have been no better served by a grey
    line they could not interrogate, and a refusal that says "see the
    PROOF tab" is the defect the determinism row already shipped once.
    """
    fnOpenTheSeededHostWorkflow(pageDashboard, serverHub)
    listPosts = _flistInterceptVerifyPosts(pageDashboard)
    _fnAnswerReadiness(pageDashboard, False)
    _fnClickTheMenuEntry(pageDashboard)
    pageDashboard.wait_for_selector(".l3-pending-list", state="visible",
                                    timeout=10000)
    sText = pageDashboard.inner_text("body").lower()
    assert "not ready to verify yet" in sText, sText
    iPending = pageDashboard.eval_on_selector_all(
        ".l3-pending-list li", "listItems => listItems.length",
    )
    assert iPending > 0, (
        "the refusal listed no reason, so it names no cause"
    )
    assert listPosts == [], (
        "an unready project started the rerun anyway"
    )
