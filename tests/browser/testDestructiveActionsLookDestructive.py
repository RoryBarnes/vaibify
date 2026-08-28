"""An action that discards recorded work must not look like one that records it.

"Delete rules…" sits directly beneath "Declare rules" in the
Determinism row, and the two were rendered identically — same class,
same colour, same weight. The researcher who asked for this read the
pair as two "Declare rules" buttons and reported a duplicate-button
bug that did not exist. Two controls with opposite consequences were
indistinguishable at a glance.

RED, not orange, and the distinction is not cosmetic: orange already
means "not checked yet" on every remote badge and level cell in this
UI, so spending it on "dangerous" would make one colour carry two
unrelated meanings on the same screen. Red is what
``.container-menu-item.danger`` already uses for destructive menu
items, so this follows a convention rather than inventing one.

The confirm dialog is unchanged and still fires. It is the signal
AFTER the click; this is the one available before it.

The declaration is driven first, because "Delete rules…" does not
exist until rules have been declared. An "if the button is present"
guard would make the assertion vacuous on this fixture — it is absent
until the click, so nothing would be checked. That is exactly how the
first draft of this test passed while asserting nothing.
"""

import pytest

from tests.browser.conftest import fnOpenTheSeededHostWorkflow


pytestmark = pytest.mark.browser

_S_READ_DANGER_BY_ACTION = """() => {
    const dict = {};
    document.querySelectorAll('[data-wf-action]').forEach(el => {
        dict[el.dataset.wfAction] =
            el.classList.contains('wf-action-danger');
    });
    return dict;
}"""


def _fnExpandEverything(pageDashboard):
    """Open every group and every row, idempotently."""
    iGroups = pageDashboard.locator(".requirement-group-header").count()
    for iIndex in range(iGroups):
        elGroup = pageDashboard.locator(
            ".requirement-group-header",
        ).nth(iIndex)
        if elGroup.evaluate(
            "el => el.closest('.requirement-group')"
            ".querySelectorAll('.requirement-row').length === 0",
        ):
            elGroup.click()
    pageDashboard.wait_for_selector(
        ".requirement-row-title", timeout=10000,
    )
    iRows = pageDashboard.locator(".requirement-row-header").count()
    for iIndex in range(iRows):
        elRow = pageDashboard.locator(
            ".requirement-row-header",
        ).nth(iIndex)
        if elRow.evaluate(
            "el => !el.closest('.requirement-row')"
            ".classList.contains('expanded')",
        ):
            elRow.click()


@pytest.mark.falsification
def test_delete_rules_is_painted_destructive_and_declare_is_not(
    pageDashboard, serverHub,
):
    """The destructive action is marked; its neighbour is not.

    Both halves matter. Asserting only that Delete is red would pass
    against a stylesheet that painted every button red, which would
    destroy the distinction it exists to draw.

    Kills: dropping the ``wf-action-danger`` class from the
    destructive branch of ``_fsRenderActionButton`` — the Delete
    button then renders exactly like the Declare button above it.
    """
    fnOpenTheSeededHostWorkflow(
        pageDashboard, serverHub, bAwaitProjectBlock=True,
    )
    _fnExpandEverything(pageDashboard)

    dictBefore = pageDashboard.evaluate(_S_READ_DANGER_BY_ACTION)
    assert "declare-determinism" in dictBefore, (
        f"the Determinism row rendered no declare action: "
        f"{sorted(dictBefore)}"
    )
    assert dictBefore["declare-determinism"] is False, (
        "the action that RECORDS a declaration is painted destructive"
    )

    pageDashboard.click('[data-wf-action="declare-determinism"]')
    pageDashboard.wait_for_selector(
        '[data-wf-action="delete-determinism"]', timeout=10000,
    )

    dictAfter = pageDashboard.evaluate(_S_READ_DANGER_BY_ACTION)
    assert dictAfter.get("delete-determinism") is True, (
        "Delete rules discards recorded work but is painted like the "
        "Declare button directly above it — the pair the researcher "
        "misread as one repeated control"
    )
    assert dictAfter.get("declare-determinism") is False, (
        "the destructive paint leaked onto the recording action, so "
        "the two are indistinguishable again"
    )
    assert pageDashboard.listPageErrors == []
