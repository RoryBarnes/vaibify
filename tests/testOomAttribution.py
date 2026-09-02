"""OOM attribution: the cgroup counter and the daemon flag, combined.

Exit 137 is SIGKILL and names no sender. Both disposable-container
lanes (the shadow rerun and the Agent Council) attribute it by reading
the container cgroup's own ``oom_kill`` counter around each command
and combining it with the daemon's ``State.OOMKilled`` flag. The
daemon flag alone missed an exec-level kill intermittently in CI
(docker-smoke, 2026-09-02, twice in one day): it is stamped by event
plumbing that races the read, where the kernel's counter is
incremented at the moment of the kill.

The pure halves live in ``disposableSpecification``; the wiring tests
here drive the REAL bounded-execution functions of both gateways with
counter values their defaults cannot produce, because a threaded
parameter can be accepted and dropped with every call site still
reading correctly.
"""

import pytest

from vaibify.docker import disposableContainer
from vaibify.docker import disposableSpecification
from vaibify.gui import agentCouncilDockerGateway
from vaibify.gui import agentCouncilRunner


S_CGROUP_V2_TEXT = """low 0
high 4
max 12
oom 3
oom_kill 3
oom_group_kill 0
"""

# cgroup v1 memory.oom_control: the ``oom_kill_disable`` line comes
# FIRST, and a prefix match reads its value instead of the counter's.
S_CGROUP_V1_TEXT = """oom_kill_disable 1
under_oom 0
oom_kill 0
"""


@pytest.mark.falsification
def test_the_parser_matches_the_counter_token_exactly():
    """oom_kill_disable also starts with oom_kill, and its value lies.

    Kills: relax the token comparison in fiParseOomKillCount to
    ``startswith("oom_kill")``, which reads the v1 file's
    ``oom_kill_disable 1`` line as a kill count of 1.
    """
    assert disposableSpecification.fiParseOomKillCount(
        S_CGROUP_V1_TEXT) == 0
    assert disposableSpecification.fiParseOomKillCount(
        S_CGROUP_V2_TEXT) == 3


def test_an_unreadable_counter_is_none_never_zero():
    """None means "could not read", which is not a claim of no kills."""
    assert disposableSpecification.fiParseOomKillCount("") is None
    assert disposableSpecification.fiParseOomKillCount(
        "cat: /sys/fs/cgroup/memory.events: No such file") is None
    assert disposableSpecification.fiParseOomKillCount(
        "oom_kill not-a-number") is None


@pytest.mark.falsification
def test_a_risen_counter_concludes_oom_with_the_daemon_flag_quiet():
    """The kernel's ledger outranks the daemon's silence.

    This is the CI failure verbatim: the balloon died 137, the counter
    rose, and State.OOMKilled stayed False because the daemon's event
    plumbing had not stamped it.

    Kills: reduce fbConcludeOomKilled to the State flag alone, which
    reintroduces the intermittent false negative this change removes.
    """
    assert disposableSpecification.fbConcludeOomKilled(0, 1, False) is True
    assert disposableSpecification.fbConcludeOomKilled(2, 3, False) is True


@pytest.mark.falsification
def test_an_unreadable_before_count_cannot_conclude_a_kill():
    """A container hosts many commands; an absolute count proves nothing.

    A prior command's legitimate OOM kill leaves the counter above
    zero forever. If a failed before-read were treated as zero, every
    later command in that container would be reported OOM-killed.

    Kills: treat None as 0 in fbConcludeOomKilled's counter half.
    """
    assert disposableSpecification.fbConcludeOomKilled(
        None, 1, False) is False
    assert disposableSpecification.fbConcludeOomKilled(
        1, None, False) is False


def test_a_flat_counter_and_a_quiet_flag_conclude_no_kill():
    assert disposableSpecification.fbConcludeOomKilled(1, 1, False) is False
    assert disposableSpecification.fbConcludeOomKilled(0, 0, False) is False


def test_the_daemon_flag_alone_still_concludes_a_kill():
    """The pid-1 kill: the container died, no counter exec can run."""
    assert disposableSpecification.fbConcludeOomKilled(
        None, None, True) is True


class _FakeDockerApi:
    def __init__(self, iExitCode, bStateOomKilled):
        self._iExitCode = iExitCode
        self._bStateOomKilled = bStateOomKilled

    def exec_inspect(self, sExecId):
        return {"ExitCode": self._iExitCode}

    def inspect_container(self, sContainerId):
        return {"State": {"OOMKilled": self._bStateOomKilled}}


class _FakeDockerClient:
    def __init__(self, iExitCode=137, bStateOomKilled=False):
        self.api = _FakeDockerApi(iExitCode, bStateOomKilled)


class _FakeExecSocket:
    def close(self):
        pass


def _ffnCounterReadSequence(listCounts):
    """A counter reader that answers the recorded values in order."""
    iterCounts = iter(listCounts)

    def _fiRead(dockerClient, sContainerId):
        return next(iterCounts)

    return _fiRead


def _fdictQuietPump(**dictOverrides):
    dictPumped = {
        "baCaptured": b"",
        "bOutputCapExceeded": False,
        "bDeadlineExceeded": False,
        "bStalled": False,
        "fStallSeconds": 600.0,
    }
    dictPumped.update(dictOverrides)
    return dictPumped


@pytest.mark.falsification
def test_the_disposable_command_threads_the_before_count(monkeypatch):
    """The snapshot survives the hop into the outcome assembler.

    The counter values (2 -> 3) are ones no default can produce, so a
    dropped parameter cannot pass by accident.

    Kills: replace the ``iOomKillsBefore`` argument of the
    ``_fdictDescribeCommandOutcome`` call with ``None``, which reads
    every kill as inconclusive and reports ``bOomKilled: False``.
    """
    dockerFake = _FakeDockerClient()
    dictGateway = {
        "dockerDisposable": dockerFake,
        "dictHandlesById": {"h1": {"sContainerId": "c1"}},
    }
    monkeypatch.setattr(
        disposableContainer, "_fiReadOomKillCount",
        _ffnCounterReadSequence([2, 3]))
    monkeypatch.setattr(
        disposableContainer, "_ftStartExecStream",
        lambda *listArgs, **dictKw: ("exec1", _FakeExecSocket()))
    monkeypatch.setattr(
        disposableSpecification, "fdictPumpBoundedExecStream",
        lambda *listArgs, **dictKw: _fdictQuietPump())
    dictOutcome = disposableContainer.fdictExecuteBoundedCommand(
        dictGateway, "h1", ["true"])
    assert dictOutcome["iExitCode"] == 137
    assert dictOutcome["bOomKilled"] is True


@pytest.mark.falsification
def test_the_council_turn_threads_the_before_count(monkeypatch):
    """The council lane's twin of the threading assertion above.

    Kills: replace the ``iOomKillsBefore`` argument of the council
    gateway's ``_fbConcludeOomKilledForContainer`` call with ``None``.
    """
    dockerFake = _FakeDockerClient()
    dictGateway = {
        "dockerCouncil": dockerFake,
        "dictHandlesById": {"h1": {"sContainerId": "c1"}},
    }
    monkeypatch.setattr(
        agentCouncilDockerGateway, "_fiReadOomKillCount",
        _ffnCounterReadSequence([2, 3]))
    monkeypatch.setattr(
        agentCouncilDockerGateway, "_ftStartExecStream",
        lambda *listArgs, **dictKw: ("exec1", _FakeExecSocket()))
    monkeypatch.setattr(
        agentCouncilRunner, "fdictPumpBoundedExecStream",
        lambda *listArgs, **dictKw: _fdictQuietPump())
    dictTurn = agentCouncilDockerGateway.fdictExecuteBoundedTurn(
        dictGateway, "h1", ["true"])
    assert dictTurn["iExitCode"] == 137
    assert dictTurn["bOomKilled"] is True


def test_a_flat_counter_reports_no_kill_through_the_disposable_lane(
        monkeypatch):
    """The negative direction of the wiring: no rise, no flag, False."""
    dockerFake = _FakeDockerClient()
    dictGateway = {
        "dockerDisposable": dockerFake,
        "dictHandlesById": {"h1": {"sContainerId": "c1"}},
    }
    monkeypatch.setattr(
        disposableContainer, "_fiReadOomKillCount",
        _ffnCounterReadSequence([2, 2]))
    monkeypatch.setattr(
        disposableContainer, "_ftStartExecStream",
        lambda *listArgs, **dictKw: ("exec1", _FakeExecSocket()))
    monkeypatch.setattr(
        disposableSpecification, "fdictPumpBoundedExecStream",
        lambda *listArgs, **dictKw: _fdictQuietPump())
    dictOutcome = disposableContainer.fdictExecuteBoundedCommand(
        dictGateway, "h1", ["true"])
    assert dictOutcome["bOomKilled"] is False
