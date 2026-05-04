"""System endpoint route handlers."""

__all__ = ["fnRegisterAll"]

import asyncio
import concurrent.futures
import json
import os

from .. import pipelineServer as _pipelineServer
from ..pipelineServer import (
    fdictGetDockerStatus,
    fdictRetryDockerConnection,
    fsDetectDockerRuntime,
)
from ..resourceMonitor import fdictGetContainerStats


_S_READY_MARKER_PATH = "/workspace/.vaibify/.entrypoint_ready"
_F_READY_PROBE_TIMEOUT_SECONDS = 5.0


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


def _ftReadReadinessMarker(connectionDocker, sContainerId):
    """Return (sStatus, sRaw) for the readiness marker; sStatus may be 'missing'."""
    sCommand = (
        "test -f " + _S_READY_MARKER_PATH
        + " && cat " + _S_READY_MARKER_PATH
    )
    iExitCode, sOutput = connectionDocker.ftResultExecuteCommand(
        sContainerId, sCommand,
    )
    if iExitCode != 0:
        return ("missing", "")
    return ("present", sOutput)


def _fdictParseReadinessMarker(sRaw):
    """Parse the marker contents into a normalized dict."""
    sStripped = (sRaw or "").strip()
    if not sStripped:
        return {"sStatus": "ok", "sReason": "", "saWarnings": []}
    try:
        dictParsed = json.loads(sStripped)
    except (ValueError, TypeError):
        return {"sStatus": "ok", "sReason": "", "saWarnings": []}
    return {
        "sStatus": str(dictParsed.get("sStatus") or "ok"),
        "sReason": str(dictParsed.get("sReason") or ""),
        "saWarnings": list(dictParsed.get("saWarnings") or []),
    }


def _fdictBuildReadyResponse(dictMarker):
    """Translate parsed marker into the API response shape."""
    sStatus = dictMarker.get("sStatus") or "ok"
    listWarnings = dictMarker.get("saWarnings") or []
    bReady = sStatus in ("ok", "failed")
    return {
        "bReady": bReady,
        "sStatus": sStatus,
        "sReason": dictMarker.get("sReason") or "",
        "saWarnings": listWarnings,
        "iWarningCount": len(listWarnings),
    }


def _fdictStalledResponse():
    """Return the response payload for a probe that exceeded its timeout."""
    return {
        "bReady": False,
        "sStatus": "stalled",
        "sReason": (
            "container is running but not responding to exec. "
            "Try `vaibify stop && vaibify start`."
        ),
        "saWarnings": [],
        "iWarningCount": 0,
    }


def _fdictBootingResponse():
    """Return the response payload for a missing readiness marker."""
    return {
        "bReady": False,
        "sStatus": "booting",
        "sReason": "",
        "saWarnings": [],
        "iWarningCount": 0,
    }


def _fdictProbeWithTimeout(connectionDocker, sContainerId):
    """Run the readiness probe under a hard timeout."""
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executorPool:
        future = executorPool.submit(
            _ftReadReadinessMarker, connectionDocker, sContainerId,
        )
        try:
            sStatus, sRaw = future.result(
                timeout=_F_READY_PROBE_TIMEOUT_SECONDS,
            )
        except concurrent.futures.TimeoutError:
            return _fdictStalledResponse()
    if sStatus == "missing":
        return _fdictBootingResponse()
    return _fdictBuildReadyResponse(_fdictParseReadinessMarker(sRaw))


def _fdictProbeContainerReadiness(connectionDocker, sContainerId):
    """Top-level probe wrapper that swallows infrastructure errors."""
    try:
        return _fdictProbeWithTimeout(connectionDocker, sContainerId)
    except Exception as exception:
        return {
            "bReady": False,
            "sStatus": "error",
            "sReason": f"readiness probe failed: {exception}",
            "saWarnings": [],
            "iWarningCount": 0,
        }


def _fnRegisterContainerReady(app, dictCtx):
    """Register GET /api/containers/{id}/ready readiness probe."""

    @app.get("/api/containers/{sContainerId}/ready")
    async def fnContainerReady(sContainerId: str):
        dictCtx["require"]()
        return await asyncio.to_thread(
            _fdictProbeContainerReadiness,
            dictCtx["docker"], sContainerId,
        )


def _fdictReadIsolationFlag(sContainerId):
    """Return the runtime isolation flag for a container id."""
    from vaibify.docker.containerManager import (
        fbContainerIsNetworkIsolated,
    )
    return {
        "bNetworkIsolation":
            fbContainerIsNetworkIsolated(sContainerId),
    }


def _fnRegisterContainerIsolation(app, dictCtx):
    """Register GET /api/containers/{id}/isolation endpoint.

    Returns the runtime ``--network none`` setting so the GUI can
    disable Overleaf, Zenodo, and other network-bound buttons before
    the user clicks them and waits for a 30-second DNS timeout.
    Audit finding F-R-08.
    """

    @app.get("/api/containers/{sContainerId}/isolation")
    async def fnContainerIsolation(sContainerId: str):
        dictCtx["require"]()
        return await asyncio.to_thread(
            _fdictReadIsolationFlag, sContainerId,
        )


def _fnRegisterDockerStatus(app, dictCtx):
    """Register the Docker availability probe + retry endpoints.

    GET returns the cached diagnosis so the container hub can render
    a banner immediately on page load. POST forces a fresh probe and
    swaps ``dictCtx['docker']`` on success, letting the user recover
    without restarting vaibify after the runtime comes back up.
    """

    @app.get("/api/system/docker-status")
    async def fnGetDockerStatus():
        return await asyncio.to_thread(fdictGetDockerStatus)

    @app.post("/api/system/docker-status/retry")
    async def fnPostDockerStatusRetry():
        return await asyncio.to_thread(
            fdictRetryDockerConnection, dictCtx,
        )


def fnRegisterAll(app, dictCtx):
    """Register all system routes."""
    _fnRegisterMonitor(app)
    _fnRegisterRuntimeInfo(app, dictCtx)
    _fnRegisterUserInfo(app)
    _fnRegisterContainerReady(app, dictCtx)
    _fnRegisterContainerIsolation(app, dictCtx)
    _fnRegisterDockerStatus(app, dictCtx)
