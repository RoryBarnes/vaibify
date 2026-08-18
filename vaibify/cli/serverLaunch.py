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
"""

# Seconds between server ping frames, and how long a peer has to
# answer one. Detection therefore takes between one and two intervals.
# These are set to the value uvicorn has historically defaulted to, so
# this states existing behaviour rather than changing it — the point is
# that it is now OURS to reason about, and appears in the reconnect
# arithmetic instead of being an unstated library default.
F_WEBSOCKET_PING_INTERVAL_SECONDS = 20.0
F_WEBSOCKET_PING_TIMEOUT_SECONDS = 20.0

__all__ = [
    "F_WEBSOCKET_PING_INTERVAL_SECONDS",
    "F_WEBSOCKET_PING_TIMEOUT_SECONDS",
    "fnRunServer",
]


def fnRunServer(app, iPort, sHost="127.0.0.1"):
    """Serve app on the loopback interface until the process stops.

    ``log_config=None`` keeps uvicorn from calling
    ``logging.config.dictConfig``, whose handler teardown CLOSES every
    handler already attached to the process — including the rotating
    vaibify.log handler the CLI attaches on the way in. File logging
    was silently dead in every CLI-launched hub until a stack trace on
    the closed handler caught it (2026-08-14). ``log_level`` still
    applies to uvicorn's own loggers.
    """
    import uvicorn
    uvicorn.run(
        app, host=sHost, port=iPort,
        log_level="warning", timeout_graceful_shutdown=3,
        log_config=None,
        ws_ping_interval=F_WEBSOCKET_PING_INTERVAL_SECONDS,
        ws_ping_timeout=F_WEBSOCKET_PING_TIMEOUT_SECONDS,
    )
