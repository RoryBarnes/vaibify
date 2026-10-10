"""Shared ephemeral-file location for secret-bearing temp writes.

Every site that needs to drop a credential into a short-lived host
file (Docker secret mount, ``GIT_ASKPASS`` script, Overleaf push
token) routes through :func:`fsGetEphemeralRoot`. The directory is
under the calling user's home — at ``~/.vaibify/tmp/`` — and is mode
0700 so cross-user filename enumeration is impossible. The same
directory works on macOS (Colima only shares $HOME into its VM by
default, so /tmp is invisible to the container daemon) and on Linux
(where /tmp is world-traversable).

Audit finding M2.
"""

import os
import stat
import time


__all__ = [
    "F_SECRET_FILE_GRACE_SECONDS",
    "S_SECRET_FILE_PREFIX",
    "fiReleaseSecretSources",
    "fiSweepUnmountedEphemeralFiles",
    "fsGetEphemeralRoot",
]


# A secret file mounted into a container must outlive the docker run
# that consumed it: on macOS the Colima daemon lazily re-resolves
# bind-mount sources during later operations, so unlinking one
# mid-session breaks that container (see
# ``containerManager.fsStartContainerDetached``). What makes a file
# garbage is therefore that NO container mounts it, which only the
# daemon can say; the grace below only keeps a file that was written
# a moment ago, for a ``docker run`` still being composed, out of the
# sweep's reach.
F_SECRET_FILE_GRACE_SECONDS = 60 * 60

# The prefix ``secretManager._fsWriteEphemeralFile`` gives a mounted
# secret. A removal releases only files carrying it; the periodic
# sweep retires everything in the root, because askpass helpers and
# Overleaf token files hold a credential or a path to one as well.
S_SECRET_FILE_PREFIX = "vc_secret_"


def fsGetEphemeralRoot():
    """Return ``~/.vaibify/tmp`` (created mode 0700) for ephemeral writes."""
    sRoot = os.path.join(os.path.expanduser("~"), ".vaibify", "tmp")
    os.makedirs(sRoot, mode=0o700, exist_ok=True)
    return sRoot


def fiSweepUnmountedEphemeralFiles(
    setMountedSources, tExcludedPrefixes=(),
    fGraceSeconds=F_SECRET_FILE_GRACE_SECONDS,
):
    """Delete the root's files no container mounts; return how many.

    ``setMountedSources`` is every host path the daemon reports any
    container, running or stopped, as mounting. The caller must have
    ENUMERATED it: an empty protected set is the destructive direction,
    so a caller that could not ask the daemon must not call this at
    all. A regular file older than the grace that is not mounted and
    does not carry an excluded prefix (the council's staged copies have
    their own lock-aware sweep) is removed. Symlinks, directories and
    files younger than the grace are left alone. A root that cannot be
    listed raises, so the reaper records the failure.
    """
    sRoot = fsGetEphemeralRoot()
    fCutoff = time.time() - fGraceSeconds
    iRemoved = 0
    for sName in os.listdir(sRoot):
        sPath = os.path.join(sRoot, sName)
        if sName.startswith(tuple(tExcludedPrefixes)):
            continue
        if sPath in setMountedSources:
            continue
        if _fbIsRegularFileOlderThan(sPath, fCutoff):
            iRemoved += _fiUnlinkQuietly(sPath)
    return iRemoved


def _fbIsRegularFileOlderThan(sPath, fCutoff):
    """Return True for a regular, non-symlink file modified before fCutoff."""
    try:
        tStat = os.lstat(sPath)
    except OSError:
        return False
    return stat.S_ISREG(tStat.st_mode) and tStat.st_mtime < fCutoff


def _fiUnlinkQuietly(sPath):
    """Unlink one file; return 1 when it went, 0 when it could not."""
    try:
        os.unlink(sPath)
    except OSError:
        return 0
    return 1


def fiReleaseSecretSources(listMountSources, setStillMountedSources):
    """Delete a removed container's secret files; return how many.

    ``listMountSources`` is what the container mounted, read before it
    was removed; ``setStillMountedSources`` is what every surviving
    container mounts, read after. A source is released only when it is
    a secret file inside the ephemeral root and no surviving container
    mounts it, so a file shared by two containers outlives the first.
    """
    sRoot = fsGetEphemeralRoot()
    iRemoved = 0
    for sSource in listMountSources:
        if os.path.dirname(sSource) != sRoot:
            continue
        if not os.path.basename(sSource).startswith(S_SECRET_FILE_PREFIX):
            continue
        if sSource in setStillMountedSources:
            continue
        if _fbIsRegularFileOlderThan(sSource, float("inf")):
            iRemoved += _fiUnlinkQuietly(sSource)
    return iRemoved
