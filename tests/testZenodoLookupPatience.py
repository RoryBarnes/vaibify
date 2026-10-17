"""Finding a deposit on Zenodo is patient, cancellable and honest.

Zenodo is often slow to accept a connection. A single short try once
refused a researcher's archived environment before the download had
begun -- while the containerizing window said the download had
finished. These tests drive the chain's lookup against a REAL loopback
server that drops, stalls and fails its answers, and assert what the
researcher is promised:

* a lookup that gets no answer is tried again, and the download then
  proceeds;
* Cancel ends a try blocked on a silent server at once, not when the
  try's own timeouts run out;
* when the budget is spent, bytes an earlier attempt downloaded are
  KEPT and the failure says Zenodo could not be reached, naming the DOI;
* the stage and its timers reach the poll as each begins;
* every connection to Zenodo is given the same, long, patience.
"""

import hashlib
import threading
import time
import types

import pytest

from tests.depositFaultServer import FaultInjectingDepositServer
from tests.testImageAcquisition import (
    S_ARCHITECTURE,
    S_LOADED_IMAGE_ID,
    S_PINNED_REFERENCE,
    S_REQUIRED_PLATFORM,
    FakeImageStore,
)
from vaibify.reproducibility import downloadPartial
from vaibify.reproducibility import imageAcquisition
from vaibify.reproducibility import rangedHttpTransport
from vaibify.reproducibility import resumableDownload
from vaibify.reproducibility import zenodoClient
from vaibify.reproducibility.imageAcquisition import (
    ImageAcquisitionRefusedError,
    S_OBTAINED_ARCHIVE,
)


BA_FILE = bytes(range(256)) * 512
S_SHA = "sha256:" + hashlib.sha256(BA_FILE).hexdigest()
S_DOI = "10.5281/zenodo.5150"
# Uncompressed, so the fake daemon's load reads the bytes as served.
S_TARBALL_NAME = "environment-image.tar"
S_FILE_PATH = "/records/5150/files/" + S_TARBALL_NAME
S_RECORD_PREFIX = "/api/records/"
S_DOI_PREFIX = "/doi/"


def fdictRecord():
    return {
        "sVersionDoi": S_DOI, "sConceptDoi": "10.5281/zenodo.5149",
        "sTarballName": S_TARBALL_NAME, "sTarballSha256": S_SHA,
        "iTarballBytes": len(BA_FILE), "sZenodoService": "zenodo",
    }


def fdictEnvelope():
    return {"dictContainer": {
        "sImageDigest": S_PINNED_REFERENCE, "sArchitecture": S_ARCHITECTURE,
        "dictImageArchive": dict(fdictRecord(), sProvenance="original",
                                 sImageDigest=S_PINNED_REFERENCE,
                                 sArchitecture=S_ARCHITECTURE),
    }}


@pytest.fixture
def serverZenodo(monkeypatch):
    """A loopback Zenodo the chain reaches only through the service table."""
    with FaultInjectingDepositServer(BA_FILE, sFilePath=S_FILE_PATH) as serverLoopback:
        monkeypatch.setattr(zenodoClient, "_SERVICES", {
            "zenodo": serverLoopback.sBase, "sandbox": serverLoopback.sBase,
        })
        monkeypatch.setattr(
            imageAcquisition, "_S_DOI_RESOLVER", serverLoopback.sBase + S_DOI_PREFIX,
        )
        yield serverLoopback


@pytest.fixture
def fnShortenTheLookup(monkeypatch):
    """Make the lookup's budget and waits fit a test; the policy is unchanged."""
    def fnShorten(fBudget=5.0, fWait=0.01):
        monkeypatch.setattr(imageAcquisition, "_F_LOCATE_BUDGET_SECONDS", fBudget)
        monkeypatch.setattr(imageAcquisition, "_F_LOCATE_BACKOFF_BASE_SECONDS", fWait)
        monkeypatch.setattr(imageAcquisition, "_F_LOCATE_BACKOFF_CAP_SECONDS", fWait)
        # The full-jitter draw is pinned to its ceiling so a wait is as
        # long as the test says, never a lucky zero.
        monkeypatch.setattr(imageAcquisition, "random", types.SimpleNamespace(
            uniform=lambda fLow, fHigh: fHigh,
        ))
    return fnShorten


def flistPaths(serverLoopback, sPrefix):
    return [
        dictRequest["sPath"] for dictRequest in serverLoopback.listRequests
        if dictRequest["sPath"].startswith(sPrefix)
    ]


def fnWaitUntil(fbCondition, fSeconds=5.0):
    fDeadline = time.monotonic() + fSeconds
    while time.monotonic() < fDeadline:
        if fbCondition():
            return
        time.sleep(0.01)
    raise AssertionError("the condition did not hold in time")


def fthreadStartTheDownload(dictAcquisition, dictControl, listOutcome, listEvents):
    """Run the verified download on a thread, recording its result or error."""
    def fnRun():
        try:
            listOutcome.append(imageAcquisition.fsDownloadVerifiedTarball(
                fdictRecord(), dictAcquisition, listEvents.append, dictControl,
            ))
        except Exception as error:  # noqa: BLE001 -- the test reads it
            listOutcome.append(error)
    threadDownload = threading.Thread(target=fnRun, daemon=True)
    threadDownload.start()
    return threadDownload


# ---------------------------------------------------------------------
# Patience: a try that gets no answer is tried again.
# ---------------------------------------------------------------------


@pytest.mark.falsification
def test_a_lookup_that_gets_no_answer_is_tried_again_and_the_image_loads(
    serverZenodo, fnShortenTheLookup,
):
    """Kills: one try at locating the deposit (a dropped answer refuses it).

    The record API and the DOI hop both drop their first connection --
    the shape of a Zenodo that did not finish a handshake in time. The
    second try finds the file, and the chain downloads, verifies and
    loads it.
    """
    fnShortenTheLookup()
    serverZenodo.dictBehavior["dictPathFaults"] = {
        S_RECORD_PREFIX: ["drop"], S_DOI_PREFIX: ["drop"],
    }
    store = FakeImageStore(bRegistryServes=False)
    listEvents = []
    dictAcquired = imageAcquisition.fdictAcquirePinnedImage(
        fdictEnvelope(), S_REQUIRED_PLATFORM, fnStatusCallback=listEvents.append,
        dockerDisposable=store,
    )
    assert dictAcquired["sObtainedFrom"] == S_OBTAINED_ARCHIVE
    assert dictAcquired["sImageReference"] == S_LOADED_IMAGE_ID
    assert store.listLoaded == [BA_FILE]
    assert len(flistPaths(serverZenodo, S_RECORD_PREFIX)) == 2
    listRetries = [
        dictEvent for dictEvent in listEvents
        if dictEvent["sPhase"] == "locatingRetrying"
    ]
    assert len(listRetries) == 1
    assert "127.0.0.1" in listRetries[0]["sSentence"]


@pytest.mark.falsification
def test_a_zenodo_that_answers_503_is_asked_again_not_followed_to_a_dead_url(
    serverZenodo, fnShortenTheLookup,
):
    """Kills: a DOI hop answered with a 5xx being read as the record's address.

    The resolver's error page is not the record page: a file URL built
    under it names nothing, and the download that follows is refused
    as final. A 503 is the server's own side failing, so the try is
    repeated -- and the second finds the file.
    """
    fnShortenTheLookup()
    serverZenodo.dictBehavior["dictPathFaults"] = {
        S_RECORD_PREFIX: [503, 503], S_DOI_PREFIX: [503],
    }
    dictAcquisition = downloadPartial.fdictOpenAcquisition(fdictRecord(), {"sKind": "test"})
    try:
        sPath = imageAcquisition.fsDownloadVerifiedTarball(
            fdictRecord(), dictAcquisition, lambda dictEvent: None,
        )
        with open(sPath, "rb") as fileTarball:
            assert fileTarball.read() == BA_FILE
    finally:
        downloadPartial.fnDiscardAcquisition(dictAcquisition)
    assert len(flistPaths(serverZenodo, S_DOI_PREFIX)) == 2


# ---------------------------------------------------------------------
# Cancel: a blocked try never makes the researcher wait.
# ---------------------------------------------------------------------


@pytest.mark.falsification
def test_cancel_ends_a_try_blocked_on_a_silent_zenodo_within_a_second(
    serverZenodo, fnShortenTheLookup,
):
    """Kills: running the try on the caller's thread (Cancel waits out its timeouts).

    The record request is held unanswered for longer than the test
    allows; with the client's read timeout at minutes, only a try the
    caller can abandon lets Cancel return at once.
    """
    fnShortenTheLookup()
    serverZenodo.dictBehavior["dictPathFaults"] = {S_RECORD_PREFIX: [("stall", 30.0)]}
    dictAcquisition = downloadPartial.fdictOpenAcquisition(fdictRecord(), {"sKind": "test"})
    dictControl = resumableDownload.fdictCreateDownloadControl()
    listOutcome, listEvents = [], []
    threadDownload = fthreadStartTheDownload(
        dictAcquisition, dictControl, listOutcome, listEvents,
    )
    try:
        fnWaitUntil(lambda: flistPaths(serverZenodo, S_RECORD_PREFIX))
        fStarted = time.monotonic()
        resumableDownload.fnRequestCancel(dictControl)
        threadDownload.join(timeout=3.0)
        fTaken = time.monotonic() - fStarted
    finally:
        serverZenodo.eventRelease.set()
        downloadPartial.fnDiscardAcquisition(dictAcquisition)
    assert not threadDownload.is_alive()
    assert fTaken < 1.5
    assert isinstance(listOutcome[0], ImageAcquisitionRefusedError)
    assert listOutcome[0].dictFailure["sFailureClass"] == resumableDownload.S_FAILURE_CANCELLED


# ---------------------------------------------------------------------
# The budget: a spent one keeps the bytes and says what happened.
# ---------------------------------------------------------------------


@pytest.mark.falsification
def test_a_spent_lookup_budget_keeps_the_bytes_an_earlier_attempt_downloaded(
    serverZenodo, fnShortenTheLookup,
):
    """Kills: a lookup refusal without a failure record (the chain deletes the partial).

    Before, an unreachable Zenodo raised a bare refusal, the archive
    link read it as nothing worth keeping, and a resumed download lost
    every byte it had -- to a server that was merely slow that minute.
    """
    fnShortenTheLookup(fBudget=0.4, fWait=0.05)
    listDrops = ["drop"] * 50
    serverZenodo.dictBehavior["dictPathFaults"] = {
        S_RECORD_PREFIX: list(listDrops), S_DOI_PREFIX: list(listDrops),
    }
    dictFirst = downloadPartial.fdictOpenAcquisition(fdictRecord(), {"sKind": "test"})
    with open(dictFirst["sPartPath"], "wb") as filePart:
        filePart.write(BA_FILE[:4096])
        downloadPartial.fnCheckpoint(dictFirst, filePart, 4096)
    downloadPartial.fnReleaseAcquisition(dictFirst)
    store = FakeImageStore(bRegistryServes=False)
    with pytest.raises(ImageAcquisitionRefusedError) as infoError:
        imageAcquisition.fdictAcquirePinnedImage(
            fdictEnvelope(), S_REQUIRED_PLATFORM, dockerDisposable=store,
        )
    dictFailure = infoError.value.dictFailure
    assert dictFailure["sFailureClass"] == imageAcquisition.S_FAILURE_ZENODO_UNREACHABLE
    assert dictFailure["bPartialKept"] is True
    assert dictFailure["iBytesOnDisk"] == 4096
    assert "could not reach Zenodo" in dictFailure["sSentence"]
    assert S_DOI in str(infoError.value)
    listKept = [
        dictPartial for dictPartial in downloadPartial.flistDescribePartials()
        if dictPartial["sAcquisitionId"] == dictFailure["sAcquisitionId"]
    ]
    assert [dictPartial["iBytesOnDisk"] for dictPartial in listKept] == [4096]
    assert store.listLoaded == []


def test_a_refused_redirect_is_final_on_the_first_try(
    serverZenodo, fnShortenTheLookup, monkeypatch,
):
    """Only an UNANSWERED try is repeated; a resolver off Zenodo is refused at once."""
    fnShortenTheLookup()
    serverZenodo.dictBehavior["dictPathFaults"] = {S_RECORD_PREFIX: [404]}
    monkeypatch.setattr(imageAcquisition, "_S_DOI_RESOLVER", "https://resolver.invalid/")
    monkeypatch.setattr(
        imageAcquisition, "_flistDoiFollowOrigins", zenodoClient.flistAllowedZenodoOrigins,
    )
    dictAcquisition = downloadPartial.fdictOpenAcquisition(fdictRecord(), {"sKind": "test"})
    try:
        with pytest.raises(ImageAcquisitionRefusedError, match="resolves outside Zenodo"):
            imageAcquisition.fsDownloadVerifiedTarball(
                fdictRecord(), dictAcquisition, lambda dictEvent: None,
            )
    finally:
        downloadPartial.fnDiscardAcquisition(dictAcquisition)
    assert len(flistPaths(serverZenodo, S_RECORD_PREFIX)) == 1


# ---------------------------------------------------------------------
# The poll: the stage and its timers as they happen.
# ---------------------------------------------------------------------


@pytest.mark.falsification
def test_the_poll_reads_the_lookup_stage_and_counts_down_to_the_next_try(
    serverZenodo, fnShortenTheLookup,
):
    """Kills: a lookup that stamps no stage (the window guesses from zero bytes).

    Between tries the poll must say which stage this is, which try
    failed and why, how long until the next, and how long until the
    lookup gives up -- each computed when the poll reads it.
    """
    fnShortenTheLookup(fBudget=60.0, fWait=3.0)
    serverZenodo.dictBehavior["dictPathFaults"] = {
        S_RECORD_PREFIX: ["drop"], S_DOI_PREFIX: ["drop"],
    }
    dictAcquisition = downloadPartial.fdictOpenAcquisition(fdictRecord(), {"sKind": "test"})
    dictControl = resumableDownload.fdictCreateDownloadControl()
    listOutcome, listEvents = [], []
    threadDownload = fthreadStartTheDownload(
        dictAcquisition, dictControl, listOutcome, listEvents,
    )
    try:
        fnWaitUntil(lambda: resumableDownload.fdictReadDownloadProgress(
            dictControl)["fSecondsUntilRetry"] > 0)
        dictFirst = resumableDownload.fdictReadDownloadProgress(dictControl)
        time.sleep(0.3)
        dictLater = resumableDownload.fdictReadDownloadProgress(dictControl)
    finally:
        resumableDownload.fnRequestCancel(dictControl)
        threadDownload.join(timeout=3.0)
        downloadPartial.fnDiscardAcquisition(dictAcquisition)
    assert dictFirst["sStage"] == imageAcquisition.S_STAGE_LOCATING
    assert dictFirst["iAttempt"] == 1
    assert "127.0.0.1" in dictFirst["sLastFailure"]
    assert dictFirst["sAcquisitionId"] == dictAcquisition["sAcquisitionId"]
    assert 0 < dictFirst["fSecondsUntilGiveUp"] <= 60.0
    assert dictLater["fSecondsUntilRetry"] < dictFirst["fSecondsUntilRetry"]
    assert dictLater["fSecondsUntilGiveUp"] < dictFirst["fSecondsUntilGiveUp"]
    assert dictLater["fSecondsInStage"] > dictFirst["fSecondsInStage"]


def test_each_stage_is_stamped_as_it_begins(serverZenodo, fnShortenTheLookup, monkeypatch):
    """The chain's stages, in the order the window must name them."""
    fnShortenTheLookup()
    listStages = []
    fnEnterStageReal = resumableDownload.fnEnterStage

    def fnRecordStage(dictControl, sStage, *args, **kwargs):
        listStages.append(sStage)
        fnEnterStageReal(dictControl, sStage, *args, **kwargs)
    monkeypatch.setattr(resumableDownload, "fnEnterStage", fnRecordStage)
    imageAcquisition.fdictAcquirePinnedImage(
        fdictEnvelope(), S_REQUIRED_PLATFORM,
        dockerDisposable=FakeImageStore(bRegistryServes=False),
        dictDownloadControl=resumableDownload.fdictCreateDownloadControl(),
    )
    assert listStages == [
        imageAcquisition.S_STAGE_REGISTRY, imageAcquisition.S_STAGE_LOCATING,
        imageAcquisition.S_STAGE_DOWNLOADING, imageAcquisition.S_STAGE_LOADING,
    ]


# ---------------------------------------------------------------------
# One patience for every connection to Zenodo.
# ---------------------------------------------------------------------


def test_every_connection_to_zenodo_is_given_the_same_long_patience():
    """The lookup, the download and the client all wait as long for Zenodo.

    In ``requests`` and in the ranged transport the connect timeout also
    bounds the TLS handshake, which Zenodo routinely takes tens of
    seconds to finish; a ten-second bound read that as unreachable.
    """
    fConnect = zenodoClient.F_CONNECT_TIMEOUT_SECONDS
    assert fConnect >= 120.0
    assert zenodoClient.F_READ_TIMEOUT_SECONDS >= 120.0
    assert rangedHttpTransport._F_CONNECT_TIMEOUT_SECONDS == fConnect
    assert resumableDownload._F_READ_TIMEOUT_SECONDS == zenodoClient.F_READ_TIMEOUT_SECONDS
    assert imageAcquisition._T_HTTP_TIMEOUT_SECONDS == (
        fConnect, zenodoClient.F_READ_TIMEOUT_SECONDS,
    )
    assert zenodoClient._TUPLE_REQUEST_TIMEOUT_SECONDS[0] == fConnect
    assert zenodoClient._TUPLE_UPLOAD_TIMEOUT_SECONDS[0] == fConnect


# ---------------------------------------------------------------------
# The deposit is named, from the envelope and the record.
# ---------------------------------------------------------------------


@pytest.mark.falsification
def test_the_poll_names_the_deposit_by_doi_file_and_record_title(
    serverZenodo, fnShortenTheLookup,
):
    """Kills: a lookup that never records which deposit it is fetching.

    The window heads the download with the deposit's name so the
    researcher can see it is the one the project pins: the DOI and file
    name from the envelope, the title from Zenodo's record once read.
    """
    fnShortenTheLookup()
    dictControl = resumableDownload.fdictCreateDownloadControl()
    imageAcquisition.fdictAcquirePinnedImage(
        fdictEnvelope(), S_REQUIRED_PLATFORM,
        dockerDisposable=FakeImageStore(bRegistryServes=False),
        dictDownloadControl=dictControl,
    )
    dictDeposit = resumableDownload.fdictReadDownloadProgress(dictControl)["dictDeposit"]
    assert dictDeposit == {
        "sDoi": S_DOI, "sFileName": S_TARBALL_NAME,
        "sTitle": "Loopback fixture environment image",
    }


def test_a_record_title_is_one_bounded_line_of_printable_text():
    """Zenodo's title is third-party text; the heading shows one short line of it."""
    sTitle = imageAcquisition._fsReadDepositTitle({"metadata": {
        "title": "An\n environment\x1b[31m image\t" + "x" * 400,
    }})
    assert "\n" not in sTitle and "\x1b" not in sTitle and "\t" not in sTitle
    assert sTitle.startswith("An environment[31m image x")
    assert len(sTitle) == imageAcquisition._I_TITLE_CHARACTERS
    assert sTitle.endswith("…")
    assert imageAcquisition._fsReadDepositTitle({}) == ""


def test_the_log_keeps_one_line_per_tenth_of_the_download():
    """The header shows live progress; the log is not a second copy of it."""
    from vaibify.docker.pinnedImageAcquisition import _ffnReportAcquisitionEventsTo
    listLines = []
    fnReportEvent = _ffnReportAcquisitionEventsTo(listLines.append)
    iTotal = 733_000_000
    for iBytes in range(0, iTotal + 1, 8 * 1024 * 1024):
        fnReportEvent({
            "sPhase": "downloading", "iBytes": iBytes, "iTotalBytes": iTotal,
            "fBytesPerSecond": 4e5, "fSecondsRemaining": 600.0,
        })
    fnReportEvent({"sPhase": "downloading", "iBytes": iTotal, "iTotalBytes": iTotal})
    fnReportEvent({"sPhase": "loading"})
    listProgress = [sLine for sLine in listLines if sLine.startswith("downloading")]
    # 0%, 10%, ... 90%, then 100%: one line per tenth, ninety-odd events.
    assert [sLine.rsplit("(", 1)[1] for sLine in listProgress] == [
        f"{iPercent}%)" for iPercent in range(0, 101, 10)
    ]
    assert not any("MB/s" in sLine or "left" in sLine for sLine in listProgress)
    assert listLines[-1] == "loading"
