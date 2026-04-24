"""Vaibify CLI entry point.

Registers all subcommands with the top-level Click group.
"""

import logging
import os
import subprocess
import sys

import click


def _fnConfigureErrorLogging():
    """Write vaibify activity to ~/.vaibify/vaibify.log."""
    sLogDir = os.path.expanduser("~/.vaibify")
    os.makedirs(sLogDir, exist_ok=True)
    sLogPath = os.path.join(sLogDir, "vaibify.log")
    fileHandler = logging.FileHandler(sLogPath)
    fileHandler.setLevel(logging.INFO)
    fileHandler.setFormatter(logging.Formatter(
        "%(asctime)s %(levelname)s %(name)s: %(message)s"
    ))
    loggerVaibify = logging.getLogger("vaibify")
    loggerVaibify.setLevel(logging.INFO)
    loggerVaibify.addHandler(fileHandler)

from .commandBuild import build
from .commandCat import cat
from .commandConfig import config
from .commandDestroy import destroy
from .commandInit import init
from .commandLs import ls
from .commandRegister import register
from .commandPublish import publish
from .commandRun import run
from .commandStart import start
from .commandStatus import status
from .commandTest import test
from .commandVerifyStep import verify_step
from .commandWorkflow import workflow
from .configLoader import fconfigResolveProject


def _fnEnsureFirstTimeSetup():
    """Run shell setup on first invocation; never block the CLI."""
    try:
        from vaibify.install.shellSetup import (
            fbIsSetupComplete, fnRunFirstTimeSetup,
        )
        if not fbIsSetupComplete():
            fnRunFirstTimeSetup()
    except Exception:
        pass


_fnEnsureFirstTimeSetup()
_fnConfigureErrorLogging()


@click.group(invoke_without_command=True)
@click.version_option(package_name="vaibify")
@click.option(
    "--config", "sConfigPath", default=None,
    type=click.Path(exists=True),
    help="Path to vaibify.yml (default: ./vaibify.yml).",
)
@click.option(
    "--port", "iPort", default=None, type=int,
    help="Port for the hub server (default: 8050, "
    "auto-shifts upward if taken).",
)
@click.pass_context
def main(ctx, sConfigPath, iPort):
    """Vaibify - Vibe boldly. Verify everything."""
    if sConfigPath:
        from .configLoader import fnSetConfigPath
        fnSetConfigPath(sConfigPath)
    if ctx.invoked_subcommand is None:
        fnLaunchHub(iPort)


def _fnAcquireHubSessionSlotOrExit(sRole, iPort):
    """Acquire a session slot or exit nonzero with a clear message."""
    import sys
    from vaibify.config.sessionRegistry import (
        SessionLimitExceededError, fnAcquireSessionSlot,
    )
    try:
        return fnAcquireSessionSlot(sRole, iPort)
    except SessionLimitExceededError as error:
        click.echo(f"Error: {error}", err=True)
        sys.exit(1)


def fnLaunchHub(iExplicitPort):
    """Start the hub-mode server and open the browser."""
    import os
    import threading
    import time
    import uvicorn
    import webbrowser
    from vaibify.config.sessionRegistry import fnReleaseSessionSlot
    from vaibify.gui.pipelineServer import fappCreateHubApplication
    from vaibify.gui.routes.sessionRoutes import S_SUPPRESS_BROWSER_ENV
    from .portAllocator import fiResolvePort
    iPort = fiResolvePort(iExplicitPort)
    fileHandleSession = _fnAcquireHubSessionSlotOrExit("hub", iPort)
    try:
        sUrl = f"http://127.0.0.1:{iPort}"
        click.echo(f"Starting Vaibify hub at {sUrl}")
        app = fappCreateHubApplication(iExpectedPort=iPort)
        if not os.environ.get(S_SUPPRESS_BROWSER_ENV):
            threading.Thread(
                target=lambda: (time.sleep(1), webbrowser.open(sUrl)),
                daemon=True,
            ).start()
        uvicorn.run(
            app, host="127.0.0.1", port=iPort, log_level="warning",
        )
    finally:
        fnReleaseSessionSlot(fileHandleSession)


main.add_command(init)
main.add_command(build)
main.add_command(start)
main.add_command(status)
main.add_command(destroy)
main.add_command(config)
main.add_command(publish)
main.add_command(run)
main.add_command(workflow)
main.add_command(verify_step)
main.add_command(ls)
main.add_command(cat)
main.add_command(register)
main.add_command(test)


@main.command("stop")
@click.option(
    "--project", "-p", "sProjectName", default=None,
    help="Project name (omit if in a project directory "
    "or only one project exists).",
)
def stop(sProjectName):
    """Stop the running Vaibify environment."""
    configProject = fconfigResolveProject(sProjectName)
    from vaibify.docker.containerManager import fnStopContainer
    sName = configProject.sProjectName
    try:
        click.echo(f"Stopping container {sName} ...")
        fnStopContainer(sName)
        click.echo("Stopped.")
    except RuntimeError:
        click.echo(f"ERROR: vaibify container {sName} is not active.")
        sys.exit(1)


@main.command("connect")
@click.option(
    "--project", "-p", default=None,
    help="Project name (optional if only one project exists).",
)
def connect(project):
    """Open a shell inside the running container."""
    configProject = fconfigResolveProject(project)
    sUser = configProject.sContainerUser
    sName = configProject.sProjectName
    subprocess.run(
        ["docker", "exec", "-it", "-u", sUser, sName, "bash"]
    )


@main.command("verify")
@click.option(
    "--project", "-p", "sProjectName", default=None,
    help="Project name (omit if in a project directory "
    "or only one project exists).",
)
def verify(sProjectName):
    """Run the isolation check script inside the container."""
    configProject = fconfigResolveProject(sProjectName)
    sUser = configProject.sContainerUser
    sScript = f"/home/{sUser}/checkIsolation.sh"
    subprocess.run(
        ["docker", "exec", "-it", configProject.sProjectName, sScript]
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
    uvicorn.run(app, host="127.0.0.1", port=8051, log_level="warning")


@main.command("gui")
@click.option(
    "--project", "-p", "sProjectName", default=None,
    help="Project name (omit to show the landing page).",
)
def gui(sProjectName):
    """Launch the Vaibify pipeline viewer GUI."""
    from vaibify.gui.pipelineServer import fappCreateApplication
    import threading
    import time
    import uvicorn
    import webbrowser
    _fnConfigureErrorLogging()
    sRoot, sTerminalUser = _ftResolveGuiConfig(sProjectName)
    sUrl = "http://127.0.0.1:8050"
    click.echo(f"Starting pipeline viewer at {sUrl}")
    app = fappCreateApplication(
        sWorkspaceRoot=sRoot, sTerminalUserArg=sTerminalUser,
        iExpectedPort=8050,
    )
    threading.Thread(
        target=lambda: (time.sleep(1), webbrowser.open(sUrl)),
        daemon=True,
    ).start()
    uvicorn.run(app, host="127.0.0.1", port=8050, log_level="warning")


def _ftResolveGuiConfig(sProjectName):
    """Return (sWorkspaceRoot, sContainerUser) for the GUI.

    When a project is specified or discoverable, use its
    config. Otherwise fall back to defaults so the landing
    page can still launch.
    """
    try:
        configProject = fconfigResolveProject(sProjectName)
        return configProject.sWorkspaceRoot, configProject.sContainerUser
    except SystemExit:
        if sProjectName is not None:
            raise
        return "/workspace", "researcher"


@main.command("push")
@click.option(
    "--project", "-p", default=None,
    help="Project name (optional if only one project exists).",
)
@click.argument("source")
@click.argument("destination")
def push(project, source, destination):
    """Push files from the host into the container workspace."""
    configProject = fconfigResolveProject(project)
    from vaibify.docker.fileTransfer import fnPushToContainer
    fnPushToContainer(configProject.sProjectName, source, destination)
    click.echo(f"Pushed {source} -> {destination}")


@main.command("pull")
@click.option(
    "--project", "-p", default=None,
    help="Project name (optional if only one project exists).",
)
@click.argument("source")
@click.argument("destination")
def pull(project, source, destination):
    """Pull files from the container workspace to the host."""
    configProject = fconfigResolveProject(project)
    from vaibify.docker.fileTransfer import fnPullFromContainer
    fnPullFromContainer(configProject.sProjectName, source, destination)
    click.echo(f"Pulled {source} -> {destination}")
