"""A remote's files group by what the researcher must do about them.

A flat list does not survive scale: measured on 2026-08-30, a thousand
files across two expanded remote rows built 1.8 MB of HTML and 12,161
DOM nodes on every render, and the researcher had to scroll past every
matching file to reach the four that differed.

The remote stays the OUTER grouping — the question is "is my data
published to Zenodo", and a file's disposition is per-remote anyway, so
there is no global bucket to sort into. Inside the row, the groups
nobody acts on come last and start closed.

Three properties, each of which is a way this could mislead:

* The actionable groups are OPEN and the matching group is CLOSED by
  default. Reversed, the change buys nothing.
* A closed group still states its COUNT. A collapsed group that hid its
  size would be worse than the flat list, because a researcher cannot
  tell "nothing there" from "not looking".
* A capped group SAYS what it is not showing. Silent truncation reads
  as a complete list, which is the same lie as an omitted file.
"""

import pytest

from tests.browser.conftest import fnOpenTheSeededHostWorkflow


pytestmark = pytest.mark.browser


@pytest.fixture(autouse=True)
def fixtureDropClaimsBetweenTests(serverHub):
    """Give every claim back after each test.

    The hub is module-scoped and the page is not, so a test that claims
    the project and stops leaves it owned by a lease nobody holds, and
    the next test's claim is refused by a session that no longer
    exists. The symptom is a locked tile intercepting the click, which
    reads like a UI bug in the feature under test.
    """
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


# A shape a real project reaches: mostly matching, a handful drifted,
# a few never compared, and more matching files than the per-group cap
# so the truncation notice has to appear.
_S_DRIVE_GROUPED_ROW = """() => {
    const listFiles = [];
    const dictBadges = {};
    const fnAdd = (sPath, sState) => {
        listFiles.push(sPath);
        dictBadges[sPath] = {
            sGithub: sState, sZenodo: sState,
            sOverleaf: 'none', sArxiv: 'none',
        };
    };
    for (let i = 0; i < 120; i++) fnAdd('data/match' + i + '.csv', 'synced');
    for (let i = 0; i < 3; i++) fnAdd('data/drift' + i + '.csv', 'drifted');
    for (let i = 0; i < 2; i++) fnAdd('data/new' + i + '.csv', 'none');
    fnAdd('data/never.csv', 'not-compared');

    VaibifyGitBadges.flistFilesForRemote = () => listFiles;
    VaibifyGitBadges.fdictGetBadgesForFile =
        (sPath) => dictBadges[sPath] || null;

    const sHtml = VaibifyWorkflowRequirements.fsRenderProjectBlock({
        dictWorkflowEnvelopeDetail: {dictRemoteSyncs: {github: {
            sLastVerified: '2026-08-30T00:00:00Z',
            iTotalFiles: listFiles.length,
            iMatching: 120, iDivergedCount: 3,
        }}},
        dictRemoteChecks: {},
        setExpandedRequirementGroups: new Set(['publishedCopies']),
        setExpandedRequirementRows: new Set(['github']),
        setToggledFileGroups: new Set(),
    });
    const elHost = document.createElement('div');
    elHost.innerHTML = sHtml;
    document.body.appendChild(elHost);
    const elRow = Array.from(elHost.querySelectorAll('.requirement-row'))
        .find(el => (el.textContent || '').indexOf('GitHub mirror') !== -1);
    const dictGroups = {};
    elRow.querySelectorAll('.file-group').forEach((elGroup) => {
        const elHeader = elGroup.querySelector('.file-group-header');
        dictGroups[elHeader.dataset.fileGroup] = {
            sHeaderText: elHeader.textContent.trim(),
            iRows: elGroup.querySelectorAll('.detail-item').length,
            sTruncated: (elGroup.querySelector('.file-group-truncated')
                || {}).textContent || '',
        };
    });
    const iTotalRows = elRow.querySelectorAll('.detail-item').length;
    elHost.remove();
    return {dictGroups: dictGroups, iTotalRows: iTotalRows,
            iFileCount: listFiles.length};
}"""


@pytest.mark.falsification
def test_the_matching_majority_is_collapsed_and_the_problems_are_not(
    pageDashboard, serverHub,
):
    """The point of the grouping: problems visible, bulk out of the way.

    Kills: flipping bOpenByDefault on the `synced` disposition, which
    reinstates the flat list the grouping exists to replace.
    """
    fnOpenTheSeededHostWorkflow(
        pageDashboard, serverHub, bAwaitProjectBlock=True,
    )
    dictSeen = pageDashboard.evaluate(_S_DRIVE_GROUPED_ROW)
    dictGroups = dictSeen["dictGroups"]

    assert dictGroups["sGithub:drifted"]["iRows"] == 3, (
        "the files that DIFFER are not listed, so the researcher has "
        f"to open something to find their problems: {dictGroups}"
    )
    assert dictGroups["sGithub:none"]["iRows"] == 2, (
        "the files missing from the remote are not listed"
    )
    assert dictGroups["sGithub:synced"]["iRows"] == 0, (
        "the 120 matching files are expanded, so the grouping bought "
        "nothing — this is the flat list with headings"
    )
    assert dictSeen["iTotalRows"] < 20, (
        "the row still renders most of the project: "
        f"{dictSeen['iTotalRows']} of {dictSeen['iFileCount']} files"
    )


@pytest.mark.falsification
def test_a_collapsed_group_still_says_how_many_it_holds(
    pageDashboard, serverHub,
):
    """A hidden count is worse than a long list.

    A researcher cannot tell "nothing matches" from "I am not looking
    at the matches" unless the closed group states its size.

    Kills: dropping the file-group-count span from
    _fsRenderOneFileGroup.
    """
    fnOpenTheSeededHostWorkflow(
        pageDashboard, serverHub, bAwaitProjectBlock=True,
    )
    dictGroups = pageDashboard.evaluate(
        _S_DRIVE_GROUPED_ROW)["dictGroups"]

    assert "120" in dictGroups["sGithub:synced"]["sHeaderText"], (
        "the collapsed Matching group hides how many files it holds: "
        f"{dictGroups['sGithub:synced']['sHeaderText']!r}"
    )
    assert "3" in dictGroups["sGithub:drifted"]["sHeaderText"], (
        "the Differs group does not state its count"
    )

    assert pageDashboard.listPageErrors == []


@pytest.mark.falsification
def test_a_capped_group_says_what_it_is_not_showing(
    pageDashboard, serverHub,
):
    """Silent truncation reads as a complete list.

    Kills: dropping the file-group-truncated notice, which leaves a
    capped group looking like the whole set.
    """
    fnOpenTheSeededHostWorkflow(
        pageDashboard, serverHub, bAwaitProjectBlock=True,
    )
    # Opening Matching exposes 120 files against a 50-row cap.
    sTruncated = pageDashboard.evaluate(
        _S_DRIVE_GROUPED_ROW.replace(
            "setToggledFileGroups: new Set(),",
            "setToggledFileGroups: new Set(['sGithub:synced']),",
        )
    )["dictGroups"]["sGithub:synced"]["sTruncated"]

    assert "50" in sTruncated and "120" in sTruncated, (
        "an opened group renders a capped list and never says so, so "
        f"70 files are invisible and look absent: {sTruncated!r}"
    )


def test_toggling_a_group_does_not_collapse_the_row_it_lives_in(
    pageDashboard, serverHub,
):
    """The click must reach the group, and stop there.

    Driven as a real click through the real delegated dispatcher,
    because that is where a selector-ordering mistake would live — the
    renderer cannot show it.
    """
    fnOpenTheSeededHostWorkflow(
        pageDashboard, serverHub, bAwaitProjectBlock=True,
    )
    pageDashboard.evaluate(_S_DRIVE_GROUPED_ROW)
    # Re-render through the dashboard itself so the live DOM (not a
    # detached fragment) carries the groups the click has to hit.
    pageDashboard.evaluate(
        "() => { VaibifyApp.fnToggleRequirementGroup('publishedCopies');"
        " VaibifyApp.fnToggleRequirementRow('github'); }"
    )
    pageDashboard.wait_for_selector(
        '.requirement-row-detail', timeout=5000,
    )
    elGroup = pageDashboard.query_selector('.file-group-header')
    if elGroup is None:
        pytest.skip(
            "the seeded host project tracks no files for any remote, "
            "so no disposition group renders to click"
        )
    elGroup.click()
    assert pageDashboard.query_selector(
        '.requirement-row-detail') is not None, (
        "clicking a disposition group collapsed the requirement row "
        "it lives in, so the group the researcher opened vanished"
    )

    assert pageDashboard.listPageErrors == []
