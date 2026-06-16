"""Pipeline control route handlers."""

__all__ = ["fnRegisterAll", "fdictComputeFileStatus"]

import asyncio
import hashlib
import json
import logging
import posixpath
import re

from docker.errors import APIError, NotFound
from fastapi import HTTPException, Request, Response, WebSocket, WebSocketDisconnect

from ..actionCatalog import fnAgentAction
from ..pipelineRunner import fsShellQuote
from ..pipelineServer import (
    WORKSPACE_ROOT,
    fbValidateWebSocketOrigin,
    fdictRequireWorkflow,
    fiGetSyncEpoch,
    fnHandlePipelineWs,
    fsSanitizeExceptionForClient,
)
from ..fileStatusManager import (
    _fbCheckStaleUserVerification,
    _fdictBuildScriptStatus,
    _fdictComputeMarkerMtimeByStep,
    _fdictComputeMaxDataMtimeByStep,
    _fdictComputeMaxMtimeByStep,
    _fdictComputeMaxPlotMtimeByStep,
    _fdictComputeMaxTestSourceMtimeByStep,
    _fdictComputeTestCategoryMtimes,
    _fdictGetModTimes,
    _flistResolveTestSourcePaths,
    _flistCollectOutputPaths,
    _flistDetectAndInvalidate,
    _fnClearStepModificationState,
    _fnUpdateModTimeBaseline,
    fbReconcileUpstreamFlags,
    fbReconcileUserVerificationTimestamps,
    fdictCollectOutputPathsByStep,
    fnCollectMarkerPathsByStep,
    fnInvalidateParentCacheForContainer,
    fsMarkerNameFromStepDirectory,
    fsWorkflowSlugFromPath,
)
from ..fileIntegrity import flistExtractAllScriptPaths
from ..testStatusManager import fbRefreshAggregateTestStates
from ..routeContext import ffilesForWorkflow
from ..pathContract import fdictAbsKeysToRepoRelative
from ..randomnessLint import fnApplyRandomnessLintToWorkflow
from ..llmInvoker import fsReadFileFromContainer
from ..workflowReloadDetector import (
    fdictDetectNewlyAvailableWorkflows,
    fdictMaybeReloadWorkflow as _fdictMaybeReloadWorkflow,
)

logger = logging.getLogger("vaibify")


def _flistExtractKillPatterns(dictWorkflow):
    """Extract unique command patterns from workflow steps."""
    setPatterns = set()
    for dictStep in dictWorkflow.get("listSteps", []):
        for sKey in ("saDataCommands", "saPlotCommands",
                     "saSetupCommands", "saCommands"):
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


def _fbCancelPipelineTask(dictPipelineTasks, sContainerId):
    """Cancel any running pipeline asyncio task for a container."""
    taskPipeline = dictPipelineTasks.get(sContainerId)
    if taskPipeline is None or taskPipeline.done():
        return False
    taskPipeline.cancel()
    dictPipelineTasks.pop(sContainerId, None)
    return True


async def _fnMarkPipelineStopped(dictCtx, sContainerId):
    """Write a stopped state file so the UI shows not running.

    Reads through the reconciling reader so a kill issued against a
    container whose runner already vanished does not double-write —
    the watchdog will have already flipped ``bRunning`` to False.
    """
    from .. import pipelineState
    dictState = await pipelineState.fdictReadReconciledState(
        dictCtx, sContainerId,
    )
    if dictState is None or not dictState.get("bRunning"):
        return
    await asyncio.to_thread(
        pipelineState.fnUpdateState,
        dictCtx["docker"], sContainerId, dictState,
        pipelineState.fdictBuildCompletedState(130),
    )


def _flistBuildCleanCommands(dictWorkflow):
    """Build rm commands for all output files and reset step stats.

    Step directory and output paths are repo-relative; join them
    with ``sProjectRepoPath`` so the rm targets land in the project
    repo rather than the container's default CWD.
    """
    sRepoRoot = dictWorkflow.get("sProjectRepoPath", "")
    listCleanCommands = []
    for dictStep in dictWorkflow.get("listSteps", []):
        if dictStep.get("bInteractive", False):
            continue
        sDir = dictStep.get("sDirectory", "")
        for sKey in ("saDataFiles", "saPlotFiles"):
            for sFile in dictStep.get(sKey, []):
                if sFile.startswith("{"):
                    continue
                sRepoRel = sFile if sFile.startswith("/") else (
                    posixpath.join(sDir, sFile) if sDir
                    else sFile)
                sPath = sRepoRel if (
                    sRepoRel.startswith("/") or not sRepoRoot
                ) else posixpath.join(sRepoRoot, sRepoRel)
                listCleanCommands.append(
                    f"rm -f {fsShellQuote(sPath)} 2>/dev/null")
        dictStep["dictRunStats"] = {}
        dictStep["dictVerification"] = {
            "sUnitTest": "untested",
            "sUser": "untested",
            "sIntegrity": "untested",
            "sQualitative": "untested",
            "sQuantitative": "untested",
        }
    return listCleanCommands


async def _fiCountMatchingProcesses(
    connectionDocker, sContainerId, sGrepPattern,
):
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


async def _fnKillMatchingProcesses(
    connectionDocker, sContainerId, listPatterns,
):
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


def _fnRegisterPipelineState(app, dictCtx):
    """Register GET /api/pipeline/{id}/state endpoint.

    Also exposed as the ``get-pipeline-state`` agent action so an
    in-container Claude can read the same post-reconciliation view the
    dashboard sees without re-implementing the stale-heartbeat
    reconciliation against the raw pipeline_state.json file.
    """

    @fnAgentAction("get-pipeline-state")
    @app.get("/api/pipeline/{sContainerId}/state")
    async def fnGetPipelineState(sContainerId: str):
        from ..pipelineState import fdictReadReconciledState
        dictCtx["require"]()
        dictState = await fdictReadReconciledState(
            dictCtx, sContainerId,
        )
        iSyncEpoch = fiGetSyncEpoch(dictCtx, sContainerId)
        if dictState is None:
            return {"bRunning": False, "iSyncEpoch": iSyncEpoch}
        dictState["iSyncEpoch"] = iSyncEpoch
        return dictState


I_HOST_LOG_TAIL_DEFAULT_LINES = 200
I_HOST_LOG_TAIL_MAX_LINES = 1000


def _fsResolveHostLogPath():
    """Return the absolute path to the host vaibify.log file."""
    import os
    return os.path.expanduser("~/.vaibify/vaibify.log")


def _fiClampLineCount(iRequested):
    """Clamp the requested tail-line count to [1, MAX]."""
    iLines = iRequested if isinstance(iRequested, int) else (
        I_HOST_LOG_TAIL_DEFAULT_LINES
    )
    if iLines < 1:
        return 1
    if iLines > I_HOST_LOG_TAIL_MAX_LINES:
        return I_HOST_LOG_TAIL_MAX_LINES
    return iLines


def _flistTailLogLinesForContainer(sLogPath, sContainerId, iLines):
    """Return the last iLines log lines that mention sContainerId.

    Walks the file once and keeps a bounded deque so a multi-megabyte
    rotated log never loads in full. Filtering on substring keeps the
    contract simple: any log line that names the container (the
    ``_fnSafeDispatch`` ``extra={"sContainerId": cid}`` tag is included
    by the default formatter via ``%(message)s`` references) qualifies.
    """
    from collections import deque
    import os
    if not os.path.isfile(sLogPath):
        return []
    dequeMatches = deque(maxlen=iLines)
    with open(sLogPath, "r", encoding="utf-8", errors="replace") as fileLog:
        for sLine in fileLog:
            if sContainerId in sLine:
                dequeMatches.append(sLine.rstrip("\n"))
    return list(dequeMatches)


def _fnRegisterHostLogTail(app, dictCtx):
    """Register GET /api/pipeline/{id}/host-log-tail endpoint."""

    @fnAgentAction("get-host-log-tail")
    @app.get("/api/pipeline/{sContainerId}/host-log-tail")
    async def fnGetHostLogTail(
        sContainerId: str,
        iLines: int = I_HOST_LOG_TAIL_DEFAULT_LINES,
    ):
        from vaibify.gui.hostIncidents import (
            flistIncidentsForContainer,
        )
        iEffectiveLines = _fiClampLineCount(iLines)
        sLogPath = _fsResolveHostLogPath()
        listLines = await asyncio.to_thread(
            _flistTailLogLinesForContainer,
            sLogPath, sContainerId, iEffectiveLines,
        )
        listIncidents = flistIncidentsForContainer(sContainerId)
        return {
            "sLogPath": sLogPath,
            "iRequestedLines": iLines,
            "iEffectiveLines": iEffectiveLines,
            "listLines": listLines,
            "listIncidents": listIncidents[-iEffectiveLines:],
        }


def _fnRegisterPipelineKill(app, dictCtx):
    """Register POST /api/pipeline/{id}/kill endpoint."""

    @fnAgentAction("kill-pipeline")
    @app.post("/api/pipeline/{sContainerId}/kill")
    async def fnKillRunningTasks(sContainerId: str):
        dictCtx["require"]()
        dictWorkflow = fdictRequireWorkflow(
            dictCtx["workflows"], sContainerId)
        bTaskCancelled = _fbCancelPipelineTask(
            dictCtx["pipelineTasks"], sContainerId)
        listPatterns = _flistExtractKillPatterns(dictWorkflow)
        listSafe = [re.escape(s) for s in listPatterns]
        sGrepPattern = (
            "|".join(listSafe) if listSafe else "")
        iCountBefore = 0
        if sGrepPattern:
            iCountBefore = await _fiCountMatchingProcesses(
                dictCtx["docker"], sContainerId, sGrepPattern)
            if iCountBefore > 0:
                await _fnKillMatchingProcesses(
                    dictCtx["docker"], sContainerId,
                    listPatterns,
                )
        await _fnMarkPipelineStopped(dictCtx, sContainerId)
        return {
            "bSuccess": True,
            "iProcessesKilled": iCountBefore,
            "bTaskCancelled": bTaskCancelled,
        }


def _fnRegisterPipelineClean(app, dictCtx):
    """Register POST /api/pipeline/{id}/clean endpoint."""

    @fnAgentAction("clean-outputs")
    @app.post("/api/pipeline/{sContainerId}/clean")
    async def fnCleanOutputs(sContainerId: str):
        dictCtx["require"]()
        dictWorkflow = fdictRequireWorkflow(
            dictCtx["workflows"], sContainerId)
        listCleanCommands = _flistBuildCleanCommands(
            dictWorkflow)
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
    async def fnPipelineWs(
        websocket: WebSocket, sContainerId: str
    ):
        if not fbValidateWebSocketOrigin(
            websocket, dictCtx["sSessionToken"],
        ):
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
        await fnHandlePipelineWs(
            websocket, dictCtx, sContainerId)


def _fnRegisterAcknowledgeStep(app, dictCtx):
    """Register POST endpoint to acknowledge step completion."""

    @fnAgentAction("acknowledge-step")
    @app.post(
        "/api/pipeline/{sContainerId}"
        "/acknowledge-step/{iStepIndex}"
    )
    async def fnAcknowledgeStep(
        sContainerId: str, iStepIndex: int,
    ):
        from .. import syncDispatcher as _syncDispatcher
        dictCtx["require"]()
        dictWorkflow = fdictRequireWorkflow(
            dictCtx["workflows"], sContainerId)
        _fnClearStepModificationState(
            dictWorkflow, iStepIndex,
        )
        dictVars = dictCtx["variables"](sContainerId)
        listPaths = _flistCollectOutputPaths(
            dictWorkflow, dictVars)
        dictModTimes = await asyncio.to_thread(
            _fdictGetModTimes,
            dictCtx["docker"], sContainerId, listPaths,
            dictCtx=dictCtx, bPipelineRunning=False,
        )
        _fnUpdateModTimeBaseline(
            dictCtx, sContainerId, dictModTimes)
        dictCtx["save"](sContainerId, dictWorkflow)
        return {"bSuccess": True}


async def fdictComputeFileStatus(
    dictCtx, sContainerId, dictWorkflow, dictVars,
):
    """Return merged output-status and test-status payload."""
    dictOutputStatus = await _fdictFetchOutputStatus(
        dictCtx, sContainerId, dictWorkflow, dictVars,
    )
    if dictOutputStatus.get("bWorkflowReloaded"):
        dictWorkflow = dictCtx["workflows"][sContainerId]
    dictTestStatus = await _fdictFetchTestStatus(
        dictCtx, sContainerId, dictWorkflow,
        dictMaxOutputMtimeByStep=dictOutputStatus.get(
            "dictMaxMtimeByStep", {},
        ),
    )
    dictOutputStatus.update(dictTestStatus)
    return dictOutputStatus


def _fbApplyRandomnessLint(dictCtx, sContainerId, dictWorkflow):
    """Run the unseeded-randomness lint, return True if any flag changed.

    The lint reads referenced configuration files from the container
    via ``fsReadFileFromContainer`` (a docker-exec round-trip), so it
    blocks. Always invoke through :func:`_fbApplyRandomnessLintAsync`
    from async code; calling it directly on the event loop reintroduces
    the audit-HIGH #14 stall. Skipped entirely when the workflow
    declares no ``dictRandomnessLint`` block.
    """
    if not dictWorkflow.get("dictRandomnessLint"):
        return False
    listSnapshot = [
        dictStep.get("dictVerification", {}).get(
            "bUnseededRandomnessWarning", False,
        )
        for dictStep in dictWorkflow.get("listSteps", [])
    ]

    def fnReadFile(sPath):
        return fsReadFileFromContainer(
            dictCtx["docker"], sContainerId, sPath,
        )

    fnApplyRandomnessLintToWorkflow(dictWorkflow, fnReadFile)
    listAfter = [
        dictStep.get("dictVerification", {}).get(
            "bUnseededRandomnessWarning", False,
        )
        for dictStep in dictWorkflow.get("listSteps", [])
    ]
    return listAfter != listSnapshot


async def _fbApplyRandomnessLintAsync(
    dictCtx, sContainerId, dictWorkflow,
):
    """Run the lint off the event loop via asyncio.to_thread."""
    return await asyncio.to_thread(
        _fbApplyRandomnessLint, dictCtx, sContainerId, dictWorkflow,
    )


def _fsBuildFileStatusEtag(dictResponse, iSyncEpoch):
    """Return a stable ETag stamp for a file-status response payload.

    The mtime vector is the single load-bearing change signal — every
    invalidation downstream rides on a per-step mtime. The hash is
    over the sorted ``(stepIndex, mtime)`` pairs, the per-step max
    mtime, plus ``iSyncEpoch`` so a manual sync bump invalidates the
    badge cache even when no file moved. Including the L1/L2/L3
    blocker counts captures verification-state transitions a pure
    mtime hash would miss.
    """
    listSignals = [
        ("syncEpoch", int(iSyncEpoch)),
        ("modTimes", sorted(
            (dictResponse.get("dictModTimes") or {}).items(),
        )),
        ("maxByStep", sorted(
            (dictResponse.get("dictMaxMtimeByStep") or {}).items(),
        )),
        ("aicsLevel", dictResponse.get("iAICSLevel", 0)),
        ("l1", dictResponse.get("iL1BlockerCount", 0)),
        ("l2", dictResponse.get("iL2BlockerCount", 0)),
        ("l3", dictResponse.get("iL3BlockerCount", 0)),
    ]
    sBody = json.dumps(listSignals, sort_keys=True, default=str)
    sDigest = hashlib.sha256(sBody.encode("utf-8")).hexdigest()
    return '"' + sDigest + '"'


def _fnRegisterFileStatus(app, dictCtx):
    """Register GET /api/pipeline/{id}/file-status endpoint.

    Supports ``If-None-Match``: clients pass the prior ETag and a
    matching server-side stamp short-circuits to a 304 with an empty
    body. The stamp covers the mtime vector + blocker counts +
    iSyncEpoch so every observable transition still produces a fresh
    payload.
    """

    @app.get("/api/pipeline/{sContainerId}/file-status")
    async def fnGetFileStatus(
        sContainerId: str, request: Request, response: Response,
    ):
        dictCtx["require"]()
        dictWorkflow = fdictRequireWorkflow(
            dictCtx["workflows"], sContainerId)
        dictVars = dictCtx["variables"](sContainerId)
        dictResponse = await fdictComputeFileStatus(
            dictCtx, sContainerId, dictWorkflow, dictVars,
        )
        sEtag = _fsBuildFileStatusEtag(
            dictResponse, fiGetSyncEpoch(dictCtx, sContainerId),
        )
        sIfNoneMatch = request.headers.get("if-none-match", "")
        if sIfNoneMatch and sIfNoneMatch == sEtag:
            return Response(status_code=304, headers={"ETag": sEtag})
        response.headers["ETag"] = sEtag
        return dictResponse


def _fnRegisterWorkflowDiscovery(app, dictCtx):
    """Register GET /api/pipeline/{id}/workflow-discovery endpoint.

    Discovery is mode-agnostic — toolkit (no workflow loaded) and
    workflow modes both poll it so a workflow.json appearing inside
    the container surfaces in the dashboard within one tick. The
    endpoint deliberately does not call ``fdictRequireWorkflow``.
    """

    @app.get("/api/pipeline/{sContainerId}/workflow-discovery")
    async def fnGetWorkflowDiscovery(sContainerId: str):
        dictCtx["require"]()
        dictResult = await asyncio.to_thread(
            fdictDetectNewlyAvailableWorkflows,
            dictCtx, sContainerId,
        )
        return {
            "listAvailableWorkflows": dictResult["listWorkflows"],
            "bWorkflowsChanged": dictResult["bChangedSinceLastPoll"],
            "listNewWorkflowPaths": dictResult["listNewWorkflowPaths"],
        }


async def _fbResolvePipelineRunning(dictCtx, sContainerId):
    """Reconcile pipeline state and return the post-reconciliation bRunning.

    The reconciling reader runs ahead of poll side-effects so a vanished
    runner is reflected before invalidation logic asks "is a pipeline
    still running?" — without this the watchdog would suppress
    file-change invalidation for hours after the runner crashed.
    """
    from ..pipelineState import fdictReadReconciledState
    dictPipelineState = await fdictReadReconciledState(
        dictCtx, sContainerId,
    )
    return bool(
        dictPipelineState and dictPipelineState.get("bRunning"),
    )


async def _fdictFetchOutputStatus(
    dictCtx, sContainerId, dictWorkflow, dictVars,
):
    """Fetch output + script mtimes, invalidations, and staleness.

    ``dictModTimes`` is collected and threaded through every helper in
    absolute-key form; the ``fdictAbsKeysToRepoRelative`` call below is
    the last transformation before the wire — enforced by
    ``testWireFormatPathsAreRepoRelative``.
    """
    bPipelineRunning = await _fbResolvePipelineRunning(
        dictCtx, sContainerId,
    )
    dictModTimes, dictReload, sWorkflowPath = await _ftFetchAndReload(
        dictCtx, sContainerId, dictWorkflow, dictVars,
        bPipelineRunning=bPipelineRunning,
    )
    if dictReload["bReplaced"]:
        fnInvalidateParentCacheForContainer(dictCtx, sContainerId)
        dictWorkflow = dictReload["dictWorkflow"]
    listInvalidated = _flistRunPollSideEffects(
        dictCtx, sContainerId, dictWorkflow, dictModTimes, dictVars,
        bPipelineRunning=bPipelineRunning,
    )
    if await _fbApplyRandomnessLintAsync(
        dictCtx, sContainerId, dictWorkflow,
    ):
        dictCtx["save"](sContainerId, dictWorkflow)
    sRepoRoot = dictWorkflow.get("sProjectRepoPath", "")
    filesPoll = await asyncio.to_thread(
        _ffilesFetchPollSnapshot, dictCtx, sContainerId, dictWorkflow,
        dictModTimes,
    )
    dictRest = _fdictBuildPollResponseRest(
        dictWorkflow, dictModTimes, dictVars, dictReload,
        sWorkflowPath, listInvalidated, sRepoRoot, filesPoll,
    )
    _fnSaveIfLevelHighWaterChanged(
        dictCtx, sContainerId, dictWorkflow, dictRest,
    )
    return {
        "dictModTimes": fdictAbsKeysToRepoRelative(
            dictModTimes, sRepoRoot,
        ),
        **dictRest,
    }


def _flistCollectPollPaths(dictWorkflow, dictVars, sWorkflowPath):
    """Return the deduplicated union of paths the poller needs mtimes for."""
    sRepoRoot = dictWorkflow.get("sProjectRepoPath", "")
    listOutputPaths = _flistCollectOutputPaths(dictWorkflow, dictVars)
    listScriptPaths = flistExtractAllScriptPaths(dictWorkflow)
    listMarkerPaths = list(fnCollectMarkerPathsByStep(
        dictWorkflow, sRepoRoot, sWorkflowPath,
    ).values())
    listTestSourcePaths = []
    for dictStep in dictWorkflow.get("listSteps", []):
        listTestSourcePaths.extend(
            _flistResolveTestSourcePaths(dictStep, dictVars),
        )
    listWorkflowPaths = [sWorkflowPath] if sWorkflowPath else []
    return list(set(
        listOutputPaths + listScriptPaths + listMarkerPaths
        + listTestSourcePaths + listWorkflowPaths,
    ))


async def _ftFetchAndReload(
    dictCtx, sContainerId, dictWorkflow, dictVars,
    bPipelineRunning=False,
):
    """Fetch the union of poll mtimes and the maybe-reloaded workflow.

    Returns ``(dictModTimes, dictReload, sWorkflowPath)``. The mtime dict
    is absolute-keyed; the response builder is the boundary at which the
    keys are converted to repo-relative for the wire.
    """
    sWorkflowPath = dictCtx["paths"].get(sContainerId, "")
    listUnionPaths = _flistCollectPollPaths(
        dictWorkflow, dictVars, sWorkflowPath,
    )
    dictModTimes = await asyncio.to_thread(
        _fdictGetModTimes, dictCtx["docker"], sContainerId, listUnionPaths,
        dictCtx=dictCtx, bPipelineRunning=bPipelineRunning,
    )
    dictReload = await asyncio.to_thread(
        _fdictMaybeReloadWorkflow, dictCtx, sContainerId,
        sWorkflowPath, dictModTimes,
    )
    return dictModTimes, dictReload, sWorkflowPath


def _fnLogInvalidations(sContainerId, listInvalidated):
    """Emit per-step invalidation log lines for the polling cycle."""
    if not listInvalidated:
        return
    logger.info(
        "POLL invalidated steps=%s container=%s",
        list(listInvalidated.keys()), sContainerId,
    )
    for sIdx, dictV in listInvalidated.items():
        logger.info(
            "  step %s sUser=%s listModifiedFiles=%s",
            sIdx, dictV.get("sUser"),
            dictV.get("listModifiedFiles", []),
        )


def _fdictLoadMarkersForPoll(dictCtx, sContainerId, dictWorkflow):
    """Return ``{iStepIndex: dictMarker_or_None}`` for the live workflow.

    Reuses :func:`stateManager._flistFetchMarkers` so the marker on-disk
    contract has one reader. Missing markers map to ``None`` and the
    hash-staleness pass skips them gracefully.
    """
    from .. import stateManager
    sProjectRepoPath = dictWorkflow.get("sProjectRepoPath", "")
    if not sProjectRepoPath:
        return {}
    sWorkflowSlug = fsWorkflowSlugFromPath(
        dictWorkflow.get("sPath", ""),
    )
    if not sWorkflowSlug:
        return {}
    listSteps = dictWorkflow.get("listSteps", []) or []
    listMarkers = stateManager._flistFetchMarkers(
        dictCtx["docker"], sContainerId, sProjectRepoPath,
        sWorkflowSlug, listSteps,
    )
    return _fdictMarkersByStepIndex(listMarkers, listSteps)


def _fdictMarkersByStepIndex(listMarkers, listSteps):
    """Map ``[(sDirectory, dictMarker)]`` onto live step indices."""
    dictByDirectory = {
        sDirectory: dictMarker for sDirectory, dictMarker in listMarkers
    }
    dictResult = {}
    for iIndex, dictStep in enumerate(listSteps):
        sDirectory = dictStep.get("sDirectory", "")
        if sDirectory and sDirectory in dictByDirectory:
            dictResult[iIndex] = dictByDirectory[sDirectory]
    return dictResult


def _fdictLoadMtimeCacheForPoll(dictWorkflow):
    """Load the persistent mtime cache from the project repo, if available."""
    from .. import mtimeCache
    sProjectRepoPath = dictWorkflow.get("sProjectRepoPath", "")
    if not sProjectRepoPath:
        return {}
    return mtimeCache.fdictLoadCache(sProjectRepoPath)


def _fnPersistMtimeCacheForPoll(dictWorkflow, dictCache):
    """Save the mtime cache atomically; absent project repo is a no-op."""
    from .. import mtimeCache
    sProjectRepoPath = dictWorkflow.get("sProjectRepoPath", "")
    if not sProjectRepoPath or not dictCache:
        return
    try:
        mtimeCache.fnSaveCache(sProjectRepoPath, dictCache)
    except OSError as error:
        logger.warning(
            "POLL mtime cache persist failed for %s: %s",
            sProjectRepoPath, error,
        )


def _flistRunPollSideEffects(
    dictCtx, sContainerId, dictWorkflow, dictModTimes, dictVars,
    bPipelineRunning=False,
):
    """Apply stale-check, invalidate, reconcile; return invalidated steps.

    Does NOT run the unseeded-randomness lint — that blocks on a docker
    exec round-trip and must be awaited via
    :func:`_fbApplyRandomnessLintAsync` from the async caller (audit
    HIGH #14). The async caller persists any randomness-flag change.
    """
    if _fbCheckStaleUserVerification(dictWorkflow, dictModTimes, dictVars):
        logger.info(
            "POLL stale-check reset sUser for container=%s", sContainerId,
        )
        dictCtx["save"](sContainerId, dictWorkflow)
    dictMarkersByStep = _fdictLoadMarkersForPoll(
        dictCtx, sContainerId, dictWorkflow,
    )
    dictMtimeCache = _fdictLoadMtimeCacheForPoll(dictWorkflow)
    listInvalidated = _flistDetectAndInvalidate(
        dictCtx, sContainerId, dictWorkflow, dictModTimes, dictVars,
        dictMarkersByStep=dictMarkersByStep,
        dictCache=dictMtimeCache,
        bPipelineRunning=bPipelineRunning,
    )
    _fnPersistMtimeCacheForPoll(dictWorkflow, dictMtimeCache)
    _fnLogInvalidations(sContainerId, listInvalidated)
    dictPathsByStep = fdictCollectOutputPathsByStep(dictWorkflow, dictVars)
    dictMaxMtimeByStep = _fdictComputeMaxMtimeByStep(
        dictPathsByStep, dictModTimes,
    )
    bAnyReconciled = (
        fbReconcileUpstreamFlags(dictWorkflow, dictMaxMtimeByStep)
        | fbReconcileUserVerificationTimestamps(dictWorkflow)
    )
    if bAnyReconciled:
        dictCtx["save"](sContainerId, dictWorkflow)
    return listInvalidated


def _fdictComputeAllPerStepMtimes(
    dictWorkflow, dictModTimes, dictVars, dictMarkerPathsByStep,
):
    """Compute every per-step mtime grouping consumed by the wire response."""
    dictPathsByStep = fdictCollectOutputPathsByStep(dictWorkflow, dictVars)
    return {
        "dictMaxMtimeByStep": _fdictComputeMaxMtimeByStep(
            dictPathsByStep, dictModTimes,
        ),
        "dictMaxPlotMtimeByStep": _fdictComputeMaxPlotMtimeByStep(
            dictWorkflow, dictModTimes, dictVars,
        ),
        "dictMaxDataMtimeByStep": _fdictComputeMaxDataMtimeByStep(
            dictWorkflow, dictModTimes, dictVars,
        ),
        "dictMarkerMtimeByStep": _fdictComputeMarkerMtimeByStep(
            dictMarkerPathsByStep, dictModTimes,
        ),
        "dictTestSourceMtimeByStep":
            _fdictComputeMaxTestSourceMtimeByStep(
                dictWorkflow, dictModTimes, dictVars,
            ),
        "dictTestCategoryMtimes": _fdictComputeTestCategoryMtimes(
            dictWorkflow, dictModTimes, dictVars,
        ),
    }




def _fdictManifestShaCache(dictCtx, sContainerId):
    """Return the per-container in-memory mtime->sha output cache.

    Process lifetime is the cache's honest scope: every entry is
    revalidated against the container mtime fetched this same poll
    before it is reused, so a server restart only costs one rehash
    batch and a stale entry can never outlive its file.
    """
    dictByContainer = dictCtx.setdefault("dictManifestShaCache", {})
    return dictByContainer.setdefault(sContainerId, {})


def _flistAllOutputRepoPaths(dictWorkflow, sRepoRoot):
    """Return deduplicated repo-relative declared outputs across steps."""
    from ..fileStatusManager import _flistStepOutputsRepoRelative
    setPaths = set()
    for dictStep in dictWorkflow.get("listSteps", []) or []:
        setPaths.update(
            _flistStepOutputsRepoRelative(dictStep, sRepoRoot),
        )
    return sorted(sPath for sPath in setPaths if sPath)


def _fiCoercePollMtime(mtimeValue):
    """Return the poll mtime as an int, or None when absent/malformed."""
    try:
        return int(float(mtimeValue))
    except (TypeError, ValueError):
        return None


def _ftSplitCachedAndChanged(listRelPaths, dictMtimesRel, dictShaCache):
    """Split outputs into cache-validated seed entries and a rehash list."""
    dictSeed = {}
    listNeedHash = []
    for sRelPath in listRelPaths:
        iMtime = _fiCoercePollMtime(dictMtimesRel.get(sRelPath))
        dictEntry = dictShaCache.get(sRelPath) or {}
        bCacheValid = (
            iMtime is not None
            and dictEntry.get("iMtime") == iMtime
            and dictEntry.get("sSha256")
        )
        if bCacheValid:
            dictSeed[sRelPath] = {
                "sSha256": dictEntry["sSha256"],
                "sSymlinkSegment": None, "bEscapesRoot": False,
            }
        else:
            listNeedHash.append(sRelPath)
    return dictSeed, listNeedHash


def _fnUpdateShaCache(dictShaCache, filesPoll, listHashed, dictMtimesRel):
    """Record freshly hashed outputs in the in-memory cache."""
    dictFresh = filesPoll.fdictHashFiles(listHashed)
    for sRelPath in listHashed:
        sSha256 = (dictFresh.get(sRelPath) or {}).get("sSha256")
        iMtime = _fiCoercePollMtime(dictMtimesRel.get(sRelPath))
        if sSha256 and iMtime is not None:
            dictShaCache[sRelPath] = {
                "iMtime": iMtime, "sSha256": sSha256,
            }


def _ffilesFetchPollSnapshot(
    dictCtx, sContainerId, dictWorkflow, dictModTimes,
):
    """Fetch the one-exec container snapshot every poll gate reads.

    Returns the raw repo path string (host dual-accept) when there is
    no project repo or the context predates the ``files`` callable, so
    legacy callers and tests keep host-clone semantics.
    """
    from vaibify.reproducibility.levelGates import _flistAllStepScriptPaths
    from vaibify.reproducibility.repoFiles import SnapshotRepoFiles
    sRepoRoot = dictWorkflow.get("sProjectRepoPath", "")
    if not sRepoRoot or dictCtx.get("files") is None:
        return sRepoRoot
    dictMtimesRel = fdictAbsKeysToRepoRelative(
        dict(dictModTimes), sRepoRoot,
    )
    dictShaCache = _fdictManifestShaCache(dictCtx, sContainerId)
    dictSeed, listNeedHash = _ftSplitCachedAndChanged(
        _flistAllOutputRepoPaths(dictWorkflow, sRepoRoot),
        dictMtimesRel, dictShaCache,
    )
    filesPoll = SnapshotRepoFiles.ffilesFetch(
        dictCtx["docker"], sContainerId, sRepoRoot,
        listScriptRelPaths=_flistAllStepScriptPaths(dictWorkflow),
        listHashRelPaths=listNeedHash,
        dictSeedHashes=dictSeed,
    )
    _fnUpdateShaCache(dictShaCache, filesPoll, listNeedHash, dictMtimesRel)
    return filesPoll


def _fdictBuildPollResponseRest(
    dictWorkflow, dictModTimes, dictVars, dictReload,
    sWorkflowPath, listInvalidated, sRepoRoot, filesPoll=None,
):
    """Return every poll-response key except ``dictModTimes``.

    The outer ``_fdictFetchOutputStatus`` owns the ``dictModTimes``
    normalization so the wire-format invariant has a single
    inspect-this-function home. Helpers here operate on the
    absolute-keyed mtimes dict. ``filesPoll`` is the per-poll container
    snapshot (one docker exec, fetched by the caller); every level
    gate and the manifest short-circuit read it so the dashboard
    reflects container truth. ``sRepoRoot`` remains for path math.
    """
    if filesPoll is None:
        filesPoll = sRepoRoot
    dictMtimes, dictScriptStatus = _ftComputePollScriptContext(
        dictWorkflow, dictModTimes, dictVars, sWorkflowPath,
        sRepoRoot, filesPoll,
    )
    dictGates = _fdictComputePollLevelGates(
        dictWorkflow, dictMtimes, dictScriptStatus, filesPoll,
    )
    return _fdictAssemblePollResponse(
        dictWorkflow, dictModTimes, dictReload, listInvalidated,
        dictMtimes, dictScriptStatus, dictGates, filesPoll,
    )


def _ftComputePollScriptContext(
    dictWorkflow, dictModTimes, dictVars, sWorkflowPath,
    sRepoRoot, filesPoll,
):
    """Compute the per-step mtimes and script status for one poll."""
    dictMarkerPathsByStep = fnCollectMarkerPathsByStep(
        dictWorkflow, sRepoRoot, sWorkflowPath,
    )
    dictMtimes = _fdictComputeAllPerStepMtimes(
        dictWorkflow, dictModTimes, dictVars, dictMarkerPathsByStep,
    )
    dictScriptStatus = _fdictBuildScriptStatus(
        dictWorkflow, dictModTimes, dictVars,
        dictMarkerMtimeByStep=dictMtimes["dictMarkerMtimeByStep"],
        filesRepo=filesPoll,
    )
    return dictMtimes, dictScriptStatus


def _fdictComputePollLevelGates(
    dictWorkflow, dictMtimes, dictScriptStatus, filesPoll,
):
    """Evaluate the AICS level and the three blocker lists for one poll."""
    from vaibify.reproducibility.levelGates import (
        fiAICSLevel, flistLevel1Blockers, flistLevel2Blockers,
        flistLevel3Blockers,
    )
    dictWorkflow["iAICSLevel"] = fiAICSLevel(
        dictWorkflow, filesPoll, dictScriptStatus,
    )
    return {
        "listBlockers": flistLevel1Blockers(
            dictWorkflow, dictMtimes["dictMaxMtimeByStep"], filesPoll,
            dictScriptStatus,
        ),
        "listLevel2Blockers": flistLevel2Blockers(
            dictWorkflow, filesPoll,
        ),
        "listLevel3Blockers": flistLevel3Blockers(
            dictWorkflow, filesPoll,
        ),
    }


def _fdictAssemblePollResponse(
    dictWorkflow, dictModTimes, dictReload, listInvalidated,
    dictMtimes, dictScriptStatus, dictGates, filesPoll,
):
    """Assemble the poll wire payload from the computed pieces."""
    dictLevelPayload = _fdictBuildLevelStatePayload(
        dictWorkflow, dictGates["listBlockers"],
        dictGates["listLevel2Blockers"], dictGates["listLevel3Blockers"],
    )
    return {
        **dictLevelPayload,
        **_fdictBuildBlockerWireKeys(dictGates),
        **dictMtimes,
        "dictWorkflowEnvelopeDetail": _fdictBuildWorkflowEnvelopeDetail(
            dictWorkflow, filesPoll,
        ),
        "iAICSLevel": dictWorkflow["iAICSLevel"],
        "dictInvalidatedSteps": listInvalidated,
        "dictScriptStatus": dictScriptStatus,
        "listStaleOutputAdvisories": _flistBuildStaleOutputAdvisories(
            dictWorkflow, dictModTimes,
        ),
        **_fdictBuildReloadWireKeys(dictReload),
    }


def _fdictBuildBlockerWireKeys(dictGates):
    """Build the blocker-list and unique-step-count wire keys."""
    return {
        "listBlockers": dictGates["listBlockers"],
        "iL1BlockerCount": _fiCountUniqueBlockingSteps(
            dictGates["listBlockers"],
        ),
        "listLevel2Blockers": dictGates["listLevel2Blockers"],
        "iL2BlockerCount": _fiCountUniqueBlockingSteps(
            dictGates["listLevel2Blockers"],
        ),
        "listLevel3Blockers": dictGates["listLevel3Blockers"],
        "iL3BlockerCount": _fiCountUniqueBlockingSteps(
            dictGates["listLevel3Blockers"],
        ),
    }


def _fdictBuildReloadWireKeys(dictReload):
    """Build the workflow-reload wire keys of the poll payload."""
    return {
        "bWorkflowReloaded": dictReload["bReplaced"],
        "sWorkflowReloadError": dictReload["sError"],
        "dictWorkflow": _fdictBuildReloadedWorkflowShape(dictReload),
    }


_S_LEVEL_RATCHET_FLAG_KEY = "_bLevelHighWaterChanged"


def _ftComputeLevelStates(
    dictWorkflow, listBlockers, listLevel2Blockers, listLevel3Blockers,
):
    """Project blocker lists into per-step and workflow-scope states."""
    from vaibify.reproducibility.levelGates import (
        fdictComputeStepLevelStates,
        fdictComputeWorkflowScopeLevelStates,
    )
    dictStepStates = fdictComputeStepLevelStates(
        dictWorkflow, listBlockers, listLevel2Blockers,
        listLevel3Blockers,
    )
    dictScopeStates = fdictComputeWorkflowScopeLevelStates(
        dictWorkflow, listLevel2Blockers, listLevel3Blockers,
    )
    return dictStepStates, dictScopeStates


def _fdictKeyStatesByStepString(dictStepStates):
    """Re-key per-step level states by step-index string for the wire."""
    return {
        str(iIndex): dictStates
        for iIndex, dictStates in dictStepStates.items()
    }


def _fdictProjectStepLevelHighWater(dictWorkflow):
    """Project each step's high-water stamps, keyed by step-index string.

    Sourced from the merged step dicts so the frontend reads
    regression memory straight off the poll payload — a step whose
    levels regressed keeps its first-attainment timestamps here.
    """
    dictResult = {}
    listSteps = dictWorkflow.get("listSteps", []) or []
    for iIndex, dictStep in enumerate(listSteps):
        if isinstance(dictStep, dict):
            dictResult[str(iIndex)] = dict(
                dictStep.get("dictLevelHighWater") or {},
            )
    return dictResult


def _fdictBuildLevelStatePayload(
    dictWorkflow, listBlockers, listLevel2Blockers, listLevel3Blockers,
):
    """Build the level-state wire keys plus the private ratchet flag.

    ``dictStepLevels`` / ``dictWorkflowScopeLevels`` carry the
    independent-level cell dicts (``{"sState", "iSatisfied",
    "iTotal", "bRegression"}`` per level — see
    ``levelGates.fdictComputeStepLevelStates``);
    ``dictStepLevelWarnings`` carries the consolidated per-step
    regression-column warning (``{"iLowestNonAttainedLevel",
    "iWarningLevel", "sWarningSeverity", "sWarningHint"}``). The
    ratchet runs here so a freshly attained level appears in the
    same poll payload that attained it; only ``attained`` cells
    stamp. The private flag key is popped by
    ``_fnSaveIfLevelHighWaterChanged`` before the response is
    returned — it must never reach the wire.
    """
    from .. import stateManager
    dictStepStates, dictScopeStates = _ftComputeLevelStates(
        dictWorkflow, listBlockers, listLevel2Blockers,
        listLevel3Blockers,
    )
    bChanged = stateManager.fbRatchetLevelHighWater(
        dictWorkflow, dictStepStates, dictScopeStates,
    )
    dictPayload = _fdictBuildLevelStateWireKeys(
        dictWorkflow, dictStepStates, dictScopeStates, listBlockers,
    )
    dictPayload[_S_LEVEL_RATCHET_FLAG_KEY] = bChanged
    return dictPayload


def _fdictBuildLevelStateWireKeys(
    dictWorkflow, dictStepStates, dictScopeStates, listBlockers,
):
    """Build the public level-state keys of the poll payload.

    Runs after the ratchet so freshly stamped high-water marks are
    already on the workflow when the projection reads them.
    """
    from vaibify.reproducibility.levelGates import (
        fdictComputeStepLevelWarnings,
    )
    dictStepWarnings = fdictComputeStepLevelWarnings(
        dictWorkflow, dictStepStates, listBlockers,
    )
    return {
        "dictStepLevels": _fdictKeyStatesByStepString(dictStepStates),
        "dictStepLevelWarnings":
            _fdictKeyStatesByStepString(dictStepWarnings),
        "dictStepLevelHighWater":
            _fdictProjectStepLevelHighWater(dictWorkflow),
        "dictWorkflowScopeLevels": dictScopeStates,
        "dictWorkflowLevelHighWater": dict(
            dictWorkflow.get("dictWorkflowLevelHighWater") or {},
        ),
    }


def _fnSaveIfLevelHighWaterChanged(
    dictCtx, sContainerId, dictWorkflow, dictRest,
):
    """Pop the private ratchet flag and persist at most one save.

    Mirrors the ``bAnyReconciled`` conditional-save precedent in
    ``_flistRunPollSideEffects``: a level transition stamps new
    high-water timestamps onto the workflow, which must be saved
    exactly once; steady-state polls save nothing.
    """
    if dictRest.pop(_S_LEVEL_RATCHET_FLAG_KEY, False):
        dictCtx["save"](sContainerId, dictWorkflow)


_T_ENVELOPE_SYNC_SERVICES = ("github", "zenodo", "overleaf", "arxiv")


def _fdictBuildWorkflowEnvelopeDetail(dictWorkflow, filesPoll):
    """Assemble the expandable Workflow-row envelope payload.

    Built entirely from sources this poll already fetched (the
    one-exec container snapshot plus the workflow dict) — NO
    additional container execs. Wire shape::

        {"listBinaries": [...per-binary capture status...],
         "dictArtifacts": {sName: {"bPresent", "bSatisfied"}}
             (empty dict when there is no project repo),
         "dictDeterminism": declared dict or None,
         "dictRemoteSyncs": {sService: dictSummary or None}}
    """
    from vaibify.reproducibility.repoFiles import (
        ffilesEnsureRepoFiles, fsRepoRootOf,
    )
    filesRepo = ffilesEnsureRepoFiles(filesPoll)
    bHasRepo = bool(fsRepoRootOf(filesRepo))
    return {
        "listBinaries": _flistEnvelopeBinaries(
            dictWorkflow, filesRepo, bHasRepo,
        ),
        "dictArtifacts": (
            _fdictEnvelopeArtifacts(dictWorkflow, filesRepo)
            if bHasRepo else {}
        ),
        "dictDeterminism":
            (dictWorkflow or {}).get("dictDeterminism") or None,
        "dictRemoteSyncs": (
            _fdictEnvelopeRemoteSyncs(filesRepo) if bHasRepo
            else dict.fromkeys(_T_ENVELOPE_SYNC_SERVICES)
        ),
    }


def _flistEnvelopeBinaries(dictWorkflow, filesRepo, bHasRepo):
    """Project declared binaries against the environment.json captures."""
    dictCapturesByPath = _fdictIndexCapturedBinariesByPath(
        filesRepo, bHasRepo,
    )
    listResult = []
    for dictDeclared in (
        (dictWorkflow or {}).get("listDeclaredBinaries") or []
    ):
        if isinstance(dictDeclared, dict):
            listResult.append(_fdictEnvelopeBinaryEntry(
                dictDeclared,
                dictCapturesByPath.get(dictDeclared.get("sBinaryPath")),
            ))
    return listResult


def _fdictIndexCapturedBinariesByPath(filesRepo, bHasRepo):
    """Index the environment.json binary captures by binary path."""
    from vaibify.reproducibility.environmentSnapshot import (
        _flistResolveCapturedBinaries,
        fdictReadEnvironmentJson,
    )
    dictEnvironment = {}
    if bHasRepo:
        dictEnvironment = fdictReadEnvironmentJson(filesRepo) or {}
    dictResult = {}
    for dictCapture in _flistResolveCapturedBinaries(dictEnvironment):
        if isinstance(dictCapture, dict):
            dictResult[dictCapture.get("sBinaryPath")] = dictCapture
    return dictResult


def _fdictEnvelopeBinaryEntry(dictDeclared, dictCapture):
    """Build one wire entry for a declared binary; nulls stay null.

    ``bVersionMatch`` is True/False only when both the expected and
    captured versions are known, ``None`` otherwise — an unknowable
    comparison must not render as a pass or a fail. ``bHashCurrent``
    requires a capture with a non-null sha256.
    """
    sExpected = dictDeclared.get("sExpectedVersion") or None
    sCapturedVersion = (dictCapture or {}).get("sVersion") or None
    sCapturedSha256 = (dictCapture or {}).get("sSha256") or None
    bVersionMatch = None
    if sExpected and sCapturedVersion:
        bVersionMatch = sExpected == sCapturedVersion
    return {
        "sBinaryPath": dictDeclared.get("sBinaryPath") or "",
        "sPurpose": dictDeclared.get("sPurpose") or "",
        "sExpectedVersion": sExpected,
        "sCapturedVersion": sCapturedVersion,
        "sCapturedSha256": sCapturedSha256,
        "bVersionMatch": bVersionMatch,
        "bHashCurrent": bool(sCapturedSha256),
    }


def _fdictEnvelopeArtifacts(dictWorkflow, filesRepo):
    """Pair on-disk presence with the L3 verdict for each artifact."""
    dictPresence = _fdictEnvelopeArtifactPresence(filesRepo)
    dictSatisfaction = _fdictEnvelopeArtifactSatisfaction(
        dictWorkflow, filesRepo,
    )
    return {
        sName: {
            "bPresent": bool(dictPresence[sName]),
            "bSatisfied": bool(dictSatisfaction[sName]),
        }
        for sName in dictPresence
    }


def _fdictEnvelopeArtifactPresence(filesRepo):
    """Return on-disk presence for the five envelope artifacts."""
    from vaibify.reproducibility.dependencyPinning import _S_LOCK_FILENAME
    from vaibify.reproducibility.dockerfileLint import fbDockerfilePresent
    from vaibify.reproducibility.environmentSnapshot import (
        fdictReadEnvironmentJson,
    )
    from vaibify.reproducibility.reproduceScriptGenerator import (
        S_REPRODUCE_SCRIPT_FILENAME,
    )
    from ..hashStaleness import fbManifestExists
    return {
        "manifest": fbManifestExists(filesRepo),
        "dependencyLock": filesRepo.fbIsFile(_S_LOCK_FILENAME),
        "environmentSnapshot":
            fdictReadEnvironmentJson(filesRepo) is not None,
        "dockerfile": fbDockerfilePresent(filesRepo),
        "reproduceScript":
            filesRepo.fbIsFile(S_REPRODUCE_SCRIPT_FILENAME),
    }


def _fdictEnvelopeArtifactSatisfaction(dictWorkflow, filesRepo):
    """Return the L3 readiness verdict for the five envelope artifacts."""
    from vaibify.reproducibility import levelGates
    return {
        "manifest": levelGates.fbVerifyManifestComplete(
            filesRepo, dictWorkflow,
        ),
        "dependencyLock": levelGates.fbVerifyDependencyLock(filesRepo),
        "environmentSnapshot": levelGates.fbVerifyEnvironmentSnapshot(
            filesRepo,
        ),
        "dockerfile": levelGates.fbVerifyDockerfilePinned(filesRepo),
        "reproduceScript": levelGates.fbVerifyReproduceScript(
            filesRepo, dictWorkflow,
        ),
    }


def _fdictEnvelopeRemoteSyncs(filesRepo):
    """Return the cached verify summary per remote service, or None.

    Reads only the already-snapshotted ``syncStatus.json`` — a service
    with no cached verify maps to ``None``, never a fabricated
    summary.
    """
    from vaibify.reproducibility import scheduledReverify
    dictResult = {}
    for sService in _T_ENVELOPE_SYNC_SERVICES:
        dictStatus = scheduledReverify.fdictReadCachedSyncStatus(
            filesRepo, sService,
        )
        dictResult[sService] = _fdictProjectSyncSummary(dictStatus)
    return dictResult


def _fdictProjectSyncSummary(dictStatus):
    """Project one cached verify status onto the wire summary, or None."""
    from vaibify.reproducibility.levelGates import (
        F_MAX_STALE_HOURS,
        _fbCachedSyncStatusFresh,
    )
    if not dictStatus or not dictStatus.get("sLastVerified"):
        return None
    return {
        "sLastVerified": dictStatus.get("sLastVerified"),
        "iTotalFiles": int(dictStatus.get("iTotalFiles") or 0),
        "iMatching": int(dictStatus.get("iMatching") or 0),
        "iDivergedCount": len(dictStatus.get("listDiverged") or []),
        "bStale": not _fbCachedSyncStatusFresh(
            dictStatus, F_MAX_STALE_HOURS,
        ),
    }


def _fiCountUniqueBlockingSteps(listBlockers):
    """Return the count of distinct step indices appearing in blockers.

    Workflow-scope entries carry ``iStepIndex=-1`` and intentionally
    count as one slot in this total, so a header like "3 steps
    blocking L2" reflects two real steps plus one workflow-scope
    issue when present. Stage 6 consumes this directly.
    """
    setSteps = set()
    for dictEntry in listBlockers or []:
        iIndex = dictEntry.get("iStepIndex")
        if isinstance(iIndex, int):
            setSteps.add(iIndex)
    return len(setSteps)


def _flistBuildStaleOutputAdvisories(dictWorkflow, dictModTimes):
    """Return the stale-output advisories the dashboard renders next poll."""
    from ..staleOutputDetector import flistStaleOutputAdvisories
    from ..workflowManager import fdictBuildDirectDependencies
    dictDirect = fdictBuildDirectDependencies(dictWorkflow)
    dictDeclaredUpstream = _fdictInvertDirectGraph(dictDirect)
    return flistStaleOutputAdvisories(
        dictWorkflow, dictModTimes, dictDeclaredUpstream,
    )


def _fdictInvertDirectGraph(dictDirect):
    """Invert producer->consumers map into a consumer->producers map."""
    dictUpstream = {}
    for iProducer, setConsumers in (dictDirect or {}).items():
        for iConsumer in setConsumers or set():
            dictUpstream.setdefault(iConsumer, set()).add(iProducer)
    return dictUpstream


def _fdictBuildReloadedWorkflowShape(dictReload):
    """Return the wire-shaped workflow dict to send back on reload, or None."""
    if not dictReload["bReplaced"]:
        return None
    from ..pipelineUtils import fdictWorkflowWithLabels
    return fdictWorkflowWithLabels(dictReload["dictWorkflow"])


async def _fdictFetchTestStatus(
    dictCtx, sContainerId, dictWorkflow,
    dictMaxOutputMtimeByStep=None,
):
    """Fetch test markers, refresh conftest, migrate flat markers, build status."""
    listStepDirs = _flistExtractStepDirectories(dictWorkflow)
    sProjectRepoPath = dictWorkflow.get("sProjectRepoPath", "")
    sWorkflowPath = dictCtx["paths"].get(sContainerId, "")
    sWorkflowSlug = fsWorkflowSlugFromPath(sWorkflowPath)
    await _fnRefreshConftestsAndMigrateMarkers(
        dictCtx["docker"], sContainerId, listStepDirs,
        sProjectRepoPath, sWorkflowSlug,
    )
    dictTestInfo = await asyncio.to_thread(
        _fdictFetchTestMarkers,
        dictCtx["docker"], sContainerId, listStepDirs,
        sProjectRepoPath, sWorkflowSlug,
    )
    dictTestMarkers = _fdictBuildTestMarkerStatus(
        dictWorkflow, dictTestInfo,
        dictMaxOutputMtimeByStep=dictMaxOutputMtimeByStep,
    )
    bChanged = _fnApplyExternalTestResults(
        dictWorkflow, dictTestMarkers,
    )
    bChanged = fbRefreshAggregateTestStates(dictWorkflow) or bChanged
    if bChanged:
        dictCtx["save"](sContainerId, dictWorkflow)
    return {
        "dictTestMarkers": dictTestMarkers,
        "dictTestFileChanges": _fdictBuildTestFileChanges(
            dictWorkflow, dictTestInfo,
        ),
    }


def _flistExtractStepDirectories(dictWorkflow):
    """Return a list of step directories from the workflow."""
    listDirs = []
    for dictStep in dictWorkflow.get("listSteps", []):
        sDir = dictStep.get("sDirectory", "")
        if sDir:
            listDirs.append(sDir)
    return listDirs


def _fdictFetchTestMarkers(
    connectionDocker, sContainerId, listStepDirs,
    sProjectRepoPath, sWorkflowSlug,
):
    """Run the batched test-marker check command."""
    from .. import syncDispatcher as _syncDispatcher
    sCommand = _syncDispatcher.fsBuildTestMarkerCheckCommand(
        listStepDirs, sProjectRepoPath, sWorkflowSlug,
    )
    try:
        iExit, sOutput = connectionDocker.ftResultExecuteCommand(
            sContainerId, sCommand
        )
    except (APIError, NotFound):
        return {
            "markers": {},
            "testFiles": {},
            "missingConftest": [],
        }
    if iExit != 0:
        return {
            "markers": {},
            "testFiles": {},
            "missingConftest": [],
        }
    return _syncDispatcher.fdictParseTestMarkerOutput(sOutput)


async def _fnRefreshConftestsAndMigrateMarkers(
    connectionDocker, sContainerId, listStepDirs,
    sProjectRepoPath, sWorkflowSlug,
):
    """Refresh outdated conftest.py copies and migrate flat-layout markers.

    Replaces the older missing-only backfill: when the template's
    version stamp bumps, every previously-written conftest gets
    rewritten on the next connect tick so test-framework behaviour
    can't drift between fresh and old workspaces. The flat-marker
    migration moves markers from the legacy
    ``.vaibify/test_markers/<step>.json`` layout into the per-slug
    subdir so older workspaces don't strand results. Both run off the
    event loop and short-circuit when there is nothing to do.
    """
    if not listStepDirs:
        return
    from ..conftestManager import (
        fnEnsureConftestsCurrent, fnMigrateFlatMarkers,
    )
    await asyncio.to_thread(
        fnEnsureConftestsCurrent,
        connectionDocker, sContainerId,
        listStepDirs, sProjectRepoPath,
    )
    await asyncio.to_thread(
        fnMigrateFlatMarkers,
        connectionDocker, sContainerId,
        sProjectRepoPath, sWorkflowSlug,
    )


# In-container script that scrubs legacy markers (missing sRunAtUtc).
# Receives a JSON list of marker paths on stdin; prints one line per
# removal so the host can log what was scrubbed.
_S_LEGACY_MARKER_DELETE_SCRIPT = (
    "import json, os, sys\n"
    "for sPath in json.loads(sys.stdin.read()):\n"
    "    if not os.path.isfile(sPath):\n"
    "        continue\n"
    "    try:\n"
    "        dictMarker = json.load(open(sPath))\n"
    "    except Exception:\n"
    "        continue\n"
    "    if 'sRunAtUtc' in dictMarker:\n"
    "        continue\n"
    "    try:\n"
    "        os.remove(sPath)\n"
    "        print(sPath)\n"
    "    except OSError:\n"
    "        pass\n"
)


def _flistMarkerPathsForSteps(listStepDirs, sProjectRepoPath):
    """Return the absolute marker file path for each step directory."""
    return [
        posixpath.join(
            sProjectRepoPath, ".vaibify", "test_markers",
            fsMarkerNameFromStepDirectory(sDir),
        )
        for sDir in listStepDirs
    ]


def _fnDeleteLegacyMarkers(
    connectionDocker, sContainerId, listStepDirs, sProjectRepoPath,
):
    """Delete markers written by the pre-2026-04 conftest format.

    The legacy conftest produced markers without ``sRunAtUtc`` /
    ``dictOutputHashes``. Once a step's stale conftest has been
    overwritten, any leftover legacy marker at the new path no longer
    reflects reality but the polling reconciliation would still apply
    its (stale) results — flashing "passed" on the badge before the
    user runs tests for real. Removing those markers makes the badge
    show "untested" until a fresh pytest run with the new conftest
    writes a trustworthy marker.
    """
    if not sProjectRepoPath or not listStepDirs:
        return
    listMarkerPaths = _flistMarkerPathsForSteps(
        listStepDirs, sProjectRepoPath,
    )
    sCmd = (
        "python3 -c " + fsShellQuote(_S_LEGACY_MARKER_DELETE_SCRIPT)
        + " <<< " + fsShellQuote(json.dumps(listMarkerPaths))
    )
    iExit, sOutput = connectionDocker.ftResultExecuteCommand(
        sContainerId, sCmd,
    )
    listDeleted = [s for s in (sOutput or "").splitlines() if s.strip()]
    if listDeleted:
        logger.info(
            "Deleted %d legacy markers: %s",
            len(listDeleted), listDeleted,
        )


def _fnEnsureConftestTemplate(
    connectionDocker, sContainerId, sContent,
):
    """Ship conftest template to /usr/share/vaibify/."""
    sTemplatePath = "/usr/share/vaibify/conftest_marker.py"
    iExit, _ = connectionDocker.ftResultExecuteCommand(
        sContainerId,
        "test -f " + sTemplatePath,
    )
    if iExit == 0:
        return
    connectionDocker.ftResultExecuteCommand(
        sContainerId,
        "mkdir -p /usr/share/vaibify",
    )
    connectionDocker.fnWriteFile(
        sContainerId, sTemplatePath,
        sContent.encode("utf-8"),
    )


def _fdictBuildTestMarkerStatus(
    dictWorkflow, dictTestInfo, dictMaxOutputMtimeByStep=None,
):
    """Map test markers to step indices and check staleness.

    ``dictMaxOutputMtimeByStep`` (str step index → mtime string) lets
    the staleness check recognise a marker as out-of-date when the
    step's output files have been regenerated since pytest last ran.
    """
    dictMarkers = dictTestInfo.get("markers", {})
    dictTestFiles = dictTestInfo.get("testFiles", {})
    dictMaxMtimes = dictMaxOutputMtimeByStep or {}
    dictResult = {}
    for iIndex, dictStep in enumerate(
        dictWorkflow.get("listSteps", [])
    ):
        sDir = dictStep.get("sDirectory", "")
        if not sDir:
            continue
        sMarkerName = fsMarkerNameFromStepDirectory(sDir)
        if sMarkerName not in dictMarkers:
            continue
        dictMarker = dictMarkers[sMarkerName]
        fMaxOutputMtime = _ffParseMtime(
            dictMaxMtimes.get(str(iIndex)),
        )
        bStale = _fbMarkerStale(
            dictMarker, dictTestFiles.get(sDir, {}),
            fMaxOutputMtime=fMaxOutputMtime,
        )
        dictResult[str(iIndex)] = {
            "dictMarker": dictMarker, "bStale": bStale,
        }
    return dictResult


def _ffParseMtime(sMtime):
    """Return mtime as float, 0.0 when missing or unparseable."""
    if not sMtime:
        return 0.0
    try:
        return float(sMtime)
    except (TypeError, ValueError):
        return 0.0


def _fbMarkerStale(dictMarker, dictTestFileInfo, fMaxOutputMtime=0):
    """Return True if the marker no longer reflects the current state.

    A marker is stale when any of:

    1. It lacks ``sRunAtUtc`` (legacy pre-2026-04 conftest format —
       cannot be trusted to map to any specific data state).
    2. Any test file is newer than the marker (existing behaviour).
    3. Any output file is newer than the marker — i.e. the data the
       step's tests would run against has moved since the recorded
       result, so the result no longer applies.
    """
    if "sRunAtUtc" not in dictMarker:
        return True
    fMarkerTime = dictMarker.get("fTimestamp", 0)
    dictMtimes = dictTestFileInfo.get("dictMtimes", {})
    for fMtime in dictMtimes.values():
        if fMtime > fMarkerTime:
            return True
    if fMaxOutputMtime and fMaxOutputMtime > fMarkerTime:
        return True
    return False


_LIST_MARKER_CATEGORY_KEYS = [
    ("integrity", "sIntegrity"),
    ("qualitative", "sQualitative"),
    ("quantitative", "sQuantitative"),
]


def _fnApplyAllMarkerCategories(dictVerify, dictCategories):
    """Apply all marker categories to a verification dict."""
    bChanged = False
    for sCategory, sVerifyKey in _LIST_MARKER_CATEGORY_KEYS:
        if _fnApplyMarkerCategory(
            dictVerify, dictCategories, sCategory, sVerifyKey,
        ):
            bChanged = True
    return bChanged


def _fnClearStaleMarkerCategories(dictVerify, dictCategories):
    """Reset to "untested" any category the stale marker would touch.

    A stale marker isn't trustworthy enough to apply, but it does tell
    us *which* categories used to have a result. Resetting those to
    "untested" makes the badge accurately reflect "no fresh result for
    the current state" instead of preserving a prior pass/fail value
    that's now meaningless. "unnecessary" categories are skipped — a
    stale marker targeting an empty-commands category is anomalous and
    must not downgrade the derived state.
    """
    bChanged = False
    for sCategory, sVerifyKey in _LIST_MARKER_CATEGORY_KEYS:
        if sCategory not in dictCategories:
            continue
        sCurrent = dictVerify.get(sVerifyKey)
        if sCurrent in ("untested", "unnecessary"):
            continue
        dictVerify[sVerifyKey] = "untested"
        bChanged = True
    return bChanged


def _fnApplyExternalTestResults(dictWorkflow, dictTestMarkers):
    """Update workflow dictVerification from external test markers.

    Returns True when any verification field was modified, so the
    caller can persist the workflow.
    """
    listSteps = dictWorkflow.get("listSteps", [])
    bChanged = False
    for sIndex, dictEntry in dictTestMarkers.items():
        iIndex = int(sIndex)
        if iIndex >= len(listSteps):
            continue
        dictVerify = listSteps[iIndex].setdefault(
            "dictVerification", {},
        )
        dictCategories = dictEntry["dictMarker"].get(
            "dictCategories", {},
        )
        if dictEntry.get("bStale"):
            if _fnClearStaleMarkerCategories(
                dictVerify, dictCategories,
            ):
                bChanged = True
            continue
        if _fnApplyAllMarkerCategories(
            dictVerify, dictCategories,
        ):
            bChanged = True
    return bChanged


def _fnApplyMarkerCategory(
    dictVerify, dictCategories, sCategory, sVerifyKey,
):
    """Apply a single category result from a marker; return True if changed.

    The truth-claim value is resolved by the canonical
    ``truthDerivation.fsResolveCategoryAxisFromCounts`` so the rule
    "what counts as passed/failed?" lives in one place; this site
    handles the sticky-``"unnecessary"`` policy that a workflow with
    no commands must never get silently re-locked by a stray marker.
    """
    from .. import truthDerivation
    if sCategory not in dictCategories:
        return False
    sNewValue = truthDerivation.fsResolveCategoryAxisFromCounts(
        dictCategories[sCategory],
    )
    if not sNewValue or dictVerify.get(sVerifyKey) == sNewValue:
        return False
    if dictVerify.get(sVerifyKey) == "unnecessary":
        logger.warning(
            "Marker reports %s for %s but the workflow declares this "
            "category as empty (\"unnecessary\"); ignoring marker so "
            "the derived state stays observable.",
            sNewValue, sVerifyKey,
        )
        return False
    dictVerify[sVerifyKey] = sNewValue
    return True


def _fsetExtractRegisteredTestFiles(dictStep):
    """Extract registered test file names from step test commands."""
    dictTests = dictStep.get("dictTests", {})
    setRegistered = set()
    for sCatKey in (
        "integrity", "qualitative", "quantitative"
    ):
        dictCat = dictTests.get(sCatKey, {})
        for sCmd in dictCat.get("saCommands", []):
            for sPart in sCmd.split():
                if (sPart.startswith("test_")
                        and sPart.endswith(".py")):
                    setRegistered.add(sPart)
                elif sPart.startswith("tests/test_"):
                    setRegistered.add(
                        sPart.replace("tests/", ""))
    return setRegistered


def _fdictExpectedTemplateHashes():
    """Return the per-category template hash baseline used to detect edits."""
    from ..testGenerator import (
        fsQuantitativeTemplateHash,
        fsIntegrityTemplateHash,
        fsQualitativeTemplateHash,
    )
    return {
        "test_quantitative.py": fsQuantitativeTemplateHash(),
        "test_integrity.py": fsIntegrityTemplateHash(),
        "test_qualitative.py": fsQualitativeTemplateHash(),
    }


def _fdictBuildStepTestChangeEntry(
    dictStep, dictDirInfo, dictExpectedHashes,
):
    """Return the per-step change entry, or ``{}`` if nothing differs."""
    listDiscovered = dictDirInfo.get("listFiles", [])
    setRegistered = _fsetExtractRegisteredTestFiles(dictStep)
    listNew = [f for f in listDiscovered if f not in setRegistered]
    listMissing = [f for f in setRegistered if f not in listDiscovered]
    listCustom = _flistFindCustomTestFiles(
        dictDirInfo.get("dictHashes", {}), dictExpectedHashes,
    )
    dictEntry = {}
    if listNew or listMissing:
        dictEntry["listNew"] = listNew
        dictEntry["listMissing"] = listMissing
    if listCustom:
        dictEntry["listCustom"] = listCustom
    return dictEntry


def _fdictBuildTestFileChanges(dictWorkflow, dictTestInfo):
    """Compare discovered test files against registered commands."""
    dictExpectedHashes = _fdictExpectedTemplateHashes()
    dictTestFiles = dictTestInfo.get("testFiles", {})
    dictResult = {}
    for iIndex, dictStep in enumerate(
        dictWorkflow.get("listSteps", [])
    ):
        sDir = dictStep.get("sDirectory", "")
        if sDir not in dictTestFiles:
            continue
        dictEntry = _fdictBuildStepTestChangeEntry(
            dictStep, dictTestFiles[sDir], dictExpectedHashes,
        )
        if dictEntry:
            dictResult[str(iIndex)] = dictEntry
    return dictResult


def _flistFindCustomTestFiles(
    dictFileHashes, dictExpectedHashes,
):
    """Return filenames whose hash differs from the template."""
    listCustom = []
    for sFilename, sExpected in dictExpectedHashes.items():
        sActual = dictFileHashes.get(sFilename)
        if sActual is not None and sActual != sExpected:
            listCustom.append(sFilename)
    return listCustom


def _fnRegisterManifestVerify(app, dictCtx):
    """Register POST /api/workflow/{id}/manifest/verify endpoint."""
    from vaibify.reproducibility import manifestWriter

    @fnAgentAction("verify-manifest")
    @app.post("/api/workflow/{sContainerId}/manifest/verify")
    async def fdictVerifyManifest(sContainerId: str):
        dictCtx["require"]()
        dictWorkflow = fdictRequireWorkflow(
            dictCtx["workflows"], sContainerId)
        filesRepo = ffilesForWorkflow(
            dictCtx, sContainerId, dictWorkflow,
        )
        listMismatches, listIncomplete = await _ftRunManifestVerify(
            manifestWriter, dictWorkflow, filesRepo,
        )
        return _fdictBuildManifestVerifyResult(
            filesRepo, listMismatches, listIncomplete,
        )


async def _ftRunManifestVerify(manifestWriter, dictWorkflow, filesRepo):
    """Run the verify+gap queries off the loop and translate failures.

    Raises ``HTTPException`` 409 on missing manifest, 422 on a
    malformed manifest. Returns ``(listMismatches, listIncomplete)``
    on success.
    """
    try:
        listMismatches = await asyncio.to_thread(
            manifestWriter.flistVerifyManifest, filesRepo,
        )
        listIncomplete = await asyncio.to_thread(
            manifestWriter.flistDeclaredButMissingFromManifest,
            filesRepo, dictWorkflow,
        )
    except FileNotFoundError as errorMissing:
        raise HTTPException(
            status_code=409,
            detail=(
                "MANIFEST.sha256 is missing. Run the workflow to "
                "regenerate the manifest before verifying."
            ),
        ) from errorMissing
    except ValueError as errorMalformed:
        raise HTTPException(
            status_code=422,
            detail=(
                "MANIFEST.sha256 is malformed and cannot be parsed. "
                "Regenerate the manifest before verifying."
            ),
        ) from errorMalformed
    return listMismatches, listIncomplete


def _fdictBuildManifestVerifyResult(
    filesRepo, listMismatches, listIncomplete,
):
    """Compose the manifest-verify response payload.

    ``iTotal`` reflects the number of entries actually recorded in
    ``MANIFEST.sha256`` rather than the workflow's declared outputs;
    the manifest is the authoritative source of truth for the verify
    operation. When the manifest is absent, ``iTotal`` falls back to 0.

    ``saIncomplete`` lists repo-relative paths the workflow currently
    declares but the manifest does not pin. The dashboard surfaces this
    so the user is not lulled by an "all clean" status when their
    legacy manifest was written before scripts and standards joined the
    envelope. An empty list means full coverage; a non-empty list is
    advisory, not a failure.
    """
    from vaibify.reproducibility import manifestWriter
    try:
        iTotal = manifestWriter.fiCountManifestEntries(filesRepo)
    except FileNotFoundError:
        iTotal = 0
    return {
        "iTotal": iTotal,
        "iMatching": iTotal - len(listMismatches),
        "listMismatches": listMismatches,
        "saIncomplete": list(listIncomplete),
    }


def fnRegisterAll(app, dictCtx):
    """Register all pipeline control routes."""
    _fnRegisterPipelineState(app, dictCtx)
    _fnRegisterHostLogTail(app, dictCtx)
    _fnRegisterPipelineKill(app, dictCtx)
    _fnRegisterPipelineClean(app, dictCtx)
    _fnRegisterPipelineWs(app, dictCtx)
    _fnRegisterAcknowledgeStep(app, dictCtx)
    _fnRegisterFileStatus(app, dictCtx)
    _fnRegisterWorkflowDiscovery(app, dictCtx)
    _fnRegisterManifestVerify(app, dictCtx)
