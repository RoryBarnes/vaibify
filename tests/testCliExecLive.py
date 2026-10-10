"""A real `vaibify connect` shell ends with its window, or with the reaper.

Both tests run the helper the CLI calls, in a child process under a
pseudo-terminal against a throwaway container, type a long-running job
into the shell, and then end the CLI the two ways a window can end it:
a hang-up, which the CLI's own exit path answers, and a SIGKILL, which
leaves the record for the hub's reaper. Each asserts on the container,
not on the CLI's own report: the session's process group is probed
empty, and the record is gone from the host.
"""

import os
import pty
import signal
import sys
import textwrap
import time

import pytest

from vaibify.gui import cliShellContainment
from tests.liveContainerLabels import fdictLabels
from tests.testDockerConnectionLive import fnRequireDaemonReachable

pytestmark = [pytest.mark.docker_live, pytest.mark.exclusive]

# The identity re-check reads /proc through the typed file read, which
# runs a Python program inside the container AS THE CONTAINER USER, as
# every vaibify image carries both; the Alpine throwaway of the
# containment tests has neither, so this lane's container is a slim
# Python image with the container user added.
S_THROWAWAY_IMAGE = "python:3.10-slim"


@pytest.fixture
def tLiveContainer():
    """Yield (sName, sContainerId, connection) for a throwaway container."""
    import secrets
    fnRequireDaemonReachable()
    import docker
    from vaibify.docker.dockerConnection import DockerConnection
    clientDocker = docker.from_env()
    sName = f"vaibifyTermContain{secrets.token_hex(4)}"
    container = clientDocker.containers.run(
        S_THROWAWAY_IMAGE, ["sleep", "300"], name=sName, detach=True,
        labels=fdictLabels(),
    )
    try:
        iExit, baOutput = container.exec_run(
            ["useradd", "-u", "1000", "-m", "researcher"])
        assert iExit == 0, baOutput
        yield (sName, container.id, DockerConnection())
    finally:
        try:
            container.remove(force=True)
        except Exception:
            pass

S_CLI_UNDER_PTY_SCRIPT = textwrap.dedent('''
    import sys
    from vaibify.gui import cliShellContainment
    cliShellContainment._S_CLI_SHELL_DIRECTORY = sys.argv[2]
    cliShellContainment.fnRunCleanedUpCliExec(sys.argv[1], "root", ["sh"])
''')


def _ftLaunchCliUnderPty(sName, sRecordDirectory):
    iPid, iMasterFd = pty.fork()
    if iPid == 0:  # pragma: no cover - the child execs the CLI helper
        os.execv(sys.executable, [
            sys.executable, "-c", S_CLI_UNDER_PTY_SCRIPT, sName, sRecordDirectory])
    return iPid, iMasterFd


def _fdictAwaitRecord(sRecordDirectory, fTimeoutSeconds=20.0):
    import json
    fDeadline = time.monotonic() + fTimeoutSeconds
    while time.monotonic() < fDeadline:
        listNames = os.listdir(sRecordDirectory) if os.path.isdir(sRecordDirectory) else []
        if listNames:
            with open(os.path.join(sRecordDirectory, listNames[0])) as fileHandle:
                return json.load(fileHandle)
        time.sleep(0.1)
    pytest.fail("the CLI never wrote its session record")


def _fnAwaitMembers(connectionDocker, sContainerId, iSessionId, iAtLeast):
    fDeadline = time.monotonic() + 15.0
    while time.monotonic() < fDeadline:
        dictProbe = connectionDocker.fdictProbeProcessGroupMembers(sContainerId, iSessionId)
        if dictProbe["bConclusive"] and dictProbe["iMemberCount"] >= iAtLeast:
            return
        time.sleep(0.2)
    pytest.fail(f"the typed job never appeared in session {iSessionId}")


def _fnDrainPtyQuietly(iMasterFd):
    try:
        os.set_blocking(iMasterFd, False)
        while True:
            try:
                if not os.read(iMasterFd, 4096):
                    break
            except BlockingIOError:
                break
    except OSError:
        pass


def _fiAwaitChildExit(iPid, fTimeoutSeconds=20.0):
    fDeadline = time.monotonic() + fTimeoutSeconds
    while time.monotonic() < fDeadline:
        iWaited, iStatus = os.waitpid(iPid, os.WNOHANG)
        if iWaited == iPid:
            return iStatus
        time.sleep(0.1)
    os.kill(iPid, signal.SIGKILL)
    os.waitpid(iPid, 0)
    pytest.fail("the CLI did not exit")


def _ftOpenShellWithAJob(tLiveContainer, tmp_path):
    sName, sContainerId, connectionDocker = tLiveContainer
    sRecordDirectory = str(tmp_path / "cliShells")
    iPid, iMasterFd = _ftLaunchCliUnderPty(sName, sRecordDirectory)
    try:
        dictRecord = _fdictAwaitRecord(sRecordDirectory)
        assert dictRecord["sContainerId"] == sContainerId
        assert dictRecord["iCliPid"] == iPid
        iSessionId = dictRecord["iSessionId"]
        os.write(iMasterFd, b"sleep 300 &\n")
        _fnAwaitMembers(connectionDocker, sContainerId, iSessionId, 2)
    except BaseException:
        os.kill(iPid, signal.SIGKILL)
        os.waitpid(iPid, 0)
        raise
    return iPid, iMasterFd, iSessionId, sRecordDirectory


def _fnAssertSessionGone(connectionDocker, sContainerId, iSessionId):
    fDeadline = time.monotonic() + 15.0
    while time.monotonic() < fDeadline:
        dictProbe = connectionDocker.fdictProbeProcessGroupMembers(sContainerId, iSessionId)
        if dictProbe["bConclusive"] and dictProbe["iMemberCount"] == 0:
            return
        time.sleep(0.2)
    pytest.fail(f"session {iSessionId} still has members: {dictProbe}")


@pytest.mark.falsification
def test_a_hangup_ends_the_cli_shell_and_everything_in_it(tLiveContainer, tmp_path):
    """Kills: the exit path's cleanup call in
    ``_fnAwaitCliExecThenEndItsSession`` removed, so the hang-up ends the
    CLI and the shell, with its backgrounded job, runs on.
    """
    sName, sContainerId, connectionDocker = tLiveContainer
    iPid, iMasterFd, iSessionId, sRecordDirectory = _ftOpenShellWithAJob(
        tLiveContainer, tmp_path)
    os.kill(iPid, signal.SIGHUP)
    iStatus = _fiAwaitChildExit(iPid)
    _fnDrainPtyQuietly(iMasterFd)
    os.close(iMasterFd)
    _fnAssertSessionGone(connectionDocker, sContainerId, iSessionId)
    assert os.listdir(sRecordDirectory) == [], "the record outlived the session"


def test_a_killed_cli_is_cleaned_up_by_the_reaper_on_proof(
    tLiveContainer, tmp_path, monkeypatch,
):
    sName, sContainerId, connectionDocker = tLiveContainer
    iPid, iMasterFd, iSessionId, sRecordDirectory = _ftOpenShellWithAJob(
        tLiveContainer, tmp_path)
    os.kill(iPid, signal.SIGKILL)
    _fiAwaitChildExit(iPid)
    dictProbe = connectionDocker.fdictProbeProcessGroupMembers(sContainerId, iSessionId)
    assert dictProbe["bConclusive"] and dictProbe["iMemberCount"] >= 1, (
        "a SIGKILLed CLI should have left its session running: that is the leak")
    monkeypatch.setattr(cliShellContainment, "_S_CLI_SHELL_DIRECTORY", sRecordDirectory)
    dictOutcome = cliShellContainment.fdictReapOrphanedCliShells(connectionDocker)
    _fnDrainPtyQuietly(iMasterFd)
    os.close(iMasterFd)
    assert len(dictOutcome["listEnded"]) == 1, dictOutcome
    _fnAssertSessionGone(connectionDocker, sContainerId, iSessionId)
    assert os.listdir(sRecordDirectory) == []
