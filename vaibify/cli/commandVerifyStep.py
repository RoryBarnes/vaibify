"""CLI subcommand: vaibify verify-step."""

import sys

import click

from .configLoader import fconfigResolveProject
from .commandUtilsDocker import (
    fconnectionRequireDocker,
    fsRequireRunningContainer,
    fdictRequireWorkflow,
)


T_VALID_STATUSES = ("passed", "failed", "untested")


def _fnValidateStatus(sStatus):
    """Exit if the status value is not recognized."""
    if sStatus not in T_VALID_STATUSES:
        sAllowed = ", ".join(T_VALID_STATUSES)
        click.echo(
            f"Error: Invalid status '{sStatus}'. "
            f"Allowed values: {sAllowed}"
        )
        sys.exit(2)


def _fnValidateStepIndex(iStep, iStepCount):
    """Exit if the step index is out of range."""
    if iStep < 1 or iStep > iStepCount:
        click.echo(
            f"Error: Step {iStep} out of range (1-{iStepCount})."
        )
        sys.exit(2)


def _fnSetUserVerification(dictWorkflow, iStepIndex, sStatus):
    """Set the sUser verification field on the specified step."""
    dictStep = dictWorkflow["listSteps"][iStepIndex]
    if "dictVerification" not in dictStep:
        dictStep["dictVerification"] = {}
    dictStep["dictVerification"]["sUser"] = sStatus


@click.command("verify-step")
@click.option(
    "--project", "-p", "sProjectName", default=None,
    help="Project name.",
)
@click.option(
    "--step", "iStep", required=True, type=int,
    help="Step number to verify (1-based).",
)
@click.option(
    "--status", "sStatus", required=True,
    type=click.Choice(T_VALID_STATUSES, case_sensitive=False),
    help="Verification status to set.",
)
def verify_step(sProjectName, iStep, sStatus):
    """Set the user verification status for a pipeline step."""
    _fnValidateStatus(sStatus)
    configProject = fconfigResolveProject(sProjectName)
    connectionDocker = fconnectionRequireDocker()
    sContainerName = fsRequireRunningContainer(configProject)
    dictResult = fdictRequireWorkflow(connectionDocker, sContainerName)
    dictWorkflow = dictResult["dictWorkflow"]
    sWorkflowPath = dictResult["sWorkflowPath"]
    listSteps = dictWorkflow.get("listSteps", [])
    _fnValidateStepIndex(iStep, len(listSteps))
    iStepIndex = iStep - 1
    _fnSetUserVerification(dictWorkflow, iStepIndex, sStatus)
    _fnSaveWorkflow(
        connectionDocker, sContainerName, dictWorkflow, sWorkflowPath
    )
    sStepName = listSteps[iStepIndex].get("sName", "")
    click.echo(
        f"Step {iStep} ({sStepName}): "
        f"user verification set to '{sStatus}'."
    )


def _fnSaveWorkflow(
    connectionDocker, sContainerName, dictWorkflow, sWorkflowPath
):
    """Save the updated workflow back to the container."""
    from vaibify.gui.workflowManager import fnSaveWorkflowToContainer
    fnSaveWorkflowToContainer(
        connectionDocker, sContainerName, dictWorkflow, sWorkflowPath
    )
