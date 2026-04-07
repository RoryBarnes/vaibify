"""Execute workflow steps by running commands directly in containers."""

import asyncio
import json
import posixpath
import re
from datetime import datetime, timezone


def fsShellQuote(sValue):
    """Safely quote a value for use in a shell command.

    Wraps the value in single quotes and escapes any embedded single
    quotes with the standard '\'' idiom, preventing shell injection.
    """
    return "'" + sValue.replace("'", "'\\''") + "'"


def _fdictBuildVariables(dictWorkflow, sWorkdir):
    """Build merged global + step variable dict for resolution."""
    from . import workflowManager

    dictGlobalVars = workflowManager.fdictBuildGlobalVariables(
        dictWorkflow, sWorkdir
    )
    dictStepVars = workflowManager.fdictBuildStepVariables(
        dictWorkflow, dictGlobalVars
    )
    dictMerged = dict(dictGlobalVars)
    dictMerged.update(dictStepVars)
    return dictMerged


def fsGenerateLogFilename(sWorkflowName):
    """Return a log filename with workflow name and UTC timestamp."""
    sTimestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    sCleanName = re.sub(r"[^a-zA-Z0-9_-]", "_", sWorkflowName)
    return f"{sCleanName}_{sTimestamp}.log"


I_MAX_LOG_LINES = 10000


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
    from . import workflowManager

    sLogsDir = posixpath.join(
        workflowManager.DEFAULT_SEARCH_ROOT,
        workflowManager.VAIBIFY_LOGS_DIR,
    )
    await asyncio.to_thread(
        connectionDocker.ftResultExecuteCommand,
        sContainerId, f"mkdir -p {fsShellQuote(sLogsDir)}",
    )
    return sLogsDir


async def _flistPreflightValidate(
    connectionDocker, sContainerId, dictWorkflow, dictVariables,
    iStartStep=1,
):
    """Validate step directories and scripts exist before running."""
    from . import workflowManager

    listErrors = []
    for iIndex, dictStep in enumerate(dictWorkflow["listSteps"]):
        iStepNumber = iIndex + 1
        if not dictStep.get("bEnabled", True):
            continue
        if iStepNumber < iStartStep:
            continue
        sStepDir = dictStep.get("sDirectory", "")
        _fnValidateStepDirectory(
            connectionDocker, sContainerId, sStepDir,
            iStepNumber, dictStep["sName"], listErrors,
        )
        _fnValidateStepCommands(
            connectionDocker, sContainerId, dictStep,
            sStepDir, dictVariables, iStepNumber, listErrors,
        )
    return listErrors


def _fnValidateStepDirectory(
    connectionDocker, sContainerId, sStepDirectory,
    iStepNumber, sStepName, listErrors,
):
    """Check that a step's working directory exists and is writable."""
    sQuoted = fsShellQuote(sStepDirectory)
    sCommand = (
        f"test -d {sQuoted} && test -w {sQuoted} "
        f"&& echo ok || "
        f"(test -d {sQuoted} && echo readonly || echo missing)"
    )
    iExitCode, sOutput = connectionDocker.ftResultExecuteCommand(
        sContainerId, sCommand
    )
    sResult = sOutput.strip()
    if sResult == "missing":
        listErrors.append(
            f"Step {iStepNumber} ({sStepName}): "
            f"directory does not exist: {sStepDirectory}"
        )
        return
    if sResult == "readonly":
        iExitCode = 1
    if iExitCode != 0:
        listErrors.append(
            f"Step {iStepNumber} ({sStepName}): "
            f"directory not writable: {sStepDirectory}"
        )


def _fnValidateStepCommands(
    connectionDocker, sContainerId, dictStep,
    sStepDirectory, dictVariables, iStepNumber, listErrors,
):
    """Check that command scripts exist in the step directory."""
    from . import workflowManager

    for sKey in ("saDataCommands", "saTestCommands", "saPlotCommands"):
        for sCommand in dictStep.get(sKey, []):
            sResolved = workflowManager.fsResolveCommand(
                sCommand, dictVariables
            )
            _fnValidateSingleCommand(
                connectionDocker, sContainerId, sResolved,
                sStepDirectory, iStepNumber, dictStep["sName"],
                listErrors,
            )


def _fnValidateSingleCommand(
    connectionDocker, sContainerId, sResolved,
    sStepDirectory, iStepNumber, sStepName, listErrors,
):
    """Check that the script in a command exists."""
    sScript = _fsExtractScriptPath(sResolved)
    if not sScript:
        return
    sCheckCommand = (
        f"cd {fsShellQuote(sStepDirectory)} 2>/dev/null && "
        f"test -f {fsShellQuote(sScript)} || "
        f"which {fsShellQuote(sScript)} >/dev/null 2>&1"
    )
    iExitCode, _ = connectionDocker.ftResultExecuteCommand(
        sContainerId, sCheckCommand
    )
    if iExitCode != 0:
        listErrors.append(
            f"Step {iStepNumber} ({sStepName}): "
            f"command not found: {sScript} "
            f"(in {sStepDirectory})"
        )


def _fsExtractScriptPath(sCommand):
    """Extract the script/executable path from a command string."""
    from .commandUtilities import fsExtractScriptPath
    sScript = fsExtractScriptPath(sCommand)
    if sScript:
        return sScript
    listTokens = sCommand.split()
    if not listTokens:
        return None
    if listTokens[0] in ("cd", "cp", "echo", "rm", "mkdir", "bash"):
        return None
    return listTokens[0]


async def _fiReportPreflightFailure(
    fnLogging, fnStatusCallback, connectionDocker,
    sContainerId, sLogPath, listLogLines, listErrors, sAction,
):
    """Emit preflight errors, write log, and return exit code 1."""
    await fnLogging({"sType": "started", "sCommand": sAction})
    for sError in listErrors:
        await fnLogging({"sType": "output", "sLine": f"ERROR: {sError}"})
    await fnStatusCallback({
        "sType": "preflightFailed", "listErrors": listErrors,
    })
    await fnWriteLogToContainer(
        connectionDocker, sContainerId, sLogPath, listLogLines
    )
    await fnStatusCallback({
        "sType": "failed", "iExitCode": 1, "sLogPath": sLogPath,
    })
    return 1


def _fnUpdatePipelineState(
    connectionDocker, sContainerId, dictState, dictEvent,
):
    """Update pipeline state based on a step event."""
    from . import pipelineState
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
        import logging
        logging.getLogger("vaibify").error(
            "Failed to save workflow stats: %s", error)


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


async def _fnFinalizeRun(
    connectionDocker, sContainerId, dictState, iResult,
    sLogPath, listLogLines, dictWorkflow, sWorkflowPath,
    fnStatusCallback,
):
    """Write final state, log, and emit completion event."""
    from . import pipelineState
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


async def _fiRunStepsAndLog(
    connectionDocker, sContainerId, dictWorkflow, sWorkdir,
    dictVariables, fnLogging, fnStatusCallback,
    sLogPath, listLogLines, sAction, iStartStep,
    sWorkflowPath="", dictInteractive=None,
):
    """Execute steps, write log, and emit final status."""
    from . import pipelineState

    iStepCount = len(dictWorkflow.get("listSteps", []))
    dictState = pipelineState.fdictBuildInitialState(
        sAction, sLogPath, iStepCount
    )
    pipelineState.fnWriteState(
        connectionDocker, sContainerId, dictState
    )
    await fnLogging({"sType": "started", "sCommand": sAction})
    fnLoggingWithFlush = _ffBuildFlushingCallback(
        fnLogging, connectionDocker, sContainerId,
        dictState, sLogPath, listLogLines,
    )
    iResult = await _fiRunStepList(
        connectionDocker, sContainerId,
        dictWorkflow, sWorkdir, dictVariables, fnLoggingWithFlush,
        iStartStep=iStartStep, dictInteractive=dictInteractive,
    )
    await _fnFinalizeRun(
        connectionDocker, sContainerId, dictState, iResult,
        sLogPath, listLogLines, dictWorkflow, sWorkflowPath,
        fnStatusCallback,
    )
    return iResult


def fnClearOutputModifiedFlags(dictWorkflow):
    """Clear modification flags on all steps before a pipeline run."""
    for dictStep in dictWorkflow.get("listSteps", []):
        dictVerification = dictStep.get("dictVerification", {})
        dictVerification.pop("bOutputModified", None)
        dictVerification.pop("listModifiedFiles", None)
        dictVerification.pop("bUpstreamModified", None)
        dictStep["dictVerification"] = dictVerification


async def _fiRunWithLogging(
    connectionDocker, sContainerId, dictWorkflow,
    sWorkdir, fnStatusCallback, sAction, iStartStep=1,
    sWorkflowPath="", dictInteractive=None,
):
    """Run steps with logging wrapper, writing log file on completion."""
    sWorkflowName = dictWorkflow.get("sWorkflowName", "pipeline")
    sLogsDir = await _fnEnsureLogsDirectory(
        connectionDocker, sContainerId
    )
    sLogFilename = fsGenerateLogFilename(sWorkflowName)
    sLogPath = posixpath.join(sLogsDir, sLogFilename)
    listLogLines = []
    fnLogging = ffBuildLoggingCallback(fnStatusCallback, listLogLines)
    dictVariables = _fdictBuildVariables(dictWorkflow, sWorkdir)
    fnClearOutputModifiedFlags(dictWorkflow)

    listPreflightErrors = await _flistPreflightValidate(
        connectionDocker, sContainerId, dictWorkflow,
        dictVariables, iStartStep,
    )
    if listPreflightErrors:
        return await _fiReportPreflightFailure(
            fnLogging, fnStatusCallback, connectionDocker,
            sContainerId, sLogPath, listLogLines,
            listPreflightErrors, sAction,
        )
    return await _fiRunStepsAndLog(
        connectionDocker, sContainerId, dictWorkflow, sWorkdir,
        dictVariables, fnLogging, fnStatusCallback,
        sLogPath, listLogLines, sAction, iStartStep,
        sWorkflowPath=sWorkflowPath,
        dictInteractive=dictInteractive,
    )


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


async def _fiRunTestCommands(
    connectionDocker, sContainerId, dictStep,
    sStepDirectory, dictVariables, fnStatusCallback,
    iStepNumber,
):
    """Run test commands per category and emit results."""
    listTestCommands = _flistResolveTestCommands(dictStep)
    if not listTestCommands:
        return 0
    await fnStatusCallback(
        {"sType": "output", "sLine": "--- Running unit tests ---"}
    )
    dictCategoryResults = await _fdictRunTestsByCategory(
        connectionDocker, sContainerId, dictStep,
        sStepDirectory, dictVariables, fnStatusCallback,
    )
    listAllLog = _flistCollectCategoryLogs(dictCategoryResults)
    await _fnWriteTestLog(
        connectionDocker, sContainerId, iStepNumber, listAllLog,
    )
    await _fnEmitPerCategoryResults(
        fnStatusCallback, iStepNumber, dictCategoryResults,
    )
    return _fiAggregateTestExitCode(dictCategoryResults)


def _flistCollectCategoryLogs(dictCategoryResults):
    """Collect all log lines from category results."""
    listLines = []
    for dictResult in dictCategoryResults.values():
        sOutput = dictResult.get("sOutput", "")
        if sOutput:
            listLines.extend(sOutput.split("\n"))
    return listLines


async def _fdictRunTestsByCategory(
    connectionDocker, sContainerId, dictStep,
    sStepDirectory, dictVariables, fnStatusCallback,
):
    """Run each test category separately, return {sCategory: dict}."""
    dictTests = dictStep.get("dictTests", {})
    dictResults = {}
    for sCatKey in ("dictIntegrity", "dictQualitative",
                    "dictQuantitative"):
        dictCat = dictTests.get(sCatKey, {})
        listCmds = dictCat.get("saCommands", [])
        if not listCmds:
            continue
        dictResults[sCatKey] = await _fdictRunOneCategoryCommands(
            connectionDocker, sContainerId, listCmds,
            sStepDirectory, dictVariables, fnStatusCallback,
        )
    if not dictResults:
        dictResults = await _fdictRunLegacyTestCommands(
            connectionDocker, sContainerId, dictStep,
            sStepDirectory, dictVariables, fnStatusCallback,
        )
    return dictResults


async def _fdictRunOneCategoryCommands(
    connectionDocker, sContainerId, listCommands,
    sStepDirectory, dictVariables, fnStatusCallback,
):
    """Run commands for one test category, return result dict."""
    listLog = []
    fnLog = ffBuildLoggingCallback(fnStatusCallback, listLog)
    iExitCode, _ = await _ftRunCommandList(
        connectionDocker, sContainerId, listCommands,
        sStepDirectory, dictVariables, fnLog,
    )
    return {
        "iExitCode": iExitCode,
        "sOutput": "\n".join(listLog),
    }


async def _fdictRunLegacyTestCommands(
    connectionDocker, sContainerId, dictStep,
    sStepDirectory, dictVariables, fnStatusCallback,
):
    """Fallback for steps using saTestCommands without dictTests."""
    listCommands = dictStep.get("saTestCommands", [])
    if not listCommands:
        return {}
    dictResult = await _fdictRunOneCategoryCommands(
        connectionDocker, sContainerId, listCommands,
        sStepDirectory, dictVariables, fnStatusCallback,
    )
    return {"legacy": dictResult}


async def _fnEmitPerCategoryResults(
    fnStatusCallback, iStepNumber, dictCategoryResults,
):
    """Emit testResult events with per-category detail."""
    dictCatDetail = {}
    listAllOutput = []
    for sCatKey, dictResult in dictCategoryResults.items():
        bPassed = dictResult["iExitCode"] == 0
        sCatOutput = dictResult.get("sOutput", "")
        dictCatDetail[sCatKey] = {
            "sStatus": "passed" if bPassed else "failed",
            "sOutput": sCatOutput,
        }
        listAllOutput.append(sCatOutput)
    bAllPassed = all(
        d["sStatus"] == "passed" for d in dictCatDetail.values()
    )
    sAggResult = "passed" if bAllPassed else "failed"
    await fnStatusCallback({
        "sType": "testResult",
        "iStepNumber": iStepNumber,
        "sResult": sAggResult,
        "sOutput": "\n".join(listAllOutput),
        "dictCategoryResults": dictCatDetail,
    })


def _fiAggregateTestExitCode(dictCategoryResults):
    """Return 0 if all categories passed, 1 otherwise."""
    for dictResult in dictCategoryResults.values():
        if dictResult.get("iExitCode", 1) != 0:
            return 1
    return 0


async def _fnWriteTestLog(
    connectionDocker, sContainerId, iStepNumber, listLogLines,
):
    """Write test output to a separate log file."""
    from . import workflowManager

    sLogsDir = posixpath.join(
        workflowManager.DEFAULT_SEARCH_ROOT,
        workflowManager.VAIBIFY_LOGS_DIR,
    )
    sTimestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    sFilename = f"test_Step{iStepNumber:02d}_{sTimestamp}.log"
    sLogPath = posixpath.join(sLogsDir, sFilename)
    await fnWriteLogToContainer(
        connectionDocker, sContainerId, sLogPath, listLogLines
    )


async def fiRunStepCommands(
    connectionDocker, sContainerId, dictStep,
    sWorkdir, dictVariables, fnStatusCallback,
    iStepNumber=0,
):
    """Run a single step's commands sequentially in its directory."""
    sStepDirectory = dictStep.get("sDirectory", sWorkdir)
    sPlotDirectory = dictVariables.get("sPlotDirectory", "Plot")
    await asyncio.to_thread(
        connectionDocker.ftResultExecuteCommand,
        sContainerId,
        f"mkdir -p {fsShellQuote(sPlotDirectory)}",
    )
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
    iPlotExit, fPlotCpu = await _ftRunCommandList(
        connectionDocker, sContainerId,
        dictStep.get("saPlotCommands", []),
        sStepDirectory, dictVariables, fnStatusCallback,
    )
    return (iPlotExit, fCpuTime + fPlotCpu)


async def _ftRunCommandList(
    connectionDocker, sContainerId, listCommands,
    sWorkdir, dictVariables, fnStatusCallback,
):
    """Execute commands, return (iExitCode, fTotalCpuSeconds)."""
    from . import workflowManager
    fTotalCpu = 0.0
    for sCommand in listCommands:
        sResolved = workflowManager.fsResolveCommand(
            sCommand, dictVariables
        )
        iExitCode, fCpu = await _ftRunSingleCommand(
            connectionDocker, sContainerId,
            sCommand, sResolved, sWorkdir, fnStatusCallback,
        )
        fTotalCpu += fCpu
        if iExitCode != 0:
            return (iExitCode, fTotalCpu)
    return (0, fTotalCpu)


async def _ftRunSingleCommand(
    connectionDocker, sContainerId,
    sOriginal, sResolved, sWorkdir, fnStatusCallback,
):
    """Execute one command, return (iExitCode, fCpuSeconds)."""
    await _fnEmitCommandHeader(
        fnStatusCallback, sOriginal, sResolved
    )
    sTimedCmd = _fsWrapWithTime(sResolved)
    iExitCode, sOutput = await asyncio.to_thread(
        connectionDocker.ftResultExecuteCommand,
        sContainerId, sTimedCmd, sWorkdir=sWorkdir,
    )
    fCpuSeconds = _fParseCpuTime(sOutput)
    for sLine in sOutput.splitlines():
        if sLine.startswith("__VAIBIFY_CPU__ "):
            continue
        await fnStatusCallback({"sType": "output", "sLine": sLine})
    if iExitCode != 0:
        await fnStatusCallback({
            "sType": "commandFailed",
            "sCommand": sResolved,
            "sDirectory": sWorkdir,
            "iExitCode": iExitCode,
        })
    return (iExitCode, fCpuSeconds)


def _fsWrapWithTime(sCommand):
    """Wrap a command with /usr/bin/time to capture CPU usage.

    Falls back to running the command without timing when
    /usr/bin/time is missing from the container.
    """
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


async def _fnEmitCommandHeader(fnStatusCallback, sOriginal, sResolved):
    """Emit the command being run, showing resolution if different."""
    await fnStatusCallback(
        {"sType": "output", "sLine": f"$ {sOriginal}"}
    )
    if sResolved != sOriginal:
        await fnStatusCallback(
            {"sType": "output", "sLine": f"  => {sResolved}"}
        )


async def _fsetSnapshotDirectory(
    connectionDocker, sContainerId, sDirectory,
):
    """Return a set of file paths in a directory."""
    iExit, sOutput = await asyncio.to_thread(
        connectionDocker.ftResultExecuteCommand,
        sContainerId,
        f"find {fsShellQuote(sDirectory)} -type f 2>/dev/null",
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


async def _fnEmitDiscoveredOutputs(
    connectionDocker, sContainerId, sDirectory,
    setFilesBefore, dictStep, iStepNumber, fnStatusCallback,
):
    """Diff directory and emit discovered output files."""
    setFilesAfter = await _fsetSnapshotDirectory(
        connectionDocker, sContainerId, sDirectory
    )
    setNewFiles = setFilesAfter - setFilesBefore
    if not setNewFiles:
        return
    listUnexpected = _flistFilterUnexpectedFiles(
        setNewFiles, sDirectory, dictStep)
    if listUnexpected:
        await fnStatusCallback({
            "sType": "discoveredOutputs",
            "iStepNumber": iStepNumber,
            "listDiscovered": listUnexpected,
        })


async def _fnEmitStepResult(fnStatusCallback, iStepNumber, iExitCode):
    """Send a stepPass or stepFail event based on exit code."""
    sType = "stepPass" if iExitCode == 0 else "stepFail"
    await fnStatusCallback({
        "sType": sType, "iStepNumber": iStepNumber,
        "iExitCode": iExitCode,
    })


async def _fnEmitCompletion(fnStatusCallback, iExitCode):
    """Send the final completed or failed event."""
    sResultType = "completed" if iExitCode == 0 else "failed"
    await fnStatusCallback(
        {"sType": sResultType, "iExitCode": iExitCode}
    )


async def _fdictLoadWorkflow(connectionDocker, sContainerId, fnStatusCallback):
    """Load workflow.json from the container, returning (dict, path)."""
    from . import workflowManager

    listWorkflows = workflowManager.flistFindWorkflowsInContainer(
        connectionDocker, sContainerId
    )
    if not listWorkflows:
        await fnStatusCallback(
            {"sType": "error", "sMessage": "No workflow found"}
        )
        return None, ""
    sPath = listWorkflows[0]["sPath"]
    dictWorkflow = workflowManager.fdictLoadWorkflowFromContainer(
        connectionDocker, sContainerId, sPath
    )
    return dictWorkflow, sPath


async def fnRunAllSteps(
    connectionDocker, sContainerId, sWorkdir, fnStatusCallback,
    bForceRun=False, dictInteractive=None,
):
    """Run all enabled steps with logging."""
    dictWorkflow, sWorkflowPath = await _fdictLoadWorkflow(
        connectionDocker, sContainerId, fnStatusCallback
    )
    if dictWorkflow is None:
        return 1
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


def fdictCreateInteractiveContext():
    """Return a context dict for pause/resume at interactive steps."""
    return {
        "eventResume": asyncio.Event(),
        "sResponse": "",
    }


def fnSetInteractiveResponse(dictContext, sResponse):
    """Set the response and trigger the resume event."""
    dictContext["sResponse"] = sResponse
    dictContext["eventResume"].set()


def _fbShouldRunStep(dictStep, iStepNumber, iStartStep):
    """Return True if this step should be executed."""
    if iStepNumber < iStartStep:
        return False
    return dictStep.get("bEnabled", True)


async def _fsMissingDependencyFile(
    connectionDocker, sContainerId, dictStep, dictVariables,
):
    """Return the first missing dependency path, or empty string."""
    import asyncio
    import re
    listAllCommands = (
        dictStep.get("saDataCommands", [])
        + dictStep.get("saPlotCommands", [])
    )
    setChecked = set()
    for sCmd in listAllCommands:
        for sMatch in re.findall(r"\{(Step\d+\.\w+)\}", sCmd):
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


async def _fbShouldSkipStep(
    connectionDocker, sContainerId, dictStep, iStepNumber,
):
    """Return True if the step's inputs are unchanged."""
    from . import syncDispatcher

    return syncDispatcher.fbStepInputsUnchanged(
        connectionDocker, sContainerId, dictStep, iStepNumber
    )


async def _fnRecordInputHashes(
    connectionDocker, sContainerId, dictStep,
):
    """Compute and store input hashes after a step runs."""
    from . import syncDispatcher

    dictHashes = syncDispatcher.fdictComputeInputHashes(
        connectionDocker, sContainerId, dictStep
    )
    if "dictRunStats" not in dictStep:
        dictStep["dictRunStats"] = {}
    dictStep["dictRunStats"]["dictInputHashes"] = dictHashes


def fsComputeStepLabel(dictWorkflow, iStepNumber):
    """Return the display label (A01, I01) for a 1-based step number."""
    iIndex = iStepNumber - 1
    listSteps = dictWorkflow.get("listSteps", [])
    if iIndex < 0 or iIndex >= len(listSteps):
        return f"{iStepNumber:02d}"
    bInteractive = listSteps[iIndex].get("bInteractive", False)
    sPrefix = "I" if bInteractive else "A"
    iCount = 0
    for iPos in range(iIndex + 1):
        bSameType = listSteps[iPos].get(
            "bInteractive", False) == bInteractive
        if bSameType:
            iCount += 1
    return f"{sPrefix}{iCount:02d}"


async def _fnEmitBanner(
    fnStatusCallback, iStepNumber, sStepName, sStepLabel=None,
):
    """Emit step banner lines to the status callback."""
    if sStepLabel is None:
        sStepLabel = f"{iStepNumber:02d}"
    sBanner = f"Step {sStepLabel} - {sStepName}"
    sLine = "=" * len(sBanner)
    for sText in ["", sLine, sBanner, sLine, ""]:
        await fnStatusCallback({"sType": "output", "sLine": sText})


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


async def _fnRunOneStep(
    connectionDocker, sContainerId, dictStep,
    iStepNumber, sWorkdir, dictVariables, fnStatusCallback,
    sStepLabel=None,
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
    if await _fbShouldSkipStep(
        connectionDocker, sContainerId, dictStep, iStepNumber
    ):
        await fnStatusCallback({
            "sType": "stepSkipped", "iStepNumber": iStepNumber,
        })
        return 0
    await fnStatusCallback({
        "sType": "stepStarted", "iStepNumber": iStepNumber,
    })
    return await _fiExecuteAndRecord(
        connectionDocker, sContainerId, dictStep,
        iStepNumber, sWorkdir, dictVariables, fnStatusCallback,
    )


def _fnRecordRunStats(
    dictStep, sStartTimestamp, fStartTime, fCpuTime=0.0,
):
    """Store timing information in the step's run stats."""
    import time
    dictStep["dictRunStats"] = {
        "sLastRun": sStartTimestamp,
        "fWallClock": round(time.time() - fStartTime, 1),
        "fCpuTime": round(fCpuTime, 1),
    }


async def _fiExecuteAndRecord(
    connectionDocker, sContainerId, dictStep,
    iStepNumber, sWorkdir, dictVariables, fnStatusCallback,
):
    """Execute step commands, record timing, emit results."""
    import time
    fStartTime = time.time()
    sStartTimestamp = datetime.now(timezone.utc).strftime(
        "%Y-%m-%d %H:%M:%S UTC"
    )
    sStepDir = dictStep.get("sDirectory", sWorkdir)
    setFilesBefore = await _fsetSnapshotDirectory(
        connectionDocker, sContainerId, sStepDir
    )
    iExitCode, fCpuTime = await fiRunStepCommands(
        connectionDocker, sContainerId,
        dictStep, sWorkdir, dictVariables, fnStatusCallback,
        iStepNumber=iStepNumber,
    )
    _fnRecordRunStats(
        dictStep, sStartTimestamp, fStartTime, fCpuTime)
    await fnStatusCallback({
        "sType": "stepStats", "iStepNumber": iStepNumber,
        "dictRunStats": dictStep["dictRunStats"],
    })
    await _fnRecordInputHashes(
        connectionDocker, sContainerId, dictStep
    )
    await _fnEmitDiscoveredOutputs(
        connectionDocker, sContainerId, sStepDir,
        setFilesBefore, dictStep, iStepNumber, fnStatusCallback,
    )
    await _fnEmitStepResult(fnStatusCallback, iStepNumber, iExitCode)
    return iExitCode


async def _fiRunStepList(
    connectionDocker, sContainerId,
    dictWorkflow, sWorkdir, dictVariables, fnStatusCallback,
    iStartStep=1, dictInteractive=None,
):
    """Iterate steps, pausing at interactive ones."""
    iFinalExitCode = 0
    for iIndex, dictStep in enumerate(dictWorkflow["listSteps"]):
        iStepNumber = iIndex + 1
        if not _fbShouldRunStep(dictStep, iStepNumber, iStartStep):
            continue
        sStepLabel = fsComputeStepLabel(dictWorkflow, iStepNumber)
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
            )
        if iExitCode != 0:
            iFinalExitCode = iExitCode
    return iFinalExitCode


async def _fiHandleInteractiveStep(
    connectionDocker, sContainerId, dictStep,
    iStepNumber, fnStatusCallback, dictInteractive,
):
    """Pause the pipeline and wait for user decision."""
    if dictInteractive is None:
        return 0
    sStepName = dictStep.get("sName", f"Step {iStepNumber}")
    await fnStatusCallback({
        "sType": "interactivePause",
        "iStepIndex": iStepNumber - 1,
        "iStepNumber": iStepNumber,
        "sStepName": sStepName,
    })
    sResponse = await _fsAwaitInteractiveDecision(
        dictInteractive,
    )
    if sResponse == "skip":
        return 0
    return await _fiRunInteractiveAndRecord(
        connectionDocker, sContainerId, dictStep,
        iStepNumber, fnStatusCallback, dictInteractive,
    )


async def _fiRunInteractiveAndRecord(
    connectionDocker, sContainerId, dictStep,
    iStepNumber, fnStatusCallback, dictInteractive,
):
    """Run the interactive terminal session and record results."""
    import time
    fStartTime = time.time()
    sStartTimestamp = datetime.now(timezone.utc).strftime(
        "%Y-%m-%d %H:%M:%S UTC"
    )
    await fnStatusCallback({
        "sType": "interactiveTerminalStart",
        "iStepNumber": iStepNumber,
        "sStepName": dictStep.get("sName", ""),
        "dictStep": dictStep,
    })
    iExitCode = await _fiAwaitInteractiveComplete(dictInteractive)
    _fnRecordRunStats(dictStep, sStartTimestamp, fStartTime, 0.0)
    await _fnRecordInputHashes(
        connectionDocker, sContainerId, dictStep,
    )
    await fnStatusCallback({
        "sType": "stepStats", "iStepNumber": iStepNumber,
        "dictRunStats": dictStep.get("dictRunStats", {}),
    })
    await _fnEmitStepResult(fnStatusCallback, iStepNumber, iExitCode)
    return iExitCode


async def _fsAwaitInteractiveDecision(dictInteractive):
    """Wait for the user to resume or skip, return response."""
    dictInteractive["eventResume"].clear()
    dictInteractive["sResponse"] = ""
    await dictInteractive["eventResume"].wait()
    return dictInteractive["sResponse"]


async def _fiAwaitInteractiveComplete(dictInteractive):
    """Wait for the frontend to signal interactive step done."""
    dictInteractive["eventResume"].clear()
    dictInteractive["sResponse"] = ""
    await dictInteractive["eventResume"].wait()
    sResponse = dictInteractive["sResponse"]
    if sResponse.startswith("complete:"):
        return int(sResponse.split(":")[1])
    return 0


async def fnRunFromStep(
    connectionDocker, sContainerId, iStartStep,
    sWorkdir, fnStatusCallback, dictInteractive=None,
):
    """Run steps starting from iStartStep (1-based) with logging."""
    dictWorkflow, sWorkflowPath = await _fdictLoadWorkflow(
        connectionDocker, sContainerId, fnStatusCallback
    )
    if dictWorkflow is None:
        return 1
    return await _fiRunWithLogging(
        connectionDocker, sContainerId, dictWorkflow,
        sWorkdir, fnStatusCallback,
        f"runFrom:{iStartStep}", iStartStep=iStartStep,
        sWorkflowPath=sWorkflowPath,
        dictInteractive=dictInteractive,
    )


async def _fbVerifyStepOutputs(
    connectionDocker, sContainerId,
    dictStep, dictVars, sWorkdir, fnStatusCallback,
):
    """Return True if all output files for a step exist."""
    from .workflowManager import fsResolveVariables
    sStepDirectory = dictStep.get("sDirectory", sWorkdir)
    listOutputFiles = (
        dictStep.get("saPlotFiles", [])
        + dictStep.get("saDataFiles", [])
    )
    for sOutputFile in listOutputFiles:
        sResolved = fsResolveVariables(sOutputFile, dictVars)
        bExists = await _fbFileExistsInContainer(
            connectionDocker, sContainerId,
            sResolved, sStepDirectory,
        )
        if not bExists:
            await fnStatusCallback(
                {"sType": "output", "sLine": f"Missing: {sResolved}"}
            )
            return False
    return True


async def _fbFileExistsInContainer(
    connectionDocker, sContainerId, sFilePath, sWorkdir,
):
    """Return True if a file exists inside the container."""
    sCommand = f"test -f {fsShellQuote(sFilePath)}"
    iExitCode, _ = await asyncio.to_thread(
        connectionDocker.ftResultExecuteCommand,
        sContainerId, sCommand, sWorkdir=sWorkdir,
    )
    return iExitCode == 0


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


def _fdictBuildWorkflowVars(dictWorkflow):
    """Extract variable substitution dict from workflow metadata."""
    return {
        "sPlotDirectory": dictWorkflow.get("sPlotDirectory", "Plot"),
        "sFigureType": dictWorkflow.get("sFigureType", "pdf"),
    }


async def fnVerifyOnly(
    connectionDocker, sContainerId, sWorkdir, fnStatusCallback,
):
    """Check that each step's output files exist without running."""
    dictWorkflow, _sPath = await _fdictLoadWorkflow(
        connectionDocker, sContainerId, fnStatusCallback
    )
    if dictWorkflow is None:
        return 1
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


async def fnRunAllTests(
    connectionDocker, sContainerId, sWorkdir, fnStatusCallback,
    dictWorkflow=None,
):
    """Run unit tests for all enabled steps without data or plot commands."""
    if dictWorkflow is None:
        dictWorkflow, _sPath = await _fdictLoadWorkflow(
            connectionDocker, sContainerId, fnStatusCallback
        )
    if dictWorkflow is None:
        return 1
    await fnStatusCallback(
        {"sType": "started", "sCommand": "runAllTests"}
    )
    dictVars = _fdictBuildWorkflowVars(dictWorkflow)
    iFinalExitCode = await _fiRunTestsForAllSteps(
        connectionDocker, sContainerId, dictWorkflow,
        dictVars, fnStatusCallback,
    )
    await _fnEmitCompletion(fnStatusCallback, iFinalExitCode)
    return iFinalExitCode


def _flistResolveTestCommands(dictStep):
    """Return test commands from structured tests or legacy list."""
    from .workflowManager import flistBuildTestCommands
    if "dictTests" in dictStep:
        return flistBuildTestCommands(dictStep)
    return dictStep.get("saTestCommands", [])


async def _fiRunTestsForAllSteps(
    connectionDocker, sContainerId, dictWorkflow,
    dictVars, fnStatusCallback,
):
    """Iterate enabled steps and run their test commands."""
    iFinalExitCode = 0
    listSteps = dictWorkflow.get("listSteps", [])
    for iIndex, dictStep in enumerate(listSteps):
        if not dictStep.get("bEnabled", True):
            continue
        if not _flistResolveTestCommands(dictStep):
            continue
        iStepNumber = iIndex + 1
        iExitCode = await _fiRunStepTests(
            connectionDocker, sContainerId, dictStep,
            dictVars, fnStatusCallback, iStepNumber,
            dictWorkflow,
        )
        if iExitCode != 0:
            iFinalExitCode = 1
    return iFinalExitCode


async def _fnEmitStepBanner(
    fnStatusCallback, iStepNumber, dictStep, dictWorkflow,
):
    """Emit banner and stepStarted event for a step."""
    sStepName = dictStep.get("sName", f"Step {iStepNumber}")
    sStepLabel = fsComputeStepLabel(dictWorkflow, iStepNumber)
    await _fnEmitBanner(
        fnStatusCallback, iStepNumber, sStepName,
        sStepLabel=sStepLabel,
    )
    await fnStatusCallback(
        {"sType": "stepStarted", "iStepNumber": iStepNumber}
    )


async def _fiRunStepTests(
    connectionDocker, sContainerId, dictStep,
    dictVars, fnStatusCallback, iStepNumber,
    dictWorkflow,
):
    """Run tests for a single step and emit results."""
    sStepDirectory = dictStep.get("sDirectory", "")
    await _fnEmitStepBanner(
        fnStatusCallback, iStepNumber, dictStep, dictWorkflow,
    )
    iExitCode = await _fiRunTestCommands(
        connectionDocker, sContainerId, dictStep,
        sStepDirectory, dictVars, fnStatusCallback,
        iStepNumber,
    )
    await _fnRecordInputHashes(
        connectionDocker, sContainerId, dictStep,
    )
    await _fnEmitStepResult(fnStatusCallback, iStepNumber, iExitCode)
    return iExitCode


def _fnToggleSelectedSteps(dictWorkflow, listStepIndices):
    """Set bEnabled only for steps whose indices are in the list."""
    setSelected = set(listStepIndices)
    for iIndex in range(len(dictWorkflow["listSteps"])):
        dictWorkflow["listSteps"][iIndex]["bEnabled"] = (
            iIndex in setSelected
        )


async def _fnExecuteSelectedSteps(
    connectionDocker, sContainerId, listStepIndices,
    dictWorkflow, sWorkflowPath, sWorkdir, fnStatusCallback,
):
    """Toggle steps, save, run with logging, and emit completion."""
    from . import workflowManager

    _fnToggleSelectedSteps(dictWorkflow, listStepIndices)
    workflowManager.fnSaveWorkflowToContainer(
        connectionDocker, sContainerId, dictWorkflow, sWorkflowPath,
    )
    return await _fiRunWithLogging(
        connectionDocker, sContainerId, dictWorkflow,
        sWorkdir, fnStatusCallback, "runSelected",
    )


async def fnRunSelectedSteps(
    connectionDocker, sContainerId, listStepIndices,
    dictWorkflow, sWorkflowPath, sWorkdir, fnStatusCallback,
):
    """Run only selected steps by toggling bEnabled."""
    from . import workflowManager

    dictBackup = json.loads(json.dumps(dictWorkflow))
    try:
        iResult = await _fnExecuteSelectedSteps(
            connectionDocker, sContainerId, listStepIndices,
            dictWorkflow, sWorkflowPath, sWorkdir, fnStatusCallback,
        )
    finally:
        workflowManager.fnSaveWorkflowToContainer(
            connectionDocker, sContainerId, dictBackup, sWorkflowPath,
        )
    return iResult
