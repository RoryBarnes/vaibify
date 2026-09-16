"""A mixed envelope reads PARTIAL on the merged row, which can push.

Two changed envelope files over three matching ones painted the
envelope rows fully red — the mark for a requirement with nothing
left standing — above a file list that was mostly green
(researcher-ruled, 2026-09-02): red is for total failure, "2 of 5
differ" is the definition of partial. And the row offered a Verify
button with no way to publish, so the fix it named ("push the
current envelope") had no control anywhere near it. Since the
2026-09-16 merge the same claims live on the Level 3 CELL of the
merged Published-copies rows.

These tests execute the SHIPPED render function inside a real
browser with the badge oracle stubbed per path — the render path is
the unit, the badge states are the fixture. The stub is restored
afterwards because the page outlives one test.
"""

import json

import pytest

from tests.browser.conftest import fnOpenTheSeededHostWorkflow


pytestmark = pytest.mark.browser


_S_RENDER_WITH_BADGES = """(dictArgs) => {
    const fnOriginal = VaibifyGitBadges.fdictGetBadgesForFile;
    VaibifyGitBadges.fdictGetBadgesForFile = (sPath) => ({
        sGithub: dictArgs.dictStates[sPath] || 'unknown',
        sZenodo: dictArgs.dictStates[sPath] || 'unknown',
        sOverleaf: 'none', sArxiv: 'none',
    });
    try {
        return VaibifyWorkflowRequirements.fsRenderProjectBlock({
            dictWorkflowEnvelopeDetail: {
                listLevel3EnvelopePaths:
                    Object.keys(dictArgs.dictStates),
                bEnvelopeInGithubMirror: false,
                bEnvelopeInZenodoArchive: false,
                dictArtifacts: {},
                dictImageCurrency: {bPinnedImageIsLive: null},
                listBinaries: [],
                dictRemoteSyncs: {github: {
                    iTotalFiles: 3, iMatching: 3, iDivergedCount: 0,
                }},
            },
            dictRemoteChecks: {},
            setToggledFileGroups: new Set(),
            bProjectBlockCollapsed: false,
            setExpandedRequirementGroups:
                new Set(['publishedCopies']),
            setExpandedRequirementRows:
                new Set(['github', 'zenodo']),
        });
    } finally {
        VaibifyGitBadges.fdictGetBadgesForFile = fnOriginal;
    }
}"""


_S_RENDER_ATTESTATION_ROW = """(bHasRecord) => {
    return VaibifyWorkflowRequirements.fsRenderProjectBlock({
        dictWorkflowEnvelopeDetail: {
            listLevel3EnvelopePaths: [],
            dictArtifacts: {},
            dictImageCurrency: {bPinnedImageIsLive: null},
            listBinaries: [],
            bRebuildAttestationCurrent: bHasRecord,
            bRebuildAttestationRunning: false,
            dictRebuildAttestation:
                bHasRecord ? {sStatus: 'passed'} : null,
        },
        dictRemoteChecks: {},
        setToggledFileGroups: new Set(),
        bProjectBlockCollapsed: false,
        setExpandedRequirementGroups: new Set(['attestation']),
        setExpandedRequirementRows: new Set(['rebuildAttestation']),
    });
}"""


def _fsRender(pageDashboard, dictStates):
    return pageDashboard.evaluate(
        _S_RENDER_WITH_BADGES, {"dictStates": dictStates},
    )


@pytest.mark.falsification
def test_mixed_divergence_reads_partial_and_offers_a_push(
    pageDashboard, serverHub,
):
    """One open, three assertions — the seeded project is leased, so
    a second open in this file would be refused as another session.

    Kills: In _fdictEnvelopeRemoteRowHealth, return "red" whenever
    listNeedsPush is non-empty, ignoring iSynced — the pre-ruling
    reading in which one changed file paints total failure over a
    mostly-matching list.
    """
    fnOpenTheSeededHostWorkflow(pageDashboard, serverHub)

    # --- mixed: some diverged, some matching -> PARTIAL, with a push
    sHtml = _fsRender(pageDashboard, {
        "reproduce.sh": "synced",
        "MANIFEST.sha256": "synced",
        "Dockerfile": "synced",
        "requirements.lock": "drifted",
        ".vaibify/environment.json": "drifted",
    })
    sMirrorRow = sHtml.split('data-req="github"')[1]
    sMirrorRow = sMirrorRow.split('data-req="zenodo"')[0]
    assert "level-cell-partial" in sMirrorRow, (
        "two diverged files over three matching ones must read "
        "PARTIAL, not total failure"
    )
    assert "level-cell-none" not in sMirrorRow
    # The two cells DISAGREE, and both are right: the Level 2 verify
    # above passed in full, and a diverged envelope is Level 3's
    # business alone. "banner not green" would be too weak here -- an
    # implementation that wrongly lowered Level 2 would satisfy it.
    assert "Level 2: met" in sMirrorRow, (
        "a diverged envelope lowered the Level 2 cell; the higher "
        "rung reached down"
    )
    assert "Level 3: met" not in sMirrorRow
    assert "wf-push-envelope" in sMirrorRow, (
        "the row proves specific files diverged and offers no way "
        "to publish them"
    )
    assert "2 of these files differ" in sMirrorRow
    # The button carries exactly the files a push would fix.
    sEncoded = sMirrorRow.split('data-paths="')[1].split('"')[0]
    from urllib.parse import unquote
    assert sorted(json.loads(unquote(sEncoded))) == [
        ".vaibify/environment.json", "requirements.lock",
    ]

    # --- the detail is indented like every other row's: the
    # requirement-row-detail wrapper carries the indent, and these
    # two rows were the only renderers returning bare content — their
    # body sat fully left-aligned under the banner
    # (researcher-reported twice, 2026-09-02).
    assert '<div class="requirement-row-detail">' in sMirrorRow, (
        "the envelope row's detail lost its indent wrapper again"
    )

    # --- an attestation on file earns a way to open it
    sWithAttestation = pageDashboard.evaluate(
        _S_RENDER_ATTESTATION_ROW, True,
    )
    assert "wf-view-attestation" in sWithAttestation, (
        "the row says an attestation is on file and offers no way "
        "to read it"
    )
    assert pageDashboard.evaluate(
        "() => typeof VaibifyApp.fnShowL3AttestationModal"
    ) == "function"
    sWithoutAttestation = pageDashboard.evaluate(
        _S_RENDER_ATTESTATION_ROW, False,
    )
    assert "wf-view-attestation" not in sWithoutAttestation, (
        "a View button over no record is a dead end"
    )

    # --- nothing matching is the state red exists for
    sAllDrifted = _fsRender(pageDashboard, {
        "requirements.lock": "drifted",
        ".vaibify/environment.json": "drifted",
    })
    sRedRow = sAllDrifted.split('data-req="github"')[1]
    sRedRow = sRedRow.split('data-req="zenodo"')[0]
    assert "level-cell-none" in sRedRow

    # --- the Zenodo row never offers a push: a Zenodo "sync" is a
    # new immutable deposit version, its own deliberate act
    sArchiveRow = sAllDrifted.split('data-req="zenodo"')[1]
    assert "wf-push-envelope" not in sArchiveRow
