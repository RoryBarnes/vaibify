"""Shared WebSocket / REST container-session authorization guard.

One gate, consulted verbatim by the pipeline WebSocket, the terminal
WebSocket, and the connect handler, so the access model lives in exactly
one place instead of being duplicated across route modules.

A browser connection is authorized only when all three hold: it arrives
from a loopback ``Origin`` (the trust boundary), its ``sToken`` query param
is a valid per-browser ``BrowserSessionRecord`` credential (the retired
shared session token is never accepted), and the lease it presents is bound
to that browser session in ``dictContainerOwners`` (the exclusivity
principal). Validating "some valid credential" plus "some valid lease"
independently would let a second browser session replay a copied lease, so
the lease is checked against the credential's own session via
:func:`containerOwnership.fbBrowserSessionOwnsLease`. Separately, the
in-container ``vaibify-do`` agent is authorized by the container's own
per-container agent token for a container that already has a live owner --
a lease-exempt machine lane that lets the agent act inside a session the
researcher has already claimed.

The lease ownership is the single authority introduced by
:mod:`vaibify.gui.containerOwnership`; this module never consults the old
process-global allow set.
"""

__all__ = [
    "fbCheckOrigin",
    "fbCheckSharedToken",
    "fbCheckLeaseOwnership",
    "fsBrowserSessionIdForCredential",
    "fbCheckBoundLeaseOwnership",
    "fbCheckAgentToken",
    "fbAuthorizeContainerSession",
    "fiContainerSessionRejectionCode",
    "fbRefuseSecondLiveConnection",
    "fnCloseWithCode",
    "fnServeUnderLiveConnectionCounters",
]

from . import browserSession
from . import containerOwnership
from .pipelineServer import fbHasAgentToken, fbValidateWebSocketOrigin


I_REJECT_AUTHORIZED = 0
I_REJECT_BAD_ORIGIN = 4003
I_REJECT_BAD_TOKEN = 4401
I_REJECT_FOREIGN_LEASE = 4403
I_REJECT_DUPLICATE_SESSION = 4409


def fbCheckOrigin(connection):
    """Return True when the connection carries a trusted loopback origin.

    Passing no expected token keeps this a pure browser-origin check;
    the agent's token bypass is handled separately by
    :func:`fbCheckAgentToken` so the two lanes never blur together.
    """
    return fbValidateWebSocketOrigin(connection)


def fbCheckSharedToken(connection, sSharedToken):
    """Return True when the connection presents the shared session token."""
    sPresented = connection.query_params.get("sToken", "")
    return bool(sSharedToken) and sPresented == sSharedToken


def fbCheckLeaseOwnership(connection, dictContainerOwners, sName):
    """Return True when the presented lease owns the named container."""
    sLeaseId = connection.query_params.get("sLeaseId", "")
    return containerOwnership.fbSessionOwnsContainer(
        dictContainerOwners, sName, sLeaseId,
    )


def fsBrowserSessionIdForCredential(connection, dictBrowserSessions):
    """Return the browser-session id for the connection's credential, or ''.

    Validates the ``sToken`` query param as a per-browser credential (never
    the retired shared token), refreshing the session's last-seen stamp, and
    returns its session id. An unknown, empty, or expired credential yields
    ``''``, which the gate maps to a bad-token refusal.
    """
    sCredential = connection.query_params.get("sToken", "")
    if not browserSession.fbValidateCredential(
        dictBrowserSessions, sCredential,
    ):
        return ""
    return browserSession.fsSessionIdForCredential(
        dictBrowserSessions, sCredential,
    )


def fbCheckBoundLeaseOwnership(
    connection, dictContainerOwners, sName, sBrowserSessionId,
):
    """Return True when the presented lease is bound to this browser session.

    The lease is checked against the credential's OWN session, so a second
    session presenting a copied lease is refused even though the lease value
    is genuine.
    """
    sLeaseId = connection.query_params.get("sLeaseId", "")
    return containerOwnership.fbBrowserSessionOwnsLease(
        dictContainerOwners, sName, sBrowserSessionId, sLeaseId,
    )


def fbCheckAgentToken(connection, dictContainerOwners, sName):
    """Return True for the in-container agent on its OWN container.

    The agent dials in over ``host.docker.internal`` (no loopback origin)
    and holds no lease. It is authorized by the per-container agent token
    minted on the named container's owner record -- a credential distinct
    from every other container's token -- so an agent compromised in one
    container cannot authenticate against another. This lane is only ever
    consulted for a connection WITHOUT a loopback origin, so a browser
    (which the user agent always stamps with an Origin header) can never
    reach it and thereby skip the lease.
    """
    sAgentToken = containerOwnership.fsAgentTokenForName(
        dictContainerOwners, sName,
    )
    return bool(sAgentToken) and fbHasAgentToken(connection, sAgentToken)


def fiContainerSessionRejectionCode(connection, dictCtx, sName):
    """Return the WebSocket close code, or ``0`` when authorized.

    Origin is the lane discriminator. A loopback browser must clear the
    full browser gate (a valid per-browser credential, then the lease bound
    to that credential's session), failing closed at the first unmet
    condition so the client can tell a bad credential from a foreign lease.
    A non-loopback connection is never a browser, so it is admitted only
    through the lease-exempt agent lane and otherwise rejected as an
    untrusted origin.
    """
    dictContainerOwners = dictCtx.get("dictContainerOwners", {})
    dictBrowserSessions = dictCtx.get("dictBrowserSessions", {})
    if not fbCheckOrigin(connection):
        if fbCheckAgentToken(connection, dictContainerOwners, sName):
            return I_REJECT_AUTHORIZED
        return I_REJECT_BAD_ORIGIN
    sBrowserSessionId = fsBrowserSessionIdForCredential(
        connection, dictBrowserSessions,
    )
    if not sBrowserSessionId:
        return I_REJECT_BAD_TOKEN
    if not fbCheckBoundLeaseOwnership(
        connection, dictContainerOwners, sName, sBrowserSessionId,
    ):
        return I_REJECT_FOREIGN_LEASE
    return I_REJECT_AUTHORIZED


def fbAuthorizeContainerSession(connection, dictCtx, sName):
    """Return True when the connection may access the named container."""
    return fiContainerSessionRejectionCode(
        connection, dictCtx, sName,
    ) == I_REJECT_AUTHORIZED


def fbRefuseSecondLiveConnection(dictContainerOwners, sName):
    """Return True when a second live pipeline connection would exceed one.

    The owner-of-record permits exactly one live *pipeline* WebSocket
    per container; a duplicate browser tab that copied the lease passes
    the lease gate but must be refused here so two sessions can never
    drive the pipeline concurrently. Terminal sockets are exempt — one
    legitimate session holds the terminal strip plus the pipeline
    socket, and several terminal tabs, at the same time.
    """
    recordOwner = dictContainerOwners.get(sName)
    return (
        recordOwner is not None
        and recordOwner.iLivePipelineConnectionCount >= 1
    )


async def fnCloseWithCode(connection, iCloseCode):
    """Complete the handshake, then close with the real refusal code.

    Closing before ``accept`` downgrades every refusal to an opaque
    HTTP 403, which a browser can only observe as close code 1006 —
    indistinguishable from a dead server. Accepting first lets the
    close frame carry the deliberate 4xxx code, so the client can
    report the true reason instead of "cannot reach server" and knows
    not to retry.
    """
    await connection.accept()
    await connection.close(code=iCloseCode)


async def fnServeUnderLiveConnectionCounters(
    connection, dictContainerOwners, sName, fnServe,
    fnIncrementGlobal, fnDecrementGlobal, bExclusivePipelineLane=False,
):
    """Serve an already-gated WebSocket under the live-connection counters.

    On the pipeline lane (``bExclusivePipelineLane``), refuses a second
    concurrent browser connection with 4409 — the duplicate-tab budget;
    the terminal lane is unbudgeted. Otherwise increments the
    per-container counters (browser lane only) and the app-global
    counter before serving, and decrements them in a ``finally`` so the
    idle watchdog and the ownership reaper always observe an accurate
    live count. The agent lane (non-loopback origin) is exempt from the
    per-container budget so a machine action never displaces the
    researcher's single session.
    """
    bBrowser = fbCheckOrigin(connection)
    if bBrowser and bExclusivePipelineLane and fbRefuseSecondLiveConnection(
        dictContainerOwners, sName,
    ):
        await fnCloseWithCode(connection, I_REJECT_DUPLICATE_SESSION)
        return
    if bBrowser:
        containerOwnership.fnIncrementLiveConnection(
            dictContainerOwners, sName,
            bPipelineLane=bExclusivePipelineLane,
        )
    fnIncrementGlobal()
    try:
        await fnServe()
    finally:
        fnDecrementGlobal()
        if bBrowser:
            containerOwnership.fnDecrementLiveConnection(
                dictContainerOwners, sName,
                bPipelineLane=bExclusivePipelineLane,
            )
