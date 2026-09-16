"""One row per remote, two halves per row, with disjoint file lists.

The scope split of 2026-08-26 gave Level 3 its own question -- does
the published reproducibility envelope match the local one -- and
first answered it in a separate "Published envelope" section. That
section duplicated the copies rows by name, so a green Level 2 banner
sat over red envelope badges with the explanation in a section the
researcher had no reason to open. The 2026-09-16 ruling merged them:
ONE row per remote in Published copies, a Level 2 cell and a Level 3
cell on that row, and the row's detail split into two LABELED halves.

The disjointness survives the merge and is still what this drives
through a real browser: Level 2 publishes the generating data and
Level 3 publishes what a third party needs in order to re-run it, so
a researcher scanning the Level 2 half for why their data is
unpublished must not find ``reproduce.sh`` among the answers.

The state asserted is unattained. The seeded host project has never
had a GitHub verify, so the honest answer is "not proven" -- and the
criterion blocks on unproven by design. An attained Level 3 cell here
would mean the criterion had been made vacuous.
"""

import pytest

from tests.browser.conftest import fnOpenTheSeededHostWorkflow


pytestmark = pytest.mark.browser

S_COPIES_GROUP = "publishedCopies"
S_ENVELOPE_FILE = "reproduce.sh"

_S_READ_MERGED_ROW = """(sRowKey) => {
    const elHeader = Array.from(document.querySelectorAll(
        '.requirement-row-header')).find(
            el => (el.dataset.req || '') === sRowKey);
    if (!elHeader) return {bFound: false};
    const elRow = elHeader.closest('.requirement-row');
    const flistPaths = (sHalf) => Array.from(
        elRow.querySelectorAll(sHalf + ' .wf-file-link')).map(
            el => el.dataset.path || '');
    return {
        bFound: true,
        sGroup: (elRow.closest('.requirement-group')
            .querySelector('.requirement-group-header')
            .dataset.group || ''),
        listLevelTwoPaths: flistPaths('.sync-level-two-files'),
        listLevelThreePaths: flistPaths('.sync-level-three-envelope'),
        listCellTitles: Array.from(
            elHeader.querySelectorAll('.step-level-cell')).map(
                el => el.getAttribute('title') || ''),
    };
}"""

_S_COUNT_GROUPS = """() => Array.from(document.querySelectorAll(
    '.requirement-group-header')).map(el => el.dataset.group || '')"""


def _fnExpandEverything(pageDashboard):
    """Open every group and every row so all file lists render."""
    iGroups = pageDashboard.locator(".requirement-group-header").count()
    for iIndex in range(iGroups):
        pageDashboard.locator(
            ".requirement-group-header",
        ).nth(iIndex).click()
    pageDashboard.wait_for_selector(
        ".requirement-row-title", timeout=10000,
    )
    iRows = pageDashboard.locator(".requirement-row-header").count()
    for iIndex in range(iRows):
        pageDashboard.locator(
            ".requirement-row-header",
        ).nth(iIndex).click()


@pytest.mark.falsification
def test_the_merged_row_keeps_its_two_halves_disjoint(
    pageDashboard, serverHub,
):
    """The merged GitHub row lists the envelope only in its L3 half.

    Kills: disable the listExcludePaths filter in
    _fsRenderRemoteFileRows, which puts the envelope files back among
    the Level 2 file groups -- the exact symptom the researcher
    reported after the scope split had supposedly landed, one
    structure later.
    """
    fnOpenTheSeededHostWorkflow(
        pageDashboard, serverHub, bAwaitProjectBlock=True,
    )
    _fnExpandEverything(pageDashboard)

    listGroups = pageDashboard.evaluate(_S_COUNT_GROUPS)
    assert "publishedEnvelope" not in listGroups, (
        "the separate Published envelope section is back; the merge "
        f"is undone: {listGroups}"
    )

    dictGithub = pageDashboard.evaluate(_S_READ_MERGED_ROW, "github")
    assert dictGithub["bFound"], "the GitHub mirror row is gone"
    assert dictGithub["sGroup"] == S_COPIES_GROUP, (
        "the GitHub row left the Published copies section: "
        f"{dictGithub['sGroup']}"
    )
    dictZenodo = pageDashboard.evaluate(_S_READ_MERGED_ROW, "zenodo")
    assert dictZenodo["bFound"], "the Zenodo archive row is gone"

    # The disjointness that WAS the split and survives the merge.
    assert S_ENVELOPE_FILE in dictGithub["listLevelThreePaths"], (
        "the Level 3 half lists no envelope files, so the merge made "
        "them invisible rather than moving them: "
        f"{dictGithub['listLevelThreePaths']}"
    )
    assert S_ENVELOPE_FILE not in dictGithub["listLevelTwoPaths"], (
        f"{S_ENVELOPE_FILE} is listed in the Level 2 half, where it "
        "reads as a reason the researcher's data is unpublished: "
        f"{dictGithub['listLevelTwoPaths']}"
    )

    # One row, one cell per level -- and the strip carries BOTH.
    sTitles = " | ".join(dictGithub["listCellTitles"])
    assert "Level 2:" in sTitles and "Level 3:" in sTitles, (
        f"the merged row does not claim both levels: {sTitles}"
    )

    # Unproven blocks. This project has never had a GitHub verify, so
    # an attained Level 3 cell would mean the criterion went vacuous.
    assert "Level 3: met" not in sTitles, (
        "the Level 3 cell reports met on a project that has never "
        f"run a GitHub verify: {sTitles}"
    )

    assert pageDashboard.listPageErrors == []
