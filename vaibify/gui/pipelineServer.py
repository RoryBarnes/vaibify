"""FastAPI application with REST and WebSocket routes for workflow viewing."""

import asyncio
import json
import os
import posixpath

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
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
    saTestFiles: List[str] = []
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
    saTestFiles: Optional[List[str]] = None
    saPlotCommands: Optional[List[str]] = None
    saPlotFiles: Optional[List[str]] = None
    dictVerification: Optional[dict] = None


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
        saTestFiles=request.saTestFiles,
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
    sFallback = posixpath.join(sWorkflowDirectory, sWorkdir, sFilePath)
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
    """Parse a JSON text message and resize if requested."""
    dictData = json.loads(sText)
    if dictData.get("sType") == "resize":
        session.fnResize(dictData["iRows"], dictData["iColumns"])


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


def fdictHandleConnect(dictCtx, sContainerId, sWorkflowPath):
    """Load workflow, cache it, return connection response."""
    try:
        dictWorkflow = workflowManager.fdictLoadWorkflowFromContainer(
            dictCtx["docker"], sContainerId, sWorkflowPath
        )
        dictCtx["workflows"][sContainerId] = dictWorkflow
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
        raise HTTPException(
            400, f"Failed to load workflow.json: {error}"
        )


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
        dictCtx["require"]()
        sAbsPath = (
            sDirectoryPath if sDirectoryPath.startswith("/")
            else f"{sWorkspaceRoot}/{sDirectoryPath}"
        )
        fnValidatePathWithinRoot(sAbsPath, sWorkspaceRoot)
        return flistQueryDirectory(
            dictCtx["docker"], sContainerId, sAbsPath
        )


def _fnRegisterMonitor(app):
    """Register GET /api/monitor route."""

    @app.get("/api/monitor/{sContainerId}")
    async def fnGetMonitorStats(sContainerId: str):
        return fdictGetContainerStats(sContainerId)


def _fnRegisterReproStubs(app):
    """Register reproducibility stub routes (501)."""

    @app.post("/api/overleaf/{sContainerId}/push")
    async def fnOverleafPush(sContainerId: str):
        raise HTTPException(501, "Not Implemented")

    @app.post("/api/overleaf/{sContainerId}/pull")
    async def fnOverleafPull(sContainerId: str):
        raise HTTPException(501, "Not Implemented")

    @app.post("/api/zenodo/{sContainerId}/archive")
    async def fnZenodoArchive(sContainerId: str):
        raise HTTPException(501, "Not Implemented")

    @app.post("/api/latex/{sContainerId}/generate")
    async def fnLatexGenerate(sContainerId: str):
        raise HTTPException(501, "Not Implemented")


def _fnRegisterFileWrite(app, dictCtx, sWorkspaceRoot):
    """Register PUT /api/file route for saving edited text files."""

    @app.put("/api/file/{sContainerId}/{sFilePath:path}")
    async def fnWriteFile(
        sContainerId: str, sFilePath: str,
        request: FileWriteRequest,
    ):
        dictCtx["require"]()
        sAbsPath = (
            sFilePath if sFilePath.startswith("/")
            else posixpath.join(sWorkspaceRoot, sFilePath)
        )
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
        dictCtx["require"]()
        sDir = dictCtx["workflowDir"](sContainerId)
        sAbsPath = fsResolveFigurePath(sDir, sFilePath)
        try:
            fbaFetchFigureWithFallback(
                dictCtx["docker"], sContainerId, sAbsPath,
                sDir, sWorkdir, sFilePath,
            )
            return Response(status_code=200)
        except HTTPException:
            raise

    @app.get("/api/figure/{sContainerId}/{sFilePath:path}")
    async def fnServeFigure(
        sContainerId: str, sFilePath: str, sWorkdir: str = ""
    ):
        dictCtx["require"]()
        sDir = dictCtx["workflowDir"](sContainerId)
        sAbsPath = fsResolveFigurePath(sDir, sFilePath)
        baContent = fbaFetchFigureWithFallback(
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


def _fnRegisterPipelineWs(app, dictCtx):
    """Register pipeline WebSocket endpoint."""

    @app.websocket("/ws/pipeline/{sContainerId}")
    async def fnPipelineWs(websocket: WebSocket, sContainerId: str):
        dictCtx["require"]()
        await fnHandlePipelineWs(websocket, dictCtx, sContainerId)


def _fnRegisterTerminalWs(app, dictCtx):
    """Register terminal WebSocket endpoint."""

    @app.websocket("/ws/terminal/{sContainerId}")
    async def fnTerminalWs(websocket: WebSocket, sContainerId: str):
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


def _fnRegisterStaticFiles(app):
    """Register index page and static file mount."""

    @app.get("/")
    async def fnServeIndex():
        return FileResponse(
            os.path.join(STATIC_DIRECTORY, "index.html")
        )

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
        return posixpath.dirname(
            fsRequireWorkflowPath(dictPaths, sContainerId))

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
    _fnRegisterReproStubs(app)
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
            )
        except Exception as error:
            raise HTTPException(500, f"Generation failed: {error}")
        dictStep = dictWorkflow["listSteps"][iStepIndex]
        dictStep["saTestCommands"] = dictResult["saTestCommands"]
        dictStep["saTestFiles"] = dictResult["saTestFiles"]
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
            dictCtx["docker"], sContainerId, dictStep
        )
        dictStep["saTestCommands"] = []
        dictStep["saTestFiles"] = []
        dictCtx["save"](sContainerId, dictWorkflow)
        return {"bSuccess": True}


def _fnRemoveTestFiles(connectionDocker, sContainerId, dictStep):
    """Remove test files from the container."""
    from .pipelineRunner import fsShellQuote

    for sPath in dictStep.get("saTestFiles", []):
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
    _fnRegisterFigure(app, dictCtx)
    _fnRegisterUserInfo(app)
    _fnRegisterPipelineWs(app, dictCtx)
    _fnRegisterTerminalWs(app, dictCtx)
    _fnRegisterStaticFiles(app)


def fappCreateApplication(
    sWorkspaceRoot="/workspace", sTerminalUserArg=None,
):
    """Build and return the configured FastAPI application."""
    global sTerminalUser
    sTerminalUser = sTerminalUserArg
    app = FastAPI(title="Vaibify Workflow Viewer")
    dictCtx = fdictBuildContext(_fconnectionCreateDocker())
    _fnRegisterAllRoutes(app, dictCtx, sWorkspaceRoot)
    return app
