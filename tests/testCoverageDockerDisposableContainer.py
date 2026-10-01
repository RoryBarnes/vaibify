"""Branch coverage for the disposable-container SDK authority.

``disposableContainer`` is the one module that reaches a Docker daemon
for disposable work. Its guarantees are refusals and settlements: an
identifier nobody minted is refused, a creation that raises is settled
(dropped or quarantined, never forgotten), destruction is identity
verified and settles only on proven absence, a bounded command that
breaches its bound is killed and never handed a fabricated exit code,
and a read-only-root container receives its archive through an exec
rather than the archive endpoint. The daemon is the boundary faked
here: a scripted client whose ``api`` answers are recorded, so every
assertion checks what the module ASKED the daemon to do. Container ids
and container names are kept distinct throughout so an id-versus-name
mixup cannot pass.
"""

import io
import socket
import tarfile
import types

import docker.errors
import pytest

from vaibify.docker import disposableContainer, disposableSpecification


S_CONTAINER_ID = "c0ffee00c0ffee00c0ffee00c0ffee00c0ffee00c0ffee00"
S_RESOURCE_NAME = "projectContainerName"


def fbaBuildFrame(baPayload, iStreamType=1):
    """Return one Docker multiplexed-stream frame carrying ``baPayload``."""
    return (
        bytes([iStreamType, 0, 0, 0])
        + len(baPayload).to_bytes(4, "big")
        + baPayload
    )


class _FakeClock:
    """A monotonic clock that only moves when a test moves it."""

    def __init__(self):
        self.fNow = 0.0
        self.listSleeps = []

    def monotonic(self):
        return self.fNow

    def sleep(self, fSeconds):
        self.listSleeps.append(fSeconds)


class _ScriptedSocket:
    """A raw exec socket replaying scripted reads and recording writes."""

    def __init__(self, listRecvResults=None, fnOnTimeout=None):
        self.listRecvResults = list(listRecvResults or [])
        self.fnOnTimeout = fnOnTimeout
        self.baSent = b""
        self.listShutdowns = []
        self.fTimeout = None
        self.bClosed = False

    def settimeout(self, fSeconds):
        self.fTimeout = fSeconds

    def recv(self, iByteCount):
        del iByteCount
        if not self.listRecvResults:
            return b""
        resultNext = self.listRecvResults.pop(0)
        if isinstance(resultNext, BaseException):
            if self.fnOnTimeout is not None:
                self.fnOnTimeout()
            raise resultNext
        return resultNext

    def send(self, baChunk):
        self.baSent += bytes(baChunk)
        return len(baChunk)

    def shutdown(self, iHow):
        self.listShutdowns.append(iHow)

    def close(self):
        self.bClosed = True


class _SocketWrapper:
    """What docker-py's ``exec_start(socket=True)`` hands back."""

    def __init__(self, socketRaw):
        self._sock = socketRaw


def fsocketOomCounter(iCount):
    """Return a socket answering the cgroup counter read with ``iCount``."""
    return _ScriptedSocket(
        [fbaBuildFrame(f"low 0\noom_kill {iCount}\n".encode())])


class _FakeApi:
    """The low-level ``client.api`` half of a scripted daemon."""

    def __init__(self):
        self.listSockets = []
        self.listExecCreated = []
        self.dictExitCodes = {}
        self.listExecInspected = []
        self.listKilled = []
        self.listRemoved = []
        self.listPutArchives = []
        self.listInspectAnswers = []
        self.exceptionRemove = None
        self.exceptionKill = None
        self.exceptionExecInspect = None
        self.bRefuseOomCounterExec = False

    def exec_create(self, sContainerId, listCommand, stdin, stdout, stderr,
                    user, workdir):
        if self.bRefuseOomCounterExec and listCommand == (
                disposableSpecification.LIST_OOM_COUNTER_COMMAND):
            raise docker.errors.APIError("container is not running")
        sExecId = f"exec{len(self.listExecCreated)}"
        self.listExecCreated.append({
            "sExecId": sExecId, "sContainerId": sContainerId,
            "listCommand": listCommand, "bStdin": stdin, "bStdout": stdout,
            "bStderr": stderr, "sUser": user, "sWorkdir": workdir,
        })
        return {"Id": sExecId}

    def exec_start(self, sExecId, socket):  # noqa: A002 -- SDK's name
        assert socket is True
        del sExecId
        return _SocketWrapper(self.listSockets.pop(0))

    def exec_inspect(self, sExecId):
        self.listExecInspected.append(sExecId)
        if self.exceptionExecInspect is not None:
            raise self.exceptionExecInspect
        return {"ExitCode": self.dictExitCodes.get(sExecId, 0)}

    def inspect_container(self, sContainerId):
        del sContainerId
        if not self.listInspectAnswers:
            raise docker.errors.NotFound("no such container")
        resultNext = self.listInspectAnswers.pop(0)
        if isinstance(resultNext, BaseException):
            raise resultNext
        return resultNext

    def kill(self, sContainerId):
        self.listKilled.append(sContainerId)
        if self.exceptionKill is not None:
            raise self.exceptionKill

    def remove_container(self, sContainerId, force, v):
        self.listRemoved.append((sContainerId, force, v))
        if self.exceptionRemove is not None:
            raise self.exceptionRemove

    def put_archive(self, sContainerId, sDestination, baArchive):
        self.listPutArchives.append((sContainerId, sDestination, baArchive))


class _FakeCreatedContainer:
    """One container the scripted daemon created."""

    def __init__(self, sId, exceptionStart=None):
        self.id = sId
        self.exceptionStart = exceptionStart
        self.bStarted = False

    def start(self):
        if self.exceptionStart is not None:
            raise self.exceptionStart
        self.bStarted = True


class _FakeContainerCollection:
    """The ``client.containers`` half of a scripted daemon."""

    def __init__(self):
        self.listCreateCalls = []
        self.exceptionCreate = None
        self.exceptionStart = None
        self.listListed = []

    def create(self, sImageReference, **dictKeywords):
        self.listCreateCalls.append((sImageReference, dictKeywords))
        if self.exceptionCreate is not None:
            raise self.exceptionCreate
        return _FakeCreatedContainer(S_CONTAINER_ID, self.exceptionStart)

    def list(self, all=False, filters=None):  # noqa: A002 -- SDK's name
        assert all is True
        sLabel = (filters or {}).get("label")
        return [
            containerFound for containerFound in self.listListed
            if not sLabel or sLabel in (containerFound.labels or {})
        ]


class _FakeImage:
    """An SDK image object: an id, attributes, and a tag recorder."""

    def __init__(self, sId, dictAttributes=None, exceptionTag=None):
        self.id = sId
        self.attrs = dictAttributes
        self.exceptionTag = exceptionTag
        self.listTags = []

    def tag(self, sRepository, tag):
        if self.exceptionTag is not None:
            raise self.exceptionTag
        self.listTags.append((sRepository, tag))


class _FakeImageCollection:
    """The ``client.images`` half of a scripted daemon."""

    def __init__(self):
        self.dictImages = {}
        self.listPulls = []
        self.exceptionPull = None
        self.listLoadAnswer = []

    def pull(self, sReference, platform):
        self.listPulls.append((sReference, platform))
        if self.exceptionPull is not None:
            raise self.exceptionPull
        return _FakeImage("sha256:pulledImageId")

    def load(self, fileStream):
        fileStream.read()
        return self.listLoadAnswer

    def get(self, sReference):
        if sReference not in self.dictImages:
            raise docker.errors.ImageNotFound(f"no such image {sReference}")
        return self.dictImages[sReference]


class _FakeDockerClient:
    """A scripted daemon: containers, images, api and version."""

    def __init__(self):
        self.api = _FakeApi()
        self.containers = _FakeContainerCollection()
        self.images = _FakeImageCollection()
        self.resultVersion = {"Arch": "arm64"}

    def version(self):
        if isinstance(self.resultVersion, BaseException):
            raise self.resultVersion
        return self.resultVersion


@pytest.fixture
def clockFake(monkeypatch):
    """Freeze both modules' clocks so deadlines and sleeps are scripted."""
    clockScripted = _FakeClock()
    monkeypatch.setattr(disposableContainer, "time", clockScripted)
    monkeypatch.setattr(disposableSpecification, "time", clockScripted)
    return clockScripted


def ftBuildLiveGateway(dockerClient, bReadOnly=False):
    """Create a gateway holding one live container; return gateway and handle."""
    dictGateway = disposableContainer.fdictCreateDisposableGateway(
        dockerClient, sResourceName=S_RESOURCE_NAME)
    dictCreated = disposableContainer.fdictReserveAndCreateContainer(
        dictGateway, "shadow", "imageAlpha:latest",
        bReadOnlyRootFilesystem=bReadOnly)
    return dictGateway, dictCreated


def fdictLabelsForReservation(sReservationId):
    """Return the inspect answer of a container carrying this reservation."""
    return {"Config": {"Labels": {
        disposableSpecification.S_DISPOSABLE_LABEL: sReservationId}},
        "State": {"Running": True, "OOMKilled": False}}


def fbaBuildArchive(sName, baContent):
    """Return a one-file root-owned tarball."""
    bufferArchive = io.BytesIO()
    with tarfile.open(fileobj=bufferArchive, mode="w") as fileTar:
        infoMember = tarfile.TarInfo(sName)
        infoMember.size = len(baContent)
        fileTar.addfile(infoMember, io.BytesIO(baContent))
    return bufferArchive.getvalue()


# ----- the handle boundary ---------------------------------------------


def testRawContainerIdIsRefusedAsAHandle():
    """A caller holding a real container id cannot drive an exec at it."""
    dockerClient = _FakeDockerClient()
    dictGateway, _ = ftBuildLiveGateway(dockerClient)
    with pytest.raises(disposableContainer.DisposableContainerError,
                       match="unknown disposable gateway handle"):
        disposableContainer.fdictExecuteBoundedCommand(
            dictGateway, S_CONTAINER_ID, ["true"])
    assert dockerClient.api.listExecCreated == []


def testHandleFromAnotherGatewayIsRefused():
    """A handle is only valid in the gateway that minted it."""
    dockerClient = _FakeDockerClient()
    _, dictCreated = ftBuildLiveGateway(dockerClient)
    dictOtherGateway = disposableContainer.fdictCreateDisposableGateway(
        dockerClient)
    with pytest.raises(disposableContainer.DisposableContainerError):
        disposableContainer.fdictDestroyAndSettle(
            dictOtherGateway, dictCreated["sHandle"])
    assert dockerClient.api.listRemoved == []


# ----- the image store ---------------------------------------------------


def testPullRequestsThePlatformAndReportsThePulledId():
    """The platform is always requested; the daemon's id is the answer."""
    dockerClient = _FakeDockerClient()
    dictPulled = disposableContainer.fdictPullImage(
        dockerClient, "registryAlpha/imageAlpha:1.0", "linux/amd64")
    assert dictPulled == {"bPulled": True, "sDetail": "sha256:pulledImageId"}
    assert dockerClient.images.listPulls == [
        ("registryAlpha/imageAlpha:1.0", "linux/amd64")]


def testPullFailureIsReportedNotRaised():
    """A failed link in the acquisition chain is an answer naming the error."""
    dockerClient = _FakeDockerClient()
    dockerClient.images.exceptionPull = docker.errors.APIError(
        "manifest unknown")
    dictPulled = disposableContainer.fdictPullImage(
        dockerClient, "registryAlpha/imageAlpha:1.0", "linux/amd64")
    assert dictPulled["bPulled"] is False
    assert dictPulled["sDetail"].startswith("APIError: ")
    assert "manifest unknown" in dictPulled["sDetail"]


def testLoadReturnsTheFirstImageThatHasAnId():
    """Id-less objects in the daemon's answer are skipped, not returned."""
    dockerClient = _FakeDockerClient()
    dockerClient.images.listLoadAnswer = [
        types.SimpleNamespace(), _FakeImage(""), _FakeImage("sha256:loaded")]
    sLoaded = disposableContainer.fsLoadImageFromStream(
        dockerClient, io.BytesIO(b"tarballBytes"))
    assert sLoaded == "sha256:loaded"


@pytest.mark.parametrize("listAnswer", [[], None, [_FakeImage("")]])
def testLoadWithNoImageReportedIsRefused(listAnswer):
    """An empty load answer would otherwise run as an empty reference."""
    dockerClient = _FakeDockerClient()
    dockerClient.images.listLoadAnswer = listAnswer
    with pytest.raises(disposableContainer.DisposableContainerError,
                       match="reported no image"):
        disposableContainer.fsLoadImageFromStream(
            dockerClient, io.BytesIO(b"tarballBytes"))


def testInspectOfAnAbsentImageAnswersNone():
    """Absence is the answer, never a platform claim."""
    assert disposableContainer.fdictInspectImage(
        _FakeDockerClient(), "imageAbsent:latest") is None


def testInspectReportsPlatformAndStringifiedLabels():
    """The image's own labels are how an overlay proves itself."""
    dockerClient = _FakeDockerClient()
    dockerClient.images.dictImages["imageAlpha:latest"] = _FakeImage(
        "sha256:imageAlphaId", {"Os": "linux", "Architecture": "amd64",
                                "Config": {"Labels": {"overlayCount": 2}}})
    assert disposableContainer.fdictInspectImage(
        dockerClient, "imageAlpha:latest") == {
        "sId": "sha256:imageAlphaId", "sOs": "linux",
        "sArchitecture": "amd64", "dictLabels": {"overlayCount": "2"}}


def testInspectOfAnImageWithoutAttributesAnswersEmptyFields():
    """Missing attributes read as empty, never as a guessed platform."""
    dockerClient = _FakeDockerClient()
    dockerClient.images.dictImages["imageBare"] = _FakeImage(
        "sha256:bareId", None)
    assert disposableContainer.fdictInspectImage(
        dockerClient, "imageBare") == {
        "sId": "sha256:bareId", "sOs": "", "sArchitecture": "",
        "dictLabels": {}}


def testTagIsAppliedToTheImageNamedById():
    """The tag lands on the image the id names."""
    dockerClient = _FakeDockerClient()
    imageHeld = _FakeImage("sha256:imageAlphaId")
    dockerClient.images.dictImages["sha256:imageAlphaId"] = imageHeld
    disposableContainer.fnTagImage(
        dockerClient, "sha256:imageAlphaId", "projectAlpha", "latest")
    assert imageHeld.listTags == [("projectAlpha", "latest")]


@pytest.mark.parametrize("bImageHeld", [False, True])
def testTagFailureIsOneNamedRefusal(bImageHeld):
    """An absent id or a refused tag both raise, naming the target name."""
    dockerClient = _FakeDockerClient()
    if bImageHeld:
        dockerClient.images.dictImages["sha256:imageAlphaId"] = _FakeImage(
            "sha256:imageAlphaId",
            exceptionTag=docker.errors.APIError("tag refused"))
    with pytest.raises(disposableContainer.DisposableContainerError,
                       match="could not be tagged as projectAlpha:latest"):
        disposableContainer.fnTagImage(
            dockerClient, "sha256:imageAlphaId", "projectAlpha", "latest")


@pytest.mark.parametrize("resultVersion, sExpected", [
    ({"Arch": "arm64"}, "arm64"),
    ({}, ""),
    (None, ""),
    (docker.errors.APIError("daemon gone"), ""),
])
def testDaemonArchitectureIsTheDaemonsAnswerOrEmpty(resultVersion, sExpected):
    """An unanswerable version call reads as unknown, not as a guess."""
    dockerClient = _FakeDockerClient()
    dockerClient.resultVersion = resultVersion
    assert disposableContainer.fsReadDaemonArchitecture(
        dockerClient) == sExpected


# ----- reserve-before-create and settle-on-every-exit ------------------


def testSuccessfulCreationGoesLiveWithTheGatewayResourceStamp():
    """The handle maps to the container id; the stamp comes from the gateway."""
    dockerClient = _FakeDockerClient()
    dictGateway, dictCreated = ftBuildLiveGateway(dockerClient)
    dictHandle = dictGateway["dictHandlesById"][dictCreated["sHandle"]]
    assert dictHandle["sContainerId"] == S_CONTAINER_ID
    assert dictHandle["sContainerName"] == dictCreated["sContainerName"]
    assert dictHandle["sContainerName"] != S_CONTAINER_ID
    dictReservation = dictGateway["dictReservationsById"][
        dictCreated["sReservationId"]]
    assert dictReservation["sStatus"] == disposableContainer.S_RESERVATION_LIVE
    dictKeywords = dockerClient.containers.listCreateCalls[0][1]
    assert dictKeywords["labels"][
        disposableSpecification.S_DISPOSABLE_RESOURCE_LABEL] == S_RESOURCE_NAME
    assert dictKeywords["labels"][
        disposableSpecification.S_DISPOSABLE_LABEL] == (
        dictCreated["sReservationId"])


def testCreateThatRaisesDropsThePendingReservation():
    """With no container there is nothing to prove gone, so nothing is kept."""
    dockerClient = _FakeDockerClient()
    dockerClient.containers.exceptionCreate = docker.errors.ImageNotFound(
        "no such image")
    dictGateway = disposableContainer.fdictCreateDisposableGateway(
        dockerClient)
    with pytest.raises(docker.errors.ImageNotFound):
        disposableContainer.fdictReserveAndCreateContainer(
            dictGateway, "shadow", "imageAbsent:latest")
    assert dictGateway["dictReservationsById"] == {}
    assert dictGateway["dictHandlesById"] == {}
    assert dockerClient.api.listRemoved == []


def testUnstartableContainerIsDestroyedWithProof():
    """A created-but-unstartable container is removed and proven absent."""
    dockerClient = _FakeDockerClient()
    dockerClient.containers.exceptionStart = docker.errors.APIError(
        "start failed")
    dictGateway = disposableContainer.fdictCreateDisposableGateway(
        dockerClient)
    with pytest.raises(docker.errors.APIError, match="start failed"):
        disposableContainer.fdictReserveAndCreateContainer(
            dictGateway, "shadow", "imageAlpha:latest")
    assert dockerClient.api.listRemoved == [(S_CONTAINER_ID, True, True)]
    assert dictGateway["dictReservationsById"] == {}
    assert disposableContainer.flistDescribeQuarantinedReservations(
        dictGateway) == []


def testUnstartableContainerThatWillNotDieStaysQuarantined():
    """An unproven removal leaves the reservation visible with its reason."""
    dockerClient = _FakeDockerClient()
    dockerClient.containers.exceptionStart = docker.errors.APIError(
        "start failed")
    dockerClient.api.exceptionRemove = docker.errors.APIError(
        "removal in progress")
    dictGateway = disposableContainer.fdictCreateDisposableGateway(
        dockerClient)
    with pytest.raises(docker.errors.APIError, match="start failed"):
        disposableContainer.fdictReserveAndCreateContainer(
            dictGateway, "council", "imageAlpha:latest")
    listQuarantined = disposableContainer.flistDescribeQuarantinedReservations(
        dictGateway)
    assert len(listQuarantined) == 1
    assert listQuarantined[0]["sRole"] == "council"
    assert listQuarantined[0]["sContainerId"] == S_CONTAINER_ID
    assert "removal in progress" in listQuarantined[0]["sReason"]
    assert dictGateway["dictHandlesById"] == {}


def testSettleNamingAStaleEpochLeavesTheSuccessorAlone():
    """A late settle from a previous epoch cannot erase the live record."""
    dictGateway = disposableContainer.fdictCreateDisposableGateway(None)
    iEpoch = disposableContainer._fiReserve(
        dictGateway, "reservationAlpha", "shadow")
    disposableContainer._fnSettleReservation(
        dictGateway, "reservationAlpha",
        disposableSpecification.S_OUTCOME_DESTROYED, iEpoch + 1)
    assert dictGateway["dictReservationsById"]["reservationAlpha"][
        "sStatus"] == disposableContainer.S_RESERVATION_PENDING
    disposableContainer._fnSettleReservation(
        dictGateway, "reservationUnknown",
        disposableSpecification.S_OUTCOME_DESTROYED, iEpoch)
    assert list(dictGateway["dictReservationsById"]) == ["reservationAlpha"]


# ----- archive copy-in ---------------------------------------------------


def testRelativeArchiveDestinationIsRefused():
    """The destination must be an absolute container path."""
    dockerClient = _FakeDockerClient()
    dictGateway, dictCreated = ftBuildLiveGateway(dockerClient)
    with pytest.raises(ValueError, match="absolute container path"):
        disposableContainer.fnCopyArchiveIntoContainer(
            dictGateway, dictCreated["sHandle"],
            fbaBuildArchive("dataFile.csv", b"x"), "relativeDirectory")
    assert dockerClient.api.listPutArchives == []


def testWritableContainerReceivesAStampedArchiveThroughTheEndpoint():
    """The archive endpoint gets the container id and a uid-1000 tarball."""
    dockerClient = _FakeDockerClient()
    dictGateway, dictCreated = ftBuildLiveGateway(dockerClient)
    disposableContainer.fnCopyArchiveIntoContainer(
        dictGateway, dictCreated["sHandle"],
        fbaBuildArchive("dataFile.csv", b"1,2\n"), "/workspace",
        sPathPrefix="repoAlpha")
    assert dockerClient.api.listExecCreated == []
    sContainerId, sDestination, baArchive = (
        dockerClient.api.listPutArchives[0])
    assert (sContainerId, sDestination) == (S_CONTAINER_ID, "/workspace")
    with tarfile.open(fileobj=io.BytesIO(baArchive)) as fileTar:
        listMembers = fileTar.getmembers()
    assert [infoMember.name for infoMember in listMembers] == [
        "repoAlpha", "repoAlpha/dataFile.csv"]
    assert {infoMember.uid for infoMember in listMembers} == {1000}


def testReadOnlyContainerReceivesTheArchiveThroughAnExec(clockFake):
    """A read-only root is served by an unprivileged in-container untar."""
    dockerClient = _FakeDockerClient()
    dictGateway, dictCreated = ftBuildLiveGateway(
        dockerClient, bReadOnly=True)
    socketExec = _ScriptedSocket()
    dockerClient.api.listSockets.append(socketExec)
    baArchive = fbaBuildArchive("dataFile.csv", b"1,2\n")
    disposableContainer.fnCopyArchiveIntoContainer(
        dictGateway, dictCreated["sHandle"], baArchive, "/tmp")
    assert dockerClient.api.listPutArchives == []
    dictExec = dockerClient.api.listExecCreated[0]
    assert dictExec["listCommand"] == ["tar", "-xf", "-", "-C", "/tmp"]
    assert dictExec["sContainerId"] == S_CONTAINER_ID
    assert dictExec["bStdin"] is True
    assert dictExec["sUser"] == (
        disposableSpecification.S_DISPOSABLE_CONTAINER_USER)
    assert socketExec.listShutdowns == [socket.SHUT_WR]
    assert socketExec.bClosed is True
    with tarfile.open(fileobj=io.BytesIO(socketExec.baSent)) as fileTar:
        assert [infoMember.uid for infoMember in fileTar.getmembers()] == [
            1000]


def testReadOnlyCopyReportsAFailedExtractionWithItsOutput(clockFake):
    """A non-zero untar exit raises with the tail of what tar printed."""
    dockerClient = _FakeDockerClient()
    dictGateway, dictCreated = ftBuildLiveGateway(
        dockerClient, bReadOnly=True)
    dockerClient.api.listSockets.append(_ScriptedSocket(
        [fbaBuildFrame(b"tar: Cannot open: Permission denied", 2)]))
    dockerClient.api.dictExitCodes["exec0"] = 2
    with pytest.raises(RuntimeError, match=r"exit 2\): tar: Cannot open"):
        disposableContainer.fnCopyArchiveIntoContainer(
            dictGateway, dictCreated["sHandle"],
            fbaBuildArchive("dataFile.csv", b"x"), "/tmp")


def testReadOnlyCopyThatOutlivesItsBudgetRaises(clockFake):
    """A stalled untar ends at the budget instead of hanging the caller."""
    dockerClient = _FakeDockerClient()
    dictGateway, dictCreated = ftBuildLiveGateway(
        dockerClient, bReadOnly=True)

    def fnAdvancePastBudget():
        clockFake.fNow += (
            disposableContainer.F_ARCHIVE_COPY_BUDGET_SECONDS + 1.0)

    dockerClient.api.listSockets.append(_ScriptedSocket(
        [socket.timeout()], fnOnTimeout=fnAdvancePastBudget))
    with pytest.raises(RuntimeError, match="exceeded its 600s budget"):
        disposableContainer.fnCopyArchiveIntoContainer(
            dictGateway, dictCreated["sHandle"],
            fbaBuildArchive("dataFile.csv", b"x"), "/tmp")
    assert dockerClient.api.listExecInspected == []


# ----- bounded command execution ---------------------------------------


def fnQueueCommandSockets(dockerClient, socketCommand, iBefore=0, iAfter=0):
    """Queue the before-counter, command and after-counter exec sockets."""
    dockerClient.api.listSockets.extend([
        fsocketOomCounter(iBefore), socketCommand, fsocketOomCounter(iAfter)])


def testBoundedCommandReportsExitOutputAndRunsAsTheContainerUser(clockFake):
    """A clean command returns the daemon's exit code and its output."""
    dockerClient = _FakeDockerClient()
    dictGateway, dictCreated = ftBuildLiveGateway(dockerClient)
    fnQueueCommandSockets(dockerClient, _ScriptedSocket(
        [fbaBuildFrame(b"stepAlpha done\n")]))
    dockerClient.api.listInspectAnswers.append(
        {"State": {"OOMKilled": False}})
    dictOutcome = disposableContainer.fdictExecuteBoundedCommand(
        dictGateway, dictCreated["sHandle"], ["python", "stepAlpha.py"],
        sWorkingDirectory="/workspace/stepAlpha")
    assert dictOutcome["iExitCode"] == 0
    assert dictOutcome["sOutput"] == "stepAlpha done\n"
    assert dictOutcome["iOutputBytes"] == len(b"stepAlpha done\n")
    assert dictOutcome["bOomKilled"] is False
    assert dictOutcome["bOutputCapExceeded"] is False
    assert dictOutcome["bWallClockExceeded"] is False
    dictCommandExec = dockerClient.api.listExecCreated[1]
    assert dictCommandExec["listCommand"] == ["python", "stepAlpha.py"]
    assert dictCommandExec["sWorkdir"] == "/workspace/stepAlpha"
    assert dictCommandExec["bStdin"] is False
    assert dictCommandExec["sContainerId"] == S_CONTAINER_ID
    assert dockerClient.api.listKilled == []


def testBoundedCommandStreamsItsStdinPayload(clockFake):
    """A stdin payload is written in full and the write half is closed."""
    dockerClient = _FakeDockerClient()
    dictGateway, dictCreated = ftBuildLiveGateway(dockerClient)
    socketCommand = _ScriptedSocket([fbaBuildFrame(b"read ok")])
    fnQueueCommandSockets(dockerClient, socketCommand)
    disposableContainer.fdictExecuteBoundedCommand(
        dictGateway, dictCreated["sHandle"], ["cat"],
        baStdinPayload=b"payloadForStdin")
    assert socketCommand.baSent == b"payloadForStdin"
    assert socketCommand.listShutdowns == [socket.SHUT_WR]
    assert dockerClient.api.listExecCreated[1]["bStdin"] is True


def testRisingCgroupCounterConcludesAnOomKill(clockFake):
    """Exit 137 plus a risen oom_kill counter is attributed to the kernel."""
    dockerClient = _FakeDockerClient()
    dictGateway, dictCreated = ftBuildLiveGateway(dockerClient)
    fnQueueCommandSockets(dockerClient, _ScriptedSocket(), iBefore=4,
                          iAfter=5)
    dockerClient.api.dictExitCodes["exec1"] = 137
    dockerClient.api.listInspectAnswers.append(
        {"State": {"OOMKilled": False}})
    dictOutcome = disposableContainer.fdictExecuteBoundedCommand(
        dictGateway, dictCreated["sHandle"], ["python", "stepAlpha.py"])
    assert dictOutcome["iExitCode"] == 137
    assert dictOutcome["bOomKilled"] is True


def testUnreadableCounterFallsBackToTheDaemonStateFlag(clockFake):
    """A counter that cannot be read is not zero; the State flag decides."""
    dockerClient = _FakeDockerClient()
    dockerClient.api.bRefuseOomCounterExec = True
    dictGateway, dictCreated = ftBuildLiveGateway(dockerClient)
    dockerClient.api.listSockets.append(_ScriptedSocket())
    dockerClient.api.listInspectAnswers.append(
        {"State": {"OOMKilled": True}})
    dictOutcome = disposableContainer.fdictExecuteBoundedCommand(
        dictGateway, dictCreated["sHandle"], ["python", "stepAlpha.py"])
    assert dictOutcome["bOomKilled"] is True
    assert len(dockerClient.api.listExecCreated) == 1


def testUnanswerableInspectsYieldNoExitCodeAndNoOomClaim(clockFake):
    """With no daemon answers, nothing is fabricated in either field."""
    dockerClient = _FakeDockerClient()
    dockerClient.api.bRefuseOomCounterExec = True
    dockerClient.api.exceptionExecInspect = docker.errors.APIError("timeout")
    dictGateway, dictCreated = ftBuildLiveGateway(dockerClient)
    dockerClient.api.listSockets.append(_ScriptedSocket())
    dockerClient.api.listInspectAnswers.append(
        docker.errors.APIError("inspect timed out"))
    dictOutcome = disposableContainer.fdictExecuteBoundedCommand(
        dictGateway, dictCreated["sHandle"], ["python", "stepAlpha.py"])
    assert dictOutcome["iExitCode"] is None
    assert dictOutcome["bOomKilled"] is False


def testOutputCapBreachKillsTheContainerAndClaimsNoExitCode(clockFake):
    """A breached cap kills the container; its exit code is never asked."""
    dockerClient = _FakeDockerClient()
    dictGateway, dictCreated = ftBuildLiveGateway(dockerClient)
    fnQueueCommandSockets(dockerClient, _ScriptedSocket(
        [fbaBuildFrame(b"0123456789")]))
    dockerClient.api.listInspectAnswers.extend([
        {"State": {"Running": False}}, {"State": {"OOMKilled": False}}])
    dictOutcome = disposableContainer.fdictExecuteBoundedCommand(
        dictGateway, dictCreated["sHandle"], ["yes"], iOutputByteCap=4)
    assert dictOutcome["bOutputCapExceeded"] is True
    assert dictOutcome["sOutput"] == "0123"
    assert dictOutcome["iExitCode"] is None
    assert "exec1" not in dockerClient.api.listExecInspected
    assert dockerClient.api.listKilled == [S_CONTAINER_ID]


def testKillIsRetriedUntilTheDaemonSaysStoppedThenBounded(clockFake):
    """A kill the daemon absorbs is repeated, but at most three times."""
    dockerClient = _FakeDockerClient()
    dockerClient.api.exceptionKill = docker.errors.APIError("kill timed out")
    dictGateway, dictCreated = ftBuildLiveGateway(dockerClient)

    def fnAdvancePastDeadline():
        clockFake.fNow += 10.0

    fnQueueCommandSockets(dockerClient, _ScriptedSocket(
        [socket.timeout()], fnOnTimeout=fnAdvancePastDeadline))
    dockerClient.api.listInspectAnswers.extend([
        {"State": {"Running": True}}, {"State": {"Running": True}},
        {"State": {"Running": True}}, {"State": {"OOMKilled": False}}])
    dictOutcome = disposableContainer.fdictExecuteBoundedCommand(
        dictGateway, dictCreated["sHandle"], ["sleep", "100"],
        fWallClockSeconds=5.0)
    assert dictOutcome["bWallClockExceeded"] is True
    assert dictOutcome["iExitCode"] is None
    assert dockerClient.api.listKilled == [S_CONTAINER_ID] * 3
    assert clockFake.listSleeps == [0.2, 0.2, 0.2]


def testKillStopsAsSoonAsTheContainerIsGone(clockFake):
    """An inspect that raises after a kill means the container is gone."""
    dockerClient = _FakeDockerClient()
    dictGateway, dictCreated = ftBuildLiveGateway(dockerClient)
    fnQueueCommandSockets(dockerClient, _ScriptedSocket(
        [fbaBuildFrame(b"0123456789")]))
    dockerClient.api.listInspectAnswers.append(
        docker.errors.NotFound("gone"))
    dictOutcome = disposableContainer.fdictExecuteBoundedCommand(
        dictGateway, dictCreated["sHandle"], ["yes"], iOutputByteCap=2)
    assert dockerClient.api.listKilled == [S_CONTAINER_ID]
    assert dictOutcome["bOutputCapExceeded"] is True
    assert dictOutcome["bOomKilled"] is False


# ----- identity-verified destruction ------------------------------------


def testDestroyRemovesOnlyAContainerCarryingItsReservation():
    """A matching label is removed, proven absent, and the handle retired."""
    dockerClient = _FakeDockerClient()
    dictGateway, dictCreated = ftBuildLiveGateway(dockerClient)
    dockerClient.api.listInspectAnswers.append(
        fdictLabelsForReservation(dictCreated["sReservationId"]))
    dictOutcome = disposableContainer.fdictDestroyAndSettle(
        dictGateway, dictCreated["sHandle"])
    assert dictOutcome["sOutcome"] == (
        disposableSpecification.S_OUTCOME_DESTROYED)
    assert dockerClient.api.listRemoved == [(S_CONTAINER_ID, True, True)]
    assert dictGateway["dictHandlesById"] == {}
    assert dictGateway["dictReservationsById"] == {}


def testDestroyRefusesAContainerWhoseLabelNamesAnotherReservation():
    """The id no longer names this gateway's container: nothing is removed."""
    dockerClient = _FakeDockerClient()
    dictGateway, dictCreated = ftBuildLiveGateway(dockerClient)
    dockerClient.api.listInspectAnswers.append(
        fdictLabelsForReservation("reservationOfSomeoneElse"))
    with pytest.raises(disposableContainer.DisposableContainerError,
                       match="nothing was destroyed"):
        disposableContainer.fdictDestroyAndSettle(
            dictGateway, dictCreated["sHandle"])
    assert dockerClient.api.listRemoved == []
    assert dictCreated["sHandle"] in dictGateway["dictHandlesById"]


def testDestroyOfAnAlreadyGoneContainerSettlesWithoutRemoval():
    """A 404 at the identity inspect is already the proof of absence."""
    dockerClient = _FakeDockerClient()
    dictGateway, dictCreated = ftBuildLiveGateway(dockerClient)
    dictOutcome = disposableContainer.fdictDestroyAndSettle(
        dictGateway, dictCreated["sHandle"])
    assert dictOutcome["sOutcome"] == (
        disposableSpecification.S_OUTCOME_DESTROYED)
    assert "already gone" in dictOutcome["sReason"]
    assert dockerClient.api.listRemoved == []
    assert dictGateway["dictHandlesById"] == {}


def testDestroyWithAnUnansweredInspectQuarantinesAndKeepsTheHandle():
    """An indeterminate daemon answer attempts no removal and stays visible."""
    dockerClient = _FakeDockerClient()
    dictGateway, dictCreated = ftBuildLiveGateway(dockerClient)
    dockerClient.api.listInspectAnswers.append(
        docker.errors.APIError("daemon timed out"))
    dictOutcome = disposableContainer.fdictDestroyAndSettle(
        dictGateway, dictCreated["sHandle"])
    assert dictOutcome["sOutcome"] == (
        disposableSpecification.S_OUTCOME_QUARANTINED)
    assert "daemon timed out" in dictOutcome["sReason"]
    assert dockerClient.api.listRemoved == []
    assert dictCreated["sHandle"] in dictGateway["dictHandlesById"]
    listQuarantined = disposableContainer.flistDescribeQuarantinedReservations(
        dictGateway)
    assert [dictEntry["sReservationId"] for dictEntry in listQuarantined] == [
        dictCreated["sReservationId"]]


def testProbeDistinguishesPresentAbsentAndIndeterminate():
    """Three answers, because absent and errored must never be conflated."""
    dockerClient = _FakeDockerClient()
    dockerClient.api.listInspectAnswers.extend([
        {"Config": {"Labels": None}},
        docker.errors.NotFound("gone"),
        ConnectionError("socket closed"),
    ])
    listAnswers = [
        disposableContainer.fdictProbeContainerAbsence(
            dockerClient, S_CONTAINER_ID)
        for _ in range(3)
    ]
    assert listAnswers[0] == {
        "sAnswer": disposableSpecification.S_ABSENCE_PRESENT,
        "sDetail": "", "dictLabels": {}}
    assert listAnswers[1]["sAnswer"] == (
        disposableSpecification.S_ABSENCE_ABSENT)
    assert listAnswers[2]["sAnswer"] == (
        disposableSpecification.S_ABSENCE_INDETERMINATE)
    assert listAnswers[2]["sDetail"] == "ConnectionError: socket closed"


def testRemovalThatFindsNothingStillRequiresTheProbe():
    """A 404 on removal is tolerated, but only the probe settles it."""
    dockerClient = _FakeDockerClient()
    dockerClient.api.exceptionRemove = docker.errors.NotFound("gone")
    dictOutcome = disposableContainer.fdictDestroyContainerAndProveAbsence(
        dockerClient, S_CONTAINER_ID)
    assert dictOutcome["sOutcome"] == (
        disposableSpecification.S_OUTCOME_DESTROYED)
    assert dictOutcome["dictProbe"]["sAnswer"] == (
        disposableSpecification.S_ABSENCE_ABSENT)


def testContainerStillPresentAfterRemovalIsQuarantined():
    """A removal the probe contradicts is not reported as completion."""
    dockerClient = _FakeDockerClient()
    dockerClient.api.listInspectAnswers.append({"Config": {"Labels": {}}})
    dictOutcome = disposableContainer.fdictDestroyContainerAndProveAbsence(
        dockerClient, S_CONTAINER_ID)
    assert dictOutcome["sOutcome"] == (
        disposableSpecification.S_OUTCOME_QUARANTINED)
    assert "'present'" in dictOutcome["sReason"]


# ----- crash recovery ----------------------------------------------------


def fcontainerBuildSurvivor(sId, sName, sResourceName):
    """Return a listed container stamped as a disposable survivor."""
    return types.SimpleNamespace(
        id=sId, name=sName, status="exited",
        labels={disposableSpecification.S_DISPOSABLE_LABEL: "reservationOld",
                disposableSpecification.S_DISPOSABLE_ROLE_LABEL: "shadow",
                disposableSpecification.S_DISPOSABLE_RESOURCE_LABEL:
                    sResourceName})


def fnPatchClientFactory(monkeypatch, dockerClient=None, exceptionConnect=None):
    """Point the lazy docker import at a scripted daemon or a failed connect."""

    def fdockerFromEnvironment(timeout):
        assert timeout == disposableContainer.I_DAEMON_TIMEOUT_SECONDS
        if exceptionConnect is not None:
            raise exceptionConnect
        return dockerClient

    moduleFake = types.SimpleNamespace(
        from_env=fdockerFromEnvironment, errors=docker.errors)
    monkeypatch.setattr(disposableContainer, "_fmoduleGetDocker",
                        lambda: moduleFake)
    monkeypatch.setattr(disposableContainer, "_fnEnsureDockerHost",
                        lambda: None)


def testReclaimDestroysASurvivorOfAVanishedContainerAndLogsIt(
        monkeypatch, caplog):
    """A survivor stamped with a gone container id is swept and reported."""
    dockerClient = _FakeDockerClient()
    sVanishedId = "abcdef0123456789abcdef01"
    dockerClient.containers.listListed = [
        fcontainerBuildSurvivor("survivorContainerId0", "survivorName",
                                sVanishedId)]
    fnPatchClientFactory(monkeypatch, dockerClient)
    with caplog.at_level("INFO", logger="vaibify"):
        dictSwept = disposableContainer.fdictReclaimStrandedDisposables()
    assert dictSwept["iQuarantined"] == 0
    assert [dictEntry["sContainerName"]
            for dictEntry in dictSwept["listSettled"]] == ["survivorName"]
    assert dockerClient.api.listRemoved == [
        ("survivorContainerId0", True, True)]
    assert "Reclaimed 1 disposable container(s)" in caplog.text


def testReclaimIsSilentWhenTheDaemonIsUnreachable(monkeypatch, caplog):
    """A hub with no Docker is an ordinary state, not a warning."""
    fnPatchClientFactory(
        monkeypatch, exceptionConnect=docker.errors.DockerException(
            "no daemon"))
    with caplog.at_level("INFO", logger="vaibify"):
        dictSwept = disposableContainer.fdictReclaimStrandedDisposables()
    assert dictSwept == {"listSettled": [], "iQuarantined": 0}
    assert "Reclaimed" not in caplog.text


def testReclaimWithNoSurvivorsNeverListsTheDaemon(monkeypatch, caplog):
    """No labeled survivors ends the pass before the full container list."""
    dockerClient = _FakeDockerClient()
    dockerClient.containers.listListed = [types.SimpleNamespace(
        id="ordinaryContainerId", name="ordinaryName", status="running",
        labels={})]
    fnPatchClientFactory(monkeypatch, dockerClient)
    with caplog.at_level("INFO", logger="vaibify"):
        dictSwept = disposableContainer.fdictReclaimStrandedDisposables()
    assert dictSwept == {"listSettled": [], "iQuarantined": 0}
    assert dockerClient.api.listRemoved == []
    assert "Reclaimed" not in caplog.text
