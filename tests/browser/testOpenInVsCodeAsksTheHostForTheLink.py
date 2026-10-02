"""View > Open in VS Code asks the host for the link and follows it.

The page used to build ``vscode://ms-vscode-remote.remote-containers/
attach?containerId=...`` itself. The installed Dev Containers extension
handles only ``/cloneInVolume`` on that authority, so the click did
nothing, and the page could not name the Docker daemon the container is
on. The host now builds the link; this proves the click reaches the
route, follows exactly what the route returned, and says so when the
route refuses. The link itself is exercised by ``tests/testVsCodeAttachLink.py``.

WHAT THIS DOES NOT COVER: that VS Code opens. That needs a real VS Code
with the Dev Containers extension and a daemon; it was not run here.
"""

import pytest

from tests.browser.conftest import (
    S_HOST_PROJECT_READY, fnOpenTheSeededHostWorkflow,
)


pytestmark = pytest.mark.browser

S_LINK = "vscode://vscode-remote/attached-container+7b7d/workspace"

_S_CAPTURE_REQUESTS_AND_FOLLOWED_LINKS = """(dictArguments) => {
    window.listRequestedPaths = [];
    window.listFollowedLinks = [];
    VaibifyApi.fdictGet = (sPath) => {
        window.listRequestedPaths.push(sPath);
        if (dictArguments.bRefuse) {
            return Promise.reject(new Error('refused by the host'));
        }
        return Promise.resolve({sUri: dictArguments.sUri});
    };
    HTMLAnchorElement.prototype.click = function () {
        window.listFollowedLinks.push(this.href);
    };
}"""


@pytest.fixture(autouse=True)
def fixtureDropClaimsBetweenTests(serverHub):
    yield
    from vaibify.config.containerLock import fnReleaseContainerLock
    dictContainerOwners = serverHub.app.state.dictContainerOwners
    for _sName, recordOwner in list(dictContainerOwners.items()):
        fileHandle = getattr(recordOwner, "fileHandleLock", None)
        if fileHandle is not None:
            try:
                fnReleaseContainerLock(fileHandle)
            except OSError:
                pass
    dictContainerOwners.clear()
    serverHub.app.state.dictSessionOwner.clear()


def _fnClickOpenInVsCode(page):
    page.click(".toolbar-menu:has(#btnVsCode) .toolbar-menu-trigger")
    page.wait_for_selector("#btnVsCode", state="visible", timeout=5000)
    page.click("#btnVsCode")
    page.wait_for_timeout(300)


@pytest.mark.falsification
def testTheButtonFollowsTheLinkTheHostBuilt(pageDashboard, serverHub):
    """Kills: building the link in the page, where the daemon is unknown."""
    fnOpenTheSeededHostWorkflow(pageDashboard, serverHub)
    pageDashboard.evaluate(
        _S_CAPTURE_REQUESTS_AND_FOLLOWED_LINKS,
        {"sUri": S_LINK, "bRefuse": False})
    _fnClickOpenInVsCode(pageDashboard)
    listPaths = [
        sPath for sPath in pageDashboard.evaluate("window.listRequestedPaths")
        if sPath.endswith("/vscode-link")
    ]
    assert listPaths == [
        f"/api/containers/{S_HOST_PROJECT_READY}/vscode-link"]
    assert pageDashboard.evaluate("window.listFollowedLinks") == [S_LINK]
    assert pageDashboard.listPageErrors == []


def testARefusedLinkIsReportedAndNothingIsFollowed(pageDashboard, serverHub):
    fnOpenTheSeededHostWorkflow(pageDashboard, serverHub)
    pageDashboard.evaluate(
        _S_CAPTURE_REQUESTS_AND_FOLLOWED_LINKS,
        {"sUri": S_LINK, "bRefuse": True})
    _fnClickOpenInVsCode(pageDashboard)
    assert pageDashboard.evaluate("window.listFollowedLinks") == []
    assert "refused by the host" in pageDashboard.inner_text("#toastContainer")
