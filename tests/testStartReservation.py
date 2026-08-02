"""The server-owned start reservation, driven through the real hub.

Design §10b. A start is arbitrated under the host flock and the
cardinality lock, answered 202 with a status-poll location and NEVER a
lease, executed as a commit-guard mode-(c) durable task, and delivered
only by the canonical poll. These tests drive the REAL hub application
over HTTP with genuine per-session credentials; only the Docker
create-then-start pair is substituted, by a fake that is held open on
purpose so the "while starting" states are real states and not
simulations of them.

The client is context-managed everywhere, because ``TestClient`` outside
a ``with`` block spins a fresh event loop per request and would drop the
durable task a real uvicorn hub keeps running.
"""

import os
import threading
import time
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from vaibify.config import containerLock, registryManager
from vaibify.gui import (
    browserSession, containerOwnership, pipelineServer, startReservation,
    startResultStore,
)
from tests.sessionTokenTestHelper import fsBootstrapCredential
from tests.testAgentLaneEnforcement import MockDockerConnection
from tests.testSessionCardinality import (
    MockDockerConnectionTwoContainers,
    S_SECOND_CONTAINER_NAME,
)

S_PROJECT_NAME = "test-container"
S_STARTED_CONTAINER_ID = "startedContainerId9876"


@pytest.fixture(autouse=True)
def fixtureIsolateHostState(tmp_path, monkeypatch):
    """Keep the flock and the project registry inside tmp_path."""
    monkeypatch.setattr(
        containerLock, "_S_LOCK_DIRECTORY", str(tmp_path / "locks"),
    )
    sRegistryDirectory = str(tmp_path / ".vaibify")
    monkeypatch.setattr(
        registryManager, "_S_REGISTRY_DIRECTORY", sRegistryDirectory,
    )
    monkeypatch.setattr(
        registryManager, "_S_REGISTRY_PATH",
        os.path.join(sRegistryDirectory, "registry.json"),
    )


@pytest.fixture
def appHub():
    """Build the real hub application over a two-container mocked Docker."""
    with patch.object(
        pipelineServer, "_fconnectionCreateDocker",
        MockDockerConnectionTwoContainers,
    ):
        return pipelineServer.fappCreateHubApplication(iExpectedPort=0)


@pytest.fixture
def appViewer():
    """Build the real viewer application (for the connect gate)."""
    with patch.object(
        pipelineServer, "_fconnectionCreateDocker", MockDockerConnection,
    ):
        return pipelineServer.fappCreateApplication(
            sWorkspaceRoot="/workspace", sTerminalUserArg="testuser",
        )


class HeldStartExecutor:
    """A create-then-start substitute the test can hold open or fail.

    Holding a real start open is what makes the "while starting" states
    genuine: the reservation is live, the durable task is running, and
    the routes are answering against the same in-flight record a slow
    ``docker create`` would produce.
    """

    def __init__(self, errorToRaise=None):
        self.eventRelease = threading.Event()
        self.eventEntered = threading.Event()
        self.errorToRaise = errorToRaise
        self.iCallCount = 0
        self.listThreadIdentities = []

    def fsExecute(self, sName, reservation, configProject):
        self.iCallCount += 1
        self.listThreadIdentities.append(threading.get_ident())
        self.eventEntered.set()
        self.eventRelease.wait(timeout=10.0)
        if self.errorToRaise is not None:
            raise self.errorToRaise
        return S_STARTED_CONTAINER_ID


def fnInstallExecutor(monkeypatch, executor):
    """Substitute the Docker half of the start with a controllable fake."""
    monkeypatch.setattr(
        startReservation, "_fsExecuteReservedStart", executor.fsExecute,
    )
    monkeypatch.setattr(
        "vaibify.docker.containerManager.fdictSettleReservationContainers",
        lambda sReservationId, bLaunchWasKilled: {
            "bConclusive": True, "listRemovedContainerIds": [],
            "sDetail": "no container was created",
        },
    )


def fnRegisterProject(client, tmp_path, sProjectName=S_PROJECT_NAME):
    """Register a minimal project directory with the hub."""
    sProjectDirectory = str(tmp_path / sProjectName)
    os.makedirs(sProjectDirectory, exist_ok=True)
    with open(
        os.path.join(sProjectDirectory, "vaibify.yml"), "w",
    ) as fileHandle:
        fileHandle.write(f"projectName: {sProjectName}\n")
    responseAdd = client.post(
        "/api/registry", json={"sDirectory": sProjectDirectory},
    )
    assert responseAdd.status_code == 200, responseAdd.text


def fclientLive(app, sCredential):
    """Return a context-managed client whose loop outlives its requests."""
    return TestClient(app, headers={"X-Session-Token": sCredential})


def fsSessionIdOnApp(app, sCredential):
    """Resolve a credential to its browser session id."""
    return browserSession.fsSessionIdForCredential(
        app.state.dictBrowserSessions, sCredential,
    )


def fnWaitForSettledResult(client, app, sReservationId, iAttempts=300):
    """Drive the loop until the durable start publishes its outcome."""
    for _ in range(iAttempts):
        recordResult = app.state.dictStartResults.get(sReservationId)
        if recordResult is not None and recordResult.sState != (
            startResultStore.S_RESULT_PENDING
        ):
            return recordResult
        client.get("/api/registry")
    raise AssertionError("the start never settled its result record")


# ------------------------------------------------------------------
# The 202 contract and the reservation itself.
# ------------------------------------------------------------------

def test_start_answers_202_with_no_lease_and_holds_a_reservation(
    appHub, tmp_path, monkeypatch,
):
    """A start reserves the container and hands back no authority.

    Nothing is running yet, so there is nothing a lease could authorize;
    the reservation lives on the owner record as the second axis while
    the container's authority state stays ACTIVE.
    """
    executor = HeldStartExecutor()
    fnInstallExecutor(monkeypatch, executor)
    sCredential = fsBootstrapCredential(appHub)
    with fclientLive(appHub, sCredential) as client:
        fnRegisterProject(client, tmp_path)
        responseStart = client.post(
            f"/api/containers/{S_PROJECT_NAME}/start",
        )
        assert responseStart.status_code == 202, responseStart.text
        dictBody = responseStart.json()
        assert "sLeaseId" not in dictBody
        assert dictBody["sStatusPath"] == (
            f"/api/containers/{S_PROJECT_NAME}/start-status"
        )
        assert executor.eventEntered.wait(timeout=5.0)
        recordOwner = appHub.state.dictContainerOwners[S_PROJECT_NAME]
        assert recordOwner.reservation is not None
        assert recordOwner.reservation.sReservationId == (
            dictBody["sReservationId"]
        )
        assert recordOwner.sState == (
            containerOwnership.S_OWNER_STATE_ACTIVE
        ), "start-progress is an orthogonal axis, not a fourth state"
        executor.eventRelease.set()
        fnWaitForSettledResult(client, appHub, dictBody["sReservationId"])
        assert recordOwner.reservation is None


def test_a_repeated_start_returns_the_same_reservation_and_no_lease(
    appHub, tmp_path, monkeypatch,
):
    """The idempotent recovery: same 202, same reservation, one launch."""
    executor = HeldStartExecutor()
    fnInstallExecutor(monkeypatch, executor)
    sCredential = fsBootstrapCredential(appHub)
    with fclientLive(appHub, sCredential) as client:
        fnRegisterProject(client, tmp_path)
        dictFirst = client.post(
            f"/api/containers/{S_PROJECT_NAME}/start",
        ).json()
        assert executor.eventEntered.wait(timeout=5.0)
        responseSecond = client.post(
            f"/api/containers/{S_PROJECT_NAME}/start",
        )
        assert responseSecond.status_code == 202, responseSecond.text
        dictSecond = responseSecond.json()
        assert dictSecond["sReservationId"] == dictFirst["sReservationId"]
        assert dictSecond["bAlreadyStarting"] is True
        assert "sLeaseId" not in dictSecond
        executor.eventRelease.set()
        fnWaitForSettledResult(client, appHub, dictFirst["sReservationId"])
    assert executor.iCallCount == 1, (
        "a repeated start must not launch a second docker create"
    )


def test_a_second_session_cannot_start_a_reserved_container(
    appHub, tmp_path, monkeypatch,
):
    """While a start is in flight another session is refused, not queued."""
    executor = HeldStartExecutor()
    fnInstallExecutor(monkeypatch, executor)
    with fclientLive(appHub, fsBootstrapCredential(appHub)) as clientOwner:
        fnRegisterProject(clientOwner, tmp_path)
        dictStart = clientOwner.post(
            f"/api/containers/{S_PROJECT_NAME}/start",
        ).json()
        assert executor.eventEntered.wait(timeout=5.0)
        clientIntruder = TestClient(appHub, headers={
            "X-Session-Token": fsBootstrapCredential(appHub),
        })
        responseForeign = clientIntruder.post(
            f"/api/containers/{S_PROJECT_NAME}/start",
        )
        assert responseForeign.status_code == 409, responseForeign.text
        assert "another browser session" in responseForeign.text
        executor.eventRelease.set()
        fnWaitForSettledResult(
            clientOwner, appHub, dictStart["sReservationId"],
        )


def test_stop_is_refused_while_the_start_reservation_is_live(
    appHub, tmp_path, monkeypatch,
):
    """Mutating a container mid-create is refused 409, even to its owner.

    This is the partial-state hazard the reservation bounds: a stop
    landing between ``docker create`` and ``docker start`` would race the
    very identity the write-ahead record exists to protect.
    """
    executor = HeldStartExecutor()
    fnInstallExecutor(monkeypatch, executor)
    listStopped = []
    monkeypatch.setattr(
        "vaibify.gui.registryRoutes._fnExecuteStop",
        lambda sContainerName: listStopped.append(sContainerName),
    )
    with fclientLive(appHub, fsBootstrapCredential(appHub)) as client:
        fnRegisterProject(client, tmp_path)
        dictStart = client.post(
            f"/api/containers/{S_PROJECT_NAME}/start",
        ).json()
        assert executor.eventEntered.wait(timeout=5.0)
        responseStop = client.post(f"/api/containers/{S_PROJECT_NAME}/stop")
        assert responseStop.status_code == 409, responseStop.text
        assert "still starting" in responseStop.text
        assert listStopped == []
        executor.eventRelease.set()
        fnWaitForSettledResult(client, appHub, dictStart["sReservationId"])


# ------------------------------------------------------------------
# Cardinality and the reaper.
# ------------------------------------------------------------------

def test_a_session_holding_another_container_cannot_start_a_second(
    appHub, tmp_path, monkeypatch,
):
    """One container per session, enforced on the START creation path."""
    executor = HeldStartExecutor()
    fnInstallExecutor(monkeypatch, executor)
    sCredential = fsBootstrapCredential(appHub)
    with fclientLive(appHub, sCredential) as client:
        fnRegisterProject(client, tmp_path, S_SECOND_CONTAINER_NAME)
        fnRegisterProject(client, tmp_path, S_PROJECT_NAME)
        responseClaim = client.post(
            f"/api/registry/{S_SECOND_CONTAINER_NAME}/claim",
        )
        assert responseClaim.status_code == 200, responseClaim.text
        responseStart = client.post(
            f"/api/containers/{S_PROJECT_NAME}/start",
        )
        assert responseStart.status_code == 409, responseStart.text
        assert S_SECOND_CONTAINER_NAME in responseStart.text
    assert executor.iCallCount == 0
    assert S_PROJECT_NAME not in appHub.state.dictContainerOwners


def test_a_reserved_record_is_never_reapable():
    """The idle reaper may not release a container mid-start.

    The reservation is what makes a record with no sockets read as LIVE
    work — without it the reaper (and the idle watchdog reading the same
    map) would free the flock under a running ``docker create``.
    """
    recordOwner = containerOwnership.OwnerRecord(
        sLeaseId="lease", fileHandleLock=None,
    )
    recordOwner.fLastSeenMonotonic = time.monotonic() - 3600.0
    assert containerOwnership.fbOwnerIsReapable(recordOwner) is True
    recordOwner.reservation = startReservation.StartReservation(
        sReservationId="0" * 32,
        recordStartTask=startReservation.StartTaskRecord(
            sStartTaskId="task", sJournalOperationId="operation",
        ),
    )
    assert containerOwnership.fbOwnerIsReapable(recordOwner) is False


# ------------------------------------------------------------------
# The canonical status poll.
# ------------------------------------------------------------------

def test_the_poll_reports_pending_then_delivers_the_owner_derived_lease(
    appHub, tmp_path, monkeypatch,
):
    """One endpoint, three states, and the only place a lease is handed out."""
    executor = HeldStartExecutor()
    fnInstallExecutor(monkeypatch, executor)
    sCredential = fsBootstrapCredential(appHub)
    with fclientLive(appHub, sCredential) as client:
        fnRegisterProject(client, tmp_path)
        dictStart = client.post(
            f"/api/containers/{S_PROJECT_NAME}/start",
        ).json()
        assert executor.eventEntered.wait(timeout=5.0)
        responsePending = client.get(
            f"/api/containers/{S_PROJECT_NAME}/start-status",
        )
        assert responsePending.status_code == 200, responsePending.text
        assert responsePending.json()["sState"] == "PENDING"
        assert "sLeaseId" not in responsePending.json()
        executor.eventRelease.set()
        fnWaitForSettledResult(client, appHub, dictStart["sReservationId"])
        responseDone = client.get(
            f"/api/containers/{S_PROJECT_NAME}/start-status",
        )
        dictDone = responseDone.json()
        assert dictDone["sState"] == "SUCCEEDED"
        assert dictDone["sContainerId"] == S_STARTED_CONTAINER_ID
        recordOwner = appHub.state.dictContainerOwners[S_PROJECT_NAME]
        assert dictDone["sLeaseId"] == recordOwner.sLeaseId, (
            "the lease must be derived from the live owner record"
        )


def test_a_foreign_session_cannot_read_another_sessions_start_result(
    appHub, tmp_path, monkeypatch,
):
    """The poll is bound to a principal, not to whoever asks first."""
    executor = HeldStartExecutor()
    fnInstallExecutor(monkeypatch, executor)
    with fclientLive(appHub, fsBootstrapCredential(appHub)) as clientOwner:
        fnRegisterProject(clientOwner, tmp_path)
        dictStart = clientOwner.post(
            f"/api/containers/{S_PROJECT_NAME}/start",
        ).json()
        executor.eventRelease.set()
        fnWaitForSettledResult(
            clientOwner, appHub, dictStart["sReservationId"],
        )
        clientIntruder = TestClient(appHub, headers={
            "X-Session-Token": fsBootstrapCredential(appHub),
        })
        responseForeign = clientIntruder.get(
            f"/api/containers/{S_PROJECT_NAME}/start-status",
        )
    assert responseForeign.status_code == 403, responseForeign.text
    assert "sLeaseId" not in responseForeign.json()


def test_a_failed_start_frees_the_container_and_blocks_a_silent_retry(
    appHub, tmp_path, monkeypatch,
):
    """A failure is delivered, and the next start must be deliberate.

    The unacknowledged failure refuses a new start so a stale failure can
    never silently re-launch; naming the reservation id — which only a
    client that actually read the outcome can do — is the acknowledgement.
    """
    executor = HeldStartExecutor(errorToRaise=RuntimeError("image missing"))
    fnInstallExecutor(monkeypatch, executor)
    sCredential = fsBootstrapCredential(appHub)
    with fclientLive(appHub, sCredential) as client:
        fnRegisterProject(client, tmp_path)
        executor.eventRelease.set()
        dictStart = client.post(
            f"/api/containers/{S_PROJECT_NAME}/start",
        ).json()
        recordResult = fnWaitForSettledResult(
            client, appHub, dictStart["sReservationId"],
        )
        assert recordResult.sState == "FAILED"
        assert S_PROJECT_NAME not in appHub.state.dictContainerOwners, (
            "a conclusively-clean failure must free the container"
        )
        responseFailed = client.get(
            f"/api/containers/{S_PROJECT_NAME}/start-status",
        )
        assert responseFailed.status_code == 200, responseFailed.text
        dictFailed = responseFailed.json()
        assert dictFailed["sState"] == "FAILED"
        assert "image missing" in dictFailed["sError"]
        assert "sLeaseId" not in dictFailed
        responseBlocked = client.post(
            f"/api/containers/{S_PROJECT_NAME}/start",
        )
        assert responseBlocked.status_code == 409, responseBlocked.text
        assert "acknowledged" in responseBlocked.text
        responseRetry = client.post(
            f"/api/containers/{S_PROJECT_NAME}/start",
            json={
                "sAcknowledgeReservationId": dictStart["sReservationId"],
            },
        )
        assert responseRetry.status_code == 202, responseRetry.text
        fnWaitForSettledResult(client, appHub, responseRetry.json()[
            "sReservationId"
        ])


# ------------------------------------------------------------------
# The connect gate while starting.
# ------------------------------------------------------------------

def test_connect_by_the_initiator_while_starting_is_a_truthful_refusal(
    appViewer,
):
    """A not-yet-running container yields a pending refusal, not a lease."""
    sCredential = fsBootstrapCredential(appViewer)
    sSessionId = fsSessionIdOnApp(appViewer, sCredential)
    from tests.testAgentLaneEnforcement import S_CONTAINER_ID
    appViewer.state.dictContainerOwners[S_PROJECT_NAME] = (
        containerOwnership.OwnerRecord(
            sLeaseId="ownerLease", fileHandleLock=None,
            sContainerId=S_CONTAINER_ID, sBrowserSessionId=sSessionId,
            reservation=startReservation.StartReservation(
                sReservationId="1" * 32,
                recordStartTask=startReservation.StartTaskRecord(
                    sStartTaskId="task", sJournalOperationId="operation",
                ),
            ),
        )
    )
    clientBrowser = TestClient(
        appViewer, headers={"X-Session-Token": sCredential},
    )
    responseConnect = clientBrowser.post(
        f"/api/connect/{S_CONTAINER_ID}",
        headers={"X-Vaibify-Lease": "ownerLease"},
    )
    assert responseConnect.status_code == 409, responseConnect.text
    assert "still starting" in responseConnect.json()["detail"]


def test_an_expiring_session_orphans_a_mid_start_record_never_releases_it(
    appHub, tmp_path, monkeypatch,
):
    """A session expiring mid-start must ORPHAN, never release (§10b).

    Releasing here would free the flock while a ``docker create`` is
    still running, and a second tab could claim the container out from
    under it. Orphaning ends the browser session's authority and leaves
    the start — and the exclusivity protecting it — untouched, which is
    what a later host transfer reclaims.
    """
    import asyncio

    from vaibify.gui import browserSession, sessionLifecycle

    executor = HeldStartExecutor()
    fnInstallExecutor(monkeypatch, executor)
    sCredential = fsBootstrapCredential(appHub)
    with fclientLive(appHub, sCredential) as client:
        fnRegisterProject(client, tmp_path)
        dictStart = client.post(
            f"/api/containers/{S_PROJECT_NAME}/start",
        ).json()
        assert executor.eventEntered.wait(timeout=5.0)
        sSessionId = fsSessionIdOnApp(appHub, sCredential)
        recordSession = appHub.state.dictBrowserSessions[
            "dictSessionsByCredential"
        ][sCredential]
        recordSession.fCreatedMonotonic -= (
            sessionLifecycle.F_ABSOLUTE_SESSION_CAP_SECONDS + 1.0
        )
        asyncio.run(
            sessionLifecycle.fnExpireIdleBrowserSessions(appHub.state),
        )
        recordOwner = appHub.state.dictContainerOwners[S_PROJECT_NAME]
        assert recordOwner.sState == (
            containerOwnership.S_OWNER_STATE_ORPHANED_SESSION
        ), "an expired session mid-start must orphan its record"
        assert recordOwner.reservation is not None, (
            "orphaning must leave the running start untouched"
        )
        assert browserSession.fbValidateCredential(
            appHub.state.dictBrowserSessions, sCredential,
        ) is False, "the expired session's credential must be revoked"
        assert recordOwner.sBrowserSessionId == sSessionId, (
            "orphaning ends the session's authority, not the binding "
            "a transfer will rebind"
        )
        executor.eventRelease.set()
        fnWaitForSettledResult(client, appHub, dictStart["sReservationId"])
        assert recordOwner.sState == (
            containerOwnership.S_OWNER_STATE_ORPHANED_SESSION
        ), "a start completing under an orphan leaves the authority alone"
        assert recordOwner.reservation is None
