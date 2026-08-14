"""Cross-process exclusion for ``.vaibify/state.json`` writers.

The write primitive of the workflow-consistency spec (§4.1), named for
what it is: **optimistic validation with cooperative exclusion, not
compare-and-swap**. Re-reading the document and then renaming over it
is check-then-act — another writer can land in between — so the only
mechanism that actually EXCLUDES is a lock held from the read through
the rename, and this module is that lock.

What it covers, and what it cannot:

- **Cooperative writers** — this hub process, a second hub on the same
  machine, and the CLI — all run on the host and funnel every
  ``state.json`` write through :func:`fcontextHoldStateWriteLock`. For
  them the lock is real exclusion: the read, the section merge, and
  the atomic rename happen with no interleaved read-modify-write.
- **Uncooperative writers** — an in-container agent (or a researcher's
  editor) writing the file directly — cannot be excluded by a host
  flock. That residual TOCTOU window is STATED here rather than
  implied closed; the atomic temp-then-rename install bounds the harm
  to a lost update, never a torn document, and the ``.bak``
  checkpoint keeps the previous document recoverable.

Conflict policy is PER OPERATION, not per primitive (§4.1):

- A ``state.json`` delta (a workflow section install, a completion
  merge) re-reads the document under this lock, so a cooperative
  conflict cannot arise; the delta discipline below section level —
  entry-by-entry merges keyed by stable step id — lives with the
  operations themselves in ``stateManager``.
- A ``project.json`` remote-data provenance record is NEVER
  auto-retried on conflict: a changed record may be a semantic
  conflict, and its writer (the slice-4 commit-during-run protocol)
  must surface it rather than reorder it away.

Lock files live under ``~/.vaibify/locks`` — host-side, because every
cooperative writer runs on the host even when the document itself
lives inside a container — keyed by resource id and document path.
They are never deleted: unlinking a lock file another process holds
open silently splits the lock into two, which is the exclusion
failure this module exists to prevent. An abandoned entry is an empty
inode, not a hazard.
"""

import fcntl
import hashlib
import os
from contextlib import contextmanager


__all__ = [
    "fcontextHoldStateWriteLock",
    "fsResolveLockFilePath",
]


S_STATE_LOCK_DIRECTORY = os.path.join(
    os.path.expanduser("~"), ".vaibify", "locks",
)


def fsResolveLockFilePath(sResourceId, sDocumentPath):
    """Return the host lock-file path for one resource's document.

    Hashed rather than sanitized: a document path is arbitrary text,
    and flattening it into a filename by substitution invites two
    documents colliding on one lock name.
    """
    sDigest = hashlib.sha256(
        f"{sResourceId}:{sDocumentPath}".encode("utf-8"),
    ).hexdigest()
    return os.path.join(
        S_STATE_LOCK_DIRECTORY, f"state-{sDigest[:32]}.lock",
    )


@contextmanager
def fcontextHoldStateWriteLock(sResourceId, sDocumentPath):
    """Hold the exclusive write lock for one state document.

    Blocking, deliberately: contention is two cooperative writers of
    one repo's state file, each holding for a single read-modify-write,
    so waiting is short and correct where failing would drop a save.
    The flock is released on exit and — the recycle-proof property
    flocks give for free — on process death, so a crashed writer can
    never wedge the document.
    """
    os.makedirs(S_STATE_LOCK_DIRECTORY, exist_ok=True)
    with open(
        fsResolveLockFilePath(sResourceId, sDocumentPath), "w",
    ) as fileLock:
        fcntl.flock(fileLock, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(fileLock, fcntl.LOCK_UN)
