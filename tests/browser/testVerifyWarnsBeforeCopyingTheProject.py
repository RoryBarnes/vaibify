"""Starting a Level 3 verification must warn before it copies anything.

The reproduction is the one part of vaibify a researcher cannot watch:
it copies their project out of the container and re-runs the whole
workflow somewhere they have no window onto. Vaibify's premise is that
a researcher always knows what agents are doing, so the moment that
premise is hardest to keep is the moment to say so plainly — and to say
it BEFORE the copy, while acting on it is still cheap.

What makes the warning worth a browser test rather than a code review
is the ORDER. A warning that appears alongside a request already in
flight is decoration; the researcher clicks "Not now" and the copy
happens anyway. So the assertions here are about the request, not the
words: no POST before the confirm, a POST after it, and none at all
when the researcher declines.

The backend is the actual safety mechanism — it refuses a copy taken
while the repository moved. This is what turns a refusal the researcher
would find baffling into one they were expecting.
"""

import pytest

from tests.browser.conftest import fnOpenTheSeededHostWorkflow


pytestmark = pytest.mark.browser

S_MODAL = "#modalConfirm"


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


def _fnAnswerReadinessReady(pageDashboard):
    """Make the readiness pre-flight say this project is ready.

    Added 2026-08-30, when readiness began to be checked BEFORE the
    copy warning: an unready project now gets a gap list and no
    proceed control, so without this the seeded project never reaches
    the warning these tests are about.

    The body is built from the real ``fdictL3ReadinessGaps`` and
    wrapped in the route's real envelope key rather than hand-written.
    A hand-written flat payload is precisely what let the pre-flight
    ship reading the flags one level too high.
    """
    import json as jsonModule
    from vaibify.reproducibility.levelGates import fdictL3ReadinessGaps
    dictGaps = {
        sKey: (True if isinstance(objValue, bool) else objValue)
        for sKey, objValue in fdictL3ReadinessGaps(
            {}, "/nonexistent-repo-for-shape",
        ).items()
    }
    pageDashboard.route(
        "**/api/workflow/**/level3/readiness",
        lambda route: route.fulfill(
            status=200, content_type="application/json",
            body=jsonModule.dumps({
                "iProofLevel": 3, "dictL3ReadinessGaps": dictGaps,
            }),
        ),
    )


def _fnOpenTheConfirm(pageDashboard):
    """Start the REAL verify-l3 action and wait for its warning.

    Driven through ``fnRunProjectAction`` -- the dispatcher the Project
    block's button calls -- rather than through the confirm opener
    directly. Calling the opener with a hand-written callback would
    prove the modal renders and prove nothing about whether the ACTION
    is behind it, which is the entire question.
    """
    _fnAnswerReadinessReady(pageDashboard)
    pageDashboard.evaluate(
        "() => VaibifyApp.fnRunProjectAction('verify-l3', '', null)")
    pageDashboard.wait_for_selector(
        S_MODAL, state="visible", timeout=5000)


def test_the_warning_names_the_copy_and_what_to_check_first(
    pageDashboard, serverHub,
):
    """The researcher must learn two things: a copy happens, and to check.

    Asserted on meaning rather than exact wording — the phrasing will
    be edited, and a test pinned to a sentence would be rewritten
    rather than consulted. What must survive an edit is that the modal
    says a copy is being made and tells the researcher to make sure
    nothing is running.
    """
    fnOpenTheSeededHostWorkflow(pageDashboard, serverHub)
    _flistInterceptVerifyPosts(pageDashboard)
    _fnOpenTheConfirm(pageDashboard)
    sText = pageDashboard.inner_text(S_MODAL).lower()
    assert "copy" in sText, sText
    assert "writing inside" in sText or "nothing is writing" in sText, (
        f"the modal never tells the researcher what to check: {sText}"
    )
    # The equivalent command sits behind the modal's "Learn more"
    # disclosure, which is the right place for it -- it is how a
    # researcher learns this is the same operation they could run
    # themselves, and it is not what they need in the first two
    # seconds. Expanded here rather than asserted on hidden markup,
    # because "present in the DOM" and "reachable by a person" are
    # different claims and only the second one is worth anything.
    pageDashboard.click("#modalConfirm details.confirm-details summary")
    sExpanded = pageDashboard.inner_text(S_MODAL).lower()
    assert "vaibify reproduce --rerun" in sExpanded, sExpanded


def test_no_copy_starts_until_the_researcher_confirms(
    pageDashboard, serverHub,
):
    """The whole point: the warning precedes the request, not accompanies it.

    A modal shown while the POST is already in flight would satisfy the
    text assertion above and protect nobody. The empty list BEFORE the
    click is the assertion that matters; the non-empty one after it
    proves the confirm still works and the test is not vacuous.
    """
    fnOpenTheSeededHostWorkflow(pageDashboard, serverHub)
    listPosts = _flistInterceptVerifyPosts(pageDashboard)
    _fnOpenTheConfirm(pageDashboard)
    assert listPosts == [], (
        "the verification POST was already sent while the warning was "
        "still on screen"
    )
    pageDashboard.click("#btnConfirmOk")
    pageDashboard.wait_for_function(
        "() => document.getElementById('modalConfirm') === null",
        timeout=5000,
    )
    pageDashboard.wait_for_timeout(500)
    assert len(listPosts) == 1, (
        "confirming did not start the verification, so this test could "
        "not have detected a missing warning either"
    )


def test_declining_starts_nothing(pageDashboard, serverHub):
    """"Not now" must mean not now.

    The failure this guards is not hypothetical in a codebase where a
    confirm is bolted onto an existing handler: the cancel path is easy
    to wire to the same callback as the confirm path, and nothing else
    on screen would look different.
    """
    fnOpenTheSeededHostWorkflow(pageDashboard, serverHub)
    listPosts = _flistInterceptVerifyPosts(pageDashboard)
    _fnOpenTheConfirm(pageDashboard)
    pageDashboard.click("#btnConfirmCancel")
    pageDashboard.wait_for_function(
        "() => document.getElementById('modalConfirm') === null",
        timeout=5000,
    )
    pageDashboard.wait_for_timeout(500)
    assert listPosts == [], (
        "declining the warning started the copy anyway"
    )
