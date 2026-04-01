"""CLI subcommand: vaibify cat."""

import sys

import click

from .configLoader import fconfigResolveProject
from .commandUtilsDocker import (
    fconnectionRequireDocker,
    fsRequireRunningContainer,
)


def _fsNormalizePath(sPath):
    """Prepend /workspace/ if the path is not absolute."""
    if not sPath.startswith("/"):
        return f"/workspace/{sPath}"
    return sPath


@click.command("cat")
@click.option(
    "--project", "-p", "sProjectName", default=None,
    help="Project name.",
)
@click.argument("sPath")
def cat(sProjectName, sPath):
    """Print file contents from the container."""
    configProject = fconfigResolveProject(sProjectName)
    connectionDocker = fconnectionRequireDocker()
    sContainerName = fsRequireRunningContainer(configProject)
    sNormalized = _fsNormalizePath(sPath)
    iExitCode, sOutput = connectionDocker.ftResultExecuteCommand(
        sContainerName, f"cat {sNormalized}"
    )
    if iExitCode != 0:
        click.echo(f"Error: {sOutput.strip()}")
        sys.exit(2)
    click.echo(sOutput, nl=False)
