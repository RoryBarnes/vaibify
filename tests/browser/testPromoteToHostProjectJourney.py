"""Promoting a host sandbox into a host-based Project, in a browser.

A green Python suite says nothing about the frontend, so this drives the
real click -> destination-choice -> name -> summary -> POST ->
registry-flip -> tile-flip journey against the fail-closed fake Docker
adapter. Promotion opens NO container connection and builds NO image, so
NOTHING here is patched to a no-op the way the container-convert journey
patches the build executor -- the point is that a host Project needs no
daemon at all.

The new Project name is kept DISTINCT from the host basename AND carries
a space (host-safe, never a Docker name), so a flip that read the wrong
field, or a path that applied the Docker rule, could not pass.
"""

import time

import pytest

from tests.browser.conftest import S_HOST_PROJECT_READY
from tests.browser.fakeDockerAdapter import S_CONTAINER_NAME


pytestmark = pytest.mark.browser


# Distinct from the basename "hostLaneReady" and carrying a space: host
# names allow spaces, and the promotion must NOT apply the Docker rule.
S_NEW_PROJECT_NAME = "Ready Host Project"


def _fnWaitForPicker(page, serverHub):
    page.goto(serverHub.fsBootstrapUrl(), wait_until="load")
    page.wait_for_selector(
        f'.container-tile[data-name="{S_CONTAINER_NAME}"]', timeout=10000,
    )


def _fnOpenPromoteWizardAndChooseHost(page):
    """Open the sandbox's wizard and pick the Host Project destination."""
    page.click(
        f'.container-tile[data-name="{S_HOST_PROJECT_READY}"] '
        '.container-tile-actions',
    )
    page.click(
        f'.container-tile[data-name="{S_HOST_PROJECT_READY}"] '
        '.container-menu-item[data-action="convert"]',
    )
    page.wait_for_selector("#modalCreateWizard", timeout=5000)
    assert page.text_content(
        "#wizardStepTitle",
    ).strip() == "How to become a Project"
    page.click('.add-choice-card[data-destination="host"]')
    page.wait_for_timeout(150)
    page.click("#btnWizardNext")
    page.wait_for_timeout(200)


def testTheSandboxTileOffersMakeAProject(pageDashboard, serverHub):
    """A host sandbox reads as a sandbox: its action is "Make a Project…"."""
    _fnWaitForPicker(pageDashboard, serverHub)
    elAction = pageDashboard.query_selector(
        f'.container-tile[data-name="{S_HOST_PROJECT_READY}"] '
        '.container-menu-item[data-action="convert"]',
    )
    assert elAction is not None
    assert "Make a Project" in elAction.text_content()
    # It is not yet a Project.
    elTile = pageDashboard.query_selector(
        f'.container-tile[data-name="{S_HOST_PROJECT_READY}"]',
    )
    assert elTile.get_attribute("data-is-project") == "false"


def testHostBranchCollectsOnlyANameAndASummary(pageDashboard, serverHub):
    """The host branch skips every container page: Name then Summary."""
    _fnWaitForPicker(pageDashboard, serverHub)
    _fnOpenPromoteWizardAndChooseHost(pageDashboard)
    assert pageDashboard.text_content(
        "#wizardStepTitle",
    ).strip() == "Project Name"
    pageDashboard.fill("#inputWizardProjectName", S_NEW_PROJECT_NAME)
    pageDashboard.click("#btnWizardNext")
    pageDashboard.wait_for_timeout(200)
    assert pageDashboard.text_content(
        "#wizardStepTitle",
    ).strip() == "Summary"
    assert pageDashboard.text_content("#btnWizardNext").strip() == "Promote"
    sSummary = pageDashboard.text_content("#wizardStepContent")
    assert "New Project name" in sSummary
    assert S_NEW_PROJECT_NAME in sSummary
    assert "no container is built" in sSummary
    # No container questions leaked into the host branch.
    assert "Python" not in sSummary, sSummary
    assert "Repositories" not in sSummary, sSummary
    assert pageDashboard.listPageErrors == []


@pytest.mark.falsification
def testPromotingFlipsTheTileToAHostProjectWithNoBuild(
    pageDashboard, serverHub,
):
    """The whole journey: choose host -> name -> Promote -> registry + tile.

    Kills: a promote submit that never re-registers the project (the tile
    would stay a sandbox), or one that flips the mode to container (a
    build the host branch must never trigger). The uncontained badge must
    survive -- a host Project is still host mode.
    """
    from vaibify.config import registryManager
    _fnWaitForPicker(pageDashboard, serverHub)
    _fnOpenPromoteWizardAndChooseHost(pageDashboard)
    pageDashboard.fill("#inputWizardProjectName", S_NEW_PROJECT_NAME)
    pageDashboard.click("#btnWizardNext")
    pageDashboard.wait_for_timeout(200)
    # Promotion has NO confirm modal (unlike the build-triggering convert).
    pageDashboard.click("#btnWizardNext")
    # The registry flip is the server-side truth: the old name is gone and
    # the new name is a host Project.
    fDeadline = time.monotonic() + 15.0
    while time.monotonic() < fDeadline:
        if (registryManager.fdictGetProject(S_HOST_PROJECT_READY) is None
                and registryManager.fdictGetProject(
                    S_NEW_PROJECT_NAME) is not None):
            break
        pageDashboard.wait_for_timeout(150)
    dictPromoted = registryManager.fdictGetProject(S_NEW_PROJECT_NAME)
    assert dictPromoted is not None, (
        "the promote POST never re-registered the project"
    )
    assert dictPromoted["sMode"] == "host", (
        "promotion must NOT flip the mode to container"
    )
    assert dictPromoted["bIsProject"] is True
    assert registryManager.fdictGetProject(S_HOST_PROJECT_READY) is None
    # No build modal was ever shown -- promotion builds nothing.
    elBuild = pageDashboard.query_selector("#modalBuildProgress")
    assert elBuild is None or not elBuild.is_visible(), (
        "a build progress modal appeared for a host promotion"
    )
    # The tile flip is the frontend truth: still host, now a Project, and
    # still uncontained.
    sTile = (
        f'.container-tile[data-name="{S_NEW_PROJECT_NAME}"]'
        '[data-mode="host"][data-is-project="true"]'
    )
    pageDashboard.wait_for_selector(sTile, timeout=15000)
    assert pageDashboard.query_selector(
        f'.container-tile[data-name="{S_HOST_PROJECT_READY}"]',
    ) is None, "the old sandbox tile survived the promotion"
    elChip = pageDashboard.query_selector(
        sTile + ' .containment-chip--direct',
    )
    assert elChip is not None, "the uncontained badge was removed"
    assert "uncontained" in elChip.text_content()
    # A host Project is not offered promotion again -- it offers
    # containerization instead.
    elAction = pageDashboard.query_selector(
        sTile + ' .container-menu-item[data-action="convert"]',
    )
    assert elAction is not None
    assert "Containerize" in elAction.text_content()
    assert pageDashboard.listPageErrors == []
