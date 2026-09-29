"""A bucket upload says how far it got, and survives a dropped connection.

A one-gigabyte environment deposit failed live on an SSL EOF twenty-odd
minutes into a single PUT, having shown nothing but "Uploading to
Zenodo" the whole time (2026-09-29). These tests drive the REAL
``requests`` stack against a loopback HTTP server, because both
properties live in how ``requests`` treats the body object: whether it
declares a length or falls back to chunked encoding, and whether a
connection the server drops surfaces as an error the retry catches.
"""

import http.server
import threading

import pytest

from vaibify.gui import archiveProgress
from vaibify.gui.routes import environmentArchiveRoutes
from vaibify.reproducibility import zenodoClient


_I_FILE_BYTES = 3 * 1024 * 1024 + 17


class _BucketServer:
    """A loopback bucket that can drop its first N uploads mid-body."""

    def __init__(self, iDropFirst=0, iStatus=200):
        self.listBodies = []
        self.listHeaders = []
        self.iRequests = 0
        serverSelf = self

        class _Handler(http.server.BaseHTTPRequestHandler):
            def do_PUT(self):
                serverSelf.iRequests += 1
                serverSelf.listHeaders.append(dict(self.headers))
                if serverSelf.iRequests <= iDropFirst:
                    self.rfile.read(64 * 1024)
                    self.close_connection = True
                    self.connection.close()
                    return
                iLength = int(self.headers.get("Content-Length") or 0)
                serverSelf.listBodies.append(self.rfile.read(iLength))
                self.send_response(iStatus)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", "2")
                self.end_headers()
                self.wfile.write(b"{}")

            def log_message(self, *listArgs):
                pass

        self._server = http.server.ThreadingHTTPServer(
            ("127.0.0.1", 0), _Handler,
        )
        self.sBucketUrl = (
            f"http://127.0.0.1:{self._server.server_address[1]}/bucket"
        )
        self._thread = threading.Thread(
            target=self._server.serve_forever, daemon=True,
        )
        self._thread.start()

    def fnStop(self):
        self._server.shutdown()
        self._server.server_close()


@pytest.fixture
def sTarballPath(tmp_path):
    pathFile = tmp_path / "environment-image.tar.zst"
    pathFile.write_bytes(bytes(range(256)) * (_I_FILE_BYTES // 256) + b"x" * (
        _I_FILE_BYTES % 256))
    return str(pathFile)


@pytest.fixture
def fixtureNoRetryWait(monkeypatch):
    monkeypatch.setattr(zenodoClient, "_F_UPLOAD_RETRY_WAIT_SECONDS", 0.0)


def _fclientZenodo():
    return zenodoClient.ZenodoClient("sandbox", sToken="test-token")


@pytest.mark.falsification
def test_an_upload_arrives_whole_with_its_length_declared(sTarballPath):
    """The counting wrapper must not change what reaches the server.

    A body object ``requests`` cannot size is sent as zero bytes or
    chunked; either would be an upload that "succeeded" with the wrong
    content. The progress callback must end at the file's size.

    Kills: dropping the wrapper's ``__len__``.
    """
    serverBucket = _BucketServer()
    listReports = []
    try:
        _fclientZenodo().fnUploadToBucket(
            serverBucket.sBucketUrl, sTarballPath,
            fnReportProgress=lambda *tReport: listReports.append(tReport),
        )
    finally:
        serverBucket.fnStop()
    with open(sTarballPath, "rb") as fileHandle:
        baExpected = fileHandle.read()
    assert serverBucket.listBodies == [baExpected]
    assert serverBucket.listHeaders[0].get("Content-Length") == str(
        _I_FILE_BYTES)
    assert "Transfer-Encoding" not in serverBucket.listHeaders[0]
    assert listReports[-1] == (_I_FILE_BYTES, _I_FILE_BYTES, 1)
    assert len(listReports) >= 3, "the counter never moved mid-upload"


@pytest.mark.falsification
def test_a_dropped_connection_is_retried_from_the_start(
    sTarballPath, fixtureNoRetryWait,
):
    """The server drops the first upload mid-body; the second lands.

    Kills: a single upload attempt, which turns one dropped connection
    into a failed deposit -- the live failure.
    """
    serverBucket = _BucketServer(iDropFirst=1)
    listReports = []
    try:
        _fclientZenodo().fnUploadToBucket(
            serverBucket.sBucketUrl, sTarballPath,
            fnReportProgress=lambda *tReport: listReports.append(tReport),
        )
    finally:
        serverBucket.fnStop()
    assert serverBucket.iRequests == 2
    assert len(serverBucket.listBodies[0]) == _I_FILE_BYTES
    assert listReports[-1] == (_I_FILE_BYTES, _I_FILE_BYTES, 2)


def test_every_attempt_dropped_names_the_attempts(
    sTarballPath, fixtureNoRetryWait,
):
    serverBucket = _BucketServer(iDropFirst=99)
    try:
        with pytest.raises(zenodoClient.ZenodoError, match="did not complete in 3 attempts"):
            _fclientZenodo().fnUploadToBucket(
                serverBucket.sBucketUrl, sTarballPath,
            )
    finally:
        serverBucket.fnStop()
    assert serverBucket.iRequests == 3


def test_a_gateway_error_is_retried_and_a_refusal_is_not(
    sTarballPath, fixtureNoRetryWait,
):
    """A 503 is Zenodo's overload; a 403 is Zenodo saying no."""
    serverGateway = _BucketServer(iStatus=503)
    try:
        with pytest.raises(zenodoClient.ZenodoError, match="503"):
            _fclientZenodo().fnUploadToBucket(
                serverGateway.sBucketUrl, sTarballPath,
            )
    finally:
        serverGateway.fnStop()
    assert serverGateway.iRequests == 3
    serverRefusing = _BucketServer(iStatus=403)
    try:
        with pytest.raises(zenodoClient.ZenodoAuthError):
            _fclientZenodo().fnUploadToBucket(
                serverRefusing.sBucketUrl, sTarballPath,
            )
    finally:
        serverRefusing.fnStop()
    assert serverRefusing.iRequests == 1


def test_the_upload_progress_reaches_the_row_record():
    """The hub's record carries the bytes and the attempt to the poll."""
    sContainerId, sRepo = "cid-upload-progress", "/repo/upload-progress"
    archiveProgress.fnRegisterDeposit(sContainerId, None, sRepo)
    try:
        environmentArchiveRoutes._ffnReportUploadProgress(sContainerId)(
            512, 2048, 2,
        )
        dictSeen = archiveProgress.fdictReadDeposit(sContainerId, sRepo)
    finally:
        archiveProgress.fnForgetDeposit(sContainerId, sRepo)
    assert dictSeen["sPhase"] == archiveProgress.S_PHASE_UPLOADING
    assert (dictSeen["iBytesRead"], dictSeen["iBytesTotal"],
            dictSeen["iAttempt"]) == (512, 2048, 2)


@pytest.mark.falsification
def test_every_step_of_a_deposit_names_itself_in_order(tmp_path, monkeypatch):
    """The row never sits on a stale phase while other work goes on.

    Driven through the route's synchronous deposit with only the image
    save, the separation check and Zenodo itself replaced. The steps
    between the byte counters -- reading the image to check the agents,
    preparing the draft -- used to leave the row on "starting" and then
    "saving" (researcher-reported, 2026-09-29).

    Kills: preparing the Zenodo draft without saying so, which leaves
    the finished save on screen while the draft is made.
    """
    pathTarball = tmp_path / "environment-image.tar.zst"
    pathTarball.write_bytes(b"not really an image")

    def ftFakeSave(sImageReference, sScratchDirectory, fnReportProgress):
        fnReportProgress(19, 19)
        return (str(pathTarball), "sha256:" + "b" * 64, 19,
                "sha256:" + "c" * 64, "d" * 32)

    class _FakeClient:
        def __init__(self, sService, sToken=None):
            self.sService = sService

        def fdictCreateDraft(self, dictMetadata):
            return {"id": 7, "links": {"bucket": "https://example/b"}}

        def fnUploadToBucket(self, sUrl, sPath, fnReportProgress=None, fnReportAttemptFailed=None):
            fnReportProgress(19, 19, 1)

        def fdictGetDeposit(self, iDepositId):
            return {"files": [{"key": "environment-image.tar.zst",
                               "checksum": "md5:" + "d" * 32,
                               "filesize": 19}]}

        def fdictPublishDraft(self, iDepositId):
            return {"doi": "10.5072/zenodo.7", "conceptdoi": ""}

    from vaibify.reproducibility import imageDeposit
    listPhases = []
    monkeypatch.setattr(imageDeposit, "ftSaveAndCompressImage", ftFakeSave)
    monkeypatch.setattr(
        imageDeposit, "fsResolveDepositScratchDirectory",
        lambda: str(tmp_path / "scratch"),
    )
    monkeypatch.setattr(zenodoClient, "ZenodoClient", _FakeClient)
    monkeypatch.setattr(
        environmentArchiveRoutes, "_fnRefuseAgentsInTheEnvironment",
        lambda sContainerId, dictContainer: None,
    )
    monkeypatch.setattr(
        archiveProgress, "fnRecordProgress",
        lambda sContainerId, sPhase, *listArgs, **dictKwargs: (
            listPhases.append(sPhase)
            if not listPhases or listPhases[-1] != sPhase else None
        ),
    )
    environmentArchiveRoutes._fdictDepositSynchronously(
        "cid", {"sWorkflowName": "w"},
        {"sImageDigest": "registry.example/p@sha256:" + "a" * 64,
         "sArchitecture": "arm64"},
        {"sZenodoService": "sandbox", "dictParentArchive": {}},
        "token", None,
    )
    assert listPhases == [
        "checking-agents", "saving", "preparing-draft", "uploading",
        "verifying",
    ]


@pytest.mark.falsification
def test_each_failed_attempt_is_reported_with_how_far_it_got(
    sTarballPath, fixtureNoRetryWait,
):
    """A researcher judges a retry by how far each attempt got.

    The server drops the first upload after reading 64 KB, so the
    attempt's bytes are real socket traffic, not a stubbed number.

    Kills: retrying without reporting the attempt that ended.
    """
    serverBucket = _BucketServer(iDropFirst=1)
    listFailed = []
    try:
        _fclientZenodo().fnUploadToBucket(
            serverBucket.sBucketUrl, sTarballPath,
            fnReportAttemptFailed=listFailed.append,
        )
    finally:
        serverBucket.fnStop()
    assert [d["iAttempt"] for d in listFailed] == [1]
    dictFailed = listFailed[0]
    assert dictFailed["sCause"] == "dropped"
    assert 0 < dictFailed["iBytesSent"] <= _I_FILE_BYTES
    assert dictFailed["iBytesTotal"] == _I_FILE_BYTES
    assert dictFailed["fSeconds"] >= 0
    assert set(dictFailed) == {
        "iAttempt", "iBytesSent", "iBytesTotal", "fSeconds", "sCause",
        "iStatus",
    }, "the report must carry no response or exception object"


def test_a_gateway_attempt_is_reported_with_its_status(
    sTarballPath, fixtureNoRetryWait,
):
    serverGateway = _BucketServer(iStatus=503)
    listFailed = []
    try:
        with pytest.raises(zenodoClient.ZenodoError):
            _fclientZenodo().fnUploadToBucket(
                serverGateway.sBucketUrl, sTarballPath,
                fnReportAttemptFailed=listFailed.append,
            )
    finally:
        serverGateway.fnStop()
    assert [(d["sCause"], d["iStatus"]) for d in listFailed] == [
        ("gateway", 503)] * 3


@pytest.mark.falsification
def test_failed_attempts_outlive_the_deposit_and_not_the_next_one():
    """All three stay visible after a final failure; a new deposit starts clean.

    Kills: the poll's wire record dropping the attempts.
    """
    sContainerId, sRepo = "cid-attempts", "/repo/attempts"
    archiveProgress.fnRegisterDeposit(sContainerId, None, sRepo)
    try:
        fnReport = environmentArchiveRoutes._ffnReportUploadAttemptFailed(
            sContainerId)
        for iAttempt in (1, 2, 3):
            fnReport({"iAttempt": iAttempt, "iBytesSent": iAttempt * 10,
                      "iBytesTotal": 100, "fSeconds": 60.0,
                      "sCause": "dropped", "iStatus": 0})
            archiveProgress.fnRecordProgress(
                sContainerId, archiveProgress.S_PHASE_UPLOADING, 0, 100,
                iAttempt=iAttempt + 1,
            )
        archiveProgress.fnRecordFailure(sContainerId, sRepo, "gave up")
        dictFailed = archiveProgress.fdictReadDeposit(sContainerId, sRepo)
        archiveProgress.fnRegisterDeposit(sContainerId, None, sRepo)
        dictFresh = archiveProgress.fdictReadDeposit(sContainerId, sRepo)
    finally:
        archiveProgress.fnForgetDeposit(sContainerId, sRepo)
    assert [d["iBytesSent"] for d in dictFailed["listAttempts"]] == [
        10, 20, 30]
    assert dictFresh["listAttempts"] == []
