"""Kill-confirmed falsification tests for the operation journal.

Design §13 cases 27, 36, 37, 42 (semantics half) and 45 (journal half),
landing with slice 3 sub-step 3a. Each test drives real records — real
child processes for holder identities, real bytes on disk for the
journal — never a stub keyed the same way the code under test is.

The carrier halves of cases 38/45 (an operation admitted against its
own record) land with sub-step 3b; the reconciliation halves of cases
30/34/35/42 (the proving exit that clears a quarantine) land with 3c.
Until 3c a quarantine is un-clearable by design.
"""

import json
import logging
import multiprocessing
import os
import signal
import subprocess
import sys
from unittest.mock import patch

import pytest

from vaibify.config import containerLock, operationJournal
from vaibify.config.containerLock import (
    ContainerBusyOperationError,
    ContainerQuarantinedError,
)
from vaibify.config.operationJournal import (
    OperationJournalUnreadableError,
    S_OPERATION_STATE_NEEDS_RECONCILIATION,
    S_RESOLUTION_BUSY,
    S_RESOLUTION_QUARANTINED,
    S_RESOLUTION_SETTLED,
    fdictReadJournalOutcome,
    fdictResolveContainerJournal,
    fnMarkOperationNeedsReconciliation,
    fnPromoteOperationToInFlight,
    fsJournalPathFor,
    fsPrepareOperation,
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


def _fnWriteRawJournalBytes(sContainerName, byteContent):
    """Write raw bytes straight to a container's journal path."""
    sPath = fsJournalPathFor(sContainerName)
    os.makedirs(os.path.dirname(sPath), exist_ok=True)
    with open(sPath, "wb") as fileHandle:
        fileHandle.write(byteContent)


def _fnCrashMidExecOperationInChildProcess(
    sLockDirectory, sJournalDirectory, sProjectName,
):
    """Child: claim the flock, journal an IN_FLIGHT exec, SIGKILL self.

    Models a hub killed mid-operation: the kernel frees the flock the
    instant the process dies, the in-memory state vanishes, and only
    the write-ahead journal record survives.
    """
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


def test_quarantine_survives_hub_sigkill_and_blocks_the_next_claim(tmp_path):
    """Design §13 case 27: the write-ahead record outlives the process.

    A real child process claims the container, journals an IN_FLIGHT
    Docker exec, and SIGKILLs itself. The kernel frees its flock and
    its lock-file PID reads as dead — exactly the state the silent
    dead-PID reap was built to take over — yet the next acquisition
    must refuse, and the stale-lock reaper must not clear the record,
    because the exec's fate is unproven until reconciliation (3c).

    Kills: in containerLock.fnAcquireContainerLock, return the freshly
    acquired flock handle directly instead of routing it through
    _ffileRefuseUnsettledJournal, so acquisition never consults the
    journal.
    """
    contextFork = multiprocessing.get_context("fork")
    processChild = contextFork.Process(
        target=_fnCrashMidExecOperationInChildProcess,
        args=(
            str(tmp_path / "locks"), str(tmp_path / "journal"), S_PROJECT,
        ),
    )
    processChild.start()
    processChild.join(timeout=30)
    assert processChild.exitcode == -signal.SIGKILL
    assert os.path.exists(containerLock.fsLockPathFor(S_PROJECT)), (
        "the dead holder's lock file must linger so the silent-reap "
        "path is genuinely exercised"
    )
    with pytest.raises(ContainerQuarantinedError):
        containerLock.fnAcquireContainerLock(S_PROJECT, 8050)
    containerLock.fnReapStaleContainerLocks()
    assert os.path.exists(fsJournalPathFor(S_PROJECT)), (
        "the reaper must not clear an unproven record"
    )
    with pytest.raises(ContainerQuarantinedError):
        containerLock.fnAcquireContainerLock(S_PROJECT, 8050)


def test_auto_tier_clears_a_provably_dead_leftover_with_a_logged_note(caplog):
    """Design §13 case 36, auto-clear half: no human for the provable.

    A helper record whose holder is a REAL exited process in a REAL
    empty process group is cleared by the automatic tier with a logged
    note, so a routinely interrupted multi-hour run never becomes a
    reflexive manual acknowledgement.

    Kills: in operationJournal._ftVerdictFromProbe, map a settled probe
    outcome to ("quarantinePermanent", ...) instead of ("settled", ...),
    so provably-dead leftovers quarantine instead of auto-clearing.
    """
    caplog.set_level(logging.INFO, logger="vaibify")
    iDeadPid = _fiSpawnExitedProcessInOwnGroup()
    sOperationId = fsPrepareOperation(S_PROJECT, "helper", "runStepBatch")
    fnPromoteOperationToInFlight(
        S_PROJECT, sOperationId,
        {"iHolderPid": iDeadPid, "iHolderProcessGroup": iDeadPid},
    )
    dictResolution = fdictResolveContainerJournal(S_PROJECT)
    assert dictResolution["sResolution"] == S_RESOLUTION_SETTLED
    assert dictResolution["listAutoClearedOperationIds"] == [sOperationId]
    assert not os.path.exists(fsJournalPathFor(S_PROJECT))
    assert "Auto-cleared settled journal record" in caplog.text


def test_live_in_flight_holder_reads_busy_never_quarantined():
    """Design §13 case 36, busy half: a live worker is in use, not lost.

    The holder is a REAL running process; the container must resolve
    BUSY and acquisition must refuse with the in-use error, never the
    quarantine error — quarantining a live run would tell the
    researcher their container is damaged while it is working.

    Kills: in operationJournal._ftVerdictFromProbe, map a live-holder
    probe outcome to ("quarantinePermanent", ...) instead of
    ("busy", ...).
    """
    processLive = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(60)"],
        start_new_session=True,
    )
    try:
        sOperationId = fsPrepareOperation(S_PROJECT, "helper", "runStepBatch")
        fnPromoteOperationToInFlight(
            S_PROJECT, sOperationId,
            {
                "iHolderPid": processLive.pid,
                "iHolderProcessGroup": processLive.pid,
            },
        )
        dictResolution = fdictResolveContainerJournal(S_PROJECT)
        assert dictResolution["sResolution"] == S_RESOLUTION_BUSY
        assert dictResolution["listBusyOperationIds"] == [sOperationId]
        with pytest.raises(ContainerBusyOperationError):
            containerLock.fnAcquireContainerLock(S_PROJECT, 8050)
    finally:
        processLive.terminate()
        processLive.wait()


def test_malformed_unreadable_and_newer_journals_read_quarantined():
    """Design §13 case 37, fail-closed half: damage never reads clean.

    Garbage bytes, a bare ``{}``, and an unknown NEWER schema version
    must each resolve QUARANTINED — and the newer version must report
    that an upgrade is required rather than being auto-cleared.

    Kills: in operationJournal._fdictResolveJournalLocked, disable the
    malformed branch (``if False and ...``) so a malformed journal
    falls through to the valid-records path with zero records and
    resolves SETTLED.
    """
    _fnWriteRawJournalBytes("garbled", b"\x00\x01 not json at all")
    dictResolution = fdictResolveContainerJournal("garbled")
    assert dictResolution["sResolution"] == S_RESOLUTION_QUARANTINED
    assert dictResolution["bRequiresUpgrade"] is False
    _fnWriteRawJournalBytes("emptyobject", b"{}")
    dictResolution = fdictResolveContainerJournal("emptyobject")
    assert dictResolution["sResolution"] == S_RESOLUTION_QUARANTINED
    _fnWriteRawJournalBytes("futureversion", json.dumps({
        "iSchemaVersion": 99, "dictFutureConcept": {},
    }).encode("utf-8"))
    dictResolution = fdictResolveContainerJournal("futureversion")
    assert dictResolution["sResolution"] == S_RESOLUTION_QUARANTINED
    assert dictResolution["bRequiresUpgrade"] is True
    with pytest.raises(ContainerQuarantinedError):
        containerLock.fnAcquireContainerLock("garbled", 8050)


def test_journal_survives_a_torn_write():
    """Design §13 case 37, durability half: a failed write tears nothing.

    A write that dies between opening and durably landing (simulated by
    a failing fsync) must leave the previous journal bytes fully intact
    and leave no artifact that reads as a journal — the property the
    temp-write → fsync → atomic-replace sequence exists to provide.

    Kills: in operationJournal._fnWriteJournalBytesAtomically, set
    ``sTemporaryPath = sPath`` so the write truncates the live journal
    in place instead of staging into a temp file.
    """
    sOperationId = fsPrepareOperation(S_PROJECT, "helper", "firstOperation")

    def fnRefuseFsync(iDescriptor):
        del iDescriptor
        raise OSError("simulated crash before the write became durable")

    with patch.object(operationJournal.os, "fsync", fnRefuseFsync):
        with pytest.raises(OSError):
            fsPrepareOperation(S_PROJECT, "helper", "secondOperation")
    dictOutcome = fdictReadJournalOutcome(S_PROJECT)
    assert dictOutcome["sReadState"] == "valid"
    assert set(dictOutcome["dictOperations"]) == {sOperationId}
    listEntries = os.listdir(operationJournal._S_JOURNAL_DIRECTORY)
    assert listEntries == [f"{S_PROJECT}{operationJournal._S_JOURNAL_SUFFIX}"]


def test_newer_version_requires_upgrade_and_malformed_refuses_writes():
    """Design §13 case 42, semantics half: no ordinary path clears damage.

    An unknown NEWER schema version demands an upgrade and is never
    rewritten; a malformed journal refuses every ordinary write, so no
    acknowledge-shaped call can silently replace (and thereby clear) a
    quarantine marker. The destructive break-glass is the 3c
    reconciliation transaction; only the refusal is asserted here.

    Kills: in operationJournal._fdictLoadPayloadForWrite, replace the
    refusal of a non-valid journal with ``return
    _fdictBuildEmptyPayload(sContainerName)``, so an ordinary prepare
    on a malformed journal succeeds and rewrites the marker.
    """
    byteFutureContent = json.dumps({"iSchemaVersion": 99}).encode("utf-8")
    _fnWriteRawJournalBytes("futureversion", byteFutureContent)
    dictResolution = fdictResolveContainerJournal("futureversion")
    assert dictResolution["bRequiresUpgrade"] is True
    with open(fsJournalPathFor("futureversion"), "rb") as fileHandle:
        assert fileHandle.read() == byteFutureContent, (
            "a newer-version journal must never be rewritten"
        )
    byteMalformedContent = b"this is not a journal"
    _fnWriteRawJournalBytes(S_PROJECT, byteMalformedContent)
    with pytest.raises(OperationJournalUnreadableError):
        fsPrepareOperation(S_PROJECT, "helper", "runStepBatch")
    with pytest.raises(OperationJournalUnreadableError):
        fnMarkOperationNeedsReconciliation(S_PROJECT, "any-id")
    with open(fsJournalPathFor(S_PROJECT), "rb") as fileHandle:
        assert fileHandle.read() == byteMalformedContent, (
            "an ordinary write must not replace a quarantined journal"
        )


def test_journal_is_a_set_and_claimable_only_when_every_record_settles():
    """Design §13 case 45, journal half: a SET keyed by operation id.

    Distinct concurrent operations coexist as distinct records; the
    container becomes claimable only once EVERY record settles, and a
    single NEEDS_RECONCILIATION record quarantines it even when its
    siblings are settled. The carrier half (each operation admitted
    against its own record) lands with sub-step 3b.

    Kills: in operationJournal.fsPrepareOperation, replace the
    record-set insertion with ``dictPayload["dictOperations"] =
    {sOperationId: dictRecord}`` so each prepare silently replaces the
    whole set and coexistence is lost.
    """
    iDeadPid = _fiSpawnExitedProcessInOwnGroup()
    sHelperId = fsPrepareOperation(S_PROJECT, "helper", "runStepBatch")
    sExecId = fsPrepareOperation(S_PROJECT, "exec", "terminalCommand")
    sWriteId = fsPrepareOperation(S_PROJECT, "file-write", "project.json")
    dictOperations = fdictReadJournalOutcome(S_PROJECT)["dictOperations"]
    assert set(dictOperations) == {sHelperId, sExecId, sWriteId}
    fnPromoteOperationToInFlight(
        S_PROJECT, sHelperId,
        {"iHolderPid": iDeadPid, "iHolderProcessGroup": iDeadPid},
    )
    fnPromoteOperationToInFlight(
        S_PROJECT, sExecId, {"sDockerExecId": "feedface"},
    )
    dictResolution = fdictResolveContainerJournal(
        S_PROJECT, FakeDockerConnectionExecSettled(),
    )
    assert dictResolution["sResolution"] == S_RESOLUTION_SETTLED
    fileHandleLock = containerLock.fnAcquireContainerLock(S_PROJECT, 8050)
    containerLock.fnReleaseContainerLock(fileHandleLock)
    sSettledId = fsPrepareOperation(S_PROJECT, "helper", "neverLaunched")
    sPoisonedId = fsPrepareOperation(S_PROJECT, "exec", "terminalCommand")
    fnMarkOperationNeedsReconciliation(S_PROJECT, sPoisonedId, "test poison")
    dictResolution = fdictResolveContainerJournal(
        S_PROJECT, FakeDockerConnectionExecSettled(),
    )
    assert dictResolution["sResolution"] == S_RESOLUTION_QUARANTINED
    assert dictResolution["listAutoClearedOperationIds"] == [sSettledId]
    assert dictResolution["listQuarantinedOperationIds"] == [sPoisonedId]
    with pytest.raises(ContainerQuarantinedError):
        containerLock.fnAcquireContainerLock(S_PROJECT, 8050)
    dictRecord = fdictReadJournalOutcome(S_PROJECT)["dictOperations"][
        sPoisonedId
    ]
    assert dictRecord["sState"] == S_OPERATION_STATE_NEEDS_RECONCILIATION
