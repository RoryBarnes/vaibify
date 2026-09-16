"""One help button, and every section folded shut behind it.

The dashboard had TWO question marks: one in the top toolbar and one
in the terminal strip. Neither said so, and the answer to "how do I
select text in the terminal" lived behind only the second. On
2026-09-16 a researcher lost most of a session to a terminal they
could not copy from, looked in the toolbar's help -- which had nothing
to say about terminals -- and never found the other one. The content
was there the whole time. Two doors to one subject is the defect; a
researcher cannot be expected to guess which door.

So: exactly ONE help control, and it carries the terminal section.
The count is asserted rather than the presence, because adding a
second help button back is precisely the regression, and a test that
only checks "a help button exists" passes happily through it.

Everything folds SHUT. The panel is reference material consulted for
one glyph, and opening it on four expanded divisions of marks and
criteria is what made it unreadable. `iOpen == 0` is the whole claim;
a section that ships open would quietly restore the wall of text.

The button sits at the FAR RIGHT of the toolbar. It used to sit
between the project name and the PROOF checkmarks -- inside an
identity readout it says nothing about, and pushing the checkmarks
off the name they describe. Asserted against the toolbar's own right
edge rather than a pixel constant, so the assertion survives a
different window size.

Kills (confirmed -- the mutation was applied, this test run, and the
named assertion observed to fail):
  - the Terminal usage section dropped from the panel -> the summary
    list no longer contains it.
"""

import pytest

from tests.browser.conftest import (
    S_HOST_PROJECT_READY,
    S_HOST_STEP_NAME,
    S_HOST_WORKFLOW_NAME,
)


pytestmark = pytest.mark.browser

I_TOOLBAR_EDGE_TOLERANCE_PIXELS = 40


def _fnOpenTheProject(pageDashboard, serverHub):
    """Enter the seeded host project so the toolbar is laid out."""
    pageDashboard.goto(serverHub.fsBootstrapUrl(), wait_until="load")
    pageDashboard.wait_for_selector(
        f'.container-tile[data-name="{S_HOST_PROJECT_READY}"]',
        timeout=15000,
    )
    pageDashboard.click(
        f'.container-tile[data-name="{S_HOST_PROJECT_READY}"] '
        '.container-tile-main',
    )
    pageDashboard.wait_for_selector("#modalConfirm", timeout=10000)
    pageDashboard.click("#btnConfirmOk")
    pageDashboard.wait_for_selector(
        f"text={S_HOST_WORKFLOW_NAME}", timeout=20000,
    )
    pageDashboard.click(f"text={S_HOST_WORKFLOW_NAME}")
    pageDashboard.wait_for_selector(
        f"text={S_HOST_STEP_NAME}", timeout=20000,
    )


@pytest.mark.falsification
def testTheOneHelpPanelCarriesTheTerminalAndOpensFolded(
    pageDashboard, serverHub,
):
    """One help button, at the right, with every section shut.

    Kills: the Terminal usage section dropped -> it is absent from the
    panel's summary list.
    """
    _fnOpenTheProject(pageDashboard, serverHub)

    iHelpControls = pageDashboard.evaluate(
        """() => document.querySelectorAll(
            '#proofLegendButton, #btnTerminalHelp').length"""
    )
    assert iHelpControls == 1, (
        f"the dashboard offers {iHelpControls} help controls; two "
        "doors to one subject is what sent a researcher to the wrong "
        "one and cost them a session"
    )

    dictEdges = pageDashboard.evaluate(
        """() => {
            const elButton =
                document.getElementById('proofLegendButton');
            const elToolbar = document.getElementById('toolbar');
            return {
                fButtonRight:
                    elButton.getBoundingClientRect().right,
                fToolbarRight:
                    elToolbar.getBoundingClientRect().right,
            };
        }"""
    )
    fGap = dictEdges["fToolbarRight"] - dictEdges["fButtonRight"]
    assert 0 <= fGap < I_TOOLBAR_EDGE_TOLERANCE_PIXELS, (
        f"the help button sits {fGap:.0f}px from the toolbar's right "
        "edge; it belongs at the far right, where the panel opens and "
        "where a researcher looks for help"
    )

    pageDashboard.click("#proofLegendButton")
    pageDashboard.wait_for_selector(
        "#proofLegendPanel.is-open", timeout=10000,
    )
    dictPanel = pageDashboard.evaluate(
        """() => {
            const elPanel =
                document.getElementById('proofLegendPanel');
            const listDetails =
                Array.from(elPanel.querySelectorAll('details'));
            return {
                iSections: listDetails.length,
                iOpen: listDetails.filter(el => el.open).length,
                listSummaries: listDetails.map(
                    el => el.querySelector('summary').innerText),
            };
        }"""
    )

    assert "Terminal usage" in dictPanel["listSummaries"], (
        "the terminal's help was folded into this panel when its own "
        "question mark was removed; without this section that content "
        f"is unreachable: {dictPanel['listSummaries']}"
    )
    assert dictPanel["iOpen"] == 0, (
        f"{dictPanel['iOpen']} of {dictPanel['iSections']} sections "
        "open on load; the panel is consulted for one glyph and must "
        "not greet the researcher with every division expanded"
    )
    assert not pageDashboard.listConsoleErrors, (
        f"console errors on the help path: "
        f"{pageDashboard.listConsoleErrors[:3]}"
    )
