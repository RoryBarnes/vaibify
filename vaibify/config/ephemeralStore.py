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
import time


__all__ = [
    "fsGetEphemeralRoot",
    "fnSweepStaleEphemeralFiles",
]


# A secret file mounted into a container must outlive the docker run
# that consumed it: on macOS the Colima daemon lazily re-resolves
# bind-mount sources during later operations, so unlinking one
# mid-session breaks that container (see
# ``containerManager.fsStartContainerDetached``). The cutoff is
# therefore an age, chosen to exceed any plausible container session,
# rather than an unlink at the point of use.
F_STALE_EPHEMERAL_AGE_SECONDS = 7 * 24 * 60 * 60


def fsGetEphemeralRoot():
    """Return ``~/.vaibify/tmp`` (created mode 0700) for ephemeral writes."""
    sRoot = os.path.join(os.path.expanduser("~"), ".vaibify", "tmp")
    os.makedirs(sRoot, mode=0o700, exist_ok=True)
    return sRoot


def fnSweepStaleEphemeralFiles(
    fMaxAgeSeconds=F_STALE_EPHEMERAL_AGE_SECONDS,
    setProtectedPaths=None,
):
    """Delete ephemeral files older than ``fMaxAgeSeconds``.

    ``setProtectedPaths`` names absolute paths that must survive the
    sweep whatever their age. A mounted secret is bind-mounted into a
    container for that container's whole life, which outlives any
    number of hub restarts, and deleting the source leaves the
    container permanently unstartable: Docker fails the mount and
    silently creates a directory stub where the file was. Age alone is
    therefore not evidence that a file is garbage -- reachability is,
    and only the caller can enumerate what the daemon still mounts.

    Live credentials must not accumulate on disk: every mounted secret
    and every askpass helper written here holds a usable token or a
    path to one. Nothing in this directory is meant to survive the
    session that produced it, so anything older than the cutoff is
    unreachable garbage. Failures are swallowed — a sweep must never
    be the reason a container fails to start.
    """
    setProtected = set(setProtectedPaths or ())
    try:
        sRoot = fsGetEphemeralRoot()
        fCutoff = time.time() - fMaxAgeSeconds
        for sName in _flistFindStaleEphemeralFiles(sRoot, fCutoff):
            sPath = os.path.join(sRoot, sName)
            if sPath in setProtected:
                continue
            os.remove(sPath)
    except OSError:
        return


def _flistFindStaleEphemeralFiles(sRoot, fCutoff):
    """Return the names under sRoot last modified before fCutoff."""
    listStale = []
    for sName in os.listdir(sRoot):
        sPath = os.path.join(sRoot, sName)
        try:
            if os.path.isfile(sPath) and os.path.getmtime(sPath) < fCutoff:
                listStale.append(sName)
        except OSError:
            continue
    return listStale
