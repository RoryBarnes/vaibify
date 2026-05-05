"""API routes for the global project registry."""

__all__ = [
    "AddProjectRequest",
    "CreateProjectRequest",
    "ContainerSettingsRequest",
    "CreateHostDirectoryRequest",
    "fnRegisterRegistryRoutes",
    "flistQueryHostDirectory",
    "fbDirectoryHasConfig",
]

import asyncio
import logging
import os
import re

from fastapi import HTTPException
from pydantic import BaseModel
from typing import List, Optional

logger = logging.getLogger("vaibify")

_RE_FOLDER_NAME = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.\- ]*$")


class AddProjectRequest(BaseModel):
    sDirectory: str


class CreateProjectRequest(BaseModel):
    sDirectory: str
    sProjectName: str
    sTemplateName: str
    sPythonVersion: str = "3.12"
    listRepositories: List[str] = []
    listFeatures: List[str] = []
    bUseGithubAuth: bool = True
    bNeverSleep: bool = False
    bNetworkIsolation: bool = False
    bClaudeAutoUpdate: bool = True
    listSystemPackages: List[str] = []
    listPythonPackages: List[str] = []
    listCondaPackages: List[str] = []
    sPackageManager: str = "pip"
    sPipInstallFlags: str = ""
    sContainerUser: str = "researcher"
    sBaseImage: str = "ubuntu:24.04"
    sWorkspaceRoot: str = "/workspace"


class ContainerSettingsRequest(BaseModel):
    bNeverSleep: Optional[bool] = None
    bClaudeAutoUpdate: Optional[bool] = None


class CreateHostDirectoryRequest(BaseModel):
    sParentPath: str
    sFolderName: str


def fnRegisterRegistryRoutes(app, dictCtx):
    """Register all registry and container lifecycle routes."""
    _fnRegisterGetRegistry(app, dictCtx)
    _fnRegisterAddProject(app, dictCtx)
    _fnRegisterRemoveProject(app, dictCtx)
    _fnRegisterBuildContainer(app, dictCtx)
    _fnRegisterStartContainer(app, dictCtx)
    _fnRegisterStopContainer(app, dictCtx)
    _fnRegisterContainerSettings(app, dictCtx)
    _fnRegisterHostDirectories(app, dictCtx)
    _fnRegisterGetTemplates(app, dictCtx)
    _fnRegisterGetTemplateConfig(app, dictCtx)
    _fnRegisterCreateProject(app, dictCtx)
    _fnRegisterCreateHostDirectory(app, dictCtx)
    _fnRegisterClaimContainer(app, dictCtx)
    _fnRegisterReleaseContainer(app, dictCtx)


def _fnRegisterGetRegistry(app, dictCtx):
    """Register GET /api/registry — list all projects with status.

    Merges registered projects with auto-discovered running
    containers so the landing page shows everything.
    """

    @app.get("/api/registry")
    async def fnGetRegistry():
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
        _fnAnnotateLockState(listContainers)
        return {
            "listContainers": listContainers,
            "listUnrecognized": listUnrecognized,
        }


def _fnAnnotateLockState(listContainers):
    """Populate bLocked / iLockedBy* fields on each container dict."""
    from vaibify.config.containerLock import fdictReadLockHolder
    for dictContainer in listContainers:
        sName = dictContainer.get("sName")
        if not sName:
            dictContainer["bLocked"] = False
            continue
        dictHolder = fdictReadLockHolder(sName)
        if dictHolder:
            dictContainer["bLocked"] = True
            dictContainer["iLockedByPid"] = dictHolder.get("iPid")
            dictContainer["iLockedByPort"] = dictHolder.get("iPort")
        else:
            dictContainer["bLocked"] = False


def _fnRejectInvalidProjectName(sName):
    """Raise HTTP 400 when sName is unsafe for lock operations."""
    from vaibify.config.containerLock import fbIsValidProjectName
    if not fbIsValidProjectName(sName):
        raise HTTPException(
            status_code=400,
            detail=f"Invalid container name: {sName!r}",
        )


def _fnRegisterClaimContainer(app, dictCtx):
    """Register POST /api/registry/{sName}/claim."""
    del dictCtx

    @app.post("/api/registry/{sName}/claim")
    async def fdictClaimContainer(sName: str):
        from vaibify.config.containerLock import (
            ContainerLockedError, fnAcquireContainerLock,
        )
        _fnRejectInvalidProjectName(sName)
        dictLocks = app.state.dictContainerLocks
        if sName in dictLocks:
            return {"sName": sName, "bClaimed": True}
        iPort = getattr(app.state, "iHubPort", 0)
        try:
            fileHandle = fnAcquireContainerLock(sName, iPort)
        except ContainerLockedError as error:
            raise HTTPException(
                status_code=409,
                detail={
                    "sName": sName,
                    "iLockedByPid": error.iHolderPid,
                    "iLockedByPort": error.iHolderPort,
                    "sMessage": str(error),
                },
            )
        dictLocks[sName] = fileHandle
        return {"sName": sName, "bClaimed": True}


def _fnRegisterReleaseContainer(app, dictCtx):
    """Register POST /api/registry/{sName}/release."""
    del dictCtx

    @app.post("/api/registry/{sName}/release")
    async def fdictReleaseContainer(sName: str):
        from vaibify.config.containerLock import fnReleaseContainerLock
        _fnRejectInvalidProjectName(sName)
        dictLocks = app.state.dictContainerLocks
        fileHandle = dictLocks.pop(sName, None)
        if fileHandle is not None:
            fnReleaseContainerLock(fileHandle)
        return {"sName": sName, "bReleased": True}


def _fnRegisterAddProject(app, dictCtx):
    """Register POST /api/registry — add a project directory."""

    @app.post("/api/registry")
    async def fnAddProject(request: AddProjectRequest):
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
    async def fnBuildContainer(
        sName: str, bNoCache: bool = False,
    ):
        dictCtx["require"]()
        dictProject = _fdictRequireProject(sName)
        try:
            await asyncio.to_thread(
                _fnExecuteBuild, dictProject, bNoCache,
            )
        except Exception as error:
            logger.error("Build failed for %s: %s", sName, error)
            raise HTTPException(500, "Build failed")
        return {"bSuccess": True, "sMessage": "Build complete"}


def _fnExecuteBuild(dictProject, bNoCache=False):
    """Load config and run the Docker image build."""
    from vaibify.cli.configLoader import (
        fconfigLoadFromPath, fsDockerDir,
    )
    from vaibify.cli.commandBuild import fnBuildFromConfig
    configProject = fconfigLoadFromPath(
        dictProject["sConfigPath"],
    )
    sDockerDir = fsDockerDir()
    fnBuildFromConfig(configProject, sDockerDir, bNoCache=bNoCache)


def _fnRegisterStartContainer(app, dictCtx):
    """Register POST /api/containers/{sName}/start."""

    @app.post("/api/containers/{sName}/start")
    async def fnStartContainer(sName: str):
        dictCtx["require"]()
        dictProject = _fdictRequireProject(sName)
        try:
            sContainerId = await asyncio.to_thread(
                _fsExecuteStart, dictProject,
            )
        except Exception as error:
            logger.error("Start failed for %s: %s", sName, error)
            raise HTTPException(500, f"Start failed: {error}")
        return {
            "bSuccess": True,
            "sContainerId": sContainerId,
        }


def _fsExecuteStart(dictProject):
    """Load config and start the container in detached mode."""
    from vaibify.cli.configLoader import (
        fconfigLoadFromPath, fsDockerDir,
    )
    from vaibify.docker.keepAliveManager import fnStartKeepAlive
    configProject = fconfigLoadFromPath(
        dictProject["sConfigPath"],
    )
    sContainerName = dictProject["sContainerName"]
    sContainerId = _fsStartOrCreate(
        configProject, sContainerName, fsDockerDir(),
    )
    if configProject.bNeverSleep:
        fnStartKeepAlive(sContainerName)
    return sContainerId


def _fsStartOrCreate(configProject, sContainerName, sDockerDir):
    """Remove any stopped container and create a fresh one.

    Always creates a new container so that secrets are mounted
    via volume args at creation time.  Restarting an existing
    container with ``docker start`` skips secret mounts and
    leaves ``/run/secrets/`` empty.
    """
    from vaibify.docker.containerManager import (
        fdictGetContainerStatus, fsStartContainerDetached,
    )
    dictStatus = fdictGetContainerStatus(sContainerName)
    if dictStatus["bRunning"]:
        raise RuntimeError(
            f"Container '{sContainerName}' is already running"
        )
    if dictStatus["bExists"]:
        _fnRemoveContainer(sContainerName)
    return fsStartContainerDetached(configProject, sDockerDir)


def _fnRemoveContainer(sContainerName):
    """Remove a stopped container so a fresh one can be created."""
    import subprocess
    subprocess.run(
        ["docker", "rm", sContainerName],
        capture_output=True, text=True,
    )


def _fnRegisterStopContainer(app, dictCtx):
    """Register POST /api/containers/{sName}/stop."""

    @app.post("/api/containers/{sName}/stop")
    async def fnStopContainer(sName: str):
        dictCtx["require"]()
        dictProject = _fdictRequireProject(sName)
        sContainerName = dictProject["sContainerName"]
        try:
            await asyncio.to_thread(
                _fnExecuteStop, sContainerName,
            )
        except Exception as error:
            logger.error("Stop failed for %s: %s", sName, error)
            raise HTTPException(500, f"Stop failed: {error}")
        return {"bSuccess": True}


def _fnExecuteStop(sContainerName):
    """Stop and remove a running container (idempotent)."""
    from vaibify.docker.containerManager import (
        fdictGetContainerStatus, fnRemoveStopped,
    )
    from vaibify.docker.keepAliveManager import fnStopKeepAlive
    dictStatus = fdictGetContainerStatus(sContainerName)
    if not dictStatus["bExists"]:
        fnStopKeepAlive(sContainerName)
        return
    if dictStatus["bRunning"]:
        _fnDockerStopCommand(sContainerName)
    fnRemoveStopped(sContainerName)
    fnStopKeepAlive(sContainerName)


def _fnDockerStopCommand(sContainerName):
    """Run 'docker stop' and raise with the real stderr on failure."""
    import subprocess
    resultProcess = subprocess.run(
        ["docker", "stop", sContainerName],
        capture_output=True, text=True,
    )
    if resultProcess.returncode != 0:
        raise RuntimeError(
            f"docker stop failed: "
            f"{resultProcess.stderr.strip()}"
        )


def _fnRegisterContainerSettings(app, dictCtx):
    """Register GET and POST /api/containers/{sName}/settings."""

    @app.get("/api/containers/{sName}/settings")
    async def fnGetContainerSettings(sName: str):
        dictProject = _fdictRequireProject(sName)
        from vaibify.config.projectConfig import fconfigLoadFromFile
        configProject = fconfigLoadFromFile(
            dictProject["sConfigPath"]
        )
        dictResult = {
            "bNeverSleep": configProject.bNeverSleep,
            "bClaudeInstalled": configProject.features.bClaude,
        }
        if configProject.features.bClaude:
            dictResult["bClaudeAutoUpdate"] = (
                configProject.features.bClaudeAutoUpdate
            )
        return dictResult

    @app.post("/api/containers/{sName}/settings")
    async def fnSetContainerSettings(
        sName: str, request: ContainerSettingsRequest
    ):
        dictProject = _fdictRequireProject(sName)
        bRestartRequired = False
        if request.bNeverSleep is not None:
            _fnUpdateYamlBoolField(
                dictProject["sConfigPath"], "neverSleep",
                request.bNeverSleep,
            )
        if request.bClaudeAutoUpdate is not None:
            bRestartRequired = _fbApplyClaudeAutoUpdate(
                dictProject["sConfigPath"],
                request.bClaudeAutoUpdate,
            )
        return {
            "bSuccess": True,
            "bRestartRequired": bRestartRequired,
        }


def _fbApplyClaudeAutoUpdate(sConfigPath, bNewValue):
    """Apply claudeAutoUpdate; 409 if Claude absent. Return bChanged."""
    from vaibify.config.projectConfig import fconfigLoadFromFile
    configProject = fconfigLoadFromFile(sConfigPath)
    if not configProject.features.bClaude:
        raise HTTPException(
            409,
            "Claude Code is not installed in this project.",
        )
    if configProject.features.bClaudeAutoUpdate == bNewValue:
        return False
    _fnUpdateFeaturesBoolField(
        sConfigPath, "claudeAutoUpdate", bNewValue,
    )
    return True


def _fnUpdateFeaturesBoolField(sConfigPath, sKey, bValue):
    """Update a nested features.<key> bool in a YAML file."""
    import yaml
    with open(sConfigPath, "r") as fileHandle:
        dictConfig = yaml.safe_load(fileHandle) or {}
    dictConfig.setdefault("features", {})
    dictConfig["features"][sKey] = bValue
    with open(sConfigPath, "w") as fileHandle:
        yaml.safe_dump(
            dictConfig, fileHandle,
            default_flow_style=False, sort_keys=False,
        )


def _fnUpdateYamlBoolField(sConfigPath, sKey, bValue):
    """Update or append a top-level boolean key in a YAML file."""
    with open(sConfigPath, "r") as fileHandle:
        listLines = fileHandle.readlines()
    sValue = "true" if bValue else "false"
    bFound = False
    for iIndex, sLine in enumerate(listLines):
        if sLine.startswith(f"{sKey}:") or sLine.startswith(
            f"{sKey} :"
        ):
            listLines[iIndex] = f"{sKey}: {sValue}\n"
            bFound = True
            break
    if not bFound:
        if listLines and not listLines[-1].endswith("\n"):
            listLines[-1] += "\n"
        listLines.append(f"{sKey}: {sValue}\n")
    with open(sConfigPath, "w") as fileHandle:
        fileHandle.writelines(listLines)


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
        sAbsPath = sPath or os.path.expanduser("~")
        _fnValidateHostPath(sAbsPath)
        listEntries = flistQueryHostDirectory(sAbsPath)
        return {
            "sCurrentPath": sAbsPath,
            "bHasConfig": fbDirectoryHasConfig(sAbsPath),
            "listEntries": listEntries,
        }


def _fnRegisterCreateHostDirectory(app, dictCtx):
    """Register POST /api/host-directories/create."""

    @app.post("/api/host-directories/create")
    async def fnCreateHostDirectory(request: CreateHostDirectoryRequest):
        _fnValidateHostPath(request.sParentPath)
        _fnValidateFolderName(request.sFolderName)
        sNewPath = _fnCreateHostFolder(
            request.sParentPath, request.sFolderName,
        )
        return {"sNewPath": sNewPath}


def _fnValidateFolderName(sFolderName):
    """Raise HTTPException if folder name is unsafe or malformed."""
    sStripped = (sFolderName or "").strip()
    if not sStripped:
        raise HTTPException(400, "Folder name is required")
    if "/" in sStripped or "\\" in sStripped:
        raise HTTPException(400, "Folder name cannot contain slashes")
    if sStripped in (".", ".."):
        raise HTTPException(400, "Invalid folder name")
    if sStripped.startswith("."):
        raise HTTPException(400, "Folder name cannot start with a dot")
    if not _RE_FOLDER_NAME.match(sStripped):
        raise HTTPException(400, "Invalid folder name")


def _fnCreateHostFolder(sParentPath, sFolderName):
    """Create a new directory under sParentPath; return the new path."""
    sNewPath = os.path.join(sParentPath, sFolderName.strip())
    if os.path.exists(sNewPath):
        raise HTTPException(409, "Directory already exists")
    try:
        os.makedirs(sNewPath, exist_ok=False)
    except (PermissionError, OSError):
        raise HTTPException(403, "Cannot create directory")
    return sNewPath


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


def _fnRegisterGetTemplates(app, dictCtx):
    """Register GET /api/setup/templates."""

    @app.get("/api/setup/templates")
    async def fnGetTemplates():
        from vaibify.config.templateManager import (
            flistAvailableTemplates,
        )
        try:
            listTemplates = flistAvailableTemplates()
        except FileNotFoundError as error:
            raise HTTPException(404, str(error))
        return {"listTemplates": listTemplates}


def _fnRegisterGetTemplateConfig(app, dictCtx):
    """Register GET /api/setup/templates/{sName}."""

    @app.get("/api/setup/templates/{sName}")
    async def fnGetTemplateConfig(sName: str):
        from vaibify.config.templateManager import (
            fdictLoadTemplateConfig,
        )
        try:
            dictConfig = fdictLoadTemplateConfig(sName)
        except FileNotFoundError as error:
            raise HTTPException(404, str(error))
        return dictConfig


def _fnRegisterCreateProject(app, dictCtx):
    """Register POST /api/projects/create."""

    @app.post("/api/projects/create")
    async def fnCreateProject(request: CreateProjectRequest):
        _fnValidateCreateDirectory(request.sDirectory)
        _fnRejectDuplicateProjectName(request.sProjectName)
        _fnScaffoldProject(request)
        _fnWriteProjectConfig(request)
        _fnRegisterNewProject(request.sDirectory)
        return {"bSuccess": True, "sDirectory": request.sDirectory}


def _fnValidateCreateDirectory(sDirectory):
    """Validate directory path for project creation."""
    if not os.path.isabs(sDirectory):
        raise HTTPException(400, "Directory must be an absolute path")
    sHome = os.path.expanduser("~")
    sResolved = os.path.realpath(sDirectory)
    if sResolved != sHome and not sResolved.startswith(sHome + os.sep):
        raise HTTPException(403, "Path is outside allowed root")


def _fnRejectDuplicateProjectName(sProjectName):
    """Raise 409 if project name conflicts with registry or Docker."""
    from vaibify.config.registryManager import flistGetAllProjects
    for dictProject in flistGetAllProjects():
        if dictProject["sName"] == sProjectName:
            raise HTTPException(
                409,
                f"A project named '{sProjectName}' is already "
                f"registered at {dictProject['sDirectory']}",
            )
    if _fbDockerContainerExists(sProjectName):
        raise HTTPException(
            409,
            f"A Docker container named '{sProjectName}' already "
            f"exists on this host",
        )


def _fbDockerContainerExists(sContainerName):
    """Return True if a Docker container with this name exists."""
    import subprocess
    try:
        resultProcess = subprocess.run(
            ["docker", "ps", "-a", "--format", "{{.Names}}",
             "--filter", f"name=^{sContainerName}$"],
            capture_output=True, text=True, timeout=5,
        )
        return sContainerName in resultProcess.stdout.split()
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


def _fnScaffoldProject(request):
    """Create directory and copy template files."""
    from vaibify.config.templateManager import fnCopyTemplate
    try:
        fnCopyTemplate(request.sTemplateName, request.sDirectory)
    except FileNotFoundError as error:
        raise HTTPException(404, str(error))


def _fnWriteProjectConfig(request):
    """Write vaibify.yml with project settings."""
    from vaibify.config.projectConfig import (
        fconfigFromYamlDict,
        fnSaveToFile,
    )
    sConfigPath = os.path.join(request.sDirectory, "vaibify.yml")
    dictYaml = _fdictBuildYamlFromRequest(request)
    configProject = fconfigFromYamlDict(dictYaml)
    fnSaveToFile(configProject, sConfigPath)


def _fdictBuildYamlFromRequest(request):
    """Translate a CreateProjectRequest into a camelCase YAML dict."""
    dictFeatures = _fdictFeaturesFromList(request.listFeatures)
    dictFeatures["claudeAutoUpdate"] = request.bClaudeAutoUpdate
    dictYaml = {
        "projectName": request.sProjectName,
        "containerUser": request.sContainerUser,
        "pythonVersion": request.sPythonVersion,
        "baseImage": request.sBaseImage,
        "workspaceRoot": request.sWorkspaceRoot,
        "packageManager": request.sPackageManager,
        "repositories": _flistRepositoriesFromUrls(
            request.listRepositories
        ),
        "features": dictFeatures,
        "secrets": _flistSecretsFromAuthFlag(request.bUseGithubAuth),
        "neverSleep": request.bNeverSleep,
        "networkIsolation": request.bNetworkIsolation,
    }
    _fnAttachOptionalPackages(dictYaml, request)
    return dictYaml


def _fnAttachOptionalPackages(dictYaml, request):
    """Attach package and pip-flag fields when present."""
    if request.listSystemPackages:
        dictYaml["systemPackages"] = list(request.listSystemPackages)
    if request.listPythonPackages:
        dictYaml["pythonPackages"] = list(request.listPythonPackages)
    if request.listCondaPackages:
        dictYaml["condaPackages"] = list(request.listCondaPackages)
    if request.sPipInstallFlags:
        dictYaml["pipInstallFlags"] = request.sPipInstallFlags


_LIST_FEATURE_NAMES = [
    "jupyter", "rLanguage", "julia", "database",
    "dvc", "latex", "claude", "gpu",
]


def _fdictFeaturesFromList(listFeatures):
    """Convert a feature-name list into the boolean YAML dict."""
    setEnabled = set(listFeatures or [])
    return {sName: sName in setEnabled
            for sName in _LIST_FEATURE_NAMES}


def _flistSecretsFromAuthFlag(bUseGithubAuth):
    """Return secret entries that match the GitHub auth toggle."""
    if not bUseGithubAuth:
        return []
    return [{"name": "gh_token", "method": "gh_auth"}]


def _flistRepositoriesFromUrls(listUrls):
    """Convert a list of git URL strings to vaibify.yml repository dicts."""
    listRepositories = []
    for sUrl in listUrls:
        listRepositories.append({
            "name": _fsRepositoryNameFromUrl(sUrl),
            "url": sUrl,
            "branch": "main",
            "installMethod": "pip_editable",
        })
    return listRepositories


def _fsRepositoryNameFromUrl(sUrl):
    """Extract a repository name from a git URL."""
    sName = sUrl.rstrip("/").rsplit("/", 1)[-1]
    if sName.endswith(".git"):
        sName = sName[:-4]
    return sName


def _fnRegisterNewProject(sDirectory):
    """Register the newly created project."""
    from vaibify.config.registryManager import fnAddProject
    try:
        fnAddProject(sDirectory)
    except ValueError as error:
        raise HTTPException(409, str(error))
