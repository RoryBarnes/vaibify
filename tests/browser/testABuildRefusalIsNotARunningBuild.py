"""A build refused for a bad package name is reported, never attached to.

The dashboard reads a bare 409 from the build route as "a build is
already running" and attaches to its progress. A refusal issued before
any build starts must therefore carry its code, and the page must show
the sentence -- which names the misspelled package and the file --
rather than wait on a build that does not exist.
"""

import json

import pytest

from tests.browser.conftest import S_CONTAINER_NAME

pytestmark = pytest.mark.browser

S_TILE = f'.container-tile[data-name="{S_CONTAINER_NAME}"]'
S_REFUSAL = (
    "'matplolib' under pythonPackages in vaibify.yml does not exist on "
    "the package index (pypi.org), so the build would stop at pip after "
    "the toolchain had already been installed. Fix the name in "
    "vaibify.yml, then build again."
)


@pytest.mark.falsification
def testARefusedBuildShowsTheSentenceAndAttachesToNothing(
    pageDashboard, serverHub,
):
    """Kills: the frontend reading this 409 as a running build, under
    which the researcher watches a progress overlay for a build that
    never started and the sentence naming the typo is never shown."""
    pageDashboard.goto(serverHub.fsBootstrapUrl(), wait_until="load")
    pageDashboard.wait_for_selector(S_TILE, timeout=15000)
    pageDashboard.route(
        f"**/api/containers/{S_CONTAINER_NAME}/stop",
        lambda routeIntercepted: routeIntercepted.fulfill(
            status=200, content_type="application/json",
            body=json.dumps({"bSuccess": True}),
        ),
    )
    pageDashboard.route(
        f"**/api/containers/{S_CONTAINER_NAME}/build**",
        lambda routeIntercepted: routeIntercepted.fulfill(
            status=409, content_type="application/json",
            body=json.dumps({"detail": {
                "sMessage": S_REFUSAL,
                "sRefusal": "unknown-python-package",
            }}),
        ),
    )
    pageDashboard.click(f"{S_TILE} .container-tile-actions")
    pageDashboard.wait_for_selector(
        f"{S_TILE} .container-tile-menu", state="visible", timeout=5000,
    )
    pageDashboard.click(f'{S_TILE} [data-action="rebuild"]')
    pageDashboard.wait_for_selector("#modalConfirm", timeout=5000)
    pageDashboard.click("#btnConfirmOk")
    pageDashboard.wait_for_selector(".toast.error", timeout=10000)
    sToast = pageDashboard.locator(".toast.error").first.inner_text()
    assert "'matplolib'" in sToast
    assert "Fix the name in vaibify.yml" in sToast
    pageDashboard.wait_for_selector(
        "#modalBuildProgress", state="hidden", timeout=10000,
    )
    assert "already running" not in pageDashboard.locator("body").inner_text()
