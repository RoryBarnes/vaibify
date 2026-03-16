"""Execute workflow steps by running commands directly in containers."""

import json
import re


PATTERN_STEP_LABEL = re.compile(
    r"\[Step(\d+)\]|Step(\d+):|=+\s*\n\s*Step(\d+)"
)
PATTERN_STEP_SUCCESS = re.compile(r"SUCCESS:\s*Step(\d+)")
PATTERN_STEP_FAILED = re.compile(r"FAILED:\s*Step(\d+)")


async def _fnRunSetupIfNeeded(
    connectionDocker, sContainerId, dictStep,
    sStepDirectory, fnStatusCallback,
):
    """Run setup commands unless bPlotOnly is True."""
    if dictStep.get("bPlotOnly", True):
        return 0
    return await _fnRunCommandList(
        connectionDocker, sContainerId,
        dictStep.get("saSetupCommands", []),
        sStepDirectory, fnStatusCallback,
    )


async def fnRunStepCommands(
    connectionDocker, sContainerId, dictStep,
    sWorkdir, fnStatusCallback,
):
    """Run a single step's commands sequentially in its directory."""
    sStepDirectory = dictStep.get("sDirectory", sWorkdir)
    iExitCode = await _fnRunSetupIfNeeded(
        connectionDocker, sContainerId, dictStep,
        sStepDirectory, fnStatusCallback,
    )
    if iExitCode != 0:
        return iExitCode
    return await _fnRunCommandList(
        connectionDocker, sContainerId,
        dictStep.get("saCommands", []),
        sStepDirectory, fnStatusCallback,
    )


async def _fnRunCommandList(
    connectionDocker, sContainerId, listCommands,
    sWorkdir, fnStatusCallback,
):
    """Execute a list of commands, returning first non-zero exit code."""
    for sCommand in listCommands:
        iExitCode = await _fnRunSingleCommand(
            connectionDocker, sContainerId,
            sCommand, sWorkdir, fnStatusCallback,
        )
        if iExitCode != 0:
            return iExitCode
    return 0


async def _fnRunSingleCommand(
    connectionDocker, sContainerId,
    sCommand, sWorkdir, fnStatusCallback,
):
    """Execute one command and stream its output lines."""
    await fnStatusCallback(
        {"sType": "output", "sLine": f"$ {sCommand}"}
    )
    iExitCode, sOutput = connectionDocker.ftResultExecuteCommand(
        sContainerId, sCommand, sWorkdir=sWorkdir
    )
    for sLine in sOutput.splitlines():
        await fnStatusCallback({"sType": "output", "sLine": sLine})
    return iExitCode


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
    """Load workflow.json from the container, returning None on failure."""
    from . import workflowManager

    listPaths = workflowManager.flistFindWorkflowsInContainer(
        connectionDocker, sContainerId
    )
    if not listPaths:
        await fnStatusCallback(
            {"sType": "error", "sMessage": "No workflow.json found"}
        )
        return None
    return workflowManager.fdictLoadWorkflowFromContainer(
        connectionDocker, sContainerId, listPaths[0]
    )


async def fnRunAllSteps(
    connectionDocker, sContainerId, sWorkdir, fnStatusCallback,
):
    """Run all enabled steps from the cached workflow."""
    dictWorkflow = await _fdictLoadWorkflow(
        connectionDocker, sContainerId, fnStatusCallback
    )
    if dictWorkflow is None:
        return 1
    await fnStatusCallback({"sType": "started", "sCommand": "runAll"})
    iResult = await _fnRunStepList(
        connectionDocker, sContainerId,
        dictWorkflow, sWorkdir, fnStatusCallback,
    )
    await _fnEmitCompletion(fnStatusCallback, iResult)
    return iResult


def _fbShouldRunStep(dictStep, iStepNumber, iStartStep):
    """Return True if this step should be executed."""
    if iStepNumber < iStartStep:
        return False
    return dictStep.get("bEnabled", True)


async def _fnRunOneStep(
    connectionDocker, sContainerId, dictStep,
    iStepNumber, sWorkdir, fnStatusCallback,
):
    """Run a single step and emit its result event."""
    iExitCode = await fnRunStepCommands(
        connectionDocker, sContainerId,
        dictStep, sWorkdir, fnStatusCallback,
    )
    await _fnEmitStepResult(fnStatusCallback, iStepNumber, iExitCode)
    return iExitCode


async def _fnRunStepList(
    connectionDocker, sContainerId,
    dictWorkflow, sWorkdir, fnStatusCallback,
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
            iStepNumber, sWorkdir, fnStatusCallback,
        )
        if iExitCode != 0:
            iFinalExitCode = iExitCode
    return iFinalExitCode


async def fnRunFromStep(
    connectionDocker, sContainerId, iStartStep,
    sWorkdir, fnStatusCallback,
):
    """Run steps starting from iStartStep (1-based)."""
    dictWorkflow = await _fdictLoadWorkflow(
        connectionDocker, sContainerId, fnStatusCallback
    )
    if dictWorkflow is None:
        return 1
    await fnStatusCallback(
        {"sType": "started", "sCommand": f"runFrom:{iStartStep}"}
    )
    iFinalExitCode = await _fnRunStepList(
        connectionDocker, sContainerId,
        dictWorkflow, sWorkdir, fnStatusCallback,
        iStartStep=iStartStep,
    )
    await _fnEmitCompletion(fnStatusCallback, iFinalExitCode)
    return iFinalExitCode


async def _fbVerifyStepOutputs(
    connectionDocker, sContainerId,
    dictStep, sWorkdir, fnStatusCallback,
):
    """Return True if all output files for a step exist."""
    sStepDirectory = dictStep.get("sDirectory", sWorkdir)
    for sOutputFile in dictStep.get("saOutputFiles", []):
        sCheckCommand = f"test -f {sOutputFile}"
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
    dictWorkflow = await _fdictLoadWorkflow(
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
    """Toggle steps, save, run, and emit completion."""
    from . import workflowManager

    _fnToggleSelectedSteps(dictWorkflow, listStepIndices)
    workflowManager.fnSaveWorkflowToContainer(
        connectionDocker, sContainerId, dictWorkflow, sWorkflowPath,
    )
    await fnStatusCallback(
        {"sType": "started", "sCommand": "runSelected"}
    )
    iResult = await _fnRunStepList(
        connectionDocker, sContainerId,
        dictWorkflow, sWorkdir, fnStatusCallback,
    )
    await _fnEmitCompletion(fnStatusCallback, iResult)
    return iResult


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
