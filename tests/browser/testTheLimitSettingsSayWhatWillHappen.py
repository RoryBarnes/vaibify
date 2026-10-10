"""Saving a limit says what will happen to it; drift says what is running.

Two surfaces, each rendering the server's own sentences and deriving
nothing: the toast after a settings save, which used to say "Use Restart
to apply" whatever had changed, and the banner that names running CPU
or memory limits differing from the project's settings.

The settings endpoint and the readiness answer are stubbed at the
network edge or handed to the real readiness surface, so what is
exercised is the production frontend path from answer to screen.

WHAT THIS DOES NOT COVER: whether a running container's limits really
differ (``testResourceLimitDrift.py``) or what a save does to the file
(``testResourceLimitSettings.py``).
"""

import json

import pytest

from tests.browser.conftest import fnOpenTheSeededHostWorkflow
from tests.browser.fakeDockerAdapter import S_CONTAINER_NAME


pytestmark = pytest.mark.browser

S_SETTINGS_GLOB = "**/api/containers/*/settings"
S_TILE = f'.container-tile[data-name="{S_CONTAINER_NAME}"]'
S_OUTCOME = (
    "The memory limit in vaibify.yml is now 6 GB. It applies the next "
    "time the container starts.")
S_DRIFT = (
    "This container runs with a 6 GB memory limit, but vaibify.yml says "
    "1 GB; the next Restart will apply 1 GB.")


def _fnStubSettings(page):
    dictSettings = {
        "bNeverSleep": False, "bX11Forwarding": False,
        "bNetworkIsolation": False, "iCpuLimit": 0,
        "fMemoryLimitGigabytes": 1.0, "dictImageTrust": None,
        "bImageObtained": False,
    }
    dictSaved = {
        "bSuccess": True, "bRestartRequired": True,
        "listLimitOutcomes": [{
            "sField": "memory", "sOutcome": "nextStart",
            "sSentence": S_OUTCOME}],
    }

    def fnHandle(routeIntercepted):
        bPost = routeIntercepted.request.method == "POST"
        routeIntercepted.fulfill(
            status=200, content_type="application/json",
            body=json.dumps(dictSaved if bPost else dictSettings))

    page.route(S_SETTINGS_GLOB, fnHandle)


@pytest.mark.falsification
def testTheSaveToastCarriesEachChangesOwnSentence(pageDashboard, serverHub):
    """Kills: going back to the fixed "Use Restart to apply" toast."""
    _fnStubSettings(pageDashboard)
    pageDashboard.goto(serverHub.fsBootstrapUrl(), wait_until="load")
    pageDashboard.wait_for_selector(S_TILE, timeout=10000)
    pageDashboard.click(f"{S_TILE} .container-tile-gear")
    pageDashboard.wait_for_selector("#settingMemoryLimit", timeout=5000)
    pageDashboard.fill("#settingMemoryLimit", "6")
    pageDashboard.click("#btnSettingsSave")
    pageDashboard.wait_for_selector("#toastContainer .toast", timeout=5000)
    sToast = pageDashboard.text_content("#toastContainer .toast")
    assert S_OUTCOME in sToast
    assert "Use Restart to apply" not in sToast
    assert pageDashboard.listPageErrors == []


_S_SURFACE = """(listDrift) => {
    VaibifyContainerManager.fnSurfaceReadinessOutcome({
        sStatus: "ready", bReady: true, saWarnings: [],
        listConfigurationDrift: [], listResourceLimitDrift: listDrift,
    });
}"""


@pytest.mark.falsification
def testTheLimitDriftBannerRendersTheServersSentence(pageDashboard, serverHub):
    """Kills: never handing the readiness answer's drift to its banner."""
    fnOpenTheSeededHostWorkflow(pageDashboard, serverHub)
    pageDashboard.evaluate(_S_SURFACE, [S_DRIFT])
    assert pageDashboard.is_visible("#resourceLimitDriftBanner")
    assert pageDashboard.text_content(
        "#resourceLimitDriftBanner li") == S_DRIFT
    pageDashboard.click("#btnDismissResourceLimitDrift")
    assert not pageDashboard.is_visible("#resourceLimitDriftBanner")
    pageDashboard.evaluate(_S_SURFACE, [])
    assert not pageDashboard.is_visible("#resourceLimitDriftBanner"), (
        "no sentences is no difference, or nothing determined")
    assert pageDashboard.listPageErrors == []
