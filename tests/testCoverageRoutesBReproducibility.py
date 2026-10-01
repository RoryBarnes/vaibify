"""Coverage of the degraded and refusing branches of ``reproducibilityRoutes``.

Each branch here answers a question the PROOF tab asks when something
cannot be read: an unreadable registry, config, mirror, lock or
Dockerfile must degrade to UNKNOWN (never to "matches"), a carrier
refusal must surface as itself rather than as an I/O outcome, and a
body the declaration state machine rejects must be refused naming the
field. The repository is a real directory under ``tmp_path`` read
through the real host adapter; only the Docker exec boundary is a
double, and it records every command it was handed.
"""

import json
import logging
import os
from collections import namedtuple

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from tests.carrierStandDown import fnStandCarrierDown
from vaibify.config import registryManager
from vaibify.config.mutationAdmission import MutationNotAdmittedError
from vaibify.docker import containerManager
from vaibify.gui.containerOwnership import OwnerRecord
from vaibify.gui.routes import reproducibilityRoutes
from vaibify.reproducibility.dockerfileComposer import (
    S_GENERATED_MARKER,
    S_RECIPE_HEADER_PREFIX,
)
from vaibify.reproducibility.gitEvidence import RecordKindUndeterminedError
from vaibify.reproducibility.repoFiles import HostRepoFiles


S_CONTAINER_ID = "c0ffee00d0cker1d"
S_CONTAINER_NAME = "containerAlpha"
S_PROJECT_NAME = "projectAlpha"
S_AGENT_TOKEN = "agentTokenForAlpha"

ExecResult = namedtuple("ExecResult", ["iExitCode", "sStdout"])


class RecordingConnection:
    """A Docker exec double that answers from a script and records commands."""

    def __init__(self, listAnswers=(), listRunning=None):
        self.listAnswers = list(listAnswers)
        self.listCommands = []
        self.listRunning = listRunning or []

    def ftResultExecuteCommand(self, sContainerId, sCommand):
        self.listCommands.append((sContainerId, sCommand))
        answerNext = self.listAnswers.pop(0)
        if isinstance(answerNext, BaseException):
            raise answerNext
        return answerNext

    def ftRunInContainerStreamed(self, sContainerId, sCommand):
        self.listCommands.append((sContainerId, sCommand))
        answerNext = self.listAnswers.pop(0)
        if isinstance(answerNext, BaseException):
            raise answerNext
        return answerNext

    def flistGetRunningContainers(self):
        return list(self.listRunning)


class UnreadableRepoFiles(HostRepoFiles):
    """A host adapter whose reads fail as a permission-denied file would."""

    def fsReadText(self, sRelPath):
        raise PermissionError(f"permission denied: {sRelPath}")

    def fbaReadBytes(self, sRelPath):
        raise PermissionError(f"permission denied: {sRelPath}")


@pytest.fixture(autouse=True)
def fixtureIsolateRegistry(tmp_path, monkeypatch):
    """Point the registry and ~ at ``tmp_path``; clear the task registry."""
    sHome = str(tmp_path / "vaibifyHome")
    sRegistryDirectory = os.path.join(sHome, ".vaibify")
    monkeypatch.setenv("HOME", sHome)
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
    reproducibilityRoutes._DICT_VERIFY_TASKS.clear()
    yield
    reproducibilityRoutes._DICT_VERIFY_TASKS.clear()


@pytest.fixture
def sProjectRepo(tmp_path):
    sRepo = str(tmp_path / "projectRepo")
    os.makedirs(os.path.join(sRepo, ".vaibify"), exist_ok=True)
    return sRepo


def fnWriteRegistry(listEntries):
    """Write ``listEntries`` as the registry's project list."""
    os.makedirs(registryManager._S_REGISTRY_DIRECTORY, exist_ok=True)
    with open(registryManager._S_REGISTRY_PATH, "w") as fileHandle:
        json.dump({"listProjects": listEntries}, fileHandle)


def fsWriteConfig(tmp_path, sBody):
    """Write a vaibify.yml under ``tmp_path`` and return its path."""
    sDirectory = str(tmp_path / "configDirectory")
    os.makedirs(sDirectory, exist_ok=True)
    sConfigPath = os.path.join(sDirectory, "vaibify.yml")
    with open(sConfigPath, "w") as fileHandle:
        fileHandle.write(sBody)
    return sConfigPath


def fdictRegistryEntry(sConfigPath, sMode="container"):
    """Return one registry entry whose name, container name and id differ."""
    return {
        "sName": S_PROJECT_NAME,
        "sDirectory": os.path.dirname(sConfigPath or "/nonexistent"),
        "sConfigPath": sConfigPath,
        "sContainerName": S_CONTAINER_NAME,
        "sMode": sMode,
    }


def fsWriteRepoFile(sProjectRepo, sRelPath, sContent):
    """Write a file inside the project repository."""
    sAbsolute = os.path.join(sProjectRepo, sRelPath)
    os.makedirs(os.path.dirname(sAbsolute), exist_ok=True)
    with open(sAbsolute, "w") as fileHandle:
        fileHandle.write(sContent)
    return sAbsolute


# ---------------------------------------------------------------------
# Declared packages against the image's mirror
# ---------------------------------------------------------------------


def testDeclaredPackagesAreUnknownWhenTheRegistryCannotBeRead(monkeypatch):
    def fdictRaiseUnreadable():
        raise OSError("registry unreadable")

    monkeypatch.setattr(registryManager, "fdictLoadRegistry", fdictRaiseUnreadable)
    assert reproducibilityRoutes._flistDeclaredPackagesForContainer(
        S_CONTAINER_NAME,
    ) == []


def testDeclaredPackagesAreReadByContainerNameNeverByProjectName(tmp_path):
    sConfigPath = fsWriteConfig(
        tmp_path,
        f"projectName: {S_PROJECT_NAME}\npythonPackages:\n  - packageAlpha\n",
    )
    fnWriteRegistry([fdictRegistryEntry(sConfigPath)])
    assert reproducibilityRoutes._flistDeclaredPackagesForContainer(
        S_CONTAINER_NAME,
    ) == ["packageAlpha"]
    assert reproducibilityRoutes._flistDeclaredPackagesForContainer(
        S_PROJECT_NAME,
    ) == []


def testAnEntryWithoutAConfigPathDeclaresNothing():
    fnWriteRegistry([fdictRegistryEntry("")])
    assert reproducibilityRoutes._flistDeclaredPackagesForContainer(
        S_CONTAINER_NAME,
    ) == []


def testAnUnloadableConfigDeclaresNothing(tmp_path):
    sConfigPath = fsWriteConfig(tmp_path, "projectName: [unterminated\n")
    fnWriteRegistry([fdictRegistryEntry(sConfigPath)])
    assert reproducibilityRoutes._flistDeclaredPackagesForContainer(
        S_CONTAINER_NAME,
    ) == []


def testAnUnreadableMirrorIsNotCheckedRatherThanMatching(
    tmp_path, sProjectRepo,
):
    """Declared packages plus an unreadable mirror is an unanswered question."""
    sConfigPath = fsWriteConfig(
        tmp_path,
        f"projectName: {S_PROJECT_NAME}\npythonPackages:\n  - packageAlpha\n",
    )
    fnWriteRegistry([fdictRegistryEntry(sConfigPath)])
    fsWriteRepoFile(
        sProjectRepo, reproducibilityRoutes.S_MIRROR_RELATIVE_PATH,
        "packageBeta==1.0\n",
    )
    dictAnswer = reproducibilityRoutes.fdictCheckImageMatchesDeclaration(
        S_CONTAINER_NAME, UnreadableRepoFiles(sProjectRepo),
    )
    assert dictAnswer["bChecked"] is False
    dictReadable = reproducibilityRoutes.fdictCheckImageMatchesDeclaration(
        S_CONTAINER_NAME, HostRepoFiles(sProjectRepo),
    )
    assert dictReadable["bChecked"] is True
    assert dictReadable["bMatches"] is False
    assert dictReadable["listMissingFromImage"] == ["packagealpha"] or (
        dictReadable["listMissingFromImage"] == ["packageAlpha"]
    )


# ---------------------------------------------------------------------
# requirements.lock against the running container
# ---------------------------------------------------------------------


def testAnUnreadableLockIsUnknownAndNeverExecs(sProjectRepo):
    fsWriteRepoFile(sProjectRepo, "requirements.lock", "packageAlpha==1.0\n")
    connectionDocker = RecordingConnection()
    dictVerdict = reproducibilityRoutes.fdictCheckLockSatisfiedByContainer(
        connectionDocker, S_CONTAINER_ID, UnreadableRepoFiles(sProjectRepo),
    )
    assert dictVerdict["sState"] == "unknown"
    assert "requirements.lock could not be read" in dictVerdict["sReason"]
    assert connectionDocker.listCommands == []


def testALockPinningNothingAsksNoQuestion(sProjectRepo):
    fsWriteRepoFile(
        sProjectRepo, "requirements.lock", "# no pins here\n\n",
    )
    connectionDocker = RecordingConnection()
    assert reproducibilityRoutes.fdictCheckLockSatisfiedByContainer(
        connectionDocker, S_CONTAINER_ID, HostRepoFiles(sProjectRepo),
    ) is None
    assert connectionDocker.listCommands == []


def testAFailedInventoryIsUnknownAndCarriesItsFingerprint(sProjectRepo):
    fsWriteRepoFile(sProjectRepo, "requirements.lock", "packageAlpha==1.0\n")
    connectionDocker = RecordingConnection([ExecResult(3, "")])
    dictVerdict = reproducibilityRoutes.fdictCheckLockSatisfiedByContainer(
        connectionDocker, S_CONTAINER_ID, HostRepoFiles(sProjectRepo),
        sRunningImageIdentity="sha256:imageRunning",
    )
    assert dictVerdict["sState"] == "unknown"
    assert dictVerdict["sReason"] == "the inventory exited 3"
    assert dictVerdict["sFingerprint"]
    assert [sId for sId, _ in connectionDocker.listCommands] == [
        S_CONTAINER_ID,
    ]


# ---------------------------------------------------------------------
# Dockerfile provenance and the recorded digest
# ---------------------------------------------------------------------


def testAnUnreadableDockerfileDeterminesNothing(sProjectRepo):
    fsWriteRepoFile(sProjectRepo, "Dockerfile", S_GENERATED_MARKER + "\n")
    dictAnswer = reproducibilityRoutes.fdictAssessDockerfileProvenance(
        UnreadableRepoFiles(sProjectRepo),
    )
    assert dictAnswer == {
        "bDockerfileDescribesPinnedImage": None,
        "sHeaderFingerprint": "", "sImageFingerprint": "",
    }


def testAGeneratedHeaderWithNoPinnedDigestDeterminesNothing(sProjectRepo):
    """No recorded digest means nothing to compare, never a mismatch."""
    fsWriteRepoFile(
        sProjectRepo, "Dockerfile",
        S_GENERATED_MARKER + "\n" + S_RECIPE_HEADER_PREFIX + "ab" * 32
        + "\nFROM baseImage\n",
    )
    fsWriteRepoFile(
        sProjectRepo, ".vaibify/environment.json", "{not json",
    )
    filesRepo = HostRepoFiles(sProjectRepo)
    assert reproducibilityRoutes._fsRecordedImageDigest(filesRepo) == ""
    dictAnswer = reproducibilityRoutes.fdictAssessDockerfileProvenance(
        filesRepo,
    )
    assert dictAnswer["bDockerfileDescribesPinnedImage"] is None
    assert dictAnswer["sHeaderFingerprint"] == ""


def testTheRecordedDigestIsReadFromTheEnvelope(sProjectRepo):
    fsWriteRepoFile(
        sProjectRepo, ".vaibify/environment.json",
        json.dumps({"dictContainer": {"sImageDigest": "image@sha256:" + "c" * 64}}),
    )
    assert reproducibilityRoutes._fsRecordedImageDigest(
        HostRepoFiles(sProjectRepo),
    ) == "image@sha256:" + "c" * 64


# ---------------------------------------------------------------------
# The image origin and the attestation commit
# ---------------------------------------------------------------------


def testAnUnreadableOriginReadsAsBuilt(monkeypatch, caplog):
    def fdictRaiseDaemonGone(sProjectName):
        raise OSError("daemon socket gone")

    monkeypatch.setattr(
        containerManager, "fdictLiveImageOriginForProject",
        fdictRaiseDaemonGone,
    )
    with caplog.at_level(logging.WARNING):
        assert reproducibilityRoutes._fdictImageOriginForContainer(
            None, S_CONTAINER_ID,
        ) is None
    assert any(
        "Could not read the image origin" in recordLog.getMessage()
        for recordLog in caplog.records
    )


def testARefusalReadingTheOriginIsNeverSwallowed(monkeypatch):
    def fdictRaiseRefusal(sProjectName):
        raise MutationNotAdmittedError("no admission for this read")

    monkeypatch.setattr(
        containerManager, "fdictLiveImageOriginForProject", fdictRaiseRefusal,
    )
    with pytest.raises(MutationNotAdmittedError):
        reproducibilityRoutes._fdictImageOriginForContainer(
            None, S_CONTAINER_ID,
        )


def testAHostProjectIsNeverCommittedThroughTheContainer(sProjectRepo):
    fnWriteRegistry([{
        "sName": S_CONTAINER_ID, "sDirectory": sProjectRepo,
        "sConfigPath": "", "sContainerName": "", "sMode": "host",
    }])
    connectionDocker = RecordingConnection()
    reproducibilityRoutes._fnCommitAttestation(
        connectionDocker, S_CONTAINER_ID,
        {"sProjectRepoPath": sProjectRepo},
    )
    assert connectionDocker.listCommands == []


def testAFailedStageLogsAndNeverCommits(caplog):
    connectionDocker = RecordingConnection([(1, "fatal: index locked")])
    with caplog.at_level(logging.WARNING):
        reproducibilityRoutes._fnCommitAttestation(
            connectionDocker, S_CONTAINER_ID,
            {"sProjectRepoPath": "/workspace/projectAlpha"},
        )
    assert len(connectionDocker.listCommands) == 1
    assert " add -- " in connectionDocker.listCommands[0][1]
    assert any(
        "Could not stage the L3 attestation: fatal: index locked"
        in recordLog.getMessage() for recordLog in caplog.records
    )


def testAFailedCommitIsLoggedButNothingToCommitIsNot(caplog):
    connectionDocker = RecordingConnection(
        [(0, ""), (1, "error: gpg failed"), (0, ""),
         (1, "nothing to commit, working tree clean")],
    )
    dictWorkflow = {"sProjectRepoPath": "/workspace/projectAlpha"}
    with caplog.at_level(logging.WARNING):
        reproducibilityRoutes._fnCommitAttestation(
            connectionDocker, S_CONTAINER_ID, dictWorkflow,
            listRelPaths=["REPRODUCED.sha256"],
        )
        reproducibilityRoutes._fnCommitAttestation(
            connectionDocker, S_CONTAINER_ID, dictWorkflow,
        )
    listWarnings = [
        recordLog.getMessage() for recordLog in caplog.records
        if "Could not commit" in recordLog.getMessage()
    ]
    assert listWarnings == [
        "Could not commit the L3 attestation: error: gpg failed",
    ]
    assert "REPRODUCED.sha256" in connectionDocker.listCommands[1][1]


def testAnExecErrorDuringTheCommitIsLoggedNotRaised(caplog):
    connectionDocker = RecordingConnection([RuntimeError("exec stream closed")])
    with caplog.at_level(logging.WARNING):
        reproducibilityRoutes._fnCommitAttestation(
            connectionDocker, S_CONTAINER_ID,
            {"sProjectRepoPath": "/workspace/projectAlpha"},
        )
    assert any(
        "exec stream closed" in recordLog.getMessage()
        for recordLog in caplog.records
    )


def testACarrierRefusalDuringTheCommitReachesTheCaller():
    connectionDocker = RecordingConnection(
        [MutationNotAdmittedError("durable admission was not open")],
    )
    with pytest.raises(MutationNotAdmittedError):
        reproducibilityRoutes._fnCommitAttestation(
            connectionDocker, S_CONTAINER_ID,
            {"sProjectRepoPath": "/workspace/projectAlpha"},
        )


def testAProvenanceCaptureFailureIsRecordedAsNone(monkeypatch, caplog):
    import asyncio

    def fdictRaiseCapture(*aArgs):
        raise RuntimeError("transcript unreadable")

    monkeypatch.setattr(
        reproducibilityRoutes, "fdictCaptureAiProvenanceStamp",
        fdictRaiseCapture,
    )
    with caplog.at_level(logging.ERROR):
        dictStamp = asyncio.run(
            reproducibilityRoutes._fdictCaptureProvenanceOrNone(
                {}, None, S_CONTAINER_ID, None,
            ),
        )
    assert dictStamp is None
    assert any(
        "AI-provenance capture failed: transcript unreadable"
        in recordLog.getMessage() for recordLog in caplog.records
    )


def testABinaryEntryReplacesAMalformedHostBinariesRecord(sProjectRepo):
    fsWriteRepoFile(
        sProjectRepo, ".vaibify/environment.json",
        json.dumps({"dictHostBinaries": {"listBinaries": "not a list"},
                    "sSchemaVersion": "1"}),
    )
    filesRepo = HostRepoFiles(sProjectRepo)
    dictCaptured = {"sBinaryPath": "/usr/local/bin/toolAlpha", "sSha256": ""}
    reproducibilityRoutes._fnAppendBinaryToEnvironmentJson(
        filesRepo, dictCaptured,
    )
    with open(os.path.join(sProjectRepo, ".vaibify/environment.json")) as fileHandle:
        dictWritten = json.load(fileHandle)
    assert dictWritten["dictHostBinaries"]["listBinaries"] == [dictCaptured]
    assert dictWritten["sSchemaVersion"] == "1"


# ---------------------------------------------------------------------
# Routes, over a bare app with the carrier stood down
# ---------------------------------------------------------------------


def fclientBuild(dictWorkflow, dictPaths=None, connectionDocker=None):
    """Return a client over the reproducibility routes and their app."""
    app = FastAPI()
    app.state.listLifespanStartup = []
    app.state.listLifespanShutdown = []
    app.state.dictContainerOwners = {
        S_CONTAINER_NAME: OwnerRecord(
            sLeaseId="leaseAlpha", fileHandleLock=None,
            sAgentToken=S_AGENT_TOKEN, sContainerId=S_CONTAINER_ID,
        ),
    }
    dictCtx = {
        "docker": connectionDocker,
        "workflows": {S_CONTAINER_ID: dictWorkflow},
        "paths": dictPaths if dictPaths is not None else {},
        "pipelineTasks": {},
        "require": lambda *aArgs: None,
        "save": lambda sId, dictWf: None,
        "variables": lambda sId: {},
        "workflowDir": lambda sId: dictWorkflow["sProjectRepoPath"],
    }
    reproducibilityRoutes.fnRegisterAll(app, dictCtx)
    return TestClient(app)


@pytest.fixture
def fixtureCarrierStoodDown(monkeypatch):
    fnStandCarrierDown(monkeypatch, reproducibilityRoutes)


def testVerifyWithoutAnActiveWorkflowPathIsRefused(sProjectRepo):
    clientHttp = fclientBuild({"sProjectRepoPath": sProjectRepo})
    responseHttp = clientHttp.post(
        f"/api/workflow/{S_CONTAINER_ID}/level3/verify",
    )
    assert responseHttp.status_code == 409
    assert "No active workflow path" in responseHttp.json()["detail"]
    assert sProjectRepo not in responseHttp.text


def testTheDeterminismScanFindsEntropyAndSaysWhatItCannotSee(sProjectRepo):
    fsWriteRepoFile(
        sProjectRepo, "stepAlpha/makeData.py",
        "import os\nfValue = os.urandom(8)\n",
    )
    dictWorkflow = {
        "sProjectRepoPath": sProjectRepo,
        "listSteps": [{
            "sName": "Step Alpha", "sDirectory": "stepAlpha",
            "saDataCommands": ["python makeData.py"],
        }, {
            "sName": "Step Beta", "sDirectory": "stepBeta",
            "saDataCommands": ["python missingScript.py"],
        }],
    }
    responseHttp = fclientBuild(dictWorkflow).get(
        f"/api/workflow/{S_CONTAINER_ID}/determinism/scan",
    )
    assert responseHttp.status_code == 200, responseHttp.text
    dictResult = responseHttp.json()
    assert dictResult["listScanned"] == ["stepAlpha/makeData.py"]
    assert dictResult["listUnreadable"] == ["stepBeta/missingScript.py"]
    assert any(
        "stepAlpha/makeData.py:2" in sIssue and "os.urandom" in sIssue
        for sIssue in dictResult["listIssues"]
    )
    assert "not that the workflow is deterministic" in dictResult["sScopeNote"]
    assert "dictMathsLibrary" in dictResult


def testCopyingTheDockerfileIsRefusedToTheAgentLane(sProjectRepo):
    clientHttp = fclientBuild({"sProjectRepoPath": sProjectRepo})
    responseHttp = clientHttp.post(
        f"/api/workflow/{S_CONTAINER_ID}/level3/dockerfile",
        headers={"X-Vaibify-Session": S_AGENT_TOKEN},
    )
    assert responseHttp.status_code == 403
    assert "in-container agent" in responseHttp.json()["detail"]
    assert S_AGENT_TOKEN not in responseHttp.text
    assert not os.path.exists(os.path.join(sProjectRepo, "Dockerfile"))


def testCopyingTheDockerfileNeverOverwritesTheResearchersOwn(
    sProjectRepo, fixtureCarrierStoodDown,
):
    sOwnDockerfile = "FROM researcherBase\nRUN echo mine\n"
    fsWriteRepoFile(sProjectRepo, "Dockerfile", sOwnDockerfile)
    responseHttp = fclientBuild({"sProjectRepoPath": sProjectRepo}).post(
        f"/api/workflow/{S_CONTAINER_ID}/level3/dockerfile",
        headers={"X-Vaibify-Session": "tokenOfNoContainer"},
    )
    assert responseHttp.status_code == 200, responseHttp.text
    dictBody = responseHttp.json()
    assert dictBody["bWritten"] is False
    assert "did not generate" in dictBody["sRefusal"]
    with open(os.path.join(sProjectRepo, "Dockerfile")) as fileHandle:
        assert fileHandle.read() == sOwnDockerfile


def testCopyingTheDockerfileWritesAGeneratedFile(
    sProjectRepo, fixtureCarrierStoodDown,
):
    responseHttp = fclientBuild({"sProjectRepoPath": sProjectRepo}).post(
        f"/api/workflow/{S_CONTAINER_ID}/level3/dockerfile",
    )
    assert responseHttp.status_code == 200, responseHttp.text
    dictBody = responseHttp.json()
    assert dictBody["bWritten"] is True
    assert dictBody["sDockerfilePath"] == sProjectRepo + "/Dockerfile"
    assert dictBody["bManifestRefreshed"] is False
    with open(os.path.join(sProjectRepo, "Dockerfile")) as fileHandle:
        assert S_GENERATED_MARKER in fileHandle.read()


def testAnUnwritableDockerfileIsA500NamingTheWrite(
    sProjectRepo, fixtureCarrierStoodDown,
):
    os.makedirs(os.path.join(sProjectRepo, "Dockerfile"))
    responseHttp = fclientBuild({"sProjectRepoPath": sProjectRepo}).post(
        f"/api/workflow/{S_CONTAINER_ID}/level3/dockerfile",
    )
    assert responseHttp.status_code == 500
    assert responseHttp.json()["detail"].startswith(
        "Could not write Dockerfile",
    )

@pytest.mark.falsification
def testTheCopiedDockerfileComposesTheProjectsOverlays(
    tmp_path, sProjectRepo, fixtureCarrierStoodDown,
):
    """The route resolves the container NAME before it composes overlays.

    The URL carries the Docker id; the project registry is keyed by
    name. The two are distinct here, so a route that handed the id to
    the composer finds no project and writes a base-only Dockerfile.

    Kills: reproducibilityRoutes._fdictWriteDockerfileThenRepin: the
    resolved `fsContainerNameForId(dictCtx["docker"], sContainerId)`
    replaced by the raw `sContainerId`.
    """
    assert S_CONTAINER_NAME != S_CONTAINER_ID
    sConfigPath = fsWriteConfig(
        tmp_path,
        f"projectName: {S_PROJECT_NAME}\nfeatures:\n  jupyter: true\n",
    )
    fnWriteRegistry([fdictRegistryEntry(sConfigPath)])
    connectionDocker = RecordingConnection(listRunning=[
        {"sContainerId": S_CONTAINER_ID, "sName": S_CONTAINER_NAME},
    ])
    responseHttp = fclientBuild(
        {"sProjectRepoPath": sProjectRepo},
        connectionDocker=connectionDocker,
    ).post(f"/api/workflow/{S_CONTAINER_ID}/level3/dockerfile")
    assert responseHttp.status_code == 200, responseHttp.text
    with open(os.path.join(sProjectRepo, "Dockerfile")) as fileHandle:
        assert "jupyter" in fileHandle.read()


@pytest.mark.parametrize("dictBody, sExpected", [
    ({"bNoStandaloneBinaries": False, "listDeclaredBinaries": "toolAlpha"},
     "listDeclaredBinaries must be a list."),
    ({"bNoStandaloneBinaries": True,
      "listDeclaredBinaries": [{"sBinaryPath": "/bin/toolAlpha"}]},
     "Waiver requires listDeclaredBinaries to be empty."),
    ({"bNoStandaloneBinaries": False, "listDeclaredBinaries": []},
     "Without the waiver, listDeclaredBinaries must be non-empty."),
    ({"bNoStandaloneBinaries": False, "listDeclaredBinaries": ["toolAlpha"]},
     "Entry 0 is not an object."),
    ({"bNoStandaloneBinaries": False, "listDeclaredBinaries": [
        {"sBinaryPath": "/bin/toolAlpha", "sPurpose": "  ",
         "sExpectedVersion": "1.0"}]},
     "Entry 0 missing string 'sPurpose'."),
])
def testTheBinaryDeclarationRefusesAnInconsistentBody(
    sProjectRepo, dictBody, sExpected,
):
    dictWorkflow = {"sProjectRepoPath": sProjectRepo}
    responseHttp = fclientBuild(dictWorkflow).post(
        f"/api/workflow/{S_CONTAINER_ID}/binaries/declare", json=dictBody,
    )
    assert responseHttp.status_code == 400
    assert responseHttp.json()["detail"] == sExpected
    assert "listDeclaredBinaries" not in dictWorkflow


def testTheBinaryDeclarationValidatorRefusesANonObject():
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as excInfo:
        reproducibilityRoutes._fnValidateBinaryDeclarationBody(["toolAlpha"])
    assert excInfo.value.status_code == 400
    assert excInfo.value.detail == "Body must be a JSON object."


def testCapturingABinaryRequiresItsPath(sProjectRepo):
    responseHttp = fclientBuild({"sProjectRepoPath": sProjectRepo}).post(
        f"/api/workflow/{S_CONTAINER_ID}/binaries/capture",
        json={"sBinaryPath": "   "},
    )
    assert responseHttp.status_code == 400
    assert responseHttp.json()["detail"] == "sBinaryPath is required."


def testAnUnknownDeterminismAnswerIsRefusedNamingTheChoices(sProjectRepo):
    dictWorkflow = {"sProjectRepoPath": sProjectRepo}
    responseHttp = fclientBuild(dictWorkflow).post(
        f"/api/workflow/{S_CONTAINER_ID}/determinism/declare",
        json={"sBlasVarianceAnswer": "maybe"},
    )
    assert responseHttp.status_code == 422
    sDetail = responseHttp.json()["detail"]
    assert sDetail.startswith("sBlasVarianceAnswer must be one of ")
    assert "'maybe'" in sDetail
    assert "dictDeterminism" not in dictWorkflow


def testABusyContainerRefusesTheDurableLaunchByName(
    tmp_path, monkeypatch, fixtureCarrierStoodDown,
):
    """The carrier's refusal of a second durable task reaches the caller."""
    from tests.testReproducibilityRoutes import (
        _fdictBuildWorkflow,
        _fnSeedReadyL3Repo,
    )
    from vaibify.gui import commitCarrier
    sRepo = str(tmp_path / "readyRepo")
    os.makedirs(sRepo)
    _fnSeedReadyL3Repo(sRepo)
    listStarted = []

    async def fdictRefuseTheLaunch(
        appState, sName, sContainerId, dictLaneTuple, fnStartTask,
        sOperation="a long-running task",
    ):
        listStarted.append(sOperation)
        return {"bLaunched": False,
                "sReason": "a step run is writing to it"}

    monkeypatch.setattr(
        commitCarrier, "fdictLaunchDurableTask", fdictRefuseTheLaunch,
    )
    clientHttp = fclientBuild(
        _fdictBuildWorkflow(sRepo),
        dictPaths={S_CONTAINER_ID: sRepo + "/.vaibify/workflows/w.json"},
    )
    responseHttp = clientHttp.post(
        f"/api/workflow/{S_CONTAINER_ID}/level3/verify",
    )
    assert responseHttp.status_code == 409, responseHttp.text
    assert responseHttp.json()["detail"] == (
        "This container is busy: a step run is writing to it."
    )
    assert listStarted == ["the Level 3 verification"]
    assert S_CONTAINER_ID not in reproducibilityRoutes._DICT_VERIFY_TASKS


def fnInitialiseGitRepository(sRepo):
    """Make ``sRepo`` a git repository with one commit by this identity."""
    import subprocess
    listIdentity = [
        "-c", "user.email=researcher@example.org",
        "-c", "user.name=Researcher Alpha",
        "-c", "commit.gpgsign=false",
    ]
    fsWriteRepoFile(sRepo, "README.md", "# project\n")
    for listArguments in (
        ["init", "-q"], ["add", "README.md"],
        ["commit", "-q", "-m", "initial"],
    ):
        subprocess.run(
            ["git", *listIdentity, *listArguments], cwd=sRepo,
            check=True, capture_output=True,
        )


@pytest.mark.parametrize("bManifestBlocked", [False, True])
def testCopyingTheDockerfileRepinsTheManifestOrSaysItCouldNot(
    sProjectRepo, fixtureCarrierStoodDown, monkeypatch, caplog,
    bManifestBlocked,
):
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    fnInitialiseGitRepository(sProjectRepo)
    fsWriteRepoFile(
        sProjectRepo, ".vaibify/workflows/w.json",
        json.dumps({"listSteps": []}),
    )
    if bManifestBlocked:
        os.makedirs(os.path.join(sProjectRepo, "MANIFEST.sha256"))
    dictWorkflow = {"sProjectRepoPath": sProjectRepo, "listSteps": []}
    with caplog.at_level(logging.WARNING, logger="vaibify"):
        responseHttp = fclientBuild(dictWorkflow).post(
            f"/api/workflow/{S_CONTAINER_ID}/level3/dockerfile",
        )
    assert responseHttp.status_code == 200, responseHttp.text
    dictBody = responseHttp.json()
    assert dictBody["bWritten"] is True
    assert dictBody["bManifestRefreshed"] is (not bManifestBlocked)
    bWarned = any(
        "manifest re-pin failed" in recordLog.getMessage()
        for recordLog in caplog.records
    )
    assert bWarned is bManifestBlocked
    if not bManifestBlocked:
        with open(os.path.join(sProjectRepo, "MANIFEST.sha256")) as fileHandle:
            assert "Dockerfile" in fileHandle.read()


@pytest.mark.xfail(
    strict=True,
    raises=RecordKindUndeterminedError,
    reason=(
        "BUG: gitEvidence._ftAskGit catches every exception, including a "
        "ControlPlaneRefusalError, and re-raises it as "
        "RecordKindUndeterminedError, so _fsRecordKindForProject's "
        "documented re-raise of a carrier refusal is unreachable and a "
        "refusal reads as an undetermined git answer."
    ),
)
def testACarrierRefusalAskingWhoseRecordItIsSurfacesAsItself():
    connectionDocker = RecordingConnection(
        [MutationNotAdmittedError("no admission for this exec")],
    )
    with pytest.raises(MutationNotAdmittedError):
        reproducibilityRoutes._fsRecordKindForProject(
            connectionDocker, S_CONTAINER_ID,
            {"sProjectRepoPath": "/workspace/projectAlpha"},
        )
