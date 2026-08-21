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


# Lowercase, because a container name becomes an IMAGE repository
# name and those may not contain capitals. Still distinct from the
# host basename it converts, which is the property this file
# depends on.
S_NEW_CONTAINER_NAME = "host-lane-ready-box"


def _fsContainerIdFor(sName):
    """Mint a per-name container id that is NOT the name.

    One shared id for every start collided the moment this file
    converted a second project: two names resolved to one id, and the
    owner-map lookup that maps id back to name answered with whichever
    was registered first. Distinct ids are also what real containers
    have, and keeping id != name is the discipline recorded in
    AGENTS.md.
    """
    return (sName[::-1] + "0" * 64)[:64]


@pytest.fixture(autouse=True)
def fixtureNoRealDockerBuildOrStart(monkeypatch, serverHub):
    """Patch the build + start executors in the live in-process server.

    Without this the convert journey's follow-on build would launch a
    real ``docker build`` and a real start against a daemon the lane
    does not have. Patched to no-ops, the routes answer success and the
    frontend flips the tile -- the surface under test -- with nothing
    touching Docker.

    The start patch tells the fake adapter the container is now
    running. A stub that started nothing left the lane insisting the
    container it had just started did not exist, so every route acting
    on the converted project 404'd for a reason unrelated to what was
    under test. The id is kept DISTINCT from the name throughout, for
    the reason recorded in AGENTS.md.
    """
    monkeypatch.setattr(
        "vaibify.gui.buildRoutes._fnExecuteBuild",
        lambda dictProject, bNoCache, dictProgress: None,
    )

    def fsStartAndRecord(sName, reservation, configProject):
        sContainerId = _fsContainerIdFor(sName)
        serverHub.adapterDocker.fnRecordContainerStarted(
            sName, sContainerId,
        )
        return sContainerId

    monkeypatch.setattr(
        "vaibify.gui.startReservation._fsExecuteReservedStart",
        fsStartAndRecord,
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


def _fnClickNextPastAnyAgentWarning(page):
    """Advance one page, acknowledging the no-agent warning if it fires.

    The lane's default features carry no coding agent, so leaving the
    Features page raises the "No coding agent selected" confirmation --
    which is the intended behaviour, not an obstacle. A journey about
    something else answers it the way a researcher would and carries
    on; the warning itself has its own test below.
    """
    page.click("#btnWizardNext")
    page.wait_for_timeout(200)
    elModal = page.query_selector("#modalConfirm")
    if elModal and elModal.is_visible() and "coding agent" in (
        page.text_content("#modalConfirm") or ""
    ):
        page.click("#btnConfirmOk")
        page.wait_for_timeout(200)


def _fnWalkConvertWizardToSummary(page):
    """From the open Name page, type a new name and advance to Summary.

    Six clicks, not five, since 2026-08-21: Files to Copy sits between
    Packages and Summary, because a container's workspace is a fresh
    Docker volume rather than the researcher's own folder and the
    conversion is where they choose what crosses over.
    """
    page.fill("#inputWizardProjectName", S_NEW_CONTAINER_NAME)
    for _iStep in range(6):
        _fnClickNextPastAnyAgentWarning(page)


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


S_SEED_SANDBOX = "seedLaneSandbox"
S_SEED_CONTAINER_NAME = "seed-lane-sandbox-box"


def _fnSeedAConvertibleHostProject(serverHub, sSandboxName=None):
    """Register a SECOND host project, with real files, for this journey.

    The shared one is consumed by the conversion journey above -- it is
    a container by then and no longer offers "Make a Project" -- so a
    test that reused it would pass alone and fail in file order, which
    is the least useful way for a test to fail.
    """
    import os
    import subprocess
    from vaibify.config import registryManager
    sSandboxName = sSandboxName or S_SEED_SANDBOX
    sDirectory = os.path.join(serverHub.sHome, sSandboxName)
    os.makedirs(sDirectory, exist_ok=True)
    for sName, sBody in (
        # Real imports: one stdlib (must never be suggested), one
        # third-party whose distribution is spelled differently from
        # its module, and one plain third-party.
        ("analysis.py",
         "import json\nimport numpy\nimport sklearn\n"),
        ("notes.txt", "leave me on the host\n"),
    ):
        with open(os.path.join(sDirectory, sName), "w") as fileEntry:
            fileEntry.write(sBody)
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


def _fnChooseOnlyTheAnalysisFile(page):
    """On the Files page, untick everything, then tick one entry.

    Deliberately not "accept the defaults": everything arrives ticked,
    so a journey that changed nothing would pass against a page that
    ignores its checkboxes entirely. Driving a real EXCLUSION is what
    proves the selection is read.
    """
    page.wait_for_selector("#wizardSeedList .wizard-seed-input",
                           timeout=10000)
    page.uncheck("#wizardSeedSelectAll")
    page.check('.wizard-seed-input[data-seed-name="analysis.py"]')


@pytest.mark.falsification
def testOnlyTheTickedFilesAreCopiedIntoTheContainer(
    pageDashboard, serverHub,
):
    """The researcher's SELECTION is what crosses into the container.

    A container's /workspace is a fresh Docker volume, not the
    researcher's folder, so before this step converted projects opened
    empty and said nothing about it (2026-08-21). The assertion is what
    reached the container adapter, never that the route answered 200:
    a seed that copied the whole directory, or nothing at all, would
    answer 200 just as happily.

    Kills: ignoring the checkboxes and seeding every entry, and
    dropping the seed call from the convert submit so nothing is
    copied at all.
    """
    _fnSeedAConvertibleHostProject(serverHub)
    # The adapter is module-scoped and the conversion above seeds too
    # (with everything ticked, which is the default). Clear it so this
    # assertion is about THIS journey's selection and not the file's
    # running total.
    serverHub.adapterDocker.listSeededPaths.clear()
    _fnWaitForPicker(pageDashboard, serverHub)
    pageDashboard.wait_for_selector(
        f'.container-tile[data-name="{S_SEED_SANDBOX}"]', timeout=10000,
    )
    _fnOpenHostTileMenu(pageDashboard, S_SEED_SANDBOX)
    pageDashboard.click(
        f'.container-tile[data-name="{S_SEED_SANDBOX}"] '
        '.container-menu-item[data-action="convert"]',
    )
    pageDashboard.wait_for_selector("#modalCreateWizard", timeout=5000)
    pageDashboard.click('.add-choice-card[data-destination="container"]')
    pageDashboard.wait_for_timeout(150)
    pageDashboard.click("#btnWizardNext")
    pageDashboard.wait_for_timeout(200)
    pageDashboard.fill(
        "#inputWizardProjectName", S_SEED_CONTAINER_NAME)
    # Name -> Python -> Repositories -> Features -> Files. Four, not
    # five: Files moved ahead of Packages so the package page can be
    # filled from the files chosen here.
    for _iStep in range(4):
        _fnClickNextPastAnyAgentWarning(pageDashboard)
    assert pageDashboard.text_content(
        "#wizardStepTitle",
    ).strip() == "Files to Copy"
    _fnChooseOnlyTheAnalysisFile(pageDashboard)
    # Files -> Packages -> Summary.
    pageDashboard.click("#btnWizardNext")
    pageDashboard.wait_for_timeout(400)
    pageDashboard.click("#btnWizardNext")
    pageDashboard.wait_for_timeout(200)
    # The summary states what will cross, so the researcher sees the
    # consequence of their ticks before committing to a build.
    sSummary = pageDashboard.text_content("#wizardStepContent")
    assert "Copied into the container" in sSummary
    assert "analysis.py" in sSummary
    assert "notes.txt" not in sSummary, (
        "an unticked file was still listed as crossing over: " + sSummary
    )
    pageDashboard.click("#btnWizardNext")
    pageDashboard.wait_for_selector("#modalConfirm", timeout=5000)
    pageDashboard.click("#btnConfirmOk")
    adapterDocker = serverHub.adapterDocker
    fDeadline = time.monotonic() + 20.0
    while time.monotonic() < fDeadline:
        if adapterDocker.listSeededPaths:
            break
        pageDashboard.wait_for_timeout(150)
    listSeeded = adapterDocker.listSeededPaths
    assert f"/workspace/{S_SEED_SANDBOX}/analysis.py" in listSeeded, (
        listSeeded
    )
    assert f"/workspace/{S_SEED_SANDBOX}/notes.txt" not in listSeeded, (
        "a file the researcher unticked was copied anyway: "
        + str(listSeeded)
    )
    # Infrastructure crosses whatever was ticked: .git because a
    # vaibify workflow must live in a git repository, and .vaibify
    # because the Project file is written into it DURING the
    # conversion -- after the researcher chose from a list that could
    # not have offered it.
    for sInfrastructure in (".git", ".vaibify"):
        assert (
            f"/workspace/{S_SEED_SANDBOX}/{sInfrastructure}" in listSeeded
        ), (sInfrastructure + " did not cross: " + str(listSeeded))
    assert pageDashboard.listPageErrors == []


@pytest.mark.falsification
def testASpacedNameIsRefusedAtTheNamingStepNotAtTheEnd(
    pageDashboard, serverHub,
):
    """The container-name rule is enforced where the name is typed.

    A researcher called their project "AI Greenhouse", walked the whole
    wizard, chose packages and files, and was refused at the final
    click because Docker names cannot contain spaces (live report,
    2026-08-21). The refusal was correct and arrived uselessly late.
    Now the Project name accepts the space -- it is the name they will
    read on the Project hub -- and the container name is derived
    Docker-safe beside it, with the rule enforced before Next.

    Kills: dropping the name check from the wizard's step validation,
    which restores the walk-all-the-way-and-be-refused journey.
    """
    S_NAMING_SANDBOX = "namingLaneSandbox"
    _fnSeedAConvertibleHostProject(serverHub, S_NAMING_SANDBOX)
    _fnWaitForPicker(pageDashboard, serverHub)
    pageDashboard.wait_for_selector(
        f'.container-tile[data-name="{S_NAMING_SANDBOX}"]', timeout=10000,
    )
    _fnOpenHostTileMenu(pageDashboard, S_NAMING_SANDBOX)
    pageDashboard.click(
        f'.container-tile[data-name="{S_NAMING_SANDBOX}"] '
        '.container-menu-item[data-action="convert"]',
    )
    pageDashboard.wait_for_selector("#modalCreateWizard", timeout=5000)
    pageDashboard.click('.add-choice-card[data-destination="container"]')
    pageDashboard.wait_for_timeout(150)
    pageDashboard.click("#btnWizardNext")
    pageDashboard.wait_for_timeout(200)
    pageDashboard.fill("#inputWizardWorkflowName", "AI Greenhouse")
    # The container name follows the Project name, made Docker-safe.
    assert " " not in pageDashboard.input_value(
        "#inputWizardProjectName",
    )
    # The suggestion is LOWERCASE: an image repository name may not
    # contain capitals, and a name with them builds nothing.
    assert pageDashboard.input_value(
        "#inputWizardProjectName",
    ) == pageDashboard.input_value(
        "#inputWizardProjectName",
    ).lower()
    # A capital typed into the CONTAINER name is refused in place --
    # this is the exact name that reached `docker build -t
    # AI-Greenhouse:base` and died there.
    pageDashboard.fill("#inputWizardProjectName", "AI-Greenhouse")
    assert "lowercase" in pageDashboard.text_content(
        "#wizardNameProblem",
    ).lower()
    # ...as is a space.
    pageDashboard.fill("#inputWizardProjectName", "AI Greenhouse")
    assert "space" in pageDashboard.text_content(
        "#wizardNameProblem",
    ).lower()
    # ...and Next does not advance past it.
    pageDashboard.click("#btnWizardNext")
    pageDashboard.wait_for_timeout(250)
    assert pageDashboard.text_content(
        "#wizardStepTitle",
    ).strip() == "Project Name", (
        "the wizard advanced past a container name Docker will refuse"
    )
    assert pageDashboard.listPageErrors == []


@pytest.mark.falsification
def testThePackagesPageIsPrefilledFromTheChosenScripts(
    pageDashboard, serverHub,
):
    """Ticking scripts fills the package list from their imports.

    The wizard used to ask for packages with no knowledge of the
    project it was converting, leaving the researcher to re-read their
    own scripts and transcribe the imports (live report, 2026-08-21).
    Files are now chosen BEFORE packages precisely so the answer can
    be read from them.

    Two fields, and the separation is the point: what vaibify detected
    stays visible apart from what the researcher adds, so a wrong
    detection can be seen and corrected rather than silently merged
    into their own list.

    Kills: rendering the packages page without the detected field, and
    scanning something other than the researcher's selection.
    """
    S_SCAN_SANDBOX = "scanLaneSandbox"
    _fnSeedAConvertibleHostProject(serverHub, S_SCAN_SANDBOX)
    _fnWaitForPicker(pageDashboard, serverHub)
    pageDashboard.wait_for_selector(
        f'.container-tile[data-name="{S_SCAN_SANDBOX}"]', timeout=10000,
    )
    _fnOpenHostTileMenu(pageDashboard, S_SCAN_SANDBOX)
    pageDashboard.click(
        f'.container-tile[data-name="{S_SCAN_SANDBOX}"] '
        '.container-menu-item[data-action="convert"]',
    )
    pageDashboard.wait_for_selector("#modalCreateWizard", timeout=5000)
    pageDashboard.click('.add-choice-card[data-destination="container"]')
    pageDashboard.wait_for_timeout(150)
    pageDashboard.click("#btnWizardNext")
    pageDashboard.wait_for_timeout(200)
    pageDashboard.fill("#inputWizardProjectName", "scan-lane-box")
    # Name -> Python -> Repositories -> Features -> Files
    for _iStep in range(4):
        _fnClickNextPastAnyAgentWarning(pageDashboard)
    assert pageDashboard.text_content(
        "#wizardStepTitle",
    ).strip() == "Files to Copy"
    pageDashboard.wait_for_selector("#wizardSeedList .wizard-seed-input",
                                    timeout=10000)
    pageDashboard.click("#btnWizardNext")
    assert pageDashboard.text_content(
        "#wizardStepTitle",
    ).strip() == "Packages"
    elDetected = pageDashboard.wait_for_selector(
        "#wizardDetectedPackages", timeout=10000,
    )
    pageDashboard.wait_for_function(
        """() => document.getElementById('wizardDetectedPackages')
            .value.trim().length > 0""",
        timeout=15000,
    )
    listDetected = sorted(
        s for s in elDetected.input_value().split("\n") if s.strip()
    )
    # numpy as itself, sklearn mapped to its distribution name, and
    # NOT json -- suggesting a stdlib module would ask the researcher
    # to pip-install part of Python.
    assert listDetected == ["numpy", "scikit-learn"], listDetected
    assert pageDashboard.input_value("#wizardPythonPackages") == "", (
        "the detected packages were merged into the researcher's own "
        "field, where they can no longer be told apart"
    )
    assert pageDashboard.listPageErrors == []


@pytest.mark.falsification
def testLeavingEveryAgentUntickedAsksBeforeContinuing(
    pageDashboard, serverHub,
):
    """An agentless container is confirmed, not silently built.

    Running an AI agent against a contained, reproducible workspace is
    what vaibify is for, so a wizard that reached the end with every
    agent unticked has almost certainly recorded a slip rather than a
    decision -- and the cost of finding out later is another full
    image build. The confirmation asks once and takes "yes" for an
    answer: it must not become a refusal, because an agentless
    container is legitimate.

    Kills: dropping the no-agent confirmation, which lets the wizard
    walk past an unintended agentless build without a word.
    """
    S_AGENT_SANDBOX = "agentLaneSandbox"
    _fnSeedAConvertibleHostProject(serverHub, S_AGENT_SANDBOX)
    _fnWaitForPicker(pageDashboard, serverHub)
    pageDashboard.wait_for_selector(
        f'.container-tile[data-name="{S_AGENT_SANDBOX}"]', timeout=10000,
    )
    _fnOpenHostTileMenu(pageDashboard, S_AGENT_SANDBOX)
    pageDashboard.click(
        f'.container-tile[data-name="{S_AGENT_SANDBOX}"] '
        '.container-menu-item[data-action="convert"]',
    )
    pageDashboard.wait_for_selector("#modalCreateWizard", timeout=5000)
    pageDashboard.click('.add-choice-card[data-destination="container"]')
    pageDashboard.wait_for_timeout(150)
    pageDashboard.click("#btnWizardNext")
    pageDashboard.wait_for_timeout(200)
    pageDashboard.fill("#inputWizardProjectName", "agent-lane-box")
    # Name -> Python -> Repositories -> Features.
    for _iStep in range(3):
        pageDashboard.click("#btnWizardNext")
        pageDashboard.wait_for_timeout(200)
    assert pageDashboard.text_content(
        "#wizardStepTitle",
    ).strip() == "Features & Authentication"
    # No agent is ticked by default in this lane, so leaving the page
    # must ask rather than proceed.
    pageDashboard.click("#btnWizardNext")
    pageDashboard.wait_for_selector("#modalConfirm", timeout=5000)
    sBody = pageDashboard.text_content("#modalConfirm")
    assert "coding agent" in sBody.lower(), sBody
    # It is a question, not a refusal: answering yes carries on.
    pageDashboard.click("#btnConfirmOk")
    pageDashboard.wait_for_timeout(300)
    assert pageDashboard.text_content(
        "#wizardStepTitle",
    ).strip() == "Files to Copy", (
        "the confirmation blocked a legitimate agentless conversion"
    )
    assert pageDashboard.listPageErrors == []


def testTickingAnAgentAsksNothing(pageDashboard, serverHub):
    """The warning is silent when an agent IS chosen.

    Without this, a confirmation that fired unconditionally would pass
    the test above just as well -- and would nag every researcher who
    did exactly the right thing.
    """
    S_TICKED_SANDBOX = "tickedLaneSandbox"
    _fnSeedAConvertibleHostProject(serverHub, S_TICKED_SANDBOX)
    _fnWaitForPicker(pageDashboard, serverHub)
    pageDashboard.wait_for_selector(
        f'.container-tile[data-name="{S_TICKED_SANDBOX}"]', timeout=10000,
    )
    _fnOpenHostTileMenu(pageDashboard, S_TICKED_SANDBOX)
    pageDashboard.click(
        f'.container-tile[data-name="{S_TICKED_SANDBOX}"] '
        '.container-menu-item[data-action="convert"]',
    )
    pageDashboard.wait_for_selector("#modalCreateWizard", timeout=5000)
    pageDashboard.click('.add-choice-card[data-destination="container"]')
    pageDashboard.wait_for_timeout(150)
    pageDashboard.click("#btnWizardNext")
    pageDashboard.wait_for_timeout(200)
    pageDashboard.fill("#inputWizardProjectName", "ticked-lane-box")
    for _iStep in range(3):
        pageDashboard.click("#btnWizardNext")
        pageDashboard.wait_for_timeout(200)
    pageDashboard.check('.wizard-feature-input[data-feature="claude"]')
    pageDashboard.click("#btnWizardNext")
    pageDashboard.wait_for_timeout(300)
    assert pageDashboard.text_content(
        "#wizardStepTitle",
    ).strip() == "Files to Copy", "choosing an agent still nagged"
    assert pageDashboard.listPageErrors == []
