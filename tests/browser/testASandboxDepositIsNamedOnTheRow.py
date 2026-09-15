"""A sandbox deposit is NAMED beside the row, never painted over it.

A sandbox deposit really does hold bytes that match the envelope, so
moving the row's colour would be a claim nobody earned: the warning
rides beside the computed state. The attestation row is the one
exception, and for the opposite reason — the rebuild happened, and it
is the CREDIT that is withheld.

Three things these tests hold that a Python-only suite cannot see at
all:

* the glyph and the Make Permanent button appear for ``sandbox`` and
  for NEITHER other state;
* the Published Envelope row renders the DOI the verify compared
  against, selectable, where it previously reported none at all;
* a production primary with an older SANDBOX verify cache shows no
  button — permanence comes from the current record, and the cache
  lags because the post-archive verify is best-effort.
"""

import pytest

from tests.browser.conftest import fnOpenTheSeededHostWorkflow


pytestmark = pytest.mark.browser


_S_RENDER_ARCHIVE_ROW = """(sPermanence) => {
    return VaibifyWorkflowRequirements.fsRenderProjectBlock({
        dictWorkflowEnvelopeDetail: {
            listLevel3EnvelopePaths: [],
            dictArtifacts: {},
            dictImageCurrency: {bPinnedImageIsLive: null},
            listBinaries: [],
            dictImageArchive: {
                sState: 'attained',
                bAnswered: true,
                sAnswer: 'archived',
                dictRecord: {sVersionDoi: '10.5072/zenodo.4242'},
                listIssues: [],
                sUncheckedReason: '',
                dictDeposit: null,
                sPermanence: sPermanence,
            },
        },
        dictRemoteChecks: {},
        setToggledFileGroups: new Set(),
        bProjectBlockCollapsed: false,
        setExpandedRequirementGroups: new Set(['artifacts']),
        setExpandedRequirementRows: new Set(['environmentArchive']),
    });
}"""


_S_RENDER_ENVELOPE_ROW = """(dictArgs) => {
    return VaibifyWorkflowRequirements.fsRenderProjectBlock({
        dictWorkflowEnvelopeDetail: {
            listLevel3EnvelopePaths: [],
            dictArtifacts: {},
            dictImageCurrency: {bPinnedImageIsLive: null},
            listBinaries: [],
            bEnvelopeInGithubMirror: false,
            bEnvelopeInZenodoArchive: true,
            dictArchivePermanence: {
                sProjectArchivePermanence: dictArgs.sPermanence,
            },
            dictRemoteSyncs: {
                zenodo: {sZenodoDoiVerified: dictArgs.sDoiVerified},
            },
        },
        dictRemoteChecks: {},
        setToggledFileGroups: new Set(),
        bProjectBlockCollapsed: false,
        setExpandedRequirementGroups: new Set(['publishedEnvelope']),
        setExpandedRequirementRows: new Set(['envelopeArchive']),
    });
}"""


_S_RENDER_ATTESTATION_ROW = """(dictPermanence) => {
    return VaibifyWorkflowRequirements.fsRenderProjectBlock({
        dictWorkflowEnvelopeDetail: {
            listLevel3EnvelopePaths: [],
            dictArtifacts: {},
            dictImageCurrency: {bPinnedImageIsLive: null},
            listBinaries: [],
            bRebuildAttestationCurrent: true,
            bRebuildAttestationRunning: false,
            dictRebuildAttestation: {sStatus: 'passed'},
            dictArchivePermanence: dictPermanence,
        },
        dictRemoteChecks: {},
        setToggledFileGroups: new Set(),
        bProjectBlockCollapsed: false,
        setExpandedRequirementGroups: new Set(['attestation']),
        setExpandedRequirementRows: new Set(['rebuildAttestation']),
    });
}"""


def _fsSelectRow(sHtml, sKey, sNextKey=""):
    """Return the markup of one requirement row."""
    sRow = sHtml.split('data-req="' + sKey + '"')[1]
    return sRow.split('data-req="' + sNextKey + '"')[0] if sNextKey else sRow


@pytest.mark.falsification
def test_a_sandbox_deposit_is_named_beside_every_row_it_touches(
    pageDashboard, serverHub,
):
    """ONE open, every assertion: the seeded project is leased, so a
    second open in this file is refused as another session.

    Kills: rendering the warning on any state other than "sandbox" —
    for instance treating "unknown" as a soft sandbox, which would
    put a permanence warning on every project whose deposit predates
    the recorded-instance field.
    """
    fnOpenTheSeededHostWorkflow(pageDashboard, serverHub)

    # --- the environment archive row: glyph, explanation, button
    sSandbox = pageDashboard.evaluate(_S_RENDER_ARCHIVE_ROW, "sandbox")
    sRow = _fsSelectRow(sSandbox, "environmentArchive")
    assert "requirement-row-warning" in sRow
    assert "Not a permanent archive" in sRow
    assert "permanence-warning" in sRow
    assert 'data-wf-action="promote-environment-archive"' in sRow
    assert "SANDBOX" in sRow
    # The row's own status sentence must not still claim "a permanent
    # archive": the bytes match either way, and what changes is
    # whether anyone has promised to keep them.
    assert "is deposited in a permanent archive" not in sRow
    # The row keeps its computed state: a sandbox deposit really does
    # hold matching bytes, and repainting the cell would be a claim
    # nobody earned.
    assert "level-cell-attained" in sRow

    # --- permanent and unknown render exactly as today
    for sState in ("permanent", "unknown", ""):
        sOther = pageDashboard.evaluate(_S_RENDER_ARCHIVE_ROW, sState)
        sOtherRow = _fsSelectRow(sOther, "environmentArchive")
        assert "requirement-row-warning" not in sOtherRow, sState
        assert "promote-environment-archive" not in sOtherRow, sState
        assert "is deposited in a permanent archive" in sOtherRow, sState

    # --- the Published Envelope row: a DOI it reported nowhere at all
    sEnvelope = pageDashboard.evaluate(_S_RENDER_ENVELOPE_ROW, {
        "sPermanence": "sandbox",
        "sDoiVerified": "10.5072/zenodo.551",
    })
    sEnvelopeRow = _fsSelectRow(sEnvelope, "envelopeArchive")
    assert "Archived DOI" in sEnvelopeRow
    assert "10.5072/zenodo.551" in sEnvelopeRow
    # Read-only and selectable, like the environment archive's own
    # deposited-DOI field: the one string a researcher needs to cite
    # must not be the hardest thing on the row to select.
    assert "readonly" in sEnvelopeRow
    assert "environment-archive-doi-value" in sEnvelopeRow
    assert 'data-wf-action="promote-project-deposit"' in sEnvelopeRow

    # --- a PRODUCTION primary with an older SANDBOX verify cache
    # offers nothing. Permanence comes from the current record; the
    # post-archive verify is best-effort, so the cache lags, and a
    # button driven off it would offer a promotion the route refuses.
    sPromoted = pageDashboard.evaluate(_S_RENDER_ENVELOPE_ROW, {
        "sPermanence": "permanent",
        "sDoiVerified": "10.5072/zenodo.551",
    })
    sPromotedRow = _fsSelectRow(sPromoted, "envelopeArchive")
    assert "10.5072/zenodo.551" in sPromotedRow, (
        "the DOI shown is still the one the verify compared against"
    )
    assert "promote-project-deposit" not in sPromotedRow
    assert "requirement-row-warning" not in sPromotedRow

    # --- the attestation row withholds the CREDIT and names which
    # archive. The rerun happened; the archive it sits in promises
    # nothing.
    sBlocked = pageDashboard.evaluate(_S_RENDER_ATTESTATION_ROW, {
        "bNoArchiveKnownSandbox": False,
        "listPermanenceIssues": [
            "The environment archive is a Zenodo SANDBOX deposit.",
        ],
    })
    sAttestation = _fsSelectRow(sBlocked, "rebuildAttestation")
    assert "does not count while an archive is a sandbox deposit" in (
        sAttestation
    )
    assert "The environment archive is a Zenodo SANDBOX deposit." in (
        sAttestation
    )
    assert "level-cell-attained" not in sAttestation

    # A payload predating the criterion must not redden the row.
    sLegacy = pageDashboard.evaluate(_S_RENDER_ATTESTATION_ROW, {})
    sLegacyRow = _fsSelectRow(sLegacy, "rebuildAttestation")
    assert "level-cell-attained" in sLegacyRow
    assert "does not count" not in sLegacyRow
