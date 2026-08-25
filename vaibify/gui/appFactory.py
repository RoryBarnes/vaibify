"""FastAPI application factory for the viewer and hub server modes.

Builds one configured application from a config dict, expressing the
hub-versus-viewer differences as flags and lifecycle hooks rather than
two near-duplicate factories. The connection probe, route registration,
context builder, and exception handler are reached through
``pipelineServer`` so patched test doubles are honoured and
route-registration semantics stay untouched.
"""

import asyncio
import logging
import secrets
import time

from fastapi import FastAPI

from . import agentCouncilController
from . import agentCouncilRegistry
from . import agentCouncilStore
from . import browserSession
from . import commitCarrier
from . import containerOwnership
from . import serverLifespan
from . import serverMiddleware
from . import sessionLifecycle
from . import startResultStore
from . import terminalContainment

logger = logging.getLogger("vaibify")

__all__ = [
    "fappCreateApplication",
    "fappCreateHubApplication",
]


def _fnInitialiseApplicationState(app, dictConfig, sSessionToken):
    """Seed the shared app.state fields used by routes and middleware."""
    app.state.listLifespanStartup = []
    app.state.listLifespanShutdown = []
    app.state.sSessionToken = sSessionToken
    app.state.sTerminalUser = dictConfig["sTerminalUser"]
    app.state.dictContainerOwners = (
        containerOwnership.fdictCreateOwnerRegistry()
    )
    app.state.dictBrowserSessions = (
        browserSession.fdictCreateBrowserSessionStore()
    )
    app.state.dictSessionOwner = (
        containerOwnership.fdictCreateSessionOwnerIndex()
    )
    app.state.dictSessionSockets = (
        containerOwnership.fdictCreateSessionSocketIndex()
    )
    app.state.dictLifecycleLocks = (
        sessionLifecycle.fdictCreateLifecycleLockStore()
    )
    app.state.dictMutationSupervisors = (
        commitCarrier.fdictCreateMutationSupervisorRegistry()
    )
    app.state.dictDurableTaskRecords = (
        commitCarrier.fdictCreateDurableTaskRegistry()
    )
    app.state.dictTerminalExecutionRecords = (
        terminalContainment.fdictCreateTerminalRecordRegistry()
    )
    app.state.dictStartResults = (
        startResultStore.fdictCreateStartResultStore()
    )
    # The council's two app-owned authorities (design sections 9.3, 7.3):
    # the registry accounts for live runner/API work and vetoes idle
    # self-exit; the campaign store holds campaigns and checkpoints their
    # durable record to host app-data. Both are plain dicts driven by
    # module functions, so ``app.state`` owns one value each.
    setattr(
        app.state, agentCouncilRegistry.S_COUNCIL_REGISTRY_STATE_KEY,
        agentCouncilRegistry.fdictCreateCouncilRegistry(),
    )
    setattr(
        app.state, agentCouncilStore.S_COUNCIL_CAMPAIGN_STORE_STATE_KEY,
        agentCouncilStore.fdictCreateCampaignStore(),
    )
    # The controller is the sole writer of campaign state (remediation
    # R1): routes submit bounded commands onto its per-campaign
    # serialization primitive rather than mutating the store themselves.
    setattr(
        app.state, agentCouncilController.S_COUNCIL_CONTROLLER_STATE_KEY,
        agentCouncilController.fdictCreateCouncilControllerState(),
    )
    app.state.bMutationAdmissionsClosed = False
    app.state.iExpectedPort = dictConfig["iExpectedPort"]
    app.state.iActiveWebSockets = 0
    app.state.fLastActivityMonotonic = time.monotonic()
    if dictConfig["bIsHub"]:
        app.state.iHubPort = dictConfig["iExpectedPort"]
        app.state.bReapOwnerships = dictConfig["bReapOwnerships"]


def _fdictBuildApplicationContext(app, dictConfig, sSessionToken):
    """Build the route context and wire shared identifiers onto it."""
    from . import pipelineServer
    dictCtx = pipelineServer.fdictBuildContext(
        pipelineServer.fconnectionBuildRouted(),
    )
    dictCtx["sSessionToken"] = sSessionToken
    dictCtx["sTerminalUser"] = dictConfig["sTerminalUser"]
    dictCtx["iPort"] = dictConfig["iExpectedPort"]
    dictCtx["dictContainerOwners"] = app.state.dictContainerOwners
    dictCtx["dictBrowserSessions"] = app.state.dictBrowserSessions
    dictCtx["dictSessionOwner"] = app.state.dictSessionOwner
    dictCtx["dictSessionSockets"] = app.state.dictSessionSockets
    if dictConfig["bIsHub"]:
        dictCtx["bIsHub"] = True
    # Published on app.state so ``routeScope.ContainerAwareRoute`` can
    # reach the workflow cache. It runs before every container-scoped
    # handler and is the one place that already classifies a request as
    # mutating, which is what the Supervised-on-host refusal needs; the
    # alternative was a second classifier in middleware that would drift
    # from this one.
    app.state.dictRouteContext = dictCtx
    return dictCtx


def _fnRegisterHubLifecycle(app, dictCtx, dictConfig):
    """Register hub-only registry routes and lock/keepalive lifecycle.

    Keep-alive stop is registered BEFORE the lock lifecycle so the
    shutdown hooks run in that order: caffeinate is stopped for every
    still-owned container before the lock-release hook clears the owner
    records (otherwise the keep-alive hook would iterate an empty dict).
    """
    if not dictConfig["bIsHub"]:
        return
    from .registryRoutes import fnRegisterRegistryRoutes
    from .hostControlChannel import fnRegisterHostControlChannel
    fnRegisterRegistryRoutes(app, dictCtx)
    fnRegisterHostControlChannel(app, dictCtx)
    _fnRegisterHubShutdownStopKeepAlive(app)
    _fnRegisterHubLockLifecycle(app)


def _fnRegisterBackgroundTasks(app, dictCtx):
    """Install the sweep, watchdog, evaluator, and threadpool tasks.

    The thread-pool executor is registered LAST so its shutdown hook is
    appended after the sweep and idle-watchdog stop hooks. Shutdown hooks
    run in append order, so the executor is torn down only after the two
    loops that submit to it via ``asyncio.to_thread`` have been
    cancelled, closing the ``cannot schedule new futures after shutdown``
    window. Each loop sleeps before its first submission, so installing
    the executor after the loop tasks are created is still race-free at
    startup.
    """
    serverLifespan._fnRegisterPeriodicContainerSweep(app, dictCtx)
    serverLifespan._fnRegisterIdleShutdownWatchdog(app, dictCtx)
    serverLifespan._fnRegisterSessionLifecycleEvaluator(app)
    serverLifespan._fnRegisterDefaultThreadPoolExecutor(app)


def _fappBuildApplication(dictConfig):
    """Build a viewer- or hub-mode FastAPI app from a config dict.

    The terminal user is threaded onto ``app.state`` and the route
    context rather than a ``pipelineServer`` module global, so a hub and
    a viewer built in the same process keep independent terminal-user
    resolution instead of the last build winning for both.
    """
    from . import pipelineServer
    from . import routeScope
    app = FastAPI(
        title=dictConfig["sTitle"],
        lifespan=serverLifespan._fcontextLifespanShared,
    )
    # Install the container-owner route class BEFORE any route registers:
    # route_class only governs routes added after the assignment, so an
    # ordering slip would silently leave routes unauthorized.
    app.router.route_class = routeScope.ContainerAwareRoute
    sSessionToken = secrets.token_urlsafe(32)
    _fnInitialiseApplicationState(app, dictConfig, sSessionToken)
    serverMiddleware.fnRegisterMiddleware(app)
    pipelineServer._fnRegisterLastResortExceptionHandler(app)
    dictCtx = _fdictBuildApplicationContext(app, dictConfig, sSessionToken)
    pipelineServer._fnRegisterAllRoutes(
        app, dictCtx, dictConfig["sWorkspaceRoot"],
    )
    # The mutation drain MUST be appended before every hub shutdown
    # hook: shutdown hooks run in append order, and the flock-release
    # hook may only run after the guarded workers have been drained
    # (design §8 — shutdown ordering is a correctness boundary).
    _fnRegisterShutdownDrainGuardedMutations(app)
    # The council drain is registered AFTER the guarded-mutation drain
    # and BEFORE the hub lifecycle so it runs before keep-alive shutdown
    # and flock release (design section 21 — shutdown ordering). Its
    # startup twin discovers and reconciles labeled runners a crash left
    # behind, and reloads accepted campaigns from durable app-data,
    # before any new council starts.
    _fnRegisterCouncilLifecycle(app)
    _fnRegisterHubLifecycle(app, dictCtx, dictConfig)
    _fnRegisterBackgroundTasks(app, dictCtx)
    routeScope.fnValidateRouteScopesOrRaise(app)
    return app


def fappCreateApplication(
    sWorkspaceRoot="/workspace", sTerminalUserArg=None,
    iExpectedPort=0,
):
    """Build and return the configured viewer FastAPI application.

    When ``iExpectedPort`` is non-zero, the SessionTokenMiddleware
    enforces a strict ``Host:`` header check (DNS rebinding defense).
    ``0`` is the deliberate opt-out for the in-process test harness,
    whose TestClient sends ``Host: testserver``; every production
    launcher passes its real bind port, and
    ``testProductionEntryPointsBindHostCheck`` fails CI if one stops
    doing so. Omitting the state entirely fails closed in the
    middleware — the sentinel must be chosen, never inherited by
    accident.
    """
    dictConfig = {
        "sTitle": "Vaibify Workflow Viewer",
        "sWorkspaceRoot": sWorkspaceRoot,
        "sTerminalUser": sTerminalUserArg,
        "iExpectedPort": iExpectedPort,
        "bIsHub": False,
        "bReapOwnerships": False,
    }
    return _fappBuildApplication(dictConfig)


def fappCreateHubApplication(iExpectedPort=0):
    """Build a hub-mode FastAPI app with registry support.

    See :func:`fappCreateApplication` for ``iExpectedPort`` semantics.
    """
    from . import pipelineServer
    dictConfig = {
        "sTitle": "Vaibify Hub",
        "sWorkspaceRoot": pipelineServer.WORKSPACE_ROOT,
        "sTerminalUser": "researcher",
        "iExpectedPort": iExpectedPort,
        "bIsHub": True,
        "bReapOwnerships": True,
    }
    return _fappBuildApplication(dictConfig)


def _fnRegisterHubLockLifecycle(app):
    """Reap stale claims at startup; release held locks at shutdown."""
    _fnRegisterHubStartupReapStaleClaims(app)
    _fnRegisterHubStartupSweepHostScratch(app)
    _fnRegisterHubShutdownReleaseLocks(app)


def _fnRegisterHubStartupSweepHostScratch(app):
    """Retire unreachable host-mode scratch before the hub serves.

    Registered HERE and not beside the ephemeral-secret sweep, which
    is gated on enumerating what the Docker daemon still bind-mounts
    and skips itself entirely when that enumeration fails. A host-only
    hub has no daemon to ask, and nothing in this subtree is mounted
    into anything, so tying the two together would mean the scratch of
    a daemon-less machine is never swept at all.
    """

    async def fnSweepHostScratch(app):
        del app
        from vaibify.host.hostScratch import fnSweepStaleHostScratch
        fnSweepStaleHostScratch()
    app.state.listLifespanStartup.append(fnSweepHostScratch)


def _fnRegisterHubStartupReapStaleClaims(app):
    """Reap dead-PID container locks before the hub serves requests."""

    async def fnReapStaleClaims(app):
        del app
        from vaibify.config.containerLock import (
            fnReapStaleContainerLocks,
        )
        fnReapStaleContainerLocks()
    app.state.listLifespanStartup.append(fnReapStaleClaims)


def _fnRegisterShutdownDrainGuardedMutations(app):
    """Stop admitting guarded mutations, then bounded-drain the workers.

    Appended BEFORE the keep-alive and flock-release hooks so shutdown
    runs in the design §8 order: close admissions → drain the mutation
    supervisors → only then stop keep-alive and release flocks. The
    executor hook is appended last of all (``_fnRegisterBackgroundTasks``
    runs after this), so by the time it shuts the pool down every
    drained worker has finished; a worker that outlives the bounded
    drain keeps running on its thread with its flock retained — it is
    never torn down while it can still commit.

    Live terminal executions are drained here like live mutation work
    (design §7/§8, case 44): each recorded process group is terminated
    and proven empty, or its journal record retains-and-quarantines,
    BEFORE the flock-release hook runs — a terminal group that may
    still write must never have its container handed to another hub.
    """

    async def fnDrainGuardedMutations(app):
        await commitCarrier.fdictDrainMutationSupervisors(app.state)
        await asyncio.to_thread(
            terminalContainment.fdictDrainAllTerminalRecords, app.state,
        )

    app.state.listLifespanShutdown.append(fnDrainGuardedMutations)


def _fdockerCreateCouncilClientOrNone():
    """Return a council Docker client, or None when no daemon is reachable.

    The council is container-only, but the hub it runs in may be a host-
    only machine with no Docker daemon at all. Reconcile and drain then
    have no runners to act on, so a failed client construction is a
    graceful skip rather than a startup or shutdown error.
    """
    try:
        from . import agentCouncilDockerGateway
        return agentCouncilDockerGateway.fdockerCreateCouncilClient()
    except Exception:
        return None


def _fnRegisterCouncilLifecycle(app):
    """Reconcile crashed-runner survivors and reload campaigns; drain on stop.

    Startup (design section 9.4, 7.5): reload accepted campaigns from
    durable app-data so a restarted hub shows them again, then discover
    and settle any labeled runner a crash left behind before a new
    council starts. Shutdown (design section 9.3): stop admitting new
    turns and destroy or visibly quarantine every live runner. Both are
    best-effort against the daemon — a host-only hub has none.
    """

    async def fnReconcileCouncilOnStartup(app):
        dictStore = getattr(
            app.state,
            agentCouncilStore.S_COUNCIL_CAMPAIGN_STORE_STATE_KEY, None)
        if isinstance(dictStore, dict):
            await asyncio.to_thread(
                agentCouncilStore.fdictReloadDurableCampaigns, dictStore)
            # A reloaded campaign still in ``planning`` had a turn with
            # no terminal record at the crash; it is classified as
            # interrupted, never resumed (remediation R1) — the labeled
            # runner reconcile below settles whatever that turn left.
            await asyncio.to_thread(
                agentCouncilController
                .fiClassifyInterruptedCampaignsOnStartup, dictStore)
        await asyncio.to_thread(_fnReconcileCouncilRunners, app)

    async def fnDrainCouncilOnShutdown(app):
        # Live drive tasks stop BEFORE the registry drain destroys
        # runners: a drive still launching turns would race the drain's
        # closed admission and report a spurious refusal instead of a
        # clean stop.
        dictControllerState = getattr(
            app.state,
            agentCouncilController.S_COUNCIL_CONTROLLER_STATE_KEY, None)
        if isinstance(dictControllerState, dict):
            agentCouncilController.fnDrainControllerOnShutdown(
                dictControllerState)
            # A bounded settle, never a proof: drives that miss the
            # deadline are handed to the registry drain below, and the
            # provisioned runner access (egress boundary, staged host
            # credential) is released for every campaign either way.
            await agentCouncilController.fnAwaitControllerSettleOnShutdown(
                dictControllerState)
        await asyncio.to_thread(_fnDrainCouncilRunners, app)

    app.state.listLifespanStartup.append(fnReconcileCouncilOnStartup)
    app.state.listLifespanShutdown.append(fnDrainCouncilOnShutdown)


def _fnReconcileCouncilRunners(app):
    """Discover and settle council runners and egress left by a crash."""
    dictRegistry = getattr(
        app.state, agentCouncilRegistry.S_COUNCIL_REGISTRY_STATE_KEY, None)
    dockerCouncil = _fdockerCreateCouncilClientOrNone()
    if not isinstance(dictRegistry, dict) or dockerCouncil is None:
        return
    agentCouncilRegistry.fdictReconcileLabeledRunnersOnRestart(
        dictRegistry, dockerCouncil)
    # The egress backstop: no council drive of OURS survives a restart,
    # so every stored campaign's proxy and network is a leftover —
    # including one an earlier hub's teardown answered INDETERMINATE
    # for and could only log. (A proxy whose campaign record is gone
    # is caught by the labeled reconcile above instead — proxies wear
    # the council label for exactly that.) Indeterminate again here
    # stays in the log and the next start sweeps again.
    dictStore = getattr(
        app.state, agentCouncilStore.S_COUNCIL_CAMPAIGN_STORE_STATE_KEY,
        None)
    if not isinstance(dictStore, dict):
        return
    from vaibify.gui import agentCouncilDockerGateway
    dictSwept = agentCouncilDockerGateway.fdictSweepCouncilEgressLeftovers(
        dockerCouncil, _flistSelectSweepableCampaigns(dictStore))
    if dictSwept["listIndeterminateResources"]:
        logger.warning(
            "startup council egress sweep could not settle: %s",
            dictSwept["listIndeterminateResources"])


def _flistSelectSweepableCampaigns(dictStore):
    """Return the stored campaign ids this hub may sweep egress for.

    The durable store is machine-wide, so a booting hub reloads a LIVE
    PEER's campaigns beside its own crash leftovers. Removing a peer's
    proxy and internal network cuts the egress its running turns are
    using — the same daemon-wide over-reach the labeled runner reconcile
    already learned to avoid, on the resources beside the runners.

    Filtered at the caller so the gateway stays free of lock knowledge
    and adds no new untraceable SDK roots to the blind-spot budget.
    """
    listSweepable = []
    for sCampaignId in list(dictStore["listInsertionOrder"]):
        dictCampaign = agentCouncilStore.fjsonGetCampaignRecord(
            dictStore, sCampaignId)
        if dictCampaign is not None and (
                agentCouncilRegistry.fbCampaignBelongsToALivePeerHub(
                    dictCampaign)):
            continue
        listSweepable.append(sCampaignId)
    return listSweepable


def _fnDrainCouncilRunners(app):
    """Close council admission and destroy or quarantine live runners."""
    dictRegistry = getattr(
        app.state, agentCouncilRegistry.S_COUNCIL_REGISTRY_STATE_KEY, None)
    if not isinstance(dictRegistry, dict):
        return
    agentCouncilRegistry.fdictDrainCouncilRegistry(
        dictRegistry, _fdockerCreateCouncilClientOrNone())


def _fnRegisterHubShutdownReleaseLocks(app):
    """Release held container locks at shutdown — except live workers'.

    A container whose guarded worker survived the bounded shutdown
    drain keeps its owner record AND its flock (design §8, case 26): an
    OS flock frees the instant this process exits anyway, but while the
    process lives no other hub may be handed a container a still-
    writing worker can commit to.
    """

    async def fnReleaseAllContainerLocks(app):
        from vaibify.config.containerLock import fnReleaseContainerLock
        dictContainerOwners = getattr(app.state, "dictContainerOwners", {})
        dictSessionOwner = getattr(app.state, "dictSessionOwner", None)
        setRetainedNames = commitCarrier.fsetNamesWithLiveMutationWork(
            app.state,
        ) | terminalContainment.fsetNamesWithLiveTerminalRecords(
            app.state,
        )
        for sName, recordOwner in list(dictContainerOwners.items()):
            if sName in setRetainedNames:
                continue
            fileHandle = getattr(recordOwner, "fileHandleLock", None)
            if fileHandle is not None:
                try:
                    fnReleaseContainerLock(fileHandle)
                except OSError:
                    pass
            dictContainerOwners.pop(sName, None)
            sBoundSessionId = getattr(recordOwner, "sBrowserSessionId", "")
            if (
                dictSessionOwner is not None
                and sBoundSessionId
                and dictSessionOwner.get(sBoundSessionId) == sName
            ):
                dictSessionOwner.pop(sBoundSessionId, None)
    app.state.listLifespanShutdown.append(fnReleaseAllContainerLocks)


def _fnRegisterHubShutdownStopKeepAlive(app):
    """Stop caffeinate for every held container when the hub shuts down.

    ``fnStopKeepAlive`` otherwise only runs on an explicit Stop; without
    this hook a hub that dies (idle self-exit, terminal close) leaks its
    keep-alive caffeinate process for every held container.
    """

    async def fnStopAllKeepAlive(app):
        from ..config.keepAliveManager import fnStopKeepAlive
        dictContainerOwners = getattr(app.state, "dictContainerOwners", {})
        for sName in list(dictContainerOwners.keys()):
            try:
                fnStopKeepAlive(sName)
            except Exception:
                logger.warning("Keep-alive stop failed for %s", sName)

    app.state.listLifespanShutdown.append(fnStopAllKeepAlive)
