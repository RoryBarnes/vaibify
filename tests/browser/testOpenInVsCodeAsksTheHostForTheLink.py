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


# Unlike the capture above, the anchor's click is NOT replaced: the
# link is really followed, which is the navigation that fires
# `beforeunload`. The listener is added after the dashboard's own guard,
# so it sees whether that guard cancelled the event.
_S_FOLLOW_THE_REAL_LINK_AND_WATCH_THE_UNLOAD_GUARD = """(sUri) => {
    VaibifyApi.fdictGet = () => Promise.resolve({sUri: sUri});
    window.listUnloadWasCancelled = [];
    window.addEventListener('beforeunload', (event) => {
        window.listUnloadWasCancelled.push(event.defaultPrevented);
    });
}"""

_S_ASK_WHETHER_THE_GUARD_CANCELS_AN_UNLOAD = """() => {
    const event = new Event('beforeunload', {cancelable: true});
    return !window.dispatchEvent(event);
}"""

# One second longer than the dashboard's grace window, so the guard has
# had time to arm itself again.
_I_PAST_THE_GRACE_WINDOW_MILLISECONDS = 4000


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


def _flistOpenInVsCodeFollowingTheRealLink(pageDashboard, serverHub):
    """Click Open in VS Code with the real anchor; return the dialogs raised."""
    fnOpenTheSeededHostWorkflow(pageDashboard, serverHub)
    pageDashboard.evaluate(
        _S_FOLLOW_THE_REAL_LINK_AND_WATCH_THE_UNLOAD_GUARD, S_LINK)
    listDialogTypes = []
    pageDashboard.on(
        "dialog",
        lambda dialog: (listDialogTypes.append(dialog.type), dialog.dismiss()),
    )
    _fnClickOpenInVsCode(pageDashboard)
    return listDialogTypes


@pytest.mark.falsification
def testOpeningVsCodeDoesNotLetTheUnloadGuardCancelTheLaunch(
    pageDashboard, serverHub,
):
    """Answering "Stay" to a leave-page prompt cancelled the launch.

    Following a ``vscode://`` link is a navigation, so the browser
    fires ``beforeunload`` first, and the dashboard's guard cancelled
    every unload. Firefox then asked the researcher whether to leave the
    page, and "Stay" abandoned the launch (reported 2026-10-04). The
    page is never unloaded, so the guard has nothing to protect here.

    Two oracles, because engines differ in whether they fire the event
    for an external link at all: the real event, where one fires, must
    not be cancelled; and an unload attempted during the launch window
    must not be cancelled in ANY engine. The older test above replaces
    the anchor's ``click``, so it never navigated and could not see this.

    Kills: removing the guard's stand-down around the launch, which
    puts the leave-page prompt back in front of the link.
    """
    listDialogTypes = _flistOpenInVsCodeFollowingTheRealLink(
        pageDashboard, serverHub)

    assert True not in pageDashboard.evaluate("window.listUnloadWasCancelled")
    assert listDialogTypes == []
    assert pageDashboard.evaluate(
        _S_ASK_WHETHER_THE_GUARD_CANCELS_AN_UNLOAD) is False
    assert pageDashboard.listPageErrors == []


@pytest.mark.falsification
def testTheUnloadGuardIsArmedAgainOnceTheLaunchHasHadItsChance(
    pageDashboard, serverHub,
):
    """The stand-down is a window, not the removal of the protection.

    The guard exists so a researcher does not close the dashboard in the
    middle of a run. Letting a launch past it must not leave it off.

    Kills: suspending the guard for good, or making the window so long
    that the protection is effectively gone.
    """
    _flistOpenInVsCodeFollowingTheRealLink(pageDashboard, serverHub)
    pageDashboard.wait_for_timeout(_I_PAST_THE_GRACE_WINDOW_MILLISECONDS)

    assert pageDashboard.evaluate(
        _S_ASK_WHETHER_THE_GUARD_CANCELS_AN_UNLOAD) is True
