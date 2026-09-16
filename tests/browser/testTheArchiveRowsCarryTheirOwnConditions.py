"""Each archive row asks its own question, and the third state is orange.

The Zenodo archive row went green on envelope agreement alone. So a
project whose archive held the envelope but no covering attestation
failed Level 3 with every publication row green -- and the "Do this
next" arrow, which only fires on an edge live at both ends, fell silent
at exactly the moment it was needed.

The row now asks three things of one immutable version, because all
three have ONE remedy: publish a Zenodo version carrying the envelope
and the attestation together. The third condition is TRI-state, and the
third state is why a boolean would not do: ``false`` is "a verify
compared them and the attestation does not cover the archived
manifest", while ``null`` is "no verify has looked". Red is a claim
about the archive, and nobody earned it.

None of this is visible to the Python suite: the gate is one boolean
either way, and only the rendered row can be wrong about which.
"""

import pytest

from tests.browser.conftest import fnOpenTheSeededHostWorkflow


pytestmark = pytest.mark.browser


_S_RENDER_ZENODO_ROW = """(dictArgs) => {
    return VaibifyWorkflowRequirements.fsRenderProjectBlock({
        dictWorkflowEnvelopeDetail: {
            listLevel3EnvelopePaths: [],
            dictArtifacts: {},
            dictImageCurrency: {bPinnedImageIsLive: null},
            listBinaries: [],
            bEnvelopeInGithubMirror: true,
            bEnvelopeInZenodoArchive: dictArgs.bEnvelopeMatched,
            dictArchivePermanence: {
                sImageArchivePermanence: 'permanent',
                sProjectArchivePermanence: dictArgs.sPermanence,
            },
            dictArchivedAttestation: dictArgs.dictArchivedAttestation,
            dictRemoteSyncs: {zenodo: {sZenodoDoiVerified: '10.5281/1'}},
        },
        dictRemoteChecks: {},
        setToggledFileGroups: new Set(),
        bProjectBlockCollapsed: false,
        setExpandedRequirementGroups: new Set(['publishedCopies']),
        setExpandedRequirementRows: new Set(['zenodo', 'github']),
    });
}"""


def _fsSelectRow(sHtml, sKey):
    """Return the markup of ONE requirement row, bounded at the next.

    Bounded deliberately: an unbounded tail carries every row below
    this one, so an assertion about what this row does NOT say reads
    the Rebuild attestation row's cells instead and fails for the
    wrong reason.
    """
    import re
    sTail = sHtml.split('data-req="' + sKey + '"')[1]
    return re.split(r'data-(?:req|group)="', sTail)[0]


def _fsRenderZenodo(pageDashboard, **dictArgs):
    dictArgs.setdefault("bEnvelopeMatched", True)
    dictArgs.setdefault("sPermanence", "permanent")
    dictArgs.setdefault(
        "dictArchivedAttestation", {"bCoversArchivedManifest": True},
    )
    return _fsSelectRow(
        pageDashboard.evaluate(_S_RENDER_ZENODO_ROW, dictArgs),
        "zenodo",
    )


@pytest.mark.falsification
def test_the_zenodo_row_asks_all_three_of_its_questions(
    pageDashboard, serverHub,
):
    """ONE open, every state: the seeded project is leased.

    Kills: judging the row from ``bEnvelopeInZenodoArchive`` alone,
    which paints green over an archive holding no covering
    attestation -- the state in which Level 3 fails and no publication
    row says so.
    """
    fnOpenTheSeededHostWorkflow(pageDashboard, serverHub)

    # --- all three satisfied: green, and nothing extra is said.
    sGreen = _fsRenderZenodo(pageDashboard)
    assert "Level 3: met" in sGreen, sGreen[:1500]
    assert "does not cover the manifest" not in sGreen

    # --- compared, and the attestation does NOT cover: red, and the
    # note names the sequence rather than the criterion.
    sMissing = _fsRenderZenodo(
        pageDashboard,
        dictArchivedAttestation={"bCoversArchivedManifest": False},
    )
    assert "Level 3: not met" in sMissing, sMissing[:1500]
    assert "does not cover the manifest in the same" in sMissing
    assert "Verify Level 3" in sMissing

    # --- NOT COMPARED: orange, never red. `null` means nobody looked,
    # and reddening it claims a divergence no verify found.
    for objNever in (None, {}):
        sNever = _fsRenderZenodo(
            pageDashboard, dictArchivedAttestation=objNever,
        )
        assert "Level 3: partially met" in sNever, sNever[:1500]
        assert "Level 3: not met" not in sNever, (
            "an archive nobody has compared was painted as diverged"
        )
        assert "No verify has checked" in sNever

    # --- a SANDBOX project deposit costs this row its level, and says
    # which remedy. Permanence is `!= sandbox`, never `== permanent`.
    sSandbox = _fsRenderZenodo(pageDashboard, sPermanence="sandbox")
    assert "Level 3: partially met" in sSandbox, sSandbox[:1500]
    assert 'data-wf-action="promote-project-deposit"' in sSandbox

    # --- and `unknown` keeps it, because the gate fails open in the
    # researcher's favour and the row may not outrank the gate.
    sUnknown = _fsRenderZenodo(pageDashboard, sPermanence="unknown")
    assert "Level 3: met" in sUnknown, sUnknown[:1500]

    # --- GitHub is asked NEITHER question, because GitHub is not an
    # archive. The two rows share one renderer, and applying the
    # archive conjuncts to both would send every project's mirror row
    # amber over a file list that is entirely green: nothing ever
    # compares an archived attestation against GitHub.
    sGithub = _fsSelectRow(
        pageDashboard.evaluate(_S_RENDER_ZENODO_ROW, {
            "bEnvelopeMatched": True,
            "sPermanence": "sandbox",
            "dictArchivedAttestation": None,
        }),
        "github",
    )
    assert "Level 3: met" in sGithub, sGithub[:1500]
    assert "No verify has checked" not in sGithub

    assert pageDashboard.listPageErrors == []
