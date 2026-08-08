"""Tests for the ``vaibify reconcile`` CLI (design §8, 3c).

Discovery picks the path: with no live flock holder the crash-time
transaction runs directly; with a live holder the request routes over
that hub's host control socket. The routing test here is real end to
end: a REAL child process holds the container flock, a REAL Unix
socket serves the REAL handlers, and the REAL CLI entry function
drives the round trip.
"""

import asyncio
import multiprocessing
import os
import subprocess
import sys
import tempfile
import time
from types import SimpleNamespace

import click
import pytest
from click.testing import CliRunner

from vaibify.cli import commandReconcile
from vaibify.cli.commandReconcile import fiRunReconcileCommand
from vaibify.config import containerLock, operationJournal
from vaibify.gui import containerOwnership, hostControlChannel

S_PROJECT = "demo"
I_HUB_PORT = 8123


@pytest.fixture(autouse=True)
def fixtureIsolateJournalAndLockDirs(tmp_path, monkeypatch):
    """Redirect ~/.vaibify/journal and ~/.vaibify/locks to tmp_path."""
    monkeypatch.setattr(
        operationJournal, "_S_JOURNAL_DIRECTORY", str(tmp_path / "journal"),
    )
    monkeypatch.setattr(
        containerLock, "_S_LOCK_DIRECTORY", str(tmp_path / "locks"),
    )
    return tmp_path


@pytest.fixture(autouse=True)
def fixtureNeverTouchARealDockerDaemon(monkeypatch):
    """Keep the CLI hermetic: no test may reach a live Docker daemon."""
    monkeypatch.setattr(
        commandReconcile, "_fconnectionCreateDockerQuietly", lambda: None,
    )


@pytest.fixture
def fixtureShortControlDirectory(monkeypatch):
    """Point the control directory at a path short enough for AF_UNIX."""
    sDirectory = tempfile.mkdtemp(prefix="vaibifyCtl")
    if len(sDirectory) > 70:
        sDirectory = tempfile.mkdtemp(prefix="vaibifyCtl", dir="/tmp")
    monkeypatch.setattr(
        hostControlChannel, "_S_CONTROL_DIRECTORY", sDirectory,
    )
    yield sDirectory
    for sEntry in os.listdir(sDirectory):
        os.unlink(os.path.join(sDirectory, sEntry))
    os.rmdir(sDirectory)


def _fnJournalDeadHelperRecord():
    """Journal an IN_FLIGHT helper whose holder is dead; return its id."""
    processDead = subprocess.Popen(
        [sys.executable, "-c", "pass"], start_new_session=True,
    )
    processDead.wait()
    sOperationId = operationJournal.fsPrepareOperation(
        S_PROJECT, "helper", "an abandoned helper",
    )
    operationJournal.fnPromoteOperationToInFlight(
        S_PROJECT, sOperationId,
        {
            "iHolderPid": processDead.pid,
            "iHolderProcessGroup": processDead.pid,
        },
    )
    return sOperationId


def test_the_command_is_registered_with_the_cli_group():
    from vaibify.cli.main import main
    resultHelp = CliRunner().invoke(main, ["reconcile", "--help"])
    assert resultHelp.exit_code == 0
    assert "Prove a quarantined container" in resultHelp.output
    assert "--break-glass" in resultHelp.output
    assert "--force-abandon" in resultHelp.output


def test_an_invalid_container_name_exits_2(capsys):
    assert fiRunReconcileCommand("../escape", True) == 2
    assert "invalid container name" in capsys.readouterr().err


def test_no_journal_and_no_holder_reports_nothing_to_reconcile(capsys):
    assert fiRunReconcileCommand(S_PROJECT, True) == 0
    assert "nothing to reconcile" in capsys.readouterr().out


def test_crash_time_reconcile_shows_the_records_and_restores_claim(capsys):
    """The happy crash-time path: display, prove, clear, claimable.

    The record is NEEDS_RECONCILIATION — the state the automatic tier
    never clears — so the claim refusal before and the grant after are
    both the reconcile command's doing.
    """
    sOperationId = _fnJournalDeadHelperRecord()
    operationJournal.fnMarkOperationNeedsReconciliation(
        S_PROJECT, sOperationId,
    )
    with pytest.raises(containerLock.ContainerQuarantinedError):
        containerLock.ffileAcquireContainerLock(S_PROJECT, 8200)
    assert fiRunReconcileCommand(S_PROJECT, True) == 0
    sOutput = capsys.readouterr().out
    assert sOperationId in sOutput
    assert "kind:      helper" in sOutput
    assert "prepared:" in sOutput
    assert "claimable again" in sOutput
    assert not os.path.exists(operationJournal.fsJournalPathFor(S_PROJECT))
    fileHandle = containerLock.ffileAcquireContainerLock(S_PROJECT, 8200)
    containerLock.fnReleaseContainerLock(fileHandle)


def test_a_declined_confirmation_leaves_the_quarantine_standing(
    capsys, monkeypatch,
):
    _fnJournalDeadHelperRecord()
    monkeypatch.setattr(click, "confirm", lambda *ta, **dictKw: False)
    assert fiRunReconcileCommand(S_PROJECT, False) == 1
    assert "quarantine stands" in capsys.readouterr().out
    assert os.path.exists(operationJournal.fsJournalPathFor(S_PROJECT))


def test_an_unprovable_operation_refuses_and_keeps_the_quarantine(capsys):
    """A Docker-identified record with no reachable verifier refuses."""
    sOperationId = operationJournal.fsPrepareOperation(
        S_PROJECT, "exec", "container command",
    )
    operationJournal.fnPromoteOperationToInFlight(
        S_PROJECT, sOperationId, {"sDockerExecId": "feedface"},
    )
    assert fiRunReconcileCommand(S_PROJECT, True) == 1
    assert "Reconciliation refused" in capsys.readouterr().err
    assert os.path.exists(operationJournal.fsJournalPathFor(S_PROJECT))


def test_a_malformed_journal_names_the_break_glass_with_the_real_hash(
    capsys,
):
    sJournalPath = operationJournal.fsJournalPathFor(S_PROJECT)
    os.makedirs(os.path.dirname(sJournalPath), exist_ok=True)
    with open(sJournalPath, "wb") as fileHandle:
        fileHandle.write(b"\x00malformed marker")
    assert fiRunReconcileCommand(S_PROJECT, True) == 1
    sErrorOutput = capsys.readouterr().err
    sMarkerSha256 = operationJournal.fsComputeJournalFileSha256(S_PROJECT)
    assert f"--break-glass {sMarkerSha256}" in sErrorOutput
    assert os.path.exists(sJournalPath)


def test_the_crash_time_break_glass_clears_the_hash_matched_marker(
    capsys, monkeypatch,
):
    listStopped = []

    def _fbRecordProvenStop(sContainerName):
        listStopped.append(sContainerName)
        return True

    monkeypatch.setattr(
        commandReconcile, "_fbStopContainerByName", _fbRecordProvenStop,
    )
    sJournalPath = operationJournal.fsJournalPathFor(S_PROJECT)
    os.makedirs(os.path.dirname(sJournalPath), exist_ok=True)
    with open(sJournalPath, "wb") as fileHandle:
        fileHandle.write(b"\x00malformed marker")
    sMarkerSha256 = operationJournal.fsComputeJournalFileSha256(S_PROJECT)
    assert fiRunReconcileCommand(
        S_PROJECT, True, sBreakGlassSha256="f" * 64,
    ) == 1
    assert os.path.exists(sJournalPath)
    assert listStopped == [], (
        "a hash-mismatched break-glass must stop nothing"
    )
    assert fiRunReconcileCommand(
        S_PROJECT, True, sBreakGlassSha256=sMarkerSha256,
    ) == 0
    assert listStopped == [S_PROJECT]
    assert not os.path.exists(sJournalPath)


def test_force_abandon_without_a_live_hub_exits_2(capsys):
    assert fiRunReconcileCommand(
        S_PROJECT, True, sForceAbandonOperationId="op-1",
    ) == 2
    assert "no live vaibify process" in capsys.readouterr().err


# ---------------------------------------------------------------------
# The live-hub routing path, end to end.
# ---------------------------------------------------------------------

def fnHoldContainerFlockInChild(
    sLockDirectory, sProjectName, iPort, eventRelease,
):
    """Child: hold the container flock as a live foreign process."""
    import vaibify.config.containerLock as childLockModule
    childLockModule._S_LOCK_DIRECTORY = sLockDirectory
    fileHandleLock = childLockModule.ffileAcquireContainerLock(
        sProjectName, iPort,
    )
    eventRelease.wait(timeout=60)
    childLockModule.fnReleaseContainerLock(fileHandleLock)


def _fprocessStartForeignFlockHolder():
    """Start a real child holding the flock; return (process, event)."""
    contextSpawn = multiprocessing.get_context("spawn")
    eventRelease = contextSpawn.Event()
    processHolder = contextSpawn.Process(
        target=fnHoldContainerFlockInChild,
        args=(
            containerLock._S_LOCK_DIRECTORY, S_PROJECT, I_HUB_PORT,
            eventRelease,
        ),
    )
    processHolder.start()
    for _ in range(300):
        if containerLock.fdictReadLockHolder(S_PROJECT):
            return processHolder, eventRelease
        time.sleep(0.1)
    eventRelease.set()
    processHolder.join()
    raise AssertionError("the child never acquired the container flock")


def _fappBuildFakeHubApplication():
    """Return a SimpleNamespace app with the hub state the channel uses."""
    return SimpleNamespace(state=SimpleNamespace(
        iHubPort=I_HUB_PORT,
        listLifespanStartup=[],
        listLifespanShutdown=[],
        dictContainerOwners={},
        dictMutationSupervisors={},
        dictDurableTaskRecords={},
    ))


async def _fiServeHubAndRunCommand(app, tArguments, dictKeywordArguments):
    """Serve the real control socket while the real CLI entry runs."""
    hostControlChannel.fnRegisterHostControlChannel(app, {})
    for fnStartup in app.state.listLifespanStartup:
        await fnStartup(app)
    try:
        return await asyncio.to_thread(
            fiRunReconcileCommand, *tArguments, **dictKeywordArguments,
        )
    finally:
        for fnShutdown in app.state.listLifespanShutdown:
            await fnShutdown(app)


def test_a_live_holder_routes_over_its_host_control_socket(
    fixtureShortControlDirectory, capsys,
):
    """Real child flock holder + real socket + real handlers, end to end."""
    processHolder, eventRelease = _fprocessStartForeignFlockHolder()
    sOperationId = _fnJournalDeadHelperRecord()
    try:
        app = _fappBuildFakeHubApplication()
        app.state.dictContainerOwners[S_PROJECT] = (
            containerOwnership.OwnerRecord(
                sLeaseId="LEASE-A", fileHandleLock=object(),
            )
        )
        iExitCode = asyncio.run(
            _fiServeHubAndRunCommand(app, (S_PROJECT, True), {}),
        )
    finally:
        eventRelease.set()
        processHolder.join(timeout=30)
    assert iExitCode == 0
    sOutput = capsys.readouterr().out
    assert "routing over" in sOutput
    assert f"proven: {sOperationId}" in sOutput
    assert not os.path.exists(operationJournal.fsJournalPathFor(S_PROJECT))


def test_a_hub_refusal_is_reported_and_the_quarantine_stands(
    fixtureShortControlDirectory, capsys,
):
    """A stale force-abandon routed to the live hub is refused."""
    processHolder, eventRelease = _fprocessStartForeignFlockHolder()
    _fnJournalDeadHelperRecord()
    try:
        app = _fappBuildFakeHubApplication()
        app.state.dictContainerOwners[S_PROJECT] = (
            containerOwnership.OwnerRecord(
                sLeaseId="LEASE-A", fileHandleLock=object(),
            )
        )
        iExitCode = asyncio.run(_fiServeHubAndRunCommand(
            app, (S_PROJECT, True),
            {"sForceAbandonOperationId": "a-stale-id"},
        ))
    finally:
        eventRelease.set()
        processHolder.join(timeout=30)
    assert iExitCode == 1
    assert "Refused by the hub" in capsys.readouterr().err
    assert os.path.exists(operationJournal.fsJournalPathFor(S_PROJECT))


def test_a_live_holder_with_no_control_socket_reports_it_honestly(
    fixtureShortControlDirectory, capsys,
):
    """A holder whose hub never bound a socket is a clear error, not a
    hang."""
    processHolder, eventRelease = _fprocessStartForeignFlockHolder()
    _fnJournalDeadHelperRecord()
    try:
        iExitCode = fiRunReconcileCommand(S_PROJECT, True)
    finally:
        eventRelease.set()
        processHolder.join(timeout=30)
    assert iExitCode == 1
    assert "still running" in capsys.readouterr().err
