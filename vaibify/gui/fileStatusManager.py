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

State Transitions
~~~~~~~~~~~~~~~~~
- Step executes      -> sUser resets to "untested"
- Data file changes  -> sUnitTest, sIntegrity, sQualitative, sQuantitative reset to "untested"
- Plot file changes  -> sUser resets to "untested" (if newer than sLastUserUpdate)
- Upstream changes   -> same resets as data file changes, plus bUpstreamModified = True
- Tests pass/fail    -> sUnitTest, sIntegrity, sQualitative, sQuantitative updated
- User clicks verify -> sUser cycles: untested -> passed -> failed -> untested
"""

import logging
import posixpath

_LIST_CATEGORY_KEYS = (
    ("dictIntegrity", "sIntegrity"),
    ("dictQualitative", "sQualitative"),
    ("dictQuantitative", "sQuantitative"),
)

__all__ = [
    "fdictCollectOutputPathsByStep",
    "fnCollectScriptPathsByStep",
    "fnCollectMarkerPathsByStep",
    "fsMarkerNameFromStepDirectory",
    "fbReconcileUpstreamFlags",
    "fbReconcileUserVerificationTimestamps",
    "fbIsStepFullyVerified",
    "flistStepRemoteFiles",
    "fnMaybeAutoArchive",
]

def fsMarkerNameFromStepDirectory(sStepDirectory):
    """Return the marker filename for a step directory."""
    return sStepDirectory.strip("/").replace("/", "_") + ".json"


def fnCollectMarkerPathsByStep(dictWorkflow, sProjectRepoPath):
    """Return {iStepIndex: sMarkerPath} for each step with a directory.

    ``sProjectRepoPath`` is the container-absolute path of the active
    workflow's project repo (auto-detected at connect time and stored
    on ``dictWorkflow['sProjectRepoPath']``). Marker files live under
    ``<sProjectRepoPath>/.vaibify/test_markers/`` so that they are
    committed alongside the rest of the project and survive clone.
    An empty ``sProjectRepoPath`` yields an empty map — the caller is
    expected to surface the no-repo state explicitly rather than
    silently falling back to a workspace-rooted default.
    """
    dictResult = {}
    if not sProjectRepoPath:
        return dictResult
    sMarkerDir = posixpath.join(
        sProjectRepoPath, ".vaibify", "test_markers",
    )
    for iIndex, dictStep in enumerate(
        dictWorkflow.get("listSteps", [])
    ):
        sStepDirectory = dictStep.get("sDirectory", "")
        if not sStepDirectory:
            continue
        sMarkerName = fsMarkerNameFromStepDirectory(sStepDirectory)
        dictResult[iIndex] = posixpath.join(sMarkerDir, sMarkerName)
    return dictResult

from . import pipelineState
from . import workflowManager
from .commandUtilities import flistExtractScripts
from .fileIntegrity import _fsNormalizePath
from .pathContract import flistNormalizeModifiedFiles
from .pipelineUtils import fsShellQuote


_T_DATA_SCRIPT_KEYS = ("saDataCommands", "saSetupCommands", "saCommands")
_T_PLOT_SCRIPT_KEYS = ("saPlotCommands",)


def fnCollectScriptPathsByStep(dictWorkflow):
    """Return {iStepIndex: {"data": [paths...], "plot": [paths...]}}."""
    dictResult = {}
    for iIndex, dictStep in enumerate(
        dictWorkflow.get("listSteps", [])
    ):
        sDir = dictStep.get("sDirectory", "")
        dictResult[iIndex] = {
            "data": _flistScriptPaths(dictStep, sDir, _T_DATA_SCRIPT_KEYS),
            "plot": _flistScriptPaths(dictStep, sDir, _T_PLOT_SCRIPT_KEYS),
        }
    return dictResult


def _flistScriptPaths(dictStep, sDirectory, tKeys):
    """Return normalized script paths for a subset of command categories."""
    listPaths = []
    setAdded = set()
    for sKey in tKeys:
        for sScript in flistExtractScripts(dictStep.get(sKey, [])):
            sPath = _fsNormalizePath(sDirectory, sScript)
            if sPath not in setAdded:
                listPaths.append(sPath)
                setAdded.add(sPath)
    return listPaths

logger = logging.getLogger("vaibify")


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
        "sRepoRoot": dictWorkflow.get("sProjectRepoPath", ""),
    }


def _fsResolveStepFilePath(sFile, sStepDir, dictVars):
    """Resolve a step-output file to an absolute container path."""
    sResolved = workflowManager.fsResolveVariables(sFile, dictVars)
    if posixpath.isabs(sResolved):
        return sResolved
    if sStepDir:
        sResolved = posixpath.join(sStepDir, sResolved)
    if posixpath.isabs(sResolved):
        return sResolved
    sRepoRoot = (dictVars or {}).get("sRepoRoot", "")
    if sRepoRoot:
        sResolved = posixpath.join(sRepoRoot, sResolved)
    return sResolved


def fdictCollectOutputPathsByStep(dictWorkflow, dictVars=None):
    """Return {iStepIndex: [resolved_paths]} for each step."""
    dictResult = {}
    if dictVars is None:
        dictVars = _fdictBuildFileStatusVars(dictWorkflow)
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
        listPaths.append(_fsResolveStepFilePath(
            sFile, sStepDir, dictGlobalVars,
        ))
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
        sResolved = _fsResolveStepFilePath(sFile, sStepDir, dictVars)
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
        dictVars = _fdictBuildFileStatusVars(dictWorkflow)
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


def _fdictComputeMaxDataMtimeByStep(dictWorkflow, dictModTimes,
                                     dictVars=None):
    """Return {stepIndex: maxDataMtimeString} using only data files."""
    if dictVars is None:
        dictVars = _fdictBuildFileStatusVars(dictWorkflow)
    dictResult = {}
    for iIndex, dictStep in enumerate(
        dictWorkflow.get("listSteps", [])
    ):
        listDataPaths = _flistResolveDataPaths(dictStep, dictVars)
        listMtimes = [
            int(dictModTimes[sPath])
            for sPath in listDataPaths if sPath in dictModTimes
        ]
        if listMtimes:
            dictResult[str(iIndex)] = str(max(listMtimes))
    return dictResult


def _flistResolveTestSourcePaths(dictStep, dictVars):
    """Return container-absolute paths to a step's test source files.

    The test SOURCE mtime represents when the unit-test contract was
    last written. Compared against a downstream step's output mtimes,
    it answers: was the current contract in force when the downstream
    consumed me? Falls back to the empty list when the step defines
    no tests (interactive / plot-only).
    """
    from .testGenerator import (
        fsIntegrityTestPath, fsQualitativeTestPath,
        fsQuantitativeTestPath,
    )
    sStepDir = dictStep.get("sDirectory", "")
    if not sStepDir:
        return []
    sRepoRoot = (dictVars or {}).get("sRepoRoot", "")
    listSources = [
        fsIntegrityTestPath(sStepDir),
        fsQualitativeTestPath(sStepDir),
        fsQuantitativeTestPath(sStepDir),
    ]
    for dictUserTest in dictStep.get(
        "dictTests", {}).get("listUserTests", []):
        sFilePath = dictUserTest.get("sFilePath", "")
        if sFilePath:
            listSources.append(sFilePath)
    return [
        _fsAbsolutizeTestPath(sPath, sRepoRoot)
        for sPath in listSources
    ]


def _fsAbsolutizeTestPath(sPath, sRepoRoot):
    """Resolve a repo-relative or absolute test path into container abs."""
    if posixpath.isabs(sPath):
        return sPath
    if sRepoRoot:
        return posixpath.join(sRepoRoot, sPath)
    return sPath


def _fdictComputeMaxTestSourceMtimeByStep(
    dictWorkflow, dictModTimes, dictVars=None,
):
    """Return {stepIndex: maxTestSourceMtimeString} per step.

    Steps with no test source files on disk (i.e. none of the
    candidate paths are present in ``dictModTimes``) are omitted from
    the result, signalling "no contract" to the caller.
    """
    if dictVars is None:
        dictVars = _fdictBuildFileStatusVars(dictWorkflow)
    dictResult = {}
    for iIndex, dictStep in enumerate(
        dictWorkflow.get("listSteps", []),
    ):
        listSources = _flistResolveTestSourcePaths(dictStep, dictVars)
        listMtimes = [
            int(dictModTimes[sPath])
            for sPath in listSources if sPath in dictModTimes
        ]
        if listMtimes:
            dictResult[str(iIndex)] = str(max(listMtimes))
    return dictResult


_T_TEST_CATEGORY_KEYS = ("integrity", "qualitative", "quantitative")


def _fdictResolveCategoryTestPaths(dictStep, dictVars):
    """Return {category: container_abs_path} for the canonical 3.

    Excludes user-provided tests; those don't fit the per-category
    UI display, which is keyed on the three canonical categories
    surfaced as Run buttons in the step renderer.
    """
    from .testGenerator import (
        fsIntegrityTestPath, fsQualitativeTestPath,
        fsQuantitativeTestPath,
    )
    sStepDir = dictStep.get("sDirectory", "")
    if not sStepDir:
        return {}
    sRepoRoot = (dictVars or {}).get("sRepoRoot", "")
    return {
        "integrity": _fsAbsolutizeTestPath(
            fsIntegrityTestPath(sStepDir), sRepoRoot,
        ),
        "qualitative": _fsAbsolutizeTestPath(
            fsQualitativeTestPath(sStepDir), sRepoRoot,
        ),
        "quantitative": _fsAbsolutizeTestPath(
            fsQuantitativeTestPath(sStepDir), sRepoRoot,
        ),
    }


def _fdictComputeTestCategoryMtimes(
    dictWorkflow, dictModTimes, dictVars=None,
):
    """Return {stepIndex: {category: mtimeString}} per step.

    Only includes categories whose source file is present in
    ``dictModTimes`` (i.e. exists on disk). Steps with no canonical
    test files are omitted entirely. Surfaces per-category contract
    age to the dashboard so a single stale category can be diagnosed
    without inspecting the container by hand.
    """
    if dictVars is None:
        dictVars = _fdictBuildFileStatusVars(dictWorkflow)
    dictResult = {}
    for iIndex, dictStep in enumerate(
        dictWorkflow.get("listSteps", []),
    ):
        dictCategoryPaths = _fdictResolveCategoryTestPaths(
            dictStep, dictVars,
        )
        dictPresent = {
            sCategory: str(int(dictModTimes[sPath]))
            for sCategory, sPath in dictCategoryPaths.items()
            if sPath in dictModTimes
        }
        if dictPresent:
            dictResult[str(iIndex)] = dictPresent
    return dictResult


def _flistResolveDataPaths(dictStep, dictVars):
    """Return resolved data file paths for a single step."""
    sStepDir = dictStep.get("sDirectory", "")
    listPaths = []
    for sFile in dictStep.get("saDataFiles", []):
        listPaths.append(_fsResolveStepFilePath(
            sFile, sStepDir, dictVars,
        ))
    return listPaths


def _fdictComputeMarkerMtimeByStep(dictMarkerPathsByStep, dictModTimes):
    """Return {stepIndex: markerMtimeString} for steps whose marker exists."""
    dictResult = {}
    for iIndex, sMarkerPath in dictMarkerPathsByStep.items():
        sMtime = dictModTimes.get(sMarkerPath)
        if sMtime:
            dictResult[str(iIndex)] = str(sMtime)
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
            dictVerification.pop("sLastUserUpdate", None)
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
                           dictModTimes=None, sRepoRoot=""):
    """Mark specific files as modified, invalidate verifications.

    ``listChangedPaths`` contains absolute container paths (the same
    form the backend uses internally for stat/exec). They are
    normalized to repo-relative form before persistence so the stored
    ``listModifiedFiles`` always matches the wire-format contract in
    ``pathContract``.
    """
    if dictModTimes is None:
        dictModTimes = {}
    dictVerification = dictStep.get("dictVerification", {})
    listDataFiles = dictStep.get("saDataFiles", [])
    if dictVerification.get("sUnitTest") == "passed":
        if _fbAnyDataFileChanged(listChangedPaths, listDataFiles):
            dictVerification["sUnitTest"] = "untested"
            for _sCatKey, sVerifKey in _LIST_CATEGORY_KEYS:
                if sVerifKey in dictVerification:
                    dictVerification[sVerifKey] = "untested"
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
            dictVerification.pop("sLastUserUpdate", None)
    if dictVerification.get("sPlotStandards") == "passed":
        listPlotFiles = dictStep.get("saPlotFiles", [])
        if _fbAnyPlotFileChanged(listChangedPaths, listPlotFiles):
            dictVerification["sPlotStandards"] = "stale"
    listExisting = dictVerification.get("listModifiedFiles", [])
    listExistingRel = flistNormalizeModifiedFiles(
        listExisting, sRepoRoot,
    )
    listChangedRel = flistNormalizeModifiedFiles(
        listChangedPaths, sRepoRoot,
    )
    setModified = set(listExistingRel) | set(listChangedRel)
    dictVerification["listModifiedFiles"] = sorted(setModified)
    dictStep["dictVerification"] = dictVerification


def _fnInvalidateDownstreamStep(dictStep):
    """Mark a downstream step as affected by upstream changes."""
    dictVerification = dictStep.get("dictVerification", {})
    if dictVerification.get("sUnitTest") == "passed":
        dictVerification["sUnitTest"] = "untested"
        for _sCatKey, sVerifKey in _LIST_CATEGORY_KEYS:
            if sVerifKey in dictVerification:
                dictVerification[sVerifKey] = "untested"
    dictVerification["bUpstreamModified"] = True
    dictStep["dictVerification"] = dictVerification


def fbReconcileUserVerificationTimestamps(dictWorkflow):
    """Strip ``sLastUserUpdate`` from steps where ``sUser`` is not 'passed'.

    ``sLastUserUpdate`` is only meaningful while the researcher's
    attestation stands. When ``sUser`` flips to ``untested`` or
    ``failed`` the timestamp becomes ghost data that misleads the UI
    ("Last updated 2026-04-07" on a step currently marked untested)
    and re-introduces stale-artifact warnings via any comparison
    that still reads the field. Enforces the "no stale timestamps"
    invariant as a pure derived state, independent of transition
    sites. Returns True when any step changed so the caller persists.
    """
    bAnyChanged = False
    for dictStep in dictWorkflow.get("listSteps", []):
        dictVerify = dictStep.get("dictVerification", {})
        if dictVerify.get("sUser") == "passed":
            continue
        if "sLastUserUpdate" in dictVerify:
            dictVerify.pop("sLastUserUpdate", None)
            dictStep["dictVerification"] = dictVerify
            bAnyChanged = True
    return bAnyChanged


def fbReconcileUpstreamFlags(dictWorkflow, dictMaxMtimeByStep):
    """Sync bUpstreamModified with the current mtime state on every step.

    Makes ``bUpstreamModified`` a pure derived field rather than an
    edge-triggered flag that lags reality. Sets it on steps whose
    output is older than any upstream's output; clears it on steps
    where current mtimes say nothing is stale. Steps with no output
    mtime (never run) are left alone — no comparison is possible.
    Returns True when any flag changed so the caller can persist.
    """
    dictUpstream = _fdictBuildUpstreamMap(dictWorkflow)
    listSteps = dictWorkflow.get("listSteps", [])
    bAnyChanged = False
    for iStep, dictStep in enumerate(listSteps):
        dictVerify = dictStep.setdefault("dictVerification", {})
        iSignal = _fiMtimeStalenessSignal(
            iStep, dictUpstream, dictMaxMtimeByStep,
        )
        if iSignal < 0:
            continue
        bHasFlag = dictVerify.get("bUpstreamModified") is True
        if iSignal == 1 and not bHasFlag:
            dictVerify["bUpstreamModified"] = True
            bAnyChanged = True
        elif iSignal == 0 and bHasFlag:
            dictVerify.pop("bUpstreamModified", None)
            bAnyChanged = True
    return bAnyChanged


def _fdictBuildUpstreamMap(dictWorkflow):
    """Invert fdictBuildDirectDependencies to {iStep: set(iUpstream)}."""
    dictDirect = workflowManager.fdictBuildDirectDependencies(
        dictWorkflow,
    )
    dictUpstream = {}
    for iUp, setDown in dictDirect.items():
        for iDown in setDown:
            dictUpstream.setdefault(iDown, set()).add(iUp)
    return dictUpstream


def _fiMtimeStalenessSignal(
    iStep, dictUpstream, dictMaxMtimeByStep,
):
    """Return 1 (stale), 0 (fresh), or -1 (unknown — step not run)."""
    iMyMtime = int(
        dictMaxMtimeByStep.get(str(iStep), "0") or 0,
    )
    if not iMyMtime:
        return -1
    for iUp in dictUpstream.get(iStep, set()):
        iUpMtime = int(
            dictMaxMtimeByStep.get(str(iUp), "0") or 0,
        )
        if iUpMtime and iUpMtime > iMyMtime:
            return 1
    return 0


def _flistNewerPaths(listPaths, dictModTimes, iThreshold):
    """Return paths whose mtime (in dictModTimes) is strictly > iThreshold."""
    listNewer = []
    for sPath in listPaths:
        sMtime = dictModTimes.get(sPath)
        if sMtime is None:
            continue
        try:
            iMtime = int(sMtime)
        except (TypeError, ValueError):
            continue
        if iMtime > iThreshold:
            listNewer.append(sPath)
    return listNewer


def _fiValidatorEpoch(dictVerification, sKey):
    """Return epoch of a validator timestamp, or None if unset."""
    sValue = dictVerification.get(sKey, "")
    if not sValue:
        return None
    return _fiParseUtcTimestamp(sValue)


def _fnAppendStaleArtifacts(
    listTarget, listPaths, sValidator, sCategory,
):
    """Append {sValidator, sCategory, sPath} entries to listTarget."""
    for sPath in listPaths:
        listTarget.append({
            "sValidator": sValidator,
            "sCategory": sCategory,
            "sPath": sPath,
        })


def _fnAppendTestStale(listStale, listBuckets, iEpoch, dictModTimes):
    """Append test-validator stale artifacts for data scripts and files."""
    _fnAppendStaleArtifacts(listStale, _flistNewerPaths(
        listBuckets["dataScript"], dictModTimes, iEpoch,
    ), "test", "dataScript")
    _fnAppendStaleArtifacts(listStale, _flistNewerPaths(
        listBuckets["dataFile"], dictModTimes, iEpoch,
    ), "test", "dataFile")


def _fnAppendUserStale(listStale, listBuckets, iEpoch, dictModTimes):
    """Append user-validator stale artifacts for all four categories."""
    for sCategory in ("dataScript", "dataFile",
                      "plotScript", "plotFile"):
        _fnAppendStaleArtifacts(listStale, _flistNewerPaths(
            listBuckets[sCategory], dictModTimes, iEpoch,
        ), "user", sCategory)


def _fbStepIsPencilStale(
    dictStep, dictStepScripts, listStepOutputPaths, dictModTimes,
    iMarkerMtime=None, setResolvedPlotPaths=None,
):
    """Return (bStale, listStaleArtifacts) via timestamp comparisons.

    The ``sLastUserUpdate`` comparison only fires when ``sUser`` is
    currently ``passed`` — if the researcher has not attested (or has
    been flipped back to ``untested``), there is nothing for the
    artifact freshness to be "stale relative to."
    """
    dictVerify = dictStep.get("dictVerification", {})
    iLastUser = _fiValidatorEpoch(dictVerify, "sLastUserUpdate")
    bUserPassed = dictVerify.get("sUser") == "passed"
    listBuckets = _fdictBuildArtifactBuckets(
        dictStep, dictStepScripts, listStepOutputPaths,
        setResolvedPlotPaths,
    )
    listStale = []
    if iMarkerMtime is not None:
        _fnAppendTestStale(
            listStale, listBuckets, iMarkerMtime, dictModTimes)
    if iLastUser is not None and bUserPassed:
        _fnAppendUserStale(
            listStale, listBuckets, iLastUser, dictModTimes)
    return (len(listStale) > 0, listStale)


def _fdictBuildArtifactBuckets(
    dictStep, dictStepScripts, listStepOutputPaths,
    setResolvedPlotPaths,
):
    """Return {category: [paths]} for each of the four artifact buckets."""
    listDataFiles, listPlotFiles = _flistSplitOutputPaths(
        dictStep, listStepOutputPaths, setResolvedPlotPaths,
    )
    return {
        "dataScript": dictStepScripts.get("data", []),
        "plotScript": dictStepScripts.get("plot", []),
        "dataFile": listDataFiles,
        "plotFile": listPlotFiles,
    }


def _fbPathIsPlot(sPath, setResolvedPlotPaths, bByBasename):
    """Return True if sPath should be classified as a plot file."""
    sKey = posixpath.basename(sPath) if bByBasename else sPath
    return sKey in setResolvedPlotPaths


def _flistSplitOutputPaths(
    dictStep, listOutputPaths, setResolvedPlotPaths=None,
):
    """Split a step's output-path list into (data_files, plot_files)."""
    bByBasename = setResolvedPlotPaths is None
    if bByBasename:
        setResolvedPlotPaths = {
            posixpath.basename(sFile)
            for sFile in dictStep.get("saPlotFiles", [])
        }
    listDataFiles = []
    listPlotFiles = []
    for sPath in listOutputPaths:
        if _fbPathIsPlot(
            sPath, setResolvedPlotPaths, bByBasename,
        ):
            listPlotFiles.append(sPath)
        else:
            listDataFiles.append(sPath)
    return listDataFiles, listPlotFiles


def _fdictBuildStepStatusEntry(
    dictStep, dictStepScripts, listOutputs, dictModTimes,
    dictResolvedVars, iMarkerMtime=None,
):
    """Compute {sStatus, listStaleArtifacts} for a single step."""
    setPlotPaths = {
        sResolved for sResolved, _sBase
        in _flistResolvePlotPaths(dictStep, dictResolvedVars)
    }
    bStale, listStale = _fbStepIsPencilStale(
        dictStep, dictStepScripts, listOutputs, dictModTimes,
        iMarkerMtime=iMarkerMtime,
        setResolvedPlotPaths=setPlotPaths,
    )
    return {
        "sStatus": "modified" if bStale else "unchanged",
        "listStaleArtifacts": listStale,
    }


def _fdictBuildScriptStatus(
    dictWorkflow, dictModTimes, dictVars=None,
    dictMarkerMtimeByStep=None,
):
    """Return per-step pencil status via timestamp staleness comparison."""
    dictScriptsByStep = fnCollectScriptPathsByStep(dictWorkflow)
    dictOutputsByStep = fdictCollectOutputPathsByStep(
        dictWorkflow, dictVars,
    )
    dictResolvedVars = dictVars or _fdictBuildFileStatusVars(dictWorkflow)
    dictMarkerMtimes = dictMarkerMtimeByStep or {}
    dictResult = {}
    for iIndex, dictStep in enumerate(
        dictWorkflow.get("listSteps", [])
    ):
        dictResult[iIndex] = _fdictBuildStepStatusEntry(
            dictStep,
            dictScriptsByStep.get(iIndex, {}),
            dictOutputsByStep.get(iIndex, []),
            dictModTimes, dictResolvedVars,
            iMarkerMtime=_fiMarkerMtime(dictMarkerMtimes, iIndex),
        )
    return dictResult


def _fiMarkerMtime(dictMarkerMtimeByStep, iIndex):
    """Return marker mtime as int for step index, or None if absent."""
    sMtime = dictMarkerMtimeByStep.get(str(iIndex))
    if sMtime is None:
        return None
    try:
        return int(sMtime)
    except (TypeError, ValueError):
        return None


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
                                  dictModTimes=None, sRepoRoot=""):
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
                                   dictModTimes, sRepoRoot)
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
    sRepoRoot = dictWorkflow.get("sProjectRepoPath", "")
    dictInvalidated = _fdictInvalidateAffectedSteps(
        dictWorkflow, dictChangedFiles, dictNewModTimes, sRepoRoot)
    dictCtx["save"](sContainerId, dictWorkflow)
    return dictInvalidated


_I_STAT_BATCH_SIZE = 200


def _fdictGetModTimes(connectionDocker, sContainerId, listPaths):
    """Return {path: mtime_string} for each file that exists."""
    dictResult = {}
    for iStart in range(0, len(listPaths), _I_STAT_BATCH_SIZE):
        dictResult.update(_fdictStatBatch(
            connectionDocker, sContainerId,
            listPaths[iStart:iStart + _I_STAT_BATCH_SIZE],
        ))
    return dictResult


def _fdictStatBatch(connectionDocker, sContainerId, listPaths):
    """Run stat on a single batch of paths; parse 'name mtime' lines."""
    if not listPaths:
        return {}
    sPathArgs = " ".join(fsShellQuote(s) for s in listPaths)
    sCmd = f"stat -c '%n %Y' {sPathArgs} 2>/dev/null || true"
    _iExitCode, sOutput = connectionDocker.ftResultExecuteCommand(
        sContainerId, sCmd,
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


# ---------------------------------------------------------------------------
# Auto Archive: full-verification transition trigger
# ---------------------------------------------------------------------------

_T_TEST_VERIF_KEYS = (
    "sUnitTest", "sIntegrity", "sQualitative", "sQuantitative",
)


def fbIsStepFullyVerified(dictStep):
    """Return True iff user has attested AND every defined test passed.

    "Defined" means the verification field is present. An absent field
    means no tests in that category — silent pass for verification
    purposes. Present-but-not-passed (untested, failed, stale) blocks
    the all-green state.
    """
    dictV = dictStep.get("dictVerification", {})
    if dictV.get("sUser") != "passed":
        return False
    for sKey in _T_TEST_VERIF_KEYS:
        if sKey in dictV and dictV[sKey] != "passed":
            return False
    return True


def flistStepRemoteFiles(dictWorkflow, iStepIndex, sService):
    """Return repo-relative paths for a step's files tracked on sService.

    ``sService`` is "Overleaf" or "Zenodo". Files are enumerated from
    the step's saPlotFiles + saDataFiles, resolved against the repo
    root, then filtered by the matching b<Service> flag in
    dictSyncStatus.
    """
    listSteps = dictWorkflow.get("listSteps", [])
    if iStepIndex < 0 or iStepIndex >= len(listSteps):
        return []
    sBoolKey = f"b{sService}"
    sRepoRoot = dictWorkflow.get("sProjectRepoPath", "")
    dictSyncStatus = dictWorkflow.get("dictSyncStatus", {}) or {}
    dictVars = _fdictBuildFileStatusVars(dictWorkflow)
    listResolved = _flistResolveStepPaths(
        listSteps[iStepIndex], dictVars,
    )
    listResult = []
    for sAbs in listResolved:
        sRel = workflowManager.fsToSyncStatusKey(sAbs, sRepoRoot)
        dictEntry = workflowManager.fdictLookupSyncEntry(
            dictSyncStatus, sRel, sRepoRoot,
        )
        if dictEntry and dictEntry.get(sBoolKey):
            listResult.append(sRel)
    return listResult


async def _fnPushOverleafForAutoArchive(
    connectionDocker, sContainerId, dictWorkflow, listFiles,
):
    """Push files to Overleaf for the auto-archive flow."""
    import asyncio
    from . import syncDispatcher
    sProjectId = dictWorkflow.get("sOverleafProjectId", "")
    if not sProjectId or not listFiles:
        return False
    sTargetDirectory = dictWorkflow.get(
        "sOverleafTargetDirectory", ""
    )
    iExit, _sOut = await asyncio.to_thread(
        syncDispatcher.ftResultPushToOverleaf,
        connectionDocker, sContainerId,
        listFiles, sProjectId, sTargetDirectory,
        dictWorkflow,
    )
    if iExit != 0:
        return False
    workflowManager.fnUpdateSyncStatus(
        dictWorkflow, listFiles, "Overleaf",
    )
    return True


async def _fnArchiveZenodoForAutoArchive(
    connectionDocker, sContainerId, dictWorkflow, listFiles,
):
    """Archive files to Zenodo for the auto-archive flow."""
    import asyncio
    from . import syncDispatcher
    if not listFiles:
        return False
    sZenodoService = dictWorkflow.get("sZenodoService", "sandbox")
    iParentDepositId = int(
        dictWorkflow.get("sZenodoDepositionId", "0") or 0
    )
    iExit, _sOut = await asyncio.to_thread(
        syncDispatcher.ftResultArchiveToZenodo,
        connectionDocker, sContainerId,
        sZenodoService, listFiles, None, iParentDepositId,
    )
    if iExit != 0:
        return False
    workflowManager.fnUpdateSyncStatus(
        dictWorkflow, listFiles, "Zenodo",
    )
    return True


async def fnMaybeAutoArchive(
    connectionDocker, sContainerId, dictWorkflow, iStepIndex,
    bWasFullyVerifiedBefore,
):
    """Push step's tracked files to Overleaf/Zenodo on verify transition.

    Fires only when the step transitions from not-fully-verified to
    fully-verified AND the workflow's bAutoArchive setting is True.
    Pushes never block the caller: failures are logged and the manual
    sync UI remains the recovery path. Returns True when at least one
    remote was pushed (callers can use this to know whether to refresh
    badges).
    """
    if not dictWorkflow.get("bAutoArchive"):
        return False
    if bWasFullyVerifiedBefore:
        return False
    listSteps = dictWorkflow.get("listSteps", [])
    if iStepIndex < 0 or iStepIndex >= len(listSteps):
        return False
    if not fbIsStepFullyVerified(listSteps[iStepIndex]):
        return False
    bAnyPushed = False
    listOverleaf = flistStepRemoteFiles(
        dictWorkflow, iStepIndex, "Overleaf",
    )
    if listOverleaf:
        try:
            bAnyPushed |= await _fnPushOverleafForAutoArchive(
                connectionDocker, sContainerId,
                dictWorkflow, listOverleaf,
            )
        except Exception as error:
            logger.warning(
                "Auto Archive: Overleaf push failed for step %d: %s",
                iStepIndex, error,
            )
    listZenodo = flistStepRemoteFiles(
        dictWorkflow, iStepIndex, "Zenodo",
    )
    if listZenodo:
        try:
            bAnyPushed |= await _fnArchiveZenodoForAutoArchive(
                connectionDocker, sContainerId,
                dictWorkflow, listZenodo,
            )
        except Exception as error:
            logger.warning(
                "Auto Archive: Zenodo push failed for step %d: %s",
                iStepIndex, error,
            )
    return bAnyPushed
