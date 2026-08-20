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
(flock and all), and the automatic re-entry through the same click path
a researcher would use — including the host warning, which a fresh
entry legitimately shows again.
"""

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


def _fnDrivePromoteWizardToSubmit(page):
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
    page.fill("#inputWizardProjectName", S_NEW_PROJECT_NAME)
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
    # The tab re-enters on its own. A fresh entry legitimately shows
    # the host warning again; continue through it.
    fDeadline = time.monotonic() + 15.0
    while time.monotonic() < fDeadline:
        if pageDashboard.is_visible("#modalConfirm"):
            pageDashboard.click("#btnConfirmOk")
        if pageDashboard.is_visible("#workflowPicker"):
            break
        pageDashboard.wait_for_timeout(200)
    assert pageDashboard.is_visible("#workflowPicker"), (
        "the tab never re-entered the promoted project"
    )
    assert pageDashboard.title() == S_NEW_PROJECT_NAME, (
        "the re-entered view does not carry the promoted name"
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
