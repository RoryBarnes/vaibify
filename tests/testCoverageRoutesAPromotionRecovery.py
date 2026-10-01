"""The promotion-recovery routes, driven over HTTP against a fake Zenodo.

The routes' guards were already pinned by calling their helpers
directly (``tests/testPromotionRecovery.py``). What is pinned here is
what a researcher actually meets through the endpoints: which request
reaches Zenodo, which one never does, what lands on disk, and what a
refusal says. Only the true boundaries are stubbed -- the ``requests``
calls ``zenodoClient`` makes, and the ``docker inspect`` probe behind
the network-isolation gate. The gate itself, the reconciliation, the
remote-names-this-promotion check and the sidecar writers all run.

Keys are kept distinct on purpose: the container id is not the
promotion id, the deposit id is not the parent id, and the keyring
token is a string that must never appear in any response body.
"""

import json
import os

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from vaibify.config import registryManager
from vaibify.docker import containerManager
from vaibify.gui import pipelineServer, routeContext
from vaibify.gui.routes import promotionRecoveryRoutes
from vaibify.reproducibility import (
    archivePromotion,
    imageArchive,
    syncBookkeeping,
    zenodoClient,
)
from vaibify.reproducibility.environmentSnapshot import (
    S_SUPERSEDED_ARCHIVE_KEY,
    fdictReadEnvironmentJson,
)
from vaibify.reproducibility.repoFiles import ffilesEnsureRepoFiles


S_CONTAINER_ID = "containerRecoveryAlpha"
S_PROMOTION_ID = "promotionBravo7"
I_DEPOSIT_ID = 4242
S_KEYRING_TOKEN = "keyring-secret-value-never-echoed"
S_WORKFLOW_RELATIVE_PATH = ".vaibify/workflows/stepFlow.json"
S_TARBALL_NAME = "environment-image.tar.zst"
S_TARBALL_MD5 = "d" * 32
I_TARBALL_BYTES = 4096


class _FakeResponse:
    """A stand-in for ``requests.Response`` carrying one JSON answer."""

    def __init__(self, iStatus, dictBody=None):
        self.status_code = iStatus
        self._dictBody = dictBody if dictBody is not None else {}
        self.text = json.dumps(self._dictBody)
        self.headers = {}

    def json(self):
        return self._dictBody

    def close(self):
        return None


class _FakeZenodoServer:
    """Answer the deposition endpoints the recovery routes call."""

    def __init__(self):
        self.dictDeposits = {}
        self.listRequests = []
        self.iForcedStatus = 0

    def fresponseGet(self, sUrl, headers=None, **dictKeywords):
        self.listRequests.append(("GET", sUrl, dict(headers or {})))
        if self.iForcedStatus:
            return _FakeResponse(self.iForcedStatus, {"message": "busy"})
        iDepositId = int(sUrl.rstrip("/").rsplit("/", 1)[-1])
        if iDepositId not in self.dictDeposits:
            return _FakeResponse(404, {"message": "not found"})
        return _FakeResponse(200, self.dictDeposits[iDepositId])

    def fresponseRequest(self, sMethod, sUrl, headers=None, **dictKeywords):
        self.listRequests.append((sMethod, sUrl, dict(headers or {})))
        if sMethod == "DELETE":
            return _FakeResponse(204)
        if sUrl.endswith("/actions/publish"):
            iDepositId = int(sUrl.split("/")[-3])
            dictDeposit = self.dictDeposits[iDepositId]
            dictDeposit.update({
                "state": "done",
                "doi": "10.5281/zenodo.%d" % iDepositId,
                "conceptdoi": "10.5281/zenodo.%d" % (iDepositId - 1),
            })
            return _FakeResponse(202, dictDeposit)
        return _FakeResponse(400, {"message": "unexpected " + sMethod})

    def flistMethods(self):
        return [tRequest[0] for tRequest in self.listRequests]


class _FakeKeyringDocker:
    """Answer the container keyring read; nothing else is reachable."""

    def __init__(self, sToken=S_KEYRING_TOKEN, bMissing=False):
        self.sToken = sToken
        self.bMissing = bMissing
        self.listSlotsAsked = []

    def fsFetchKeyringSecret(self, sContainerId, sSlot):
        self.listSlotsAsked.append((sContainerId, sSlot))
        if self.bMissing:
            raise LookupError("no such slot")
        return self.sToken


@pytest.fixture(autouse=True)
def fixtureIsolatedRegistry(monkeypatch, tmp_path):
    """Point the project registry at an empty file under tmp_path."""
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


@pytest.fixture
def fakeZenodo(monkeypatch):
    """Route every Zenodo HTTP call to an in-memory server."""
    serverFake = _FakeZenodoServer()
    monkeypatch.setattr(
        zenodoClient.requests, "get", serverFake.fresponseGet,
    )
    monkeypatch.setattr(
        zenodoClient.requests, "request", serverFake.fresponseRequest,
    )
    return serverFake


@pytest.fixture
def fixtureNetworkOpen(monkeypatch):
    """Answer the ``docker inspect`` probe: the container is not sealed."""
    monkeypatch.setattr(
        containerManager, "ftProbeNetworkIsolation",
        lambda sContainer: (True, False),
    )


@pytest.fixture
def sProjectRepo(tmp_path):
    """A host project repo whose envelope pins an image."""
    sRepo = str(tmp_path / "projectRepo")
    os.makedirs(os.path.join(sRepo, ".vaibify", "workflows"))
    with open(
        os.path.join(sRepo, ".vaibify", "environment.json"), "w",
    ) as fileOut:
        json.dump({"dictContainer": {
            "sImageDigest": "registry.example/image@sha256:" + "a" * 64,
            "sArchitecture": "arm64",
            imageArchive.S_IMAGE_ARCHIVE_KEY: {
                "sVersionDoi": "10.5072/zenodo.100",
                "sProvenance": imageArchive.S_PROVENANCE_ORIGINAL,
                "sImageStreamSha256": "sha256:" + "c" * 64,
            },
        }}, fileOut)
    return sRepo


def _fdictBuildWorkflow(sProjectRepo):
    return {
        "sProjectRepoPath": sProjectRepo,
        "listSteps": [],
        "sZenodoService": "sandbox",
    }


def _fclientBuild(sProjectRepo, connectionDocker=None, dictWorkflow=None):
    """Build the bare app around one workflow; return (client, workflow)."""
    dictWorkflow = dictWorkflow or _fdictBuildWorkflow(sProjectRepo)
    app = FastAPI()
    dictCtx = {
        "docker": connectionDocker or _FakeKeyringDocker(),
        "workflows": {S_CONTAINER_ID: dictWorkflow},
        "paths": {
            S_CONTAINER_ID: sProjectRepo + "/" + S_WORKFLOW_RELATIVE_PATH,
        },
        "require": lambda sContainerId: None,
    }
    promotionRecoveryRoutes.fnRegisterAll(app, dictCtx)
    return TestClient(app), dictWorkflow


def _fnSeedPendingPromotion(sProjectRepo, sLane, iDepositId=I_DEPOSIT_ID):
    """Write one pending-promotion record into the sidecar."""
    dictRecord = archivePromotion.fdictBuildPendingPromotion(
        S_PROMOTION_ID, sLane, "zenodo",
        [{"sBasename": S_TARBALL_NAME,
          "sSha256": "sha256:" + "b" * 64,
          "sMd5": S_TARBALL_MD5, "iBytes": I_TARBALL_BYTES}],
        iParentDepositId=77,
    )
    dictRecord["iDepositId"] = iDepositId
    syncBookkeeping.fnUpdatePendingPromotion(
        ffilesEnsureRepoFiles(sProjectRepo), S_WORKFLOW_RELATIVE_PATH,
        S_PROMOTION_ID, dictRecord,
    )
    return dictRecord


def _flistPendingOnDisk(sProjectRepo):
    return syncBookkeeping.flistReadPendingPromotions(
        ffilesEnsureRepoFiles(sProjectRepo), S_WORKFLOW_RELATIVE_PATH,
    )


def _fdictDraftDeposit(sState="inprogress", bNamesPromotion=True):
    sDescription = "An environment image."
    if bNamesPromotion:
        sDescription = archivePromotion.fsStampPromotionIdIntoDescription(
            sDescription, S_PROMOTION_ID,
        )
    return {
        "id": I_DEPOSIT_ID,
        "state": sState,
        "files": [{
            "filename": S_TARBALL_NAME,
            "checksum": "md5:" + S_TARBALL_MD5,
            "filesize": I_TARBALL_BYTES,
        }],
        "metadata": {"description": sDescription},
    }


def _fsPath(sSuffix=""):
    return (
        f"/api/workflow/{S_CONTAINER_ID}/promotions" + sSuffix
    )


# ── Listing ──


def testListingReadsTheSidecarNotTheBrowser(sProjectRepo):
    """A record written by a browser that has since gone is still listed."""
    _fnSeedPendingPromotion(sProjectRepo, archivePromotion.S_LANE_IMAGE)
    clientTest, _dictWorkflow = _fclientBuild(sProjectRepo)
    responseHttp = clientTest.get(_fsPath("/pending"))
    assert responseHttp.status_code == 200
    listPending = responseHttp.json()["listPending"]
    assert [dictRecord["sPromotionId"] for dictRecord in listPending] == [
        S_PROMOTION_ID,
    ]
    assert listPending[0]["iDepositId"] == I_DEPOSIT_ID


def testListingWithoutAnOpenProjectIsRefusedByName(sProjectRepo):
    clientTest, _dictWorkflow = _fclientBuild(sProjectRepo)
    responseHttp = clientTest.get(
        "/api/workflow/containerNeverOpened/promotions/pending",
    )
    assert responseHttp.status_code == 404
    dictDetail = responseHttp.json()["detail"]
    assert dictDetail["sRefusal"] == pipelineServer.S_REFUSAL_NO_PROJECT_OPEN
    assert "No project is open" in dictDetail["sMessage"]
    assert sProjectRepo not in responseHttp.text


# ── Reconcile ──


def testReconcileOfAnUnknownPromotionNamesItAndAsksNobody(
    sProjectRepo, fakeZenodo, fixtureNetworkOpen,
):
    clientTest, _dictWorkflow = _fclientBuild(sProjectRepo)
    responseHttp = clientTest.post(_fsPath("/promotionNobody/reconcile"))
    assert responseHttp.status_code == 404
    assert "promotionNobody" in responseHttp.json()["detail"]
    assert fakeZenodo.listRequests == []


def testReconcileAsksTheTargetedInstanceWithTheStoredToken(
    sProjectRepo, fakeZenodo, fixtureNetworkOpen,
):
    """The token comes from the CONTAINER keyring slot of the target.

    The record targeted production, so the production slot is read and
    the GET goes to zenodo.org -- never to the sandbox the project
    otherwise publishes to. The token authorizes the request and is
    never echoed back.
    """
    _fnSeedPendingPromotion(sProjectRepo, archivePromotion.S_LANE_IMAGE)
    fakeZenodo.dictDeposits[I_DEPOSIT_ID] = _fdictDraftDeposit()
    connectionDocker = _FakeKeyringDocker()
    clientTest, _dictWorkflow = _fclientBuild(
        sProjectRepo, connectionDocker,
    )
    responseHttp = clientTest.post(
        _fsPath("/" + S_PROMOTION_ID + "/reconcile"),
    )
    assert responseHttp.status_code == 200
    dictOutcome = responseHttp.json()
    assert dictOutcome["sOutcome"] == archivePromotion.S_OUTCOME_PUBLISHABLE
    assert dictOutcome["listActions"] == ["resume", "discard"]
    assert dictOutcome["dictRecord"]["sPromotionId"] == S_PROMOTION_ID
    assert connectionDocker.listSlotsAsked == [
        (S_CONTAINER_ID, "zenodo_token_production"),
    ]
    sMethod, sUrl, dictHeaders = fakeZenodo.listRequests[0]
    assert sMethod == "GET"
    assert sUrl.startswith("https://zenodo.org/api/deposit/depositions/")
    assert dictHeaders["Authorization"] == "Bearer " + S_KEYRING_TOKEN
    assert S_KEYRING_TOKEN not in responseHttp.text


def testReconcileWithNoStoredTokenAsksWithoutCredentials(
    sProjectRepo, fakeZenodo, fixtureNetworkOpen,
):
    """An absent slot is not an error; it is an unauthenticated ask."""
    _fnSeedPendingPromotion(sProjectRepo, archivePromotion.S_LANE_IMAGE)
    fakeZenodo.dictDeposits[I_DEPOSIT_ID] = _fdictDraftDeposit()
    clientTest, _dictWorkflow = _fclientBuild(
        sProjectRepo, _FakeKeyringDocker(bMissing=True),
    )
    responseHttp = clientTest.post(
        _fsPath("/" + S_PROMOTION_ID + "/reconcile"),
    )
    assert responseHttp.status_code == 200
    assert "Authorization" not in fakeZenodo.listRequests[0][2]


def testReconcileOnASealedContainerIsRefusedBeforeAnyRequest(
    sProjectRepo, fakeZenodo, monkeypatch,
):
    """The isolation gate answers before a 30-second DNS timeout could."""
    monkeypatch.setattr(
        containerManager, "ftProbeNetworkIsolation",
        lambda sContainer: (True, True),
    )
    _fnSeedPendingPromotion(sProjectRepo, archivePromotion.S_LANE_IMAGE)
    clientTest, _dictWorkflow = _fclientBuild(sProjectRepo)
    responseHttp = clientTest.post(
        _fsPath("/" + S_PROMOTION_ID + "/reconcile"),
    )
    assert responseHttp.status_code == 409
    assert responseHttp.json()["detail"]["sError"] == (
        routeContext.S_ISOLATION_BLOCK_ERROR
    )
    assert fakeZenodo.listRequests == []


def testReconcileOfAnUnreadableZenodoKeepsTheRecord(
    sProjectRepo, fakeZenodo, fixtureNetworkOpen,
):
    """A 500 is a question nobody answered, and licenses nothing."""
    _fnSeedPendingPromotion(sProjectRepo, archivePromotion.S_LANE_IMAGE)
    fakeZenodo.iForcedStatus = 503
    clientTest, _dictWorkflow = _fclientBuild(sProjectRepo)
    responseHttp = clientTest.post(
        _fsPath("/" + S_PROMOTION_ID + "/reconcile"),
    )
    assert responseHttp.status_code == 200
    assert responseHttp.json()["sOutcome"] == (
        archivePromotion.S_OUTCOME_UNKNOWN
    )
    assert responseHttp.json()["listActions"] == []
    assert len(_flistPendingOnDisk(sProjectRepo)) == 1


# ── Resume ──


def testResumePublishesTheDraftAndAdoptsItIntoTheEnvelope(
    sProjectRepo, fakeZenodo, fixtureNetworkOpen,
):
    """The lane's whole purpose: upload finished, publish did not.

    The old sandbox record is carried as the superseded note in the same
    write, and the settled record leaves the sidecar.
    """
    _fnSeedPendingPromotion(sProjectRepo, archivePromotion.S_LANE_IMAGE)
    fakeZenodo.dictDeposits[I_DEPOSIT_ID] = _fdictDraftDeposit()
    clientTest, dictWorkflow = _fclientBuild(sProjectRepo)
    responseHttp = clientTest.post(
        _fsPath("/" + S_PROMOTION_ID + "/resume"),
    )
    assert responseHttp.status_code == 200, responseHttp.text
    assert responseHttp.json() == {
        "bAdopted": True, "sDoi": "10.5281/zenodo.%d" % I_DEPOSIT_ID,
    }
    assert "POST" in fakeZenodo.flistMethods()
    dictContainer = fdictReadEnvironmentJson(
        ffilesEnsureRepoFiles(sProjectRepo),
    )["dictContainer"]
    dictStamped = dictContainer[imageArchive.S_IMAGE_ARCHIVE_KEY]
    assert dictStamped["sVersionDoi"] == "10.5281/zenodo.%d" % I_DEPOSIT_ID
    assert dictStamped["sConceptDoi"] == (
        "10.5281/zenodo.%d" % (I_DEPOSIT_ID - 1)
    )
    assert dictStamped["sZenodoService"] == "zenodo"
    assert dictStamped["sTarballName"] == S_TARBALL_NAME
    assert dictContainer[S_SUPERSEDED_ARCHIVE_KEY]["sVersionDoi"] == (
        "10.5072/zenodo.100"
    )
    assert _flistPendingOnDisk(sProjectRepo) == []
    assert dictWorkflow["listPendingPromotions"] == []


def testResumeRefusesADraftThatDoesNotNameThisPromotion(
    sProjectRepo, fakeZenodo, fixtureNetworkOpen,
):
    """Nothing is published into a deposit the record cannot claim."""
    _fnSeedPendingPromotion(sProjectRepo, archivePromotion.S_LANE_IMAGE)
    fakeZenodo.dictDeposits[I_DEPOSIT_ID] = _fdictDraftDeposit(
        bNamesPromotion=False,
    )
    clientTest, _dictWorkflow = _fclientBuild(sProjectRepo)
    responseHttp = clientTest.post(
        _fsPath("/" + S_PROMOTION_ID + "/resume"),
    )
    assert responseHttp.status_code == 409
    assert "does not name this promotion" in responseHttp.json()["detail"]
    assert fakeZenodo.flistMethods() == ["GET", "GET"]
    assert len(_flistPendingOnDisk(sProjectRepo)) == 1


def testResumeRefusesAPartlyUploadedDraftWithTheOutcomeReason(
    sProjectRepo, fakeZenodo, fixtureNetworkOpen,
):
    """Resume needs bytes the interruption did not preserve."""
    _fnSeedPendingPromotion(sProjectRepo, archivePromotion.S_LANE_IMAGE)
    dictDraft = _fdictDraftDeposit()
    dictDraft["files"] = []
    fakeZenodo.dictDeposits[I_DEPOSIT_ID] = dictDraft
    clientTest, _dictWorkflow = _fclientBuild(sProjectRepo)
    responseHttp = clientTest.post(
        _fsPath("/" + S_PROMOTION_ID + "/resume"),
    )
    assert responseHttp.status_code == 409
    assert "missing 1 of its files" in responseHttp.json()["detail"]
    assert "POST" not in fakeZenodo.flistMethods()


# ── Adopt ──


def testAdoptOfAPublishedImagePromotionStampsAndSettles(
    sProjectRepo, fakeZenodo, fixtureNetworkOpen,
):
    _fnSeedPendingPromotion(sProjectRepo, archivePromotion.S_LANE_IMAGE)
    dictDeposit = _fdictDraftDeposit("done")
    dictDeposit.update({
        "doi": "10.5281/zenodo.5000", "conceptdoi": "10.5281/zenodo.4999",
    })
    fakeZenodo.dictDeposits[I_DEPOSIT_ID] = dictDeposit
    clientTest, _dictWorkflow = _fclientBuild(sProjectRepo)
    responseHttp = clientTest.post(
        _fsPath("/" + S_PROMOTION_ID + "/adopt"),
    )
    assert responseHttp.status_code == 200, responseHttp.text
    assert responseHttp.json()["sDoi"] == "10.5281/zenodo.5000"
    dictStamped = fdictReadEnvironmentJson(
        ffilesEnsureRepoFiles(sProjectRepo),
    )["dictContainer"][imageArchive.S_IMAGE_ARCHIVE_KEY]
    assert dictStamped["sVersionDoi"] == "10.5281/zenodo.5000"
    assert dictStamped["sTarballMd5"] == S_TARBALL_MD5
    assert dictStamped["sImageStreamSha256"] == "sha256:" + "c" * 64
    assert set(fakeZenodo.flistMethods()) == {"GET"}
    assert _flistPendingOnDisk(sProjectRepo) == []


def testAdoptRefusesADraftThatWasNeverPublished(
    sProjectRepo, fakeZenodo, fixtureNetworkOpen,
):
    _fnSeedPendingPromotion(sProjectRepo, archivePromotion.S_LANE_IMAGE)
    fakeZenodo.dictDeposits[I_DEPOSIT_ID] = _fdictDraftDeposit()
    clientTest, _dictWorkflow = _fclientBuild(sProjectRepo)
    responseHttp = clientTest.post(
        _fsPath("/" + S_PROMOTION_ID + "/adopt"),
    )
    assert responseHttp.status_code == 409
    assert "only the publish is left" in responseHttp.json()["detail"]
    assert len(_flistPendingOnDisk(sProjectRepo)) == 1


def testAdoptOfAProjectPromotionWithoutALeaseKeepsTheRecord(
    sProjectRepo, fakeZenodo, fixtureNetworkOpen,
):
    """The project lane writes through the carrier, which the real gate
    refuses to a request holding no lease -- and the record survives.

    Settling the record before the DOI is durably written would drop
    the only handle on a minted DOI.
    """
    _fnSeedPendingPromotion(sProjectRepo, archivePromotion.S_LANE_PROJECT)
    dictDeposit = _fdictDraftDeposit("done")
    dictDeposit.update({
        "doi": "10.5281/zenodo.6000", "conceptdoi": "10.5281/zenodo.5999",
    })
    fakeZenodo.dictDeposits[I_DEPOSIT_ID] = dictDeposit
    clientTest, _dictWorkflow = _fclientBuild(sProjectRepo)
    responseHttp = clientTest.post(
        _fsPath("/" + S_PROMOTION_ID + "/adopt"),
    )
    assert responseHttp.status_code == 403
    assert "claim or connect" in responseHttp.json()["detail"]
    assert len(_flistPendingOnDisk(sProjectRepo)) == 1


# ── Discard ──


def testDiscardOfAResumableDraftDeletesItAndTheRecord(
    sProjectRepo, fakeZenodo,
):
    _fnSeedPendingPromotion(sProjectRepo, archivePromotion.S_LANE_IMAGE)
    dictDraft = _fdictDraftDeposit()
    dictDraft["files"] = []
    fakeZenodo.dictDeposits[I_DEPOSIT_ID] = dictDraft
    clientTest, dictWorkflow = _fclientBuild(sProjectRepo)
    responseHttp = clientTest.delete(_fsPath("/" + S_PROMOTION_ID))
    assert responseHttp.status_code == 200
    assert responseHttp.json() == {"bDiscarded": True}
    listDeletes = [
        tRequest for tRequest in fakeZenodo.listRequests
        if tRequest[0] == "DELETE"
    ]
    assert len(listDeletes) == 1
    assert listDeletes[0][1].endswith("/deposit/depositions/%d" % I_DEPOSIT_ID)
    assert _flistPendingOnDisk(sProjectRepo) == []
    assert dictWorkflow["listPendingPromotions"] == []


def testDiscardOfADepositZenodoAnswers404ForSendsNoDelete(
    sProjectRepo, fakeZenodo,
):
    """A 404 is an answer: the record goes, and nothing is deleted twice."""
    _fnSeedPendingPromotion(sProjectRepo, archivePromotion.S_LANE_IMAGE)
    clientTest, _dictWorkflow = _fclientBuild(sProjectRepo)
    responseHttp = clientTest.delete(_fsPath("/" + S_PROMOTION_ID))
    assert responseHttp.status_code == 200
    assert fakeZenodo.flistMethods() == ["GET"]
    assert _flistPendingOnDisk(sProjectRepo) == []


def testDiscardOfARecordThatNeverDraftedAsksZenodoNothing(
    sProjectRepo, fakeZenodo,
):
    _fnSeedPendingPromotion(
        sProjectRepo, archivePromotion.S_LANE_IMAGE, iDepositId=0,
    )
    clientTest, _dictWorkflow = _fclientBuild(sProjectRepo)
    responseHttp = clientTest.delete(_fsPath("/" + S_PROMOTION_ID))
    assert responseHttp.status_code == 200
    assert fakeZenodo.listRequests == []
    assert _flistPendingOnDisk(sProjectRepo) == []


def testDiscardOfAPublishedDepositIsRefusedAndKeepsTheRecord(
    sProjectRepo, fakeZenodo,
):
    _fnSeedPendingPromotion(sProjectRepo, archivePromotion.S_LANE_IMAGE)
    dictDeposit = _fdictDraftDeposit("done")
    dictDeposit["doi"] = "10.5281/zenodo.7000"
    fakeZenodo.dictDeposits[I_DEPOSIT_ID] = dictDeposit
    clientTest, _dictWorkflow = _fclientBuild(sProjectRepo)
    responseHttp = clientTest.delete(_fsPath("/" + S_PROMOTION_ID))
    assert responseHttp.status_code == 409
    assert "published" in responseHttp.json()["detail"]
    assert "DELETE" not in fakeZenodo.flistMethods()
    assert len(_flistPendingOnDisk(sProjectRepo)) == 1
