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

import time

import pytest

from tests.browser.conftest import (
    S_HOST_PROJECT_MISSING,
    S_HOST_PROJECT_READY,
)
from tests.browser.fakeDockerAdapter import S_CONTAINER_NAME


pytestmark = pytest.mark.browser


@pytest.fixture(autouse=True)
def fixtureDropClaimsBetweenJourneys(serverHub):
    """Give every claim back after each journey.

    The hub is module-scoped and the browser page is not, so each test
    arrives with a fresh session and a fresh lease. A journey that
    claims a project and stops -- which several here do deliberately,
    because the WARNING is what they are testing -- would otherwise
    leave that project owned by a lease nobody holds any more, and the
    next journey's claim is refused 409 by a session that no longer
    exists. Released the way the hub's own shutdown hook does: drop
    the flock, then the owner record.
    """
    yield
    from vaibify.config.containerLock import fnReleaseContainerLock
    dictContainerOwners = serverHub.app.state.dictContainerOwners
    for _sName, recordOwner in list(dictContainerOwners.items()):
        fileHandle = getattr(recordOwner, "fileHandleLock", None)
        if fileHandle is not None:
            try:
                fnReleaseContainerLock(fileHandle)
            except OSError:
                pass
    dictContainerOwners.clear()
    serverHub.app.state.dictSessionOwner.clear()


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


# ---------------------------------------------------------------------
# The warning, and the badge that outlives it
# ---------------------------------------------------------------------
#
# The disclosure is the whole product stance in one dialog: host mode
# runs your pipeline, and the container is what lets vaibify vouch for
# it. Three things about it are load-bearing rather than cosmetic.
#
# It comes AFTER the claim, because arbitration must run first -- a
# researcher who reads and accepts a disclosure about a project another
# session is holding has accepted nothing. "Go back" therefore has to
# RELEASE what the claim just took, or declining leaves the project
# held by a tab that never opened it. And the badge is permanent: the
# warning is dismissible and the mode is not.


def _fnOpenTheReadyHostProject(page, serverHub):
    """Click the ready host tile and wait for the warning."""
    _fnWaitForPicker(page, serverHub)
    page.click(
        f'.container-tile[data-name="{S_HOST_PROJECT_READY}"] '
        '.container-tile-main',
    )
    page.wait_for_selector("#modalConfirm", timeout=5000)


@pytest.mark.falsification
def testEnteringAHostProjectWarnsBeforeTheProjectOpens(
    pageDashboard, serverHub,
):
    """The disclosure names what is given up, by name.

    Kills: entering a host project without the warning, which ships
    uncontained execution with no disclosure at all.
    """
    _fnOpenTheReadyHostProject(pageDashboard, serverHub)
    sBody = pageDashboard.text_content("#modalConfirm")
    assert "not contained" in sBody
    assert "Level 3" in sBody
    assert "Supervised" in sBody
    assert "full user authority" in sBody
    assert pageDashboard.query_selector(
        "#checkboxConfirmModal",
    ) is not None, "the don't-warn-again choice is missing"


@pytest.mark.falsification
def testGoingBackReleasesTheProjectItJustClaimed(
    pageDashboard, serverHub,
):
    """Declining must not leave the project held by nobody.

    Kills: dropping the cancel callback, so "Go back" closes the
    dialog and the claim stands -- the project renders as in use by a
    session that never opened it, until the grace reaper notices.
    """
    _fnOpenTheReadyHostProject(pageDashboard, serverHub)
    assert serverHub.app.state.dictContainerOwners.get(
        S_HOST_PROJECT_READY,
    ) is not None, "the claim did not happen, so this proves nothing"
    pageDashboard.click("#btnConfirmCancel")
    pageDashboard.wait_for_function(
        """() => document.getElementById('modalConfirm') === null""",
        timeout=5000,
    )
    # The release is an HTTP round-trip the cancel callback fires; the
    # modal closing does not mean it has landed. Poll with a deadline
    # instead of a fixed sleep — a dropped callback (the mutation this
    # test kills) still fails here, because no wait makes a request
    # that was never sent arrive.
    fDeadline = time.monotonic() + 5.0
    while time.monotonic() < fDeadline:
        if serverHub.app.state.dictContainerOwners.get(
            S_HOST_PROJECT_READY,
        ) is None:
            break
        pageDashboard.wait_for_timeout(100)
    assert serverHub.app.state.dictContainerOwners.get(
        S_HOST_PROJECT_READY,
    ) is None, "going back left the project claimed"
    assert pageDashboard.listPageErrors == []


def testTheWarningNamesItsTwoChoicesInTheResearchersWords(
    pageDashboard, serverHub,
):
    """Not "Cancel"/"Confirm": the buttons say what they do."""
    _fnOpenTheReadyHostProject(pageDashboard, serverHub)
    assert pageDashboard.text_content(
        "#btnConfirmCancel",
    ).strip() == "Go back"
    assert pageDashboard.text_content(
        "#btnConfirmOk",
    ).strip() == "I understand, continue"


@pytest.mark.falsification
def testContinuingShowsThePermanentUncontainedBadge(
    pageDashboard, serverHub,
):
    """The warning is dismissible; the mode is not.

    Kills: never showing the badge, which leaves a researcher with no
    standing indication that their commands are running with their own
    authority rather than inside a container.
    """
    _fnOpenTheReadyHostProject(pageDashboard, serverHub)
    pageDashboard.click("#btnConfirmOk")
    pageDashboard.wait_for_selector(
        "#hostModeBadgePicker:visible", timeout=10000,
    )
    sBadge = pageDashboard.text_content("#hostModeBadgePicker")
    assert "HOST MODE" in sBadge
    assert "uncontained" in sBadge
    assert pageDashboard.text_content(
        "#activeResourceLabel",
    ).strip() == "Directory:"


@pytest.mark.falsification
def testAContainerProjectShowsNoUncontainedBadge(
    pageDashboard, serverHub,
):
    """The other direction: the badge is a claim, not decoration.

    Kills: showing the badge for every project, which would tell a
    researcher inside a container that their work is uncontained --
    a false alarm that trains them to ignore the real one.
    """
    _fnWaitForPicker(pageDashboard, serverHub)
    # Driven with the mode the container TILE declares -- the value the
    # registry listing put there, read back through the real renderer
    # and the real applier. Honest about its limit: it stops one step
    # short of the click path, which in this lane would trigger a
    # BUILD (the fake adapter reports the container as not built) and
    # test a different journey entirely. The host direction above IS
    # driven end to end, so what is unproven here is only that the
    # container path calls the applier -- which the same call site
    # serves for both modes.
    pageDashboard.evaluate(
        """(sName) => VaibifyApp.fnApplyProjectMode(
            document.querySelector(
                '.container-tile[data-name="' + sName + '"]'
            ).dataset.mode
        )""",
        S_CONTAINER_NAME,
    )
    assert pageDashboard.is_hidden("#hostModeBadgePicker"), (
        "a containerized project was labelled uncontained"
    )
    assert pageDashboard.is_hidden("#hostModeBadge")
    assert pageDashboard.text_content(
        "#activeResourceLabel",
    ).strip() == "Container:"


def testAcknowledgingSuppressesTheWarningOnTheNextEntry(
    pageDashboard, serverHub,
):
    """The preference is per project directory and it is honoured.

    Driven through the real preferences route and a real reload, not
    by inspecting a variable: the suppression has to survive the page,
    which is the whole reason it is not in localStorage.
    """
    _fnOpenTheReadyHostProject(pageDashboard, serverHub)
    pageDashboard.check("#checkboxConfirmModal")
    pageDashboard.click("#btnConfirmOk")
    pageDashboard.wait_for_selector(
        "#hostModeBadgePicker:visible", timeout=10000,
    )
    # Leave the project the way a researcher does, then come back.
    pageDashboard.click("#btnWorkflowBack")
    pageDashboard.wait_for_selector(
        f'.container-tile[data-name="{S_HOST_PROJECT_READY}"]'
        '[data-warning-acknowledged="true"]',
        timeout=10000,
    )
    pageDashboard.click(
        f'.container-tile[data-name="{S_HOST_PROJECT_READY}"] '
        '.container-tile-main',
    )
    pageDashboard.wait_for_selector(
        "#hostModeBadgePicker:visible", timeout=10000,
    )
    assert pageDashboard.query_selector("#modalConfirm") is None, (
        "the warning showed again for an acknowledged project"
    )


# ---------------------------------------------------------------------
# Adding a host project at all
# ---------------------------------------------------------------------
#
# Until the Add dialog offered the tier, a host project could be made
# only by calling the API -- which is not a feature, it is a test
# fixture. The tier is a MODE rather than a third way of adding, so
# choosing it re-offers the same two paths (an existing directory, or
# create from a template) instead of forcing one of them.


def _fnOpenTheAddDialog(page, serverHub):
    """Load the dashboard and open the Add dialog."""
    _fnWaitForPicker(page, serverHub)
    page.click("#btnAddContainer")
    page.wait_for_selector("#modalAddChoice", timeout=5000)


def testTheAddDialogAsksWhereTheWorkRunsFirst(pageDashboard, serverHub):
    """Stage 1 is the environment kind, and both kinds are offered.

    The host tier used to be a third card beside Add Existing and
    Create New, which put "Add Container" over a choice between a
    container and not-a-container.
    """
    _fnOpenTheAddDialog(pageDashboard, serverHub)
    sHostCard = pageDashboard.text_content("#btnChoiceKindHost")
    assert "This machine" in sHostCard
    assert "experimentation" in sHostCard
    sContainerCard = pageDashboard.text_content("#btnChoiceKindContainer")
    assert "Container" in sContainerCard
    assert pageDashboard.is_hidden("#addChoiceHowStage")


@pytest.mark.falsification
def testChoosingTheHostKindReOffersBothPathsWithTheDisclosure(
    pageDashboard, serverHub,
):
    """The kind is a stage, not a path, and it says what it costs.

    Kills: the host card being wired to one path only, which silently
    removes the other -- a researcher who already has a vaibify.yml
    directory could then only register it as a container.
    """
    _fnOpenTheAddDialog(pageDashboard, serverHub)
    pageDashboard.click("#btnChoiceKindHost")
    pageDashboard.wait_for_selector(
        "#addChoiceHowStage", state="visible", timeout=5000,
    )
    assert pageDashboard.is_hidden("#addChoiceCards")
    assert pageDashboard.is_visible("#btnChoiceAddExisting")
    assert pageDashboard.is_visible("#btnChoiceCreateNew")
    sNote = pageDashboard.text_content("#addChoiceHostNote")
    assert "full user authority" in sNote
    assert "Level 3" in sNote
    assert "This machine" in pageDashboard.text_content("#addChoiceTitle")


@pytest.mark.falsification
def testTheContainerKindTakesTheSameSecondStageWithoutTheDisclosure(
    pageDashboard, serverHub,
):
    """The other direction: one second stage, and the note is host-only.

    Kills: showing the uncontained disclosure for a container, which
    would teach a researcher to dismiss the one notice that matters.
    """
    _fnOpenTheAddDialog(pageDashboard, serverHub)
    pageDashboard.click("#btnChoiceKindContainer")
    pageDashboard.wait_for_selector(
        "#addChoiceHowStage", state="visible", timeout=5000,
    )
    assert pageDashboard.is_visible("#btnChoiceAddExisting")
    assert pageDashboard.is_hidden("#addChoiceHostNote")
    assert "Container" in pageDashboard.text_content("#addChoiceTitle")


@pytest.mark.falsification
def testTheHostCreateWizardSkipsEveryContainerPage(
    pageDashboard, serverHub,
):
    """Directory, template, summary -- and nothing about an image.

    Walked end to end through the real wizard, choosing a real
    directory and a real template, because the page list is only
    observable by advancing: a researcher discovers the Python-version
    page by arriving at it. There is no name page -- a host sandbox is
    named by its directory.

    Kills: the host wizard walking the container page list, which asks
    for a Python version, repositories to clone, image features and
    package lists for a container that will never exist, then records
    the answers where nothing reads them.
    """
    _fnOpenTheAddDialog(pageDashboard, serverHub)
    pageDashboard.click("#btnChoiceKindHost")
    pageDashboard.click("#btnChoiceCreateNew")
    pageDashboard.wait_for_selector("#modalCreateWizard", timeout=5000)
    listTitles = [pageDashboard.text_content("#wizardStepTitle").strip()]

    pageDashboard.click("#btnWizardChooseDirectory")
    pageDashboard.wait_for_selector(
        "#modalAddContainer", state="visible", timeout=5000,
    )
    pageDashboard.click("#btnAddContainerConfirm")
    pageDashboard.wait_for_selector(
        "#modalAddContainer", state="hidden", timeout=5000,
    )
    pageDashboard.click("#btnWizardNext")
    pageDashboard.wait_for_timeout(300)
    listTitles.append(pageDashboard.text_content("#wizardStepTitle").strip())

    pageDashboard.wait_for_selector(
        "#wizardStepContent .add-choice-card[data-template]",
        timeout=10000,
    )
    pageDashboard.click(
        "#wizardStepContent .add-choice-card[data-template]",
    )
    pageDashboard.click("#btnWizardNext")
    pageDashboard.wait_for_timeout(300)
    listTitles.append(pageDashboard.text_content("#wizardStepTitle").strip())

    # No Project Name page: a host sandbox is named by its directory, so
    # Template advances straight to Summary.
    assert listTitles == [
        "Project Directory", "Template", "Summary",
    ], listTitles
    assert pageDashboard.text_content(
        "#btnWizardNext",
    ).strip() == "Create"
    sSummary = pageDashboard.text_content("#wizardStepContent")
    assert "Host" in sSummary
    assert "Python" not in sSummary, (
        "the host summary states a setting nothing reads: " + sSummary
    )
    assert "Project Name" not in sSummary, (
        "the host summary names a sandbox identity nobody chose: "
        + sSummary
    )


def testTheContainerCreateWizardStillWalksEveryPage(
    pageDashboard, serverHub,
):
    """The other direction: the container wizard is unchanged."""
    _fnOpenTheAddDialog(pageDashboard, serverHub)
    pageDashboard.click("#btnChoiceKindContainer")
    pageDashboard.click("#btnChoiceCreateNew")
    pageDashboard.wait_for_selector("#modalCreateWizard", timeout=5000)
    iVisibleDots = pageDashboard.evaluate(
        """() => Array.from(
            document.querySelectorAll('.wizard-progress-step')
        ).filter((el) => el.style.display !== 'none').length"""
    )
    assert iVisibleDots == 8, (
        f"the container wizard lost pages: {iVisibleDots} shown"
    )


def testTheHostWizardShowsOnlyItsOwnProgressDots(
    pageDashboard, serverHub,
):
    """Three pages, three dots -- not three of eight that never arrive."""
    _fnOpenTheAddDialog(pageDashboard, serverHub)
    pageDashboard.click("#btnChoiceKindHost")
    pageDashboard.click("#btnChoiceCreateNew")
    pageDashboard.wait_for_selector("#modalCreateWizard", timeout=5000)
    iVisibleDots = pageDashboard.evaluate(
        """() => Array.from(
            document.querySelectorAll('.wizard-progress-step')
        ).filter((el) => el.style.display !== 'none').length"""
    )
    assert iVisibleDots == 3


# ── Grouping by machine, and saying what each tile is ────────────────
#
# A host project rendered under a heading reading "Containers", and the
# only thing marking it out was a status dot in vaibify's own brand
# colour -- which spends the brand on "this one is odd" and says
# nothing about why. Both were reported by a researcher on the first
# real walkthrough. The heading now names the MACHINE, because that is
# the axis that survives a remote machine hosting either kind, and the
# containment is said in words on the tile.


@pytest.mark.falsification
def testTheListIsHeadedByTheMachineNotTheContainment(
    pageDashboard, serverHub,
):
    """The heading must not call a host project a container.

    Kills: heading the list "Containers", which files every host
    project under the one word it is not.
    """
    _fnWaitForPicker(pageDashboard, serverHub)
    sHeading = pageDashboard.text_content("#labelContainers")
    assert "This machine" in sHeading, sHeading
    assert "Container" not in sHeading, (
        f"the heading still names the containment: {sHeading}"
    )


@pytest.mark.falsification
def testEveryTileSaysWhetherItIsContained(pageDashboard, serverHub):
    """The chip carries what the heading no longer can.

    Asserted on BOTH tiles in the same list: a chip that always reads
    "contained" would pass a host-only assertion and quietly tell a
    researcher their uncontained project was contained, which is the
    single most consequential thing this screen says.

    Kills: rendering one containment for every tile.
    """
    _fnWaitForPicker(pageDashboard, serverHub)
    assert _fsTileChip(pageDashboard, S_HOST_PROJECT_READY) == "uncontained"
    assert _fsTileChip(pageDashboard, S_CONTAINER_NAME) == "contained"


def _fsTileChip(page, sName):
    """Return a tile's containment chip text."""
    return page.text_content(
        f'.container-tile[data-name="{sName}"] .containment-chip',
    ).strip()
