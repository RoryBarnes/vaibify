"""``vaibify setup-docker``: give this machine a Docker vaibify can use.

The command-line face of the dashboard's "Set up Docker" wizard, and
the same plan: ``runtimeSetupFacts`` reads the machine,
``runtimeSetupPlan`` lists what is left, ``runtimeSetupRunner`` runs
one step. After every step the machine is read again and planned
again, so the command resumes from wherever the machine is, and a step
that ran without changing anything stops the command rather than
repeating it. In a terminal an administrator step asks through
``sudo`` there.
"""

import sys

import click

from vaibify.install.runtimeSetupFacts import (
    S_PRIVILEGE_NONE,
    S_PRIVILEGE_PASSWORDLESS_SUDO,
    fdictReadHostFacts,
)
from vaibify.install.runtimeSetupPlan import (
    S_KIND_NEEDS_YOU,
    fdictPlanRuntimeSetup,
    fdictValidateColimaSettings,
)
from vaibify.install.runtimeSetupRunner import (
    S_PRIVILEGE_TERMINAL_SUDO,
    RuntimeSetupError,
    fnRunAction,
)


__all__ = ["fnSetupDockerCommand"]


I_EXIT_NEEDS_YOU = 2
_T_START_STEP_PREFIXES = ("start-", "resize-")


def _fsPrivilegeChannelForTerminal(dictFacts):
    if sys.stdin.isatty():
        return S_PRIVILEGE_TERMINAL_SUDO
    if dictFacts["sPrivilegeChannel"] == S_PRIVILEGE_PASSWORDLESS_SUDO:
        return S_PRIVILEGE_PASSWORDLESS_SUDO
    return S_PRIVILEGE_NONE


def _ftReadPlan(dictRequested):
    """Return ``(dictFacts, dictPlan)`` planned for a terminal."""
    dictFacts = fdictReadHostFacts()
    dictFacts["sPrivilegeChannel"] = _fsPrivilegeChannelForTerminal(dictFacts)
    try:
        dictSettings = fdictValidateColimaSettings(dictRequested, dictFacts)
    except ValueError as error:
        raise click.BadParameter(str(error)) from None
    return dictFacts, fdictPlanRuntimeSetup(dictFacts, dictSettings)


def _fnPrintStep(dictStep):
    click.echo("")
    click.echo(click.style(dictStep["sTitle"], bold=True))
    click.echo(dictStep["sExplanation"])
    for sCommand in dictStep["listCommandsToShow"]:
        click.echo("    " + sCommand)
    if dictStep.get("dictSettings"):
        dictSettings = dictStep["dictSettings"]
        click.echo(f"    CPUs: {dictSettings['iCpus']}, memory: "
                   f"{dictSettings['iMemoryGigabytes']} GB "
                   "(change with --cpus and --memory)")


def _fsChooseAction(dictStep, bYes):
    listActions = dictStep["listActions"]
    if len(listActions) == 1 or bYes:
        return listActions[0]["sActionName"]
    for iIndex, dictAction in enumerate(listActions, start=1):
        click.echo(f"  {iIndex}. {dictAction['sLabel']}")
    iChoice = click.prompt(
        "Choose", type=click.IntRange(1, len(listActions)), default=1)
    return listActions[iChoice - 1]["sActionName"]


def _fbWaitForTheResearcher(dictStep, bYes):
    """Return True to plan again after a needs-you step, False to stop."""
    if bYes or not sys.stdin.isatty():
        click.echo("\nThis step needs you. Do it, then run "
                   "`vaibify setup-docker` again.")
        return False
    click.prompt("Press Enter when you have done this (Ctrl-C to stop)",
                 default="", show_default=False)
    return True


def _fbShouldRunOptionalStep(dictStep, bYes):
    if bYes or not sys.stdin.isatty():
        click.echo("\nDocker is ready. Optional: " + dictStep["sTitle"]
                   + " (run `vaibify setup-docker` in a terminal to do it).")
        return False
    return click.confirm("Do this now?", default=False)


@click.command("setup-docker")
@click.option("--yes", "-y", "bYes", is_flag=True, default=False,
              help="Run every step without asking; choose the first "
                   "option where there is a choice.")
@click.option("--no-start", "bNoStart", is_flag=True, default=False,
              help="Install what is missing, but do not start or restart "
                   "a runtime.")
@click.option("--cpus", "iCpus", type=int, default=None,
              help="CPUs for a new Colima machine (default: all but one).")
@click.option("--memory", "iMemoryGigabytes", type=int, default=None,
              help="Memory in GB for a new Colima machine (default: half "
                   "the RAM, at least 4).")
def fnSetupDockerCommand(bYes, bNoStart, iCpus, iMemoryGigabytes):
    """Install and start the Docker runtime vaibify's containers need."""
    dictRequested = {sKey: iValue for sKey, iValue in (
        ("iCpus", iCpus), ("iMemoryGigabytes", iMemoryGigabytes))
        if iValue is not None}
    sLastStepRun = ""
    while True:
        dictFacts, dictPlan = _ftReadPlan(dictRequested)
        if not dictPlan["listSteps"]:
            click.echo("Docker is ready for vaibify.")
            return
        dictStep = dictPlan["listSteps"][0]
        _fnPrintStep(dictStep)
        if dictStep["sKind"] == S_KIND_NEEDS_YOU:
            if dictPlan["bReady"] or not _fbWaitForTheResearcher(
                    dictStep, bYes):
                sys.exit(0 if dictPlan["bReady"] else I_EXIT_NEEDS_YOU)
            continue
        if dictPlan["bReady"] and not _fbShouldRunOptionalStep(dictStep, bYes):
            return
        if bNoStart and dictStep["sStepName"].startswith(
                _T_START_STEP_PREFIXES):
            click.echo("\nStopping before this step (--no-start).")
            return
        if dictStep["sStepName"] == sLastStepRun:
            raise click.ClickException(
                f"'{dictStep['sTitle']}' ran but the machine still needs "
                "it. Run `vaibify doctor` for a diagnosis.")
        sActionName = _fsChooseAction(dictStep, bYes)
        if not bYes and not click.confirm("Run this step?", default=True):
            return
        try:
            fnRunAction(dictPlan, sActionName, dictFacts["sPrivilegeChannel"],
                        click.echo, bInteractive=sys.stdin.isatty())
        except RuntimeSetupError as error:
            raise click.ClickException(str(error)) from None
        sLastStepRun = dictStep["sStepName"]
