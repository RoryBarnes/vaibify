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


def testTheHubHelpExplainsTheFourBlocks(pageDashboard, serverHub):
    _fnLoadTheHub(pageDashboard, serverHub)
    pageDashboard.click("#btnHubHelp")
    pageDashboard.wait_for_selector("#modalInfo", timeout=5000)
    # An outline: four folded sections whose headings read first.
    assert pageDashboard.locator("#modalInfo details.hub-help-section").count() == 4
    assert pageDashboard.locator("#modalInfo details[open]").count() == 0
    sOutline = pageDashboard.locator("#modalInfo").inner_text()
    for sHeading in ("Creating an environment", "Legend", "Troubleshooting",
                     "Leftover processes and files"):
        assert sHeading in sOutline
    assert "One environment per browser tab" not in sOutline
    for elSummary in pageDashboard.locator("#modalInfo summary").all():
        elSummary.click()
    sHelp = pageDashboard.locator("#modalInfo").inner_text()
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


def _fnFailTheStop(pageDashboard, sDetail):
    pageDashboard.route(
        f"**/api/containers/{S_CONTAINER_NAME}/stop",
        lambda routeIntercepted: routeIntercepted.fulfill(
            status=500, content_type="application/json",
            body=json.dumps({"detail": sDetail}),
        ),
    )
    pageDashboard.route(
        "**/api/system/doctor",
        lambda routeIntercepted: routeIntercepted.fulfill(
            status=200, content_type="application/json",
            body=json.dumps({"listFindings": [{
                "sName": "docker-daemon", "sLevel": "ok", "sScope": "host",
                "sMessage": "Docker daemon reachable.",
            }]}),
        ),
    )


@pytest.mark.falsification
def testTheDiagnosisOpensWithTheFailureItself(pageDashboard, serverHub):
    """Kills: a diagnosis that shows only the machine's checks.

    The checks are about this machine and can all pass while the
    failure sits inside a download: a researcher clicked a failed
    acquisition's toast and got a page of passing checks, with the
    error that mattered nowhere on it (2026-09-26).
    """
    _fnLoadTheHub(pageDashboard, serverHub)
    _fnFailTheStop(pageDashboard, "the deposit could not be decompressed")
    _fnOpenTheKebab(pageDashboard)
    pageDashboard.click(f'{S_TILE} [data-action="stop"]')
    pageDashboard.wait_for_selector(".toast.error", timeout=10000)
    pageDashboard.click(".toast.error")
    pageDashboard.wait_for_selector(
        "#modalInfo .diagnosis-finding--ok", timeout=10000,
    )
    sFailure = pageDashboard.locator(
        "#modalInfo .diagnosis-failure").inner_text()
    assert "the deposit could not be decompressed" in sFailure
    assert pageDashboard.locator("#modalInfo .diagnosis-copy").count() == 1
    assert pageDashboard.listPageErrors == []


@pytest.mark.falsification
def testSelectingAToastsTextDoesNotOpenTheDiagnosis(pageDashboard, serverHub):
    """Kills: treating the click that ends a text selection as "act".

    A researcher tried to copy a failure and was carried into the
    diagnosis instead, and the toast they were copying was gone.
    """
    _fnLoadTheHub(pageDashboard, serverHub)
    _fnFailTheStop(
        pageDashboard, "a long failure message the researcher wants to copy",
    )
    _fnOpenTheKebab(pageDashboard)
    pageDashboard.click(f'{S_TILE} [data-action="stop"]')
    pageDashboard.wait_for_selector(".toast.error", timeout=10000)
    # Let the slide-in finish, and close the menu that opened the stop,
    # so the drag lands on the toast's settled text.
    pageDashboard.wait_for_timeout(500)
    pageDashboard.keyboard.press("Escape")
    dictBox = pageDashboard.locator(".toast.error").first.bounding_box()
    fY = dictBox["y"] + dictBox["height"] / 2
    pageDashboard.mouse.move(dictBox["x"] + 8, fY)
    pageDashboard.mouse.down()
    pageDashboard.mouse.move(dictBox["x"] + dictBox["width"] / 2, fY, steps=8)
    pageDashboard.mouse.up()
    pageDashboard.wait_for_timeout(400)
    assert pageDashboard.evaluate("() => String(window.getSelection())"), (
        "the drag selected nothing, so this test proves nothing"
    )
    assert not pageDashboard.is_visible("#modalInfo"), (
        "selecting the toast's text opened the diagnosis"
    )
    assert pageDashboard.locator(".toast.error").count() >= 1
    assert pageDashboard.listPageErrors == []
