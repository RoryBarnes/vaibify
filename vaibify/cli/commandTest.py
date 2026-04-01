"""CLI subcommand: vaibify test."""

import sys

import click

from .configLoader import fconfigResolveProject
from .commandUtilsDocker import (
    fconnectionRequireDocker,
    fsRequireRunningContainer,
    fdictRequireWorkflow,
    fnPrintJson,
    fbShouldOutputJson,
)


def _flistCollectTestCommands(dictStep):
    """Return all test commands from both legacy and new formats."""
    from vaibify.gui.workflowManager import flistBuildTestCommands
    listCommands = list(dictStep.get("saTestCommands", []))
    listFromDict = flistBuildTestCommands(dictStep)
    for sCommand in listFromDict:
        if sCommand not in listCommands:
            listCommands.append(sCommand)
    return listCommands


def _fdictRunStepTests(
    connectionDocker, sContainerName, dictStep, iStepIndex
):
    """Run tests for a single step and return a result dict."""
    sStepDirectory = dictStep.get("sDirectory", "/workspace")
    listCommands = _flistCollectTestCommands(dictStep)
    if not listCommands:
        return _fdictBuildStepResult(
            iStepIndex, dictStep, "skipped", 0, "No test commands"
        )
    iExitCode = _fiRunTestCommandList(
        connectionDocker, sContainerName, listCommands, sStepDirectory
    )
    sStatus = "passed" if iExitCode == 0 else "failed"
    return _fdictBuildStepResult(
        iStepIndex, dictStep, sStatus, iExitCode, ""
    )


def _fdictBuildStepResult(
    iStepIndex, dictStep, sStatus, iExitCode, sMessage
):
    """Build a result dict for one step's test run."""
    return {
        "iNumber": iStepIndex + 1,
        "sName": dictStep.get("sName", ""),
        "sStatus": sStatus,
        "iExitCode": iExitCode,
        "sMessage": sMessage,
    }


def _fiRunTestCommandList(
    connectionDocker, sContainerName, listCommands, sWorkdir
):
    """Run a list of test commands, returning first non-zero exit code."""
    for sCommand in listCommands:
        iExitCode, sOutput = connectionDocker.ftResultExecuteCommand(
            sContainerName, sCommand, sWorkdir=sWorkdir
        )
        click.echo(sOutput.rstrip())
        if iExitCode != 0:
            return iExitCode
    return 0


def _fnPrintTestResults(listResults):
    """Print test results as a human-readable table."""
    click.echo(f"{'#':>3}  {'Name':<30}  {'Status':<10}  {'Exit'}")
    click.echo("-" * 55)
    for dictResult in listResults:
        _fnPrintTestRow(dictResult)


def _fnPrintTestRow(dictResult):
    """Print one row of test results."""
    iNumber = dictResult["iNumber"]
    sName = dictResult["sName"][:30]
    sStatus = dictResult["sStatus"]
    iExitCode = dictResult["iExitCode"]
    click.echo(f"{iNumber:>3}  {sName:<30}  {sStatus:<10}  {iExitCode}")


@click.command("test")
@click.option(
    "--project", "-p", "sProjectName", default=None,
    help="Project name.",
)
@click.option(
    "--step", "iStep", default=None, type=int,
    help="Test only this step (1-based).",
)
@click.option(
    "--json", "bJson", is_flag=True, default=False,
    help="Output in JSON format.",
)
def test(sProjectName, iStep, bJson):
    """Run tests for pipeline steps."""
    configProject = fconfigResolveProject(sProjectName)
    connectionDocker = fconnectionRequireDocker()
    sContainerName = fsRequireRunningContainer(configProject)
    dictResult = fdictRequireWorkflow(connectionDocker, sContainerName)
    dictWorkflow = dictResult["dictWorkflow"]
    listSteps = dictWorkflow.get("listSteps", [])
    if iStep is not None:
        iStepIndex = iStep - 1
        if iStepIndex < 0 or iStepIndex >= len(listSteps):
            click.echo(
                f"Error: Step {iStep} out of range "
                f"(1-{len(listSteps)})."
            )
            sys.exit(2)
        listStepIndices = [iStepIndex]
    else:
        listStepIndices = list(range(len(listSteps)))
    listResults = _flistRunAllTests(
        connectionDocker, sContainerName, listSteps, listStepIndices
    )
    bAnyFailed = any(
        dictResult["sStatus"] == "failed" for dictResult in listResults
    )
    if fbShouldOutputJson(bJson):
        fnPrintJson({"listResults": listResults, "bAllPassed": not bAnyFailed})
    else:
        _fnPrintTestResults(listResults)
    if bAnyFailed:
        sys.exit(1)


def _flistRunAllTests(
    connectionDocker, sContainerName, listSteps, listStepIndices
):
    """Run tests for the specified step indices and return results."""
    listResults = []
    for iStepIndex in listStepIndices:
        dictResult = _fdictRunStepTests(
            connectionDocker, sContainerName,
            listSteps[iStepIndex], iStepIndex,
        )
        listResults.append(dictResult)
    return listResults
