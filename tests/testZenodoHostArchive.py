"""The project deposit and the token validation run on the host, with no exec.

Since 2026-10-29 the Zenodo token never enters the container, so the
deposit (``syncDispatcher.ftResultArchiveToZenodo``) streams the
selected files out through the confined reader and uploads them from
the host with the project's ACTIVE token, and the validation
(``ftResultValidateZenodoToken``) probes the instance from the host.
The connection double here has no exec surface at all: an exec would
be an ``AttributeError``, which is the proof that none happens.
"""

import hashlib
import os

import pytest

from tests.zenodoTokenFixtures import (  # noqa: F401  (autouse fixture)
    fixtureHostZenodoSlots,
)
from vaibify.gui import syncDispatcher
from vaibify.reproducibility import zenodoClient

S_CONTAINER_ID = "cid-archive-not-the-name"
DICT_METADATA = {"sTitle": "A project", "listCreators": [{"sName": "X"}]}


class _ReadOnlyConnection:
    """Serves file bytes through the confined reader; nothing else exists."""

    def __init__(self, dictFiles):
        self.dictFiles = dict(dictFiles)
        self.listRead = []

    def fiterReadFileConfined(self, sContainerId, sFilePath, sAuthorizedRoot=None):
        self.listRead.append((sFilePath, sAuthorizedRoot))
        if sFilePath not in self.dictFiles:
            raise FileNotFoundError(f"Not found: {sFilePath}")
        yield self.dictFiles[sFilePath]


class _RecordingZenodo:
    """A Zenodo that records how it was built and what it was asked."""

    listBuilt = []
    listPhasesOfLast = []
    bRefuseDraftByDefault = False

    def __init__(self, sService="sandbox", sToken=None, sBaseUrl=None):
        _RecordingZenodo.listBuilt.append((sService, sToken))
        self.listPhases = []
        _RecordingZenodo.listPhasesOfLast = self.listPhases
        self.dictUploaded = {}
        self.bRefuseDraft = _RecordingZenodo.bRefuseDraftByDefault

    def fdictCreateDraft(self, dictMetadata):
        if self.bRefuseDraft:
            raise zenodoClient.ZenodoAuthError("Zenodo authentication failed (401)")
        self.listPhases.append(("created", dictMetadata["title"]))
        return {"id": 42, "links": {"bucket": "https://example/b"}}

    def fdictGetNewVersionDraft(self, iParent):
        self.listPhases.append(("newversion", iParent))
        return {"id": 43, "links": {"bucket": "https://example/b"}}

    def fnClearDraftFiles(self, iDepositId):
        self.listPhases.append(("cleared", iDepositId))

    def fnSetMetadata(self, iDepositId, dictMetadata):
        self.listPhases.append(("metadata", iDepositId))

    def fnUploadToBucket(self, sBucketUrl, sPath, **dictKeywords):
        with open(sPath, "rb") as fileLocal:
            self.dictUploaded[os.path.basename(sPath)] = fileLocal.read()
        self.listPhases.append(("upload", os.path.basename(sPath)))

    def fdictGetDeposit(self, iDepositId):
        return {"files": [
            {"key": sKey, "filesize": len(ba),
             "checksum": "md5:" + hashlib.md5(ba, usedforsecurity=False).hexdigest()}
            for sKey, ba in self.dictUploaded.items()
        ]}

    def fdictPublishDraft(self, iDepositId):
        self.listPhases.append(("published", iDepositId))
        return {"doi": "10.5072/zenodo.42", "conceptdoi": "10.5072/zenodo.41",
                "links": {"html": "https://example/r/42"}}

    def fnDeleteDraft(self, iDepositId):
        self.listPhases.append(("discarded", iDepositId))


@pytest.fixture
def clientZenodoFake(monkeypatch):
    _RecordingZenodo.listBuilt = []
    monkeypatch.setattr(zenodoClient, "ZenodoClient", _RecordingZenodo)
    return _RecordingZenodo


def _ftArchive(connection, listPaths, sService="sandbox", iParent=0):
    return syncDispatcher.ftResultArchiveToZenodo(
        connection, S_CONTAINER_ID, sService, listPaths, DICT_METADATA, iParent,
    )


def testTheDepositRunsOnTheHostWithTheActiveToken(
    clientZenodoFake, fixtureHostZenodoSlots, tmp_path, monkeypatch,
):
    monkeypatch.setenv("HOME", str(tmp_path))
    fixtureHostZenodoSlots.fnStoreActive("sandbox", "active-token")
    connection = _ReadOnlyConnection({"/workspace/repo/out/data.csv": b"a,b\n"})
    iExit, sOut = _ftArchive(connection, ["/workspace/repo/out/data.csv"])
    assert iExit == 0, sOut
    assert sOut.startswith("ZENODO_RESULT=")
    assert '"sDoi": "10.5072/zenodo.42"' in sOut
    assert clientZenodoFake.listBuilt == [("sandbox", "active-token")]
    assert connection.listRead == [("/workspace/repo/out/data.csv", "/workspace")]
    assert not list((tmp_path / ".vaibify").rglob("data.csv")), (
        "the staged copy is removed after the upload"
    )


def testTheProductionInstanceSpendsTheProductionToken(
    clientZenodoFake, fixtureHostZenodoSlots,
):
    fixtureHostZenodoSlots.fnStoreActive("sandbox", "sandbox-token")
    fixtureHostZenodoSlots.fnStoreActive("production", "production-token")
    connection = _ReadOnlyConnection({"/workspace/a.txt": b"x"})
    iExit, _ = _ftArchive(connection, ["/workspace/a.txt"], sService="zenodo")
    assert iExit == 0
    assert clientZenodoFake.listBuilt == [("zenodo", "production-token")]


def testWithoutAnActiveTokenNothingIsBuiltAndTheRefusalSaysWhy(clientZenodoFake):
    connection = _ReadOnlyConnection({"/workspace/a.txt": b"x"})
    iExit, sOut = _ftArchive(connection, ["/workspace/a.txt"])
    assert iExit == 1
    assert sOut.startswith("no-token: ")
    assert "No sandbox Zenodo token is stored on this host" in sOut
    assert clientZenodoFake.listBuilt == []
    assert connection.listRead == [], "no file leaves the container for nothing"


def testAPendingTokenIsNotSpent(clientZenodoFake, fixtureHostZenodoSlots):
    fixtureHostZenodoSlots.fnStorePending("zenodo_token_sandbox", "pending-token")
    iExit, sOut = _ftArchive(_ReadOnlyConnection({}), ["/workspace/a.txt"])
    assert iExit == 1
    assert "awaiting your confirmation" in sOut
    assert clientZenodoFake.listBuilt == []


def testTheVersionedFlowClearsAndReMetadatasTheParentDraft(
    clientZenodoFake, fixtureHostZenodoSlots,
):
    fixtureHostZenodoSlots.fnStoreActive("sandbox", "t")
    connection = _ReadOnlyConnection({"/workspace/a.txt": b"x"})
    iExit, sOut = _ftArchive(connection, ["/workspace/a.txt"], iParent=7)
    assert iExit == 0, sOut
    listPhases = [tPhase[0] for tPhase in clientZenodoFake.listPhasesOfLast]
    assert listPhases == ["newversion", "cleared", "metadata", "upload", "published"]


def testAnHttpRefusalOnTheDraftIsReportedAsHttp(
    clientZenodoFake, fixtureHostZenodoSlots, monkeypatch,
):
    fixtureHostZenodoSlots.fnStoreActive("sandbox", "t")
    monkeypatch.setattr(_RecordingZenodo, "bRefuseDraftByDefault", True)
    iExit, sOut = _ftArchive(_ReadOnlyConnection({"/workspace/a.txt": b"x"}), ["/workspace/a.txt"])
    assert iExit == 1
    assert sOut.startswith("HTTP ")
    assert syncDispatcher.fdictClassifyError(1, sOut)["sErrorType"] == "auth"


def testTwoSelectedFilesWithOneNameAreALocalError(clientZenodoFake, fixtureHostZenodoSlots):
    fixtureHostZenodoSlots.fnStoreActive("sandbox", "t")
    connection = _ReadOnlyConnection({"/workspace/a/x.txt": b"1", "/workspace/b/x.txt": b"2"})
    iExit, sOut = _ftArchive(connection, ["/workspace/a/x.txt", "/workspace/b/x.txt"])
    assert iExit == 1
    assert sOut.startswith("LOCAL-FILE-ERROR")
    assert "x.txt" in sOut
    assert clientZenodoFake.listBuilt == [], "nothing was uploaded"


def testAMissingFileIsALocalErrorBeforeAnyDraftExists(clientZenodoFake, fixtureHostZenodoSlots):
    fixtureHostZenodoSlots.fnStoreActive("sandbox", "t")
    iExit, sOut = _ftArchive(_ReadOnlyConnection({}), ["/workspace/absent.csv"])
    assert iExit == 1
    assert sOut.startswith("LOCAL-FILE-ERROR")
    assert syncDispatcher.fdictClassifyError(1, sOut)["sErrorType"] == "localFileMissing"
    assert clientZenodoFake.listBuilt == []


def testAnInvalidServiceOrPathIsRefusedBeforeTheToken(clientZenodoFake):
    with pytest.raises(ValueError, match="Invalid Zenodo service"):
        _ftArchive(_ReadOnlyConnection({}), ["/a"], sService="production")
    with pytest.raises(ValueError, match="Null byte"):
        _ftArchive(_ReadOnlyConnection({}), ["/a\x00b"])


# ---------------------------------------------------------------------------
# Validation and the connectivity check
# ---------------------------------------------------------------------------


def _fnProbeAnswers(monkeypatch, sOutcome, sDetail="detail text"):
    listAsked = []

    def fdictProbe(sService, sToken):
        listAsked.append((sService, sToken))
        return {"sOutcome": sOutcome, "sDetail": sDetail, "listDepositTitles": []}

    monkeypatch.setattr(zenodoClient, "fdictProbeToken", fdictProbe)
    return listAsked


def testAPassingValidationCarriesNoDetail(fixtureHostZenodoSlots, monkeypatch):
    fixtureHostZenodoSlots.fnStoreActive("sandbox", "tok")
    listAsked = _fnProbeAnswers(monkeypatch, zenodoClient.S_PROBE_ACCEPTED)
    assert syncDispatcher.ftResultValidateZenodoToken(object(), S_CONTAINER_ID, "sandbox") == (
        True, "",
    )
    assert listAsked == [("sandbox", "tok")]


def testARejectionNamesTheInstanceThatRejectedAndNotTheResponse(
    fixtureHostZenodoSlots, monkeypatch,
):
    fixtureHostZenodoSlots.fnStoreActive("sandbox", "tok")
    _fnProbeAnswers(monkeypatch, zenodoClient.S_PROBE_REJECTED, "SECRETLOOKINGSTRING")
    bPass, sDetail = syncDispatcher.ftResultValidateZenodoToken(object(), S_CONTAINER_ID, "sandbox")
    assert bPass is False
    assert "sandbox.zenodo.org rejected" in sDetail
    assert "not zenodo.org" in sDetail
    assert "SECRETLOOKINGSTRING" not in sDetail


def testAProductionRejectionPointsTheOtherWay(fixtureHostZenodoSlots, monkeypatch):
    fixtureHostZenodoSlots.fnStoreActive("production", "tok")
    _fnProbeAnswers(monkeypatch, zenodoClient.S_PROBE_REJECTED)
    _, sDetail = syncDispatcher.ftResultValidateZenodoToken(object(), S_CONTAINER_ID, "zenodo")
    assert "zenodo.org rejected" in sDetail
    assert "sandbox.zenodo.org" in sDetail


def testAnUndeterminedProbeIsNotATokenProblem(fixtureHostZenodoSlots, monkeypatch):
    fixtureHostZenodoSlots.fnStoreActive("sandbox", "tok")
    _fnProbeAnswers(monkeypatch, zenodoClient.S_PROBE_UNDETERMINED, "ConnectionError: x")
    bPass, sDetail = syncDispatcher.ftResultValidateZenodoToken(object(), S_CONTAINER_ID, "sandbox")
    assert bPass is False
    assert "not a token problem" in sDetail
    assert "Nothing about the token was decided" in sDetail


def testAMissingTokenSaysNothingWasTested(monkeypatch):
    listAsked = _fnProbeAnswers(monkeypatch, zenodoClient.S_PROBE_ACCEPTED)
    bPass, sDetail = syncDispatcher.ftResultValidateZenodoToken(object(), S_CONTAINER_ID, "sandbox")
    assert bPass is False
    assert "No sandbox Zenodo token is stored on this host" in sDetail
    assert listAsked == []


def testTheConnectivityCheckReadsTheHostActiveSlots(fixtureHostZenodoSlots):
    assert syncDispatcher.fdictCheckConnectivity(object(), S_CONTAINER_ID, "zenodo") == {
        "bConnected": False, "sMessage": "Token not found",
    }
    fixtureHostZenodoSlots.fnStoreActive("production", "tok")
    assert syncDispatcher.fdictCheckConnectivity(object(), S_CONTAINER_ID, "zenodo") == {
        "bConnected": True, "sMessage": "Connected",
    }


def testTheConnectivityCheckNamesAPendingTokenAndAnUnreadableKeyring(fixtureHostZenodoSlots):
    fixtureHostZenodoSlots.fnStorePending("zenodo_token_sandbox", "pending")
    dictCheck = syncDispatcher.fdictCheckConnectivity(object(), S_CONTAINER_ID, "zenodo")
    assert dictCheck["bConnected"] is False
    assert "awaits your confirmation" in dictCheck["sMessage"]
    fixtureHostZenodoSlots.fnBreakEveryRead()
    dictCheck = syncDispatcher.fdictCheckConnectivity(object(), S_CONTAINER_ID, "zenodo")
    assert dictCheck["bConnected"] is False
    assert dictCheck["sMessage"].startswith("Could not check")
