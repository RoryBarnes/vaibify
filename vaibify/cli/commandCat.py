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
@click.argument("path")
def fnCatCommand(sProjectName, path):
    """Print file contents from the container."""
    configProject = fconfigResolveProject(sProjectName)
    connectionDocker = fconnectionRequireDocker()
    sContainerName = fsRequireRunningContainer(configProject)
    sNormalized = _fsNormalizePath(path)
    # A TYPED read, not a shell command. The old form interpolated the
    # caller's path into `cat {path}` and handed the result to
    # `/bin/bash -c`, so `vaibify cat '/tmp/a; rm -rf /workspace'` ran
    # both halves -- and a path containing a space simply failed.
    # fbaFetchFile builds its own program under two layers of quoting;
    # nothing the caller types reaches a shell as syntax.
    try:
        baContent = connectionDocker.fbaFetchFile(
            sContainerName, sNormalized,
        )
    except FileNotFoundError:
        click.echo(f"Error: cannot read {sNormalized} from the container")
        sys.exit(2)
    except ValueError as error:
        click.echo(f"Error: {error}")
        sys.exit(2)
    sys.stdout.buffer.write(baContent)
