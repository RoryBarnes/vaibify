"""The Level 3 published-copy row reaches the screen, in its own group.

The scope split gave Level 3 its own question -- does the published
reproducibility envelope match the local one -- and a row to answer
it. A backend criterion nobody can see is a criterion that will be
re-derived, argued about, and eventually removed by someone who
cannot find where it surfaces, so this drives the row through a real
browser.

Placement carries the argument and is asserted as such. The row sits
with the envelope ARTIFACTS it is about, not beside the Level 2 sync
rows, because putting it there would re-suggest exactly the coupling
the split removed: Level 2 publishes the generating data, Level 3
publishes what a third party needs to re-run it, and a researcher
scanning the Level 2 group must not find a reproduce.sh problem
reported among reasons their data is unpublished.

The state asserted is `red`. The seeded host project has never had a
GitHub verify, so the honest answer is "not proven" -- and the
criterion blocks on unproven by design, symmetric with the Level 2
gate. A green row here would mean the criterion had been made
vacuous.

Kills (confirmed, not assumed): removing _flistEnvelopeMirrorRows
from the artifacts group fails the presence assertion; moving it into
the publishedCopies group fails the placement assertion.
"""

import pytest

from tests.browser.conftest import (
    S_HOST_PROJECT_READY,
    S_HOST_STEP_NAME,
    S_HOST_WORKFLOW_NAME,
)


pytestmark = pytest.mark.browser

S_ROW_TITLE = "Envelope matches the GitHub mirror"


def _fnOpenTheHostWorkflow(pageDashboard, serverHub):
    pageDashboard.goto(serverHub.fsBootstrapUrl(), wait_until="load")
    pageDashboard.wait_for_selector(
        f'.container-tile[data-name="{S_HOST_PROJECT_READY}"]',
        timeout=15000,
    )
    pageDashboard.click(
        f'.container-tile[data-name="{S_HOST_PROJECT_READY}"] '
        '.container-tile-main',
    )
    pageDashboard.wait_for_selector("#modalConfirm", timeout=10000)
    pageDashboard.click("#btnConfirmOk")
    pageDashboard.wait_for_selector(
        f"text={S_HOST_WORKFLOW_NAME}", timeout=20000,
    )
    pageDashboard.click(f"text={S_HOST_WORKFLOW_NAME}")
    pageDashboard.wait_for_selector(
        f"text={S_HOST_STEP_NAME}", timeout=20000,
    )
    pageDashboard.wait_for_selector(
        ".project-block-header", timeout=20000,
    )


def test_the_envelope_row_renders_in_the_artifacts_group(
    pageDashboard, serverHub,
):
    _fnOpenTheHostWorkflow(pageDashboard, serverHub)

    # Open every requirement group so all rows render; the row's
    # group is asserted below rather than assumed by which one is
    # clicked.
    iGroups = pageDashboard.locator(".requirement-group-header").count()
    for iIndex in range(iGroups):
        pageDashboard.locator(
            ".requirement-group-header",
        ).nth(iIndex).click()
    pageDashboard.wait_for_selector(
        ".requirement-row-title", timeout=10000,
    )

    dictPlacement = pageDashboard.evaluate(
        """(sTitle) => {
            const listTitles = Array.from(document.querySelectorAll(
                '.requirement-row-title'));
            const elRow = listTitles.find(
                el => (el.textContent || '').indexOf(sTitle) !== -1);
            if (!elRow) return {bFound: false};
            const elGroup = elRow.closest('.requirement-group');
            const elHeader = elGroup
                ? elGroup.querySelector('.requirement-group-header')
                : null;
            return {
                bFound: true,
                sGroup: elHeader ? (elHeader.dataset.group || '') : '',
            };
        }""",
        S_ROW_TITLE,
    )

    assert dictPlacement["bFound"], (
        f"the Level 3 published-copy row ({S_ROW_TITLE!r}) does not "
        "render at all, so the criterion behind it is invisible to "
        "the researcher it blocks"
    )
    assert dictPlacement["sGroup"] == "artifacts", (
        "the envelope row must sit with the envelope artifacts, not "
        "among the Level 2 published-copies rows — putting it there "
        "reports a reproduce.sh problem as a reason the researcher's "
        f"DATA is unpublished. Found in group: "
        f"{dictPlacement['sGroup']!r}"
    )

    # Unproven blocks. This project has never had a GitHub verify, so
    # a passing row would mean the criterion had gone vacuous.
    sState = pageDashboard.evaluate(
        """(sTitle) => {
            const listTitles = Array.from(document.querySelectorAll(
                '.requirement-row-title'));
            const elRow = listTitles.find(
                el => (el.textContent || '').indexOf(sTitle) !== -1);
            const elHeader = elRow.closest('.requirement-row-header')
                || elRow.parentElement;
            return (elHeader ? elHeader.className : '') +
                ' ' + (elRow.className || '');
        }""",
        S_ROW_TITLE,
    )
    assert "green" not in sState, (
        "the envelope row reports a match on a project that has never "
        f"run a GitHub verify: {sState!r}"
    )

    assert pageDashboard.listPageErrors == []
