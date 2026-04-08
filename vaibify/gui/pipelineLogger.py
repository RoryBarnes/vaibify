"""Logging infrastructure for pipeline execution."""

__all__ = [
    "I_MAX_LOG_LINES",
    "fsGenerateLogFilename",
    "ffBuildLoggingCallback",
    "fnWriteLogToContainer",
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


def fsGenerateLogFilename(sWorkflowName):
    """Return a log filename with workflow name and UTC timestamp."""
    sTimestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    sCleanName = re.sub(r"[^a-zA-Z0-9_-]", "_", sWorkflowName)
    return f"{sCleanName}_{sTimestamp}.log"


def ffBuildLoggingCallback(fnOriginalCallback, listLogLines):
    """Return a callback that logs output lines and forwards events."""
    async def fnLoggingCallback(dictEvent):
        await fnOriginalCallback(dictEvent)
        sLine = _fsExtractLogLine(dictEvent)
        if sLine is not None:
            if len(listLogLines) >= I_MAX_LOG_LINES:
                del listLogLines[0]
            listLogLines.append(sLine)
    return fnLoggingCallback


def _fsExtractLogLine(dictEvent):
    """Return the log line from a pipeline event, or None."""
    if dictEvent.get("sType") == "output":
        return dictEvent.get("sLine", "")
    if dictEvent.get("sType") == "commandFailed":
        return (f"FAILED: {dictEvent.get('sCommand', '')} "
                f"(exit {dictEvent.get('iExitCode', '?')})")
    return None


async def fnWriteLogToContainer(
    connectionDocker, sContainerId, sLogPath, listLogLines,
):
    """Write accumulated log lines to a file in the container."""
    sContent = "\n".join(listLogLines) + "\n"
    await asyncio.to_thread(
        connectionDocker.fnWriteFile,
        sContainerId, sLogPath, sContent.encode("utf-8"),
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
    dictState, sLogPath, listLogLines,
):
    """Return a callback that logs events and flushes on step results."""
    async def fnLoggingWithFlush(dictEvent):
        await fnLogging(dictEvent)
        _fnUpdatePipelineState(
            connectionDocker, sContainerId, dictState, dictEvent
        )
        sEventType = dictEvent.get("sType", "")
        if sEventType in ("stepPass", "stepFail"):
            await fnWriteLogToContainer(
                connectionDocker, sContainerId, sLogPath,
                listLogLines,
            )
    return fnLoggingWithFlush


def _fnUpdatePipelineState(
    connectionDocker, sContainerId, dictState, dictEvent,
):
    """Update pipeline state based on a step event."""
    sEventType = dictEvent.get("sType", "")
    if sEventType == "output":
        pipelineState.fnAppendOutput(
            dictState, dictEvent.get("sLine", ""))
    elif sEventType == "stepStarted":
        pipelineState.fnUpdateState(
            connectionDocker, sContainerId, dictState,
            pipelineState.fdictBuildStepStarted(
                dictEvent["iStepNumber"]))
    elif sEventType in ("stepPass", "stepFail", "stepSkipped"):
        dictStepStatusMap = {
            "stepPass": "passed", "stepFail": "failed",
            "stepSkipped": "skipped",
        }
        pipelineState.fnRecordStepResult(
            connectionDocker, sContainerId, dictState,
            pipelineState.fdictBuildStepResult(
                dictEvent["iStepNumber"],
                dictStepStatusMap[sEventType],
                dictEvent.get("iExitCode", 0)))


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
    fnStatusCallback,
):
    """Write final state, log, and emit completion event."""
    pipelineState.fnUpdateState(
        connectionDocker, sContainerId, dictState,
        pipelineState.fdictBuildCompletedState(iResult),
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
