"""A container request the hub refuses says WHY, and logs it once.

Before this, an unowned container and a container held by another
session answered the same bare 403 from ``routeScope``, and neither
left a trace in the hub log. A page whose claim lapsed (the reaper
drops a socketless claim after thirty seconds, and a browser that
throttles an unseen page leaves a longer gap) could not tell "claim it
again" from "someone else has it", so it could not recover. The
refusal for a valid browser session aimed at an unowned container now
carries ``sRefusal: "claim-required"``; every other refusal keeps its
bare body, because that code is an invitation to claim and offering it
to a session that cannot claim would send it to a refusal.

The hub application is real, the container name differs from its
Docker id (the owner map is name-keyed and every URL carries the id),
and the claim is made over HTTP exactly as the browser makes it.
"""

import asyncio
import logging
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from vaibify.config import containerLock, mutationAdmission
from vaibify.gui import containerOwnership
from vaibify.gui import pipelineServer
from vaibify.gui import sessionLifecycle
from tests.sessionTokenTestHelper import fsBootstrapCredential
from tests.testAgentLaneEnforcement import (
    MockDockerConnection,
    S_CONTAINER_ID,
    S_CONTAINER_NAME,
)


S_CONTAINER_SCOPED_URL = f"/api/pipeline/{S_CONTAINER_ID}/state"
S_CLAIM_URL = f"/api/registry/{S_CONTAINER_NAME}/claim"
S_REFUSAL_LOGGER = "vaibify"


@pytest.fixture(autouse=True)
def fixtureIsolateLockDir(tmp_path, monkeypatch):
    """Keep the host flock out of the researcher's real lock directory."""
    monkeypatch.setattr(
        containerLock, "_S_LOCK_DIRECTORY", str(tmp_path),
    )


@pytest.fixture
def appHub():
    """Build the real hub application over a mocked Docker."""
    with patch.object(
        pipelineServer, "_fconnectionCreateDocker",
        MockDockerConnection,
    ):
        return pipelineServer.fappCreateHubApplication(iExpectedPort=0)


@pytest.fixture
def clientBrowser(appHub):
    return TestClient(
        appHub,
        headers={"X-Session-Token": fsBootstrapCredential(appHub)},
    )


@pytest.fixture
def clientSecondBrowser(appHub):
    """A different browser session on the same hub."""
    return TestClient(
        appHub,
        headers={"X-Session-Token": fsBootstrapCredential(appHub)},
    )


def _fsClaimAndReturnLease(clientBrowser):
    responseClaim = clientBrowser.post(S_CLAIM_URL)
    assert responseClaim.status_code == 200, responseClaim.text
    return responseClaim.json()["sLeaseId"]


def _flistRefusalRecords(caplog):
    return [
        recordLog.getMessage() for recordLog in caplog.records
        if recordLog.getMessage().startswith("REFUSED container request")
    ]


@pytest.mark.falsification
def test_an_unowned_container_answers_claim_required(
    appHub, clientBrowser,
):
    """A valid session with no owner record is told to claim, in code.

    Kills: answering the bare refusal on the unowned branch of
    routeScope.fiAuthorizeContainerHttp, which leaves the dashboard
    unable to tell a lapsed claim from someone else's.
    """
    assert not appHub.state.dictContainerOwners
    responseHttp = clientBrowser.get(S_CONTAINER_SCOPED_URL)
    assert responseHttp.status_code == 403, responseHttp.text
    dictDetail = responseHttp.json()["detail"]
    assert dictDetail["sRefusal"] == "claim-required"
    assert "claim" in dictDetail["sMessage"].lower()


@pytest.mark.falsification
def test_a_container_held_by_another_session_stays_bare(
    appHub, clientBrowser, clientSecondBrowser,
):
    """Another session's claim is NOT an invitation to claim.

    Kills: tagging every 403 claim-required, which would send a session
    that cannot claim (the project is held) into a claim it must lose.
    """
    sLeaseId = _fsClaimAndReturnLease(clientBrowser)
    responseHttp = clientSecondBrowser.get(
        S_CONTAINER_SCOPED_URL, headers={"X-Vaibify-Lease": sLeaseId},
    )
    assert responseHttp.status_code == 403, responseHttp.text
    assert "sRefusal" not in str(responseHttp.json()), responseHttp.text


@pytest.mark.falsification
def test_the_viewer_never_offers_a_claim_it_has_no_route_for():
    """The single-container viewer has no claim route: its refusal is bare.

    Kills: offering the cure outside the hub, which sends a viewer's page
    to a route that does not exist.
    """
    with patch.object(
        pipelineServer, "_fconnectionCreateDocker", MockDockerConnection,
    ):
        appViewer = pipelineServer.fappCreateApplication(
            sWorkspaceRoot="/workspace", sTerminalUserArg="testuser",
        )
    clientViewer = TestClient(
        appViewer,
        headers={"X-Session-Token": fsBootstrapCredential(appViewer)},
    )
    responseHttp = clientViewer.get(S_CONTAINER_SCOPED_URL)
    assert responseHttp.status_code == 403, responseHttp.text
    assert "claim-required" not in responseHttp.text


def test_a_request_with_no_credential_stays_bare(appHub):
    """The control: no browser session, no structured refusal."""
    responseHttp = TestClient(appHub).get(S_CONTAINER_SCOPED_URL)
    assert responseHttp.status_code in (401, 403), responseHttp.text
    assert "claim-required" not in responseHttp.text


@pytest.mark.falsification
def test_each_refusal_logs_one_line_that_names_no_secret(
    appHub, clientBrowser, clientSecondBrowser, caplog,
):
    """One log line per refusal; the lease and credential never appear.

    Kills: dropping the log call in routeScope._fiRefuseAndLog, which
    returns the diagnosis of a lapsed claim to "nothing in the log".
    """
    caplog.set_level(logging.INFO, logger=S_REFUSAL_LOGGER)
    clientBrowser.get(S_CONTAINER_SCOPED_URL)
    sLeaseId = _fsClaimAndReturnLease(clientBrowser)
    clientSecondBrowser.get(
        S_CONTAINER_SCOPED_URL, headers={"X-Vaibify-Lease": sLeaseId},
    )
    listRefusals = _flistRefusalRecords(caplog)
    assert len(listRefusals) == 2, listRefusals
    assert "claim-required" in listRefusals[0]
    assert "held-by-another-session" in listRefusals[1]
    for sLine in listRefusals:
        assert repr(S_CONTAINER_ID) in sLine or repr(S_CONTAINER_NAME) in sLine
    sEverything = "\n".join(
        recordLog.getMessage() for recordLog in caplog.records
    )
    sCredential = clientBrowser.headers["X-Session-Token"]
    assert sLeaseId not in sEverything
    assert sCredential not in sEverything
    assert clientSecondBrowser.headers["X-Session-Token"] not in sEverything


def test_the_pipeline_socket_of_a_session_that_loses_it_is_revoked(
    appHub, clientBrowser,
):
    """Pins F4: a lost SOCKET revokes, a lost claim alone does not.

    A real pipeline socket is opened and closed through the served hub,
    then the reconnect window is run out. The session is orphaned and
    its next HTTP request is 401, so only a fresh ``vaibify open``
    recovers it -- which is why leaving the dashboard must release
    BEFORE it drops the socket (the claim-required recovery cannot
    reach a revoked session).
    """
    sCredential = clientBrowser.headers["X-Session-Token"]
    sLeaseId = _fsClaimAndReturnLease(clientBrowser)
    with patch(
        "vaibify.gui.routes.pipelineRoutes.fnHandlePipelineWs",
        _fnAcceptAndHold,
    ):
        with clientBrowser.websocket_connect(
            f"/ws/pipeline/{S_CONTAINER_ID}"
            f"?sToken={sCredential}&sLeaseId={sLeaseId}",
            headers={"origin": "http://localhost"},
        ):
            pass
    recordOwner = appHub.state.dictContainerOwners[S_CONTAINER_NAME]
    assert recordOwner.bSocketEverExisted
    _fnOrphanPastWindow(appHub, recordOwner)
    assert recordOwner.sState == containerOwnership.S_OWNER_STATE_ORPHANED_SESSION
    responseAfter = clientBrowser.get(S_CONTAINER_SCOPED_URL)
    assert responseAfter.status_code == 401, responseAfter.text


async def _fnAcceptAndHold(websocket, dictCtx, sContainerId, **dictUnused):
    await websocket.accept()


def _fnOrphanPastWindow(appHub, recordOwner):
    recordOwner.fLastSeenMonotonic -= (
        sessionLifecycle.F_RECONNECT_WINDOW_SECONDS + 1
    )
    asyncio.run(sessionLifecycle.fnOrphanOwnersPastReconnectWindow(
        appHub.state,
    ))


# ---------------------------------------------------------------------
# A claim lost WHILE a request is in flight.
# ---------------------------------------------------------------------

S_DISCOVERY_POLL_URL = (
    f"/api/pipeline/{S_CONTAINER_ID}/workflow-discovery"
)


class MockDockerConnectionThatLosesTheClaimMidExec(MockDockerConnection):
    """Release the claim between admission and the exec, then gate it.

    The real ``DockerConnection`` primitives call
    ``fnAssertContainerCommandAdmitted`` before every exec; this double
    calls the SAME gate under the same rule, so the commit-time
    revalidation the request's admission carries is the real one. The
    release is the real release, made with the record's own lease and
    session the way the release route makes it, so the owner map, the
    session index and the flock all change exactly as they do live.
    """

    def __init__(self):
        super().__init__()
        self.appHub = None
        self.bReleaseBeforeNextExec = False
        self.errorRaiseAtNextExec = None

    def ftResultExecuteCommand(self, sContainerId, sCommand):
        if self.bReleaseBeforeNextExec:
            self.bReleaseBeforeNextExec = False
            self._fnReleaseTheClaim()
        if self.errorRaiseAtNextExec is not None:
            errorRaise, self.errorRaiseAtNextExec = (
                self.errorRaiseAtNextExec, None,
            )
            raise errorRaise
        mutationAdmission.fnAssertContainerCommandAdmitted(
            sContainerId, "ftResultExecuteCommand",
        )
        return super().ftResultExecuteCommand(sContainerId, sCommand)

    def _fnReleaseTheClaim(self):
        appState = self.appHub.state
        recordOwner = appState.dictContainerOwners[S_CONTAINER_NAME]
        assert containerOwnership.fbReleaseOwnership(
            appState.dictContainerOwners, S_CONTAINER_NAME,
            recordOwner.sLeaseId, recordOwner.sBrowserSessionId,
            dictSessionOwner=appState.dictSessionOwner,
        )


@pytest.fixture
def tClientLosingTheClaim():
    """Return ``(client, docker)`` claimed on a hub whose exec is gated."""
    connectionDocker = MockDockerConnectionThatLosesTheClaimMidExec()
    with patch.object(
        pipelineServer, "_fconnectionCreateDocker",
        lambda: connectionDocker,
    ):
        appHub = pipelineServer.fappCreateHubApplication(iExpectedPort=0)
    connectionDocker.appHub = appHub
    clientBrowser = TestClient(
        appHub,
        headers={"X-Session-Token": fsBootstrapCredential(appHub)},
        raise_server_exceptions=False,
    )
    clientBrowser.headers["X-Vaibify-Lease"] = _fsClaimAndReturnLease(
        clientBrowser,
    )
    return (clientBrowser, connectionDocker)


@pytest.mark.falsification
def test_a_poll_whose_claim_lapses_mid_exec_answers_claim_required(
    tClientLosingTheClaim,
):
    """Losing a claim never surfaces a 500.

    A dashboard poll was admitted while its session held the claim; the
    claim was released before its exec reached the primitive, whose
    commit-time revalidation then refused it. That refusal escaped the
    route and became the last-resort 500, so the page that should have
    re-claimed by itself showed a server failure instead. The poll must
    answer what the same request answers once the claim is gone.

    Kills: removing the lost-claim translation in
    routeScope.ContainerAwareRoute, which lets the stale-admission
    refusal reach the last-resort 500 handler.
    """
    clientBrowser, connectionDocker = tClientLosingTheClaim
    responseHeld = clientBrowser.get(S_DISCOVERY_POLL_URL)
    assert responseHeld.status_code == 200, responseHeld.text
    connectionDocker.bReleaseBeforeNextExec = True
    responseLost = clientBrowser.get(S_DISCOVERY_POLL_URL)
    assert responseLost.status_code == 403, responseLost.text
    dictDetail = responseLost.json()["detail"]
    assert dictDetail["sRefusal"] == "claim-required", dictDetail


@pytest.mark.falsification
def test_a_refusal_while_the_claim_still_holds_stays_loud(
    tClientLosingTheClaim,
):
    """A refusal that is NOT a lost claim is never dressed up as one.

    A quarantined journal refuses the exec while the session still owns
    the container; answering claim-required there would send the page
    into a claim that cannot cure it, and hide the reconcile the
    refusal's own sentence names.

    Kills: answering every control-plane refusal from a container route
    with the authority's refusal, whether or not the claim was lost.
    """
    clientBrowser, connectionDocker = tClientLosingTheClaim
    sQuarantine = "a quarantined record awaits vaibify reconcile"
    connectionDocker.errorRaiseAtNextExec = (
        mutationAdmission.MutationNotAdmittedError(sQuarantine)
    )
    responseHttp = clientBrowser.get(S_DISCOVERY_POLL_URL)
    assert responseHttp.status_code == 500, responseHttp.text
    assert responseHttp.json()["detail"] == sQuarantine
