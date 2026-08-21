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
    """The reviewed defect: the enabled path must wear both halves.

    The credential half is the STAGER, not a staged path: staging is
    per turn (the connection stages at runner creation and deletes the
    host file the moment its tarball is built), so nothing is staged
    at build time and no token sits at rest between turns.
    """
    dictCalls = {}
    _fnPatchEgressProvisioning(monkeypatch, dictCalls)

    def _fsStageForTurn():
        dictCalls.setdefault("iStagerCalls", 0)
        dictCalls["iStagerCalls"] += 1
        return "/tmp/stagedCredential.json"

    dictRuntime = _fdictBuildAccessRuntime(
        fsStageRunnerCredential=_fsStageForTurn)
    dictParticipant = {"sRequestedModel": "opus"}

    connectionFirst = controller.fconnectionBuildParticipantConnection(
        dictRuntime, dictParticipant)
    connectionSecond = controller.fconnectionBuildParticipantConnection(
        dictRuntime, {"sRequestedModel": "sonnet"})

    assert connectionFirst.fsStageRunnerCredential is _fsStageForTurn
    assert dictCalls.get("iStagerCalls", 0) == 0, (
        "staging is per turn; building a connection must stage nothing")
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

    def _fsExplodeProxyLaunch(dictGateway, sCampaignId, saAllowedHostnames):
        raise RuntimeError("proxy never reached listening")

    monkeypatch.setattr(
        agentCouncilDockerGateway, "fsLaunchAllowlistProxy",
        _fsExplodeProxyLaunch)
    dictRuntime = _fdictBuildAccessRuntime(
        fsStageRunnerCredential=lambda: "/tmp/stagedCredential.json")
    with pytest.raises(RuntimeError, match="never reached listening"):
        controller.fconnectionBuildParticipantConnection(
            dictRuntime, {"sRequestedModel": "opus"})
    assert dictCalls["listRemovals"] == ["campaign-access-1"]
    assert dictRuntime["dictRunnerAccess"] is None


def _fdictBuildProvisionedRuntime(monkeypatch, dictCalls, sState):
    """A runtime holding provisioned access and a campaign in one state."""
    _fnPatchEgressProvisioning(monkeypatch, dictCalls)
    dictRuntime = _fdictBuildAccessRuntime()
    dictRuntime["dictRunnerAccess"] = {
        "dictEgress": {"sNetworkName": "net", "sProxyInternalAddress": "a",
                       "iProxyPort": 8888},
    }
    dictRuntime["dictCampaign"] = {"sState": sState}
    return dictRuntime


def testTerminalSettleReleasesTheEgressBoundary(monkeypatch):
    """A campaign that can drive no more turns strands nothing."""
    dictCalls = {}
    dictRuntime = _fdictBuildProvisionedRuntime(
        monkeypatch, dictCalls, agentCouncilCampaign.S_STATE_FAILED)
    asyncio.run(controller._fnReleaseRunnerAccessIfSettled(dictRuntime))
    assert dictCalls["listRemovals"] == ["campaign-access-1"]
    assert dictRuntime["dictRunnerAccess"] is None


def testIndeterminateTeardownKeepsTheRetryState(monkeypatch):
    """An unproven removal must not discard the record of what may exist."""
    dictCalls = {}
    dictRuntime = _fdictBuildProvisionedRuntime(
        monkeypatch, dictCalls, agentCouncilCampaign.S_STATE_FAILED)
    monkeypatch.setattr(
        agentCouncilDockerGateway, "fdictRemoveCampaignEgressResources",
        lambda dictGateway, sCampaignId: {
            "bProxyAbsenceProven": False, "bNetworkAbsenceProven": True,
            "saIndeterminateResources": ["vaibifyCouncilProxy-x"]})
    asyncio.run(controller._fnReleaseRunnerAccessIfSettled(dictRuntime))
    assert dictRuntime["dictRunnerAccess"] is not None, (
        "clearing the access on an indeterminate answer discards the "
        "retry state while the proxy may still exist")


def testContinuableStateKeepsRunnerAccess(monkeypatch):
    """A campaign waiting on the researcher keeps its boundary."""
    dictCalls = {}
    dictRuntime = _fdictBuildProvisionedRuntime(
        monkeypatch, dictCalls, agentCouncilCampaign.S_STATE_NEEDS_HUMAN)
    asyncio.run(controller._fnReleaseRunnerAccessIfSettled(dictRuntime))
    assert "listRemovals" not in dictCalls
    assert dictRuntime["dictRunnerAccess"] is not None


def testCredentialIsStagedPerTurnAndDeletedBeforeDelivery(monkeypatch,
                                                          tmp_path):
    """No token copy survives past the tarball build, even mid-turn.

    The order is the contract: stage → read into the delivery tarball →
    DELETE the host file → deliver. A fault after the build cannot
    strand a token, and a paused campaign holds no file at all.
    """
    from vaibify.gui import agentCouncilProviders
    listCallOrder = []
    pathStaged = tmp_path / "stagedCredential.json"

    def _fsStageForTurn():
        pathStaged.write_text('{"claudeAiOauth": {"accessToken": "tok"}}')
        listCallOrder.append("staged")
        return str(pathStaged)

    monkeypatch.setattr(
        secretManager, "fnCleanupSecretFiles",
        lambda listPaths: listCallOrder.append(("cleaned", list(listPaths))))
    monkeypatch.setattr(
        agentCouncilDockerGateway, "fdictReserveAndCreateRunner",
        lambda *tArguments, **dictKeywords: {
            "bCreated": True, "sHandle": "handle-1",
            "sReservationId": "reservation-1"})
    monkeypatch.setattr(
        agentCouncilDockerGateway, "fnCopySnapshotIntoRunner",
        lambda *tArguments, **dictKeywords: None)
    monkeypatch.setattr(
        agentCouncilProviders, "fnDeliverCredentialIntoRunner",
        lambda dictGateway, sHandle, baTar: listCallOrder.append(
            "delivered"))

    connection = agentCouncilProviders.ClaudeRunnerConnection(
        {"bFakeGateway": True}, "campaign-access-1", "sha256:" + "00" * 32,
        b"tar", "opus", dictEgress=None,
        fsStageRunnerCredential=_fsStageForTurn)
    asyncio.run(connection.fdictPrepareImmutableContext({}))
    assert listCallOrder == [
        "staged", ("cleaned", [str(pathStaged)]), "delivered"], (
        "the host copy must be deleted BEFORE delivery, not after")
    assert connection._fdictComposeRunnerEnvironment()[
        "CLAUDE_CONFIG_DIR"] == (
        agentCouncilProviders.S_RUNNER_CLAUDE_CONFIG_DIRECTORY)


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
    """Hub shutdown strands no council network or proxy."""
    dictCalls = {}
    dictRuntime = _fdictBuildProvisionedRuntime(
        monkeypatch, dictCalls, agentCouncilCampaign.S_STATE_PLANNING)
    dictControllerState = controller.fdictCreateCouncilControllerState()
    dictControllerState["dictCampaignRuntime"][
        dictRuntime["sCampaignId"]] = dictRuntime
    asyncio.run(controller.fnAwaitControllerSettleOnShutdown(
        dictControllerState, fDeadlineSeconds=0.1))
    assert dictCalls["listRemovals"] == ["campaign-access-1"]


def testReleaseDrainSettlesAPausedRuntime(monkeypatch, tmp_path):
    """A needsHuman campaign cannot outlive the lease it was built under.

    The reviewed leak: release refused only LIVE drives, and the old
    drain merely set bStopRequested on a paused runtime — nothing
    transitioned, nothing released the boundary. The drain now settles
    the paused runtime: interrupted, checkpointed, egress released,
    runtime dropped.
    """
    dictCalls = {}
    _fnPatchEgressProvisioning(monkeypatch, dictCalls)
    dictStore, dictRegistry, sCampaignId = (
        _tBuildRegisteredPlanningCampaign(tmp_path))
    dictCampaign = agentCouncilStore.fjsonGetCampaignRecord(
        dictStore, sCampaignId)
    dictCampaign["sState"] = agentCouncilCampaign.S_STATE_NEEDS_HUMAN
    dictControllerState = controller.fdictCreateCouncilControllerState()
    dictControllerState["dictCampaignRuntime"][sCampaignId] = {
        "sCampaignId": "campaign-access-1",
        "dictCampaign": dictCampaign,
        "dictStore": dictStore,
        "dictGateway": {"bFakeGateway": True},
        "dictRunnerAccess": {"dictEgress": {"sNetworkName": "net"}},
        "taskDrive": _FakeLiveTask(bDone=True),
        "bLaunchInProgress": False,
    }
    asyncio.run(controller.fnDrainControllerForResource(
        dictControllerState, S_RESOURCE_NAME))
    assert dictCampaign["sState"] == (
        agentCouncilCampaign.S_STATE_INTERRUPTED)
    assert dictCalls["listRemovals"] == ["campaign-access-1"]
    assert dictControllerState["dictCampaignRuntime"] == {}
    dictStored = agentCouncilStore.fjsonGetCampaignRecord(
        dictStore, sCampaignId)
    assert dictStored["sState"] == agentCouncilCampaign.S_STATE_INTERRUPTED


def testDisposeReleasesTheRuntimeAndRefusesWhileLive(monkeypatch):
    """Delete's controller half: refuse live, release and drop paused."""
    dictCalls = {}
    _fnPatchEgressProvisioning(monkeypatch, dictCalls)
    dictControllerState = controller.fdictCreateCouncilControllerState()
    dictControllerState["dictCampaignRuntime"]["campaign-access-1"] = {
        "sCampaignId": "campaign-access-1",
        "dictCampaign": {"dictProjectIdentity": {}},
        "dictGateway": {"bFakeGateway": True},
        "dictRunnerAccess": {"dictEgress": {"sNetworkName": "net"}},
        "taskDrive": _FakeLiveTask(bDone=False),
        "bLaunchInProgress": False,
    }
    with pytest.raises(controller.CouncilCommandError):
        asyncio.run(controller.fdictDisposeCampaignRuntime(
            dictControllerState, "campaign-access-1"))
    dictControllerState["dictCampaignRuntime"]["campaign-access-1"][
        "taskDrive"] = _FakeLiveTask(bDone=True)
    dictDisposed = asyncio.run(controller.fdictDisposeCampaignRuntime(
        dictControllerState, "campaign-access-1"))
    assert dictDisposed["bDisposed"] is True
    assert dictCalls["listRemovals"] == ["campaign-access-1"]
    assert dictControllerState["dictCampaignRuntime"] == {}


def testLaunchWindowCountsAsLive():
    """The provisioning race: taskDrive None but launching = busy."""
    dictControllerState = controller.fdictCreateCouncilControllerState()
    dictControllerState["dictCampaignRuntime"]["campaign-access-1"] = {
        "dictCampaign": {"dictProjectIdentity": {
            "sResourceName": S_RESOURCE_NAME}},
        "taskDrive": None,
        "bLaunchInProgress": True,
    }
    assert controller.fbCampaignDriveIsLive(
        dictControllerState, "campaign-access-1") is True
    assert controller.fbControllerHasLiveDriveForResource(
        dictControllerState, S_RESOURCE_NAME) is True


def testStartupEgressSweepRemovesEveryStoredCampaignsResources():
    """The durable backstop removes composed names, proving absence."""
    import docker as moduleDocker

    class _FakeLowLevelApi:
        def __init__(self):
            self.listRemoved = []

        def remove_container(self, sName, force=False, v=False):
            self.listRemoved.append(sName)

        def inspect_container(self, sName):
            raise moduleDocker.errors.NotFound("gone")

        def remove_network(self, sName):
            self.listRemoved.append(sName)

        def inspect_network(self, sName):
            raise moduleDocker.errors.NotFound("gone")

    class _FakeCouncilClient:
        def __init__(self):
            self.api = _FakeLowLevelApi()

    clientFake = _FakeCouncilClient()
    dictSwept = agentCouncilDockerGateway.fdictSweepCouncilEgressLeftovers(
        clientFake, ["campaign-one", "../hostile", "campaign-two"])
    assert dictSwept["listIndeterminateResources"] == []
    assert sorted(clientFake.api.listRemoved) == sorted([
        "vaibifyCouncilProxy-campaign-one",
        "vaibifyCouncilEgress-campaign-one",
        "vaibifyCouncilProxy-campaign-two",
        "vaibifyCouncilEgress-campaign-two"]), (
        "a hostile campaign id must be skipped, never composed into a "
        "resource name")


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
    # A REAL campaign record and store: the successful release drains
    # the paused runtime through the settle path, which transitions
    # and checkpoints — a bare stub dict would mask a drain that
    # cannot actually settle what it meets.
    dictStore = agentCouncilStore.fdictCreateCampaignStore(
        sDurableStoreRoot=str(tmp_path / "councils"))
    dictCampaign = agentCouncilCampaign.fdictCreateCampaign(
        "Is the pipeline sound?",
        [agentCouncilCampaign.fdictCreateParticipant("claude", "opus"),
         agentCouncilCampaign.fdictCreateParticipant("claude", "sonnet")],
        dictProjectIdentity={
            "sResourceName": "demo",
            "sProjectRepoPath": S_REPO_PATH,
            "sSnapshotIdentity": "",
        })
    agentCouncilCampaign.fnTransitionCampaignState(
        dictCampaign, agentCouncilCampaign.S_STATE_PLANNING,
        "test convened")
    agentCouncilStore.fdictRegisterStartedCampaign(dictStore, dictCampaign)
    dictControllerState["dictCampaignRuntime"][
        dictCampaign["sCampaignId"]] = {
        "sCampaignId": dictCampaign["sCampaignId"],
        "dictCampaign": dictCampaign,
        "dictStore": dictStore,
        "dictGateway": {"bFakeGateway": True},
        "taskDrive": _FakeLiveTask(bDone=False),
        "dictRunnerAccess": None,
        "bLaunchInProgress": False,
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
        dictControllerState["dictCampaignRuntime"][
            dictCampaign["sCampaignId"]][
            "taskDrive"] = _FakeLiveTask(bDone=True)
        responseAfter = client.post(
            "/api/registry/demo/release",
            headers={"X-Vaibify-Lease": sLeaseId})
        assert responseAfter.status_code == 200, responseAfter.text
        assert responseAfter.json()["bReleased"] is True
    # The successful release SETTLED the paused runtime: interrupted,
    # runtime dropped — a campaign cannot continue past its lease.
    assert dictControllerState["dictCampaignRuntime"] == {}
    assert dictCampaign["sState"] == (
        agentCouncilCampaign.S_STATE_INTERRUPTED)
    # And CLOSED council admission atomically, so a respond racing the
    # release is refused at the command gate; a fresh claim reopens.
    assert "demo" in dictControllerState["setClosedResourceAdmissions"]
    with TestClient(app) as clientReclaim:
        assert clientReclaim.post(
            "/api/registry/demo/claim").status_code == 200
    assert "demo" not in dictControllerState["setClosedResourceAdmissions"]


def testIndeterminateTeardownRefusesTheDelete(monkeypatch):
    """Delete cannot drop the record that names what may still exist.

    The startup sweep composes leftover names from STORED campaign
    ids; deleting the record while the daemon answers indeterminately
    would orphan the very network nobody proved gone. The dispose
    refuses (the route answers 409) and the runtime keeps its retry
    state.
    """
    dictCalls = {}
    dictRuntime = _fdictBuildProvisionedRuntime(
        monkeypatch, dictCalls, agentCouncilCampaign.S_STATE_INTERRUPTED)
    monkeypatch.setattr(
        agentCouncilDockerGateway, "fdictRemoveCampaignEgressResources",
        lambda dictGateway, sCampaignId: {
            "bProxyAbsenceProven": True, "bNetworkAbsenceProven": False,
            "saIndeterminateResources": ["vaibifyCouncilEgress-x"]})
    dictRuntime["taskDrive"] = _FakeLiveTask(bDone=True)
    dictControllerState = controller.fdictCreateCouncilControllerState()
    dictControllerState["dictCampaignRuntime"][
        dictRuntime["sCampaignId"]] = dictRuntime
    with pytest.raises(controller.CouncilCommandError) as errorInfo:
        asyncio.run(controller.fdictDisposeCampaignRuntime(
            dictControllerState, dictRuntime["sCampaignId"]))
    assert "orphan" in str(errorInfo.value)
    assert dictRuntime["sCampaignId"] in (
        dictControllerState["dictCampaignRuntime"])
    assert dictRuntime["dictRunnerAccess"] is not None


def testIndeterminateTeardownKeepsTheRuntimeOnReleaseDrain(monkeypatch,
                                                           tmp_path):
    """The drain retains the retry state an unproven removal leaves."""
    dictCalls = {}
    _fnPatchEgressProvisioning(monkeypatch, dictCalls)
    monkeypatch.setattr(
        agentCouncilDockerGateway, "fdictRemoveCampaignEgressResources",
        lambda dictGateway, sCampaignId: {
            "bProxyAbsenceProven": False, "bNetworkAbsenceProven": True,
            "saIndeterminateResources": ["vaibifyCouncilProxy-x"]})
    dictStore, dictRegistry, sCampaignId = (
        _tBuildRegisteredPlanningCampaign(tmp_path))
    dictCampaign = agentCouncilStore.fjsonGetCampaignRecord(
        dictStore, sCampaignId)
    dictControllerState = controller.fdictCreateCouncilControllerState()
    dictControllerState["dictCampaignRuntime"][sCampaignId] = {
        "sCampaignId": "campaign-access-1",
        "dictCampaign": dictCampaign,
        "dictStore": dictStore,
        "dictGateway": {"bFakeGateway": True},
        "dictRunnerAccess": {"dictEgress": {"sNetworkName": "net"}},
        "taskDrive": _FakeLiveTask(bDone=True),
        "bLaunchInProgress": False,
    }
    asyncio.run(controller.fnDrainControllerForResource(
        dictControllerState, S_RESOURCE_NAME))
    assert sCampaignId in dictControllerState["dictCampaignRuntime"], (
        "popping the runtime on an indeterminate teardown discards the "
        "retry state while the proxy may still exist")
    assert dictCampaign["sState"] == (
        agentCouncilCampaign.S_STATE_INTERRUPTED)


def testCancelledLaunchSettlesInsteadOfStrandingTheRuntime(tmp_path):
    """Cancellation takes the same settlement path as any launch fault.

    The reviewed hole: CancelledError was re-raised before cleanup, so
    the runtime stayed registered with bLaunchInProgress True forever —
    permanently busy to every predicate, with possibly-live egress
    nobody would release.
    """
    dictStore, dictRegistry, sCampaignId = (
        _tBuildRegisteredPlanningCampaign(tmp_path))
    dictControllerState = controller.fdictCreateCouncilControllerState()

    async def _fdictCancelCapture():
        raise asyncio.CancelledError()

    async def _fnDriveLaunch():
        await controller.fdictLaunchCampaignDeliberation(
            dictControllerState, dictStore, dictRegistry, sCampaignId,
            _fdictCancelCapture, "sha256:" + "00" * 32)

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(_fnDriveLaunch())
    assert dictControllerState["dictCampaignRuntime"] == {}
    dictStored = agentCouncilStore.fjsonGetCampaignRecord(
        dictStore, sCampaignId)
    assert dictStored["sState"] == agentCouncilCampaign.S_STATE_INTERRUPTED
    assert dictStored["listStateTransitions"][-1]["sReason"] == (
        "launchCancelledBeforeDeliberation")


def testClosedAdmissionRefusesTurnDrivingCommands(tmp_path):
    """After the atomic close, no launch or continuation can pass."""
    dictStore, dictRegistry, sCampaignId = (
        _tBuildRegisteredPlanningCampaign(tmp_path))
    dictControllerState = controller.fdictCreateCouncilControllerState()
    bClean = controller.fbCloseResourceAdmission(
        dictControllerState, S_RESOURCE_NAME)
    assert bClean is True

    async def _fdictNeverCapture():
        raise AssertionError("a closed resource must never reach capture")

    async def _fnDriveLaunch():
        await controller.fdictLaunchCampaignDeliberation(
            dictControllerState, dictStore, dictRegistry, sCampaignId,
            _fdictNeverCapture, "sha256:" + "00" * 32)

    with pytest.raises(controller.CouncilCommandError) as errorInfo:
        asyncio.run(_fnDriveLaunch())
    assert "lease was released" in str(errorInfo.value)
    dictControllerState["dictCampaignRuntime"][sCampaignId] = {
        "dictCampaign": agentCouncilStore.fjsonGetCampaignRecord(
            dictStore, sCampaignId),
        "taskDrive": _FakeLiveTask(bDone=True),
        "bLaunchInProgress": False,
    }
    with pytest.raises(controller.CouncilCommandError):
        asyncio.run(controller.fdictContinueCampaignAfterResponse(
            dictControllerState, dictStore, dictRegistry, sCampaignId,
            "an answer"))
    controller.fnReopenResourceAdmission(
        dictControllerState, S_RESOURCE_NAME)
    assert S_RESOURCE_NAME not in dictControllerState[
        "setClosedResourceAdmissions"]


def testAdmissionCloseSeesADriveThatSlippedIn():
    """The close-then-recheck: a live drive makes the close report dirty."""
    dictControllerState = controller.fdictCreateCouncilControllerState()
    dictControllerState["dictCampaignRuntime"]["campaign-slipped"] = {
        "dictCampaign": {"dictProjectIdentity": {
            "sResourceName": S_RESOURCE_NAME}},
        "taskDrive": _FakeLiveTask(bDone=False),
        "bLaunchInProgress": False,
    }
    assert controller.fbCloseResourceAdmission(
        dictControllerState, S_RESOURCE_NAME) is False, (
        "a drive that spawned before the close must be SEEN by the "
        "re-check so the release refuses instead of proceeding")


def testCancelledBuildThreadCannotRegisterALateRuntime(monkeypatch,
                                                       tmp_path):
    """The reviewer's reproduced race: the worker outlives the cancel.

    Cancelling the awaiting future does not stop the build thread; the
    old handler cleaned up BEFORE the thread registered the runtime,
    which then appeared after cleanup found nothing. The launch now
    shields the build and its failure handler waits the thread out, so
    cleanup always sees what was actually built.
    """
    import threading
    import time as moduleTime
    dictCalls = {}
    _fnPatchEgressProvisioning(monkeypatch, dictCalls)
    dictStore, dictRegistry, sCampaignId = (
        _tBuildRegisteredPlanningCampaign(tmp_path))
    eventBuildStarted = threading.Event()

    def _fdictSlowBuild(dictControllerState, dictStoreArg, dictRegistryArg,
                        sCampaignIdArg, dictCampaign, sImageReference,
                        baSnapshotTar, fsStageRunnerCredential=None):
        eventBuildStarted.set()
        moduleTime.sleep(0.25)
        dictRuntime = {
            "sCampaignId": sCampaignIdArg,
            "dictCampaign": dictCampaign,
            "dictStore": dictStoreArg,
            "dictGateway": {"bFakeGateway": True},
            "dictRunnerAccess": {"dictEgress": {"sNetworkName": "net"}},
            "bLaunchInProgress": True,
            "taskDrive": None,
        }
        dictControllerState["dictCampaignRuntime"][
            sCampaignIdArg] = dictRuntime
        return dictRuntime

    monkeypatch.setattr(
        controller, "_fdictBuildCampaignRuntime", _fdictSlowBuild)

    async def _fdictQuickCapture():
        import tarfile as moduleTar
        sDirectory = os.path.join(
            dictStore["sDurableStoreRoot"], sCampaignId, "snapshot")
        os.makedirs(sDirectory, exist_ok=True)
        with moduleTar.open(
                os.path.join(sDirectory, "snapshot.tar"), "w"):
            pass
        return {"sSnapshotSha256": "fixture-hash"}

    dictControllerState = controller.fdictCreateCouncilControllerState()

    async def _fnDriveCancelledLaunch():
        taskLaunch = asyncio.create_task(
            controller.fdictLaunchCampaignDeliberation(
                dictControllerState, dictStore, dictRegistry, sCampaignId,
                _fdictQuickCapture, "sha256:" + "00" * 32))
        while not eventBuildStarted.is_set():
            await asyncio.sleep(0.01)
        taskLaunch.cancel()
        try:
            await taskLaunch
        except asyncio.CancelledError:
            pass

    asyncio.run(_fnDriveCancelledLaunch())
    assert dictControllerState["dictCampaignRuntime"] == {}, (
        "the build thread registered the runtime AFTER cleanup ran — "
        "the handler must wait the worker out before cleaning")
    assert dictCalls.get("listRemovals") == [sCampaignId], (
        "the late-built egress boundary was never released")


def testHalfProvisionedIndeterminateTeardownKeepsTheTombstone(monkeypatch):
    """A fault mid-provisioning with an unproven cleanup keeps the record.

    The earlier shape cleaned up in-line, ignored the settlement, and
    left dictRunnerAccess None — the outer handler then read "nothing
    to release" and dropped the runtime while the network may still
    exist.
    """
    dictCalls = {}
    _fnPatchEgressProvisioning(monkeypatch, dictCalls)
    monkeypatch.setattr(
        agentCouncilDockerGateway, "fsLaunchAllowlistProxy",
        lambda dictGateway, sCampaignId, saAllowedHostnames: (
            (_ for _ in ()).throw(RuntimeError("proxy never listened"))))
    monkeypatch.setattr(
        agentCouncilDockerGateway, "fdictRemoveCampaignEgressResources",
        lambda dictGateway, sCampaignId: {
            "bProxyAbsenceProven": True, "bNetworkAbsenceProven": False,
            "saIndeterminateResources": ["vaibifyCouncilEgress-x"]})
    dictRuntime = _fdictBuildAccessRuntime(
        fsStageRunnerCredential=lambda: "/tmp/stagedCredential.json")
    with pytest.raises(RuntimeError, match="proxy never listened"):
        controller.fconnectionBuildParticipantConnection(
            dictRuntime, {"sRequestedModel": "opus"})
    assert dictRuntime["dictRunnerAccess"] is not None, (
        "an indeterminate half-provisioning cleanup must keep the "
        "tombstone, or delete can drop the id the startup sweep needs")


def testDrainFaultReopensAdmissionAndKeepsOwnership(monkeypatch, tmp_path):
    """A release that does not commit can never leave admission closed."""
    from fastapi import FastAPI
    from vaibify.config import containerLock
    from vaibify.gui import containerOwnership, sessionLifecycle

    monkeypatch.setattr(containerLock, "_S_LOCK_DIRECTORY", str(tmp_path))

    async def _fnExplodeDrain(dictControllerState, sResourceName):
        raise RuntimeError("drain fell over mid-settlement")

    monkeypatch.setattr(
        controller, "fnDrainControllerForResource", _fnExplodeDrain)
    app = FastAPI()
    app.state.dictContainerOwners = {}
    dictControllerState = controller.fdictCreateCouncilControllerState()
    app.state.dictCouncilControllerState = dictControllerState

    async def _fnDriveFailingRelease():
        iStatus, dictPayload = containerOwnership.ftClaim(
            app.state.dictContainerOwners, "demo",
            containerOwnership.fsMintLease(), 8050)
        assert iStatus == 200
        with pytest.raises(RuntimeError, match="fell over"):
            await sessionLifecycle.ftReleaseExplicit(
                app.state, "demo", dictPayload["sLeaseId"])

    asyncio.run(_fnDriveFailingRelease())
    assert "demo" not in dictControllerState[
        "setClosedResourceAdmissions"], (
        "a non-committing release left council admission closed for a "
        "container that is still owned")
    assert "demo" in app.state.dictContainerOwners
