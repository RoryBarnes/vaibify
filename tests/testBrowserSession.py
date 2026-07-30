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
