"""Port selection helpers for vaibify CLI entry points.

Allows multiple concurrent vaibify instances on one host: if the
preferred port is already bound, scan upward for the next free one
so ``vaibify`` with no flags just works. Explicit ``--port`` values
are honoured verbatim so the user's intent is not overridden.

``fiResolveProjectPort`` extends that with stable per-project
assignment: once a project has been started for the first time, its
chosen port is persisted to ``vaibify.yml`` so subsequent restarts
always bind the same port and the browser tab survives the cycle.

``fiResolveHubPort`` does the same job for the project-agnostic hub.
Since the hub has no ``vaibify.yml`` to write to, the persistence
target is ``~/.vaibify/hub-port.json`` (see ``hubPortRegistry``).
Same survival guarantee: the dashboard tab opened from a prior hub
run keeps working across a Ctrl-C/restart cycle.
"""

import socket
import sys
import time


_I_DEFAULT_PREFERRED_PORT = 8050
_I_DEFAULT_MAX_ATTEMPTS = 20
_F_SELF_ZOMBIE_WAIT_SECONDS = 3.0
_F_SELF_ZOMBIE_POLL_INTERVAL = 0.1


def _fsBuildHolderDetail(iPort, dictHolder):
    """Build the holder-identifying clause for PortInUseError messages."""
    iHolderPid = dictHolder.get("iPid", 0)
    sHolderProject = dictHolder.get("sProjectName", "")
    if iHolderPid and sHolderProject:
        return (
            f"port {iPort} is held by vaibify project "
            f"'{sHolderProject}' (pid {iHolderPid})"
        )
    if iHolderPid:
        return (
            f"port {iPort} is held by another process "
            f"(pid {iHolderPid})"
        )
    return f"port {iPort} is in use by another process"


class PortInUseError(RuntimeError):
    """Raised when the requested port is held by an unrelated process."""

    def __init__(self, iPort, sProjectName, dictHolder):
        self.iPort = iPort
        self.sProjectName = sProjectName
        self.dictHolder = dictHolder or {}
        sDetail = _fsBuildHolderDetail(iPort, self.dictHolder)
        sMessage = (
            f"Cannot start project '{sProjectName}': {sDetail}. "
            f"Stop the holder or pass --port to override; the "
            f"persisted port lives in vaibify.yml under "
            f"`dashboardPort`."
        )
        super().__init__(sMessage)


def fbIsPortFree(iPort):
    """Return True if a TCP bind on 127.0.0.1:iPort would succeed.

    Sets ``SO_REUSEADDR`` before binding so a socket lingering in
    ``TIME_WAIT`` (which can hold a recently-closed listener for tens
    of seconds on macOS) is reported as free. Uvicorn also binds with
    ``SO_REUSEADDR`` on POSIX, so this preflight matches its actual
    bind behaviour — without it we lose the per-project-stable-port
    guarantee for the whole TIME_WAIT window after every restart.
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        sock.bind(("127.0.0.1", iPort))
    except OSError:
        return False
    finally:
        sock.close()
    return True


def fiPickFreePort(
    iPreferred=_I_DEFAULT_PREFERRED_PORT,
    iMaxAttempts=_I_DEFAULT_MAX_ATTEMPTS,
):
    """Return iPreferred if free, else the next free port in range."""
    for iOffset in range(iMaxAttempts):
        iCandidate = iPreferred + iOffset
        if fbIsPortFree(iCandidate):
            return iCandidate
    raise RuntimeError(
        f"No free TCP port found in "
        f"{iPreferred}..{iPreferred + iMaxAttempts - 1}."
    )


def fiResolvePort(iExplicitPort, iPreferred=_I_DEFAULT_PREFERRED_PORT):
    """Return the port to bind, auto-picking when none was supplied.

    When ``iExplicitPort`` is None, scan for a free port starting at
    ``iPreferred`` and announce the fallback on stderr. When the user
    passed an explicit port, return it unchanged so uvicorn surfaces
    the bind error naturally if it is taken.
    """
    if iExplicitPort is not None:
        return iExplicitPort
    iPort = fiPickFreePort(iPreferred=iPreferred)
    if iPort != iPreferred:
        print(
            f"Port {iPreferred} in use; starting on {iPort}.",
            file=sys.stderr,
        )
    return iPort


def fiResolveProjectPort(
    config, iExplicitPort, sConfigPath,
    fdictSaveConfig=None,
):
    """Return the dashboard port for a project, persisting on first use.

    The contract that keeps the browser tab valid across restarts:

    1. ``--port`` (``iExplicitPort``) wins unconditionally so the user
       can always override the persisted assignment.
    2. If the project already has ``config.iDashboardPort`` set, that
       exact port is the only acceptable answer. A bind conflict means
       either our own dying process (wait briefly for release) or a
       foreign process (raise ``PortInUseError`` with a message that
       names the holder).
    3. On the very first launch the port is unassigned (``0``); pick a
       free one via the existing scan and write the result back to
       ``vaibify.yml`` so step 2 applies on every subsequent run.

    ``fdictSaveConfig`` is injected so this helper has no hard dependency
    on the YAML writer (kept testable). Callers pass
    ``projectConfig.fnSaveToFile`` in normal use.
    """
    if iExplicitPort is not None:
        return iExplicitPort
    if config.iDashboardPort > 0:
        return _fiAcquirePersistedPort(config)
    return _fiAssignAndPersistPort(
        config, sConfigPath, fdictSaveConfig,
    )


def _fiAcquirePersistedPort(config):
    """Bind the project's persisted port, waiting for self-zombie."""
    iPort = config.iDashboardPort
    if fbIsPortFree(iPort):
        return iPort
    if _fbWaitForSelfZombieRelease(config.sProjectName, iPort):
        return iPort
    dictHolder = _fdictReadContainerLockHolder(config.sProjectName)
    raise PortInUseError(iPort, config.sProjectName, dictHolder)


def _fiAssignAndPersistPort(config, sConfigPath, fdictSaveConfig):
    """Pick a free port, persist it to vaibify.yml, return it."""
    iPort = fiPickFreePort()
    config.iDashboardPort = iPort
    if fdictSaveConfig and sConfigPath:
        _fnPersistDashboardPort(config, sConfigPath, fdictSaveConfig)
    return iPort


def _fnPersistDashboardPort(config, sConfigPath, fdictSaveConfig):
    """Write the auto-assigned port back to disk; warn on failure."""
    iPort = config.iDashboardPort
    try:
        fdictSaveConfig(config, sConfigPath)
    except OSError as errorWrite:
        print(
            f"Warning: could not persist dashboardPort={iPort} "
            f"to {sConfigPath}: {errorWrite}",
            file=sys.stderr,
        )
        return
    print(
        f"Assigned dashboard port {iPort} to project "
        f"'{config.sProjectName}' (persisted to {sConfigPath}).",
        file=sys.stderr,
    )


def _fbWaitForSelfZombieRelease(sProjectName, iPort):
    """Poll for the port to free up while our own zombie shuts down.

    Returns True if the port became free within the budget. We treat a
    bind conflict as "our own zombie" only when the project's
    container lock confirms the holder is *this* project — anything
    else is a foreign process and must fail loudly.
    """
    dictHolder = _fdictReadContainerLockHolder(sProjectName)
    if dictHolder.get("sProjectName") != sProjectName:
        return False
    fDeadline = time.monotonic() + _F_SELF_ZOMBIE_WAIT_SECONDS
    while time.monotonic() < fDeadline:
        if fbIsPortFree(iPort):
            return True
        time.sleep(_F_SELF_ZOMBIE_POLL_INTERVAL)
    return fbIsPortFree(iPort)


def _fdictReadContainerLockHolder(sProjectName):
    """Return the lock-holder dict for sProjectName, or {} on any error."""
    try:
        from vaibify.config.containerLock import fdictReadLockHolder
    except ImportError:
        return {}
    try:
        return fdictReadLockHolder(sProjectName) or {}
    except Exception:
        return {}


def fiResolveHubPort(iExplicitPort):
    """Return the hub's bind port, persisting the choice across restarts.

    Mirrors ``fiResolveProjectPort`` but reads/writes
    ``~/.vaibify/hub-port.json`` instead of a project's ``vaibify.yml``
    (the hub is project-agnostic). The same survival contract: once a
    hub has launched on port N, future bare ``vaibify`` invocations
    bind N so any open dashboard tab survives Ctrl-C/restart cycles.

    Behaviour:

    1. ``--port`` (``iExplicitPort``) wins unconditionally.
    2. If a port is persisted and free → bind it.
    3. If a port is persisted but held by a *live foreign listener*
       (something accepts TCP connections there and no vaibify hub
       slot claims the port) → scan upward, persist the new port,
       and warn that the old URL is dead.
    4. Any other conflict — a dying hub still draining, or a socket
       lingering after the previous hub exited — gets a brief wait
       for the port to clear. If it clears, the persisted port is
       kept. If not, the hub binds a scanned port for THIS session
       only and the persisted port is left alone, so the researcher's
       bookmarked URL resolves again on the next restart.
    """
    if iExplicitPort is not None:
        return iExplicitPort
    iPersisted = _fiReadPersistedHubPort()
    if iPersisted <= 0:
        return _fiAssignFirstHubPort()
    if fbIsPortFree(iPersisted):
        return iPersisted
    return _fiResolveContestedHubPort(iPersisted)


def _fiAssignFirstHubPort():
    """First launch: pick a free port and persist it for restarts."""
    iPort = fiPickFreePort()
    _fnPersistHubPortSafely(iPort)
    print(
        f"Assigned hub port {iPort} (persisted for future "
        f"restarts).",
        file=sys.stderr,
    )
    return iPort


def _fiResolveContestedHubPort(iPersisted):
    """Resolve a persisted-but-busy hub port: wait, keep, or hop.

    The holder is classified ONCE, and both decisions — whether the
    brief wait is worth taking, and whether a hopped port may
    overwrite the persisted one — read that single answer, so they
    can never disagree about who held the port.
    """
    bLiveListener = _fbPortHasLiveListener(iPersisted)
    bLiveForeignHolder = bLiveListener and not _fdictReadHubSlot(iPersisted)
    if not bLiveForeignHolder and _fbWaitForHubPortRelease(iPersisted):
        return iPersisted
    return _fiHopFromContestedPort(iPersisted, bLiveListener)


def _fiHopFromContestedPort(iPersisted, bHolderIsLiveListener):
    """Scan a fresh port; persist it only when the old one is truly lost.

    A hopped port is persisted only when the persisted port is
    confirmed held by a live listener — a process that ACCEPTS
    connections will not release the port, so the bookmark it broke
    is gone for good and the file should follow reality. An
    unprovable holder (nothing accepts; the socket is lingering from
    a dying predecessor) keeps the persisted port on disk so the
    next restart returns to the researcher's bookmarked URL.
    """
    iPort = fiPickFreePort()
    if iPort == iPersisted:
        return iPort
    if bHolderIsLiveListener:
        _fnPersistHubPortSafely(iPort)
        print(
            f"Hub port {iPersisted} is held by another process; "
            f"binding {iPort} instead. Existing dashboard tabs at "
            f"the old URL will need to be reopened.",
            file=sys.stderr,
        )
        return iPort
    print(
        f"Hub port {iPersisted} has not cleared yet (a socket "
        f"from a previous run is lingering); binding {iPort} for "
        f"this session only. The next restart will return to port "
        f"{iPersisted}.",
        file=sys.stderr,
    )
    return iPort


def _fbPortHasLiveListener(iPort):
    """Return True when something ACCEPTS TCP connections on iPort now.

    A socket lingering from a dead or dying process refuses the bind
    but cannot accept; only a live server answers a connect. This is
    the discriminator between "the persisted port is genuinely owned
    by a live foreign process" (hop and persist the new port) and
    "the previous hub's socket has not cleared" (hop for this session
    but keep the persisted port).
    """
    socketProbe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    socketProbe.settimeout(0.25)
    try:
        socketProbe.connect(("127.0.0.1", iPort))
    except OSError:
        return False
    finally:
        socketProbe.close()
    return True


def _fbWaitForHubPortRelease(iPort):
    """Poll briefly for the persisted hub port to become bindable.

    Deliberately NOT gated on finding a live hub session slot: the
    dying hub releases its slot (``fnLaunchHub``'s finally) before
    its sockets fully clear, so exactly the restart this wait exists
    for — reproduced live 2026-08-14 as the 8051→8050 hop — found no
    slot and got no wait.
    """
    fDeadline = time.monotonic() + _F_SELF_ZOMBIE_WAIT_SECONDS
    while time.monotonic() < fDeadline:
        if fbIsPortFree(iPort):
            return True
        time.sleep(_F_SELF_ZOMBIE_POLL_INTERVAL)
    return fbIsPortFree(iPort)


def _fiReadPersistedHubPort():
    """Read the persisted hub port, returning 0 on any error."""
    try:
        from vaibify.config.hubPortRegistry import (
            fiReadPersistedHubPort,
        )
    except ImportError:
        return 0
    try:
        return fiReadPersistedHubPort()
    except Exception:
        return 0


def _fnPersistHubPortSafely(iPort):
    """Write iPort to the persistence file; swallow failures."""
    try:
        from vaibify.config.hubPortRegistry import fnPersistHubPort
    except ImportError:
        return
    try:
        fnPersistHubPort(iPort)
    except Exception:
        pass


def _fdictReadHubSlot(iPort):
    """Return a live hub-role slot holding iPort, or {} on any error."""
    try:
        from vaibify.config.sessionRegistry import (
            fdictReadHubSlotByPort,
        )
    except ImportError:
        return {}
    try:
        return fdictReadHubSlotByPort(iPort) or {}
    except Exception:
        return {}
