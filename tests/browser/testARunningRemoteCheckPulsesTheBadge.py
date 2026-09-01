"""A configured remote pulses while its check runs, and never lies.

Reopening a project after a day painted the Published-copies badges
orange purely because the cached verify had aged out. The dashboard now
re-checks every configured remote on entry, and each badge pulses until
its own answer arrives — which is a claim about the SCREEN, so a Python
test of the poll key proves nothing about it.

Three properties are driven here through the real renderer and the real
stylesheet:

* A running check PULSES. Asserting the class alone would pass against
  a stylesheet with no rule at all, so the markup is attached to the
  live document and ``animationName`` is read back off the level cell.
* A running check does NOT move the colour. Nothing has been compared
  yet, so a check in flight must not paint a pass or a failure.
* A check that could not complete says so and stays out of red. "Could
  not reach the remote" is not a divergence, and red on this row is the
  most expensive false accusation the dashboard can make.

The states are driven with a synthetic check map because the seeded
host project configures no remotes at all — which is itself the fourth
property, asserted last: an unconfigured remote never pulses.
"""

import pytest

from tests.browser.conftest import fnOpenTheSeededHostWorkflow


pytestmark = pytest.mark.browser


@pytest.fixture(autouse=True)
def fixtureDropClaimsBetweenTests(serverHub):
    """Give every claim back after each test.

    The hub is module-scoped and the page is not, so a test that
    claims the project and stops leaves it owned by a lease nobody
    holds, and the next test's claim is refused by a session that no
    longer exists. The symptom is not a 409 but a locked tile
    intercepting the click, which reads like a UI bug in the feature
    under test.
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


# Renders the Project block with GitHub CHECKING and Zenodo answered
# but UNCHECKABLE, attaches it to the live document so the stylesheet
# applies, and reads back what a researcher would actually see.
_S_DRIVE_CHECK_STATES = """() => {
    const dictSync = {
        sLastVerified: "2026-08-29T00:00:00Z",
        iTotalFiles: 3, iMatching: 3, iDivergedCount: 0,
        bStale: true, bScopeStale: false,
    };
    const sHtml = VaibifyWorkflowRequirements.fsRenderProjectBlock({
        dictWorkflowEnvelopeDetail: {
            dictRemoteSyncs: {github: dictSync, zenodo: dictSync},
        },
        dictRemoteChecks: {
            github: {sState: "checking", sReason: ""},
            zenodo: {
                sState: "uncheckable",
                sReason: "could not resolve zenodo.org",
            },
        },
        setExpandedRequirementGroups: new Set(["publishedCopies"]),
        setExpandedRequirementRows: new Set(["github", "zenodo"]),
    });
    const elHost = document.createElement("div");
    elHost.innerHTML = sHtml;
    document.body.appendChild(elHost);
    const fnFindRow = (sLabel) => Array.from(
        elHost.querySelectorAll('.requirement-row')).find(
            el => (el.textContent || '').indexOf(sLabel) !== -1);
    const elGithub = fnFindRow('GitHub mirror');
    const elZenodo = fnFindRow('Zenodo deposit');
    const fsAnimationOf = (elRow) => {
        const elCell = elRow.querySelector(
            '.requirement-row-header .step-level-cell');
        return elCell
            ? window.getComputedStyle(elCell).animationName : 'no-cell';
    };
    const elGroupHeader = Array.from(elHost.querySelectorAll(
        '.requirement-group-header')).find(
            el => (el.dataset.group || '') === 'publishedCopies');
    const elGroupCell = elGroupHeader.querySelector('.step-level-cell');
    const fsCheckTextOf = (elRow) => {
        const elCheck = elRow.querySelector('.requirement-row-check');
        return elCheck ? elCheck.textContent : '';
    };
    const dictAnswer = {
        sGithubAnimation: fsAnimationOf(elGithub),
        sZenodoAnimation: fsAnimationOf(elZenodo),
        sGroupAnimation:
            window.getComputedStyle(elGroupCell).animationName,
        sGithubMarkup: elGithub.innerHTML,
        sZenodoMarkup: elZenodo.innerHTML,
        sGithubCheckText: fsCheckTextOf(elGithub),
        sZenodoCheckText: fsCheckTextOf(elZenodo),
    };
    elHost.remove();
    return dictAnswer;
}"""


# The same block with no check map at all -- the state every project
# is in before the open-time refresh answers, and the state an
# unconfigured remote stays in forever.
_S_DRIVE_NO_CHECKS = """() => {
    const sHtml = VaibifyWorkflowRequirements.fsRenderProjectBlock({
        dictWorkflowEnvelopeDetail: {dictRemoteSyncs: {github: {
            sLastVerified: "2026-08-29T00:00:00Z",
            iTotalFiles: 3, iMatching: 3, iDivergedCount: 0,
        }}},
        setExpandedRequirementGroups: new Set(["publishedCopies"]),
        setExpandedRequirementRows: new Set(),
    });
    const elHost = document.createElement("div");
    elHost.innerHTML = sHtml;
    document.body.appendChild(elHost);
    const elRow = Array.from(elHost.querySelectorAll(
        '.requirement-row')).find(
            el => (el.textContent || '').indexOf('GitHub mirror') !== -1);
    const elCell = elRow.querySelector(
        '.requirement-row-header .step-level-cell');
    const sAnimation = window.getComputedStyle(elCell).animationName;
    elHost.remove();
    return sAnimation;
}"""


@pytest.mark.falsification
def test_a_running_check_pulses_without_moving_the_colour(
    pageDashboard, serverHub,
):
    """The badge pulses while asking, and claims nothing while it does.

    Kills: removing the `requirement-row-checking` class from
    _fsRenderRequirementRow, and removing the pulse rule from
    styleMain.css, each fail the animation assertion.
    """
    fnOpenTheSeededHostWorkflow(
        pageDashboard, serverHub, bAwaitProjectBlock=True,
    )
    dictSeen = pageDashboard.evaluate(_S_DRIVE_CHECK_STATES)

    assert dictSeen["sGithubAnimation"] == "pulse", (
        "a remote whose check is in flight renders a still badge, so "
        "the researcher cannot tell 'vaibify is asking' from 'this is "
        f"the answer': animation is {dictSeen['sGithubAnimation']!r}"
    )
    assert dictSeen["sGroupAnimation"] == "pulse", (
        "the collapsed Published-copies banner shows a settled light "
        "over a question still open"
    )
    # The cached verify was complete but stale, so the row is partial.
    # A check in flight has compared nothing and must not move that.
    assert "level-cell-partial" in dictSeen["sGithubMarkup"], (
        "a check in flight moved the light off what the last "
        "completed verify earned: "
        f"{dictSeen['sGithubMarkup'][:400]}"
    )
    assert "Checking" in dictSeen["sGithubCheckText"], (
        "the expanded row never says a check is running: "
        f"{dictSeen['sGithubCheckText']!r}"
    )


@pytest.mark.falsification
def test_a_check_that_could_not_run_says_so_and_stays_out_of_red(
    pageDashboard, serverHub,
):
    """An unreachable remote is a missing answer, never a divergence.

    Kills: making _fsDescribeCheck return "" for the uncheckable state
    fails the text assertion; painting the row red on an uncheckable
    check fails the colour assertion.
    """
    fnOpenTheSeededHostWorkflow(
        pageDashboard, serverHub, bAwaitProjectBlock=True,
    )
    dictSeen = pageDashboard.evaluate(_S_DRIVE_CHECK_STATES)

    assert dictSeen["sZenodoAnimation"] != "pulse", (
        "a settled check leaves its badge pulsing, so the pulse stops "
        "meaning anything"
    )
    assert "Could not check" in dictSeen["sZenodoCheckText"], (
        "the row hides that its check failed, so the researcher reads "
        "the last verify's counts as current: "
        f"{dictSeen['sZenodoCheckText']!r}"
    )
    assert "could not resolve zenodo.org" in (
        dictSeen["sZenodoCheckText"]
    ), "the row says a check failed and never says why"
    assert "level-cell-none" not in dictSeen["sZenodoMarkup"], (
        "an unreachable remote paints the divergence colour, which "
        "accuses the researcher of publishing something wrong over "
        f"evidence nobody gathered: {dictSeen['sZenodoMarkup'][:400]}"
    )

    assert pageDashboard.listPageErrors == []


def test_a_remote_with_no_check_never_pulses(pageDashboard, serverHub):
    """Absence from the check map is how an unconfigured remote rests.

    The refresh marks only the services the workflow configured, so a
    remote with no DOI and no binding is never in the map — and a
    renderer that pulsed on absence would leave those badges flashing
    for the life of the session.
    """
    fnOpenTheSeededHostWorkflow(
        pageDashboard, serverHub, bAwaitProjectBlock=True,
    )
    sAnimation = pageDashboard.evaluate(_S_DRIVE_NO_CHECKS)
    assert sAnimation in ("none", ""), (
        "a remote nobody is checking pulses anyway: "
        f"animation is {sAnimation!r}"
    )

    assert pageDashboard.listPageErrors == []


def test_opening_a_project_asks_every_remote_again(
    pageDashboard, serverHub,
):
    """The trigger, not the rendering: entering a project starts the ask.

    Everything above drives the renderer directly, so all of it would
    still pass if nothing ever called the refresh route — which is the
    shape of a feature that is green and does nothing. This one watches
    the wire. The seeded host project configures no remotes, so the
    route answers an empty pair; what is asserted is that it was ASKED,
    because that call is what turns a day-old cached badge into a
    current one.
    """
    listSeen = []
    pageDashboard.on("request", lambda request: (
        listSeen.append(
            ("badges" if "/badges" in request.url else "refresh")
        )
        if (
            (request.method == "GET" and "/api/git/" in request.url
             and "/badges" in request.url)
            or (request.method == "POST"
                and "/remotes/refresh" in request.url)
        ) else None
    ))
    fnOpenTheSeededHostWorkflow(
        pageDashboard, serverHub, bAwaitProjectBlock=True,
    )
    # The call is fire-and-forget by design, so there is no DOM change
    # to wait on; the Project block is already rendered by the time
    # the opener returns, and this is the settling margin for one POST.
    pageDashboard.wait_for_timeout(2000)
    assert "refresh" in listSeen, (
        "opening a project never asked the remotes anything, so the "
        "badges keep reporting whatever the cache aged into"
    )
    # ORDER, and it is a regression fix rather than tidiness. The
    # refresh registers durable work; the badge read PAUSES while
    # durable work is live and answers with NO badge map at all. Fired
    # alongside, the refresh won the race and a fresh hub -- which has
    # no previous map to fall back on -- painted "No files tracked for
    # this remote yet" over a repository full of them, and kept
    # painting it until something bumped the sync epoch. Observed on a
    # real project, 2026-08-30.
    assert listSeen.index("badges") < listSeen.index("refresh"), (
        "the remote refresh was issued before the badge seed, so the "
        "first badge read of the session is paused by durable work "
        f"and the map stays empty: {listSeen}"
    )

    assert pageDashboard.listPageErrors == []
