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


@click.command("ls")
@click.option(
    "--project", "-p", "sProjectName", default=None,
    help="Project name.",
)
@click.option(
    "--json", "bJson", is_flag=True, default=False,
    help="Output in JSON format.",
)
@click.argument("path", default="/workspace")
def fnListCommand(sProjectName, bJson, path):
    """List files in the container workspace."""
    configProject = fconfigResolveProject(sProjectName)
    connectionDocker = fconnectionRequireDocker()
    sContainerName = fsRequireRunningContainer(configProject)
    sNormalized = _fsNormalizePath(path)
    # A TYPED read through an audited adapter, not a shell command. The
    # old form interpolated the caller's path into `ls -1 {path}` and
    # handed it to `/bin/bash -c`, so a listing was an arbitrary command
    # execution triggered by a path argument -- and any path with a
    # space in it failed.
    try:
        listFiles = connectionDocker.flistDirectoryEntries(
            sContainerName, sNormalized,
        )
    except FileNotFoundError:
        click.echo(f"Error: cannot list {sNormalized} in the container")
        sys.exit(2)
    if fbShouldOutputJson(bJson):
        fnPrintJson({"sPath": sNormalized, "listFiles": listFiles})
    else:
        click.echo("\n".join(listFiles))
