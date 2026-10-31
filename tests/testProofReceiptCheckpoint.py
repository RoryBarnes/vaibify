"""Every project deposit carries a checkpoint receipt built from what it uploads.

Source: ``vaibify/gui/routes/syncRoutes.py`` (the observation before
staging, the receipt hook, the persist step's record and repository
write) and ``vaibify/gui/syncDispatcher.py`` (the generic staging hook).

The real dispatcher runs here, staging through a read-only connection
and uploading to a recording Zenodo, so the bytes a test inspects are
the bytes the deposit received. Only the observation is given: its
own tests live in ``tests/testProofObservation.py``.
"""

import copy
import hashlib
import json

import pytest

from tests.testProofReceiptDocument import fdictKnownObservation
from tests.testZenodoHostArchive import (  # noqa: F401  (fixtures)
    _ReadOnlyConnection,
    _RecordingZenodo,
    clientZenodoFake,
    fixtureHostZenodoSlots,
)
from vaibify.gui import syncDispatcher
from vaibify.gui.routes import syncRoutes
from vaibify.reproducibility import proofReceipt, syncBookkeeping


S_CONTAINER_ID = "receiptArchiveContainer"
S_REPO = "/workspace/repo"
S_OUT = S_REPO + "/Step/out.csv"
BA_AT_OBSERVATION = b"a,b\n1,2\n"
BA_AT_STAGING = b"a,b\n1,2\n3,4\n"
DICT_METADATA = {"sTitle": "A project", "listCreators": [{"sName": "X"}]}


def _fdictWorkflow():
    return {"sProjectRepoPath": S_REPO,
            "sZenodoConceptDoi": "10.5281/zenodo.100"}


@pytest.fixture
def fnArchive(monkeypatch, clientZenodoFake, fixtureHostZenodoSlots):
    """Archive one file whose bytes changed between observation and staging."""
    fixtureHostZenodoSlots.fnStoreActive("sandbox", "active-token")
    dictObservation = fdictKnownObservation()
    dictObservation["dictRecords"]["zenodo"]["dictComparedHashes"] = {
        "Step/out.csv": hashlib.sha256(BA_AT_OBSERVATION).hexdigest()}
    monkeypatch.setattr(proofReceipt, "fdictCaptureObservation",
                        lambda _c, _i, _w: copy.deepcopy(dictObservation))
    connection = _ReadOnlyConnection({S_OUT: BA_AT_STAGING})

    def ftArchive(dictCheckpoint):
        return syncRoutes._ftArchiveWithCheckpointReceipt(
            syncDispatcher, {"docker": connection}, S_CONTAINER_ID,
            _fdictWorkflow(), [S_OUT], "sandbox", DICT_METADATA, 101,
            dictCheckpoint)
    return ftArchive


def _fbaUploadedReceipt():
    return _RecordingZenodo.listUploadsOfLast[proofReceipt.S_RECEIPT_DEPOSIT_KEY]


@pytest.fixture(autouse=True)
def fnRememberUploads(monkeypatch):
    """Keep the last fake client's uploads reachable after the call."""
    fnRealInit = _RecordingZenodo.__init__

    def fnInit(self, *listArgs, **dictKeywords):
        fnRealInit(self, *listArgs, **dictKeywords)
        _RecordingZenodo.listUploadsOfLast = self.dictUploaded
    monkeypatch.setattr(_RecordingZenodo, "__init__", fnInit)


@pytest.mark.falsification
def testTheInventoryIsTheStagedBytesWhenAFileChangesAfterTheObservation(
    fnArchive,
):
    """The receipt lists what was uploaded, not what the repository held earlier.

    Kills: the inventory taken from the repository as observed.
    """
    dictCheckpoint = {}
    iExit, sOut = fnArchive(dictCheckpoint)
    assert iExit == 0, sOut
    listContents = dictCheckpoint["dictReceipt"]["dictArchive"]["listContents"]
    assert listContents == [{
        "sKey": "out.csv", "sRepoPath": "Step/out.csv",
        "sSha256": hashlib.sha256(BA_AT_STAGING).hexdigest(),
        "sMd5": hashlib.md5(BA_AT_STAGING, usedforsecurity=False).hexdigest(),
        "iSizeBytes": len(BA_AT_STAGING)}]


def testTheUploadedReceiptIsTheOneSerialization(fnArchive):
    dictCheckpoint = {}
    iExit, _sOut = fnArchive(dictCheckpoint)
    assert iExit == 0
    assert _fbaUploadedReceipt() == dictCheckpoint["baReceipt"]
    dictReceipt = json.loads(dictCheckpoint["baReceipt"])
    assert dictReceipt["sKind"] == "checkpoint"
    assert dictReceipt["dictArchive"]["sFollowsRecordId"] == "101"
    assert dictReceipt["dictArchive"]["sConceptDoi"] == "10.5281/zenodo.100"


@pytest.mark.falsification
def testTheRepositoryReceiptIsTheUploadedBytes(fnArchive, monkeypatch):
    """The repository copy is the same serialization the deposit received.

    Kills: the repository copy serialized a second time.
    """
    dictCheckpoint = {}
    fnArchive(dictCheckpoint)
    listWritten = []
    monkeypatch.setattr(
        syncDispatcher, "fsCommitProofReceiptWrite",
        lambda _c, _i, _w, _r, baReceipt: listWritten.append(baReceipt) or "")
    syncRoutes._fnWriteRepositoryReceipt(
        {}, S_CONTAINER_ID, _fdictWorkflow(), None, {}, dictCheckpoint)
    assert listWritten == [_fbaUploadedReceipt()]


@pytest.mark.falsification
def testACheckpointWhoseUploadFailsLeavesTheRepositoryReceiptAlone(
    fnArchive, monkeypatch,
):
    """Nothing is written into the repository until a version exists.

    Kills: the repository receipt written before publication.
    """
    from vaibify.reproducibility import zenodoClient

    def fnRefuseUpload(self, sBucketUrl, sPath, **dictKeywords):
        raise zenodoClient.ZenodoError("HTTP 500 from the bucket")

    listTouched = []
    monkeypatch.setattr(_RecordingZenodo, "fnUploadToBucket", fnRefuseUpload)
    monkeypatch.setattr(
        syncDispatcher, "fnWriteProofReceiptIntoProject",
        lambda *listArgs: listTouched.append(listArgs))
    monkeypatch.setattr(
        syncDispatcher, "fsCommitProofReceiptWrite",
        lambda *listArgs: listTouched.append(listArgs) or "")
    iExit, sOut = fnArchive({})
    assert iExit == 1 and "HTTP" in sOut
    assert listTouched == []


def testAFailedRepositoryWriteAfterPublicationWarnsAndKeepsTheArchive(
    fnArchive, monkeypatch, caplog,
):
    dictCheckpoint = {}
    fnArchive(dictCheckpoint)
    monkeypatch.setattr(
        syncDispatcher, "fsCommitProofReceiptWrite",
        lambda *listArgs: "refused: '.vaibify' is a symlink")
    dictResult = {"bSuccess": True}
    syncRoutes._fnWriteRepositoryReceipt(
        {}, S_CONTAINER_ID, _fdictWorkflow(), None, dictResult, dictCheckpoint)
    assert dictResult["bSuccess"] is True
    assert "could not be written into the repository" in dictResult[
        "sProofReceiptWarning"]
    assert "symlink" in caplog.text


def testTheDepositRecordNotesTheReceiptAsBookkeeping(fnArchive):
    dictCheckpoint = {}
    fnArchive(dictCheckpoint)
    dictWorkflow = {"dictRemotes": {"zenodo": {"listRecords": []}}}
    syncRoutes._fnRecordPublishedReceipt(
        dictWorkflow, {"iDepositId": 102, "sDoi": "10.5281/zenodo.102"},
        dictCheckpoint)
    dictNote = dictWorkflow["dictRemotes"]["zenodo"]["dictReceipt"]
    assert dictNote["sKind"] == "checkpoint"
    assert dictNote["sRecordId"] == "102"
    assert dictNote["sSha256"] == hashlib.sha256(
        dictCheckpoint["baReceipt"]).hexdigest()
    assert dictNote["listContents"][0]["sRepoPath"] == "Step/out.csv"
    dictDeclarative = copy.deepcopy(dictWorkflow)
    syncBookkeeping.fdictExtractSyncBookkeeping(dictDeclarative)
    assert "dictReceipt" not in json.dumps(dictDeclarative), (
        "the note would land in project.json, which every remote compares")


def testAnObservationThatFailsRefusesBeforeAnyUpload(
    monkeypatch, clientZenodoFake, fixtureHostZenodoSlots,
):
    fixtureHostZenodoSlots.fnStoreActive("sandbox", "active-token")

    def fdictBrokenCapture(_c, _i, _w):
        raise RuntimeError("the container stopped")

    monkeypatch.setattr(proofReceipt, "fdictCaptureObservation",
                        fdictBrokenCapture)
    iExit, sOut = syncRoutes._ftArchiveWithCheckpointReceipt(
        syncDispatcher, {"docker": _ReadOnlyConnection({S_OUT: b"x"})},
        S_CONTAINER_ID, _fdictWorkflow(), [S_OUT], "sandbox", DICT_METADATA,
        0, {})
    assert iExit == 1
    assert "nothing was published" in sOut and "the container stopped" in sOut
    assert clientZenodoFake.listBuilt == []


def testASelectedFileNamedLikeTheReceiptIsRefused():
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as excInfo:
        syncRoutes._fnRefuseBasenameCollisions(
            [S_OUT, S_REPO + "/Step/proofReceipt.json"])
    assert excInfo.value.status_code == 400
    assert "proofReceipt.json" in excInfo.value.detail


def testTheDispatcherRefusesAnExtraFileThatCollides(tmp_path):
    with pytest.raises(ValueError):
        syncDispatcher._fsStageExtraFile(
            str(tmp_path), [S_REPO + "/proofReceipt.json"], [],
            "proofReceipt.json", lambda _l: b"{}")


def testAFirstArchiveFollowsNoRecord():
    dictArchive = syncRoutes._fdictCheckpointArchive(
        "sandbox", _fdictWorkflow(), 0, [])
    assert (dictArchive["sFollowsRecordId"], dictArchive["sConceptDoi"]) == (
        "", "")


def testTheEnvironmentImageDepositCarriesNoReceipt():
    """Receipts are for project deposits; the image lane never stages one."""
    import inspect
    from vaibify.reproducibility import imageDeposit
    sSource = inspect.getsource(imageDeposit)
    assert "proofReceipt" not in sSource
    assert "fbaBuildExtraFile" not in sSource
