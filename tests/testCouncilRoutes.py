"""HTTP integration tests for the Agent Council routes (design section 15.3).

Every test drives a real ``TestClient`` with the container NAME distinct
from the container ID — the repository shipped a fatal name-vs-id bug
under a fully green suite because its fixtures used name == id, so these
never do. The assertions are the section 15.3 list: the browser lease is
required, a foreign lease is refused, the agent-token lane is refused on
every mutating route and on the credential-bearing capabilities read,
start registers exactly one turn-in-flight, a launch while a turn is
already live is refused, a stop leaves no live work, and accepting a plan
writes only host app-data — never the project container.
"""

import os

import pytest
from fastapi.testclient import TestClient
from unittest.mock import patch

from vaibify.gui import (
    actionCatalog,
    agentCouncilRegistry,
    agentCouncilStore,
    browserSession,
    containerOwnership,
    pipelineServer,
)
from vaibify.config import registryManager
from tests.sessionTokenTestHelper import fsBootstrapCredential


S_CONTAINER_ID = "councilcontainerid"
S_CONTAINER_NAME = "council-project"
S_HOST_PROJECT = "council-host-project"
S_AGENT_TOKEN = "agent-token-for-council-container"


DICT_START_BODY = {
    "sQuestion": "Which sampler settings converge fastest?",
    "listParticipants": [
        {"sProvider": "claude", "sRequestedModel": "modelOne"},
        {"sProvider": "claude", "sRequestedModel": "modelTwo"},
    ],
}


class MockDockerCouncil:
    """A Docker double whose reported NAME differs from the URL ID.

    The council routes only need name resolution and never write to the
    container, so the write recorders exist purely to prove the negative:
    accepting a plan must leave the container untouched.
    """

    def __init__(self):
        self.listWrites = []

    def flistGetRunningContainers(self):
        return [{
            "sContainerId": S_CONTAINER_ID,
            "sShortId": "council",
            "sName": S_CONTAINER_NAME,
            "sImage": "ubuntu:24.04",
        }]

    def fnWriteFile(self, sContainerId, sPath, baContent, **kwargs):
        self.listWrites.append((sContainerId, sPath))

    def fnWriteFileViaTar(self, sContainerId, sPath, baContent, **kwargs):
        self.listWrites.append((sContainerId, sPath))


@pytest.fixture(autouse=True)
def fixtureIsolateRegistry(tmp_path, monkeypatch):
    """Redirect the project registry to a temp directory for every test."""
    sRegistryDirectory = str(tmp_path / ".vaibify-registry")
    monkeypatch.setattr(
        registryManager, "_S_REGISTRY_DIRECTORY", sRegistryDirectory)
    monkeypatch.setattr(
        registryManager, "_S_REGISTRY_PATH",
        os.path.join(sRegistryDirectory, "registry.json"))


def _fnBuildAppWithTmpStore(tmp_path):
    """Build a viewer app whose council store writes under tmp_path."""
    with patch.object(
        pipelineServer, "_fconnectionCreateDocker", MockDockerCouncil,
    ):
        app = pipelineServer.fappCreateApplication(
            sWorkspaceRoot="/workspace", sTerminalUserArg="testuser")
    app.state.dictCouncilCampaignStore = (
        agentCouncilStore.fdictCreateCampaignStore(
            sDurableStoreRoot=str(tmp_path / "councils")))
    return app


def _tEstablishOwnership(app, sName, sContainerId):
    """Mint a browser credential and a lease bound to it for a container.

    Returns ``(sCredential, sLease)``. The owner record is keyed by NAME
    while the URL carries the ID, so the bound-lease gate must resolve
    one to the other — the name-vs-id property under test.
    """
    sCredential = fsBootstrapCredential(app)
    sBrowserSessionId = browserSession.fsSessionIdForCredential(
        app.state.dictBrowserSessions, sCredential)
    sLease = containerOwnership.fsMintLease()
    app.state.dictContainerOwners[sName] = containerOwnership.OwnerRecord(
        sLeaseId=sLease, fileHandleLock=None, sAgentToken=S_AGENT_TOKEN,
        sContainerId=sContainerId, sBrowserSessionId=sBrowserSessionId)
    return sCredential, sLease


@pytest.fixture
def tOwnerClient(tmp_path):
    """A connected browser client that owns the container project.

    Returns ``(client, app, docker)`` with the lease header set so the
    bound-lease authority admits it.
    """
    app = _fnBuildAppWithTmpStore(tmp_path)
    sCredential, sLease = _tEstablishOwnership(
        app, S_CONTAINER_NAME, S_CONTAINER_ID)
    client = TestClient(app, headers={
        "X-Session-Token": sCredential, "X-Vaibify-Lease": sLease,
    })
    return client, app, MockDockerCouncil


def _sStartOneCampaign(client):
    """Start a campaign and return its id, asserting the 200."""
    response = client.post(
        f"/api/agent-councils/{S_CONTAINER_ID}/start", json=DICT_START_BODY)
    assert response.status_code == 200, response.text
    return response.json()["sCampaignId"]


# ── browser lease is required, foreign lease refused ──────────────

def test_start_requires_browser_lease(tmp_path):
    """A start with no lease is refused by the bound-lease authority."""
    app = _fnBuildAppWithTmpStore(tmp_path)
    _tEstablishOwnership(app, S_CONTAINER_NAME, S_CONTAINER_ID)
    sCredential = fsBootstrapCredential(app)
    clientNoLease = TestClient(app, headers={"X-Session-Token": sCredential})
    response = clientNoLease.post(
        f"/api/agent-councils/{S_CONTAINER_ID}/start", json=DICT_START_BODY)
    assert response.status_code == 403, response.text


def test_foreign_lease_refused(tmp_path):
    """A genuine-but-foreign lease value does not authorize the container."""
    app = _fnBuildAppWithTmpStore(tmp_path)
    _tEstablishOwnership(app, S_CONTAINER_NAME, S_CONTAINER_ID)
    sCredential = fsBootstrapCredential(app)
    clientForeign = TestClient(app, headers={
        "X-Session-Token": sCredential,
        "X-Vaibify-Lease": containerOwnership.fsMintLease(),
    })
    response = clientForeign.get(f"/api/agent-councils/{S_CONTAINER_ID}")
    assert response.status_code == 403, response.text


# ── the agent-token lane is refused everywhere ────────────────────

def test_agent_token_lane_refused_on_start(tmp_path):
    """A valid in-container agent token cannot start a council."""
    app = _fnBuildAppWithTmpStore(tmp_path)
    _tEstablishOwnership(app, S_CONTAINER_NAME, S_CONTAINER_ID)
    clientAgent = TestClient(app, headers={
        actionCatalog.S_SESSION_HEADER_NAME: S_AGENT_TOKEN,
        "Host": "host.docker.internal:8050",
    })
    response = clientAgent.post(
        f"/api/agent-councils/{S_CONTAINER_ID}/start", json=DICT_START_BODY)
    assert response.status_code == 403, response.text


def test_agent_token_lane_refused_on_capabilities(tmp_path):
    """The capabilities read is credential-bearing, so it refuses the agent."""
    app = _fnBuildAppWithTmpStore(tmp_path)
    _tEstablishOwnership(app, S_CONTAINER_NAME, S_CONTAINER_ID)
    clientAgent = TestClient(app, headers={
        actionCatalog.S_SESSION_HEADER_NAME: S_AGENT_TOKEN,
        "Host": "host.docker.internal:8050",
    })
    response = clientAgent.get(
        f"/api/agent-councils/{S_CONTAINER_ID}/capabilities")
    assert response.status_code == 403, response.text


# ── start registers exactly one turn; the idle veto then holds ────

def test_start_registers_one_turn_and_vetoes_idle(tOwnerClient):
    """Start creates the campaign and registers one stable turn record."""
    client, app, _ = tOwnerClient
    sCampaignId = _sStartOneCampaign(client)
    dictRegistry = app.state.dictCouncilRegistry
    listTurns = [tKey for tKey in dictRegistry["setTurnsInFlight"]
                 if tKey[0] == sCampaignId]
    assert len(listTurns) == 1, dictRegistry["setTurnsInFlight"]
    assert agentCouncilRegistry.fbHubHasLiveCouncilWork(app) is True


def test_respond_while_turn_live_is_refused(tOwnerClient):
    """A continuation launched while a turn is already live is refused."""
    client, _, _ = tOwnerClient
    sCampaignId = _sStartOneCampaign(client)
    response = client.post(
        f"/api/agent-councils/{S_CONTAINER_ID}/{sCampaignId}/respond",
        json={"sResponseText": "please continue"})
    assert response.status_code == 409, response.text


def test_duplicate_turn_launch_refused_at_registry():
    """The registry refuses a second launch of the same turn key.

    Registry-level, so it is asserted directly rather than over HTTP: a
    second ``fbRegisterTurnInFlight`` for the same (campaign, turn) pair
    returns False, which is the refusal the route surfaces as a 409.
    """
    dictRegistry = agentCouncilRegistry.fdictCreateCouncilRegistry()
    assert agentCouncilRegistry.fbRegisterTurnInFlight(
        dictRegistry, "campaign-x", "turn-1") is True
    assert agentCouncilRegistry.fbRegisterTurnInFlight(
        dictRegistry, "campaign-x", "turn-1") is False


# ── a stop leaves no live work (the human-pause guarantee) ────────

def test_request_stop_leaves_no_live_work(tOwnerClient):
    """After a stop the registry reports no live council work at all."""
    client, app, _ = tOwnerClient
    sCampaignId = _sStartOneCampaign(client)
    response = client.post(
        f"/api/agent-councils/{S_CONTAINER_ID}/{sCampaignId}/request-stop")
    assert response.status_code == 200, response.text
    from vaibify.gui import agentCouncilCampaign
    assert response.json()["dictCampaign"]["sState"] == (
        agentCouncilCampaign.S_STATE_INTERRUPTED)
    assert agentCouncilRegistry.fbHubHasLiveCouncilWork(app) is False


# ── accepting a plan writes host app-data, never the project ──────

def test_accept_plan_writes_local_only_not_the_project(tOwnerClient):
    """A plan lands under the durable app-data root, not in the container."""
    client, app, _ = tOwnerClient
    sCampaignId = _sStartOneCampaign(client)
    client.post(
        f"/api/agent-councils/{S_CONTAINER_ID}/{sCampaignId}/request-stop")
    response = client.post(
        f"/api/agent-councils/{S_CONTAINER_ID}/{sCampaignId}/accept-plan",
        json={"sPlanText": "# Plan\n\nStep one.\n"})
    assert response.status_code == 200, response.text
    sPlanPath = response.json()["sLocalPlanPath"]
    assert os.path.isfile(sPlanPath), sPlanPath
    assert sPlanPath.startswith(
        app.state.dictCouncilCampaignStore["sDurableStoreRoot"])


def test_accept_plan_does_not_write_the_container(tmp_path):
    """The negative, proven on the docker double's write recorder."""
    docker = MockDockerCouncil()
    with patch.object(
        pipelineServer, "_fconnectionCreateDocker", lambda: docker,
    ):
        app = pipelineServer.fappCreateApplication(
            sWorkspaceRoot="/workspace", sTerminalUserArg="testuser")
    app.state.dictCouncilCampaignStore = (
        agentCouncilStore.fdictCreateCampaignStore(
            sDurableStoreRoot=str(tmp_path / "councils")))
    sCredential, sLease = _tEstablishOwnership(
        app, S_CONTAINER_NAME, S_CONTAINER_ID)
    client = TestClient(app, headers={
        "X-Session-Token": sCredential, "X-Vaibify-Lease": sLease})
    sCampaignId = _sStartOneCampaign(client)
    client.post(
        f"/api/agent-councils/{S_CONTAINER_ID}/{sCampaignId}/request-stop")
    client.post(
        f"/api/agent-councils/{S_CONTAINER_ID}/{sCampaignId}/accept-plan",
        json={"sPlanText": "# Plan\n"})
    assert docker.listWrites == [], (
        "accepting a plan wrote to the project container: "
        f"{docker.listWrites}")


# ── event polling returns the sequence and the eviction bounds ────

def test_events_poll_returns_sequence_and_bounds(tOwnerClient):
    """The events endpoint reports retained events with lowest/highest."""
    client, _, _ = tOwnerClient
    sCampaignId = _sStartOneCampaign(client)
    response = client.get(
        f"/api/agent-councils/{S_CONTAINER_ID}/{sCampaignId}/events",
        params={"iAfter": 0})
    assert response.status_code == 200, response.text
    dictEvents = response.json()
    assert dictEvents["iHighestRetainedSequence"] >= 1
    assert any(dictEvent["sKind"] == "campaignStarted"
               for dictEvent in dictEvents["listEvents"])
    # An iAfter at the high-water mark returns nothing new.
    responseTail = client.get(
        f"/api/agent-councils/{S_CONTAINER_ID}/{sCampaignId}/events",
        params={"iAfter": dictEvents["iHighestRetainedSequence"]})
    assert responseTail.json()["listEvents"] == []


def test_list_and_get_campaign(tOwnerClient):
    """A started campaign appears in the listing and can be fetched."""
    client, _, _ = tOwnerClient
    sCampaignId = _sStartOneCampaign(client)
    responseList = client.get(f"/api/agent-councils/{S_CONTAINER_ID}")
    assert responseList.status_code == 200, responseList.text
    listCampaigns = responseList.json()["listCampaigns"]
    assert any(dictSummary["sCampaignId"] == sCampaignId
               for dictSummary in listCampaigns)
    responseGet = client.get(
        f"/api/agent-councils/{S_CONTAINER_ID}/{sCampaignId}")
    assert responseGet.status_code == 200, responseGet.text
    assert responseGet.json()["dictCampaign"]["sCampaignId"] == sCampaignId


def test_capabilities_reports_container_providers(tOwnerClient):
    """A container project reports the council available, with providers."""
    client, _, _ = tOwnerClient
    response = client.get(
        f"/api/agent-councils/{S_CONTAINER_ID}/capabilities")
    assert response.status_code == 200, response.text
    dictCapabilities = response.json()
    assert dictCapabilities["bAvailable"] is True
    assert dictCapabilities["listProviders"]


def test_delete_removes_a_stopped_campaign(tOwnerClient):
    """Deleting a stopped campaign removes it from the store and disk."""
    client, app, _ = tOwnerClient
    sCampaignId = _sStartOneCampaign(client)
    client.post(
        f"/api/agent-councils/{S_CONTAINER_ID}/{sCampaignId}/request-stop")
    response = client.delete(
        f"/api/agent-councils/{S_CONTAINER_ID}/{sCampaignId}")
    assert response.status_code == 200, response.text
    assert agentCouncilStore.fjsonGetCampaignRecord(
        app.state.dictCouncilCampaignStore, sCampaignId) is None


def test_delete_refused_while_a_turn_is_live(tOwnerClient):
    """A campaign with a live turn must be stopped before it is deleted."""
    client, _, _ = tOwnerClient
    sCampaignId = _sStartOneCampaign(client)
    response = client.delete(
        f"/api/agent-councils/{S_CONTAINER_ID}/{sCampaignId}")
    assert response.status_code == 409, response.text


def test_start_refuses_a_single_model_council(tOwnerClient):
    """A council of one distinct model is refused at validation."""
    client, _, _ = tOwnerClient
    response = client.post(
        f"/api/agent-councils/{S_CONTAINER_ID}/start",
        json={
            "sQuestion": "anything",
            "listParticipants": [
                {"sProvider": "claude", "sRequestedModel": "same"},
                {"sProvider": "claude", "sRequestedModel": "same"},
            ],
        })
    assert response.status_code == 400, response.text


def test_start_refuses_an_unreviewed_provider(tOwnerClient):
    """A provider outside the reviewed vocabulary is refused by the model."""
    client, _, _ = tOwnerClient
    response = client.post(
        f"/api/agent-councils/{S_CONTAINER_ID}/start",
        json={
            "sQuestion": "anything",
            "listParticipants": [
                {"sProvider": "gemini", "sRequestedModel": "modelOne"},
                {"sProvider": "claude", "sRequestedModel": "modelTwo"},
            ],
        })
    assert response.status_code == 422, response.text


# ── the container-only refusal, both directions (design section 21) ──

def _fnRegisterHostProject(tmp_path, sName):
    """Register one host-mode project the connection router would refuse."""
    sDirectory = str(tmp_path / sName)
    os.makedirs(sDirectory, exist_ok=True)
    with open(os.path.join(sDirectory, "vaibify.yml"), "w") as fileConfig:
        fileConfig.write(f"projectName: {sName}\n")
    registryManager.fnAddProject(sDirectory, sMode="host")


def test_host_project_is_refused_with_the_marker(tmp_path):
    """A host project the caller owns is refused 409 carrying the marker."""
    _fnRegisterHostProject(tmp_path, S_HOST_PROJECT)
    app = _fnBuildAppWithTmpStore(tmp_path)
    sCredential, sLease = _tEstablishOwnership(
        app, S_HOST_PROJECT, S_HOST_PROJECT)
    client = TestClient(app, headers={
        "X-Session-Token": sCredential, "X-Vaibify-Lease": sLease})
    response = client.post(
        f"/api/agent-councils/{S_HOST_PROJECT}/start", json=DICT_START_BODY)
    assert response.status_code == 409, response.text
    assert response.json()["detail"]["sUnavailableIn"] == "host-mode"


def test_capabilities_reports_the_host_marker_rather_than_refusing(tmp_path):
    """Capabilities reports the marker at 200 so the toolbar can explain."""
    _fnRegisterHostProject(tmp_path, S_HOST_PROJECT)
    app = _fnBuildAppWithTmpStore(tmp_path)
    sCredential, sLease = _tEstablishOwnership(
        app, S_HOST_PROJECT, S_HOST_PROJECT)
    client = TestClient(app, headers={
        "X-Session-Token": sCredential, "X-Vaibify-Lease": sLease})
    response = client.get(
        f"/api/agent-councils/{S_HOST_PROJECT}/capabilities")
    assert response.status_code == 200, response.text
    assert response.json()["sUnavailableIn"] == "host-mode"
    assert response.json()["bAvailable"] is False


def test_container_project_is_never_host_refused(tOwnerClient):
    """The other direction: a container project carries no host marker."""
    client, _, _ = tOwnerClient
    response = client.post(
        f"/api/agent-councils/{S_CONTAINER_ID}/start", json=DICT_START_BODY)
    dictBody = response.json()
    detail = dictBody.get("detail") if isinstance(dictBody, dict) else {}
    if isinstance(detail, dict):
        assert "sUnavailableIn" not in detail, response.text
