"""Shadow-rerun lock files stop accumulating, and the backlog clears safely.

The lane lock was never unlinked: one file per container incarnation
and per reproduction token, hundreds of empty files over a season. The
release now unlinks the file while still holding its flock, after an
inode re-check that keeps a lock on an orphaned inode from passing as
exclusion, and a reaper removes the backlog by taking each free file's
flock before unlinking it.
"""

import fcntl
import os

import pytest

from vaibify.config import containerLock
from vaibify.config.pidFileRegistry import ffileOpenNoFollow
from vaibify.reproducibility import shadowRerun


def _flistShadowLocks():
    sDirectory = containerLock.fsGetLockDirectory()
    return sorted(s for s in os.listdir(sDirectory)
                  if s.startswith("shadow-")) if os.path.isdir(sDirectory) else []


def test_the_lock_file_exists_while_held_and_is_gone_after_release():
    with shadowRerun._fcontextHoldShadowLaneLock("resource-a"):
        assert len(_flistShadowLocks()) == 1
    assert _flistShadowLocks() == []


def test_a_second_rerun_of_the_same_resource_is_still_refused():
    with shadowRerun._fcontextHoldShadowLaneLock("resource-a"):
        with pytest.raises(shadowRerun.ShadowRerunRefusedError):
            with shadowRerun._fcontextHoldShadowLaneLock("resource-a"):
                pass
    assert _flistShadowLocks() == []


def test_an_inode_unlinked_between_open_and_flock_is_retried(monkeypatch):
    """The race the inode check exists for, made deterministic.

    The first open returns a handle whose file is then unlinked before
    the flock, exactly as a release or the reaper would; a lock on that
    orphan must not be returned, because it excludes nobody.
    """
    sLockPath = os.path.join(containerLock.fsGetLockDirectory(), "shadow-" + "0" * 16 + ".lock")
    os.makedirs(os.path.dirname(sLockPath), exist_ok=True)
    listOpened = []

    def ffileOpenThenLoseTheFirst(sPath):
        fileHandle = ffileOpenNoFollow(sPath)
        listOpened.append(fileHandle)
        if len(listOpened) == 1:
            os.unlink(sPath)
        return fileHandle

    monkeypatch.setattr(
        "vaibify.config.pidFileRegistry.ffileOpenNoFollow", ffileOpenThenLoseTheFirst)
    fileHandleLock = shadowRerun._ffileAcquireShadowLaneLock(sLockPath)
    try:
        assert len(listOpened) == 2, "the orphaned inode was accepted as the lock"
        assert shadowRerun._fbLockHandleIsStillThePath(fileHandleLock, sLockPath)
    finally:
        shadowRerun._fnReleaseShadowLaneLock(fileHandleLock, sLockPath)
    assert not os.path.exists(sLockPath)


def test_the_reaper_removes_free_shadow_locks_and_nothing_else():
    sDirectory = containerLock.fsGetLockDirectory()
    os.makedirs(sDirectory, exist_ok=True)
    sFree = os.path.join(sDirectory, "shadow-" + "a" * 16 + ".lock")
    sState = os.path.join(sDirectory, "state-project.lock")
    sNotEmpty = os.path.join(sDirectory, "shadow-" + "b" * 16 + ".lock")
    for sPath, sContent in ((sFree, ""), (sState, ""), (sNotEmpty, "{}")):
        with open(sPath, "w") as fileHandle:
            fileHandle.write(sContent)
    with shadowRerun._fcontextHoldShadowLaneLock("held-resource"):
        [sHeld] = [s for s in _flistShadowLocks()
                   if s not in (os.path.basename(sFree), os.path.basename(sNotEmpty))]
        assert shadowRerun.fiReapFreeShadowLaneLocks() == 1
        assert os.path.exists(os.path.join(sDirectory, sHeld)), "a held lock was reaped"
    assert not os.path.exists(sFree)
    assert os.path.exists(sState), "state locks are never this reaper's"
    assert os.path.exists(sNotEmpty), "only an empty file is a shadow lock"


def test_a_lock_held_by_another_handle_survives_the_reap():
    sDirectory = containerLock.fsGetLockDirectory()
    os.makedirs(sDirectory, exist_ok=True)
    sHeld = os.path.join(sDirectory, "shadow-" + "c" * 16 + ".lock")
    fileHandleHolder = ffileOpenNoFollow(sHeld)
    fcntl.flock(fileHandleHolder, fcntl.LOCK_EX | fcntl.LOCK_NB)
    try:
        assert shadowRerun.fiReapFreeShadowLaneLocks() == 0
        assert os.path.exists(sHeld)
    finally:
        fileHandleHolder.close()


def test_the_hub_registers_the_shadow_lock_reaper():
    from fastapi import FastAPI
    from vaibify.gui import appFactory, remnantReapers
    app = FastAPI()
    app.state.listLifespanStartup = []
    app.state.listLifespanShutdown = []
    remnantReapers.fnRegisterReaper(
        app, "shadowLaneLocks",
        remnantReapers.ffnWrapSweepAsReaper(appFactory._fiReapFreeShadowLaneLocks))
    [(sName, fdictReaper)] = app.state.listRemnantReapers
    assert fdictReaper({}) == {"sOutcome": "ran", "iRemoved": 0, "sReason": "", "sRemedy": ""}
