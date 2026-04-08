"""Preflight validation for pipeline steps."""

__all__ = []

from . import workflowManager
from .commandUtilities import fsExtractScriptPath
from .pipelineUtils import fsShellQuote


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
    from .pipelineLogger import fnWriteLogToContainer
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
