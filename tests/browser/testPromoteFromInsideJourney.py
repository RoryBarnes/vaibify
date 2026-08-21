"""Promoting a host sandbox from INSIDE its own open browser session.

The tile-based promotion journey next door drives the wizard from the
picker, where nothing owns the project. This one drives the exact
journey that used to dead-end: open the sandbox's workflow (claiming it
and connecting the live pipeline WebSocket), open the Files panel,
click "Convert to Project", and promote — while this very tab holds the
lease. The old behavior answered the final Promote with "'…' is open in
a browser session right now. Close it, then convert it.", leaving no
in-browser way to finish. Now the server releases the caller's own
session as part of the promotion and the tab re-enters the project
under its new name.

The journey is real all the way down: a real claim, a real
session-bound lease, a live pipeline socket the submit path must quiet
deliberately (the server closes a leftover one with a refusal code the
connection monitor reads as an outage), the real lifecycle release
(flock and all), and the automatic re-entry that ends in the STEP
VIEWER with the project's sole workflow open — no Environment-hub dead
end, and no re-shown host warning, because the researcher accepted it
to enter this very session and promotion never changes the directory
the warning is about.
"""

import os
import time

import pytest

from tests.browser.conftest import (
    S_HOST_PROJECT_READY,
    S_HOST_STEP_NAME,
    S_HOST_WORKFLOW_NAME,
)


pytestmark = pytest.mark.browser


# Distinct from the basename and carrying a space: host names allow
# spaces, and the promotion must not apply the Docker rule.
S_NEW_PROJECT_NAME = "Promoted From Inside"


def _fnOpenTheSandboxWorkflow(page, serverHub):
    """Warn, continue, and open the seeded workflow with its live socket."""
    page.goto(serverHub.fsBootstrapUrl(), wait_until="load")
    page.wait_for_selector(
        f'.container-tile[data-name="{S_HOST_PROJECT_READY}"]',
        timeout=15000,
    )
    page.click(
        f'.container-tile[data-name="{S_HOST_PROJECT_READY}"] '
        '.container-tile-main',
    )
    page.wait_for_selector("#modalConfirm", timeout=10000)
    page.click("#btnConfirmOk")
    page.wait_for_selector(f"text={S_HOST_WORKFLOW_NAME}", timeout=20000)
    page.click(f"text={S_HOST_WORKFLOW_NAME}")
    page.wait_for_selector(f"text={S_HOST_STEP_NAME}", timeout=20000)
    # The pipeline socket connects lazily, on the first pipeline
    # action, and stays open afterwards -- a researcher who ran a step
    # holds a live socket when they later promote. Reproduce that state
    # through the same production entry point every action calls, so
    # the submit path's channel discipline is exercised over a LIVE
    # socket.
    page.evaluate(
        "() => VaibifyPipelineRunner.fnConnectPipelineWebSocket()",
    )
    page.wait_for_function(
        "() => VaibifyWebSocket.fbIsOpen()", timeout=15000,
    )


def _fnDrivePromoteWizardToSubmit(page, sNewName=S_NEW_PROJECT_NAME):
    """From the Files bar, choose Host Project, name it, click Promote."""
    page.click('.left-tab[data-panel="files"]')
    page.wait_for_selector(
        "#panelFiles.active", state="visible", timeout=10000,
    )
    page.wait_for_selector("#fileConvertToProjectBar", timeout=10000)
    page.click("#btnConvertToProject")
    page.wait_for_selector("#modalCreateWizard", timeout=5000)
    page.click('.add-choice-card[data-destination="host"]')
    page.wait_for_timeout(150)
    page.click("#btnWizardNext")
    page.wait_for_timeout(200)
    page.fill("#inputWizardProjectName", sNewName)
    page.click("#btnWizardNext")
    page.wait_for_timeout(200)
    assert page.text_content("#btnWizardNext").strip() == "Promote"
    page.click("#btnWizardNext")


@pytest.mark.falsification
def testPromotingFromInsideTheOpenProjectSucceedsAndReenters(
    pageDashboard, serverHub,
):
    """The whole in-place journey: promote while owning, land back inside.

    Kills: reinstating the unconditional owned-project refusal (the
    registry never flips and the "open in a browser session" toast
    returns), and a frontend that promotes but strands the tab on a
    dead lease instead of re-entering the renamed project.
    """
    from vaibify.config import registryManager
    _fnOpenTheSandboxWorkflow(pageDashboard, serverHub)
    _fnDrivePromoteWizardToSubmit(pageDashboard)
    # Server-side truth first: the registry flips even though this tab
    # held the project when Promote was clicked.
    fDeadline = time.monotonic() + 15.0
    while time.monotonic() < fDeadline:
        if (registryManager.fdictGetProject(S_HOST_PROJECT_READY) is None
                and registryManager.fdictGetProject(
                    S_NEW_PROJECT_NAME) is not None):
            break
        pageDashboard.wait_for_timeout(150)
    dictPromoted = registryManager.fdictGetProject(S_NEW_PROJECT_NAME)
    assert dictPromoted is not None, (
        "promoting from inside the open project never re-registered it"
    )
    assert dictPromoted["sMode"] == "host"
    assert dictPromoted["bIsProject"] is True
    # The old session was RELEASED, not stranded: the owner map no
    # longer carries the old name.
    assert S_HOST_PROJECT_READY not in (
        serverHub.app.state.dictContainerOwners
    )
    # The tab re-enters on its own and, because the project has
    # exactly one workflow, lands back in the STEP VIEWER -- the
    # sandbox's file view transformed into the Project's dashboard,
    # with no Environment-hub dead end and no re-shown host warning.
    pageDashboard.wait_for_selector(
        "#mainLayout.active", timeout=20000,
    )
    pageDashboard.wait_for_selector(
        f"text={S_HOST_STEP_NAME}", timeout=20000,
    )
    assert not pageDashboard.is_visible("#modalConfirm"), (
        "the host warning was re-shown on an in-session promotion"
    )
    # Re-entry claimed the project under its NEW name.
    assert S_NEW_PROJECT_NAME in (
        serverHub.app.state.dictContainerOwners
    )
    # The live socket was quieted deliberately, so the connection
    # monitor never read the release as an outage (surfacing stops
    # every poller for good and demands a reload).
    assert pageDashboard.evaluate(
        "() => VaibifyConnectionMonitor.fbHasSurfaced()",
    ) is False, (
        "the promotion made the connection monitor surface an outage"
    )
    assert pageDashboard.listPageErrors == [], pageDashboard.listPageErrors


S_BLANK_SANDBOX = "blankLaneSandbox"
S_BLANK_NEW_NAME = "Promoted From Blank"


def _fnSeedBlankSandbox(serverHub, sSandboxName=S_BLANK_SANDBOX):
    """Register a host sandbox that is a git repo with NO workflow.

    This is the shape every fresh sandbox now has — the sandbox
    template scaffolds no workflow at all — so the promotion itself
    must bring the first workflow file into being.
    """
    import json as moduleJson
    import subprocess
    from vaibify.config import registryManager
    sDirectory = os.path.join(serverHub.sHome, sSandboxName)
    os.makedirs(sDirectory, exist_ok=True)
    with open(
        os.path.join(sDirectory, "vaibify.yml"), "w",
    ) as fileConfig:
        fileConfig.write(f"projectName: {sSandboxName}\n")
    with open(
        os.path.join(sDirectory, "analysis.py"), "w",
    ) as fileScript:
        fileScript.write("print('placeholder')\n")
    for listCommand in (
        ["git", "init", "-q"],
        ["git", "config", "user.email", "lane@example.invalid"],
        ["git", "config", "user.name", "Browser Lane"],
        ["git", "add", "-A"],
        ["git", "-c", "commit.gpgsign=false", "commit", "-q",
         "-m", "seed"],
    ):
        subprocess.run(
            listCommand, cwd=sDirectory, check=True,
            capture_output=True,
        )
    registryManager.fnAddProject(sDirectory, sMode="host")
    return sDirectory


def _fnEnterTheBlankProject(page, serverHub, sSandboxName=S_BLANK_SANDBOX):
    """Warn, continue, and enter the workflow-less Blank Project view."""
    page.goto(serverHub.fsBootstrapUrl(), wait_until="load")
    page.wait_for_selector(
        f'.container-tile[data-name="{sSandboxName}"]',
        timeout=15000,
    )
    page.click(
        f'.container-tile[data-name="{sSandboxName}"] '
        '.container-tile-main',
    )
    page.wait_for_selector("#modalConfirm", timeout=10000)
    page.click("#btnConfirmOk")
    page.wait_for_selector("#btnNoWorkflow", timeout=20000)
    assert page.locator("#listWorkflows .container-card").count() == 0, (
        "a blank sandbox offered a workflow card it should not have"
    )
    page.click("#btnNoWorkflow")
    page.wait_for_selector("#mainLayout.active", timeout=20000)


@pytest.mark.falsification
def testPromotingABlankSandboxScaffoldsAndOpensTheEmptyProject(
    pageDashboard, serverHub,
):
    """A blank sandbox's promotion ends in the empty step viewer.

    The 2026-08-20 live report: with the sandbox template scaffolding
    no workflow, clicking Promote flipped the registry, but the
    re-entry found zero workflow cards, fired the birth animation over
    an empty picker, and the Project the researcher had just named was
    nowhere on screen. Promotion now scaffolds the Project's first
    workflow file, so the sole-workflow re-entry opens it and the
    researcher lands on the "No steps yet" row — the same end state a
    promotion from a workflow-bearing sandbox reaches.

    Kills: blinding the re-entry's sole-workflow open, which re-strands
    this journey on the picker (the step viewer never activates and the
    empty-state row never renders). The backend half — dropping the
    scaffold itself — is killed by the promote route's own test.
    """
    import json as moduleJson
    from vaibify.config import registryManager
    sDirectory = _fnSeedBlankSandbox(serverHub)
    _fnEnterTheBlankProject(pageDashboard, serverHub)
    _fnDrivePromoteWizardToSubmit(pageDashboard, S_BLANK_NEW_NAME)
    fDeadline = time.monotonic() + 15.0
    while time.monotonic() < fDeadline:
        if registryManager.fdictGetProject(S_BLANK_NEW_NAME) is not None:
            break
        pageDashboard.wait_for_timeout(150)
    assert registryManager.fdictGetProject(S_BLANK_NEW_NAME) is not None
    # The scaffold is on disk, named after the PROMOTED Project.
    sWorkflowPath = os.path.join(
        sDirectory, ".vaibify", "projects", "project.json",
    )
    assert os.path.isfile(sWorkflowPath), (
        "promotion left the blank sandbox with no workflow file"
    )
    with open(sWorkflowPath) as fileWorkflow:
        dictWorkflow = moduleJson.load(fileWorkflow)
    assert dictWorkflow["sWorkflowName"] == S_BLANK_NEW_NAME
    # The re-entry opens the scaffolded workflow: step viewer, with
    # the empty-state row offering the first step.
    pageDashboard.wait_for_selector("#mainLayout.active", timeout=20000)
    pageDashboard.wait_for_selector(".step-empty-state", timeout=20000)
    assert S_BLANK_NEW_NAME in (
        serverHub.app.state.dictContainerOwners
    ), "the re-entry never claimed the promoted Project"
    assert pageDashboard.listPageErrors == [], pageDashboard.listPageErrors


S_CURTAIN_SANDBOX = "curtainLaneSandbox"
S_CURTAIN_NEW_NAME = "Promoted Behind Curtain"


@pytest.mark.falsification
def testThePromotionHandOffStaysBehindTheCurtain(
    pageDashboard, serverHub,
):
    """The re-entry's hub screens are covered, and the cover comes off.

    The hand-off necessarily tears down to the Environment hub,
    reloads the tiles, and passes the Project hub before the step
    viewer is ready. A researcher watching three screens flash by
    reads it as being kicked out (live report, 2026-08-20), so an
    opaque curtain carries the new Project's name across the
    transition — and must then LEAVE, or the fix for a flash is a
    permanently blank dashboard.

    Kills: dropping the curtain show from the promote submit's
    held-by-this-tab branch (the curtain never appears and the hubs
    flash again).
    """
    _fnSeedBlankSandbox(serverHub, S_CURTAIN_SANDBOX)
    _fnEnterTheBlankProject(pageDashboard, serverHub, S_CURTAIN_SANDBOX)
    _fnDrivePromoteWizardToSubmit(pageDashboard, S_CURTAIN_NEW_NAME)
    # The curtain is up during the hand-off, naming the new Project.
    elCurtain = pageDashboard.wait_for_selector(
        ".vaibify-promotion-curtain", timeout=15000,
    )
    assert S_CURTAIN_NEW_NAME in elCurtain.text_content()
    # The hand-off completes in the step viewer...
    pageDashboard.wait_for_selector("#mainLayout.active", timeout=20000)
    pageDashboard.wait_for_selector(".step-empty-state", timeout=20000)
    # ...and the curtain comes off.
    pageDashboard.wait_for_selector(
        ".vaibify-promotion-curtain", state="detached", timeout=10000,
    )
    assert pageDashboard.listPageErrors == [], pageDashboard.listPageErrors
