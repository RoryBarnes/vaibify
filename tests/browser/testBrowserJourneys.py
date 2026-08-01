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
