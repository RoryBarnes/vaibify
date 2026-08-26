"""An unproven badge's menu offers the comparison that resolves it.

Clicking a remote badge opens a per-remote picklist. arXiv's has led
with "Verify now" since it was written -- arXiv is pull-only, so
verifying IS its primary action. GitHub's offered Sync now / View on
GitHub / Refresh from GitHub / Edit settings, Zenodo's Archive now,
Overleaf's Push. None of those re-runs the content comparison the
badge reports.

That was survivable while the GitHub badge showed local git state.
Once it became "was this compared against the published copy"
(2026-08-25), an orange badge opened a menu of four items that could
not turn it blue, and the only control that could was on a different
panel -- which a researcher found by asking.

Position tracks state: leading on unknown/drifted, last on a badge
already showing a verified match. Never omitted, because re-verifying
is how a researcher confirms a badge is current rather than cached.

Kills (confirmed, not assumed): dropping the verify item entirely
fails the presence assertion; making its position fixed fails the
ordering assertion for one state or the other.
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


S_LABELS_FOR_STATE = """([sRemoteKey, sState]) => {
    const el = document.createElement('span');
    el.className = 'remote-badge badge-' + sState;
    document.body.appendChild(el);
    VaibifySyncManager.fnOpenRemotePicklistForBadge(
        el, sRemoteKey, 'Step/out.json', '');
    const listLabels = Array.from(document.querySelectorAll(
        '#remotePicklistMenu .picklist-items *')).map(
            n => (n.textContent || '').trim()).filter(s => s.length);
    VaibifySyncManager.fnDismissAllPicklists();
    el.remove();
    return listLabels;
}"""


def _flistMenuLabels(pageDashboard, sRemoteKey, sState):
    return pageDashboard.evaluate(
        S_LABELS_FOR_STATE, [sRemoteKey, sState],
    )


def test_every_badge_menu_can_resolve_the_badge(
    pageDashboard, serverHub,
):
    """One test, every case — one session may hold one container.

    Split into a test per remote this would read better and run not at
    all: the second test's claim is refused with "In use in another
    browser session", which is the model working as designed.
    """
    _fnOpenTheHostWorkflow(pageDashboard, serverHub)

    # All three, because the gap was in the shared builder; asserting
    # only GitHub would pass against a fix that special-cased the
    # remote that happened to get reported.
    for sRemoteKey in ("sGithub", "sZenodo", "sOverleaf"):
        for sState in ("unknown", "drifted"):
            listLabels = _flistMenuLabels(
                pageDashboard, sRemoteKey, sState,
            )
            assert any("Verify now" in s for s in listLabels), (
                f"{sRemoteKey} in state {sState!r} opens a menu with "
                f"no way to run the comparison the badge reports: "
                f"{listLabels}"
            )
            assert "Verify now" in listLabels[0], (
                f"{sRemoteKey} in state {sState!r} must LEAD with the "
                f"verify — it is the whole question the badge asks: "
                f"{listLabels}"
            )

    # Present but demoted on a badge already showing a verified match.
    # Re-verifying is how a researcher confirms a badge is current
    # rather than merely cached, so it must stay reachable.
    listSynced = _flistMenuLabels(pageDashboard, "sGithub", "synced")
    assert any("Verify now" in s for s in listSynced), listSynced
    assert "Verify now" not in listSynced[0], (
        f"a verified badge should not lead with re-verifying it: "
        f"{listSynced}"
    )
    assert "Sync now" in listSynced[0], (
        f"the original primary action lost its place: {listSynced}"
    )

    # arXiv builds its list in a different branch and has led with
    # Verify now all along; an unconditional addition would duplicate
    # it there.
    listArxiv = _flistMenuLabels(pageDashboard, "sArxiv", "unknown")
    iCount = len([s for s in listArxiv if "Verify now" in s])
    assert iCount <= 1, (
        f"arXiv's menu has {iCount} verify entries: {listArxiv}"
    )

    assert pageDashboard.listPageErrors == []
