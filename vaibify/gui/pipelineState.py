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
    "StateWriter",
    "fnEvictStateLockForContainer",
]

import asyncio
import json
import logging
import queue
import threading
from datetime import datetime, timezone

try:
    import docker.errors as _dockerErrors
    _T_DOCKER_API_ERROR = (_dockerErrors.APIError,)
except ImportError:  # docker SDK absent in some test environments
    _T_DOCKER_API_ERROR = ()

_loggerState = logging.getLogger("vaibify")

I_MAX_OUTPUT_LINES = 500
I_HEARTBEAT_INTERVAL_SECONDS = 5
# Tolerate ~11 missed beats so transient docker-pool contention from
# the parallel badge/poll fan-out doesn't mass-kill healthy long runs.
# A truly dead runner is still reconciled in under a minute.
I_HEARTBEAT_STALE_SECONDS = 60
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
    tBenignErrors = (
        (json.JSONDecodeError, OSError, TypeError, ValueError)
        + _T_DOCKER_API_ERROR
    )
    try:
        iExitCode, sOutput = connectionDocker.ftResultExecuteCommand(
            sContainerId,
            f"cat {S_STATE_PATH} 2>/dev/null",
        )
        if iExitCode != 0 or not sOutput.strip():
            return None
        return json.loads(sOutput)
    except tBenignErrors:
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


def _fnEnsureStateLockForContainer(dictCtx, sContainerId):
    """Lazily allocate a per-container reconciliation lock in dictCtx."""
    dictLocks = dictCtx.setdefault("dictPipelineStateLocks", {})
    if sContainerId not in dictLocks:
        dictLocks[sContainerId] = asyncio.Lock()


def fnEvictStateLockForContainer(dictCtx, sContainerId):
    """Drop a per-container reconciliation lock when the container is gone.

    The lock dict grew without bound across the GUI lifetime — every
    container ever observed leaked an asyncio.Lock. Eviction is safe
    only when no coroutine is currently awaiting the lock; callers
    should invoke this from the same sweep that culls stale entries
    from the running-container snapshot.
    """
    dictLocks = dictCtx.get("dictPipelineStateLocks", {})
    dictLocks.pop(sContainerId, None)


def _fnStampHostIncidentFields(dictReconciled, dictIncident):
    """Copy host-incident details into the reconciled state dict."""
    dictReconciled["sFailureCauseHost"] = (
        dictIncident.get("sExceptionRepr", "")
        or dictIncident.get("sMessage", "")
    )
    dictReconciled["sLastHostIncidentIso"] = dictIncident.get("sIso", "")


def _fdictReconcileStaleHeartbeat(
    dictState, fNow=None, dictIncident=None,
):
    """Return a reconciled copy of state where the runner is declared dead.

    When ``dictIncident`` is supplied (the latest host-side exception
    captured for this container by :mod:`vaibify.gui.hostIncidents`),
    its repr is stamped into ``sFailureCauseHost`` so a container-side
    agent can read the actual cause-of-death out of the state file
    instead of giving up at ``heartbeat_stale (...)``. The active step
    is captured BEFORE the ``fdictBuildCompletedState`` overlay wipes
    ``iActiveStep`` to -1, so the report still names the step that
    was running when the runner died.
    """
    iActiveStepAtDeath = dictState.get("iActiveStep", -1)
    dictReconciled = dict(dictState)
    dictReconciled.update(
        fdictBuildCompletedState(I_EXIT_CODE_RUNNER_DISAPPEARED),
    )
    dictReconciled["sFailureReason"] = fsBuildHeartbeatStaleReason(
        dictState, fNow,
    )
    dictReconciled["iActiveStepAtDeath"] = iActiveStepAtDeath
    if dictIncident:
        _fnStampHostIncidentFields(dictReconciled, dictIncident)
    else:
        dictReconciled.setdefault("sFailureCauseHost", "")
        dictReconciled.setdefault("sLastHostIncidentIso", "")
    return dictReconciled


def _fdictLookupHostIncident(sContainerId):
    """Return the latest host-incident dict for sContainerId, or None.

    Imported lazily so this module stays importable when the incident
    store is unavailable (e.g. narrow unit tests that mock only the
    pipeline-state surface).
    """
    try:
        from vaibify.gui.hostIncidents import (
            fdictLatestIncidentForContainer,
        )
    except ImportError:
        return None
    return fdictLatestIncidentForContainer(sContainerId)


async def fdictReadReconciledState(dictCtx, sContainerId, fNow=None):
    """Read pipeline state and reconcile a vanished runner inline.

    The runner stamps ``sLastHeartbeat`` from a daemon thread; if the
    file still claims ``bRunning: True`` but the heartbeat is older
    than ``I_HEARTBEAT_STALE_SECONDS``, the runner is presumed dead.
    The reconciler flips ``bRunning`` to False, stamps the sentinel
    exit code, plus the latest host-incident (if any) into
    ``sFailureCauseHost``, and writes atomically. Subsequent calls
    observe the already-reconciled file and return it unchanged.
    """
    connectionDocker = dictCtx["docker"]
    _fnEnsureStateLockForContainer(dictCtx, sContainerId)
    lockState = dictCtx["dictPipelineStateLocks"][sContainerId]
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
        dictIncident = _fdictLookupHostIncident(sContainerId)
        dictReconciled = _fdictReconcileStaleHeartbeat(
            dictState, fNow, dictIncident=dictIncident,
        )
        await asyncio.to_thread(
            fnWriteState, connectionDocker, sContainerId, dictReconciled,
        )
        return dictReconciled


# ---------------------------------------------------------------------------
# Single-writer state-write queue (runner-side architecture).
#
# Producers (heartbeat thread, flushing callback, finalize) enqueue
# small mutation closures via the public ``fnEnqueue*`` methods. The
# producer holds the in-memory lock only across the dict.update; the
# writer thread does all docker I/O without touching that lock. This
# eliminates the multi-second pause where a heartbeat could wait
# behind a step-result writing 4 MB of log over a slow docker exec.
#
# Step-result events are debounce-coalesced: at high step rates (1000
# steps in a sweep) emitting one write per result is O(N) writes of an
# O(N)-sized state file, i.e. O(N^2) write volume. The debounce window
# collapses bursts to a single write per ``_F_STEP_RESULT_DEBOUNCE``
# seconds; terminal updates (``bRunning: False``) flush immediately so
# the dashboard's "done" transition is never delayed by a debounce.
# ---------------------------------------------------------------------------

_SENTINEL_WRITE = object()
_SENTINEL_SHUTDOWN = object()
_SENTINEL_FLUSH = object()
_F_STEP_RESULT_DEBOUNCE = 1.0


class StateWriter:
    """Single-writer queue for ``pipeline_state.json`` writes per run.

    Producers call ``fnEnqueueUpdate``/``fnEnqueueStepResult``/etc.,
    which merge into the in-memory ``dictState`` under a short-lived
    lock and then signal the writer thread. The writer thread snapshots
    the state under the same lock and performs the (slow) docker I/O
    outside it, so producers never block on docker.
    """

    def __init__(self, connectionDocker, sContainerId, dictState):
        self.connectionDocker = connectionDocker
        self.sContainerId = sContainerId
        self.dictState = dictState
        self.lockState = threading.Lock()
        self.queueWrites = queue.Queue()
        self.eventStop = threading.Event()
        self.fStepResultDebounce = _F_STEP_RESULT_DEBOUNCE
        self.lockDebounce = threading.Lock()
        self.bStepResultPending = False
        self.timerDebounce = None
        self.threadWriter = threading.Thread(
            target=self._fnRunWriter,
            name=f"vaibify-state-writer-{sContainerId[:8]}",
            daemon=True,
        )

    def fnStart(self):
        """Start the writer thread and persist the initial state."""
        self.threadWriter.start()
        self.queueWrites.put(_SENTINEL_WRITE)

    def fnEnqueueUpdate(self, dictUpdate):
        """Merge ``dictUpdate`` into state and request a persist.

        A terminal transition (``bRunning: False``) is flushed
        immediately so the dashboard's "done" state cannot be delayed
        by a pending step-result debounce window.
        """
        with self.lockState:
            self.dictState.update(dictUpdate)
        if self._fbIsTerminalUpdate(dictUpdate):
            self._fnFlushDebouncedStepResults()
        self.queueWrites.put(_SENTINEL_WRITE)

    def fnEnqueueStepResult(self, dictResult):
        """Record a step result in state and debounce-coalesce the write.

        Bursts of step results inside a single
        ``fStepResultDebounce``-second window produce at most one
        persist. The in-memory state always reflects every result the
        moment this method returns; only the docker write is deferred.
        """
        with self.lockState:
            sKey = str(dictResult["iStepNumber"])
            self.dictState.setdefault("dictStepResults", {})[sKey] = {
                "sStatus": dictResult["sStatus"],
                "iExitCode": dictResult["iExitCode"],
            }
        self._fnArmStepResultDebounce()

    def fnEnqueueOutputLine(self, sLine):
        """Append an output line; no immediate persist (next write coalesces)."""
        with self.lockState:
            fnAppendOutput(self.dictState, sLine)

    def fnStop(self):
        """Signal the writer to drain and exit, then join with no timeout."""
        self.eventStop.set()
        self._fnCancelDebounceTimer()
        self.queueWrites.put(_SENTINEL_SHUTDOWN)
        self.threadWriter.join()

    @staticmethod
    def _fbIsTerminalUpdate(dictUpdate):
        """Return True iff ``dictUpdate`` ends the run (must flush)."""
        if "bRunning" in dictUpdate and not dictUpdate.get("bRunning"):
            return True
        return False

    def _fnArmStepResultDebounce(self):
        """Start (or extend) the debounce timer for step-result flushes."""
        with self.lockDebounce:
            self.bStepResultPending = True
            if self.timerDebounce is not None:
                return
            timerNew = threading.Timer(
                self.fStepResultDebounce,
                self._fnFireStepResultDebounce,
            )
            timerNew.daemon = True
            self.timerDebounce = timerNew
            timerNew.start()

    def _fnFireStepResultDebounce(self):
        """Timer callback: enqueue one persist for the coalesced batch."""
        with self.lockDebounce:
            self.timerDebounce = None
            if not self.bStepResultPending:
                return
            self.bStepResultPending = False
        self.queueWrites.put(_SENTINEL_WRITE)

    def _fnFlushDebouncedStepResults(self):
        """Cancel any pending debounce and enqueue an immediate persist."""
        with self.lockDebounce:
            bWasPending = self.bStepResultPending
            self.bStepResultPending = False
            if self.timerDebounce is not None:
                self.timerDebounce.cancel()
                self.timerDebounce = None
        if bWasPending:
            self.queueWrites.put(_SENTINEL_WRITE)

    def _fnCancelDebounceTimer(self):
        """Stop the debounce timer; pending state survives via dictState."""
        with self.lockDebounce:
            if self.timerDebounce is not None:
                self.timerDebounce.cancel()
                self.timerDebounce = None
            self.bStepResultPending = False

    def _fnRunWriter(self):
        """Consume the queue; coalesce bursts; write each snapshot."""
        while True:
            item = self.queueWrites.get()
            if item is _SENTINEL_SHUTDOWN:
                self._fnFlushPendingWrites()
                return
            self._fnDrainCoalesced()
            self._fnPersistSnapshot()

    def _fnDrainCoalesced(self):
        """Pull any other pending write tokens without blocking."""
        while True:
            try:
                item = self.queueWrites.get_nowait()
            except queue.Empty:
                return
            if item is _SENTINEL_SHUTDOWN:
                self.queueWrites.put(_SENTINEL_SHUTDOWN)
                return

    def _fnFlushPendingWrites(self):
        """On shutdown, write one final snapshot reflecting all updates."""
        self._fnPersistSnapshot()

    def _fnPersistSnapshot(self):
        """Snapshot under lock; persist outside it; log on failure."""
        with self.lockState:
            dictSnapshot = _fdictDeepCopyState(self.dictState)
        try:
            fnWriteState(
                self.connectionDocker, self.sContainerId, dictSnapshot,
            )
        except Exception as error:
            _loggerState.warning(
                "pipeline state write failed: %s", error,
            )


def _fdictDeepCopyState(dictState):
    """Return a snapshot safe to hand to docker I/O without re-entry races."""
    dictSnapshot = dict(dictState)
    dictResults = dictState.get("dictStepResults")
    if isinstance(dictResults, dict):
        dictSnapshot["dictStepResults"] = dict(dictResults)
    listOutput = dictState.get("listRecentOutput")
    if isinstance(listOutput, list):
        dictSnapshot["listRecentOutput"] = list(listOutput)
    return dictSnapshot
