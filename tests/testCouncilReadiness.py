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
            "bHasLogin": False, "iExpiresAtEpochMilliseconds": 0,
            "sLoginProblem": f"{sProvider} has a stubbed problem"})
    _, clientHttp = _tBuildClient(tmp_path, monkeypatch)
    dictCapabilities = _fdictCapabilities(clientHttp)
    assert dictCapabilities["sCouncilReadiness"] == "blocked"
    assert "Log in" in dictCapabilities["sReadinessReason"]
    assert "claude: claude has a stubbed problem." in (
        dictCapabilities["sReadinessReason"])


# ----- WHY there is no login must reach the button --------------------------------------


class _LoginFileConnection:
    """A container whose login file is whatever the test says it is."""

    def __init__(self, fnRead):
        self.fnRead = fnRead

    def fbaFetchCredentialFile(self, sContainerId, sPath):
        return self.fnRead(sPath)


def _fdictReadClaudeLogin(sEvidencePath, fnRead):
    from vaibify.gui import councilRouteGuards
    return councilRouteGuards.fdictReadProjectLoginState(
        {"docker": _LoginFileConnection(fnRead)}, "anyContainer", "claude")


def _fnRaiseFileNotFound(sPath):
    raise FileNotFoundError(sPath)


def _fnRaiseDaemonDown(sPath):
    raise OSError("daemon down at /var/run/docker.sock")


def _fbaLapsedLogin(sPath):
    import json
    import time
    return json.dumps({"claudeAiOauth": {
        "accessToken": "fixture-access-token",
        "expiresAt": int((time.time() - 3600) * 1000)}}).encode("utf-8")


DICT_UNUSABLE_LOGINS = {
    "missing": (_fnRaiseFileNotFound, "no persisted Claude login was found"),
    "unparseable": (lambda sPath: b"not json", "not readable JSON"),
    "withoutToken": (lambda sPath: b"{}", "no access token"),
    "lapsed": (_fbaLapsedLogin, "expired"),
}


@pytest.mark.parametrize("sCase", sorted(DICT_UNUSABLE_LOGINS))
def test_each_unusable_login_says_which_way_it_is_unusable(
        sEvidencePath, sCase):
    fnRead, sExpectedFragment = DICT_UNUSABLE_LOGINS[sCase]
    dictLogin = _fdictReadClaudeLogin(sEvidencePath, fnRead)
    assert dictLogin["bHasLogin"] is False
    assert sExpectedFragment in dictLogin["sLoginProblem"], dictLogin


@pytest.mark.falsification
def test_the_unusable_logins_are_told_apart_not_one_sentence(sEvidencePath):
    """A boolean made all of these read alike; the sentences must differ.

    Kills: every failure collapsing back to one generic reason, which is
    the reported "I logged in and it still says no provider is logged
    in" with nothing to tell the researcher which of these it was.
    """
    setReasons = {
        _fdictReadClaudeLogin(sEvidencePath, fnRead)["sLoginProblem"]
        for fnRead, _ in DICT_UNUSABLE_LOGINS.values()}
    assert len(setReasons) == len(DICT_UNUSABLE_LOGINS)


def test_a_missing_login_names_the_path_the_hub_looked_at(sEvidencePath):
    dictLogin = _fdictReadClaudeLogin(sEvidencePath, _fnRaiseFileNotFound)
    assert "/workspace/.claude/.credentials.json" in (
        dictLogin["sLoginProblem"])


@pytest.mark.falsification
def test_an_unreadable_container_does_not_leak_the_daemons_words(
        sEvidencePath):
    """The daemon's own words are the Docker boundary's to translate.

    Kills: the unreadable-container reason carrying the exception text,
    which would put a socket path or daemon message in front of the
    researcher where the remedy belongs.
    """
    from vaibify.gui import councilRouteGuards
    dictLogin = _fdictReadClaudeLogin(sEvidencePath, _fnRaiseDaemonDown)
    assert dictLogin["bHasLogin"] is False
    assert dictLogin["sLoginProblem"] == (
        councilRouteGuards.S_LOGIN_UNREADABLE_PROBLEM)
    assert "docker.sock" not in dictLogin["sLoginProblem"]


def test_a_usable_login_has_no_problem_and_holds_no_secret(sEvidencePath):
    import json
    dictLogin = _fdictReadClaudeLogin(
        sEvidencePath, lambda sPath: json.dumps({"claudeAiOauth": {
            "accessToken": "the-secret-token",
            "refreshToken": "the-refresh-token"}}).encode("utf-8"))
    assert dictLogin["bHasLogin"] is True
    assert dictLogin["sLoginProblem"] == ""
    assert "the-secret-token" not in repr(dictLogin)


@pytest.mark.falsification
def test_the_reason_names_each_providers_own_problem():
    """Each sentence stands alone: provider, reason, one full stop.

    Kills: the reasons run together with doubled full stops, which reads
    as a message assembled from fragments rather than a diagnosis.
    """
    sReason = agentCouncilReadiness.fsComposeNoLoginReason(
        agentCouncilReadiness.fsSummarizeLoginProblems([
            {"sProvider": "claude", "sLoginProblem": "the login expired."},
            {"sProvider": "codex", "sLoginProblem": "no login was found"},
            {"sProvider": "gemini", "sLoginProblem": ""}]))
    assert "claude: the login expired." in sReason
    assert "codex: no login was found." in sReason
    assert "gemini:" not in sReason
    assert ".." not in sReason
    assert sReason.endswith("then convene.")


def test_a_reason_without_problems_is_still_the_remedy():
    sReason = agentCouncilReadiness.fsComposeNoLoginReason("")
    assert "Log in" in sReason
    assert "  " not in sReason


@pytest.mark.falsification
def test_blocked_over_http_names_the_path_and_every_provider(
        tmp_path, monkeypatch, sEvidencePath):
    """The real route, the real extractors, no stub of the function.

    Only the container read is replaced, so a reason that was dropped
    anywhere between the adapter and the button fails here.

    Kills: the capabilities route not forwarding each provider's own
    login problem, which leaves the button saying only that nothing was
    found.
    """
    app, clientHttp = _tBuildClient(tmp_path, monkeypatch)
    monkeypatch.setattr(
        app.state.dictRouteContext["docker"], "fbaFetchCredentialFile",
        lambda sContainerId, sPath: _fnRaiseFileNotFound(sPath))
    dictCapabilities = _fdictCapabilities(clientHttp)
    sReason = dictCapabilities["sReadinessReason"]
    assert dictCapabilities["sCouncilReadiness"] == "blocked"
    assert "/workspace/.claude/.credentials.json" in sReason
    for sProvider in ("claude", "codex", "gemini"):
        assert f"{sProvider}:" in sReason, sReason
    assert "Log in" in sReason


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
    """Recorded per campaign, carried into the capture, remembered.

    The provider seam is the gated fake the other start tests use, so
    no runner — and no Docker client — is ever built; the gate is opened
    at the end so the drive settles.
    """
    import json
    import threading
    from vaibify.gui import agentCouncilController, agentCouncilSnapshotScope
    from tests.testCouncilRoutes import _GatedFakeConnection
    eventGate = threading.Event()
    monkeypatch.setattr(
        agentCouncilController, "fconnectionBuildParticipantConnection",
        lambda dictRuntime, dictParticipant: _GatedFakeConnection(
            eventGate, dictParticipant["sRequestedModel"]))
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
    eventGate.set()


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
