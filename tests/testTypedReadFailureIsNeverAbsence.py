"""A read that failed is never reported as a path that is not there.

Source: ``DockerConnection._ftRunTypedRead`` / ``fbaFetchFile`` and the
module-level ``_fnRaiseWhenTypedReadFailed`` in
``vaibify/docker/dockerConnection.py``; the state loader in
``vaibify/gui/stateManager.py``; ``DockerConnection._ftRunProgramWithStdin``.

``fbaFetchFile`` used to raise ``FileNotFoundError`` for ANY nonzero
exit. The state loader reads that as "state.json is absent", falls back
to the ``.bak`` checkpoint or bootstraps an empty document, and the
merge then installs one project's section into it -- writing over every
other project's section. Under user-mode emulation the container's
interpreter dies of SIGSEGV (exit 139, no output) about once in four
hundred starts, so "absent" was a live misreading, not a hypothetical.

The program tests run the REAL typed-read program text in a subprocess;
the connection tests script the exec answers the daemon gives (a
signal death is exit 139 with empty streams, as measured).
"""

import base64
import os
import subprocess
import sys

import pytest

from tests.confinedWriteHarness import ExecProgramDaemon
from tests.testConfinedContainerWrite import _fconnectionOverDaemon
from vaibify.docker import dockerConnection as dockerConnectionModule
from vaibify.docker.confinedWrite import ContainerWriteRefusedError
from vaibify.docker.dockerConnection import ExecResult
from vaibify.gui import stateManager

I_SEGMENTATION_FAULT_EXIT_CODE = 139
S_STATE_PATH = "/workspace/project/.vaibify/state.json"


def _fsRenderFileReadProgram(sPath):
    return dockerConnectionModule._DICT_TYPED_READ_PROGRAMS[
        dockerConnectionModule.S_TYPED_READ_FILE_BASE64
    ].replace(
        dockerConnectionModule._S_TYPED_READ_PATH_SLOT, repr(sPath),
    )


def _fiRunFileReadProgram(sPath):
    return subprocess.run(
        [sys.executable, "-c", _fsRenderFileReadProgram(sPath)],
        capture_output=True,
    ).returncode


def _fconnectionAnswering(listExitCodes, baContent=b"{}"):
    """Return a connection whose execs answer the scripted exit codes.

    A zero answers ``baContent`` base64-encoded, the way the file read
    does; any other code answers with empty streams. Every command is
    recorded on ``listCommands``.
    """
    connection = _fconnectionOverDaemon(ExecProgramDaemon(), "cid-read")
    connection.listCommands = []
    iterExitCodes = iter(listExitCodes)

    def ftRunScripted(sContainerId, sCommand, sWorkdir=None, sUser=None, sLane=None):
        connection.listCommands.append(sCommand)
        iExitCode = next(iterExitCodes, 0)
        sStdout = (base64.b64encode(baContent).decode()
                   if iExitCode == 0 else "")
        return ExecResult(iExitCode=iExitCode, sStdout=sStdout, sStderr="")

    connection.ftRunInContainerStreamed = ftRunScripted
    return connection


@pytest.mark.falsification
def testTheFileReadProgramAnswersAbsenceWithItsOwnStatus(tmp_path):
    """Only a missing path or parent is the not-found status.

    Kills: dropping the program's absence handler, which turns a missing
    file into a traceback exit indistinguishable from any other failure.
    """
    sFile = os.path.join(str(tmp_path), "present.txt")
    with open(sFile, "w") as fileOut:
        fileOut.write("x")
    iNotFound = dockerConnectionModule._I_TYPED_READ_NOT_FOUND_EXIT_CODE
    assert _fiRunFileReadProgram(sFile) == 0
    assert _fiRunFileReadProgram(sFile + ".missing") == iNotFound
    assert _fiRunFileReadProgram(os.path.join(sFile, "child")) == iNotFound
    assert _fiRunFileReadProgram(str(tmp_path)) not in (0, iNotFound)


@pytest.mark.falsification
def testACrashedFileReadIsNeverReportedAsMissing():
    """A read killed twice is an OSError that names the signal.

    Kills: ``_fnRaiseWhenTypedReadFailed`` raising ``FileNotFoundError``
    for every nonzero exit, which the state loader reads as absence.
    """
    connection = _fconnectionAnswering(
        [I_SEGMENTATION_FAULT_EXIT_CODE, I_SEGMENTATION_FAULT_EXIT_CODE])
    with pytest.raises(OSError) as infoError:
        connection.fbaFetchFile("cid-read", S_STATE_PATH)
    assert not isinstance(infoError.value, FileNotFoundError)
    assert "killed by signal 11 (exit 139)" in str(infoError.value)


def testOnlyTheNotFoundStatusIsMissing():
    connection = _fconnectionAnswering(
        [dockerConnectionModule._I_TYPED_READ_NOT_FOUND_EXIT_CODE, 1])
    with pytest.raises(FileNotFoundError):
        connection.fbaFetchFile("cid-read", S_STATE_PATH)
    with pytest.raises(OSError) as infoError:
        connection.fbaFetchFile("cid-read", S_STATE_PATH)
    assert not isinstance(infoError.value, FileNotFoundError)
    assert "(exit 1)" in str(infoError.value)


@pytest.mark.falsification
def testAnUnreportedExitIsNeverMissing():
    """An exec whose exit status never arrived says so.

    Kills: dropping the unreported-exit branch, which left a read the
    daemon never settled described as "(exit None)".
    """
    connection = _fconnectionAnswering([None])
    with pytest.raises(OSError) as infoError:
        connection.fbaFetchFile("cid-read", S_STATE_PATH)
    assert not isinstance(infoError.value, FileNotFoundError)
    assert "did not report how the read ended" in str(infoError.value)


@pytest.mark.falsification
def testAReadKilledByASignalIsReadOnceMore():
    """One emulator crash no longer fails a read.

    Kills: ``_ftRunTypedRead`` returning the killed attempt's result.
    """
    connection = _fconnectionAnswering(
        [I_SEGMENTATION_FAULT_EXIT_CODE, 0], baContent=b'{"a": 1}')
    assert connection.fbaFetchFile("cid-read", S_STATE_PATH) == b'{"a": 1}'
    assert len(connection.listCommands) == 2
    assert connection.listCommands[0] == connection.listCommands[1]


@pytest.mark.falsification
def testACrashedStateReadRefusesTheMergeAndWritesNothing(monkeypatch):
    """The completion merge refuses loudly instead of writing over sections.

    Every exec dies of SIGSEGV. The loader must neither fall back to the
    checkpoint nor bootstrap, and the merge must write nothing -- no
    temporary file, no quarantine ``mv``, no rename.

    Kills: ``fbaFetchFile`` mapping a failed read to
    ``FileNotFoundError``, under which the merge bootstraps an empty
    document and writes this project's section as the whole file.
    """
    connection = _fconnectionAnswering(
        [I_SEGMENTATION_FAULT_EXIT_CODE] * 20)
    listWrites = []
    monkeypatch.setattr(
        connection, "fnWriteFile",
        lambda *tArguments, **dictKeywords: listWrites.append(tArguments))
    with pytest.raises(OSError) as infoError:
        stateManager.fdictMergeRunResultsIntoState(
            connection, "cid-read", S_STATE_PATH,
            ".vaibify/projects/project.json",
            {"stepone": {"fWallClock": 1.0}}, {},
        )
    assert "killed by signal 11" in str(infoError.value)
    assert listWrites == []
    assert len(connection.listCommands) == 2
    assert all("base64" in sCommand for sCommand in connection.listCommands)


class _LateSettlingDaemon(ExecProgramDaemon):
    """Report the exec as still running on the first inspect."""

    def __init__(self):
        super().__init__()
        self.iInspections = 0

    def exec_inspect(self, sExecId):
        self.iInspections += 1
        if self.iInspections == 1:
            return {"ExitCode": None, "Running": True}
        return super().exec_inspect(sExecId)


@pytest.mark.falsification
def testAWriteRefusedBeforeTheDaemonSettledIsStillARefusal(
    tmp_path, monkeypatch,
):
    """An exit status not yet recorded is awaited, never read as zero.

    Kills: reading the first inspect's ``ExitCode or 0``, which turned a
    refused write into a success whenever the stream closed a moment
    before the daemon recorded how the program exited.
    """
    monkeypatch.setattr(
        dockerConnectionModule, "_F_EXEC_SETTLE_INTERVAL_SECONDS", 0.0)
    sRoot = os.path.realpath(str(tmp_path))
    connection = _fconnectionOverDaemon(_LateSettlingDaemon(), "cid-late")
    with pytest.raises(ContainerWriteRefusedError):
        connection.fnWriteFile(
            "cid-late", os.path.join(sRoot, ".git", "config"), b"x",
            sAuthorizedRoot=sRoot, tForbiddenNames=(".git",))
