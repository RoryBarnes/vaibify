"""The orphan transition and its trigger (design §4/§5, slice 6).

Drives ``sessionLifecycle.fnOrphanSession`` and the zero-sockets
orphaning trigger against real stores: a real browser-session store
(so revocation and capability cancellation are observable), real
connection records driven through the real increment/decrement
counters, the real reaper loop, and the real agent-lane middleware.
The container NAME stays distinct from the Docker ID throughout (repo
epistemics rule).
"""

import time
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from vaibify.config import containerLock, operationJournal
from vaibify.gui import (
    browserSession,
    containerOwnership,
    pipelineServer,
    serverLifespan,
    serverMiddleware,
    sessionLifecycle,
    webSocketAuthorization,
)

S_PROJECT_NAME = "SampleProject"
S_CONTAINER_ID = "cid-0123456789ab"
F_GRACE_SECONDS = 30.0


@pytest.fixture(autouse=True)
def fixtureIsolateJournalAndLockDirectories(tmp_path, monkeypatch):
    """Keep the journal and flock directories out of ~/.vaibify."""
    monkeypatch.setattr(
        operationJournal, "_S_JOURNAL_DIRECTORY", str(tmp_path / "journal"),
    )
    monkeypatch.setattr(
        containerLock, "_S_LOCK_DIRECTORY", str(tmp_path / "locks"),
    )


class _FakeWebSocketConnection:
    """A stand-in socket that records its active close codes."""

    def __init__(self):
        self.listCloseCodes = []

    async def close(self, code=1000):
        self.listCloseCodes.append(code)


def _fstateBuildAppState():
    """Return a bare app.state stand-in with every lifecycle store."""
    return SimpleNamespace(
        bReapOwnerships=True,
        dictContainerOwners={},
        dictSessionOwner=containerOwnership.fdictCreateSessionOwnerIndex(),
        dictSessionSockets=(
            containerOwnership.fdictCreateSessionSocketIndex()
        ),
        dictBrowserSessions=browserSession.fdictCreateBrowserSessionStore(),
        dictMutationSupervisors={},
        dictDurableTaskRecords={},
        dictTerminalExecutionRecords={},
    )


def _tSeedOwnedContainer(stateApp):
    """Seed an ACTIVE owned container bound to a REAL browser session.

    Returns ``(sSessionId, sCredential, sCapability)``; the session
    comes from a genuine bootstrap redemption so its revocation and
    its capability's cancellation are observable in the real store.
    """
    sCapability = browserSession.fsMintBootstrapCapability(
        stateApp.dictBrowserSessions,
    )
    sSessionId, sCredential = browserSession.ftRedeemCapability(
        stateApp.dictBrowserSessions, sCapability,
    )
    recordOwner = containerOwnership.OwnerRecord(
        sLeaseId=containerOwnership.fsMintLease(),
        fileHandleLock=None,
        sAgentToken=containerOwnership.fsMintAgentToken(),
        sContainerId=S_CONTAINER_ID,
        sBrowserSessionId=sSessionId,
    )
    stateApp.dictContainerOwners[S_PROJECT_NAME] = recordOwner
    stateApp.dictSessionOwner[sSessionId] = S_PROJECT_NAME
    return (sSessionId, sCredential, sCapability)


def _recordRegisterLiveSocket(stateApp, sSessionId, bPipelineLane=True):
    """Open one real-shaped live socket on the seeded owner record."""
    containerOwnership.fnIncrementLiveConnection(
        stateApp.dictContainerOwners, S_PROJECT_NAME,
        bPipelineLane=bPipelineLane,
    )
    recordConnection = containerOwnership.ConnectionRecord(
        websocket=_FakeWebSocketConnection(),
        sBrowserSessionId=sSessionId,
        iOwnerGeneration=1,
        sLane=(
            containerOwnership.S_LANE_PIPELINE if bPipelineLane
            else containerOwnership.S_LANE_TERMINAL
        ),
    )
    containerOwnership.fnRegisterSessionSocket(
        stateApp.dictSessionSockets, recordConnection,
    )
    return recordConnection


def _fnCloseLiveSocket(stateApp, recordConnection):
    """Drive the socket's ``finally`` path: decrement and deregister."""
    containerOwnership.fnDecrementLiveConnectionForRecord(
        stateApp.dictContainerOwners, S_PROJECT_NAME, recordConnection,
    )
    containerOwnership.fnDeregisterSessionSocket(
        stateApp.dictSessionSockets, recordConnection,
    )


# -- fnOrphanSession: the §5 commit -----------------------------------------


@pytest.mark.asyncio
async def testOrphanSessionRevokesStampsClosesAndRetainsTheRecord():
    """The full §5 commit: revoke, cancel, stamp, close — retain.

    The credential authorizes nothing afterwards, the bootstrap
    capability's bounded replay is cancelled, every live socket is
    actively closed with 4401, and the record keeps its flock slot,
    lease, agent token, generation, and cardinality entry.
    """
    stateApp = _fstateBuildAppState()
    sSessionId, sCredential, sCapability = _tSeedOwnedContainer(stateApp)
    recordConnection = _recordRegisterLiveSocket(stateApp, sSessionId)
    recordOwner = stateApp.dictContainerOwners[S_PROJECT_NAME]
    sLeaseBefore = recordOwner.sLeaseId
    sAgentTokenBefore = recordOwner.sAgentToken
    await sessionLifecycle.fnOrphanSession(stateApp, S_PROJECT_NAME)
    assert recordOwner.sState == (
        containerOwnership.S_OWNER_STATE_ORPHANED_SESSION
    )
    assert recordOwner.fOrphanedSinceMonotonic > 0.0
    assert browserSession.fbValidateCredential(
        stateApp.dictBrowserSessions, sCredential,
    ) is False, "the orphaned session's credential must authorize nothing"
    assert browserSession.ftRedeemCapability(
        stateApp.dictBrowserSessions, sCapability,
    ) == (None, None), (
        "the bootstrap capability's bounded replay must be cancelled"
    )
    assert recordConnection.websocket.listCloseCodes == [4401], (
        "the session's live socket must be actively closed"
    )
    assert stateApp.dictContainerOwners[S_PROJECT_NAME] is recordOwner
    assert recordOwner.sLeaseId == sLeaseBefore
    assert recordOwner.sAgentToken == sAgentTokenBefore
    assert recordOwner.iOwnerGeneration == 1
    assert stateApp.dictSessionOwner == {sSessionId: S_PROJECT_NAME}, (
        "orphaning retains ownership; only release/reap drops the entry"
    )


@pytest.mark.asyncio
async def testOrphanSessionIsIdempotentAndHonoursTheRecheck():
    """A second orphan is a no-op; a failed recheck skips the commit."""
    stateApp = _fstateBuildAppState()
    _tSeedOwnedContainer(stateApp)
    recordOwner = stateApp.dictContainerOwners[S_PROJECT_NAME]
    await sessionLifecycle.fnOrphanSession(
        stateApp, S_PROJECT_NAME,
        fbStillWarranted=lambda recordAny: False,
    )
    assert recordOwner.sState == containerOwnership.S_OWNER_STATE_ACTIVE, (
        "a recheck that fails under the lock must skip the transition"
    )
    await sessionLifecycle.fnOrphanSession(stateApp, S_PROJECT_NAME)
    fFirstStamp = recordOwner.fOrphanedSinceMonotonic
    assert fFirstStamp > 0.0
    await sessionLifecycle.fnOrphanSession(stateApp, S_PROJECT_NAME)
    assert recordOwner.fOrphanedSinceMonotonic == fFirstStamp, (
        "orphaning an already-orphaned record must be a no-op"
    )


@pytest.mark.asyncio
async def testOrphanSessionToleratesAnUnknownContainer():
    """Orphaning an unowned name commits nothing and raises nothing."""
    stateApp = _fstateBuildAppState()
    await sessionLifecycle.fnOrphanSession(stateApp, "AbsentProject")
    assert stateApp.dictContainerOwners == {}


# -- the §4 zero-sockets trigger --------------------------------------------


@pytest.mark.asyncio
async def testOrphanTriggerFiresOnlyPastTheReconnectWindow():
    """A real socket open/close cycle orphans only past the window."""
    stateApp = _fstateBuildAppState()
    sSessionId, sCredential, _ = _tSeedOwnedContainer(stateApp)
    recordOwner = stateApp.dictContainerOwners[S_PROJECT_NAME]
    recordConnection = _recordRegisterLiveSocket(stateApp, sSessionId)
    _fnCloseLiveSocket(stateApp, recordConnection)
    recordOwner.fLastSeenMonotonic = time.monotonic() - (
        sessionLifecycle.F_RECONNECT_WINDOW_SECONDS + 1.0
    )
    await sessionLifecycle.fnOrphanOwnersPastReconnectWindow(stateApp)
    assert recordOwner.sState == (
        containerOwnership.S_OWNER_STATE_ORPHANED_SESSION
    )
    assert browserSession.fbValidateCredential(
        stateApp.dictBrowserSessions, sCredential,
    ) is False


@pytest.mark.falsification
@pytest.mark.asyncio
async def testReloadReconnectWithinWindowRetainsOwnership():
    """A reload's socket gap inside the window never orphans.

    Case 10 (design §4): the last socket drops (a reload tearing down
    its WebSocket), the trigger runs within the reconnect window, and
    the record stays ACTIVE with its lease intact; after the reload's
    socket reconnects, even an ancient last-seen stamp cannot orphan,
    because a live socket vetoes the trigger outright. A live durable
    task rides through untouched either way.

    Kills: measuring the reconnect window against zero seconds (orphan
    immediately on the last socket close) in
    ``sessionLifecycle._fbOwnerPastReconnectWindow``.
    """
    stateApp = _fstateBuildAppState()
    sSessionId, sCredential, _ = _tSeedOwnedContainer(stateApp)
    recordTask = SimpleNamespace(
        sTaskId="task-live", sState="running",
        taskAsync=SimpleNamespace(done=lambda: False),
    )
    stateApp.dictDurableTaskRecords[S_PROJECT_NAME] = recordTask
    recordOwner = stateApp.dictContainerOwners[S_PROJECT_NAME]
    sLeaseBefore = recordOwner.sLeaseId
    recordConnection = _recordRegisterLiveSocket(stateApp, sSessionId)
    _fnCloseLiveSocket(stateApp, recordConnection)
    await sessionLifecycle.fnOrphanOwnersPastReconnectWindow(stateApp)
    assert recordOwner.sState == (
        containerOwnership.S_OWNER_STATE_ACTIVE
    ), "a socket gap inside the reconnect window must not orphan"
    _recordRegisterLiveSocket(stateApp, sSessionId)
    recordOwner.fLastSeenMonotonic = time.monotonic() - 99999.0
    await sessionLifecycle.fnOrphanOwnersPastReconnectWindow(stateApp)
    assert recordOwner.sState == containerOwnership.S_OWNER_STATE_ACTIVE
    assert recordOwner.sLeaseId == sLeaseBefore
    assert browserSession.fbValidateCredential(
        stateApp.dictBrowserSessions, sCredential,
    ) is True
    assert stateApp.dictDurableTaskRecords[S_PROJECT_NAME] is recordTask


@pytest.mark.falsification
@pytest.mark.asyncio
async def testTerminalLaneSocketVetoesTheOrphanTrigger():
    """Per-lane counting: any live socket vetoes, not just pipeline.

    Case 18 (design §4): closing ONE terminal socket while the
    pipeline socket lives leaves the record ACTIVE, and a session
    holding ONLY a terminal socket (pipeline count zero) is likewise
    never orphaned — the trigger counts every browser lane.

    Kills: counting only the pipeline lane
    (``iLivePipelineConnectionCount``) in the trigger predicate
    ``sessionLifecycle._fbOwnerPastReconnectWindow``.
    """
    stateApp = _fstateBuildAppState()
    sSessionId, _, _ = _tSeedOwnedContainer(stateApp)
    recordOwner = stateApp.dictContainerOwners[S_PROJECT_NAME]
    _recordRegisterLiveSocket(stateApp, sSessionId, bPipelineLane=True)
    recordTerminal = _recordRegisterLiveSocket(
        stateApp, sSessionId, bPipelineLane=False,
    )
    _fnCloseLiveSocket(stateApp, recordTerminal)
    recordOwner.fLastSeenMonotonic = time.monotonic() - 99999.0
    await sessionLifecycle.fnOrphanOwnersPastReconnectWindow(stateApp)
    assert recordOwner.sState == (
        containerOwnership.S_OWNER_STATE_ACTIVE
    ), "closing one terminal must not orphan while the pipeline lives"
    # Now the terminal-only shape: pipeline gone, one terminal open.
    stateTerminalOnly = _fstateBuildAppState()
    sOtherSessionId, _, _ = _tSeedOwnedContainer(stateTerminalOnly)
    recordOther = stateTerminalOnly.dictContainerOwners[S_PROJECT_NAME]
    _recordRegisterLiveSocket(
        stateTerminalOnly, sOtherSessionId, bPipelineLane=False,
    )
    recordOther.fLastSeenMonotonic = time.monotonic() - 99999.0
    await sessionLifecycle.fnOrphanOwnersPastReconnectWindow(
        stateTerminalOnly,
    )
    assert recordOther.sState == containerOwnership.S_OWNER_STATE_ACTIVE, (
        "a live terminal-only session must not be orphaned"
    )


@pytest.mark.asyncio
async def testClaimThatNeverOpenedASocketFallsToTheIdleWindow():
    """``bSocketEverExisted`` gates the trigger (claim-then-crash)."""
    stateApp = _fstateBuildAppState()
    _tSeedOwnedContainer(stateApp)
    recordOwner = stateApp.dictContainerOwners[S_PROJECT_NAME]
    assert recordOwner.bSocketEverExisted is False
    recordOwner.fLastSeenMonotonic = time.monotonic() - 99999.0
    await sessionLifecycle.fnOrphanOwnersPastReconnectWindow(stateApp)
    assert recordOwner.sState == containerOwnership.S_OWNER_STATE_ACTIVE, (
        "a claim with no socket ever falls to the idle reap window, "
        "never the orphan trigger"
    )


# -- the reaper, driven through the REAL orphan transition ------------------


@pytest.mark.falsification
@pytest.mark.asyncio
async def testReapGraceMeasuresFromTheRealOrphanTransition():
    """The reap grace starts at the REAL orphan commit's stamp.

    Case 7, orphan-transition half (design §7, extending the slice-5
    hand-set-state coverage): a record whose last socket died long ago
    but which the trigger orphaned only moments ago is NOT reapable —
    the grace measures from the commit's ``fOrphanedSinceMonotonic``
    stamp — and becomes reapable only once that stamp ages past the
    grace with a stale agent stamp.

    Kills: dropping the ``fOrphanedSinceMonotonic`` stamp from
    ``sessionLifecycle._flistCommitOrphanSynchronously``.
    """
    stateApp = _fstateBuildAppState()
    sSessionId, _, _ = _tSeedOwnedContainer(stateApp)
    recordOwner = stateApp.dictContainerOwners[S_PROJECT_NAME]
    recordConnection = _recordRegisterLiveSocket(stateApp, sSessionId)
    _fnCloseLiveSocket(stateApp, recordConnection)
    recordOwner.fLastSeenMonotonic = time.monotonic() - 99999.0
    await sessionLifecycle.fnOrphanOwnersPastReconnectWindow(stateApp)
    assert recordOwner.sState == (
        containerOwnership.S_OWNER_STATE_ORPHANED_SESSION
    )
    assert containerOwnership.fbOwnerIsReapable(
        recordOwner, F_GRACE_SECONDS,
    ) is False, (
        "the reap grace must run from the orphan commit, not the "
        "long-dead last socket"
    )
    recordOwner.fOrphanedSinceMonotonic = time.monotonic() - (
        F_GRACE_SECONDS + 1.0
    )
    assert containerOwnership.fbOwnerIsReapable(
        recordOwner, F_GRACE_SECONDS,
    ) is True


def _appBuildAgentReaperApplication():
    """A real application whose one container is agent-owned."""
    from tests.testAgentLaneEnforcement import MockDockerConnection
    with patch.object(
        pipelineServer, "_fconnectionCreateDocker", MockDockerConnection,
    ):
        app = pipelineServer.fappCreateApplication(
            sWorkspaceRoot="/workspace", sTerminalUserArg="testuser",
        )
    app.state.bReapOwnerships = True
    sCapability = browserSession.fsMintBootstrapCapability(
        app.state.dictBrowserSessions,
    )
    sSessionId, _ = browserSession.ftRedeemCapability(
        app.state.dictBrowserSessions, sCapability,
    )
    app.state.dictContainerOwners[S_PROJECT_NAME] = (
        containerOwnership.OwnerRecord(
            sLeaseId=containerOwnership.fsMintLease(),
            fileHandleLock=None,
            sAgentToken="agent-token-for-orphan-tests",
            sContainerId=S_CONTAINER_ID,
            sBrowserSessionId=sSessionId,
        )
    )
    return app


@pytest.mark.falsification
@pytest.mark.asyncio
async def testOrphanedRecordWithLiveAgentRestActivityIsNotReaped():
    """A live in-container agent pins a REALLY-orphaned record.

    Case 13 (design §7): the record is orphaned through the real
    ``fnOrphanSession`` transition, its orphan stamp is aged past the
    grace, and a REAL admitted agent REST call (no socket) through the
    real middleware refreshes the activity stamp — the reaper loop
    retains the record; once the agent stamp goes stale, the same loop
    releases it.

    Kills: dropping the agent-activity-stamp condition from the
    orphan-record reapability predicate in
    ``containerOwnership._fbOrphanedRecordIsReapable``.
    """
    app = _appBuildAgentReaperApplication()
    recordOwner = app.state.dictContainerOwners[S_PROJECT_NAME]
    await sessionLifecycle.fnOrphanSession(app.state, S_PROJECT_NAME)
    recordOwner.fOrphanedSinceMonotonic = time.monotonic() - (
        F_GRACE_SECONDS + 1.0
    )
    clientAgent = TestClient(app, headers={
        "X-Vaibify-Session": "agent-token-for-orphan-tests",
        "Host": "host.docker.internal:8050",
    })
    assert clientAgent.get(
        f"/api/pipeline/{S_CONTAINER_ID}/state",
    ).status_code == 200
    dictCtx = {
        "docker": SimpleNamespace(flistGetRunningContainers=lambda: []),
    }
    serverLifespan._fnReapIdleOwnershipsForApp(
        SimpleNamespace(state=app.state), dictCtx,
    )
    assert S_PROJECT_NAME in app.state.dictContainerOwners, (
        "an orphaned record with fresh agent REST activity was reaped"
    )
    recordOwner.fLastAgentActivityMonotonic = time.monotonic() - (
        F_GRACE_SECONDS + 1.0
    )
    serverLifespan._fnReapIdleOwnershipsForApp(
        SimpleNamespace(state=app.state), dictCtx,
    )
    assert S_PROJECT_NAME not in app.state.dictContainerOwners


@pytest.mark.falsification
@pytest.mark.asyncio
async def testInFlightAgentRequestPinsARealOrphanedRecordInTheReaperLoop():
    """The in-flight bracket pins a really-orphaned record mid-call.

    Case 20, slice-6 half (design §7, extending the slice-5 bracket
    coverage onto the real transition): a LONG admitted agent request
    outlives its admission-time activity stamp, so mid-dispatch — the
    stamp aged past the grace, the record orphaned through the real
    ``fnOrphanSession``, every other clock stale — only the in-flight
    bracket pins the record, and the reaper loop must retain it for
    the call's whole duration.

    Kills: neutralizing the in-flight increment in
    ``serverMiddleware._fresponseServeAdmittedAgentRequest``.
    """
    stateApp = _fstateBuildAppState()
    _tSeedOwnedContainer(stateApp)
    recordOwner = stateApp.dictContainerOwners[S_PROJECT_NAME]
    recordOwner.sAgentToken = "agent-token-in-flight"
    await sessionLifecycle.fnOrphanSession(stateApp, S_PROJECT_NAME)
    recordOwner.fOrphanedSinceMonotonic = time.monotonic() - 99999.0
    dictCtx = {
        "docker": SimpleNamespace(flistGetRunningContainers=lambda: []),
    }

    class FakeUrl:
        path = f"/api/pipeline/{S_CONTAINER_ID}/state"

    requestFake = SimpleNamespace(
        headers={"x-vaibify-session": "agent-token-in-flight"},
        url=FakeUrl(),
        query_params={},
    )
    dictObserved = {}

    async def fnProbeReaperMidDispatch(request):
        # The long call's admission stamp has aged past the grace by
        # the time the reaper next runs; only the bracket pins now.
        recordOwner.fLastAgentActivityMonotonic = time.monotonic() - (
            F_GRACE_SECONDS + 1.0
        )
        serverLifespan._fnReapIdleOwnershipsForApp(
            SimpleNamespace(state=stateApp), dictCtx,
        )
        dictObserved["bRetainedMidCall"] = (
            S_PROJECT_NAME in stateApp.dictContainerOwners
        )
        return "served"

    sResult = await serverMiddleware._fresponseServeAdmittedAgentRequest(
        requestFake, stateApp.dictContainerOwners,
        fnProbeReaperMidDispatch,
    )
    assert sResult == "served"
    assert dictObserved["bRetainedMidCall"] is True, (
        "the reaper released an orphaned record while an admitted "
        "agent request was mid-flight"
    )


# -- the per-frame re-auth backstop (design §5) ------------------------------


def _connectionBuildBrowserConnection(sCredential):
    """A loopback-origin connection presenting a browser credential."""
    return SimpleNamespace(
        headers={"origin": "http://127.0.0.1:8050"},
        query_params={"sToken": sCredential},
    )


def testPerFrameCheckTracksTheCredentialAndExemptsTheAgentLane():
    """The built check follows revocation; the agent lane is constant."""
    dictStore = browserSession.fdictCreateBrowserSessionStore()
    sCapability = browserSession.fsMintBootstrapCapability(dictStore)
    sSessionId, sCredential = browserSession.ftRedeemCapability(
        dictStore, sCapability,
    )
    fbCheck = webSocketAuthorization.ffnBuildPerFrameCredentialCheck(
        _connectionBuildBrowserConnection(sCredential), dictStore,
    )
    assert fbCheck() is True
    browserSession.fbRevokeSessionById(dictStore, sSessionId)
    assert fbCheck() is False, (
        "a revoked session's frames must stop authorizing"
    )
    connectionAgent = SimpleNamespace(
        headers={}, query_params={"sToken": "agent-token-value"},
    )
    fbAgentCheck = webSocketAuthorization.ffnBuildPerFrameCredentialCheck(
        connectionAgent, dictStore,
    )
    assert fbAgentCheck() is True, (
        "the agent lane carries no browser credential to re-check"
    )


class _FakePipelineWebSocket:
    """Feeds queued text frames; records closes and sent events."""

    def __init__(self, listFrames):
        self.listFrames = list(listFrames)
        self.listCloseCodes = []
        self.listSentJson = []

    async def receive_text(self):
        return self.listFrames.pop(0)

    async def close(self, code=1000):
        self.listCloseCodes.append(code)

    async def send_json(self, dictEvent):
        self.listSentJson.append(dictEvent)


@pytest.mark.falsification
@pytest.mark.asyncio
async def testPipelineFrameFromARevokedSessionIsRefusedNotDispatched():
    """A frame already in flight at revocation is refused, not run.

    The §5 per-frame backstop behind the active close: a pipeline
    frame whose session was revoked between accept and receive is
    answered with close 4401 and never dispatched.

    Kills: neutralizing the per-frame credential check in
    ``pipelineServer.fnPipelineMessageLoop``.
    """
    websocketFake = _FakePipelineWebSocket(['{"sAction": "runAll"}'])
    await pipelineServer.fnPipelineMessageLoop(
        websocketFake, None, S_CONTAINER_ID,
        {"listSteps": []}, {}, "",
        fbFrameCredentialStillActive=lambda: False,
    )
    assert websocketFake.listCloseCodes == [4401], (
        "the revoked session's frame must be refused with 4401"
    )
    assert websocketFake.listSentJson == [], (
        "no event may be dispatched for a refused frame"
    )


class _FakeTerminalWebSocket:
    """Feeds queued terminal messages; records closes."""

    def __init__(self, listMessages):
        self.listMessages = list(listMessages)
        self.listCloseCodes = []

    async def receive(self):
        return self.listMessages.pop(0)

    async def close(self, code=1000):
        self.listCloseCodes.append(code)


@pytest.mark.falsification
@pytest.mark.asyncio
async def testTerminalKeystrokeFromARevokedSessionIsRefused():
    """A revoked session's keystroke never reaches the container.

    The terminal half of the §5 per-frame backstop: input arriving
    after revocation is refused with close 4401 and is not forwarded
    to the terminal session.

    Kills: neutralizing the per-frame credential check in
    ``pipelineServer.fnTerminalInputLoop``.
    """
    listForwarded = []
    sessionFake = SimpleNamespace(
        fnSendInput=lambda baInput: listForwarded.append(baInput),
    )
    websocketFake = _FakeTerminalWebSocket([{"bytes": b"rm -rf /\n"}])
    await pipelineServer.fnTerminalInputLoop(
        sessionFake, websocketFake,
        fbFrameCredentialStillActive=lambda: False,
    )
    assert websocketFake.listCloseCodes == [4401]
    assert listForwarded == [], (
        "a revoked session's keystroke must never reach the container"
    )
