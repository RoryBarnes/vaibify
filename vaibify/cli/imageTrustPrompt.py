"""Ask, on the command line, how an image vaibify did not build may run.

The same three answers, with the same words, as the dashboard modal:
both read :mod:`vaibify.config.imageTrust`. Nothing is ever chosen
silently. Interactively the researcher picks one of the three; without a
terminal the answer must arrive as ``--image-trust`` (and, separately,
``--with-credentials``), or the start stops and says so.
"""

__all__ = ["fnConfirmImageTrustOrExit"]

import sys

import click

from vaibify.config import imageTrust
from vaibify.config.registryManager import fnRecordImageTrust


def fnConfirmImageTrustOrExit(config, sChoice, bWithCredentials):
    """Make sure the project's image has a recorded answer, or exit.

    A built image needs none. An explicit ``--image-trust`` is recorded
    as the researcher's answer for the image now under the project's
    tag. Without one, an answered digest proceeds, and an unanswered
    one asks (interactive) or stops with the flags to pass.
    """
    from vaibify.docker.containerManager import (
        fdictBuildImageTrustPromptForProject,
    )
    if bWithCredentials and not sChoice:
        raise click.UsageError("--with-credentials needs --image-trust.")
    dictPrompt = fdictBuildImageTrustPromptForProject(config.sProjectName)
    if dictPrompt is None or dictPrompt["bBuiltByVaibify"]:
        return
    if sChoice is None:
        if _fbAnswerMatchesImage(dictPrompt):
            return
        sChoice, bWithCredentials = _ftAskOrExit(dictPrompt)
    _fnRecordAnswer(config.sProjectName, dictPrompt, sChoice, bWithCredentials)
    if sChoice == imageTrust.S_TRUST_INSPECT:
        click.echo(
            "Inspect only: no persistent container is created. Rerun the "
            "image in the verification lane, or start again choosing "
            "--image-trust restricted or as-built.")
        sys.exit(0)


def _fbAnswerMatchesImage(dictPrompt):
    """True when an answer is recorded for exactly this image digest."""
    dictAnswer = dictPrompt["dictCurrentAnswer"]
    return dictAnswer.get("sImageDigest") == dictPrompt["sImageDigest"]


def _fnRecordAnswer(sProjectName, dictPrompt, sChoice, bWithCredentials):
    """Store the answer for the prompt's digest in the host registry."""
    fnRecordImageTrust(sProjectName, imageTrust.fdictBuildTrustRecord(
        dictPrompt["sImageDigest"], sChoice, bWithCredentials))


def _ftAskOrExit(dictPrompt):
    """Return ``(sChoice, bWithCredentials)`` from the terminal, or exit 2."""
    _fnPrintPrompt(dictPrompt)
    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        click.echo(
            "\nThere is no terminal to ask on. Choose with "
            "--image-trust {restricted,as-built,inspect}, and add "
            "--with-credentials only if the image's code may read your "
            "stored credentials.", err=True)
        sys.exit(2)
    listOptions = dictPrompt["listOptions"]
    iPicked = click.prompt(
        "Choose 1, 2 or 3", type=click.IntRange(1, len(listOptions)))
    sChoice = listOptions[iPicked - 1]["sChoice"]
    if sChoice == imageTrust.S_TRUST_INSPECT:
        return sChoice, False
    return sChoice, _fbAskAboutCredentials(dictPrompt["dictCredentials"])


def _fbAskAboutCredentials(dictCredentials):
    """Ask the separate, default-off credentials question."""
    click.echo(f"\n{dictCredentials['sLabel']}")
    for sLine in dictCredentials["listDetailLines"]:
        click.echo(f"  - {sLine}")
    return click.confirm("Attach them?", default=False)


def _fnPrintPrompt(dictPrompt):
    """Print the image's provenance and the three options with their text."""
    click.echo(
        "This image was not built by vaibify. Choose how it may run.\n"
        f"  Digest:     {dictPrompt['sImageDigest']}\n"
        f"  Size:       {dictPrompt['iSizeBytes']} bytes\n"
        f"  User:       {dictPrompt['sDeclaredUser'] or '(not declared)'}\n"
        f"  Entrypoint: "
        f"{' '.join(dictPrompt['listDeclaredEntrypoint']) or '(none)'}\n"
        f"  Obtained from: {dictPrompt['sObtainedFrom'] or '(unknown)'}\n")
    for iIndex, dictOption in enumerate(dictPrompt["listOptions"], start=1):
        click.echo(f"{iIndex}. {dictOption['sLabel']} -- {dictOption['sSummary']}")
        for sLine in dictOption["listDetailLines"]:
            click.echo(f"     - {sLine}")
    click.echo(
        "Restricted-mode compatibility contract: "
        f"{dictPrompt['sRestrictedDocumentationUrl']}")
