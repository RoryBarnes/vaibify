"""FastAPI application with REST and WebSocket routes for workflow viewing."""

import asyncio
import json
import logging
import os
import posixpath
import re
import secrets
import signal
import time
from contextlib import asynccontextmanager

logger = logging.getLogger("vaibify")

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from starlette.middleware.base import BaseHTTPMiddleware
from typing import Dict, List, Optional

WORKSPACE_ROOT = "/workspace"

__all__ = [
    "fappCreateApplication",
    "fappCreateHubApplication",
    "fbIsAllowedHostHeader",
    "fdictBuildContext",
    "fdictHandleConnect",
    "ffBuildResilientWsCallback",
    "fnDispatchAction",
    "fnHandlePipelineWs",
    "fnPipelineMessageLoop",
    "fnRejectNotConnected",
    "fnRejectTerminalStart",
    "fnRunTerminalSession",
    "fnSignalTerminalAbnormalExit",
    "fnTerminalInputLoop",
    "fnTerminalReadLoop",
    "fnValidatePathWithinRoot",
    "fbHasAgentToken",
    "fbValidateWebSocketOrigin",
    "fsContainerNameForId",
    "fsGetOriginHeader",
    "fdictExtractSettings",
    "fdictFilterNonNone",
    "fdictRequireWorkflow",
    "fdictStepFromRequest",
    "fiGetSyncEpoch",
    "fnBumpSyncEpoch",
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
    "fnIncrementWebSocketCount",
    "fnDecrementWebSocketCount",
]

from . import actionCatalog
from . import agentSessionBridge
from . import conftestManager
from . import containerOwnership
from . import workflowManager
from ..docker.dockerErrorDiagnosis import fdictDiagnoseDockerError
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
    bConfirmDestructive: bool = False


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
    sBaseHash: Optional[str] = None


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


class ArxivConfigureRequest(BaseModel):
    sArxivId: str = ""
    dictPathMap: Dict[str, str] = {}
    bRemove: bool = False


class GitIdentityRequest(BaseModel):
    sName: str
    sEmail: str


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
    """Try primary path, then fallback with sWorkdir prefix.

    Multi-panel scientific figures routinely exceed the small-file
    64 MB cap, so the figure fetch opts out (``iMaxBytes=None``); the
    cap is a default for callers fetching JSON/markers, not for
    user-authored binary content.
    """
    try:
        return connectionDocker.fbaFetchFile(
            sContainerId, sAbsPath, iMaxBytes=None,
        )
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
    fnValidatePathWithinRoot(sFallback, WORKSPACE_ROOT)
    try:
        return connectionDocker.fbaFetchFile(
            sContainerId, sFallback, iMaxBytes=None,
        )
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


def _fbExceptionIsWsClosed(exc):
    """Return True iff ``exc`` signals the WebSocket has already closed.

    A closed browser tab, overnight network blip, or background-tab
    throttle used to crash long-running pipelines through the streaming
    chunk emitter; this narrow classification keeps real runtime bugs
    visible while the WS-closed family becomes a benign signal at the
    callback boundary. Callers drop the chunk and continue the run;
    reconnecting clients catch up via ``pipelineState`` polls.
    """
    if isinstance(exc, WebSocketDisconnect):
        return True
    if not isinstance(exc, RuntimeError):
        return False
    sMessage = str(exc).lower()
    return (
        "websocket.send" in sMessage
        or "websocket.close" in sMessage
        or "response already completed" in sMessage
    )


def ffBuildResilientWsCallback(websocket):
    """Return an async callback that swallows WS-closed errors silently.

    The runner is callback-agnostic; this boundary wrapper is the only
    site that knows about WebSocket semantics. After the first closed-WS
    signal, subsequent invocations short-circuit so the runner stays
    decoupled from frontend liveness for the rest of the run.
    """
    dictState = {"bWsClosed": False}

    async def fnCallback(dictEvent):
        if dictState["bWsClosed"]:
            return
        try:
            await websocket.send_json(dictEvent)
        except Exception as exc:
            if not _fbExceptionIsWsClosed(exc):
                raise
            dictState["bWsClosed"] = True
            logger.warning(
                "WebSocket closed mid-run; runner continues. "
                "Reconnecting clients reconcile via pipelineState. "
                "Trigger: %s",
                exc,
            )
    return fnCallback


# Module-level registry the terminal route consults to hand the active
# runner's interactive context to ``fnTerminalReadLoop`` so an abnormal
# terminal exit posts the runner-unblock sentinel (audit HIGH #9).
DICT_INTERACTIVE_CONTEXTS_BY_CONTAINER = {}


def _fnPublishInteractiveContext(sContainerId, dictInteractive):
    """Publish a runner's interactive context for the terminal route."""
    DICT_INTERACTIVE_CONTEXTS_BY_CONTAINER[sContainerId] = dictInteractive


def _fnUnpublishInteractiveContext(sContainerId, dictInteractive):
    """Remove the runner's interactive context if still the published one.

    The identity check guards against a fresh ``fnPipelineMessageLoop``
    that has already published its own context in the same slot — only
    drop the entry when it still points at the same dict this loop
    instance published, so a new loop's registration is never evicted
    by the prior loop's ``finally`` clean-up.
    """
    if DICT_INTERACTIVE_CONTEXTS_BY_CONTAINER.get(sContainerId) is (
        dictInteractive
    ):
        DICT_INTERACTIVE_CONTEXTS_BY_CONTAINER.pop(sContainerId, None)


def fdictInteractiveContextForContainer(sContainerId):
    """Return the active runner's interactive context, or ``None``."""
    return DICT_INTERACTIVE_CONTEXTS_BY_CONTAINER.get(sContainerId)


async def fnPipelineMessageLoop(
    websocket, connectionDocker, sContainerId,
    dictWorkflow, dictWorkflowPathCache, sWorkflowDirectory,
    dictPipelineTasks=None,
):
    """Receive and dispatch pipeline WebSocket messages.

    Event types the server emits on this socket (consumed by frontend
    dispatchers and the in-container ``vaibify-do`` CLI):

    - ``output`` / ``commandFailed`` / ``stepResult`` / ``completed`` /
      ``progress`` / ``error`` / ``pipelineError`` — pipeline status.
    - ``wsHeartbeat`` — emitted by ``_actxWebSocketHeartbeat`` in
      ``pipelineRunner`` every ``F_WS_HEARTBEAT_INTERVAL`` seconds
      while a single command is running. Pure keepalive: clients must
      ignore it (frontend filter in ``scriptPipelineRunner.js``,
      ``vaibify-do`` filter in ``_fnStreamWsEvents``).
    """
    from .pipelineRunner import (
        fdictCreateInteractiveContext,
        fnSetInteractiveResponse,
    )
    dictInteractive = fdictCreateInteractiveContext()
    fnCallback = ffBuildResilientWsCallback(websocket)
    _fnPublishInteractiveContext(sContainerId, dictInteractive)

    try:
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
                _fnRegisterPipelineTask(
                    dictPipelineTasks, sContainerId, taskPipeline,
                )
    finally:
        _fnUnpublishInteractiveContext(sContainerId, dictInteractive)


async def _fnSafeDispatch(
    sAction, dictRequest, connectionDocker,
    sContainerId, dictWorkflow, dictWorkflowPathCache,
    sWorkflowDirectory, fnCallback, dictInteractive,
):
    """Wrap fnDispatchAction with error handling.

    Tags the failure log with ``sContainerId`` so the host-incident
    ring buffer (consumed by ``pipelineState._fdictReconcileStaleHeartbeat``)
    can pair the exception with the dying container's state file.
    """
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
            extra={"sContainerId": sContainerId},
        )
        try:
            await fnCallback({
                "sType": "failed",
                "iExitCode": 1,
                "sMessage": fsSanitizeExceptionForClient(exc),
            })
        except Exception:
            pass


def _fnRegisterPipelineTask(dictPipelineTasks, sContainerId, taskPipeline):
    """Store a pipeline task and arrange for self-eviction on completion.

    Without the done-callback, completed-normally tasks linger in
    ``dictPipelineTasks`` forever — a memory leak proportional to the
    number of runs across the container's lifetime. The callback fires
    after the task finishes (success, failure, or cancellation) and
    drops the entry only if it still points at this task, so a brand-new
    run for the same container is never accidentally evicted.
    """
    dictPipelineTasks[sContainerId] = taskPipeline

    def fnEvictOnDone(taskCompleted):
        if dictPipelineTasks.get(sContainerId) is taskCompleted:
            dictPipelineTasks.pop(sContainerId, None)
    taskPipeline.add_done_callback(fnEvictOnDone)


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

I_TERMINAL_ABNORMAL_EXIT_CODE = 130


def fnSignalTerminalAbnormalExit(dictInteractive):
    """Post a complete:130 sentinel to the interactive context.

    The runner's interactive paused-state awaits on
    ``interactiveSteps.fnSetInteractiveResponse``. When the terminal
    WebSocket dies abnormally (subprocess crash, kernel hangup, exec
    pipe break) the runner would otherwise block forever. This helper
    converts the dead terminal into a runner-visible step failure.

    Callers should pass ``dictInteractive`` only when the terminal
    session is tied to an active interactive step. ``None`` is a
    no-op so the helper is safe to call from generic terminal paths.
    """
    if dictInteractive is None:
        return
    from .interactiveSteps import fnSetInteractiveResponse
    fnSetInteractiveResponse(
        dictInteractive,
        f"complete:{I_TERMINAL_ABNORMAL_EXIT_CODE}",
    )


async def _fbReadOnceAndForward(session, websocket):
    """Read one chunk and forward to the websocket; True on success."""
    baOutput = session.fbaReadOutput()
    if baOutput:
        await websocket.send_bytes(baOutput)
    else:
        await asyncio.sleep(0.05)
    return True


async def fnTerminalReadLoop(session, websocket, dictInteractive=None):
    """Continuously read terminal output and send to WebSocket.

    Posts ``complete:130`` to ``dictInteractive`` via
    :func:`fnSignalTerminalAbnormalExit` on abnormal exit so a runner
    paused at ``interactiveComplete`` does not block forever.
    """
    bAbnormal = False
    try:
        while session._bRunning:
            try:
                await _fbReadOnceAndForward(session, websocket)
            except Exception:
                bAbnormal = True
                break
    finally:
        if bAbnormal or not session._bRunning:
            fnSignalTerminalAbnormalExit(dictInteractive)


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
    session, websocket, dictTerminalSessions, dictInteractive=None,
):
    """Manage terminal session lifecycle after successful start.

    ``dictInteractive`` is the active runner's interactive context; when
    provided, ``fnTerminalReadLoop`` posts a ``complete:130`` sentinel
    on abnormal exit so a runner paused at ``interactiveComplete`` does
    not deadlock when the terminal-WS dies (audit HIGH #9).
    """
    sSessionId = session.sSessionId
    dictTerminalSessions[sSessionId] = session
    await websocket.send_json(
        {"sType": "connected", "sSessionId": sSessionId}
    )
    taskReader = asyncio.create_task(
        fnTerminalReadLoop(session, websocket, dictInteractive)
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
    """Cache the container's user and register the viewer's served record.

    Hub authorization is decided by the lease recorded at claim time, so
    a hub never adds an ownership record here (doing so would re-open the
    append-only authorization leak the lease model closes). The viewer
    holds exactly one container for its process lifetime and has no claim
    route, so its served container is recorded in ``dictContainerOwners``
    here purely to keep the idle busy-veto honest about a mid-run viewer.
    """
    _fnRegisterViewerServedContainer(dictCtx, sContainerId)
    dictCtx["containerUsers"][sContainerId] = (
        _fsResolveContainerUser(dictCtx, sContainerId)
    )
    _fnPushAgentSession(dictCtx, sContainerId)


def _fnRegisterViewerServedContainer(dictCtx, sContainerId):
    """Record a viewer's served container, keyed by its canonical name.

    The viewer has no claim route, so it mints its own lease here and
    must key the record by the SAME canonical name the gate, reaper, and
    keep-alive teardown use (per the owner-map key decision) -- keying by
    the raw docker id would make every gate lookup miss and would stop
    keep-alive by the wrong key on teardown. The minted lease is stashed
    on ``dictCtx['sViewerLease']`` so the connect response can hand it to
    the viewer's browser, which then presents it on its WebSockets.
    """
    if dictCtx.get("bIsHub"):
        return
    dictContainerOwners = dictCtx.get("dictContainerOwners")
    if dictContainerOwners is None:
        return
    sName = fsContainerNameForId(dictCtx.get("docker"), sContainerId)
    if sName in dictContainerOwners:
        dictCtx["sViewerLease"] = dictContainerOwners[sName].sLeaseId
        return
    sLeaseId = containerOwnership.fsMintLease()
    dictContainerOwners[sName] = containerOwnership.OwnerRecord(
        sLeaseId=sLeaseId, fileHandleLock=None,
        sAgentToken=containerOwnership.fsMintAgentToken(),
        sContainerId=sContainerId,
    )
    dictCtx["sViewerLease"] = sLeaseId


def _fsAgentTokenForContainerId(dictCtx, sContainerId):
    """Return the owning session's per-container agent token, or ''."""
    dictContainerOwners = dictCtx.get("dictContainerOwners") or {}
    sName = fsContainerNameForId(dictCtx.get("docker"), sContainerId)
    return containerOwnership.fsAgentTokenForName(dictContainerOwners, sName)


def _fnPushAgentSession(dictCtx, sContainerId):
    """Write the vaibify-do session + catalog into the container.

    The agent receives this container's own per-container token, never
    the hub-wide session token, so its credential authorizes only the
    container it runs inside.
    """
    sAgentToken = _fsAgentTokenForContainerId(dictCtx, sContainerId)
    try:
        agentSessionBridge.fnPushAgentSessionToContainer(
            dictCtx["docker"], sContainerId,
            sAgentToken, dictCtx.get("iPort", 0),
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
        "sLeaseId": dictCtx.get("sViewerLease", ""),
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


def _fsValidateConnectWorkflowPath(sWorkflowPath):
    """Normalize and validate a connect-supplied workflow path."""
    sNormalized = posixpath.normpath(sWorkflowPath)
    fnValidatePathWithinRoot(sNormalized, WORKSPACE_ROOT)
    if not sNormalized.endswith(".json"):
        raise HTTPException(
            400, "sWorkflowPath must point at a .json file")
    if workflowManager.S_VAIBIFY_WORKFLOWS_SUFFIX not in sNormalized:
        raise HTTPException(
            400,
            "sWorkflowPath must be under .vaibify/workflows/ "
            "inside a project repo",
        )
    return sNormalized


async def fdictHandleConnect(dictCtx, sContainerId, sWorkflowPath):
    """Load workflow, cache it, return connection response."""
    if sWorkflowPath is None:
        return _fdictConnectNoWorkflow(dictCtx, sContainerId)
    sWorkflowPath = _fsValidateConnectWorkflowPath(sWorkflowPath)
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
        await _fnRefreshConftestsAndMigrateMarkers(
            dictCtx, sContainerId, dictWorkflow, sResolved,
        )
        from .workflowReloadDetector import fnRecordSelfWriteMtime
        fnRecordSelfWriteMtime(dictCtx, sContainerId, sResolved)
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
            "sLeaseId": dictCtx.get("sViewerLease", ""),
        }
    except HTTPException:
        raise
    except Exception as error:
        logger.error("Workflow load failed: %s", error)
        raise HTTPException(400, "Workflow load failed")


async def _fnRefreshConftestsAndMigrateMarkers(
    dictCtx, sContainerId, dictWorkflow, sWorkflowPath,
):
    """Refresh stale conftests and migrate flat markers at connect time.

    Both operations are process-cached inside ``conftestManager`` so
    poll-time calls in ``_fdictAttachTestStatus`` become no-ops after
    the first sweep here. The migration is namespaced by the workflow
    slug derived from ``sWorkflowPath`` so flat markers land in the
    same per-slug subdirectory the poll path reads. Failures log and
    swallow so a connect handshake never fails on a migration issue.
    """
    sProjectRepoPath = dictWorkflow.get("sProjectRepoPath", "")
    if not sProjectRepoPath:
        return
    listStepDirs = [
        dictStep.get("sDirectory", "")
        for dictStep in dictWorkflow.get("listSteps", [])
        if dictStep.get("sDirectory", "")
    ]
    try:
        await asyncio.to_thread(
            conftestManager.fnEnsureConftestsCurrent,
            dictCtx["docker"], sContainerId, listStepDirs,
            sProjectRepoPath,
        )
        await asyncio.to_thread(
            conftestManager.fnMigrateFlatMarkers,
            dictCtx["docker"], sContainerId, sProjectRepoPath,
            fsWorkflowSlugFromPath(sWorkflowPath),
        )
    except Exception as error:
        logger.warning(
            "Conftest refresh / marker migration failed: %s", error,
        )


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
# WebSocket origin validation
# ---------------------------------------------------------------

def fsContainerNameForId(connectionDocker, sContainerId):
    """Resolve a docker container id to its canonical project name.

    The owner-of-record map is keyed by container NAME (the project name
    the claim route writes), but the WebSocket routes receive the docker
    id in their path because the downstream ``docker exec`` needs it. This
    single conversion lets the name-keyed gate, reaper, and keep-alive
    teardown stay consistent with the name-keyed claim writes. Falls back
    to the supplied identifier when Docker is unavailable or the container
    is not in the running set, so a caller that already holds a name (the
    viewer, or a test fixture where name == id) is unaffected.
    """
    if connectionDocker is None:
        return sContainerId
    try:
        for dictRow in connectionDocker.flistGetRunningContainers():
            if dictRow.get("sContainerId") == sContainerId:
                return dictRow.get("sName") or sContainerId
    except Exception:
        return sContainerId
    return sContainerId


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
    async def fnGetSessionToken(request: Request):
        if request.headers.get(
            actionCatalog.S_SESSION_HEADER_NAME.lower(), "",
        ):
            raise HTTPException(
                status_code=403,
                detail="The in-container agent must not read the hub "
                "session token.",
            )
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
    fbStepTestsPassing,
    fbStepTimingClean,
    fbStepUserApproved,
    fbReconcileUpstreamFlags,
    fbReconcileUserVerificationTimestamps,
    fdictCollectOutputPathsByStep,
    flistStepRemoteFiles,
    fnCollectMarkerPathsByStep,
    fnCollectScriptPathsByStep,
    fnMaybeAutoArchive,
    fsMarkerNameFromStepDirectory,
    fsWorkflowSlugFromPath,
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
    fbRefreshAggregateTestStates,
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
        from .workflowReloadDetector import fnRecordSelfWriteMtime
        fnRecordSelfWriteMtime(dictRaw, sContainerId, sPath)

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

    def fnFiles(sContainerId):
        from vaibify.reproducibility.repoFiles import ContainerRepoFiles
        dictWorkflow = dictWorkflows.get(sContainerId) or {}
        sRepoPath = dictWorkflow.get("sProjectRepoPath", "")
        return ContainerRepoFiles(
            dictRaw["docker"], sContainerId, sRepoPath,
        )

    return fnRequire, fnSave, fnVariables, fnWorkflowDir, fnFiles


def fnBumpSyncEpoch(dictCtx, sContainerId):
    """Increment the per-container sync epoch.

    Every sync-mutating route (push, add-file, commit-canonical,
    pull/fetch/refresh of the project repo) bumps this counter so the
    state poll can detect that remote-facing git state may have
    changed and trigger exactly one badge refresh — no timers, no
    extra polling loops.
    """
    dictEpochs = dictCtx.setdefault("dictSyncEpochs", {})
    dictEpochs[sContainerId] = dictEpochs.get(sContainerId, 0) + 1


def fiGetSyncEpoch(dictCtx, sContainerId):
    """Return the current sync epoch for a container (0 when untouched)."""
    return dictCtx.get("dictSyncEpochs", {}).get(sContainerId, 0)


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
        "lastSelfWriteMtimes": {},
        "lastDiscoveredWorkflows": {},
        "dictPipelineStateLocks": {},
        "dictSyncEpochs": {},
    }
    fnRequire, fnSave, fnVariables, fnWorkflowDir, fnFiles = (
        _ftupleBuildHelpers(dictRaw, dictWorkflows, dictPaths)
    )
    dictRaw["require"] = fnRequire
    dictRaw["save"] = fnSave
    dictRaw["variables"] = fnVariables
    dictRaw["workflowDir"] = fnWorkflowDir
    dictRaw["files"] = fnFiles
    return RouteContext(dictRaw)


# ---------------------------------------------------------------
# Route registration (delegates to route modules)
# ---------------------------------------------------------------

def _fnRegisterAllRoutes(app, dictCtx, sWorkspaceRoot):
    """Register all API routes on the app."""
    from . import routes

    routes.workflowRoutes.fnRegisterAll(app, dictCtx)
    routes.fileRoutes.fnRegisterAll(app, dictCtx, sWorkspaceRoot)
    routes.draftRoutes.fnRegisterAll(app, dictCtx)
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
    routes.levelRoutes.fnRegisterAll(app, dictCtx)
    routes.reproducibilityRoutes.fnRegisterAll(app, dictCtx)
    _fnRegisterStaticFiles(app, dictCtx)


# ---------------------------------------------------------------
# Application factories
# ---------------------------------------------------------------


def _fnRegisterLastResortExceptionHandler(app):
    """Convert any unhandled route exception into a sanitized 500 JSON.

    Without this handler an unexpected exception becomes a bare 500
    whose traceback goes only to uvicorn's stderr — never to the
    vaibify log file — and the client receives no structured body.
    The full traceback is logged to the "vaibify" logger; the client
    sees only ``fsSanitizeExceptionForClient`` output so internal
    paths and credentials can never leak.
    """
    from fastapi.responses import JSONResponse

    @app.exception_handler(Exception)
    async def fnHandleUnexpectedRouteException(request, exc):
        logger.error(
            "Unhandled exception on %s %s",
            request.method, request.url.path, exc_info=exc,
        )
        return JSONResponse(
            status_code=500,
            content={"detail": fsSanitizeExceptionForClient(exc)},
        )


# ---------------------------------------------------------------
# Re-exports from the extracted server modules (backward compat).
# Internal callers should import from the canonical module; these
# bindings keep external importers and the test patch surface
# (e.g. ``pipelineServer._fconnectionCreateDocker``) working.
# ---------------------------------------------------------------

from .dockerStatus import (  # noqa: E402,F401
    _dictDockerStatus,
    _fbCaffeinateRunning,
    _fconnectionCreateDocker,
    _fdictSleepWarningForContext,
    _fnClearDockerError,
    _fnRecordDockerError,
    _fnRequireDocker,
    _fsBuildDockerUnavailableDetail,
    fdictGetDockerStatus,
    fdictRetryDockerConnection,
    fsDetectDockerRuntime,
)
from .serverMiddleware import (  # noqa: E402,F401
    ActivityTrackingMiddleware,
    SecurityHeadersMiddleware,
    SessionTokenMiddleware,
    _SET_LOCAL_HOST_NAMES,
    _fbRequestHasAllowedHost,
    _ftSplitHostPort,
    fbIsAllowedHostHeader,
    fnRegisterMiddleware,
)
from .serverLifespan import (  # noqa: E402,F401
    F_CONTAINER_SWEEP_INTERVAL_SECONDS,
    F_HUB_IDLE_TIMEOUT_SECONDS,
    F_HUB_WATCHDOG_INTERVAL_SECONDS,
    I_VAIBIFY_IO_THREAD_POOL_FLOOR,
    S_HUB_IDLE_TIMEOUT_ENV,
    _alifespanShared,
    _fIdleTimeoutSeconds,
    _fbAnyContainerRunning,
    _fbAnyHeldContainerBusy,
    _fbHubShouldSelfExit,
    _fbOwnedNamePipelineRunning,
    _flistBusyCandidateIds,
    _flistHeldContainerIds,
    _flistRunningIdsForName,
    _fnIdleShutdownWatchdogLoop,
    _fnInvokeMaybeAsync,
    _fnPeriodicContainerSweepLoop,
    _fnPruneSpawnedChildrenForApp,
    _fnReapIdleOwnershipsForApp,
    _fnRegisterDefaultThreadPoolExecutor,
    _fnRegisterIdleShutdownWatchdog,
    _fnRegisterPeriodicContainerSweep,
    _fnRunOneContainerSweep,
    _fnRunShutdownHookSafely,
    _fnRunStartupHookSafely,
    fnDecrementWebSocketCount,
    fnIncrementWebSocketCount,
    fnRegisterLifespanTask,
)
from .appFactory import (  # noqa: E402,F401
    _fnRegisterHubLockLifecycle,
    _fnRegisterHubShutdownReleaseLocks,
    _fnRegisterHubShutdownStopKeepAlive,
    _fnRegisterHubStartupReapStaleClaims,
    fappCreateApplication,
    fappCreateHubApplication,
)
