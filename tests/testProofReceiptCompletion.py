"""The completion version: inherited files, one receipt replaced, journaled.

Source: ``vaibify/reproducibility/archivePromotion.py`` (the completion
lane's record and reconciliation), ``vaibify/gui/syncDispatcher.py``
(the Zenodo steps and the settlement), ``vaibify/gui/routes/syncRoutes.py``
(the automatic step, the retry route, the archive's refusal) and
``vaibify/gui/routes/promotionRecoveryRoutes.py`` (resume, adopt,
discard).

The fake Zenodo below models what a completion depends on: published
versions, a new-version draft that INHERITS its parent's files, per-file
deletion, a bucket, and a ``links.latest`` naming the newest version.
"""

import copy
import hashlib
import json
import os

import pytest

from tests.remoteBindingFixtures import (  # noqa: F401  (autouse fixture)
    fixtureBindingsFollowProjectJson,
)
from tests.testCoverageRoutesBSync import (  # noqa: F401  (fixture)
    S_CONTAINER_ID,
    SyncExecDouble,
    fdictSandboxWorkflow,
    fixtureIsolateHostState,
    ftBuildClient,
)
from vaibify.gui import syncDispatcher
from vaibify.gui.routes import promotionRecoveryRoutes, syncRoutes
from vaibify.reproducibility import archivePromotion, syncBookkeeping
from vaibify.reproducibility.repoFiles import HostRepoFiles


BA_DATA = b"a,b\n1,2\n"
BA_CHECKPOINT_RECEIPT = b'{"sKind": "checkpoint"}\n'
BA_COMPLETION_RECEIPT = b'{"sKind": "completion"}\n'
S_PROMOTION_ID = "promotion-" + "c" * 32
S_RECEIPT_KEY = "proofReceipt.json"


def _fsMd5(baContent):
    return hashlib.md5(baContent, usedforsecurity=False).hexdigest()


def _fdictItem(sKey, sRepoPath, baContent):
    return {"sKey": sKey, "sRepoPath": sRepoPath,
            "sSha256": hashlib.sha256(baContent).hexdigest(),
            "sMd5": _fsMd5(baContent), "iSizeBytes": len(baContent)}


def fdictCheckpointNote():
    """The deposit record's note of checkpoint 101's receipt."""
    dictReceipt = _fdictItem(S_RECEIPT_KEY, "", BA_CHECKPOINT_RECEIPT)
    return dict(dictReceipt, sKind="checkpoint", sRecordId="101",
                sDoi="10.5072/zenodo.101",
                listContents=[_fdictItem("out.csv", "Step/out.csv", BA_DATA)])


def fdictCompletionRecord():
    return archivePromotion.fdictBuildPendingCompletion(
        S_PROMOTION_ID, "sandbox", fdictCheckpointNote(),
        _fdictItem(S_RECEIPT_KEY, ".vaibify/proofReceipt.json",
                   BA_COMPLETION_RECEIPT))


class _ArchiveZenodo:
    """Zenodo as a completion meets it."""

    def __init__(self):
        self.dictDeposits = {}
        self.iLatest = 101
        self.iNextId = 200
        self.listCalls = []
        self.fnAddVersion(101, {"out.csv": BA_DATA,
                                S_RECEIPT_KEY: BA_CHECKPOINT_RECEIPT})

    def fnAddVersion(self, iId, dictFiles, sState="done", sDescription=""):
        self.dictDeposits[iId] = {
            "id": iId, "state": sState,
            "metadata": {"description": sDescription},
            "listFiles": [{"filename": sKey, "id": f"f{iId}-{sKey}",
                           "baContent": baContent}
                          for sKey, baContent in dictFiles.items()]}

    def fdictGetDeposit(self, iDepositId):
        dictDeposit = self.dictDeposits[int(iDepositId)]
        return {
            "id": dictDeposit["id"], "state": dictDeposit["state"],
            "metadata": copy.deepcopy(dictDeposit["metadata"]),
            "links": {"bucket": f"bucket/{dictDeposit['id']}",
                      "latest": f"https://zenodo/api/records/{self.iLatest}"},
            "doi": f"10.5072/zenodo.{dictDeposit['id']}",
            "conceptdoi": "10.5072/zenodo.100",
            "files": [{"filename": d["filename"], "id": d["id"],
                       "checksum": _fsMd5(d["baContent"]),
                       "filesize": len(d["baContent"])}
                      for d in dictDeposit["listFiles"]]}

    def fdictGetNewVersionDraft(self, iParent):
        self.listCalls.append(("newversion", iParent))
        iDraft = self.iNextId
        self.iNextId += 1
        dictParent = self.dictDeposits[int(iParent)]
        self.dictDeposits[iDraft] = {
            "id": iDraft, "state": "unsubmitted",
            "metadata": copy.deepcopy(dictParent["metadata"]),
            "listFiles": copy.deepcopy(dictParent["listFiles"])}
        return self.fdictGetDeposit(iDraft)

    def fnSetMetadata(self, iDepositId, dictApi):
        self.listCalls.append(("metadata", iDepositId))
        self.dictDeposits[int(iDepositId)]["metadata"]["description"] = (
            dictApi.get("description"))

    def fnDeleteDraftFile(self, iDepositId, sFileId):
        self.listCalls.append(("delete", sFileId))
        dictDeposit = self.dictDeposits[int(iDepositId)]
        dictDeposit["listFiles"] = [
            d for d in dictDeposit["listFiles"] if d["id"] != sFileId]

    def fnUploadToBucket(self, sBucketUrl, sPath, **_dictKeywords):
        iDepositId = int(sBucketUrl.rsplit("/", 1)[1])
        with open(sPath, "rb") as fileUpload:
            baContent = fileUpload.read()
        self.listCalls.append(("upload", os.path.basename(sPath), baContent))
        self.dictDeposits[iDepositId]["listFiles"].append({
            "filename": os.path.basename(sPath),
            "id": f"f{iDepositId}-new", "baContent": baContent})

    def fdictPublishDraft(self, iDepositId):
        self.listCalls.append(("publish", int(iDepositId)))
        self.dictDeposits[int(iDepositId)]["state"] = "done"
        self.iLatest = int(iDepositId)
        return {"doi": f"10.5072/zenodo.{iDepositId}",
                "conceptdoi": "10.5072/zenodo.100",
                "links": {"html": f"https://zenodo/record/{iDepositId}"}}

    def flistPhases(self):
        return [tCall[0] for tCall in self.listCalls]


def fdictJournalHooks(listNotes):
    return {
        "fnNoteDraft": lambda iDraft: listNotes.append(("draft", iDraft)),
        "fnNoteMismatch": lambda listKeys: listNotes.append(
            ("mismatch", list(listKeys))),
        "fnNoteUploading": lambda: listNotes.append(("uploading",)),
        "fnNotePublished": lambda d: listNotes.append(("published",)),
    }


def _ftPublish(clientZenodo, dictRecord=None, baReceipt=BA_COMPLETION_RECEIPT,
               listNotes=None):
    return syncDispatcher.ftPublishCompletionWithClient(
        clientZenodo, dictRecord or fdictCompletionRecord(), baReceipt,
        {"title": "A project", "description": "Data."},
        fdictJournalHooks(listNotes if listNotes is not None else []))


# ── The Zenodo steps ─────────────────────────────────────────────────


def testACompletionInheritsTheDataAndReplacesOnlyTheReceipt():
    clientZenodo = _ArchiveZenodo()
    listNotes = []
    iExit, sOut = _ftPublish(clientZenodo, listNotes=listNotes)
    assert iExit == 0, sOut
    dictPublished = clientZenodo.dictDeposits[200]
    assert {d["filename"]: d["baContent"] for d in dictPublished[
        "listFiles"]} == {"out.csv": BA_DATA,
                          S_RECEIPT_KEY: BA_COMPLETION_RECEIPT}
    assert [tCall for tCall in clientZenodo.listCalls
            if tCall[0] == "upload"] == [
        ("upload", S_RECEIPT_KEY, BA_COMPLETION_RECEIPT)]
    assert listNotes == [("draft", 200), ("uploading",), ("published",)]
    assert archivePromotion.fbDescriptionCarriesPromotionId(
        dictPublished["metadata"]["description"], S_PROMOTION_ID)


@pytest.mark.falsification
def testACompletionRefusesAnInheritedFileWhoseMd5OrSizeDiffers():
    """Inherited files are matched by checksum and size, never by key alone.

    Kills: the inherited-file check compares keys only.
    """
    clientZenodo = _ArchiveZenodo()
    clientZenodo.fnAddVersion(101, {"out.csv": b"a,b\n9,9\n",
                                    S_RECEIPT_KEY: BA_CHECKPOINT_RECEIPT})
    listNotes = []
    iExit, sOut = _ftPublish(clientZenodo, listNotes=listNotes)
    assert iExit == 1 and sOut.startswith("INHERITED-MISMATCH")
    assert ("mismatch", ["out.csv"]) in listNotes
    assert "upload" not in clientZenodo.flistPhases()
    assert "publish" not in clientZenodo.flistPhases()


@pytest.mark.falsification
def testAForeignUnpublishedDraftIsRefused():
    """A draft another operation stamped is neither cleared nor published.

    Kills: a draft adopted without its stamp.
    """
    clientZenodo = _ArchiveZenodo()
    clientZenodo.dictDeposits[101]["metadata"]["description"] = (
        archivePromotion.fsStampPromotionIdIntoDescription(
            "Data.", "promotion-" + "d" * 32))
    iExit, sOut = _ftPublish(clientZenodo)
    assert iExit == 1 and sOut.startswith("FOREIGN-DRAFT")
    assert set(clientZenodo.flistPhases()) == {"newversion"}


@pytest.mark.falsification
def testANewerConceptVersionRefusesTheCompletion():
    """A completion only follows the checkpoint it completes.

    Kills: the latest-version check removed.
    """
    clientZenodo = _ArchiveZenodo()
    clientZenodo.fnAddVersion(102, {"out.csv": BA_DATA})
    clientZenodo.iLatest = 102
    iExit, sOut = _ftPublish(clientZenodo)
    assert iExit == 1 and sOut.startswith("NEWER-VERSION") and "102" in sOut
    assert clientZenodo.listCalls == []


def testADuplicateSubmissionOpensOneDraft():
    clientZenodo = _ArchiveZenodo()
    dictRecord = fdictCompletionRecord()

    def fnRefuseUpload(*listArgs, **dictKeywords):
        raise syncDispatcher.zenodoClient.ZenodoError("bucket busy")

    fnRealUpload = clientZenodo.fnUploadToBucket
    clientZenodo.fnUploadToBucket = fnRefuseUpload
    assert _ftPublish(clientZenodo, dictRecord)[0] == 1
    dictRecord["iDepositId"] = 200
    clientZenodo.fnUploadToBucket = fnRealUpload
    assert _ftPublish(clientZenodo, dictRecord)[0] == 0
    assert clientZenodo.flistPhases().count("newversion") == 1


def testAStoredReceiptThatIsNotTheJournaledOneIsRefusedBeforeAnything():
    iExit, sOut = syncDispatcher.ftResultPublishCompletion(
        None, S_CONTAINER_ID, "sandbox", fdictCompletionRecord(),
        b"altered", {}, fdictJournalHooks([]))
    assert iExit == 1 and sOut.startswith("RECEIPT-MISMATCH")


# ── Reconciliation ───────────────────────────────────────────────────


def _fdictReconcile(clientZenodo, dictRecord, baStored=BA_COMPLETION_RECEIPT):
    return archivePromotion.fdictReconcileCompletion(
        dictRecord, clientZenodo.fdictGetDeposit, baStored)


@pytest.mark.falsification
def testAPublishedVersionStillHoldingTheCheckpointReceiptIsMismatched():
    """Matching data files do not make a version with the old receipt published.

    Kills: the inventory names the checkpoint's receipt instead of the
    completion's.
    """
    clientZenodo = _ArchiveZenodo()
    clientZenodo.fnAddVersion(200, {"out.csv": BA_DATA,
                                    S_RECEIPT_KEY: BA_CHECKPOINT_RECEIPT})
    dictRecord = dict(fdictCompletionRecord(), iDepositId=200)
    assert _fdictReconcile(clientZenodo, dictRecord)["sOutcome"] == (
        archivePromotion.S_OUTCOME_MISMATCHED)


@pytest.mark.falsification
def testAPublishedVersionMissingTheReceiptIsMismatched():
    """A version without the receipt is not the intended version.

    Kills: the receipt left out of the completion's inventory.
    """
    clientZenodo = _ArchiveZenodo()
    clientZenodo.fnAddVersion(200, {"out.csv": BA_DATA})
    dictRecord = dict(fdictCompletionRecord(), iDepositId=200)
    assert _fdictReconcile(clientZenodo, dictRecord)["sOutcome"] == (
        archivePromotion.S_OUTCOME_MISMATCHED)


def testAPublishedCompletionIsAdoptedNeverRepublished():
    clientZenodo = _ArchiveZenodo()
    _ftPublish(clientZenodo)
    dictRecord = dict(fdictCompletionRecord(), iDepositId=200)
    dictOutcome = _fdictReconcile(clientZenodo, dictRecord)
    assert dictOutcome["sOutcome"] == archivePromotion.S_OUTCOME_PUBLISHED
    assert dictOutcome["listActions"] == [archivePromotion.S_ACTION_ADOPT]


def testADraftMissingOnlyTheReceiptIsResumable():
    clientZenodo = _ArchiveZenodo()
    clientZenodo.fnAddVersion(200, {"out.csv": BA_DATA}, sState="unsubmitted")
    dictOutcome = _fdictReconcile(
        clientZenodo, dict(fdictCompletionRecord(), iDepositId=200))
    assert dictOutcome["listActions"] == ["resume", "discard"]


@pytest.mark.falsification
def testAStoredReceiptAlteredOnDiskIsNotResumed():
    """Resume uploads the stored bytes, so altered bytes offer only discard.

    Kills: the stored bytes not checked against the journal.
    """
    clientZenodo = _ArchiveZenodo()
    clientZenodo.fnAddVersion(200, {"out.csv": BA_DATA}, sState="unsubmitted")
    dictOutcome = _fdictReconcile(
        clientZenodo, dict(fdictCompletionRecord(), iDepositId=200),
        baStored=b'{"sKind": "tampered"}\n')
    assert dictOutcome["listActions"] == ["discard"]
    assert "altered or replaced" in dictOutcome["sMessage"]


@pytest.mark.falsification
def testADraftHoldingAnExtraFileIsNotResumed():
    """A completion's inventory is exact: an unlisted file is a non-match.

    Kills: the completion lane's extra-file check removed.
    """
    clientZenodo = _ArchiveZenodo()
    clientZenodo.fnAddVersion(200, {"out.csv": BA_DATA, "stray.txt": b"x"},
                              sState="unsubmitted")
    dictOutcome = _fdictReconcile(
        clientZenodo, dict(fdictCompletionRecord(), iDepositId=200))
    assert dictOutcome["listActions"] == ["discard"]


@pytest.mark.falsification
def testTheProjectAndImageLanesStillOfferOnlyDiscardOnAPartialUpload():
    """Their bytes do not survive an interruption, so resume stays theirs not.

    Kills: the completion lane's resume applied to every lane.
    """
    clientZenodo = _ArchiveZenodo()
    clientZenodo.fnAddVersion(300, {}, sState="unsubmitted")
    for sLane in (archivePromotion.S_LANE_IMAGE, archivePromotion.S_LANE_PROJECT):
        dictRecord = archivePromotion.fdictBuildPendingPromotion(
            "promotion-" + "e" * 32, sLane, "zenodo",
            [archivePromotion.fdictDescribeInventoryFile(
                _fdictItem("image.tar.zst", "", b"bytes"))])
        dictRecord["iDepositId"] = 300
        assert archivePromotion.fdictReconcilePromotion(
            dictRecord, clientZenodo.fdictGetDeposit)["listActions"] == [
            "discard"], sLane


# ── Authorization: one coherent observation that attains Level 2 ────


def _fdictObservation(bLevelTwo=True, bCoherent=True, sAt="2026-11-03T17:42:10Z"):
    return {"dictVerdict": {"bAtLeastLevel1": True, "bAtLeastLevel2": bLevelTwo},
            "bCoherent": bCoherent, "sEvaluatedAtUtc": sAt,
            "listChangedReads": [] if bCoherent else ["Step/out.csv"]}


def _fnServeObservations(monkeypatch, listObservations, listPerformed):
    listQueue = list(listObservations)

    async def fdictCapture(*_listArgs):
        return listQueue.pop(0)

    async def fdictPerform(_c, _i, _w, _r, dictObservation):
        listPerformed.append(dictObservation)
        return {"bCompleted": True}

    monkeypatch.setattr(syncRoutes, "_fdictCaptureUnderTheDrain", fdictCapture)
    monkeypatch.setattr(syncRoutes, "_fdictPerformCompletion", fdictPerform)


def _fdictCompleteAutomatically(dictResult=None):
    import asyncio
    return asyncio.run(syncRoutes._fdictCompleteAfterCheckpoint(
        {}, S_CONTAINER_ID, {}, None, dictResult or {}))


@pytest.mark.falsification
def testAnIncoherentObservationAuthorizesNoCompletion(monkeypatch):
    """Files that moved while vaibify read them authorize nothing.

    Kills: bCoherent ignored by the completion's authorization.
    """
    listPerformed = []
    _fnServeObservations(monkeypatch, [_fdictObservation(bCoherent=False)],
                         listPerformed)
    dictOutcome = _fdictCompleteAutomatically()
    assert dictOutcome["bCompleted"] is False
    assert "changed while vaibify was reading" in dictOutcome["sReason"]
    assert listPerformed == []


@pytest.mark.falsification
def testNoCompletionIsMintedWhileLevelTwoIsNotAttained(monkeypatch):
    """A version is never minted to say "not met".

    Kills: the attained check removed from the authorization.
    """
    listPerformed = []
    _fnServeObservations(monkeypatch, [_fdictObservation(bLevelTwo=False)],
                         listPerformed)
    assert _fdictCompleteAutomatically()["bCompleted"] is False
    assert listPerformed == []


def testAVerifyWarningAuthorizesNoCompletion(monkeypatch):
    listPerformed = []
    _fnServeObservations(monkeypatch, [_fdictObservation()], listPerformed)
    dictOutcome = _fdictCompleteAutomatically(
        {"sPostPushVerifyWarning": "the record lags Zenodo's index"})
    assert dictOutcome["bCompleted"] is False and listPerformed == []


@pytest.mark.falsification
def testTheCompletionIsAuthorizedByTheObservationItRecords(monkeypatch):
    """The observation that authorizes is the one the receipt records.

    Kills: a second capture after authorization.
    """
    listPerformed = []
    dictFirst = _fdictObservation(sAt="2026-11-03T17:42:10Z")
    _fnServeObservations(monkeypatch, [
        dictFirst, _fdictObservation(sAt="2026-11-03T17:43:00Z")],
        listPerformed)
    assert _fdictCompleteAutomatically()["bCompleted"] is True
    assert listPerformed == [dictFirst]


# ── The retry route ──────────────────────────────────────────────────


def _fdictWorkflowWithCheckpoint(tmp_path, listPending=()):
    dictWorkflow = fdictSandboxWorkflow(str(tmp_path / "projectRepo"))
    dictWorkflow["dictRemotes"]["zenodo"]["dictReceipt"] = (
        fdictCheckpointNote())
    dictWorkflow["listPendingPromotions"] = list(listPending)
    return dictWorkflow


@pytest.mark.falsification
def testARetryNeverOpensASecondDraftOverALiveCompletion(
    monkeypatch, tmp_path,
):
    """A journaled completion is reconciled first; a live one refuses.

    Kills: reconciliation skipped before the retry.
    """
    dictRecord = dict(fdictCompletionRecord(), iDepositId=200)
    dictWorkflow = _fdictWorkflowWithCheckpoint(tmp_path, [dictRecord])
    listPerformed = []
    _fnServeObservations(monkeypatch, [_fdictObservation()], listPerformed)
    monkeypatch.setattr(
        syncRoutes, "_fdictReconcileOneCompletion",
        lambda *_listArgs: {"sOutcome": "published", "sMessage": "Published."})
    clientSync, _, _ = ftBuildClient(monkeypatch, SyncExecDouble(), dictWorkflow)
    responseHttp = clientSync.post(
        f"/api/zenodo/{S_CONTAINER_ID}/archive-completion")
    assert responseHttp.status_code == 409
    assert S_PROMOTION_ID in responseHttp.json()["detail"]
    assert listPerformed == []


def testTheRetryRefusesAnIncoherentObservation(monkeypatch, tmp_path):
    dictWorkflow = _fdictWorkflowWithCheckpoint(tmp_path)
    listPerformed = []
    _fnServeObservations(monkeypatch, [_fdictObservation(bCoherent=False)],
                         listPerformed)
    clientSync, _, _ = ftBuildClient(monkeypatch, SyncExecDouble(), dictWorkflow)
    responseHttp = clientSync.post(
        f"/api/zenodo/{S_CONTAINER_ID}/archive-completion")
    assert responseHttp.status_code == 409
    assert "files changed while vaibify was reading" in responseHttp.json()[
        "detail"]
    assert listPerformed == []


def testTheRetryRefusesWhenTheLatestVersionIsAlreadyACompletion(
    monkeypatch, tmp_path,
):
    dictWorkflow = _fdictWorkflowWithCheckpoint(tmp_path)
    dictWorkflow["dictRemotes"]["zenodo"]["dictReceipt"]["sKind"] = "completion"
    _fnServeObservations(monkeypatch, [_fdictObservation()], [])
    clientSync, _, _ = ftBuildClient(monkeypatch, SyncExecDouble(), dictWorkflow)
    assert clientSync.post(
        f"/api/zenodo/{S_CONTAINER_ID}/archive-completion").status_code == 409


def testTheArchiveRefusesWhileACompletionIsInFlight():
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as excInfo:
        syncRoutes._fnRefuseWhileACompletionIsInFlight(
            {"listPendingPromotions": [fdictCompletionRecord()]})
    assert excInfo.value.status_code == 409
    assert S_PROMOTION_ID in excInfo.value.detail


# ── Recovery ─────────────────────────────────────────────────────────


@pytest.mark.falsification
def testAdoptingACompletionNeverReachesTheImageAdopter(monkeypatch):
    """A completion's adoption writes the deposit record, never environment.json.

    Kills: the completion lane sent to the image adopter.
    """
    import asyncio
    listSettled = []

    def fnImageAdopter(*_listArgs):
        raise AssertionError("the image adopter would stamp environment.json")

    async def fdictSettle(*listArgs):
        listSettled.append(listArgs)
        return {"bCompleted": True}

    monkeypatch.setattr(promotionRecoveryRoutes, "_fnAdoptImageDeposit",
                        fnImageAdopter)
    monkeypatch.setattr(syncDispatcher, "fdictSettlePublishedCompletion",
                        fdictSettle)
    monkeypatch.setattr(
        promotionRecoveryRoutes, "_fbaRestoreCompletionReceiptOrNone",
        lambda *_listArgs: BA_COMPLETION_RECEIPT)
    dictOutcome = asyncio.run(promotionRecoveryRoutes._fdictAdoptForLane(
        {}, S_CONTAINER_ID, {}, None, "key",
        dict(fdictCompletionRecord(), iDepositId=200),
        {"sDoi": "10.5072/zenodo.200", "sConceptDoi": "10.5072/zenodo.100"},
        None))
    assert dictOutcome["bAdopted"] is True
    assert listSettled[0][6] == BA_COMPLETION_RECEIPT


@pytest.mark.falsification
def testResumeUploadsTheStoredBytesNotTheRepositoryCopy(monkeypatch, tmp_path):
    """After a restart the stored bytes are the ones uploaded, byte for byte.

    Kills: resume reads the repository copy (or rebuilds) instead of the
    stored receipt.
    """
    import asyncio
    sRepo = str(tmp_path / "repo")
    os.makedirs(sRepo + "/.vaibify/pendingReceipts")
    with open(sRepo + f"/.vaibify/pendingReceipts/{S_PROMOTION_ID}.json",
              "wb") as filePending:
        filePending.write(BA_COMPLETION_RECEIPT)
    with open(sRepo + "/.vaibify/proofReceipt.json", "wb") as fileRepository:
        fileRepository.write(b'{"replaced": "by someone else"}\n')
    clientZenodo = _ArchiveZenodo()
    clientZenodo.fnAddVersion(200, {"out.csv": BA_DATA}, sState="unsubmitted",
                              sDescription=archivePromotion
                              .fsStampPromotionIdIntoDescription(
                                  "Data.", S_PROMOTION_ID))
    dictRecord = dict(fdictCompletionRecord(), iDepositId=200)
    listSettled = _fnStandInRecoveryCarriers(
        monkeypatch, clientZenodo, sRepo)
    asyncio.run(promotionRecoveryRoutes._fdictResumeCompletion(
        {}, S_CONTAINER_ID, {"sProjectRepoPath": sRepo}, dictRecord, None))
    assert [tCall for tCall in clientZenodo.listCalls
            if tCall[0] == "upload"] == [
        ("upload", S_RECEIPT_KEY, BA_COMPLETION_RECEIPT)]
    assert listSettled[0][6] == BA_COMPLETION_RECEIPT


def _fnStandInRecoveryCarriers(monkeypatch, clientZenodo, sRepo):
    """Run the recovery's workers inline over a host repository."""
    from vaibify.gui import routeContext
    listSettled = []

    async def fgenericRunInline(_sId, fnWorker, _sTarget, _request):
        return fnWorker()["objResult"]

    async def fdictSettle(*listArgs):
        listSettled.append(listArgs)
        return {"bCompleted": True}

    monkeypatch.setattr(routeContext, "fgenericRunWorkerUnderTheDrain",
                        fgenericRunInline)
    monkeypatch.setattr(routeContext, "ffilesForWorkflow",
                        lambda *_listArgs: HostRepoFiles(sRepo))
    monkeypatch.setattr(promotionRecoveryRoutes, "ffilesForWorkflow",
                        lambda *_listArgs: HostRepoFiles(sRepo))
    monkeypatch.setattr(syncDispatcher, "fdictSettlePublishedCompletion",
                        fdictSettle)
    monkeypatch.setattr(
        promotionRecoveryRoutes, "_ftRequireSettleableDeposit",
        lambda *_listArgs: (clientZenodo, {}))
    monkeypatch.setattr(syncDispatcher, "fdictCompletionJournalHooks",
                        lambda *_listArgs: fdictJournalHooks([]))
    return listSettled


def testAnOrphanedPendingReceiptIsSwept(tmp_path):
    sRepo = str(tmp_path / "repo")
    os.makedirs(sRepo + "/.vaibify/pendingReceipts")
    sLive = "promotion-" + "1" * 32
    sOrphan = "promotion-" + "2" * 32
    for sId in (sLive, sOrphan):
        with open(sRepo + f"/.vaibify/pendingReceipts/{sId}.json", "w") as f:
            f.write("{}")
    filesRepo = HostRepoFiles(sRepo)
    syncBookkeeping.fnUpdatePendingPromotion(
        filesRepo, ".vaibify/projects/project.json", sLive,
        {"sPromotionId": sLive, "sLane": "completion"})
    syncDispatcher.fnSweepOrphanPendingReceipts(filesRepo)
    assert sorted(os.listdir(sRepo + "/.vaibify/pendingReceipts")) == [
        sLive + ".json"]


def testACompletionNoteRecordsTheCompletionReceipt():
    dictNote = archivePromotion.fdictDescribeCompletionReceiptNote(
        dict(fdictCompletionRecord(), iDepositId=200),
        {"iDepositId": 200, "sDoi": "10.5072/zenodo.200"})
    assert dictNote["sKind"] == "completion"
    assert dictNote["sSha256"] == hashlib.sha256(
        BA_COMPLETION_RECEIPT).hexdigest()
    assert dictNote["listContents"] == fdictCheckpointNote()["listContents"]
    assert json.dumps(dictNote)


# ── What the Zenodo row says ─────────────────────────────────────────


def _fsRepoWithZenodoRecordAbout(tmp_path, listRecordIds):
    from tests.syncStatusFixtures import fdictBuildCachedVerify, fsRecentVerifyIso
    sRepo = str(tmp_path / "repo")
    os.makedirs(sRepo + "/.vaibify")
    with open(sRepo + "/.vaibify/syncStatus.json", "w") as fileStatus:
        json.dump({"zenodo": fdictBuildCachedVerify(
            sService="zenodo", sLastVerified=fsRecentVerifyIso(),
            sZenodoDoi="10.5072/zenodo.101", sEndpointVerified="sandbox",
            dictRemoteIdentity={"sEndpoint": "sandbox",
                                "listRecordIds": listRecordIds})},
            fileStatus)
    return sRepo


def _fdictWorkflowAfterCompletion(sRepo, sVerifyWarning=""):
    dictNote = archivePromotion.fdictDescribeCompletionReceiptNote(
        dict(fdictCompletionRecord(), iDepositId=200),
        {"iDepositId": 200, "sDoi": "10.5072/zenodo.200"})
    if sVerifyWarning:
        dictNote["sVerifyWarning"] = sVerifyWarning
    return {"sProjectRepoPath": sRepo, "sZenodoService": "sandbox",
            "dictRemotes": {"zenodo": {
                "sRecordId": "200", "sService": "sandbox",
                "sDoi": "10.5072/zenodo.200", "dictReceipt": dictNote}}}


@pytest.mark.falsification
def testAFailedReverifyAfterCompletionShowsVerificationPendingNotMet(tmp_path):
    """Until a verify of the NEW record succeeds, the row says pending, and why.

    Kills: the pending state dropped -- a completion read as verified on
    the strength of a record about its checkpoint.
    """
    from vaibify.reproducibility import proofReceipt
    sRepo = _fsRepoWithZenodoRecordAbout(tmp_path, ["101"])
    dictState = proofReceipt.fdictDescribeArchiveReceiptState(
        _fdictWorkflowAfterCompletion(sRepo, "the record lags the index"),
        sRepo)
    assert dictState["sState"] == "completion-verify-failed"
    assert "the record lags the index" in dictState["sMessage"]
    assert dictState["bOfferCompletion"] is False
    assert proofReceipt.fdictDescribeArchiveReceiptState(
        _fdictWorkflowAfterCompletion(sRepo), sRepo)["sMessage"].endswith(
        "verification pending.")


def testACompletionVerifiedAgainstItsOwnRecordReadsCompletion(tmp_path):
    from vaibify.reproducibility import proofReceipt
    sRepo = _fsRepoWithZenodoRecordAbout(tmp_path, ["200"])
    assert proofReceipt.fdictDescribeArchiveReceiptState(
        _fdictWorkflowAfterCompletion(sRepo, "stale warning"), sRepo)[
        "sState"] == "completion"


def testACheckpointOffersTheCompletionAndAnInFlightOneDoesNot(tmp_path):
    from vaibify.reproducibility import proofReceipt
    sRepo = _fsRepoWithZenodoRecordAbout(tmp_path, ["101"])
    dictWorkflow = {"sProjectRepoPath": sRepo, "dictRemotes": {"zenodo": {
        "sRecordId": "101", "dictReceipt": fdictCheckpointNote()}}}
    dictState = proofReceipt.fdictDescribeArchiveReceiptState(
        dictWorkflow, sRepo)
    assert (dictState["sState"], dictState["bOfferCompletion"]) == (
        "checkpoint", True)
    dictWorkflow["listPendingPromotions"] = [fdictCompletionRecord()]
    dictState = proofReceipt.fdictDescribeArchiveReceiptState(
        dictWorkflow, sRepo)
    assert (dictState["sState"], dictState["bOfferCompletion"]) == (
        "completion-in-flight", False)
    assert proofReceipt.fdictDescribeArchiveReceiptState(
        {"dictRemotes": {}}, sRepo)["sState"] == "none"
