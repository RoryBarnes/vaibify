"""The Zenodo row says which PROOF receipt the latest version carries.

The archive is a checkpoint, and every content-changing archive is a
pair: a checkpoint version, then a completion version that records
Level 2. The row renders the server's verdict on which receipt the
latest version carries -- including "verification pending" after a
completion, which is the truth until a verify of the new record
succeeds -- and offers the completion button only when the server
offers it. An interrupted completion is named as one, never as an
environment-archive promotion.
"""

import re

import pytest

from tests.browser.conftest import fnOpenTheSeededHostWorkflow


pytestmark = pytest.mark.browser


_S_RENDER_ZENODO_ROW = """(dictProofReceipt) => {
    return VaibifyWorkflowRequirements.fsRenderProjectBlock({
        dictWorkflowEnvelopeDetail: {
            listLevel3EnvelopePaths: [],
            dictArtifacts: {},
            dictImageCurrency: {bPinnedImageIsLive: null},
            listBinaries: [],
            bEnvelopeInGithubMirror: true,
            bEnvelopeInZenodoArchive: true,
            bRebuildAttestationCurrent: true,
            dictArchivePermanence: {
                sImageArchivePermanence: 'permanent',
                sProjectArchivePermanence: 'permanent',
            },
            dictArchivedAttestation: {bCoversArchivedManifest: true},
            dictRemoteSyncs: {zenodo: {sZenodoDoiVerified: ''}},
            dictZenodoCrossInstance: {},
            dictBlockedRows: {},
            dictProofReceipt: dictProofReceipt,
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


def _fsRenderZenodoRow(pageDashboard, dictProofReceipt):
    return _fsSelectRow(pageDashboard.evaluate(
        _S_RENDER_ZENODO_ROW, dictProofReceipt), "zenodo")


@pytest.mark.falsification
def test_the_zenodo_row_renders_the_receipt_verdict_and_its_offer(
    pageDashboard, serverHub,
):
    """ONE open, every state: the server's sentence, and its offer only.

    Kills: the completion button offered without the server's offer.
    """
    fnOpenTheSeededHostWorkflow(pageDashboard, serverHub)
    sRow = _fsRenderZenodoRow(pageDashboard, {
        "sState": "checkpoint", "bOfferCompletion": True,
        "sMessage": "The latest version, record 101, carries a "
                    "checkpoint receipt."})
    assert 'data-receipt-state="checkpoint"' in sRow, sRow[:2000]
    assert "carries a checkpoint receipt" in sRow
    assert 'data-wf-action="archive-zenodo-completion"' in sRow
    sRow = _fsRenderZenodoRow(pageDashboard, {
        "sState": "completion-pending", "bOfferCompletion": False,
        "sMessage": "Completion archived as record 102; verification "
                    "pending."})
    assert "verification pending" in sRow
    assert 'data-wf-action="archive-zenodo-completion"' not in sRow, (
        "the row offered a completion the server did not offer")
    sRow = _fsRenderZenodoRow(pageDashboard, None)
    assert "proof-receipt-state" not in sRow


def test_the_completion_outcome_is_the_servers(pageDashboard, serverHub):
    fnOpenTheSeededHostWorkflow(pageDashboard, serverHub)
    dictRefused = pageDashboard.evaluate(
        """() => VaibifyApp.fdictDescribeCompletionOutcome({
            bCompleted: false,
            sReason: "Level 2 is not met in vaibify's observation",
        })""")
    assert dictRefused["sType"] == "warning"
    assert "Level 2 is not met" in dictRefused["sMessage"]
    dictArchived = pageDashboard.evaluate(
        """() => VaibifyApp.fdictDescribeCompletionOutcome({
            bCompleted: true, sDoi: "10.5072/zenodo.102",
            sPostCompletionVerifyWarning: "the record lags the index",
        })""")
    assert dictArchived["sType"] == "warning"
    assert "10.5072/zenodo.102" in dictArchived["sMessage"]
    assert "the record lags the index" in dictArchived["sMessage"]


_S_RENDER_PENDING = """(listPending) => {
    return VaibifyWorkflowRequirements.fsRenderProjectBlock({
        dictWorkflowEnvelopeDetail: {
            listLevel3EnvelopePaths: [],
            dictArtifacts: {},
            dictImageCurrency: {bPinnedImageIsLive: null},
            listBinaries: [],
            listPendingPromotions: listPending,
        },
        dictRemoteChecks: {},
        setToggledFileGroups: new Set(),
        bProjectBlockCollapsed: false,
        setExpandedRequirementGroups: new Set(['promotions']),
        setExpandedRequirementRows: new Set([
            'promotion-promotion-cccc']),
    });
}"""


def test_an_interrupted_completion_is_named_as_one(pageDashboard, serverHub):
    fnOpenTheSeededHostWorkflow(pageDashboard, serverHub)
    sHtml = pageDashboard.evaluate(_S_RENDER_PENDING, [{
        "sPromotionId": "promotion-cccc", "sLane": "completion",
        "sTargetService": "sandbox", "iDepositId": 200,
        "sPhase": "drafted", "sStartedIso": "2026-11-03T17:44:00+00:00",
        "listFiles": [],
    }])
    assert "Interrupted completion version" in sHtml
    assert "A completion version of the latest archive started" in sHtml
    assert "environment archive" not in sHtml
    assert 'data-wf-action="reconcile-promotion"' in sHtml
