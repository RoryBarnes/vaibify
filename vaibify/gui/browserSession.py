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
    "S_SESSION_STATE_ACTIVE",
    "S_SESSION_STATE_REVOKED",
    "S_CAPABILITY_OPERATION_BOOTSTRAP",
    "S_CAPABILITY_OPERATION_TRANSFER",
    "fdictCreateBrowserSessionStore",
    "fsMintBootstrapCapability",
    "fsMintTransferCapability",
    "fsCapabilityOperationKind",
    "ftRedeemCapability",
    "fdictInspectTransferCapability",
    "fnExpireCapability",
    "fnExpireCapabilitiesForSession",
    "ftMintDetachedSessionRecord",
    "fnDiscardSessionRecord",
    "fnRevokeSessionById",
    "fnStoreTransferResult",
    "fbValidateCredential",
    "fsSessionIdForCredential",
    "I_CAPABILITY_TTL_SECONDS",
]

# Bounded replay window: re-presenting a redeemed capability within this
# many seconds returns the same credential, so a bootstrap response
# dropped in transit is recoverable without minting a second session.
I_CAPABILITY_TTL_SECONDS = 300

_lockBrowserSessions = threading.Lock()

# The browser-credential axis (design §2.2): a REVOKED record authorizes
# nothing, ever — revocation is how the ORPHANED_SESSION transition cuts
# a departed browser's credential without deleting its audit trail.
S_SESSION_STATE_ACTIVE = "ACTIVE"
S_SESSION_STATE_REVOKED = "REVOKED"

# The capability operations (design §2.4): "bootstrap" mints a plain
# browser session; "transfer" additionally commits the host-authorized
# ownership transfer (slice 5) and is minted ONLY over the
# peer-authenticated host control socket, never by the hub launch path.
S_CAPABILITY_OPERATION_BOOTSTRAP = "bootstrap"
S_CAPABILITY_OPERATION_TRANSFER = "transfer"


@dataclass
class BootstrapCapability:
    """A one-time launch capability exchanged for a browser credential.

    A ``transfer`` capability additionally names its target container
    and the owner generation it expects (the ABA guard: a stale
    capability can never displace a successor owner), and stores the
    committed result tuple so the same ARMED→REDEEMED→EXPIRED bounded
    replay that recovers a lost bootstrap response also recovers a
    lost transfer response (design §2.4, case 3).
    """

    sCapability: str
    sState: str            # ARMED | REDEEMED | EXPIRED
    fMintedMonotonic: float
    sOperation: str = S_CAPABILITY_OPERATION_BOOTSTRAP
    sContainerName: str = ""
    iExpectedOwnerGeneration: int = 0
    sIssuedCredential: str = ""
    sIssuedSessionId: str = ""
    sIssuedLease: str = ""
    iIssuedOwnerGeneration: int = 0


@dataclass
class BrowserSessionRecord:
    """One browser session's identity and its bearer credential."""

    sSessionId: str
    sCredential: str
    fCreatedMonotonic: float
    fLastSeenMonotonic: float
    sState: str = S_SESSION_STATE_ACTIVE


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
        if recordCap.sOperation != S_CAPABILITY_OPERATION_BOOTSTRAP:
            # A transfer capability must commit the transfer
            # transaction (sessionLifecycle.ftTransferOwnership); the
            # plain bootstrap lane may never redeem it into a bare
            # credential with the ownership commit skipped.
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
    sSessionId, sCredential = _tCreateSessionRecordLocked(dictStore, fNow)
    recordCap.sState = "REDEEMED"
    recordCap.sIssuedSessionId = sSessionId
    recordCap.sIssuedCredential = sCredential
    return (sSessionId, sCredential)


def _tCreateSessionRecordLocked(dictStore, fNow):
    """Create and store a fresh session record. Caller holds the lock."""
    sSessionId = secrets.token_urlsafe(16)
    sCredential = secrets.token_urlsafe(32)
    dictStore["dictSessionsByCredential"][sCredential] = BrowserSessionRecord(
        sSessionId=sSessionId,
        sCredential=sCredential,
        fCreatedMonotonic=fNow,
        fLastSeenMonotonic=fNow,
    )
    return (sSessionId, sCredential)


# ---------------------------------------------------------------------
# The transfer capability lane (design §2.4, slice 5). Minted only over
# the host control socket; redeemed by sessionLifecycle.ftTransferOwnership.
# ---------------------------------------------------------------------

def fsMintTransferCapability(
    dictStore, sContainerName, iExpectedOwnerGeneration,
):
    """Mint an ARMED transfer capability bound to a container+generation."""
    sCapability = secrets.token_urlsafe(32)
    with _lockBrowserSessions:
        dictStore["dictCapabilities"][sCapability] = BootstrapCapability(
            sCapability=sCapability,
            sState="ARMED",
            fMintedMonotonic=time.monotonic(),
            sOperation=S_CAPABILITY_OPERATION_TRANSFER,
            sContainerName=sContainerName,
            iExpectedOwnerGeneration=iExpectedOwnerGeneration,
        )
    return sCapability


def fsCapabilityOperationKind(dictStore, sCapability):
    """Return a known capability's operation kind, or '' when unknown."""
    with _lockBrowserSessions:
        recordCap = dictStore.get("dictCapabilities", {}).get(sCapability)
        return recordCap.sOperation if recordCap is not None else ""


def fdictInspectTransferCapability(dictStore, sCapability):
    """Return a transfer capability's live view, or None when unknown.

    Applies the TTL on inspection exactly as :func:`ftRedeemCapability`
    does on redemption: a capability past its window is marked EXPIRED
    whatever its state, so a stale REDEEMED result is never replayed
    forever. ``dictStoredResult`` carries the committed transfer tuple
    for a REDEEMED capability still inside the replay window.
    """
    with _lockBrowserSessions:
        recordCap = dictStore.get("dictCapabilities", {}).get(sCapability)
        if recordCap is None or (
            recordCap.sOperation != S_CAPABILITY_OPERATION_TRANSFER
        ):
            return None
        fRemainingTtl = I_CAPABILITY_TTL_SECONDS - (
            time.monotonic() - recordCap.fMintedMonotonic
        )
        if fRemainingTtl <= 0:
            recordCap.sState = "EXPIRED"
        return {
            "sState": recordCap.sState,
            "fRemainingTtlSeconds": max(0.0, fRemainingTtl),
            "sContainerName": recordCap.sContainerName,
            "iExpectedOwnerGeneration": recordCap.iExpectedOwnerGeneration,
            "dictStoredResult": _fdictStoredTransferResult(recordCap),
        }


def _fdictStoredTransferResult(recordCap):
    """Return the stored transfer tuple, or None while not REDEEMED."""
    if recordCap.sState != "REDEEMED":
        return None
    return {
        "sSessionId": recordCap.sIssuedSessionId,
        "sCredential": recordCap.sIssuedCredential,
        "sLeaseId": recordCap.sIssuedLease,
        "iOwnerGeneration": recordCap.iIssuedOwnerGeneration,
    }


def fnExpireCapability(dictStore, sCapability):
    """Mark a capability EXPIRED so it can never be redeemed or replayed."""
    with _lockBrowserSessions:
        recordCap = dictStore.get("dictCapabilities", {}).get(sCapability)
        if recordCap is not None:
            recordCap.sState = "EXPIRED"


def fnExpireCapabilitiesForSession(dictStore, sSessionId):
    """Expire every capability issued to one browser session.

    The orphan transition's step (d) (design §5): a session whose
    credential is revoked must not remain recoverable through the
    bounded replay of the capability that minted it. Tickets and
    download capabilities will join this cancellation when their
    mechanism slices land; today the store holds only bootstrap and
    transfer capabilities.
    """
    if not sSessionId:
        return
    with _lockBrowserSessions:
        for recordCap in dictStore.get("dictCapabilities", {}).values():
            if recordCap.sIssuedSessionId == sSessionId:
                recordCap.sState = "EXPIRED"


def ftMintDetachedSessionRecord(dictStore):
    """Mint a session record bound to no capability yet (transfer pre-mint).

    The transfer transaction pre-mints everything reversible before it
    fences terminals (design §6.1); the session becomes reachable only
    when :func:`fnStoreTransferResult` binds it to the capability at
    the commit point, and :func:`fnDiscardSessionRecord` rolls it back
    on any pre-commit refusal.
    """
    with _lockBrowserSessions:
        return _tCreateSessionRecordLocked(dictStore, time.monotonic())


def fnDiscardSessionRecord(dictStore, sCredential):
    """Remove a pre-minted, never-issued session record (rollback)."""
    with _lockBrowserSessions:
        dictStore.get("dictSessionsByCredential", {}).pop(sCredential, None)


def fnRevokeSessionById(dictStore, sSessionId):
    """Revoke every ACTIVE record of a session id; return True if any."""
    if not sSessionId:
        return False
    bRevokedAny = False
    with _lockBrowserSessions:
        for recordSession in dictStore.get(
            "dictSessionsByCredential", {},
        ).values():
            if recordSession.sSessionId == sSessionId and (
                recordSession.sState == S_SESSION_STATE_ACTIVE
            ):
                recordSession.sState = S_SESSION_STATE_REVOKED
                bRevokedAny = True
    return bRevokedAny


def fnStoreTransferResult(
    dictStore, sCapability, sSessionId, sCredential, sLeaseId,
    iOwnerGeneration,
):
    """Mark a transfer capability REDEEMED with its stored result tuple.

    Part of the transfer's synchronous commit (design §6.1): after this
    the bounded replay returns exactly this tuple, so a lost response
    is recoverable without a second transfer.
    """
    with _lockBrowserSessions:
        recordCap = dictStore.get("dictCapabilities", {}).get(sCapability)
        if recordCap is None:
            return
        recordCap.sState = "REDEEMED"
        recordCap.sIssuedSessionId = sSessionId
        recordCap.sIssuedCredential = sCredential
        recordCap.sIssuedLease = sLeaseId
        recordCap.iIssuedOwnerGeneration = iOwnerGeneration


def fbValidateCredential(dictStore, sCredential):
    """Return True when the credential names a live, ACTIVE browser session.

    A REVOKED record authorizes nothing and its last-seen stamp is left
    untouched — a revoked browser presenting its old credential must not
    look recently active. Refreshes the ACTIVE session's last-seen stamp
    (the sliding-window input the expiry lifecycle will consume in a
    later slice).
    """
    if not sCredential:
        return False
    with _lockBrowserSessions:
        recordSession = dictStore.get(
            "dictSessionsByCredential", {},
        ).get(sCredential)
        if recordSession is None:
            return False
        if recordSession.sState != S_SESSION_STATE_ACTIVE:
            return False
        recordSession.fLastSeenMonotonic = time.monotonic()
        return True


def fsSessionIdForCredential(dictStore, sCredential):
    """Return the browser-session id for a credential, or '' if unknown.

    Tolerates an empty or malformed store (missing sub-key) by returning
    '', so a caller that supplies a placeholder store never raises.
    """
    with _lockBrowserSessions:
        recordSession = dictStore.get(
            "dictSessionsByCredential", {},
        ).get(sCredential)
        return recordSession.sSessionId if recordSession else ""
