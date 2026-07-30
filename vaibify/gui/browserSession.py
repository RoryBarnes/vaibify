"""Per-browser session identity and the capability bootstrap.

Replaces the single shared hub session token (which ``/api/session-token``
handed to any caller that cleared the middleware, so a container-side
actor on loopback could obtain it). The browser is launched with an
unguessable one-time capability in the URL fragment, exchanges it once at
``/api/bootstrap`` for a per-browser ``BrowserSessionRecord`` credential,
and presents that credential on every request. The container never
receives the capability or any credential.

This module owns the capability and browser-session stores. The full
lifecycle (sliding expiry, revocation, the ORPHANED_SESSION reclaim, the
lease binding) lands in later slices; here a redeemed credential is valid
until process exit, and the capability supports bounded replay so a lost
bootstrap response is recoverable.
"""

import secrets
import threading
import time
from dataclasses import dataclass

__all__ = [
    "BootstrapCapability",
    "BrowserSessionRecord",
    "fdictCreateBrowserSessionStore",
    "fsMintBootstrapCapability",
    "ftRedeemCapability",
    "fbValidateCredential",
    "fsSessionIdForCredential",
    "I_CAPABILITY_TTL_SECONDS",
]

# Bounded replay window: re-presenting a redeemed capability within this
# many seconds returns the same credential, so a bootstrap response
# dropped in transit is recoverable without minting a second session.
I_CAPABILITY_TTL_SECONDS = 300

_lockBrowserSessions = threading.Lock()


@dataclass
class BootstrapCapability:
    """A one-time launch capability exchanged for a browser credential."""

    sCapability: str
    sState: str            # ARMED | REDEEMED | EXPIRED
    fMintedMonotonic: float
    sIssuedCredential: str = ""
    sIssuedSessionId: str = ""


@dataclass
class BrowserSessionRecord:
    """One browser session's identity and its bearer credential."""

    sSessionId: str
    sCredential: str
    fCreatedMonotonic: float
    fLastSeenMonotonic: float


def fdictCreateBrowserSessionStore():
    """Return an empty browser-session store for ``app.state``."""
    return {
        "dictCapabilities": {},          # sCapability -> BootstrapCapability
        "dictSessionsByCredential": {},  # sCredential -> BrowserSessionRecord
    }


def fsMintBootstrapCapability(dictStore):
    """Mint an unguessable ARMED capability, record it, and return it."""
    sCapability = secrets.token_urlsafe(32)
    with _lockBrowserSessions:
        dictStore["dictCapabilities"][sCapability] = BootstrapCapability(
            sCapability=sCapability,
            sState="ARMED",
            fMintedMonotonic=time.monotonic(),
        )
    return sCapability


def ftRedeemCapability(dictStore, sCapability):
    """Exchange a capability for a browser-session credential.

    The first redemption mints a ``BrowserSessionRecord`` and records the
    issued credential on the capability. Re-presenting the SAME capability
    within :data:`I_CAPABILITY_TTL_SECONDS` returns the SAME credential (so
    a lost response is recoverable), never a second session. After the TTL
    the capability is ``EXPIRED`` and yields nothing. Returns
    ``(sSessionId, sCredential)`` or ``(None, None)``.
    """
    with _lockBrowserSessions:
        recordCap = dictStore["dictCapabilities"].get(sCapability)
        if recordCap is None:
            return (None, None)
        fNow = time.monotonic()
        if fNow - recordCap.fMintedMonotonic > I_CAPABILITY_TTL_SECONDS:
            recordCap.sState = "EXPIRED"
            return (None, None)
        if recordCap.sState == "REDEEMED":
            return (recordCap.sIssuedSessionId, recordCap.sIssuedCredential)
        if recordCap.sState != "ARMED":
            return (None, None)
        return _tMintSessionForCapability(dictStore, recordCap, fNow)


def _tMintSessionForCapability(dictStore, recordCap, fNow):
    """Mint the session for a first redemption. Caller holds the lock."""
    sSessionId = secrets.token_urlsafe(16)
    sCredential = secrets.token_urlsafe(32)
    dictStore["dictSessionsByCredential"][sCredential] = BrowserSessionRecord(
        sSessionId=sSessionId,
        sCredential=sCredential,
        fCreatedMonotonic=fNow,
        fLastSeenMonotonic=fNow,
    )
    recordCap.sState = "REDEEMED"
    recordCap.sIssuedSessionId = sSessionId
    recordCap.sIssuedCredential = sCredential
    return (sSessionId, sCredential)


def fbValidateCredential(dictStore, sCredential):
    """Return True when the credential names a live browser session.

    Refreshes the session's last-seen stamp (the sliding-window input the
    expiry lifecycle will consume in a later slice).
    """
    if not sCredential:
        return False
    with _lockBrowserSessions:
        recordSession = dictStore["dictSessionsByCredential"].get(sCredential)
        if recordSession is None:
            return False
        recordSession.fLastSeenMonotonic = time.monotonic()
        return True


def fsSessionIdForCredential(dictStore, sCredential):
    """Return the browser-session id for a credential, or '' if unknown."""
    with _lockBrowserSessions:
        recordSession = dictStore["dictSessionsByCredential"].get(sCredential)
        return recordSession.sSessionId if recordSession else ""
