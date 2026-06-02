"""Pipeline state persistence for reconnecting to running pipelines.

Writes state to /workspace/.vaibify/pipeline_state.json inside the
container so the GUI can recover pipeline status after a browser
disconnect, tab close, or GUI restart.
"""

__all__ = [
    "I_MAX_OUTPUT_LINES",
    "I_HEARTBEAT_INTERVAL_SECONDS",
    "I_HEARTBEAT_STALE_SECONDS",
    "I_EXIT_CODE_RUNNER_DISAPPEARED",
    "S_STATE_PATH",
    "S_STATE_PATH_TEMP",
    "fdictBuildInitialState",
    "fdictBuildStepStarted",
    "fdictBuildStepResult",
    "fdictBuildCompletedState",
    "fdictBuildInteractivePauseState",
    "fdictBuildHeartbeatUpdate",
    "fbHeartbeatIsStale",
    "fnWriteState",
    "fnUpdateState",
    "fnRecordStepResult",
    "fnAppendOutput",
    "fdictReadState",
    "fdictReadReconciledState",
    "fsBuildHeartbeatStaleReason",
    "fnClearState",
]

import asyncio
import json
from datetime import datetime, timezone

I_MAX_OUTPUT_LINES = 500
I_HEARTBEAT_INTERVAL_SECONDS = 5
I_HEARTBEAT_STALE_SECONDS = 15
# Sentinel exit code stamped by the poll-side reconciler when the
# runner thread has vanished without writing a final state. Sits
# outside the OS exit-code range (0-255) so callers can distinguish
# a runner crash from any real subprocess exit.
I_EXIT_CODE_RUNNER_DISAPPEARED = -9999
S_STATE_PATH = "/workspace/.vaibify/pipeline_state.json"
S_STATE_PATH_TEMP = "/workspace/.vaibify/pipeline_state.json.tmp"


def fdictBuildInitialState(sAction, sLogPath, iStepCount, iRunnerPid=0):
    """Build the initial state dictionary when a pipeline starts.

    The ``iRunnerPid``/``sLastHeartbeat``/``sFailureReason`` triple is the
    runner-liveness contract. The runner stamps its own PID on start and
    updates ``sLastHeartbeat`` from a daemon thread; the poll endpoint
    reconciles ``bRunning`` to ``False`` and stamps ``sFailureReason`` if
    the heartbeat is older than the staleness window.
    """
    return {
        "bRunning": True,
        "sAction": sAction,
        "sLogPath": sLogPath,
        "sStartTime": datetime.now(timezone.utc).isoformat(),
        "sEndTime": "",
        "iExitCode": -1,
        "iActiveStep": -1,
        "iStepCount": iStepCount,
        "dictStepResults": {},
        "listRecentOutput": [],
        "iRunnerPid": iRunnerPid,
        "sLastHeartbeat": datetime.now(timezone.utc).isoformat(),
        "sFailureReason": "",
    }


def fdictBuildStepStarted(iStepNumber):
    """Return a partial update dict for a step starting."""
    return {"iActiveStep": iStepNumber}


def fdictBuildStepResult(iStepNumber, sStatus, iExitCode=0):
    """Return a result entry for a completed step."""
    return {
        "iStepNumber": iStepNumber,
        "sStatus": sStatus,
        "iExitCode": iExitCode,
    }


def fdictBuildCompletedState(iExitCode):
    """Return a partial update dict for pipeline completion."""
    return {
        "bRunning": False,
        "bInteractivePause": False,
        "iActiveStep": -1,
        "iExitCode": iExitCode,
        "sEndTime": datetime.now(timezone.utc).isoformat(),
    }


def fdictBuildInteractivePauseState(iStepNumber, sStepName):
    """Return a partial update for an interactive pause."""
    return {
        "bRunning": True,
        "bInteractivePause": True,
        "iActiveStep": iStepNumber,
        "sActiveStepName": sStepName,
    }


def fnWriteState(connectionDocker, sContainerId, dictState):
    """Write the state dict atomically via temp-then-rename.

    A concurrent reader (badge poll, agent CLI, watchdog reconciler)
    must never observe a half-written JSON document. The temp-file
    plus ``mv`` pattern relies on POSIX rename atomicity within the
    same filesystem so the canonical path either has the previous
    contents or the new contents — never a truncated mix.
    """
    sContent = json.dumps(dictState, indent=2)
    connectionDocker.fnWriteFile(
        sContainerId, S_STATE_PATH_TEMP, sContent.encode("utf-8")
    )
    connectionDocker.ftResultExecuteCommand(
        sContainerId, f"mv {S_STATE_PATH_TEMP} {S_STATE_PATH}",
    )


def fnUpdateState(connectionDocker, sContainerId, dictState, dictUpdate):
    """Merge dictUpdate into dictState and write to container."""
    dictState.update(dictUpdate)
    fnWriteState(connectionDocker, sContainerId, dictState)


def fnRecordStepResult(
    connectionDocker, sContainerId, dictState, dictResult
):
    """Add a step result and write to container."""
    sKey = str(dictResult["iStepNumber"])
    dictState["dictStepResults"][sKey] = {
        "sStatus": dictResult["sStatus"],
        "iExitCode": dictResult["iExitCode"],
    }
    fnWriteState(connectionDocker, sContainerId, dictState)


def fnAppendOutput(dictState, sLine):
    """Append an output line to the ring buffer."""
    listOutput = dictState["listRecentOutput"]
    listOutput.append(sLine)
    if len(listOutput) > I_MAX_OUTPUT_LINES:
        dictState["listRecentOutput"] = listOutput[-I_MAX_OUTPUT_LINES:]


def fdictBuildHeartbeatUpdate():
    """Return a partial-update dict that refreshes ``sLastHeartbeat``."""
    return {"sLastHeartbeat": datetime.now(timezone.utc).isoformat()}


def fbHeartbeatIsStale(dictState, fNowEpoch=None):
    """Return True iff ``sLastHeartbeat`` is older than the staleness window.

    Legacy state files written before the heartbeat contract existed may
    omit ``sLastHeartbeat`` entirely; treat those as not-stale so we
    don't spuriously reconcile state from old runs.
    """
    sLastHeartbeat = dictState.get("sLastHeartbeat", "")
    if not sLastHeartbeat:
        return False
    try:
        dtBeat = datetime.fromisoformat(sLastHeartbeat)
    except ValueError:
        return False
    if fNowEpoch is None:
        fNowEpoch = datetime.now(timezone.utc).timestamp()
    return (fNowEpoch - dtBeat.timestamp()) > I_HEARTBEAT_STALE_SECONDS


def fdictReadState(connectionDocker, sContainerId):
    """Read the pipeline state from the container, or None.

    Any failure mode — docker daemon hiccup, half-written file mid-rename,
    container down — degrades to ``None`` so callers (badge poll, agent
    CLI, watchdog) always have a usable answer instead of an exception
    bubbling up to the request handler.
    """
    try:
        iExitCode, sOutput = connectionDocker.ftResultExecuteCommand(
            sContainerId,
            f"cat {S_STATE_PATH} 2>/dev/null",
        )
        if iExitCode != 0 or not sOutput.strip():
            return None
        return json.loads(sOutput)
    except (json.JSONDecodeError, OSError, TypeError, ValueError):
        return None


def fnClearState(connectionDocker, sContainerId):
    """Remove the pipeline state file."""
    connectionDocker.ftResultExecuteCommand(
        sContainerId, f"rm -f {S_STATE_PATH} {S_STATE_PATH_TEMP}"
    )


def fsBuildHeartbeatStaleReason(dictState, fNowEpoch=None):
    """Return a human-readable reason string for a stale heartbeat."""
    sLastHeartbeat = dictState.get("sLastHeartbeat", "")
    try:
        dtBeat = datetime.fromisoformat(sLastHeartbeat)
        if fNowEpoch is None:
            fNowEpoch = datetime.now(timezone.utc).timestamp()
        fAgeSeconds = fNowEpoch - dtBeat.timestamp()
        return (
            f"heartbeat_stale (last beat {fAgeSeconds:.0f}s ago, "
            f"window {I_HEARTBEAT_STALE_SECONDS}s)"
        )
    except (ValueError, TypeError):
        return "heartbeat_stale (unparseable timestamp)"


def _flockAcquireStateLockForContainer(dictCtx, sContainerId):
    """Lazily allocate and return the per-container reconciliation lock."""
    dictLocks = dictCtx.setdefault("dictPipelineStateLocks", {})
    lockState = dictLocks.get(sContainerId)
    if lockState is None:
        lockState = asyncio.Lock()
        dictLocks[sContainerId] = lockState
    return lockState


def _fdictReconcileStaleHeartbeat(dictState, fNow=None):
    """Return a reconciled copy of state where the runner is declared dead."""
    dictReconciled = dict(dictState)
    dictReconciled.update(
        fdictBuildCompletedState(I_EXIT_CODE_RUNNER_DISAPPEARED),
    )
    dictReconciled["sFailureReason"] = fsBuildHeartbeatStaleReason(
        dictState, fNow,
    )
    return dictReconciled


async def fdictReadReconciledState(dictCtx, sContainerId, fNow=None):
    """Read pipeline state and reconcile a vanished runner inline.

    The runner stamps ``sLastHeartbeat`` from a daemon thread; if the
    file still claims ``bRunning: True`` but the heartbeat is older
    than ``I_HEARTBEAT_STALE_SECONDS``, the runner is presumed dead.
    The reconciler flips ``bRunning`` to False, stamps the sentinel
    exit code, and writes atomically. Subsequent calls observe the
    already-reconciled file and return it unchanged.
    """
    connectionDocker = dictCtx["docker"]
    lockState = _flockAcquireStateLockForContainer(dictCtx, sContainerId)
    async with lockState:
        dictState = await asyncio.to_thread(
            fdictReadState, connectionDocker, sContainerId,
        )
        if dictState is None:
            return None
        if not dictState.get("bRunning"):
            return dictState
        if not fbHeartbeatIsStale(dictState, fNow):
            return dictState
        dictReconciled = _fdictReconcileStaleHeartbeat(dictState, fNow)
        await asyncio.to_thread(
            fnWriteState, connectionDocker, sContainerId, dictReconciled,
        )
        return dictReconciled
