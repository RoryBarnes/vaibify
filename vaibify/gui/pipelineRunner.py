"""Execute workflow steps by running commands directly in containers."""

import asyncio
import json
import posixpath
from datetime import datetime, timezone

from . import pipelineState
from . import workflowManager

__all__ = [
    "fsShellQuote",
    "fnRunAllSteps",
    "fnRunFromStep",
    "fnRunSelectedSteps",
    "fnVerifyOnly",
    "fnRunAllTests",
    "fsGenerateLogFilename",
    "fdictCreateInteractiveContext",
    "fnSetInteractiveResponse",
    "fsComputeStepLabel",
    "fnClearOutputModifiedFlags",
    "ffBuildLoggingCallback",
    "fnWriteLogToContainer",
]

# ---------------------------------------------------------------------------
# Re-exports from pipelineUtils (true leaf — breaks circular imports).
# ---------------------------------------------------------------------------

from .pipelineUtils import (  # noqa: F401
    fsShellQuote,
    fsComputeStepLabel,
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
    I_MAX_LOG_LINES,
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

from .fileIntegrity import (  # noqa: F401
    fbStepInputsUnchanged,
    fdictComputeInputHashes,
)


# ---------------------------------------------------------------------------
# Preflight validation (kept here for mockability via module namespace).
# ---------------------------------------------------------------------------

async def _flistPreflightValidate(
    connectionDocker, sContainerId, dictWorkflow, dictVariables,
    iStartStep=1,
):
    """Validate step directories and scripts exist before running."""
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
# Core command execution
# ---------------------------------------------------------------------------

async def _ftRunCommandList(
    connectionDocker, sContainerId, listCommands,
    sWorkdir, dictVariables, fnStatusCallback,
):
    """Execute commands, return (iExitCode, fTotalCpuSeconds)."""
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
    iStepNumber=0,
):
    """Run a single step's commands sequentially in its directory."""
    from .pipelineTestRunner import _fiRunTestCommands

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


async def _fbVerifyStepOutputs(
    connectionDocker, sContainerId,
    dictStep, dictVars, sWorkdir, fnStatusCallback,
):
    """Return True if all output files for a step exist."""
    sStepDirectory = dictStep.get("sDirectory", sWorkdir)
    listOutputFiles = (
        dictStep.get("saPlotFiles", [])
        + dictStep.get("saDataFiles", [])
    )
    for sOutputFile in listOutputFiles:
        sResolved = workflowManager.fsResolveVariables(
            sOutputFile, dictVars
        )
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


# ---------------------------------------------------------------------------
# Dependency and skip checks
# ---------------------------------------------------------------------------

def _fbShouldRunStep(dictStep, iStepNumber, iStartStep):
    """Return True if this step should be executed."""
    if iStepNumber < iStartStep:
        return False
    return dictStep.get("bEnabled", True)


async def _fsMissingDependencyFile(
    connectionDocker, sContainerId, dictStep, dictVariables,
):
    """Return the first missing dependency path, or empty string."""
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
    return fbStepInputsUnchanged(
        connectionDocker, sContainerId, dictStep, iStepNumber
    )


async def _fnRecordInputHashes(
    connectionDocker, sContainerId, dictStep,
):
    """Compute and store input hashes after a step runs."""
    dictHashes = fdictComputeInputHashes(
        connectionDocker, sContainerId, dictStep
    )
    if "dictRunStats" not in dictStep:
        dictStep["dictRunStats"] = {}
    dictStep["dictRunStats"]["dictInputHashes"] = dictHashes


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


# ---------------------------------------------------------------------------
# Main orchestration loop
# ---------------------------------------------------------------------------

async def _fiRunStepList(
    connectionDocker, sContainerId,
    dictWorkflow, sWorkdir, dictVariables, fnStatusCallback,
    iStartStep=1, dictInteractive=None,
):
    """Iterate steps, pausing at interactive ones."""
    from .interactiveSteps import _fiHandleInteractiveStep

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


async def _fiRunStepsAndLog(
    connectionDocker, sContainerId, dictWorkflow, sWorkdir,
    dictVariables, fnLogging, fnStatusCallback,
    sLogPath, listLogLines, sAction, iStartStep,
    sWorkflowPath="", dictInteractive=None,
):
    """Execute steps, write log, and emit final status."""
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


# ---------------------------------------------------------------------------
# Workflow loading
# ---------------------------------------------------------------------------

async def _fdictLoadWorkflow(connectionDocker, sContainerId, fnStatusCallback):
    """Load workflow.json from the container, returning (dict, path)."""
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


# ---------------------------------------------------------------------------
# Public API: top-level entry points
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Step selection helpers
# ---------------------------------------------------------------------------

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
