"""Peer-hub isolation against a REAL daemon and a REAL foreign flock.

``testCouncilPeerHubIsolation.py`` stubs discovery, so it proves the
reconcile's decision logic and nothing about whether the owner label
survives a round trip through Docker. That gap matters here: the whole
fix rests on a label the daemon has to store and hand back, and a
label that never reached the daemon would leave every unit test green
while a peer hub kept destroying live runners.

So this lane creates a REAL container through the gateway, holds a REAL
flock in a REAL child process, and runs the REAL reconcile against the
REAL daemon. The pair is the point: spared while the peer lives,
destroyed once it does not.
"""

import multiprocessing
import os
import secrets
import time

import pytest

from tests.testDockerConnectionLive import fnRequireDaemonReachable
from vaibify.config import containerLock
from vaibify.gui import agentCouncilDockerGateway as moduleGateway
from vaibify.gui import agentCouncilRegistry as registry
from vaibify.gui import agentCouncilRunner

pytestmark = pytest.mark.docker_live

S_RUNNER_TEST_IMAGE = os.environ.get(
    "VAIBIFY_COUNCIL_TEST_IMAGE", "python:3.10-slim")
I_MEBIBYTE = 1024 * 1024
I_PEER_PORT = 8979


def _fdictSmallLimits():
    dictLimits = agentCouncilRunner.fdictBuildDefaultRunnerLimits()
    dictLimits.update({
        "iMemoryBytes": 256 * I_MEBIBYTE,
        "iWorkingTreeBytes": 64 * I_MEBIBYTE,
        "iScratchBytes": 16 * I_MEBIBYTE,
        "iPidsLimit": 64,
    })
    return dictLimits


def fnHoldContainerFlockInChild(sLockDirectory, sProjectName, iPort,
                                eventRelease):
    """Child: hold the container flock as a genuinely foreign process."""
    import vaibify.config.containerLock as childLockModule
    childLockModule._S_LOCK_DIRECTORY = sLockDirectory
    fileHandleLock = childLockModule.ffileAcquireContainerLock(
        sProjectName, iPort)
    eventRelease.wait(timeout=60)
    childLockModule.fnReleaseContainerLock(fileHandleLock)


def _tStartLivePeerHolding(sProjectName):
    """Start a real child holding the project's flock; return (proc, event)."""
    contextSpawn = multiprocessing.get_context("spawn")
    eventRelease = contextSpawn.Event()
    processHolder = contextSpawn.Process(
        target=fnHoldContainerFlockInChild,
        args=(containerLock._S_LOCK_DIRECTORY, sProjectName, I_PEER_PORT,
              eventRelease))
    processHolder.start()
    for _ in range(300):
        if containerLock.fdictReadLockHolder(sProjectName):
            return processHolder, eventRelease
        time.sleep(0.1)
    eventRelease.set()
    processHolder.join(timeout=30)
    raise AssertionError("the child never acquired the container flock")


@pytest.fixture
def tLiveCouncilRunnerForAProject():
    """Yield a factory making REAL labelled runners, and clean up after.

    The factory returns (sContainerId, sProjectName) so a caller can
    assert on the daemon's own view of the container afterwards.
    """
    fnRequireDaemonReachable()
    dockerCouncil = moduleGateway.fdockerCreateCouncilClient()
    listCreatedIds = []

    def _tCreateRunnerOwnedBy(sProjectName):
        dictGateway = moduleGateway.fdictCreateCouncilDockerGateway(
            dockerCouncil, registry.fdictCreateCouncilRegistry(),
            sProjectName)
        dictLimits = _fdictSmallLimits()
        dictCreated = moduleGateway.fdictReserveAndCreateRunner(
            dictGateway, f"peeriso{secrets.token_hex(4)}", "claude",
            {"iMemoryBytes": dictLimits["iMemoryBytes"],
             "fCpuCount": dictLimits["fCpuCount"]},
            S_RUNNER_TEST_IMAGE, dictLimits=dictLimits)
        assert dictCreated["bCreated"] is True, dictCreated
        sContainerId = dictGateway["dictHandlesById"][
            dictCreated["sHandle"]]["sContainerId"]
        listCreatedIds.append(sContainerId)
        return sContainerId

    try:
        yield _tCreateRunnerOwnedBy
    finally:
        for sContainerId in listCreatedIds:
            try:
                dockerCouncil.api.remove_container(
                    sContainerId, force=True, v=True)
            except Exception:
                pass


def _fbContainerStillExists(sContainerId):
    """Ask the daemon directly, never the gateway's own bookkeeping."""
    dockerCouncil = moduleGateway.fdockerCreateCouncilClient()
    try:
        dockerCouncil.containers.get(sContainerId)
        return True
    except Exception:
        return False


def testTheOwnerLabelSurvivesTheRoundTripThroughDocker(
    tLiveCouncilRunnerForAProject,
):
    """Discovery must read back the owner the create stamped.

    Everything else here depends on this, and it is precisely what a
    stubbed discovery cannot show.
    """
    sProjectName = "liveownerproject"
    sContainerId = tLiveCouncilRunnerForAProject(sProjectName)

    listSurvivors = moduleGateway.flistDiscoverLabeledRunners(
        moduleGateway.fdockerCreateCouncilClient())
    listMine = [dictSurvivor for dictSurvivor in listSurvivors
                if dictSurvivor["sContainerId"] == sContainerId]

    assert listMine, "the labelled runner was not discovered at all"
    assert listMine[0]["sResourceName"] == sProjectName


def testARealReconcileSparesALivePeersRealRunner(
    tLiveCouncilRunnerForAProject,
):
    """The bug, end to end: a booting hub leaves a live peer's runner up."""
    sProjectName = "livepeerproject"
    sContainerId = tLiveCouncilRunnerForAProject(sProjectName)
    processHolder, eventRelease = _tStartLivePeerHolding(sProjectName)
    try:
        dictReport = registry.fdictReconcileLabeledRunnersOnRestart(
            registry.fdictCreateCouncilRegistry(),
            moduleGateway.fdockerCreateCouncilClient())
    finally:
        eventRelease.set()
        processHolder.join(timeout=30)

    assert _fbContainerStillExists(sContainerId), (
        "a live peer hub's REAL runner was destroyed by another hub's "
        "startup reconcile")
    assert any(sReservation.startswith("council-")
               for sReservation in dictReport["listSparedToLivePeer"])


def testARealReconcileDestroysTheRunnerOnceThePeerIsGone(
    tLiveCouncilRunnerForAProject,
):
    """The falsifying half: sparing is conditional, not a blanket refusal.

    Identical container, identical project, identical call -- the only
    difference is that no process holds the lease. If this passed too,
    the sparing test above would prove nothing.
    """
    sProjectName = "livegoneproject"
    sContainerId = tLiveCouncilRunnerForAProject(sProjectName)
    assert not containerLock.fdictReadLockHolder(sProjectName), (
        "the premise failed: something already holds this project's lock")

    registry.fdictReconcileLabeledRunnersOnRestart(
        registry.fdictCreateCouncilRegistry(),
        moduleGateway.fdockerCreateCouncilClient())

    assert not _fbContainerStillExists(sContainerId), (
        "crash recovery regressed: an orphaned runner survived a "
        "reconcile with no live peer holding its project")
