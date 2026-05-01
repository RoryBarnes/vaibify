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
    fpreflightColimaVersion,
    fpreflightDaemon,
    fpreflightDockerContextActive,
)
from .preflightResult import fnPrintPreflightReport


__all__ = ["doctor", "flistRunDoctorChecks"]


def _flistBuildOnlyChecks(config):
    """Run build-only pre-flight helpers and return their results."""
    from .commandBuild import (
        _fpreflightArch, _fpreflightDisk, _fpreflightMemory,
    )
    listResults = []
    listResults.extend(_fpreflightArch(config))
    listResults.extend(_fpreflightDisk())
    listResults.extend(_fpreflightMemory())
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


def _flistSharedChecks():
    """Run pre-flight helpers shared across every doctor scope."""
    listResults = [
        fpreflightDockerContextActive(),
        fpreflightDaemon(),
    ]
    resultColimaVersion = fpreflightColimaVersion()
    if resultColimaVersion is not None:
        listResults.append(resultColimaVersion)
    return listResults


def flistRunDoctorChecks(config, bBuildScope, bStartScope):
    """Return the full ordered list of PreflightResult for the chosen scope."""
    listResults = _flistSharedChecks()
    if any(r.sLevel == "fail" and r.sName == "docker-daemon"
           for r in listResults):
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
    config = fconfigResolveProject(sProjectName)
    listResults = flistRunDoctorChecks(config, bBuildScope, bStartScope)
    fnPrintPreflightReport(_flistFilterQuiet(listResults, bQuiet))
    _fnPrintDoctorSummary(listResults)
    if any(r.sLevel == "fail" for r in listResults):
        sys.exit(1)
