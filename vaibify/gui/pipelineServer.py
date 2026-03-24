"""FastAPI application with REST and WebSocket routes for workflow viewing."""

import asyncio
import json
import logging
import os
import posixpath
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
    saDataCommands: List[str] = []
    saDataFiles: List[str] = []
    saTestCommands: List[str] = []
    saPlotCommands: List[str] = []
    saPlotFiles: List[str] = []


class StepUpdateRequest(BaseModel):
    sName: Optional[str] = None
    sDirectory: Optional[str] = None
    bPlotOnly: Optional[bool] = None
    bEnabled: Optional[bool] = None
    saDataCommands: Optional[List[str]] = None
    saDataFiles: Optional[List[str]] = None
    saTestCommands: Optional[List[str]] = None
    saPlotCommands: Optional[List[str]] = None
    saPlotFiles: Optional[List[str]] = None
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


class RunRequest(BaseModel):
    listStepIndices: List[int] = []
    iStartStep: Optional[int] = None


class FileWriteRequest(BaseModel):
    sContent: str


class TestGenerateRequest(BaseModel):
    bUseApi: bool = False
    sApiKey: Optional[str] = None


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
    raise HTTPException(404, f"Figure not found: {sAbsPath}")


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
        raise HTTPException(404, f"Figure not found: {error}")


def flistDirectoryEntries(connectionDocker, sContainerId, sAbsPath):
    """Run find command and return stripped output lines."""
    sCommand = (
        f"find {fsShellQuote(sAbsPath)} -maxdepth 1 -mindepth 1 "
        f"\\( -type f -o -type d \\) 2>/dev/null | sort"
    )
    _, sOutput = connectionDocker.ftResultExecuteCommand(
        sContainerId, sCommand
    )
    return [s.strip() for s in sOutput.splitlines() if s.strip()]


def fdictEntryFromPath(connectionDocker, sContainerId, sPath):
    """Build a directory entry dict for a single path."""
    _, sTypeOutput = connectionDocker.ftResultExecuteCommand(
        sContainerId,
        f"test -d {fsShellQuote(sPath)} && echo d || echo f",
    )
    return {
        "sName": posixpath.basename(sPath),
        "sPath": sPath,
        "bIsDirectory": "d" in sTypeOutput,
    }


def flistQueryDirectory(connectionDocker, sContainerId, sAbsPath):
    """List files and directories at the given path."""
    listPaths = flistDirectoryEntries(
        connectionDocker, sContainerId, sAbsPath
    )
    return [
        fdictEntryFromPath(connectionDocker, sContainerId, s)
        for s in listPaths
    ]


async def _fnDispatchRunFrom(
    connectionDocker, sContainerId, dictRequest,
    sWorkflowDirectory, fnCallback,
):
    """Dispatch runFrom with the start step from the request."""
    await fnRunFromStep(
        connectionDocker, sContainerId,
        dictRequest.get("iStartStep", 1),
        sWorkflowDirectory, fnCallback,
    )


async def fnDispatchAction(
    sAction, dictRequest, connectionDocker,
    sContainerId, dictWorkflow, dictWorkflowPathCache,
    sWorkflowDirectory, fnCallback,
):
    """Route a WebSocket pipeline action to the correct runner."""
    if sAction == "runAll":
        await fnRunAllSteps(
            connectionDocker, sContainerId, sWorkflowDirectory, fnCallback)
    elif sAction == "forceRunAll":
        await fnRunAllSteps(
            connectionDocker, sContainerId, sWorkflowDirectory,
            fnCallback, bForceRun=True)
    elif sAction == "runFrom":
        await _fnDispatchRunFrom(
            connectionDocker, sContainerId, dictRequest,
            sWorkflowDirectory, fnCallback)
    elif sAction == "verify":
        await fnVerifyOnly(
            connectionDocker, sContainerId, sWorkflowDirectory, fnCallback)
    elif sAction == "runSelected":
        await _fnDispatchSelected(
            connectionDocker, sContainerId, dictRequest,
            dictWorkflow, dictWorkflowPathCache, sWorkflowDirectory, fnCallback)


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
    """Receive and dispatch pipeline WebSocket messages."""
    while True:
        dictRequest = json.loads(await websocket.receive_text())

        async def fnCallback(dictEvent):
            await websocket.send_json(dictEvent)

        await fnDispatchAction(
            dictRequest.get("sAction", "runAll"),
            dictRequest, connectionDocker, sContainerId,
            dictWorkflow, dictWorkflowPathCache,
            sWorkflowDirectory, fnCallback,
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
    if "no space left on device" in sRawError.lower():
        return "Docker disk full. Run: docker image prune -f"
    if "no such container" in sRawError.lower():
        return "Container not found. It may have stopped."
    if "connection refused" in sRawError.lower():
        return "Cannot connect to Docker. Is it running?"
    if "permission denied" in sRawError.lower():
        return "Permission denied. Check Docker access."
    if len(sRawError) > 200:
        return sRawError[:200] + "..."
    return sRawError


def fdictHandleConnect(dictCtx, sContainerId, sWorkflowPath):
    """Load workflow, cache it, return connection response."""
    try:
        dictWorkflow = workflowManager.fdictLoadWorkflowFromContainer(
            dictCtx["docker"], sContainerId, sWorkflowPath
        )
        dictCtx["workflows"][sContainerId] = dictWorkflow
        dictCtx["setAllowedContainers"].add(sContainerId)
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


def _fbContainerHasVaibify(connectionDocker, sContainerId):
    """Return True if the container has a .vaibify directory."""
    iExitCode, _ = connectionDocker.ftResultExecuteCommand(
        sContainerId,
        f"test -d {fsShellQuote(WORKSPACE_ROOT + '/.vaibify')}",
    )
    return iExitCode == 0


def _fnRegisterContainers(app, dictCtx):
    """Register GET /api/containers route."""

    @app.get("/api/containers")
    async def fnGetContainers():
        dictCtx["require"]()
        try:
            listContainers = dictCtx["docker"].flistGetRunningContainers()
            for dictContainer in listContainers:
                dictContainer["bConfigured"] = _fbContainerHasVaibify(
                    dictCtx["docker"], dictContainer["sContainerId"],
                )
            return listContainers
        except Exception as error:
            raise HTTPException(500, f"Docker error: {error}")


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
            raise HTTPException(500, f"Search failed: {error}")


class CreateWorkflowRequest(BaseModel):
    sWorkflowName: str
    sFileName: str


def _fnRegisterWorkflowCreate(app, dictCtx):
    """Register POST /api/workflows/{id}/create route."""

    @app.post("/api/workflows/{sContainerId}/create")
    async def fnCreateWorkflow(
        sContainerId: str, request: CreateWorkflowRequest
    ):
        dictCtx["require"]()
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
            "/workspace", workflowManager.VAIBIFY_WORKFLOWS_DIR
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
        sFigureDir = dictWorkflow.get(
            "sOverleafFigureDirectory", "figures")
        sProjectId = dictWorkflow.get("sOverleafProjectId", "")
        sGithubUrl = dictWorkflow.get("sGithubBaseUrl", "")
        sDoi = dictWorkflow.get("sZenodoDoi", "")
        sTexFile = dictWorkflow.get("sTexFilename", "main.tex")
        iExit, sOut = await asyncio.to_thread(
            syncDispatcher.ftResultPushToOverleaf,
            dictCtx["docker"], sContainerId,
            request.listFilePaths, sProjectId, sFigureDir,
            dictWorkflow=dictWorkflow,
            sGithubBaseUrl=sGithubUrl,
            sDoi=sDoi,
            sTexFilename=sTexFile,
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
            f"find {sDirectory} -maxdepth 1 -name '*.py' "
            f"-printf '%f\\n' 2>/dev/null || "
            f"ls {sDirectory}/*.py 2>/dev/null | "
            f"xargs -n1 basename 2>/dev/null",
        )
        listFiles = [
            s.strip() for s in sOutput.strip().splitlines()
            if s.strip()
        ] if iExit == 0 and sOutput.strip() else []
        return workflowManager.fdictAutoDetectScripts(listFiles)


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
    _fnRegisterSyncRoutes(app, dictCtx)


def _fnRegisterFileWrite(app, dictCtx, sWorkspaceRoot):
    """Register PUT /api/file route for saving edited text files."""

    @app.put("/api/file/{sContainerId}/{sFilePath:path}")
    async def fnWriteFile(
        sContainerId: str, sFilePath: str,
        request: FileWriteRequest, sWorkdir: str = "",
    ):
        dictCtx["require"]()
        sDir = dictCtx["workflowDir"](sContainerId)
        if sFilePath.startswith("/"):
            sAbsPath = sFilePath
        elif sWorkdir:
            sBase = sWorkdir if sWorkdir.startswith("/") \
                else posixpath.join(sDir, sWorkdir)
            sAbsPath = posixpath.join(sBase, sFilePath)
        else:
            sAbsPath = posixpath.join(sDir, sFilePath)
        fnValidatePathWithinRoot(sAbsPath, sWorkspaceRoot)
        baContent = request.sContent.encode("utf-8")
        dictCtx["docker"].fnWriteFile(
            sContainerId, sAbsPath, baContent
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
        listEntries = flistDirectoryEntries(
            dictCtx["docker"], sContainerId, sLogsDir
        )
        listLogs = [
            posixpath.basename(s) for s in listEntries
            if s.endswith(".log")
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
            raise HTTPException(404, f"Log not found: {error}")


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
        listPaths = [sAbsPath]
        if sWorkdir and not sFilePath.startswith("/"):
            if sWorkdir.startswith("/"):
                listPaths.append(
                    posixpath.join(sWorkdir, sFilePath))
            else:
                listPaths.append(
                    posixpath.join(sDir, sWorkdir, sFilePath))
        sTestCmd = " || ".join(
            f"test -f {p}" for p in listPaths)
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


def fsDetectDockerRuntime():
    """Detect the Docker runtime (colima, desktop, orbstack, etc.)."""
    import subprocess
    try:
        resultContext = subprocess.run(
            ["docker", "context", "ls", "--format", "{{.Name}}:{{.Current}}"],
            capture_output=True, text=True, timeout=5,
        )
        for sLine in resultContext.stdout.strip().split("\n"):
            if ":true" in sLine.lower():
                sContext = sLine.split(":")[0].strip().lower()
                if "colima" in sContext:
                    return {"sRuntime": "colima", "sSleepWarning":
                        "Your Docker runtime (Colima) does not "
                        "sleep automatically. Use 'caffeinate -s' "
                        "to prevent macOS from sleeping during "
                        "long pipeline runs."}
                if "desktop" in sContext or "default" == sContext:
                    return {"sRuntime": "desktop", "sSleepWarning":
                        "Ensure Docker Desktop is configured to "
                        "not sleep idle VMs (Settings > Resources "
                        "> Advanced). Also consider running "
                        "'caffeinate -s' to prevent macOS sleep."}
                if "orbstack" in sContext:
                    return {"sRuntime": "orbstack", "sSleepWarning":
                        "OrbStack VMs survive sleep. Use "
                        "'caffeinate -s' to prevent macOS from "
                        "sleeping during long pipeline runs."}
                return {"sRuntime": sContext, "sSleepWarning":
                    "Use 'caffeinate -s' to prevent macOS from "
                    "sleeping during long pipeline runs."}
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
        sRepoRoot = dictCtx["workflowDir"](sContainerId)
        listPaths = _flistCollectOutputPaths(
            dictWorkflow, sRepoRoot)
        dictModTimes = await asyncio.to_thread(
            _fdictGetModTimes,
            dictCtx["docker"], sContainerId, listPaths,
        )
        listInvalidated = _flistDetectAndInvalidate(
            dictCtx, sContainerId, dictWorkflow,
            dictModTimes, sRepoRoot,
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


def fdictCollectOutputPathsByStep(dictWorkflow, sRepoRoot=""):
    """Return {iStepIndex: [resolved_paths]} for each step."""
    dictResult = {}
    dictGlobalVars = _fdictFileStatusGlobalVars(
        dictWorkflow, sRepoRoot)
    for iIndex, dictStep in enumerate(
        dictWorkflow.get("listSteps", [])
    ):
        dictResult[iIndex] = _flistResolveStepPaths(
            dictStep, dictGlobalVars, sRepoRoot,
        )
    return dictResult


def _fdictFileStatusGlobalVars(dictWorkflow, sRepoRoot=""):
    """Return global variables with absolute paths for resolution."""
    sPlotDir = dictWorkflow.get("sPlotDirectory", "Plot")
    if sRepoRoot and not sPlotDir.startswith("/"):
        sPlotDir = posixpath.join(sRepoRoot, sPlotDir)
    return {
        "sPlotDirectory": sPlotDir,
        "sFigureType": dictWorkflow.get("sFigureType", "pdf"),
    }


def _flistResolveStepPaths(dictStep, dictGlobalVars,
                           sRepoRoot=""):
    """Return resolved output paths for a single step."""
    from .workflowManager import fsResolveVariables
    sStepDir = dictStep.get("sDirectory", "")
    if sRepoRoot and not sStepDir.startswith("/"):
        sStepDir = posixpath.join(sRepoRoot, sStepDir)
    listPaths = []
    for sFile in (dictStep.get("saDataFiles", [])
                  + dictStep.get("saPlotFiles", [])):
        sResolved = fsResolveVariables(sFile, dictGlobalVars)
        if not sResolved.startswith("/"):
            sResolved = posixpath.join(sStepDir, sResolved)
        listPaths.append(sResolved)
    return listPaths


def _flistCollectOutputPaths(dictWorkflow, sRepoRoot=""):
    """Collect all resolved output file paths from the workflow."""
    dictByStep = fdictCollectOutputPathsByStep(
        dictWorkflow, sRepoRoot)
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
    listExisting = dictVerification.get("listModifiedFiles", [])
    setModified = set(listExisting)
    setModified.update(listChangedPaths)
    dictVerification["listModifiedFiles"] = sorted(setModified)
    dictStep["dictVerification"] = dictVerification


def _fnInvalidateDownstreamStep(dictStep):
    """Mark a downstream step as affected by upstream changes."""
    dictVerification = dictStep.get("dictVerification", {})
    if dictVerification.get("sUnitTest") == "passed":
        dictVerification["sUnitTest"] = "untested"
    dictVerification["bUpstreamModified"] = True
    dictStep["dictVerification"] = dictVerification


def _fdictBuildScriptStatus(dictWorkflow, dictCurrentHashes):
    """Compare current script hashes against stored run hashes."""
    from .syncDispatcher import _fsNormalizePath
    from .commandUtilities import flistExtractScripts
    dictResult = {}
    for iIndex, dictStep in enumerate(
        dictWorkflow.get("listSteps", [])
    ):
        dictRunStats = dictStep.get("dictRunStats", {})
        dictStoredHashes = dictRunStats.get("dictInputHashes", {})
        if not dictStoredHashes:
            dictResult[iIndex] = "unknown"
            continue
        sDirectory = dictStep.get("sDirectory", "")
        bModified = False
        for sKey in ("saDataCommands", "saPlotCommands"):
            for sScript in flistExtractScripts(
                dictStep.get(sKey, [])
            ):
                sPath = _fsNormalizePath(sDirectory, sScript)
                sStored = dictStoredHashes.get(sPath)
                sCurrent = dictCurrentHashes.get(sPath)
                if sStored is None or sStored != sCurrent:
                    bModified = True
                    break
            if bModified:
                break
        dictResult[iIndex] = "modified" if bModified else "unchanged"
    return dictResult


def _flistDetectAndInvalidate(dictCtx, sContainerId,
                              dictWorkflow, dictNewModTimes,
                              sRepoRoot=""):
    """Detect file changes and invalidate affected steps."""
    if "dictPreviousModTimes" not in dictCtx:
        dictCtx["dictPreviousModTimes"] = {}
    dictPrevByContainer = dictCtx["dictPreviousModTimes"]
    dictOldModTimes = dictPrevByContainer.get(sContainerId, {})
    dictPrevByContainer[sContainerId] = dict(dictNewModTimes)
    if not dictOldModTimes:
        return []
    if _fbPipelineIsRunning(dictCtx, sContainerId):
        return []
    dictPathsByStep = fdictCollectOutputPathsByStep(
        dictWorkflow, sRepoRoot)
    dictChangedFiles = _fdictFindChangedFiles(
        dictPathsByStep, dictOldModTimes, dictNewModTimes,
    )
    if not dictChangedFiles:
        return []
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
    dictCtx["save"](sContainerId, dictWorkflow)
    setAllAffected = setDirectChanged | setDownstream
    dictInvalidated = {}
    for iIndex in setAllAffected:
        if 0 <= iIndex < len(listSteps):
            dictInvalidated[iIndex] = listSteps[iIndex].get(
                "dictVerification", {})
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


def _fnRegisterPipelineKill(app, dictCtx):
    """Register POST /api/pipeline/{id}/kill endpoint."""

    @app.post("/api/pipeline/{sContainerId}/kill")
    async def fnKillRunningTasks(sContainerId: str):
        dictCtx["require"]()
        dictWorkflow = fdictRequireWorkflow(
            dictCtx["workflows"], sContainerId)
        listPatterns = _flistExtractKillPatterns(dictWorkflow)
        sGrepPattern = "|".join(listPatterns) if listPatterns else ""
        if not sGrepPattern:
            return {"bSuccess": True, "iProcessesKilled": 0}
        sCountCommand = (
            f"ps aux | grep -E '{sGrepPattern}' "
            f"| grep -v grep | wc -l"
        )
        _, sCountBefore = await asyncio.to_thread(
            dictCtx["docker"].ftResultExecuteCommand,
            sContainerId, sCountCommand,
        )
        iCountBefore = 0
        try:
            iCountBefore = int(sCountBefore.strip())
        except ValueError:
            pass
        if iCountBefore > 0:
            for sPattern in listPatterns:
                sBracket = "[" + sPattern[0] + "]" + sPattern[1:]
                sKill = (
                    f"ps aux | grep '{sBracket}' "
                    f"| awk '{{print $2}}' "
                    f"| xargs kill -9 2>/dev/null"
                )
                await asyncio.to_thread(
                    dictCtx["docker"].ftResultExecuteCommand,
                    sContainerId, sKill,
                )
        return {
            "bSuccess": True,
            "iProcessesKilled": iCountBefore,
        }


def _fnRegisterPipelineClean(app, dictCtx):
    """Register POST /api/pipeline/{id}/clean endpoint."""

    @app.post("/api/pipeline/{sContainerId}/clean")
    async def fnCleanOutputs(sContainerId: str):
        dictCtx["require"]()
        dictWorkflow = fdictRequireWorkflow(
            dictCtx["workflows"], sContainerId)
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
            dictCtx["docker"], sContainerId, sUser=sTerminalUser
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
        sWorkflowDirectory = posixpath.dirname(
            fsRequireWorkflowPath(dictPaths, sContainerId))
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
    """Register container, file, monitor, and stub routes."""
    _fnRegisterContainers(app, dictCtx)
    _fnRegisterWorkflowSearch(app, dictCtx)
    _fnRegisterWorkflowCreate(app, dictCtx)
    _fnRegisterConnect(app, dictCtx)
    _fnRegisterFiles(app, dictCtx, sWorkspaceRoot)
    _fnRegisterFileWrite(app, dictCtx, sWorkspaceRoot)
    _fnRegisterMonitor(app)
    _fnRegisterReproEndpoints(app, dictCtx)
    _fnRegisterDag(app, dictCtx)
    _fnRegisterDatasetDownload(app, dictCtx)
    _fnRegisterLogRoutes(app, dictCtx)
    _fnRegisterSettingsGet(app, dictCtx)
    _fnRegisterSettingsPut(app, dictCtx)


def _fnRegisterTestGenerate(app, dictCtx):
    """Register test generation and deletion routes."""

    @app.post("/api/steps/{sContainerId}/{iStepIndex}/generate-test")
    async def fnGenerateTest(
        sContainerId: str, iStepIndex: int,
        request: TestGenerateRequest,
    ):
        dictCtx["require"]()
        from .testGenerator import (
            fbContainerHasClaude, fdictGenerateTest,
        )
        if not request.bUseApi:
            bHasClaude = fbContainerHasClaude(
                dictCtx["docker"], sContainerId
            )
            if not bHasClaude:
                return {"bNeedsFallback": True}
        dictWorkflow = fdictRequireWorkflow(
            dictCtx["workflows"], sContainerId
        )
        dictVars = dictCtx["variables"](sContainerId)
        try:
            dictResult = await asyncio.to_thread(
                fdictGenerateTest,
                dictCtx["docker"], sContainerId, iStepIndex,
                dictWorkflow, dictVars,
                request.bUseApi, request.sApiKey,
                sUser=sTerminalUser,
            )
        except Exception as error:
            raise HTTPException(500, f"Generation failed: {error}")
        dictStep = dictWorkflow["listSteps"][iStepIndex]
        dictStep["saTestCommands"] = dictResult["saTestCommands"]
        dictCtx["save"](sContainerId, dictWorkflow)
        return {
            "bGenerated": True,
            "sFilePath": dictResult["sFilePath"],
            "sContent": dictResult["sContent"],
        }

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
        _fnRemoveTestFiles(
            dictCtx["docker"], sContainerId, dictStep, iStepIndex
        )
        dictStep["saTestCommands"] = []
        dictCtx["save"](sContainerId, dictWorkflow)
        return {"bSuccess": True}


class SaveAndRunTestRequest(BaseModel):
    sContent: str
    sFilePath: str


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
        sTestCmd = f"cd {dictStep.get('sDirectory', '/workspace')}"
        sTestCmd += f" && python -m pytest {request.sFilePath} -v"
        iExitCode, sOutput = await asyncio.to_thread(
            dictCtx["docker"].ftResultExecuteCommand,
            sContainerId, sTestCmd,
        )
        bPassed = iExitCode == 0
        if bPassed:
            dictStep.setdefault("dictVerification", {})
            dictStep["dictVerification"]["sUnitTest"] = "passed"
            dictStep.setdefault("saTestCommands", [])
            sRunCmd = f"python -m pytest {request.sFilePath} -v"
            if sRunCmd not in dictStep["saTestCommands"]:
                dictStep["saTestCommands"].append(sRunCmd)
        else:
            dictStep.setdefault("dictVerification", {})
            dictStep["dictVerification"]["sUnitTest"] = "failed"
        return {
            "bPassed": bPassed,
            "sOutput": sOutput,
            "iExitCode": iExitCode,
        }


def _fnRegisterTestRun(app, dictCtx):
    """Register POST /api/steps/{id}/{step}/run-tests."""

    @app.post(
        "/api/steps/{sContainerId}/{iStepIndex}/run-tests"
    )
    async def fnRunTests(sContainerId: str, iStepIndex: int):
        import asyncio
        dictCtx["require"]()
        dictWorkflow = fdictRequireWorkflow(
            dictCtx["workflows"], sContainerId)
        dictStep = dictWorkflow["listSteps"][iStepIndex]
        listCmds = dictStep.get("saTestCommands", [])
        if not listCmds:
            raise HTTPException(400, "No test commands")
        sDir = dictStep.get("sDirectory", "/workspace")
        sFullCmd = " && ".join(
            [f"cd {sDir}"] + listCmds)
        iExitCode, sOutput = await asyncio.to_thread(
            dictCtx["docker"].ftResultExecuteCommand,
            sContainerId, sFullCmd,
        )
        bPassed = iExitCode == 0
        dictStep.setdefault("dictVerification", {})
        dictStep["dictVerification"]["sUnitTest"] = (
            "passed" if bPassed else "failed")
        return {
            "bPassed": bPassed,
            "sOutput": sOutput,
            "iExitCode": iExitCode,
        }


def _fnRemoveTestFiles(
    connectionDocker, sContainerId, dictStep, iStepIndex,
):
    """Remove generated test file from the container."""
    from .pipelineRunner import fsShellQuote
    from .testGenerator import fsTestFilePath

    sDirectory = dictStep.get("sDirectory", "")
    sPath = fsTestFilePath(sDirectory, iStepIndex)
    connectionDocker.ftResultExecuteCommand(
        sContainerId, f"rm -f {fsShellQuote(sPath)}"
    )


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
            "https://cdn.jsdelivr.net; "
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
    app.add_middleware(SecurityHeadersMiddleware)
    dictCtx = fdictBuildContext(_fconnectionCreateDocker())
    dictCtx["sSessionToken"] = sSessionToken
    dictCtx["setAllowedContainers"] = app.state.setAllowedContainers
    _fnRegisterAllRoutes(app, dictCtx, sWorkspaceRoot)
    return app
