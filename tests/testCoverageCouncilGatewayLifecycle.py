"""The council Docker gateway and runner connections against a fake daemon.

The Docker daemon is the one external boundary here. ``_FakeCouncilDaemon``
stands in for the docker-py client (``containers``, ``networks`` and the
low-level ``api``) and serves every exec over a REAL socket pair, so the
gateway's own stdin streaming, Docker frame demultiplexing, output cap,
deadline and stall logic all run unmodified. The council registry is the
real one, so every reservation settlement asserted below is the registry's
own answer, not a stub's.

Container ids and container names are deliberately different strings
(``ctr...`` versus ``vaibifyCouncil...``), and a handle is neither, so a
name-versus-id or handle-versus-id mixup in the gateway would fail here.
"""

import asyncio
import io
import json
import socket
import tarfile
import threading
import time
import types

import pytest

from vaibify.docker import disposableSpecification
from vaibify.gui import agentCouncilAntigravityProvider
from vaibify.gui import agentCouncilCampaign
from vaibify.gui import agentCouncilCodexProvider
from vaibify.gui import agentCouncilDockerGateway as gateway
from vaibify.gui import agentCouncilEgress
from vaibify.gui import agentCouncilProviders
from vaibify.gui import agentCouncilRegistry
from vaibify.gui import agentCouncilRunner


S_CAMPAIGN_ID = "campaignAlpha"
S_OTHER_CAMPAIGN_ID = "campaignBeta"
S_RESOURCE_NAME = "projectContainerAlpha"
S_IMAGE_REFERENCE = "imageAlpha@sha256:" + "a" * 64
I_COUNCIL_UID = 1000


class _FakeNotFoundError(Exception):
    """The fake daemon's positive 404."""


class _FakeImageNotFoundError(Exception):
    """The fake daemon's missing-image answer."""


MODULE_FAKE_DOCKER = types.SimpleNamespace(
    errors=types.SimpleNamespace(
        NotFound=_FakeNotFoundError,
        ImageNotFound=_FakeImageNotFoundError,
    ),
)


def fbaFrameStdout(baPayload):
    """Wrap bytes in one Docker multiplexed stdout frame."""
    return (b"\x01\x00\x00\x00" + len(baPayload).to_bytes(4, "big")
            + baPayload)


def fdictReadTarMembers(baTar):
    """Return {name: (uid, gid, bytes-or-None)} for every member."""
    dictMembers = {}
    with tarfile.open(fileobj=io.BytesIO(baTar), mode="r:*") as fileTar:
        for infoMember in fileTar:
            baContent = None
            if infoMember.isreg():
                baContent = fileTar.extractfile(infoMember).read()
            dictMembers[infoMember.name] = (
                infoMember.uid, infoMember.gid, baContent)
    return dictMembers


def fbaBuildSnapshotTar(dictFiles):
    """Build a snapshot tarball whose members claim root ownership."""
    bufferTar = io.BytesIO()
    with tarfile.open(fileobj=bufferTar, mode="w") as fileTar:
        for sName, baContent in dictFiles.items():
            infoMember = tarfile.TarInfo(name=sName)
            infoMember.size = len(baContent)
            infoMember.uid = 0
            infoMember.gid = 0
            infoMember.uname = "root"
            fileTar.addfile(infoMember, io.BytesIO(baContent))
    return bufferTar.getvalue()


class _FakeContainer:
    """One container the fake daemon holds."""

    def __init__(self, daemonFake, sContainerId, sName, dictLabels,
                 dictCreateKeywords):
        self._daemonFake = daemonFake
        self.id = sContainerId
        self.name = sName
        self.labels = dictLabels
        self.status = "created"
        self.dictCreateKeywords = dictCreateKeywords
        self.bRunning = False
        self.bStateOomKilled = False
        self.dictNetworks = {}
        self.dictExtractedByDirectory = {}

    def start(self):
        if self._daemonFake.errorOnStart is not None:
            raise self._daemonFake.errorOnStart
        self.bRunning = True
        self.status = "running"
        self._daemonFake.listStartedIds.append(self.id)


class _FakeContainerCollection:
    def __init__(self, daemonFake):
        self._daemonFake = daemonFake

    def create(self, sImage, **dictKeywords):
        return self._daemonFake.fcontainerCreate(sImage, dictKeywords)

    def list(self, **dictKeywords):
        self._daemonFake.listListCalls.append(dictKeywords)
        return list(self._daemonFake.listDiscoverable)


class _FakeNetworkCollection:
    def __init__(self, daemonFake):
        self._daemonFake = daemonFake

    def create(self, sName, **dictKeywords):
        if self._daemonFake.errorOnNetworkCreate is not None:
            raise self._daemonFake.errorOnNetworkCreate
        self._daemonFake.dictNetworksByName[sName] = dictKeywords


class _FakeLowLevelApi:
    """The docker-py ``APIClient`` surface the gateway reaches."""

    def __init__(self, daemonFake):
        self._daemonFake = daemonFake

    def exec_create(self, sContainerId, listCommand, stdin, stdout, stderr,
                    user, workdir):
        return self._daemonFake.fdictCreateExec(
            sContainerId, listCommand, stdin, user, workdir)

    def exec_start(self, sExecId, socket):
        return self._daemonFake.fsocketStartExec(sExecId)

    def exec_inspect(self, sExecId):
        self._daemonFake.listExecInspected.append(sExecId)
        if self._daemonFake.errorOnExecInspect is not None:
            raise self._daemonFake.errorOnExecInspect
        return {"ExitCode": self._daemonFake.dictExecsById[sExecId][
            "iExitCode"]}

    def inspect_container(self, sIdentifier):
        return self._daemonFake.fdictInspectContainer(sIdentifier)

    def kill(self, sContainerId):
        self._daemonFake.fnKillContainer(sContainerId)

    def remove_container(self, sIdentifier, force, v):
        self._daemonFake.fnRemoveContainer(sIdentifier)

    def put_archive(self, sContainerId, sPath, baData):
        if self._daemonFake.errorOnPutArchive is not None:
            raise self._daemonFake.errorOnPutArchive
        self._daemonFake.listPutArchives.append(
            (sContainerId, sPath, baData))

    def logs(self, sContainerId, stdout, stderr):
        return self._daemonFake.fbaNextProxyLog()

    def connect_container_to_network(self, sContainerId, sNetworkName):
        if self._daemonFake.errorOnConnect is not None:
            raise self._daemonFake.errorOnConnect
        self._daemonFake.listConnected.append((sContainerId, sNetworkName))

    def pull(self, sImage):
        self._daemonFake.listPulled.append(sImage)
        if self._daemonFake.errorOnPull is not None:
            raise self._daemonFake.errorOnPull
        self._daemonFake.setLocalImages.add(sImage)

    def remove_network(self, sNetworkName):
        self._daemonFake.fnRemoveNetwork(sNetworkName)

    def inspect_network(self, sNetworkName):
        if sNetworkName in self._daemonFake.setNetworkInspectFaults:
            raise RuntimeError("daemon did not answer the network inspect")
        if sNetworkName not in self._daemonFake.dictNetworksByName:
            raise _FakeNotFoundError(sNetworkName)
        return {"Name": sNetworkName}


class _FakeCouncilDaemon:
    """A scripted Docker daemon; every exec rides a real socket pair."""

    def __init__(self):
        self.containers = _FakeContainerCollection(self)
        self.networks = _FakeNetworkCollection(self)
        self.api = _FakeLowLevelApi(self)
        self.dictContainersById = {}
        self.dictExecsById = {}
        self.dictNetworksByName = {}
        self.listCreateCalls = []
        self.listStartedIds = []
        self.listKilledIds = []
        self.listRemovedIdentifiers = []
        self.listExecInspected = []
        self.listListCalls = []
        self.listDiscoverable = []
        self.listPutArchives = []
        self.listConnected = []
        self.listPulled = []
        self.listRemovedNetworks = []
        self.listThreads = []
        self.listExtractions = []
        self.listOomCounterReadings = []
        self.listProxyLogs = []
        self.setLocalImages = {S_IMAGE_REFERENCE,
                               agentCouncilEgress.S_PROXY_IMAGE}
        self.setInspectFaults = set()
        self.setRemoveFaults = set()
        self.setRemoveIgnored = set()
        self.setNetworkRemoveFaults = set()
        self.setNetworkRemoveIgnored = set()
        self.setNetworkInspectFaults = set()
        self.errorOnCreate = None
        self.errorOnStart = None
        self.errorOnExecCreate = None
        self.errorOnNetworkCreate = None
        self.errorOnPutArchive = None
        self.errorOnConnect = None
        self.errorOnPull = None
        self.iKillsBeforeStopping = 1
        self.iExtractExitCode = 0
        self.sProxyAddress = "10.9.8.7"
        self.errorOnExecInspect = None
        self.eventRelease = threading.Event()
        self.fnHandleTurn = lambda dictExec: (b"", 0)
        self._iCounter = 0

    def fsMintIdentifier(self, sPrefix):
        self._iCounter += 1
        return f"{sPrefix}{self._iCounter:04d}{'f' * 8}"

    def fcontainerCreate(self, sImage, dictKeywords):
        self.listCreateCalls.append((sImage, dictKeywords))
        if self.errorOnCreate is not None:
            raise self.errorOnCreate
        if sImage not in self.setLocalImages:
            raise _FakeImageNotFoundError(sImage)
        containerNew = _FakeContainer(
            self, self.fsMintIdentifier("ctr"), dictKeywords["name"],
            dict(dictKeywords.get("labels") or {}), dictKeywords)
        sNetwork = dictKeywords.get("network") or dictKeywords.get(
            "network_mode")
        containerNew.dictNetworks[sNetwork] = {"IPAddress": self.sProxyAddress}
        self.dictContainersById[containerNew.id] = containerNew
        return containerNew

    def fcontainerFind(self, sIdentifier):
        for containerHeld in self.dictContainersById.values():
            if sIdentifier in (containerHeld.id, containerHeld.name):
                return containerHeld
        return None

    def fdictInspectContainer(self, sIdentifier):
        if sIdentifier in self.setInspectFaults:
            raise RuntimeError("daemon read timed out")
        containerHeld = self.fcontainerFind(sIdentifier)
        if containerHeld is None:
            raise _FakeNotFoundError(sIdentifier)
        return {
            "Config": {"Labels": dict(containerHeld.labels)},
            "State": {"Running": containerHeld.bRunning,
                      "OOMKilled": containerHeld.bStateOomKilled},
            "NetworkSettings": {"Networks": dict(containerHeld.dictNetworks)},
        }

    def fnKillContainer(self, sContainerId):
        self.listKilledIds.append(sContainerId)
        containerHeld = self.fcontainerFind(sContainerId)
        if containerHeld is None:
            raise _FakeNotFoundError(sContainerId)
        if self.listKilledIds.count(sContainerId) >= self.iKillsBeforeStopping:
            containerHeld.bRunning = False

    def fnRemoveContainer(self, sIdentifier):
        self.listRemovedIdentifiers.append(sIdentifier)
        if sIdentifier in self.setRemoveFaults:
            raise RuntimeError("daemon refused the removal")
        containerHeld = self.fcontainerFind(sIdentifier)
        if containerHeld is None:
            raise _FakeNotFoundError(sIdentifier)
        if sIdentifier in self.setRemoveIgnored:
            return
        del self.dictContainersById[containerHeld.id]

    def fnRemoveNetwork(self, sNetworkName):
        self.listRemovedNetworks.append(sNetworkName)
        if sNetworkName in self.setNetworkRemoveFaults:
            raise RuntimeError("network has active endpoints")
        if sNetworkName not in self.dictNetworksByName:
            raise _FakeNotFoundError(sNetworkName)
        if sNetworkName in self.setNetworkRemoveIgnored:
            return
        del self.dictNetworksByName[sNetworkName]

    def fbaNextProxyLog(self):
        jsonNext = (self.listProxyLogs.pop(0) if self.listProxyLogs
                    else b"")
        if isinstance(jsonNext, Exception):
            raise jsonNext
        return jsonNext

    def fdictCreateExec(self, sContainerId, listCommand, bStdin, sUser,
                        sWorkingDirectory):
        if self.errorOnExecCreate is not None:
            raise self.errorOnExecCreate
        sExecId = self.fsMintIdentifier("exec")
        self.dictExecsById[sExecId] = {
            "sExecId": sExecId, "sContainerId": sContainerId,
            "listCommand": list(listCommand), "bStdin": bStdin,
            "sUser": sUser, "sWorkingDirectory": sWorkingDirectory,
            "baStdin": b"", "iExitCode": None,
        }
        return {"Id": sExecId}

    def fsocketStartExec(self, sExecId):
        socketClient, socketDaemon = socket.socketpair()
        threadServe = threading.Thread(
            target=self._fnServeExec,
            args=(self.dictExecsById[sExecId], socketDaemon), daemon=True)
        threadServe.start()
        self.listThreads.append(threadServe)
        return types.SimpleNamespace(_sock=socketClient)

    def _ftAnswerExec(self, dictExec):
        listCommand = dictExec["listCommand"]
        if listCommand == disposableSpecification.LIST_OOM_COUNTER_COMMAND:
            iCount = (self.listOomCounterReadings.pop(0)
                      if self.listOomCounterReadings else 0)
            return (f"low 0\noom_kill {iCount}\n".encode("utf-8"), 0)
        if listCommand[:3] == ["tar", "-xf", "-"]:
            containerHeld = self.fcontainerFind(dictExec["sContainerId"])
            dictMembers = fdictReadTarMembers(dictExec["baStdin"])
            containerHeld.dictExtractedByDirectory[listCommand[-1]] = (
                dictMembers)
            self.listExtractions.append(
                (containerHeld.id, listCommand[-1], dictMembers))
            sMessage = "" if self.iExtractExitCode == 0 else (
                "tar: /council: Cannot open: Read-only file system\n")
            return (sMessage.encode("utf-8"), self.iExtractExitCode)
        return self.fnHandleTurn(dictExec)

    def _fnServeExec(self, dictExec, socketDaemon):
        try:
            if dictExec["bStdin"]:
                baReceived = b""
                while True:
                    baChunk = socketDaemon.recv(65536)
                    if not baChunk:
                        break
                    baReceived += baChunk
                dictExec["baStdin"] = baReceived
            baOutput, iExitCode = self._ftAnswerExec(dictExec)
            dictExec["iExitCode"] = iExitCode
            if baOutput is None:
                self.eventRelease.wait(timeout=5.0)
            else:
                socketDaemon.sendall(fbaFrameStdout(baOutput))
        except OSError:
            pass
        finally:
            socketDaemon.close()

    def flistExecsRunning(self, listCommandPrefix):
        return [dictExec for dictExec in self.dictExecsById.values()
                if dictExec["listCommand"][:len(listCommandPrefix)]
                == listCommandPrefix
                and dictExec["listCommand"]
                != disposableSpecification.LIST_OOM_COUNTER_COMMAND]


@pytest.fixture
def daemonFake(monkeypatch):
    """A fake daemon wired in at the gateway's docker-module seam."""
    daemonCreated = _FakeCouncilDaemon()
    monkeypatch.setattr(gateway, "_fmoduleGetDocker",
                        lambda: MODULE_FAKE_DOCKER)
    monkeypatch.setattr(agentCouncilRunner, "F_STREAM_POLL_SECONDS", 0.02)
    yield daemonCreated
    daemonCreated.eventRelease.set()
    for threadServe in daemonCreated.listThreads:
        threadServe.join(timeout=2.0)


@pytest.fixture
def dictGatewayState(daemonFake):
    """A gateway over the fake daemon and a fresh real registry."""
    return gateway.fdictCreateCouncilDockerGateway(
        daemonFake, agentCouncilRegistry.fdictCreateCouncilRegistry(),
        sResourceName=S_RESOURCE_NAME)


def fdictRunnerCost():
    return {"iMemoryBytes": 1024 * 1024 * 1024, "fCpuCount": 1.0}


def fdictCreateRunner(dictGatewayState, sCampaignId=S_CAMPAIGN_ID,
                      sProvider="claude", **dictKeywords):
    return gateway.fdictReserveAndCreateRunner(
        dictGatewayState, sCampaignId, sProvider, fdictRunnerCost(),
        S_IMAGE_REFERENCE, **dictKeywords)


def fsContainerIdForHandle(dictGatewayState, sHandle):
    return dictGatewayState["dictHandlesById"][sHandle]["sContainerId"]


def fdictReservation(dictGatewayState, sReservationId):
    return dictGatewayState["dictRegistry"]["dictReservationsById"].get(
        sReservationId)


# ----- client construction ------------------------------------------------


def testCouncilClientUsesTheShortDaemonTimeoutByDefault(monkeypatch):
    """The council client bounds every call so teardown can quarantine."""
    listEnsured = []
    listTimeouts = []
    moduleDocker = types.SimpleNamespace(
        from_env=lambda timeout: listTimeouts.append(timeout) or "client")
    monkeypatch.setattr(gateway, "_fnEnsureDockerHost",
                        lambda: listEnsured.append(True))
    monkeypatch.setattr(gateway, "_fmoduleGetDocker", lambda: moduleDocker)
    assert gateway.fdockerCreateCouncilClient() == "client"
    assert gateway.fdockerCreateCouncilClient(iTimeoutSeconds=7) == "client"
    assert listTimeouts == [gateway.I_COUNCIL_DAEMON_TIMEOUT_SECONDS, 7]
    assert listEnsured == [True, True]


# ----- reserve-before-create ---------------------------------------------


def testCreatedRunnerIsLabeledLiveAndAddressedOnlyByAnOpaqueHandle(
        daemonFake, dictGatewayState):
    """The handle is neither the container id nor its name."""
    dictCreated = fdictCreateRunner(dictGatewayState)
    assert dictCreated["bCreated"] is True
    sHandle = dictCreated["sHandle"]
    sContainerId = fsContainerIdForHandle(dictGatewayState, sHandle)
    assert sHandle not in (sContainerId, dictCreated["sContainerName"])
    assert daemonFake.listStartedIds == [sContainerId]
    dictKeywords = daemonFake.listCreateCalls[0][1]
    assert dictKeywords["labels"] == {
        agentCouncilRunner.S_COUNCIL_LABEL: dictCreated["sReservationId"],
        agentCouncilRunner.S_COUNCIL_ROLE_LABEL: "runner",
        agentCouncilRunner.S_COUNCIL_RESOURCE_LABEL: S_RESOURCE_NAME,
    }
    dictReservation = fdictReservation(
        dictGatewayState, dictCreated["sReservationId"])
    assert dictReservation["sStatus"] == agentCouncilRegistry.S_RESERVATION_LIVE
    assert dictReservation["sContainerId"] == sContainerId


def testAdmissionRefusalCreatesNothingAndReturnsTheReason(
        daemonFake, dictGatewayState):
    """A drained registry refuses before any SDK create is attempted."""
    dictGatewayState["dictRegistry"]["bAdmittingNewTurns"] = False
    dictCreated = fdictCreateRunner(dictGatewayState)
    assert dictCreated == {
        "bCreated": False,
        "sRefusalReason":
            "the council registry is draining and admits no new turns",
        "sHandle": "",
    }
    assert daemonFake.listCreateCalls == []
    assert dictGatewayState["dictRegistry"]["dictReservationsById"] == {}


def testCreateFaultDropsThePendingReservationAndPropagates(
        daemonFake, dictGatewayState):
    """No container ever existed, so the reservation is simply freed."""
    daemonFake.errorOnCreate = RuntimeError("image pull denied")
    with pytest.raises(RuntimeError, match="image pull denied"):
        fdictCreateRunner(dictGatewayState)
    assert dictGatewayState["dictRegistry"]["dictReservationsById"] == {}
    assert dictGatewayState["dictHandlesById"] == {}


def testStartFaultDestroysTheCreatedContainerAndFreesItsBudget(
        daemonFake, dictGatewayState):
    """A created-but-unstartable container is removed with proof."""
    daemonFake.errorOnStart = RuntimeError("OCI runtime create failed")
    with pytest.raises(RuntimeError, match="OCI runtime"):
        fdictCreateRunner(dictGatewayState)
    assert len(daemonFake.listRemovedIdentifiers) == 1
    assert daemonFake.dictContainersById == {}
    assert dictGatewayState["dictRegistry"]["dictReservationsById"] == {}


def testStartFaultWithUnprovenRemovalLeavesAVisibleQuarantine(
        daemonFake, dictGatewayState):
    """The reservation keeps its budget and shows on the UI surface."""
    daemonFake.errorOnStart = RuntimeError("OCI runtime create failed")
    daemonFake.setRemoveFaults.add("ctr0001ffffffff")
    with pytest.raises(RuntimeError):
        fdictCreateRunner(dictGatewayState)
    listQuarantined = gateway.flistDescribeQuarantinedReservations(
        dictGatewayState, S_CAMPAIGN_ID)
    assert len(listQuarantined) == 1
    assert listQuarantined[0]["sCampaignId"] == S_CAMPAIGN_ID
    assert listQuarantined[0]["sProvider"] == "claude"
    assert gateway.flistDescribeQuarantinedReservations(
        dictGatewayState, S_OTHER_CAMPAIGN_ID) == []
    dictReservation = fdictReservation(
        dictGatewayState, listQuarantined[0]["sReservationId"])
    assert dictReservation["sContainerId"] == "ctr0001ffffffff"


@pytest.mark.parametrize("sOperation", ["copy", "turn", "destroy"])
def testARawContainerIdentifierIsNeverAcceptedAsAHandle(
        daemonFake, dictGatewayState, sOperation):
    """Even the id of a container the gateway created is refused."""
    dictCreated = fdictCreateRunner(dictGatewayState)
    sContainerId = fsContainerIdForHandle(
        dictGatewayState, dictCreated["sHandle"])
    dictOperations = {
        "copy": lambda: gateway.fnCopySnapshotIntoRunner(
            dictGatewayState, sContainerId, fbaBuildSnapshotTar({})),
        "turn": lambda: gateway.fdictExecuteBoundedTurn(
            dictGatewayState, sContainerId, ["true"]),
        "destroy": lambda: gateway.fdictDestroyAndSettle(
            dictGatewayState, sContainerId),
    }
    with pytest.raises(gateway.CouncilGatewayError, match="unknown council"):
        dictOperations[sOperation]()
    assert daemonFake.dictExecsById == {}
    assert daemonFake.listRemovedIdentifiers == []


# ----- snapshot copy-in ---------------------------------------------------


def testSnapshotIsExtractedInsideTheContainerStampedToTheCouncilUser(
        daemonFake, dictGatewayState):
    """Root-claimed members arrive re-stamped, extracted by the user."""
    dictCreated = fdictCreateRunner(dictGatewayState)
    baSnapshot = fbaBuildSnapshotTar({"src/model.py": b"print(1)\n"})
    gateway.fnCopySnapshotIntoRunner(
        dictGatewayState, dictCreated["sHandle"], baSnapshot)
    sContainerId = fsContainerIdForHandle(
        dictGatewayState, dictCreated["sHandle"])
    dictExtracted = daemonFake.dictContainersById[
        sContainerId].dictExtractedByDirectory
    assert dictExtracted["/council"]["src/model.py"] == (
        I_COUNCIL_UID, I_COUNCIL_UID, b"print(1)\n")
    dictExec = daemonFake.flistExecsRunning(["tar"])[0]
    assert dictExec["sUser"] == agentCouncilRunner.S_COUNCIL_CONTAINER_USER
    assert dictExec["sContainerId"] == sContainerId


def testSnapshotCopyRefusesARelativeDestinationBeforeAnyExec(
        daemonFake, dictGatewayState):
    dictCreated = fdictCreateRunner(dictGatewayState)
    with pytest.raises(ValueError, match="absolute container path"):
        gateway.fnCopySnapshotIntoRunner(
            dictGatewayState, dictCreated["sHandle"],
            fbaBuildSnapshotTar({}), sDestinationDirectory="council")
    assert daemonFake.dictExecsById == {}


def testSnapshotExtractionFailureRaisesWithTheExitCodeAndOutputTail(
        daemonFake, dictGatewayState):
    dictCreated = fdictCreateRunner(dictGatewayState)
    daemonFake.iExtractExitCode = 2
    with pytest.raises(RuntimeError) as excinfo:
        gateway.fnCopySnapshotIntoRunner(
            dictGatewayState, dictCreated["sHandle"],
            fbaBuildSnapshotTar({"a.txt": b"a"}))
    assert "(exit 2)" in str(excinfo.value)
    assert "Read-only file system" in str(excinfo.value)


def testSnapshotCopyThatOutlivesItsBudgetRaises(
        monkeypatch, daemonFake, dictGatewayState):
    """A silent extractor cannot hold the copy past its deadline."""
    monkeypatch.setattr(
        agentCouncilRunner, "F_SNAPSHOT_COPY_BUDGET_SECONDS", 0.15)
    dictCreated = fdictCreateRunner(dictGatewayState)
    daemonFake._ftAnswerExec = lambda dictExec: (None, 0)
    with pytest.raises(RuntimeError, match="exceeded its"):
        gateway.fnCopySnapshotIntoRunner(
            dictGatewayState, dictCreated["sHandle"],
            fbaBuildSnapshotTar({"a.txt": b"a"}))


# ----- bounded turns -----------------------------------------------------


def testBoundedTurnStreamsStdinAndReportsTheRealExitAndOutput(
        daemonFake, dictGatewayState):
    """The daemon's exit code and stdout arrive unaltered."""
    dictCreated = fdictCreateRunner(dictGatewayState)

    def ftAnswerTurn(dictExec):
        return (b"received:" + dictExec["baStdin"], 3)

    daemonFake.fnHandleTurn = ftAnswerTurn
    dictTurn = gateway.fdictExecuteBoundedTurn(
        dictGatewayState, dictCreated["sHandle"], ["cat"],
        sWorkingDirectory="/council", baStdinPayload=b"question text")
    assert dictTurn["iExitCode"] == 3
    assert dictTurn["sOutput"] == "received:question text"
    assert dictTurn["iOutputBytes"] == len("received:question text")
    assert dictTurn["bOomKilled"] is False
    assert dictTurn["bOutputCapExceeded"] is False
    assert dictTurn["bWallClockExceeded"] is False
    assert daemonFake.listKilledIds == []
    dictExec = daemonFake.flistExecsRunning(["cat"])[0]
    assert dictExec["sWorkingDirectory"] == "/council"
    assert dictExec["bStdin"] is True


def testATurnWithoutStdinOpensTheExecWithStdinClosed(
        daemonFake, dictGatewayState):
    dictCreated = fdictCreateRunner(dictGatewayState)
    daemonFake.fnHandleTurn = lambda dictExec: (b"done", 0)
    dictTurn = gateway.fdictExecuteBoundedTurn(
        dictGatewayState, dictCreated["sHandle"], ["true"])
    assert dictTurn["sOutput"] == "done"
    assert daemonFake.flistExecsRunning(["true"])[0]["bStdin"] is False


def testAnUnanswerableExecInspectYieldsNoExitCodeRatherThanAGuess(
        daemonFake, dictGatewayState):
    dictCreated = fdictCreateRunner(dictGatewayState)
    daemonFake.fnHandleTurn = lambda dictExec: (b"partial", 0)
    daemonFake.errorOnExecInspect = RuntimeError("exec inspect timed out")
    dictTurn = gateway.fdictExecuteBoundedTurn(
        dictGatewayState, dictCreated["sHandle"], ["true"])
    assert dictTurn["iExitCode"] is None
    assert dictTurn["sOutput"] == "partial"
    assert daemonFake.listKilledIds == []


def testACgroupCounterRiseDuringTheTurnIsReportedAsAnOomKill(
        daemonFake, dictGatewayState):
    dictCreated = fdictCreateRunner(dictGatewayState)
    daemonFake.listOomCounterReadings = [4, 5]
    daemonFake.fnHandleTurn = lambda dictExec: (b"", 137)
    dictTurn = gateway.fdictExecuteBoundedTurn(
        dictGatewayState, dictCreated["sHandle"], ["true"])
    assert dictTurn["iExitCode"] == 137
    assert dictTurn["bOomKilled"] is True


def testAnUnreadableCounterFallsBackToTheDaemonStateFlag(
        daemonFake, dictGatewayState):
    """No counter exec could run; the pid-1 flag still concludes."""
    dictCreated = fdictCreateRunner(dictGatewayState)
    sContainerId = fsContainerIdForHandle(
        dictGatewayState, dictCreated["sHandle"])
    daemonFake.dictContainersById[sContainerId].bStateOomKilled = True
    daemonFake.errorOnExecCreate = RuntimeError("container not running")
    assert gateway._fiReadOomKillCount(daemonFake, sContainerId) is None
    assert gateway._fbConcludeOomKilledForContainer(
        daemonFake, sContainerId, None) is True


def testAnUnanswerableInspectConcludesNoKillFromTwoFlatCounters(
        daemonFake, dictGatewayState):
    dictCreated = fdictCreateRunner(dictGatewayState)
    sContainerId = fsContainerIdForHandle(
        dictGatewayState, dictCreated["sHandle"])
    daemonFake.listOomCounterReadings = [2]
    daemonFake.setInspectFaults.add(sContainerId)
    assert gateway._fbConcludeOomKilledForContainer(
        daemonFake, sContainerId, 2) is False


def testAnOutputCapBreachKillsTheContainerAndNeverAsksForAnExitCode(
        daemonFake, dictGatewayState):
    dictCreated = fdictCreateRunner(dictGatewayState)
    sContainerId = fsContainerIdForHandle(
        dictGatewayState, dictCreated["sHandle"])
    daemonFake.fnHandleTurn = lambda dictExec: (b"x" * 5000, 0)
    dictTurn = gateway.fdictExecuteBoundedTurn(
        dictGatewayState, dictCreated["sHandle"], ["yes"],
        iOutputByteCap=100)
    assert dictTurn["bOutputCapExceeded"] is True
    assert dictTurn["iExitCode"] is None
    assert dictTurn["iOutputBytes"] == 100
    assert daemonFake.listKilledIds == [sContainerId]
    sTurnExecId = daemonFake.flistExecsRunning(["yes"])[0]["sExecId"]
    assert sTurnExecId not in daemonFake.listExecInspected


def testASilentTurnIsKilledAtItsWallClock(daemonFake, dictGatewayState):
    dictCreated = fdictCreateRunner(dictGatewayState)
    daemonFake.fnHandleTurn = lambda dictExec: (None, 0)
    dictTurn = gateway.fdictExecuteBoundedTurn(
        dictGatewayState, dictCreated["sHandle"], ["sleep", "999"],
        fWallClockSeconds=0.15, fStallSeconds=60.0)
    assert dictTurn["bWallClockExceeded"] is True
    assert dictTurn["bStalled"] is False
    assert dictTurn["iExitCode"] is None
    assert len(daemonFake.listKilledIds) == 1


def testASilentTurnIsKilledAsAStallBeforeItsWallClock(
        daemonFake, dictGatewayState):
    dictCreated = fdictCreateRunner(dictGatewayState)
    daemonFake.fnHandleTurn = lambda dictExec: (None, 0)
    dictTurn = gateway.fdictExecuteBoundedTurn(
        dictGatewayState, dictCreated["sHandle"], ["sleep", "999"],
        fWallClockSeconds=60.0, fStallSeconds=0.1)
    assert dictTurn["bStalled"] is True
    assert dictTurn["bWallClockExceeded"] is False
    assert dictTurn["fStallSeconds"] == 0.1
    assert dictTurn["iExitCode"] is None
    assert len(daemonFake.listKilledIds) == 1


def testKillRepeatsUntilTheDaemonReportsTheContainerStopped(
        monkeypatch, daemonFake, dictGatewayState):
    """A daemon that absorbs the first kill gets a second one."""
    listSleeps = []
    monkeypatch.setattr(gateway.time, "sleep", listSleeps.append)
    dictCreated = fdictCreateRunner(dictGatewayState)
    sContainerId = fsContainerIdForHandle(
        dictGatewayState, dictCreated["sHandle"])
    daemonFake.iKillsBeforeStopping = 2
    gateway._fnKillContainerQuietly(daemonFake, sContainerId)
    assert daemonFake.listKilledIds == [sContainerId, sContainerId]
    assert listSleeps == [0.2, 0.2]


def testKillIsBoundedAgainstAContainerThatNeverStops(
        monkeypatch, daemonFake, dictGatewayState):
    monkeypatch.setattr(gateway.time, "sleep", lambda fSeconds: None)
    dictCreated = fdictCreateRunner(dictGatewayState)
    sContainerId = fsContainerIdForHandle(
        dictGatewayState, dictCreated["sHandle"])
    daemonFake.iKillsBeforeStopping = 99
    gateway._fnKillContainerQuietly(daemonFake, sContainerId)
    assert daemonFake.listKilledIds == [sContainerId] * 3


def testKillStopsAsSoonAsTheContainerIsGone(monkeypatch, daemonFake):
    """A kill that raises and an inspect that 404s end the loop at once."""
    monkeypatch.setattr(gateway.time, "sleep", lambda fSeconds: None)
    gateway._fnKillContainerQuietly(daemonFake, "ctrAlreadyGone")
    assert daemonFake.listKilledIds == ["ctrAlreadyGone"]


# ----- identity-verified destruction --------------------------------------


def testDestroyRemovesTheLabeledContainerFreesBudgetAndRetiresHandle(
        daemonFake, dictGatewayState):
    dictCreated = fdictCreateRunner(dictGatewayState)
    sHandle = dictCreated["sHandle"]
    sContainerId = fsContainerIdForHandle(dictGatewayState, sHandle)
    dictOutcome = gateway.fdictDestroyAndSettle(dictGatewayState, sHandle)
    assert dictOutcome["sOutcome"] == agentCouncilRunner.S_OUTCOME_DESTROYED
    assert daemonFake.listRemovedIdentifiers == [sContainerId]
    assert dictGatewayState["dictRegistry"]["dictReservationsById"] == {}
    with pytest.raises(gateway.CouncilGatewayError):
        gateway.fdictDestroyAndSettle(dictGatewayState, sHandle)


def testDestroyRefusesAContainerWhoseCouncilLabelDoesNotMatch(
        daemonFake, dictGatewayState):
    """An id that now names some other container is never removed."""
    dictCreated = fdictCreateRunner(dictGatewayState)
    sHandle = dictCreated["sHandle"]
    sContainerId = fsContainerIdForHandle(dictGatewayState, sHandle)
    daemonFake.dictContainersById[sContainerId].labels = {
        "vaibify-project": "someProject"}
    with pytest.raises(gateway.CouncilGatewayError, match="refused"):
        gateway.fdictDestroyAndSettle(dictGatewayState, sHandle)
    assert daemonFake.listRemovedIdentifiers == []
    assert sHandle in dictGatewayState["dictHandlesById"]


def testDestroyOfAnAlreadyGoneContainerSettlesDestroyedWithoutRemoval(
        daemonFake, dictGatewayState):
    dictCreated = fdictCreateRunner(dictGatewayState)
    sContainerId = fsContainerIdForHandle(
        dictGatewayState, dictCreated["sHandle"])
    del daemonFake.dictContainersById[sContainerId]
    dictOutcome = gateway.fdictDestroyAndSettle(
        dictGatewayState, dictCreated["sHandle"])
    assert dictOutcome["sOutcome"] == agentCouncilRunner.S_OUTCOME_DESTROYED
    assert "already gone" in dictOutcome["sReason"]
    assert daemonFake.listRemovedIdentifiers == []
    assert dictGatewayState["dictHandlesById"] == {}


def testDestroyWithAnUnansweredInspectQuarantinesAndAttemptsNothing(
        daemonFake, dictGatewayState):
    dictCreated = fdictCreateRunner(dictGatewayState)
    sHandle = dictCreated["sHandle"]
    sContainerId = fsContainerIdForHandle(dictGatewayState, sHandle)
    daemonFake.setInspectFaults.add(sContainerId)
    dictOutcome = gateway.fdictDestroyAndSettle(dictGatewayState, sHandle)
    assert dictOutcome["sOutcome"] == agentCouncilRunner.S_OUTCOME_QUARANTINED
    assert "no removal was attempted" in dictOutcome["sReason"]
    assert "daemon read timed out" in dictOutcome["sReason"]
    assert daemonFake.listRemovedIdentifiers == []
    assert sHandle in dictGatewayState["dictHandlesById"]
    assert fdictReservation(
        dictGatewayState, dictCreated["sReservationId"])["sStatus"] == (
            agentCouncilRegistry.S_RESERVATION_QUARANTINED)


def testARetryAfterQuarantineCanStillDestroyAndFree(
        daemonFake, dictGatewayState):
    """The quarantined handle stays resolvable for a later retry."""
    dictCreated = fdictCreateRunner(dictGatewayState)
    sHandle = dictCreated["sHandle"]
    sContainerId = fsContainerIdForHandle(dictGatewayState, sHandle)
    daemonFake.setRemoveFaults.add(sContainerId)
    dictFirst = gateway.fdictDestroyAndSettle(dictGatewayState, sHandle)
    assert dictFirst["sOutcome"] == agentCouncilRunner.S_OUTCOME_QUARANTINED
    assert "may still be running" in dictFirst["sReason"]
    daemonFake.setRemoveFaults.clear()
    dictSecond = gateway.fdictDestroyAndSettle(dictGatewayState, sHandle)
    assert dictSecond["sOutcome"] == agentCouncilRunner.S_OUTCOME_DESTROYED
    assert dictGatewayState["dictRegistry"]["dictReservationsById"] == {}


def testARemovalTheDaemonAcceptsButDoesNotPerformIsNotCalledDestroyed(
        daemonFake, dictGatewayState):
    dictCreated = fdictCreateRunner(dictGatewayState)
    sContainerId = fsContainerIdForHandle(
        dictGatewayState, dictCreated["sHandle"])
    daemonFake.setRemoveIgnored.add(sContainerId)
    dictOutcome = gateway.fdictDestroyAndSettle(
        dictGatewayState, dictCreated["sHandle"])
    assert dictOutcome["sOutcome"] == agentCouncilRunner.S_OUTCOME_QUARANTINED
    assert "'present'" in dictOutcome["sReason"]
    assert dictOutcome["dictProbe"]["sAnswer"] == (
        agentCouncilRunner.S_ABSENCE_PRESENT)


def testRemovalOfAContainerAlreadyGoneIsProvenAbsent(daemonFake):
    """A NotFound on removal still gets the positive absence probe."""
    dictOutcome = gateway.fdictDestroyRunnerAndProveAbsence(
        daemonFake, "ctrNeverExisted")
    assert dictOutcome["sOutcome"] == agentCouncilRunner.S_OUTCOME_DESTROYED
    assert dictOutcome["dictProbe"]["sAnswer"] == (
        agentCouncilRunner.S_ABSENCE_ABSENT)


def testAbsenceProbeKeepsItsThreeAnswersDistinct(
        daemonFake, dictGatewayState):
    dictCreated = fdictCreateRunner(dictGatewayState)
    sContainerId = fsContainerIdForHandle(
        dictGatewayState, dictCreated["sHandle"])
    dictPresent = gateway.fdictProbeRunnerAbsence(daemonFake, sContainerId)
    assert dictPresent["sAnswer"] == agentCouncilRunner.S_ABSENCE_PRESENT
    assert dictPresent["dictLabels"][agentCouncilRunner.S_COUNCIL_LABEL] == (
        dictCreated["sReservationId"])
    dictAbsent = gateway.fdictProbeRunnerAbsence(daemonFake, "ctrGone")
    assert dictAbsent == {"sAnswer": agentCouncilRunner.S_ABSENCE_ABSENT,
                          "sDetail": "", "dictLabels": {}}
    daemonFake.setInspectFaults.add(sContainerId)
    dictUnknown = gateway.fdictProbeRunnerAbsence(daemonFake, sContainerId)
    assert dictUnknown["sAnswer"] == (
        agentCouncilRunner.S_ABSENCE_INDETERMINATE)
    assert dictUnknown["sDetail"].startswith("RuntimeError:")


def testDiscoveryReportsEveryLabeledSurvivorWithItsReservation(daemonFake):
    daemonFake.listDiscoverable = [
        types.SimpleNamespace(
            id="ctr0100aaaa", name="vaibifyCouncilRunnerOne",
            status="exited", labels={
                agentCouncilRunner.S_COUNCIL_LABEL: "council-x-1",
                agentCouncilRunner.S_COUNCIL_ROLE_LABEL: "runner",
                agentCouncilRunner.S_COUNCIL_RESOURCE_LABEL: "projectOne",
            }),
        types.SimpleNamespace(
            id="ctr0200bbbb", name="vaibifyCouncilUnlabeled",
            status="running", labels=None),
    ]
    listDiscovered = gateway.flistDiscoverLabeledRunners(daemonFake)
    assert daemonFake.listListCalls == [
        {"all": True,
         "filters": {"label": agentCouncilRunner.S_COUNCIL_LABEL}}]
    assert listDiscovered[0] == {
        "sContainerId": "ctr0100aaaa",
        "sContainerName": "vaibifyCouncilRunnerOne",
        "sReservationId": "council-x-1", "sRole": "runner",
        "sResourceName": "projectOne", "sStatus": "exited",
    }
    assert listDiscovered[1]["sReservationId"] == ""
    assert listDiscovered[1]["sResourceName"] == ""


# ----- egress network and proxy ------------------------------------------


def testInternalNetworkIsCreatedInternalAndNamedForTheCampaign(
        daemonFake, dictGatewayState):
    sNetworkName = gateway.fsCreateCampaignInternalNetwork(
        dictGatewayState, S_CAMPAIGN_ID)
    assert sNetworkName == agentCouncilEgress.fsComposeNetworkName(
        S_CAMPAIGN_ID)
    assert daemonFake.dictNetworksByName[sNetworkName] == {
        "driver": "bridge", "internal": True}


def testInternalNetworkCreationFaultIsAnEgressSetupError(
        daemonFake, dictGatewayState):
    daemonFake.errorOnNetworkCreate = RuntimeError("address pool exhausted")
    with pytest.raises(agentCouncilEgress.EgressSetupError,
                       match="address pool exhausted"):
        gateway.fsCreateCampaignInternalNetwork(
            dictGatewayState, S_CAMPAIGN_ID)


@pytest.fixture
def dictProxyTiming(monkeypatch):
    monkeypatch.setattr(
        agentCouncilEgress, "F_PROXY_READY_POLL_SECONDS", 0.005)
    monkeypatch.setattr(
        agentCouncilEgress, "F_PROXY_READY_DEADLINE_SECONDS", 0.1)
    return {}


def fsLaunchProxy(dictGatewayState, saHostnames=("API.Example.org",)):
    return gateway.fsLaunchAllowlistProxy(
        dictGatewayState, S_CAMPAIGN_ID, list(saHostnames))


def testProxyLaunchesHardenedDualHomedAndReturnsItsInternalAddress(
        daemonFake, dictGatewayState, dictProxyTiming):
    daemonFake.listProxyLogs = [
        RuntimeError("logs not yet available"),
        b"starting\n",
        (agentCouncilEgress.S_PROXY_READY_LINE + "\n").encode("utf-8"),
    ]
    sAddress = fsLaunchProxy(dictGatewayState)
    assert sAddress == daemonFake.sProxyAddress
    sImage, dictKeywords = daemonFake.listCreateCalls[0]
    assert sImage == agentCouncilEgress.S_PROXY_IMAGE
    assert dictKeywords["cap_drop"] == ["ALL"]
    assert dictKeywords["user"] == agentCouncilRunner.S_COUNCIL_CONTAINER_USER
    assert dictKeywords["mem_limit"] == gateway.I_PROXY_MEMORY_BYTES
    assert dictKeywords["pids_limit"] == gateway.I_PROXY_PIDS_LIMIT
    assert dictKeywords["network"] == agentCouncilEgress.fsComposeNetworkName(
        S_CAMPAIGN_ID)
    assert dictKeywords["labels"][agentCouncilRunner.S_COUNCIL_LABEL] == (
        f"egress-{S_CAMPAIGN_ID}")
    assert dictKeywords["labels"][
        agentCouncilRunner.S_COUNCIL_RESOURCE_LABEL] == S_RESOURCE_NAME
    assert "api.example.org" in json.dumps(dictKeywords["command"])
    assert "API.Example.org" not in json.dumps(dictKeywords["command"])
    sProxyId = daemonFake.listStartedIds[0]
    assert daemonFake.listPutArchives[0][:2] == (sProxyId, "/")
    assert daemonFake.listConnected == [(sProxyId, "bridge")]
    assert daemonFake.listPulled == []


def testProxyImageIsPulledOnlyOnAProvenMiss(
        daemonFake, dictGatewayState, dictProxyTiming):
    daemonFake.setLocalImages.discard(agentCouncilEgress.S_PROXY_IMAGE)
    daemonFake.listProxyLogs = [
        agentCouncilEgress.S_PROXY_READY_LINE.encode("utf-8")]
    assert fsLaunchProxy(dictGatewayState) == daemonFake.sProxyAddress
    assert daemonFake.listPulled == [agentCouncilEgress.S_PROXY_IMAGE]
    assert len(daemonFake.listCreateCalls) == 2


def testAFailedProxyPullIsReportedAsAProxyCreationFailure(
        daemonFake, dictGatewayState, dictProxyTiming):
    daemonFake.setLocalImages.discard(agentCouncilEgress.S_PROXY_IMAGE)
    daemonFake.errorOnPull = RuntimeError("registry DNS failure")
    with pytest.raises(agentCouncilEgress.EgressSetupError) as excinfo:
        fsLaunchProxy(dictGatewayState)
    assert "failed to create the egress proxy container" in str(
        excinfo.value)
    assert "registry DNS failure" in str(excinfo.value)
    assert daemonFake.listStartedIds == []


def testAnInvalidAllowlistIsRefusedBeforeAnyContainerExists(
        daemonFake, dictGatewayState):
    with pytest.raises(agentCouncilEgress.EgressSetupError):
        fsLaunchProxy(dictGatewayState, saHostnames=("bad host;rm",))
    assert daemonFake.listCreateCalls == []


@pytest.mark.parametrize("sFault, sExpected", [
    ("archive", "failed to copy the proxy script"),
    ("bridge", "failed to attach the egress proxy"),
    ("crash", "crashed on startup"),
    ("silent", "never reported"),
    ("noAddress", "reports no address"),
    ("inspect", "could not read the internal-network address"),
])
def testEveryProxyStartupFaultRemovesTheHalfBuiltProxy(
        daemonFake, dictGatewayState, dictProxyTiming, sFault, sExpected):
    """No failure after the create may leave a dual-homed proxy behind."""
    sReadyLog = agentCouncilEgress.S_PROXY_READY_LINE.encode("utf-8")
    daemonFake.listProxyLogs = [sReadyLog]
    if sFault == "archive":
        daemonFake.errorOnPutArchive = RuntimeError("read-only rootfs")
    elif sFault == "bridge":
        daemonFake.errorOnConnect = RuntimeError("no such network")
    elif sFault == "crash":
        daemonFake.listProxyLogs = [b"Traceback (most recent call last):\n"]
    elif sFault == "silent":
        daemonFake.listProxyLogs = []
    elif sFault == "noAddress":
        daemonFake.sProxyAddress = ""
    elif sFault == "inspect":
        daemonFake.setInspectFaults.add("ctr0001ffffffff")
    with pytest.raises(agentCouncilEgress.EgressSetupError, match=sExpected):
        fsLaunchProxy(dictGatewayState)
    assert "ctr0001ffffffff" in daemonFake.listRemovedIdentifiers


def testCampaignEgressRemovalProvesBothResourcesAbsent(
        daemonFake, dictGatewayState, dictProxyTiming):
    gateway.fsCreateCampaignInternalNetwork(dictGatewayState, S_CAMPAIGN_ID)
    daemonFake.listProxyLogs = [
        agentCouncilEgress.S_PROXY_READY_LINE.encode("utf-8")]
    fsLaunchProxy(dictGatewayState)
    dictRemoved = gateway.fdictRemoveCampaignEgressResources(
        dictGatewayState, S_CAMPAIGN_ID)
    assert dictRemoved == {"bProxyAbsenceProven": True,
                           "bNetworkAbsenceProven": True,
                           "saIndeterminateResources": []}
    sProxyName = agentCouncilEgress.fsComposeProxyContainerName(S_CAMPAIGN_ID)
    assert daemonFake.listRemovedIdentifiers == [sProxyName]


def testUnansweredEgressRemovalIsReportedIndeterminateNeverAbsent(
        daemonFake, dictGatewayState):
    sProxyName = agentCouncilEgress.fsComposeProxyContainerName(S_CAMPAIGN_ID)
    sNetworkName = agentCouncilEgress.fsComposeNetworkName(S_CAMPAIGN_ID)
    daemonFake.setRemoveFaults.add(sProxyName)
    daemonFake.setNetworkRemoveFaults.add(sNetworkName)
    daemonFake.dictNetworksByName[sNetworkName] = {}
    dictRemoved = gateway.fdictRemoveCampaignEgressResources(
        dictGatewayState, S_CAMPAIGN_ID)
    assert dictRemoved["bProxyAbsenceProven"] is False
    assert dictRemoved["bNetworkAbsenceProven"] is False
    assert dictRemoved["saIndeterminateResources"] == [
        sProxyName, sNetworkName]


def testAnInspectFaultAfterRemovalIsIndeterminateAndSurvivorsArePresent(
        daemonFake, dictGatewayState):
    sProxyName = agentCouncilEgress.fsComposeProxyContainerName(S_CAMPAIGN_ID)
    sNetworkName = agentCouncilEgress.fsComposeNetworkName(S_CAMPAIGN_ID)
    daemonFake.setInspectFaults.add(sProxyName)
    daemonFake.setNetworkInspectFaults.add(sNetworkName)
    assert gateway._fsRemoveProxyContainer(daemonFake, sProxyName) == (
        agentCouncilRunner.S_ABSENCE_INDETERMINATE)
    assert gateway._fsRemoveInternalNetwork(daemonFake, sNetworkName) == (
        agentCouncilRunner.S_ABSENCE_INDETERMINATE)
    daemonFake.setNetworkInspectFaults.clear()
    daemonFake.dictNetworksByName[sNetworkName] = {}
    daemonFake.setNetworkRemoveIgnored.add(sNetworkName)
    assert gateway._fsRemoveInternalNetwork(daemonFake, sNetworkName) == (
        agentCouncilRunner.S_ABSENCE_PRESENT)


def testTheProxyThatSurvivesRemovalIsReportedPresent(
        daemonFake, dictGatewayState, dictProxyTiming):
    daemonFake.listProxyLogs = [
        agentCouncilEgress.S_PROXY_READY_LINE.encode("utf-8")]
    fsLaunchProxy(dictGatewayState)
    sProxyName = agentCouncilEgress.fsComposeProxyContainerName(S_CAMPAIGN_ID)
    daemonFake.setRemoveIgnored.add(sProxyName)
    assert gateway._fsRemoveProxyContainer(daemonFake, sProxyName) == (
        agentCouncilRunner.S_ABSENCE_PRESENT)
    dictRemoved = gateway.fdictRemoveCampaignEgressResources(
        dictGatewayState, S_CAMPAIGN_ID)
    assert dictRemoved["bProxyAbsenceProven"] is False


def testLeftoverSweepSkipsUnsafeIdsAndReportsWhatItCouldNotProve(
        daemonFake):
    """A malformed id is never composed into a resource name."""
    sNetworkBeta = agentCouncilEgress.fsComposeNetworkName(
        S_OTHER_CAMPAIGN_ID)
    daemonFake.setNetworkRemoveFaults.add(sNetworkBeta)
    daemonFake.dictNetworksByName[sNetworkBeta] = {}
    dictSwept = gateway.fdictSweepCouncilEgressLeftovers(
        daemonFake, ["bad id;rm -rf", S_CAMPAIGN_ID, S_OTHER_CAMPAIGN_ID])
    assert dictSwept["listRemovedResources"] == [
        agentCouncilEgress.fsComposeProxyContainerName(S_CAMPAIGN_ID),
        agentCouncilEgress.fsComposeNetworkName(S_CAMPAIGN_ID),
        agentCouncilEgress.fsComposeProxyContainerName(S_OTHER_CAMPAIGN_ID),
    ]
    assert dictSwept["listIndeterminateResources"] == [sNetworkBeta]
    assert not any("bad id" in sIdentifier
                   for sIdentifier in daemonFake.listRemovedIdentifiers)


# ----- runner connections over the real gateway ---------------------------


S_PLAN_TEXT = "PLAN BODY --dangerously-skip-permissions"
S_INSTRUCTION_TEXT = "charter: review the plan"
S_REQUESTED_MODEL = "modelAlpha"


def fdictBuildTurnRequest():
    return {
        "sInstructionChannel": S_INSTRUCTION_TEXT,
        "listQuotedMaterial": [{
            "sSourceKind": "researcherPlan",
            "sAuthorIdentity": "researcher",
            "sContent": S_PLAN_TEXT,
        }],
        "dictOutputSchema": {"type": "object", "required": ["sVerdict"]},
    }


def ftupleStagerWritingFile(pathDirectory, baContent, iExpiresAt=0):
    """A credential stager that writes a real file and records it."""
    listStaged = []

    def ftStage():
        pathStaged = pathDirectory / f"staged{len(listStaged)}.json"
        pathStaged.write_bytes(baContent)
        listStaged.append(pathStaged)
        return (str(pathStaged), iExpiresAt)

    return ftStage, listStaged


def fsJsonLines(listEvents):
    return "".join(json.dumps(dictEvent) + "\n" for dictEvent in listEvents)


def fdictExtractedUnder(daemonFake, sDirectory):
    """Every member extracted into any container under one directory."""
    dictMembers = {}
    for tExtraction in daemonFake.listExtractions:
        if tExtraction[1] == sDirectory:
            dictMembers.update(tExtraction[2])
    return dictMembers


def fdictTurnExec(daemonFake, sProgram):
    return daemonFake.flistExecsRunning([sProgram])[0]


async def flistDriveOneTurn(connectionRunner, dictTurnRequest):
    """Run prepare, start, stream, collect and completion in order."""
    dictContext = await connectionRunner.fdictPrepareImmutableContext(
        dictTurnRequest)
    await connectionRunner.fnStartTurn(dictTurnRequest)
    listEvents = [dictEvent async for dictEvent
                  in connectionRunner.fiterStreamNormalizedEvents()]
    dictResult = await connectionRunner.fdictCollectStructuredResult()
    sCompletion = await connectionRunner.fsReportCompletion()
    return [dictContext, listEvents, dictResult, sCompletion]


def fbaSnapshotAlpha():
    return fbaBuildSnapshotTar({"src/stepAlpha.py": b"value = 1\n"})


def testClaudeTurnRunsThroughTheGatewayAndSettlesTerminal(
        daemonFake, dictGatewayState, tmp_path):
    """Snapshot, login, argv/stdin split, result and proven teardown."""
    ftStage, listStaged = ftupleStagerWritingFile(
        tmp_path, b'{"claudeAiOauth": {"accessToken": "tokenAlpha"}}')
    connectionRunner = agentCouncilProviders.ClaudeRunnerConnection(
        dictGatewayState, S_CAMPAIGN_ID, S_IMAGE_REFERENCE,
        fbaSnapshotAlpha(), S_REQUESTED_MODEL,
        ftStageRunnerCredential=ftStage, saCliProgram=["fakeClaude"])
    daemonFake.fnHandleTurn = lambda dictExec: (fsJsonLines([
        {"type": "system", "subtype": "init", "model": "modelAlpha-2026"},
        {"type": "result", "result": '{"sVerdict": "accept"}',
         "usage": {"output_tokens": 7}},
    ]).encode("utf-8"), 0)
    dictContext, listEvents, dictResult, sCompletion = asyncio.run(
        flistDriveOneTurn(connectionRunner, fdictBuildTurnRequest()))
    assert dictContext["sContextIdentity"].startswith(
        f"council-{S_CAMPAIGN_ID}-")
    assert dictResult == {"sVerdict": "accept"}
    assert [dictEvent["type"] for dictEvent in listEvents] == [
        "system", "result"]
    assert connectionRunner.dictModelIdentity["sResolvedModel"] == (
        "modelAlpha-2026")
    assert sCompletion == agentCouncilCampaign.S_COMPLETION_TERMINAL
    assert dictGatewayState["dictRegistry"]["dictReservationsById"] == {}
    assert not listStaged[0].exists()
    dictCredentialTree = fdictExtractedUnder(daemonFake, "/tmp")
    assert dictCredentialTree["vaibifyCouncilClaude/.credentials.json"] == (
        I_COUNCIL_UID, I_COUNCIL_UID,
        b'{"claudeAiOauth": {"accessToken": "tokenAlpha"}}')
    assert fdictExtractedUnder(daemonFake, "/council")[
        "src/stepAlpha.py"][2] == b"value = 1\n"
    dictExec = fdictTurnExec(daemonFake, "fakeClaude")
    assert S_PLAN_TEXT not in " ".join(dictExec["listCommand"])
    assert S_PLAN_TEXT.encode("utf-8") in dictExec["baStdin"]
    assert dictExec["listCommand"][-1] == S_INSTRUCTION_TEXT
    dictKeywords = daemonFake.listCreateCalls[0][1]
    assert dictKeywords["environment"] == {
        agentCouncilProviders.S_CLAUDE_CONFIG_DIRECTORY_ENV:
            agentCouncilProviders.S_RUNNER_CLAUDE_CONFIG_DIRECTORY}
    assert dictKeywords["network_mode"] == "none"


def fconnectionBuildClaude(dictGatewayState, **dictKeywords):
    return agentCouncilProviders.ClaudeRunnerConnection(
        dictGatewayState, S_CAMPAIGN_ID, S_IMAGE_REFERENCE,
        fbaSnapshotAlpha(), S_REQUESTED_MODEL,
        saCliProgram=["fakeClaude"], **dictKeywords)


def testEgressWiringReachesTheRunnerAsProxyEnvironmentAndBlackHoleDns(
        daemonFake, dictGatewayState, tmp_path):
    """The static host-credential lane and egress wiring together."""
    pathLogin = tmp_path / "hostLogin.json"
    pathLogin.write_bytes(b'{"claudeAiOauth": {"accessToken": "t"}}')
    dictEgress = {"sProxyInternalAddress": "10.20.30.40",
                  "sNetworkName": "vaibifyCouncilEgress-campaignAlpha"}
    connectionRunner = fconnectionBuildClaude(
        dictGatewayState, dictEgress=dictEgress,
        sHostCredentialPath=str(pathLogin))
    asyncio.run(connectionRunner.fdictPrepareImmutableContext(
        fdictBuildTurnRequest()))
    dictKeywords = daemonFake.listCreateCalls[0][1]
    assert dictKeywords["network_mode"] == dictEgress["sNetworkName"]
    assert dictKeywords["environment"]["HTTPS_PROXY"] == (
        "http://10.20.30.40:8888")
    assert dictKeywords["environment"][
        agentCouncilProviders.S_CLAUDE_CONFIG_DIRECTORY_ENV] == (
            agentCouncilProviders.S_RUNNER_CLAUDE_CONFIG_DIRECTORY)
    assert dictKeywords["dns"] == [agentCouncilEgress.S_BLACK_HOLE_NAMESERVER]
    assert dictKeywords["dns_opt"] == ["timeout:1", "attempts:1"]
    assert fdictExtractedUnder(daemonFake, "/tmp")[
        "vaibifyCouncilClaude/.credentials.json"][2] == pathLogin.read_bytes()
    assert pathLogin.exists()
    connectionRunner.fnCleanupHostCredential()
    assert not pathLogin.exists()
    assert connectionRunner.sHostCredentialPath == ""


def testNoCredentialLaneDeliversOnlyTheSnapshot(
        daemonFake, dictGatewayState):
    connectionRunner = fconnectionBuildClaude(dictGatewayState)
    asyncio.run(connectionRunner.fdictPrepareImmutableContext(
        fdictBuildTurnRequest()))
    assert [tExtraction[1] for tExtraction in daemonFake.listExtractions] == [
        "/council"]
    assert daemonFake.listCreateCalls[0][1]["environment"] is None


def testRunnerAdmissionRefusalRaisesWithTheRegistryReason(
        daemonFake, dictGatewayState):
    dictGatewayState["dictRegistry"]["bAdmittingNewTurns"] = False
    connectionRunner = fconnectionBuildClaude(dictGatewayState)
    with pytest.raises(gateway.CouncilGatewayError,
                       match="runner admission refused: the council "
                             "registry is draining"):
        asyncio.run(connectionRunner.fdictPrepareImmutableContext(
            fdictBuildTurnRequest()))
    assert daemonFake.listCreateCalls == []


def testAPrepareFaultDestroysTheRunnerBeforeThePropagatedError(
        daemonFake, dictGatewayState):
    daemonFake.iExtractExitCode = 2
    connectionRunner = fconnectionBuildClaude(dictGatewayState)
    with pytest.raises(RuntimeError, match="extraction failed"):
        asyncio.run(connectionRunner.fdictPrepareImmutableContext(
            fdictBuildTurnRequest()))
    assert daemonFake.dictContainersById == {}
    assert dictGatewayState["dictRegistry"]["dictReservationsById"] == {}
    assert connectionRunner._sHandle == ""
    assert connectionRunner._dictDestroyOutcome["sOutcome"] == (
        agentCouncilRunner.S_OUTCOME_DESTROYED)


def testAStartFaultDestroysTheRunnerAndAFailedDestroyNeverMasksIt(
        daemonFake, dictGatewayState):
    """The original turn failure is the exception the engine sees."""
    connectionRunner = fconnectionBuildClaude(dictGatewayState)

    async def fnPrepareThenFailStart():
        await connectionRunner.fdictPrepareImmutableContext(
            fdictBuildTurnRequest())
        sContainerId = next(iter(daemonFake.dictContainersById))
        daemonFake.dictContainersById[sContainerId].labels = {}
        daemonFake.errorOnExecCreate = RuntimeError("exec refused")
        await connectionRunner.fnStartTurn(fdictBuildTurnRequest())

    with pytest.raises(RuntimeError, match="exec refused"):
        asyncio.run(fnPrepareThenFailStart())
    assert connectionRunner._sHandle == ""
    assert len(daemonFake.dictContainersById) == 1
    assert daemonFake.listRemovedIdentifiers == []


def testACollectFaultDestroysTheRunnerBeforePropagating(
        monkeypatch, daemonFake, dictGatewayState):
    daemonFake.fnHandleTurn = lambda dictExec: (b"", 0)
    connectionRunner = fconnectionBuildClaude(dictGatewayState)

    def fdictRaiseResultFault(listEvents, dictExecution=None):
        raise KeyError("result handling fault")

    monkeypatch.setattr(agentCouncilProviders, "fdictExtractStructuredResult",
                        fdictRaiseResultFault)

    async def fnDriveToCollect():
        await connectionRunner.fdictPrepareImmutableContext(
            fdictBuildTurnRequest())
        await connectionRunner.fnStartTurn(fdictBuildTurnRequest())
        await connectionRunner.fdictCollectStructuredResult()

    with pytest.raises(KeyError, match="result handling fault"):
        asyncio.run(fnDriveToCollect())
    assert daemonFake.dictContainersById == {}
    assert dictGatewayState["dictRegistry"]["dictReservationsById"] == {}
    iRemovalsAfterFirstFault = len(daemonFake.listRemovedIdentifiers)
    with pytest.raises(KeyError):
        asyncio.run(connectionRunner.fdictCollectStructuredResult())
    assert len(daemonFake.listRemovedIdentifiers) == iRemovalsAfterFirstFault


def testAnUnprovenTeardownReportsTheTurnIndeterminate(
        daemonFake, dictGatewayState):
    daemonFake.fnHandleTurn = lambda dictExec: (b"", 0)
    connectionRunner = fconnectionBuildClaude(dictGatewayState)

    async def fsDriveAndComplete():
        await connectionRunner.fdictPrepareImmutableContext(
            fdictBuildTurnRequest())
        await connectionRunner.fnStartTurn(fdictBuildTurnRequest())
        daemonFake.setRemoveFaults.update(daemonFake.dictContainersById)
        return await connectionRunner.fsReportCompletion()

    assert asyncio.run(fsDriveAndComplete()) == (
        agentCouncilCampaign.S_COMPLETION_INDETERMINATE)
    listReservations = list(
        dictGatewayState["dictRegistry"]["dictReservationsById"].values())
    assert [dictReservation["sStatus"] for dictReservation
            in listReservations] == [
                agentCouncilRegistry.S_RESERVATION_QUARANTINED]


def testACompletionWithNoRunnerEverBuiltIsIndeterminate(dictGatewayState):
    connectionRunner = fconnectionBuildClaude(dictGatewayState)
    assert asyncio.run(connectionRunner.fsReportCompletion()) == (
        agentCouncilCampaign.S_COMPLETION_INDETERMINATE)


def testATurnCutShortByTheLoginNamesTheLoginRatherThanTheBudget(
        daemonFake, dictGatewayState, tmp_path):
    """End to end: clamp, real deadline kill, and the recorded cause."""
    iExpiresAt = int((time.time() + 0.3) * 1000)
    ftStage, listStaged = ftupleStagerWritingFile(
        tmp_path, b"{}", iExpiresAt=iExpiresAt)
    connectionRunner = fconnectionBuildClaude(
        dictGatewayState, ftStageRunnerCredential=ftStage,
        fWallClockSeconds=3600.0)
    daemonFake.fnHandleTurn = lambda dictExec: (None, 0)
    listOutcome = asyncio.run(flistDriveOneTurn(
        connectionRunner, fdictBuildTurnRequest()))
    dictResult = listOutcome[2]
    assert dictResult["bWallClockExceeded"] is True
    assert dictResult["sEmptyResultReason"] == (
        agentCouncilProviders.S_EMPTY_BECAUSE_LOGIN_EXPIRY)
    assert dictResult["fElapsedSeconds"] < 5.0
    assert listOutcome[3] == agentCouncilCampaign.S_COMPLETION_TERMINAL


# ----- the baseline-evidence sandbox -------------------------------------


def ffnBuildBaseline(dictGatewayState, **dictKeywords):
    return agentCouncilProviders.ffnBuildBaselineEvidenceExecutor(
        dictGatewayState, S_CAMPAIGN_ID, S_IMAGE_REFERENCE, "snapshotHashA",
        fbaSnapshotAlpha(), **dictKeywords)


def testBaselineEvidenceRunsInAnIsolatedSandboxAndDigestsItsOutput(
        daemonFake, dictGatewayState):
    import hashlib
    daemonFake.fnHandleTurn = lambda dictExec: (b"3 passed\n", 0)
    fdictExecute = ffnBuildBaseline(dictGatewayState)
    dictEvidence = fdictExecute({"sCommandText": "pytest -q"})
    assert dictEvidence == {
        "sSnapshotHash": "snapshotHashA",
        "sExecutionImageIdentity": S_IMAGE_REFERENCE,
        "iExitCode": 0,
        "sOutputDigest": hashlib.sha256(b"3 passed\n").hexdigest(),
    }
    dictKeywords = daemonFake.listCreateCalls[0][1]
    assert dictKeywords["network_mode"] == "none"
    assert dictKeywords["environment"] is None
    assert dictKeywords["labels"][agentCouncilRunner.S_COUNCIL_ROLE_LABEL] == (
        "sandbox")
    dictExec = fdictTurnExec(daemonFake, "/bin/sh")
    assert dictExec["listCommand"] == ["/bin/sh", "-c", "pytest -q"]
    assert dictExec["sWorkingDirectory"] == "/council"
    assert daemonFake.dictContainersById == {}
    assert dictGatewayState["dictRegistry"]["dictReservationsById"] == {}


def testBaselineSandboxAdmissionRefusalIsAGatewayError(
        daemonFake, dictGatewayState):
    dictGatewayState["dictRegistry"]["bAdmittingNewTurns"] = False
    with pytest.raises(gateway.CouncilGatewayError,
                       match="baseline sandbox admission refused"):
        ffnBuildBaseline(dictGatewayState)({"sCommandText": "true"})


def testBaselineEvidenceOverAnUnprovenSandboxIsNeverReturned(
        daemonFake, dictGatewayState):
    """A sandbox that may still exist cannot back a confirmed claim."""
    def ftAnswerAndBreakRemoval(dictExec):
        daemonFake.setRemoveFaults.add(dictExec["sContainerId"])
        return (b"ok", 0)

    daemonFake.fnHandleTurn = ftAnswerAndBreakRemoval
    with pytest.raises(gateway.CouncilGatewayError,
                       match="destruction is unproven"):
        ffnBuildBaseline(dictGatewayState)({"sCommandText": "true"})
    listReservations = list(
        dictGatewayState["dictRegistry"]["dictReservationsById"].values())
    assert listReservations[0]["sProvider"] == "baselineSandbox"
    assert listReservations[0]["sStatus"] == (
        agentCouncilRegistry.S_RESERVATION_QUARANTINED)


def testABaselineCopyFaultPropagatesAndStillDestroysTheSandbox(
        daemonFake, dictGatewayState):
    daemonFake.iExtractExitCode = 1
    with pytest.raises(RuntimeError, match="extraction failed"):
        ffnBuildBaseline(dictGatewayState)({"sCommandText": "true"})
    assert daemonFake.dictContainersById == {}
    assert dictGatewayState["dictRegistry"]["dictReservationsById"] == {}


# ----- Codex and Antigravity connections ----------------------------------


def fconnectionBuildCodex(dictGatewayState, **dictKeywords):
    return agentCouncilCodexProvider.CodexRunnerConnection(
        dictGatewayState, S_CAMPAIGN_ID, S_IMAGE_REFERENCE,
        fbaSnapshotAlpha(), S_REQUESTED_MODEL,
        saCliProgram=["fakeCodex"], **dictKeywords)


def testCodexTurnDeliversItsConfigTreeAndNormalizesItsEvents(
        daemonFake, dictGatewayState, tmp_path):
    ftStage, listStaged = ftupleStagerWritingFile(
        tmp_path, b'{"auth_mode": "chatgpt"}')
    connectionRunner = fconnectionBuildCodex(
        dictGatewayState, ftStageRunnerCredential=ftStage)
    daemonFake.fnHandleTurn = lambda dictExec: (fsJsonLines([
        {"type": "thread.started"},
        {"type": "item.completed", "item": {
            "type": "agent_message", "text": '{"sVerdict": "revise"}'}},
        {"type": "turn.completed", "usage": {"input_tokens": 11}},
    ]).encode("utf-8"), 0)
    dictTurnRequest = fdictBuildTurnRequest()
    listOutcome = asyncio.run(flistDriveOneTurn(
        connectionRunner, dictTurnRequest))
    assert listOutcome[2] == {"sVerdict": "revise"}
    assert listOutcome[3] == agentCouncilCampaign.S_COMPLETION_TERMINAL
    assert connectionRunner.dictModelIdentity["sResolvedModel"] == (
        S_REQUESTED_MODEL)
    assert connectionRunner.dictModelIdentity["dictUsage"] == {
        "input_tokens": 11}
    dictTree = fdictExtractedUnder(daemonFake, "/tmp")
    assert dictTree["vaibifyCouncilCodex/.codex/auth.json"][2] == (
        b'{"auth_mode": "chatgpt"}')
    assert json.loads(dictTree["vaibifyCouncilCodex/turn-schema.json"][2]) == (
        dictTurnRequest["dictOutputSchema"])
    assert not listStaged[0].exists()
    dictEnvironment = daemonFake.listCreateCalls[0][1]["environment"]
    assert dictEnvironment == {
        "HOME": agentCouncilCodexProvider.S_RUNNER_CODEX_HOME,
        "CODEX_HOME": agentCouncilCodexProvider.S_RUNNER_CODEX_CONFIG_DIRECTORY,
    }
    dictExec = fdictTurnExec(daemonFake, "fakeCodex")
    assert S_PLAN_TEXT not in " ".join(dictExec["listCommand"])
    assert S_PLAN_TEXT.encode("utf-8") in dictExec["baStdin"]


def testACodexTurnThatFailsLeavesTheResolvedModelEmpty(
        daemonFake, dictGatewayState):
    connectionRunner = fconnectionBuildCodex(dictGatewayState)
    daemonFake.fnHandleTurn = lambda dictExec: (fsJsonLines([
        {"type": "turn.failed", "message": "usage limit reached"},
    ]).encode("utf-8"), 1)
    listOutcome = asyncio.run(flistDriveOneTurn(
        connectionRunner, fdictBuildTurnRequest()))
    assert connectionRunner.dictModelIdentity["sResolvedModel"] == ""
    assert listOutcome[1][-1]["is_error"] is True
    assert listOutcome[2]["sEmptyResultReason"] == (
        agentCouncilProviders.S_FAILURE_RATE_LIMIT)
    assert [tExtraction[1] for tExtraction in daemonFake.listExtractions] == [
        "/council"]


def testACodexStartFaultDestroysTheRunner(daemonFake, dictGatewayState):
    connectionRunner = fconnectionBuildCodex(dictGatewayState)

    async def fnPrepareThenFailStart():
        await connectionRunner.fdictPrepareImmutableContext(
            fdictBuildTurnRequest())
        daemonFake.errorOnExecCreate = RuntimeError("exec refused")
        await connectionRunner.fnStartTurn(fdictBuildTurnRequest())

    with pytest.raises(RuntimeError, match="exec refused"):
        asyncio.run(fnPrepareThenFailStart())
    assert daemonFake.dictContainersById == {}
    assert dictGatewayState["dictRegistry"]["dictReservationsById"] == {}


def fconnectionBuildAntigravity(dictGatewayState, **dictKeywords):
    return agentCouncilAntigravityProvider.AntigravityRunnerConnection(
        dictGatewayState, S_CAMPAIGN_ID, S_IMAGE_REFERENCE,
        fbaSnapshotAlpha(), S_REQUESTED_MODEL,
        saCliProgram=["fakeAgy"], **dictKeywords)


def flistAntigravityStream(sAgentName):
    return [
        {"event": "init", "init": {"model": "modelAlpha-resolved",
                                   "agent": sAgentName}},
        {"event": "step_update", "step_update": {
            "step_type": "agent_response", "text_delta": "thinking"}},
        {"event": "result", "result": {
            "status": "SUCCESS", "usage": {"tokens": 5},
            "structured_output": {"sVerdict": "accept"}}},
    ]


def testAntigravityTurnDeliversCharterAsAgentAndReadsTheInitModel(
        daemonFake, dictGatewayState, tmp_path):
    ftStage, listStaged = ftupleStagerWritingFile(
        tmp_path, b'{"token": {"access_token": "t"}}')
    connectionRunner = fconnectionBuildAntigravity(
        dictGatewayState, ftStageRunnerCredential=ftStage)
    daemonFake.fnHandleTurn = lambda dictExec: (fsJsonLines(
        flistAntigravityStream("vaibify-council")).encode("utf-8"), 0)
    listOutcome = asyncio.run(flistDriveOneTurn(
        connectionRunner, fdictBuildTurnRequest()))
    assert listOutcome[2] == {"sVerdict": "accept"}
    assert listOutcome[3] == agentCouncilCampaign.S_COMPLETION_TERMINAL
    assert [dictEvent["type"] for dictEvent in listOutcome[1]] == [
        "system", "assistant", "result"]
    assert connectionRunner.dictModelIdentity["sResolvedModel"] == (
        "modelAlpha-resolved")
    dictTree = fdictExtractedUnder(daemonFake, "/tmp")
    sAgentDocument = dictTree[
        "vaibifyCouncilAntigravity/.gemini/config/agents/"
        "vaibify-council/agent.md"][2].decode("utf-8")
    assert S_INSTRUCTION_TEXT in sAgentDocument
    assert sAgentDocument.startswith("---\nname: vaibify-council\n")
    dictSettings = json.loads(dictTree[
        "vaibifyCouncilAntigravity/.gemini/antigravity-cli/"
        "settings.json"][2])
    assert "read_url(*)" in dictSettings["permissions"]["deny"]
    assert not listStaged[0].exists()
    dictExec = fdictTurnExec(daemonFake, "fakeAgy")
    assert S_INSTRUCTION_TEXT not in dictExec["listCommand"]
    assert S_PLAN_TEXT not in " ".join(dictExec["listCommand"])
    dictStdinEvent = json.loads(dictExec["baStdin"].decode("utf-8"))
    assert S_PLAN_TEXT in dictStdinEvent["message"]["content"]
    assert daemonFake.listCreateCalls[0][1]["environment"] == {
        "HOME": agentCouncilAntigravityProvider.S_RUNNER_ANTIGRAVITY_HOME}


def testAnAntigravityInitFromAnotherAgentIsNotTakenAsTheResolvedModel(
        daemonFake, dictGatewayState):
    connectionRunner = fconnectionBuildAntigravity(dictGatewayState)
    daemonFake.fnHandleTurn = lambda dictExec: (fsJsonLines(
        flistAntigravityStream("someOtherAgent")).encode("utf-8"), 0)
    asyncio.run(flistDriveOneTurn(connectionRunner, fdictBuildTurnRequest()))
    assert connectionRunner.dictModelIdentity["sResolvedModel"] == ""


def testAntigravityStartAndCollectFaultsDestroyTheRunner(
        monkeypatch, daemonFake, dictGatewayState):
    connectionRunner = fconnectionBuildAntigravity(dictGatewayState)

    async def fnPrepareThenFailStart():
        await connectionRunner.fdictPrepareImmutableContext(
            fdictBuildTurnRequest())
        daemonFake.errorOnExecCreate = RuntimeError("exec refused")
        await connectionRunner.fnStartTurn(fdictBuildTurnRequest())

    with pytest.raises(RuntimeError, match="exec refused"):
        asyncio.run(fnPrepareThenFailStart())
    assert daemonFake.dictContainersById == {}
    daemonFake.errorOnExecCreate = None

    def fdictRaiseResultFault(listNativeEvents, dictExecution=None):
        raise ValueError("result handling fault")

    monkeypatch.setattr(
        agentCouncilAntigravityProvider,
        "fdictExtractAntigravityStructuredResult", fdictRaiseResultFault)
    daemonFake.fnHandleTurn = lambda dictExec: (b"", 0)

    async def fnDriveToCollect():
        await connectionRunner.fdictPrepareImmutableContext(
            fdictBuildTurnRequest())
        await connectionRunner.fnStartTurn(fdictBuildTurnRequest())
        await connectionRunner.fdictCollectStructuredResult()

    with pytest.raises(ValueError, match="result handling fault"):
        asyncio.run(fnDriveToCollect())
    assert daemonFake.dictContainersById == {}
    assert dictGatewayState["dictRegistry"]["dictReservationsById"] == {}


# ----- Codex and Antigravity carry the real reason a turn ended -------------
#
# Both adapters append a result event of their own after the stream ends,
# even when the stream just stopped, so a turn the gateway destroyed at a
# bound used to be collected as an empty or failed ANSWER and the engine
# filed it as a schema failure instead of naming the bound that fired.


LIST_PROVIDER_BUILDERS = [
    ("codex", fconnectionBuildCodex),
    ("antigravity", fconnectionBuildAntigravity),
]


def fdictCollectOneTurn(daemonFake, dictGatewayState, fconnectionBuild,
                        fnHandleTurn, **dictKeywords):
    """Drive one turn of one provider and return its collected result."""
    connectionRunner = fconnectionBuild(dictGatewayState, **dictKeywords)
    daemonFake.fnHandleTurn = fnHandleTurn
    return asyncio.run(flistDriveOneTurn(
        connectionRunner, fdictBuildTurnRequest()))[2]


def testATurnKilledAtItsWallClockSaysWhichClockRanOut(
        daemonFake, dictGatewayState):
    for sProvider, fconnectionBuild in LIST_PROVIDER_BUILDERS:
        dictResult = fdictCollectOneTurn(
            daemonFake, dictGatewayState, fconnectionBuild,
            lambda dictExec: (None, 0), fWallClockSeconds=0.15)
        assert dictResult["sEmptyResultReason"] == (
            agentCouncilProviders.S_EMPTY_BECAUSE_WALL_CLOCK), sProvider
        assert dictResult["bWallClockExceeded"] is True, sProvider


def testATurnKilledAsAStallSaysItProducedNothing(
        daemonFake, dictGatewayState):
    for sProvider, fconnectionBuild in LIST_PROVIDER_BUILDERS:
        dictResult = fdictCollectOneTurn(
            daemonFake, dictGatewayState, fconnectionBuild,
            lambda dictExec: (None, 0), fWallClockSeconds=60.0,
            fStallSeconds=0.1)
        assert dictResult["sEmptyResultReason"] == (
            agentCouncilProviders.S_EMPTY_BECAUSE_STALL), sProvider
        assert dictResult["bStalled"] is True, sProvider


def testATurnKilledAtItsOutputCapSaysSo(daemonFake, dictGatewayState):
    for sProvider, fconnectionBuild in LIST_PROVIDER_BUILDERS:
        dictResult = fdictCollectOneTurn(
            daemonFake, dictGatewayState, fconnectionBuild,
            lambda dictExec: (b"x" * 5000, 0), iOutputByteCap=100)
        assert dictResult["sEmptyResultReason"] == (
            agentCouncilProviders.S_EMPTY_BECAUSE_OUTPUT_CAP), sProvider
        assert dictResult["bOutputCapExceeded"] is True, sProvider


@pytest.mark.falsification
def testATurnTheKernelStoppedForMemorySaysSo(daemonFake, dictGatewayState):
    """Kills: letting the adapter's own failure result hide a gateway kill.

    The kill exits 137, which the Codex normalizer turns into a CLI
    failure of its own; the bound that fired is the cause.
    """
    for sProvider, fconnectionBuild in LIST_PROVIDER_BUILDERS:
        daemonFake.listOomCounterReadings = [4, 5]
        dictResult = fdictCollectOneTurn(
            daemonFake, dictGatewayState, fconnectionBuild,
            lambda dictExec: (b"", 137))
        assert dictResult["sEmptyResultReason"] == (
            agentCouncilProviders.S_EMPTY_BECAUSE_OUT_OF_MEMORY), sProvider
        assert dictResult["bOomKilled"] is True, sProvider


def fdictCollectATurnCutShortByTheLogin(daemonFake, dictGatewayState,
                                        tmp_path, fconnectionBuild):
    iExpiresAt = int((time.time() + 0.3) * 1000)
    ftStage, _ = ftupleStagerWritingFile(
        tmp_path, b'{"token": {"access_token": "t"}}', iExpiresAt=iExpiresAt)
    return fdictCollectOneTurn(
        daemonFake, dictGatewayState, fconnectionBuild,
        lambda dictExec: (None, 0), ftStageRunnerCredential=ftStage,
        fWallClockSeconds=3600.0)


@pytest.mark.falsification
def testACodexTurnCutShortByTheLoginNamesTheLogin(
        daemonFake, dictGatewayState, tmp_path):
    """Kills: Codex never recording that the login shortened its budget."""
    dictResult = fdictCollectATurnCutShortByTheLogin(
        daemonFake, dictGatewayState, tmp_path, fconnectionBuildCodex)
    assert dictResult["sEmptyResultReason"] == (
        agentCouncilProviders.S_EMPTY_BECAUSE_LOGIN_EXPIRY)


@pytest.mark.falsification
def testAnAntigravityTurnCutShortByTheLoginNamesTheLogin(
        daemonFake, dictGatewayState, tmp_path):
    """Kills: Antigravity never recording that the login shortened its budget."""
    dictResult = fdictCollectATurnCutShortByTheLogin(
        daemonFake, dictGatewayState, tmp_path, fconnectionBuildAntigravity)
    assert dictResult["sEmptyResultReason"] == (
        agentCouncilProviders.S_EMPTY_BECAUSE_LOGIN_EXPIRY)


@pytest.mark.falsification
def testANonzeroExitCarriesItsExitCodeAndAcquitsEveryBound(
        daemonFake, dictGatewayState):
    """Kills: reporting a CLI failure without the facts of how it ended.

    Codex's own normalizer reads the exit code and reports the CLI's
    failure; Antigravity's stream says nothing about it, so its turn is
    the empty stream it was. Both now carry the exit code beside the
    reason, which is what the explanation of a kill reads.
    """
    dictReasonByProvider = {
        "codex": agentCouncilProviders.S_FAILURE_CLI_ERROR_RESULT,
        "antigravity": "noResultEvent",
    }
    for sProvider, fconnectionBuild in LIST_PROVIDER_BUILDERS:
        dictResult = fdictCollectOneTurn(
            daemonFake, dictGatewayState, fconnectionBuild,
            lambda dictExec: (b"", 3))
        assert dictResult["sEmptyResultReason"] == (
            dictReasonByProvider[sProvider]), sProvider
        assert dictResult["jsonExitCode"] == 3, sProvider
        assert [
            dictResult[sFlag] for sFlag in (
                "bWallClockExceeded", "bOutputCapExceeded", "bOomKilled",
                "bStalled")
        ] == [False] * 4, sProvider


@pytest.mark.falsification
def testAGenuinelyEmptyCompletionIsNamedNotFiledAsMissingFields(
        daemonFake, dictGatewayState):
    """Kills: returning a bare empty answer for a turn that said nothing."""
    dictStreamByProvider = {
        "codex": fsJsonLines([{"type": "thread.started"}]),
        "antigravity": fsJsonLines([
            {"event": "init", "init": {"model": "m", "agent": "a"}},
            {"event": "result", "result": {"status": "SUCCESS"}}]),
    }
    for sProvider, fconnectionBuild in LIST_PROVIDER_BUILDERS:
        baStream = dictStreamByProvider[sProvider].encode("utf-8")
        dictResult = fdictCollectOneTurn(
            daemonFake, dictGatewayState, fconnectionBuild,
            lambda dictExec: (baStream, 0))
        assert dictResult["sEmptyResultReason"] == (
            "resultEventCarriedNoText"), sProvider
        assert dictResult["jsonExitCode"] == 0, sProvider


def testAnAnswerThatArrivesIsNeverReplacedByTheBoundDiagnosis(
        daemonFake, dictGatewayState):
    """The reason only describes a turn that produced no answer."""
    baStream = fsJsonLines([
        {"type": "item.completed", "item": {
            "type": "agent_message", "text": '{"sVerdict": "revise"}'}},
        {"type": "turn.completed", "usage": {}},
    ]).encode("utf-8")
    dictResult = fdictCollectOneTurn(
        daemonFake, dictGatewayState, fconnectionBuildCodex,
        lambda dictExec: (baStream, 0))
    assert dictResult == {"sVerdict": "revise"}
