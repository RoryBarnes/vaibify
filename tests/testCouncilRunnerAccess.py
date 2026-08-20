"""The production launch path actually carries credentials and egress.

The review that motivated these tests found the sharpest possible gap:
every fake lane was green while the PRODUCTION connection factory built
runners with no egress network and no credential — a shape whose first
enabled-gate launch would burn paid runners on containers that can
reach nothing and log in as nobody. These tests pin the enabled path at
the unit seam, with the Docker gateway functions patched (lane 2 of
``design/agentCouncilVerificationLanes.md``: no daemon, no login):

- the default factory provisions the campaign's egress boundary and
  staged credential once, threads both into every participant's
  connection, and memoizes across participants;
- a runtime launched with no credential stager REFUSES rather than
  building a credential-less production connection;
- a provisioning fault tears the egress boundary back down;
- runner access is released exactly when the campaign can drive no
  further turn, on the drive-settle, stop, and shutdown paths;
- a failed launch leaves a ``failed`` record, never a phantom
  ``planning`` one (the transactional-start contract);
- the release busy-predicate sees a live drive for the right resource
  and only that resource;
- staleness moves when a dirty file's CONTENT changes even though the
  porcelain digest cannot see it.
"""

import asyncio
import os

import pytest

from vaibify.gui import agentCouncilCampaign
from vaibify.gui import agentCouncilController as controller
from vaibify.gui import agentCouncilDockerGateway
from vaibify.gui import agentCouncilRegistry
from vaibify.gui import agentCouncilStore
from vaibify.config import secretManager


S_RESOURCE_NAME = "vaibify-council-project"
S_REPO_PATH = "/workspace/sampleRepo"


def _fdictBuildAccessRuntime(sCampaignId="campaign-access-1",
                             fsStageRunnerCredential=None):
    """A minimal runtime dict shaped like the controller builds."""
    return {
        "sCampaignId": sCampaignId,
        "sImageReference": "ubuntu:24.04",
        "baSnapshotTar": b"tarbytes",
        "dictGateway": {"bFakeGateway": True},
        "dictRunnerAccess": None,
        "fsStageRunnerCredential": fsStageRunnerCredential,
        "taskDrive": None,
        "sTurnId": "",
    }


def _fnPatchEgressProvisioning(monkeypatch, dictCalls):
    """Route the gateway's egress builders onto recorders."""
    monkeypatch.setattr(
        agentCouncilDockerGateway, "fsCreateCampaignInternalNetwork",
        lambda dictGateway, sCampaignId: dictCalls.setdefault(
            "listNetworks", []).append(sCampaignId) or (
            f"vaibifyCouncilEgress-{sCampaignId}"))
    monkeypatch.setattr(
        agentCouncilDockerGateway, "fsLaunchAllowlistProxy",
        lambda dictGateway, sCampaignId, saAllowedHostnames: dictCalls
        .setdefault("listAllowlists", []).append(list(saAllowedHostnames))
        or "172.30.0.2")
    monkeypatch.setattr(
        agentCouncilDockerGateway, "fdictRemoveCampaignEgressResources",
        lambda dictGateway, sCampaignId: dictCalls.setdefault(
            "listRemovals", []).append(sCampaignId) or {
            "bProxyAbsenceProven": True, "bNetworkAbsenceProven": True,
            "saIndeterminateResources": []})


def testProductionFactoryThreadsEgressAndCredentialAndMemoizes(monkeypatch):
    """The reviewed defect: the enabled path must wear both halves."""
    dictCalls = {}
    _fnPatchEgressProvisioning(monkeypatch, dictCalls)
    dictRuntime = _fdictBuildAccessRuntime(
        fsStageRunnerCredential=lambda: "/tmp/stagedCredential.json")
    dictParticipant = {"sRequestedModel": "opus"}

    connectionFirst = controller.fconnectionBuildParticipantConnection(
        dictRuntime, dictParticipant)
    connectionSecond = controller.fconnectionBuildParticipantConnection(
        dictRuntime, {"sRequestedModel": "sonnet"})

    assert connectionFirst.sHostCredentialPath == (
        "/tmp/stagedCredential.json")
    assert connectionFirst.dictEgress == {
        "sNetworkName": "vaibifyCouncilEgress-campaign-access-1",
        "sProxyInternalAddress": "172.30.0.2",
        "iProxyPort": 8888,
    }
    assert connectionSecond.dictEgress == connectionFirst.dictEgress
    # One boundary per campaign: the second participant reuses it.
    assert dictCalls["listNetworks"] == ["campaign-access-1"]
    assert dictCalls["listAllowlists"] == [["api.anthropic.com"]]


def testProvisioningWithoutAStagerRefuses(monkeypatch):
    """A production connection with no credential source must not build."""
    dictCalls = {}
    _fnPatchEgressProvisioning(monkeypatch, dictCalls)
    dictRuntime = _fdictBuildAccessRuntime(fsStageRunnerCredential=None)
    with pytest.raises(controller.CouncilCommandError) as errorInfo:
        controller.fconnectionBuildParticipantConnection(
            dictRuntime, {"sRequestedModel": "opus"})
    assert "credential stager" in str(errorInfo.value)
    assert "listNetworks" not in dictCalls, (
        "no egress may be provisioned for a connection that cannot exist")


def testProvisioningFaultTearsTheEgressBackDown(monkeypatch):
    """A half-provisioned boundary must not outlive its failed launch."""
    dictCalls = {}
    _fnPatchEgressProvisioning(monkeypatch, dictCalls)

    def _fsExplodeStager():
        raise RuntimeError("no persisted login")

    dictRuntime = _fdictBuildAccessRuntime(
        fsStageRunnerCredential=_fsExplodeStager)
    with pytest.raises(RuntimeError, match="no persisted login"):
        controller.fconnectionBuildParticipantConnection(
            dictRuntime, {"sRequestedModel": "opus"})
    assert dictCalls["listRemovals"] == ["campaign-access-1"]
    assert dictRuntime["dictRunnerAccess"] is None


def _fdictBuildProvisionedRuntime(monkeypatch, dictCalls, sState):
    """A runtime holding provisioned access and a campaign in one state."""
    _fnPatchEgressProvisioning(monkeypatch, dictCalls)
    listCleaned = dictCalls.setdefault("listCleanedCredentialPaths", [])
    monkeypatch.setattr(
        secretManager, "fnCleanupSecretFiles",
        lambda listPaths: listCleaned.extend(listPaths))
    dictRuntime = _fdictBuildAccessRuntime()
    dictRuntime["dictRunnerAccess"] = {
        "dictEgress": {"sNetworkName": "net", "sProxyInternalAddress": "a",
                       "iProxyPort": 8888},
        "sHostCredentialPath": "/tmp/stagedCredential.json",
    }
    dictRuntime["dictCampaign"] = {"sState": sState}
    return dictRuntime


def testTerminalSettleReleasesCredentialAndEgress(monkeypatch):
    """A campaign that can drive no more turns strands nothing."""
    dictCalls = {}
    dictRuntime = _fdictBuildProvisionedRuntime(
        monkeypatch, dictCalls, agentCouncilCampaign.S_STATE_FAILED)
    asyncio.run(controller._fnReleaseRunnerAccessIfSettled(dictRuntime))
    assert dictCalls["listCleanedCredentialPaths"] == [
        "/tmp/stagedCredential.json"]
    assert dictCalls["listRemovals"] == ["campaign-access-1"]
    assert dictRuntime["dictRunnerAccess"] is None


def testContinuableStateKeepsRunnerAccess(monkeypatch):
    """A campaign waiting on the researcher keeps its boundary."""
    dictCalls = {}
    dictRuntime = _fdictBuildProvisionedRuntime(
        monkeypatch, dictCalls, agentCouncilCampaign.S_STATE_NEEDS_HUMAN)
    asyncio.run(controller._fnReleaseRunnerAccessIfSettled(dictRuntime))
    assert dictCalls["listCleanedCredentialPaths"] == []
    assert "listRemovals" not in dictCalls
    assert dictRuntime["dictRunnerAccess"] is not None


def _tBuildRegisteredPlanningCampaign(tmp_path):
    """A store holding one registered planning campaign, plus registry."""
    dictStore = agentCouncilStore.fdictCreateCampaignStore(
        sDurableStoreRoot=str(tmp_path / "councils"))
    dictRegistry = agentCouncilRegistry.fdictCreateCouncilRegistry()
    dictCampaign = agentCouncilCampaign.fdictCreateCampaign(
        "Is the pipeline sound?",
        [agentCouncilCampaign.fdictCreateParticipant("claude", "opus"),
         agentCouncilCampaign.fdictCreateParticipant("claude", "sonnet")],
        dictProjectIdentity={
            "sResourceName": S_RESOURCE_NAME,
            "sProjectRepoPath": S_REPO_PATH,
            "sSnapshotIdentity": "",
        })
    agentCouncilCampaign.fnTransitionCampaignState(
        dictCampaign, agentCouncilCampaign.S_STATE_PLANNING,
        "test convened")
    agentCouncilStore.fdictRegisterStartedCampaign(dictStore, dictCampaign)
    return dictStore, dictRegistry, dictCampaign["sCampaignId"]


def testFailedLaunchLeavesAFailedRecordNeverAPhantomPlanningOne(tmp_path):
    """Transactional start: capture faults, the record says failed."""
    dictStore, dictRegistry, sCampaignId = (
        _tBuildRegisteredPlanningCampaign(tmp_path))
    dictControllerState = controller.fdictCreateCouncilControllerState()

    async def _fdictExplodeCapture():
        raise RuntimeError("capture blew up")

    async def _fnDriveLaunch():
        await controller.fdictLaunchCampaignDeliberation(
            dictControllerState, dictStore, dictRegistry, sCampaignId,
            _fdictExplodeCapture, "ubuntu:24.04")

    with pytest.raises(RuntimeError, match="capture blew up"):
        asyncio.run(_fnDriveLaunch())
    dictStored = agentCouncilStore.fjsonGetCampaignRecord(
        dictStore, sCampaignId)
    assert dictStored["sState"] == agentCouncilCampaign.S_STATE_FAILED
    sLastReason = dictStored["listStateTransitions"][-1]["sReason"]
    assert sLastReason.startswith("launchFailedBeforeDeliberation")
    assert dictControllerState["dictCampaignRuntime"] == {}


class _FakeLiveTask:
    """A task double whose liveness the predicate reads via done()."""

    def __init__(self, bDone):
        self._bDone = bDone

    def done(self):
        return self._bDone


def testLiveDrivePredicateSeesOnlyItsResource():
    """The release busy-check: right resource, live task, nothing else."""
    dictControllerState = controller.fdictCreateCouncilControllerState()
    dictControllerState["dictCampaignRuntime"]["campaign-live"] = {
        "dictCampaign": {"dictProjectIdentity": {
            "sResourceName": S_RESOURCE_NAME}},
        "taskDrive": _FakeLiveTask(bDone=False),
    }
    dictControllerState["dictCampaignRuntime"]["campaign-done"] = {
        "dictCampaign": {"dictProjectIdentity": {
            "sResourceName": "other-project"}},
        "taskDrive": _FakeLiveTask(bDone=False),
    }
    assert controller.fbControllerHasLiveDriveForResource(
        dictControllerState, S_RESOURCE_NAME) is True
    assert controller.fbControllerHasLiveDriveForResource(
        dictControllerState, "third-project") is False
    dictControllerState["dictCampaignRuntime"]["campaign-live"][
        "taskDrive"] = _FakeLiveTask(bDone=True)
    assert controller.fbControllerHasLiveDriveForResource(
        dictControllerState, S_RESOURCE_NAME) is False


def testShutdownSettleReleasesEveryCampaignsRunnerAccess(monkeypatch):
    """Hub shutdown strands no token copy and no council network."""
    dictCalls = {}
    dictRuntime = _fdictBuildProvisionedRuntime(
        monkeypatch, dictCalls, agentCouncilCampaign.S_STATE_PLANNING)
    dictControllerState = controller.fdictCreateCouncilControllerState()
    dictControllerState["dictCampaignRuntime"][
        dictRuntime["sCampaignId"]] = dictRuntime
    asyncio.run(controller.fnAwaitControllerSettleOnShutdown(
        dictControllerState, fDeadlineSeconds=0.1))
    assert dictCalls["listCleanedCredentialPaths"] == [
        "/tmp/stagedCredential.json"]
    assert dictCalls["listRemovals"] == ["campaign-access-1"]


def testStalenessMovesWhenADirtyFilesContentChanges(tmp_path):
    """The porcelain digest cannot see worktree bytes; the path map can."""
    import json
    from vaibify.gui import agentCouncilContext
    from vaibify.gui.routes import councilRoutes

    dictBaselineIdentities = {
        "analysis/results.txt": {"sType": "file", "sIdentity": "a" * 40}}
    sCampaignId = "campaign-stale-1"
    sSnapshotDirectory = os.path.join(
        str(tmp_path), sCampaignId, "snapshot")
    os.makedirs(sSnapshotDirectory)
    with open(os.path.join(sSnapshotDirectory, "manifest.json"), "w",
              encoding="utf-8") as fileManifest:
        json.dump({
            "sBaselineHeadSha": "c" * 40,
            "sBaselinePorcelainDigest": "porcelain-digest",
            "sBaselinePathIdentitiesDigest":
                agentCouncilContext.fsComputePathIdentitiesDigest(
                    dictBaselineIdentities),
        }, fileManifest)

    class _FakeStalenessDocker:
        def __init__(self, dictPathIdentities):
            self.dictPathIdentities = dictPathIdentities

        def fdictFetchWorktreeIdentities(self, sContainerId, sRepoPath):
            return {"bSuccess": True, "sHeadSha": "c" * 40,
                    "sPorcelainDigest": "porcelain-digest",
                    "dictPathIdentities": self.dictPathIdentities}

    dictStore = {"sDurableStoreRoot": str(tmp_path)}
    dictFresh = councilRoutes._fdictComputeBaselineStaleness(
        {"docker": _FakeStalenessDocker(dict(dictBaselineIdentities))},
        dictStore, "cid", S_REPO_PATH, sCampaignId)
    assert dictFresh["bPlanningBaselineStale"] is False

    dictStale = councilRoutes._fdictComputeBaselineStaleness(
        {"docker": _FakeStalenessDocker({
            "analysis/results.txt": {
                "sType": "file", "sIdentity": "b" * 40}})},
        dictStore, "cid", S_REPO_PATH, sCampaignId)
    assert dictStale["bPlanningBaselineStale"] is True
    assert "file contents changed" in dictStale["sPlanningBaselineSummary"]


def testReleaseRefusesWhileACouncilIsDeliberatingThenAllows(tmp_path,
                                                            monkeypatch):
    """A live drive is paid work; the lease release refuses, then allows.

    Driven over the HUB app (the release route's real home), with the
    council-busy refusal NOT force-overridable: it sits with the
    live-run and guarded-mutation refusals, above the ``bForce`` gate.
    """
    from fastapi import FastAPI
    from starlette.testclient import TestClient
    from vaibify.config import containerLock
    from vaibify.gui.registryRoutes import fnRegisterRegistryRoutes

    monkeypatch.setattr(containerLock, "_S_LOCK_DIRECTORY", str(tmp_path))
    app = FastAPI()
    app.state.dictContainerOwners = {}
    app.state.iHubPort = 8050
    fnRegisterRegistryRoutes(
        app, {"require": lambda *tArguments: None, "docker": None})
    dictControllerState = controller.fdictCreateCouncilControllerState()
    app.state.dictCouncilControllerState = dictControllerState
    dictControllerState["dictCampaignRuntime"]["campaign-live"] = {
        "dictCampaign": {
            "dictProjectIdentity": {"sResourceName": "demo"},
            "sState": agentCouncilCampaign.S_STATE_PLANNING,
        },
        "taskDrive": _FakeLiveTask(bDone=False),
        "dictRunnerAccess": None,
    }
    with TestClient(app) as client:
        sLeaseId = client.post(
            "/api/registry/demo/claim").json()["sLeaseId"]
        response = client.post(
            "/api/registry/demo/release",
            headers={"X-Vaibify-Lease": sLeaseId})
        assert response.status_code == 409, response.text
        assert "Agent Council" in response.json()["detail"]["sMessage"]
        responseForced = client.post(
            "/api/registry/demo/release",
            headers={"X-Vaibify-Lease": sLeaseId},
            json={"bForce": True})
        assert responseForced.status_code == 409, (
            "force must not override the council-busy refusal")
        dictControllerState["dictCampaignRuntime"]["campaign-live"][
            "taskDrive"] = _FakeLiveTask(bDone=True)
        responseAfter = client.post(
            "/api/registry/demo/release",
            headers={"X-Vaibify-Lease": sLeaseId})
        assert responseAfter.status_code == 200, responseAfter.text
        assert responseAfter.json()["bReleased"] is True
