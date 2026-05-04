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
_S_VAIBIFY_DIR_PATH = "/workspace/.vaibify"
_F_READY_PROBE_TIMEOUT_SECONDS = 5.0
_I_STALE_IMAGE_THRESHOLD_SECONDS = 30
_S_EXPECTED_ENTRYPOINT_VERSION = "1"


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
    """Return ``(sStatus, sBody)`` describing entrypoint readiness state.

    ``sStatus`` is one of:

    - ``"present"``: the marker file exists; ``sBody`` carries its
      raw contents (which the caller parses as JSON).
    - ``"stale-image"``: the ``.vaibify`` directory exists but the
      marker file does not, and the directory is older than
      :data:`_I_STALE_IMAGE_THRESHOLD_SECONDS`. This signals an image
      built before the marker code was added — the entrypoint
      finished its work but never wrote the marker, so reconnects
      otherwise wait the full polling timeout for nothing.
    - ``"missing"``: neither the marker nor the ``.vaibify`` directory
      exists in a way we recognize as stale; the entrypoint is still
      booting (real boot) and the caller should keep polling.

    The shell command is always exit-0 on a healthy container; an
    exit-nonzero from the docker exec is treated as "missing" so a
    transient exec error does not falsely flag the image as stale.
    """
    sCommand = _fsBuildReadinessProbeCommand()
    iExitCode, sOutput = connectionDocker.ftResultExecuteCommand(
        sContainerId, sCommand,
    )
    if iExitCode != 0:
        return ("missing", "")
    return _ftParseProbeOutput(sOutput)


def _fsBuildReadinessProbeCommand():
    """Return the shell command that classifies the readiness state.

    Output shape (one of):

    - ``MARKER_PRESENT\\n<json-body>``
    - ``DIR_ONLY <age-seconds>``
    - ``NOTHING``

    The directory age is computed from ``stat -c %Y`` versus
    ``date +%s``; both are coreutils on every supported container
    base image. ``stat`` falures (e.g., the dir disappeared between
    the test and the stat) collapse to age 0, which the host
    classifies as still booting rather than stale.
    """
    return (
        f"if [ -f {_S_READY_MARKER_PATH} ]; then "
        f"echo MARKER_PRESENT; cat {_S_READY_MARKER_PATH}; "
        f"elif [ -d {_S_VAIBIFY_DIR_PATH} ]; then "
        f"iAge=$(( $(date +%s) - "
        f"$(stat -c %Y {_S_VAIBIFY_DIR_PATH} 2>/dev/null "
        f"|| echo 0) )); "
        f"echo \"DIR_ONLY $iAge\"; "
        f"else echo NOTHING; fi"
    )


def _ftParseProbeOutput(sOutput):
    """Translate the probe command's output into ``(sStatus, sBody)``."""
    sStripped = (sOutput or "").lstrip()
    if not sStripped:
        return ("present", "")
    if sStripped.startswith("MARKER_PRESENT"):
        sBody = sStripped[len("MARKER_PRESENT"):].lstrip("\n")
        return ("present", sBody)
    if sStripped.startswith("DIR_ONLY"):
        return _ftParseDirOnlyLine(sStripped)
    if sStripped.startswith("NOTHING"):
        return ("missing", "")
    return ("present", sOutput)


def _ftParseDirOnlyLine(sStripped):
    """Decide whether a ``DIR_ONLY <age>`` line means stale or booting."""
    listParts = sStripped.split()
    try:
        iAge = int(listParts[1])
    except (IndexError, ValueError):
        iAge = 0
    if iAge >= _I_STALE_IMAGE_THRESHOLD_SECONDS:
        return ("stale-image", "")
    return ("missing", "")


def _fdictParseReadinessMarker(sRaw):
    """Parse the marker contents into a normalized dict.

    ``sEntrypointVersion`` is read with an empty-string default so a
    legacy marker (written before version baking) reaches the response
    builder without raising; the version-mismatch check there only
    fires when an explicit version differs from the host's expected
    value, never on absent values.
    """
    sStripped = (sRaw or "").strip()
    if not sStripped:
        return {
            "sStatus": "ok", "sReason": "",
            "saWarnings": [], "sEntrypointVersion": "",
        }
    try:
        dictParsed = json.loads(sStripped)
    except (ValueError, TypeError):
        return {
            "sStatus": "ok", "sReason": "",
            "saWarnings": [], "sEntrypointVersion": "",
        }
    return {
        "sStatus": str(dictParsed.get("sStatus") or "ok"),
        "sReason": str(dictParsed.get("sReason") or ""),
        "saWarnings": list(dictParsed.get("saWarnings") or []),
        "sEntrypointVersion": str(
            dictParsed.get("sEntrypointVersion") or "",
        ),
    }


def _fdictBuildReadyResponse(dictMarker):
    """Translate parsed marker into the API response shape.

    A marker whose ``sEntrypointVersion`` is non-empty and differs
    from the host's expected version flips the response to
    ``"stale-version"`` so the dashboard can surface a rebuild
    suggestion. Absent versions (legacy markers) pass through
    unchanged so we never falsely flag images that simply predate
    the version field.
    """
    sStatus = dictMarker.get("sStatus") or "ok"
    listWarnings = dictMarker.get("saWarnings") or []
    sActualVersion = dictMarker.get("sEntrypointVersion") or ""
    if _fbVersionIsMismatched(sActualVersion):
        return _fdictStaleVersionResponse(sActualVersion)
    bReady = sStatus in ("ok", "failed")
    return {
        "bReady": bReady,
        "sStatus": sStatus,
        "sReason": dictMarker.get("sReason") or "",
        "saWarnings": listWarnings,
        "iWarningCount": len(listWarnings),
    }


def _fbVersionIsMismatched(sActualVersion):
    """Return True when the marker carries a non-matching version."""
    if not sActualVersion:
        return False
    return sActualVersion != _S_EXPECTED_ENTRYPOINT_VERSION


def _fdictStaleImageResponse():
    """Return the response payload for a marker-less stale image.

    ``bReady`` is True so the dashboard exits its polling spinner —
    the container is otherwise functional, just slow on every
    reconnect because the host probe waits for a marker the
    entrypoint never writes. The dashboard surfaces the ``sReason``
    as a banner with a Rebuild instruction.
    """
    return {
        "bReady": True,
        "sStatus": "stale-image",
        "sReason": (
            "Container's image predates the readiness-marker "
            "entrypoint. Reconnects will be slow until you rebuild "
            "the image (kebab menu → Rebuild)."
        ),
        "saWarnings": [],
        "iWarningCount": 0,
    }


def _fdictStaleVersionResponse(sActualVersion):
    """Return the response payload for a version-mismatched marker."""
    return {
        "bReady": True,
        "sStatus": "stale-version",
        "sReason": (
            f"Container entrypoint reports version "
            f"\"{sActualVersion}\"; host expects "
            f"\"{_S_EXPECTED_ENTRYPOINT_VERSION}\". Some features "
            "may behave incorrectly until you rebuild the image "
            "(kebab menu → Rebuild)."
        ),
        "saWarnings": [],
        "iWarningCount": 0,
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
    if sStatus == "stale-image":
        return _fdictStaleImageResponse()
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
