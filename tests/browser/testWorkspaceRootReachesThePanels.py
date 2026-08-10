"""The root the server sent is the root the panels actually request.

Storing the answer is not the same as using it. The failure this
catches is the ordinary one for a value threaded through a dashboard:
the handshake carries it, a getter returns it, and the panel that
needed it goes on writing the constant — green everywhere, and the
host researcher's file browser still opens a directory that exists on
nobody's machine.

So the assertion is on the REQUEST. The root is applied through the
real applier, the real file panel is asked to load its default
directory, and the URL it produces is read off the wire.
"""

import json

import pytest


pytestmark = pytest.mark.browser

S_FILES_ROUTE_GLOB = "**/api/files/**"
S_HOST_ROOT = "/Users/somebody/science/myProject"


S_CONNECT_ROUTE_GLOB = "**/api/connect/*"


def _fsCapturedFilesRequestPath(page, serverHub, sWorkspaceRoot):
    """Return the URLs the file panel requests for its default view.

    The root is delivered the way it is delivered in production — on
    the connect handshake, through the real entry function — rather
    than poked into the applier, so what is proven includes the
    handshake field actually being read.
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
                "sProjectMode": "host" if sWorkspaceRoot else "container",
                "sWorkspaceRoot": sWorkspaceRoot,
            }),
        ),
    )
    listRequested = []
    page.route(
        S_FILES_ROUTE_GLOB,
        lambda routeIntercepted: (
            listRequested.append(routeIntercepted.request.url),
            routeIntercepted.fulfill(
                status=200,
                content_type="application/json",
                body="[]",
            ),
        )[-1],
    )
    page.evaluate(
        "async () => { await VaibifyApp.fnEnterNoWorkflow('fake-resource'); }",
    )
    page.evaluate(
        "async () => { await VaibifyFiles.fnLoadDirectory(); }",
    )
    return listRequested


def testTheFilePanelBrowsesTheRootTheServerSent(pageDashboard, serverHub):
    """A host project's file panel opens its own directory.

    Kills: keeping the ``/workspace`` constant in the file panel's
    default, which browses a path a host machine does not have and
    reports the project empty.
    """
    listRequested = _fsCapturedFilesRequestPath(
        pageDashboard, serverHub, S_HOST_ROOT,
    )
    assert listRequested, "the file panel issued no request at all"
    assert S_HOST_ROOT in listRequested[-1], (
        f"the file panel ignored the root the server sent: {listRequested}"
    )
    assert pageDashboard.listPageErrors == []


def testTheContainerRootStillReachesThePanel(pageDashboard, serverHub):
    """The other direction: a container project browses /workspace.

    Kills: hard-coding a host-shaped root, or dropping the fallback
    that keeps an older server — one that sends no root at all —
    behaving exactly as it did.
    """
    listRequested = _fsCapturedFilesRequestPath(
        pageDashboard, serverHub, "",
    )
    assert listRequested, "the file panel issued no request at all"
    assert "/workspace" in listRequested[-1], (
        f"a container project stopped browsing /workspace: {listRequested}"
    )
    assert pageDashboard.listPageErrors == []
