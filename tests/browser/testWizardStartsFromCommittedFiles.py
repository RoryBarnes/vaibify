"""The Files page offers the committed versions of pinned files that differ.

A reader who re-ran a published project on their own machine has, by
design, outputs that differ from the ones the author's manifest pins,
and Level 3 verification refuses a project in that state. Converting
is the moment to choose: copy the folder as it is (the default), or
start the container from the committed versions. The choice is only
offered when something differs, it is never ticked for the researcher,
the summary states it, and it reaches the seed request.

Real git in the researcher's folder, so the page's list comes from the
real host route, and the seed request reaches the real seed route. The
lane has no container, so the fail-closed adapter refuses the restore's
git; the test asserts that the restore was ATTEMPTED at the seeded
destination and that its failure is said, not swallowed. (An earlier
draft intercepted the seed request, and passed while the route read
the choice off the wrong request model.)
"""

import os
import time

import pytest

from tests.browser.testConvertToProjectJourney import (
    _fnClickNextPastAnyAgentWarning,
    _fnOpenHostTileMenu,
    _fnWaitForPicker,
    fixtureNoRealDockerBuildOrStart,  # noqa: F401 -- autouse fixture
)
from tests.reproductionSourceFixtures import (
    fnCommitEverything,
    fnWriteManifest,
    fnWriteText,
    fsRunGit,
)


pytestmark = pytest.mark.browser

S_PUBLISHED_SANDBOX = "publishedLaneClone"
S_PUBLISHED_CONTAINER_NAME = "published-lane-clone-box"
S_PINNED_OUTPUT = "Step/result.json"


def _fnSeedAPublishedCloneTheReaderReRan(serverHub):
    """A host project whose committed manifest pins a file the reader changed."""
    from vaibify.config import registryManager
    sDirectory = os.path.join(serverHub.sHome, S_PUBLISHED_SANDBOX)
    os.makedirs(sDirectory, exist_ok=True)
    fsRunGit(["init", "-q"], sDirectory)
    fnWriteText(sDirectory, S_PINNED_OUTPUT, "author 1.000000000001\n")
    fnWriteText(sDirectory, "vaibify.yml",
                f"projectName: {S_PUBLISHED_SANDBOX}\n")
    fnWriteManifest(sDirectory, [S_PINNED_OUTPUT])
    fnCommitEverything(sDirectory, "author publishes")
    fnWriteText(sDirectory, S_PINNED_OUTPUT, "reader 1.000000000002\n")
    registryManager.fnAddProject(sDirectory, sMode="host")


def _fnOpenConvertWizardOnFilesPage(page, serverHub):
    _fnWaitForPicker(page, serverHub)
    page.wait_for_selector(
        f'.container-tile[data-name="{S_PUBLISHED_SANDBOX}"]',
        timeout=10000,
    )
    _fnOpenHostTileMenu(page, S_PUBLISHED_SANDBOX)
    page.click(
        f'.container-tile[data-name="{S_PUBLISHED_SANDBOX}"] '
        '.container-menu-item[data-action="convert"]',
    )
    page.wait_for_selector("#modalCreateWizard", timeout=5000)
    page.wait_for_timeout(200)
    page.fill("#inputWizardProjectName", S_PUBLISHED_CONTAINER_NAME)
    # Name -> Environment -> Python -> Repositories -> Features -> Files.
    for _iStep in range(5):
        _fnClickNextPastAnyAgentWarning(page)
    assert page.text_content("#wizardStepTitle").strip() == "Files to Copy"


@pytest.mark.falsification
def test_the_reader_can_start_the_container_from_the_committed_files(
    pageDashboard, serverHub,
):
    """Offered unticked, stated in the summary, and carried to the seed.

    Kills: dropping the researcher's choice from the seed request, so
    the container always holds their own run and Level 3 verification
    refuses with no way through that keeps the author's manifest.
    """
    _fnSeedAPublishedCloneTheReaderReRan(serverHub)
    adapterDocker = serverHub.adapterDocker
    adapterDocker.listSeededPaths.clear()
    _fnOpenConvertWizardOnFilesPage(pageDashboard, serverHub)

    pageDashboard.wait_for_selector(
        "#wizardRestoreCommittedFiles", timeout=10000)
    assert not pageDashboard.is_checked("#wizardRestoreCommittedFiles"), (
        "the committed versions were chosen for the researcher; copying "
        "the folder as it is must stay the default"
    )
    sOffer = pageDashboard.text_content("#wizardCommittedFiles")
    assert "1 file the manifest pins differs" in sOffer, sOffer
    assert S_PINNED_OUTPUT in sOffer, sOffer
    pageDashboard.check("#wizardRestoreCommittedFiles")

    # Files -> Packages -> Summary.
    pageDashboard.click("#btnWizardNext")
    pageDashboard.wait_for_timeout(400)
    pageDashboard.click("#btnWizardNext")
    pageDashboard.wait_for_timeout(200)
    sSummary = pageDashboard.text_content("#wizardStepContent")
    assert "Pinned files that differ (1)" in sSummary, sSummary
    assert "the committed versions" in sSummary, sSummary

    pageDashboard.click("#btnWizardNext")
    pageDashboard.wait_for_selector("#modalConfirm", timeout=5000)
    pageDashboard.click("#btnConfirmOk")
    locatorToast = pageDashboard.locator(
        ".toast", has_text="Copied").first
    locatorToast.wait_for(timeout=20000)
    sToast = locatorToast.inner_text()
    sDestination = f"/workspace/{S_PUBLISHED_SANDBOX}"
    assert any(
        s.startswith(sDestination) for s in adapterDocker.listSeededPaths
    ), adapterDocker.listSeededPaths
    listGitAtDestination = [
        s for s in adapterDocker.listSeenCommands
        if "git" in s and sDestination in s
    ]
    assert listGitAtDestination, (
        "the seed never asked git about the copied repository, so the "
        "researcher's choice did not reach the restore: "
        + str(adapterDocker.listSeenCommands[-10:])
    )
    assert "NOT put in place" in sToast, (
        "a restore that could not run was not said: " + sToast
    )
    assert pageDashboard.listPageErrors == []
