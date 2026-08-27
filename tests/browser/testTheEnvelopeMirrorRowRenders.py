"""Two published-copy sections, one per level, with disjoint files.

The scope split gave Level 3 its own question -- does the published
reproducibility envelope match the local one -- and the Project block
answers it in its own section, "Published envelope", parallel to the
Level 2 "Published copies" section (researcher's ruling, 2026-08-26).

The two sections must not merely both exist; their FILE LISTS must be
disjoint, and that is what this drives through a real browser. Level 2
publishes the generating data and Level 3 publishes what a third party
needs in order to re-run it, so a researcher scanning Level 2 for why
their data is unpublished must not find ``reproduce.sh`` among the
answers. The gates were made scope-aware first and the rows were not,
which is exactly how the researcher came to see ``reproduce.sh``
listed under the Level 2 GitHub mirror after the split had supposedly
landed.

Selection is by SECTION, never by row title: both sections contain a
row titled "GitHub mirror" -- that parallel is the point -- so a
find-first-by-title lookup would silently assert against the Level 2
row while appearing to test the Level 3 one.

The state asserted is `red`. The seeded host project has never had a
GitHub verify, so the honest answer is "not proven" -- and the
criterion blocks on unproven by design, symmetric with the Level 2
gate. A green row here would mean the criterion had been made vacuous.

Kills (confirmed, not assumed): dropping the publishedEnvelope section
from fsRenderProjectBlock fails the presence assertion; merging its
row back into publishedCopies fails the placement assertion; removing
the exclude-list filter in _fsRenderRemoteFileRows fails the
disjointness assertion.
"""

import pytest

from tests.browser.conftest import fnOpenTheSeededHostWorkflow


pytestmark = pytest.mark.browser

S_ROW_TITLE = "GitHub mirror"
S_LEVEL2_GROUP = "publishedCopies"
S_LEVEL3_GROUP = "publishedEnvelope"

# Read out of the section bodies and compared, rather than asserted as
# a literal list: the envelope membership is the backend's to define
# and this test is about where the files LAND, not what they are.
S_ENVELOPE_FILE = "reproduce.sh"

_S_READ_SECTION = """(sGroup) => {
    const listHeaders = Array.from(document.querySelectorAll(
        '.requirement-group-header'));
    const elHeader = listHeaders.find(
        el => (el.dataset.group || '') === sGroup);
    if (!elHeader) return {bFound: false};
    const elGroup = elHeader.closest('.requirement-group');
    const flistText = (sSelector) => Array.from(
        elGroup.querySelectorAll(sSelector)).map(
            el => (el.textContent || '').trim());
    return {
        bFound: true,
        listRowTitles: flistText('.requirement-row-title'),
        listFilePaths: Array.from(
            elGroup.querySelectorAll('.wf-file-link')).map(
                el => el.dataset.path || ''),
        sMarkup: elGroup.className + ' ' + Array.from(
            elGroup.querySelectorAll('.requirement-row')).map(
                el => el.className).join(' '),
    };
}"""




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
def test_the_two_published_sections_are_parallel_and_disjoint(
    pageDashboard, serverHub,
):
    """The two published sections exist and their file lists are disjoint.

    Kills: disable the listExcludePaths filter in
    _fsRenderRemoteFileRows, which puts the envelope files back among
    the Level 2 published-copies rows -- the exact symptom the
    researcher reported after the scope split had supposedly landed.
    """
    fnOpenTheSeededHostWorkflow(pageDashboard, serverHub, bAwaitProjectBlock=True)
    _fnExpandEverything(pageDashboard)

    dictLevel3 = pageDashboard.evaluate(
        _S_READ_SECTION, S_LEVEL3_GROUP,
    )
    dictLevel2 = pageDashboard.evaluate(
        _S_READ_SECTION, S_LEVEL2_GROUP,
    )

    assert dictLevel3["bFound"], (
        "there is no 'Published envelope' section, so the Level 3 "
        "published-copy criterion blocks the researcher with nothing "
        "on screen naming it"
    )
    assert dictLevel2["bFound"], "the Level 2 section vanished"

    assert S_ROW_TITLE in " ".join(dictLevel3["listRowTitles"]), (
        "the Level 3 section has no GitHub mirror row: "
        f"{dictLevel3['listRowTitles']}"
    )

    # The permanent-archive twin (2026-08-26, reversing the same-day
    # GitHub-only ruling): the section must also carry the Zenodo
    # archive row, or the criterion blocks with nothing naming it.
    assert "Zenodo archive" in " ".join(dictLevel3["listRowTitles"]), (
        "the Level 3 section has no Zenodo archive row: "
        f"{dictLevel3['listRowTitles']}"
    )

    # The disjointness that IS the split. An envelope file listed in
    # the Level 2 section reports a reproducibility problem as a
    # reason the researcher's DATA is unpublished.
    assert S_ENVELOPE_FILE in dictLevel3["listFilePaths"], (
        "the Level 3 section lists no envelope files, so the split "
        "made them invisible rather than moving them: "
        f"{dictLevel3['listFilePaths']}"
    )
    assert S_ENVELOPE_FILE not in dictLevel2["listFilePaths"], (
        f"{S_ENVELOPE_FILE} is still listed under the Level 2 "
        "published-copies section, where it reads as a reason the "
        f"researcher's data is unpublished: {dictLevel2['listFilePaths']}"
    )

    # Unproven blocks. This project has never had a GitHub verify, so
    # a passing row would mean the criterion had gone vacuous.
    assert "green" not in dictLevel3["sMarkup"], (
        "the envelope row reports a match on a project that has never "
        f"run a GitHub verify: {dictLevel3['sMarkup']!r}"
    )

    assert pageDashboard.listPageErrors == []
