"""The council button's readiness is derived from facts, and start re-checks.

Contract C of the credential-consent plan. ``sCouncilReadiness`` names
what a click should open; ``bAvailable`` keeps meaning "can start now".
The pure table is tested row by row, and the capabilities route is
driven over real HTTP with the REAL gate reading a real (temporary)
credential document, so a state that looks right for the wrong reason
fails. Start re-validates on its own: a readiness answer can never be
used to skip a gate by calling start directly.
"""

import os

import pytest
from fastapi.testclient import TestClient
from unittest.mock import patch

from vaibify.config import registryManager
from vaibify.gui import (
    agentCouncilContext,
    agentCouncilCredentialGate,
    agentCouncilCredentialStore,
    agentCouncilReadiness,
    agentCouncilStore,
    browserSession,
    containerOwnership,
    pipelineServer,
)
from tests.sessionTokenTestHelper import fsBootstrapCredential
from tests.testCouncilRoutes import (
    MockDockerCouncil,
    S_CONTAINER_ID,
    S_CONTAINER_NAME,
    S_IMAGE_IDENTITY,
    S_PROJECT_REPO,
    _fdictWriteFixtureSnapshot,
)


@pytest.mark.parametrize(
    "bAuthorized,bLogin,bChoice,sMarker,sExpected", [
        (True, True, False, "", "ready"),
        (False, True, False, "", "needsCredentialTest"),
        (True, True, True, "", "needsSnapshotChoice"),
        (False, True, True, "", "needsBoth"),
        (False, False, False, "", "blocked"),
        (True, True, False, "host-mode", "blocked"),
        (False, True, True, "snapshot-too-large", "blocked"),
    ])
def test_the_readiness_table(bAuthorized, bLogin, bChoice, sMarker,
                             sExpected):
    dictAnswer = agentCouncilReadiness.fdictComposeCouncilReadiness(
        bAuthorized, bLogin, bChoice, sMarker, "the wall")
    assert dictAnswer["sCouncilReadiness"] == sExpected
    if sExpected == "blocked":
        assert dictAnswer["sReadinessReason"]


@pytest.mark.falsification
def test_a_credential_marker_is_a_consent_not_a_wall():
    """The credential gate is the one shut gate a click can open.

    Kills: the credential marker added to the blocking markers, which
    would grey the button for every researcher who has not consented.
    """
    dictCapabilities = {
        "bAvailable": False, "sUnavailableIn": "credential-evidence",
        "sReason": "no credential test has been run",
        "listProviders": [{"sProvider": "claude", "bHasProjectLogin": True}],
    }
    agentCouncilReadiness.fnApplyCouncilReadiness(dictCapabilities, False)
    assert dictCapabilities["sCouncilReadiness"] == "needsCredentialTest"
    assert dictCapabilities["bAvailable"] is False


# ----- over real HTTP, the real gate ---------------------------------------------


@pytest.fixture
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


def _tBuildClient(tmp_path, monkeypatch):
    monkeypatch.setattr(
        agentCouncilContext, "fdictCaptureProjectContextSnapshot",
        _fdictWriteFixtureSnapshot)
    with patch.object(pipelineServer, "_fconnectionCreateDocker",
                      MockDockerCouncil):
        app = pipelineServer.fappCreateApplication(
            sWorkspaceRoot="/workspace", sTerminalUserArg="testuser")
    app.state.dictCouncilCampaignStore = (
        agentCouncilStore.fdictCreateCampaignStore(
            sDurableStoreRoot=str(tmp_path / "councils")))
    app.state.dictRouteContext["workflows"][S_CONTAINER_ID] = {
        "sProjectRepoPath": S_PROJECT_REPO}
    sCredential = fsBootstrapCredential(app)
    sLease = containerOwnership.fsMintLease()
    app.state.dictContainerOwners[S_CONTAINER_NAME] = (
        containerOwnership.OwnerRecord(
            sLeaseId=sLease, fileHandleLock=None, sAgentToken="tok",
            sContainerId=S_CONTAINER_ID,
            sBrowserSessionId=browserSession.fsSessionIdForCredential(
                app.state.dictBrowserSessions, sCredential)))
    return app, TestClient(app, headers={
        "X-Session-Token": sCredential, "X-Vaibify-Lease": sLease})


def _fnAuthorize(sEvidencePath, sProvider="claude"):
    agentCouncilCredentialStore.fdictRecordConsent(
        sEvidencePath, sProvider, S_IMAGE_IDENTITY)
    agentCouncilCredentialStore.fdictBeginCredentialTest(
        sEvidencePath, sProvider, S_IMAGE_IDENTITY, "a" * 32)
    agentCouncilCredentialStore.fdictPublishCredentialTestOutcome(
        sEvidencePath, sProvider, S_IMAGE_IDENTITY, "a" * 32, "passed")


def _fdictCapabilities(clientHttp):
    response = clientHttp.get(
        f"/api/agent-councils/{S_CONTAINER_ID}/capabilities")
    assert response.status_code == 200, response.text
    return response.json()


def test_no_consent_with_a_login_needs_a_credential_test(
        tmp_path, monkeypatch, sEvidencePath):
    _, clientHttp = _tBuildClient(tmp_path, monkeypatch)
    dictCapabilities = _fdictCapabilities(clientHttp)
    assert dictCapabilities["sCouncilReadiness"] == "needsCredentialTest"
    assert dictCapabilities["bAvailable"] is False
    dictClaude = next(dictProvider for dictProvider
                      in dictCapabilities["listProviders"]
                      if dictProvider["sProvider"] == "claude")
    assert dictClaude["bHasProjectLogin"] is True
    assert dictClaude["sCredentialState"] == "noConsent"


def test_the_size_check_runs_before_any_consent(
        tmp_path, monkeypatch, sEvidencePath):
    """The free question is asked first (plan section D)."""
    _, clientHttp = _tBuildClient(tmp_path, monkeypatch)
    assert "dictSnapshotFeasibility" in _fdictCapabilities(clientHttp)


def test_a_passed_test_is_ready(tmp_path, monkeypatch, sEvidencePath):
    _fnAuthorize(sEvidencePath)
    _, clientHttp = _tBuildClient(tmp_path, monkeypatch)
    dictCapabilities = _fdictCapabilities(clientHttp)
    assert dictCapabilities["sCouncilReadiness"] == "ready"
    assert dictCapabilities["bAvailable"] is True


def test_start_re_validates_after_a_withdrawal(
        tmp_path, monkeypatch, sEvidencePath):
    """Ready a moment ago is not ready now: start asks the gate itself."""
    _fnAuthorize(sEvidencePath)
    _, clientHttp = _tBuildClient(tmp_path, monkeypatch)
    assert _fdictCapabilities(clientHttp)["sCouncilReadiness"] == "ready"
    agentCouncilCredentialStore.fdictWithdrawConsent(
        sEvidencePath, "claude", S_IMAGE_IDENTITY)
    response = clientHttp.post(
        f"/api/agent-councils/{S_CONTAINER_ID}/start",
        json={"sQuestion": "anything", "listParticipants": [
            {"sProvider": "claude", "sRequestedModel": "modelOne"},
            {"sProvider": "claude", "sRequestedModel": "modelTwo"}]})
    assert response.status_code == 409, response.text
    assert "withdrew" in response.json()["detail"]


def test_a_host_project_is_blocked(tmp_path, monkeypatch, sEvidencePath):
    from vaibify.gui.routes import councilRoutes
    dictAnswer = councilRoutes._fdictHostModeCapabilities()
    assert dictAnswer["sCouncilReadiness"] == "blocked"
    assert dictAnswer["bAvailable"] is False


def test_an_oversized_repository_is_blocked_even_with_a_pass(
        tmp_path, monkeypatch, sEvidencePath):
    _fnAuthorize(sEvidencePath)
    app, clientHttp = _tBuildClient(tmp_path, monkeypatch)
    app.state.dictRouteContext["docker"].dictRepositoryWeight.update({
        "iFileCount": 10_000_000, "iTotalBytes": 10 ** 12})
    dictCapabilities = _fdictCapabilities(clientHttp)
    assert dictCapabilities["sCouncilReadiness"] == "blocked"
    assert dictCapabilities["bAvailable"] is False
    assert dictCapabilities["sReadinessReason"]


def test_no_login_anywhere_is_blocked_with_the_login_remedy(
        tmp_path, monkeypatch, sEvidencePath):
    from vaibify.gui.routes import councilRoutes
    monkeypatch.setattr(
        councilRoutes, "fdictReadProjectLoginState",
        lambda dictCtx, sContainerId, sProvider="claude": {
            "bHasLogin": False, "iExpiresAtEpochMilliseconds": 0})
    _, clientHttp = _tBuildClient(tmp_path, monkeypatch)
    dictCapabilities = _fdictCapabilities(clientHttp)
    assert dictCapabilities["sCouncilReadiness"] == "blocked"
    assert "Log in" in dictCapabilities["sReadinessReason"]


# ----- the snapshot half (plan contract C, scope from B) --------------------------------


DICT_TRACKED_READ = {
    "bSuccess": True, "sReason": "", "sHeadSha": "head0001",
    "sPorcelainDigest": "porcelain0001", "iChangedCount": 1,
    "dictEntries": {"code/step.py": {
        "sMode": "100644", "listStages": [0], "bSkipWorktree": False,
        "sType": "file", "sIdentity": "ab" * 20, "iSizeBytes": 2048}}}


def _fnMakeWholeDirectoryTooLarge(app, iTrackedBytes=2048, monkeypatch=None,
                                  tmp_path=None):
    """30 GB of ignored output beside a small tracked set."""
    from vaibify.gui import agentCouncilSnapshotScope
    dockerMock = app.state.dictRouteContext["docker"]
    dockerMock.dictRepositoryWeight.update({
        "iFileCount": 345000, "iTotalBytes": 30 * 10 ** 9,
        "bTruncated": True})
    dictTracked = json_deep_copy(DICT_TRACKED_READ)
    dictTracked["dictEntries"]["code/step.py"]["iSizeBytes"] = iTrackedBytes
    dockerMock.fdictFetchTrackedIdentities = (
        lambda sContainerId, sRepoPath: dictTracked)
    dockerMock.fdictFetchUntrackedInventory = (
        lambda sContainerId, sRepoPath: {
            "bSuccess": True, "bComplete": True, "listEntries": [
                ["output/run0/result.bin", "ignored", 30 * 10 ** 9]]})
    monkeypatch.setattr(
        agentCouncilSnapshotScope, "fsResolveSnapshotScopeDirectory",
        lambda: str(tmp_path / "scopeStore"))


def json_deep_copy(jsonValue):
    import json
    return json.loads(json.dumps(jsonValue))


def test_too_large_but_tracked_fits_is_a_choice(
        tmp_path, monkeypatch, sEvidencePath):
    _fnAuthorize(sEvidencePath)
    app, clientHttp = _tBuildClient(tmp_path, monkeypatch)
    _fnMakeWholeDirectoryTooLarge(app, monkeypatch=monkeypatch,
                                  tmp_path=tmp_path)
    dictCapabilities = _fdictCapabilities(clientHttp)
    assert dictCapabilities["sCouncilReadiness"] == "needsSnapshotChoice"
    dictOffer = dictCapabilities["dictTrackedScopeOffer"]
    assert (dictOffer["bFits"], dictOffer["iTrackedFileCount"]) == (True, 1)
    assert dictOffer["iUncommittedEditCount"] == 1
    assert dictOffer["dictOmissionSummary"]["iOmittedCount"] == 1
    assert dictOffer["sObservationId"]


def test_too_large_and_no_consent_needs_both(
        tmp_path, monkeypatch, sEvidencePath):
    app, clientHttp = _tBuildClient(tmp_path, monkeypatch)
    _fnMakeWholeDirectoryTooLarge(app, monkeypatch=monkeypatch,
                                  tmp_path=tmp_path)
    assert _fdictCapabilities(clientHttp)["sCouncilReadiness"] == "needsBoth"


@pytest.mark.falsification
def test_a_remembered_tracked_scope_is_ready(
        tmp_path, monkeypatch, sEvidencePath):
    """The choice, once made, is the project's visible default.

    Kills: the remembered scope being ignored, which would ask the
    researcher the same size question before every council.
    """
    from vaibify.gui import agentCouncilSnapshotScope
    _fnAuthorize(sEvidencePath)
    app, clientHttp = _tBuildClient(tmp_path, monkeypatch)
    _fnMakeWholeDirectoryTooLarge(app, monkeypatch=monkeypatch,
                                  tmp_path=tmp_path)
    agentCouncilSnapshotScope.fnRememberScope(
        S_CONTAINER_NAME, S_PROJECT_REPO,
        agentCouncilSnapshotScope.fdictComposeSnapshotScope("gitTracked"))
    dictCapabilities = _fdictCapabilities(clientHttp)
    assert dictCapabilities["sCouncilReadiness"] == "ready"
    assert dictCapabilities["dictSnapshotScopeDefault"]["sScope"] == (
        "gitTracked")


def test_a_tracked_set_too_large_too_is_blocked_with_the_reason(
        tmp_path, monkeypatch, sEvidencePath):
    _fnAuthorize(sEvidencePath)
    app, clientHttp = _tBuildClient(tmp_path, monkeypatch)
    _fnMakeWholeDirectoryTooLarge(app, iTrackedBytes=10 ** 12,
                                  monkeypatch=monkeypatch, tmp_path=tmp_path)
    dictCapabilities = _fdictCapabilities(clientHttp)
    assert dictCapabilities["sCouncilReadiness"] == "blocked"
    assert "does not help" in dictCapabilities["sReadinessReason"]


def test_start_records_and_remembers_the_chosen_scope(
        tmp_path, monkeypatch, sEvidencePath):
    """Recorded per campaign, carried into the capture, remembered."""
    import json
    from vaibify.gui import agentCouncilSnapshotScope
    monkeypatch.setattr(
        agentCouncilSnapshotScope, "fsResolveSnapshotScopeDirectory",
        lambda: str(tmp_path / "scopeStore"))
    _fnAuthorize(sEvidencePath)
    app, clientHttp = _tBuildClient(tmp_path, monkeypatch)
    response = clientHttp.post(
        f"/api/agent-councils/{S_CONTAINER_ID}/start",
        json={"sQuestion": "anything", "sSnapshotScope": "gitTracked",
              "listParticipants": [
                  {"sProvider": "claude", "sRequestedModel": "modelOne"},
                  {"sProvider": "claude", "sRequestedModel": "modelTwo"}]})
    assert response.status_code == 200, response.text
    dictCampaign = response.json()["dictCampaign"]
    assert dictCampaign["dictSnapshotScope"]["sScope"] == "gitTracked"
    sManifest = os.path.join(str(tmp_path / "councils"),
                             dictCampaign["sCampaignId"], "snapshot",
                             "manifest.json")
    assert json.load(open(sManifest))["dictSnapshotScope"]["sScope"] == (
        "gitTracked")
    assert agentCouncilSnapshotScope.fdictReadRememberedScope(
        S_CONTAINER_NAME, S_PROJECT_REPO)["sScope"] == "gitTracked"


def test_start_refuses_an_unknown_scope(tmp_path, monkeypatch, sEvidencePath):
    _fnAuthorize(sEvidencePath)
    _, clientHttp = _tBuildClient(tmp_path, monkeypatch)
    response = clientHttp.post(
        f"/api/agent-councils/{S_CONTAINER_ID}/start",
        json={"sQuestion": "anything", "sSnapshotScope": "everything",
              "listParticipants": [
                  {"sProvider": "claude", "sRequestedModel": "modelOne"},
                  {"sProvider": "claude", "sRequestedModel": "modelTwo"}]})
    assert response.status_code == 400, response.text
