"""Cross-project refusal proofs for the campaign identity binding (R2).

A campaign is bound at creation to the canonical identity pair the
routes resolve on every request: the container NAME that is the lease
principal, and the open workflow's project-repo path. These tests prove
the two directions the remediation plan names, with container name ≠ id
throughout (the repository's name-vs-id lesson):

- another resource's campaign answers the SAME 404 an unknown id gets,
  on every ``{sCampaignId}`` route, and never appears in the foreign
  listing — even when the campaign has live work (no 409 leak);
- the same container with a different active project repo cannot reach
  a campaign bound to the first repo.
"""

import os

import pytest
from fastapi.testclient import TestClient
from unittest.mock import patch

from vaibify.gui import (
    agentCouncilContext,
    agentCouncilController,
    agentCouncilStore,
    browserSession,
    containerOwnership,
    pipelineServer,
)
from vaibify.config import registryManager
from tests.sessionTokenTestHelper import fsBootstrapCredential
from tests.testCouncilRoutes import (
    _GatedFakeConnection,
    _fdictWriteFixtureSnapshot,
)


S_CONTAINER_ID_A = "identitycontaineraid"
S_CONTAINER_NAME_A = "identity-project-a"
S_CONTAINER_ID_B = "identitycontainerbid"
S_CONTAINER_NAME_B = "identity-project-b"
S_REPO_A = "/workspace/repo-alpha"
S_REPO_B = "/workspace/repo-beta"

DICT_START_BODY = {
    "sQuestion": "Which estimator handles censoring best?",
    "listParticipants": [
        {"sProvider": "claude", "sRequestedModel": "modelOne"},
        {"sProvider": "claude", "sRequestedModel": "modelTwo"},
    ],
}


class MockDockerTwoContainers:
    """A Docker double reporting two containers, each with name != id."""

    def flistGetRunningContainers(self):
        return [
            {"sContainerId": S_CONTAINER_ID_A, "sShortId": "identa",
             "sName": S_CONTAINER_NAME_A, "sImage": "ubuntu:24.04",
             "sImageIdentity": "sha256:" + "aa11" * 16},
            {"sContainerId": S_CONTAINER_ID_B, "sShortId": "identb",
             "sName": S_CONTAINER_NAME_B, "sImage": "ubuntu:24.04",
             "sImageIdentity": "sha256:" + "bb22" * 16},
        ]

    def fbaFetchFile(self, sContainerId, sPath, iMaxBytes=None):
        """Answer the launch-time login-presence probe, and only it."""
        if sPath.endswith("/.claude/.credentials.json"):
            return (b'{"claudeAiOauth": '
                    b'{"accessToken": "fixture-access-token"}}')
        raise AssertionError(f"unmodelled container read: {sPath!r}")


@pytest.fixture(autouse=True)
def fixtureIsolateRegistry(tmp_path, monkeypatch):
    """Redirect the project registry to a temp directory for every test."""
    sRegistryDirectory = str(tmp_path / ".vaibify-registry")
    monkeypatch.setattr(
        registryManager, "_S_REGISTRY_DIRECTORY", sRegistryDirectory)
    monkeypatch.setattr(
        registryManager, "_S_REGISTRY_PATH",
        os.path.join(sRegistryDirectory, "registry.json"))


@pytest.fixture(autouse=True)
def fixtureFakeDeliberation(monkeypatch):
    """Gate-closed fake connections + fixture snapshot writer.

    The gate stays CLOSED: the campaign started under resource A keeps
    a live drive for the whole test, which is exactly the premise the
    foreign-delete leak check needs (a running campaign must answer a
    foreign caller 404, never a busy 409).
    """
    import threading
    eventGate = threading.Event()
    monkeypatch.setattr(
        agentCouncilController, "fconnectionBuildParticipantConnection",
        lambda dictRuntime, dictParticipant: _GatedFakeConnection(eventGate))
    monkeypatch.setattr(
        agentCouncilContext, "fdictCaptureProjectContextSnapshot",
        _fdictWriteFixtureSnapshot)
    from vaibify.gui import agentCouncilCredentialGate
    monkeypatch.setattr(
        agentCouncilCredentialGate, "fdictEvaluateCredentialEnablement",
        lambda sProvider, sImageIdentity=None: {
            "bEnabled": True, "sReason": "", "dictRecord": {}})


def _fnSeedWorkflowRepo(app, sContainerId, sProjectRepoPath):
    """Bind the open workflow's project repo for one container."""
    app.state.dictRouteContext["workflows"][sContainerId] = {
        "sProjectRepoPath": sProjectRepoPath}


def _clientForContainer(app, sName, sContainerId):
    """Mint ownership of one container and return its browser client."""
    sCredential = fsBootstrapCredential(app)
    sBrowserSessionId = browserSession.fsSessionIdForCredential(
        app.state.dictBrowserSessions, sCredential)
    sLease = containerOwnership.fsMintLease()
    app.state.dictContainerOwners[sName] = containerOwnership.OwnerRecord(
        sLeaseId=sLease, fileHandleLock=None,
        sAgentToken=f"agent-token-{sName}",
        sContainerId=sContainerId, sBrowserSessionId=sBrowserSessionId)
    return TestClient(app, headers={
        "X-Session-Token": sCredential, "X-Vaibify-Lease": sLease})


@pytest.fixture
def tTwoResourceApp(tmp_path):
    """One hub, two owned container projects, campaign started under A."""
    with patch.object(
        pipelineServer, "_fconnectionCreateDocker", MockDockerTwoContainers,
    ):
        app = pipelineServer.fappCreateApplication(
            sWorkspaceRoot="/workspace", sTerminalUserArg="testuser")
    app.state.dictCouncilCampaignStore = (
        agentCouncilStore.fdictCreateCampaignStore(
            sDurableStoreRoot=str(tmp_path / "councils")))
    _fnSeedWorkflowRepo(app, S_CONTAINER_ID_A, S_REPO_A)
    _fnSeedWorkflowRepo(app, S_CONTAINER_ID_B, S_REPO_B)
    with _clientForContainer(
            app, S_CONTAINER_NAME_A, S_CONTAINER_ID_A) as clientA:
        clientB = _clientForContainer(
            app, S_CONTAINER_NAME_B, S_CONTAINER_ID_B)
        response = clientA.post(
            f"/api/agent-councils/{S_CONTAINER_ID_A}/start",
            json=DICT_START_BODY)
        assert response.status_code == 200, response.text
        yield app, clientA, clientB, response.json()["sCampaignId"]


def test_owner_still_reaches_its_own_campaign(tTwoResourceApp):
    """Sanity: the binding does not lock the rightful owner out."""
    _, clientA, _, sCampaignId = tTwoResourceApp
    response = clientA.get(
        f"/api/agent-councils/{S_CONTAINER_ID_A}/{sCampaignId}")
    assert response.status_code == 200, response.text
    dictIdentity = response.json()["dictCampaign"]["dictProjectIdentity"]
    assert dictIdentity["sResourceName"] == S_CONTAINER_NAME_A
    assert dictIdentity["sProjectRepoPath"] == S_REPO_A


def test_foreign_resource_gets_404_on_every_campaign_route(tTwoResourceApp):
    """B's valid session cannot reach, mutate, or delete A's campaign.

    The campaign still has LIVE work (started, never stopped), so the
    delete leg also proves the ordering: a foreign caller sees 404,
    never the 409 that would leak that the campaign exists and runs.
    """
    _, _, clientB, sCampaignId = tTwoResourceApp
    sBase = f"/api/agent-councils/{S_CONTAINER_ID_B}/{sCampaignId}"
    listAttempts = [
        ("GET", sBase, None),
        ("GET", f"{sBase}/events", None),
        ("POST", f"{sBase}/respond", {"sResponseText": "carry on"}),
        ("POST", f"{sBase}/request-stop", None),
        ("POST", f"{sBase}/accept-plan", {"sPlanText": "# plan"}),
        ("POST", f"{sBase}/grant-resolution-round", {"iGrantedRounds": 1}),
        ("POST", f"{sBase}/resolve-objections",
         {"dictDispositionByObjectionId": {}}),
        ("POST", f"{sBase}/reject-candidate", {"sReasonText": "no"}),
        ("DELETE", sBase, None),
    ]
    for sMethod, sUrl, dictBody in listAttempts:
        response = clientB.request(sMethod, sUrl, json=dictBody)
        assert response.status_code == 404, (
            f"{sMethod} {sUrl} answered {response.status_code}: "
            f"{response.text}")
        assert response.json()["detail"] == (
            f"no council campaign '{sCampaignId}'")


def test_foreign_listing_omits_the_campaign(tTwoResourceApp):
    """A's campaign is absent from B's listing entirely."""
    _, _, clientB, sCampaignId = tTwoResourceApp
    response = clientB.get(f"/api/agent-councils/{S_CONTAINER_ID_B}")
    assert response.status_code == 200, response.text
    assert all(dictSummary["sCampaignId"] != sCampaignId
               for dictSummary in response.json()["listCampaigns"])


def test_second_repo_in_same_container_cannot_reach_campaign(
        tTwoResourceApp):
    """Same container, different active repo: the campaign vanishes."""
    app, clientA, _, sCampaignId = tTwoResourceApp
    _fnSeedWorkflowRepo(app, S_CONTAINER_ID_A, S_REPO_B)
    response = clientA.get(
        f"/api/agent-councils/{S_CONTAINER_ID_A}/{sCampaignId}")
    assert response.status_code == 404, response.text
    responseList = clientA.get(f"/api/agent-councils/{S_CONTAINER_ID_A}")
    assert responseList.json()["listCampaigns"] == []
    _fnSeedWorkflowRepo(app, S_CONTAINER_ID_A, S_REPO_A)
    responseBack = clientA.get(
        f"/api/agent-councils/{S_CONTAINER_ID_A}/{sCampaignId}")
    assert responseBack.status_code == 200, responseBack.text


def test_unbound_legacy_record_matches_no_principal(tTwoResourceApp):
    """A stored record with an empty identity is unreachable, not open.

    A record predating the binding (or with a hand-emptied identity)
    must fail closed: it matches no principal at all.
    """
    app, clientA, _, _ = tTwoResourceApp
    from vaibify.gui import agentCouncilCampaign
    dictLegacy = agentCouncilCampaign.fdictCreateCampaign(
        "legacy question", [
            agentCouncilCampaign.fdictCreateParticipant("claude", "mOne"),
            agentCouncilCampaign.fdictCreateParticipant("claude", "mTwo"),
        ])
    agentCouncilStore.fdictRegisterStartedCampaign(
        app.state.dictCouncilCampaignStore, dictLegacy)
    response = clientA.get(
        f"/api/agent-councils/{S_CONTAINER_ID_A}"
        f"/{dictLegacy['sCampaignId']}")
    assert response.status_code == 404, response.text
