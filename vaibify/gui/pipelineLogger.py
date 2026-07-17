"""Logging infrastructure for pipeline execution."""

__all__ = [
    "I_MAX_LOG_LINES",
    "I_LOG_BYTE_BUDGET",
    "I_LOG_LINE_BYTE_CAP",
    "I_LOG_RETENTION_COUNT",
    "fsGenerateLogFilename",
    "ffBuildLoggingCallback",
    "fnWriteLogToContainer",
    "fnPruneOldLogs",
]

import asyncio
import json
import logging
import posixpath
import re
from datetime import datetime, timezone

from . import pipelineState
from . import workflowManager
from .pipelineUtils import fsShellQuote


I_MAX_LOG_LINES = 10000
# Cap each appended log line at 8 KB to keep one runaway scientific
# print (e.g. a multi-megabyte numpy array repr) from blowing past the
# in-memory budget or the append-mode exec line-length safety margin.
I_LOG_LINE_BYTE_CAP = 8 * 1024
# Cap the in-memory ring buffer at ~4 MB so a long-running pipeline
# does not exhaust the runner heap; eviction keeps the most recent
# lines so the final log file is contiguous from the tail.
I_LOG_BYTE_BUDGET = 4 * 1024 * 1024
# Keep this many historical log files in ``.vaibify/logs/`` and prune
# the rest on each fresh run. Older logs are interesting only for
# postmortem; they should never compete with the active run for disk.
I_LOG_RETENTION_COUNT = 20


def fsGenerateLogFilename(sWorkflowName):
    """Return a log filename with workflow name and UTC timestamp."""
    sTimestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    sCleanName = re.sub(r"[^a-zA-Z0-9_-]", "_", sWorkflowName)
    return f"{sCleanName}_{sTimestamp}.log"


def ffBuildLoggingCallback(fnOriginalCallback, listLogLines):
    """Return a callback that logs output lines and forwards events."""
    dictByteAccum = {"iBytes": _fiBytesInLines(listLogLines)}

    async def fnLoggingCallback(dictEvent):
        await fnOriginalCallback(dictEvent)
        listLines = _flistExtractLogLines(dictEvent)
        for sLine in listLines:
            sLineCapped = _fsCapLineBytes(sLine, I_LOG_LINE_BYTE_CAP)
            _fnAppendLogLineWithBudget(
                listLogLines, sLineCapped, dictByteAccum,
            )
    return fnLoggingCallback


def _fiBytesInLines(listLogLines):
    """Return the total UTF-8 byte size of the in-memory log buffer."""
    return sum(_fiLineByteSize(s) for s in listLogLines)


def _fiLineByteSize(sLine):
    """Return the UTF-8 byte size of a line, coercing non-strings via str()."""
    if not isinstance(sLine, str):
        sLine = str(sLine)
    return len(sLine.encode("utf-8", errors="replace"))


def _fsCapLineBytes(sLine, iMaxBytes):
    """Return ``sLine`` truncated to ``iMaxBytes`` UTF-8 bytes."""
    baLine = sLine.encode("utf-8", errors="replace")
    if len(baLine) <= iMaxBytes:
        return sLine
    return baLine[:iMaxBytes].decode("utf-8", errors="replace") + " ...[truncated]"


def _fnAppendLogLineWithBudget(listLogLines, sLine, dictByteAccum):
    """Append ``sLine`` and evict the head until both caps are respected."""
    iLineBytes = _fiLineByteSize(sLine)
    listLogLines.append(sLine)
    dictByteAccum["iBytes"] += iLineBytes
    while listLogLines and (
        len(listLogLines) > I_MAX_LOG_LINES
        or dictByteAccum["iBytes"] > I_LOG_BYTE_BUDGET
    ):
        sEvicted = listLogLines.pop(0)
        dictByteAccum["iBytes"] -= _fiLineByteSize(sEvicted)


def _fsExtractLogLine(dictEvent):
    """Return the log line from a pipeline event, or None.

    Single-line shim kept for legacy callers that expect one line per
    event; new ``outputBatch`` events carry many lines and must go
    through :func:`_flistExtractLogLines`.
    """
    if dictEvent.get("sType") == "output":
        return dictEvent.get("sLine", "")
    if dictEvent.get("sType") == "commandFailed":
        return (f"FAILED: {dictEvent.get('sCommand', '')} "
                f"(exit {dictEvent.get('iExitCode', '?')})")
    return None


def _flistExtractLogLines(dictEvent):
    """Return zero or more log lines from a pipeline event."""
    sType = dictEvent.get("sType")
    if sType == "outputBatch":
        return list(dictEvent.get("listLines") or [])
    sLine = _fsExtractLogLine(dictEvent)
    if sLine is None:
        return []
    return [sLine]


async def fnWriteLogToContainer(
    connectionDocker, sContainerId, sLogPath, listLogLines,
):
    """Append accumulated log lines to a file in the container.

    Uses ``cat >>`` rather than ``put_archive`` so a transient disk-full
    or tar-encoding error does not truncate the file — the previous
    bytes survive even when the next append fails. Each line is already
    capped at ``I_LOG_LINE_BYTE_CAP`` by the logging callback, so the
    here-doc cannot collide with shell argv length limits.
    """
    if not listLogLines:
        return
    sContent = "\n".join(listLogLines) + "\n"
    await asyncio.to_thread(
        _fnAppendLogContent,
        connectionDocker, sContainerId, sLogPath, sContent,
    )
    listLogLines.clear()


def _fnAppendLogContent(
    connectionDocker, sContainerId, sLogPath, sContent,
):
    """Append ``sContent`` to ``sLogPath`` inside the container.

    Encodes the payload as base64 and decodes it inside the container,
    so a scientific stdout line containing any shell metacharacter — or
    a literal heredoc sentinel — cannot escape into command execution.
    The previous ``cat <<'EOF'`` form was vulnerable: a line equal to
    the sentinel terminated the heredoc and the remainder of the buffer
    ran as shell (CLAUDE.md command-injection threat).
    """
    import base64
    sQuotedPath = fsShellQuote(sLogPath)
    sEncoded = base64.b64encode(
        sContent.encode("utf-8", errors="replace"),
    ).decode("ascii")
    sCommand = (
        f"printf '%s' {fsShellQuote(sEncoded)} | "
        f"base64 -d >> {sQuotedPath}"
    )
    iExitCode, sOutput = connectionDocker.ftResultExecuteCommand(
        sContainerId, sCommand,
    )
    if iExitCode != 0:
        logging.getLogger("vaibify").warning(
            "log append failed (exit %s): %s", iExitCode, sOutput[:200],
        )


# Files modified inside this window are presumed to belong to an
# active run that is still appending; prune leaves them alone so
# concurrent workflow runs on the same container cannot truncate
# each other's logs (R2 review).
I_LOG_PRUNE_AGE_MINUTES = 10


async def fnPruneOldLogs(
    connectionDocker, sContainerId, sLogsDir,
    iRetentionCount=I_LOG_RETENTION_COUNT,
):
    """Delete all but the ``iRetentionCount`` most recent log files.

    Files modified in the last ``I_LOG_PRUNE_AGE_MINUTES`` minutes are
    excluded so concurrent runs in the same container cannot unlink
    each other's still-active logs.
    """
    sQuoted = fsShellQuote(sLogsDir)
    iKeepPlusOne = iRetentionCount + 1
    sCommand = (
        f"find {sQuoted} -maxdepth 1 -type f -name '*.log' "
        f"-mmin +{I_LOG_PRUNE_AGE_MINUTES} -printf '%T@ %p\\n' "
        f"2>/dev/null | sort -rn | "
        f"tail -n +{iKeepPlusOne} | "
        f"cut -d' ' -f2- | xargs -r rm -f"
    )
    await asyncio.to_thread(
        connectionDocker.ftResultExecuteCommand,
        sContainerId, sCommand,
    )


async def _fnEnsureLogsDirectory(connectionDocker, sContainerId):
    """Create .vaibify/logs/ directory if it does not exist."""
    sLogsDir = posixpath.join(
        workflowManager.DEFAULT_SEARCH_ROOT,
        workflowManager.VAIBIFY_LOGS_DIR,
    )
    await asyncio.to_thread(
        connectionDocker.ftResultExecuteCommand,
        sContainerId, f"mkdir -p {fsShellQuote(sLogsDir)}",
    )
    return sLogsDir


def _ffBuildFlushingCallback(
    fnLogging, connectionDocker, sContainerId,
    dictState, sLogPath, listLogLines, lockState=None,
    stateWriter=None,
):
    """Return a callback that logs events and flushes on step results.

    ``stateWriter`` is the per-run ``pipelineState.StateWriter`` that
    owns persistence; producers enqueue updates rather than calling
    ``fnUpdateState`` directly so the heartbeat thread never sits
    behind a docker exec. Legacy callers (standalone director, older
    tests) may pass only ``lockState`` for the in-line write path; in
    that case docker I/O still happens inline as before.
    """
    async def fnLoggingWithFlush(dictEvent):
        await fnLogging(dictEvent)
        _fnUpdatePipelineState(
            connectionDocker, sContainerId, dictState, dictEvent,
            lockState, stateWriter=stateWriter,
        )
        sEventType = dictEvent.get("sType", "")
        if sEventType in ("stepPass", "stepFail"):
            await fnWriteLogToContainer(
                connectionDocker, sContainerId, sLogPath,
                listLogLines,
            )
    return fnLoggingWithFlush


_DICT_STEP_RESULT_STATUS = {
    "stepPass": "passed",
    "stepFail": "failed",
    "stepSkipped": "skipped",
}


def _fnApplyStepResultEvent(
    connectionDocker, sContainerId, dictState, dictEvent, lockState,
):
    """Persist a stepPass/stepFail/stepSkipped event under the state lock."""
    sEventType = dictEvent.get("sType", "")
    with lockState:
        pipelineState.fnRecordStepResult(
            connectionDocker, sContainerId, dictState,
            pipelineState.fdictBuildStepResult(
                dictEvent["iStepNumber"],
                _DICT_STEP_RESULT_STATUS[sEventType],
                dictEvent.get("iExitCode", 0)))


def _fnUpdatePipelineState(
    connectionDocker, sContainerId, dictState, dictEvent, lockState=None,
    stateWriter=None,
):
    """Update pipeline state based on a step event.

    With ``stateWriter`` (preferred): producer holds the writer's
    in-memory lock briefly, then a dedicated thread does the docker
    I/O — no producer ever waits on docker. With only ``lockState``
    (legacy / standalone director): the legacy in-line write path is
    used, where the lock guards both the memory update and the two
    docker calls inside ``fnUpdateState``.
    """
    if stateWriter is not None:
        _fnDispatchEventToWriter(stateWriter, dictEvent)
        return
    import threading as _threading
    if lockState is None:
        lockState = _threading.Lock()
    _fnDispatchEventInline(
        connectionDocker, sContainerId, dictState, dictEvent, lockState,
    )


def _fnDispatchEventToWriter(stateWriter, dictEvent):
    """Route a callback event through the single-writer queue."""
    sEventType = dictEvent.get("sType", "")
    if sEventType == "output":
        stateWriter.fnEnqueueOutputLine(dictEvent.get("sLine", ""))
    elif sEventType == "outputBatch":
        for sLine in (dictEvent.get("listLines") or []):
            stateWriter.fnEnqueueOutputLine(sLine)
    elif sEventType == "stepStarted":
        stateWriter.fnEnqueueUpdate(
            pipelineState.fdictBuildStepStarted(
                dictEvent["iStepNumber"],
                dictEvent.get("fWallClockBudgetSeconds", 0.0),
            )
        )
    elif sEventType in _DICT_STEP_RESULT_STATUS:
        stateWriter.fnEnqueueStepResult(
            pipelineState.fdictBuildStepResult(
                dictEvent["iStepNumber"],
                _DICT_STEP_RESULT_STATUS[sEventType],
                dictEvent.get("iExitCode", 0),
            )
        )


def _fnDispatchEventInline(
    connectionDocker, sContainerId, dictState, dictEvent, lockState,
):
    """Legacy in-line write path used when no StateWriter is supplied."""
    sEventType = dictEvent.get("sType", "")
    if sEventType == "output":
        with lockState:
            pipelineState.fnAppendOutput(
                dictState, dictEvent.get("sLine", ""))
    elif sEventType == "outputBatch":
        listLines = dictEvent.get("listLines") or []
        if listLines:
            with lockState:
                for sLine in listLines:
                    pipelineState.fnAppendOutput(dictState, sLine)
    elif sEventType == "stepStarted":
        with lockState:
            pipelineState.fnUpdateState(
                connectionDocker, sContainerId, dictState,
                pipelineState.fdictBuildStepStarted(
                    dictEvent["iStepNumber"],
                    dictEvent.get("fWallClockBudgetSeconds", 0.0)))
    elif sEventType in _DICT_STEP_RESULT_STATUS:
        _fnApplyStepResultEvent(
            connectionDocker, sContainerId, dictState, dictEvent,
            lockState,
        )


def _fnSaveWorkflowStats(
    connectionDocker, sContainerId, dictWorkflow, sWorkflowPath,
):
    """Save updated workflow (with run stats) back to container."""
    try:
        sContent = json.dumps(dictWorkflow, indent=2)
        connectionDocker.fnWriteFile(
            sContainerId, sWorkflowPath,
            sContent.encode("utf-8"),
        )
    except Exception as error:
        logging.getLogger("vaibify").error(
            "Failed to save workflow stats: %s", error)


async def _fnFinalizeRun(
    connectionDocker, sContainerId, dictState, iResult,
    sLogPath, listLogLines, dictWorkflow, sWorkflowPath,
    fnStatusCallback, lockState=None, stateWriter=None,
):
    """Write final state, log, and emit completion event."""
    dictCompleted = pipelineState.fdictBuildCompletedState(iResult)
    if stateWriter is not None:
        stateWriter.fnEnqueueUpdate(dictCompleted)
    else:
        import threading as _threading
        if lockState is None:
            lockState = _threading.Lock()
        with lockState:
            pipelineState.fnUpdateState(
                connectionDocker, sContainerId, dictState, dictCompleted,
            )
    await fnWriteLogToContainer(
        connectionDocker, sContainerId, sLogPath, listLogLines
    )
    if sWorkflowPath:
        _fnSaveWorkflowStats(
            connectionDocker, sContainerId, dictWorkflow,
            sWorkflowPath,
        )
    await fnStatusCallback(
        {"sType": "completed" if iResult == 0 else "failed",
         "iExitCode": iResult, "sLogPath": sLogPath}
    )
