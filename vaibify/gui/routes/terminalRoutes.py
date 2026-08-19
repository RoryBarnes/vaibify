"""Terminal WebSocket route handler.

The terminal was withdrawn on 2026-08-02 because its containment could
not be PROVEN: a shell can ``setsid`` out of the process group the
containment record tracks, so "the terminal stopped" was never
provable, and release, hand-over and shutdown all claimed the container
was quiet. It is back on 2026-08-11 by a different resolution — not by
proving containment, which nobody has done, but by **no longer making
the claim that the terminal could falsify**.

That distinction is the whole design. Nothing here proves a detached
descendant is gone. What changed is that a container in which a
terminal ran now reports *quiescence unproven* and routes the
researcher to ``vaibify reconcile``, instead of reporting quiet. The
machinery for that was never removed — ``terminalContainment`` survived
the withdrawal precisely so records written by an earlier version could
still be reconciled, and ``fdictTerminateAndProveRecord`` already
terminates-and-proves and quarantines what it cannot prove. So the
honest downgrade is not new code; it is the behaviour that was already
there, now reached by a live feature rather than only by legacy
records.

The visible cost, which was accepted deliberately: using a terminal in
a container means that container cannot afterwards be certified quiet,
and a release may leave it quarantined until reconcile settles it. The
reasoning is that a tool which treats the command line as ground truth
must not delete the command line to keep a status line tidy.

HOST projects are served here too (2026-08-15 ruling): the same gate,
the same seam, a real PTY on the researcher's own machine journaled
with the ``terminal`` kind before the shell's first instruction — and
the same honest cost, quiescence unproven until reconcile. What a host
session adds is the banner: the first bytes the researcher sees say
this shell runs on THEIR machine and that processes can outlive the
tab, because the host-mode modal was acknowledged once at claim time
and this reminder recurs per session.
"""

__all__ = ["fnRegisterAll"]

import asyncio

from fastapi import WebSocket

from .. import pipelineServer as _pipelineServer
from ..attributionLog import (
    S_TERMINAL_CHANNEL,
    S_TERMINAL_CLOSED_DETAIL,
    S_TERMINAL_OPENED_DETAIL,
)
from ..pipelineServer import (
    fnRejectTerminalStart,
    fnRunTerminalSession,
    fsContainerNameForId,
)
from ..webSocketAuthorization import (
    ffnBuildPerFrameCredentialCheck,
    fiContainerSessionRejectionCode,
    fnCloseWithCode,
    fnServeUnderLiveConnectionCounters,
)
from ..terminalSession import HostTerminalSession, TerminalSession

# The per-session reminder Rory ruled on (2026-08-15): the host-mode
# modal is the standing consent, and this banner recurs at every shell
# start so the researcher never mistakes the pane for a contained one.
# Sent as the session's first output bytes, before any shell output.
S_HOST_TERMINAL_BANNER = (
    "\r\n"
    "\x1b[33m[vaibify]\x1b[0m This shell runs on YOUR OWN machine, in "
    "the project directory.\r\n"
    "\x1b[33m[vaibify]\x1b[0m Processes you start here can keep running "
    "after you close this session;\r\n"
    "\x1b[33m[vaibify]\x1b[0m vaibify will report this project's "
    "quiescence as unproven until reconciled.\r\n"
    "\r\n"
)


# Through a tunnel the host banner's first sentence becomes false, and
# it is the sentence that matters: a researcher who believes a shell is
# local reasons about its blast radius as local. The remote variant
# NAMES the machine instead, because "your own machine" and
# "compute-server-3" call for different care with an rm.
#
# The quiescence sentence is unchanged and must stay: a terminal is a
# terminal wherever it runs, and vaibify still cannot prove a shell's
# descendants died.
def fsRemoteTerminalBanner(sExecutionHostname):
    """Return the banner for a shell reached over a tunnel."""
    sWhere = sExecutionHostname or "the remote machine"
    return (
        "\r\n"
        "\x1b[33m[vaibify]\x1b[0m This shell runs on \x1b[1m" + sWhere
        + "\x1b[0m, not on the computer you are sitting at.\r\n"
        "\x1b[33m[vaibify]\x1b[0m Processes you start here can keep "
        "running after you close this session;\r\n"
        "\x1b[33m[vaibify]\x1b[0m vaibify will report this project's "
        "quiescence as unproven until reconciled.\r\n"
        "\r\n"
    )


def _fnRegisterTerminalWs(app, dictCtx):
    """Register terminal WebSocket endpoint."""

    @app.websocket("/ws/terminal/{sContainerId}")
    async def fnTerminalWs(
        websocket: WebSocket, sContainerId: str
    ):
        sName = fsContainerNameForId(
            dictCtx.get("docker"), sContainerId,
        )
        iRejectCode = fiContainerSessionRejectionCode(
            websocket, dictCtx, sName,
        )
        if iRejectCode:
            await fnCloseWithCode(websocket, iRejectCode)
            return
        # AFTER the ownership gate, deliberately. A caller with no
        # standing must not be able to learn which resources are host
        # projects; the mode is answered only to a session that already
        # owns the thing it is asking about. The host branch skips the
        # daemon requirement — a host-only machine has no daemon, and
        # asking first would answer "install Docker" about a project
        # that never wanted one.
        from vaibify.config.registryManager import fbIsHostProject
        if fbIsHostProject(sName):
            await _fnTrackAndServeTerminal(
                app, websocket, dictCtx, sContainerId, sName,
                bHostProject=True,
            )
            return
        dictCtx["require"](sContainerId)
        await _fnTrackAndServeTerminal(
            app, websocket, dictCtx, sContainerId, sName,
        )


async def _fnTrackAndServeTerminal(
    app, websocket, dictCtx, sContainerId, sName, bHostProject=False,
):
    """Accept and serve a terminal session under the live-connection counters.

    Delegates to the shared counter wrapper so the app-global live
    WebSocket count is driven identically to the pipeline route; the
    idle-shutdown watchdog can never retire a hub while a terminal tab
    is attached, even briefly mid-handshake.

    The terminal is NOT budgeted by the per-container one-session
    count: one session legitimately holds several sockets at once, and
    budgeting all of them is what shipped the Run-Step-always-refused
    bug — the terminal, opened on workflow entry, held the only slot.
    """

    async def fnServe():
        await websocket.accept()
        await _fnStartAndRunTerminal(
            app, websocket, dictCtx, sContainerId, sName,
            bHostProject=bHostProject,
        )

    await fnServeUnderLiveConnectionCounters(
        websocket, dictCtx.get("dictContainerOwners", {}), sName,
        fnServe, lambda: _pipelineServer.fnIncrementWebSocketCount(app),
        lambda: _pipelineServer.fnDecrementWebSocketCount(app),
        dictSessionSockets=dictCtx.get("dictSessionSockets"),
        dictBrowserSessions=dictCtx.get("dictBrowserSessions"),
    )


async def _fnStartAndRunTerminal(
    app, websocket, dictCtx, sContainerId, sName, bHostProject=False,
):
    """Start the terminal session and run it to completion.

    The session start is the journaled create → journal → start split,
    so the exec becomes a durable ``TerminalExecutionRecord`` that
    release, reaping and shutdown terminate-and-prove — and quarantine
    when they cannot. That record is what makes the weaker claim
    honest: without it a detached descendant would be invisible instead
    of merely unproven. The start runs in a worker thread — group
    discovery polls the container — so the hub event loop is never
    blocked behind a slow probe.

    The host leg builds the PTY twin instead of a Docker exec, and its
    first output is the per-session banner: the researcher is on THEIR
    OWN machine and processes can outlive the tab.
    """
    recordOwner = (dictCtx.get("dictContainerOwners") or {}).get(sName)
    dictContainment = {
        "appState": app.state,
        "sContainerName": sName,
        "iOwnerGeneration": (
            recordOwner.iOwnerGeneration if recordOwner is not None
            else 0
        ),
    }
    if bHostProject:
        session = HostTerminalSession(
            dictCtx["docker"], sName, dictContainment,
        )
    else:
        session = TerminalSession(
            dictCtx["docker"], sContainerId,
            sUser=dictCtx["containerUsers"].get(
                sContainerId, dictCtx.get("sTerminalUser")
            ),
            dictContainment=dictContainment,
        )
    try:
        await asyncio.to_thread(session.fnStart)
    except Exception as error:
        await fnRejectTerminalStart(websocket, error)
        return
    dictInteractive = (
        _pipelineServer.fdictInteractiveContextForContainer(sContainerId)
    )
    _fnRecordTerminalAttribution(
        dictCtx, sContainerId, S_TERMINAL_OPENED_DETAIL,
    )
    try:
        await fnRunTerminalSession(
            session, websocket, dictCtx["terminals"],
            dictInteractive=dictInteractive,
            fbFrameCredentialStillActive=(
                ffnBuildPerFrameCredentialCheck(
                    websocket, dictCtx.get("dictBrowserSessions"),
                )
            ),
            sIntroductionBanner=_fsBannerForThisShell(
                dictCtx, websocket, bHostProject,
            ),
        )
    finally:
        _fnRecordTerminalAttribution(
            dictCtx, sContainerId, S_TERMINAL_CLOSED_DETAIL,
        )


def _fsBannerForThisShell(dictCtx, websocket, bHostProject):
    """Return the banner this shell should open with.

    A remote shell is always banner-worthy, host mode or not: that it
    runs somewhere else is the surprising part, and a container on
    another machine is no more "here" than a host directory is.
    """
    from ..webSocketAuthorization import fsBrowserSessionIdForCredential
    sBrowserSessionId = fsBrowserSessionIdForCredential(
        websocket, dictCtx.get("dictBrowserSessions"),
    )
    if _pipelineServer.fbConnectionIsRemote(dictCtx, sBrowserSessionId):
        return fsRemoteTerminalBanner(
            _pipelineServer.fsExecutionHostname(),
        )
    return S_HOST_TERMINAL_BANNER if bHostProject else ""


def _fnRecordTerminalAttribution(dictCtx, sContainerId, sDetail):
    """Record a terminal open/close as a Supervised-mode event.

    The terminal is a recorded CHANNEL, not (yet) recorded content —
    a change made while a terminal session is open attributes to the
    session, but its keystrokes are not captured. The Supervised
    docs state this granularity.

    The channel and the two detail strings come from
    :mod:`vaibify.gui.attributionLog`, which pairs them into the open
    interval that makes a long session attributive. A literal here
    that drifted from the judge's spelling would silently reduce the
    interval back to two unpaired instants.
    """
    from ..routeContext import fnRecordAttributionEvent
    dictWorkflow = (
        dictCtx.get("workflows") or {}
    ).get(sContainerId) or {}
    fnRecordAttributionEvent(
        dictCtx, sContainerId, dictWorkflow, S_TERMINAL_CHANNEL,
        sDetail,
    )


def fnRegisterAll(app, dictCtx):
    """Register all terminal routes."""
    _fnRegisterTerminalWs(app, dictCtx)
