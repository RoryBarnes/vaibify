"""The council routes' pure decision helpers, driven directly.

The HTTP behavior of every council route is pinned over a real
``TestClient`` in ``tests/testCouncilRoutes.py``. The helpers here
decide WHAT those routes answer — whether a campaign has live work,
what a stop drains, which chairbot index is in range, whether a sealed
baseline is stale, which accepted plan seeds an implementation council
— and several of their branches need a registry, store or repository
state that the end-to-end fixture cannot cheaply stage. They are driven
against the real registry and the real campaign store; the one
container read (the worktree identity) is a scripted double.
"""

import json
import os

import pytest
from fastapi import HTTPException

from vaibify.gui import (
    agentCouncilCampaign,
    agentCouncilContext,
    agentCouncilRegistry,
    agentCouncilStore,
)
from vaibify.gui.routes import councilRoutes


S_CAMPAIGN_ALPHA = "campaign-alpha"
S_CAMPAIGN_BETA = "campaign-beta"
S_CONTAINER_ID = "f00dcafe4321"
S_RESOURCE = "projectAlpha"
S_REPO_PATH = "/workspace/repoAlpha"


# ----- live work and the stop drain ---------------------------------------


def testAnActiveApiRequestIsLiveWorkForItsCampaignOnly():
    dictRegistry = agentCouncilRegistry.fdictCreateCouncilRegistry()
    agentCouncilRegistry.fdictRecordApiRequest(
        dictRegistry, "request-1", S_CAMPAIGN_ALPHA, "claude")
    assert councilRoutes._fbCampaignHasLiveWork(
        dictRegistry, S_CAMPAIGN_ALPHA) is True
    assert councilRoutes._fbCampaignHasLiveWork(
        dictRegistry, S_CAMPAIGN_BETA) is False
    agentCouncilRegistry.fnSettleApiRequest(
        dictRegistry, "request-1",
        agentCouncilRegistry.S_API_REQUEST_INTERRUPTED)
    assert councilRoutes._fbCampaignHasLiveWork(
        dictRegistry, S_CAMPAIGN_ALPHA) is False


def testAPendingRunnerReservationIsLiveWorkForItsCampaignOnly():
    dictRegistry = agentCouncilRegistry.fdictCreateCouncilRegistry()
    dictReserved = agentCouncilRegistry.fdictReserveRunner(
        dictRegistry, S_CAMPAIGN_ALPHA, "reservation-1", "claude",
        {"iMemoryBytes": 1, "fCpuCount": 0.1})
    assert dictReserved["bReserved"] is True
    assert councilRoutes._fbCampaignHasLiveWork(
        dictRegistry, S_CAMPAIGN_ALPHA) is True
    assert councilRoutes._fbCampaignHasLiveWork(
        dictRegistry, S_CAMPAIGN_BETA) is False


def testALaunchIsRefusedWhileATurnIsInFlight():
    dictRegistry = agentCouncilRegistry.fdictCreateCouncilRegistry()
    agentCouncilRegistry.fbRegisterTurnInFlight(
        dictRegistry, S_CAMPAIGN_ALPHA, "turn-1")
    dictControllerState = {"dictCampaignRuntime": {}}
    with pytest.raises(HTTPException) as excInfo:
        councilRoutes._fnRefuseLaunchWhileCampaignBusy(
            dictControllerState, dictRegistry, S_CAMPAIGN_ALPHA)
    assert excInfo.value.status_code == 409
    assert "already in flight" in excInfo.value.detail
    councilRoutes._fnRefuseLaunchWhileCampaignBusy(
        dictControllerState, dictRegistry, S_CAMPAIGN_BETA)


def testTheStopDrainRetiresOnlyThisCampaignsTurnsAndRequests():
    dictRegistry = agentCouncilRegistry.fdictCreateCouncilRegistry()
    for sCampaignId in (S_CAMPAIGN_ALPHA, S_CAMPAIGN_BETA):
        agentCouncilRegistry.fbRegisterTurnInFlight(
            dictRegistry, sCampaignId, "turn-1")
        agentCouncilRegistry.fdictRecordApiRequest(
            dictRegistry, f"request-{sCampaignId}", sCampaignId, "claude")
    councilRoutes._fnDrainCampaignWork(dictRegistry, S_CAMPAIGN_ALPHA)
    assert dictRegistry["setTurnsInFlight"] == {(S_CAMPAIGN_BETA, "turn-1")}
    dictRequests = dictRegistry["dictApiRequestsById"]
    assert dictRequests[f"request-{S_CAMPAIGN_ALPHA}"]["sStatus"] == (
        agentCouncilRegistry.S_API_REQUEST_INTERRUPTED)
    assert dictRequests[f"request-{S_CAMPAIGN_BETA}"]["sStatus"] == (
        agentCouncilRegistry.S_API_REQUEST_ACTIVE)
    assert councilRoutes._fbCampaignHasLiveWork(
        dictRegistry, S_CAMPAIGN_ALPHA) is False


def testAChairbotIndexOutsideTheRosterIsRefused():
    listParticipants = [{"sParticipantId": "participant-one"},
                        {"sParticipantId": "participant-two"}]
    assert councilRoutes._fsResolveChairbotId(listParticipants, 1) == (
        "participant-two")
    with pytest.raises(HTTPException) as excInfo:
        councilRoutes._fsResolveChairbotId(listParticipants, 2)
    assert excInfo.value.status_code == 400
    assert "outside the participant list" in excInfo.value.detail


# ----- baseline staleness ------------------------------------------------------


class WorktreeIdentityDocker:
    """Answers the one typed read the staleness producer makes."""

    def __init__(self, dictObservation):
        self.dictObservation = dictObservation
        self.listReads = []

    def fdictFetchWorktreeIdentities(self, sContainerId, sRepoRoot):
        self.listReads.append((sContainerId, sRepoRoot))
        return self.dictObservation


def fdictBuildStoreWithManifest(tmp_path, dictManifest):
    dictStore = agentCouncilStore.fdictCreateCampaignStore(
        sDurableStoreRoot=str(tmp_path / "agentCouncils"))
    if dictManifest is not None:
        sSnapshotDirectory = os.path.join(
            dictStore["sDurableStoreRoot"], S_CAMPAIGN_ALPHA, "snapshot")
        os.makedirs(sSnapshotDirectory)
        with open(os.path.join(sSnapshotDirectory, "manifest.json"), "w",
                  encoding="utf-8") as fileManifest:
            json.dump(dictManifest, fileManifest)
    return dictStore


def fdictComputeStaleness(tmp_path, dictManifest, dictObservation):
    dockerWorktree = WorktreeIdentityDocker(dictObservation)
    dictStaleness = councilRoutes._fdictComputeBaselineStaleness(
        {"docker": dockerWorktree},
        fdictBuildStoreWithManifest(tmp_path, dictManifest),
        S_CONTAINER_ID, S_REPO_PATH, S_CAMPAIGN_ALPHA)
    return dictStaleness, dockerWorktree


DICT_BASELINE_MANIFEST = {"sBaselineHeadSha": "commit0001",
                          "sBaselinePorcelainDigest": "porcelain0001"}


def testNoSealedManifestIsUnknownNeverFresh(tmp_path):
    dictStaleness, dockerWorktree = fdictComputeStaleness(
        tmp_path, None, {"bSuccess": True})
    assert dictStaleness == {
        "bPlanningBaselineStale": None,
        "sPlanningBaselineSummary": "no sealed snapshot manifest"}
    assert dockerWorktree.listReads == []


def testAManifestPredatingTheBaselineFieldsIsUnknown(tmp_path):
    dictStaleness, dockerWorktree = fdictComputeStaleness(
        tmp_path, {"sSnapshotIdentity": "snapshot0001"}, {"bSuccess": True})
    assert dictStaleness["bPlanningBaselineStale"] is None
    assert "predates the baseline identity fields" in (
        dictStaleness["sPlanningBaselineSummary"])
    assert dockerWorktree.listReads == []


def testAFailedRepositoryReadIsUnknownAndNamesTheFailureClass(tmp_path):
    dictStaleness, dockerWorktree = fdictComputeStaleness(
        tmp_path, DICT_BASELINE_MANIFEST,
        {"bSuccess": False, "sReason": "container stopped"})
    assert dictStaleness == {
        "bPlanningBaselineStale": None,
        "sPlanningBaselineSummary":
            "the baseline comparison could not run (RuntimeError)"}
    assert dockerWorktree.listReads == [(S_CONTAINER_ID, S_REPO_PATH)]


def testAChangedWorkingTreeUnderTheSameCommitIsStale(tmp_path):
    dictStaleness, _ = fdictComputeStaleness(
        tmp_path, DICT_BASELINE_MANIFEST,
        {"bSuccess": True, "sHeadSha": "commit0001",
         "sPorcelainDigest": "porcelain0002"})
    assert dictStaleness == {
        "bPlanningBaselineStale": True,
        "sPlanningBaselineSummary": "the working tree changed"}


def testAnUnchangedBaselineIsFresh(tmp_path):
    dictStaleness, _ = fdictComputeStaleness(
        tmp_path, DICT_BASELINE_MANIFEST,
        {"bSuccess": True, "sHeadSha": "commit0001",
         "sPorcelainDigest": "porcelain0001"})
    assert dictStaleness == {"bPlanningBaselineStale": False,
                             "sPlanningBaselineSummary": ""}


def testChangedContentUnderAnUnchangedPorcelainIsStale(tmp_path):
    dictBaselineIdentities = {"stepAlpha/dataFile.csv": {
        "sType": "file", "sIdentity": "1" * 40}}
    dictManifest = dict(
        DICT_BASELINE_MANIFEST,
        sBaselinePathIdentitiesDigest=(
            agentCouncilContext.fsComputePathIdentitiesDigest(
                dictBaselineIdentities)))
    dictStaleness, _ = fdictComputeStaleness(
        tmp_path, dictManifest,
        {"bSuccess": True, "sHeadSha": "commit0001",
         "sPorcelainDigest": "porcelain0001",
         "dictPathIdentities": {"stepAlpha/dataFile.csv": {
             "sType": "file", "sIdentity": "2" * 40}}})
    assert dictStaleness == {
        "bPlanningBaselineStale": True,
        "sPlanningBaselineSummary": "file contents changed"}


@pytest.mark.parametrize("dictStaleness,sExpected", [
    ({"bPlanningBaselineStale": True,
      "sPlanningBaselineSummary": "the working tree changed"},
     "the repository has CHANGED since this council's sealed baseline "
     "(the working tree changed); the plan speaks about the baseline, not "
     "the tree as it stands now"),
    ({"bPlanningBaselineStale": None, "sPlanningBaselineSummary": ""},
     "the baseline comparison could not run (no verdict); treat the "
     "repository as possibly changed since capture"),
    ({"bPlanningBaselineStale": False, "sPlanningBaselineSummary": ""}, ""),
])
def testStalenessBecomesOneHonestSentence(dictStaleness, sExpected):
    assert councilRoutes._fsDescribeBaselineStaleness(dictStaleness) == (
        sExpected)


# ----- the implementation council's seed ----------------------------------------


def fdictBuildSourceCampaign(sState, sResourceName=S_RESOURCE,
                             sRepoPath=S_REPO_PATH):
    listParticipants = [
        agentCouncilCampaign.fdictCreateParticipant("claude", "modelOne"),
        agentCouncilCampaign.fdictCreateParticipant("claude", "modelTwo"),
    ]
    dictCampaign = agentCouncilCampaign.fdictCreateCampaign(
        "Should stepAlpha stream its output?", listParticipants,
        dictProjectIdentity={"sResourceName": sResourceName,
                             "sProjectRepoPath": sRepoPath})
    dictCampaign["sState"] = sState
    dictCampaign["dictCandidatePlan"] = {"dictResult": {
        "sSummary": "Stream the output in bounded chunks.",
        "listPlanItems": ["Replace the buffer with a generator."]}}
    return dictCampaign


def fdictStoreWith(tmp_path, dictCampaign):
    dictStore = agentCouncilStore.fdictCreateCampaignStore(
        sDurableStoreRoot=str(tmp_path / "agentCouncils"))
    agentCouncilStore.fdictRegisterStartedCampaign(dictStore, dictCampaign)
    return dictStore


def testASeedFromAnUnknownCampaignIsNotFound(tmp_path):
    dictStore = agentCouncilStore.fdictCreateCampaignStore(
        sDurableStoreRoot=str(tmp_path / "agentCouncils"))
    with pytest.raises(HTTPException) as excInfo:
        councilRoutes._fsLoadAcceptedPlanSeed(
            dictStore, "campaign-missing", S_RESOURCE, S_REPO_PATH)
    assert excInfo.value.status_code == 404
    assert "campaign-missing" in excInfo.value.detail


def testASeedFromAnotherProjectsCampaignIsRefused(tmp_path):
    dictCampaign = fdictBuildSourceCampaign(
        agentCouncilCampaign.S_STATE_PLAN_ACCEPTED,
        sResourceName="projectBeta")
    dictStore = fdictStoreWith(tmp_path, dictCampaign)
    with pytest.raises(HTTPException) as excInfo:
        councilRoutes._fsLoadAcceptedPlanSeed(
            dictStore, dictCampaign["sCampaignId"], S_RESOURCE, S_REPO_PATH)
    assert excInfo.value.status_code == 409
    assert "different project or repository" in excInfo.value.detail


def testASeedWhosePlanFileVanishedIsRecomposedFromTheRecord(tmp_path):
    dictCampaign = fdictBuildSourceCampaign(
        agentCouncilCampaign.S_STATE_AWAITING_IMPLEMENTATION)
    dictStore = fdictStoreWith(tmp_path, dictCampaign)
    sSeed = councilRoutes._fsLoadAcceptedPlanSeed(
        dictStore, dictCampaign["sCampaignId"], S_RESOURCE, S_REPO_PATH)
    assert sSeed.startswith("# Council plan\n")
    assert "DRAFT" not in sSeed
    assert "Stream the output in bounded chunks." in sSeed
    assert "Replace the buffer with a generator." in sSeed


def testASealedPlanFileIsPreferredOverRecomposition(tmp_path):
    dictCampaign = fdictBuildSourceCampaign(
        agentCouncilCampaign.S_STATE_PLAN_ACCEPTED)
    dictStore = fdictStoreWith(tmp_path, dictCampaign)
    agentCouncilStore.fsAcceptCampaignPlanLocally(
        dictStore, dictCampaign["sCampaignId"], "# The sealed plan\n")
    assert councilRoutes._fsLoadAcceptedPlanSeed(
        dictStore, dictCampaign["sCampaignId"], S_RESOURCE,
        S_REPO_PATH) == "# The sealed plan\n"
