"""The top bar's reproduction badge renders the server's answers.

The badge sits in the TOP BAR, immediately right of the right-most
PROOF check beside the project name (researcher's placement ruling,
2026-10-12; it had lived on the left column's Project header, right of
its level strip, and before that as a "(reproduced)" suffix on that
heading). It says one of two things, each the server's:

- "Reproducing" while the poll reports a Level 3 verification live;
- "Reproduced" when the server's reproduction label has ``bShow``
  true -- for the AUTHOR, their own current Level 3 attestation; for a
  READER, their own reproduction -- with hover text naming the date,
  the platform and any emulation. Every other label state (absent,
  diverged, evidence changed, unreadable, no payload at all) draws
  nothing, because a badge is a claim and silence is not evidence.

A later attempt that reached NO verdict keeps an earlier "Reproduced"
(the server's rule), and the dashboard's own warning glyph appears to
its right carrying that attempt's date and the server's reason -- the
sentence lives on the glyph, not on the badge.
"""

import json

import pytest

from tests.browser.conftest import fnOpenTheSeededHostWorkflow

pytestmark = pytest.mark.browser


@pytest.fixture(autouse=True)
def fixtureDropClaimsBetweenTests(serverHub):
    """Give every claim back after each test (see the sibling files)."""
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


_S_RENDER = """([dictLabel, bRunning]) => {
    var dictContext = {
        dictWorkflowEnvelopeDetail: {
            listLevel3EnvelopePaths: [], dictArtifacts: {},
            listBinaries: [], bRebuildAttestationRunning: bRunning,
        },
        dictReproductionLabel: dictLabel,
        dictRemoteChecks: {},
        setToggledFileGroups: new Set(),
        bProjectBlockCollapsed: false,
        setExpandedRequirementGroups: new Set(),
        setExpandedRequirementRows: new Set(),
        fsLevelCellState: () => "not-started",
        fsLevelCellTooltip: () => "",
    };
    var el = document.createElement("div");
    el.innerHTML =
        VaibifyWorkflowRequirements.fsRenderReproductionBadge(dictContext);
    var elBlock = document.createElement("div");
    elBlock.innerHTML =
        VaibifyWorkflowRequirements.fsRenderProjectBlock(dictContext);
    document.body.appendChild(el);
    document.body.appendChild(elBlock);
    var elTitle = elBlock.querySelector(".project-block-title");
    var elBadge = el.querySelector(".project-reproduction-badge");
    var elGlyph = el.querySelector(".project-reproduction-warning");
    var dictAnswer = {
        sHeading: elTitle.textContent.replace(/[\\u25b8\\u25be]/g, "").trim(),
        sBadgeText: elBadge ? elBadge.textContent : "",
        sBadgeTitle: elBadge ? elBadge.title : "",
        bProjectBlockCarriesBadge: Boolean(elBlock.querySelector(
            ".project-reproduction-badge, .project-reproduction-warning")),
        sGlyphText: elGlyph ? elGlyph.textContent : "",
        sGlyphTitle: elGlyph ? elGlyph.title : "",
        bGlyphFollowsBadge: Boolean(elGlyph && elBadge &&
            elBadge.nextElementSibling === elGlyph),
        sGlyphColor: elGlyph ? getComputedStyle(elGlyph).color : "",
    };
    el.remove();
    elBlock.remove();
    return dictAnswer;
}"""

# The color the step rows' orange warning glyph actually paints, read
# off a live element of the existing class -- so "same visual
# language" is measured, not assumed.
_S_READ_STEP_WARNING_COLOR = """() => {
    var el = document.createElement("span");
    el.className = "step-regression-cell regression-warning-orange";
    el.textContent = "\\u26a0";
    document.body.appendChild(el);
    var sColor = getComputedStyle(el).color;
    el.remove();
    return sColor;
}"""


def _fdictRender(pageDashboard, dictLabel, bRunning=False):
    return pageDashboard.evaluate(_S_RENDER, [dictLabel, bRunning])


def _fdictShown(**dictFields):
    dictLabel = {
        "bShow": True, "sState": "reproduced", "sReason": "",
        "bEmulated": False, "sPlatform": "linux/amd64",
        "sRecordedIso": "2026-10-04T12:00:00+00:00",
        "sLatestAttemptIso": "", "sLatestAttemptVerdict": "",
    }
    dictLabel.update(dictFields)
    return dictLabel


@pytest.mark.falsification
def test_the_badge_reads_reproduced_only_for_a_shown_label(
    pageDashboard, serverHub,
):
    """Kills: drawing the badge whatever the server's verdict says."""
    fnOpenTheSeededHostWorkflow(pageDashboard, serverHub)
    dictShown = _fdictRender(pageDashboard, _fdictShown())
    assert dictShown["sBadgeText"] == "Reproduced", dictShown
    assert dictShown["bProjectBlockCarriesBadge"] is False, (
        "the Project block's header still carries the badge"
    )
    assert dictShown["sHeading"] == "Project", (
        f"the heading still carries the label: {dictShown['sHeading']!r}"
    )
    assert "2026-10-04" in dictShown["sBadgeTitle"]
    assert "linux/amd64" in dictShown["sBadgeTitle"]
    for dictHidden in (
        None,
        _fdictShown(bShow=False, sState="absent"),
        _fdictShown(bShow=False, sState="diverged"),
        _fdictShown(bShow=False, sState="evidence-changed"),
        _fdictShown(bShow=False, sState="unreadable"),
    ):
        dictRead = _fdictRender(pageDashboard, dictHidden)
        assert dictRead["sBadgeText"] == "", dictHidden
        assert dictRead["sGlyphText"] == "", dictHidden
        assert dictRead["sHeading"] == "Project", dictHidden
    assert pageDashboard.listPageErrors == []


def test_the_badge_hover_names_emulation(pageDashboard, serverHub):
    fnOpenTheSeededHostWorkflow(pageDashboard, serverHub)
    sEmulated = _fdictRender(
        pageDashboard, _fdictShown(bEmulated=True))["sBadgeTitle"]
    assert "under emulation" in sEmulated


@pytest.mark.falsification
def test_a_later_inconclusive_attempt_puts_a_warning_beside_the_badge(
    pageDashboard, serverHub,
):
    """The kept badge gains the warning glyph, and only for no verdict.

    Kills: never rendering the glyph -- an inconclusive later attempt
    leaves the earlier "Reproduced" standing with nothing to say a
    later run could not decide.
    """
    fnOpenTheSeededHostWorkflow(pageDashboard, serverHub)
    dictLater = _fdictRender(pageDashboard, _fdictShown(
        sLatestAttemptIso="2026-10-05T08:00:00+00:00",
        sLatestAttemptVerdict="no-verdict",
        sReason="the daemon was unreachable"))
    assert dictLater["sBadgeText"] == "Reproduced", dictLater
    assert dictLater["sGlyphText"] == "⚠", dictLater
    assert dictLater["bGlyphFollowsBadge"] is True, (
        "the warning glyph is not immediately right of the badge"
    )
    assert "2026-10-05" in dictLater["sGlyphTitle"], dictLater
    assert "the daemon was unreachable" in dictLater["sGlyphTitle"]
    assert "no verdict" not in dictLater["sBadgeTitle"], (
        "the inconclusive attempt is still told on the badge's own "
        f"hover: {dictLater['sBadgeTitle']!r}"
    )
    assert "Reproduced on 2026-10-04" in dictLater["sBadgeTitle"]
    sStepGlyphColor = pageDashboard.evaluate(_S_READ_STEP_WARNING_COLOR)
    assert dictLater["sGlyphColor"] == sStepGlyphColor, (
        "the glyph does not paint like the dashboard's warning glyph"
    )

    # A later attempt that reached a verdict: the server reports no
    # inconclusive attempt, and the glyph is gone.
    dictSettled = _fdictRender(pageDashboard, _fdictShown(
        sRecordedIso="2026-10-06T08:00:00+00:00"))
    assert dictSettled["sBadgeText"] == "Reproduced"
    assert dictSettled["sGlyphText"] == "", dictSettled
    assert pageDashboard.listPageErrors == []


def test_a_running_verification_reads_reproducing_over_any_label(
    pageDashboard, serverHub,
):
    """While the server reports a run, the badge says only that."""
    fnOpenTheSeededHostWorkflow(pageDashboard, serverHub)
    dictRunning = _fdictRender(pageDashboard, _fdictShown(), bRunning=True)
    assert dictRunning["sBadgeText"] == "Reproducing", dictRunning
    assert dictRunning["sGlyphText"] == ""
    dictUnlabelled = _fdictRender(pageDashboard, None, bRunning=True)
    assert dictUnlabelled["sBadgeText"] == "Reproducing"


# --- Where the badge sits, read off the live dashboard ---
#
# The hub's own file-status answer is fetched and only the label is
# rewritten, so every other part of the page is what the real poll
# drew. The geometry is read twice in ONE evaluate: as the page stands
# (no level reached, so the name is the right-most thing) and with all
# three checks lit, restoring the class before the page can render.

_S_READ_TOP_BAR = """() => {
    const elSlot = document.getElementById("projectReproductionBadge");
    const elBadge = document.querySelector(
        "#toolbar #workflowSwitcher .project-reproduction-badge");
    const elGlyph = document.querySelector(
        "#toolbar #workflowSwitcher .project-reproduction-warning");
    const listChecks = Array.from(document.querySelectorAll(
        "#workflowSwitcher .vaibify-checkmark"));
    const elLastCheck = listChecks[listChecks.length - 1];
    const fsAnchorRight = () => {
        const listLit = listChecks.filter(
            (el) => getComputedStyle(el).display !== "none");
        const elAnchor = listLit.length ? listLit[listLit.length - 1]
            : document.getElementById("activeWorkflowName");
        return elAnchor.getBoundingClientRect().right;
    };
    const fdictMeasure = () => {
        const rectBadge = elBadge.getBoundingClientRect();
        const rectName = document.getElementById("activeWorkflowName")
            .getBoundingClientRect();
        return {
            fBadgeLeft: rectBadge.left, fAnchorRight: fsAnchorRight(),
            bSameRow: Math.abs((rectBadge.top + rectBadge.bottom) / 2 -
                (rectName.top + rectName.bottom) / 2) < rectName.height,
        };
    };
    if (!elBadge) return null;
    const dictAtPageLevel = fdictMeasure();
    const listLevelClasses = ["proof-level-1", "proof-level-2",
        "proof-level-3"].filter((s) => document.body.classList.contains(s));
    document.body.classList.remove(...listLevelClasses);
    document.body.classList.add("proof-level-3");
    const dictAtLevelThree = fdictMeasure();
    document.body.classList.remove("proof-level-3");
    document.body.classList.add(...listLevelClasses);
    return {
        sText: elBadge.textContent, sTitle: elBadge.title,
        bSlotFollowsLastCheck: Boolean(elSlot && elLastCheck &&
            elSlot.previousElementSibling === elLastCheck),
        bBadgeInSlot: elSlot.contains(elBadge),
        sGlyphText: elGlyph ? elGlyph.textContent : "",
        sGlyphTitle: elGlyph ? elGlyph.title : "",
        bGlyphFollowsBadge: Boolean(elGlyph &&
            elBadge.nextElementSibling === elGlyph),
        dictAtPageLevel: dictAtPageLevel,
        dictAtLevelThree: dictAtLevelThree,
        bProjectBlockCarriesBadge: Boolean(document.querySelector(
            "#projectBlock .project-reproduction-badge, " +
            "#projectBlock .project-reproduction-warning")),
        bProjectBlockRendered: Boolean(document.querySelector(
            "#projectBlock .project-block-header")),
    };
}"""


def _fnServeTheLabel(pageDashboard, dictLabel):
    """Rewrite only the label on the hub's own file-status answer."""
    def fnFileStatus(route):
        dictStatus = json.loads(route.fetch().text())
        dictStatus["dictReproductionLabel"] = dictLabel
        route.fulfill(status=200, content_type="application/json",
                      body=json.dumps(dictStatus))
    pageDashboard.route("**/api/pipeline/*/file-status*", fnFileStatus)


def _fdictReadTopBarOnceShown(pageDashboard):
    dictRead = None
    for _ in range(120):
        dictRead = pageDashboard.evaluate(_S_READ_TOP_BAR)
        if dictRead and dictRead["bProjectBlockRendered"]:
            return dictRead
        pageDashboard.wait_for_timeout(250)
    return dictRead


def _fdictBuildAuthorLabel(tmp_path, monkeypatch, dictLastNoVerdict=None):
    """The label the REAL server code gives an author, from real files.

    A real git repository whose manifest is the viewer's own, a real
    attestation over it, and the real poll snapshot program -- the
    browser then renders exactly what the server built.
    """
    from tests.testReproductionLabelInPollResponse import (
        fdictPollAuthorLabel, fnWriteAuthorAttestation, fsBuildAuthorProject,
    )
    with monkeypatch.context() as patchIdentity:
        for sName, sValue in (
            ("GIT_CONFIG_NOSYSTEM", "1"),
            ("GIT_CONFIG_GLOBAL", __import__("os").devnull),
            ("GIT_COMMITTER_EMAIL", "author@example.invalid"),
            ("GIT_AUTHOR_EMAIL", "author@example.invalid"),
            ("GIT_COMMITTER_NAME", "Author"),
            ("GIT_AUTHOR_NAME", "Author"),
        ):
            patchIdentity.setenv(sName, sValue)
        sRepo = fsBuildAuthorProject(tmp_path)
        fnWriteAuthorAttestation(sRepo)
        return fdictPollAuthorLabel(sRepo, dictLastNoVerdict)


@pytest.mark.falsification
def test_the_badge_sits_in_the_top_bar_right_of_the_last_check(
    pageDashboard, serverHub,
):
    """In the top bar, immediately right of the right-most PROOF check.

    Kills: placing the badge's slot before the Level 3 check -- the
    badge would then sit between the checks rather than after them.
    """
    _fnServeTheLabel(pageDashboard, _fdictShown(
        sLatestAttemptIso="2026-10-05T08:00:00+00:00",
        sLatestAttemptVerdict="no-verdict",
        sReason="the daemon was unreachable"))
    fnOpenTheSeededHostWorkflow(
        pageDashboard, serverHub, bAwaitProjectBlock=True)
    dictRead = _fdictReadTopBarOnceShown(pageDashboard)
    assert dictRead, "no reproduction badge in the top bar"
    assert dictRead["sText"] == "Reproduced", dictRead
    assert dictRead["bBadgeInSlot"] is True
    assert dictRead["bSlotFollowsLastCheck"] is True, (
        "the badge's slot is not immediately after the last PROOF check")
    for sLevel in ("dictAtPageLevel", "dictAtLevelThree"):
        dictGeometry = dictRead[sLevel]
        assert dictGeometry["fBadgeLeft"] >= dictGeometry["fAnchorRight"], (
            f"the badge is left of the right-most check ({sLevel}): "
            f"{dictGeometry!r}")
        assert dictGeometry["bSameRow"] is True, (sLevel, dictGeometry)
    assert dictRead["sGlyphText"] == "⚠", dictRead
    assert dictRead["bGlyphFollowsBadge"] is True
    assert "the daemon was unreachable" in dictRead["sGlyphTitle"]
    assert dictRead["bProjectBlockCarriesBadge"] is False, (
        "the left column's Project header still carries the badge")
    assert pageDashboard.listPageErrors == []


@pytest.mark.falsification
def test_an_authors_current_attestation_reads_reproduced_in_the_top_bar(
    pageDashboard, serverHub, tmp_path, monkeypatch,
):
    """The author's label, as the server builds it, renders in the top bar.

    Kills: never rendering the top bar's badge -- the author's
    "Reproduced" would then appear nowhere on the page.
    """
    dictLabel = _fdictBuildAuthorLabel(tmp_path, monkeypatch)
    assert dictLabel["bShow"] is True, dictLabel
    _fnServeTheLabel(pageDashboard, dictLabel)
    fnOpenTheSeededHostWorkflow(
        pageDashboard, serverHub, bAwaitProjectBlock=True)
    dictRead = _fdictReadTopBarOnceShown(pageDashboard)
    assert dictRead and dictRead["sText"] == "Reproduced", dictRead
    assert dictRead["sTitle"] == (
        "Reproduced on 2026-10-12 (linux/arm64) under emulation."), dictRead
    assert dictRead["sGlyphText"] == "", dictRead
    assert dictRead["bProjectBlockCarriesBadge"] is False
    assert pageDashboard.listPageErrors == []
