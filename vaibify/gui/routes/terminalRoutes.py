"""Terminal WebSocket route — withdrawn for the alpha.

The interactive terminal is the one lane whose containment could not be
proven: a shell can `setsid` out of the process group the containment
record tracks, so "the terminal stopped" was never provable, and an
authority-ending path (release, transfer, shutdown) could not honestly
say the container was quiet. Rather than ship an unprovable boundary to
alpha testers, the route is withdrawn: it accepts the handshake and
closes with :data:`I_REJECT_TERMINAL_DISABLED`.

The refusal is the FIRST statement in the handler, before any Docker
lookup, any ownership gate, and any counter. That ordering is the
contract, not an accident:

* it creates and refreshes NO ownership — an unauthenticated dial-in
  cannot extend a stranger's lease or reset its liveness stamp;
* it increments NO connection count, so the idle watchdog and the
  ownership reaper still see the truth;
* it creates NO ``TerminalSession`` and NO journal record, so no
  container acquires a new quarantine-bearing operation; and
* it reveals NOTHING about whether the named container exists — the
  Docker lookup that used to run first was an existence oracle open to
  any caller that could reach the socket.

The in-container agent never opens this lane (``vaibifyDo.py`` opens
``/ws/pipeline/`` only), so withdrawing it does not impair the agent.
"""

__all__ = ["fnRegisterAll"]

from fastapi import WebSocket

from ..webSocketAuthorization import (
    I_REJECT_TERMINAL_DISABLED,
    fnCloseWithCode,
)


def _fnRegisterTerminalWs(app, dictCtx):
    """Register the terminal WebSocket endpoint as an unconditional refusal."""

    @app.websocket("/ws/terminal/{sContainerId}")
    async def fnTerminalWs(
        websocket: WebSocket, sContainerId: str
    ):
        await fnCloseWithCode(websocket, I_REJECT_TERMINAL_DISABLED)


def fnRegisterAll(app, dictCtx):
    """Register all terminal routes."""
    _fnRegisterTerminalWs(app, dictCtx)
