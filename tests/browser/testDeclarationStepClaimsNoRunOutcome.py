"""A step that runs no commands must not report a run outcome.

A researcher opened their project and found the AI Declaration step
wearing a red run light. Hovering it said "last run failed". They had
never run it, and there is nothing there to run: the declaration
kind's command block is empty by construction
(``fdictBuildAiDeclarationStep``) — its whole content is a markdown
file and an attestation. The step is nonetheless ``bInteractive``, so
a Run All pauses on it, and every way that pause can end — abandoned
after the wait window, dismissed — is recorded through the same light
that otherwise reports whether work succeeded. So the column was
answering a question about the researcher's response to a prompt in
the vocabulary of a failed computation.

The column is now declared NOT-APPLICABLE for such a step, in the same
muted-dash vocabulary the level strip already uses for "there is
nothing here to attain". That is deliberately not suppression: a
suppressed light hides a state that exists, and this one reports that
no execution state exists. Whether the declaration is signed lives in
the L2 cell, which is where the researcher was looking anyway.

The predicate keys on the COMMANDS, not on ``sStepKind`` alone, so a
declaration step that somehow acquires a command gets its light back
and reports honestly rather than being permanently exempt.

Both surfaces are asserted, because a fix confined to the collapsed
row could move "failed" into the expanded body rather than retiring
the claim. Checking that turned up a fact worth recording: the
expanded body is ALREADY silent, because the "Last run" line renders
only from ``_fsRenderLevelOneBody`` and a declaration step takes the
"no requirements at this level" branch instead. So the second
assertion pins a property the code already had rather than one this
change introduced — which is exactly the assertion worth keeping, as
nothing else stops a future edit from routing declaration steps
through the ordinary L1 body and reintroducing the claim there.

Kills (confirmed, not assumed): returning ``false`` from
``fbStepRunsNoCommands`` -> the declaration step renders an ordinary
``.step-status`` light and the first assertion fails naming it.
"""

import pytest

from tests.browser.conftest import (
    fnOpenTheSeededHostWorkflow,
    S_HOST_DECLARATION_STEP_NAME,
    S_HOST_STEP_NAME,
)


pytestmark = pytest.mark.browser

S_DECLARATION_ROW = (
    f'.step-item:has-text("{S_HOST_DECLARATION_STEP_NAME}")'
)




def test_the_declaration_step_reports_no_run_outcome(
    pageDashboard, serverHub,
):
    """One test, both surfaces — the session holds one browser."""
    fnOpenTheSeededHostWorkflow(pageDashboard, serverHub)
    pageDashboard.wait_for_selector(S_DECLARATION_ROW, timeout=20000)

    # 1. The collapsed row's execution column carries no light at all.
    iLights = pageDashboard.locator(
        S_DECLARATION_ROW + " .step-status",
    ).count()
    assert iLights == 0, (
        "the AI Declaration step runs no commands, so it has no "
        f"execution outcome to report, but {iLights} run light(s) "
        "rendered — every state one can show is a claim about work "
        "that does not exist"
    )

    # 2. The cell is still THERE, so the columns stay aligned and the
    #    researcher can find out why it is empty.
    elCell = pageDashboard.locator(
        S_DECLARATION_ROW + " .step-status-cell",
    ).first
    assert elCell.count() == 1
    sTooltip = elCell.get_attribute("title") or ""
    assert "no commands" in sTooltip, (
        f"the empty cell must say why it is empty: {sTooltip!r}"
    )
    assert "failed" not in sTooltip, sTooltip

    # 3. An ordinary step is untouched — the change must not have
    #    retired the run light generally.
    assert pageDashboard.locator(
        f'.step-item:has-text("{S_HOST_STEP_NAME}") .step-status',
    ).count() == 1, (
        "a step with real commands must still carry its run light"
    )

    # 4. The expanded body makes no run claim either, so the fix is
    #    not merely relocating "failed" out of the collapsed row.
    pageDashboard.click(S_DECLARATION_ROW)
    sDeclarationBody = (
        f'.step-wrapper:has-text("{S_HOST_DECLARATION_STEP_NAME}")'
    )
    pageDashboard.wait_for_selector(
        sDeclarationBody + " .step-level-section", timeout=15000,
    )
    assert pageDashboard.locator(
        sDeclarationBody + " .step-last-run",
    ).count() == 0, (
        "the expanded declaration step rendered a Last run line — a "
        "run outcome for a step that runs nothing"
    )

    # The ordinary step still has one, so assertion 4 is not passing
    # because the selector matches nothing anywhere.
    pageDashboard.click(f'.step-item:has-text("{S_HOST_STEP_NAME}")')
    elOrdinaryLastRun = pageDashboard.locator(
        f'.step-wrapper:has-text("{S_HOST_STEP_NAME}") .step-last-run',
    ).first
    elOrdinaryLastRun.wait_for(state="visible", timeout=15000)

    assert pageDashboard.listPageErrors == []
