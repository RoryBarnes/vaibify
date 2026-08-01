"""Tests for the proving reconciliation transaction (design §8, 3c).

Covers the display projection (allowlisted fields only), the proving
core (dead-writer / live-writer / indeterminate / stale-id outcomes,
per record kind), the self-holder substitution the live hub uses, the
crash-time transaction under the reconciliation flock, and the
destructive break-glass for malformed markers.

The falsification (kill-confirmed) halves of cases 30, 34, 35, 42 and
46 live in ``testReconciliationMutationCoverage.py``.
"""

import json
import multiprocessing
import os
import signal
import subprocess
import sys

import pytest

from vaibify.config import containerLock, operationJournal, reconciliation
from vaibify.config.operationJournal import (
    fdictReadJournalOutcome,
    fnMarkOperationNeedsReconciliation,
    fnPromoteOperationToInFlight,
    fsComputeJournalFileSha256,
    fsJournalPathFor,
    fsPrepareOperation,
)
from vaibify.config.reconciliation import (
    ReconciliationRefusedError,
    fdictExecuteBreakGlass,
    fdictProveJournalRecordsSettled,
    fdictReconcileCrashTimeJournal,
    flistDescribeJournalRecords,
)

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


def _fnWriteRawJournalBytes(sContainerName, byteContent):
    """Write raw bytes straight to a container's journal path."""
    sPath = fsJournalPathFor(sContainerName)
    os.makedirs(os.path.dirname(sPath), exist_ok=True)
    with open(sPath, "wb") as fileHandle:
        fileHandle.write(byteContent)


# ---------------------------------------------------------------------
# The display projection.
# ---------------------------------------------------------------------

def test_describe_returns_empty_list_for_an_absent_journal():
    assert flistDescribeJournalRecords(S_PROJECT) == []


def test_describe_shows_only_the_allowlisted_display_fields():
    """No journal content beyond the display allowlist may leave."""
    sOperationId = fsPrepareOperation(S_PROJECT, "helper", "a helper task")
    fnPromoteOperationToInFlight(
        S_PROJECT, sOperationId,
        {"iHolderPid": 12345, "iHolderProcessGroup": 12345},
    )
    listRecords = flistDescribeJournalRecords(S_PROJECT)
    assert len(listRecords) == 1
    setExpectedKeys = {"sOperationId"} | set(
        reconciliation._TUPLE_DISPLAY_FIELDS
    )
    assert set(listRecords[0]) == setExpectedKeys
    assert listRecords[0]["sOperationId"] == sOperationId
    assert listRecords[0]["sKind"] == "helper"
    assert "12345" not in json.dumps(listRecords[0])


def test_describe_raises_with_the_read_state_for_a_damaged_journal():
    _fnWriteRawJournalBytes(S_PROJECT, b"not json at all")
    with pytest.raises(ReconciliationRefusedError) as excInfo:
        flistDescribeJournalRecords(S_PROJECT)
    assert excInfo.value.sReadState not in ("", "valid", "absent")


# ---------------------------------------------------------------------
# The proving core.
# ---------------------------------------------------------------------

def test_prove_reports_an_absent_journal_as_trivially_proven():
    dictProven = fdictProveJournalRecordsSettled(S_PROJECT, None, set())
    assert dictProven["bProven"] is True
    assert dictProven["listClearableOperationIds"] == []


def test_prove_refuses_a_newer_schema_version_requiring_upgrade():
    _fnWriteRawJournalBytes(S_PROJECT, json.dumps({
        "iSchemaVersion": 999,
        "sContainerName": S_PROJECT,
        "dictOperations": {},
    }).encode("utf-8"))
    dictProven = fdictProveJournalRecordsSettled(S_PROJECT, None, set())
    assert dictProven["bProven"] is False
    assert dictProven["sReadState"] == "requiresUpgrade"


def test_prove_refuses_a_malformed_journal_and_names_the_break_glass():
    _fnWriteRawJournalBytes(S_PROJECT, b"\x00garbage")
    dictProven = fdictProveJournalRecordsSettled(S_PROJECT, None, set())
    assert dictProven["bProven"] is False
    assert "break-glass" in dictProven["sRefusalReason"]


def test_prove_refuses_when_the_expected_id_set_does_not_match():
    """The ABA guard: subset, superset, and disjoint sets all refuse."""
    sOperationId = _fsJournalDeadHelperRecord()
    for setExpected in (set(), {sOperationId, "extra"}, {"other"}):
        dictProven = fdictProveJournalRecordsSettled(
            S_PROJECT, None, setExpected,
        )
        assert dictProven["bProven"] is False
        assert "changed since it was inspected" in (
            dictProven["sRefusalReason"]
        )
    assert fdictReadJournalOutcome(S_PROJECT)["dictOperations"], (
        "a refused proof must leave the journal untouched"
    )


def test_prove_clears_a_prepared_record_that_never_launched():
    sOperationId = fsPrepareOperation(S_PROJECT, "helper", "never launched")
    dictProven = fdictProveJournalRecordsSettled(
        S_PROJECT, None, {sOperationId},
    )
    assert dictProven["bProven"] is True
    assert dictProven["listClearableOperationIds"] == [sOperationId]


def test_prove_refuses_while_the_recorded_writer_is_still_alive():
    """A REAL live child holding the recorded process group refuses."""
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
        dictProven = fdictProveJournalRecordsSettled(
            S_PROJECT, None, {sOperationId},
        )
        assert dictProven["bProven"] is False
        assert "still alive" in dictProven["sRefusalReason"]
    finally:
        processLive.kill()
        processLive.wait()


def test_prove_accepts_a_dead_writer_with_a_settled_effect():
    sOperationId = _fsJournalDeadHelperRecord()
    dictProven = fdictProveJournalRecordsSettled(
        S_PROJECT, None, {sOperationId},
    )
    assert dictProven["bProven"] is True
    assert dictProven["listClearableOperationIds"] == [sOperationId]


def test_prove_refuses_an_indeterminate_docker_probe_and_stays_quarantined():
    """An exec with no reachable verifier cannot be proven settled."""
    sOperationId = fsPrepareOperation(S_PROJECT, "exec", "container command")
    fnPromoteOperationToInFlight(
        S_PROJECT, sOperationId, {"sDockerExecId": "feedface"},
    )
    dictProven = fdictProveJournalRecordsSettled(
        S_PROJECT, None, {sOperationId},
    )
    assert dictProven["bProven"] is False
    assert "cannot be proven settled" in dictProven["sRefusalReason"]
    assert fdictReadJournalOutcome(S_PROJECT)["dictOperations"], (
        "the container must stay quarantined"
    )


def test_prove_accepts_a_torn_file_write_whose_writer_is_proven_dead(
    tmp_path,
):
    """Dead writer + torn target bytes = the accepted human judgement."""
    iDeadPid = _fiSpawnExitedProcessInOwnGroup()
    sTargetPath = str(tmp_path / "torn.json")
    with open(sTargetPath, "w") as fileHandle:
        fileHandle.write("neither old nor new content")
    sOperationId = fsPrepareOperation(S_PROJECT, "file-write", sTargetPath)
    fnPromoteOperationToInFlight(
        S_PROJECT, sOperationId,
        {
            "iHolderPid": iDeadPid,
            "iHolderProcessGroup": iDeadPid,
            "sExpectedSha256": "0" * 64,
            "sPriorSha256": "1" * 64,
        },
    )
    dictProven = fdictProveJournalRecordsSettled(
        S_PROJECT, None, {sOperationId},
    )
    assert dictProven["bProven"] is True
    assert "accepts the target's current content" in (
        dictProven["listRecordNotes"][0]
    )


def test_self_holder_record_is_probed_without_identity_only_when_idle(
    tmp_path,
):
    """The live-hub substitution drops the hub's own PID only on proof.

    A mode-(b) worker journals the hub's own PID. While the supervisor
    registry still reports live guarded work the record must read as a
    live writer and refuse; once proven idle the identity is dropped so
    the kind-specific effect check decides.
    """
    sTargetPath = str(tmp_path / "written.json")
    with open(sTargetPath, "wb") as fileHandle:
        fileHandle.write(b"the committed content")
    sExpectedSha256 = operationJournal._fsComputeHostFileSha256(sTargetPath)
    sOperationId = fsPrepareOperation(S_PROJECT, "file-write", sTargetPath)
    fnPromoteOperationToInFlight(
        S_PROJECT, sOperationId,
        {
            "iHolderPid": os.getpid(),
            "iHolderProcessGroup": os.getpgrp(),
            "sExpectedSha256": sExpectedSha256,
        },
    )
    dictRefused = fdictProveJournalRecordsSettled(
        S_PROJECT, None, {sOperationId},
        fbSelfHolderProvenIdle=lambda: False,
    )
    assert dictRefused["bProven"] is False
    assert "still alive" in dictRefused["sRefusalReason"]
    dictProven = fdictProveJournalRecordsSettled(
        S_PROJECT, None, {sOperationId},
        fbSelfHolderProvenIdle=lambda: True,
    )
    assert dictProven["bProven"] is True


def test_self_holder_substitution_never_touches_a_foreign_pid_record():
    """The substitution is for the hub's OWN pid only; a dead foreign
    holder is proven by the ordinary recycle-proof probe."""
    sOperationId = _fsJournalDeadHelperRecord()
    dictProven = fdictProveJournalRecordsSettled(
        S_PROJECT, None, {sOperationId},
        fbSelfHolderProvenIdle=lambda: False,
    )
    assert dictProven["bProven"] is True


# ---------------------------------------------------------------------
# The crash-time transaction.
# ---------------------------------------------------------------------

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


def test_crash_time_transaction_clears_the_quarantine_and_restores_claim():
    """A SIGKILLed hub's quarantine is cleared by the real transaction."""
    _fnCrashChildMidOperation()
    with pytest.raises(containerLock.ContainerQuarantinedError):
        containerLock.fnAcquireContainerLock(S_PROJECT, 8200)
    setExpectedIds = set(
        fdictReadJournalOutcome(S_PROJECT)["dictOperations"]
    )
    dictProven = fdictReconcileCrashTimeJournal(
        S_PROJECT, FakeDockerConnectionExecSettled(), setExpectedIds,
    )
    assert dictProven["bProven"] is True
    assert not os.path.exists(fsJournalPathFor(S_PROJECT))
    fileHandle = containerLock.fnAcquireContainerLock(S_PROJECT, 8200)
    containerLock.fnReleaseContainerLock(fileHandle)


def test_crash_time_transaction_refuses_and_keeps_the_marker_on_no_proof():
    _fnCrashChildMidOperation()
    setExpectedIds = set(
        fdictReadJournalOutcome(S_PROJECT)["dictOperations"]
    )
    with pytest.raises(ReconciliationRefusedError):
        fdictReconcileCrashTimeJournal(S_PROJECT, None, setExpectedIds)
    assert os.path.exists(fsJournalPathFor(S_PROJECT)), (
        "a refused transaction must leave the quarantine standing"
    )


def test_crash_time_transaction_routes_to_the_live_holder_instead():
    """While a live process holds the flock the crash path refuses."""
    fileHandleHolder = containerLock.fnAcquireContainerLock(S_PROJECT, 8300)
    try:
        with pytest.raises(ReconciliationRefusedError) as excInfo:
            fdictReconcileCrashTimeJournal(S_PROJECT, None, set())
        assert "vaibify reconcile" in str(excInfo.value)
    finally:
        containerLock.fnReleaseContainerLock(fileHandleHolder)


def test_reconciliation_is_the_one_authority_that_clears_a_poisoned_record():
    """3a's ordinary settle refuses; the transaction clears (case 30/38)."""
    sOperationId = _fsJournalDeadHelperRecord()
    fnMarkOperationNeedsReconciliation(S_PROJECT, sOperationId)
    with pytest.raises(operationJournal.OperationJournalRecordError):
        operationJournal.fnSettleOperation(S_PROJECT, sOperationId)
    dictProven = fdictReconcileCrashTimeJournal(
        S_PROJECT, None, {sOperationId},
    )
    assert dictProven["bProven"] is True
    assert not os.path.exists(fsJournalPathFor(S_PROJECT))


# ---------------------------------------------------------------------
# The break-glass.
# ---------------------------------------------------------------------

def test_break_glass_refuses_a_valid_journal():
    _fsJournalDeadHelperRecord()
    sMarkerSha256 = fsComputeJournalFileSha256(S_PROJECT)
    with pytest.raises(ReconciliationRefusedError) as excInfo:
        fdictExecuteBreakGlass(S_PROJECT, sMarkerSha256)
    assert "ordinary reconciliation" in str(excInfo.value)
    assert os.path.exists(fsJournalPathFor(S_PROJECT))


def test_break_glass_refuses_a_newer_version_requiring_upgrade():
    _fnWriteRawJournalBytes(S_PROJECT, json.dumps({
        "iSchemaVersion": 999,
        "sContainerName": S_PROJECT,
        "dictOperations": {},
    }).encode("utf-8"))
    sMarkerSha256 = fsComputeJournalFileSha256(S_PROJECT)
    with pytest.raises(ReconciliationRefusedError) as excInfo:
        fdictExecuteBreakGlass(S_PROJECT, sMarkerSha256)
    assert "upgrade vaibify" in str(excInfo.value)
    assert os.path.exists(fsJournalPathFor(S_PROJECT))


def test_break_glass_refuses_a_hash_mismatch_and_clears_on_the_match():
    """The raw-bytes hash is the ABA guard for an id-less record."""
    _fnWriteRawJournalBytes(S_PROJECT, b"\x00the malformed marker")
    with pytest.raises(ReconciliationRefusedError) as excInfo:
        fdictExecuteBreakGlass(S_PROJECT, "f" * 64)
    assert "replaced" in str(excInfo.value)
    assert os.path.exists(fsJournalPathFor(S_PROJECT))
    listStopped = []
    fdictExecuteBreakGlass(
        S_PROJECT, fsComputeJournalFileSha256(S_PROJECT),
        fnStopContainerByName=listStopped.append,
    )
    assert listStopped == [S_PROJECT], (
        "the possibly-relevant container must be stopped first"
    )
    assert not os.path.exists(fsJournalPathFor(S_PROJECT))


def test_break_glass_refuses_while_a_live_process_holds_the_flock():
    fileHandleHolder = containerLock.fnAcquireContainerLock(S_PROJECT, 8300)
    _fnWriteRawJournalBytes(S_PROJECT, b"\x00the malformed marker")
    try:
        with pytest.raises(ReconciliationRefusedError):
            fdictExecuteBreakGlass(
                S_PROJECT, fsComputeJournalFileSha256(S_PROJECT),
            )
    finally:
        containerLock.fnReleaseContainerLock(fileHandleHolder)
    assert os.path.exists(fsJournalPathFor(S_PROJECT))


