"""A council campaign's event log on disk, so its Agent tabs survive a restart.

The in-memory ring (``agentCouncilStore.CouncilEventRing``) stays the
poll's window -- 500 events and 1 MiB -- and this file is everything:
``events.jsonl`` beside ``campaign.json``, mode 600, one redacted JSON
event per line, appended as each event is recorded. It is always kept;
nothing is ever rebuilt from ``campaign.json``, which holds settled turn
results and not the streamed activity the Agent tabs show.

``iSequence`` continues across a restart: on reload the last complete
line's sequence is recovered and the ring numbers on from it. A final
line that was torn by a crash (no newline, or not JSON) is truncated
away and the truncation logged, so a torn write can never be read as an
event.

Paging back is by BYTE cursor, never by sequence: each page reads fixed
size blocks backwards from the cursor until it has enough complete
lines, so every page costs a bounded read however old the events it
reaches.
"""

__all__ = [
    "I_PAGE_BLOCK_BYTES",
    "S_EVENT_LOG_BASENAME",
    "fdictReadEarlierEvents",
    "fiRecoverEventLog",
    "flistReadTailEvents",
    "fnAppendEventToLog",
    "fsEventLogPath",
]

import json
import logging
import os

logger = logging.getLogger("vaibify")

S_EVENT_LOG_BASENAME = "events.jsonl"
I_PAGE_BLOCK_BYTES = 65536
I_MAXIMUM_PAGE_EVENTS = 500
_I_FILE_MODE_PRIVATE = 0o600


def fsEventLogPath(sCampaignDirectory):
    """Return the campaign's event log path."""
    return os.path.join(sCampaignDirectory, S_EVENT_LOG_BASENAME)


def _fdictParseLine(baLine):
    try:
        dictEvent = json.loads(baLine.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return None
    if not isinstance(dictEvent, dict) or not isinstance(
            dictEvent.get("iSequence"), int):
        return None
    return dictEvent


def _fiStartOfLastLine(fileLog, iSize):
    """Return the offset where the file's final line begins."""
    iCursor = iSize
    while iCursor > 0:
        iStart = max(0, iCursor - I_PAGE_BLOCK_BYTES)
        fileLog.seek(iStart)
        baBlock = fileLog.read(iCursor - iStart)
        iNewline = baBlock.rfind(b"\n", 0, len(baBlock) - (
            1 if iCursor == iSize else 0))
        if iNewline >= 0:
            return iStart + iNewline + 1
        iCursor = iStart
    return 0


def fiRecoverEventLog(sPath):
    """Return the last complete event's sequence; truncate a torn tail.

    0 when there is no log. A final line with no newline, or one that
    does not parse, is cut away -- only the final line can have been
    torn, because every earlier one was followed by another append.
    """
    try:
        fileLog = open(sPath, "r+b")
    except FileNotFoundError:
        return 0
    with fileLog:
        iSize = fileLog.seek(0, os.SEEK_END)
        while iSize > 0:
            iStart = _fiStartOfLastLine(fileLog, iSize)
            fileLog.seek(iStart)
            baLine = fileLog.read(iSize - iStart)
            dictEvent = _fdictParseLine(baLine.rstrip(b"\n")) if baLine.endswith(
                b"\n") else None
            if dictEvent is not None:
                return dictEvent["iSequence"]
            logger.warning(
                "Council event log %s ended in a torn line (%d bytes); "
                "truncated it", sPath, iSize - iStart)
            fileLog.truncate(iStart)
            iSize = iStart
    return 0


def fnAppendEventToLog(sPath, dictEvent):
    """Append one already-redacted, sequence-stamped event as a line."""
    baLine = (json.dumps(dictEvent, sort_keys=True) + "\n").encode("utf-8")
    iDescriptor = os.open(
        sPath, os.O_WRONLY | os.O_APPEND | os.O_CREAT, _I_FILE_MODE_PRIVATE)
    try:
        os.write(iDescriptor, baLine)
    finally:
        os.close(iDescriptor)


def _ftReadLinesBackward(fileLog, iBeforeOffset, iMaximumEvents):
    """Collect up to N complete events ending before an offset.

    Returns ``(listEvents oldest-first, iEarliestOffset)``. Reads at
    most enough blocks to gather the events, never the whole file.
    """
    listEvents = []
    iCursor = iBeforeOffset
    baCarry = b""
    while iCursor > 0 and len(listEvents) < iMaximumEvents:
        iStart = max(0, iCursor - I_PAGE_BLOCK_BYTES)
        fileLog.seek(iStart)
        baBlock = fileLog.read(iCursor - iStart) + baCarry
        listLines = baBlock.split(b"\n")
        baCarry = listLines.pop(0) if iStart > 0 else b""
        iOffset = iStart + len(baCarry) + (1 if iStart > 0 else 0)
        listOffsets = []
        for baLine in listLines:
            listOffsets.append(iOffset)
            iOffset += len(baLine) + 1
        for baLine, iLineOffset in reversed(list(zip(listLines, listOffsets))):
            dictEvent = _fdictParseLine(baLine) if baLine else None
            if dictEvent is not None:
                listEvents.append((iLineOffset, dictEvent))
                if len(listEvents) >= iMaximumEvents:
                    break
        iCursor = iStart
    listEvents.reverse()
    iEarliest = listEvents[0][0] if listEvents else 0
    return [dictEvent for _, dictEvent in listEvents], iEarliest


def fdictReadEarlierEvents(sPath, iBeforeOffset, iMaximumEvents):
    """Return one page of events ending before a byte cursor.

    ``iBeforeOffset`` < 0 means the end of the file. The answer names
    the cursor for the next page back and whether the start was reached.
    """
    iMaximumEvents = max(1, min(int(iMaximumEvents), I_MAXIMUM_PAGE_EVENTS))
    try:
        fileLog = open(sPath, "rb")
    except FileNotFoundError:
        return {"listEvents": [], "iNextBeforeOffset": 0,
                "bStartReached": True}
    with fileLog:
        iSize = fileLog.seek(0, os.SEEK_END)
        iBefore = iSize if iBeforeOffset < 0 else min(iBeforeOffset, iSize)
        listEvents, iEarliest = _ftReadLinesBackward(
            fileLog, iBefore, iMaximumEvents)
    return {"listEvents": listEvents, "iNextBeforeOffset": iEarliest,
            "bStartReached": iEarliest == 0}


def flistReadTailEvents(sPath, iMaximumEvents):
    """Return the newest N events, oldest first (the ring's reload seed)."""
    return fdictReadEarlierEvents(sPath, -1, iMaximumEvents)["listEvents"]
