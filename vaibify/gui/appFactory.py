"""FastAPI application factory for the viewer and hub server modes.

Builds one configured application from a config dict, expressing the
hub-versus-viewer differences as flags and lifecycle hooks rather than
two near-duplicate factories. The connection probe, route registration,
context builder, and exception handler are reached through
``pipelineServer`` so patched test doubles are honoured and
route-registration semantics stay untouched.
"""

import logging
import secrets
import time

from fastapi import FastAPI

from . import browserSession
from . import commitCarrier
from . import containerOwnership
from . import serverLifespan
from . import serverMiddleware
from . import sessionLifecycle

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
        pipelineServer._fconnectionCreateDocker(),
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
    fnRegisterRegistryRoutes(app, dictCtx)
    _fnRegisterHubShutdownStopKeepAlive(app)
    _fnRegisterHubLockLifecycle(app)


def _fnRegisterBackgroundTasks(app, dictCtx):
    """Install the sweep, idle-watchdog, and threadpool lifespan tasks.

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
        lifespan=serverLifespan._alifespanShared,
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
    _fnRegisterHubShutdownReleaseLocks(app)


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
    """

    async def fnDrainGuardedMutations(app):
        await commitCarrier.fdictDrainMutationSupervisors(app.state)

    app.state.listLifespanShutdown.append(fnDrainGuardedMutations)


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
