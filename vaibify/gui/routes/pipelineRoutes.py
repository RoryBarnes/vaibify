"""Pipeline control route handlers."""

__all__ = ["fnRegisterAll", "fdictComputeFileStatus"]

import asyncio
import json
import logging
import posixpath
import re
from datetime import datetime, timezone

from fastapi import HTTPException, WebSocket, WebSocketDisconnect

from ..actionCatalog import fnAgentAction
from ..pipelineRunner import fsShellQuote
from ..pipelineServer import (
    WORKSPACE_ROOT,
    fbValidateWebSocketOrigin,
    fdictRequireWorkflow,
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
    fsMarkerNameFromStepDirectory,
)
from ..fileIntegrity import flistExtractAllScriptPaths
from ..pathContract import fdictAbsKeysToRepoRelative
from ..randomnessLint import fnApplyRandomnessLintToWorkflow
from ..llmInvoker import fsReadFileFromContainer

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


def _fnMarkPipelineStopped(connectionDocker, sContainerId):
    """Write a stopped state file so the UI shows not running."""
    from .. import pipelineState
    dictState = pipelineState.fdictReadState(
        connectionDocker, sContainerId)
    if dictState is None or not dictState.get("bRunning"):
        return
    pipelineState.fnUpdateState(
        connectionDocker, sContainerId, dictState,
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
    """Register GET /api/pipeline/{id}/state endpoint."""

    @app.get("/api/pipeline/{sContainerId}/state")
    async def fnGetPipelineState(sContainerId: str):
        from ..pipelineState import fdictReadState
        dictCtx["require"]()
        dictState = await asyncio.to_thread(
            fdictReadState, dictCtx["docker"], sContainerId
        )
        if dictState is None:
            return {"bRunning": False}
        return await _fdictReconcileLivenessIfNeeded(
            dictCtx["docker"], sContainerId, dictState
        )


async def _fdictReconcileLivenessIfNeeded(
    connectionDocker, sContainerId, dictState,
):
    """Detect a runner that vanished without finalizing and update state.

    Pure read-side check: ``sLastHeartbeat`` is the truth signal. If
    the recorded heartbeat is older than the staleness window and the
    state still claims ``bRunning: True``, we declare the runner dead,
    overwrite the state file with ``bRunning: False`` and a populated
    ``sFailureReason``, then return the reconciled state. Subsequent
    polls see ``bRunning: False`` and fall through unchanged.
    """
    from .. import pipelineState
    if not dictState.get("bRunning"):
        return dictState
    if not pipelineState.fbHeartbeatIsStale(dictState):
        return dictState
    sFailureReason = _fsBuildHeartbeatStaleReason(dictState)
    dictReconciled = dict(dictState)
    dictReconciled.update(
        pipelineState.fdictBuildCompletedState(
            pipelineState.I_EXIT_CODE_RUNNER_DISAPPEARED))
    dictReconciled["sFailureReason"] = sFailureReason
    await asyncio.to_thread(
        pipelineState.fnWriteState,
        connectionDocker, sContainerId, dictReconciled,
    )
    return dictReconciled


def _fsBuildHeartbeatStaleReason(dictState):
    """Format a human-readable reason string for a stale heartbeat."""
    from .. import pipelineState
    sLastHeartbeat = dictState.get("sLastHeartbeat", "")
    try:
        dtBeat = datetime.fromisoformat(sLastHeartbeat)
        fAgeSeconds = (
            datetime.now(timezone.utc).timestamp() - dtBeat.timestamp())
        return (
            f"heartbeat_stale (last beat {fAgeSeconds:.0f}s ago, "
            f"window {pipelineState.I_HEARTBEAT_STALE_SECONDS}s)"
        )
    except (ValueError, TypeError):
        return "heartbeat_stale (unparseable timestamp)"


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
        _fnMarkPipelineStopped(
            dictCtx["docker"], sContainerId)
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

    The lint reads referenced configuration files from the container.
    Skipped entirely when the workflow declares no ``dictRandomnessLint``
    block, keeping the polling cost zero for workflows that opt out.
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


def _fnRegisterFileStatus(app, dictCtx):
    """Register GET /api/pipeline/{id}/file-status endpoint."""

    @app.get("/api/pipeline/{sContainerId}/file-status")
    async def fnGetFileStatus(sContainerId: str):
        dictCtx["require"]()
        dictWorkflow = fdictRequireWorkflow(
            dictCtx["workflows"], sContainerId)
        dictVars = dictCtx["variables"](sContainerId)
        return await fdictComputeFileStatus(
            dictCtx, sContainerId, dictWorkflow, dictVars,
        )


async def _fdictFetchOutputStatus(
    dictCtx, sContainerId, dictWorkflow, dictVars,
):
    """Fetch output + script mtimes, invalidations, and staleness."""
    listOutputPaths = _flistCollectOutputPaths(
        dictWorkflow, dictVars)
    listScriptPaths = flistExtractAllScriptPaths(dictWorkflow)
    dictMarkerPathsByStep = fnCollectMarkerPathsByStep(
        dictWorkflow, dictWorkflow.get("sProjectRepoPath", ""),
    )
    listMarkerPaths = list(dictMarkerPathsByStep.values())
    listTestSourcePaths = []
    for dictStep in dictWorkflow.get("listSteps", []):
        listTestSourcePaths.extend(
            _flistResolveTestSourcePaths(dictStep, dictVars),
        )
    listUnionPaths = list(set(
        listOutputPaths + listScriptPaths + listMarkerPaths
        + listTestSourcePaths,
    ))
    dictModTimes = await asyncio.to_thread(
        _fdictGetModTimes,
        dictCtx["docker"], sContainerId, listUnionPaths,
    )
    dictPathsByStep = fdictCollectOutputPathsByStep(
        dictWorkflow, dictVars,
    )
    dictMarkerMtimeByStep = _fdictComputeMarkerMtimeByStep(
        dictMarkerPathsByStep, dictModTimes,
    )
    bStaleReset = _fbCheckStaleUserVerification(
        dictWorkflow, dictModTimes, dictVars)
    if bStaleReset:
        logger.info(
            "POLL stale-check reset sUser for container=%s",
            sContainerId,
        )
        dictCtx["save"](sContainerId, dictWorkflow)
    listInvalidated = _flistDetectAndInvalidate(
        dictCtx, sContainerId, dictWorkflow,
        dictModTimes, dictVars,
    )
    if listInvalidated:
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
    sRepoRoot = dictWorkflow.get("sProjectRepoPath", "")
    dictMaxMtimeByStep = _fdictComputeMaxMtimeByStep(
        dictPathsByStep, dictModTimes,
    )
    bAnyReconciled = (
        fbReconcileUpstreamFlags(dictWorkflow, dictMaxMtimeByStep)
        | fbReconcileUserVerificationTimestamps(dictWorkflow)
        | _fbApplyRandomnessLint(dictCtx, sContainerId, dictWorkflow)
    )
    if bAnyReconciled:
        dictCtx["save"](sContainerId, dictWorkflow)
    return {
        "dictModTimes": fdictAbsKeysToRepoRelative(
            dictModTimes, sRepoRoot,
        ),
        "dictMaxMtimeByStep": dictMaxMtimeByStep,
        "dictMaxPlotMtimeByStep": _fdictComputeMaxPlotMtimeByStep(
            dictWorkflow, dictModTimes, dictVars,
        ),
        "dictMaxDataMtimeByStep": _fdictComputeMaxDataMtimeByStep(
            dictWorkflow, dictModTimes, dictVars,
        ),
        "dictMarkerMtimeByStep": dictMarkerMtimeByStep,
        "dictTestSourceMtimeByStep":
            _fdictComputeMaxTestSourceMtimeByStep(
                dictWorkflow, dictModTimes, dictVars,
            ),
        "dictTestCategoryMtimes": _fdictComputeTestCategoryMtimes(
            dictWorkflow, dictModTimes, dictVars,
        ),
        "dictInvalidatedSteps": listInvalidated,
        "dictScriptStatus": _fdictBuildScriptStatus(
            dictWorkflow, dictModTimes, dictVars,
            dictMarkerMtimeByStep=dictMarkerMtimeByStep,
        ),
    }


async def _fdictFetchTestStatus(
    dictCtx, sContainerId, dictWorkflow,
    dictMaxOutputMtimeByStep=None,
):
    """Fetch test markers, backfill conftest, and build test status."""
    listStepDirs = _flistExtractStepDirectories(dictWorkflow)
    sProjectRepoPath = dictWorkflow.get("sProjectRepoPath", "")
    dictTestInfo = await asyncio.to_thread(
        _fdictFetchTestMarkers,
        dictCtx["docker"], sContainerId, listStepDirs,
        sProjectRepoPath,
    )
    await _fnBackfillMissingConftest(
        dictCtx["docker"], sContainerId,
        dictTestInfo.get("missingConftest", []),
        dictWorkflow.get("sProjectRepoPath", ""),
    )
    dictTestMarkers = _fdictBuildTestMarkerStatus(
        dictWorkflow, dictTestInfo,
        dictMaxOutputMtimeByStep=dictMaxOutputMtimeByStep,
    )
    bChanged = _fnApplyExternalTestResults(
        dictWorkflow, dictTestMarkers,
    )
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
    connectionDocker, sContainerId, listStepDirs, sProjectRepoPath,
):
    """Run the batched test-marker check command."""
    from .. import syncDispatcher as _syncDispatcher
    sCommand = _syncDispatcher.fsBuildTestMarkerCheckCommand(
        listStepDirs, sProjectRepoPath,
    )
    iExit, sOutput = connectionDocker.ftResultExecuteCommand(
        sContainerId, sCommand
    )
    if iExit != 0:
        return {
            "markers": {},
            "testFiles": {},
            "missingConftest": [],
        }
    return _syncDispatcher.fdictParseTestMarkerOutput(sOutput)


async def _fnBackfillMissingConftest(
    connectionDocker, sContainerId, listMissingDirs, sProjectRepoPath,
):
    """Write conftest.py into step dirs that have tests/ but no conftest."""
    from ..testGenerator import (
        fnWriteConftestMarker, fsConftestContent,
    )
    if not listMissingDirs:
        return
    logger.info(
        "Backfilling conftest.py into %d dirs: %s",
        len(listMissingDirs), listMissingDirs,
    )
    for sDir in listMissingDirs:
        try:
            await asyncio.to_thread(
                fnWriteConftestMarker,
                connectionDocker, sContainerId, sDir, sProjectRepoPath,
            )
            logger.info("Wrote conftest.py to %s", sDir)
        except Exception as exc:
            logger.error(
                "Failed to write conftest.py to %s: %s",
                sDir, exc,
            )
    await asyncio.to_thread(
        _fnDeleteLegacyMarkers,
        connectionDocker, sContainerId,
        listMissingDirs, sProjectRepoPath,
    )
    await asyncio.to_thread(
        _fnEnsureConftestTemplate,
        connectionDocker, sContainerId,
        fsConftestContent(sProjectRepoPath),
    )


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
    listMarkerPaths = [
        posixpath.join(
            sProjectRepoPath, ".vaibify", "test_markers",
            fsMarkerNameFromStepDirectory(sDir),
        )
        for sDir in listStepDirs
    ]
    sScript = (
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
    sCmd = (
        "python3 -c " + fsShellQuote(sScript)
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
    that's now meaningless.
    """
    bChanged = False
    for sCategory, sVerifyKey in _LIST_MARKER_CATEGORY_KEYS:
        if sCategory not in dictCategories:
            continue
        if dictVerify.get(sVerifyKey) == "untested":
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
    """Apply a single category result from a marker; return True if changed."""
    if sCategory not in dictCategories:
        return False
    dictCat = dictCategories[sCategory]
    sNewValue = None
    if dictCat.get("iFailed", 0) > 0:
        sNewValue = "failed"
    elif dictCat.get("iPassed", 0) > 0:
        sNewValue = "passed"
    if sNewValue is None or dictVerify.get(sVerifyKey) == sNewValue:
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


def _fdictBuildTestFileChanges(dictWorkflow, dictTestInfo):
    """Compare discovered test files against registered commands."""
    from ..testGenerator import (
        fsQuantitativeTemplateHash,
        fsIntegrityTemplateHash,
        fsQualitativeTemplateHash,
    )
    dictExpectedHashes = {
        "test_quantitative.py": fsQuantitativeTemplateHash(),
        "test_integrity.py": fsIntegrityTemplateHash(),
        "test_qualitative.py": fsQualitativeTemplateHash(),
    }
    dictTestFiles = dictTestInfo.get("testFiles", {})
    dictResult = {}
    for iIndex, dictStep in enumerate(
        dictWorkflow.get("listSteps", [])
    ):
        sDir = dictStep.get("sDirectory", "")
        if sDir not in dictTestFiles:
            continue
        dictDirInfo = dictTestFiles[sDir]
        listDiscovered = dictDirInfo.get("listFiles", [])
        setRegistered = _fsetExtractRegisteredTestFiles(
            dictStep)
        listNew = [
            f for f in listDiscovered
            if f not in setRegistered
        ]
        listMissing = [
            f for f in setRegistered
            if f not in listDiscovered
        ]
        listCustom = _flistFindCustomTestFiles(
            dictDirInfo.get("dictHashes", {}),
            dictExpectedHashes,
        )
        dictEntry = {}
        if listNew or listMissing:
            dictEntry["listNew"] = listNew
            dictEntry["listMissing"] = listMissing
        if listCustom:
            dictEntry["listCustom"] = listCustom
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
        sProjectRepo = dictWorkflow.get("sProjectRepoPath") or ""
        try:
            listMismatches = await asyncio.to_thread(
                manifestWriter.flistVerifyManifest, sProjectRepo,
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
        return _fdictBuildManifestVerifyResult(
            dictWorkflow, listMismatches,
        )


def _fdictBuildManifestVerifyResult(dictWorkflow, listMismatches):
    """Compose the manifest-verify response payload.

    ``iTotal`` reflects the number of entries actually recorded in
    ``MANIFEST.sha256`` rather than the workflow's declared outputs;
    the manifest is the authoritative source of truth for the verify
    operation. When the manifest is absent, ``iTotal`` falls back to 0.
    """
    from vaibify.reproducibility import manifestWriter
    sProjectRepo = dictWorkflow.get("sProjectRepoPath") or ""
    try:
        iTotal = manifestWriter.fiCountManifestEntries(sProjectRepo)
    except FileNotFoundError:
        iTotal = 0
    return {
        "iTotal": iTotal,
        "iMatching": iTotal - len(listMismatches),
        "listMismatches": listMismatches,
    }


def fnRegisterAll(app, dictCtx):
    """Register all pipeline control routes."""
    _fnRegisterPipelineState(app, dictCtx)
    _fnRegisterPipelineKill(app, dictCtx)
    _fnRegisterPipelineClean(app, dictCtx)
    _fnRegisterPipelineWs(app, dictCtx)
    _fnRegisterAcknowledgeStep(app, dictCtx)
    _fnRegisterFileStatus(app, dictCtx)
    _fnRegisterManifestVerify(app, dictCtx)
