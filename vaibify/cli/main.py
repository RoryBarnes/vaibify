"""Vaibify CLI entry point.

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
@click.version_option(package_name="vaibify")
def main():
    """Vaibify - Vibe boldly. Verify everything."""
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
    """Stop the running Vaibify environment."""
    config = fconfigLoad()
    from vaibify.docker.containerManager import fnStopContainer
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
    from vaibify.install.setupServer import fappCreateSetupWizard
    import threading
    import time
    import uvicorn
    import webbrowser
    sUrl = "http://127.0.0.1:8051"
    click.echo(f"Starting setup wizard at {sUrl}")
    app = fappCreateSetupWizard()
    threading.Thread(
        target=lambda: (time.sleep(1), webbrowser.open(sUrl)),
        daemon=True,
    ).start()
    uvicorn.run(app, host="127.0.0.1", port=8051)


@main.command("gui")
def gui():
    """Launch the Vaibify pipeline viewer GUI."""
    config = fconfigLoad()
    from vaibify.gui.pipelineServer import (
        fappCreateApplication,
    )
    import threading
    import time
    import uvicorn
    import webbrowser
    sRoot = config.sWorkspaceRoot
    sUrl = "http://127.0.0.1:8050"
    click.echo(f"Starting pipeline viewer at {sUrl}")
    app = fappCreateApplication(sWorkspaceRoot=sRoot)
    threading.Thread(
        target=lambda: (time.sleep(1), webbrowser.open(sUrl)),
        daemon=True,
    ).start()
    uvicorn.run(app, host="127.0.0.1", port=8050)


@main.command("push")
@click.argument("source")
@click.argument("destination")
def push(source, destination):
    """Push files from the host into the container workspace."""
    config = fconfigLoad()
    from vaibify.docker.fileTransfer import fnPushToContainer
    fnPushToContainer(config.sProjectName, source, destination)
    click.echo(f"Pushed {source} -> {destination}")


@main.command("pull")
@click.argument("source")
@click.argument("destination")
def pull(source, destination):
    """Pull files from the container workspace to the host."""
    config = fconfigLoad()
    from vaibify.docker.fileTransfer import fnPullFromContainer
    fnPullFromContainer(config.sProjectName, source, destination)
    click.echo(f"Pulled {source} -> {destination}")
