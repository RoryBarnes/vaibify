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


def _fiResolveStepNumber(sStep, dictWorkflow):
    """Return the 1-based step number for a label or a number.

    Researchers speak labels (``A09``, ``I01``); the rest of vaibify —
    error messages, the dashboard, the agent commands — uses them too,
    so this command accepts them rather than making the researcher
    count rows. The translation is the single labeller in
    ``pipelineUtils``, never an inline one.
    """
    from vaibify.gui.pipelineUtils import fiStepIndexFromLabel
    try:
        return int(sStep)
    except (TypeError, ValueError):
        pass
    try:
        return fiStepIndexFromLabel(dictWorkflow, str(sStep)) + 1
    except (KeyError, ValueError, IndexError):
        click.echo(
            f"Error: '{sStep}' is neither a step number nor a step "
            f"label present in this project."
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


def _fnRecordDeclarationBaseline(
    connectionDocker, sContainerName, dictWorkflow, iStepIndex, sStatus,
):
    """Record what an AI Declaration sign-off covers, as the dashboard does.

    Without this, a sign-off made here would carry no baseline and read
    as a legacy sign-off whose later changes are never tracked. Exits
    rather than signing when the covered files cannot be read.
    """
    from vaibify.reproducibility.aiDeclarationStep import (
        fbStepIsAiDeclaration,
    )
    from vaibify.reproducibility.declarationFreshness import (
        S_BASELINE_KEY, DeclarationEvidenceUnreadableError,
        fdictBuildDeclarationBaseline, fnClearDeclarationBaseline,
    )
    from vaibify.reproducibility.repoFiles import ContainerRepoFiles
    dictStep = dictWorkflow["listSteps"][iStepIndex]
    if not fbStepIsAiDeclaration(dictStep):
        return
    dictVerification = dictStep.setdefault("dictVerification", {})
    fnClearDeclarationBaseline(dictVerification)
    if sStatus != "passed":
        return
    filesRepo = ContainerRepoFiles(
        connectionDocker, sContainerName,
        dictWorkflow.get("sProjectRepoPath", ""))
    try:
        dictVerification[S_BASELINE_KEY] = fdictBuildDeclarationBaseline(
            dictWorkflow, filesRepo, "")
    except DeclarationEvidenceUnreadableError as error:
        click.echo(
            f"Error: the AI Declaration was not signed off: {error}, "
            "so vaibify could not record what the sign-off covers.")
        sys.exit(1)


@click.command("verify-step")
@click.option(
    "--project", "-p", "sProjectName", default=None,
    help="Project name.",
)
@click.option(
    "--step", "sStep", required=True,
    help="Step to verify: a label (A09, I01) or a 1-based number.",
)
@click.option(
    "--status", "sStatus", required=True,
    type=click.Choice(T_VALID_STATUSES, case_sensitive=False),
    help="Verification status to set.",
)
def fnVerifyStepCommand(sProjectName, sStep, sStatus):
    """Set the user verification status for a pipeline step."""
    _fnValidateStatus(sStatus)
    configProject = fconfigResolveProject(sProjectName)
    connectionDocker = fconnectionRequireDocker()
    sContainerName = fsRequireRunningContainer(configProject)
    dictResult = fdictRequireWorkflow(connectionDocker, sContainerName)
    dictWorkflow = dictResult["dictWorkflow"]
    sWorkflowPath = dictResult["sWorkflowPath"]
    listSteps = dictWorkflow.get("listSteps", [])
    iStep = _fiResolveStepNumber(sStep, dictWorkflow)
    _fnValidateStepIndex(iStep, len(listSteps))
    iStepIndex = iStep - 1
    _fnRecordDeclarationBaseline(
        connectionDocker, sContainerName, dictWorkflow, iStepIndex,
        sStatus,
    )
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
