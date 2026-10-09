"""What recreating a container discards from its writable layer, in one sentence.

Restart, Rebuild, and switching or re-obtaining an image all remove the
container and create a new one, so whatever lives in the old container's
writable layer -- everything outside its mounted volumes and host
directories, with an agent's scratch files in /tmp foremost -- is
discarded. Every confirmation of such an action, and the CLI's repair
lane, says so with the sentence built here, and names the size of /tmp
when it was measured.

The size comes from a typed read of ``du -sxk /tmp`` (``-x`` keeps the
count to the writable layer's own filesystem). A measurement that did not
happen says why; a timeout is never a size. The CLI repair lane makes no
measurement at all, and its sentence claims neither a size nor a failure.
"""

from vaibify.config.resourceLimits import fsFormatBytes

__all__ = [
    "S_STATE_MEASURED",
    "S_STATE_TIMEOUT",
    "S_STATE_NOT_RUNNING",
    "S_STATE_UNREADABLE",
    "S_STATE_NOT_MEASURED",
    "fiParseTmpBytes",
    "fsDescribeWritableLayerLoss",
]


S_STATE_MEASURED = "measured"
S_STATE_TIMEOUT = "timeout"
S_STATE_NOT_RUNNING = "notRunning"
S_STATE_UNREADABLE = "unreadable"
S_STATE_NOT_MEASURED = "notMeasured"

_S_PRESERVED = "mounted volumes and host directories are preserved."


def fiParseTmpBytes(sDuOutput):
    """Return the bytes ``du -sxk`` reports on its last line, or None.

    ``du`` reports unreadable subdirectories on stderr and still prints
    its total, which is the last line; anything that is not a number of
    kilobytes there means no measurement.
    """
    listLines = [sLine for sLine in (sDuOutput or "").splitlines()
                 if sLine.strip()]
    if not listLines:
        return None
    try:
        return int(listLines[-1].split()[0]) * 1024
    except ValueError:
        return None


def fsDescribeWritableLayerLoss(sState, iTmpBytes=None, sReason=""):
    """Return the sentence every recreate confirmation carries."""
    if sState == S_STATE_MEASURED and iTmpBytes is not None:
        return (
            "Files in the container's writable layer, including "
            f"{fsFormatBytes(iTmpBytes)} in /tmp, are discarded; "
            + _S_PRESERVED)
    if sState == S_STATE_NOT_MEASURED:
        return (
            "Files in the container's writable layer, including /tmp, are "
            "discarded; " + _S_PRESERVED)
    return (
        "Files in the container's writable layer, including /tmp, are "
        "discarded (the size of /tmp could not be measured: "
        f"{_fsReasonNotMeasured(sState, sReason)}); " + _S_PRESERVED)


def _fsReasonNotMeasured(sState, sReason):
    if sState == S_STATE_TIMEOUT:
        return sReason or "the container did not answer in time"
    if sState == S_STATE_NOT_RUNNING:
        return "the container is not running"
    return sReason or "it could not be read"
