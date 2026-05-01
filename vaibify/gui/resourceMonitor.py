"""Container CPU, memory, and disk statistics via subprocess.

The functions here surface live container vitals to the GUI. The
return shape is a structured dict so the frontend can distinguish
"daemon unreachable" or "container not running" from the legitimate
"container is idle at 0% CPU". A degraded reading carries
``bAvailable=False`` plus an ``sReason`` so the dashboard can render
an informative state instead of misleading zeros.
"""

__all__ = [
    "fdictGetContainerStats",
]

import json
import subprocess


_F_DISK_WARNING_FRACTION = 0.10
_S_REASON_DAEMON = "daemon-unreachable"
_S_REASON_TIMEOUT = "timeout"
_S_REASON_NOT_RUNNING = "container-not-running"
_S_REASON_PARSE = "parse-error"


def fdictGetContainerStats(sContainerId):
    """Return CPU, memory, and disk stats for a running container."""
    dictStats = _fdictRunStatsCollection(sContainerId)
    dictStats["dictDisk"] = _fdictGetDiskStats(sContainerId)
    dictStats["bDiskWarning"] = _fbIsDiskWarning(dictStats["dictDisk"])
    return dictStats


def _fdictRunStatsCollection(sContainerId):
    """Collect docker stats output and translate it into the response dict."""
    tStatsResult = _ftRunDockerStats(sContainerId)
    bSuccess, sReason, sRawOutput = tStatsResult
    if not bSuccess:
        return _fdictUnavailableStats(sReason)
    return _fdictParseStatsJson(sRawOutput)


def _ftRunDockerStats(sContainerId):
    """Execute docker stats, returning (bSuccess, sReason, sStdout)."""
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
    except FileNotFoundError:
        return (False, _S_REASON_DAEMON, "")
    except subprocess.TimeoutExpired:
        return (False, _S_REASON_TIMEOUT, "")
    if resultProcess.returncode != 0:
        return (False, _fsClassifyDockerError(resultProcess.stderr), "")
    return (True, "", resultProcess.stdout.strip())


def _fsClassifyDockerError(sStderr):
    """Map a non-zero docker stats stderr message to a stable reason."""
    sLower = (sStderr or "").lower()
    if "cannot connect to the docker daemon" in sLower:
        return _S_REASON_DAEMON
    if "no such container" in sLower or "is not running" in sLower:
        return _S_REASON_NOT_RUNNING
    return _S_REASON_DAEMON


def _fdictParseStatsJson(sRawOutput):
    """Parse the JSON line from docker stats into a stats dict."""
    try:
        dictRaw = json.loads(sRawOutput)
    except (json.JSONDecodeError, TypeError):
        return _fdictUnavailableStats(_S_REASON_PARSE)
    return {
        "bAvailable": True,
        "sReason": "",
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


def _fdictUnavailableStats(sReason):
    """Return an unavailable stats payload preserving zeroed fields."""
    return {
        "bAvailable": False,
        "sReason": sReason,
        "fCpuPercent": 0.0,
        "fMemoryPercent": 0.0,
        "sMemoryUsage": "0B",
        "sMemoryLimit": "0B",
    }


def _fdictGetDiskStats(sContainerId):
    """Return disk-usage stats for the container's root filesystem."""
    tDiskResult = _ftRunContainerDiskQuery(sContainerId)
    bSuccess, sReason, sRawOutput = tDiskResult
    if not bSuccess:
        return _fdictUnavailableDiskStats(sReason)
    return _fdictParseDfOutput(sRawOutput)


def _ftRunContainerDiskQuery(sContainerId):
    """Run df inside the container and return (bSuccess, sReason, sStdout)."""
    listCommand = [
        "docker", "exec", sContainerId,
        "df", "-PB1", "/",
    ]
    try:
        resultProcess = subprocess.run(
            listCommand,
            capture_output=True, text=True, timeout=10,
        )
    except FileNotFoundError:
        return (False, _S_REASON_DAEMON, "")
    except subprocess.TimeoutExpired:
        return (False, _S_REASON_TIMEOUT, "")
    if resultProcess.returncode != 0:
        return (False, _fsClassifyDockerError(resultProcess.stderr), "")
    return (True, "", resultProcess.stdout)


def _fdictParseDfOutput(sRawOutput):
    """Parse a `df -PB1 /` table into total/used/free byte counts."""
    listLines = [s for s in sRawOutput.splitlines() if s.strip()]
    if len(listLines) < 2:
        return _fdictUnavailableDiskStats(_S_REASON_PARSE)
    listFields = listLines[-1].split()
    if len(listFields) < 4:
        return _fdictUnavailableDiskStats(_S_REASON_PARSE)
    try:
        iTotalBytes = int(listFields[1])
        iUsedBytes = int(listFields[2])
        iFreeBytes = int(listFields[3])
    except ValueError:
        return _fdictUnavailableDiskStats(_S_REASON_PARSE)
    return _fdictBuildDiskPayload(iTotalBytes, iUsedBytes, iFreeBytes)


def _fdictBuildDiskPayload(iTotalBytes, iUsedBytes, iFreeBytes):
    """Translate raw byte counts into the disk payload."""
    fFreeFraction = _ffSafeFraction(iFreeBytes, iTotalBytes)
    return {
        "bAvailable": True,
        "sReason": "",
        "iTotalBytes": iTotalBytes,
        "iUsedBytes": iUsedBytes,
        "iFreeBytes": iFreeBytes,
        "fFreeFraction": fFreeFraction,
        "sTotalHuman": _fsFormatBytes(iTotalBytes),
        "sUsedHuman": _fsFormatBytes(iUsedBytes),
        "sFreeHuman": _fsFormatBytes(iFreeBytes),
    }


def _ffSafeFraction(iNumerator, iDenominator):
    """Return iNumerator/iDenominator, falling back to 0.0 on bad input."""
    if iDenominator <= 0:
        return 0.0
    return float(iNumerator) / float(iDenominator)


def _fdictUnavailableDiskStats(sReason):
    """Return a disk payload that signals data was not collected."""
    return {
        "bAvailable": False,
        "sReason": sReason,
        "iTotalBytes": 0,
        "iUsedBytes": 0,
        "iFreeBytes": 0,
        "fFreeFraction": 0.0,
        "sTotalHuman": "",
        "sUsedHuman": "",
        "sFreeHuman": "",
    }


def _fbIsDiskWarning(dictDisk):
    """Return True when free space dipped below the warning threshold."""
    if not dictDisk.get("bAvailable"):
        return False
    return dictDisk.get("fFreeFraction", 0.0) < _F_DISK_WARNING_FRACTION


def _fsFormatBytes(iBytes):
    """Format an integer byte count into a short human-readable string."""
    listUnits = ["B", "KiB", "MiB", "GiB", "TiB", "PiB"]
    dCurrent = float(iBytes)
    for sUnit in listUnits:
        if dCurrent < 1024.0 or sUnit == listUnits[-1]:
            return f"{dCurrent:.1f} {sUnit}"
        dCurrent /= 1024.0
    return f"{dCurrent:.1f} {listUnits[-1]}"
