"""FastAPI application with REST and WebSocket routes for workflow viewing."""

import asyncio
import json
import logging
import os
import posixpath
import re
import secrets

logger = logging.getLogger("vaibify")

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from starlette.middleware.base import BaseHTTPMiddleware
from typing import List, Optional

WORKSPACE_ROOT = "/workspace"

from . import workflowManager
from .figureServer import fsMimeTypeForFile
from .pipelineRunner import (
    fnRunAllSteps,
    fnRunFromStep,
    fnRunSelectedSteps,
    fnVerifyOnly,
    fsShellQuote,
)
from .resourceMonitor import fdictGetContainerStats
from .terminalSession import TerminalSession


STATIC_DIRECTORY = os.path.join(os.path.dirname(__file__), "static")

sTerminalUser = None


class StepCreateRequest(BaseModel):
    sName: str
    sDirectory: str
    bPlotOnly: bool = True
    bInteractive: bool = False
    saDataCommands: List[str] = []
    saDataFiles: List[str] = []
    saTestCommands: List[str] = []
    saPlotCommands: List[str] = []
    saPlotFiles: List[str] = []


class StepUpdateRequest(BaseModel):
    sName: Optional[str] = None
    sDirectory: Optional[str] = None
    bPlotOnly: Optional[bool] = None
    bInteractive: Optional[bool] = None
    bEnabled: Optional[bool] = None
    saDataCommands: Optional[List[str]] = None
    saDataFiles: Optional[List[str]] = None
    saTestCommands: Optional[List[str]] = None
    saPlotCommands: Optional[List[str]] = None
    saPlotFiles: Optional[List[str]] = None
    saDependencies: Optional[List[str]] = None
    dictVerification: Optional[dict] = None
    dictPlotFileCategories: Optional[dict] = None
    dictDataFileCategories: Optional[dict] = None


class ReorderRequest(BaseModel):
    iFromIndex: int
    iToIndex: int


class WorkflowSettingsRequest(BaseModel):
    sPlotDirectory: Optional[str] = None
    sFigureType: Optional[str] = None
    iNumberOfCores: Optional[int] = None
    fTolerance: Optional[float] = None


class RunRequest(BaseModel):
    listStepIndices: List[int] = []
    iStartStep: Optional[int] = None


class FileWriteRequest(BaseModel):
    sContent: str


class DependencyScanRequest(BaseModel):
    saDataCommands: List[str] = []


class TestGenerateRequest(BaseModel):
    bUseApi: bool = False
    sApiKey: Optional[str] = None
    bDeterministic: bool = True


def fnValidatePathWithinRoot(sResolvedPath, sAllowedRoot):
    """Raise 403 if sResolvedPath escapes sAllowedRoot via traversal."""
    sNormalized = posixpath.normpath(sResolvedPath)
    sRoot = posixpath.normpath(sAllowedRoot)
    if not sNormalized.startswith(sRoot + "/") and sNormalized != sRoot:
        raise HTTPException(
            403, "Path traversal is not permitted"
        )
    return sNormalized


def fdictExtractSettings(dictWorkflow):
    """Return the settings subset from a workflow dict."""
    return {
        "sPlotDirectory": dictWorkflow.get("sPlotDirectory", "Plot"),
        "sFigureType": dictWorkflow.get("sFigureType", "pdf"),
        "iNumberOfCores": dictWorkflow.get("iNumberOfCores", -1),
        "fTolerance": dictWorkflow.get("fTolerance", 1e-6),
    }


def fdictFilterNonNone(dictSource):
    """Return a dict with only the non-None values."""
    return {k: v for k, v in dictSource.items() if v is not None}


def fdictStepFromRequest(request):
    """Build a step dict from a StepCreateRequest."""
    return workflowManager.fdictCreateStep(
        sName=request.sName,
        sDirectory=request.sDirectory,
        bPlotOnly=request.bPlotOnly,
        bInteractive=request.bInteractive,
        saDataCommands=request.saDataCommands,
        saDataFiles=request.saDataFiles,
        saTestCommands=request.saTestCommands,
        saPlotCommands=request.saPlotCommands,
        saPlotFiles=request.saPlotFiles,
    )


def fdictRequireWorkflow(dictWorkflowCache, sContainerId):
    """Return cached workflow or raise 404."""
    dictWorkflow = dictWorkflowCache.get(sContainerId)
    if not dictWorkflow:
        raise HTTPException(404, "Not connected to container")
    return dictWorkflow


def fsResolveWorkflowPath(connectionDocker, sContainerId, sWorkflowPath):
    """Resolve workflow path via discovery if not provided."""
    if sWorkflowPath is not None:
        return sWorkflowPath
    listWorkflows = workflowManager.flistFindWorkflowsInContainer(
        connectionDocker, sContainerId
    )
    return listWorkflows[0]["sPath"] if listWorkflows else None


def fsResolveFigurePath(sWorkflowDirectory, sFilePath):
    """Return absolute path for a figure file."""
    if sFilePath.startswith("/"):
        return sFilePath
    if sFilePath.startswith("workspace/"):
        return "/" + sFilePath
    return posixpath.join(sWorkflowDirectory, sFilePath)


def fbaFetchFigureWithFallback(
    connectionDocker, sContainerId, sAbsPath,
    sWorkflowDirectory, sWorkdir, sFilePath,
):
    """Try primary path, then fallback with sWorkdir prefix."""
    try:
        return connectionDocker.fbaFetchFile(sContainerId, sAbsPath)
    except Exception:
        pass
    if sWorkdir and not sFilePath.startswith("/"):
        return _fbaFetchFallback(
            connectionDocker, sContainerId,
            sWorkflowDirectory, sWorkdir, sFilePath,
        )
    raise HTTPException(404, "Figure not found")


def _fbaFetchFallback(
    connectionDocker, sContainerId,
    sWorkflowDirectory, sWorkdir, sFilePath,
):
    """Attempt to fetch figure from workdir-relative path."""
    if sWorkdir.startswith("/"):
        sFallback = posixpath.join(sWorkdir, sFilePath)
    else:
        sFallback = posixpath.join(
            sWorkflowDirectory, sWorkdir, sFilePath)
    try:
        return connectionDocker.fbaFetchFile(sContainerId, sFallback)
    except Exception as error:
        raise HTTPException(
            404, f"Figure not found: "
            f"{_fsSanitizeServerError(str(error))}")


def flistQueryDirectory(connectionDocker, sContainerId, sAbsPath):
    """List files and directories in a single Docker exec call."""
    sCommand = (
        f"find {fsShellQuote(sAbsPath)} -maxdepth 1 -mindepth 1 "
        f"-printf '%y %p\\n' 2>/dev/null | sort -k2"
    )
    _, sOutput = connectionDocker.ftResultExecuteCommand(
        sContainerId, sCommand
    )
    return _flistParseDirectoryOutput(sOutput)


def _flistParseDirectoryOutput(sOutput):
    """Parse find -printf '%y %p' output into entry dicts."""
    listEntries = []
    for sLine in sOutput.splitlines():
        sLine = sLine.strip()
        if not sLine or len(sLine) < 3:
            continue
        sType = sLine[0]
        sPath = sLine[2:]
        listEntries.append({
            "sName": posixpath.basename(sPath),
            "sPath": sPath,
            "bIsDirectory": sType == "d",
        })
    return listEntries


async def _fnDispatchRunFrom(
    connectionDocker, sContainerId, dictRequest,
    sWorkflowDirectory, fnCallback, dictInteractive=None,
):
    """Dispatch runFrom with the start step from the request."""
    await fnRunFromStep(
        connectionDocker, sContainerId,
        dictRequest.get("iStartStep", 1),
        sWorkflowDirectory, fnCallback,
        dictInteractive=dictInteractive,
    )


async def fnDispatchAction(
    sAction, dictRequest, connectionDocker,
    sContainerId, dictWorkflow, dictWorkflowPathCache,
    sWorkflowDirectory, fnCallback, dictInteractive=None,
):
    """Route a WebSocket pipeline action to the correct runner."""
    if sAction == "runAll":
        await fnRunAllSteps(
            connectionDocker, sContainerId, sWorkflowDirectory,
            fnCallback, dictInteractive=dictInteractive)
    elif sAction == "forceRunAll":
        await fnRunAllSteps(
            connectionDocker, sContainerId, sWorkflowDirectory,
            fnCallback, bForceRun=True,
            dictInteractive=dictInteractive)
    elif sAction == "runFrom":
        await _fnDispatchRunFrom(
            connectionDocker, sContainerId, dictRequest,
            sWorkflowDirectory, fnCallback,
            dictInteractive=dictInteractive)
    elif sAction == "verify":
        await fnVerifyOnly(
            connectionDocker, sContainerId, sWorkflowDirectory, fnCallback)
    elif sAction == "runSelected":
        await _fnDispatchSelected(
            connectionDocker, sContainerId, dictRequest,
            dictWorkflow, dictWorkflowPathCache,
            sWorkflowDirectory, fnCallback)


async def _fnDispatchSelected(
    connectionDocker, sContainerId, dictRequest,
    dictWorkflow, dictWorkflowPathCache,
    sWorkflowDirectory, fnCallback,
):
    """Dispatch the runSelected action."""
    await fnRunSelectedSteps(
        connectionDocker, sContainerId,
        dictRequest.get("listStepIndices", []),
        dictWorkflow, dictWorkflowPathCache.get(sContainerId),
        sWorkflowDirectory, fnCallback,
    )


async def fnTerminalReadLoop(session, websocket):
    """Continuously read terminal output and send to WebSocket."""
    while session._bRunning:
        try:
            baOutput = session.fbaReadOutput()
            if baOutput:
                await websocket.send_bytes(baOutput)
            else:
                await asyncio.sleep(0.05)
        except Exception:
            break


async def fnTerminalInputLoop(session, websocket):
    """Receive WebSocket messages and route to terminal session."""
    while True:
        message = await websocket.receive()
        if message.get("type") == "websocket.disconnect":
            break
        if "bytes" in message:
            session.fnSendInput(message["bytes"])
        elif "text" in message:
            _fnHandleTerminalText(session, message["text"])


def _fnHandleTerminalText(session, sText):
    """Parse a JSON text message and handle resize or kill."""
    try:
        dictData = json.loads(sText)
    except (json.JSONDecodeError, ValueError):
        return
    if dictData.get("sType") == "resize":
        iRows = max(1, min(500, int(dictData.get("iRows", 24))))
        iColumns = max(1, min(1000, int(dictData.get("iColumns", 80))))
        session.fnResize(iRows, iColumns)
    elif dictData.get("sType") == "kill":
        session.fnKillForeground()


async def fnRejectTerminalStart(websocket, error):
    """Send error and close WebSocket when terminal start fails."""
    await websocket.send_json(
        {"sType": "error", "sMessage": f"Terminal failed: {error}"}
    )
    await websocket.close()


async def fnRejectNotConnected(websocket):
    """Send not-connected error and close WebSocket."""
    await websocket.send_json(
        {"sType": "error", "sMessage": "Not connected"}
    )
    await websocket.close()


async def fnPipelineMessageLoop(
    websocket, connectionDocker, sContainerId,
    dictWorkflow, dictWorkflowPathCache, sWorkflowDirectory,
):
    """Receive and dispatch pipeline WebSocket messages.

    Pipeline actions run as background tasks so the loop stays
    alive to receive interactive resume/skip messages.
    """
    from .pipelineRunner import (
        fdictCreateInteractiveContext,
        fnSetInteractiveResponse,
    )
    dictInteractive = fdictCreateInteractiveContext()
    taskPipeline = None

    async def fnCallback(dictEvent):
        await websocket.send_json(dictEvent)

    while True:
        dictRequest = json.loads(await websocket.receive_text())
        sAction = dictRequest.get("sAction", "")
        if sAction in ("interactiveResume", "interactiveSkip"):
            _fnHandleInteractiveResponse(
                dictInteractive, sAction,
                dictRequest,
            )
            continue
        if sAction == "interactiveComplete":
            _fnHandleInteractiveComplete(
                dictInteractive, dictRequest,
            )
            continue
        taskPipeline = asyncio.create_task(
            fnDispatchAction(
                sAction, dictRequest, connectionDocker,
                sContainerId, dictWorkflow,
                dictWorkflowPathCache, sWorkflowDirectory,
                fnCallback, dictInteractive=dictInteractive,
            )
        )


def _fnHandleInteractiveResponse(
    dictInteractive, sAction, dictRequest,
):
    """Set the resume/skip response on the interactive context."""
    if sAction == "interactiveResume":
        fnSetInteractiveResponse(dictInteractive, "resume")
    elif sAction == "interactiveSkip":
        fnSetInteractiveResponse(dictInteractive, "skip")


def _fnHandleInteractiveComplete(dictInteractive, dictRequest):
    """Signal that the interactive terminal command finished."""
    iExitCode = dictRequest.get("iExitCode", 0)
    fnSetInteractiveResponse(
        dictInteractive, f"complete:{iExitCode}",
    )


async def fnRunTerminalSession(
    session, websocket, dictTerminalSessions,
):
    """Manage terminal session lifecycle after successful start."""
    sSessionId = session.sSessionId
    dictTerminalSessions[sSessionId] = session
    await websocket.send_json(
        {"sType": "connected", "sSessionId": sSessionId}
    )
    taskReader = asyncio.create_task(
        fnTerminalReadLoop(session, websocket)
    )
    try:
        await fnTerminalInputLoop(session, websocket)
    except WebSocketDisconnect:
        pass
    finally:
        session.fnClose()
        taskReader.cancel()
        dictTerminalSessions.pop(sSessionId, None)


def _fsSanitizeServerError(sRawError):
    """Return a user-friendly error message, log the raw error."""
    logger.error("Raw Docker/server error: %s", sRawError)
    if "no space left on device" in sRawError.lower():
        return "Docker disk full. Run: docker image prune -f"
    if "no such container" in sRawError.lower():
        return "Container not found. It may have stopped."
    if "connection refused" in sRawError.lower():
        return "Cannot connect to Docker. Is it running?"
    if "permission denied" in sRawError.lower():
        return "Permission denied. Check Docker access."
    if len(sRawError) > 500:
        return sRawError[:500] + "..."
    return sRawError


def _fsResolveContainerUser(dictCtx, sContainerId):
    """Query the container for its built-in user.

    Reads the CONTAINER_USER environment variable set during
    ``docker build``. Falls back to ``"researcher"`` if the
    variable is not found.
    """
    try:
        iExitCode, sOutput = dictCtx["docker"].ftResultExecuteCommand(
            sContainerId, "printenv CONTAINER_USER",
        )
        if iExitCode == 0 and sOutput.strip():
            return sOutput.strip()
    except Exception:
        pass
    return "researcher"


def _fnAuthorizeContainer(dictCtx, sContainerId):
    """Authorize the container and cache its user."""
    dictCtx["setAllowedContainers"].add(sContainerId)
    dictCtx["containerUsers"][sContainerId] = (
        _fsResolveContainerUser(dictCtx, sContainerId)
    )


def _fdictConnectNoWorkflow(dictCtx, sContainerId):
    """Return response for no-workflow mode."""
    _fnAuthorizeContainer(dictCtx, sContainerId)
    return {
        "sContainerId": sContainerId,
        "sWorkflowPath": None,
        "dictWorkflow": None,
    }


def fdictHandleConnect(dictCtx, sContainerId, sWorkflowPath):
    """Load workflow, cache it, return connection response."""
    if sWorkflowPath is None:
        return _fdictConnectNoWorkflow(dictCtx, sContainerId)
    try:
        dictWorkflow = workflowManager.fdictLoadWorkflowFromContainer(
            dictCtx["docker"], sContainerId, sWorkflowPath
        )
        dictCtx["workflows"][sContainerId] = dictWorkflow
        _fnAuthorizeContainer(dictCtx, sContainerId)
        sResolved = fsResolveWorkflowPath(
            dictCtx["docker"], sContainerId, sWorkflowPath
        )
        dictCtx["paths"][sContainerId] = sResolved
        return {
            "sContainerId": sContainerId,
            "sWorkflowPath": sResolved,
            "dictWorkflow": dictWorkflow,
        }
    except Exception as error:
        logger.error("Workflow load failed: %s", error)
        sUserMessage = _fsSanitizeServerError(str(error))
        raise HTTPException(400, sUserMessage)


def _fnRegisterWorkflowSearch(app, dictCtx):
    """Register GET /api/workflows route."""

    @app.get("/api/workflows/{sContainerId}")
    async def fnFindWorkflows(sContainerId: str):
        dictCtx["require"]()
        try:
            return workflowManager.flistFindWorkflowsInContainer(
                dictCtx["docker"], sContainerId
            )
        except Exception as error:
            raise HTTPException(
                500, f"Search failed: "
                f"{_fsSanitizeServerError(str(error))}")


class CreateWorkflowRequest(BaseModel):
    sWorkflowName: str
    sFileName: str
    sRepoDirectory: str


def _fnRejectDuplicateWorkflowName(
    connectionDocker, sContainerId, sWorkflowName
):
    """Raise 409 if another workflow in the container uses this name."""
    listExisting = workflowManager.flistFindWorkflowsInContainer(
        connectionDocker, sContainerId
    )
    for dictWorkflow in listExisting:
        if dictWorkflow["sName"] == sWorkflowName:
            raise HTTPException(
                409,
                f"A workflow named '{sWorkflowName}' already exists "
                f"at {dictWorkflow['sPath']}",
            )


def _fsValidateRepoDirectory(
    connectionDocker, sContainerId, sRepoDirectory
):
    """Validate the repo directory exists under /workspace/."""
    sClean = sRepoDirectory.strip().strip("/")
    if not sClean:
        raise HTTPException(
            400, "sRepoDirectory is required"
        )
    if ".." in sClean.split("/"):
        raise HTTPException(
            400, "sRepoDirectory may not contain '..'"
        )
    sFullPath = posixpath.join("/workspace", sClean)
    iExitCode, _ = connectionDocker.ftResultExecuteCommand(
        sContainerId, f"test -d {fsShellQuote(sFullPath)}"
    )
    if iExitCode != 0:
        raise HTTPException(
            404, f"Repo directory not found: {sFullPath}"
        )
    return sFullPath


def _fnRegisterRepoList(app, dictCtx):
    """Register GET /api/repos/{id} to list top-level repo directories."""

    @app.get("/api/repos/{sContainerId}")
    async def fnListRepos(sContainerId: str):
        dictCtx["require"]()
        sCommand = (
            "find /workspace -mindepth 1 -maxdepth 1 -type d "
            "-not -name '.*' -printf '%f\\n' 2>/dev/null"
        )
        iExitCode, sOutput = dictCtx["docker"].ftResultExecuteCommand(
            sContainerId, sCommand
        )
        if iExitCode != 0:
            raise HTTPException(500, "Failed to list repos")
        listRepos = sorted([
            sLine.strip() for sLine in sOutput.splitlines()
            if sLine.strip()
        ])
        return {"listRepos": listRepos}


def _fnRegisterWorkflowCreate(app, dictCtx):
    """Register POST /api/workflows/{id}/create route."""

    @app.post("/api/workflows/{sContainerId}/create")
    async def fnCreateWorkflow(
        sContainerId: str, request: CreateWorkflowRequest
    ):
        dictCtx["require"]()
        _fnRejectDuplicateWorkflowName(
            dictCtx["docker"], sContainerId, request.sWorkflowName
        )
        sRepoDirectory = _fsValidateRepoDirectory(
            dictCtx["docker"], sContainerId, request.sRepoDirectory
        )
        sFileName = request.sFileName.strip()
        if not sFileName.endswith(".json"):
            sFileName += ".json"
        dictBlank = {
            "sWorkflowName": request.sWorkflowName,
            "sPlotDirectory": "Plot",
            "sFigureType": "pdf",
            "iNumberOfCores": -1,
            "listSteps": [],
        }
        sContent = json.dumps(dictBlank, indent=2) + "\n"
        sWorkflowDir = posixpath.join(
            sRepoDirectory, workflowManager.VAIBIFY_WORKFLOWS_DIR
        )
        dictCtx["docker"].ftResultExecuteCommand(
            sContainerId, f"mkdir -p {fsShellQuote(sWorkflowDir)}"
        )
        sFullPath = posixpath.join(sWorkflowDir, sFileName)
        dictCtx["docker"].fnWriteFile(
            sContainerId, sFullPath, sContent.encode("utf-8")
        )
        return {
            "sPath": sFullPath,
            "sName": request.sWorkflowName,
            "sSource": "vaibify",
        }


def _fnRegisterConnect(app, dictCtx):
    """Register POST /api/connect route."""

    @app.post("/api/connect/{sContainerId}")
    async def fnConnect(
        sContainerId: str, sWorkflowPath: Optional[str] = None
    ):
        dictCtx["require"]()
        return fdictHandleConnect(dictCtx, sContainerId, sWorkflowPath)


def _fnRegisterFiles(app, dictCtx, sWorkspaceRoot):
    """Register GET /api/files route."""

    @app.get("/api/files/{sContainerId}/{sDirectoryPath:path}")
    async def fnListDirectory(sContainerId: str, sDirectoryPath: str):
        import asyncio
        dictCtx["require"]()
        sAbsPath = f"/{sDirectoryPath}" if not sDirectoryPath.startswith("/") else sDirectoryPath
        fnValidatePathWithinRoot(sAbsPath, sWorkspaceRoot)
        return await asyncio.to_thread(
            flistQueryDirectory,
            dictCtx["docker"], sContainerId, sAbsPath
        )


class FileUploadRequest(BaseModel):
    sFilename: str
    sDestination: str = "/workspace"
    sContentBase64: str


class FilePullRequest(BaseModel):
    sContainerPath: str
    sHostDestination: str


def _fnRegisterFileUpload(app, dictCtx, sWorkspaceRoot):
    """Register POST /api/files/{id}/upload for host-to-container transfer."""
    import base64

    @app.post("/api/files/{sContainerId}/upload")
    async def fnUploadFile(
        sContainerId: str, request: FileUploadRequest,
    ):
        import asyncio
        dictCtx["require"]()
        sSafeFilename = posixpath.basename(request.sFilename)
        sDestPath = posixpath.join(
            request.sDestination, sSafeFilename)
        fnValidatePathWithinRoot(sDestPath, sWorkspaceRoot)
        try:
            baContent = base64.b64decode(request.sContentBase64)
            await asyncio.to_thread(
                dictCtx["docker"].fnWriteFile,
                sContainerId, sDestPath, baContent,
            )
        except Exception as error:
            raise HTTPException(
                status_code=500, detail=str(error))
        return {"bSuccess": True, "sPath": sDestPath}


def _fnRegisterFileDownload(app, dictCtx, sWorkspaceRoot):
    """Register GET /api/files/{id}/download for container-to-host transfer."""

    @app.get("/api/files/{sContainerId}/download/{sFilePath:path}")
    async def fnDownloadFile(sContainerId: str, sFilePath: str):
        import asyncio
        dictCtx["require"]()
        sAbsPath = fsResolveFigurePath(
            dictCtx["workflowDir"](sContainerId), sFilePath
        )
        fnValidatePathWithinRoot(sAbsPath, sWorkspaceRoot)
        try:
            baContent = await asyncio.to_thread(
                dictCtx["docker"].fbaFetchFile,
                sContainerId, sAbsPath,
            )
        except Exception as error:
            raise HTTPException(
                status_code=500, detail=str(error))
        sFilename = posixpath.basename(sAbsPath)
        sMimeType = fsMimeTypeForFile(sAbsPath)
        return Response(
            content=baContent,
            media_type=sMimeType,
            headers={
                "Content-Disposition":
                    f'attachment; filename="{sFilename}"',
            },
        )


def _fnRegisterFilePull(app, dictCtx, sWorkspaceRoot):
    """Register POST /api/files/{id}/pull for container-to-host copy."""

    @app.post("/api/files/{sContainerId}/pull")
    async def fnPullFile(
        sContainerId: str, request: FilePullRequest,
    ):
        import asyncio
        dictCtx["require"]()
        fnValidatePathWithinRoot(
            request.sContainerPath, sWorkspaceRoot)
        sHostDest = os.path.realpath(
            os.path.expanduser(request.sHostDestination))
        _fnValidateHostDestination(sHostDest)
        try:
            await asyncio.to_thread(
                _fnDockerCopy, sContainerId,
                request.sContainerPath, sHostDest,
            )
        except Exception as error:
            raise HTTPException(
                status_code=500, detail=str(error))
        return {"bSuccess": True, "sHostPath": sHostDest}


def _fnValidateHostDestination(sResolvedPath):
    """Raise 403 if the destination escapes the user's home directory."""
    sHome = os.path.expanduser("~")
    if sResolvedPath != sHome and not sResolvedPath.startswith(
            sHome + os.sep):
        raise HTTPException(
            403, "Destination outside home directory")


def _fnDockerCopy(sContainerId, sContainerPath, sHostDest):
    """Run docker cp to copy from container to host."""
    import subprocess
    sSource = f"{sContainerId}:{sContainerPath}"
    subprocess.run(
        ["docker", "cp", sSource, sHostDest],
        check=True, capture_output=True,
    )


def _fnRegisterMonitor(app):
    """Register GET /api/monitor route."""

    @app.get("/api/monitor/{sContainerId}")
    async def fnGetMonitorStats(sContainerId: str):
        return fdictGetContainerStats(sContainerId)


class SyncPushRequest(BaseModel):
    listFilePaths: List[str]
    sCommitMessage: str = "[vaibify] Update outputs"


class GitAddFileRequest(BaseModel):
    sFilePath: str
    sCommitMessage: str = "[vaibify] Add data file"


class SyncSetupRequest(BaseModel):
    sService: str
    sProjectId: Optional[str] = None
    sToken: Optional[str] = None


def _fdictBuildOverleafArgs(dictWorkflow):
    """Extract Overleaf push arguments from workflow settings."""
    return {
        "sProjectId": dictWorkflow.get("sOverleafProjectId", ""),
        "sTargetDirectory": dictWorkflow.get(
            "sOverleafFigureDirectory", "figures"),
        "dictWorkflow": dictWorkflow,
        "sGithubBaseUrl": dictWorkflow.get("sGithubBaseUrl", ""),
        "sDoi": dictWorkflow.get("sZenodoDoi", ""),
        "sTexFilename": dictWorkflow.get("sTexFilename", "main.tex"),
    }


def _fnRegisterOverleafPush(app, dictCtx):
    """Register POST /api/overleaf/{id}/push endpoint."""
    from . import syncDispatcher

    @app.post("/api/overleaf/{sContainerId}/push")
    async def fnOverleafPush(
        sContainerId: str, request: SyncPushRequest,
    ):
        dictCtx["require"]()
        dictWorkflow = fdictRequireWorkflow(
            dictCtx["workflows"], sContainerId)
        dictOverleafArgs = _fdictBuildOverleafArgs(dictWorkflow)
        iExit, sOut = await asyncio.to_thread(
            syncDispatcher.ftResultPushToOverleaf,
            dictCtx["docker"], sContainerId,
            request.listFilePaths, **dictOverleafArgs,
        )
        dictResult = syncDispatcher.fdictSyncResult(iExit, sOut)
        if not dictResult["bSuccess"]:
            return dictResult
        workflowManager.fnUpdateSyncStatus(
            dictWorkflow, request.listFilePaths, "Overleaf")
        dictCtx["save"](sContainerId, dictWorkflow)
        return dictResult


def _fnRegisterZenodoArchive(app, dictCtx):
    """Register POST /api/zenodo/{id}/archive endpoint."""
    from . import syncDispatcher

    @app.post("/api/zenodo/{sContainerId}/archive")
    async def fnZenodoArchive(
        sContainerId: str, request: SyncPushRequest,
    ):
        dictCtx["require"]()
        dictWorkflow = fdictRequireWorkflow(
            dictCtx["workflows"], sContainerId)
        iExit, sOut = await asyncio.to_thread(
            syncDispatcher.ftResultArchiveToZenodo,
            dictCtx["docker"], sContainerId,
            "zenodo", request.listFilePaths,
        )
        dictResult = syncDispatcher.fdictSyncResult(iExit, sOut)
        if not dictResult["bSuccess"]:
            return dictResult
        workflowManager.fnUpdateSyncStatus(
            dictWorkflow, request.listFilePaths, "Zenodo")
        dictCtx["save"](sContainerId, dictWorkflow)
        return dictResult


def _fnRegisterGithubPush(app, dictCtx):
    """Register POST /api/github/{id}/push endpoint."""
    from . import syncDispatcher

    @app.post("/api/github/{sContainerId}/push")
    async def fnGithubPush(
        sContainerId: str, request: SyncPushRequest,
    ):
        dictCtx["require"]()
        dictWorkflow = fdictRequireWorkflow(
            dictCtx["workflows"], sContainerId)
        sWorkdir = posixpath.dirname(
            dictCtx["paths"].get(sContainerId, ""))
        iExit, sOut = await asyncio.to_thread(
            syncDispatcher.ftResultPushToGithub,
            dictCtx["docker"], sContainerId,
            request.listFilePaths, request.sCommitMessage,
            sWorkdir,
        )
        dictResult = syncDispatcher.fdictSyncResult(iExit, sOut)
        if not dictResult["bSuccess"]:
            return dictResult
        sCommitHash = sOut.strip().splitlines()[-1] if sOut else ""
        workflowManager.fnUpdateSyncStatus(
            dictWorkflow, request.listFilePaths, "Github")
        _fnStoreCommitHash(
            dictWorkflow, request.listFilePaths, sCommitHash)
        dictCtx["save"](sContainerId, dictWorkflow)
        dictResult["sCommitHash"] = sCommitHash
        return dictResult


def _fnRegisterGithubAddFile(app, dictCtx):
    """Register POST /api/github/{id}/add-file endpoint."""
    from . import syncDispatcher

    @app.post("/api/github/{sContainerId}/add-file")
    async def fnGithubAddFile(
        sContainerId: str, request: GitAddFileRequest,
    ):
        dictCtx["require"]()
        dictWorkflow = fdictRequireWorkflow(
            dictCtx["workflows"], sContainerId)
        sWorkdir = posixpath.dirname(
            dictCtx["paths"].get(sContainerId, ""))
        fnValidatePathWithinRoot(
            posixpath.normpath(
                posixpath.join(sWorkdir, request.sFilePath)
            ),
            WORKSPACE_ROOT,
        )
        iExit, sOut = await asyncio.to_thread(
            syncDispatcher.ftResultAddFileToGithub,
            dictCtx["docker"], sContainerId,
            request.sFilePath, request.sCommitMessage,
            sWorkdir,
        )
        dictResult = syncDispatcher.fdictSyncResult(iExit, sOut)
        if dictResult["bSuccess"]:
            sHash = sOut.strip().splitlines()[-1] if sOut else ""
            dictResult["sCommitHash"] = sHash
        return dictResult


def _fnRegisterScriptRoutes(app, dictCtx):
    """Register script listing and scanning routes."""

    @app.get("/api/sync/{sContainerId}/scripts")
    async def fnGetScripts(sContainerId: str):
        dictCtx["require"]()
        dictWorkflow = fdictRequireWorkflow(
            dictCtx["workflows"], sContainerId)
        dictDirMap = workflowManager.fdictBuildStepDirectoryMap(
            dictWorkflow)
        listGroups = []
        for iStep, dictStep in enumerate(
            dictWorkflow.get("listSteps", [])
        ):
            listScripts = (
                workflowManager.flistExtractStepScripts(dictStep)
            )
            if listScripts:
                listGroups.append({
                    "sStepName": dictStep.get("sName", ""),
                    "sCamelCaseDir": dictDirMap.get(iStep, ""),
                    "listScripts": listScripts,
                })
        return listGroups

    @app.post("/api/steps/{sContainerId}/{iStepIndex}/scan-scripts")
    async def fnScanScripts(sContainerId: str, iStepIndex: int):
        dictCtx["require"]()
        dictWorkflow = fdictRequireWorkflow(
            dictCtx["workflows"], sContainerId)
        dictStep = dictWorkflow["listSteps"][iStepIndex]
        sDirectory = dictStep.get("sDirectory", "")
        iExit, sOutput = await asyncio.to_thread(
            dictCtx["docker"].ftResultExecuteCommand,
            sContainerId,
            f"find {fsShellQuote(sDirectory)} -maxdepth 1"
            f" -name '*.py' "
            f"-printf '%f\\n' 2>/dev/null || "
            f"ls {fsShellQuote(sDirectory)}/*.py 2>/dev/null"
            f" | xargs -n1 basename 2>/dev/null",
        )
        listFiles = [
            s.strip() for s in sOutput.strip().splitlines()
            if s.strip()
        ] if iExit == 0 and sOutput.strip() else []
        return workflowManager.fdictAutoDetectScripts(listFiles)

    @app.post(
        "/api/steps/{sContainerId}/{iStepIndex}/scan-dependencies"
    )
    async def fnScanDependencies(
        sContainerId: str,
        iStepIndex: int,
        request: DependencyScanRequest,
    ):
        dictCtx["require"]()
        dictWorkflow = fdictRequireWorkflow(
            dictCtx["workflows"], sContainerId)
        return await _fdictScanDependencies(
            dictCtx, sContainerId, iStepIndex,
            request.saDataCommands, dictWorkflow,
        )


async def _fdictScanDependencies(
    dictCtx, sContainerId, iStepIndex,
    saDataCommands, dictWorkflow,
):
    """Scan commands for file loads and cross-reference upstream outputs."""
    dictStep = dictWorkflow.get("listSteps", [{}])[iStepIndex] \
        if iStepIndex < len(dictWorkflow.get("listSteps", [])) else {}
    sStepDirectory = dictStep.get("sDirectory", "")
    listAllDetected = await _flistDetectLoadsInCommands(
        dictCtx, sContainerId, saDataCommands, sStepDirectory,
    )
    dictResult = _fdictCrossReferenceFiles(
        listAllDetected, dictWorkflow, iStepIndex
    )
    if not dictResult.get("listSuggestions") and iStepIndex > 0:
        dictResult["listUpstreamOutputs"] = _flistCollectUpstreamOutputs(
            dictWorkflow, iStepIndex,
        )
    return dictResult


async def _flistDetectLoadsInCommands(
    dictCtx, sContainerId, saDataCommands, sStepDirectory,
):
    """Scan each command's script for file-load calls."""
    from .commandUtilities import ftExtractScriptPathForLanguage
    from .dependencyScanner import flistScanForLoadCalls, fsDetectLanguage

    listAllDetected = []
    for sCommand in saDataCommands:
        listDetected = await _flistDetectLoadsInOneCommand(
            dictCtx, sContainerId, sCommand, sStepDirectory,
        )
        listAllDetected.extend(listDetected)
    return listAllDetected


async def _flistDetectLoadsInOneCommand(
    dictCtx, sContainerId, sCommand, sStepDirectory,
):
    """Return detected load calls from a single command's script."""
    from .commandUtilities import ftExtractScriptPathForLanguage
    from .dependencyScanner import flistScanForLoadCalls, fsDetectLanguage

    sScriptPath, sLanguage = ftExtractScriptPathForLanguage(sCommand)
    if not sScriptPath:
        return []
    sAbsScriptPath = _fsJoinStepPath(sStepDirectory, sScriptPath)
    sSourceCode = await _fsReadContainerFile(
        dictCtx, sContainerId, sAbsScriptPath,
    )
    if sSourceCode is None:
        return []
    sLanguage = _fsResolveLanguage(
        sLanguage, sScriptPath, sCommand, sSourceCode,
    )
    if sLanguage == "unknown":
        return []
    listDetected = flistScanForLoadCalls(sSourceCode, sLanguage)
    for dictItem in listDetected:
        dictItem["sFoundInScript"] = sScriptPath
    return listDetected


def _fsResolveLanguage(sLanguage, sScriptPath, sCommand, sSourceCode):
    """Detect language from source if not already known."""
    from .dependencyScanner import fsDetectLanguage
    if sLanguage != "unknown":
        return sLanguage
    sFirstLine = sSourceCode.split("\n", 1)[0]
    return fsDetectLanguage(sScriptPath, sCommand, sFirstLine)


def _flistCollectUpstreamOutputs(dictWorkflow, iStepIndex):
    """Collect all saDataFiles entries from steps preceding iStepIndex."""
    listUpstream = []
    listSteps = dictWorkflow.get("listSteps", [])
    for iIndex in range(min(iStepIndex, len(listSteps))):
        dictStep = listSteps[iIndex]
        sStepName = dictStep.get("sName", f"Step {iIndex + 1}")
        iStepNumber = iIndex + 1
        for sFileName in dictStep.get("saDataFiles", []):
            sStem = os.path.splitext(os.path.basename(sFileName))[0]
            sTemplateVariable = "{" + f"Step{iStepNumber:02d}.{sStem}" + "}"
            listUpstream.append({
                "sFileName": sFileName,
                "sSourceStepName": sStepName,
                "iSourceStep": iStepNumber,
                "sTemplateVariable": sTemplateVariable,
            })
    return listUpstream


def _fsJoinStepPath(sStepDirectory, sScriptPath):
    """Join a step directory with a script path when the path is relative."""
    if os.path.isabs(sScriptPath) or not sStepDirectory:
        return sScriptPath
    return os.path.join(sStepDirectory, sScriptPath)


async def _fsReadContainerFile(dictCtx, sContainerId, sFilePath):
    """Fetch a file from the container and return its contents as a string."""
    try:
        baContent = await asyncio.to_thread(
            dictCtx["docker"].fbaFetchFile,
            sContainerId, sFilePath,
        )
        return baContent.decode("utf-8")
    except Exception:
        return None


def _fsetCollectCurrentStepOutputs(dictWorkflow, iCurrentStep):
    """Return basenames of data and plot files produced by the current step."""
    listSteps = dictWorkflow.get("listSteps", [])
    if iCurrentStep >= len(listSteps):
        return set()
    dictStep = listSteps[iCurrentStep]
    setOutputs = set()
    for sFile in dictStep.get("saDataFiles", []):
        setOutputs.add(os.path.basename(sFile))
    for sFile in dictStep.get("saPlotFiles", []):
        setOutputs.add(os.path.basename(sFile))
    return setOutputs


def _flistFilterOwnOutputs(listDetected, setOwnOutputs):
    """Remove detected files whose basename matches a current step output."""
    listFiltered = []
    for dictItem in listDetected:
        sBasename = os.path.basename(dictItem["sFileName"])
        if sBasename not in setOwnOutputs:
            listFiltered.append(dictItem)
    return listFiltered


def _fdictCrossReferenceFiles(listDetected, dictWorkflow, iCurrentStep):
    """Match detected filenames against upstream step outputs."""
    dictStemRegistry = workflowManager.fdictBuildStemRegistry(dictWorkflow)
    setOwnOutputs = _fsetCollectCurrentStepOutputs(
        dictWorkflow, iCurrentStep,
    )
    listDetected = _flistFilterOwnOutputs(listDetected, setOwnOutputs)
    listSuggestions = []
    listUnmatchedFiles = []
    for dictItem in listDetected:
        _fnClassifyDetectedItem(
            dictItem, dictStemRegistry, dictWorkflow,
            iCurrentStep, listSuggestions, listUnmatchedFiles,
        )
    return {
        "listSuggestions": listSuggestions,
        "listUnmatchedFiles": listUnmatchedFiles,
    }


def _fnClassifyDetectedItem(
    dictItem, dictStemRegistry, dictWorkflow,
    iCurrentStep, listSuggestions, listUnmatchedFiles,
):
    """Sort a detected file reference into suggestions or unmatched."""
    sFileName = dictItem["sFileName"]
    sStem = os.path.splitext(os.path.basename(sFileName))[0]
    dictMatch = _fdictFindStemMatch(
        sStem, dictStemRegistry, dictWorkflow, iCurrentStep
    )
    if dictMatch:
        dictMatch.update({
            "sFileName": sFileName,
            "sFoundInScript": dictItem.get("sFoundInScript", ""),
            "sLoadFunction": dictItem["sLoadFunction"],
            "iLineNumber": dictItem["iLineNumber"],
        })
        listSuggestions.append(dictMatch)
    else:
        listUnmatchedFiles.append({
            "sFileName": sFileName,
            "sLoadFunction": dictItem["sLoadFunction"],
            "iLineNumber": dictItem["iLineNumber"],
            "sFoundInScript": dictItem.get("sFoundInScript", ""),
        })


def _fdictFindStemMatch(
    sStem, dictStemRegistry, dictWorkflow, iCurrentStep,
):
    """Return match dict if sStem maps to an upstream step output."""
    for sKey, iStepNumber in dictStemRegistry.items():
        iStepIndex = iStepNumber - 1
        if iStepIndex >= iCurrentStep:
            continue
        sRegistryStem = sKey.split(".", 1)[1] if "." in sKey else ""
        if sRegistryStem == sStem:
            sStepName = dictWorkflow["listSteps"][iStepIndex].get(
                "sName", f"Step {iStepNumber}"
            )
            return {
                "iSourceStep": iStepNumber,
                "sSourceStepName": sStepName,
                "sTemplateVariable": "{" + sKey + "}",
                "sResolvedPath": sKey,
            }
    return None


def _fnStoreCommitHash(dictWorkflow, listFilePaths, sCommitHash):
    """Store the git commit hash in sync status for each file."""
    dictSync = dictWorkflow.get("dictSyncStatus", {})
    for sPath in listFilePaths:
        if sPath in dictSync:
            dictSync[sPath]["sGithubCommit"] = sCommitHash


def _fnRegisterSyncRoutes(app, dictCtx):
    """Register sync status, file list, setup, and check routes."""
    from . import syncDispatcher

    @app.get("/api/sync/{sContainerId}/status")
    async def fnGetSyncStatus(sContainerId: str):
        dictWorkflow = fdictRequireWorkflow(
            dictCtx["workflows"], sContainerId)
        return workflowManager.fdictGetSyncStatus(dictWorkflow)

    @app.get("/api/sync/{sContainerId}/files")
    async def fnGetSyncFiles(sContainerId: str):
        dictWorkflow = fdictRequireWorkflow(
            dictCtx["workflows"], sContainerId)
        dictSync = workflowManager.fdictGetSyncStatus(dictWorkflow)
        return syncDispatcher.flistCollectOutputFiles(
            dictWorkflow, dictSync)

    @app.post("/api/sync/{sContainerId}/setup")
    async def fnSetupConnection(
        sContainerId: str, request: SyncSetupRequest,
    ):
        dictCtx["require"]()
        syncDispatcher.fnValidateServiceName(request.sService)
        if request.sToken:
            sTokenName = f"{request.sService}_token"
            try:
                syncDispatcher.fnStoreCredentialInContainer(
                    dictCtx["docker"], sContainerId,
                    sTokenName, request.sToken,
                )
            except Exception as error:
                return {
                    "bConnected": False,
                    "sMessage": f"Failed to store credentials: {error}",
                }
        dictResult = syncDispatcher.fdictCheckConnectivity(
            dictCtx["docker"], sContainerId, request.sService)
        if dictResult["bConnected"] and request.sService == "zenodo":
            bValid = await asyncio.to_thread(
                syncDispatcher.fbValidateZenodoToken,
                dictCtx["docker"], sContainerId,
            )
            if not bValid:
                dictResult["bConnected"] = False
                dictResult["sMessage"] = (
                    "Token stored but validation failed. "
                    "Check that the token has deposit scopes."
                )
        return dictResult

    @app.get("/api/sync/{sContainerId}/check/{sService}")
    async def fnCheckConnection(
        sContainerId: str, sService: str,
    ):
        dictCtx["require"]()
        syncDispatcher.fnValidateServiceName(sService)
        return syncDispatcher.fdictCheckConnectivity(
            dictCtx["docker"], sContainerId, sService)


def _fnRegisterDag(app, dictCtx):
    """Register DAG visualization endpoint."""
    from . import syncDispatcher

    @app.get("/api/workflow/{sContainerId}/dag")
    async def fnGetDag(sContainerId: str):
        dictCtx["require"]()
        dictWorkflow = fdictRequireWorkflow(
            dictCtx["workflows"], sContainerId
        )
        iExit, result = await asyncio.to_thread(
            syncDispatcher.ftResultGenerateDagSvg,
            dictCtx["docker"], sContainerId, dictWorkflow,
        )
        if iExit != 0:
            raise HTTPException(500, f"DAG failed: {result}")
        return Response(content=result, media_type="image/svg+xml")


class DatasetDownloadRequest(BaseModel):
    iRecordId: int
    sFileName: str
    sDestination: str


def _fnRegisterDatasetDownload(app, dictCtx):
    """Register Zenodo dataset download endpoint."""
    from . import syncDispatcher

    @app.post("/api/zenodo/{sContainerId}/download")
    async def fnDownloadDataset(
        sContainerId: str, request: DatasetDownloadRequest,
    ):
        dictCtx["require"]()
        iExit, sOut = await asyncio.to_thread(
            syncDispatcher.ftResultDownloadDataset,
            dictCtx["docker"], sContainerId,
            "zenodo", request.iRecordId,
            request.sFileName, request.sDestination,
        )
        if iExit != 0:
            raise HTTPException(500, f"Download failed: {sOut}")
        return {"bSuccess": True}


def _fnRegisterReproEndpoints(app, dictCtx):
    """Register all reproducibility and sync endpoints."""
    _fnRegisterOverleafPush(app, dictCtx)
    _fnRegisterZenodoArchive(app, dictCtx)
    _fnRegisterGithubPush(app, dictCtx)
    _fnRegisterGithubAddFile(app, dictCtx)
    _fnRegisterScriptRoutes(app, dictCtx)
    _fnRegisterSyncRoutes(app, dictCtx)


def _fnRegisterFileWrite(app, dictCtx, sWorkspaceRoot):
    """Register PUT /api/file route for saving edited text files."""

    @app.put("/api/file/{sContainerId}/{sFilePath:path}")
    async def fnWriteFile(
        sContainerId: str, sFilePath: str,
        request: FileWriteRequest, sWorkdir: str = "",
    ):
        dictCtx["require"]()
        sAbsPath = fsResolveFigurePath(
            dictCtx["workflowDir"](sContainerId), sFilePath
        )
        fnValidatePathWithinRoot(sAbsPath, sWorkspaceRoot)
        baContent = request.sContent.encode("utf-8")
        try:
            dictCtx["docker"].fnWriteFile(
                sContainerId, sAbsPath, baContent
            )
        except Exception as error:
            raise HTTPException(
                500, f"Write failed: {_fsSanitizeServerError(str(error))}"
            )
        return {"bSuccess": True, "sPath": sAbsPath}


def _fnRegisterLogRoutes(app, dictCtx):
    """Register log listing and fetching routes."""

    @app.get("/api/logs/{sContainerId}")
    async def fnListLogs(sContainerId: str):
        dictCtx["require"]()
        sLogsDir = posixpath.join(
            WORKSPACE_ROOT, workflowManager.VAIBIFY_LOGS_DIR
        )
        listEntries = flistQueryDirectory(
            dictCtx["docker"], sContainerId, sLogsDir
        )
        listLogs = [
            e["sName"] for e in listEntries
            if e["sName"].endswith(".log")
        ]
        return sorted(listLogs, reverse=True)

    @app.get("/api/logs/{sContainerId}/{sLogFilename}")
    async def fnGetLogContent(sContainerId: str, sLogFilename: str):
        dictCtx["require"]()
        sLogsDir = posixpath.join(
            WORKSPACE_ROOT, workflowManager.VAIBIFY_LOGS_DIR
        )
        sLogPath = posixpath.join(sLogsDir, sLogFilename)
        fnValidatePathWithinRoot(sLogPath, sLogsDir)
        try:
            baContent = dictCtx["docker"].fbaFetchFile(
                sContainerId, sLogPath
            )
            return Response(
                content=baContent, media_type="text/plain"
            )
        except Exception as error:
            raise HTTPException(
                404, f"Log not found: "
                f"{_fsSanitizeServerError(str(error))}")


def _fnRegisterSettingsGet(app, dictCtx):
    """Register GET /api/settings route."""

    @app.get("/api/settings/{sContainerId}")
    async def fnGetSettings(sContainerId: str):
        return fdictExtractSettings(
            fdictRequireWorkflow(dictCtx["workflows"], sContainerId)
        )


def _fnRegisterSettingsPut(app, dictCtx):
    """Register PUT /api/settings route."""

    @app.put("/api/settings/{sContainerId}")
    async def fnUpdateSettings(
        sContainerId: str, request: WorkflowSettingsRequest
    ):
        dictCtx["require"]()
        dictWorkflow = fdictRequireWorkflow(dictCtx["workflows"], sContainerId)
        for sKey, value in fdictFilterNonNone(
            request.model_dump()
        ).items():
            dictWorkflow[sKey] = value
        dictCtx["save"](sContainerId, dictWorkflow)
        return fdictExtractSettings(dictWorkflow)


def _fnRegisterStepsList(app, dictCtx):
    """Register GET /api/steps and validate routes."""

    @app.get("/api/steps/{sContainerId}")
    async def fnGetSteps(sContainerId: str):
        return workflowManager.flistExtractStepNames(
            fdictRequireWorkflow(dictCtx["workflows"], sContainerId)
        )

    @app.get("/api/steps/{sContainerId}/validate")
    async def fnValidateReferences(sContainerId: str):
        dictWorkflow = fdictRequireWorkflow(dictCtx["workflows"], sContainerId)
        return {
            "listWarnings": workflowManager.flistValidateReferences(
                dictWorkflow
            )
        }


def _fnRegisterStepGet(app, dictCtx):
    """Register GET /api/steps/{id}/{index} route."""

    @app.get("/api/steps/{sContainerId}/{iStepIndex}")
    async def fnGetStep(sContainerId: str, iStepIndex: int):
        dictWorkflow = fdictRequireWorkflow(dictCtx["workflows"], sContainerId)
        try:
            dictStep = workflowManager.fdictGetStep(
                dictWorkflow, iStepIndex
            )
            dictStep["saResolvedOutputFiles"] = (
                workflowManager.flistResolveOutputFiles(
                    dictStep, dictCtx["variables"](sContainerId)
                )
            )
            return dictStep
        except IndexError as error:
            raise HTTPException(404, str(error))


def _fnRegisterStepCreate(app, dictCtx):
    """Register POST /api/steps/{id}/create route."""

    @app.post("/api/steps/{sContainerId}/create")
    async def fnCreateStep(
        sContainerId: str, request: StepCreateRequest
    ):
        dictCtx["require"]()
        dictWorkflow = fdictRequireWorkflow(dictCtx["workflows"], sContainerId)
        dictStep = fdictStepFromRequest(request)
        dictWorkflow["listSteps"].append(dictStep)
        dictCtx["save"](sContainerId, dictWorkflow)
        return {
            "iIndex": len(dictWorkflow["listSteps"]) - 1,
            "dictStep": dictStep,
        }


def _fnRegisterStepInsert(app, dictCtx):
    """Register POST /api/steps/{id}/insert route."""

    @app.post("/api/steps/{sContainerId}/insert/{iPosition}")
    async def fnInsertStep(
        sContainerId: str, iPosition: int,
        request: StepCreateRequest,
    ):
        dictCtx["require"]()
        dictWorkflow = fdictRequireWorkflow(dictCtx["workflows"], sContainerId)
        dictStep = fdictStepFromRequest(request)
        workflowManager.fnInsertStep(dictWorkflow, iPosition, dictStep)
        dictCtx["save"](sContainerId, dictWorkflow)
        return {
            "iIndex": iPosition,
            "dictStep": dictStep,
            "listSteps": dictWorkflow["listSteps"],
        }


def _fnRegisterStepUpdate(app, dictCtx):
    """Register PUT /api/steps/{id}/{index} route."""

    @app.put("/api/steps/{sContainerId}/{iStepIndex}")
    async def fnUpdateStep(
        sContainerId: str, iStepIndex: int,
        request: StepUpdateRequest,
    ):
        dictCtx["require"]()
        dictWorkflow = fdictRequireWorkflow(dictCtx["workflows"], sContainerId)
        try:
            workflowManager.fnUpdateStep(
                dictWorkflow, iStepIndex,
                fdictFilterNonNone(request.model_dump()),
            )
        except IndexError as error:
            raise HTTPException(404, str(error))
        dictCtx["save"](sContainerId, dictWorkflow)
        return dictWorkflow["listSteps"][iStepIndex]


def _fnRegisterStepDelete(app, dictCtx):
    """Register DELETE /api/steps/{id}/{index} route."""

    @app.delete("/api/steps/{sContainerId}/{iStepIndex}")
    async def fnDeleteStep(sContainerId: str, iStepIndex: int):
        dictCtx["require"]()
        dictWorkflow = fdictRequireWorkflow(dictCtx["workflows"], sContainerId)
        try:
            workflowManager.fnDeleteStep(dictWorkflow, iStepIndex)
        except IndexError as error:
            raise HTTPException(404, str(error))
        dictCtx["save"](sContainerId, dictWorkflow)
        return {"bSuccess": True, "listSteps": dictWorkflow["listSteps"]}


def _fnRegisterStepReorder(app, dictCtx):
    """Register POST /api/steps/{id}/reorder route."""

    @app.post("/api/steps/{sContainerId}/reorder")
    async def fnReorderSteps(
        sContainerId: str, request: ReorderRequest
    ):
        dictCtx["require"]()
        dictWorkflow = fdictRequireWorkflow(dictCtx["workflows"], sContainerId)
        try:
            workflowManager.fnReorderStep(
                dictWorkflow, request.iFromIndex, request.iToIndex
            )
        except IndexError as error:
            raise HTTPException(400, str(error))
        dictCtx["save"](sContainerId, dictWorkflow)
        return {"listSteps": dictWorkflow["listSteps"]}


def _flistBuildFigureCheckPaths(sAbsPath, sWorkdir, sDir, sFilePath):
    """Build list of paths to check for figure existence."""
    listPaths = [sAbsPath]
    if sWorkdir and not sFilePath.startswith("/"):
        if sWorkdir.startswith("/"):
            listPaths.append(posixpath.join(sWorkdir, sFilePath))
        else:
            listPaths.append(posixpath.join(sDir, sWorkdir, sFilePath))
    return listPaths


def _fnRegisterFigure(app, dictCtx):
    """Register GET and HEAD /api/figure routes."""

    @app.head("/api/figure/{sContainerId}/{sFilePath:path}")
    async def fnCheckFigure(
        sContainerId: str, sFilePath: str, sWorkdir: str = ""
    ):
        import asyncio
        dictCtx["require"]()
        sDir = dictCtx["workflowDir"](sContainerId)
        sAbsPath = fsResolveFigurePath(sDir, sFilePath)
        fnValidatePathWithinRoot(sAbsPath, WORKSPACE_ROOT)
        listPaths = _flistBuildFigureCheckPaths(
            sAbsPath, sWorkdir, sDir, sFilePath,
        )
        sTestCmd = " || ".join(
            f"test -f {fsShellQuote(p)}" for p in listPaths)
        iExitCode, _ = await asyncio.to_thread(
            dictCtx["docker"].ftResultExecuteCommand,
            sContainerId, sTestCmd,
        )
        if iExitCode == 0:
            return Response(status_code=200)
        raise HTTPException(404, "Not found")

    @app.get("/api/figure/{sContainerId}/{sFilePath:path}")
    async def fnServeFigure(
        sContainerId: str, sFilePath: str, sWorkdir: str = ""
    ):
        import asyncio
        dictCtx["require"]()
        sDir = dictCtx["workflowDir"](sContainerId)
        sAbsPath = fsResolveFigurePath(sDir, sFilePath)
        fnValidatePathWithinRoot(sAbsPath, WORKSPACE_ROOT)
        baContent = await asyncio.to_thread(
            fbaFetchFigureWithFallback,
            dictCtx["docker"], sContainerId, sAbsPath,
            sDir, sWorkdir, sFilePath,
        )
        return Response(
            content=baContent,
            media_type=fsMimeTypeForFile(sAbsPath),
        )


async def fnHandlePipelineWs(websocket, dictCtx, sContainerId):
    """Accept and run the pipeline WebSocket session."""
    await websocket.accept()
    dictWorkflow = dictCtx["workflows"].get(sContainerId)
    if not dictWorkflow:
        await fnRejectNotConnected(websocket)
        return
    sDir = posixpath.dirname(dictCtx["paths"].get(sContainerId, ""))
    try:
        await fnPipelineMessageLoop(
            websocket, dictCtx["docker"], sContainerId,
            dictWorkflow, dictCtx["paths"], sDir,
        )
    except WebSocketDisconnect:
        pass


def _flistExtractKillPatterns(dictWorkflow):
    """Extract unique command patterns from workflow steps."""
    setPatterns = set()
    for dictStep in dictWorkflow.get("listSteps", []):
        for sKey in ("saDataCommands", "saPlotCommands"):
            for sCommand in dictStep.get(sKey, []):
                listTokens = sCommand.split()
                if not listTokens:
                    continue
                if listTokens[0] in ("python", "python3"):
                    if len(listTokens) > 1:
                        setPatterns.add(listTokens[1])
                elif listTokens[0] not in (
                    "cp", "cd", "echo", "rm", "mkdir",
                ):
                    setPatterns.add(listTokens[0])
    return sorted(setPatterns)


def _fnRegisterPipelineState(app, dictCtx):
    """Register GET /api/pipeline/{id}/state endpoint."""

    @app.get("/api/pipeline/{sContainerId}/state")
    async def fnGetPipelineState(sContainerId: str):
        import asyncio
        from .pipelineState import fdictReadState
        dictCtx["require"]()
        dictState = await asyncio.to_thread(
            fdictReadState, dictCtx["docker"], sContainerId
        )
        if dictState is None:
            return {"bRunning": False}
        return dictState


def _fnRegisterRuntimeInfo(app, dictCtx):
    """Register GET /api/runtime endpoint."""

    @app.get("/api/runtime")
    async def fnGetRuntimeInfo():
        import asyncio
        return await asyncio.to_thread(fsDetectDockerRuntime)


def _fbCaffeinateRunning():
    """Return True if a caffeinate process is active for this user."""
    import subprocess
    try:
        resultProcess = subprocess.run(
            ["pgrep", "-u", str(os.getuid()), "-x", "caffeinate"],
            capture_output=True, timeout=2,
        )
        return resultProcess.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


def _fdictSleepWarningForContext(sContext):
    """Return runtime info dict with appropriate sleep warning."""
    if _fbCaffeinateRunning():
        return {"sRuntime": sContext, "sSleepWarning": ""}
    sSleepDefault = (
        "Use 'caffeinate -s' to prevent macOS from "
        "sleeping during long pipeline runs."
    )
    if "colima" in sContext:
        return {"sRuntime": "colima", "sSleepWarning":
            "Your Docker runtime (Colima) does not "
            "sleep automatically. " + sSleepDefault}
    if "desktop" in sContext or "default" == sContext:
        return {"sRuntime": "desktop", "sSleepWarning":
            "Ensure Docker Desktop is configured to "
            "not sleep idle VMs (Settings > Resources "
            "> Advanced). Also consider running "
            "'caffeinate -s' to prevent macOS sleep."}
    if "orbstack" in sContext:
        return {"sRuntime": "orbstack", "sSleepWarning":
            "OrbStack VMs survive sleep. " + sSleepDefault}
    return {"sRuntime": sContext, "sSleepWarning": sSleepDefault}


def fsDetectDockerRuntime():
    """Detect the Docker runtime (colima, desktop, orbstack, etc.)."""
    import subprocess
    try:
        resultContext = subprocess.run(
            ["docker", "context", "ls", "--format",
             "{{.Name}}:{{.Current}}"],
            capture_output=True, text=True, timeout=5,
        )
        for sLine in resultContext.stdout.strip().split("\n"):
            if ":true" in sLine.lower():
                sContext = sLine.split(":")[0].strip().lower()
                return _fdictSleepWarningForContext(sContext)
    except Exception:
        pass
    return {"sRuntime": "unknown", "sSleepWarning":
        "Use 'caffeinate -s' to prevent your computer from "
        "sleeping during long pipeline runs."}


def _fnRegisterFileStatus(app, dictCtx):
    """Register GET /api/pipeline/{id}/file-status endpoint."""

    @app.get("/api/pipeline/{sContainerId}/file-status")
    async def fnGetFileStatus(sContainerId: str):
        import asyncio
        from . import syncDispatcher as _syncDispatcher
        dictCtx["require"]()
        dictWorkflow = fdictRequireWorkflow(
            dictCtx["workflows"], sContainerId)
        dictVars = dictCtx["variables"](sContainerId)
        listPaths = _flistCollectOutputPaths(
            dictWorkflow, dictVars)
        dictModTimes = await asyncio.to_thread(
            _fdictGetModTimes,
            dictCtx["docker"], sContainerId, listPaths,
        )
        listInvalidated = _flistDetectAndInvalidate(
            dictCtx, sContainerId, dictWorkflow,
            dictModTimes, dictVars,
        )
        dictCurrentHashes = await asyncio.to_thread(
            _syncDispatcher.fdictComputeAllScriptHashes,
            dictCtx["docker"], sContainerId, dictWorkflow,
        )
        dictScriptStatus = _fdictBuildScriptStatus(
            dictWorkflow, dictCurrentHashes,
        )
        return {
            "dictModTimes": dictModTimes,
            "dictInvalidatedSteps": listInvalidated,
            "dictScriptStatus": dictScriptStatus,
        }


def fdictCollectOutputPathsByStep(dictWorkflow, dictVars=None):
    """Return {iStepIndex: [resolved_paths]} for each step."""
    dictResult = {}
    if dictVars is None:
        dictVars = {
            "sPlotDirectory": dictWorkflow.get(
                "sPlotDirectory", "Plot"),
            "sFigureType": dictWorkflow.get("sFigureType", "pdf"),
        }
    for iIndex, dictStep in enumerate(
        dictWorkflow.get("listSteps", [])
    ):
        dictResult[iIndex] = _flistResolveStepPaths(
            dictStep, dictVars,
        )
    return dictResult


def _flistResolveStepPaths(dictStep, dictGlobalVars):
    """Return resolved output paths for a single step."""
    from .workflowManager import fsResolveVariables
    sStepDir = dictStep.get("sDirectory", "")
    listPaths = []
    for sFile in (dictStep.get("saDataFiles", [])
                  + dictStep.get("saPlotFiles", [])):
        sResolved = fsResolveVariables(sFile, dictGlobalVars)
        if not sResolved.startswith("/"):
            sResolved = posixpath.join(sStepDir, sResolved)
        listPaths.append(sResolved)
    return listPaths


def _flistCollectOutputPaths(dictWorkflow, dictVars=None):
    """Collect all resolved output file paths from the workflow."""
    dictByStep = fdictCollectOutputPathsByStep(dictWorkflow, dictVars)
    listPaths = []
    for iIndex in sorted(dictByStep.keys()):
        listPaths.extend(dictByStep[iIndex])
    return listPaths


def _fbPipelineIsRunning(dictCtx, sContainerId):
    """Return True if a pipeline is currently running in container."""
    from .pipelineState import fdictReadState
    dictState = fdictReadState(dictCtx["docker"], sContainerId)
    if dictState is None:
        return False
    return dictState.get("bRunning", False)


def _fdictFindChangedFiles(dictPathsByStep, dictOldModTimes,
                           dictNewModTimes):
    """Return {stepIndex: [changed file paths]} for files with new mtimes."""
    dictChanged = {}
    for iIndex, listPaths in dictPathsByStep.items():
        listChangedPaths = []
        for sPath in listPaths:
            sOldTime = dictOldModTimes.get(sPath)
            sNewTime = dictNewModTimes.get(sPath)
            if sNewTime and sNewTime != sOldTime:
                listChangedPaths.append(sPath)
        if listChangedPaths:
            dictChanged[iIndex] = listChangedPaths
    return dictChanged


def _fnInvalidateStepFiles(dictStep, listChangedPaths):
    """Mark specific files as modified, invalidate unit tests."""
    dictVerification = dictStep.get("dictVerification", {})
    if dictVerification.get("sUnitTest") == "passed":
        dictVerification["sUnitTest"] = "untested"
    if dictVerification.get("sPlotStandards") == "passed":
        listPlotFiles = dictStep.get("saPlotFiles", [])
        if _fbAnyPlotFileChanged(listChangedPaths, listPlotFiles):
            dictVerification["sPlotStandards"] = "stale"
    listExisting = dictVerification.get("listModifiedFiles", [])
    setModified = set(listExisting)
    setModified.update(listChangedPaths)
    dictVerification["listModifiedFiles"] = sorted(setModified)
    dictStep["dictVerification"] = dictVerification


def _fbAnyPlotFileChanged(listChangedPaths, listPlotFiles):
    """Return True if any changed path matches a plot file."""
    setPlotBasenames = set()
    for sPlotFile in listPlotFiles:
        setPlotBasenames.add(posixpath.basename(sPlotFile))
    for sChangedPath in listChangedPaths:
        sChangedBasename = posixpath.basename(sChangedPath)
        if sChangedBasename in setPlotBasenames:
            return True
    return False


def _fnInvalidateDownstreamStep(dictStep):
    """Mark a downstream step as affected by upstream changes."""
    dictVerification = dictStep.get("dictVerification", {})
    if dictVerification.get("sUnitTest") == "passed":
        dictVerification["sUnitTest"] = "untested"
    dictVerification["bUpstreamModified"] = True
    dictStep["dictVerification"] = dictVerification


def _fbStepScriptsModified(dictStep, dictCurrentHashes):
    """Return True if any script hash differs from stored hashes."""
    from .syncDispatcher import _fsNormalizePath
    from .commandUtilities import flistExtractScripts
    dictStoredHashes = dictStep.get(
        "dictRunStats", {}
    ).get("dictInputHashes", {})
    if not dictStoredHashes:
        return None
    sDirectory = dictStep.get("sDirectory", "")
    for sKey in ("saDataCommands", "saPlotCommands"):
        for sScript in flistExtractScripts(dictStep.get(sKey, [])):
            sPath = _fsNormalizePath(sDirectory, sScript)
            if dictStoredHashes.get(sPath) != dictCurrentHashes.get(sPath):
                return True
    return False


def _fdictBuildScriptStatus(dictWorkflow, dictCurrentHashes):
    """Compare current script hashes against stored run hashes."""
    dictResult = {}
    for iIndex, dictStep in enumerate(
        dictWorkflow.get("listSteps", [])
    ):
        bModified = _fbStepScriptsModified(dictStep, dictCurrentHashes)
        if bModified is None:
            dictResult[iIndex] = "unknown"
        elif bModified:
            dictResult[iIndex] = "modified"
        else:
            dictResult[iIndex] = "unchanged"
    return dictResult


def _fdictDetectChangedFiles(dictCtx, sContainerId,
                             dictWorkflow, dictNewModTimes,
                             dictVars=None):
    """Return changed files by step index, or empty if none changed."""
    if "dictPreviousModTimes" not in dictCtx:
        dictCtx["dictPreviousModTimes"] = {}
    dictPrevByContainer = dictCtx["dictPreviousModTimes"]
    dictOldModTimes = dictPrevByContainer.get(sContainerId, {})
    dictPrevByContainer[sContainerId] = dict(dictNewModTimes)
    if not dictOldModTimes:
        return {}
    if _fbPipelineIsRunning(dictCtx, sContainerId):
        return {}
    dictPathsByStep = fdictCollectOutputPathsByStep(
        dictWorkflow, dictVars)
    return _fdictFindChangedFiles(
        dictPathsByStep, dictOldModTimes, dictNewModTimes,
    )


def _fdictInvalidateAffectedSteps(dictWorkflow, dictChangedFiles):
    """Invalidate changed and downstream steps, return verification map."""
    dictDownstream = workflowManager.fdictBuildDownstreamMap(
        dictWorkflow)
    setDirectChanged = set(dictChangedFiles.keys())
    setDownstream = set()
    for iIndex in setDirectChanged:
        setDownstream |= dictDownstream.get(iIndex, set())
    setDownstream -= setDirectChanged
    listSteps = dictWorkflow.get("listSteps", [])
    for iIndex, listPaths in dictChangedFiles.items():
        if 0 <= iIndex < len(listSteps):
            _fnInvalidateStepFiles(listSteps[iIndex], listPaths)
    for iIndex in setDownstream:
        if 0 <= iIndex < len(listSteps):
            _fnInvalidateDownstreamStep(listSteps[iIndex])
    setAllAffected = setDirectChanged | setDownstream
    dictInvalidated = {}
    for iIndex in setAllAffected:
        if 0 <= iIndex < len(listSteps):
            dictInvalidated[iIndex] = listSteps[iIndex].get(
                "dictVerification", {})
    return dictInvalidated


def _flistDetectAndInvalidate(dictCtx, sContainerId,
                              dictWorkflow, dictNewModTimes,
                              dictVars=None):
    """Detect file changes and invalidate affected steps."""
    dictChangedFiles = _fdictDetectChangedFiles(
        dictCtx, sContainerId, dictWorkflow,
        dictNewModTimes, dictVars,
    )
    if not dictChangedFiles:
        return {}
    dictInvalidated = _fdictInvalidateAffectedSteps(
        dictWorkflow, dictChangedFiles)
    dictCtx["save"](sContainerId, dictWorkflow)
    return dictInvalidated


def _fdictGetModTimes(connectionDocker, sContainerId, listPaths):
    """Return {path: mtime_string} for each file that exists."""
    if not listPaths:
        return {}
    sPathArgs = " ".join(
        fsShellQuote(s) for s in listPaths[:200]
    )
    sCmd = f"stat -c '%n %Y' {sPathArgs} 2>/dev/null || true"
    iExitCode, sOutput = connectionDocker.ftResultExecuteCommand(
        sContainerId, sCmd
    )
    dictResult = {}
    for sLine in (sOutput or "").strip().split("\n"):
        sLine = sLine.strip()
        if not sLine:
            continue
        listParts = sLine.rsplit(" ", 1)
        if len(listParts) == 2:
            dictResult[listParts[0]] = listParts[1]
    return dictResult


async def _fiCountMatchingProcesses(connectionDocker, sContainerId,
                                     sGrepPattern):
    """Count processes matching the grep pattern in the container."""
    sCountCommand = (
        f"ps aux | grep -E '{sGrepPattern}' "
        f"| grep -v grep | wc -l"
    )
    _, sCountOutput = await asyncio.to_thread(
        connectionDocker.ftResultExecuteCommand,
        sContainerId, sCountCommand,
    )
    try:
        return int(sCountOutput.strip())
    except ValueError:
        return 0


async def _fnKillMatchingProcesses(connectionDocker, sContainerId,
                                   listPatterns):
    """Kill all processes matching the given patterns."""
    for sPattern in listPatterns:
        sBracket = "[" + sPattern[0] + "]" + sPattern[1:]
        sKill = (
            f"ps aux | grep '{sBracket}' "
            f"| awk '{{print $2}}' "
            f"| xargs kill -9 2>/dev/null"
        )
        await asyncio.to_thread(
            connectionDocker.ftResultExecuteCommand,
            sContainerId, sKill,
        )


def _fnRegisterPipelineKill(app, dictCtx):
    """Register POST /api/pipeline/{id}/kill endpoint."""

    @app.post("/api/pipeline/{sContainerId}/kill")
    async def fnKillRunningTasks(sContainerId: str):
        dictCtx["require"]()
        dictWorkflow = fdictRequireWorkflow(
            dictCtx["workflows"], sContainerId)
        listPatterns = _flistExtractKillPatterns(dictWorkflow)
        listSafe = [re.escape(s) for s in listPatterns]
        sGrepPattern = "|".join(listSafe) if listSafe else ""
        if not sGrepPattern:
            return {"bSuccess": True, "iProcessesKilled": 0}
        iCountBefore = await _fiCountMatchingProcesses(
            dictCtx["docker"], sContainerId, sGrepPattern)
        if iCountBefore > 0:
            await _fnKillMatchingProcesses(
                dictCtx["docker"], sContainerId, listPatterns)
        return {
            "bSuccess": True,
            "iProcessesKilled": iCountBefore,
        }


def _flistBuildCleanCommands(dictWorkflow):
    """Build rm commands for all output files and reset step stats."""
    listCleanCommands = []
    for dictStep in dictWorkflow.get("listSteps", []):
        if dictStep.get("bInteractive", False):
            continue
        sDir = dictStep.get("sDirectory", "")
        for sKey in ("saDataFiles", "saPlotFiles"):
            for sFile in dictStep.get(sKey, []):
                if sFile.startswith("{"):
                    continue
                sPath = sFile if sFile.startswith("/") else (
                    posixpath.join(sDir, sFile) if sDir
                    else sFile)
                listCleanCommands.append(
                    f"rm -f {fsShellQuote(sPath)} 2>/dev/null")
        dictStep["dictRunStats"] = {}
        dictStep["dictVerification"] = {
            "sUnitTest": "untested", "sUser": "untested"}
    return listCleanCommands


def _fnRegisterPipelineClean(app, dictCtx):
    """Register POST /api/pipeline/{id}/clean endpoint."""

    @app.post("/api/pipeline/{sContainerId}/clean")
    async def fnCleanOutputs(sContainerId: str):
        dictCtx["require"]()
        dictWorkflow = fdictRequireWorkflow(
            dictCtx["workflows"], sContainerId)
        listCleanCommands = _flistBuildCleanCommands(dictWorkflow)
        if listCleanCommands:
            sCommand = " ; ".join(listCleanCommands)
            await asyncio.to_thread(
                dictCtx["docker"].ftResultExecuteCommand,
                sContainerId, sCommand)
        dictCtx["save"](sContainerId, dictWorkflow)
        return {"bSuccess": True}


def _fnRegisterPipelineWs(app, dictCtx):
    """Register pipeline WebSocket endpoint."""

    @app.websocket("/ws/pipeline/{sContainerId}")
    async def fnPipelineWs(websocket: WebSocket, sContainerId: str):
        if not fbValidateWebSocketOrigin(websocket):
            await websocket.close(code=4003)
            return
        sToken = websocket.query_params.get("sToken", "")
        if sToken != dictCtx["sSessionToken"]:
            await websocket.close(code=4401)
            return
        if sContainerId not in dictCtx["setAllowedContainers"]:
            await websocket.close(code=4403)
            return
        dictCtx["require"]()
        await fnHandlePipelineWs(websocket, dictCtx, sContainerId)


def _fnRegisterTerminalWs(app, dictCtx):
    """Register terminal WebSocket endpoint."""

    @app.websocket("/ws/terminal/{sContainerId}")
    async def fnTerminalWs(websocket: WebSocket, sContainerId: str):
        if not fbValidateWebSocketOrigin(websocket):
            await websocket.close(code=4003)
            return
        sToken = websocket.query_params.get("sToken", "")
        if sToken != dictCtx["sSessionToken"]:
            await websocket.close(code=4401)
            return
        if sContainerId not in dictCtx["setAllowedContainers"]:
            await websocket.close(code=4403)
            return
        dictCtx["require"]()
        await websocket.accept()
        session = TerminalSession(
            dictCtx["docker"], sContainerId,
            sUser=dictCtx["containerUsers"].get(
                sContainerId, sTerminalUser
            ),
        )
        try:
            session.fnStart()
        except Exception as error:
            await fnRejectTerminalStart(websocket, error)
            return
        await fnRunTerminalSession(
            session, websocket, dictCtx["terminals"]
        )


def _fnRegisterUserInfo(app):
    """Register GET /api/user route."""

    @app.get("/api/user")
    async def fnGetUser():
        return {"sUserName": sTerminalUser or "User"}


def _fnRegisterStaticFiles(app, dictCtx):
    """Register index page, token endpoint, and static file mount."""

    @app.get("/")
    async def fnServeIndex():
        return FileResponse(
            os.path.join(STATIC_DIRECTORY, "index.html"),
            headers={"Cache-Control": "no-cache, no-store"},
        )

    @app.get("/api/session-token")
    async def fnGetSessionToken():
        return {"sToken": dictCtx["sSessionToken"]}

    if os.path.isdir(STATIC_DIRECTORY):
        app.mount(
            "/static",
            StaticFiles(directory=STATIC_DIRECTORY),
            name="static",
        )


def _fnRequireDocker(connectionDocker):
    """Raise 503 if Docker is unavailable."""
    if connectionDocker is None:
        raise HTTPException(503, "Docker support is not available")


def fsRequireWorkflowPath(dictPaths, sContainerId):
    """Return workflow path or raise 404."""
    sPath = dictPaths.get(sContainerId)
    if not sPath:
        raise HTTPException(404, "Not connected to container")
    return sPath


def fdictResolveVariables(dictWorkflows, dictPaths, sContainerId):
    """Build resolved variable dict for a container."""
    dictWorkflow = dictWorkflows.get(sContainerId)
    sPath = dictPaths.get(sContainerId)
    if not dictWorkflow or not sPath:
        return {}
    return workflowManager.fdictBuildGlobalVariables(dictWorkflow, sPath)


def _ftupleBuildHelpers(connectionDocker, dictWorkflows, dictPaths):
    """Build closure-based helper functions for the context."""

    def fnRequire():
        _fnRequireDocker(connectionDocker)

    def fnSave(sContainerId, dictWorkflow):
        sPath = fsRequireWorkflowPath(dictPaths, sContainerId)
        workflowManager.fnSaveWorkflowToContainer(
            connectionDocker, sContainerId, dictWorkflow, sPath)

    def fnVariables(sContainerId):
        return fdictResolveVariables(dictWorkflows, dictPaths, sContainerId)

    def fnWorkflowDir(sContainerId):
        sPath = dictPaths.get(sContainerId)
        if not sPath:
            return WORKSPACE_ROOT
        sWorkflowDirectory = posixpath.dirname(sPath)
        if "/.vaibify" in sWorkflowDirectory:
            return sWorkflowDirectory[
                :sWorkflowDirectory.index("/.vaibify")]
        return sWorkflowDirectory

    return fnRequire, fnSave, fnVariables, fnWorkflowDir


def fdictBuildContext(connectionDocker):
    """Build the shared context dict for route handlers."""
    dictWorkflows = {}
    dictPaths = {}
    dictTerminals = {}
    fnRequire, fnSave, fnVariables, fnWorkflowDir = _ftupleBuildHelpers(
        connectionDocker, dictWorkflows, dictPaths
    )
    return {
        "docker": connectionDocker,
        "workflows": dictWorkflows,
        "paths": dictPaths,
        "terminals": dictTerminals,
        "containerUsers": {},
        "require": fnRequire,
        "save": fnSave,
        "variables": fnVariables,
        "workflowDir": fnWorkflowDir,
    }


def _fconnectionCreateDocker():
    """Lazily create a DockerConnection or return None."""
    try:
        from ..docker.dockerConnection import DockerConnection
        return DockerConnection()
    except Exception:
        return None


def _fnRegisterCoreRoutes(app, dictCtx, sWorkspaceRoot):
    """Register workflow, file, monitor, and stub routes."""
    _fnRegisterWorkflowSearch(app, dictCtx)
    _fnRegisterRepoList(app, dictCtx)
    _fnRegisterWorkflowCreate(app, dictCtx)
    _fnRegisterConnect(app, dictCtx)
    _fnRegisterFileDownload(app, dictCtx, sWorkspaceRoot)
    _fnRegisterFilePull(app, dictCtx, sWorkspaceRoot)
    _fnRegisterFileUpload(app, dictCtx, sWorkspaceRoot)
    _fnRegisterFiles(app, dictCtx, sWorkspaceRoot)
    _fnRegisterFileWrite(app, dictCtx, sWorkspaceRoot)
    _fnRegisterMonitor(app)
    _fnRegisterReproEndpoints(app, dictCtx)
    _fnRegisterDag(app, dictCtx)
    _fnRegisterDatasetDownload(app, dictCtx)
    _fnRegisterLogRoutes(app, dictCtx)
    _fnRegisterSettingsGet(app, dictCtx)
    _fnRegisterSettingsPut(app, dictCtx)


async def _fdictRunTestGeneration(
    dictCtx, sContainerId, iStepIndex,
    dictWorkflow, fdictGenerate, request,
):
    """Invoke the test generator and return its result dict."""
    dictVars = dictCtx["variables"](sContainerId)
    sUser = dictCtx["containerUsers"].get(
        sContainerId, sTerminalUser
    )
    try:
        return await asyncio.to_thread(
            fdictGenerate,
            dictCtx["docker"], sContainerId, iStepIndex,
            dictWorkflow, dictVars,
            request.bUseApi, request.sApiKey,
            sUser=sUser,
            bDeterministic=request.bDeterministic,
        )
    except Exception as error:
        raise HTTPException(
            500, f"Generation failed: "
            f"{_fsSanitizeServerError(str(error))}")


def _fbNeedsClaudeFallback(dictCtx, sContainerId, request):
    """Return True if we need an LLM fallback and Claude is unavailable."""
    if request.bDeterministic or request.bUseApi:
        return False
    from .testGenerator import fbContainerHasClaude
    return not fbContainerHasClaude(dictCtx["docker"], sContainerId)


def _fnApplyGeneratedTests(
    dictCtx, sContainerId, dictWorkflow, iStepIndex, dictResult,
):
    """Store generated test categories in the step and save."""
    from .workflowManager import flistBuildTestCommands
    dictStep = dictWorkflow["listSteps"][iStepIndex]
    dictTests = dictStep.setdefault("dictTests", {})
    for sCategory in ("dictIntegrity", "dictQualitative", "dictQuantitative"):
        if sCategory in dictResult:
            dictTests[sCategory] = dictResult[sCategory]
    dictStep["saTestCommands"] = flistBuildTestCommands(dictStep)
    dictCtx["save"](sContainerId, dictWorkflow)


def _fdictBuildGenerateResponse(dictResult):
    """Build the HTTP response dict for test generation."""
    return {
        "bGenerated": True,
        "dictIntegrity": dictResult.get("dictIntegrity", {}),
        "dictQualitative": dictResult.get("dictQualitative", {}),
        "dictQuantitative": dictResult.get("dictQuantitative", {}),
    }


def _fnRegisterTestGenerate(app, dictCtx):
    """Register test generation and deletion routes."""

    @app.post("/api/steps/{sContainerId}/{iStepIndex}/generate-test")
    async def fnGenerateTest(
        sContainerId: str, iStepIndex: int,
        request: TestGenerateRequest,
    ):
        dictCtx["require"]()
        from .testGenerator import fbContainerHasClaude, fdictGenerateAllTests
        if _fbNeedsClaudeFallback(dictCtx, sContainerId, request):
            return {"bNeedsFallback": True}
        dictWorkflow = fdictRequireWorkflow(
            dictCtx["workflows"], sContainerId
        )
        dictResult = await _fdictRunTestGeneration(
            dictCtx, sContainerId, iStepIndex,
            dictWorkflow, fdictGenerateAllTests, request,
        )
        _fnApplyGeneratedTests(
            dictCtx, sContainerId, dictWorkflow, iStepIndex,
            dictResult,
        )
        return _fdictBuildGenerateResponse(dictResult)

    @app.delete(
        "/api/steps/{sContainerId}/{iStepIndex}/generated-test"
    )
    async def fnDeleteGeneratedTest(
        sContainerId: str, iStepIndex: int,
    ):
        dictCtx["require"]()
        dictWorkflow = fdictRequireWorkflow(
            dictCtx["workflows"], sContainerId
        )
        dictStep = dictWorkflow["listSteps"][iStepIndex]
        _fnRemoveTestDirectory(
            dictCtx["docker"], sContainerId, dictStep,
        )
        dictStep["dictTests"] = {
            "dictQualitative": {"saCommands": [], "sFilePath": ""},
            "dictQuantitative": {
                "saCommands": [], "sFilePath": "", "sStandardsPath": "",
            },
            "dictIntegrity": {"saCommands": [], "sFilePath": ""},
            "listUserTests": [],
        }
        dictStep["saTestCommands"] = []
        dictVerification = dictStep.setdefault("dictVerification", {})
        dictVerification["sUnitTest"] = "untested"
        dictVerification["sQualitative"] = "untested"
        dictVerification["sQuantitative"] = "untested"
        dictVerification["sIntegrity"] = "untested"
        dictCtx["save"](sContainerId, dictWorkflow)
        return {"bSuccess": True}


class SaveAndRunTestRequest(BaseModel):
    sContent: str
    sFilePath: str


def _fsBuildPytestCommand(sDirectory, sFilePath):
    """Build a pytest command string for a test file."""
    return (
        f"cd {fsShellQuote(sDirectory)}"
        f" && python -m pytest"
        f" {fsShellQuote(sFilePath)} -v"
    )


def _fnRegisterTestCommand(dictStep, bPassed, sFilePath):
    """Add the pytest run command to the step if the test passed."""
    if not bPassed:
        return
    dictStep.setdefault("saTestCommands", [])
    sRunCmd = f"python -m pytest {sFilePath} -v"
    if sRunCmd not in dictStep["saTestCommands"]:
        dictStep["saTestCommands"].append(sRunCmd)


def _fnRegisterTestSaveAndRun(app, dictCtx):
    """Register POST /api/steps/{id}/{step}/save-and-run-test."""

    @app.post(
        "/api/steps/{sContainerId}/{iStepIndex}/save-and-run-test"
    )
    async def fnSaveAndRunTest(
        sContainerId: str, iStepIndex: int,
        request: SaveAndRunTestRequest,
    ):
        import asyncio
        dictCtx["require"]()
        dictWorkflow = fdictRequireWorkflow(
            dictCtx["workflows"], sContainerId)
        dictStep = dictWorkflow["listSteps"][iStepIndex]
        dictCtx["docker"].fnWriteFile(
            sContainerId, request.sFilePath,
            request.sContent.encode("utf-8"),
        )
        sTestCmd = _fsBuildPytestCommand(
            dictStep.get("sDirectory", "/workspace"),
            request.sFilePath,
        )
        iExitCode, sOutput = await asyncio.to_thread(
            dictCtx["docker"].ftResultExecuteCommand,
            sContainerId, sTestCmd,
        )
        bPassed = iExitCode == 0
        _fnRecordTestResult(
            dictStep, bPassed, dictWorkflow, iStepIndex)
        _fnRegisterTestCommand(dictStep, bPassed, request.sFilePath)
        dictCtx["save"](sContainerId, dictWorkflow)
        return {
            "bPassed": bPassed,
            "sOutput": sOutput,
            "iExitCode": iExitCode,
        }


def _flistResolveTestCommands(dictStep):
    """Return test commands from structured tests or legacy list."""
    from .workflowManager import flistBuildTestCommands
    if "dictTests" in dictStep:
        return flistBuildTestCommands(dictStep)
    return dictStep.get("saTestCommands", [])


_LIST_TEST_CATEGORIES = (
    ("dictIntegrity", "sIntegrity"),
    ("dictQualitative", "sQualitative"),
    ("dictQuantitative", "sQuantitative"),
)


async def _fdictRunAllTestCategories(dictCtx, sContainerId, dictStep):
    """Run each test category and return {category: result_dict}."""
    import asyncio
    sDir = dictStep.get("sDirectory", "/workspace")
    dictVerification = dictStep.setdefault("dictVerification", {})
    dictCategoryResults = {}
    for sCategory, sVerifKey in _LIST_TEST_CATEGORIES:
        dictResult = await _fdictRunOneTestCategory(
            dictCtx, sContainerId, dictStep, sDir, sCategory,
        )
        if dictResult is None:
            continue
        dictVerification[sVerifKey] = (
            "passed" if dictResult["bPassed"] else "failed")
        dictCategoryResults[sCategory] = dictResult
    return dictCategoryResults


async def _fdictRunOneTestCategory(
    dictCtx, sContainerId, dictStep, sDirectory, sCategory,
):
    """Execute one test category and return result dict, or None."""
    import asyncio
    dictCat = dictStep.get("dictTests", {}).get(sCategory, {})
    listCatCmds = dictCat.get("saCommands", [])
    if not listCatCmds:
        return None
    sCatCmd = " && ".join(
        [f"cd {fsShellQuote(sDirectory)}"] + listCatCmds)
    iCatExit, sCatOutput = await asyncio.to_thread(
        dictCtx["docker"].ftResultExecuteCommand,
        sContainerId, sCatCmd,
    )
    return {
        "bPassed": iCatExit == 0,
        "sOutput": sCatOutput,
        "iExitCode": iCatExit,
    }


def _fdictBuildTestResponse(bAllPassed, dictCategoryResults):
    """Build the HTTP response dict for a test run."""
    iMaxExitCode = max(
        (d["iExitCode"] for d in dictCategoryResults.values()),
        default=0,
    )
    return {
        "bPassed": bAllPassed,
        "iExitCode": iMaxExitCode,
        "sOutput": "",
        "dictCategoryResults": dictCategoryResults,
    }


def _fnRegisterTestRun(app, dictCtx):
    """Register POST /api/steps/{id}/{step}/run-tests."""

    @app.post(
        "/api/steps/{sContainerId}/{iStepIndex}/run-tests"
    )
    async def fnRunTests(sContainerId: str, iStepIndex: int):
        import asyncio
        from .workflowManager import flistBuildTestCommands
        dictCtx["require"]()
        dictWorkflow = fdictRequireWorkflow(
            dictCtx["workflows"], sContainerId)
        dictStep = dictWorkflow["listSteps"][iStepIndex]
        listCmds = _flistResolveTestCommands(dictStep)
        if not listCmds:
            raise HTTPException(400, "No test commands")
        dictCategoryResults = await _fdictRunAllTestCategories(
            dictCtx, sContainerId, dictStep,
        )
        bAllPassed = all(
            d["bPassed"] for d in dictCategoryResults.values()
        )
        _fnRecordTestResult(
            dictStep, bAllPassed, dictWorkflow, iStepIndex)
        dictCtx["save"](sContainerId, dictWorkflow)
        return _fdictBuildTestResponse(
            bAllPassed, dictCategoryResults)

    @app.post(
        "/api/steps/{sContainerId}/{iStepIndex}/run-test-category"
    )
    async def fnRunTestCategory(
        sContainerId: str, iStepIndex: int, request: Request,
    ):
        import asyncio
        dictCtx["require"]()
        dictBody = await request.json()
        sCategory = dictBody.get("sCategory", "")
        dictCategoryKeyMap = {
            "integrity": ("dictIntegrity", "sIntegrity"),
            "qualitative": ("dictQualitative", "sQualitative"),
            "quantitative": ("dictQuantitative", "sQuantitative"),
        }
        if sCategory not in dictCategoryKeyMap:
            raise HTTPException(
                400, f"Unknown category: {sCategory}")
        sDictKey, sVerifKey = dictCategoryKeyMap[sCategory]
        dictWorkflow = fdictRequireWorkflow(
            dictCtx["workflows"], sContainerId)
        dictStep = dictWorkflow["listSteps"][iStepIndex]
        dictTests = dictStep.get("dictTests", {})
        dictCat = dictTests.get(sDictKey, {})
        listCmds = dictCat.get("saCommands", [])
        if not listCmds:
            raise HTTPException(
                400, f"No commands for category: {sCategory}")
        sDir = dictStep.get("sDirectory", "/workspace")
        sFullCmd = " && ".join(
            [f"cd {fsShellQuote(sDir)}"] + listCmds)
        iExitCode, sOutput = await asyncio.to_thread(
            dictCtx["docker"].ftResultExecuteCommand,
            sContainerId, sFullCmd,
        )
        bPassed = iExitCode == 0
        dictVerification = dictStep.setdefault(
            "dictVerification", {})
        dictVerification[sVerifKey] = (
            "passed" if bPassed else "failed")
        _fnUpdateAggregateTestState(dictStep)
        dictCtx["save"](sContainerId, dictWorkflow)
        return {
            "bPassed": bPassed,
            "sOutput": sOutput,
            "iExitCode": iExitCode,
        }


def _fnRecordTestResult(dictStep, bPassed, dictWorkflow,
                        iStepIndex):
    """Update verification state after test execution."""
    dictVerification = dictStep.setdefault(
        "dictVerification", {})
    dictVerification["sUnitTest"] = (
        "passed" if bPassed else "failed")
    if bPassed:
        dictVerification.pop("listModifiedFiles", None)
        dictVerification.pop("bUpstreamModified", None)
        _fnClearDownstreamUpstreamFlags(
            dictWorkflow, iStepIndex)


def _fnClearDownstreamUpstreamFlags(dictWorkflow, iStepIndex):
    """Clear bUpstreamModified on downstream steps."""
    dictDownstream = workflowManager.fdictBuildDownstreamMap(
        dictWorkflow)
    listSteps = dictWorkflow.get("listSteps", [])
    for iDown in dictDownstream.get(iStepIndex, set()):
        if 0 <= iDown < len(listSteps):
            dictVerify = listSteps[iDown].get(
                "dictVerification", {})
            dictVerify.pop("bUpstreamModified", None)


def _fnRemoveTestFiles(
    connectionDocker, sContainerId, dictStep, iStepIndex,
):
    """Remove generated test file from the container. Deprecated."""
    from .pipelineRunner import fsShellQuote
    from .testGenerator import fsTestFilePath

    sDirectory = dictStep.get("sDirectory", "")
    sPath = fsTestFilePath(sDirectory, iStepIndex)
    connectionDocker.ftResultExecuteCommand(
        sContainerId, f"rm -f {fsShellQuote(sPath)}"
    )


def _fnRemoveTestDirectory(connectionDocker, sContainerId, dictStep):
    """Remove the entire tests subdirectory from the container."""
    from .pipelineRunner import fsShellQuote
    from .workflowManager import fsTestsDirectory

    sDirectory = dictStep.get("sDirectory", "")
    sTestsDir = fsTestsDirectory(sDirectory)
    connectionDocker.ftResultExecuteCommand(
        sContainerId, f"rm -rf {fsShellQuote(sTestsDir)}"
    )


def _fnUpdateAggregateTestState(dictStep):
    """Compute aggregate sUnitTest from per-category verification states."""
    dictVerification = dictStep.get("dictVerification", {})
    dictTests = dictStep.get("dictTests", {})
    listStates = []
    for sCategory, sVerifKey in (
        ("dictIntegrity", "sIntegrity"),
        ("dictQualitative", "sQualitative"),
        ("dictQuantitative", "sQuantitative"),
    ):
        dictCat = dictTests.get(sCategory, {})
        if (dictCat.get("saCommands", [])):
            listStates.append(
                dictVerification.get(sVerifKey, "untested"))
    if not listStates:
        dictVerification["sUnitTest"] = "untested"
    elif "failed" in listStates:
        dictVerification["sUnitTest"] = "failed"
    elif all(s == "passed" for s in listStates):
        dictVerification["sUnitTest"] = "passed"
    else:
        dictVerification["sUnitTest"] = "untested"


def _fsPlotStandardPath(sBasename):
    """Return the standard PNG filename for a plot basename."""
    return f"{sBasename}_standard.png"


def _fsBuildConvertCommand(sPlotPath, sOutputDir, sBasename):
    """Build a shell command to convert a plot to a standard PNG."""
    sStandardBase = posixpath.splitext(sBasename)[0]
    sStandardPng = posixpath.join(
        sOutputDir, _fsPlotStandardPath(sStandardBase))
    sStandardPrefix = posixpath.join(
        sOutputDir, f"{sStandardBase}_standard")
    return (
        f"pdftoppm -png -r 72 -singlefile "
        f"{fsShellQuote(sPlotPath)} "
        f"{fsShellQuote(sStandardPrefix)} "
        f"2>/dev/null || "
        f"gs -q -dNOPAUSE -dBATCH -sDEVICE=pngalpha "
        f"-r72 -dUseCropBox "
        f"-sOutputFile={fsShellQuote(sStandardPng)} "
        f"{fsShellQuote(sPlotPath)} 2>/dev/null || true"
    )


def _flistResolvePlotPaths(dictStep, dictVars):
    """Return list of (resolved_path, basename) for step plot files."""
    from .workflowManager import fsResolveVariables
    sStepDir = dictStep.get("sDirectory", "")
    listResult = []
    for sFile in dictStep.get("saPlotFiles", []):
        sResolved = fsResolveVariables(sFile, dictVars)
        if not sResolved.startswith("/"):
            sResolved = posixpath.join(sStepDir, sResolved)
        sBasename = posixpath.basename(sResolved)
        listResult.append((sResolved, sBasename))
    return listResult


def _fnRegisterStandardizePlots(app, dictCtx):
    """Register POST /api/steps/{id}/{step}/standardize-plots."""

    @app.post(
        "/api/steps/{sContainerId}/{iStepIndex}/standardize-plots"
    )
    async def fnStandardizePlots(
        sContainerId: str, iStepIndex: int,
        request: Request,
    ):
        import asyncio
        from datetime import datetime, timezone
        dictCtx["require"]()
        dictWorkflow = fdictRequireWorkflow(
            dictCtx["workflows"], sContainerId)
        dictStep = dictWorkflow["listSteps"][iStepIndex]
        dictVars = dictCtx["variables"](sContainerId)
        dictBody = await request.json()
        sTargetFile = dictBody.get("sFileName", "")
        listPlots = _flistResolvePlotPaths(dictStep, dictVars)
        if not listPlots:
            raise HTTPException(400, "No plot files in this step")
        listConverted = await _flistConvertToStandards(
            dictCtx, sContainerId, listPlots, sTargetFile)
        if not listConverted:
            raise HTTPException(
                500, "Conversion failed: no standard PNGs "
                "were created. Check that ghostscript or "
                "poppler-utils is installed in the container.")
        listStandardizedBasenames = _flistStandardizedBasenames(
            listPlots, sTargetFile)
        dictVerification = dictStep.setdefault(
            "dictVerification", {})
        sTimestamp = datetime.now(timezone.utc).strftime(
            "%Y-%m-%d %H:%M UTC")
        dictVerification["sLastStandardized"] = sTimestamp
        dictCtx["save"](sContainerId, dictWorkflow)
        return {
            "bSuccess": True,
            "listConverted": listConverted,
            "listStandardizedBasenames": listStandardizedBasenames,
            "sTimestamp": sTimestamp,
        }

    @app.post(
        "/api/steps/{sContainerId}/{iStepIndex}/compare-plot"
    )
    async def fnComparePlot(
        sContainerId: str, iStepIndex: int,
        request: Request,
    ):
        dictCtx["require"]()
        dictWorkflow = fdictRequireWorkflow(
            dictCtx["workflows"], sContainerId)
        dictStep = dictWorkflow["listSteps"][iStepIndex]
        dictVars = dictCtx["variables"](sContainerId)
        dictBody = await request.json()
        sFileName = dictBody.get("sFileName", "")
        if not sFileName:
            raise HTTPException(400, "sFileName is required")
        listPlots = _flistResolvePlotPaths(dictStep, dictVars)
        sPlotPath = _fsFindPlotPath(listPlots, sFileName)
        sStandardPath = _fsFindStandardForFile(
            listPlots, sFileName)
        if not sStandardPath:
            raise HTTPException(
                404, "No standard found for this file")
        return {
            "sPlotPath": sPlotPath,
            "sStandardPath": sStandardPath,
        }

    @app.get(
        "/api/steps/{sContainerId}/{iStepIndex}/plot-standards"
    )
    async def fnCheckPlotStandards(
        sContainerId: str, iStepIndex: int,
    ):
        import asyncio
        dictCtx["require"]()
        dictWorkflow = fdictRequireWorkflow(
            dictCtx["workflows"], sContainerId)
        dictStep = dictWorkflow["listSteps"][iStepIndex]
        dictVars = dictCtx["variables"](sContainerId)
        listPlots = _flistResolvePlotPaths(dictStep, dictVars)
        dictStandards = await _fdictCheckStandardsExist(
            dictCtx, sContainerId, listPlots)
        return {"dictStandards": dictStandards}


async def _fdictCheckStandardsExist(
    dictCtx, sContainerId, listPlots,
):
    """Check which standard PNGs exist in the container."""
    import asyncio
    if not listPlots:
        return {}
    listPaths = []
    listBasenames = []
    for sResolved, sBasename in listPlots:
        sBase = posixpath.splitext(sBasename)[0]
        sDir = posixpath.dirname(sResolved)
        sStandardPath = posixpath.join(
            sDir, _fsPlotStandardPath(sBase))
        listPaths.append(sStandardPath)
        listBasenames.append(sBasename)
    sCheckCommand = " && ".join(
        f'test -f {fsShellQuote(sPath)} && echo "Y" || echo "N"'
        for sPath in listPaths
    )
    tResult = await asyncio.to_thread(
        dictCtx["docker"].ftResultExecuteCommand,
        sContainerId, sCheckCommand,
    )
    sOutput = tResult[1] if tResult else ""
    listLines = sOutput.strip().split("\n")
    dictResult = {}
    for iIdx, sBasename in enumerate(listBasenames):
        if iIdx < len(listLines):
            dictResult[sBasename] = listLines[iIdx].strip() == "Y"
        else:
            dictResult[sBasename] = False
    return dictResult


def _flistStandardizedBasenames(listPlots, sTargetFile):
    """Return basenames of plots that were standardized."""
    listResult = []
    for _sResolved, sBasename in listPlots:
        if sTargetFile and sBasename != sTargetFile:
            continue
        listResult.append(sBasename)
    return listResult


def _fsFindPlotPath(listPlots, sFileName):
    """Return the resolved plot path for a given filename."""
    for sResolved, sBasename in listPlots:
        if sBasename == sFileName or sResolved.endswith(sFileName):
            return sResolved
    return ""


def _fsFindStandardForFile(listPlots, sFileName):
    """Return the standard PNG path for a given plot filename."""
    for sResolved, sBasename in listPlots:
        if sBasename == sFileName or sResolved.endswith(sFileName):
            sBase = posixpath.splitext(sBasename)[0]
            sDir = posixpath.dirname(sResolved)
            return posixpath.join(
                sDir, _fsPlotStandardPath(sBase))
    return ""


async def _flistConvertToStandards(
    dictCtx, sContainerId, listPlots, sTargetFile,
):
    """Convert plot files to standard PNGs inside the container."""
    import asyncio
    listCommands = []
    listConverted = []
    for sResolved, sBasename in listPlots:
        if sTargetFile and sBasename != sTargetFile:
            continue
        sOutputDir = posixpath.dirname(sResolved)
        sCommand = _fsBuildConvertCommand(
            sResolved, sOutputDir, sBasename)
        listCommands.append(sCommand)
        sBase = posixpath.splitext(sBasename)[0]
        listConverted.append(_fsPlotStandardPath(sBase))
    if not listCommands:
        return []
    sFullCommand = " && ".join(listCommands)
    await asyncio.to_thread(
        dictCtx["docker"].ftResultExecuteCommand,
        sContainerId, sFullCommand,
    )
    return await _flistVerifyConverted(
        dictCtx, sContainerId, listPlots,
        listConverted, sTargetFile,
    )


async def _flistVerifyConverted(
    dictCtx, sContainerId, listPlots, listConverted,
    sTargetFile,
):
    """Return only the basenames whose standard PNGs exist."""
    import asyncio
    listVerified = []
    for sConverted, (sResolved, sBasename) in zip(
        listConverted, listPlots,
    ):
        if sTargetFile and sBasename != sTargetFile:
            continue
        sDir = posixpath.dirname(sResolved)
        sFullPath = posixpath.join(sDir, sConverted)
        iExitCode, _ = await asyncio.to_thread(
            dictCtx["docker"].ftResultExecuteCommand,
            sContainerId, f"test -f {fsShellQuote(sFullPath)}",
        )
        if iExitCode == 0:
            listVerified.append(sConverted)
    return listVerified


def _fnRegisterStepRoutes(app, dictCtx):
    """Register all step CRUD routes."""
    _fnRegisterStepsList(app, dictCtx)
    _fnRegisterStepGet(app, dictCtx)
    _fnRegisterStepCreate(app, dictCtx)
    _fnRegisterStepInsert(app, dictCtx)
    _fnRegisterStepUpdate(app, dictCtx)
    _fnRegisterStepDelete(app, dictCtx)
    _fnRegisterStepReorder(app, dictCtx)


def _fnRegisterAllRoutes(app, dictCtx, sWorkspaceRoot):
    """Register all API routes on the app."""
    _fnRegisterCoreRoutes(app, dictCtx, sWorkspaceRoot)
    _fnRegisterStepRoutes(app, dictCtx)
    _fnRegisterTestGenerate(app, dictCtx)
    _fnRegisterTestSaveAndRun(app, dictCtx)
    _fnRegisterTestRun(app, dictCtx)
    _fnRegisterStandardizePlots(app, dictCtx)
    _fnRegisterFigure(app, dictCtx)
    _fnRegisterUserInfo(app)
    _fnRegisterPipelineState(app, dictCtx)
    _fnRegisterRuntimeInfo(app, dictCtx)
    _fnRegisterFileStatus(app, dictCtx)
    _fnRegisterPipelineKill(app, dictCtx)
    _fnRegisterPipelineClean(app, dictCtx)
    _fnRegisterPipelineWs(app, dictCtx)
    _fnRegisterTerminalWs(app, dictCtx)
    _fnRegisterStaticFiles(app, dictCtx)


class SessionTokenMiddleware(BaseHTTPMiddleware):
    """Reject /api/ requests missing a valid session token."""

    async def dispatch(self, request: Request, call_next):
        sPath = request.url.path
        bNeedsToken = (
            sPath.startswith("/api/")
            and sPath != "/api/session-token"
        )
        if bNeedsToken:
            sToken = request.headers.get("x-session-token", "")
            if not sToken:
                bIsWebSocket = (
                    request.headers.get("upgrade", "").lower()
                    == "websocket")
                bIsDownload = "/download/" in sPath
                if bIsWebSocket or bIsDownload:
                    sToken = request.query_params.get(
                        "sToken", "")
            sExpected = request.app.state.sSessionToken
            if sToken != sExpected:
                return Response(
                    status_code=401,
                    content='{"detail":"Unauthorized"}',
                    media_type="application/json",
                )
        return await call_next(request)


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Add security headers to all HTTP responses."""

    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = (
            "strict-origin-when-cross-origin"
        )
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; "
            "script-src 'self' https://cdnjs.cloudflare.com "
            "https://cdn.jsdelivr.net; "
            "worker-src 'self' blob: "
            "https://cdnjs.cloudflare.com; "
            "style-src 'self' 'unsafe-inline' "
            "https://cdn.jsdelivr.net "
            "https://fonts.googleapis.com; "
            "font-src 'self' https://fonts.gstatic.com; "
            "img-src 'self' data: blob:; "
            "connect-src 'self' "
            "ws://127.0.0.1:* wss://127.0.0.1:* "
            "ws://localhost:* wss://localhost:*; "
            "frame-ancestors 'none'"
        )
        return response


def fbValidateWebSocketOrigin(websocket: WebSocket):
    """Return True only if the WebSocket origin is localhost."""
    sOrigin = ""
    for sKey, sVal in websocket.headers.items():
        if sKey.lower() == "origin":
            sOrigin = sVal
            break
    if not sOrigin:
        return False
    listAllowed = [
        "http://127.0.0.1", "http://localhost",
        "https://127.0.0.1", "https://localhost",
    ]
    for sAllowed in listAllowed:
        if sOrigin.startswith(sAllowed):
            return True
    return False


def fappCreateApplication(
    sWorkspaceRoot="/workspace", sTerminalUserArg=None,
):
    """Build and return the configured FastAPI application."""
    global sTerminalUser
    sTerminalUser = sTerminalUserArg
    app = FastAPI(title="Vaibify Workflow Viewer")
    sSessionToken = secrets.token_urlsafe(32)
    app.state.sSessionToken = sSessionToken
    app.state.setAllowedContainers = set()
    app.add_middleware(SessionTokenMiddleware)
    app.add_middleware(SecurityHeadersMiddleware)
    dictCtx = fdictBuildContext(_fconnectionCreateDocker())
    dictCtx["sSessionToken"] = sSessionToken
    dictCtx["setAllowedContainers"] = app.state.setAllowedContainers
    _fnRegisterAllRoutes(app, dictCtx, sWorkspaceRoot)
    return app


def fappCreateHubApplication():
    """Build a hub-mode FastAPI app with registry support.

    Unlike ``fappCreateApplication``, this does not require a
    project config and can manage multiple projects via the
    global registry.
    """
    from .registryRoutes import fnRegisterRegistryRoutes
    global sTerminalUser
    sTerminalUser = "researcher"
    app = FastAPI(title="Vaibify Hub")
    sSessionToken = secrets.token_urlsafe(32)
    app.state.sSessionToken = sSessionToken
    app.state.setAllowedContainers = set()
    app.add_middleware(SessionTokenMiddleware)
    app.add_middleware(SecurityHeadersMiddleware)
    dictCtx = fdictBuildContext(_fconnectionCreateDocker())
    dictCtx["sSessionToken"] = sSessionToken
    dictCtx["setAllowedContainers"] = app.state.setAllowedContainers
    _fnRegisterAllRoutes(app, dictCtx, WORKSPACE_ROOT)
    fnRegisterRegistryRoutes(app, dictCtx)
    return app
