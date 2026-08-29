"""A returning researcher is told what happened, not just refused.

The pre-expiry warning assumes an audience. A twelve-hour cap started
in the afternoon fires in the small hours, so the warning that was
supposed to mitigate it fires at a screen nobody is watching. What the
hub can still do is answer the NEXT request honestly, and the refusal
is the only place that request lands.

Driven over real HTTP through the whole middleware, because the notice
is a property of the 401 the middleware writes — a unit call to
``fdictEndingNoticeForCredential`` would prove only that a dict has
keys in it.
"""

import pytest
from fastapi.testclient import TestClient

from vaibify.gui import browserSession, sessionLifecycle


def _appBuildRealApplication():
    from unittest.mock import patch
    from tests.testAgentLaneEnforcement import MockDockerConnection
    from vaibify.gui import pipelineServer
    with patch.object(
        pipelineServer, "_fconnectionCreateDocker", MockDockerConnection,
    ):
        return pipelineServer.fappCreateApplication(
            sWorkspaceRoot="/workspace", sTerminalUserArg="testuser",
        )


def _ftMintLiveSession(app):
    """Return (sSessionId, sCredential) for one bootstrapped session."""
    sCapability = browserSession.fsMintBootstrapCapability(
        app.state.dictBrowserSessions,
    )
    return browserSession.ftRedeemCapability(
        app.state.dictBrowserSessions, sCapability,
    )


def testARevokedCredentialIsToldWhatEndedItAndWhen():
    app = _appBuildRealApplication()
    sSessionId, sCredential = _ftMintLiveSession(app)
    client = TestClient(app, headers={"X-Session-Token": sCredential})
    assert client.get("/api/session/lifetime").status_code == 200

    browserSession.fbRevokeSessionById(
        app.state.dictBrowserSessions, sSessionId,
        sEndedMessage="This browser session reached its maximum "
                      "lifetime and ended.",
    )

    response = client.get("/api/session/lifetime")
    assert response.status_code == 401
    dictDetail = response.json()["detail"]
    assert "maximum lifetime" in dictDetail["sMessage"]
    assert dictDetail["sEndedWallClockIso"], (
        "the notice must carry a WALL-CLOCK stamp: every other stamp "
        "on the record is monotonic and cannot say what time it was"
    )


def testACredentialTheHubNeverKnewIsStillJustUnauthorized():
    """The notice discloses nothing: it is keyed on what the caller holds.

    A guessed or stale-from-another-hub credential must meet exactly
    the answer it met before, or the refusal becomes an oracle for
    which credentials once existed here.
    """
    app = _appBuildRealApplication()
    client = TestClient(app, headers={"X-Session-Token": "never-issued"})
    response = client.get("/api/session/lifetime")
    assert response.status_code == 401
    assert response.json()["detail"] == "Unauthorized"


def testASessionCappedByTheEvaluatorLeavesAReadableNotice():
    """End to end: the evaluator's own expiry is what writes the notice.

    A revocation the test performed itself would prove the plumbing and
    not the path. This ages a real session past the cap and lets
    ``fnExpireIdleBrowserSessions`` commit it.
    """
    import asyncio
    import time
    app = _appBuildRealApplication()
    sSessionId, sCredential = _ftMintLiveSession(app)
    for recordSession in app.state.dictBrowserSessions[
        "dictSessionsByCredential"
    ].values():
        recordSession.fCreatedMonotonic = (
            time.monotonic()
            - sessionLifecycle.F_ABSOLUTE_SESSION_CAP_SECONDS - 1.0
        )

    asyncio.run(
        sessionLifecycle.fnExpireIdleBrowserSessions(app.state),
    )

    client = TestClient(app, headers={"X-Session-Token": sCredential})
    response = client.get("/api/session/lifetime")
    assert response.status_code == 401
    sMessage = response.json()["detail"]["sMessage"]
    assert "maximum lifetime" in sMessage, sMessage
    assert "vaibify open" in sMessage, (
        "a notice that does not name the recovery is only a nicer "
        "refusal"
    )


def testAnIdleExpiryAndACapExpirySayDifferentThings():
    """The two triggers are not interchangeable to the researcher.

    The cap fires on a session that may have been in use all day and is
    fixed by re-attaching or by raising the cap; sliding idle fires on
    one nobody touched. A single sentence for both would be true of
    neither.
    """
    sCapMessage = sessionLifecycle._fsExpiryEndedMessage(
        {"fAgeSeconds": 43201.0, "fIdleSeconds": 0.0}, 43200.0, "proj",
    )
    sIdleMessage = sessionLifecycle._fsExpiryEndedMessage(
        {"fAgeSeconds": 60.0, "fIdleSeconds": 99999.0}, 43200.0, "proj",
    )
    assert "maximum lifetime" in sCapMessage
    assert "no activity" in sIdleMessage
    assert sCapMessage != sIdleMessage
    for sMessage in (sCapMessage, sIdleMessage):
        assert "'proj'" in sMessage
        assert "kept running" in sMessage, (
            "session expiry orphans; the container and its work are "
            "retained, and the notice must say so"
        )


@pytest.mark.parametrize("sEndedMessage", ["", None])
def testAnEndingWithNothingToSayFallsBackToTheBareRefusal(sEndedMessage):
    """The hub says what it knows and invents nothing for what it does not."""
    dictStore = browserSession.fdictCreateBrowserSessionStore()
    sCapability = browserSession.fsMintBootstrapCapability(dictStore)
    sSessionId, sCredential = browserSession.ftRedeemCapability(
        dictStore, sCapability,
    )
    browserSession.fbRevokeSessionById(
        dictStore, sSessionId, sEndedMessage=sEndedMessage or "",
    )
    assert browserSession.fdictEndingNoticeForCredential(
        dictStore, sCredential,
    ) is None


def testAnActiveCredentialHasNoEndingNotice():
    dictStore = browserSession.fdictCreateBrowserSessionStore()
    sCapability = browserSession.fsMintBootstrapCapability(dictStore)
    _, sCredential = browserSession.ftRedeemCapability(
        dictStore, sCapability,
    )
    assert browserSession.fdictEndingNoticeForCredential(
        dictStore, sCredential,
    ) is None
