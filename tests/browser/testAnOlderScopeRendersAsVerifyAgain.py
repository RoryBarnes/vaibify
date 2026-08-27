"""A cache from an older scope paints orange, and says why.

The backend answers ``bScopeStale`` and the gate refuses such a cache,
both with unit tests. What nothing exercised was the step in between:
whether the researcher's SCREEN turns that flag into a mark they can
read. That gap is the "a green Python suite says nothing about the
frontend" trap in miniature -- the flag was tested, the renderer that
consumes it was not, and a registry entry written against it would
have recorded a guarantee nobody checks.

The assertions read the level-cell vocabulary (`partial`), not the
mark name: the renderer maps "orange" onto it, so asserting the mark
would pass against markup that never reached the level strip at all.

Two properties, and the second is the one that matters.

ORANGE (partial), not red. The researcher has published nothing wrong; they have
no evidence yet about files the scope has since added. Red accuses
them of a divergence that was never observed, and this row is the one
place a false accusation is most expensive -- it is the Level 2
publication claim.

And the row must SAY why. An amber mark beside "all files matching" is
the shape a researcher reasonably reads as a bug, because the count is
complete for the question the old verify asked. The text is what
distinguishes "your data is unpublished" from "verify again".

The state is driven through the real renderer with a synthetic sync
summary, because the seeded host project has never verified anything
and a never-verified cache renders `unknown` by a different branch.

Kills (confirmed, not assumed): removing the `bScopeStale` clause from
_fsSyncRowState fails the colour assertion; removing it from
_fsDescribeSyncState fails the text assertion.
"""

import pytest

from tests.browser.conftest import fnOpenTheSeededHostWorkflow


pytestmark = pytest.mark.browser

# A verify that was COMPLETE for the scope it ran under: every file it
# compared matched. Only the scope is out of date, so any orange here
# comes from that and nothing else.
_S_DRIVE_SCOPE_STALE_ROW = """() => {
    const dictSync = {
        sLastVerified: "2026-08-26T00:00:00Z",
        iTotalFiles: 3, iMatching: 3, iDivergedCount: 0,
        bStale: false, bScopeStale: true,
    };
    const dictDetail = {dictRemoteSyncs: {github: dictSync}};
    const sHtml = VaibifyWorkflowRequirements.fsRenderProjectBlock({
        dictWorkflowEnvelopeDetail: dictDetail,
        setExpandedRequirementGroups: new Set(["publishedCopies"]),
        setExpandedRequirementRows: new Set(["github"]),
    });
    const elHost = document.createElement("div");
    elHost.innerHTML = sHtml;
    const listHeaders = Array.from(elHost.querySelectorAll(
        '.requirement-group-header'));
    const elGroup = listHeaders.find(
        el => (el.dataset.group || '') === 'publishedCopies')
            .closest('.requirement-group');
    const elRow = Array.from(elGroup.querySelectorAll('.requirement-row'))
        .find(el => (el.textContent || '').indexOf('GitHub mirror') !== -1);
    const elStatus = elRow.querySelector('.requirement-row-status');
    return {
        sRowMarkup: elRow.innerHTML,
        sStatusText: elStatus ? elStatus.textContent : '',
    };
}"""




@pytest.mark.falsification
def test_an_older_scope_paints_orange_and_says_verify_again(
    pageDashboard, serverHub,
):
    """An older-scope cache paints partial and says to verify again.

    Kills: disable the bScopeStale branch in _fsSyncRowState, which
    paints a pass over files no verify has compared.
    """
    fnOpenTheSeededHostWorkflow(pageDashboard, serverHub, bAwaitProjectBlock=True)
    dictRow = pageDashboard.evaluate(_S_DRIVE_SCOPE_STALE_ROW)

    # The renderer maps the "orange" mark onto the shared level-cell
    # vocabulary, so the DOM carries `partial` -- asserting the mark
    # name would pass against a row that never reached the strip.
    assert "level-cell-partial" in dictRow["sRowMarkup"], (
        "a cache verified under an older scope renders as something "
        "other than partial, so the researcher is either told nothing "
        "is wrong or accused of a divergence never observed: "
        f"{dictRow['sRowMarkup'][:400]}"
    )
    assert "level-cell-attained" not in dictRow["sRowMarkup"], (
        "the row reports a pass over files no verify has compared"
    )
    assert "verify again" in dictRow["sStatusText"].lower(), (
        "the row shows an amber mark beside a complete-looking count "
        "and never says the scope grew, which reads as a bug rather "
        f"than an action: {dictRow['sStatusText']!r}"
    )

    assert pageDashboard.listPageErrors == []
