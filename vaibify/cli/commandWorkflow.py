"""CLI subcommand: vaibify workflow."""

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


def _fnPrintStepTable(dictWorkflow):
    """Print a human-readable table of workflow steps."""
    listSteps = dictWorkflow.get("listSteps", [])
    sWorkflowName = dictWorkflow.get("sWorkflowName", "(unnamed)")
    click.echo(f"Workflow: {sWorkflowName}")
    click.echo(f"Steps: {len(listSteps)}")
    click.echo("")
    click.echo(f"{'#':>3}  {'Name':<30}  {'Status':<10}  {'Last Run'}")
    click.echo("-" * 70)
    for iIndex, dictStep in enumerate(listSteps):
        _fnPrintStepRow(iIndex, dictStep)


def _fnPrintStepRow(iIndex, dictStep):
    """Print one row of the step summary table."""
    iNumber = iIndex + 1
    sName = dictStep.get("sName", "")[:30]
    dictVerification = dictStep.get("dictVerification", {})
    sStatus = dictVerification.get("sUser", "untested")
    dictRunStats = dictStep.get("dictRunStats", {})
    sLastRun = dictRunStats.get("sLastRun", "-")
    click.echo(f"{iNumber:>3}  {sName:<30}  {sStatus:<10}  {sLastRun}")


def _fdictStepDetail(iStepIndex, dictStep):
    """Build a detail dict for a single step."""
    return {
        "iNumber": iStepIndex + 1,
        "sName": dictStep.get("sName", ""),
        "sDirectory": dictStep.get("sDirectory", ""),
        "bEnabled": dictStep.get("bEnabled", True),
        "bPlotOnly": dictStep.get("bPlotOnly", True),
        "bInteractive": dictStep.get("bInteractive", False),
        "dictVerification": dictStep.get("dictVerification", {}),
        "dictRunStats": dictStep.get("dictRunStats", {}),
        "saDataCommands": dictStep.get("saDataCommands", []),
        "saPlotCommands": dictStep.get("saPlotCommands", []),
        "saTestCommands": dictStep.get("saTestCommands", []),
    }


def _fnPrintStepDetail(iStepIndex, dictStep):
    """Print detailed info for a single step."""
    dictDetail = _fdictStepDetail(iStepIndex, dictStep)
    click.echo(f"Step {dictDetail['iNumber']}: {dictDetail['sName']}")
    click.echo(f"  Directory:   {dictDetail['sDirectory']}")
    click.echo(f"  Enabled:     {dictDetail['bEnabled']}")
    click.echo(f"  Plot only:   {dictDetail['bPlotOnly']}")
    click.echo(f"  Interactive: {dictDetail['bInteractive']}")
    dictVerification = dictDetail["dictVerification"]
    click.echo(f"  User status: {dictVerification.get('sUser', 'untested')}")
    dictRunStats = dictDetail["dictRunStats"]
    sLastRun = dictRunStats.get("sLastRun", "-")
    click.echo(f"  Last run:    {sLastRun}")


def _fdictWorkflowSummary(dictWorkflow):
    """Build a JSON-friendly summary of the workflow."""
    listStepSummaries = []
    for iIndex, dictStep in enumerate(dictWorkflow.get("listSteps", [])):
        listStepSummaries.append(_fdictStepDetail(iIndex, dictStep))
    return {
        "sWorkflowName": dictWorkflow.get("sWorkflowName", ""),
        "iStepCount": len(listStepSummaries),
        "listSteps": listStepSummaries,
    }


@click.command("workflow")
@click.option(
    "--project", "-p", "sProjectName", default=None,
    help="Project name.",
)
@click.option(
    "--step", "iStep", default=None, type=int,
    help="Show detail for this step (1-based).",
)
@click.option(
    "--json", "bJson", is_flag=True, default=False,
    help="Output in JSON format.",
)
def workflow(sProjectName, iStep, bJson):
    """Print workflow summary or step details."""
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
        if fbShouldOutputJson(bJson):
            fnPrintJson(_fdictStepDetail(iStepIndex, listSteps[iStepIndex]))
        else:
            _fnPrintStepDetail(iStepIndex, listSteps[iStepIndex])
        return
    if fbShouldOutputJson(bJson):
        fnPrintJson(_fdictWorkflowSummary(dictWorkflow))
    else:
        _fnPrintStepTable(dictWorkflow)
