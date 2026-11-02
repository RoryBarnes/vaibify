"""A host JSON record whose read-modify-write cannot lose a concurrent update.

Two host stores keep one JSON file per project and change it by reading
the whole file, changing one field and replacing the file: the Zenodo
token migration journal (:mod:`vaibify.config.zenodoMigrationJournal`)
and the remote binding record (:mod:`vaibify.config.remoteBindings`).
Two writers that read the same version both replace it, and the second
replacement drops the first one's change: with two writers released
together on distinct keys, one update was lost in each of 200 rounds,
in both stores (measured 2026-11-02). Each read, mutation and write
therefore runs behind an exclusive lock on a sibling file, as the
registry's own read-modify-write already did.

The lock is ``fcntl.flock``, not ``lockf``. A POSIX record lock belongs
to the PROCESS, so it would not exclude two threads of the one hub; a
``flock`` lock belongs to the open file description, so two separate
``open()`` calls exclude each other, even twice in the same thread.

The rules for every lock taken through this module:

1. **Each lock is taken exactly once per operation.** Nothing that runs
   while a lock is held calls anything that takes the same lock: a
   second blocking ``flock`` on the same file, from the thread already
   holding it, waits forever.
2. **Lock order.** A project's migration pass lock
   (:func:`vaibify.config.zenodoMigrationJournal.fcontextHoldPassLock`)
   is outer, a record lock is inner. Never take a pass lock while
   holding a record lock.
3. **Only in a worker thread.** A blocking lock is taken in
   ``asyncio.to_thread`` or the drain's worker, never on the event
   loop's thread.

A lock file outlives the record it guards. Deleting it while another
writer waits would hand the next opener a fresh file, and a lock on the
old one would then exclude nothing.
"""

__all__ = ["fcontextHoldRecordLock", "fnMutateRecordLocked"]

import contextlib
import fcntl
import os

_I_DIRECTORY_MODE = 0o700
_I_LOCK_FILE_MODE = 0o600


@contextlib.contextmanager
def fcontextHoldRecordLock(sLockPath):
    """Hold an exclusive, blocking ``flock`` on ``sLockPath`` for the block.

    The lock file is created mode 600 in a mode-700 directory, and a
    symbolic link in its place is refused rather than followed.
    """
    sDirectory = os.path.dirname(sLockPath)
    os.makedirs(sDirectory, mode=_I_DIRECTORY_MODE, exist_ok=True)
    os.chmod(sDirectory, _I_DIRECTORY_MODE)
    iDescriptor = os.open(
        sLockPath, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, _I_LOCK_FILE_MODE,
    )
    try:
        fcntl.flock(iDescriptor, fcntl.LOCK_EX)
        yield
    finally:
        os.close(iDescriptor)


def fnMutateRecordLocked(sRecordPath, fdictReadRecord, fnMutate, fnWriteRecord):
    """Read, change and write one record while holding its lock.

    ``fdictReadRecord()`` returns the record as it is now;
    ``fnMutate(dictRecord)`` changes it in place and may raise to
    abandon the write; ``fnWriteRecord(dictRecord)`` replaces the file.
    The lock is ``sRecordPath`` plus ``.lock``.
    """
    with fcontextHoldRecordLock(sRecordPath + ".lock"):
        dictRecord = fdictReadRecord()
        fnMutate(dictRecord)
        fnWriteRecord(dictRecord)
