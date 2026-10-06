"""A tile held by another vaibify hub explains itself when clicked.

The click handler answers a locked tile with a warning toast naming
the holder's port and the way out, but the locked style once set
``pointer-events: none`` on the very element that carries the click
listener, so the browser never delivered the click: the researcher
got a grey tile and, at best, a hover tooltip. The click here is a raw
mouse click at the tile name's coordinates, so it goes through the
browser's own hit-testing exactly as a researcher's click does, rather
than an element click Playwright could deliver regardless.

The registry is answered by ``page.route``, so what is driven is the
real frontend render and click path over canned server data.
"""

import json

import pytest

pytestmark = pytest.mark.browser

S_REGISTRY_GLOB = "**/api/registry"
S_NAME = "locked-demo"
I_HOLDER_PORT = 8050


def _fnServeLockedContainer(page):
    """Make the picker render one container held by another hub."""
    page.route(
        S_REGISTRY_GLOB,
        lambda routeIntercepted: routeIntercepted.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps({
                "listContainers": [{
                    "sName": S_NAME,
                    "sContainerId": "deadbeefcafe",
                    "sStatus": "running",
                    "bLocked": True,
                    "iLockedByPid": 4242,
                    "iLockedByPort": I_HOLDER_PORT,
                }],
                "listUnrecognized": [],
            }),
        ),
    )


def _fnClickTheTileName(page):
    """Click where a researcher would: the middle of the tile's name."""
    elName = page.wait_for_selector(
        ".container-tile--locked .container-tile-name", timeout=10000,
    )
    dictBox = elName.bounding_box()
    page.mouse.click(
        dictBox["x"] + dictBox["width"] / 2,
        dictBox["y"] + dictBox["height"] / 2,
    )


@pytest.mark.falsification
def testClickingALockedTileNamesTheHolderAndTheRemedy(
    pageDashboard, serverHub,
):
    """The toast says who holds the container and what to do about it.

    Kills: restoring ``pointer-events: none`` on
    ``.container-tile--locked .container-tile-main``, which swallows
    the click before ``fnHandleContainerClick`` can explain anything.
    """
    _fnServeLockedContainer(pageDashboard)
    pageDashboard.goto(serverHub.fsBootstrapUrl(), wait_until="load")
    _fnClickTheTileName(pageDashboard)
    elToast = pageDashboard.wait_for_selector(
        "#toastContainer .toast.warning", timeout=5000,
    )
    sText = elToast.inner_text()
    assert f"port {I_HOLDER_PORT}" in sText, sText
    assert "vaibify sessions" in sText, sText
    assert pageDashboard.listPageErrors == []
