"""Port selection helpers for vaibify CLI entry points.

Allows multiple concurrent vaibify instances on one host: if the
preferred port is already bound, scan upward for the next free one
so ``vaibify`` with no flags just works. Explicit ``--port`` values
are honoured verbatim so the user's intent is not overridden.
"""

import socket
import sys


_I_DEFAULT_PREFERRED_PORT = 8050
_I_DEFAULT_MAX_ATTEMPTS = 20


def fbIsPortFree(iPort):
    """Return True if a TCP bind on 127.0.0.1:iPort would succeed."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.bind(("127.0.0.1", iPort))
    except OSError:
        return False
    finally:
        sock.close()
    return True


def fiPickFreePort(
    iPreferred=_I_DEFAULT_PREFERRED_PORT,
    iMaxAttempts=_I_DEFAULT_MAX_ATTEMPTS,
):
    """Return iPreferred if free, else the next free port in range."""
    for iOffset in range(iMaxAttempts):
        iCandidate = iPreferred + iOffset
        if fbIsPortFree(iCandidate):
            return iCandidate
    raise RuntimeError(
        f"No free TCP port found in "
        f"{iPreferred}..{iPreferred + iMaxAttempts - 1}."
    )


def fiResolvePort(iExplicitPort, iPreferred=_I_DEFAULT_PREFERRED_PORT):
    """Return the port to bind, auto-picking when none was supplied.

    When ``iExplicitPort`` is None, scan for a free port starting at
    ``iPreferred`` and announce the fallback on stderr. When the user
    passed an explicit port, return it unchanged so uvicorn surfaces
    the bind error naturally if it is taken.
    """
    if iExplicitPort is not None:
        return iExplicitPort
    iPort = fiPickFreePort(iPreferred=iPreferred)
    if iPort != iPreferred:
        print(
            f"Port {iPreferred} in use; starting on {iPort}.",
            file=sys.stderr,
        )
    return iPort
