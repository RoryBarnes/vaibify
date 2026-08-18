"""Converting a host sandbox into a containerized Project, in a browser.

A green Python suite says nothing about the frontend, so this drives
the real click -> wizard -> confirm -> POST -> registry-flip -> tile-flip
journey against the fail-closed fake Docker adapter. The two executors
that would otherwise attempt a REAL ``docker build`` and container start
are patched to no-ops IN THE LIVE SERVER (uvicorn runs in this process),
so the journey exercises the frontend and the real HTTP + registry
boundary without a daemon -- exactly what the host lane exists for.

The container name typed on the Name page is kept DISTINCT from the host
basename, so a flip that read the wrong field could not pass.
"""

import time

import pytest

from tests.browser.conftest import S_HOST_PROJECT_READY
from tests.browser.fakeDockerAdapter import S_CONTAINER_NAME


pytestmark = pytest.mark.browser


S_NEW_CONTAINER_NAME = "hostLaneReadyBox"


@pytest.fixture(autouse=True)
def fixtureNoRealDockerBuildOrStart(monkeypatch):
    """Patch the build + start executors in the live in-process server.

    Without this the convert journey's follow-on build would launch a
    real ``docker build`` and a real start against a daemon the lane
    does not have. Patched to no-ops, the routes answer success and the
    frontend flips the tile -- the surface under test -- with nothing
    touching Docker.
    """
    monkeypatch.setattr(
        "vaibify.gui.buildRoutes._fnExecuteBuild",
        lambda dictProject, bNoCache, dictProgress: None,
    )
    monkeypatch.setattr(
        "vaibify.gui.startReservation._fsExecuteReservedStart",
        lambda sName, reservation, configProject: "converted123",
    )


def _fnWaitForPicker(page, serverHub):
    page.goto(serverHub.fsBootstrapUrl(), wait_until="load")
    page.wait_for_selector(
        f'.container-tile[data-name="{S_CONTAINER_NAME}"]', timeout=10000,
    )


def _fnOpenHostTileMenu(page, sName):
    page.click(
        f'.container-tile[data-name="{sName}"] .container-tile-actions',
    )


def testOnlyAHostTileOffersConvertToProject(pageDashboard, serverHub):
    """The action is host-only: a container is already a Project.

    Asserted on both tiles in the one list, so a menu item rendered
    unconditionally would fail here rather than mislead a researcher. A
    host sandbox that has not graduated offers "Make a Project…".
    """
    _fnWaitForPicker(pageDashboard, serverHub)
    elHostAction = pageDashboard.query_selector(
        f'.container-tile[data-name="{S_HOST_PROJECT_READY}"] '
        '.container-menu-item[data-action="convert"]',
    )
    assert elHostAction is not None, "the host tile offers no Convert action"
    assert "Make a Project" in elHostAction.text_content()
    assert pageDashboard.query_selector(
        f'.container-tile[data-name="{S_CONTAINER_NAME}"] '
        '.container-menu-item[data-action="convert"]',
    ) is None, "a containerized tile offered Convert"


def _fnOpenConvertMenuAndChooseContainer(page):
    """Open the sandbox's wizard and pick the Containerized destination.

    A host sandbox now opens on the destination choice, so the container
    flow the rest of this file exercises is reached by choosing
    "Containerized Project" and advancing to the Name page.
    """
    _fnOpenHostTileMenu(page, S_HOST_PROJECT_READY)
    page.click(
        f'.container-tile[data-name="{S_HOST_PROJECT_READY}"] '
        '.container-menu-item[data-action="convert"]',
    )
    page.wait_for_selector("#modalCreateWizard", timeout=5000)
    assert page.text_content(
        "#wizardStepTitle",
    ).strip() == "How to become a Project"
    page.click('.add-choice-card[data-destination="container"]')
    page.wait_for_timeout(150)
    page.click("#btnWizardNext")
    page.wait_for_timeout(200)


def testConvertWizardOpensOnNameWithADockerSafePrefill(
    pageDashboard, serverHub,
):
    """After choosing container, the Name page is pre-filled Docker-safe."""
    _fnWaitForPicker(pageDashboard, serverHub)
    _fnOpenConvertMenuAndChooseContainer(pageDashboard)
    assert pageDashboard.text_content(
        "#wizardStepTitle",
    ).strip() == "Project Name"
    sPrefill = pageDashboard.input_value("#inputWizardProjectName")
    assert sPrefill, "the Name page opened with no suggestion"
    import re
    assert re.match(r"^[a-zA-Z0-9][a-zA-Z0-9_.-]*$", sPrefill), sPrefill
    assert pageDashboard.listPageErrors == []


def _fnWalkConvertWizardToSummary(page):
    """From the open Name page, type a new name and advance to Summary."""
    page.fill("#inputWizardProjectName", S_NEW_CONTAINER_NAME)
    for _iStep in range(5):
        page.click("#btnWizardNext")
        page.wait_for_timeout(200)


def testConvertSummaryNamesTheNewContainerAndOmitsTemplate(
    pageDashboard, serverHub,
):
    """A conversion has no Template; its summary names the new container."""
    _fnWaitForPicker(pageDashboard, serverHub)
    _fnOpenConvertMenuAndChooseContainer(pageDashboard)
    _fnWalkConvertWizardToSummary(pageDashboard)
    assert pageDashboard.text_content(
        "#wizardStepTitle",
    ).strip() == "Summary"
    assert pageDashboard.text_content(
        "#btnWizardNext",
    ).strip() == "Convert"
    sSummary = pageDashboard.text_content("#wizardStepContent")
    assert "New container name" in sSummary
    assert S_NEW_CONTAINER_NAME in sSummary
    assert "Template" not in sSummary, (
        "the conversion summary states a template it never chose: "
        + sSummary
    )


@pytest.mark.falsification
def testConvertingFlipsTheTileFromHostToContainer(
    pageDashboard, serverHub,
):
    """The whole journey: click -> confirm -> POST -> registry + tile flip.

    Kills: a convert submit that never re-registers the project, which
    would leave the host tile unchanged and the researcher with no
    container to build.
    """
    from vaibify.config import registryManager
    _fnWaitForPicker(pageDashboard, serverHub)
    _fnOpenConvertMenuAndChooseContainer(pageDashboard)
    _fnWalkConvertWizardToSummary(pageDashboard)
    pageDashboard.click("#btnWizardNext")
    # The one confirm modal, warning before the irreversible-ish step.
    pageDashboard.wait_for_selector("#modalConfirm", timeout=5000)
    sBody = pageDashboard.text_content("#modalConfirm")
    assert "Re-register" in sBody
    assert S_NEW_CONTAINER_NAME in sBody
    assert pageDashboard.text_content(
        "#btnConfirmOk",
    ).strip() == "Convert and build"
    pageDashboard.click("#btnConfirmOk")
    # The registry flip is the server-side truth: poll the isolated
    # registry until the host entry is gone and the new container exists.
    fDeadline = time.monotonic() + 15.0
    while time.monotonic() < fDeadline:
        if (registryManager.fdictGetProject(S_HOST_PROJECT_READY) is None
                and registryManager.fdictGetProject(
                    S_NEW_CONTAINER_NAME) is not None):
            break
        pageDashboard.wait_for_timeout(150)
    dictConverted = registryManager.fdictGetProject(S_NEW_CONTAINER_NAME)
    assert dictConverted is not None, (
        "the convert POST never re-registered the project"
    )
    assert dictConverted["sMode"] == "container"
    assert registryManager.fdictGetProject(S_HOST_PROJECT_READY) is None
    # The tile flip is the frontend truth: the reload fnBuildContainer
    # runs in its finally must render the project as a container.
    pageDashboard.wait_for_selector(
        f'.container-tile[data-name="{S_NEW_CONTAINER_NAME}"]'
        '[data-mode="container"]',
        timeout=15000,
    )
    assert pageDashboard.query_selector(
        f'.container-tile[data-name="{S_HOST_PROJECT_READY}"]',
    ) is None, "the old host tile survived the conversion"
    assert pageDashboard.listPageErrors == []
