"""The "Make this a Project" bar appears exactly where adoption can succeed.

Adoption hands a bare repository NAME to the tracking sidecar, which
resolves it against the resource's own repository root. Only a
directory sitting DIRECTLY under that root can therefore become a
project repository: the workspace root itself is a Docker-managed
volume and never a repository, and anything deeper would nest inside
one. The backend refuses both, so the bar must not offer them -- a
button that produces a refusal is how the researcher learns to
distrust the panel.

What this file proves is frontend-only, deliberately. Driving the real
POST would need ``git init``, ``git commit``, ``mkdir -p`` and the
sidecar write added to the fake adapter's modelled commands, each
owing a Lane 2 assertion; adoption's BEHAVIOR is asked of a real
container in ``testContainerAcceptance.py`` instead, which is the
division the lane contract sets. Here: where the bar shows, where it
does not, and that an unnamed project is refused in the panel before a
request is ever issued.
"""

import json

import pytest


pytestmark = pytest.mark.browser

S_BAR_SELECTOR = "#fileAdoptProjectBar"
S_CONNECT_ROUTE_GLOB = "**/api/connect/*"
S_FILES_ROUTE_GLOB = "**/api/files/**"
S_ADOPT_ROUTE_GLOB = "**/api/workflows/*/adopt-directory"

S_WORKSPACE_ROOT = "/workspace"
S_SANDBOX_DIRECTORY = "analysisSandbox"


def _fnOpenFilesPanel(page, serverHub, sProjectMode="container"):
    """Load the dashboard in the given project mode and open Files.

    Panels are shown by the ``active`` class the tab handler toggles,
    so the panel is opened the way the tab does it rather than by
    poking inline display -- which would report a visible element the
    real dashboard would still have hidden.
    """
    page.goto(serverHub.fsBootstrapUrl(), wait_until="load")
    page.wait_for_selector(".container-tile", timeout=10000)
    page.route(
        S_CONNECT_ROUTE_GLOB,
        lambda routeIntercepted: routeIntercepted.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps({
                "sContainerId": "fake-resource",
                "sWorkflowPath": None,
                "dictWorkflow": None,
                "sLeaseId": "",
                "sProjectMode": sProjectMode,
                "sWorkspaceRoot": S_WORKSPACE_ROOT,
            }),
        ),
    )
    page.route(
        S_FILES_ROUTE_GLOB,
        lambda routeIntercepted: routeIntercepted.fulfill(
            status=200, content_type="application/json", body="[]",
        ),
    )
    page.evaluate(
        "async () => { await VaibifyApp.fnEnterNoWorkflow('fake-resource'); }",
    )
    page.evaluate(
        """() => {
            ['panelSteps', 'panelLogs'].forEach((sId) => {
                const el = document.getElementById(sId);
                if (el) el.classList.remove('active');
            });
            const elFiles = document.getElementById('panelFiles');
            if (elFiles) elFiles.classList.add('active');
        }"""
    )


def _fnBrowseTo(page, sPath):
    """Navigate the Files panel to sPath through the product's own loader."""
    page.evaluate(
        "async (sPath) => { await VaibifyFiles.fnLoadDirectory(sPath); }",
        sPath,
    )


def testTheBarOffersATopLevelWorkspaceDirectory(pageDashboard, serverHub):
    """A sandbox directory an agent created is offered for adoption.

    This is the affordance whose absence cost a researcher the whole
    journey: the directories appeared in this very panel, and nothing
    anywhere offered to make them a Project.
    """
    _fnOpenFilesPanel(pageDashboard, serverHub)
    _fnBrowseTo(pageDashboard, S_WORKSPACE_ROOT + "/" + S_SANDBOX_DIRECTORY)
    elBar = pageDashboard.locator(S_BAR_SELECTOR)
    assert elBar.count() == 1, "the adopt bar is not in the page"
    assert elBar.is_visible(), (
        "browsing a top-level workspace directory offers no way to "
        "make it a Project"
    )
    assert pageDashboard.listPageErrors == []


def testTheBarIsHiddenAtTheWorkspaceRoot(pageDashboard, serverHub):
    """The workspace root is a Docker volume and never a repository.

    Offering it would produce a refusal from the backend, and a button
    whose only outcome is a refusal teaches the researcher to ignore
    the panel.
    """
    _fnOpenFilesPanel(pageDashboard, serverHub)
    _fnBrowseTo(pageDashboard, S_WORKSPACE_ROOT)
    assert not pageDashboard.locator(S_BAR_SELECTOR).is_visible(), (
        "the workspace root is offered for adoption, which the backend "
        "refuses"
    )
    assert pageDashboard.listPageErrors == []


def testTheBarIsHiddenForANestedDirectory(pageDashboard, serverHub):
    """A directory two levels down would nest inside another repository.

    Adopting it would make the OUTER directory the project repository
    and resolve every declared path from a root the researcher never
    named, which the backend refuses by code.
    """
    _fnOpenFilesPanel(pageDashboard, serverHub)
    _fnBrowseTo(
        pageDashboard,
        S_WORKSPACE_ROOT + "/" + S_SANDBOX_DIRECTORY + "/subdirectory",
    )
    assert not pageDashboard.locator(S_BAR_SELECTOR).is_visible(), (
        "a nested directory is offered for adoption, which would "
        "repoint the project root"
    )
    assert pageDashboard.listPageErrors == []


def testTheBarIsHiddenInHostMode(pageDashboard, serverHub):
    """A host sandbox is promoted through the convert bar, not this one.

    Two doors to one outcome is the shape that produced the original
    confusion, where promotion existed but only for a mode the
    researcher was not in.
    """
    _fnOpenFilesPanel(pageDashboard, serverHub, sProjectMode="host")
    _fnBrowseTo(pageDashboard, S_WORKSPACE_ROOT + "/" + S_SANDBOX_DIRECTORY)
    assert not pageDashboard.locator(S_BAR_SELECTOR).is_visible(), (
        "host mode offers both the convert bar and the adopt bar"
    )
    assert pageDashboard.listPageErrors == []


def testAnUnnamedProjectIsRefusedWithoutARequest(pageDashboard, serverHub):
    """The name is required, and the panel says so before any request.

    The name is what the Project field renders. Sending an empty one
    would earn a 400 whose remedy is "give it a name" -- correct, but a
    round trip to learn what the panel already knows.
    """
    _fnOpenFilesPanel(pageDashboard, serverHub)
    _fnBrowseTo(pageDashboard, S_WORKSPACE_ROOT + "/" + S_SANDBOX_DIRECTORY)
    listRequests = []
    pageDashboard.route(
        S_ADOPT_ROUTE_GLOB,
        lambda routeIntercepted: (
            listRequests.append(routeIntercepted.request.url),
            routeIntercepted.fulfill(
                status=200, content_type="application/json", body="{}",
            ),
        )[-1],
    )
    pageDashboard.fill("#inputAdoptProjectName", "   ")
    pageDashboard.click("#btnAdoptProject")
    elOutcome = pageDashboard.locator("#adoptProjectOutcome")
    elOutcome.wait_for(state="visible", timeout=5000)
    assert "name" in elOutcome.inner_text().lower(), (
        f"the panel does not say a name is needed: "
        f"{elOutcome.inner_text()!r}"
    )
    assert listRequests == [], (
        f"an unnamed adoption was sent to the server anyway: "
        f"{listRequests!r}"
    )
    assert pageDashboard.listPageErrors == []
