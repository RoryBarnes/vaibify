"""``vaibify remote <ssh-host>`` — the laptop half of a remote session.

This process is a tunnel and a browser launcher. It is deliberately
not a second vaibify: the backend beside the researcher's projects is
the only control plane, and everything this does is get a browser
pointed at it through a loopback-to-loopback forward.

Two rules shape the whole module.

**Nothing a user typed reaches the remote command string.** OpenSSH
hands its remote command to the far side's login shell, so the command
is fixed source text with exactly one interpolated value: an integer
port, cast and range-checked. There is deliberately no ``--project``
option -- a project name may contain a space, and the safety argument
the codebase relies on elsewhere ("no shell=True exists in the
package") does not survive a feature that builds a remote shell
command. The project is chosen in the dashboard afterwards, over HTTP,
where the character set is already handled correctly.

**The client never opens an address the remote chose.** The URL is
built from the port this process picked and forwarded. See
``remoteProtocol`` for why the schema has no URL-shaped field at all.

The reconnection ladder is the other half of the continuity contract:
the tunnel is retried for as long as the hub promises to hold the
session, and not one attempt longer, because an attempt landing after
that window is refused and the refusal reads to a researcher as a dead
server.
"""

import re
import subprocess
import sys
import time

import click

from .remoteProtocol import (
    RemoteProtocolError,
    fdictParseStartupRecord,
    fsLocalDashboardUrl,
)

# How long the client keeps trying to rebuild a dropped tunnel. Matched
# to the hub's hold window on purpose: retrying longer would present a
# credential the hub has already revoked, and retrying for less would
# give up while the session was still there for the taking.
F_RECONNECT_WINDOW_SECONDS = 900.0
F_RECONNECT_MAX_DELAY_SECONDS = 30.0
F_RECONNECT_MARGIN_SECONDS = 5.0

# How long to wait for the helper's one line before concluding the far
# side is not going to answer.
F_RECORD_TIMEOUT_SECONDS = 90.0

# A destination is user input that becomes an ARGV ELEMENT, never part
# of the command string. It still may not look like an option, because
# argv position is not protection against a value that starts with a
# dash and is read as a flag by the program receiving it.
_RE_SSH_DESTINATION = re.compile(r"^[A-Za-z0-9._-]+(@[A-Za-z0-9._-]+)?$")

__all__ = ["fnRemoteCommand"]


class RemoteClientError(Exception):
    """A remote session could not be established. Always explained."""


def fnValidateSshDestination(sDestination):
    """Raise unless sDestination is safe to pass to ssh as one argv."""
    if not sDestination:
        raise RemoteClientError("no SSH destination was given")
    if sDestination.startswith("-"):
        raise RemoteClientError(
            f"the destination {sDestination!r} begins with a dash, so "
            "ssh would read it as an option rather than a host",
        )
    if not _RE_SSH_DESTINATION.match(sDestination):
        raise RemoteClientError(
            f"the destination {sDestination!r} is not a plain "
            "[user@]host. Put ports, identity files, and proxy jumps "
            "in ~/.ssh/config, where OpenSSH already understands them.",
        )


def fsaBuildSshCommand(sDestination, iPort):
    """Return the full ssh argv for one remote session.

    The remote command is fixed source text apart from the port, which
    is cast and bounded by the caller. The destination is its own argv
    element and never appears inside the command string.
    """
    sRemoteCommand = (
        f"vaibify remote-helper --port {int(iPort)}"
    )
    return [
        "ssh",
        "-o", "ExitOnForwardFailure=yes",
        "-o", "ServerAliveInterval=15",
        "-o", "ServerAliveCountMax=3",
        # No remote pseudo-terminal: the helper speaks protocol on
        # stdout, and a PTY would mix its streams and mangle newlines.
        "-T",
        "-L", f"127.0.0.1:{int(iPort)}:127.0.0.1:{int(iPort)}",
        sDestination,
        sRemoteCommand,
    ]


def fiChooseLocalPort(iExplicitPort=None):
    """Return a port free on this machine, or the explicit one.

    An explicit port is used verbatim and its conflict is reported
    rather than silently worked around: a researcher who named a port
    is usually matching a bookmark or a firewall rule, and quietly
    moving would defeat both.
    """
    from .portAllocator import fbIsPortFree, fiPickFreePort
    if iExplicitPort:
        iPort = int(iExplicitPort)
        if not 1 <= iPort <= 65535:
            raise RemoteClientError(
                f"port {iPort} is not a usable TCP port",
            )
        if not fbIsPortFree(iPort):
            raise RemoteClientError(
                f"port {iPort} is already in use on this machine. "
                "Choose another, or omit --port to let vaibify pick.",
            )
        return iPort
    return fiPickFreePort(iPreferred=18050)


def ffNextReconnectDelaySeconds(iAttempt, fElapsedSeconds):
    """Return the next backoff delay, or -1 when the window is spent.

    The same shape the browser uses, for the same reason: the hub's
    hold window is the budget, and an attempt scheduled past it would
    present a credential that has already been revoked.
    """
    fDelay = min(2.0 ** iAttempt, F_RECONNECT_MAX_DELAY_SECONDS)
    fBudget = F_RECONNECT_WINDOW_SECONDS - F_RECONNECT_MARGIN_SECONDS
    if fElapsedSeconds + fDelay > fBudget:
        return -1.0
    return fDelay


def _fprocessStartTunnel(sDestination, iPort):
    """Launch ssh with the forward and the helper command."""
    return subprocess.Popen(
        fsaBuildSshCommand(sDestination, iPort),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


def _fsReadStartupLine(processTunnel):
    """Return the helper's first stdout line, or raise with context.

    A silent remote is the commonest real failure and it has several
    causes, so the SSH stderr is carried into the message: "no record"
    alone would send a researcher to debug vaibify when the answer is
    usually an authentication prompt or a missing PATH entry.
    """
    sLine = processTunnel.stdout.readline()
    if sLine:
        return sLine
    processTunnel.poll()
    sErrors = ""
    try:
        sErrors = processTunnel.stderr.read() or ""
    except Exception:
        pass
    raise RemoteClientError(
        "the remote produced no vaibify startup record.\n"
        f"ssh exited with status {processTunnel.returncode}.\n"
        f"{sErrors.strip()}"
    )


def ftEstablishSession(sDestination, iPort):
    """Open one tunnel and return (process, validated record)."""
    processTunnel = _fprocessStartTunnel(sDestination, iPort)
    try:
        dictRecord = fdictParseStartupRecord(
            _fsReadStartupLine(processTunnel), iPort,
        )
    except (RemoteProtocolError, RemoteClientError):
        _fnStopTunnel(processTunnel)
        raise
    return processTunnel, dictRecord


def _fnStopTunnel(processTunnel):
    """Close the tunnel's stdin and stop the ssh process."""
    if processTunnel is None:
        return
    try:
        if processTunnel.stdin:
            processTunnel.stdin.close()
        processTunnel.wait(timeout=5)
    except Exception:
        try:
            processTunnel.kill()
        except Exception:
            pass


def _fnReportSession(dictRecord, iPort):
    """Tell the researcher what they are attached to, and how to stop."""
    click.echo(
        f"Connected to {dictRecord['sHostname']} "
        f"({dictRecord['sExecutionMode']} mode)."
    )
    click.echo(f"  Dashboard: http://127.0.0.1:{iPort}")
    click.echo(
        "  The browser tab that just opened is signed in; this address "
        "alone cannot sign in."
    )
    click.echo("  Press Ctrl-C to close the tunnel. The remote hub keeps")
    click.echo("  running, so a pipeline in flight is not interrupted.")


def _fiHoldAndReconnect(processTunnel, sDestination, iPort):
    """Keep the tunnel up, rebuilding it while the window allows.

    A rebuilt tunnel does NOT re-open the browser. Inside the hub's
    hold window the tab's own socket reconnects on its own, and
    throwing a second tab at the researcher would be both startling and
    wrong -- the first one is still the signed-in session.
    """
    iAttempt = 0
    fElapsed = 0.0
    while True:
        processTunnel.wait()
        fDelay = ffNextReconnectDelaySeconds(iAttempt, fElapsed)
        if fDelay < 0:
            click.echo(
                "\nThe tunnel stayed down for "
                f"{F_RECONNECT_WINDOW_SECONDS / 60:.0f} minutes, so the "
                "remote session has expired. Any run you started is "
                "still going on that machine; re-run this command to "
                "pick it back up.",
                err=True,
            )
            return 1
        click.echo(
            f"\nTunnel lost. Reconnecting in {fDelay:.0f}s "
            f"(attempt {iAttempt + 1}).",
            err=True,
        )
        time.sleep(fDelay)
        iAttempt += 1
        fElapsed += fDelay
        try:
            processTunnel, _ = ftEstablishSession(sDestination, iPort)
        except (RemoteClientError, RemoteProtocolError) as error:
            click.echo(f"  still down: {error}", err=True)
            continue
        click.echo("Reconnected. The dashboard tab should recover.", err=True)
        iAttempt = 0
        fElapsed = 0.0


@click.command("remote")
@click.argument("sDestination")
@click.option(
    "--port", "iExplicitPort", default=None, type=int,
    help="Local and remote loopback port to use. Both ends use the "
         "same number so the dashboard's Host check keeps passing.",
)
def fnRemoteCommand(sDestination, iExplicitPort):
    """Open a vaibify dashboard for a remote machine over SSH."""
    from .main import _fnOpenBrowserUnlessSuppressed
    try:
        fnValidateSshDestination(sDestination)
        iPort = fiChooseLocalPort(iExplicitPort)
    except RemoteClientError as error:
        click.echo(f"Error: {error}", err=True)
        sys.exit(2)
    click.echo(f"Connecting to {sDestination} ...")
    try:
        processTunnel, dictRecord = ftEstablishSession(
            sDestination, iPort,
        )
    except (RemoteClientError, RemoteProtocolError) as error:
        click.echo(f"Error: {error}", err=True)
        sys.exit(1)
    _fnReportSession(dictRecord, iPort)
    _fnOpenBrowserUnlessSuppressed(
        fsLocalDashboardUrl(iPort, dictRecord["sBootstrapCapability"]),
    )
    try:
        iStatus = _fiHoldAndReconnect(processTunnel, sDestination, iPort)
    except KeyboardInterrupt:
        click.echo("\nClosing the tunnel. The remote hub keeps running.")
        iStatus = 0
    finally:
        _fnStopTunnel(processTunnel)
    sys.exit(iStatus)
