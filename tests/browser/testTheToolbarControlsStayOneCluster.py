"""The toolbar's controls sit in one run, hard against the right edge.

Adding the host-settings gear tore them apart, and the cause is worth
recording because it looks like the opposite of a bug: the gear was
given ``margin-left: auto`` to push it right. But
``.toolbar-title-group`` already owns the bar's free space with
``margin-right: auto``, and flexbox splits free space EQUALLY between
every auto margin -- so a second one did not push the gear right, it
halved the slack and parked the gear and the help button across a gap
from the buttons they belong with. The researcher's words for it:
"the Agent Council..Admin buttons seem to no longer be pegged to the
?" (2026-09-21).

A fixed ``margin-left`` was the same mistake in miniature: it stacked
on the toolbar's own ``gap: 8px`` and left the help button 16px from
its neighbour where every other pair sat at 8px.

Neither is visible to a Python suite, and neither is visible to a
browser test that only asserts an element EXISTS. What is asserted
here is geometry.
"""

import pytest

from tests.browser.conftest import (
    S_HOST_PROJECT_READY,
    S_HOST_STEP_NAME,
    S_HOST_WORKFLOW_NAME,
)


pytestmark = pytest.mark.browser

# Every control that must travel together, in bar order.
T_CLUSTER_SELECTORS = (
    "#btnAgentCouncil",
    "#toolbarMenuRun",
    "#btnHostSettings",
    "#proofLegendButton",
)

S_READ_CLUSTER_GEOMETRY = """() => {
    const bar = document.getElementById('toolbar');
    const listItems = Array.from(bar.children).filter(function (el) {
        return el.offsetParent !== null
            && el.getBoundingClientRect().width > 0
            && !el.classList.contains('toolbar-title-group')
            && !el.classList.contains('toolbar-logo');
    });
    const listGaps = [];
    for (let i = 1; i < listItems.length; i += 1) {
        listGaps.push(Math.round(
            listItems[i].getBoundingClientRect().left
            - listItems[i - 1].getBoundingClientRect().right));
    }
    const elLast = listItems[listItems.length - 1];
    const rBar = bar.getBoundingClientRect();
    return {
        listGaps: listGaps,
        iRightInset: Math.round(
            rBar.right - elLast.getBoundingClientRect().right),
        iBarPaddingRight: parseInt(getComputedStyle(bar).paddingRight, 10),
        iFlexGap: parseInt(getComputedStyle(bar).gap, 10),
    };
}"""


def _fnOpenTheProjectDashboard(page, serverHub):
    """Enter the host project and land in its step viewer."""
    page.goto(serverHub.fsBootstrapUrl(), wait_until="load")
    page.wait_for_selector(
        f'.container-tile[data-name="{S_HOST_PROJECT_READY}"]'
        ':not(.container-tile--locked)', timeout=60000)
    page.click(
        f'.container-tile[data-name="{S_HOST_PROJECT_READY}"] '
        '.container-tile-main')
    page.wait_for_selector("#modalConfirm", timeout=10000)
    page.click("#btnConfirmOk")
    page.wait_for_selector(f"text={S_HOST_WORKFLOW_NAME}", timeout=20000)
    page.click(f"text={S_HOST_WORKFLOW_NAME}")
    page.wait_for_selector(f"text={S_HOST_STEP_NAME}", timeout=20000)


@pytest.mark.falsification
def testEveryToolbarControlIsOneRunAgainstTheRightEdge(
    pageDashboard, serverHub,
):
    """One slack owner, one gap rule, at every width a researcher uses.

    The oracle is the toolbar's own stylesheet rather than a measured
    constant: the spacing between controls must equal the bar's
    declared ``gap``, and the last control must end exactly one
    ``padding-right`` from the bar's edge. A rule that produced any
    other number is a rule that put space where the design did not.

    Kills: giving the host-settings gear ``margin-left: auto``, which
    splits the bar's free space with the title group instead of
    pushing anything, and strands the right-hand controls across a gap.
    """
    _fnOpenTheProjectDashboard(pageDashboard, serverHub)
    for sSelector in T_CLUSTER_SELECTORS:
        assert pageDashboard.is_visible(sSelector), (
            f"{sSelector} must be on the project dashboard's toolbar"
        )
    for iWidth in (1680, 1440, 1280, 1100):
        pageDashboard.set_viewport_size({"width": iWidth, "height": 900})
        pageDashboard.wait_for_timeout(150)
        dictGeometry = pageDashboard.evaluate(S_READ_CLUSTER_GEOMETRY)
        iGap = dictGeometry["iFlexGap"]
        assert set(dictGeometry["listGaps"]) == {iGap}, (
            f"at {iWidth}px the toolbar controls are not one run: gaps "
            f"{dictGeometry['listGaps']} against a declared gap of {iGap}"
        )
        assert dictGeometry["iRightInset"] == (
            dictGeometry["iBarPaddingRight"]
        ), (
            f"at {iWidth}px the last control does not sit against the "
            f"bar's right padding edge"
        )
    assert pageDashboard.listPageErrors == [], pageDashboard.listPageErrors
