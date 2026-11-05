"""The Network row above the steps is for something to act on, never for a normal state.

A web-mode project walking the quickstart saw "Network mode: web
(network proxy running) Nothing refused recently" above its steps, a
standing notice about the default mode behaving normally, with no way
to close it (researcher-reported, 2026-11-05). The row is drawn from
the readiness answer's ``dictNetwork``, so it is driven here through
the page's own readiness surface with the server's shapes, and read
off the real element.
"""

import json

import pytest

from tests.browser.conftest import S_CONTAINER_NAME
from tests.browser.fakeDockerAdapter import S_CONTAINER_ID


pytestmark = pytest.mark.browser

S_TILE = f'.container-tile[data-name="{S_CONTAINER_NAME}"]'

_S_SURFACE_NETWORK = """(dictNetwork) => {
    VaibifyContainerManager.fnSurfaceReadinessOutcome(
        {sStatus: "ready", bReady: true, dictNetwork: dictNetwork},
        %s);
    var elBanner = document.getElementById("networkBanner");
    return {sDisplay: elBanner.style.display, sText: elBanner.innerText};
}""" % json.dumps(S_CONTAINER_ID)


def fdictNetwork(sMode, sContainerState, bSealed, sProxyState="not-applicable",
                 listRefused=None, bFileAgrees=True):
    return {
        "sMode": sMode, "sName": S_CONTAINER_NAME, "bSealed": bSealed,
        "sProxyState": sProxyState, "listRefused": listRefused or [],
        "sContainerState": sContainerState, "sEvidence": "",
        "bFileAgrees": bFileAgrees,
    }


def _fnLoadTheHub(pageDashboard, serverHub):
    pageDashboard.goto(serverHub.fsBootstrapUrl(), wait_until="load")
    pageDashboard.wait_for_selector(S_TILE, timeout=15000)


@pytest.mark.falsification
def testANormalSealedOrIsolatedProjectShowsNoRow(pageDashboard, serverHub):
    """Kills: drawing the row for every readiness answer that carries a network."""
    _fnLoadTheHub(pageDashboard, serverHub)
    for dictNetwork in (
        fdictNetwork("web", "sealed", True, sProxyState="running"),
        fdictNetwork("allowlist", "sealed", True, sProxyState="running"),
        fdictNetwork("isolated", "isolated", False),
        fdictNetwork("web", "unknown", True, sProxyState="running"),
    ):
        dictShown = pageDashboard.evaluate(_S_SURFACE_NETWORK, dictNetwork)
        assert dictShown["sDisplay"] == "none", dictNetwork
        assert dictShown["sText"] == ""
    assert pageDashboard.listPageErrors == []


def testWhatNeedsAttentionIsShownAndNamed(pageDashboard, serverHub):
    _fnLoadTheHub(pageDashboard, serverHub)
    dictOpen = pageDashboard.evaluate(
        _S_SURFACE_NETWORK, fdictNetwork("open", "open", False))
    assert dictOpen["sDisplay"] == "block"
    assert "Network mode: open" in dictOpen["sText"]
    dictDisagreement = pageDashboard.evaluate(
        _S_SURFACE_NETWORK, fdictNetwork("web", "open", True, "running", bFileAgrees=False))
    assert "created open" in dictDisagreement["sText"]
    assert "asks for web" in dictDisagreement["sText"]
    dictUnstated = pageDashboard.evaluate(
        _S_SURFACE_NETWORK, fdictNetwork("unstated", "unknown", False))
    assert "names no network mode" in dictUnstated["sText"]
    dictDown = pageDashboard.evaluate(
        _S_SURFACE_NETWORK, fdictNetwork("web", "sealed", True, "down"))
    assert "network proxy down" in dictDown["sText"]
    dictRefused = pageDashboard.evaluate(_S_SURFACE_NETWORK, fdictNetwork(
        "allowlist", "sealed", True, "running",
        listRefused=[{"sHost": "data.example.org", "iPort": 443, "iCount": 3,
                      "sReason": "not in the allowlist"}]))
    assert "Refused 3" in dictRefused["sText"]
    assert "Nothing refused" not in dictRefused["sText"]
    assert pageDashboard.locator("#networkBanner .btn-allow-network-host").count() == 1
    dictQuiet = pageDashboard.evaluate(
        _S_SURFACE_NETWORK, fdictNetwork("web", "sealed", True, "running"))
    assert dictQuiet["sDisplay"] == "none"
    assert pageDashboard.listPageErrors == []
