"""Execute workflow steps by running commands directly in containers."""

import asyncio
import logging
import os
import posixpath
import threading

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

# ---------------------------------------------------------------------------
# Preflight validation (kept here for mockability via module namespace).
# ---------------------------------------------------------------------------

async def _flistPreflightValidate(
    connectionDocker, sContainerId, dictWorkflow, dictVariables,
    iStartStep=1, setRunStepIndices=None,
):
    """Validate step directories and scripts exist before running."""
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
    sCmd = (
        f"git -C {fsShellQuote(sProjectRepoPath)} "
        f"log -1 --format=%ct HEAD 2>/dev/null"
    )
    iExitCode, sOutput = await asyncio.to_thread(
        connectionDocker.ftResultExecuteCommand,
        sContainerId, sCmd,
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
    """Compute the env prefix once and stash it in dictVariables."""
    sProjectRepoPath = dictWorkflow.get("sProjectRepoPath", "")
    sEnvPrefix = await _fsBuildDeterminismEnvPrefix(
        connectionDocker, sContainerId, sProjectRepoPath,
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
    """Execute one command, return (iExitCode, fCpuSeconds)."""
    await _fnEmitCommandHeader(
        fnStatusCallback, sOriginal, sResolved
    )
    sTimedCmd = _fsWrapWithTime(sEnvPrefix + sResolved)
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


async def _fbVerifyStepOutputs(
    connectionDocker, sContainerId,
    dictStep, dictVars, sWorkdir, fnStatusCallback,
):
    """Return True if all output files for a step exist."""
    sStepDirectory = workflowManager.fsResolveStepWorkdir(
        dictStep.get("sDirectory", sWorkdir), dictVars,
    )
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


async def _fiRunStepsAndLog(
    connectionDocker, sContainerId, dictWorkflow, sWorkdir,
    dictVariables, fnLogging, fnStatusCallback,
    sLogPath, listLogLines, sAction, iStartStep,
    sWorkflowPath="", dictInteractive=None, sRunMode="full",
    setRunStepIndices=None,
):
    """Execute steps, write log, and emit final status."""
    iStepCount = len(dictWorkflow.get("listSteps", []))
    dictState = pipelineState.fdictBuildInitialState(
        sAction, sLogPath, iStepCount, iRunnerPid=os.getpid()
    )
    lockState = threading.Lock()
    eventStopHeartbeat = threading.Event()
    with lockState:
        pipelineState.fnWriteState(
            connectionDocker, sContainerId, dictState
        )
    threadHeartbeat = _fnStartHeartbeatThread(
        connectionDocker, sContainerId, dictState,
        lockState, eventStopHeartbeat,
    )
    try:
        await fnLogging({"sType": "started", "sCommand": sAction})
        fnLoggingWithFlush = _ffBuildFlushingCallback(
            fnLogging, connectionDocker, sContainerId,
            dictState, sLogPath, listLogLines, lockState,
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
            fnStatusCallback, lockState,
        )
    finally:
        eventStopHeartbeat.set()
        threadHeartbeat.join(timeout=2)
    return iResult


def _fnStartHeartbeatThread(
    connectionDocker, sContainerId, dictState, lockState, eventStop,
):
    """Spawn a daemon thread that refreshes ``sLastHeartbeat`` periodically."""
    threadHeartbeat = threading.Thread(
        target=_fnRunHeartbeatLoop,
        args=(connectionDocker, sContainerId, dictState,
              lockState, eventStop),
        name="vaibify-pipeline-heartbeat",
        daemon=True,
    )
    threadHeartbeat.start()
    return threadHeartbeat


def _fnRunHeartbeatLoop(
    connectionDocker, sContainerId, dictState, lockState, eventStop,
):
    """Tick ``sLastHeartbeat`` until ``eventStop`` is set."""
    fInterval = pipelineState.I_HEARTBEAT_INTERVAL_SECONDS
    while not eventStop.wait(fInterval):
        try:
            with lockState:
                pipelineState.fnUpdateState(
                    connectionDocker, sContainerId, dictState,
                    pipelineState.fdictBuildHeartbeatUpdate(),
                )
        except Exception as error:
            logging.getLogger("vaibify").warning(
                "pipeline heartbeat write failed: %s", error)


async def _fiRunWithLogging(
    connectionDocker, sContainerId, dictWorkflow,
    sWorkdir, fnStatusCallback, sAction, iStartStep=1,
    sWorkflowPath="", dictInteractive=None, sRunMode="full",
    setRunStepIndices=None,
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
    await _fnInjectDeterminismEnvPrefix(
        connectionDocker, sContainerId, dictWorkflow, dictVariables,
    )
    fnClearOutputModifiedFlags(dictWorkflow)

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
