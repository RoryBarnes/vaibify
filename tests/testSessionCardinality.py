"""Slice 4 of the ORPHANED_SESSION foundation: one container per session.

Falsification case 19, claim half (design §9 / §13): the cardinality
read-check-write on the ``dictSessionOwner`` reverse index runs on every
existing creation path — the browser claim route (committed through the
``sessionLifecycle`` lock order) and the viewer first-connect creation.
These tests drive the REAL applications over HTTP with genuine browser
credentials, and every container keeps its NAME distinct from its Docker
ID (repo epistemics rule): the owner map is name-keyed, and a fixture
with name == id can green-light an id-keyed lookup bug.
"""

import asyncio
from unittest.mock import patch

import httpx
import pytest
from fastapi.testclient import TestClient

from vaibify.config import containerLock
from vaibify.gui import browserSession, pipelineServer
from tests.sessionTokenTestHelper import fsBootstrapCredential
from tests.testAgentLaneEnforcement import (
    MockDockerConnection,
    S_CONTAINER_ID,
    S_CONTAINER_NAME,
)

S_SECOND_CONTAINER_NAME = "second-container"
S_SECOND_CONTAINER_ID = "def456container"


class MockDockerConnectionTwoContainers(MockDockerConnection):
    """The agent-lane mock, reporting a second running container."""

    def flistGetRunningContainers(self):
        return [
            {
                "sContainerId": S_CONTAINER_ID,
                "sShortId": "abc123",
                "sName": S_CONTAINER_NAME,
                "sImage": "ubuntu:24.04",
            },
            {
                "sContainerId": S_SECOND_CONTAINER_ID,
                "sShortId": "def456",
                "sName": S_SECOND_CONTAINER_NAME,
                "sImage": "ubuntu:24.04",
            },
        ]


@pytest.fixture(autouse=True)
def fixtureIsolateLockDirectory(tmp_path, monkeypatch):
    """Redirect the host flock directory to a per-test tmp_path."""
    monkeypatch.setattr(
        containerLock, "_S_LOCK_DIRECTORY", str(tmp_path),
    )


@pytest.fixture
def appHub():
    """Build the real hub application over a two-container mocked Docker."""
    with patch.object(
        pipelineServer, "_fconnectionCreateDocker",
        MockDockerConnectionTwoContainers,
    ):
        return pipelineServer.fappCreateHubApplication(iExpectedPort=0)


@pytest.fixture
def appViewer():
    """Build the real viewer application over a mocked Docker."""
    with patch.object(
        pipelineServer, "_fconnectionCreateDocker",
        MockDockerConnection,
    ):
        return pipelineServer.fappCreateApplication(
            sWorkspaceRoot="/workspace", sTerminalUserArg="testuser",
        )


def _fsSessionIdOnApp(app, sCredential):
    """Resolve a minted credential to its browser session id."""
    return browserSession.fsSessionIdForCredential(
        app.state.dictBrowserSessions, sCredential,
    )


@pytest.mark.falsification
def test_second_claim_by_the_same_session_is_refused(appHub):
    """One session may hold one container; a second claim is a named 409.

    Session S claims container A (granted), then claims container B: the
    cardinality authority refuses with 409, the refusal names A so the
    message is actionable, A stays owned by S, and B stays unowned.

    Kills: In containerOwnership.ftdictClaim, replace
    ``if sHeldElsewhereName:`` with ``if False:`` so the cardinality
    read-check never refuses and one browser session accumulates two
    owner records.
    """
    sCredential = fsBootstrapCredential(appHub)
    clientBrowser = TestClient(
        appHub, headers={"X-Session-Token": sCredential},
    )
    responseFirst = clientBrowser.post(
        f"/api/registry/{S_CONTAINER_NAME}/claim",
    )
    assert responseFirst.status_code == 200, responseFirst.text
    responseSecond = clientBrowser.post(
        f"/api/registry/{S_SECOND_CONTAINER_NAME}/claim",
    )
    assert responseSecond.status_code == 409, responseSecond.text
    dictDetail = responseSecond.json()["detail"]
    assert dictDetail["sHeldContainerName"] == S_CONTAINER_NAME
    assert S_CONTAINER_NAME in dictDetail["sMessage"]
    assert "sLeaseId" not in dictDetail
    dictOwners = appHub.state.dictContainerOwners
    sSessionId = _fsSessionIdOnApp(appHub, sCredential)
    assert dictOwners[S_CONTAINER_NAME].sBrowserSessionId == sSessionId
    assert S_SECOND_CONTAINER_NAME not in dictOwners
    assert appHub.state.dictSessionOwner == {sSessionId: S_CONTAINER_NAME}


@pytest.mark.asyncio
@pytest.mark.falsification
async def test_concurrent_claims_on_two_containers_resolve_to_one_record(
    appHub,
):
    """A same-session race on two DIFFERENT containers grants exactly one.

    Session S fires claims at containers A and B concurrently
    (``asyncio.gather`` over the real claim route). The two requests take
    two different container-mutation locks but serialize on the single
    hub-wide cardinality lock, so exactly one claim is granted and
    ``dictSessionOwner`` ends with exactly one entry for S — the case-19
    race can never leave one session holding two records.

    Kills: In containerOwnership.ftdictClaim, replace
    ``if sHeldElsewhereName:`` with ``if False:`` so the cardinality
    read-check never refuses and both racing claims are granted.
    """
    sCredential = fsBootstrapCredential(appHub)
    transportHub = httpx.ASGITransport(app=appHub)
    async with httpx.AsyncClient(
        transport=transportHub, base_url="http://testserver",
        headers={"X-Session-Token": sCredential},
    ) as clientAsync:
        listResponses = await asyncio.gather(
            clientAsync.post(f"/api/registry/{S_CONTAINER_NAME}/claim"),
            clientAsync.post(
                f"/api/registry/{S_SECOND_CONTAINER_NAME}/claim",
            ),
        )
    assert sorted(
        response.status_code for response in listResponses
    ) == [200, 409], [response.text for response in listResponses]
    listGrantedNames = [
        response.json()["sName"] for response in listResponses
        if response.status_code == 200
    ]
    assert len(listGrantedNames) == 1
    sSessionId = _fsSessionIdOnApp(appHub, sCredential)
    assert appHub.state.dictSessionOwner == {
        sSessionId: listGrantedNames[0],
    }, "the race must resolve to exactly one cardinality record"
    assert list(appHub.state.dictContainerOwners) == listGrantedNames


@pytest.mark.falsification
def test_viewer_first_connect_refuses_a_session_holding_another_container(
    appViewer,
):
    """The viewer creation path enforces cardinality before minting.

    The reverse index already records the session as holding another
    container; its first connect to the served container must be refused
    409 (naming the held container) with no owner record minted, not
    silently granted a second ownership.

    Kills: In pipelineServer._fnRegisterViewerServedContainer, replace
    ``if sHeldElsewhereName:`` with ``if False:`` so the viewer creation
    path mints a second owner record for a session that already holds a
    different container.
    """
    sCredential = fsBootstrapCredential(appViewer)
    sSessionId = _fsSessionIdOnApp(appViewer, sCredential)
    appViewer.state.dictSessionOwner[sSessionId] = "AlreadyHeldProject"
    clientBrowser = TestClient(
        appViewer, headers={"X-Session-Token": sCredential},
    )
    responseConnect = clientBrowser.post(f"/api/connect/{S_CONTAINER_ID}")
    assert responseConnect.status_code == 409, responseConnect.text
    assert "AlreadyHeldProject" in responseConnect.json()["detail"]
    assert S_CONTAINER_NAME not in appViewer.state.dictContainerOwners, (
        "a refused first connect must not mint an owner record"
    )


def test_viewer_first_connect_without_a_conflict_still_binds(appViewer):
    """An unconflicted viewer first connect keeps working end to end."""
    sCredential = fsBootstrapCredential(appViewer)
    clientBrowser = TestClient(
        appViewer, headers={"X-Session-Token": sCredential},
    )
    responseConnect = clientBrowser.post(f"/api/connect/{S_CONTAINER_ID}")
    assert responseConnect.status_code == 200, responseConnect.text
    sSessionId = _fsSessionIdOnApp(appViewer, sCredential)
    recordOwner = appViewer.state.dictContainerOwners[S_CONTAINER_NAME]
    assert recordOwner.sBrowserSessionId == sSessionId
    assert appViewer.state.dictSessionOwner == {
        sSessionId: S_CONTAINER_NAME,
    }


def test_same_session_reclaim_of_its_own_container_stays_idempotent(
    appHub,
):
    """The bound session re-presenting its lease is never self-refused."""
    sCredential = fsBootstrapCredential(appHub)
    clientBrowser = TestClient(
        appHub, headers={"X-Session-Token": sCredential},
    )
    sLeaseId = clientBrowser.post(
        f"/api/registry/{S_CONTAINER_NAME}/claim",
    ).json()["sLeaseId"]
    responseReclaim = clientBrowser.post(
        f"/api/registry/{S_CONTAINER_NAME}/claim",
        headers={"X-Vaibify-Lease": sLeaseId},
    )
    assert responseReclaim.status_code == 200, responseReclaim.text
    assert responseReclaim.json()["sLeaseId"] == sLeaseId


def test_release_frees_the_session_for_its_next_claim(appHub):
    """Release-then-claim-another is the documented recovery path."""
    sCredential = fsBootstrapCredential(appHub)
    clientBrowser = TestClient(
        appHub, headers={"X-Session-Token": sCredential},
    )
    sLeaseId = clientBrowser.post(
        f"/api/registry/{S_CONTAINER_NAME}/claim",
    ).json()["sLeaseId"]
    responseRelease = clientBrowser.post(
        f"/api/registry/{S_CONTAINER_NAME}/release",
        headers={"X-Vaibify-Lease": sLeaseId},
    )
    assert responseRelease.json()["bReleased"] is True
    responseNext = clientBrowser.post(
        f"/api/registry/{S_SECOND_CONTAINER_NAME}/claim",
    )
    assert responseNext.status_code == 200, responseNext.text
