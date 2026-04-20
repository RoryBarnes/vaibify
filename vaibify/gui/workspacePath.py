"""Resolve a container ID to the host filesystem path of its workspace.

``gitStatus`` and ``badgeState`` run host-side against a concrete
filesystem path. The dashboard knows containers by their container ID,
but the host-path binding lives in Docker's mount table. This module
runs ``docker inspect`` lazily and caches the result so every dashboard
poll doesn't fork a subprocess.

Graceful degradation: if docker is unavailable or the mount can't be
found, functions return an empty string. Callers treat an empty root
as "git status unavailable" rather than erroring the whole response.
"""

import json
import os
import subprocess

__all__ = [
    "S_CONTAINER_WORKSPACE_PATH",
    "fsHostWorkspacePathForContainer",
    "fnClearCache",
]


S_CONTAINER_WORKSPACE_PATH = "/workspace"

_dictCache = {}


def fnClearCache():
    """Drop cached lookups (for tests + container recreations)."""
    _dictCache.clear()


def _fsRunDockerInspect(sContainerId):
    """Return raw stdout from ``docker inspect`` for one container."""
    try:
        resultProcess = subprocess.run(
            ["docker", "inspect", sContainerId],
            capture_output=True, text=True, timeout=10,
        )
    except (subprocess.TimeoutExpired, OSError):
        return ""
    if resultProcess.returncode != 0:
        return ""
    return resultProcess.stdout or ""


def _fsExtractHostPath(sInspectOutput, sDestination):
    """Return the host path bound to sDestination, or empty on no match."""
    try:
        listContainers = json.loads(sInspectOutput)
    except (ValueError, TypeError):
        return ""
    if not isinstance(listContainers, list) or not listContainers:
        return ""
    dictFirst = listContainers[0]
    listMounts = dictFirst.get("Mounts") or []
    for dictMount in listMounts:
        if dictMount.get("Destination") == sDestination:
            sSource = dictMount.get("Source") or ""
            if sSource and os.path.isdir(sSource):
                return sSource
    return ""


def fsHostWorkspacePathForContainer(sContainerId):
    """Return the host path bound to /workspace for this container.

    Caches positive lookups in memory; re-queries docker on a cache
    miss. Returns an empty string when docker is not reachable or the
    container no longer exists.
    """
    if not sContainerId:
        return ""
    if sContainerId in _dictCache:
        return _dictCache[sContainerId]
    sOutput = _fsRunDockerInspect(sContainerId)
    if not sOutput:
        return ""
    sHostPath = _fsExtractHostPath(
        sOutput, S_CONTAINER_WORKSPACE_PATH,
    )
    if sHostPath:
        _dictCache[sContainerId] = sHostPath
    return sHostPath
