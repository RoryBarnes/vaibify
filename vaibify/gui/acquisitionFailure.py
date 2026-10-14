"""One plain description per way obtaining the author's image can fail.

Three lanes show the researcher the same failure -- containerizing a
clone, reproducing a published project, promoting an archive -- and
until 2026-10-13 each showed it differently: a toast with a Python
exception, a card with "crashed: OSError", a modal built for Docker
build output. The window they now share reads ONE record, built here
from whatever the chain raised: what happened in a sentence, what is
held on disk and what each button does to it, whether trying again can
help, and the technical text for diagnosis, collapsed.

No sentence here names a "link of the chain", a "pinned reference", a
"daemon", a Python type, or a ``docker login``. The download's own
refusals already carry a researcher's sentence (``sSentence`` in
``dictFailure``); this module supplies the rest and the fallbacks.
"""

__all__ = [
    "T_FINAL_FAILURE_CLASSES",
    "fdictDescribeAcquisitionFailure",
    "fsDescribeHeldBytes",
]

from vaibify.reproducibility import imageAcquisition, resumableDownload

# Classes nothing can resume from: Try again would repeat the refusal.
T_FINAL_FAILURE_CLASSES = (
    resumableDownload.S_FAILURE_NOT_AVAILABLE,
    resumableDownload.S_FAILURE_DEPOSIT_CHANGED,
    resumableDownload.S_FAILURE_CANCELLED,
)

# Classes whose remedy is on this machine, where the doctor's checks look.
_T_DIAGNOSABLE_CLASSES = (
    imageAcquisition.S_FAILURE_DAEMON_LOAD,
    resumableDownload.S_FAILURE_DISK_FULL,
    resumableDownload.S_FAILURE_NO_ROOM,
    resumableDownload.S_FAILURE_WRITE_FAILED,
    "overlay-build",
)

_S_FAILURE_UNKNOWN = "unknown"


def fsDescribeHeldBytes(dictFailure):
    """Say what is on disk and what each decision does to it."""
    iOnDisk = int(dictFailure.get("iBytesOnDisk") or 0)
    iExpected = int(dictFailure.get("iExpectedBytes") or 0)
    if not dictFailure.get("bPartialKept") or iOnDisk <= 0:
        return "Nothing downloaded is kept."
    sOf = f" of {resumableDownload.fsDescribeBytes(iExpected)}" if iExpected else ""
    return (
        f"{resumableDownload.fsDescribeBytes(iOnDisk)}{sOf} downloaded so far "
        "are kept; Try again continues from there, and giving up deletes them."
    )


def fdictDescribeAcquisitionFailure(error):
    """Return the shared failure record for whatever the acquisition raised."""
    dictFailure = dict(getattr(error, "dictFailure", None) or {})
    sClass = str(dictFailure.get("sFailureClass") or _fsClassOfPlainError(error))
    sSentence = str(dictFailure.get("sSentence") or _fsSentenceForPlainError(error))
    sStderrTail = str(getattr(error, "sStderrTail", "") or "")
    return {
        "sFailureClass": sClass,
        "sSentence": sSentence,
        "sHeld": fsDescribeHeldBytes(dictFailure),
        "bPartialKept": bool(dictFailure.get("bPartialKept")),
        "iBytesOnDisk": int(dictFailure.get("iBytesOnDisk") or 0),
        "iExpectedBytes": int(dictFailure.get("iExpectedBytes") or 0),
        "sAcquisitionId": str(dictFailure.get("sAcquisitionId") or ""),
        "bRetryable": sClass not in T_FINAL_FAILURE_CLASSES,
        "bDiagnosable": sClass in _T_DIAGNOSABLE_CLASSES,
        "sAlsoChecked": _fsAlsoChecked(getattr(error, "listAttempts", None) or []),
        "sTechnicalDetail": "\n".join(filter(None, [str(error), sStderrTail])),
    }


def _fsClassOfPlainError(error):
    """Name the class of a failure that did not come from the download."""
    if getattr(error, "sStderrTail", ""):
        return "overlay-build"
    return _S_FAILURE_UNKNOWN


def _fsSentenceForPlainError(error):
    """A sentence for a failure outside the download: the error's own words.

    Refusals from the acquisition lane are written for the researcher
    already (an unprovable baseline, a mismatched pin); an exception
    that is not one is reported by what it did, never by its type.
    """
    sText = str(error).strip()
    if getattr(error, "sStderrTail", ""):
        return (
            "Adding the agents on top of the author's image failed. "
            + sText
        )
    if isinstance(error, (
        imageAcquisition.ImageAcquisitionRefusedError,
    )) or type(error).__name__.endswith("RefusedError"):
        return sText or "Obtaining the author's environment was refused."
    return (
        "Obtaining the author's environment failed before it finished"
        + (f": {sText}" if sText else ".")
    )


def _fsAlsoChecked(listAttempts):
    """Name the other sources the chain tried, as facts, after the cause.

    An archived image is on no public registry and on no fresh machine,
    so those two misses are the chain working; they are stated last and
    without Docker's words, which read as errors (2026-10-13).
    """
    listSentences = []
    for dictAttempt in listAttempts:
        if dictAttempt.get("bSucceeded"):
            continue
        if dictAttempt.get("sLink") == imageAcquisition.S_LINK_REGISTRY:
            listSentences.append(
                "no public image registry has this image (expected for an "
                "archived image)"
            )
        elif dictAttempt.get("sLink") == imageAcquisition.S_LINK_LOCAL:
            listSentences.append("no copy of it is on this machine")
    if not listSentences:
        return ""
    return "Also checked: " + "; ".join(listSentences) + "."
