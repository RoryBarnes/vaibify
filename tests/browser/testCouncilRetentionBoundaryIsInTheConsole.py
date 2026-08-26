"""A real evicted event ring puts its boundary in the log, not overhead.

The council's event console is bounded and rolls off. The dashboard has
to admit that — a console that silently skips events is exactly what the
"never misrepresent the dashboard" rule forbids — but until 2026-08-25
it admitted it as a BANNER above the tab bar, so a notice about missing
console lines rendered on the Council tab, the Plan tab and the chat
tab, none of which display a single event.

This drives the whole real path with a deliberately tiny ring: the
store evicts for real, the events route reports the retention boundary,
the frontend ingests it, and the marker has to appear inside the agent
log and nowhere else. Nothing is patched into the frontend — a test
hook that set the boundary directly would prove the renderer works and
say nothing about whether eviction ever reaches it.
"""

import shutil
import tempfile

import pytest

from vaibify.gui import agentCouncilStore

from .testCouncilPlanningJourney import (  # noqa: F401 — fixtures
    _fdictClaimAndActivate,
    _fnConveneThroughTheForm,
    _fnScriptedProviderSeam,
)
from .testBrowserJourneys import _fnReleaseBrowserLaneOwnership


pytestmark = pytest.mark.browser

# Small enough that an ordinary deliberation overruns it immediately.
I_TINY_EVENT_RING = 3


@pytest.fixture(autouse=True)
def _fnIsolateStoreWithATinyRing(serverHub):
    """Give the hub a council store whose event ring evicts at once."""
    sTempRoot = tempfile.mkdtemp(prefix="councilRetentionLane")
    dictStore = agentCouncilStore.fdictCreateCampaignStore(
        sDurableStoreRoot=sTempRoot,
        dictBounds={"iEventCountBound": I_TINY_EVENT_RING})
    setattr(serverHub.app.state,
            agentCouncilStore.S_COUNCIL_CAMPAIGN_STORE_STATE_KEY, dictStore)
    yield
    _fnReleaseBrowserLaneOwnership(serverHub.app.state)
    shutil.rmtree(sTempRoot, ignore_errors=True)


@pytest.mark.falsification
def testTheRetentionBoundaryAppearsInTheLogAndNowhereElse(
        pageDashboard, serverHub):
    """It marks the console's own boundary, on the console's own tab.

    Kills: dropping the marker, floating it above the tab bar, and
    rendering it outside the log element.
    """
    _fdictClaimAndActivate(pageDashboard, serverHub)
    _fnConveneThroughTheForm(pageDashboard)

    pageDashboard.click('.council-tab[data-tab^="participant:"]')
    pageDashboard.wait_for_selector(
        ".council-event-evicted", timeout=20000)

    # INSIDE the log, not floating above the tabs.
    assert pageDashboard.evaluate(
        """() => {
            const el = document.querySelector('.council-event-evicted');
            return Boolean(el && el.closest('.council-event-log'));
        }"""), "the retention marker is not inside the event log"
    sMarker = pageDashboard.inner_text(".council-event-evicted")
    assert "no longer retained" in sMarker
    assert "artifacts remain" in sMarker

    # And absent from every tab that shows no events at all — the whole
    # reason it moved.
    for sTab in ("council", "plan", "chat"):
        pageDashboard.click('.council-tab[data-tab="' + sTab + '"]')
        assert pageDashboard.query_selector(".council-event-evicted") is None, (
            f"the retention notice is showing on the {sTab} tab, which "
            "displays no console events")

    assert pageDashboard.listPageErrors == []
    assert pageDashboard.listConsoleErrors == []
