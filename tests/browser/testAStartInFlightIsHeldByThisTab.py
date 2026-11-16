"""A container this tab is starting reads as held by this tab, never as another session's.

A start reserves the container under the tab's browser session and
mints its lease at once, but the page learns the lease only when the
start succeeds. For the minutes a long start takes (an entrypoint
cloning repositories), every listing used to compare leases alone,
grey the tile and answer a click with "In use in another browser
session" (researcher-reported, 2026-11-05). The server now attributes a
record bound to the caller's own browser session to the caller, so the
tile wears the held chip and stays clickable. Driven on the real page
against the real hub, with an owner record bound to the page's own
session and a lease the page has never seen.
"""

import pytest

from tests.browser.conftest import (
    S_CONTAINER_NAME, S_STORED_CREDENTIAL_SCRIPT,
)


pytestmark = pytest.mark.browser

S_TILE = f'.container-tile[data-name="{S_CONTAINER_NAME}"]'
S_HELD_CHIP = f"{S_TILE} .containment-chip--held"


@pytest.fixture
def fixtureDropTheOwnerRecord(serverHub):
    yield
    serverHub.app.state.dictContainerOwners.pop(S_CONTAINER_NAME, None)


@pytest.mark.falsification
def testATileThisTabIsStartingWearsTheHeldChipAndIsNotLocked(
    pageDashboard, serverHub, fixtureDropTheOwnerRecord,
):
    """Kills: the listing attributing a held record by its lease only."""
    from vaibify.gui import browserSession, containerOwnership
    pageDashboard.goto(serverHub.fsBootstrapUrl(), wait_until="load")
    pageDashboard.wait_for_selector(S_TILE, timeout=15000)
    sCredential = pageDashboard.evaluate(S_STORED_CREDENTIAL_SCRIPT)
    sSessionId = browserSession.fsSessionIdForCredential(
        serverHub.app.state.dictBrowserSessions, sCredential)
    assert sSessionId
    serverHub.app.state.dictContainerOwners[S_CONTAINER_NAME] = (
        containerOwnership.OwnerRecord(
            sLeaseId="lease-minted-at-reservation", fileHandleLock=None,
            sBrowserSessionId=sSessionId))
    pageDashboard.evaluate("() => VaibifyContainerManager.fnLoadContainers()")
    pageDashboard.wait_for_selector(S_HELD_CHIP, timeout=10000)
    assert pageDashboard.locator(f"{S_TILE}.container-tile--locked").count() == 0
    assert pageDashboard.locator(S_TILE).get_attribute("data-locked") is None
    assert pageDashboard.listPageErrors == []
