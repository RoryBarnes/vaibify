"""A hub /api/connect must not admit an unclaimed container.

The ownerless-connect exception exists for the single-container viewer,
which mints its lease inside connect and has no claim route. The hub
must not share that exception: an empty owner record in a hub can mean
another hub process holds the host flock, and connect runs a write path
(it caches the workflow and pushes an agent session — with no owner
record that session token is empty, which clobbers the real owner's
in-container credential). So a hub client must claim first.

These drive the real hub application over HTTP with the container name
distinct from its docker id (the same discipline the WebSocket-gate
tests use), so a regression that keys or gates the wrong way is caught
against a live request, not a stub.
"""

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from vaibify.config import containerLock
from vaibify.gui import pipelineServer
from tests.sessionTokenTestHelper import fsBootstrapCredential
from tests.testAgentLaneEnforcement import (
    MockDockerConnection,
    S_CONTAINER_ID,
    S_CONTAINER_NAME,
)


@pytest.fixture(autouse=True)
def fixtureIsolateLockDir(tmp_path, monkeypatch):
    """Redirect ~/.vaibify/locks/ to a per-test tmp_path.

    The host flock lives on disk and outlives the app fixture, so
    without this a claim in one test holds the lock into the next.
    """
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


@pytest.mark.falsification
def test_hub_refuses_connect_to_an_unclaimed_container(
    appHub, clientBrowser,
):
    """No claim record → the hub connect is refused, not bootstrapped.

    Kills: In workflowRoutes._fnRequireOwningLeaseForConnect, drop the
    ``if dictCtx.get("bIsHub"): raise HTTPException(409, ...)`` branch so
    an ownerless container is left open in the hub too.
    """
    assert not appHub.state.dictContainerOwners
    responseHttp = clientBrowser.post(f"/api/connect/{S_CONTAINER_ID}")
    assert responseHttp.status_code == 409, responseHttp.text
    assert "claim" in responseHttp.text.lower()
    # The refused connect must not have written an owner record or
    # pushed an (empty-token) agent session as a side effect.
    assert not appHub.state.dictContainerOwners


def test_hub_admits_connect_after_a_claim(appHub, clientBrowser):
    """The legitimate flow — claim, then connect — still succeeds."""
    responseClaim = clientBrowser.post(
        f"/api/registry/{S_CONTAINER_NAME}/claim",
    )
    assert responseClaim.status_code == 200, responseClaim.text
    sLeaseId = responseClaim.json()["sLeaseId"]
    responseHttp = clientBrowser.post(
        f"/api/connect/{S_CONTAINER_ID}",
        headers={"X-Vaibify-Lease": sLeaseId},
    )
    assert responseHttp.status_code == 200, responseHttp.text


def test_hub_refuses_connect_bearing_a_foreign_lease(
    appHub, clientBrowser,
):
    """A claim by one session, a connect presenting a different lease."""
    responseClaim = clientBrowser.post(
        f"/api/registry/{S_CONTAINER_NAME}/claim",
    )
    assert responseClaim.status_code == 200
    responseHttp = clientBrowser.post(
        f"/api/connect/{S_CONTAINER_ID}",
        headers={"X-Vaibify-Lease": "not-the-owning-lease"},
    )
    assert responseHttp.status_code == 409, responseHttp.text


@pytest.mark.falsification
def test_release_refuses_second_session_presenting_a_copied_lease(
    appHub, clientBrowser,
):
    """A second browser session cannot release with the owner's copied lease.

    Session A claims the container, binding the owner record to its browser
    session. Session B redeems its OWN valid credential but copies A's genuine
    lease value onto the release request. The lease VALUE is real, yet it is
    bound to A's session, so B's release must return ``bReleased: False`` and
    A's owner record must survive — dropping it would hand A's authorization
    to a tab that never claimed the container. As a positive control, the true
    owner releases successfully.

    Kills: in containerOwnership.fnReleaseOwnership, the session-bound guard
    ``if not (bBoundOwner or bUnboundOwner): return False`` reverted to the
    value-only ``if recordOwner.sLeaseId != sLeaseId: return False``, so a
    copied lease value alone releases another session's container.
    """
    responseClaim = clientBrowser.post(
        f"/api/registry/{S_CONTAINER_NAME}/claim",
    )
    assert responseClaim.status_code == 200, responseClaim.text
    sLeaseId = responseClaim.json()["sLeaseId"]

    clientSessionB = TestClient(
        appHub,
        headers={"X-Session-Token": fsBootstrapCredential(appHub)},
    )
    responseSessionB = clientSessionB.post(
        f"/api/registry/{S_CONTAINER_NAME}/release",
        headers={"X-Vaibify-Lease": sLeaseId},
    )
    assert responseSessionB.status_code == 200, responseSessionB.text
    assert responseSessionB.json()["bReleased"] is False, (
        "a second session with the owner's copied lease must not release "
        "the container"
    )
    assert S_CONTAINER_NAME in appHub.state.dictContainerOwners, (
        "the true owner's record must survive a foreign release attempt"
    )

    responseOwner = clientBrowser.post(
        f"/api/registry/{S_CONTAINER_NAME}/release",
        headers={"X-Vaibify-Lease": sLeaseId},
    )
    assert responseOwner.status_code == 200, responseOwner.text
    assert responseOwner.json()["bReleased"] is True, (
        "the true owner presenting its own lease must release"
    )
    assert S_CONTAINER_NAME not in appHub.state.dictContainerOwners
