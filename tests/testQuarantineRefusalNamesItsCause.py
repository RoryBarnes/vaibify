"""A quarantine refusal must name the operation that caused it.

The quarantine is the loudest thing in a log when it fires: it repeats
once per refused request, and every copy is a
``MutationNotAdmittedError`` that reads like a primary failure. It is
not one. It is the correct consequence of an EARLIER operation that
could not be proven finished, and until 2026-08-12 it identified that
operation by a hex id and nothing else.

That cost an hour on a real investigation. A single failed rename of
``state.json`` poisoned its record; the twelve quarantine messages
that followed were read as the defect, and the diagnosis started at
the wrong end of the causal chain. The record already carried the
kind, the target and the timestamp. Only the sentence was silent.

These tests are about a MESSAGE, which is unusual and deliberate: the
message is the entire diagnostic surface of a mechanism that hides the
failure it is protecting against.
"""

import os

import pytest

from vaibify.config import containerLock, operationJournal
from vaibify.config.mutationAdmission import (
    MutationNotAdmittedError,
    fnAssertOperationAdmittedByIdentity,
)

S_CONTAINER_NAME = "quarantine-message-project"
S_POISONED_TARGET = "/srv/project/.vaibify/state.json"


@pytest.fixture(autouse=True)
def fixtureIsolateJournal(tmp_path, monkeypatch):
    """Redirect the journal and lock roots to tmp_path."""
    monkeypatch.setattr(
        operationJournal, "_S_JOURNAL_DIRECTORY", str(tmp_path / "journal"),
    )
    monkeypatch.setattr(
        containerLock, "_S_LOCK_DIRECTORY", str(tmp_path / "locks"),
    )


def _fsPoisonARecordAndReturnTheRefusal():
    """Quarantine a file-write record; return the refusal it produces."""
    sPoisonedId = operationJournal.fsPrepareOperation(
        S_CONTAINER_NAME, "file-write", S_POISONED_TARGET,
    )
    operationJournal.fnPromoteOperationToInFlight(
        S_CONTAINER_NAME, sPoisonedId,
        {"iHolderPid": os.getpid(), "iHolderProcessGroup": os.getpgrp()},
    )
    operationJournal.fnMarkOperationNeedsReconciliation(
        S_CONTAINER_NAME, sPoisonedId, sNote="the rename failed",
    )
    sLaterId = operationJournal.fsPrepareOperation(
        S_CONTAINER_NAME, "exec", "a later, innocent operation",
    )
    operationJournal.fnPromoteOperationToInFlight(
        S_CONTAINER_NAME, sLaterId, {"sDockerExecId": "later"},
    )
    with pytest.raises(MutationNotAdmittedError) as excRefusal:
        fnAssertOperationAdmittedByIdentity(
            S_CONTAINER_NAME, sLaterId, {"sDockerExecId": "later"},
        )
    return str(excRefusal.value)


@pytest.mark.falsification
def testTheRefusalNamesTheOperationThatCausedIt():
    """Kind and target, not a hex id alone.

    A reader who has just been handed this message needs to know where
    to look. The kind says what was happening; the target says to
    what. Both are journal-native fields — an allowlisted kind and a
    bounded string — so naming them leaks nothing and stores nothing
    new.

    Kills: identifying the quarantining record by its id alone, which
    is the sentence this replaced.
    """
    sRefusal = _fsPoisonARecordAndReturnTheRefusal()
    assert "file-write" in sRefusal, sRefusal
    assert S_POISONED_TARGET in sRefusal, sRefusal


def testTheRefusalTimestampsTheOperation():
    """When it happened separates this run's failure from last week's.

    A quarantine survives a crash and a restart by design, so the
    record refusing today's request may be days old — and a researcher
    reading the message cannot otherwise tell.
    """
    sRefusal = _fsPoisonARecordAndReturnTheRefusal()
    dictPayload = operationJournal.fdictReadJournalOutcome(
        S_CONTAINER_NAME,
    )["dictOperations"]
    listStamps = [
        dictRecord.get("sInFlightIso") or dictRecord.get("sPreparedIso")
        for dictRecord in dictPayload.values()
    ]
    assert any(sStamp and sStamp in sRefusal for sStamp in listStamps), (
        sRefusal, listStamps,
    )


@pytest.mark.falsification
def testTheRefusalSaysTheFailureIsElsewhere():
    """The one sentence that would have redirected the investigation.

    Every reader of this message is holding a traceback that ends
    here, and the mechanism's own correctness makes that traceback
    look like the bug. Saying plainly that the original failure is in
    the named operation is the difference between an hour and a
    minute.

    Kills: dropping the redirection, leaving a refusal that is
    accurate about the state and silent about where to look.
    """
    sRefusal = _fsPoisonARecordAndReturnTheRefusal()
    assert "not here" in sRefusal.lower(), sRefusal


def testAnUnquarantinedContainerIsStillAdmitted():
    """The other direction: naming a cause must not invent one.

    A gate that refused every operation would satisfy every assertion
    above, and this is the test that says it does not.
    """
    sOperationId = operationJournal.fsPrepareOperation(
        S_CONTAINER_NAME, "exec", "ordinary work",
    )
    operationJournal.fnPromoteOperationToInFlight(
        S_CONTAINER_NAME, sOperationId, {"sDockerExecId": "fine"},
    )
    fnAssertOperationAdmittedByIdentity(
        S_CONTAINER_NAME, sOperationId, {"sDockerExecId": "fine"},
    )
