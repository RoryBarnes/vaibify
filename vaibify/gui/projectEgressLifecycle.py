"""The hub's care of sealed-mode egress: reconcile at start, sweep on the reaper's cadence.

A project's proxy sidecar and sealed network are created by the launch
and removed by the stop. A hub that died between the two leaves them
behind; a container removed by hand leaves them behind. At start, and
on the remnant reaper's cadence, the hub asks the DAEMON whether any
container is still attached to each identity's network, releases the
egress of every identity nothing uses, and restores the proxy of a
running container that has lost one, replaying how it was launched. The project registry is
deliberately not consulted: a test hub with a throwaway registry once
released the proxy of a researcher's running container, because
"absent from my registry" was read as "absent from the daemon". The
council's own sweep never touches these: they carry the project label,
not the council's.
"""

__all__ = [
    "fdictReconcileProjectEgress",
    "fdictReapOrphanedProjectEgress",
    "fnRegisterProjectEgressLifecycle",
    "fsRestoreProxyIfMissing",
]

import asyncio
import logging

from vaibify.config.containerLock import (
    LifecycleLockedError, fcontextHoldLifecycleLock,
)
from vaibify.docker import projectEgress

from . import remnantReapers, serverLifespan

logger = logging.getLogger(__name__)


def fdictReconcileProjectEgress():
    """Release unused egress, restore a running container's missing proxy; return outcomes."""
    dictOutcomes = projectEgress.fdictReconcileProjectEgress()
    listReleased = sorted(
        sUuid for sUuid, sOutcome in dictOutcomes.items() if sOutcome == "released")
    if listReleased:
        logger.info("project egress reconcile released %s", ", ".join(listReleased))
    for sProjectUuid, sOutcome in dictOutcomes.items():
        if sOutcome == "kept":
            dictOutcomes[sProjectUuid] = fsRestoreProxyIfMissing(sProjectUuid)
    return dictOutcomes


def fsRestoreProxyIfMissing(sProjectUuid):
    """Restore the proxy of a RUNNING sealed container that has lost it; return the outcome.

    A missing proxy leaves the container with no network at all and no
    error inside it, only timeouts. The restore runs under the
    container's lifecycle lock, the one start, stop and delete take, so
    it can never race them or another hub; a held lock skips this pass.
    The state is re-read under the lock, because the stop that held it
    may have removed the very container being restored.
    """
    if projectEgress.fsProxyState(sProjectUuid) == projectEgress.S_PROXY_STATE_RUNNING:
        return "kept"
    listRunning = projectEgress.flistContainersUsingEgress(sProjectUuid)
    if not listRunning:
        return "kept"
    sContainerName = listRunning[0]
    logger.warning("network proxy of %s is %s while the container runs",
                   sContainerName, projectEgress.fsProxyState(sProjectUuid))
    try:
        with fcontextHoldLifecycleLock(sContainerName, "reconnected to its network"):
            return _fsRestoreUnderTheLock(sProjectUuid, sContainerName)
    except LifecycleLockedError:
        logger.info("network proxy restore of %s deferred: its lifecycle "
                    "lock is held", sContainerName)
        return "deferred"


def _fsRestoreUnderTheLock(sProjectUuid, sContainerName):
    if projectEgress.fsProxyState(sProjectUuid) == projectEgress.S_PROXY_STATE_RUNNING:
        return "kept"
    if sContainerName not in projectEgress.flistContainersUsingEgress(sProjectUuid):
        return "kept"
    try:
        projectEgress.fsRestoreProjectProxy(sProjectUuid)
    except projectEgress.EgressSetupError as error:
        logger.warning("network proxy of %s could not be restored: %s",
                       sContainerName, error)
        return "restore-refused"
    return "restored"


def fdictReapOrphanedProjectEgress(dictCtx):
    """The reaper's entry: release orphaned egress, reporting what went."""
    del dictCtx
    try:
        dictOutcomes = fdictReconcileProjectEgress()
    except projectEgress.EgressSetupError as error:
        return remnantReapers.fdictBuildReaperOutcome(
            remnantReapers.S_OUTCOME_FORBIDDEN, sReason=str(error),
            sRemedy="Start Docker, then rescan.",
        )
    return remnantReapers.fdictBuildReaperOutcome(
        remnantReapers.S_OUTCOME_RAN,
        iRemoved=sum(1 for sOutcome in dictOutcomes.values() if sOutcome == "released"),
    )


def fnRegisterProjectEgressLifecycle(app):
    """Reconcile at the hub's start and on the remnant reaper's cadence."""
    async def fnReconcileAtStartup(app):
        del app
        try:
            await asyncio.to_thread(fdictReconcileProjectEgress)
        except Exception:  # noqa: BLE001 -- a daemon that is down defers the sweep to the reaper
            logger.warning("project egress reconcile failed at startup", exc_info=True)

    async def fnNothingToStop(app):
        del app

    serverLifespan.fnRegisterLifespanTask(app, fnReconcileAtStartup, fnNothingToStop)
    remnantReapers.fnRegisterReaper(
        app, "projectEgressProxies", fdictReapOrphanedProjectEgress,
    )
