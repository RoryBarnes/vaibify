"""The absolute session cap and the sliding-idle window as settings.

Three tiers, environment first, resolved on every evaluation rather
than once at import — so a change needs no hub restart, and RAISING the
cap rescues a session that has not expired yet. The last property is
the one worth a test: it is the reason the resolution moved out of a
module constant at all.

The autouse fixture in ``conftest`` neutralises the two preference
readers so the researcher's real ``~/.vaibify/preferences.json`` cannot
bend the rest of the suite; every test here patches them back, which is
what makes the preference tier exercisable at all.
"""

import math

import pytest

from vaibify.config import preferencesStore
from vaibify.gui import browserSession, sessionLifecycle


@pytest.fixture
def fnSetPreferences(monkeypatch):
    """Return a setter for the two stored timeout preferences."""

    def fnSet(sCap="", sSlidingIdle=""):
        monkeypatch.setattr(
            preferencesStore, "fsSessionCapPreference", lambda: sCap,
        )
        monkeypatch.setattr(
            preferencesStore, "fsSlidingIdlePreference",
            lambda: sSlidingIdle,
        )

    return fnSet


def testAStoredPreferenceReplacesTheBuiltInDefault(fnSetPreferences):
    fnSetPreferences(sCap="7200", sSlidingIdle="120")
    assert sessionLifecycle.ffResolveSessionCapSeconds() == 7200.0
    assert sessionLifecycle.ffResolveSlidingIdleSeconds() == 120.0


def testTheEnvironmentOverrideBeatsTheStoredPreference(
    fnSetPreferences, monkeypatch,
):
    """The env tier is what the test lanes drive, so it must keep winning."""
    fnSetPreferences(sCap="7200")
    monkeypatch.setenv(
        sessionLifecycle.S_ABSOLUTE_SESSION_CAP_ENV, "99",
    )
    assert sessionLifecycle.ffResolveSessionCapSeconds() == 99.0


def testAnUnsetPreferenceFallsThroughToTheDefault(fnSetPreferences):
    fnSetPreferences()
    assert sessionLifecycle.ffResolveSessionCapSeconds() == (
        sessionLifecycle.F_ABSOLUTE_SESSION_CAP_SECONDS
    )


def testAGarbagePreferenceFallsThroughRatherThanBeingAdopted(
    fnSetPreferences,
):
    """A malformed value must not become the cap.

    Adopting it would be worse than ignoring it: a cap of 0 logs every
    session out immediately, and a NaN comparison is always False, so a
    cap silently stops existing.
    """
    for sGarbage in ("banana", "-1", "nan", ""):
        fnSetPreferences(sCap=sGarbage)
        assert sessionLifecycle.ffResolveSessionCapSeconds() == (
            sessionLifecycle.F_ABSOLUTE_SESSION_CAP_SECONDS
        ), f"{sGarbage!r} must not be adopted as a cap"


def testNeverIsItsOwnNamedChoiceNotAVeryLargeNumber(fnSetPreferences):
    """"never" must resolve to an unreachable window, in those words.

    A 30-day number would outlive every hub process, so it would never
    fire while the dashboard still claimed a bound existed — a control
    reporting a guarantee it does not impose.
    """
    for sToken in sorted(preferencesStore.SET_NEVER_TOKENS):
        fnSetPreferences(sCap=sToken)
        assert math.isinf(sessionLifecycle.ffResolveSessionCapSeconds())


def _fdictLifetime(fAgeSeconds, fIdleSeconds=0.0):
    return {"fAgeSeconds": fAgeSeconds, "fIdleSeconds": fIdleSeconds}


def testRaisingTheCapRescuesASessionThatHasNotExpiredYet(fnSetPreferences):
    """The live-read property, stated as the researcher experiences it.

    A session 3 hours from a 4-hour cap is doomed under an import-time
    constant no matter what anybody sets. Read at evaluation, raising
    the cap makes it survive — with no hub restart, which is the whole
    point of the setting.
    """
    fnSetPreferences(sCap="14400")
    dictLifetime = _fdictLifetime(fAgeSeconds=14300.0)
    assert sessionLifecycle._fbBrowserSessionHasExpired(
        dictLifetime, None,
        sessionLifecycle.ffResolveSessionCapSeconds(),
        sessionLifecycle.ffResolveSlidingIdleSeconds(),
    ) is False

    dictExpired = _fdictLifetime(fAgeSeconds=14500.0)
    assert sessionLifecycle._fbBrowserSessionHasExpired(
        dictExpired, None,
        sessionLifecycle.ffResolveSessionCapSeconds(),
        sessionLifecycle.ffResolveSlidingIdleSeconds(),
    ) is True

    fnSetPreferences(sCap="28800")
    assert sessionLifecycle._fbBrowserSessionHasExpired(
        dictExpired, None,
        sessionLifecycle.ffResolveSessionCapSeconds(),
        sessionLifecycle.ffResolveSlidingIdleSeconds(),
    ) is False, (
        "raising the cap must rescue a session the old cap had passed"
    )


def testNeverMeansNoSessionEverReachesTheCap(fnSetPreferences):
    fnSetPreferences(sCap="never", sSlidingIdle="never")
    assert sessionLifecycle._fbBrowserSessionHasExpired(
        _fdictLifetime(fAgeSeconds=1.0e12, fIdleSeconds=1.0e12), None,
        sessionLifecycle.ffResolveSessionCapSeconds(),
        sessionLifecycle.ffResolveSlidingIdleSeconds(),
    ) is False


class StateAppFake:
    def __init__(self, dictBrowserSessions):
        self.dictBrowserSessions = dictBrowserSessions


def _ftMintSession():
    """Return (store, credential) for one live browser session."""
    dictStore = browserSession.fdictCreateBrowserSessionStore()
    sCapability = browserSession.fsMintBootstrapCapability(dictStore)
    sSessionId, sCredential = browserSession.ftRedeemCapability(
        dictStore, sCapability,
    )
    return dictStore, sSessionId, sCredential


def testTheCountdownReportsNeverRatherThanANonJsonInfinity(
    fnSetPreferences,
):
    """``math.inf`` is not JSON, and a huge finite number is a lie.

    The dashboard would either fail to serialize or count down toward a
    deadline that does not exist; both misreport the hub's own state.
    """
    fnSetPreferences(sCap="never")
    dictStore, _, sCredential = _ftMintSession()
    dictView = sessionLifecycle.fdictSessionExpiryView(
        StateAppFake(dictStore), sCredential,
    )
    assert dictView["bSessionKnown"] is True
    assert dictView["bNeverExpires"] is True
    assert dictView["fSecondsUntilSessionCap"] is None
    assert dictView["bExpiringSoon"] is False
    import json
    json.dumps(dictView)


def testTheCountdownTracksTheSettableCapNotTheBuiltInDefault(
    fnSetPreferences,
):
    fnSetPreferences(sCap="600")
    dictStore, _, sCredential = _ftMintSession()
    dictView = sessionLifecycle.fdictSessionExpiryView(
        StateAppFake(dictStore), sCredential,
    )
    assert dictView["fSecondsUntilSessionCap"] == pytest.approx(
        600.0, abs=5.0,
    )
