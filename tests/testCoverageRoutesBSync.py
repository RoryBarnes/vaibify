"""Coverage of the sync routes' refusals, fallbacks and best-effort paths.

The remotes (Zenodo, Overleaf, GitHub) are stubbed at the one call that
would leave the machine -- the dispatcher's archive upload, the mirror
listing, the remote-verify fetch -- and nothing else: the promotion
guards, the path validators, the credential roll-back ladder, the
ephemeral-file sweep and the carried-error translation all run for
real. The container id differs from every name a route could confuse
it with, the registry and ``~`` both live under ``tmp_path``, and the
Docker daemon is reached only through the exec double below.
"""

import asyncio
import json
import os
import time
from types import SimpleNamespace

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from tests.carrierStandDown import fnStandCarrierDown
from vaibify.config import registryManager
from vaibify.docker import containerManager
from vaibify.gui import syncDispatcher
from vaibify.gui.routes import syncRoutes


S_CONTAINER_ID = "containerIdSyncPanel"
S_TOKEN_SECRET = "tokenSecretValue"


@pytest.fixture(autouse=True)
def fixtureIsolateHostState(tmp_path, monkeypatch):
    """Point ``~``, the registry and the isolation probe off the machine."""
    sHome = str(tmp_path / "researcherHome")
    os.makedirs(sHome, exist_ok=True)
    monkeypatch.setenv("HOME", sHome)
    sRegistryDirectory = os.path.join(sHome, ".vaibify")
    monkeypatch.setattr(
        registryManager, "_S_REGISTRY_DIRECTORY", sRegistryDirectory,
    )
    monkeypatch.setattr(
        registryManager, "_S_REGISTRY_PATH",
        os.path.join(sRegistryDirectory, "registry.json"),
    )
    monkeypatch.setattr(
        containerManager, "fbContainerIsNetworkIsolated",
        lambda sContainerId: False,
    )
    return sHome


class SyncExecDouble:
    """The Docker exec boundary: existence probes and blob hashing."""

    def __init__(self, dictBlobShas=None, bRaiseOnExec=False):
        self.dictBlobShas = dict(dictBlobShas or {})
        self.bRaiseOnExec = bRaiseOnExec
        self.listProbedPaths = []
        self.listCommands = []

    def flistContainerPathsExist(self, sContainerId, listPaths):
        self.listProbedPaths.extend(listPaths)
        return [True for _ in listPaths]

    def ftResultExecuteCommand(self, sContainerId, sCommand):
        self.listCommands.append(sCommand)
        if self.bRaiseOnExec:
            raise RuntimeError("daemon connection reset")
        if "hashlib" in sCommand:
            return (0, json.dumps(self.dictBlobShas))
        return (0, "")


def fdictSandboxWorkflow(sProjectRepo):
    """Return a workflow whose primary Zenodo deposit is a sandbox one."""
    return {
        "sProjectRepoPath": sProjectRepo,
        "listSteps": [],
        "dictSyncStatus": {},
        "sZenodoService": "sandbox",
        "sZenodoDepositionId": "551",
        "sZenodoLatestDoi": "10.5072/zenodo.551",
        "sZenodoLatestUrl": "https://sandbox.zenodo.org/record/551",
        "dictRemotes": {"zenodo": {
            "sRecordId": "551",
            "sDoi": "10.5072/zenodo.551",
            "sService": "sandbox",
        }},
    }


def ftBuildClient(monkeypatch, dockerDouble, dictWorkflow):
    """Return ``(client, dictCtx, listSaved)`` over the sync routes."""
    fnStandCarrierDown(monkeypatch, syncRoutes)
    app = FastAPI()
    app.state.listLifespanStartup = []
    app.state.listLifespanShutdown = []
    listSaved = []
    dictCtx = {
        "docker": dockerDouble,
        "workflows": {S_CONTAINER_ID: dictWorkflow},
        "paths": {},
        "require": lambda *aArgs: None,
        "save": lambda sId, dictWf: listSaved.append(sId),
        "variables": lambda sId: {},
        "workflowDir": lambda sId: dictWorkflow["sProjectRepoPath"],
    }
    syncRoutes.fnRegisterAll(app, dictCtx)
    return TestClient(app), dictCtx, listSaved


# ── Zenodo promotion: refusals and the failed-publish path ──


def testPromotingAProductionDepositIsRefusedByName(monkeypatch, tmp_path):
    dictWorkflow = fdictSandboxWorkflow(str(tmp_path / "projectRepo"))
    dictWorkflow["dictRemotes"]["zenodo"]["sService"] = "zenodo"
    clientSync, _, listSaved = ftBuildClient(
        monkeypatch, SyncExecDouble(), dictWorkflow,
    )
    responseHttp = clientSync.post(
        f"/api/zenodo/{S_CONTAINER_ID}/promote", json={"listFilePaths": []},
    )
    assert responseHttp.status_code == 409
    assert "already on production" in responseHttp.json()["detail"]
    assert listSaved == []


def testPromotingBesideDeclaredRecordsNamesThem(monkeypatch, tmp_path):
    dictWorkflow = fdictSandboxWorkflow(str(tmp_path / "projectRepo"))
    dictWorkflow["dictRemotes"]["zenodo"]["listRecords"] = [
        {"sRecordId": "record7042"},
    ]
    clientSync, _, listSaved = ftBuildClient(
        monkeypatch, SyncExecDouble(), dictWorkflow,
    )
    responseHttp = clientSync.post(
        f"/api/zenodo/{S_CONTAINER_ID}/promote", json={"listFilePaths": []},
    )
    assert responseHttp.status_code == 409
    sDetail = responseHttp.json()["detail"]
    assert "record7042" in sDetail
    assert "Nothing was published" in sDetail
    assert listSaved == []


def testAFailedPromotionPersistsNothing(monkeypatch, tmp_path):
    """A refused upload leaves the sandbox record exactly as it was."""
    sRepo = str(tmp_path / "projectRepo")
    dictWorkflow = fdictSandboxWorkflow(sRepo)
    listUploads = []

    def ftRefusedUpload(
        connectionDocker, sContainerId, sService, listFilePaths,
        dictMetadata, iParentDepositId,
    ):
        listUploads.append((sService, list(listFilePaths), iParentDepositId))
        return (1, "HTTP 401 Unauthorized: the token was rejected")

    monkeypatch.setattr(
        syncDispatcher, "ftResultArchiveToZenodo", ftRefusedUpload,
    )
    clientSync, _, listSaved = ftBuildClient(
        monkeypatch, SyncExecDouble(), dictWorkflow,
    )
    responseHttp = clientSync.post(
        f"/api/zenodo/{S_CONTAINER_ID}/promote",
        json={"listFilePaths": ["stepAlpha/dataFile.csv"]},
    )
    assert responseHttp.status_code == 200
    assert responseHttp.json()["bSuccess"] is False
    assert listUploads == [
        ("zenodo", [sRepo + "/stepAlpha/dataFile.csv"], 0),
    ]
    assert listSaved == []
    assert "dictSuperseded" not in dictWorkflow["dictRemotes"]["zenodo"]
    assert dictWorkflow["dictRemotes"]["zenodo"]["sService"] == "sandbox"


def testAnEmptySelectionPromotesTheCollectedUnion(monkeypatch, tmp_path):
    """The server, not the caller, decides what a promotion publishes."""
    dictWorkflow = fdictSandboxWorkflow(str(tmp_path / "projectRepo"))
    listSelections = []

    def ftRefusedUpload(
        connectionDocker, sContainerId, sService, listFilePaths,
        dictMetadata, iParentDepositId,
    ):
        listSelections.append(list(listFilePaths))
        return (1, "refused")

    def flistFakeCandidates(*aArgs, **dictArgs):
        return [{"sPath": "stepAlpha/dataFile.csv"}]

    monkeypatch.setattr(
        syncDispatcher, "ftResultArchiveToZenodo", ftRefusedUpload,
    )
    monkeypatch.setattr(
        syncDispatcher, "flistCollectZenodoArchiveCandidates",
        flistFakeCandidates,
    )
    clientSync, _, _ = ftBuildClient(
        monkeypatch, SyncExecDouble(), dictWorkflow,
    )
    responseHttp = clientSync.post(
        f"/api/zenodo/{S_CONTAINER_ID}/promote", json={"listFilePaths": []},
    )
    assert responseHttp.status_code == 200
    assert listSelections == [
        [dictWorkflow["sProjectRepoPath"] + "/stepAlpha/dataFile.csv"],
    ]


def testASuccessfulPromotionRetiresTheSandboxRecord(monkeypatch, tmp_path):
    sRepo = str(tmp_path / "projectRepo")
    dictWorkflow = fdictSandboxWorkflow(sRepo)
    sZenodoResult = json.dumps({
        "iDepositId": 7001, "sDoi": "10.5281/zenodo.7001",
        "sConceptDoi": "10.5281/zenodo.7000",
        "sHtmlUrl": "https://zenodo.org/record/7001",
    })

    def ftPublishedUpload(*aArgs, **dictArgs):
        return (0, "uploaded\nZENODO_RESULT=" + sZenodoResult + "\n")

    def fdictNoNetwork(*aArgs, **dictArgs):
        raise OSError("the test lane has no network")

    from vaibify.reproducibility import scheduledReverify
    monkeypatch.setattr(
        syncDispatcher, "ftResultArchiveToZenodo", ftPublishedUpload,
    )
    monkeypatch.setattr(
        scheduledReverify, "_fdictFetchZenodoHashes", fdictNoNetwork,
    )
    dockerDouble = SyncExecDouble(
        dictBlobShas={"stepAlpha/dataFile.csv": "a" * 40},
    )
    clientSync, _, listSaved = ftBuildClient(
        monkeypatch, dockerDouble, dictWorkflow,
    )
    responseHttp = clientSync.post(
        f"/api/zenodo/{S_CONTAINER_ID}/promote",
        json={"listFilePaths": ["stepAlpha/dataFile.csv"]},
    )
    assert responseHttp.status_code == 200, responseHttp.text
    dictBody = responseHttp.json()
    assert dictBody["bSuccess"] is True
    assert dictBody["sDoi"] == "10.5281/zenodo.7001"
    dictZenodo = dictWorkflow["dictRemotes"]["zenodo"]
    assert dictZenodo["dictSuperseded"]["sDoi"] == "10.5072/zenodo.551"
    assert dictZenodo["sService"] == "zenodo"
    assert listSaved == [S_CONTAINER_ID]


def testStartNewConceptClearsThePrimaryAndSaves(monkeypatch, tmp_path):
    dictWorkflow = fdictSandboxWorkflow(str(tmp_path / "projectRepo"))
    clientSync, _, listSaved = ftBuildClient(
        monkeypatch, SyncExecDouble(), dictWorkflow,
    )
    responseHttp = clientSync.post(
        f"/api/zenodo/{S_CONTAINER_ID}/start-new-concept",
    )
    assert responseHttp.status_code == 200
    assert responseHttp.json() == {
        "bCleared": True, "sTargetService": "sandbox",
    }
    dictZenodo = dictWorkflow["dictRemotes"]["zenodo"]
    assert "sRecordId" not in dictZenodo
    assert dictZenodo["dictSuperseded"]["sRecordId"] == "551"
    assert listSaved == [S_CONTAINER_ID]


def testRetiringWithNoRecordLeavesNoSupersededNote():
    dictWorkflow = {"dictRemotes": {"zenodo": {"sService": "sandbox"}}}
    syncRoutes._fnRetireSupersededZenodoRecord(dictWorkflow)
    assert "dictSuperseded" not in dictWorkflow["dictRemotes"]["zenodo"]


def testZenodoRecordsListingNamesThePrimary(monkeypatch, tmp_path):
    dictWorkflow = fdictSandboxWorkflow(str(tmp_path / "projectRepo"))
    clientSync, _, _ = ftBuildClient(
        monkeypatch, SyncExecDouble(), dictWorkflow,
    )
    responseHttp = clientSync.get(f"/api/zenodo/{S_CONTAINER_ID}/records")
    assert responseHttp.status_code == 200
    assert responseHttp.json()["sPrimaryRecordId"] == "551"


def testPostArchiveDigestsNeedAProjectRepo():
    dockerDouble = SyncExecDouble()
    dictDigests = syncRoutes._fdictComputePostArchiveZenodoDigests(
        {"docker": dockerDouble}, S_CONTAINER_ID,
        {"sProjectRepoPath": ""}, ["stepAlpha/dataFile.csv"],
    )
    assert dictDigests == {}
    assert dockerDouble.listCommands == []


# ── Overleaf ──


def testManuscriptPullWithoutARepositoryPathIs409(monkeypatch, tmp_path):
    dictWorkflow = {
        "sProjectRepoPath": "", "listSteps": [],
        "sOverleafProjectId": "overleafProjectAlpha",
    }
    clientSync, _, _ = ftBuildClient(
        monkeypatch, SyncExecDouble(), dictWorkflow,
    )
    responseHttp = clientSync.post(
        f"/api/overleaf/{S_CONTAINER_ID}/pull-manuscript",
    )
    assert responseHttp.status_code == 409
    assert responseHttp.json()["detail"] == (
        "The project has no repository path."
    )


def testAnAbsentMirrorIsRefreshedOnceThenFiltered(monkeypatch):
    from vaibify.reproducibility import overleafMirror
    listAnswers = [[], [
        {"sPath": "main.tex", "sType": "blob"},
        {"sPath": "figures", "sType": "tree"},
        {"sPath": "figures/plotAlpha.pdf", "sType": "blob"},
        {"sPath": "references.bib", "sType": "blob"},
    ]]
    listRefreshed = []
    monkeypatch.setattr(
        overleafMirror, "flistListMirrorTree",
        lambda sProjectId: listAnswers.pop(0),
    )
    dispatcherDouble = SimpleNamespace(
        ftRefreshOverleafMirror=lambda sProjectId: (
            listRefreshed.append(sProjectId) or (True, {})
        ),
    )
    listPaths = syncRoutes._flistManuscriptMirrorPaths(
        dispatcherDouble, "overleafProjectAlpha",
    )
    assert listPaths == ["main.tex", "references.bib"]
    assert listRefreshed == ["overleafProjectAlpha"]


def testOverleafPushWithoutARequestRunsTheLegacyLane():
    listCalls = []
    dispatcherDouble = SimpleNamespace(
        ftResultPushToOverleaf=lambda *aArgs, **dictArgs: (
            listCalls.append((aArgs[1], dictArgs["sMirrorSha"])) or (0, "ok")
        ),
    )
    tResult = asyncio.run(syncRoutes._ftRunOverleafPushCall(
        dispatcherDouble, SyncExecDouble(), S_CONTAINER_ID,
        ["figures/plotAlpha.pdf"], "mirrorShaValue", {},
    ))
    assert tResult == (0, "ok")
    assert listCalls == [(S_CONTAINER_ID, "mirrorShaValue")]


def testOverleafPushWithNoOwnerRecordIs403():
    listCalls = []
    dispatcherDouble = SimpleNamespace(
        ftResultPushToOverleaf=lambda *aArgs, **dictArgs: listCalls.append(1),
    )
    requestStandIn = SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(dictContainerOwners={})),
        headers={},
    )
    with pytest.raises(HTTPException) as excInfo:
        asyncio.run(syncRoutes._ftRunOverleafPushCall(
            dispatcherDouble, SyncExecDouble(), S_CONTAINER_ID,
            ["figures/plotAlpha.pdf"], "mirrorShaValue", {},
            requestHttp=requestStandIn,
        ))
    assert excInfo.value.status_code == 403
    assert "claim or connect first" in excInfo.value.detail
    assert listCalls == []


def testPushProvenanceWithoutARepoRecordsNothing():
    dockerDouble = SyncExecDouble()
    dictWorkflow = {"sProjectRepoPath": "", "dictRemotes": {}}
    syncRoutes._fnRecordPushProvenance(
        {"docker": dockerDouble}, S_CONTAINER_ID, dictWorkflow,
        ["figures/plotAlpha.pdf"], "figures",
    )
    assert dictWorkflow["dictRemotes"] == {}
    assert dockerDouble.listCommands == []


def testAFailedProvenanceRecordNeverFailsThePush(caplog):
    dockerDouble = SyncExecDouble(bRaiseOnExec=True)
    dictWorkflow = {"sProjectRepoPath": "/workspace/projectAlpha"}
    with caplog.at_level("WARNING", logger="vaibify"):
        syncRoutes._fnRecordPushProvenance(
            {"docker": dockerDouble}, S_CONTAINER_ID, dictWorkflow,
            ["figures/plotAlpha.pdf"], "figures",
        )
    assert "dictRemotes" not in dictWorkflow
    assert any(
        "provenance recording failed" in recordLog.getMessage()
        for recordLog in caplog.records
    )


def fsWriteMirrorGitFile(sHome, sProjectId, sName, fMtime):
    """Create one file under a fake mirror's ``.git`` with a fixed mtime."""
    sGitDir = os.path.join(
        sHome, ".vaibify", "overleaf-mirrors", sProjectId, ".git",
    )
    os.makedirs(sGitDir, exist_ok=True)
    sPath = os.path.join(sGitDir, sName)
    with open(sPath, "w") as fileHandle:
        fileHandle.write("ref\n")
    os.utime(sPath, (fMtime, fMtime))
    return sPath


def testMirrorRefreshedAtFallsBackToHeadWithoutAFetch(fixtureIsolateHostState):
    fsWriteMirrorGitFile(
        fixtureIsolateHostState, "overleafProjectAlpha", "HEAD", 86400.0,
    )
    assert syncRoutes._fsReadMirrorRefreshedAt("overleafProjectAlpha") == (
        "1970-01-02T00:00:00Z"
    )


def testMirrorRefreshedAtPrefersTheFetchStamp(fixtureIsolateHostState):
    fsWriteMirrorGitFile(
        fixtureIsolateHostState, "overleafProjectAlpha", "HEAD", 86400.0,
    )
    fsWriteMirrorGitFile(
        fixtureIsolateHostState, "overleafProjectAlpha", "FETCH_HEAD",
        2 * 86400.0,
    )
    assert syncRoutes._fsReadMirrorRefreshedAt("overleafProjectAlpha") == (
        "1970-01-03T00:00:00Z"
    )


def testAMirrorNeverCreatedHasNoRefreshStamp():
    assert syncRoutes._fsReadMirrorRefreshedAt("overleafProjectAbsent") == ""


# ── GitHub push helpers ──


def testAnEmptyPushSelectionProbesNothing():
    dockerDouble = SyncExecDouble()
    syncRoutes._fnRefuseMissingPushFiles(
        dockerDouble, S_CONTAINER_ID, [], "/workspace/projectAlpha",
    )
    assert dockerDouble.listProbedPaths == []


def testAnInterruptedPushWhoseProbeFailsIsIndeterminate(monkeypatch):
    """A probe that cannot run must never be reported as a landed push."""
    from vaibify.gui import containerGit
    listSleeps = []
    monkeypatch.setattr(containerGit.time, "sleep", listSleeps.append)
    dockerDouble = SyncExecDouble(bRaiseOnExec=True)
    dictResult = syncRoutes._fdictResolveInterruptedPush(
        {"docker": dockerDouble}, S_CONTAINER_ID, "/workspace/projectAlpha",
    )
    assert dictResult["bSuccess"] is False
    assert dictResult["sErrorType"] == "indeterminate"
    assert len(dockerDouble.listCommands) == 3
    assert len(listSleeps) == 2


def testAnInterruptedPushTheProbeProvesLandedIsASuccess():
    """Zero commits ahead of the upstream is the one proof of a landed push."""

    class ProbeDouble(SyncExecDouble):
        def ftResultExecuteCommand(self, sContainerId, sCommand):
            self.listCommands.append(sCommand)
            return (0, "abcdef0123456789\n0\t0\n")

    dictResult = syncRoutes._fdictResolveInterruptedPush(
        {"docker": ProbeDouble()}, S_CONTAINER_ID, "/workspace/projectAlpha",
    )
    assert dictResult["bSuccess"] is True
    assert "confirmed by repository probe" in dictResult["sOutput"]


@pytest.mark.parametrize("sName, sEmail, sField", [
    ("", "researcher@example.org", "sName"),
    ("   ", "researcher@example.org", "sName"),
    ("Researcher Alpha", "", "sEmail"),
])
def testGitIdentityRefusesAnEmptyField(sName, sEmail, sField):
    with pytest.raises(HTTPException) as excInfo:
        syncRoutes._fnValidateGitIdentity(sName, sEmail)
    assert excInfo.value.status_code == 400
    assert excInfo.value.detail == f"{sField} must be a non-empty string."


# ── Credential roll-back ladder ──


class CredentialDispatcherDouble:
    """Container keyring operations that fail like an unreachable daemon.

    The slot naming is the real dispatcher's, so a wrong instance name
    would still be caught by ``fsZenodoTokenNameForInstance``.
    """

    fsZenodoTokenNameForInstance = staticmethod(
        syncDispatcher.fsZenodoTokenNameForInstance,
    )

    def __init__(self, bCopyRaises=True, bDeleteRaises=True):
        self.bCopyRaises = bCopyRaises
        self.bDeleteRaises = bDeleteRaises
        self.listDeleted = []
        self.listCopied = []

    def fnDeleteCredentialForProject(self, connectionDocker, sId, sSlot):
        self.listDeleted.append(sSlot)
        if self.bDeleteRaises:
            raise RuntimeError("daemon unreachable")

    def fbCopyCredentialForProject(self, connectionDocker, sId, sFrom, sTo):
        self.listCopied.append((sFrom, sTo))
        if self.bCopyRaises:
            raise RuntimeError("daemon unreachable")
        return True


def testCleanupTargetsTheInstanceSlotAndSwallowsFailure():
    dispatcherDouble = CredentialDispatcherDouble()
    syncRoutes._fnCleanupCredential(
        dispatcherDouble, SyncExecDouble(), S_CONTAINER_ID, "zenodo",
        "production",
    )
    assert dispatcherDouble.listDeleted == ["zenodo_token_production"]


def testASnapshotThatCannotBeCopiedHasNoBackupSlot():
    dispatcherDouble = CredentialDispatcherDouble()
    tSlots = syncRoutes._ftSnapshotContainerCredential(
        dispatcherDouble, {"docker": SyncExecDouble()}, S_CONTAINER_ID,
        "zenodo", "sandbox",
    )
    assert tSlots == ("zenodo_token_sandbox", None)


def testDroppingASnapshotSwallowsADaemonFailure():
    dispatcherDouble = CredentialDispatcherDouble()
    syncRoutes._fnDropContainerSnapshot(
        dispatcherDouble, {"docker": SyncExecDouble()}, S_CONTAINER_ID,
        "zenodo_token_sandbox_backup",
    )
    assert dispatcherDouble.listDeleted == ["zenodo_token_sandbox_backup"]


def testAnOverleafRestoreThatFailsSaysTheTokenWasNotRestored(monkeypatch):
    """The disposition must not claim a restore that did not happen."""
    from vaibify.config import secretManager

    def fnKeyringRefuses(sName, sValue, sBackend):
        raise RuntimeError("keyring locked")

    monkeypatch.setattr(secretManager, "fnStoreSecret", fnKeyringRefuses)
    dispatcherDouble = CredentialDispatcherDouble()
    dictResult = {"sMessage": "Validation failed"}
    syncRoutes._fnRollBackFailedCredential(
        dispatcherDouble, {"docker": SyncExecDouble()}, S_CONTAINER_ID,
        "overleaf", "", "previousTokenValue", dictResult,
    )
    assert dictResult["sMessage"] == (
        "Validation failed — the entered token was not saved"
    )
    assert "previousTokenValue" not in dictResult["sMessage"]


# ── Verify, arXiv and destination validation ──


def testAMissingManifestIsA409NamingTheRemedy():
    with pytest.raises(HTTPException) as excInfo:
        syncRoutes._fnRaiseVerifyError(FileNotFoundError("gone"), "github")
    assert excInfo.value.status_code == 409
    assert "MANIFEST.sha256 is missing" in excInfo.value.detail


def testAnAmbiguousArxivMatchIsA409NotARemoteFailure():
    """A path map the researcher can fix is not reported as a 502."""
    from vaibify.reproducibility import arxivClient
    errorAmbiguous = arxivClient.ArxivAmbiguousMatchError(
        "plotAlpha.pdf matches figures/plotAlpha.pdf and old/plotAlpha.pdf",
    )
    with pytest.raises(HTTPException) as excInfo:
        syncRoutes._fnRaiseVerifyError(errorAmbiguous, "arxiv")
    assert excInfo.value.status_code == 409
    assert "old/plotAlpha.pdf" in excInfo.value.detail


def testAShapeInvalidVerifyInputIsA422WithTheCredentialRedacted():
    errorShape = ValueError(
        "bad remote https://researcher:" + S_TOKEN_SECRET
        + "@github.com/owner/repository.git",
    )
    with pytest.raises(HTTPException) as excInfo:
        syncRoutes._fnRaiseVerifyError(errorShape, "github")
    assert excInfo.value.status_code == 422
    assert excInfo.value.detail.startswith("Verify input invalid for github")
    assert S_TOKEN_SECRET not in excInfo.value.detail


def testAnHttpRefusalFromTheVerifyIsRaisedAsItself(monkeypatch, tmp_path):
    def fdictRefusingVerify(dictWorkflow, sService, filesRepo):
        raise HTTPException(409, "the arXiv id names no e-print")

    monkeypatch.setattr(
        syncRoutes, "fdictRunRemoteVerifyBlocking", fdictRefusingVerify,
    )
    dictWorkflow = {"sProjectRepoPath": str(tmp_path), "listSteps": []}
    clientSync, dictCtx, _ = ftBuildClient(
        monkeypatch, SyncExecDouble(), dictWorkflow,
    )
    responseHttp = clientSync.post(
        f"/api/sync/{S_CONTAINER_ID}/arxiv/verify",
    )
    assert responseHttp.status_code == 409
    assert responseHttp.json()["detail"] == "the arXiv id names no e-print"
    assert "dictSyncEpochs" not in dictCtx


def testReverifyScheduleSaysWhenItNeverRan(monkeypatch, tmp_path):
    dictWorkflow = {"sProjectRepoPath": str(tmp_path), "listSteps": []}
    clientSync, _, _ = ftBuildClient(
        monkeypatch, SyncExecDouble(), dictWorkflow,
    )
    responseHttp = clientSync.get(
        f"/api/sync/{S_CONTAINER_ID}/reverify-schedule",
    )
    assert responseHttp.status_code == 200
    dictBody = responseHttp.json()
    assert dictBody["bEverRan"] is False
    assert dictBody["sLastReverifyIso"] == ""


def testReverifyScheduleReportsTheRecordedPass(
    monkeypatch, tmp_path, fixtureIsolateHostState,
):
    sStateDirectory = os.path.join(fixtureIsolateHostState, ".vaibify")
    os.makedirs(sStateDirectory, exist_ok=True)
    with open(os.path.join(sStateDirectory, "reverifyState.json"), "w") as fileHandle:
        json.dump({"sLastReverifyIso": "2026-01-02T03:04:05Z"}, fileHandle)
    dictWorkflow = {"sProjectRepoPath": str(tmp_path), "listSteps": []}
    clientSync, _, _ = ftBuildClient(
        monkeypatch, SyncExecDouble(), dictWorkflow,
    )
    dictBody = clientSync.get(
        f"/api/sync/{S_CONTAINER_ID}/reverify-schedule",
    ).json()
    assert dictBody["bEverRan"] is True
    assert dictBody["sLastReverifyIso"] == "2026-01-02T03:04:05Z"


def testArxivPathMapMustBeAnObject():
    with pytest.raises(HTTPException) as excInfo:
        syncRoutes._fnValidateArxivPathMap(["figures/plotAlpha.pdf"])
    assert excInfo.value.status_code == 400
    assert "must be a JSON object" in excInfo.value.detail


def testArxivPathMapRefusesAnEmptyValue():
    with pytest.raises(HTTPException) as excInfo:
        syncRoutes._fnValidateArxivPathMap({"figures/plotAlpha.pdf": ""})
    assert excInfo.value.status_code == 400
    assert excInfo.value.detail == (
        "dictPathMap value must be a non-empty string."
    )


class LockFailingRepoFiles:
    """A repo adapter whose file lock cannot be taken."""

    def flockAcquireForFile(self, sRelPath):
        raise OSError("read-only file system")


def testAnArxivCacheThatCannotBeClearedReturnsTheError():
    sError = syncRoutes._fsClearArxivSyncCache(LockFailingRepoFiles())
    assert sError == "read-only file system"


# ── Startup sweep of ephemeral credential files ──


def fsWriteStaleEphemeralFile(sHome, sName):
    """Write a credential file a week and a day old; return its path."""
    sRoot = os.path.join(sHome, ".vaibify", "tmp")
    os.makedirs(sRoot, exist_ok=True)
    sPath = os.path.join(sRoot, sName)
    with open(sPath, "w") as fileHandle:
        fileHandle.write("stale credential\n")
    fOld = time.time() - 8 * 24 * 60 * 60
    os.utime(sPath, (fOld, fOld))
    return sPath


def testTheStartupSweepSparesMountedFilesOnly(
    monkeypatch, tmp_path, fixtureIsolateHostState,
):
    sMounted = fsWriteStaleEphemeralFile(
        fixtureIsolateHostState, "mountedSecret",
    )
    sOrphan = fsWriteStaleEphemeralFile(
        fixtureIsolateHostState, "orphanSecret",
    )
    # Shaped like the DockerConnection the hub passes, never like the
    # SDK client: an SDK-shaped double is how the dead sweep stayed green.
    dockerDouble = SyncExecDouble()
    dockerDouble.fsetListMountSourcesOfAllContainers = lambda: {sMounted}
    fnStandCarrierDown(monkeypatch, syncRoutes)
    app = FastAPI()
    app.state.listLifespanStartup = []
    app.state.listLifespanShutdown = []
    syncRoutes._fnRegisterEphemeralSecretSweep(app, {"docker": dockerDouble})
    assert not app.state.listLifespanStartup, "the sweep is a reaper now"
    [(sReaperName, fdictReaper)] = app.state.listRemnantReapers
    assert sReaperName == "ephemeralSecretFiles"
    dictOutcome = fdictReaper({"docker": dockerDouble})
    assert dictOutcome["sOutcome"] == "ran"
    assert os.path.exists(sMounted)
    assert not os.path.exists(sOrphan)
