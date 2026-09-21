"""Blank Project mode: the banner is legible and the timeouts reachable.

Both defects were reported from this exact screen (2026-09-21), and
neither is visible to a Python suite — one is a computed colour, the
other is whether a click reaches a panel at all. The source-level
guards live in ``tests/testTheDashboardSettingsReachEveryMode.py``;
what is asserted here is the rendered result, in a real browser, in
the mode that hid them.
"""

import os
import subprocess

import pytest


pytestmark = pytest.mark.browser

# One sandbox per test: the hub is shared across this file and a
# sandbox the first test claimed is locked for the second.
S_BANNER_SANDBOX = "bannerLegibilitySandbox"
S_SETTINGS_SANDBOX = "hostSettingsSandbox"
S_RENEWAL_SANDBOX = "sessionRenewalSandbox"


def _fnSeedBlankSandbox(serverHub, sSandboxName):
    """Register a host sandbox that is a git repo with NO workflow."""
    from vaibify.config import registryManager
    sDirectory = os.path.join(serverHub.sHome, sSandboxName)
    os.makedirs(sDirectory, exist_ok=True)
    with open(os.path.join(sDirectory, "vaibify.yml"), "w") as fileConfig:
        fileConfig.write(f"projectName: {sSandboxName}\n")
    for listCommand in (
        ["git", "init", "-q"],
        ["git", "config", "user.email", "lane@example.invalid"],
        ["git", "config", "user.name", "Browser Lane"],
        ["git", "add", "-A"],
        ["git", "-c", "commit.gpgsign=false", "commit", "-q", "-m", "seed"],
    ):
        subprocess.run(
            listCommand, cwd=sDirectory, check=True, capture_output=True,
        )
    registryManager.fnAddProject(sDirectory, sMode="host")
    return sDirectory


def _fnEnterTheBlankProject(page, serverHub, sSandboxName):
    """Warn, continue, and enter the workflow-less Blank Project view."""
    page.goto(serverHub.fsBootstrapUrl(), wait_until="load")
    page.wait_for_selector(
        f'.container-tile[data-name="{sSandboxName}"]', timeout=15000,
    )
    page.click(
        f'.container-tile[data-name="{sSandboxName}"] '
        '.container-tile-main',
    )
    page.wait_for_selector("#modalConfirm", timeout=10000)
    page.click("#btnConfirmOk")
    page.wait_for_selector("#btnNoWorkflow", timeout=20000)
    page.click("#btnNoWorkflow")
    page.wait_for_selector("#mainLayout.active", timeout=20000)


@pytest.mark.falsification
def testTheProjectBannerIsLegibleAgainstTheToolbar(
    pageDashboard, serverHub,
):
    """The banner inherits the toolbar's colour rather than a link's.

    The oracle is not a hard-coded colour: it is the toolbar's own
    text, read off a sibling element in the same bar. Whatever the
    theme paints that with is what a researcher can read there, so the
    banner must match it — and the defect was precisely that the
    banner did not, because an unstyled ``<a>`` took the user agent's
    dark blue.

    Kills: writing markup into ``#activeWorkflowName`` again — any
    element the stylesheet does not name takes the browser default and
    stops matching its own toolbar.
    """
    _fnSeedBlankSandbox(serverHub, S_BANNER_SANDBOX)
    _fnEnterTheBlankProject(pageDashboard, serverHub, S_BANNER_SANDBOX)
    pageDashboard.wait_for_selector("#activeWorkflowName", timeout=15000)
    sBannerColour = pageDashboard.evaluate(
        "() => getComputedStyle("
        "document.getElementById('activeWorkflowName')).color",
    )
    sSiblingColour = pageDashboard.evaluate(
        "() => getComputedStyle("
        "document.getElementById('activeContainerName')).color",
    )
    assert sBannerColour == sSiblingColour, (
        "the project banner must read in the same colour as the rest "
        "of the toolbar's values"
    )
    iDescendants = pageDashboard.evaluate(
        "() => document.getElementById('activeWorkflowName')"
        ".querySelectorAll('*').length",
    )
    assert iDescendants == 0, (
        "the banner renders text; an element inside it carries styling "
        "of its own and is how it became illegible"
    )


@pytest.mark.falsification
def testTheSessionLifetimeIsReachableFromABlankProject(
    pageDashboard, serverHub,
):
    """The host gear opens, and the session cap is in it and settable.

    The oracle is the researcher's report: their Blank Project session
    timed out, and the control that prevents it lived behind a panel
    this mode does not render. So the test enters that mode — not the
    project mode — and requires the control to be reachable by
    clicking, with the value the server reports selected.

    Kills: rendering the host timeouts into the project settings panel
    (whose renderer returns early with no open project, and whose tab
    Blank Project mode hides), or hiding the gear per dashboard mode.
    """
    _fnSeedBlankSandbox(serverHub, S_SETTINGS_SANDBOX)
    _fnEnterTheBlankProject(pageDashboard, serverHub, S_SETTINGS_SANDBOX)
    pageDashboard.wait_for_selector("#btnHostSettings", timeout=15000)
    assert pageDashboard.is_visible("#btnHostSettings"), (
        "the host-settings gear must be visible in Blank Project mode"
    )
    pageDashboard.click("#btnHostSettings")
    pageDashboard.wait_for_selector(
        "#hostSettingsPanel #gsSessionCap", timeout=10000,
    )
    assert pageDashboard.is_visible("#hostSettingsPanel #gsSessionCap")
    pageDashboard.wait_for_function(
        "() => document.getElementById('gsSessionCap').value !== ''",
        timeout=10000,
    )
    listOptions = pageDashboard.evaluate(
        "() => Array.from(document.getElementById('gsSessionCap').options)"
        ".map(o => o.value)",
    )
    assert "604800" in listOptions, (
        "a researcher must be able to choose a cap that outlives a "
        "working week from this panel"
    )
    pageDashboard.select_option("#gsSessionCap", "604800")
    pageDashboard.wait_for_selector(".toast.success", timeout=10000)
    assert pageDashboard.listPageErrors == [], pageDashboard.listPageErrors


@pytest.mark.falsification
def testTheGearCarriesARenewalAWarningCanBeDismissedPastOf(
    pageDashboard, serverHub,
):
    """A dismissed warning must not take the remedy away with it.

    The warning toast carries the renewal, and a toast has a close
    button — so the remedy was reachable only until the researcher
    tidied it away, which is the moment a busy person tidies it. The
    oracle is that asymmetry: a remedy offered once, on a notice
    designed to be dismissed, is not a remedy. The gear carries the
    same action permanently, and reports what the server says is left.

    Kills: rendering the panel without the session row, leaving the
    renewal reachable only from a toast.
    """
    _fnSeedBlankSandbox(serverHub, S_RENEWAL_SANDBOX)
    _fnEnterTheBlankProject(pageDashboard, serverHub, S_RENEWAL_SANDBOX)
    pageDashboard.wait_for_selector("#btnHostSettings", timeout=15000)
    pageDashboard.click("#btnHostSettings")
    pageDashboard.wait_for_selector("#btnRenewSession", timeout=10000)
    pageDashboard.wait_for_function(
        "() => {const el = document.getElementById('gsSessionRemaining');"
        "return el && el.textContent && el.textContent !== 'Reading\u2026';}",
        timeout=10000,
    )
    sRemaining = pageDashboard.locator("#gsSessionRemaining").inner_text()
    assert "left" in sRemaining or "Never expires" in sRemaining, (
        f"the session row must report the server's countdown, got "
        f"{sRemaining!r}"
    )
    pageDashboard.click("#btnRenewSession")
    pageDashboard.wait_for_selector(".toast.success", timeout=10000)
    assert "Session renewed" in pageDashboard.locator(
        ".toast.success").first.inner_text()
    assert pageDashboard.listPageErrors == [], pageDashboard.listPageErrors
