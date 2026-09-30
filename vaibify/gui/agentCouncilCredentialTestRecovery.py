"""Restart recovery for council credential tests.

A credential test runs on its hub's own thread and holds a per-key test
lock for its whole life. When that hub dies, three things can be left
behind: a job still marked ``running`` (which keeps its provider
suspended), the job's runners and network, and — for the milliseconds
one exists — a staged token copy. A finished job may also have left
resources whose removal could not be PROVEN. This module settles all of
them, and is careful about what it must NOT touch: a job a live peer
hub is still running, and a token copy a live process still holds.

It changes on the hub's lifecycle, not with the checks, which is why it
is its own module beside ``agentCouncilCredentialTest``.
"""

from . import agentCouncilCredentialGate
from . import agentCouncilCredentialStore
from . import agentCouncilCredentialTest
from . import agentCouncilRunner
from . import agentCouncilStagedCopies
from .agentCouncilCredentialTestRecords import (
    S_JOB_RUNNING,
    flistReadAllJobRecords,
    flistReadRunningJobRecords,
    fnWriteJobRecord,
)

__all__ = ["fdictSweepOrphanedCredentialTests"]


def fdictSweepOrphanedCredentialTests(dockerCouncil=None):
    """Settle every job whose hub died; never touch a live peer's job.

    Two proofs a job is NOT orphaned, either of which spares it: a
    DIFFERENT live process holds the job's project lock (the peer-hub
    test campaigns use), or the job's own test lock is still held —
    the lock its hub keeps for the whole test and the kernel drops the
    moment that hub dies. A swept job is recorded ``incomplete`` and its
    runners and egress removed; council token copies no live process
    holds are deleted whatever their age; and a FINISHED job whose
    clean-up was left unproven is retried until it is proven.
    """
    from ..config import containerLock
    sDirectory = agentCouncilCredentialGate.fsResolveCredentialStoreDirectory()
    dictReport = {"listSwept": [], "listSpared": [], "listReconciled": [],
                  "iStagedCopiesRemoved":
                      agentCouncilStagedCopies.fiSweepOrphanedStagedCopies()}
    for dictJob in flistReadRunningJobRecords():
        if containerLock.fdictReadLockHolder(dictJob.get("sResourceName", "")):
            dictReport["listSpared"].append(dictJob["sJobId"])
            continue
        fileJobLock = agentCouncilCredentialStore.ffileTryAcquireTestLock(
            sDirectory, dictJob["sProvider"], dictJob["sImageIdentity"])
        if fileJobLock is None:
            dictReport["listSpared"].append(dictJob["sJobId"])
            continue
        try:
            _fnSettleOrphanedJob(dictJob, dockerCouncil)
        finally:
            fileJobLock.close()
        dictReport["listSwept"].append(dictJob["sJobId"])
    dictReport["listReconciled"] = _flistReconcileUnsettledJobs(dockerCouncil)
    return dictReport


def _flistRemoveJobLeftovers(dictJob, dockerCouncil):
    """Destroy the job's labeled runners and egress; return what stayed.

    With no daemon to ask, nothing can be proven gone, and saying so is
    what keeps the job on the retry list.
    """
    from . import agentCouncilDockerGateway
    if dockerCouncil is None:
        return ["runners and network (no Docker daemon answered)"]
    listUnsettled = []
    for dictSurvivor in agentCouncilDockerGateway.flistDiscoverLabeledRunners(
            dockerCouncil):
        if agentCouncilCredentialTest.fbRunnerLabelBelongsToJob(
                dictSurvivor["sReservationId"], dictJob["sJobId"]):
            dictDestroyed = (
                agentCouncilDockerGateway.fdictDestroyRunnerAndProveAbsence(
                    dockerCouncil, dictSurvivor["sContainerId"]))
            if dictDestroyed["sOutcome"] != (
                    agentCouncilRunner.S_OUTCOME_DESTROYED):
                listUnsettled.append("runner " + dictSurvivor["sContainerName"])
    listUnsettled.extend(agentCouncilCredentialTest.flistRemoveTestEgress(
        dictJob, agentCouncilDockerGateway.fdictCreateCouncilDockerGateway(
            dockerCouncil, {})))
    return listUnsettled


def _fnSettleOrphanedJob(dictJob, dockerCouncil):
    """Remove an orphaned job's leftovers and record it ``incomplete``."""
    listUnsettled = _flistRemoveJobLeftovers(dictJob, dockerCouncil)
    dictJob["listUnsettledResources"] = listUnsettled
    sDetail = "the hub running this test stopped before it finished"
    agentCouncilCredentialTest.fnPublishJobOutcome(
        dictJob, agentCouncilCredentialStore.S_OUTCOME_INCOMPLETE,
        dictJob.get("sCurrentCheck", ""),
        agentCouncilCredentialTest.fsDescribeUnsettled(listUnsettled, sDetail) if listUnsettled
        else sDetail)


def _flistReconcileUnsettledJobs(dockerCouncil):
    """Retry the clean-up of finished jobs left unproven; return their ids."""
    listReconciled = []
    if dockerCouncil is None:
        return listReconciled
    for dictJob in flistReadAllJobRecords():
        if dictJob.get("sStatus") == S_JOB_RUNNING or not dictJob.get(
                "listUnsettledResources"):
            continue
        dictJob["listUnsettledResources"] = _flistRemoveJobLeftovers(
            dictJob, dockerCouncil)
        fnWriteJobRecord(dictJob)
        if not dictJob["listUnsettledResources"]:
            listReconciled.append(dictJob["sJobId"])
    return listReconciled
