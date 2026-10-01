"""Fail-closed branches of terminalContainment's terminate-and-prove.

The operation journal is REAL (redirected into tmp_path); the Docker
connection is a recorder with scriptable failures, because the daemon
is the external boundary. Every assertion is about the record's final
state and what the journal says afterwards: a quarantine that did not
poison the journal, or a settle that left a record behind, is the
defect these tests exist to catch.
"""

import logging
import subprocess
import sys

import pytest

from vaibify.config import containerLock, operationJournal
from vaibify.gui import terminalContainment
from vaibify.gui.terminalContainment import (
    TerminalContainmentError,
    TerminalExecutionRecord,
    fdictTerminateAndProveRecord,
)


S_CONTAINER_NAME = "containerAlpha"
S_CONTAINER_ID = "c0ffee00dockerid"
S_EXEC_ID = "execIdentifierAlpha"


@pytest.fixture(autouse=True)
def fixtureIsolateJournal(tmp_path, monkeypatch):
    """Redirect the operation journal and locks into tmp_path."""
    monkeypatch.setattr(
        operationJournal, "_S_JOURNAL_DIRECTORY", str(tmp_path / "journal"),
    )
    monkeypatch.setattr(
        containerLock, "_S_LOCK_DIRECTORY", str(tmp_path / "locks"),
    )


class ContainmentConnectionRecorder:
    """Docker stand-in whose probes, signals and inspects can fail."""

    def __init__(self):
        self.listSignals = []
        self.exceptionSignal = None
        self.exceptionProbe = None
        self.exceptionInspect = None
        self.exceptionShellProbe = None
        self.iMemberCount = 0
        self.bExecRunning = False
        self.listShellProbes = []

    def fnSignalProcessGroupMembers(self, sContainerId, iGroup, sSignalName):
        self.listSignals.append((sContainerId, iGroup, sSignalName))
        if self.exceptionSignal:
            raise self.exceptionSignal

    def fdictProbeProcessGroupMembers(self, sContainerId, iGroup):
        if self.exceptionProbe:
            raise self.exceptionProbe
        return {
            "bConclusive": True, "iMemberCount": self.iMemberCount,
            "sDetail": f"{self.iMemberCount} member(s)",
        }

    def fdictInspectExec(self, sExecId):
        if self.exceptionInspect:
            raise self.exceptionInspect
        return {"Running": self.bExecRunning}

    def ftRunRootShellProbe(self, sContainerId, sScript):
        self.listShellProbes.append(sContainerId)
        if self.exceptionShellProbe:
            raise self.exceptionShellProbe
        return (1, "")


def fsJournalOperation(iProcessGroup=0, sExecId=S_EXEC_ID):
    """Prepare and promote a real in-flight terminal journal record."""
    sOperationId = operationJournal.fsPrepareOperation(
        S_CONTAINER_NAME, "terminal", S_CONTAINER_ID,
    )
    operationJournal.fnPromoteOperationToInFlight(
        S_CONTAINER_NAME, sOperationId,
        {"sDockerExecId": sExecId, "sDockerContainerId": S_CONTAINER_ID},
    )
    if iProcessGroup > 0:
        operationJournal.fnAmendInFlightHolderIdentity(
            S_CONTAINER_NAME, sOperationId,
            {"iHolderProcessGroup": iProcessGroup},
        )
    return sOperationId


def frecordBuild(connection, sOperationId, iProcessGroup=0,
                 sExecId=S_EXEC_ID, iHolderPid=0, dictRegistry=None):
    """Return a terminal record over a journal operation."""
    return TerminalExecutionRecord(
        sOperationId=sOperationId, sContainerName=S_CONTAINER_NAME,
        sContainerId=S_CONTAINER_ID, sDockerExecId=sExecId,
        iOwnerGeneration=1, connectionDocker=connection,
        dictRegistry=dictRegistry, iProcessGroup=iProcessGroup,
        iHolderPid=iHolderPid,
    )


def fdictJournalOperations():
    """Return the container's journal records keyed by operation id."""
    return operationJournal.fdictReadJournalOutcome(S_CONTAINER_NAME)[
        "dictOperations"
    ]


def fnPoisonOperation(sOperationId):
    """Mark an operation as needing reconciliation, as a reconcile would."""
    operationJournal.fnMarkOperationNeedsReconciliation(
        S_CONTAINER_NAME, sOperationId, sNote="poisoned elsewhere",
    )


def testDiscoveryThatNeverSeesTheMarkerRefusesTheTerminal():
    """A probe that raises every time is folded to not-ready, then refused."""
    connection = ContainmentConnectionRecorder()
    connection.exceptionShellProbe = RuntimeError("exec failed")
    with pytest.raises(TerminalContainmentError, match="never reported"):
        terminalContainment.fiDiscoverTerminalProcessGroup(
            connection, S_CONTAINER_ID, "/tmp/.vaibifyTerminalGroup.x",
            fTimeoutSeconds=0,
        )
    assert connection.listShellProbes == [S_CONTAINER_ID]


@pytest.mark.parametrize(
    "sState,bExpectedProven",
    [
        (terminalContainment.S_RECORD_STATE_SETTLED, True),
        (terminalContainment.S_RECORD_STATE_QUARANTINED, False),
    ],
)
def testAnAlreadyResolvedRecordIsNotDrainedTwice(sState, bExpectedProven):
    """A settled or quarantined record answers from its state, no signals."""
    connection = ContainmentConnectionRecorder()
    recordTerminal = frecordBuild(connection, "operationUnused", 55)
    recordTerminal.sState = sState
    dictOutcome = fdictTerminateAndProveRecord(recordTerminal)
    assert dictOutcome == {
        "bProvenEmpty": bExpectedProven, "sDetail": f"record already {sState}",
    }
    assert connection.listSignals == []


def testFailedSignalStillSettlesWhenTheProbeProvesEmpty():
    """A signal error is logged; a conclusive empty probe still settles."""
    connection = ContainmentConnectionRecorder()
    connection.exceptionSignal = RuntimeError("signal refused")
    sOperationId = fsJournalOperation(iProcessGroup=55)
    dictRegistry = {}
    recordTerminal = frecordBuild(
        connection, sOperationId, 55, dictRegistry=dictRegistry,
    )
    dictOutcome = fdictTerminateAndProveRecord(recordTerminal, 0, 0)
    assert dictOutcome["bProvenEmpty"] is True
    assert recordTerminal.sState == terminalContainment.S_RECORD_STATE_SETTLED
    assert connection.listSignals == [(S_CONTAINER_ID, 55, "TERM")]
    assert sOperationId not in fdictJournalOperations()
    assert dictRegistry == {}


def testRaisingProbeEscalatesToKillAndQuarantines():
    """An exception from the probe is inconclusive, never proof of empty."""
    connection = ContainmentConnectionRecorder()
    connection.exceptionProbe = RuntimeError("daemon hung up")
    sOperationId = fsJournalOperation(iProcessGroup=55)
    recordTerminal = frecordBuild(connection, sOperationId, 55)
    dictOutcome = fdictTerminateAndProveRecord(recordTerminal, 0, 0)
    assert dictOutcome["bProvenEmpty"] is False
    assert "process-group probe raised: daemon hung up" in dictOutcome["sDetail"]
    assert [tSignal[2] for tSignal in connection.listSignals] == ["TERM", "KILL"]
    dictRecord = fdictJournalOperations()[sOperationId]
    assert dictRecord["sState"] == (
        operationJournal.S_OPERATION_STATE_NEEDS_RECONCILIATION
    )


def testEmptyGroupWhoseRecordCannotSettleIsQuarantined():
    """Proof of empty is not enough when the journal refuses the settle."""
    connection = ContainmentConnectionRecorder()
    sOperationId = fsJournalOperation(iProcessGroup=55)
    fnPoisonOperation(sOperationId)
    recordTerminal = frecordBuild(connection, sOperationId, 55)
    dictOutcome = fdictTerminateAndProveRecord(recordTerminal, 0, 0)
    assert dictOutcome["bProvenEmpty"] is False
    assert "group is empty but the record could not settle" in (
        dictOutcome["sDetail"]
    )
    assert recordTerminal.sState == (
        terminalContainment.S_RECORD_STATE_QUARANTINED
    )


def testUndiscoveredExecWhoseStateCannotBeReadIsQuarantined():
    """No group and no inspect answer means the exec may be live: quarantine."""
    connection = ContainmentConnectionRecorder()
    connection.exceptionInspect = RuntimeError("inspect failed")
    sOperationId = fsJournalOperation()
    recordTerminal = frecordBuild(connection, sOperationId)
    dictOutcome = fdictTerminateAndProveRecord(recordTerminal)
    assert dictOutcome["bProvenEmpty"] is False
    assert "exec state could not be read: inspect failed" in (
        dictOutcome["sDetail"]
    )
    assert fdictJournalOperations()[sOperationId]["sState"] == (
        operationJournal.S_OPERATION_STATE_NEEDS_RECONCILIATION
    )


def testDeadUndiscoveredExecWhoseRecordCannotSettleIsQuarantined():
    """A dead exec settles only if the journal agrees; otherwise quarantine."""
    connection = ContainmentConnectionRecorder()
    sOperationId = fsJournalOperation()
    fnPoisonOperation(sOperationId)
    recordTerminal = frecordBuild(connection, sOperationId)
    dictOutcome = fdictTerminateAndProveRecord(recordTerminal)
    assert dictOutcome["bProvenEmpty"] is False
    assert "could not settle the record" in dictOutcome["sDetail"]


def fiReapedChildPid():
    """Return the pid of a child process that has already exited."""
    processChild = subprocess.Popen([sys.executable, "-c", "pass"])
    processChild.wait()
    return processChild.pid


def testDeadHostShellWithoutLeadershipSettlesItsRecord():
    """A host shell that died before its gate provably spawned nothing."""
    connection = ContainmentConnectionRecorder()
    sOperationId = fsJournalOperation(sExecId="")
    dictRegistry = {S_CONTAINER_NAME: {}}
    recordTerminal = frecordBuild(
        connection, sOperationId, sExecId="", iHolderPid=fiReapedChildPid(),
        dictRegistry=dictRegistry,
    )
    dictOutcome = fdictTerminateAndProveRecord(recordTerminal)
    assert dictOutcome["bProvenEmpty"] is True
    assert "died before its gate opened" in dictOutcome["sDetail"]
    assert sOperationId not in fdictJournalOperations()
    assert recordTerminal.sState == terminalContainment.S_RECORD_STATE_SETTLED


def testDeadHostShellWhoseRecordCannotSettleIsQuarantined():
    """The host twin of the settle refusal also fails closed."""
    connection = ContainmentConnectionRecorder()
    sOperationId = fsJournalOperation(sExecId="")
    fnPoisonOperation(sOperationId)
    recordTerminal = frecordBuild(
        connection, sOperationId, sExecId="", iHolderPid=fiReapedChildPid(),
    )
    dictOutcome = fdictTerminateAndProveRecord(recordTerminal)
    assert dictOutcome["bProvenEmpty"] is False
    assert "could not settle the record" in dictOutcome["sDetail"]


def testQuarantineOfAnUnjournaledRecordLogsAndStillQuarantines(caplog):
    """A record the journal never held cannot be poisoned; that is logged."""
    connection = ContainmentConnectionRecorder()
    connection.bExecRunning = True
    recordTerminal = frecordBuild(connection, "operationNeverJournaled")
    with caplog.at_level(logging.WARNING, logger="vaibify"):
        dictOutcome = fdictTerminateAndProveRecord(recordTerminal)
    assert dictOutcome["bProvenEmpty"] is False
    assert recordTerminal.sState == (
        terminalContainment.S_RECORD_STATE_QUARANTINED
    )
    assert any(
        "Could not poison terminal record operationNeverJournaled" in
        recordLog.getMessage() for recordLog in caplog.records
    )
