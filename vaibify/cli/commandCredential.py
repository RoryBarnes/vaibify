"""CLI subcommand: ``vaibify credential set|status|delete``.

The command-line equivalent of the dashboard's credential forms
(``POST /api/sync/{id}/setup``), so a project can be published to
GitHub, Zenodo and Overleaf with no browser. The token is read at a
hidden prompt and travels ONLY in the JSON body of that one request to
the local hub: never on the command line (where ``ps`` and shell
history see it), never in an environment variable, never in a log line
or an error message. ``delete`` is ``vaibify revoke`` under the same
roof; ``revoke`` stays for anyone who already uses it.
"""

import sys

import click

from . import hubSession
from .commandRemote import DeclaredNameArgument
from .commandRevoke import (
    _LIST_VALID_SERVICES,
    _LIST_VALID_ZENODO_INSTANCES,
    _fdictRevokeForService,
    fnPrintRevocationReport,
)


S_SETUP_ROUTE = "/api/sync/%s/setup"
S_STATUS_ROUTE = "/api/sync/%s/status"


def _fsResolveContainerName(sProjectName):
    from .configLoader import fconfigResolveProject
    return fconfigResolveProject(sProjectName).sProjectName


def fdictBuildSetupBody(sService, sToken, sProjectId, sZenodoInstance):
    """Return the setup request body the dashboard's form sends."""
    return {"sService": sService, "sProjectId": sProjectId or "",
            "sToken": sToken, "sZenodoInstance": sZenodoInstance}


def _fiSendUnderLease(sProjectName, iPort, sMethod, sRoute, dictBody=None):
    """Claim the project, send one request, release; return the exit code."""
    dictSession = hubSession.fdictOpenResearcherSession(
        _fsResolveContainerName(sProjectName), iPort)
    try:
        iStatusCode, jsonBody = hubSession.ftSendSessionRequest(
            dictSession, sMethod, sRoute % dictSession["sContainerId"],
            dictBody)
    finally:
        hubSession.fnReleaseContainer(dictSession)
    hubSession._fnPrintPayload(jsonBody, False)
    return 0 if 200 <= iStatusCode < 300 else 1


@click.group("credential")
def fnCredentialCommand():
    """Store, inspect, or delete the credentials vaibify publishes with."""


@fnCredentialCommand.command("set")
@click.argument("sService", metavar="SERVICE", cls=DeclaredNameArgument,
                type=click.Choice(_LIST_VALID_SERVICES, case_sensitive=False))
@click.option("--project", "-p", "sProjectName", default=None,
              help="Project name (omit inside a project directory).")
@click.option("--project-id", "sProjectId", default="",
              help="Overleaf: the Overleaf project id to bind.")
@click.option("--instance", "sZenodoInstance",
              type=click.Choice(_LIST_VALID_ZENODO_INSTANCES,
                                case_sensitive=False),
              default="sandbox", help="Zenodo: sandbox or production.")
@click.option("--port", "iPort", default=None, type=int,
              help="Port of the vaibify session to drive.")
def fnSetCredentialCommand(sService, sProjectName, sProjectId,
                           sZenodoInstance, iPort):
    """Store a token for SERVICE; asked for at a hidden prompt."""
    sToken = click.prompt("%s token" % sService, hide_input=True)
    try:
        iExitCode = _fiSendUnderLease(
            sProjectName, iPort, "POST", S_SETUP_ROUTE,
            fdictBuildSetupBody(sService.lower(), sToken, sProjectId,
                                sZenodoInstance.lower()))
    except hubSession.HubSessionError as error:
        click.echo("Error: %s" % error, err=True)
        sys.exit(4)
    sys.exit(iExitCode)


@fnCredentialCommand.command("status")
@click.option("--project", "-p", "sProjectName", default=None,
              help="Project name (omit inside a project directory).")
@click.option("--port", "iPort", default=None, type=int,
              help="Port of the vaibify session to drive.")
def fnCredentialStatusCommand(sProjectName, iPort):
    """Show which remotes are configured and reachable."""
    try:
        iExitCode = _fiSendUnderLease(
            sProjectName, iPort, "GET", S_STATUS_ROUTE)
    except hubSession.HubSessionError as error:
        click.echo("Error: %s" % error, err=True)
        sys.exit(4)
    sys.exit(iExitCode)


@fnCredentialCommand.command("delete")
@click.argument("sService", metavar="SERVICE", cls=DeclaredNameArgument,
                type=click.Choice(_LIST_VALID_SERVICES, case_sensitive=False))
@click.option("--keyring-slot", "sKeyringSlot", default="",
              help="GitHub only: per-repo keyring slot to clear.")
@click.option("--instance", "sZenodoInstance",
              type=click.Choice(_LIST_VALID_ZENODO_INSTANCES,
                                case_sensitive=False),
              default="sandbox", help="Zenodo: sandbox or production.")
def fnDeleteCredentialCommand(sService, sKeyringSlot, sZenodoInstance):
    """Revoke SERVICE's stored credential and clear its keyring slot."""
    dictResult = _fdictRevokeForService(
        sService.lower(), sKeyringSlot, sZenodoInstance.lower())
    fnPrintRevocationReport(sService, dictResult)
    if not dictResult["bLocalCleared"]:
        sys.exit(1)
