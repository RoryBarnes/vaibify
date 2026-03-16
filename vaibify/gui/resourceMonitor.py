"""Container CPU, memory, and disk statistics via subprocess."""

import json
import subprocess


def fdictGetContainerStats(sContainerId):
    """Return CPU and memory stats for a running container.

    Uses ``docker stats --no-stream`` to avoid a Docker-py dependency.
    Returns a dict with fCpuPercent, fMemoryPercent, sMemoryUsage,
    and sMemoryLimit.
    """
    sRawOutput = _fsRunDockerStats(sContainerId)
    if not sRawOutput:
        return _fdictEmptyStats()
    return _fdictParseStatsJson(sRawOutput)


def _fsRunDockerStats(sContainerId):
    """Execute docker stats and return raw JSON string."""
    listCommand = [
        "docker", "stats", "--no-stream",
        "--format", "{{json .}}",
        sContainerId,
    ]
    try:
        resultProcess = subprocess.run(
            listCommand,
            capture_output=True, text=True, timeout=10,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return ""
    if resultProcess.returncode != 0:
        return ""
    return resultProcess.stdout.strip()


def _fdictParseStatsJson(sRawOutput):
    """Parse the JSON line from docker stats into a stats dict."""
    try:
        dictRaw = json.loads(sRawOutput)
    except (json.JSONDecodeError, TypeError):
        return _fdictEmptyStats()
    return {
        "fCpuPercent": _ffParsePercent(dictRaw.get("CPUPerc", "0%")),
        "fMemoryPercent": _ffParsePercent(
            dictRaw.get("MemPerc", "0%")
        ),
        "sMemoryUsage": _fsSplitMemoryUsage(
            dictRaw.get("MemUsage", "0B / 0B")
        ),
        "sMemoryLimit": _fsSplitMemoryLimit(
            dictRaw.get("MemUsage", "0B / 0B")
        ),
    }


def _ffParsePercent(sPercent):
    """Convert a percentage string like '12.34%' to a float."""
    try:
        return float(sPercent.rstrip("%"))
    except (ValueError, AttributeError):
        return 0.0


def _fsSplitMemoryUsage(sMemoryUsage):
    """Return the usage portion before the slash."""
    listParts = sMemoryUsage.split("/")
    return listParts[0].strip() if listParts else "0B"


def _fsSplitMemoryLimit(sMemoryUsage):
    """Return the limit portion after the slash."""
    listParts = sMemoryUsage.split("/")
    if len(listParts) >= 2:
        return listParts[1].strip()
    return "0B"


def _fdictEmptyStats():
    """Return a zeroed stats dict when data is unavailable."""
    return {
        "fCpuPercent": 0.0,
        "fMemoryPercent": 0.0,
        "sMemoryUsage": "0B",
        "sMemoryLimit": "0B",
    }
