"""The environment-archive routes: refusals, launches, and their workers.

``tests/testEnvironmentArchiveRoutes.py`` pins the answer route's
guardrails on a bare app. This file covers the rest of the module:

* the deposit and promote routes' PREFLIGHT, in the order the promote
  docstring promises -- policy before credentials before bytes -- with
  every refusal asserted by status and by the words the researcher sees;
* the durable LAUNCH, driven over the served application with a real
  lease so the real carrier registers the work (a bare app is refused
  by that same carrier, and that refusal is asserted too);
* the two background WORKERS, called directly, because a response has
  returned long before they finish and a TestClient cannot wait on a
  task the handler detached.

What is stubbed is only what leaves the machine or the hub: Zenodo's
HTTP (``zenodoClient.requests``), the ``docker inspect`` isolation
probe, and the two whole save-and-upload operations the workers hand
off to (``imageDeposit.fdictDepositImageArchive`` and
``archivePromotion.ftPromoteImageArchive``), which run ``docker save``
and a multi-gigabyte upload and are tested in their own modules. HOME
is a scratch directory, because the deposit's scratch space lives
under it.
"""

import asyncio
import json
import os
import shlex

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from tests.testCarrierMigratedRoutes import (
    DockerDoubleThatCallsTheRealGates,
    _tConnectGatedClient,
)
from tests.testDraftRoutes import S_CONTAINER_ID
from vaibify.config import registryManager
from vaibify.docker import containerManager
from vaibify.gui import archiveProgress, routeContext
from vaibify.gui.routes import environmentArchiveRoutes
from vaibify.reproducibility import (
    archivePromotion,
    imageArchive,
    imageDeposit,
    syncBookkeeping,
    zenodoClient,
)
from vaibify.reproducibility.environmentSnapshot import (
    S_SUPERSEDED_ARCHIVE_KEY,
    fdictReadEnvironmentJson,
)
from vaibify.reproducibility.repoFiles import ffilesEnsureRepoFiles


S_BARE_CONTAINER_ID = "containerArchiveFoxtrot"
S_SANDBOX_TOKEN = "sandbox-secret-never-echoed"
S_PRODUCTION_TOKEN = "production-secret-never-echoed"
S_IMAGE_DIGEST = "registry.example/image@sha256:" + "a" * 64
S_WORKFLOW_RELATIVE_PATH = ".vaibify/workflows/stepFlow.json"
S_ENVIRONMENT_CONTAINER_PATH = "/workspace/.vaibify/environment.json"

DICT_SANDBOX_RECORD = {
    "sVersionDoi": "10.5072/zenodo.100",
    "sZenodoService": "sandbox",
    "sProvenance": imageArchive.S_PROVENANCE_ORIGINAL,
}


class _FakeResponse:
    def __init__(self, iStatus, dictBody=None):
        self.status_code = iStatus
        self._dictBody = dictBody if dictBody is not None else {}
        self.text = json.dumps(self._dictBody)
        self.headers = {}

    def json(self):
        return self._dictBody

    def close(self):
        return None


class _FakeZenodoHttp:
    """Answer every GET with one configured status; record each call."""

    def __init__(self):
        self.iStatus = 200
        self.dictBody = []
        self.listRequests = []

    def fresponseGet(self, sUrl, headers=None, **dictKeywords):
        self.listRequests.append((sUrl, dict(headers or {})))
        return _FakeResponse(self.iStatus, self.dictBody)


class _KeyringDocker:
    """A connection whose only reachable surface is the keyring read."""

    def __init__(self, dictSlots=None, bMissing=False):
        self.dictSlots = dictSlots or {}
        self.bMissing = bMissing

    def fsFetchKeyringSecret(self, sContainerId, sSlot):
        if self.bMissing:
            raise LookupError(
                "No Zenodo token is stored in the container keyring; "
                "connect Zenodo from the Repos panel.",
            )
        return self.dictSlots.get(sSlot, "")


class _GatedDockerWithKeyring(DockerDoubleThatCallsTheRealGates):
    """The gated double, plus the keyring and file probes the routes ask."""

    def __init__(self, dictSlots):
        super().__init__()
        self.dictSlots = dictSlots

    def fsFetchKeyringSecret(self, sContainerId, sSlot):
        return self.dictSlots.get(sSlot, "")

    def fbContainerPathIsFile(self, sContainerId, sPath):
        DockerDoubleThatCallsTheRealGates.fbContainerPathIsFile(
            self, sContainerId, sPath,
        )
        return sPath in self._dictFiles

    def ftResultExecuteCommand(self, sContainerId, sCommand, sWorkdir=None):
        tResult = DockerDoubleThatCallsTheRealGates.ftResultExecuteCommand(
            self, sContainerId, sCommand, sWorkdir,
        )
        listWords = shlex.split(sCommand)
        if listWords[:2] == ["mv", "-f"] and len(listWords) == 4 and (
            listWords[2] in self._dictFiles
        ):
            self._dictFiles[listWords[3]] = self._dictFiles.pop(listWords[2])
        return tResult


@pytest.fixture(autouse=True)
def fixtureIsolatedHubState(monkeypatch, tmp_path):
    """Empty registry, empty progress record, scratch HOME, open network."""
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
    monkeypatch.setattr(archiveProgress, "DICT_DEPOSITS", {})
    sHome = str(tmp_path / "researcherHome")
    os.makedirs(sHome)
    monkeypatch.setenv("HOME", sHome)
    monkeypatch.setattr(
        containerManager, "ftProbeNetworkIsolation",
        lambda sContainer: (True, False),
    )
    return sHome


@pytest.fixture
def fakeZenodo(monkeypatch):
    serverFake = _FakeZenodoHttp()
    monkeypatch.setattr(
        zenodoClient.requests, "get", serverFake.fresponseGet,
    )
    return serverFake


def _fdictContainerBlock(dictArchiveRecord=None):
    dictContainer = {"sImageDigest": S_IMAGE_DIGEST, "sArchitecture": "arm64"}
    if dictArchiveRecord is not None:
        dictContainer[imageArchive.S_IMAGE_ARCHIVE_KEY] = dict(
            dictArchiveRecord,
        )
    return dictContainer


@pytest.fixture
def sProjectRepo(tmp_path):
    """A host project repo whose envelope pins an image on the sandbox."""
    sRepo = str(tmp_path / "projectRepo")
    os.makedirs(os.path.join(sRepo, ".vaibify", "workflows"))
    with open(
        os.path.join(sRepo, ".vaibify", "environment.json"), "w",
    ) as fileOut:
        json.dump({"dictContainer": _fdictContainerBlock(
            DICT_SANDBOX_RECORD,
        )}, fileOut)
    return sRepo


def _fclientBare(sProjectRepo, connectionDocker, dictWorkflow=None):
    """The bare-app pattern of the existing route tests; no lease exists."""
    dictWorkflow = dictWorkflow if dictWorkflow is not None else {
        "sProjectRepoPath": sProjectRepo, "listSteps": [],
        "sZenodoService": "sandbox",
    }
    app = FastAPI()
    dictCtx = {
        "docker": connectionDocker,
        "workflows": {S_BARE_CONTAINER_ID: dictWorkflow},
        "paths": {
            S_BARE_CONTAINER_ID:
                sProjectRepo + "/" + S_WORKFLOW_RELATIVE_PATH,
        },
        "require": lambda sContainerId: None,
    }
    environmentArchiveRoutes.fnRegisterAll(app, dictCtx)
    return TestClient(app), dictWorkflow


def _fsBarePath(sAction):
    return (
        f"/api/workflow/{S_BARE_CONTAINER_ID}/environment-archive/"
        + sAction
    )


def _fsServedPath(sAction):
    return f"/api/workflow/{S_CONTAINER_ID}/environment-archive/" + sAction


def _tConnectWithEnvelope(dictSlots, dictContainer=None):
    """Connect the served app with an envelope in the container."""
    connectionDocker = _GatedDockerWithKeyring(dictSlots)
    connectionDocker._dictFiles[S_ENVIRONMENT_CONTAINER_PATH] = json.dumps(
        {"dictContainer": dictContainer or _fdictContainerBlock(
            DICT_SANDBOX_RECORD,
        )},
    ).encode("utf-8")
    client, connectionDocker = _tConnectGatedClient(connectionDocker)
    return client, connectionDocker


def _fnCancelRegisteredWorker(sContainerId):
    """Stop a detached worker the handler launched, so nothing leaks."""
    dictEntry = archiveProgress.DICT_DEPOSITS.get(sContainerId) or {}
    taskWorker = dictEntry.get("task")
    if taskWorker is None or taskWorker.done():
        return
    loopWorker = taskWorker.get_loop()
    if not loopWorker.is_closed():
        loopWorker.call_soon_threadsafe(taskWorker.cancel)


# ── Preflight refusals, before anything leaves the hub ──


def testAWorkflowWithoutAProjectRepoHasNothingToArchive(
    sProjectRepo,
):
    clientTest, _dictWorkflow = _fclientBare(
        sProjectRepo, _KeyringDocker(),
        dictWorkflow={"sProjectRepoPath": "", "listSteps": []},
    )
    responseHttp = clientTest.post(_fsBarePath("deposit"))
    assert responseHttp.status_code == 409
    assert "no project repository" in responseHttp.json()["detail"]


def testASecondDepositWhileOneRunsIsRefused(sProjectRepo):
    archiveProgress.fnRegisterDeposit(S_BARE_CONTAINER_ID, None, sProjectRepo)
    clientTest, _dictWorkflow = _fclientBare(sProjectRepo, _KeyringDocker())
    responseHttp = clientTest.post(_fsBarePath("deposit"))
    assert responseHttp.status_code == 409
    assert "already running" in responseHttp.json()["detail"]


def testAKeyringThatCannotBeReadIsA409WithItsInstruction(
    sProjectRepo,
):
    clientTest, _dictWorkflow = _fclientBare(
        sProjectRepo, _KeyringDocker(bMissing=True),
    )
    responseHttp = clientTest.post(_fsBarePath("deposit"))
    assert responseHttp.status_code == 409
    assert "Repos panel" in responseHttp.json()["detail"]


def testADepositWithNoStoredTokenIsRefusedBeforeAnySave(
    sProjectRepo,
):
    clientTest, _dictWorkflow = _fclientBare(sProjectRepo, _KeyringDocker())
    responseHttp = clientTest.post(_fsBarePath("deposit"))
    assert responseHttp.status_code == 409
    assert "No Zenodo token is stored" in responseHttp.json()["detail"]
    assert archiveProgress.fbDepositIsLive(S_BARE_CONTAINER_ID) is False


def testADepositWithoutALeaseIsRefusedByTheCarrier(sProjectRepo):
    """The token is read, then the carrier refuses an unbound request.

    The token must not appear in the refusal.
    """
    clientTest, _dictWorkflow = _fclientBare(
        sProjectRepo,
        _KeyringDocker({"zenodo_token_sandbox": S_SANDBOX_TOKEN}),
    )
    responseHttp = clientTest.post(_fsBarePath("deposit"))
    assert responseHttp.status_code == 403
    assert "claim or connect" in responseHttp.json()["detail"]
    assert S_SANDBOX_TOKEN not in responseHttp.text
    assert archiveProgress.fbDepositIsLive(S_BARE_CONTAINER_ID) is False


def testAReferencedDoiZenodoCannotServeIsA502NamingIt(
    sProjectRepo, fakeZenodo,
):
    """Zenodo's refusal is the researcher's to read, never a 500."""
    fakeZenodo.iStatus = 404
    fakeZenodo.dictBody = {"message": "PID does not exist."}
    clientTest, _dictWorkflow = _fclientBare(sProjectRepo, _KeyringDocker())
    responseHttp = clientTest.post(_fsBarePath("answer"), json={
        "sAnswer": "referenced", "sVersionDoi": "10.5281/zenodo.9000001",
    })
    assert responseHttp.status_code == 502
    assert "9000001" in responseHttp.json()["detail"]
    assert fakeZenodo.listRequests[0][0].startswith(
        "https://sandbox.zenodo.org/api/records/",
    )


def testAReferenceToADifferentImageIsRefusedWithTheMismatch(
    sProjectRepo, fakeZenodo,
):
    dictRecord = imageArchive.fdictBuildArchiveRecord(
        sVersionDoi="10.5281/zenodo.9000002",
        sConceptDoi="10.5281/zenodo.9000000",
        sTarballSha256="sha256:" + "b" * 64, iTarballBytes=10,
        sDepositedIso="2026-01-01T00:00:00+00:00",
        sProvenance=imageArchive.S_PROVENANCE_ORIGINAL,
        sImageDigest="registry.example/other@sha256:" + "e" * 64,
        sArchitecture="arm64", sTarballName="environment-image.tar.zst",
    )
    fakeZenodo.dictBody = {
        "doi": "10.5281/zenodo.9000002",
        "metadata": {"description": imageArchive.fdictStampDepositMetadata(
            {"sDescription": "An image."}, dictRecord,
        )["sDescription"]},
    }
    clientTest, dictWorkflow = _fclientBare(sProjectRepo, _KeyringDocker())
    responseHttp = clientTest.post(_fsBarePath("answer"), json={
        "sAnswer": "referenced", "sVersionDoi": "10.5281/zenodo.9000002",
    })
    assert responseHttp.status_code == 409
    assert imageArchive.S_IMAGE_ARCHIVE_KEY not in dictWorkflow
    dictStamped = fdictReadEnvironmentJson(
        ffilesEnsureRepoFiles(sProjectRepo),
    )["dictContainer"][imageArchive.S_IMAGE_ARCHIVE_KEY]
    assert dictStamped["sVersionDoi"] == "10.5072/zenodo.100"


def testAnEnvelopeWithoutAnArchitectureIsRefusedBeforeTheSave(
    tmp_path,
):
    """A record that names no platform can never be checked; say so first."""
    sRepo = str(tmp_path / "noArchitecture")
    os.makedirs(os.path.join(sRepo, ".vaibify"))
    with open(os.path.join(sRepo, ".vaibify", "environment.json"), "w") as (
        fileOut
    ):
        json.dump({"dictContainer": {"sImageDigest": S_IMAGE_DIGEST}}, fileOut)
    clientTest, _dictWorkflow = _fclientBare(
        sRepo, _KeyringDocker({"zenodo_token_sandbox": S_SANDBOX_TOKEN}),
    )
    responseHttp = clientTest.post(_fsBarePath("deposit"))
    assert responseHttp.status_code == 409
    assert "not the architecture" in responseHttp.json()["detail"]


# ── Promote preflight: policy, then credentials, then bytes ──


def testAPromotionOnASealedContainerIsRefusedFirst(
    sProjectRepo, monkeypatch, fakeZenodo,
):
    monkeypatch.setattr(
        containerManager, "ftProbeNetworkIsolation",
        lambda sContainer: (True, True),
    )
    clientTest, _dictWorkflow = _fclientBare(sProjectRepo, _KeyringDocker())
    responseHttp = clientTest.post(_fsBarePath("promote"))
    assert responseHttp.status_code == 409
    assert responseHttp.json()["detail"]["sError"] == (
        routeContext.S_ISOLATION_BLOCK_ERROR
    )
    assert fakeZenodo.listRequests == []


@pytest.mark.parametrize("dictRecord, sFragment", [
    ({"sVersionDoi": "10.5281/zenodo.200", "sZenodoService": "zenodo"},
     "already on production Zenodo"),
    ({}, "does not record which Zenodo instance"),
])
def testOnlyAKnownSandboxDepositCanBePromoted(
    tmp_path, fakeZenodo, dictRecord, sFragment,
):
    """Refused on policy before the production token is even read."""
    sRepo = str(tmp_path / "promotedAlready")
    os.makedirs(os.path.join(sRepo, ".vaibify"))
    with open(os.path.join(sRepo, ".vaibify", "environment.json"), "w") as (
        fileOut
    ):
        json.dump({"dictContainer": _fdictContainerBlock(dictRecord)}, fileOut)
    connectionDocker = _KeyringDocker(bMissing=True)
    clientTest, _dictWorkflow = _fclientBare(sRepo, connectionDocker)
    responseHttp = clientTest.post(_fsBarePath("promote"))
    assert responseHttp.status_code == 409
    assert sFragment in responseHttp.json()["detail"]
    assert "Nothing was deposited" in responseHttp.json()["detail"]
    assert fakeZenodo.listRequests == []


def testAMissingProductionTokenIsNamedForTheCredentialPrompt(
    sProjectRepo, fakeZenodo,
):
    """A sandbox token is not this one; the dashboard asks by name."""
    clientTest, _dictWorkflow = _fclientBare(
        sProjectRepo,
        _KeyringDocker({"zenodo_token_sandbox": S_SANDBOX_TOKEN}),
    )
    responseHttp = clientTest.post(_fsBarePath("promote"))
    assert responseHttp.status_code == 409
    dictDetail = responseHttp.json()["detail"]
    assert dictDetail["sError"] == "PRODUCTION-TOKEN-MISSING"
    assert "PRODUCTION Zenodo token" in dictDetail["sMessage"]
    assert S_SANDBOX_TOKEN not in responseHttp.text
    assert fakeZenodo.listRequests == []


def testAKeyringLookupFailureReadsAsAMissingProductionToken(
    sProjectRepo,
):
    clientTest, _dictWorkflow = _fclientBare(
        sProjectRepo, _KeyringDocker(bMissing=True),
    )
    responseHttp = clientTest.post(_fsBarePath("promote"))
    assert responseHttp.status_code == 409
    assert responseHttp.json()["detail"]["sError"] == (
        "PRODUCTION-TOKEN-MISSING"
    )


@pytest.mark.parametrize("iStatus, sFragment", [
    (401, "rejected the stored production token"),
    (500, "could not reach production Zenodo"),
])
def testAProductionTokenThatDoesNotWorkIsRefusedBeforeTheSave(
    sProjectRepo, fakeZenodo, iStatus, sFragment,
):
    """One authenticated read moves the failure ahead of the long half."""
    fakeZenodo.iStatus = iStatus
    fakeZenodo.dictBody = {"message": "refused"}
    clientTest, _dictWorkflow = _fclientBare(
        sProjectRepo,
        _KeyringDocker({"zenodo_token_production": S_PRODUCTION_TOKEN}),
    )
    responseHttp = clientTest.post(_fsBarePath("promote"))
    assert responseHttp.status_code == 409
    assert sFragment in responseHttp.json()["detail"]
    assert S_PRODUCTION_TOKEN not in responseHttp.text
    sUrl, dictHeaders = fakeZenodo.listRequests[0]
    assert sUrl.startswith("https://zenodo.org/api/deposit/depositions")
    assert dictHeaders["Authorization"] == "Bearer " + S_PRODUCTION_TOKEN
    assert archiveProgress.fbDepositIsLive(S_BARE_CONTAINER_ID) is False


# ── Launches through the real carrier, over the served app ──


def testADepositLaunchIsRegisteredAsDurableWork(monkeypatch):
    """The answer returns at once; the progress row shows it starting."""
    monkeypatch.setattr(
        imageDeposit, "fdictDepositImageArchive",
        lambda *tArguments, **dictKeywords: (_ for _ in ()).throw(
            RuntimeError("stopped before the save"),
        ),
    )
    client, _connectionDocker = _tConnectWithEnvelope(
        {"zenodo_token_sandbox": S_SANDBOX_TOKEN},
    )
    try:
        responseHttp = client.post(_fsServedPath("deposit"))
        assert responseHttp.status_code == 200, responseHttp.text
        assert responseHttp.json() == {
            "bAccepted": True, "sPhase": archiveProgress.S_PHASE_STARTING,
        }
        assert S_SANDBOX_TOKEN not in responseHttp.text
        assert archiveProgress.DICT_DEPOSITS[S_CONTAINER_ID][
            "sProjectRepoPath"
        ] == "/workspace"
    finally:
        _fnCancelRegisteredWorker(S_CONTAINER_ID)


def testAPromotionLaunchIsRegisteredAsDurableWork(
    monkeypatch, fakeZenodo,
):
    monkeypatch.setattr(
        archivePromotion, "ftPromoteImageArchive",
        lambda *tArguments, **dictKeywords: (_ for _ in ()).throw(
            RuntimeError("stopped before the save"),
        ),
    )
    client, _connectionDocker = _tConnectWithEnvelope(
        {"zenodo_token_production": S_PRODUCTION_TOKEN},
    )
    try:
        responseHttp = client.post(_fsServedPath("promote"))
        assert responseHttp.status_code == 200, responseHttp.text
        assert responseHttp.json()["bAccepted"] is True
        assert S_PRODUCTION_TOKEN not in responseHttp.text
        assert S_CONTAINER_ID in archiveProgress.DICT_DEPOSITS
    finally:
        _fnCancelRegisteredWorker(S_CONTAINER_ID)


def testClearingTheAnswerRemovesItAndForgetsAFailedAttempt():
    """Absence is what unanswered means; an empty value would not be."""
    client, connectionDocker = _tConnectWithEnvelope({})
    dictWorkflow = client.app.state.dictRouteContext["workflows"][
        S_CONTAINER_ID
    ]
    dictWorkflow[imageArchive.S_IMAGE_ARCHIVE_KEY] = {"sAnswer": "declined"}
    archiveProgress.fnRecordFailure(
        S_CONTAINER_ID, "/workspace", "upload interrupted",
    )
    responseHttp = client.post(
        _fsServedPath("answer"), json={"sAnswer": "cleared"},
    )
    assert responseHttp.status_code == 200, responseHttp.text
    assert responseHttp.json() == {"sAnswer": "cleared"}
    assert imageArchive.S_IMAGE_ARCHIVE_KEY not in dictWorkflow
    assert S_CONTAINER_ID not in archiveProgress.DICT_DEPOSITS
    listSavedWorkflows = [
        json.loads(baContent) for sPath, baContent
        in connectionDocker._dictFiles.items()
        if sPath.endswith("myFlow.json")
    ]
    assert listSavedWorkflows, "the cleared answer was never saved"
    assert imageArchive.S_IMAGE_ARCHIVE_KEY not in listSavedWorkflows[-1]


def testAnAnswerLeavesALiveDepositForTheResearcherToWatch():
    client, _connectionDocker = _tConnectWithEnvelope({})
    archiveProgress.fnRegisterDeposit(S_CONTAINER_ID, None, "/workspace")
    responseHttp = client.post(
        _fsServedPath("answer"), json={"sAnswer": "declined"},
    )
    assert responseHttp.status_code == 200, responseHttp.text
    assert archiveProgress.fbDepositIsLive(S_CONTAINER_ID) is True


# ── The deposit worker ──


def _fdictDepositedRecord(sVersionDoi="10.5072/zenodo.300"):
    return imageArchive.fdictBuildArchiveRecord(
        sVersionDoi=sVersionDoi, sConceptDoi="10.5072/zenodo.299",
        sTarballSha256="sha256:" + "b" * 64, iTarballBytes=2048,
        sDepositedIso="2026-01-02T00:00:00+00:00",
        sProvenance=imageArchive.S_PROVENANCE_ORIGINAL,
        sImageDigest=S_IMAGE_DIGEST, sArchitecture="arm64",
        sTarballName="environment-image.tar.zst", sZenodoService="sandbox",
    )


def testTheDepositWorkerReportsEachPhaseAndStampsTheRecord(
    sProjectRepo, monkeypatch, fixtureIsolatedHubState,
):
    """Saving, uploading and verifying each move the row; then it settles."""
    listPhasesSeen = []
    dictCall = {}

    def fdictDepositRecordingProgress(
        clientZenodo, sImageReference, sArchitecture, sScratchDirectory,
        dictMetadata, fnReportProgress, dictAttestation,
        fnReportUploadStarted=None, fnReportVerifying=None,
    ):
        dictCall.update({
            "sService": clientZenodo.sService,
            "sToken": clientZenodo._sToken,
            "sImageReference": sImageReference,
            "sArchitecture": sArchitecture,
            "sScratchDirectory": sScratchDirectory,
            "dictMetadata": dictMetadata,
        })
        for fnReport, tArguments in (
            (fnReportProgress, (10, 20)),
            (fnReportUploadStarted, (2048,)),
            (fnReportVerifying, ()),
        ):
            fnReport(*tArguments)
            listPhasesSeen.append(
                archiveProgress.DICT_DEPOSITS[S_CONTAINER_ID]["sPhase"],
            )
        return _fdictDepositedRecord()

    monkeypatch.setattr(
        imageDeposit, "fdictDepositImageArchive",
        fdictDepositRecordingProgress,
    )
    archiveProgress.fnRegisterDeposit(S_CONTAINER_ID, None, sProjectRepo)
    dictWorkflow = {"sProjectRepoPath": sProjectRepo, "listSteps": [],
                    "sProjectTitle": "Project Golf"}
    asyncio.run(environmentArchiveRoutes._fnRunDepositWorker(
        S_CONTAINER_ID, dictWorkflow, _fdictContainerBlock(),
        S_SANDBOX_TOKEN, ffilesEnsureRepoFiles(sProjectRepo),
    ))
    assert listPhasesSeen == [
        archiveProgress.S_PHASE_SAVING, archiveProgress.S_PHASE_UPLOADING,
        archiveProgress.S_PHASE_VERIFYING,
    ]
    assert archiveProgress.DICT_DEPOSITS[S_CONTAINER_ID]["sPhase"] == (
        archiveProgress.S_PHASE_SETTLED
    )
    assert dictCall["sService"] == "sandbox"
    assert dictCall["sToken"] == S_SANDBOX_TOKEN
    assert dictCall["sImageReference"] == S_IMAGE_DIGEST
    assert dictCall["dictMetadata"]["sTitle"] == (
        "Container image for Project Golf"
    )
    assert dictCall["sScratchDirectory"].startswith(fixtureIsolatedHubState)
    assert not os.path.exists(dictCall["sScratchDirectory"])
    dictStamped = fdictReadEnvironmentJson(
        ffilesEnsureRepoFiles(sProjectRepo),
    )["dictContainer"][imageArchive.S_IMAGE_ARCHIVE_KEY]
    assert dictStamped["sVersionDoi"] == "10.5072/zenodo.300"


def testAFailedDepositKeepsItsReasonAndRemovesItsScratch(
    sProjectRepo, monkeypatch,
):
    """The response returned minutes ago; the row is the only messenger."""
    listScratch = []

    def fdictFailAfterTheSave(clientZenodo, sImageReference, sArchitecture,
                              sScratchDirectory, *tArguments, **dictKeywords):
        listScratch.append(sScratchDirectory)
        raise zenodoClient.ZenodoAuthError(
            "Zenodo authentication failed (401): token revoked",
        )

    monkeypatch.setattr(
        imageDeposit, "fdictDepositImageArchive", fdictFailAfterTheSave,
    )
    archiveProgress.fnRegisterDeposit(S_CONTAINER_ID, None, sProjectRepo)
    asyncio.run(environmentArchiveRoutes._fnRunDepositWorker(
        S_CONTAINER_ID, {"sProjectRepoPath": sProjectRepo},
        _fdictContainerBlock(), S_SANDBOX_TOKEN,
        ffilesEnsureRepoFiles(sProjectRepo),
    ))
    dictEntry = archiveProgress.DICT_DEPOSITS[S_CONTAINER_ID]
    assert dictEntry["sPhase"] == archiveProgress.S_PHASE_FAILED
    assert dictEntry["sReason"] == (
        "Zenodo authentication failed (401): token revoked"
    )
    assert S_SANDBOX_TOKEN not in dictEntry["sReason"]
    assert not os.path.exists(listScratch[0])


def testAFailureWithNoMessageIsNamedByItsKind():
    assert environmentArchiveRoutes._fsDescribeDepositFailure(
        TimeoutError(),
    ) == "TimeoutError"


@pytest.mark.parametrize("dictWorkflow, sExpectedTitle", [
    ({"dictZenodoMetadata": {"sTitle": "Metadata Title"},
      "sProjectTitle": "Project Title"},
     "Container image for Metadata Title"),
    ({"sWorkflowName": "Workflow Name"}, "Container image for Workflow Name"),
    ({}, "Container image for this project"),
])
def testTheImageDepositIsTitledAfterTheBestNameAvailable(
    dictWorkflow, sExpectedTitle,
):
    dictMetadata = environmentArchiveRoutes._fdictBuildArchiveDepositMetadata(
        dictWorkflow,
    )
    assert dictMetadata["sTitle"] == sExpectedTitle
    assert "docker load" in dictMetadata["sDescription"]


# ── The promotion worker ──


def _fnSeedPendingPromotion(sProjectRepo, sPromotionId):
    syncBookkeeping.fnUpdatePendingPromotion(
        ffilesEnsureRepoFiles(sProjectRepo), S_WORKFLOW_RELATIVE_PATH,
        sPromotionId, archivePromotion.fdictBuildPendingPromotion(
            sPromotionId, archivePromotion.S_LANE_IMAGE, "zenodo", [],
        ),
    )


def _flistPending(sProjectRepo):
    return syncBookkeeping.flistReadPendingPromotions(
        ffilesEnsureRepoFiles(sProjectRepo), S_WORKFLOW_RELATIVE_PATH,
    )


def _fnRunPromotionWorker(sProjectRepo, dictWorkflow):
    filesRepo = ffilesEnsureRepoFiles(sProjectRepo)
    asyncio.run(environmentArchiveRoutes._fnRunPromotionWorker(
        S_CONTAINER_ID, dictWorkflow,
        fdictReadEnvironmentJson(filesRepo)["dictContainer"],
        S_PRODUCTION_TOKEN, filesRepo, S_WORKFLOW_RELATIVE_PATH,
    ))


@pytest.mark.parametrize("bManifestRefreshed", [True, False])
def testThePromotionSettlesItsRecordOnlyOnceTheManifestIsPinned(
    sProjectRepo, monkeypatch, bManifestRefreshed,
):
    """A DOI on file over an incoherent envelope is still recoverable.

    The pending record is the only handle recovery has on that interior
    state, so it survives unless the re-pin reported success. The re-pin
    asks git whether the manifest is this project's own, and a scratch
    directory has no git, so its answer is supplied; the envelope write
    beside it is the real one.
    """
    dictSeen = {}

    def ftPromoteRecordingHooks(dictContainer, sToken, filesRepo,
                                sSidecarKey, dictMetadata, dictHooks):
        dictSeen["sToken"] = sToken
        dictSeen["sSidecarKey"] = sSidecarKey
        dictHooks["fnReportSaveProgress"](5, 10)
        dictSeen["sAfterSave"] = archiveProgress.DICT_DEPOSITS[
            S_CONTAINER_ID]["sPhase"]
        dictHooks["fnReportUploadStarted"](10)
        dictHooks["fnReportVerifying"]()
        return (_fdictDepositedRecord("10.5281/zenodo.400"),
                "promotionHotel")

    monkeypatch.setattr(
        archivePromotion, "ftPromoteImageArchive", ftPromoteRecordingHooks,
    )
    monkeypatch.setattr(
        imageDeposit, "_fbRepinManifestOrWarn",
        lambda filesRepo, dictWorkflow: bManifestRefreshed,
    )
    _fnSeedPendingPromotion(sProjectRepo, "promotionHotel")
    archiveProgress.fnRegisterDeposit(S_CONTAINER_ID, None, sProjectRepo)
    dictWorkflow = {"sProjectRepoPath": sProjectRepo}
    _fnRunPromotionWorker(sProjectRepo, dictWorkflow)
    assert dictSeen["sToken"] == S_PRODUCTION_TOKEN
    assert dictSeen["sSidecarKey"] == S_WORKFLOW_RELATIVE_PATH
    assert dictSeen["sAfterSave"] == archiveProgress.S_PHASE_SAVING
    assert archiveProgress.DICT_DEPOSITS[S_CONTAINER_ID]["sPhase"] == (
        archiveProgress.S_PHASE_SETTLED
    )
    dictContainer = fdictReadEnvironmentJson(
        ffilesEnsureRepoFiles(sProjectRepo),
    )["dictContainer"]
    assert dictContainer[imageArchive.S_IMAGE_ARCHIVE_KEY][
        "sVersionDoi"] == "10.5281/zenodo.400"
    assert dictContainer[S_SUPERSEDED_ARCHIVE_KEY]["sVersionDoi"] == (
        "10.5072/zenodo.100"
    )
    listPendingIds = [
        dictRecord["sPromotionId"] for dictRecord in _flistPending(sProjectRepo)
    ]
    assert listPendingIds == ([] if bManifestRefreshed else ["promotionHotel"])
    assert [
        dictRecord["sPromotionId"]
        for dictRecord in dictWorkflow["listPendingPromotions"]
    ] == listPendingIds


def testAFailedPromotionKeepsItsRecordVisibleAndItsReason(
    sProjectRepo, monkeypatch,
):
    """The mirror refreshes on failure too: the card must not wait a reload."""
    def ftPromoteFailingMidUpload(dictContainer, sToken, filesRepo,
                                  sSidecarKey, dictMetadata, dictHooks):
        _fnSeedPendingPromotion(sProjectRepo, "promotionIndia")
        raise zenodoClient.ZenodoError("Zenodo API error (502): gateway")

    monkeypatch.setattr(
        archivePromotion, "ftPromoteImageArchive", ftPromoteFailingMidUpload,
    )
    archiveProgress.fnRegisterDeposit(S_CONTAINER_ID, None, sProjectRepo)
    dictWorkflow = {"sProjectRepoPath": sProjectRepo}
    _fnRunPromotionWorker(sProjectRepo, dictWorkflow)
    dictEntry = archiveProgress.DICT_DEPOSITS[S_CONTAINER_ID]
    assert dictEntry["sPhase"] == archiveProgress.S_PHASE_FAILED
    assert dictEntry["sReason"] == "Zenodo API error (502): gateway"
    assert [
        dictRecord["sPromotionId"]
        for dictRecord in dictWorkflow["listPendingPromotions"]
    ] == ["promotionIndia"]
