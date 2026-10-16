"""Check the connection to the archive, bounded, and say what was observed.

A researcher watching a download crawl, or reading why it stopped, has
one question vaibify can help with: is it this computer, the network,
or Zenodo? This module answers with OBSERVATIONS, then with what might
explain them, never with a verdict the probes cannot establish. Three
probes, in order, over the download's own transport
(:mod:`rangedHttpTransport`), with no credentials offered:

1. reach ``doi.org``, to tell "offline" from "Zenodo is unreachable".
   It runs FIRST, under its own short bound: the Zenodo probe waits as
   long as the download does for a slow handshake, and run second the
   resolver was the probe the budget ran out on -- exactly when its
   answer was the one that told the two apart;
2. connect to the recorded service and request the file from the
   download's current offset, timing the connection and the first byte
   (before the deposit's file has been located, the service's front
   page);
3. read at most :data:`I_PROBE_BYTES_CAP` of the body for at most
   :data:`F_THROUGHPUT_PROBE_SECONDS`, measuring the rate -- the cap is
   on the bytes READ, so a server that ignores ``Range`` cannot stream
   the whole file into a check.

The whole check is bounded by :data:`F_CHECK_BUDGET_SECONDS` through
the download's own Cancel path: a timer requests cancel at the deadline,
which shuts the live socket so a blocked read or a hanging connection
returns at once. The check contacts only the recorded service and the
DOI resolver; the file URL comes from the download's sidecar, written by
vaibify itself, and every hop is still checked against the allowlist by
the transport.
"""

__all__ = [
    "F_CHECK_BUDGET_SECONDS",
    "F_THROUGHPUT_PROBE_SECONDS",
    "I_PROBE_BYTES_CAP",
    "fdictProbeArchiveConnection",
]

import socket
import threading
import time
from urllib.parse import urlsplit

from vaibify.reproducibility import rangedHttpTransport, resumableDownload
from vaibify.reproducibility import zenodoClient

F_CHECK_BUDGET_SECONDS = 45.0
F_THROUGHPUT_PROBE_SECONDS = 15.0
I_PROBE_BYTES_CAP = 1024 * 1024
_I_READ_BLOCK_BYTES = 64 * 1024
_F_RESOLVER_PROBE_SECONDS = 10.0
_F_SLOW_START_SECONDS = 5.0
_F_SLOW_RATE_BYTES_PER_SECOND = 100_000.0
_S_DOI_PROBE_URL = zenodoClient.S_DOI_RESOLVER_BASE

S_OUTCOME_REACHED = "reached"
S_OUTCOME_STALLED = "stalled"
S_OUTCOME_DROPPED = "dropped"
S_OUTCOME_OUT_OF_TIME = "out-of-time"
S_OUTCOME_NOT_CHECKED = "not-checked"


def fdictProbeArchiveConnection(dictSidecar):
    """Run the three probes against the download the sidecar describes.

    Returns ``{"sServiceHost", "listObservations", "listExplanations",
    "dictMeasured"}``. Never raises for a network outcome: an
    unreachable host is an observation, not an error.
    """
    fStarted = time.monotonic()
    dictMeasured = _fdictEmptyMeasurement(dictSidecar)
    dictResolverControl, threadResolverDeadline = _ftStartBoundedControl(
        _F_RESOLVER_PROBE_SECONDS,
    )
    try:
        _fnProbeTheResolver(dictResolverControl, dictMeasured)
    finally:
        threadResolverDeadline.cancel()
    dictControl, threadDeadline = _ftStartBoundedControl(
        F_CHECK_BUDGET_SECONDS - (time.monotonic() - fStarted),
    )
    try:
        _fnProbeTheFile(dictSidecar, dictControl, dictMeasured)
    finally:
        threadDeadline.cancel()
    dictMeasured["fSecondsTaken"] = time.monotonic() - fStarted
    dictMeasured["bOutOfTime"] = dictControl["eventCancel"].is_set()
    return {
        "sServiceHost": dictMeasured["sServiceHost"],
        "listObservations": _flistObservations(dictMeasured),
        "listExplanations": _flistExplanations(dictMeasured),
        "dictMeasured": dictMeasured,
    }


def _ftStartBoundedControl(fSeconds):
    """Return a fresh control and the started timer that cancels it.

    Each probe group gets its own: the resolver's short bound must end
    the resolver probe without cancelling the Zenodo probe after it.
    """
    dictControl = resumableDownload.fdictCreateDownloadControl(
        {"sKind": "connection-check"},
    )
    threadDeadline = threading.Timer(
        max(0.0, fSeconds), resumableDownload.fnRequestCancel,
        args=(dictControl,),
    )
    threadDeadline.daemon = True
    threadDeadline.start()
    return dictControl, threadDeadline


def _fdictEmptyMeasurement(dictSidecar):
    """The measurement record before any probe ran."""
    sBaseUrl = _fsServiceBaseUrl(dictSidecar)
    return {
        "sServiceBaseUrl": sBaseUrl,
        "sServiceHost": str(urlsplit(sBaseUrl).hostname or ""),
        "sFileUrl": str(dictSidecar.get("sFileUrl") or ""),
        "iOffset": int(dictSidecar.get("iDurableBytes") or 0),
        "iExpectedBytes": int(dictSidecar.get("iExpectedBytes") or 0),
        "sServiceOutcome": S_OUTCOME_NOT_CHECKED,
        "sServiceDetail": "",
        "iStatus": 0,
        "sReason": "",
        "sContentType": "",
        "fRetryAfterSeconds": 0.0,
        "fSecondsToHeaders": 0.0,
        "fSecondsToFirstByte": 0.0,
        "iBytesRead": 0,
        "fBytesPerSecond": 0.0,
        "sResolverOutcome": S_OUTCOME_NOT_CHECKED,
        "fSecondsTaken": 0.0,
        "bOutOfTime": False,
    }


def _fsServiceBaseUrl(dictSidecar):
    """The recorded service's base URL; the DOI's prefix for an old record."""
    sService = str(dictSidecar.get("sZenodoService") or "").strip()
    if not sService:
        sService = zenodoClient.fsServiceForDoi(
            str(dictSidecar.get("sVersionDoi") or ""),
        )
    return zenodoClient.fsResolveServiceBaseUrl(sService)


# ---------------------------------------------------------------------
# Probes 2 and 3: the service and the file.
# ---------------------------------------------------------------------


def _fnProbeTheFile(dictSidecar, dictControl, dictMeasured):
    """Connect, request the file from the offset, and measure the body."""
    del dictSidecar
    sUrl = dictMeasured["sFileUrl"] or dictMeasured["sServiceBaseUrl"] + "/"
    iOffset = dictMeasured["iOffset"] if dictMeasured["sFileUrl"] else 0
    fRequested = time.monotonic()
    try:
        dictAnswer = rangedHttpTransport.fdictOpenRangedGet(
            sUrl, iOffset, zenodoClient.flistAllowedZenodoOrigins(),
            dictControl, F_THROUGHPUT_PROBE_SECONDS,
        )
    except rangedHttpTransport.CancelledDuringTransport:
        dictMeasured["sServiceOutcome"] = S_OUTCOME_OUT_OF_TIME
        return
    except rangedHttpTransport.TransportFailure as error:
        dictMeasured["sServiceOutcome"] = error.sFailureClass
        dictMeasured["sServiceDetail"] = str(error)
        return
    except (OSError, ValueError) as error:
        dictMeasured["sServiceOutcome"] = S_OUTCOME_DROPPED
        dictMeasured["sServiceDetail"] = f"{type(error).__name__}: {error}"
        return
    dictMeasured["fSecondsToHeaders"] = time.monotonic() - fRequested
    _fnRecordTheAnswer(dictAnswer, dictMeasured)
    try:
        _fnMeasureTheBody(dictAnswer, dictControl, dictMeasured, fRequested)
    finally:
        rangedHttpTransport.fnCloseAnswer(dictAnswer)


def _fnRecordTheAnswer(dictAnswer, dictMeasured):
    """Keep the status line and the headers the sentences read."""
    dictHeaders = dictAnswer["dictHeaders"]
    dictMeasured.update({
        "sServiceOutcome": S_OUTCOME_REACHED,
        "iStatus": int(dictAnswer["iStatus"]),
        "sReason": str(dictAnswer["httpresponseAnswer"].reason or ""),
        "sContentType": str(dictHeaders.get("content-type") or "").lower(),
        "fRetryAfterSeconds": resumableDownload.ffRetryAfterSeconds(
            dictHeaders.get("retry-after"),
        ),
    })


def _fnMeasureTheBody(dictAnswer, dictControl, dictMeasured, fRequested):
    """Read up to the cap, for up to the probe window; record the rate.

    The cap is enforced on the bytes read, never trusted from a header,
    so a server that ignores ``Range`` and streams the whole file is
    cut off at the same megabyte as one that honors it.
    """
    httpresponseAnswer = dictAnswer["httpresponseAnswer"]
    fProbeEnds = time.monotonic() + F_THROUGHPUT_PROBE_SECONDS
    fFirstByte = 0.0
    fLastByte = 0.0
    iBytesRead = 0
    while iBytesRead < I_PROBE_BYTES_CAP and time.monotonic() < fProbeEnds:
        if dictControl["eventCancel"].is_set():
            dictMeasured["sServiceOutcome"] = S_OUTCOME_OUT_OF_TIME
            break
        try:
            baBlock = httpresponseAnswer.read1(
                min(_I_READ_BLOCK_BYTES, I_PROBE_BYTES_CAP - iBytesRead),
            )
        except socket.timeout:
            if iBytesRead == 0:
                dictMeasured["sServiceOutcome"] = S_OUTCOME_STALLED
            break
        except (OSError, ValueError) as error:
            if dictControl["eventCancel"].is_set():
                dictMeasured["sServiceOutcome"] = S_OUTCOME_OUT_OF_TIME
            elif iBytesRead == 0:
                dictMeasured["sServiceOutcome"] = S_OUTCOME_DROPPED
                dictMeasured["sServiceDetail"] = f"{type(error).__name__}: {error}"
            break
        if not baBlock:
            break
        fLastByte = time.monotonic()
        if not fFirstByte:
            fFirstByte = fLastByte
        iBytesRead += len(baBlock)
    dictMeasured["iBytesRead"] = iBytesRead
    if fFirstByte:
        dictMeasured["fSecondsToFirstByte"] = fFirstByte - fRequested
    if iBytesRead and fLastByte > fFirstByte:
        dictMeasured["fBytesPerSecond"] = iBytesRead / (fLastByte - fFirstByte)


# ---------------------------------------------------------------------
# Probe 1: the DOI resolver.
# ---------------------------------------------------------------------


def _fnProbeTheResolver(dictControl, dictMeasured):
    """Reach ``doi.org``; any answer counts, a redirect included.

    The transport follows redirects only inside its allowlist and the
    resolver's front page redirects to another host, so a refused hop
    means the resolver ANSWERED -- which is all this probe asks.
    """
    if dictControl["eventCancel"].is_set():
        dictMeasured["sResolverOutcome"] = S_OUTCOME_OUT_OF_TIME
        return
    try:
        dictAnswer = rangedHttpTransport.fdictOpenRangedGet(
            _S_DOI_PROBE_URL, 0, [zenodoClient.fsOriginOfUrl(_S_DOI_PROBE_URL)],
            dictControl, _F_RESOLVER_PROBE_SECONDS,
        )
    except rangedHttpTransport.CancelledDuringTransport:
        dictMeasured["sResolverOutcome"] = S_OUTCOME_OUT_OF_TIME
        return
    except rangedHttpTransport.TransportFailure as error:
        dictMeasured["sResolverOutcome"] = (
            S_OUTCOME_REACHED
            if error.sFailureClass == rangedHttpTransport.S_FAILURE_REFUSED_HOST
            else error.sFailureClass
        )
        return
    except (OSError, ValueError):
        dictMeasured["sResolverOutcome"] = S_OUTCOME_DROPPED
        return
    rangedHttpTransport.fnCloseAnswer(dictAnswer)
    dictMeasured["sResolverOutcome"] = S_OUTCOME_REACHED


# ---------------------------------------------------------------------
# What was observed, then what might explain it.
# ---------------------------------------------------------------------


def _fsServiceName(dictMeasured):
    """"Zenodo" for a Zenodo host; the host itself otherwise."""
    sHost = dictMeasured["sServiceHost"]
    return "Zenodo" if sHost.endswith("zenodo.org") else sHost


def _flistObservations(dictMeasured):
    """One sentence per thing the probes saw, in the order they saw it."""
    listSentences = []
    sOutcome = dictMeasured["sServiceOutcome"]
    sHost = dictMeasured["sServiceHost"]
    if sOutcome == S_OUTCOME_REACHED:
        listSentences.extend(_flistReachedSentences(dictMeasured))
    elif sOutcome == S_OUTCOME_STALLED:
        listSentences.append(
            f"Reached {sHost}, but it sent no data for "
            f"{F_THROUGHPUT_PROBE_SECONDS:.0f} s after answering."
        )
    elif sOutcome == S_OUTCOME_DROPPED:
        listSentences.append(
            f"Reached {sHost}, but the connection was closed by the other "
            f"side ({dictMeasured['sServiceDetail']})."
        )
    elif sOutcome == rangedHttpTransport.S_FAILURE_TLS:
        listSentences.append(
            f"A secure connection to {sHost} could not be made "
            f"({dictMeasured['sServiceDetail']})."
        )
    elif sOutcome == S_OUTCOME_OUT_OF_TIME:
        listSentences.append(
            f"{sHost} had not answered when the check reached its "
            f"{F_CHECK_BUDGET_SECONDS:.0f} s limit."
        )
    else:
        listSentences.append(
            f"Could not reach {sHost} ({dictMeasured['sServiceDetail']})."
        )
    listSentences.append(_fsResolverSentence(dictMeasured))
    if dictMeasured["bOutOfTime"]:
        listSentences.append(
            f"The check stopped at its {F_CHECK_BUDGET_SECONDS:.0f} s limit."
        )
    return [sSentence for sSentence in listSentences if sSentence]


def _flistReachedSentences(dictMeasured):
    """The sentences for a service that answered the request."""
    sName = _fsServiceName(dictMeasured)
    iStatus = dictMeasured["iStatus"]
    listSentences = [f"Reached {dictMeasured['sServiceHost']}."]
    if "text/html" in dictMeasured["sContentType"]:
        listSentences.append(
            f"{sName} returned a web page instead of the file (perhaps a "
            "network login page or a bot check)."
        )
        return listSentences
    if dictMeasured["fRetryAfterSeconds"] > 0:
        listSentences.append(
            f"{sName} answered '{dictMeasured['sReason'].lower()}' and asked "
            f"vaibify to wait {dictMeasured['fRetryAfterSeconds']:.0f} s."
        )
        return listSentences
    if iStatus not in (200, 206):
        listSentences.append(
            f"{sName} answered {iStatus} {dictMeasured['sReason']} for the file."
        )
        return listSentences
    if dictMeasured["iBytesRead"] == 0:
        listSentences.append(
            f"It answered but sent no data within "
            f"{F_THROUGHPUT_PROBE_SECONDS:.0f} s."
        )
        return listSentences
    listSentences.append(_fsThroughputSentence(dictMeasured))
    sRemaining = _fsRemainingSentence(dictMeasured)
    if sRemaining:
        listSentences.append(sRemaining)
    return listSentences


def _fsThroughputSentence(dictMeasured):
    sStart = f"It took {dictMeasured['fSecondsToFirstByte']:.0f} s to start sending"
    if dictMeasured["fBytesPerSecond"] > 0:
        return (
            f"{sStart} and then sent at "
            f"{dictMeasured['fBytesPerSecond'] / 1e6:.1f} MB/s."
        )
    return f"{sStart}."


def _fsRemainingSentence(dictMeasured):
    """How long the rest would take at the measured rate, or ""."""
    iRemaining = dictMeasured["iExpectedBytes"] - dictMeasured["iOffset"]
    fRate = dictMeasured["fBytesPerSecond"]
    if iRemaining <= 0 or fRate <= 0:
        return ""
    return (
        f"The remaining {resumableDownload.fsDescribeBytes(iRemaining)} would "
        f"take about {_fsDuration(iRemaining / fRate)} at this rate."
    )


def _fsDuration(fSeconds):
    if fSeconds < 90:
        return f"{max(1, round(fSeconds))} s"
    if fSeconds < 5400:
        return f"{round(fSeconds / 60)} minutes"
    return f"{fSeconds / 3600:.1f} hours"


def _fsResolverSentence(dictMeasured):
    sOutcome = dictMeasured["sResolverOutcome"]
    if sOutcome == S_OUTCOME_REACHED:
        return "Reached doi.org."
    if sOutcome == S_OUTCOME_NOT_CHECKED:
        return ""
    if sOutcome == S_OUTCOME_OUT_OF_TIME:
        return (
            f"doi.org did not answer within {_F_RESOLVER_PROBE_SECONDS:.0f} s."
        )
    return "Could not reach doi.org."


def _flistExplanations(dictMeasured):
    """What might explain the observations; empty when nothing stands out."""
    sService = dictMeasured["sServiceOutcome"]
    sResolver = dictMeasured["sResolverOutcome"]
    sName = _fsServiceName(dictMeasured)
    bServiceDown = sService not in (S_OUTCOME_REACHED, S_OUTCOME_STALLED, S_OUTCOME_DROPPED)
    if bServiceDown and sResolver != S_OUTCOME_REACHED:
        return [
            "This usually means this computer is offline, or a network "
            "filter is blocking them."
        ]
    if bServiceDown:
        return [
            f"This computer is online, so the trouble is between here and "
            f"{dictMeasured['sServiceHost']}: {sName} may be down, or a "
            "network filter may block it."
        ]
    listSentences = []
    if "text/html" in dictMeasured["sContentType"]:
        listSentences.append(
            "Open the file's address in a browser on this machine to see "
            "the page it shows."
        )
    if dictMeasured["fRetryAfterSeconds"] > 0:
        listSentences.append(
            f"{sName} is rate-limiting or in maintenance; the download "
            "retries on its own after the wait."
        )
    if dictMeasured["fSecondsToFirstByte"] > _F_SLOW_START_SECONDS:
        listSentences.append(f"Slow starts like this often mean {sName} is busy.")
    if 0 < dictMeasured["fBytesPerSecond"] < _F_SLOW_RATE_BYTES_PER_SECOND:
        listSentences.append(
            "At this rate the download continues, slowly; leaving it "
            "running costs nothing but time."
        )
    if sService in (S_OUTCOME_STALLED, S_OUTCOME_DROPPED):
        listSentences.append(
            f"{sName} accepts connections but is not serving the file "
            "steadily; this is usually load on their side, and the "
            "download's own retries are the remedy."
        )
    return listSentences
