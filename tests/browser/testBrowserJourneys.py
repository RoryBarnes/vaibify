"""Lane 1 journeys: the dashboard, in a real browser, against a real server.

Ordered deliberately. Refusal-honesty comes first because that is this
repository's actual shipped-bug shape -- all-grey badges, Run Step
always refused and mislabelled "cannot reach server", passed steps
rendering as missing markers. A dashboard that lies is worse than one
that is slow, so the first thing the lane proves is that a backend
refusal reaches the screen as a refusal.

Every journey asserts both what the browser shows and what the backend
authoritatively holds afterwards. That is what makes this a front-and-
back test rather than a frontend mock test.

WHAT THIS LANE DOES NOT COVER -- stated because silence about an
unverified surface reads as verification:

* The Docker boundary. The adapter here is a fail-closed fake; only
  Lane 2, against a real container, speaks for container launch, file
  ownership on write, or the real transport.
* Terminal WebSocket content, figure rendering, and the sync panel.
* Anything requiring a second browser session's lease to be minted by
  a different hub process.

Lane 1 failing blocks merge. Lane 2 failing blocks the next release,
not retroactively -- it runs nightly, so fake-vs-reality drift is
caught up to a day late.
"""

import json
import os

import pytest

from tests.browser.fakeDockerAdapter import (
    S_CONTAINER_ID,
    S_CONTAINER_NAME,
    UnmodelledContainerCall,
)


pytestmark = pytest.mark.browser


def _fsPageCredential(page):
    """Return the per-browser credential the page bootstrapped, from storage.

    After navigating with a ``#bootstrap=`` fragment the dashboard redeems
    its capability and stashes the credential in ``sessionStorage``; reading
    it back is how a journey obtains the genuine credential the retired
    ``/api/session-token`` oracle used to hand out.
    """
    return page.evaluate(
        """() => window.sessionStorage.getItem('vaibifySessionCredential')"""
    )


# ---------------------------------------------------------------------
# Journey 1 -- a refusal must look like a refusal
# ---------------------------------------------------------------------


def testBackendRefusalIsNotRenderedAsSuccess(pageDashboard, serverHub):
    """An unauthorized API call must not read as a success anywhere.

    The bar is not "the request failed" -- it is that nothing in the
    response could be mistaken for a granted action by a caller that
    only checks for a payload.
    """
    pageDashboard.goto(serverHub.fsBootstrapUrl(), wait_until="load")
    dictResult = pageDashboard.evaluate(
        """async (sBaseUrl) => {
            const response = await fetch(
                sBaseUrl + '/api/projects', {
                    headers: {'x-session-token': 'not-the-real-token'},
                });
            return {
                iStatus: response.status,
                sBody: await response.text(),
            };
        }""",
        serverHub.sBaseUrl,
    )
    assert dictResult["iStatus"] == 401, (
        "A bad session token was not refused: "
        f"{dictResult['iStatus']} {dictResult['sBody'][:200]}"
    )
    assert "bSuccess" not in dictResult["sBody"], (
        "A refusal carried a success-shaped payload, which a caller "
        "checking only for a body would render as an accepted action."
    )


# ---------------------------------------------------------------------
# Journey 2 -- the page evaluates cleanly
# ---------------------------------------------------------------------


def testDashboardLoadsWithNoConsoleErrors(pageDashboard, serverHub):
    """Zero console errors, zero page errors, zero failed assets.

    A single ReferenceError means a module failed to evaluate and every
    module below it in load order is dead -- the exact failure the five
    parallel agents shipped green, because no automated check executed
    the frontend at all.
    """
    listFailedRequests = []
    pageDashboard.on("requestfailed", lambda request: (
        listFailedRequests.append(request.url)
    ))
    listBadResponses = []
    pageDashboard.on("response", lambda response: (
        listBadResponses.append((response.url, response.status))
        if response.status >= 500 else None
    ))

    pageDashboard.goto(serverHub.fsBootstrapUrl(), wait_until="networkidle")

    assert pageDashboard.listPageErrors == [], (
        "Uncaught page errors: " + "; ".join(pageDashboard.listPageErrors)
    )
    assert pageDashboard.listConsoleErrors == [], (
        "Console errors: " + "; ".join(pageDashboard.listConsoleErrors)
    )
    assert listFailedRequests == [], (
        "Assets failed to load: " + "; ".join(listFailedRequests)
    )
    assert listBadResponses == [], (
        f"Server errors during load: {listBadResponses}"
    )


def testFrontendGlobalsResolveAsBareIdentifiers(pageDashboard, serverHub):
    """The IIFE modules must actually be on the page.

    Probed as bare identifiers, not via ``window``: the modules are
    declared with ``const``, which creates a global *lexical* binding
    rather than a ``window`` property, so ``window.VaibifyApp`` is
    ``undefined`` for a module that is working perfectly. AGENTS.md
    records a session lost to exactly that false alarm.
    """
    pageDashboard.goto(serverHub.fsBootstrapUrl(), wait_until="networkidle")
    assert pageDashboard.evaluate("typeof VaibifyApp") == "object", (
        "VaibifyApp did not evaluate; the module graph is broken."
    )
    assert pageDashboard.evaluate(
        "typeof VaibifyUtilities"
    ) == "object"


# ---------------------------------------------------------------------
# Journey 3 -- the seeded project renders from the real response
# ---------------------------------------------------------------------


def testSeededProjectReachesTheBrowser(pageDashboard, serverHub):
    """The seeded project must arrive over real HTTP and be rendered.

    The token is NOT set by hand here. The application installs its own
    ``fetch`` wrapper that injects ``x-session-token``, so adding the
    header explicitly sends it twice -- and Starlette joins duplicate
    headers with ", ", producing ``"<token>, <token>"``, which the
    middleware correctly rejects with a 401. Riding the app's wrapper
    is both the faithful path and the working one.
    """
    pageDashboard.goto(serverHub.fsBootstrapUrl(), wait_until="networkidle")
    dictPayload = pageDashboard.evaluate(
        """async (sBaseUrl) => {
            const response = await fetch(sBaseUrl + '/api/registry');
            return {iStatus: response.status,
                    sBody: await response.text()};
        }""",
        serverHub.sBaseUrl,
    )
    assert dictPayload["iStatus"] == 200, dictPayload["sBody"][:300]
    assert S_CONTAINER_NAME in dictPayload["sBody"], (
        "The seeded project never reached the browser: "
        + dictPayload["sBody"][:300]
    )
    assert "gj1132" not in dictPayload["sBody"].lower(), (
        "The lane is reading the developer's real ~/.vaibify registry "
        "instead of its isolated one."
    )
    pageDashboard.wait_for_selector(
        f"text={S_CONTAINER_NAME}", timeout=15000,
    )


def testDoubledSessionTokenHeaderIsRefused(pageDashboard, serverHub):
    """A doubled token HEADER must fail closed, not fail open.

    Renamed from "duplicate session", which implied coverage this does
    not have: this is one browser sending one header twice, an
    entirely different mechanism from a second browser session
    copying a lease (see the duplicate-session journey below).

    Discovered while building this lane: because the app's fetch
    wrapper already injects the header, a caller that also sets it
    produces a doubled value. The property that matters is the
    direction of the failure -- a doubled credential is refused, never
    accepted by prefix or by taking the first element.
    """
    pageDashboard.goto(serverHub.fsBootstrapUrl(), wait_until="networkidle")
    sCredential = _fsPageCredential(pageDashboard)
    assert sCredential, "the page must have bootstrapped a credential"
    iStatus = pageDashboard.evaluate(
        """async ([sBaseUrl, sToken]) => {
            const response = await fetch(sBaseUrl + '/api/registry', {
                headers: {'x-session-token': sToken},
            });
            return response.status;
        }""",
        [serverHub.sBaseUrl, sCredential],
    )
    assert iStatus == 401


# ---------------------------------------------------------------------
# The fake's own contract
# ---------------------------------------------------------------------


def testTheDockerFakeRefusesUnmodelledCalls(serverHub):
    """The adapter must never invent an answer.

    This is the property that separates Lane 1 from the twenty
    hand-rolled mocks it does not want to become. A permissive
    catch-all would make every journey above pass for the wrong reason.
    """
    with pytest.raises(UnmodelledContainerCall):
        serverHub.adapterDocker.ftResultExecuteCommand(
            "any-container", "rm -rf /workspace",
        )


# ---------------------------------------------------------------------
# Journey 4 -- agent choices persist from the real creation wizard
# ---------------------------------------------------------------------


@pytest.mark.parametrize(
    "sAgent,sProjectName,sFeatureField,sAutoUpdateField",
    [
        ("codex", "browser-codex", "bCodex", "bCodexAutoUpdate"),
        ("gemini", "browser-gemini", "bGemini", "bGeminiAutoUpdate"),
        ("opencode", "browser-opencode", "bOpenCode", "bOpenCodeAutoUpdate"),
        ("cline", "browser-cline", "bCline", "bClineAutoUpdate"),
        ("openhands", "browser-openhands", "bOpenHands", "bOpenHandsAutoUpdate"),
        ("pi", "browser-pi", "bPi", "bPiAutoUpdate"),
    ],
)
def testCreationWizardPersistsSelectedAgent(
    pageDashboard, serverHub, sAgent, sProjectName,
    sFeatureField, sAutoUpdateField,
):
    """A selected agent must reach the saved config with auto-update on.

    This drives the normal browser flow, including the host-directory
    picker.  Inspecting the written config is deliberate: the checkbox
    state alone could agree with a broken request serializer, while the
    config is what the image builder later consumes.
    """
    from vaibify.config.projectConfig import fconfigLoadFromFile

    pageDashboard.goto(serverHub.fsBootstrapUrl(), wait_until="networkidle")
    pageDashboard.locator("#btnAddContainer").click()
    pageDashboard.locator("#btnChoiceKindContainer").click()
    pageDashboard.locator("#btnChoiceCreateNew").click()
    pageDashboard.locator("#btnWizardChooseDirectory").click()
    sRelativeDirectory = os.path.relpath(
        serverHub.sHome, os.path.expanduser("~"),
    )
    for sDirectoryPart in sRelativeDirectory.split(os.sep):
        pageDashboard.locator(
            f".directory-entry-name:text-is('{sDirectoryPart}')"
        ).click()
    pageDashboard.locator("#btnDirectoryNewFolder").click()
    pageDashboard.locator("#modalInput .input-modal-field").fill(
        sProjectName
    )
    pageDashboard.locator("#btnInputConfirm").click()
    pageDashboard.locator(
        f".directory-entry-name:text-is('{sProjectName}')"
    ).click()
    pageDashboard.locator("#btnAddContainerConfirm").click()

    pageDashboard.locator("#btnWizardNext").click()
    pageDashboard.locator("[data-template='sandbox']").click()
    pageDashboard.locator("#btnWizardNext").click()
    pageDashboard.locator("#inputWizardProjectName").fill(sProjectName)
    pageDashboard.locator("#btnWizardNext").click()
    pageDashboard.locator("#btnWizardNext").click()
    pageDashboard.locator("#btnWizardNext").click()
    pageDashboard.locator(
        f".wizard-feature-input[data-feature='{sAgent}']"
    ).check()
    pageDashboard.locator("#btnWizardNext").click()
    pageDashboard.locator("#btnWizardNext").click()

    with pageDashboard.expect_response(
        lambda response: response.url.endswith("/api/projects/create")
        and response.status == 200,
    ) as responseInfo:
        pageDashboard.locator("#btnWizardNext").click()
    response = responseInfo.value
    dictRequest = response.request.post_data_json
    assert sAgent in dictRequest["listFeatures"]

    sConfigPath = os.path.join(
        serverHub.sHome, sProjectName, "vaibify.yml",
    )
    configProject = fconfigLoadFromFile(sConfigPath)
    assert getattr(configProject.features, sFeatureField) is True
    assert getattr(configProject.features, sAutoUpdateField) is True


# ---------------------------------------------------------------------
# Journey 8 -- a superseded socket must not orphan its replacement
# ---------------------------------------------------------------------


def testStaleSocketCloseDoesNotOrphanTheLiveSocket(
    pageDashboard, serverHub,
):
    """A dying socket may only clear the slot it still holds.

    ``onclose`` fires asynchronously, so the socket ``fnConnect`` tore
    down lands *after* its replacement is stored. Clearing the shared
    reference unconditionally left ``fiGetReadyState()`` at -1 with a
    perfectly healthy socket open, and the reconnect that followed was
    answered 4409 as a duplicate session.

    The race is driven here with a substituted WebSocket constructor so
    the ordering is deterministic rather than hoped for, but the module
    under test is the real one, evaluated by the real browser.
    """
    pageDashboard.goto(serverHub.fsBootstrapUrl(), wait_until="load")
    dictResult = pageDashboard.evaluate(
        """() => {
            const listCreated = [];
            const wsReal = window.WebSocket;
            function WebSocketFake(sUrl) {
                this.readyState = WebSocketFake.CONNECTING;
                this.url = sUrl;
                this.close = () => { this.readyState = 2; };
                listCreated.push(this);
            }
            WebSocketFake.CONNECTING = 0;
            WebSocketFake.OPEN = 1;
            WebSocketFake.CLOSING = 2;
            WebSocketFake.CLOSED = 3;
            window.WebSocket = WebSocketFake;
            try {
                VaibifyWebSocket.fnConnect('container-a', 'token');
                const wsFirst = listCreated[0];
                wsFirst.readyState = WebSocketFake.OPEN;
                wsFirst.onopen();

                /* Force a replacement the way a workflow switch does. */
                VaibifyWebSocket.fnDisconnect();
                VaibifyWebSocket.fnConnect('container-b', 'token');
                const wsSecond = listCreated[1];
                wsSecond.readyState = WebSocketFake.OPEN;
                wsSecond.onopen();

                /* A frame buffered by the superseded socket, and only
                 * then its close, both arriving after the replacement
                 * is live. */
                let iStaleDelivered = 0;
                VaibifyWebSocket.fnOnEvent(
                    'staleProbe', () => { iStaleDelivered++; });
                wsFirst.onmessage(
                    {data: JSON.stringify({sType: 'staleProbe'})});
                wsFirst.readyState = WebSocketFake.CLOSED;
                wsFirst.onclose({code: 1000});

                return {
                    iCreated: listCreated.length,
                    iStaleDelivered: iStaleDelivered,
                    iReadyState: VaibifyWebSocket.fiGetReadyState(),
                    bIsOpen: !!VaibifyWebSocket.fbIsOpen(),
                    bSecondStillHeld:
                        VaibifyWebSocket.fiGetReadyState() ===
                        WebSocketFake.OPEN,
                };
            } finally {
                window.WebSocket = wsReal;
                VaibifyWebSocket.fnDisconnect();
            }
        }"""
    )
    assert dictResult["iCreated"] == 2, dictResult
    assert dictResult["iReadyState"] != -1, (
        "the live socket was orphaned by a superseded socket's close; "
        "every later send silently queues and the reconnect is 4409'd"
    )
    assert dictResult["bIsOpen"], dictResult
    assert dictResult["iStaleDelivered"] == 0, (
        "a frame from the superseded socket was dispatched into the "
        "current view, so one container's run output can be rendered "
        "as another's"
    )


# ---------------------------------------------------------------------
# Journey -- the 'vaibify open' landing (#transfer= fragment)
# ---------------------------------------------------------------------


def testTransferFragmentAttachesTheTransferredSessionAndLease(
    pageDashboard, serverHub,
):
    """A ``#transfer=`` fragment becomes a live credential AND lease.

    The ``vaibify open`` landing (design paragraph 6, slice 5), front and
    back: the page must exchange the capability at ``/api/transfer``,
    stash the transferred credential in sessionStorage, record the
    transferred lease for the container, clear the fragment, and load
    with zero console errors; the backend must hold the rotated lease
    bound to the NEW browser session at generation 2, with the old
    session's credential revoked. The container NAME stays distinct
    from the container ID throughout.
    """
    from vaibify.gui import browserSession, containerOwnership
    from tests.browser.fakeDockerAdapter import S_CONTAINER_ID
    stateApp = serverHub.app.state
    sOldLaunch = browserSession.fsMintBootstrapCapability(
        stateApp.dictBrowserSessions,
    )
    sOldSessionId, sOldCredential = browserSession.ftRedeemCapability(
        stateApp.dictBrowserSessions, sOldLaunch,
    )
    recordOwner = containerOwnership.OwnerRecord(
        sLeaseId=containerOwnership.fsMintLease(),
        fileHandleLock=None,
        sAgentToken=containerOwnership.fsMintAgentToken(),
        sContainerId=S_CONTAINER_ID,
        sBrowserSessionId=sOldSessionId,
    )
    stateApp.dictContainerOwners[S_CONTAINER_NAME] = recordOwner
    stateApp.dictSessionOwner[sOldSessionId] = S_CONTAINER_NAME
    sTransferCapability = browserSession.fsMintTransferCapability(
        stateApp.dictBrowserSessions, S_CONTAINER_NAME, 1,
    )
    try:
        pageDashboard.goto(
            f"{serverHub.sBaseUrl}/#transfer={sTransferCapability}",
            wait_until="networkidle",
        )
        sCredential = _fsPageCredential(pageDashboard)
        assert sCredential, "the transfer exchange minted no credential"
        sStoredLease = pageDashboard.evaluate(
            """() => window.sessionStorage.getItem(
                'vaibifyContainerLease')"""
        )
        dictLease = json.loads(sStoredLease or "{}")
        assert dictLease.get("sName") == S_CONTAINER_NAME
        assert dictLease.get("sLeaseId") == recordOwner.sLeaseId
        assert "#transfer" not in pageDashboard.url, (
            "the one-time capability must be cleared from the URL "
            "after a successful exchange"
        )
        assert recordOwner.iOwnerGeneration == 2
        assert recordOwner.sBrowserSessionId not in ("", sOldSessionId)
        assert browserSession.fbValidateCredential(
            stateApp.dictBrowserSessions, sOldCredential,
        ) is False, "the displaced session's credential must be revoked"
        assert browserSession.fbValidateCredential(
            stateApp.dictBrowserSessions, sCredential,
        ) is True
        assert pageDashboard.listPageErrors == []
        assert pageDashboard.listConsoleErrors == []
    finally:
        # serverHub is module-scoped: leave no owner record behind for
        # the journeys that run after this one.
        stateApp.dictContainerOwners.pop(S_CONTAINER_NAME, None)
        stateApp.dictSessionOwner.pop(sOldSessionId, None)
        stateApp.dictSessionOwner.pop(recordOwner.sBrowserSessionId, None)


# ---------------------------------------------------------------------
# Journey -- the pre-expiry warning (design paragraph 11, slice 7)
# ---------------------------------------------------------------------


def testSessionCapWarningComesFromTheBackendAndReachesTheScreen(
    pageDashboard, serverHub,
):
    """A session near its absolute cap is warned, in backend numbers.

    The dashboard cannot know when its session ends; the server does.
    This drives the real path front to back: the page polls
    ``/api/session/lifetime``, the server answers from the session
    record's own creation stamp, and the researcher sees a toast
    naming the remaining minutes. Nothing here fakes the response --
    only the record's age is moved, exactly as a real day-long tab
    would move it.

    A fresh session must produce NO warning, so the toast cannot be a
    banner that is always on.
    """
    from vaibify.gui import sessionLifecycle
    stateApp = serverHub.app.state
    pageDashboard.goto(serverHub.fsBootstrapUrl(), wait_until="networkidle")
    sCredential = _fsPageCredential(pageDashboard)
    assert sCredential, "the page must have bootstrapped a credential"
    assert pageDashboard.locator(".toast").count() == 0, (
        "a fresh session must not be warned about its cap"
    )
    recordSession = stateApp.dictBrowserSessions[
        "dictSessionsByCredential"
    ][sCredential]
    fCreatedRestore = recordSession.fCreatedMonotonic
    try:
        # Age the record to five minutes short of the cap. The page is
        # told nothing; it re-polls and learns it from the server.
        recordSession.fCreatedMonotonic -= (
            sessionLifecycle.F_ABSOLUTE_SESSION_CAP_SECONDS - 300.0
        )
        pageDashboard.evaluate(
            "() => VaibifyPolling.fnStartSessionLifetimePolling()"
        )
        locatorToast = pageDashboard.locator(
            ".toast", has_text="maximum lifetime"
        )
        locatorToast.first.wait_for(state="visible", timeout=10000)
        sToastText = locatorToast.first.inner_text()
        assert "5 minutes" in sToastText, sToastText
        assert "vaibify open" in sToastText, (
            "the warning must name the re-attach path, not just the loss"
        )
        assert pageDashboard.listPageErrors == []
        assert pageDashboard.listConsoleErrors == []
    finally:
        recordSession.fCreatedMonotonic = fCreatedRestore


def testAnEndedSessionIsToldWhatHappenedNotThatTheServerRestarted(
    pageDashboard, serverHub,
):
    """The post-hoc notice, in a real browser, from a real 401.

    The pre-expiry warning above assumes somebody is watching. A cap
    that starts in the afternoon fires in the small hours, so the tab
    the researcher comes back to has already missed it -- and what it
    used to say was "Vaibify server has been restarted", which is a
    guess and, for this case, a false one: the hub is still running and
    ended the session on purpose.

    Nothing is faked. The session is revoked on the server the way an
    expiry revokes it, the page's own poll meets the real middleware
    401, and the toast must carry the server's sentence.
    """
    from vaibify.gui import browserSession
    stateApp = serverHub.app.state
    pageDashboard.goto(serverHub.fsBootstrapUrl(), wait_until="networkidle")
    sCredential = _fsPageCredential(pageDashboard)
    assert sCredential
    recordSession = stateApp.dictBrowserSessions[
        "dictSessionsByCredential"
    ][sCredential]

    browserSession.fbRevokeSessionById(
        stateApp.dictBrowserSessions, recordSession.sSessionId,
        sEndedMessage=(
            "This browser session reached its maximum lifetime (12h) "
            "and ended. Container 'demo' kept running and its work was "
            "retained. Run 'vaibify open' for a fresh tab, or raise "
            "the session cap in Settings."
        ),
    )
    pageDashboard.evaluate(
        "() => VaibifyPolling.fnStartSessionLifetimePolling()"
    )

    locatorToast = pageDashboard.locator(
        ".toast", has_text="maximum lifetime"
    )
    locatorToast.first.wait_for(state="visible", timeout=15000)
    sToastText = locatorToast.first.inner_text()
    assert "restarted" not in sToastText, sToastText
    assert "kept running" in sToastText, sToastText
    assert "vaibify open" in sToastText, (
        "a notice that does not name the recovery is only a nicer "
        "refusal"
    )
    assert "It ended at" in sToastText, (
        "the wall-clock time is the part the countdown could not "
        "deliver, because nobody was at the screen when it fired"
    )
    assert pageDashboard.listPageErrors == []


# ---------------------------------------------------------------------
# Journey -- a start reports its real outcome, not its acceptance
# ---------------------------------------------------------------------


def testStartReportsItsRealOutcomeAndNotTheAcceptedRequest(
    pageDashboard, serverHub,
):
    """The page must not call a container started when it is still starting.

    The start is a server-owned reservation (design §10b): the POST
    answers 202 and the outcome — with the container's lease — arrives
    only from the status poll. A page that toasted "Container started"
    on the 202 would tell the researcher a container is running while
    it is still pulling, or has already failed, which is exactly the
    class of dashboard lie this repository forbids.

    Driven front to back in a real browser against the real hub. Only
    the Docker create-then-start pair is substituted, and it is HELD
    OPEN on purpose so the "still starting" window is a real window:
    while it is held the page must show progress and the reservation
    must be live on the server; when it is released the page must show
    success and hold the lease the server derived.
    """
    import threading

    from vaibify.gui import startReservation

    stateApp = serverHub.app.state
    _fnWriteBrowserLaneProjectConfig(serverHub)
    eventRelease = threading.Event()
    fnRealExecutor = startReservation._fsExecuteReservedStart
    fnRealRunningList = serverHub.adapterDocker.flistGetRunningContainers

    def _fsHeldStart(sName, reservation, configProject):
        eventRelease.wait(timeout=30.0)
        return "browserLaneStartedContainerId"

    # The lane's fake lists the project container as RUNNING, which is
    # right for every other journey and is the one state a start may not
    # begin from: a start on a running container is refused outright, so
    # the premise has to be the honest pre-start one.
    serverHub.adapterDocker.flistGetRunningContainers = lambda: []
    startReservation._fsExecuteReservedStart = _fsHeldStart
    try:
        pageDashboard.goto(
            serverHub.fsBootstrapUrl(), wait_until="networkidle",
        )
        pageDashboard.evaluate(
            # Deliberately NOT awaited: the start is in flight for as
            # long as the held executor says, and `evaluate` resolves a
            # returned promise, which would block until it finished.
            "() => { VaibifyContainerManager.fnStartContainer('%s'); }"
            % S_CONTAINER_NAME
        )
        pageDashboard.locator(
            ".toast", has_text="Starting container"
        ).first.wait_for(state="visible", timeout=10000)
        assert pageDashboard.locator(
            ".toast", has_text="Container started"
        ).count() == 0, (
            "the page reported success while the start was still running"
        )
        recordOwner = stateApp.dictContainerOwners[S_CONTAINER_NAME]
        assert recordOwner.reservation is not None, (
            "the server must hold a live reservation while starting"
        )
        eventRelease.set()
        pageDashboard.locator(
            ".toast", has_text="Container started"
        ).first.wait_for(state="visible", timeout=15000)
        assert recordOwner.reservation is None
        sPageLease = pageDashboard.evaluate("() => VaibifyApp.fsGetLeaseId()")
        assert sPageLease == recordOwner.sLeaseId, (
            "the page must hold the lease the status poll derived from "
            "the live owner record"
        )
        assert pageDashboard.listPageErrors == []
        assert pageDashboard.listConsoleErrors == []
    finally:
        startReservation._fsExecuteReservedStart = fnRealExecutor
        serverHub.adapterDocker.flistGetRunningContainers = fnRealRunningList
        eventRelease.set()
        _fnReleaseBrowserLaneOwnership(stateApp)


def _fnWriteBrowserLaneProjectConfig(serverHub):
    """Write the seeded project's vaibify.yml so a start can load it."""
    pathProject = os.path.join(serverHub.sHome, "browserLaneProject")
    os.makedirs(pathProject, exist_ok=True)
    with open(os.path.join(pathProject, "vaibify.yml"), "w") as fileHandle:
        fileHandle.write(f"projectName: {S_CONTAINER_NAME}\n")


def _fnReleaseBrowserLaneOwnership(stateApp):
    """Drop the owner record this journey created, freeing its flock."""
    from vaibify.gui import containerOwnership
    recordOwner = stateApp.dictContainerOwners.get(S_CONTAINER_NAME)
    if recordOwner is None:
        return
    containerOwnership.fbReleaseOwnership(
        stateApp.dictContainerOwners, S_CONTAINER_NAME,
        recordOwner.sLeaseId,
        sBrowserSessionId=recordOwner.sBrowserSessionId,
        dictSessionOwner=stateApp.dictSessionOwner,
    )


# ---------------------------------------------------------------------
# Journey -- the terminal, in a real browser
# ---------------------------------------------------------------------


def testTheTerminalDialsItsSocketAndTheServerGatesIt(
    pageDashboard, serverHub,
):
    """The page opens a terminal socket, and an unclaimed dial is refused.

    Two halves that only mean something together. The server half opens
    a real ``/ws/terminal`` socket from the real browser with no claim
    behind it and reads the close code: it must be an AUTHORIZATION
    refusal, because that is what it is -- the feature exists and this
    caller has no standing in the container.

    The frontend half asserts the page DOES dial the lane for a
    container project when a command is sent into it — since the lazy
    dial (2026-08-17) the shell spawns on the researcher's first
    gesture or an explicit send, never on entry; the entry half of
    that contract is testTheShellDialsOnlyOnTheResearchersGesture.
    While the terminal was withdrawn this asserted the opposite, and
    the reason it did is worth keeping in view: a socket left to be
    refused surfaces a deliberate refusal as a connection failure.
    That is why the pane now names the deliberate close codes rather
    than printing "[Connection closed]" for all of them.

    Run in a real browser because a string search of the source cannot
    tell whether a module actually reaches for the socket at runtime.
    """
    pageDashboard.goto(serverHub.fsBootstrapUrl(), wait_until="networkidle")
    sCredential = _fsPageCredential(pageDashboard)

    iCloseCode = pageDashboard.evaluate(
        """(sCredential) => new Promise((fnResolve) => {
            const sProtocol =
                window.location.protocol === 'https:' ? 'wss:' : 'ws:';
            const ws = new WebSocket(
                sProtocol + '//' + window.location.host +
                '/ws/terminal/any-container-id?sToken=' +
                encodeURIComponent(sCredential) + '&sLeaseId=any-lease');
            ws.onclose = (event) => fnResolve(event.code);
            ws.onerror = () => fnResolve(-1);
        })""",
        sCredential,
    )
    from vaibify.gui import webSocketAuthorization
    assert iCloseCode in (
        webSocketAuthorization.I_REJECT_BAD_TOKEN,
        webSocketAuthorization.I_REJECT_FOREIGN_LEASE,
        webSocketAuthorization.I_REJECT_BAD_ORIGIN,
    ), (
        "a dial with no claim behind it must be refused BY THE GATE, "
        f"with an authorization code; got {iCloseCode}"
    )

    try:
        dictFrontend = pageDashboard.evaluate(
            """async ([sContainerId, sName]) => {
                const listUrls = [];
                const wsReal = window.WebSocket;
                function WebSocketFake(sUrl) {
                    listUrls.push(sUrl);
                    this.readyState = 0;
                    this.close = () => {};
                    this.send = () => {};
                    this.addEventListener = () => {};
                }
                WebSocketFake.CONNECTING = 0;
                WebSocketFake.OPEN = 1;
                WebSocketFake.CLOSING = 2;
                WebSocketFake.CLOSED = 3;
                window.WebSocket = WebSocketFake;
                try {
                    /* The real entry path: claim the container, then
                     * enter it -- entering is what builds the terminal
                     * strip (fnEnsureTab). Calling fnCreatePane alone
                     * would find no container id, return early, and
                     * prove nothing. */
                    const dictClaim = await VaibifyApi.fdictPost(
                        '/api/registry/' + encodeURIComponent(sName)
                        + '/claim', {});
                    VaibifyApp.fnRecordClaimedLease(
                        sName, dictClaim.sLeaseId);
                    await VaibifyApp.fnEnterNoWorkflow(sContainerId);
                    VaibifyTerminal.fnCreateTab();
                    const listRows = Array.from(document.querySelectorAll(
                        '#terminalStrip .xterm-rows'));
                    return {
                        listUrls: listUrls,
                        bTabOpened: listRows.length > 0,
                        bSendAccepted:
                            VaibifyTerminal.fbSendCommandInFreshTab(
                                'echo hello') === true,
                        sRowsText: listRows.map(
                            (el) => el.textContent).join(' '),
                    };
                } finally {
                    window.WebSocket = wsReal;
                }
            }""",
            [S_CONTAINER_ID, S_CONTAINER_NAME],
        )
    finally:
        _fnReleaseBrowserLaneOwnership(serverHub.app.state)

    assert dictFrontend["bTabOpened"], (
        "no terminal was actually opened, so the assertions below would "
        "pass vacuously"
    )
    listTerminalUrls = [
        sUrl for sUrl in dictFrontend["listUrls"] if "/ws/terminal" in sUrl
    ]
    assert listTerminalUrls != [], (
        "the frontend never dialled the terminal lane, so the pane "
        "shows a shell that is not connected to anything"
    )
    assert dictFrontend["bSendAccepted"], (
        "fbSendCommandInFreshTab reported False in a container, so an "
        "interactive step would refuse where a shell is available"
    )
    assert "runs on your own machine" not in (
        dictFrontend["sRowsText"].lower()
    ), (
        "the host notice was rendered in a container project: "
        f"{dictFrontend['sRowsText']!r}"
    )
    assert pageDashboard.listPageErrors == []
    # Entering a container also opens the Repos panel, whose sidecar
    # read the fail-closed fake does not model, so it answers 500. That
    # is a declared gap in the fake, not a frontend fault, and it is
    # named rather than tolerated silently: any console error that is
    # NOT a failed request -- a ReferenceError, say -- still fails here.
    listScriptErrors = [
        sError for sError in pageDashboard.listConsoleErrors
        if "Failed to load resource" not in sError
    ]
    assert listScriptErrors == [], listScriptErrors


def testTheShellDialsOnlyOnTheResearchersGesture(
    pageDashboard, serverHub,
):
    """Entering a workflow spawns no shell; the first gesture does.

    A shell is a quarantine-bearing operation: once one has run, the
    container's quiescence can only be proven, never assumed, and an
    unclean exit quarantines the container until ``vaibify
    reconcile``. The eager dial meant every workflow entry ran a
    shell nobody asked for — the 2026-08-14 quarantine was exactly
    that shell — so entry must leave the pane armed but silent, and
    the researcher's first mousedown into it is what dials.
    """
    pageDashboard.goto(serverHub.fsBootstrapUrl(), wait_until="networkidle")
    try:
        dictOutcome = pageDashboard.evaluate(
            """async ([sContainerId, sName]) => {
                const listUrls = [];
                const wsReal = window.WebSocket;
                function WebSocketFake(sUrl) {
                    listUrls.push(sUrl);
                    this.readyState = 0;
                    this.close = () => {};
                    this.send = () => {};
                    this.addEventListener = () => {};
                }
                WebSocketFake.CONNECTING = 0;
                WebSocketFake.OPEN = 1;
                WebSocketFake.CLOSING = 2;
                WebSocketFake.CLOSED = 3;
                window.WebSocket = WebSocketFake;
                function fiTerminalDials() {
                    return listUrls.filter(
                        (sUrl) => sUrl.includes('/ws/terminal')).length;
                }
                try {
                    const dictClaim = await VaibifyApi.fdictPost(
                        '/api/registry/' + encodeURIComponent(sName)
                        + '/claim', {});
                    VaibifyApp.fnRecordClaimedLease(
                        sName, dictClaim.sLeaseId);
                    await VaibifyApp.fnEnterNoWorkflow(sContainerId);
                    const iDialsAfterEntry = fiTerminalDials();
                    const elContainer = document.querySelector(
                        '#terminalStrip .terminal-pane-container');
                    elContainer.dispatchEvent(new MouseEvent(
                        'mousedown', {bubbles: true}));
                    const iDialsAfterClick = fiTerminalDials();
                    elContainer.dispatchEvent(new MouseEvent(
                        'mousedown', {bubbles: true}));
                    return {
                        iDialsAfterEntry: iDialsAfterEntry,
                        iDialsAfterClick: iDialsAfterClick,
                        iDialsAfterSecondClick: fiTerminalDials(),
                    };
                } finally {
                    window.WebSocket = wsReal;
                }
            }""",
            [S_CONTAINER_ID, S_CONTAINER_NAME],
        )
    finally:
        _fnReleaseBrowserLaneOwnership(serverHub.app.state)
    assert dictOutcome["iDialsAfterEntry"] == 0, (
        "entering the workflow dialled a terminal socket, which spawns "
        "a shell nobody asked for and stakes the quiescence claim"
    )
    assert dictOutcome["iDialsAfterClick"] == 1, (
        "the researcher's first gesture into the pane did not dial"
    )
    assert dictOutcome["iDialsAfterSecondClick"] == 1, (
        "a second gesture re-dialled an already-connected tab"
    )
    assert pageDashboard.listPageErrors == []


# ---------------------------------------------------------------------
# Journey -- a reload mid-start must not strand the researcher
# ---------------------------------------------------------------------


def testReloadDuringAStartPicksTheOutcomeBackUp(
    pageDashboard, browserChromium, serverHub,
):
    """A start outlives the page that asked for it, so the page resumes.

    The start is a server-owned reservation: the POST only reserves it,
    and the outcome AND the container's lease arrive from the status
    poll. A researcher who reloads while a multi-gigabyte image pulls
    therefore used to be stranded -- the server finished, and no page
    was listening. The tab remembers the pending start in
    sessionStorage, which survives a reload, and resumes the poll on
    load.

    The mechanism under test IS sessionStorage's scope, so it is driven
    both ways: the SAME browser context reloads and must recover, and a
    SEPARATE context is the negative control -- it must not adopt
    another tab's start, because sessionStorage is per tab and the lease
    is another session's.
    """
    import threading

    from vaibify.gui import startReservation

    stateApp = serverHub.app.state
    _fnWriteBrowserLaneProjectConfig(serverHub)
    eventRelease = threading.Event()
    fnRealExecutor = startReservation._fsExecuteReservedStart
    fnRealRunningList = serverHub.adapterDocker.flistGetRunningContainers

    def _fsHeldStart(sName, reservation, configProject):
        eventRelease.wait(timeout=30.0)
        return "browserLaneStartedContainerId"

    serverHub.adapterDocker.flistGetRunningContainers = lambda: []
    startReservation._fsExecuteReservedStart = _fsHeldStart
    pageControl = None
    try:
        pageDashboard.goto(
            serverHub.fsBootstrapUrl(), wait_until="networkidle",
        )
        pageDashboard.evaluate(
            "() => { VaibifyContainerManager.fnStartContainer('%s'); }"
            % S_CONTAINER_NAME
        )
        pageDashboard.locator(
            ".toast", has_text="Starting container"
        ).first.wait_for(state="visible", timeout=10000)
        assert pageDashboard.evaluate(
            "() => window.sessionStorage.getItem('vaibifyPendingStartName')"
        ) == S_CONTAINER_NAME, (
            "the tab did not record the start it is following, so a "
            "reload has nothing to resume"
        )

        # The negative control, opened while the start is still in
        # flight: a different browser context has neither the
        # sessionStorage marker nor the owning session.
        contextControl = browserChromium.new_context()
        pageControl = contextControl.new_page()
        pageControl.goto(
            serverHub.fsBootstrapUrl(), wait_until="networkidle",
        )
        assert pageControl.evaluate(
            "() => window.sessionStorage.getItem('vaibifyPendingStartName')"
        ) is None, (
            "a separate browser context adopted another tab's start"
        )

        pageDashboard.reload(wait_until="networkidle")
        eventRelease.set()

        pageDashboard.locator(
            ".toast", has_text="Container started"
        ).first.wait_for(state="visible", timeout=20000)
        recordOwner = stateApp.dictContainerOwners[S_CONTAINER_NAME]
        sPageLease = pageDashboard.evaluate(
            "() => VaibifyApp.fsGetLeaseId()"
        )
        assert sPageLease == recordOwner.sLeaseId, (
            "the reloaded page did not recover the lease the start "
            "produced, so it owns a container it cannot act on"
        )
        assert pageDashboard.evaluate(
            "() => window.sessionStorage.getItem('vaibifyPendingStartName')"
        ) is None, "the resumed start was never cleared"
        assert pageDashboard.listPageErrors == []
    finally:
        startReservation._fsExecuteReservedStart = fnRealExecutor
        serverHub.adapterDocker.flistGetRunningContainers = fnRealRunningList
        eventRelease.set()
        if pageControl is not None:
            pageControl.context.close()
        _fnReleaseBrowserLaneOwnership(stateApp)


def testAStartSurvivesATransientStatusPollFailure(
    pageDashboard, serverHub,
):
    """A single transient /start-status error must not abandon a start.

    The status poll runs while a page is still settling -- most sharply
    the RESUMED poll on a just-reloaded tab, whose first request can
    fail before the session is re-established. The pre-fix loop
    abandoned the whole start on that one failure, stranding a running
    container and flaking the reload-resume journey on loaded CI. The
    poll now tolerates a bounded run of transient errors.

    Deterministic and adversarial: the FIRST /start-status is aborted
    exactly once, then let through. Without the tolerance the follow
    throws on that abort and no "Container started" toast ever appears;
    the assertion that the abort actually fired keeps the test honest.
    """
    from vaibify.gui import startReservation

    stateApp = serverHub.app.state
    _fnWriteBrowserLaneProjectConfig(serverHub)
    fnRealExecutor = startReservation._fsExecuteReservedStart
    fnRealRunningList = serverHub.adapterDocker.flistGetRunningContainers

    def _fsImmediateStart(sName, reservation, configProject):
        return "browserLaneStartedContainerId"

    serverHub.adapterDocker.flistGetRunningContainers = lambda: []
    startReservation._fsExecuteReservedStart = _fsImmediateStart

    dictRoute = {"iSeen": 0}

    def _fnAbortFirstStatusPoll(route):
        dictRoute["iSeen"] += 1
        if dictRoute["iSeen"] == 1:
            route.abort("failed")
        else:
            route.continue_()

    try:
        pageDashboard.goto(
            serverHub.fsBootstrapUrl(), wait_until="networkidle",
        )
        pageDashboard.route("**/start-status", _fnAbortFirstStatusPoll)
        pageDashboard.evaluate(
            "() => { VaibifyContainerManager.fnStartContainer('%s'); }"
            % S_CONTAINER_NAME
        )
        pageDashboard.locator(
            ".toast", has_text="Container started"
        ).first.wait_for(state="visible", timeout=20000)
        assert dictRoute["iSeen"] >= 2, (
            "the transient failure never fired, so the retry path was "
            "not exercised"
        )
        assert pageDashboard.listPageErrors == []
    finally:
        startReservation._fsExecuteReservedStart = fnRealExecutor
        serverHub.adapterDocker.flistGetRunningContainers = fnRealRunningList
        _fnReleaseBrowserLaneOwnership(stateApp)
