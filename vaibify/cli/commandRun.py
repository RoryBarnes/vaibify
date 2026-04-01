"""CLI subcommand: vaibify run."""

import asyncio
import sys

import click

from .configLoader import fconfigResolveProject
from .commandUtilsDocker import (
    fconnectionRequireDocker,
    fsRequireRunningContainer,
)


def fnCliStatusCallback(dictEvent):
    """Print step progress events to stdout."""
    sType = dictEvent.get("sType", "")
    if sType == "output":
        click.echo(dictEvent.get("sLine", ""))
    elif sType == "stepStarted":
        iStep = dictEvent.get("iStepNumber", 0)
        click.echo(f"[step {iStep:02d}] started")
    elif sType == "stepPass":
        iStep = dictEvent.get("iStepNumber", 0)
        click.echo(f"[step {iStep:02d}] passed")
    elif sType == "stepFail":
        iStep = dictEvent.get("iStepNumber", 0)
        click.echo(f"[step {iStep:02d}] FAILED")
    elif sType == "stepSkipped":
        iStep = dictEvent.get("iStepNumber", 0)
        click.echo(f"[step {iStep:02d}] skipped")
    elif sType == "completed":
        click.echo("Pipeline completed successfully.")
    elif sType == "failed":
        iExitCode = dictEvent.get("iExitCode", 1)
        click.echo(f"Pipeline failed (exit {iExitCode}).")
    elif sType == "error":
        click.echo(f"Error: {dictEvent.get('sMessage', '')}")


async def _fnAsyncStatusCallback(dictEvent):
    """Async wrapper around fnCliStatusCallback."""
    fnCliStatusCallback(dictEvent)


def _fnValidateStepOptions(iStep, iFrom):
    """Exit if both --step and --from are provided."""
    if iStep is not None and iFrom is not None:
        click.echo("Error: --step and --from are mutually exclusive.")
        sys.exit(2)


def _fiRunPipeline(connectionDocker, sContainerName, iStep, iFrom):
    """Dispatch to the correct pipeline runner function."""
    from vaibify.gui.pipelineRunner import (
        fnRunAllSteps, fnRunFromStep, fnRunSelectedSteps,
    )
    from vaibify.gui.workflowManager import (
        fdictLoadWorkflowFromContainer, flistFindWorkflowsInContainer,
    )
    sWorkdir = "/workspace"
    if iStep is not None:
        return _fiRunSingleStep(
            connectionDocker, sContainerName, iStep, sWorkdir
        )
    if iFrom is not None:
        return asyncio.run(fnRunFromStep(
            connectionDocker, sContainerName, iFrom,
            sWorkdir, _fnAsyncStatusCallback,
        ))
    return asyncio.run(fnRunAllSteps(
        connectionDocker, sContainerName,
        sWorkdir, _fnAsyncStatusCallback,
    ))


def _fiRunSingleStep(connectionDocker, sContainerName, iStep, sWorkdir):
    """Run a single step by index (1-based)."""
    from vaibify.gui.pipelineRunner import fnRunSelectedSteps
    from vaibify.gui.workflowManager import (
        fdictLoadWorkflowFromContainer, flistFindWorkflowsInContainer,
    )
    listWorkflows = flistFindWorkflowsInContainer(
        connectionDocker, sContainerName
    )
    if not listWorkflows:
        click.echo("Error: No workflow found in container.")
        return 2
    sWorkflowPath = listWorkflows[0]["sPath"]
    dictWorkflow = fdictLoadWorkflowFromContainer(
        connectionDocker, sContainerName, sWorkflowPath
    )
    iStepIndex = iStep - 1
    iStepCount = len(dictWorkflow.get("listSteps", []))
    if iStepIndex < 0 or iStepIndex >= iStepCount:
        click.echo(
            f"Error: Step {iStep} out of range (1-{iStepCount})."
        )
        return 2
    return asyncio.run(fnRunSelectedSteps(
        connectionDocker, sContainerName,
        [iStepIndex], dictWorkflow, sWorkflowPath,
        sWorkdir, _fnAsyncStatusCallback,
    ))


@click.command("run")
@click.option(
    "--project", "-p", "sProjectName", default=None,
    help="Project name.",
)
@click.option(
    "--step", "iStep", default=None, type=int,
    help="Run only this step (1-based).",
)
@click.option(
    "--from", "iFrom", default=None, type=int,
    help="Run from this step onward (1-based).",
)
def run(sProjectName, iStep, iFrom):
    """Run pipeline steps in the container."""
    _fnValidateStepOptions(iStep, iFrom)
    configProject = fconfigResolveProject(sProjectName)
    connectionDocker = fconnectionRequireDocker()
    sContainerName = fsRequireRunningContainer(configProject)
    iExitCode = _fiRunPipeline(
        connectionDocker, sContainerName, iStep, iFrom
    )
    if iExitCode != 0:
        sys.exit(1 if iExitCode != 2 else 2)
