"""The Project Hub explains itself and its failures.

Opening a Project used to show the server's raw text; the automatic
list refresh used to fail silently; the screen had no help. Asserted in
a real browser against the real hub, on the seeded host project.
"""

import json

import pytest

from tests.browser.conftest import S_HOST_PROJECT_READY, S_HOST_WORKFLOW_NAME

pytestmark = pytest.mark.browser


def _fnOpenTheSeededHostPicker(pageDashboard, serverHub):
    """Acknowledge the host warning and stop at the Project Hub."""
    # A previous test's page held the project and never went Back; the
    # module-scoped hub would refuse this page's claim as another
    # session's. Drop the record so each test starts unheld.
    serverHub.app.state.dictContainerOwners.pop(S_HOST_PROJECT_READY, None)
    pageDashboard.goto(serverHub.fsBootstrapUrl(), wait_until="load")
    sTile = f'.container-tile[data-name="{S_HOST_PROJECT_READY}"]'
    pageDashboard.wait_for_selector(sTile, timeout=15000)
    pageDashboard.click(f"{sTile} .container-tile-main")
    pageDashboard.wait_for_selector("#modalConfirm", timeout=10000)
    pageDashboard.click("#btnConfirmOk")
    pageDashboard.wait_for_selector(
        f"text={S_HOST_WORKFLOW_NAME}", timeout=20000,
    )


def testTheProjectHubHelpIsAnOutlineOfThreeBlocks(pageDashboard, serverHub):
    _fnOpenTheSeededHostPicker(pageDashboard, serverHub)
    pageDashboard.click("#btnProjectHubHelp")
    pageDashboard.wait_for_selector("#modalInfo details.hub-help-section")
    assert pageDashboard.locator(
        "#modalInfo details.hub-help-section").count() == 3
    sOutline = pageDashboard.locator("#modalInfo").inner_text()
    for sHeading in ("Using the Project Hub", "Legend", "Troubleshooting"):
        assert sHeading in sOutline
    # The legend draws the real badge class, so it cannot drift.
    assert pageDashboard.locator("#modalInfo .host-mode-badge").count() == 1


def testAProjectThatWillNotLoadSaysWhyAndOffersADiagnosis(
    pageDashboard, serverHub,
):
    _fnOpenTheSeededHostPicker(pageDashboard, serverHub)
    sReason = (
        "The project file could not be loaded: Invalid project.json at "
        ".vaibify/projects/x.json: missing required top-level key "
        "'listSteps'"
    )
    pageDashboard.route(
        "**/api/connect/*",
        lambda routeIntercepted: routeIntercepted.fulfill(
            status=400, content_type="application/json",
            body=json.dumps({"detail": {"sMessage": sReason}}),
        ),
    )
    pageDashboard.click(f"text={S_HOST_WORKFLOW_NAME}")
    pageDashboard.wait_for_selector(".toast.error", timeout=10000)
    sToast = pageDashboard.locator(".toast.error").first.inner_text()
    assert "missing required top-level key" in sToast
    assert "Click to run a diagnosis" in sToast


def testAProjectListThatCannotRefreshSaysSo(pageDashboard, serverHub):
    _fnOpenTheSeededHostPicker(pageDashboard, serverHub)
    pageDashboard.route(
        "**/api/workflows/*",
        lambda routeIntercepted: routeIntercepted.fulfill(
            status=500, content_type="application/json",
            body=json.dumps({"detail": "Search failed: the daemon vanished"}),
        ),
    )
    pageDashboard.wait_for_selector(
        ".toast.error:has-text(\"project list could not be refreshed\")",
        timeout=15000,
    )
    sToast = pageDashboard.locator(".toast.error").first.inner_text()
    assert "the daemon vanished" in sToast
    assert "Click to run a diagnosis" in sToast
