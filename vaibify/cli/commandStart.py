"""CLI subcommand: vaibify start."""

import json
import os
import re
import subprocess
import sys

import click

from .configLoader import fconfigResolveProject, fsDockerDir
from .portAllocator import fiResolvePort
from .preflightChecks import fpreflightColimaVersion, fpreflightDaemon
from .preflightResult import PreflightResult, fnPrintPreflightReport


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


def _fnAcquireGuiSessionSlotOrExit(iPort):
    """Acquire a session slot for the workflow viewer or exit nonzero."""
    from vaibify.config.sessionRegistry import (
        SessionLimitExceededError, fnAcquireSessionSlot,
    )
    try:
        return fnAcquireSessionSlot("viewer", iPort)
    except SessionLimitExceededError as error:
        click.echo(f"Error: {error}", err=True)
        sys.exit(1)


def fnLaunchGui(config, iExplicitPort):
    """Launch the workflow viewer GUI bound to the given port."""
    click.echo("Launching workflow viewer ...")
    from vaibify.gui.pipelineServer import (
        fappCreateApplication,
    )
    from vaibify.config.containerLock import fnReleaseContainerLock
    from vaibify.config.sessionRegistry import fnReleaseSessionSlot
    import uvicorn
    iPort = fiResolvePort(iExplicitPort)
    fileHandleSession = _fnAcquireGuiSessionSlotOrExit(iPort)
    try:
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
    finally:
        fnReleaseSessionSlot(fileHandleSession)


def _fpreflightImage(config):
    """Pre-flight: project image built locally."""
    from vaibify.docker import fbImageExists
    sTag = f"{config.sProjectName}:latest"
    if fbImageExists(sTag):
        return PreflightResult(
            sName="image", sLevel="ok",
            sMessage=f"Image {sTag} present",
        )
    return PreflightResult(
        sName="image", sLevel="fail",
        sMessage=f"Image {sTag} not found",
        sRemediation="Run 'vaibify build' first.",
    )


def _flistpreflightPorts(config):
    """Pre-flight every host port forwarding entry; return all results."""
    from vaibify.docker import fbForwardedHostPortFree
    listResults = []
    for dictPort in getattr(config, "listPorts", []) or []:
        iHost = int(dictPort.get("host", dictPort.get("container")))
        listResults.append(
            _fpreflightSinglePort(iHost, fbForwardedHostPortFree),
        )
    return listResults


def _fpreflightSinglePort(iHost, fbCheck):
    """Build one PreflightResult for the given host port."""
    if fbCheck(iHost):
        return PreflightResult(
            sName=f"port-{iHost}", sLevel="ok",
            sMessage=f"Host port {iHost} free",
        )
    sRemediation = (
        f"Edit vaibify.yml's `ports:` section to choose a free "
        f"host port, or stop the conflicting process. Find the "
        f"process holding the port with: `lsof -i :{iHost}`"
    )
    return PreflightResult(
        sName=f"port-{iHost}", sLevel="fail",
        sMessage=f"Host port {iHost} already in use",
        sRemediation=sRemediation,
    )


def _fpreflightContainerName(config):
    """Pre-flight: container name not already in use, or auto-clean stale."""
    from vaibify.docker.containerManager import (
        fdictGetContainerStatus, fnRemoveStopped,
    )
    dictStatus = fdictGetContainerStatus(config.sProjectName)
    if not dictStatus["bExists"]:
        return PreflightResult(
            sName="container-name", sLevel="ok",
            sMessage=f"Container '{config.sProjectName}' name available",
        )
    if dictStatus["bRunning"]:
        return _fpreflightRunningContainer(config.sProjectName)
    fnRemoveStopped(config.sProjectName)
    return _fpreflightRemovedStaleContainer(config.sProjectName)


def _fpreflightRemovedStaleContainer(sProjectName):
    """Build a warn-level PreflightResult for an auto-removed stopped container."""
    return PreflightResult(
        sName="container-name", sLevel="warn",
        sMessage=(
            f"Removed stopped container '{sProjectName}' "
            f"from prior session."
        ),
    )


def _fpreflightRunningContainer(sProjectName):
    """Build a fail-level PreflightResult for an already-running container."""
    sRemediation = (
        f"Use `vaibify stop` first, or attach with `vaibify exec`."
    )
    return PreflightResult(
        sName="container-name", sLevel="fail",
        sMessage=f"Container '{sProjectName}' is already running.",
        sRemediation=sRemediation,
    )


def _flistpreflightBindMounts(config):
    """Pre-flight every bind-mount source path; return all results."""
    listResults = []
    for dictMount in getattr(config, "listBindMounts", []) or []:
        listResults.append(_fpreflightSingleBindMount(dictMount))
    return listResults


def _fpreflightSingleBindMount(dictMount):
    """Build one PreflightResult for the given bind-mount entry."""
    sHostPath = dictMount.get("host", "")
    if sHostPath and os.path.exists(sHostPath):
        return PreflightResult(
            sName=f"bind-mount:{sHostPath}", sLevel="ok",
            sMessage=f"Bind-mount source {sHostPath} exists",
        )
    sRemediation = (
        "Edit vaibify.yml's `bindMounts` to remove or update this "
        "path, or create the path on the host."
    )
    return PreflightResult(
        sName=f"bind-mount:{sHostPath}", sLevel="fail",
        sMessage=f"Bind-mount source path missing: {sHostPath}",
        sRemediation=sRemediation,
    )


_RE_BIND_MOUNT_PATH_OK = re.compile(r"^[\w./~ -]+$", re.ASCII)


def _flistpreflightBindMountFormats(config):
    """Pre-flight every bind-mount source path's format; return all results."""
    listResults = []
    for dictMount in getattr(config, "listBindMounts", []) or []:
        resultPath = _fpreflightBindMountPathFormat(dictMount)
        if resultPath is not None:
            listResults.append(resultPath)
    return listResults


def _fpreflightBindMountPathFormat(dictMount):
    """Warn if dictMount["host"] has spaces or non-ASCII characters."""
    sHostPath = dictMount.get("host", "")
    if not sHostPath:
        return None
    if _RE_BIND_MOUNT_PATH_OK.match(sHostPath) and " " not in sHostPath:
        return None
    return _fpreflightBindMountFormatWarn(sHostPath)


def _fpreflightBindMountFormatWarn(sHostPath):
    """Build the warn-level PreflightResult for a flagged bind-mount path."""
    sRemediation = (
        "Colima usually handles spaces and non-ASCII characters, "
        "but if the mount appears empty inside the container "
        "consider renaming the host path."
    )
    return PreflightResult(
        sName=f"bind-mount-format:{sHostPath}", sLevel="warn",
        sMessage=(
            f"Bind-mount source path has spaces or non-ASCII "
            f"characters: {sHostPath}"
        ),
        sRemediation=sRemediation,
    )


_LIST_COLIMA_DEFAULT_SHARED_ROOTS = ("/Users", "/private/tmp")


def _flistColimaSharedRoots():
    """Return the list of host paths Colima is currently sharing.

    Falls back to a static default list (``$HOME``, ``/Users``,
    ``/private/tmp``) if ``colima list --json`` is unavailable or
    its output cannot be parsed; current Colima releases share these
    roots by default.
    """
    listParsed = _flistParseColimaSharedRoots()
    if listParsed:
        return listParsed
    return _flistColimaDefaultSharedRoots()


def _flistColimaDefaultSharedRoots():
    """Return the static default Colima shared-root list."""
    listRoots = [os.path.expanduser("~")]
    listRoots.extend(_LIST_COLIMA_DEFAULT_SHARED_ROOTS)
    return listRoots


def _flistParseColimaSharedRoots():
    """Try to parse ``colima list --json`` for the active VM's mounts."""
    try:
        resultProcess = subprocess.run(
            ["colima", "list", "--json"],
            capture_output=True, text=True, timeout=5,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return []
    if resultProcess.returncode != 0:
        return []
    return _flistParseColimaJsonMounts(resultProcess.stdout)


def _flistParseColimaJsonMounts(sStdout):
    """Extract mount-source paths from colima JSON output."""
    listRoots = []
    for sLine in (sStdout or "").splitlines():
        sLine = sLine.strip()
        if not sLine:
            continue
        try:
            dictRow = json.loads(sLine)
        except (ValueError, TypeError):
            continue
        listRoots.extend(_flistRootsFromColimaRow(dictRow))
    return listRoots


def _flistRootsFromColimaRow(dictRow):
    """Pull mount source paths out of one ``colima list --json`` row."""
    listRoots = []
    for dictMount in dictRow.get("mounts", []) or []:
        sLocation = dictMount.get("location", "")
        if sLocation:
            listRoots.append(sLocation)
    return listRoots


def _fbHostPathInsideRoots(sHostPath, listRoots):
    """Return True if sHostPath resolves under any path in listRoots."""
    sResolved = os.path.realpath(os.path.expanduser(sHostPath))
    for sRoot in listRoots:
        sResolvedRoot = os.path.realpath(os.path.expanduser(sRoot))
        if _fbPathStartsWith(sResolved, sResolvedRoot):
            return True
    return False


def _fbPathStartsWith(sPath, sPrefix):
    """True iff sPath equals sPrefix or is a descendant directory."""
    if sPath == sPrefix:
        return True
    sPrefixSlash = sPrefix.rstrip(os.sep) + os.sep
    return sPath.startswith(sPrefixSlash)


def _flistpreflightColimaSharedRoots(config):
    """Pre-flight: every bind-mount lives under a Colima shared root."""
    from vaibify.docker.dockerContext import fbColimaActive
    if not fbColimaActive():
        return []
    listRoots = _flistColimaSharedRoots()
    listResults = []
    for dictMount in getattr(config, "listBindMounts", []) or []:
        sHostPath = dictMount.get("host", "")
        if not sHostPath:
            continue
        if _fbHostPathInsideRoots(sHostPath, listRoots):
            continue
        listResults.append(_fpreflightColimaShareMissing(sHostPath))
    return listResults


def _fpreflightColimaShareMissing(sHostPath):
    """Build a fail-level PreflightResult for a path Colima is not sharing."""
    sRemediation = (
        f"Stop and restart Colima with "
        f"`colima start --mount '{sHostPath}:w'`."
    )
    return PreflightResult(
        sName=f"colima-share:{sHostPath}", sLevel="fail",
        sMessage=f"Colima isn't sharing '{sHostPath}'.",
        sRemediation=sRemediation,
    )


def flistRunStartPreflight(config):
    """Run all pre-flight checks for `vaibify start`; return all results."""
    listResults = [fpreflightDaemon("start")]
    if listResults[0].sLevel == "fail":
        return listResults
    listResults.append(_fpreflightImage(config))
    listResults.extend(_flistpreflightPorts(config))
    listResults.append(_fpreflightContainerName(config))
    listResults.extend(_flistpreflightBindMounts(config))
    listResults.extend(_flistpreflightBindMountFormats(config))
    listResults.extend(_flistpreflightColimaSharedRoots(config))
    resultColimaVersion = fpreflightColimaVersion()
    if resultColimaVersion is not None:
        listResults.append(resultColimaVersion)
    return listResults


def _fnPrintWarningsIfAny(listResults):
    """Print only warn-level entries (passes are silent on the happy path)."""
    listWarnings = [r for r in listResults if r.sLevel == "warn"]
    if listWarnings:
        fnPrintPreflightReport(listWarnings)


def _fnEnforcePreflightOrExit(listResults):
    """Print and exit if any pre-flight result failed."""
    if any(r.sLevel == "fail" for r in listResults):
        fnPrintPreflightReport(listResults)
        sys.exit(1)


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
    listPreflight = flistRunStartPreflight(config)
    _fnEnforcePreflightOrExit(listPreflight)
    _fnPrintWarningsIfAny(listPreflight)
    sDockerDir = fsDockerDir()
    click.echo(f"Starting container {config.sProjectName} ...")
    _fnStartContainer(config, sDockerDir, command)
    if bGui:
        fnLaunchGui(config, iPort)
