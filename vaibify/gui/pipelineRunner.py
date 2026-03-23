"""Execute workflow steps by running commands directly in containers."""

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


PATTERN_STEP_LABEL = re.compile(
    r"\[Step(\d+)\]|Step(\d+):|=+\s*\n\s*Step(\d+)"
)
PATTERN_STEP_SUCCESS = re.compile(r"SUCCESS:\s*Step(\d+)")
PATTERN_STEP_FAILED = re.compile(r"FAILED:\s*Step(\d+)")


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


def ffBuildLoggingCallback(fnOriginalCallback, listLogLines):
    """Return a callback that logs output lines and forwards events."""
    async def fnLoggingCallback(dictEvent):
        await fnOriginalCallback(dictEvent)
        if dictEvent.get("sType") == "output":
            listLogLines.append(dictEvent.get("sLine", ""))
        elif dictEvent.get("sType") == "commandFailed":
            listLogLines.append(
                f"FAILED: {dictEvent.get('sCommand', '')} "
                f"(exit {dictEvent.get('iExitCode', '?')})"
            )
    return fnLoggingCallback


async def fnWriteLogToContainer(
    connectionDocker, sContainerId, sLogPath, listLogLines,
):
    """Write accumulated log lines to a file in the container."""
    sContent = "\n".join(listLogLines) + "\n"
    connectionDocker.fnWriteFile(
        sContainerId, sLogPath, sContent.encode("utf-8")
    )


async def _fnEnsureLogsDirectory(connectionDocker, sContainerId):
    """Create .vaibify/logs/ directory if it does not exist."""
    from . import workflowManager

    sLogsDir = posixpath.join(
        workflowManager.DEFAULT_SEARCH_ROOT,
        workflowManager.VAIBIFY_LOGS_DIR,
    )
    connectionDocker.ftResultExecuteCommand(
        sContainerId, f"mkdir -p {fsShellQuote(sLogsDir)}"
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
    sCheckDir = f"test -d {sQuoted}"
    iExitCode, _ = connectionDocker.ftResultExecuteCommand(
        sContainerId, sCheckDir
    )
    if iExitCode != 0:
        listErrors.append(
            f"Step {iStepNumber} ({sStepName}): "
            f"directory does not exist: {sStepDirectory}"
        )
        return
    sCheckWrite = f"test -w {sQuoted}"
    iExitCode, _ = connectionDocker.ftResultExecuteCommand(
        sContainerId, sCheckWrite
    )
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


async def _fiRunStepsAndLog(
    connectionDocker, sContainerId, dictWorkflow, sWorkdir,
    dictVariables, fnLogging, fnStatusCallback,
    sLogPath, listLogLines, sAction, iStartStep,
    sWorkflowPath="",
):
    """Execute steps, write log, and emit final status."""
    from . import pipelineState  # noqa: E402

    iStepCount = len(dictWorkflow.get("listSteps", []))
    dictState = pipelineState.fdictBuildInitialState(
        sAction, sLogPath, iStepCount
    )
    pipelineState.fnWriteState(
        connectionDocker, sContainerId, dictState
    )
    await fnLogging({"sType": "started", "sCommand": sAction})

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

    iResult = await _fiRunStepList(
        connectionDocker, sContainerId,
        dictWorkflow, sWorkdir, dictVariables, fnLoggingWithFlush,
        iStartStep=iStartStep,
    )
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
    return iResult


async def _fiRunWithLogging(
    connectionDocker, sContainerId, dictWorkflow,
    sWorkdir, fnStatusCallback, sAction, iStartStep=1,
    sWorkflowPath="",
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
    )


async def _fiRunSetupIfNeeded(
    connectionDocker, sContainerId, dictStep,
    sStepDirectory, dictVariables, fnStatusCallback,
):
    """Run data analysis commands unless bPlotOnly is True."""
    if dictStep.get("bPlotOnly", False):
        return 0
    return await _fiRunCommandList(
        connectionDocker, sContainerId,
        dictStep.get("saDataCommands", []),
        sStepDirectory, dictVariables, fnStatusCallback,
    )


async def _fiRunTestCommands(
    connectionDocker, sContainerId, dictStep,
    sStepDirectory, dictVariables, fnStatusCallback,
    iStepNumber,
):
    """Run test commands and emit result. Does not abort on failure."""
    listTestCommands = dictStep.get("saTestCommands", [])
    if not listTestCommands:
        return 0
    await fnStatusCallback(
        {"sType": "output", "sLine": "--- Running unit tests ---"}
    )
    listTestLog = []
    fnTestLog = ffBuildLoggingCallback(fnStatusCallback, listTestLog)
    iExitCode = await _fiRunCommandList(
        connectionDocker, sContainerId, listTestCommands,
        sStepDirectory, dictVariables, fnTestLog,
    )
    await _fnWriteTestLog(
        connectionDocker, sContainerId, iStepNumber, listTestLog
    )
    sResult = "passed" if iExitCode == 0 else "failed"
    await fnStatusCallback({
        "sType": "testResult",
        "iStepNumber": iStepNumber,
        "sResult": sResult,
    })
    return iExitCode


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
    connectionDocker.ftResultExecuteCommand(
        sContainerId,
        f"mkdir -p {fsShellQuote(sPlotDirectory)}"
    )
    iExitCode = await _fiRunSetupIfNeeded(
        connectionDocker, sContainerId, dictStep,
        sStepDirectory, dictVariables, fnStatusCallback,
    )
    if iExitCode != 0:
        return iExitCode
    await _fiRunTestCommands(
        connectionDocker, sContainerId, dictStep,
        sStepDirectory, dictVariables, fnStatusCallback,
        iStepNumber,
    )
    return await _fiRunCommandList(
        connectionDocker, sContainerId,
        dictStep.get("saPlotCommands", []),
        sStepDirectory, dictVariables, fnStatusCallback,
    )


async def _fiRunCommandList(
    connectionDocker, sContainerId, listCommands,
    sWorkdir, dictVariables, fnStatusCallback,
):
    """Execute a list of commands, returning first non-zero exit code."""
    from . import workflowManager

    for sCommand in listCommands:
        sResolved = workflowManager.fsResolveCommand(
            sCommand, dictVariables
        )
        iExitCode = await _fiRunSingleCommand(
            connectionDocker, sContainerId,
            sCommand, sResolved, sWorkdir, fnStatusCallback,
        )
        if iExitCode != 0:
            return iExitCode
    return 0


async def _fiRunSingleCommand(
    connectionDocker, sContainerId,
    sOriginal, sResolved, sWorkdir, fnStatusCallback,
):
    """Execute one command and stream its output lines."""
    await _fnEmitCommandHeader(
        fnStatusCallback, sOriginal, sResolved
    )
    iExitCode, sOutput = connectionDocker.ftResultExecuteCommand(
        sContainerId, sResolved, sWorkdir=sWorkdir
    )
    for sLine in sOutput.splitlines():
        await fnStatusCallback({"sType": "output", "sLine": sLine})
    if iExitCode != 0:
        await fnStatusCallback({
            "sType": "commandFailed",
            "sCommand": sResolved,
            "sDirectory": sWorkdir,
            "iExitCode": iExitCode,
        })
    return iExitCode


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
    iExit, sOutput = connectionDocker.ftResultExecuteCommand(
        sContainerId,
        f"find {fsShellQuote(sDirectory)} -type f 2>/dev/null",
    )
    if iExit != 0 or not sOutput.strip():
        return set()
    return set(sOutput.strip().splitlines())


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
    setExpected = set()
    for sKey in ("saDataFiles", "saPlotFiles"):
        for sFile in dictStep.get(sKey, []):
            setExpected.add(sFile)
    listDiscovered = []
    for sFile in sorted(setNewFiles):
        sRelative = sFile
        if sFile.startswith(sDirectory + "/"):
            sRelative = sFile[len(sDirectory) + 1:]
        bExpected = sRelative in setExpected or sFile in setExpected
        listDiscovered.append({
            "sFilePath": sRelative,
            "bExpected": bExpected,
        })
    listUnexpected = [
        d for d in listDiscovered if not d["bExpected"]
    ]
    if listUnexpected:
        await fnStatusCallback({
            "sType": "discoveredOutputs",
            "iStepNumber": iStepNumber,
            "listDiscovered": listUnexpected,
        })


async def _fnEmitStepResult(fnStatusCallback, iStepNumber, iExitCode):
    """Send a stepPass or stepFail event based on exit code."""
    sType = "stepPass" if iExitCode == 0 else "stepFail"
    await fnStatusCallback(
        {"sType": sType, "iStepNumber": iStepNumber}
    )


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
    bForceRun=False,
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
    )


def _fbShouldRunStep(dictStep, iStepNumber, iStartStep):
    """Return True if this step should be executed."""
    if dictStep.get("bInteractive", False):
        return False
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


async def _fnEmitBanner(fnStatusCallback, iStepNumber, sStepName):
    """Emit step banner lines to the status callback."""
    sBanner = f"Step {iStepNumber:02d} - {sStepName}"
    sLine = "=" * len(sBanner)
    for sText in ["", sLine, sBanner, sLine, ""]:
        await fnStatusCallback({"sType": "output", "sLine": sText})


async def _fiCheckDependencies(
    connectionDocker, sContainerId, dictStep,
    dictVariables, iStepNumber, fnStatusCallback,
):
    """Return 1 if dependencies are missing, 0 otherwise."""
    sStepMissing = await _fsMissingDependencyFile(
        connectionDocker, sContainerId, dictStep, dictVariables
    )
    if not sStepMissing:
        return 0
    sStepName = dictStep.get("sName", f"Step {iStepNumber}")
    await fnStatusCallback({
        "sType": "output",
        "sLine": f"SKIPPED: Step {iStepNumber:02d} - "
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
):
    """Run a single step, record timing, and emit result."""
    if dictStep.get("bInteractive", False):
        return 0
    iDepResult = await _fiCheckDependencies(
        connectionDocker, sContainerId, dictStep,
        dictVariables, iStepNumber, fnStatusCallback,
    )
    if iDepResult != 0:
        return iDepResult
    sStepName = dictStep.get("sName", f"Step {iStepNumber}")
    await _fnEmitBanner(fnStatusCallback, iStepNumber, sStepName)
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
    iExitCode = await fiRunStepCommands(
        connectionDocker, sContainerId,
        dictStep, sWorkdir, dictVariables, fnStatusCallback,
        iStepNumber=iStepNumber,
    )
    dictStep["dictRunStats"] = {
        "sLastRun": sStartTimestamp,
        "fWallClock": round(time.time() - fStartTime, 1),
    }
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
    iStartStep=1,
):
    """Iterate steps and run each eligible one from iStartStep."""
    iFinalExitCode = 0
    for iIndex, dictStep in enumerate(dictWorkflow["listSteps"]):
        iStepNumber = iIndex + 1
        if not _fbShouldRunStep(dictStep, iStepNumber, iStartStep):
            continue
        iExitCode = await _fnRunOneStep(
            connectionDocker, sContainerId, dictStep,
            iStepNumber, sWorkdir, dictVariables, fnStatusCallback,
        )
        if iExitCode != 0:
            iFinalExitCode = iExitCode
    return iFinalExitCode


async def fnRunFromStep(
    connectionDocker, sContainerId, iStartStep,
    sWorkdir, fnStatusCallback,
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
    )


async def _fbVerifyStepOutputs(
    connectionDocker, sContainerId,
    dictStep, sWorkdir, fnStatusCallback,
):
    """Return True if all output files for a step exist."""
    sStepDirectory = dictStep.get("sDirectory", sWorkdir)
    for sOutputFile in dictStep.get("saPlotFiles", []):
        sCheckCommand = f"test -f {fsShellQuote(sOutputFile)}"
        iExitCode, _ = connectionDocker.ftResultExecuteCommand(
            sContainerId, sCheckCommand, sWorkdir=sStepDirectory
        )
        if iExitCode != 0:
            await fnStatusCallback(
                {"sType": "output", "sLine": f"Missing: {sOutputFile}"}
            )
            return False
    return True


async def _fnVerifyStepList(
    connectionDocker, sContainerId, dictWorkflow,
    sWorkdir, fnStatusCallback,
):
    """Verify outputs for every step, returning True if all present."""
    bAllPresent = True
    for iIndex, dictStep in enumerate(dictWorkflow["listSteps"]):
        bStepOk = await _fbVerifyStepOutputs(
            connectionDocker, sContainerId,
            dictStep, sWorkdir, fnStatusCallback,
        )
        await _fnEmitStepResult(
            fnStatusCallback, iIndex + 1, 0 if bStepOk else 1
        )
        if not bStepOk:
            bAllPresent = False
    return bAllPresent


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
    bAllPresent = await _fnVerifyStepList(
        connectionDocker, sContainerId, dictWorkflow,
        sWorkdir, fnStatusCallback,
    )
    iExitCode = 0 if bAllPresent else 1
    await _fnEmitCompletion(fnStatusCallback, iExitCode)
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
