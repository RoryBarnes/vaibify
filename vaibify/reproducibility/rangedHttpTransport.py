"""One ranged HTTP GET, cancellable at every phase, to an allowlisted host.

``requests`` is the right tool for a record page and the wrong one for
a gigabyte that must be resumable and cancellable. Its ``iter_content``
decodes any ``Content-Encoding`` the server chose, so a byte offset
would stop meaning bytes of the file; its timeouts bound each socket
wait and nothing else, so a DNS lookup cannot be interrupted at all;
and the socket a blocked read is waiting on is four private attributes
deep. This module owns the socket instead.

What it does, and only this:

* checks the ORIGIN of the URL handed in, and of every ``Location`` a
  redirect names, against the allowlist BEFORE a connection is opened
  for it -- the same discipline ``zenodoClient.fresponseGetWithinAllowlist``
  follows for the record page;
* resolves and connects on a WORKER from a bounded pool, so the caller
  can stop waiting at a deadline or on Cancel while the operating
  system's lookup finishes on its own time. The pool holds at most
  ``_I_MAX_CONNECT_WORKERS`` threads, abandoned ones included; an
  attempt that finds every worker busy waits, cancellably, and says so;
* carries an ATTEMPT TOKEN: a worker's socket is kept only if the
  coordinator still wants that attempt, and a late one is closed on
  arrival. Workers write no file and touch no progress counter;
* sends ``Range: bytes=<start>-`` and ``Accept-Encoding: identity`` and
  reads the body RAW in small blocks, so the caller can check Cancel
  between blocks;
* lets Cancel shut the live socket down from another thread
  (``shutdown(SHUT_RDWR)``), which wakes a read blocked on a stalled
  connection on macOS and Linux.

Proxies named in the environment (``HTTPS_PROXY`` and friends, read by
``urllib.request.getproxies``) are honored with a CONNECT tunnel; no
credential store is ever consulted. TLS trusts the bundle ``requests``
trusts (``certifi``), so this path and the record fetch agree about
which certificates are good.
"""

__all__ = [
    "CancelledDuringTransport",
    "S_FAILURE_COULD_NOT_CONNECT",
    "S_FAILURE_REFUSED_HOST",
    "S_FAILURE_TLS",
    "TransportFailure",
    "fdictOpenRangedGet",
    "fiCountLiveConnectWorkers",
    "fnCloseAnswer",
    "fnShutdownLiveSocket",
    "fsBuildRangeHeader",
]

import base64
import http.client
import socket
import ssl
import threading
import time
from urllib.parse import urljoin, urlsplit
from urllib.request import getproxies, proxy_bypass

import certifi


S_FAILURE_REFUSED_HOST = "refused-host"
S_FAILURE_TLS = "tls"
S_FAILURE_COULD_NOT_CONNECT = "could-not-connect"

_I_MAX_REDIRECT_HOPS = 5
_I_MAX_CONNECT_WORKERS = 4
_F_CONNECT_TIMEOUT_SECONDS = 10.0
_F_WORKER_POLL_SECONDS = 0.05
_S_USER_AGENT = "vaibify"

# The pool's accounting: how many connect workers are alive, abandoned
# ones included. A worker blocked in the operating system's resolver
# cannot be interrupted, so the only way to bound threads under
# unlimited Try-again is to count them and make a new attempt wait.
_lockPool = threading.Lock()
_iLiveConnectWorkers = 0


class TransportFailure(Exception):
    """A GET that could not be opened; ``bTransient`` says whether to retry.

    ``sFailureClass`` names the shape for the sentence table; the
    message is the technical detail kept for diagnosis.
    """

    def __init__(self, sMessage, sFailureClass, bTransient):
        super().__init__(sMessage)
        self.sFailureClass = sFailureClass
        self.bTransient = bTransient


def fiCountLiveConnectWorkers():
    """Return how many connect workers exist right now, abandoned included."""
    with _lockPool:
        return _iLiveConnectWorkers


def fsBuildRangeHeader(iStartByte):
    """Return the ``Range`` value asking for the file from ``iStartByte`` on."""
    return f"bytes={int(iStartByte)}-"


# ---------------------------------------------------------------------
# The request, hop by hop.
# ---------------------------------------------------------------------


def fdictOpenRangedGet(
    sUrl, iStartByte, listAllowedOrigins, dictControl, fReadTimeoutSeconds,
):
    """Open a ranged GET and return its answer with the headers read.

    Returns ``{"httpresponseAnswer", "socketLive", "iStatus",
    "dictHeaders", "sFinalUrl"}``. Redirects are followed by hand, each
    hop's origin checked first; a hop outside the allowlist raises
    :class:`TransportFailure` (``refused-host``, never transient).
    ``dictControl`` is the download control dict: its ``eventCancel``
    is honored at every wait, and the socket is registered under
    ``dictLive["socketLive"]`` so :func:`fnShutdownLiveSocket` reaches
    it. Cancel raises ``CancelledDuringTransport`` for the caller to
    translate.
    """
    for _iHop in range(_I_MAX_REDIRECT_HOPS + 1):
        _fnRefuseOutsideAllowlist(sUrl, listAllowedOrigins)
        socketLive = _fsocketConnect(sUrl, dictControl, fReadTimeoutSeconds)
        _fnRegisterLiveSocket(dictControl, socketLive)
        try:
            httpresponseAnswer = _fhttpresponseSendAndReadHeaders(
                socketLive, sUrl, iStartByte,
            )
        except BaseException:
            _fnCloseSocketQuietly(socketLive)
            _fnRegisterLiveSocket(dictControl, None)
            raise
        if not _fbAnswersWithRedirect(httpresponseAnswer):
            return {
                "httpresponseAnswer": httpresponseAnswer,
                "socketLive": socketLive,
                "iStatus": int(httpresponseAnswer.status),
                "dictHeaders": {
                    sKey.lower(): sValue
                    for sKey, sValue in httpresponseAnswer.getheaders()
                },
                "sFinalUrl": sUrl,
            }
        sNextUrl = urljoin(sUrl, str(httpresponseAnswer.getheader("Location")))
        _fnCloseSocketQuietly(socketLive)
        _fnRegisterLiveSocket(dictControl, None)
        sUrl = sNextUrl
    raise TransportFailure(
        f"more than {_I_MAX_REDIRECT_HOPS} redirects", S_FAILURE_REFUSED_HOST,
        False,
    )


def _fnRefuseOutsideAllowlist(sUrl, listAllowedOrigins):
    """Raise unless the URL's origin is one the allowlist names."""
    from vaibify.reproducibility.zenodoClient import fsOriginOfUrl
    sOrigin = fsOriginOfUrl(sUrl)
    if sOrigin and sOrigin in listAllowedOrigins:
        return
    raise TransportFailure(
        "refusing to fetch from " + (sOrigin or "a URL with no host")
        + ": it is not a Zenodo host this vaibify knows ("
        + ", ".join(listAllowedOrigins) + ")",
        S_FAILURE_REFUSED_HOST, False,
    )


def _fbAnswersWithRedirect(httpresponseAnswer):
    iStatus = int(httpresponseAnswer.status)
    return 300 <= iStatus < 400 and bool(
        httpresponseAnswer.getheader("Location"),
    )


def _fhttpresponseSendAndReadHeaders(socketLive, sUrl, iStartByte):
    """Write the GET on an open socket and parse the status and headers."""
    tParts = urlsplit(sUrl)
    sTarget = tParts.path or "/"
    if tParts.query:
        sTarget += "?" + tParts.query
    sRequest = (
        f"GET {sTarget} HTTP/1.1\r\n"
        f"Host: {tParts.netloc}\r\n"
        f"User-Agent: {_S_USER_AGENT}\r\n"
        "Accept: */*\r\n"
        "Accept-Encoding: identity\r\n"
        f"Range: {fsBuildRangeHeader(iStartByte)}\r\n"
        "Connection: close\r\n"
        "\r\n"
    )
    socketLive.sendall(sRequest.encode("ascii"))
    httpresponseAnswer = http.client.HTTPResponse(socketLive, method="GET")
    httpresponseAnswer.begin()
    return httpresponseAnswer


def fnCloseAnswer(dictAnswer):
    """Close an answer's response and socket, quietly."""
    try:
        dictAnswer["httpresponseAnswer"].close()
    except Exception:  # noqa: BLE001 -- closing is best effort
        pass
    _fnCloseSocketQuietly(dictAnswer.get("socketLive"))


def _fnCloseSocketQuietly(socketAny):
    if socketAny is None:
        return
    try:
        socketAny.close()
    except OSError:
        pass


# ---------------------------------------------------------------------
# Cancel reaches the socket from another thread.
# ---------------------------------------------------------------------


def _fnRegisterLiveSocket(dictControl, socketAny):
    """Record (or clear) the socket a Cancel must shut down."""
    with dictControl["lockLive"]:
        dictControl["dictLive"]["socketLive"] = socketAny


def fnShutdownLiveSocket(dictControl):
    """Shut the live socket down so a blocked read returns at once.

    ``shutdown`` rather than ``close``: closing a descriptor another
    thread is blocked on does not reliably wake that thread on macOS;
    shutting the connection down does, on both platforms.
    """
    with dictControl["lockLive"]:
        socketAny = dictControl["dictLive"].get("socketLive")
    if socketAny is None:
        return
    try:
        socketAny.shutdown(socket.SHUT_RDWR)
    except OSError:
        pass


class CancelledDuringTransport(Exception):
    """Cancel was requested while a connection was being opened."""


# ---------------------------------------------------------------------
# Connecting on a bounded, abandonable worker pool.
# ---------------------------------------------------------------------


def _fsocketConnect(sUrl, dictControl, fReadTimeoutSeconds):
    """Return a connected (and, for https, TLS-wrapped) socket, or raise.

    The DNS lookup and the connect run on a worker; this thread waits
    with a deadline, checking Cancel every ``_F_WORKER_POLL_SECONDS``.
    An attempt abandoned here leaves its worker to finish on its own
    (bounded by the pool), and the worker closes the socket it made.
    """
    global _iLiveConnectWorkers
    # "errorConnect" is set by the worker alone, so a read of it is a
    # ``get``: present only when an attempt actually failed.
    dictAttempt = {
        "iToken": _fiNextToken(dictControl), "socketConnected": None,
        "bAbandoned": False, "bDone": False,
    }
    _fnWaitForAFreeWorker(dictControl)
    threadConnect = threading.Thread(
        target=_fnConnectInWorker, args=(dictAttempt, sUrl, dictControl),
        name="vaibify-connect", daemon=True,
    )
    with _lockPool:
        _iLiveConnectWorkers += 1
    threadConnect.start()
    fDeadline = time.monotonic() + _F_CONNECT_TIMEOUT_SECONDS + 1.0
    while not dictAttempt["bDone"]:
        if dictControl["eventCancel"].wait(_F_WORKER_POLL_SECONDS):
            _fnAbandonAttempt(dictAttempt)
            raise CancelledDuringTransport()
        if time.monotonic() >= fDeadline:
            _fnAbandonAttempt(dictAttempt)
            raise TransportFailure(
                f"could not connect to {urlsplit(sUrl).hostname} within "
                f"{_F_CONNECT_TIMEOUT_SECONDS:.0f} s",
                S_FAILURE_COULD_NOT_CONNECT, True,
            )
    errorConnect = dictAttempt.get("errorConnect")
    if errorConnect is not None:
        raise errorConnect
    socketConnected = dictAttempt["socketConnected"]
    socketConnected.settimeout(fReadTimeoutSeconds)
    return socketConnected


def _fiNextToken(dictControl):
    with dictControl["lockLive"]:
        dictControl["dictLive"]["iCurrentToken"] = (
            dictControl["dictLive"].get("iCurrentToken", 0) + 1
        )
        return dictControl["dictLive"]["iCurrentToken"]


def _fnAbandonAttempt(dictAttempt):
    """Mark an attempt so its worker closes the socket it may still make."""
    with _lockPool:
        dictAttempt["bAbandoned"] = True
        socketLate = dictAttempt["socketConnected"]
        dictAttempt["socketConnected"] = None
    _fnCloseSocketQuietly(socketLate)


def _fnWaitForAFreeWorker(dictControl):
    """Wait, cancellably, until fewer than the maximum workers are alive."""
    bSaid = False
    while True:
        with _lockPool:
            bFree = _iLiveConnectWorkers < _I_MAX_CONNECT_WORKERS
        if bFree:
            return
        if not bSaid:
            bSaid = True
            with dictControl["lockLive"]:
                dictControl["dictLive"]["sAttemptState"] = (
                    "Earlier connection attempts are still hanging; "
                    "waiting for one to time out"
                )
        if dictControl["eventCancel"].wait(_F_WORKER_POLL_SECONDS):
            raise CancelledDuringTransport()


def _fnConnectInWorker(dictAttempt, sUrl, dictControl):
    """Resolve, connect, tunnel and wrap; hand back a socket or an error.

    Runs on the pool. Writes nothing but the attempt's own slots, and
    only after checking, under the pool lock, that the attempt still
    wants the result; a late socket is closed here.
    """
    global _iLiveConnectWorkers
    socketConnected, errorConnect = None, None
    try:
        socketConnected = _fsocketOpenToUrl(sUrl)
    except ssl.SSLError as error:
        errorConnect = TransportFailure(
            f"the secure connection could not be established: {error}",
            S_FAILURE_TLS, False,
        )
    except (OSError, http.client.HTTPException) as error:
        errorConnect = TransportFailure(
            f"could not connect: {type(error).__name__}: {error}",
            S_FAILURE_COULD_NOT_CONNECT, True,
        )
    with _lockPool:
        _iLiveConnectWorkers -= 1
        bWanted = not dictAttempt["bAbandoned"] and (
            dictControl["dictLive"].get("iCurrentToken") == dictAttempt["iToken"]
        )
        if bWanted:
            dictAttempt["socketConnected"] = socketConnected
            if errorConnect is not None:
                dictAttempt["errorConnect"] = errorConnect
            socketConnected = None
        dictAttempt["bDone"] = True
    _fnCloseSocketQuietly(socketConnected)


def _fsocketOpenToUrl(sUrl):
    """Open a plain or TLS socket to the URL's host, via a proxy if one is set."""
    tParts = urlsplit(sUrl)
    bTls = tParts.scheme.lower() == "https"
    sHost = tParts.hostname or ""
    iPort = tParts.port or (443 if bTls else 80)
    dictProxy = _fdictProxyFor(tParts.scheme.lower(), sHost)
    if dictProxy is None:
        socketRaw = socket.create_connection(
            (sHost, iPort), timeout=_F_CONNECT_TIMEOUT_SECONDS,
        )
    else:
        socketRaw = _fsocketOpenTunnel(dictProxy, sHost, iPort)
    if not bTls:
        return socketRaw
    try:
        return ssl.create_default_context(cafile=certifi.where()).wrap_socket(
            socketRaw, server_hostname=sHost,
        )
    except BaseException:
        _fnCloseSocketQuietly(socketRaw)
        raise


def _fdictProxyFor(sScheme, sHost):
    """Return ``{sHost, iPort, sAuthorization}`` of the proxy to use, or None."""
    if proxy_bypass(sHost):
        return None
    sProxyUrl = getproxies().get(sScheme) or ""
    if not sProxyUrl:
        return None
    tProxy = urlsplit(sProxyUrl if "://" in sProxyUrl else "http://" + sProxyUrl)
    sAuthorization = ""
    if tProxy.username:
        sCredentials = f"{tProxy.username}:{tProxy.password or ''}"
        sAuthorization = "Basic " + base64.b64encode(
            sCredentials.encode("utf-8"),
        ).decode("ascii")
    return {
        "sHost": tProxy.hostname or "", "iPort": tProxy.port or 3128,
        "sAuthorization": sAuthorization,
    }


def _fsocketOpenTunnel(dictProxy, sHost, iPort):
    """CONNECT through an HTTP proxy and return the tunnelled socket."""
    socketProxy = socket.create_connection(
        (dictProxy["sHost"], dictProxy["iPort"]),
        timeout=_F_CONNECT_TIMEOUT_SECONDS,
    )
    try:
        sRequest = f"CONNECT {sHost}:{iPort} HTTP/1.1\r\nHost: {sHost}:{iPort}\r\n"
        if dictProxy["sAuthorization"]:
            sRequest += f"Proxy-Authorization: {dictProxy['sAuthorization']}\r\n"
        socketProxy.sendall((sRequest + "\r\n").encode("ascii"))
        httpresponseTunnel = http.client.HTTPResponse(socketProxy, method="CONNECT")
        httpresponseTunnel.begin()
        if int(httpresponseTunnel.status) != 200:
            raise OSError(
                f"the proxy refused the tunnel: {httpresponseTunnel.status} "
                f"{httpresponseTunnel.reason}"
            )
    except BaseException:
        _fnCloseSocketQuietly(socketProxy)
        raise
    return socketProxy
