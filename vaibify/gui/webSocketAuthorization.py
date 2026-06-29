"""Shared WebSocket / REST container-session authorization guard.

One gate, consulted verbatim by the pipeline WebSocket, the terminal
WebSocket, and the connect handler, so the access model lives in exactly
one place instead of being duplicated across route modules.

A browser connection is authorized only when all three hold: it arrives
from a loopback ``Origin`` (the trust boundary), it carries the shared
session token (the CSRF / trust credential), and it presents the lease
that currently owns the container in ``dictContainerOwners`` (the
exclusivity principal). Separately, the in-container ``vaibify-do`` agent
is authorized by the shared token alone for a container that already has
a live owner -- a per-container, lease-exempt machine lane that lets the
agent act inside a session the researcher has already claimed.

The lease ownership is the single authority introduced by
:mod:`vaibify.gui.containerOwnership`; this module never consults the old
process-global allow set.
"""

__all__ = [
    "fbCheckOrigin",
    "fbCheckSharedToken",
    "fbCheckLeaseOwnership",
    "fbCheckAgentToken",
    "fbAuthorizeContainerSession",
    "fiContainerSessionRejectionCode",
    "fbRefuseSecondLiveConnection",
    "fnServeUnderLiveConnectionCounters",
]

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
    full browser gate (shared token then owning lease), failing closed at
    the first unmet condition so the client can tell a bad token from a
    foreign lease. A non-loopback connection is never a browser, so it is
    admitted only through the lease-exempt agent lane and otherwise
    rejected as an untrusted origin.
    """
    sSharedToken = dictCtx.get("sSessionToken", "")
    dictContainerOwners = dictCtx.get("dictContainerOwners", {})
    if not fbCheckOrigin(connection):
        if fbCheckAgentToken(connection, dictContainerOwners, sName):
            return I_REJECT_AUTHORIZED
        return I_REJECT_BAD_ORIGIN
    if not fbCheckSharedToken(connection, sSharedToken):
        return I_REJECT_BAD_TOKEN
    if not fbCheckLeaseOwnership(connection, dictContainerOwners, sName):
        return I_REJECT_FOREIGN_LEASE
    return I_REJECT_AUTHORIZED


def fbAuthorizeContainerSession(connection, dictCtx, sName):
    """Return True when the connection may access the named container."""
    return fiContainerSessionRejectionCode(
        connection, dictCtx, sName,
    ) == I_REJECT_AUTHORIZED


def fbRefuseSecondLiveConnection(dictContainerOwners, sName):
    """Return True when a second live browser connection would exceed one.

    The owner-of-record permits exactly one live WebSocket per
    container; a duplicate browser tab that copied the lease passes the
    lease gate but must be refused here so the one-session guarantee
    holds at the connection layer, not merely at claim time.
    """
    recordOwner = dictContainerOwners.get(sName)
    return recordOwner is not None and recordOwner.iLiveConnectionCount >= 1


async def fnServeUnderLiveConnectionCounters(
    connection, dictContainerOwners, sName, fnServe,
    fnIncrementGlobal, fnDecrementGlobal,
):
    """Serve an already-gated WebSocket under the live-connection counters.

    Refuses a duplicate browser tab with 4409 (the owner permits one
    live connection); otherwise increments the per-container counter
    (browser lane only) and the app-global counter before serving, and
    decrements both in a ``finally`` so the idle watchdog and the
    ownership reaper always observe an accurate live count. The agent
    lane (non-loopback origin) is exempt from the per-container budget so
    a machine action never displaces the researcher's single session.
    """
    bBrowser = fbCheckOrigin(connection)
    if bBrowser and fbRefuseSecondLiveConnection(dictContainerOwners, sName):
        await connection.close(code=I_REJECT_DUPLICATE_SESSION)
        return
    if bBrowser:
        containerOwnership.fnIncrementLiveConnection(
            dictContainerOwners, sName,
        )
    fnIncrementGlobal()
    try:
        await fnServe()
    finally:
        fnDecrementGlobal()
        if bBrowser:
            containerOwnership.fnDecrementLiveConnection(
                dictContainerOwners, sName,
            )
