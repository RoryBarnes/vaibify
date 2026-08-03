"""The commit-guard carrier (design §8) — slice 3b falsification cases.

Covers the load-bearing negative tests (a real primitive attempted
outside the carrier is refused, cases 16/16c), the shielded-supervisor
drain (16b), ordered shutdown flock retention (26), the out-of-band
cancellation plane (31), the reaper veto (32), the journal identity
gate (38, 45), and the parent-gated two-phase helper spawn driven with
REAL child processes (41). Falsification-marked tests record their
kill on a ``Kills:`` line and in ``tests/falsificationRegistry.py``.
"""

import asyncio
import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from vaibify.config import containerLock, operationJournal
from vaibify.config.mutationAdmission import (
    MutationAdmission,
    MutationNotAdmittedError,
    fnAssertOperationAdmittedByIdentity,
)
from vaibify.docker.dockerConnection import DockerConnection
from vaibify.gui import commitCarrier, pipelineServer, serverLifespan
from vaibify.gui import sessionLifecycle
from vaibify.gui.containerOwnership import OwnerRecord
from tests.sessionTokenTestHelper import fsBootstrapCredential

S_CONTAINER_NAME = "carrierproj"
S_CONTAINER_ID = "carriercid123"
REPO_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module", autouse=True)
def fnLeaveUsableEventLoopSlotBehind():
    """Restore a usable main-thread event-loop slot after this module.

    On Python 3.9 ``asyncio.run`` leaves the policy's loop slot set to
    ``None`` with ``_set_called`` latched, so a LATER test that builds
    an ``asyncio.Event()`` outside a running loop (legal on 3.9, where
    primitives bind at construction) raises "no current event loop".
    Installing a fresh loop restores the pre-module observable state.
    """
    yield
    asyncio.set_event_loop(asyncio.new_event_loop())


# ---------------------------------------------------------------------
# Stubs: a REAL DockerConnection over a recording fake client, so the
# funnel gates in dockerConnection.py are the actual code under test.
# ---------------------------------------------------------------------

class _StubContainer:
    def __init__(self, sContainerId):
        self.id = sContainerId
        self.listPutArchiveCalls = []

    def put_archive(self, sDirectory, bufferTar):
        self.listPutArchiveCalls.append(sDirectory)


class _StubApiClient:
    def __init__(self):
        self.listExecCreateCalls = []

    def exec_create(self, sContainerId, **dictKwargs):
        self.listExecCreateCalls.append(sContainerId)
        return {"Id": "stub-exec-id"}

    def exec_start(self, sExecId, stream=True, demux=True):
        return iter([])

    def exec_inspect(self, sExecId):
        return {"ExitCode": 0, "Running": False}


def _fconnectionBuildStubbedDockerConnection(stubContainer):
    """Real DockerConnection methods over an in-memory fake client."""
    connectionDocker = object.__new__(DockerConnection)
    connectionDocker._dictContainers = {stubContainer.id: stubContainer}
    connectionDocker._clientDocker = SimpleNamespace(api=_StubApiClient())
    return connectionDocker


def _ftBuildOwnedAppState(iOwnerGeneration=1):
    """Return (appState, recordOwner, dictLaneTuple) for one owner."""
    recordOwner = OwnerRecord(
        sLeaseId="lease-1", fileHandleLock=None, sAgentToken="agent-tok",
        sContainerId=S_CONTAINER_ID, sBrowserSessionId="sess-1",
        iOwnerGeneration=iOwnerGeneration,
    )
    appState = SimpleNamespace(
        dictContainerOwners={S_CONTAINER_NAME: recordOwner},
        dictBrowserSessions={}, dictSessionOwner={},
        dictMutationSupervisors={}, dictDurableTaskRecords={},
        bMutationAdmissionsClosed=False,
    )
    dictLaneTuple = {
        "sLane": "browser", "iOwnerGeneration": iOwnerGeneration,
        "sBrowserSessionId": "sess-1", "sLeaseId": "lease-1",
        "sContainerName": S_CONTAINER_NAME,
    }
    return appState, recordOwner, dictLaneTuple


@pytest.fixture
def clientWithProbeRoutes():
    """A real app whose dummy routes attempt raw primitive mutations."""
    stubContainer = _StubContainer(S_CONTAINER_ID)
    connectionDocker = _fconnectionBuildStubbedDockerConnection(
        stubContainer,
    )
    with patch.object(
        pipelineServer, "_fconnectionCreateDocker", lambda: MagicMock(),
    ):
        app = pipelineServer.fappCreateApplication(
            sWorkspaceRoot="/workspace", sTerminalUserArg="testuser",
        )

    @app.post("/api/carrier-probe/raw-write")
    async def fnProbeRawWrite():
        connectionDocker.fnWriteFile(S_CONTAINER_ID, "/tmp/probe", b"x")
        return {"bWrote": True}

    @app.post("/api/carrier-probe/threaded-write")
    async def fnProbeThreadedWrite():
        await asyncio.to_thread(
            connectionDocker.fnWriteFile, S_CONTAINER_ID, "/tmp/probe",
            b"x",
        )
        return {"bWrote": True}

    @app.post("/api/carrier-probe/durable-exec")
    async def fnProbeDurableExec():
        connectionDocker.texecRunInContainerStreamedWithChunks(
            S_CONTAINER_ID, "touch /tmp/effect", None, sUser="probe",
        )
        return {"bLaunched": True}

    @app.post("/api/carrier-probe/admitted-write")
    async def fnProbeAdmittedWrite():
        tAdmissionTokens = commitCarrier.ftupleOpenEstablishingAdmission(
            S_CONTAINER_NAME, S_CONTAINER_ID,
        )
        try:
            connectionDocker.fnWriteFile(
                S_CONTAINER_ID, "/tmp/probe", b"x",
            )
        finally:
            commitCarrier.fnCloseRequestAdmission(tAdmissionTokens)
        return {"bWrote": True}

    clientHttp = TestClient(
        app, headers={"X-Session-Token": fsBootstrapCredential(app)},
        raise_server_exceptions=False,
    )
    return clientHttp, stubContainer, connectionDocker


# ---------------------------------------------------------------------
# Case 16 — a real side effect outside the carrier is refused.
# ---------------------------------------------------------------------

@pytest.mark.falsification
def test_route_write_without_carrier_admission_is_refused_mode_a(
    clientWithProbeRoutes,
):
    """A dummy route's direct synchronous container write never lands.

    Case 16, mode (a) half: the request lane is marked by
    ``ContainerAwareRoute``, the dummy route holds no carrier-minted
    admission, so the REAL ``fnWriteFileViaTar`` refuses before any
    byte reaches ``put_archive``.

    Kills: removing the ``fnAssertContainerWriteAdmitted`` gate from
    ``dockerConnection.fnWriteFileViaTar``.
    """
    clientHttp, stubContainer, _ = clientWithProbeRoutes
    responseHttp = clientHttp.post("/api/carrier-probe/raw-write")
    assert responseHttp.status_code == 500
    assert stubContainer.listPutArchiveCalls == []


@pytest.mark.falsification
def test_route_write_without_carrier_admission_is_refused_mode_b(
    clientWithProbeRoutes,
):
    """A dummy route's thread-hopped container write never lands.

    Case 16, mode (b) half: the enforced-lane context copies into the
    ``to_thread`` worker, so crossing a thread-pool ``await`` does not
    launder an unadmitted mutation past the funnel.

    Kills: making ``fnAssertContainerWriteAdmitted`` a no-op inside an
    enforced lane (the ``fbLaneEnforced`` early-return inverted).
    """
    clientHttp, stubContainer, _ = clientWithProbeRoutes
    responseHttp = clientHttp.post("/api/carrier-probe/threaded-write")
    assert responseHttp.status_code == 500
    assert stubContainer.listPutArchiveCalls == []


def test_carrier_admitted_write_passes_the_funnel(clientWithProbeRoutes):
    """Positive control: a carrier admission lets the same write land."""
    clientHttp, stubContainer, _ = clientWithProbeRoutes
    responseHttp = clientHttp.post("/api/carrier-probe/admitted-write")
    assert responseHttp.status_code == 200
    assert stubContainer.listPutArchiveCalls == ["/tmp"]


# ---------------------------------------------------------------------
# Case 16c — a durable-task exec launch without a mode-(c) guard.
# ---------------------------------------------------------------------

@pytest.mark.falsification
def test_route_durable_exec_without_mode_c_guard_is_refused(
    clientWithProbeRoutes,
):
    """A dummy route cannot launch the streamed durable exec primitive.

    Case 16c: a future "durable task" cannot bypass the carrier while
    the structural suite stays green — the streamed create/start split
    refuses in an enforced lane without a carrier-minted mode-(c)
    durable-task guard, before ``exec_create`` is ever reached.

    Kills: removing the ``fnAssertDurableExecAdmitted`` gate from
    ``dockerConnection.texecRunInContainerStreamedWithChunks``.
    """
    clientHttp, stubContainer, connectionDocker = clientWithProbeRoutes
    responseHttp = clientHttp.post("/api/carrier-probe/durable-exec")
    assert responseHttp.status_code == 500
    listCreates = connectionDocker._clientDocker.api.listExecCreateCalls
    assert listCreates == []


# ---------------------------------------------------------------------
# Case 16b — the shielded supervisor holds the drain past cancellation.
# ---------------------------------------------------------------------

@pytest.mark.falsification
def test_cancelled_requester_leaves_drain_held_until_worker_ends():
    """Cancelling the requesting coroutine does not free the drain.

    Case 16b (lock-drain half; the transfer wording arrives with slice
    5): a competing acquisition of the per-container mutation lock
    stays blocked after the requester is cancelled, for as long as the
    worker thread lives, and acquires only after the worker terminates
    — so no successor can be admitted while an old-generation effect
    can still commit.

    Kills: dropping ``asyncio.shield`` from
    ``commitCarrier.fdictRunLockHeldMutation`` (awaiting the supervisor
    task bare), which lets the requester's cancellation release the
    lock while the worker thread keeps running.
    """
    async def _fnDrive():
        appState, _, dictLaneTuple = _ftBuildOwnedAppState()
        eventStarted = threading.Event()
        eventRelease = threading.Event()
        listEffects = []

        def fnWorker(supervisor):
            del supervisor
            eventStarted.set()
            eventRelease.wait(5)
            listEffects.append("committed")
            return "done"

        taskRequest = asyncio.get_running_loop().create_task(
            commitCarrier.fdictRunLockHeldMutation(
                appState, S_CONTAINER_NAME, S_CONTAINER_ID,
                dictLaneTuple, "helper", "slowWrite", fnWorker,
            ),
        )
        await asyncio.to_thread(eventStarted.wait, 5)
        supervisor = next(
            iter(appState.dictMutationSupervisors.values()),
        )
        taskRequest.cancel()
        with pytest.raises(asyncio.CancelledError):
            await taskRequest
        lockMutation = sessionLifecycle.flockContainerMutationForAppState(
            appState, S_CONTAINER_NAME,
        )
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(lockMutation.acquire(), 0.3)
        assert listEffects == []
        eventRelease.set()
        await asyncio.wait_for(supervisor.taskSupervisor, 5)
        await asyncio.wait_for(lockMutation.acquire(), 2)
        lockMutation.release()
        assert listEffects == ["committed"]
        assert appState.dictMutationSupervisors == {}

    asyncio.run(_fnDrive())


# ---------------------------------------------------------------------
# Case 26 — ordered shutdown retains the flock of a live worker.
# ---------------------------------------------------------------------

@pytest.mark.falsification
def test_hub_shutdown_retains_flock_while_guarded_worker_lives(
    monkeypatch, tmp_path,
):
    """The hub's shutdown hooks never free a live worker's flock.

    Case 26: the drain hook (appended first) expires its bound while
    the worker still runs; the flock-release hook then skips that
    container, so a second "hub" acquisition still refuses while the
    worker can commit, and the owner record survives shutdown.

    Kills: removing the ``setRetainedNames`` skip from
    ``appFactory._fnRegisterHubShutdownReleaseLocks``.
    """
    monkeypatch.setattr(
        containerLock, "_S_LOCK_DIRECTORY", str(tmp_path / "locks"),
    )
    monkeypatch.setattr(
        commitCarrier, "F_SHUTDOWN_DRAIN_TIMEOUT_SECONDS", 0.05,
    )

    async def _fnDrive():
        from vaibify.gui import appFactory
        with patch.object(
            pipelineServer, "_fconnectionCreateDocker",
            lambda: MagicMock(),
        ):
            app = appFactory.fappCreateHubApplication()
        fileHandleLock = containerLock.fnAcquireContainerLock(
            S_CONTAINER_NAME, 8137,
        )
        app.state.dictContainerOwners[S_CONTAINER_NAME] = OwnerRecord(
            sLeaseId="lease-1", fileHandleLock=fileHandleLock,
            sContainerId=S_CONTAINER_ID, sBrowserSessionId="sess-1",
        )
        dictLaneTuple = {
            "sLane": "browser", "iOwnerGeneration": 1,
            "sBrowserSessionId": "sess-1", "sLeaseId": "lease-1",
            "sContainerName": S_CONTAINER_NAME,
        }
        eventStarted, eventRelease = threading.Event(), threading.Event()

        def fnWorker(supervisor):
            del supervisor
            eventStarted.set()
            eventRelease.wait(5)
            return "ok"

        taskRequest = asyncio.get_running_loop().create_task(
            commitCarrier.fdictRunLockHeldMutation(
                app.state, S_CONTAINER_NAME, S_CONTAINER_ID,
                dictLaneTuple, "helper", "shutdownRace", fnWorker,
            ),
        )
        await asyncio.to_thread(eventStarted.wait, 5)
        for fnShutdown in list(app.state.listLifespanShutdown):
            await fnShutdown(app)
        assert S_CONTAINER_NAME in app.state.dictContainerOwners
        with pytest.raises(containerLock.ContainerLockedError):
            containerLock.fnAcquireContainerLock(S_CONTAINER_NAME, 9999)
        eventRelease.set()
        dictOutcome = await asyncio.wait_for(taskRequest, 5)
        assert dictOutcome["bCommitted"] is True
        containerLock.fnReleaseContainerLock(fileHandleLock)

    asyncio.run(_fnDrive())


# ---------------------------------------------------------------------
# Case 31 (carrier half) — out-of-band cancel; supervisor is the
# single settler/releaser. The transfer competitor arrives in slice 5;
# a competing lock acquirer stands in for it here.
# ---------------------------------------------------------------------

@pytest.mark.falsification
def test_out_of_band_cancel_leaves_supervisor_as_single_releaser():
    """A cancel signals the supervisor; it never settles or unlocks.

    Case 31 (carrier half): the cancel plane returns immediately while
    the drain is held (it never acquires the lock, so it cannot
    deadlock on the worker it terminates), the journal record survives
    as ``CANCEL_REQUESTED`` while the worker is still alive, and only
    the supervisor — after the worker truly terminates — settles the
    record and releases the lock to the competing acquirer.

    Kills: making the cancel plane settle the journal record itself
    (``fnSettleOperation`` inside ``fdictRequestLockHeldCancel``),
    which clears the write-ahead record while the worker can still
    commit.
    """
    async def _fnDrive():
        appState, _, dictLaneTuple = _ftBuildOwnedAppState()
        eventCancelSeen = threading.Event()

        def fnWorker(supervisor):
            supervisor.eventCancelRequested.wait(5)
            eventCancelSeen.set()
            time.sleep(0.3)
            return "aborted-cleanly"

        taskRequest = asyncio.get_running_loop().create_task(
            commitCarrier.fdictRunLockHeldMutation(
                appState, S_CONTAINER_NAME, S_CONTAINER_ID,
                dictLaneTuple, "helper", "cancelRace", fnWorker,
            ),
        )
        supervisor = await _fsupervisorWaitRegistered(appState)
        lockMutation = sessionLifecycle.flockContainerMutationForAppState(
            appState, S_CONTAINER_NAME,
        )
        taskCompeting = asyncio.get_running_loop().create_task(
            lockMutation.acquire(),
        )
        await asyncio.sleep(0)
        dictCancel = commitCarrier.fdictRequestLockHeldCancel(
            appState, S_CONTAINER_NAME, dictLaneTuple,
        )
        assert dictCancel["bCancelSignalled"] is True
        await asyncio.to_thread(eventCancelSeen.wait, 5)
        dictOutcomeRead = operationJournal.fdictReadJournalOutcome(
            S_CONTAINER_NAME,
        )
        assert dictOutcomeRead["dictOperations"][
            supervisor.sOperationId
        ]["sState"] == "CANCEL_REQUESTED"
        assert not taskCompeting.done()
        dictOutcome = await asyncio.wait_for(taskRequest, 5)
        assert dictOutcome["bCancelRequested"] is True
        await asyncio.wait_for(taskCompeting, 2)
        lockMutation.release()
        assert operationJournal.fdictReadJournalOutcome(
            S_CONTAINER_NAME,
        )["dictOperations"] == {}

    asyncio.run(_fnDrive())


async def _fsupervisorWaitRegistered(appState):
    """Wait until the supervisor exists and journaled its operation."""
    for _ in range(500):
        for supervisor in appState.dictMutationSupervisors.values():
            if supervisor.sOperationId:
                return supervisor
        await asyncio.sleep(0.01)
    raise AssertionError("supervisor never registered its operation")


# ---------------------------------------------------------------------
# Case 32 (carrier half) — the reaper cannot release live guarded work.
# ---------------------------------------------------------------------

@pytest.mark.falsification
def test_reaper_never_releases_owner_with_live_guarded_work():
    """The idle reaper is vetoed while a guarded worker lives.

    Case 32 (carrier half; the full cancel-versus-reaper race rides on
    slice 5's transfer): a past-grace, zero-socket owner record whose
    container hosts a live mutation supervisor is NOT reaped — the
    supervisor is the single releasing party — and is reaped normally
    once the supervisor is gone.

    Kills: dropping ``fbContainerHasLiveMutationWork`` from the busy
    veto in ``serverLifespan._fnReapIdleOwnershipsForApp``.
    """
    recordOwner = OwnerRecord(
        sLeaseId="lease-1", fileHandleLock=None,
        sContainerId=S_CONTAINER_ID, sBrowserSessionId="sess-1",
    )
    recordOwner.fLastSeenMonotonic = time.monotonic() - 10000.0
    appState = SimpleNamespace(
        bReapOwnerships=True,
        dictContainerOwners={S_CONTAINER_NAME: recordOwner},
        dictSessionOwner={},
        dictMutationSupervisors={}, dictDurableTaskRecords={},
    )
    app = SimpleNamespace(state=appState)
    supervisor = commitCarrier.MutationSupervisor(
        sSupervisorId="s1", sName=S_CONTAINER_NAME,
        sContainerId=S_CONTAINER_ID, dictLaneTuple={},
    )
    supervisor.taskSupervisor = SimpleNamespace(done=lambda: False)
    appState.dictMutationSupervisors["s1"] = supervisor
    dictCtx = {
        "docker": SimpleNamespace(flistGetRunningContainers=lambda: []),
    }
    serverLifespan._fnReapIdleOwnershipsForApp(app, dictCtx)
    assert S_CONTAINER_NAME in appState.dictContainerOwners
    appState.dictMutationSupervisors.clear()
    serverLifespan._fnReapIdleOwnershipsForApp(app, dictCtx)
    assert S_CONTAINER_NAME not in appState.dictContainerOwners


# ---------------------------------------------------------------------
# Case 38 — the identity gate: own id + holder proceeds; a different
# op/holder, malformed journal, or any NEEDS_RECONCILIATION refuses.
# ---------------------------------------------------------------------

@pytest.mark.falsification
def test_identity_gate_admits_own_record_and_refuses_foreign_holder():
    """Only the matching operation id AND holder identity may commit.

    Case 38 (holder half): the operation's own record admits it with
    no self-deadlock; a different holder identity under the same id,
    and an id with no record, are both refused.

    Kills: neutralizing the holder-identity comparison in
    ``mutationAdmission.fnAssertOperationAdmittedByIdentity`` so any
    holder passes under a present record.
    """
    sOperationId = operationJournal.fsPrepareOperation(
        S_CONTAINER_NAME, "exec", S_CONTAINER_ID,
    )
    operationJournal.fnPromoteOperationToInFlight(
        S_CONTAINER_NAME, sOperationId, {"sDockerExecId": "abc123"},
    )
    fnAssertOperationAdmittedByIdentity(
        S_CONTAINER_NAME, sOperationId, {"sDockerExecId": "abc123"},
    )
    with pytest.raises(MutationNotAdmittedError):
        fnAssertOperationAdmittedByIdentity(
            S_CONTAINER_NAME, sOperationId, {"sDockerExecId": "OTHER"},
        )
    with pytest.raises(MutationNotAdmittedError):
        fnAssertOperationAdmittedByIdentity(
            S_CONTAINER_NAME, "unknown-operation", {},
        )


@pytest.mark.falsification
def test_identity_gate_refuses_sitting_owner_mid_quarantine():
    """No operation resumes while any record needs reconciliation.

    Case 38 (quarantine half): a sitting owner's own, correctly
    identified operation is refused when ANOTHER record in the set is
    ``NEEDS_RECONCILIATION`` — and a malformed journal refuses
    everything (fail closed).

    Kills: neutralizing the NEEDS_RECONCILIATION scan in
    ``mutationAdmission.fnAssertOperationAdmittedByIdentity``.
    """
    sMineId = operationJournal.fsPrepareOperation(
        S_CONTAINER_NAME, "exec", S_CONTAINER_ID,
    )
    operationJournal.fnPromoteOperationToInFlight(
        S_CONTAINER_NAME, sMineId, {"sDockerExecId": "mine"},
    )
    sOtherId = operationJournal.fsPrepareOperation(
        S_CONTAINER_NAME, "helper", "otherWork",
    )
    operationJournal.fnPromoteOperationToInFlight(
        S_CONTAINER_NAME, sOtherId,
        {"iHolderPid": 12345, "iHolderProcessGroup": 12345},
    )
    fnAssertOperationAdmittedByIdentity(
        S_CONTAINER_NAME, sMineId, {"sDockerExecId": "mine"},
    )
    operationJournal.fnMarkOperationNeedsReconciliation(
        S_CONTAINER_NAME, sOtherId, sNote="wedged",
    )
    with pytest.raises(MutationNotAdmittedError):
        fnAssertOperationAdmittedByIdentity(
            S_CONTAINER_NAME, sMineId, {"sDockerExecId": "mine"},
        )
    sMalformedPath = operationJournal.fsJournalPathFor("malformedproj")
    os.makedirs(operationJournal._S_JOURNAL_DIRECTORY, exist_ok=True)
    with open(sMalformedPath, "wb") as fileHandle:
        fileHandle.write(b"{ torn")
    with pytest.raises(MutationNotAdmittedError):
        fnAssertOperationAdmittedByIdentity("malformedproj", "any", {})


# ---------------------------------------------------------------------
# Case 45 (carrier half) — a per-container SET of records; each
# operation is admitted against its OWN record while others coexist.
# ---------------------------------------------------------------------

@pytest.mark.falsification
def test_carrier_admits_each_operation_against_its_own_record():
    """Coexisting journal records never refuse each other's owner.

    Case 45 (carrier half): an exec, a helper, and a file-write record
    coexist in one container's journal set, and each operation passes
    the identity gate against its own record — presence of the others
    is never a refusal.

    Kills: an added presence-based refusal (raise when more than one
    record exists) in
    ``mutationAdmission.fnAssertOperationAdmittedByIdentity``.
    """
    sExecId = operationJournal.fsPrepareOperation(
        S_CONTAINER_NAME, "exec", S_CONTAINER_ID,
    )
    operationJournal.fnPromoteOperationToInFlight(
        S_CONTAINER_NAME, sExecId, {"sDockerExecId": "exec-a"},
    )
    sHelperId = operationJournal.fsPrepareOperation(
        S_CONTAINER_NAME, "helper", "terminalDrain",
    )
    operationJournal.fnPromoteOperationToInFlight(
        S_CONTAINER_NAME, sHelperId,
        {"iHolderPid": os.getpid(), "iHolderProcessGroup": os.getpgrp()},
    )
    sWriteId = operationJournal.fsPrepareOperation(
        S_CONTAINER_NAME, "file-write", "/workspace/project.json",
    )
    operationJournal.fnPromoteOperationToInFlight(
        S_CONTAINER_NAME, sWriteId,
        {"sExpectedSha256": "aa", "sPriorSha256": "bb"},
    )
    fnAssertOperationAdmittedByIdentity(
        S_CONTAINER_NAME, sExecId, {"sDockerExecId": "exec-a"},
    )
    fnAssertOperationAdmittedByIdentity(
        S_CONTAINER_NAME, sHelperId, {"iHolderPid": os.getpid()},
    )
    fnAssertOperationAdmittedByIdentity(
        S_CONTAINER_NAME, sWriteId, {"sExpectedSha256": "aa"},
    )


# ---------------------------------------------------------------------
# Case 41 (gate part) — kill a REAL parent at each two-phase
# transition; no transition leaves an unidentified writer or an effect.
# ---------------------------------------------------------------------

S_GATE_DRIVER_SOURCE = """
import sys, time
sPhaseStop = sys.argv[1]
sContainerName = sys.argv[2]
sEffectPath = sys.argv[3]


def fnHook(sPhase):
    print("PHASE:" + sPhase, flush=True)
    if sPhase == sPhaseStop:
        time.sleep(60)


from vaibify.gui.commitCarrier import fdictLaunchGatedHelperProcess
fdictLaunchGatedHelperProcess(
    sContainerName, "gateKillTest",
    [sys.executable, "-c",
     "open(%r, 'w').write('acted')" % sEffectPath],
    fnPhaseCallback=fnHook,
)
print("PHASE:released", flush=True)
time.sleep(60)
"""


def _fprocessRunGateDriver(tmp_path, sPhaseStop):
    """Spawn the REAL parent process and stall it at one phase."""
    sHomeDirectory = tmp_path / f"home-{sPhaseStop}"
    sHomeDirectory.mkdir()
    sDriverPath = tmp_path / "gateDriver.py"
    sDriverPath.write_text(S_GATE_DRIVER_SOURCE)
    sEffectPath = tmp_path / f"effect-{sPhaseStop}"
    dictEnvironment = dict(os.environ)
    dictEnvironment["HOME"] = str(sHomeDirectory)
    dictEnvironment["PYTHONPATH"] = (
        str(REPO_ROOT) + os.pathsep + dictEnvironment.get("PYTHONPATH", "")
    )
    processParent = subprocess.Popen(
        [sys.executable, str(sDriverPath), sPhaseStop, S_CONTAINER_NAME,
         str(sEffectPath)],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        env=dictEnvironment,
    )
    while True:
        sLine = processParent.stdout.readline().decode("utf-8")
        if not sLine:
            raise AssertionError(
                "gate driver exited before reaching phase "
                f"{sPhaseStop}: {processParent.stderr.read().decode()}"
            )
        if sLine.strip() == f"PHASE:{sPhaseStop}":
            break
    return processParent, sHomeDirectory, sEffectPath


def _fdictWaitForSettledResolution(fSeconds=6.0):
    """Poll the auto-probe tier until the leftover record settles."""
    fDeadline = time.monotonic() + fSeconds
    while True:
        dictResolution = operationJournal.fdictResolveContainerJournal(
            S_CONTAINER_NAME,
        )
        if dictResolution["sResolution"] == (
            operationJournal.S_RESOLUTION_SETTLED
        ):
            return dictResolution
        if time.monotonic() >= fDeadline:
            return dictResolution
        time.sleep(0.1)


@pytest.mark.falsification
@pytest.mark.parametrize("sPhaseStop, sExpectedLeftoverState", [
    ("prepared", "PREPARED"),
    ("spawned", "PREPARED"),
    ("promoted", "IN_FLIGHT"),
])
def test_parent_kill_at_each_two_phase_transition_leaves_no_actor(
    tmp_path, monkeypatch, sPhaseStop, sExpectedLeftoverState,
):
    """SIGKILL the real parent at each transition; nothing ever acts.

    Case 41 (gate part), with REAL child processes: killed after
    PREPARED-before-spawn, after spawn-before-identity-persist (the
    helper self-aborts at the gate), and after identity-persist-before
    -release — none leaves an unidentified writer, the effect file
    never appears, and the leftover record auto-clears as provably
    never-acted / dead-and-settled.

    Kills: reordering ``commitCarrier.S_GATED_HELPER_STUB`` so the
    helper runs its command BEFORE reading the stdin gate — the killed
    parent then leaves a landed effect with no releasing gate, which
    the effect-file assertion catches.
    """
    processParent, sHomeDirectory, sEffectPath = _fprocessRunGateDriver(
        tmp_path, sPhaseStop,
    )
    os.kill(processParent.pid, signal.SIGKILL)
    processParent.wait(timeout=10)
    fDeadline = time.monotonic() + 2.0
    while time.monotonic() < fDeadline:
        assert not sEffectPath.exists(), (
            "the gated helper acted although its parent died before "
            "releasing the gate"
        )
        time.sleep(0.1)
    monkeypatch.setattr(
        operationJournal, "_S_JOURNAL_DIRECTORY",
        str(sHomeDirectory / ".vaibify" / "journal"),
    )
    dictOutcomeRead = operationJournal.fdictReadJournalOutcome(
        S_CONTAINER_NAME,
    )
    listStates = [
        dictRecord["sState"]
        for dictRecord in dictOutcomeRead["dictOperations"].values()
    ]
    assert listStates == [sExpectedLeftoverState]
    dictResolution = _fdictWaitForSettledResolution()
    assert dictResolution["sResolution"] == (
        operationJournal.S_RESOLUTION_SETTLED
    )
    assert not sEffectPath.exists()


# ---------------------------------------------------------------------
# Supporting structure: forgery, mode (a), mode (c), drain, minting.
# ---------------------------------------------------------------------

def test_mutation_admission_cannot_be_forged():
    """A route constructing MutationAdmission directly is refused."""
    with pytest.raises(MutationNotAdmittedError):
        MutationAdmission(
            object(), S_CONTAINER_NAME, S_CONTAINER_ID, "request",
        )


def test_mint_function_is_referenced_only_by_the_carrier():
    """The private mint has exactly two homes: its module, the carrier.

    This is what keeps "unforgeable" a tested property instead of a
    convention: any new module that reaches for the mint fails here.
    """
    listOffenders = []
    for pathFile in sorted((REPO_ROOT / "vaibify").rglob("*.py")):
        sRelative = pathFile.relative_to(REPO_ROOT).as_posix()
        if sRelative in (
            "vaibify/config/mutationAdmission.py",
            "vaibify/gui/commitCarrier.py",
        ):
            continue
        if "_fadmissionMintForCommitCarrier" in pathFile.read_text(
            encoding="utf-8",
        ):
            listOffenders.append(sRelative)
    assert listOffenders == []


def test_mode_a_commits_journals_and_settles():
    """Mode (a): prepared -> in-flight -> effect -> settled, no await."""
    appState, _, dictLaneTuple = _ftBuildOwnedAppState()
    stubContainer = _StubContainer(S_CONTAINER_ID)
    connectionDocker = _fconnectionBuildStubbedDockerConnection(
        stubContainer,
    )
    dictCommit = commitCarrier.fdictCommitSynchronousMutation(
        appState, S_CONTAINER_NAME, S_CONTAINER_ID, dictLaneTuple,
        "file-write", "/workspace/project.json",
        lambda: connectionDocker.fnWriteFile(
            S_CONTAINER_ID, "/workspace/project.json", b"{}",
        ),
        {
            "sDockerContainerId": S_CONTAINER_ID,
            "sExpectedSha256": "aa", "sPriorSha256": "bb",
        },
    )
    assert dictCommit["bCommitted"] is True
    assert dictCommit["bJournalSettled"] is True
    assert stubContainer.listPutArchiveCalls == ["/workspace"]
    assert operationJournal.fdictReadJournalOutcome(
        S_CONTAINER_NAME,
    )["sReadState"] == "absent"


def test_mode_a_refuses_stale_generation_without_committing():
    """A rotated owner generation refuses the commit; nothing runs."""
    appState, recordOwner, dictLaneTuple = _ftBuildOwnedAppState()
    recordOwner.iOwnerGeneration = 2
    listEffects = []
    with pytest.raises(commitCarrier.CommitRefusedError):
        commitCarrier.fdictCommitSynchronousMutation(
            appState, S_CONTAINER_NAME, S_CONTAINER_ID, dictLaneTuple,
            "file-write", "/workspace/project.json",
            lambda: listEffects.append("ran"),
            {"sExpectedSha256": "aa", "sPriorSha256": "bb"},
        )
    assert listEffects == []
    assert operationJournal.fdictReadJournalOutcome(
        S_CONTAINER_NAME,
    )["sReadState"] == "absent"


def test_mode_c_refuses_second_durable_launch_and_compare_matches():
    """Mode (c): one live durable task per container; id-matched cleanup."""
    async def _fnDrive():
        appState, _, dictLaneTuple = _ftBuildOwnedAppState()
        eventRelease = asyncio.Event()

        async def _fnBody():
            await eventRelease.wait()
            return "ran"

        dictLaunch = await commitCarrier.fdictLaunchDurableTask(
            appState, S_CONTAINER_NAME, S_CONTAINER_ID, dictLaneTuple,
            lambda: asyncio.get_running_loop().create_task(_fnBody()),
        )
        assert dictLaunch["bLaunched"] is True
        dictSecond = await commitCarrier.fdictLaunchDurableTask(
            appState, S_CONTAINER_NAME, S_CONTAINER_ID, dictLaneTuple,
            lambda: asyncio.get_running_loop().create_task(_fnBody()),
        )
        assert dictSecond["bLaunched"] is False
        dictStaleCancel = await commitCarrier.fdictRequestDurableTaskCancel(
            appState, S_CONTAINER_NAME, "not-the-stable-id",
            dictLaneTuple,
        )
        assert dictStaleCancel["bCancelled"] is False
        eventRelease.set()
        await dictLaunch["taskAsync"]
        await asyncio.sleep(0.05)
        assert appState.dictDurableTaskRecords == {}

    asyncio.run(_fnDrive())


def test_mode_c_cancel_refuses_and_leaves_the_task_running():
    """Mode (c) has no generic cancel, and says so without pretending.

    A durable task's worker runs in a thread Python cannot interrupt,
    so cancelling the asyncio task stops only the AWAITING of it. The
    previous implementation did exactly that and then removed the
    registry entry, which is the dangerous half: release, transfer and
    the reaper stopped seeing work that was still running, against a
    container they were now free to hand to somebody else.

    Asserted on the STATE, not just the answer -- a refusal that had
    already cancelled the task would still return bCancelled False.
    """
    async def _fnDrive():
        appState, _, dictLaneTuple = _ftBuildOwnedAppState()
        eventRelease = asyncio.Event()

        async def _fnBody():
            await eventRelease.wait()
            return "ran"

        dictLaunch = await commitCarrier.fdictLaunchDurableTask(
            appState, S_CONTAINER_NAME, S_CONTAINER_ID, dictLaneTuple,
            lambda: asyncio.get_running_loop().create_task(_fnBody()),
        )
        dictCancel = await commitCarrier.fdictRequestDurableTaskCancel(
            appState, S_CONTAINER_NAME, dictLaunch["sTaskId"],
            dictLaneTuple,
        )
        assert dictCancel["bCancelled"] is False
        assert dictCancel["bSupported"] is False
        assert not dictLaunch["taskAsync"].cancelled(), (
            "the refusal cancelled the asyncio task anyway"
        )
        assert appState.dictDurableTaskRecords, (
            "the refusal removed the registry entry, so the container "
            "now looks idle while its worker is still running"
        )
        eventRelease.set()
        assert await dictLaunch["taskAsync"] == "ran"
        await asyncio.sleep(0.05)
        assert appState.dictDurableTaskRecords == {}

    asyncio.run(_fnDrive())


def test_closed_admissions_refuse_every_carrier_mode():
    """After shutdown begins, no carrier mode admits a new mutation."""
    async def _fnDrive():
        appState, _, dictLaneTuple = _ftBuildOwnedAppState()
        await commitCarrier.fdictDrainMutationSupervisors(
            appState, fTimeoutSeconds=0.01,
        )
        with pytest.raises(commitCarrier.CommitRefusedError):
            commitCarrier.fdictCommitSynchronousMutation(
                appState, S_CONTAINER_NAME, S_CONTAINER_ID,
                dictLaneTuple, "file-write", "t", lambda: None,
                {"sExpectedSha256": "aa"},
            )
        with pytest.raises(commitCarrier.CommitRefusedError):
            await commitCarrier.fdictRunLockHeldMutation(
                appState, S_CONTAINER_NAME, S_CONTAINER_ID,
                dictLaneTuple, "helper", "t", lambda supervisor: None,
            )
        with pytest.raises(commitCarrier.CommitRefusedError):
            await commitCarrier.fdictLaunchDurableTask(
                appState, S_CONTAINER_NAME, S_CONTAINER_ID,
                dictLaneTuple,
                lambda: asyncio.get_running_loop().create_task(
                    asyncio.sleep(0),
                ),
            )

    asyncio.run(_fnDrive())


def test_durable_task_journals_execs_through_create_journal_start():
    """A mode-(c) task's streamed exec is journaled and settles."""
    async def _fnDrive():
        appState, _, dictLaneTuple = _ftBuildOwnedAppState()
        stubContainer = _StubContainer(S_CONTAINER_ID)
        connectionDocker = _fconnectionBuildStubbedDockerConnection(
            stubContainer,
        )

        async def _fnBody():
            return await asyncio.to_thread(
                connectionDocker.texecRunInContainerStreamedWithChunks,
                S_CONTAINER_ID, "python run.py", None, None, "runner",
            )

        dictLaunch = await commitCarrier.fdictLaunchDurableTask(
            appState, S_CONTAINER_NAME, S_CONTAINER_ID, dictLaneTuple,
            lambda: asyncio.get_running_loop().create_task(_fnBody()),
        )
        resultExec = await dictLaunch["taskAsync"]
        assert resultExec.iExitCode == 0
        listCreates = (
            connectionDocker._clientDocker.api.listExecCreateCalls
        )
        assert listCreates == [S_CONTAINER_ID]
        assert operationJournal.fdictReadJournalOutcome(
            S_CONTAINER_NAME,
        )["sReadState"] == "absent"

    asyncio.run(_fnDrive())


# ---------------------------------------------------------------------
# The carrier runs workers in a THREAD, so an async worker is a bug.
# ---------------------------------------------------------------------

# The source-shape check that used to live here is gone. It read the
# worker argument at every call site and flagged a same-module
# `async def` passed as the seventh positional argument -- which caught
# the one spelling that had already burned us and missed keyword
# arguments, aliases, imported functions, lambdas returning coroutines
# and async callable objects. The carrier now refuses a coroutine
# worker at RUNTIME, at the public entrance and again at the call, so
# the weaker check bought nothing and cost an exemption list: it flagged
# the tests below, whose whole purpose is to hand the carrier an
# `async def` and watch it be refused.


def testACoroutineWorkerIsRefusedAtRuntime():
    """The declaration check: an async worker never silently no-ops."""
    async def _fnAsyncWorker(supervisor):
        del supervisor
        return "never runs"

    with pytest.raises(TypeError, match="coroutine function"):
        commitCarrier._fnCallWorkerSynchronously(
            _fnAsyncWorker, object(),
        )


def testAWorkerReturningAnAwaitableIsRefusedAtRuntime():
    """The result check, for the shapes a declaration check cannot see.

    A lambda returning a coroutine, a callable object with an async
    ``__call__``, a sync wrapper that forgot to await: none of these is
    a coroutine FUNCTION, and all of them would have the same effect --
    the work never runs while the carrier reports success.
    """
    async def _fnInner():
        return 1

    class _CallableWithAsyncCall:
        async def __call__(self, supervisor):
            del supervisor
            return 1

    for fnWorker in (
        lambda supervisor: _fnInner(),
        _CallableWithAsyncCall(),
    ):
        with pytest.raises(TypeError, match="awaitable|coroutine"):
            commitCarrier._fnCallWorkerSynchronously(fnWorker, object())


def testASynchronousWorkerStillRuns():
    """The negative control: a refusal that refused everything is useless."""
    listRan = []

    def _fnWorker(supervisor):
        del supervisor
        listRan.append("ran")
        return "done"

    assert commitCarrier._fnCallWorkerSynchronously(
        _fnWorker, object(),
    ) == "done"
    assert listRan == ["ran"]


def testACoroutineWorkerIsRefusedBeforeAnythingIsJournaled():
    """An `async def` is a programming error, not a container quarantine.

    The declaration check used to run inside the supervisor, after the
    operation journal record was prepared and promoted. So a worker
    somebody wrote with `async def` -- whose body never executed and
    which therefore touched nothing -- went through the failed-worker
    settlement and left the container NEEDING RECONCILIATION. The
    researcher would be told to run 'vaibify reconcile' because of a
    keyword.

    Asserted on the JOURNAL, not the exception: an early raise that
    still journaled first would pass a check that only caught TypeError.
    """
    async def _fnDrive():
        appState, _, dictLaneTuple = _ftBuildOwnedAppState()

        async def _fnAsyncWorker(supervisor):
            del supervisor
            return "never runs"

        with pytest.raises(TypeError, match="coroutine function"):
            await commitCarrier.fdictRunLockHeldMutation(
                appState, S_CONTAINER_NAME, S_CONTAINER_ID,
                dictLaneTuple, "file-write", "/workspace/project.json",
                _fnAsyncWorker,
            )
        assert operationJournal.fdictReadJournalOutcome(
            S_CONTAINER_NAME,
        )["sReadState"] == "absent", (
            "a coroutine worker journaled an operation before being "
            "refused, so a keyword quarantined a container"
        )
        assert not commitCarrier.fbContainerHasLiveMutationWork(
            appState, S_CONTAINER_NAME,
        )

    asyncio.run(_fnDrive())


def testAnAwaitableWithoutCloseStillRaisesTypeError():
    """A custom awaitable need not have close(); the diagnosis survives.

    Calling close() blindly raised AttributeError and buried the real
    reason the worker was refused.
    """
    class _AwaitableWithoutClose:
        def __await__(self):
            yield
            return 1

    def _fnWorker(supervisor):
        del supervisor
        return _AwaitableWithoutClose()

    with pytest.raises(TypeError, match="awaitable"):
        commitCarrier._fnCallWorkerSynchronously(_fnWorker, object())
