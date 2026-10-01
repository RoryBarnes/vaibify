"""Coverage of the Replay-axis refusals and helpers in ``replayRoutes``.

The Replay axis records what governed AI-assisted work, so its
refusals must name their cause without echoing a host path, a capture
that fails after its requester left must be logged rather than lost,
and redaction must never fail open when the session env file is
absent. The container file surface is an in-memory double; host files
are real files under ``tmp_path`` with HOME pointed there.
"""

import asyncio
import logging
import os
import posixpath
import stat
from collections import namedtuple

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from tests.carrierStandDown import fnStandCarrierDown
from vaibify.config import registryManager
from vaibify.gui.actionCatalog import S_SESSION_ENV_PATH
from vaibify.gui.promptRecordManager import S_PROMPT_RECORD_SESSIONS_DIRECTORY
from vaibify.gui.routes import replayRoutes
from vaibify.reproducibility.repoFiles import HostRepoFiles


S_CONTAINER_ID = "d0cker1dreplay"
S_REPO_PATH = "/workspace/repositoryAlpha"
S_HUB_TOKEN = "hubTokenValue"
S_AGENT_TOKEN = "agentTokenValue"

StreamedResult = namedtuple("StreamedResult", ["iExitCode", "sStdout", "sStderr"])


class ContainerFilesDouble:
    """An in-memory container file surface with a scripted exec answer."""

    def __init__(self, tExecAnswer=None):
        self.dictFiles = {}
        self.listCommands = []
        self.tExecAnswer = tExecAnswer or StreamedResult(0, "", "")

    def fbaFetchFile(self, sContainerId, sFilePath):
        if sFilePath not in self.dictFiles:
            raise FileNotFoundError(sFilePath)
        return self.dictFiles[sFilePath]

    def fnWriteFile(self, sContainerId, sFilePath, baContent):
        self.dictFiles[sFilePath] = baContent

    def ftRunInContainerStreamed(self, sContainerId, sCommand):
        self.listCommands.append(sCommand)
        return self.tExecAnswer


@pytest.fixture(autouse=True)
def fixtureIsolateRegistry(tmp_path, monkeypatch):
    sHome = str(tmp_path / "researcherHome")
    os.makedirs(sHome)
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
    return sHome


@pytest.fixture
def fixtureCarrierStoodDown(monkeypatch):
    fnStandCarrierDown(monkeypatch, replayRoutes)


def ftBuildClient(dictWorkflow, connectionDocker=None):
    """Return ``(client, listSaves)`` over the Replay routes alone."""
    listSaves = []
    dictCtx = {
        "docker": connectionDocker or ContainerFilesDouble(),
        "workflows": {S_CONTAINER_ID: dictWorkflow},
        "require": lambda *aArgs: None,
        "save": lambda sId, dictWf: listSaves.append(sId),
        "sSessionToken": S_HUB_TOKEN,
    }
    app = FastAPI()
    replayRoutes.fnRegisterAll(app, dictCtx)
    return TestClient(app), listSaves


def fsUrl(sSuffix):
    return f"/api/workflow/{S_CONTAINER_ID}{sSuffix}"


def testANonObjectModelDeclarationIsRefused():
    with pytest.raises(HTTPException) as excInfo:
        replayRoutes._fdictValidateModelBody(["modelAlpha"])
    assert excInfo.value.status_code == 400
    assert excInfo.value.detail == "Model declaration must be an object."


def testANonObjectPersonalLayerDeclarationIsRefused():
    with pytest.raises(HTTPException) as excInfo:
        replayRoutes._fsValidatePersonalLayerStatus("declared-private")
    assert excInfo.value.status_code == 400
    assert "must be an object" in excInfo.value.detail


def testReadingTheContextOfAWorkflowWithoutARepoIsRefused():
    clientHttp, _ = ftBuildClient({"listSteps": []})
    responseHttp = clientHttp.get(fsUrl("/project-context"))
    assert responseHttp.status_code == 400
    assert responseHttp.json()["detail"] == (
        "This workflow has no project repository."
    )


def testAdoptingARootFileOtherThanTheTwoNamesIsRefused(
    fixtureCarrierStoodDown,
):
    connectionDocker = ContainerFilesDouble()
    connectionDocker.dictFiles[S_REPO_PATH + "/README.md"] = b"# readme\n"
    clientHttp, _ = ftBuildClient(
        {"sProjectRepoPath": S_REPO_PATH, "listSteps": []}, connectionDocker,
    )
    responseHttp = clientHttp.post(
        fsUrl("/project-context/import"),
        json={"bAdoptRepoRoot": True, "sRootBasename": "README.md"},
    )
    assert responseHttp.status_code == 400
    assert responseHttp.json()["detail"] == (
        "sRootBasename must be CLAUDE.md or AGENTS.md."
    )
    assert S_REPO_PATH + "/.vaibify/AGENTS.md" not in connectionDocker.dictFiles
    assert connectionDocker.listCommands == []


def testAFailedSymlinkReplacementIsSurfacedNotIgnored(
    fixtureCarrierStoodDown,
):
    connectionDocker = ContainerFilesDouble(
        StreamedResult(1, "", "ln: permission denied"),
    )
    connectionDocker.dictFiles[S_REPO_PATH + "/CLAUDE.md"] = b"# rules\n"
    clientHttp, _ = ftBuildClient(
        {"sProjectRepoPath": S_REPO_PATH, "listSteps": []}, connectionDocker,
    )
    responseHttp = clientHttp.post(
        fsUrl("/project-context/import"),
        json={"bAdoptRepoRoot": True, "sRootBasename": "CLAUDE.md"},
    )
    assert responseHttp.status_code == 500
    assert "symlink failed: ln: permission denied" in (
        responseHttp.json()["detail"]
    )
    assert connectionDocker.dictFiles[
        S_REPO_PATH + "/.vaibify/AGENTS.md"
    ] == b"# rules\n"
    assert len(connectionDocker.listCommands) == 1
    assert "ln -s" in connectionDocker.listCommands[0]


def testRedactionKeepsTheHubTokenWhenTheSessionEnvIsAbsent():
    listSecrets = replayRoutes._flistGatherSessionSecrets(
        {"sSessionToken": S_HUB_TOKEN, "docker": ContainerFilesDouble()},
        S_CONTAINER_ID,
    )
    assert listSecrets == [S_HUB_TOKEN]


def testRedactionReadsEveryValueInTheSessionEnv():
    connectionDocker = ContainerFilesDouble()
    connectionDocker.dictFiles[S_SESSION_ENV_PATH] = (
        f"VAIBIFY_AGENT_TOKEN={S_AGENT_TOKEN}\nnot a pair\n".encode()
    )
    assert replayRoutes._flistGatherSessionSecrets(
        {"sSessionToken": "", "docker": connectionDocker}, S_CONTAINER_ID,
    ) == [S_AGENT_TOKEN]


def testACaptureThatFailsAfterItsRequesterLeftIsLogged(caplog):
    dictRegistry = {}

    async def fnFailingCapture():
        raise RuntimeError("transcript listing failed")

    async def fnDriveTheCapture():
        taskCapture = asyncio.get_running_loop().create_task(
            fnFailingCapture(),
        )
        replayRoutes._fdictStartCapture(
            dictRegistry, "containerAlpha", taskCapture,
        )
        assert "containerAlpha" in dictRegistry
        await asyncio.wait({taskCapture})
        await asyncio.sleep(0)

    with caplog.at_level(logging.WARNING):
        asyncio.run(fnDriveTheCapture())
    assert dictRegistry == {}
    assert any(
        "Prompt Record capture for containerAlpha failed: "
        "transcript listing failed" in recordLog.getMessage()
        for recordLog in caplog.records
    )


def testApprovingAFirstCaptureRequiresTheRecordToBeOn(
    fixtureCarrierStoodDown,
):
    dictWorkflow = {"sProjectRepoPath": S_REPO_PATH, "listSteps": []}
    clientHttp, listSaves = ftBuildClient(dictWorkflow)
    responseHttp = clientHttp.post(
        fsUrl("/prompt-record/approve-first-capture"),
    )
    assert responseHttp.status_code == 409
    assert responseHttp.json()["detail"] == (
        "The Prompt Record is not enabled."
    )
    assert listSaves == []


def testTheReviewSampleIsTheHeadOfTheNewestSession(tmp_path):
    sRepo = str(tmp_path / "repositoryHost")
    sSessionDirectory = os.path.join(sRepo, S_PROMPT_RECORD_SESSIONS_DIRECTORY)
    os.makedirs(sSessionDirectory)
    with open(os.path.join(sSessionDirectory, "sessionNewest.jsonl"), "w") as fileHandle:
        fileHandle.write("\n".join(f"line {i}" for i in range(60)))
    filesRepo = HostRepoFiles(sRepo)
    assert replayRoutes._fsReviewSample(filesRepo, {}) == ""
    sSample = replayRoutes._fsReviewSample(filesRepo, {"listCaptures": [
        {"sSessionFileName": "sessionOlder.jsonl"},
        {"sSessionFileName": "sessionNewest.jsonl"},
    ]})
    assert sSample.split("\n") == [f"line {i}" for i in range(40)]
    assert replayRoutes._fsReviewSample(filesRepo, {"listCaptures": [
        {"sSessionFileName": "sessionMissing.jsonl"},
    ]}) == ""


def testEndingSupervisionOnHostRefusesAContainerProject(
    fixtureCarrierStoodDown,
):
    dictWorkflow = {"sProjectRepoPath": S_REPO_PATH, "listSteps": []}
    clientHttp, listSaves = ftBuildClient(dictWorkflow)
    responseHttp = clientHttp.post(fsUrl("/supervision/end-on-host"))
    assert responseHttp.status_code == 409
    assert "runs in a container" in responseHttp.json()["detail"]
    assert listSaves == []


def testEndingSupervisionOnHostRefusesWhenItIsNotOn(
    tmp_path, fixtureCarrierStoodDown,
):
    import json
    os.makedirs(registryManager._S_REGISTRY_DIRECTORY, exist_ok=True)
    with open(registryManager._S_REGISTRY_PATH, "w") as fileHandle:
        json.dump({"listProjects": [{
            "sName": S_CONTAINER_ID, "sDirectory": str(tmp_path),
            "sConfigPath": "", "sContainerName": "", "sMode": "host",
        }]}, fileHandle)
    dictWorkflow = {"sProjectRepoPath": str(tmp_path), "listSteps": []}
    clientHttp, listSaves = ftBuildClient(dictWorkflow)
    responseHttp = clientHttp.post(fsUrl("/supervision/end-on-host"))
    assert responseHttp.status_code == 409
    assert responseHttp.json()["detail"] == (
        "Supervised mode is not on for this workflow."
    )
    assert listSaves == []


def testHashingAPersonalLayerFileNeedsALabel(fixtureIsolateRegistry):
    sHostPath = os.path.join(fixtureIsolateRegistry, "personalNotes.md")
    with open(sHostPath, "w") as fileHandle:
        fileHandle.write("private instructions\n")
    clientHttp, _ = ftBuildClient({"sProjectRepoPath": S_REPO_PATH})
    responseHttp = clientHttp.post(
        fsUrl("/personal-layer/hash"),
        json={"sHostPath": sHostPath, "sLabel": "   "},
    )
    assert responseHttp.status_code == 400
    assert "non-empty sLabel" in responseHttp.json()["detail"]
    assert sHostPath not in responseHttp.text


def testHashingAPersonalLayerFileReturnsOnlyTheCommitment(
    fixtureIsolateRegistry,
):
    import hashlib
    sHostPath = os.path.join(fixtureIsolateRegistry, "personalNotes.md")
    baContent = b"private instructions\n"
    with open(sHostPath, "wb") as fileHandle:
        fileHandle.write(baContent)
    clientHttp, _ = ftBuildClient({"sProjectRepoPath": S_REPO_PATH})
    responseHttp = clientHttp.post(
        fsUrl("/personal-layer/hash"),
        json={"sHostPath": sHostPath, "sLabel": "globalRules"},
    )
    assert responseHttp.status_code == 200, responseHttp.text
    dictCommitment = responseHttp.json()["dictHashCommitment"]
    assert dictCommitment["sSha256"] == hashlib.sha256(baContent).hexdigest()
    assert dictCommitment["iByteCount"] == len(baContent)
    assert sHostPath not in responseHttp.text


@pytest.mark.skipif(
    hasattr(os, "geteuid") and os.geteuid() == 0,
    reason="root reads a mode-000 file, so the refusal cannot be provoked",
)
def testAnUnreadablePersonalLayerFileNeverEchoesItsPath(
    fixtureIsolateRegistry,
):
    sHostPath = os.path.join(fixtureIsolateRegistry, "lockedNotes.md")
    with open(sHostPath, "w") as fileHandle:
        fileHandle.write("private instructions\n")
    os.chmod(sHostPath, 0)
    try:
        clientHttp, _ = ftBuildClient({"sProjectRepoPath": S_REPO_PATH})
        responseHttp = clientHttp.post(
            fsUrl("/personal-layer/hash"),
            json={"sHostPath": sHostPath, "sLabel": "globalRules"},
        )
    finally:
        os.chmod(sHostPath, stat.S_IRUSR | stat.S_IWUSR)
    assert responseHttp.status_code == 400
    assert responseHttp.json()["detail"] == "Could not read the file."
    assert fixtureIsolateRegistry not in responseHttp.text


def testTheContextPathIsJoinedUnderTheRepository():
    assert replayRoutes._fsContextAbsolutePath(
        {"sProjectRepoPath": S_REPO_PATH},
    ) == posixpath.join(S_REPO_PATH, ".vaibify", "AGENTS.md")
