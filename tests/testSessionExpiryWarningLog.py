"""After an expiry, the hub log says whether the tab was warned.

A researcher's session reached its cap with the tab open and in use,
and afterwards nothing could say whether the expiry warnings had been
drawn, drawn in a tab nobody was looking at, or never drawn at all. The
log now records the three facts that tell those apart: the read that
first told a session it was in a band, what the page did with the
warning, and, on the expiry line, the highest band the session was ever
told. A renewal names its session and the control that made it.
"""

import asyncio
import logging
import time
from types import SimpleNamespace

import pytest

from vaibify.gui import browserSession, containerOwnership, sessionLifecycle


def _fstateBuildAppState():
    """Return a bare app.state stand-in with every lifecycle store."""
    return SimpleNamespace(
        bReapOwnerships=True,
        dictContainerOwners={},
        dictSessionOwner=containerOwnership.fdictCreateSessionOwnerIndex(),
        dictSessionSockets=(
            containerOwnership.fdictCreateSessionSocketIndex()
        ),
        dictBrowserSessions=browserSession.fdictCreateBrowserSessionStore(),
        dictMutationSupervisors={},
        dictDurableTaskRecords={},
        dictTerminalExecutionRecords={},
    )


def _tMintBrowserSession(stateApp):
    """Redeem a real bootstrap capability; return (sSessionId, sCredential)."""
    sCapability = browserSession.fsMintBootstrapCapability(
        stateApp.dictBrowserSessions,
    )
    return browserSession.ftRedeemCapability(
        stateApp.dictBrowserSessions, sCapability,
    )


def _fnAgeSessionToFraction(stateApp, sCredential, fFraction):
    """Set a credential's cap clock to ``fFraction`` of the cap."""
    recordSession = stateApp.dictBrowserSessions[
        "dictSessionsByCredential"][sCredential]
    recordSession.fCreatedMonotonic = time.monotonic() - (
        sessionLifecycle.F_ABSOLUTE_SESSION_CAP_SECONDS * fFraction
    )


def _flistVaibifyMessages(caplog, sFragment):
    """Return the captured vaibify log messages containing ``sFragment``."""
    return [
        recordLog.getMessage() for recordLog in caplog.records
        if recordLog.name == "vaibify" and sFragment in recordLog.getMessage()
    ]


@pytest.mark.falsification
def testABandIsLoggedOnceAndAgainAfterARenewal(caplog):
    """Each band is logged on the first read that reports it, once.

    The oracle is the poll's cadence: the page reads its lifetime every
    minute, so a line per read would bury the one moment that matters
    under thousands of copies of it. After a renewal the clock restarts,
    the next crossing is a new announcement, and it must be logged
    again, or a second week's warnings would leave no trace.

    Kills: ``fbRaiseReportedWarningFraction`` answering True for every
    band above zero instead of only for a new high.
    """
    caplog.set_level(logging.INFO, logger="vaibify")
    stateApp = _fstateBuildAppState()
    sSessionId, sCredential = _tMintBrowserSession(stateApp)
    _fnAgeSessionToFraction(stateApp, sCredential, 0.76)
    for _ in range(3):
        sessionLifecycle.fdictSessionExpiryView(stateApp, sCredential)
    listBandLines = _flistVaibifyMessages(caplog, "reached its 75% expiry")
    assert len(listBandLines) == 1, listBandLines
    assert sSessionId[:8] in listBandLines[0]

    sessionLifecycle.fdictRenewSessionExpiry(
        stateApp, sCredential, "warning",
    )
    _fnAgeSessionToFraction(stateApp, sCredential, 0.76)
    sessionLifecycle.fdictSessionExpiryView(stateApp, sCredential)
    assert len(
        _flistVaibifyMessages(caplog, "reached its 75% expiry")
    ) == 2, "the renewed session's next crossing must be logged again"


@pytest.mark.falsification
def testTheExpiryLineSaysHowFarTheTabWasWarned(caplog):
    """The expiry line names the highest band the session was ever told.

    The oracle is the question a researcher asks first after losing a
    session: was I told? One session expires without any lifetime read
    in a band, and its line must say no warning was reported; another
    is told it reached the final band and then expires, and its line
    must name that band.

    Kills: the expiry line ignoring the band the session was told.
    """
    caplog.set_level(logging.INFO, logger="vaibify")
    stateApp = _fstateBuildAppState()
    sSilentId, sSilentCredential = _tMintBrowserSession(stateApp)
    sWarnedId, sWarnedCredential = _tMintBrowserSession(stateApp)
    _fnAgeSessionToFraction(stateApp, sWarnedCredential, 0.96)
    sessionLifecycle.fdictSessionExpiryView(stateApp, sWarnedCredential)
    for sCredential in (sSilentCredential, sWarnedCredential):
        _fnAgeSessionToFraction(stateApp, sCredential, 1.01)
    asyncio.run(sessionLifecycle.fnExpireIdleBrowserSessions(stateApp))
    listExpiryLines = _flistVaibifyMessages(caplog, " expired (age ")
    dictLineById = {
        sId[:8]: [s for s in listExpiryLines if sId[:8] in s]
        for sId in (sSilentId, sWarnedId)
    }
    assert len(dictLineById[sSilentId[:8]]) == 1, listExpiryLines
    assert len(dictLineById[sWarnedId[:8]]) == 1, listExpiryLines
    assert "no expiry warning was reported" in (
        dictLineById[sSilentId[:8]][0]
    )
    assert "highest expiry warning reported to its dashboard: 95%" in (
        dictLineById[sWarnedId[:8]][0]
    )


def _clientBuildBareSessionRoutes():
    """Mount the session routes alone and mint one browser credential.

    Bare, with no middleware in front of the handlers, so a refusal can
    only come from the handler under test.
    """
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from vaibify.gui.routes import sessionRoutes
    appBare = FastAPI()
    appBare.state.dictBrowserSessions = (
        browserSession.fdictCreateBrowserSessionStore()
    )
    sessionRoutes.fnRegisterAll(appBare, {})
    sSessionId, sCredential = _tMintBrowserSession(appBare.state)
    clientBrowser = TestClient(
        appBare, headers={"X-Session-Token": sCredential},
        raise_server_exceptions=False,
    )
    return clientBrowser, sSessionId


@pytest.mark.falsification
def testTheWarningReportLogsOnlyTheServersOwnBands(caplog):
    """A page's report is logged only in the server's own vocabulary.

    The oracle is what the line is for: it is evidence about a warning
    the server issued. A band the server never issues, or an event it
    does not know, would put a statement in the hub log that nothing on
    the server stands behind, so both are refused and leave no line.
    The in-container agent holds no browser tab and is refused outright.

    Kills: dropping the band check from ``fbIsReportableExpiryWarning``.
    """
    caplog.set_level(logging.INFO, logger="vaibify")
    clientBrowser, sSessionId = _clientBuildBareSessionRoutes()
    dictReport = {
        "sEvent": "displayed", "fWarningFraction": 0.75,
        "bTabVisible": True, "bWindowFocused": False,
    }
    responseLogged = clientBrowser.post(
        "/api/session/expiry-warning", json=dictReport,
    )
    assert responseLogged.status_code == 200, responseLogged.text
    assert responseLogged.json() == {"bLogged": True}
    listLines = _flistVaibifyMessages(caplog, "expiry warning")
    assert listLines == [
        f"SESSION {sSessionId[:8]}: the dashboard displayed its 75% "
        "expiry warning (tab visible: yes; window focused: no)"
    ], listLines

    for dictBadReport in (
        dict(dictReport, fWarningFraction=0.5),
        dict(dictReport, sEvent="rendered"),
    ):
        responseRefused = clientBrowser.post(
            "/api/session/expiry-warning", json=dictBadReport,
        )
        assert responseRefused.status_code == 422, dictBadReport
    responseAgent = clientBrowser.post(
        "/api/session/expiry-warning", json=dictReport,
        headers={"X-Vaibify-Session": "agent-token-for-warning-tests"},
    )
    assert responseAgent.status_code == 403
    assert len(_flistVaibifyMessages(caplog, "expiry warning")) == 1


def testAClosedWarningIsLoggedAsClosedUnrenewed(caplog):
    """Closing a warning is its own line, distinct from drawing one."""
    caplog.set_level(logging.INFO, logger="vaibify")
    clientBrowser, sSessionId = _clientBuildBareSessionRoutes()
    responseClosed = clientBrowser.post("/api/session/expiry-warning", json={
        "sEvent": "closed", "fWarningFraction": 0.95,
        "bTabVisible": True, "bWindowFocused": True,
    })
    assert responseClosed.status_code == 200, responseClosed.text
    assert _flistVaibifyMessages(caplog, "expiry warning") == [
        f"SESSION {sSessionId[:8]}: its 95% expiry warning was closed "
        "without renewing (tab visible: yes; window focused: yes)"
    ]


def testARenewalNamesItsSessionAndTheControlUsed(caplog):
    """The renewal line says whose clock restarted and from where.

    Before, it said only that "a" session was renewed, which left a
    week-old renewal unattributable to any session on a hub serving
    several. A renewal from a page that names no control is logged as
    unnamed rather than guessed.
    """
    caplog.set_level(logging.INFO, logger="vaibify")
    clientBrowser, sSessionId = _clientBuildBareSessionRoutes()
    assert clientBrowser.post(
        "/api/session/renew", json={"sSource": "settings"},
    ).json()["bRenewed"] is True
    assert clientBrowser.post("/api/session/renew").json()["bRenewed"]
    assert _flistVaibifyMessages(caplog, "lifetime renewed") == [
        f"Browser session {sSessionId[:8]} lifetime renewed by the "
        "researcher from the Settings panel.",
        f"Browser session {sSessionId[:8]} lifetime renewed by the "
        "researcher from an unnamed control.",
    ]
