"""How vaibify runs uvicorn — one place, so every hub agrees.

Four call sites start a uvicorn server (the hub, the setup wizard, the
single-project viewer, and ``vaibify start --gui``). They had four
copies of the same keyword arguments, and the copies had already
diverged on the one that matters least and agreed by accident on the
one that matters most.

The socket-liveness settings are the reason this module exists. A
WebSocket peer that stops answering does not close its socket: a
laptop that sleeps, a Wi-Fi network that vanishes, or an SSH tunnel
whose far end is a still-open forwarder all leave a connection that
reads as established forever. Protocol-level ping is the ONLY thing
that notices, and it was left entirely to uvicorn's defaults —
unstated, unobserved, and free to change under us on any upgrade.

The consequence was not subtle. The hub's live-connection count stayed
stuck at one, so the reconnect window never started, so the returning
browser was refused as a duplicate tab by its own ghost and told to
close a tab that did not exist.

The bind addresses are the second thing this module owns. A vaibify
server binds the loopback interface for the browser, and on Linux ALSO
the Docker bridge gateway for the in-container agent — see
:mod:`vaibify.docker.bridgeGateway` for why the second address exists
and why it is Linux-only. uvicorn binds one address per server, so the
sockets are bound here and handed to it. Widening the bind is a
deliberate exposure decision: the gateway address is reachable from
every container on that daemon and from nothing beyond it, and a
request arriving there without a valid per-container agent token is
refused by the Host-header check exactly as a rebinding attack on
loopback would be.
"""

import logging
import platform
import socket

import click

from vaibify.docker.bridgeGateway import fsResolveDockerBridgeGateway

logger = logging.getLogger(__name__)

# Seconds between server ping frames, and how long a peer has to
# answer one. Detection therefore takes between one and two intervals.
# These are set to the value uvicorn has historically defaulted to, so
# this states existing behaviour rather than changing it — the point is
# that it is now OURS to reason about, and appears in the reconnect
# arithmetic instead of being an unstated library default.
F_WEBSOCKET_PING_INTERVAL_SECONDS = 20.0
F_WEBSOCKET_PING_TIMEOUT_SECONDS = 20.0

S_LOOPBACK_HOST = "127.0.0.1"

# How long the daemon gets to name its bridge gateway at launch. The
# SDK's own default is a minute, which a hung daemon would spend
# before the hub printed anything at all.
I_GATEWAY_LOOKUP_TIMEOUT_SECONDS = 5

__all__ = [
    "F_WEBSOCKET_PING_INTERVAL_SECONDS",
    "F_WEBSOCKET_PING_TIMEOUT_SECONDS",
    "I_GATEWAY_LOOKUP_TIMEOUT_SECONDS",
    "S_LOOPBACK_HOST",
    "flistBindServerSockets",
    "flistResolveBindAddresses",
    "fnRunServer",
]


def flistResolveBindAddresses(sLoopbackHost=S_LOOPBACK_HOST):
    """Return the addresses a vaibify server binds, loopback first.

    On Linux the Docker bridge gateway follows, when the daemon can name
    it. Anywhere else the answer is loopback alone: a macOS daemon runs
    in a VM that forwards ``host.docker.internal`` to the host's
    loopback, so the second address would be a hole with no traffic
    behind it.
    """
    listAddresses = [sLoopbackHost]
    if platform.system() != "Linux":
        return listAddresses
    sGateway = fsResolveDockerBridgeGateway(I_GATEWAY_LOOKUP_TIMEOUT_SECONDS)
    if sGateway and sGateway != sLoopbackHost:
        listAddresses.append(sGateway)
    return listAddresses


def flistBindServerSockets(listAddresses, iPort):
    """Bind one listening socket per address on ``iPort``.

    The first address is the loopback one and is mandatory: failing to
    bind it is the port-in-use error the port allocator exists to
    prevent, and it propagates. Any later address is the bridge
    gateway, and a gateway the kernel will not bind — the daemon
    stopped between naming it and now — degrades to loopback only, and
    says so, because a silent degrade is the defect this fixes.
    """
    listSockets = []
    for sAddress in listAddresses:
        socketListening = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        socketListening.setsockopt(
            socket.SOL_SOCKET, socket.SO_REUSEADDR, 1,
        )
        try:
            socketListening.bind((sAddress, iPort))
        except OSError as error:
            socketListening.close()
            if not listSockets:
                raise
            logger.warning(
                "Could not bind %s:%d (%s); in-container agents cannot "
                "reach this hub", sAddress, iPort, error,
            )
            click.echo(
                f"Warning: could not bind {sAddress}:{iPort} ({error}); "
                "in-container agents (vaibify-do) cannot reach this hub.",
                err=True,
            )
            continue
        socketListening.set_inheritable(True)
        listSockets.append(socketListening)
    return listSockets


def _fnAnnounceBridgeBinding(listSockets, iPort):
    """Say where the in-container agent can reach this server.

    Only Linux has a bridge address to announce or to miss. The telling
    is the load-bearing half: a Linux hub that bound loopback alone is
    one whose in-container agents will fail on every call, and the
    researcher should learn that here rather than from an agent that
    has quietly started working around it.
    """
    if platform.system() != "Linux":
        return
    listBridgeAddresses = [
        socketBound.getsockname()[0] for socketBound in listSockets[1:]
    ]
    if listBridgeAddresses:
        sBridgeUrl = f"http://{listBridgeAddresses[0]}:{iPort}"
        logger.info("In-container agents reach this server at %s", sBridgeUrl)
        click.echo(
            f"In-container agents (vaibify-do) reach this server at "
            f"{sBridgeUrl} (Docker bridge gateway)."
        )
        return
    logger.warning(
        "No Docker bridge gateway was bound; in-container agents "
        "cannot reach this server"
    )
    click.echo(
        "Warning: the Docker daemon did not name its bridge gateway, so "
        "this server listens on loopback only and in-container agents "
        "(vaibify-do) cannot reach it. Start the daemon, then restart "
        "vaibify.",
        err=True,
    )


def fnRunServer(app, iPort, sHost=S_LOOPBACK_HOST):
    """Serve app on loopback (and the Linux bridge gateway) until stopped.

    ``log_config=None`` keeps uvicorn from calling
    ``logging.config.dictConfig``, whose handler teardown CLOSES every
    handler already attached to the process — including the rotating
    vaibify.log handler the CLI attaches on the way in. File logging
    was silently dead in every CLI-launched hub until a stack trace on
    the closed handler caught it (2026-08-14). ``log_level`` still
    applies to uvicorn's own loggers.

    The sockets are bound here and passed in, because ``uvicorn.run``
    binds exactly one address. ``host`` and ``port`` on the config are
    then descriptive — uvicorn reads them for its own log line, never
    to bind — and are kept truthful for that reason.
    """
    import uvicorn
    listSockets = flistBindServerSockets(
        flistResolveBindAddresses(sHost), iPort,
    )
    _fnAnnounceBridgeBinding(listSockets, iPort)
    configUvicorn = uvicorn.Config(
        app, host=sHost, port=iPort,
        log_level="warning", timeout_graceful_shutdown=3,
        log_config=None,
        ws_ping_interval=F_WEBSOCKET_PING_INTERVAL_SECONDS,
        ws_ping_timeout=F_WEBSOCKET_PING_TIMEOUT_SECONDS,
    )
    uvicorn.Server(configUvicorn).run(sockets=listSockets)
