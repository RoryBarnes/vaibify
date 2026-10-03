"""The hijacked exec socket: a program that exits early still gets to say why.

Source: ``dockerConnection._ftExchangeWithExecSocket``.

The tree copy streams a whole archive on a program's stdin. When the
program stops early (a full disk, a refusal), the daemon closes the
connection while the host is still sending, and on Linux that close is a
reset: the host's next ``send`` AND its next ``recv`` raise
``ConnectionResetError``. Reading only after the send finished meant the
reason the program had written to its standard error was never read, and
the copy failed with "Connection reset by peer" instead of "No space left
on device". CI's rootful Linux daemon showed it; this test reproduces it
over a real TCP connection, where the reset is a real one.
"""

import os
import socket
import struct
import threading

import pytest

from vaibify.docker import dockerConnection as dockerConnectionModule

I_FRAME_STDOUT = 1
I_FRAME_STDERR = 2


def _ftTcpPair():
    socketServer = socket.create_server(("127.0.0.1", 0))
    socketClient = socket.create_connection(socketServer.getsockname())
    socketPeer, _ = socketServer.accept()
    socketServer.close()
    return socketClient, socketPeer


def _fbaFrame(iStream, baPayload):
    return struct.pack(">BxxxL", iStream, len(baPayload)) + baPayload


def _fnCloseWithReset(socketPeer):
    socketPeer.setsockopt(
        socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0))
    socketPeer.close()


def testAProgramThatExitsEarlyStillDeliversItsReasonThroughAReset():
    """The reason arrives although the peer reset the connection.

    Whether a reset discards what the peer had already queued depends on
    the platform's TCP stack, so this documents the symptom CI met and
    ``testTheOutputIsReadWhileThePayloadIsStillBeingSent`` is the test
    that holds the property on every platform.
    """
    socketClient, socketPeer = _ftTcpPair()
    sReason = b"failed: OSError: [Errno 28] No space left on device\n"

    def fnPeer():
        # The program says why at once, then stops reading while the host
        # is still sending; the host is blocked in ``send`` by the time
        # the connection is reset.
        try:
            socketPeer.sendall(_fbaFrame(I_FRAME_STDERR, sReason))
            threading.Event().wait(0.5)
        finally:
            _fnCloseWithReset(socketPeer)

    threadPeer = threading.Thread(target=fnPeer, daemon=True)
    threadPeer.start()
    try:
        baStdout, baStderr = dockerConnectionModule._ftExchangeWithExecSocket(
            socketClient, os.urandom(1 << 20) * 96)
    finally:
        socketClient.close()
    threadPeer.join(timeout=30)
    assert baStderr == sReason
    assert baStdout == b""


def testAFileIsStreamedAndTheOutputOfAProgramThatReadItAllIsKept(tmp_path):
    socketClient, socketPeer = _ftTcpPair()
    baPayload = os.urandom(1 << 20) * 3
    listReceived = []

    def fnPeer():
        listChunks = []
        while True:
            baChunk = socketPeer.recv(1 << 16)
            if not baChunk:
                break
            listChunks.append(baChunk)
        listReceived.append(b"".join(listChunks))
        socketPeer.sendall(_fbaFrame(I_FRAME_STDOUT, b"out"))
        socketPeer.sendall(_fbaFrame(I_FRAME_STDERR, b"warn"))
        socketPeer.close()

    threadPeer = threading.Thread(target=fnPeer, daemon=True)
    threadPeer.start()
    pathPayload = tmp_path / "payload.bin"
    pathPayload.write_bytes(baPayload)
    try:
        with open(str(pathPayload), "rb") as filePayload:
            baStdout, baStderr = (
                dockerConnectionModule._ftExchangeWithExecSocket(
                    socketClient, None, fileStdin=filePayload))
    finally:
        socketClient.close()
    threadPeer.join(timeout=30)
    assert listReceived == [baPayload]
    assert (baStdout, baStderr) == (b"out", b"warn")


class _RecordingSocket:
    """A socket whose reads announce themselves; sends go to the real one."""

    def __init__(self, socketReal, eventRead):
        self._sock = socketReal
        self.eventRead = eventRead

    def fileno(self):
        return self._sock.fileno()

    def recv(self, iBytes):
        baData = self._sock.recv(iBytes)
        self.eventRead.set()
        return baData


class _FileThatWaitsForTheFirstRead:
    """A payload whose second chunk is released only once output was read."""

    def __init__(self, eventRead):
        self.eventRead = eventRead
        self.iReads = 0
        self.bOutputWasReadFirst = None

    def read(self, iBytes):
        self.iReads += 1
        if self.iReads == 1:
            return b"first chunk"
        if self.iReads == 2:
            self.bOutputWasReadFirst = self.eventRead.wait(timeout=5)
            return b"second chunk"
        return b""


@pytest.mark.falsification
def testTheOutputIsReadWhileThePayloadIsStillBeingSent():
    """Output is consumed before the send finishes, on any platform.

    The second chunk of the payload is held back until the host has read
    from the socket. A host that reads only after the whole payload is
    sent never does, so the chunk is never released.

    Kills: starting the reader after the send, which loses the reason a
    program gave when it stopped early.
    """
    socketClient, socketPeer = _ftTcpPair()
    eventRead = threading.Event()
    filePayload = _FileThatWaitsForTheFirstRead(eventRead)

    def fnPeer():
        socketPeer.sendall(_fbaFrame(I_FRAME_STDERR, b"early reason\n"))
        while socketPeer.recv(65536):
            pass
        socketPeer.close()

    threadPeer = threading.Thread(target=fnPeer, daemon=True)
    threadPeer.start()
    try:
        _, baStderr = dockerConnectionModule._ftExchangeWithExecSocket(
            _RecordingSocket(socketClient, eventRead), None,
            fileStdin=filePayload)
    finally:
        socketClient.close()
    threadPeer.join(timeout=30)
    assert filePayload.bOutputWasReadFirst is True
    assert baStderr == b"early reason\n"
