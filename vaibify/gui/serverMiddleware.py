"""HTTP middleware and loopback Host/Origin validation.

Houses the three request middlewares (session-token plus Host check,
security headers, activity stamp) and the loopback Host-header
predicate behind the DNS-rebinding defence. ``fnRegisterMiddleware``
installs them, plus gzip, in the order the app factory relies on.
"""

import time

from fastapi import Request
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import Response
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.routing import Match

from . import actionCatalog
from . import browserSession
from . import containerOwnership

__all__ = [
    "SessionTokenMiddleware",
    "SecurityHeadersMiddleware",
    "ActivityTrackingMiddleware",
    "fbIsAllowedHostHeader",
    "fbRequestRidesAgentLane",
    "fnRegisterMiddleware",
]


_SET_LOCAL_HOST_NAMES = frozenset({"127.0.0.1", "localhost", "[::1]"})


def fbIsAllowedHostHeader(sHostHeader, iExpectedPort):
    """Return True when sHostHeader resolves to a local loopback origin.

    Guards against DNS rebinding: an attacker-controlled domain that
    has been re-pointed at 127.0.0.1 would send its original name in
    the ``Host:`` header, so rejecting anything outside the loopback
    set prevents a remote page from driving local API endpoints.
    """
    if not sHostHeader:
        return False
    sHostPort = sHostHeader.split(",", 1)[0].strip()
    sHost, sPort = _ftSplitHostPort(sHostPort)
    if sHost not in _SET_LOCAL_HOST_NAMES:
        return False
    if sPort == "":
        return True
    try:
        iPort = int(sPort)
    except ValueError:
        return False
    return iPort == iExpectedPort


def _ftSplitHostPort(sHostPort):
    """Split host and port, tolerating bracketed IPv6 and bare hosts."""
    if sHostPort.startswith("["):
        iBracket = sHostPort.find("]")
        if iBracket == -1:
            return (sHostPort, "")
        sHost = sHostPort[: iBracket + 1]
        sRest = sHostPort[iBracket + 1:]
        sPort = sRest.lstrip(":") if sRest.startswith(":") else ""
        return (sHost, sPort)
    if ":" in sHostPort:
        sHost, sPort = sHostPort.rsplit(":", 1)
        return (sHost, sPort)
    return (sHostPort, "")


class SessionTokenMiddleware(BaseHTTPMiddleware):
    """Reject requests with unsafe Host headers or missing session tokens.

    An in-container ``vaibify-do`` agent authenticates via the
    ``X-Vaibify-Session`` header and reaches the backend through
    ``host.docker.internal``, so requests that present a valid agent
    token bypass the browser-oriented Host-header loopback check.

    Authenticating as the agent is not the same as being allowed to act:
    the agent lane is additionally filtered through the action catalog,
    so a user-only action refused by ``vaibify-do`` cannot be reached by
    calling the backend directly.
    """

    async def dispatch(self, request: Request, call_next):
        dictContainerOwners = getattr(
            request.app.state, "dictContainerOwners", {},
        )
        if _fbAgentRequestAuthorized(request, dictContainerOwners):
            if not _fbAgentLanePermitsRequest(request):
                return _fresponseJsonError(
                    403,
                    "User-only action: ask the researcher to run it "
                    "from the dashboard",
                )
            return await call_next(request)
        if not _fbRequestHasAllowedHost(request):
            return _fresponseJsonError(400, "Invalid Host header")
        if _fbBrowserTokenRejected(request):
            return _fresponseJsonError(401, "Unauthorized")
        return await call_next(request)


def fbRequestRidesAgentLane(request):
    """Return True when this request authenticated as an in-container agent.

    Exposed so a handler that must treat the agent differently from the
    researcher's browser (the host-writing file pull, for instance) can
    ask the same authority the middleware used, instead of re-deriving
    the lane from raw headers.
    """
    dictContainerOwners = getattr(
        request.app.state, "dictContainerOwners", {},
    )
    return _fbAgentRequestAuthorized(request, dictContainerOwners)


def _fbAgentLanePermitsRequest(request):
    """Return True when the catalog admits this request on the agent lane."""
    return actionCatalog.fbAgentLanePermitsRoute(
        request.method, _fsRouteTemplateForRequest(request),
    )


def _fsRouteTemplateForRequest(request):
    """Return the path template of the route that will serve this request.

    The middleware runs before routing, so the catalog lookup has only a
    concrete URL to work with while the catalog is keyed by templates
    like ``/api/files/{sContainerId}/pull``. Asking each registered
    route whether it fully matches the request scope recovers the
    template the router itself will choose. An unmatched request yields
    ``""``, which the catalog treats as an unregistered route and fails
    closed for state-mutating methods.
    """
    for route in request.app.routes:
        if not hasattr(route, "matches"):
            continue
        matchResult, _ = route.matches(request.scope)
        if matchResult == Match.FULL:
            return getattr(route, "path", "")
    return ""


def _fbAgentRequestAuthorized(request, dictContainerOwners):
    """Authorize an in-container agent by its per-container token.

    The agent presents its container's own token (the ``X-Vaibify-Session``
    header on REST) and may act only on the container named by the
    request path, so a token minted for one container never authorizes
    another. The agent reaches the backend over ``host.docker.internal``,
    so this lane intentionally precedes — and on success bypasses — the
    loopback Host check.
    """
    sPresented = _fsAgentPresentedToken(request)
    sContainerId = _fsContainerIdFromPath(request.url.path)
    return containerOwnership.fbAgentTokenAuthorizesContainerId(
        dictContainerOwners, sPresented, sContainerId,
    )


def _fsAgentPresentedToken(request):
    """Return the agent's presented token: header for REST, query for WS."""
    sHeader = request.headers.get(
        actionCatalog.S_SESSION_HEADER_NAME.lower(), "",
    )
    if sHeader:
        return sHeader
    if request.headers.get("upgrade", "").lower() == "websocket":
        return request.query_params.get("sToken", "")
    return ""


def _fsContainerIdFromPath(sPath):
    """Return the container-id path segment of an ``/api`` or ``/ws`` route."""
    listSegments = sPath.split("/")
    if len(listSegments) > 3 and listSegments[1] in ("api", "ws"):
        return listSegments[3]
    return ""


def _fbBrowserTokenRejected(request):
    """Return True when a browser request to a guarded path lacks a credential.

    Accepts only a per-browser credential minted via the capability
    bootstrap (``/api/bootstrap``); the retired shared hub session token is
    no longer honored, closing the oracle a container-side actor on loopback
    could once read. ``/api/bootstrap`` is the sole unauthenticated entry
    point and is exempt.
    """
    sPath = request.url.path
    bNeedsToken = (
        sPath.startswith("/api/")
        and sPath != "/api/bootstrap"
    )
    if not bNeedsToken:
        return False
    sPresented = _fsBrowserPresentedToken(request, sPath)
    dictBrowserSessions = getattr(
        request.app.state, "dictBrowserSessions", None,
    )
    return not (
        dictBrowserSessions is not None
        and browserSession.fbValidateCredential(
            dictBrowserSessions, sPresented,
        )
    )


def _fsBrowserPresentedToken(request, sPath):
    """Return the browser token from the header, or the query for WS/download."""
    sToken = request.headers.get("x-session-token", "")
    if sToken:
        return sToken
    bIsWebSocket = (
        request.headers.get("upgrade", "").lower() == "websocket"
    )
    bIsDownload = "/download/" in sPath
    if bIsWebSocket or bIsDownload:
        return request.query_params.get("sToken", "")
    return ""


def _fresponseJsonError(iStatusCode, sDetail):
    """Return a JSON error Response with the given status and detail."""
    return Response(
        status_code=iStatusCode,
        content='{"detail":"' + sDetail + '"}',
        media_type="application/json",
    )


def _fbRequestHasAllowedHost(request):
    """Return True when the request Host header is a permitted loopback.

    An application whose state was never initialised carries no
    ``iExpectedPort`` at all. That is a misconfiguration, not a
    deliberate opt-out, so it fails closed rather than silently
    disabling the DNS-rebinding defence for every request the app
    serves. The explicit value ``0`` remains the documented opt-out for
    the in-process test harness, whose default ``Host`` is
    ``testserver``; every production entry point passes its real bind
    port, which ``testProductionEntryPointsBindHostCheck`` enforces.
    """
    iExpectedPort = getattr(request.app.state, "iExpectedPort", None)
    if iExpectedPort is None:
        return False
    if not iExpectedPort:
        return True
    sHostHeader = request.headers.get("host", "")
    return fbIsAllowedHostHeader(sHostHeader, iExpectedPort)


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Add security headers to all HTTP responses."""

    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = (
            "strict-origin-when-cross-origin"
        )
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; "
            "script-src 'self'; "
            "worker-src 'self' blob:; "
            "style-src 'self' 'unsafe-inline' "
            "https://fonts.googleapis.com; "
            "font-src 'self' https://fonts.gstatic.com; "
            "img-src 'self' data: blob:; "
            "connect-src 'self' "
            "ws://127.0.0.1:* wss://127.0.0.1:* "
            "ws://localhost:* wss://localhost:*; "
            # base-uri 'none' stops an injected <base> tag from
            # re-homing the dashboard's root-relative API calls to an
            # attacker origin (there is no fallback to default-src for
            # base-uri or form-action). form-action 'self' likewise
            # confines any injected form. Both close the escalation a
            # hostile filename in the file browser could otherwise reach
            # even under the script-src restriction. No CDN origin is
            # granted script execution: pdf.js and xterm are vendored
            # locally, so script-src and worker-src are 'self' only.
            "base-uri 'none'; "
            "form-action 'self'; "
            "frame-ancestors 'none'"
        )
        return response


class ActivityTrackingMiddleware(BaseHTTPMiddleware):
    """Stamp ``app.state.fLastActivityMonotonic`` on every HTTP request.

    The monotonic clock is the idle watchdog's HTTP-activity signal.
    Live-browser presence is tracked separately by the WebSocket
    counter so a connected-but-quiet tab never trips the timeout.
    """

    async def dispatch(self, request: Request, call_next):
        request.app.state.fLastActivityMonotonic = time.monotonic()
        return await call_next(request)


def fnRegisterMiddleware(app):
    """Add the activity, session-token, security-header, and gzip layers."""
    app.add_middleware(ActivityTrackingMiddleware)
    app.add_middleware(SessionTokenMiddleware)
    app.add_middleware(SecurityHeadersMiddleware)
    app.add_middleware(GZipMiddleware, minimum_size=1024)
