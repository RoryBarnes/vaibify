"""The confined single-file write streams, counts, and never half-replaces.

Source: ``vaibify/docker/confinedWrite.py`` (the single-file program) and
``DockerConnection.fnWriteFileFromStream``.

The program used to read all of standard input into memory before it
wrote a byte, so a large upload meant a large allocation inside the
container, and it renamed whatever it had received into place whether or
not that was everything the sender meant to send. These tests drive the
REAL program: in a subprocess where the property is about the result on
disk, and in-process where the property is about HOW it reads, which only
a stand-in for standard input that records its calls can see.
"""

import errno
import io
import os
import stat
import subprocess
import sys
from types import SimpleNamespace

import pytest

from tests.confinedWriteHarness import ExecProgramDaemon
from tests.testConfinedContainerWrite import _fconnectionOverDaemon
from vaibify.docker import confinedWrite
from vaibify.docker.confinedWrite import (
    ContainerWriteExistsError,
    ContainerWriteRefusedError,
    I_EXISTS_EXIT_CODE,
    I_NOT_FOUND_EXIT_CODE,
    I_NO_SPACE_EXIT_CODE,
    I_REFUSED_EXIT_CODE,
    fsRenderConfinedWriteProgram,
)

I_MEBIBYTE = 1 << 20


def _fexecuteProgram(sProgram, baStdin=b""):
    return subprocess.run(
        [sys.executable, "-c", sProgram], input=baStdin,
        capture_output=True,
    )


def _fsRealPath(pathTmp):
    return os.path.realpath(str(pathTmp))


def _fsReadFile(sPath):
    with open(sPath, "rb") as fileIn:
        return fileIn.read()


class _RecordingStdin:
    """A binary stdin that remembers how it was read.

    ``listReadSizes`` holds the size argument of every ``read`` call
    (``-1`` for a bare ``read()``), which is what separates a bounded
    chunked copy from a slurp. ``fnBeforeFirstRead`` runs once, before
    the first bytes are handed over: the window in which a file can
    appear while an upload is still arriving.
    """

    def __init__(self, baContent, fnBeforeFirstRead=None, iMaxReads=None):
        self._fileContent = io.BytesIO(baContent)
        self.listReadSizes = []
        self._fnBeforeFirstRead = fnBeforeFirstRead
        self._iMaxReads = iMaxReads

    def read(self, iSize=-1):
        if self._fnBeforeFirstRead is not None and not self.listReadSizes:
            self._fnBeforeFirstRead()
        self.listReadSizes.append(iSize)
        if self._iMaxReads is not None and len(self.listReadSizes) > self._iMaxReads:
            raise AssertionError(
                f"the program kept reading past {self._iMaxReads} reads")
        return self._fileContent.read(iSize)


class _EndlessStdin(_RecordingStdin):
    """A sender that never stops: every read returns a full chunk."""

    def read(self, iSize=-1):
        self.listReadSizes.append(iSize)
        if len(self.listReadSizes) > self._iMaxReads:
            raise AssertionError(
                f"the program kept reading past {self._iMaxReads} reads")
        return b"x" * iSize


def _fiRunProgramInProcess(monkeypatch, sProgram, stdinFake):
    """Exec the program in this process; return its exit status (0 if none)."""
    monkeypatch.setattr(sys, "stdin", SimpleNamespace(buffer=stdinFake))
    try:
        exec(compile(sProgram, "<confined-write>", "exec"), {"__name__": "x"})
    except SystemExit as exitProgram:
        return exitProgram.code or 0
    return 0


def _flistTemporaries(sDirectory):
    return [sName for sName in os.listdir(sDirectory)
            if sName.startswith(".vaibify-write-")]


# ---------------------------------------------------------------------
# Streaming: bounded memory whatever the size
# ---------------------------------------------------------------------


@pytest.mark.falsification
def testALargeStreamIsCopiedInBoundedChunksNeverSlurped(tmp_path, monkeypatch):
    """A payload past the old 64 MiB fetch ceiling lands without one big read.

    The payload is small here and the property is the READ PATTERN: every
    call asks for a bounded chunk, so the memory held is the chunk, not
    the file. A 70 MB fixture would prove the same thing slower.

    Kills: reading standard input whole (``read()``), which is how the
    program held an entire upload in memory.
    """
    sRoot = _fsRealPath(tmp_path)
    sTarget = os.path.join(sRoot, "big.bin")
    baPayload = os.urandom(I_MEBIBYTE) * 5 + b"tail"
    stdinFake = _RecordingStdin(baPayload)
    iExit = _fiRunProgramInProcess(
        monkeypatch, fsRenderConfinedWriteProgram(sTarget), stdinFake)
    assert iExit == 0
    assert _fsReadFile(sTarget) == baPayload
    assert all(0 < iSize <= I_MEBIBYTE for iSize in stdinFake.listReadSizes), (
        stdinFake.listReadSizes
    )
    assert len(stdinFake.listReadSizes) >= 6


# ---------------------------------------------------------------------
# Refusing to replace
# ---------------------------------------------------------------------


@pytest.mark.falsification
def testAForbiddenReplacementIsRefusedAndTheOldBytesStay(tmp_path):
    """An existing file is not replaced when the caller said not to.

    Kills: ignoring ``bReplaceAllowed`` (rendering it as always True).
    """
    sRoot = _fsRealPath(tmp_path)
    sTarget = os.path.join(sRoot, "data.csv")
    with open(sTarget, "wb") as fileOld:
        fileOld.write(b"old")
    resultProc = _fexecuteProgram(
        fsRenderConfinedWriteProgram(sTarget, bReplaceAllowed=False), b"new")
    assert resultProc.returncode == I_EXISTS_EXIT_CODE, resultProc.stderr
    assert b"already exists" in resultProc.stderr
    assert _fsReadFile(sTarget) == b"old"
    assert _flistTemporaries(sRoot) == []


def testAForbiddenReplacementStillCreatesAFreshFile(tmp_path):
    sRoot = _fsRealPath(tmp_path)
    sTarget = os.path.join(sRoot, "fresh.csv")
    resultProc = _fexecuteProgram(
        fsRenderConfinedWriteProgram(sTarget, bReplaceAllowed=False), b"new")
    assert resultProc.returncode == 0, resultProc.stderr
    assert _fsReadFile(sTarget) == b"new"
    assert os.listdir(sRoot) == ["fresh.csv"]


@pytest.mark.falsification
def testAFileThatAppearsWhileTheUploadArrivesIsNotReplaced(
    tmp_path, monkeypatch,
):
    """The existence check is repeated atomically at the commit.

    A large upload takes minutes, and the name can be taken in that
    time. The file that appears while the bytes are still arriving must
    survive, byte for byte.

    Kills: committing with a plain rename when replacement is forbidden,
    which clobbers a file that appeared after the early existence check.
    """
    sRoot = _fsRealPath(tmp_path)
    sTarget = os.path.join(sRoot, "contested.txt")

    def fnAppear():
        with open(sTarget, "wb") as fileAppeared:
            fileAppeared.write(b"someone else's")

    stdinFake = _RecordingStdin(b"mine", fnBeforeFirstRead=fnAppear)
    iExit = _fiRunProgramInProcess(
        monkeypatch,
        fsRenderConfinedWriteProgram(sTarget, bReplaceAllowed=False),
        stdinFake)
    assert iExit == I_EXISTS_EXIT_CODE
    assert _fsReadFile(sTarget) == b"someone else's"
    assert _flistTemporaries(sRoot) == []


@pytest.mark.parametrize("sKind", ["symlink", "directory"])
def testAForbiddenReplacementNamesAnExistingLinkOrDirectoryAsRefused(
    tmp_path, sKind,
):
    sRoot = _fsRealPath(tmp_path)
    sTarget = os.path.join(sRoot, "thing")
    if sKind == "symlink":
        os.symlink(sRoot, sTarget)
    else:
        os.mkdir(sTarget)
    resultProc = _fexecuteProgram(
        fsRenderConfinedWriteProgram(sTarget, bReplaceAllowed=False), b"x")
    assert resultProc.returncode == I_REFUSED_EXIT_CODE, resultProc.stderr


# ---------------------------------------------------------------------
# Stating the size
# ---------------------------------------------------------------------


@pytest.mark.falsification
def testAShortBodyIsRefusedTheTemporaryRemovedAndTheOldBytesStay(tmp_path):
    """A stream that ends early never replaces the file.

    This is the dropped-connection case: the sender meant 1000 bytes and
    the program saw 5.

    Kills: dropping the final size comparison, which renames a truncated
    file into place.
    """
    sRoot = _fsRealPath(tmp_path)
    sTarget = os.path.join(sRoot, "state.json")
    with open(sTarget, "wb") as fileOld:
        fileOld.write(b"old")
    resultProc = _fexecuteProgram(
        fsRenderConfinedWriteProgram(sTarget, iExpectedBytes=1000), b"short")
    assert resultProc.returncode == I_REFUSED_EXIT_CODE, resultProc.stderr
    assert b"5 bytes arrived but 1000 were expected" in resultProc.stderr
    assert _fsReadFile(sTarget) == b"old"
    assert _flistTemporaries(sRoot) == []


@pytest.mark.falsification
def testAnOverlongStreamIsRefusedAtOnceNotDrainedToTheEnd(
    tmp_path, monkeypatch,
):
    """A sender that exceeds its stated size cannot fill the disk.

    The sender here never stops; the program must refuse a few chunks
    past the limit rather than keep reading.

    Kills: removing the in-loop size check so the program copies until
    the stream ends.
    """
    sRoot = _fsRealPath(tmp_path)
    sTarget = os.path.join(sRoot, "bounded.bin")
    stdinFake = _EndlessStdin(b"", iMaxReads=50)
    iExpectedBytes = 3 * I_MEBIBYTE
    iExit = _fiRunProgramInProcess(
        monkeypatch,
        fsRenderConfinedWriteProgram(sTarget, iExpectedBytes=iExpectedBytes),
        stdinFake)
    assert iExit == I_REFUSED_EXIT_CODE
    assert len(stdinFake.listReadSizes) <= 4
    assert not os.path.exists(sTarget)
    assert _flistTemporaries(sRoot) == []


def testAnExactSizeIsAccepted(tmp_path):
    sRoot = _fsRealPath(tmp_path)
    sTarget = os.path.join(sRoot, "exact.bin")
    resultProc = _fexecuteProgram(
        fsRenderConfinedWriteProgram(sTarget, iExpectedBytes=9), b"123456789")
    assert resultProc.returncode == 0, resultProc.stderr
    assert _fsReadFile(sTarget) == b"123456789"


def testAnEmptyFileWithAnExpectedSizeOfZeroIsAccepted(tmp_path):
    sRoot = _fsRealPath(tmp_path)
    sTarget = os.path.join(sRoot, "empty.txt")
    resultProc = _fexecuteProgram(
        fsRenderConfinedWriteProgram(sTarget, iExpectedBytes=0), b"")
    assert resultProc.returncode == 0, resultProc.stderr
    assert _fsReadFile(sTarget) == b""


# ---------------------------------------------------------------------
# Durability and a full disk
# ---------------------------------------------------------------------


@pytest.mark.falsification
def testTheBytesAreFlushedToDiskBeforeTheRename(tmp_path, monkeypatch):
    """The rename only ever publishes bytes that were fsynced.

    Kills: removing the program's ``fsync``, after which a crash can
    leave a renamed file whose content never reached the disk.
    """
    sRoot = _fsRealPath(tmp_path)
    sTarget = os.path.join(sRoot, "durable.txt")
    listEvents = []
    fnRealFsync, fnRealRename = os.fsync, os.rename
    monkeypatch.setattr(
        os, "fsync", lambda iFile: (listEvents.append("fsync"),
                                    fnRealFsync(iFile))[1])
    monkeypatch.setattr(
        os, "rename", lambda *tArgs, **dictArgs: (listEvents.append("rename"),
                                                  fnRealRename(
                                                      *tArgs, **dictArgs))[1])
    iExit = _fiRunProgramInProcess(
        monkeypatch, fsRenderConfinedWriteProgram(sTarget),
        _RecordingStdin(b"payload"))
    assert iExit == 0
    assert listEvents == ["fsync", "rename"]


@pytest.mark.falsification
def testAFullDiskExitsWithItsOwnStatusAndLeavesTheOldFileAlone(
    tmp_path, monkeypatch,
):
    """ENOSPC is reported as a full disk, with nothing left behind.

    Kills: letting the OSError escape as a bare traceback, which the
    caller can only report as an unexplained failure.
    """
    sRoot = _fsRealPath(tmp_path)
    sTarget = os.path.join(sRoot, "full.bin")
    with open(sTarget, "wb") as fileOld:
        fileOld.write(b"old")

    def fnFull(iFile):
        raise OSError(errno.ENOSPC, "No space left on device")

    monkeypatch.setattr(os, "fsync", fnFull)
    iExit = _fiRunProgramInProcess(
        monkeypatch, fsRenderConfinedWriteProgram(sTarget),
        _RecordingStdin(b"new"))
    assert iExit == I_NO_SPACE_EXIT_CODE
    monkeypatch.undo()
    assert _fsReadFile(sTarget) == b"old"
    assert _flistTemporaries(sRoot) == []


# ---------------------------------------------------------------------
# The error each exit status becomes
# ---------------------------------------------------------------------


def _ftExecResult(iExitCode, sStderr=""):
    return SimpleNamespace(iExitCode=iExitCode, sStderr=sStderr, sStdout="")


def testAnExistsStatusBecomesTheExistsErrorWhichIsStillARefusal():
    with pytest.raises(ContainerWriteExistsError) as infoError:
        confinedWrite.fnRaiseWhenWriteFailed(
            _ftExecResult(I_EXISTS_EXIT_CODE, "refused: 'a' already exists"),
            "/w/a")
    assert isinstance(infoError.value, ContainerWriteRefusedError)


@pytest.mark.falsification
def testAFullDiskStatusBecomesAnOSErrorCarryingEnospc():
    """Both legs raise ``OSError`` with ``errno.ENOSPC``; a route tests one.

    Kills: mapping the no-space status to a plain failure with no errno.
    """
    with pytest.raises(OSError) as infoError:
        confinedWrite.fnRaiseWhenWriteFailed(
            _ftExecResult(I_NO_SPACE_EXIT_CODE, "failed: full"), "/w/a")
    assert infoError.value.errno == errno.ENOSPC
    assert not isinstance(infoError.value, ContainerWriteRefusedError)


@pytest.mark.parametrize("dictBad", [
    {"bReplaceAllowed": "yes"},
    {"iExpectedBytes": -1},
    {"iExpectedBytes": True},
    {"iExpectedBytes": "5"},
])
def testTheRenderRefusesValuesThatAreNotLiterals(dictBad):
    with pytest.raises((TypeError, ValueError)):
        fsRenderConfinedWriteProgram("/w/a.txt", **dictBad)


# ---------------------------------------------------------------------
# Through the connection, over a daemon that really runs the program
# ---------------------------------------------------------------------


def testTheStreamFunnelWritesFromAFileLikeSource(tmp_path):
    sRoot = _fsRealPath(tmp_path)
    sTarget = os.path.join(sRoot, "streamed.bin")
    connection = _fconnectionOverDaemon(ExecProgramDaemon(), "cid-stream")
    baPayload = os.urandom(I_MEBIBYTE) + b"end"
    connection.fnWriteFileFromStream(
        "cid-stream", sTarget, io.BytesIO(baPayload),
        iExpectedBytes=len(baPayload), sAuthorizedRoot=sRoot)
    assert _fsReadFile(sTarget) == baPayload


def testTheStreamFunnelRaisesTheExistsErrorWithTheOldBytesKept(tmp_path):
    sRoot = _fsRealPath(tmp_path)
    sTarget = os.path.join(sRoot, "taken.txt")
    with open(sTarget, "wb") as fileOld:
        fileOld.write(b"old")
    connection = _fconnectionOverDaemon(ExecProgramDaemon(), "cid-exists")
    with pytest.raises(ContainerWriteExistsError):
        connection.fnWriteFileFromStream(
            "cid-exists", sTarget, io.BytesIO(b"new"),
            bReplaceAllowed=False, sAuthorizedRoot=sRoot)
    assert _fsReadFile(sTarget) == b"old"


def testTheStreamFunnelRefusesASourceShorterThanItsStatedSize(tmp_path):
    sRoot = _fsRealPath(tmp_path)
    sTarget = os.path.join(sRoot, "short.txt")
    connection = _fconnectionOverDaemon(ExecProgramDaemon(), "cid-short")
    with pytest.raises(ContainerWriteRefusedError):
        connection.fnWriteFileFromStream(
            "cid-short", sTarget, io.BytesIO(b"abc"),
            iExpectedBytes=10, sAuthorizedRoot=sRoot)
    assert not os.path.exists(sTarget)


@pytest.mark.falsification
def testTheBytesEntryPointStatesItsLengthSoATruncatedTransferIsRefused(
    tmp_path,
):
    """``fnWriteFileViaTar`` tells the program how many bytes to expect.

    The daemon double here delivers only part of what was sent, the way
    a dropped hijacked connection does. With the length stated the
    program refuses; without it the truncated file would be renamed
    into place and read as the real one.

    Kills: ``fnWriteFileViaTar`` omitting ``iExpectedBytes``.
    """
    sRoot = _fsRealPath(tmp_path)
    sTarget = os.path.join(sRoot, "wire.bin")

    class _TruncatingDaemon(ExecProgramDaemon):
        def _fnServeUntilClosed(self, sExecId, listCommand, socketFar):
            socketFar.settimeout(30)
            from tests.confinedWriteHarness import _fbaReadUntilHalfClose
            baStdin = _fbaReadUntilHalfClose(socketFar)[:4]
            resultProc = subprocess.run(
                [sys.executable] + listCommand[1:], input=baStdin,
                capture_output=True)
            self.dictExitCodes[sExecId] = resultProc.returncode
            self.listReceivedStdin.append(baStdin)

    connection = _fconnectionOverDaemon(_TruncatingDaemon(), "cid-wire")
    with pytest.raises(ContainerWriteRefusedError):
        connection.fnWriteFileViaTar(
            "cid-wire", sTarget, b"0123456789", sAuthorizedRoot=sRoot)
    assert not os.path.exists(sTarget)


# ---------------------------------------------------------------------
# Creating the parents of a nested file (a dropped folder)
# ---------------------------------------------------------------------


@pytest.mark.falsification
def testMissingParentsAreCreatedBelowTheRootWhenAsked(tmp_path):
    """A nested file lands with its folders made, one component at a time.

    Kills: ignoring ``bCreateParents``, which refuses every file whose
    folder does not exist yet.
    """
    sRoot = _fsRealPath(tmp_path)
    sTarget = os.path.join(sRoot, "a", "b", "file.txt")
    resultProc = _fexecuteProgram(
        fsRenderConfinedWriteProgram(
            sTarget, sAuthorizedRoot=sRoot, bCreateParents=True), b"nested")
    assert resultProc.returncode == 0, resultProc.stderr
    assert _fsReadFile(sTarget) == b"nested"
    assert stat.S_IMODE(os.stat(os.path.join(sRoot, "a")).st_mode) == 0o755


@pytest.mark.falsification
def testAMissingParentWithoutTheFlagIsNotFoundAndNothingIsCreated(tmp_path):
    """Kills: creating directories whether or not the caller asked."""
    sRoot = _fsRealPath(tmp_path)
    sTarget = os.path.join(sRoot, "a", "file.txt")
    resultProc = _fexecuteProgram(
        fsRenderConfinedWriteProgram(sTarget, sAuthorizedRoot=sRoot), b"x")
    assert resultProc.returncode == I_NOT_FOUND_EXIT_CODE, resultProc.stderr
    assert os.listdir(sRoot) == []


@pytest.mark.falsification
def testAMissingDirectoryAtOrAboveTheRootIsNeverCreated(tmp_path):
    """The root is the caller's boundary; the program does not extend it.

    Kills: creating missing components without checking that they are
    below the authorized root.
    """
    sRoot = _fsRealPath(tmp_path) + "/does/not/exist"
    sTarget = sRoot + "/inner/file.txt"
    resultProc = _fexecuteProgram(
        fsRenderConfinedWriteProgram(
            sTarget, sAuthorizedRoot=sRoot, bCreateParents=True), b"x")
    assert resultProc.returncode == I_NOT_FOUND_EXIT_CODE, resultProc.stderr
    assert not os.path.exists(_fsRealPath(tmp_path) + "/does")


@pytest.mark.falsification
def testParentCreationNeverFollowsASymlinkOutOfTheRoot(tmp_path):
    """Kills: creating a directory through a planted link."""
    sRoot = _fsRealPath(tmp_path / "project")
    sOutside = _fsRealPath(tmp_path / "outside")
    os.makedirs(sRoot)
    os.makedirs(sOutside)
    os.symlink(sOutside, os.path.join(sRoot, "linked"))
    resultProc = _fexecuteProgram(
        fsRenderConfinedWriteProgram(
            os.path.join(sRoot, "linked", "new", "f.txt"),
            sAuthorizedRoot=sRoot, bCreateParents=True), b"x")
    assert resultProc.returncode == I_REFUSED_EXIT_CODE, resultProc.stderr
    assert os.listdir(sOutside) == []


def testTheNotFoundStatusBecomesFileNotFound():
    with pytest.raises(FileNotFoundError):
        confinedWrite.fnRaiseWhenWriteFailed(
            _ftExecResult(confinedWrite.I_NOT_FOUND_EXIT_CODE,
                          "not found: 'a'"), "/w/a/f")
