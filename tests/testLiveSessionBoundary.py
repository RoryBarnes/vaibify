"""One-session guarantees driven across the SERVED application.

``testContainerSessionResolution`` already drives the WebSocket gates,
but on a bespoke ``FastAPI()`` with one or two routes hand-registered on
it. That proves the gate function behaves; it does not prove the
application a researcher actually talks to is wired to it. This file
closes that gap: every test below builds the real viewer application
through ``pipelineServer.fappCreateApplication``, mints the lease by
POSTing to ``/api/connect`` exactly as the browser does, and then opens
a real WebSocket with that lease.

Two rules, both learned the hard way (see AGENTS.md "Lessons"):

* the container NAME and the container ID are kept distinct, because the
  owner-of-record map is name-keyed while every URL carries the id, and
  a fixture with ``name == id`` once hid a bug that would have closed
  every real session;
* the lease is never fabricated where the browser would not fabricate
  it -- it comes back from the connect response, so the HTTP lane and
  the WebSocket lane are proven to agree on one credential.

Only the innermost serve function of each lane is stubbed (there is no
container to attach to). The origin check, the per-browser credential
check, the bound-lease check, the id->name resolution and the
per-container connection budget all run for real.
"""

from unittest.mock import AsyncMock, patch

import pytest
from fastapi import WebSocket
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from vaibify.gui import pipelineServer
from vaibify.gui import webSocketAuthorization
from tests.sessionTokenTestHelper import fsBootstrapCredential
from tests.testAgentLaneEnforcement import (
    MockDockerConnection,
    S_CONTAINER_ID,
    S_CONTAINER_NAME,
    S_WORKFLOW_PATH,
)


_DICT_LOOPBACK_ORIGIN = {"origin": "http://localhost"}


@pytest.fixture
def appServed():
    """Build the real viewer application over a mocked Docker."""
    with patch.object(
        pipelineServer, "_fconnectionCreateDocker",
        MockDockerConnection,
    ):
        return pipelineServer.fappCreateApplication(
            sWorkspaceRoot="/workspace", sTerminalUserArg="testuser",
        )


@pytest.fixture
def sBrowserCredential(appServed):
    """Mint a real per-browser credential from the served app's store."""
    return fsBootstrapCredential(appServed)


@pytest.fixture
def clientBrowser(appServed, sBrowserCredential):
    """A client authenticated as the researcher's browser.

    Presents a real per-browser credential redeemed from the app's own
    bootstrap store -- exactly what the browser holds after ``/api/bootstrap``
    -- so ``/api/connect`` binds the viewer's ownership to this session.
    """
    return TestClient(
        appServed,
        headers={"X-Session-Token": sBrowserCredential},
    )


def _fsConnectAndReturnLease(clientBrowser):
    """POST /api/connect and return the lease the server minted."""
    responseHttp = clientBrowser.post(
        f"/api/connect/{S_CONTAINER_ID}",
        params={"sWorkflowPath": S_WORKFLOW_PATH},
    )
    assert responseHttp.status_code == 200, responseHttp.text
    sLeaseId = responseHttp.json()["sLeaseId"]
    assert sLeaseId, "connect must hand the browser a lease"
    return sLeaseId


def _fsPipelineUrl(sCredential, sLeaseId):
    """Build the pipeline WebSocket URL, addressed by the docker ID."""
    return (
        f"/ws/pipeline/{S_CONTAINER_ID}"
        f"?sToken={sCredential}&sLeaseId={sLeaseId}"
    )


def _fsTerminalUrl(sCredential, sLeaseId):
    """Build the terminal WebSocket URL, addressed by the docker ID."""
    return (
        f"/ws/terminal/{S_CONTAINER_ID}"
        f"?sToken={sCredential}&sLeaseId={sLeaseId}"
    )


I_CLOSE_NORMAL = 1000


async def _fnAcceptAndCloseNormally(
    websocket,
    dictCtx,
    sContainerId,
    **dictUnused,
):
    """Stand-in serve function that closes as soon as it is reached.

    Used where the test asserts a REFUSAL code: if the gate ever admits
    the connection, the socket must still close promptly and with an
    ordinary code, so a broken gate surfaces as the WRONG close code
    rather than as a hung suite.
    """
    await websocket.accept()
    await websocket.close(code=I_CLOSE_NORMAL)


def _fnRegisterUnbudgetedLaneWs(app):
    """Add a test-owned socket on the UNBUDGETED lane to the served app.

    The interactive terminal was the production holder of an unbudgeted
    socket; it is withdrawn for the alpha, so the lane has no production
    caller left. The budget itself is unchanged and the lane returns when
    the containment boundary can be proven, so the coexistence property
    is driven here through the real
    ``fnServeUnderLiveConnectionCounters`` on the real served
    application, over a real connection, rather than left unasserted.

    The lane flag is deliberately omitted so the DEFAULT is what is
    under test: flipping it to ``True`` budgets every socket, which is
    the Run-Step-always-refused regression.
    """
    @app.websocket("/ws/unbudgeted/{sContainerId}")
    async def fnUnbudgetedWs(websocket: WebSocket, sContainerId: str):
        async def fnServe():
            await websocket.accept()
            try:
                await websocket.receive_text()
            except WebSocketDisconnect:
                pass

        await webSocketAuthorization.fnServeUnderLiveConnectionCounters(
            websocket, app.state.dictContainerOwners, S_CONTAINER_NAME,
            fnServe, lambda: None, lambda: None,
            dictBrowserSessions=app.state.dictBrowserSessions,
        )


def _fsUnbudgetedUrl(sCredential, sLeaseId):
    """Build the test-owned unbudgeted-lane URL, addressed by docker ID."""
    return (
        f"/ws/unbudgeted/{S_CONTAINER_ID}"
        f"?sToken={sCredential}&sLeaseId={sLeaseId}"
    )


@pytest.mark.falsification
def testConnectMintsTheLeaseThatOpensThePipelineWebSocket(
    appServed, clientBrowser, sBrowserCredential,
):
    """The lease connect returns must authorize the pipeline socket.

    The full round trip a browser performs: claim the container over
    HTTP, then present the same lease over the WebSocket. The URL
    carries the docker ID while the owner record is filed under the
    container NAME, so the socket opens only if the route resolves one
    to the other -- the exact name-vs-id defect that a ``name == id``
    fixture hid behind a green suite.

    Kills: pipelineRoutes._fnRegisterPipelineWs: the id->name resolution
    `sName = fsContainerNameForId(` replaced by `sName = sContainerId`.
    """
    sLeaseId = _fsConnectAndReturnLease(clientBrowser)
    assert S_CONTAINER_NAME in appServed.state.dictContainerOwners
    assert S_CONTAINER_ID not in appServed.state.dictContainerOwners

    listPipelineCountsWhileServing = []

    async def _fnRecordAndServe(
        websocket,
        dictCtx,
        sContainerId,
        **dictUnused,
    ):
        await websocket.accept()
        listPipelineCountsWhileServing.append(
            appServed.state.dictContainerOwners[S_CONTAINER_NAME]
            .iLivePipelineConnectionCount
        )

    with patch(
        "vaibify.gui.routes.pipelineRoutes.fnHandlePipelineWs",
        _fnRecordAndServe,
    ):
        with clientBrowser.websocket_connect(
            _fsPipelineUrl(sBrowserCredential, sLeaseId),
            headers=_DICT_LOOPBACK_ORIGIN,
        ):
            pass
    assert listPipelineCountsWhileServing == [1], (
        "the lease minted by /api/connect must open the pipeline "
        "WebSocket on the served application and register as the one "
        "live pipeline connection"
    )


@pytest.mark.falsification
def testPipelineWebSocketRefusesALeaseConnectNeverMinted(
    appServed, clientBrowser, sBrowserCredential,
):
    """A lease the server never issued is refused 4403, after accept.

    A second tab that guesses or copies a stale lease must not reach the
    pipeline. The refusal is observed on receive because the handshake
    completes first: closing before ``accept`` would reach the browser
    as an opaque 1006, indistinguishable from a dead server.

    Kills: webSocketAuthorization.fiContainerSessionRejectionCode: the
    lease branch `if not fbCheckBoundLeaseOwnership(connection,
    dictContainerOwners, sName, sBrowserSessionId):` neutralized to
    `if False:`.

    The serve function is stubbed to return immediately so that an
    admitted socket closes normally instead of blocking the suite: a
    broken gate must show up as a wrong close CODE, never as a hang.
    """
    _fsConnectAndReturnLease(clientBrowser)
    with patch(
        "vaibify.gui.routes.pipelineRoutes.fnHandlePipelineWs",
        _fnAcceptAndCloseNormally,
    ):
        with clientBrowser.websocket_connect(
            _fsPipelineUrl(sBrowserCredential, "a-lease-nobody-minted"),
            headers=_DICT_LOOPBACK_ORIGIN,
        ) as websocketForeign:
            with pytest.raises(WebSocketDisconnect) as excInfo:
                websocketForeign.receive_text()
    assert excInfo.value.code == 4403, (
        "a lease the server never minted must be closed 4403; "
        f"got {excInfo.value.code}"
    )


@pytest.mark.falsification
def testDuplicateTabPipelineWebSocketIsRefusedOnTheServedApplication(
    appServed, clientBrowser, sBrowserCredential,
):
    """A copied lease opening a second pipeline socket is refused 4409.

    Two live pipeline sockets would let two tabs drive runs into one
    container concurrently. The duplicate clears the lease gate -- it
    holds the real lease -- and must be stopped by the per-container
    lane budget instead, with the first socket still being served.

    Kills: webSocketAuthorization.fnServeUnderLiveConnectionCounters:
    the budget branch `if bBrowser and bExclusivePipelineLane and
    fbRefuseSecondLiveConnection(` neutralized to `if False:`.

    The first socket is held open by its serve stub; any socket that
    reaches serve AFTER it closes normally, so a lifted budget shows up
    as the wrong close code instead of hanging the suite.
    """
    sLeaseId = _fsConnectAndReturnLease(clientBrowser)
    listReachedServe = []

    async def _fnHoldFirstCloseRest(
        websocket,
        dictCtx,
        sContainerId,
        **dictUnused,
    ):
        await websocket.accept()
        listReachedServe.append(sContainerId)
        if len(listReachedServe) > 1:
            await websocket.close(code=I_CLOSE_NORMAL)
            return
        try:
            await websocket.receive_text()
        except WebSocketDisconnect:
            pass

    with patch(
        "vaibify.gui.routes.pipelineRoutes.fnHandlePipelineWs",
        _fnHoldFirstCloseRest,
    ):
        with clientBrowser.websocket_connect(
            _fsPipelineUrl(sBrowserCredential, sLeaseId),
            headers=_DICT_LOOPBACK_ORIGIN,
        ):
            with clientBrowser.websocket_connect(
                _fsPipelineUrl(sBrowserCredential, sLeaseId),
                headers=_DICT_LOOPBACK_ORIGIN,
            ) as websocketDuplicate:
                with pytest.raises(WebSocketDisconnect) as excInfo:
                    websocketDuplicate.receive_text()
            assert excInfo.value.code == 4409, (
                "a second concurrent pipeline socket on the same lease "
                f"must be closed 4409; got {excInfo.value.code}"
            )
    assert listReachedServe == [S_CONTAINER_ID], (
        "only the first pipeline socket may reach the serve function"
    )


@pytest.mark.falsification
def testTerminalAndPipelineWebSocketsCoexistOnTheServedApplication(
    appServed, clientBrowser, sBrowserCredential,
):
    """One session holds an unbudgeted AND the pipeline socket at once.

    This is Run Step as the GUI drove it before the terminal was
    withdrawn: the terminal strip opened its socket on workflow entry
    and was still open when the pipeline socket arrived. Budgeting every
    socket instead of only the pipeline lane shipped the
    Run-Step-always-refused bug, where the terminal held the single slot
    and every run was closed 4409 and mislabelled "cannot reach server".
    The socket now comes from a test-owned route on that lane, because
    the terminal route refuses every caller; the wrapper, the served
    application, and the connection are all real.

    Kills: webSocketAuthorization.fnServeUnderLiveConnectionCounters:
    the signature default `bExclusivePipelineLane=False,` flipped to
    `bExclusivePipelineLane=True,`, which budgets every other lane too.
    """
    sLeaseId = _fsConnectAndReturnLease(clientBrowser)
    _fnRegisterUnbudgetedLaneWs(appServed)
    listCountsWhilePipelineServes = []

    async def _fnRecordAndServe(
        websocket,
        dictCtx,
        sContainerId,
        **dictUnused,
    ):
        await websocket.accept()
        recordOwner = (
            appServed.state.dictContainerOwners[S_CONTAINER_NAME]
        )
        listCountsWhilePipelineServes.append((
            recordOwner.iLiveConnectionCount,
            recordOwner.iLivePipelineConnectionCount,
        ))

    with patch(
        "vaibify.gui.routes.pipelineRoutes.fnHandlePipelineWs",
        _fnRecordAndServe,
    ):
        with clientBrowser.websocket_connect(
            _fsUnbudgetedUrl(sBrowserCredential, sLeaseId),
            headers=_DICT_LOOPBACK_ORIGIN,
        ):
            with clientBrowser.websocket_connect(
                _fsPipelineUrl(sBrowserCredential, sLeaseId),
                headers=_DICT_LOOPBACK_ORIGIN,
            ):
                pass
    assert listCountsWhilePipelineServes == [(2, 1)], (
        "with the unbudgeted socket live, the same session's pipeline "
        "socket must be served -- two live connections, one of them on "
        "the pipeline lane -- never refused as a duplicate tab"
    )
    recordOwner = appServed.state.dictContainerOwners[S_CONTAINER_NAME]
    assert recordOwner.iLiveConnectionCount == 0
    assert recordOwner.iLivePipelineConnectionCount == 0


def testTerminalDoesNotSpendThePipelineBudgetOnTheServedApplication(
    appServed, clientBrowser, sBrowserCredential,
):
    """A terminal must not consume the one-pipeline-per-container slot.

    Driven end to end -- claim over HTTP, then dial the terminal with
    the lease that claim returned -- so the property is observed
    through the same application a browser talks to, not a route
    registered in isolation. While the terminal was withdrawn this
    asserted the withdrawal code instead; the terminal serves again,
    and what matters on the served app is the budget.

    Budgeting all sockets is what shipped the Run-Step-always-refused
    bug: the terminal opens on workflow entry, held the only slot, and
    every Run Step came back 4409 mislabelled "cannot reach server".
    """
    sLeaseId = _fsConnectAndReturnLease(clientBrowser)
    recordOwner = appServed.state.dictContainerOwners[S_CONTAINER_NAME]
    with patch(
        "vaibify.gui.routes.terminalRoutes._fnStartAndRunTerminal",
        new=AsyncMock(),
    ):
        with clientBrowser.websocket_connect(
            _fsTerminalUrl(sBrowserCredential, sLeaseId),
            headers=_DICT_LOOPBACK_ORIGIN,
        ):
            pass
    assert recordOwner.iLivePipelineConnectionCount == 0, (
        "the terminal spent the per-container pipeline budget; every "
        "Run Step in this container would be refused 4409"
    )


@pytest.mark.parametrize("fsBuildUrl", [_fsPipelineUrl])
def testSecondSessionCopiedLeaseIsRefusedOnBothSockets(
    appServed, clientBrowser, sBrowserCredential, fsBuildUrl,
):
    """A second browser session presenting the owner's real lease is refused.

    Session A claims the container (its credential binds the owner record).
    Session B redeems its OWN valid credential but copies A's genuine lease
    value onto the socket URL. The credential is valid and the lease value
    is real, yet the bound-lease gate ties the lease to A's session, so B is
    refused 4403 -- the copied-lease replay the strong predicate exists to
    stop, proven across a real connection with the container NAME distinct
    from its ID.

    The terminal socket is no longer a parameter: it refuses EVERY
    caller with the withdrawal code, so running it here would assert
    the withdrawal rather than the bound-lease gate and would go green
    for the wrong reason. ``testWithdrawnTerminalRefusesTheOwnerOnThe
    ServedApplication`` covers it directly instead.
    """
    sLeaseId = _fsConnectAndReturnLease(clientBrowser)
    sCredentialSessionB = fsBootstrapCredential(appServed)
    assert sCredentialSessionB != sBrowserCredential
    clientSessionB = TestClient(
        appServed, headers={"X-Session-Token": sCredentialSessionB},
    )
    with patch(
        "vaibify.gui.routes.pipelineRoutes.fnHandlePipelineWs",
        _fnAcceptAndCloseNormally,
    ):
        with clientSessionB.websocket_connect(
            fsBuildUrl(sCredentialSessionB, sLeaseId),
            headers=_DICT_LOOPBACK_ORIGIN,
        ) as websocketSessionB:
            with pytest.raises(WebSocketDisconnect) as excInfo:
                websocketSessionB.receive_text()
    assert excInfo.value.code == 4403, (
        "a second session presenting the owner's copied lease must be "
        f"closed 4403; got {excInfo.value.code}"
    )
