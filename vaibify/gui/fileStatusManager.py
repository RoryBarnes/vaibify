"""File-status polling, modification tracking, and step invalidation.

Verification State Machine
--------------------------
Each step carries a ``dictVerification`` with orthogonal state fields:

**Execution verification** (set by test runner / pipeline):
- ``sUnitTest``:      untested | passed | failed | unnecessary
- ``sIntegrity``:     untested | passed | failed | unnecessary
- ``sQualitative``:   untested | passed | failed | unnecessary
- ``sQuantitative``:  untested | passed | failed | unnecessary

``unnecessary`` is the derivation-hook value for a category whose
``saCommands`` list is empty — no tests are defined, so there is
nothing to run. ``fbStepTestsPassing`` treats it as green;
invalidation skips it; marker-application leaves it sticky.

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
import shlex

from docker.errors import APIError, NotFound

from ..reproducibility.stepPredicates import (
    fbStepTestsPassing,
    fbStepTimingClean,
    fbStepUserApproved,
)

_S_POLL_PATHFILE = "/tmp/vaibifyPoll.list"

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
    "fsWorkflowSlugFromPath",
    "fbReconcileUpstreamFlags",
    "fbReconcileUserVerificationTimestamps",
    "fbStepTestsPassing",
    "fbStepTimingClean",
    "fbStepUserApproved",
    "flistStepRemoteFiles",
    "fnMaybeAutoArchive",
]

def fsMarkerNameFromStepDirectory(sStepDirectory):
    """Return the marker filename for a step directory."""
    return sStepDirectory.strip("/").replace("/", "_") + ".json"


def fsWorkflowSlugFromPath(sWorkflowPath):
    """Return the marker-namespace slug for a workflow JSON path.

    Two workflows in the same project repo namespace their markers by
    workflow JSON basename so they don't clobber each other when steps
    share an ``sDirectory``. Returns an empty string when the path is
    missing — callers treat that as a no-marker state.
    """
    if not sWorkflowPath:
        return ""
    sBase = posixpath.basename(sWorkflowPath)
    if sBase.endswith(".json"):
        sBase = sBase[:-5]
    return sBase


def fnCollectMarkerPathsByStep(
    dictWorkflow, sProjectRepoPath, sWorkflowPath,
):
    """Return {iStepIndex: sMarkerPath} for each step with a directory.

    Markers live under
    ``<sProjectRepoPath>/.vaibify/test_markers/<workflowSlug>/`` so
    workflows in the same project repo never clobber each other.
    ``sWorkflowPath`` is the workflow JSON's container path (e.g.
    ``dictWorkflow['sPath']``) — its basename minus ``.json`` is the
    namespace slug. An empty ``sProjectRepoPath`` or empty
    ``sWorkflowPath`` yields an empty map; the caller surfaces the
    no-workflow state explicitly rather than falling back silently.
    """
    dictResult = {}
    sSlug = fsWorkflowSlugFromPath(sWorkflowPath)
    if not sProjectRepoPath or not sSlug:
        return dictResult
    sMarkerDir = posixpath.join(
        sProjectRepoPath, ".vaibify", "test_markers", sSlug,
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
    for sFile in (dictStep.get("saOutputDataFiles", [])
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
    """Return True if a pipeline is currently running in container.

    Raw read — does not reconcile a vanished runner. Async callers
    must resolve liveness via ``pipelineState.fdictReadReconciledState``
    and pass the resulting boolean down through
    ``_flistDetectAndInvalidate``; this helper survives as the sync
    fallback for code paths (and tests) that don't have an event loop
    on hand.
    """
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
    for sFile in dictStep.get("saOutputDataFiles", []):
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


def _fnResetUserAttestationIfStale(dictVerification):
    """Mark ``sUser`` as ``stale`` while preserving ``sLastUserUpdate``.

    Two-state semantics: ``sUser`` flips to ``stale`` (the researcher
    *did* attest, but the outputs changed since) and ``sLastUserUpdate``
    is preserved as evidence of the prior attestation. ``untested`` is
    reserved for steps that were never attested. Mutations-only; caller
    owns the ``dictStep["dictVerification"] = dictVerification``
    write-back per the dashboard-ground-truth contract — this helper
    never persists.
    """
    dictVerification["sUser"] = "stale"


def _fnClearPlotInvalidationFlags(dictVerification):
    """Clear ``listModifiedFiles`` + ``bOutputModified``. Mutations-only."""
    dictVerification.pop("listModifiedFiles", None)
    dictVerification.pop("bOutputModified", None)


def _fdictDefaultPlotVars(dictWorkflow):
    """Return the {plot-dir, figure-type} block when caller passed no dictVars."""
    return {
        "sPlotDirectory": dictWorkflow.get("sPlotDirectory", "Plot"),
        "sFigureType": dictWorkflow.get("sFigureType", "pdf"),
    }


_dictLastLoggedStaleByStep = {}


def _fnLogFreshnessCheck(
    iIndex, sLastUserUpdate, iUserEpoch, listPlotPaths, bStale,
):
    """Log freshness at DEBUG per poll, at INFO on a staleness transition."""
    bPreviousStale = _dictLastLoggedStaleByStep.get(iIndex)
    _dictLastLoggedStaleByStep[iIndex] = bStale
    if bPreviousStale is not None and bPreviousStale != bStale:
        logger.info(
            "Freshness transition step %d: bStale %s -> %s "
            "(sLastUserUpdate=%s)",
            iIndex, bPreviousStale, bStale, sLastUserUpdate,
        )
        return
    logger.debug(
        "Freshness check step %d: sLastUserUpdate=%s "
        "iUserEpoch=%s paths=%s bStale=%s",
        iIndex, sLastUserUpdate, iUserEpoch, listPlotPaths, bStale,
    )


def _fdictResolvePlotPathsForAttestedSteps(dictWorkflow, dictVars):
    """Resolve plot paths once per user-attested step.

    Returns ``{iIndex: [resolved_path, ...]}`` skipping any step where
    ``sUser != "passed"`` — the only steps that the stale-attestation
    check needs to inspect. Pre-resolving once at the top of
    ``_fbCheckStaleUserVerification`` keeps the per-poll cost flat as
    the inner loop grows: ``_flistResolvePlotPaths`` is invoked
    exactly once per attested step, not interleaved with the
    evaluation work.
    """
    dictResult = {}
    for iIndex, dictStep in enumerate(
        dictWorkflow.get("listSteps", []),
    ):
        dictV = dictStep.get("dictVerification", {})
        if dictV.get("sUser") != "passed":
            continue
        dictResult[iIndex] = [
            tEntry[0]
            for tEntry in _flistResolvePlotPaths(dictStep, dictVars)
        ]
    return dictResult


def _ftEvaluateFreshness(iIndex, dictStep, dictModTimes, listPlotPaths):
    """Return ``(bChecked, bStale)`` for the stale-attestation check.

    ``listPlotPaths`` is pre-resolved by the caller (see
    ``_fdictResolvePlotPathsForAttestedSteps``). ``bChecked`` is False
    when the step is not user-attested, has no ``sLastUserUpdate``
    field, or carries an unparseable timestamp — in any of those
    cases the caller skips the step. When ``bChecked`` is True the
    freshness-check log line has been emitted as a side effect.
    """
    dictVerification = dictStep.get("dictVerification", {})
    if dictVerification.get("sUser") != "passed":
        return False, False
    sLastUserUpdate = dictVerification.get("sLastUserUpdate", "")
    if not sLastUserUpdate:
        return False, False
    iUserEpoch = _fiParseUtcTimestamp(sLastUserUpdate)
    if iUserEpoch is None:
        return False, False
    bStale = _fbAnyMtimeNewerThan(
        listPlotPaths, dictModTimes, iUserEpoch,
    )
    _fnLogFreshnessCheck(
        iIndex, sLastUserUpdate, iUserEpoch, listPlotPaths, bStale,
    )
    return True, bStale


def _fbCheckStaleUserVerification(dictWorkflow, dictModTimes,
                                   dictVars=None):
    """Reset sUser if plot files are newer than sLastUserUpdate.

    Returns True if any step was modified, so the caller can save.
    This handles the case where outputs changed before the server
    started, so poll-based delta detection never fires.
    """
    bChanged = False
    if dictVars is None:
        dictVars = _fdictDefaultPlotVars(dictWorkflow)
    dictPathsByStep = _fdictResolvePlotPathsForAttestedSteps(
        dictWorkflow, dictVars,
    )
    for iIndex, dictStep in enumerate(
        dictWorkflow.get("listSteps", []),
    ):
        listPlotPaths = dictPathsByStep.get(iIndex, [])
        bChecked, bStale = _ftEvaluateFreshness(
            iIndex, dictStep, dictModTimes, listPlotPaths,
        )
        if bChecked and _fbApplyStaleVerdict(dictStep, bStale):
            bChanged = True
    return bChanged


def _fbApplyStaleVerdict(dictStep, bStale):
    """Persist the stale/fresh verdict on a step. Returns True iff mutated."""
    dictVerification = dictStep["dictVerification"]
    if bStale:
        _fnResetUserAttestationIfStale(dictVerification)
        dictStep["dictVerification"] = dictVerification
        return True
    if dictVerification.get("listModifiedFiles"):
        _fnClearPlotInvalidationFlags(dictVerification)
        dictStep["dictVerification"] = dictVerification
        return True
    return False


_SET_PASSED_TEST_STATES = frozenset({"passed", "passed-from-marker"})


def _fnApplyDataInvalidation(
    dictVerification, listChangedPaths, listDataFiles,
):
    """Reset ``sUnitTest`` + per-category verifications on a data-file change.

    Mutations-only on ``dictVerification``; outer caller persists.
    Steps whose category is ``unnecessary`` (no commands) stay sticky
    so the dashboard does not show false invalidation. Marker-bootstrapped
    steps (``passed-from-marker``) demote on data change just like fresh
    ``passed`` — the guard short-circuits only the no-op states.
    """
    if dictVerification.get("sUnitTest") not in _SET_PASSED_TEST_STATES:
        return
    if not _fbAnyDataFileChanged(listChangedPaths, listDataFiles):
        return
    dictVerification["sUnitTest"] = "untested"
    for _sCatKey, sVerifKey in _LIST_CATEGORY_KEYS:
        if dictVerification.get(sVerifKey) == "unnecessary":
            continue
        if sVerifKey in dictVerification:
            dictVerification[sVerifKey] = "untested"


def _fnApplyPlotInvalidation(
    dictStep, dictVerification, listChangedPaths, dictModTimes,
):
    """Reset sUser via _fnResetUserAttestationIfStale on a plot change.

    Mutations-only. The invariant-preserving log line stays here so a
    "why did sUser get reset?" answer is one grep away.
    """
    if dictVerification.get("sUser") != "passed":
        return
    bPlotNewer = _fbPlotNewerThanUserVerification(
        dictStep, listChangedPaths, dictModTimes,
    )
    logger.info(
        "_fnInvalidateStepFiles sUser=%s changed=%s "
        "bPlotNewer=%s sLastUserUpdate=%s",
        dictVerification.get("sUser"), listChangedPaths,
        bPlotNewer, dictVerification.get("sLastUserUpdate"),
    )
    if bPlotNewer:
        _fnResetUserAttestationIfStale(dictVerification)


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
    _fnApplyDataInvalidation(
        dictVerification, listChangedPaths,
        dictStep.get("saOutputDataFiles", []),
    )
    _fnApplyPlotInvalidation(
        dictStep, dictVerification, listChangedPaths, dictModTimes,
    )
    if dictVerification.get("sPlotStandards") == "passed":
        if _fbAnyPlotFileChanged(
            listChangedPaths, dictStep.get("saPlotFiles", []),
        ):
            dictVerification["sPlotStandards"] = "stale"
    listExistingRel = flistNormalizeModifiedFiles(
        dictVerification.get("listModifiedFiles", []), sRepoRoot,
    )
    listChangedRel = flistNormalizeModifiedFiles(
        listChangedPaths, sRepoRoot,
    )
    dictVerification["listModifiedFiles"] = sorted(
        set(listExistingRel) | set(listChangedRel),
    )
    dictStep["dictVerification"] = dictVerification


def _fnInvalidateDownstreamStep(dictStep):
    """Mark a downstream step as affected by upstream changes."""
    dictVerification = dictStep.get("dictVerification", {})
    if dictVerification.get("sUnitTest") in _SET_PASSED_TEST_STATES:
        dictVerification["sUnitTest"] = "untested"
        for _sCatKey, sVerifKey in _LIST_CATEGORY_KEYS:
            if dictVerification.get(sVerifKey) == "unnecessary":
                continue
            if sVerifKey in dictVerification:
                dictVerification[sVerifKey] = "untested"
    dictVerification["bUpstreamModified"] = True
    dictStep["dictVerification"] = dictVerification


def fbReconcileUserVerificationTimestamps(dictWorkflow):
    """Strip ``sLastUserUpdate`` from steps where it carries no evidence.

    ``sLastUserUpdate`` is only meaningful while attestation evidence
    exists. ``"passed"`` (currently attested) and ``"stale"`` (was
    attested, outputs changed since) both retain the timestamp — the
    ``"stale"`` case is the load-bearing distinction that lets the
    dashboard tell *"outputs changed after you verified"* apart from
    *"never verified"*. ``"untested"`` and ``"failed"`` strip the
    timestamp: there was no attestation, so the field is ghost data
    that misleads the UI and re-introduces stale-artifact warnings.
    Returns True when any step changed so the caller persists.
    """
    bAnyChanged = False
    for dictStep in dictWorkflow.get("listSteps", []):
        dictVerify = dictStep.get("dictVerification", {})
        if dictVerify.get("sUser") in ("passed", "stale"):
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
    dictResolvedVars, iMarkerMtime=None, dictManifestCache=None,
    filesRepo=None,
):
    """Compute {sStatus, listStaleArtifacts} for a single step.

    The mtime-based verdict (``_fbStepIsPencilStale``) is the source
    of "may be stale". The optional manifest short-circuit refines it
    to "is/isn't actually drifted": if the project repo carries a
    ``MANIFEST.sha256`` that matches every output of this step, an
    mtime-stale verdict is downgraded to ``unchanged``. This prevents
    a fresh git clone — where every mtime is "now" — from
    false-positively flagging every step as modified.
    """
    setPlotPaths = {
        sResolved for sResolved, _sBase
        in _flistResolvePlotPaths(dictStep, dictResolvedVars)
    }
    bStale, listStale = _fbStepIsPencilStale(
        dictStep, dictStepScripts, listOutputs, dictModTimes,
        iMarkerMtime=iMarkerMtime,
        setResolvedPlotPaths=setPlotPaths,
    )
    if bStale and _fbStepHashesMatchManifest(
        dictStep, dictResolvedVars, dictManifestCache, filesRepo,
    ):
        bStale = False
        listStale = []
    return {
        "sStatus": "modified" if bStale else "unchanged",
        "listStaleArtifacts": listStale,
    }


def _fbStepHashesMatchManifest(
    dictStep, dictResolvedVars, dictManifestCache, filesRepo=None,
):
    """Return True iff every output's content matches MANIFEST.sha256.

    Conservative: returns False when there is no project repo, no
    manifest, no declared outputs, any declared output is absent from
    the manifest (cannot prove freshness without an entry), or any
    tracked output drifted from its expected hash. Only when every
    declared output is both manifest-tracked and bit-identical to its
    expected hash does the short-circuit fire. ``filesRepo`` (the
    poll's container snapshot) supersedes the host-path fallback so
    the manifest is read where it actually lives.
    """
    if dictManifestCache is None:
        return False
    sRepoRoot = (dictResolvedVars or {}).get("sRepoRoot", "")
    if filesRepo is None:
        filesRepo = sRepoRoot
    if not sRepoRoot:
        return False
    from . import hashStaleness
    if not hashStaleness.fbManifestExists(filesRepo):
        return False
    listRelPaths = _flistStepOutputsRepoRelative(dictStep, sRepoRoot)
    if not listRelPaths:
        return False
    if not _fbAllPathsTrackedByManifest(filesRepo, listRelPaths):
        return False
    setStale = hashStaleness.fsetStaleOutputsAgainstManifest(
        filesRepo, listRelPaths, dictManifestCache,
    )
    return len(setStale) == 0


def _fbAllPathsTrackedByManifest(filesRepo, listRelPaths):
    """Return True iff every path appears as a manifest entry."""
    from . import hashStaleness
    dictEntries = hashStaleness._fdictReadManifestEntries(filesRepo)
    if not dictEntries:
        return False
    for sRelPath in listRelPaths:
        if sRelPath not in dictEntries:
            return False
    return True


def _flistStepOutputsRepoRelative(dictStep, sRepoRoot):
    """Return repo-relative output paths declared on a step.

    Resolves each ``saOutputDataFiles``/``saPlotFiles`` entry against the
    step directory the same way ``_fsResolveStepFilePath`` does, then
    strips the repo root so the result lines up with manifest keys
    (which are repo-relative POSIX strings written by
    ``manifestWriter``).
    """
    from .pathContract import fsAbsToRepoRelative
    sStepDir = dictStep.get("sDirectory", "")
    listRelative = []
    for sFile in (dictStep.get("saOutputDataFiles", [])
                  + dictStep.get("saPlotFiles", [])):
        if not sFile:
            continue
        sAbs = _fsResolveStepFilePath(
            sFile, sStepDir, {"sRepoRoot": sRepoRoot},
        )
        listRelative.append(fsAbsToRepoRelative(sAbs, sRepoRoot))
    return listRelative


def _fdictBuildScriptStatus(
    dictWorkflow, dictModTimes, dictVars=None,
    dictMarkerMtimeByStep=None, filesRepo=None,
):
    """Return per-step pencil status via timestamp staleness comparison.

    ``filesRepo`` is the poll's repo snapshot; when supplied, the
    manifest short-circuit reads container truth instead of probing
    the host filesystem at a container path.
    """
    dictScriptsByStep = fnCollectScriptPathsByStep(dictWorkflow)
    dictOutputsByStep = fdictCollectOutputPathsByStep(
        dictWorkflow, dictVars,
    )
    dictResolvedVars = dictVars or _fdictBuildFileStatusVars(dictWorkflow)
    dictMarkerMtimes = dictMarkerMtimeByStep or {}
    dictManifestCache = {}
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
            dictManifestCache=dictManifestCache,
            filesRepo=filesRepo,
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
                             dictVars=None, bPipelineRunning=None):
    """Return changed files by step index, or empty if none changed.

    ``bPipelineRunning`` is resolved by the async caller via the
    reconciling reader so a vanished runner does not silently suppress
    invalidation. Falling back to the sync read keeps non-async callers
    (e.g. legacy tests) working without observable change.
    """
    if "dictPreviousModTimes" not in dictCtx:
        dictCtx["dictPreviousModTimes"] = {}
    dictPrevByContainer = dictCtx["dictPreviousModTimes"]
    dictOldModTimes = dictPrevByContainer.get(sContainerId, {})
    dictPrevByContainer[sContainerId] = dict(dictNewModTimes)
    if not dictOldModTimes:
        return {}
    if bPipelineRunning is None:
        bPipelineRunning = _fbPipelineIsRunning(dictCtx, sContainerId)
    if bPipelineRunning:
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


def _flistDetectHashStaleFiles(
    dictWorkflow, sWorkspaceRoot, dictCache,
    dictMarkersByStep, dictMtimeHintsByStep=None,
):
    """Return ``{iStepIndex: [stale_repo_rel_paths]}`` per marker drift."""
    dictResult = {}
    listSteps = dictWorkflow.get("listSteps", [])
    dictHintsByStep = dictMtimeHintsByStep or {}
    for iIndex, dictStep in enumerate(listSteps):
        listStale = _flistStaleOutputsForStepIndex(
            dictStep, iIndex, dictMarkersByStep, sWorkspaceRoot,
            dictCache, dictHintsByStep,
        )
        if listStale:
            dictResult[iIndex] = listStale
    return dictResult


def _flistStaleOutputsForStepIndex(
    dictStep, iIndex, dictMarkersByStep, sWorkspaceRoot,
    dictCache, dictHintsByStep,
):
    """Return sorted stale repo-rel paths for one step, or ``[]`` when none."""
    from . import hashStaleness
    dictMarker = _fdictMarkerForStep(dictStep, iIndex, dictMarkersByStep)
    if dictMarker is None:
        return []
    if not hashStaleness.fbMarkerHasHashes(dictMarker):
        return []
    setStale = hashStaleness.fsetStaleOutputsForStep(
        dictMarker, sWorkspaceRoot, dictCache,
        dictMtimeHints=dictHintsByStep.get(iIndex),
    )
    return sorted(setStale) if setStale else []


def _fdictMarkerForStep(dictStep, iIndex, dictMarkersByStep):
    """Return the marker iff its ``sLabel`` and ``sDirectory`` match the live step."""
    dictMarker = dictMarkersByStep.get(iIndex)
    if not isinstance(dictMarker, dict):
        return None
    if not _fbMarkerIdentityMatchesStep(dictStep, dictMarker):
        return None
    return dictMarker


def _fbMarkerIdentityMatchesStep(dictStep, dictMarker):
    """Return True iff marker label/directory agree with the live step."""
    sLiveLabel = dictStep.get("sLabel", "")
    sLiveDirectory = dictStep.get("sDirectory", "")
    sMarkerLabel = dictMarker.get("sLabel", "")
    sMarkerDirectory = dictMarker.get("sDirectory", "")
    if sLiveLabel and sMarkerLabel and sLiveLabel != sMarkerLabel:
        return False
    if sLiveDirectory and sMarkerDirectory:
        if _fsRepoRelDirectory(sLiveDirectory) != sMarkerDirectory:
            return False
    return True


def _fsRepoRelDirectory(sDirectory):
    """Strip a leading slash so live and marker directories compare cleanly."""
    if not sDirectory:
        return ""
    return sDirectory.lstrip("/")


def _fdictHashStaleAbsPathsByStep(
    dictHashStaleByStep, dictWorkflow, dictVars,
):
    """Translate hash-stale repo-rel paths to absolute container paths.

    Marker hashes are keyed by *repo-relative* paths (the conftest
    plugin writes them via :func:`_fsRepoRelFromFile`), so we join
    each entry directly against the project repo root rather than
    re-joining the step directory.
    """
    sRepoRoot = _fsResolveRepoRoot(dictWorkflow, dictVars)
    dictResult = {}
    for iIndex, listRelPaths in dictHashStaleByStep.items():
        dictResult[iIndex] = [
            _fsAbsFromRepoRelative(sRelPath, sRepoRoot)
            for sRelPath in listRelPaths
        ]
    return dictResult


def _fsResolveRepoRoot(dictWorkflow, dictVars):
    """Pick the repo root from ``dictVars`` first, falling back to workflow."""
    sFromVars = (dictVars or {}).get("sRepoRoot", "")
    if sFromVars:
        return sFromVars
    return dictWorkflow.get("sProjectRepoPath", "")


def _fsAbsFromRepoRelative(sRepoRelPath, sRepoRoot):
    """Join a repo-relative posix path against the project repo root."""
    if posixpath.isabs(sRepoRelPath) or not sRepoRoot:
        return sRepoRelPath
    return posixpath.join(sRepoRoot, sRepoRelPath)


def _fdictUnionChangedFiles(dictMtimeChanged, dictHashStaleAbs):
    """Return per-step deduped union of mtime-changed and hash-stale paths."""
    setKeys = set(dictMtimeChanged.keys()) | set(dictHashStaleAbs.keys())
    dictResult = {}
    for iIndex in setKeys:
        setPaths = set(dictMtimeChanged.get(iIndex, []))
        setPaths.update(dictHashStaleAbs.get(iIndex, []))
        dictResult[iIndex] = sorted(setPaths)
    return dictResult


def _flistDetectAndInvalidate(
    dictCtx, sContainerId, dictWorkflow, dictNewModTimes,
    dictVars=None, dictMarkersByStep=None, dictCache=None,
    bPipelineRunning=None,
):
    """Detect mtime + hash drift and invalidate affected steps."""
    dictChangedFiles = _fdictDetectChangedFiles(
        dictCtx, sContainerId, dictWorkflow,
        dictNewModTimes, dictVars,
        bPipelineRunning=bPipelineRunning,
    )
    dictHashStaleByStep = _fdictHashStaleFromMarkers(
        dictWorkflow, dictNewModTimes, dictMarkersByStep, dictCache,
    )
    if not dictChangedFiles and not dictHashStaleByStep:
        return {}
    dictInvalidated = _fdictApplyInvalidationFromDrifts(
        dictWorkflow, dictChangedFiles, dictHashStaleByStep,
        dictNewModTimes, dictVars,
    )
    dictCtx["save"](sContainerId, dictWorkflow)
    return dictInvalidated


def _fdictApplyInvalidationFromDrifts(
    dictWorkflow, dictChangedFiles, dictHashStaleByStep,
    dictNewModTimes, dictVars,
):
    """Merge mtime + hash drift and invalidate affected steps in-place."""
    sRepoRoot = dictWorkflow.get("sProjectRepoPath", "")
    dictHashStaleAbs = _fdictHashStaleAbsPathsByStep(
        dictHashStaleByStep, dictWorkflow, dictVars,
    )
    dictUnionChanged = _fdictUnionChangedFiles(
        dictChangedFiles, dictHashStaleAbs,
    )
    return _fdictInvalidateAffectedSteps(
        dictWorkflow, dictUnionChanged, dictNewModTimes, sRepoRoot,
    )


def _fdictHashStaleFromMarkers(
    dictWorkflow, dictNewModTimes, dictMarkersByStep, dictCache,
):
    """Compute per-step hash drift; gracefully no-ops without markers."""
    if not dictMarkersByStep:
        return {}
    sRepoRoot = dictWorkflow.get("sProjectRepoPath", "")
    if not sRepoRoot:
        return {}
    dictHintsByStep = _fdictBuildMtimeHintsByStep(
        dictWorkflow, dictMarkersByStep, dictNewModTimes, sRepoRoot,
    )
    return _flistDetectHashStaleFiles(
        dictWorkflow, sRepoRoot,
        dictCache if dictCache is not None else {},
        dictMarkersByStep, dictHintsByStep,
    )


def _fdictBuildMtimeHintsByStep(
    dictWorkflow, dictMarkersByStep, dictNewModTimes, sRepoRoot,
):
    """Return ``{iStepIndex: {sRepoRelPath: fMtime}}`` from already-known mtimes.

    The poller already stat'd every output file; we just rewrap the
    same data under repo-relative keys so the cache lookup can skip the
    redundant ``os.stat`` for each hash check.
    """
    dictResult = {}
    listSteps = dictWorkflow.get("listSteps", [])
    for iIndex, dictMarker in dictMarkersByStep.items():
        if not (0 <= iIndex < len(listSteps)):
            continue
        if not isinstance(dictMarker, dict):
            continue
        dictHints = _fdictMtimeHintsForStep(
            listSteps[iIndex], dictMarker, dictNewModTimes, sRepoRoot,
        )
        if dictHints:
            dictResult[iIndex] = dictHints
    return dictResult


def _fdictMtimeHintsForStep(
    dictStep, dictMarker, dictNewModTimes, sRepoRoot,
):
    """Return ``{sRepoRelPath: fMtime}`` mtime hints for one step's outputs."""
    dictHints = {}
    for sRelPath in (dictMarker.get("dictOutputHashes") or {}):
        sAbs = _fsAbsFromRepoRelative(sRelPath, sRepoRoot)
        sMtime = dictNewModTimes.get(sAbs)
        if sMtime is None:
            continue
        try:
            dictHints[sRelPath] = float(sMtime)
        except (TypeError, ValueError):
            continue
    return dictHints


def _fdictStatViaPathfile(connectionDocker, sContainerId, listPaths):
    """Return {sAbsPath: sMtime} for paths that exist; one exec total."""
    dictModTimes, _sFingerprint = _ftStatAndFingerprintViaPathfile(
        connectionDocker, sContainerId, listPaths, "",
    )
    return dictModTimes


def _ftStatAndFingerprintViaPathfile(
    connectionDocker, sContainerId, listPaths, sFingerprintPath,
):
    """Return ``({sAbsPath: sMtime}, sFingerprint)``; one exec total.

    When ``sFingerprintPath`` is given, the same exec also emits a
    ``fingerprint:<sha256>`` line for that file, giving the workflow
    reload detector a content signal without a second docker-exec
    round trip per poll tick. The fingerprint comes back empty when
    the file is missing or the hash command flaked.
    """
    if not listPaths and not sFingerprintPath:
        return {}, ""
    baContent = ("\n".join(listPaths) + "\n").encode("utf-8")
    sCmd = (
        f"xargs -d '\\n' -a {_S_POLL_PATHFILE} "
        f"stat -c '%n %Y' 2>/dev/null"
    )
    if sFingerprintPath:
        sCmd += (
            f"; printf 'fingerprint:%s\\n' "
            f"\"$(sha256sum -- {shlex.quote(sFingerprintPath)} "
            f"2>/dev/null | cut -d' ' -f1)\""
        )
    try:
        connectionDocker.fnWriteFileViaTar(
            sContainerId, _S_POLL_PATHFILE, baContent,
        )
        _iExit, sOutput = connectionDocker.ftResultExecuteCommand(
            sContainerId, sCmd,
        )
    except (APIError, NotFound):
        logger.info(
            "container vanished mid-poll, container=%s", sContainerId,
        )
        return {}, ""
    return _ftParseStatAndFingerprintLines(sOutput)


def _ftParseStatAndFingerprintLines(sOutput):
    """Split the ``fingerprint:`` marker line out of stat output.

    Stat lines are '%n %Y' with absolute paths, so no stat line can
    start with the marker; the marker line is unambiguous.
    """
    sFingerprint = ""
    listStatLines = []
    for sLine in (sOutput or "").strip().split("\n"):
        sLine = sLine.strip()
        if sLine.startswith("fingerprint:"):
            sFingerprint = sLine[len("fingerprint:"):].strip()
        elif sLine:
            listStatLines.append(sLine)
    return _fdictParseStatLines("\n".join(listStatLines)), sFingerprint


def _fdictParseStatLines(sOutput):
    """Parse 'name mtime' lines from stat output into a dict."""
    dictResult = {}
    for sLine in (sOutput or "").strip().split("\n"):
        sLine = sLine.strip()
        if not sLine:
            continue
        listParts = sLine.rsplit(" ", 1)
        if len(listParts) == 2:
            dictResult[listParts[0]] = listParts[1]
    return dictResult


def _flistEvictAbsentKeys(dictAll, setKeysToKeep):
    """Drop and return cache keys that aren't in setKeysToKeep."""
    listEvicted = [
        sKey for sKey in list(dictAll.keys())
        if sKey not in setKeysToKeep
    ]
    for sKey in listEvicted:
        dictAll.pop(sKey, None)
    return listEvicted


# Authoritative list of every container-id-keyed dict that lives on
# the shared ``dictCtx`` and grows once per container forever unless
# swept. Two side effects:
#   1. ``fnSweepAllContainerCaches`` iterates this list to evict stale
#      keys from each dict in lockstep with the running-container set.
#   2. ``fdictBuildContext`` in ``pipelineServer`` initializes most of
#      these keys eagerly. ``dictManifestShaCache`` is the exception:
#      ``pipelineRoutes`` lazily ``setdefault``s it on first access, so
#      it may legitimately be absent from ``dictCtx`` at sweep time.
#      The sweep tolerates that by skipping any name that does not
#      resolve to a dict — adding a new container-keyed cache means
#      appending it here AND either initializing it in
#      ``fdictBuildContext`` or relying on the same isinstance guard.
# ``dictPipelineStateLocks`` is evicted via the dedicated lock helper
# (``pipelineState.fnEvictStateLockForContainer``) so the asyncio.Lock
# objects are released cleanly rather than dropped raw, and is omitted
# from this plain-dict list for that reason.
_LIST_CONTAINER_KEYED_CACHES = (
    "workflows",
    "paths",
    "containerUsers",
    "pipelineTasks",
    "sourceCodeDeps",
    "lastSelfWriteFingerprints",
    "lastDiscoveredWorkflows",
    "dictSyncEpochs",
    "dictWorkflowEpochs",
    "dictManifestShaCache",
)


def fnSweepAllContainerCaches(dictCtx, listRunningContainers):
    """Fan eviction across every per-container cache vaibify keeps.

    Without one coordinator the docker-substrate cache, the state-lock
    dict, and the file-status cache drift out of phase: a container
    that has been gone for hours can still hold an asyncio.Lock or a
    workflow snapshot while the docker handle has already been
    evicted. Call this from the same place that refreshes the running
    list (e.g. the registry route, the periodic background sweep) so
    all sweeps see one consistent snapshot. Returns the union of
    evicted container ids.
    """
    setRunning = set(listRunningContainers or [])
    setEvicted = set()
    if dictCtx is None:
        return setEvicted
    setEvicted |= _fsetSweepPlainDicts(dictCtx, setRunning)
    setEvicted |= _fsetSweepStateLocks(dictCtx, setRunning)
    setEvicted |= _fsetSweepInteractiveContexts(setRunning)
    _fnFanOutToSiblingModules(dictCtx, setRunning)
    return setEvicted


def _fsetSweepPlainDicts(dictCtx, setRunning):
    """Evict stale keys from every dict named in _LIST_CONTAINER_KEYED_CACHES."""
    setEvicted = set()
    for sCacheName in _LIST_CONTAINER_KEYED_CACHES:
        dictCache = dictCtx.get(sCacheName)
        if not isinstance(dictCache, dict):
            continue
        for sContainerId in list(dictCache.keys()):
            if sContainerId not in setRunning:
                dictCache.pop(sContainerId, None)
                setEvicted.add(sContainerId)
    return setEvicted


def _fsetSweepStateLocks(dictCtx, setRunning):
    """Evict pipeline-state asyncio locks for absent containers."""
    setEvicted = set()
    dictLocks = dictCtx.get("dictPipelineStateLocks") or {}
    for sContainerId in list(dictLocks.keys()):
        if sContainerId not in setRunning:
            pipelineState.fnEvictStateLockForContainer(
                dictCtx, sContainerId,
            )
            setEvicted.add(sContainerId)
    return setEvicted


def _fsetSweepInteractiveContexts(setRunning):
    """Evict module-level interactive-context registrations for absent ids.

    ``DICT_INTERACTIVE_CONTEXTS_BY_CONTAINER`` is published from
    ``fnPipelineMessageLoop``'s own ``finally`` block, but a crashed
    container that never reaches the finally (process kill, ws abort)
    can leak its registration; this sweep is the safety net.
    """
    setEvicted = set()
    try:
        from . import pipelineServer
    except ImportError:
        return setEvicted
    dictContexts = getattr(
        pipelineServer, "DICT_INTERACTIVE_CONTEXTS_BY_CONTAINER", None,
    )
    if not isinstance(dictContexts, dict):
        return setEvicted
    for sContainerId in list(dictContexts.keys()):
        if sContainerId not in setRunning:
            dictContexts.pop(sContainerId, None)
            setEvicted.add(sContainerId)
    return setEvicted


def _fnFanOutToSiblingModules(dictCtx, setRunning):
    """Notify sibling modules (docker pool, host incidents) of the sweep.

    Each fan-out is wrapped so a missing module or one-off failure
    cannot abort the rest of the sweep.
    """
    connectionDocker = dictCtx.get("docker") if dictCtx else None
    if connectionDocker is not None:
        try:
            connectionDocker.fnEvictAbsentContainers(setRunning)
        except Exception:
            logging.getLogger("vaibify").debug(
                "fnEvictAbsentContainers failed during sweep",
                exc_info=True,
            )
    try:
        from . import hostIncidents
    except ImportError:
        return
    _fnEvictHostIncidentBuckets(hostIncidents, setRunning)


def _fnEvictHostIncidentBuckets(moduleHostIncidents, setRunning):
    """Drop per-container incident deques for ids no longer running."""
    dictIncidents = getattr(
        moduleHostIncidents, "_dictHostIncidents", None,
    )
    if not isinstance(dictIncidents, dict):
        return
    for sContainerId in list(dictIncidents.keys()):
        if sContainerId not in setRunning:
            moduleHostIncidents.fnEvictHostIncidentsForContainer(
                sContainerId,
            )


def _fdictGetModTimes(connectionDocker, sContainerId, listPaths):
    """Return {sAbsPath: sMtime} for each polled path that exists.

    Stats every requested path directly in a single batched exec via
    ``_fdictStatViaPathfile``. The previous design indirected through
    a parent-directory mtime cache so unchanged subtrees could skip
    per-child stat, but POSIX does not bump a directory's mtime when
    an existing child is rewritten in place — only add/remove/rename
    do. Out-of-band in-place edits (an in-container agent rewriting
    ``workflow.json`` or a step script through the ``Edit`` tool,
    ``vim :w``, ``sed -i`` on some platforms) therefore left the cache
    returning the pre-edit mtime, and the reload detector / "step
    source modified" invalidation silently no-op'd. Stat-the-children
    directly removes the silent-stale failure mode at the cost of one
    extra path-list per poll exec (still one exec round-trip).
    """
    if not listPaths:
        return {}
    return _fdictStatViaPathfile(
        connectionDocker, sContainerId, listPaths,
    )


def ftGetModTimesAndFingerprint(
    connectionDocker, sContainerId, listPaths, sFingerprintPath,
):
    """Return ``(dictModTimes, sFingerprint)`` in one exec round trip.

    Same contract as :func:`_fdictGetModTimes` plus the sha256 content
    fingerprint of ``sFingerprintPath``, collected in the same batched
    exec so the workflow reload detector's content comparison adds no
    per-tick container load.
    """
    return _ftStatAndFingerprintViaPathfile(
        connectionDocker, sContainerId, listPaths, sFingerprintPath,
    )


# ---------------------------------------------------------------------------
# Auto Archive: full-verification transition trigger
# ---------------------------------------------------------------------------

# fbStepUserApproved, fbStepTimingClean, fbStepTestsPassing are re-exported
# from vaibify.reproducibility.stepPredicates (imported at module top) so
# existing callers continue to import them from this module.


def flistStepRemoteFiles(dictWorkflow, iStepIndex, sService):
    """Return repo-relative paths for a step's files tracked on sService.

    ``sService`` is "Overleaf" or "Zenodo". Files are enumerated from
    the step's saPlotFiles + saOutputDataFiles, resolved against the repo
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
    await _fnPersistAutoArchiveZenodoDigests(
        connectionDocker, sContainerId, dictWorkflow,
        listFiles, sZenodoService,
    )
    return True


async def _fnPersistAutoArchiveZenodoDigests(
    connectionDocker, sContainerId, dictWorkflow,
    listFiles, sZenodoService,
):
    """Best-effort post-archive digest stamping for the auto-archive flow.

    Failures are logged and swallowed: the archive itself already
    succeeded, so the auto-archive return value should reflect that
    rather than the digest snapshot. A missing digest stamp leaves the
    badge as drifted, which is the honest state.
    """
    import asyncio
    try:
        dictDigests = await asyncio.to_thread(
            _fdictAutoArchiveZenodoDigests,
            connectionDocker, sContainerId, dictWorkflow, listFiles,
        )
        workflowManager.fnUpdateZenodoDigests(
            dictWorkflow, dictDigests, sZenodoService=sZenodoService,
        )
    except Exception as exc:
        logging.getLogger("vaibify").warning(
            "Auto Archive: Zenodo digest stamp failed: %s", exc,
        )


def _fdictAutoArchiveZenodoDigests(
    connectionDocker, sContainerId, dictWorkflow, listFiles,
):
    """Compute post-archive blob SHAs scoped to the workflow's repo.

    Mirrors ``syncRoutes._fdictComputePostArchiveZenodoDigests``;
    duplicated here so the auto-archive path does not import a
    route-private helper.
    """
    from . import containerGit
    sRepo = dictWorkflow.get("sProjectRepoPath", "")
    if not sRepo:
        return {}
    listRepoRel = [
        workflowManager.fsToSyncStatusKey(sPath, sRepo)
        for sPath in listFiles
    ]
    dictShas = containerGit.fdictComputeBlobShasInContainer(
        connectionDocker, sContainerId, listRepoRel, sWorkspace=sRepo,
    )
    return {
        sPath: dictShas.get(
            workflowManager.fsToSyncStatusKey(sPath, sRepo), "",
        )
        for sPath in listFiles
    }


def _ffilesForWorkflowRepo(dictWorkflow, connectionDocker, sContainerId):
    """Return the repo-file adapter for the workflow's project repo.

    ``sProjectRepoPath`` is a *container* path, so the honest adapter
    is a ``ContainerRepoFiles`` whenever a docker connection is in
    hand. Without one (legacy callers, unit tests on host clones) the
    raw path string is returned and the reproducibility entry points'
    dual-accept wraps it in a host adapter.
    """
    sProjectRepoPath = dictWorkflow.get("sProjectRepoPath", "")
    if connectionDocker is None or not sContainerId:
        return sProjectRepoPath
    from vaibify.reproducibility.repoFiles import ContainerRepoFiles
    return ContainerRepoFiles(
        connectionDocker, sContainerId, sProjectRepoPath,
    )


def _fnRefreshEnvelopeIfLevel1(
    dictWorkflow, sContainerId=None, connectionDocker=None,
):
    """Regenerate the L3 reproducibility envelope on L1 transition.

    Called from the same hook that drives auto-archive. Failures are
    logged and swallowed — the manifest is best-effort here; the
    manual Archive button remains the recovery path. The envelope is
    regenerated regardless of bAutoArchive so the local repo always
    reflects the latest verified state. ``sContainerId`` is threaded
    through so the Tier 3 environment.json (which requires the
    running container's image digest) is written, not silently
    skipped. With ``connectionDocker`` supplied the envelope is read
    and written *inside the container*, where the project repo lives.
    """
    from vaibify.reproducibility.levelGates import fbAtLeastLevel1
    filesRepo = _ffilesForWorkflowRepo(
        dictWorkflow, connectionDocker, sContainerId,
    )
    if not fbAtLeastLevel1(dictWorkflow, filesRepo):
        return
    try:
        from vaibify.reproducibility import dataArchiver
        dataArchiver.fnGenerateReproducibilityEnvelope(
            filesRepo, dictWorkflow,
            sContainerName=sContainerId,
            listHostBinaries=dictWorkflow.get("saHostBinaries"),
        )
    except Exception as error:
        logger.warning(
            "L1-transition envelope refresh failed: %s", error,
        )


def _fnDispatchEnvelopeRefreshIfPromoted(
    dictWorkflow, sContainerId, bPromoted, connectionDocker=None,
):
    """Refresh L3 envelope on the L0->L1 transition.

    Runs BEFORE the ``bAutoArchive`` gate in the caller — the local
    repo's manifest must reflect the latest verified state regardless
    of whether the researcher opted into automatic remote pushes.
    Auto-archive trap: do not collapse this into the bAutoArchive
    branch.
    """
    if bPromoted:
        _fnRefreshEnvelopeIfLevel1(
            dictWorkflow, sContainerId, connectionDocker,
        )


async def _fbDispatchOverleafAutoPush(
    connectionDocker, sContainerId, dictWorkflow, iStepIndex,
):
    """Push the step's Overleaf-tracked files; True iff anything was pushed.

    The exception handler stays in this dispatcher: a failure must
    surface in logs but never block the auto-archive caller, so the
    manual sync UI remains the recovery path.
    """
    listOverleaf = flistStepRemoteFiles(
        dictWorkflow, iStepIndex, "Overleaf",
    )
    if not listOverleaf:
        return False
    try:
        return await _fnPushOverleafForAutoArchive(
            connectionDocker, sContainerId, dictWorkflow, listOverleaf,
        )
    except Exception as error:
        logger.warning(
            "Auto Archive: Overleaf push failed for step %d: %s",
            iStepIndex, error,
        )
        return False


async def _fbDispatchZenodoAutoArchive(
    connectionDocker, sContainerId, dictWorkflow, iStepIndex,
):
    """Archive the step's Zenodo-tracked files; True iff anything was archived.

    Same swallow-and-log policy as the Overleaf dispatcher.
    """
    listZenodo = flistStepRemoteFiles(
        dictWorkflow, iStepIndex, "Zenodo",
    )
    if not listZenodo:
        return False
    try:
        return await _fnArchiveZenodoForAutoArchive(
            connectionDocker, sContainerId, dictWorkflow, listZenodo,
        )
    except Exception as error:
        logger.warning(
            "Auto Archive: Zenodo push failed for step %d: %s",
            iStepIndex, error,
        )
        return False


async def fnMaybeAutoArchive(
    connectionDocker, sContainerId, dictWorkflow, iStepIndex,
    iAICSLevelBefore,
):
    """Push step's tracked files to Overleaf/Zenodo on L1 transition.

    Fires only when this step's transition promoted the workflow to
    ``iAICSLevel >= 1`` (was below 1, is now at or above) AND the
    workflow's bAutoArchive setting is True. Pushes never block the
    caller: failures are logged and the manual sync UI remains the
    recovery path. Returns True when at least one remote was pushed
    (callers use this to know whether to refresh badges).

    Also refreshes the L3 reproducibility envelope (MANIFEST.sha256 +
    requirements.lock + .vaibify/environment.json) when this step's
    transition leaves the workflow at L1 or higher. The refresh runs
    independently of bAutoArchive so the local repo's manifest always
    reflects the latest fully-verified state.
    """
    from vaibify.reproducibility.levelGates import fiAICSLevel
    filesRepo = _ffilesForWorkflowRepo(
        dictWorkflow, connectionDocker, sContainerId,
    )
    iLevelNow = fiAICSLevel(dictWorkflow, filesRepo)
    bPromoted = iAICSLevelBefore < 1 <= iLevelNow
    _fnDispatchEnvelopeRefreshIfPromoted(
        dictWorkflow, sContainerId, bPromoted, connectionDocker,
    )
    if not dictWorkflow.get("bAutoArchive") or not bPromoted:
        return False
    listSteps = dictWorkflow.get("listSteps", [])
    if iStepIndex < 0 or iStepIndex >= len(listSteps):
        return False
    bAnyPushed = await _fbDispatchOverleafAutoPush(
        connectionDocker, sContainerId, dictWorkflow, iStepIndex,
    )
    bAnyPushed |= await _fbDispatchZenodoAutoArchive(
        connectionDocker, sContainerId, dictWorkflow, iStepIndex,
    )
    return bAnyPushed
