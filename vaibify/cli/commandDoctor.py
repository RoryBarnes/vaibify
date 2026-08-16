"""CLI subcommand: vaibify doctor.

Aggregates every relevant pre-flight check into a single status
report, modelled after ``brew doctor`` / ``flutter doctor``. The
command runs the build-relevant subset, the start-relevant subset,
or both, and exits non-zero whenever any check fails.

A HOST project gets its own check set (host-mode plan, Phase D): the
things that matter are the registered directory still existing and
``git``/``python3`` being present — and the thing that must NOT run
is the Docker battery, because answering "install Docker" about a
project that never wanted one is the ordering bug the routes already
fixed. Every scope also reports WHICH checkout answers this command:
an editable install resolves to one working tree forever, and a
researcher juggling worktrees has already lost an afternoon to a hub
that ran code from the wrong one.
"""

import os
import shutil
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
from .preflightResult import PreflightResult, fnPrintPreflightReport


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
        _flistPreflightBindMountFormats, _flistPreflightBindMounts,
        _flistPreflightColimaSharedRoots, _flistPreflightPorts,
        _fpreflightContainerName, _fpreflightImage,
    )
    listResults = [_fpreflightImage(config)]
    listResults.extend(_flistPreflightPorts(config))
    listResults.append(_fpreflightContainerName(config))
    listResults.extend(_flistPreflightBindMounts(config))
    listResults.extend(_flistPreflightBindMountFormats(config))
    listResults.extend(_flistPreflightColimaSharedRoots(config))
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


def fpreflightInstalledCheckout():
    """Report which checkout's code is answering this command.

    An editable install binds the ``vaibify`` command to ONE working
    tree permanently, and nothing else ever says which: a hub launched
    from a worktree ran the MAIN checkout's code for a whole
    walkthrough day before anyone thought to ask (2026-08-14). Always
    informational — doctor cannot know which tree the researcher
    intended — but now the fact is on the report instead of in
    ``lsof``.
    """
    import vaibify
    sPackageDirectory = os.path.dirname(os.path.abspath(vaibify.__file__))
    return PreflightResult(
        sName="installed-checkout",
        sLevel="info",
        sMessage=(
            "this command runs the code checked out at "
            f"{os.path.dirname(sPackageDirectory)}"
        ),
    )


def _fdictHostProjectOrNone(config):
    """Return the registry record when config names a HOST project."""
    if config is None:
        return None
    from vaibify.config.registryManager import fdictGetProject
    dictProject = fdictGetProject(
        getattr(config, "sProjectName", "") or "",
    )
    if dictProject is None or dictProject.get("sMode") != "host":
        return None
    return dictProject


def _flistHostProjectChecks(dictProject):
    """The checks that matter for a project running on this machine.

    No Docker battery, deliberately: a host-only machine has no
    daemon, and a wall of daemon failures in front of the three
    checks that matter is how a report stops being read.
    """
    sName = dictProject.get("sName", "")
    listResults = [PreflightResult(
        sName="host-mode",
        sLevel="ok",
        sMessage=(
            f"'{sName}' is a host project; Docker is not required "
            "and was not checked"
        ),
    )]
    sDirectory = dictProject.get("sDirectory") or ""
    if os.path.isdir(sDirectory):
        listResults.append(PreflightResult(
            sName="host-directory", sLevel="ok",
            sMessage=f"project directory exists: {sDirectory}",
        ))
    else:
        listResults.append(PreflightResult(
            sName="host-directory", sLevel="fail",
            sMessage=(
                f"the registered directory is gone: {sDirectory}"
            ),
            sRemediation=(
                "Restore the directory, or re-register the project "
                "at its new location and revoke this entry."
            ),
        ))
    if shutil.which("git"):
        listResults.append(PreflightResult(
            sName="host-git", sLevel="ok", sMessage="git is on PATH",
        ))
    else:
        listResults.append(PreflightResult(
            sName="host-git", sLevel="fail",
            sMessage="git is not on PATH",
            sRemediation=(
                "Every vaibify workflow lives in a git repository; "
                "the badges, commits and pushes all shell out to git."
            ),
        ))
    if shutil.which("python3"):
        listResults.append(PreflightResult(
            sName="host-python3", sLevel="ok",
            sMessage="python3 is on PATH",
        ))
    else:
        listResults.append(PreflightResult(
            sName="host-python3", sLevel="warn",
            sMessage="python3 is not on PATH",
            sRemediation=(
                "vaibify's own helper programs (test markers, "
                "introspection) run under python3; steps written in "
                "other languages still work, but those helpers will "
                "not."
            ),
        ))
    return listResults


def flistRunDoctorChecks(config, bBuildScope, bStartScope):
    """Return the full ordered list of PreflightResult for the chosen scope.

    ``config`` may be None — no project configured yet — in which case
    only the environment checks run. The project-scoped checks need a
    config to know what to check; the environment does not. A HOST
    project replaces the Docker battery with the host check set, and
    the build/start scopes do not apply to it — there is no image and
    no container to scope to.
    """
    listResults = [fpreflightInstalledCheckout()]
    dictHostProject = _fdictHostProjectOrNone(config)
    if dictHostProject is not None:
        listResults.extend(_flistHostProjectChecks(dictHostProject))
        return listResults
    listResults.extend(_flistSharedChecks())
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
def fnDoctorCommand(sProjectName, bQuiet, bBuildScope, bStartScope):
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
