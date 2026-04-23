"""Session management route: spawn a new vaibify hub process.

Exposes ``POST /api/session/spawn``. Picks a free port with the
shared port allocator, launches a detached child running
``python -m vaibify --port <free>``, and returns the URL the
frontend should open in a new browser tab. No user-controlled
arguments are ever appended to the child command line, and the
route rejects requests carrying the in-container agent token so
only browser-origin callers can spawn new hubs.
"""

__all__ = ["fnRegisterAll"]

import subprocess
import sys

from fastapi import HTTPException, Request


_I_MAX_LIVE_SPAWNS = 5
_S_AGENT_SESSION_HEADER_NAME = "x-vaibify-session"


def _fnLaunchDetachedHub(iPort):
    """Spawn a detached vaibify hub child on the given port."""
    return subprocess.Popen(
        [sys.executable, "-m", "vaibify", "--port", str(iPort)],
        start_new_session=True,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
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
        return {
            "sUrl": f"http://127.0.0.1:{iPort}",
            "iPort": iPort,
        }


def fnRegisterAll(app, dictCtx):
    """Register all session routes."""
    del dictCtx
    _fnRegisterSpawn(app)
