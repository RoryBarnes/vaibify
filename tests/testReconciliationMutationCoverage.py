"""Kill-confirmed falsification tests for the reconciliation transaction.

Design §13 cases 30, 34, 35, 42 (transaction side) and 46 (break-glass
half), landing with slice 3 sub-step 3c. Each test drives real records
— real child processes for holder identities (case 30's quarantine
comes from a genuinely SIGKILLed child, case 34's live writer is a
genuinely live one), real bytes on disk for the journal — never a stub
keyed the same way the code under test is.

Scope notes: case 42's semantics half (auto-clear tiering) landed with
3a in ``testOperationJournalMutationCoverage.py``; case 46's
DRAINING-transfer halves landed with slice 5 (``testHostTransfer.py``
and ``testTerminalContainmentLive.py``); case 26b's full
force-abandon lifecycle landed with slice 5 in
``testHostControlChannel.py`` — the poison refusal
surface is unit-tested in ``testPoisonRecord.py``.
"""

import json
import multiprocessing
import os
import signal
import subprocess
import sys
import threading

import pytest

from vaibify.cli import commandReconcile
from vaibify.config import containerLock, operationJournal
from vaibify.config.operationJournal import (
    fdictReadJournalOutcome,
    fnPromoteOperationToInFlight,
    fsComputeJournalFileSha256,
    fsJournalPathFor,
    fsPrepareOperation,
)
from vaibify.config.reconciliation import (
    ReconciliationRefusedError,
    fdictExecuteBreakGlass,
    fdictReconcileCrashTimeJournal,
)

pytestmark = pytest.mark.falsification

S_PROJECT = "demo"


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


class FakeDockerConnectionExecSettled:
    """A Docker connection whose recorded execs have all exited."""

    def fdictInspectExec(self, sDockerExecId):
        del sDockerExecId
        return {"Running": False}


def _fiSpawnExitedProcessInOwnGroup():
    """Spawn a process as its own group leader and reap it; return its pid."""
    processHolder = subprocess.Popen(
        [sys.executable, "-c", "pass"], start_new_session=True,
    )
    processHolder.wait()
    return processHolder.pid


def _fsJournalDeadHelperRecord():
    """Journal an IN_FLIGHT helper whose holder is dead; return its id."""
    iDeadPid = _fiSpawnExitedProcessInOwnGroup()
    sOperationId = fsPrepareOperation(S_PROJECT, "helper", "a helper")
    fnPromoteOperationToInFlight(
        S_PROJECT, sOperationId,
        {"iHolderPid": iDeadPid, "iHolderProcessGroup": iDeadPid},
    )
    return sOperationId


def _fnCrashMidExecOperationInChildProcess(
    sLockDirectory, sJournalDirectory, sProjectName,
):
    """Child: claim the flock, journal an IN_FLIGHT exec, SIGKILL self."""
    import vaibify.config.containerLock as childLockModule
    import vaibify.config.operationJournal as childJournalModule
    childLockModule._S_LOCK_DIRECTORY = sLockDirectory
    childJournalModule._S_JOURNAL_DIRECTORY = sJournalDirectory
    childLockModule.fnAcquireContainerLock(sProjectName, 8123)
    sOperationId = childJournalModule.fsPrepareOperation(
        sProjectName, "exec", "container-side command",
    )
    childJournalModule.fnPromoteOperationToInFlight(
        sProjectName, sOperationId, {"sDockerExecId": "feedface"},
    )
    os.kill(os.getpid(), signal.SIGKILL)


def _fnCrashChildMidOperation():
    """Run the crash fixture in a spawned child and wait for the kill."""
    contextSpawn = multiprocessing.get_context("spawn")
    processChild = contextSpawn.Process(
        target=_fnCrashMidExecOperationInChildProcess,
        args=(
            containerLock._S_LOCK_DIRECTORY,
            operationJournal._S_JOURNAL_DIRECTORY,
            S_PROJECT,
        ),
    )
    processChild.start()
    processChild.join(timeout=30)
    assert processChild.exitcode == -signal.SIGKILL


def test_reconcile_cli_clears_a_sigkill_quarantine_and_restores_claim(
    capsys, monkeypatch,
):
    """Design §13 case 30: quarantine → vaibify reconcile → claimable.

    A real child process claims the container, journals an IN_FLIGHT
    Docker exec, and SIGKILLs itself — 3a's case-27 fixture. The REAL
    CLI entry function is then driven with a Docker connection that
    reports the exec exited: it must show the abandoned operation, its
    container, and when it was prepared; prove it settled; clear the
    marker; and leave the container genuinely claimable again.

    Kills: in reconciliation.fnCleanupAndClearProvenRecords, replace
    the final operationJournal.fnClearOperationsReconciled call with a
    no-op, so the transaction reports success but the durable marker
    is never cleared and the container stays unclaimable.
    """
    _fnCrashChildMidOperation()
    with pytest.raises(containerLock.ContainerQuarantinedError):
        containerLock.fnAcquireContainerLock(S_PROJECT, 8200)
    setOperationIds = set(
        fdictReadJournalOutcome(S_PROJECT)["dictOperations"]
    )
    assert len(setOperationIds) == 1
    sOperationId = next(iter(setOperationIds))
    monkeypatch.setattr(
        commandReconcile, "_fconnectionCreateDockerQuietly",
        FakeDockerConnectionExecSettled,
    )
    iExitCode = commandReconcile.fiRunReconcileCommand(S_PROJECT, True)
    assert iExitCode == 0
    sOutput = capsys.readouterr().out
    assert sOperationId in sOutput
    assert S_PROJECT in sOutput
    assert "prepared:" in sOutput
    assert not os.path.exists(fsJournalPathFor(S_PROJECT))
    fileHandle = containerLock.fnAcquireContainerLock(S_PROJECT, 8200)
    containerLock.fnReleaseContainerLock(fileHandle)


def test_reconciliation_refuses_while_the_recorded_writer_lives(tmp_path):
    """Design §13 case 34: no clear over a live writer, ever.

    A REAL child process holds the recorded process group. While it
    lives the transaction must refuse and keep the quarantine; the
    moment it is dead and reaped the same transaction succeeds —
    proving the refusal was about writer liveness and nothing else.

    Kills: in reconciliation._ftReconcileRecordVerdict, make the
    bHolderAlive branch return (True, ...) — treating a live recorded
    writer as clearable — so the transaction clears a quarantine over
    a process that can still write.
    """
    processLive = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(60)"],
        start_new_session=True,
    )
    try:
        sOperationId = fsPrepareOperation(S_PROJECT, "helper", "live")
        fnPromoteOperationToInFlight(
            S_PROJECT, sOperationId,
            {
                "iHolderPid": processLive.pid,
                "iHolderProcessGroup": processLive.pid,
            },
        )
        with pytest.raises(ReconciliationRefusedError) as excInfo:
            fdictReconcileCrashTimeJournal(
                S_PROJECT, None, {sOperationId},
            )
        assert "still alive" in str(excInfo.value)
        assert os.path.exists(fsJournalPathFor(S_PROJECT)), (
            "the quarantine must stand while the writer lives"
        )
    finally:
        processLive.kill()
        processLive.wait()
    dictProven = fdictReconcileCrashTimeJournal(
        S_PROJECT, None, {sOperationId},
    )
    assert dictProven["bProven"] is True
    assert not os.path.exists(fsJournalPathFor(S_PROJECT))


def test_reconcile_versus_claim_is_atomic_on_the_container_flock():
    """Design §13 case 35: a reconciliation and a claim never both win.

    The transaction is held open mid-flight (its cleanup step blocked
    on an event, after the records were proven but before the marker
    clears). A claim in that window must lose the flock race outright
    — it must NOT slip through via its journal auto-tier, which would
    happily clear the provably-dead record while the transaction's
    cleanup is still executing. After the transaction completes, the
    claim succeeds.

    Kills: in containerLock.ffileAcquireReconciliationLock, replace
    the flock-acquisition loop with a bare open of the lock path, so
    the transaction runs without holding the container flock and the
    concurrent claim is admitted mid-transaction.
    """
    sOperationId = _fsJournalDeadHelperRecord()
    eventCleanupEntered = threading.Event()
    eventReleaseCleanup = threading.Event()
    dictCatalogEntry = operationJournal.DICT_OPERATION_PROBE_CATALOG[
        "helper"
    ]
    fnOriginalCleanup = dictCatalogEntry["fnCleanupAfterSettledProbe"]

    def fnBlockingCleanup(dictRecord, connectionDocker):
        eventCleanupEntered.set()
        assert eventReleaseCleanup.wait(timeout=30)
        fnOriginalCleanup(dictRecord, connectionDocker)

    dictCatalogEntry["fnCleanupAfterSettledProbe"] = fnBlockingCleanup
    try:
        threadReconcile = threading.Thread(
            target=fdictReconcileCrashTimeJournal,
            args=(S_PROJECT, None, {sOperationId}),
        )
        threadReconcile.start()
        assert eventCleanupEntered.wait(timeout=30)
        with pytest.raises(containerLock.ContainerLockedError):
            containerLock.fnAcquireContainerLock(S_PROJECT, 8400)
    finally:
        eventReleaseCleanup.set()
        threadReconcile.join(timeout=30)
        dictCatalogEntry["fnCleanupAfterSettledProbe"] = fnOriginalCleanup
    fileHandle = containerLock.fnAcquireContainerLock(S_PROJECT, 8400)
    containerLock.fnReleaseContainerLock(fileHandle)


def test_a_stale_reconciliation_cannot_clear_a_successor_record():
    """Design §13 case 42 (transaction side): the expected-id compare.

    The operation inspected before the call settles, and a successor
    operation is journaled in its place. The stale transaction — still
    carrying the OLD operation id — must refuse and leave the
    successor's record untouched, even though the successor's holder
    is provably dead and would clear on its own merits.

    Kills: in reconciliation.fdictProveJournalRecordsSettled, disable
    the expected-id-set comparison (``if False:``), so a stale request
    proves and clears whatever records now stand in the journal.
    """
    sInspectedOperationId = _fsJournalDeadHelperRecord()
    operationJournal.fnSettleOperation(S_PROJECT, sInspectedOperationId)
    sSuccessorOperationId = _fsJournalDeadHelperRecord()
    assert sSuccessorOperationId != sInspectedOperationId
    with pytest.raises(ReconciliationRefusedError) as excInfo:
        fdictReconcileCrashTimeJournal(
            S_PROJECT, None, {sInspectedOperationId},
        )
    assert "changed since it was inspected" in str(excInfo.value)
    assert sSuccessorOperationId in (
        fdictReadJournalOutcome(S_PROJECT)["dictOperations"]
    ), "the successor's record must be untouched"


def test_a_newer_version_journal_requires_upgrade_never_a_blind_clear():
    """Design §13 case 42 (recovery outcomes): unknown-newer → upgrade.

    A journal from a NEWER vaibify build refuses both the ordinary
    transaction and the break-glass: a readable record is never
    destroyed by a build that cannot understand it.

    Kills: in operationJournal.fnAssertJournalIsBreakGlassClearable,
    drop the requiresUpgrade refusal (raise removed in favour of a
    pass-through), so the break-glass destroys a newer-version record
    it cannot read.
    """
    sJournalPath = fsJournalPathFor(S_PROJECT)
    os.makedirs(os.path.dirname(sJournalPath), exist_ok=True)
    with open(sJournalPath, "wb") as fileHandle:
        fileHandle.write(json.dumps({
            "iSchemaVersion": 999,
            "sContainerName": S_PROJECT,
            "dictOperations": {},
        }).encode("utf-8"))
    with pytest.raises(ReconciliationRefusedError):
        fdictReconcileCrashTimeJournal(S_PROJECT, None, set())
    with pytest.raises(ReconciliationRefusedError) as excInfo:
        fdictExecuteBreakGlass(
            S_PROJECT, fsComputeJournalFileSha256(S_PROJECT),
        )
    assert "upgrade vaibify" in str(excInfo.value)
    assert os.path.exists(sJournalPath)


def test_break_glass_clears_only_the_malformed_record_it_names():
    """Design §13 case 46 (break-glass half): the raw-bytes hash guard.

    A malformed marker is inspected and hashed; before the break-glass
    lands, the marker is REPLACED by a different malformed record. The
    break-glass carrying the old hash must refuse — without stopping
    any container, because a request proven stale may act on nothing —
    and only the hash of the record actually on disk clears it.

    Kills: in operationJournal.fnAssertJournalIsBreakGlassClearable,
    disable the hash comparison (``if False:``), so a stale
    break-glass destroys a replacement record it never inspected.
    """
    sJournalPath = fsJournalPathFor(S_PROJECT)
    os.makedirs(os.path.dirname(sJournalPath), exist_ok=True)
    with open(sJournalPath, "wb") as fileHandle:
        fileHandle.write(b"\x00the inspected malformed marker")
    sInspectedSha256 = fsComputeJournalFileSha256(S_PROJECT)
    with open(sJournalPath, "wb") as fileHandle:
        fileHandle.write(b"\x00a REPLACEMENT malformed marker")
    listStopped = []
    with pytest.raises(ReconciliationRefusedError) as excInfo:
        fdictExecuteBreakGlass(
            S_PROJECT, sInspectedSha256,
            fnStopContainerByName=listStopped.append,
        )
    assert "replaced" in str(excInfo.value)
    assert os.path.exists(sJournalPath), (
        "the replacement record must be untouched"
    )
    assert listStopped == [], (
        "a stale break-glass must stop no container"
    )
    fdictExecuteBreakGlass(
        S_PROJECT, fsComputeJournalFileSha256(S_PROJECT),
        fnStopContainerByName=listStopped.append,
    )
    assert listStopped == [S_PROJECT]
    assert not os.path.exists(sJournalPath)
