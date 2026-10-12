"""The Project header's reproduction badge renders the server's answers.

The badge sits right of the Project block's level strip and replaces
the "(reproduced)" suffix the heading used to carry (researcher's
ruling). It says one of two things, each the server's:

- "Reproducing" while the poll reports a Level 3 verification live;
- "Reproduced" when the server's reproduction label has ``bShow``
  true, with hover text naming the date, the platform and any
  emulation. Every other label state (absent, diverged, evidence
  changed, unreadable, no payload at all) draws nothing, because a
  badge is a claim and silence is not evidence.

A later attempt that reached NO verdict keeps an earlier "Reproduced"
(the server's rule), and the dashboard's own warning glyph appears to
its right carrying that attempt's date and the server's reason -- the
sentence lives on the glyph, not on the badge.
"""

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
    var sHtml = VaibifyWorkflowRequirements.fsRenderProjectBlock({
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
        // The level strip renders only with a level projection; any
        // will do, since only its POSITION is read here.
        fsLevelCellState: () => "not-started",
        fsLevelCellTooltip: () => "",
    });
    var el = document.createElement("div");
    el.innerHTML = sHtml;
    document.body.appendChild(el);
    var elTitle = el.querySelector(".project-block-title");
    var elBadge = el.querySelector(
        ".project-block-header .project-reproduction-badge");
    var elGlyph = el.querySelector(
        ".project-block-header .project-reproduction-warning");
    var elStrip = el.querySelector(".project-block-header .step-level-strip");
    var dictAnswer = {
        sHeading: elTitle.textContent.replace(/[\\u25b8\\u25be]/g, "").trim(),
        sBadgeText: elBadge ? elBadge.textContent : "",
        sBadgeTitle: elBadge ? elBadge.title : "",
        bBadgeRightOfStrip: Boolean(elBadge && elStrip &&
            (elStrip.compareDocumentPosition(elBadge) &
                Node.DOCUMENT_POSITION_FOLLOWING)),
        sGlyphText: elGlyph ? elGlyph.textContent : "",
        sGlyphTitle: elGlyph ? elGlyph.title : "",
        bGlyphFollowsBadge: Boolean(elGlyph && elBadge &&
            elBadge.nextElementSibling === elGlyph),
        sGlyphColor: elGlyph ? getComputedStyle(elGlyph).color : "",
    };
    el.remove();
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
    assert dictShown["bBadgeRightOfStrip"] is True, (
        "the badge is not to the right of the level strip"
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
