"""The resource-limit help text never recommends the cap that killed a run.

The wizard once said "A minimal demo container runs comfortably at 1 CPU
and 1 GB", and a Claude Code session with subagents was killed by the
kernel at exactly that cap. Both places a researcher sets the limits --
the creation wizard and the settings dialog -- now carry one sentence
that says what a cap costs. This drives both real surfaces, because a
green Python suite never executes the frontend.

WHAT THIS DOES NOT COVER: whether a given cap is adequate for a given
project. That advice is computed by the server (the advisory sizing in
``resourceAdequacy``), never by this static text.
"""

import json

import pytest

from tests.browser.conftest import S_HOST_PROJECT_READY
from tests.browser.fakeDockerAdapter import S_CONTAINER_NAME


pytestmark = pytest.mark.browser

S_SETTINGS_GLOB = "**/api/containers/*/settings"
S_TILE = f'.container-tile[data-name="{S_CONTAINER_NAME}"]'
S_OLD_RECOMMENDATION = "1 CPU and 1 GB"
S_COST_OF_A_CAP = "AI agents in the container often need several GB"


def _fnOpenTheWizardAtTheResourceLimits(page, serverHub):
    page.goto(serverHub.fsBootstrapUrl(), wait_until="load")
    sTile = f'.container-tile[data-name="{S_HOST_PROJECT_READY}"]'
    page.wait_for_selector(sTile, timeout=10000)
    page.click(f"{sTile} .container-tile-actions")
    page.click(f'{sTile} .container-menu-item[data-action="convert"]')
    page.wait_for_selector("#modalCreateWizard", timeout=5000)
    for _iStep in range(8):
        if page.query_selector("#wizardMemoryLimit"):
            return
        page.click("#btnWizardNext")
        page.wait_for_timeout(200)
        elModal = page.query_selector("#modalConfirm")
        if elModal and elModal.is_visible():
            page.click("#btnConfirmOk")
            page.wait_for_timeout(200)
    raise AssertionError("the wizard never showed the resource limits")


def _fnOpenTheSettingsDialog(page, serverHub):
    dictSettings = {
        "bNeverSleep": False, "bX11Forwarding": False,
        "bNetworkIsolation": False, "iCpuLimit": 1,
        "fMemoryLimitGigabytes": 1.0, "dictImageTrust": None,
        "bImageObtained": False,
    }
    page.route(S_SETTINGS_GLOB, lambda routeIntercepted: (
        routeIntercepted.fulfill(
            status=200, content_type="application/json",
            body=json.dumps(dictSettings))))
    page.goto(serverHub.fsBootstrapUrl(), wait_until="load")
    page.wait_for_selector(S_TILE, timeout=10000)
    page.click(f"{S_TILE} .container-tile-gear")
    page.wait_for_selector("#settingMemoryLimit", timeout=5000)


@pytest.mark.falsification
def testTheWizardSaysWhatACapCosts(pageDashboard, serverHub):
    """Kills: restoring the wizard's 1 CPU and 1 GB recommendation."""
    _fnOpenTheWizardAtTheResourceLimits(pageDashboard, serverHub)
    sText = pageDashboard.text_content("#modalCreateWizard")
    assert S_OLD_RECOMMENDATION not in sText
    assert S_COST_OF_A_CAP in sText
    assert pageDashboard.listPageErrors == []


@pytest.mark.falsification
def testTheSettingsDialogSaysWhatACapCosts(pageDashboard, serverHub):
    """Kills: restoring the settings dialog's old help sentence."""
    _fnOpenTheSettingsDialog(pageDashboard, serverHub)
    sText = pageDashboard.text_content("#modalSettings")
    assert S_OLD_RECOMMENDATION not in sText
    assert S_COST_OF_A_CAP in sText
    assert pageDashboard.listPageErrors == []
