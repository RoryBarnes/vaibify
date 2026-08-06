"""CLI subcommand: vaibify doctor.

Aggregates every relevant pre-flight check into a single status
report, modelled after ``brew doctor`` / ``flutter doctor``. The
command runs the build-relevant subset, the start-relevant subset,
or both, and exits non-zero whenever any check fails.
"""

import sys

import click

from .configLoader import fconfigResolveProject
from .preflightChecks import (
    fpreflightColimaHostagentLog,
    fpreflightColimaVersion,
    fpreflightDaemon,
    fpreflightDockerContextActive,
    fpreflightLinuxDockerService,
)
from .preflightResult import fnPrintPreflightReport


__all__ = ["doctor", "flistRunDoctorChecks"]


def _flistBuildOnlyChecks(config):
    """Run build-only pre-flight helpers and return their results."""
    from .commandBuild import (
        _flistPreflightArch, _flistPreflightDisk, _flistPreflightMemory,
    )
    listResults = []
    listResults.extend(_flistPreflightArch(config))
    listResults.extend(_flistPreflightDisk())
    listResults.extend(_flistPreflightMemory())
    return listResults


def _flistStartOnlyChecks(config):
    """Run start-only pre-flight helpers and return their results."""
    from .commandStart import (
        _flistpreflightBindMountFormats, _flistpreflightBindMounts,
        _flistpreflightColimaSharedRoots, _flistpreflightPorts,
        _fpreflightContainerName, _fpreflightImage,
    )
    listResults = [_fpreflightImage(config)]
    listResults.extend(_flistpreflightPorts(config))
    listResults.append(_fpreflightContainerName(config))
    listResults.extend(_flistpreflightBindMounts(config))
    listResults.extend(_flistpreflightBindMountFormats(config))
    listResults.extend(_flistpreflightColimaSharedRoots(config))
    return listResults


def _flistOptionalSharedChecks():
    """Run optional shared probes; return only the non-None results."""
    listOptional = [
        fpreflightColimaVersion(),
        fpreflightColimaHostagentLog(),
        fpreflightLinuxDockerService(),
    ]
    return [r for r in listOptional if r is not None]


def _flistSharedChecks():
    """Run pre-flight helpers shared across every doctor scope."""
    listResults = [
        fpreflightDockerContextActive(),
        fpreflightDaemon(),
    ]
    listResults.extend(_flistOptionalSharedChecks())
    return listResults


def flistRunDoctorChecks(config, bBuildScope, bStartScope):
    """Return the full ordered list of PreflightResult for the chosen scope.

    ``config`` may be None — no project configured yet — in which case
    only the environment checks run. The project-scoped checks need a
    config to know what to check; the environment does not.
    """
    listResults = _flistSharedChecks()
    if any(r.sLevel == "fail" and r.sName == "docker-daemon"
           for r in listResults):
        return listResults
    if config is None:
        return listResults
    bBoth = (not bBuildScope and not bStartScope)
    if bBuildScope or bBoth:
        listResults.extend(_flistBuildOnlyChecks(config))
    if bStartScope or bBoth:
        listResults.extend(_flistStartOnlyChecks(config))
    return listResults


def _flistFilterQuiet(listResults, bQuiet):
    """Return listResults minus ok-level entries when bQuiet is True."""
    if not bQuiet:
        return listResults
    return [r for r in listResults if r.sLevel != "ok"]


def _ftCountLevels(listResults):
    """Return (iOk, iWarn, iFail) tallies across listResults."""
    iOk = sum(1 for r in listResults if r.sLevel == "ok")
    iWarn = sum(1 for r in listResults if r.sLevel == "warn")
    iFail = sum(1 for r in listResults if r.sLevel == "fail")
    return iOk, iWarn, iFail


def _fnPrintDoctorSummary(listResults):
    """Print the trailing `N ok / M warn / K fail` summary line."""
    iOk, iWarn, iFail = _ftCountLevels(listResults)
    click.echo(f"\n{iOk} ok / {iWarn} warn / {iFail} fail")


@click.command("doctor")
@click.option(
    "--project", "-p", "sProjectName", default=None,
    help="Project name (omit if in a project directory).",
)
@click.option(
    "--quiet", "bQuiet", is_flag=True, default=False,
    help="Suppress 'ok' lines; show only warns and fails.",
)
@click.option(
    "--build", "bBuildScope", is_flag=True, default=False,
    help="Run only the build-relevant subset.",
)
@click.option(
    "--start", "bStartScope", is_flag=True, default=False,
    help="Run only the start-relevant subset.",
)
def doctor(sProjectName, bQuiet, bBuildScope, bStartScope):
    """Run pre-flight checks and print a status report."""
    config = _fconfigResolveProjectOrNone(sProjectName)
    if config is None:
        click.echo(
            "No project configured yet; running the environment "
            "checks only. Project-scoped checks (image, ports, "
            "mounts) run once a project exists."
        )
    listResults = flistRunDoctorChecks(config, bBuildScope, bStartScope)
    fnPrintPreflightReport(_flistFilterQuiet(listResults, bQuiet))
    _fnPrintDoctorSummary(listResults)
    if any(r.sLevel == "fail" for r in listResults):
        sys.exit(1)


def _fconfigResolveProjectOrNone(sProjectName):
    """Resolve the project config, or None when no project exists yet.

    Doctor is most valuable *before* ``vaibify init`` has ever run —
    a Docker problem is the usual reason a first build fails — so an
    empty registry must not lock the environment checks away. The
    resolver prints its own guidance (run init, or pick a project)
    before exiting; catching the exit lets that guidance appear and
    the environment report still run. An explicit ``--project`` that
    does not resolve stays a hard error: asking for a project that
    does not exist is a mistake, not an absence.
    """
    if sProjectName:
        return fconfigResolveProject(sProjectName)
    try:
        return fconfigResolveProject(None)
    except SystemExit:
        return None
