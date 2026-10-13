"""A confined write whose program was killed says so, and is sent once more.

Source: ``vaibify/docker/confinedWrite.py`` (``fnRaiseWhenWriteFailed``,
``fiSignalThatKilledProgram``) and
``DockerConnection._ftRunConfinedWriteProgram``.

An image built for another architecture runs every container process
under user-mode emulation, where ``python3`` is occasionally killed by
SIGSEGV before it does anything: Docker reports exit 139 and the program
writes nothing to either stream. The failure message used to fall
through to "no error text" with the exit code dropped, which a
researcher saw after a run as "recording its results failed" with no way
to tell a crash from a refusal. The daemon double here really runs the
program, and kills the attempts a test names the way the emulator does:
stdin consumed, no output, exit 139.
"""

import io
import os
import subprocess
import sys
from types import SimpleNamespace

import pytest

from tests.confinedWriteHarness import ExecProgramDaemon
from tests.confinedWriteHarness import _fbaReadUntilHalfClose
from tests.testConfinedContainerWrite import _fconnectionOverDaemon
from vaibify.docker import confinedWrite
from vaibify.docker.confinedWrite import ContainerWriteExistsError

I_SEGMENTATION_FAULT_EXIT_CODE = 139


class _KillingDaemon(ExecProgramDaemon):
    """Kill the first ``iAttemptsToKill`` execs; run the rest for real."""

    def __init__(self, iAttemptsToKill):
        super().__init__()
        self.iAttemptsToKill = iAttemptsToKill

    def _fnServeUntilClosed(self, sExecId, listCommand, socketFar):
        socketFar.settimeout(30)
        baStdin = _fbaReadUntilHalfClose(socketFar)
        self.listReceivedStdin.append(baStdin)
        if len(self.listReceivedStdin) <= self.iAttemptsToKill:
            self.dictExitCodes[sExecId] = I_SEGMENTATION_FAULT_EXIT_CODE
            return
        resultProc = subprocess.run(
            [sys.executable] + listCommand[1:], input=baStdin,
            capture_output=True,
        )
        self.dictExitCodes[sExecId] = resultProc.returncode


def _fsRealPath(pathTmp):
    return os.path.realpath(str(pathTmp))


def _fsReadFile(sPath):
    with open(sPath, "rb") as fileIn:
        return fileIn.read()


def _ftExecResult(iExitCode, sStderr="", sStdout=""):
    return SimpleNamespace(
        iExitCode=iExitCode, sStderr=sStderr, sStdout=sStdout,
    )


@pytest.mark.falsification
def testASignalDeathNamesTheSignalAndTheExitCode():
    """The researcher-facing text says the writer was killed, and how.

    Kills: dropping the signal branch, which answered a crash with the
    generic "gave no reason" sentence, as if the program had declined.
    """
    with pytest.raises(OSError) as infoError:
        confinedWrite.fnRaiseWhenWriteFailed(
            _ftExecResult(I_SEGMENTATION_FAULT_EXIT_CODE), "/w/state.json")
    sMessage = str(infoError.value)
    assert "killed by signal 11" in sMessage
    assert "exit 139" in sMessage
    assert "retrying is safe" in sMessage


@pytest.mark.falsification
def testAStartFailureNamesTheDaemonsOwnWords():
    """An exec the daemon could not start is reported on stdout, not stderr.

    Kills: reading only stderr, which turned the daemon's own sentence
    into a message that gave no reason.
    """
    sDaemonSentence = (
        "OCI runtime exec failed: exec failed: unable to start "
        "container process"
    )
    with pytest.raises(OSError) as infoError:
        confinedWrite.fnRaiseWhenWriteFailed(
            _ftExecResult(127, sStdout=sDaemonSentence + "\r\n"),
            "/w/state.json")
    assert sDaemonSentence in str(infoError.value)


@pytest.mark.falsification
def testAnUnexplainedFailureNamesItsExitCode():
    """A failure with no known status and no text still names its code.

    Kills: the generic failure dropping its exit code, which left the
    researcher "no error text" and nothing to search for.
    """
    with pytest.raises(OSError) as infoError:
        confinedWrite.fnRaiseWhenWriteFailed(
            _ftExecResult(2), "/w/state.json")
    sMessage = str(infoError.value)
    assert "(exit 2)" in sMessage
    assert "gave no reason" in sMessage


@pytest.mark.falsification
def testOnlyTheSignalRangeCountsAsADeath():
    """A code above the highest Linux signal is a failure, never a rerun.

    Kills: ``fiSignalThatKilledProgram`` answering any code above 128,
    which would rerun a write that containerd reported as status
    unknown (255).
    """
    assert confinedWrite.fiSignalThatKilledProgram(_ftExecResult(137)) == 9
    assert confinedWrite.fiSignalThatKilledProgram(_ftExecResult(192)) == 64
    for iExitCode in (0, 1, confinedWrite.I_REFUSED_EXIT_CODE, 128, 255):
        assert confinedWrite.fiSignalThatKilledProgram(
            _ftExecResult(iExitCode)) == 0


@pytest.mark.falsification
def testAWriterKilledByASignalIsSentOnceMoreAndLands(tmp_path):
    """One emulator crash no longer loses a state save.

    Kills: returning the first attempt's result without a rerun.
    """
    sRoot = _fsRealPath(tmp_path)
    sTarget = os.path.join(sRoot, "state.json.0123.tmp")
    daemon = _KillingDaemon(iAttemptsToKill=1)
    connection = _fconnectionOverDaemon(daemon, "cid-killed-once")
    baPayload = b'{"iStateSchemaVersion": 3}\n'
    connection.fnWriteFile("cid-killed-once", sTarget, baPayload)
    assert _fsReadFile(sTarget) == baPayload
    assert daemon.listReceivedStdin == [baPayload, baPayload]


@pytest.mark.falsification
def testTheRerunSendsTheSourceFromWhereTheFirstAttemptStarted(tmp_path):
    """The rerun rewinds to the offset the caller handed over, not to 0.

    Kills: a rerun that does not rewind, which sends the rest of an
    already-drained source (nothing), so a crash loses the write.
    """
    sRoot = _fsRealPath(tmp_path)
    sTarget = os.path.join(sRoot, "streamed.bin")
    daemon = _KillingDaemon(iAttemptsToKill=1)
    connection = _fconnectionOverDaemon(daemon, "cid-rewind")
    fileSource = io.BytesIO(b"skip:payload")
    fileSource.seek(len(b"skip:"))
    connection.fnWriteFileFromStream(
        "cid-rewind", sTarget, fileSource, sAuthorizedRoot=sRoot)
    assert _fsReadFile(sTarget) == b"payload"
    assert daemon.listReceivedStdin == [b"payload", b"payload"]


def testASecondDeathRaisesTheNamedFailureAndKeepsTheOldBytes(tmp_path):
    sRoot = _fsRealPath(tmp_path)
    sTarget = os.path.join(sRoot, "state.json")
    with open(sTarget, "wb") as fileOld:
        fileOld.write(b"old")
    daemon = _KillingDaemon(iAttemptsToKill=2)
    connection = _fconnectionOverDaemon(daemon, "cid-killed-twice")
    with pytest.raises(OSError) as infoError:
        connection.fnWriteFile("cid-killed-twice", sTarget, b"new")
    assert "killed by signal 11 (exit 139)" in str(infoError.value)
    assert len(daemon.listReceivedStdin) == 2
    assert _fsReadFile(sTarget) == b"old"


@pytest.mark.falsification
def testANoReplaceWriteIsNeverSentTwice(tmp_path):
    """A no-replace attempt killed after its link landed must not rerun.

    Kills: rerunning a no-replace write, whose second attempt would
    refuse the first attempt's own file as already existing.
    """
    sRoot = _fsRealPath(tmp_path)
    sTarget = os.path.join(sRoot, "upload.bin")
    daemon = _KillingDaemon(iAttemptsToKill=1)
    connection = _fconnectionOverDaemon(daemon, "cid-no-replace")
    with pytest.raises(OSError) as infoError:
        connection.fnWriteFileFromStream(
            "cid-no-replace", sTarget, io.BytesIO(b"bytes"),
            bReplaceAllowed=False, sAuthorizedRoot=sRoot)
    assert not isinstance(infoError.value, ContainerWriteExistsError)
    assert "exit 139" in str(infoError.value)
    assert len(daemon.listReceivedStdin) == 1
