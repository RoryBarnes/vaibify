"""A `vaibify connect` shell is recorded on the host and ended on exit.

The CLI's ``docker exec -it`` survived its client: close the window and
the shell, and every agent started in it, ran on with nothing recording
them. The exec is now wrapped like the dashboard terminal, its session
leader recorded on the host, and every exit path ends the session and
proves it empty before the record goes. A CLI killed outright leaves
its record for the hub's reaper, which acts only once the CLI pid is
provably dead and the leader's identity still matches.
"""

import json
import os
import signal
import subprocess
import sys

import pytest

from vaibify.config.processLiveness import fsNowClaimIso
from vaibify.gui import cliShellContainment, terminalContainment

S_NAME = "cliShellProject"
S_CONTAINER_ID = "d1ce" * 16
I_SESSION = 777


class ConnectionDockerFake:
    """A daemon with one running container and one in-container session."""

    def __init__(self, bConclusive=True, sLeaderClock="123456", bRunning=True):
        self.listSignals = []
        self.bGroupEmpty = False
        self.bConclusive = bConclusive
        self.sLeaderClock = sLeaderClock
        self.bRunning = bRunning

    def flistGetRunningContainers(self):
        return [{"sName": S_NAME, "sContainerId": S_CONTAINER_ID}]

    def fdictReadContainerState(self, sContainerId):
        assert sContainerId == S_CONTAINER_ID
        return {"Running": self.bRunning} if self.bRunning else None

    def fbaFetchFile(self, sContainerId, sFilePath):
        assert sContainerId == S_CONTAINER_ID
        assert sFilePath == f"/proc/{I_SESSION}/stat"
        if not self.sLeaderClock:
            raise FileNotFoundError(sFilePath)
        listFields = ["S", "1", str(I_SESSION), str(I_SESSION), "0", "-1",
                      "4194560", "0", "0", "0", "0", "0", "0", "0", "0", "20",
                      "0", "1", "0", self.sLeaderClock, "1234", "5678"]
        return f"{I_SESSION} (sh) {' '.join(listFields)}\n".encode()

    def fnSignalProcessGroupMembers(self, sContainerId, iProcessGroup, sSignalName):
        self.listSignals.append((iProcessGroup, sSignalName))
        self.bGroupEmpty = True

    def fdictProbeProcessGroupMembers(self, sContainerId, iProcessGroup):
        if not self.bConclusive:
            return {"bConclusive": False, "iMemberCount": -1, "sDetail": "probe failed"}
        return {"bConclusive": True, "iMemberCount": 0 if self.bGroupEmpty else 1,
                "sDetail": ""}


@pytest.fixture(autouse=True)
def fnShortenWaits(monkeypatch):
    monkeypatch.setattr(terminalContainment, "F_TERMINATE_WAIT_SECONDS", 0.05)
    monkeypatch.setattr(terminalContainment, "F_KILL_WAIT_SECONDS", 0.05)


def _flistRecords():
    sDirectory = cliShellContainment._S_CLI_SHELL_DIRECTORY
    return sorted(os.listdir(sDirectory)) if os.path.isdir(sDirectory) else []


def test_the_record_names_the_session_the_cli_and_their_clocks():
    connectionDocker = ConnectionDockerFake()
    sPath = cliShellContainment._fsWriteCliShellRecord(
        connectionDocker, S_NAME, S_CONTAINER_ID, I_SESSION)
    assert os.path.basename(sPath) == f"{S_CONTAINER_ID}-{I_SESSION}.json"
    with open(sPath) as fileHandle:
        dictRecord = json.load(fileHandle)
    assert dictRecord["iCliPid"] == os.getpid()
    assert dictRecord["iSessionId"] == I_SESSION
    assert dictRecord["sSessionStartClock"] == "123456"
    assert dictRecord["sCliStartedIso"]
    assert oct(os.stat(os.path.dirname(sPath)).st_mode & 0o777) == "0o700"
    cliShellContainment._fnUnlinkCliShellRecord(sPath)
    assert _flistRecords() == []


class _ProcessChildFake:
    def __init__(self, fnOnWait=None):
        self.fnOnWait = fnOnWait
        self.bKilled = False

    def wait(self, timeout=None):
        if self.fnOnWait is not None and timeout is None:
            self.fnOnWait()
        return 0

    def kill(self):
        self.bKilled = True


def test_a_hangup_during_the_wait_ends_the_session_and_deletes_the_record():
    connectionDocker = ConnectionDockerFake()
    sPath = cliShellContainment._fsWriteCliShellRecord(
        connectionDocker, S_NAME, S_CONTAINER_ID, I_SESSION)

    def fnHangupOurselves():
        os.kill(os.getpid(), signal.SIGHUP)

    cliShellContainment._fnAwaitCliExecThenEndItsSession(
        _ProcessChildFake(fnHangupOurselves), connectionDocker, S_NAME,
        S_CONTAINER_ID, I_SESSION, sPath)
    assert (I_SESSION, "TERM") in connectionDocker.listSignals
    assert _flistRecords() == [], "the record outlived a proven-empty session"
    assert signal.getsignal(signal.SIGHUP) is signal.SIG_DFL or callable(
        signal.getsignal(signal.SIGHUP))


def test_a_normal_exit_ends_the_session_too():
    connectionDocker = ConnectionDockerFake()
    sPath = cliShellContainment._fsWriteCliShellRecord(
        connectionDocker, S_NAME, S_CONTAINER_ID, I_SESSION)
    cliShellContainment._fnAwaitCliExecThenEndItsSession(
        _ProcessChildFake(), connectionDocker, S_NAME, S_CONTAINER_ID,
        I_SESSION, sPath)
    assert connectionDocker.listSignals[0] == (I_SESSION, "TERM")
    assert _flistRecords() == []


def test_an_indeterminate_proof_keeps_the_record_for_the_reaper():
    connectionDocker = ConnectionDockerFake(bConclusive=False)
    sPath = cliShellContainment._fsWriteCliShellRecord(
        connectionDocker, S_NAME, S_CONTAINER_ID, I_SESSION)
    cliShellContainment._fnAwaitCliExecThenEndItsSession(
        _ProcessChildFake(), connectionDocker, S_NAME, S_CONTAINER_ID,
        I_SESSION, sPath)
    assert (I_SESSION, "KILL") in connectionDocker.listSignals
    assert _flistRecords() == [os.path.basename(sPath)]


def _fnWriteRecord(iCliPid, sStartedIso, sClock="123456"):
    from vaibify.config import pidFileRegistry
    pidFileRegistry.fnEnsureDirectory(cliShellContainment._S_CLI_SHELL_DIRECTORY)
    sPath = os.path.join(cliShellContainment._S_CLI_SHELL_DIRECTORY,
                         f"{S_CONTAINER_ID}-{I_SESSION}.json")
    with open(sPath, "w") as fileHandle:
        json.dump({"sContainerId": S_CONTAINER_ID, "sContainerName": S_NAME,
                   "iSessionId": I_SESSION, "sSessionStartClock": sClock,
                   "iCliPid": iCliPid, "sCliStartedIso": sStartedIso}, fileHandle)
    return sPath


def _fiProvablyDeadPid():
    processChild = subprocess.Popen([sys.executable, "-c", "pass"])
    processChild.wait()
    return processChild.pid


def test_the_reaper_leaves_a_live_clis_record_alone():
    _fnWriteRecord(os.getpid(), fsNowClaimIso())
    connectionDocker = ConnectionDockerFake()
    dictOutcome = cliShellContainment.fdictReapOrphanedCliShells(connectionDocker)
    assert len(dictOutcome["listKept"]) == 1
    assert connectionDocker.listSignals == []
    assert len(_flistRecords()) == 1


def test_the_reaper_ends_the_session_of_a_dead_cli_and_deletes_the_record():
    _fnWriteRecord(_fiProvablyDeadPid(), fsNowClaimIso())
    connectionDocker = ConnectionDockerFake()
    dictOutcome = cliShellContainment.fdictReapOrphanedCliShells(connectionDocker)
    assert len(dictOutcome["listEnded"]) == 1
    assert (I_SESSION, "TERM") in connectionDocker.listSignals
    assert _flistRecords() == []


def test_the_reaper_refuses_a_recycled_leader_pid():
    _fnWriteRecord(_fiProvablyDeadPid(), fsNowClaimIso(), sClock="123456")
    connectionDocker = ConnectionDockerFake(sLeaderClock="999999")
    dictOutcome = cliShellContainment.fdictReapOrphanedCliShells(connectionDocker)
    assert len(dictOutcome["listDeleted"]) == 1
    assert connectionDocker.listSignals == [], "a recycled pid was signalled"
    assert _flistRecords() == []


def test_the_reaper_drops_the_record_of_a_gone_container_without_signalling():
    _fnWriteRecord(_fiProvablyDeadPid(), fsNowClaimIso())
    connectionDocker = ConnectionDockerFake(bRunning=False)
    dictOutcome = cliShellContainment.fdictReapOrphanedCliShells(connectionDocker)
    assert len(dictOutcome["listDeleted"]) == 1
    assert connectionDocker.listSignals == []


def test_the_reaper_keeps_a_record_whose_proof_is_indeterminate():
    _fnWriteRecord(_fiProvablyDeadPid(), fsNowClaimIso())
    connectionDocker = ConnectionDockerFake(bConclusive=False)
    dictOutcome = cliShellContainment.fdictReapOrphanedCliShells(connectionDocker)
    assert len(dictOutcome["listKept"]) == 1
    assert len(_flistRecords()) == 1


def test_a_malformed_record_is_dropped():
    from vaibify.config import pidFileRegistry
    pidFileRegistry.fnEnsureDirectory(cliShellContainment._S_CLI_SHELL_DIRECTORY)
    with open(os.path.join(cliShellContainment._S_CLI_SHELL_DIRECTORY,
                           "junk.json"), "w") as fileHandle:
        fileHandle.write("{}")
    dictOutcome = cliShellContainment.fdictReapOrphanedCliShells(ConnectionDockerFake())
    assert len(dictOutcome["listDeleted"]) == 1
    assert _flistRecords() == []


def test_a_container_that_is_not_running_is_refused_with_the_remedy():
    class _NothingRunning:
        def flistGetRunningContainers(self):
            return []
    with pytest.raises(terminalContainment.TerminalContainmentError, match="vaibify start"):
        cliShellContainment._fsRunningContainerIdForName(_NothingRunning(), S_NAME)


def test_the_hub_reaper_declines_without_a_daemon_and_counts_with_one():
    from vaibify.gui import appFactory
    _fnWriteRecord(_fiProvablyDeadPid(), fsNowClaimIso())
    dictDeclined = appFactory._fdictReapOrphanedCliShells({"docker": None})
    assert dictDeclined["sOutcome"] == "forbidden" and dictDeclined["sRemedy"]
    dictRan = appFactory._fdictReapOrphanedCliShells({"docker": ConnectionDockerFake()})
    assert dictRan == {"sOutcome": "ran", "iRemoved": 1, "sReason": "", "sRemedy": ""}
