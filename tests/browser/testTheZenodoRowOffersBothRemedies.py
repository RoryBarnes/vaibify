"""A record on the other Zenodo offers both remedies, in plain words.

A promoted project's record lives on zenodo.org while its declared
target stayed on the sandbox. The row showed a refusal and one button,
"Start a new concept" -- Zenodo jargon for giving up the version chain
-- while the remedy that keeps the chain lived in a settings dialog
nobody could find (researcher-reported, 2026-09-29).
"""

import re

import pytest

from tests.browser.conftest import fnOpenTheSeededHostWorkflow


pytestmark = pytest.mark.browser


_S_RENDER_ZENODO_ROW = """(dictCrossInstance) => {
    return VaibifyWorkflowRequirements.fsRenderProjectBlock({
        dictWorkflowEnvelopeDetail: {
            listLevel3EnvelopePaths: [],
            dictArtifacts: {},
            dictImageCurrency: {bPinnedImageIsLive: null},
            listBinaries: [],
            bEnvelopeInGithubMirror: true,
            bEnvelopeInZenodoArchive: false,
            dictArchivePermanence: {
                sImageArchivePermanence: 'permanent',
                sProjectArchivePermanence: 'permanent',
            },
            dictArchivedAttestation: {bCoversArchivedManifest: null},
            dictRemoteSyncs: {zenodo: {sZenodoDoiVerified: ''}},
            dictZenodoCrossInstance: dictCrossInstance,
        },
        dictRemoteChecks: {},
        setToggledFileGroups: new Set(),
        bProjectBlockCollapsed: false,
        setExpandedRequirementGroups: new Set(['publishedCopies']),
        setExpandedRequirementRows: new Set(['zenodo']),
    });
}"""


def _fsSelectRow(sHtml, sKey):
    """Return the markup of ONE requirement row, bounded at the next."""
    sTail = sHtml.split('data-req="' + sKey + '"')[1]
    return re.split(r'data-(?:req|group)="', sTail)[0]


@pytest.mark.falsification
def test_a_record_on_the_other_zenodo_offers_both_remedies(
    pageDashboard, serverHub,
):
    """Both buttons, short labels, both sites named above them; no jargon.

    Kills: offering only "start a new record" again, which leaves the
    remedy that keeps the version chain without a control.
    """
    from vaibify.reproducibility import syncBookkeeping
    dictCrossInstance = syncBookkeeping.fdictDescribeCrossInstanceParent({
        "sZenodoService": "sandbox",
        "sZenodoDepositionId": "991",
        "dictRemotes": {"zenodo": {
            "sRecordId": "991", "sDoi": "10.5281/zenodo.991",
            "sService": "zenodo",
        }},
    }, "sandbox")
    fnOpenTheSeededHostWorkflow(pageDashboard, serverHub)
    sRow = _fsSelectRow(
        pageDashboard.evaluate(_S_RENDER_ZENODO_ROW, dictCrossInstance),
        "zenodo",
    )

    assert 'data-wf-action="publish-where-the-zenodo-record-is"' in sRow
    assert "Deposit a new version\u2026" in sRow, sRow[:2000]
    assert 'data-wf-action="start-new-zenodo-concept"' in sRow
    assert "Start a new deposit\u2026" in sRow
    assert "zenodo.org (permanent)" in sRow
    assert "the Zenodo sandbox (testing only)" in sRow
    assert "10.5281/zenodo.991" in sRow
    assert "concept" not in re.sub(r'data-wf-action="[^"]*"', "", sRow), (
        "Zenodo's word for the record group reached the researcher"
    )


def test_no_mismatch_offers_neither(pageDashboard, serverHub):
    fnOpenTheSeededHostWorkflow(pageDashboard, serverHub)
    sRow = _fsSelectRow(
        pageDashboard.evaluate(_S_RENDER_ZENODO_ROW, {}), "zenodo",
    )
    assert "publish-where-the-zenodo-record-is" not in sRow
    assert "start-new-zenodo-concept" not in sRow
