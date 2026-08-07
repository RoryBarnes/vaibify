"""The Docker layer of the server-owned start (design §10b, slice 9).

Create-then-start, the killable launch, and the label-keyed cleanup with
its conclusiveness rule. The termination test drives a REAL child process
that ignores SIGTERM — a mocked ``Popen`` would agree with whatever the
code did, and the whole point of the escalation is that the polite signal
sometimes does not work. The label-scoping test drives a REAL daemon when
one is reachable: two throwaway containers, only one of them carrying the
reservation label.
"""

import os
import secrets
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from vaibify.docker import containerManager


S_RESERVATION_ID = "0123456789abcdef0123456789abcdef"
# A child that ignores every catchable termination signal, so only the
# KILL escalation can end it.
S_SIGNAL_IGNORING_CHILD = (
    "import signal, sys, time\n"
    "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
    "sys.stdout.write('ready\\n')\n"
    "sys.stdout.flush()\n"
    "time.sleep(60)\n"
)


def fconfigBuildMinimal(sProjectName="reservationProject"):
    """Return the smallest config the run-argument builder accepts."""
    return SimpleNamespace(
        sProjectName=sProjectName,
        sWorkspaceRoot="/workspace",
        sContainerUser="researcher",
        listPorts=[], listBindMounts=[], listSecrets=[],
        bNetworkIsolation=False, iCpuLimit=0, fMemoryLimitGigabytes=0.0,
        features=SimpleNamespace(
            bGpu=False, bClaude=False, bCodex=False, bGemini=False,
            bOpenCode=False, bCline=False, bOpenHands=False, bPi=False,
        ),
    )


@pytest.fixture(autouse=True)
def fixtureNoX11(monkeypatch):
    """Keep the host's X11 state out of the assembled argument lists."""
    monkeypatch.setattr(
        containerManager, "flistConfigureX11Args", lambda: [],
    )


# ------------------------------------------------------------------
# Create-then-start argument assembly.
# ------------------------------------------------------------------

def test_create_arguments_drop_the_run_only_flags():
    """``docker create`` takes no ``-d`` and must not carry ``--rm``.

    A created-but-unstarted container that removed itself would erase
    the very identity the write-ahead record exists to name.
    """
    saArgs = containerManager.flistBuildRunArgs(
        fconfigBuildMinimal(), bCreateOnly=True,
    )
    assert "-t" in saArgs
    assert "-d" not in saArgs
    assert "--rm" not in saArgs


def test_create_command_labels_the_container_with_the_reservation():
    """The reservation id rides an immutable label on the container."""
    listCommands = []
    with patch.object(
        containerManager, "_fsRunKillableDockerCommand",
        lambda saCommand, fnRegisterProcess=None: (
            listCommands.append(saCommand) or "created123"
        ),
    ):
        sContainerId = containerManager.fsCreateContainerForReservation(
            fconfigBuildMinimal(), S_RESERVATION_ID,
        )
    assert sContainerId == "created123"
    saCommand = listCommands[0]
    assert saCommand[:2] == ["docker", "create"]
    iLabelIndex = saCommand.index("--label")
    assert saCommand[iLabelIndex + 1] == (
        f"vaibify.reservation={S_RESERVATION_ID}"
    )
    assert saCommand[-2:] == ["sleep", "infinity"]


@pytest.mark.parametrize("sCandidate", [
    "", "not-hex", "0123456789abcdef0123456789abcde",
    "0123456789abcdef0123456789abcdef0", "--privileged",
    "0123456789abcdef0123456789abcde;", "0123456789ABCDEF0123456789ABCDEF",
])
def test_a_reservation_id_that_is_not_server_minted_hex_is_refused(
    sCandidate,
):
    """Label values are validated before they can reach a command line."""
    with pytest.raises(ValueError):
        containerManager.fnValidateReservationIdOrRaise(sCandidate)


def test_start_uses_the_recorded_container_id():
    """The second half starts exactly the container that was created."""
    listCommands = []
    with patch.object(
        containerManager, "_fsRunKillableDockerCommand",
        lambda saCommand, fnRegisterProcess=None: (
            listCommands.append(saCommand) or ""
        ),
    ):
        containerManager.fnStartCreatedContainer("created123")
    assert listCommands == [["docker", "start", "created123"]]


# ------------------------------------------------------------------
# The killable launch and its termination escalation.
# ------------------------------------------------------------------

def test_killable_command_hands_its_live_process_to_the_registrar():
    """The cancel path can only signal a process it was given."""
    listProcesses = []
    sOutput = containerManager._fsRunKillableDockerCommand(
        [sys.executable, "-c", "print('containerId')"],
        listProcesses.append,
    )
    assert sOutput == "containerId"
    assert len(listProcesses) == 1
    assert isinstance(listProcesses[0], subprocess.Popen)


def test_killable_command_raises_with_the_real_stderr():
    """A failing launch reports what Docker actually said."""
    with pytest.raises(RuntimeError, match="boom"):
        containerManager._fsRunKillableDockerCommand([
            sys.executable, "-c",
            "import sys; sys.stderr.write('boom'); sys.exit(1)",
        ])


def test_termination_escalates_to_kill_for_a_real_signal_ignoring_child():
    """TERM, bounded wait, KILL, then wait for the REAL exit.

    Driven against a live child that ignores SIGTERM, because the
    escalation exists precisely for the process the polite signal does
    not stop; a mocked process would confirm whatever the code did.
    """
    processChild = subprocess.Popen(
        [sys.executable, "-c", S_SIGNAL_IGNORING_CHILD],
        stdout=subprocess.PIPE, text=True,
    )
    assert processChild.stdout.readline().strip() == "ready", (
        "the child never reported its signal handler installed; a TERM "
        "sent before then would prove nothing"
    )
    try:
        dictOutcome = containerManager.fdictTerminateDockerProcess(
            processChild, fGraceSeconds=0.5,
        )
    finally:
        if processChild.poll() is None:
            processChild.kill()
            processChild.wait()
    assert dictOutcome["bTerminated"] is True
    assert dictOutcome["bKilled"] is True, (
        "a SIGTERM-ignoring process was reported as terminated without "
        "the KILL escalation"
    )
    assert dictOutcome["bExited"] is True
    assert processChild.poll() is not None, (
        "termination returned before the process had actually exited"
    )


def test_termination_of_an_already_exited_process_signals_nothing():
    """An exited launch needs no signal and reports its own return code."""
    processChild = subprocess.Popen([sys.executable, "-c", "raise SystemExit(3)"])
    processChild.wait()
    dictOutcome = containerManager.fdictTerminateDockerProcess(processChild)
    assert dictOutcome == {
        "bExited": True, "bTerminated": False, "bKilled": False,
        "iReturnCode": 3,
    }


# ------------------------------------------------------------------
# Label-keyed cleanup and the conclusiveness rule.
# ------------------------------------------------------------------

def _fnStubProbeAnswers(monkeypatch, listAnswers):
    """Answer each probe call in turn from a fixed, declared script."""
    listCalls = []

    def _ftAnswer(saCommand):
        listCalls.append(saCommand)
        return listAnswers[min(len(listCalls) - 1, len(listAnswers) - 1)]

    monkeypatch.setattr(containerManager, "_ftRunProbeCommand", _ftAnswer)
    return listCalls


def test_settlement_removes_the_labelled_container_and_confirms_absence(
    monkeypatch,
):
    """Found, removed, re-queried empty: that is a conclusive settlement."""
    listCalls = _fnStubProbeAnswers(monkeypatch, [
        (True, "partial123\n"), (True, ""), (True, ""),
    ])
    dictSettlement = containerManager.fdictSettleReservationContainers(
        S_RESERVATION_ID, bLaunchWasKilled=True,
    )
    assert dictSettlement["bConclusive"] is True
    assert dictSettlement["listRemovedContainerIds"] == ["partial123"]
    assert ["docker", "rm", "-f", "partial123"] in listCalls


def test_settlement_is_inconclusive_when_the_daemon_does_not_answer(
    monkeypatch,
):
    """An unanswered query is not "no such container"; it is unknown."""
    _fnStubProbeAnswers(monkeypatch, [(False, "")])
    dictSettlement = containerManager.fdictSettleReservationContainers(
        S_RESERVATION_ID, bLaunchWasKilled=False,
    )
    assert dictSettlement["bConclusive"] is False
    assert "did not answer" in dictSettlement["sDetail"]


def test_a_killed_create_leaving_nothing_behind_is_inconclusive(
    monkeypatch,
):
    """Killing the CLI does not prove the daemon abandoned the request.

    Design §10b: "reconcile long enough" is not a safety condition. With
    no labelled container to point at, the honest answer is uncertainty
    — which the caller must turn into a quarantine, never a clean start.
    """
    monkeypatch.setattr(
        containerManager, "_F_SETTLEMENT_WINDOW_SECONDS", 0.2,
    )
    monkeypatch.setattr(
        containerManager, "_F_SETTLEMENT_POLL_SECONDS", 0.05,
    )
    _fnStubProbeAnswers(monkeypatch, [(True, "")])
    dictSettlement = containerManager.fdictSettleReservationContainers(
        S_RESERVATION_ID, bLaunchWasKilled=True,
    )
    assert dictSettlement["bConclusive"] is False
    assert "may still create one" in dictSettlement["sDetail"]


def test_a_clean_create_failure_leaving_nothing_behind_is_conclusive(
    monkeypatch,
):
    """A launch that exited on its own has a daemon answer to trust."""
    _fnStubProbeAnswers(monkeypatch, [(True, ""), (True, "")])
    dictSettlement = containerManager.fdictSettleReservationContainers(
        S_RESERVATION_ID, bLaunchWasKilled=False,
    )
    assert dictSettlement["bConclusive"] is True
    assert dictSettlement["listRemovedContainerIds"] == []


def test_a_container_appearing_late_is_still_removed(monkeypatch):
    """The bounded window catches a container the daemon finished late."""
    monkeypatch.setattr(
        containerManager, "_F_SETTLEMENT_WINDOW_SECONDS", 1.0,
    )
    monkeypatch.setattr(
        containerManager, "_F_SETTLEMENT_POLL_SECONDS", 0.05,
    )
    _fnStubProbeAnswers(monkeypatch, [
        (True, ""), (True, "late456\n"), (True, ""), (True, ""),
    ])
    dictSettlement = containerManager.fdictSettleReservationContainers(
        S_RESERVATION_ID, bLaunchWasKilled=True,
    )
    assert dictSettlement["bConclusive"] is True
    assert dictSettlement["listRemovedContainerIds"] == ["late456"]


def test_a_survivor_after_removal_is_inconclusive(monkeypatch):
    """A label that still resolves after ``rm -f`` is not settled."""
    _fnStubProbeAnswers(monkeypatch, [(True, "stubborn789\n")])
    dictSettlement = containerManager.fdictSettleReservationContainers(
        S_RESERVATION_ID, bLaunchWasKilled=False,
    )
    assert dictSettlement["bConclusive"] is False
    assert "survived removal" in dictSettlement["sDetail"]


# ------------------------------------------------------------------
# Real daemon: the label scopes the removal.
# ------------------------------------------------------------------

def fnRequireDockerCommandLineAnswers():
    """Skip when the docker CLI cannot answer, unless the run demanded it.

    The start path shells out to the docker CLI, so THAT is what must be
    reachable here — ``testDockerConnectionLive``'s probe asks docker-py,
    which resolves a different socket on a context-based install and
    would skip a test the CLI could have run. The skip-versus-fail
    decision itself is the shared one, so ``VAIBIFY_REQUIRE_DOCKER_DAEMON``
    still turns a convenience skip into a failure and no lane can report
    a false green.
    """
    from tests.testDockerConnectionLive import (
        S_OUTCOME_FAIL, S_OUTCOME_PROCEED, S_REQUIRE_DAEMON_ENV,
        fsDaemonRequirementOutcome,
    )
    resultProcess = subprocess.run(
        ["docker", "info", "--format", "{{.ServerVersion}}"],
        capture_output=True, text=True,
    )
    sOutcome = fsDaemonRequirementOutcome(
        resultProcess.returncode == 0,
        bool(os.environ.get(S_REQUIRE_DAEMON_ENV)),
    )
    if sOutcome == S_OUTCOME_PROCEED:
        return
    if sOutcome == S_OUTCOME_FAIL:
        pytest.fail(
            "The docker CLI does not answer, but "
            f"{S_REQUIRE_DAEMON_ENV} is set: this run was required to "
            "exercise the real start path, so skipping would report a "
            "false green."
        )
    pytest.skip("no docker daemon reachable from the docker CLI")

@pytest.mark.docker_live
def test_only_the_reservation_labelled_container_is_removed():
    """A cleanup must never touch another incarnation of the container.

    Two throwaway containers exist; only one carries the reservation
    label. The settlement must remove exactly that one and leave the
    other running, which is the whole reason cleanup keys on an
    immutable label rather than on the container name.
    """
    fnRequireDockerCommandLineAnswers()
    sReservationId = secrets.token_hex(16)
    sSuffix = secrets.token_hex(4)
    sLabelledName = f"vaibifyStartLabelled{sSuffix}"
    sBystanderName = f"vaibifyStartBystander{sSuffix}"
    _fnRunThrowawayContainer(sLabelledName, [
        "--label", f"vaibify.reservation={sReservationId}",
    ])
    _fnRunThrowawayContainer(sBystanderName, [])
    try:
        dictSettlement = containerManager.fdictSettleReservationContainers(
            sReservationId, bLaunchWasKilled=False,
        )
        assert dictSettlement["bConclusive"] is True, dictSettlement
        assert not _fbContainerExists(sLabelledName), (
            "the labelled container survived its reservation cleanup"
        )
        assert _fbContainerExists(sBystanderName), (
            "the cleanup removed a container that carried no reservation "
            "label — it is keying on something other than the label"
        )
    finally:
        for sName in (sLabelledName, sBystanderName):
            subprocess.run(
                ["docker", "rm", "-f", sName],
                capture_output=True, text=True,
            )


def _fnRunThrowawayContainer(sName, listExtraArgs):
    """Start a tiny, uniquely named container for a live test."""
    resultProcess = subprocess.run(
        ["docker", "run", "-d", "--name", sName] + listExtraArgs
        + ["alpine:3.20", "sleep", "120"],
        capture_output=True, text=True,
    )
    assert resultProcess.returncode == 0, resultProcess.stderr


def _fbContainerExists(sName):
    """Return True when a container of this exact name still exists."""
    resultProcess = subprocess.run(
        ["docker", "ps", "-a", "-q", "--filter", f"name=^{sName}$"],
        capture_output=True, text=True,
    )
    return bool(resultProcess.stdout.strip())
