"""A failure on the hub ends in a diagnosis, not a dead end.

Every action on the environment hub used to report a failure as the
server's raw text and stop there. Now the toast ends in "Click to run
a diagnosis", the click runs doctor's host checks through the hub and
renders them, and the ? beside the toolbar explains the page. All
three are asserted in a real browser against the real hub.
"""

import json

import pytest

from tests.browser.conftest import S_CONTAINER_NAME

pytestmark = pytest.mark.browser

S_TILE = f'.container-tile[data-name="{S_CONTAINER_NAME}"]'


def _fnLoadTheHub(pageDashboard, serverHub):
    pageDashboard.goto(serverHub.fsBootstrapUrl(), wait_until="load")
    pageDashboard.wait_for_selector(S_TILE, timeout=15000)


def _fnOpenTheKebab(pageDashboard):
    pageDashboard.click(f"{S_TILE} .container-tile-actions")
    pageDashboard.wait_for_selector(
        f"{S_TILE} .container-tile-menu", state="visible", timeout=5000,
    )


def testAFailedStopToastOpensTheDoctorReport(pageDashboard, serverHub):
    _fnLoadTheHub(pageDashboard, serverHub)
    pageDashboard.route(
        f"**/api/containers/{S_CONTAINER_NAME}/stop",
        lambda routeIntercepted: routeIntercepted.fulfill(
            status=500, content_type="application/json",
            body=json.dumps({"detail": (
                f"Stop of '{S_CONTAINER_NAME}' failed. The Colima "
                "virtual machine is not running. Command: colima start "
                "(Docker said: docker stop failed: Cannot connect)"
            )}),
        ),
    )
    pageDashboard.route(
        "**/api/system/doctor",
        lambda routeIntercepted: routeIntercepted.fulfill(
            status=200, content_type="application/json",
            body=json.dumps({"listFindings": [{
                "sName": "docker-daemon", "sLevel": "fail",
                "sScope": "host",
                "sMessage": "Docker daemon not reachable.",
                "sRemediation": "The Colima virtual machine is not running.",
                "sCommand": "colima start",
            }]}),
        ),
    )
    _fnOpenTheKebab(pageDashboard)
    pageDashboard.click(f'{S_TILE} [data-action="stop"]')
    pageDashboard.wait_for_selector(".toast.error", timeout=10000)
    sToast = pageDashboard.locator(".toast.error").first.inner_text()
    assert "Colima virtual machine is not running" in sToast
    assert "Click to run a diagnosis" in sToast
    pageDashboard.click(".toast.error")
    pageDashboard.wait_for_selector(
        "#modalInfo .diagnosis-finding--fail", timeout=10000,
    )
    sReport = pageDashboard.locator("#modalInfo").inner_text()
    assert "docker-daemon" in sReport
    assert "colima start" in sReport


def testTheHubHelpExplainsTheThreeBlocks(pageDashboard, serverHub):
    _fnLoadTheHub(pageDashboard, serverHub)
    pageDashboard.click("#btnHubHelp")
    pageDashboard.wait_for_selector("#modalInfo", timeout=5000)
    sHelp = pageDashboard.locator("#modalInfo").inner_text()
    for sHeading in ("Creating an environment", "Legend", "Troubleshooting"):
        assert sHeading in sHelp
    assert "One environment per browser tab" in sHelp
    # The legend draws the real glyphs, so it cannot drift from the tiles.
    assert pageDashboard.locator(
        "#modalInfo .status-dot.status-running").count() == 1
    assert pageDashboard.locator(
        "#modalInfo .containment-chip--held").count() == 1


def testAStartRefusedForAnUnbuiltImageOffersTheBuild(
    pageDashboard, serverHub,
):
    _fnLoadTheHub(pageDashboard, serverHub)
    sMessage = (
        f"The image for '{S_CONTAINER_NAME}' has not been built yet, so "
        "there is nothing to start."
    )
    pageDashboard.route(
        f"**/api/containers/{S_CONTAINER_NAME}/start",
        lambda routeIntercepted: routeIntercepted.fulfill(
            status=409, content_type="application/json",
            body=json.dumps({
                "sName": S_CONTAINER_NAME, "sMessage": sMessage,
                "sAction": "build",
                "detail": {"sMessage": sMessage, "sAction": "build"},
            }),
        ),
    )
    _fnOpenTheKebab(pageDashboard)
    pageDashboard.click(f'{S_TILE} [data-action="start"]')
    pageDashboard.wait_for_selector("#modalConfirm", timeout=10000)
    sDialog = pageDashboard.locator("#modalConfirm").inner_text()
    assert "has not been built yet" in sDialog
    assert "Build now" in sDialog
