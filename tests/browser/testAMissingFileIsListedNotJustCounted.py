"""A file absent from a remote is the one the researcher must see.

`flistFilesForRemote` dropped every badge whose state was "none",
treating "not on the remote" as though it were the absence of an
answer. It is the opposite -- it is the answer that blocks
publication -- and the consequences compounded:

* the Published-copies row counted the file (24 of 25 matching) while
  its own expandable list had no row for it, so the count and the list
  contradicted each other;
* the disposition group literally named "Not on the remote" could
  never be populated, by construction;
* the badge on that row is the control that pushes the file, so the
  only fix was unreachable from the place the problem was reported.

A researcher spent a session asking which file was missing, with the
answer sitting in the poll payload the whole time (2026-09-08).

`tests/browser/testRemoteFilesGroupByDisposition.py` covers the same
group and could not catch this: it REPLACES
`VaibifyGitBadges.flistFilesForRemote` with a stub returning every
seeded path, so it exercises the grouping against a function that
never filters. This file drives the real one, seeded through the real
refresh path.
"""

import pytest

from tests.browser.conftest import fnOpenTheSeededHostWorkflow


pytestmark = pytest.mark.browser


@pytest.fixture(autouse=True)
def fixtureDropClaimsBetweenTests(serverHub):
    """Give every claim back after each test.

    The hub is module-scoped and the page is not, so a test that
    claims the project and stops leaves it owned by a lease nobody
    holds. The next test's claim is refused and the symptom is a
    locked tile intercepting the click -- which reads as a UI bug in
    the feature under test rather than as leaked state.
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


_S_SEED_AND_ASK = """async () => {
    const dictBadges = {
        'data/published.csv': {sGithub: 'synced', sZenodo: 'synced'},
        'data/changed.csv': {sGithub: 'drifted', sZenodo: 'drifted'},
        '.vaibify/absent.json': {sGithub: 'none', sZenodo: 'none'},
        'data/unseen.csv': {},
    };
    const fdictGetReal = VaibifyApi.fdictGet;
    VaibifyApi.fdictGet = () => Promise.resolve({dictBadges: dictBadges});
    try {
        await VaibifyGitBadges.fnRefresh('cid-probe');
    } finally {
        VaibifyApi.fdictGet = fdictGetReal;
    }
    return {
        listGithub: VaibifyGitBadges.flistFilesForRemote('sGithub'),
        sAbsentState: VaibifyGitBadges.fdictGetBadgesForFile(
            '.vaibify/absent.json', '').sGithub,
    };
}"""


@pytest.mark.falsification
def test_a_file_absent_from_the_remote_is_still_listed(
    pageDashboard, serverHub,
):
    """"Not on the remote" is an answer, and the one that matters.

    Kills: restoring the `!== "none"` filter in
    `flistFilesForRemote`.
    """
    fnOpenTheSeededHostWorkflow(
        pageDashboard, serverHub, bAwaitProjectBlock=True,
    )
    dictSeen = pageDashboard.evaluate(_S_SEED_AND_ASK)

    assert ".vaibify/absent.json" in dictSeen["listGithub"], (
        "a file missing from GitHub is absent from the list the "
        "researcher reads: " + str(dictSeen["listGithub"])
    )
    assert dictSeen["sAbsentState"] == "none", (
        "the fixture no longer describes a missing file, so this "
        "test is not about the bug it names"
    )


@pytest.mark.falsification
def test_a_file_the_map_never_mentioned_is_not_listed(
    pageDashboard, serverHub,
):
    """The other direction, so the fix is not "list everything".

    A path carrying no state for this remote is genuinely nothing
    known. Listing it would put a row under a disposition heading
    that asserts something nobody checked -- the placeholder reads
    "unknown" precisely so an unloaded map states no negative.

    Kills: replacing the filter with a bare `Object.keys(...)`.
    """
    fnOpenTheSeededHostWorkflow(
        pageDashboard, serverHub, bAwaitProjectBlock=True,
    )
    dictSeen = pageDashboard.evaluate(_S_SEED_AND_ASK)

    assert "data/unseen.csv" not in dictSeen["listGithub"], (
        "a path with no state for this remote was listed anyway: "
        + str(dictSeen["listGithub"])
    )
    assert "data/published.csv" in dictSeen["listGithub"]
    assert "data/changed.csv" in dictSeen["listGithub"]
