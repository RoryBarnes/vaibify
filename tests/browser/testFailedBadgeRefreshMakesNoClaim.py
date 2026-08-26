"""A failed badge refresh must not state that nothing is synced.

``fnRefresh``'s ``.catch`` used to do exactly two things: hide the
pause label, and set ``dictBadges = {}``. An empty map means every
file falls through to the placeholder, which returned ``none`` --
whose tooltip reads "not synced to this remote". So one failed HTTP
request repainted the whole dashboard with a confident negative about
files it had never examined, and nothing anywhere said a request had
failed.

The pause path three lines above got this right, with a comment
saying so: "Applying it would replace every badge with 'none' and
report that as fact. The last known map stands." The failure path
never got the same treatment, and a researcher whose project was
fully in sync spent a session unable to work out why every icon was
dark.

Two halves, and the second is the one that matters. Keeping the last
map only helps when there IS one; the case that bit the researcher is
a failure on the FIRST refresh after a hub restart, when there is
nothing to keep. That is why the placeholder had to change too:
"no entry for this file" now reads ``unknown``, not ``none``.

Kills (confirmed, not assumed): restoring ``dictBadges = {}`` in the
catch fails the last-map assertion; restoring ``none`` in
``_fdictPlaceholderBadges`` fails the no-prior-map assertion.
"""

import pytest

from tests.browser.conftest import (
    S_HOST_PROJECT_READY,
    S_HOST_STEP_NAME,
    S_HOST_WORKFLOW_NAME,
)


pytestmark = pytest.mark.browser


def _fnOpenTheHostWorkflow(pageDashboard, serverHub):
    pageDashboard.goto(serverHub.fsBootstrapUrl(), wait_until="load")
    pageDashboard.wait_for_selector(
        f'.container-tile[data-name="{S_HOST_PROJECT_READY}"]',
        timeout=15000,
    )
    pageDashboard.click(
        f'.container-tile[data-name="{S_HOST_PROJECT_READY}"] '
        '.container-tile-main',
    )
    pageDashboard.wait_for_selector("#modalConfirm", timeout=10000)
    pageDashboard.click("#btnConfirmOk")
    pageDashboard.wait_for_selector(
        f"text={S_HOST_WORKFLOW_NAME}", timeout=20000,
    )
    pageDashboard.click(f"text={S_HOST_WORKFLOW_NAME}")
    pageDashboard.wait_for_selector(
        f"text={S_HOST_STEP_NAME}", timeout=20000,
    )


def test_a_failed_refresh_claims_nothing_and_says_so(
    pageDashboard, serverHub,
):
    """One test, both halves — the session holds one browser."""
    _fnOpenTheHostWorkflow(pageDashboard, serverHub)

    # Half 1: with no prior map, an unseen file must read unknown.
    # This is the researcher's case: first refresh after a restart.
    sState = pageDashboard.evaluate(
        """() => VaibifyGitBadges.fdictGetBadgesForFile(
            'NoSuchStep/never-seen.json', '').sGithub""",
    )
    assert sState == "unknown", (
        "a file the badge map has no entry for reported "
        f"{sState!r}; 'none' renders as 'not synced to this remote', "
        "which is a claim about a file nothing has examined"
    )

    # Seed a known map, then fail the next refresh and prove the map
    # survived. Reaching in through fnRefresh keeps this driving the
    # real code path rather than a hand-built state object.
    pageDashboard.route(
        "**/api/git/**/badges",
        lambda route: route.fulfill(status=503, body="upstream down"),
    )
    iBeforeCount = pageDashboard.evaluate(
        """() => VaibifyGitBadges.flistFilesForRemote('sGitState')
            .length""",
    )
    pageDashboard.evaluate(
        """async (sContainerId) => {
            await VaibifyGitBadges.fnRefresh(sContainerId);
        }""",
        S_HOST_PROJECT_READY,
    )
    iAfterCount = pageDashboard.evaluate(
        """() => VaibifyGitBadges.flistFilesForRemote('sGitState')
            .length""",
    )
    assert iAfterCount == iBeforeCount, (
        f"a failed refresh changed the badge map from {iBeforeCount} "
        f"entries to {iAfterCount}: the last known reading must stand, "
        "exactly as it does for a paused refresh"
    )

    # Half 2: the failure is visible. A quiet wrong answer is the
    # defect; a quiet right answer is still half the defect.
    sTitle = pageDashboard.evaluate(
        """() => {
            const el = document.getElementById('refreshPausedBadge');
            if (!el) return '(absent)';
            return getComputedStyle(el).display === 'none'
                ? '(hidden)' : (el.title || '');
        }""",
    )
    assert "could not be refreshed" in sTitle, (
        f"nothing on screen says the refresh failed: {sTitle!r}"
    )
    assert "not a problem with your files" in sTitle, (
        "the notice must not read as a problem with the researcher's "
        f"repository: {sTitle!r}"
    )

    assert pageDashboard.listPageErrors == []
