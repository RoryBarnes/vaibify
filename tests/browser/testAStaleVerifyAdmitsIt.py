"""A green cell on a stale verify wears the drift where the check is.

Twice in one live session (2026-09-16) a researcher read a green
Level 2 cell as current fact while the badges beside it showed live
divergence: once on GitHub minutes after a directory align rewrote
project.json, once on the Zenodo row for the same file -- whose cell
kept the check because Verify Now is per-service and only GitHub had
been re-asked.

The verdict is the verify's to change; only a comparison moves a
cell. The SILENCE is not: when live badges positively contradict the
recorded agreement, the row says so beside the check, with the
per-service remedy -- push for GitHub, "next published version" for
immutable Zenodo. Only "drifted" counts: unknown contradicts
nothing, and "none" means different things per service.
"""

import pytest

from tests.browser.conftest import fnOpenTheSeededHostWorkflow


pytestmark = pytest.mark.browser


_S_RENDER_WITH_BADGES = """(dictArgs) => {
    const fnOriginalBadges = VaibifyGitBadges.fdictGetBadgesForFile;
    const fnOriginalFiles = VaibifyGitBadges.flistFilesForRemote;
    VaibifyGitBadges.fdictGetBadgesForFile = (sPath) => ({
        sGithub: dictArgs.dictStates[sPath] || 'unknown',
        sZenodo: dictArgs.dictStates[sPath] || 'unknown',
    });
    VaibifyGitBadges.flistFilesForRemote = () =>
        Object.keys(dictArgs.dictStates);
    try {
        return VaibifyWorkflowRequirements.fsRenderProjectBlock({
            dictWorkflowEnvelopeDetail: {
                listLevel3EnvelopePaths: [],
                listBinaries: [],
                dictArtifacts: {},
                dictRemoteSyncs: {
                    github: {iTotalFiles: 2, iMatching: 2,
                             iDivergedCount: 0},
                    zenodo: {iTotalFiles: 2, iMatching: 2,
                             iDivergedCount: 0},
                },
            },
            dictRemoteChecks: {},
            setToggledFileGroups: new Set(),
            bProjectBlockCollapsed: false,
            setExpandedRequirementGroups: new Set(['publishedCopies']),
            setExpandedRequirementRows: new Set(['github', 'zenodo']),
        });
    } finally {
        VaibifyGitBadges.fdictGetBadgesForFile = fnOriginalBadges;
        VaibifyGitBadges.flistFilesForRemote = fnOriginalFiles;
    }
}"""


def _fsSelectRow(sHtml, sKey, sNextKey):
    sTail = sHtml.split('data-req="' + sKey + '"')[1]
    return sTail.split('data-req="' + sNextKey + '"')[0]


@pytest.mark.falsification
def test_a_drifted_badge_under_a_green_cell_is_said_out_loud(
    pageDashboard, serverHub,
):
    """ONE open, every state: the seeded project is leased.

    Kills: dropping the badge consultation from the merged row --
    the shipped silence, in which a cached full match rendered as
    current fact over a file the badges already knew had changed.
    """
    fnOpenTheSeededHostWorkflow(pageDashboard, serverHub)

    # --- one file drifted since the (green) verify: both rows say
    # so, each with its own remedy.
    sHtml = pageDashboard.evaluate(_S_RENDER_WITH_BADGES, {
        "dictStates": {
            ".vaibify/projects/project.json": "drifted",
            "Data/result.csv": "synced",
        },
    })
    sGithubRow = _fsSelectRow(sHtml, "github", "zenodo")
    sZenodoRow = _fsSelectRow(sHtml, "zenodo", "overleaf")
    assert "changed locally since the verify" in sGithubRow, (
        "the GitHub row keeps a silent check over a drifted badge"
    )
    assert "Push, then verify." in sGithubRow
    assert "changed locally since the verify" in sZenodoRow, (
        "the Zenodo row keeps a silent check over a drifted badge"
    )
    assert "next published version" in sZenodoRow, (
        "the Zenodo remedy suggests a push, which cannot fix an "
        "immutable deposit"
    )
    # The CHECK stays: a completed verify really did match, and only
    # a comparison may move a cell.
    assert "Level 2: met" in sGithubRow, (
        "the marker repainted the verdict instead of annotating it"
    )

    # --- nothing drifted: no warning anywhere.
    sClean = pageDashboard.evaluate(_S_RENDER_WITH_BADGES, {
        "dictStates": {
            ".vaibify/projects/project.json": "synced",
            "Data/result.csv": "synced",
        },
    })
    assert "changed locally since the verify" not in sClean, (
        "the marker fires without a contradiction; a permanent "
        "warning is furniture nobody reads"
    )

    # --- unknown badges contradict nothing: unchecked is never a
    # warning either.
    sUnknown = pageDashboard.evaluate(_S_RENDER_WITH_BADGES, {
        "dictStates": {".vaibify/projects/project.json": "unknown"},
    })
    assert "changed locally since the verify" not in sUnknown
