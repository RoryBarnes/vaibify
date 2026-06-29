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

from . import actionCatalog
from . import containerOwnership

__all__ = [
    "SessionTokenMiddleware",
    "SecurityHeadersMiddleware",
    "ActivityTrackingMiddleware",
    "fbIsAllowedHostHeader",
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
    """

    async def dispatch(self, request: Request, call_next):
        dictContainerOwners = getattr(
            request.app.state, "dictContainerOwners", {},
        )
        if _fbAgentRequestAuthorized(request, dictContainerOwners):
            return await call_next(request)
        if not _fbRequestHasAllowedHost(request):
            return _fresponseJsonError(400, "Invalid Host header")
        if _fbBrowserTokenRejected(request):
            return _fresponseJsonError(401, "Unauthorized")
        return await call_next(request)


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
    """Return True when a browser request to a guarded path lacks the token."""
    sPath = request.url.path
    bNeedsToken = (
        sPath.startswith("/api/") and sPath != "/api/session-token"
    )
    if not bNeedsToken:
        return False
    sExpected = request.app.state.sSessionToken
    return _fsBrowserPresentedToken(request, sPath) != sExpected


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
    """Return True when the request Host header is a permitted loopback."""
    iExpectedPort = getattr(request.app.state, "iExpectedPort", 0)
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
            "script-src 'self' https://cdnjs.cloudflare.com "
            "https://cdn.jsdelivr.net; "
            "worker-src 'self' blob: "
            "https://cdnjs.cloudflare.com; "
            "style-src 'self' 'unsafe-inline' "
            "https://cdn.jsdelivr.net "
            "https://fonts.googleapis.com; "
            "font-src 'self' https://fonts.gstatic.com; "
            "img-src 'self' data: blob:; "
            "connect-src 'self' "
            "ws://127.0.0.1:* wss://127.0.0.1:* "
            "ws://localhost:* wss://localhost:*; "
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
