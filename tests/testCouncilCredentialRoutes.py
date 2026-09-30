"""The consent routes carry all four authorization layers on every route.

Contract A3 of the credential-consent plan. Every route — the two
mutations, the cancel, and the two reads — is driven over real HTTP
through the real app, with the container NAME distinct from its ID, and
each layer is proven on its own:

1. no browser credential is refused;
2. a browser credential without the container's lease, or holding
   ANOTHER session's lease, is refused;
3. the mutations and the cancel are catalog-excluded, so the agent lane
   is refused by the middleware even with the handler's own check off;
4. every handler refuses the agent-token lane itself, reads included —
   the one layer that stands alone for a GET, because the middleware
   admits agent reads and the agent's token satisfies the lease layer.

A refused request writes nothing: no consent, no outcome, no job.
"""

import json
import os

import pytest
from fastapi.testclient import TestClient
from unittest.mock import patch

from vaibify.config import registryManager
from vaibify.gui import (
    actionCatalog,
    agentCouncilCredentialGate,
    agentCouncilCredentialStore,
    agentCouncilCredentialTest,
    agentCouncilCredentialTestRecords,
    agentCouncilDockerGateway,
    browserSession,
    containerOwnership,
    councilRouteGuards,
    pipelineServer,
)
from tests.sessionTokenTestHelper import fsBootstrapCredential
from tests.testCouncilRoutes import (
    MockDockerCouncil,
    S_CONTAINER_ID,
    S_CONTAINER_NAME,
    S_IMAGE_IDENTITY,
)

S_AGENT_TOKEN = "agent-token-credential-routes"
S_PREFIX = f"/api/council-credentials/{S_CONTAINER_ID}"


@pytest.fixture(autouse=True)
def sEvidencePath(tmp_path, monkeypatch):
    sPath = str(tmp_path / "agentCouncils" / "credentialEvidence.json")
    monkeypatch.setattr(
        agentCouncilCredentialGate, "fsResolveCredentialEvidencePath",
        lambda: sPath)
    sRegistryDirectory = str(tmp_path / ".vaibify-registry")
    monkeypatch.setattr(
        registryManager, "_S_REGISTRY_DIRECTORY", sRegistryDirectory)
    monkeypatch.setattr(
        registryManager, "_S_REGISTRY_PATH",
        os.path.join(sRegistryDirectory, "registry.json"))
    return sPath


@pytest.fixture(autouse=True)
def fixtureJobsRunNothing(monkeypatch):
    """A started job records its marker and ends at once, spending nothing."""
    monkeypatch.setattr(agentCouncilDockerGateway,
                        "fdockerCreateCouncilClient", lambda: object())

    def _fnFinishImmediately(dictJob, dictRuntime):
        dictRuntime.pop("fileJobLock").close()

    monkeypatch.setattr(agentCouncilCredentialTest, "fnRunCredentialTestJob",
                        _fnFinishImmediately)


class AppHarness:
    """The real app, an owned container, and two browser sessions."""

    def __init__(self):
        with patch.object(pipelineServer, "_fconnectionCreateDocker",
                          MockDockerCouncil):
            self.app = pipelineServer.fappCreateApplication(
                sWorkspaceRoot="/workspace", sTerminalUserArg="testuser")
        self.sCredential = fsBootstrapCredential(self.app)
        self.sOtherCredential = fsBootstrapCredential(self.app)
        self.sLease = containerOwnership.fsMintLease()
        self.app.state.dictContainerOwners[S_CONTAINER_NAME] = (
            containerOwnership.OwnerRecord(
                sLeaseId=self.sLease, fileHandleLock=None,
                sAgentToken=S_AGENT_TOKEN, sContainerId=S_CONTAINER_ID,
                sBrowserSessionId=browserSession.fsSessionIdForCredential(
                    self.app.state.dictBrowserSessions, self.sCredential)))

    def fclientOwner(self):
        return TestClient(self.app, headers={
            "X-Session-Token": self.sCredential,
            "X-Vaibify-Lease": self.sLease})

    def fclientWithoutLease(self):
        return TestClient(self.app, headers={
            "X-Session-Token": self.sCredential})

    def fclientOtherSessionWithStolenLease(self):
        return TestClient(self.app, headers={
            "X-Session-Token": self.sOtherCredential,
            "X-Vaibify-Lease": self.sLease})

    def fclientAgent(self):
        return TestClient(self.app, headers={
            actionCatalog.S_SESSION_HEADER_NAME: S_AGENT_TOKEN})

    def fclientAnonymous(self):
        return TestClient(self.app)


@pytest.fixture
def harness():
    return AppHarness()


S_JOB_ID = "0123456789abcdef0123456789abcdef"

LIST_ROUTES = [
    ("POST", f"{S_PREFIX}/credential-test",
     {"listProviders": [{"sProvider": "claude"}]}),
    ("GET", f"{S_PREFIX}/credential-test/{S_JOB_ID}", None),
    ("POST", f"{S_PREFIX}/credential-test/{S_JOB_ID}/cancel", None),
    ("DELETE", f"{S_PREFIX}/credential-consent/claude", None),
    ("GET", f"{S_PREFIX}/panel", None),
]


def _fresponseSend(clientHttp, sMethod, sPath, jsonBody):
    return clientHttp.request(sMethod, sPath, json=jsonBody)


def _fnAssertNothingWritten(sEvidencePath):
    assert not os.path.exists(sEvidencePath), (
        "a refused request wrote to the credential document")
    assert not os.path.isdir(
        agentCouncilCredentialTestRecords.fsResolveJobRecordDirectory()), (
        "a refused request created a job record")


# ----- the owner path works --------------------------------------------------------


def test_the_owner_consents_reads_progress_and_the_panel(
        harness, sEvidencePath):
    clientOwner = harness.fclientOwner()
    responseStart = clientOwner.post(
        f"{S_PREFIX}/credential-test",
        json={"listProviders": [{"sProvider": "claude"}]})
    assert responseStart.status_code == 200, responseStart.text
    dictStarted = responseStart.json()["listJobs"][0]
    assert dictStarted["sProvider"] == "claude"
    sJobId = dictStarted["sJobId"]
    assert sJobId != "claude"
    responseJob = clientOwner.get(f"{S_PREFIX}/credential-test/{sJobId}")
    assert responseJob.status_code == 200, responseJob.text
    assert "listStagedPaths" not in responseJob.json()
    responsePanel = clientOwner.get(f"{S_PREFIX}/panel")
    assert responsePanel.status_code == 200, responsePanel.text
    dictPanel = responsePanel.json()
    assert dictPanel["sImageIdentity"] == S_IMAGE_IDENTITY
    assert dictPanel["listProjectsSharingImage"] == [S_CONTAINER_NAME]
    dictClaude = next(dictProvider for dictProvider
                      in dictPanel["listProviders"]
                      if dictProvider["sProvider"] == "claude")
    assert dictClaude["sConsentState"] == "active"
    assert dictClaude["sRunningJobId"] == sJobId


def test_the_image_comes_from_the_container_never_the_request(
        harness, sEvidencePath):
    harness.fclientOwner().post(
        f"{S_PREFIX}/credential-test",
        json={"listProviders": [{"sProvider": "claude"}],
              "sImageIdentity": "sha256:" + "ff" * 32})
    dictDocument = json.load(open(sEvidencePath))
    assert list(dictDocument["dictConsents"]) == [
        agentCouncilCredentialStore.fsComposeCredentialKey(
            "claude", S_IMAGE_IDENTITY)]


def test_withdraw_answers_with_the_providers_new_state(
        harness, sEvidencePath):
    clientOwner = harness.fclientOwner()
    clientOwner.post(f"{S_PREFIX}/credential-test",
                     json={"listProviders": [{"sProvider": "claude"}]})
    responseWithdraw = clientOwner.delete(
        f"{S_PREFIX}/credential-consent/claude")
    assert responseWithdraw.status_code == 200, responseWithdraw.text
    assert responseWithdraw.json()["bWithdrawn"] is True
    assert responseWithdraw.json()["dictProvider"]["sState"] == "withdrawn"


def test_an_unknown_provider_and_a_modelless_provider_are_refused(
        harness, sEvidencePath):
    clientOwner = harness.fclientOwner()
    for dictBody in ({"listProviders": [{"sProvider": "notAProvider"}]},
                     {"listProviders": [{"sProvider": "codex"}]}):
        response = clientOwner.post(f"{S_PREFIX}/credential-test",
                                    json=dictBody)
        assert response.status_code in (400, 422), response.text
    _fnAssertNothingWritten(sEvidencePath)


def test_a_job_of_another_container_reads_as_unknown(harness, sEvidencePath):
    dictJob = agentCouncilCredentialTestRecords.fdictCreateJobRecord(
        S_JOB_ID, "claude", S_IMAGE_IDENTITY, "anotherProject",
        "anothercontainerid", "haiku")
    agentCouncilCredentialTestRecords.fnWriteJobRecord(dictJob)
    response = harness.fclientOwner().get(
        f"{S_PREFIX}/credential-test/{S_JOB_ID}")
    assert response.status_code == 404


# ----- every route, every refusal ---------------------------------------------------


@pytest.mark.parametrize("sMethod,sPath,jsonBody", LIST_ROUTES)
def test_no_browser_credential_is_refused(harness, sEvidencePath, sMethod,
                                          sPath, jsonBody):
    response = _fresponseSend(harness.fclientAnonymous(), sMethod, sPath,
                              jsonBody)
    assert response.status_code in (401, 403), response.text
    _fnAssertNothingWritten(sEvidencePath)


@pytest.mark.falsification
@pytest.mark.parametrize("sMethod,sPath,jsonBody", LIST_ROUTES)
def test_a_missing_lease_is_refused(harness, sEvidencePath, sMethod, sPath,
                                    jsonBody):
    """Layer 2: a valid browser credential alone does not reach a route.

    Kills: the lease authority admitting a browser session that holds
    no lease.
    """
    response = _fresponseSend(harness.fclientWithoutLease(), sMethod, sPath,
                              jsonBody)
    assert response.status_code == 403, response.text
    _fnAssertNothingWritten(sEvidencePath)


@pytest.mark.parametrize("sMethod,sPath,jsonBody", LIST_ROUTES)
def test_another_sessions_lease_is_refused(harness, sEvidencePath, sMethod,
                                           sPath, jsonBody):
    response = _fresponseSend(harness.fclientOtherSessionWithStolenLease(),
                              sMethod, sPath, jsonBody)
    assert response.status_code == 403, response.text
    _fnAssertNothingWritten(sEvidencePath)


@pytest.mark.parametrize("sMethod,sPath,jsonBody", LIST_ROUTES)
def test_the_agent_lane_is_refused_with_a_real_token(
        harness, sEvidencePath, sMethod, sPath, jsonBody):
    """A valid agent token for THIS container is still refused everywhere."""
    response = _fresponseSend(harness.fclientAgent(), sMethod, sPath,
                              jsonBody)
    assert response.status_code == 403, response.text
    _fnAssertNothingWritten(sEvidencePath)


# ----- each layer alone ---------------------------------------------------------------


@pytest.mark.falsification
@pytest.mark.parametrize("sPath", [
    f"{S_PREFIX}/panel", f"{S_PREFIX}/credential-test/{S_JOB_ID}"])
def test_the_handler_alone_refuses_an_agent_read(harness, sEvidencePath,
                                                 sPath):
    """Layer 4 standing alone: the middleware admits agent GETs, and the
    agent's token satisfies the lease layer, so only the handler's own
    refusal keeps the researcher's consent state from the agent.

    Kills: the shared council guard no longer refusing the agent lane.
    """
    assert actionCatalog.fbAgentLanePermitsRoute(
        "GET", sPath.replace(S_CONTAINER_ID, "{sContainerId}")
        .replace(S_JOB_ID, "{sJobId}"))
    response = harness.fclientAgent().get(sPath)
    assert response.status_code == 403, response.text
    assert "agent" in response.json()["detail"]


@pytest.mark.falsification
@pytest.mark.parametrize("sMethod,sPath,jsonBody", [
    tRoute for tRoute in LIST_ROUTES if tRoute[0] != "GET"])
def test_the_middleware_alone_refuses_agent_mutations(
        harness, sEvidencePath, monkeypatch, sMethod, sPath, jsonBody):
    """Layer 3 standing alone: with every handler's own refusal off, the
    catalog exclusion still refuses the agent before a handler runs.

    Kills: a consent mutation dropped from the human-only exclusions.
    """
    monkeypatch.setattr(councilRouteGuards, "fnRejectAgentTokenLane",
                        lambda requestHttp: None)
    sTemplate = (sPath.replace(S_CONTAINER_ID, "{sContainerId}")
                 .replace(S_JOB_ID, "{sJobId}")
                 .replace("/claude", "/{sProvider}"))
    assert (sMethod, sTemplate) in (
        actionCatalog.SET_INTENTIONALLY_EXCLUDED_PATHS)
    response = _fresponseSend(harness.fclientAgent(), sMethod, sPath,
                              jsonBody)
    assert response.status_code == 403, response.text
    _fnAssertNothingWritten(sEvidencePath)


def test_every_new_path_carries_the_container_id_for_the_lease():
    """Layer 2 applies by convention to a ``{sContainerId}`` path only."""
    from vaibify.gui import routeScope
    for sMethod, sPath, _ in LIST_ROUTES:
        sTemplate = (sPath.replace(S_CONTAINER_ID, "{sContainerId}")
                     .replace(S_JOB_ID, "{sJobId}")
                     .replace("/claude", "/{sProvider}"))
        assert "{sContainerId}" in sTemplate
        if sMethod == "GET":
            assert (sMethod, sTemplate) in routeScope.SET_CONTAINER_READ_ROUTES
