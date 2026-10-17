"""The two viewers belong to the project that filled them.

The viewers are page state, and the page outlives every dashboard: a
researcher who left one project's dashboard and opened another --
even in another environment -- found the first project's figures
still on screen (2026-10-16). Worse than stale, they were wrong: a
viewer resolves its path against the CURRENT container only when it
displays, so Back and Refresh fetched the new project's file of the
old name beneath the old project's picture.

The researcher's rule is the oracle: another project opens with empty
viewers; stepping out to the Projects Hub or the Environments Hub and
returning to the SAME project finds them exactly as they were left.
Every journey here leaves and re-enters through the real hub screens
and the real dashboard entry points, never by calling the viewer
module directly.
"""

import json

import pytest

from tests.browser.conftest import (
    S_HOST_PROJECT_READY,
    S_HOST_STEP_NAME,
    S_HOST_WORKFLOW_NAME,
)


pytestmark = pytest.mark.browser

S_FIGURE_ROUTE_GLOB = "**/api/figure/**"
S_CONNECT_ROUTE_GLOB = "**/api/connect/*"
S_FIRST_FILE = "firstViewerFile.txt"
S_SECOND_FILE = "secondViewerFile.txt"
S_OTHER_CONTAINER_ID = "fake-resource"
S_TILE_SELECTOR = f'.container-tile[data-name="{S_HOST_PROJECT_READY}"]'


def _fnServeFilesByName(page):
    """Answer every viewer fetch with text naming the file it asked for.

    The name in the body is the marker the assertions read, so each
    file on screen is identifiable without a real file behind it.
    """
    page.route(
        S_FIGURE_ROUTE_GLOB,
        lambda routeIntercepted: routeIntercepted.fulfill(
            status=200,
            content_type="text/plain",
            body="contents of " +
            routeIntercepted.request.url.split("?")[0].split("/")[-1],
        ),
    )


def _fnOpenTheProjectHubFromTheTile(page):
    """Click the host project's tile and land on its Projects Hub."""
    page.wait_for_selector(
        S_TILE_SELECTOR + ":not(.container-tile--locked)", timeout=90000,
    )
    page.click(S_TILE_SELECTOR + " .container-tile-main")
    page.wait_for_function(
        """() => {
            const elPicker = document.getElementById('workflowPicker');
            const elModal = document.getElementById('modalConfirm');
            return (elPicker && elPicker.style.display === 'flex') ||
                (elModal && getComputedStyle(elModal).display !== 'none');
        }""",
        timeout=10000,
    )
    if page.is_visible("#modalConfirm"):
        page.click("#btnConfirmOk")
    page.wait_for_selector(f"text={S_HOST_WORKFLOW_NAME}", timeout=20000)


def _fnOpenTheEnvironmentsHub(page, serverHub):
    """Load the dashboard fresh and enter the host project's tile."""
    page.goto(serverHub.fsBootstrapUrl(), wait_until="load")
    page.wait_for_selector(S_TILE_SELECTOR, timeout=15000)
    _fnServeFilesByName(page)


def _fnOpenTheSeededProject(page):
    """From the Projects Hub, open the seeded project's dashboard."""
    page.click(f"text={S_HOST_WORKFLOW_NAME}")
    page.wait_for_selector(f"text={S_HOST_STEP_NAME}", timeout=20000)


def _fnWorkInTheEnvironmentWithoutAProject(page):
    """From the Projects Hub, open the dashboard with no project."""
    page.click("#btnNoWorkflow")
    page.wait_for_selector("#mainLayout.active", timeout=20000)


def _fnShowTwoFilesInViewerA(page):
    """Fill viewer A with two files so it has a Back entry and history."""
    for sFile in (S_FIRST_FILE, S_SECOND_FILE):
        page.evaluate(
            "(sPath) => VaibifyFigureViewer.fnDisplayFileInViewer("
            "'A', sPath, '')",
            sFile,
        )
        page.wait_for_function(
            "(sFile) => document.getElementById('viewportA')"
            ".innerText.includes('contents of ' + sFile)",
            arg=sFile, timeout=10000,
        )


def _fnReturnToTheProjectHub(page):
    """Leave the dashboard by the menu's Switch Project path."""
    page.evaluate("() => VaibifyApp.fnReconnectToCurrentContainer()")
    page.wait_for_selector(f"text={S_HOST_WORKFLOW_NAME}", timeout=20000)


def _fnReturnToTheEnvironmentsHub(page):
    """Leave the dashboard by the logo, releasing the claim for real."""
    page.click(".toolbar-logo")
    page.wait_for_function(
        "() => document.getElementById('containerLanding')"
        ".style.display === 'flex'",
        timeout=20000,
    )


def _fdictViewerAState(page):
    """Read what viewer A shows and offers, as the researcher sees it."""
    return page.evaluate(
        """() => ({
            sText: document.getElementById('viewportA').innerText,
            bBackEnabled: !document.getElementById('btnBackA').disabled,
            listHistory: Array.from(
                document.querySelectorAll('#selectFigureA option'))
                .map((el) => el.value).filter(Boolean),
        })"""
    )


def _fnAssertViewerAKept(page):
    """Assert viewer A survived, and that its Back button still WORKS.

    Reading the button's enabled state is not enough: the code that
    shipped emptied the Back history on leaving while the button and
    the history list stayed on screen looking intact.
    """
    dictState = _fdictViewerAState(page)
    assert "contents of " + S_SECOND_FILE in dictState["sText"], (
        "returning to the same project lost the file viewer A was "
        f"showing: {dictState['sText']!r}"
    )
    assert dictState["bBackEnabled"], (
        "returning to the same project lost viewer A's Back entry"
    )
    assert set(dictState["listHistory"]) == {S_FIRST_FILE, S_SECOND_FILE}, (
        f"returning to the same project lost the history: {dictState}"
    )
    page.click("#btnBackA")
    page.wait_for_function(
        "(sFile) => document.getElementById('viewportA')"
        ".innerText.includes('contents of ' + sFile)",
        arg=S_FIRST_FILE, timeout=10000,
    )


def _fnAssertViewerAEmpty(dictState):
    assert "contents of" not in dictState["sText"], (
        "another project opened showing the previous project's file: "
        f"{dictState['sText']!r}"
    )
    assert not dictState["bBackEnabled"], (
        "another project opened with Back leading into the previous "
        "project's files"
    )
    assert dictState["listHistory"] == [], (
        "another project opened listing the previous project's files: "
        f"{dictState['listHistory']}"
    )


@pytest.mark.falsification
def testReturningFromTheProjectsHubKeepsTheViewers(pageDashboard, serverHub):
    """Projects Hub and back to the same project: nothing moves.

    Kills: emptying the viewers on every dashboard entry, which
    replaces the leak with the opposite loss.
    """
    _fnOpenTheEnvironmentsHub(pageDashboard, serverHub)
    _fnOpenTheProjectHubFromTheTile(pageDashboard)
    _fnOpenTheSeededProject(pageDashboard)
    _fnShowTwoFilesInViewerA(pageDashboard)
    _fnReturnToTheProjectHub(pageDashboard)
    _fnOpenTheSeededProject(pageDashboard)
    _fnAssertViewerAKept(pageDashboard)
    assert pageDashboard.listPageErrors == []


@pytest.mark.falsification
def testReturningFromTheEnvironmentsHubKeepsTheViewers(
    pageDashboard, serverHub,
):
    """Environments Hub and back to the same project: nothing moves.

    This leave releases the claim and closes the terminals, which is
    exactly where the old code dropped the viewers' history while
    leaving their picture on screen.

    Kills: emptying the viewers when the dashboard disconnects.
    """
    _fnOpenTheEnvironmentsHub(pageDashboard, serverHub)
    _fnOpenTheProjectHubFromTheTile(pageDashboard)
    _fnOpenTheSeededProject(pageDashboard)
    _fnShowTwoFilesInViewerA(pageDashboard)
    _fnReturnToTheEnvironmentsHub(pageDashboard)
    _fnOpenTheProjectHubFromTheTile(pageDashboard)
    _fnOpenTheSeededProject(pageDashboard)
    _fnAssertViewerAKept(pageDashboard)
    assert pageDashboard.listPageErrors == []


@pytest.mark.falsification
def testLeavingAProjectForNoProjectEmptiesTheViewers(
    pageDashboard, serverHub,
):
    """Same environment, different project, entered with no project.

    Kills: entering the no-project dashboard without asking whose
    files the viewers hold -- the shipped leak.
    """
    _fnOpenTheEnvironmentsHub(pageDashboard, serverHub)
    _fnOpenTheProjectHubFromTheTile(pageDashboard)
    _fnOpenTheSeededProject(pageDashboard)
    _fnShowTwoFilesInViewerA(pageDashboard)
    _fnReturnToTheProjectHub(pageDashboard)
    _fnWorkInTheEnvironmentWithoutAProject(pageDashboard)
    _fnAssertViewerAEmpty(_fdictViewerAState(pageDashboard))
    assert pageDashboard.listPageErrors == []


@pytest.mark.falsification
def testOpeningAProjectAfterNoProjectEmptiesTheViewers(
    pageDashboard, serverHub,
):
    """Same environment, the reverse direction: into a project.

    Kills: opening a project's dashboard without asking whose files
    the viewers hold.
    """
    _fnOpenTheEnvironmentsHub(pageDashboard, serverHub)
    _fnOpenTheProjectHubFromTheTile(pageDashboard)
    _fnWorkInTheEnvironmentWithoutAProject(pageDashboard)
    _fnShowTwoFilesInViewerA(pageDashboard)
    _fnReturnToTheProjectHub(pageDashboard)
    _fnOpenTheSeededProject(pageDashboard)
    _fnAssertViewerAEmpty(_fdictViewerAState(pageDashboard))
    assert pageDashboard.listPageErrors == []


@pytest.mark.falsification
def testAnotherEnvironmentEmptiesTheViewers(pageDashboard, serverHub):
    """Two environments, both entered with no project.

    The project path is the same ("no project") on both sides, so only
    the environment tells the two apart.

    Kills: identifying the viewers' project by its path alone.
    """
    _fnOpenTheEnvironmentsHub(pageDashboard, serverHub)
    _fnOpenTheProjectHubFromTheTile(pageDashboard)
    _fnWorkInTheEnvironmentWithoutAProject(pageDashboard)
    _fnShowTwoFilesInViewerA(pageDashboard)
    _fnReturnToTheEnvironmentsHub(pageDashboard)
    pageDashboard.route(
        S_CONNECT_ROUTE_GLOB,
        lambda routeIntercepted: routeIntercepted.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps({
                "sContainerId": S_OTHER_CONTAINER_ID,
                "sWorkflowPath": None,
                "dictWorkflow": None,
                "sLeaseId": "",
                "sProjectMode": "container",
                "sWorkspaceRoot": "",
            }),
        ),
    )
    pageDashboard.evaluate(
        "async (sId) => { await VaibifyApp.fnEnterNoWorkflow(sId); }",
        S_OTHER_CONTAINER_ID,
    )
    _fnAssertViewerAEmpty(_fdictViewerAState(pageDashboard))
    assert pageDashboard.listPageErrors == []
