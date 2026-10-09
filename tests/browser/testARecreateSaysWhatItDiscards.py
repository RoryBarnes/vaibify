"""Every action that recreates the container says what it would discard.

Restart, Rebuild, Force Rebuild, Re-obtain, and both image switches all
create a new container, discarding the old one's writable layer -- an
agent's scratch files in /tmp above all. Each confirmation now opens on
"Checking what this would discard..." with Confirm DISABLED, asks the
hub, and enables Confirm only once the hub's sentence is on screen: the
researcher never acts on an interim text. A request that fails still
enables Confirm, with a sentence saying the size is unknown and why.

The hub's answer is held at the network edge so "before" and "after"
are both observable; everything else is the production frontend.

WHAT THIS DOES NOT COVER: the measurement itself
(``testWritableLayerLoss.py``) or what each action then does
(``testContainerizeFromPinnedImage.py`` and the lifecycle journeys).
"""

import json

import pytest

from tests.browser.fakeDockerAdapter import S_CONTAINER_NAME
from tests.browser.testContainerizeFromPinnedImage import (
    _fnListTheTileAsBuiltWithAnObtainablePin,
    _fnListTheTileAsObtained,
    _fnWaitForPicker,
)


pytestmark = pytest.mark.browser

S_PREVIEW_GLOB = "**/api/containers/*/writable-layer-preview"
S_CHECKING = "Checking what this would discard"
S_MEASURED = (
    "Files in the container's writable layer, including 1.4 GB in /tmp, "
    "are discarded; mounted volumes and host directories are preserved.")
S_TILE = f'.container-tile[data-name="{S_CONTAINER_NAME}"]'

T_BUILT_ACTIONS = ("restart", "rebuild", "force-rebuild",
                   "switch-to-pinned-image")
T_OBTAINED_ACTIONS = ("reobtain", "switch-to-building")


def _flistHoldThePreview(page):
    listHeld = []
    page.route(S_PREVIEW_GLOB, lambda routeHeld: listHeld.append(routeHeld))
    return listHeld


def _fnOpenTheConfirmation(page, serverHub, sAction):
    if sAction in T_OBTAINED_ACTIONS:
        _fnListTheTileAsObtained(page, "running")
    else:
        _fnListTheTileAsBuiltWithAnObtainablePin(page, "running")
    _fnWaitForPicker(page, serverHub)
    page.click(f"{S_TILE} .container-tile-actions")
    page.click(f'{S_TILE} .container-menu-item[data-action="{sAction}"]')
    page.wait_for_selector("#modalConfirm", timeout=5000)


def _fnWaitForTheHold(page, listHeld):
    for _iTick in range(100):
        if listHeld:
            return
        page.wait_for_timeout(100)
    raise AssertionError("the confirmation never asked the hub")


@pytest.mark.parametrize("sAction", T_BUILT_ACTIONS + T_OBTAINED_ACTIONS)
def testConfirmWaitsForTheHubsSentence(pageDashboard, serverHub, sAction):
    listHeld = _flistHoldThePreview(pageDashboard)
    _fnOpenTheConfirmation(pageDashboard, serverHub, sAction)
    _fnWaitForTheHold(pageDashboard, listHeld)
    assert pageDashboard.is_disabled("#btnConfirmOk")
    assert S_CHECKING in pageDashboard.text_content("#modalConfirm")
    listHeld[0].fulfill(
        status=200, content_type="application/json",
        body=json.dumps({"sState": "measured", "iTmpBytes": 1503238553,
                         "sSentence": S_MEASURED}))
    pageDashboard.wait_for_selector("#btnConfirmOk:enabled", timeout=5000)
    sDialog = pageDashboard.text_content("#modalConfirm")
    assert S_MEASURED in sDialog
    assert S_CHECKING not in sDialog
    assert "Workspace files are preserved" not in sDialog
    pageDashboard.click("#btnConfirmCancel")
    assert pageDashboard.listPageErrors == []


@pytest.mark.falsification
def testRestartWaitsForTheHubBeforeItCanBeConfirmed(pageDashboard, serverHub):
    """Kills: Restart's confirmation no longer asking what it discards."""
    listHeld = _flistHoldThePreview(pageDashboard)
    _fnOpenTheConfirmation(pageDashboard, serverHub, "restart")
    _fnWaitForTheHold(pageDashboard, listHeld)
    assert pageDashboard.is_disabled("#btnConfirmOk")
    listHeld[0].fulfill(
        status=200, content_type="application/json",
        body=json.dumps({"sState": "measured", "iTmpBytes": 1503238553,
                         "sSentence": S_MEASURED}))
    pageDashboard.wait_for_selector("#btnConfirmOk:enabled", timeout=5000)
    assert S_MEASURED in pageDashboard.text_content("#modalConfirm")


@pytest.mark.falsification
def testConfirmIsDisabledUntilTheAnswerArrives(pageDashboard, serverHub):
    """Kills: enabling Confirm before the hub's sentence arrives."""
    listHeld = _flistHoldThePreview(pageDashboard)
    _fnOpenTheConfirmation(pageDashboard, serverHub, "rebuild")
    _fnWaitForTheHold(pageDashboard, listHeld)
    pageDashboard.wait_for_timeout(300)
    assert pageDashboard.is_disabled("#btnConfirmOk")
    listHeld[0].fulfill(
        status=200, content_type="application/json",
        body=json.dumps({"sState": "measured", "iTmpBytes": 1,
                         "sSentence": S_MEASURED}))
    pageDashboard.wait_for_selector("#btnConfirmOk:enabled", timeout=5000)


def testAFailedRequestStillEnablesConfirmAndSaysTheSizeIsUnknown(
    pageDashboard, serverHub,
):
    pageDashboard.route(S_PREVIEW_GLOB, lambda routeFailed: routeFailed.fulfill(
        status=500, content_type="application/json",
        body=json.dumps({"detail": "boom"})))
    _fnOpenTheConfirmation(pageDashboard, serverHub, "restart")
    pageDashboard.wait_for_selector("#btnConfirmOk:enabled", timeout=5000)
    sDialog = pageDashboard.text_content("#modalConfirm")
    assert "the size of /tmp could not be measured" in sDialog
    assert S_CHECKING not in sDialog
