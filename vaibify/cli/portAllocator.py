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
    """Return True if a TCP bind on 127.0.0.1:iPort would succeed."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
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
    fnSaveConfig=None,
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

    ``fnSaveConfig`` is injected so this helper has no hard dependency
    on the YAML writer (kept testable). Callers pass
    ``projectConfig.fnSaveToFile`` in normal use.
    """
    if iExplicitPort is not None:
        return iExplicitPort
    if config.iDashboardPort > 0:
        return _fiAcquirePersistedPort(config)
    return _fiAssignAndPersistPort(
        config, sConfigPath, fnSaveConfig,
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


def _fiAssignAndPersistPort(config, sConfigPath, fnSaveConfig):
    """Pick a free port, persist it to vaibify.yml, return it."""
    iPort = fiPickFreePort()
    config.iDashboardPort = iPort
    if fnSaveConfig and sConfigPath:
        _fnPersistDashboardPort(config, sConfigPath, fnSaveConfig)
    return iPort


def _fnPersistDashboardPort(config, sConfigPath, fnSaveConfig):
    """Write the auto-assigned port back to disk; warn on failure."""
    iPort = config.iDashboardPort
    try:
        fnSaveConfig(config, sConfigPath)
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
    3. If a port is persisted but held by *our own* hub zombie
       (detected via ``sessionRegistry`` slot scan) → wait briefly.
    4. If held by anything else, or nothing is persisted → scan
       upward via the existing allocator, persist the winning port,
       and warn the user on stderr.
    """
    if iExplicitPort is not None:
        return iExplicitPort
    iPersisted = _fiReadPersistedHubPort()
    if iPersisted > 0:
        iResolved = _fiTryPersistedHubPort(iPersisted)
        if iResolved > 0:
            return iResolved
    return _fiAssignAndPersistHubPort(iPersisted)


def _fiTryPersistedHubPort(iPersisted):
    """Return iPersisted if bindable now (possibly after a brief wait)."""
    if fbIsPortFree(iPersisted):
        return iPersisted
    if _fbWaitForHubZombieRelease(iPersisted):
        return iPersisted
    return 0


def _fiAssignAndPersistHubPort(iPersistedHint):
    """Pick a free port for the hub, persist it, warn on shift."""
    iPort = fiPickFreePort()
    _fnPersistHubPortSafely(iPort)
    if iPersistedHint > 0 and iPort != iPersistedHint:
        print(
            f"Hub port {iPersistedHint} is held by another process; "
            f"binding {iPort} instead. Existing dashboard tabs at "
            f"the old URL will need to be reopened.",
            file=sys.stderr,
        )
    elif iPersistedHint == 0:
        print(
            f"Assigned hub port {iPort} (persisted for future "
            f"restarts).",
            file=sys.stderr,
        )
    return iPort


def _fbWaitForHubZombieRelease(iPort):
    """Poll for the hub's own dying instance to release iPort."""
    dictHolder = _fdictReadHubSlot(iPort)
    if not dictHolder:
        return False
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
