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

The hub that connects to a sealed container also points its proxy at
itself (:func:`fnPinProjectEgressToHub`) before the agent is told which
hub to dial, so a container launched by one hub and taken over by
another can still reach the hub that now owns it.
"""

__all__ = [
    "fdictDescribeProjectEgress",
    "fdictReconcileProjectEgress",
    "fdictReapOrphanedProjectEgress",
    "fnPinProjectEgressToHub",
    "fnRegisterProjectEgressLifecycle",
    "fsDescribeHubPinMismatch",
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


def fnPinProjectEgressToHub(connectionDocker, sContainerId, iHubPort):
    """Point the container's network proxy at hub ``iHubPort``, if it has one.

    A container with no registered identity, or a project with no
    proxy, has nothing to pin. A failure is logged and never raised:
    the agent's session file is still written, and the dashboard's
    network banner reports the port the proxy actually serves.
    """
    from vaibify.config.registryManager import fdictGetProject
    from .pipelineServer import fsContainerNameForId
    sContainerName = fsContainerNameForId(connectionDocker, sContainerId)
    sProjectUuid = (fdictGetProject(sContainerName) or {}).get("sProjectUuid") or ""
    if not sProjectUuid:
        return
    try:
        sPin = projectEgress.fsPinProjectEgressHub(sProjectUuid, iHubPort)
    except (projectEgress.EgressSetupError, OSError):
        logger.warning("could not point the network proxy of %s at hub port %d",
                       sContainerName, iHubPort, exc_info=True)
        return
    if sPin == projectEgress.S_PIN_FIXED:
        logger.warning(
            "the network proxy of %s was started by an older vaibify and "
            "cannot be pointed at hub port %d; restart the container",
            sContainerName, iHubPort)


def fdictDescribeProjectEgress(sProjectUuid, iHubPort):
    """Return the network banner's proxy facts, with ``sHubPinMismatch``.

    ``iHubPort`` is the port of the hub asking; the agent is told the
    port :func:`agentSessionBridge.fiAgentHubPort` makes of it, and a
    proxy serving any other refuses every call the agent makes.
    """
    from .agentSessionBridge import fiAgentHubPort
    dictEgress = projectEgress.fdictDescribeProjectEgress(sProjectUuid)
    dictEgress["sHubPinMismatch"] = fsDescribeHubPinMismatch(
        dictEgress, fiAgentHubPort(iHubPort))
    return dictEgress


def fsDescribeHubPinMismatch(dictEgress, iAgentHubPort):
    """Return the sentence for a proxy serving another hub, or "".

    A proxy that reads its port from a file is fixed by reopening the
    container from this dashboard, which re-pins it; an older one only
    by a restart, which makes a new proxy.
    """
    sPin = dictEgress.get("sPin")
    iPinnedHubPort = dictEgress.get("iPinnedHubPort", 0)
    if sPin not in (projectEgress.S_PIN_PINNED, projectEgress.S_PIN_FIXED):
        return ""
    if iPinnedHubPort == iAgentHubPort:
        return ""
    sServes = (f"serves hub port {iPinnedHubPort}" if iPinnedHubPort
               else "serves no hub")
    sSentence = (f"This container's network proxy {sServes}; this dashboard "
                 f"is {iAgentHubPort}.")
    if sPin == projectEgress.S_PIN_FIXED:
        return sSentence + " Restart the container to reach it from here."
    return sSentence + " Reopen the container from this dashboard to point the proxy here."


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
