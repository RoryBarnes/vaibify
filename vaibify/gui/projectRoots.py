"""The roots a resource's files live under, resolved per resource.

A container project's files live under the workspace volume mounted
into the container, so ``/workspace`` is both the discovery root and
the boundary every path guard measures against. A host project has no
container and no volume: its files live in the directory the
researcher registered, on the host filesystem. Discovery searches,
relative-path bases, and path guards all reach for the same idea --
"the root this project's files live under" -- and until host mode
there was only one answer, so the idea had no name.

The container answer is passed in rather than read from a constant
here: the sites that ask already know which root they mean (the
workflow search root, the connect path guard's root), and threading it
keeps this module from becoming a second authority on what those roots
are. Only a host resource overrides it.

**Ephemeral working files ask the same question about a different
root.** A container may write a throwaway program, a credential it is
about to hand to a keyring, or a graph description to ``/tmp``,
because ``/tmp`` inside a container is disposable by construction. On
the host it is neither disposable nor admitted: the host path guard
permits exactly the project root and the host-diagnostics subtree, so
a ``/tmp`` literal is refused there -- which is how the whole
introspection lane came to answer 500 for a host project. That is one
question ("which root does this resource use") with two answers, so it
lives beside the other one rather than in a module of its own.
"""

__all__ = ["fsResolveProjectRoot", "fsResolveScratchDirectory"]

from vaibify.config import registryManager


def fsResolveProjectRoot(sResourceId, sContainerRoot):
    """Return the root directory this resource's project files live under.

    Parameters
    ----------
    sResourceId : str
        The identifier every container-scoped route already carries: a
        Docker container id for a container project, the registry name
        for a host project.
    sContainerRoot : str
        The root to answer with for anything that is not a host
        project. An unregistered id resolves here too, which is what a
        viewer connected straight to a container id is.

    Returns
    -------
    str
        ``sContainerRoot`` unchanged for container resources; the
        registered directory for a host project.

    Raises
    ------
    ValueError
        When a registry entry declares host mode but records no
        directory. There is no honest answer for such an entry, and
        falling back to the container root would validate host paths
        against a root no host path can ever be inside -- or, on the
        discovery lane, silently search a directory that does not
        exist and report a project with no workflows.
    """
    if not registryManager.fbIsHostProject(sResourceId):
        return sContainerRoot
    return _fsHostProjectDirectory(sResourceId)


def _fsHostProjectDirectory(sResourceId):
    """Return a host project's registered directory, or raise."""
    dictProject = registryManager.fdictGetProject(sResourceId) or {}
    sDirectory = dictProject.get("sDirectory") or ""
    if not sDirectory:
        raise ValueError(
            f"Host project {sResourceId!r} records no directory; its "
            "registry entry is unusable until the directory is "
            "restored."
        )
    return sDirectory


def fsResolveScratchDirectory(
    sResourceId, sOperationName, sContainerScratchRoot,
):
    """Return the directory one operation's ephemeral files belong in.

    Parameters
    ----------
    sResourceId : str
        The identifier every container-scoped route already carries.
    sOperationName : str
        A bare name identifying this operation, unique per call where
        two of them can overlap. On the host it names the operation's
        own directory under the project's scratch subtree, which is
        what the sweeper later retires by age; callers compose their
        file name from it in both modes, so a second export cannot
        overwrite the first one's input while it is still being read.
    sContainerScratchRoot : str
        The directory to answer with for anything that is not a host
        project -- ``/tmp`` at every call site today.

    Returns
    -------
    str
        ``sContainerScratchRoot`` unchanged for container resources; a
        freshly created, private (0700) operation directory under the
        host-diagnostics subtree for a host project. That subtree is
        one of the two roots the host path guard admits, so a write
        there is permitted where a ``/tmp`` write is refused.
    """
    if not registryManager.fbIsHostProject(sResourceId):
        return sContainerScratchRoot
    from vaibify.host import hostScratch
    return hostScratch.fsCreateOperationScratchDirectory(
        _fsHostProjectDirectory(sResourceId), sOperationName,
    )
