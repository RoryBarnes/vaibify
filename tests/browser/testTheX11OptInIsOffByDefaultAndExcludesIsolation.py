"""The X11 forwarding opt-in in the creation wizard and the settings dialog.

A green Python suite does not execute the frontend, so this drives the
real controls: the wizard's Runtime options must offer the opt-in OFF,
the opt-in and network isolation must exclude each other on the page
(the server and the launch refuse the pair as well), and the choice must
reach the wire. The settings dialog must show what the server reports and
send what the researcher chose. The settings endpoint is stubbed so the
journey exercises the frontend path, not the registry.

WHAT THIS DOES NOT COVER: that a container created with the opt-in has a
working display. That needs a real X server and daemon; it is exercised
by hand, and the Python suite covers the argument assembly.
"""

import json

import pytest

from tests.browser.conftest import S_HOST_PROJECT_READY
from tests.browser.fakeDockerAdapter import S_CONTAINER_NAME


pytestmark = pytest.mark.browser

S_SETTINGS_GLOB = "**/api/containers/*/settings"
S_TILE = f'.container-tile[data-name="{S_CONTAINER_NAME}"]'


def _fnOpenTheConvertWizard(page, serverHub):
    page.goto(serverHub.fsBootstrapUrl(), wait_until="load")
    page.wait_for_selector(
        f'.container-tile[data-name="{S_HOST_PROJECT_READY}"]', timeout=10000)
    page.click(
        f'.container-tile[data-name="{S_HOST_PROJECT_READY}"] '
        '.container-tile-actions')
    page.click(
        f'.container-tile[data-name="{S_HOST_PROJECT_READY}"] '
        '.container-menu-item[data-action="convert"]')
    page.wait_for_selector("#modalCreateWizard", timeout=5000)
    page.wait_for_timeout(200)


def _fnAdvanceToTheRuntimeOptions(page):
    """Click Next until the Runtime options page is on screen."""
    for _iStep in range(8):
        if page.query_selector("#wizardX11Forwarding"):
            return
        page.click("#btnWizardNext")
        page.wait_for_timeout(200)
        elModal = page.query_selector("#modalConfirm")
        if elModal and elModal.is_visible():
            page.click("#btnConfirmOk")
            page.wait_for_timeout(200)
    raise AssertionError("the wizard never showed the Runtime options")


def testTheWizardOffersX11OffAndExcludesItFromIsolation(
    pageDashboard, serverHub,
):
    _fnOpenTheConvertWizard(pageDashboard, serverHub)
    _fnAdvanceToTheRuntimeOptions(pageDashboard)
    assert not pageDashboard.is_checked("#wizardX11Forwarding")
    assert pageDashboard.is_enabled("#wizardNetworkIsolation")
    pageDashboard.check("#wizardX11Forwarding")
    assert pageDashboard.is_disabled("#wizardNetworkIsolation")
    pageDashboard.uncheck("#wizardX11Forwarding")
    pageDashboard.check("#wizardNetworkIsolation")
    assert pageDashboard.is_disabled("#wizardX11Forwarding")
    pageDashboard.uncheck("#wizardNetworkIsolation")
    assert pageDashboard.is_enabled("#wizardX11Forwarding")
    assert pageDashboard.listPageErrors == []


@pytest.mark.falsification
def testTheWizardRemembersTheChoiceWhenThePageIsLeft(
    pageDashboard, serverHub,
):
    """Kills: dropping the X11 choice when the page is left."""
    _fnOpenTheConvertWizard(pageDashboard, serverHub)
    _fnAdvanceToTheRuntimeOptions(pageDashboard)
    pageDashboard.check("#wizardX11Forwarding")
    pageDashboard.click("#btnWizardNext")
    pageDashboard.wait_for_timeout(300)
    elModal = pageDashboard.query_selector("#modalConfirm")
    if elModal and elModal.is_visible():
        pageDashboard.click("#btnConfirmOk")
        pageDashboard.wait_for_timeout(300)
    pageDashboard.click("#btnWizardBack")
    pageDashboard.wait_for_selector("#wizardX11Forwarding", timeout=5000)
    assert pageDashboard.is_checked("#wizardX11Forwarding")
    assert pageDashboard.is_disabled("#wizardNetworkIsolation")


def _fnStubSettings(page, listPostBodies, dictGet):
    def fnHandle(routeIntercepted):
        requestIntercepted = routeIntercepted.request
        if requestIntercepted.method == "POST":
            listPostBodies.append(requestIntercepted.post_data_json)
            sBody = json.dumps({"bSuccess": True, "bRestartRequired": True})
        else:
            sBody = json.dumps(dictGet)
        routeIntercepted.fulfill(
            status=200, content_type="application/json", body=sBody)

    page.route(S_SETTINGS_GLOB, fnHandle)


def _fnOpenTheSettingsDialog(page, serverHub):
    page.goto(serverHub.fsBootstrapUrl(), wait_until="load")
    page.wait_for_selector(S_TILE, timeout=10000)
    page.click(f"{S_TILE} .container-tile-gear")
    page.wait_for_selector("#settingX11Forwarding", timeout=5000)


@pytest.mark.falsification
def testTheSettingsDialogSendsTheChosenX11Value(pageDashboard, serverHub):
    """Kills: a settings save that never sends the X11 choice."""
    listPostBodies = []
    _fnStubSettings(pageDashboard, listPostBodies, {
        "bNeverSleep": False, "bX11Forwarding": False,
        "bNetworkIsolation": False, "iCpuLimit": 0,
        "fMemoryLimitGigabytes": 0, "dictImageTrust": None,
        "bImageObtained": False,
    })
    _fnOpenTheSettingsDialog(pageDashboard, serverHub)
    assert not pageDashboard.is_checked("#settingX11Forwarding")
    pageDashboard.check("#settingX11Forwarding")
    pageDashboard.click("#btnSettingsSave")
    pageDashboard.wait_for_timeout(500)
    assert len(listPostBodies) == 1
    assert listPostBodies[0]["bX11Forwarding"] is True
    assert pageDashboard.listPageErrors == []


def testTheSettingsDialogDisablesX11WhileNetworkIsolationIsOn(
    pageDashboard, serverHub,
):
    listPostBodies = []
    _fnStubSettings(pageDashboard, listPostBodies, {
        "bNeverSleep": False, "bX11Forwarding": False,
        "bNetworkIsolation": True, "iCpuLimit": 0,
        "fMemoryLimitGigabytes": 0, "dictImageTrust": None,
        "bImageObtained": False,
    })
    _fnOpenTheSettingsDialog(pageDashboard, serverHub)
    assert pageDashboard.is_disabled("#settingX11Forwarding")
    assert "Unavailable while network isolation is on" in (
        pageDashboard.text_content("#modalSettings"))
