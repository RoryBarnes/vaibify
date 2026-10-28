"""The Zenodo archive block offers Verify before the researcher publishes.

The archive half of Level 3 asks for an attestation IN the archive, and
a Zenodo version cannot be changed, so publishing before verifying costs
a second version. The verification lived only in the Run menu and the
Rebuild attestation row, and was reported as hard to find
(researcher-reported, 2026-10-27).
"""

import re

import pytest

from tests.browser.conftest import fnOpenTheSeededHostWorkflow


pytestmark = pytest.mark.browser


_S_RENDER_ZENODO_ROW = """(bAttestationCurrent) => {
    return VaibifyWorkflowRequirements.fsRenderProjectBlock({
        dictWorkflowEnvelopeDetail: {
            listLevel3EnvelopePaths: [],
            dictArtifacts: {},
            dictImageCurrency: {bPinnedImageIsLive: null},
            listBinaries: [],
            bEnvelopeInGithubMirror: true,
            bEnvelopeInZenodoArchive: false,
            bRebuildAttestationCurrent: bAttestationCurrent,
            dictArchivePermanence: {
                sImageArchivePermanence: 'permanent',
                sProjectArchivePermanence: 'permanent',
            },
            dictArchivedAttestation: {bCoversArchivedManifest: null},
            dictRemoteSyncs: {zenodo: {sZenodoDoiVerified: ''}},
            dictZenodoCrossInstance: {},
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
def test_the_zenodo_row_offers_verify_while_the_attestation_is_not_current(
    pageDashboard, serverHub,
):
    """Kills: dropping the Verify offer from the Zenodo archive block."""
    fnOpenTheSeededHostWorkflow(pageDashboard, serverHub)
    sRow = _fsSelectRow(
        pageDashboard.evaluate(_S_RENDER_ZENODO_ROW, False), "zenodo")
    assert 'data-wf-action="verify-l3"' in sRow, sRow[:2000]
    assert "publishing first costs a second Zenodo version" in sRow


def test_a_current_attestation_offers_no_verify_on_the_zenodo_row(
    pageDashboard, serverHub,
):
    fnOpenTheSeededHostWorkflow(pageDashboard, serverHub)
    sRow = _fsSelectRow(
        pageDashboard.evaluate(_S_RENDER_ZENODO_ROW, True), "zenodo")
    assert 'data-wf-action="verify-l3"' not in sRow
