"""Pipeline control route handlers."""

__all__ = ["fnRegisterAll", "fdictComputeFileStatus"]

import asyncio
import logging
import posixpath
import re

from fastapi import HTTPException, WebSocket, WebSocketDisconnect

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
    _fdictGetModTimes,
    _flistCollectOutputPaths,
    _flistDetectAndInvalidate,
    _fnClearStepModificationState,
    _fnUpdateModTimeBaseline,
    fdictCollectOutputPathsByStep,
    fnCollectMarkerPathsByStep,
    fsMarkerNameFromStepDirectory,
)
from ..fileIntegrity import flistExtractAllScriptPaths

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
        return dictState


def _fnRegisterPipelineKill(app, dictCtx):
    """Register POST /api/pipeline/{id}/kill endpoint."""

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
        await fnHandlePipelineWs(
            websocket, dictCtx, sContainerId)


def _fnRegisterAcknowledgeStep(app, dictCtx):
    """Register POST endpoint to acknowledge step completion."""

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
    )
    dictOutputStatus.update(dictTestStatus)
    return dictOutputStatus


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
    dictMarkerPathsByStep = fnCollectMarkerPathsByStep(dictWorkflow)
    listMarkerPaths = list(dictMarkerPathsByStep.values())
    listUnionPaths = list(set(
        listOutputPaths + listScriptPaths + listMarkerPaths,
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
    return {
        "dictModTimes": dictModTimes,
        "dictMaxMtimeByStep": _fdictComputeMaxMtimeByStep(
            dictPathsByStep, dictModTimes,
        ),
        "dictMaxPlotMtimeByStep": _fdictComputeMaxPlotMtimeByStep(
            dictWorkflow, dictModTimes, dictVars,
        ),
        "dictMaxDataMtimeByStep": _fdictComputeMaxDataMtimeByStep(
            dictWorkflow, dictModTimes, dictVars,
        ),
        "dictMarkerMtimeByStep": dictMarkerMtimeByStep,
        "dictInvalidatedSteps": listInvalidated,
        "dictScriptStatus": _fdictBuildScriptStatus(
            dictWorkflow, dictModTimes, dictVars,
            dictMarkerMtimeByStep=dictMarkerMtimeByStep,
        ),
    }


async def _fdictFetchTestStatus(
    dictCtx, sContainerId, dictWorkflow,
):
    """Fetch test markers, backfill conftest, and build test status."""
    listStepDirs = _flistExtractStepDirectories(dictWorkflow)
    dictTestInfo = await asyncio.to_thread(
        _fdictFetchTestMarkers,
        dictCtx["docker"], sContainerId, listStepDirs,
    )
    await _fnBackfillMissingConftest(
        dictCtx["docker"], sContainerId,
        dictTestInfo.get("missingConftest", []),
    )
    dictTestMarkers = _fdictBuildTestMarkerStatus(
        dictWorkflow, dictTestInfo,
    )
    _fnApplyExternalTestResults(dictWorkflow, dictTestMarkers)
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
):
    """Run the batched test-marker check command."""
    from .. import syncDispatcher as _syncDispatcher
    sCommand = _syncDispatcher.fsBuildTestMarkerCheckCommand(
        listStepDirs,
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
    connectionDocker, sContainerId, listMissingDirs,
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
                connectionDocker, sContainerId, sDir,
            )
            logger.info("Wrote conftest.py to %s", sDir)
        except Exception as exc:
            logger.error(
                "Failed to write conftest.py to %s: %s",
                sDir, exc,
            )
    await asyncio.to_thread(
        _fnEnsureConftestTemplate,
        connectionDocker, sContainerId,
        fsConftestContent(),
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


def _fdictBuildTestMarkerStatus(dictWorkflow, dictTestInfo):
    """Map test markers to step indices and check staleness."""
    dictMarkers = dictTestInfo.get("markers", {})
    dictTestFiles = dictTestInfo.get("testFiles", {})
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
        bStale = _fbMarkerStale(
            dictMarker, dictTestFiles.get(sDir, {}),
        )
        dictResult[str(iIndex)] = {
            "dictMarker": dictMarker, "bStale": bStale,
        }
    return dictResult


def _fbMarkerStale(dictMarker, dictTestFileInfo):
    """Return True if any test file is newer than the marker."""
    fMarkerTime = dictMarker.get("fTimestamp", 0)
    dictMtimes = dictTestFileInfo.get("dictMtimes", {})
    for fMtime in dictMtimes.values():
        if fMtime > fMarkerTime:
            return True
    return False


_LIST_MARKER_CATEGORY_KEYS = [
    ("integrity", "sIntegrity"),
    ("qualitative", "sQualitative"),
    ("quantitative", "sQuantitative"),
]


def _fnApplyAllMarkerCategories(dictVerify, dictCategories):
    """Apply all marker categories to a verification dict."""
    for sCategory, sVerifyKey in _LIST_MARKER_CATEGORY_KEYS:
        _fnApplyMarkerCategory(
            dictVerify, dictCategories, sCategory, sVerifyKey,
        )


def _fnApplyExternalTestResults(dictWorkflow, dictTestMarkers):
    """Update workflow dictVerification from external test markers."""
    listSteps = dictWorkflow.get("listSteps", [])
    for sIndex, dictEntry in dictTestMarkers.items():
        iIndex = int(sIndex)
        if dictEntry["bStale"] or iIndex >= len(listSteps):
            continue
        dictVerify = listSteps[iIndex].setdefault(
            "dictVerification", {},
        )
        dictCategories = dictEntry["dictMarker"].get(
            "dictCategories", {},
        )
        _fnApplyAllMarkerCategories(
            dictVerify, dictCategories)


def _fnApplyMarkerCategory(
    dictVerify, dictCategories, sCategory, sVerifyKey,
):
    """Apply a single category result from a marker."""
    if sCategory not in dictCategories:
        return
    dictCat = dictCategories[sCategory]
    if dictCat.get("iFailed", 0) > 0:
        dictVerify[sVerifyKey] = "failed"
    elif dictCat.get("iPassed", 0) > 0:
        dictVerify[sVerifyKey] = "passed"


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


def fnRegisterAll(app, dictCtx):
    """Register all pipeline control routes."""
    _fnRegisterPipelineState(app, dictCtx)
    _fnRegisterPipelineKill(app, dictCtx)
    _fnRegisterPipelineClean(app, dictCtx)
    _fnRegisterPipelineWs(app, dictCtx)
    _fnRegisterAcknowledgeStep(app, dictCtx)
    _fnRegisterFileStatus(app, dictCtx)
