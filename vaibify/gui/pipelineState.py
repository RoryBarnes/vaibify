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
    "fsStatePathFor",
    "fdictBuildInitialState",
    "fdictBuildStepStarted",
    "fdictBuildStepResult",
    "fdictBuildCompletedState",
    "fdictBuildInteractivePauseState",
    "fdictBuildHeartbeatUpdate",
    "fbHeartbeatIsStale",
    "fdictActiveStepBudgetStatus",
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
import posixpath
import queue
import threading
from datetime import datetime, timezone

from ..docker.dockerConnection import fbErrorMeansContainerUnreachable
# The leaf module, not the runner's re-export: importing the
# runner here closes a load-time cycle.
from .pipelineUtils import fsBuildUniqueTemporaryPath, fsShellQuote

_loggerState = logging.getLogger("vaibify")

I_MAX_OUTPUT_LINES = 500
I_HEARTBEAT_INTERVAL_SECONDS = 5
# Tolerate ~11 missed beats so transient docker-pool contention from
# the parallel badge/poll fan-out doesn't mass-kill healthy long runs.
# A truly dead runner is still reconciled in under a minute.
I_HEARTBEAT_STALE_SECONDS = 60
# A reconcile reads and writes pipeline_state.json via one docker exec
# each, under the per-container state lock. Those execs have no native
# timeout, so a hung exec would hold the lock forever and deadlock ALL
# future reconciliation for the container (the runner-death safety net
# is exactly what would then never run). Bounding each exec releases
# the lock and retries next cycle. A state file is tiny; 15s is
# generous.
_F_STATE_IO_TIMEOUT_SECONDS = 15.0
# Sentinel exit code stamped by the poll-side reconciler when the
# runner thread has vanished without writing a final state. Sits
# outside the OS exit-code range (0-255) so callers can distinguish
# a runner crash from any real subprocess exit.
I_EXIT_CODE_RUNNER_DISAPPEARED = -9999
# The container answer, and the DEFAULT rather than the only one:
# a host project's state lives under the directory the researcher
# registered, because /workspace exists on nobody's laptop. The
# constant stays because it is the container's real path and several
# tests and doubles name it; the functions below ask
# :func:`fsStatePathFor` instead of embedding it.
#
# Its ``_TEMP`` companion is GONE. The temp name is per-writer, so no
# constant can name it, and a double that kept keying on the old
# spelling would model a rename the product never performs -- which is
# exactly what one of them did until this was removed.
S_STATE_PATH = "/workspace/.vaibify/pipeline_state.json"
_S_STATE_RELATIVE = ".vaibify/pipeline_state.json"


def fsStatePathFor(sResourceId):
    """Return this resource's pipeline-state path.

    ``posixpath`` for both modes deliberately: host mode is macOS and
    Linux only, where it and ``os.path`` are the same module, and the
    workflow manager composes container and host paths the same way
    for the same reason (see the root AGENTS.md path-module section).
    """
    from .pipelineServer import WORKSPACE_ROOT
    from .projectRoots import fsResolveProjectRoot
    return posixpath.join(
        fsResolveProjectRoot(sResourceId, WORKSPACE_ROOT),
        _S_STATE_RELATIVE,
    )


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
        "sActiveStepStartedIso": "",
        "fActiveStepBudgetSeconds": 0.0,
    }


def fdictBuildStepStarted(iStepNumber, fWallClockBudgetSeconds=0.0):
    """Return a partial update dict for a step starting.

    ``fWallClockBudgetSeconds`` is the resolved per-step wall-clock
    budget (0 = no budget). It is stamped alongside a fresh start
    timestamp so the poll can compute over-budget status live without
    re-reading the workflow. The heartbeat only proves the *runner* is
    alive; the budget is what distinguishes a legitimately long step
    from one that has silently stalled while the daemon heartbeat keeps
    beating.
    """
    return {
        "iActiveStep": iStepNumber,
        "sActiveStepStartedIso": datetime.now(timezone.utc).isoformat(),
        "fActiveStepBudgetSeconds": float(fWallClockBudgetSeconds or 0.0),
    }


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
        "sActiveStepStartedIso": "",
        "fActiveStepBudgetSeconds": 0.0,
    }


def fdictBuildInteractivePauseState(iStepNumber, sStepName):
    """Return a partial update for an interactive pause."""
    return {
        "bRunning": True,
        "bInteractivePause": True,
        "iActiveStep": iStepNumber,
        "sActiveStepName": sStepName,
        # An interactive step waits on a human, not on compute; clear
        # any prior automatic step's budget stamp so a long human pause
        # is never mislabelled "over budget".
        "sActiveStepStartedIso": "",
        "fActiveStepBudgetSeconds": 0.0,
    }


def fnWriteState(connectionDocker, sContainerId, dictState):
    """Write the state dict atomically via temp-then-rename.

    A concurrent reader (badge poll, agent CLI, watchdog reconciler)
    must never observe a half-written JSON document. The temp-file
    plus ``mv`` pattern relies on POSIX rename atomicity within the
    same filesystem so the canonical path either has the previous
    contents or the new contents — never a truncated mix.

    The temp name is unique per WRITER, for the reason state.json's
    is: the run's writer thread and the stale-heartbeat reconciler
    both write this file, and one fixed name lets whichever renames
    first consume the other's temp file. Here the loser said nothing
    at all — the rename's exit code was discarded — so the update
    simply vanished, and a lost terminal update is a pipeline the
    dashboard shows running forever. It is reported now, at the
    volume the callers can act on: this writer is on the run's own
    thread and inside a carrier worker, where raising would poison a
    journal record over a state file.
    """
    sContent = json.dumps(dictState, indent=2)
    sStatePath = fsStatePathFor(sContainerId)
    sTempPath = fsBuildUniqueTemporaryPath(sStatePath)
    sQuotedTempPath = fsShellQuote(sTempPath)
    connectionDocker.fnWriteFile(
        sContainerId, sTempPath, sContent.encode("utf-8")
    )
    iExit, sOutput = connectionDocker.ftResultExecuteCommand(
        sContainerId,
        f"mv {sQuotedTempPath} {fsShellQuote(sStatePath)} || "
        f"{{ iStatus=$?; rm -f {sQuotedTempPath}; exit $iStatus; }}",
    )
    if iExit != 0:
        _loggerState.warning(
            "pipeline state rename to %s failed (exit %d): %s",
            sStatePath, iExit, sOutput,
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


def fdictActiveStepBudgetStatus(dictState, fNowEpoch=None):
    """Return the live over-budget status of the active step.

    A step's *wall-clock budget* is a per-step (or workflow-default)
    ceiling on how long the step may run before the dashboard flags it
    as possibly hung. Unlike the heartbeat — which only proves the
    runner process is alive — the budget makes a genuinely stalled step
    distinguishable from a legitimately long one while the daemon
    heartbeat keeps beating.

    The result is advisory and NON-gating: an over-budget step is still
    running, so ``bRunning`` is untouched and no failure is fabricated.
    The flag only tells the researcher to look. It is computed fresh on
    every poll from the stamped start time and never persisted as
    terminal state, so the dashboard always reflects real elapsed time
    (the dashboard-honesty contract).
    """
    fBudget = _ffCoerceStateBudget(dictState.get("fActiveStepBudgetSeconds"))
    sStartedIso = dictState.get("sActiveStepStartedIso", "")
    dictStatus = {
        "bActiveStepOverBudget": False,
        "fActiveStepBudgetSeconds": fBudget,
        "fActiveStepElapsedSeconds": 0.0,
    }
    if not dictState.get("bRunning") or fBudget <= 0 or not sStartedIso:
        return dictStatus
    try:
        dtStarted = datetime.fromisoformat(sStartedIso)
    except (ValueError, TypeError):
        return dictStatus
    if fNowEpoch is None:
        fNowEpoch = datetime.now(timezone.utc).timestamp()
    fElapsed = max(0.0, fNowEpoch - dtStarted.timestamp())
    dictStatus["fActiveStepElapsedSeconds"] = fElapsed
    dictStatus["bActiveStepOverBudget"] = fElapsed > fBudget
    return dictStatus


def _ffCoerceStateBudget(value):
    """Coerce a persisted budget field to a non-negative float."""
    try:
        fValue = float(value)
    except (TypeError, ValueError):
        return 0.0
    return fValue if fValue > 0 else 0.0


def fdictReadState(connectionDocker, sContainerId):
    """Read the pipeline state from the container, or None.

    A TYPED READ, not a general exec. This used to assemble
    ``cat <path>`` and hand it to the command primitive, which cannot
    tell a read from a delete and therefore treats every one as
    mutating — so on an enforced lane the dashboard's own state read was
    refused unless the route wrapped it in a carrier, and every caller
    inherited that. ``fbaFetchFile`` names a declared read operation and
    the adapter builds the command, so the path can never become
    program text and the read needs no admission.

    Any failure mode — docker daemon hiccup, half-written file
    mid-rename, container down, file absent — degrades to ``None`` so
    callers (badge poll, agent CLI, watchdog) always have a usable
    answer instead of an exception bubbling up to the request handler.
    ``fbaFetchFile`` spells "absent" as ``FileNotFoundError``, which is
    an ``OSError`` and so already lands in that net; a carrier refusal
    is NOT, because ``ControlPlaneRefusalError`` is deliberately not an
    ``OSError``, so a refusal still surfaces loudly. The substrate's own
    errors are recognised by the connection-level predicate rather than
    by naming Docker SDK types here, so a host-mode connection's plain
    ``OSError``\\ s classify identically.
    """
    tBenignErrors = (json.JSONDecodeError, OSError, TypeError, ValueError)
    try:
        baContent = connectionDocker.fbaFetchFile(
            sContainerId, fsStatePathFor(sContainerId),
        )
        if not baContent.strip():
            return None
        return json.loads(baContent)
    except tBenignErrors:
        return None
    except Exception as error:
        if fbErrorMeansContainerUnreachable(error):
            return None
        raise


def fnClearState(connectionDocker, sContainerId):
    """Remove the pipeline state file and any temp file left beside it.

    The temp suffix is a wildcard because the name is per-writer now.
    The glob sits OUTSIDE the quotes deliberately — the state path is
    quoted, so a directory containing a space is still one argument,
    and only the suffix is left for the shell to expand. An
    unmatched pattern reaches ``rm -f``, which is silent about a file
    that is not there.
    """
    sStatePath = fsStatePathFor(sContainerId)
    connectionDocker.ftResultExecuteCommand(
        sContainerId,
        f"rm -f {fsShellQuote(sStatePath)} "
        f"{fsShellQuote(sStatePath)}.*.tmp",
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


async def _fnPersistReconciledOnTheBackgroundLane(
    connectionDocker, sContainerId, dictReconciled,
):
    """Write a reconciled state dict without a carrier admission.

    The default persister, and honest about which lane it is on: the
    status poll and the watchdog reach here from background work that
    opens no carrier, which is the deliberate, named remainder the
    admission gate documents. A caller that IS inside an enforced
    request lane must supply its own carrier-backed persister instead,
    or the write is refused at the primitive.
    """
    await asyncio.wait_for(
        asyncio.to_thread(
            fnWriteState, connectionDocker, sContainerId, dictReconciled,
        ),
        timeout=_F_STATE_IO_TIMEOUT_SECONDS,
    )


async def fdictReadReconciledState(
    dictCtx, sContainerId, fNow=None, fnPersistReconciled=None,
):
    """Read pipeline state and reconcile a vanished runner inline.

    The runner stamps ``sLastHeartbeat`` from a daemon thread; if the
    file still claims ``bRunning: True`` but the heartbeat is older
    than ``I_HEARTBEAT_STALE_SECONDS``, the runner is presumed dead.
    The reconciler flips ``bRunning`` to False, stamps the sentinel
    exit code, plus the latest host-incident (if any) into
    ``sFailureCauseHost``, and writes atomically. Subsequent calls
    observe the already-reconciled file and return it unchanged.

    ``fnPersistReconciled(dictReconciled)`` is awaited instead of the
    default background write when the caller is inside an enforced
    request lane. The READ needs no such treatment — it is a typed read
    — but the reconciling WRITE is a real container mutation, and the
    alternative to letting a request carry it was to have the request
    read non-reconcilingly, which would make Kill report a flat
    "killed (130)" over a runner that had actually died with a recorded
    exit code and ``sFailureCauseHost``. The dashboard states what
    happened; a cheaper reader would have it state something else.

    The persister runs INSIDE the per-container state lock, which is
    what serialises this reconcile against the status poll's. That
    ordering (state lock, then whatever the persister takes) is the
    only one: a carrier worker is synchronous and cannot await this
    coroutine, so nothing can hold a mutation lock while waiting here.
    """
    connectionDocker = dictCtx["docker"]
    _fnEnsureStateLockForContainer(dictCtx, sContainerId)
    lockState = dictCtx["dictPipelineStateLocks"][sContainerId]
    async with lockState:
        try:
            dictState = await asyncio.wait_for(
                asyncio.to_thread(
                    fdictReadState, connectionDocker, sContainerId,
                ),
                timeout=_F_STATE_IO_TIMEOUT_SECONDS,
            )
        except asyncio.TimeoutError:
            # A hung state read must not hold the reconcile lock
            # forever — that deadlocks every future reconciliation for
            # this container, so a dead runner would stay bRunning=True
            # indefinitely. Bail this cycle (lock released on return);
            # the next poll retries.
            _loggerState.warning(
                "pipeline-state read timed out for container=%s; "
                "skipping reconcile this cycle", sContainerId,
            )
            return None
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
        try:
            if fnPersistReconciled is None:
                await _fnPersistReconciledOnTheBackgroundLane(
                    connectionDocker, sContainerId, dictReconciled,
                )
            else:
                await asyncio.wait_for(
                    fnPersistReconciled(dictReconciled),
                    timeout=_F_STATE_IO_TIMEOUT_SECONDS,
                )
        except asyncio.TimeoutError:
            # The in-memory reconciliation is correct and returned to
            # the caller; persistence retries next cycle rather than
            # holding the lock on a hung write.
            _loggerState.warning(
                "pipeline-state write timed out for container=%s; "
                "reconcile applied in memory, persist next cycle",
                sContainerId,
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
