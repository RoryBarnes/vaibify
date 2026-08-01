"""The safe reaper's ORPHANED→RELEASED conditions (design §7, slice 5).

An ORPHANED_SESSION record may be released only when no live task, no
live socket, no in-flight agent request, no fresh agent-activity
stamp, and no unsettled journal record stands, and only once the reap
grace has elapsed FROM THE ORPHAN STAMP — never from the last socket.
ACTIVE records keep the pre-existing idle-grace reap unchanged. The
orphaning trigger itself is slice 6; these tests set ``sState``
directly, which the design's slice map sanctions.

The agent-liveness inputs are stamped by the REAL middleware over a
real application and a real HTTP client, with the container name kept
distinct from the Docker id (repo epistemics rule).
"""

import time
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from vaibify.config import containerLock, operationJournal
from vaibify.gui import (
    containerOwnership,
    pipelineServer,
    serverLifespan,
    serverMiddleware,
)
from vaibify.gui.containerOwnership import (
    OwnerRecord,
    S_OWNER_STATE_ORPHANED_SESSION,
)
from tests.testAgentLaneEnforcement import MockDockerConnection

S_PROJECT_NAME = "SampleProject"
S_CONTAINER_ID = "cid-abc123def456"
S_AGENT_TOKEN = "agent-token-for-reaper-tests"
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


def _recordBuildOrphanedRecord(
    fOrphanedAgoSeconds=F_GRACE_SECONDS + 1.0,
    fLastSeenAgoSeconds=99999.0,
):
    """Return an ORPHANED record whose clocks are set relative to now."""
    fNowMonotonic = time.monotonic()
    recordOwner = OwnerRecord(
        sLeaseId="lease-orphaned",
        fileHandleLock=None,
        sAgentToken=S_AGENT_TOKEN,
        sContainerId=S_CONTAINER_ID,
        sBrowserSessionId="session-orphaned",
    )
    recordOwner.sState = S_OWNER_STATE_ORPHANED_SESSION
    recordOwner.fOrphanedSinceMonotonic = (
        fNowMonotonic - fOrphanedAgoSeconds
    )
    recordOwner.fLastSeenMonotonic = fNowMonotonic - fLastSeenAgoSeconds
    return recordOwner


# -- the record-local §7 conditions ----------------------------------------


@pytest.mark.falsification
def testOrphanedReapGraceRunsFromTheOrphanStampNotTheLastSocket():
    """The grace window measures from ``fOrphanedSinceMonotonic``.

    Case 7, orphan-grace half (design §7): a record whose last socket
    died long ago but which entered ORPHANED_SESSION only moments ago
    is NOT reapable — the reconnect-and-transfer window must start at
    the orphan transition — while the same record past the grace from
    its ORPHAN stamp is.

    Kills: measuring the orphan branch's elapsed time from
    ``fLastSeenMonotonic`` instead of ``fOrphanedSinceMonotonic``.
    """
    recordFreshOrphan = _recordBuildOrphanedRecord(
        fOrphanedAgoSeconds=1.0, fLastSeenAgoSeconds=99999.0,
    )
    assert containerOwnership.fbOwnerIsReapable(
        recordFreshOrphan, F_GRACE_SECONDS,
    ) is False
    recordStaleOrphan = _recordBuildOrphanedRecord(
        fOrphanedAgoSeconds=F_GRACE_SECONDS + 1.0,
    )
    assert containerOwnership.fbOwnerIsReapable(
        recordStaleOrphan, F_GRACE_SECONDS,
    ) is True


@pytest.mark.falsification
def testOrphanedReapRequiresAStaleAgentActivityStamp():
    """A fresh agent-activity stamp pins an otherwise reapable orphan.

    Case 7, agent-stamp half (design §7): the reap succeeds only past
    the grace AND with a stale agent stamp; an in-container agent that
    acted moments ago retains its record however long the orphan has
    stood, and a never-stamped record (0.0) is not pinned.

    Kills: dropping the agent-activity-stamp condition from the
    orphan-record reapability predicate.
    """
    recordPinned = _recordBuildOrphanedRecord()
    recordPinned.fLastAgentActivityMonotonic = time.monotonic() - 1.0
    assert containerOwnership.fbOwnerIsReapable(
        recordPinned, F_GRACE_SECONDS,
    ) is False
    recordStaleAgent = _recordBuildOrphanedRecord()
    recordStaleAgent.fLastAgentActivityMonotonic = (
        time.monotonic() - F_GRACE_SECONDS - 1.0
    )
    assert containerOwnership.fbOwnerIsReapable(
        recordStaleAgent, F_GRACE_SECONDS,
    ) is True
    recordNeverStamped = _recordBuildOrphanedRecord()
    assert recordNeverStamped.fLastAgentActivityMonotonic == 0.0
    assert containerOwnership.fbOwnerIsReapable(
        recordNeverStamped, F_GRACE_SECONDS,
    ) is True


def testOrphanedReapRequiresZeroInFlightAgentRequests():
    """One in-flight agent request vetoes the reap outright."""
    recordOwner = _recordBuildOrphanedRecord()
    recordOwner.iInFlightAgentRequests = 1
    assert containerOwnership.fbOwnerIsReapable(
        recordOwner, F_GRACE_SECONDS,
    ) is False
    recordOwner.iInFlightAgentRequests = 0
    assert containerOwnership.fbOwnerIsReapable(
        recordOwner, F_GRACE_SECONDS,
    ) is True


def testOrphanedReapVetoedByALiveSocket():
    """A live connection count vetoes even a long-orphaned record."""
    recordOwner = _recordBuildOrphanedRecord()
    recordOwner.iLiveConnectionCount = 1
    assert containerOwnership.fbOwnerIsReapable(
        recordOwner, F_GRACE_SECONDS,
    ) is False


def testActiveRecordsKeepTheIdleGraceReapUnchanged():
    """ACTIVE reapability still measures from the last live socket."""
    recordOwner = OwnerRecord(
        sLeaseId="lease-active", fileHandleLock=None,
        sContainerId=S_CONTAINER_ID,
    )
    recordOwner.fLastSeenMonotonic = (
        time.monotonic() - F_GRACE_SECONDS - 1.0
    )
    # A fresh agent stamp does NOT pin an ACTIVE record: the §7
    # conditions bind the ORPHANED state only (the release-time agent
    # veto is the slice-6 explicit-release rule, not this predicate).
    recordOwner.fLastAgentActivityMonotonic = time.monotonic()
    assert containerOwnership.fbOwnerIsReapable(
        recordOwner, F_GRACE_SECONDS,
    ) is True


# -- the reaper-loop vetoes that need app state -----------------------------


def _appBuildReaperApplication(recordOwner):
    """Return a minimal (app, dictCtx) pair around one owner record."""
    stateApp = SimpleNamespace(
        bReapOwnerships=True,
        dictContainerOwners={S_PROJECT_NAME: recordOwner},
        dictSessionOwner={},
        dictMutationSupervisors={},
        dictDurableTaskRecords={},
        dictTerminalExecutionRecords={},
    )
    dictCtx = {
        "docker": SimpleNamespace(flistGetRunningContainers=lambda: []),
    }
    return (SimpleNamespace(state=stateApp), dictCtx)


@pytest.mark.falsification
def testOrphanedReapVetoedWhileAJournalRecordIsUnsettled():
    """An unsettled journal record retains the orphaned ownership.

    Case 7, journal half (design §7): ORPHANED→RELEASED requires every
    journaled operation record settled — a PREPARED record left by an
    interrupted operation vetoes the reap, and the same pass releases
    the record once the journal is settled.

    Kills: dropping ``_fbOrphanedOwnerJournalUnsettled`` from the
    reaper's busy veto in ``_fnReapIdleOwnershipsForApp``.
    """
    recordOwner = _recordBuildOrphanedRecord()
    app, dictCtx = _appBuildReaperApplication(recordOwner)
    sOperationId = operationJournal.fsPrepareOperation(
        S_PROJECT_NAME, "exec", S_CONTAINER_ID,
    )
    serverLifespan._fnReapIdleOwnershipsForApp(app, dictCtx)
    assert S_PROJECT_NAME in app.state.dictContainerOwners, (
        "an orphaned owner was reaped over an unsettled journal record"
    )
    operationJournal.fnSettleOperation(S_PROJECT_NAME, sOperationId)
    serverLifespan._fnReapIdleOwnershipsForApp(app, dictCtx)
    assert S_PROJECT_NAME not in app.state.dictContainerOwners


def testOrphanedReapTreatsAnUnreadableJournalAsUnsettled():
    """A malformed journal reads as unsettled — fail closed."""
    import os
    recordOwner = _recordBuildOrphanedRecord()
    app, dictCtx = _appBuildReaperApplication(recordOwner)
    sJournalPath = operationJournal.fsJournalPathFor(S_PROJECT_NAME)
    os.makedirs(os.path.dirname(sJournalPath), exist_ok=True)
    with open(sJournalPath, "wb") as fileHandle:
        fileHandle.write(b"\x00not a journal")
    serverLifespan._fnReapIdleOwnershipsForApp(app, dictCtx)
    assert S_PROJECT_NAME in app.state.dictContainerOwners


@pytest.mark.falsification
def testOrphanedReapAndCancelCannotBothReleaseTheRecord():
    """The reaper never releases an orphan whose durable task lives.

    Case 32, orphaned half (the carrier half is slice 3): an
    ORPHANED_SESSION record past every grace, whose container still
    hosts a live — even cancel-requested — durable task, is retained;
    the task's own finalizer is the single releasing party, and the
    reaper collects the record only once the task record is gone.

    Kills: dropping ``fbContainerHasLiveMutationWork`` from the busy
    veto in ``serverLifespan._fnReapIdleOwnershipsForApp``.
    """
    recordOwner = _recordBuildOrphanedRecord()
    app, dictCtx = _appBuildReaperApplication(recordOwner)
    recordTask = SimpleNamespace(
        sTaskId="task-1", sState="cancelRequested",
        taskAsync=SimpleNamespace(done=lambda: False),
    )
    app.state.dictDurableTaskRecords[S_PROJECT_NAME] = recordTask
    serverLifespan._fnReapIdleOwnershipsForApp(app, dictCtx)
    assert S_PROJECT_NAME in app.state.dictContainerOwners, (
        "the reaper released an orphan while its durable task lived"
    )
    app.state.dictDurableTaskRecords.clear()
    serverLifespan._fnReapIdleOwnershipsForApp(app, dictCtx)
    assert S_PROJECT_NAME not in app.state.dictContainerOwners


# -- the agent-liveness stamps, through the real middleware ------------------


@pytest.fixture
def appWithAgentOwner():
    """A real application whose one container is agent-owned."""
    with patch.object(
        pipelineServer, "_fconnectionCreateDocker", MockDockerConnection,
    ):
        app = pipelineServer.fappCreateApplication(
            sWorkspaceRoot="/workspace", sTerminalUserArg="testuser",
        )
    app.state.dictContainerOwners[S_PROJECT_NAME] = OwnerRecord(
        sLeaseId="lease-agent", fileHandleLock=None,
        sAgentToken=S_AGENT_TOKEN, sContainerId=S_CONTAINER_ID,
    )
    return app


def _clientBuildAgentClient(app):
    """Return a TestClient authenticated as the in-container agent."""
    return TestClient(app, headers={
        "X-Vaibify-Session": S_AGENT_TOKEN,
        "Host": "host.docker.internal:8050",
    })


def testAdmittedAgentRequestStampsLivenessAndUnwindsInFlight(
    appWithAgentOwner,
):
    """A real admitted agent call stamps activity and closes its bracket."""
    recordOwner = appWithAgentOwner.state.dictContainerOwners[
        S_PROJECT_NAME
    ]
    assert recordOwner.fLastAgentActivityMonotonic == 0.0
    clientAgent = _clientBuildAgentClient(appWithAgentOwner)
    response = clientAgent.get(f"/api/pipeline/{S_CONTAINER_ID}/state")
    assert response.status_code == 200
    assert recordOwner.fLastAgentActivityMonotonic > 0.0
    assert recordOwner.iInFlightAgentRequests == 0


def testRefusedAndCrossContainerAgentRequestsDoNotPinTheRecord(
    appWithAgentOwner,
):
    """Only ADMITTED calls stamp: refusals and foreign tokens never pin."""
    recordOwner = appWithAgentOwner.state.dictContainerOwners[
        S_PROJECT_NAME
    ]
    clientAgent = _clientBuildAgentClient(appWithAgentOwner)
    responseUserOnly = clientAgent.post(
        f"/api/workflow/{S_CONTAINER_ID}/supervision/configure",
        json={},
    )
    assert responseUserOnly.status_code == 403
    responseForeign = clientAgent.get(
        "/api/pipeline/cid-of-some-other-container/state",
    )
    assert responseForeign.status_code in (400, 401)
    assert recordOwner.fLastAgentActivityMonotonic == 0.0
    assert recordOwner.iInFlightAgentRequests == 0


@pytest.mark.falsification
@pytest.mark.asyncio
async def testLongRunningAgentRequestHoldsTheRecordLiveFullDuration():
    """The in-flight bracket pins the record for the call's whole life.

    Case 20, slice-5 half (design §7): while an admitted agent request
    is still being served, ``iInFlightAgentRequests`` is above zero
    and the record is un-reapable no matter how stale every clock is;
    the bracket closes in ``finally`` and refreshes the activity stamp
    even on that path.

    Kills: neutralizing the in-flight increment in
    ``serverMiddleware._fresponseServeAdmittedAgentRequest``.
    """
    recordOwner = _recordBuildOrphanedRecord()
    dictContainerOwners = {S_PROJECT_NAME: recordOwner}
    dictObserved = {}

    class FakeUrl:
        path = f"/api/pipeline/{S_CONTAINER_ID}/state"

    requestFake = SimpleNamespace(
        headers={"x-vaibify-session": S_AGENT_TOKEN},
        url=FakeUrl(),
        query_params={},
    )

    async def fnProbePinnedDuringDispatch(request):
        dictObserved["iInFlightDuring"] = (
            recordOwner.iInFlightAgentRequests
        )
        dictObserved["bReapableDuring"] = (
            containerOwnership.fbOwnerIsReapable(
                recordOwner, F_GRACE_SECONDS,
            )
        )
        return "served"

    sResult = await serverMiddleware._fresponseServeAdmittedAgentRequest(
        requestFake, dictContainerOwners, fnProbePinnedDuringDispatch,
    )
    assert sResult == "served"
    assert dictObserved["iInFlightDuring"] == 1
    assert dictObserved["bReapableDuring"] is False, (
        "an orphaned record was reapable while an admitted agent "
        "request was mid-flight"
    )
    assert recordOwner.iInFlightAgentRequests == 0
    assert recordOwner.fLastAgentActivityMonotonic > 0.0
