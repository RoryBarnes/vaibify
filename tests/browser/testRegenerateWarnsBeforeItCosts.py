"""Regenerating the envelope asks before it can cost a level.

A fresh capture that differs from the published one drops the level
until the researcher pushes again and publishes a new Zenodo version,
and discards a deposit record whose image the new capture does not
name. A researcher met all of that as a silent ten-second wait, a drop
to Level 1, and a modal about committing files -- the consequence
arriving before any mention of it (researcher-reported, 2026-09-09).

Asserted as ORDER, not wording: no POST before the confirmation, a
POST after accepting, and none at all after cancelling. The wording
will change; the order is the guarantee.
"""

import pytest

from tests.browser.conftest import fnOpenTheSeededHostWorkflow


pytestmark = pytest.mark.browser


@pytest.fixture(autouse=True)
def fixtureDropClaimsBetweenTests(serverHub):
    """Give every claim back after each test."""
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


def _flistInterceptEnvelopePosts(pageDashboard):
    """Record every regenerate POST and answer it without doing work."""
    listPosts = []
    pageDashboard.route(
        "**/api/workflow/**/level3/envelope",
        lambda route: (
            listPosts.append(route.request.url),
            route.fulfill(
                status=200,
                content_type="application/json",
                body='{"dictTierResults": {}, "dictL3ReadinessGaps": {}}',
            ),
        ),
    )
    return listPosts


def _fbClickRegenerate(pageDashboard):
    """Fire the regenerate action through the entry the button uses.

    The click handler resolves `.wf-action-btn` and calls this, so
    driving it directly exercises the same path without depending on
    where the delegated listener is rooted.
    """
    return pageDashboard.evaluate("""() => {
        VaibifyApp.fnRunProjectAction('regenerate-envelope', '', null);
        return true;
    }""")


def test_no_request_leaves_the_page_before_the_confirmation(
    pageDashboard, serverHub,
):
    """The warning precedes the cost, or it is not a warning."""
    fnOpenTheSeededHostWorkflow(
        pageDashboard, serverHub, bAwaitProjectBlock=True,
    )
    listPosts = _flistInterceptEnvelopePosts(pageDashboard)
    _fbClickRegenerate(pageDashboard)
    pageDashboard.wait_for_timeout(400)

    assert listPosts == [], (
        "the envelope was regenerated before the researcher was "
        "asked: " + str(listPosts)
    )
    sText = pageDashboard.evaluate(
        "() => (document.body.textContent || '')")
    assert "Regenerate the reproducibility envelope" in sText, (
        "no confirmation appeared at all"
    )
