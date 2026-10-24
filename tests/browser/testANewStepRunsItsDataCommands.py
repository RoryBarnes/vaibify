"""A new step runs its data commands unless the researcher says otherwise.

The add-step form opened with "Plot only" ticked, and the form sends
the box's state explicitly, so every step created through the
dashboard without touching the box was saved plots-only and its data
commands never ran (alpha tester, 2026-10-23). The ruling (R1b) is that
plots-only is off unless asked for. A step that IS plots-only while it
has data commands says so on its row, because nothing else on the
collapsed row would.
"""

import json

import pytest

from tests.browser.conftest import fnOpenTheSeededHostWorkflow


pytestmark = pytest.mark.browser

S_CREATE_ROUTE_GLOB = "**/api/steps/*/create"
S_MARKER_TEXT = "Data commands skipped (plots only)"


def _fnOpenTheAddStepForm(pageDashboard):
    pageDashboard.evaluate("() => VaibifyStepEditor.fnOpenCreateModal()")
    pageDashboard.wait_for_selector(
        "#modalStepEditor.active", timeout=10000,
    )


@pytest.mark.falsification
def testTheAddStepFormOpensWithPlotsOnlyUnchecked(pageDashboard, serverHub):
    """Kills: initializing the form's Plot only box as checked."""
    fnOpenTheSeededHostWorkflow(pageDashboard, serverHub)
    _fnOpenTheAddStepForm(pageDashboard)
    assert pageDashboard.is_checked("#inputPlotOnly") is False


def testAStepMadeThroughTheFormIsSavedToRunItsData(pageDashboard, serverHub):
    """The saved step, as the server built it, is not plots-only."""
    fnOpenTheSeededHostWorkflow(pageDashboard, serverHub)
    _fnOpenTheAddStepForm(pageDashboard)
    pageDashboard.fill("#inputStepName", "Fresh Analysis")
    pageDashboard.fill("#inputSetupCommands", "python3 makeData.py")
    with pageDashboard.expect_response(S_CREATE_ROUTE_GLOB) as infoResponse:
        pageDashboard.click("#btnModalSave")
    dictSent = json.loads(infoResponse.value.request.post_data)
    dictSaved = infoResponse.value.json()["dictStep"]
    assert dictSent["bPlotOnly"] is False
    assert dictSaved["bPlotOnly"] is False
    pageDashboard.wait_for_selector("text=Fresh Analysis", timeout=10000)
    assert pageDashboard.listPageErrors == []


def _flistMarkersAfterSettingPlotOnly(pageDashboard, bPlotOnly):
    """Set the first step's Plot only in the page; read its rendered row.

    The step list renders on the next animation frame, so the row is
    read once a render has run, never synchronously.
    """
    pageDashboard.evaluate(
        """(bPlotOnly) => {
            VaibifyApp.fdictGetWorkflow().listSteps[0].bPlotOnly = bPlotOnly;
            window._bMarkerRowRendered = false;
            VaibifyApp.fnRenderStepList();
            requestAnimationFrame(() => requestAnimationFrame(() => {
                window._bMarkerRowRendered = true;
            }));
        }""",
        bPlotOnly,
    )
    pageDashboard.wait_for_function(
        "() => window._bMarkerRowRendered === true", timeout=10000,
    )
    return pageDashboard.evaluate(
        """() => Array.from(document.querySelectorAll(
            '.step-item[data-index="0"] .step-execution-setting'))
            .map((el) => el.textContent)"""
    )


def testAPlotsOnlyStepWithDataCommandsSaysTheyAreSkipped(
    pageDashboard, serverHub,
):
    """The seeded first step has data commands; the row names the skip."""
    fnOpenTheSeededHostWorkflow(pageDashboard, serverHub)
    assert _flistMarkersAfterSettingPlotOnly(pageDashboard, True) == [
        S_MARKER_TEXT,
    ]
    assert _flistMarkersAfterSettingPlotOnly(pageDashboard, False) == []
