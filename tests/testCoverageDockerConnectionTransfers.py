"""DockerConnection's daemon-API edges: archives, execs, probes, errors.

The docker SDK client is the only thing faked, by a small explicit
double rather than a ``MagicMock`` so that an attribute the code does
not expect fails loudly instead of answering with another mock. Tar
archives are built and parsed for real; process-group probes are fed
the output a real ``/bin/sh`` walk would print.

Container id and name differ throughout, and the fake daemon resolves
only ids.
"""

import io
import logging
import os
import shlex
import sys
import tarfile
from types import SimpleNamespace

import pytest

from vaibify.config import mutationAdmission
from vaibify.docker import dockerConnection as dockerConnectionModule
from vaibify.docker.dockerConnection import DockerConnection


S_CONTAINER_ID = "8e1d44c0b7a2f9936d10"
S_CONTAINER_NAME = "vaibifyProjectBeta"


class FakeDaemonError(Exception):
    """An SDK-shaped error carrying an HTTP status code."""

    def __init__(self, sMessage, iStatusCode=None):
        super().__init__(sMessage)
        self.status_code = iStatusCode


class RecordingContainer:
    """A container double that records archive puts and answers execs."""

    def __init__(self):
        self.id = S_CONTAINER_ID
        self.name = S_CONTAINER_NAME
        self.short_id = S_CONTAINER_ID[:10]
        self.image = SimpleNamespace(
            attrs={"Config": {"User": "researcher"}},
            tags=["vaibify-beta:latest"], id="sha256:" + "ef" * 32,
        )
        self.attrs = {}
        self.iReloadCount = 0
        self.listPutArchives = []
        self.listExecAnswers = []
        self.listExecCalls = []
        self.listArchiveChunks = []
        self.errorArchive = None

    def reload(self):
        self.iReloadCount += 1

    def put_archive(self, sDirectory, fileArchive):
        baArchive = fileArchive.read()
        self.listPutArchives.append((sDirectory, baArchive))
        return True

    def get_archive(self, sPath):
        if self.errorArchive is not None:
            raise self.errorArchive
        return iter(self.listArchiveChunks), {"name": sPath}

    def exec_run(self, cmd, **dictKwargs):
        self.listExecCalls.append({"cmd": cmd, **dictKwargs})
        objAnswer = self.listExecAnswers.pop(0)
        if isinstance(objAnswer, Exception):
            raise objAnswer
        return objAnswer


class FakeDockerApi:
    """The low-level ``client.api`` surface the connection drives."""

    def __init__(self):
        self.listCalls = []
        self.dictExecInspect = {}
        self.listStreamChunks = []

    def exec_create(self, sContainerId, **dictKwargs):
        self.listCalls.append(("exec_create", sContainerId, dictKwargs))
        return {"Id": "execIdentifier01"}

    def exec_start(self, sExecId, **dictKwargs):
        self.listCalls.append(("exec_start", sExecId, dictKwargs))
        if dictKwargs.get("stream"):
            return iter(self.listStreamChunks)
        return "rawSocketHandle"

    def exec_resize(self, sExecId, **dictKwargs):
        self.listCalls.append(("exec_resize", sExecId, dictKwargs))

    def exec_inspect(self, sExecId):
        self.listCalls.append(("exec_inspect", sExecId, {}))
        objAnswer = self.dictExecInspect[sExecId]
        if isinstance(objAnswer, Exception):
            raise objAnswer
        return objAnswer


class FakeDockerClient:
    """A docker client double holding one container, addressed by id."""

    def __init__(self, container):
        self.containerHeld = container
        self.api = FakeDockerApi()
        self.iGetCount = 0
        self.containers = SimpleNamespace(get=self._fcontainerGet)
        self.images = SimpleNamespace(get=self._fnGetImage)
        self.objImageAnswer = None
        self.objInfoAnswer = {}

    def _fcontainerGet(self, sContainerId):
        self.iGetCount += 1
        if sContainerId != self.containerHeld.id:
            raise FakeDaemonError("No such container", 404)
        return self.containerHeld

    def _fnGetImage(self, sImageName):
        if isinstance(self.objImageAnswer, Exception):
            raise self.objImageAnswer
        return SimpleNamespace(id="sha256:" + "12" * 32)

    def info(self):
        if isinstance(self.objInfoAnswer, Exception):
            raise self.objInfoAnswer
        return self.objInfoAnswer


def ftBuildConnection(monkeypatch, container=None):
    """Return (connection, client, container) over a fake daemon."""
    container = container or RecordingContainer()
    clientDocker = FakeDockerClient(container)
    monkeypatch.setattr(
        dockerConnectionModule, "_fnEnsureDockerHost", lambda: None,
    )
    monkeypatch.setattr(
        dockerConnectionModule, "_fmoduleGetDocker",
        lambda: SimpleNamespace(from_env=lambda timeout: clientDocker),
    )
    monkeypatch.setattr(dockerConnectionModule, "_CACHED_CONTAINER_USER", {})
    return DockerConnection(), clientDocker, container


def flistReadTarMembers(baArchive):
    """Return the TarInfo members of an uncompressed tar held in bytes."""
    with tarfile.open(fileobj=io.BytesIO(baArchive), mode="r") as fileTar:
        return fileTar.getmembers()


def fbaBuildTar(listEntries):
    """Return tar bytes from (name, bytes-or-None-for-directory) pairs."""
    bufferTar = io.BytesIO()
    with tarfile.open(fileobj=bufferTar, mode="w") as fileTar:
        for sName, baContent in listEntries:
            infoEntry = tarfile.TarInfo(name=sName)
            if baContent is None:
                infoEntry.type = tarfile.DIRTYPE
                fileTar.addfile(infoEntry)
                continue
            infoEntry.size = len(baContent)
            fileTar.addfile(infoEntry, io.BytesIO(baContent))
    return bufferTar.getvalue()


# ---------------------------------------------------------------------
# Construction: the docker import and the session pool
# ---------------------------------------------------------------------


def testMissingDockerPackageExplainsTheBrokenInstall(monkeypatch):
    """An absent docker SDK raises ImportError with the repair command."""
    monkeypatch.setitem(sys.modules, "docker", None)
    with pytest.raises(ImportError, match="force-reinstall vaibify"):
        dockerConnectionModule._fmoduleGetDocker()


def testPoolTuningSkipsAClientWithoutAMountableSession():
    """A client whose api cannot mount adapters is left untouched."""
    clientDocker = SimpleNamespace(api=SimpleNamespace())
    dockerConnectionModule._fnTuneDockerSessionPool(clientDocker)
    assert not hasattr(clientDocker.api, "listMounted")


class RecordingSession:
    """A requests-session double recording mounts, optionally refusing."""

    def __init__(self, sSocketPath="", setRefusedPrefixes=()):
        self.listMounted = []
        self.setRefusedPrefixes = set(setRefusedPrefixes)
        self.adapters = {
            "http+docker://": SimpleNamespace(socket_path=sSocketPath),
        }

    def mount(self, sPrefix, adapterMounted):
        if sPrefix in self.setRefusedPrefixes:
            raise RuntimeError(f"refused {sPrefix}")
        self.listMounted.append((sPrefix, adapterMounted))


def testPoolTuningWithoutASocketPathMountsOnlyTcpSchemes():
    """No unix socket path means the docker scheme keeps its adapter."""
    sessionDocker = RecordingSession(sSocketPath="")
    dockerConnectionModule._fnTuneDockerSessionPool(
        SimpleNamespace(api=sessionDocker))
    assert [sPrefix for sPrefix, _ in sessionDocker.listMounted] == [
        "http://", "https://",
    ]


def testPoolTuningFailuresAreLoggedAndNeverFatal(caplog):
    """A refused TCP mount and a refused unix mount both only warn."""
    pytest.importorskip("docker.transport.unixconn")
    sessionDocker = RecordingSession(
        sSocketPath="/var/run/docker.sock",
        setRefusedPrefixes={"http://", "http+docker://"},
    )
    with caplog.at_level(logging.WARNING, logger="vaibify"):
        dockerConnectionModule._fnTuneDockerSessionPool(
            SimpleNamespace(api=sessionDocker))
    assert [sPrefix for sPrefix, _ in sessionDocker.listMounted] == [
        "https://",
    ]
    sLog = caplog.text
    assert "docker pool tune failed for http://" in sLog
    assert "docker unix-socket pool tune failed" in sLog


def testContainerUserIsNotCachedForANonStringId(monkeypatch):
    """A container whose id is not a string resolves but is not cached."""
    monkeypatch.setattr(dockerConnectionModule, "_CACHED_CONTAINER_USER", {})
    containerOdd = SimpleNamespace(
        id=424242, image=SimpleNamespace(attrs={"Config": {"User": "analyst"}}),
    )
    assert dockerConnectionModule._fsResolveContainerUser(
        containerOdd) == "analyst"
    assert dockerConnectionModule._CACHED_CONTAINER_USER == {}


# ---------------------------------------------------------------------
# Image state and daemon capacity
# ---------------------------------------------------------------------


def testImageStateDistinguishesBuiltMissingAndUnanswered(monkeypatch):
    """Only ImageNotFound means missing; any other failure is unanswered."""
    dockerErrors = pytest.importorskip("docker.errors")
    connection, clientDocker, _ = ftBuildConnection(monkeypatch)
    assert connection.fsImageState("vaibify-beta:latest") == "built"
    clientDocker.objImageAnswer = dockerErrors.ImageNotFound("no image")
    assert connection.fsImageState("vaibify-beta:latest") == "missing"
    clientDocker.objImageAnswer = ConnectionError("daemon busy")
    assert connection.fsImageState("vaibify-beta:latest") == "unanswered"


def testDaemonCapacityReadsMemoryAndCpusAndZeroesOnFailure(monkeypatch):
    """docker info's MemTotal/NCPU are returned; a failure is all zero."""
    connection, clientDocker, _ = ftBuildConnection(monkeypatch)
    clientDocker.objInfoAnswer = {"MemTotal": 8_300_000_000, "NCPU": 6}
    assert connection.fdictReadDaemonCapacity() == {
        "iMemoryBytes": 8_300_000_000, "iCpuCount": 6,
    }
    clientDocker.objInfoAnswer = {"MemTotal": None}
    assert connection.fdictReadDaemonCapacity() == {
        "iMemoryBytes": 0, "iCpuCount": 0,
    }
    clientDocker.objInfoAnswer = RuntimeError("daemon hiccup")
    assert connection.fdictReadDaemonCapacity() == {
        "iMemoryBytes": 0, "iCpuCount": 0,
    }


# ---------------------------------------------------------------------
# Container lookup and exec sessions
# ---------------------------------------------------------------------


def testContainerLookupIsCachedById(monkeypatch):
    """A second lookup of the same id does not ask the daemon again."""
    connection, clientDocker, container = ftBuildConnection(monkeypatch)
    assert connection.fcontainerGetById(S_CONTAINER_ID) is container
    assert connection.fcontainerGetById(S_CONTAINER_ID) is container
    assert clientDocker.iGetCount == 1
    with pytest.raises(FakeDaemonError):
        connection.fcontainerGetById(S_CONTAINER_NAME)


def testRunningExecIdentifiersKeepOnlyThoseStillRunning(monkeypatch):
    """Each listed exec is confirmed through exec_inspect after a reload."""
    connection, clientDocker, container = ftBuildConnection(monkeypatch)
    container.attrs = {"ExecIDs": ["execLive", "execDone", "execAlsoLive"]}
    clientDocker.api.dictExecInspect = {
        "execLive": {"Running": True},
        "execDone": {"Running": False},
        "execAlsoLive": {"Running": True},
    }
    assert connection.flistRunningExecIdentifiers(S_CONTAINER_ID) == [
        "execLive", "execAlsoLive",
    ]
    assert container.iReloadCount == 1


def testRunningExecIdentifiersOfAnIdleContainerIsEmpty(monkeypatch):
    """ExecIDs of None is no running work, with no inspect calls."""
    connection, clientDocker, container = ftBuildConnection(monkeypatch)
    container.attrs = {"ExecIDs": None}
    assert connection.flistRunningExecIdentifiers(S_CONTAINER_ID) == []
    assert clientDocker.api.listCalls == []


def testRunningExecInspectFailurePropagates(monkeypatch):
    """An unreadable exec is not reported as 'not running'."""
    connection, clientDocker, container = ftBuildConnection(monkeypatch)
    container.attrs = {"ExecIDs": ["execUnreadable"]}
    clientDocker.api.dictExecInspect = {
        "execUnreadable": FakeDaemonError("daemon timeout", 500),
    }
    with pytest.raises(FakeDaemonError, match="daemon timeout"):
        connection.flistRunningExecIdentifiers(S_CONTAINER_ID)


def testExecCreateWithAnArgvSkipsShellSplittingAndUsesTheImageUser(
    monkeypatch,
):
    """listCommand is passed verbatim; the user defaults to the image's."""
    connection, clientDocker, _ = ftBuildConnection(monkeypatch)
    listArgv = ["/bin/sh", "-c", "echo 'a b' && exit 3"]
    sExecId = connection.fsExecCreate(S_CONTAINER_ID, listCommand=listArgv)
    assert sExecId == "execIdentifier01"
    sMethod, sTarget, dictKwargs = clientDocker.api.listCalls[0]
    assert (sMethod, sTarget) == ("exec_create", S_CONTAINER_ID)
    assert dictKwargs["cmd"] == listArgv
    assert dictKwargs["user"] == "researcher"
    assert dictKwargs["tty"] is True


def testExecStartResizeAndInspectForwardToTheDaemon(monkeypatch):
    """Socket start, PTY resize and inspect reach the low-level API."""
    connection, clientDocker, _ = ftBuildConnection(monkeypatch)
    clientDocker.api.dictExecInspect = {"execIdentifier01": {"Running": True}}
    assert connection.fsocketExecStart("execIdentifier01") == (
        "rawSocketHandle")
    connection.fnExecResize("execIdentifier01", 40, 120)
    assert connection.fdictInspectExec("execIdentifier01") == {
        "Running": True,
    }
    assert clientDocker.api.listCalls[:2] == [
        ("exec_start", "execIdentifier01", {"socket": True, "tty": True}),
        ("exec_resize", "execIdentifier01", {"height": 40, "width": 120}),
    ]


def testStreamedExecWithoutACallbackAccumulatesTheTrailingPartial(
    monkeypatch,
):
    """With no emitter the strings carry every line, including the tail."""
    connection, clientDocker, _ = ftBuildConnection(monkeypatch)
    clientDocker.api.listStreamChunks = [
        (b"first line\nsecond ", None),
        (None, b"warning one\n"),
        (b"half", b"trailing error"),
    ]
    clientDocker.api.dictExecInspect = {"execIdentifier01": {"ExitCode": 4}}
    tExecResult = connection.ftRunInContainerStreamedWithChunks(
        S_CONTAINER_ID, "runStep", None)
    assert tExecResult.iExitCode == 4
    assert tExecResult.sStdout == "first line\nsecond half"
    assert tExecResult.sStderr == "warning one\ntrailing error"


# ---------------------------------------------------------------------
# Archive reads
# ---------------------------------------------------------------------


def testDirectoryArchiveUnderTheCapIsReturnedWhole(monkeypatch):
    """Chunks are concatenated in order when within the ceiling."""
    connection, _, container = ftBuildConnection(monkeypatch)
    container.listArchiveChunks = [b"abc", b"def", b"g"]
    assert connection.fbaFetchDirectoryArchive(
        S_CONTAINER_ID, "/workspace/repoBeta", 7) == b"abcdefg"


def testDirectoryArchiveOverTheCapIsRefusedMidStream(monkeypatch):
    """The ceiling is enforced as chunks arrive, before the rest is read."""
    connection, _, container = ftBuildConnection(monkeypatch)
    listPulled = []

    def fiterChunks():
        for baChunk in (b"a" * 5, b"b" * 5, b"c" * 5):
            listPulled.append(baChunk)
            yield baChunk

    container.listArchiveChunks = fiterChunks()
    with pytest.raises(ValueError, match="exceeds the 8 byte ceiling"):
        connection.fbaFetchDirectoryArchive(
            S_CONTAINER_ID, "/workspace/repoBeta", 8)
    assert len(listPulled) == 2


def testDirectoryArchiveOfAnUnreadablePathIsFileNotFound(monkeypatch):
    """A daemon refusal becomes FileNotFoundError naming path and cause."""
    connection, _, container = ftBuildConnection(monkeypatch)
    container.errorArchive = FakeDaemonError("path not found", 404)
    with pytest.raises(
        FileNotFoundError, match="/workspace/gone: path not found",
    ):
        connection.fbaFetchDirectoryArchive(
            S_CONTAINER_ID, "/workspace/gone", 1024)


def testStreamFileSkipsADirectoryEntryAndClosesTheStream(monkeypatch):
    """The first REGULAR member's bytes are yielded; the stream is closed."""
    connection, _, container = ftBuildConnection(monkeypatch)
    baArchive = fbaBuildTar([
        ("outputDirectory", None),
        ("outputDirectory/dataFile.csv", b"x,y\n1,2\n"),
    ])

    class ClosableStream:
        def __init__(self):
            self.bClosed = False
            self.iterChunks = iter(
                [baArchive[iStart:iStart + 100]
                 for iStart in range(0, len(baArchive), 100)])

        def __iter__(self):
            return self

        def __next__(self):
            return next(self.iterChunks)

        def close(self):
            self.bClosed = True
            raise OSError("socket already closed")

    streamArchive = ClosableStream()
    container.listArchiveChunks = streamArchive
    baContent = b"".join(connection.fiterStreamFile(
        S_CONTAINER_ID, "/workspace/outputDirectory", iChunkSizeBytes=3))
    assert baContent == b"x,y\n1,2\n"
    assert streamArchive.bClosed is True


def testBytesPipeReadAllDrainsEveryChunk():
    """read() with no size, or None, returns everything left."""
    pipeBytes = dockerConnectionModule._BytesGeneratorPipe(
        [b"alpha", b"beta", b"gamma"])
    assert pipeBytes.read(3) == b"alp"
    assert pipeBytes.read() == b"habetagamma"
    assert pipeBytes.read(None) == b""
    assert pipeBytes.read(10) == b""


# ---------------------------------------------------------------------
# Tree writes and host-path copies
# ---------------------------------------------------------------------


def fnPopulateHostTree(pathRoot):
    """Create a small host directory with a nested file and a symlink."""
    pathTree = pathRoot / "inputData"
    (pathTree / "nested").mkdir(parents=True)
    (pathTree / "dataFile.csv").write_bytes(b"1,2,3\n")
    (pathTree / "nested" / "notes.txt").write_bytes(b"notes")
    os.symlink("/etc/passwd", str(pathTree / "outsideLink"))
    return pathTree


def testTreeWriteStampsContainerOwnershipAndKeepsSymlinksAsLinks(
    monkeypatch, tmp_path,
):
    """Every entry is uid/gid 1000 with no host names; links stay links."""
    pathTree = fnPopulateHostTree(tmp_path)
    connection, _, container = ftBuildConnection(monkeypatch)
    connection.fnWriteTreeViaTar(
        S_CONTAINER_ID, "/workspace/projectBeta", [str(pathTree)])
    sDestination, baArchive = container.listPutArchives[0]
    assert sDestination == "/workspace/projectBeta"
    dictMembers = {
        infoMember.name: infoMember
        for infoMember in flistReadTarMembers(baArchive)
    }
    assert set(dictMembers) == {
        "inputData", "inputData/dataFile.csv", "inputData/nested",
        "inputData/nested/notes.txt", "inputData/outsideLink",
    }
    for infoMember in dictMembers.values():
        assert (infoMember.uid, infoMember.gid) == (1000, 1000)
        assert (infoMember.uname, infoMember.gname) == ("", "")
    assert dictMembers["inputData/outsideLink"].issym()
    assert dictMembers["inputData/outsideLink"].linkname == "/etc/passwd"


def testTreeWriteHonoursExplicitOwnership(monkeypatch, tmp_path):
    """A caller-supplied uid/gid is stamped instead of the default."""
    pathTree = fnPopulateHostTree(tmp_path)
    connection, _, container = ftBuildConnection(monkeypatch)
    connection.fnWriteTreeViaTar(
        S_CONTAINER_ID, "/workspace", [str(pathTree)], iUid=2001, iGid=2002)
    listMembers = flistReadTarMembers(container.listPutArchives[0][1])
    assert {(infoMember.uid, infoMember.gid) for infoMember in listMembers} == {
        (2001, 2002),
    }


def testTreeWriteInAnEnforcedLaneIsRefusedBeforeTheDaemon(
    monkeypatch, tmp_path,
):
    """Without an admission no archive reaches put_archive."""
    pathTree = fnPopulateHostTree(tmp_path)
    connection, _, container = ftBuildConnection(monkeypatch)
    tokenLane = mutationAdmission.ftokenMarkEnforcedLane()
    try:
        with pytest.raises(mutationAdmission.MutationNotAdmittedError):
            connection.fnWriteTreeViaTar(
                S_CONTAINER_ID, "/workspace", [str(pathTree)])
    finally:
        mutationAdmission.fnResetEnforcedLane(tokenLane)
    assert container.listPutArchives == []


def fnAnswerDirectoryProbe(container, bIsDirectory):
    """Queue the typed-read answer for one is-a-directory probe."""
    container.listExecAnswers.append(
        (0, (b"1" if bIsDirectory else b"0", b"")))


def testCopyFileIntoAnExistingDirectoryKeepsItsBasename(
    monkeypatch, tmp_path,
):
    """docker cp semantics: file into a directory lands under its name."""
    pathSource = tmp_path / "dataFile.csv"
    pathSource.write_bytes(b"payload")
    connection, _, container = ftBuildConnection(monkeypatch)
    fnAnswerDirectoryProbe(container, True)
    connection.fnCopyHostPathIntoContainer(
        S_CONTAINER_ID, str(pathSource), "/workspace/inputs")
    sDestination, baArchive = container.listPutArchives[0]
    assert sDestination == "/workspace/inputs"
    listMembers = flistReadTarMembers(baArchive)
    assert [infoMember.name for infoMember in listMembers] == ["dataFile.csv"]
    sProbeProgram = shlex.split(container.listExecCalls[0]["cmd"][2])[2]
    assert repr("/workspace/inputs") in sProbeProgram


def testCopyFileToANewPathWritesThatExactPath(monkeypatch, tmp_path):
    """A destination that is not a directory IS the file to write."""
    pathSource = tmp_path / "dataFile.csv"
    pathSource.write_bytes(b"payload")
    connection, _, container = ftBuildConnection(monkeypatch)
    fnAnswerDirectoryProbe(container, False)
    connection.fnCopyHostPathIntoContainer(
        S_CONTAINER_ID, str(pathSource), "/workspace/inputs/renamed.csv")
    sDestination, baArchive = container.listPutArchives[0]
    assert sDestination == "/workspace/inputs"
    infoMember = flistReadTarMembers(baArchive)[0]
    assert infoMember.name == "renamed.csv"
    assert (infoMember.uid, infoMember.gid) == (1000, 1000)


def testCopyDirectoryIntoAnExistingDirectoryNestsIt(monkeypatch, tmp_path):
    """A directory into an existing directory arrives under its own name."""
    pathTree = fnPopulateHostTree(tmp_path)
    connection, _, container = ftBuildConnection(monkeypatch)
    fnAnswerDirectoryProbe(container, True)
    connection.fnCopyHostPathIntoContainer(
        S_CONTAINER_ID, str(pathTree), "/workspace/projectBeta")
    sDestination, baArchive = container.listPutArchives[0]
    assert sDestination == "/workspace/projectBeta"
    assert "inputData/dataFile.csv" in {
        infoMember.name for infoMember in flistReadTarMembers(baArchive)
    }


@pytest.mark.xfail(
    strict=True,
    raises=AssertionError,
    reason=(
        "fnCopyHostPathIntoContainer promises docker cp's reading ('any "
        "other destination IS the path to write'), but a DIRECTORY "
        "source copied to a not-yet-existing destination is archived "
        "under the SOURCE basename into dirname(destination), so it "
        "lands at /workspace/inputData rather than /workspace/renamedData"
    ),
)
def testCopyDirectoryToANewPathLandsAtThatPath(monkeypatch, tmp_path):
    """docker cp ./inputData /workspace/renamedData creates renamedData."""
    pathTree = fnPopulateHostTree(tmp_path)
    connection, _, container = ftBuildConnection(monkeypatch)
    fnAnswerDirectoryProbe(container, False)
    connection.fnCopyHostPathIntoContainer(
        S_CONTAINER_ID, str(pathTree), "/workspace/renamedData")
    sDestination, baArchive = container.listPutArchives[0]
    setLandedPaths = {
        sDestination + "/" + infoMember.name
        for infoMember in flistReadTarMembers(baArchive)
    }
    assert "/workspace/renamedData/dataFile.csv" in setLandedPaths


# ---------------------------------------------------------------------
# Process-group probes and signals
# ---------------------------------------------------------------------


def testProcessGroupProbeParsesTheMemberCount(monkeypatch):
    """The /proc walk's iMembers line is the conclusive count, run as root."""
    connection, _, container = ftBuildConnection(monkeypatch)
    container.listExecAnswers.append((0, b"noise\niMembers=3\n"))
    dictProbe = connection.fdictProbeProcessGroupMembers(S_CONTAINER_ID, 4242)
    assert dictProbe == {
        "bConclusive": True, "iMemberCount": 3,
        "sDetail": "3 live member(s)",
    }
    dictCall = container.listExecCalls[0]
    assert dictCall["user"] == "root"
    assert '"$3" = "4242"' in dictCall["cmd"][2]


@pytest.mark.parametrize("tAnswer", [
    (0, b"no count here\n"),
    (0, b"iMembers=many\n"),
    (1, b"iMembers=0\n"),
    (None, b"iMembers=0\n"),
])
def testProcessGroupProbeWithoutAUsableCountIsInconclusive(
    monkeypatch, tAnswer,
):
    """Unparseable output or a failed exit never reads as 'empty group'."""
    connection, _, container = ftBuildConnection(monkeypatch)
    container.listExecAnswers.append(tAnswer)
    dictProbe = connection.fdictProbeProcessGroupMembers(S_CONTAINER_ID, 17)
    assert dictProbe["bConclusive"] is False
    assert dictProbe["iMemberCount"] == -1


def testProcessGroupProbeOfAGoneContainerIsConclusivelyEmpty(monkeypatch):
    """A 404 or 'is not running' 409 proves no process survives."""
    connection, _, container = ftBuildConnection(monkeypatch)
    container.listExecAnswers.append(FakeDaemonError("No such container", 404))
    container.listExecAnswers.append(
        FakeDaemonError("Container abc is not running", 409))
    for _ in range(2):
        dictProbe = connection.fdictProbeProcessGroupMembers(
            S_CONTAINER_ID, 17)
        assert dictProbe["bConclusive"] is True
        assert dictProbe["iMemberCount"] == 0


def testProcessGroupProbeFailureOfAnotherKindIsInconclusive(monkeypatch):
    """A 500 proves nothing about the container's processes."""
    connection, _, container = ftBuildConnection(monkeypatch)
    container.listExecAnswers.append(FakeDaemonError("server error", 500))
    dictProbe = connection.fdictProbeProcessGroupMembers(S_CONTAINER_ID, 17)
    assert dictProbe["bConclusive"] is False
    assert "server error" in dictProbe["sDetail"]


def testSignalRefusesAnythingButTermAndKill(monkeypatch):
    """HUP is not allowlisted, and nothing is executed."""
    connection, _, container = ftBuildConnection(monkeypatch)
    with pytest.raises(ValueError, match="only TERM and KILL"):
        connection.fnSignalProcessGroupMembers(S_CONTAINER_ID, 17, "HUP")
    assert container.listExecCalls == []


def testSignalRunsOnePassPerOwnerAndSwallowsEachFailure(monkeypatch):
    """Root then the container uid are each tried despite failures."""
    connection, _, container = ftBuildConnection(monkeypatch)
    container.listExecAnswers.extend([
        FakeDaemonError("EPERM", 500), FakeDaemonError("gone", 404),
    ])
    connection.fnSignalProcessGroupMembers(S_CONTAINER_ID, 17, "KILL")
    assert [dictCall["user"] for dictCall in container.listExecCalls] == [
        "root", "1000",
    ]
    assert 'kill -KILL "$iMemberPid"' in container.listExecCalls[0]["cmd"][2]


@pytest.mark.parametrize("objGroup", [0, -5, True, "17", 3.0])
def testProcessGroupScriptRejectsAnythingButAPositiveInteger(objGroup):
    """Only a validated integer can reach the shell script."""
    with pytest.raises(ValueError, match="positive integer"):
        dockerConnectionModule._fsBuildProcessGroupScript(objGroup, ":")


# ---------------------------------------------------------------------
# Error classification
# ---------------------------------------------------------------------


def testContainerGoneReadsTheStatusFromTheResponseToo():
    """status_code may live on the error or on its response."""
    errorNested = Exception("Conflict: container is not running")
    errorNested.response = SimpleNamespace(status_code=409)
    assert dockerConnectionModule.fbErrorMeansContainerGone(errorNested)
    errorOtherConflict = FakeDaemonError("name already in use", 409)
    assert not dockerConnectionModule.fbErrorMeansContainerGone(
        errorOtherConflict)
    assert not dockerConnectionModule.fbErrorMeansContainerGone(
        ValueError("no status"))


def testUnreachableIsFalseWhenTheDockerSdkIsAbsent(monkeypatch):
    """Without docker-py no APIError can exist, so nothing matches."""
    monkeypatch.setitem(sys.modules, "docker.errors", None)
    assert dockerConnectionModule.fbErrorMeansContainerUnreachable(
        OSError("anything")) is False
