"""A terminal whose owning hub died is ended, with proof, by a reaper pass.

A Docker terminal record holds an exec id, a container id and, after
discovery, the shell's process group; it holds no hub pid. Owner death
is known only from the container flock: a hub holds it while it owns
the container, release drains terminals first, and lock release keeps
the flock while live terminal records remain, so a FREE flock is proof
of a dead owner. Before this, such a record read as "busy" to every
claim forever, because its exec was still running. The reaper now ends
it through the one exit every terminal record has, terminate-and-prove,
so it settles or quarantines and never merely disappears.
"""

import fcntl
import os

import pytest

from vaibify.config import containerLock, operationJournal
from vaibify.gui import appFactory, terminalContainment

S_PROJECT = "orphanedTerminalProject"
S_CONTAINER_ID = "c0ffee" * 10 + "c0ff"
I_GROUP = 4321


class ConnectionDockerFake:
    """A daemon whose exec keeps running until its group is signalled.

    Fail-closed on the container id: a probe of any other container
    raises, so a reaper that consulted the wrong record is visible.
    """

    def __init__(self):
        self.listSignals = []
        self.bGroupEmpty = False

    def fdictInspectExec(self, sExecId):
        return {"Running": not self.bGroupEmpty}

    def fnSignalProcessGroupMembers(self, sContainerId, iProcessGroup, sSignalName):
        assert sContainerId == S_CONTAINER_ID
        self.listSignals.append((iProcessGroup, sSignalName))
        self.bGroupEmpty = True

    def fdictProbeProcessGroupMembers(self, sContainerId, iProcessGroup):
        assert sContainerId == S_CONTAINER_ID
        return {"bConclusive": True,
                "iMemberCount": 0 if self.bGroupEmpty else 1, "sDetail": ""}


def _fsJournalTerminalRecord(dictIdentityExtra=None):
    sOperationId = operationJournal.fsPrepareOperation(
        S_PROJECT, terminalContainment.S_TERMINAL_OPERATION_KIND, S_CONTAINER_ID)
    dictIdentity = {"sDockerExecId": "exec-orphan",
                    "sDockerContainerId": S_CONTAINER_ID}
    dictIdentity.update(dictIdentityExtra or {})
    operationJournal.fnPromoteOperationToInFlight(S_PROJECT, sOperationId, dictIdentity)
    return sOperationId


def _fdictJournalRecords():
    return operationJournal.fdictReadJournalOutcome(S_PROJECT)["dictOperations"]


def _fnRunReaperPass(connectionDocker):
    containerLock.fnReapStaleContainerLocks(
        connectionDocker,
        fnTerminateOrphanedTerminals=(
            terminalContainment.fdictTerminateOrphanedJournalRecords),
    )


@pytest.mark.falsification
def test_a_free_flock_and_a_running_exec_are_terminated_and_settled():
    """Kills: the terminator call in ``containerLock``'s
    ``_fnTerminateOrphansThenResolve`` removed, so the free-flock pass
    only resolves the journal, meets a running exec, and leaves the
    container busy forever.

    Oracle: the journal file on disk and the daemon double's signal
    log, never the reaper's own return value.
    """
    _fsJournalTerminalRecord({"iHolderProcessGroup": I_GROUP})
    connectionDocker = ConnectionDockerFake()
    _fnRunReaperPass(connectionDocker)
    assert (I_GROUP, "TERM") in connectionDocker.listSignals
    assert _fdictJournalRecords() == {}, "the record was not settled"


def test_a_free_lock_file_counts_as_a_dead_owner_too():
    _fsJournalTerminalRecord({"iHolderProcessGroup": I_GROUP})
    containerLock._fnEnsureLockDirectory()
    with open(containerLock.fsLockPathFor(S_PROJECT), "w") as fileHandle:
        fileHandle.write("{}")
    connectionDocker = ConnectionDockerFake()
    _fnRunReaperPass(connectionDocker)
    assert connectionDocker.listSignals
    assert _fdictJournalRecords() == {}


def test_a_held_flock_is_left_alone():
    sOperationId = _fsJournalTerminalRecord({"iHolderProcessGroup": I_GROUP})
    containerLock._fnEnsureLockDirectory()
    fileHandleHolder = containerLock._ffileOpenLockFileNoFollow(
        containerLock.fsLockPathFor(S_PROJECT))
    fcntl.flock(fileHandleHolder, fcntl.LOCK_EX | fcntl.LOCK_NB)
    try:
        connectionDocker = ConnectionDockerFake()
        _fnRunReaperPass(connectionDocker)
    finally:
        fileHandleHolder.close()
    assert connectionDocker.listSignals == [], "a live hub's terminal was signalled"
    assert _fdictJournalRecords()[sOperationId]["sState"] == "IN_FLIGHT"


def test_a_quarantined_record_is_skipped():
    sOperationId = _fsJournalTerminalRecord({"iHolderProcessGroup": I_GROUP})
    operationJournal.fnMarkOperationNeedsReconciliation(
        S_PROJECT, sOperationId, sNote="left by a test")
    connectionDocker = ConnectionDockerFake()
    _fnRunReaperPass(connectionDocker)
    assert connectionDocker.listSignals == []
    assert _fdictJournalRecords()[sOperationId]["sState"] == "NEEDS_RECONCILIATION"


def test_a_record_without_a_group_is_left_to_reconcile():
    sOperationId = _fsJournalTerminalRecord()
    connectionDocker = ConnectionDockerFake()
    _fnRunReaperPass(connectionDocker)
    assert connectionDocker.listSignals == []
    assert sOperationId in _fdictJournalRecords()


def test_the_hub_reaper_declines_without_a_daemon_and_counts_with_one():
    _fsJournalTerminalRecord({"iHolderProcessGroup": I_GROUP})
    dictDeclined = appFactory._fdictReapOrphanedTerminals({"docker": None})
    assert dictDeclined["sOutcome"] == "forbidden"
    assert dictDeclined["sRemedy"]
    dictRan = appFactory._fdictReapOrphanedTerminals(
        {"docker": ConnectionDockerFake()})
    assert dictRan == {"sOutcome": "ran", "iRemoved": 1, "sReason": "", "sRemedy": ""}
    assert _fdictJournalRecords() == {}


def test_the_startup_reap_keeps_its_terminator_free_signature():
    """``fnReapStaleContainerLocks()`` with no arguments still works."""
    _fsJournalTerminalRecord({"iHolderProcessGroup": I_GROUP})
    containerLock.fnReapStaleContainerLocks()
    assert os.path.exists(operationJournal.fsJournalPathFor(S_PROJECT))
