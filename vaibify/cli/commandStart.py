"""CLI subcommand: vaibify start."""

import sys

import click

from .configLoader import fconfigLoad, fsDockerDir


def _fnStartContainer(config, sDockerDir, sCommand):
    """Start the container via the Docker container manager.

    Uses lazy import so the CLI remains usable without Docker installed.
    """
    try:
        from vaibify.docker.containerManager import (
            fnStartContainer,
        )
    except ImportError:
        click.echo(
            "Error: Docker support is not installed. "
            "Install with: pip install vaibify[docker]"
        )
        sys.exit(1)
    saCommand = [sCommand] if sCommand else None
    fnStartContainer(config, sDockerDir, saCommand=saCommand)


def fnLaunchGui(config):
    """Launch the workflow viewer GUI."""
    click.echo("Launching workflow viewer ...")
    from vaibify.gui.pipelineServer import (
        fappCreateApplication,
    )
    import uvicorn
    sRoot = config.sWorkspaceRoot
    app = fappCreateApplication(sWorkspaceRoot=sRoot)
    uvicorn.run(app, host="127.0.0.1", port=8050)


@click.command("start")
@click.option(
    "--gui",
    "bGui",
    is_flag=True,
    default=False,
    help="Also launch the workflow viewer GUI.",
)
@click.option(
    "--jupyter",
    "bJupyter",
    is_flag=True,
    default=False,
    help="Enable Jupyter overlay with port forwarding.",
)
@click.argument("sCommand", required=False, default=None)
def start(bGui, bJupyter, sCommand):
    """Start the Vaibify environment."""
    config = fconfigLoad()
    sDockerDir = fsDockerDir()
    click.echo(f"Starting container {config.sProjectName} ...")
    _fnStartContainer(config, sDockerDir, sCommand)
    if bGui:
        fnLaunchGui(config)
