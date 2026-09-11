"""CLI subcommand: vaibify doctor.

Aggregates every relevant pre-flight check into a single status
report, modelled after ``brew doctor`` / ``flutter doctor``. The
command runs the build-relevant subset, the start-relevant subset,
or both, and exits non-zero whenever any check fails.

Three host-side prerequisites reached a researcher one crash at a time
on 2026-09-05 -- a Docker context aimed at a stopped runtime, an absent
host ``gh``, and a missing council credential record. They share no
mechanism and one shape: something the MACHINE must provide, that the
repository cannot carry, that nothing checks until the moment it is
needed. This command is where that shape gets asked about in advance.
It REPORTS and never gates, and it must never offer to satisfy a
prerequisite whose whole purpose is that only the maintainer can.

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
    fpreflightCouncilCredentialEvidence,
    fpreflightDaemon,
    fpreflightDockerContextActive,
    fpreflightDockerEndpoint,
    fpreflightLinuxDockerService,
)
from .preflightResult import (
    S_LEVEL_FAIL, S_LEVEL_NOT_CHECKED, S_LEVEL_OK, S_LEVEL_WARN,
    S_SCOPE_CONTAINER, S_SCOPE_HOST, S_SCOPE_PROJECT, PreflightResult,
    flistRenderResultsForJson, fnPrintScopedPreflightReport,
)


__all__ = ["doctor", "flistRunDoctorChecks"]


def _flistBuildOnlyChecks(config):
    """Run build-only pre-flight helpers and return their results."""
    from .commandBuild import (
        _flistPreflightArch, _flistPreflightDisk, _flistPreflightMemory,
    )
    from .doctorHostChecks import (
        flistCheckDepositScratchSpace, flistCheckResourceAllocation,
    )
    listResults = []
    listResults.extend(_flistPreflightArch(config))
    listResults.extend(_flistPreflightDisk())
    listResults.extend(_flistPreflightMemory())
    listResults.extend(flistCheckResourceAllocation(config))
    listResults.extend(flistCheckDepositScratchSpace(config))
    return listResults


def _flistStartOnlyChecks(config):
    """Run start-only pre-flight helpers and return their results."""
    from .commandStart import (
        _flistPreflightBindMountFormats, _flistPreflightBindMounts,
        _flistPreflightColimaSharedRoots, _flistPreflightPorts,
        _fpreflightContainerName, _fpreflightImage,
        flistPreflightSecrets,
    )
    listResults = [_fpreflightImage(config)]
    listResults.extend(flistPreflightSecrets(config))
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
    return [
        preflightResult for preflightResult in listOptional
        if preflightResult is not None
    ]


def _flistSharedChecks():
    """Run pre-flight helpers shared across every doctor scope.

    The endpoint sits beside the context because they are different
    facts and only the pair identifies a context aimed at a runtime
    that is not running. The council's credential evidence sits here
    because it is the third member of a family the 2026-09-05
    walkthrough surfaced one crash at a time: a prerequisite the HOST
    must provide, that the repository cannot carry, that nothing
    checked until the moment it was needed.
    """
    listResults = [
        fpreflightDockerContextActive(),
        fpreflightDockerEndpoint(),
        fpreflightDaemon(),
    ]
    listResults.extend(_flistOptionalSharedChecks())
    listResults.append(fpreflightCouncilCredentialEvidence())
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


def _fconnectionOpenDockerQuietly():
    """Return a DockerConnection, or None when the daemon is unreachable."""
    try:
        from vaibify.docker.dockerConnection import DockerConnection
        return DockerConnection()
    except Exception:
        return None


def _flistUnassessedContainerScope(sReason):
    """Return the container-scope checks as unassessed, with the reason.

    One result per thing that WOULD have been checked, rather than one
    summary line, because a scope that vanishes from the report reads
    as a scope with nothing wrong in it.
    """
    return [
        PreflightResult(
            sName=sName, sLevel=S_LEVEL_NOT_CHECKED,
            sScope=S_SCOPE_CONTAINER, sMessage=sReason,
        )
        for sName in (
            "network-attachment", "resolver-configuration",
            "dns-resolution",
        )
    ]


def _flistContainerScopeChecks(config, bOnline):
    """Run the checks that need a RUNNING container, or say why not."""
    from vaibify.docker.containerManager import fdictGetContainerStatus
    from .doctorNetwork import flistDiagnoseContainerNetwork
    sContainerName = config.sProjectName
    dictStatus = fdictGetContainerStatus(sContainerName)
    if not dictStatus["bExists"]:
        return _flistUnassessedContainerScope(
            f"no container named '{sContainerName}' exists, so nothing "
            "inside one could be examined. Run `vaibify start` first."
        )
    if not dictStatus["bRunning"]:
        return _flistUnassessedContainerScope(
            f"the container '{sContainerName}' exists but is "
            f"{dictStatus['sStatus']}; nothing can be examined inside "
            "a container that is not running."
        )
    connectionDocker = _fconnectionOpenDockerQuietly()
    if connectionDocker is None:
        return _flistUnassessedContainerScope(
            "the Docker connection could not be opened, so nothing "
            "inside the container could be examined."
        )
    return flistDiagnoseContainerNetwork(
        config, sContainerName, connectionDocker, bOnline,
    )


def _flistProjectScopeChecks(config):
    """Run the checks about vaibify's own record of this project.

    The journal check runs whatever the container's state, because the
    journal is a file on this host and a quarantine is exactly what a
    researcher meets when the container will not do anything. The
    other two need a running container and report unassessed without
    one.
    """
    from vaibify.docker.containerManager import fdictGetContainerStatus
    from . import doctorProjectChecks
    sContainerName = config.sProjectName
    connectionDocker = _fconnectionOpenDockerQuietly()
    listResults = doctorProjectChecks.flistCheckJournalQuarantine(
        sContainerName, connectionDocker,
    )
    if connectionDocker is None or not fdictGetContainerStatus(
        sContainerName,
    )["bRunning"]:
        listResults.extend(_flistUnassessedProjectScope())
        return listResults
    # The startup observations read the readiness marker under the
    # WORKSPACE root, so they never depended on finding a repository
    # and must not be gated on one. Gating them was a real loss: a
    # project that had never reached Level 3 got no observations, no
    # ownership assessment, and no explanation for either.
    listResults.extend(doctorProjectChecks.flistReportStartupObservations(
        connectionDocker, sContainerName, config.sWorkspaceRoot,
    ))
    sRepoPath = doctorProjectChecks.fsDiscoverProjectRepoPath(
        connectionDocker, sContainerName, config.sWorkspaceRoot,
    )
    if not sRepoPath:
        listResults.extend(_flistUnassessedRepositoryChecks(
            "no git repository was found directly under "
            f"{config.sWorkspaceRoot}, so the checks that read one "
            "could not run."
        ))
        return listResults
    listResults.extend(doctorProjectChecks.flistCheckEnvelopeCurrency(
        connectionDocker, sContainerName, sRepoPath,
    ))
    listResults.extend(doctorProjectChecks.flistCheckWorkspaceOwnership(
        connectionDocker, sContainerName, sRepoPath,
    ))
    return listResults


def _flistUnassessedRepositoryChecks(sReason):
    """Return the checks that need a project REPOSITORY, as unassessed."""
    return [
        PreflightResult(
            sName=sName, sLevel=S_LEVEL_NOT_CHECKED,
            sScope=S_SCOPE_PROJECT, sMessage=sReason,
        )
        for sName in ("envelope-image-currency", "workspace-ownership")
    ]


def _flistUnassessedProjectScope(sReason=""):
    """Return the container-dependent project checks as unassessed."""
    sMessage = sReason or (
        "the container is not running, so the files these checks read "
        "could not be examined."
    )
    return [
        PreflightResult(
            sName=sName, sLevel=S_LEVEL_NOT_CHECKED,
            sScope=S_SCOPE_PROJECT, sMessage=sMessage,
        )
        for sName in (
            "envelope-image-currency", "workspace-ownership",
            "startup-observations",
        )
    ]


def flistRunDoctorChecks(
    config, bBuildScope, bStartScope, bContainerScope=False,
    bOnline=False,
):
    """Return the full ordered list of PreflightResult for the chosen scope.

    ``config`` may be None — no project configured yet — in which case
    only the environment checks run. The project-scoped checks need a
    config to know what to check; the environment does not. A HOST
    project replaces the Docker battery with the host check set, and
    the build/start/container scopes do not apply to it — there is no
    image and no container to scope to.

    With no scope flag every scope runs. With one or more, only those
    run, and the exit code treats exactly those as REQUESTED.
    """
    listResults = [fpreflightInstalledCheckout()]
    dictHostProject = _fdictHostProjectOrNone(config)
    if dictHostProject is not None:
        listResults.extend(_flistHostProjectChecks(dictHostProject))
        return listResults
    listResults.extend(_flistSharedChecks())
    if any(
        preflightResult.sLevel == S_LEVEL_FAIL
        and preflightResult.sName == "docker-daemon"
        for preflightResult in listResults
    ):
        return listResults
    if config is None:
        return listResults
    bEveryScope = not (bBuildScope or bStartScope or bContainerScope)
    if bBuildScope or bEveryScope:
        listResults.extend(_flistBuildOnlyChecks(config))
    if bStartScope or bEveryScope:
        listResults.extend(_flistStartOnlyChecks(config))
    if bContainerScope or bEveryScope:
        listResults.extend(_flistContainerScopeChecks(config, bOnline))
        listResults.extend(_flistProjectScopeChecks(config))
    return listResults


def _flistFilterQuiet(listResults, bQuiet):
    """Return listResults minus ok-level entries when bQuiet is True."""
    if not bQuiet:
        return listResults
    return [
        preflightResult for preflightResult in listResults
        if preflightResult.sLevel != S_LEVEL_OK
    ]


def _ftCountLevels(listResults):
    """Return (iOk, iWarn, iFail, iNotChecked) tallies across listResults."""
    dictTally = {sLevel: 0 for sLevel in (
        S_LEVEL_OK, S_LEVEL_WARN, S_LEVEL_FAIL, S_LEVEL_NOT_CHECKED,
    )}
    for preflightResult in listResults:
        if preflightResult.sLevel in dictTally:
            dictTally[preflightResult.sLevel] += 1
    iOk = dictTally[S_LEVEL_OK]
    iWarn = dictTally[S_LEVEL_WARN]
    iFail = dictTally[S_LEVEL_FAIL]
    iNotChecked = dictTally[S_LEVEL_NOT_CHECKED]
    return iOk, iWarn, iFail, iNotChecked


def _fnPrintDoctorSummary(listResults):
    """Print the trailing tally line, counting unassessed separately."""
    iOk, iWarn, iFail, iNotChecked = _ftCountLevels(listResults)
    click.echo(
        f"\n{iOk} ok / {iWarn} warn / {iFail} fail "
        f"/ {iNotChecked} not checked"
    )


I_EXIT_EVERYTHING_ASSESSED = 0
I_EXIT_SOMETHING_FAILED = 1
I_EXIT_SCOPE_UNASSESSED = 2

_DICT_SCOPE_FOR_FLAG = {
    "bContainerScope": (S_SCOPE_CONTAINER, S_SCOPE_PROJECT),
}


def fiResolveDoctorExitCode(listResults, tRequestedScopes):
    """Return the process exit code for one doctor run.

    ``1`` beats ``2``: a failure is a stronger statement than an
    absence, and a caller that branches on non-zero gets the same
    answer either way.

    ``2`` fires for ANY applicable check that could not be assessed
    inside an EXPLICITLY requested scope -- not only when the whole
    scope was unassessable. One timed-out probe surrounded by
    successes exiting 0 is a skipped-green lane wearing a diagnostic's
    clothes, which is the failure this repository has shipped before
    (`docker info || exit 0`). With no scope flag nothing is
    explicitly requested, so an idle machine's unassessed container
    checks do not turn an ordinary run non-zero.
    """
    if any(
        preflightResult.sLevel == S_LEVEL_FAIL
        for preflightResult in listResults
    ):
        return I_EXIT_SOMETHING_FAILED
    if any(
        preflightResult.sLevel == S_LEVEL_NOT_CHECKED
        and preflightResult.sScope in tRequestedScopes
        for preflightResult in listResults
    ):
        return I_EXIT_SCOPE_UNASSESSED
    return I_EXIT_EVERYTHING_ASSESSED


def _ftRequestedScopes(bBuildScope, bStartScope, bContainerScope):
    """Return the scopes the researcher asked for by name."""
    tScopes = ()
    if bBuildScope or bStartScope:
        tScopes += (S_SCOPE_HOST,)
    if bContainerScope:
        tScopes += (S_SCOPE_CONTAINER, S_SCOPE_PROJECT)
    return tScopes


def _fnExplainOneCheck(listResults, sCheckName):
    """Print the mechanism behind one named check, or say it did not run."""
    listMatched = [
        preflightResult for preflightResult in listResults
        if preflightResult.sName == sCheckName
    ]
    if not listMatched:
        click.echo(
            f"No check named '{sCheckName}' ran in this scope. The "
            "report lists every check that did."
        )
        return
    for preflightResult in listMatched:
        click.echo(f"{preflightResult.sName}: {preflightResult.sMessage}")
        if preflightResult.sMechanism:
            click.echo("\nHow this is decided:")
            click.echo(preflightResult.sMechanism)
        else:
            click.echo(
                "\nThis check records no mechanism note beyond its "
                "own message."
            )


@click.command("doctor")
@click.option(
    "--project", "-p", "sProjectName", default=None,
    help="Project name (omit if in a project directory).",
)
@click.option(
    "--quiet", "bQuiet", is_flag=True, default=False,
    help="Suppress 'ok' lines; show only warns, fails and unassessed.",
)
@click.option(
    "--build", "bBuildScope", is_flag=True, default=False,
    help="Run only the build-relevant subset.",
)
@click.option(
    "--start", "bStartScope", is_flag=True, default=False,
    help="Run only the start-relevant subset.",
)
@click.option(
    "--container", "bContainerScope", is_flag=True, default=False,
    help="Run only the checks that look inside the running container "
         "and at this project's own records.",
)
@click.option(
    "--online", "bOnline", is_flag=True, default=False,
    help="Permit connection attempts to the host this project already "
         "depends on. Without it, doctor resolves names but opens no "
         "connections.",
)
@click.option(
    "--json", "bJson", is_flag=True, default=False,
    help="Emit the results as JSON instead of a human report.",
)
@click.option(
    "--explain", "sExplainCheck", default="",
    help="Print how one named check decides its answer, and nothing "
         "else.",
)
def fnDoctorCommand(
    sProjectName, bQuiet, bBuildScope, bStartScope, bContainerScope,
    bOnline, bJson, sExplainCheck,
):
    """Run pre-flight checks and print a status report."""
    config = _fconfigResolveProjectOrNone(sProjectName)
    if config is None and not bJson:
        click.echo(
            "No project configured yet; running the environment "
            "checks only. Project-scoped checks (image, ports, "
            "mounts) run once a project exists."
        )
    listResults = flistRunDoctorChecks(
        config, bBuildScope, bStartScope, bContainerScope, bOnline,
    )
    if sExplainCheck:
        _fnExplainOneCheck(listResults, sExplainCheck)
        return
    _fnReportDoctorResults(listResults, bQuiet, bJson)
    sys.exit(fiResolveDoctorExitCode(
        listResults,
        _ftRequestedScopes(bBuildScope, bStartScope, bContainerScope),
    ))


def _fnReportDoctorResults(listResults, bQuiet, bJson):
    """Render the results in whichever form was asked for."""
    if bJson:
        from .commandUtilsDocker import fnPrintJson
        iOk, iWarn, iFail, iNotChecked = _ftCountLevels(listResults)
        fnPrintJson({
            "listResults": flistRenderResultsForJson(listResults),
            "iOk": iOk, "iWarn": iWarn, "iFail": iFail,
            "iNotChecked": iNotChecked,
        })
        return
    fnPrintScopedPreflightReport(_flistFilterQuiet(listResults, bQuiet))
    _fnPrintDoctorSummary(listResults)


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
