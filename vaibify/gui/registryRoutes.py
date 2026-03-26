"""API routes for the global project registry."""

import logging
import os

from fastapi import HTTPException
from pydantic import BaseModel
from typing import Optional

logger = logging.getLogger("vaibify")


class AddProjectRequest(BaseModel):
    sDirectory: str


def fnRegisterRegistryRoutes(app, dictCtx):
    """Register all registry and container lifecycle routes."""
    _fnRegisterGetRegistry(app, dictCtx)
    _fnRegisterAddProject(app, dictCtx)
    _fnRegisterRemoveProject(app, dictCtx)
    _fnRegisterBuildContainer(app, dictCtx)
    _fnRegisterStartContainer(app, dictCtx)
    _fnRegisterStopContainer(app, dictCtx)
    _fnRegisterHostDirectories(app, dictCtx)


def _fnRegisterGetRegistry(app, dictCtx):
    """Register GET /api/registry — list all projects with status.

    Merges registered projects with auto-discovered running
    containers so the landing page shows everything.
    """

    @app.get("/api/registry")
    async def fnGetRegistry():
        dictCtx["require"]()
        from vaibify.config.registryManager import (
            flistGetAllProjectsWithStatus,
        )
        listRegistered = flistGetAllProjectsWithStatus()
        listVaibify, listUnrecognized = (
            _ftupleDiscoverAllContainers(dictCtx)
        )
        listContainers = _flistMergeProjectsAndContainers(
            listRegistered, listVaibify,
        )
        return {
            "listContainers": listContainers,
            "listUnrecognized": listUnrecognized,
        }


def _fnRegisterAddProject(app, dictCtx):
    """Register POST /api/registry — add a project directory."""

    @app.post("/api/registry")
    async def fnAddProject(request: AddProjectRequest):
        dictCtx["require"]()
        from vaibify.config.registryManager import (
            fnAddProject, fdictGetProject,
        )
        try:
            fnAddProject(request.sDirectory)
        except FileNotFoundError as error:
            raise HTTPException(404, str(error))
        except ValueError as error:
            raise HTTPException(409, str(error))
        sName = _fsProjectNameForDirectory(request.sDirectory)
        return fdictGetProject(sName)


def _fsProjectNameForDirectory(sDirectory):
    """Load config from directory and return project name."""
    from vaibify.config.registryManager import (
        fsDiscoverConfigInDirectory,
    )
    from vaibify.cli.configLoader import fconfigLoadFromPath
    sConfigPath = fsDiscoverConfigInDirectory(sDirectory)
    configProject = fconfigLoadFromPath(sConfigPath)
    return configProject.sProjectName


def _fnRegisterRemoveProject(app, dictCtx):
    """Register DELETE /api/registry/{sName}."""

    @app.delete("/api/registry/{sName}")
    async def fnRemoveProject(sName: str):
        dictCtx["require"]()
        from vaibify.config.registryManager import (
            fnRemoveProject,
        )
        try:
            fnRemoveProject(sName)
        except KeyError as error:
            raise HTTPException(404, str(error))
        return {"bSuccess": True}


def _fnRegisterBuildContainer(app, dictCtx):
    """Register POST /api/containers/{sName}/build."""

    @app.post("/api/containers/{sName}/build")
    async def fnBuildContainer(sName: str):
        dictCtx["require"]()
        dictProject = _fdictRequireProject(sName)
        try:
            _fnExecuteBuild(dictProject)
        except Exception as error:
            logger.error("Build failed for %s: %s", sName, error)
            raise HTTPException(500, "Build failed")
        return {"bSuccess": True, "sMessage": "Build complete"}


def _fnExecuteBuild(dictProject):
    """Load config and run the Docker image build."""
    from vaibify.cli.configLoader import (
        fconfigLoadFromPath, fsDockerDir,
    )
    from vaibify.cli.commandBuild import fnBuildFromConfig
    configProject = fconfigLoadFromPath(
        dictProject["sConfigPath"],
    )
    sDockerDir = fsDockerDir()
    fnBuildFromConfig(configProject, sDockerDir, bNoCache=False)


def _fnRegisterStartContainer(app, dictCtx):
    """Register POST /api/containers/{sName}/start."""

    @app.post("/api/containers/{sName}/start")
    async def fnStartContainer(sName: str):
        dictCtx["require"]()
        dictProject = _fdictRequireProject(sName)
        try:
            sContainerId = _fsExecuteStart(dictProject)
        except Exception as error:
            logger.error("Start failed for %s: %s", sName, error)
            raise HTTPException(500, "Start failed")
        return {
            "bSuccess": True,
            "sContainerId": sContainerId,
        }


def _fsExecuteStart(dictProject):
    """Load config and start the container in detached mode."""
    from vaibify.cli.configLoader import (
        fconfigLoadFromPath, fsDockerDir,
    )
    from vaibify.docker.containerManager import (
        fsStartContainerDetached,
    )
    configProject = fconfigLoadFromPath(
        dictProject["sConfigPath"],
    )
    sDockerDir = fsDockerDir()
    return fsStartContainerDetached(configProject, sDockerDir)


def _fnRegisterStopContainer(app, dictCtx):
    """Register POST /api/containers/{sName}/stop."""

    @app.post("/api/containers/{sName}/stop")
    async def fnStopContainer(sName: str):
        dictCtx["require"]()
        dictProject = _fdictRequireProject(sName)
        sContainerName = dictProject["sContainerName"]
        try:
            _fnExecuteStop(sContainerName)
        except Exception as error:
            logger.error("Stop failed for %s: %s", sName, error)
            raise HTTPException(500, "Stop failed")
        return {"bSuccess": True}


def _fnExecuteStop(sContainerName):
    """Stop and remove a running container."""
    from vaibify.docker.containerManager import fnStopContainer
    fnStopContainer(sContainerName)


def _fdictRequireProject(sName):
    """Look up a project in the registry or raise 404."""
    from vaibify.config.registryManager import fdictGetProject
    dictProject = fdictGetProject(sName)
    if dictProject is None:
        raise HTTPException(
            404, f"Project '{sName}' not found in registry"
        )
    return dictProject


def _ftupleDiscoverAllContainers(dictCtx):
    """Query Docker for all running containers.

    Returns
    -------
    tuple
        (listVaibify, listUnrecognized) where vaibify containers
        have ``.vaibify/`` inside and unrecognized do not.
    """
    connectionDocker = dictCtx.get("docker")
    if connectionDocker is None:
        return [], []
    try:
        listContainers = connectionDocker.flistGetRunningContainers()
    except Exception:
        return [], []
    return _ftupleSplitContainers(connectionDocker, listContainers)


def _ftupleSplitContainers(connectionDocker, listContainers):
    """Split containers into vaibify and unrecognized lists."""
    listVaibify = []
    listUnrecognized = []
    for dictContainer in listContainers:
        if _fbIsVaibifyContainer(connectionDocker, dictContainer):
            listVaibify.append(
                _fdictContainerToProject(
                    connectionDocker, dictContainer,
                )
            )
        else:
            listUnrecognized.append(dictContainer)
    return listVaibify, listUnrecognized


def _fbIsVaibifyContainer(connectionDocker, dictContainer):
    """Return True if the container has a .vaibify directory."""
    try:
        iExitCode, _ = connectionDocker.ftResultExecuteCommand(
            dictContainer["sContainerId"],
            "test -d /workspace/.vaibify",
        )
        return iExitCode == 0
    except Exception:
        return False


def _fdictContainerToProject(connectionDocker, dictContainer):
    """Convert a Docker container dict to project-like dict."""
    return {
        "sName": dictContainer["sName"],
        "sDirectory": "",
        "sConfigPath": "",
        "sContainerName": dictContainer["sName"],
        "sContainerId": dictContainer["sContainerId"],
        "bImageExists": True,
        "bRunning": True,
        "sStatus": "running",
        "bDiscovered": True,
    }


def _flistMergeProjectsAndContainers(
    listRegistered, listDiscovered,
):
    """Merge registry entries with discovered containers.

    Discovered containers that match a registered project by
    name get their ``sContainerId`` added to the registry
    entry. Discovered containers with no registry match appear
    as separate entries.
    """
    setRegisteredNames = {
        dictProject["sContainerName"]
        for dictProject in listRegistered
    }
    for dictRegistered in listRegistered:
        _fnEnrichWithContainerId(dictRegistered, listDiscovered)
    listNew = [
        dictContainer for dictContainer in listDiscovered
        if dictContainer["sName"] not in setRegisteredNames
    ]
    return listRegistered + listNew


def _fnEnrichWithContainerId(dictRegistered, listDiscovered):
    """Add sContainerId to a registry entry if running."""
    sContainerName = dictRegistered["sContainerName"]
    for dictContainer in listDiscovered:
        if dictContainer["sName"] == sContainerName:
            dictRegistered["sContainerId"] = (
                dictContainer["sContainerId"]
            )
            return


def _fnRegisterHostDirectories(app, dictCtx):
    """Register GET /api/host-directories for browsing host dirs."""

    @app.get("/api/host-directories")
    async def fnGetHostDirectories(
        sPath: Optional[str] = None,
    ):
        dictCtx["require"]()
        sAbsPath = sPath or os.path.expanduser("~")
        _fnValidateHostPath(sAbsPath)
        listEntries = flistQueryHostDirectory(sAbsPath)
        return {
            "sCurrentPath": sAbsPath,
            "bHasConfig": fbDirectoryHasConfig(sAbsPath),
            "listEntries": listEntries,
        }


def _fnValidateHostPath(sPath):
    """Raise HTTPException if the path is invalid or escapes home."""
    if not os.path.isabs(sPath):
        raise HTTPException(400, "Path must be absolute")
    sHome = os.path.expanduser("~")
    sResolved = os.path.realpath(sPath)
    if sResolved != sHome and not sResolved.startswith(sHome + os.sep):
        raise HTTPException(403, "Path is outside allowed root")
    if not os.path.isdir(sResolved):
        raise HTTPException(404, "Directory not found")


def flistQueryHostDirectory(sAbsPath):
    """List subdirectories on the host filesystem.

    Returns the same entry shape as the container directory
    listing (``sName``, ``sPath``, ``bIsDirectory``) plus
    ``bHasConfig`` indicating a vaibify project.

    Parameters
    ----------
    sAbsPath : str
        Absolute path to list.

    Returns
    -------
    list
        Sorted list of directory entry dicts.
    """
    listEntries = []
    try:
        for entry in os.scandir(sAbsPath):
            if entry.is_dir(follow_symlinks=False):
                listEntries.append(_fdictBuildHostEntry(entry))
    except PermissionError:
        raise HTTPException(403, "Permission denied")
    return _flistSortDirectoryEntries(listEntries)


def _fdictBuildHostEntry(entry):
    """Build a directory entry dict from an os.DirEntry."""
    return {
        "sName": entry.name,
        "sPath": entry.path,
        "bIsDirectory": True,
        "bHasConfig": fbDirectoryHasConfig(entry.path),
    }


def fbDirectoryHasConfig(sDirectoryPath):
    """Return True if the directory contains vaibify.yml."""
    sConfigPath = os.path.join(sDirectoryPath, "vaibify.yml")
    return os.path.isfile(sConfigPath)


def _flistSortDirectoryEntries(listEntries):
    """Sort entries: non-hidden alphabetically, then hidden."""
    listVisible = [
        e for e in listEntries if not e["sName"].startswith(".")
    ]
    listHidden = [
        e for e in listEntries if e["sName"].startswith(".")
    ]
    listVisible.sort(key=lambda e: e["sName"].lower())
    listHidden.sort(key=lambda e: e["sName"].lower())
    return listVisible + listHidden
