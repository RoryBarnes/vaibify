"""A terminal record that never learned its group settles on a stopped container.

A terminal whose start was refused before its process group was
recorded leaves a journal record nothing inside the container can
prove empty, so the container quarantines. Stopping the container is
the documented escape — no process survives a stop — but the probe
refused before it ever asked the daemon about the container, so a
researcher who had stopped (and so removed) the container could clear
the quarantine by no route at all: Reconcile refused, and
stop-and-certify had nothing left to stop.

The probe now asks the daemon for the recorded container's state. These
tests pin that a removed or stopped container settles the record, and
that a running or paused one never does, because both still hold the
shell. Each guarantee carries its kill on a ``Kills:`` line and in
``tests/falsificationRegistry.py``.
"""

import pytest

from vaibify.config import operationJournal


class ContainerStateConnection:
    """A Docker stand-in whose exec is dead and whose container answers."""

    def __init__(self, valueState):
        self.valueState = valueState

    def fdictInspectExec(self, sExecId):
        return {"Running": False}

    def fdictReadContainerState(self, sContainerId):
        if isinstance(self.valueState, BaseException):
            raise self.valueState
        return self.valueState


def _ftResolveGroupLessTerminal(valueState):
    """Return the resolver's (verdict, detail) for a group-less record."""
    dictRecord = {
        "sState": operationJournal.S_OPERATION_STATE_IN_FLIGHT,
        "sKind": "terminal", "sTarget": "targetAlpha",
        "sPreparedIso": "2026-01-01T00:00:00",
        "sInFlightIso": "2026-01-01T00:00:01",
        "sDockerExecId": "execAlpha",
        "sDockerContainerId": "containerAlpha",
    }
    return operationJournal._ftResolveOperationRecord(
        dictRecord, ContainerStateConnection(valueState),
    )


@pytest.mark.falsification
def testAGroupLessTerminalSettlesOnceItsContainerIsGone():
    """Kills: dropping the removed-container clause from the probe."""
    sVerdict, sDetail = _ftResolveGroupLessTerminal(None)
    assert sVerdict == "settled", sDetail
    assert "no longer exists" in sDetail


@pytest.mark.falsification
def testAGroupLessTerminalSettlesOnceItsContainerIsStopped():
    """Kills: dropping the stopped-container clause from the probe."""
    sVerdict, sDetail = _ftResolveGroupLessTerminal(
        {"Running": False, "Status": "exited"},
    )
    assert sVerdict == "settled", sDetail
    assert "stopped" in sDetail


@pytest.mark.falsification
def testAGroupLessTerminalInARunningOrPausedContainerStaysQuarantined():
    """Kills: settling a paused container, whose processes all survive."""
    for dictState in (
        {"Running": True, "Paused": False},
        {"Running": True, "Paused": True},
        {},
    ):
        sVerdict, sDetail = _ftResolveGroupLessTerminal(dictState)
        assert sVerdict == "quarantinePermanent", (dictState, sDetail)
        assert "must be stopped" in sDetail


def testAnUnreadableContainerStateIsTransientNeverSettled():
    sVerdict, sDetail = _ftResolveGroupLessTerminal(
        RuntimeError("daemon did not answer"),
    )
    assert sVerdict == "quarantineTransient", sDetail
    assert "could not be read" in sDetail
