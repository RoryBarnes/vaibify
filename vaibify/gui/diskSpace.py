"""Pre-flight disk-space helpers for the workflow runner.

A workflow that runs to completion only to fail on the last write
because ``/workspace`` ran out of free bytes wastes everything the
sweep already computed. This module gives the pre-flight validator a
cheap way to check the container's free space against a workflow's
declared output budget and surface a banner before the run starts.

The check is intentionally a *warning*, not a hard block: vaibify
cannot honestly estimate output size without a per-workflow
``iEstimatedOutputBytes`` field, and that field is optional. The
caller decides whether to surface or suppress the message; the
runner currently appends it to ``listErrors`` so the dashboard
shows the banner.
"""

import logging

__all__ = [
    "I_DEFAULT_MIN_FREE_BYTES",
    "F_HEADROOM_MULTIPLIER",
    "fnCheckWorkspaceFreeBytes",
    "fdictAssertSpaceForOutputs",
]


_logger = logging.getLogger("vaibify")

I_DEFAULT_MIN_FREE_BYTES = 1 * 1024 * 1024 * 1024
F_HEADROOM_MULTIPLIER = 2.0
_S_WORKSPACE_PATH = "/workspace"


def fnCheckWorkspaceFreeBytes(connectionDocker, sContainerId):
    """Return free bytes in ``/workspace`` as int, or -1 on failure.

    Runs ``df -B1 /workspace`` inside the container and parses the
    free-bytes column. A negative return signals the probe could not
    determine free space (df unavailable, container vanished, output
    malformed); callers should treat that as "unknown" rather than
    "out of space".
    """
    try:
        iExitCode, sOutput = connectionDocker.ftResultExecuteCommand(
            sContainerId, f"df -B1 {_S_WORKSPACE_PATH}",
        )
    except Exception:
        _logger.info(
            "df probe raised for container %s", sContainerId,
        )
        return -1
    if iExitCode != 0 or not sOutput:
        return -1
    return _fiParseFreeBytes(sOutput)


def _fiParseFreeBytes(sOutput):
    """Return the ``Available`` column from a df -B1 output, or -1."""
    listLines = (sOutput or "").splitlines()
    if len(listLines) < 2:
        return -1
    # Locate the data line: df may wrap long device names onto a second
    # line, so the value row is the last non-empty line that has the
    # expected six columns.
    for sLine in reversed(listLines):
        listColumns = sLine.split()
        if len(listColumns) >= 6:
            try:
                return int(listColumns[3])
            except ValueError:
                continue
    return -1


def fdictAssertSpaceForOutputs(
    connectionDocker, sContainerId, iEstimatedBytes,
):
    """Return a warning dict when free space is tight, else None.

    The shape ``{"sCode": ..., "sMessage": ..., "iFreeBytes":
    int, "iRequiredBytes": int}`` lets callers (the runner's
    pre-flight banner, the agent CLI) render an actionable message
    without re-deriving the thresholds.
    """
    iFreeBytes = fnCheckWorkspaceFreeBytes(
        connectionDocker, sContainerId,
    )
    if iFreeBytes < 0:
        return None
    iRequiredBytes = _fiComputeRequiredBytes(iEstimatedBytes)
    if iFreeBytes >= iRequiredBytes:
        return None
    return _fdictBuildSpaceWarning(
        iFreeBytes, iRequiredBytes, iEstimatedBytes,
    )


def _fiComputeRequiredBytes(iEstimatedBytes):
    """Return the larger of 1 GB and 2x the estimated output bytes."""
    try:
        iEstimated = int(iEstimatedBytes or 0)
    except (TypeError, ValueError):
        iEstimated = 0
    iHeadroom = int(iEstimated * F_HEADROOM_MULTIPLIER)
    return max(I_DEFAULT_MIN_FREE_BYTES, iHeadroom)


def _fdictBuildSpaceWarning(iFreeBytes, iRequiredBytes, iEstimatedBytes):
    """Build the human-readable disk-space warning payload."""
    return {
        "sCode": "low-workspace-disk-space",
        "iFreeBytes": iFreeBytes,
        "iRequiredBytes": iRequiredBytes,
        "iEstimatedBytes": int(iEstimatedBytes or 0),
        "sMessage": _fsFormatSpaceMessage(
            iFreeBytes, iRequiredBytes, iEstimatedBytes,
        ),
    }


def _fsFormatSpaceMessage(iFreeBytes, iRequiredBytes, iEstimatedBytes):
    """Format the user-facing free-space warning string."""
    return (
        "Workspace free space is low: "
        f"{_fsFormatBytes(iFreeBytes)} free, "
        f"need at least {_fsFormatBytes(iRequiredBytes)} "
        f"({_fsFormatBytes(iEstimatedBytes or 0)} estimated "
        "output budget x2 headroom). The workflow may run out of "
        "disk before completing."
    )


def _fsFormatBytes(iBytes):
    """Return a short human-readable byte string (GB/MB/KB/B)."""
    iValue = int(iBytes or 0)
    if iValue >= 1024 ** 3:
        return f"{iValue / 1024 ** 3:.1f} GB"
    if iValue >= 1024 ** 2:
        return f"{iValue / 1024 ** 2:.1f} MB"
    if iValue >= 1024:
        return f"{iValue / 1024:.1f} KB"
    return f"{iValue} B"
