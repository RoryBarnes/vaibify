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

from . import containerOwnership
from . import serverLifespan
from . import serverMiddleware

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
    app = FastAPI(
        title=dictConfig["sTitle"],
        lifespan=serverLifespan._alifespanShared,
    )
    sSessionToken = secrets.token_urlsafe(32)
    _fnInitialiseApplicationState(app, dictConfig, sSessionToken)
    serverMiddleware.fnRegisterMiddleware(app)
    pipelineServer._fnRegisterLastResortExceptionHandler(app)
    dictCtx = _fdictBuildApplicationContext(app, dictConfig, sSessionToken)
    pipelineServer._fnRegisterAllRoutes(
        app, dictCtx, dictConfig["sWorkspaceRoot"],
    )
    _fnRegisterHubLifecycle(app, dictCtx, dictConfig)
    _fnRegisterBackgroundTasks(app, dictCtx)
    return app


def fappCreateApplication(
    sWorkspaceRoot="/workspace", sTerminalUserArg=None,
    iExpectedPort=0,
):
    """Build and return the configured viewer FastAPI application.

    When ``iExpectedPort`` is non-zero, the SessionTokenMiddleware
    enforces a strict ``Host:`` header check (DNS rebinding defense).
    CLI launchers pass the real bind port; test fixtures omit the
    argument so TestClient's default ``testserver`` host is accepted.
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


def _fnRegisterHubShutdownReleaseLocks(app):
    """Release all held container locks when the hub shuts down."""

    async def fnReleaseAllContainerLocks(app):
        from vaibify.config.containerLock import fnReleaseContainerLock
        dictContainerOwners = getattr(app.state, "dictContainerOwners", {})
        for recordOwner in list(dictContainerOwners.values()):
            fileHandle = getattr(recordOwner, "fileHandleLock", None)
            if fileHandle is None:
                continue
            try:
                fnReleaseContainerLock(fileHandle)
            except OSError:
                pass
        dictContainerOwners.clear()
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
