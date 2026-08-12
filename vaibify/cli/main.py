"""Vaibify CLI entry point.

Registers all subcommands with the top-level Click group.
"""

import logging
import logging.handlers
import os
import subprocess
import sys

import click

I_LOG_MAX_BYTES = 10 * 1024 * 1024
I_LOG_BACKUP_COUNT = 5


def _fbHasFileHandlerAttached(loggerVaibify):
    """Return True when any file handler is already attached."""
    return any(
        isinstance(handlerExisting, logging.FileHandler)
        for handlerExisting in loggerVaibify.handlers
    )


def _fbHasIncidentHandlerAttached(loggerVaibify):
    """Return True when the host-incident handler is already attached."""
    from vaibify.gui.hostIncidents import HostIncidentHandler
    return any(
        isinstance(handlerExisting, HostIncidentHandler)
        for handlerExisting in loggerVaibify.handlers
    )


def _fnAttachHostIncidentHandler(loggerVaibify):
    """Attach the in-memory ring-buffer handler if not already mounted.

    Captures host-side exceptions tagged with ``sContainerId`` so the
    pipeline-state reconciler can stamp the cause-of-death into the
    container-readable state file. Runs at INFO level alongside the
    rotating file handler so warnings as well as errors land in the
    ring.
    """
    from vaibify.gui.hostIncidents import HostIncidentHandler
    if _fbHasIncidentHandlerAttached(loggerVaibify):
        return
    handlerIncident = HostIncidentHandler()
    handlerIncident.setLevel(logging.INFO)
    loggerVaibify.addHandler(handlerIncident)


class _DefaultContainerIdFilter(logging.Filter):
    """Ensure every record has ``sContainerId`` so the formatter never KeyErrors.

    The host-log-tail diagnostic feature greps lines by container id; the
    formatter renders ``[cid:%(sContainerId)s]`` so the substring match
    finds entries that ``logger.error(..., extra={"sContainerId": cid})``
    tagged. Records without the attribute (most of them) default to ``-``.
    """

    def filter(self, recordLog):
        if not hasattr(recordLog, "sContainerId"):
            recordLog.sContainerId = "-"
        return True


def _fnConfigureErrorLogging(sLogDirOverride=None):
    """Attach one rotating file handler for ~/.vaibify/vaibify.log."""
    sLogDir = sLogDirOverride or os.path.expanduser("~/.vaibify")
    os.makedirs(sLogDir, exist_ok=True)
    sLogPath = os.path.join(sLogDir, "vaibify.log")
    loggerVaibify = logging.getLogger("vaibify")
    loggerVaibify.setLevel(logging.INFO)
    if not _fbHasFileHandlerAttached(loggerVaibify):
        rotatingHandler = logging.handlers.RotatingFileHandler(
            sLogPath, maxBytes=I_LOG_MAX_BYTES,
            backupCount=I_LOG_BACKUP_COUNT,
        )
        rotatingHandler.setLevel(logging.INFO)
        rotatingHandler.addFilter(_DefaultContainerIdFilter())
        rotatingHandler.setFormatter(logging.Formatter(
            "%(asctime)s %(levelname)s %(name)s "
            "[cid:%(sContainerId)s]: %(message)s"
        ))
        loggerVaibify.addHandler(rotatingHandler)
    _fnAttachHostIncidentHandler(loggerVaibify)

from .actionCommands import fnDoCommand
from .commandBuild import fnBuildCommand
from .commandCat import fnCatCommand
from .commandConfig import fnConfigCommand
from .commandDestroy import fnDestroyCommand
from .commandDoctor import fnDoctorCommand
from .commandGenerateStandards import fnGenerateStandardsCommand
from .commandInit import fnInitCommand
from .commandLs import fnListCommand
from .commandOpen import fnOpenContainerCommand
from .commandReconcile import fnReconcileCommand
from .commandRegister import fnRegisterCommand
from .commandReproduce import fnReproduceCommand
from .commandRevoke import fnRevokeCommand
from .commandRun import fnRunCommand
from .commandSessions import fnListSessionsCommand
from .commandStart import fnStartCommand
from .commandStatus import fnStatusCommand
from .commandTest import fnTestCommand
from .commandVerifyStep import fnVerifyStepCommand
from .commandWorkflow import fnWorkflowCommand
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
    _fnConfigureErrorLogging()
    if sConfigPath:
        from .configLoader import fnSetConfigPath
        fnSetConfigPath(sConfigPath)
    if ctx.invoked_subcommand is None:
        fnLaunchHub(iPort)


def _ffileAcquireHubSessionSlotOrExit(sRole, iPort):
    """Acquire a session slot or exit nonzero with a clear message."""
    import sys
    from vaibify.config.sessionRegistry import (
        SessionLimitExceededError, ffileAcquireSessionSlot,
    )
    try:
        return ffileAcquireSessionSlot(sRole, iPort)
    except SessionLimitExceededError as error:
        click.echo(f"Error: {error}", err=True)
        sys.exit(1)


def _fnOpenBrowserUnlessSuppressed(sUrl):
    """Open sUrl in a background thread unless the suppress env var is set."""
    import os
    import threading
    import time
    import webbrowser
    from vaibify.gui.routes.sessionRoutes import S_SUPPRESS_BROWSER_ENV
    if os.environ.get(S_SUPPRESS_BROWSER_ENV):
        return
    threading.Thread(
        target=lambda: (time.sleep(1), webbrowser.open(sUrl)),
        daemon=True,
    ).start()


def _fsLaunchUrlWithCapability(sBaseUrl, app):
    """Append a one-time bootstrap capability to the browser launch URL.

    The capability authorises the launched browser to exchange it once for
    a per-browser session credential. It goes in the URL FRAGMENT so it
    never reaches the server's access log, and it is never echoed to the
    terminal. Apps without a browser-session store (e.g. the setup wizard)
    fall back to the bare URL.
    """
    from vaibify.gui import browserSession
    dictStore = getattr(app.state, "dictBrowserSessions", None)
    if dictStore is None:
        return sBaseUrl
    sCapability = browserSession.fsMintBootstrapCapability(dictStore)
    return f"{sBaseUrl}/#bootstrap={sCapability}"


def _fnAnnounceAndOpen(sBaseUrl, app, sWhat):
    """Print where vaibify is serving, then open the credentialled tab.

    The address and the usable LINK are not the same string, and saying
    so is the whole point of this function. The dashboard authenticates
    only by redeeming a one-time capability carried in the URL
    FRAGMENT, which is deliberately never echoed -- a fragment stays
    out of access logs, and printing it would put a credential in the
    researcher's scrollback.

    What this replaced printed the bare address and nothing else, so a
    researcher who used it -- a restored tab, a bookmark, a retyped
    address -- reached a dashboard that answered 401 to every call and
    showed a spinner forever. The address is still printed, because it
    is genuinely useful for knowing the port; it is now labelled as the
    address rather than offered as the way in.
    """
    click.echo(f"Starting {sWhat} at {sBaseUrl}")
    click.echo(
        "Opening your browser. The dashboard signs in with a one-time "
        "link, so that tab is the way in — this address alone cannot "
        "sign in. If no window opened, re-run this command."
    )
    _fnOpenBrowserUnlessSuppressed(
        _fsLaunchUrlWithCapability(sBaseUrl, app),
    )


def fnLaunchHub(iExplicitPort):
    """Start the hub-mode server and open the browser.

    The hub is project-agnostic — it shows every registered container
    and the user picks one from the dashboard — so the per-project
    stable-port machinery used by ``vaibify start --gui`` does not
    apply directly. ``fiResolveHubPort`` provides an analogous
    survival contract via ``~/.vaibify/hub-port.json``: the hub binds
    the same port across Ctrl-C/restart cycles whenever possible, so
    any dashboard tab opened from the prior run keeps working without
    a reload. Falls back to a free-port scan (and warns on stderr)
    when the persisted port is held by another process.
    """
    import uvicorn
    from vaibify.config.sessionRegistry import fnReleaseSessionSlot
    from vaibify.gui.pipelineServer import fappCreateHubApplication
    from .portAllocator import fiResolveHubPort
    iPort = fiResolveHubPort(iExplicitPort)
    fileHandleSession = _ffileAcquireHubSessionSlotOrExit("hub", iPort)
    try:
        sUrl = f"http://127.0.0.1:{iPort}"
        app = fappCreateHubApplication(iExpectedPort=iPort)
        _fnAnnounceAndOpen(sUrl, app, "vaibify")
        uvicorn.run(
            app, host="127.0.0.1", port=iPort,
            log_level="warning", timeout_graceful_shutdown=3,
        )
    finally:
        fnReleaseSessionSlot(fileHandleSession)


main.add_command(fnInitCommand)
main.add_command(fnBuildCommand)
main.add_command(fnStartCommand)
main.add_command(fnStatusCommand)
main.add_command(fnDestroyCommand)
main.add_command(fnConfigCommand)
main.add_command(fnReproduceCommand)
main.add_command(fnRunCommand)
main.add_command(fnWorkflowCommand)
main.add_command(fnVerifyStepCommand)
main.add_command(fnListCommand)
main.add_command(fnCatCommand)
main.add_command(fnRegisterCommand)
main.add_command(fnRevokeCommand)
main.add_command(fnTestCommand)
main.add_command(fnGenerateStandardsCommand)
main.add_command(fnDoctorCommand)
main.add_command(fnListSessionsCommand)
main.add_command(fnDoCommand)
main.add_command(fnReconcileCommand)
main.add_command(fnOpenContainerCommand)


@main.command("stop")
@click.option(
    "--project", "-p", "sProjectName", default=None,
    help="Project name (omit if in a project directory "
    "or only one project exists).",
)
def fnStopCommand(sProjectName):
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
def fnConnectCommand(project):
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
def fnVerifyCommand(sProjectName):
    """Run the isolation check script inside the container."""
    configProject = fconfigResolveProject(sProjectName)
    sUser = configProject.sContainerUser
    sScript = f"/home/{sUser}/checkIsolation.sh"
    subprocess.run(
        ["docker", "exec", "-it", "-u", sUser,
         configProject.sProjectName, sScript]
    )


@main.command("setup")
def fnSetupCommand():
    """Launch the setup wizard to create or edit configuration."""
    from vaibify.install.setupServer import fappCreateSetupWizard
    import uvicorn
    sUrl = "http://127.0.0.1:8051"
    click.echo(f"Starting setup wizard at {sUrl}")
    app = fappCreateSetupWizard()
    _fnOpenBrowserUnlessSuppressed(sUrl)
    uvicorn.run(
        app, host="127.0.0.1", port=8051,
        log_level="warning", timeout_graceful_shutdown=3,
    )


@main.command("gui")
@click.option(
    "--project", "-p", "sProjectName", default=None,
    help="Project to open. Omit for the landing page, which is also "
         "what the bare 'vaibify' command shows.",
)
def fnGuiCommand(sProjectName):
    """Launch the vaibify dashboard."""
    from vaibify.gui.pipelineServer import fappCreateApplication
    import uvicorn
    _fnConfigureErrorLogging()
    if sProjectName is None:
        # The landing page is the HUB, and there is one implementation
        # of it. This branch used to build the single-project viewer
        # with a "/workspace" workspace root -- a container path, on
        # the researcher's laptop -- while the help text promised the
        # landing page, and the project-resolution error it printed on
        # the way was a sys.exit somebody had caught and discarded.
        fnLaunchHub(None)
        return
    configProject = fconfigResolveProject(sProjectName)
    sUrl = "http://127.0.0.1:8050"
    app = fappCreateApplication(
        sWorkspaceRoot=configProject.sWorkspaceRoot,
        sTerminalUserArg=configProject.sContainerUser,
        iExpectedPort=8050,
    )
    _fnAnnounceAndOpen(sUrl, app, f"vaibify: {sProjectName}")
    uvicorn.run(
        app, host="127.0.0.1", port=8050,
        log_level="warning", timeout_graceful_shutdown=3,
    )


@main.command("push")
@click.option(
    "--project", "-p", default=None,
    help="Project name (optional if only one project exists).",
)
@click.argument("source")
@click.argument("destination")
def fnPushCommand(project, source, destination):
    """Push files from the host into the project workspace."""
    configProject = fconfigResolveProject(project)
    if _fbCopiedWithinHostProject(configProject, source, destination):
        return
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
def fnPullCommand(project, source, destination):
    """Pull files from the project workspace to the host."""
    configProject = fconfigResolveProject(project)
    if _fbCopiedWithinHostProject(configProject, source, destination):
        return
    from vaibify.docker.fileTransfer import fnPullFromContainer
    fnPullFromContainer(configProject.sProjectName, source, destination)
    click.echo(f"Pulled {source} -> {destination}")


def _fbCopiedWithinHostProject(configProject, sSource, sDestination):
    """Copy on the researcher's own machine; False if not a host project.

    ``docker cp`` is the wrong verb twice for a host project: there is
    no container on the other side, and the files were never anywhere
    else. What is left is an ordinary copy, with project-relative
    paths resolved against the project directory so ``vaibify push
    data.csv Step01/`` means what it says in both modes.

    A copy onto itself is refused rather than performed: ``shutil``
    would truncate the file before reading it, and a researcher who
    typed the same path twice meant to move nothing.
    """
    import shutil
    from vaibify.config.registryManager import fbIsHostProject
    if not fbIsHostProject(configProject.sProjectName):
        return False
    sSourcePath = _fsResolveAgainstProject(configProject, sSource)
    sDestinationPath = _fsResolveAgainstProject(
        configProject, sDestination,
    )
    if os.path.isdir(sDestinationPath):
        sDestinationPath = os.path.join(
            sDestinationPath, os.path.basename(sSourcePath.rstrip(os.sep)),
        )
    if os.path.exists(sDestinationPath) and os.path.samefile(
        sSourcePath, sDestinationPath,
    ):
        click.echo(
            f"{sSourcePath} is already where you asked for it; this "
            "project's files live on this machine."
        )
        return True
    sDestinationParent = os.path.dirname(sDestinationPath)
    if sDestinationParent and not os.path.isdir(sDestinationParent):
        raise click.ClickException(
            f"{sDestinationParent} does not exist. Create it first — "
            "this command copies, it does not build a tree, and "
            "neither does the container lane it mirrors."
        )
    if os.path.isdir(sSourcePath):
        shutil.copytree(sSourcePath, sDestinationPath, dirs_exist_ok=True)
    else:
        shutil.copy2(sSourcePath, sDestinationPath)
    click.echo(f"Copied {sSourcePath} -> {sDestinationPath}")
    return True


def _fsResolveAgainstProject(configProject, sPath):
    """Return an absolute path, resolving a relative one in the project."""
    from vaibify.config.registryManager import fdictGetProject
    if os.path.isabs(sPath):
        return sPath
    dictProject = fdictGetProject(configProject.sProjectName) or {}
    return os.path.join(
        dictProject.get("sDirectory") or os.getcwd(), sPath,
    )
