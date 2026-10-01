"""The host-control operation handlers, driven without a socket.

``tests/testHostControlChannel.py`` proves the wire: a real Unix socket,
the real peer-credential shim, one request per connection. That lane
cannot cheaply stage a held drain lock, a live supervisor, or a
daemon refusal, so the handlers' refusal branches are driven here by
awaiting each handler on a hand-built hub state, exactly as the socket
dispatcher does. The journal, the registry, the lifecycle locks and
the reconciliation transactions are the real ones under temp
directories; only the Docker daemon (a container restart) is patched.

Container names are never container ids here: the owner record's
``sContainerId`` is deliberately different from the name every request
carries.
"""

import asyncio
import os
import struct
import threading
from types import SimpleNamespace

import pytest

from vaibify.config import (
    containerLock,
    operationJournal,
    reconciliation,
    registryManager,
)
from vaibify.gui import (
    browserSession,
    containerOwnership,
    hostControlChannel,
    sessionLifecycle,
)

S_PROJECT = "projectAlpha"
S_CONTAINER_ID = "a1b2c3d4e5f6"


@pytest.fixture(autouse=True)
def fixtureIsolateHostState(tmp_path, monkeypatch):
    """Keep the journal, container locks and registry in tmp_path."""
    monkeypatch.setattr(
        operationJournal, "_S_JOURNAL_DIRECTORY", str(tmp_path / "journal"))
    monkeypatch.setattr(
        containerLock, "_S_LOCK_DIRECTORY", str(tmp_path / "locks"))
    sRegistryDirectory = str(tmp_path / "registry")
    monkeypatch.setattr(
        registryManager, "_S_REGISTRY_DIRECTORY", sRegistryDirectory)
    monkeypatch.setattr(
        registryManager, "_S_REGISTRY_PATH",
        os.path.join(sRegistryDirectory, "registry.json"))
    monkeypatch.setattr(
        registryManager, "_S_LOCK_PATH",
        os.path.join(sRegistryDirectory, "registry.lock"))
    monkeypatch.setattr(
        hostControlChannel, "F_RECONCILE_DRAIN_WAIT_SECONDS", 0.05)
    return tmp_path


def fappBuildHub():
    """Return a hand-built hub app carrying the state the handlers read."""
    return SimpleNamespace(state=SimpleNamespace(
        iHubPort=0,
        listLifespanStartup=[],
        listLifespanShutdown=[],
        dictContainerOwners={},
        dictMutationSupervisors={},
        dictDurableTaskRecords={},
        dictBrowserSessions=browserSession.fdictCreateBrowserSessionStore(),
    ))


def frecordBuildOwner(bHoldsFlock=True, sState=None, iGeneration=1):
    """Return an owner record for S_PROJECT, holding the flock or not."""
    recordOwner = containerOwnership.OwnerRecord(
        sLeaseId="LEASE-ALPHA",
        fileHandleLock=object() if bHoldsFlock else None,
        sContainerId=S_CONTAINER_ID,
        iOwnerGeneration=iGeneration)
    if sState is not None:
        recordOwner.sState = sState
    return recordOwner


def fappBuildHubHolding(**dictOwnerKeywords):
    appHub = fappBuildHub()
    appHub.state.dictContainerOwners[S_PROJECT] = frecordBuildOwner(
        **dictOwnerKeywords)
    return appHub


def fdictAwaitWithDrainHeld(appHub, fnHandler, dictRequest):
    """Await a handler while another holder keeps the drain lock."""
    async def fdictRun():
        lockMutation = sessionLifecycle.flockContainerMutationForAppState(
            appHub.state, S_PROJECT)
        await lockMutation.acquire()
        try:
            return await fnHandler(appHub, {}, dictRequest)
        finally:
            lockMutation.release()
    return asyncio.run(fdictRun())


def fdictAwait(appHub, fnHandler, dictRequest, dictCtx=None):
    return asyncio.run(fnHandler(appHub, dictCtx or {}, dictRequest))


def fsPlantMalformedMarker():
    """Write an unparseable journal marker; return its sha256."""
    sPath = operationJournal.fsJournalPathFor(S_PROJECT)
    os.makedirs(os.path.dirname(sPath), exist_ok=True)
    with open(sPath, "w") as fileJournal:
        fileJournal.write("{ torn marker bytes")
    return operationJournal.fsComputeJournalFileSha256(S_PROJECT)


def fnRegisterHostProject(tmp_path):
    sDirectory = str(tmp_path / S_PROJECT)
    os.makedirs(sDirectory, exist_ok=True)
    with open(os.path.join(sDirectory, "vaibify.yml"), "w") as fileConfig:
        fileConfig.write(f"projectName: {S_PROJECT}\n")
    registryManager.fnAddProject(sDirectory, sMode="host")


# ----- the flock question --------------------------------------------------


def testTheFlockQuestionNeedsARecordThatActuallyHoldsTheFlock():
    appHub = fappBuildHub()
    assert hostControlChannel.fbHubHoldsContainerFlockForName(
        appHub.state, S_PROJECT) is False
    appHub.state.dictContainerOwners[S_PROJECT] = frecordBuildOwner(
        bHoldsFlock=False)
    assert hostControlChannel.fbHubHoldsContainerFlockForName(
        appHub.state, S_PROJECT) is False
    appHub.state.dictContainerOwners[S_PROJECT] = frecordBuildOwner()
    assert hostControlChannel.fbHubHoldsContainerFlockForName(
        appHub.state, S_PROJECT) is True
    assert hostControlChannel.fbHubHoldsContainerFlockForName(
        appHub.state, S_CONTAINER_ID) is False, "keyed by NAME, never id"


# ----- reconcile -------------------------------------------------------------


def testReconcileRefusesWhileAGuardedMutationHoldsTheDrain():
    appHub = fappBuildHubHolding()
    dictResponse = fdictAwaitWithDrainHeld(
        appHub, hostControlChannel._fdictHandleReconcile,
        {"sContainerName": S_PROJECT, "listExpectedOperationIds": ["op-1"]})
    assert dictResponse["bAccepted"] is False
    assert "still holds container 'projectAlpha's drain" in (
        dictResponse["sError"])
    assert "force-abandon it first" in dictResponse["sError"]


def testReconcileReportsACleanupRefusalAndLeavesTheMarker(monkeypatch):
    """A proof that holds but whose cleanup refuses is not a reconcile."""
    listCleanups = []
    monkeypatch.setattr(
        reconciliation, "fdictProveJournalRecordsSettled",
        lambda *arguments: {"bProven": True,
                            "listClearableOperationIds": ["op-1"],
                            "listRecordNotes": []})

    def fnRefuseCleanup(sName, listIds, connectionDocker):
        listCleanups.append((sName, list(listIds)))
        raise reconciliation.ReconciliationRefusedError(
            "the helper container could not be removed")

    monkeypatch.setattr(
        reconciliation, "fnCleanupAndClearProvenRecords", fnRefuseCleanup)
    appHub = fappBuildHubHolding()
    dictResponse = fdictAwait(
        appHub, hostControlChannel._fdictHandleReconcile,
        {"sContainerName": S_PROJECT, "listExpectedOperationIds": ["op-1"]})
    assert dictResponse == {
        "bAccepted": False,
        "sError": "the helper container could not be removed"}
    assert listCleanups == [(S_PROJECT, ["op-1"])]
    lockMutation = sessionLifecycle.flockContainerMutationForAppState(
        appHub.state, S_PROJECT)
    assert not lockMutation.locked(), "the drain must be released"


# ----- force-abandon ---------------------------------------------------------


def testForceAbandonRequiresTheExpectedOperationId():
    appHub = fappBuildHubHolding()
    for dictExtra in ({}, {"sExpectedOperationId": ""},
                      {"sExpectedOperationId": 17}):
        dictResponse = fdictAwait(
            appHub, hostControlChannel._fdictHandleForceAbandon,
            dict({"sContainerName": S_PROJECT}, **dictExtra))
        assert dictResponse["bAccepted"] is False
        assert "requires sExpectedOperationId" in dictResponse["sError"]
    assert appHub.state.dictContainerOwners[S_PROJECT].poison is None


def testForceAbandonRefusesAContainerThisHubHasNoRecordFor():
    dictResponse = fdictAwait(
        fappBuildHub(), hostControlChannel._fdictHandleForceAbandon,
        {"sContainerName": S_PROJECT, "sExpectedOperationId": "op-1"})
    assert dictResponse == {
        "bAccepted": False,
        "sError": "this hub holds no owner record for container "
                  "'projectAlpha'"}


def fsupervisorBuild(sName, sOperationId, sSupervisorId, listTerminated):
    return SimpleNamespace(
        sName=sName, sOperationId=sOperationId,
        sSupervisorId=sSupervisorId, eventCancelRequested=threading.Event(),
        fnTerminateWorker=lambda: listTerminated.append(sSupervisorId),
        taskSupervisor=None)


def testForceAbandonPoisonsAndSignalsOnlyTheMatchedLiveSupervisor():
    listTerminated = []
    appHub = fappBuildHubHolding()
    supervisorMatched = fsupervisorBuild(
        S_PROJECT, "op-live", "supervisor-matched", listTerminated)
    supervisorOtherOperation = fsupervisorBuild(
        S_PROJECT, "op-other", "supervisor-other-operation", listTerminated)
    supervisorOtherContainer = fsupervisorBuild(
        "projectBeta", "op-live", "supervisor-other-container",
        listTerminated)
    supervisorNoHook = fsupervisorBuild(
        S_PROJECT, "op-live", "supervisor-without-hook", listTerminated)
    supervisorNoHook.fnTerminateWorker = None
    appHub.state.dictMutationSupervisors = {
        "key-one": supervisorOtherContainer,
        "key-two": supervisorOtherOperation,
        "key-three": supervisorMatched,
        "key-four": supervisorNoHook,
    }
    dictResponse = fdictAwait(
        appHub, hostControlChannel._fdictHandleForceAbandon,
        {"sContainerName": S_PROJECT, "sExpectedOperationId": "op-live"})
    assert dictResponse["bAccepted"] is True
    assert dictResponse["bPoisoned"] is True
    assert dictResponse["bDurableMirrorWritten"] is False, (
        "no journal record exists to mirror into, and it must say so")
    recordPoison = appHub.state.dictContainerOwners[S_PROJECT].poison
    assert recordPoison.sGuardedOperationId == "op-live"
    assert recordPoison.sContainerId == S_CONTAINER_ID
    assert recordPoison.sTaskHandleId in (
        "supervisor-matched", "supervisor-without-hook")
    assert listTerminated == ["supervisor-matched"]
    assert supervisorMatched.eventCancelRequested.is_set()
    assert supervisorNoHook.eventCancelRequested.is_set()
    assert not supervisorOtherOperation.eventCancelRequested.is_set()
    assert not supervisorOtherContainer.eventCancelRequested.is_set()


def testForceAbandonMatchesALiveDurableTaskById():
    appHub = fappBuildHubHolding()
    appHub.state.dictDurableTaskRecords = {
        S_PROJECT: SimpleNamespace(sTaskId="task-durable-7", taskAsync=None)}
    dictResponse = fdictAwait(
        appHub, hostControlChannel._fdictHandleForceAbandon,
        {"sContainerName": S_PROJECT,
         "sExpectedOperationId": "task-durable-7"})
    assert dictResponse["bAccepted"] is True
    assert appHub.state.dictContainerOwners[S_PROJECT].poison.sTaskHandleId \
        == "task-durable-7"


# ----- break-glass -------------------------------------------------------------


@pytest.mark.parametrize("sOperation", ["break-glass", "abandon-host-journal"])
def testMarkerClearingRequiresTheMarkerHash(sOperation):
    fnHandler = hostControlChannel._DICT_SOCKET_OPERATION_HANDLERS[sOperation]
    for dictExtra in ({}, {"sMarkerSha256": ""}, {"sMarkerSha256": ["f"]}):
        dictResponse = fdictAwait(
            fappBuildHubHolding(), fnHandler,
            dict({"sContainerName": S_PROJECT}, **dictExtra))
        assert dictResponse["bAccepted"] is False
        assert f"{sOperation} requires sMarkerSha256" in (
            dictResponse["sError"])


@pytest.mark.parametrize("sOperation", ["break-glass", "abandon-host-journal"])
def testMarkerClearingRefusesAContainerThisHubDoesNotHold(sOperation):
    fnHandler = hostControlChannel._DICT_SOCKET_OPERATION_HANDLERS[sOperation]
    sSha256 = fsPlantMalformedMarker()
    dictResponse = fdictAwait(
        fappBuildHubHolding(bHoldsFlock=False), fnHandler,
        {"sContainerName": S_PROJECT, "sMarkerSha256": sSha256})
    assert dictResponse["bAccepted"] is False
    assert "this hub does not hold container 'projectAlpha'" in (
        dictResponse["sError"])
    assert os.path.exists(operationJournal.fsJournalPathFor(S_PROJECT))


@pytest.mark.parametrize("sOperation", ["break-glass", "abandon-host-journal"])
def testMarkerClearingRefusesWhileTheDrainIsHeld(sOperation):
    fnHandler = hostControlChannel._DICT_SOCKET_OPERATION_HANDLERS[sOperation]
    sSha256 = fsPlantMalformedMarker()
    dictResponse = fdictAwaitWithDrainHeld(
        fappBuildHubHolding(), fnHandler,
        {"sContainerName": S_PROJECT, "sMarkerSha256": sSha256})
    assert dictResponse == {
        "bAccepted": False,
        "sError": "a guarded mutation still holds container "
                  "'projectAlpha's drain"}
    assert os.path.exists(operationJournal.fsJournalPathFor(S_PROJECT))


# ----- abandon-host-journal ------------------------------------------------------


def testAbandonRefusesAnInvalidContainerName():
    dictResponse = fdictAwait(
        fappBuildHubHolding(),
        hostControlChannel._fdictHandleAbandonHostJournal,
        {"sContainerName": "../escape", "sMarkerSha256": "f" * 64})
    assert dictResponse == {"bAccepted": False,
                            "sError": "a valid sContainerName is required"}


def testAbandonRefusesAContainerizedProjectAndKeepsTheMarker():
    sSha256 = fsPlantMalformedMarker()
    dictResponse = fdictAwait(
        fappBuildHubHolding(),
        hostControlChannel._fdictHandleAbandonHostJournal,
        {"sContainerName": S_PROJECT, "sMarkerSha256": sSha256})
    assert dictResponse["bAccepted"] is False
    assert "is for host projects" in dictResponse["sError"]
    assert "--break-glass" in dictResponse["sError"]
    assert os.path.exists(operationJournal.fsJournalPathFor(S_PROJECT))


def testAbandonClearsAHostProjectsHashMatchedMarkerAndAuditsIt(tmp_path):
    from vaibify.config import abandonmentAudit
    fnRegisterHostProject(tmp_path)
    sSha256 = fsPlantMalformedMarker()
    appHub = fappBuildHubHolding()
    dictStale = fdictAwait(
        appHub, hostControlChannel._fdictHandleAbandonHostJournal,
        {"sContainerName": S_PROJECT, "sMarkerSha256": "0" * 64})
    assert dictStale["bAccepted"] is False
    assert not abandonmentAudit.fbHasRecordedAbandonment(S_PROJECT, sSha256)
    dictResponse = fdictAwait(
        appHub, hostControlChannel._fdictHandleAbandonHostJournal,
        {"sContainerName": S_PROJECT, "sMarkerSha256": sSha256})
    assert dictResponse == {"bAccepted": True, "bCleared": True}
    assert not os.path.exists(operationJournal.fsJournalPathFor(S_PROJECT))
    assert abandonmentAudit.fbHasRecordedAbandonment(S_PROJECT, sSha256)


# ----- mint-transfer -------------------------------------------------------------


def testMintTransferRefusesAHubWithNoSessionStore():
    appHub = fappBuildHubHolding(iGeneration=3)
    del appHub.state.dictBrowserSessions
    dictResponse = fdictAwait(
        appHub, hostControlChannel._fdictHandleMintTransfer,
        {"sContainerName": S_PROJECT, "iExpectedOwnerGeneration": 3})
    assert dictResponse["bAccepted"] is False
    assert "no browser-session store" in dictResponse["sError"]
    assert "sTransferCapability" not in dictResponse


# ----- list-reattachable -----------------------------------------------------------


def testOnlyOrphanedSessionsAreListedAsReattachable():
    appHub = fappBuildHub()
    appHub.state.dictContainerOwners = {
        S_PROJECT: frecordBuildOwner(
            sState=containerOwnership.S_OWNER_STATE_ORPHANED_SESSION,
            iGeneration=4),
        "projectBeta": frecordBuildOwner(),
    }
    dictResponse = fdictAwait(
        appHub, hostControlChannel._fdictHandleListReattachable, {})
    assert dictResponse == {"bAccepted": True, "listReattachable": [
        {"sContainerName": S_PROJECT, "iOwnerGeneration": 4}]}


# ----- repair-container ----------------------------------------------------------------


def fnInstallRepairDaemon(monkeypatch, errorToRaise=None):
    """Patch the ONE daemon call a repair makes; record what it was asked."""
    from vaibify.docker import containerManager
    listRepairs = []

    def fdictRepair(config, sContainerName, sOperation, sImageIdentity=""):
        listRepairs.append((config, sContainerName, sOperation))
        if errorToRaise is not None:
            raise errorToRaise
        return {"sContainerId": S_CONTAINER_ID}

    monkeypatch.setattr(
        containerManager, "fdictRepairContainerLifecycle", fdictRepair)
    return listRepairs


def testRepairRefusesAnInvalidNameOrAnUnknownOperation():
    dictResponse = fdictAwait(
        fappBuildHubHolding(), hostControlChannel._fdictHandleRepairContainer,
        {"sContainerName": "", "sRepairOperation": "restart"})
    assert dictResponse["sError"] == "a valid sContainerName is required"
    for valueOperation in ("", "rebuild", None):
        dictResponse = fdictAwait(
            fappBuildHubHolding(),
            hostControlChannel._fdictHandleRepairContainer,
            {"sContainerName": S_PROJECT, "sRepairOperation": valueOperation})
        assert dictResponse == {
            "bAccepted": False,
            "sError": "sRepairOperation must be 'restart' or 'recreate'"}


def testRepairRefusesAContainerThisHubDoesNotHold(monkeypatch):
    listRepairs = fnInstallRepairDaemon(monkeypatch)
    dictResponse = fdictAwait(
        fappBuildHubHolding(bHoldsFlock=False),
        hostControlChannel._fdictHandleRepairContainer,
        {"sContainerName": S_PROJECT, "sRepairOperation": "restart"})
    assert "does not hold container 'projectAlpha'" in dictResponse["sError"]
    assert listRepairs == []


def testRepairRefusesWhileTheDrainIsHeld(monkeypatch):
    listRepairs = fnInstallRepairDaemon(monkeypatch)
    dictResponse = fdictAwaitWithDrainHeld(
        fappBuildHubHolding(), hostControlChannel._fdictHandleRepairContainer,
        {"sContainerName": S_PROJECT, "sRepairOperation": "restart"})
    assert dictResponse["bAccepted"] is False
    assert "would destroy work in progress" in dictResponse["sError"]
    assert listRepairs == []


def testRepairRefusesOverLiveGuardedWork(monkeypatch):
    listRepairs = fnInstallRepairDaemon(monkeypatch)
    appHub = fappBuildHubHolding()
    appHub.state.dictMutationSupervisors = {"key-one": fsupervisorBuild(
        S_PROJECT, "op-live", "supervisor-live", [])}
    dictResponse = fdictAwait(
        appHub, hostControlChannel._fdictHandleRepairContainer,
        {"sContainerName": S_PROJECT, "sRepairOperation": "restart"})
    assert dictResponse == {
        "bAccepted": False,
        "sError": "guarded work is live in container 'projectAlpha'; a "
                  "repair would destroy it"}
    assert listRepairs == []


def testRepairRestartsAHeldContainerAndReturnsItsAnnouncements(monkeypatch):
    listRepairs = fnInstallRepairDaemon(monkeypatch)
    objectDocker = object()
    dictResponse = fdictAwait(
        fappBuildHubHolding(), hostControlChannel._fdictHandleRepairContainer,
        {"sContainerName": S_PROJECT, "sRepairOperation": "restart"},
        dictCtx={"docker": objectDocker})
    assert dictResponse["bAccepted"] is True
    assert dictResponse["bRepaired"] is True
    assert dictResponse["sOperation"] == "restart"
    assert len(dictResponse["listAnnouncements"]) == 1
    assert dictResponse["listAnnouncements"][0].startswith(
        "Restarting 'projectAlpha' will:")
    assert listRepairs == [(None, S_PROJECT, "restart")]
    assert not os.path.exists(operationJournal.fsJournalPathFor(S_PROJECT)), (
        "the repair's journal record must settle once it returns")


def testARestartTheDaemonRefusesComesBackAsARefusal(monkeypatch):
    fnInstallRepairDaemon(
        monkeypatch, errorToRaise=RuntimeError("the daemon is unreachable"))
    dictResponse = fdictAwait(
        fappBuildHubHolding(), hostControlChannel._fdictHandleRepairContainer,
        {"sContainerName": S_PROJECT, "sRepairOperation": "restart"})
    assert dictResponse == {"bAccepted": False,
                            "sError": "the daemon is unreachable"}


# ----- connection service, with a scripted peer ------------------------------------


class ScriptedPeerSocket:
    """A connected peer's kernel-credential answer, for either platform."""

    def __init__(self, iUid, errorOnRead=None):
        self.iUid = iUid
        self.errorOnRead = errorOnRead
        self.listAsked = []

    def getsockopt(self, iLevel, iOption, iBufferLength):
        self.listAsked.append((iLevel, iOption, iBufferLength))
        if self.errorOnRead is not None:
            raise self.errorOnRead
        if hasattr(__import__("socket"), "SO_PEERCRED"):
            return struct.pack("3i", 4242, self.iUid, 20)
        return (struct.pack("IIh", 0, self.iUid, 1) + b"\x00\x00"
                + struct.pack("I", 20)).ljust(76, b"\x00")


class RecordingWriter:
    """An asyncio stream writer double that records what was sent."""

    def __init__(self, socketPeer, errorOnDrain=None):
        self.socketPeer = socketPeer
        self.errorOnDrain = errorOnDrain
        self.listWritten = []
        self.bClosed = False

    def get_extra_info(self, sName):
        return self.socketPeer if sName == "socket" else None

    def write(self, baData):
        self.listWritten.append(baData)

    async def drain(self):
        if self.errorOnDrain is not None:
            raise self.errorOnDrain

    def close(self):
        self.bClosed = True


class ScriptedReader:
    """An asyncio stream reader double answering one readline."""

    def __init__(self, baLine=b"", errorOnRead=None):
        self.baLine = baLine
        self.errorOnRead = errorOnRead

    async def readline(self):
        if self.errorOnRead is not None:
            raise self.errorOnRead
        return self.baLine


def fnServeOne(readerScripted, writerRecording):
    asyncio.run(hostControlChannel._fnServeHostControlConnection(
        fappBuildHub(), {}, readerScripted, writerRecording))


@pytest.mark.parametrize("socketPeer", [
    None, ScriptedPeerSocket(os.getuid(), errorOnRead=OSError("gone")),
    ScriptedPeerSocket(os.getuid() + 1)])
def testAnUnprovablePeerIsClosedWithoutAByte(socketPeer):
    writerRecording = RecordingWriter(socketPeer)
    fnServeOne(ScriptedReader(b'{"sOperation": "list-reattachable"}\n'),
               writerRecording)
    assert writerRecording.listWritten == []
    assert writerRecording.bClosed is True


def testAPeerThatHangsUpBeforeTheDrainIsClosedQuietly():
    writerRecording = RecordingWriter(
        ScriptedPeerSocket(os.getuid()),
        errorOnDrain=ConnectionResetError("peer hung up"))
    fnServeOne(ScriptedReader(b'{"sOperation": "list-reattachable"}\n'),
               writerRecording)
    assert len(writerRecording.listWritten) == 1
    assert writerRecording.bClosed is True


@pytest.mark.parametrize("errorOnRead", [
    ValueError("line over the limit"),
    asyncio.LimitOverrunError("line over the limit", 65536)])
def testAnOversizedRequestLineIsRefused(errorOnRead):
    writerRecording = RecordingWriter(ScriptedPeerSocket(os.getuid()))
    fnServeOne(ScriptedReader(errorOnRead=errorOnRead), writerRecording)
    assert writerRecording.listWritten == [
        b'{"bAccepted": false, "sError": "the request line was oversized '
        b'or timed out"}\n']
    assert writerRecording.bClosed is True


# ----- the peer-credential dispatch, both platforms ---------------------------


@pytest.mark.parametrize("bLinux", [True, False])
def testPeerCredentialsDispatchOnThePlatformsSocketOption(
        monkeypatch, bLinux):
    import socket
    if bLinux:
        monkeypatch.setattr(socket, "SO_PEERCRED", 17, raising=False)
    else:
        monkeypatch.delattr(socket, "SO_PEERCRED", raising=False)
    socketPeer = ScriptedPeerSocket(4321)
    assert hostControlChannel.ftPeerUidGid(socketPeer) == (4321, 20)
    if bLinux:
        assert socketPeer.listAsked == [
            (socket.SOL_SOCKET, 17, struct.calcsize("3i"))]
    else:
        assert socketPeer.listAsked == [(0, 0x0001, 76)]


# ----- socket-directory hygiene without binding ----------------------------------


def testStaleSweepToleratesAMissingControlDirectory(tmp_path, monkeypatch):
    sMissing = str(tmp_path / "neverCreated")
    monkeypatch.setattr(hostControlChannel, "_S_CONTROL_DIRECTORY", sMissing)
    hostControlChannel.fnUnlinkStaleControlSockets()
    assert not os.path.exists(sMissing)


def testStaleSweepNeverTouchesForeignOrNonSocketEntries(
        tmp_path, monkeypatch):
    import vaibify.config.sessionRegistry as sessionRegistry
    sDirectory = tmp_path / "control"
    sDirectory.mkdir()
    listNames = ["notes.txt", "hub-abc.controlSocket",
                 "hub-9001.controlSocket", "hub-.controlSocket"]
    for sName in listNames:
        (sDirectory / sName).write_text("not a socket")
    monkeypatch.setattr(
        hostControlChannel, "_S_CONTROL_DIRECTORY", str(sDirectory))
    listAskedPorts = []
    monkeypatch.setattr(
        sessionRegistry, "fdictReadHubSlotByPort",
        lambda iPort: listAskedPorts.append(iPort) or {})
    hostControlChannel.fnUnlinkStaleControlSockets()
    assert sorted(os.listdir(sDirectory)) == sorted(listNames)
    assert listAskedPorts == [9001], "foreign names never reach the oracle"


def testShutdownWithNoServerLeavesTheSocketPathAlone(tmp_path, monkeypatch):
    monkeypatch.setattr(
        hostControlChannel, "_S_CONTROL_DIRECTORY", str(tmp_path))
    appHub = fappBuildHub()
    appHub.state.iHubPort = 8123
    hostControlChannel.fnRegisterHostControlChannel(appHub, {})
    sPath = hostControlChannel.fsControlSocketPathForPort(8123)
    with open(sPath, "w") as fileSquatter:
        fileSquatter.write("left by somebody else")
    asyncio.run(appHub.state.listLifespanShutdown[0](appHub))
    assert os.path.exists(sPath)


def testBreakGlassStopsByNameThroughTheProvenSettledStop(monkeypatch):
    from vaibify.docker import containerManager
    listStopped = []
    monkeypatch.setattr(
        containerManager, "fbStopContainerProvenSettled",
        lambda sContainerName: listStopped.append(sContainerName) or False)
    assert hostControlChannel._fbStopContainerByNameProven(S_PROJECT) is (
        False)
    assert listStopped == [S_PROJECT]
