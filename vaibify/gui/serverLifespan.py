"""Lifespan task registration and background watchdog/sweep loops.

Drives every startup/shutdown hook through one lifespan context and
folds the periodic container sweep, the IO thread-pool install, and the
idle self-shutdown watchdog onto a single ``fnRegisterLifespanTask``
helper. The watchdog's self-exit decision is resolved through
``pipelineServer`` so a patched test double on that module is honoured.
"""

import asyncio
import logging
import os
import signal
import time
from contextlib import asynccontextmanager

from . import containerOwnership

logger = logging.getLogger("vaibify")

__all__ = [
    "fnRegisterLifespanTask",
    "fnIncrementWebSocketCount",
    "fnDecrementWebSocketCount",
]


@asynccontextmanager
async def _alifespanShared(app):
    """Single lifespan that drives every registered startup/shutdown hook.

    Modules append callables to ``app.state.listLifespanStartup`` and
    ``app.state.listLifespanShutdown`` between app construction and the
    first ASGI request. This replaces the deprecated
    ``@app.on_event("startup"/"shutdown")`` decorators (FastAPI emits
    a DeprecationWarning when those are used; mixing them with
    ``lifespan=`` is also unsupported).

    Each startup hook runs in its own ``try/except`` so a single
    failing hook cannot abort the lifespan before ``yield``; if it
    did, the shutdown loop would be skipped and resources already
    acquired by earlier hooks (e.g. background tasks, container
    locks) would leak. Shutdown hooks likewise run independently so
    one failure does not silence subsequent cleanup.
    """
    for fnStartup in list(getattr(app.state, "listLifespanStartup", [])):
        await _fnRunStartupHookSafely(fnStartup, app)
    yield
    for fnShutdown in list(getattr(app.state, "listLifespanShutdown", [])):
        await _fnRunShutdownHookSafely(fnShutdown, app)


async def _fnRunStartupHookSafely(fnHook, app):
    """Invoke a startup hook, logging any exception without re-raising."""
    try:
        await _fnInvokeMaybeAsync(fnHook, app)
    except Exception as errorAny:
        logger.warning(
            "Lifespan startup hook %s failed: %s",
            getattr(fnHook, "__name__", repr(fnHook)),
            type(errorAny).__name__,
        )


async def _fnRunShutdownHookSafely(fnHook, app):
    """Invoke a shutdown hook, logging any exception without re-raising."""
    try:
        await _fnInvokeMaybeAsync(fnHook, app)
    except Exception as errorAny:
        logger.warning(
            "Lifespan shutdown hook %s failed: %s",
            getattr(fnHook, "__name__", repr(fnHook)),
            type(errorAny).__name__,
        )


async def _fnInvokeMaybeAsync(fnHook, app):
    """Invoke a lifespan hook that may be sync or async."""
    objectResult = fnHook(app)
    if asyncio.iscoroutine(objectResult):
        await objectResult


def fnRegisterLifespanTask(app, fnStart, fnStop):
    """Append a start/stop hook pair to the app's lifespan task lists."""
    app.state.listLifespanStartup.append(fnStart)
    app.state.listLifespanShutdown.append(fnStop)


# Interval between periodic container-cache sweeps. The eviction work
# itself is cheap (one Docker list + a handful of dict pops); the cap
# determines worst-case latency between a container disappearing and
# its cached state being dropped, which bounds memory growth across
# multi-week host uptimes without measurably loading the event loop.
F_CONTAINER_SWEEP_INTERVAL_SECONDS = 60.0


def _fnRegisterPeriodicContainerSweep(app, dictCtx, fInterval=None):
    """Install a background asyncio task that evicts caches on a timer.

    Today ``fnSweepAllContainerCaches`` only fires on
    ``GET /api/registry``; a user who never reopens the picker leaves
    every per-container cache dormant. This loop calls the sweep every
    ``fInterval`` seconds so eviction tracks reality even on idle hubs.
    The task is registered on the lifespan so it is cleanly cancelled
    at shutdown.
    """
    fIntervalEffective = (
        fInterval if fInterval is not None
        else F_CONTAINER_SWEEP_INTERVAL_SECONDS
    )

    async def fnStartSweepTask(app):
        taskSweep = asyncio.create_task(
            _fnPeriodicContainerSweepLoop(dictCtx, fIntervalEffective),
            name="vaibify-container-sweep",
        )
        app.state.taskContainerSweep = taskSweep

    async def fnStopSweepTask(app):
        taskSweep = getattr(app.state, "taskContainerSweep", None)
        if taskSweep is None or taskSweep.done():
            return
        taskSweep.cancel()
        try:
            await taskSweep
        except (asyncio.CancelledError, Exception):
            pass

    fnRegisterLifespanTask(app, fnStartSweepTask, fnStopSweepTask)


async def _fnPeriodicContainerSweepLoop(dictCtx, fInterval):
    """Run ``fnSweepAllContainerCaches`` forever on a fixed cadence.

    Exits cleanly on ``CancelledError`` (lifespan shutdown). Any other
    exception is logged but the loop continues — a transient docker
    error must not silently terminate the sweep for the rest of the
    process lifetime.
    """
    while True:
        try:
            await asyncio.sleep(fInterval)
            await _fnRunOneContainerSweep(dictCtx)
        except asyncio.CancelledError:
            return
        except Exception:
            logger.warning(
                "Periodic container sweep iteration failed",
                exc_info=True,
            )


async def _fnRunOneContainerSweep(dictCtx):
    """Execute a single sweep tick against the current running set."""
    from .fileStatusManager import fnSweepAllContainerCaches
    connectionDocker = dictCtx.get("docker") if dictCtx else None
    if connectionDocker is None:
        return
    try:
        listContainers = await asyncio.to_thread(
            connectionDocker.flistGetRunningContainers,
        )
    except Exception:
        logger.warning(
            "Could not list running containers for sweep",
            exc_info=True,
        )
        return
    listIds = [
        dictRow.get("sContainerId", "") for dictRow in listContainers
    ]
    fnSweepAllContainerCaches(
        dictCtx, [sId for sId in listIds if sId],
    )


# Docker connection pool ceiling (see ``_fnTuneDockerSessionPool``) is
# 32 simultaneous in-flight HTTP requests against the daemon. Sizing
# the event loop's default ThreadPoolExecutor to at least 32 workers
# stops the pool from becoming the bottleneck for the many concurrent
# ``asyncio.to_thread`` callers (heartbeat, badge fan-out, state
# writer, file-status poll). The ``cpu_count() * 4`` upper bound
# keeps small hosts honest while still scaling with core count on a
# multi-core researcher workstation.
I_VAIBIFY_IO_THREAD_POOL_FLOOR = 32


def _fnRegisterDefaultThreadPoolExecutor(app):
    """Install a named ``vaibify-io`` ThreadPoolExecutor on startup.

    Python's default executor sizes to ``cpu_count() + 4`` workers,
    which under-provisions vaibify for the concurrent docker-exec
    workload the dashboard generates. The replacement is named so it
    shows up in ``py-spy``/``thread dump`` output, and is shut down
    on lifespan exit so the process exits cleanly.
    """

    async def fnInstallExecutor(app):
        from concurrent.futures import ThreadPoolExecutor
        iWorkers = max(
            I_VAIBIFY_IO_THREAD_POOL_FLOOR,
            (os.cpu_count() or 1) * 4,
        )
        executorIo = ThreadPoolExecutor(
            max_workers=iWorkers,
            thread_name_prefix="vaibify-io",
        )
        app.state.executorIoThreadPool = executorIo
        asyncio.get_running_loop().set_default_executor(executorIo)

    async def fnShutdownExecutor(app):
        executorIo = getattr(app.state, "executorIoThreadPool", None)
        if executorIo is None:
            return
        executorIo.shutdown(wait=False, cancel_futures=True)
        app.state.executorIoThreadPool = None

    fnRegisterLifespanTask(app, fnInstallExecutor, fnShutdownExecutor)


F_HUB_IDLE_TIMEOUT_SECONDS = 1800.0


F_HUB_WATCHDOG_INTERVAL_SECONDS = 60.0


S_HUB_IDLE_TIMEOUT_ENV = "VAIBIFY_HUB_IDLE_TIMEOUT_SECONDS"


def _fIdleTimeoutSeconds():
    """Return the idle timeout, honoring the env override when valid."""
    sOverride = os.environ.get(S_HUB_IDLE_TIMEOUT_ENV, "")
    if not sOverride:
        return F_HUB_IDLE_TIMEOUT_SECONDS
    try:
        return float(sOverride)
    except ValueError:
        return F_HUB_IDLE_TIMEOUT_SECONDS


def fnIncrementWebSocketCount(app):
    """Increment the live-WebSocket presence counter on the app state."""
    iCurrent = getattr(app.state, "iActiveWebSockets", 0)
    app.state.iActiveWebSockets = iCurrent + 1


def fnDecrementWebSocketCount(app):
    """Decrement the live-WebSocket presence counter, floored at zero."""
    iCurrent = getattr(app.state, "iActiveWebSockets", 0)
    app.state.iActiveWebSockets = max(0, iCurrent - 1)


def _flistHeldContainerIds(app, dictCtx):
    """Resolve owned container names to running container ids via Docker.

    Owned names are the keys of ``dictContainerOwners`` (the single
    owner-of-record map keyed by project/container name); the
    running-container list maps each name to its id. A Docker failure
    propagates so the caller can fail safe (treat as busy).
    """
    dictContainerOwners = getattr(app.state, "dictContainerOwners", {})
    setHeldNames = set(dictContainerOwners.keys())
    if not setHeldNames:
        return []
    connectionDocker = dictCtx.get("docker")
    listContainers = connectionDocker.flistGetRunningContainers()
    return [
        dictRow.get("sContainerId", "")
        for dictRow in listContainers
        if dictRow.get("sName", "") in setHeldNames
    ]


def _fbAnyContainerRunning(dictCtx, listContainerIds):
    """Return True if any container id reports a running pipeline."""
    from .fileStatusManager import _fbPipelineIsRunning
    for sContainerId in listContainerIds:
        if sContainerId and _fbPipelineIsRunning(dictCtx, sContainerId):
            return True
    return False


def _flistBusyCandidateIds(app, dictCtx):
    """Return the container ids whose run should veto idle self-exit.

    Both hub and viewer record their containers in the single
    ``dictContainerOwners`` map. A hub keys it by project name and
    resolves each name to a running container id via Docker; the
    single-container viewer keys it by the served container id directly,
    so a hub is distinguished by the presence of ``iHubPort`` on the app
    state. Reading the one owner map (never a deleted allow-set) is what
    keeps a hub mid-pipeline from self-SIGTERMing.
    """
    dictContainerOwners = getattr(app.state, "dictContainerOwners", {})
    if not dictContainerOwners:
        return []
    if getattr(app.state, "iHubPort", None) is not None:
        return _flistHeldContainerIds(app, dictCtx)
    return list(dictContainerOwners.keys())


def _fbAnyHeldContainerBusy(app, dictCtx):
    """Return True if any held or served container has a pipeline mid-run.

    Covers the hub (containers it locks) and the viewer (containers it
    serves). Fail-safe: any Docker error while listing or probing is
    treated as busy so the watchdog never retires a session whose
    container is only briefly unreachable.
    """
    try:
        listIds = _flistBusyCandidateIds(app, dictCtx)
        if not listIds:
            return False
        return _fbAnyContainerRunning(dictCtx, listIds)
    except Exception:
        return True


def _fbHubShouldSelfExit(app, dictCtx, fTimeout):
    """Return True only when no tab is connected, nothing is mid-run,
    and the HTTP-activity clock has been idle at least ``fTimeout``."""
    if getattr(app.state, "iActiveWebSockets", 0) > 0:
        return False
    if _fbAnyHeldContainerBusy(app, dictCtx):
        return False
    fLast = getattr(
        app.state, "fLastActivityMonotonic", time.monotonic(),
    )
    return (time.monotonic() - fLast) >= fTimeout


def _fnPruneSpawnedChildrenForApp(app):
    """Drop exited spawn children so the list can't grow between spawns."""
    listChildren = getattr(app.state, "listSpawnedChildren", None)
    if not listChildren:
        return
    from .routes.sessionRoutes import _fnPruneDeadChildren
    _fnPruneDeadChildren(listChildren)


def _fbOwnedNamePipelineRunning(app, dictCtx, sName):
    """Return True when an owned container's pipeline is mid-run.

    Used by the ownership reaper's busy veto so a claimed-but-disconnected
    owner is never released while its container is still running. A Docker
    failure fails safe to busy (``True``) so a transient outage never
    evicts an owner whose pipeline cannot be confirmed idle.
    """
    try:
        return _fbAnyContainerRunning(
            dictCtx, _flistRunningIdsForName(dictCtx, sName),
        )
    except Exception:
        return True


def _flistRunningIdsForName(dictCtx, sName):
    """Return the running container ids matching a single owned name."""
    connectionDocker = dictCtx.get("docker")
    listContainers = connectionDocker.flistGetRunningContainers()
    return [
        dictRow.get("sContainerId", "")
        for dictRow in listContainers
        if dictRow.get("sName", "") == sName
    ]


def _fnReapIdleOwnershipsForApp(app, dictCtx):
    """Release every idle, past-grace owner record that holds no live run.

    Only hubs enable this (``bReapOwnerships``); the single-container
    viewer's served record carries no host flock and dies with the
    process, so it is never force-released here.
    """
    if not getattr(app.state, "bReapOwnerships", False):
        return
    dictContainerOwners = getattr(app.state, "dictContainerOwners", {})
    containerOwnership.flistReapIdleOwnerships(
        dictContainerOwners,
        lambda sName: _fbOwnedNamePipelineRunning(app, dictCtx, sName),
    )


async def _fnIdleShutdownWatchdogLoop(app, dictCtx, fInterval, fTimeout):
    """Self-SIGTERM once the hub is idle past ``fTimeout``; else keep polling.

    SIGTERM (not a direct teardown) lets uvicorn run the existing
    graceful-shutdown hooks that release container locks and the
    session slot. Exits cleanly on ``CancelledError`` at shutdown.
    """
    while True:
        try:
            await asyncio.sleep(fInterval)
            _fnPruneSpawnedChildrenForApp(app)
            _fnReapIdleOwnershipsForApp(app, dictCtx)
            from . import pipelineServer
            if pipelineServer._fbHubShouldSelfExit(
                app, dictCtx, fTimeout,
            ):
                os.kill(os.getpid(), signal.SIGTERM)
                return
        except asyncio.CancelledError:
            return
        except Exception:
            logger.warning(
                "Idle-shutdown watchdog iteration failed", exc_info=True,
            )


def _fnRegisterIdleShutdownWatchdog(app, dictCtx, fInterval=None):
    """Install the idle self-shutdown watchdog on the lifespan.

    Mirrors ``_fnRegisterPeriodicContainerSweep``: starts an asyncio
    task at startup and cancels it cleanly at shutdown. The timeout is
    read once at registration so the env override is honored.
    """
    fIntervalEffective = (
        fInterval if fInterval is not None
        else F_HUB_WATCHDOG_INTERVAL_SECONDS
    )
    fTimeout = _fIdleTimeoutSeconds()

    async def fnStartWatchdog(app):
        app.state.fLastActivityMonotonic = time.monotonic()
        app.state.taskIdleWatchdog = asyncio.create_task(
            _fnIdleShutdownWatchdogLoop(
                app, dictCtx, fIntervalEffective, fTimeout,
            ),
            name="vaibify-idle-watchdog",
        )

    async def fnStopWatchdog(app):
        taskWatchdog = getattr(app.state, "taskIdleWatchdog", None)
        if taskWatchdog is None or taskWatchdog.done():
            return
        taskWatchdog.cancel()
        try:
            await taskWatchdog
        except (asyncio.CancelledError, Exception):
            pass

    fnRegisterLifespanTask(app, fnStartWatchdog, fnStopWatchdog)
