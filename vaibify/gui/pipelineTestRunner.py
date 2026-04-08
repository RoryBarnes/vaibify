"""Test execution within pipeline steps."""

import posixpath
from datetime import datetime, timezone


def _flistResolveTestCommands(dictStep):
    """Return test commands from structured tests or legacy list."""
    from .workflowManager import flistResolveTestCommands
    return flistResolveTestCommands(dictStep)


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
    from .pipelineLogger import ffBuildLoggingCallback
    from .pipelineRunner import _ftRunCommandList

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


def _flistCollectCategoryLogs(dictCategoryResults):
    """Collect all log lines from category results."""
    listLines = []
    for dictResult in dictCategoryResults.values():
        sOutput = dictResult.get("sOutput", "")
        if sOutput:
            listLines.extend(sOutput.split("\n"))
    return listLines


async def _fnWriteTestLog(
    connectionDocker, sContainerId, iStepNumber, listLogLines,
):
    """Write test output to a separate log file."""
    from . import workflowManager
    from .pipelineLogger import fnWriteLogToContainer

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


async def fnRunAllTests(
    connectionDocker, sContainerId, sWorkdir, fnStatusCallback,
    dictWorkflow=None,
):
    """Run unit tests for all enabled steps."""
    from .pipelineRunner import (
        _fdictBuildWorkflowVars,
        _fdictLoadWorkflow,
        _fnEmitCompletion,
    )

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
    from .pipelineRunner import _fnEmitBanner, fsComputeStepLabel

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
    from .pipelineRunner import _fnEmitStepResult, _fnRecordInputHashes

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
