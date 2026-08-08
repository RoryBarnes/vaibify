"""Tests for the capability bootstrap and per-browser session store.

The capability replaces the shared-token oracle: it is minted at launch,
carried in the URL fragment, exchanged once for a per-browser credential,
and supports bounded replay so a lost bootstrap response is recoverable.
"""

from vaibify.gui import browserSession


def _fdictStore():
    return browserSession.fdictCreateBrowserSessionStore()


def test_mint_redeem_and_validate_round_trip():
    dictStore = _fdictStore()
    sCapability = browserSession.fsMintBootstrapCapability(dictStore)
    assert sCapability

    sSessionId, sCredential = browserSession.ftRedeemCapability(
        dictStore, sCapability,
    )
    assert sSessionId and sCredential
    assert browserSession.fbValidateCredential(dictStore, sCredential) is True
    assert browserSession.fsSessionIdForCredential(
        dictStore, sCredential,
    ) == sSessionId


def test_redeem_is_bounded_replay_returns_same_credential():
    """A second redemption of the same capability returns the same result.

    A bootstrap response can be lost after the server sends it; retrying
    the exchange must recover the same credential, never mint a second
    session.
    """
    dictStore = _fdictStore()
    sCapability = browserSession.fsMintBootstrapCapability(dictStore)
    tFirst = browserSession.ftRedeemCapability(dictStore, sCapability)
    tSecond = browserSession.ftRedeemCapability(dictStore, sCapability)
    assert tFirst == tSecond
    assert tFirst[1]
    # Only one session was minted.
    assert len(dictStore["dictSessionsByCredential"]) == 1


def test_unknown_capability_yields_nothing():
    dictStore = _fdictStore()
    assert browserSession.ftRedeemCapability(
        dictStore, "never-minted",
    ) == (None, None)


def test_expired_capability_yields_nothing():
    """Past the TTL a capability is EXPIRED and mints no session."""
    dictStore = _fdictStore()
    sCapability = browserSession.fsMintBootstrapCapability(dictStore)
    # Age it past the replay window by rewinding its mint time.
    recordCap = dictStore["dictCapabilities"][sCapability]
    recordCap.fMintedMonotonic -= (
        browserSession.I_CAPABILITY_TTL_SECONDS + 1
    )
    assert browserSession.ftRedeemCapability(
        dictStore, sCapability,
    ) == (None, None)
    assert recordCap.sState == "EXPIRED"
    assert dictStore["dictSessionsByCredential"] == {}


def test_empty_and_unknown_credentials_are_invalid():
    dictStore = _fdictStore()
    assert browserSession.fbValidateCredential(dictStore, "") is False
    assert browserSession.fbValidateCredential(dictStore, "nope") is False
    assert browserSession.fsSessionIdForCredential(dictStore, "nope") == ""


def test_distinct_capabilities_mint_distinct_sessions():
    dictStore = _fdictStore()
    sCapA = browserSession.fsMintBootstrapCapability(dictStore)
    sCapB = browserSession.fsMintBootstrapCapability(dictStore)
    assert sCapA != sCapB
    _, sCredA = browserSession.ftRedeemCapability(dictStore, sCapA)
    _, sCredB = browserSession.ftRedeemCapability(dictStore, sCapB)
    assert sCredA != sCredB
    assert browserSession.fsSessionIdForCredential(dictStore, sCredA) != (
        browserSession.fsSessionIdForCredential(dictStore, sCredB)
    )


def test_credentials_and_capabilities_are_unguessable_length():
    """Minted secrets carry enough entropy to resist guessing."""
    dictStore = _fdictStore()
    sCapability = browserSession.fsMintBootstrapCapability(dictStore)
    _, sCredential = browserSession.ftRedeemCapability(dictStore, sCapability)
    assert len(sCapability) >= 32
    assert len(sCredential) >= 32


# ---------------------------------------------------------------------
# The store is bounded, and bounded in the one direction that is safe.
# ---------------------------------------------------------------------

def test_the_sweep_removes_only_records_that_authorize_nothing():
    """Expired capabilities and retired revoked sessions, and nothing else.

    The store had no pruning at all, so every capability ever minted and
    every session ever revoked stayed for the life of the hub. The sweep
    that fixes that must be surgical: an ACTIVE session is a working
    researcher, and a sweep that reached one would log them out to
    reclaim memory.
    """
    dictStore = browserSession.fdictCreateBrowserSessionStore()
    sCapabilityLive = browserSession.fsMintBootstrapCapability(dictStore)
    _sSessionIdActive, sCredentialActive = browserSession.ftRedeemCapability(
        dictStore, sCapabilityLive,
    )
    sCapabilityStale = browserSession.fsMintBootstrapCapability(dictStore)
    sSessionIdRevoked, sCredentialRevoked = (
        browserSession.ftRedeemCapability(
            dictStore,
            browserSession.fsMintBootstrapCapability(dictStore),
        )
    )
    browserSession.fbRevokeSessionById(dictStore, sSessionIdRevoked)

    # Age the stale capability and the revoked session past their
    # windows by moving their stamps, not by sleeping.
    dictStore["dictCapabilities"][sCapabilityStale].fMintedMonotonic -= (
        browserSession.I_CAPABILITY_TTL_SECONDS + 1
    )
    dictStore["dictSessionsByCredential"][
        sCredentialRevoked
    ].fLastSeenMonotonic -= (
        browserSession.F_REVOKED_RETENTION_SECONDS + 1
    )

    browserSession._fnSweepDeadRecordsLocked(dictStore)

    assert sCapabilityStale not in dictStore["dictCapabilities"]
    assert sCredentialRevoked not in dictStore["dictSessionsByCredential"]
    assert sCredentialActive in dictStore["dictSessionsByCredential"], (
        "the sweep removed an ACTIVE session, logging out a researcher "
        "who was working"
    )
    assert browserSession.fbValidateCredential(dictStore, sCredentialActive)


def test_a_recently_revoked_session_is_kept_as_its_own_audit_trail():
    """Revocation is not deletion; the record says a session was cut."""
    dictStore = browserSession.fdictCreateBrowserSessionStore()
    sSessionId, sCredential = browserSession.ftRedeemCapability(
        dictStore, browserSession.fsMintBootstrapCapability(dictStore),
    )
    browserSession.fbRevokeSessionById(dictStore, sSessionId)
    browserSession._fnSweepDeadRecordsLocked(dictStore)
    assert sCredential in dictStore["dictSessionsByCredential"]
    assert not browserSession.fbValidateCredential(dictStore, sCredential)


def test_the_active_session_cap_refuses_rather_than_evicting():
    """At the cap, a NEW session is refused; no live one is displaced.

    The direction is the whole point. Evicting an active principal to
    admit a new one trades a visible refusal for an invisible logout of
    somebody mid-run, and the researcher who loses their session is not
    the one who asked for anything.
    """
    dictStore = browserSession.fdictCreateBrowserSessionStore()
    listCredentials = []
    for _ in range(browserSession.I_ACTIVE_SESSION_CAP):
        _sSessionId, sCredential = browserSession.ftRedeemCapability(
            dictStore, browserSession.fsMintBootstrapCapability(dictStore),
        )
        listCredentials.append(sCredential)
    assert all(listCredentials)

    sCapabilityOverflow = browserSession.fsMintBootstrapCapability(dictStore)
    tRefused = browserSession.ftRedeemCapability(
        dictStore, sCapabilityOverflow,
    )
    assert tRefused == (None, None), (
        "a session past the cap was minted anyway"
    )
    assert all(
        browserSession.fbValidateCredential(dictStore, sCredential)
        for sCredential in listCredentials
    ), "an existing active session was evicted to admit a new one"
    assert dictStore["dictCapabilities"][sCapabilityOverflow].sState == (
        "ARMED"
    ), (
        "a refused redemption burned the capability, so the researcher "
        "cannot retry once a session frees up"
    )


def test_a_refused_capability_mint_returns_empty_rather_than_a_token():
    """At the capability cap, minting refuses instead of accumulating."""
    dictStore = browserSession.fdictCreateBrowserSessionStore()
    listCapabilities = [
        browserSession.fsMintBootstrapCapability(dictStore)
        for _ in range(browserSession.I_ARMED_CAPABILITY_CAP)
    ]
    assert all(listCapabilities)
    assert browserSession.fsMintBootstrapCapability(dictStore) == ""
    assert len(dictStore["dictCapabilities"]) == (
        browserSession.I_ARMED_CAPABILITY_CAP
    )
