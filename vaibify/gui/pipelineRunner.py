"""Execute workflow steps by running commands directly in containers."""

import asyncio
import contextlib
import logging
import os
import posixpath
import threading
import time

from . import pipelineState
from . import workflowManager

__all__ = [
    "fdictMapOutputTokenStems",
    "fsShellQuote",
    "fnRunAllSteps",
    "fnRunFromStep",
    "fnRunSelectedSteps",
    "fnVerifyOnly",
    "fnRunAllTests",
    "fsGenerateLogFilename",
    "fdictCreateInteractiveContext",
    "fnSetInteractiveResponse",
    "fsLabelFromStepIndex",
    "fnClearOutputModifiedFlags",
    "ffBuildLoggingCallback",
    "fnWriteLogToContainer",
    "SET_VALID_RUN_MODES",
]

SET_VALID_RUN_MODES = {"full", "dataOnly", "plotsOnly"}

# ---------------------------------------------------------------------------
# Re-exports from pipelineUtils (true leaf — breaks circular imports).
# ---------------------------------------------------------------------------

from .pipelineUtils import (  # noqa: F401
    fdictMapOutputTokenStems,
    fsShellQuote,
    fsLabelFromStepIndex,
    fiStepIndexFromLabel,
    flistStepsWithLabels,
    fdictWorkflowWithLabels,
    fdictStepWithLabel,
    fnAttachStepLabels,
    _fnRecordRunStats,
    _fdictBuildWorkflowVars,
    fnClearOutputModifiedFlags,
    _fnEmitCommandHeader,
    _fnEmitStepResult,
    _fnEmitCompletion,
    _fnEmitBanner,
)

# ---------------------------------------------------------------------------
# Re-exports from extracted modules so existing imports keep working.
# ---------------------------------------------------------------------------

from .pipelineValidator import (  # noqa: F401
    _fnValidateStepDirectory,
    _fnValidateStepCommands,
    _fnValidateSingleCommand,
    _fsExtractScriptPath,
    _fiReportPreflightFailure,
)

from .pipelineLogger import (  # noqa: F401
    ffBuildLoggingCallback,
    _fsExtractLogLine,
    fnWriteLogToContainer,
    _fnEnsureLogsDirectory,
    fsGenerateLogFilename,
    fnPruneOldLogs,
    I_MAX_LOG_LINES,
    I_LOG_BYTE_BUDGET,
    I_LOG_LINE_BYTE_CAP,
    I_LOG_RETENTION_COUNT,
    _ffBuildFlushingCallback,
    _fnUpdatePipelineState,
    _fnSaveWorkflowStats,
    _fnFinalizeRun,
)

from .pipelineTestRunner import (  # noqa: F401
    _fiRunTestCommands,
    _fiAggregateTestExitCode,
    _flistCollectCategoryLogs,
    _fnWriteTestLog,
    _flistResolveTestCommands,
    fnRunAllTests,
)

from .interactiveSteps import (  # noqa: F401
    fdictCreateInteractiveContext,
    fnSetInteractiveResponse,
)

# ---------------------------------------------------------------------------
# Preflight validation (kept here for mockability via module namespace).
# ---------------------------------------------------------------------------

async def _flistPreflightValidate(
    connectionDocker, sContainerId, dictWorkflow, dictVariables,
    iStartStep=1, setRunStepIndices=None,
):
    """Return preflight errors (hard-blocks). Soft warnings flow separately."""
    listErrors = []
    for iIndex, dictStep in enumerate(dictWorkflow["listSteps"]):
        iStepNumber = iIndex + 1
        if not _fbStepIncludedInRun(
            dictStep, iIndex, setRunStepIndices,
        ):
            continue
        if iStepNumber < iStartStep:
            continue
        sStepDir = workflowManager.fsResolveStepWorkdir(
            dictStep.get("sDirectory", ""), dictVariables,
        )
        _fnValidateStepDirectory(
            connectionDocker, sContainerId, sStepDir,
            iStepNumber, dictStep["sName"], listErrors,
        )
        _fnValidateStepCommands(
            connectionDocker, sContainerId, dictStep,
            sStepDir, dictVariables, iStepNumber, listErrors,
        )
    return listErrors


def _flistCollectPreflightWarnings(
    connectionDocker, sContainerId, dictWorkflow,
):
    """Return soft preflight warnings — never blocks the run.

    Low disk space surfaces here so the run still starts. The runner
    emits each warning as a ``preflightWarning`` status event before
    executing steps. A negative probe (df unavailable) returns ``[]``.
    """
    from . import diskSpace
    listWarnings = []
    iEstimatedBytes = int(dictWorkflow.get("iEstimatedOutputBytes", 0) or 0)
    dictWarning = diskSpace.fdictAssertSpaceForOutputs(
        connectionDocker, sContainerId, iEstimatedBytes,
    )
    if dictWarning is not None:
        listWarnings.append(dictWarning["sMessage"])
    return listWarnings


# ---------------------------------------------------------------------------
# Variable resolution
# ---------------------------------------------------------------------------

def _fdictBuildVariables(dictWorkflow, sWorkdir):
    """Build merged global + step variable dict for resolution."""
    dictGlobalVars = workflowManager.fdictBuildGlobalVariables(
        dictWorkflow, sWorkdir
    )
    dictStepVars = workflowManager.fdictBuildStepVariables(
        dictWorkflow, dictGlobalVars
    )
    dictMerged = dict(dictGlobalVars)
    dictMerged.update(dictStepVars)
    return dictMerged


# ---------------------------------------------------------------------------
# Determinism: SOURCE_DATE_EPOCH injection (matplotlib + reproducible builds)
# ---------------------------------------------------------------------------

S_ENV_PREFIX_KEY = "__sEnvPrefix"


async def _fiQueryHeadCommitEpoch(
    connectionDocker, sContainerId, sProjectRepoPath,
):
    """Return HEAD commit epoch as int, or 0 if unavailable."""
    if not sProjectRepoPath:
        return 0
    sCommand = (
        f"git -C {fsShellQuote(sProjectRepoPath)} "
        f"log -1 --format=%ct HEAD 2>/dev/null"
    )
    iExitCode, sOutput = await asyncio.to_thread(
        connectionDocker.ftResultExecuteCommand,
        sContainerId, sCommand,
    )
    if iExitCode != 0:
        return 0
    try:
        return int(sOutput.strip())
    except ValueError:
        return 0


async def _fsBuildDeterminismEnvPrefix(
    connectionDocker, sContainerId, sProjectRepoPath,
):
    """Return shell prefix that exports SOURCE_DATE_EPOCH for the run.

    The value is the project-repo HEAD commit epoch, so identical
    source produces byte-stable matplotlib PDFs across reruns.
    Returns empty string if the epoch cannot be determined; callers
    must not block step execution on the result.
    """
    iEpoch = await _fiQueryHeadCommitEpoch(
        connectionDocker, sContainerId, sProjectRepoPath,
    )
    if iEpoch <= 0:
        return ""
    return f"export SOURCE_DATE_EPOCH={iEpoch} && "


async def _fnInjectDeterminismEnvPrefix(
    connectionDocker, sContainerId, dictWorkflow, dictVariables,
):
    """Compute the env prefix once and stash it in dictVariables.

    Bundles the determinism prefix with a
    ``VAIBIFY_ACTIVE_WORKFLOW_SLUG`` export so the marker conftest
    namespaces writes under the active workflow when commands flow
    through ``_ftRunCommandList`` (e.g. the runAllTests path).
    """
    from .fileStatusManager import fsWorkflowSlugFromPath
    sProjectRepoPath = dictWorkflow.get("sProjectRepoPath", "")
    sEnvPrefix = await _fsBuildDeterminismEnvPrefix(
        connectionDocker, sContainerId, sProjectRepoPath,
    )
    sWorkflowSlug = fsWorkflowSlugFromPath(
        dictWorkflow.get("sPath", ""),
    )
    if sWorkflowSlug:
        sEnvPrefix += (
            "export VAIBIFY_ACTIVE_WORKFLOW_SLUG="
            + fsShellQuote(sWorkflowSlug) + " && "
        )
    dictVariables[S_ENV_PREFIX_KEY] = sEnvPrefix


# ---------------------------------------------------------------------------
# Core command execution
# ---------------------------------------------------------------------------

async def _ftRunCommandList(
    connectionDocker, sContainerId, listCommands,
    sWorkdir, dictVariables, fnStatusCallback,
):
    """Execute commands, return (iExitCode, fTotalCpuSeconds)."""
    fTotalCpu = 0.0
    sEnvPrefix = ""
    if dictVariables:
        sEnvPrefix = dictVariables.get(S_ENV_PREFIX_KEY, "")
    for sCommand in listCommands:
        sResolved = workflowManager.fsResolveCommand(
            sCommand, dictVariables
        )
        iExitCode, fCpu = await _ftRunSingleCommand(
            connectionDocker, sContainerId,
            sCommand, sResolved, sWorkdir, fnStatusCallback,
            sEnvPrefix=sEnvPrefix,
        )
        fTotalCpu += fCpu
        if iExitCode != 0:
            return (iExitCode, fTotalCpu)
    return (0, fTotalCpu)


async def _ftRunSingleCommand(
    connectionDocker, sContainerId,
    sOriginal, sResolved, sWorkdir, fnStatusCallback,
    sEnvPrefix="",
):
    """Execute one command, return (iExitCode, fCpuSeconds).

    Output is streamed line-by-line via the docker-py low-level exec
    API; ``fnStatusCallback`` receives ``{"sType":"outputBatch",...}``
    events (with per-line fallbacks for non-line traffic) as the
    command produces them, so the in-container ``vaibify-do`` WebSocket
    sees traffic throughout the run without paying per-line frame
    overhead on chatty runs.
    """
    await _fnEmitCommandHeader(
        fnStatusCallback, sOriginal, sResolved
    )
    sTimedCmd = _fsWrapWithTime(sEnvPrefix + sResolved)
    loopMain = asyncio.get_running_loop()
    dictAccum = {"fCpu": 0.0}
    fnEmitChunk, faDrainPending = _ftBuildBatchingEmitter(
        fnStatusCallback, loopMain, dictAccum,
    )
    async with _actxWebSocketHeartbeat(fnStatusCallback):
        try:
            resultExec = await asyncio.to_thread(
                connectionDocker.texecRunInContainerStreamedWithChunks,
                sContainerId, sTimedCmd, fnEmitChunk,
                sWorkdir=sWorkdir,
            )
        finally:
            await faDrainPending()
    if resultExec.iExitCode != 0:
        await fnStatusCallback({
            "sType": "commandFailed",
            "sCommand": sResolved,
            "sDirectory": sWorkdir,
            "iExitCode": resultExec.iExitCode,
        })
    return (resultExec.iExitCode, dictAccum["fCpu"])


# Coalescing thresholds for the streaming chunk emitter. A subprocess
# emitting at ~100 lines/sec previously produced ~360k WS frames per
# hour; batching collapses that into ~36k while still keeping the
# 100 ms upper bound on perceived latency.
I_BATCH_MAX_LINES = 50
F_BATCH_MAX_INTERVAL_SECONDS = 0.1


def _ftBuildBatchingEmitter(fnStatusCallback, loopMain, dictAccum):
    """Build a ``(fnEmitChunk, faDrainPending)`` pair that coalesces lines.

    Lines arriving on the worker thread are accumulated into a buffer
    and flushed as a single ``{"sType": "outputBatch", "listLines":
    [...]}`` event when EITHER the buffer reaches ``I_BATCH_MAX_LINES``
    OR a ``loop.call_later`` timer fires ``F_BATCH_MAX_INTERVAL_SECONDS``
    after the first un-flushed line. The timer guarantees a flush even
    when the producer goes idle after one line — without it the buffer
    sat until the next line arrived or the per-command teardown drain
    fired, which on a sporadic chatty step delayed dashboard output by
    seconds.

    ``faDrainPending`` is an async coroutine that empties the buffer on
    per-command teardown so no lines are stuck after the docker exec
    returns; it must be ``await``-ed from the event-loop thread (the
    same thread that runs the docker ``asyncio.to_thread`` await).
    Errors forwarding a batch (WS closed, MemoryError on a huge
    scientific line, asyncio cancellation) are logged exactly once;
    subsequent chunks become no-ops so the producer worker finishes
    the docker exec instead of tearing the whole run down.
    """
    dictBatch = {
        "listLines": [], "fFirstLineAt": 0.0, "bDisabled": False,
        "handleTimer": None,
    }
    lockBuffer = threading.Lock()

    def fnEmitChunk(sStream, sLine):
        if sLine.startswith("__VAIBIFY_CPU__ "):
            dictAccum["fCpu"] = _fParseCpuTime(sLine)
            return
        if dictBatch["bDisabled"]:
            return
        listToSend, bFirstLine = _flistAppendAndMaybeDrainBatch(
            dictBatch, lockBuffer, sLine,
        )
        if listToSend:
            _fnFlushBatchFromWorker(
                dictBatch, fnStatusCallback, loopMain, listToSend,
            )
        elif bFirstLine:
            _fnScheduleTimerFlush(
                dictBatch, lockBuffer, fnStatusCallback, loopMain,
            )

    async def faDrainPending():
        _fnCancelTimerFlush(dictBatch)
        with lockBuffer:
            listToSend = dictBatch["listLines"]
            dictBatch["listLines"] = []
            dictBatch["fFirstLineAt"] = 0.0
        if not listToSend or dictBatch["bDisabled"]:
            return
        await _faFlushBatchFromLoop(
            dictBatch, fnStatusCallback, listToSend,
        )

    return fnEmitChunk, faDrainPending


def _fnScheduleTimerFlush(
    dictBatch, lockBuffer, fnStatusCallback, loopMain,
):
    """Arm a ``call_later`` on the loop to drain the buffer after the window.

    Runs in the worker thread; uses ``call_soon_threadsafe`` to hand
    the scheduling itself to the event loop thread so ``call_later``
    sees a consistent loop state.
    """
    def fnArm():
        if dictBatch["handleTimer"] is not None:
            return
        dictBatch["handleTimer"] = loopMain.call_later(
            F_BATCH_MAX_INTERVAL_SECONDS,
            lambda: loopMain.create_task(_faTimerFlush(
                dictBatch, lockBuffer, fnStatusCallback,
            )),
        )
    loopMain.call_soon_threadsafe(fnArm)


def _fnCancelTimerFlush(dictBatch):
    """Cancel any pending timer so a manual flush isn't followed by a stale one."""
    handleTimer = dictBatch.get("handleTimer")
    if handleTimer is not None:
        try:
            handleTimer.cancel()
        except Exception:
            pass
        dictBatch["handleTimer"] = None


async def _faTimerFlush(dictBatch, lockBuffer, fnStatusCallback):
    """Loop-side timer callback: drain whatever has accumulated."""
    dictBatch["handleTimer"] = None
    with lockBuffer:
        listToSend = dictBatch["listLines"]
        dictBatch["listLines"] = []
        dictBatch["fFirstLineAt"] = 0.0
    if not listToSend or dictBatch["bDisabled"]:
        return
    await _faFlushBatchFromLoop(
        dictBatch, fnStatusCallback, listToSend,
    )


def _flistAppendAndMaybeDrainBatch(dictBatch, lockBuffer, sLine):
    """Append ``sLine``; return ``(drained_lines, bFirstLine)``.

    ``bFirstLine`` is True when this call started a fresh batch — the
    caller arms the timer-driven flush only on that transition. Any
    size- or stale-time-triggered drain returns a non-empty list and
    the caller cancels the timer separately.
    """
    with lockBuffer:
        bFirstLine = not dictBatch["listLines"]
        if bFirstLine:
            dictBatch["fFirstLineAt"] = time.monotonic()
        dictBatch["listLines"].append(sLine)
        fElapsed = time.monotonic() - dictBatch["fFirstLineAt"]
        bSizeReached = (
            len(dictBatch["listLines"]) >= I_BATCH_MAX_LINES
        )
        bTimeReached = fElapsed >= F_BATCH_MAX_INTERVAL_SECONDS
        if not (bSizeReached or bTimeReached):
            return [], bFirstLine
        listDrained = dictBatch["listLines"]
        dictBatch["listLines"] = []
        dictBatch["fFirstLineAt"] = 0.0
        return listDrained, bFirstLine


def _fnFlushBatchFromWorker(
    dictBatch, fnStatusCallback, loopMain, listLines,
):
    """Ship one batch via ``run_coroutine_threadsafe``; disable on error.

    Called from the docker-py streaming worker thread, NOT the event
    loop thread; ``future.result()`` is therefore safe (it blocks the
    worker while the loop services the coroutine).
    """
    try:
        future = asyncio.run_coroutine_threadsafe(
            fnStatusCallback(
                {"sType": "outputBatch", "listLines": listLines}
            ),
            loopMain,
        )
        future.result()
    except BaseException as error:
        dictBatch["bDisabled"] = True
        logging.getLogger("vaibify").warning(
            "streaming chunk emitter disabled after error: %s",
            error,
        )


async def _faFlushBatchFromLoop(
    dictBatch, fnStatusCallback, listLines,
):
    """Await one batch directly on the event-loop thread.

    The teardown path runs on the same loop that the docker
    ``asyncio.to_thread`` was awaited on, so we must NOT use
    ``run_coroutine_threadsafe`` + ``.result()`` (which would
    deadlock the loop). Awaiting the coroutine in place is the safe
    equivalent.
    """
    try:
        await fnStatusCallback(
            {"sType": "outputBatch", "listLines": listLines}
        )
    except BaseException as error:
        dictBatch["bDisabled"] = True
        logging.getLogger("vaibify").warning(
            "streaming chunk emitter disabled after error: %s",
            error,
        )


def _ffBuildStreamingChunkEmitter(fnStatusCallback, loopMain, dictAccum):
    """Backwards-compatible shim that flushes after every line.

    Production callers should use :func:`_ftBuildBatchingEmitter` to
    benefit from the per-100ms / 50-line coalescing and the matching
    ``faDrainPending`` async teardown. This shim exists for callers
    that imported the symbol directly and treat the emitter as a
    one-line-at-a-time forwarder; each emit triggers an immediate
    batch-of-one flush so the dispatched event shape matches the
    production contract (``outputBatch`` with ``listLines``). The
    shim assumes ``loopMain`` is running on a different thread than
    the caller, which is always true for the docker streaming worker
    callbacks and for the legacy emitter tests.
    """
    dictBatch = {"bDisabled": False}

    def fnEmitOne(sStream, sLine):
        if sLine.startswith("__VAIBIFY_CPU__ "):
            dictAccum["fCpu"] = _fParseCpuTime(sLine)
            return
        if dictBatch["bDisabled"]:
            return
        _fnFlushBatchFromWorker(
            dictBatch, fnStatusCallback, loopMain, [sLine],
        )

    return fnEmitOne


# Interval in seconds between server-emitted ``wsHeartbeat`` frames
# during a single command. Tuned for the default 60 s F_READ_TIMEOUT on
# the vaibify-do client; tests monkeypatch this constant to drive the
# loop in a fraction of a second.
F_WS_HEARTBEAT_INTERVAL = 15.0


@contextlib.asynccontextmanager
async def _actxWebSocketHeartbeat(fnStatusCallback):
    """Emit ``wsHeartbeat`` events on the WS while a command runs.

    Keeps the in-container ``vaibify-do`` socket's per-recv inactivity
    timer reset across multi-minute blocking commands without coupling
    to the underlying docker exec call.
    """
    taskBeat = asyncio.create_task(
        _fnEmitHeartbeatLoop(fnStatusCallback)
    )
    try:
        yield
    finally:
        taskBeat.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await taskBeat


async def _fnEmitHeartbeatLoop(fnStatusCallback):
    """Loop emitting ``wsHeartbeat`` events until cancelled.

    A failed send (closed socket, transient back-pressure exception)
    is logged once and the loop continues. The previous behaviour was
    to ``return`` on first exception, which permanently disabled
    keep-alives for the rest of the command and let the
    ``vaibify-do`` client's per-recv timer fire on long blocking
    docker execs.
    """
    while True:
        await asyncio.sleep(F_WS_HEARTBEAT_INTERVAL)
        try:
            await fnStatusCallback(
                {"sType": "wsHeartbeat", "fEpoch": time.time()}
            )
        except asyncio.CancelledError:
            raise
        except Exception as error:
            logging.getLogger("vaibify").warning(
                "ws heartbeat emit failed (continuing): %s", error,
            )


def _fsWrapWithTime(sCommand):
    """Wrap a command with /usr/bin/time to capture CPU usage."""
    return (
        f"{{ if [ -x /usr/bin/time ]; then "
        f"/usr/bin/time -f '__VAIBIFY_CPU__ %U %S' "
        f"{sCommand}; else {sCommand}; fi; }} 2>&1"
    )


def _fParseCpuTime(sOutput):
    """Extract user+system CPU seconds from time output."""
    for sLine in sOutput.splitlines():
        if sLine.startswith("__VAIBIFY_CPU__ "):
            listParts = sLine.split()
            try:
                fUser = float(listParts[1])
                fSystem = float(listParts[2])
                return fUser + fSystem
            except (IndexError, ValueError):
                pass
    return 0.0


# ---------------------------------------------------------------------------
# Step running helpers
# ---------------------------------------------------------------------------

async def fiRunStepCommands(
    connectionDocker, sContainerId, dictStep,
    sWorkdir, dictVariables, fnStatusCallback,
    iStepNumber=0, sRunMode="full",
):
    """Run a single step's commands sequentially in its directory.

    ``sRunMode`` gates which sections execute:
    ``full`` (default) runs data, tests, then plots; ``dataOnly``
    runs data only; ``plotsOnly`` runs plots only.
    """
    from .pipelineTestRunner import _fiRunTestCommands

    sStepDirectory = workflowManager.fsResolveStepWorkdir(
        dictStep.get("sDirectory", sWorkdir), dictVariables,
    )
    sPlotDirectory = dictVariables.get("sPlotDirectory", "Plot")
    await asyncio.to_thread(
        connectionDocker.ftResultExecuteCommand,
        sContainerId,
        f"mkdir -p {fsShellQuote(sPlotDirectory)}",
    )
    iExitCode, fCpuTime = 0, 0.0
    if sRunMode != "plotsOnly":
        iExitCode, fCpuTime = await _fiRunSetupIfNeeded(
            connectionDocker, sContainerId, dictStep,
            sStepDirectory, dictVariables, fnStatusCallback,
        )
        if iExitCode != 0:
            return (iExitCode, fCpuTime)
        await _fiRunTestCommands(
            connectionDocker, sContainerId, dictStep,
            sStepDirectory, dictVariables, fnStatusCallback,
            iStepNumber,
        )
    if sRunMode == "dataOnly":
        return (iExitCode, fCpuTime)
    iPlotExit, fPlotCpu = await _ftRunCommandList(
        connectionDocker, sContainerId,
        dictStep.get("saPlotCommands", []),
        sStepDirectory, dictVariables, fnStatusCallback,
    )
    return (iPlotExit, fCpuTime + fPlotCpu)


async def _fiRunSetupIfNeeded(
    connectionDocker, sContainerId, dictStep,
    sStepDirectory, dictVariables, fnStatusCallback,
):
    """Run data analysis commands unless bPlotOnly is True."""
    if dictStep.get("bPlotOnly", False):
        return (0, 0.0)
    return await _ftRunCommandList(
        connectionDocker, sContainerId,
        dictStep.get("saDataCommands", []),
        sStepDirectory, dictVariables, fnStatusCallback,
    )


# ---------------------------------------------------------------------------
# Output discovery and verification
# ---------------------------------------------------------------------------

_I_DISCOVERY_DEFAULT_MAX_DEPTH = 1
_I_DISCOVERY_MAX_FILES = 5


def _fiDiscoveryMaxDepthForStep(dictStep):
    """Return the snapshot recursion depth for a step (override or default)."""
    iDepth = dictStep.get("iDiscoveryMaxDepth")
    if isinstance(iDepth, int) and iDepth > 0:
        return iDepth
    return _I_DISCOVERY_DEFAULT_MAX_DEPTH


async def _fsetSnapshotDirectory(
    connectionDocker, sContainerId, sDirectory, iMaxDepth,
):
    """Return a set of file paths up to ``iMaxDepth`` levels deep."""
    iExit, sOutput = await asyncio.to_thread(
        connectionDocker.ftResultExecuteCommand,
        sContainerId,
        f"find {fsShellQuote(sDirectory)} -maxdepth {iMaxDepth} "
        f"-type f 2>/dev/null",
    )
    if iExit != 0 or not sOutput.strip():
        return set()
    return set(sOutput.strip().splitlines())


def _flistFilterUnexpectedFiles(setNewFiles, sDirectory, dictStep):
    """Return list of unexpected output file dicts from new files."""
    setExpected = set()
    for sKey in ("saDataFiles", "saPlotFiles"):
        for sFile in dictStep.get(sKey, []):
            setExpected.add(sFile)
    listUnexpected = []
    for sFile in sorted(setNewFiles):
        sRelative = sFile
        if sFile.startswith(sDirectory + "/"):
            sRelative = sFile[len(sDirectory) + 1:]
        bExpected = sRelative in setExpected or sFile in setExpected
        if not bExpected:
            listUnexpected.append({
                "sFilePath": sRelative,
                "bExpected": False,
            })
    return listUnexpected


def _ftCapDiscoveredFiles(listUnexpected):
    """Return ``(listCapped, iTotal)`` slicing to ``_I_DISCOVERY_MAX_FILES``."""
    iTotal = len(listUnexpected)
    return listUnexpected[:_I_DISCOVERY_MAX_FILES], iTotal


async def _fnEmitDiscoveredOutputs(
    connectionDocker, sContainerId, sDirectory,
    setFilesBefore, dictStep, iStepNumber, fnStatusCallback,
):
    """Diff directory and emit discovered output files (capped for UI)."""
    iMaxDepth = _fiDiscoveryMaxDepthForStep(dictStep)
    setFilesAfter = await _fsetSnapshotDirectory(
        connectionDocker, sContainerId, sDirectory, iMaxDepth,
    )
    setNewFiles = setFilesAfter - setFilesBefore
    if not setNewFiles:
        return
    listUnexpected = _flistFilterUnexpectedFiles(
        setNewFiles, sDirectory, dictStep)
    if not listUnexpected:
        return
    listCapped, iTotal = _ftCapDiscoveredFiles(listUnexpected)
    await fnStatusCallback({
        "sType": "discoveredOutputs",
        "iStepNumber": iStepNumber,
        "listDiscovered": listCapped,
        "iTotalDiscovered": iTotal,
    })


_S_VERIFY_PATHFILE_PREFIX = "/tmp/vaibifyVerify."
# A per-step output-existence check is one cheap exec; 30s is generous.
# The bound exists so a stalled docker exec surfaces as a loud
# unverified result instead of an invisible, unbounded hang.
_F_VERIFY_STEP_EXEC_TIMEOUT_SECONDS = 30.0


def _fsVerifyPathfileForCall():
    """Return a per-call temp pathfile so concurrent verifies don't clobber.

    Two interleaved ``_fbVerifyStepList`` calls (badge-driven L3 +
    user-triggered verify, or two parallel step runs) used to share a
    single ``/tmp/vaibifyVerify.list``: the second write truncated the
    first between write and read, producing false "missing" reports.
    A uuid suffix per call keeps each invocation's pathfile distinct
    and ``rm -f`` cleans up afterward.
    """
    import uuid
    return f"{_S_VERIFY_PATHFILE_PREFIX}{uuid.uuid4().hex}.list"


async def _fbVerifyStepOutputs(
    connectionDocker, sContainerId,
    dictStep, dictVars, sWorkdir, fnStatusCallback,
):
    """Return True if every output file for a step exists; one exec total.

    Writes the absolute paths into a temp file in the container, then
    runs a single ``xargs`` invocation that prints each path that
    exists. The set difference identifies missing files. Replaces the
    prior pattern of one ``test -f`` exec per file (3000+ execs for a
    1000-step × 3-output workflow).
    """
    sStepDirectory = workflowManager.fsResolveStepWorkdir(
        dictStep.get("sDirectory", sWorkdir), dictVars,
    )
    listAbsolutePaths = _flistBuildStepOutputAbsPaths(
        dictStep, dictVars, sStepDirectory,
    )
    if not listAbsolutePaths:
        return True
    try:
        setMissing = await asyncio.wait_for(
            asyncio.to_thread(
                _fsetMissingPathsBatched,
                connectionDocker, sContainerId, listAbsolutePaths,
            ),
            timeout=_F_VERIFY_STEP_EXEC_TIMEOUT_SECONDS,
        )
    except asyncio.TimeoutError:
        # A stalled container exec must NEVER hang the whole verify
        # forever (the reported 2h33m no-completion incident). Report
        # the step as unverifiable and let verify finish and emit its
        # completion, exactly as the OSError degradation does.
        await fnStatusCallback({
            "sType": "output",
            "sLine": (
                "Verification timed out checking this step's outputs "
                "after "
                + str(int(_F_VERIFY_STEP_EXEC_TIMEOUT_SECONDS))
                + "s — reporting it unverified rather than hanging."
            ),
        })
        return False
    if setMissing:
        sFirstMissing = next(
            sPath for sPath in listAbsolutePaths if sPath in setMissing
        )
        await fnStatusCallback(
            {"sType": "output", "sLine": f"Missing: {sFirstMissing}"}
        )
        return False
    return True


def _flistBuildStepOutputAbsPaths(dictStep, dictVars, sStepDirectory):
    """Resolve a step's output files into absolute container paths."""
    listOutputFiles = (
        dictStep.get("saPlotFiles", [])
        + dictStep.get("saDataFiles", [])
    )
    listAbsolute = []
    for sOutputFile in listOutputFiles:
        sResolved = workflowManager.fsResolveVariables(
            sOutputFile, dictVars,
        )
        listAbsolute.append(_fsAbsoluteWithinStepDir(
            sResolved, sStepDirectory,
        ))
    return listAbsolute


def _fsAbsoluteWithinStepDir(sPath, sStepDirectory):
    """Return sPath rooted at sStepDirectory when it is repo-relative."""
    if sPath.startswith("/"):
        return sPath
    if not sStepDirectory:
        return sPath
    return posixpath.normpath(posixpath.join(sStepDirectory, sPath))


def _fsetMissingPathsBatched(
    connectionDocker, sContainerId, listAbsolutePaths,
):
    """Return the set of absolute paths absent from the container.

    Mirrors ``fileStatusManager._fdictStatViaPathfile``: writes the
    path list into ``_S_VERIFY_PATHFILE`` via tar, then runs one
    ``xargs`` that prints each path that exists. The host parses the
    output and diffs against the requested list to find the missing
    ones. A failed write or exec degrades to "all missing" so a broken
    container fails loud rather than silently passing verification.
    """
    baContent = ("\n".join(listAbsolutePaths) + "\n").encode("utf-8")
    sPathfile = _fsVerifyPathfileForCall()
    try:
        try:
            _fnWriteVerifyPathfile(
                connectionDocker, sContainerId, sPathfile, baContent,
            )
            _iExit, sOutput = connectionDocker.ftResultExecuteCommand(
                sContainerId,
                "xargs -d '\\n' -a " + sPathfile
                + " -I{} sh -c 'test -e \"$1\" && printf %s\\\\n \"$1\"'"
                " _ {} 2>/dev/null",
            )
        except OSError:
            return set(listAbsolutePaths)
    finally:
        _fnRemoveVerifyPathfile(connectionDocker, sContainerId, sPathfile)
    setPresent = {
        sLine.strip() for sLine in (sOutput or "").splitlines()
        if sLine.strip()
    }
    return {sPath for sPath in listAbsolutePaths if sPath not in setPresent}


def _fnWriteVerifyPathfile(
    connectionDocker, sContainerId, sPathfile, baContent,
):
    """Write the temp pathfile via the preferred docker write helper."""
    fnWriter = getattr(connectionDocker, "fnWriteFileViaTar", None)
    if fnWriter is None:
        fnWriter = connectionDocker.fnWriteFile
    fnWriter(sContainerId, sPathfile, baContent)


def _fnRemoveVerifyPathfile(connectionDocker, sContainerId, sPathfile):
    """Best-effort cleanup of the per-call verify pathfile."""
    try:
        connectionDocker.ftResultExecuteCommand(
            sContainerId, f"rm -f {sPathfile}",
        )
    except Exception:
        pass


async def _fbVerifyStepList(
    connectionDocker, sContainerId, dictWorkflow,
    sWorkdir, fnStatusCallback,
):
    """Verify outputs for every step, returning True if all present."""
    dictVars = _fdictBuildWorkflowVars(dictWorkflow)
    bAllPresent = True
    for iIndex, dictStep in enumerate(dictWorkflow["listSteps"]):
        bStepOk = await _fbVerifyStepOutputs(
            connectionDocker, sContainerId,
            dictStep, dictVars, sWorkdir, fnStatusCallback,
        )
        await _fnEmitStepResult(
            fnStatusCallback, iIndex + 1, 0 if bStepOk else 1
        )
        if not bStepOk:
            bAllPresent = False
    return bAllPresent


# ---------------------------------------------------------------------------
# Dependency and skip checks
# ---------------------------------------------------------------------------

def _fbShouldRunStep(
    dictStep, iStepNumber, iStartStep, setRunStepIndices=None,
):
    """Return True if this step should be executed.

    When ``setRunStepIndices`` is supplied (non-None), only those
    0-based indices run; ``bRunEnabled`` is ignored for that run.
    Otherwise the step's persisted ``bRunEnabled`` flag controls
    inclusion.
    """
    if iStepNumber < iStartStep:
        return False
    return _fbStepIncludedInRun(
        dictStep, iStepNumber - 1, setRunStepIndices,
    )


def _fbStepIncludedInRun(dictStep, iIndex, setRunStepIndices):
    """Return True when this step is in scope for the current run."""
    if setRunStepIndices is not None:
        return iIndex in setRunStepIndices
    return dictStep.get("bRunEnabled", True)


async def _fsMissingDependencyFile(
    connectionDocker, sContainerId, dictStep, dictVariables,
):
    """Return the first missing dependency path, or empty string."""
    import re
    listAllCommands = (
        dictStep.get("saDataCommands", [])
        + dictStep.get("saPlotCommands", [])
        + dictStep.get("saSetupCommands", [])
        + dictStep.get("saCommands", [])
    )
    setChecked = set()
    for sCommand in listAllCommands:
        for sMatch in re.findall(r"\{(Step\d+\.\w+)\}", sCommand):
            if sMatch in setChecked:
                continue
            setChecked.add(sMatch)
            sPath = dictVariables.get(sMatch, "")
            if not sPath:
                continue
            sQuoted = fsShellQuote(sPath)
            iExitCode, _ = await asyncio.to_thread(
                connectionDocker.ftResultExecuteCommand,
                sContainerId, f"test -f {sQuoted}"
            )
            if iExitCode != 0:
                return sPath
    return ""


# ---------------------------------------------------------------------------
# Dependency check with banner
# ---------------------------------------------------------------------------

async def _fiCheckDependencies(
    connectionDocker, sContainerId, dictStep,
    dictVariables, iStepNumber, fnStatusCallback,
    sStepLabel=None,
):
    """Return 1 if dependencies are missing, 0 otherwise."""
    sStepMissing = await _fsMissingDependencyFile(
        connectionDocker, sContainerId, dictStep, dictVariables
    )
    if not sStepMissing:
        return 0
    if sStepLabel is None:
        sStepLabel = f"{iStepNumber:02d}"
    sStepName = dictStep.get("sName", f"Step {iStepNumber}")
    await fnStatusCallback({
        "sType": "output",
        "sLine": f"SKIPPED: Step {sStepLabel} - "
                 f"{sStepName} (dependency not found: "
                 f"{sStepMissing})",
    })
    await fnStatusCallback({
        "sType": "stepFail", "iStepNumber": iStepNumber,
        "iExitCode": 1,
    })
    return 1


# ---------------------------------------------------------------------------
# Single-step execution
# ---------------------------------------------------------------------------

async def _fnRunOneStep(
    connectionDocker, sContainerId, dictStep,
    iStepNumber, sWorkdir, dictVariables, fnStatusCallback,
    sStepLabel=None, sRunMode="full",
):
    """Run a single automatic step with timing and result."""
    iDepResult = await _fiCheckDependencies(
        connectionDocker, sContainerId, dictStep,
        dictVariables, iStepNumber, fnStatusCallback,
        sStepLabel=sStepLabel,
    )
    if iDepResult != 0:
        return iDepResult
    sStepName = dictStep.get("sName", f"Step {iStepNumber}")
    await _fnEmitBanner(
        fnStatusCallback, iStepNumber, sStepName, sStepLabel,
    )
    await fnStatusCallback({
        "sType": "stepStarted", "iStepNumber": iStepNumber,
    })
    return await _fiExecuteAndRecord(
        connectionDocker, sContainerId, dictStep,
        iStepNumber, sWorkdir, dictVariables, fnStatusCallback,
        sRunMode=sRunMode,
    )


async def _fiExecuteAndRecord(
    connectionDocker, sContainerId, dictStep,
    iStepNumber, sWorkdir, dictVariables, fnStatusCallback,
    sRunMode="full",
):
    """Execute step commands, record timing, emit results."""
    import time
    fStartTime = time.time()
    sStepDir = workflowManager.fsResolveStepWorkdir(
        dictStep.get("sDirectory", sWorkdir), dictVariables,
    )
    await asyncio.to_thread(
        workflowManager.fnCleanStepScratchDirs,
        connectionDocker, sContainerId, dictStep, dictVariables,
    )
    setFilesBefore = await _fsetSnapshotDirectory(
        connectionDocker, sContainerId, sStepDir,
        _fiDiscoveryMaxDepthForStep(dictStep),
    )
    iExitCode, fCpuTime = await fiRunStepCommands(
        connectionDocker, sContainerId,
        dictStep, sWorkdir, dictVariables, fnStatusCallback,
        iStepNumber=iStepNumber, sRunMode=sRunMode,
    )
    _fnRecordRunStats(dictStep, fStartTime, fCpuTime)
    await fnStatusCallback({
        "sType": "stepStats", "iStepNumber": iStepNumber,
        "dictRunStats": dictStep["dictRunStats"],
    })
    await _fnEmitDiscoveredOutputs(
        connectionDocker, sContainerId, sStepDir,
        setFilesBefore, dictStep, iStepNumber, fnStatusCallback,
    )
    await _fnEmitStepResult(fnStatusCallback, iStepNumber, iExitCode)
    return iExitCode


# ---------------------------------------------------------------------------
# Main orchestration loop
# ---------------------------------------------------------------------------

async def _fiRunStepList(
    connectionDocker, sContainerId,
    dictWorkflow, sWorkdir, dictVariables, fnStatusCallback,
    iStartStep=1, dictInteractive=None, sRunMode="full",
    setRunStepIndices=None,
):
    """Iterate steps, pausing at interactive ones."""
    from .interactiveSteps import _fiHandleInteractiveStep

    iFinalExitCode = 0
    for iIndex, dictStep in enumerate(dictWorkflow["listSteps"]):
        iStepNumber = iIndex + 1
        if not _fbShouldRunStep(
            dictStep, iStepNumber, iStartStep, setRunStepIndices,
        ):
            continue
        sStepLabel = fsLabelFromStepIndex(dictWorkflow, iIndex)
        if dictStep.get("bInteractive", False):
            iExitCode = await _fiHandleInteractiveStep(
                connectionDocker, sContainerId, dictStep,
                iStepNumber, fnStatusCallback, dictInteractive,
            )
        else:
            iExitCode = await _fnRunOneStep(
                connectionDocker, sContainerId, dictStep,
                iStepNumber, sWorkdir, dictVariables,
                fnStatusCallback, sStepLabel=sStepLabel,
                sRunMode=sRunMode,
            )
        if iExitCode != 0:
            iFinalExitCode = iExitCode
    return iFinalExitCode


def _ftInitializeRunState(
    connectionDocker, sContainerId, dictWorkflow,
    sAction, sLogPath,
):
    """Build initial run state and start the single-writer thread.

    Returns ``(dictState, stateWriter)``. The writer owns the only
    docker I/O for ``pipeline_state.json``; producers (heartbeat,
    flushing callback, finalize) enqueue updates so docker hiccups
    never starve the heartbeat thread.
    """
    iStepCount = len(dictWorkflow.get("listSteps", []))
    dictState = pipelineState.fdictBuildInitialState(
        sAction, sLogPath, iStepCount, iRunnerPid=os.getpid()
    )
    stateWriter = pipelineState.StateWriter(
        connectionDocker, sContainerId, dictState,
    )
    stateWriter.fnStart()
    return dictState, stateWriter


async def _fiRunStepsAndLog(
    connectionDocker, sContainerId, dictWorkflow, sWorkdir,
    dictVariables, fnLogging, fnStatusCallback,
    sLogPath, listLogLines, sAction, iStartStep,
    sWorkflowPath="", dictInteractive=None, sRunMode="full",
    setRunStepIndices=None,
):
    """Execute steps, write log, and emit final status."""
    dictState, stateWriter = _ftInitializeRunState(
        connectionDocker, sContainerId, dictWorkflow,
        sAction, sLogPath,
    )
    eventStopHeartbeat = threading.Event()
    threadHeartbeat = _fnStartHeartbeatThread(
        connectionDocker, sContainerId, dictState,
        stateWriter, eventStopHeartbeat,
    )
    try:
        await fnLogging({"sType": "started", "sCommand": sAction})
        fnLoggingWithFlush = _ffBuildFlushingCallback(
            fnLogging, connectionDocker, sContainerId,
            dictState, sLogPath, listLogLines,
            stateWriter=stateWriter,
        )
        iResult = await _fiRunStepList(
            connectionDocker, sContainerId,
            dictWorkflow, sWorkdir, dictVariables, fnLoggingWithFlush,
            iStartStep=iStartStep, dictInteractive=dictInteractive,
            sRunMode=sRunMode, setRunStepIndices=setRunStepIndices,
        )
        await _fnFinalizeRun(
            connectionDocker, sContainerId, dictState, iResult,
            sLogPath, listLogLines, dictWorkflow, sWorkflowPath,
            fnStatusCallback, stateWriter=stateWriter,
        )
    finally:
        # Ordering: stop heartbeat producer first so it cannot enqueue
        # after the writer is told to drain; then drain + stop writer.
        # ``join()`` carries no timeout — the writer drains the queue
        # before returning, so the late-heartbeat-overwrites-completed
        # race that motivated HIGH #12 cannot fire.
        eventStopHeartbeat.set()
        threadHeartbeat.join()
        stateWriter.fnStop()
    return iResult


def _fnStartHeartbeatThread(
    connectionDocker, sContainerId, dictState, lockOrWriter, eventStop,
):
    """Spawn a daemon thread that refreshes ``sLastHeartbeat`` periodically.

    ``lockOrWriter`` may be either a ``pipelineState.StateWriter``
    (preferred — heartbeats enqueue and never wait on docker) or a
    legacy ``threading.Lock`` (older callers that still write inline).
    """
    threadHeartbeat = threading.Thread(
        target=_fnRunHeartbeatLoop,
        args=(connectionDocker, sContainerId, dictState,
              lockOrWriter, eventStop),
        name="vaibify-pipeline-heartbeat",
        daemon=True,
    )
    threadHeartbeat.start()
    return threadHeartbeat


def _fnRunHeartbeatLoop(
    connectionDocker, sContainerId, dictState, lockOrWriter, eventStop,
):
    """Tick ``sLastHeartbeat`` until ``eventStop`` is set.

    ``lockOrWriter`` is a ``pipelineState.StateWriter`` (preferred) or
    a ``threading.Lock`` (legacy). The writer path never holds a lock
    across docker I/O — that is the architectural fix for CRITICAL #2.
    """
    fInterval = pipelineState.I_HEARTBEAT_INTERVAL_SECONDS
    while not eventStop.wait(fInterval):
        try:
            _fnPostHeartbeat(
                connectionDocker, sContainerId, dictState, lockOrWriter,
            )
        except Exception as error:
            logging.getLogger("vaibify").warning(
                "pipeline heartbeat write failed: %s", error)


def _fnPostHeartbeat(
    connectionDocker, sContainerId, dictState, lockOrWriter,
):
    """Dispatch one heartbeat update via writer queue or legacy lock."""
    dictBeat = pipelineState.fdictBuildHeartbeatUpdate()
    if isinstance(lockOrWriter, pipelineState.StateWriter):
        lockOrWriter.fnEnqueueUpdate(dictBeat)
        return
    with lockOrWriter:
        pipelineState.fnUpdateState(
            connectionDocker, sContainerId, dictState, dictBeat,
        )


async def _ftPrepareLogAndVariables(
    connectionDocker, sContainerId, dictWorkflow, sWorkdir,
    fnStatusCallback,
):
    """Set up log path, logging callback, variables, and clear output flags."""
    from .pipelineLogger import fnPruneOldLogs
    sWorkflowName = dictWorkflow.get("sWorkflowName", "pipeline")
    sLogsDir = await _fnEnsureLogsDirectory(
        connectionDocker, sContainerId
    )
    await fnPruneOldLogs(connectionDocker, sContainerId, sLogsDir)
    sLogFilename = fsGenerateLogFilename(sWorkflowName)
    sLogPath = posixpath.join(sLogsDir, sLogFilename)
    listLogLines = []
    fnLogging = ffBuildLoggingCallback(fnStatusCallback, listLogLines)
    dictVariables = _fdictBuildVariables(dictWorkflow, sWorkdir)
    await _fnInjectDeterminismEnvPrefix(
        connectionDocker, sContainerId, dictWorkflow, dictVariables,
    )
    fnClearOutputModifiedFlags(dictWorkflow)
    return sLogPath, listLogLines, fnLogging, dictVariables


async def _fiRunWithLogging(
    connectionDocker, sContainerId, dictWorkflow,
    sWorkdir, fnStatusCallback, sAction, iStartStep=1,
    sWorkflowPath="", dictInteractive=None, sRunMode="full",
    setRunStepIndices=None,
):
    """Run steps with logging wrapper, writing log file on completion."""
    sLogPath, listLogLines, fnLogging, dictVariables = (
        await _ftPrepareLogAndVariables(
            connectionDocker, sContainerId, dictWorkflow,
            sWorkdir, fnStatusCallback,
        )
    )
    listPreflightErrors = await _flistPreflightValidate(
        connectionDocker, sContainerId, dictWorkflow,
        dictVariables, iStartStep,
        setRunStepIndices=setRunStepIndices,
    )
    if listPreflightErrors:
        return await _fiReportPreflightFailure(
            fnLogging, fnStatusCallback, connectionDocker,
            sContainerId, sLogPath, listLogLines,
            listPreflightErrors, sAction,
        )
    listPreflightWarnings = _flistCollectPreflightWarnings(
        connectionDocker, sContainerId, dictWorkflow,
    )
    for sWarning in listPreflightWarnings:
        await fnStatusCallback({
            "sType": "preflightWarning", "sMessage": sWarning,
        })
    return await _fiRunStepsAndLog(
        connectionDocker, sContainerId, dictWorkflow, sWorkdir,
        dictVariables, fnLogging, fnStatusCallback,
        sLogPath, listLogLines, sAction, iStartStep,
        sWorkflowPath=sWorkflowPath,
        dictInteractive=dictInteractive,
        sRunMode=sRunMode,
        setRunStepIndices=setRunStepIndices,
    )


# ---------------------------------------------------------------------------
# Public API: top-level entry points
# ---------------------------------------------------------------------------
# Every entry point requires the caller to pass the active workflow and
# its container path. Runner-side rediscovery was removed after a live
# incident where a container held two workflows and the alphabetical
# first was silently run instead of the dashboard-selected one.

async def fnRunAllSteps(
    connectionDocker, sContainerId, dictWorkflow, sWorkflowPath,
    sWorkdir, fnStatusCallback,
    bForceRun=False, dictInteractive=None,
):
    """Run all enabled steps with logging."""
    if bForceRun:
        for dictStep in dictWorkflow.get("listSteps", []):
            dictStep["dictRunStats"] = {}
    return await _fiRunWithLogging(
        connectionDocker, sContainerId, dictWorkflow,
        sWorkdir, fnStatusCallback,
        "forceRunAll" if bForceRun else "runAll",
        sWorkflowPath=sWorkflowPath,
        dictInteractive=dictInteractive,
    )


async def fnRunFromStep(
    connectionDocker, sContainerId, iStartStep,
    dictWorkflow, sWorkflowPath,
    sWorkdir, fnStatusCallback, dictInteractive=None,
):
    """Run steps starting from iStartStep (1-based) with logging."""
    return await _fiRunWithLogging(
        connectionDocker, sContainerId, dictWorkflow,
        sWorkdir, fnStatusCallback,
        f"runFrom:{iStartStep}", iStartStep=iStartStep,
        sWorkflowPath=sWorkflowPath,
        dictInteractive=dictInteractive,
    )


async def fnVerifyOnly(
    connectionDocker, sContainerId, dictWorkflow, sWorkflowPath,
    sWorkdir, fnStatusCallback,
):
    """Check that each step's output files exist without running."""
    del sWorkflowPath
    await fnStatusCallback(
        {"sType": "started", "sCommand": "verify"}
    )
    bAllPresent = await _fbVerifyStepList(
        connectionDocker, sContainerId, dictWorkflow,
        sWorkdir, fnStatusCallback,
    )
    iExitCode = 0 if bAllPresent else 1
    await _fnEmitCompletion(fnStatusCallback, iExitCode)
    return iExitCode


# ---------------------------------------------------------------------------
# Selected-steps execution
# ---------------------------------------------------------------------------

async def fnRunSelectedSteps(
    connectionDocker, sContainerId, listStepIndices,
    dictWorkflow, sWorkflowPath, sWorkdir, fnStatusCallback,
    sRunMode="full",
):
    """Run only the listed step indices for this run.

    The workflow's persistent ``bRunEnabled`` flags are not mutated.
    Run scope is communicated as a parameter set so that an
    interrupted run cannot leave the workflow definition in a
    half-toggled state on disk.
    """
    if sRunMode not in SET_VALID_RUN_MODES:
        raise ValueError(
            f"Unknown sRunMode: {sRunMode!r}. "
            f"Valid values: {sorted(SET_VALID_RUN_MODES)}"
        )
    setRunStepIndices = set(listStepIndices)
    return await _fiRunWithLogging(
        connectionDocker, sContainerId, dictWorkflow,
        sWorkdir, fnStatusCallback, "runSelected",
        sWorkflowPath=sWorkflowPath,
        sRunMode=sRunMode,
        setRunStepIndices=setRunStepIndices,
    )
