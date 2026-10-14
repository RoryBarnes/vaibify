"""Download a deposit's tarball into an acquisition: resume, retry, report.

The policy half of the resilient download. The bytes live in an
acquisition directory (:mod:`downloadPartial`); each request is one
cancellable ranged GET (:mod:`rangedHttpTransport`); this module decides
what to do with each answer and when to try again.

**Resume with Range requests** (RFC 9110 section 14). Every request asks
for the bytes from the end of the part file on. A ``206`` is checked
field by field -- the start must equal the bytes on disk, the total
must equal the size the envelope records, the body must be exactly as
long as the part the server named -- and a server may legitimately
answer with a SHORTER part than asked for, which is accepted and
followed by the next request. A ``200`` means the server ignored the
range: the part is truncated to zero and the download starts over,
saying so. A ``416`` with every byte already on disk is a complete
file. A total that differs from the envelope's size means the deposit
changed, which nothing here can resume from.

**Retry on transient failures only**: a connection that drops or never
opens, a stalled read, an incomplete body, ``429`` and the ``5xx``
gateway answers. Waits are exponential with full jitter (Brooker,
"Exponential Backoff and Jitter", AWS Architecture Blog, 2015), base 5 s,
cap 120 s, and ``Retry-After`` (RFC 9110 section 10.2.3) is honored.
**Progress means the bytes on disk passed their previous high-water
mark.** A server that ignores Range and drops after the same prefix
every time never advances it, and uses up the budget of six attempts;
then the download stops and waits for the researcher's decision, with
the partial KEPT. Try again starts a fresh budget. ``4xx`` answers other
than ``429`` are never retried: the server read the request and said no.

**Cancel returns within a second in every state.** The body is read in
64 KiB blocks with the cancel flag checked between them; a read blocked
on a stalled socket is woken by shutting the socket down from the
cancelling thread; every wait is an ``Event.wait``; and the connect
phase is abandoned on the transport's worker pool. Cancelling means the
researcher gave up, so the caller deletes the partial.

**Progress is computed when read.** The worker only stamps counters and
timestamps as bytes land; :func:`fdictReadDownloadProgress` derives the
rolling rate, the time remaining and how long the connection has been
silent at the moment a poll asks, so a blocked read still reads as
"no data for 45 s" without the worker having to wake.

**A power cut loses at most one checkpoint.** Every 32 MiB the part is
synced and the sidecar's durable count rewritten, durably. The caller's
final sha256 check against the envelope remains the only authority on
the bytes: resume can cost time, never correctness.
"""

__all__ = [
    "DownloadCancelledError",
    "DownloadRefusedError",
    "S_FAILURE_BUDGET_EXHAUSTED",
    "S_FAILURE_CANCELLED",
    "S_FAILURE_CHALLENGE_PAGE",
    "S_FAILURE_COMPRESSED_TRANSFER",
    "S_FAILURE_DEPOSIT_CHANGED",
    "S_FAILURE_DISK_FULL",
    "S_FAILURE_GREW_PAST_SIZE",
    "S_FAILURE_INCOMPLETE",
    "S_FAILURE_NOT_AVAILABLE",
    "S_FAILURE_NO_ROOM",
    "S_FAILURE_WRITE_FAILED",
    "fdictCreateDownloadControl",
    "fdictReadDownloadProgress",
    "fnRequestCancel",
    "fsDescribeBytes",
    "fsDownloadIntoAcquisition",
]

import email.utils
import errno
import hashlib
import http.client
import logging
import random
import re
import shutil
import socket
import threading
import time
from collections import deque
from datetime import datetime, timezone

from vaibify.config import keepAliveManager
from vaibify.reproducibility import downloadPartial
from vaibify.reproducibility import rangedHttpTransport


logger = logging.getLogger("vaibify")

S_FAILURE_BUDGET_EXHAUSTED = "budget-exhausted"
S_FAILURE_NOT_AVAILABLE = "not-available"
S_FAILURE_CHALLENGE_PAGE = "challenge-page"
S_FAILURE_COMPRESSED_TRANSFER = "compressed-transfer"
S_FAILURE_DEPOSIT_CHANGED = "deposit-changed"
S_FAILURE_GREW_PAST_SIZE = "grew-past-size"
S_FAILURE_INCOMPLETE = "incomplete"
S_FAILURE_DISK_FULL = "disk-full"
S_FAILURE_NO_ROOM = "no-room"
S_FAILURE_WRITE_FAILED = "write-failed"
S_FAILURE_CANCELLED = "cancelled"

_I_READ_BLOCK_BYTES = 64 * 1024
_I_CHECKPOINT_BYTES = 32 * 1024 * 1024
_I_STATUS_EVERY_BYTES = 8 * 1024 * 1024
_I_ATTEMPTS_WITHOUT_ADVANCE = 6
_F_BACKOFF_BASE_SECONDS = 5.0
_F_BACKOFF_CAP_SECONDS = 120.0
_F_RETRY_AFTER_CAP_SECONDS = 600.0
_F_READ_TIMEOUT_SECONDS = 60.0
_F_RATE_WINDOW_SECONDS = 30.0
_F_SLOW_RATE_BYTES_PER_SECOND = 100_000.0
_F_SLOW_RATE_NOTICE_SECONDS = 120.0
_F_FREE_SPACE_MULTIPLIER = 1.25
_T_TRANSIENT_STATUSES = (429, 500, 502, 503, 504)
_T_PERMANENT_STATUSES = (400, 401, 403, 404, 410)
_REGEX_CONTENT_RANGE = re.compile(r"^\s*bytes\s+(\d+)-(\d+)/(\d+)\s*$")


class DownloadRefusedError(Exception):
    """The download stopped and will not continue on its own.

    ``sFailureClass`` keys the sentence table; ``bPartialKept`` says
    whether the bytes on disk are worth keeping for a resume (a budget
    spent, a full disk) or were thrown away (a deposit that changed).
    ``dictDetail`` carries the byte counts and the technical text.
    """

    def __init__(self, sSentence, sFailureClass, bPartialKept, dictDetail=None):
        super().__init__(sSentence)
        self.sFailureClass = sFailureClass
        self.bPartialKept = bPartialKept
        self.dictDetail = dict(dictDetail or {})


class DownloadCancelledError(DownloadRefusedError):
    """The researcher gave up; the caller deletes the partial."""

    def __init__(self, dictDetail=None):
        super().__init__(
            "The download was canceled.", S_FAILURE_CANCELLED, False,
            dictDetail,
        )


class _TransientFailure(Exception):
    """One attempt ended in a way another attempt may cure."""

    def __init__(self, sWhat, fRetryAfterSeconds=0.0):
        super().__init__(sWhat)
        self.fRetryAfterSeconds = fRetryAfterSeconds


class _RestartFromZero(Exception):
    """The server's answer cannot continue the partial; start over."""


def fsDescribeBytes(iBytes):
    """Render a byte count the way the progress window says it: in MB."""
    return f"{int(iBytes) / 1e6:.0f} MB"


# ---------------------------------------------------------------------
# The control dict: Cancel, and the counters a poll reads.
# ---------------------------------------------------------------------


def fdictCreateDownloadControl(dictPurpose=None):
    """Return the control dict one download runs under.

    ``eventCancel`` is the one Cancel signal; ``dictLive`` holds the
    counters the worker stamps and :func:`fdictReadDownloadProgress`
    derives from; ``dictPurpose`` is recorded in the sidecar so an
    interrupted partial can say what it was for.
    """
    return {
        "eventCancel": threading.Event(),
        "lockLive": threading.Lock(),
        "dictPurpose": dict(dictPurpose or {}),
        "dictLive": {
            "iBytesOnDisk": 0, "iExpectedBytes": 0, "iHighWaterBytes": 0,
            "fStartedMonotonic": 0.0, "fLastByteMonotonic": 0.0,
            "dequeRateSamples": deque(), "iAttempt": 0,
            "sAttemptState": "", "fRetryAtMonotonic": 0.0,
            "sLastFailure": "", "socketLive": None, "iCurrentToken": 0,
            "sAcquisitionId": "", "fSlowSinceMonotonic": 0.0,
            "bSlowNoticeGiven": False, "bFinished": False,
        },
    }


def fnRequestCancel(dictControl):
    """Ask the download to stop: set the flag and wake any blocked read."""
    dictControl["eventCancel"].set()
    rangedHttpTransport.fnShutdownLiveSocket(dictControl)


def fdictReadDownloadProgress(dictControl):
    """Derive what the progress window shows from the live counters.

    Computed at read time, so a worker blocked on a dead socket still
    reads as silent for however long it has been.
    """
    with dictControl["lockLive"]:
        dictLive = dict(dictControl["dictLive"])
        listSamples = list(dictLive["dequeRateSamples"])
    fNow = time.monotonic()
    fRate = _ffRollingRate(listSamples, fNow)
    iRemaining = max(0, dictLive["iExpectedBytes"] - dictLive["iBytesOnDisk"])
    fRetryIn = max(0.0, dictLive["fRetryAtMonotonic"] - fNow) if (
        dictLive["fRetryAtMonotonic"] > fNow) else 0.0
    return {
        "iBytes": dictLive["iBytesOnDisk"],
        "iTotalBytes": dictLive["iExpectedBytes"],
        "fBytesPerSecond": fRate,
        "fSecondsRemaining": (iRemaining / fRate) if fRate > 0 else None,
        "fSecondsSinceLastByte": (
            fNow - dictLive["fLastByteMonotonic"]
            if dictLive["fLastByteMonotonic"] else None
        ),
        "iAttempt": dictLive["iAttempt"],
        "sAttemptState": dictLive["sAttemptState"],
        "fSecondsUntilRetry": fRetryIn,
        "bSlow": bool(dictLive["fSlowSinceMonotonic"]) and (
            fNow - dictLive["fSlowSinceMonotonic"] >= _F_SLOW_RATE_NOTICE_SECONDS
        ),
        "sLastFailure": dictLive["sLastFailure"],
        "sAcquisitionId": dictLive["sAcquisitionId"],
        "bFinished": dictLive["bFinished"],
    }


def _ffRollingRate(listSamples, fNow):
    """Bytes per second over the last window, 0 when nothing recent landed."""
    listRecent = [
        tSample for tSample in listSamples
        if fNow - tSample[0] <= _F_RATE_WINDOW_SECONDS
    ]
    if len(listRecent) < 2:
        return 0.0
    fSpan = listRecent[-1][0] - listRecent[0][0]
    if fSpan <= 0.0:
        return 0.0
    return (listRecent[-1][1] - listRecent[0][1]) / fSpan


def _fnStampLive(dictControl, dictUpdate):
    with dictControl["lockLive"]:
        dictControl["dictLive"].update(dictUpdate)


def _fnNoteBytesLanded(dictControl, iBytesOnDisk):
    """Record that the part grew, for the rate and the silence clock."""
    fNow = time.monotonic()
    with dictControl["lockLive"]:
        dictLive = dictControl["dictLive"]
        dictLive["iBytesOnDisk"] = iBytesOnDisk
        dictLive["fLastByteMonotonic"] = fNow
        dequeSamples = dictLive["dequeRateSamples"]
        dequeSamples.append((fNow, iBytesOnDisk))
        while dequeSamples and fNow - dequeSamples[0][0] > _F_RATE_WINDOW_SECONDS:
            dequeSamples.popleft()


# ---------------------------------------------------------------------
# The download.
# ---------------------------------------------------------------------


def fsDownloadIntoAcquisition(
    dictAcquisition, sFileUrl, listAllowedOrigins, fnStatus, dictControl,
):
    """Bring the acquisition's part file to the recorded size; return its sha256.

    Returns ``sha256:<hex>`` of the complete file. Raises
    :class:`DownloadCancelledError` when Cancel was requested and
    :class:`DownloadRefusedError` when the download stopped for any
    other reason, saying whether the partial was kept.
    """
    iExpectedBytes = int(dictAcquisition["dictSidecar"]["iExpectedBytes"])
    _fnStampLive(dictControl, {
        "iExpectedBytes": iExpectedBytes,
        "sAcquisitionId": dictAcquisition["sAcquisitionId"],
        "fStartedMonotonic": time.monotonic(),
    })
    downloadPartial.fnRecordSidecar(dictAcquisition, {"sFileUrl": sFileUrl})
    _fnRefuseWithoutRoomForTheRest(dictAcquisition, iExpectedBytes, dictControl)
    dictRun = {
        "hasher": None, "iOnDisk": 0, "iHighWater": 0, "iWithoutAdvance": 0,
        "iAttempt": 0, "bRestartedAfter416": False,
    }
    _fnRehashThePrefix(dictAcquisition, dictControl, dictRun)
    _fnHoldSleepPrevention(dictAcquisition["sAcquisitionId"])
    try:
        while dictRun["iOnDisk"] < iExpectedBytes:
            dictRun["iAttempt"] += 1
            _fnStampLive(dictControl, {
                "iAttempt": dictRun["iAttempt"], "sAttemptState": "",
                "fRetryAtMonotonic": 0.0,
            })
            try:
                _fnRunOneAttempt(
                    dictAcquisition, sFileUrl, listAllowedOrigins, fnStatus,
                    dictControl, dictRun,
                )
            except _TransientFailure as error:
                _fnWaitBeforeRetrying(
                    error, dictAcquisition, dictControl, dictRun, fnStatus,
                )
            except _RestartFromZero as error:
                _fnRestartFromZero(
                    dictAcquisition, dictControl, dictRun, fnStatus, str(error),
                )
    finally:
        _fnReleaseSleepPrevention(dictAcquisition["sAcquisitionId"])
    _fnRefuseUnlessExactlyTheRecordedSize(dictAcquisition, dictControl, dictRun)
    _fnStampLive(dictControl, {"bFinished": True})
    return "sha256:" + dictRun["hasher"].hexdigest()


def _fnRefuseWithoutRoomForTheRest(dictAcquisition, iExpectedBytes, dictControl):
    """Refuse unless the disk has room for the bytes still to come, with slack."""
    iOnDisk = downloadPartial.fiBytesOnDisk(dictAcquisition)
    iNeeded = int(max(0, iExpectedBytes - iOnDisk) * _F_FREE_SPACE_MULTIPLIER)
    iFree = shutil.disk_usage(dictAcquisition["sDirectory"]).free
    if iFree >= iNeeded:
        return
    raise DownloadRefusedError(
        "There is not enough free space on this disk to finish the "
        f"download: about {iNeeded / 1e9:.1f} GB is needed and "
        f"{iFree / 1e9:.1f} GB is free. Free some space, then try again; "
        f"the {fsDescribeBytes(iOnDisk)} already downloaded are kept.",
        S_FAILURE_NO_ROOM, True, _fdictDetail(dictAcquisition, dictControl, ""),
    )


def _fnRehashThePrefix(dictAcquisition, dictControl, dictRun):
    """Hash the bytes already on disk into a fresh hasher, cancellably."""
    hasher = hashlib.sha256()
    iOnDisk = 0
    try:
        with open(dictAcquisition["sPartPath"], "rb") as filePart:
            while True:
                if dictControl["eventCancel"].is_set():
                    raise DownloadCancelledError(
                        _fdictDetail(dictAcquisition, dictControl, ""))
                baBlock = filePart.read(_I_READ_BLOCK_BYTES)
                if not baBlock:
                    break
                hasher.update(baBlock)
                iOnDisk += len(baBlock)
    except FileNotFoundError:
        pass
    dictRun["hasher"] = hasher
    dictRun["iOnDisk"] = iOnDisk
    dictRun["iHighWater"] = max(
        iOnDisk, int(dictAcquisition["dictSidecar"].get("iHighWaterBytes") or 0),
    )
    _fnStampLive(dictControl, {"iBytesOnDisk": iOnDisk, "iHighWaterBytes": dictRun["iHighWater"]})


def _fnRunOneAttempt(
    dictAcquisition, sFileUrl, listAllowedOrigins, fnStatus, dictControl,
    dictRun,
):
    """One ranged GET from the bytes on disk; append whatever it serves."""
    iExpectedBytes = int(dictAcquisition["dictSidecar"]["iExpectedBytes"])
    _fnReportDownloading(fnStatus, dictControl)
    try:
        dictAnswer = rangedHttpTransport.fdictOpenRangedGet(
            sFileUrl, dictRun["iOnDisk"], listAllowedOrigins, dictControl,
            _F_READ_TIMEOUT_SECONDS,
        )
    except rangedHttpTransport.CancelledDuringTransport:
        raise DownloadCancelledError(_fdictDetail(dictAcquisition, dictControl, ""))
    except rangedHttpTransport.TransportFailure as error:
        if error.bTransient:
            raise _TransientFailure(str(error)) from error
        raise DownloadRefusedError(
            _fsSentenceForTransportRefusal(error), error.sFailureClass, True,
            _fdictDetail(dictAcquisition, dictControl, str(error)),
        ) from error
    try:
        iPartLength = _fiJudgeTheAnswer(
            dictAnswer, dictRun, iExpectedBytes, dictAcquisition, dictControl,
        )
        if iPartLength > 0:
            _fnStreamTheBody(
                dictAnswer, iPartLength, dictAcquisition, dictControl, dictRun,
                fnStatus,
            )
    finally:
        rangedHttpTransport.fnCloseAnswer(dictAnswer)


def _fsSentenceForTransportRefusal(error):
    if error.sFailureClass == rangedHttpTransport.S_FAILURE_TLS:
        return (
            "A secure connection to Zenodo could not be made. This usually "
            "means this computer's clock is wrong, or a network device is "
            "intercepting secure traffic. Check the clock, or try another "
            "network."
        )
    return (
        "The archived image's link leaves Zenodo, so it was not fetched "
        f"({error})."
    )


def _fiJudgeTheAnswer(dictAnswer, dictRun, iExpectedBytes, dictAcquisition, dictControl):
    """Decide what the status and headers mean; return the body length to read.

    Zero means nothing to read: the file was already complete.
    """
    iStatus = dictAnswer["iStatus"]
    dictHeaders = dictAnswer["dictHeaders"]
    _fnRefuseACompressedTransfer(dictHeaders, dictAcquisition, dictControl)
    _fnRefuseAChallengePage(iStatus, dictHeaders, dictAcquisition, dictControl)
    if iStatus in _T_TRANSIENT_STATUSES:
        raise _TransientFailure(
            f"Zenodo answered {iStatus} {dictAnswer['httpresponseAnswer'].reason}",
            _ffRetryAfterSeconds(dictHeaders.get("retry-after")),
        )
    if iStatus in _T_PERMANENT_STATUSES:
        raise DownloadRefusedError(
            "The author's archived environment is no longer available from "
            f"Zenodo (it answered {iStatus}). Nothing can be downloaded; "
            "contact the author, or build from the Dockerfile, which cannot "
            "reproduce the archived bytes.",
            S_FAILURE_NOT_AVAILABLE, False,
            _fdictDetail(dictAcquisition, dictControl, f"HTTP {iStatus}"),
        )
    if iStatus == 416:
        return _fiJudgeRangeNotSatisfiable(dictRun, iExpectedBytes)
    if iStatus == 200:
        return _fiJudgeWholeFile(dictHeaders, dictRun, iExpectedBytes, dictAcquisition, dictControl)
    if iStatus == 206:
        return _fiJudgePartialContent(dictHeaders, dictRun, iExpectedBytes, dictAcquisition, dictControl)
    raise _TransientFailure(f"Zenodo gave an unexpected answer ({iStatus})")


def _fnRefuseACompressedTransfer(dictHeaders, dictAcquisition, dictControl):
    sEncoding = str(dictHeaders.get("content-encoding") or "identity").strip().lower()
    if sEncoding in ("", "identity"):
        return
    raise DownloadRefusedError(
        "The server compressed the transfer, so the bytes received would "
        "not be the archived file's own and the download could not be "
        "resumed. The download was stopped.",
        S_FAILURE_COMPRESSED_TRANSFER, True,
        _fdictDetail(dictAcquisition, dictControl, f"Content-Encoding: {sEncoding}"),
    )


def _fnRefuseAChallengePage(iStatus, dictHeaders, dictAcquisition, dictControl):
    """A web page where the file should be is a login page or a bot check."""
    sType = str(dictHeaders.get("content-type") or "").lower()
    if iStatus in (200, 206, 403) and "text/html" in sType:
        raise DownloadRefusedError(
            "Zenodo returned a web page instead of the file. This usually "
            "means a network login page (hotel or campus Wi-Fi) or an "
            "automated-traffic check is in the way. Open a browser, log "
            "in to the network if it asks, then try again.",
            S_FAILURE_CHALLENGE_PAGE, True,
            _fdictDetail(dictAcquisition, dictControl, f"HTTP {iStatus}, {sType}"),
        )


def _fiJudgeRangeNotSatisfiable(dictRun, iExpectedBytes):
    if dictRun["iOnDisk"] == iExpectedBytes:
        return 0
    if dictRun["bRestartedAfter416"]:
        raise _TransientFailure(
            "the server would not serve the remaining bytes (416)",
        )
    dictRun["bRestartedAfter416"] = True
    raise _RestartFromZero(
        "the server said the saved bytes do not match the file (416)",
    )


def _fiJudgeWholeFile(dictHeaders, dictRun, iExpectedBytes, dictAcquisition, dictControl):
    if dictRun["iOnDisk"] > 0:
        raise _RestartFromZero("the server would not resume")
    iLength = _fiHeaderInteger(dictHeaders.get("content-length"))
    if iLength is not None and iLength != iExpectedBytes:
        raise DownloadRefusedError(
            _fsDepositChangedSentence(iLength, iExpectedBytes),
            S_FAILURE_DEPOSIT_CHANGED, False,
            _fdictDetail(dictAcquisition, dictControl, f"Content-Length {iLength}"),
        )
    return iExpectedBytes


def _fiJudgePartialContent(dictHeaders, dictRun, iExpectedBytes, dictAcquisition, dictControl):
    matchRange = _REGEX_CONTENT_RANGE.match(str(dictHeaders.get("content-range") or ""))
    if matchRange is None:
        raise _TransientFailure("the server's 206 answer named no byte range")
    iStart, iEnd, iTotal = (int(sGroup) for sGroup in matchRange.groups())
    if iTotal != iExpectedBytes:
        raise DownloadRefusedError(
            _fsDepositChangedSentence(iTotal, iExpectedBytes),
            S_FAILURE_DEPOSIT_CHANGED, False,
            _fdictDetail(dictAcquisition, dictControl, f"Content-Range total {iTotal}"),
        )
    if iStart != dictRun["iOnDisk"] or iEnd < iStart or iEnd > iTotal - 1:
        raise _TransientFailure(
            f"the server answered bytes {iStart}-{iEnd} where "
            f"{dictRun['iOnDisk']}- was asked for"
        )
    iLength = _fiHeaderInteger(dictHeaders.get("content-length"))
    if iLength is not None and iLength != iEnd - iStart + 1:
        raise _TransientFailure("the server's part length disagrees with its range")
    return iEnd - iStart + 1


def _fsDepositChangedSentence(iServed, iExpectedBytes):
    return (
        "The archived file is not the one this project recorded: Zenodo "
        f"reports {iServed} bytes and the project's envelope records "
        f"{iExpectedBytes}. The download was stopped and the partial file "
        "deleted; the deposit may have been replaced."
    )


def _fiHeaderInteger(sValue):
    try:
        return int(str(sValue).strip())
    except (TypeError, ValueError):
        return None


def _ffRetryAfterSeconds(sValue):
    """Seconds a ``Retry-After`` asks for, 0 when absent or unreadable."""
    if not sValue:
        return 0.0
    sText = str(sValue).strip()
    if sText.isdigit():
        return min(float(sText), _F_RETRY_AFTER_CAP_SECONDS)
    try:
        datetimeAt = email.utils.parsedate_to_datetime(sText)
    except (TypeError, ValueError):
        return 0.0
    if datetimeAt.tzinfo is None:
        datetimeAt = datetimeAt.replace(tzinfo=timezone.utc)
    fSeconds = (datetimeAt - datetime.now(timezone.utc)).total_seconds()
    return min(max(0.0, fSeconds), _F_RETRY_AFTER_CAP_SECONDS)


def _fnStreamTheBody(
    dictAnswer, iPartLength, dictAcquisition, dictControl, dictRun, fnStatus,
):
    """Append the body to the part in small blocks, hashing and checkpointing."""
    httpresponseAnswer = dictAnswer["httpresponseAnswer"]
    iExpectedBytes = int(dictAcquisition["dictSidecar"]["iExpectedBytes"])
    iRead, iSinceCheckpoint, iSinceStatus = 0, 0, 0
    with open(dictAcquisition["sPartPath"], "ab") as filePart:
        try:
            while iRead < iPartLength:
                baBlock = _fbaReadOneBlock(
                    httpresponseAnswer, iPartLength - iRead, dictAcquisition,
                    dictControl, dictRun,
                )
                _fnAppendBlock(filePart, baBlock, dictAcquisition, dictControl, dictRun)
                if dictRun["iOnDisk"] > iExpectedBytes:
                    raise DownloadRefusedError(
                        "The download grew past the size the project records "
                        "and was stopped.", S_FAILURE_GREW_PAST_SIZE, False,
                        _fdictDetail(dictAcquisition, dictControl, ""),
                    )
                iRead += len(baBlock)
                iSinceCheckpoint += len(baBlock)
                iSinceStatus += len(baBlock)
                if iSinceCheckpoint >= _I_CHECKPOINT_BYTES:
                    downloadPartial.fnCheckpoint(dictAcquisition, filePart, dictRun["iOnDisk"])
                    iSinceCheckpoint = 0
                if iSinceStatus >= _I_STATUS_EVERY_BYTES:
                    _fnReportDownloading(fnStatus, dictControl)
                    iSinceStatus = 0
        finally:
            downloadPartial.fnCheckpoint(dictAcquisition, filePart, dictRun["iOnDisk"])
    _fnReportDownloading(fnStatus, dictControl)


def _fbaReadOneBlock(httpresponseAnswer, iRemaining, dictAcquisition, dictControl, dictRun):
    """Read up to one block, translating the ways a read can end badly."""
    if dictControl["eventCancel"].is_set():
        raise DownloadCancelledError(_fdictDetail(dictAcquisition, dictControl, ""))
    try:
        # read1, not read: read(n) waits for n bytes, so the bytes that
        # arrived before a stall sat in the buffer, uncounted and
        # unwritten, until the timeout threw them away. read1 hands
        # back what has landed.
        baBlock = httpresponseAnswer.read1(min(_I_READ_BLOCK_BYTES, iRemaining))
    except socket.timeout:
        raise _TransientFailure(
            f"Zenodo stopped sending: no data for {_F_READ_TIMEOUT_SECONDS:.0f} s "
            f"at {fsDescribeBytes(dictRun['iOnDisk'])}"
        ) from None
    except (OSError, http.client.HTTPException) as error:
        if dictControl["eventCancel"].is_set():
            raise DownloadCancelledError(
                _fdictDetail(dictAcquisition, dictControl, "")) from None
        raise _TransientFailure(
            f"the connection dropped at {fsDescribeBytes(dictRun['iOnDisk'])} "
            f"({type(error).__name__}: {error})"
        ) from error
    if not baBlock:
        raise _TransientFailure(
            f"the connection closed at {fsDescribeBytes(dictRun['iOnDisk'])} "
            f"with {fsDescribeBytes(iRemaining)} of this part still expected"
        )
    return baBlock


def _fnAppendBlock(filePart, baBlock, dictAcquisition, dictControl, dictRun):
    """Write one block; a write that fails is a refusal that keeps the part."""
    try:
        filePart.write(baBlock)
    except OSError as error:
        bFull = error.errno == errno.ENOSPC
        raise DownloadRefusedError(
            (
                "This disk ran out of space while the download was being "
                f"saved. Free some space, then try again; the "
                f"{fsDescribeBytes(dictRun['iOnDisk'])} already downloaded are kept."
                if bFull else
                f"The downloaded bytes could not be written to disk ({error}). "
                "Fix the disk problem, then try again; the bytes already "
                "downloaded are kept."
            ),
            S_FAILURE_DISK_FULL if bFull else S_FAILURE_WRITE_FAILED, True,
            _fdictDetail(dictAcquisition, dictControl, str(error)),
        ) from error
    dictRun["hasher"].update(baBlock)
    dictRun["iOnDisk"] += len(baBlock)
    _fnNoteBytesLanded(dictControl, dictRun["iOnDisk"])


# ---------------------------------------------------------------------
# Between attempts.
# ---------------------------------------------------------------------


def _fnWaitBeforeRetrying(error, dictAcquisition, dictControl, dictRun, fnStatus):
    """Charge the budget, then wait with jitter; raise when the budget is spent."""
    if dictRun["iOnDisk"] > dictRun["iHighWater"]:
        dictRun["iHighWater"] = dictRun["iOnDisk"]
        dictRun["iWithoutAdvance"] = 0
    else:
        dictRun["iWithoutAdvance"] += 1
    _fnStampLive(dictControl, {
        "sLastFailure": str(error), "iHighWaterBytes": dictRun["iHighWater"],
    })
    downloadPartial.fnRecordSidecar(dictAcquisition, {
        "sLastFailure": str(error), "iHighWaterBytes": dictRun["iHighWater"],
    })
    if dictRun["iWithoutAdvance"] >= _I_ATTEMPTS_WITHOUT_ADVANCE:
        raise DownloadRefusedError(
            _fsBudgetSpentSentence(dictAcquisition, dictRun, str(error)),
            S_FAILURE_BUDGET_EXHAUSTED, True,
            _fdictDetail(dictAcquisition, dictControl, str(error)),
        )
    fWait = error.fRetryAfterSeconds or random.uniform(
        0.0, min(
            _F_BACKOFF_CAP_SECONDS,
            _F_BACKOFF_BASE_SECONDS * (2 ** (dictRun["iWithoutAdvance"] - 1)),
        ),
    )
    sState = (
        f"{_fsCapitalize(str(error))}. Reconnecting in {fWait:.0f} s "
        f"(attempt {dictRun['iAttempt'] + 1})"
    )
    _fnStampLive(dictControl, {
        "sAttemptState": sState, "fRetryAtMonotonic": time.monotonic() + fWait,
    })
    fnStatus({
        "sPhase": "downloadRetrying", "sSentence": sState,
        "iBytes": dictRun["iOnDisk"],
        "iTotalBytes": int(dictAcquisition["dictSidecar"]["iExpectedBytes"]),
        "iAttempt": dictRun["iAttempt"], "fWaitSeconds": fWait,
    })
    if dictControl["eventCancel"].wait(fWait):
        raise DownloadCancelledError(_fdictDetail(dictAcquisition, dictControl, ""))


def _fsCapitalize(sText):
    return sText[:1].upper() + sText[1:] if sText else sText


def _fsBudgetSpentSentence(dictAcquisition, dictRun, sLastFailure):
    iExpectedBytes = int(dictAcquisition["dictSidecar"]["iExpectedBytes"])
    return (
        f"The download stopped after {_I_ATTEMPTS_WITHOUT_ADVANCE} attempts "
        f"in a row that got no further than {fsDescribeBytes(dictRun['iOnDisk'])} "
        f"of {fsDescribeBytes(iExpectedBytes)} (last: {sLastFailure}). "
        "The bytes downloaded so far are kept; Try again continues from "
        "there."
    )


def _fnRestartFromZero(dictAcquisition, dictControl, dictRun, fnStatus, sWhy):
    """Throw the partial away and begin again, saying why."""
    downloadPartial.fnTruncatePart(dictAcquisition, 0)
    downloadPartial.fnRecordSidecar(dictAcquisition, {"iDurableBytes": 0})
    _fnRehashThePrefix(dictAcquisition, dictControl, dictRun)
    dictRun["iWithoutAdvance"] += 1
    if dictRun["iWithoutAdvance"] >= _I_ATTEMPTS_WITHOUT_ADVANCE:
        raise DownloadRefusedError(
            _fsBudgetSpentSentence(dictAcquisition, dictRun, sWhy),
            S_FAILURE_BUDGET_EXHAUSTED, True,
            _fdictDetail(dictAcquisition, dictControl, sWhy),
        )
    sSentence = f"Starting over: {sWhy}."
    _fnStampLive(dictControl, {"sAttemptState": sSentence, "sLastFailure": sWhy})
    fnStatus({"sPhase": "downloadRestarting", "sSentence": sSentence})


def _fnRefuseUnlessExactlyTheRecordedSize(dictAcquisition, dictControl, dictRun):
    """A short file is incomplete, never corrupt; say which."""
    iExpectedBytes = int(dictAcquisition["dictSidecar"]["iExpectedBytes"])
    iOnDisk = downloadPartial.fiBytesOnDisk(dictAcquisition)
    if iOnDisk == iExpectedBytes and dictRun["iOnDisk"] == iExpectedBytes:
        return
    raise DownloadRefusedError(
        f"The download is incomplete: {fsDescribeBytes(iOnDisk)} of "
        f"{fsDescribeBytes(iExpectedBytes)} are on disk. Try again to "
        "continue from there.",
        S_FAILURE_INCOMPLETE, True, _fdictDetail(dictAcquisition, dictControl, ""),
    )


# ---------------------------------------------------------------------
# Reporting.
# ---------------------------------------------------------------------


def _fnReportDownloading(fnStatus, dictControl):
    """Emit one ``downloading`` event from the derived progress."""
    dictProgress = fdictReadDownloadProgress(dictControl)
    _fnNoticeASlowRate(dictProgress, dictControl, fnStatus)
    fnStatus({
        "sPhase": "downloading",
        "iBytes": dictProgress["iBytes"],
        "iTotalBytes": dictProgress["iTotalBytes"],
        "fBytesPerSecond": dictProgress["fBytesPerSecond"],
        "fSecondsRemaining": dictProgress["fSecondsRemaining"],
        "iAttempt": dictProgress["iAttempt"],
    })


def _fnNoticeASlowRate(dictProgress, dictControl, fnStatus):
    """Say once, without stopping, that the rate has been low for two minutes."""
    fNow = time.monotonic()
    with dictControl["lockLive"]:
        dictLive = dictControl["dictLive"]
        bSlow = 0.0 < dictProgress["fBytesPerSecond"] < _F_SLOW_RATE_BYTES_PER_SECOND
        if not bSlow:
            dictLive["fSlowSinceMonotonic"] = 0.0
            dictLive["bSlowNoticeGiven"] = False
            return
        if not dictLive["fSlowSinceMonotonic"]:
            dictLive["fSlowSinceMonotonic"] = fNow
        bDue = (
            fNow - dictLive["fSlowSinceMonotonic"] >= _F_SLOW_RATE_NOTICE_SECONDS
            and not dictLive["bSlowNoticeGiven"]
        )
        dictLive["bSlowNoticeGiven"] = dictLive["bSlowNoticeGiven"] or bDue
    if bDue:
        fnStatus({
            "sPhase": "downloadSlow",
            "sSentence": (
                "The download has run below 100 kB/s for two minutes. It "
                "continues; Check connection can say whether Zenodo or this "
                "network is the slow side."
            ),
            "fBytesPerSecond": dictProgress["fBytesPerSecond"],
        })


def _fdictDetail(dictAcquisition, dictControl, sTechnicalDetail):
    """The facts every refusal carries beside its sentence."""
    dictProgress = fdictReadDownloadProgress(dictControl)
    return {
        "sAcquisitionId": dictAcquisition["sAcquisitionId"],
        "iBytesOnDisk": downloadPartial.fiBytesOnDisk(dictAcquisition),
        "iExpectedBytes": int(dictAcquisition["dictSidecar"].get("iExpectedBytes") or 0),
        "iAttempt": dictProgress["iAttempt"],
        "sTechnicalDetail": sTechnicalDetail,
    }


# ---------------------------------------------------------------------
# Sleep prevention for the download's duration.
# ---------------------------------------------------------------------


def _fsKeepAliveName(sAcquisitionId):
    """A registry name no container can have, so no container sweep stops it."""
    return "download@" + sAcquisitionId


def _fnHoldSleepPrevention(sAcquisitionId):
    """Keep the machine awake while the download runs; a closed lid drops it.

    The keep-alive exits with this process (``-w``), so a hub that
    crashes mid-download does not leave the laptop awake for nothing.
    """
    if not keepAliveManager.fbPlatformSupportsKeepAlive():
        return
    try:
        keepAliveManager.fnStartKeepAlive(
            _fsKeepAliveName(sAcquisitionId), bExitWithThisProcess=True,
        )
    except OSError as error:
        logger.warning("Sleep prevention could not be held for the download: %s", error)


def _fnReleaseSleepPrevention(sAcquisitionId):
    if not keepAliveManager.fbPlatformSupportsKeepAlive():
        return
    try:
        keepAliveManager.fnStopKeepAlive(_fsKeepAliveName(sAcquisitionId))
    except OSError as error:
        logger.warning("Sleep prevention could not be released: %s", error)
