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


__all__ = [
    "fsGetEphemeralRoot",
]


def fsGetEphemeralRoot():
    """Return ``~/.vaibify/tmp`` (created mode 0700) for ephemeral writes."""
    sRoot = os.path.join(os.path.expanduser("~"), ".vaibify", "tmp")
    os.makedirs(sRoot, mode=0o700, exist_ok=True)
    return sRoot
