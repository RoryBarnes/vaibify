"""Browser-session routes: spawn a new hub, and read a session's lifetime.

``POST /api/session/spawn`` picks a free port with the shared port
allocator, launches a detached child running ``python -m vaibify
--port <free>``, and returns the URL the frontend should open in a new
browser tab. No user-controlled arguments are ever appended to the
child command line. The URL carries a one-time sign-in capability that
the child mints over its host control socket, because the child is told
not to open a browser and so arms none of its own: a bare address would
reach a dashboard that answers 401 to every call. A child that never
becomes ready, or will not mint one, is stopped rather than left
running with nobody able to sign in to it.

``GET /api/session/lifetime`` reports how long the PRESENTING browser
session has before its absolute cap — the backend truth the dashboard's
pre-expiry warning renders (design §11).

``POST /api/session/renew`` restarts that cap clock for the presenting
session. It is the remedy the warning offers, and it is triggered by a
researcher, never by a poll: an automatic renewal would delete the cap
while the Settings control went on claiming one existed.

``GET /api/session/quit-preflight`` and ``POST /api/session/quit`` are
the hubs' Quit button and Admin -> Quit: what a Quit would leave
running and what refuses it, and the Quit itself (``hubQuit``), which
closes the admission gate here and in every spawned window, settles,
re-checks, and only then shuts this hub down.

Every route rejects requests carrying the in-container agent token: only
a browser-origin caller may spawn or quit a hub, and the agent holds no
browser session whose lifetime it could read.
"""

__all__ = ["fnRegisterAll", "S_SUPPRESS_BROWSER_ENV"]

import asyncio
import os
import socket
import subprocess
import sys
import time

from fastapi import HTTPException, Request
from pydantic import BaseModel

from ..routeScope import (
    S_CARRIER_LIFECYCLE_TRANSACTION,
    S_CARRIER_TYPED_READ,
    ffnDeclareCarrierMode,
)


_I_MAX_LIVE_SPAWNS = 5
_F_CHILD_EXIT_TIMEOUT_SECONDS = 10.0
_F_SHUTDOWN_SIGNAL_DELAY_SECONDS = 0.2
_S_AGENT_SESSION_HEADER_NAME = "x-vaibify-session"
_S_BROWSER_CREDENTIAL_HEADER_NAME = "x-session-token"
_F_READY_TIMEOUT_SECONDS = 30.0
_F_READY_POLL_INTERVAL_SECONDS = 0.05
S_SUPPRESS_BROWSER_ENV = "VAIBIFY_SUPPRESS_BROWSER"


def _fprocessLaunchDetachedHub(iPort):
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


def _fnRejectContainerAgentCallers(request, sDetail=""):
    """Deny requests that authenticate via the in-container agent token."""
    sAgentToken = request.headers.get(_S_AGENT_SESSION_HEADER_NAME, "")
    if sAgentToken:
        raise HTTPException(
            status_code=403,
            detail=sDetail or (
                "Spawning new vaibify windows is not permitted "
                "from inside the container."
            ),
        )


def _fbIsPortAcceptingConnections(iPort):
    """Return True if 127.0.0.1:iPort accepts a TCP connection now."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(0.2)
    try:
        return sock.connect_ex(("127.0.0.1", iPort)) == 0
    finally:
        sock.close()


async def _fbAwaitChildReady(iPort, fTimeoutSeconds, processChild=None):
    """Poll until the child answers on its port AND its control socket.

    Both, because the listener appears before the application is
    assembled and the sign-in capability comes from the control socket.
    A child that has already exited is not waited for.
    """
    from vaibify.gui.hostControlChannel import fsControlSocketPathForPort
    fDeadline = time.monotonic() + fTimeoutSeconds
    while time.monotonic() < fDeadline:
        if processChild is not None and processChild.poll() is not None:
            return False
        if _fbIsPortAcceptingConnections(iPort) and os.path.exists(
            fsControlSocketPathForPort(iPort),
        ):
            return True
        await asyncio.sleep(_F_READY_POLL_INTERVAL_SECONDS)
    return False


def _fnReapSpawnedChild(listChildren, processChild):
    """Stop a spawned hub nobody can sign in to, and forget it."""
    if processChild in listChildren:
        listChildren.remove(processChild)
    try:
        processChild.terminate()
    except OSError:
        pass


async def _fsSignInUrlForSpawnedChild(listChildren, processChild, iPort):
    """Return the URL that signs a browser in to the spawned hub.

    The capability is one-time and rides the URL fragment, which never
    reaches an access log. A child that cannot supply one is stopped
    and the request refused, so a failed click leaves no hub behind.
    """
    from vaibify.cli.hubSession import (
        HubSessionError, fsRequestBootstrapCapability,
    )
    sCapability = ""
    if await _fbAwaitChildReady(
        iPort, _F_READY_TIMEOUT_SECONDS, processChild,
    ):
        try:
            sCapability = await asyncio.to_thread(
                fsRequestBootstrapCapability, iPort)
        except HubSessionError:
            sCapability = ""
    if not sCapability:
        _fnReapSpawnedChild(listChildren, processChild)
        raise HTTPException(
            status_code=502,
            detail="The new vaibify window did not start. Run "
            "'vaibify' in a terminal to see why.",
        )
    return f"http://127.0.0.1:{iPort}/#bootstrap={sCapability}"


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
        child = _fprocessLaunchDetachedHub(iPort)
        # Kept beside the handle, so a Quit can find the child's control
        # socket (hubQuit.flistTrustedChildPorts).
        child.iHubPort = iPort
        listChildren.append(child)
        return {
            "sUrl": await _fsSignInUrlForSpawnedChild(
                listChildren, child, iPort),
            "iPort": iPort,
        }


def _fnRegisterLifetime(app):
    """Register GET /api/session/lifetime on the given app.

    The backend truth behind the pre-expiry dashboard warning (design
    §11). It reports only the clocks of the session whose credential
    the request presents — resolved from the header, never from a
    caller-supplied session id — so no browser can read another's
    remaining lifetime. The in-container agent holds no browser
    session at all and is refused outright.
    """

    @app.get("/api/session/lifetime")
    async def fdictGetSessionLifetime(request: Request):
        from ..sessionLifecycle import fdictSessionExpiryView
        _fnRejectContainerAgentCallers(
            request,
            sDetail="The in-container agent holds no browser session, "
            "so it has no session lifetime to read.",
        )
        return fdictSessionExpiryView(
            request.app.state,
            request.headers.get(_S_BROWSER_CREDENTIAL_HEADER_NAME, ""),
        )


def _fnRegisterRenewal(app):
    """Register POST /api/session/renew on the given app.

    The action behind the expiry warning's "Renew" (design §11). It
    restarts the cap clock for the session whose credential the request
    presents, resolved from the header exactly as the read is, so no
    browser can renew another's session and no caller-supplied id is
    honoured.

    It is deliberately a POST the RESEARCHER triggers. The dashboard
    must never call it from a poll or on a timer: the cap exists to
    bound a tab nobody is watching, and an automatic renewal would
    quietly delete the cap while leaving the setting on screen claiming
    one exists. The in-container agent is refused for the same reason
    it is refused the read -- it holds no browser session, and an agent
    that could extend the researcher's credential could outlive the
    researcher's attention.
    """

    @app.post("/api/session/renew")
    async def fdictRenewSessionLifetime(request: Request):
        from ..sessionLifecycle import fdictRenewSessionExpiry
        _fnRejectContainerAgentCallers(
            request,
            sDetail="The in-container agent holds no browser session, "
            "so it has no session lifetime to renew.",
        )
        return fdictRenewSessionExpiry(
            request.app.state,
            request.headers.get(_S_BROWSER_CREDENTIAL_HEADER_NAME, ""),
        )


def _fnAwaitChildExit(processChild):
    """Wait, bounded, for a terminated child to exit; kill one that will not."""
    try:
        processChild.wait(timeout=_F_CHILD_EXIT_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired:
        try:
            processChild.kill()
            processChild.wait(timeout=_F_CHILD_EXIT_TIMEOUT_SECONDS)
        except (OSError, subprocess.TimeoutExpired):
            pass
    except OSError:
        pass


def _fnRegisterSpawnedChildShutdown(app):
    """Terminate every spawned hub child when this process shuts down.

    Without this hook a hub that exits (idle self-shutdown, terminal
    close, Quit) leaves its spawned children running; they are otherwise
    only pruned on the next spawn and never actively terminated.

    The children are waited for, and only then is a Quit's lease
    renewal stopped: a child's admission gate must stay closed until
    the child has exited, so its lease cannot lapse in between.
    """
    if not hasattr(app.state, "listSpawnedChildren"):
        app.state.listSpawnedChildren = []
    if not hasattr(app.state, "listLifespanShutdown"):
        app.state.listLifespanShutdown = []

    async def fnTerminateSpawnedChildren(app):
        from .. import hubQuit
        listChildren = list(app.state.listSpawnedChildren)
        for processChild in listChildren:
            try:
                processChild.terminate()
            except OSError:
                pass
        for processChild in listChildren:
            await asyncio.to_thread(_fnAwaitChildExit, processChild)
        app.state.listSpawnedChildren.clear()
        hubQuit.fnStopLeaseRenewal(app)

    app.state.listLifespanShutdown.append(fnTerminateSpawnedChildren)


def _fnRejectBrowserHubCaller(request, sAction):
    """Refuse the in-container agent, by either of its two signals."""
    from ..routeContext import fnRejectAgentTokenLane
    _fnRejectContainerAgentCallers(
        request,
        sDetail=f"{sAction} is the researcher's, never the in-container "
        "agent's.",
    )
    fnRejectAgentTokenLane(request)


def _fnRegisterQuitPreflight(app, dictCtx):
    """Register GET /api/session/quit-preflight on the given app.

    What the Quit dialog shows before anything is closed: the
    environments left running, the child windows that will close, the
    open terminals, and every reason a Quit would be refused, by window.
    """

    @app.get("/api/session/quit-preflight")
    @ffnDeclareCarrierMode(S_CARRIER_TYPED_READ)
    async def fdictReadQuitPreflight(request: Request):
        from .. import hubQuit
        _fnRejectBrowserHubCaller(request, "Quitting vaibify")
        return await hubQuit.fdictBuildQuitPreflight(app, dictCtx)


class QuitRequest(BaseModel):
    bStopEnvironments: bool = False


async def _fnScheduleHubShutdown(app):
    """Mark the Quit committed, then SIGTERM this hub once the reply is out.

    Runs as the response's background task, so the 202 has flushed; the
    short delay lets the socket close cleanly before uvicorn's graceful
    shutdown runs every hook.
    """
    from .. import serverLifespan
    app.state.bQuitTerminationScheduled = True
    await asyncio.sleep(_F_SHUTDOWN_SIGNAL_DELAY_SECONDS)
    serverLifespan.fnSignalHubToShutDown()


def _fnRegisterQuit(app, dictCtx):
    """Register POST /api/session/quit on the given app (D6-D8, D12).

    The whole transaction is ``hubQuit.fdictRunQuit``; this answers 202
    when it succeeds and schedules the shutdown, and 409 with every
    reason and every environment already stopped when it does not.
    """

    @app.post("/api/session/quit")
    @ffnDeclareCarrierMode(S_CARRIER_LIFECYCLE_TRANSACTION)
    async def fresponseQuitVaibify(
        request: Request, requestQuit: QuitRequest,
    ):
        from fastapi.responses import JSONResponse
        from starlette.background import BackgroundTask
        from .. import hubQuit
        _fnRejectBrowserHubCaller(request, "Quitting vaibify")
        try:
            dictResult = await hubQuit.fdictRunQuit(
                app, dictCtx, requestQuit.bStopEnvironments,
            )
        except hubQuit.QuitRefusedError as error:
            raise HTTPException(status_code=409, detail=error.dictDetail)
        return JSONResponse(
            status_code=202, content=dictResult,
            background=BackgroundTask(_fnScheduleHubShutdown, app),
        )


def _fnRegisterShuttingDownRefusal(app):
    """Answer work refused by a closed Quit gate as 503, in its own words."""
    from fastapi.responses import JSONResponse
    from vaibify.config.mutationAdmission import AdmissionsClosedError
    from ..routeScope import S_REFUSAL_SHUTTING_DOWN

    async def fresponseShuttingDown(request, errorRefused):
        del request
        return JSONResponse(status_code=503, content={"detail": {
            "sMessage": str(errorRefused),
            "sRefusal": S_REFUSAL_SHUTTING_DOWN,
        }})

    app.add_exception_handler(AdmissionsClosedError, fresponseShuttingDown)


def fnRegisterAll(app, dictCtx):
    """Register all session routes."""
    _fnRegisterSpawn(app)
    _fnRegisterLifetime(app)
    _fnRegisterRenewal(app)
    _fnRegisterQuitPreflight(app, dictCtx)
    _fnRegisterQuit(app, dictCtx)
    _fnRegisterShuttingDownRefusal(app)
    _fnRegisterSpawnedChildShutdown(app)
