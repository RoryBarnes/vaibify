"""A fake Docker daemon API that really executes the programs it is handed.

The confined container write execs a fixed program with the file's bytes on
its standard input. A test double that merely recorded the call would agree
with the code by construction, so this harness answers ``exec_create`` /
``exec_start(socket=True)`` / ``exec_inspect`` the way a daemon does: it
hands the caller one end of a real socket pair, reads everything the caller
sends until the write side is half-closed, runs the program in a real
subprocess, and answers with the daemon's multiplexed frame format and the
real exit code. "Container paths" are ordinary absolute paths on the test
machine, so the program's symlink and mode behavior is the real behavior.

``fnBeforeRun`` is the deterministic race hook: it fires after the caller
has composed and sent its request and before the program starts, which is
exactly the window a lexical check cannot protect.
"""

import socket
import struct
import subprocess
import sys
import threading

I_FRAME_STDOUT = 1
I_FRAME_STDERR = 2


class ExecProgramDaemon:
    """The ``client.api`` surface the confined write drives."""

    def __init__(self, bExecute=True, fnBeforeRun=None):
        self.bExecute = bExecute
        self.fnBeforeRun = fnBeforeRun
        self.listExecCreateKeywords = []
        self.listReceivedStdin = []
        self.dictExitCodes = {}
        self.listThreads = []
        self._iNext = 0

    def exec_create(self, sContainerId, **dictKeywords):
        self.listExecCreateKeywords.append(dict(dictKeywords))
        self._iNext += 1
        return {"Id": "harnessExec%02d" % self._iNext}

    def exec_start(self, sExecId, socket=False, **dictKeywords):
        assert socket, "the confined write must use the hijacked socket"
        listCommand = self.listExecCreateKeywords[-1]["cmd"]
        socketNear, socketFar = _fsocketPair()
        threadDaemon = threading.Thread(
            target=self._fnServe,
            args=(sExecId, listCommand, socketFar), daemon=True,
        )
        threadDaemon.start()
        self.listThreads.append(threadDaemon)
        return socketNear

    def exec_inspect(self, sExecId):
        for threadDaemon in self.listThreads:
            threadDaemon.join(timeout=60)
        return {"ExitCode": self.dictExitCodes.get(sExecId, 0),
                "Running": False}

    def _fnServe(self, sExecId, listCommand, socketFar):
        try:
            self._fnServeUntilClosed(sExecId, listCommand, socketFar)
        finally:
            socketFar.close()

    def _fnServeUntilClosed(self, sExecId, listCommand, socketFar):
        socketFar.settimeout(30)
        baStdin = _fbaReadUntilHalfClose(socketFar)
        self.listReceivedStdin.append(baStdin)
        if self.fnBeforeRun is not None:
            self.fnBeforeRun()
        if not self.bExecute:
            self.dictExitCodes[sExecId] = 0
            return
        resultProc = subprocess.run(
            [sys.executable] + listCommand[1:], input=baStdin,
            capture_output=True,
        )
        self.dictExitCodes[sExecId] = resultProc.returncode
        for iStream, baChunk in (
            (I_FRAME_STDOUT, resultProc.stdout),
            (I_FRAME_STDERR, resultProc.stderr),
        ):
            if baChunk:
                socketFar.sendall(
                    struct.pack(">BxxxL", iStream, len(baChunk)) + baChunk)


def _fsocketPair():
    return socket.socketpair()


def _fbaReadUntilHalfClose(socketFar):
    listChunks = []
    while True:
        baChunk = socketFar.recv(1 << 16)
        if not baChunk:
            return b"".join(listChunks)
        listChunks.append(baChunk)
