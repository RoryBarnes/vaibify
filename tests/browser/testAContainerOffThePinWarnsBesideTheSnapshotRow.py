"""A container running a different image than the pin raises the ⚠.

The mismatch between the envelope's pinned image and the image the
open container runs was reported only INSIDE the expanded Environment
snapshot row, under a collapsed Artifacts group, so a researcher who
had just rebuilt saw Level 3 and nothing else (researcher-reported,
2026-09-28). The ruling: the level stands -- the published record is
still true -- and the ⚠ rides BESIDE the row and on the Artifacts
heading, as the sandbox-deposit warning does.

Three things only a rendered page shows: the glyph appears on the row
and on the collapsed heading for a determined mismatch; it appears for
NEITHER "nothing determined" NOR "derived" (agents stacked on the pin);
and the row's Level 3 cell does not move.
"""

import pytest

from tests.browser.conftest import fnOpenTheSeededHostWorkflow


pytestmark = pytest.mark.browser


_S_RENDER_ARTIFACTS = """(dictArgs) => {
    return VaibifyWorkflowRequirements.fsRenderProjectBlock({
        dictWorkflowEnvelopeDetail: {
            listLevel3EnvelopePaths: [],
            dictArtifacts: {
                environmentSnapshot: {bPresent: true, bSatisfied: true},
            },
            dictImageCurrency: dictArgs.dictImageCurrency,
            listBinaries: [],
        },
        dictRemoteChecks: {},
        setToggledFileGroups: new Set(),
        bProjectBlockCollapsed: false,
        setExpandedRequirementGroups: new Set(dictArgs.listOpenGroups),
        setExpandedRequirementRows: new Set(),
    });
}"""

_DICT_MISMATCH = {
    "bPinnedImageIsLive": False,
    "sPinnedImageDigest": "ai-project@sha256:" + "5" * 64,
    "sLiveImageDigest": "ai-project@sha256:" + "e" * 64,
}


def _fsRender(pageDashboard, dictImageCurrency, listOpenGroups):
    return pageDashboard.evaluate(_S_RENDER_ARTIFACTS, {
        "dictImageCurrency": dictImageCurrency,
        "listOpenGroups": listOpenGroups,
    })


def _fsSnapshotRow(sHtml):
    sRow = sHtml.split('data-req="environmentSnapshot"')[1]
    return sRow.split('data-req="')[0]


def _fsRowHeader(sRow):
    """Return the row's header markup: title, glyph and level cells."""
    return sRow.split("requirement-row-header")[1].split(
        "requirement-next-step")[0].split('<div class="requirement-row-detail')[0]


def _fsArtifactsHeading(sHtml):
    """Return the Artifacts group's heading, up to its level strip."""
    sHeading = sHtml.split('data-group="artifacts"')[1]
    return sHeading.split("level-cell")[0]


@pytest.mark.falsification
def test_a_container_off_the_pin_raises_the_glyph_and_nothing_else(
    pageDashboard, serverHub,
):
    """ONE open, every assertion: the seeded project is leased.

    Kills: not raising the row warning on a determined mismatch.
    """
    fnOpenTheSeededHostWorkflow(pageDashboard, serverHub)
    sOpen = _fsRender(pageDashboard, _DICT_MISMATCH, ["artifacts"])
    sRow = _fsSnapshotRow(sOpen)
    assert "requirement-row-warning" in sRow
    assert "different image" in sRow
    sClosed = _fsRender(pageDashboard, _DICT_MISMATCH, [])
    assert "requirement-group-warning" in _fsArtifactsHeading(sClosed), (
        "a collapsed Artifacts group must still show the warning"
    )
    sQuiet = _fsRender(
        pageDashboard, {"bPinnedImageIsLive": True}, ["artifacts"],
    )
    sQuietHeader = _fsRowHeader(_fsSnapshotRow(sQuiet))
    assert "requirement-row-warning" not in sQuietHeader
    sGlyph = sRow.split('<span class="requirement-row-warning"')[1]
    sGlyph = '<span class="requirement-row-warning"' + sGlyph.split(
        "</span>")[0] + "</span>"
    assert _fsRowHeader(sRow).replace(sGlyph, "") == sQuietHeader, (
        "the glyph is the ONLY difference: the row's colour and its "
        "Level 3 cell do not move"
    )
    for dictNoVerdict in (
        {"bPinnedImageIsLive": None},
        {"bPinnedImageIsLive": None, "sRelation": "derived"},
    ):
        sHtml = _fsRender(pageDashboard, dictNoVerdict, [])
        assert "requirement-group-warning" not in _fsArtifactsHeading(sHtml), (
            dictNoVerdict
        )
