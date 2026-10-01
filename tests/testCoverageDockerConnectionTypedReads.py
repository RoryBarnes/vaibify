"""The typed-read adapters of ``DockerConnection``, driven end to end.

Each adapter names a declared program, the connection embeds the
caller's path as a Python literal, quotes the whole program as ONE
shell argument, and hands it to the container's ``exec_run``. The only
boundary faked here is the daemon: ``fcontainerLocalProgram`` answers
``exec_run`` by unquoting the argument and running the program on THIS
machine against ``tmp_path``, so the program text, the quoting, and
the host-side parsing are all exercised together. Error answers that a
real filesystem cannot produce on demand (a failed exec, unparseable
output, a short batch) come from ``fcontainerCannedAnswers``.

Container ids and names are deliberately distinct: the fake client
resolves ONLY ids, so an adapter that looked a container up by name
would fail here.
"""

import base64
import hashlib
import json
import os
import shlex
import socket
import subprocess
import sys
from types import SimpleNamespace

import pytest

from vaibify.docker import dockerConnection as dockerConnectionModule
from vaibify.docker.dockerConnection import DockerConnection


S_CONTAINER_ID = "3f9c2a7be41d0c55aa9e"
S_CONTAINER_NAME = "vaibifyProjectAlpha"


class FakeContainerNotFound(Exception):
    """What the fake daemon raises for an id it does not hold."""

    status_code = 404


class LocalProgramContainer:
    """A container double whose exec runs the typed-read program here."""

    def __init__(self, dictEnvironment=None):
        self.id = S_CONTAINER_ID
        self.name = S_CONTAINER_NAME
        self.image = SimpleNamespace(
            attrs={"Config": {"User": "researcher"}},
            tags=["vaibify-alpha:latest"], id="sha256:" + "ab" * 32,
        )
        self.listExecCalls = []
        self.dictEnvironment = dictEnvironment

    def exec_run(self, cmd, demux=True, user=None, workdir=None):
        self.listExecCalls.append({"listCommand": cmd, "sUser": user})
        listArguments = shlex.split(cmd[2])
        assert cmd[:2] == ["/bin/bash", "-c"]
        assert listArguments[:2] == ["python3", "-c"]
        assert len(listArguments) == 3
        processProgram = subprocess.run(
            [sys.executable, "-c", listArguments[2]],
            capture_output=True, env=self.dictEnvironment,
        )
        self.listExecCalls[-1]["baStdout"] = processProgram.stdout
        return processProgram.returncode, (
            processProgram.stdout, processProgram.stderr,
        )


class CannedAnswerContainer:
    """A container double answering each exec from a fixed queue."""

    def __init__(self, listAnswers):
        self.id = S_CONTAINER_ID
        self.name = S_CONTAINER_NAME
        self.image = SimpleNamespace(
            attrs={"Config": {"User": "researcher"}},
            tags=[], id="sha256:" + "cd" * 32,
        )
        self.listAnswers = list(listAnswers)
        self.listPrograms = []

    def exec_run(self, cmd, demux=True, user=None, workdir=None):
        self.listPrograms.append(shlex.split(cmd[2])[2])
        iExitCode, sStdout, sStderr = self.listAnswers.pop(0)
        return iExitCode, (sStdout.encode(), sStderr.encode())


def fconnectionForContainer(monkeypatch, container):
    """Return a DockerConnection whose daemon holds exactly one container."""

    def fcontainerGetById(sContainerId):
        if sContainerId != container.id:
            raise FakeContainerNotFound(sContainerId)
        return container

    clientDocker = SimpleNamespace(
        containers=SimpleNamespace(get=fcontainerGetById),
        api=object(),
    )
    monkeypatch.setattr(
        dockerConnectionModule, "_fnEnsureDockerHost", lambda: None,
    )
    monkeypatch.setattr(
        dockerConnectionModule, "_fmoduleGetDocker",
        lambda: SimpleNamespace(from_env=lambda timeout: clientDocker),
    )
    monkeypatch.setattr(dockerConnectionModule, "_CACHED_CONTAINER_USER", {})
    return DockerConnection()


def fdictHermeticGitEnvironment(pathHome):
    """Return an environment where git reads no user or system config."""
    pathGlobalConfig = pathHome / "emptyGitConfig"
    pathGlobalConfig.write_text("")
    dictEnvironment = dict(os.environ)
    dictEnvironment.update({
        "GIT_CONFIG_GLOBAL": str(pathGlobalConfig),
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_AUTHOR_NAME": "Test Author",
        "GIT_AUTHOR_EMAIL": "author@example.invalid",
        "GIT_COMMITTER_NAME": "Test Author",
        "GIT_COMMITTER_EMAIL": "author@example.invalid",
    })
    return dictEnvironment


def fnRunGit(pathRepository, listArguments, dictEnvironment):
    """Run one git command in a repository, failing the test on error."""
    subprocess.run(
        ["git", "-C", str(pathRepository)] + listArguments,
        check=True, capture_output=True, env=dictEnvironment,
    )


# ---------------------------------------------------------------------
# Credential and keyring reads
# ---------------------------------------------------------------------


def testCredentialFileReadReturnsTheBytesOnDisk(monkeypatch, tmp_path):
    """A small login document comes back byte-identical."""
    pathCredential = tmp_path / "loginDocument.json"
    baContent = b'{"sToken": "abc\\u00e9"}\n\x00\xff'
    pathCredential.write_bytes(baContent)
    connection = fconnectionForContainer(monkeypatch, LocalProgramContainer())
    assert connection.fbaFetchCredentialFile(
        S_CONTAINER_ID, str(pathCredential)) == baContent


def testOversizedCredentialIsRefusedAfterReadingOneExtraByte(
    monkeypatch, tmp_path,
):
    """The container reads ceiling+1 bytes, never the whole file."""
    iCeiling = dockerConnectionModule.I_MAX_CREDENTIAL_FILE_BYTES
    pathCredential = tmp_path / "hostileLogin.json"
    pathCredential.write_bytes(b"x" * (iCeiling * 3))
    container = LocalProgramContainer()
    connection = fconnectionForContainer(monkeypatch, container)
    with pytest.raises(ValueError, match="byte ceiling"):
        connection.fbaFetchCredentialFile(
            S_CONTAINER_ID, str(pathCredential))
    baEncoded = container.listExecCalls[-1]["baStdout"]
    assert len(base64.b64decode(baEncoded)) == iCeiling + 1


def testCredentialFileAtTheCeilingIsAccepted(monkeypatch, tmp_path):
    """Exactly the ceiling is 'at the limit', not 'over it'."""
    iCeiling = dockerConnectionModule.I_MAX_CREDENTIAL_FILE_BYTES
    pathCredential = tmp_path / "boundaryLogin.json"
    pathCredential.write_bytes(b"y" * iCeiling)
    connection = fconnectionForContainer(monkeypatch, LocalProgramContainer())
    assert len(connection.fbaFetchCredentialFile(
        S_CONTAINER_ID, str(pathCredential))) == iCeiling


def testAbsentCredentialFileRaisesFileNotFound(monkeypatch, tmp_path):
    """An unreadable credential path is FileNotFoundError, naming it."""
    sMissing = str(tmp_path / "noSuchLogin.json")
    connection = fconnectionForContainer(monkeypatch, LocalProgramContainer())
    with pytest.raises(FileNotFoundError, match="noSuchLogin.json"):
        connection.fbaFetchCredentialFile(S_CONTAINER_ID, sMissing)


def testKeyringSecretIsStrippedAndTheSlotRidesAsALiteral(monkeypatch):
    """The slot name is embedded via repr; the value is whitespace-trimmed."""
    container = CannedAnswerContainer([(0, "tokenValue123\n", "")])
    connection = fconnectionForContainer(monkeypatch, container)
    sSecret = connection.fsFetchKeyringSecret(
        S_CONTAINER_ID, "zenodo_token'); import os #")
    assert sSecret == "tokenValue123"
    assert repr("zenodo_token'); import os #") in container.listPrograms[0]


def testKeyringFailureNamesTheSlotAndLeaksNothingFromTheContainer(
    monkeypatch,
):
    """A failed read raises LookupError naming only the slot."""
    container = CannedAnswerContainer(
        [(1, "partialSecretBytes", "Traceback: keyring value=hunter2")])
    connection = fconnectionForContainer(monkeypatch, container)
    with pytest.raises(LookupError) as errorInfo:
        connection.fsFetchKeyringSecret(S_CONTAINER_ID, "zenodo_token")
    sMessage = str(errorInfo.value)
    assert "'zenodo_token'" in sMessage
    assert "hunter2" not in sMessage
    assert "partialSecretBytes" not in sMessage


def testEmptyKeyringSlotAnswersEmptyString(monkeypatch):
    """No stored token is an answer, not an error."""
    connection = fconnectionForContainer(
        monkeypatch, CannedAnswerContainer([(0, "", "")]))
    assert connection.fsFetchKeyringSecret(S_CONTAINER_ID, "slotAlpha") == ""


# ---------------------------------------------------------------------
# Network-state probes (decoded, never raised)
# ---------------------------------------------------------------------


def testResolveHostnameDecodesTheAnswerAndMarksItAnswered(monkeypatch):
    """The program's JSON comes back with bAnswered True added."""
    dictProgramAnswer = {
        "sHostname": "example.invalid", "listAddresses": ["192.0.2.7"],
        "listFamilies": ["AF_INET"], "sError": "",
    }
    container = CannedAnswerContainer([(0, json.dumps(dictProgramAnswer), "")])
    connection = fconnectionForContainer(monkeypatch, container)
    dictAnswer = connection.fdictResolveHostnameInContainer(
        S_CONTAINER_ID, "example.invalid", fTimeoutSeconds=1.5)
    assert dictAnswer["bAnswered"] is True
    assert dictAnswer["listAddresses"] == ["192.0.2.7"]
    assert repr(["example.invalid", "1.5"]) in container.listPrograms[0]


def testProbeThatCouldNotRunIsUnansweredWithABoundedError(monkeypatch):
    """A failed exec is 'unassessed', with stderr cut to 400 chars."""
    sLongError = "  " + "E" * 1000 + "  "
    connection = fconnectionForContainer(
        monkeypatch, CannedAnswerContainer([(126, "", sLongError)]))
    dictAnswer = connection.fdictResolveHostnameInContainer(
        S_CONTAINER_ID, "example.invalid")
    assert dictAnswer == {"bAnswered": False, "sError": "E" * 400}


def testProbeWithUnreadableOutputIsUnanswered(monkeypatch):
    """Exit 0 with non-JSON output is not a resolver verdict."""
    connection = fconnectionForContainer(
        monkeypatch, CannedAnswerContainer([(0, "not json at all", "")]))
    dictAnswer = connection.fdictProbeTcpHandshakeInContainer(
        S_CONTAINER_ID, "example.invalid")
    assert dictAnswer == {
        "bAnswered": False, "sError": "unreadable probe output",
    }


def testTcpHandshakeArgumentsCarryPortSchemeAndProxy(monkeypatch):
    """Port, tls/plain, and proxy reach the program as string literals."""
    container = CannedAnswerContainer([(0, "{}", "")])
    connection = fconnectionForContainer(monkeypatch, container)
    connection.fdictProbeTcpHandshakeInContainer(
        S_CONTAINER_ID, "git.example.invalid", iPort=22,
        fTimeoutSeconds=3.0, bUseTls=False, sProxyHost="proxy.invalid",
        iProxyPort=3128,
    )
    assert repr([
        "git.example.invalid", "22", "3.0", "plain", "proxy.invalid", "3128",
    ]) in container.listPrograms[0]


def testTcpHandshakeReachesALocalListenerWithoutTls(monkeypatch):
    """A plain dial to a listening loopback port reports connected."""
    socketListener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        socketListener.bind(("127.0.0.1", 0))
        socketListener.listen(1)
        iPort = socketListener.getsockname()[1]
        connection = fconnectionForContainer(
            monkeypatch, LocalProgramContainer())
        dictAnswer = connection.fdictProbeTcpHandshakeInContainer(
            S_CONTAINER_ID, "127.0.0.1", iPort=iPort, bUseTls=False)
    finally:
        socketListener.close()
    assert dictAnswer["bAnswered"] is True
    assert dictAnswer["bConnected"] is True
    assert dictAnswer["sAddress"] == "127.0.0.1"
    assert dictAnswer["bTlsVerified"] is False
    assert dictAnswer["sError"] == ""


def testTcpHandshakeToAClosedPortIsAnAnswerNotAnException(monkeypatch):
    """A refused connection is reported in sError, still answered."""
    socketProbe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    socketProbe.bind(("127.0.0.1", 0))
    iClosedPort = socketProbe.getsockname()[1]
    socketProbe.close()
    connection = fconnectionForContainer(monkeypatch, LocalProgramContainer())
    dictAnswer = connection.fdictProbeTcpHandshakeInContainer(
        S_CONTAINER_ID, "127.0.0.1", iPort=iClosedPort,
        fTimeoutSeconds=1.0, bUseTls=False)
    assert dictAnswer["bAnswered"] is True
    assert dictAnswer["bConnected"] is False
    assert dictAnswer["sError"] != ""


# ---------------------------------------------------------------------
# Workspace ownership probe
# ---------------------------------------------------------------------


def fnPopulateOwnedTree(pathRoot):
    """Create three files under a subdirectory, all owned by this user."""
    pathSubdirectory = pathRoot / "stepAlpha"
    pathSubdirectory.mkdir()
    for sName in ("dataFile.csv", "notes.txt", "figure.pdf"):
        (pathSubdirectory / sName).write_text(sName)


def testForeignOwnedProbeFindsNothingWhenEveryPathIsExpected(
    monkeypatch, tmp_path,
):
    """Files owned by the expected uid appear in neither list."""
    fnPopulateOwnedTree(tmp_path)
    connection = fconnectionForContainer(monkeypatch, LocalProgramContainer())
    dictAnswer = connection.fdictFindForeignOwnedPaths(
        S_CONTAINER_ID, str(tmp_path), iExpectedUid=os.getuid())
    assert dictAnswer["bAnswered"] is True
    assert dictAnswer["listRootOwned"] == []
    assert dictAnswer["listOtherOwned"] == []
    assert dictAnswer["bTruncated"] is False


def testForeignOwnedProbeNamesOtherOwnersUpToItsCap(monkeypatch, tmp_path):
    """Unexpected owners are listed by the right key and capped."""
    fnPopulateOwnedTree(tmp_path)
    connection = fconnectionForContainer(monkeypatch, LocalProgramContainer())
    dictAnswer = connection.fdictFindForeignOwnedPaths(
        S_CONTAINER_ID, str(tmp_path), iExpectedUid=os.getuid() + 1,
        iMaxNamed=2,
    )
    sExpectedKey = "listRootOwned" if os.getuid() == 0 else "listOtherOwned"
    sOtherKey = "listOtherOwned" if os.getuid() == 0 else "listRootOwned"
    assert len(dictAnswer[sExpectedKey]) == 2
    assert dictAnswer[sOtherKey] == []
    assert all(
        sPath.startswith(str(tmp_path)) for sPath in dictAnswer[sExpectedKey]
    )


def testForeignOwnedProbeStopsAtTheVisitBound(monkeypatch, tmp_path):
    """A walk past iMaxVisits says it is truncated."""
    fnPopulateOwnedTree(tmp_path)
    connection = fconnectionForContainer(monkeypatch, LocalProgramContainer())
    dictAnswer = connection.fdictFindForeignOwnedPaths(
        S_CONTAINER_ID, str(tmp_path), iExpectedUid=os.getuid(),
        iMaxVisits=1,
    )
    assert dictAnswer["bTruncated"] is True


# ---------------------------------------------------------------------
# File, directory, and existence reads
# ---------------------------------------------------------------------


def testFetchFileOfAnAbsentPathRaisesFileNotFound(monkeypatch, tmp_path):
    """The small-file read names the missing path."""
    connection = fconnectionForContainer(monkeypatch, LocalProgramContainer())
    with pytest.raises(FileNotFoundError, match="absentFile.json"):
        connection.fbaFetchFile(
            S_CONTAINER_ID, str(tmp_path / "absentFile.json"))


def testDirectoryListingIsSortedAndHandlesSpacesInNames(
    monkeypatch, tmp_path,
):
    """Entries come back sorted; a space in a path is not a shell split."""
    pathDirectory = tmp_path / "Plot Output"
    pathDirectory.mkdir()
    for sName in ("zeta.csv", "alpha file.csv", "middle.csv"):
        (pathDirectory / sName).write_text("x")
    connection = fconnectionForContainer(monkeypatch, LocalProgramContainer())
    assert connection.flistDirectoryEntries(
        S_CONTAINER_ID, str(pathDirectory),
    ) == ["alpha file.csv", "middle.csv", "zeta.csv"]


def testEmptyDirectoryIsAnEmptyListNotAnError(monkeypatch, tmp_path):
    """Empty and missing are different answers."""
    pathEmpty = tmp_path / "emptyDirectory"
    pathEmpty.mkdir()
    connection = fconnectionForContainer(monkeypatch, LocalProgramContainer())
    assert connection.flistDirectoryEntries(
        S_CONTAINER_ID, str(pathEmpty)) == []
    with pytest.raises(FileNotFoundError, match="missingDirectory"):
        connection.flistDirectoryEntries(
            S_CONTAINER_ID, str(tmp_path / "missingDirectory"))


def testFileAndDirectoryProbesDistinguishTheTwoKinds(monkeypatch, tmp_path):
    """isFile and isDirectory each answer only for their own kind."""
    pathFile = tmp_path / "dataFile.csv"
    pathFile.write_text("1,2\n")
    connection = fconnectionForContainer(monkeypatch, LocalProgramContainer())
    assert connection.fbContainerPathIsFile(S_CONTAINER_ID, str(pathFile))
    assert not connection.fbContainerPathIsFile(
        S_CONTAINER_ID, str(tmp_path))
    assert connection.fbContainerPathIsDirectory(
        S_CONTAINER_ID, str(tmp_path))
    assert not connection.fbContainerPathIsDirectory(
        S_CONTAINER_ID, str(pathFile))


def testFailedPathProbeRaisesRatherThanAnsweringAbsent(monkeypatch):
    """A read that could not run is OSError, never False."""
    connection = fconnectionForContainer(
        monkeypatch, CannedAnswerContainer([(137, "", "killed\n")]))
    with pytest.raises(OSError, match="/workspace/stepAlpha.*killed"):
        connection.fbContainerPathIsFile(
            S_CONTAINER_ID, "/workspace/stepAlpha")


def testBatchedExistenceWithNoPathsRunsNothing(monkeypatch):
    """An empty request is answered without an exec."""
    container = CannedAnswerContainer([])
    connection = fconnectionForContainer(monkeypatch, container)
    assert connection.flistContainerPathsExist(S_CONTAINER_ID, []) == []
    assert connection.flistContainerDirectoriesExist(
        S_CONTAINER_ID, []) == []
    assert connection.fdictHashContainerRepoPaths(
        S_CONTAINER_ID, "/workspace", []) == {}
    assert connection.flistReadGitRepoStatuses(S_CONTAINER_ID, []) == []
    assert connection.fdictStatPathMtimes(S_CONTAINER_ID, []) == {}
    assert container.listPrograms == []


def testBatchedExistenceKeepsOrderAcrossTwoExecs(monkeypatch, tmp_path):
    """Answers realign onto the right paths when the list is split."""
    pathPresent = tmp_path / "presentFile.csv"
    pathPresent.write_text("x")
    sFiller = str(tmp_path / ("absentPathSegment" * 3))
    listPaths = [sFiller + str(iIndex) for iIndex in range(1100)]
    listPaths.append(str(pathPresent))
    listPaths.append(sFiller + "Tail")
    container = LocalProgramContainer()
    connection = fconnectionForContainer(monkeypatch, container)
    listAnswers = connection.flistContainerPathsExist(
        S_CONTAINER_ID, listPaths)
    assert len(container.listExecCalls) >= 2
    assert len(listAnswers) == len(listPaths)
    assert [iIndex for iIndex, bExists in enumerate(listAnswers)
            if bExists] == [1100]


def testShortBatchedAnswerIsRefusedRatherThanRealigned(monkeypatch):
    """Two answers for three paths raises instead of shifting."""
    connection = fconnectionForContainer(
        monkeypatch, CannedAnswerContainer([(0, "[true, false]", "")]))
    with pytest.raises(OSError, match="answered 2 of 3 paths"):
        connection.flistContainerPathsExist(
            S_CONTAINER_ID, ["/a", "/b", "/c"])


def testFailedBatchedExistenceRaises(monkeypatch):
    """A batched probe whose exec failed is OSError with stderr."""
    connection = fconnectionForContainer(
        monkeypatch, CannedAnswerContainer([(1, "", "no python3\n")]))
    with pytest.raises(OSError, match="no python3"):
        connection.flistContainerDirectoriesExist(S_CONTAINER_ID, ["/a"])


def testBatchedDirectoryProbeAnswersPerPath(monkeypatch, tmp_path):
    """A file, a directory and an absent path answer F, T, F."""
    pathFile = tmp_path / "dataFile.csv"
    pathFile.write_text("x")
    connection = fconnectionForContainer(monkeypatch, LocalProgramContainer())
    assert connection.flistContainerDirectoriesExist(
        S_CONTAINER_ID,
        [str(pathFile), str(tmp_path), str(tmp_path / "absent")],
    ) == [False, True, False]


# ---------------------------------------------------------------------
# Repository hashing and the poll snapshot
# ---------------------------------------------------------------------


def testRepoHashesMatchHashlibAndFlagAnEscape(monkeypatch, tmp_path):
    """Contained files hash to sha256; an absolute path escapes."""
    pathFile = tmp_path / "dataFile.csv"
    pathFile.write_bytes(b"alpha,beta\n1,2\n")
    connection = fconnectionForContainer(monkeypatch, LocalProgramContainer())
    dictHashes = connection.fdictHashContainerRepoPaths(
        S_CONTAINER_ID, str(tmp_path), ["dataFile.csv", "/etc/hosts"])
    assert dictHashes["dataFile.csv"]["sSha256"] == hashlib.sha256(
        b"alpha,beta\n1,2\n").hexdigest()
    assert dictHashes["/etc/hosts"]["bEscapesRoot"] is True
    assert dictHashes["/etc/hosts"]["sSha256"] is None


@pytest.mark.parametrize("tAnswer", [
    (1, "", "exec failed"),
    (0, "{not json", ""),
    (0, "[1, 2, 3]", ""),
])
def testUnusableRepoHashAnswerCollapsesToEmpty(monkeypatch, tAnswer):
    """A failed, unparseable, or non-object answer is {} — never partial."""
    connection = fconnectionForContainer(
        monkeypatch, CannedAnswerContainer([tAnswer]))
    assert connection.fdictHashContainerRepoPaths(
        S_CONTAINER_ID, "/workspace/repoAlpha", ["dataFile.csv"]) == {}


def testOneFailedRepoHashBatchDiscardsEarlierBatches(monkeypatch):
    """A second batch failing returns {} even though the first answered."""
    listRelativePaths = [
        "stepDirectory/" + "segment" * 7 + str(iIndex)
        for iIndex in range(1200)
    ]
    dictFirstBatch = {sPath: {"sSha256": "0" * 64} for sPath in
                      listRelativePaths[:10]}
    container = CannedAnswerContainer([
        (0, json.dumps(dictFirstBatch), ""),
        (1, "", "second batch failed"),
    ])
    connection = fconnectionForContainer(monkeypatch, container)
    assert connection.fdictHashContainerRepoPaths(
        S_CONTAINER_ID, "/workspace/repoAlpha", listRelativePaths) == {}
    assert len(container.listPrograms) == 2


def testRepoSnapshotOverTheArgumentBudgetIsRefusedBeforeAnyExec(
    monkeypatch,
):
    """One snapshot is never split; over budget raises naming both sizes."""
    container = CannedAnswerContainer([])
    connection = fconnectionForContainer(monkeypatch, container)
    listContentPaths = ["stepAlpha/" + "x" * 100 + str(i) for i in range(700)]
    with pytest.raises(ValueError, match="budget"):
        connection.ftReadRepoSnapshot(
            S_CONTAINER_ID, "/workspace/repoAlpha", listContentPaths,
            [], [], [],
        )
    assert container.listPrograms == []


def testRepoSnapshotRunsLocallyAndPrefixesEachGroup(monkeypatch, tmp_path):
    """Content, skip-text and hash groups all reach the program."""
    (tmp_path / "dataFile.csv").write_text("a,b\n")
    (tmp_path / "large.bin").write_bytes(b"\x00\x01")
    connection = fconnectionForContainer(monkeypatch, LocalProgramContainer())
    tExecResult = connection.ftReadRepoSnapshot(
        S_CONTAINER_ID, str(tmp_path), ["dataFile.csv", "large.bin"],
        ["large.bin"], ["dataFile.csv"], [],
    )
    dictSnapshot = json.loads(tExecResult.sStdout)
    assert dictSnapshot["dictFiles"]["dataFile.csv"]["sText"] == "a,b\n"
    assert dictSnapshot["dictFiles"]["large.bin"]["bIsFile"] is True
    assert dictSnapshot["dictFiles"]["large.bin"]["sText"] is None
    assert dictSnapshot["dictHashes"]["dataFile.csv"]["sSha256"] == (
        hashlib.sha256(b"a,b\n").hexdigest())


# ---------------------------------------------------------------------
# Git reads
# ---------------------------------------------------------------------


@pytest.fixture
def tRepositoryWithHistory(tmp_path):
    """Return (repository path, environment) with one commit and changes."""
    dictEnvironment = fdictHermeticGitEnvironment(tmp_path)
    pathRepository = tmp_path / "repoAlpha"
    pathRepository.mkdir()
    fnRunGit(pathRepository, ["init", "-q", "-b", "main"], dictEnvironment)
    (pathRepository / "trackedFile.txt").write_bytes(b"tracked content\n")
    (pathRepository / ".gitignore").write_text("ignoredOutput.log\n")
    fnRunGit(pathRepository, ["add", "."], dictEnvironment)
    fnRunGit(pathRepository, ["commit", "-q", "-m", "first"], dictEnvironment)
    (pathRepository / "untrackedFile.csv").write_bytes(b"12345")
    (pathRepository / "ignoredOutput.log").write_bytes(b"ignored")
    return pathRepository, dictEnvironment


def testGitStatusReportsBranchPorcelainAndAMissingRepository(
    monkeypatch, tmp_path, tRepositoryWithHistory,
):
    """A real repo reports its branch and changes; a plain dir is missing."""
    pathRepository, dictEnvironment = tRepositoryWithHistory
    pathPlain = tmp_path / "notARepository"
    pathPlain.mkdir()
    connection = fconnectionForContainer(
        monkeypatch, LocalProgramContainer(dictEnvironment))
    listStatuses = connection.flistReadGitRepoStatuses(
        S_CONTAINER_ID, [str(pathRepository), str(pathPlain)])
    assert listStatuses[0]["bMissing"] is False
    assert listStatuses[0]["sBranch"].strip() == "main"
    assert "?? untrackedFile.csv" in listStatuses[0]["sPorcelain"]
    assert listStatuses[0]["sUrl"] == ""
    assert listStatuses[1] == {"sPath": str(pathPlain), "bMissing": True}


@pytest.mark.parametrize("sMethodName, sWhat", [
    ("flistReadGitRepoStatuses", "repository status"),
    ("fdictFetchWorktreeIdentities", "worktree identit"),
])
def testGitReadsRaiseOnFailureAndOnGarbage(monkeypatch, sMethodName, sWhat):
    """A failed exec and an unparseable answer are both OSError."""
    connection = fconnectionForContainer(monkeypatch, CannedAnswerContainer([
        (2, "", "fatal: bad"), (0, "<<garbage>>", ""),
    ]))
    fnMethod = getattr(connection, sMethodName)
    objArgument = (["/workspace/repoAlpha"]
                   if sMethodName.startswith("flist")
                   else "/workspace/repoAlpha")
    with pytest.raises(OSError, match="fatal: bad"):
        fnMethod(S_CONTAINER_ID, objArgument)
    with pytest.raises(OSError, match="unparseable"):
        fnMethod(S_CONTAINER_ID, objArgument)


def testTrackedIdentitiesMatchGitHashObject(
    monkeypatch, tRepositoryWithHistory,
):
    """The in-container blob identity equals git's own for raw bytes."""
    pathRepository, dictEnvironment = tRepositoryWithHistory
    sExpectedBlob = subprocess.run(
        ["git", "-C", str(pathRepository), "hash-object", "--no-filters",
         "trackedFile.txt"],
        capture_output=True, text=True, check=True, env=dictEnvironment,
    ).stdout.strip()
    connection = fconnectionForContainer(
        monkeypatch, LocalProgramContainer(dictEnvironment))
    dictObservation = connection.fdictFetchTrackedIdentities(
        S_CONTAINER_ID, str(pathRepository))
    assert dictObservation["bSuccess"] is True
    dictEntry = dictObservation["dictEntries"]["trackedFile.txt"]
    assert dictEntry["sType"] == "file"
    assert dictEntry["sIdentity"] == sExpectedBlob
    assert "untrackedFile.csv" not in dictObservation["dictEntries"]
    assert len(dictObservation["sHeadSha"]) == 40


def testTrackedIdentitiesRefuseADirectoryThatIsNotARepository(
    monkeypatch, tmp_path,
):
    """bSuccess False is a refusal to observe, not a quiet repository."""
    dictEnvironment = fdictHermeticGitEnvironment(tmp_path)
    pathPlain = tmp_path / "plainDirectory"
    pathPlain.mkdir()
    connection = fconnectionForContainer(
        monkeypatch, LocalProgramContainer(dictEnvironment))
    dictObservation = connection.fdictFetchTrackedIdentities(
        S_CONTAINER_ID, str(pathPlain))
    assert dictObservation["bSuccess"] is False
    assert dictObservation["dictEntries"] == {}


def testTrackedIdentityReadFailuresRaiseNamingTheRead(monkeypatch):
    """Failed exec and bad JSON both raise OSError naming the read."""
    connection = fconnectionForContainer(monkeypatch, CannedAnswerContainer([
        (1, "", "exec refused"), (0, "{bad", ""),
    ]))
    with pytest.raises(OSError, match="tracked identities.*exec refused"):
        connection.fdictFetchTrackedIdentities(S_CONTAINER_ID, "/repo")
    with pytest.raises(OSError, match="tracked identities read answered"):
        connection.fdictFetchTrackedIdentities(S_CONTAINER_ID, "/repo")


def testUntrackedInventoryListsUntrackedAndIgnoredWithSizes(
    monkeypatch, tRepositoryWithHistory,
):
    """Names and sizes, each tagged with why it was left behind."""
    pathRepository, dictEnvironment = tRepositoryWithHistory
    connection = fconnectionForContainer(
        monkeypatch, LocalProgramContainer(dictEnvironment))
    dictInventory = connection.fdictFetchUntrackedInventory(
        S_CONTAINER_ID, str(pathRepository))
    assert dictInventory["bSuccess"] is True
    assert dictInventory["bComplete"] is True
    assert sorted(map(tuple, dictInventory["listEntries"])) == [
        ("ignoredOutput.log", "ignored", 7),
        ("untrackedFile.csv", "untracked", 5),
    ]


# ---------------------------------------------------------------------
# Poll reads: mtimes, sha256, filesystem usage, repository weight
# ---------------------------------------------------------------------


def testStatMtimesSkipsAbsentPaths(monkeypatch, tmp_path):
    """Only existing paths are keyed; mtimes are integer strings."""
    pathFile = tmp_path / "dataFile.csv"
    pathFile.write_text("x")
    os.utime(pathFile, (1700000000.9, 1700000000.9))
    connection = fconnectionForContainer(monkeypatch, LocalProgramContainer())
    assert connection.fdictStatPathMtimes(
        S_CONTAINER_ID, [str(pathFile), str(tmp_path / "absent.csv")],
    ) == {str(pathFile): "1700000000"}


def testStatMtimesRaiseOnFailedOrGarbledRead(monkeypatch):
    """The poll cannot mistake a failed read for 'nothing exists'."""
    connection = fconnectionForContainer(monkeypatch, CannedAnswerContainer([
        (1, "", "stat failed"), (0, "][", ""),
    ]))
    with pytest.raises(OSError, match="stat failed"):
        connection.fdictStatPathMtimes(S_CONTAINER_ID, ["/a"])
    with pytest.raises(OSError, match="unparseable"):
        connection.fdictStatPathMtimes(S_CONTAINER_ID, ["/a"])


def testFileSha256MatchesHashlibAndIsEmptyForAnAbsentFile(
    monkeypatch, tmp_path,
):
    """An unreadable file is '' (cannot compare), not an error."""
    pathFile = tmp_path / "workflow.json"
    pathFile.write_bytes(b'{"listSteps": []}')
    connection = fconnectionForContainer(monkeypatch, LocalProgramContainer())
    assert connection.fsHashContainerFileSha256(
        S_CONTAINER_ID, str(pathFile),
    ) == hashlib.sha256(b'{"listSteps": []}').hexdigest()
    assert connection.fsHashContainerFileSha256(
        S_CONTAINER_ID, str(tmp_path / "absent.json")) == ""


def testFileSha256RaisesWhenTheReadItselfFails(monkeypatch):
    """A failed exec is OSError naming the path, never ''."""
    connection = fconnectionForContainer(
        monkeypatch, CannedAnswerContainer([(1, "", "denied")]))
    with pytest.raises(OSError, match="workflow.json.*denied"):
        connection.fsHashContainerFileSha256(
            S_CONTAINER_ID, "/workspace/workflow.json")


def testFilesystemUsageMatchesStatvfs(monkeypatch, tmp_path):
    """Total and free agree with statvfs computed on the same path."""
    connection = fconnectionForContainer(monkeypatch, LocalProgramContainer())
    dictUsage = connection.fdictReadFilesystemUsage(
        S_CONTAINER_ID, str(tmp_path))
    statFilesystem = os.statvfs(str(tmp_path))
    assert dictUsage["iTotalBytes"] == (
        statFilesystem.f_blocks * statFilesystem.f_frsize)
    assert 0 <= dictUsage["iUsedBytes"] <= dictUsage["iTotalBytes"]
    assert dictUsage["iFreeBytes"] <= dictUsage["iTotalBytes"]


def testFilesystemUsageOfAnAbsentPathRaisesFileNotFound(
    monkeypatch, tmp_path,
):
    """statvfs on a missing path fails the read, naming the path."""
    connection = fconnectionForContainer(monkeypatch, LocalProgramContainer())
    with pytest.raises(FileNotFoundError, match="noSuchMount"):
        connection.fdictReadFilesystemUsage(
            S_CONTAINER_ID, str(tmp_path / "noSuchMount"))


def testRepositoryWeightCountsContentAndPrunesTheGitDirectory(
    monkeypatch, tmp_path,
):
    """A .git directory and __pycache__ are not weighed; content is."""
    pathRepository = tmp_path / "repoAlpha"
    (pathRepository / ".git" / "objects").mkdir(parents=True)
    (pathRepository / ".git" / "objects" / "pack").write_bytes(b"p" * 5000)
    (pathRepository / "__pycache__").mkdir()
    (pathRepository / "__pycache__" / "module.pyc").write_bytes(b"c" * 900)
    (pathRepository / "dataFile.csv").write_bytes(b"d" * 300)
    (pathRepository / "script.py").write_bytes(b"s" * 20)
    os.symlink("dataFile.csv", str(pathRepository / "linkToData"))
    os.symlink("/etc/hosts", str(pathRepository / "escapingLink"))
    connection = fconnectionForContainer(monkeypatch, LocalProgramContainer())
    dictWeight = connection.fdictWeighRepository(
        S_CONTAINER_ID, str(pathRepository))
    assert dictWeight["iFileCount"] == 4
    assert dictWeight["iTotalBytes"] == 320
    assert dictWeight["bTruncated"] is False
    assert dictWeight["listLargestFiles"][0] == {
        "sPath": "dataFile.csv", "iSizeBytes": 300,
    }
    assert dictWeight["listEscapingSymlinks"] == [
        {"sPath": "escapingLink", "sTarget": "/etc/hosts"},
    ]


def testRepositoryWeightOfAFailedReadRaisesFileNotFound(monkeypatch):
    """A weight probe that could not run names the repository."""
    connection = fconnectionForContainer(
        monkeypatch, CannedAnswerContainer([(1, "", "boom")]))
    with pytest.raises(FileNotFoundError, match="/workspace/repoAlpha"):
        connection.fdictWeighRepository(
            S_CONTAINER_ID, "/workspace/repoAlpha")


def testTypedReadAddressesTheContainerByIdNotName(monkeypatch):
    """The fake daemon knows only the id; asking by name fails."""
    connection = fconnectionForContainer(
        monkeypatch, CannedAnswerContainer([(0, "1", "")]))
    with pytest.raises(FakeContainerNotFound):
        connection.fbContainerPathIsFile(S_CONTAINER_NAME, "/workspace")
    assert connection.fbContainerPathIsFile(S_CONTAINER_ID, "/workspace")
