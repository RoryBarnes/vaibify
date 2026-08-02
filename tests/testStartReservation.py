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


class MockDockerProjectNotRunning(MockDockerConnectionTwoContainers):
    """The two-container mock with the project container NOT running.

    This is the state a start actually begins from, and saying so is
    load-bearing now: a start is refused outright when the daemon
    reports the container already running, so a fixture that lists it as
    running is asking the hub to start something that is up. Every test
    below inherited that contradiction from the agent-lane mock and only
    passed because nothing checked.
    """

    def flistGetRunningContainers(self):
        return [
            dictContainer
            for dictContainer in super().flistGetRunningContainers()
            if dictContainer["sName"] != S_PROJECT_NAME
        ]


@pytest.fixture
def appHub():
    """Build the real hub application over a two-container mocked Docker."""
    with patch.object(
        pipelineServer, "_fconnectionCreateDocker",
        MockDockerProjectNotRunning,
    ):
        return pipelineServer.fappCreateHubApplication(iExpectedPort=0)


@pytest.fixture
def appHubProjectAlreadyRunning():
    """Build the hub over a Docker that reports the project RUNNING."""
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


# ------------------------------------------------------------------
# Wave 1: the start may not cost the researcher their container.
# ------------------------------------------------------------------

def test_starting_a_running_container_you_own_keeps_your_ownership(
    appHubProjectAlreadyRunning, tmp_path, monkeypatch,
):
    """Pressing Start on a container you own that is up costs nothing.

    The whole defect in one test. The container is RUNNING and the
    session OWNS it; Start is a mistake, not an attack, and its only
    correct outcome is a refusal that leaves the lease, the flock, the
    cardinality entry, and the exclusivity against a second session
    exactly as they were. Before the fix the refusal ran the failure
    settlement, which released ownership the start had not created, and
    the researcher's container silently became somebody else's to claim.

    Driven adversarially: the refusal is asserted, then the ownership,
    then a SECOND browser session is made to try the same container and
    must still be refused. A test that stopped at the 409 would pass
    even if the record had been emptied.
    """
    appHub = appHubProjectAlreadyRunning
    executor = HeldStartExecutor()
    fnInstallExecutor(monkeypatch, executor)
    sCredential = fsBootstrapCredential(appHub)
    with fclientLive(appHub, sCredential) as client:
        fnRegisterProject(client, tmp_path)
        responseClaim = client.post(
            f"/api/registry/{S_PROJECT_NAME}/claim", json={},
        )
        assert responseClaim.status_code == 200, responseClaim.text
        sLeaseId = responseClaim.json()["sLeaseId"]
        recordOwner = appHub.state.dictContainerOwners[S_PROJECT_NAME]
        iGenerationBefore = recordOwner.iOwnerGeneration

        responseStart = client.post(
            f"/api/containers/{S_PROJECT_NAME}/start",
        )
        assert responseStart.status_code == 409, responseStart.text
        assert "already running" in responseStart.json()["sMessage"]

        assert executor.iCallCount == 0, (
            "a refused start must not launch anything"
        )
        recordAfter = appHub.state.dictContainerOwners.get(S_PROJECT_NAME)
        assert recordAfter is not None, (
            "the refusal released the owner record: the researcher lost "
            "a container they own and that is still running"
        )
        assert recordAfter.sLeaseId == sLeaseId
        assert recordAfter.iOwnerGeneration == iGenerationBefore
        assert recordAfter.fileHandleLock is not None, (
            "the host flock was freed by a start that never took it"
        )

        sCredentialSecond = fsBootstrapCredential(appHub)
        assert sCredentialSecond != sCredential
        responseForeign = client.post(
            f"/api/registry/{S_PROJECT_NAME}/claim", json={},
            headers={"X-Session-Token": sCredentialSecond},
        )
        assert responseForeign.status_code == 409, (
            "a second session was able to claim the container, so the "
            f"first session's ownership did not survive: "
            f"{responseForeign.text}"
        )


def test_a_start_refused_as_running_never_reserves_or_journals(
    appHubProjectAlreadyRunning, tmp_path, monkeypatch,
):
    """The refusal precedes the reservation, not the other way round.

    Ordering, asserted directly: an already-running container must be
    refused BEFORE ownership is arbitrated, so there is no reservation
    to unwind and no write-ahead journal record to settle. Refusing
    after reserving is what made the failure settlement reachable at
    all.
    """
    appHub = appHubProjectAlreadyRunning
    executor = HeldStartExecutor()
    fnInstallExecutor(monkeypatch, executor)
    sCredential = fsBootstrapCredential(appHub)
    with fclientLive(appHub, sCredential) as client:
        fnRegisterProject(client, tmp_path)
        responseStart = client.post(
            f"/api/containers/{S_PROJECT_NAME}/start",
        )
        assert responseStart.status_code == 409
    assert S_PROJECT_NAME not in appHub.state.dictContainerOwners, (
        "an unowned container must stay unowned when its start is "
        "refused outright"
    )
    assert appHub.state.dictStartResults == {}, (
        "a refused start opened an outcome record, which would then "
        "block the next start until somebody acknowledged it"
    )


def test_a_failed_start_does_not_release_a_successors_ownership(
    appHub, tmp_path, monkeypatch,
):
    """A transfer during a start rotates the ownership the start created.

    The residual the Boolean could not see. The start DOES create the
    ownership, so "did I create it?" stays true for the whole run -- but
    a host transfer rotates the lease, the generation, and the browser
    session on that record while the start is in flight, and the
    ownership that exists at settlement is the successor's, not the one
    the start established. Releasing it would hand a live successor's
    container away.

    Keys are kept DISTINCT on purpose: the successor's lease, generation
    and session all differ from the originals, so a comparison that
    checked only one of them would still pass.
    """
    executor = HeldStartExecutor(errorToRaise=RuntimeError("boom"))
    fnInstallExecutor(monkeypatch, executor)
    sCredential = fsBootstrapCredential(appHub)
    with fclientLive(appHub, sCredential) as client:
        fnRegisterProject(client, tmp_path)
        dictStart = client.post(
            f"/api/containers/{S_PROJECT_NAME}/start",
        ).json()
        assert executor.eventEntered.wait(timeout=5.0)

        recordOwner = appHub.state.dictContainerOwners[S_PROJECT_NAME]
        recordOwner.sLeaseId = "successor-lease-value"
        recordOwner.iOwnerGeneration += 1
        recordOwner.sBrowserSessionId = "successor-browser-session"

        executor.eventRelease.set()
        fnWaitForSettledResult(client, appHub, dictStart["sReservationId"])

        recordAfter = appHub.state.dictContainerOwners.get(S_PROJECT_NAME)
        assert recordAfter is not None, (
            "the failed start released the SUCCESSOR's ownership -- the "
            "record it created had already been rotated away from it"
        )
        assert recordAfter.sLeaseId == "successor-lease-value"
        assert recordAfter.reservation is None


def test_a_failed_start_still_releases_the_ownership_it_created(
    appHub, tmp_path, monkeypatch,
):
    """The negative control for the test above.

    With no transfer, the identity still holds at settlement and the
    start DID create the ownership, so the release must happen -- a
    guard that simply never released would pass the previous test and
    leave every failed start holding a container forever.
    """
    executor = HeldStartExecutor(errorToRaise=RuntimeError("boom"))
    fnInstallExecutor(monkeypatch, executor)
    sCredential = fsBootstrapCredential(appHub)
    with fclientLive(appHub, sCredential) as client:
        fnRegisterProject(client, tmp_path)
        dictStart = client.post(
            f"/api/containers/{S_PROJECT_NAME}/start",
        ).json()
        assert executor.eventEntered.wait(timeout=5.0)
        executor.eventRelease.set()
        fnWaitForSettledResult(client, appHub, dictStart["sReservationId"])
        assert S_PROJECT_NAME not in appHub.state.dictContainerOwners, (
            "a failed start that DID create the ownership must free it, "
            "or the container is unclaimable until the hub restarts"
        )


# ------------------------------------------------------------------
# Wave 1: cancelling a start, and recovering a lease.
# ------------------------------------------------------------------

def test_cancel_start_reaches_its_handler_while_a_start_is_live(
    appHub, tmp_path, monkeypatch,
):
    """The cancel route must not be refused by the starting-409 gate.

    The lifecycle authority refuses every name-keyed lifecycle mutation
    with 409 while a start reservation is live, and its own message told
    the researcher to "wait for the start to finish or cancel it" -- and
    then refused the cancel by the same rule. A wedged start could only
    be escaped by killing the hub. Nothing exercised the route over
    HTTP, so nothing noticed.

    Asserted on the CODE, not the wording: 409 with the gate's message
    means the request never reached ``ftCancelStart`` at all.
    """
    executor = HeldStartExecutor()
    fnInstallExecutor(monkeypatch, executor)
    sCredential = fsBootstrapCredential(appHub)
    with fclientLive(appHub, sCredential) as client:
        fnRegisterProject(client, tmp_path)
        dictStart = client.post(
            f"/api/containers/{S_PROJECT_NAME}/start",
        ).json()
        assert executor.eventEntered.wait(timeout=5.0)

        responseCancel = client.post(
            f"/api/containers/{S_PROJECT_NAME}/start/cancel",
        )
        executor.eventRelease.set()

        assert responseCancel.status_code != 409, (
            "the cancel was refused by the starting gate before it "
            f"reached its handler: {responseCancel.text}"
        )
        assert responseCancel.status_code == 200, responseCancel.text
        dictCancel = responseCancel.json()
        assert dictCancel["sReservationId"] == dictStart["sReservationId"]
        fnWaitForSettledResult(client, appHub, dictStart["sReservationId"])


def test_a_foreign_session_cannot_cancel_another_sessions_start(
    appHub, tmp_path, monkeypatch,
):
    """Permitting the route while starting must not permit everybody.

    The gate now authorizes the cancel path while a reservation is live,
    which is only safe because ``ftCancelStart`` re-arbitrates on the
    session itself. A second browser session must still be refused, or
    the exemption would be a hole rather than a fix.
    """
    executor = HeldStartExecutor()
    fnInstallExecutor(monkeypatch, executor)
    sCredential = fsBootstrapCredential(appHub)
    with fclientLive(appHub, sCredential) as client:
        fnRegisterProject(client, tmp_path)
        dictStart = client.post(
            f"/api/containers/{S_PROJECT_NAME}/start",
        ).json()
        assert executor.eventEntered.wait(timeout=5.0)

        sCredentialSecond = fsBootstrapCredential(appHub)
        responseForeign = client.post(
            f"/api/containers/{S_PROJECT_NAME}/start/cancel",
            headers={"X-Session-Token": sCredentialSecond},
        )
        executor.eventRelease.set()

        assert responseForeign.status_code in (403, 409), (
            "a foreign session cancelled another session's start: "
            f"{responseForeign.text}"
        )
        recordOwner = appHub.state.dictContainerOwners[S_PROJECT_NAME]
        assert not recordOwner.reservation.recordStartTask.bCancelRequested, (
            "a refused cancel still flagged the start task"
        )
        fnWaitForSettledResult(client, appHub, dictStart["sReservationId"])


def test_the_owner_recovers_its_lease_after_the_result_record_expires(
    appHub, tmp_path, monkeypatch,
):
    """Ownership outlives the transient outcome ledger.

    The result ledger is bounded by design, so the record carrying a
    successful start's lease eventually goes away. Ownership does not,
    and a session that still holds it is entitled to its CURRENT lease
    -- otherwise a researcher who reloaded after a long start reached a
    404 and a dashboard that could not act on a container it owned.

    The record is dropped outright here rather than waited out, which is
    the same state expiry produces and does not make the suite sleep.
    """
    executor = HeldStartExecutor()
    fnInstallExecutor(monkeypatch, executor)
    sCredential = fsBootstrapCredential(appHub)
    with fclientLive(appHub, sCredential) as client:
        fnRegisterProject(client, tmp_path)
        dictStart = client.post(
            f"/api/containers/{S_PROJECT_NAME}/start",
        ).json()
        assert executor.eventEntered.wait(timeout=5.0)
        executor.eventRelease.set()
        fnWaitForSettledResult(client, appHub, dictStart["sReservationId"])

        appHub.state.dictStartResults.clear()
        recordOwner = appHub.state.dictContainerOwners[S_PROJECT_NAME]

        dictRecovered = client.get(
            f"/api/containers/{S_PROJECT_NAME}/start-status",
        ).json()
        assert dictRecovered["sState"] == startResultStore.S_RESULT_OWNED
        assert dictRecovered["sLeaseId"] == recordOwner.sLeaseId, (
            "the recovered lease must be the LIVE one, so a transfer "
            "that rotated it hands out the successor's"
        )


def test_neither_the_agent_token_nor_a_foreign_session_recovers_a_lease(
    appHub, tmp_path, monkeypatch,
):
    """Lease recovery is browser-credential-only.

    The recovery answer hands out a container's live lease, so the two
    lanes that must never reach it are driven directly: the
    per-container agent token (a machine credential that holds no
    browser session) and a second, genuine browser session that does not
    own the container. Both must come away with nothing -- not a lease,
    not a container id.
    """
    executor = HeldStartExecutor()
    fnInstallExecutor(monkeypatch, executor)
    sCredential = fsBootstrapCredential(appHub)
    with fclientLive(appHub, sCredential) as client:
        fnRegisterProject(client, tmp_path)
        dictStart = client.post(
            f"/api/containers/{S_PROJECT_NAME}/start",
        ).json()
        assert executor.eventEntered.wait(timeout=5.0)
        executor.eventRelease.set()
        fnWaitForSettledResult(client, appHub, dictStart["sReservationId"])
        appHub.state.dictStartResults.clear()

        recordOwner = appHub.state.dictContainerOwners[S_PROJECT_NAME]
        assert recordOwner.sAgentToken, "the fixture must mint an agent token"

        responseAgent = client.get(
            f"/api/containers/{S_PROJECT_NAME}/start-status",
            headers={"X-Session-Token": recordOwner.sAgentToken},
        )
        assert responseAgent.status_code != 200 or not (
            responseAgent.json().get("sLeaseId")
        ), (
            "the in-container agent token recovered a browser lease: "
            f"{responseAgent.text}"
        )

        sCredentialForeign = fsBootstrapCredential(appHub)
        responseForeign = client.get(
            f"/api/containers/{S_PROJECT_NAME}/start-status",
            headers={"X-Session-Token": sCredentialForeign},
        )
        assert responseForeign.status_code != 200 or not (
            responseForeign.json().get("sLeaseId")
        ), (
            "a foreign browser session recovered another session's "
            f"lease: {responseForeign.text}"
        )


def test_a_failed_result_clears_at_its_window_so_a_retry_can_run(
    appHub, tmp_path, monkeypatch,
):
    """An unacknowledged failure must not block a container forever.

    A FAILED record refuses the next start until the researcher names
    it, which is right while they might still be looking -- and wrong
    forever. Without its own window, a browser that failed a start and
    never came back left the container unstartable from any session.
    """
    executor = HeldStartExecutor(errorToRaise=RuntimeError("boom"))
    fnInstallExecutor(monkeypatch, executor)
    monkeypatch.setattr(
        startResultStore, "F_FAILED_RESULT_WINDOW_SECONDS", 0.0,
    )
    sCredential = fsBootstrapCredential(appHub)
    with fclientLive(appHub, sCredential) as client:
        fnRegisterProject(client, tmp_path)
        dictStart = client.post(
            f"/api/containers/{S_PROJECT_NAME}/start",
        ).json()
        assert executor.eventEntered.wait(timeout=5.0)
        executor.eventRelease.set()
        fnWaitForSettledResult(client, appHub, dictStart["sReservationId"])

        executorRetry = HeldStartExecutor()
        fnInstallExecutor(monkeypatch, executorRetry)
        responseRetry = client.post(
            f"/api/containers/{S_PROJECT_NAME}/start",
        )
        assert responseRetry.status_code == 202, (
            "an expired failure still blocked the retry: "
            f"{responseRetry.text}"
        )
        assert executorRetry.eventEntered.wait(timeout=5.0)
        executorRetry.eventRelease.set()
        fnWaitForSettledResult(
            client, appHub, responseRetry.json()["sReservationId"],
        )


def test_a_pending_result_is_never_expired_out_from_under_a_slow_start(
    appHub, tmp_path, monkeypatch,
):
    """A cold pull outlives every window in the ledger.

    The record's lifetime used to run from CREATION and apply to every
    state, so a start slower than the TTL had its record pruned while it
    was still running -- and the poll that was supposed to deliver its
    outcome answered "no start has been requested".
    """
    executor = HeldStartExecutor()
    fnInstallExecutor(monkeypatch, executor)
    monkeypatch.setattr(startResultStore, "F_RESULT_TTL_SECONDS", 0.0)
    monkeypatch.setattr(
        startResultStore, "F_FAILED_RESULT_WINDOW_SECONDS", 0.0,
    )
    sCredential = fsBootstrapCredential(appHub)
    with fclientLive(appHub, sCredential) as client:
        fnRegisterProject(client, tmp_path)
        dictStart = client.post(
            f"/api/containers/{S_PROJECT_NAME}/start",
        ).json()
        assert executor.eventEntered.wait(timeout=5.0)

        dictStatus = client.get(
            f"/api/containers/{S_PROJECT_NAME}/start-status",
        ).json()
        assert dictStatus["sState"] == startResultStore.S_RESULT_PENDING, (
            "the in-flight start's record was expired away, so its "
            f"outcome can never be delivered: {dictStatus}"
        )
        assert dictStatus["sReservationId"] == dictStart["sReservationId"]
        executor.eventRelease.set()
        fnWaitForSettledResult(client, appHub, dictStart["sReservationId"])
