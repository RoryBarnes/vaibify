"""The resumable deposit download, driven against a real misbehaving server.

Every case here runs the real downloader over a real loopback socket
(``depositFaultServer``): the server drops connections, stalls, ignores
``Range``, answers short parts, lies about the file's size, and serves
web pages where the file should be. What is asserted is what a
researcher would see -- the file arrives with the recorded sha256, a
resume asks for the bytes that are missing and no others, Cancel
returns within a second whatever the connection is doing, a partial
is kept when another attempt can continue from it and thrown away
when none can -- and what the server SAW: the ``Range`` of every
request, the identity encoding, and no credential.

Waits are made short by a fixture; the retry policy itself is not
changed, only its clock.
"""

import errno
import hashlib
import json
import os
import signal
import subprocess
import sys
import textwrap
import threading
import time

import pytest

from tests.depositFaultServer import FaultInjectingDepositServer
from tests.testImageAcquisition import FakeImageStore
from vaibify.config import durableFile
from vaibify.reproducibility import downloadPartial
from vaibify.reproducibility import imageAcquisition
from vaibify.reproducibility import rangedHttpTransport
from vaibify.reproducibility import resumableDownload
from vaibify.reproducibility import zenodoClient
from vaibify.reproducibility.resumableDownload import (
    DownloadCancelledError,
    DownloadRefusedError,
)


I_KIBIBYTE = 1024
BA_FILE = bytes(range(256)) * (3 * I_KIBIBYTE)   # 768 KiB, not compressible
S_SHA = "sha256:" + hashlib.sha256(BA_FILE).hexdigest()
S_DOI = "10.5281/zenodo.5150"
# Uncompressed, so the fake daemon's load reads the bytes as served.
S_TARBALL_NAME = "environment-image.tar"
S_FILE_PATH = "/records/5150/files/" + S_TARBALL_NAME


def fdictRecord(**dictOverrides):
    dictRecord = {
        "sVersionDoi": S_DOI, "sConceptDoi": "10.5281/zenodo.5149",
        "sTarballName": S_TARBALL_NAME, "sTarballSha256": S_SHA,
        "iTarballBytes": len(BA_FILE), "sZenodoService": "zenodo",
    }
    dictRecord.update(dictOverrides)
    return dictRecord


@pytest.fixture(autouse=True)
def fnMakeRetryWaitsShort(monkeypatch):
    """Milliseconds between attempts; the policy is unchanged."""
    monkeypatch.setattr(resumableDownload, "_F_BACKOFF_BASE_SECONDS", 0.01)
    monkeypatch.setattr(resumableDownload, "_F_BACKOFF_CAP_SECONDS", 0.02)


@pytest.fixture
def serverFault():
    with FaultInjectingDepositServer(BA_FILE, sFilePath=S_FILE_PATH) as serverLoopback:
        yield serverLoopback


def fdictOpenFor(dictRecord=None):
    return downloadPartial.fdictOpenAcquisition(
        dictRecord or fdictRecord(), {"sKind": "test"},
    )


def fsDownload(serverLoopback, dictAcquisition, dictControl=None, listEvents=None):
    """Run the downloader against the loopback server; return the sha256."""
    return resumableDownload.fsDownloadIntoAcquisition(
        dictAcquisition, serverLoopback.sFileUrl, [serverLoopback.sBase],
        listEvents.append if listEvents is not None else (lambda dictEvent: None),
        dictControl or resumableDownload.fdictCreateDownloadControl(),
    )


def flistRangeStarts(serverLoopback):
    """The first byte each file request asked for, in order."""
    return [
        int(dictRequest["dictHeaders"].get("range", "bytes=0-")[len("bytes="):].split("-")[0])
        for dictRequest in serverLoopback.listFileRequests
    ]


def fthreadStartDownload(serverLoopback, dictAcquisition, dictControl, listOutcome):
    """Run the download on a thread, recording its result or error."""
    def fnRun():
        try:
            listOutcome.append(fsDownload(serverLoopback, dictAcquisition, dictControl))
        except Exception as error:  # noqa: BLE001 -- the test reads it
            listOutcome.append(error)
    threadDownload = threading.Thread(target=fnRun, daemon=True)
    threadDownload.start()
    return threadDownload


def fnWaitUntil(fbCondition, fSeconds=5.0):
    fDeadline = time.monotonic() + fSeconds
    while time.monotonic() < fDeadline:
        if fbCondition():
            return
        time.sleep(0.01)
    raise AssertionError("the condition did not hold in time")


# ---------------------------------------------------------------------
# Resume.
# ---------------------------------------------------------------------


@pytest.mark.falsification
def test_a_drop_mid_body_resumes_from_the_bytes_on_disk(serverFault):
    """Kills: asking for the file from byte 0 on every request."""
    iDrop = 200 * I_KIBIBYTE
    serverFault.dictBehavior["listDropAfterBytes"] = [iDrop]
    dictAcquisition = fdictOpenFor()
    assert fsDownload(serverFault, dictAcquisition) == S_SHA
    assert flistRangeStarts(serverFault) == [0, iDrop]
    assert serverFault.iBytesServed + iDrop < 2 * len(BA_FILE)
    downloadPartial.fnDiscardAcquisition(dictAcquisition)


def test_every_request_asks_for_the_raw_bytes_and_carries_no_credential(serverFault):
    dictAcquisition = fdictOpenFor()
    fsDownload(serverFault, dictAcquisition)
    for dictRequest in serverFault.listFileRequests:
        assert dictRequest["dictHeaders"]["accept-encoding"] == "identity"
        assert "authorization" not in dictRequest["dictHeaders"]
        assert dictRequest["dictHeaders"]["range"].startswith("bytes=")
    downloadPartial.fnDiscardAcquisition(dictAcquisition)


@pytest.mark.falsification
def test_a_shorter_206_is_accepted_and_the_next_range_follows_it(serverFault):
    """Kills: refusing a 206 whose part stops short of the file's end."""
    iShort = 100 * I_KIBIBYTE
    serverFault.dictBehavior["iShortPartBytes"] = iShort
    dictAcquisition = fdictOpenFor()
    assert fsDownload(serverFault, dictAcquisition) == S_SHA
    listStarts = flistRangeStarts(serverFault)
    assert listStarts == list(range(0, len(BA_FILE), iShort))


def test_a_server_that_ignores_range_and_drops_at_one_point_spends_the_budget(serverFault):
    """Restarts and drops alternate, none passes the high-water mark, the budget ends it."""
    serverFault.dictBehavior["bIgnoreRange"] = True
    serverFault.dictBehavior["listDropAfterBytes"] = [100 * I_KIBIBYTE] * 20
    dictAcquisition = fdictOpenFor()
    with pytest.raises(DownloadRefusedError) as infoError:
        fsDownload(serverFault, dictAcquisition)
    assert infoError.value.sFailureClass == resumableDownload.S_FAILURE_BUDGET_EXHAUSTED
    assert infoError.value.bPartialKept is True
    assert len(serverFault.listFileRequests) <= 8


@pytest.mark.falsification
def test_attempts_that_bring_no_new_byte_spend_the_budget(serverFault):
    """Kills: resetting the budget on an attempt that did not pass the high-water mark."""
    serverFault.dictBehavior["listDropAfterBytes"] = [100 * I_KIBIBYTE] + [0] * 19
    dictAcquisition = fdictOpenFor()
    with pytest.raises(DownloadRefusedError) as infoError:
        fsDownload(serverFault, dictAcquisition)
    assert infoError.value.sFailureClass == resumableDownload.S_FAILURE_BUDGET_EXHAUSTED
    assert downloadPartial.fiBytesOnDisk(dictAcquisition) == 100 * I_KIBIBYTE
    assert len(serverFault.listFileRequests) == 7


def test_a_whole_file_answer_to_a_ranged_request_starts_over_and_says_so(serverFault):
    serverFault.dictBehavior["listDropAfterBytes"] = [100 * I_KIBIBYTE]
    listEvents = []
    dictAcquisition = fdictOpenFor()
    dictControl = resumableDownload.fdictCreateDownloadControl()

    def fnStatus(dictEvent):
        listEvents.append(dictEvent)
        if dictEvent.get("sPhase") == "downloadRetrying":
            serverFault.dictBehavior["bIgnoreRange"] = True

    sSha = resumableDownload.fsDownloadIntoAcquisition(
        dictAcquisition, serverFault.sFileUrl, [serverFault.sBase], fnStatus, dictControl,
    )
    assert sSha == S_SHA
    listRestarts = [d for d in listEvents if d.get("sPhase") == "downloadRestarting"]
    assert listRestarts and "would not resume" in listRestarts[0]["sSentence"]


def test_a_416_with_bytes_still_missing_restarts_once(serverFault):
    serverFault.dictBehavior["listDropAfterBytes"] = [100 * I_KIBIBYTE]
    listEvents = []
    dictAcquisition = fdictOpenFor()
    dictControl = resumableDownload.fdictCreateDownloadControl()

    def fnStatus(dictEvent):
        listEvents.append(dictEvent)
        if dictEvent.get("sPhase") == "downloadRetrying" and not serverFault.dictBehavior["listCannedAnswers"]:
            serverFault.dictBehavior["listCannedAnswers"] = [(416, {}, b"")]

    assert resumableDownload.fsDownloadIntoAcquisition(
        dictAcquisition, serverFault.sFileUrl, [serverFault.sBase], fnStatus, dictControl,
    ) == S_SHA
    assert any("416" in d.get("sSentence", "") for d in listEvents if d.get("sPhase") == "downloadRestarting")


# ---------------------------------------------------------------------
# Answers that end the download, and answers that are retried.
# ---------------------------------------------------------------------


@pytest.mark.falsification
def test_retry_after_is_honored(serverFault):
    """Kills: ignoring the Retry-After a 429 carries."""
    serverFault.dictBehavior["listCannedAnswers"] = [(429, {"Retry-After": "1"}, b"")]
    listEvents = []
    dictAcquisition = fdictOpenFor()
    fStarted = time.monotonic()
    assert fsDownload(serverFault, dictAcquisition, listEvents=listEvents) == S_SHA
    listRetries = [d for d in listEvents if d.get("sPhase") == "downloadRetrying"]
    assert listRetries[0]["fWaitSeconds"] == 1.0
    assert time.monotonic() - fStarted >= 0.9


@pytest.mark.falsification
@pytest.mark.parametrize("iStatus", [200, 403])
def test_a_web_page_where_the_file_should_be_is_named_as_such(serverFault, iStatus):
    """Kills: not recognizing a web page where the file should be."""
    serverFault.dictBehavior["listCannedAnswers"] = [
        (iStatus, {"Content-Type": "text/html; charset=utf-8"}, b"<html>log in</html>"),
    ]
    with pytest.raises(DownloadRefusedError) as infoError:
        fsDownload(serverFault, fdictOpenFor())
    assert infoError.value.sFailureClass == resumableDownload.S_FAILURE_CHALLENGE_PAGE
    assert "web page" in str(infoError.value)
    assert len(serverFault.listFileRequests) == 1


@pytest.mark.falsification
def test_a_compressed_transfer_is_refused_before_a_byte_is_kept(serverFault):
    """Kills: accepting a transfer the server compressed."""
    serverFault.dictBehavior["sContentEncoding"] = "gzip"
    dictAcquisition = fdictOpenFor()
    with pytest.raises(DownloadRefusedError) as infoError:
        fsDownload(serverFault, dictAcquisition)
    assert infoError.value.sFailureClass == resumableDownload.S_FAILURE_COMPRESSED_TRANSFER
    assert downloadPartial.fiBytesOnDisk(dictAcquisition) == 0


def test_a_changed_deposit_is_refused_not_resumed(serverFault):
    serverFault.dictBehavior["listDropAfterBytes"] = [100 * I_KIBIBYTE]
    serverFault.dictBehavior["iAdvertisedTotal"] = len(BA_FILE) + 1
    with pytest.raises(DownloadRefusedError) as infoError:
        fsDownload(serverFault, fdictOpenFor())
    assert infoError.value.sFailureClass == resumableDownload.S_FAILURE_DEPOSIT_CHANGED
    assert infoError.value.bPartialKept is False
    assert "not the one this project recorded" in str(infoError.value)


@pytest.mark.parametrize("iStatus", [401, 404, 410])
def test_a_permanent_answer_is_never_retried(serverFault, iStatus):
    serverFault.dictBehavior["listCannedAnswers"] = [(iStatus, {}, b"")] * 3
    with pytest.raises(DownloadRefusedError) as infoError:
        fsDownload(serverFault, fdictOpenFor())
    assert infoError.value.sFailureClass == resumableDownload.S_FAILURE_NOT_AVAILABLE
    assert len(serverFault.listFileRequests) == 1


def test_a_stalled_read_times_out_and_resumes(serverFault, monkeypatch):
    monkeypatch.setattr(resumableDownload, "_F_READ_TIMEOUT_SECONDS", 0.3)
    serverFault.dictBehavior["tStallAfterBytes"] = (100 * I_KIBIBYTE, 30.0)
    listEvents = []
    dictAcquisition = fdictOpenFor()
    dictControl = resumableDownload.fdictCreateDownloadControl()

    def fnStatus(dictEvent):
        listEvents.append(dictEvent)
        if dictEvent.get("sPhase") == "downloadRetrying":
            serverFault.dictBehavior["tStallAfterBytes"] = None
            serverFault.eventRelease.set()

    assert resumableDownload.fsDownloadIntoAcquisition(
        dictAcquisition, serverFault.sFileUrl, [serverFault.sBase], fnStatus, dictControl,
    ) == S_SHA
    assert any("stopped sending" in d.get("sSentence", "") for d in listEvents)
    assert flistRangeStarts(serverFault)[1] == 100 * I_KIBIBYTE, (
        "every byte that arrived before the stall must be kept and resumed after"
    )


# ---------------------------------------------------------------------
# Cancel, in every state, within a second.
# ---------------------------------------------------------------------


@pytest.mark.falsification
def test_cancel_wakes_a_read_blocked_on_a_stalled_socket(serverFault):
    """Kills: Cancel setting the flag without shutting the socket down."""
    serverFault.dictBehavior["tStallAfterBytes"] = (100 * I_KIBIBYTE, 30.0)
    dictAcquisition = fdictOpenFor()
    dictControl = resumableDownload.fdictCreateDownloadControl()
    listOutcome = []
    threadDownload = fthreadStartDownload(serverFault, dictAcquisition, dictControl, listOutcome)
    fnWaitUntil(lambda: resumableDownload.fdictReadDownloadProgress(dictControl)["iBytes"] >= 100 * I_KIBIBYTE)
    time.sleep(0.2)
    dictProgress = resumableDownload.fdictReadDownloadProgress(dictControl)
    assert dictProgress["fSecondsSinceLastByte"] >= 0.15
    fStarted = time.monotonic()
    resumableDownload.fnRequestCancel(dictControl)
    threadDownload.join(timeout=2.0)
    assert not threadDownload.is_alive()
    assert time.monotonic() - fStarted < 1.0
    assert isinstance(listOutcome[0], DownloadCancelledError)


@pytest.mark.falsification
def test_cancel_cuts_a_retry_wait_short(serverFault, monkeypatch):
    """Kills: sleeping out the backoff instead of waiting on the cancel flag."""
    monkeypatch.setattr(resumableDownload, "_F_BACKOFF_BASE_SECONDS", 30.0)
    monkeypatch.setattr(resumableDownload, "_F_BACKOFF_CAP_SECONDS", 30.0)
    serverFault.dictBehavior["listCannedAnswers"] = [(503, {}, b"")] * 3
    dictControl = resumableDownload.fdictCreateDownloadControl()
    listOutcome = []
    threadDownload = fthreadStartDownload(serverFault, fdictOpenFor(), dictControl, listOutcome)
    fnWaitUntil(lambda: resumableDownload.fdictReadDownloadProgress(dictControl)["fSecondsUntilRetry"] > 0)
    fStarted = time.monotonic()
    resumableDownload.fnRequestCancel(dictControl)
    threadDownload.join(timeout=2.0)
    assert not threadDownload.is_alive()
    assert time.monotonic() - fStarted < 1.0
    assert isinstance(listOutcome[0], DownloadCancelledError)


@pytest.fixture
def eventHangingConnect(monkeypatch):
    """Make every connection attempt hang until the event is set, then connect."""
    eventHang = threading.Event()
    fsocketReal = rangedHttpTransport._fsocketOpenToUrl

    def fsocketHangThenConnect(sUrl):
        eventHang.wait(10.0)
        return fsocketReal(sUrl)

    monkeypatch.setattr(rangedHttpTransport, "_fsocketOpenToUrl", fsocketHangThenConnect)
    yield eventHang
    eventHang.set()
    fnWaitUntil(lambda: rangedHttpTransport.fiCountLiveConnectWorkers() == 0, 15.0)


@pytest.mark.falsification
def test_cancel_during_a_hanging_connection_returns_at_once(serverFault, eventHangingConnect):
    """Kills: waiting for the connect worker instead of abandoning it."""
    dictControl = resumableDownload.fdictCreateDownloadControl()
    listOutcome = []
    threadDownload = fthreadStartDownload(serverFault, fdictOpenFor(), dictControl, listOutcome)
    fnWaitUntil(lambda: rangedHttpTransport.fiCountLiveConnectWorkers() == 1)
    fStarted = time.monotonic()
    resumableDownload.fnRequestCancel(dictControl)
    threadDownload.join(timeout=2.0)
    assert not threadDownload.is_alive()
    assert time.monotonic() - fStarted < 1.0
    assert isinstance(listOutcome[0], DownloadCancelledError)
    assert rangedHttpTransport.fiCountLiveConnectWorkers() == 1


@pytest.mark.falsification
def test_a_hanging_connection_meets_its_deadline(serverFault, eventHangingConnect, monkeypatch):
    """The coordinator stops waiting at the deadline while the lookup hangs on.

    Kills: no deadline on the connect phase (the first attempt never ends).
    """
    monkeypatch.setattr(rangedHttpTransport, "_F_CONNECT_TIMEOUT_SECONDS", 0.2)
    dictControl = resumableDownload.fdictCreateDownloadControl()
    listOutcome = []
    threadDownload = fthreadStartDownload(serverFault, fdictOpenFor(), dictControl, listOutcome)
    fStarted = time.monotonic()
    try:
        fnWaitUntil(
            lambda: resumableDownload.fdictReadDownloadProgress(dictControl)["iAttempt"] >= 2,
            4.0,
        )
    finally:
        resumableDownload.fnRequestCancel(dictControl)
        threadDownload.join(timeout=3.0)
    assert time.monotonic() - fStarted < 3.0
    dictProgress = resumableDownload.fdictReadDownloadProgress(dictControl)
    assert "could not connect" in dictProgress["sLastFailure"]
    assert not threadDownload.is_alive()


@pytest.mark.falsification
def test_connect_workers_are_bounded_however_often_the_user_retries(
    serverFault, eventHangingConnect,
):
    """Kills: starting a worker without waiting for a free slot."""
    iMostSeen = 0
    dictAcquisition = fdictOpenFor()
    for _iCycle in range(50):
        dictControl = resumableDownload.fdictCreateDownloadControl()
        listOutcome = []
        threadDownload = fthreadStartDownload(serverFault, dictAcquisition, dictControl, listOutcome)
        time.sleep(0.02)
        iMostSeen = max(iMostSeen, rangedHttpTransport.fiCountLiveConnectWorkers())
        resumableDownload.fnRequestCancel(dictControl)
        threadDownload.join(timeout=2.0)
        assert not threadDownload.is_alive()
        iMostSeen = max(iMostSeen, rangedHttpTransport.fiCountLiveConnectWorkers())
    assert iMostSeen <= 4
    assert "still hanging" in resumableDownload.fdictReadDownloadProgress(dictControl)["sAttemptState"]
    assert not os.path.exists(dictAcquisition["sPartPath"])
    assert dictAcquisition["dictSidecar"]["iDurableBytes"] == 0


# ---------------------------------------------------------------------
# The disk.
# ---------------------------------------------------------------------


@pytest.mark.falsification
def test_a_disk_without_room_for_the_rest_refuses_and_keeps_the_partial(serverFault, monkeypatch):
    """Kills: skipping the free-space check before the first byte."""
    dictAcquisition = fdictOpenFor()
    monkeypatch.setattr(
        resumableDownload.shutil, "disk_usage",
        lambda sPath: type("Usage", (), {"free": 0})(),
    )
    with pytest.raises(DownloadRefusedError) as infoError:
        fsDownload(serverFault, dictAcquisition)
    assert infoError.value.sFailureClass == resumableDownload.S_FAILURE_NO_ROOM
    assert infoError.value.bPartialKept is True
    assert serverFault.listFileRequests == []


@pytest.mark.falsification
def test_a_short_file_is_incomplete_never_corrupt(serverFault):
    """Kills: skipping the exact-size check when the run believes it finished."""
    dictAcquisition = fdictOpenFor()
    downloadPartial.fnTruncatePart(dictAcquisition, 0)
    with open(dictAcquisition["sPartPath"], "ab") as filePart:
        filePart.write(BA_FILE[: 100 * I_KIBIBYTE])
    dictControl = resumableDownload.fdictCreateDownloadControl()
    dictRun = {"iOnDisk": len(BA_FILE), "hasher": hashlib.sha256()}
    with pytest.raises(DownloadRefusedError) as infoError:
        resumableDownload._fnRefuseUnlessExactlyTheRecordedSize(
            dictAcquisition, dictControl, dictRun,
        )
    assert infoError.value.sFailureClass == resumableDownload.S_FAILURE_INCOMPLETE
    assert infoError.value.bPartialKept is True
    assert "incomplete" in str(infoError.value)


def test_a_disk_that_fills_mid_download_refuses_and_keeps_what_landed(serverFault, monkeypatch):
    fopenReal = open
    dictCounter = {"iWritten": 0}

    class _FileThatFills:
        def __init__(self, fileReal):
            self._fileReal = fileReal

        def write(self, baBlock):
            if dictCounter["iWritten"] + len(baBlock) > 100 * I_KIBIBYTE:
                raise OSError(errno.ENOSPC, "No space left on device")
            dictCounter["iWritten"] += len(baBlock)
            return self._fileReal.write(baBlock)

        def __getattr__(self, sName):
            return getattr(self._fileReal, sName)

        def __enter__(self):
            return self

        def __exit__(self, *aExceptionInfo):
            self._fileReal.close()

    def fopenFilling(sPath, sMode="r", *aArguments, **dictKeywords):
        fileReal = fopenReal(sPath, sMode, *aArguments, **dictKeywords)
        return _FileThatFills(fileReal) if sMode == "ab" else fileReal

    monkeypatch.setattr(resumableDownload, "open", fopenFilling, raising=False)
    dictAcquisition = fdictOpenFor()
    with pytest.raises(DownloadRefusedError) as infoError:
        fsDownload(serverFault, dictAcquisition)
    assert infoError.value.sFailureClass == resumableDownload.S_FAILURE_DISK_FULL
    assert infoError.value.bPartialKept is True
    assert "Free some space" in str(infoError.value)
    assert 0 < downloadPartial.fiBytesOnDisk(dictAcquisition) <= 100 * I_KIBIBYTE


# ---------------------------------------------------------------------
# Checkpoints, torn tails, interruption and adoption.
# ---------------------------------------------------------------------


@pytest.mark.falsification
def test_the_sidecar_is_written_durably(serverFault, monkeypatch):
    """Kills: writing the sidecar with a plain open instead of the durable writer."""
    listDurableWrites = []
    fnReal = downloadPartial.fnWriteFileDurably

    def fnRecord(sPath, baContent, iMode=0o600):
        listDurableWrites.append(sPath)
        fnReal(sPath, baContent, iMode)

    monkeypatch.setattr(downloadPartial, "fnWriteFileDurably", fnRecord)
    monkeypatch.setattr(resumableDownload, "_I_CHECKPOINT_BYTES", 64 * I_KIBIBYTE)
    dictAcquisition = fdictOpenFor()
    fsDownload(serverFault, dictAcquisition)
    # One checkpoint per 64 KiB, less one: the socket hands the body over
    # in blocks that need not align with the checkpoint size (Linux reads
    # them smaller than macOS does), so the last crossing can coincide
    # with the completion write and be folded into it.
    iCheckpointsExpected = len(BA_FILE) // (64 * I_KIBIBYTE) - 1
    assert listDurableWrites.count(dictAcquisition["sSidecarPath"]) >= iCheckpointsExpected


def test_the_durable_writer_flushes_the_drive_where_it_can(monkeypatch, tmp_path):
    listCalls = []

    class _FcntlWithFullFsync:
        F_FULLFSYNC = 51

        @staticmethod
        def fcntl(iDescriptor, iCommand):
            listCalls.append(("fullfsync", iCommand))

    monkeypatch.setattr(durableFile, "fcntl", _FcntlWithFullFsync)
    monkeypatch.setattr(durableFile.os, "fsync", lambda iDescriptor: listCalls.append(("fsync",)))
    durableFile.fnWriteFileDurably(str(tmp_path / "record.json"), b"{}")
    assert ("fullfsync", 51) in listCalls
    assert (tmp_path / "record.json").read_bytes() == b"{}"
    assert list(tmp_path.glob("*.tmp")) == []


@pytest.mark.falsification
def test_a_torn_tail_is_cut_back_to_the_checkpoint_on_adoption(serverFault):
    """Kills: adopting a partial without truncating it to the durable count."""
    iDurable = 200 * I_KIBIBYTE
    dictFirst = fdictOpenFor()
    with open(dictFirst["sPartPath"], "ab") as filePart:
        filePart.write(BA_FILE[:iDurable])
        downloadPartial.fnCheckpoint(dictFirst, filePart, iDurable)
        filePart.write(b"\x00" * (64 * I_KIBIBYTE))
    downloadPartial.fnReleaseAcquisition(dictFirst)
    dictAdopted = fdictOpenFor()
    assert dictAdopted["bAdopted"] is True
    assert dictAdopted["sAcquisitionId"] == dictFirst["sAcquisitionId"]
    assert downloadPartial.fiBytesOnDisk(dictAdopted) == iDurable
    assert fsDownload(serverFault, dictAdopted) == S_SHA
    assert flistRangeStarts(serverFault) == [iDurable]


def test_a_live_download_is_never_adopted_or_deleted(serverFault):
    dictLive = fdictOpenFor()
    assert downloadPartial.flistDescribePartials()[0]["sState"] == "live"
    with pytest.raises(downloadPartial.PartialHeldElsewhereError):
        downloadPartial.fnDeletePartial(dictLive["sAcquisitionId"])
    dictSecond = fdictOpenFor()
    assert dictSecond["bAdopted"] is False
    assert dictSecond["sAcquisitionId"] != dictLive["sAcquisitionId"]
    downloadPartial.fnDiscardAcquisition(dictSecond)
    downloadPartial.fnReleaseAcquisition(dictLive)
    assert downloadPartial.flistDescribePartials()[0]["sState"] == "interrupted"
    downloadPartial.fnDeletePartial(dictLive["sAcquisitionId"])
    assert downloadPartial.flistDescribePartials() == []


def test_a_partial_without_a_sidecar_is_unusable_and_a_tmp_directory_is_legacy():
    sRoot = downloadPartial.fsResolveDownloadRoot()
    os.makedirs(os.path.join(sRoot, "acquisition-broken"))
    open(os.path.join(sRoot, "acquisition-broken", "lock"), "w").close()
    os.makedirs(os.path.join(sRoot, "tmpabc123"))
    dictStates = {
        dictPartial["sAcquisitionId"]: dictPartial["sState"]
        for dictPartial in downloadPartial.flistDescribePartials()
    }
    assert dictStates == {"acquisition-broken": "unusable", "tmpabc123": "legacy"}
    assert fdictOpenFor()["bAdopted"] is False


S_CHILD_PROGRAM = textwrap.dedent("""
    import json, sys
    sys.path.insert(0, sys.argv[1])
    from vaibify.reproducibility import downloadPartial, resumableDownload
    downloadPartial._S_DOWNLOAD_ROOT = sys.argv[2]
    resumableDownload._I_CHECKPOINT_BYTES = 64 * 1024
    dictRecord = json.loads(sys.argv[4])
    dictAcquisition = downloadPartial.fdictOpenAcquisition(dictRecord, {"sKind": "child"})
    print(dictAcquisition["sAcquisitionId"], flush=True)
    resumableDownload.fsDownloadIntoAcquisition(
        dictAcquisition, sys.argv[3], [sys.argv[3].split("/records/")[0]],
        lambda dictEvent: None, resumableDownload.fdictCreateDownloadControl(),
    )
""")


@pytest.mark.falsification
def test_a_download_killed_mid_stream_is_interrupted_not_lost_and_is_adopted(serverFault):
    """A real process, really killed; its lock frees and its bytes are resumed.

    Kills: never adopting an interrupted partial (every download starts at 0).
    """
    serverFault.dictBehavior["tStallAfterBytes"] = (200 * I_KIBIBYTE, 60.0)
    sRoot = downloadPartial.fsResolveDownloadRoot()
    sRepository = os.path.dirname(os.path.dirname(os.path.abspath(resumableDownload.__file__)))
    processChild = subprocess.Popen(
        [sys.executable, "-c", S_CHILD_PROGRAM, os.path.dirname(sRepository), sRoot,
         serverFault.sFileUrl, json.dumps(fdictRecord())],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    try:
        sAcquisitionId = processChild.stdout.readline().strip()
        assert sAcquisitionId.startswith("acquisition-")
        sSidecarPath = os.path.join(sRoot, sAcquisitionId, downloadPartial.S_SIDECAR_NAME)
        fnWaitUntil(lambda: (downloadPartial.fdictReadSidecar(sSidecarPath) or {}).get("iDurableBytes", 0) >= 128 * I_KIBIBYTE, 15.0)
        assert downloadPartial.flistDescribePartials()[0]["sState"] == "live"
        with pytest.raises(downloadPartial.PartialHeldElsewhereError):
            downloadPartial.fnDeletePartial(sAcquisitionId)
    finally:
        processChild.send_signal(signal.SIGKILL)
        processChild.wait(timeout=10)
    iDurable = downloadPartial.fdictReadSidecar(sSidecarPath)["iDurableBytes"]
    assert downloadPartial.flistDescribePartials()[0]["sState"] == "interrupted"
    serverFault.dictBehavior["tStallAfterBytes"] = None
    dictAdopted = fdictOpenFor()
    assert dictAdopted["bAdopted"] is True and dictAdopted["sAcquisitionId"] == sAcquisitionId
    assert downloadPartial.fiBytesOnDisk(dictAdopted) == iDurable
    assert fsDownload(serverFault, dictAdopted) == S_SHA
    assert flistRangeStarts(serverFault)[-1] == iDurable


# ---------------------------------------------------------------------
# Progress read while nothing moves; the slow-rate notice.
# ---------------------------------------------------------------------


def test_the_slow_rate_notice_is_given_once_without_stopping(serverFault, monkeypatch):
    monkeypatch.setattr(resumableDownload, "_F_SLOW_RATE_BYTES_PER_SECOND", 1e15)
    monkeypatch.setattr(resumableDownload, "_F_SLOW_RATE_NOTICE_SECONDS", 0.2)
    monkeypatch.setattr(resumableDownload, "_I_STATUS_EVERY_BYTES", 64 * I_KIBIBYTE)
    serverFault.dictBehavior["tStallAfterBytes"] = (300 * I_KIBIBYTE, 30.0)
    threading.Timer(0.4, serverFault.eventRelease.set).start()
    listEvents = []
    assert fsDownload(serverFault, fdictOpenFor(), listEvents=listEvents) == S_SHA
    listSlow = [d for d in listEvents if d.get("sPhase") == "downloadSlow"]
    assert len(listSlow) == 1
    assert "continues" in listSlow[0]["sSentence"]


# ---------------------------------------------------------------------
# Through the acquisition chain: what is kept, what is deleted.
# ---------------------------------------------------------------------


def fdictEnvelopeFor(serverLoopback, dictRecord=None):
    return {"dictContainer": {
        "sImageDigest": "registry.example/project@sha256:" + "a" * 64,
        "sArchitecture": "amd64",
        "dictImageArchive": dictRecord or fdictRecord(),
    }}


@pytest.fixture
def fnPointZenodoAtTheFaultServer(monkeypatch, serverFault):
    monkeypatch.setattr(zenodoClient, "_SERVICES", {
        "zenodo": serverFault.sBase, "sandbox": serverFault.sBase,
    })


@pytest.mark.falsification
def test_no_stored_credential_reaches_the_record_or_the_file(
    serverFault, fnPointZenodoAtTheFaultServer, monkeypatch, tmp_path,
):
    """Kills: letting requests consult netrc for the record fetch."""
    pathNetrc = tmp_path / "netrc"
    pathNetrc.write_text("machine 127.0.0.1 login alice password hunter2\n")
    monkeypatch.setenv("NETRC", str(pathNetrc))
    import requests.utils
    assert requests.utils.get_netrc_auth(serverFault.sBase + "/api/records/5150") is not None
    dictAcquired = imageAcquisition.fdictAcquirePinnedImage(
        fdictEnvelopeFor(serverFault), "linux/amd64",
        dockerDisposable=FakeImageStore(bRegistryServes=False),
    )
    assert dictAcquired["sObtainedFrom"] == imageAcquisition.S_OBTAINED_ARCHIVE
    listRequests = serverFault.listRequests
    assert any(d["sPath"] == "/api/records/5150" for d in listRequests)
    assert all("authorization" not in d["dictHeaders"] for d in listRequests)


@pytest.mark.falsification
def test_a_daemon_that_cannot_load_keeps_the_verified_file_for_the_retry(
    serverFault, fnPointZenodoAtTheFaultServer,
):
    """Kills: discarding a verified tarball when the daemon refused to load it."""
    storeBroken = FakeImageStore(bRegistryServes=False, bLoadable=False)
    with pytest.raises(imageAcquisition.ImageAcquisitionRefusedError):
        imageAcquisition.fdictAcquirePinnedImage(
            fdictEnvelopeFor(serverFault), "linux/amd64", dockerDisposable=storeBroken,
        )
    listPartials = downloadPartial.flistDescribePartials()
    assert [d["sState"] for d in listPartials] == ["interrupted"]
    assert listPartials[0]["dictSidecar"]["bComplete"] is True
    iRequestsBefore = len(serverFault.listFileRequests)
    dictAcquired = imageAcquisition.fdictAcquirePinnedImage(
        fdictEnvelopeFor(serverFault), "linux/amd64",
        dockerDisposable=FakeImageStore(bRegistryServes=False),
    )
    assert dictAcquired["sObtainedFrom"] == imageAcquisition.S_OBTAINED_ARCHIVE
    assert len(serverFault.listFileRequests) == iRequestsBefore
    assert downloadPartial.flistDescribePartials() == []


@pytest.mark.falsification
def test_a_spent_budget_keeps_the_partial_and_the_next_acquisition_adopts_it(
    serverFault, fnPointZenodoAtTheFaultServer,
):
    """Kills: deleting the partial when the download gave up for the researcher to decide."""
    # One attempt that advances, then six that do not: exactly the budget.
    serverFault.dictBehavior["listDropAfterBytes"] = [100 * I_KIBIBYTE] + [0] * 6
    with pytest.raises(imageAcquisition.ImageAcquisitionRefusedError) as infoError:
        imageAcquisition.fdictAcquirePinnedImage(
            fdictEnvelopeFor(serverFault), "linux/amd64",
            dockerDisposable=FakeImageStore(bRegistryServes=False),
        )
    assert infoError.value.dictFailure["sFailureClass"] == resumableDownload.S_FAILURE_BUDGET_EXHAUSTED
    assert infoError.value.dictFailure["bPartialKept"] is True
    assert infoError.value.dictFailure["iBytesOnDisk"] == 100 * I_KIBIBYTE
    assert [d["sState"] for d in downloadPartial.flistDescribePartials()] == ["interrupted"]
    listEvents = []
    dictAcquired = imageAcquisition.fdictAcquirePinnedImage(
        fdictEnvelopeFor(serverFault), "linux/amd64", fnStatusCallback=listEvents.append,
        dockerDisposable=FakeImageStore(bRegistryServes=False),
    )
    assert dictAcquired["sObtainedFrom"] == imageAcquisition.S_OBTAINED_ARCHIVE
    assert any(d.get("sPhase") == "resuming" and d["iBytes"] == 100 * I_KIBIBYTE for d in listEvents)
    assert downloadPartial.flistDescribePartials() == []


@pytest.mark.falsification
def test_cancelling_the_chain_stops_it_and_leaves_nothing_on_disk(
    serverFault, fnPointZenodoAtTheFaultServer,
):
    """Kills: keeping the partial after the researcher gave up."""
    serverFault.dictBehavior["tStallAfterBytes"] = (100 * I_KIBIBYTE, 30.0)
    dictControl = resumableDownload.fdictCreateDownloadControl({"sKind": "test"})
    storeLocal = FakeImageStore(bRegistryServes=False, bLocalCopy=True)
    listOutcome = []

    def fnRun():
        try:
            listOutcome.append(imageAcquisition.fdictAcquirePinnedImage(
                fdictEnvelopeFor(serverFault), "linux/amd64",
                dockerDisposable=storeLocal, dictDownloadControl=dictControl,
            ))
        except Exception as error:  # noqa: BLE001 -- the test reads it
            listOutcome.append(error)

    threadChain = threading.Thread(target=fnRun, daemon=True)
    threadChain.start()
    fnWaitUntil(lambda: resumableDownload.fdictReadDownloadProgress(dictControl)["iBytes"] >= 100 * I_KIBIBYTE)
    resumableDownload.fnRequestCancel(dictControl)
    threadChain.join(timeout=3.0)
    assert not threadChain.is_alive()
    assert isinstance(listOutcome[0], imageAcquisition.ImageAcquisitionRefusedError)
    assert listOutcome[0].dictFailure["sFailureClass"] == resumableDownload.S_FAILURE_CANCELLED
    assert downloadPartial.flistDescribePartials() == []
    assert not storeLocal.listLoaded


# ---------------------------------------------------------------------
# Findings of the 2026-10-14 adversarial review of the transport.
# ---------------------------------------------------------------------


@pytest.mark.falsification
def test_a_drop_before_the_headers_keeps_the_partial_and_retries(serverFault):
    """A resumed request the server drops before answering is a transient
    failure like a drop mid-body, never an escape that deletes the bytes.

    Kills: re-raising header-stage socket errors raw, so the retry loop
    never sees them and the acquisition is thrown away.
    """
    serverFault.dictBehavior["listDropAfterBytes"] = [64 * I_KIBIBYTE]
    serverFault.dictBehavior["listDropBeforeHeaders"] = [False, True]
    dictAcquisition = fdictOpenFor()
    assert fsDownload(serverFault, dictAcquisition) == S_SHA
    assert flistRangeStarts(serverFault) == [0, 64 * I_KIBIBYTE, 64 * I_KIBIBYTE]
    assert dictAcquisition["dictSidecar"]["iHighWaterBytes"] == len(BA_FILE)


class _LockThatPausesCallersOnTheirFirstRelease:
    """The pool lock, with a barrier on each caller's FIRST release.

    Under the fix a caller's first release follows its reservation, so
    the barrier changes nothing; under a check-then-claim pool the first
    release follows the CHECK, and every caller paused there proceeds to
    claim a slot the others have also been promised.
    """

    def __init__(self, setCallerIdents, barrierCallers):
        self._lockReal = threading.Lock()
        self._setCallerIdents = setCallerIdents
        self._barrierCallers = barrierCallers
        self._setReleasedOnce = set()
        self._lockBookkeeping = threading.Lock()

    def __enter__(self):
        self._lockReal.acquire()
        return self

    def __exit__(self, *aExceptionParts):
        self._lockReal.release()
        iIdent = threading.get_ident()
        with self._lockBookkeeping:
            bFirst = iIdent in self._setCallerIdents and iIdent not in self._setReleasedOnce
            self._setReleasedOnce.add(iIdent)
        if bFirst:
            self._barrierCallers.wait(timeout=5.0)
        return False


@pytest.mark.falsification
def test_eight_simultaneous_callers_never_hold_more_than_four_workers(
    serverFault, eventHangingConnect, monkeypatch,
):
    """Kills: checking the pool in one critical section and claiming the
    slot in a later one, through which every concurrent caller passes.
    """
    I_CALLERS = 8
    setCallerIdents = set()
    barrierCallers = threading.Barrier(I_CALLERS)
    monkeypatch.setattr(
        rangedHttpTransport, "_lockPool",
        _LockThatPausesCallersOnTheirFirstRelease(setCallerIdents, barrierCallers),
    )
    listControls = [resumableDownload.fdictCreateDownloadControl() for _ in range(I_CALLERS)]
    listErrors = []

    def fnConnect(dictControl):
        setCallerIdents.add(threading.get_ident())
        try:
            rangedHttpTransport._fsocketConnect(serverFault.sFileUrl, dictControl, 1.0).close()
        except Exception as error:  # noqa: BLE001 -- the test reads it
            listErrors.append(error)

    listThreads = [
        threading.Thread(target=fnConnect, args=(dictControl,), daemon=True)
        for dictControl in listControls
    ]
    for threadCaller in listThreads:
        threadCaller.start()
    fnWaitUntil(lambda: sum(
        "still hanging" in dictControl["dictLive"].get("sAttemptState", "")
        for dictControl in listControls
    ) == I_CALLERS - 4, 5.0)
    iMostSeen = rangedHttpTransport.fiCountLiveConnectWorkers()
    for dictControl in listControls:
        resumableDownload.fnRequestCancel(dictControl)
    for threadCaller in listThreads:
        threadCaller.join(timeout=5.0)
    assert iMostSeen == 4
    assert all(isinstance(error, rangedHttpTransport.CancelledDuringTransport) for error in listErrors)


def _ftListenOnLoopbackForOneConnection():
    """A plain listener that records the first bytes one client sends."""
    import socket as moduleSocket
    socketListener = moduleSocket.socket()
    socketListener.bind(("127.0.0.1", 0))
    socketListener.listen(1)
    listReceived = []

    def fnAccept():
        try:
            socketClient, _tPeer = socketListener.accept()
        except OSError:
            return
        socketClient.settimeout(2.0)
        try:
            listReceived.append(socketClient.recv(4096))
        except OSError:
            listReceived.append(b"")
        socketClient.close()

    threadListener = threading.Thread(target=fnAccept, daemon=True)
    threadListener.start()
    return socketListener, listReceived, threadListener


@pytest.mark.falsification
def test_an_https_proxy_hears_tls_before_any_credential(monkeypatch):
    """Kills: a plain socket to an https:// proxy, which carried the
    CONNECT and its Proxy-Authorization in clear before any handshake.
    """
    socketListener, listReceived, threadListener = _ftListenOnLoopbackForOneConnection()
    iPort = socketListener.getsockname()[1]
    monkeypatch.setattr(
        rangedHttpTransport, "getproxies",
        lambda: {"http": f"https://reviewer:not-a-real-secret@127.0.0.1:{iPort}"},
    )
    monkeypatch.setattr(rangedHttpTransport, "proxy_bypass", lambda sHost: False)
    try:
        with pytest.raises(Exception):
            rangedHttpTransport._fsocketOpenToUrl("http://127.0.0.1:1/file")
        threadListener.join(timeout=5.0)
    finally:
        socketListener.close()
    assert listReceived, "the proxy was never contacted"
    baFirst = listReceived[0]
    assert baFirst[:1] == b"\x16", "the first byte is not a TLS handshake record"
    assert b"CONNECT" not in baFirst
    assert b"Proxy-Authorization" not in baFirst
    assert b"not-a-real-secret" not in baFirst


def test_a_proxy_scheme_the_tunnel_cannot_honour_is_refused_before_connecting(monkeypatch):
    socketListener, listReceived, threadListener = _ftListenOnLoopbackForOneConnection()
    iPort = socketListener.getsockname()[1]
    monkeypatch.setattr(
        rangedHttpTransport, "getproxies",
        lambda: {"http": f"socks5://reviewer:not-a-real-secret@127.0.0.1:{iPort}"},
    )
    monkeypatch.setattr(rangedHttpTransport, "proxy_bypass", lambda sHost: False)
    try:
        with pytest.raises(rangedHttpTransport.TransportFailure) as errorRaised:
            rangedHttpTransport._fsocketOpenToUrl("http://127.0.0.1:1/file")
    finally:
        socketListener.close()
        threadListener.join(timeout=2.0)
    assert not errorRaised.value.bTransient
    assert "socks5" in str(errorRaised.value)
    assert "not-a-real-secret" not in str(errorRaised.value)
    assert listReceived == [], "the proxy was contacted despite the refusal"


@pytest.mark.falsification
@pytest.mark.parametrize("sName", [
    downloadPartial.S_LOCK_NAME, downloadPartial.S_SIDECAR_NAME,
    "archive" + downloadPartial.S_PART_SUFFIX, ".hidden", "nested/file.tar",
])
def test_a_record_naming_an_acquisition_file_is_refused_before_any_directory_exists(sName):
    """A deposit record that names its archive after the lock or the
    sidecar would replace that file under the owner once verified.

    Kills: storing the download under the record's name unchecked.
    """
    sRoot = downloadPartial.fsResolveDownloadRoot()
    listBefore = sorted(os.listdir(sRoot))
    with pytest.raises(ValueError) as errorRaised:
        fdictOpenFor(fdictRecord(sTarballName=sName))
    assert "cannot be stored" in str(errorRaised.value) or "not a name" in str(errorRaised.value)
    assert sorted(os.listdir(sRoot)) == listBefore


def test_an_absent_or_ordinary_archive_name_is_stored_as_before():
    assert downloadPartial.fsValidatedTarballName("") == "image.tar"
    assert downloadPartial.fsValidatedTarballName(None) == "image.tar"
    assert downloadPartial.fsValidatedTarballName("environment-image.tar.zst") == (
        "environment-image.tar.zst"
    )
    dictAcquisition = fdictOpenFor()
    assert os.path.basename(downloadPartial.fsCompletePath(dictAcquisition)) == S_TARBALL_NAME
    downloadPartial.fnDiscardAcquisition(dictAcquisition)
