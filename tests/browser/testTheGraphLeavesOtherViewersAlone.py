"""The dependency graph repaints only a viewer it still holds.

The graph is an image, so recoloring its edges means painting it again,
and the dashboard did that after every step-list render, every test
result and every run event, into the last viewer it had used. The
viewports are permanent elements whose contents the figure viewer
swaps, so the check "is that viewport still in the page" was always
true: a figure opened over the graph snapped back to the graph on the
next render (alpha tester, 2026-10-23). A graph requested just before a
project switch also painted when its reply arrived.

The oracle is what the researcher sees: whatever they opened last in a
viewer stays there; another project never shows this project's graph;
returning to the same project finds the graph where it was left.
"""

import pytest

from tests.browser.conftest import (
    S_HOST_STEP_NAME,
    S_HOST_WORKFLOW_NAME,
    fnOpenTheSeededHostWorkflow,
)


pytestmark = pytest.mark.browser

S_DAG_ROUTE_GLOB = "**/api/workflow/*/dag"
S_FIGURE_ROUTE_GLOB = "**/api/figure/**"
S_GRAPH_SVG = (
    '<svg xmlns="http://www.w3.org/2000/svg" width="40" height="20">'
    '<g class="edge"><path d="M0 0 L40 20"/></g></svg>'
)
S_FIGURE_FILE = "openedOverTheGraph.txt"
S_VIEWER_GRAPH_SCRIPT = """(sLetter) => !!document.getElementById(
    'viewport' + sLetter).querySelector('.dag-container img.dag-image')"""


def _fnServeTheGraphAndFiles(page):
    page.route(S_DAG_ROUTE_GLOB, lambda routeIntercepted: routeIntercepted
               .fulfill(status=200, content_type="image/svg+xml",
                        body=S_GRAPH_SVG))
    page.route(S_FIGURE_ROUTE_GLOB, lambda routeIntercepted: routeIntercepted
               .fulfill(status=200, content_type="text/plain",
                        body="contents of " + S_FIGURE_FILE))


def _fsShowTheGraph(page):
    """Show the graph; return the letter of the viewer it was painted in.

    Every graph already on screen is marked first, so the wait is for a
    NEW paint, including a repaint into the viewer that held it.
    """
    page.evaluate(
        """() => {
            document.querySelectorAll('.dag-container').forEach(
                (el) => { el._bPaintedBefore = true; });
            VaibifyApp.fnShowDag();
        }"""
    )
    page.wait_for_function(
        """() => Array.from(document.querySelectorAll(
            '.dag-container img.dag-image')).some(
            (el) => !el.closest('.dag-container')._bPaintedBefore)""",
        timeout=15000,
    )
    return page.evaluate(
        """() => ['A', 'B'].find((sLetter) => Array.from(
            document.getElementById('viewport' + sLetter)
            .querySelectorAll('.dag-container'))
            .some((el) => !el._bPaintedBefore))"""
    )


def _fnOpenTheFigureIn(page, sLetter):
    page.evaluate(
        "(sLetter) => VaibifyFigureViewer.fnDisplayFileInViewer("
        "sLetter, '" + S_FIGURE_FILE + "', '')", sLetter,
    )
    page.wait_for_function(
        "(sLetter) => document.getElementById('viewport' + sLetter)"
        ".innerText.includes('contents of " + S_FIGURE_FILE + "')",
        arg=sLetter, timeout=10000,
    )


def _fnRenderEverythingThatRepaintsTheGraph(page):
    """Run the renders that repaint the graph, and let them settle."""
    page.evaluate(
        """() => new Promise((fnResolve) => {
            VaibifyApp.fnRenderStepList();
            VaibifyApp.fnUpdateHighlightState();
            requestAnimationFrame(() => requestAnimationFrame(fnResolve));
        })"""
    )


def _fbViewerShowsTheFigure(page, sLetter):
    return page.evaluate(
        "(sLetter) => document.getElementById('viewport' + sLetter)"
        ".innerText.includes('contents of " + S_FIGURE_FILE + "')",
        sLetter,
    )


def _fbAnyViewerShowsTheGraph(page):
    return any(page.evaluate(S_VIEWER_GRAPH_SCRIPT, sLetter)
               for sLetter in ("A", "B"))


def _fnLeaveForTheProjectHub(page):
    page.evaluate("() => VaibifyApp.fnReconnectToCurrentContainer()")
    page.wait_for_selector(f"text={S_HOST_WORKFLOW_NAME}", timeout=20000)


def _fnWorkWithoutAProject(page):
    _fnLeaveForTheProjectHub(page)
    page.click("#btnNoWorkflow")
    page.wait_for_selector("#mainLayout.active", timeout=20000)


@pytest.mark.falsification
def testAFigureOpenedOverTheGraphSurvivesARender(pageDashboard, serverHub):
    """Kills: repainting the graph without asking whether it holds the viewer."""
    fnOpenTheSeededHostWorkflow(pageDashboard, serverHub)
    _fnServeTheGraphAndFiles(pageDashboard)
    sLetter = _fsShowTheGraph(pageDashboard)
    _fnOpenTheFigureIn(pageDashboard, sLetter)
    _fnRenderEverythingThatRepaintsTheGraph(pageDashboard)
    assert _fbViewerShowsTheFigure(pageDashboard, sLetter)
    assert not pageDashboard.evaluate(S_VIEWER_GRAPH_SCRIPT, sLetter)
    sAgain = _fsShowTheGraph(pageDashboard)
    assert sAgain != sLetter
    assert _fbViewerShowsTheFigure(pageDashboard, sLetter)
    assert pageDashboard.listPageErrors == []


@pytest.mark.falsification
def testAGraphThatArrivesAfterASwitchIsDropped(pageDashboard, serverHub):
    """Kills: painting a graph reply without checking the project it is for."""
    fnOpenTheSeededHostWorkflow(pageDashboard, serverHub)
    listHeld = []
    pageDashboard.route(
        S_DAG_ROUTE_GLOB,
        lambda routeIntercepted: listHeld.append(routeIntercepted),
    )
    pageDashboard.evaluate("() => { VaibifyApp.fnShowDag(); }")
    for _ in range(50):
        if listHeld:
            break
        pageDashboard.wait_for_timeout(100)
    assert listHeld, "the graph was never requested"
    _fnWorkWithoutAProject(pageDashboard)
    listHeld[0].fulfill(status=200, content_type="image/svg+xml",
                        body=S_GRAPH_SVG)
    pageDashboard.wait_for_timeout(1000)
    _fnRenderEverythingThatRepaintsTheGraph(pageDashboard)
    assert not _fbAnyViewerShowsTheGraph(pageDashboard)


def testAnotherProjectNeverShowsThisProjectsGraph(pageDashboard, serverHub):
    fnOpenTheSeededHostWorkflow(pageDashboard, serverHub)
    _fnServeTheGraphAndFiles(pageDashboard)
    _fsShowTheGraph(pageDashboard)
    _fnWorkWithoutAProject(pageDashboard)
    _fnRenderEverythingThatRepaintsTheGraph(pageDashboard)
    assert not _fbAnyViewerShowsTheGraph(pageDashboard)


def testReturningToTheSameProjectKeepsTheGraph(pageDashboard, serverHub):
    """The graph stays, a render repaints it, and Show DAG reuses its viewer."""
    fnOpenTheSeededHostWorkflow(pageDashboard, serverHub)
    _fnServeTheGraphAndFiles(pageDashboard)
    sLetter = _fsShowTheGraph(pageDashboard)
    _fnLeaveForTheProjectHub(pageDashboard)
    pageDashboard.click(f"text={S_HOST_WORKFLOW_NAME}")
    pageDashboard.wait_for_selector(f"text={S_HOST_STEP_NAME}", timeout=20000)
    assert pageDashboard.evaluate(S_VIEWER_GRAPH_SCRIPT, sLetter)
    pageDashboard.evaluate(
        "(sLetter) => { document.getElementById('viewport' + sLetter)"
        ".querySelector('.dag-container')._bBeforeRender = true; }",
        sLetter,
    )
    _fnRenderEverythingThatRepaintsTheGraph(pageDashboard)
    assert pageDashboard.evaluate(
        "(sLetter) => { const el = document.getElementById('viewport' + "
        "sLetter).querySelector('.dag-container'); "
        "return !!el && !el._bBeforeRender; }",
        sLetter,
    ), "a render did not repaint the graph it still holds"
    assert _fsShowTheGraph(pageDashboard) == sLetter
    assert pageDashboard.listPageErrors == []
