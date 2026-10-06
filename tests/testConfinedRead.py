"""The container's confined reads: containment, races, and exit status.

Source: ``vaibify/docker/confinedRead.py`` and
``DockerConnection.fiterReadFileConfined`` / ``fiterReadDirectoryAsTar``.

The programs run in a real subprocess against a real directory tree
(``tests/confinedReadScenarios.py``); the connection is driven over a fake
daemon that speaks the real exec framing on a real socket pair
(``tests/confinedWriteHarness.py``). The behaviour shared with the host
leg is pinned in ``tests/testConfinedReadParity.py``.
"""

import io
import os
import struct
import subprocess
import sys
from types import SimpleNamespace

import pytest

from tests.confinedReadScenarios import (
    ContainerLeg,
    fdictBuildProject,
    fnWriteFile,
    fsRealPath,
)
from tests.confinedWriteHarness import (
    ExecProgramDaemon,
    I_FRAME_STDERR,
    I_FRAME_STDOUT,
    _fbaReadUntilHalfClose,
)
from tests.testConfinedContainerWrite import _fconnectionOverDaemon
from vaibify.config import mutationAdmission
from vaibify.docker import confinedRead
from vaibify.docker import dockerConnection as dockerConnectionModule
from vaibify.docker.confinedRead import (
    ContainerReadRefusedError,
    fsRenderConfinedReadProgram,
)


@pytest.fixture()
def dictProject(tmp_path):
    return fdictBuildProject(tmp_path)


# ---------------------------------------------------------------------
# The file program
# ---------------------------------------------------------------------


@pytest.mark.falsification
def testALinkLeadingOutOfTheRootIsRefusedAndNoOutsideByteIsSent(dictProject):
    """A link whose target is outside the root is refused, not followed.

    Kills: making the root-containment test always pass, which follows a
    link planted by the in-container agent to any file the user can read.
    """
    sOutcome, objAnswer = ContainerLeg.ftReadFile(
        dictProject["sRoot"], dictProject["sRoot"] + "/escapeLink")
    assert sOutcome == "refused", objAnswer
    assert "secret" not in str(objAnswer).replace("secret.txt", "")
    assert "escapeLink" in objAnswer and "secret.txt" in objAnswer


@pytest.mark.falsification
def testASymlinkedDirectoryInThePathIsRefusedAndNothingIsSent(dictProject):
    """A symlinked intermediate component is refused.

    Kills: dropping ``O_NOFOLLOW`` from the directory walk, which follows
    a link planted in place of a parent directory.
    """
    sOutcome, objAnswer = ContainerLeg.ftReadFile(
        dictProject["sRoot"],
        dictProject["sRoot"] + "/linkedDir/nested/file.txt")
    assert sOutcome == "refused", objAnswer
    assert b"nested" not in (objAnswer if isinstance(objAnswer, bytes) else b"")


@pytest.mark.falsification
def testAComponentSwappedAfterTheCheckCannotRedirectTheRead(
    tmp_path, monkeypatch,
):
    """The race closes by construction: descriptors, not names.

    The program runs in-process, and the moment the walk has descended
    into ``inner`` that directory is moved away and a symlink to an
    outside directory takes its name. A read that re-resolved the NAME
    would return the outside bytes; this one reads from the directory it
    holds a descriptor for.

    Kills: opening the file by its full path name instead of relative to
    the descriptor already held.
    """
    sRoot = fsRealPath(tmp_path) + "/project"
    sOutside = fsRealPath(tmp_path) + "/outside"
    fnWriteFile(sRoot + "/inner/data.txt", b"inside")
    fnWriteFile(sOutside + "/data.txt", b"outside")
    sProgram = fsRenderConfinedReadProgram(sRoot + "/inner/data.txt", sRoot)
    fnRealOpen = os.open
    dictState = {"bSwapped": False}

    def fiOpenThenSwap(sName, iFlags, *tArguments, **dictKeywords):
        iDescriptor = fnRealOpen(sName, iFlags, *tArguments, **dictKeywords)
        if sName == "inner" and not dictState["bSwapped"]:
            dictState["bSwapped"] = True
            os.rename(sRoot + "/inner", sRoot + "/moved")
            os.symlink(sOutside, sRoot + "/inner")
        return iDescriptor

    bufferOut = io.BytesIO()
    monkeypatch.setattr(os, "open", fiOpenThenSwap)
    monkeypatch.setattr(sys, "stdout", SimpleNamespace(buffer=bufferOut))
    exec(compile(sProgram, "<confined-read>", "exec"), {"__name__": "x"})
    monkeypatch.undo()
    assert dictState["bSwapped"]
    assert bufferOut.getvalue() == b"inside"


@pytest.mark.falsification
def testAFifoIsRefusedAndTheReadNeverBlocksWaitingForAWriter(dictProject):
    """Opening a FIFO for reading blocks until someone writes to it.

    Kills: opening without ``O_NONBLOCK``, which hangs on a FIFO an agent
    left in the project (the subprocess timeout turns the hang into a
    failing test).
    """
    sProgram = fsRenderConfinedReadProgram(
        dictProject["sRoot"] + "/fifo", dictProject["sRoot"])
    resultProc = subprocess.run(
        [sys.executable, "-c", sProgram], capture_output=True, timeout=10)
    assert resultProc.returncode == confinedRead.I_REFUSED_EXIT_CODE
    assert b"not a regular file" in resultProc.stderr


def testAFileStreamsInChunksNotAllAtOnce(dictProject, monkeypatch):
    """No single write to stdout exceeds the chunk size."""
    sProgram = fsRenderConfinedReadProgram(
        dictProject["sRoot"] + "/big.bin", dictProject["sRoot"])

    class _RecordingBuffer(io.BytesIO):
        listWriteSizes = []

        def write(self, baChunk):
            self.listWriteSizes.append(len(baChunk))
            return super().write(baChunk)

    bufferOut = _RecordingBuffer()
    monkeypatch.setattr(sys, "stdout", SimpleNamespace(buffer=bufferOut))
    exec(compile(sProgram, "<confined-read>", "exec"), {"__name__": "x"})
    assert max(bufferOut.listWriteSizes) <= 1 << 20
    assert len(bufferOut.listWriteSizes) >= 3


# ---------------------------------------------------------------------
# The archive program
# ---------------------------------------------------------------------


@pytest.mark.falsification
def testAnArchiveStoresALinkAsALinkAndNeverFollowsIt(dictProject):
    """Kills: ``os.stat(..., follow_symlinks=True)`` in the walk, which
    reads a link as whatever it points at (a directory is descended into,
    a file silently becomes a skipped entry).
    """
    import tarfile
    sOutcome, baArchive = ContainerLeg.ftReadArchive(
        dictProject["sRoot"], dictProject["sRoot"])
    assert sOutcome == "ok", baArchive
    with tarfile.open(fileobj=io.BytesIO(baArchive)) as tarIn:
        dictMembers = {infoMember.name: infoMember for infoMember in tarIn}
    assert dictMembers["project/outsideDirLink"].issym()
    assert dictMembers["project/linkToPlain"].issym()
    assert not [sName for sName in dictMembers
                if sName.startswith("project/outsideDirLink/")]


@pytest.mark.falsification
def testASpecialFileInATreeIsSkippedAndCounted(dictProject):
    """Kills: not counting what the archive leaves out."""
    sProgram = confinedRead.fsRenderConfinedArchiveProgram(
        dictProject["sRoot"], dictProject["sRoot"])
    resultProc = subprocess.run(
        [sys.executable, "-c", sProgram], capture_output=True, timeout=30)
    assert resultProc.returncode == 0, resultProc.stderr
    assert confinedRead.fiParseSkippedCount(
        resultProc.stderr.decode()) == 1


# ---------------------------------------------------------------------
# Through the connection, over a daemon
# ---------------------------------------------------------------------


class _ScriptedDaemon(ExecProgramDaemon):
    """A daemon that answers with scripted frames instead of running code."""

    def __init__(self, listFrames, iExitCode=0, bNeverSettles=False):
        super().__init__()
        self._listFrames = listFrames
        self._iExitCode = iExitCode
        self._bNeverSettles = bNeverSettles

    def _fnServeUntilClosed(self, sExecId, listCommand, socketFar):
        socketFar.settimeout(30)
        _fbaReadUntilHalfClose(socketFar)
        for iStream, baChunk in self._listFrames:
            socketFar.sendall(
                struct.pack(">BxxxL", iStream, len(baChunk)) + baChunk)
        self.dictExitCodes[sExecId] = self._iExitCode

    def exec_inspect(self, sExecId):
        for threadDaemon in self.listThreads:
            threadDaemon.join(timeout=30)
        if self._bNeverSettles:
            return {"Running": True, "ExitCode": None}
        return {"ExitCode": self.dictExitCodes.get(sExecId, 0),
                "Running": False}


def testAFileReadThroughTheConnectionYieldsTheRealBytes(dictProject):
    connection = _fconnectionOverDaemon(ExecProgramDaemon(), "cid-read")
    baRead = b"".join(connection.fiterReadFileConfined(
        "cid-read", dictProject["sRoot"] + "/linkToPlain",
        sAuthorizedRoot=dictProject["sRoot"]))
    assert baRead == b"hello"


def testARefusalAndAMissingFileRaiseBeforeAnyChunkIsYielded(dictProject):
    connection = _fconnectionOverDaemon(ExecProgramDaemon(), "cid-refuse")
    iterRefused = connection.fiterReadFileConfined(
        "cid-refuse", dictProject["sRoot"] + "/escapeLink",
        sAuthorizedRoot=dictProject["sRoot"])
    with pytest.raises(ContainerReadRefusedError, match="escapeLink"):
        next(iterRefused)
    iterMissing = connection.fiterReadFileConfined(
        "cid-refuse", dictProject["sRoot"] + "/nothing.txt",
        sAuthorizedRoot=dictProject["sRoot"])
    with pytest.raises(FileNotFoundError):
        next(iterMissing)


@pytest.mark.falsification
def testAPathOutsideTheRootIsRefusedByTheRendererAsTheSameError(dictProject):
    """A path the renderer rejects reaches a route as the SAME refusal.

    Kills: letting the renderer's ``ValueError`` escape untranslated, so
    a route would answer a researcher's out-of-root path with a 500.
    """
    connection = _fconnectionOverDaemon(ExecProgramDaemon(), "cid-outside")
    with pytest.raises(ContainerReadRefusedError):
        next(connection.fiterReadFileConfined(
            "cid-outside", dictProject["sOutside"] + "/secret.txt",
            sAuthorizedRoot=dictProject["sRoot"]))
    assert connection._clientDocker.api.listExecCreateKeywords == []


@pytest.mark.falsification
def testAReadThatFailsAfterSendingBytesRaisesAtTheEnd():
    """A truncated body must not pass for a whole one.

    The program sent bytes and then failed. The bytes already yielded
    cannot be taken back, so the error has to arrive at the end of the
    stream where a route can abort the response.

    Kills: ignoring the program's exit status once the stream has ended.
    """
    daemon = _ScriptedDaemon(
        [(I_FRAME_STDOUT, b"partial"), (I_FRAME_STDERR, b"disk error\n")],
        iExitCode=1)
    connection = _fconnectionOverDaemon(daemon, "cid-truncated")
    iterRead = connection.fiterReadFileConfined(
        "cid-truncated", "/w/p/file.bin", sAuthorizedRoot="/w/p")
    assert next(iterRead) == b"partial"
    with pytest.raises(OSError, match="disk error"):
        next(iterRead)


@pytest.mark.falsification
def testAnExecTheDaemonNeverSettlesIsAnErrorNotASuccess(monkeypatch):
    """No exit status is not exit status zero.

    Kills: treating an unsettled exec as a clean exit, which would let a
    read that died mid-stream be served as complete.
    """
    monkeypatch.setattr(
        dockerConnectionModule, "_I_EXEC_SETTLE_ATTEMPTS", 2)
    monkeypatch.setattr(
        dockerConnectionModule, "_F_EXEC_SETTLE_INTERVAL_SECONDS", 0.0)
    daemon = _ScriptedDaemon(
        [(I_FRAME_STDOUT, b"bytes")], bNeverSettles=True)
    connection = _fconnectionOverDaemon(daemon, "cid-unsettled")
    iterRead = connection.fiterReadFileConfined(
        "cid-unsettled", "/w/p/file.bin", sAuthorizedRoot="/w/p")
    assert next(iterRead) == b"bytes"
    with pytest.raises(OSError, match="did not report"):
        next(iterRead)


@pytest.mark.falsification
def testTheConfinedReadExecsAsTheContainerUserNeverRoot(dictProject):
    """The read has no more authority than the container user.

    Kills: naming root as the exec user of the read program.
    """
    daemon = ExecProgramDaemon()
    connection = _fconnectionOverDaemon(daemon, "cid-user")
    b"".join(connection.fiterReadFileConfined(
        "cid-user", dictProject["sRoot"] + "/plain.txt",
        sAuthorizedRoot=dictProject["sRoot"]))
    dictKeywords = daemon.listExecCreateKeywords[0]
    assert dictKeywords["user"] == "researcher"
    assert dictKeywords["cmd"][0] == "python3"


@pytest.mark.falsification
def testADownloadNeedsNoCarrierAdmissionBecauseAReadMutatesNothing(
    dictProject,
):
    """In an enforced lane with no admission, a read still works.

    A download is requested while a run may be live; gating it behind
    the mutation drain would refuse it for no reason. The write funnel
    next door is gated and refuses in the same lane.

    Kills: adding a mutation-admission assertion to the confined read.
    """
    connection = _fconnectionOverDaemon(ExecProgramDaemon(), "cid-lane")
    tokenLane = mutationAdmission.ftokenMarkEnforcedLane()
    try:
        baRead = b"".join(connection.fiterReadFileConfined(
            "cid-lane", dictProject["sRoot"] + "/plain.txt",
            sAuthorizedRoot=dictProject["sRoot"]))
    finally:
        mutationAdmission.fnResetEnforcedLane(tokenLane)
    assert baRead == b"hello"


def testAnArchiveThroughTheConnectionIsATarOfTheTree(dictProject):
    import tarfile
    connection = _fconnectionOverDaemon(ExecProgramDaemon(), "cid-tar")
    baArchive = b"".join(connection.fiterReadDirectoryAsTar(
        "cid-tar", dictProject["sRoot"] + "/sub",
        sAuthorizedRoot=dictProject["sRoot"]))
    with tarfile.open(fileobj=io.BytesIO(baArchive)) as tarIn:
        assert "sub/nested/file.txt" in tarIn.getnames()


def testTheSkippedCountIsLoggedNotLost(dictProject, caplog):
    import logging
    connection = _fconnectionOverDaemon(ExecProgramDaemon(), "cid-skip")
    with caplog.at_level(logging.WARNING, logger="vaibify"):
        b"".join(connection.fiterReadDirectoryAsTar(
            "cid-skip", dictProject["sRoot"],
            sAuthorizedRoot=dictProject["sRoot"]))
    assert any("without 1 special" in sRecord for sRecord in caplog.messages)


def testTheExitCodeMappingNamesEachKindOfFailure():
    with pytest.raises(FileNotFoundError):
        confinedRead.fnRaiseWhenReadFailed(
            confinedRead.I_NOT_FOUND_EXIT_CODE, "not found: 'x'", "/w/x")
    with pytest.raises(ContainerReadRefusedError):
        confinedRead.fnRaiseWhenReadFailed(3, "refused: nope", "/w/x")
    with pytest.raises(OSError) as infoError:
        confinedRead.fnRaiseWhenReadFailed(1, "boom", "/w/x")
    assert not isinstance(
        infoError.value, (ContainerReadRefusedError, FileNotFoundError))
    confinedRead.fnRaiseWhenReadFailed(0, "", "/w/x")
