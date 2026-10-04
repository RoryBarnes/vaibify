"""The left column's heading reads "Project (reproduced)" only when told to.

The label is the server's verdict, rendered and never re-derived: a
payload with ``bShow`` true makes the heading read "Project
(reproduced)", with hover text naming the date, the platform and --
when the run was emulated, or a later attempt reached no verdict --
those facts too. Every other state (absent, diverged, evidence
changed, unreadable, no payload at all) leaves the heading reading
"Project", because a heading is a claim and silence is not evidence.
"""

import pytest

from tests.browser.conftest import fnOpenTheSeededHostWorkflow

pytestmark = pytest.mark.browser

_S_RENDER = """(dictLabel) => {
    var sHtml = VaibifyWorkflowRequirements.fsRenderProjectBlock({
        dictWorkflowEnvelopeDetail: {
            listLevel3EnvelopePaths: [], dictArtifacts: {},
            listBinaries: [],
        },
        dictReproductionLabel: dictLabel,
        dictRemoteChecks: {},
        setToggledFileGroups: new Set(),
        bProjectBlockCollapsed: false,
        setExpandedRequirementGroups: new Set(),
        setExpandedRequirementRows: new Set(),
    });
    var el = document.createElement("div");
    el.innerHTML = sHtml;
    var elTitle = el.querySelector(".project-block-title");
    return {sText: elTitle.textContent.trim(), sTitle: elTitle.title};
}"""


def _fdictRender(pageDashboard, dictLabel):
    return pageDashboard.evaluate(_S_RENDER, dictLabel)


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
def test_the_heading_reads_reproduced_only_for_a_shown_label(
    pageDashboard, serverHub,
):
    """Kills: drawing the label whatever the server's verdict says."""
    fnOpenTheSeededHostWorkflow(pageDashboard, serverHub)
    dictShown = _fdictRender(pageDashboard, _fdictShown())
    assert dictShown["sText"].replace("▸", "").replace("▾", "").strip() in (
        "Project (reproduced)",)
    assert "2026-10-04" in dictShown["sTitle"]
    assert "linux/amd64" in dictShown["sTitle"]
    for dictHidden in (
        None,
        _fdictShown(bShow=False, sState="absent"),
        _fdictShown(bShow=False, sState="diverged"),
        _fdictShown(bShow=False, sState="evidence-changed"),
        _fdictShown(bShow=False, sState="unreadable"),
    ):
        sText = _fdictRender(pageDashboard, dictHidden)["sText"]
        assert "reproduced" not in sText, dictHidden
        assert sText.replace("▸", "").replace("▾", "").strip() == "Project"
    assert pageDashboard.listPageErrors == []


def test_the_hover_names_emulation_and_a_later_attempt_with_no_verdict(
    pageDashboard, serverHub,
):
    fnOpenTheSeededHostWorkflow(pageDashboard, serverHub)
    sEmulated = _fdictRender(
        pageDashboard, _fdictShown(bEmulated=True))["sTitle"]
    assert "under emulation" in sEmulated
    sLater = _fdictRender(pageDashboard, _fdictShown(
        sLatestAttemptIso="2026-10-05T08:00:00+00:00",
        sLatestAttemptVerdict="no-verdict",
        sReason="the daemon was unreachable"))["sTitle"]
    assert "Reproduced on 2026-10-04" in sLater
    assert "The latest attempt, on 2026-10-05, reached no verdict: " \
        "the daemon was unreachable." in sLater
