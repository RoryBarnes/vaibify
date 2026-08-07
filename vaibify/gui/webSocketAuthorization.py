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
    "fsBrowserSessionIdForCredential",
    "fbCheckBoundLeaseOwnership",
    "fbCheckAgentToken",
    "fbAuthorizeContainerSession",
    "fiContainerSessionRejectionCode",
    "fbRefuseSecondLiveConnection",
    "ffnBuildPerFrameCredentialCheck",
    "fnCloseWithCode",
    "fnServeUnderLiveConnectionCounters",
    "fbContainerIsPoisoned",
    "I_REJECT_TERMINAL_DISABLED",
    "I_REJECT_POISONED",
]

from . import browserSession
from . import containerOwnership
from .pipelineServer import fbHasAgentToken, fbValidateWebSocketOrigin


I_REJECT_AUTHORIZED = 0
I_REJECT_BAD_ORIGIN = 4003
I_REJECT_BAD_TOKEN = 4401
I_REJECT_FOREIGN_LEASE = 4403
I_REJECT_DUPLICATE_SESSION = 4409

# The interactive terminal is withdrawn for the alpha (see AGENTS.md,
# "Withdrawn for the alpha"). The code is deliberately distinct from
# every authorization refusal above: the socket is refused because the
# feature is gone, not because this browser lacks standing, and a
# client that cannot tell the two apart would advise the researcher to
# re-claim a container that is already theirs.
I_REJECT_TERMINAL_DISABLED = 4503

# A container whose owner record is POISONED: a guarded worker was
# force-abandoned and could not be proven dead, so every mutation
# channel is refused until reconciliation proves it gone. Distinct from
# every authorization code because the caller's standing is not the
# problem -- the container is -- and the recovery is 'vaibify
# reconcile', not a re-claim.
I_REJECT_POISONED = 4423


def fbCheckOrigin(connection):
    """Return True when the connection carries a trusted loopback origin.

    Passing no expected token keeps this a pure browser-origin check;
    the agent's token bypass is handled separately by
    :func:`fbCheckAgentToken` so the two lanes never blur together.
    """
    return fbValidateWebSocketOrigin(connection)


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


def fbContainerIsPoisoned(dictContainerOwners, sName):
    """Return True when the named container carries a poison record.

    A poisoned record means a guarded worker was force-abandoned and
    could not be proven dead. Exclusivity is RETAINED — the record keeps
    its flock — and every mutation is refused until reconciliation
    proves the worker gone. The pipeline socket is a mutation channel,
    so it is refused too; safe observability reads and the trusted host
    reconciliation lane are not, because fencing those would fence off
    the poison's own cure.
    """
    recordOwner = (dictContainerOwners or {}).get(sName)
    return recordOwner is not None and getattr(
        recordOwner, "poison", None,
    ) is not None


def ffnBuildPerFrameCredentialCheck(
    connection, dictBrowserSessions, dictContainerOwners=None, sName="",
    iAcceptedGeneration=None,
):
    """Return the per-frame re-auth backstop for an accepted WebSocket.

    The active close on revocation (design §5) is authoritative; this
    backstop covers a socket already mid-frame in the revoke window: a
    browser socket's frames keep authorizing only while the credential
    it connected with still names an ACTIVE browser session, so a
    REVOKED session's in-flight frame is refused instead of dispatched.
    Each passing check also refreshes the session's last-seen stamp —
    frames are activity for the sliding-idle window (design §11).

    ``dictContainerOwners`` extends the same backstop to the two
    container-scoped facts that can change under a live socket: the
    container becoming POISONED, and its owner GENERATION rotating
    under a host transfer. Both are re-read per frame rather than
    captured at accept, because a socket admitted before either event
    is exactly the socket that must stop acting after it. These checks
    apply to the AGENT lane too — the agent holds no browser session,
    but it drives the same mutation channel, and a poisoned container
    must refuse a machine caller just as firmly as a human one.
    """
    bBrowser = fbCheckOrigin(connection)
    sCredential = connection.query_params.get("sToken", "")

    def fbFrameStillAuthorized():
        if bBrowser and not browserSession.fbValidateCredential(
            dictBrowserSessions or {}, sCredential,
        ):
            return False
        if dictContainerOwners is None:
            return True
        if fbContainerIsPoisoned(dictContainerOwners, sName):
            return False
        return iAcceptedGeneration is None or (
            containerOwnership.fiOwnerGenerationForName(
                dictContainerOwners, sName,
            ) == iAcceptedGeneration
        )

    return fbFrameStillAuthorized


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
    dictSessionSockets=None, dictBrowserSessions=None,
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

    Each admitted browser socket is tracked as a ``ConnectionRecord``
    stamped with the owner generation current at accept: the decrement
    is matched on that record, so a socket whose ``finally`` fires after
    the generation rotated decrements nothing on the successor, and the
    record is indexed by browser session in ``dictSessionSockets`` for
    the active-close path a revocation needs (design §2.3).
    """
    bBrowser = fbCheckOrigin(connection)
    if bBrowser and bExclusivePipelineLane and fbRefuseSecondLiveConnection(
        dictContainerOwners, sName,
    ):
        await fnCloseWithCode(connection, I_REJECT_DUPLICATE_SESSION)
        return
    recordConnection = None
    if bBrowser:
        containerOwnership.fnIncrementLiveConnection(
            dictContainerOwners, sName,
            bPipelineLane=bExclusivePipelineLane,
        )
        recordConnection = _frecordBuildConnectionRecord(
            connection, dictContainerOwners, sName,
            bExclusivePipelineLane, dictBrowserSessions,
        )
        containerOwnership.fnRegisterSessionSocket(
            dictSessionSockets, recordConnection,
        )
    fnIncrementGlobal()
    try:
        await fnServe()
    finally:
        fnDecrementGlobal()
        if bBrowser:
            containerOwnership.fnDecrementLiveConnectionForRecord(
                dictContainerOwners, sName, recordConnection,
            )
            containerOwnership.fnDeregisterSessionSocket(
                dictSessionSockets, recordConnection,
            )


def _frecordBuildConnectionRecord(
    connection, dictContainerOwners, sName, bExclusivePipelineLane,
    dictBrowserSessions,
):
    """Build the tracked record for an admitted browser WebSocket."""
    return containerOwnership.ConnectionRecord(
        websocket=connection,
        sBrowserSessionId=fsBrowserSessionIdForCredential(
            connection, dictBrowserSessions or {},
        ),
        iOwnerGeneration=containerOwnership.fiOwnerGenerationForName(
            dictContainerOwners, sName,
        ),
        sLane=(
            containerOwnership.S_LANE_PIPELINE
            if bExclusivePipelineLane
            else containerOwnership.S_LANE_TERMINAL
        ),
    )
