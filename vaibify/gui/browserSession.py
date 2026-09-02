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

import datetime
import logging
import secrets
import threading
import time
from dataclasses import dataclass

logger = logging.getLogger("vaibify")

__all__ = [
    "BootstrapCapability",
    "BrowserSessionRecord",
    "S_SESSION_STATE_ACTIVE",
    "S_SESSION_STATE_REVOKED",
    "S_CAPABILITY_OPERATION_BOOTSTRAP",
    "S_CAPABILITY_OPERATION_TRANSFER",
    "fdictCreateBrowserSessionStore",
    "fbSessionIsRemote",
    "fsMintBootstrapCapability",
    "fsMintTransferCapability",
    "fsCapabilityOperationKind",
    "ftRedeemCapability",
    "fdictInspectTransferCapability",
    "fnExpireCapability",
    "fnExpireCapabilitiesForSession",
    "ftMintDetachedSessionRecord",
    "fnDiscardSessionRecord",
    "fbRevokeSessionById",
    "fdictEndingNoticeForCredential",
    "fnStoreTransferResult",
    "fbValidateCredential",
    "fsSessionIdForCredential",
    "fdictActiveSessionLifetimes",
    "fdictLifetimeForCredential",
    "I_CAPABILITY_TTL_SECONDS",
    "I_ACTIVE_SESSION_CAP",
    "I_ARMED_CAPABILITY_CAP",
    "F_REVOKED_RETENTION_SECONDS",
]

# Bounded replay window: re-presenting a redeemed capability within this
# many seconds returns the same credential, so a bootstrap response
# dropped in transit is recoverable without minting a second session.
I_CAPABILITY_TTL_SECONDS = 300

# The store is bounded, and bounded in the one direction that is safe.
# A sweep removes ONLY records that already authorize nothing -- expired
# capabilities and revoked sessions past their retention -- so no live
# principal is ever evicted to make room. Evicting an ACTIVE session
# would log a working researcher out mid-run to satisfy a counter,
# which is a worse outcome than refusing a NEW session: the refusal is
# visible, recoverable, and affects somebody who has not started yet.
I_ACTIVE_SESSION_CAP = 64
I_ARMED_CAPABILITY_CAP = 64

# How long a REVOKED session record is kept after revocation. Not zero,
# because the record IS the audit trail of a session that was cut, and a
# tab that returns after a revocation should meet a record that says so
# rather than a hole.
#
# A day, not an hour, because the record is also the ONLY memory of why
# a credential stopped working, and the case that motivates the notice
# is a cap started in the afternoon firing in the small hours: an
# hour's retention is swept clean long before the researcher wakes up,
# and they meet a bare "Unauthorized" for an event the hub could have
# explained. A REVOKED record authorizes nothing, so retaining it
# longer widens no capability; the ACTIVE cap is what bounds the store.
F_REVOKED_RETENTION_SECONDS = 86400.0

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
    # Minted by a process serving a browser on ANOTHER machine, over an
    # SSH tunnel. Carried on the capability rather than guessed at the
    # hub, because the only process that knows is the one that minted
    # it: through the tunnel a remote browser is an ordinary loopback
    # client and is indistinguishable from a local one, which is
    # exactly the property that keeps the security model unchanged.
    bRemoteSession: bool = False
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
    bRemoteSession: bool = False
    # Why this session ended, and when by the wall clock. Written once,
    # at revocation. The wall clock is deliberate: every other stamp on
    # this record is monotonic (correct for measuring windows, useless
    # for telling a researcher what time it happened), and "your session
    # ended at 05:28" is the whole content of the notice.
    sEndedMessage: str = ""
    sEndedWallClockIso: str = ""


def fdictCreateBrowserSessionStore():
    """Return an empty browser-session store for ``app.state``."""
    return {
        "dictCapabilities": {},          # sCapability -> BootstrapCapability
        "dictSessionsByCredential": {},  # sCredential -> BrowserSessionRecord
    }


def fsMintBootstrapCapability(dictStore, bRemoteSession=False):
    """Mint an unguessable ARMED capability, or ``""`` when at capacity.

    The sweep runs first, so the cap is measured against records that
    are actually live rather than against accumulated debris. A refusal
    returns ``""`` -- the caller must report it, never launch a browser
    at a URL carrying an empty capability.
    """
    with _lockBrowserSessions:
        _fnSweepDeadRecordsLocked(dictStore)
        if _fiCountArmedCapabilitiesLocked(dictStore) >= (
            I_ARMED_CAPABILITY_CAP
        ):
            logger.warning(
                "Refusing a new launch capability: %d are already "
                "outstanding. Redeem or wait for one to expire.",
                I_ARMED_CAPABILITY_CAP,
            )
            return ""
        sCapability = secrets.token_urlsafe(32)
        dictStore["dictCapabilities"][sCapability] = BootstrapCapability(
            sCapability=sCapability,
            sState="ARMED",
            fMintedMonotonic=time.monotonic(),
            bRemoteSession=bool(bRemoteSession),
        )
    return sCapability


def _fnSweepDeadRecordsLocked(dictStore):
    """Drop records that already authorize nothing. Caller holds the lock.

    Two classes, and only these two: a capability past its replay TTL
    (it can never be redeemed again) and a REVOKED session past its
    retention. An ACTIVE session is never touched no matter how many
    there are -- the store is bounded by refusing new issuance, not by
    evicting live principals.
    """
    fNow = time.monotonic()
    dictCapabilities = dictStore["dictCapabilities"]
    for sCapability in list(dictCapabilities):
        if fNow - dictCapabilities[sCapability].fMintedMonotonic > (
            I_CAPABILITY_TTL_SECONDS
        ):
            dictCapabilities.pop(sCapability, None)
    dictSessions = dictStore["dictSessionsByCredential"]
    for sCredential in list(dictSessions):
        recordSession = dictSessions[sCredential]
        if recordSession.sState != S_SESSION_STATE_REVOKED:
            continue
        if fNow - recordSession.fLastSeenMonotonic > (
            F_REVOKED_RETENTION_SECONDS
        ):
            dictSessions.pop(sCredential, None)


def _fiCountArmedCapabilitiesLocked(dictStore):
    """Count capabilities that could still be redeemed."""
    return sum(
        1 for recordCap in dictStore["dictCapabilities"].values()
        if recordCap.sState == "ARMED"
    )


def _fiCountActiveSessionsLocked(dictStore):
    """Count sessions whose credential still authorizes."""
    return sum(
        1 for recordSession in dictStore["dictSessionsByCredential"].values()
        if recordSession.sState == S_SESSION_STATE_ACTIVE
    )


def fbSessionIsRemote(dictStore, sSessionId):
    """Return True when this browser session arrived over a tunnel.

    Answered from the session record rather than from the request,
    because through the tunnel a remote browser IS an ordinary
    loopback client -- which is the property that keeps Host, Origin
    and credential checks unweakened, and the reason the fact has to
    be carried rather than detected.
    """
    with _lockBrowserSessions:
        for recordSession in dictStore.get(
            "dictSessionsByCredential", {},
        ).values():
            if recordSession.sSessionId == sSessionId:
                return bool(recordSession.bRemoteSession)
    return False


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
        return _ftMintSessionForCapability(dictStore, recordCap, fNow)


def _ftMintSessionForCapability(dictStore, recordCap, fNow):
    """Mint the session for a first redemption. Caller holds the lock.

    A refused mint leaves the capability ARMED: the researcher can
    redeem it once a session frees up, which is the whole point of
    refusing rather than evicting.
    """
    sSessionId, sCredential = _ftCreateSessionRecordLocked(
        dictStore, fNow, recordCap.bRemoteSession,
    )
    if sSessionId is None:
        return (None, None)
    recordCap.sState = "REDEEMED"
    recordCap.sIssuedSessionId = sSessionId
    recordCap.sIssuedCredential = sCredential
    return (sSessionId, sCredential)


def _ftCreateSessionRecordLocked(dictStore, fNow, bRemoteSession=False):
    """Create and store a fresh session record, or refuse at the cap.

    Returns ``(None, None)`` when the hub already holds
    :data:`I_ACTIVE_SESSION_CAP` active sessions. Refusing is the safe
    direction: the alternative is evicting somebody's live session to
    make room, which logs a working researcher out to satisfy a counter.
    """
    _fnSweepDeadRecordsLocked(dictStore)
    if _fiCountActiveSessionsLocked(dictStore) >= I_ACTIVE_SESSION_CAP:
        logger.warning(
            "Refusing a new browser session: %d are already active.",
            I_ACTIVE_SESSION_CAP,
        )
        return (None, None)
    sSessionId = secrets.token_urlsafe(16)
    sCredential = secrets.token_urlsafe(32)
    dictStore["dictSessionsByCredential"][sCredential] = BrowserSessionRecord(
        sSessionId=sSessionId,
        sCredential=sCredential,
        fCreatedMonotonic=fNow,
        fLastSeenMonotonic=fNow,
        bRemoteSession=bool(bRemoteSession),
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
        return _ftCreateSessionRecordLocked(dictStore, time.monotonic())


def fnDiscardSessionRecord(dictStore, sCredential):
    """Remove a pre-minted, never-issued session record (rollback)."""
    with _lockBrowserSessions:
        dictStore.get("dictSessionsByCredential", {}).pop(sCredential, None)


def fbRevokeSessionById(dictStore, sSessionId, sEndedMessage=""):
    """Revoke every ACTIVE record of a session id; return True if any.

    ``sEndedMessage`` is the sentence a returning browser is shown in
    place of a bare "Unauthorized". It is composed by the caller, which
    is the only party that knows WHY it is revoking, and stored rather
    than a code so the explanation and the event cannot drift apart in
    a frontend lookup table.
    """
    if not sSessionId:
        return False
    sEndedWallClockIso = datetime.datetime.now().astimezone().isoformat()
    bRevokedAny = False
    with _lockBrowserSessions:
        for recordSession in dictStore.get(
            "dictSessionsByCredential", {},
        ).values():
            if recordSession.sSessionId == sSessionId and (
                recordSession.sState == S_SESSION_STATE_ACTIVE
            ):
                recordSession.sState = S_SESSION_STATE_REVOKED
                recordSession.sEndedMessage = sEndedMessage
                recordSession.sEndedWallClockIso = sEndedWallClockIso
                bRevokedAny = True
    return bRevokedAny


def fdictEndingNoticeForCredential(dictStore, sCredential):
    """Return why a revoked credential stopped working, or None.

    The post-hoc half of the session-expiry story. The pre-expiry
    warning assumes an audience: a cap that starts in the afternoon
    fires in the small hours, and a countdown shown to an empty room
    mitigates nothing. This is what the hub can still say afterwards,
    and it is answered for the credential the request PRESENTS — never
    for another session — so it discloses nothing a caller did not
    already hold.

    ``None`` for an unknown, still-ACTIVE, or already-swept credential:
    the hub says what it knows and invents nothing for what it does not.
    """
    if not sCredential:
        return None
    with _lockBrowserSessions:
        recordSession = dictStore.get(
            "dictSessionsByCredential", {},
        ).get(sCredential)
        if recordSession is None or (
            recordSession.sState != S_SESSION_STATE_REVOKED
        ):
            return None
        if not recordSession.sEndedMessage:
            return None
        return {
            "sEndedMessage": recordSession.sEndedMessage,
            "sEndedWallClockIso": recordSession.sEndedWallClockIso,
        }


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


def fdictActiveSessionLifetimes(dictStore):
    """Return ``{sSessionId: {fIdleSeconds, fAgeSeconds}}`` for ACTIVE records.

    The lifecycle evaluator's read of the two expiry clocks (design
    §11), taken under the store lock so a concurrent mint or revocation
    is never observed half-applied. Policy — the windows, the live-socket
    veto, and what an expiry commits — stays in ``sessionLifecycle``;
    this function only reports the clocks.

    A session id carrying several credential records (a replayed
    bootstrap re-issues the SAME credential, so this is rare) is as
    recently seen as its most recent record and as old as its earliest
    one: the reading that never expires a session some live credential
    is still refreshing.
    """
    fNow = time.monotonic()
    dictLifetimes = {}
    with _lockBrowserSessions:
        for recordSession in dictStore.get(
            "dictSessionsByCredential", {},
        ).values():
            if recordSession.sState != S_SESSION_STATE_ACTIVE:
                continue
            dictExisting = dictLifetimes.get(recordSession.sSessionId)
            dictLifetime = {
                "fIdleSeconds": fNow - recordSession.fLastSeenMonotonic,
                "fAgeSeconds": fNow - recordSession.fCreatedMonotonic,
            }
            if dictExisting is not None:
                dictLifetime["fIdleSeconds"] = min(
                    dictExisting["fIdleSeconds"],
                    dictLifetime["fIdleSeconds"],
                )
                dictLifetime["fAgeSeconds"] = max(
                    dictExisting["fAgeSeconds"], dictLifetime["fAgeSeconds"],
                )
            dictLifetimes[recordSession.sSessionId] = dictLifetime
    return dictLifetimes


def fdictLifetimeForCredential(dictStore, sCredential):
    """Return one credential's ``{fIdleSeconds, fAgeSeconds}``, or None.

    The read behind the pre-expiry warning: a session may only ever be
    told about ITS OWN clocks, so this resolves the presenting
    credential rather than a session id supplied by the caller. A
    REVOKED or unknown credential answers None, and the last-seen stamp
    is deliberately NOT refreshed here — the middleware already did
    that for an authorized request, and a read of remaining lifetime
    must not itself extend the lifetime.
    """
    if not sCredential:
        return None
    fNow = time.monotonic()
    with _lockBrowserSessions:
        recordSession = dictStore.get(
            "dictSessionsByCredential", {},
        ).get(sCredential)
        if recordSession is None or (
            recordSession.sState != S_SESSION_STATE_ACTIVE
        ):
            return None
        return {
            "fIdleSeconds": fNow - recordSession.fLastSeenMonotonic,
            "fAgeSeconds": fNow - recordSession.fCreatedMonotonic,
        }


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
