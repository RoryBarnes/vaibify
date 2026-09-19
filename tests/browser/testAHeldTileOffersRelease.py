"""A tile this tab holds says so, and the hold can be let go.

Starting a container from the picker claims it for the tab before the
container is ever opened, and the reaper spares that claim while the tab
is present. A researcher who then created a second container and pressed
Start was refused with "this browser session already holds ..." and had
no idea why: the held tile looked like every other running one, and no
control released it. Two remedies, each asserted in a real browser:

* a tile the tab holds wears a chip and its actions menu offers
  **Release**, which drops the owner record on the server;
* a start refused for that reason opens a dialog naming the held
  container and, on confirm, releases it and retries the start.
"""

import json

import pytest

from tests.browser.conftest import S_CONTAINER_NAME

pytestmark = pytest.mark.browser

S_TILE = f'.container-tile[data-name="{S_CONTAINER_NAME}"]'
S_HELD_CHIP = f"{S_TILE} .containment-chip--held"
S_OTHER_CONTAINER = "someOtherContainer"
S_REFUSAL = (
    "This browser session already holds container "
    f"'{S_OTHER_CONTAINER}'; release it before claiming another"
)


def _fnLoadTheHub(pageDashboard, serverHub):
    pageDashboard.goto(serverHub.fsBootstrapUrl(), wait_until="load")
    pageDashboard.wait_for_selector(S_TILE, timeout=15000)


def _fnOpenTheKebab(pageDashboard):
    pageDashboard.click(f"{S_TILE} .container-tile-actions")
    pageDashboard.wait_for_selector(
        f"{S_TILE} .container-tile-menu", state="visible", timeout=5000,
    )


def testAHeldTileWearsTheChipAndOffersRelease(pageDashboard, serverHub):
    """Claim from the page, see the chip, release from the menu."""
    _fnLoadTheHub(pageDashboard, serverHub)
    assert pageDashboard.locator(S_HELD_CHIP).count() == 0
    bClaimed = pageDashboard.evaluate(
        "(sName) => VaibifyContainerManager.fbClaimContainer(sName)",
        S_CONTAINER_NAME,
    )
    assert bClaimed is True
    assert S_CONTAINER_NAME in serverHub.app.state.dictContainerOwners
    pageDashboard.evaluate("() => VaibifyContainerManager.fnLoadContainers()")
    pageDashboard.wait_for_selector(S_HELD_CHIP, timeout=10000)
    _fnOpenTheKebab(pageDashboard)
    pageDashboard.click(f'{S_TILE} [data-action="release"]')
    pageDashboard.wait_for_selector(
        S_HELD_CHIP, state="detached", timeout=10000,
    )
    # The server is the authority: the chip going away must mean the
    # owner record went away, not that the tile forgot to draw it.
    assert S_CONTAINER_NAME not in serverHub.app.state.dictContainerOwners


def testARefusedStartOffersToReleaseTheHeldContainer(
    pageDashboard, serverHub,
):
    """The dead-end toast became a dialog that does the release."""
    _fnLoadTheHub(pageDashboard, serverHub)
    # This tab believes it holds another container, exactly as it would
    # after starting one from the picker and never opening it.
    pageDashboard.evaluate(
        "([sName, sLease]) => VaibifyApp.fnRecordClaimedLease(sName, sLease)",
        [S_OTHER_CONTAINER, "lease-held"],
    )
    listReleaseBodies = []
    listStartAttempts = []

    def fnAnswerRelease(routeIntercepted):
        listReleaseBodies.append(routeIntercepted.request.headers)
        routeIntercepted.fulfill(
            status=200, content_type="application/json", body="{}",
        )

    def fnRefuseStart(routeIntercepted):
        listStartAttempts.append(1)
        routeIntercepted.fulfill(
            status=409, content_type="application/json",
            body=json.dumps({
                "sName": S_CONTAINER_NAME, "sMessage": S_REFUSAL,
                "sHeldContainerName": S_OTHER_CONTAINER,
                "detail": {
                    "sMessage": S_REFUSAL,
                    "sHeldContainerName": S_OTHER_CONTAINER,
                },
            }),
        )

    pageDashboard.route(
        f"**/api/registry/{S_OTHER_CONTAINER}/release", fnAnswerRelease,
    )
    pageDashboard.route(
        f"**/api/containers/{S_CONTAINER_NAME}/start", fnRefuseStart,
    )
    _fnOpenTheKebab(pageDashboard)
    pageDashboard.click(f'{S_TILE} [data-action="start"]')
    pageDashboard.wait_for_selector("#modalConfirm", timeout=10000)
    sDialog = pageDashboard.locator("#modalConfirm").inner_text()
    assert S_OTHER_CONTAINER in sDialog
    assert "Release and continue" in sDialog
    assert listReleaseBodies == []
    pageDashboard.click("#btnConfirmOk")
    # Confirming releases the held container with its lease, then
    # retries the start -- which this stub refuses again, so the offer
    # comes back: two attempts, one release, and the dialog on screen.
    for _ in range(50):
        if len(listStartAttempts) == 2 and len(listReleaseBodies) == 1:
            break
        pageDashboard.wait_for_timeout(200)
    assert len(listReleaseBodies) == 1
    assert listReleaseBodies[0].get("x-vaibify-lease") == "lease-held"
    assert len(listStartAttempts) == 2
