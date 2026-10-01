"""Coverage of the git panel's status, manifest, commit and merge paths.

Every git command runs inside the container through
``ftResultExecuteCommand``, so the double below is the Docker exec
boundary and nothing else: it answers each command by what the command
IS (the combined status probe, a glob listing, ``git add``, ``git
commit``, ``merge-tree``, ``merge``), and the real ``containerGit``
parsers, ``manifestCheck`` and the route handlers run on its answers.
The container id and the stand-in container name are distinct, so a
route that confused the two would miss the workflow cache.
"""

import json
import os

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from tests.carrierStandDown import fnStandCarrierDown
from vaibify.gui.routes import gitRoutes


S_CONTAINER_ID = "containerIdGitPanel"
S_PROJECT_REPO = "/workspace/projectAlpha"
S_HEAD_SHA = "1111111111111111111111111111111111111111"
S_NEW_HEAD_SHA = "2222222222222222222222222222222222222222"


def fsBuildStatusOutput(sBranch="main", iAhead=0, iBehind=0, listLines=()):
    """Return the combined status probe's output for a real repository."""
    listBody = [
        "__VAIBIFY_HEAD__", S_HEAD_SHA, "__VAIBIFY_STATUS__",
        f"# branch.oid {S_HEAD_SHA}",
        f"# branch.head {sBranch}",
        f"# branch.upstream origin/{sBranch}",
        f"# branch.ab +{iAhead} -{iBehind}",
    ]
    listBody.extend(listLines)
    return "\n".join(listBody) + "\n"


class GitExecDouble:
    """Answer each container git command by its shape; record them all."""

    def __init__(self, sStatusOutput, listVaibifyFiles=(), listRootFiles=()):
        self.sStatusOutput = sStatusOutput
        self.listVaibifyFiles = list(listVaibifyFiles)
        self.listRootFiles = list(listRootFiles)
        self.tAddResult = (0, "")
        self.tCommitResult = (0, "")
        self.tPreviewResult = (0, "treeObjectId")
        self.tMergeResult = (0, "")
        self.listCommands = []
        self.listContainerIds = []

    def ftResultExecuteCommand(self, sContainerId, sCommand):
        self.listCommands.append(sCommand)
        self.listContainerIds.append(sContainerId)
        if "--is-inside-work-tree" in sCommand:
            return (0, self.sStatusOutput)
        if "glob.glob" in sCommand:
            if "zenodo-refs" in sCommand:
                return (0, json.dumps(self.listVaibifyFiles))
            return (0, json.dumps(self.listRootFiles))
        if " add -- " in sCommand:
            return self.tAddResult
        if " commit -m " in sCommand:
            return self.tCommitResult
        if "merge-tree" in sCommand:
            return self.tPreviewResult
        if " merge --no-ff" in sCommand:
            return self.tMergeResult
        if "rev-parse HEAD" in sCommand:
            return (0, S_NEW_HEAD_SHA + "\n")
        return (0, "")


def fdictBuildWorkflow(sProjectRepo=S_PROJECT_REPO):
    """Return a minimal workflow attached (or not) to a project repo."""
    return {
        "sProjectRepoPath": sProjectRepo,
        "listSteps": [],
        "dictSyncStatus": {},
        "dictRemotes": {},
    }


@pytest.fixture(autouse=True)
def fixtureClearFetchCache():
    gitRoutes._DICT_LAST_FETCH.clear()
    yield
    gitRoutes._DICT_LAST_FETCH.clear()


def ftBuildClient(monkeypatch, dockerDouble, dictWorkflow):
    """Return ``(client, dictCtx)`` over the git routes, carrier stood down."""
    fnStandCarrierDown(monkeypatch, gitRoutes)
    app = FastAPI()
    dictCtx = {
        "docker": dockerDouble,
        "workflows": {S_CONTAINER_ID: dictWorkflow},
        "paths": {},
        "require": lambda *aArgs: None,
        "save": lambda sId, dictWf: None,
    }
    gitRoutes.fnRegisterAll(app, dictCtx)
    return TestClient(app), dictCtx


def testStatusReadsTheProjectRepoNotTheWorkspaceRoot(monkeypatch):
    dockerDouble = GitExecDouble(fsBuildStatusOutput(sBranch="featureBranch"))
    clientGit, _ = ftBuildClient(
        monkeypatch, dockerDouble, fdictBuildWorkflow(),
    )
    responseHttp = clientGit.get(f"/api/git/{S_CONTAINER_ID}/status")
    assert responseHttp.status_code == 200
    dictBody = responseHttp.json()
    assert dictBody["bIsRepo"] is True
    assert dictBody["sBranch"] == "featureBranch"
    assert dictBody["sHeadSha"] == S_HEAD_SHA
    assert dockerDouble.listContainerIds == [S_CONTAINER_ID]
    assert f"cd {S_PROJECT_REPO} " in dockerDouble.listCommands[0]


def testManifestCheckWithoutARepoRunsNoGit(monkeypatch):
    dockerDouble = GitExecDouble(fsBuildStatusOutput())
    clientGit, _ = ftBuildClient(
        monkeypatch, dockerDouble, fdictBuildWorkflow(sProjectRepo=""),
    )
    responseHttp = clientGit.get(f"/api/git/{S_CONTAINER_ID}/manifest-check")
    assert responseHttp.status_code == 200
    dictBody = responseHttp.json()
    assert dictBody["bIsRepo"] is False
    assert dictBody["sReason"] == "Workflow is not in a git repository"
    assert dockerDouble.listCommands == []


def testManifestCheckNamesAnUntrackedCanonicalFile(monkeypatch):
    dockerDouble = GitExecDouble(
        fsBuildStatusOutput(listLines=["? requirements.txt"]),
        listVaibifyFiles=[".vaibify/zenodo-refs.json"],
        listRootFiles=["requirements.txt"],
    )
    clientGit, _ = ftBuildClient(
        monkeypatch, dockerDouble, fdictBuildWorkflow(),
    )
    responseHttp = clientGit.get(f"/api/git/{S_CONTAINER_ID}/manifest-check")
    assert responseHttp.status_code == 200
    dictBody = responseHttp.json()
    assert dictBody["bIsRepo"] is True
    assert dictBody["iCanonicalCount"] == 2
    assert [dictEntry["sPath"] for dictEntry in dictBody["listNeedsCommit"]] == [
        "requirements.txt",
    ]


def testManifestCheckOfANonRepositoryListsNothing(monkeypatch):
    dockerDouble = GitExecDouble("__VAIBIFY_NOT_REPO__\n")
    clientGit, _ = ftBuildClient(
        monkeypatch, dockerDouble, fdictBuildWorkflow(),
    )
    responseHttp = clientGit.get(f"/api/git/{S_CONTAINER_ID}/manifest-check")
    assert responseHttp.status_code == 200
    dictBody = responseHttp.json()
    assert dictBody["bIsRepo"] is False
    assert dictBody["sReason"] == "Not a git repository"
    assert not any("glob.glob" in sCommand for sCommand in dockerDouble.listCommands)


def testCommitCanonicalRefusesANonRepository(monkeypatch):
    dockerDouble = GitExecDouble("__VAIBIFY_NOT_REPO__\n")
    clientGit, dictCtx = ftBuildClient(
        monkeypatch, dockerDouble, fdictBuildWorkflow(),
    )
    responseHttp = clientGit.post(
        f"/api/git/{S_CONTAINER_ID}/commit-canonical", json={},
    )
    assert responseHttp.status_code == 409
    assert responseHttp.json()["detail"] == "Workspace is not a git repository."
    assert "dictSyncEpochs" not in dictCtx


def testCommitCanonicalWithNothingToCommitCommitsNothing(monkeypatch):
    dockerDouble = GitExecDouble(
        fsBuildStatusOutput(), listRootFiles=["requirements.txt"],
    )
    clientGit, dictCtx = ftBuildClient(
        monkeypatch, dockerDouble, fdictBuildWorkflow(),
    )
    responseHttp = clientGit.post(
        f"/api/git/{S_CONTAINER_ID}/commit-canonical", json={},
    )
    assert responseHttp.status_code == 200
    assert responseHttp.json() == {
        "bSuccess": True, "sCommitHash": S_HEAD_SHA, "iFilesCommitted": 0,
    }
    assert not any(" commit -m " in s for s in dockerDouble.listCommands)
    assert "dictSyncEpochs" not in dictCtx


def testCommitCanonicalUsesATimestampedDefaultMessage(monkeypatch):
    dockerDouble = GitExecDouble(
        fsBuildStatusOutput(listLines=["? requirements.txt"]),
        listRootFiles=["requirements.txt"],
    )
    clientGit, dictCtx = ftBuildClient(
        monkeypatch, dockerDouble, fdictBuildWorkflow(),
    )
    responseHttp = clientGit.post(
        f"/api/git/{S_CONTAINER_ID}/commit-canonical", json={},
    )
    assert responseHttp.status_code == 200
    assert responseHttp.json()["iFilesCommitted"] == 1
    assert responseHttp.json()["sCommitHash"] == S_NEW_HEAD_SHA
    listCommits = [s for s in dockerDouble.listCommands if " commit -m " in s]
    assert len(listCommits) == 1
    assert "[vaibify] workspace state at " in listCommits[0]
    assert listCommits[0].rstrip().endswith("-- requirements.txt")
    assert dictCtx["dictSyncEpochs"] == {S_CONTAINER_ID: 1}


def testCommitCanonicalReportsAFailedAdd(monkeypatch):
    dockerDouble = GitExecDouble(
        fsBuildStatusOutput(listLines=["? requirements.txt"]),
        listRootFiles=["requirements.txt"],
    )
    dockerDouble.tAddResult = (128, "fatal: index.lock exists\n")
    clientGit, _ = ftBuildClient(
        monkeypatch, dockerDouble, fdictBuildWorkflow(),
    )
    responseHttp = clientGit.post(
        f"/api/git/{S_CONTAINER_ID}/commit-canonical",
        json={"sCommitMessage": "Record the state"},
    )
    assert responseHttp.status_code == 500
    assert responseHttp.json()["detail"] == (
        "git add failed: fatal: index.lock exists"
    )
    assert not any(" commit -m " in s for s in dockerDouble.listCommands)


def testCommitCanonicalReportsAFailedCommit(monkeypatch):
    dockerDouble = GitExecDouble(
        fsBuildStatusOutput(listLines=["? requirements.txt"]),
        listRootFiles=["requirements.txt"],
    )
    dockerDouble.tCommitResult = (1, "Author identity unknown\n")
    clientGit, dictCtx = ftBuildClient(
        monkeypatch, dockerDouble, fdictBuildWorkflow(),
    )
    responseHttp = clientGit.post(
        f"/api/git/{S_CONTAINER_ID}/commit-canonical",
        json={"sCommitMessage": "Record the state"},
    )
    assert responseHttp.status_code == 500
    assert responseHttp.json()["detail"] == (
        "git commit failed: Author identity unknown"
    )
    assert "dictSyncEpochs" not in dictCtx


def testMergeUpstreamRefusesADirtyTree(monkeypatch):
    dockerDouble = GitExecDouble(fsBuildStatusOutput(
        iAhead=1, iBehind=1,
        listLines=[
            "1 .M N... 100644 100644 100644 "
            f"{S_HEAD_SHA} {S_HEAD_SHA} stepAlpha/dataFile.csv",
        ],
    ))
    clientGit, _ = ftBuildClient(
        monkeypatch, dockerDouble, fdictBuildWorkflow(),
    )
    responseHttp = clientGit.post(f"/api/git/{S_CONTAINER_ID}/merge-upstream")
    assert responseHttp.status_code == 200
    dictBody = responseHttp.json()
    assert dictBody["bSuccess"] is False
    assert "stepAlpha/dataFile.csv" in json.dumps(dictBody)
    assert not any("merge" in s for s in dockerDouble.listCommands)


def testMergeUpstreamRefusesAConflictingMerge(monkeypatch):
    dockerDouble = GitExecDouble(fsBuildStatusOutput(iAhead=1, iBehind=2))
    dockerDouble.tPreviewResult = (1, "treeObjectId\nMANIFEST.sha256\n")
    clientGit, _ = ftBuildClient(
        monkeypatch, dockerDouble, fdictBuildWorkflow(),
    )
    responseHttp = clientGit.post(f"/api/git/{S_CONTAINER_ID}/merge-upstream")
    assert responseHttp.status_code == 200
    dictBody = responseHttp.json()
    assert dictBody["sRefusal"] == "merge-would-conflict"
    assert dictBody["dictMergePreview"]["listConflictPaths"] == [
        "MANIFEST.sha256",
    ]
    assert not any(" merge --no-ff" in s for s in dockerDouble.listCommands)


def testMergeUpstreamMergesACleanPreviewAndBumpsTheEpoch(monkeypatch):
    dockerDouble = GitExecDouble(fsBuildStatusOutput(iAhead=1, iBehind=1))
    clientGit, dictCtx = ftBuildClient(
        monkeypatch, dockerDouble, fdictBuildWorkflow(),
    )
    responseHttp = clientGit.post(f"/api/git/{S_CONTAINER_ID}/merge-upstream")
    assert responseHttp.status_code == 200, responseHttp.text
    dictBody = responseHttp.json()
    assert dictBody["bSuccess"] is True
    assert dictBody["sNewHeadSha"] == S_NEW_HEAD_SHA
    assert dictBody["sBranch"] == "main"
    assert any(" merge --no-ff" in s for s in dockerDouble.listCommands)
    assert dictCtx["dictSyncEpochs"] == {S_CONTAINER_ID: 1}
    assert (S_CONTAINER_ID, S_PROJECT_REPO) in gitRoutes._DICT_LAST_FETCH


def testAFailedMergeIsA502WithTheCredentialStripped(monkeypatch):
    dockerDouble = GitExecDouble(fsBuildStatusOutput(iAhead=1, iBehind=1))
    dockerDouble.tMergeResult = (
        1, "fatal: unable to access https://researcher:tokenSecret@"
        "git.example/alpha.git\n",
    )
    clientGit, dictCtx = ftBuildClient(
        monkeypatch, dockerDouble, fdictBuildWorkflow(),
    )
    responseHttp = clientGit.post(f"/api/git/{S_CONTAINER_ID}/merge-upstream")
    assert responseHttp.status_code == 502
    sDetail = responseHttp.json()["detail"]
    assert sDetail.startswith("git merge failed: ")
    assert "https://git.example/alpha.git" in sDetail
    assert "tokenSecret" not in responseHttp.text
    assert "dictSyncEpochs" not in dictCtx


def testMergeUpstreamWithoutAProjectRepoIs409(monkeypatch):
    dockerDouble = GitExecDouble(fsBuildStatusOutput())
    clientGit, _ = ftBuildClient(
        monkeypatch, dockerDouble, fdictBuildWorkflow(sProjectRepo=""),
    )
    responseHttp = clientGit.post(f"/api/git/{S_CONTAINER_ID}/merge-upstream")
    assert responseHttp.status_code == 409
    assert "Project repo not detected" in responseHttp.json()["detail"]
    assert dockerDouble.listCommands == []


def testAFailedFastForwardIsA502WithTheCredentialStripped():
    dockerDouble = GitExecDouble(fsBuildStatusOutput())

    def ftAnswerPull(sContainerId, sCommand):
        return (1, "fatal: https://researcher:tokenSecret@git.example/a.git")

    dockerDouble.ftResultExecuteCommand = ftAnswerPull
    with pytest.raises(gitRoutes.HTTPException) as excInfo:
        gitRoutes._fnRunGitPullFastForwardOrFail(
            dockerDouble, S_CONTAINER_ID, S_PROJECT_REPO,
        )
    assert excInfo.value.status_code == 502
    assert "tokenSecret" not in excInfo.value.detail
    assert excInfo.value.detail.startswith("git pull --ff-only failed: ")


class UnreadableRepoFiles:
    """A repo adapter whose every read fails like an unreadable volume."""

    def fbIsFile(self, sRelPath):
        raise OSError("input/output error")

    def fsReadText(self, sRelPath):
        raise OSError("input/output error")


def testAnUnreadableGithubCacheReadsAsNoVerdict():
    """A cache that cannot be read is not a claim that nothing matches."""
    dictStatus = gitRoutes._fdictLoadCachedGithubStatus(UnreadableRepoFiles())
    assert dictStatus == {}


def frequestStandIn():
    """Return the one attribute path the stood-down save reads off a request."""
    from types import SimpleNamespace
    return SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace()))


def fnWriteGithubCache(pathRepo, dictWorkflow):
    """Write a cached GitHub verify that covered every canonical path."""
    from vaibify.reproducibility import manifestWriter
    listCanonical = manifestWriter.flistCollectCanonicalRepoPaths(dictWorkflow)
    os.makedirs(os.path.join(pathRepo, ".vaibify"), exist_ok=True)
    with open(os.path.join(pathRepo, ".vaibify", "syncStatus.json"), "w") as fileHandle:
        json.dump({"github": {
            "sLastVerified": "2026-01-01T00:00:00Z",
            "iTotalFiles": len(listCanonical),
            "listDiverged": [],
        }}, fileHandle)
    return listCanonical


def testReconcileBookkeepingFailureStillReturnsTheVerify(tmp_path, monkeypatch):
    """A save that fails is logged; the reconciled verify is still returned."""
    fnStandCarrierDown(monkeypatch, gitRoutes)
    sRepo = str(tmp_path / "projectRepo")
    dictWorkflow = fdictBuildWorkflow(sProjectRepo=sRepo)
    dictWorkflow["listSteps"] = [{
        "sName": "stepAlpha", "sDirectory": "stepAlpha",
        "saDataFiles": ["dataFile.csv"], "saPlotFiles": [],
        "saDataCommands": ["python run.py"],
    }]
    listCanonical = fnWriteGithubCache(sRepo, dictWorkflow)
    assert listCanonical, "the fixture must declare a canonical path"
    listSaved = []

    def fnFailingSave(sContainerId, dictWf):
        listSaved.append(sContainerId)
        raise OSError("no space left on device")

    dictCtx = {"paths": {}, "save": fnFailingSave}
    dictStatus = gitRoutes._fdictReconcileSyncStatusFromVerify(
        dictCtx, S_CONTAINER_ID, dictWorkflow, requestHttp=frequestStandIn(),
    )
    assert dictStatus["sLastVerified"] == "2026-01-01T00:00:00Z"
    assert listSaved == [S_CONTAINER_ID]
    assert dictWorkflow["dictSyncStatus"]


def testReconcileReRaisesAControlPlaneRefusal(tmp_path, monkeypatch):
    """A refusal is not a bookkeeping failure and must not be swallowed."""
    from vaibify.config.mutationAdmission import MutationNotAdmittedError
    fnStandCarrierDown(monkeypatch, gitRoutes)
    sRepo = str(tmp_path / "projectRepo")
    dictWorkflow = fdictBuildWorkflow(sProjectRepo=sRepo)
    dictWorkflow["listSteps"] = [{
        "sName": "stepAlpha", "sDirectory": "stepAlpha",
        "saDataFiles": ["dataFile.csv"], "saPlotFiles": [],
        "saDataCommands": ["python run.py"],
    }]
    fnWriteGithubCache(sRepo, dictWorkflow)

    def fnRefusedSave(sContainerId, dictWf):
        raise MutationNotAdmittedError("no live admission")

    with pytest.raises(MutationNotAdmittedError):
        gitRoutes._fdictReconcileSyncStatusFromVerify(
            {"paths": {}, "save": fnRefusedSave},
            S_CONTAINER_ID, dictWorkflow, requestHttp=frequestStandIn(),
        )
