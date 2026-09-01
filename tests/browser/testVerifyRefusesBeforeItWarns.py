"""An unready project is told what is missing, not warned about a copy.

Reported 2026-08-30. The researcher clicked Verify Level 3, read the
modal explaining that vaibify would copy their project out of its
container, accepted it — and got a toast refusing the whole thing for a
readiness check they believed was met.

Two failures in one sequence. The warning describes the risks of an
operation that was never going to start, so accepting it taught the
researcher that the warning means nothing. And the refusal arrived
after the decision instead of before it, naming no gap they could act
on.

So the readiness check now runs FIRST, and an unready project gets an
informational modal listing what is still to do — with no proceed
control at all, because a button offering to "Copy and verify" over a
list of reasons it cannot is a button that lies.

The non-blocking warnings are named too, and that is deliberate rather
than thorough: a step directory disagreeing with its name is the
loudest red thing on the screen and has nothing to do with whether the
project reproduces. Silence about it invites exactly the wrong
conclusion — which is the conclusion the researcher drew.
"""

import pytest

from tests.browser.conftest import fnOpenTheSeededHostWorkflow


pytestmark = pytest.mark.browser

S_CONFIRM_MODAL = "#modalConfirm"
S_INFO_MODAL = "#modalInfo"


@pytest.fixture(autouse=True)
def fixtureDropClaimsBetweenTests(serverHub):
    """Give every claim back after each test.

    The hub is module-scoped and the page is not, so a test that
    claims the project and stops leaves it owned by a lease nobody
    holds; the symptom is a locked tile intercepting the next click.
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
    """Record every L3 verify POST without letting one happen."""
    listPosts = []
    pageDashboard.route(
        "**/api/workflow/**/level3/verify",
        lambda route: (
            listPosts.append(route.request.url),
            route.fulfill(
                status=200, content_type="application/json",
                body='{"bAccepted": true, "sPhase": "starting"}',
            ),
        ),
    )
    return listPosts


def _fnAnswerReadinessWith(pageDashboard, bReady):
    """Serve a readiness payload SHAPED LIKE THE ROUTE'S.

    Built from the real ``fdictL3ReadinessGaps`` and wrapped in the
    real envelope key, rather than hand-written. A hand-written FLAT
    payload is what let this suite pass while the pre-flight read the
    flags one level too high and declared every project unready —
    the test and the code agreed with each other and neither was the
    product (reported 2026-08-30). Deriving the body from the
    function the route calls means a shape change breaks the test
    instead of hiding in it.
    """
    import json as jsonModule
    from vaibify.reproducibility.levelGates import fdictL3ReadinessGaps
    dictGaps = fdictL3ReadinessGaps({}, "/nonexistent-repo-for-shape")
    dictGaps = {
        sKey: (bReady if isinstance(objValue, bool) else objValue)
        for sKey, objValue in dictGaps.items()
    }
    sBody = jsonModule.dumps({
        "iProofLevel": 3 if bReady else 1,
        "dictL3ReadinessGaps": dictGaps,
    })
    pageDashboard.route(
        "**/api/workflow/**/level3/readiness",
        lambda route: route.fulfill(
            status=200, content_type="application/json", body=sBody,
        ),
    )


@pytest.mark.falsification
def test_an_unready_project_is_told_what_is_missing(
    pageDashboard, serverHub,
):
    """The gaps arrive BEFORE the decision, and nothing is posted.

    Kills: removing the readiness pre-flight from
    fnConfirmLevel3Verification, which puts the copy warning back in
    front of a researcher whose verification cannot start.
    """
    fnOpenTheSeededHostWorkflow(pageDashboard, serverHub)
    listPosts = _flistInterceptVerifyPosts(pageDashboard)
    # NOT intercepted: the seeded project is genuinely not L3-ready,
    # so the real hub answers and the real payload shape is exercised.
    # A faked answer here is what hid the nesting bug.
    pageDashboard.evaluate(
        "() => VaibifyApp.fnRunProjectAction('verify-l3', '', null)")
    pageDashboard.wait_for_selector(
        S_INFO_MODAL, state="visible", timeout=5000)

    sText = pageDashboard.inner_text(S_INFO_MODAL).lower()
    assert "repeatability rules" in sText, (
        f"the modal names no specific readiness gap: {sText}"
    )
    assert "copy" not in sText, (
        "the researcher is warned about a copy that cannot happen, "
        f"which is how the warning became noise: {sText}"
    )
    assert pageDashboard.query_selector(S_CONFIRM_MODAL) is None, (
        "the confirm modal opened alongside the refusal, so there is "
        "still a button offering to start what cannot start"
    )
    assert listPosts == [], (
        "an unready project POSTed the verification anyway"
    )


@pytest.mark.falsification
def test_a_ready_project_still_gets_the_copy_warning(
    pageDashboard, serverHub,
):
    """The pre-flight must not swallow the safety notice.

    The warning exists because the copy is the one part of vaibify a
    researcher cannot watch. A pre-flight that suppressed it would
    trade one failure for a worse one.

    Kills: making fnConfirmLevel3Verification return after the
    readiness fetch regardless of its verdict.
    """
    fnOpenTheSeededHostWorkflow(pageDashboard, serverHub)
    _flistInterceptVerifyPosts(pageDashboard)
    _fnAnswerReadinessWith(pageDashboard, True)

    pageDashboard.evaluate(
        "() => VaibifyApp.fnRunProjectAction('verify-l3', '', null)")
    pageDashboard.wait_for_selector(
        S_CONFIRM_MODAL, state="visible", timeout=5000)

    sText = pageDashboard.inner_text(S_CONFIRM_MODAL).lower()
    assert "copy" in sText, (
        f"a ready project lost its copy warning: {sText}"
    )

    assert pageDashboard.listPageErrors == []


@pytest.mark.falsification
def test_the_preflight_reads_the_gaps_from_inside_their_envelope(
    pageDashboard, serverHub,
):
    """The route answers {iProofLevel, dictL3ReadinessGaps}, not flags.

    Reading the flags off the OUTER object gave `undefined` for every
    one, so `!== true` held for all seven and a fully-ready project was
    told it was not ready — the L3 verification became unstartable
    from the dashboard. It shipped because this file's fake answered a
    FLAT payload nobody sends, so the test and the code agreed with
    each other and neither was the product (reported 2026-08-30).

    Separate from the copy-warning test beside it: that one pins that
    the safety notice survives the pre-flight, this one pins that the
    pre-flight reads the right level. Two guarantees, two mutations.

    Kills: returning the whole response from _fdictFetchL3Readiness
    instead of its dictL3ReadinessGaps.
    """
    fnOpenTheSeededHostWorkflow(pageDashboard, serverHub)
    _flistInterceptVerifyPosts(pageDashboard)
    _fnAnswerReadinessWith(pageDashboard, True)

    pageDashboard.evaluate(
        "() => VaibifyApp.fnRunProjectAction('verify-l3', '', null)")
    pageDashboard.wait_for_selector(
        S_CONFIRM_MODAL, state="visible", timeout=5000)
    assert pageDashboard.query_selector(S_INFO_MODAL) is None, (
        "a ready project was shown the not-ready gap list, so the "
        "pre-flight is reading the flags at the wrong level"
    )

    assert pageDashboard.listPageErrors == []


def test_the_unready_modal_says_which_red_marks_are_not_the_cause(
    pageDashboard, serverHub,
):
    """A researcher looking at a red glyph must learn it is not this.

    Skipped when the seeded project has no non-conforming step: the
    note is conditional by design, and asserting it unconditionally
    would pin a fixture detail rather than the behaviour.
    """
    fnOpenTheSeededHostWorkflow(pageDashboard, serverHub)
    _flistInterceptVerifyPosts(pageDashboard)
    _fnAnswerReadinessWith(pageDashboard, False)
    pageDashboard.evaluate(
        "() => VaibifyApp.fnRunProjectAction('verify-l3', '', null)")
    pageDashboard.wait_for_selector(
        S_INFO_MODAL, state="visible", timeout=5000)
    sText = pageDashboard.inner_text(S_INFO_MODAL)
    if "Not blocking this" not in sText:
        pytest.skip(
            "the seeded project has no step whose directory disagrees "
            "with its name, so the conditional note does not render"
        )
    assert "not a reproducibility one" in sText, (
        "the note names a non-blocking warning without saying it "
        f"cannot affect the verification: {sText}"
    )
