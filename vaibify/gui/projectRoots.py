"""The root directory a project's files live under, resolved per resource.

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
"""

__all__ = ["fsResolveProjectRoot"]

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
    dictProject = registryManager.fdictGetProject(sResourceId) or {}
    sDirectory = dictProject.get("sDirectory") or ""
    if not sDirectory:
        raise ValueError(
            f"Host project {sResourceId!r} records no directory; its "
            "registry entry is unusable until the directory is "
            "restored."
        )
    return sDirectory
