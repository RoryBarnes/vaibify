"""The hub's care of sealed-mode egress: reconcile at start, sweep on the reaper's cadence.

A project's proxy sidecar and sealed network are created by the launch
and removed by the stop. A hub that died between the two leaves them
behind; a container removed by hand leaves them behind. At start, and
on the remnant reaper's cadence, the hub asks the DAEMON whether any
container is still attached to each identity's network, and releases
the egress of every identity nothing uses. The project registry is
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
]

import asyncio
import logging

from vaibify.docker import projectEgress

from . import remnantReapers, serverLifespan

logger = logging.getLogger(__name__)


def fdictReconcileProjectEgress():
    """Release the egress of every identity no container uses; return outcomes."""
    dictOutcomes = projectEgress.fdictReconcileProjectEgress()
    listReleased = sorted(
        sUuid for sUuid, sOutcome in dictOutcomes.items() if sOutcome == "released")
    if listReleased:
        logger.info("project egress reconcile released %s", ", ".join(listReleased))
    return dictOutcomes


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
