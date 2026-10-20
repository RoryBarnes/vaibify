"""CLI subcommand: vaibify verify-step.

A sign-off is recorded by the dashboard's own route,
``PUT /api/steps/{id}/{index}``, on the researcher lane. This command
used to write ``sUser`` straight into ``project.json``, which recorded
the verdict without the two things that make it an attestation: the
container clock's ``sLastUserUpdate`` and the hashes of the plots it
covers (``dictUserVerifiedHashes``, and for the AI Declaration its
baseline). Every later freshness check reads those, so a CLI sign-off
read as a legacy one whose later changes were never tracked. The route
does all of it, from one authority, for both surfaces.
"""

import sys

import click

from . import hubSession
from .configLoader import fconfigResolveProject


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


def _fiResolveStepIndex(dictSession, sStep):
    """Return the 0-based index of a label (A09, I01) or a 1-based number."""
    try:
        return int(sStep) - 1
    except (TypeError, ValueError):
        return hubSession.fiResolveStepLabel(dictSession, str(sStep))


def _fdictReadStep(dictSession, iStepIndex):
    """Return the step as the hub holds it, or raise naming the step."""
    iStatusCode, jsonBody = hubSession.ftSendSessionRequest(
        dictSession, "GET", "/api/steps/%s/%d" % (
            dictSession["sContainerId"], iStepIndex))
    if iStatusCode != 200 or not isinstance(jsonBody, dict):
        raise hubSession.HubSessionError(
            "step %d is not in this project (HTTP %d)"
            % (iStepIndex + 1, iStatusCode))
    return jsonBody


def fdictSignOffBody(dictStep, sStatus):
    """Return the update the dashboard sends: the stored block, sUser set.

    The route replaces the whole verification block and computes the
    timestamp and the signed-off hashes itself, so the block is sent as
    stored with only the verdict changed.
    """
    dictVerification = dict(dictStep.get("dictVerification") or {})
    dictVerification["sUser"] = sStatus
    return {"dictVerification": dictVerification}


def _ftSignOff(dictSession, sStep, sStatus):
    """Record the sign-off through the route; return (status, body, step)."""
    iStepIndex = _fiResolveStepIndex(dictSession, sStep)
    dictStep = _fdictReadStep(dictSession, iStepIndex)
    iStatusCode, jsonBody = hubSession.ftSendSessionRequest(
        dictSession, "PUT", "/api/steps/%s/%d" % (
            dictSession["sContainerId"], iStepIndex),
        fdictSignOffBody(dictStep, sStatus))
    return iStatusCode, jsonBody, dictStep


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
@click.option("--port", "iPort", default=None, type=int,
              help="Port of the vaibify session to drive.")
def fnVerifyStepCommand(sProjectName, sStep, sStatus, iPort):
    """Set the user verification status for a pipeline step."""
    _fnValidateStatus(sStatus)
    sContainerName = fconfigResolveProject(sProjectName).sProjectName
    try:
        dictSession = hubSession.fdictOpenResearcherSession(
            sContainerName, iPort)
        try:
            iStatusCode, jsonBody, dictStep = _ftSignOff(
                dictSession, sStep, sStatus)
        finally:
            hubSession.fnReleaseContainer(dictSession)
    except hubSession.HubSessionError as error:
        click.echo("Error: %s" % error, err=True)
        sys.exit(4)
    if not 200 <= iStatusCode < 300:
        sDetail = (jsonBody or {}).get("detail", jsonBody) if isinstance(
            jsonBody, dict) else jsonBody
        click.echo("Error: the sign-off was not recorded (HTTP %d): %s"
                   % (iStatusCode, sDetail), err=True)
        sys.exit(1)
    click.echo(
        f"Step {sStep} ({dictStep.get('sName', '')}): "
        f"user verification set to '{sStatus}'."
    )
