"""Session expiry, the absolute cap, and the ~5 s evaluator (design §11).

Drives ``sessionLifecycle.fnExpireIdleBrowserSessions`` and
``fnEvaluateSessionLifecycle`` against a REAL browser-session store and
real owner records, and drives the evaluator's scheduling through the
real lifespan start/stop hooks. The container NAME stays distinct from
the Docker ID throughout (repo epistemics rule), and every clock is
moved by rewriting a record's monotonic stamps — never by sleeping.
"""

import asyncio
import time
from types import SimpleNamespace

import pytest

from vaibify.config import containerLock, operationJournal
from vaibify.gui import (
    browserSession,
    containerOwnership,
    serverLifespan,
    sessionLifecycle,
)

S_PROJECT_NAME = "SampleProject"
S_CONTAINER_ID = "cid-9876543210ff"


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


def _tMintBrowserSession(stateApp):
    """Redeem a real bootstrap capability; return (sSessionId, sCredential)."""
    sCapability = browserSession.fsMintBootstrapCapability(
        stateApp.dictBrowserSessions,
    )
    return browserSession.ftRedeemCapability(
        stateApp.dictBrowserSessions, sCapability,
    )


def _recordSeedOwnedContainer(stateApp, sSessionId):
    """Seed an ACTIVE owner record bound to an existing browser session."""
    recordOwner = containerOwnership.OwnerRecord(
        sLeaseId=containerOwnership.fsMintLease(),
        fileHandleLock=None,
        sAgentToken=containerOwnership.fsMintAgentToken(),
        sContainerId=S_CONTAINER_ID,
        sBrowserSessionId=sSessionId,
    )
    stateApp.dictContainerOwners[S_PROJECT_NAME] = recordOwner
    stateApp.dictSessionOwner[sSessionId] = S_PROJECT_NAME
    return recordOwner


def _recordSessionForCredential(stateApp, sCredential):
    """Return the live BrowserSessionRecord behind a credential."""
    return stateApp.dictBrowserSessions[
        "dictSessionsByCredential"
    ][sCredential]


def _fnAgeSessionPastSlidingIdle(stateApp, sCredential):
    """Rewind a credential's last-seen stamp past the sliding-idle window."""
    recordSession = _recordSessionForCredential(stateApp, sCredential)
    recordSession.fLastSeenMonotonic = time.monotonic() - (
        sessionLifecycle.F_SLIDING_IDLE_SECONDS + 1.0
    )


def _fnAgeSessionBy(stateApp, sCredential, fSeconds):
    """Rewind a credential's CREATION stamp — the absolute-cap clock."""
    recordSession = _recordSessionForCredential(stateApp, sCredential)
    recordSession.fCreatedMonotonic = time.monotonic() - fSeconds


def _recordOpenLiveSocket(stateApp, sSessionId):
    """Open one real-shaped live pipeline socket on the owner record."""
    containerOwnership.fnIncrementLiveConnection(
        stateApp.dictContainerOwners, S_PROJECT_NAME, bPipelineLane=True,
    )
    recordConnection = containerOwnership.ConnectionRecord(
        connection=_FakeWebSocketConnection(),
        sBrowserSessionId=sSessionId,
        iOwnerGeneration=1,
        sLane=containerOwnership.S_LANE_PIPELINE,
    )
    containerOwnership.fnRegisterSessionSocket(
        stateApp.dictSessionSockets, recordConnection,
    )
    return recordConnection


# -- the ownerless half of the sweep ----------------------------------------


@pytest.mark.asyncio
async def testIdleSessionWithNoOwnerIsRevoked():
    """A picker-only session past the idle window loses its credential."""
    stateApp = _fstateBuildAppState()
    sSessionId, sCredential = _tMintBrowserSession(stateApp)
    _fnAgeSessionPastSlidingIdle(stateApp, sCredential)
    await sessionLifecycle.fnExpireIdleBrowserSessions(stateApp)
    assert browserSession.fbValidateCredential(
        stateApp.dictBrowserSessions, sCredential,
    ) is False, "an expired ownerless session must be revoked"
    assert stateApp.dictContainerOwners == {}
    assert sSessionId not in stateApp.dictSessionOwner


@pytest.mark.asyncio
async def testSessionInsideTheIdleWindowIsUntouched():
    """A session still inside its window keeps authorizing."""
    stateApp = _fstateBuildAppState()
    _, sCredential = _tMintBrowserSession(stateApp)
    await sessionLifecycle.fnExpireIdleBrowserSessions(stateApp)
    assert browserSession.fbValidateCredential(
        stateApp.dictBrowserSessions, sCredential,
    ) is True


# -- the owning half: orphan, never a bare revoke ---------------------------


@pytest.mark.falsification
@pytest.mark.asyncio
async def testExpiredOwningSessionIsOrphanedNotBareRevoked():
    """An expired session that OWNS a container is orphaned (§11).

    A bare revoke would strand an ACTIVE record whose owner can no
    longer authenticate: the reaper's ORPHANED conditions would never
    match it and no browser could ever release it. The commit must be
    the real ``fnOrphanSession`` transition — credential revoked AND
    the record moved to ORPHANED_SESSION with its stamp — while the
    lease, agent token, generation, and cardinality entry are retained.

    Kills: committing an expired owning session with a bare
    ``fnRevokeSessionById`` instead of ``fnOrphanSession`` in
    ``sessionLifecycle._fnCommitSessionExpiry``.
    """
    stateApp = _fstateBuildAppState()
    sSessionId, sCredential = _tMintBrowserSession(stateApp)
    recordOwner = _recordSeedOwnedContainer(stateApp, sSessionId)
    sLeaseBefore = recordOwner.sLeaseId
    sAgentTokenBefore = recordOwner.sAgentToken
    _fnAgeSessionPastSlidingIdle(stateApp, sCredential)
    await sessionLifecycle.fnExpireIdleBrowserSessions(stateApp)
    assert recordOwner.sState == (
        containerOwnership.S_OWNER_STATE_ORPHANED_SESSION
    ), "an expired OWNING session must be orphaned, not bare-revoked"
    assert recordOwner.fOrphanedSinceMonotonic > 0.0, (
        "a stranded ACTIVE record carries no orphan stamp, so the "
        "reaper's ORPHANED conditions can never release it"
    )
    assert browserSession.fbValidateCredential(
        stateApp.dictBrowserSessions, sCredential,
    ) is False
    assert stateApp.dictContainerOwners[S_PROJECT_NAME] is recordOwner
    assert recordOwner.sLeaseId == sLeaseBefore
    assert recordOwner.sAgentToken == sAgentTokenBefore
    assert recordOwner.iOwnerGeneration == 1
    assert stateApp.dictSessionOwner == {sSessionId: S_PROJECT_NAME}


@pytest.mark.asyncio
async def testExpiredSessionBoundElsewhereIsRevokedWithoutTouchingTheOwner():
    """A stale reverse-index entry may not orphan a successor's record."""
    stateApp = _fstateBuildAppState()
    sSessionId, sCredential = _tMintBrowserSession(stateApp)
    sSuccessorId, _ = _tMintBrowserSession(stateApp)
    recordOwner = _recordSeedOwnedContainer(stateApp, sSessionId)
    # A transfer rebound the record; the stale index still names the
    # expired session.
    recordOwner.sBrowserSessionId = sSuccessorId
    _fnAgeSessionPastSlidingIdle(stateApp, sCredential)
    await sessionLifecycle.fnExpireIdleBrowserSessions(stateApp)
    assert browserSession.fbValidateCredential(
        stateApp.dictBrowserSessions, sCredential,
    ) is False
    assert recordOwner.sState == containerOwnership.S_OWNER_STATE_ACTIVE, (
        "the successor's record must not be orphaned on a stale "
        "predecessor's behalf"
    )


# -- the live-socket veto ---------------------------------------------------


@pytest.mark.falsification
@pytest.mark.asyncio
async def testLiveWebSocketVetoesSlidingIdle():
    """A quiet-but-connected socket is activity (§11).

    The socket layer never refreshes the credential's last-seen stamp,
    so a dashboard that only streams pipeline events looks idle to the
    sweep. A live connection vetoes sliding idle outright; once that
    socket closes, the same stale stamp expires the session.

    Kills: dropping the live-connection veto from
    ``sessionLifecycle._fbBrowserSessionHasExpired``.
    """
    stateApp = _fstateBuildAppState()
    sSessionId, sCredential = _tMintBrowserSession(stateApp)
    recordOwner = _recordSeedOwnedContainer(stateApp, sSessionId)
    recordConnection = _recordOpenLiveSocket(stateApp, sSessionId)
    _fnAgeSessionPastSlidingIdle(stateApp, sCredential)
    await sessionLifecycle.fnExpireIdleBrowserSessions(stateApp)
    assert recordOwner.sState == containerOwnership.S_OWNER_STATE_ACTIVE, (
        "a live WebSocket must veto sliding-idle expiry"
    )
    assert browserSession.fbValidateCredential(
        stateApp.dictBrowserSessions, sCredential,
    ) is True
    assert recordConnection.connection.listCloseCodes == []
    # fbValidateCredential above refreshed the stamp; age it again now
    # that the socket is gone.
    containerOwnership.fnDecrementLiveConnectionForRecord(
        stateApp.dictContainerOwners, S_PROJECT_NAME, recordConnection,
    )
    containerOwnership.fnDeregisterSessionSocket(
        stateApp.dictSessionSockets, recordConnection,
    )
    _fnAgeSessionPastSlidingIdle(stateApp, sCredential)
    await sessionLifecycle.fnExpireIdleBrowserSessions(stateApp)
    assert recordOwner.sState == (
        containerOwnership.S_OWNER_STATE_ORPHANED_SESSION
    ), "with the socket gone the same stale stamp must expire"


# -- the absolute cap (slice 7) ---------------------------------------------


@pytest.mark.falsification
@pytest.mark.asyncio
async def testAbsoluteCapFiresDespiteALiveWebSocket():
    """The cap overrides the socket veto (§11), and orphans an owner.

    The socket veto is scoped to sliding idle ALONE. A forgotten-open
    tab — the sole case the absolute cap exists to bound — holds a
    live socket by definition, so a veto generalized to all three
    triggers would make the cap unreachable in exactly its target
    case. Here the session is fully "active": a live pipeline socket
    and a last-seen stamp refreshed a moment ago. Only its age has run
    out, and that must be enough — committed through the orphan
    transition, since the session owns a container.

    Kills: letting a live WebSocket veto the absolute cap as well as
    sliding idle in ``sessionLifecycle._fbBrowserSessionHasExpired``.
    """
    stateApp = _fstateBuildAppState()
    sSessionId, sCredential = _tMintBrowserSession(stateApp)
    recordOwner = _recordSeedOwnedContainer(stateApp, sSessionId)
    recordConnection = _recordOpenLiveSocket(stateApp, sSessionId)
    _fnAgeSessionBy(
        stateApp, sCredential,
        sessionLifecycle.F_ABSOLUTE_SESSION_CAP_SECONDS + 1.0,
    )
    assert recordOwner.iLiveConnectionCount == 1
    await sessionLifecycle.fnExpireIdleBrowserSessions(stateApp)
    assert recordOwner.sState == (
        containerOwnership.S_OWNER_STATE_ORPHANED_SESSION
    ), "the absolute cap must fire regardless of socket liveness"
    assert browserSession.fbValidateCredential(
        stateApp.dictBrowserSessions, sCredential,
    ) is False
    assert recordConnection.connection.listCloseCodes == [4401], (
        "the capped session's live socket must be actively closed"
    )
    assert stateApp.dictContainerOwners[S_PROJECT_NAME] is recordOwner, (
        "the cap ends the browser session's authority, not the record"
    )


@pytest.mark.asyncio
async def testSessionInsideTheAbsoluteCapWithALiveSocketSurvives():
    """An aged-but-not-capped session with a socket keeps authorizing."""
    stateApp = _fstateBuildAppState()
    sSessionId, sCredential = _tMintBrowserSession(stateApp)
    recordOwner = _recordSeedOwnedContainer(stateApp, sSessionId)
    _recordOpenLiveSocket(stateApp, sSessionId)
    _fnAgeSessionBy(
        stateApp, sCredential,
        sessionLifecycle.F_ABSOLUTE_SESSION_CAP_SECONDS - 60.0,
    )
    _fnAgeSessionPastSlidingIdle(stateApp, sCredential)
    await sessionLifecycle.fnExpireIdleBrowserSessions(stateApp)
    assert recordOwner.sState == containerOwnership.S_OWNER_STATE_ACTIVE
    assert browserSession.fbValidateCredential(
        stateApp.dictBrowserSessions, sCredential,
    ) is True


# -- the pre-expiry warning's backend truth ---------------------------------


@pytest.mark.falsification
def testExpiryViewCountsDownTheCapForThePresentingSessionOnly():
    """The warning's payload is derived from the session's own record.

    The countdown is the ABSOLUTE CAP's remaining lifetime measured
    from the presenting credential's creation stamp — the deadline
    that has no socket veto — and it crosses ``bExpiringSoon`` exactly
    at the configured lead. A credential the store does not know, or
    one already revoked, is answered ``bSessionKnown`` False with a
    zero countdown, never another session's clocks.

    Kills: reporting the sliding-idle clock instead of the
    absolute-cap clock in ``sessionLifecycle.fdictSessionExpiryView``
    — a countdown toward a deadline a live socket forbids.
    """
    stateApp = _fstateBuildAppState()
    _, sCredential = _tMintBrowserSession(stateApp)
    dictFresh = sessionLifecycle.fdictSessionExpiryView(
        stateApp, sCredential,
    )
    assert dictFresh["bSessionKnown"] is True
    assert dictFresh["bExpiringSoon"] is False
    assert dictFresh["fWarningLeadSeconds"] == (
        sessionLifecycle.F_EXPIRY_WARNING_LEAD_SECONDS
    )
    assert dictFresh["fSecondsUntilSessionCap"] == pytest.approx(
        sessionLifecycle.F_ABSOLUTE_SESSION_CAP_SECONDS, abs=5.0,
    )
    # Idle for longer than the whole warning lead, but young: the
    # sliding clock must NOT be what the countdown reports.
    _fnAgeSessionPastSlidingIdle(stateApp, sCredential)
    assert sessionLifecycle.fdictSessionExpiryView(
        stateApp, sCredential,
    )["bExpiringSoon"] is False, (
        "the countdown must track the capped deadline, not idleness"
    )
    _fnAgeSessionBy(
        stateApp, sCredential,
        sessionLifecycle.F_ABSOLUTE_SESSION_CAP_SECONDS
        - sessionLifecycle.F_EXPIRY_WARNING_LEAD_SECONDS + 60.0,
    )
    dictWarned = sessionLifecycle.fdictSessionExpiryView(
        stateApp, sCredential,
    )
    assert dictWarned["bExpiringSoon"] is True
    assert 0.0 < dictWarned["fSecondsUntilSessionCap"] <= (
        sessionLifecycle.F_EXPIRY_WARNING_LEAD_SECONDS
    )
    dictUnknown = sessionLifecycle.fdictSessionExpiryView(
        stateApp, "not-a-credential",
    )
    assert dictUnknown == {
        "bSessionKnown": False,
        "fSecondsUntilSessionCap": 0.0,
        "fWarningLeadSeconds": (
            sessionLifecycle.F_EXPIRY_WARNING_LEAD_SECONDS
        ),
        "bExpiringSoon": False,
    }


def testExpiryViewRefusesToExtendTheSessionItReports():
    """Reading remaining lifetime must not refresh the idle clock."""
    stateApp = _fstateBuildAppState()
    _, sCredential = _tMintBrowserSession(stateApp)
    _fnAgeSessionPastSlidingIdle(stateApp, sCredential)
    fStampBefore = _recordSessionForCredential(
        stateApp, sCredential,
    ).fLastSeenMonotonic
    sessionLifecycle.fdictSessionExpiryView(stateApp, sCredential)
    assert _recordSessionForCredential(
        stateApp, sCredential,
    ).fLastSeenMonotonic == fStampBefore, (
        "a lifetime read must not itself extend the lifetime"
    )


def testRevokedCredentialLearnsNothingFromTheExpiryView():
    """A revoked session's credential reads as unknown."""
    stateApp = _fstateBuildAppState()
    sSessionId, sCredential = _tMintBrowserSession(stateApp)
    browserSession.fnRevokeSessionById(
        stateApp.dictBrowserSessions, sSessionId,
    )
    assert sessionLifecycle.fdictSessionExpiryView(
        stateApp, sCredential,
    )["bSessionKnown"] is False


# -- the evaluator pass and its scheduling ----------------------------------


@pytest.mark.asyncio
async def testEvaluatorPassRunsBothTheOrphanTriggerAndTheSweep():
    """One pass orphans a socket-less owner AND revokes an idle session."""
    stateApp = _fstateBuildAppState()
    sOwnerSessionId, sOwnerCredential = _tMintBrowserSession(stateApp)
    recordOwner = _recordSeedOwnedContainer(stateApp, sOwnerSessionId)
    recordConnection = _recordOpenLiveSocket(stateApp, sOwnerSessionId)
    containerOwnership.fnDecrementLiveConnectionForRecord(
        stateApp.dictContainerOwners, S_PROJECT_NAME, recordConnection,
    )
    containerOwnership.fnDeregisterSessionSocket(
        stateApp.dictSessionSockets, recordConnection,
    )
    recordOwner.fLastSeenMonotonic = time.monotonic() - (
        sessionLifecycle.F_RECONNECT_WINDOW_SECONDS + 1.0
    )
    _, sPickerCredential = _tMintBrowserSession(stateApp)
    _fnAgeSessionPastSlidingIdle(stateApp, sPickerCredential)
    await sessionLifecycle.fnEvaluateSessionLifecycle(stateApp)
    assert recordOwner.sState == (
        containerOwnership.S_OWNER_STATE_ORPHANED_SESSION
    ), "the evaluator must run the zero-sockets orphan trigger"
    assert browserSession.fbValidateCredential(
        stateApp.dictBrowserSessions, sOwnerCredential,
    ) is False
    assert browserSession.fbValidateCredential(
        stateApp.dictBrowserSessions, sPickerCredential,
    ) is False, "the evaluator must run the session sweep too"


def _fappBuildEvaluatorApplication(stateApp):
    """Wrap a lifecycle app.state in a lifespan-registrable app stub."""
    stateApp.listLifespanStartup = []
    stateApp.listLifespanShutdown = []
    return SimpleNamespace(state=stateApp)


def testEvaluatorLoopTicksOnItsOwnCadenceAndStopsAtShutdown():
    """The registered evaluator runs passes, then cancels cleanly."""
    stateApp = _fstateBuildAppState()
    sSessionId, sCredential = _tMintBrowserSession(stateApp)
    _recordSeedOwnedContainer(stateApp, sSessionId)
    _fnAgeSessionPastSlidingIdle(stateApp, sCredential)
    app = _fappBuildEvaluatorApplication(stateApp)
    serverLifespan._fnRegisterSessionLifecycleEvaluator(
        app, fInterval=0.01,
    )

    async def fnDrive():
        for fnStartup in app.state.listLifespanStartup:
            await fnStartup(app)
        for _ in range(200):
            await asyncio.sleep(0.01)
            if stateApp.dictContainerOwners[S_PROJECT_NAME].sState == (
                containerOwnership.S_OWNER_STATE_ORPHANED_SESSION
            ):
                break
        for fnShutdown in app.state.listLifespanShutdown:
            await fnShutdown(app)
        return app.state.taskSessionLifecycleEvaluator

    taskEvaluator = asyncio.run(fnDrive())
    assert stateApp.dictContainerOwners[S_PROJECT_NAME].sState == (
        containerOwnership.S_OWNER_STATE_ORPHANED_SESSION
    ), "the scheduled evaluator never committed an expiry"
    assert taskEvaluator.done(), (
        "the evaluator task must be cancelled by the lifespan shutdown"
    )


def testEvaluatorLoopSurvivesAFailingPass():
    """One raising pass is logged and the loop keeps ticking."""
    stateApp = _fstateBuildAppState()
    app = _fappBuildEvaluatorApplication(stateApp)
    listCalls = []

    async def fnFailThenSucceed(stateAny):
        listCalls.append(stateAny)
        if len(listCalls) == 1:
            raise RuntimeError("transient evaluator failure")

    async def fnDrive():
        taskLoop = asyncio.create_task(
            serverLifespan._fnSessionLifecycleEvaluatorLoop(app, 0.01),
        )
        for _ in range(200):
            await asyncio.sleep(0.01)
            if len(listCalls) >= 3:
                break
        taskLoop.cancel()
        try:
            await taskLoop
        except asyncio.CancelledError:
            pass

    import unittest.mock
    with unittest.mock.patch.object(
        sessionLifecycle, "fnEvaluateSessionLifecycle", fnFailThenSucceed,
    ):
        asyncio.run(fnDrive())
    assert len(listCalls) >= 3, (
        "a single failed pass must not terminate the evaluator loop"
    )


def _appBuildRealApplication():
    """Build the real application over the fail-closed Docker mock."""
    from unittest.mock import patch
    from tests.testAgentLaneEnforcement import MockDockerConnection
    from vaibify.gui import pipelineServer
    with patch.object(
        pipelineServer, "_fconnectionCreateDocker", MockDockerConnection,
    ):
        return pipelineServer.fappCreateApplication(
            sWorkspaceRoot="/workspace", sTerminalUserArg="testuser",
        )


def testLifetimeRouteServesTheOwnSessionAndRefusesTheAgentLane():
    """The route is browser-only and reports the presenter's own clocks.

    Two refusals stack. The route names no container, so the agent
    lane's per-container token authorizes nothing on it and the
    middleware never admits it: the agent's request falls through to
    the browser-credential check and is answered 401. It is refused
    again at the handler (see the sibling test), which is what holds
    if a future path shape ever admits the lane.
    """
    from fastapi.testclient import TestClient
    app = _appBuildRealApplication()
    sCapability = browserSession.fsMintBootstrapCapability(
        app.state.dictBrowserSessions,
    )
    _, sCredential = browserSession.ftRedeemCapability(
        app.state.dictBrowserSessions, sCapability,
    )
    clientBrowser = TestClient(app, headers={
        "X-Session-Token": sCredential,
    })
    responseBrowser = clientBrowser.get("/api/session/lifetime")
    assert responseBrowser.status_code == 200
    dictPayload = responseBrowser.json()
    assert dictPayload["bSessionKnown"] is True
    assert dictPayload["bExpiringSoon"] is False
    assert dictPayload["fSecondsUntilSessionCap"] > 0.0
    app.state.dictContainerOwners[S_PROJECT_NAME] = (
        containerOwnership.OwnerRecord(
            sLeaseId=containerOwnership.fsMintLease(),
            fileHandleLock=None,
            sAgentToken="agent-token-for-lifetime-tests",
            sContainerId=S_CONTAINER_ID,
            sBrowserSessionId="",
        )
    )
    clientAgent = TestClient(app, headers={
        "X-Vaibify-Session": "agent-token-for-lifetime-tests",
        "Host": "host.docker.internal:8050",
    })
    assert clientAgent.get(
        "/api/session/lifetime",
    ).status_code == 401, (
        "the in-container agent holds no browser session and must be "
        "refused, never handed a session's remaining lifetime"
    )


def testLifetimeHandlerRefusesAnAgentTokenOnItsOwn():
    """The handler's own agent refusal, with no middleware above it."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from vaibify.gui.routes.sessionRoutes import fnRegisterAll
    app = FastAPI()
    fnRegisterAll(app, {})
    app.state.dictBrowserSessions = (
        browserSession.fdictCreateBrowserSessionStore()
    )
    clientAgent = TestClient(app, headers={
        "X-Vaibify-Session": "any-agent-token",
    })
    responseAgent = clientAgent.get("/api/session/lifetime")
    assert responseAgent.status_code == 403
    assert "no browser session" in responseAgent.json()["detail"]


def testHubApplicationRegistersTheEvaluatorOnItsLifespan():
    """A real hub carries the evaluator among its background tasks."""
    from unittest.mock import patch
    from tests.testAgentLaneEnforcement import MockDockerConnection
    from vaibify.gui import pipelineServer
    with patch.object(
        pipelineServer, "_fconnectionCreateDocker", MockDockerConnection,
    ):
        app = pipelineServer.fappCreateApplication(
            sWorkspaceRoot="/workspace", sTerminalUserArg="testuser",
        )
    listNames = [
        getattr(fnStartup, "__name__", "") for fnStartup
        in app.state.listLifespanStartup
    ]
    assert "fnStartEvaluator" in listNames, (
        "the lifecycle evaluator must be scheduled by the real "
        "application, not only by a hand-built test app"
    )
