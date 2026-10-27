"""The convene form's maximum rounds reaches the council's record.

The backend always accepted ``iMaximumRounds`` (default 3) and bounded
the deliberation by it, but the form offered only the minimum, so a
researcher could raise the floor and never the ceiling. A source-level
check cannot show that the value leaves the page; this drives the real
form against the real hub store.
"""

import pytest

from .testBrowserJourneys import _fnReleaseBrowserLaneOwnership  # noqa: F401
from .testCouncilPlanningJourney import (  # noqa: F401 — fixture wiring
    _fdictClaimAndActivate,
    _fdictStore,
    _fnIsolateCouncilStore,
    _fnScriptedProviderSeam,
    _fsNewestCampaignId,
)
from vaibify.gui import agentCouncilStore

pytestmark = pytest.mark.browser


@pytest.mark.falsification
def testTheConveneFormSendsTheMaximumRounds(pageDashboard, serverHub):
    """Kills: a form that renders the field but never sends it."""
    _fdictClaimAndActivate(pageDashboard, serverHub)
    pageDashboard.click("#btnAgentCouncil")
    pageDashboard.wait_for_selector("#btnCouncilPlanChange", timeout=8000)
    pageDashboard.click("#btnCouncilPlanChange")
    pageDashboard.wait_for_selector("#councilQuestion", timeout=8000)
    assert pageDashboard.input_value("#councilMaximumRounds") == "3"
    pageDashboard.fill("#councilQuestion", "Replace the integrator")
    listOptions = pageDashboard.eval_on_selector(
        '.council-model[data-index="0"]',
        "el => Array.from(el.options).map(o => o.value).filter(v => v)")
    pageDashboard.select_option('.council-model[data-index="0"]',
                                listOptions[0])
    pageDashboard.select_option('.council-model[data-index="1"]',
                                listOptions[1])
    pageDashboard.fill("#councilMaximumRounds", "5")
    pageDashboard.click("#btnCouncilConvene")
    pageDashboard.wait_for_selector(
        "#agentCouncilWorkspaceBody .council-summary", timeout=8000)
    dictCampaign = agentCouncilStore.fjsonGetCampaignRecord(
        _fdictStore(serverHub), _fsNewestCampaignId(serverHub))
    assert dictCampaign["dictSettings"]["iMaximumRounds"] == 5
    assert pageDashboard.listPageErrors == []
    assert pageDashboard.listConsoleErrors == []


@pytest.mark.falsification
def testTheExecutionMenuSaysMembersMayEditAndRunTheirCopies(
        pageDashboard, serverHub):
    """The hint under the Execution permission menu is on the page.

    Kills: the convene form rendered without the hint.
    """
    _fdictClaimAndActivate(pageDashboard, serverHub)
    pageDashboard.click("#btnAgentCouncil")
    pageDashboard.wait_for_selector("#btnCouncilPlanChange", timeout=8000)
    pageDashboard.click("#btnCouncilPlanChange")
    pageDashboard.wait_for_selector("#councilExecutionHint", timeout=8000)
    assert pageDashboard.inner_text("#councilExecutionHint") == (
        "members may edit and run their own copies")
    assert pageDashboard.is_visible("#councilExecutionHint")
    assert pageDashboard.listPageErrors == []
    assert pageDashboard.listConsoleErrors == []
