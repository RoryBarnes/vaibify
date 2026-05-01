"""System endpoint route handlers."""

__all__ = ["fnRegisterAll"]

import asyncio
import os

from .. import pipelineServer as _pipelineServer
from ..pipelineServer import fsDetectDockerRuntime
from ..resourceMonitor import fdictGetContainerStats


def _fnRegisterMonitor(app):
    """Register GET /api/monitor route."""

    @app.get("/api/monitor/{sContainerId}")
    async def fnGetMonitorStats(sContainerId: str):
        return await asyncio.to_thread(
            fdictGetContainerStats, sContainerId,
        )


def _fnRegisterRuntimeInfo(app, dictCtx):
    """Register GET /api/runtime endpoint."""

    @app.get("/api/runtime")
    async def fnGetRuntimeInfo():
        return await asyncio.to_thread(fsDetectDockerRuntime)


def _fnRegisterUserInfo(app):
    """Register GET /api/user route."""

    @app.get("/api/user")
    async def fnGetUser():
        return {
            "sUserName": _pipelineServer.sTerminalUser or "User"
        }


def _fbProbeEntrypointReady(connectionDocker, sContainerId):
    """Return True when the entrypoint ready marker exists."""
    try:
        iExitCode, _ = connectionDocker.ftResultExecuteCommand(
            sContainerId,
            "test -f /workspace/.vaibify/.entrypoint_ready",
        )
        return iExitCode == 0
    except Exception:
        return False


def _fnRegisterContainerReady(app, dictCtx):
    """Register GET /api/containers/{id}/ready readiness probe."""

    @app.get("/api/containers/{sContainerId}/ready")
    async def fnContainerReady(sContainerId: str):
        dictCtx["require"]()
        bReady = await asyncio.to_thread(
            _fbProbeEntrypointReady,
            dictCtx["docker"], sContainerId,
        )
        return {"bReady": bReady}


def fnRegisterAll(app, dictCtx):
    """Register all system routes."""
    _fnRegisterMonitor(app)
    _fnRegisterRuntimeInfo(app, dictCtx)
    _fnRegisterUserInfo(app)
    _fnRegisterContainerReady(app, dictCtx)
