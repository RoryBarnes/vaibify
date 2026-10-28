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


_S_RENDER_ZENODO_ROW = """([bAttestationCurrent, dictBlockedRows]) => {
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
            dictBlockedRows: dictBlockedRows,
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
        pageDashboard.evaluate(_S_RENDER_ZENODO_ROW, [False, {}]), "zenodo")
    assert 'data-wf-action="verify-l3"' in sRow, sRow[:2000]
    assert "publishing first costs a second Zenodo version" in sRow


def test_a_current_attestation_offers_no_verify_on_the_zenodo_row(
    pageDashboard, serverHub,
):
    fnOpenTheSeededHostWorkflow(pageDashboard, serverHub)
    sRow = _fsSelectRow(
        pageDashboard.evaluate(_S_RENDER_ZENODO_ROW, [True, {}]), "zenodo")
    assert 'data-wf-action="verify-l3"' not in sRow


def _fsVerifyButtonIn(sRow):
    """Return the markup of the Verify button inside a rendered row."""
    return re.search(
        r'<button[^>]*data-wf-action="verify-l3"[^>]*>', sRow).group(0)


@pytest.mark.falsification
def test_verify_stays_live_while_the_endgame_holds_the_zenodo_row(
    pageDashboard, serverHub,
):
    """The Zenodo row waits on the attestation; the button that makes one must not.

    In the endgame, an attestation that is not current makes the
    ordering hold the archive row back ("do this later, attest first"),
    and every control the row renders was disabled with it -- including
    the Verify control that resolves exactly that wait (found in review,
    2026-10-27). The button waits only when the attestation row does.

    Kills: rendering the Verify control with the Zenodo row's own wait.
    """
    fnOpenTheSeededHostWorkflow(pageDashboard, serverHub)
    sRow = _fsSelectRow(pageDashboard.evaluate(
        _S_RENDER_ZENODO_ROW,
        [False, {"envelopeArchive": "attest before you publish"}]),
        "zenodo")
    assert "disabled" not in _fsVerifyButtonIn(sRow), sRow[:2000]
    sRow = _fsSelectRow(pageDashboard.evaluate(
        _S_RENDER_ZENODO_ROW,
        [False, {"envelopeArchive": "attest before you publish",
                 "rebuildAttestation": "regenerate the envelope first"}]),
        "zenodo")
    assert "disabled" in _fsVerifyButtonIn(sRow)
