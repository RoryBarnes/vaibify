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
from vaibify.gui import councilRouteGuards
from vaibify.gui.routes import councilRoutes
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
        # A repository comfortably inside the snapshot bounds, so the
        # capabilities pre-flight permits a council. Mutable on purpose:
        # the too-large journey raises it, which is what a researcher's
        # 30 GB output tree does to the real probe.
        self.dictRepositoryWeight = {
            "iFileCount": 120, "iTotalBytes": 2 * 1024 * 1024,
            "bTruncated": False, "bLargestFilesTruncated": False,
            "listLargestFiles": [{"sPath": "README.md", "iSizeBytes": 1024}],
            "listEscapingSymlinks": [], "listSpecialFiles": [],
            "listSubmodules": [],
        }
        # A daemon whose memory is BELOW the floor, so these tests read
        # the declared floors and never this machine's real capacity: a
        # bound assertion that changed with the developer's RAM would be
        # a test of the laptop, not of the code.
        self.dictDaemonCapacity = {"iMemoryBytes": 0, "iCpuCount": 0}

    def fdictWeighRepository(self, sContainerId, sRepositoryPath):
        return dict(self.dictRepositoryWeight)

    def fdictReadDaemonCapacity(self):
        return dict(self.dictDaemonCapacity)

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
                               sSnapshotStoreRoot=None, dictBounds=None,
                               listExcludedPaths=None):
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


def test_capabilities_refuse_a_repository_too_large_to_snapshot(
    tmp_path,
):
    """The pre-flight, and it must run BEFORE the researcher invests.

    Every turn ships an immutable snapshot of the repository, and the
    capture bounds are enforced mid-stream — so without this the
    refusal arrives only after participants are chosen and a question
    is written, and the question is the expensive part. A researcher
    hit exactly that on a 30 GB output tree (2026-08-22).

    Asserts the REASON names the real numbers, not merely that
    something was refused: "unavailable" with no figures sends someone
    hunting for a permission problem they do not have.
    """
    docker = MockDockerCouncil()
    docker.dictRepositoryWeight = {
        "iFileCount": 22342,
        "iTotalBytes": 30 * 1024 * 1024 * 1024,
        "bTruncated": False,
    }
    with patch.object(
        pipelineServer, "_fconnectionCreateDocker", lambda: docker,
    ):
        app = pipelineServer.fappCreateApplication(
            sWorkspaceRoot="/workspace", sTerminalUserArg="testuser")
    app.state.dictRouteContext["workflows"][S_CONTAINER_ID] = {
        "sProjectRepoPath": S_PROJECT_REPO}
    sCredential, sLease = _tEstablishOwnership(
        app, S_CONTAINER_NAME, S_CONTAINER_ID)
    with TestClient(app, headers={
        "X-Session-Token": sCredential, "X-Vaibify-Lease": sLease,
    }) as client:
        dictCapabilities = client.get(
            f"/api/agent-councils/{S_CONTAINER_ID}/capabilities").json()

    assert dictCapabilities["bAvailable"] is False
    assert dictCapabilities["sUnavailableIn"] == "snapshot-too-large"
    assert "22342" in dictCapabilities["sReason"]
    assert "30720 MB" in dictCapabilities["sReason"]
    assert dictCapabilities["dictSnapshotFeasibility"]["bFits"] is False


def _fdictBlankProjectApp(tmp_path, listTrackedNames):
    """An app whose container has NO workflow open, tracking these repos."""
    docker = MockDockerCouncil()
    with patch.object(
        pipelineServer, "_fconnectionCreateDocker", lambda: docker,
    ):
        app = pipelineServer.fappCreateApplication(
            sWorkspaceRoot="/workspace", sTerminalUserArg="testuser")
    # The Blank Project state: a container is open, no workflow is.
    app.state.dictRouteContext["workflows"].pop(S_CONTAINER_ID, None)
    app.state.dictCouncilCampaignStore = (
        agentCouncilStore.fdictCreateCampaignStore(
            sDurableStoreRoot=str(tmp_path / "councils")))
    return app, docker, listTrackedNames


def _fdictCapabilitiesForBlankProject(tmp_path, monkeypatch,
                                      listTrackedNames):
    """Drive capabilities in the Blank Project state; return the payload."""
    from vaibify.gui import trackedReposManager
    monkeypatch.setattr(
        trackedReposManager, "fdictReadOrSeedSidecar",
        lambda connectionDocker, sContainerId: {
            "listTracked": [{"sName": sName} for sName in listTrackedNames],
            "listIgnored": [],
        })
    app, _, _ = _fdictBlankProjectApp(tmp_path, listTrackedNames)
    sCredential, sLease = _tEstablishOwnership(
        app, S_CONTAINER_NAME, S_CONTAINER_ID)
    with TestClient(app, headers={
        "X-Session-Token": sCredential, "X-Vaibify-Lease": sLease,
    }) as client:
        return client.get(
            f"/api/agent-councils/{S_CONTAINER_ID}/capabilities").json()


def test_a_blank_project_convenes_against_its_one_tracked_directory(
    tmp_path, monkeypatch,
):
    """The point of the whole change: no steps defined is not a refusal.

    A project with no steps yet is arguably the one a PLANNING council
    helps most, and it was refused outright because the campaign's repo
    half came only from an open workflow. It now comes from the
    tracked-repos sidecar — the researcher's own recorded statement
    about which directories are part of this project.

    Note what is NOT special-cased: whether that directory is empty
    (true greenfield) or holds files with no steps (slightly
    brownfield). Both are just a directory, and the snapshot machinery
    already handles either.

    Drives the START route, not merely capabilities. Capabilities
    resolves the directory through its own call, so a version of this
    test that only read capabilities passed with the principal
    resolver's Blank Project branch DELETED — green while the feature
    was broken. Convening is the behaviour; asserting it is the test.
    """
    from vaibify.gui import trackedReposManager
    monkeypatch.setattr(
        trackedReposManager, "fdictReadOrSeedSidecar",
        lambda connectionDocker, sContainerId: {
            "listTracked": [{"sName": "theOnlyRepo"}], "listIgnored": [],
        })
    app, _, _ = _fdictBlankProjectApp(tmp_path, ["theOnlyRepo"])
    sCredential, sLease = _tEstablishOwnership(
        app, S_CONTAINER_NAME, S_CONTAINER_ID)

    with TestClient(app, headers={
        "X-Session-Token": sCredential, "X-Vaibify-Lease": sLease,
    }) as client:
        response = client.post(
            f"/api/agent-councils/{S_CONTAINER_ID}/start",
            json=DICT_START_BODY)

        assert response.status_code == 200, (
            "a Blank Project with one tracked directory could not "
            f"convene: {response.text}")
        dictCampaign = agentCouncilStore.fjsonGetCampaignRecord(
            app.state.dictCouncilCampaignStore,
            response.json()["sCampaignId"])
        assert dictCampaign["dictProjectIdentity"]["sProjectRepoPath"] == (
            "/workspace/theOnlyRepo"), (
            "the campaign bound to the wrong directory: "
            f"{dictCampaign['dictProjectIdentity']}")


def test_a_blank_project_capabilities_agree_with_convening(
    tmp_path, monkeypatch,
):
    """The toolbar must not promise what the start route would refuse."""
    dictCapabilities = _fdictCapabilitiesForBlankProject(
        tmp_path, monkeypatch, ["theOnlyRepo"])

    assert dictCapabilities["bAvailable"] is True, (
        "a Blank Project with one tracked directory was refused a "
        f"council: {dictCapabilities.get('sReason')}")


def test_a_blank_project_with_no_tracked_directory_says_what_to_do(
    tmp_path, monkeypatch,
):
    """Refused, but with the action that fixes it."""
    dictCapabilities = _fdictCapabilitiesForBlankProject(
        tmp_path, monkeypatch, [])

    assert dictCapabilities["bAvailable"] is False
    assert "Repos panel" in dictCapabilities["sReason"]


def test_several_tracked_directories_are_offered_not_refused(
    tmp_path, monkeypatch,
):
    """Ambiguity is a QUESTION, and the first version got this wrong.

    A toolkit container tracks many repositories by design — one live
    project tracks nine — so refusing until only one is tracked told
    the researcher to break the Repos panel's actual purpose. The
    candidates are published instead, and the convene form asks.

    Still never GUESSED: silently picking one would snapshot the wrong
    codebase and every participant would reason about the wrong thing,
    with nothing in the plan to show it.
    """
    dictCapabilities = _fdictCapabilitiesForBlankProject(
        tmp_path, monkeypatch, ["alpha", "beta"])

    assert dictCapabilities["bAvailable"] is True
    assert dictCapabilities["listCandidateDirectories"] == ["alpha", "beta"]


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
    client, app, _ = tOwnerClient
    # The absence is driven at the SOURCE — the container read fails —
    # rather than by patching the probe's return value. A patched
    # boolean stops exercising the route the moment the route calls a
    # different helper, which is exactly what happened when the probe
    # learned to explain itself in prose (2026-08-24): the test kept
    # patching a function nothing called and started passing a start it
    # was written to refuse.
    dockerFake = app.state.dictRouteContext["docker"]
    monkeypatch.setattr(
        dockerFake, "fbaFetchCredentialFile",
        lambda sContainerId, sPath: (_ for _ in ()).throw(
            FileNotFoundError(sPath)))
    response = client.post(
        f"/api/agent-councils/{S_CONTAINER_ID}/start", json=DICT_START_BODY)
    assert response.status_code == 409, response.text
    assert "no persisted Claude login" in response.json()["detail"]
    assert app.state.dictCouncilCampaignStore["listInsertionOrder"] == [], (
        "a login-less start must register no campaign at all")


def test_start_refuses_a_project_whose_claude_login_has_expired(
        tOwnerClient, monkeypatch):
    """An EXPIRED login is a different refusal with a different remedy.

    A live council spent two runners on this and reported it as a
    schema-validation failure: the token had expired 38 hours earlier,
    the runner is given no refresh token, so the CLI exited without
    calling the API and every schema field was reported missing
    (2026-08-24). Asserting the remedy text, not merely the 409 —
    telling a researcher with an expired login to "log in" sends them
    somewhere that looks already done.
    """
    client, app, _ = tOwnerClient
    dockerFake = app.state.dictRouteContext["docker"]
    baExpired = json.dumps({"claudeAiOauth": {
        "accessToken": "fixture-access-token",
        "scopes": ["user:inference"],
        "expiresAt": int((time.time() - 3600) * 1000)}}).encode("utf-8")
    monkeypatch.setattr(
        dockerFake, "fbaFetchCredentialFile",
        lambda sContainerId, sPath: baExpired)
    response = client.post(
        f"/api/agent-councils/{S_CONTAINER_ID}/start", json=DICT_START_BODY)
    assert response.status_code == 409, response.text
    sDetail = response.json()["detail"]
    assert "expired" in sDetail and "refresh" in sDetail, sDetail
    assert app.state.dictCouncilCampaignStore["listInsertionOrder"] == [], (
        "an expired-login start must register no campaign at all")


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


def test_the_chosen_directory_binds_the_campaign(tmp_path, monkeypatch):
    """The researcher's pick is what the campaign is about.

    Publishing candidates is only useful if choosing one works, and the
    choice has to reach dictProjectIdentity — the snapshot and every
    participant follow from it.
    """
    from vaibify.gui import trackedReposManager
    monkeypatch.setattr(
        trackedReposManager, "fdictReadOrSeedSidecar",
        lambda connectionDocker, sContainerId: {
            "listTracked": [{"sName": "alpha"}, {"sName": "beta"}],
            "listIgnored": [],
        })
    app, _, _ = _fdictBlankProjectApp(tmp_path, ["alpha", "beta"])
    sCredential, sLease = _tEstablishOwnership(
        app, S_CONTAINER_NAME, S_CONTAINER_ID)

    with TestClient(app, headers={
        "X-Session-Token": sCredential, "X-Vaibify-Lease": sLease,
    }) as client:
        response = client.post(
            f"/api/agent-councils/{S_CONTAINER_ID}/start",
            json=dict(DICT_START_BODY, sProjectDirectory="beta"))

        assert response.status_code == 200, response.text
        dictCampaign = agentCouncilStore.fjsonGetCampaignRecord(
            app.state.dictCouncilCampaignStore,
            response.json()["sCampaignId"])
        assert dictCampaign["dictProjectIdentity"]["sProjectRepoPath"] == (
            "/workspace/beta")


def test_a_directory_outside_the_tracked_set_is_refused(
    tmp_path, monkeypatch,
):
    """The choice is validated, never trusted.

    It becomes a container path, so a basename the project does not
    track must not be joined onto the workspace root just because a
    client asked. Kills a version that took the field at its word.
    """
    from vaibify.gui import trackedReposManager
    monkeypatch.setattr(
        trackedReposManager, "fdictReadOrSeedSidecar",
        lambda connectionDocker, sContainerId: {
            "listTracked": [{"sName": "alpha"}], "listIgnored": [],
        })
    app, _, _ = _fdictBlankProjectApp(tmp_path, ["alpha"])
    sCredential, sLease = _tEstablishOwnership(
        app, S_CONTAINER_NAME, S_CONTAINER_ID)

    with TestClient(app, headers={
        "X-Session-Token": sCredential, "X-Vaibify-Lease": sLease,
    }) as client:
        response = client.post(
            f"/api/agent-councils/{S_CONTAINER_ID}/start",
            json=dict(DICT_START_BODY, sProjectDirectory="../etc"))

        assert response.status_code == 400, response.text
        assert "tracked directories" in response.text


def test_one_oversized_file_is_caught_by_the_preflight(tmp_path, monkeypatch):
    """The bound the first pre-flight forgot.

    It checked the file COUNT and the TOTAL and silently ignored the
    per-member cap, so a repository comfortably inside both still hit
    the capture's third bound after the researcher had chosen
    participants and written a question — one 85 MB data file in an
    otherwise ordinary research repo (live report, 2026-08-22).
    """
    from vaibify.gui import trackedReposManager
    monkeypatch.setattr(
        trackedReposManager, "fdictReadOrSeedSidecar",
        lambda connectionDocker, sContainerId: {
            "listTracked": [{"sName": "theOnlyRepo"}], "listIgnored": [],
        })
    app, docker, _ = _fdictBlankProjectApp(tmp_path, ["theOnlyRepo"])
    docker.dictRepositoryWeight = {
        "iFileCount": 400,
        "iTotalBytes": 100 * 1024 * 1024,
        "bTruncated": False,
        "bLargestFilesTruncated": False,
        "listLargestFiles": [
            {"sPath": "examples/SSDistOrbDistRot/marshnb/4.inv",
             "iSizeBytes": 85912419},
            {"sPath": "README.md", "iSizeBytes": 2048},
        ],
    }
    sCredential, sLease = _tEstablishOwnership(
        app, S_CONTAINER_NAME, S_CONTAINER_ID)

    with TestClient(app, headers={
        "X-Session-Token": sCredential, "X-Vaibify-Lease": sLease,
    }) as client:
        dictCapabilities = client.get(
            f"/api/agent-councils/{S_CONTAINER_ID}/capabilities").json()

    dictFeasibility = dictCapabilities["dictSnapshotFeasibility"]
    assert dictFeasibility["bFits"] is False, (
        "a repository whose largest file exceeds the member cap was "
        "reported as fitting; the refusal would arrive at convene time")
    assert [dictFile["sPath"] for dictFile in
            dictFeasibility["listOversizedFiles"]] == [
        "examples/SSDistOrbDistRot/marshnb/4.inv"], (
        "the pre-flight must name the offending file, and only it: the "
        "researcher's move is to exclude it by name")
    assert "4.inv" in dictFeasibility["sReason"]
    assert "81 MB" in dictFeasibility["sReason"]
    # Oversized files alone do NOT block the button. Blocking it would
    # hide the convene form, which is the only place the exclusion can
    # be offered — the researcher would be told what is wrong and given
    # no way to act on it.
    assert dictFeasibility["bResolvableByExcludingFiles"] is True
    assert dictCapabilities["bAvailable"] is True


def test_each_candidate_directory_can_be_weighed_on_its_own(
    tmp_path, monkeypatch,
):
    """The per-directory pre-flight, and why it is not the poll.

    A toolkit container tracks many repositories — one live project
    tracks nine — so the capabilities poll deliberately publishes the
    candidates without weighing them: nine metadata walks every few
    seconds to answer a question nobody asked. The convene form asks
    per candidate instead, and this is that route.

    Asserted with the two directories answering DIFFERENTLY, because a
    route that returned one repository's verdict for every path would
    pass any single-directory assertion.
    """
    from vaibify.gui import trackedReposManager
    monkeypatch.setattr(
        trackedReposManager, "fdictReadOrSeedSidecar",
        lambda connectionDocker, sContainerId: {
            "listTracked": [{"sName": "tidyRepo"}, {"sName": "hugeRepo"}],
            "listIgnored": [],
        })
    app, docker, _ = _fdictBlankProjectApp(
        tmp_path, ["tidyRepo", "hugeRepo"])
    dictWeightByPath = {
        "/workspace/tidyRepo": {
            "iFileCount": 12, "iTotalBytes": 4096, "bTruncated": False,
            "bLargestFilesTruncated": False,
            "listLargestFiles": [{"sPath": "a.py", "iSizeBytes": 512}],
        },
        "/workspace/hugeRepo": {
            "iFileCount": 900, "iTotalBytes": 200 * 1024 * 1024,
            "bTruncated": False, "bLargestFilesTruncated": False,
            "listLargestFiles": [
                {"sPath": "data/cube.npy", "iSizeBytes": 90 * 1024 * 1024}],
        },
    }
    docker.fdictWeighRepository = (
        lambda sContainerId, sPath: dict(dictWeightByPath[sPath]))
    sCredential, sLease = _tEstablishOwnership(
        app, S_CONTAINER_NAME, S_CONTAINER_ID)

    with TestClient(app, headers={
        "X-Session-Token": sCredential, "X-Vaibify-Lease": sLease,
    }) as client:
        sRoute = f"/api/agent-councils/{S_CONTAINER_ID}/snapshot-feasibility"
        dictTidy = client.get(
            sRoute, params={"sProjectDirectory": "tidyRepo"}).json()
        dictHuge = client.get(
            sRoute, params={"sProjectDirectory": "hugeRepo"}).json()
        responseForeign = client.get(
            sRoute, params={"sProjectDirectory": "notTracked"})

    assert dictTidy["bFits"] is True
    assert dictTidy["listOversizedFiles"] == []
    assert dictHuge["bFits"] is False
    assert dictHuge["bResolvableByExcludingFiles"] is True
    assert [dictFile["sPath"] for dictFile
            in dictHuge["listOversizedFiles"]] == ["data/cube.npy"]
    assert responseForeign.status_code == 400, (
        "a directory this project does not track was weighed; the "
        "basename becomes a container path and must be validated")


def test_convene_forwards_the_researchers_exclusions_to_the_capture(
    tmp_path, monkeypatch,
):
    """The exclusions must actually REACH the capture.

    Kills: dropping listExcludedPaths between the request body and
    fdictCaptureProjectContextSnapshot.

    Every layer of this feature can be correct and the researcher still
    blocked, if the one wire between the form and the capture is not
    connected — and no unit test of either end can see that.
    """
    from vaibify.gui import trackedReposManager
    monkeypatch.setattr(
        trackedReposManager, "fdictReadOrSeedSidecar",
        lambda connectionDocker, sContainerId: {
            "listTracked": [{"sName": "theOnlyRepo"}], "listIgnored": [],
        })
    app, _, _ = _fdictBlankProjectApp(tmp_path, ["theOnlyRepo"])
    listSeenExclusions = []

    def _fdictRecordingCapture(*tArguments, **dictKeywords):
        listSeenExclusions.append(dictKeywords.get("listExcludedPaths"))
        return _fdictWriteFixtureSnapshot(*tArguments, **dictKeywords)

    monkeypatch.setattr(
        agentCouncilContext, "fdictCaptureProjectContextSnapshot",
        _fdictRecordingCapture)
    sCredential, sLease = _tEstablishOwnership(
        app, S_CONTAINER_NAME, S_CONTAINER_ID)

    with TestClient(app, headers={
        "X-Session-Token": sCredential, "X-Vaibify-Lease": sLease,
    }) as client:
        response = client.post(
            f"/api/agent-councils/{S_CONTAINER_ID}/start",
            json={**DICT_START_BODY,
                  "listExcludedPaths": ["data/cube.npy"]})
        assert response.status_code == 200, response.text

    assert listSeenExclusions == [["data/cube.npy"]], (
        "the researcher's exclusions never reached the capture; the "
        "convene form would tick a box that does nothing")


def test_snapshot_feasibility_refuses_the_agent_token_lane(tmp_path):
    """It reads the project's repository shape, so the agent is refused.

    Same reasoning as the capabilities read beside it: an in-container
    agent that can enumerate what a project holds and how large it is
    has been handed a survey of the researcher's machine it was never
    granted, and the catalog cannot express that capability on its own.
    """
    app = _fnBuildAppWithTmpStore(tmp_path)
    _tEstablishOwnership(app, S_CONTAINER_NAME, S_CONTAINER_ID)
    clientAgent = TestClient(app, headers={
        actionCatalog.S_SESSION_HEADER_NAME: S_AGENT_TOKEN,
        "Host": "host.docker.internal:8050",
    })
    response = clientAgent.get(
        f"/api/agent-councils/{S_CONTAINER_ID}/snapshot-feasibility")
    assert response.status_code == 403, response.text


# ── ask the chairbot: the conversation lane's gates ───────────────

def _fnPatchChatGatewayForRoutes(monkeypatch, dictRecorded):
    """Answer the chat lane's gateway calls without a daemon.

    The chat module's own suite (tests/testCouncilChat.py) proves what
    the lane DOES. These route tests prove only what the HTTP skin
    enforces before it gets there, so the gateway is reduced to the
    minimum that lets an open succeed — and every call is recorded, so
    a gate that failed open would show as a runner nobody authorized.
    """
    from vaibify.gui import agentCouncilChat, agentCouncilDockerGateway
    monkeypatch.setattr(
        agentCouncilDockerGateway, "fdockerCreateCouncilClient",
        lambda *args, **kwargs: object())
    # NOT the gateway factory: the campaign read route builds a
    # registry-only view through the same function, so replacing it
    # would break a route this suite is not testing.
    monkeypatch.setattr(
        agentCouncilDockerGateway, "fsCreateCampaignInternalNetwork",
        lambda dictGateway, sScope: "vaibifyCouncilEgress-fake")
    monkeypatch.setattr(
        agentCouncilDockerGateway, "fsLaunchAllowlistProxy",
        lambda dictGateway, sScope, saHostnames: "172.30.0.2")
    monkeypatch.setattr(
        agentCouncilDockerGateway, "fdictRemoveCampaignEgressResources",
        lambda dictGateway, sScope: {
            "bProxyAbsenceProven": True, "bNetworkAbsenceProven": True,
            "saIndeterminateResources": []})
    monkeypatch.setattr(
        agentCouncilDockerGateway, "fdictReserveAndCreateRunner",
        lambda *args, **kwargs: dictRecorded.setdefault(
            "listRunners", []).append(args[1]) or {
            "bCreated": True, "sRefusalReason": "", "sHandle": "h",
            "sReservationId": "r", "sContainerName": "n", "sRole": "runner"})
    monkeypatch.setattr(
        agentCouncilDockerGateway, "fnCopySnapshotIntoRunner",
        lambda *args, **kwargs: None)
    monkeypatch.setattr(
        agentCouncilDockerGateway, "fdictDestroyAndSettle",
        lambda dictGateway, sHandle: {
            "sOutcome": "destroyed", "sReason": ""})
    monkeypatch.setattr(
        agentCouncilChat, "_fnDeliverChatCredential", lambda dictSession: None)


def test_chat_refuses_the_agent_token_lane_on_every_mutating_route(tmp_path):
    """A compromised agent must not be able to spend the subscription.

    Every chat message is a paid provider turn over a copy of the
    researcher's own login, so an agent that could open a conversation
    could spend it in a loop. The catalog exclusion and the handler's
    own refusal have to agree, and this drives the real middleware.
    """
    app = _fnBuildAppWithTmpStore(tmp_path)
    _tEstablishOwnership(app, S_CONTAINER_NAME, S_CONTAINER_ID)
    clientAgent = TestClient(app, headers={
        actionCatalog.S_SESSION_HEADER_NAME: S_AGENT_TOKEN,
        "Host": "host.docker.internal:8050",
    })
    sBase = f"/api/agent-councils/{S_CONTAINER_ID}/campaign-x/chat"

    assert clientAgent.post(sBase + "/open").status_code == 403
    assert clientAgent.post(
        sBase + "/ask", json={"sQuestionText": "hi"}).status_code == 403
    assert clientAgent.post(sBase + "/close").status_code == 403
    assert clientAgent.get(sBase).status_code == 403


@pytest.mark.falsification
def test_chat_routes_refuse_a_campaign_bound_to_another_principal(
        tOwnerClient, eventTurnGate):
    """A foreign campaign answers 404 — the same answer an unknown id gets.

    Kills: the read route skipping the principal match.
    """
    client, app, _ = tOwnerClient
    sCampaignId = _sStartOneCampaign(client)
    eventTurnGate.set()
    _fnWaitForNoLiveCouncilWork(app)
    dictRecord = app.state.dictCouncilCampaignStore["dictEntriesById"][
        sCampaignId]["dictCampaign"]
    dictRecord["dictProjectIdentity"]["sResourceName"] = "another-project"

    sBase = f"/api/agent-councils/{S_CONTAINER_ID}/{sCampaignId}/chat"
    assert client.get(sBase).status_code == 404
    assert client.post(sBase + "/open").status_code == 404


def test_chat_read_reports_a_closed_conversation_rather_than_404(
        tOwnerClient, eventTurnGate):
    """The panel polls this; "nothing open" must not read as a failure."""
    client, app, _ = tOwnerClient
    sCampaignId = _sStartOneCampaign(client)
    eventTurnGate.set()
    _fnWaitForNoLiveCouncilWork(app)

    response = client.get(
        f"/api/agent-councils/{S_CONTAINER_ID}/{sCampaignId}/chat")

    assert response.status_code == 200, response.text
    assert response.json()["bOpen"] is False


@pytest.mark.falsification
def test_chat_open_refuses_a_project_with_no_claude_login(
        tOwnerClient, eventTurnGate, monkeypatch):
    """Opening builds a runner, so it passes start's login probe first.

    Discovering an absent login after a container has been created and
    destroyed would report a failed conversation where "log in" is the
    truth.

    Kills: open skipping the login probe.
    """
    client, app, _ = tOwnerClient
    dictRecorded = {}
    _fnPatchChatGatewayForRoutes(monkeypatch, dictRecorded)
    sCampaignId = _sStartOneCampaign(client)
    eventTurnGate.set()
    _fnWaitForNoLiveCouncilWork(app)
    from vaibify.gui import agentCouncilProviders
    monkeypatch.setattr(
        agentCouncilProviders, "fsExplainUnusableRunnerCredential",
        lambda *args: "the project's Claude login expired 3.0 hours ago")

    response = client.post(
        f"/api/agent-councils/{S_CONTAINER_ID}/{sCampaignId}/chat/open")

    assert response.status_code == 409, response.text
    assert "expired" in response.json()["detail"]
    assert dictRecorded.get("listRunners") is None, (
        "a refused open must build no runner at all")


def test_chat_open_and_close_over_real_http(
        tOwnerClient, eventTurnGate, monkeypatch):
    """The falsification twin: the gates admit a legitimate conversation.

    Without this every refusal above is equally satisfied by a lane
    that refuses everything.
    """
    client, app, _ = tOwnerClient
    dictRecorded = {}
    _fnPatchChatGatewayForRoutes(monkeypatch, dictRecorded)
    sCampaignId = _sStartOneCampaign(client)
    eventTurnGate.set()
    _fnWaitForNoLiveCouncilWork(app)
    sBase = f"/api/agent-councils/{S_CONTAINER_ID}/{sCampaignId}/chat"

    responseOpen = client.post(sBase + "/open")
    assert responseOpen.status_code == 200, responseOpen.text
    assert responseOpen.json()["bOpen"] is True
    assert dictRecorded["listRunners"] == [sCampaignId]

    responseClose = client.post(sBase + "/close")
    assert responseClose.status_code == 200, responseClose.text
    assert client.get(sBase).json()["bOpen"] is False


@pytest.mark.falsification
def test_chat_open_refuses_once_the_lease_was_released(
        tOwnerClient, eventTurnGate, monkeypatch):
    """A released project's council must not be talked to.

    The release authority CLOSES council admission for the container
    before dropping the lease. A conversation is not a controller
    command, so it has to honour that gate explicitly or it becomes the
    one way to spend paid work against a project this hub gave up.

    Kills: open ignoring closed council admission.
    """
    client, app, _ = tOwnerClient
    _fnPatchChatGatewayForRoutes(monkeypatch, {})
    sCampaignId = _sStartOneCampaign(client)
    eventTurnGate.set()
    _fnWaitForNoLiveCouncilWork(app)
    agentCouncilController.fbCloseResourceAdmission(
        app.state.dictCouncilControllerState, S_CONTAINER_NAME)

    sBase = f"/api/agent-councils/{S_CONTAINER_ID}/{sCampaignId}/chat"
    responseOpen = client.post(sBase + "/open")

    assert responseOpen.status_code == 409, responseOpen.text
    assert "lease was released" in responseOpen.json()["detail"]


@pytest.mark.falsification
def test_chat_close_still_works_once_the_lease_was_released(
        tOwnerClient, eventTurnGate, monkeypatch):
    """Closing is the one chat action a released project must keep.

    Close is how a researcher makes a released project releasable, so
    gating it on admission would put the only exit behind the gate it
    opens.

    Kills: the twin that gates close on admission like open.
    """
    client, app, _ = tOwnerClient
    _fnPatchChatGatewayForRoutes(monkeypatch, {})
    sCampaignId = _sStartOneCampaign(client)
    eventTurnGate.set()
    _fnWaitForNoLiveCouncilWork(app)
    agentCouncilController.fbCloseResourceAdmission(
        app.state.dictCouncilControllerState, S_CONTAINER_NAME)

    sBase = f"/api/agent-councils/{S_CONTAINER_ID}/{sCampaignId}/chat"
    assert client.post(sBase + "/close").status_code == 200


# ── every campaign-scoped route can be told its directory ────────

T_CAMPAIGN_SCOPED_ACTIONS = (
    ("POST", "/respond", {"sResponseText": "the content-hash policy"}),
    ("POST", "/request-stop", None),
    ("POST", "/grant-resolution-round", {"iGrantedRounds": 1}),
    ("POST", "/resolve-objections", {"dictDispositionByObjectionId": {}}),
    ("POST", "/reject-candidate", {"sReasonText": "not now"}),
    ("POST", "/accept-plan", None),
    ("POST", "/chat/open", None),
    ("POST", "/chat/ask", {"sQuestionText": "why?"}),
    ("POST", "/chat/close", None),
    ("DELETE", "", None),
)


@pytest.mark.parametrize("sMethod,sSuffix,dictBody", T_CAMPAIGN_SCOPED_ACTIONS)
@pytest.mark.falsification
def test_every_campaign_action_accepts_a_chosen_directory(
        tmp_path, monkeypatch, sMethod, sSuffix, dictBody):
    """A project tracking several directories must still be ANSWERABLE.

    The 2026-08-24 fix taught the READ routes to accept the directory
    and stopped there, so a researcher on a toolkit container with no
    workflow open could watch a council perfectly well and not answer
    it: every action refused with "a council needs to be told which one
    it is about". Reported live on 2026-08-25 against the chat's open,
    which was simply the first of the ten anyone clicked.

    The assertion is deliberately NOT "200": each of these has its own
    lifecycle preconditions and most will refuse a freshly-started
    campaign for reasons of their own. What must never happen is the
    DIRECTORY refusal, because that one is not about the campaign at
    all — it says the server could not tell which repository was meant
    after being told.

    Kills: the respond route never forwarding its directory to the
    resolver.
    """
    app = _fnBuildAppWithTmpStore(tmp_path)
    # A toolkit container: several tracked repositories, no open
    # workflow, so nothing but the query parameter can disambiguate.
    app.state.dictRouteContext["workflows"].pop(S_CONTAINER_ID, None)
    monkeypatch.setattr(
        councilRouteGuards, "flistTrackedDirectoryNames",
        lambda dictCtx, sContainerId: ["vplanet", "vplot", "vspace"])
    sCredential, sLease = _tEstablishOwnership(
        app, S_CONTAINER_NAME, S_CONTAINER_ID)

    with TestClient(app, headers={
        "X-Session-Token": sCredential, "X-Vaibify-Lease": sLease,
    }) as client:
        sUrl = (f"/api/agent-councils/{S_CONTAINER_ID}/campaign-any"
                f"{sSuffix}?sProjectDirectory=vplanet")
        response = client.request(sMethod, sUrl, json=dictBody)

    assert "needs to be told which one" not in response.text, (
        f"{sMethod} {sSuffix} refused a directory it was explicitly "
        f"given: {response.text[:200]}")


@pytest.mark.falsification
def test_a_campaign_action_still_refuses_an_untracked_directory(tmp_path,
                                                                monkeypatch):
    """The falsification twin: the value is validated, never trusted.

    It becomes a container path, so a basename this project does not
    track must be refused rather than joined onto the workspace root.

    Kills: dropping the untracked-directory guard.
    """
    app = _fnBuildAppWithTmpStore(tmp_path)
    app.state.dictRouteContext["workflows"].pop(S_CONTAINER_ID, None)
    monkeypatch.setattr(
        councilRouteGuards, "flistTrackedDirectoryNames",
        lambda dictCtx, sContainerId: ["vplanet", "vplot"])
    sCredential, sLease = _tEstablishOwnership(
        app, S_CONTAINER_NAME, S_CONTAINER_ID)

    with TestClient(app, headers={
        "X-Session-Token": sCredential, "X-Vaibify-Lease": sLease,
    }) as client:
        response = client.post(
            f"/api/agent-councils/{S_CONTAINER_ID}/campaign-any/respond"
            "?sProjectDirectory=../etc",
            json={"sResponseText": "x"})

    assert response.status_code == 400, response.text
    assert "tracked directories" in response.text
