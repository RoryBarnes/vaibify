"""Container CPU, memory, and disk statistics.

The functions here surface live container vitals to the GUI. The
return shape is a structured dict so the frontend can distinguish
"daemon unreachable" or "container not running" from the legitimate
"container is idle at 0% CPU". A degraded reading carries
``bAvailable=False`` plus an ``sReason`` so the dashboard can render
an informative state instead of misleading zeros.

The two halves reach the daemon differently, and the difference is the
point. CPU and memory come from ``docker stats``, which asks the DAEMON
about a container and never enters it. Disk used to come from ``docker
exec ... df``, which enters it -- an arbitrary command execution as far
as the boundary can tell -- and now goes through the gateway's typed
read instead.
"""

__all__ = [
    "fdictGetContainerStats",
]

import concurrent.futures
import json
import subprocess


_F_DISK_WARNING_FRACTION = 0.10
_F_DISK_QUERY_TIMEOUT_SECONDS = 10.0
_S_CONTAINER_ROOT_PATH = "/"
_S_REASON_DAEMON = "daemon-unreachable"
_S_REASON_TIMEOUT = "timeout"
_S_REASON_NOT_RUNNING = "container-not-running"
_S_REASON_PARSE = "parse-error"


def fdictGetContainerStats(connectionDocker, sContainerId):
    """Return CPU, memory, and disk stats for a running container.

    ``connectionDocker`` is REQUIRED rather than optional. A default of
    ``None`` with a subprocess fallback would be a silent path back to
    the raw ``docker exec`` this replaced, and the one thing an
    always-available fallback guarantees is that nobody notices when the
    guarded path stops being taken.
    """
    dictStats = _fdictRunStatsCollection(sContainerId)
    dictStats["dictDisk"] = _fdictGetDiskStats(
        connectionDocker, sContainerId,
    )
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


def _fdictGetDiskStats(connectionDocker, sContainerId):
    """Return disk-usage stats for the container's root filesystem.

    This used to run ``docker exec -u <user> <id> df -PB1 /`` from a GUI
    module -- a container exec assembled outside every guarded primitive,
    which the boundary must treat as mutating because the primitive
    cannot know that this particular argv only reads. It is now a TYPED
    READ: the adapter is handed a path and picks a declared operation,
    and the program is fixed source text in the gateway.

    The degraded-state vocabulary is unchanged, because it is what the
    dashboard renders instead of misleading zeros. What changed is where
    each reason comes from: a gone container is now recognised by the
    daemon's own 404/409 rather than by matching English in stderr.
    """
    tUsage = _ftReadFilesystemUsage(connectionDocker, sContainerId)
    bSuccess, sReason, tCounts = tUsage
    if not bSuccess:
        return _fdictUnavailableDiskStats(sReason)
    return _fdictBuildDiskPayload(*tCounts)


def _ftReadUsageCounts(connectionDocker, sContainerId):
    """Return ``(total, used, free)`` bytes for the container rootfs.

    The field extraction lives HERE, inside what the deadline wraps,
    rather than at the caller. A reading missing a field would otherwise
    raise past the classification and reach the dashboard as a traceback
    instead of the parse-error the degraded vocabulary exists to carry.
    """
    dictUsage = connectionDocker.fdictReadFilesystemUsage(
        sContainerId, _S_CONTAINER_ROOT_PATH,
    )
    return (
        dictUsage["iTotalBytes"],
        dictUsage["iUsedBytes"],
        dictUsage["iFreeBytes"],
    )


def _ftReadFilesystemUsage(connectionDocker, sContainerId):
    """Read the rootfs usage under a deadline; classify any failure.

    The deadline is preserved deliberately. The raw call it replaced
    carried ``timeout=10``; the gateway's client timeout is ten MINUTES,
    so dropping the bound would let one wedged container hold the
    dashboard's monitor request for that long and report nothing at all
    in the meantime.

    What the bound does and does not do, because a timeout that is
    described loosely is worse than none: it bounds how long the CALLER
    waits. The exec continues inside the container, exactly as the
    orphaned ``docker exec`` did when its subprocess timeout fired. The
    executor is shut down with ``wait=False`` for that reason -- the
    ``with`` form re-joins the worker on exit, which makes the timeout
    report a stall while still blocking for its full duration.
    """
    executorPool = concurrent.futures.ThreadPoolExecutor(max_workers=1)
    try:
        future = executorPool.submit(
            _ftReadUsageCounts, connectionDocker, sContainerId,
        )
        try:
            return (True, "", future.result(
                timeout=_F_DISK_QUERY_TIMEOUT_SECONDS,
            ))
        except concurrent.futures.TimeoutError:
            return (False, _S_REASON_TIMEOUT, None)
        except Exception as error:
            return (False, _fsClassifyReadFailure(error), None)
    finally:
        executorPool.shutdown(wait=False)


def _fsClassifyReadFailure(error):
    """Map a typed-read failure onto the dashboard's degraded reasons.

    ``fbErrorMeansContainerGone`` is the gateway's own predicate rather
    than a second copy: a 404, or a 409 saying the container is not
    running, is what "not running" means to the daemon. Everything else
    is the daemon-unreachable catch-all the string-matching version also
    fell back to, so an unrecognised failure keeps reporting a degraded
    state rather than a plausible-looking zero.
    """
    from vaibify.docker.dockerConnection import fbErrorMeansContainerGone
    if isinstance(error, (json.JSONDecodeError, TypeError, KeyError)):
        return _S_REASON_PARSE
    if fbErrorMeansContainerGone(error):
        return _S_REASON_NOT_RUNNING
    return _S_REASON_DAEMON


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
