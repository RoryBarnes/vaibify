"""Two hubs on one Docker daemon must not destroy each other's runners.

The council label is daemon-wide, so a startup reconcile that swept by
that label alone destroyed a live peer's runners mid-deliberation. The
fix is not a new mechanism: a project container is already held by an
exclusive ``fcntl.flock``, so the reconcile can ask who owns a survivor
before settling it.

Every test here drives a REAL foreign flock through a REAL spawned
child process, never a patched predicate. A stub would answer whatever
the test wanted and would have been equally green before the fix --
which is the failure mode this repository has shipped before. The
sparing test and the sweeping test are a falsification PAIR: one proves
a live peer is spared, the other proves the reconcile has not simply
stopped destroying things.
"""

import multiprocessing
import tempfile
import time

import pytest

from vaibify.config import containerLock
from vaibify.gui import agentCouncilDockerGateway
from vaibify.gui import agentCouncilRegistry as registry
from vaibify.gui import agentCouncilRunner


S_PEER_PROJECT = "peerhubproject"
S_OUR_PROJECT = "ourhubproject"
I_PEER_PORT = 8977


def fnHoldContainerFlockInChild(sLockDirectory, sProjectName, iPort,
                                eventRelease):
    """Child: hold the container flock as a genuinely foreign process."""
    import vaibify.config.containerLock as childLockModule
    childLockModule._S_LOCK_DIRECTORY = sLockDirectory
    fileHandleLock = childLockModule.ffileAcquireContainerLock(
        sProjectName, iPort)
    eventRelease.wait(timeout=60)
    childLockModule.fnReleaseContainerLock(fileHandleLock)


@pytest.fixture
def tprocessLivePeerHub():
    """Yield a real child process holding S_PEER_PROJECT's flock.

    ``fdictReadLockHolder`` deliberately reports nothing for the CURRENT
    process, so an in-process acquisition could not stand in for a peer
    even if we wanted it to.
    """
    contextSpawn = multiprocessing.get_context("spawn")
    eventRelease = contextSpawn.Event()
    processHolder = contextSpawn.Process(
        target=fnHoldContainerFlockInChild,
        args=(containerLock._S_LOCK_DIRECTORY, S_PEER_PROJECT,
              I_PEER_PORT, eventRelease))
    processHolder.start()
    try:
        for _ in range(300):
            if containerLock.fdictReadLockHolder(S_PEER_PROJECT):
                break
            time.sleep(0.1)
        else:
            raise AssertionError(
                "the child never acquired the container flock")
        yield processHolder
    finally:
        eventRelease.set()
        processHolder.join(timeout=30)


def _flistRecordingDestroyer(listDestroyedIds):
    """Return a destroy double that RECORDS what it was asked to remove."""
    def fdictDestroyRunnerAndProveAbsence(dockerCouncil, sContainerId):
        listDestroyedIds.append(sContainerId)
        return {"sOutcome": agentCouncilRunner.S_OUTCOME_DESTROYED,
                "sReason": "", "dictProbe": {}}
    return fdictDestroyRunnerAndProveAbsence


def _fnPlantSurvivors(monkeypatch, listSurvivors, listDestroyedIds):
    """Make discovery return these survivors and record every destroy."""
    monkeypatch.setattr(
        agentCouncilDockerGateway, "flistDiscoverLabeledRunners",
        lambda dockerCouncil: list(listSurvivors))
    monkeypatch.setattr(
        agentCouncilDockerGateway, "fdictDestroyRunnerAndProveAbsence",
        _flistRecordingDestroyer(listDestroyedIds))


def _fdictSurvivor(sContainerId, sResourceName):
    """One discovered survivor. Container id != resource name, always.

    The name-vs-id collapse is this repository's recorded fatal bug, so
    the two keys are kept visibly distinct in every fixture here.
    """
    return {"sContainerId": sContainerId,
            "sContainerName": "runner-" + sContainerId,
            "sReservationId": "res-" + sContainerId,
            "sRole": "runner",
            "sResourceName": sResourceName,
            "sStatus": "running"}


def testAReconcileSparesALivePeersRunners(monkeypatch, tprocessLivePeerHub):
    """The bug itself: a booting hub must not destroy a live peer's work."""
    listDestroyedIds = []
    _fnPlantSurvivors(
        monkeypatch,
        [_fdictSurvivor("cPeer", S_PEER_PROJECT)],
        listDestroyedIds)

    dictReport = registry.fdictReconcileLabeledRunnersOnRestart(
        registry.fdictCreateCouncilRegistry(), object())

    assert listDestroyedIds == [], (
        "a live peer hub's runner was destroyed by another hub's startup "
        f"reconcile: {listDestroyedIds}")
    assert dictReport["listSparedToLivePeer"] == ["res-cPeer"]
    assert dictReport["listDestroyed"] == []


def testAReconcileStillSweepsWhenNoPeerHoldsTheProject(monkeypatch):
    """The other half of the pair: crash recovery still recovers.

    Same survivor shape, same code path, no live holder -- so a fix that
    simply stopped destroying things fails here.
    """
    listDestroyedIds = []
    _fnPlantSurvivors(
        monkeypatch,
        [_fdictSurvivor("cOrphan", S_OUR_PROJECT)],
        listDestroyedIds)
    assert not containerLock.fdictReadLockHolder(S_OUR_PROJECT), (
        "the premise failed: something already holds this project's lock")

    dictReport = registry.fdictReconcileLabeledRunnersOnRestart(
        registry.fdictCreateCouncilRegistry(), object())

    assert listDestroyedIds == ["cOrphan"]
    assert dictReport["listDestroyed"] == ["res-cOrphan"]
    assert dictReport["listSparedToLivePeer"] == []


def testOnlyThePeersOwnRunnerIsSpared(monkeypatch, tprocessLivePeerHub):
    """Sparing is per-project, not a blanket amnesty once a peer exists."""
    listDestroyedIds = []
    _fnPlantSurvivors(
        monkeypatch,
        [_fdictSurvivor("cPeer", S_PEER_PROJECT),
         _fdictSurvivor("cOrphan", S_OUR_PROJECT)],
        listDestroyedIds)

    dictReport = registry.fdictReconcileLabeledRunnersOnRestart(
        registry.fdictCreateCouncilRegistry(), object())

    assert listDestroyedIds == ["cOrphan"]
    assert dictReport["listSparedToLivePeer"] == ["res-cPeer"]
    assert dictReport["iSurvivorsDiscovered"] == 2


def testAnUnlabeledSurvivorIsStillSwept(monkeypatch, tprocessLivePeerHub):
    """Unattributable means sweepable, even while a peer is live.

    A survivor carrying no resource label cannot be shown to belong to
    anyone living, so the fail-closed answer is the old behaviour. Note
    the live peer in this test: the sparing must not generalize from
    "some hub is alive" to "leave everything alone".
    """
    listDestroyedIds = []
    dictSurvivor = _fdictSurvivor("cLegacy", S_PEER_PROJECT)
    del dictSurvivor["sResourceName"]
    _fnPlantSurvivors(monkeypatch, [dictSurvivor], listDestroyedIds)

    dictReport = registry.fdictReconcileLabeledRunnersOnRestart(
        registry.fdictCreateCouncilRegistry(), object())

    assert listDestroyedIds == ["cLegacy"]
    assert dictReport["listSparedToLivePeer"] == []


def testTheRunnerSpecificationStampsTheResourceLabel():
    """Sparing is only possible if creation recorded the owner."""
    dictSpecification = (
        agentCouncilRunner.fdictComposeRunnerCreateSpecification(
            "image@sha256:" + "ab" * 32, "res-1",
            sResourceName=S_OUR_PROJECT))
    dictLabels = dictSpecification["dictCreateKeywords"]["labels"]
    assert dictLabels[
        agentCouncilRunner.S_COUNCIL_RESOURCE_LABEL] == S_OUR_PROJECT


def testTheGatewayStampsItsResourceOntoEveryRunnerItCreates(monkeypatch):
    """The gateway is the single place the owner can be forgotten."""
    dictCapturedLabels = {}

    class _FakeContainers:
        def create(self, sImageReference, **dictKeywords):
            dictCapturedLabels.update(dictKeywords["labels"])
            return SimpleFakeContainer()

    class SimpleFakeContainer:
        id = "container-created-id"

        def start(self):
            return None

    dictGateway = (
        agentCouncilDockerGateway.fdictCreateCouncilDockerGateway(
            type("FakeDocker", (), {"containers": _FakeContainers()})(),
            registry.fdictCreateCouncilRegistry(),
            S_OUR_PROJECT))

    dictCreated = agentCouncilDockerGateway.fdictReserveAndCreateRunner(
        dictGateway, "campaign-1", "claude",
        {"iMemoryBytes": 1, "fCpuCount": 0.1, "iPidsLimit": 1},
        "image@sha256:" + "cd" * 32)

    assert dictCreated["bCreated"] is True
    assert dictCapturedLabels[
        agentCouncilRunner.S_COUNCIL_RESOURCE_LABEL] == S_OUR_PROJECT


def testTheControllerGivesItsGatewayTheCampaignsProject(monkeypatch):
    """The production threading, which no other test here exercises.

    Everything above proves the reconcile spares correctly GIVEN a
    stamped runner, and that the gateway stamps GIVEN a resource name.
    This is the join: the controller must hand the campaign's own
    project identity to the gateway it builds, or production creates
    unattributable runners and every peer sweeps them.
    """
    from vaibify.gui import agentCouncilController

    monkeypatch.setattr(
        agentCouncilDockerGateway, "fdockerCreateCouncilClient",
        lambda: object())
    dictRuntime = {
        "dictGateway": None,
        "dictRegistry": registry.fdictCreateCouncilRegistry(),
        "dictCampaign": {
            "dictProjectIdentity": {"sResourceName": S_OUR_PROJECT},
        },
    }

    dictGateway = agentCouncilController._fdictEnsureRuntimeGateway(
        dictRuntime)

    assert dictGateway["sResourceName"] == S_OUR_PROJECT


# --- The store lane: the runners were spared, their campaigns were not ---
#
# The runner reconcile learned to ask who owns a survivor. The two
# passes that run beside it — reload-and-classify, and the egress sweep
# — kept enumerating from the machine-wide durable store and treating
# everything in it as this hub's leftovers. Same daemon-wide over-reach,
# one layer up, on the campaign records rather than the containers.


def _fdictBuildStoreHolding(sCampaignId, sResourceName, sState="planning"):
    """A campaign store holding one reloaded campaign, checkpoint and all."""
    from vaibify.gui import agentCouncilStore

    dictStore = agentCouncilStore.fdictCreateCampaignStore(
        sDurableStoreRoot=tempfile.mkdtemp(prefix="councilPeerStore"))
    dictCampaign = {
        "sCampaignId": sCampaignId,
        "sState": sState,
        "sQuestion": "does a peer hub own this?",
        "listParticipants": [],
        "listStateTransitions": [],
        "dictProjectIdentity": {"sResourceName": sResourceName,
                                "sProjectRepoPath": "/workspace/repo",
                                "sSnapshotIdentity": "snap-1",
                                "sSnapshotScopeNote": ""},
    }
    agentCouncilStore.fdictRegisterStartedCampaign(dictStore, dictCampaign)
    return dictStore


def testALivePeersCampaignIsNotClassifiedInterrupted(tprocessLivePeerHub):
    """A working council must not be declared dead by a booting neighbour.

    "Planning with no runner I can see" is true of a crash AND of a
    council another hub is running right now, and this pass could not
    tell them apart. It rewrote the peer's checkpoint — the record the
    peer's own hub reloads if it ever restarts.
    """
    from vaibify.gui import agentCouncilController, agentCouncilStore

    dictStore = _fdictBuildStoreHolding("campaign-peer", S_PEER_PROJECT)

    iClassified = agentCouncilController.fiClassifyInterruptedCampaignsOnStartup(
        dictStore)

    assert iClassified == 0
    assert agentCouncilStore.fjsonGetCampaignRecord(
        dictStore, "campaign-peer")["sState"] == "planning"


def testAnOrphanedCampaignIsStillClassifiedInterrupted():
    """The other half: crash recovery must still recover.

    Same shape, same code path, no live holder — so a fix that simply
    stopped classifying anything fails here.
    """
    from vaibify.gui import agentCouncilController, agentCouncilStore

    dictStore = _fdictBuildStoreHolding("campaign-orphan", S_OUR_PROJECT)
    assert not containerLock.fdictReadLockHolder(S_OUR_PROJECT), (
        "the premise failed: something already holds this project's lock")

    iClassified = agentCouncilController.fiClassifyInterruptedCampaignsOnStartup(
        dictStore)

    assert iClassified == 1
    assert agentCouncilStore.fjsonGetCampaignRecord(
        dictStore, "campaign-orphan")["sState"] == "interrupted"


def testALivePeersEgressIsNotSweptAtStartup(tprocessLivePeerHub):
    """Removing a peer's proxy and network cuts its running turns' egress.

    The runners were already spared by the reconcile above; their egress
    was not, so a spared runner lost the network it reaches its provider
    through — a subtler kill than destroying the container, and one the
    runner-level test cannot see.
    """
    from vaibify.gui import appFactory

    dictStore = _fdictBuildStoreHolding("campaign-peer", S_PEER_PROJECT)

    assert appFactory._flistSelectSweepableCampaigns(dictStore) == []


def testAnOrphanedCampaignsEgressIsStillSwept():
    """The falsification twin: the backstop still backstops."""
    from vaibify.gui import appFactory

    dictStore = _fdictBuildStoreHolding("campaign-orphan", S_OUR_PROJECT)

    assert appFactory._flistSelectSweepableCampaigns(dictStore) == [
        "campaign-orphan"]


def testACampaignWithNoRecordedOwnerStaysSweepable(tprocessLivePeerHub):
    """Unattributable means sweepable, exactly as for a runner.

    Note the live peer: the sparing must not generalize from "some hub
    is alive" to "leave everything alone", and a record predating the
    identity binding must not become permanently unsweepable.
    """
    from vaibify.gui import appFactory

    dictStore = _fdictBuildStoreHolding("campaign-legacy", "")

    assert appFactory._flistSelectSweepableCampaigns(dictStore) == [
        "campaign-legacy"]
