"""A remote session gets its own hold window, end to end.

Through the tunnel a remote browser IS an ordinary loopback client.
That is the property that keeps Host, Origin, credential and lease
checks completely unweakened -- and it is exactly why the hub cannot
DETECT a remote session and has to be TOLD. The fact rides the
capability, minted by the one process that knows.

Getting this wrong is not cosmetic. The remote client retries a
dropped tunnel for fifteen minutes; a hub that held the session for
fifteen seconds would refuse every attempt after the first few, and
the refusal would surface to the researcher as a dead server. That is
the exact mismatch this branch fixed for the local lane, and shipping
the remote lane without this would reintroduce it one lane over.

The pair is symmetric on purpose: "remote sessions get 900s" is
equally satisfied by giving EVERY session 900s, which would quietly
multiply the local lane's credential lifetime by sixty.
"""

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from vaibify.config import containerLock
from vaibify.gui import browserSession, pipelineServer, sessionLifecycle
from tests.testAgentLaneEnforcement import (
    MockDockerConnection,
    S_CONTAINER_ID,
    S_CONTAINER_NAME,
)


@pytest.fixture(autouse=True)
def fixtureIsolateLockDir(tmp_path, monkeypatch):
    monkeypatch.setattr(
        containerLock, "_S_LOCK_DIRECTORY", str(tmp_path),
    )


@pytest.fixture
def appHub():
    with patch.object(
        pipelineServer, "_fconnectionCreateDocker",
        MockDockerConnection,
    ):
        return pipelineServer.fappCreateHubApplication(iExpectedPort=0)


def _fsCredentialForLane(appHub, bRemoteSession):
    """Bootstrap a credential on the given lane, as the hub would."""
    dictStore = appHub.state.dictBrowserSessions
    sCapability = browserSession.fsMintBootstrapCapability(
        dictStore, bRemoteSession,
    )
    with TestClient(appHub) as clientPlain:
        responseBootstrap = clientPlain.post(
            "/api/bootstrap", json={"sCapability": sCapability},
        )
    assert responseBootstrap.status_code == 200, responseBootstrap.text
    return responseBootstrap.json()["sCredential"]


def _ffWindowReportedToTheBrowser(appHub, sCredential):
    """Return the hold window the connect handshake actually sends."""
    clientBrowser = TestClient(
        appHub, headers={"X-Session-Token": sCredential},
    )
    responseClaim = clientBrowser.post(
        f"/api/registry/{S_CONTAINER_NAME}/claim",
    )
    assert responseClaim.status_code == 200, responseClaim.text
    responseConnect = clientBrowser.post(
        f"/api/connect/{S_CONTAINER_ID}",
        headers={"X-Vaibify-Lease": responseClaim.json()["sLeaseId"]},
    )
    assert responseConnect.status_code == 200, responseConnect.text
    return responseConnect.json()["fReconnectWindowSeconds"]


def test_a_remote_session_is_told_the_remote_window(appHub):
    """The whole chain: minted remote, redeemed, reported at connect."""
    sCredential = _fsCredentialForLane(appHub, bRemoteSession=True)
    fWindow = _ffWindowReportedToTheBrowser(appHub, sCredential)
    assert fWindow == sessionLifecycle.F_REMOTE_RECONNECT_WINDOW_SECONDS
    assert fWindow > sessionLifecycle.F_RECONNECT_WINDOW_SECONDS


def test_a_local_session_is_still_told_the_local_window(appHub):
    """The symmetric half, and it is not a formality.

    Widening every session would satisfy the test above and multiply
    the local lane's credential lifetime by sixty.
    """
    sCredential = _fsCredentialForLane(appHub, bRemoteSession=False)
    fWindow = _ffWindowReportedToTheBrowser(appHub, sCredential)
    assert fWindow == sessionLifecycle.F_RECONNECT_WINDOW_SECONDS


def test_the_flag_does_not_leak_into_an_unrelated_session(appHub):
    """One remote session must not make the next one remote too.

    The store is process-wide, so a flag written anywhere other than
    the individual record would leak across sessions -- invisibly,
    because both sessions still work.
    """
    _fsCredentialForLane(appHub, bRemoteSession=True)
    sCredentialLocal = _fsCredentialForLane(appHub, bRemoteSession=False)
    assert _ffWindowReportedToTheBrowser(appHub, sCredentialLocal) == (
        sessionLifecycle.F_RECONNECT_WINDOW_SECONDS
    )


def test_the_client_and_the_hub_agree_on_the_remote_window():
    """Two numbers in two modules that must not drift apart.

    The client retries for its window; the hub holds for its window.
    If the client's is longer, its tail presents a revoked credential
    -- the defect this whole contract exists to prevent.
    """
    from vaibify.cli.commandRemote import F_RECONNECT_WINDOW_SECONDS
    assert F_RECONNECT_WINDOW_SECONDS <= (
        sessionLifecycle.F_REMOTE_RECONNECT_WINDOW_SECONDS
    ), (
        "the remote client retries for longer than the hub promises to "
        "hold the session; the last attempts would be refused and "
        "reported to the researcher as a dead server"
    )
