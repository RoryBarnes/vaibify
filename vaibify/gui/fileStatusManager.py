"""File-status polling, modification tracking, and step invalidation.

Verification State Machine
--------------------------
Each step carries a ``dictVerification`` with orthogonal state fields:

**Execution verification** (set by test runner / pipeline):
- ``sUnitTest``:      untested | passed | failed
- ``sIntegrity``:     untested | passed | failed
- ``sQualitative``:   untested | passed | failed
- ``sQuantitative``:  untested | passed | failed

**User verification** (set by researcher clicking UI badge):
- ``sUser``:          untested | passed | failed

**Plot standards** (set by standardize-plots endpoint):
- ``sPlotStandards``: passed | stale

**Invalidation metadata** (set by polling, cleared on acknowledge):
- ``listModifiedFiles``:  list of changed output paths
- ``bUpstreamModified``:  True when an upstream step's outputs changed

**Timestamps** (set by UI / polling):
- ``sLastUserUpdate``:  UTC timestamp when user last set sUser
- ``sLastDepsCheck``:   UTC timestamp when dependencies last passed
- ``sLastTestRun``:     UTC timestamp of last test execution

State Transitions
~~~~~~~~~~~~~~~~~
- Step executes      -> sUser resets to "untested"
- Data file changes  -> sUnitTest resets to "untested"
- Plot file changes  -> sUser resets to "untested" (if newer than sLastUserUpdate)
- Upstream changes   -> bUpstreamModified = True, sUnitTest -> "untested"
- Tests pass/fail    -> sUnitTest, sIntegrity, sQualitative, sQuantitative updated
- User clicks verify -> sUser cycles: untested -> passed -> failed -> untested
"""

import logging
import posixpath

__all__ = [
    "fdictCollectOutputPathsByStep",
]

from . import pipelineState
from . import workflowManager
from .commandUtilities import flistExtractScripts
from .fileIntegrity import _fsNormalizePath
from .pipelineUtils import fsShellQuote

logger = logging.getLogger("vaibify")


def _fnRecordStepRunTimestamp(dictWorkflow, iStepIndex):
    """Set sLastRun on the step's dictRunStats."""
    from datetime import datetime, timezone
    listSteps = dictWorkflow.get("listSteps", [])
    if iStepIndex < 0 or iStepIndex >= len(listSteps):
        return
    dictStep = listSteps[iStepIndex]
    if "dictRunStats" not in dictStep:
        dictStep["dictRunStats"] = {}
    dictStep["dictRunStats"]["sLastRun"] = (
        datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    )


def _fnClearStepModificationState(dictWorkflow, iStepIndex):
    """Clear modification flags from a step's verification."""
    listSteps = dictWorkflow.get("listSteps", [])
    if iStepIndex < 0 or iStepIndex >= len(listSteps):
        return
    dictVerify = listSteps[iStepIndex].get("dictVerification", {})
    dictVerify.pop("listModifiedFiles", None)
    dictVerify.pop("bOutputModified", None)


def _fnUpdateModTimeBaseline(dictCtx, sContainerId, dictModTimes):
    """Update stored mtimes so the next poll doesn't re-flag files."""
    if "dictPreviousModTimes" not in dictCtx:
        dictCtx["dictPreviousModTimes"] = {}
    dictCtx["dictPreviousModTimes"][sContainerId] = dict(dictModTimes)


def _fdictBuildFileStatusVars(dictWorkflow):
    """Build variable dict for file path resolution."""
    return {
        "sPlotDirectory": dictWorkflow.get("sPlotDirectory", "Plot"),
        "sFigureType": dictWorkflow.get("sFigureType", "pdf"),
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
    sStepDir = dictStep.get("sDirectory", "")
    listPaths = []
    for sFile in (dictStep.get("saDataFiles", [])
                  + dictStep.get("saPlotFiles", [])):
        sResolved = workflowManager.fsResolveVariables(
            sFile, dictGlobalVars)
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


def _flistResolvePlotPaths(dictStep, dictVars):
    """Return list of (resolved_path, basename) for step plot files."""
    sStepDir = dictStep.get("sDirectory", "")
    listResult = []
    for sFile in dictStep.get("saPlotFiles", []):
        sResolved = workflowManager.fsResolveVariables(sFile, dictVars)
        if not sResolved.startswith("/"):
            sResolved = posixpath.join(sStepDir, sResolved)
        sBasename = posixpath.basename(sResolved)
        listResult.append((sResolved, sBasename))
    return listResult


def _fbPipelineIsRunning(dictCtx, sContainerId):
    """Return True if a pipeline is currently running in container."""
    dictState = pipelineState.fdictReadState(
        dictCtx["docker"], sContainerId)
    if dictState is None:
        return False
    return dictState.get("bRunning", False)


def _fdictComputeMaxMtimeByStep(dictPathsByStep, dictModTimes):
    """Return {stepIndex: maxMtimeString} for steps with output files."""
    dictResult = {}
    for iIndex, listPaths in dictPathsByStep.items():
        listMtimes = [
            int(dictModTimes[sPath])
            for sPath in listPaths if sPath in dictModTimes
        ]
        if listMtimes:
            dictResult[str(iIndex)] = str(max(listMtimes))
    return dictResult


def _fdictComputeMaxPlotMtimeByStep(dictWorkflow, dictModTimes,
                                     dictVars=None):
    """Return {stepIndex: maxPlotMtimeString} using only plot files."""
    if dictVars is None:
        dictVars = {
            "sPlotDirectory": dictWorkflow.get(
                "sPlotDirectory", "Plot"),
            "sFigureType": dictWorkflow.get("sFigureType", "pdf"),
        }
    dictResult = {}
    for iIndex, dictStep in enumerate(
        dictWorkflow.get("listSteps", [])
    ):
        listPlotTuples = _flistResolvePlotPaths(dictStep, dictVars)
        listMtimes = [
            int(dictModTimes[sPath])
            for sPath, _ in listPlotTuples if sPath in dictModTimes
        ]
        if listMtimes:
            dictResult[str(iIndex)] = str(max(listMtimes))
    return dictResult


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


def _fbAnyDataFileChanged(listChangedPaths, listDataFiles):
    """Return True if any changed path matches a data file."""
    setDataBasenames = {
        posixpath.basename(sFile) for sFile in listDataFiles
    }
    for sChangedPath in listChangedPaths:
        if posixpath.basename(sChangedPath) in setDataBasenames:
            return True
    return False


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


def _fbPlotNewerThanUserVerification(dictStep, listChangedPaths,
                                     dictModTimes):
    """Return True if a changed plot file is newer than sLastUserUpdate."""
    dictVerification = dictStep.get("dictVerification", {})
    listPlotFiles = dictStep.get("saPlotFiles", [])
    if not _fbAnyPlotFileChanged(listChangedPaths, listPlotFiles):
        return False
    sLastUserUpdate = dictVerification.get("sLastUserUpdate", "")
    if not sLastUserUpdate:
        return True
    iUserEpoch = _fiParseUtcTimestamp(sLastUserUpdate)
    if iUserEpoch is None:
        return True
    return _fbAnyMtimeNewerThan(listChangedPaths, dictModTimes,
                                iUserEpoch)


def _fiParseUtcTimestamp(sTimestamp):
    """Parse 'YYYY-MM-DD HH:MM[:SS] UTC' to Unix epoch seconds."""
    from datetime import datetime, timezone
    try:
        sClean = sTimestamp.replace(" UTC", "").strip()
        for sFmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
            try:
                dtParsed = datetime.strptime(sClean, sFmt)
                dtUtc = dtParsed.replace(tzinfo=timezone.utc)
                return int(dtUtc.timestamp())
            except ValueError:
                continue
        return None
    except AttributeError:
        return None


def _fbAnyMtimeNewerThan(listPaths, dictModTimes, iThreshold):
    """Return True if any path in dictModTimes has mtime > iThreshold."""
    for sPath in listPaths:
        sMtime = dictModTimes.get(sPath)
        if sMtime and int(sMtime) > iThreshold:
            return True
    return False


def _fbCheckStaleUserVerification(dictWorkflow, dictModTimes,
                                   dictVars=None):
    """Reset sUser if plot files are newer than sLastUserUpdate.

    Returns True if any step was modified, so the caller can save.
    This handles the case where outputs changed before the server
    started, so poll-based delta detection never fires.
    """
    import logging
    logger = logging.getLogger("vaibify")
    bChanged = False
    if dictVars is None:
        dictVars = {
            "sPlotDirectory": dictWorkflow.get(
                "sPlotDirectory", "Plot"),
            "sFigureType": dictWorkflow.get("sFigureType", "pdf"),
        }
    for iIndex, dictStep in enumerate(
        dictWorkflow.get("listSteps", [])
    ):
        dictVerification = dictStep.get("dictVerification", {})
        if dictVerification.get("sUser") != "passed":
            continue
        sLastUserUpdate = dictVerification.get(
            "sLastUserUpdate", "")
        if not sLastUserUpdate:
            continue
        iUserEpoch = _fiParseUtcTimestamp(sLastUserUpdate)
        if iUserEpoch is None:
            continue
        listPlotTuples = _flistResolvePlotPaths(dictStep, dictVars)
        listPlotPaths = [tEntry[0] for tEntry in listPlotTuples]
        bStale = _fbAnyMtimeNewerThan(
            listPlotPaths, dictModTimes, iUserEpoch)
        logger.info(
            "Freshness check step %d: sLastUserUpdate=%s "
            "iUserEpoch=%s paths=%s bStale=%s",
            iIndex, sLastUserUpdate, iUserEpoch,
            listPlotPaths, bStale,
        )
        if bStale:
            dictVerification["sUser"] = "untested"
            dictStep["dictVerification"] = dictVerification
            bChanged = True
        else:
            if dictVerification.get("listModifiedFiles"):
                dictVerification.pop("listModifiedFiles", None)
                dictVerification.pop("bOutputModified", None)
                dictStep["dictVerification"] = dictVerification
                bChanged = True
    return bChanged


def _fnInvalidateStepFiles(dictStep, listChangedPaths,
                           dictModTimes=None):
    """Mark specific files as modified, invalidate verifications."""
    if dictModTimes is None:
        dictModTimes = {}
    dictVerification = dictStep.get("dictVerification", {})
    listDataFiles = dictStep.get("saDataFiles", [])
    if dictVerification.get("sUnitTest") == "passed":
        if _fbAnyDataFileChanged(listChangedPaths, listDataFiles):
            dictVerification["sUnitTest"] = "untested"
    if dictVerification.get("sUser") == "passed":
        bPlotNewer = _fbPlotNewerThanUserVerification(
            dictStep, listChangedPaths, dictModTimes
        )
        logger.info(
            "_fnInvalidateStepFiles sUser=%s changed=%s "
            "bPlotNewer=%s sLastUserUpdate=%s",
            dictVerification.get("sUser"), listChangedPaths,
            bPlotNewer,
            dictVerification.get("sLastUserUpdate"),
        )
        if bPlotNewer:
            dictVerification["sUser"] = "untested"
    if dictVerification.get("sPlotStandards") == "passed":
        listPlotFiles = dictStep.get("saPlotFiles", [])
        if _fbAnyPlotFileChanged(listChangedPaths, listPlotFiles):
            dictVerification["sPlotStandards"] = "stale"
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


def _fbStepScriptsModified(dictStep, dictCurrentHashes):
    """Return True if any script hash differs from stored hashes."""
    dictStoredHashes = dictStep.get(
        "dictRunStats", {}
    ).get("dictInputHashes", {})
    if not dictStoredHashes:
        return None
    sDirectory = dictStep.get("sDirectory", "")
    for sKey in ("saDataCommands", "saPlotCommands",
                 "saSetupCommands", "saCommands"):
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


def _fdictInvalidateAffectedSteps(dictWorkflow, dictChangedFiles,
                                  dictModTimes=None):
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
            _fnInvalidateStepFiles(listSteps[iIndex], listPaths,
                                   dictModTimes)
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
        dictWorkflow, dictChangedFiles, dictNewModTimes)
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
