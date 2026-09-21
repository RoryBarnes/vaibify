"""The expiry warning is proportional, and the researcher can answer it.

Two defects shipped together. The cap was twelve hours, which bounded a
researcher's working week rather than an unattended tab, and reaching
it destroyed the agent conversations the session held. And the warning
was a fixed fifteen-minute lead: most of a one-hour cap and a rounding
error in a seven-day one, so the same constant meant "plenty of notice"
and "no notice" depending on a number set elsewhere.

These tests pin the replacement: fractional bands resolved on the
server, and an explicit renewal that only a researcher can trigger.
"""

import time
from types import SimpleNamespace

import pytest

from vaibify.gui import browserSession, sessionLifecycle


def _fstateBuildSessionState():
    """Return an app-state stand-in carrying only the session store."""
    return SimpleNamespace(
        dictBrowserSessions=browserSession.fdictCreateBrowserSessionStore(),
    )


def _tMintBrowserSession(stateApp):
    """Redeem a real bootstrap capability; return (sSessionId, sCredential)."""
    sCapability = browserSession.fsMintBootstrapCapability(
        stateApp.dictBrowserSessions,
    )
    return browserSession.ftRedeemCapability(
        stateApp.dictBrowserSessions, sCapability,
    )


def _fnAgeSessionBy(stateApp, sCredential, fSeconds):
    """Rewind a credential's CREATION stamp — the absolute-cap clock."""
    recordSession = stateApp.dictBrowserSessions[
        "dictSessionsByCredential"][sCredential]
    recordSession.fCreatedMonotonic = time.monotonic() - fSeconds


I_SEVEN_DAYS_SECONDS = 7 * 24 * 60 * 60


@pytest.mark.falsification
def testTheDefaultCapOutlivesAWorkingWeek():
    """The shipped default must be at least seven days.

    The oracle is the researcher's own ruling (2026-09-21) and the cost
    it responds to: reaching the cap ends the browser session and with
    it every agent conversation that session held. A cap of hours
    bounds the researcher, not the unattended tab the cap exists for.
    Seven days is the stated floor; longer is a setting.

    Kills: lowering ``sessionLifecycle.F_ABSOLUTE_SESSION_CAP_SECONDS``
    back to an hours-scale default.
    """
    assert sessionLifecycle.F_ABSOLUTE_SESSION_CAP_SECONDS >= (
        I_SEVEN_DAYS_SECONDS
    ), (
        "a default that ends a session inside a working week destroys "
        "agent context the researcher cannot get back"
    )


def testTheBandsAreOrderedFractionsBelowOne():
    """Three ascending fractions, each strictly inside the cap."""
    tBands = sessionLifecycle.T_EXPIRY_WARNING_FRACTIONS
    assert len(tBands) >= 3
    assert list(tBands) == sorted(tBands)
    assert all(0.0 < fBand < 1.0 for fBand in tBands)


@pytest.mark.parametrize("fElapsed,fExpected", [
    (0.0, None), (0.5, None), (0.74, None),
    (0.75, 0.75), (0.89, 0.75), (0.90, 0.90), (0.94, 0.90),
    (0.95, 0.95), (1.0, 0.95),
])
def testTheHighestCrossedBandIsTheOneReported(fElapsed, fExpected):
    """A fraction reports the loudest band it has reached, not the first."""
    assert sessionLifecycle.ffHighestWarningFractionCrossed(
        fElapsed,
    ) == fExpected


def testAZeroCapIsSpentRatherThanDividedBy():
    """A researcher-settable cap of zero must not raise."""
    assert sessionLifecycle.ffElapsedFractionOfCap(10.0, 0.0) == 1.0
    assert sessionLifecycle.ffElapsedFractionOfCap(0.0, 100.0) == 0.0
    assert sessionLifecycle.ffElapsedFractionOfCap(1000.0, 100.0) == 1.0


@pytest.mark.falsification
def testRenewalRestartsTheClockAndOnlyForThePresentingSession():
    """Renewal resets this credential's cap and answers with the new truth.

    The oracle is the cap's own definition: it is measured from when
    the session was minted, so restarting that stamp is exactly what
    "renew" means, and the reported countdown must move with it. A
    renewal that reset nothing would leave the researcher clicking a
    remedy that does nothing while the warning returns a minute later.

    A credential the store does not know renews nothing — past the cap
    is a decision, not a clock to wind back.

    Kills: making ``browserSession.fbRenewSessionLifetime`` a no-op
    that reports success.
    """
    stateApp = _fstateBuildSessionState()
    _, sCredential = _tMintBrowserSession(stateApp)
    fCap = sessionLifecycle.F_ABSOLUTE_SESSION_CAP_SECONDS
    _fnAgeSessionBy(stateApp, sCredential, fCap * 0.96)
    dictWarned = sessionLifecycle.fdictSessionExpiryView(
        stateApp, sCredential,
    )
    assert dictWarned["bExpiringSoon"] is True

    dictRenewed = sessionLifecycle.fdictRenewSessionExpiry(
        stateApp, sCredential,
    )
    assert dictRenewed["bRenewed"] is True
    assert dictRenewed["bExpiringSoon"] is False
    assert dictRenewed["fSecondsUntilSessionCap"] > (
        dictWarned["fSecondsUntilSessionCap"]
    )
    assert dictRenewed["fElapsedFraction"] == pytest.approx(0.0, abs=1e-3)

    dictUnknown = sessionLifecycle.fdictRenewSessionExpiry(
        stateApp, "not-a-credential",
    )
    assert dictUnknown["bRenewed"] is False
    assert dictUnknown["bSessionKnown"] is False


def testARevokedSessionCannotBeRenewedBackToLife():
    """Renewal resurrects nothing: a revoked credential stays revoked."""
    stateApp = _fstateBuildSessionState()
    sSessionId, sCredential = _tMintBrowserSession(stateApp)
    browserSession.fbRevokeSessionById(
        stateApp.dictBrowserSessions, sSessionId,
    )
    assert browserSession.fbRenewSessionLifetime(
        stateApp.dictBrowserSessions, sCredential,
    ) is False
    assert browserSession.fbValidateCredential(
        stateApp.dictBrowserSessions, sCredential,
    ) is False


def _appBuildRealApplication():
    """Build the real application over the fail-closed Docker mock."""
    from unittest.mock import patch
    from tests.testAgentLaneEnforcement import MockDockerConnection
    from vaibify.gui import pipelineServer
    with patch.object(
        pipelineServer, "_fconnectionCreateDocker", MockDockerConnection,
    ):
        return pipelineServer.fappCreateApplication(
            sWorkspaceRoot="/workspace", sTerminalUserArg="testuser",
        )


@pytest.mark.falsification
def testTheRenewRouteIsBrowserOnlyAndRenewsThePresenterAlone():
    """The renewal is reachable by a browser and refused to the agent lane.

    The oracle is the cap's purpose: it bounds a tab nobody is
    watching. An in-container agent is not a watcher — it is the thing
    that keeps running when the researcher walks away — so an agent
    that could restart the clock would delete the cap while the setting
    went on claiming one existed.

    Kills: removing the agent-lane refusal from the renew handler in
    ``sessionRoutes``.
    """
    from fastapi.testclient import TestClient
    from vaibify.gui import containerOwnership
    app = _appBuildRealApplication()
    sCapability = browserSession.fsMintBootstrapCapability(
        app.state.dictBrowserSessions,
    )
    _, sCredential = browserSession.ftRedeemCapability(
        app.state.dictBrowserSessions, sCapability,
    )
    clientBrowser = TestClient(app, headers={"X-Session-Token": sCredential})
    responseRenew = clientBrowser.post("/api/session/renew")
    assert responseRenew.status_code == 200
    dictPayload = responseRenew.json()
    assert dictPayload["bRenewed"] is True
    assert dictPayload["bSessionKnown"] is True

    del containerOwnership
    # The HANDLER's own refusal, driven with no middleware in front of
    # it. On the real hub two refusals stack and the middleware answers
    # first, which means an assertion made through the full app passes
    # whether or not the handler refuses at all -- it reports on the
    # middleware. This mounts the session routes bare, so the only
    # thing that can refuse is the thing under test.
    from fastapi import FastAPI
    from vaibify.gui import browserSession as moduleSessions
    from vaibify.gui.routes import sessionRoutes
    appBare = FastAPI()
    appBare.state.dictBrowserSessions = (
        moduleSessions.fdictCreateBrowserSessionStore()
    )
    sessionRoutes.fnRegisterAll(appBare, {})
    clientBare = TestClient(appBare, raise_server_exceptions=False)
    assert clientBare.post("/api/session/renew").status_code == 200, (
        "a browser-origin caller reaches the handler"
    )
    responseAgent = clientBare.post("/api/session/renew", headers={
        "X-Vaibify-Session": "agent-token-for-renewal-tests",
    })
    assert responseAgent.status_code == 403, (
        "the in-container agent must never extend the researcher's "
        "credential, and the handler must be the one to say so"
    )
    assert "no browser session" in responseAgent.json()["detail"]


def testTheDashboardHoldsNoCopyOfTheWarningThresholds():
    """The bands live on the server; the frontend renders what it is told.

    A mirrored predicate in JavaScript is a second authority on a
    question that has one — the failure this repository already shipped
    on the determinism row. The frontend may read
    ``fWarningFraction``; it may not compute it.
    """
    import pathlib
    sScript = pathlib.Path(
        "vaibify/gui/static/scriptApplication.js",
    ).read_text(encoding="utf-8")
    for fBand in sessionLifecycle.T_EXPIRY_WARNING_FRACTIONS:
        assert str(fBand) not in sScript, (
            f"{fBand} is a warning threshold the server owns; the "
            "dashboard must read fWarningFraction instead of holding "
            "its own copy"
        )


def testTheRenewalIsNeverCalledFromAPollingPath():
    """Only an explicit researcher action may restart the cap clock."""
    import pathlib
    sPolling = pathlib.Path(
        "vaibify/gui/static/scriptPolling.js",
    ).read_text(encoding="utf-8")
    assert "/api/session/renew" not in sPolling, (
        "a renewal on a timer deletes the cap while the Settings "
        "control goes on claiming one exists"
    )
