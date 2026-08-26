"""A verify repaints the badges it just made true.

The researcher-visible half of the sync-epoch hole (see
``tests/testSyncEpochReachesTheIdleDashboard.py`` for the payload
half). Clicking Verify now rewrote the cache correctly and left the
screen showing the old answer, because the epoch that signals "the
cache moved" rode a payload nobody polls outside a live run.

Two independent paths are asserted, and both matter:

1. The verify action refreshes the badges ITSELF, so the researcher
   who clicked does not watch a stale icon for a poll interval. This
   is the one that makes the button feel like it did something.
2. The epoch still rides the file-status payload, which is the
   GENERAL signal -- a verify can also come from the in-container
   agent's ``vaibify-do verify-remote`` or the scheduled sweep, and
   neither of those has a browser to repaint from.

Path 1 alone would leave the agent and sweep cases stale; path 2
alone would leave the click feeling dead for up to a poll interval.
Neither is redundant.

Kills (confirmed, not assumed): removing the fnRefresh call from
``fnVerifyRemoteFromDashboard`` fails the first assertion; removing
``iSyncEpoch`` from the file-status payload fails the second.
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


def test_a_verify_repaints_and_the_epoch_still_travels(
    pageDashboard, serverHub,
):
    """One test, both paths — one session may hold one container."""
    _fnOpenTheHostWorkflow(pageDashboard, serverHub)

    # Path 1. Stub the verify POST so no network is touched, and count
    # badge fetches across the call. The researcher's click must cause
    # one without waiting for any poll.
    pageDashboard.route(
        "**/api/sync/**/verify",
        lambda route: route.fulfill(
            status=200,
            content_type="application/json",
            body='{"sService": "github", "iTotalFiles": 19,'
                 ' "iMatching": 19, "listDiverged": [],'
                 ' "listComparedPaths": []}',
        ),
    )
    pageDashboard.evaluate(
        """() => {
            window._iBadgeFetches = 0;
            window._fnOriginalRefresh = VaibifyGitBadges.fnRefresh;
            VaibifyGitBadges.fnRefresh = function (sId) {
                window._iBadgeFetches += 1;
                return window._fnOriginalRefresh(sId);
            };
        }""",
    )
    pageDashboard.evaluate(
        """async () => {
            await VaibifySyncManager.fnVerifyRemoteFromDashboard(
                'github', null);
        }""",
    )
    iFetches = pageDashboard.evaluate("() => window._iBadgeFetches")
    assert iFetches >= 1, (
        "clicking Verify now did not refresh the badges it had just "
        "made true — the researcher watches a stale icon and cannot "
        "tell the action from a no-op"
    )

    # The toast must report the VERDICT, not the plumbing.
    sToast = pageDashboard.locator("#toastContainer").inner_text()
    assert "19" in sToast, (
        f"the completion toast does not say what was found: {sToast!r}"
    )

    # Path 2. The general signal: the epoch rides the payload that is
    # polled when nothing is running. Without it, a verify from the
    # in-container agent or the scheduled sweep never repaints.
    bHasEpoch = pageDashboard.evaluate(
        """async () => {
            const sId = VaibifyApp.fsGetContainerId();
            const d = await VaibifyApi.fdictGet(
                '/api/pipeline/' + sId + '/file-status?iWorkflowEpoch=-1');
            return typeof d.iSyncEpoch === 'number';
        }""",
    )
    assert bHasEpoch, (
        "the file-status payload carries no iSyncEpoch, so the only "
        "poll that runs outside a live run cannot observe a sync bump"
    )

    assert pageDashboard.listPageErrors == []
