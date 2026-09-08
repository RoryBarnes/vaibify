"""A gateway-killed command is never asked for an exit code.

The daemon's answer for a SIGKILL'd exec is a race: 137 when the exec
has settled, an inspect that blocks out the whole 60-second client
timeout when it has not -- both measured live, back to back
(2026-09-02), which is what made the runaway-cap live test
intermittent and put a swallowed minute-long stall on every breach
path. Both gateways' docstrings promise None for a killed command;
skipping the ask makes the promise true by construction.
"""

import pytest

from vaibify.docker import disposableContainer
from vaibify.docker import disposableSpecification
from vaibify.gui import agentCouncilDockerGateway
from vaibify.gui import agentCouncilRunner


class _FakeRecordingApi:
    """Answers 137 and records whether anyone asked."""

    def __init__(self):
        self.listExecInspected = []

    def exec_inspect(self, sExecId):
        self.listExecInspected.append(sExecId)
        return {"ExitCode": 137}

    def inspect_container(self, sContainerId):
        return {"State": {"OOMKilled": False, "Running": False}}

    def kill(self, sContainerId):
        pass


class _FakeDockerClient:
    def __init__(self):
        self.api = _FakeRecordingApi()


class _FakeExecSocket:
    def close(self):
        pass


def _fdictPump(bBreach):
    return {
        "baCaptured": b"x" * 16,
        "bOutputCapExceeded": bBreach,
        "bDeadlineExceeded": False,
        "bStalled": False,
        "fStallSeconds": 600.0,
    }


def _fnWireDisposable(monkeypatch, dockerFake, bBreach):
    monkeypatch.setattr(
        disposableContainer, "_fiReadOomKillCount",
        lambda dockerClient, sContainerId: 0)
    monkeypatch.setattr(
        disposableContainer, "_ftStartExecStream",
        lambda *listArgs, **dictKw: ("exec1", _FakeExecSocket()))
    monkeypatch.setattr(
        disposableSpecification, "fdictPumpBoundedExecStream",
        lambda *listArgs, **dictKw: _fdictPump(bBreach))
    return {
        "dockerDisposable": dockerFake,
        "dictHandlesById": {"h1": {"sContainerId": "c1"}},
    }


def _fnWireCouncil(monkeypatch, dockerFake, bBreach):
    monkeypatch.setattr(
        agentCouncilDockerGateway, "_fiReadOomKillCount",
        lambda dockerClient, sContainerId: 0)
    monkeypatch.setattr(
        agentCouncilDockerGateway, "_ftStartExecStream",
        lambda *listArgs, **dictKw: ("exec1", _FakeExecSocket()))
    monkeypatch.setattr(
        agentCouncilRunner, "fdictPumpBoundedExecStream",
        lambda *listArgs, **dictKw: _fdictPump(bBreach))
    return {
        "dockerCouncil": dockerFake,
        "dictHandlesById": {"h1": {"sContainerId": "c1"}},
    }


@pytest.mark.falsification
def test_a_breached_disposable_command_is_not_asked_for_an_exit_code(
        monkeypatch):
    """The fake would answer 137; the contract says nobody asks.

    Kills: make the exec_inspect in _fdictDescribeCommandOutcome
    unconditional again, which launders the daemon race into the
    outcome -- the recorded ask and the 137 both betray it.
    """
    dockerFake = _FakeDockerClient()
    dictGateway = _fnWireDisposable(monkeypatch, dockerFake, bBreach=True)
    dictOutcome = disposableContainer.fdictExecuteBoundedCommand(
        dictGateway, "h1", ["true"])
    assert dictOutcome["bOutputCapExceeded"] is True
    assert dictOutcome["iExitCode"] is None
    assert dockerFake.api.listExecInspected == [], (
        "a gateway-killed command was asked for an exit code"
    )


@pytest.mark.falsification
def test_a_breached_council_turn_is_not_asked_for_an_exit_code(
        monkeypatch):
    """The council lane's twin of the assertion above.

    Kills: make the exec_inspect in fdictExecuteBoundedTurn
    unconditional again.
    """
    dockerFake = _FakeDockerClient()
    dictGateway = _fnWireCouncil(monkeypatch, dockerFake, bBreach=True)
    dictTurn = agentCouncilDockerGateway.fdictExecuteBoundedTurn(
        dictGateway, "h1", ["true"])
    assert dictTurn["bOutputCapExceeded"] is True
    assert dictTurn["iExitCode"] is None
    assert dockerFake.api.listExecInspected == [], (
        "a gateway-killed turn was asked for an exit code"
    )


def test_an_unbreached_command_still_reports_its_exit_code(monkeypatch):
    """The skip is scoped to breaches; ordinary exits thread through."""
    dockerFake = _FakeDockerClient()
    dictGateway = _fnWireDisposable(monkeypatch, dockerFake, bBreach=False)
    dictOutcome = disposableContainer.fdictExecuteBoundedCommand(
        dictGateway, "h1", ["true"])
    assert dictOutcome["iExitCode"] == 137
    assert dockerFake.api.listExecInspected == ["exec1"]


def test_an_unbreached_turn_still_reports_its_exit_code(monkeypatch):
    dockerFake = _FakeDockerClient()
    dictGateway = _fnWireCouncil(monkeypatch, dockerFake, bBreach=False)
    dictTurn = agentCouncilDockerGateway.fdictExecuteBoundedTurn(
        dictGateway, "h1", ["true"])
    assert dictTurn["iExitCode"] == 137
    assert dockerFake.api.listExecInspected == ["exec1"]


class _FakeAbsorbingApi:
    """A daemon that absorbs the first kill, like the wedged one measured."""

    def __init__(self, iKillsToStop=2):
        self._iKillsToStop = iKillsToStop
        self.iKillsReceived = 0
        self.bRunning = True

    def kill(self, sContainerId):
        self.iKillsReceived += 1
        if self.iKillsReceived >= self._iKillsToStop:
            self.bRunning = False

    def inspect_container(self, sContainerId):
        return {"State": {"Running": self.bRunning}}


class _FakeAbsorbingClient:
    def __init__(self, iKillsToStop=2):
        self.api = _FakeAbsorbingApi(iKillsToStop)


@pytest.mark.falsification
def test_the_disposable_kill_retries_until_the_daemon_confirms(
        monkeypatch):
    """One absorbed kill must not leave the runaway running.

    The fake reproduces the wedged daemon measured live: the first
    kill is absorbed, the second lands. A single-attempt kill leaves
    the container running and the breach unenforced.

    Kills: bound the disposable kill loop at one attempt.
    """
    monkeypatch.setattr(disposableContainer.time, "sleep", lambda f: None)
    dockerFake = _FakeAbsorbingClient(iKillsToStop=2)
    disposableContainer._fnKillContainerQuietly(dockerFake, "c1")
    assert dockerFake.api.bRunning is False
    assert dockerFake.api.iKillsReceived == 2


@pytest.mark.falsification
def test_the_council_kill_retries_until_the_daemon_confirms(monkeypatch):
    """The council lane's twin of the retry assertion above.

    Kills: bound the council kill loop at one attempt.
    """
    monkeypatch.setattr(
        agentCouncilDockerGateway.time, "sleep", lambda f: None)
    dockerFake = _FakeAbsorbingClient(iKillsToStop=2)
    agentCouncilDockerGateway._fnKillContainerQuietly(dockerFake, "c1")
    assert dockerFake.api.bRunning is False
    assert dockerFake.api.iKillsReceived == 2


def test_a_confirmed_kill_stops_asking(monkeypatch):
    """A daemon that answers stopped is not killed again."""
    monkeypatch.setattr(disposableContainer.time, "sleep", lambda f: None)
    dockerFake = _FakeAbsorbingClient(iKillsToStop=1)
    disposableContainer._fnKillContainerQuietly(dockerFake, "c1")
    assert dockerFake.api.iKillsReceived == 1
