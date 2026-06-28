"""CLI subcommand: vaibify sessions.

Lists live Vaibify hub/viewer sessions and the containers each holds,
and gracefully stops them. This is the host-side analog of
``jupyter server list`` / ``jupyter server stop``; it is not invokable
from inside a container. ``stop`` refuses any PID that is not a known
live Vaibify session, and ``--all`` excludes the current process so a
session never terminates itself.
"""

import os
import signal
import sys

import click

from vaibify.config.containerLock import flistReadAllLockHolders
from vaibify.config.sessionRegistry import flistReadAllSlots


@click.group("sessions", invoke_without_command=True)
@click.pass_context
def sessions(ctx):
    """List or stop live Vaibify sessions."""
    if ctx.invoked_subcommand is None:
        fnListSessions()


def fnListSessions():
    """Print every live hub/viewer session and the containers it holds."""
    listSlots = flistReadAllSlots()
    if not listSlots:
        click.echo("No live Vaibify sessions.")
        return
    listHolders = flistReadAllLockHolders()
    for dictSlot in listSlots:
        fnPrintSessionLine(dictSlot, listHolders)


def fnPrintSessionLine(dictSlot, listHolders):
    """Print one session's identity and the containers it holds."""
    sContainers = fsJoinHeldContainers(dictSlot, listHolders)
    click.echo(
        f"pid={dictSlot.get('iPid')} "
        f"role={dictSlot.get('sRole')} "
        f"port={dictSlot.get('iPort')} "
        f"started={dictSlot.get('sStartedIso')} "
        f"containers=[{sContainers}]"
    )


def fsJoinHeldContainers(dictSlot, listHolders):
    """Return comma-joined container names held on the session's port."""
    iPort = dictSlot.get("iPort")
    if iPort is None:
        return ""
    listNames = [
        str(dictHolder.get("sProjectName"))
        for dictHolder in listHolders
        if dictHolder.get("iPort") == iPort
    ]
    return ", ".join(listNames)


@sessions.command("stop")
@click.argument("ipid", metavar="PID", type=int, required=False)
@click.option(
    "--all", "bAll", is_flag=True,
    help="Stop every live session except the current one.",
)
def stop(ipid, bAll):
    """Gracefully stop a Vaibify session by PID, or every session with --all."""
    if bAll:
        fnStopAllSessions()
        return
    if ipid is None:
        click.echo("Error: provide a PID or use --all.", err=True)
        sys.exit(1)
    fnStopSession(ipid)


def fnStopSession(iPid):
    """SIGTERM a PID only when it is a known live Vaibify session."""
    setSessionPids = {dictSlot.get("iPid") for dictSlot in flistReadAllSlots()}
    if iPid not in setSessionPids:
        click.echo(f"Error: pid {iPid} is not a Vaibify session.", err=True)
        sys.exit(1)
    fnTerminateSessionPid(iPid)


def fnStopAllSessions():
    """SIGTERM every live session slot except the current process."""
    iSelfPid = os.getpid()
    listOtherPids = [
        dictSlot.get("iPid") for dictSlot in flistReadAllSlots()
        if dictSlot.get("iPid") != iSelfPid
    ]
    if not listOtherPids:
        click.echo("No other Vaibify sessions to stop.")
        return
    for iPid in listOtherPids:
        fnTerminateSessionPid(iPid)


def fnTerminateSessionPid(iPid):
    """Send SIGTERM to one session PID and report it.

    Refuses a non-positive PID so a corrupted slot can never make
    ``os.kill`` signal the whole process group, and tolerates a session
    that exited between enumeration and the signal (TOCTOU).
    """
    if not isinstance(iPid, int) or isinstance(iPid, bool) or iPid <= 0:
        click.echo(f"Skipped invalid session pid={iPid}.", err=True)
        return
    try:
        os.kill(iPid, signal.SIGTERM)
    except ProcessLookupError:
        click.echo(f"Vaibify session pid={iPid} already exited.")
        return
    except OSError as error:
        click.echo(f"Error: could not stop pid={iPid}: {error}", err=True)
        return
    click.echo(f"Stopped Vaibify session pid={iPid}.")
