"""Replace a credential slot only after the candidate token has passed its check.

The store this replaces wrote the new token into the live slot, asked
the service about it, and restored a snapshot when the answer was no.
Review found two defects in that order (2026-11-24). A validation that
RAISED -- a malformed Overleaf project ID -- skipped the restore and
left an unvalidated token installed, with the old one gone. And the one
Overleaf slot every project on the computer shares was guarded only by
each container's own lock, so project A's failed attempt could restore
the old token over project B's success after B had reported it.

The order here has no restore at all:

1. the caller fingerprints the slot as it is now (:func:`fsFingerprintSlot`);
2. the caller checks the CANDIDATE with the service, which never touches
   the slot;
3. :func:`fnCommitValidatedCandidate` takes the slot's lock, refuses with
   :class:`CredentialSlotChanged` when the slot no longer carries the
   fingerprint from step 1, and otherwise writes and reads back.

An exception anywhere before step 3 leaves the live slot exactly as it
was, because nothing has written it.

The lock is a host file lock per slot under ``~/.vaibify/credentialLocks``,
so it excludes other hubs and the CLI as well as other threads. Every
writer of a credential slot takes it, never while holding another
slot's lock, and holds it only for keyring work: the service is asked
outside it. The fingerprint is a SHA-256 of the value, held instead of
the token so that no traceback or debugger frame shows a secret.

The three-state read and the verified write are
:mod:`vaibify.config.zenodoTokenSlots`'s, which are slot-generic: a read
that cannot reach the keyring raises rather than answering "absent", and
a write counts only when an equal value reads back.
"""

import contextlib
import fcntl
import hashlib
import os
import time

from vaibify.config import zenodoTokenSlots


__all__ = [
    "CredentialSlotBusy",
    "CredentialSlotChanged",
    "fsSlotLockPath",
    "fcontextHoldSlotLock",
    "fsFingerprintSlot",
    "fbSlotHoldsValue",
    "fnCommitValidatedCandidate",
    "fnDeleteSlotUnderLock",
]


_S_CREDENTIAL_LOCK_DIRECTORY = os.path.join(
    os.path.expanduser("~"), ".vaibify", "credentialLocks",
)
_F_LOCK_WAIT_SECONDS = 30.0
_F_LOCK_POLL_SECONDS = 0.05


class CredentialSlotBusy(RuntimeError):
    """Another writer held the slot's lock for the whole wait."""


class CredentialSlotChanged(RuntimeError):
    """The slot changed between the fingerprint and the commit."""


def fsSlotLockPath(sSlot):
    """Return the host lock file for one slot.

    Hashed rather than sanitized: a slot name carries ``:`` and ``/``
    (``github_token:<owner>/<repo>``), and flattening by substitution
    invites two slots colliding on one lock.
    """
    sDigest = hashlib.sha256(sSlot.encode("utf-8")).hexdigest()
    return os.path.join(
        _S_CREDENTIAL_LOCK_DIRECTORY, f"slot-{sDigest[:32]}.lock",
    )


def _fnAcquireBeforeDeadline(iDescriptor, fWaitSeconds):
    """Take the exclusive lock, or raise :class:`CredentialSlotBusy`."""
    fDeadline = time.monotonic() + fWaitSeconds
    while True:
        try:
            fcntl.flock(iDescriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return
        except BlockingIOError:
            if time.monotonic() >= fDeadline:
                raise CredentialSlotBusy(
                    "Another vaibify window or command was changing this "
                    f"credential for {fWaitSeconds:.0f} seconds; nothing "
                    "was changed. Try again."
                ) from None
            time.sleep(_F_LOCK_POLL_SECONDS)


@contextlib.contextmanager
def fcontextHoldSlotLock(sSlot, fWaitSeconds=None):
    """Hold the slot's host-wide lock for the life of the context."""
    sLockPath = fsSlotLockPath(sSlot)
    os.makedirs(os.path.dirname(sLockPath), mode=0o700, exist_ok=True)
    iDescriptor = os.open(sLockPath, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        _fnAcquireBeforeDeadline(
            iDescriptor,
            _F_LOCK_WAIT_SECONDS if fWaitSeconds is None else fWaitSeconds,
        )
        try:
            yield
        finally:
            fcntl.flock(iDescriptor, fcntl.LOCK_UN)
    finally:
        os.close(iDescriptor)


def fsFingerprintSlot(sSlot):
    """Return a digest of the slot's value, '' when it is absent.

    Raises :class:`zenodoTokenSlots.TokenStoreUnreadable` when the
    keyring cannot be read: an unreadable slot has no fingerprint, and
    treating it as absent would let a commit overwrite a token nobody
    could see.
    """
    sValue = zenodoTokenSlots.fsReadSlot(sSlot)
    if not sValue:
        return ""
    return hashlib.sha256(sValue.encode("utf-8")).hexdigest()


def fbSlotHoldsValue(sSlot):
    """Return True when the slot holds a value; raise when unreadable."""
    return bool(zenodoTokenSlots.fsReadSlot(sSlot))


def fnCommitValidatedCandidate(sSlot, sCandidate, sFingerprintBefore):
    """Write an already-validated candidate unless the slot moved meanwhile."""
    with fcontextHoldSlotLock(sSlot):
        if fsFingerprintSlot(sSlot) != sFingerprintBefore:
            raise CredentialSlotChanged(
                "This credential was changed by another project or "
                "window while yours was being checked, so yours was not "
                "stored. Check it again to replace the newer one."
            )
        zenodoTokenSlots.fnStoreSlotVerified(sSlot, sCandidate)


def fnDeleteSlotUnderLock(sSlot):
    """Remove the slot while holding its lock; an absent slot is no error."""
    with fcontextHoldSlotLock(sSlot):
        zenodoTokenSlots.fnDeleteSlot(sSlot)
