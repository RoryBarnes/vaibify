"""The X11 opt-in and the network mode in the creation wizard and the settings dialog.

A green Python suite does not execute the frontend, so this drives the
real controls: the wizard's Runtime options must offer the opt-in OFF
and the network mode as ALLOWLIST (ruling D2), the opt-in and a sealing
mode must exclude each other on the page (ruling D4; the server and the
launch refuse the pair as well), and both choices must reach the wire.
The settings dialog must show what the server reports and send what the
researcher chose. The settings endpoint is stubbed so the journey
exercises the frontend path, not the registry.

WHAT THIS DOES NOT COVER: that a container created with the opt-in has a
working display, or that a sealed container is sealed. Those need a real
X server and daemon; the Python suite and the live tests cover them.
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


@pytest.mark.falsification
def testTheWizardOffersX11OffAndAllowlistAndExcludesThePair(
    pageDashboard, serverHub,
):
    """Kills: a wizard whose new project is not sealed by default (ruling D2)."""
    _fnOpenTheConvertWizard(pageDashboard, serverHub)
    _fnAdvanceToTheRuntimeOptions(pageDashboard)
    assert not pageDashboard.is_checked("#wizardX11Forwarding")
    assert pageDashboard.input_value("#wizardNetworkMode") == "allowlist"
    assert pageDashboard.is_disabled("#wizardX11Forwarding")
    pageDashboard.select_option("#wizardNetworkMode", "open")
    assert pageDashboard.is_enabled("#wizardX11Forwarding")
    pageDashboard.check("#wizardX11Forwarding")
    assert pageDashboard.is_disabled("#wizardNetworkMode")
    assert pageDashboard.input_value("#wizardNetworkMode") == "open"
    pageDashboard.uncheck("#wizardX11Forwarding")
    assert pageDashboard.is_enabled("#wizardNetworkMode")
    pageDashboard.select_option("#wizardNetworkMode", "isolated")
    assert pageDashboard.is_disabled("#wizardX11Forwarding")
    assert pageDashboard.listPageErrors == []


@pytest.mark.falsification
def testTheWizardRemembersTheChoiceWhenThePageIsLeft(
    pageDashboard, serverHub,
):
    """Kills: dropping the X11 choice when the page is left."""
    _fnOpenTheConvertWizard(pageDashboard, serverHub)
    _fnAdvanceToTheRuntimeOptions(pageDashboard)
    pageDashboard.select_option("#wizardNetworkMode", "open")
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
    assert pageDashboard.input_value("#wizardNetworkMode") == "open"
    assert pageDashboard.is_disabled("#wizardNetworkMode")


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


def _fdictSettings(sNetworkMode, bX11Forwarding=False):
    return {
        "bNeverSleep": False, "bX11Forwarding": bX11Forwarding,
        "bNetworkIsolation": sNetworkMode == "isolated",
        "sNetworkMode": sNetworkMode, "listAllowedHosts": ["data.example.org"],
        "iCpuLimit": 0, "fMemoryLimitGigabytes": 0, "dictImageTrust": None,
        "bImageObtained": False,
    }


def _fnOpenTheSettingsDialog(page, serverHub):
    page.goto(serverHub.fsBootstrapUrl(), wait_until="load")
    page.wait_for_selector(S_TILE, timeout=10000)
    page.click(f"{S_TILE} .container-tile-gear")
    page.wait_for_selector("#settingX11Forwarding", timeout=5000)


@pytest.mark.falsification
def testTheSettingsDialogSendsTheChosenX11Value(pageDashboard, serverHub):
    """Kills: a settings save that never sends the X11 choice."""
    listPostBodies = []
    _fnStubSettings(pageDashboard, listPostBodies, _fdictSettings("open"))
    _fnOpenTheSettingsDialog(pageDashboard, serverHub)
    assert not pageDashboard.is_checked("#settingX11Forwarding")
    pageDashboard.check("#settingX11Forwarding")
    pageDashboard.click("#btnSettingsSave")
    pageDashboard.wait_for_timeout(500)
    assert len(listPostBodies) == 1
    assert listPostBodies[0]["bX11Forwarding"] is True
    assert pageDashboard.listPageErrors == []


@pytest.mark.falsification
def testTheSettingsDialogSendsTheChosenNetworkMode(pageDashboard, serverHub):
    """Kills: a settings save that never sends the network mode."""
    listPostBodies = []
    _fnStubSettings(pageDashboard, listPostBodies, _fdictSettings("open"))
    _fnOpenTheSettingsDialog(pageDashboard, serverHub)
    assert pageDashboard.input_value("#settingNetworkMode") == "open"
    assert "data.example.org" in pageDashboard.text_content("#modalSettings")
    pageDashboard.select_option("#settingNetworkMode", "allowlist")
    pageDashboard.click("#btnSettingsSave")
    pageDashboard.wait_for_timeout(500)
    assert len(listPostBodies) == 1
    assert listPostBodies[0]["sNetworkMode"] == "allowlist"
    assert listPostBodies[0]["bX11Forwarding"] is False
    assert pageDashboard.listPageErrors == []


def testTheSettingsDialogDisablesX11WhileTheNetworkIsSealed(
    pageDashboard, serverHub,
):
    listPostBodies = []
    _fnStubSettings(pageDashboard, listPostBodies, _fdictSettings("allowlist"))
    _fnOpenTheSettingsDialog(pageDashboard, serverHub)
    assert pageDashboard.is_disabled("#settingX11Forwarding")
    assert "Unavailable while the network mode is not open" in (
        pageDashboard.text_content("#modalSettings"))


def testTheSettingsDialogLocksTheModeOpenWhileX11IsOn(pageDashboard, serverHub):
    listPostBodies = []
    _fnStubSettings(pageDashboard, listPostBodies, _fdictSettings("open", True))
    _fnOpenTheSettingsDialog(pageDashboard, serverHub)
    assert pageDashboard.is_checked("#settingX11Forwarding")
    assert pageDashboard.is_disabled("#settingNetworkMode")
    assert "Locked to open" in pageDashboard.text_content("#modalSettings")
