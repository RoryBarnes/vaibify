"""The vaibify that runs on the far end of an SSH connection.

This is not a second control plane. It starts or adopts a hub beside
the researcher's projects, asks that hub for one sign-in capability,
says so in a single line, and then does nothing at all until its
channel closes. Everything it can do, ``vaibify open`` could already
do locally; the only new thing is that it says its answer in a form a
program on another machine can check.

Three properties are load-bearing.

**Stdout is protocol, stderr is prose.** Exactly one record line
reaches stdout. A helper that logs to stdout corrupts the record its
client is parsing, which is why every diagnostic here goes to stderr
deliberately.

**A hub it cannot identify is not reused.** A live process holding a
hub slot on the expected port does not prove compatibility: a hub of
any other version satisfies that equally, and driving one with a
protocol it does not share is how "it connected" becomes an incident.
The version now lives in the slot, so this refuses instead of guessing.

**The hub it starts is meant to outlive it.** That is the whole point
of the feature -- a laptop closing must not kill a running pipeline --
so the child is detached, and this process's death says nothing about
the hub's. Note what that rules out: the gated create-suspended
journal pattern used for host execution cannot be used here. Its
records key by PROJECT and settle only when the holder is dead with an
empty process group, so a long-lived hub would leave a record that
never settles, which would make that project permanently unclaimable
and permanently veto both the ownership reaper and idle shutdown.
"""

import os
import socket
import sys
import time

import click

from .remoteProtocol import fsFormatStartupRecord

# How long to wait for a hub this helper started to answer on both its
# TCP port and its control socket. A cold start imports the world.
F_READINESS_TIMEOUT_SECONDS = 60.0
F_READINESS_POLL_SECONDS = 0.25

# A remote hub deliberately outlives its tunnel, so it must not retire
# on the ordinary idle clock while the researcher is asleep. Expressed
# here rather than in the hub because it is a property of being driven
# remotely, not of being a hub.
F_REMOTE_HUB_IDLE_TIMEOUT_SECONDS = 43200.0

__all__ = ["fnRemoteHelperCommand"]


def _fnSay(sMessage):
    """Write a diagnostic to stderr, never to the protocol stream."""
    click.echo(f"[vaibify-remote] {sMessage}", err=True)


def _fnFailAndExit(sMessage):
    """Explain on stderr and exit nonzero, saying nothing on stdout."""
    _fnSay(f"error: {sMessage}")
    sys.exit(1)


def fbPortAcceptsConnections(iPort):
    """Return True when something is listening on loopback iPort."""
    connectionProbe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    connectionProbe.settimeout(0.5)
    try:
        return connectionProbe.connect_ex(("127.0.0.1", int(iPort))) == 0
    finally:
        connectionProbe.close()


def fdictFindCompatibleHub(iPort):
    """Return the live hub slot on iPort if we may reuse it, else {}.

    Returns ``{}`` for "no hub of ours is there", and raises for "a hub
    is there and we must not touch it" -- two different answers that a
    single falsy return would have collapsed into "start another one",
    which is precisely the mistake that produces two hubs fighting
    over one port.
    """
    from vaibify.config.sessionRegistry import (
        fdictReadHubSlotByPort, fsRunningVaibifyVersion,
    )
    dictSlot = fdictReadHubSlotByPort(iPort)
    if not dictSlot:
        return {}
    sTheirs = str(dictSlot.get("sVaibifyVersion", "unknown"))
    sOurs = fsRunningVaibifyVersion()
    if sTheirs != sOurs:
        raise RuntimeError(
            f"a vaibify hub is already running on port {iPort}, but it "
            f"is version {sTheirs} and this is {sOurs}. Refusing to "
            "drive a hub whose protocol this version may not share; "
            "stop it, or choose another port."
        )
    return dictSlot


def _fnRefuseForeignListener(iPort):
    """Refuse a listener on iPort that is not a vaibify hub of ours."""
    if fbPortAcceptsConnections(iPort):
        raise RuntimeError(
            f"something is already listening on port {iPort} of the "
            "remote machine and it is not a vaibify hub. Forwarding to "
            "it would hand the browser an unknown service; choose "
            "another port."
        )


def _fprocessStartDetachedHub(iPort):
    """Start a hub that will outlive this helper, and return it.

    Modelled on the dashboard's own detached-hub spawn: a new session,
    stdio to devnull, and no expectation that this process supervises
    it afterwards. The browser suppression matters -- there is very
    often no display on a compute machine, and a hub that tried to
    open one would hang or spray errors into its own startup.
    """
    import subprocess
    from vaibify.gui.routes.sessionRoutes import S_SUPPRESS_BROWSER_ENV
    dictChildEnvironment = {
        **os.environ,
        S_SUPPRESS_BROWSER_ENV: "1",
        "VAIBIFY_HUB_IDLE_TIMEOUT_SECONDS": str(
            F_REMOTE_HUB_IDLE_TIMEOUT_SECONDS,
        ),
    }
    return subprocess.Popen(
        [sys.executable, "-m", "vaibify", "--no-browser",
         "--port", str(int(iPort))],
        start_new_session=True,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        env=dictChildEnvironment,
    )


def _fnAwaitHubReadiness(iPort, processHub):
    """Block until the hub answers on TCP and its control socket.

    Both, not either. The TCP listener appears before the application
    is assembled, and the capability this helper exists to fetch comes
    from the control socket -- so a helper that waited only for the
    port would race the very thing it is about to ask for.
    """
    from vaibify.gui.hostControlChannel import (
        fsControlSocketPathForPort,
    )
    fDeadline = time.monotonic() + F_READINESS_TIMEOUT_SECONDS
    while time.monotonic() < fDeadline:
        if processHub is not None and processHub.poll() is not None:
            raise RuntimeError(
                "the remote hub exited during startup (status "
                f"{processHub.returncode}). Run 'vaibify' on that "
                "machine to see why."
            )
        if fbPortAcceptsConnections(iPort) and os.path.exists(
            fsControlSocketPathForPort(iPort),
        ):
            return
        time.sleep(F_READINESS_POLL_SECONDS)
    raise RuntimeError(
        f"the remote hub did not become ready within "
        f"{F_READINESS_TIMEOUT_SECONDS:.0f}s"
    )


def fsMintOneCapability(iPort):
    """Return one sign-in capability from the hub on iPort."""
    from .hubSession import HubSessionError, fsRequestBootstrapCapability
    try:
        # Declared here and nowhere else: this is the one process
        # that knows the browser is on another machine.
        return fsRequestBootstrapCapability(iPort, bRemoteSession=True)
    except HubSessionError as error:
        raise RuntimeError(str(error))


def fsDescribeExecutionMode():
    """Return the execution mode this remote machine can offer.

    Reported honestly rather than aspirationally: a machine with no
    reachable Docker daemon says ``host``, because that is what a
    researcher will actually get there.
    """
    try:
        from vaibify.docker import fbDockerDaemonReachable
        return "docker" if fbDockerDaemonReachable() else "host"
    except Exception:
        return "host"


def _fnEmitStartupRecord(iPort, sCapability):
    """Write the single protocol line and flush it."""
    sRecord = fsFormatStartupRecord(
        iPort=iPort,
        sBootstrapCapability=sCapability,
        sExecutionMode=fsDescribeExecutionMode(),
        sHostname=socket.gethostname(),
    )
    sys.stdout.write(sRecord + "\n")
    sys.stdout.flush()


def _fnHoldChannelOpen():
    """Block until stdin closes, then return.

    The SSH connection is what forwards the port, and it lives exactly
    as long as this command does. Reading stdin to EOF is how a remote
    command notices that its client hung up, including when the client
    was killed rather than asked to stop politely.
    """
    try:
        while sys.stdin.readline():
            pass
    except (KeyboardInterrupt, OSError):
        pass


@click.command("remote-helper")
@click.option(
    "--port", "iPort", required=True, type=int,
    help="The loopback port the client has forwarded to this machine.",
)
def fnRemoteHelperCommand(iPort):
    """Serve one remote vaibify session. Not for interactive use."""
    if not 1 <= iPort <= 65535:
        _fnFailAndExit(f"port {iPort} is not a usable TCP port")
    try:
        dictExisting = fdictFindCompatibleHub(iPort)
        processHub = None
        if dictExisting:
            _fnSay(f"reusing the vaibify hub already on port {iPort}")
        else:
            _fnRefuseForeignListener(iPort)
            _fnSay(f"starting a vaibify hub on port {iPort}")
            processHub = _fprocessStartDetachedHub(iPort)
        _fnAwaitHubReadiness(iPort, processHub)
        sCapability = fsMintOneCapability(iPort)
    except RuntimeError as error:
        _fnFailAndExit(str(error))
        return
    _fnEmitStartupRecord(iPort, sCapability)
    _fnSay("ready; holding the tunnel open until the client hangs up")
    _fnHoldChannelOpen()
    _fnSay("client hung up; the hub keeps running")
