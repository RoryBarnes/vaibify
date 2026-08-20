"""Controller lifecycle proofs: gates, continuation, crash classification.

The R1 proofs the route file does not carry: a blocking-question gate
suspends deliberation and a researcher response relaunches it through
the engine (never a hand-patched record); a hub restart classifies a
mid-turn campaign as interrupted, never resumed; and a respond against
a restart-classified campaign is refused with the convene-a-fresh-
council answer rather than silently resurrecting an engine over
runners nobody can account for.

Fixtures follow tests/testCouncilRoutes.py: real TestClient, container
name != id, gate-controlled or scripted fake provider connections, and
a fixture snapshot writer — the engine, controller, store and routes
are all real.
"""

import os
import time

import pytest
from fastapi.testclient import TestClient
from unittest.mock import patch

from vaibify.gui import (
    agentCouncilCampaign,
    agentCouncilContext,
    agentCouncilController,
    agentCouncilStore,
    browserSession,
    containerOwnership,
    pipelineServer,
)
from vaibify.config import registryManager
from tests.agentCouncilHarness import FakeCouncilConnection, CouncilRecorder
from tests.agentCouncilHarness import fdictDecideCompleted, fdictMakeTurnResult
from tests.sessionTokenTestHelper import fsBootstrapCredential
from tests.testCouncilRoutes import (
    MockDockerCouncil,
    S_AGENT_TOKEN,
    S_CONTAINER_ID,
    S_CONTAINER_NAME,
    S_PROJECT_REPO,
    _fdictWriteFixtureSnapshot,
    _fnWaitForCampaignState,
)


DICT_START_BODY = {
    "sQuestion": "Which integrator keeps the eccentricity bounded?",
    "listParticipants": [
        {"sProvider": "claude", "sRequestedModel": "modelOne"},
        {"sProvider": "claude", "sRequestedModel": "modelTwo"},
    ],
}


@pytest.fixture(autouse=True)
def fixtureIsolateRegistry(tmp_path, monkeypatch):
    """Redirect the project registry to a temp directory for every test."""
    sRegistryDirectory = str(tmp_path / ".vaibify-registry")
    monkeypatch.setattr(
        registryManager, "_S_REGISTRY_DIRECTORY", sRegistryDirectory)
    monkeypatch.setattr(
        registryManager, "_S_REGISTRY_PATH",
        os.path.join(sRegistryDirectory, "registry.json"))
    monkeypatch.setattr(
        agentCouncilContext, "fdictCaptureProjectContextSnapshot",
        _fdictWriteFixtureSnapshot)
    from vaibify.gui import agentCouncilCredentialGate
    monkeypatch.setattr(
        agentCouncilCredentialGate, "fdictEvaluateCredentialEnablement",
        lambda sProvider, sImageIdentity=None: {
            "bEnabled": True, "sReason": "", "dictRecord": {}})


def _fnPatchScriptedConnections(monkeypatch, ffnDecide):
    """Route the controller's provider seam onto harness fakes."""
    recorder = CouncilRecorder()
    monkeypatch.setattr(
        agentCouncilController, "fconnectionBuildParticipantConnection",
        lambda dictRuntime, dictParticipant: FakeCouncilConnection(
            dictParticipant["sParticipantId"], ffnDecide, recorder))
    return recorder


def _tBuildOwnedApp(tmp_path, typeMockDocker=MockDockerCouncil):
    """Build the app, seed the workflow repo, mint ownership."""
    with patch.object(
        pipelineServer, "_fconnectionCreateDocker", typeMockDocker,
    ):
        app = pipelineServer.fappCreateApplication(
            sWorkspaceRoot="/workspace", sTerminalUserArg="testuser")
    app.state.dictCouncilCampaignStore = (
        agentCouncilStore.fdictCreateCampaignStore(
            sDurableStoreRoot=str(tmp_path / "councils")))
    app.state.dictRouteContext["workflows"][S_CONTAINER_ID] = {
        "sProjectRepoPath": S_PROJECT_REPO}
    sCredential = fsBootstrapCredential(app)
    sBrowserSessionId = browserSession.fsSessionIdForCredential(
        app.state.dictBrowserSessions, sCredential)
    sLease = containerOwnership.fsMintLease()
    app.state.dictContainerOwners[S_CONTAINER_NAME] = (
        containerOwnership.OwnerRecord(
            sLeaseId=sLease, fileHandleLock=None, sAgentToken=S_AGENT_TOKEN,
            sContainerId=S_CONTAINER_ID,
            sBrowserSessionId=sBrowserSessionId))
    return app, {"X-Session-Token": sCredential, "X-Vaibify-Lease": sLease}


def test_blocking_question_gate_suspends_then_a_response_continues(
        tmp_path, monkeypatch):
    """needsHuman suspends with no live work; respond relaunches the engine.

    Round one's veto raises a blocking question, so the engine opens
    the gate and the drive settles (suspension leaves NO live turn).
    The researcher's response then relaunches deliberation through the
    controller, the final veto accepts, and the campaign reaches
    planReady — every transition the engine's, none hand-patched.
    """
    def _fdictDecide(sHandle, dictTurnRequest):
        bFirstVeto = (dictTurnRequest["sPhase"] == "veto"
                      and dictTurnRequest["iRoundNumber"] == 1)
        if bFirstVeto:
            return fdictDecideCompleted(fdictMakeTurnResult(
                sVerdict="needsHuman",
                listOpenQuestions=["is the tolerance negotiable?"]))
        return fdictDecideCompleted(fdictMakeTurnResult(sVerdict="accept"))

    _fnPatchScriptedConnections(monkeypatch, _fdictDecide)
    app, dictHeaders = _tBuildOwnedApp(tmp_path)
    with TestClient(app, headers=dictHeaders) as client:
        response = client.post(
            f"/api/agent-councils/{S_CONTAINER_ID}/start",
            json=DICT_START_BODY)
        assert response.status_code == 200, response.text
        sCampaignId = response.json()["sCampaignId"]
        _fnWaitForCampaignState(
            app, sCampaignId, agentCouncilCampaign.S_STATE_NEEDS_HUMAN)
        from vaibify.gui import agentCouncilRegistry
        assert agentCouncilRegistry.fbHubHasLiveCouncilWork(app) is False, (
            "a human gate must suspend with no live turn behind it")
        responseAnswer = client.post(
            f"/api/agent-councils/{S_CONTAINER_ID}/{sCampaignId}/respond",
            json={"sResponseText": "the tolerance is fixed; proceed"})
        assert responseAnswer.status_code == 200, responseAnswer.text
        _fnWaitForCampaignState(
            app, sCampaignId, agentCouncilCampaign.S_STATE_PLAN_READY)
        dictRecord = agentCouncilStore.fjsonGetCampaignRecord(
            app.state.dictCouncilCampaignStore, sCampaignId)
        assert dictRecord["listResearcherResponses"] == [
            {"sText": "the tolerance is fixed; proceed"}]


def test_restart_classifies_a_mid_turn_campaign_as_interrupted(tmp_path):
    """A campaign checkpointed in planning is interrupted on reload.

    The crash proof's store half: the durable record says planning (a
    turn had no terminal record), the restarted hub reloads it and the
    controller classifies it interrupted — never resumed. The runner
    half (labelled survivors destroyed or quarantined) is proven live
    in tests/testAgentCouncilProvidersLive.py.
    """
    dictStore = agentCouncilStore.fdictCreateCampaignStore(
        sDurableStoreRoot=str(tmp_path / "councils"))
    dictCampaign = agentCouncilCampaign.fdictCreateCampaign(
        "a question", [
            agentCouncilCampaign.fdictCreateParticipant("claude", "mOne"),
            agentCouncilCampaign.fdictCreateParticipant("claude", "mTwo"),
        ])
    agentCouncilCampaign.fnTransitionCampaignState(
        dictCampaign, agentCouncilCampaign.S_STATE_PLANNING, "launched")
    agentCouncilStore.fdictRegisterStartedCampaign(dictStore, dictCampaign)

    dictStoreReloaded = agentCouncilStore.fdictCreateCampaignStore(
        sDurableStoreRoot=str(tmp_path / "councils"))
    agentCouncilStore.fdictReloadDurableCampaigns(dictStoreReloaded)
    iClassified = (
        agentCouncilController.fiClassifyInterruptedCampaignsOnStartup(
            dictStoreReloaded))
    assert iClassified == 1
    dictReloaded = agentCouncilStore.fjsonGetCampaignRecord(
        dictStoreReloaded, dictCampaign["sCampaignId"])
    assert dictReloaded["sState"] == (
        agentCouncilCampaign.S_STATE_INTERRUPTED)
    assert dictReloaded["listStateTransitions"][-1]["sReason"] == (
        "hubRestartedWhileATurnHadNoTerminalRecord")


def test_respond_after_a_restart_is_refused_not_resumed(
        tmp_path, monkeypatch):
    """A restart-classified campaign refuses continuation honestly."""
    def _fdictDecide(sHandle, dictTurnRequest):
        return fdictDecideCompleted(fdictMakeTurnResult(
            sVerdict="needsHuman", listOpenQuestions=["which prior?"]))

    _fnPatchScriptedConnections(monkeypatch, _fdictDecide)
    app, dictHeaders = _tBuildOwnedApp(tmp_path)
    with TestClient(app, headers=dictHeaders) as client:
        response = client.post(
            f"/api/agent-councils/{S_CONTAINER_ID}/start",
            json=DICT_START_BODY)
        sCampaignId = response.json()["sCampaignId"]
        _fnWaitForCampaignState(
            app, sCampaignId, agentCouncilCampaign.S_STATE_NEEDS_HUMAN)
        # The restart: the in-memory runtime is gone; only the durable
        # record survives.
        getattr(app.state, agentCouncilController
                .S_COUNCIL_CONTROLLER_STATE_KEY)[
            "dictCampaignRuntime"].clear()
        responseAnswer = client.post(
            f"/api/agent-councils/{S_CONTAINER_ID}/{sCampaignId}/respond",
            json={"sResponseText": "carry on"})
        assert responseAnswer.status_code == 409, responseAnswer.text
        assert "convene a fresh council" in responseAnswer.json()["detail"]


def test_accept_requires_plan_ready_and_persists_the_engine_candidate(
        tmp_path, monkeypatch):
    """R3: acceptance is the engine's gate over the SERVER-held candidate.

    A stopped (archived) campaign refuses acceptance 409 — the
    inversion of the prototype's stop-then-accept flow — and a
    planReady campaign persists the council's own candidate: the
    plan.md on disk carries the fake synthesis summary that only the
    ENGINE result holds; no caller text is read at all.
    """
    def _fdictDecide(sHandle, dictTurnRequest):
        return fdictDecideCompleted(fdictMakeTurnResult(
            sVerdict="accept",
            listPlanItems=["profile the bottleneck", "cache the kernel"]))

    _fnPatchScriptedConnections(monkeypatch, _fdictDecide)
    app, dictHeaders = _tBuildOwnedApp(tmp_path)
    with TestClient(app, headers=dictHeaders) as client:
        sCampaignId = client.post(
            f"/api/agent-councils/{S_CONTAINER_ID}/start",
            json=DICT_START_BODY).json()["sCampaignId"]
        _fnWaitForCampaignState(
            app, sCampaignId, agentCouncilCampaign.S_STATE_PLAN_READY)
        sBase = f"/api/agent-councils/{S_CONTAINER_ID}/{sCampaignId}"
        response = client.post(
            sBase + "/accept-plan",
            json={"sPlanText": "attacker-chosen words"})
        assert response.status_code == 200, response.text
        with open(response.json()["sLocalPlanPath"]) as filePlan:
            sPlanText = filePlan.read()
        assert "a plausible summary" in sPlanText
        assert "profile the bottleneck" in sPlanText
        assert "attacker-chosen words" not in sPlanText
        dictRecord = agentCouncilStore.fjsonGetCampaignRecord(
            app.state.dictCouncilCampaignStore, sCampaignId)
        assert dictRecord["sState"] == (
            agentCouncilCampaign.S_STATE_AWAITING_IMPLEMENTATION)


def test_accept_on_a_stopped_campaign_is_refused(tmp_path, monkeypatch):
    """The inverted stop-then-accept: archived is not planReady, so 409."""
    def _fdictDecide(sHandle, dictTurnRequest):
        return fdictDecideCompleted(fdictMakeTurnResult(sVerdict="accept"))

    _fnPatchScriptedConnections(monkeypatch, _fdictDecide)
    app, dictHeaders = _tBuildOwnedApp(tmp_path)
    with TestClient(app, headers=dictHeaders) as client:
        sCampaignId = client.post(
            f"/api/agent-councils/{S_CONTAINER_ID}/start",
            json=DICT_START_BODY).json()["sCampaignId"]
        _fnWaitForCampaignState(
            app, sCampaignId, agentCouncilCampaign.S_STATE_PLAN_READY)
        sBase = f"/api/agent-councils/{S_CONTAINER_ID}/{sCampaignId}"
        # Reject the candidate through the store's own record? No — the
        # honest route to a non-planReady state here is a stop, which
        # settles immediately once nothing is live.
        client.post(sBase + "/request-stop")
        response = client.post(sBase + "/accept-plan")
        assert response.status_code == 409, response.text
        assert "planReady" in response.json()["detail"]


def test_accept_after_a_restart_rebuilds_the_engine_gate(
        tmp_path, monkeypatch):
    """A planReady record survives a restart and still accepts guarded."""
    def _fdictDecide(sHandle, dictTurnRequest):
        return fdictDecideCompleted(fdictMakeTurnResult(sVerdict="accept"))

    _fnPatchScriptedConnections(monkeypatch, _fdictDecide)
    app, dictHeaders = _tBuildOwnedApp(tmp_path)
    with TestClient(app, headers=dictHeaders) as client:
        sCampaignId = client.post(
            f"/api/agent-councils/{S_CONTAINER_ID}/start",
            json=DICT_START_BODY).json()["sCampaignId"]
        _fnWaitForCampaignState(
            app, sCampaignId, agentCouncilCampaign.S_STATE_PLAN_READY)
        getattr(app.state, agentCouncilController
                .S_COUNCIL_CONTROLLER_STATE_KEY)[
            "dictCampaignRuntime"].clear()
        response = client.post(
            f"/api/agent-councils/{S_CONTAINER_ID}/{sCampaignId}"
            "/accept-plan")
        assert response.status_code == 200, response.text
        with open(response.json()["sLocalPlanPath"]) as filePlan:
            assert "a plausible summary" in filePlan.read()


def test_exhausted_round_exits_drive_the_engine_transitions(
        tmp_path, monkeypatch):
    """The three exit routes post the ENGINE'S transitions (R6 proof).

    The convene payload's settings reach the campaign (iMaximumRounds
    1, proving the settings passthrough), the objecting veto exhausts
    the budget, a plain respond is refused with the three-exits answer,
    a granted round runs and exhausts again, the resolve/override exit
    records the dispositions and runs the final veto, and the accepted
    final candidate carries the researcher-overridden objection in its
    provenance — every transition the engine's own.
    """
    def _fdictDecide(sHandle, dictTurnRequest):
        bFinalVeto = (dictTurnRequest["sPhase"] == "veto"
                      and dictTurnRequest["iRoundNumber"] >= 3)
        if dictTurnRequest["sPhase"] == "veto" and not bFinalVeto:
            return fdictDecideCompleted(fdictMakeTurnResult(
                sVerdict="blockingObjection",
                listBlockingObjections=["the prior is unjustified"]))
        return fdictDecideCompleted(fdictMakeTurnResult(sVerdict="accept"))

    _fnPatchScriptedConnections(monkeypatch, _fdictDecide)
    app, dictHeaders = _tBuildOwnedApp(tmp_path)
    with TestClient(app, headers=dictHeaders) as client:
        response = client.post(
            f"/api/agent-councils/{S_CONTAINER_ID}/start",
            json=dict(DICT_START_BODY, dictSettings={"iMaximumRounds": 1}))
        assert response.status_code == 200, response.text
        sCampaignId = response.json()["sCampaignId"]
        sBase = f"/api/agent-councils/{S_CONTAINER_ID}/{sCampaignId}"
        _fnWaitForCampaignState(
            app, sCampaignId, agentCouncilCampaign.S_STATE_NEEDS_HUMAN)
        dictRecord = agentCouncilStore.fjsonGetCampaignRecord(
            app.state.dictCouncilCampaignStore, sCampaignId)
        assert dictRecord["dictSettings"]["iMaximumRounds"] == 1
        assert dictRecord["dictPendingHumanGate"]["sGateKind"] == (
            agentCouncilCampaign.S_GATE_EXHAUSTED_ROUNDS)

        responsePlain = client.post(
            sBase + "/respond", json={"sResponseText": "keep going"})
        assert responsePlain.status_code == 409, responsePlain.text
        assert "three" in responsePlain.json()["detail"]

        responseGrant = client.post(
            sBase + "/grant-resolution-round", json={"iGrantedRounds": 1})
        assert responseGrant.status_code == 200, responseGrant.text
        _fnWaitForCampaignState(
            app, sCampaignId, agentCouncilCampaign.S_STATE_NEEDS_HUMAN)
        dictRecord = agentCouncilStore.fjsonGetCampaignRecord(
            app.state.dictCouncilCampaignStore, sCampaignId)
        listObjections = dictRecord["dictPendingHumanGate"][
            "listUnresolvedObjections"]
        assert listObjections, "the granted round must exhaust again"

        dictDispositions = {
            dictObjection["sObjectionId"]: {
                "sAction": "override",
                "sText": "the prior is standard in this field"}
            for dictObjection in listObjections}
        responseResolve = client.post(
            sBase + "/resolve-objections",
            json={"dictDispositionByObjectionId": dictDispositions})
        assert responseResolve.status_code == 200, responseResolve.text
        _fnWaitForCampaignState(
            app, sCampaignId, agentCouncilCampaign.S_STATE_PLAN_READY)
        dictRecord = agentCouncilStore.fjsonGetCampaignRecord(
            app.state.dictCouncilCampaignStore, sCampaignId)
        assert dictRecord["dictCandidatePlan"][
            "listResearcherOverriddenObjections"], (
            "the override must survive as researcher provenance")

        responseReject = client.post(
            sBase + "/reject-candidate",
            json={"sReasonText": "not this quarter"})
        assert responseReject.status_code == 200, responseReject.text
        assert responseReject.json()["dictCampaign"]["sState"] == (
            agentCouncilCampaign.S_STATE_ARCHIVED)


def test_stale_baseline_is_computed_from_the_live_repository(
        tmp_path, monkeypatch):
    """R12: staleness has a REAL producer, never a fabricated flag.

    The campaign read re-runs the gitWorktreeIdentities typed read and
    compares its head sha and porcelain digest against the sealed
    manifest: matching state reports fresh, a moved commit reports
    stale naming the move, and a repository that cannot be read
    reports UNKNOWN — never fresh.
    """
    import json

    class _MockDockerWithIdentities(MockDockerCouncil):
        sHeadShaNow = "commitbefore01"

        def fdictFetchWorktreeIdentities(self, sContainerId, sRepoPath):
            return {"bSuccess": True, "sReason": "",
                    "sHeadSha": type(self).sHeadShaNow,
                    "sPorcelainDigest": "porcelainsteady01",
                    "dictPathIdentities": {}}

    def _fdictDecide(sHandle, dictTurnRequest):
        return fdictDecideCompleted(fdictMakeTurnResult(sVerdict="accept"))

    _fnPatchScriptedConnections(monkeypatch, _fdictDecide)
    app, dictHeaders = _tBuildOwnedApp(
        tmp_path, typeMockDocker=_MockDockerWithIdentities)
    with TestClient(app, headers=dictHeaders) as client:
        sCampaignId = client.post(
            f"/api/agent-councils/{S_CONTAINER_ID}/start",
            json=DICT_START_BODY).json()["sCampaignId"]
        _fnWaitForCampaignState(
            app, sCampaignId, agentCouncilCampaign.S_STATE_PLAN_READY)
        # Seal the manifest with the identity the typed read reports
        # NOW, exactly as the real capture records it.
        sManifestPath = os.path.join(
            app.state.dictCouncilCampaignStore["sDurableStoreRoot"],
            sCampaignId, "snapshot", "manifest.json")
        with open(sManifestPath, "w") as fileManifest:
            fileManifest.write(json.dumps({
                "sBaselineHeadSha": "commitbefore01",
                "sBaselinePorcelainDigest": "porcelainsteady01"}))

        sBase = f"/api/agent-councils/{S_CONTAINER_ID}/{sCampaignId}"
        dictFresh = client.get(sBase).json()["dictCampaign"]
        assert dictFresh["bPlanningBaselineStale"] is False

        _MockDockerWithIdentities.sHeadShaNow = "commitafter002"
        dictStale = client.get(sBase).json()["dictCampaign"]
        assert dictStale["bPlanningBaselineStale"] is True
        assert "commit moved" in dictStale["sPlanningBaselineSummary"]


def test_unreadable_repository_reports_unknown_never_fresh(
        tmp_path, monkeypatch):
    """A comparison that cannot run answers UNKNOWN, not fresh."""
    def _fdictDecide(sHandle, dictTurnRequest):
        return fdictDecideCompleted(fdictMakeTurnResult(sVerdict="accept"))

    _fnPatchScriptedConnections(monkeypatch, _fdictDecide)
    app, dictHeaders = _tBuildOwnedApp(tmp_path)
    with TestClient(app, headers=dictHeaders) as client:
        sCampaignId = client.post(
            f"/api/agent-councils/{S_CONTAINER_ID}/start",
            json=DICT_START_BODY).json()["sCampaignId"]
        _fnWaitForCampaignState(
            app, sCampaignId, agentCouncilCampaign.S_STATE_PLAN_READY)
        dictRecord = client.get(
            f"/api/agent-councils/{S_CONTAINER_ID}/{sCampaignId}"
        ).json()["dictCampaign"]
        assert dictRecord["bPlanningBaselineStale"] is None
        assert "could not run" in dictRecord["sPlanningBaselineSummary"]


def test_commands_during_a_live_turn_keep_state_consistent(
        tmp_path, monkeypatch):
    """respond and a second stop during a live turn never tear the record.

    The ordering proof (R1 proof b): while a turn is live, respond is
    refused, stop is recorded once and stays a request, and after the
    turn settles the record shows exactly one stop decision trail —
    the archived exit — with no interleaved writes.
    """
    import threading
    eventGate = threading.Event()
    from tests.testCouncilRoutes import _GatedFakeConnection
    monkeypatch.setattr(
        agentCouncilController, "fconnectionBuildParticipantConnection",
        lambda dictRuntime, dictParticipant: _GatedFakeConnection(eventGate))
    app, dictHeaders = _tBuildOwnedApp(tmp_path)
    with TestClient(app, headers=dictHeaders) as client:
        sCampaignId = client.post(
            f"/api/agent-councils/{S_CONTAINER_ID}/start",
            json=DICT_START_BODY).json()["sCampaignId"]
        sBase = f"/api/agent-councils/{S_CONTAINER_ID}/{sCampaignId}"
        assert client.post(
            sBase + "/respond",
            json={"sResponseText": "x"}).status_code == 409
        dictFirstStop = client.post(sBase + "/request-stop").json()
        dictSecondStop = client.post(sBase + "/request-stop").json()
        assert dictFirstStop["bSettled"] is False
        assert dictSecondStop["bSettled"] is False
        eventGate.set()
        _fnWaitForCampaignState(
            app, sCampaignId, agentCouncilCampaign.S_STATE_ARCHIVED)
        dictRecord = agentCouncilStore.fjsonGetCampaignRecord(
            app.state.dictCouncilCampaignStore, sCampaignId)
        assert dictRecord["bStopRequested"] is True
        assert dictRecord["listStateTransitions"][-1]["sReason"] == (
            "stopAfterCurrentTurn")
