"""A lost claim is recovered wherever the researcher meets it.

A claim with no socket lives on a thirty-second presence window, and a
browser throttles an unseen page for longer than that, so a researcher
who returns to an open project finds the hub no longer holds the claim
for it. The hub now says so in a code (``claim-required``), and the
page does the one thing that cures it -- claim again -- in one place,
for every request, instead of in four.

What these journeys pin, each against the real hub and a real browser:

* the cure happens on the dashboard and on the Project Hub, once however
  many pollers are refused at once, and a reconnect follows it;
* it is NEVER applied to a refusal that is not the page's to cure (a
  project another session holds), to an answer that arrives after the
  researcher left the view it was asked from, or twice for one request;
* a recovery's reconnect can never become the hub's last word after the
  researcher chose another project;
* leaving for the Environments page releases the claim FIRST, lets the
  hub's own close of the page's sockets pass as the expected thing it
  is, and lets the release's OUTCOME decide whether to go;
* a session the hub really ended says so once;
* the Project Hub names the environment it lists.

The claim is dropped here the way the reaper drops one (the owner
record is removed from the live hub), so the real refusal is driven on
the real routes without a timing test.
"""

import json
import os
import time
import urllib.request

import pytest

from tests.browser.conftest import (
    S_HOST_PROJECT_READY,
    S_HOST_STEP_NAME,
    S_HOST_WORKFLOW_NAME,
    fnOpenTheSeededHostWorkflow,
)
from tests.browser.fakeDockerAdapter import (
    S_CONTAINER_ID,
    S_CONTAINER_NAME,
)
from vaibify.gui.actionCatalog import S_SESSION_ENV_PATH
from tests.browser.testLostClaimIsRecoverable import (  # noqa: F401
    _fnHandTheClaimToAnotherSession,
    _fnReachTheWorkflowPicker,
    _fnTakeTheClaimAway,
    fixtureDropClaimsBetweenJourneys,
)
from tests.browser.testProjectSwitchShowsItsProgress import (
    _fnAwaitHeld,
    _fnHoldRequests,
    _fnStartSwitchToSecond,
)
from tests.browser.testSwitchingProjectsKeepsEachProjectsState import (
    S_SECOND_STEP_NAME,
    S_SECOND_WORKFLOW_NAME,
    _fsWriteSecondWorkflow,
)


pytestmark = pytest.mark.browser

S_CLAIM_URL_SUFFIX = f"/api/registry/{S_HOST_PROJECT_READY}/claim"
S_RELEASE_ROUTE = "**/api/registry/*/release"
S_PIPELINE_SOCKET_ROUTE = "**/ws/pipeline/**"
S_CLAIM_REQUIRED_BODY = json.dumps({"detail": {
    "sMessage": "This project is no longer claimed by this session. "
                "Select it again to claim it.",
    "sRefusal": "claim-required",
}})
F_SHORT_RECONNECT_WINDOW_SECONDS = 3.0
S_BUSY_SENTENCE = (
    "A pipeline run is live in this container; stop it or wait for it "
    "to finish before releasing."
)


def _flistRecordRequests(pageDashboard):
    """Return a list that fills with ``(method, url)`` for every request."""
    listRequests = []
    pageDashboard.on("request", lambda request: listRequests.append(
        (request.method, request.url)))
    return listRequests


def _flistRecordUnauthorizedAnswers(pageDashboard):
    """Return a list that fills with the URL of every 401 the page got."""
    listUnauthorized = []
    pageDashboard.on("response", lambda response: (
        listUnauthorized.append(response.url)
        if response.status == 401 else None))
    return listUnauthorized


def _flistClaimPosts(listRequests):
    return [
        sUrl for sMethod, sUrl in listRequests
        if sMethod == "POST" and sUrl.endswith(S_CLAIM_URL_SUFFIX)
    ]


def _fsToastText(pageDashboard):
    return pageDashboard.text_content("#toastContainer") or ""


def _fsPageSessionId(pageDashboard, serverHub):
    from vaibify.gui import browserSession
    sCredential = pageDashboard.evaluate(
        "() => window.sessionStorage.getItem('vaibifySessionCredential')")
    return browserSession.fsSessionIdForCredential(
        serverHub.app.state.dictBrowserSessions, sCredential)


def _fnAwaitOwnerOfThisPage(pageDashboard, serverHub, fTimeoutSeconds=25.0):
    """Wait until the hub's owner record is bound to this page's session."""
    sSessionId = _fsPageSessionId(pageDashboard, serverHub)
    fDeadline = time.monotonic() + fTimeoutSeconds
    while time.monotonic() < fDeadline:
        recordOwner = serverHub.app.state.dictContainerOwners.get(
            S_HOST_PROJECT_READY)
        if recordOwner is not None and (
                recordOwner.sBrowserSessionId == sSessionId):
            return recordOwner
        pageDashboard.wait_for_timeout(200)
    raise AssertionError(
        "the page never took its claim on the project again")


def _fnOpenTheDashboardWithItsSocket(pageDashboard, serverHub):
    """Open the seeded project and dial its pipeline socket for real."""
    fnOpenTheSeededHostWorkflow(pageDashboard, serverHub)
    pageDashboard.evaluate(
        "() => VaibifyPipelineRunner.fnConnectPipelineWebSocket()")
    pageDashboard.wait_for_function(
        "() => VaibifyWebSocket.fbIsOpen()", timeout=15000)


def _fnProbeWithoutWaiting(pageDashboard, sUrl, sKey):
    """Send one request through VaibifyApi and keep its outcome on window."""
    pageDashboard.evaluate(
        """([sUrl, sKey]) => {
            window.__dictProbes = window.__dictProbes || {};
            window.__dictProbes[sKey] = {bSettled: false};
            VaibifyApi.fdictGet(sUrl).then(
                () => { window.__dictProbes[sKey] =
                    {bSettled: true, bOk: true}; },
                (error) => { window.__dictProbes[sKey] =
                    {bSettled: true, bOk: false,
                     bHandled: !!error.bHandledByRecovery}; });
        }""", [sUrl, sKey])


def _fdictAwaitProbe(pageDashboard, sKey):
    pageDashboard.wait_for_function(
        "(sKey) => window.__dictProbes[sKey].bSettled", arg=sKey,
        timeout=20000)
    return pageDashboard.evaluate(
        "(sKey) => window.__dictProbes[sKey]", sKey)


def _fnStopTheDashboardPollers(pageDashboard):
    """Stop every poller so the one request a test sends is the only one."""
    pageDashboard.evaluate("""() => {
        Object.keys(VaibifyPolling).forEach(function (sName) {
            if (sName.startsWith('fnStop')) VaibifyPolling[sName]();
        });
    }""")


def _fnClickEnvironments(pageDashboard):
    pageDashboard.evaluate(
        "() => document.getElementById('btnAdminContainers').click()")
    pageDashboard.wait_for_selector("#modalConfirm", timeout=10000)
    pageDashboard.click("#btnConfirmOk")


def _fnHoldTheHubBusy(monkeypatch):
    """Make the hub refuse a release the way a live run makes it."""
    from vaibify.gui import sessionLifecycle
    dictBusy = {"sSentence": S_BUSY_SENTENCE}
    monkeypatch.setattr(
        sessionLifecycle, "_fsReleaseBusyReason",
        lambda appState, sName, bForce: dictBusy["sSentence"])
    return dictBusy


def _fnShortenTheReconnectWindow(monkeypatch):
    from vaibify.gui import sessionLifecycle
    monkeypatch.setattr(
        sessionLifecycle, "F_RECONNECT_WINDOW_SECONDS",
        F_SHORT_RECONNECT_WINDOW_SECONDS)


def _fnAwaitLanding(pageDashboard):
    pageDashboard.wait_for_selector(
        "#containerLanding", state="visible", timeout=20000)


def _fnRouteThePipelineSocket(pageDashboard):
    """Pass the pipeline socket through to the real hub, holding handles.

    ``connect_to_server`` keeps this honest: every byte still reaches
    the real hub. The page-side close handler is installed so a close the
    page asks for is NOT forwarded, which lets a test deliver the hub's
    own close frame at the moment of its choosing.
    """
    listRoutes = []

    def _fnHandle(routeSocket):
        serverSocket = routeSocket.connect_to_server()
        routeSocket.on_message(lambda sMessage: serverSocket.send(sMessage))
        serverSocket.on_message(lambda sMessage: routeSocket.send(sMessage))
        routeSocket.on_close(lambda iCode, sReason: None)
        serverSocket.on_close(lambda iCode, sReason: None)
        listRoutes.append(routeSocket)

    pageDashboard.route_web_socket(S_PIPELINE_SOCKET_ROUTE, _fnHandle)
    return listRoutes


def _fnInstallAnotherSessionsClaim(serverHub):
    """Have a SECOND browser session claim the project, over real HTTP."""
    from vaibify.gui import browserSession
    dictStore = serverHub.app.state.dictBrowserSessions
    sCapability = browserSession.fsMintBootstrapCapability(dictStore)
    _sSessionId, sCredential = browserSession.ftRedeemCapability(
        dictStore, sCapability)
    requestClaim = urllib.request.Request(
        serverHub.sBaseUrl + S_CLAIM_URL_SUFFIX, data=b"{}",
        method="POST", headers={
            "X-Session-Token": sCredential,
            "Content-Type": "application/json",
        })
    with urllib.request.urlopen(requestClaim, timeout=10) as responseClaim:
        assert responseClaim.status == 200


# ---------------------------------------------------------------------
# Recovery, wherever the lost claim is met
# ---------------------------------------------------------------------


@pytest.mark.falsification
def test_a_lost_claim_on_the_open_dashboard_is_taken_again_by_the_next_poll(
    pageDashboard, serverHub,
):
    """The page's own poll meets the refusal; the claim and project return.

    Kills: removing the recovery from the one place every request
    passes through (VaibifyApi), which restores a dashboard whose every
    poll fails with "You do not hold this container's lease" until the
    researcher walks back to the tile.
    """
    fnOpenTheSeededHostWorkflow(pageDashboard, serverHub)
    listRequests = _flistRecordRequests(pageDashboard)
    _fnTakeTheClaimAway(serverHub)
    _fnAwaitOwnerOfThisPage(pageDashboard, serverHub)
    pageDashboard.wait_for_timeout(1500)
    assert "do not hold" not in _fsToastText(pageDashboard).lower()
    listClaims = _flistClaimPosts(listRequests)
    assert listClaims, "the lapsed claim was never claimed again"
    iFirstClaim = listRequests.index(("POST", listClaims[0]))
    listReconnects = [
        iIndex for iIndex, (sMethod, sUrl) in enumerate(listRequests)
        if sMethod == "POST" and "/api/connect/" in sUrl
        and "sWorkflowPath=" in sUrl and iIndex > iFirstClaim
    ]
    assert listReconnects, (
        "the open project was not connected again after the claim, so "
        "the container's agent would keep a token the new claim retired"
    )
    assert pageDashboard.locator(f"text={S_HOST_STEP_NAME}").first.is_visible()
    assert pageDashboard.listPageErrors == []


@pytest.mark.falsification
def test_a_lost_claim_on_the_project_hub_is_taken_again_by_a_refresh(
    pageDashboard, serverHub,
):
    """The Project Hub's list refresh recovers, and the cards click.

    Kills: a list request the recovery cannot see -- here, the page
    forgetting which container it selected, so the refusal names no
    container the recovery may claim.
    """
    _fnReachTheWorkflowPicker(pageDashboard, serverHub)
    _fnTakeTheClaimAway(serverHub)
    pageDashboard.click("#btnRefreshWorkflows")
    _fnAwaitOwnerOfThisPage(pageDashboard, serverHub)
    pageDashboard.wait_for_selector(
        f"#listWorkflows >> text={S_HOST_WORKFLOW_NAME}", timeout=15000)
    assert "could not be" not in _fsToastText(pageDashboard).lower()
    pageDashboard.click(f"text={S_HOST_WORKFLOW_NAME}")
    pageDashboard.wait_for_selector(f"text={S_HOST_STEP_NAME}", timeout=20000)
    assert pageDashboard.listPageErrors == []


@pytest.mark.falsification
def test_the_recovery_claims_the_container_by_its_name_not_its_id(
    pageDashboard, serverHub,
):
    """A container whose name differs from its id is claimed by NAME.

    The owner map is keyed by name while every URL carries the id, and
    a recovery that claimed by the id the refusal came from would
    succeed for a host project (whose id IS its name) and fail for
    every container. This drives the container the lane's fake reports.

    Kills: claiming by the id the refusal's URL carried.
    """
    pageDashboard.goto(serverHub.fsBootstrapUrl(), wait_until="load")
    pageDashboard.wait_for_selector(
        f'.container-tile[data-name="{S_CONTAINER_NAME}"]', timeout=15000)
    assert serverHub.app.state.dictContainerOwners.get(
        S_CONTAINER_NAME) is None
    listRequests = _flistRecordRequests(pageDashboard)
    pageDashboard.evaluate(
        "(sId) => VaibifyContainerManager.fnConnectToContainer(sId)",
        S_CONTAINER_ID)
    pageDashboard.wait_for_function(
        "() => document.getElementById('listWorkflows')"
        ".innerText.trim().length > 0", timeout=15000)
    sClaimSuffix = f"/api/registry/{S_CONTAINER_NAME}/claim"
    assert any(
        sMethod == "POST" and sUrl.endswith(sClaimSuffix)
        for sMethod, sUrl in listRequests
    ), "the recovery never claimed the container by its name"
    assert S_CONTAINER_NAME in serverHub.app.state.dictContainerOwners


@pytest.mark.falsification
def test_four_pollers_refused_at_once_claim_exactly_once(
    pageDashboard, serverHub,
):
    """One recovery per container, however many requests are refused.

    The first claim is held so every refusal has arrived before it
    resolves; a second claim sent before the first one's lease is
    stored would be refused as another session's.

    Kills: removing the per-container single flight, so each refused
    request claims for itself.
    """
    fnOpenTheSeededHostWorkflow(pageDashboard, serverHub)
    listRequests = _flistRecordRequests(pageDashboard)
    listHeld = []
    _fnHoldRequests(
        pageDashboard, "**/api/registry/*/claim", listHeld,
        lambda request: not listHeld)
    _fnTakeTheClaimAway(serverHub)
    for iProbe, sRoute in enumerate((
        "pipeline/{}/state", "pipeline/{}/file-status",
        "repos/{}/status", "git/{}/status",
    )):
        _fnProbeWithoutWaiting(
            pageDashboard,
            "/api/" + sRoute.format(S_HOST_PROJECT_READY) + "?probe=b3",
            f"four{iProbe}")
    _fnAwaitHeld(pageDashboard, listHeld)
    pageDashboard.wait_for_timeout(800)
    listHeld[0].continue_()
    for iProbe in range(4):
        _fdictAwaitProbe(pageDashboard, f"four{iProbe}")
    pageDashboard.unroute_all(behavior="ignoreErrors")
    assert len(_flistClaimPosts(listRequests)) == 1, _flistClaimPosts(
        listRequests)


@pytest.mark.falsification
def test_an_answer_that_arrives_after_a_switch_is_dropped_not_recovered(
    pageDashboard, serverHub,
):
    """A refusal to a poll made for the project the researcher LEFT.

    The poll is held while the first project is open, the researcher
    switches to the second, and only then is the refusal released.
    Recovering it would claim and reconnect on behalf of a view that no
    longer exists.

    Kills: reading the container and view when the refusal ARRIVES
    instead of when the request was ISSUED.
    """
    sSecondPath = _fsWriteSecondWorkflow(serverHub)
    try:
        fnOpenTheSeededHostWorkflow(pageDashboard, serverHub)
        listRequests = _flistRecordRequests(pageDashboard)
        listHeld = []
        _fnHoldRequests(
            pageDashboard, "**/api/pipeline/*/file-status*", listHeld,
            lambda request: not listHeld)
        _fnAwaitHeld(pageDashboard, listHeld)
        _fnStartSwitchToSecond(pageDashboard)
        pageDashboard.wait_for_selector(
            f"text={S_SECOND_STEP_NAME}", timeout=20000)
        iClaimsBefore = len(_flistClaimPosts(listRequests))
        listHeld[0].fulfill(
            status=403, content_type="application/json",
            body=S_CLAIM_REQUIRED_BODY)
        pageDashboard.wait_for_timeout(1500)
        assert len(_flistClaimPosts(listRequests)) == iClaimsBefore, (
            "a refusal to a poll for the project the researcher left "
            "was recovered"
        )
        assert "claimed by this session" not in _fsToastText(pageDashboard)
        assert pageDashboard.listPageErrors == []
    finally:
        pageDashboard.unroute_all(behavior="ignoreErrors")
        os.remove(sSecondPath)


@pytest.mark.falsification
def test_a_project_held_by_another_session_is_never_claimed_over(
    pageDashboard, serverHub,
):
    """The control for recovery: the refusal that is NOT the page's to cure.

    Another session holds the project, so the hub's refusal carries no
    code. Claiming would be refused, and a recovery loop on every poll
    would be the page hammering a lock it cannot take.

    Kills: recovering on any refusal instead of the code.
    """
    _fnReachTheWorkflowPicker(pageDashboard, serverHub)
    _fnHandTheClaimToAnotherSession(serverHub)
    listRequests = _flistRecordRequests(pageDashboard)
    pageDashboard.click("#btnRefreshWorkflows")
    pageDashboard.wait_for_function(
        "() => document.getElementById('toastContainer')"
        ".innerText.trim().length > 0", timeout=15000)
    pageDashboard.wait_for_timeout(1000)
    assert _flistClaimPosts(listRequests) == []
    assert "lease" in _fsToastText(pageDashboard).lower()


@pytest.mark.falsification
def test_a_claim_taken_during_the_recovery_is_reported_and_not_retried(
    pageDashboard, serverHub,
):
    """Another session wins the race: its sentence is shown, nothing retried.

    The recovery's claim is held until a second session has claimed the
    project, so the claim route answers "in use". The original request
    is not sent a second time, and the sentence is the claim route's
    own, which names who holds it.

    Kills: treating a refused recovery as a recovered one.
    """
    fnOpenTheSeededHostWorkflow(pageDashboard, serverHub)
    listRequests = _flistRecordRequests(pageDashboard)
    listHeld = []
    _fnHoldRequests(
        pageDashboard, "**/api/registry/*/claim", listHeld,
        lambda request: not listHeld)
    _fnTakeTheClaimAway(serverHub)
    sProbeUrl = f"/api/pipeline/{S_HOST_PROJECT_READY}/state?probe=b5b"
    _fnProbeWithoutWaiting(pageDashboard, sProbeUrl, "taken")
    _fnAwaitHeld(pageDashboard, listHeld)
    _fnInstallAnotherSessionsClaim(serverHub)
    listHeld[0].continue_()
    dictOutcome = _fdictAwaitProbe(pageDashboard, "taken")
    pageDashboard.unroute_all(behavior="ignoreErrors")
    assert dictOutcome["bOk"] is False
    assert dictOutcome["bHandled"] is True
    listProbeRequests = [
        sUrl for sMethod, sUrl in listRequests if sUrl.endswith(sProbeUrl)]
    assert len(listProbeRequests) == 1, (
        "the refused recovery retried the original request")
    assert "in use" in _fsToastText(pageDashboard).lower()


@pytest.mark.falsification
def test_a_recovery_connect_never_outlasts_the_researchers_next_choice(
    pageDashboard, serverHub,
):
    """The hub's cached project is the one the researcher chose LAST.

    The recovery's reconnect is held in flight, and the researcher
    switches to the second project meanwhile. Every connect for one
    container runs through one queue, so the switch waits its turn and
    the hub ends on the second project. A recovery connect sent outside
    the queue lands AFTER the switch and leaves the hub on the first
    while the page shows the second.

    Kills: sending the recovery's connect outside the connect queue.
    """
    sSecondPath = _fsWriteSecondWorkflow(serverHub)
    try:
        fnOpenTheSeededHostWorkflow(pageDashboard, serverHub)
        listHeld = []
        _fnHoldRequests(
            pageDashboard, "**/api/connect/**", listHeld,
            lambda request: not listHeld)
        _fnTakeTheClaimAway(serverHub)
        _fnProbeWithoutWaiting(
            pageDashboard,
            f"/api/pipeline/{S_HOST_PROJECT_READY}/state?probe=b5c", "queue")
        _fnAwaitHeld(pageDashboard, listHeld)
        _fnStartSwitchToSecond(pageDashboard)
        pageDashboard.wait_for_timeout(1500)
        listHeld[0].continue_()
        pageDashboard.wait_for_selector(
            f"text={S_SECOND_STEP_NAME}", timeout=20000)
        pageDashboard.unroute_all(behavior="ignoreErrors")
        sCachedPath = serverHub.app.state.dictRouteContext["paths"][
            S_HOST_PROJECT_READY]
        assert sCachedPath.endswith(S_SECOND_WORKFLOW_NAME + ".json"), (
            "the hub's cached project is not the one the page shows")
    finally:
        pageDashboard.unroute_all(behavior="ignoreErrors")
        os.remove(sSecondPath)


# ---------------------------------------------------------------------
# Leaving for the Environments page
# ---------------------------------------------------------------------


@pytest.mark.falsification
def test_a_refused_release_keeps_the_session_alive_past_the_reconnect_window(
    pageDashboard, serverHub, monkeypatch,
):
    """Leaving while a run is live cannot revoke the page's own session.

    The hub refuses the release (a run is live). The page used to close
    its socket FIRST and then ask, so a refusal left a claim held by a
    session with no socket, which the hub revokes a window later: every
    later request answered 401 and only ``vaibify open`` recovered.
    Now the release comes first, a refusal keeps the researcher where
    they are, and once the run is over the same click lets them leave
    and open a project again.

    Kills: tearing the page down before the release is answered.
    """
    _fnShortenTheReconnectWindow(monkeypatch)
    dictBusy = _fnHoldTheHubBusy(monkeypatch)
    listUnauthorized = _flistRecordUnauthorizedAnswers(pageDashboard)
    _fnOpenTheDashboardWithItsSocket(pageDashboard, serverHub)
    _fnClickEnvironments(pageDashboard)
    pageDashboard.wait_for_function(
        "() => document.getElementById('toastContainer')"
        ".innerText.includes('pipeline run is live')", timeout=15000)
    pageDashboard.wait_for_timeout(
        int((F_SHORT_RECONNECT_WINDOW_SECONDS + 7.0) * 1000))
    assert listUnauthorized == [], listUnauthorized
    assert pageDashboard.evaluate("() => VaibifyWebSocket.fbIsOpen()")
    dictBusy["sSentence"] = ""
    _fnClickEnvironments(pageDashboard)
    _fnAwaitLanding(pageDashboard)
    fnOpenTheSeededHostWorkflow(pageDashboard, serverHub)
    assert listUnauthorized == [], listUnauthorized


@pytest.mark.falsification
def test_the_hubs_close_before_its_answer_is_the_expected_close(
    pageDashboard, serverHub,
):
    """A committed release closes the page's socket before it answers.

    The answer is delayed so the 4401 close arrives FIRST. The page
    asked for this close and must not read it as a rejected credential.

    Kills: removing the mark that tells the socket's close handler the
    release was asked for.
    """
    def _fnDelayTheAnswer(routeRelease):
        responseHub = routeRelease.fetch()
        pageDashboard.wait_for_timeout(1500)
        routeRelease.fulfill(response=responseHub)

    _fnOpenTheDashboardWithItsSocket(pageDashboard, serverHub)
    pageDashboard.route(S_RELEASE_ROUTE, _fnDelayTheAnswer)
    _fnClickEnvironments(pageDashboard)
    _fnAwaitLanding(pageDashboard)
    pageDashboard.unroute_all(behavior="ignoreErrors")
    pageDashboard.wait_for_timeout(1500)
    assert pageDashboard.evaluate(
        "() => VaibifyConnectionMonitor.fbHasSurfaced()") is False
    sToast = _fsToastText(pageDashboard).lower()
    assert "did not accept" not in sToast and "cannot reach" not in sToast
    assert pageDashboard.listPageErrors == []


@pytest.mark.falsification
def test_the_hubs_close_after_its_answer_is_the_expected_close_too(
    pageDashboard, serverHub,
):
    """The same close, arriving AFTER the answer has been acted on.

    The close and the answer travel on separate connections and arrive
    in either order, so the mark must live on the socket until its own
    close handler has run, never be cleared by the HTTP answer.

    Kills: clearing the mark when the release's answer arrives.
    """
    listRoutes = _fnRouteThePipelineSocket(pageDashboard)
    _fnOpenTheDashboardWithItsSocket(pageDashboard, serverHub)
    _fnClickEnvironments(pageDashboard)
    _fnAwaitLanding(pageDashboard)
    assert listRoutes, "the pipeline socket was never routed"
    listRoutes[-1].close(code=4401, reason="")
    pageDashboard.wait_for_timeout(1500)
    assert pageDashboard.evaluate(
        "() => VaibifyConnectionMonitor.fbHasSurfaced()") is False
    assert "did not accept" not in _fsToastText(pageDashboard).lower()


@pytest.mark.falsification
def test_a_close_nobody_asked_for_still_surfaces(pageDashboard, serverHub):
    """The control: the mark must not suppress a real failure.

    Kills: ignoring every 4401 instead of the marked socket's.
    """
    listRoutes = _fnRouteThePipelineSocket(pageDashboard)
    _fnOpenTheDashboardWithItsSocket(pageDashboard, serverHub)
    listRoutes[-1].close(code=4401, reason="")
    pageDashboard.wait_for_function(
        "() => VaibifyConnectionMonitor.fbHasSurfaced()", timeout=15000)
    assert "did not accept" in _fsToastText(pageDashboard).lower()


@pytest.mark.falsification
def test_a_refused_release_stays_on_the_dashboard_with_its_socket(
    pageDashboard, serverHub, monkeypatch,
):
    """409: the hub's own sentence, the dashboard, and the socket stays.

    Kills: leaving the dashboard whatever the release answered.
    """
    _fnHoldTheHubBusy(monkeypatch)
    _fnOpenTheDashboardWithItsSocket(pageDashboard, serverHub)
    _fnClickEnvironments(pageDashboard)
    pageDashboard.wait_for_function(
        "() => document.getElementById('toastContainer')"
        ".innerText.includes('pipeline run is live')", timeout=15000)
    assert pageDashboard.locator("#mainLayout.active").count() == 1
    assert pageDashboard.evaluate("() => VaibifyWebSocket.fbIsOpen()")
    assert S_HOST_PROJECT_READY in serverHub.app.state.dictContainerOwners


@pytest.mark.falsification
def test_a_release_answered_not_released_is_not_taken_for_released(
    pageDashboard, serverHub,
):
    """200 with ``bReleased: false`` while the hub still holds the claim.

    The page asks the hub's own list whether it still holds the
    container, finds it does, and stays with the hub's sentence.

    Kills: treating any 200 as released.
    """
    sSentence = "The hub did not release this container just now."
    pageDashboard.route(S_RELEASE_ROUTE, lambda routeRelease: (
        routeRelease.fulfill(
            status=200, content_type="application/json",
            body=json.dumps({"bReleased": False, "sMessage": sSentence}))))
    _fnOpenTheDashboardWithItsSocket(pageDashboard, serverHub)
    _fnClickEnvironments(pageDashboard)
    pageDashboard.wait_for_function(
        "(sSentence) => document.getElementById('toastContainer')"
        ".innerText.includes(sSentence)", arg=sSentence, timeout=15000)
    assert pageDashboard.locator("#mainLayout.active").count() == 1
    assert pageDashboard.evaluate("() => VaibifyWebSocket.fbIsOpen()")
    pageDashboard.unroute_all(behavior="ignoreErrors")


def test_a_release_that_never_reached_the_hub_changes_nothing(
    pageDashboard, serverHub,
):
    """The request is lost before the hub sees it: not confirmed, no change."""
    pageDashboard.route(
        S_RELEASE_ROUTE, lambda routeRelease: routeRelease.abort())
    _fnOpenTheDashboardWithItsSocket(pageDashboard, serverHub)
    _fnClickEnvironments(pageDashboard)
    pageDashboard.wait_for_function(
        "() => document.getElementById('toastContainer')"
        ".innerText.includes('could not be confirmed')", timeout=15000)
    assert pageDashboard.locator("#mainLayout.active").count() == 1
    assert pageDashboard.evaluate("() => VaibifyWebSocket.fbIsOpen()")
    assert S_HOST_PROJECT_READY in serverHub.app.state.dictContainerOwners
    pageDashboard.unroute_all(behavior="ignoreErrors")


@pytest.mark.falsification
def test_a_poll_refused_while_the_release_is_answered_does_not_take_the_claim_back(
    pageDashboard, serverHub,
):
    """The release itself makes the polls in flight fail; they must not undo it.

    The hub commits the release and the answer is held. A request made in
    that gap is refused for want of a claim -- the very claim just given
    back -- and a page that recovered it would leave the Environments
    page holding the project again.

    Kills: letting the recovery run while the page is releasing.
    """
    listHeld = []
    listRequests = _flistRecordRequests(pageDashboard)
    _fnOpenTheDashboardWithItsSocket(pageDashboard, serverHub)
    iClaimsBeforeLeaving = len(_flistClaimPosts(listRequests))
    pageDashboard.route(S_RELEASE_ROUTE, lambda routeRelease: (
        listHeld.append((routeRelease, routeRelease.fetch()))))
    _fnClickEnvironments(pageDashboard)
    for _ in range(100):
        if listHeld:
            break
        pageDashboard.wait_for_timeout(100)
    assert listHeld, "the release never reached the hub"
    _fnProbeWithoutWaiting(
        pageDashboard,
        f"/api/pipeline/{S_HOST_PROJECT_READY}/state?probe=leaving", "leaving")
    dictOutcome = _fdictAwaitProbe(pageDashboard, "leaving")
    assert dictOutcome["bOk"] is False and dictOutcome["bHandled"] is True
    assert len(_flistClaimPosts(listRequests)) == iClaimsBeforeLeaving
    routeRelease, responseHub = listHeld[0]
    routeRelease.fulfill(response=responseHub)
    _fnAwaitLanding(pageDashboard)
    pageDashboard.unroute_all(behavior="ignoreErrors")
    pageDashboard.wait_for_timeout(1500)
    assert S_HOST_PROJECT_READY not in serverHub.app.state.dictContainerOwners


@pytest.mark.falsification
def test_a_recovery_claim_that_lands_after_leaving_is_given_back(
    pageDashboard, serverHub,
):
    """A claim already on the wire cannot be recalled; the page undoes it.

    The recovery's claim request is held, the researcher leaves for the
    Environments page, and only then does the claim land. The page took
    it, so the page gives it back: the hub holds nothing and the page
    keeps no lease.

    Kills: leaving the claim in place once it has landed after leaving.
    """
    fnOpenTheSeededHostWorkflow(pageDashboard, serverHub)
    _fnStopTheDashboardPollers(pageDashboard)
    listHeld = []
    _fnHoldRequests(
        pageDashboard, "**/api/registry/*/claim", listHeld,
        lambda request: not listHeld)
    _fnTakeTheClaimAway(serverHub)
    _fnProbeWithoutWaiting(
        pageDashboard,
        f"/api/pipeline/{S_HOST_PROJECT_READY}/state?probe=late", "late")
    _fnAwaitHeld(pageDashboard, listHeld)
    _fnClickEnvironments(pageDashboard)
    _fnAwaitLanding(pageDashboard)
    listHeld[0].continue_()
    _fdictAwaitProbe(pageDashboard, "late")
    pageDashboard.unroute_all(behavior="ignoreErrors")
    for _ in range(50):
        if S_HOST_PROJECT_READY not in serverHub.app.state.dictContainerOwners:
            break
        pageDashboard.wait_for_timeout(100)
    assert S_HOST_PROJECT_READY not in serverHub.app.state.dictContainerOwners
    assert pageDashboard.evaluate("() => VaibifyApp.fsGetLeaseId()") == ""


@pytest.mark.falsification
def test_a_blank_dashboard_is_reconnected_so_the_agent_gets_the_new_token(
    pageDashboard, serverHub,
):
    """A Blank Project is open on the dashboard too, and its agent needs the token.

    A reclaim mints a new agent token and only a connect writes it into
    the container. The container in this lane has a name that differs
    from its id, and its session file is the thing the agent reads, so
    that is what is asserted.

    Kills: skipping the reconnect when no workflow is open.
    """
    from vaibify.config.containerLock import fnReleaseContainerLock
    pageDashboard.goto(serverHub.fsBootstrapUrl(), wait_until="load")
    pageDashboard.wait_for_selector(
        f'.container-tile[data-name="{S_CONTAINER_NAME}"]', timeout=15000)
    pageDashboard.evaluate(
        "(sId) => VaibifyContainerManager.fnConnectToContainer(sId)",
        S_CONTAINER_ID)
    pageDashboard.wait_for_selector("#workflowPicker", state="visible")
    pageDashboard.click("#btnNoWorkflow")
    pageDashboard.wait_for_selector("#mainLayout.active", timeout=20000)
    assert pageDashboard.evaluate(
        "() => VaibifyApp.fsGetWorkflowPath()") is None
    _fnStopTheDashboardPollers(pageDashboard)
    dictOwners = serverHub.app.state.dictContainerOwners
    recordOriginal = dictOwners[S_CONTAINER_NAME]
    assert recordOriginal.sAgentToken.encode() in (
        serverHub.adapterDocker._dictFiles[S_SESSION_ENV_PATH])
    dictOwners.pop(S_CONTAINER_NAME)
    fnReleaseContainerLock(recordOriginal.fileHandleLock)
    _fnProbeWithoutWaiting(
        pageDashboard, f"/api/workflows/{S_CONTAINER_ID}?probe=blank", "blank")
    assert _fdictAwaitProbe(pageDashboard, "blank")["bOk"] is True
    recordRecovered = dictOwners[S_CONTAINER_NAME]
    assert recordRecovered.sAgentToken != recordOriginal.sAgentToken
    assert recordRecovered.sAgentToken.encode() in (
        serverHub.adapterDocker._dictFiles[S_SESSION_ENV_PATH]), (
        "the agent in the container still holds the retired token")


@pytest.mark.falsification
def test_a_reconnect_that_failed_is_not_a_recovery(pageDashboard, serverHub):
    """The claim was taken again but the agent's token was not renewed.

    The reconnect is refused, so the recovery is INCOMPLETE: the original
    request is not retried as though all were well, and the researcher
    is told what to do about the agent.

    Kills: counting a failed reconnect as a recovered claim.
    """
    fnOpenTheSeededHostWorkflow(pageDashboard, serverHub)
    _fnStopTheDashboardPollers(pageDashboard)
    pageDashboard.route("**/api/connect/**", lambda routeConnect: (
        routeConnect.fulfill(
            status=502, content_type="application/json",
            body=json.dumps({"detail": "The reconnect could not complete."}))))
    _fnTakeTheClaimAway(serverHub)
    sProbeUrl = f"/api/pipeline/{S_HOST_PROJECT_READY}/state?probe=reconnect"
    listRequests = _flistRecordRequests(pageDashboard)
    _fnProbeWithoutWaiting(pageDashboard, sProbeUrl, "reconnect")
    dictOutcome = _fdictAwaitProbe(pageDashboard, "reconnect")
    pageDashboard.unroute_all(behavior="ignoreErrors")
    assert dictOutcome["bOk"] is False and dictOutcome["bHandled"] is True
    assert len([
        sUrl for sMethod, sUrl in listRequests if sUrl.endswith(sProbeUrl)
    ]) == 1, "the request was retried although the reconnect failed"
    assert "open the project again" in _fsToastText(pageDashboard).lower()


@pytest.mark.falsification
def test_a_request_made_after_leaving_does_not_take_the_claim_back(
    pageDashboard, serverHub,
):
    """The Environments page holds no container, so nothing recovers one.

    A poll still in flight when the researcher left is refused for want
    of the claim they gave up on purpose. The container they were in is
    still the page's selection, so its URL still names it; recovering
    would hand the project back to a page that is no longer in it.

    Kills: recovering while the Environments page is showing.
    """
    listRequests = _flistRecordRequests(pageDashboard)
    _fnOpenTheDashboardWithItsSocket(pageDashboard, serverHub)
    _fnClickEnvironments(pageDashboard)
    _fnAwaitLanding(pageDashboard)
    iClaimsBefore = len(_flistClaimPosts(listRequests))
    _fnProbeWithoutWaiting(
        pageDashboard,
        f"/api/pipeline/{S_HOST_PROJECT_READY}/state?probe=landing",
        "landing")
    dictOutcome = _fdictAwaitProbe(pageDashboard, "landing")
    assert dictOutcome["bOk"] is False and dictOutcome["bHandled"] is True
    pageDashboard.wait_for_timeout(500)
    assert len(_flistClaimPosts(listRequests)) == iClaimsBefore
    assert S_HOST_PROJECT_READY not in serverHub.app.state.dictContainerOwners


@pytest.mark.falsification
def test_a_release_that_committed_but_lost_its_answer_still_lets_you_leave(
    pageDashboard, serverHub,
):
    """The hub released; only its answer was lost.

    The page does not assert either way. It asks the hub's own list,
    finds the container no longer held, and carries on to the
    Environments page without a false "still holding" message.

    Kills: treating an unconfirmed release as a refused one.
    """
    def _fnCommitThenLoseTheAnswer(routeRelease):
        routeRelease.fetch()
        routeRelease.abort()

    listUnauthorized = _flistRecordUnauthorizedAnswers(pageDashboard)
    _fnOpenTheDashboardWithItsSocket(pageDashboard, serverHub)
    pageDashboard.route(S_RELEASE_ROUTE, _fnCommitThenLoseTheAnswer)
    _fnClickEnvironments(pageDashboard)
    _fnAwaitLanding(pageDashboard)
    pageDashboard.unroute_all(behavior="ignoreErrors")
    assert S_HOST_PROJECT_READY not in serverHub.app.state.dictContainerOwners
    assert "still hold" not in _fsToastText(pageDashboard).lower()
    pageDashboard.wait_for_timeout(1500)
    assert listUnauthorized == []


# ---------------------------------------------------------------------
# A session the hub really ended says so once
# ---------------------------------------------------------------------


@pytest.mark.falsification
def test_a_socket_closed_4401_by_an_ended_session_names_what_happened(
    pageDashboard, serverHub,
):
    """A 4401 close carries no reason, but the hub still has one to give.

    The hub ended this session and closed its socket. The close says only
    "rejected"; the notice the hub recorded -- what happened, when, and
    what to run -- is answered to any request the revoked credential
    makes, so the page asks for it before it speaks.

    Kills: surfacing the bare close, which says the session "restarted or
    expired" and names nothing to run.
    """
    from vaibify.gui import browserSession
    listRoutes = _fnRouteThePipelineSocket(pageDashboard)
    _fnOpenTheDashboardWithItsSocket(pageDashboard, serverHub)
    browserSession.fbRevokeSessionById(
        serverHub.app.state.dictBrowserSessions,
        _fsPageSessionId(pageDashboard, serverHub),
        sEndedMessage=(
            "This browser session ended because its dashboard stopped "
            "answering. Run 'vaibify open' to attach a fresh tab."))
    listRoutes[-1].close(code=4401, reason="")
    pageDashboard.wait_for_function(
        "() => VaibifyConnectionMonitor.fbHasSurfaced()", timeout=15000)
    sToast = _fsToastText(pageDashboard)
    assert "vaibify open" in sToast, sToast
    assert "It ended at" in sToast, sToast
    assert "did not accept" not in sToast, sToast


@pytest.mark.falsification
def test_a_session_whose_socket_was_lost_is_told_once_what_to_run(
    pageDashboard, serverHub, monkeypatch,
):
    """The hub revokes a session whose socket went away; the page says so once.

    A claim that has had a socket and lost it for a whole reconnect
    window is orphaned and its credential revoked, and only
    ``vaibify open`` recovers it. Every poller then meets a 401, and the
    researcher is told ONE sentence naming that, never a lease toast
    per poller.

    Kills: letting every refused poller raise its own notice.
    """
    from vaibify.gui import containerOwnership
    _fnShortenTheReconnectWindow(monkeypatch)
    _fnOpenTheDashboardWithItsSocket(pageDashboard, serverHub)
    pageDashboard.evaluate("() => VaibifyWebSocket.fnDisconnect()")
    fDeadline = time.monotonic() + 25.0
    while time.monotonic() < fDeadline:
        recordOwner = serverHub.app.state.dictContainerOwners.get(
            S_HOST_PROJECT_READY)
        if recordOwner is not None and recordOwner.sState == (
                containerOwnership.S_OWNER_STATE_ORPHANED_SESSION):
            break
        pageDashboard.wait_for_timeout(250)
    else:
        raise AssertionError("the hub never orphaned the socketless session")
    pageDashboard.wait_for_function(
        "() => document.getElementById('toastContainer')"
        ".innerText.includes('vaibify open')", timeout=20000)
    pageDashboard.wait_for_timeout(7000)
    sToast = _fsToastText(pageDashboard)
    assert sToast.count("vaibify open") == 1, sToast
    assert "do not hold" not in sToast.lower()


# ---------------------------------------------------------------------
# The Project Hub names the environment it lists
# ---------------------------------------------------------------------


@pytest.mark.falsification
def test_the_project_hub_names_the_environment_from_a_tile(
    pageDashboard, serverHub,
):
    """A container's Project Hub is headed by the name its tile shows.

    Kills: dropping the assignment of the line in fnShowWorkflowPicker.
    """
    pageDashboard.goto(serverHub.fsBootstrapUrl(), wait_until="load")
    pageDashboard.wait_for_selector(
        f'.container-tile[data-name="{S_CONTAINER_NAME}"]', timeout=15000)
    pageDashboard.evaluate(
        "(sId) => VaibifyContainerManager.fnConnectToContainer(sId)",
        S_CONTAINER_ID)
    pageDashboard.wait_for_selector("#workflowPicker", state="visible")
    assert pageDashboard.inner_text("#pickerEnvironmentLine") == (
        f"Environment: {S_CONTAINER_NAME}")
    assert pageDashboard.evaluate(
        """() => {
            const elLine = document.getElementById('pickerEnvironmentLine');
            const elLabel = document.querySelector(
                '#workflowPicker .picker-section-label');
            return elLine.compareDocumentPosition(elLabel) &
                Node.DOCUMENT_POSITION_FOLLOWING;
        }""") != 0, "the line is not above \"Available Projects:\""


def test_the_project_hub_names_this_computer_for_a_host_project(
    pageDashboard, serverHub,
):
    """A host project has no container: the line says where it lives."""
    _fnReachTheWorkflowPicker(pageDashboard, serverHub)
    assert pageDashboard.inner_text("#pickerEnvironmentLine") == (
        "Environment: this computer")


def test_admin_projects_shows_the_same_line(pageDashboard, serverHub):
    """The Project Hub reached from the dashboard is headed the same way."""
    fnOpenTheSeededHostWorkflow(pageDashboard, serverHub)
    pageDashboard.evaluate(
        "() => document.getElementById('btnAdminWorkflows').click()")
    pageDashboard.wait_for_selector("#modalConfirm", timeout=10000)
    pageDashboard.click("#btnConfirmOk")
    pageDashboard.wait_for_selector("#workflowPicker", state="visible")
    assert pageDashboard.inner_text("#pickerEnvironmentLine") == (
        "Environment: this computer")
