"""Ask, on the command line, to approve what a container may reach.

The same words as the dashboard modal: both read
:mod:`vaibify.config.reachApproval`. Nothing is ever approved silently.
Interactively the researcher answers yes or no, default no; without a
terminal the approval must arrive as ``--approve-reach``, or the start
stops and says so.
"""

__all__ = ["fnConfirmReachOrExit"]

import sys

import click

from vaibify.config import reachApproval
from vaibify.config.registryManager import fnRecordReachApproval


def fnConfirmReachOrExit(config, bApprove):
    """Make sure the file's reach-widening settings are approved, or exit.

    Nothing widens, or the record covers the file: return. The flag
    records and continues. A terminal asks, and a refusal stops the
    start with nothing recorded; no terminal stops it naming the flag.
    """
    from vaibify.docker.containerManager import fdictBuildReachPromptIfUnapproved
    dictPrompt = fdictBuildReachPromptIfUnapproved(config)
    if dictPrompt is None:
        return
    if not dictPrompt["bRegistered"]:
        click.echo(dictPrompt["sText"], err=True)
        sys.exit(2)
    if not bApprove:
        _fnAskOrExit(dictPrompt)
    fnRecordReachApproval(
        config.sProjectName, reachApproval.fdictBuildReachApprovalRecord(config))


def _fnAskOrExit(dictPrompt):
    """Ask on the terminal, default no; exit 2 without one or on a no."""
    click.echo(f"{dictPrompt['sTitle']}\n{dictPrompt['sText']}")
    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        click.echo(
            "\nThere is no terminal to ask on. Approve these settings "
            "with --approve-reach, or change vaibify.yml.", err=True)
        sys.exit(2)
    if not click.confirm("Approve and start?", default=False):
        click.echo("Not started; nothing was recorded.")
        sys.exit(2)
