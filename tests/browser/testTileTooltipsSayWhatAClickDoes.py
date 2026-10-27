"""Each Environment Hub tile says what it is and what a click does.

Two stopped containers once showed two red dots and no word about what
to do (researcher-reported, 2026-10-26), although clicking a stopped
tile starts it. Each tile now carries a tooltip naming its state and
the click's effect, and a stopped container's dot is grey: red is kept
for what needs fixing. The oracle for each sentence is what
``fnHandleContainerClick`` does for that state.

The registry is answered by ``page.route``, so the real render runs
over one tile in every state.
"""

import json

import pytest

pytestmark = pytest.mark.browser

DICT_TOOLTIP_BY_NAME = {
    "running-demo": "Running. Click to open it.",
    "stopped-demo": (
        "Stopped. Click to start it and open it, or use ⋮ → "
        "Start to start it without opening."),
    "unbuilt-demo": "Not built yet. Click to build its image and start it.",
    "obtained-demo": (
        "Not built yet. Click to obtain the author's image and start it."),
    "host-demo": "Ready on this machine, with no container. Click to open it.",
    "gone-demo": (
        "Its folder is gone: /clones/gone-demo. Move it back, or remove "
        "the tile with ⋮ → Remove from list."),
}
LIST_CONTAINERS = [
    {"sName": "running-demo", "sContainerId": "c1", "sStatus": "running"},
    {"sName": "stopped-demo", "sContainerId": "c2", "sStatus": "stopped"},
    {"sName": "unbuilt-demo", "sContainerId": "", "sStatus": "not built"},
    {"sName": "obtained-demo", "sContainerId": "", "sStatus": "not built",
     "dictImageSource": {"sSource": "archive"}},
    {"sName": "host-demo", "sMode": "host", "sStatus": "ready",
     "sDirectory": "/clones/host-demo"},
    {"sName": "gone-demo", "sMode": "host", "sStatus": "missing",
     "sDirectory": "/clones/gone-demo"},
]
S_STOPPED_DOT_IS_GREY = """() => {
    const elProbe = document.createElement('span');
    elProbe.style.backgroundColor = 'var(--text-muted)';
    document.body.appendChild(elProbe);
    const sGrey = getComputedStyle(elProbe).backgroundColor;
    elProbe.remove();
    const elDot = document.querySelector(
        '.container-tile[data-name="stopped-demo"] .status-dot');
    return getComputedStyle(elDot).backgroundColor === sGrey;
}"""


def _fnOpenTheHubWithEveryState(page, serverHub):
    page.route("**/api/registry", lambda routeIntercepted: (
        routeIntercepted.fulfill(
            status=200, content_type="application/json",
            body=json.dumps({"listContainers": LIST_CONTAINERS,
                             "listUnrecognized": []}))))
    page.goto(serverHub.fsBootstrapUrl(), wait_until="load")
    page.wait_for_selector('.container-tile[data-name="gone-demo"]',
                           timeout=15000)


@pytest.mark.falsification
def testEveryTileSaysWhatItIsAndWhatAClickDoes(pageDashboard, serverHub):
    """Kills: a tile whose dot is the only word about its state."""
    _fnOpenTheHubWithEveryState(pageDashboard, serverHub)
    for sName, sTooltip in DICT_TOOLTIP_BY_NAME.items():
        sTitle = pageDashboard.get_attribute(
            f'.container-tile[data-name="{sName}"] .container-tile-main',
            "title")
        assert sTitle == sTooltip, (sName, sTitle)
    assert pageDashboard.listPageErrors == []


@pytest.mark.falsification
def testAStoppedContainerIsGreyNotRed(pageDashboard, serverHub):
    """Kills: a resting container drawn in the red kept for failures.

    The colour is read off the live dot against the theme's own grey,
    so a class with no rule behind it cannot pass.
    """
    _fnOpenTheHubWithEveryState(pageDashboard, serverHub)
    assert pageDashboard.evaluate(S_STOPPED_DOT_IS_GREY)
