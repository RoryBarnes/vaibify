"""FastAPI application with REST and WebSocket routes for workflow viewing."""

import asyncio
import json
import logging
import os
import posixpath
import re
import secrets
from contextlib import asynccontextmanager

logger = logging.getLogger("vaibify")

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from starlette.middleware.base import BaseHTTPMiddleware
from typing import List, Optional

WORKSPACE_ROOT = "/workspace"

__all__ = [
    "fappCreateApplication",
    "fappCreateHubApplication",
    "fbIsAllowedHostHeader",
    "fdictBuildContext",
    "fdictHandleConnect",
    "fnDispatchAction",
    "fnHandlePipelineWs",
    "fnPipelineMessageLoop",
    "fnRejectNotConnected",
    "fnRejectTerminalStart",
    "fnRunTerminalSession",
    "fnTerminalInputLoop",
    "fnTerminalReadLoop",
    "fnValidatePathWithinRoot",
    "fbHasAgentToken",
    "fbValidateWebSocketOrigin",
    "fsGetOriginHeader",
    "fdictExtractSettings",
    "fdictFilterNonNone",
    "fdictRequireWorkflow",
    "fdictStepFromRequest",
    "fsSanitizeExceptionForClient",
    "fsComputeStaticCacheVersion",
    "fdictDiagnoseDockerError",
    "fdictGetDockerStatus",
    "fdictRetryDockerConnection",
    "fsDetectDockerRuntime",
    "fsRequireWorkflowPath",
    "fsResolveFigurePath",
    "fsResolveWorkflowPath",
    "fdictResolveVariables",
    "flistQueryDirectory",
    "fbaFetchFigureWithFallback",
]

from . import actionCatalog
from . import agentSessionBridge
from . import workflowManager
from .figureServer import fsMimeTypeForFile
from .pipelineRunner import (
    fnRunAllSteps,
    fnRunFromStep,
    fnRunSelectedSteps,
    fnRunAllTests,
    fnVerifyOnly,
)
from .pipelineUtils import fsShellQuote
from .resourceMonitor import fdictGetContainerStats
from .terminalSession import TerminalSession


STATIC_DIRECTORY = os.path.join(os.path.dirname(__file__), "static")

_DICT_KNOWN_ERROR_PATTERNS = {
    "No such container": "Container not found. It may have stopped.",
    "not running": "Container is not running.",
    "connection refused": "Could not connect to container.",
    "timeout": "Operation timed out.",
}


def fsSanitizeExceptionForClient(exc):
    """Return a user-safe error message without leaking internal paths."""
    sRaw = str(exc)
    for sPattern, sMessage in _DICT_KNOWN_ERROR_PATTERNS.items():
        if sPattern.lower() in sRaw.lower():
            return sMessage
    return "Pipeline action failed. Check server logs for details."

sTerminalUser = None


# ---------------------------------------------------------------
# Pydantic request models (shared across route modules)
# ---------------------------------------------------------------

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
    bRunEnabled: Optional[bool] = None
    saDataCommands: Optional[List[str]] = None
    saDataFiles: Optional[List[str]] = None
    saTestCommands: Optional[List[str]] = None
    saPlotCommands: Optional[List[str]] = None
    saPlotFiles: Optional[List[str]] = None
    saDependencies: Optional[List[str]] = None
    dictVerification: Optional[dict] = None
    dictTests: Optional[dict] = None
    dictRunStats: Optional[dict] = None
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
    bAutoArchive: Optional[bool] = None


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
    bForceOverwrite: bool = False


class FileUploadRequest(BaseModel):
    sFilename: str
    sDestination: str = "/workspace"
    sContentBase64: str


class FilePullRequest(BaseModel):
    sContainerPath: str
    sHostDestination: str


class SyncPushRequest(BaseModel):
    listFilePaths: List[str]
    sCommitMessage: str = "[vaibify] Update outputs"
    sTargetDirectory: Optional[str] = None


class OverleafDiffRequest(BaseModel):
    listFilePaths: List[str]
    sTargetDirectory: str


class GitAddFileRequest(BaseModel):
    sFilePath: str
    sCommitMessage: str = "[vaibify] Add data file"


class SyncSetupRequest(BaseModel):
    sService: str
    sProjectId: Optional[str] = None
    sToken: Optional[str] = None
    sZenodoInstance: Optional[str] = None


class SyncTrackingRequest(BaseModel):
    sPath: str
    sService: str
    bTrack: bool


class ZenodoCreatorRequest(BaseModel):
    sName: str
    sAffiliation: Optional[str] = ""
    sOrcid: Optional[str] = ""


class ZenodoMetadataRequest(BaseModel):
    sTitle: str
    sDescription: Optional[str] = ""
    listCreators: List[ZenodoCreatorRequest] = []
    sLicense: Optional[str] = "CC-BY-4.0"
    listKeywords: List[str] = []
    sRelatedGithubUrl: Optional[str] = ""


class CreateWorkflowRequest(BaseModel):
    sWorkflowName: str
    sFileName: str
    sRepoDirectory: str


class SaveAndRunTestRequest(BaseModel):
    sContent: str
    sFilePath: str


class DatasetDownloadRequest(BaseModel):
    iRecordId: int
    sFileName: str
    sDestination: str


# ---------------------------------------------------------------
# Shared utility functions
# ---------------------------------------------------------------

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
        "bAutoArchive": dictWorkflow.get("bAutoArchive", False),
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


# ---------------------------------------------------------------
# Pipeline WebSocket / dispatch functions
# ---------------------------------------------------------------

async def _fnDispatchRunFrom(
    connectionDocker, sContainerId, dictRequest,
    dictWorkflow, sWorkflowDirectory, fnCallback,
    dictInteractive=None,
):
    """Dispatch runFrom with the start step from the request."""
    iStartStep = _fiResolveStartStep(dictRequest, dictWorkflow)
    await fnRunFromStep(
        connectionDocker, sContainerId,
        iStartStep, sWorkflowDirectory, fnCallback,
        dictInteractive=dictInteractive,
    )


def _fiResolveStartStep(dictRequest, dictWorkflow):
    """Return the 1-based start step from index or label in the request.

    ``iStartStep`` is 1-based to match the pipeline runner's convention.
    A ``sStartStepLabel`` like ``"A09"`` resolves to the 0-based index,
    then +1 for the 1-based caller.
    """
    from .pipelineUtils import fiStepIndexFromLabel
    sLabel = dictRequest.get("sStartStepLabel")
    if sLabel:
        return fiStepIndexFromLabel(dictWorkflow, sLabel) + 1
    return dictRequest.get("iStartStep", 1)


def _flistResolveSelectedIndices(dictRequest, dictWorkflow):
    """Return the resolved, deduplicated list of 0-based step indices.

    Accepts ``listStepIndices`` (ints) and ``listStepLabels`` (strings
    like ``"A09"``) together; labels translate via
    ``fiStepIndexFromLabel``. Order follows indices-first then labels.
    """
    from .pipelineUtils import fiStepIndexFromLabel
    listOut = []
    setSeen = set()
    for iValue in dictRequest.get("listStepIndices", []):
        iIndex = int(iValue)
        if iIndex not in setSeen:
            listOut.append(iIndex)
            setSeen.add(iIndex)
    for sLabel in dictRequest.get("listStepLabels", []):
        iIndex = fiStepIndexFromLabel(dictWorkflow, sLabel)
        if iIndex not in setSeen:
            listOut.append(iIndex)
            setSeen.add(iIndex)
    return listOut


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
            dictWorkflow, sWorkflowDirectory, fnCallback,
            dictInteractive=dictInteractive)
    elif sAction == "verify":
        await fnVerifyOnly(
            connectionDocker, sContainerId, sWorkflowDirectory, fnCallback)
    elif sAction == "runAllTests":
        await fnRunAllTests(
            connectionDocker, sContainerId, sWorkflowDirectory, fnCallback,
            dictWorkflow=dictWorkflow)
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
    from .pipelineRunner import SET_VALID_RUN_MODES
    listIndices = _flistResolveSelectedIndices(
        dictRequest, dictWorkflow,
    )
    sRunMode = dictRequest.get("sRunMode", "full")
    if sRunMode not in SET_VALID_RUN_MODES:
        raise ValueError(
            f"Unknown sRunMode: {sRunMode!r}. "
            f"Valid values: {sorted(SET_VALID_RUN_MODES)}"
        )
    await fnRunSelectedSteps(
        connectionDocker, sContainerId,
        listIndices,
        dictWorkflow, dictWorkflowPathCache.get(sContainerId),
        sWorkflowDirectory, fnCallback,
        sRunMode=sRunMode,
    )


async def fnPipelineMessageLoop(
    websocket, connectionDocker, sContainerId,
    dictWorkflow, dictWorkflowPathCache, sWorkflowDirectory,
    dictPipelineTasks=None,
):
    """Receive and dispatch pipeline WebSocket messages."""
    from .pipelineRunner import (
        fdictCreateInteractiveContext,
        fnSetInteractiveResponse,
    )
    dictInteractive = fdictCreateInteractiveContext()

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
            _fnSafeDispatch(
                sAction, dictRequest, connectionDocker,
                sContainerId, dictWorkflow,
                dictWorkflowPathCache, sWorkflowDirectory,
                fnCallback, dictInteractive,
            )
        )
        if dictPipelineTasks is not None:
            dictPipelineTasks[sContainerId] = taskPipeline


async def _fnSafeDispatch(
    sAction, dictRequest, connectionDocker,
    sContainerId, dictWorkflow, dictWorkflowPathCache,
    sWorkflowDirectory, fnCallback, dictInteractive,
):
    """Wrap fnDispatchAction with error handling."""
    try:
        await fnDispatchAction(
            sAction, dictRequest, connectionDocker,
            sContainerId, dictWorkflow,
            dictWorkflowPathCache, sWorkflowDirectory,
            fnCallback, dictInteractive=dictInteractive,
        )
    except Exception as exc:
        logger.error(
            "Pipeline action '%s' failed: %s", sAction, exc,
            exc_info=True,
        )
        try:
            await fnCallback({
                "sType": "failed",
                "iExitCode": 1,
                "sMessage": fsSanitizeExceptionForClient(exc),
            })
        except Exception:
            pass


def _fnHandleInteractiveResponse(
    dictInteractive, sAction, dictRequest,
):
    """Set the resume/skip response on the interactive context."""
    from .pipelineRunner import fnSetInteractiveResponse
    if sAction == "interactiveResume":
        fnSetInteractiveResponse(dictInteractive, "resume")
    elif sAction == "interactiveSkip":
        fnSetInteractiveResponse(dictInteractive, "skip")


def _fnHandleInteractiveComplete(dictInteractive, dictRequest):
    """Signal that the interactive terminal command finished."""
    from .pipelineRunner import fnSetInteractiveResponse
    iExitCode = dictRequest.get("iExitCode", 0)
    fnSetInteractiveResponse(
        dictInteractive, f"complete:{iExitCode}",
    )


# ---------------------------------------------------------------
# Terminal session functions
# ---------------------------------------------------------------

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


# ---------------------------------------------------------------
# Pipeline WebSocket handler
# ---------------------------------------------------------------

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
            dictPipelineTasks=dictCtx["pipelineTasks"],
        )
    except WebSocketDisconnect:
        pass


# ---------------------------------------------------------------
# Container connection helpers
# ---------------------------------------------------------------

def _fsResolveContainerUser(dictCtx, sContainerId):
    """Query the container for its built-in user."""
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
    _fnPushAgentSession(dictCtx, sContainerId)


def _fnPushAgentSession(dictCtx, sContainerId):
    """Write the vaibify-do session + catalog into the container."""
    try:
        agentSessionBridge.fnPushAgentSessionToContainer(
            dictCtx["docker"], sContainerId,
            dictCtx["sSessionToken"], dictCtx.get("iPort", 0),
        )
    except Exception as error:
        logger.warning(
            "Agent session push failed for %s: %s",
            sContainerId, error,
        )


def _fdictConnectNoWorkflow(dictCtx, sContainerId):
    """Return response for no-workflow mode."""
    _fnAuthorizeContainer(dictCtx, sContainerId)
    return {
        "sContainerId": sContainerId,
        "sWorkflowPath": None,
        "dictWorkflow": None,
    }


async def _fnScanDependenciesBackground(
    dictCtx, sContainerId, dictWorkflow,
):
    """Scan source-code dependencies and cache results."""
    from .routes.scriptRoutes import fdictScanAllDependencies
    try:
        dictDeps = await fdictScanAllDependencies(
            dictCtx, sContainerId, dictWorkflow,
        )
        dictCtx["sourceCodeDeps"][sContainerId] = dictDeps
        _fnAnnotateStepsWithDeps(dictWorkflow, dictDeps)
    except Exception as error:
        logger.warning("Source-code dep scan failed: %s", error)


def _fnAnnotateStepsWithDeps(dictWorkflow, dictDeps):
    """Add saSourceCodeDeps to each step from scan results."""
    listSteps = dictWorkflow.get("listSteps", [])
    dictDownToUp = _fdictInvertDeps(dictDeps, len(listSteps))
    for iStep, dictStep in enumerate(listSteps):
        listUpstream = sorted(dictDownToUp.get(iStep, set()))
        dictStep["saSourceCodeDeps"] = [
            i + 1 for i in listUpstream
        ]


def _fdictInvertDeps(dictUpToDown, iStepCount):
    """Invert {upstream: set(downstream)} to {downstream: set(upstream)}."""
    dictResult = {}
    for iUpstream, setDownstream in dictUpToDown.items():
        for iDown in setDownstream:
            dictResult.setdefault(iDown, set()).add(iUpstream)
    return dictResult


async def fdictHandleConnect(dictCtx, sContainerId, sWorkflowPath):
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
        from . import containerGit
        dictWorkflow["sProjectRepoPath"] = (
            containerGit.fsDetectProjectRepoInContainer(
                dictCtx["docker"], sContainerId, sResolved,
            )
        )
        if workflowManager.fnMigrateArchiveToTracking(dictWorkflow):
            dictCtx["save"](sContainerId, dictWorkflow)
        if workflowManager.fbMigrateModifiedFilesToRepoRelative(
            dictWorkflow,
        ):
            dictCtx["save"](sContainerId, dictWorkflow)
        _fnLaunchDependencyScan(
            dictCtx, sContainerId, dictWorkflow,
        )
        dictFileStatus = await _fdictComputeConnectFileStatus(
            dictCtx, sContainerId, dictWorkflow,
        )
        from .pipelineUtils import fdictWorkflowWithLabels
        return {
            "sContainerId": sContainerId,
            "sWorkflowPath": sResolved,
            "dictWorkflow": fdictWorkflowWithLabels(dictWorkflow),
            "dictFileStatus": dictFileStatus,
        }
    except Exception as error:
        logger.error("Workflow load failed: %s", error)
        sUserMessage = _fsSanitizeServerError(str(error))
        raise HTTPException(400, sUserMessage)


async def _fdictComputeConnectFileStatus(
    dictCtx, sContainerId, dictWorkflow,
):
    """Compute file-status payload for the connect response."""
    from .routes.pipelineRoutes import fdictComputeFileStatus
    try:
        dictVars = dictCtx["variables"](sContainerId)
        return await fdictComputeFileStatus(
            dictCtx, sContainerId, dictWorkflow, dictVars,
        )
    except Exception as error:
        logger.warning(
            "Connect file-status precompute failed: %s", error,
        )
        return None


def _fnLaunchDependencyScan(
    dictCtx, sContainerId, dictWorkflow,
):
    """Schedule background source-code dependency scan."""
    try:
        loop = asyncio.get_running_loop()
        loop.create_task(
            _fnScanDependenciesBackground(
                dictCtx, sContainerId, dictWorkflow,
            )
        )
    except RuntimeError:
        logger.debug("No event loop for dependency scan")


# ---------------------------------------------------------------
# Docker runtime detection
# ---------------------------------------------------------------

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


# ---------------------------------------------------------------
# WebSocket origin validation
# ---------------------------------------------------------------

def fbValidateWebSocketOrigin(websocket: WebSocket, sExpectedToken=None):
    """Return True if the WebSocket carries a trusted origin or agent token.

    Browser clients identify themselves by a loopback ``Origin`` header.
    In-container ``vaibify-do`` agents dial in via
    ``host.docker.internal`` and can't set a loopback origin, so they
    authenticate by presenting the backend's session token in the
    ``X-Vaibify-Session`` header or ``sToken`` query parameter; when
    that matches, origin validation is bypassed because the token is
    already the authoritative credential.
    """
    if sExpectedToken and fbHasAgentToken(websocket, sExpectedToken):
        return True
    sOrigin = fsGetOriginHeader(websocket)
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


def fsGetOriginHeader(websocket: WebSocket):
    """Return the Origin header value or empty string."""
    for sKey, sVal in websocket.headers.items():
        if sKey.lower() == "origin":
            return sVal
    return ""


def fbHasAgentToken(websocket: WebSocket, sExpectedToken):
    """Return True if the WS carries the expected agent token."""
    sHeaderToken = ""
    sHeaderName = actionCatalog.S_SESSION_HEADER_NAME.lower()
    for sKey, sVal in websocket.headers.items():
        if sKey.lower() == sHeaderName:
            sHeaderToken = sVal
            break
    if sHeaderToken and sHeaderToken == sExpectedToken:
        return True
    sQueryToken = websocket.query_params.get("sToken", "")
    return bool(sQueryToken) and sQueryToken == sExpectedToken


# ---------------------------------------------------------------
# Static file serving
# ---------------------------------------------------------------

def fsComputeStaticCacheVersion():
    """Return a version string derived from static file mtimes."""
    iMaxMtime = 0
    for sName in os.listdir(STATIC_DIRECTORY):
        sPath = os.path.join(STATIC_DIRECTORY, sName)
        if os.path.isfile(sPath) and sName != "index.html":
            iMtime = int(os.path.getmtime(sPath))
            if iMtime > iMaxMtime:
                iMaxMtime = iMtime
    return str(iMaxMtime)


def _fnRegisterStaticFiles(app, dictCtx):
    """Register index page, token endpoint, and static file mount."""

    @app.get("/")
    async def fnServeIndex():
        sIndexPath = os.path.join(STATIC_DIRECTORY, "index.html")
        with open(sIndexPath, "r") as fileIndex:
            sContent = fileIndex.read()
        sVersion = fsComputeStaticCacheVersion()
        sContent = sContent.replace("__CACHE_VERSION__", sVersion)
        return Response(
            content=sContent,
            media_type="text/html",
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


# ---------------------------------------------------------------
# Re-exports from fileStatusManager and testStatusManager
# ---------------------------------------------------------------

from .fileStatusManager import (  # noqa: F401
    _fbAnyDataFileChanged,
    _fbAnyMtimeNewerThan,
    _fbAnyPlotFileChanged,
    _fbCheckStaleUserVerification,
    _fbPipelineIsRunning,
    _fbPlotNewerThanUserVerification,
    _fbStepIsPencilStale,
    _fdictBuildFileStatusVars,
    _fdictBuildScriptStatus,
    _fdictComputeMaxMtimeByStep,
    _fdictComputeMaxPlotMtimeByStep,
    _fdictDetectChangedFiles,
    _fdictFindChangedFiles,
    _fdictGetModTimes,
    _fdictInvalidateAffectedSteps,
    _fiParseUtcTimestamp,
    _flistCollectOutputPaths,
    _flistDetectAndInvalidate,
    _flistResolvePlotPaths,
    _flistResolveStepPaths,
    _fnClearStepModificationState,
    _fnInvalidateDownstreamStep,
    _fnInvalidateStepFiles,
    _fnUpdateModTimeBaseline,
    fbAllStepsFullyVerified,
    fbIsStepFullyVerified,
    fbReconcileUpstreamFlags,
    fbReconcileUserVerificationTimestamps,
    fdictCollectOutputPathsByStep,
    flistStepRemoteFiles,
    fnCollectMarkerPathsByStep,
    fnCollectScriptPathsByStep,
    fnMaybeAutoArchive,
    fsMarkerNameFromStepDirectory,
)

from .testStatusManager import (  # noqa: F401
    _LIST_TEST_CATEGORIES,
    _fdictBuildTestResponse,
    _flistResolveTestCommands,
    _fnClearDownstreamUpstreamFlags,
    _fnRecordTestResult,
    _fnRegisterTestCommand,
    _fnRemoveTestDirectory,
    _fnRemoveTestFiles,
    _fnUpdateAggregateTestState,
    _fsBuildPytestCommand,
)


# ---------------------------------------------------------------
# Lazy re-exports from route modules (backward compatibility)
# ---------------------------------------------------------------

_DICT_ROUTE_RE_EXPORTS = {
    # pipelineRoutes
    "_fbCancelPipelineTask": "routes.pipelineRoutes",
    "_fbMarkerStale": "routes.pipelineRoutes",
    "_fdictBuildTestFileChanges": "routes.pipelineRoutes",
    "_fdictBuildTestMarkerStatus": "routes.pipelineRoutes",
    "_flistBuildCleanCommands": "routes.pipelineRoutes",
    "_flistExtractKillPatterns": "routes.pipelineRoutes",
    "_flistExtractStepDirectories": "routes.pipelineRoutes",
    "_flistFindCustomTestFiles": "routes.pipelineRoutes",
    "_fnApplyAllMarkerCategories": "routes.pipelineRoutes",
    "_fnApplyExternalTestResults": "routes.pipelineRoutes",
    "_fnApplyMarkerCategory": "routes.pipelineRoutes",
    "_fnMarkPipelineStopped": "routes.pipelineRoutes",
    "_fsetExtractRegisteredTestFiles": "routes.pipelineRoutes",
    # syncRoutes
    "_fdictBuildOverleafArgs": "routes.syncRoutes",
    # scriptRoutes
    "_fdictFindStemMatch": "routes.scriptRoutes",
    "_flistCollectUpstreamOutputs": "routes.scriptRoutes",
    "_flistFilterOwnOutputs": "routes.scriptRoutes",
    "_fnClassifyDetectedItem": "routes.scriptRoutes",
    "_fnStoreCommitHash": "routes.scriptRoutes",
    "_fsJoinStepPath": "routes.scriptRoutes",
    "_fsResolveLanguage": "routes.scriptRoutes",
    "_fsetCollectCurrentStepOutputs": "routes.scriptRoutes",
    # testRoutes
    "_fbNeedsClaudeFallback": "routes.testRoutes",
    "_fdictBuildGenerateResponse": "routes.testRoutes",
    "_fdictRunAllTestCategories": "routes.testRoutes",
    "_fdictRunOneTestCategory": "routes.testRoutes",
    "_fdictRunTestGeneration": "routes.testRoutes",
    "_fnApplyGeneratedTests": "routes.testRoutes",
    # plotRoutes
    "_fdictCheckStandardsExist": "routes.plotRoutes",
    "_flistConvertToStandards": "routes.plotRoutes",
    "_flistStandardizedBasenames": "routes.plotRoutes",
    "_flistVerifyConverted": "routes.plotRoutes",
    "_fsFindPlotPath": "routes.plotRoutes",
    "_fsFindStandardForFile": "routes.plotRoutes",
    # figureRoutes
    "_flistBuildFigureCheckPaths": "routes.figureRoutes",
    # fileRoutes
    "_fnDockerCopy": "routes.fileRoutes",
    "_fnValidateHostDestination": "routes.fileRoutes",
    # workflowRoutes
    "_fnRejectDuplicateWorkflowName": "routes.workflowRoutes",
    "_fsValidateRepoDirectory": "routes.workflowRoutes",
}


def __getattr__(sName):
    """Lazily import re-exported symbols from route modules."""
    if sName in _DICT_ROUTE_RE_EXPORTS:
        import importlib
        sModule = _DICT_ROUTE_RE_EXPORTS[sName]
        module = importlib.import_module(
            f".{sModule}", package="vaibify.gui"
        )
        value = getattr(module, sName)
        globals()[sName] = value
        return value
    raise AttributeError(
        f"module {__name__!r} has no attribute {sName!r}"
    )


# ---------------------------------------------------------------
# Application context builder
# ---------------------------------------------------------------

def _fnRequireDocker(connectionDocker):
    """Raise 503 if Docker is unavailable, with a specific diagnosis."""
    if connectionDocker is not None:
        return
    sDetail = _fsBuildDockerUnavailableDetail()
    raise HTTPException(503, sDetail)


def _fsBuildDockerUnavailableDetail():
    """Compose the 503 detail string from the cached diagnosis."""
    sError = _dictDockerStatus.get("sError", "")
    sHint = _dictDockerStatus.get("sHint", "")
    sCommand = _dictDockerStatus.get("sCommand", "")
    sDetail = "Docker support is not available."
    if sHint:
        sDetail += " " + sHint
    if sCommand:
        sDetail += " Try: " + sCommand
    if sError:
        sDetail += " (cause: " + sError + ")"
    return sDetail


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


def _ftupleBuildHelpers(dictRaw, dictWorkflows, dictPaths):
    """Build closure-based helper functions for the context.

    Closures look up ``dictRaw["docker"]`` dynamically rather than
    capturing the connection at build time, so a runtime swap (after a
    successful ``/api/system/docker-status/retry``) is visible to all
    routes without restarting vaibify.
    """

    def fnRequire():
        _fnRequireDocker(dictRaw["docker"])

    def fnSave(sContainerId, dictWorkflow):
        sPath = fsRequireWorkflowPath(dictPaths, sContainerId)
        workflowManager.fnSaveWorkflowToContainer(
            dictRaw["docker"], sContainerId, dictWorkflow, sPath)

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
    """Build the shared context for route handlers.

    Returns a RouteContext that supports both attribute access
    (``dictCtx.docker``) and dict access (``dictCtx["docker"]``)
    for backward compatibility.
    """
    from .routeContext import RouteContext

    dictWorkflows = {}
    dictPaths = {}
    dictTerminals = {}
    dictRaw = {
        "docker": connectionDocker,
        "workflows": dictWorkflows,
        "paths": dictPaths,
        "terminals": dictTerminals,
        "containerUsers": {},
        "pipelineTasks": {},
        "sourceCodeDeps": {},
    }
    fnRequire, fnSave, fnVariables, fnWorkflowDir = _ftupleBuildHelpers(
        dictRaw, dictWorkflows, dictPaths
    )
    dictRaw["require"] = fnRequire
    dictRaw["save"] = fnSave
    dictRaw["variables"] = fnVariables
    dictRaw["workflowDir"] = fnWorkflowDir
    return RouteContext(dictRaw)


_dictDockerStatus = {"sError": "", "sHint": "", "sCommand": ""}


def _fconnectionCreateDocker():
    """Lazily create a DockerConnection or return None.

    Failures are captured into ``_dictDockerStatus`` so the 503 path
    and the ``/api/system/docker-status`` probe can surface a specific
    diagnosis instead of a generic 'Docker support is not available'
    toast that leaves the user guessing whether the daemon, the
    runtime, or the binary is at fault.
    """
    try:
        from ..docker.dockerConnection import DockerConnection
        connection = DockerConnection()
    except Exception as error:
        _fnRecordDockerError(str(error) or repr(error))
        return None
    _fnClearDockerError()
    return connection


def _fnRecordDockerError(sError):
    """Store the most recent Docker init failure for surfacing in UI."""
    dictDiagnosis = fdictDiagnoseDockerError(sError)
    _dictDockerStatus["sError"] = sError
    _dictDockerStatus["sHint"] = dictDiagnosis["sHint"]
    _dictDockerStatus["sCommand"] = dictDiagnosis["sCommand"]


def _fnClearDockerError():
    """Reset the diagnosis holder when Docker is reachable."""
    _dictDockerStatus["sError"] = ""
    _dictDockerStatus["sHint"] = ""
    _dictDockerStatus["sCommand"] = ""


def fdictDiagnoseDockerError(sError):
    """Return ``{sHint, sCommand}`` for a Docker init error string.

    Pattern-matches common runtime failures (Colima stale disk lock,
    daemon not running, docker binary missing, socket permission
    denied) and emits an actionable hint plus a copy-pasteable shell
    command. The verbatim error is always carried alongside in
    ``_dictDockerStatus`` so an unrecognized failure mode still
    reaches the user instead of being hidden behind a generic hint.
    """
    sLower = (sError or "").lower()
    if "in use by instance" in sLower:
        return {
            "sHint": "Colima's VM lock is stale, likely from an "
                     "unclean shutdown. Force-stop and restart Colima.",
            "sCommand": "colima stop --force && colima start",
        }
    if _fbErrorIsDaemonUnreachable(sLower):
        return {
            "sHint": "The Docker daemon is not reachable. Start your "
                     "Docker runtime (Colima or Docker Desktop).",
            "sCommand": "colima start",
        }
    if _fbErrorIsBinaryMissing(sLower):
        return {
            "sHint": "The 'docker' command was not found on PATH. "
                     "Install Docker Desktop or Colima.",
            "sCommand": "brew install colima docker",
        }
    if "permission denied" in sLower:
        return {
            "sHint": "Docker socket permission was denied. Restart "
                     "your runtime so the socket is recreated with "
                     "the expected ownership.",
            "sCommand": "colima restart",
        }
    return {
        "sHint": "Docker is not reachable. Verify that your Docker "
                 "runtime is running and that 'docker info' succeeds.",
        "sCommand": "docker info",
    }


def _fbErrorIsDaemonUnreachable(sLower):
    """True if the error text suggests the daemon socket is down."""
    if "cannot connect" in sLower and (
        "daemon" in sLower or "docker.sock" in sLower
    ):
        return True
    if "connection refused" in sLower and "docker" in sLower:
        return True
    return False


def _fbErrorIsBinaryMissing(sLower):
    """True if the error text suggests the 'docker' binary is absent."""
    if "filenotfounderror" in sLower:
        return True
    if "no such file or directory" in sLower and "docker" in sLower:
        return True
    if "[errno 2]" in sLower and "docker" in sLower:
        return True
    return False


def fdictGetDockerStatus():
    """Return a snapshot of the current Docker availability state."""
    return {
        "bAvailable": not _dictDockerStatus["sError"],
        "sError": _dictDockerStatus["sError"],
        "sHint": _dictDockerStatus["sHint"],
        "sCommand": _dictDockerStatus["sCommand"],
    }


def fdictRetryDockerConnection(dictCtx):
    """Re-attempt the Docker connection and swap dictCtx on success.

    Mutating ``dictCtx['docker']`` lets every route closure pick up
    the new connection without a vaibify restart, because
    ``_ftupleBuildHelpers`` reads the connection from the shared
    raw-dict at call time rather than capturing it at build time.
    """
    connectionNew = _fconnectionCreateDocker()
    dictCtx["docker"] = connectionNew
    return fdictGetDockerStatus()


# ---------------------------------------------------------------
# Route registration (delegates to route modules)
# ---------------------------------------------------------------

def _fnRegisterAllRoutes(app, dictCtx, sWorkspaceRoot):
    """Register all API routes on the app."""
    from . import routes

    routes.workflowRoutes.fnRegisterAll(app, dictCtx)
    routes.fileRoutes.fnRegisterAll(app, dictCtx, sWorkspaceRoot)
    routes.syncRoutes.fnRegisterAll(app, dictCtx)
    routes.scriptRoutes.fnRegisterAll(app, dictCtx)
    routes.settingsRoutes.fnRegisterAll(app, dictCtx)
    routes.stepRoutes.fnRegisterAll(app, dictCtx)
    routes.testRoutes.fnRegisterAll(app, dictCtx)
    routes.plotRoutes.fnRegisterAll(app, dictCtx)
    routes.figureRoutes.fnRegisterAll(app, dictCtx)
    routes.systemRoutes.fnRegisterAll(app, dictCtx)
    routes.pipelineRoutes.fnRegisterAll(app, dictCtx)
    routes.terminalRoutes.fnRegisterAll(app, dictCtx)
    routes.repoRoutes.fnRegisterAll(app, dictCtx)
    routes.gitRoutes.fnRegisterAll(app, dictCtx)
    routes.sessionRoutes.fnRegisterAll(app, dictCtx)
    _fnRegisterStaticFiles(app, dictCtx)


# ---------------------------------------------------------------
# Middleware
# ---------------------------------------------------------------

_SET_LOCAL_HOST_NAMES = frozenset({"127.0.0.1", "localhost", "[::1]"})


def fbIsAllowedHostHeader(sHostHeader, iExpectedPort):
    """Return True when sHostHeader resolves to a local loopback origin.

    Guards against DNS rebinding: an attacker-controlled domain that
    has been re-pointed at 127.0.0.1 would send its original name in
    the ``Host:`` header, so rejecting anything outside the loopback
    set prevents a remote page from driving local API endpoints.
    """
    if not sHostHeader:
        return False
    sHostPort = sHostHeader.split(",", 1)[0].strip()
    sHost, sPort = _ftSplitHostPort(sHostPort)
    if sHost not in _SET_LOCAL_HOST_NAMES:
        return False
    if sPort == "":
        return True
    try:
        iPort = int(sPort)
    except ValueError:
        return False
    return iPort == iExpectedPort


def _ftSplitHostPort(sHostPort):
    """Split host and port, tolerating bracketed IPv6 and bare hosts."""
    if sHostPort.startswith("["):
        iBracket = sHostPort.find("]")
        if iBracket == -1:
            return (sHostPort, "")
        sHost = sHostPort[: iBracket + 1]
        sRest = sHostPort[iBracket + 1:]
        sPort = sRest.lstrip(":") if sRest.startswith(":") else ""
        return (sHost, sPort)
    if ":" in sHostPort:
        sHost, sPort = sHostPort.rsplit(":", 1)
        return (sHost, sPort)
    return (sHostPort, "")


class SessionTokenMiddleware(BaseHTTPMiddleware):
    """Reject requests with unsafe Host headers or missing session tokens.

    An in-container ``vaibify-do`` agent authenticates via the
    ``X-Vaibify-Session`` header and reaches the backend through
    ``host.docker.internal``, so requests that present a valid agent
    token bypass the browser-oriented Host-header loopback check.
    """

    async def dispatch(self, request: Request, call_next):
        sExpected = request.app.state.sSessionToken
        sAgentToken = request.headers.get(
            actionCatalog.S_SESSION_HEADER_NAME.lower(), "",
        )
        if sAgentToken and sAgentToken == sExpected:
            return await call_next(request)
        if not _fbRequestHasAllowedHost(request):
            return Response(
                status_code=400,
                content='{"detail":"Invalid Host header"}',
                media_type="application/json",
            )
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
            if sToken != sExpected:
                return Response(
                    status_code=401,
                    content='{"detail":"Unauthorized"}',
                    media_type="application/json",
                )
        return await call_next(request)


def _fbRequestHasAllowedHost(request):
    """Return True when the request Host header is a permitted loopback."""
    iExpectedPort = getattr(request.app.state, "iExpectedPort", 0)
    if not iExpectedPort:
        return True
    sHostHeader = request.headers.get("host", "")
    return fbIsAllowedHostHeader(sHostHeader, iExpectedPort)


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


# ---------------------------------------------------------------
# Application factories
# ---------------------------------------------------------------

@asynccontextmanager
async def _alifespanShared(app):
    """Single lifespan that drives every registered startup/shutdown hook.

    Modules append callables to ``app.state.listLifespanStartup`` and
    ``app.state.listLifespanShutdown`` between app construction and the
    first ASGI request. This replaces the deprecated
    ``@app.on_event("startup"/"shutdown")`` decorators (FastAPI emits
    a DeprecationWarning when those are used; mixing them with
    ``lifespan=`` is also unsupported).

    Each startup hook runs in its own ``try/except`` so a single
    failing hook cannot abort the lifespan before ``yield``; if it
    did, the shutdown loop would be skipped and resources already
    acquired by earlier hooks (e.g. background tasks, container
    locks) would leak. Shutdown hooks likewise run independently so
    one failure does not silence subsequent cleanup.
    """
    for fnStartup in list(getattr(app.state, "listLifespanStartup", [])):
        await _fnRunStartupHookSafely(fnStartup, app)
    yield
    for fnShutdown in list(getattr(app.state, "listLifespanShutdown", [])):
        await _fnRunShutdownHookSafely(fnShutdown, app)


async def _fnRunStartupHookSafely(fnHook, app):
    """Invoke a startup hook, logging any exception without re-raising."""
    try:
        await _fnInvokeMaybeAsync(fnHook, app)
    except Exception as errorAny:
        logger.warning(
            "Lifespan startup hook %s failed: %s",
            getattr(fnHook, "__name__", repr(fnHook)),
            type(errorAny).__name__,
        )


async def _fnRunShutdownHookSafely(fnHook, app):
    """Invoke a shutdown hook, logging any exception without re-raising."""
    try:
        await _fnInvokeMaybeAsync(fnHook, app)
    except Exception as errorAny:
        logger.warning(
            "Lifespan shutdown hook %s failed: %s",
            getattr(fnHook, "__name__", repr(fnHook)),
            type(errorAny).__name__,
        )


async def _fnInvokeMaybeAsync(fnHook, app):
    """Invoke a lifespan hook that may be sync or async."""
    objectResult = fnHook(app)
    if asyncio.iscoroutine(objectResult):
        await objectResult


def fappCreateApplication(
    sWorkspaceRoot="/workspace", sTerminalUserArg=None,
    iExpectedPort=0,
):
    """Build and return the configured FastAPI application.

    When ``iExpectedPort`` is non-zero, the SessionTokenMiddleware
    enforces a strict ``Host:`` header check (DNS rebinding defense).
    CLI launchers pass the real bind port; test fixtures omit the
    argument so TestClient's default ``testserver`` host is accepted.
    """
    global sTerminalUser
    sTerminalUser = sTerminalUserArg
    app = FastAPI(
        title="Vaibify Workflow Viewer", lifespan=_alifespanShared,
    )
    app.state.listLifespanStartup = []
    app.state.listLifespanShutdown = []
    sSessionToken = secrets.token_urlsafe(32)
    app.state.sSessionToken = sSessionToken
    app.state.setAllowedContainers = set()
    app.state.iExpectedPort = iExpectedPort
    app.add_middleware(SessionTokenMiddleware)
    app.add_middleware(SecurityHeadersMiddleware)
    dictCtx = fdictBuildContext(_fconnectionCreateDocker())
    dictCtx["sSessionToken"] = sSessionToken
    dictCtx["iPort"] = iExpectedPort
    dictCtx["setAllowedContainers"] = app.state.setAllowedContainers
    _fnRegisterAllRoutes(app, dictCtx, sWorkspaceRoot)
    return app


def fappCreateHubApplication(iExpectedPort=0):
    """Build a hub-mode FastAPI app with registry support.

    See :func:`fappCreateApplication` for ``iExpectedPort`` semantics.
    """
    from .registryRoutes import fnRegisterRegistryRoutes
    global sTerminalUser
    sTerminalUser = "researcher"
    app = FastAPI(title="Vaibify Hub", lifespan=_alifespanShared)
    app.state.listLifespanStartup = []
    app.state.listLifespanShutdown = []
    sSessionToken = secrets.token_urlsafe(32)
    app.state.sSessionToken = sSessionToken
    app.state.setAllowedContainers = set()
    app.state.iExpectedPort = iExpectedPort
    app.state.iHubPort = iExpectedPort
    app.state.dictContainerLocks = {}
    app.add_middleware(SessionTokenMiddleware)
    app.add_middleware(SecurityHeadersMiddleware)
    dictCtx = fdictBuildContext(_fconnectionCreateDocker())
    dictCtx["sSessionToken"] = sSessionToken
    dictCtx["iPort"] = iExpectedPort
    dictCtx["setAllowedContainers"] = app.state.setAllowedContainers
    _fnRegisterAllRoutes(app, dictCtx, WORKSPACE_ROOT)
    fnRegisterRegistryRoutes(app, dictCtx)
    _fnRegisterHubShutdownReleaseLocks(app)
    return app


def _fnRegisterHubShutdownReleaseLocks(app):
    """Release all held container locks when the hub shuts down."""

    async def fnReleaseAllContainerLocks(app):
        from vaibify.config.containerLock import fnReleaseContainerLock
        for fileHandle in list(app.state.dictContainerLocks.values()):
            try:
                fnReleaseContainerLock(fileHandle)
            except OSError:
                pass
        app.state.dictContainerLocks.clear()
    app.state.listLifespanShutdown.append(fnReleaseAllContainerLocks)
