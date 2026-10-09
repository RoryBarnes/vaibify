"""The advice that an agent may need more memory is the server's, verbatim.

The threshold and the sentence belong to ``resourceAdequacy`` on the
server. The dashboard shows them in two places -- a banner from the
readiness answer, and a warning after a settings save -- and must never
hold a threshold or a sentence of its own, or the two authorities would
drift apart the first time either changed.

WHAT THIS DOES NOT COVER: when the server advises
(``testResourceAdequacy.py``).
"""

import json

import pytest

from tests.browser.conftest import fnOpenTheSeededHostWorkflow
from tests.browser.fakeDockerAdapter import S_CONTAINER_NAME


pytestmark = pytest.mark.browser

S_ADVICE = (
    "This project runs an AI agent with a 1 GB memory limit. Agents and "
    "the jobs they start often need several GB; 5 GB is a starting point, "
    "not a requirement. Raise or remove the limit in Settings.")
S_TILE = f'.container-tile[data-name="{S_CONTAINER_NAME}"]'

_S_SURFACE = """(listAdvice) => {
    VaibifyContainerManager.fnSurfaceReadinessOutcome({
        sStatus: "ready", bReady: true, saWarnings: [],
        listConfigurationDrift: [], listResourceLimitDrift: [],
        listResourceAdvisories: listAdvice,
    });
}"""


@pytest.mark.falsification
def testTheAdviceBannerShowsTheServersSentence(pageDashboard, serverHub):
    """Kills: the banner wording the advice itself instead of the server."""
    fnOpenTheSeededHostWorkflow(pageDashboard, serverHub)
    pageDashboard.evaluate(_S_SURFACE, [S_ADVICE])
    assert pageDashboard.text_content("#resourceAdvisoryBanner li") == S_ADVICE
    pageDashboard.evaluate(_S_SURFACE, [])
    assert not pageDashboard.is_visible("#resourceAdvisoryBanner")
    assert pageDashboard.listPageErrors == []


def testASaveThatLeavesTheLimitSmallWarnsWithTheServersSentence(
    pageDashboard, serverHub,
):
    def fnHandle(routeIntercepted):
        bPost = routeIntercepted.request.method == "POST"
        routeIntercepted.fulfill(
            status=200, content_type="application/json",
            body=json.dumps({
                "bSuccess": True, "bRestartRequired": True,
                "listLimitOutcomes": [], "listResourceAdvisories": [S_ADVICE],
            } if bPost else {
                "bNeverSleep": False, "bX11Forwarding": False,
                "bNetworkIsolation": False, "iCpuLimit": 0,
                "fMemoryLimitGigabytes": 1.0, "dictImageTrust": None,
                "bImageObtained": False,
            }))

    pageDashboard.route("**/api/containers/*/settings", fnHandle)
    pageDashboard.goto(serverHub.fsBootstrapUrl(), wait_until="load")
    pageDashboard.wait_for_selector(S_TILE, timeout=10000)
    pageDashboard.click(f"{S_TILE} .container-tile-gear")
    pageDashboard.wait_for_selector("#btnSettingsSave", timeout=5000)
    pageDashboard.click("#btnSettingsSave")
    pageDashboard.wait_for_selector(
        "#toastContainer .toast.warning", timeout=5000)
    assert S_ADVICE in pageDashboard.text_content(
        "#toastContainer .toast.warning")
    assert pageDashboard.listPageErrors == []
