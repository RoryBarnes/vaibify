"""VaibCask CLI entry point.

Registers all subcommands with the top-level Click group.
"""

import subprocess

import click

from .commandBuild import build
from .commandConfig import config
from .commandDestroy import destroy
from .commandInit import init
from .commandPublish import publish
from .commandStart import start
from .commandStatus import status
from .configLoader import fconfigLoad


@click.group()
@click.version_option(package_name="vaibcask")
def main():
    """VaibCask - Vibe boldly. Verify everything."""
    pass


main.add_command(init)
main.add_command(build)
main.add_command(start)
main.add_command(status)
main.add_command(destroy)
main.add_command(config)
main.add_command(publish)


@main.command("stop")
def stop():
    """Stop the running VaibCask environment."""
    config = fconfigLoad()
    from vaibcask.docker.containerManager import fnStopContainer
    click.echo(f"Stopping container {config.sProjectName} ...")
    fnStopContainer(config.sProjectName)
    click.echo("Stopped.")


@main.command("connect")
def connect():
    """Open a shell inside the running container."""
    config = fconfigLoad()
    sUser = config.sContainerUser
    sName = config.sProjectName
    subprocess.run(
        ["docker", "exec", "-it", "-u", sUser, sName, "bash"]
    )


@main.command("verify")
def verify():
    """Run the isolation check script inside the container."""
    config = fconfigLoad()
    sUser = config.sContainerUser
    sScript = f"/home/{sUser}/checkIsolation.sh"
    subprocess.run(
        ["docker", "exec", "-it", config.sProjectName, sScript]
    )


@main.command("setup")
def setup():
    """Launch the setup wizard to create or edit configuration."""
    from vaibcask.install.setupServer import fappCreateSetupWizard
    import uvicorn
    click.echo("Starting setup wizard at http://127.0.0.1:8051")
    app = fappCreateSetupWizard()
    uvicorn.run(app, host="127.0.0.1", port=8051)


@main.command("gui")
def gui():
    """Launch the VaibCask pipeline viewer GUI."""
    config = fconfigLoad()
    from vaibcask.gui.pipelineServer import (
        fappCreateApplication,
    )
    import uvicorn
    sRoot = config.sWorkspaceRoot
    click.echo("Starting pipeline viewer at http://127.0.0.1:8050")
    app = fappCreateApplication(sWorkspaceRoot=sRoot)
    uvicorn.run(app, host="127.0.0.1", port=8050)


@main.command("push")
@click.argument("sSource")
@click.argument("sDestination")
def push(sSource, sDestination):
    """Push files from the host into the container workspace."""
    config = fconfigLoad()
    from vaibcask.docker.fileTransfer import fnPushToContainer
    fnPushToContainer(config.sProjectName, sSource, sDestination)
    click.echo(f"Pushed {sSource} -> {sDestination}")


@main.command("pull")
@click.argument("sSource")
@click.argument("sDestination")
def pull(sSource, sDestination):
    """Pull files from the container workspace to the host."""
    config = fconfigLoad()
    from vaibcask.docker.fileTransfer import fnPullFromContainer
    fnPullFromContainer(config.sProjectName, sSource, sDestination)
    click.echo(f"Pulled {sSource} -> {sDestination}")
