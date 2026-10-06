"""The file routes' refusal and failure paths, driven over the served app.

Every request below goes through ``pipelineServer.fappCreateApplication``
with a real per-browser credential and the lease ``/api/connect``
minted, and the Docker double calls the SAME admission gates the real
connection calls (``tests/testCarrierMigratedRoutes``). Only the
container itself is simulated. The container NAME (``test-container``)
and the container ID (``draftcontainer``) are distinct, so a route that
looked a project up by the wrong key would fail here rather than pass.

What is pinned is what a researcher meets when something is wrong: a
stale editor base, a malformed upload, an unlistable directory, a pull
aimed outside their home, a seed of a project that no longer exists.
Each refusal is asserted by status AND by what the body says, and no
body may carry a host path outside the project.
"""

import base64
import hashlib
import json
import os

import pytest

from tests.testCarrierMigratedRoutes import (
    DockerDoubleThatCallsTheRealGates,
    _tConnectGatedClient,
)
from tests.testDraftRoutes import S_CONTAINER_ID
from vaibify.config import mutationAdmission, registryManager
from vaibify.gui.routes import fileRoutes


S_CONTAINER_NAME = "test-container"
S_REPO = "/workspace"


class _GatedDockerWithFileSystem(DockerDoubleThatCallsTheRealGates):
    """The gated double, answering file probes from its content store."""

    def __init__(self):
        super().__init__()
        self.setDirectoryPaths = set()
        self.setOversizedPaths = set()
        self.sStreamFailure = ""
        self.sWriteFailure = ""
        self.listTreeWrites = []
        self.listTreeWriteKeywords = []
        self.errorTreeWrite = None

    def fbContainerPathIsFile(self, sContainerId, sPath):
        DockerDoubleThatCallsTheRealGates.fbContainerPathIsFile(
            self, sContainerId, sPath,
        )
        return sPath in self._dictFiles

    def fbaFetchFile(self, sContainerId, sPath, iMaxBytes=None):
        if sPath in self.setOversizedPaths:
            raise ValueError("file exceeds the fetch cap")
        return DockerDoubleThatCallsTheRealGates.fbaFetchFile(
            self, sContainerId, sPath, iMaxBytes,
        )

    def flistDirectoryEntries(self, sContainerId, sPath):
        if sPath in self.setDirectoryPaths:
            return ["entryAlpha", "subdirectoryBravo"]
        return DockerDoubleThatCallsTheRealGates.flistDirectoryEntries(
            self, sContainerId, sPath,
        )

    def flistContainerDirectoriesExist(self, sContainerId, listPaths):
        return [
            sPath.endswith("subdirectoryBravo") for sPath in listPaths
        ]

    def fiterStreamFile(
        self, sContainerId, sPath, iChunkSizeBytes=1048576,
    ):
        if self.sStreamFailure:
            raise RuntimeError(self.sStreamFailure)
        if sPath not in self._dictFiles:
            raise FileNotFoundError("No such file: " + sPath)
        baContent = self._dictFiles[sPath]
        for iStart in range(0, len(baContent), 4):
            yield baContent[iStart:iStart + 4]

    def fiterReadFileConfined(
        self, sContainerId, sPath, sAuthorizedRoot=None,
    ):
        """The download's confined read, answering as the stream does."""
        yield from self.fiterStreamFile(sContainerId, sPath)

    def fnWriteFile(
        self, sContainerId, sPath, baContent,
        iMode=None, iUid=None, iGid=None,
        sAuthorizedRoot=None, tForbiddenNames=(),
    ):
        if self.sWriteFailure:
            mutationAdmission.fnAssertContainerWriteAdmitted(
                sContainerId, "fnWriteFileViaTar",
            )
            raise OSError(self.sWriteFailure)
        return DockerDoubleThatCallsTheRealGates.fnWriteFile(
            self, sContainerId, sPath, baContent,
            iMode=iMode, iUid=iUid, iGid=iGid,
        )

    def fnWriteTreeViaTar(
        self, sContainerId, sDestinationDirectory, listHostPaths,
        iUid=None, iGid=None, sArchiveName=None,
        sAuthorizedRoot=None, tForbiddenNames=(),
        bCreateDestination=False,
    ):
        mutationAdmission.fnAssertContainerWriteAdmitted(
            sContainerId, "fnWriteTreeViaTar",
        )
        if self.sWriteFailure:
            raise OSError(self.sWriteFailure)
        if self.errorTreeWrite is not None:
            raise self.errorTreeWrite
        self.listTreeWrites.append(
            (sDestinationDirectory, list(listHostPaths)),
        )
        self.listTreeWriteKeywords.append({
            "sAuthorizedRoot": sAuthorizedRoot,
            "bCreateDestination": bCreateDestination,
        })


@pytest.fixture(autouse=True)
def fixtureIsolatedRegistryAndHome(monkeypatch, tmp_path):
    """An empty registry, and a HOME that is a scratch directory."""
    sRegistryDirectory = str(tmp_path / "registryHome")
    monkeypatch.setattr(
        registryManager, "_S_REGISTRY_DIRECTORY", sRegistryDirectory,
    )
    monkeypatch.setattr(
        registryManager, "_S_REGISTRY_PATH",
        os.path.join(sRegistryDirectory, "registry.json"),
    )
    monkeypatch.setattr(
        registryManager, "_S_LOCK_PATH",
        os.path.join(sRegistryDirectory, "registry.lock"),
    )
    sHome = str(tmp_path / "researcherHome")
    os.makedirs(sHome)
    monkeypatch.setenv("HOME", sHome)
    return sHome


@pytest.fixture
def tclientFiles():
    """A connected client over the file-system-aware gated double.

    The typed-probe ledger is cleared after connect for the same reason
    the admission ledger is: connecting legitimately probes the
    workflow file, and a route that asked nothing must read as empty.
    """
    client, connectionDocker = _tConnectGatedClient(
        _GatedDockerWithFileSystem(),
    )
    connectionDocker.listTypedPathProbes.clear()
    return client, connectionDocker


def _fsSha256(baContent):
    return hashlib.sha256(baContent).hexdigest()


def _fnRegisterProject(sDirectory, sMode="container"):
    """Write one registry entry keyed by the container NAME."""
    os.makedirs(registryManager._S_REGISTRY_DIRECTORY, exist_ok=True)
    with open(registryManager._S_REGISTRY_PATH, "w") as fileOut:
        json.dump({"listProjects": [{
            "sName": S_CONTAINER_NAME,
            "sDirectory": sDirectory,
            "sMode": sMode,
        }]}, fileOut)


def _fsDetailText(responseHttp):
    return json.dumps(responseHttp.json())


# ── Editor save: the base-hash conflict check ──


def testASaveOverAFileChangedSinceEditBeganIsAConflict(
    tclientFiles,
):
    """The response carries what is on disk NOW, for a three-way diff."""
    client, connectionDocker = tclientFiles
    sPath = S_REPO + "/src/analysis.py"
    connectionDocker._dictFiles[sPath] = b"x = 'theirs'\n"
    responseHttp = client.put(
        f"/api/file/{S_CONTAINER_ID}/src/analysis.py",
        json={"sContent": "x = 'mine'\n",
              "sBaseHash": _fsSha256(b"x = 'original'\n")},
    )
    assert responseHttp.status_code == 409
    dictDetail = responseHttp.json()["detail"]
    assert dictDetail["sMessage"] == "File changed on disk since edit started"
    assert dictDetail["sCurrentHash"] == _fsSha256(b"x = 'theirs'\n")
    assert dictDetail["sCurrentContent"] == "x = 'theirs'\n"
    assert connectionDocker._dictFiles[sPath] == b"x = 'theirs'\n"


def testAConflictOverBinaryContentCarriesNoText(tclientFiles):
    """Bytes that are not UTF-8 cannot be diffed as text; say nothing."""
    client, connectionDocker = tclientFiles
    connectionDocker._dictFiles[S_REPO + "/src/blob.py"] = b"\xff\xfe\x00"
    responseHttp = client.put(
        f"/api/file/{S_CONTAINER_ID}/src/blob.py",
        json={"sContent": "text\n", "sBaseHash": _fsSha256(b"other")},
    )
    assert responseHttp.status_code == 409
    assert responseHttp.json()["detail"]["sCurrentContent"] == ""


def testANewFileMatchesTheEmptyBaseAndIsWritten(tclientFiles):
    """Absence trivially matches an empty base; a fresh save is no conflict."""
    client, connectionDocker = tclientFiles
    responseHttp = client.put(
        f"/api/file/{S_CONTAINER_ID}/src/fresh.py",
        json={"sContent": "y = 1\n", "sBaseHash": _fsSha256(b"")},
    )
    assert responseHttp.status_code == 200, responseHttp.text
    assert responseHttp.json()["sPath"] == S_REPO + "/src/fresh.py"
    assert connectionDocker._dictFiles[S_REPO + "/src/fresh.py"] == (
        b"y = 1\n"
    )


def testAFileTooLargeToFetchSkipsTheCheckRatherThanBlocking(
    tclientFiles,
):
    client, connectionDocker = tclientFiles
    sPath = S_REPO + "/src/huge.py"
    connectionDocker._dictFiles[sPath] = b"old"
    connectionDocker.setOversizedPaths.add(sPath)
    responseHttp = client.put(
        f"/api/file/{S_CONTAINER_ID}/src/huge.py",
        json={"sContent": "new", "sBaseHash": _fsSha256(b"stale base")},
    )
    assert responseHttp.status_code == 200, responseHttp.text
    assert connectionDocker._dictFiles[sPath] == b"new"


def testAFailedWriteIsReportedInTheResearchersWords(tclientFiles):
    """Docker's raw text is translated, not forwarded."""
    client, connectionDocker = tclientFiles
    connectionDocker.sWriteFailure = (
        "write /var/lib/docker/overlay2/abc: no space left on device"
    )
    responseHttp = client.put(
        f"/api/file/{S_CONTAINER_ID}/src/full.py",
        json={"sContent": "z = 3\n"},
    )
    assert responseHttp.status_code == 500
    assert responseHttp.json()["detail"] == (
        "Write failed: Docker disk full. Run: docker image prune -f"
    )
    assert "/var/lib/docker" not in responseHttp.text


def testASaveWithNoProjectRepositoryIsRefused(tclientFiles):
    client, _connectionDocker = tclientFiles
    dictCtx = client.app.state.dictRouteContext
    dictCtx["workflows"][S_CONTAINER_ID]["sProjectRepoPath"] = ""
    responseHttp = client.put(
        f"/api/file/{S_CONTAINER_ID}/src/any.py",
        json={"sContent": "a\n"},
    )
    assert responseHttp.status_code == 400
    assert responseHttp.json()["detail"] == (
        "Active project has no repository path"
    )


def testASaveWithNoProjectOpenSaysSoRatherThanBlamingDocker(
    tclientFiles,
):
    client, _connectionDocker = tclientFiles
    client.app.state.dictRouteContext["workflows"].pop(S_CONTAINER_ID)
    responseHttp = client.put(
        f"/api/file/{S_CONTAINER_ID}/src/any.py",
        json={"sContent": "a\n"},
    )
    assert responseHttp.status_code == 404
    assert "No project is open" in _fsDetailText(responseHttp)


# ── Upload ──


def testAnUploadThatIsNotBase64IsRefusedBeforeAnyWrite(
    tclientFiles,
):
    """A malformed body is a 400, never a worker failure that poisons."""
    client, connectionDocker = tclientFiles
    responseHttp = client.post(
        f"/api/files/{S_CONTAINER_ID}/upload",
        json={"sFilename": "table.csv", "sDestination": S_REPO,
              "sContentBase64": "a"},
    )
    assert responseHttp.status_code == 400
    assert "not valid base64" in responseHttp.json()["detail"]
    assert S_REPO + "/table.csv" not in connectionDocker._dictFiles
    assert connectionDocker.listAdmittedPrimitives == []


def testAnUploadKeepsOnlyTheFilenameItWasGiven(tclientFiles):
    """Directory components in the filename cannot steer the write."""
    client, connectionDocker = tclientFiles
    responseHttp = client.post(
        f"/api/files/{S_CONTAINER_ID}/upload",
        json={"sFilename": "../../escape/table.csv",
              "sDestination": S_REPO + "/data",
              "sContentBase64": base64.b64encode(b"a,b\n").decode()},
    )
    assert responseHttp.status_code == 200, responseHttp.text
    assert responseHttp.json()["sPath"] == S_REPO + "/data/table.csv"
    assert connectionDocker._dictFiles[S_REPO + "/data/table.csv"] == (
        b"a,b\n"
    )


def testAnUploadOutsideTheProjectIsRefused(tclientFiles):
    client, connectionDocker = tclientFiles
    responseHttp = client.post(
        f"/api/files/{S_CONTAINER_ID}/upload",
        json={"sFilename": "profile", "sDestination": "/etc",
              "sContentBase64": base64.b64encode(b"x").decode()},
    )
    assert responseHttp.status_code == 403
    assert "/etc/profile" not in connectionDocker._dictFiles


def testAFailedUploadWriteIsAServerErrorNamingTheCause(
    tclientFiles,
):
    client, connectionDocker = tclientFiles
    connectionDocker.sWriteFailure = "container stopped mid-write"
    responseHttp = client.post(
        f"/api/files/{S_CONTAINER_ID}/upload",
        json={"sFilename": "table.csv", "sDestination": S_REPO,
              "sContentBase64": base64.b64encode(b"a").decode()},
    )
    assert responseHttp.status_code == 500
    assert "container stopped mid-write" in responseHttp.json()["detail"]


S_REFUSAL_TEXT = (
    "The container has an unfinished operation. Run `vaibify reconcile` "
    "to settle it before changing anything else."
)


def _fnMakeEveryWriteRefuse(monkeypatch, connectionDocker):
    def fnRefuseTheWrite(*listArguments, **dictKeywords):
        raise mutationAdmission.MutationNotAdmittedError(S_REFUSAL_TEXT)

    monkeypatch.setattr(connectionDocker, "fnWriteFile", fnRefuseTheWrite)


@pytest.mark.parametrize("sMethod, sUrlTemplate, dictBody", [
    ("put", "/api/file/{sId}/src/full.py", {"sContent": "z = 3\n"}),
    ("post", "/api/files/{sId}/upload", {
        "sFilename": "table.csv", "sDestination": S_REPO,
        "sContentBase64": base64.b64encode(b"a").decode(),
    }),
    ("put", "/api/draft/{sId}/src/full.py", {
        "sContent": "z = 3\n", "sWorkdir": S_REPO, "sBaseHash": "",
    }),
])
def testARefusedWriteAnswersWithTheRefusalsOwnText(
    tclientFiles, monkeypatch, sMethod, sUrlTemplate, dictBody,
):
    """A control-plane refusal names `vaibify reconcile`, not "failed"."""
    client, connectionDocker = tclientFiles
    _fnMakeEveryWriteRefuse(monkeypatch, connectionDocker)
    responseHttp = getattr(client, sMethod)(
        sUrlTemplate.format(sId=S_CONTAINER_ID), json=dictBody,
    )
    assert responseHttp.status_code == 500
    assert S_REFUSAL_TEXT in responseHttp.text
    assert "Write failed" not in responseHttp.text
    assert "write failed" not in responseHttp.text.lower()


# ── Download ──


def testADownloadStreamsEveryChunkAsAnAttachment(tclientFiles):
    client, connectionDocker = tclientFiles
    connectionDocker._dictFiles[S_REPO + "/data/result.csv"] = (
        b"column,value\n1,2\n"
    )
    responseHttp = client.get(
        f"/api/files/{S_CONTAINER_ID}/download/data/result.csv",
    )
    assert responseHttp.status_code == 200
    assert responseHttp.content == b"column,value\n1,2\n"
    assert responseHttp.headers["content-disposition"] == (
        'attachment; filename="result.csv"; '
        "filename*=UTF-8''result.csv"
    )


def testAnEmptyFileDownloadsAsAnEmptyBody(tclientFiles):
    client, connectionDocker = tclientFiles
    connectionDocker._dictFiles[S_REPO + "/data/empty.csv"] = b""
    responseHttp = client.get(
        f"/api/files/{S_CONTAINER_ID}/download/data/empty.csv",
    )
    assert responseHttp.status_code == 200
    assert responseHttp.content == b""


def testADownloadThatCannotOpenFailsBeforeTheStatusIsSent(
    tclientFiles,
):
    """A 200 with a truncated body would read as a complete file."""
    client, connectionDocker = tclientFiles
    connectionDocker.sStreamFailure = "No such container: draftcontainer"
    responseHttp = client.get(
        f"/api/files/{S_CONTAINER_ID}/download/data/result.csv",
    )
    assert responseHttp.status_code == 500
    sDetail = responseHttp.json()["detail"]
    assert "result.csv" in sDetail
    assert "Container not found. It may have stopped." in sDetail
    assert "No such container" not in sDetail


@pytest.mark.falsification
def testADownloadOutsideTheWorkspaceIsRefused(tclientFiles):
    """A download of a path outside the workspace is refused.

    Kills: downloadRoutes download route (the streaming handler): the call
    `fsValidatePathWithinRoot(sAbsPath, sProjectRoot)` deleted.
    """
    client, _connectionDocker = tclientFiles
    responseHttp = client.get(
        f"/api/files/{S_CONTAINER_ID}/download//etc/passwd",
    )
    assert responseHttp.status_code == 403


# ── Directory listing and the existence batch ──


def testAnUnlistableDirectoryIsA404NotAnEmptyListing(
    tclientFiles,
):
    """"Empty directory" would be a claim made because the read failed."""
    client, _connectionDocker = tclientFiles
    responseHttp = client.get(
        f"/api/files/{S_CONTAINER_ID}/workspace/missingDirectory",
    )
    assert responseHttp.status_code == 404
    assert responseHttp.json()["detail"] == (
        "Cannot list directory: /workspace/missingDirectory"
    )


def testAListableDirectoryReturnsItsEntries(tclientFiles):
    client, connectionDocker = tclientFiles
    connectionDocker.setDirectoryPaths.add("/workspace/data")
    responseHttp = client.get(f"/api/files/{S_CONTAINER_ID}/workspace/data")
    assert responseHttp.status_code == 200
    assert responseHttp.json() == [
        {"sName": "entryAlpha", "sPath": "/workspace/data/entryAlpha",
         "bIsDirectory": False},
        {"sName": "subdirectoryBravo",
         "sPath": "/workspace/data/subdirectoryBravo",
         "bIsDirectory": True},
    ]


def testTheExistenceBatchAnswersUnderTheCallersOwnKeys(
    tclientFiles,
):
    """Relative and absolute spellings resolve, and answer positionally."""
    client, connectionDocker = tclientFiles
    connectionDocker.setExistingPaths.add(S_REPO + "/data/present.csv")
    responseHttp = client.post(
        f"/api/files/{S_CONTAINER_ID}/exist",
        json={"saRelativePaths": [
            "data/present.csv", S_REPO + "/data/absent.csv",
        ]},
    )
    assert responseHttp.status_code == 200, responseHttp.text
    assert responseHttp.json() == {"dictExists": {
        "data/present.csv": True, S_REPO + "/data/absent.csv": False,
    }}


def testAnEmptyExistenceBatchAsksTheContainerNothing(tclientFiles):
    client, connectionDocker = tclientFiles
    responseHttp = client.post(
        f"/api/files/{S_CONTAINER_ID}/exist", json={"saRelativePaths": []},
    )
    assert responseHttp.status_code == 200
    assert responseHttp.json() == {"dictExists": {}}
    assert connectionDocker.listTypedPathProbes == []


def testAnOversizedExistenceBatchIsRefusedWithItsCap(tclientFiles):
    client, connectionDocker = tclientFiles
    responseHttp = client.post(
        f"/api/files/{S_CONTAINER_ID}/exist",
        json={"saRelativePaths": ["file.txt"] * (
            fileRoutes.I_MAX_EXISTENCE_BATCH + 1
        )},
    )
    assert responseHttp.status_code == 400
    assert responseHttp.json()["detail"] == "Batch capped at 1000 paths"
    assert connectionDocker.listTypedPathProbes == []


def testAnExistenceProbeThatEscapesTheWorkspaceIsRefused(
    tclientFiles,
):
    client, connectionDocker = tclientFiles
    responseHttp = client.post(
        f"/api/files/{S_CONTAINER_ID}/exist",
        json={"saRelativePaths": ["../../etc/shadow"]},
    )
    assert responseHttp.status_code == 403
    assert connectionDocker.listTypedPathProbes == []


# ── Pull to the host ──


def testAPullIntoADirectoryLandsUnderTheSourceBasename(
    tclientFiles, fixtureIsolatedRegistryAndHome,
):
    """The answer is where the file landed, not the directory asked for."""
    client, connectionDocker = tclientFiles
    connectionDocker._dictFiles[S_REPO + "/data/result.csv"] = b"1,2\n"
    sDestination = os.path.join(fixtureIsolatedRegistryAndHome, "Downloads")
    os.makedirs(sDestination)
    responseHttp = client.post(
        f"/api/files/{S_CONTAINER_ID}/pull",
        json={"sContainerPath": S_REPO + "/data/result.csv",
              "sHostDestination": sDestination},
    )
    assert responseHttp.status_code == 200, responseHttp.text
    sLanded = responseHttp.json()["sHostPath"]
    assert sLanded == os.path.join(
        os.path.realpath(sDestination), "result.csv",
    )
    with open(sLanded, "rb") as fileIn:
        assert fileIn.read() == b"1,2\n"


def testAPullOfADirectoryIsRefusedRatherThanHalfDone(
    tclientFiles, fixtureIsolatedRegistryAndHome,
):
    """A single-file stream would otherwise pull one file and say success."""
    client, connectionDocker = tclientFiles
    connectionDocker.setDirectoryPaths.add(S_REPO + "/data")
    sDestination = os.path.join(fixtureIsolatedRegistryAndHome, "copy.csv")
    responseHttp = client.post(
        f"/api/files/{S_CONTAINER_ID}/pull",
        json={"sContainerPath": S_REPO + "/data",
              "sHostDestination": sDestination},
    )
    assert responseHttp.status_code == 500
    assert "is a directory" in responseHttp.json()["detail"]
    assert not os.path.exists(sDestination)


def testAPullOutsideTheResearchersHomeIsRefused(
    tclientFiles, tmp_path,
):
    client, connectionDocker = tclientFiles
    connectionDocker._dictFiles[S_REPO + "/data/result.csv"] = b"1"
    sOutside = str(tmp_path / "elsewhere")
    responseHttp = client.post(
        f"/api/files/{S_CONTAINER_ID}/pull",
        json={"sContainerPath": S_REPO + "/data/result.csv",
              "sHostDestination": sOutside},
    )
    assert responseHttp.status_code == 403
    assert responseHttp.json()["detail"] == (
        "Destination outside home directory"
    )
    assert not os.path.exists(sOutside)


@pytest.mark.falsification
def testAPullOfAPathOutsideTheWorkspaceIsRefused(
    tclientFiles, fixtureIsolatedRegistryAndHome,
):
    """A pull of a path outside the workspace is refused.

    Kills: fileRoutes.fdictHandlePullFile: the call
    `fsValidatePathWithinRoot(request.sContainerPath, <root>)` deleted.
    """
    client, _connectionDocker = tclientFiles
    responseHttp = client.post(
        f"/api/files/{S_CONTAINER_ID}/pull",
        json={"sContainerPath": "/etc/shadow",
              "sHostDestination": fixtureIsolatedRegistryAndHome},
    )
    assert responseHttp.status_code == 403
    assert not os.path.exists(
        os.path.join(fixtureIsolatedRegistryAndHome, "shadow"),
    )


# ── Seeding a converted container's workspace ──


def _fsBuildHostProject(tmp_path):
    sDirectory = str(tmp_path / "hostProjectBravo")
    os.makedirs(os.path.join(sDirectory, ".git"))
    os.makedirs(os.path.join(sDirectory, "scripts"))
    with open(os.path.join(sDirectory, "scripts", "stepAlpha.py"), "w") as (
        fileOut
    ):
        fileOut.write("print('alpha')\n")
    return sDirectory


def testASeedCopiesTheSelectionPlusTheGitDirectory(
    tclientFiles, tmp_path,
):
    """".git" always crosses; a workflow must live inside a repository."""
    client, connectionDocker = tclientFiles
    sDirectory = _fsBuildHostProject(tmp_path)
    _fnRegisterProject(sDirectory)
    responseHttp = client.post(
        f"/api/files/{S_CONTAINER_ID}/seed-workspace",
        json={"saRelativePaths": ["scripts"]},
    )
    assert responseHttp.status_code == 200, responseHttp.text
    sRealDirectory = os.path.realpath(sDirectory)
    assert responseHttp.json() == {
        "bSuccess": True,
        "sDestination": "/workspace/hostProjectBravo",
        "iCopiedCount": 2,
        "listRestoredPaths": [],
        "sRestoreRefusal": "",
    }
    assert connectionDocker.listTreeWrites == [(
        "/workspace/hostProjectBravo",
        [os.path.join(sRealDirectory, "scripts"),
         os.path.join(sRealDirectory, ".git")],
    )]


def testASeedHandsOverTheWorkingTreeNotTheCommittedVersion(
    tclientFiles, tmp_path,
):
    """The copy's source is the researcher's files as they are now.

    A file edited since the last commit is copied with its edit: the
    path the writer receives reads back the working-tree bytes, which
    differ from what HEAD holds.
    """
    import subprocess
    client, connectionDocker = tclientFiles
    sDirectory = str(tmp_path / "hostProjectCharlie")
    os.makedirs(sDirectory)
    for listArguments in (["init", "-q"],):
        subprocess.run(["git", "-C", sDirectory, *listArguments], check=True)
    sFile = os.path.join(sDirectory, "result.txt")
    with open(sFile, "w") as fileOut:
        fileOut.write("committed\n")
    subprocess.run(["git", "-C", sDirectory, "add", "-A"], check=True)
    subprocess.run(
        ["git", "-C", sDirectory, "-c", "user.email=a@example.invalid",
         "-c", "user.name=A", "commit", "-q", "-m", "seed"], check=True,
    )
    with open(sFile, "w") as fileOut:
        fileOut.write("edited since the commit\n")
    _fnRegisterProject(sDirectory)
    responseHttp = client.post(
        f"/api/files/{S_CONTAINER_ID}/seed-workspace",
        json={"saRelativePaths": ["result.txt"]},
    )
    assert responseHttp.status_code == 200, responseHttp.text
    sHandedOver = connectionDocker.listTreeWrites[0][1][0]
    with open(sHandedOver) as fileRead:
        assert fileRead.read() == "edited since the commit\n"


def testASeedOfAnUnregisteredProjectNamesTheContainer(
    tclientFiles,
):
    client, connectionDocker = tclientFiles
    responseHttp = client.post(
        f"/api/files/{S_CONTAINER_ID}/seed-workspace",
        json={"saRelativePaths": ["scripts"]},
    )
    assert responseHttp.status_code == 404
    assert responseHttp.json()["detail"] == (
        "'test-container' is not a registered project."
    )
    assert connectionDocker.listTreeWrites == []


def testASeedOfAHostProjectIsRefusedWithTheReason(
    tclientFiles, tmp_path,
):
    client, connectionDocker = tclientFiles
    _fnRegisterProject(_fsBuildHostProject(tmp_path), sMode="host")
    responseHttp = client.post(
        f"/api/files/{S_CONTAINER_ID}/seed-workspace",
        json={"saRelativePaths": ["scripts"]},
    )
    assert responseHttp.status_code == 409
    assert "runs on this machine" in responseHttp.json()["detail"]
    assert connectionDocker.listTreeWrites == []


def testASeedWhoseDirectoryVanishedDoesNotLeakItsPath(
    tclientFiles, tmp_path,
):
    client, connectionDocker = tclientFiles
    sVanished = str(tmp_path / "vanishedProject")
    _fnRegisterProject(sVanished)
    responseHttp = client.post(
        f"/api/files/{S_CONTAINER_ID}/seed-workspace",
        json={"saRelativePaths": ["scripts"]},
    )
    assert responseHttp.status_code == 404
    assert "no longer exists" in responseHttp.json()["detail"]
    assert sVanished not in responseHttp.text
    assert connectionDocker.listTreeWrites == []


def testAFailedSeedCopyIsReportedNotSwallowed(
    tclientFiles, tmp_path,
):
    client, connectionDocker = tclientFiles
    _fnRegisterProject(_fsBuildHostProject(tmp_path))
    connectionDocker.sWriteFailure = "tar stream interrupted"
    responseHttp = client.post(
        f"/api/files/{S_CONTAINER_ID}/seed-workspace",
        json={"saRelativePaths": ["scripts"]},
    )
    assert responseHttp.status_code == 500
    assert "tar stream interrupted" in responseHttp.json()["detail"]


@pytest.mark.falsification
def testASeedAsksTheReceiverToCreateItsDestinationBelowTheWorkspaceRoot(
    tclientFiles, tmp_path,
):
    """The separate ``mkdir -p`` exec is gone; the receiver creates it.

    The destination is bounded by the workspace root, so creation can
    never reach above the volume.

    Kills: dropping ``bCreateDestination=True`` from the seed's write,
    which sends the copy to a destination directory nobody created.
    """
    client, connectionDocker = tclientFiles
    _fnRegisterProject(_fsBuildHostProject(tmp_path))
    responseHttp = client.post(
        f"/api/files/{S_CONTAINER_ID}/seed-workspace",
        json={"saRelativePaths": ["scripts"]},
    )
    assert responseHttp.status_code == 200, responseHttp.text
    assert connectionDocker.listTreeWriteKeywords == [{
        "sAuthorizedRoot": "/workspace", "bCreateDestination": True,
    }]
    assert not [
        dictAdmitted for dictAdmitted in connectionDocker.listAdmittedPrimitives
        if "mkdir" in dictAdmitted["sCommand"]
    ]


def _fnPlantTreeWriteError(connectionDocker, iMembersLanded):
    from vaibify.docker.confinedWrite import ContainerWriteRefusedError
    errorRefused = ContainerWriteRefusedError(
        "Copy into /workspace/hostProjectBravo refused: 'scripts' is a "
        "symlink or not a directory")
    errorRefused.iMembersLanded = iMembersLanded
    connectionDocker.errorTreeWrite = errorRefused


@pytest.mark.falsification
def testASeedRefusedBeforeAnythingLandedAnswers403WithTheReason(
    tclientFiles, tmp_path,
):
    """A decided refusal is a 4xx the container survives.

    Kills: answering every refused copy with 500, which poisons the
    journal record and quarantines a container over a symlink the
    researcher can simply remove.
    """
    client, connectionDocker = tclientFiles
    _fnRegisterProject(_fsBuildHostProject(tmp_path))
    _fnPlantTreeWriteError(connectionDocker, 0)
    responseHttp = client.post(
        f"/api/files/{S_CONTAINER_ID}/seed-workspace",
        json={"saRelativePaths": ["scripts"]},
    )
    assert responseHttp.status_code == 403
    assert "symlink or not a directory" in responseHttp.json()["detail"]


@pytest.mark.falsification
def testASeedThatStoppedPartWayIsAServerFailureNotARefusal(
    tclientFiles, tmp_path,
):
    """Members landed, so the container's state is no longer known.

    Kills: carrying a partial copy back as a clean refusal, which would
    leave a half-seeded workspace unreconciled.
    """
    client, connectionDocker = tclientFiles
    _fnRegisterProject(_fsBuildHostProject(tmp_path))
    _fnPlantTreeWriteError(connectionDocker, 3)
    responseHttp = client.post(
        f"/api/files/{S_CONTAINER_ID}/seed-workspace",
        json={"saRelativePaths": ["scripts"]},
    )
    assert responseHttp.status_code == 500


def testAnExplicitlySelectedGitDirectoryIsNotCopiedTwice(
    tclientFiles, tmp_path,
):
    client, connectionDocker = tclientFiles
    sDirectory = _fsBuildHostProject(tmp_path)
    _fnRegisterProject(sDirectory)
    responseHttp = client.post(
        f"/api/files/{S_CONTAINER_ID}/seed-workspace",
        json={"saRelativePaths": [".git", "scripts/stepAlpha.py"]},
    )
    assert responseHttp.status_code == 200, responseHttp.text
    assert responseHttp.json()["iCopiedCount"] == 2
    listCopied = connectionDocker.listTreeWrites[0][1]
    assert [os.path.basename(sPath) for sPath in listCopied] == [
        ".git", "stepAlpha.py",
    ]


@pytest.mark.parametrize("listSelection, iStatus, sFragment", [
    ([], 400, "No files were selected"),
    (["../outsideProject"], 403, "is outside the project"),
    (["scripts/vanished.py"], 404, "no longer exists"),
])
def testASeedSelectionIsProvenInsideTheProjectBeforeCopying(
    tclientFiles, tmp_path, listSelection, iStatus, sFragment,
):
    """The ``.git`` entry is appended to every selection, so the empty
    case is a project whose directory holds no infrastructure at all."""
    client, connectionDocker = tclientFiles
    sDirectory = str(tmp_path / "bareProject")
    os.makedirs(os.path.join(sDirectory, "scripts"))
    _fnRegisterProject(sDirectory)
    responseHttp = client.post(
        f"/api/files/{S_CONTAINER_ID}/seed-workspace",
        json={"saRelativePaths": listSelection},
    )
    assert responseHttp.status_code == iStatus
    assert sFragment in responseHttp.json()["detail"]
    assert os.path.realpath(sDirectory) not in responseHttp.text
    assert connectionDocker.listTreeWrites == []
