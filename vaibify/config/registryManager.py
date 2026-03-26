"""Global project registry at ~/.vaibify/registry.json."""

import json
import os
import re
import tempfile

_S_REGISTRY_DIRECTORY = os.path.expanduser("~/.vaibify")
_S_REGISTRY_PATH = os.path.join(_S_REGISTRY_DIRECTORY, "registry.json")


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

    Parameters
    ----------
    dictRegistry : dict
        Registry dict with key ``listProjects``.
    """
    os.makedirs(_S_REGISTRY_DIRECTORY, exist_ok=True)
    sContent = json.dumps(dictRegistry, indent=2) + "\n"
    iFileDescriptor, sTempPath = tempfile.mkstemp(
        dir=_S_REGISTRY_DIRECTORY, suffix=".tmp",
    )
    try:
        os.write(iFileDescriptor, sContent.encode("utf-8"))
        os.close(iFileDescriptor)
        os.replace(sTempPath, _S_REGISTRY_PATH)
    except Exception:
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


def fsContainerNameFromDirectory(sDirectory):
    """Derive a Docker container name from a directory path.

    Parameters
    ----------
    sDirectory : str
        Absolute path to the project directory.

    Returns
    -------
    str
        Lowercase, hyphen-separated name.
    """
    sBaseName = os.path.basename(os.path.normpath(sDirectory))
    sLowered = sBaseName.lower()
    sCleaned = re.sub(r"[^a-z0-9]+", "-", sLowered)
    return sCleaned.strip("-")


def fnAddProject(sDirectory):
    """Register a project directory in the global registry.

    Parameters
    ----------
    sDirectory : str
        Absolute path to the project directory.

    Raises
    ------
    FileNotFoundError
        If no config file exists in the directory.
    ValueError
        If the project is already registered.
    """
    sAbsDirectory = os.path.abspath(sDirectory)
    sConfigPath = fsDiscoverConfigInDirectory(sAbsDirectory)
    sName = _fsProjectNameFromConfig(sConfigPath)
    dictRegistry = fdictLoadRegistry()
    _fnCheckNotDuplicate(dictRegistry, sName, sAbsDirectory)
    sContainerName = sName
    dictProject = _fdictBuildProjectEntry(
        sName, sAbsDirectory, sConfigPath, sContainerName,
    )
    dictRegistry["listProjects"].append(dictProject)
    fnSaveRegistry(dictRegistry)


def _fsProjectNameFromConfig(sConfigPath):
    """Load config and return the project name."""
    from vaibify.config.projectConfig import fconfigLoadFromFile
    configProject = fconfigLoadFromFile(sConfigPath)
    return configProject.sProjectName


def _fnCheckNotDuplicate(dictRegistry, sName, sDirectory):
    """Raise ValueError if container name already registered."""
    for dictExisting in dictRegistry["listProjects"]:
        if dictExisting["sName"] == sName:
            raise ValueError(
                f"Container '{sName}' is already registered"
            )


def _fdictBuildProjectEntry(
    sName, sDirectory, sConfigPath, sContainerName,
):
    """Construct a registry entry dict."""
    return {
        "sName": sName,
        "sDirectory": sDirectory,
        "sConfigPath": sConfigPath,
        "sContainerName": sContainerName,
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
    dictRegistry = fdictLoadRegistry()
    listProjects = dictRegistry["listProjects"]
    iOriginalLength = len(listProjects)
    dictRegistry["listProjects"] = [
        dictProject for dictProject in listProjects
        if dictProject["sName"] != sName
    ]
    if len(dictRegistry["listProjects"]) == iOriginalLength:
        raise KeyError(f"Project '{sName}' not found in registry")
    fnSaveRegistry(dictRegistry)


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
    """Add Docker status fields to a project entry copy."""
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


def _fsResolveDisplayStatus(bImageExists, dictContainerStatus):
    """Return a human-readable status string."""
    if dictContainerStatus["bRunning"]:
        return "running"
    if bImageExists:
        return "stopped"
    return "not built"
