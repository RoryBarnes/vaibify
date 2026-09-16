"""A compared-but-required-by-nothing file is not a Level 2 defect.

The attestation and the AI-provenance stamp are compared against
every remote so their badges are truthful -- and required by no
criterion in that comparison, so they are excluded from Level 2. The
copies rows used to render them in the ordinary Level 2 file list
anyway: a red badge under a green Level 2 cell, with nothing on the
screen explaining the pair (external review, 2026-09-16). Both facts
now render together in one informational block, and the Level 2 list
carries only Level 2 files.
"""

import pytest

from tests.browser.conftest import fnOpenTheSeededHostWorkflow


pytestmark = pytest.mark.browser


_S_STAMP = ".vaibify/ai_provenance.json"

_S_RENDER_COPIES_ROW = """(dictArgs) => {
    VaibifyGitBadges.flistFilesForRemote = function () {
        return dictArgs.listRemoteFiles;
    };
    return VaibifyWorkflowRequirements.fsRenderProjectBlock({
        dictWorkflowEnvelopeDetail: {
            listLevel3EnvelopePaths: [],
            listComparedNotRequiredPaths: dictArgs.listNotRequired,
            listBinaries: [],
            dictArtifacts: {},
            dictRemoteSyncs: {},
        },
        dictRemoteChecks: {},
        setToggledFileGroups: new Set(),
        bProjectBlockCollapsed: false,
        setExpandedRequirementGroups: new Set(["publishedCopies"]),
        setExpandedRequirementRows: new Set(["github"]),
    });
}"""


@pytest.mark.falsification
def test_the_stamp_renders_informationally_and_not_as_level_two(
    pageDashboard, serverHub,
):
    """ONE open, every state: the seeded project is leased.

    Kills: leaving the compared-not-required paths in the Level 2
    exclusion out -- the stamp then renders among the Level 2 file
    groups, where its honest badge reads as an unpublished-data
    defect under a green cell.
    """
    fnOpenTheSeededHostWorkflow(pageDashboard, serverHub)
    sHtml = pageDashboard.evaluate(_S_RENDER_COPIES_ROW, {
        "listRemoteFiles": ["Data/result.csv", _S_STAMP],
        "listNotRequired": [_S_STAMP],
    })
    assert "Data/result.csv" in sHtml, (
        "the Level 2 file list is empty, so exclusion cannot be told "
        "from nothing rendering at all"
    )
    assert "Informational — this comparison gates nothing" in sHtml, (
        "the informational block is missing; the stamp's badge has "
        "no home that says what it means"
    )
    assert "never lowers a PROOF level" in sHtml, (
        "the block does not say the one thing it exists to say"
    )
    # The title must claim only the COMPARISON: the attestation IS a
    # Level 3 requirement, judged inside the Zenodo archive, and a
    # researcher read the old "never a requirement" as denying that
    # within a day of it shipping.
    assert "IS a Level 3 requirement" in sHtml, (
        "the block no longer says where the attestation's real "
        "requirement lives, so its title reads as denying one exists"
    )
    iBlockStart = sHtml.index("informational-files-block")
    sLevelTwoHalf = sHtml[:iBlockStart]
    assert _S_STAMP not in sLevelTwoHalf, (
        "the stamp still renders in the Level 2 file list above the "
        "informational block: a red badge under a green Level 2 cell"
    )
    assert _S_STAMP in sHtml[iBlockStart:], (
        "the stamp vanished instead of moving: its badge is real "
        "and must stay visible"
    )
