"""The Docker side of the memory watch: the sample, and a stopped container's evidence.

The sampler runs on its own lifespan loop rather than inside the
file-status poll, because the poll does not run in Blank Project mode
and pauses during runs -- exactly when memory is most likely to run
out. Every ``F_MEMORY_SAMPLE_INTERVAL_SECONDS`` it asks the daemon for
the state of each container a browser session owns, and runs one typed
read of a running container's memory cgroup, bounded by
``F_MEMORY_READ_TIMEOUT_SECONDS``. Host-mode projects have no container
and are skipped. What it learns is folded into the record that
``containerMemoryWatch`` keeps and describes; the constants live there
because the record's sentences quote them.

A stopped container's own evidence is read here too, just before the
container is removed by a start (``startReservation`` on the hub,
``commandStart`` in the CLI): removal destroys the only record of a
kill that took the main process down with it.
"""

import asyncio
import logging
from datetime import datetime, timezone

from vaibify.docker import cgroupMemory

from . import containerMemoryWatch

logger = logging.getLogger("vaibify")

__all__ = [
    "fdictReadExitedOomEvidence",
    "fnSampleContainerMemory",
    "fnSampleOwnedContainers",
    "fnRegisterMemorySampler",
]


def fdictReadExitedOomEvidence(sContainerName):
    """Return what Docker says about how a stopped container ended.

    Read just before the container is removed, because removal destroys
    the only record of a kill that took the container's main process
    down with it. A daemon that does not answer yields
    ``bAnswered=False``, which is logged; it never blocks the removal.
    """
    from vaibify.docker import containerManager
    try:
        dictInspect = containerManager.fjsonInspectContainer(sContainerName)
    except Exception:  # noqa: BLE001 -- evidence is best-effort
        dictInspect = {}
    if not dictInspect:
        logger.warning(
            "Docker did not describe the stopped container %s before its "
            "removal, so whether it was killed for lack of memory is "
            "unknown", sContainerName)
    dictState = dictInspect.get("State") or {}
    return {
        "bAnswered": bool(dictInspect),
        "sContainerId": str(dictInspect.get("Id", "") or ""),
        "bOomKilled": bool(dictState.get("OOMKilled")),
        "iExitCode": dictState.get("ExitCode"),
        "sFinishedIso": containerMemoryWatch.fsNormalizeDockerTime(
            dictState.get("FinishedAt")),
    }


def _fnRecordUnknown(dictStore, sName, sContainerId, sState, sReason=""):
    """Record a measurement that could not be taken, as of now."""
    containerMemoryWatch.fnRecordFailure(
        dictStore, sName, sContainerId, sState, sReason,
        datetime.now(timezone.utc))


async def fnSampleContainerMemory(
    dictStore, dictReadsInFlight, connectionDocker, sName, sContainerId,
):
    """Take one measurement of one container and fold it into the store."""
    if not sContainerId:
        _fnRecordUnknown(
            dictStore, sName, "", containerMemoryWatch.S_STATE_NOT_RUNNING)
        return
    try:
        dictState = await asyncio.to_thread(
            connectionDocker.fdictReadContainerState, sContainerId)
    except Exception as error:  # noqa: BLE001 -- reported, never raised
        _fnRecordUnknown(
            dictStore, sName, sContainerId,
            containerMemoryWatch.S_STATE_UNREADABLE,
            f"Docker did not describe the container ({type(error).__name__})")
        return
    if dictState is None:
        _fnRecordUnknown(
            dictStore, sName, sContainerId, containerMemoryWatch.S_STATE_GONE)
        return
    if not dictState.get("Running"):
        _fnRecordStoppedContainer(dictStore, sName, sContainerId, dictState)
        return
    await _fnMeasureRunningContainer(
        dictStore, dictReadsInFlight, connectionDocker, sName,
        sContainerId, dictState)


def _fnRecordStoppedContainer(dictStore, sName, sContainerId, dictState):
    """Record a stopped container, and any OOM kill Docker reports for it."""
    if dictState.get("OOMKilled"):
        containerMemoryWatch.fnRecordExitedOomKill(dictStore, sName, {
            "sContainerId": sContainerId,
            "sFinishedIso": containerMemoryWatch.fsNormalizeDockerTime(
                dictState.get("FinishedAt")),
        })
    _fnRecordUnknown(
        dictStore, sName, sContainerId,
        containerMemoryWatch.S_STATE_NOT_RUNNING)


async def _fnMeasureRunningContainer(
    dictStore, dictReadsInFlight, connectionDocker, sName, sContainerId,
    dictState,
):
    """Read the running container's cgroup, bounded, and record the result."""
    try:
        sReadText = await _fsReadWithDeadline(
            dictReadsInFlight, connectionDocker, sName, sContainerId)
    except Exception as error:  # noqa: BLE001 -- reported, never raised
        _fnRecordUnknown(
            dictStore, sName, sContainerId,
            containerMemoryWatch.S_STATE_UNREADABLE,
            f"its memory files could not be read ({type(error).__name__})")
        return
    if sReadText is None:
        _fnRecordUnknown(
            dictStore, sName, sContainerId,
            containerMemoryWatch.S_STATE_TIMEOUT)
        return
    containerMemoryWatch.fnRecordMeasurement(
        dictStore, sName, sContainerId,
        cgroupMemory.fdictParseCgroupMemory(sReadText), dictState,
        datetime.now(timezone.utc))


async def _fsReadWithDeadline(
    dictReadsInFlight, connectionDocker, sName, sContainerId,
):
    """Return the cgroup read's text, or None when it missed its deadline.

    The read runs on a worker thread and the wait is bounded by
    ``asyncio.wait_for``. A wait abandoned at the deadline does not stop
    the thread, which ends only at the Docker client's own socket
    deadline; that is safe here because the read writes nothing, which
    is why the carrier's rule against this pattern (it governs workers
    that WRITE) does not apply. What it must not do is pile threads up
    behind a hung daemon, so a container whose previous read is still
    out reports a timeout and launches no new one.
    """
    taskPrevious = dictReadsInFlight.get(sName)
    if taskPrevious is not None and not taskPrevious.done():
        return None
    taskRead = asyncio.ensure_future(asyncio.to_thread(
        connectionDocker.fsReadCgroupMemory, sContainerId))
    taskRead.add_done_callback(_fnConsumeAbandonedRead)
    dictReadsInFlight[sName] = taskRead
    try:
        return await asyncio.wait_for(
            asyncio.shield(taskRead),
            containerMemoryWatch.F_MEMORY_READ_TIMEOUT_SECONDS)
    except asyncio.TimeoutError:
        return None


def _fnConsumeAbandonedRead(taskRead):
    """Retrieve an abandoned read's outcome so asyncio does not warn of it."""
    if not taskRead.cancelled():
        taskRead.exception()


def _flistSampledContainers(app):
    """Return ``(sName, sContainerId)`` for every owned container to sample."""
    from vaibify.config.registryManager import fbIsHostProject
    dictOwners = dict(getattr(app.state, "dictContainerOwners", {}) or {})
    return [
        (sName, getattr(recordOwner, "sContainerId", "") or "")
        for sName, recordOwner in dictOwners.items()
        if not fbIsHostProject(sName)
    ]


async def fnSampleOwnedContainers(app, dictCtx, dictReadsInFlight):
    """Sample every owned container once; a failure stays with its container."""
    from vaibify.config.connectionAvailability import fbDockerReachable
    connectionDocker = (dictCtx or {}).get("docker")
    dictStore = app.state.dictContainerMemory
    bReachable = fbDockerReachable(connectionDocker)
    listSampled = _flistSampledContainers(app)
    _fnForgetFinishedReads(dictReadsInFlight, {t[0] for t in listSampled})
    for sName, sContainerId in listSampled:
        if not bReachable:
            _fnRecordUnknown(
                dictStore, sName, sContainerId,
                containerMemoryWatch.S_STATE_UNREADABLE,
                "Docker is not reachable")
            continue
        try:
            await fnSampleContainerMemory(
                dictStore, dictReadsInFlight, connectionDocker, sName,
                sContainerId)
        except Exception:  # noqa: BLE001 -- one container never stops the loop
            logger.warning(
                "Memory sample of container %s failed", sName, exc_info=True)


def _fnForgetFinishedReads(dictReadsInFlight, setSampledNames):
    """Drop finished reads of containers that are no longer owned."""
    for sName in list(dictReadsInFlight):
        if sName not in setSampledNames and dictReadsInFlight[sName].done():
            dictReadsInFlight.pop(sName, None)


async def _fnMemorySamplerLoop(app, dictCtx, fInterval):
    """Sample every owned container's memory forever on a fixed cadence.

    Sleeps before its first pass, like the other lifespan loops, so the
    thread-pool executor registered after them is in place first.
    """
    dictReadsInFlight = {}
    while True:
        try:
            await asyncio.sleep(fInterval)
            await fnSampleOwnedContainers(app, dictCtx, dictReadsInFlight)
        except asyncio.CancelledError:
            return
        except Exception:  # noqa: BLE001 -- the loop must outlive one pass
            logger.warning("Memory sampler iteration failed", exc_info=True)


def fnRegisterMemorySampler(app, dictCtx, fInterval=None):
    """Install the memory sampler on the app's lifespan."""
    from . import serverLifespan
    fIntervalEffective = (
        fInterval if fInterval is not None
        else containerMemoryWatch.F_MEMORY_SAMPLE_INTERVAL_SECONDS)

    async def fnStartSampler(app):
        app.state.taskMemorySampler = asyncio.create_task(
            _fnMemorySamplerLoop(app, dictCtx, fIntervalEffective),
            name="vaibify-memory-sampler",
        )

    async def fnStopSampler(app):
        await serverLifespan.fnCancelBackgroundTask(app, "taskMemorySampler")

    serverLifespan.fnRegisterLifespanTask(app, fnStartSampler, fnStopSampler)
