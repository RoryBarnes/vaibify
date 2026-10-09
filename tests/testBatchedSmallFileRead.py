"""The batched small-file read, and the poll that reads markers with it.

The poll used to read each step's test marker with its own container
round trip, on the hub's event loop: a 73-step project froze every
request the hub served for ~5 s per poll (measured 2026-10-05), which
read to the researcher as a project menu that would not open. These
tests run the REAL typed-read program -- the command the adapter
builds is executed on this machine in place of the container -- so a
change to the program text is exercised rather than shadowed by a
fake that agrees with itself.
"""

import base64
import json
import subprocess
import threading
from unittest.mock import MagicMock, patch

import pytest

from vaibify.docker import dockerConnection
from vaibify.docker.dockerConnection import DockerConnection, ExecResult
from vaibify.docker.execArgumentBudget import I_EXEC_ARGUMENT_BUDGET_BYTES
from vaibify.gui import stateManager


def _fconnectionRunningLocally(listCommands=None):
    """Return a DockerConnection whose exec runs the command here.

    Only the transport is replaced: ``_ftRunTypedRead`` still chooses
    and renders the program, and the decoder still reads its answer.
    """
    connectionDocker = object.__new__(DockerConnection)

    def ftRunHere(sContainerId, sCommand, sWorkdir=None, sUser=None):
        if listCommands is not None:
            listCommands.append(sCommand)
        processResult = subprocess.run(
            ["bash", "-c", sCommand], capture_output=True, text=True,
        )
        return ExecResult(
            iExitCode=processResult.returncode,
            sStdout=processResult.stdout,
            sStderr=processResult.stderr,
        )

    connectionDocker.ftRunInContainerStreamed = ftRunHere
    return connectionDocker


@pytest.mark.falsification
def test_an_absent_file_is_answered_as_none_not_omitted(tmp_path):
    """Every requested path is answered; an absent one is ``None``.

    Kills: the program skipping an unopenable path instead of recording
    it, which the decoder must then refuse as an incomplete answer --
    an omitted path would otherwise read as absent without the
    container having said so.
    """
    pathPresent = tmp_path / "present.json"
    pathPresent.write_bytes(b'{"iExitStatus": 0}')
    pathAbsent = tmp_path / "absent.json"

    dictFiles = _fconnectionRunningLocally().fdictFetchSmallFiles(
        "cid", [str(pathPresent), str(pathAbsent)],
    )

    assert dictFiles == {
        str(pathPresent): b'{"iExitStatus": 0}',
        str(pathAbsent): None,
    }


@pytest.mark.falsification
def test_an_oversized_file_costs_the_ceiling_and_is_refused(tmp_path):
    """The container stops one byte past the ceiling; the host refuses.

    Kills: reading the whole file in the container, which would ship an
    arbitrarily large document across the socket before any cap could
    reject it.
    """
    iCeiling = dockerConnection.I_MAX_SMALL_FILE_BYTES
    pathLarge = tmp_path / "large.json"
    pathLarge.write_bytes(b"x" * (iCeiling + 100))
    listCommands = []

    with pytest.raises(ValueError, match="large.json"):
        _fconnectionRunningLocally(listCommands).fdictFetchSmallFiles(
            "cid", [str(pathLarge)],
        )

    processResult = subprocess.run(
        ["bash", "-c", listCommands[0]], capture_output=True, text=True,
    )
    sEncoded = json.loads(processResult.stdout)[str(pathLarge)]
    assert len(base64.b64decode(sEncoded)) == iCeiling + 1


def test_a_failed_read_raises_rather_than_reporting_files_absent():
    connectionDocker = object.__new__(DockerConnection)
    connectionDocker.ftRunInContainerStreamed = MagicMock(
        return_value=ExecResult(
            iExitCode=1, sStdout="", sStderr="container is not running",
        ),
    )
    with pytest.raises(OSError, match="container is not running"):
        connectionDocker.fdictFetchSmallFiles("cid", ["/a.json"])


@pytest.mark.falsification
def test_an_answer_missing_a_requested_path_is_a_failed_read():
    """Kills: dropping the completeness check on a batch's answer."""
    connectionDocker = object.__new__(DockerConnection)
    connectionDocker.ftRunInContainerStreamed = MagicMock(
        return_value=ExecResult(
            iExitCode=0, sStdout=json.dumps({"/a.json": None}),
            sStderr="",
        ),
    )
    with pytest.raises(OSError, match="1 of 2"):
        connectionDocker.fdictFetchSmallFiles(
            "cid", ["/a.json", "/b.json"],
        )


@pytest.mark.falsification
def test_a_large_request_is_split_to_fit_one_exec_argument(tmp_path):
    """Thousands of markers still read, in argument-sized batches.

    Kills: rendering the whole path list into one exec, which Linux
    refuses past ~128 KB in a single argument (execArgumentBudget).
    """
    listPaths = [
        str(tmp_path / ("step%05d_with_a_long_directory_name.json" % i))
        for i in range(3000)
    ]
    listCommands = []

    dictFiles = _fconnectionRunningLocally(listCommands).fdictFetchSmallFiles(
        "cid", listPaths,
    )

    assert dictFiles == {sPath: None for sPath in listPaths}
    assert len(listCommands) > 1
    for sCommand in listCommands:
        assert len(sCommand.encode("utf-8")) < 2 * I_EXEC_ARGUMENT_BUDGET_BYTES


def test_markers_round_trip_through_the_real_program(tmp_path):
    """A present, an absent and a malformed marker, read in one exec."""
    pathMarkers = tmp_path / ".vaibify" / "test_markers" / "demo"
    pathMarkers.mkdir(parents=True)
    (pathMarkers / "stepA.json").write_text(json.dumps({"iExitStatus": 0}))
    (pathMarkers / "nested_sub.json").write_text("{not json")
    listSteps = [
        {"sDirectory": "stepA"}, {"sDirectory": "stepB"},
        {"sDirectory": "nested/sub"}, {"sName": "no directory"},
    ]
    listCommands = []

    listMarkers = stateManager._flistFetchMarkers(
        _fconnectionRunningLocally(listCommands), "cid",
        str(tmp_path), "demo", listSteps,
    )

    assert [(dictStep["sDirectory"], dictMarker)
            for dictStep, dictMarker in listMarkers] == [
        ("stepA", {"iExitStatus": 0}),
        ("stepB", None),
        ("nested/sub", None),
    ]
    assert len(listCommands) == 1


@pytest.mark.falsification
@pytest.mark.asyncio
async def test_the_poll_reads_markers_off_the_event_loop():
    """The marker read runs in a worker; its answer reaches the poll.

    Kills: calling the marker read synchronously from the async poll,
    which blocks every other request the hub serves for as long as the
    container takes to answer.
    """
    from vaibify.gui.routes import pipelineRoutes
    iLoopThread = threading.get_ident()
    listReadThreads = []
    dictMarkers = {0: {"iExitStatus": 0}}

    def fdictRecordThread(dictCtx, sContainerId, dictWorkflow):
        listReadThreads.append(threading.get_ident())
        return dictMarkers

    sModule = "vaibify.gui.routes.pipelineRoutes."
    dictCtx = {
        "docker": MagicMock(), "save": MagicMock(),
        "variables": MagicMock(return_value={}), "paths": {},
    }
    with patch(sModule + "_fdictLoadMarkersForPoll", fdictRecordThread), \
            patch(sModule + "_flistCollectOutputPaths", return_value=[]), \
            patch(sModule + "ftGetModTimesAndFingerprint",
                  return_value=({}, "")), \
            patch(sModule + "_fbCheckStaleUserVerification",
                  return_value=False), \
            patch(sModule + "_ffilesFetchPollSnapshot",
                  return_value="") as mockSnapshot:
        await pipelineRoutes._fdictFetchOutputStatus(
            dictCtx, "cid", {"listSteps": []}, {},
        )

    assert listReadThreads and listReadThreads[0] != iLoopThread
    # The markers the worker read are the ones the snapshot is asked to
    # hash for: the sixth positional argument.
    assert mockSnapshot.call_args[0][5] is dictMarkers


def test_the_host_leg_answers_the_same_contract(tmp_path, monkeypatch):
    """Present bytes, absent ``None``, over the ceiling refused -- on host.

    The path guard is exercised with hostile input by
    ``testHostPathGuardCorpus``; it is stubbed here so this test reads
    only the answer's shape, which the two legs must share.
    """
    from vaibify.host.hostConnection import HostConnection
    connectionHost = object.__new__(HostConnection)
    monkeypatch.setattr(
        connectionHost, "_fsValidateHostPath",
        lambda sContainerId, sPath: sPath,
    )
    pathPresent = tmp_path / "present.json"
    pathPresent.write_bytes(b"{}")
    pathLarge = tmp_path / "large.json"
    pathLarge.write_bytes(
        b"x" * (dockerConnection.I_MAX_SMALL_FILE_BYTES + 1))

    assert connectionHost.fdictFetchSmallFiles(
        "project", [str(pathPresent), str(tmp_path / "absent.json")],
    ) == {str(pathPresent): b"{}", str(tmp_path / "absent.json"): None}
    with pytest.raises(ValueError, match="large.json"):
        connectionHost.fdictFetchSmallFiles("project", [str(pathLarge)])
