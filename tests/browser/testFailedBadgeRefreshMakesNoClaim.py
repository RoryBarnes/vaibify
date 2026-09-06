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

import json

import pytest

from tests.browser.conftest import (
    fnOpenTheSeededHostWorkflow,
    S_HOST_PROJECT_READY,
)


pytestmark = pytest.mark.browser




def test_a_failed_refresh_claims_nothing_and_says_so(
    pageDashboard, serverHub,
):
    """One test, both halves — the session holds one browser."""
    fnOpenTheSeededHostWorkflow(pageDashboard, serverHub)

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

    # Seed the prior map THROUGH the real refresh path, from a canned
    # success. Waiting for the dashboard's own first refresh to fill
    # it is not deterministic: a busy hub answers that refresh with
    # bRefreshPaused and no map (observed in CI -- the map stayed
    # empty for 30 seconds, and on another run a request issued
    # before the 503 route existed landed between the two readings).
    # The success route stays registered underneath the 503 one, so a
    # natural refresh that resolves late carries the same three
    # entries and cannot move the count. Reaching in through
    # fnRefresh keeps this driving the real code path rather than a
    # hand-built state object.
    sSeededBadges = json.dumps({"dictBadges": {
        "Analysis/result.json": {
            "sGitState": "clean", "sGithub": "synced",
            "sOverleaf": "none", "sZenodo": "none", "sArxiv": "none",
        },
        "Analysis/figure.pdf": {
            "sGitState": "modified", "sGithub": "stale",
            "sOverleaf": "synced", "sZenodo": "none", "sArxiv": "none",
        },
        "Analysis/notes.md": {
            "sGitState": "untracked", "sGithub": "none",
            "sOverleaf": "none", "sZenodo": "none", "sArxiv": "none",
        },
    }})
    pageDashboard.route(
        "**/api/git/**/badges",
        lambda route: route.fulfill(
            status=200, content_type="application/json",
            body=sSeededBadges,
        ),
    )
    pageDashboard.evaluate(
        """async (sContainerId) => {
            await VaibifyGitBadges.fnRefresh(sContainerId);
        }""",
        S_HOST_PROJECT_READY,
    )
    pageDashboard.route(
        "**/api/git/**/badges",
        lambda route: route.fulfill(status=503, body="upstream down"),
    )
    iBeforeCount = pageDashboard.evaluate(
        """() => VaibifyGitBadges.flistFilesForRemote('sGitState')
            .length""",
    )
    assert iBeforeCount == 3, (
        f"the seeded map holds {iBeforeCount} entries, not 3: the "
        "refresh path did not apply the canned success, so the "
        "assertion below would compare nothing against nothing"
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
