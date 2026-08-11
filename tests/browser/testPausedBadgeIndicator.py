"""A paused git-badge refresh is visible, not merely non-destructive.

The badges pause themselves rather than queue behind live work — a
poll must never wait on a step run of unpredictable length. The
frontend already keeps the last known map instead of blanking it,
which stops the dashboard from claiming a repository has no remote
state when in truth nobody looked.

That is half of honest. The other half is saying so. Badges older than
the moment they are being read as, with nothing on screen to say it,
are the dashboard asserting a repository state it did not read; the
repository's own rule is that the GUI is the researcher's ground truth
and staleness is never hidden.

Both directions are driven here, because the failure that matters is
not only "the label never appears" — a label stuck on forever would
teach the researcher to ignore it, which is the same outcome by a
longer road.

STATED LIMIT: this lane never opens a workflow (its one container
renders as "not built", so clicking it starts a real build), and the
toolbar the badge lives on is hidden until one is open. So what is
asserted is the element's OWN display state, driven by the real
refresh through the real module, plus that it sits inside the toolbar
rather than somewhere nobody would look. On-screen visibility with a
workflow open is not proven here and is owed to the manual
walkthrough.
"""

import json

import pytest


pytestmark = pytest.mark.browser

S_BADGES_ROUTE_GLOB = "**/api/git/*/badges"


def _fnAnswerBadgesWith(page, dictBody):
    """Intercept the badge poll and answer it with a canned payload."""
    page.route(
        S_BADGES_ROUTE_GLOB,
        lambda routeIntercepted: routeIntercepted.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps(dictBody),
        ),
    )


def _fsBadgeDisplay(page):
    """Return the pause label's own inline display value."""
    return page.evaluate(
        "() => document.getElementById('refreshPausedBadge')"
        ".style.display",
    )


def _fnDriveOneBadgeRefresh(page, serverHub):
    """Load the dashboard and run one real badge refresh."""
    page.goto(serverHub.fsBootstrapUrl(), wait_until="load")
    page.wait_for_selector(".container-tile", timeout=10000)
    page.evaluate(
        "async () => { await VaibifyGitBadges.fnRefresh('any-resource'); }",
    )


def testAPausedRefreshSaysSoAndNamesWhatIsBusy(pageDashboard, serverHub):
    """The label appears, and carries the reason the server gave.

    Kills: rendering nothing on the paused branch, which leaves badges
    from an earlier minute on screen with no indication that they are
    not current.
    """
    _fnAnswerBadgesWith(pageDashboard, {
        "bRefreshPaused": True,
        "sPausedBy": "a pipeline run",
    })
    _fnDriveOneBadgeRefresh(pageDashboard, serverHub)
    assert _fsBadgeDisplay(pageDashboard) != "none", (
        "a paused refresh left the badges silently stale"
    )
    sTitle = pageDashboard.get_attribute("#refreshPausedBadge", "title")
    assert "a pipeline run" in sTitle, sTitle
    assert pageDashboard.evaluate(
        "() => !!document.getElementById('refreshPausedBadge')"
        ".closest('#toolbar')",
    ), "the pause label is not on the workflow toolbar"
    assert pageDashboard.listPageErrors == []


def testACompletedRefreshClearsTheLabel(pageDashboard, serverHub):
    """The other direction: the label goes when the reading is current.

    Kills: showing the pause unconditionally, which would leave a
    permanent staleness warning over badges that are perfectly fresh —
    and a warning that is always on is one nobody reads.
    """
    _fnAnswerBadgesWith(pageDashboard, {
        "bRefreshPaused": True,
        "sPausedBy": "a pipeline run",
    })
    _fnDriveOneBadgeRefresh(pageDashboard, serverHub)
    assert _fsBadgeDisplay(pageDashboard) != "none"

    pageDashboard.unroute(S_BADGES_ROUTE_GLOB)
    _fnAnswerBadgesWith(pageDashboard, {
        "dictBadges": {},
        "dictGit": {},
    })
    pageDashboard.evaluate(
        "async () => { await VaibifyGitBadges.fnRefresh('any-resource'); }",
    )
    assert _fsBadgeDisplay(pageDashboard) == "none", (
        "the pause label survived a refresh that actually completed"
    )
    assert pageDashboard.listPageErrors == []
