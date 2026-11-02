"""Concurrent writers of a host record never lose each other's update.

Source: ``vaibify/config/lockedJsonRecord.py``, its two users
``vaibify/config/zenodoMigrationJournal.py`` and
``vaibify/config/remoteBindings.py``, and the pass lock in
``vaibify/gui/zenodoTokenMigration.py``.

Both stores read the whole file, change one field and replace the file.
Before the lock, two writers released together on distinct keys lost
one update in each of 200 rounds, in both stores (measured 2026-11-02);
the expected value here, zero lost, is that measurement's complement.
The pass lock is taken once per pass or decision, and a decision ends
with a pass, so the decision must not take it a second time: a second
``flock`` on the same file from the holding thread blocks forever.
"""

import threading

import pytest

from vaibify.config import remoteBindings
from vaibify.config import zenodoMigrationJournal as journal
from vaibify.config import zenodoTokenSlots as slots
from vaibify.gui.zenodoTokenMigration import (
    S_DECISION_CONFIRM, fdictDecideMigratedToken, fdictRunMigrationPass,
)
from tests.testZenodoTokenMigration import (  # noqa: F401 -- fixture
    S_CONTAINER_ID, S_SANDBOX_SLOT, S_TOKEN, S_UUID, tMigration,
)

I_RACE_ROUNDS = 200
F_DECISION_TIMEOUT_SECONDS = 10.0


def _fiLostUpdates(fnWrite, fbWasKept):
    """Release two writers together per round; return how many updates vanished."""
    iLost = 0
    for iRound in range(I_RACE_ROUNDS):
        barrierStart = threading.Barrier(2)
        listKeys = [f"a{iRound}", f"b{iRound}"]

        def fnWriteAfterTheBarrier(sKey):
            barrierStart.wait()
            fnWrite(sKey)

        listThreads = [
            threading.Thread(target=fnWriteAfterTheBarrier, args=(sKey,))
            for sKey in listKeys
        ]
        for threadWriter in listThreads:
            threadWriter.start()
        for threadWriter in listThreads:
            threadWriter.join()
        iLost += sum(not fbWasKept(sKey) for sKey in listKeys)
    return iLost


@pytest.mark.falsification
def testTwoJournalWritersLoseNothing():
    """Every one of 400 slot updates, written in pairs at once, survives.

    Kills: ``fcontextHoldRecordLock`` yielding without taking the lock.
    """
    sIdentity = "race" + "0" * 28
    iLost = _fiLostUpdates(
        lambda sKey: journal.fnRecordSlotState(
            sIdentity, sKey, journal.S_STATE_ABSENT,
        ),
        lambda sKey: sKey in journal.fdictReadJournal(sIdentity)["dictSlots"],
    )
    assert iLost == 0


def testTwoBindingWritersLoseNothing():
    sIdentity = "race" + "1" * 28
    iLost = _fiLostUpdates(
        lambda sKey: remoteBindings.fnConfirmBinding(
            sIdentity, sKey, "study", "overleaf",
            {"sProjectId": "overleafProjectSynthetic"}, "researcher",
        ),
        lambda sKey: sKey in remoteBindings.fdictReadBindings(
            sIdentity,
        )["dictWorkflows"],
    )
    assert iLost == 0


def _fnStageAPendingSandboxToken(tMigration):
    source, _, _ = tMigration
    source.dictSlots[S_SANDBOX_SLOT] = S_TOKEN
    fdictRunMigrationPass(None, S_CONTAINER_ID)
    assert slots.fsReadSlot(slots.fsPendingSlot(S_SANDBOX_SLOT, S_UUID)) == S_TOKEN


def _fthreadStartDaemon(fnTarget):
    threadWorker = threading.Thread(target=fnTarget, daemon=True)
    threadWorker.start()
    return threadWorker


@pytest.mark.falsification
def testADecisionCompletesUnderItsOwnPassLock(tMigration):
    """A confirmation finishes and is recorded; it does not wait on itself.

    Kills: ``fdictDecideMigratedToken`` ending with the public
    ``fdictRunMigrationPass``, which takes the pass lock the decision
    already holds. The hung daemon thread holds only a lock under a
    temporary directory and ends with the process.
    """
    _fnStageAPendingSandboxToken(tMigration)
    threadDecision = _fthreadStartDaemon(lambda: fdictDecideMigratedToken(
        None, S_CONTAINER_ID, S_SANDBOX_SLOT, S_DECISION_CONFIRM,
    ))
    threadDecision.join(timeout=F_DECISION_TIMEOUT_SECONDS)
    assert not threadDecision.is_alive(), "the decision waited on its own lock"
    assert journal.fdictSlotRecord(S_UUID, S_SANDBOX_SLOT)["sState"] == (
        journal.S_STATE_ACTIVATED
    )


def testADecisionAndAConnectTimePassBothComplete(tMigration):
    _fnStageAPendingSandboxToken(tMigration)
    barrierStart = threading.Barrier(2)

    def fnDecide():
        barrierStart.wait()
        fdictDecideMigratedToken(
            None, S_CONTAINER_ID, S_SANDBOX_SLOT, S_DECISION_CONFIRM,
        )

    def fnPass():
        barrierStart.wait()
        fdictRunMigrationPass(None, S_CONTAINER_ID)

    listThreads = [_fthreadStartDaemon(fnDecide), _fthreadStartDaemon(fnPass)]
    for threadWorker in listThreads:
        threadWorker.join(timeout=F_DECISION_TIMEOUT_SECONDS)
    assert not any(threadWorker.is_alive() for threadWorker in listThreads)
    dictSlots = journal.fdictReadJournal(S_UUID)["dictSlots"]
    assert dictSlots[S_SANDBOX_SLOT]["sState"] == journal.S_STATE_ACTIVATED
    assert all(
        dictSlots[sSlot]["sState"] == journal.S_STATE_ABSENT
        for sSlot in slots.LIST_CONTAINER_ZENODO_SLOTS if sSlot != S_SANDBOX_SLOT
    )
    assert slots.fsReadActiveToken("sandbox", S_UUID) == S_TOKEN


def testAConfirmAndTheHubsTargetUpdateBothPersist():
    sIdentity = "race" + "2" * 28
    sKey = "projects/study.json::study"
    remoteBindings.fnConfirmBinding(
        sIdentity, sKey, "study", "zenodo", {"sInstance": "sandbox"}, "researcher",
    )
    for iRound in range(50):
        barrierStart = threading.Barrier(2)
        sProjectId = f"overleafProject{iRound}"
        sDeposition = str(1000 + iRound)

        def fnConfirmOverleaf():
            barrierStart.wait()
            remoteBindings.fnConfirmBinding(
                sIdentity, sKey, "study", "overleaf",
                {"sProjectId": sProjectId}, "researcher",
            )

        def fnMoveZenodo():
            barrierStart.wait()
            remoteBindings.fnUpdateBoundTarget(
                sIdentity, sKey, "zenodo", {"sDepositionId": sDeposition},
            )

        listThreads = [
            _fthreadStartDaemon(fnConfirmOverleaf), _fthreadStartDaemon(fnMoveZenodo),
        ]
        for threadWorker in listThreads:
            threadWorker.join()
        assert remoteBindings.fdictConfirmedBinding(
            sIdentity, sKey, "overleaf",
        )["dictTarget"]["sProjectId"] == sProjectId
        assert remoteBindings.fdictConfirmedBinding(
            sIdentity, sKey, "zenodo",
        )["dictTarget"]["sDepositionId"] == sDeposition


def testARotationAnswerAndAPassBothPersist(tMigration):
    source, _, _ = tMigration
    slots.fnStoreActiveToken("sandbox", S_UUID, "the-host-token")
    source.dictSlots[S_SANDBOX_SLOT] = S_TOKEN
    fdictRunMigrationPass(None, S_CONTAINER_ID)
    for _ in range(20):
        barrierStart = threading.Barrier(2)

        def fnAnswer():
            barrierStart.wait()
            journal.fnRecordRotationDecision(S_UUID, journal.S_ROTATION_KEPT)

        def fnPass():
            barrierStart.wait()
            fdictRunMigrationPass(None, S_CONTAINER_ID)

        sFoundBefore = journal.fdictReadJournal(S_UUID)["sLastTokenFoundAt"]
        listThreads = [_fthreadStartDaemon(fnAnswer), _fthreadStartDaemon(fnPass)]
        for threadWorker in listThreads:
            threadWorker.join(timeout=F_DECISION_TIMEOUT_SECONDS)
        dictJournal = journal.fdictReadJournal(S_UUID)
        assert dictJournal["sRotationDecision"] == journal.S_ROTATION_KEPT
        assert dictJournal["sLastTokenFoundAt"] > sFoundBefore
        assert dictJournal["dictSlots"][S_SANDBOX_SLOT]["sState"] == (
            journal.S_STATE_CONFLICT
        )
