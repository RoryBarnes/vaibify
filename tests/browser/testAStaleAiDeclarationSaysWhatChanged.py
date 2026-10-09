"""A signed AI Declaration goes stale when another step's script changes.

Drives the whole lane against the host lane's real files: write and
attach a declaration, sign it off by clicking the real marker (the
server records what the sign-off covers), edit the first step's script
on disk, and wait for the ordinary poll. The step must then read
*Stale*, carry the server's sentence naming the changed step, and leave
Level 2 blocked; one click signs it off again and the warning goes.
The script is restored afterwards, and the step is put back unsigned.
"""

import os

import pytest

from tests.browser.conftest import (
    S_HOST_DECLARATION_STEP_NAME,
    S_HOST_PROJECT_READY,
    S_HOST_STEP_NAME,
    fnOpenTheSeededHostWorkflow,
)


pytestmark = pytest.mark.browser

S_DECLARATION_BODY = (
    f'.step-wrapper:has-text("{S_HOST_DECLARATION_STEP_NAME}")'
)
S_SIGN_OFF_MARKER = (
    S_DECLARATION_BODY + ' .ai-declaration-attestation '
    '.verification-row[data-approver="user"]'
)
I_POLL_WAIT_MILLISECONDS = 30000


def _fnSignOff(pageDashboard, sExpectedLabel):
    pageDashboard.click(S_SIGN_OFF_MARKER)
    pageDashboard.wait_for_selector(
        S_SIGN_OFF_MARKER + f' .verification-badge:has-text('
        f'"{sExpectedLabel}")', timeout=15000)


@pytest.mark.falsification
def test_a_changed_script_makes_the_sign_off_stale(pageDashboard, serverHub):
    """Kills: the dashboard no longer applying the poll's freshness
    verdict, so the step keeps reading Passed over a stale sign-off.
    """
    sProject = os.path.join(serverHub.sHome, S_HOST_PROJECT_READY)
    sDeclaration = os.path.join(sProject, "AI_USAGE.md")
    sScript = os.path.join(sProject, S_HOST_STEP_NAME, "makeNumbers.py")
    with open(sScript) as fileScript:
        sOriginalScript = fileScript.read()
    with open(sDeclaration, "w") as fileDeclaration:
        fileDeclaration.write("# AI Usage Declaration\n\nNone.\n")
    try:
        fnOpenTheSeededHostWorkflow(pageDashboard, serverHub)
        pageDashboard.click(
            f'.step-item:has-text("{S_HOST_DECLARATION_STEP_NAME}")')
        pageDashboard.wait_for_selector(
            S_DECLARATION_BODY + " .ai-declaration-preview",
            timeout=15000)
        _fnSignOff(pageDashboard, "Passed")
        elLine = pageDashboard.locator(
            S_DECLARATION_BODY + " .ai-declaration-freshness")
        assert elLine.count() == 0, (
            elLine.first.get_attribute("class"), elLine.first.inner_text())

        with open(sScript, "a") as fileScript:
            fileScript.write("# edited after the sign-off\n")
        elStale = pageDashboard.locator(
            S_DECLARATION_BODY + " .ai-declaration-freshness-stale")
        elStale.wait_for(state="visible", timeout=I_POLL_WAIT_MILLISECONDS)
        sSentence = elStale.inner_text()
        assert "'s scripts changed after the AI Declaration was signed " \
            "off" in sSentence, sSentence
        assert "sign off again" in sSentence
        pageDashboard.wait_for_selector(
            S_SIGN_OFF_MARKER + ' .verification-badge:has-text("Stale")',
            timeout=15000)
        sTooltip = pageDashboard.locator(
            f'.step-item:has-text("{S_HOST_DECLARATION_STEP_NAME}") '
            '.step-regression-cell').get_attribute("title") or ""
        assert "scripts changed" in sTooltip, sTooltip

        with open(sScript, "w") as fileScript:
            fileScript.write(sOriginalScript)
        pageDashboard.wait_for_timeout(7000)
        assert pageDashboard.locator(
            S_DECLARATION_BODY + " .ai-declaration-freshness-stale",
        ).count() == 1, "a reverted change must not clear a stale sign-off"

        _fnSignOff(pageDashboard, "Passed")
        pageDashboard.wait_for_selector(
            S_DECLARATION_BODY + " .ai-declaration-freshness-stale",
            state="detached", timeout=I_POLL_WAIT_MILLISECONDS)
        _fnSignOff(pageDashboard, "Failed")
        _fnSignOff(pageDashboard, "Error")
        _fnSignOff(pageDashboard, "Untested")
        assert pageDashboard.listPageErrors == []
    finally:
        with open(sScript, "w") as fileScript:
            fileScript.write(sOriginalScript)
        if os.path.exists(sDeclaration):
            os.remove(sDeclaration)
