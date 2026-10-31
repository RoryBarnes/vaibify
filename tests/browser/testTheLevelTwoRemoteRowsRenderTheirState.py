"""The GitHub and Zenodo rows render the authorities' states, never a boolean.

Level 2 asks two things of each remote -- does the copy match, and can a
reader retrieve it -- and two backend authorities answer them as
``public | not-public | unknown`` and ``matched | differs | unknown``.
"Unknown" covers evidence that cannot answer: a stale record, one about a
remote the project no longer publishes to, one written before vaibify
asked what a reader is served, and a Zenodo comparison WITHHELD because a
declared record restricts its files. Each of those used to paint the row
"not met" -- a failure nobody observed -- or, for the withheld archive,
"N diverged" over files nobody could read.

Both surfaces are driven with the real payload shapes: the PROOF tab
through its readiness route, and Published copies through the renderer
with a canned poll detail.
"""

import json

import pytest

from tests.browser.conftest import fnOpenTheSeededHostWorkflow

pytestmark = pytest.mark.browser

S_IDENTITY_REASON = (
    "Not compared: the last GitHub check compared someone/other, branch "
    "main, which is not the remote this project publishes to now — verify "
    "again to check the current one.")
S_PREDATES_REASON = (
    "The last Zenodo check was made before vaibify asked what a reader is "
    "served — verify again.")
S_RESTRICTED_REASON = (
    "Zenodo record 2 restricts its files. Edit the record on Zenodo, set "
    "its files to public, and publish the edit; its DOI does not change.")
S_WITHHELD_REASON = "Not compared: " + S_RESTRICTED_REASON
S_PRIVATE_REASON = (
    "GitHub serves someone/something to your login but not to a reader: it "
    "is private, or internal to an organization.")
LIST_REMOTE_ROWS = (
    "bGithubFullySynced", "bGithubRepositoryPublic",
    "bZenodoFullySynced", "bZenodoArchivePublic",
)


@pytest.fixture(autouse=True)
def fixtureDropClaimsBetweenTests(serverHub):
    yield
    from vaibify.config.containerLock import fnReleaseContainerLock
    dictContainerOwners = serverHub.app.state.dictContainerOwners
    for _sName, recordOwner in list(dictContainerOwners.items()):
        fileHandle = getattr(recordOwner, "fileHandleLock", None)
        if fileHandle is not None:
            try:
                fnReleaseContainerLock(fileHandle)
            except OSError:
                pass
    dictContainerOwners.clear()
    serverHub.app.state.dictSessionOwner.clear()


def _fdictState(sState, sReason):
    return {"sState": sState, "sReason": sReason}


def _fdictLevelTwoGaps(dictCopyStates, dictAccessStates):
    """The real gaps shape, with every boolean the states imply."""
    from vaibify.reproducibility.levelGates import fdictLevel2Gaps
    dictGaps = fdictLevel2Gaps({}, "/nonexistent-repo-for-shape")
    dictGaps.update({
        "dictPublishedCopyStates": dictCopyStates,
        "dictPublicAccessStates": dictAccessStates,
        "bGithubFullySynced": dictCopyStates["github"]["sState"] == "matched",
        "bZenodoFullySynced": dictCopyStates["zenodo"]["sState"] == "matched",
        "bGithubRepositoryPublic":
            dictAccessStates["github"]["sState"] == "public",
        "bZenodoArchivePublic":
            dictAccessStates["zenodo"]["sState"] == "public",
    })
    return dictGaps


def _fnServeReadiness(page, dictGaps):
    page.route("**/api/workflow/**/level2/readiness", lambda route: (
        route.fulfill(status=200, content_type="application/json",
                      body=json.dumps({"iProofLevel": 1,
                                       "dictLevel2Gaps": dictGaps}))))
    page.route("**/api/workflow/**/level3/readiness", lambda route: (
        route.fulfill(status=200, content_type="application/json",
                      body=json.dumps({"iProofLevel": 1,
                                       "dictL3ReadinessGaps": {},
                                       "sRecordKind": "attestation"}))))


def _fnOpenTheLevelTwoSection(page, serverHub, dictGaps):
    fnOpenTheSeededHostWorkflow(page, serverHub)
    _fnServeReadiness(page, dictGaps)
    page.click('.left-tab[data-panel="proof"]')
    page.wait_for_selector(".proof-level-section-header", timeout=15000)
    sSection = '.proof-level-section[data-level="2"]'
    page.wait_for_selector(sSection, timeout=10000)
    if page.locator(sSection + ".collapsed").count():
        page.click('.proof-level-section-header[data-level="2"]')
    page.wait_for_selector(
        '.proof-req-entry[data-req-key="bZenodoArchivePublic"]', timeout=15000)


def _fdictRowLight(page, sKey):
    """Return the row's light state and every word the row shows."""
    return page.evaluate("""(sKey) => {
        const elRow = document.querySelector(
            '.proof-req-entry[data-req-key="' + sKey + '"]');
        const elLight = elRow.querySelector('.step-level-cell');
        return {
            sLight: Array.from(elLight.classList).find(
                (s) => s.startsWith('level-cell-')),
            sTitle: elLight.getAttribute('title'),
            sText: elRow.innerText,
        };
    }""", sKey)


@pytest.mark.falsification
def test_the_four_level_two_remote_rows_render_unknown_never_not_met(
    pageDashboard, serverHub,
):
    """Another remote's comparison, a withheld one, a missing verdict: "?".

    Every boolean here is False, which is what the gate answers for
    each -- so a row that fell back to its boolean would paint "not met",
    claiming a failure the evidence never showed.

    Kills: _fsRequirementStateOrEmpty no longer reading the Level 2
    states, so the rows fall back to their booleans.
    """
    dictGaps = _fdictLevelTwoGaps(
        {"github": _fdictState("unknown", S_IDENTITY_REASON),
         "zenodo": _fdictState("unknown", S_WITHHELD_REASON)},
        {"github": _fdictState("unknown", S_IDENTITY_REASON),
         "zenodo": _fdictState("unknown", S_PREDATES_REASON)},
    )
    _fnOpenTheLevelTwoSection(pageDashboard, serverHub, dictGaps)
    dictReasons = {
        "bGithubFullySynced": S_IDENTITY_REASON,
        "bGithubRepositoryPublic": S_IDENTITY_REASON,
        "bZenodoFullySynced": S_WITHHELD_REASON,
        "bZenodoArchivePublic": S_PREDATES_REASON,
    }
    for sKey in LIST_REMOTE_ROWS:
        dictRow = _fdictRowLight(pageDashboard, sKey)
        assert dictRow["sLight"] == "level-cell-unknown", (sKey, dictRow)
        assert dictReasons[sKey] in dictRow["sText"], (sKey, dictRow)
        assert dictReasons[sKey] in dictRow["sTitle"], (sKey, dictRow)
    assert pageDashboard.listPageErrors == []


def test_not_public_and_public_read_as_the_authorities_say(
    pageDashboard, serverHub,
):
    """A private repository is not met, with its remedy; a public archive is met."""
    dictGaps = _fdictLevelTwoGaps(
        {"github": _fdictState("matched", "Every published file matches GitHub."),
         "zenodo": _fdictState("matched", "Every published file matches Zenodo.")},
        {"github": _fdictState("not-public", S_PRIVATE_REASON),
         "zenodo": _fdictState("public", "Zenodo serves record 1's files.")},
    )
    _fnOpenTheLevelTwoSection(pageDashboard, serverHub, dictGaps)
    dictPrivate = _fdictRowLight(pageDashboard, "bGithubRepositoryPublic")
    assert dictPrivate["sLight"] == "level-cell-none", dictPrivate
    assert S_PRIVATE_REASON in dictPrivate["sText"]
    for sKey in ("bGithubFullySynced", "bZenodoFullySynced",
                 "bZenodoArchivePublic"):
        assert _fdictRowLight(pageDashboard, sKey)["sLight"] == (
            "level-cell-attained"), sKey
    dictMirror = _fdictRowLight(pageDashboard, "bGithubFullySynced")
    assert "public GitHub" not in dictMirror["sText"], (
        "the mirror row still promises a public repository nothing checked")
    assert pageDashboard.listPageErrors == []


# ── Published copies ──────────────────────────────────────────────────


_S_RENDER_PUBLISHED_COPIES = """(dictArgs) => {
    const fnOriginalBadges = VaibifyGitBadges.fdictGetBadgesForFile;
    const fnOriginalFiles = VaibifyGitBadges.flistFilesForRemote;
    VaibifyGitBadges.fdictGetBadgesForFile = () => (
        {sGithub: 'unknown', sZenodo: 'unknown'});
    VaibifyGitBadges.flistFilesForRemote = () => [];
    try {
        return VaibifyWorkflowRequirements.fsRenderProjectBlock({
            dictWorkflowEnvelopeDetail: {
                listLevel3EnvelopePaths: [], listBinaries: [],
                dictArtifacts: {},
                dictRemoteSyncs: dictArgs.dictRemoteSyncs,
                dictPublishedCopyStates: dictArgs.dictCopyStates,
                dictPublicAccessStates: dictArgs.dictAccessStates,
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


def test_a_withheld_archive_reads_not_compared_and_a_private_mirror_half_met(
    pageDashboard, serverHub,
):
    fnOpenTheSeededHostWorkflow(pageDashboard, serverHub)
    sHtml = pageDashboard.evaluate(_S_RENDER_PUBLISHED_COPIES, {
        "dictRemoteSyncs": {
            "github": {"iTotalFiles": 2, "iMatching": 2, "iDivergedCount": 0},
            "zenodo": {"iTotalFiles": 0, "iMatching": 0, "iDivergedCount": 0,
                       "bComparisonWithheld": True},
        },
        "dictCopyStates": {
            "github": _fdictState("matched", "Every published file matches GitHub."),
            "zenodo": _fdictState("unknown", S_WITHHELD_REASON),
        },
        "dictAccessStates": {
            "github": _fdictState("not-public", S_PRIVATE_REASON),
            "zenodo": _fdictState("not-public", S_RESTRICTED_REASON),
        },
    })
    sZenodoRow = _fsSelectRow(sHtml, "zenodo", "overleaf")
    assert S_WITHHELD_REASON in sZenodoRow
    assert "0 of 0" not in sZenodoRow and "diverged" not in sZenodoRow, (
        "a comparison nobody made is described as one")
    assert "Level 2: not checked yet" in sZenodoRow
    assert "Not public — " + S_RESTRICTED_REASON in sZenodoRow
    sGithubRow = _fsSelectRow(sHtml, "github", "zenodo")
    assert "Level 2: partially met" in sGithubRow, (
        "a matching copy no reader can retrieve reads as fully met")
    assert "Not public — " + S_PRIVATE_REASON in sGithubRow
    assert pageDashboard.listPageErrors == []


def test_a_stale_or_moved_record_is_unknown_in_published_copies(
    pageDashboard, serverHub,
):
    fnOpenTheSeededHostWorkflow(pageDashboard, serverHub)
    sHtml = pageDashboard.evaluate(_S_RENDER_PUBLISHED_COPIES, {
        "dictRemoteSyncs": {
            "github": {"iTotalFiles": 2, "iMatching": 1, "iDivergedCount": 1},
            "zenodo": {"iTotalFiles": 2, "iMatching": 2, "iDivergedCount": 0},
        },
        "dictCopyStates": {
            "github": _fdictState("unknown", S_IDENTITY_REASON),
            "zenodo": _fdictState("matched", "Every published file matches Zenodo."),
        },
        "dictAccessStates": {
            "github": _fdictState("unknown", S_IDENTITY_REASON),
            "zenodo": _fdictState("public", "Zenodo serves record 1's files."),
        },
    })
    sGithubRow = _fsSelectRow(sHtml, "github", "zenodo")
    assert "Level 2: not checked yet" in sGithubRow
    assert S_IDENTITY_REASON in sGithubRow
    assert "1 of 2" not in sGithubRow, (
        "another remote's counts are shown as this one's")
    assert sGithubRow.count(S_IDENTITY_REASON) == 1, (
        "the same reason is said twice")
    sZenodoRow = _fsSelectRow(sHtml, "zenodo", "overleaf")
    assert "Level 2: met" in sZenodoRow
    assert "Public — Zenodo serves record 1" in sZenodoRow
    assert pageDashboard.listPageErrors == []
