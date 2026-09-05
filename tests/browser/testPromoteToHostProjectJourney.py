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


def _fnSeedAPromotableSandbox(serverHub, sSandboxName):
    """Register a fresh host sandbox for one promotion test.

    Promotion is reached from INSIDE the project (the Files panel's
    "Convert to Project" bar) since the 2026-09-04 ruling, and entering
    a project claims its lease. The lease outlives the test, so a
    second test reusing the shared sandbox meets a tile locked "In use
    in another browser session" and fails on click interception -- a
    failure that says nothing about promotion. Seeding per test is the
    pattern the convert journey already established for the same
    hazard.
    """
    import os
    import subprocess
    from vaibify.config import registryManager
    sDirectory = os.path.join(serverHub.sHome, sSandboxName)
    os.makedirs(sDirectory, exist_ok=True)
    with open(os.path.join(sDirectory, "analysis.py"), "w") as fileEntry:
        fileEntry.write("import json\n")
    with open(
        os.path.join(sDirectory, "vaibify.yml"), "w",
    ) as fileConfig:
        fileConfig.write(f"projectName: {sSandboxName}\n")
    subprocess.run(
        ["git", "init", "-q"], cwd=sDirectory, check=True,
        capture_output=True,
    )
    registryManager.fnAddProject(sDirectory, sMode="host")
    return sDirectory


def _fnOpenPromoteWizardAndChooseHost(page, sSandboxName):
    """Open the promote wizard from the Files panel and choose Host Project.

    Driven through the Files panel's "Convert to Project" bar, which is
    the promotion door as of the 2026-09-04 ruling. The tile's kebab
    containerizes outright and no longer reaches this choice, so a test
    that still drove it there would be asserting a path a researcher
    cannot take.
    """
    _fnEnterHostFilesPanel(page, sSandboxName)
    page.wait_for_selector(
        "#fileConvertToProjectBar", state="visible", timeout=10000,
    )
    page.click("#btnConvertToProject")
    page.wait_for_selector("#modalCreateWizard", timeout=5000)
    assert page.text_content(
        "#wizardStepTitle",
    ).strip() == "How to become a Project"
    page.click('.add-choice-card[data-destination="host"]')
    page.wait_for_timeout(150)
    page.click("#btnWizardNext")
    page.wait_for_timeout(200)


def _fnEnterHostFilesPanel(page, sName):
    """Enter a host project's no-workflow view and open its Files tab."""
    page.click(
        f'.container-tile[data-name="{sName}"] .container-tile-main',
    )
    # The uncontained warning may or may not appear (its per-directory
    # acknowledgement is host-global and can persist across journeys),
    # so dismiss it only if it is there.
    page.wait_for_timeout(400)
    if page.is_visible("#modalConfirm"):
        page.click("#btnConfirmOk")
    page.wait_for_selector("#btnNoWorkflow", timeout=20000)
    page.click("#btnNoWorkflow")
    page.wait_for_selector(
        '.left-tab[data-panel="files"]', state="visible", timeout=20000,
    )
    page.click('.left-tab[data-panel="files"]')
    page.wait_for_selector(
        "#panelFiles.active", state="visible", timeout=10000,
    )


def testTheSandboxTileOffersContainerizeEnvironment(
    pageDashboard, serverHub,
):
    """A host sandbox's action reads "Containerize Environment".

    One label serves both host states since 2026-09-04; the
    sandbox/Project difference survives in ``data-is-project``,
    asserted below, because it still decides whether the wizard
    shows a destination step.
    """
    _fnWaitForPicker(pageDashboard, serverHub)
    elAction = pageDashboard.query_selector(
        f'.container-tile[data-name="{S_HOST_PROJECT_READY}"] '
        '.container-menu-item[data-action="convert"]',
    )
    assert elAction is not None
    assert "Containerize Environment" in elAction.text_content()
    # It is not yet a Project.
    elTile = pageDashboard.query_selector(
        f'.container-tile[data-name="{S_HOST_PROJECT_READY}"]',
    )
    assert elTile.get_attribute("data-is-project") == "false"


def testHostBranchCollectsOnlyANameAndASummary(pageDashboard, serverHub):
    """The host branch skips every container page: Name then Summary."""
    sSandbox = "promoteSummarySandbox"
    _fnSeedAPromotableSandbox(serverHub, sSandbox)
    _fnWaitForPicker(pageDashboard, serverHub)
    pageDashboard.wait_for_selector(
        f'.container-tile[data-name="{sSandbox}"]', timeout=10000,
    )
    _fnOpenPromoteWizardAndChooseHost(pageDashboard, sSandbox)
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
def testPromotingFlipsTheRegistryWithNoBuild(
    pageDashboard, serverHub,
):
    """The whole journey: choose host -> name -> Promote -> registry.

    Renamed from ...FlipsTheTile... on 2026-09-04: the tile assertions
    moved to their own test, because promotion's re-entry carries this
    tab through the hub it would have to observe.

    Kills: a promote submit that never re-registers the project (the
    entry would stay a sandbox, bIsProject False), or one that flips
    the mode to container -- a build the host branch must never
    trigger.
    """
    from vaibify.config import registryManager
    sSandbox = "promoteFlipSandbox"
    _fnSeedAPromotableSandbox(serverHub, sSandbox)
    _fnWaitForPicker(pageDashboard, serverHub)
    pageDashboard.wait_for_selector(
        f'.container-tile[data-name="{sSandbox}"]', timeout=10000,
    )
    _fnOpenPromoteWizardAndChooseHost(pageDashboard, sSandbox)
    pageDashboard.fill("#inputWizardProjectName", S_NEW_PROJECT_NAME)
    pageDashboard.click("#btnWizardNext")
    pageDashboard.wait_for_timeout(200)
    # Promotion has NO confirm modal (unlike the build-triggering convert).
    pageDashboard.click("#btnWizardNext")
    # The registry flip is the server-side truth: the old name is gone and
    # the new name is a host Project.
    fDeadline = time.monotonic() + 15.0
    while time.monotonic() < fDeadline:
        if (registryManager.fdictGetProject(sSandbox) is None
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
    assert registryManager.fdictGetProject(sSandbox) is None
    # No build modal was ever shown -- promotion builds nothing.
    elBuild = pageDashboard.query_selector("#modalBuildProgress")
    assert elBuild is None or not elBuild.is_visible(), (
        "a build progress modal appeared for a host promotion"
    )
    # Promotion from inside RE-ENTERS the renamed project, so the tab
    # ends in the dashboard. Check the in-project claim while it is on
    # screen: the Files panel of a freshly-promoted host Project must
    # NOT offer "Convert to Project" -- it already is one. The bar's
    # own id, hidden rather than merely absent.
    pageDashboard.wait_for_selector("#mainLayout.active", timeout=20000)
    pageDashboard.click('.left-tab[data-panel="files"]')
    pageDashboard.wait_for_selector(
        "#panelFiles.active", state="visible", timeout=10000,
    )
    assert pageDashboard.is_hidden("#fileConvertToProjectBar"), (
        "a host Project's Files panel still offered 'Convert to Project'"
    )
    # The promoted TILE is asserted by
    # testAPromotedProjectRendersAsAProjectInTheHub below, not here.
    # Re-entry tears down to the Environment hub, reloads the tiles and
    # passes the Project hub behind the promotion curtain on its way to
    # the step viewer (fnShowPromotionCurtain), so a tile assertion
    # made from this tab would be racing a transition rather than
    # observing a state.
    assert pageDashboard.listPageErrors == []


@pytest.mark.falsification
def testAPromotedProjectRendersAsAProjectInTheHub(
    pageDashboard, serverHub,
):
    """A promoted entry's TILE reads host, Project, and uncontained.

    Split from the journey above (2026-09-04). That test drives the
    promotion, and the promotion's own hand-off carries the tab through
    the hub and out to the step viewer, so it cannot then observe the
    hub it passed through. Rendering is a separate question from the
    flow that produces it, so the registry is moved directly here and
    only the DISPLAY is driven -- which is also what lets this assert
    the failure mode that matters, a promoted project still rendering
    as a sandbox.

    Kills: dropping bIsProject from the tile's dataset (the tile would
    read as a sandbox and offer the wrong affordances), or rendering a
    promoted host Project as contained.
    """
    from vaibify.config import registryManager
    sSandbox = "promotedTileSandbox"
    sPromoted = "Promoted Tile Project"
    _fnSeedAPromotableSandbox(serverHub, sSandbox)
    registryManager.fnPromoteHostProject(sSandbox, sPromoted)
    _fnWaitForPicker(pageDashboard, serverHub)
    sTile = (
        f'.container-tile[data-name="{sPromoted}"]'
        '[data-mode="host"][data-is-project="true"]'
    )
    pageDashboard.wait_for_selector(sTile, timeout=15000)
    assert pageDashboard.query_selector(
        f'.container-tile[data-name="{sSandbox}"]',
    ) is None, "the old sandbox tile survived the promotion"
    elChip = pageDashboard.query_selector(
        sTile + ' .containment-chip--direct',
    )
    assert elChip is not None, "the uncontained badge was removed"
    assert "uncontained" in elChip.text_content()
    # A promoted host Project carries the same label as a sandbox; what
    # changed for it is that the wizard skips the destination step, not
    # the wording of the menu item.
    elAction = pageDashboard.query_selector(
        sTile + ' .container-menu-item[data-action="convert"]',
    )
    assert elAction is not None
    assert "Containerize Environment" in elAction.text_content()
    assert pageDashboard.listPageErrors == []
