"""A container's start banners never follow the researcher into another project.

The readiness banners -- build warnings, an environment older than its
file, pinned-environment and resource notices -- are drawn only when a
container's start is checked. Nothing cleared them on entering another
project, so a researcher who opened a converted project, saw its
``clone-unknown`` warning, removed the container and reopened the
project in host mode was still shown "1 build warning from the most
recent container start" for a project with no container at all
(researcher-reported, 2026-10-16). Driven without a reload, because a
reload clears every banner and would hide the defect.
"""

import pytest

from tests.browser.conftest import (
    S_HOST_PROJECT_READY,
    S_HOST_WORKFLOW_NAME,
)


pytestmark = pytest.mark.browser

S_TILE = f'.container-tile[data-name="{S_HOST_PROJECT_READY}"]'

_S_SURFACE_A_REMOVED_CONTAINERS_START = """() => {
    VaibifyContainerManager.fnSurfaceReadinessOutcome({
        sStatus: "ready", bReady: true,
        saWarnings: ["aigreen: clone-unknown: destination path exists"],
        listConfigurationDrift: ["The environment's settings changed."],
        listResourceAdvisories: ["The memory limit may be small."],
    }, "removedContainer0123456789abcdef");
}"""

_S_SHOWN_BANNERS = """() => [
    "buildWarningsBanner", "configurationDriftBanner",
    "resourceAdvisoryBanner",
].filter(function (sId) {
    var elBanner = document.getElementById(sId);
    return elBanner && elBanner.style.display === "block";
})"""


@pytest.mark.falsification
def test_opening_a_project_without_that_container_clears_its_start_banners(
    pageDashboard, serverHub,
):
    """Kills: entering a project that leaves another container's banners up."""
    pageDashboard.goto(serverHub.fsBootstrapUrl(), wait_until="load")
    pageDashboard.wait_for_selector(
        S_TILE + ":not(.container-tile--locked)", timeout=90000)
    pageDashboard.evaluate(_S_SURFACE_A_REMOVED_CONTAINERS_START)
    assert pageDashboard.evaluate(_S_SHOWN_BANNERS) == [
        "buildWarningsBanner", "configurationDriftBanner",
        "resourceAdvisoryBanner",
    ]
    pageDashboard.click(S_TILE + " .container-tile-main")
    pageDashboard.wait_for_selector("#modalConfirm", timeout=10000)
    pageDashboard.click("#btnConfirmOk")
    pageDashboard.wait_for_selector(
        f"text={S_HOST_WORKFLOW_NAME}", timeout=20000)
    assert pageDashboard.evaluate(_S_SHOWN_BANNERS) == []
    assert pageDashboard.listPageErrors == []
