"""Session management route: spawn a new vaibify hub process.

Exposes ``POST /api/session/spawn``. Picks a free port with the
shared port allocator, launches a detached child running
``python -m vaibify --port <free>``, and returns the URL the
frontend should open in a new browser tab. No user-controlled
arguments are ever appended to the child command line, and the
route rejects requests carrying the in-container agent token so
only browser-origin callers can spawn new hubs.
"""

__all__ = ["fnRegisterAll", "S_SUPPRESS_BROWSER_ENV"]

import asyncio
import os
import socket
import subprocess
import sys
import time

from fastapi import HTTPException, Request


_I_MAX_LIVE_SPAWNS = 5
_S_AGENT_SESSION_HEADER_NAME = "x-vaibify-session"
_F_READY_TIMEOUT_SECONDS = 5.0
_F_READY_POLL_INTERVAL_SECONDS = 0.05
S_SUPPRESS_BROWSER_ENV = "VAIBIFY_SUPPRESS_BROWSER"


def _fnLaunchDetachedHub(iPort):
    """Spawn a detached vaibify hub child on the given port.

    Sets ``VAIBIFY_SUPPRESS_BROWSER=1`` in the child's environment so
    the spawned hub does not open its own browser tab — the spawning
    frontend already calls ``window.open`` on the returned URL, and
    without this suppression the user sees two tabs.
    """
    dictChildEnv = {**os.environ, S_SUPPRESS_BROWSER_ENV: "1"}
    return subprocess.Popen(
        [sys.executable, "-m", "vaibify", "--port", str(iPort)],
        start_new_session=True,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        env=dictChildEnv,
    )


def _fnPruneDeadChildren(listChildren):
    """Drop child Popen entries whose process has already exited."""
    listChildren[:] = [
        child for child in listChildren if child.poll() is None
    ]


def _fnRejectContainerAgentCallers(request):
    """Deny requests that authenticate via the in-container agent token."""
    sAgentToken = request.headers.get(_S_AGENT_SESSION_HEADER_NAME, "")
    if sAgentToken:
        raise HTTPException(
            status_code=403,
            detail="Spawning new vaibify windows is not permitted "
            "from inside the container.",
        )


def _fbIsPortAcceptingConnections(iPort):
    """Return True if 127.0.0.1:iPort accepts a TCP connection now."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(0.2)
    try:
        return sock.connect_ex(("127.0.0.1", iPort)) == 0
    finally:
        sock.close()


async def _fnAwaitChildReady(iPort, fTimeoutSeconds):
    """Poll until the child's port accepts connections or timeout elapses.

    Returning early avoids the browser hitting a transient "unable to
    connect" page while the spawned hub binds its socket.
    """
    fDeadline = time.monotonic() + fTimeoutSeconds
    while time.monotonic() < fDeadline:
        if _fbIsPortAcceptingConnections(iPort):
            return True
        await asyncio.sleep(_F_READY_POLL_INTERVAL_SECONDS)
    return False


def _fnRegisterSpawn(app):
    """Register POST /api/session/spawn on the given app."""
    if not hasattr(app.state, "listSpawnedChildren"):
        app.state.listSpawnedChildren = []

    @app.post("/api/session/spawn")
    async def fdictSpawnSession(request: Request):
        from vaibify.cli.portAllocator import fiPickFreePort
        _fnRejectContainerAgentCallers(request)
        listChildren = app.state.listSpawnedChildren
        _fnPruneDeadChildren(listChildren)
        if len(listChildren) >= _I_MAX_LIVE_SPAWNS:
            raise HTTPException(
                status_code=429,
                detail=f"Too many active spawned sessions "
                f"(limit {_I_MAX_LIVE_SPAWNS}).",
            )
        iPort = fiPickFreePort(iPreferred=8050)
        child = _fnLaunchDetachedHub(iPort)
        listChildren.append(child)
        await _fnAwaitChildReady(iPort, _F_READY_TIMEOUT_SECONDS)
        return {
            "sUrl": f"http://127.0.0.1:{iPort}",
            "iPort": iPort,
        }


def fnRegisterAll(app, dictCtx):
    """Register all session routes."""
    del dictCtx
    _fnRegisterSpawn(app)
