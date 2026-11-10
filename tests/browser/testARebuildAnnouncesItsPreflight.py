"""A Rebuild answers its click before its preflight answers.

The Rebuild flows ask the build route's refusals first, and those
checks look package names and repository branches up online: on a real
project the page sat unchanged for 10-15 s after Confirm, so the
researcher could not tell the click had registered. The page must say
what it is waiting on while the preflight is still unanswered.
"""

import json

import pytest

from tests.browser.conftest import S_CONTAINER_NAME

pytestmark = pytest.mark.browser

S_TILE = f'.container-tile[data-name="{S_CONTAINER_NAME}"]'


@pytest.mark.falsification
def testTheRebuildPreflightIsAnnouncedWhileItIsUnanswered(
    pageDashboard, serverHub,
):
    """Kills: dropping the announcement from the preflight, under which
    nothing on the page changes until the preflight answers."""
    pageDashboard.goto(serverHub.fsBootstrapUrl(), wait_until="load")
    pageDashboard.wait_for_selector(S_TILE, timeout=15000)
    listHeldPreflights = []
    pageDashboard.route(
        f"**/api/containers/{S_CONTAINER_NAME}/build?bPreflightOnly=true",
        lambda routeIntercepted: listHeldPreflights.append(routeIntercepted),
    )
    pageDashboard.click(f"{S_TILE} .container-tile-actions")
    pageDashboard.wait_for_selector(
        f"{S_TILE} .container-tile-menu", state="visible", timeout=5000,
    )
    pageDashboard.click(f'{S_TILE} [data-action="rebuild"]')
    pageDashboard.wait_for_selector("#modalConfirm", timeout=5000)
    pageDashboard.click("#btnConfirmOk")
    pageDashboard.wait_for_function(
        "() => Array.from(document.querySelectorAll('.toast.info')).some("
        "function (el) { return el.textContent.indexOf("
        "'Checking vaibify.yml before anything is stopped') !== -1; })",
        timeout=5000,
    )
    assert len(listHeldPreflights) == 1, "the preflight was never sent"
    assert pageDashboard.locator(
        f"{S_TILE} .status-dot.status-pending").count() == 1
    listHeldPreflights[0].fulfill(
        status=409, content_type="application/json",
        body=json.dumps({"detail": {
            "sMessage": "refused for the test",
            "sRefusal": "unknown-python-package",
        }}),
    )
    pageDashboard.wait_for_selector(".toast.error", timeout=10000)
