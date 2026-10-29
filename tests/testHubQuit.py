"""Quit: one busy predicate, a closable admission gate, and its routes.

The idle watchdog and Quit ask one question, ``flistHubBusyReasons``, so
the two cannot disagree about whether the hub may stop. Quit then
closes the admission gate (``mutationAdmission``) so nothing starts
between its final check and the shutdown, and reopens every gate it
closed on any exit but success.

The falsification tests here drive the real carrier and the real
pipeline-socket message loop, never a stub of the predicate under test:
a slow lock-held mutation is a real ``fdictRunLockHeldMutation`` whose
worker sleeps in its thread, and a pipeline run is a real durable launch.
"""

import asyncio
import threading
import time
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from vaibify.config import containerLock, mutationAdmission
from vaibify.gui import commitCarrier, hubQuit, pipelineServer, serverLifespan
from vaibify.gui import hostControlChannel
from vaibify.gui.containerOwnership import OwnerRecord
from tests.sessionTokenTestHelper import fsBootstrapCredential
from tests.testProjectScopedAgentActions import _FakeRunSocket


S_CONTAINER_NAME = "quitproj"
S_CONTAINER_ID = "quitcid123"


class _LiveTask:
    """A durable task that is still running, as the registry sees one."""

    def done(self):
        return False


def _fappBuildHub():
    with patch.object(pipelineServer, "_fconnectionCreateDocker",
                      lambda: MagicMock()):
        from vaibify.gui import appFactory
        return appFactory.fappCreateHubApplication()


def _fnSeedLiveDurableTask(appState, sOperation="a pipeline run"):
    appState.dictDurableTaskRecords[S_CONTAINER_NAME] = (
        commitCarrier.DurableTaskRecord(
            sTaskId="task-1", sName=S_CONTAINER_NAME,
            sContainerId=S_CONTAINER_ID, iOwnerGeneration=1,
            taskAsync=_LiveTask(), admission=None, sOperation=sOperation,
        )
    )


def _fdictSeedOwner(app):
    """Seed an owned container and return its browser lane tuple."""
    fileHandleLock = containerLock.ffileAcquireContainerLock(
        S_CONTAINER_NAME, 8137,
    )
    app.state.dictContainerOwners[S_CONTAINER_NAME] = OwnerRecord(
        sLeaseId="lease-1", fileHandleLock=fileHandleLock,
        sContainerId=S_CONTAINER_ID, sBrowserSessionId="sess-1",
    )
    return {
        "sLane": "browser", "iOwnerGeneration": 1,
        "sBrowserSessionId": "sess-1", "sLeaseId": "lease-1",
        "sContainerName": S_CONTAINER_NAME,
    }


# ---------------------------------------------------------------------
# The gate.
# ---------------------------------------------------------------------

def testAClosedGateRefusesNewWorkAndReopensByItsOwnId():
    sGateId = mutationAdmission.fsCloseAdmissions()
    assert mutationAdmission.fbAdmissionGateClosed()
    with pytest.raises(mutationAdmission.AdmissionsClosedError):
        mutationAdmission.fnAssertAdmissionGateOpen("A test start")
    mutationAdmission.fnReopenAdmissions("not-the-gate")
    assert mutationAdmission.fbAdmissionGateClosed()
    mutationAdmission.fnReopenAdmissions(sGateId)
    assert not mutationAdmission.fbAdmissionGateClosed()
    mutationAdmission.fnAssertAdmissionGateOpen("A test start")


def testASecondCloseIsRefusedWhileTheFirstHolds():
    mutationAdmission.fsCloseAdmissions()
    with pytest.raises(mutationAdmission.AdmissionsClosedError,
                       match="already in progress"):
        mutationAdmission.fsCloseAdmissions()


def testALeasedGateLapsesAndALapsedGateIsNotRenewed():
    sGateId = mutationAdmission.fsCloseAdmissions(fLeaseSeconds=0.05)
    assert mutationAdmission.fbRenewAdmissions(sGateId, 0.05)
    time.sleep(0.1)
    assert not mutationAdmission.fbAdmissionGateClosed()
    assert not mutationAdmission.fbRenewAdmissions(sGateId, 10.0)
    assert not mutationAdmission.fbAdmissionGateClosed()


def testWorkAlreadyGrantedFinishesThroughAClosedGate():
    """A commit inside a live durable admission is not new work."""
    mutationAdmission.fsCloseAdmissions()
    admission = mutationAdmission._fadmissionMintForCommitCarrier(
        S_CONTAINER_NAME, S_CONTAINER_ID,
        mutationAdmission.S_ADMISSION_MODE_DURABLE_TASK, bDurable=True,
    )
    tokenAdmission = mutationAdmission.ftokenActivateAdmission(admission)
    try:
        assert not mutationAdmission.fbAdmissionGateClosedForNewWork()
    finally:
        mutationAdmission.fnDeactivateAdmission(tokenAdmission)
    assert mutationAdmission.fbAdmissionGateClosedForNewWork()


def testARequestAdmissionIsNotWorkAlreadyGranted():
    mutationAdmission.fsCloseAdmissions()
    admission = mutationAdmission._fadmissionMintForCommitCarrier(
        S_CONTAINER_NAME, S_CONTAINER_ID,
        mutationAdmission.S_ADMISSION_MODE_REQUEST,
    )
    tokenAdmission = mutationAdmission.ftokenActivateAdmission(admission)
    try:
        assert mutationAdmission.fbAdmissionGateClosedForNewWork()
    finally:
        mutationAdmission.fnDeactivateAdmission(tokenAdmission)


@pytest.mark.asyncio
@pytest.mark.falsification
async def testAStaleReopenLeavesANewerGateClosed():
    """A reopen naming a lapsed Quit's gate cannot open the next Quit's.

    Kills: ``fnReopenAdmissions`` ignoring the gate id it is handed.
    """
    sStaleGateId = mutationAdmission.fsCloseAdmissions(fLeaseSeconds=0.05)
    await asyncio.sleep(0.1)
    sNewerGateId = mutationAdmission.fsCloseAdmissions(fLeaseSeconds=30.0)
    dictResponse = await hostControlChannel._fdictHandleReopenAdmissions(
        MagicMock(), {},
        {"dictGateTree": {"iPort": 0, "sGateId": sStaleGateId,
                          "listChildren": []}},
    )
    assert dictResponse["bAccepted"] is True
    assert mutationAdmission.fbAdmissionGateClosed()
    mutationAdmission.fnReopenAdmissions(sNewerGateId)
    assert not mutationAdmission.fbAdmissionGateClosed()


def testTheCouncilAdmitsNoTurnWhileTheGateIsClosed():
    from vaibify.gui import agentCouncilRegistry
    dictRegistry = agentCouncilRegistry.fdictCreateCouncilRegistry()
    mutationAdmission.fsCloseAdmissions()
    dictReserved = agentCouncilRegistry.fdictReserveRunner(
        dictRegistry, "campaign-1", "reservation-1", "anthropic", {},
    )
    assert dictReserved["bReserved"] is False
    assert "shutting down" in dictReserved["sRefusalReason"]


@pytest.mark.falsification
def testANewWebSocketIsRefusedWhileTheGateIsClosed():
    """An authorized new socket is refused 4505 once a Quit closes the gate.

    Kills: the socket gate answering "authorized" whatever the gate says.
    """
    from vaibify.gui import webSocketAuthorization
    with patch.object(webSocketAuthorization, "fbCheckOrigin",
                      return_value=False), \
            patch.object(webSocketAuthorization, "fbCheckAgentToken",
                         return_value=True):
        assert webSocketAuthorization.fiContainerSessionRejectionCode(
            MagicMock(), {}, S_CONTAINER_NAME) == 0
        mutationAdmission.fsCloseAdmissions()
        assert webSocketAuthorization.fiContainerSessionRejectionCode(
            MagicMock(), {}, S_CONTAINER_NAME,
        ) == webSocketAuthorization.I_REJECT_SHUTTING_DOWN


@pytest.mark.asyncio
async def testAClaimIsRefusedWhileTheGateIsClosed():
    from vaibify.gui import sessionLifecycle
    from tests.testHostTransfer import _fstateBuildAppState
    mutationAdmission.fsCloseAdmissions()
    with pytest.raises(mutationAdmission.AdmissionsClosedError):
        await sessionLifecycle.ftClaimWithCardinality(
            _fstateBuildAppState(), S_CONTAINER_NAME, "lease", 0,
        )


# ---------------------------------------------------------------------
# The busy predicate.
# ---------------------------------------------------------------------

def testTheBusyReasonsNameEveryInFlightCarrierItem():
    app = _fappBuildHub()
    dictCtx = app.state.dictRouteContext
    assert serverLifespan.flistHubBusyReasons(app, dictCtx) == []
    _fnSeedLiveDurableTask(app.state, sOperation="a verification")
    supervisor = commitCarrier.MutationSupervisor(
        sSupervisorId="sup-1", sName="otherproj", sContainerId="cid",
        dictLaneTuple={}, sOperationKind="helper", sTarget="slowWrite",
    )
    app.state.dictMutationSupervisors["sup-1"] = supervisor
    listReasons = serverLifespan.flistHubBusyReasons(app, dictCtx)
    assert "helper on slowWrite in 'otherproj'" in listReasons
    assert f"a verification in '{S_CONTAINER_NAME}'" in listReasons
    assert serverLifespan.fiCountInFlightCarrierWork(app) == 2


def testTheBusyReasonsNameTheModuleLevelWork():
    app = _fappBuildHub()
    from vaibify.gui import conversionLifecycle, reproductionProgress
    with patch.object(reproductionProgress, "fbHubHoldsLiveReproduction",
                      return_value=True), \
            patch.object(conversionLifecycle, "fbHubHoldsLiveConversion",
                         return_value=True):
        listReasons = serverLifespan.flistHubBusyReasons(
            app, app.state.dictRouteContext)
    assert any("reproduction" in sReason for sReason in listReasons)
    assert any("containerized" in sReason for sReason in listReasons)


def testTheWatchdogWaitsForAnInFlightCarrierMutation():
    app = _fappBuildHub()
    app.state.fLastActivityMonotonic = time.monotonic() - 1000
    dictCtx = app.state.dictRouteContext
    assert serverLifespan._fbHubShouldSelfExit(app, dictCtx, 10.0)
    _fnSeedLiveDurableTask(app.state)
    assert not serverLifespan._fbHubShouldSelfExit(app, dictCtx, 10.0)


def testTheTerminalWarningUsesTheContractsWords():
    assert hubQuit.fsDescribeTerminalWarning(0) == ""
    sWarning = hubQuit.fsDescribeTerminalWarning(2)
    assert sWarning == (
        "2 terminals will disconnect. Programs started in them may keep "
        "running inside the environment; vaibify cannot prove they "
        "stopped. Run `vaibify reconcile` afterwards to settle that."
    )
    assert hubQuit.fsDescribeTerminalWarning(1).startswith(
        "1 terminal will disconnect.")


# ---------------------------------------------------------------------
# The routes.
# ---------------------------------------------------------------------

@pytest.fixture
def clientHub():
    app = _fappBuildHub()
    clientBrowser = TestClient(
        app, headers={"X-Session-Token": fsBootstrapCredential(app)},
    )
    return app, clientBrowser


@pytest.fixture
def fixtureShortSettle(monkeypatch):
    monkeypatch.setattr(hubQuit, "F_QUIT_SETTLE_SECONDS", 0.3)
    monkeypatch.setattr(hubQuit, "F_SETTLE_POLL_SECONDS", 0.05)


def testThePreflightReportsTheReasonsAndTheTerminals(clientHub):
    app, clientBrowser = clientHub
    _fnSeedLiveDurableTask(app.state)
    app.state.dictTerminalExecutionRecords[S_CONTAINER_NAME] = {
        "op-1": object(), "op-2": object(),
    }
    dictPreflight = clientBrowser.get("/api/session/quit-preflight").json()
    assert dictPreflight["bCanQuit"] is False
    assert dictPreflight["listReasons"] == [{
        "sWindow": "this window",
        "sReason": f"a pipeline run in '{S_CONTAINER_NAME}'",
    }]
    assert dictPreflight["iOpenTerminals"] == 2
    assert dictPreflight["sTerminalWarning"].startswith(
        "2 terminals will disconnect.")


@pytest.mark.falsification
def testTheAgentLaneCannotQuitOrReadThePreflight(clientHub):
    """Kills: Quit and its preflight dropping their agent-lane refusal."""
    _app, clientBrowser = clientHub
    dictAgentHeaders = {"X-Vaibify-Session": "agent-token"}
    assert clientBrowser.get(
        "/api/session/quit-preflight", headers=dictAgentHeaders,
    ).status_code == 403
    assert clientBrowser.post(
        "/api/session/quit", json={"bStopEnvironments": False},
        headers=dictAgentHeaders,
    ).status_code == 403


def testAnIdleHubQuitsAndSignalsAfterTheReply(clientHub):
    app, clientBrowser = clientHub
    with patch.object(serverLifespan, "fnSignalHubToShutDown") as mockSignal:
        response = clientBrowser.post(
            "/api/session/quit", json={"bStopEnvironments": False})
    assert response.status_code == 202
    assert response.json()["bQuitting"] is True
    assert mockSignal.called
    assert app.state.bQuitTerminationScheduled is True
    assert mutationAdmission.fbAdmissionGateClosed()


@pytest.mark.falsification
def testQuitIsRefusedDuringAPipelineRun(clientHub, fixtureShortSettle):
    """A run that outlasts the settlement refuses the Quit, gates reopened.

    Kills: dropping the final re-check after the settlement.
    """
    app, clientBrowser = clientHub
    _fnSeedLiveDurableTask(app.state)
    with patch.object(serverLifespan, "fnSignalHubToShutDown") as mockSignal:
        response = clientBrowser.post(
            "/api/session/quit", json={"bStopEnvironments": False})
    assert response.status_code == 409
    dictDetail = response.json()["detail"]
    assert {"sWindow": "this window",
            "sReason": f"a pipeline run in '{S_CONTAINER_NAME}'"} in (
        dictDetail["listReasons"])
    assert not mockSignal.called
    assert not mutationAdmission.fbAdmissionGateClosed()


@pytest.mark.falsification
def testAMutationIsRefusedWithItsOwnWordsWhileQuitting(clientHub):
    """A route with no start check of its own is refused at the route layer.

    Registering a project has no gate check in its handler, so only the
    route class's check can refuse it.

    Kills: the route class's Quit-gate check answering "not refused".
    """
    _app, clientBrowser = clientHub
    assert clientBrowser.post("/api/registry", json={}).status_code == 422
    mutationAdmission.fsCloseAdmissions()
    response = clientBrowser.post("/api/registry", json={})
    assert response.status_code == 503
    assert response.json()["detail"]["sRefusal"] == "shutting-down"


def testAFailedStopNamesTheEnvironmentsAlreadyStopped(
    clientHub, fixtureShortSettle, monkeypatch,
):
    app, clientBrowser = clientHub
    app.state.dictContainerOwners["first"] = MagicMock()
    app.state.dictContainerOwners["second"] = MagicMock()
    monkeypatch.setattr(hubQuit, "_ftHeldEnvironments",
                        lambda app, dictCtx: (["first", "second"], False))
    monkeypatch.setattr(serverLifespan, "flistHubBusyReasons",
                        lambda app, dictCtx: [])
    listStopped = []

    def fnStop(sContainerName):
        if sContainerName == "second":
            raise RuntimeError("the daemon refused")
        listStopped.append(sContainerName)

    with patch("vaibify.gui.registryRoutes._fnExecuteStop", fnStop), \
            patch.object(serverLifespan, "fnSignalHubToShutDown") as mockSignal:
        response = clientBrowser.post(
            "/api/session/quit", json={"bStopEnvironments": True})
    assert response.status_code == 409
    assert response.json()["detail"]["listStoppedEnvironments"] == ["first"]
    assert listStopped == ["first"]
    assert not mockSignal.called
    assert not mutationAdmission.fbAdmissionGateClosed()


# ---------------------------------------------------------------------
# The carrier's own check: a slow mutation, and an already-open socket.
# ---------------------------------------------------------------------

@pytest.mark.asyncio
@pytest.mark.falsification
async def testASlowCarrierMutationMakesQuitWaitThenSucceedOrFail(monkeypatch):
    """Within the settlement Quit waits for it; beyond, it fails naming it.

    Kills: dropping in-flight carrier mutations from the busy reasons.
    """
    monkeypatch.setattr(hubQuit, "F_SETTLE_POLL_SECONDS", 0.05)
    app = _fappBuildHub()
    dictCtx = app.state.dictRouteContext
    dictLaneTuple = _fdictSeedOwner(app)
    eventRelease = threading.Event()

    def fnSlowWrite(supervisor):
        del supervisor
        eventRelease.wait(10)
        return "written"

    monkeypatch.setattr(hubQuit, "F_QUIT_SETTLE_SECONDS", 0.4)
    taskMutation = asyncio.ensure_future(commitCarrier.fdictRunLockHeldMutation(
        app.state, S_CONTAINER_NAME, S_CONTAINER_ID, dictLaneTuple,
        "helper", "slowWrite", fnSlowWrite,
    ))
    await asyncio.sleep(0.1)
    with pytest.raises(hubQuit.QuitRefusedError) as errorRefused:
        await hubQuit.fdictRunQuit(app, dictCtx, False)
    assert {"sWindow": "this window",
            "sReason": f"helper on slowWrite in '{S_CONTAINER_NAME}'"} in (
        errorRefused.value.dictDetail["listReasons"])
    assert not mutationAdmission.fbAdmissionGateClosed()

    monkeypatch.setattr(hubQuit, "F_QUIT_SETTLE_SECONDS", 5.0)
    threading.Timer(0.5, eventRelease.set).start()
    fStarted = time.monotonic()
    dictResult = await hubQuit.fdictRunQuit(app, dictCtx, False)
    assert dictResult["bQuitting"] is True
    assert time.monotonic() - fStarted >= 0.3
    assert (await taskMutation)["bCommitted"] is True


class _GateClosingSocket(_FakeRunSocket):
    """An already-open pipeline socket: the Quit closes the gate under it."""

    async def receive_text(self):
        if not mutationAdmission.fbAdmissionGateClosed():
            mutationAdmission.fsCloseAdmissions()
        return await super().receive_text()


async def _ftRunOneFrame(socketFake):
    from tests.testHostTransfer import (
        _dictBuildBrowserLaneTuple, _fstateBuildAppState, _tSeedOwnedContainer,
    )
    stateApp = _fstateBuildAppState()
    sSessionId, _, sLease = _tSeedOwnedContainer(stateApp)
    from tests.testHostTransfer import S_CONTAINER_ID as sSeededId
    from tests.testHostTransfer import S_PROJECT_NAME
    dictDurable = {
        "appState": stateApp, "sName": S_PROJECT_NAME,
        "dictLaneTuple": _dictBuildBrowserLaneTuple(
            stateApp, sSessionId, sLease),
    }
    listDispatched = []

    async def fnRecordDispatch(sAction, *args, **kwargs):
        listDispatched.append(sAction)

    with patch.object(pipelineServer, "fnDispatchAction", fnRecordDispatch):
        with pytest.raises(WebSocketDisconnect):
            await pipelineServer.fnPipelineMessageLoop(
                socketFake, MagicMock(), sSeededId,
                {"sWorkflowName": "Open", "sProjectRepoPath": "/workspace/p",
                 "listSteps": []},
                {sSeededId: "/workspace/p/.vaibify/projects/p.json"},
                "/workspace/p", dictPipelineTasks={},
                dictDurableContext=dictDurable,
            )
        for _ in range(4):
            await asyncio.sleep(0)
    return listDispatched


@pytest.mark.asyncio
@pytest.mark.parametrize("sAction", sorted({
    "runAll", "forceRunAll", "runFrom", "runSelected", "verify",
    "runAllTests",
}))
async def testEveryRunActionLaunchesWhenTheGateIsOpen(sAction):
    """The positive control the falsification test below depends on."""
    listDispatched = await _ftRunOneFrame(_FakeRunSocket([{"sAction": sAction}]))
    assert listDispatched == [sAction]


@pytest.mark.asyncio
@pytest.mark.falsification
async def testARunOverAnAlreadyOpenSocketIsRefusedOnceTheGateCloses():
    """The carrier refuses it, so a socket opened before the Quit is covered.

    Kills: moving the gate check out of the carrier (``mutationAdmission``)
    into an HTTP-only dependency, which a socket's frames never pass.
    """
    socketFake = _GateClosingSocket([{"sAction": "runAll"}])
    listDispatched = await _ftRunOneFrame(socketFake)
    assert listDispatched == []
    [dictRefusal] = socketFake.listSent
    assert dictRefusal["sType"] == "runRefused"
    assert "shutting down" in dictRefusal["sMessage"]
