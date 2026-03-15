"""FastAPI application with REST and WebSocket routes for pipeline viewing."""

import asyncio
import json
import os
import posixpath

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from typing import List, Optional

from . import sceneManager
from .figureServer import fsMimeTypeForFile
from .pipelineRunner import (
    fnRunAllScenes,
    fnRunFromScene,
    fnRunSelectedScenes,
    fnVerifyOnly,
)
from .resourceMonitor import fdictGetContainerStats
from .terminalSession import TerminalSession


STATIC_DIRECTORY = os.path.join(os.path.dirname(__file__), "static")

sTerminalUser = None


class SceneCreateRequest(BaseModel):
    sName: str
    sDirectory: str
    bPlotOnly: bool = True
    saSetupCommands: List[str] = []
    saCommands: List[str] = []
    saOutputFiles: List[str] = []


class SceneUpdateRequest(BaseModel):
    sName: Optional[str] = None
    sDirectory: Optional[str] = None
    bPlotOnly: Optional[bool] = None
    bEnabled: Optional[bool] = None
    saSetupCommands: Optional[List[str]] = None
    saCommands: Optional[List[str]] = None
    saOutputFiles: Optional[List[str]] = None


class ReorderRequest(BaseModel):
    iFromIndex: int
    iToIndex: int


class ScriptSettingsRequest(BaseModel):
    sPlotDirectory: Optional[str] = None
    sFigureType: Optional[str] = None
    iNumberOfCores: Optional[int] = None


class RunRequest(BaseModel):
    listSceneIndices: List[int] = []
    iStartScene: Optional[int] = None


def fdictExtractSettings(dictScript):
    """Return the settings subset from a script dict."""
    return {
        "sPlotDirectory": dictScript.get("sPlotDirectory", "Plot"),
        "sFigureType": dictScript.get("sFigureType", "pdf"),
        "iNumberOfCores": dictScript.get("iNumberOfCores", -1),
    }


def fdictFilterNonNone(dictSource):
    """Return a dict with only the non-None values."""
    return {k: v for k, v in dictSource.items() if v is not None}


def fdictSceneFromRequest(request):
    """Build a scene dict from a SceneCreateRequest."""
    return sceneManager.fdictCreateScene(
        sName=request.sName,
        sDirectory=request.sDirectory,
        bPlotOnly=request.bPlotOnly,
        saSetupCommands=request.saSetupCommands,
        saCommands=request.saCommands,
        saOutputFiles=request.saOutputFiles,
    )


def fdictRequireScript(dictScriptCache, sContainerId):
    """Return cached script or raise 404."""
    dictScript = dictScriptCache.get(sContainerId)
    if not dictScript:
        raise HTTPException(404, "Not connected to container")
    return dictScript


def fsResolveScriptPath(connectionDocker, sContainerId, sScriptPath):
    """Resolve script path via discovery if not provided."""
    if sScriptPath is not None:
        return sScriptPath
    listPaths = sceneManager.flistFindScriptsInContainer(
        connectionDocker, sContainerId
    )
    return listPaths[0] if listPaths else None


def fsResolveFigurePath(sScriptDirectory, sFilePath):
    """Return absolute path for a figure file."""
    if sFilePath.startswith("/"):
        return sFilePath
    return posixpath.join(sScriptDirectory, sFilePath)


def fbaFetchFigureWithFallback(
    connectionDocker, sContainerId, sAbsPath,
    sScriptDirectory, sWorkdir, sFilePath,
):
    """Try primary path, then fallback with sWorkdir prefix."""
    try:
        return connectionDocker.fbaFetchFile(sContainerId, sAbsPath)
    except Exception:
        pass
    if sWorkdir and not sFilePath.startswith("/"):
        return _fbaFetchFallback(
            connectionDocker, sContainerId,
            sScriptDirectory, sWorkdir, sFilePath,
        )
    raise HTTPException(404, f"Figure not found: {sAbsPath}")


def _fbaFetchFallback(
    connectionDocker, sContainerId,
    sScriptDirectory, sWorkdir, sFilePath,
):
    """Attempt to fetch figure from workdir-relative path."""
    sFallback = posixpath.join(sScriptDirectory, sWorkdir, sFilePath)
    try:
        return connectionDocker.fbaFetchFile(sContainerId, sFallback)
    except Exception as error:
        raise HTTPException(404, f"Figure not found: {error}")


def flistDirectoryEntries(connectionDocker, sContainerId, sAbsPath):
    """Run find command and return stripped output lines."""
    sCommand = (
        f"find {sAbsPath} -maxdepth 1 -mindepth 1 "
        f"\\( -type f -o -type d \\) 2>/dev/null | sort"
    )
    _, sOutput = connectionDocker.ftResultExecuteCommand(
        sContainerId, sCommand
    )
    return [s.strip() for s in sOutput.splitlines() if s.strip()]


def fdictEntryFromPath(connectionDocker, sContainerId, sPath):
    """Build a directory entry dict for a single path."""
    _, sTypeOutput = connectionDocker.ftResultExecuteCommand(
        sContainerId, f"test -d {sPath} && echo d || echo f",
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
    sScriptDirectory, fnCallback,
):
    """Dispatch runFrom with the start scene from the request."""
    await fnRunFromScene(
        connectionDocker, sContainerId,
        dictRequest.get("iStartScene", 1),
        sScriptDirectory, fnCallback,
    )


async def fnDispatchAction(
    sAction, dictRequest, connectionDocker,
    sContainerId, dictScript, dictScriptPathCache,
    sScriptDirectory, fnCallback,
):
    """Route a WebSocket pipeline action to the correct runner."""
    if sAction == "runAll":
        await fnRunAllScenes(
            connectionDocker, sContainerId, sScriptDirectory, fnCallback)
    elif sAction == "runFrom":
        await _fnDispatchRunFrom(
            connectionDocker, sContainerId, dictRequest,
            sScriptDirectory, fnCallback)
    elif sAction == "verify":
        await fnVerifyOnly(
            connectionDocker, sContainerId, sScriptDirectory, fnCallback)
    elif sAction == "runSelected":
        await _fnDispatchSelected(
            connectionDocker, sContainerId, dictRequest,
            dictScript, dictScriptPathCache, sScriptDirectory, fnCallback)


async def _fnDispatchSelected(
    connectionDocker, sContainerId, dictRequest,
    dictScript, dictScriptPathCache,
    sScriptDirectory, fnCallback,
):
    """Dispatch the runSelected action."""
    await fnRunSelectedScenes(
        connectionDocker, sContainerId,
        dictRequest.get("listSceneIndices", []),
        dictScript, dictScriptPathCache.get(sContainerId),
        sScriptDirectory, fnCallback,
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
    dictScript, dictScriptPathCache, sScriptDirectory,
):
    """Receive and dispatch pipeline WebSocket messages."""
    while True:
        dictRequest = json.loads(await websocket.receive_text())

        async def fnCallback(dictEvent):
            await websocket.send_json(dictEvent)

        await fnDispatchAction(
            dictRequest.get("sAction", "runAll"),
            dictRequest, connectionDocker, sContainerId,
            dictScript, dictScriptPathCache,
            sScriptDirectory, fnCallback,
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


def fdictHandleConnect(dictCtx, sContainerId, sScriptPath):
    """Load script, cache it, return connection response."""
    try:
        dictScript = sceneManager.fdictLoadScriptFromContainer(
            dictCtx["docker"], sContainerId, sScriptPath
        )
        dictCtx["scripts"][sContainerId] = dictScript
        sResolved = fsResolveScriptPath(
            dictCtx["docker"], sContainerId, sScriptPath
        )
        dictCtx["paths"][sContainerId] = sResolved
        return {
            "sContainerId": sContainerId,
            "sScriptPath": sResolved,
            "dictScript": dictScript,
        }
    except Exception as error:
        raise HTTPException(
            400, f"Failed to load script.json: {error}"
        )


def _fnRegisterContainers(app, dictCtx):
    """Register GET /api/containers route."""

    @app.get("/api/containers")
    async def fnGetContainers():
        dictCtx["require"]()
        try:
            return dictCtx["docker"].flistGetRunningContainers()
        except Exception as error:
            raise HTTPException(500, f"Docker error: {error}")


def _fnRegisterScriptSearch(app, dictCtx):
    """Register GET /api/scripts route."""

    @app.get("/api/scripts/{sContainerId}")
    async def fnFindScripts(sContainerId: str):
        dictCtx["require"]()
        try:
            return sceneManager.flistFindScriptsInContainer(
                dictCtx["docker"], sContainerId
            )
        except Exception as error:
            raise HTTPException(500, f"Search failed: {error}")


def _fnRegisterConnect(app, dictCtx):
    """Register POST /api/connect route."""

    @app.post("/api/connect/{sContainerId}")
    async def fnConnect(
        sContainerId: str, sScriptPath: Optional[str] = None
    ):
        dictCtx["require"]()
        return fdictHandleConnect(dictCtx, sContainerId, sScriptPath)


def _fnRegisterFiles(app, dictCtx, sWorkspaceRoot):
    """Register GET /api/files route."""

    @app.get("/api/files/{sContainerId}/{sDirectoryPath:path}")
    async def fnListDirectory(sContainerId: str, sDirectoryPath: str):
        dictCtx["require"]()
        sAbsPath = (
            sDirectoryPath if sDirectoryPath.startswith("/")
            else f"{sWorkspaceRoot}/{sDirectoryPath}"
        )
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


def _fnRegisterSettingsGet(app, dictCtx):
    """Register GET /api/settings route."""

    @app.get("/api/settings/{sContainerId}")
    async def fnGetSettings(sContainerId: str):
        return fdictExtractSettings(
            fdictRequireScript(dictCtx["scripts"], sContainerId)
        )


def _fnRegisterSettingsPut(app, dictCtx):
    """Register PUT /api/settings route."""

    @app.put("/api/settings/{sContainerId}")
    async def fnUpdateSettings(
        sContainerId: str, request: ScriptSettingsRequest
    ):
        dictCtx["require"]()
        dictScript = fdictRequireScript(dictCtx["scripts"], sContainerId)
        for sKey, value in fdictFilterNonNone(
            request.model_dump()
        ).items():
            dictScript[sKey] = value
        dictCtx["save"](sContainerId, dictScript)
        return fdictExtractSettings(dictScript)


def _fnRegisterScenesList(app, dictCtx):
    """Register GET /api/scenes and validate routes."""

    @app.get("/api/scenes/{sContainerId}")
    async def fnGetScenes(sContainerId: str):
        return sceneManager.flistExtractSceneNames(
            fdictRequireScript(dictCtx["scripts"], sContainerId)
        )

    @app.get("/api/scenes/{sContainerId}/validate")
    async def fnValidateReferences(sContainerId: str):
        dictScript = fdictRequireScript(dictCtx["scripts"], sContainerId)
        return {
            "listWarnings": sceneManager.flistValidateReferences(
                dictScript
            )
        }


def _fnRegisterSceneGet(app, dictCtx):
    """Register GET /api/scenes/{id}/{index} route."""

    @app.get("/api/scenes/{sContainerId}/{iSceneIndex}")
    async def fnGetScene(sContainerId: str, iSceneIndex: int):
        dictScript = fdictRequireScript(dictCtx["scripts"], sContainerId)
        try:
            dictScene = sceneManager.fdictGetScene(
                dictScript, iSceneIndex
            )
            dictScene["saResolvedOutputFiles"] = (
                sceneManager.flistResolveOutputFiles(
                    dictScene, dictCtx["variables"](sContainerId)
                )
            )
            return dictScene
        except IndexError as error:
            raise HTTPException(404, str(error))


def _fnRegisterSceneCreate(app, dictCtx):
    """Register POST /api/scenes/{id}/create route."""

    @app.post("/api/scenes/{sContainerId}/create")
    async def fnCreateScene(
        sContainerId: str, request: SceneCreateRequest
    ):
        dictCtx["require"]()
        dictScript = fdictRequireScript(dictCtx["scripts"], sContainerId)
        dictScene = fdictSceneFromRequest(request)
        dictScript["listScenes"].append(dictScene)
        dictCtx["save"](sContainerId, dictScript)
        return {
            "iIndex": len(dictScript["listScenes"]) - 1,
            "dictScene": dictScene,
        }


def _fnRegisterSceneInsert(app, dictCtx):
    """Register POST /api/scenes/{id}/insert route."""

    @app.post("/api/scenes/{sContainerId}/insert/{iPosition}")
    async def fnInsertScene(
        sContainerId: str, iPosition: int,
        request: SceneCreateRequest,
    ):
        dictCtx["require"]()
        dictScript = fdictRequireScript(dictCtx["scripts"], sContainerId)
        dictScene = fdictSceneFromRequest(request)
        sceneManager.fnInsertScene(dictScript, iPosition, dictScene)
        dictCtx["save"](sContainerId, dictScript)
        return {
            "iIndex": iPosition,
            "dictScene": dictScene,
            "listScenes": dictScript["listScenes"],
        }


def _fnRegisterSceneUpdate(app, dictCtx):
    """Register PUT /api/scenes/{id}/{index} route."""

    @app.put("/api/scenes/{sContainerId}/{iSceneIndex}")
    async def fnUpdateScene(
        sContainerId: str, iSceneIndex: int,
        request: SceneUpdateRequest,
    ):
        dictCtx["require"]()
        dictScript = fdictRequireScript(dictCtx["scripts"], sContainerId)
        try:
            sceneManager.fnUpdateScene(
                dictScript, iSceneIndex,
                fdictFilterNonNone(request.model_dump()),
            )
        except IndexError as error:
            raise HTTPException(404, str(error))
        dictCtx["save"](sContainerId, dictScript)
        return dictScript["listScenes"][iSceneIndex]


def _fnRegisterSceneDelete(app, dictCtx):
    """Register DELETE /api/scenes/{id}/{index} route."""

    @app.delete("/api/scenes/{sContainerId}/{iSceneIndex}")
    async def fnDeleteScene(sContainerId: str, iSceneIndex: int):
        dictCtx["require"]()
        dictScript = fdictRequireScript(dictCtx["scripts"], sContainerId)
        try:
            sceneManager.fnDeleteScene(dictScript, iSceneIndex)
        except IndexError as error:
            raise HTTPException(404, str(error))
        dictCtx["save"](sContainerId, dictScript)
        return {"bSuccess": True, "listScenes": dictScript["listScenes"]}


def _fnRegisterSceneReorder(app, dictCtx):
    """Register POST /api/scenes/{id}/reorder route."""

    @app.post("/api/scenes/{sContainerId}/reorder")
    async def fnReorderScenes(
        sContainerId: str, request: ReorderRequest
    ):
        dictCtx["require"]()
        dictScript = fdictRequireScript(dictCtx["scripts"], sContainerId)
        try:
            sceneManager.fnReorderScene(
                dictScript, request.iFromIndex, request.iToIndex
            )
        except IndexError as error:
            raise HTTPException(400, str(error))
        dictCtx["save"](sContainerId, dictScript)
        return {"listScenes": dictScript["listScenes"]}


def _fnRegisterFigure(app, dictCtx):
    """Register GET /api/figure route."""

    @app.get("/api/figure/{sContainerId}/{sFilePath:path}")
    async def fnServeFigure(
        sContainerId: str, sFilePath: str, sWorkdir: str = ""
    ):
        dictCtx["require"]()
        sDir = dictCtx["scriptDir"](sContainerId)
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
    dictScript = dictCtx["scripts"].get(sContainerId)
    if not dictScript:
        await fnRejectNotConnected(websocket)
        return
    sDir = posixpath.dirname(dictCtx["paths"].get(sContainerId, ""))
    try:
        await fnPipelineMessageLoop(
            websocket, dictCtx["docker"], sContainerId,
            dictScript, dictCtx["paths"], sDir,
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


def fsRequireScriptPath(dictPaths, sContainerId):
    """Return script path or raise 404."""
    sPath = dictPaths.get(sContainerId)
    if not sPath:
        raise HTTPException(404, "Not connected to container")
    return sPath


def fdictResolveVariables(dictScripts, dictPaths, sContainerId):
    """Build resolved variable dict for a container."""
    dictScript = dictScripts.get(sContainerId)
    sPath = dictPaths.get(sContainerId)
    if not dictScript or not sPath:
        return {}
    return sceneManager.fdictBuildGlobalVariables(dictScript, sPath)


def _ftupleBuildHelpers(connectionDocker, dictScripts, dictPaths):
    """Build closure-based helper functions for the context."""

    def fnRequire():
        _fnRequireDocker(connectionDocker)

    def fnSave(sContainerId, dictScript):
        sPath = fsRequireScriptPath(dictPaths, sContainerId)
        sceneManager.fnSaveScriptToContainer(
            connectionDocker, sContainerId, dictScript, sPath)

    def fnVariables(sContainerId):
        return fdictResolveVariables(dictScripts, dictPaths, sContainerId)

    def fnScriptDir(sContainerId):
        return posixpath.dirname(
            fsRequireScriptPath(dictPaths, sContainerId))

    return fnRequire, fnSave, fnVariables, fnScriptDir


def fdictBuildContext(connectionDocker):
    """Build the shared context dict for route handlers."""
    dictScripts = {}
    dictPaths = {}
    dictTerminals = {}
    fnRequire, fnSave, fnVariables, fnScriptDir = _ftupleBuildHelpers(
        connectionDocker, dictScripts, dictPaths
    )
    return {
        "docker": connectionDocker,
        "scripts": dictScripts,
        "paths": dictPaths,
        "terminals": dictTerminals,
        "require": fnRequire,
        "save": fnSave,
        "variables": fnVariables,
        "scriptDir": fnScriptDir,
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
    _fnRegisterScriptSearch(app, dictCtx)
    _fnRegisterConnect(app, dictCtx)
    _fnRegisterFiles(app, dictCtx, sWorkspaceRoot)
    _fnRegisterMonitor(app)
    _fnRegisterReproStubs(app)
    _fnRegisterSettingsGet(app, dictCtx)
    _fnRegisterSettingsPut(app, dictCtx)


def _fnRegisterSceneRoutes(app, dictCtx):
    """Register all scene CRUD routes."""
    _fnRegisterScenesList(app, dictCtx)
    _fnRegisterSceneGet(app, dictCtx)
    _fnRegisterSceneCreate(app, dictCtx)
    _fnRegisterSceneInsert(app, dictCtx)
    _fnRegisterSceneUpdate(app, dictCtx)
    _fnRegisterSceneDelete(app, dictCtx)
    _fnRegisterSceneReorder(app, dictCtx)


def _fnRegisterAllRoutes(app, dictCtx, sWorkspaceRoot):
    """Register all API routes on the app."""
    _fnRegisterCoreRoutes(app, dictCtx, sWorkspaceRoot)
    _fnRegisterSceneRoutes(app, dictCtx)
    _fnRegisterFigure(app, dictCtx)
    _fnRegisterPipelineWs(app, dictCtx)
    _fnRegisterTerminalWs(app, dictCtx)
    _fnRegisterStaticFiles(app)


def fappCreateApplication(sWorkspaceRoot="/workspace"):
    """Build and return the configured FastAPI application."""
    app = FastAPI(title="VaibCask Pipeline Viewer")
    dictCtx = fdictBuildContext(_fconnectionCreateDocker())
    _fnRegisterAllRoutes(app, dictCtx, sWorkspaceRoot)
    return app
