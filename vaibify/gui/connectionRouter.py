"""The connection router: one object, two legs, dispatch by registry mode.

``dictCtx["docker"]`` used to hold a ``DockerConnection`` or ``None``.
It now holds this router, which owns that same Docker leg (still
possibly ``None`` — the daemon being down did not stop being a state)
plus a ``HostConnection``, and dispatches every resource-scoped call by
asking the registry which mode the resource id names: a registered host
project goes to the host leg, everything else — Docker ids, the browser
lane's fake containers, test doubles' name==id fixtures — goes to the
Docker leg. The ~200 transport call sites pass the resource id already,
so none of them change; what changed is spelled out in
``vaibify/config/connectionAvailability.py``: the fifteen-odd
daemon-down branches ask ``fbDockerReachable`` instead of ``is None``.

The router adds NO authority: both legs carry their own admission
gates, and a call that reaches a missing Docker leg raises the typed
``DockerLegUnavailableError`` rather than an ``AttributeError`` from
``None`` — the sites that used to guard with ``is None`` now guard
with the predicate, and anything unguarded fails loudly with the
reason in its name.

Docker-only surface (container discovery, the exec-id PTY cluster) is
delegated verbatim through ``__getattr__``: those calls have no host
meaning, and the capability inventory's dispositions — not this
router — are what refuse container-only operations for host resources
at the route layer. The process-group signal/probe pair LEFT that
surface on 2026-08-15: the host terminal gave both a host meaning, so
they are resource-routed like every other duck call the drain
machinery makes.
"""

__all__ = [
    "ConnectionRouter",
    "DockerLegUnavailableError",
    "TUPLE_RESOURCE_ROUTED_METHOD_NAMES",
]

from vaibify.config.registryManager import fbIsHostProject

TUPLE_RESOURCE_ROUTED_METHOD_NAMES = (
    "ftResultExecuteCommand",
    "ftRunInContainerStreamed",
    "ftRunInContainerStreamedWithChunks",
    "fnWriteFile",
    "fnWriteFileViaTar",
    "fnWriteTreeViaTar",
    "fbaFetchFile",
    "flistDirectoryEntries",
    "fbContainerPathIsFile",
    "fbContainerPathIsDirectory",
    "flistContainerPathsExist",
    "flistContainerDirectoriesExist",
    "fdictHashContainerRepoPaths",
    "fdictReadFilesystemUsage",
    "fdictStatPathMtimes",
    "flistReadGitRepoStatuses",
    "fsHashContainerFileSha256",
    "fiterStreamFile",
    "fdictProbeProcessGroupMembers",
    "fnSignalProcessGroupMembers",
    "fdictLaunchTerminalShellSuspended",
)


class DockerLegUnavailableError(RuntimeError):
    """A Docker-lane call was made while no daemon connection exists."""


class ConnectionRouter:
    """Duck-typed connection dispatching by the resource's registry mode."""

    def __init__(self, connectionDockerLeg, connectionHostLeg):
        self.connectionDockerLeg = connectionDockerLeg
        self.connectionHostLeg = connectionHostLeg

    def fbDockerLegPresent(self):
        """The marker ``fbDockerReachable`` probes: is a daemon leg live?"""
        return self.connectionDockerLeg is not None

    def fconnectionForResource(self, sResourceId):
        """Return the leg serving a resource id, failing loudly on none."""
        if fbIsHostProject(sResourceId):
            return self.connectionHostLeg
        if self.connectionDockerLeg is None:
            raise DockerLegUnavailableError(
                f"'{sResourceId}' is not a registered host project and "
                "no Docker connection exists; guard Docker-lane calls "
                "with fbDockerReachable"
            )
        return self.connectionDockerLeg

    # -- the resource-routed duck surface, one explicit method each so
    # every delegation is a grep-able attribute call the capability
    # inventory records, with its honest return-cast prefix. --

    def ftResultExecuteCommand(self, sResourceId, *tArguments, **dictKeywords):
        """Dispatch to the leg the resource id names."""
        return self.fconnectionForResource(
            sResourceId,
        ).ftResultExecuteCommand(sResourceId, *tArguments, **dictKeywords)

    def ftRunInContainerStreamed(self, sResourceId, *tArguments, **dictKeywords):
        """Dispatch to the leg the resource id names."""
        return self.fconnectionForResource(
            sResourceId,
        ).ftRunInContainerStreamed(sResourceId, *tArguments, **dictKeywords)

    def ftRunInContainerStreamedWithChunks(
        self, sResourceId, *tArguments, **dictKeywords,
    ):
        """Dispatch to the leg the resource id names."""
        return self.fconnectionForResource(
            sResourceId,
        ).ftRunInContainerStreamedWithChunks(sResourceId, *tArguments, **dictKeywords)

    def fnWriteFile(self, sResourceId, *tArguments, **dictKeywords):
        """Dispatch to the leg the resource id names."""
        self.fconnectionForResource(sResourceId).fnWriteFile(
            sResourceId, *tArguments, **dictKeywords,
        )

    def fnWriteFileViaTar(self, sResourceId, *tArguments, **dictKeywords):
        """Dispatch to the leg the resource id names."""
        self.fconnectionForResource(sResourceId).fnWriteFileViaTar(
            sResourceId, *tArguments, **dictKeywords,
        )

    def fnWriteTreeViaTar(self, sResourceId, *tArguments, **dictKeywords):
        """Dispatch to the leg the resource id names."""
        self.fconnectionForResource(sResourceId).fnWriteTreeViaTar(
            sResourceId, *tArguments, **dictKeywords,
        )

    def fbaFetchFile(self, sResourceId, *tArguments, **dictKeywords):
        """Dispatch to the leg the resource id names."""
        return self.fconnectionForResource(sResourceId).fbaFetchFile(
            sResourceId, *tArguments, **dictKeywords,
        )

    def flistDirectoryEntries(self, sResourceId, *tArguments, **dictKeywords):
        """Dispatch to the leg the resource id names."""
        return self.fconnectionForResource(
            sResourceId,
        ).flistDirectoryEntries(sResourceId, *tArguments, **dictKeywords)

    def fbContainerPathIsFile(self, sResourceId, *tArguments, **dictKeywords):
        """Dispatch to the leg the resource id names."""
        return self.fconnectionForResource(
            sResourceId,
        ).fbContainerPathIsFile(sResourceId, *tArguments, **dictKeywords)

    def fbContainerPathIsDirectory(self, sResourceId, *tArguments, **dictKeywords):
        """Dispatch to the leg the resource id names."""
        return self.fconnectionForResource(
            sResourceId,
        ).fbContainerPathIsDirectory(sResourceId, *tArguments, **dictKeywords)

    def flistContainerPathsExist(self, sResourceId, *tArguments, **dictKeywords):
        """Dispatch to the leg the resource id names."""
        return self.fconnectionForResource(
            sResourceId,
        ).flistContainerPathsExist(sResourceId, *tArguments, **dictKeywords)

    def flistContainerDirectoriesExist(
        self, sResourceId, *tArguments, **dictKeywords
    ):
        """Dispatch to the leg the resource id names."""
        return self.fconnectionForResource(
            sResourceId,
        ).flistContainerDirectoriesExist(
            sResourceId, *tArguments, **dictKeywords
        )

    def fdictHashContainerRepoPaths(
        self, sResourceId, *tArguments, **dictKeywords
    ):
        """Dispatch to the leg the resource id names."""
        return self.fconnectionForResource(
            sResourceId,
        ).fdictHashContainerRepoPaths(
            sResourceId, *tArguments, **dictKeywords
        )

    def fdictReadFilesystemUsage(self, sResourceId, *tArguments, **dictKeywords):
        """Dispatch to the leg the resource id names."""
        return self.fconnectionForResource(
            sResourceId,
        ).fdictReadFilesystemUsage(sResourceId, *tArguments, **dictKeywords)

    def fdictStatPathMtimes(self, sResourceId, *tArguments, **dictKeywords):
        """Dispatch to the leg the resource id names."""
        return self.fconnectionForResource(
            sResourceId,
        ).fdictStatPathMtimes(sResourceId, *tArguments, **dictKeywords)

    def flistReadGitRepoStatuses(
        self, sResourceId, *tArguments, **dictKeywords,
    ):
        """Dispatch to the leg the resource id names."""
        return self.fconnectionForResource(
            sResourceId,
        ).flistReadGitRepoStatuses(
            sResourceId, *tArguments, **dictKeywords,
        )

    def fsHashContainerFileSha256(self, sResourceId, *tArguments, **dictKeywords):
        """Dispatch to the leg the resource id names."""
        return self.fconnectionForResource(sResourceId).fsHashContainerFileSha256(
            sResourceId, *tArguments, **dictKeywords,
        )

    def fiterStreamFile(self, sResourceId, *tArguments, **dictKeywords):
        """Dispatch to the leg the resource id names."""
        return self.fconnectionForResource(sResourceId).fiterStreamFile(
            sResourceId, *tArguments, **dictKeywords,
        )

    def fdictProbeProcessGroupMembers(
        self, sResourceId, *tArguments, **dictKeywords,
    ):
        """Dispatch to the leg the resource id names."""
        return self.fconnectionForResource(
            sResourceId,
        ).fdictProbeProcessGroupMembers(
            sResourceId, *tArguments, **dictKeywords,
        )

    def fnSignalProcessGroupMembers(
        self, sResourceId, *tArguments, **dictKeywords,
    ):
        """Dispatch to the leg the resource id names."""
        self.fconnectionForResource(
            sResourceId,
        ).fnSignalProcessGroupMembers(
            sResourceId, *tArguments, **dictKeywords,
        )

    def fdictLaunchTerminalShellSuspended(
        self, sResourceId, *tArguments, **dictKeywords,
    ):
        """Dispatch to the leg the resource id names.

        Host-only in practice — the Docker leg has no such method and
        a misrouted call fails loudly with ``AttributeError`` — but
        routed explicitly so the terminal seam can hold the ROUTER,
        never a leg, exactly like every other caller.
        """
        return self.fconnectionForResource(
            sResourceId,
        ).fdictLaunchTerminalShellSuspended(
            sResourceId, *tArguments, **dictKeywords,
        )

    def __getattr__(self, sAttributeName):
        """Delegate the Docker-only surface to the Docker leg verbatim."""
        if sAttributeName.startswith("_"):
            raise AttributeError(sAttributeName)
        if self.connectionDockerLeg is None:
            raise DockerLegUnavailableError(
                f"'{sAttributeName}' needs the Docker leg and no Docker "
                "connection exists; guard with fbDockerReachable"
            )
        return getattr(self.connectionDockerLeg, sAttributeName)
