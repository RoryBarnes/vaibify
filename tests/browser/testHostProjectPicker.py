"""The picker, in a real browser, with host and container tiles side by side.

A green Python suite says nothing about the frontend, and this chunk is
entirely frontend: what a host tile renders, what it does NOT render,
and what happens when the researcher clicks it. Every assertion below
is made against BOTH tiles in the same list, because the failure that
matters is not "the host tile is wrong" -- it is "the host tile is
right and the container tile beside it stopped working".

Three specific traps this lane is here to catch:

* **The build trap.** A host project has no image, so its status is
  never "not built"; if it fell into the container click path it would
  be offered a build that can never happen
  (``fnHandleContainerClick``'s ``not built -> click -> build``).
* **The missing resource id.** A host registry entry carries no
  ``sContainerId``. If the tile does not put the NAME there, the click
  path resolves an empty id and returns in silence -- nothing happens,
  no error, no diagnosis.
* **Container-only controls.** Start/stop/restart/rebuild and the
  settings gear are all Docker machinery. The server refuses them, but
  a control that is offered and then refused teaches the researcher
  that vaibify is broken rather than that the action does not apply.
"""

import pytest

from tests.browser.conftest import (
    S_HOST_PROJECT_MISSING,
    S_HOST_PROJECT_READY,
)
from tests.browser.fakeDockerAdapter import S_CONTAINER_NAME


pytestmark = pytest.mark.browser


def _fnWaitForPicker(page, serverHub):
    """Load the dashboard and wait for the container list to render."""
    page.goto(serverHub.fsBootstrapUrl(), wait_until="load")
    page.wait_for_selector(
        f'.container-tile[data-name="{S_CONTAINER_NAME}"]',
        timeout=10000,
    )


def _fsTileMode(page, sName):
    """Return the rendered mode attribute for one tile."""
    return page.get_attribute(
        f'.container-tile[data-name="{sName}"]', "data-mode",
    )


def testEveryRegisteredProjectRendersWithItsDeclaredMode(
    pageDashboard, serverHub,
):
    """Host and container tiles appear together, each labelled."""
    _fnWaitForPicker(pageDashboard, serverHub)
    assert _fsTileMode(pageDashboard, S_CONTAINER_NAME) == "container"
    assert _fsTileMode(pageDashboard, S_HOST_PROJECT_READY) == "host"
    assert _fsTileMode(pageDashboard, S_HOST_PROJECT_MISSING) == "host"
    assert pageDashboard.listPageErrors == []


@pytest.mark.falsification
def testAHostTileCarriesItsNameAsTheResourceId(
    pageDashboard, serverHub,
):
    """Without this the click path resolves nothing and says nothing.

    Kills: the tile taking sContainerId straight from the registry
    entry, which a host project does not have -- the tile renders, the
    click resolves an empty id, and nothing happens at all.
    """
    _fnWaitForPicker(pageDashboard, serverHub)
    sResourceId = pageDashboard.get_attribute(
        f'.container-tile[data-name="{S_HOST_PROJECT_READY}"]',
        "data-container-id",
    )
    assert sResourceId == S_HOST_PROJECT_READY


def testAReadyHostProjectIsNotOfferedABuild(pageDashboard, serverHub):
    """A host tile never renders the status that triggers a build."""
    _fnWaitForPicker(pageDashboard, serverHub)
    sClass = pageDashboard.get_attribute(
        f'.container-tile[data-name="{S_HOST_PROJECT_READY}"] '
        '.status-dot',
        "class",
    )
    assert "status-host-ready" in sClass
    assert "status-not-built" not in sClass


def testAMissingHostProjectShowsThePathThatIsGone(
    pageDashboard, serverHub,
):
    """The remedy is the path: renamed folder, or a stale entry."""
    _fnWaitForPicker(pageDashboard, serverHub)
    sClass = pageDashboard.get_attribute(
        f'.container-tile[data-name="{S_HOST_PROJECT_MISSING}"] '
        '.status-dot',
        "class",
    )
    assert "status-missing" in sClass
    sNote = pageDashboard.text_content(
        f'.container-tile[data-name="{S_HOST_PROJECT_MISSING}"] '
        '.container-tile-note',
    )
    assert S_HOST_PROJECT_MISSING in sNote


T_CONTAINER_ONLY_ACTIONS = (
    "start", "cancel-start", "stop", "restart",
    "rebuild", "force-rebuild",
)


@pytest.mark.falsification
def testAHostTileOffersNoContainerLifecycleAction(
    pageDashboard, serverHub,
):
    """None of the Docker actions appear on a host tile's menu.

    Kills: rendering the container-only menu items unconditionally, so
    a host project is offered Start, Stop and Rebuild and every one of
    them comes back refused.
    """
    _fnWaitForPicker(pageDashboard, serverHub)
    for sAction in T_CONTAINER_ONLY_ACTIONS:
        assert pageDashboard.query_selector(
            f'.container-tile[data-name="{S_HOST_PROJECT_READY}"] '
            f'.container-menu-item[data-action="{sAction}"]',
        ) is None, f"host tile offered {sAction}"
    assert pageDashboard.query_selector(
        f'.container-tile[data-name="{S_HOST_PROJECT_READY}"] '
        '.container-menu-item[data-action="remove"]',
    ) is not None, "a host project must still be removable from the list"


@pytest.mark.falsification
def testTheContainerTileKeepsEveryLifecycleAction(
    pageDashboard, serverHub,
):
    """The other direction: suppression is scoped to host tiles.

    Kills: suppressing the menu items for every tile, which leaves a
    containerized project with no way to start, stop or rebuild it.
    """
    _fnWaitForPicker(pageDashboard, serverHub)
    for sAction in T_CONTAINER_ONLY_ACTIONS:
        assert pageDashboard.query_selector(
            f'.container-tile[data-name="{S_CONTAINER_NAME}"] '
            f'.container-menu-item[data-action="{sAction}"]',
        ) is not None, f"container tile lost {sAction}"


def testTheSettingsGearIsOnTheContainerTileOnly(
    pageDashboard, serverHub,
):
    """Both directions of the same suppression, in one assertion pair."""
    _fnWaitForPicker(pageDashboard, serverHub)
    assert pageDashboard.query_selector(
        f'.container-tile[data-name="{S_CONTAINER_NAME}"] '
        '.container-tile-gear',
    ) is not None
    assert pageDashboard.query_selector(
        f'.container-tile[data-name="{S_HOST_PROJECT_READY}"] '
        '.container-tile-gear',
    ) is None


@pytest.mark.falsification
def testClickingAMissingHostProjectRefusesAndClaimsNothing(
    pageDashboard, serverHub,
):
    """A gone directory is named, not silently claimed and connected.

    Kills: removing the host branch from the click path, so a host
    tile falls through to the container path -- which tries to START
    a container the project does not have.
    """
    _fnWaitForPicker(pageDashboard, serverHub)
    pageDashboard.click(
        f'.container-tile[data-name="{S_HOST_PROJECT_MISSING}"] '
        '.container-tile-main',
    )
    pageDashboard.wait_for_selector(".toast", timeout=5000)
    sToast = pageDashboard.text_content(".toast")
    assert S_HOST_PROJECT_MISSING in sToast
    assert "vaibify.yml" in sToast
    assert (
        serverHub.app.state.dictContainerOwners.get(
            S_HOST_PROJECT_MISSING,
        ) is None
    ), "a project that is not on disk was claimed anyway"
    assert pageDashboard.listPageErrors == []
