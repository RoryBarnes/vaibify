"""Five dashboard edges that failed silently or answered the wrong question.

* a path containing "#" or "?" listed a different directory;
* a repository poll that outlived a container switch painted the old
  container's answer onto the new one;
* a socket still connecting threw on an interactive frame, losing it;
* Push was a dead click when its pre-check failed;
* the reproduction report links answered 401 in the new tab, because the
  hub's credential is a header an anchor cannot carry.

Kills (the mutation applied, the test run, the named assertion observed
to fail) are recorded beside each test.
"""

import json

import pytest

from tests.browser.conftest import fnOpenTheSeededHostWorkflow

pytestmark = pytest.mark.browser


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


@pytest.mark.falsification
def testAPathWithAFragmentOrQueryCharacterListsThatDirectory(
    pageDashboard, serverHub,
):
    """Kills: building the listing URL from the raw, unencoded path."""
    fnOpenTheSeededHostWorkflow(pageDashboard, serverHub)
    listUrls = []
    pageDashboard.on("request", lambda request: listUrls.append(request.url))
    pageDashboard.evaluate(
        "VaibifyFiles.fnLoadDirectory('/workspace/odd#name?x=1')")
    pageDashboard.wait_for_timeout(500)
    listListing = [sUrl for sUrl in listUrls if "/api/files/" in sUrl
                   and "odd" in sUrl]
    assert listListing, listUrls
    assert all("odd%23name%3Fx%3D1" in sUrl for sUrl in listListing), (
        listListing)


_S_SWITCH_CONTAINER_DURING_POLL = """async () => {
    const fdictGetReal = VaibifyApi.fdictGet;
    const fsGetContainerReal = VaibifyApp.fsGetContainerId;
    let fnRelease;
    VaibifyApi.fdictGet = () => new Promise(
        (fnResolve) => { fnRelease = fnResolve; });
    const listApplied = [];
    VaibifyPolling.fnSetReposHandler((dictStatus) => listApplied.push(
        dictStatus));
    try {
        VaibifyPolling.fnStartReposPolling('old-container');
        VaibifyApp.fsGetContainerId = () => 'new-container';
        fnRelease({sMarker: 'the old container answer'});
        await new Promise((fnDone) => setTimeout(fnDone, 200));
    } finally {
        VaibifyPolling.fnStopReposPolling();
        VaibifyApi.fdictGet = fdictGetReal;
        VaibifyApp.fsGetContainerId = fsGetContainerReal;
    }
    return listApplied;
}"""


@pytest.mark.falsification
def testARepositoryAnswerForAnotherContainerIsNotApplied(
    pageDashboard, serverHub,
):
    """Kills: applying a poll response after the open container changed."""
    fnOpenTheSeededHostWorkflow(pageDashboard, serverHub)
    listApplied = pageDashboard.evaluate(_S_SWITCH_CONTAINER_DURING_POLL)
    assert listApplied == []


_S_SEND_WHILE_CONNECTING = """async () => {
    const WebSocketReal = window.WebSocket;
    const listSent = [];
    let socketFake = null;
    window.WebSocket = class {
        constructor() {
            this.readyState = 0;
            socketFake = this;
        }
        send(sMessage) {
            if (this.readyState !== 1) {
                throw new Error('InvalidStateError: still CONNECTING');
            }
            listSent.push(sMessage);
        }
        close() {}
    };
    window.WebSocket.OPEN = 1;
    window.WebSocket.CONNECTING = 0;
    let sThrown = '';
    try {
        VaibifyWebSocket.fnDisconnect();
        VaibifyWebSocket.fnConnect('probe-container', 'probe-token');
        try {
            VaibifyWebSocket.fnSendDirect(
                {sAction: 'interactiveComplete', iExitCode: 0});
        } catch (error) { sThrown = String(error); }
        socketFake.readyState = 1;
        socketFake.onopen();
    } finally {
        VaibifyWebSocket.fnDisconnect();
        window.WebSocket = WebSocketReal;
    }
    return {sThrown: sThrown, listSent: listSent};
}"""


@pytest.mark.falsification
def testAnInteractiveFrameSentWhileConnectingIsDeliveredOnOpen(
    pageDashboard, serverHub,
):
    """Kills: sending straight to a socket that is still connecting."""
    fnOpenTheSeededHostWorkflow(pageDashboard, serverHub)
    dictOutcome = pageDashboard.evaluate(_S_SEND_WHILE_CONNECTING)
    assert dictOutcome["sThrown"] == ""
    assert [json.loads(sFrame) for sFrame in dictOutcome["listSent"]] == [
        {"sAction": "interactiveComplete", "iExitCode": 0}]


@pytest.mark.falsification
def testPushTellsTheResearcherWhenItsPreCheckFails(pageDashboard, serverHub):
    """Kills: letting the pre-check rejection escape with no toast."""
    fnOpenTheSeededHostWorkflow(pageDashboard, serverHub)
    pageDashboard.route(
        "**/api/sync/*/check/github",
        lambda route: route.fulfill(
            status=500, content_type="application/json",
            body=json.dumps({"detail": "the remote check failed"})))
    pageDashboard.evaluate("VaibifySyncManager.fnOpenPushModal('github')")
    pageDashboard.wait_for_selector(".toast.error", timeout=10000)
    sToast = pageDashboard.locator(".toast.error").first.inner_text()
    assert "GitHub" in sToast and "could not be opened" in sToast
    assert pageDashboard.listPageErrors == []
