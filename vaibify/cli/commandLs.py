"""CLI subcommand: vaibify ls."""

import sys

import click

from .configLoader import fconfigResolveProject
from .commandUtilsDocker import (
    fconnectionRequireDocker,
    fsRequireRunningContainer,
    fnPrintJson,
    fbShouldOutputJson,
)


def _fsNormalizePath(sPath):
    """Prepend /workspace/ if the path is not absolute."""
    if sPath and not sPath.startswith("/"):
        return f"/workspace/{sPath}"
    return sPath


def _flistParseDirectoryListing(sOutput):
    """Parse ls output into a list of filename strings."""
    listFiles = []
    for sLine in sOutput.splitlines():
        sStripped = sLine.strip()
        if sStripped:
            listFiles.append(sStripped)
    return listFiles


@click.command("ls")
@click.option(
    "--project", "-p", "sProjectName", default=None,
    help="Project name.",
)
@click.option(
    "--json", "bJson", is_flag=True, default=False,
    help="Output in JSON format.",
)
@click.argument("sPath", default="/workspace")
def ls(sProjectName, bJson, sPath):
    """List files in the container workspace."""
    configProject = fconfigResolveProject(sProjectName)
    connectionDocker = fconnectionRequireDocker()
    sContainerName = fsRequireRunningContainer(configProject)
    sNormalized = _fsNormalizePath(sPath)
    iExitCode, sOutput = connectionDocker.ftResultExecuteCommand(
        sContainerName, f"ls -1 {sNormalized}"
    )
    if iExitCode != 0:
        click.echo(f"Error: {sOutput.strip()}")
        sys.exit(2)
    if fbShouldOutputJson(bJson):
        listFiles = _flistParseDirectoryListing(sOutput)
        fnPrintJson({"sPath": sNormalized, "listFiles": listFiles})
    else:
        click.echo(sOutput.rstrip())
