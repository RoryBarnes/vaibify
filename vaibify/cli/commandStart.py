"""CLI subcommand: vaibify start."""

import sys

import click

from .configLoader import fconfigResolveProject, fsDockerDir
from .portAllocator import fiResolvePort


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
    try:
        fnStartContainer(config, sDockerDir, saCommand=saCommand)
    except RuntimeError as error:
        _fnHandleDockerRuntimeError(error, config.sProjectName)


def _fnHandleDockerRuntimeError(error, sProjectName):
    """Print a friendly message for known Docker exit scenarios."""
    sMessage = str(error)
    if "exit 137" in sMessage:
        click.echo(
            f"\nContainer '{sProjectName}' was stopped externally "
            f"(SIGKILL). This is normal when stopping the "
            f"container from another terminal or the GUI."
        )
        sys.exit(0)
    if "exit 130" in sMessage or "exit 143" in sMessage:
        click.echo(
            f"\nContainer '{sProjectName}' exited cleanly."
        )
        sys.exit(0)
    click.echo(f"Error: {sMessage}", err=True)
    sys.exit(1)


def _fnAcquireProjectLockOrExit(sProjectName, iPort):
    """Acquire the per-container lock or exit with a clear message."""
    from vaibify.config.containerLock import (
        ContainerLockedError, fnAcquireContainerLock,
    )
    try:
        return fnAcquireContainerLock(sProjectName, iPort)
    except ContainerLockedError as error:
        click.echo(
            f"Error: {error}\n"
            f"Stop the other vaibify session or pass a different "
            f"--project.",
            err=True,
        )
        sys.exit(1)


def fnLaunchGui(config, iExplicitPort):
    """Launch the workflow viewer GUI bound to the given port."""
    click.echo("Launching workflow viewer ...")
    from vaibify.gui.pipelineServer import (
        fappCreateApplication,
    )
    from vaibify.config.containerLock import fnReleaseContainerLock
    import uvicorn
    iPort = fiResolvePort(iExplicitPort)
    fileHandleLock = _fnAcquireProjectLockOrExit(
        config.sProjectName, iPort,
    )
    try:
        sRoot = config.sWorkspaceRoot
        app = fappCreateApplication(
            sWorkspaceRoot=sRoot, iExpectedPort=iPort,
        )
        uvicorn.run(app, host="127.0.0.1", port=iPort)
    finally:
        fnReleaseContainerLock(fileHandleLock)


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
@click.option(
    "--port", "iPort", default=None, type=int,
    help="Port for the workflow viewer GUI (default: 8050, "
    "auto-shifts upward if taken).",
)
@click.option(
    "--project", "-p", "sProjectName", default=None,
    help="Project name (omit if in a project directory "
    "or only one project exists).",
)
@click.argument("command", required=False, default=None)
def start(bGui, bJupyter, iPort, sProjectName, command):
    """Start the Vaibify environment."""
    config = fconfigResolveProject(sProjectName)
    sDockerDir = fsDockerDir()
    click.echo(f"Starting container {config.sProjectName} ...")
    _fnStartContainer(config, sDockerDir, command)
    if bGui:
        fnLaunchGui(config, iPort)
