"""Opening the project menu, switching, and Admin -> Projects show progress.

Each of the three waits on a round trip to the hub -- a search of the
container for projects, or saving one project and loading the next --
and each used to show nothing until that round trip finished. On a hub
that was slow to answer, a researcher clicked the project name and saw
no menu for many seconds, and switched projects with no sign the switch
had begun (2026-10-05). Each test HOLDS the hub's answer, asserts what
the page shows while it waits, then releases the answer and asserts the
page settles.
"""

import os

import pytest

from tests.browser.testSwitchingProjectsKeepsEachProjectsState import (
    S_SECOND_STEP_NAME,
    S_SECOND_WORKFLOW_NAME,
    _fsWriteSecondWorkflow,
)
from tests.browser.testLostClaimIsRecoverable import (  # noqa: F401
    fixtureDropClaimsBetweenJourneys,
)
from tests.browser.conftest import (
    S_HOST_PROJECT_READY,
    S_HOST_WORKFLOW_NAME,
    fnOpenTheSeededHostWorkflow,
)


pytestmark = pytest.mark.browser

_S_WORKFLOW_SEARCH_ROUTE = "**/api/workflows/*"
_S_CONNECT_ROUTE = "**/api/connect/**"


def _fnHoldRequests(pageDashboard, sRoute, listHeld, fbHold=None):
    """Hold matching requests in ``listHeld``; let the rest through."""
    def fnRoute(routeIntercepted):
        if fbHold is None or fbHold(routeIntercepted.request):
            listHeld.append(routeIntercepted)
            return
        routeIntercepted.continue_()
    pageDashboard.route(sRoute, fnRoute)


def _fnAwaitHeld(pageDashboard, listHeld):
    """Wait until the page has sent the request being held.

    ``wait_for_timeout`` is what lets Playwright dispatch the route
    handler that fills ``listHeld``; a bare Python sleep would not.
    """
    for _ in range(100):
        if listHeld:
            return
        pageDashboard.wait_for_timeout(100)
    raise AssertionError("the page never sent the request being held")


def _fnStartSwitchToSecond(pageDashboard):
    pageDashboard.click("#activeWorkflowName")
    pageDashboard.click(
        f'.workflow-dropdown-item[data-name="{S_SECOND_WORKFLOW_NAME}"]',
    )
    pageDashboard.wait_for_selector("#modalConfirm", timeout=10000)
    pageDashboard.click("#btnConfirmOk")


def _fbIsTheSecondProjectsConnect(request):
    return (request.method == "POST"
            and S_SECOND_WORKFLOW_NAME in request.url)


@pytest.mark.falsification
def test_the_project_menu_opens_before_the_list_arrives(
    pageDashboard, serverHub,
):
    """Kills: waiting for the container search before opening the menu."""
    sSecondPath = _fsWriteSecondWorkflow(serverHub)
    try:
        fnOpenTheSeededHostWorkflow(pageDashboard, serverHub)
        listHeld = []
        _fnHoldRequests(pageDashboard, _S_WORKFLOW_SEARCH_ROUTE, listHeld)

        pageDashboard.click("#activeWorkflowName")
        _fnAwaitHeld(pageDashboard, listHeld)
        elStatus = pageDashboard.locator(
            "#workflowDropdown.active .workflow-dropdown-status")
        assert elStatus.is_visible()
        assert elStatus.inner_text() == "Finding projects…"

        listHeld[0].continue_()
        pageDashboard.wait_for_selector(
            f'.workflow-dropdown-item[data-name="{S_SECOND_WORKFLOW_NAME}"]',
            timeout=10000,
        )
        assert pageDashboard.locator(
            "#workflowDropdown .workflow-dropdown-status").count() == 0
        assert pageDashboard.listPageErrors == []
    finally:
        pageDashboard.unroute_all(behavior="ignoreErrors")
        os.remove(sSecondPath)


@pytest.mark.falsification
def test_a_switch_names_the_project_it_is_opening(pageDashboard, serverHub):
    """Kills: leaving the toolbar silent between confirm and arrival."""
    sSecondPath = _fsWriteSecondWorkflow(serverHub)
    try:
        fnOpenTheSeededHostWorkflow(pageDashboard, serverHub)
        listHeld = []
        _fnHoldRequests(pageDashboard, _S_CONNECT_ROUTE, listHeld,
                        _fbIsTheSecondProjectsConnect)

        _fnStartSwitchToSecond(pageDashboard)
        _fnAwaitHeld(pageDashboard, listHeld)
        elName = pageDashboard.locator("#activeWorkflowName.switching")
        assert elName.is_visible()
        assert elName.inner_text() == (
            "Opening " + S_SECOND_WORKFLOW_NAME + "…")
        pageDashboard.click("#activeWorkflowName")
        assert pageDashboard.locator(
            "#workflowDropdown.active").count() == 0, (
            "a second switch could be started over the first"
        )

        listHeld[0].continue_()
        pageDashboard.wait_for_selector(
            f"text={S_SECOND_STEP_NAME}", timeout=20000,
        )
        assert pageDashboard.locator(
            "#activeWorkflowName.switching").count() == 0
        assert pageDashboard.inner_text("#activeWorkflowName") == (
            S_SECOND_WORKFLOW_NAME)
        assert pageDashboard.listPageErrors == []
    finally:
        pageDashboard.unroute_all(behavior="ignoreErrors")
        os.remove(sSecondPath)


@pytest.mark.falsification
def test_a_failed_switch_puts_the_open_projects_name_back(
    pageDashboard, serverHub,
):
    """Kills: leaving "Opening ..." on the toolbar after a refused switch."""
    sSecondPath = _fsWriteSecondWorkflow(serverHub)
    try:
        fnOpenTheSeededHostWorkflow(pageDashboard, serverHub)
        sOpenName = pageDashboard.inner_text("#activeWorkflowName")
        listHeld = []
        _fnHoldRequests(pageDashboard, _S_CONNECT_ROUTE, listHeld,
                        _fbIsTheSecondProjectsConnect)

        _fnStartSwitchToSecond(pageDashboard)
        _fnAwaitHeld(pageDashboard, listHeld)
        listHeld[0].fulfill(
            status=500, content_type="application/json",
            body='{"detail": "The project could not be loaded."}',
        )
        pageDashboard.wait_for_function(
            "() => !document.getElementById('activeWorkflowName')"
            ".classList.contains('switching')", timeout=10000,
        )
        assert pageDashboard.inner_text("#activeWorkflowName") == sOpenName
        assert pageDashboard.listPageErrors == []
    finally:
        pageDashboard.unroute_all(behavior="ignoreErrors")
        os.remove(sSecondPath)


@pytest.mark.falsification
def test_admin_projects_shows_the_list_screen_while_it_searches(
    pageDashboard, serverHub,
):
    """Kills: searching the container before leaving the dashboard."""
    sSecondPath = _fsWriteSecondWorkflow(serverHub)
    try:
        fnOpenTheSeededHostWorkflow(pageDashboard, serverHub)
        listHeld = []
        _fnHoldRequests(pageDashboard, _S_WORKFLOW_SEARCH_ROUTE, listHeld)

        pageDashboard.evaluate(
            "() => document.getElementById('btnAdminWorkflows').click()")
        pageDashboard.wait_for_selector("#modalConfirm", timeout=10000)
        pageDashboard.click("#btnConfirmOk")
        _fnAwaitHeld(pageDashboard, listHeld)
        assert pageDashboard.locator("#workflowPicker").is_visible()
        elStatus = pageDashboard.locator(
            "#listWorkflows .workflow-loading-banner")
        assert elStatus.inner_text() == "Finding projects…"

        for routeHeld in listHeld:
            routeHeld.continue_()
        pageDashboard.unroute_all(behavior="ignoreErrors")
        pageDashboard.wait_for_selector(
            f"#listWorkflows >> text={S_SECOND_WORKFLOW_NAME}",
            timeout=10000,
        )
        assert pageDashboard.locator(
            "#listWorkflows .workflow-loading-banner").count() == 0
        assert pageDashboard.listPageErrors == []
    finally:
        pageDashboard.unroute_all(behavior="ignoreErrors")
        os.remove(sSecondPath)


@pytest.mark.falsification
def test_a_late_failed_search_never_replaces_a_list_already_shown(
    pageDashboard, serverHub,
):
    """Kills: the first search's failure overwriting a list drawn since.

    The list screen now appears before its first search answers, so the
    screen's own three-second refresh can draw the list first. A first
    search that then FAILS -- a claim reaped while it was in flight is
    one way -- used to replace those cards with its failure line, and a
    researcher's click on a project landed on nothing (found by the
    Firefox lane, 2026-10-05).
    """
    listHeld = []
    listSearchesSeen = []

    def fnHoldTheConnectSearch(routeIntercepted):
        # The screen's refresh poll fires its first search as the screen
        # appears, before the connect asks; the connect's is the second.
        listSearchesSeen.append(routeIntercepted.request.url)
        if len(listSearchesSeen) == 2:
            listHeld.append(routeIntercepted)
            return
        routeIntercepted.continue_()

    pageDashboard.route(_S_WORKFLOW_SEARCH_ROUTE, fnHoldTheConnectSearch)
    try:
        pageDashboard.goto(serverHub.fsBootstrapUrl(), wait_until="load")
        pageDashboard.click(
            f'.container-tile[data-name="{S_HOST_PROJECT_READY}"] '
            '.container-tile-main', timeout=15000,
        )
        pageDashboard.wait_for_selector("#modalConfirm", timeout=10000)
        pageDashboard.click("#btnConfirmOk")
        _fnAwaitHeld(pageDashboard, listHeld)
        pageDashboard.wait_for_selector(
            f"#listWorkflows >> text={S_HOST_WORKFLOW_NAME}", timeout=20000,
        )

        with pageDashboard.expect_response(
            lambda response: response.status == 500, timeout=10000,
        ):
            listHeld[0].fulfill(
                status=500, content_type="application/json",
                body='{"detail": "Search failed."}',
            )
        # The failure is handled within the same few event-loop turns
        # that deliver it; the overwrite this guards against happened
        # within milliseconds. A superseded failure leaves no trace to
        # wait on, by design.
        pageDashboard.wait_for_timeout(500)

        assert pageDashboard.locator(
            f"#listWorkflows >> text={S_HOST_WORKFLOW_NAME}").is_visible()
        assert "could not be loaded" not in pageDashboard.inner_text(
            "#listWorkflows")
    finally:
        pageDashboard.unroute_all(behavior="ignoreErrors")
