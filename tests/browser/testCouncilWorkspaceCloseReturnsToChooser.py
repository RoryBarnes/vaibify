"""Closing a dead council's workspace must lead back to the chooser.

Until 2026-08-27 the toolbar click focused ANY previously opened
campaign forever and the workspace close merely hid the panel, so a
researcher who opened a failed council was trapped: every click on
Agent Council reopened the same dead campaign, and the chooser — the
only path to "Plan a change" — was unreachable. Only a real browser
proves the loop is actually broken: the routing, the close handler,
and the chooser render are three cooperating pieces of frontend state.
"""

import shutil
import tempfile

import pytest

from vaibify.gui import agentCouncilStore

from .testBrowserJourneys import _fnReleaseBrowserLaneOwnership
from .testCouncilPlanningJourney import (  # noqa: F401 — fixture wiring
    _fdictClaimAndActivate,
    _fnScriptedProviderSeam,
)
from .testCouncilRetryOffer import _fdictBuildInterruptedRetryableCampaign

pytestmark = pytest.mark.browser


@pytest.fixture(autouse=True)
def _fnIsolateStoreWithADeadCouncil(serverHub):
    """Give the hub a store holding one non-live (interrupted) council."""
    sTempRoot = tempfile.mkdtemp(prefix="councilCloseChooserLane")
    dictStore = agentCouncilStore.fdictCreateCampaignStore(
        sDurableStoreRoot=sTempRoot)
    agentCouncilStore.fdictRegisterStartedCampaign(
        dictStore, _fdictBuildInterruptedRetryableCampaign())
    setattr(serverHub.app.state,
            agentCouncilStore.S_COUNCIL_CAMPAIGN_STORE_STATE_KEY, dictStore)
    yield
    _fnReleaseBrowserLaneOwnership(serverHub.app.state)
    shutil.rmtree(sTempRoot, ignore_errors=True)


def testClosingADeadCouncilsWorkspaceReturnsToTheChooser(
        pageDashboard, serverHub):
    """Open a dead council, close it, and reach the chooser both ways.

    Kills: the 2026-08-27 trap — workspace close that only hides, plus
    a toolbar click that refocuses a campaign that is neither live nor
    waiting on the researcher.
    """
    _fdictClaimAndActivate(pageDashboard, serverHub)
    pageDashboard.click("#btnAgentCouncil")
    pageDashboard.wait_for_selector("#btnCouncilPlanChange", timeout=8000)
    pageDashboard.wait_for_selector(".council-open-row", timeout=8000)
    pageDashboard.click(".council-open-row")
    pageDashboard.wait_for_selector(
        "#agentCouncilWorkspace", state="visible", timeout=16000)

    pageDashboard.click("#btnAgentCouncilWorkspaceClose")
    pageDashboard.wait_for_selector(
        "#agentCouncilWorkspace", state="hidden", timeout=8000)
    pageDashboard.wait_for_selector("#btnCouncilPlanChange", timeout=8000)

    pageDashboard.click("#btnAgentCouncilModalClose")
    pageDashboard.wait_for_selector(
        "#agentCouncilModal", state="hidden", timeout=8000)
    pageDashboard.click("#btnAgentCouncil")
    pageDashboard.wait_for_selector("#btnCouncilPlanChange", timeout=8000)
    bWorkspaceHidden = pageDashboard.is_hidden("#agentCouncilWorkspace")
    assert bWorkspaceHidden

    assert pageDashboard.listPageErrors == []
    assert pageDashboard.listConsoleErrors == []
