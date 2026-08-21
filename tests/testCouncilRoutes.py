"""HTTP integration tests for the Agent Council routes (design section 15.3).

Every test drives a real ``TestClient`` with the container NAME distinct
from the container ID — the repository shipped a fatal name-vs-id bug
under a fully green suite because its fixtures used name == id, so these
never do. The assertions are the section 15.3 list: the browser lease is
required, a foreign lease is refused, the agent-token lane is refused on
every mutating route and on the credential-bearing capabilities read,
start registers exactly one turn-in-flight, a launch while a turn is
already live is refused, a stop is honest (a request against a live
engine, an immediate settle otherwise), and accepting a plan writes only
host app-data — never the project container.

Since R1b, start drives the REAL controller and engine: the fixture
substitutes gate-controlled fake provider connections and a fixture
snapshot writer, so deliberation runs with no daemon and each test
decides when turns settle by opening the gate. Clients that launch a
campaign are context-managed so the drive task's event loop outlives
the individual request.
"""

import asyncio
import io
import json
import os
import tarfile
import time

import pytest
from fastapi.testclient import TestClient
from unittest.mock import patch

from vaibify.gui import (
    actionCatalog,
    agentCouncilContext,
    agentCouncilController,
    agentCouncilRegistry,
    agentCouncilStore,
    browserSession,
    containerOwnership,
    pipelineServer,
)
from vaibify.config import registryManager
from tests.agentCouncilHarness import fdictMakeTurnResult
from tests.sessionTokenTestHelper import fsBootstrapCredential


S_CONTAINER_ID = "councilcontainerid"
S_CONTAINER_NAME = "council-project"
# The immutable content-addressed image id the fake reports beside the
# display tag — the identity the credential gate and runners must read.
S_IMAGE_IDENTITY = "sha256:" + "ab12" * 16
S_HOST_PROJECT = "council-host-project"
S_AGENT_TOKEN = "agent-token-for-council-container"
S_PROJECT_REPO = "/workspace/project-repo"


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
            "sImageIdentity": S_IMAGE_IDENTITY,
        }]

    def fbaFetchCredentialFile(self, sContainerId, sPath):
        return self.fbaFetchFile(sContainerId, sPath)

    def fbaFetchFile(self, sContainerId, sPath, iMaxBytes=None):
        """Answer the launch-time login-presence probe, and only it.

        Fail-closed like every other double here: the ONE path the
        council reads is the persisted Claude login, and anything else
        raises rather than returning plausible bytes.
        """
        if sPath.endswith("/.claude/.credentials.json"):
            return json.dumps({
                "claudeAiOauth": {"accessToken": "fixture-access-token"},
            }).encode("utf-8")
        raise AssertionError(f"unmodelled container read: {sPath!r}")

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


class _GatedFakeConnection:
    """A provider connection whose turns settle only when the gate opens.

    The gate is a ``threading.Event`` polled from the coroutine, NOT an
    ``asyncio.Event``: the test thread opens the gate while the drive
    task runs on the TestClient portal's loop in another thread, and a
    Python 3.9 asyncio.Event is bound to whichever loop existed at its
    construction — setting it cross-thread is exactly the
    different-loop trap.
    """

    def __init__(self, eventGate):
        self._eventGate = eventGate

    async def fdictPrepareImmutableContext(self, dictTurnRequest):
        return {"sContextIdentity": "gated-fake"}

    async def fnStartTurn(self, dictTurnRequest):
        while not self._eventGate.is_set():
            await asyncio.sleep(0.02)

    async def fiterStreamNormalizedEvents(self):
        if False:
            yield {}

    async def fdictCollectStructuredResult(self):
        return fdictMakeTurnResult(sVerdict="accept")

    async def fsReportCompletion(self):
        return "terminal"


def _fdictWriteFixtureSnapshot(connectionDocker, sContainerId,
                               sProjectRepoPath, sCampaignId,
                               sSnapshotStoreRoot=None):
    """Write a minimal sealed snapshot the way the real capture would."""
    sDirectory = os.path.join(sSnapshotStoreRoot, sCampaignId, "snapshot")
    os.makedirs(sDirectory, exist_ok=True)
    with tarfile.open(
            os.path.join(sDirectory, "snapshot.tar"), "w") as fileTar:
        baProject = b'{"name": "route-test-fixture"}'
        infoProject = tarfile.TarInfo(name="project.json")
        infoProject.size = len(baProject)
        fileTar.addfile(infoProject, io.BytesIO(baProject))
    with open(os.path.join(sDirectory, "manifest.json"),
              "w") as fileManifest:
        fileManifest.write(json.dumps({
            "sSnapshotSha256": "fixture-snapshot-hash",
            "sCommitSha": "fixturecommit0001",
            "sDirtyStateDigest": "fixturedigest0001",
            "sBaselineHeadSha": "fixturecommit0001",
            "sBaselinePorcelainDigest": "fixtureporcelain0001"}))
    return {"sSnapshotSha256": "fixture-snapshot-hash"}


@pytest.fixture(autouse=True)
def eventTurnGate(monkeypatch):
    """Substitute gated fake connections and the fixture snapshot writer.

    Returns the gate; a test opens it to let in-flight turns settle.
    The engine itself runs REAL — only the provider seam and the
    daemon-touching capture are replaced.
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
    return eventGate


def _fnWaitForNoLiveCouncilWork(app, fDeadlineSeconds=10.0):
    """Poll until every turn-in-flight retires, or fail loudly.

    The store checkpoint that makes a terminal state VISIBLE lands
    before the drive task's ``finally`` retires the turn — record
    first, accounting second, deliberately — so a state-poll followed
    by an instant registry assertion races the unwind (it lost on the
    Python 3.14 CI lane). The guarantee is that live work retires; a
    bounded wait asserts exactly that, and a turn that never retires
    still fails here.
    """
    fDeadline = time.monotonic() + fDeadlineSeconds
    while time.monotonic() < fDeadline:
        if not agentCouncilRegistry.fbHubHasLiveCouncilWork(app):
            return
        time.sleep(0.05)
    raise AssertionError(
        "council work never retired: "
        f"{app.state.dictCouncilRegistry['setTurnsInFlight']}")


def _fnWaitForCampaignState(app, sCampaignId, sExpectedState,
                            fDeadlineSeconds=15.0):
    """Poll the store until the campaign reaches a state, or fail."""
    fDeadline = time.monotonic() + fDeadlineSeconds
    sObservedState = ""
    while time.monotonic() < fDeadline:
        dictRecord = agentCouncilStore.fjsonGetCampaignRecord(
            app.state.dictCouncilCampaignStore, sCampaignId)
        sObservedState = dictRecord["sState"] if dictRecord else "(gone)"
        if sObservedState == sExpectedState:
            return
        time.sleep(0.05)
    raise AssertionError(
        f"campaign never reached {sExpectedState!r} "
        f"(stuck at {sObservedState!r})")


def _fnBuildAppWithTmpStore(tmp_path):
    """Build a viewer app whose council store writes under tmp_path.

    The workflow cache is seeded with an open project repo because the
    campaign identity is bound to (resource name, project repo): every
    council route resolves that pair before touching the store.
    """
    with patch.object(
        pipelineServer, "_fconnectionCreateDocker", MockDockerCouncil,
    ):
        app = pipelineServer.fappCreateApplication(
            sWorkspaceRoot="/workspace", sTerminalUserArg="testuser")
    app.state.dictCouncilCampaignStore = (
        agentCouncilStore.fdictCreateCampaignStore(
            sDurableStoreRoot=str(tmp_path / "councils")))
    app.state.dictRouteContext["workflows"][S_CONTAINER_ID] = {
        "sProjectRepoPath": S_PROJECT_REPO}
    app.state.dictRouteContext["workflows"][S_HOST_PROJECT] = {
        "sProjectRepoPath": S_PROJECT_REPO}
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

    Yields ``(client, app, docker)`` with the lease header set so the
    bound-lease authority admits it. Context-managed so the campaign
    drive task's event loop outlives each individual request.
    """
    app = _fnBuildAppWithTmpStore(tmp_path)
    sCredential, sLease = _tEstablishOwnership(
        app, S_CONTAINER_NAME, S_CONTAINER_ID)
    with TestClient(app, headers={
        "X-Session-Token": sCredential, "X-Vaibify-Lease": sLease,
    }) as client:
        yield client, app, MockDockerCouncil


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


# ── a stop is honest: a request while live, settled at the boundary ──

def test_request_stop_is_cooperative_then_settles(
        tOwnerClient, eventTurnGate):
    """A stop against a live engine is a REQUEST that settles honestly.

    While turns are in flight the response says ``bStopRequested`` and
    the state stays the truth (planning) — never a fabricated terminal
    state over runners nobody settled. Opening the gate lets the
    in-flight wave settle; the engine then archives at the next
    boundary and every piece of live work retires.
    """
    client, app, _ = tOwnerClient
    sCampaignId = _sStartOneCampaign(client)
    response = client.post(
        f"/api/agent-councils/{S_CONTAINER_ID}/{sCampaignId}/request-stop")
    assert response.status_code == 200, response.text
    dictBody = response.json()
    assert dictBody["bStopRequested"] is True
    assert dictBody["bSettled"] is False
    assert dictBody["dictCampaign"]["sState"] == "planning"
    eventTurnGate.set()
    from vaibify.gui import agentCouncilCampaign
    _fnWaitForCampaignState(
        app, sCampaignId, agentCouncilCampaign.S_STATE_ARCHIVED)
    _fnWaitForNoLiveCouncilWork(app)


def test_deliberation_reaches_plan_ready_with_no_hand_patched_state(
        tOwnerClient, eventTurnGate):
    """Start → real engine over the controller → planReady (R1 proof a).

    Nothing patches the campaign record: the gate opens, every fake
    turn accepts, and the ENGINE walks the record to planReady through
    the store's checkpoints. The identity triple carries the sealed
    snapshot hash the fixture capture returned.
    """
    client, app, _ = tOwnerClient
    eventTurnGate.set()
    sCampaignId = _sStartOneCampaign(client)
    from vaibify.gui import agentCouncilCampaign
    _fnWaitForCampaignState(
        app, sCampaignId, agentCouncilCampaign.S_STATE_PLAN_READY)
    dictRecord = agentCouncilStore.fjsonGetCampaignRecord(
        app.state.dictCouncilCampaignStore, sCampaignId)
    assert dictRecord["dictCandidatePlan"] is not None
    assert dictRecord["dictProjectIdentity"]["sSnapshotIdentity"] == (
        "fixture-snapshot-hash")
    _fnWaitForNoLiveCouncilWork(app)


# ── accepting a plan writes host app-data, never the project ──────

def test_accept_plan_writes_local_only_not_the_project(
        tOwnerClient, eventTurnGate):
    """A plan lands under the durable app-data root, not in the container."""
    client, app, _ = tOwnerClient
    eventTurnGate.set()
    sCampaignId = _sStartOneCampaign(client)
    from vaibify.gui import agentCouncilCampaign
    _fnWaitForCampaignState(
        app, sCampaignId, agentCouncilCampaign.S_STATE_PLAN_READY)
    response = client.post(
        f"/api/agent-councils/{S_CONTAINER_ID}/{sCampaignId}/accept-plan",
        json={"sPlanText": "# Plan\n\nStep one.\n"})
    assert response.status_code == 200, response.text
    sPlanPath = response.json()["sLocalPlanPath"]
    assert os.path.isfile(sPlanPath), sPlanPath
    assert sPlanPath.startswith(
        app.state.dictCouncilCampaignStore["sDurableStoreRoot"])
    # The sealed content identity matches the artifact byte for byte.
    import hashlib
    with open(sPlanPath, "rb") as filePlan:
        sExpectedSha256 = hashlib.sha256(filePlan.read()).hexdigest()
    assert response.json()["sPlanSha256"] == sExpectedSha256


def test_accept_plan_does_not_write_the_container(tmp_path, eventTurnGate):
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
    app.state.dictRouteContext["workflows"][S_CONTAINER_ID] = {
        "sProjectRepoPath": S_PROJECT_REPO}
    sCredential, sLease = _tEstablishOwnership(
        app, S_CONTAINER_NAME, S_CONTAINER_ID)
    eventTurnGate.set()
    with TestClient(app, headers={
        "X-Session-Token": sCredential, "X-Vaibify-Lease": sLease,
    }) as client:
        sCampaignId = _sStartOneCampaign(client)
        from vaibify.gui import agentCouncilCampaign
        _fnWaitForCampaignState(
            app, sCampaignId, agentCouncilCampaign.S_STATE_PLAN_READY)
        client.post(
            f"/api/agent-councils/{S_CONTAINER_ID}/{sCampaignId}"
            "/accept-plan",
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
    assert any(dictEvent["sEventKind"] == "campaignStarted"
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
    assert responseGet.json()["listQuarantinedRunners"] == []


def test_get_campaign_reports_quarantined_runners(tOwnerClient):
    """A quarantined reservation surfaces on the campaign read.

    The "runner may exist" surface (remediation R4): a reservation the
    daemon could not prove gone stays visible on the campaign response,
    keyed to THIS campaign — a foreign campaign's quarantine must not
    appear.
    """
    client, app, _ = tOwnerClient
    sCampaignId = _sStartOneCampaign(client)
    dictRegistry = app.state.dictCouncilRegistry
    agentCouncilRegistry.fdictReserveRunner(
        dictRegistry, sCampaignId, "res-quarantined", "claude",
        {"iMemoryBytes": 1024, "fCpuCount": 1.0})
    agentCouncilRegistry.fnMarkRunnerCreated(
        dictRegistry, "res-quarantined", "container-unproven")
    agentCouncilRegistry.fdictSettleReservation(
        dictRegistry, "res-quarantined", "quarantined")
    agentCouncilRegistry.fdictReserveRunner(
        dictRegistry, "another-campaign", "res-foreign", "claude",
        {"iMemoryBytes": 1024, "fCpuCount": 1.0})
    agentCouncilRegistry.fnMarkRunnerCreated(
        dictRegistry, "res-foreign", "container-foreign")
    agentCouncilRegistry.fdictSettleReservation(
        dictRegistry, "res-foreign", "quarantined")
    responseGet = client.get(
        f"/api/agent-councils/{S_CONTAINER_ID}/{sCampaignId}")
    assert responseGet.status_code == 200, responseGet.text
    assert responseGet.json()["listQuarantinedRunners"] == [{
        "sReservationId": "res-quarantined",
        "sCampaignId": sCampaignId,
        "sProvider": "claude",
    }]


def test_capabilities_reports_container_providers(tOwnerClient):
    """A container project reports the council available, with providers."""
    client, _, _ = tOwnerClient
    response = client.get(
        f"/api/agent-councils/{S_CONTAINER_ID}/capabilities")
    assert response.status_code == 200, response.text
    dictCapabilities = response.json()
    assert dictCapabilities["bAvailable"] is True
    assert dictCapabilities["listProviders"]


def test_delete_removes_a_stopped_campaign(tOwnerClient, eventTurnGate):
    """Deleting a settled campaign removes it from the store and disk."""
    client, app, _ = tOwnerClient
    sCampaignId = _sStartOneCampaign(client)
    client.post(
        f"/api/agent-councils/{S_CONTAINER_ID}/{sCampaignId}/request-stop")
    eventTurnGate.set()
    from vaibify.gui import agentCouncilCampaign
    _fnWaitForCampaignState(
        app, sCampaignId, agentCouncilCampaign.S_STATE_ARCHIVED)
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


# ── the enabled-path wiring the review found missing ───────────────


def test_start_hands_the_resolved_image_to_the_credential_gate(
        tOwnerClient, monkeypatch):
    """The evidence record's image pin is compared at START, always.

    The gate used to be consulted with no image identity, so an
    evidence record verified in a different image enabled paid work
    anyway. Start now resolves the project image first and the gate
    sees it on every call.
    """
    from vaibify.gui import agentCouncilCredentialGate
    listSeenImageIdentities = []

    def _fdictRecordingGate(sProvider, sImageIdentity=None):
        listSeenImageIdentities.append(sImageIdentity)
        return {"bEnabled": True, "sReason": "", "dictRecord": {}}

    monkeypatch.setattr(
        agentCouncilCredentialGate, "fdictEvaluateCredentialEnablement",
        _fdictRecordingGate)
    client, app, docker = tOwnerClient
    _sStartOneCampaign(client)
    assert listSeenImageIdentities == [S_IMAGE_IDENTITY], (
        "start must evaluate the gate with the resolved IMMUTABLE "
        "image identity — never blind, and never the repointable tag")


def test_capture_refusal_leaves_a_failed_record_never_planning(
        tOwnerClient, monkeypatch):
    """Transactional start at the route: no phantom planning campaign.

    A coherence refusal answers 409 AND the registered record says
    failed — a reader who never saw the response cannot mistake the
    campaign for one that is deliberating.
    """
    def _fdictRefuseCapture(*tArguments, **dictKeywords):
        raise agentCouncilContext.SnapshotRefusedError(
            "the repository changed while the snapshot was streaming")

    monkeypatch.setattr(
        agentCouncilContext, "fdictCaptureProjectContextSnapshot",
        _fdictRefuseCapture)
    client, app, docker = tOwnerClient
    response = client.post(
        f"/api/agent-councils/{S_CONTAINER_ID}/start", json=DICT_START_BODY)
    assert response.status_code == 409, response.text
    dictStore = app.state.dictCouncilCampaignStore
    listStates = [
        agentCouncilStore.fjsonGetCampaignRecord(dictStore, sId)["sState"]
        for sId in dictStore["listInsertionOrder"]]
    assert listStates == ["failed"], (
        "a failed start must not strand a planning record")


# The release-refuses-while-deliberating proof lives in
# tests/testCouncilRunnerAccess.py: the release route is a HUB route
# (fnRegisterRegistryRoutes), not part of the viewer app this file
# builds, so it is driven there over the hub-app fixture shape.


def test_capabilities_compare_the_resolved_image_like_start_does(
        tOwnerClient, monkeypatch):
    """Capabilities can never advertise what start would refuse.

    The reviewed optimism: the capabilities read evaluated the gate
    image-blind, so an evidence record for a different image showed an
    available council whose start 409'd. Both now compare the same
    resolved immutable image identity.
    """
    from vaibify.gui import agentCouncilCredentialGate
    listSeenImageIdentities = []

    def _fdictRecordingGate(sProvider, sImageIdentity=None):
        listSeenImageIdentities.append(sImageIdentity)
        return {"bEnabled": True, "sReason": "", "dictRecord": {}}

    monkeypatch.setattr(
        agentCouncilCredentialGate, "fdictEvaluateCredentialEnablement",
        _fdictRecordingGate)
    client, app, docker = tOwnerClient
    response = client.get(
        f"/api/agent-councils/{S_CONTAINER_ID}/capabilities")
    assert response.status_code == 200, response.text
    assert response.json()["bAvailable"] is True
    assert listSeenImageIdentities == [S_IMAGE_IDENTITY], (
        "the capabilities read must compare the same immutable image "
        "identity start compares — image-blind optimism advertises a "
        "council start will refuse")


def test_start_refuses_a_project_with_no_claude_login(
        tOwnerClient, monkeypatch):
    """R10's presence probe: no login, no campaign, no runner.

    The extraction used to discover an absent login only inside the
    first turn's prepare — AFTER a runner had been created — so the
    researcher read a failed turn instead of "log in". The probe now
    runs at start, before anything registers.
    """
    from vaibify.gui import agentCouncilProviders
    monkeypatch.setattr(
        agentCouncilProviders, "fbRunnerCredentialIsPresent",
        lambda connectionDocker, sContainerId, sPath: False)
    client, app, _ = tOwnerClient
    response = client.post(
        f"/api/agent-councils/{S_CONTAINER_ID}/start", json=DICT_START_BODY)
    assert response.status_code == 409, response.text
    assert "log in to Claude" in response.json()["detail"]
    assert app.state.dictCouncilCampaignStore["listInsertionOrder"] == [], (
        "a login-less start must register no campaign at all")


def test_login_presence_probe_holds_no_credential_material():
    """The probe answers a boolean; the token never leaves the read."""
    from vaibify.gui import agentCouncilProviders

    class _FakeLoginConnection:
        def fbaFetchCredentialFile(self, sContainerId, sPath):
            return json.dumps({
                "claudeAiOauth": {
                    "accessToken": "the-secret-token",
                    "refreshToken": "the-refresh-token"},
            }).encode("utf-8")

    bPresent = agentCouncilProviders.fbRunnerCredentialIsPresent(
        _FakeLoginConnection(), "cid", "/workspace/x/.claude/.credentials.json")
    assert bPresent is True
    assert isinstance(bPresent, bool), (
        "the probe must answer a boolean, never the credential itself")

    class _FakeMissingLoginConnection:
        def fbaFetchCredentialFile(self, sContainerId, sPath):
            raise FileNotFoundError(sPath)

    assert agentCouncilProviders.fbRunnerCredentialIsPresent(
        _FakeMissingLoginConnection(), "cid", "/workspace/x/.claude/x") is False
