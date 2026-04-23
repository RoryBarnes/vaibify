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


def fnRegisterAll(app, dictCtx):
    """Register all system routes."""
    _fnRegisterMonitor(app)
    _fnRegisterRuntimeInfo(app, dictCtx)
    _fnRegisterUserInfo(app)
