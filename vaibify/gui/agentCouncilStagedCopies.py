"""Council token copies on disk: which are held, and which are orphans.

A council turn and a credential test each stage a copy of the project's
access token as a mode-600 file, deliver it into a runner, and delete it
— a life of milliseconds. A hub that dies inside that window leaves the
copy behind. An age threshold cannot tell that orphan from a copy a
live peer hub is using at this moment, and a record of staged paths
would be a record naming where credential copies sit.

So ownership is PROVEN by a lock instead: the process that stages a
copy takes an exclusive ``fcntl.flock`` on it straight away and holds
it for the rest of the copy's life. The kernel drops the lock the
moment that process dies, so a copy nobody holds is an orphan at ANY
age, and a copy a live peer holds is left alone. The only age that
matters is :data:`F_LOCK_ACQUIRE_GRACE_SECONDS`, which covers the
microseconds between a file's creation and its lock.
"""

import fcntl
import os
import threading
import time

__all__ = [
    "TUPLE_STAGED_CREDENTIAL_NAMES",
    "F_LOCK_ACQUIRE_GRACE_SECONDS",
    "fnHoldStagedCopy",
    "fiReleaseVanishedHolds",
    "fiSweepOrphanedStagedCopies",
]

# Every council token copy is staged under one of these names (the
# provider adapters' ``fsMaterializeSecretValue`` names, spelled again in
# agentCouncilProviders, agentCouncilCodexProvider and
# agentCouncilAntigravityProvider).
TUPLE_STAGED_CREDENTIAL_NAMES = (
    "claudeCouncilAccessToken", "codexCouncilAccessToken",
    "antigravityCouncilAccessToken")
F_LOCK_ACQUIRE_GRACE_SECONDS = 2.0

_dictHeldCopies = {}
_lockHeldCopies = threading.Lock()


def fnHoldStagedCopy(sPath):
    """Take and keep the lock that marks a staged copy as alive.

    Holds already released by deletion are closed first, so the number
    of open handles is bounded by the copies alive at once.
    """
    fiReleaseVanishedHolds()
    fileHold = open(sPath, "rb")
    try:
        fcntl.flock(fileHold, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        fileHold.close()
        raise
    with _lockHeldCopies:
        _dictHeldCopies[sPath] = fileHold


def fiReleaseVanishedHolds():
    """Close the holds on copies that have been deleted; return how many."""
    with _lockHeldCopies:
        listGone = [sPath for sPath in _dictHeldCopies
                    if not os.path.exists(sPath)]
        for sPath in listGone:
            _dictHeldCopies.pop(sPath).close()
    return len(listGone)


def _flistCouncilCopies(sRoot):
    """Return the council token copies under the staging root.

    The ``vc_secret_<name>_`` prefix is the one
    ``secretManager._fsWriteEphemeralFile`` writes.
    """
    return [os.path.join(sRoot, sName) for sName in sorted(os.listdir(sRoot))
            if any(sName.startswith(f"vc_secret_{sPrefix}_")
                   for sPrefix in TUPLE_STAGED_CREDENTIAL_NAMES)]


def _fbDeleteIfUnheld(sPath):
    """Delete a copy only while holding its lock; False if someone holds it."""
    try:
        fileProbe = open(sPath, "rb")
    except FileNotFoundError:
        return False
    try:
        fcntl.flock(fileProbe, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        fileProbe.close()
        return False
    try:
        os.remove(sPath)
    finally:
        fileProbe.close()
    return True


def fiSweepOrphanedStagedCopies():
    """Delete every council token copy no live process holds; return count.

    Failures are swallowed: a sweep must never be the reason a hub
    fails to start or a reaper tick dies.
    """
    from ..config import secretManager
    fiReleaseVanishedHolds()
    iRemoved = 0
    try:
        sRoot = secretManager._fsGetTempDirectory()
        fCutoff = time.time() - F_LOCK_ACQUIRE_GRACE_SECONDS
        for sPath in _flistCouncilCopies(sRoot):
            try:
                if os.path.getmtime(sPath) < fCutoff and (
                        _fbDeleteIfUnheld(sPath)):
                    iRemoved += 1
            except OSError:
                continue
    except OSError:
        return iRemoved
    return iRemoved
