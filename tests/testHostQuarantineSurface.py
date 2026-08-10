"""What a quarantined HOST project tells the researcher, and offers them.

A quarantine is where vaibify has to say something it would rather not:
work it started cannot be proven finished. For a container that
sentence is about a container — stop it, reconcile, carry on. For a
host project it is about the machine the researcher is sitting at, and
the words have to change with it, because "reconciliation is required
before this container can be claimed" sends someone looking for a
container that does not exist.

Two things are proven here.

**The words.** A host quarantine names what is still detected, says why
ownership stays locked, names the levers, and — the part host mode is
never allowed to leave out — says that a command which detached into
its own session is invisible to all of it. The container sentence is
asserted alongside, unchanged, because the failure that matters is not
"the host wording is missing" but "the host wording replaced the
container's".

**The lever.** ``--terminate-recorded`` is the thing a host project has
instead of a container to stop, and it is crash-time only: a live hub
owns the records it wrote, and killing them behind its back is not
reconciliation, it is sabotage of a running process. The refusal for
that case names ``--force-abandon``, which is the lever that does apply.
"""

import os
import signal
import subprocess
import sys
import time
from unittest.mock import patch

import pytest
from click.testing import CliRunner

from vaibify.cli import commandReconcile
from vaibify.config import containerLock, operationJournal, registryManager


S_HOST_PROJECT = "quarantine-host-project"
S_CONTAINER_PROJECT = "quarantine-container-project"


@pytest.fixture(autouse=True)
def fixtureIsolateHostState(tmp_path, monkeypatch):
    """Redirect the registry and the container locks into tmp_path."""
    sRegistryDirectory = str(tmp_path / ".vaibify")
    monkeypatch.setattr(
        registryManager, "_S_REGISTRY_DIRECTORY", sRegistryDirectory,
    )
    monkeypatch.setattr(
        registryManager, "_S_REGISTRY_PATH",
        os.path.join(sRegistryDirectory, "registry.json"),
    )
    monkeypatch.setattr(
        containerLock, "_S_LOCK_DIRECTORY", str(tmp_path / "locks"),
    )


def _fnRegisterProject(tmp_path, sProjectName, sMode):
    """Create a project directory and register it in the given mode."""
    sProjectDirectory = str(tmp_path / sProjectName)
    os.makedirs(sProjectDirectory, exist_ok=True)
    with open(
        os.path.join(sProjectDirectory, "vaibify.yml"), "w",
    ) as fileConfig:
        fileConfig.write(f"projectName: {sProjectName}\n")
    registryManager.fnAddProject(sProjectDirectory, sMode=sMode)


# ── The words ────────────────────────────────────────────────────────


@pytest.mark.falsification
def testAHostQuarantineNamesTheMachineAndWhatCannotBeSeen(tmp_path):
    """The host sentence, including the limit it must never omit.

    Kills: giving a host project the container sentence, which tells a
    researcher to reconcile a container they do not have and says
    nothing about what vaibify cannot detect.
    """
    _fnRegisterProject(tmp_path, S_HOST_PROJECT, "host")
    error = containerLock.ContainerQuarantinedError(
        S_HOST_PROJECT, "holder pid 4242 is still alive",
    )
    sMessage = str(error)
    assert "Host run not settled" in sMessage, sMessage
    assert "process group" in sMessage, sMessage
    assert "ownership remains locked" in sMessage, sMessage
    assert "--terminate-recorded" in sMessage, sMessage
    assert "detached into a new session" in sMessage, (
        "a host quarantine claimed more than host mode can prove: "
        f"{sMessage}"
    )
    assert "container can be claimed" not in sMessage, (
        f"a host project was told to reconcile a container: {sMessage}"
    )


@pytest.mark.falsification
def testAContainerQuarantineKeepsItsOwnSentence(tmp_path):
    """The other direction: containers are not given host wording.

    Kills: making the host sentence unconditional, which would tell
    every containerized researcher to terminate recorded host
    processes for a project whose work runs somewhere else entirely.
    """
    _fnRegisterProject(tmp_path, S_CONTAINER_PROJECT, "container")
    sMessage = str(containerLock.ContainerQuarantinedError(
        S_CONTAINER_PROJECT, "exec 9f is still running",
    ))
    assert "container can be claimed" in sMessage, sMessage
    assert "--terminate-recorded" not in sMessage, sMessage
    assert "detached into a new session" not in sMessage, sMessage


# ── The lever ────────────────────────────────────────────────────────


# A sleeper nobody in THIS process tree will reap. A pytest-owned
# child that is killed becomes a zombie, and a zombie still answers
# ``os.kill(pid, 0)`` — so the journal probe would read "the recorded
# writer is still alive" immediately after a successful termination,
# and the test would be measuring pytest's parenthood rather than the
# lever. At crash time there is by definition no parent: the process
# vaibify started is an orphan, init reaps it, and the probe sees it
# gone. The double fork reproduces that; the middle process prints the
# grandchild's pid and exits.
_S_ORPHAN_LAUNCHER = (
    "import os, sys, time\n"
    "iPid = os.fork()\n"
    "if iPid == 0:\n"
    "    os.setsid()\n"
    "    os.close(0)\n"
    "    os.close(1)\n"
    "    os.close(2)\n"
    "    time.sleep(120)\n"
    "    os._exit(0)\n"
    "sys.stdout.write(str(iPid))\n"
    "sys.stdout.flush()\n"
)


def _fiStartOrphanedSleeper():
    """Return the pid of an orphaned sleeper leading its own session."""
    processLauncher = subprocess.Popen(
        [sys.executable, "-c", _S_ORPHAN_LAUNCHER],
        stdout=subprocess.PIPE,
    )
    baOutput, _ = processLauncher.communicate(timeout=15)
    processLauncher.wait(timeout=10)
    return int(baOutput.decode().strip())


def _fnJournalHostExecRecord(sProjectName, iPid):
    """Journal an in-flight host-exec record naming a live process."""
    sOperationId = operationJournal.fsPrepareOperation(
        sProjectName, "host-exec", "pipeline-step:A03",
    )
    operationJournal.fnPromoteOperationToInFlight(
        sProjectName, sOperationId,
        {"iHolderPid": iPid, "iHolderProcessGroup": iPid},
    )


@pytest.mark.falsification
def testTerminateRecordedSignalsTheJournaledGroupThenReproves(tmp_path):
    """The whole lever, end to end, against a real process.

    The run is terminated, the journal is then provable, and the
    project comes out of quarantine — which is the only sequence that
    is any use to a researcher. A terminate that left the record
    unsettled would be a button that changes the machine and not the
    dashboard.

    Kills: reporting the termination without re-running the proof, so
    the project stays quarantined after the thing quarantining it has
    been stopped.
    """
    _fnRegisterProject(tmp_path, S_HOST_PROJECT, "host")
    iSleeperPid = _fiStartOrphanedSleeper()
    try:
        _fnJournalHostExecRecord(S_HOST_PROJECT, iSleeperPid)
        result = CliRunner().invoke(
            commandReconcile.fnReconcileCommand,
            [S_HOST_PROJECT, "--terminate-recorded", "--yes"],
        )
        assert "Terminated 1 recorded process group(s)" in result.output, (
            result.output
        )
        assert _fbProcessGroupGone(iSleeperPid), (
            "the recorded process group outlived the termination"
        )
        assert result.exit_code == 0, result.output
        assert "claimable again" in result.output, (
            "the proof was not re-run after the termination, so the "
            f"project is still quarantined: {result.output}"
        )
    finally:
        _fnKillLeftoverSleeper(iSleeperPid)


@pytest.mark.falsification
def testTerminateRecordedRefusesAContainerProject(tmp_path):
    """A container is settled by stopping it, not by signalling pids.

    Kills: dropping the mode check, which sends a containerized
    project's journal — whose holder pids are the HUB's own workers —
    to a process-group terminator.
    """
    _fnRegisterProject(tmp_path, S_CONTAINER_PROJECT, "container")
    result = CliRunner().invoke(
        commandReconcile.fnReconcileCommand,
        [S_CONTAINER_PROJECT, "--terminate-recorded", "--yes"],
    )
    assert result.exit_code == 2, result.output
    assert "is for host projects" in result.output


@pytest.mark.falsification
def testTerminateRecordedRefusesWhileALiveHubHoldsTheProject(tmp_path):
    """A live hub owns the records it wrote; this lever is crash-time.

    Kills: letting the flag through to the terminator while a hub is
    live, which kills processes that hub is still streaming from and
    leaves it reading pipes whose writers vanished.
    """
    _fnRegisterProject(tmp_path, S_HOST_PROJECT, "host")
    # The holder is stubbed because ``fdictReadLockHolder`` reports a
    # lock held by the CURRENT process as unheld, by design — so a
    # same-process test cannot stand in for a live hub by taking the
    # flock. What is under test is the routing decision, which reads
    # exactly this answer.
    with patch.object(
        commandReconcile, "fdictReadLockHolder",
        lambda sName: {"iPid": 4242, "iPort": 8099},
    ):
        result = CliRunner().invoke(
            commandReconcile.fnReconcileCommand,
            [S_HOST_PROJECT, "--terminate-recorded", "--yes"],
        )
    assert result.exit_code == 2, result.output
    assert "--force-abandon" in result.output, result.output


def testTerminateRecordedOnAQuietProjectSaysSoAndClears(tmp_path):
    """Nothing recorded is a clean answer, not an error."""
    _fnRegisterProject(tmp_path, S_HOST_PROJECT, "host")
    result = CliRunner().invoke(
        commandReconcile.fnReconcileCommand,
        [S_HOST_PROJECT, "--terminate-recorded", "--yes"],
    )
    assert result.exit_code == 0, result.output
    assert "Terminated 0 recorded process group(s)" in result.output
    assert "no journal marker" in result.output


def _fbProcessGroupGone(iProcessGroup, fTimeoutSeconds=10.0):
    """Poll until the group is empty; False if it never empties.

    Polling rather than ``wait()``: an orphaned grandchild is not this
    process's child, so there is nothing to wait on — which is the
    whole reason it stands in for a crash-time record.
    """
    fDeadline = time.monotonic() + fTimeoutSeconds
    while time.monotonic() < fDeadline:
        try:
            os.killpg(iProcessGroup, 0)
        except ProcessLookupError:
            return True
        except PermissionError:
            return False
        time.sleep(0.05)
    return False


def _fnKillLeftoverSleeper(iProcessGroup):
    """Make sure a test's sleeper cannot outlive the test."""
    try:
        os.killpg(iProcessGroup, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        return
    _fbProcessGroupGone(iProcessGroup)
