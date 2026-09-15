"""The "Do this first" arrow rides the SECTION that holds its row.

A green Python suite proves the ORDER is computed; it does not execute
one line of the thing that shows it. This renders the real Project
block in a real browser and asks the questions a Python test cannot:
does the arrow appear at all, does it appear on the section that
actually contains its row and on no other, and does it stay away
entirely when the backend says order does not matter.

On the section banner rather than the project banner (researcher's
ruling, 2026-09-14): "Artifacts" is where the work is, and the marker
travels to Published envelope and then Attestation as the answer moves
down the ladder. That placement is also what creates the conflict
worth a click: the arrow sits inside a banner whose own handler
TOGGLES that section, and no test that does not click can see it.
"""

import pytest

from tests.browser.conftest import fnOpenTheSeededHostWorkflow


pytestmark = pytest.mark.browser


_S_RENDER_WITH_NEXT_STEP = """(dictNextStep) => {
    return VaibifyWorkflowRequirements.fsRenderProjectBlock({
        dictWorkflowEnvelopeDetail: {
            listLevel3EnvelopePaths: [],
            dictArtifacts: {
                manifest: {bPresent: true, bSatisfied: false},
                reproduceScript: {bPresent: true, bSatisfied: false},
            },
            dictImageCurrency: {bPinnedImageIsLive: null},
            listBinaries: [],
            dictNextOrderedStep: dictNextStep,
        },
        dictRemoteChecks: {},
        setToggledFileGroups: new Set(),
        bProjectBlockCollapsed: false,
        setExpandedRequirementGroups: new Set(['artifacts']),
        setExpandedRequirementRows: new Set(),
    });
}"""


_DICT_NEXT_STEP = {
    "sRowKey": "reproduceScript",
    "sReason": "MANIFEST.sha256 pins reproduce.sh.",
    "listBlockedRowKeys": ["manifest"],
}


@pytest.mark.falsification
def test_the_arrow_points_at_a_row_that_is_really_there(
    pageDashboard, serverHub,
):
    """ONE open, every assertion: the seeded project is leased, so a
    second open in this file is refused as another session.

    The arrow must appear on the section holding its row and on NO
    other section, and never on the project banner. The click half
    matters for a different reason: the arrow sits inside a banner
    whose handler toggles that very section, so a registration in the
    wrong order collapses the section over the row the researcher was
    just told to open.

    Kills: registering ``.ordering-arrow`` AFTER
    ``.requirement-group-header``. The dispatcher returns on its first
    match, so position is the whole guard -- ``stopPropagation`` is
    not, which this test established by mutation rather than by
    reading.
    """
    fnOpenTheSeededHostWorkflow(pageDashboard, serverHub)

    sHtml = pageDashboard.evaluate(
        _S_RENDER_WITH_NEXT_STEP, _DICT_NEXT_STEP,
    )
    assert 'class="ordering-arrow"' in sHtml, "no arrow was rendered"
    assert 'data-ordering-row="reproduceScript"' in sHtml
    assert 'data-ordering-group="artifacts"' in sHtml
    assert sHtml.count('class="ordering-arrow"') == 1, (
        "exactly one section may claim the next step"
    )

    # It rides the ARTIFACTS banner: after that group header, and
    # before the row it names. The project banner has none.
    iProjectHeader = sHtml.index('class="project-block-header"')
    iGroup = sHtml.index('data-group="artifacts"')
    iArrow = sHtml.index('class="ordering-arrow"')
    iRow = sHtml.index('data-req="reproduceScript"')
    assert iProjectHeader < iGroup < iArrow < iRow, (
        "the arrow is not on the banner of the section holding its row"
    )
    assert 'class="ordering-arrow"' not in sHtml[:iGroup], (
        "the arrow is still on the project banner"
    )

    # The row is marked, and says WHY it comes first where a touch
    # screen can read it -- the arrow's tooltip cannot be hovered.
    assert "requirement-row-next" in sHtml
    assert "MANIFEST.sha256 pins reproduce.sh." in sHtml

    # ...and silence really is silent: no arrow, no marked row, no
    # reason line anywhere.
    sQuiet = pageDashboard.evaluate(_S_RENDER_WITH_NEXT_STEP, None)
    assert "ordering-arrow" not in sQuiet
    assert "requirement-row-next" not in sQuiet

    dictReached = pageDashboard.evaluate(
        """() => {
            // Drive the real delegated dispatcher over a synthetic
            // arrow: the assertion is about the registry's ORDER, and
            // that is a property of the dispatcher, not of any one
            // project's live state.
            // Inside #panelSteps: that is where the delegated click
            // listener is actually bound, so appending to document.body
            // would test nothing and pass nothing.
            const elPanel = document.querySelector('#panelSteps');
            if (!elPanel) {
                return {sOpenedRow: 'NO-PANEL', bCollapsed: false};
            }
            const elHost = document.createElement('div');
            elHost.innerHTML =
                '<div class="requirement-group-header" ' +
                'data-group="artifacts">' +
                '<button class="ordering-arrow" ' +
                'data-ordering-group="artifacts" ' +
                'data-ordering-row="reproduceScript"></button></div>';
            elPanel.appendChild(elHost);
            let sOpenedRow = '';
            let bToggled = false;
            const fnRealExpand = VaibifyApp.fnExpandRequirementRow;
            const fnRealToggle = VaibifyApp.fnToggleRequirementGroup;
            VaibifyApp.fnExpandRequirementRow = (sGroup, sRow) => {
                sOpenedRow = sRow;
            };
            VaibifyApp.fnToggleRequirementGroup = () => {
                bToggled = true;
            };
            try {
                elHost.querySelector('.ordering-arrow').click();
            } finally {
                VaibifyApp.fnExpandRequirementRow = fnRealExpand;
                VaibifyApp.fnToggleRequirementGroup = fnRealToggle;
                elHost.remove();
            }
            return {sOpenedRow: sOpenedRow, bCollapsed: bToggled};
        }"""
    )
    assert dictReached["sOpenedRow"] == "reproduceScript", (
        "the arrow did not open the row it names"
    )
    assert dictReached["bCollapsed"] is False, (
        "the click fell through and toggled the section it sits on"
    )
