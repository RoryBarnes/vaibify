"""Global project registry at ~/.vaibify/registry.json."""

import fcntl
import json
import os
import tempfile

_S_REGISTRY_DIRECTORY = os.path.expanduser("~/.vaibify")
_S_REGISTRY_PATH = os.path.join(_S_REGISTRY_DIRECTORY, "registry.json")
_S_LOCK_PATH = os.path.join(_S_REGISTRY_DIRECTORY, "registry.lock")


def fdictLoadRegistry():
    """Read the registry file and return its contents.

    Returns
    -------
    dict
        Registry dict with key ``listProjects``.
    """
    if not os.path.isfile(_S_REGISTRY_PATH):
        return {"listProjects": []}
    try:
        with open(_S_REGISTRY_PATH, "r") as fileHandle:
            dictRegistry = json.load(fileHandle)
    except (json.JSONDecodeError, OSError):
        return {"listProjects": []}
    if not isinstance(dictRegistry, dict):
        return {"listProjects": []}
    dictRegistry.setdefault("listProjects", [])
    return dictRegistry


def fnSaveRegistry(dictRegistry):
    """Write the registry dict atomically to disk.

    Uses a file lock to prevent concurrent writes from
    losing updates.

    Parameters
    ----------
    dictRegistry : dict
        Registry dict with key ``listProjects``.
    """
    os.makedirs(_S_REGISTRY_DIRECTORY, exist_ok=True)
    with _ffileOpenRegistryLock():
        _fnWriteRegistryAtomic(dictRegistry)


def _ffileOpenRegistryLock():
    """Open and acquire an exclusive lock for registry writes."""
    fileHandle = open(_S_LOCK_PATH, "w")
    fcntl.flock(fileHandle, fcntl.LOCK_EX)
    return fileHandle


def _fnMutateRegistryLocked(fnMutateRegistry):
    """Run a read-modify-write of the registry under the exclusive lock.

    Reading before taking the lock lets two concurrent registrations
    both pass their duplicate checks against the same stale snapshot
    and the second write silently keep both entries — so the read, the
    check, the mutation, and the write must all happen while the lock
    is held. ``fnMutateRegistry`` may raise to abandon the write; the
    lock is released either way.
    """
    os.makedirs(_S_REGISTRY_DIRECTORY, exist_ok=True)
    with _ffileOpenRegistryLock():
        dictRegistry = fdictLoadRegistry()
        fnMutateRegistry(dictRegistry)
        _fnWriteRegistryAtomic(dictRegistry)


def _fnWriteRegistryAtomic(dictRegistry):
    """Write registry content to a temp file and replace."""
    sContent = json.dumps(dictRegistry, indent=2) + "\n"
    iFileDescriptor, sTempPath = tempfile.mkstemp(
        dir=_S_REGISTRY_DIRECTORY, suffix=".tmp",
    )
    bClosed = False
    try:
        os.write(iFileDescriptor, sContent.encode("utf-8"))
        os.close(iFileDescriptor)
        bClosed = True
        os.replace(sTempPath, _S_REGISTRY_PATH)
    except Exception:
        if not bClosed:
            os.close(iFileDescriptor)
        _fnSilentRemove(sTempPath)
        raise


def _fnSilentRemove(sPath):
    """Remove a file, ignoring errors if it does not exist."""
    try:
        os.unlink(sPath)
    except OSError:
        pass


def fsDiscoverConfigInDirectory(sDirectory):
    """Find the vaibify config file in a project directory.

    Parameters
    ----------
    sDirectory : str
        Absolute path to the project directory.

    Returns
    -------
    str
        Absolute path to the config file found.

    Raises
    ------
    FileNotFoundError
        If no config file is found in the directory.
    """
    sPath = os.path.join(sDirectory, "vaibify.yml")
    if os.path.isfile(sPath):
        return sPath
    raise FileNotFoundError(
        f"No vaibify.yml found in {sDirectory}"
    )


def fnAddProject(sDirectory, sMode="container"):
    """Register a project directory in the global registry.

    Parameters
    ----------
    sDirectory : str
        Absolute path to the project directory.
    sMode : str
        ``"container"`` (the default) or ``"host"``. A host project's
        pipeline runs directly on this machine; its registry name is
        its resource id everywhere a container project carries a
        Docker id.

    Raises
    ------
    FileNotFoundError
        If no config file exists in the directory.
    ValueError
        If the project is already registered, or ``sMode`` is not a
        recognized mode.
    """
    if sMode not in ("container", "host"):
        raise ValueError(
            f"Unknown project mode {sMode!r}; expected 'container' "
            "or 'host'"
        )
    sAbsDirectory = os.path.abspath(sDirectory)
    sConfigPath = fsDiscoverConfigInDirectory(sAbsDirectory)
    sName = _fsProjectNameFromConfig(sConfigPath)
    sContainerName = sName
    dictProject = _fdictBuildProjectEntry(
        sName, sAbsDirectory, sConfigPath, sContainerName, sMode,
    )

    def fnAppendUnlessDuplicate(dictRegistry):
        _fnCheckNotDuplicate(dictRegistry, sName, sAbsDirectory)
        dictRegistry["listProjects"].append(dictProject)

    _fnMutateRegistryLocked(fnAppendUnlessDuplicate)


def _fsProjectNameFromConfig(sConfigPath):
    """Load config and return the project name."""
    from vaibify.config.projectConfig import fconfigLoadFromFile
    configProject = fconfigLoadFromFile(sConfigPath)
    return configProject.sProjectName


def _fnCheckNotDuplicate(dictRegistry, sName, sDirectory):
    """Raise ValueError on a name or physical-directory duplicate.

    Two registry entries must never resolve to one physical directory:
    locks, journals, and ownership are all name-keyed, so a directory
    registered under two names would grant two "exclusive" sessions on
    the same files.
    """
    for dictExisting in dictRegistry["listProjects"]:
        if dictExisting["sName"] == sName:
            raise ValueError(
                f"Container '{sName}' is already registered"
            )
        if _fbSamePhysicalDirectory(
            dictExisting["sDirectory"], sDirectory,
        ):
            raise ValueError(
                f"Directory '{sDirectory}' is already registered "
                f"as '{dictExisting['sName']}'"
            )


def _fbSamePhysicalDirectory(sExistingPath, sCandidatePath):
    """Return True when two paths name one physical directory.

    ``os.path.samefile`` compares inodes, which is what catches symlink
    aliases and case variants on macOS's case-insensitive filesystems —
    realpath *string* equality would miss the latter. The realpath
    fallback covers entries whose directory no longer exists on disk.
    """
    try:
        return os.path.samefile(sExistingPath, sCandidatePath)
    except OSError:
        return (
            os.path.realpath(sExistingPath)
            == os.path.realpath(sCandidatePath)
        )


def _fdictBuildProjectEntry(
    sName, sDirectory, sConfigPath, sContainerName, sMode,
):
    """Construct a registry entry dict.

    ``sMode`` is stored explicitly for new entries; entries written
    before host mode existed have no key, and an absent key reads as
    container mode everywhere (see :func:`fbIsHostProject`).
    """
    return {
        "sName": sName,
        "sDirectory": sDirectory,
        "sConfigPath": sConfigPath,
        "sContainerName": sContainerName,
        "sMode": sMode,
    }


def fnRemoveProject(sName):
    """Remove a project from the registry by name.

    Parameters
    ----------
    sName : str
        Project name to remove.

    Raises
    ------
    KeyError
        If the project is not found.
    """
    def fnRemoveByName(dictRegistry):
        listProjects = dictRegistry["listProjects"]
        listRemaining = [
            dictProject for dictProject in listProjects
            if dictProject["sName"] != sName
        ]
        if len(listRemaining) == len(listProjects):
            raise KeyError(
                f"Project '{sName}' not found in registry"
            )
        dictRegistry["listProjects"] = listRemaining

    _fnMutateRegistryLocked(fnRemoveByName)


def fsGetContainerUser(sContainerName):
    """Return the container user for a registered container.

    Parameters
    ----------
    sContainerName : str
        Container name to look up in the registry.

    Returns
    -------
    str
        The container user, or ``"researcher"`` as fallback.
    """
    dictProject = fdictGetProject(sContainerName)
    if dictProject is None:
        return "researcher"
    try:
        from vaibify.cli.configLoader import fconfigLoadFromPath
        configProject = fconfigLoadFromPath(dictProject["sConfigPath"])
        return configProject.sContainerUser
    except Exception:
        return "researcher"


def fbIsHostProject(sResourceId):
    """Return True when the resource id names a registered host project.

    A host project's resource id is its registry name; a container's
    resource id is a Docker container id, which is not a registry key,
    so a missing entry reads as container mode. An entry without
    ``sMode`` also reads as container mode — every entry written before
    host mode existed is a container project.
    """
    dictProject = fdictGetProject(sResourceId)
    if dictProject is None:
        return False
    return dictProject.get("sMode") == "host"


def fdictGetProject(sName):
    """Return the registry entry for a project, or None.

    Parameters
    ----------
    sName : str
        Project name to look up.

    Returns
    -------
    dict or None
        The project entry, or None if not found.
    """
    dictRegistry = fdictLoadRegistry()
    for dictProject in dictRegistry["listProjects"]:
        if dictProject["sName"] == sName:
            return dictProject
    return None


def flistGetAllProjects():
    """Return the list of all registered projects.

    Returns
    -------
    list
        List of project entry dicts.
    """
    dictRegistry = fdictLoadRegistry()
    return dictRegistry["listProjects"]


def flistGetAllProjectsWithStatus():
    """Return all projects enriched with container status.

    Returns
    -------
    list
        Each entry has added keys: ``bImageExists``,
        ``bRunning``, ``sStatus``.
    """
    listProjects = flistGetAllProjects()
    listEnriched = []
    for dictProject in listProjects:
        dictEnriched = _fdictEnrichWithStatus(dictProject)
        listEnriched.append(dictEnriched)
    return listEnriched


def _fdictEnrichWithStatus(dictProject):
    """Add status fields to a project entry copy, branching by mode."""
    if dictProject.get("sMode") == "host":
        return _fdictEnrichHostProjectStatus(dictProject)
    from vaibify.docker.imageBuilder import fbImageExists
    from vaibify.docker.containerManager import (
        fdictGetContainerStatus,
    )
    dictEnriched = dict(dictProject)
    sContainerName = dictProject["sContainerName"]
    sImageTag = f"{sContainerName}:latest"
    dictEnriched["bImageExists"] = fbImageExists(sImageTag)
    dictStatus = fdictGetContainerStatus(sContainerName)
    dictEnriched["bRunning"] = dictStatus["bRunning"]
    dictEnriched["sStatus"] = _fsResolveDisplayStatus(
        dictEnriched["bImageExists"], dictStatus,
    )
    return dictEnriched


def _fdictEnrichHostProjectStatus(dictProject):
    """Add status fields to a host entry copy; Docker is never consulted.

    Host picker vocabulary (host-mode plan §9): ``ready`` when the
    project directory and its config are both present, ``missing``
    when either is gone (the tile renders red with the path shown).
    ``in use`` is not decided here — it is the existing name-keyed
    lock annotation, which works unchanged for host names. There is
    no image and no container, so those fields are honestly False.
    """
    from vaibify.config import preferencesStore
    dictEnriched = dict(dictProject)
    dictEnriched["bImageExists"] = False
    dictEnriched["bRunning"] = False
    sDirectory = dictProject.get("sDirectory", "")
    bProjectPresent = (
        os.path.isdir(sDirectory)
        and os.path.isfile(dictProject.get("sConfigPath", ""))
    )
    dictEnriched["sStatus"] = "ready" if bProjectPresent else "missing"
    # Whether the uncontained-execution warning was acknowledged is
    # answered HERE rather than by the dashboard comparing paths: the
    # acknowledgement is keyed by canonical directory, and a frontend
    # doing its own realpath would key a symlinked alias differently
    # and warn again for a project the researcher already accepted.
    dictEnriched["bHostWarningAcknowledged"] = (
        bool(sDirectory)
        and preferencesStore.fbHostWarningAcknowledged(sDirectory)
    )
    return dictEnriched


def _fsResolveDisplayStatus(bImageExists, dictContainerStatus):
    """Return a human-readable status string."""
    if dictContainerStatus["bRunning"]:
        return "running"
    if bImageExists:
        return "stopped"
    return "not built"
