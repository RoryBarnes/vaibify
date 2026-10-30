"""The hub's X11 authorization lifecycle: reconcile at start, probe on a cadence, settle at stop.

A container's X authorization outlives the hub that made it, and the X
server can lose it (a server restart) or keep it after its container is
gone (a hub that died). So the hub settles every recorded authorization
when it starts, again every minute while it runs, and once more as it
stops: a record whose container the daemon says is gone is released
(probe, then revoke), a live container's authorization is renewed when
the server lost it. The first start also withdraws the ``xhost`` grants
earlier versions made, which admitted every local client.

Nothing runs while the X server is not running. On a Mac the helper's
connection would START the server through its launchd socket, and a
hub that relaunched the X server every minute after the researcher quit
it would be worse than a stale record.
"""

__all__ = [
    "F_X11_PROBE_INTERVAL_SECONDS",
    "fbXServerAvailable",
    "fdictReconcileAuthorizations",
    "fnRegisterX11Lifecycle",
]

import asyncio
import logging
import os
import platform

from vaibify.docker import containerManager, x11Authorizations, x11Forwarding

from . import serverLifespan

logger = logging.getLogger(__name__)

F_X11_PROBE_INTERVAL_SECONDS = 60.0


def fbXServerAvailable():
    """True when probing the display cannot start a server as a side effect."""
    if not os.environ.get("DISPLAY"):
        return False
    if platform.system() == "Darwin":
        return x11Forwarding.fbMacXServerIsRunning()
    return True


def _fbContainerStillExists(sContainerName):
    """True unless the daemon ANSWERED that no such container exists."""
    dictPresence = containerManager.fdictProbeContainerPresence(sContainerName)
    return not (dictPresence["bAnswered"] and not dictPresence["bPresent"])


def fdictReconcileAuthorizations():
    """Settle every recorded authorization; ``{}`` when the server is not running."""
    if not x11Authorizations.fdictReadAuthorizations():
        return {}
    if not fbXServerAvailable():
        return {}
    return x11Authorizations.fdictReconcile(_fbContainerStillExists)


def _fnRevokeLegacyGrants():
    """Withdraw earlier versions' xhost grants, only beside a running server."""
    if fbXServerAvailable():
        x11Forwarding.fnRevokeLegacyXhostGrants()


async def _fnProbeLoop(fInterval):
    while True:
        await asyncio.sleep(fInterval)
        try:
            await asyncio.to_thread(fdictReconcileAuthorizations)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 -- one failed probe never ends the loop
            logger.warning("X11 authorization probe failed", exc_info=True)


def fnRegisterX11Lifecycle(app, fInterval=None):
    """Register the start reconcile, the probe loop and the stop reconcile."""
    fIntervalEffective = (
        fInterval if fInterval is not None else F_X11_PROBE_INTERVAL_SECONDS
    )

    async def fnStartX11Lifecycle(app):
        await asyncio.to_thread(_fnRevokeLegacyGrants)
        await asyncio.to_thread(fdictReconcileAuthorizations)
        app.state.taskX11Probe = asyncio.create_task(
            _fnProbeLoop(fIntervalEffective), name="vaibify-x11-probe",
        )

    async def fnStopX11Lifecycle(app):
        await serverLifespan.fnCancelBackgroundTask(app, "taskX11Probe")
        await asyncio.to_thread(fdictReconcileAuthorizations)

    serverLifespan.fnRegisterLifespanTask(
        app, fnStartX11Lifecycle, fnStopX11Lifecycle,
    )
