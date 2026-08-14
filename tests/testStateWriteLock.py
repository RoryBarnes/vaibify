"""The state write lock actually excludes concurrent cooperative writers.

Slice 2 of the workflow-consistency spec (§4.1). Schema v3 made the
save a read-modify-write, which preserves sibling sections
SEQUENTIALLY; with no lock, two overlapping savers each install their
own section over a stale read and the later write drops the earlier.
The write primitive is a cross-process flock held from the read
through the rename.

These tests drive TWO REAL THREADS against a REAL flock (the lock
directory is redirected into the test's tmp dir). The interleave is
forced, not hoped for: the first writer's document read sleeps long
enough for the second writer's whole read-modify-write to complete —
so without exclusion the lost update happens EVERY run, and with it
the second writer provably blocks until the first finishes. Both
halves are asserted: both sections survive, AND the writes were
serialized in lock order — a test that only checked survival would
pass if the second writer never ran at all.
"""

import json
import threading
import time

import pytest

from vaibify.gui import stateWriteLock
from vaibify.gui.stateManager import (
    fdictMergeRunResultsIntoState,
    fnSaveStateToContainer,
)


S_CONTAINER_ID = "cid-write-lock-demo"
S_STATE_PATH = "/workspace/exampleRepo/.vaibify/state.json"
S_KEY_A = ".vaibify/projects/alpha.json"
S_KEY_B = ".vaibify/projects/beta.json"
F_FIRST_READ_SLEEP_SECONDS = 0.4


class _ConnectionWithSlowFirstRead:
    """Files that behave like files, with the FIRST document read slow.

    The sleep sits inside the first reader's read — squarely between
    its read and its write — so a second writer that is not excluded
    completes its entire read-modify-write inside the window, and the
    first writer then installs its section over a stale document. The
    write ORDER is recorded so serialization itself is assertable.
    """

    def __init__(self):
        self.dictFiles = {}
        self.listWriteOrder = []
        self.bFirstReadDone = False
        self.lockInternal = threading.Lock()

    def fbaFetchFile(self, _sContainerId, sPath):
        with self.lockInternal:
            bSleepThisRead = (
                sPath == S_STATE_PATH and not self.bFirstReadDone
            )
            if bSleepThisRead:
                self.bFirstReadDone = True
            baContent = self.dictFiles.get(sPath)
        if bSleepThisRead:
            time.sleep(F_FIRST_READ_SLEEP_SECONDS)
        if baContent is None:
            raise FileNotFoundError(sPath)
        return baContent

    def fnWriteFile(self, _sContainerId, sPath, baPayload):
        with self.lockInternal:
            self.dictFiles[sPath] = baPayload

    def ftResultExecuteCommand(self, _sContainerId, sCommand):
        import re
        matchMove = re.match(
            r"mv (?:-f )?'([^']+)' '([^']+)'", sCommand,
        )
        if matchMove:
            sSource, sDestination = matchMove.groups()
            with self.lockInternal:
                if sSource not in self.dictFiles:
                    return (1, f"mv: {sSource}: no such file")
                self.dictFiles[sDestination] = self.dictFiles.pop(
                    sSource,
                )
                if sDestination == S_STATE_PATH:
                    self.listWriteOrder.append(
                        threading.current_thread().name,
                    )
        return (0, "")

    def fdictReadState(self):
        return json.loads(self.dictFiles[S_STATE_PATH].decode("utf-8"))


def _fdictSectionWith(sMarkerValue):
    return {
        "dictStepState": {
            "analyze": {"dictVerification": {"sUser": sMarkerValue}},
        },
    }


@pytest.fixture()
def pathLockDirectory(tmp_path, monkeypatch):
    """Point the lock directory into this test's private tmp dir."""
    pathLocks = tmp_path / "locks"
    monkeypatch.setattr(
        stateWriteLock, "S_STATE_LOCK_DIRECTORY", str(pathLocks),
    )
    return pathLocks


@pytest.mark.falsification
def testTwoConcurrentSaversBothSurvive(pathLockDirectory):
    """Kills: a write lock that never takes the flock.

    Writer A (slow read) saves workflow A's section; writer B saves
    workflow B's concurrently. Unexcluded, B's whole read-modify-write
    lands inside A's read-to-write window and A's install erases B's
    section — the schema-v3 sequential guarantee is not a concurrency
    guarantee, which is why the lock exists.
    """
    connection = _ConnectionWithSlowFirstRead()

    def fnSaveSection(sWorkflowKey, sMarkerValue):
        fnSaveStateToContainer(
            connection, S_CONTAINER_ID, S_STATE_PATH,
            _fdictSectionWith(sMarkerValue), sWorkflowKey=sWorkflowKey,
        )

    threadWriterA = threading.Thread(
        target=fnSaveSection, args=(S_KEY_A, "saved-by-A"),
        name="writerA",
    )
    threadWriterB = threading.Thread(
        target=fnSaveSection, args=(S_KEY_B, "saved-by-B"),
        name="writerB",
    )
    threadWriterA.start()
    # Give A time to enter its (slow) read while holding the lock,
    # so B genuinely contends instead of winning the lock first.
    time.sleep(F_FIRST_READ_SLEEP_SECONDS / 4)
    threadWriterB.start()
    threadWriterA.join(timeout=30)
    threadWriterB.join(timeout=30)
    assert not threadWriterA.is_alive() and not threadWriterB.is_alive()

    dictSections = connection.fdictReadState()["dictWorkflowState"]
    assert S_KEY_A in dictSections, "writer A's own section vanished"
    assert S_KEY_B in dictSections, (
        "writer B's section was erased by writer A's stale install — "
        "the lost update the write lock exists to prevent"
    )
    assert connection.listWriteOrder == ["writerA", "writerB"], (
        "the writes were not serialized in lock order; survival was "
        f"luck, not exclusion: {connection.listWriteOrder}"
    )


@pytest.mark.falsification
def testTheCompletionMergeContendsOnTheSameLock(pathLockDirectory):
    """Kills: the completion merge acquiring a lock of its own.

    The merge and the save write the SAME document, so they must hold
    the SAME lock — a merge keyed differently excludes nothing, and a
    run's completion racing a researcher's save re-creates the lost
    update inside one workflow's section.
    """
    connection = _ConnectionWithSlowFirstRead()

    def fnSaveSlowly():
        fnSaveStateToContainer(
            connection, S_CONTAINER_ID, S_STATE_PATH,
            _fdictSectionWith("saved-by-A"), sWorkflowKey=S_KEY_A,
        )

    dictOutcome = {}

    def fnMergeRunResults():
        dictOutcome.update(fdictMergeRunResultsIntoState(
            connection, S_CONTAINER_ID, S_STATE_PATH, S_KEY_B,
            {"analyze": {"fWallClock": 4.2}}, {"analyze": "Analyze"},
        ))

    threadSaver = threading.Thread(
        target=fnSaveSlowly, name="writerA",
    )
    threadMerger = threading.Thread(
        target=fnMergeRunResults, name="writerB",
    )
    threadSaver.start()
    time.sleep(F_FIRST_READ_SLEEP_SECONDS / 4)
    threadMerger.start()
    threadSaver.join(timeout=30)
    threadMerger.join(timeout=30)
    assert not threadSaver.is_alive() and not threadMerger.is_alive()

    assert dictOutcome.get("bPersisted") is True
    dictSections = connection.fdictReadState()["dictWorkflowState"]
    assert S_KEY_A in dictSections, (
        "the researcher's save was erased by the completion merge"
    )
    assert dictSections[S_KEY_B]["dictStepState"]["analyze"][
        "dictRunStats"
    ] == {"fWallClock": 4.2}, "the run's results vanished"
    assert connection.listWriteOrder == ["writerA", "writerB"], (
        "the merge did not wait for the save; they hold different "
        f"locks: {connection.listWriteOrder}"
    )


def testTheLockFileIsNeverDeleted(pathLockDirectory):
    """Unlinking a held lock file splits the lock in two.

    The exclusion property depends on every writer flocking the SAME
    inode; a cleanup that deletes 'stale' lock files would hand the
    next writer a fresh inode while the current holder still holds the
    old one. The lock file must survive its holder.
    """
    with stateWriteLock.fcontextHoldStateWriteLock(
        S_CONTAINER_ID, S_STATE_PATH,
    ):
        pass
    import os
    sLockPath = stateWriteLock.fsResolveLockFilePath(
        S_CONTAINER_ID, S_STATE_PATH,
    )
    assert os.path.exists(sLockPath), (
        "the lock file was removed on release; the next writer flocks "
        "a different inode than a concurrent holder"
    )
