"""The resize acknowledgement marks a point in the OUTPUT stream.

xterm re-wraps its buffer the instant it is resized, but the program
in the pane learns its new width only when SIGWINCH arrives. A program
that repaints in place -- cursor up N rows, erase, reprint, which is
what a full-screen agent does dozens of times a second -- therefore
composes a frame for one width and has it painted at another, its
cursor-up lands short, its erase misses, and the old frame survives
above the new one. That is the duplicated text a researcher sees.

The hub closes the window by making the acknowledgement an ORDERING
MARKER rather than a mere reply. Two properties carry it, and both are
asserted here because each is invisible to the other's test:

  1. The ioctl happens in the READ loop, not where the request lands.
     In the input loop it would race the reader: bytes already read
     could still be unsent when the acknowledgement went out.
  2. The reader DRAINS before it acknowledges. Whatever the program
     has already written was composed for the old width, and the read
     loop sleeps between polls, so at that moment the pty still holds
     frames the browser has not seen. Acknowledging first has the
     browser reflow and then paint that old-width text into a
     re-wrapped buffer -- the exact mismatch being prevented.

Property 2 is not theory. Measured on a real pane against a
SIGWINCH-aware repainter at fifty frames a second: acknowledging
before draining left THREE stale frames on screen, indistinguishable
from having no ordering at all; draining first left one. The one that
remains is the program's own model of what it printed, which no
ordering here can reach.
"""

import asyncio
import json

import pytest

from vaibify.gui import pipelineServer


class FakeTerminalSession:
    """A session that records what happened to it, in order."""

    def __init__(self, listEvents, listChunks=()):
        self._listEvents = listEvents
        self._listChunks = list(listChunks)
        self._bRunning = True

    def fbaReadOutput(self):
        if not self._listChunks:
            return b""
        baChunk = self._listChunks.pop(0)
        self._listEvents.append(("read", baChunk))
        return baChunk

    def fnResize(self, iRows, iColumns):
        self._listEvents.append(("ioctl", iRows, iColumns))

    def fnKillForeground(self):
        self._listEvents.append(("kill",))


class FakeWebSocket:
    """A websocket that records the ORDER of what it was sent."""

    def __init__(self, listEvents):
        self._listEvents = listEvents

    async def send_bytes(self, baPayload):
        self._listEvents.append(("sent", baPayload))

    async def send_json(self, dictPayload):
        self._listEvents.append(("acknowledged", dictPayload))


def _ftDriveOneResize(listChunks, dictPendingResize):
    """Run the reader's resize step once; return (events, session)."""
    listEvents = []
    session = FakeTerminalSession(listEvents, listChunks)
    asyncio.get_event_loop_policy().new_event_loop()
    asyncio.run(pipelineServer._fnApplyPendingResizeAndAcknowledge(
        session, FakeWebSocket(listEvents), dictPendingResize,
    ))
    return listEvents, session


@pytest.mark.falsification
def testTheRequestIsQueuedRatherThanAppliedWhereItLands():
    """The input loop must not touch the pty; the reader owns that.
    Kills: `_fnHandleTerminalText` resizing the pty itself instead of
    queueing, i.e. the ioctl back in the input loop where it races
    the reader.
    """
    listEvents = []
    session = FakeTerminalSession(listEvents)
    dictPendingResize = {}
    pipelineServer._fnHandleTerminalText(
        session,
        json.dumps({"sType": "resize", "iRows": 15, "iColumns": 62,
                    "iSequence": 7}),
        dictPendingResize,
    )
    assert ("ioctl", 15, 62) not in listEvents, (
        "the input loop resized the pty itself, so the ioctl no longer "
        "sits at a known point in the output stream and the "
        "acknowledgement orders nothing"
    )
    assert dictPendingResize == {
        "iRows": 15, "iColumns": 62, "iSequence": 7,
    }, f"the request was not queued for the reader: {dictPendingResize}"


@pytest.mark.falsification
def testEveryPendingByteIsForwardedBeforeTheIoctl():
    """The drain is the property; assert it as an ORDER, not a count.

    Asserting merely that the bytes were sent would pass against a
    reader that acknowledged first and forwarded afterwards, which is
    the shipped defect this covers.
    
    Kills: the drain removed from
    `_fnApplyPendingResizeAndAcknowledge`, so the hub acknowledges
    before forwarding what the program already wrote.
    """
    listEvents, _ = _ftDriveOneResize(
        [b"frame-one", b"frame-two"],
        {"iRows": 15, "iColumns": 62, "iSequence": 3},
    )
    listKinds = [tEvent[0] for tEvent in listEvents]
    iIoctl = listKinds.index("ioctl")
    iAcknowledged = listKinds.index("acknowledged")
    listSent = [i for i, sKind in enumerate(listKinds) if sKind == "sent"]

    assert listSent, "nothing was forwarded, so the drain did not run"
    assert max(listSent) < iIoctl, (
        "output composed at the OLD width was still unsent when the "
        "pty was resized, so the browser will paint it into a buffer "
        f"already re-wrapped; order was {listKinds}"
    )
    assert iIoctl < iAcknowledged, (
        "the browser was told to reflow before the pty had actually "
        f"been resized; order was {listKinds}"
    )


def testTheAcknowledgementNamesTheResizeItCompleted():
    """A sequence lets the browser ignore a stale acknowledgement."""
    listEvents, _ = _ftDriveOneResize(
        [], {"iRows": 15, "iColumns": 62, "iSequence": 9},
    )
    listAcks = [tEvent[1] for tEvent in listEvents
                if tEvent[0] == "acknowledged"]
    assert len(listAcks) == 1, f"expected one acknowledgement: {listEvents}"
    assert listAcks[0] == {
        "sType": "resizeApplied", "iSequence": 9,
        "iRows": 15, "iColumns": 62,
    }, listAcks[0]


@pytest.mark.falsification
def testTheSlotIsClearedSoOneRequestResizesOnce():
    """A slot left populated would re-resize on every read.
    Kills: `dictPendingResize.clear()` removed, so one request
    resizes the pty again on every pass of the read loop.
    """
    dictPendingResize = {"iRows": 15, "iColumns": 62, "iSequence": 1}
    listEvents, _ = _ftDriveOneResize([], dictPendingResize)
    assert dictPendingResize == {}, (
        "the request survived being applied, so the reader will resize "
        "the pty again on its next pass"
    )


def testNothingPendingIsSilent():
    """No request must mean no ioctl and no acknowledgement."""
    listEvents, _ = _ftDriveOneResize([b"output"], {})
    assert listEvents == [], (
        "the reader acted on an empty slot; a spurious acknowledgement "
        f"makes the browser reflow for no reason: {listEvents}"
    )


@pytest.mark.parametrize("iRequested,iExpected", [
    (0, 1), (-5, 1), (99999, 500),
])
def testRowCountsAreClampedBeforeTheyReachTheIoctl(iRequested, iExpected):
    """A browser is not trusted to size the pty."""
    dictPendingResize = {}
    pipelineServer._fnHandleTerminalText(
        FakeTerminalSession([]),
        json.dumps({"sType": "resize", "iRows": iRequested,
                    "iColumns": 80}),
        dictPendingResize,
    )
    assert dictPendingResize["iRows"] == iExpected


def testACallerWithNoReaderStillResizes():
    """The legacy path keeps working, and says so by resizing at once.

    Direct library and test callers pass no slot because they have no
    read loop to hand it to. They get no acknowledgement -- and so no
    ordering guarantee -- but they must not silently stop resizing.
    """
    listEvents = []
    pipelineServer._fnHandleTerminalText(
        FakeTerminalSession(listEvents),
        json.dumps({"sType": "resize", "iRows": 15, "iColumns": 62}),
        None,
    )
    assert ("ioctl", 15, 62) in listEvents, listEvents
