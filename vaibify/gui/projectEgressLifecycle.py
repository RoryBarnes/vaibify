"""The hub's care of allowlist-mode egress: reconcile at start, sweep on the reaper's cadence.

A project's proxy sidecar and sealed network are created by the launch
and removed by the stop. A hub that died between the two leaves them
behind; a container removed by hand leaves them behind. At start, and
on the remnant reaper's cadence, the hub asks the daemon which project
identities still have a container and releases the egress of every
identity that does not. The council's own sweep never touches these:
they carry the project label, not the council's.
"""

__all__ = [
    "fdictReconcileProjectEgress",
    "fdictReapOrphanedProjectEgress",
    "fnRegisterProjectEgressLifecycle",
]

import asyncio
import logging

from vaibify.config import registryManager
from vaibify.docker import containerManager, projectEgress

from . import remnantReapers, serverLifespan

logger = logging.getLogger(__name__)


def _fdictContainerNamesByUuid():
    """Return ``{sProjectUuid: sContainerName}`` for every registered project."""
    dictNames = {}
    for dictProject in registryManager.flistGetAllProjects():
        sUuid = dictProject.get("sProjectUuid") or ""
        if sUuid:
            dictNames[sUuid] = dictProject.get("sContainerName") or dictProject["sName"]
    return dictNames


def fdictReconcileProjectEgress():
    """Release the egress of every identity without a container; return outcomes.

    An identity the registry no longer knows has no container. One it
    knows keeps its egress unless the daemon ANSWERED that the
    container is absent; an unanswered daemon releases nothing.
    """
    dictNames = _fdictContainerNamesByUuid()

    def fbContainerExists(sProjectUuid):
        sName = dictNames.get(sProjectUuid)
        if not sName:
            return False
        dictPresence = containerManager.fdictProbeContainerPresence(sName)
        return not (dictPresence["bAnswered"] and not dictPresence["bPresent"])

    return projectEgress.fdictReconcileProjectEgress(fbContainerExists)


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
