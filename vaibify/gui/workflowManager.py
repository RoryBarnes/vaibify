"""Load, validate, and CRUD operations on workflow.json."""

import copy
import json
import os
import posixpath
import re
import shlex

from . import stateManager, workflowMigrations
from .workflowMigrations import (
    fbMigrateModifiedFilesToRepoRelative,
    fdictMigrateTestFormat,
    fnMigrateArchiveToTracking,
    fnMigrateRunEnabledKey,
    fnNormalizeSceneReferences,
)

S_STEP_REF_PATTERN = r"\{Step(\d+)\.([^}]+)\}"
S_VAIBIFY_WORKFLOWS_SUFFIX = "/.vaibify/workflows/"

__all__ = [
    "fbStepRequiresTests",
    "fbValidateWorkflow",
    "fsDescribeValidationFailure",
    "fsDeriveProjectRepoPathFromWorkflow",
    "fnAttachComputedTrackedPaths",
    "fdictAutoDetectScripts",
    "fdictBuildDirectDependencies",
    "fdictBuildImplicitDependencies",
    "fdictBuildDownstreamMap",
    "fdictBuildGlobalVariables",
    "fdictBuildStepDirectoryMap",
    "fdictBuildStepVariables",
    "fdictCreateStep",
    "fdictGetStep",
    "fdictGetSyncStatus",
    "fdictGetZenodoMetadata",
    "fdictInitializeZenodoMetadata",
    "fdictLoadWorkflowFromContainer",
    "fdictLookupSyncEntry",
    "fdictMigrateTestFormat",
    "fnNormalizeSceneReferences",
    "flistBuildTestCommands",
    "flistCollectArchiveDataFiles",
    "flistCollectArchiveFiles",
    "flistCollectArchivePlots",
    "flistCollectSupportingDataFiles",
    "flistCollectSupportingFiles",
    "flistCollectSupportingPlots",
    "flistExtractOutputFiles",
    "flistExtractStepNames",
    "flistExtractStepScripts",
    "flistFilterFigureFiles",
    "flistFindWorkflowsInContainer",
    "flistResolveOutputFiles",
    "flistResolveTestCommands",
    "flistValidateOutputFilePaths",
    "flistValidateReferences",
    "flistValidateStepDirectories",
    "fnDeleteStep",
    "fnInsertStep",
    "fnMigrateArchiveToTracking",
    "fbMigrateModifiedFilesToRepoRelative",
    "fnRenumberAllReferences",
    "fnReorderStep",
    "fnSaveWorkflowToContainer",
    "fnSetServiceTracking",
    "fnSetZenodoMetadata",
    "fnUpdateOverleafDigests",
    "fnUpdateStep",
    "fnUpdateSyncStatus",
    "fnUpdateZenodoDigests",
    "fsCamelCaseDirectory",
    "fsGetFileCategory",
    "fsGetPlotCategory",
    "fsResolveCommand",
    "fsResolveStepWorkdir",
    "fsResolveVariables",
    "fsTestsDirectory",
    "fsToSyncStatusKey",
]

DEFAULT_SEARCH_ROOT = "/workspace"

VAIBIFY_DIRECTORY = ".vaibify"
VAIBIFY_WORKFLOWS_DIR = ".vaibify/workflows"
VAIBIFY_LOGS_DIR = ".vaibify/logs"

T_REQUIRED_WORKFLOW_KEYS = ("sPlotDirectory", "listSteps")
T_REQUIRED_STEP_KEYS = ("sName", "sDirectory", "saPlotCommands", "saPlotFiles")


def flistFindWorkflowsInContainer(
    connectionDocker, sContainerId, sSearchRoot=None
):
    """Search for workflow.json files inside git-tracked project repos.

    Candidates outside a git work tree are dropped — every valid
    vaibify workflow must live inside the project repo it belongs to.
    Each returned entry carries the auto-detected
    ``sProjectRepoPath`` so downstream code never has to re-probe.
    """
    if sSearchRoot is None:
        sSearchRoot = DEFAULT_SEARCH_ROOT
    sCommand = (
        f"find {sSearchRoot} -maxdepth 4"
        f" -path '*/.vaibify/workflows/*.json'"
        f" -type f 2>/dev/null"
    )
    iExitCode, sOutput = connectionDocker.ftResultExecuteCommand(
        sContainerId, sCommand
    )
    listCandidates = [
        sLine.strip() for sLine in sOutput.splitlines()
        if sLine.strip().endswith(".json")
    ]
    if not listCandidates:
        return []
    dictRepoByPath = _fdictDetectReposForCandidates(
        connectionDocker, sContainerId, listCandidates,
    )
    listResults = []
    for sPath in listCandidates:
        sRepo = dictRepoByPath.get(sPath, "")
        if not sRepo:
            continue
        sName = _fsReadWorkflowName(
            connectionDocker, sContainerId, sPath,
        )
        listResults.append({
            "sPath": sPath,
            "sName": sName,
            "sRepoName": posixpath.basename(sRepo),
            "sProjectRepoPath": sRepo,
        })
    return sorted(listResults, key=lambda d: d["sName"])


def _fdictDetectReposForCandidates(
    connectionDocker, sContainerId, listCandidatePaths,
):
    """Return {workflow-path: project-repo-path} via a single docker exec.

    Paths whose parent directory is not inside a git work tree are
    omitted from the result, so only workflows under version control
    survive discovery.
    """
    sPathsJson = json.dumps(listCandidatePaths)
    sScript = (
        "import json, os, subprocess, sys\n"
        "paths = json.loads(sys.stdin.read())\n"
        "out = {}\n"
        "for p in paths:\n"
        "    d = os.path.dirname(p)\n"
        "    if not d: continue\n"
        "    r = subprocess.run(\n"
        "        ['git', '-C', d, 'rev-parse', '--show-toplevel'],\n"
        "        capture_output=True, text=True, timeout=5,\n"
        "    )\n"
        "    if r.returncode == 0:\n"
        "        out[p] = r.stdout.strip()\n"
        "print(json.dumps(out))\n"
    )
    sCommand = (
        "python3 -c " + shlex.quote(sScript) +
        " <<< " + shlex.quote(sPathsJson)
    )
    iExit, sOutput = connectionDocker.ftResultExecuteCommand(
        sContainerId, sCommand,
    )
    if iExit != 0:
        return {}
    try:
        return json.loads((sOutput or "").strip().splitlines()[-1])
    except (ValueError, IndexError):
        return {}


def _fsReadWorkflowName(connectionDocker, sContainerId, sPath):
    """Read sWorkflowName from a workflow JSON file in the container."""
    try:
        baContent = connectionDocker.fbaFetchFile(sContainerId, sPath)
        dictWorkflow = json.loads(baContent.decode("utf-8"))
        return dictWorkflow.get(
            "sWorkflowName", posixpath.basename(sPath)
        )
    except Exception:
        return posixpath.basename(sPath)


def fdictLoadWorkflowFromContainer(
    connectionDocker, sContainerId, sWorkflowPath=None
):
    """Fetch, migrate, validate, and parse workflow.json.

    Also reads the sibling ``state.json`` (or bootstraps from
    committed test-markers when absent) and merges machine-local
    runtime state into the returned dict so route handlers and the
    frontend continue to see one merged shape.
    """
    from .pipelineUtils import fnAttachStepLabels
    if sWorkflowPath is None:
        listWorkflows = flistFindWorkflowsInContainer(
            connectionDocker, sContainerId
        )
        if not listWorkflows:
            raise FileNotFoundError(
                "No workflow.json found under search root"
            )
        sWorkflowPath = listWorkflows[0]["sPath"]
    baContent = connectionDocker.fbaFetchFile(sContainerId, sWorkflowPath)
    dictWorkflow = json.loads(baContent.decode("utf-8"))
    sRepoPath = fsDeriveProjectRepoPathFromWorkflow(sWorkflowPath)
    workflowMigrations.fnApplyMigrations(
        dictWorkflow, sProjectRepoPath=sRepoPath,
    )
    sFailure = fsDescribeValidationFailure(dictWorkflow)
    if sFailure:
        raise ValueError(
            f"Invalid workflow.json at {sWorkflowPath}: {sFailure}"
        )
    _fnLoadAndMergeState(
        connectionDocker, sContainerId, dictWorkflow, sRepoPath,
    )
    fnAttachStepLabels(dictWorkflow)
    fnAttachComputedTrackedPaths(dictWorkflow)
    return dictWorkflow


def _fnLoadAndMergeState(
    connectionDocker, sContainerId, dictWorkflow, sRepoPath,
):
    """Load .vaibify/state.json (or bootstrap from markers) and merge in.

    Attaches ``dictStateLoadNotice`` to ``dictWorkflow`` when the load
    took the recovery path (``.bak`` fallback or marker-bootstrap
    after corruption) so the connect response can surface a toast to
    the user. The notice is a transient, non-persisted field; see
    ``_fdictStripComputedFields``.
    """
    if not sRepoPath:
        return
    sStatePath = stateManager.fsStatePathFromRepo(sRepoPath)
    dictState, sStatus = stateManager.ftLoadStateWithStatus(
        connectionDocker, sContainerId, sStatePath,
    )
    if dictState is None:
        dictState = stateManager.fdictBootstrapStateFromMarkers(
            connectionDocker, sContainerId, dictWorkflow, sRepoPath,
        )
        stateManager.fnSaveStateToContainer(
            connectionDocker, sContainerId, sStatePath, dictState,
        )
    dictNotice = _fdictBuildStateLoadNotice(sStatus)
    if dictNotice:
        dictWorkflow["dictStateLoadNotice"] = dictNotice
    stateManager.fnMergeStateIntoWorkflow(dictWorkflow, dictState)
    stateManager.fnEnsureVaibifyGitignore(
        connectionDocker, sContainerId, sRepoPath,
    )


def _fdictBuildStateLoadNotice(sStatus):
    """Return a transient toast payload for non-clean state loads.

    Returns ``None`` for the silent paths (clean load, fresh checkout
    bootstrap) so the frontend only shows the toast when something
    actually deserves the user's attention — namely, that their last
    save did not land cleanly and was recovered from a checkpoint or
    rebuilt entirely.
    """
    if sStatus == "loaded-from-bak":
        return {
            "sLevel": "warning",
            "sMessage": (
                "state.json was missing or unreadable; "
                "vaibify recovered the previous good state from "
                ".vaibify/state.json.bak. Verify your test and "
                "user-acknowledgement statuses before continuing."
            ),
        }
    if sStatus == "corrupted":
        return {
            "sLevel": "warning",
            "sMessage": (
                "state.json and its .bak checkpoint both failed to "
                "parse; verifications were rebuilt from test "
                "markers and by-eye 'researcher passed' statuses "
                "were lost. The corrupt files are quarantined at "
                ".vaibify/state.json.corrupted-<timestamp> for "
                "hand-recovery."
            ),
        }
    return None


def fsDeriveProjectRepoPathFromWorkflow(sWorkflowPath):
    """Return the project repo root that contains a workflow file.

    By contract every vaibify workflow lives at
    ``<sProjectRepoPath>/.vaibify/workflows/<name>.json``. Stripping
    that suffix yields the repo root. Returns ``""`` when the path
    does not match (callers should treat that as no migration
    context, not an error).
    """
    if not sWorkflowPath:
        return ""
    iSplit = sWorkflowPath.find(S_VAIBIFY_WORKFLOWS_SUFFIX)
    if iSplit <= 0:
        return ""
    return sWorkflowPath[:iSplit]


def fbValidateWorkflow(dictWorkflow):
    """Return True when all required keys and step structures exist."""
    return fsDescribeValidationFailure(dictWorkflow) == ""


def fsDescribeValidationFailure(dictWorkflow):
    """Return a human-readable diagnostic, or empty string when valid.

    Names the failing check so toast messages can be specific
    instead of pointing at a 900-line file.
    """
    for sKey in T_REQUIRED_WORKFLOW_KEYS:
        if sKey not in dictWorkflow:
            return f"missing required top-level key '{sKey}'"
    if not isinstance(dictWorkflow.get("listSteps"), list):
        return "'listSteps' is not a list"
    for iIndex, dictStep in enumerate(dictWorkflow["listSteps"]):
        sLabel = f"Step{iIndex + 1:02d}"
        for sField in T_REQUIRED_STEP_KEYS:
            if sField not in dictStep:
                return f"{sLabel} is missing required field '{sField}'"
    listOutWarnings = flistValidateOutputFilePaths(dictWorkflow)
    if listOutWarnings:
        return listOutWarnings[0]
    listDirWarnings = flistValidateStepDirectories(dictWorkflow)
    if listDirWarnings:
        return listDirWarnings[0]
    return ""


def flistValidateOutputFilePaths(dictWorkflow):
    """Return warnings for output paths that leave the project repo.

    Scans ``saOutputFiles``, ``saDataFiles``, and ``saPlotFiles`` on
    every step. Absolute paths and ``..``-escaping paths are flagged;
    template-bearing paths (containing ``{``) are skipped because
    they are resolved against the global variables dict at run time.
    """
    listWarnings = []
    for iIndex, dictStep in enumerate(dictWorkflow.get("listSteps", [])):
        sLabel = f"Step{iIndex + 1:02d}"
        sDirectory = dictStep.get("sDirectory", "")
        for sKey in ("saOutputFiles", "saDataFiles", "saPlotFiles"):
            for sPath in dictStep.get(sKey, []):
                sWarning = _fsCheckOutputPathBoundary(
                    sPath, sDirectory, sLabel, sKey,
                )
                if sWarning:
                    listWarnings.append(sWarning)
    return listWarnings


def flistValidateStepDirectories(dictWorkflow):
    """Return warnings for step sDirectory values that leave the project repo.

    Absolute paths (``/``-prefixed) and ``..``-escaping paths are
    flagged; template-bearing paths (containing ``{``) are skipped
    because they are resolved against the global variables dict at
    run time.
    """
    listWarnings = []
    for iIndex, dictStep in enumerate(dictWorkflow.get("listSteps", [])):
        sLabel = f"Step{iIndex + 1:02d}"
        sDirectory = dictStep.get("sDirectory", "")
        sWarning = _fsCheckStepDirectoryBoundary(sDirectory, sLabel)
        if sWarning:
            listWarnings.append(sWarning)
    return listWarnings


def _fsCheckStepDirectoryBoundary(sDirectory, sLabel):
    """Return a warning for one sDirectory leaving the project repo, or ''."""
    if not isinstance(sDirectory, str) or not sDirectory:
        return ""
    if "{" in sDirectory:
        return ""
    if posixpath.isabs(sDirectory):
        return (
            f"{sLabel}: sDirectory '{sDirectory}' must be repo-relative, "
            f"not absolute"
        )
    sNorm = posixpath.normpath(sDirectory)
    if sNorm == ".." or sNorm.startswith("../"):
        return (
            f"{sLabel}: sDirectory '{sDirectory}' escapes the project "
            f"repo (resolves to '{sNorm}')"
        )
    return ""


def _fsCheckOutputPathBoundary(sPath, sDirectory, sLabel, sKey):
    """Return a warning for one path leaving the project repo, or ''."""
    if not isinstance(sPath, str) or "{" in sPath:
        return ""
    if posixpath.isabs(sPath):
        return (
            f"{sLabel}: {sKey} entry '{sPath}' must be repo-relative, "
            f"not absolute"
        )
    sJoined = posixpath.normpath(posixpath.join(sDirectory or "", sPath))
    if sJoined == ".." or sJoined.startswith("../"):
        return (
            f"{sLabel}: {sKey} entry '{sPath}' escapes the project "
            f"repo (resolves to '{sJoined}')"
        )
    return ""


def fsResolveVariables(sTemplate, dictVariables):
    """Replace {name} tokens in sTemplate with values from dictVariables."""

    def fnReplace(resultMatch):
        sToken = resultMatch.group(1)
        if sToken in dictVariables:
            return str(dictVariables[sToken])
        return resultMatch.group(0)

    return re.sub(r"\{([^}]+)\}", fnReplace, sTemplate)


def fdictBuildGlobalVariables(dictWorkflow, sWorkflowPath):
    """Build the global variable dict from workflow.json top-level keys."""
    sWorkflowDirectory = posixpath.dirname(sWorkflowPath)
    sRepoRoot = sWorkflowDirectory
    if "/.vaibify" in sRepoRoot:
        sRepoRoot = sRepoRoot[:sRepoRoot.index("/.vaibify")]
    sPlotDirectory = dictWorkflow.get("sPlotDirectory", "Plot")
    if not posixpath.isabs(sPlotDirectory):
        sPlotDirectory = posixpath.join(sRepoRoot, sPlotDirectory)
    return {
        "sPlotDirectory": sPlotDirectory,
        "sRepoRoot": sRepoRoot,
        "iNumberOfCores": dictWorkflow.get("iNumberOfCores", -1),
        "sFigureType": dictWorkflow.get("sFigureType", "pdf").lower(),
    }


def flistResolveOutputFiles(dictStep, dictVariables):
    """Return output file paths with template variables resolved."""
    listResolved = []
    for sPath in dictStep.get("saDataFiles", []):
        listResolved.append(fsResolveVariables(sPath, dictVariables))
    for sPath in dictStep.get("saPlotFiles", []):
        listResolved.append(fsResolveVariables(sPath, dictVariables))
    return listResolved


def flistExtractStepNames(dictWorkflow):
    """Return a list of step summary dicts."""
    listSteps = []
    for iIndex, dictStep in enumerate(dictWorkflow["listSteps"]):
        listSteps.append(
            {
                "iIndex": iIndex,
                "iNumber": iIndex + 1,
                "sName": dictStep["sName"],
                "bRunEnabled": dictStep.get("bRunEnabled", True),
                "bPlotOnly": dictStep.get("bPlotOnly", True),
                "sDirectory": dictStep["sDirectory"],
            }
        )
    return listSteps


T_VERIFICATION_STATES = ("untested", "passed", "failed", "error")


def fbStepRequiresTests(dictStep):
    """Return True when data commands exist but no test commands."""
    bHasData = len(dictStep.get("saDataCommands", [])) > 0
    bHasTests = len(dictStep.get("saTestCommands", [])) > 0
    return bHasData and not bHasTests


def fdictCreateStep(
    sName,
    sDirectory,
    bPlotOnly=True,
    bInteractive=False,
    saDataCommands=None,
    saDataFiles=None,
    saTestCommands=None,
    saPlotCommands=None,
    saPlotFiles=None,
):
    """Return a new step dictionary with validated fields."""
    return {
        "sName": sName,
        "sDirectory": sDirectory,
        "bRunEnabled": True,
        "bPlotOnly": bPlotOnly,
        "bInteractive": bInteractive,
        "saDataCommands": saDataCommands if saDataCommands else [],
        "saDataFiles": saDataFiles if saDataFiles else [],
        "saTestCommands": saTestCommands if saTestCommands else [],
        "saPlotCommands": saPlotCommands if saPlotCommands else [],
        "saPlotFiles": saPlotFiles if saPlotFiles else [],
        "dictTests": {
            "dictQualitative": {"saCommands": [], "sFilePath": ""},
            "dictQuantitative": {
                "saCommands": [], "sFilePath": "", "sStandardsPath": "",
            },
            "dictIntegrity": {"saCommands": [], "sFilePath": ""},
            "listUserTests": [],
        },
        "dictVerification": {
            "sUnitTest": "untested",
            "sUser": "untested",
            "sQualitative": "untested",
            "sQuantitative": "untested",
            "sIntegrity": "untested",
        },
        "saDependencies": [],
        "dictRunStats": {},
    }


def fdictGetStep(dictWorkflow, iStepIndex):
    """Return a copy of the step at iStepIndex."""
    if iStepIndex < 0 or iStepIndex >= len(dictWorkflow["listSteps"]):
        raise IndexError(f"Step index {iStepIndex} out of range")
    return dict(dictWorkflow["listSteps"][iStepIndex])


def fsRemapStepReferences(sText, fnRemap):
    """Apply fnRemap to all {StepNN.variable} tokens in sText."""

    def fnReplace(resultMatch):
        iOldNumber = int(resultMatch.group(1))
        sVariable = resultMatch.group(2)
        iNewNumber = fnRemap(iOldNumber)
        if iNewNumber == iOldNumber:
            return resultMatch.group(0)
        return "{" + f"Step{iNewNumber:02d}" + "." + sVariable + "}"

    return re.sub(S_STEP_REF_PATTERN, fnReplace, sText)


def fnRenumberAllReferences(dictWorkflow, fnRemap):
    """Update all {StepNN.*} references in every step per fnRemap."""
    for dictStep in dictWorkflow["listSteps"]:
        for sKey in ("saDataCommands", "saTestCommands",
                     "saPlotCommands", "saPlotFiles",
                     "saDependencies", "saSetupCommands",
                     "saCommands", "saOutputFiles"):
            if sKey in dictStep and dictStep[sKey]:
                dictStep[sKey] = [
                    fsRemapStepReferences(sItem, fnRemap)
                    for sItem in dictStep[sKey]
                ]


def fnInsertStep(dictWorkflow, iPosition, dictStep):
    """Insert a step at iPosition, renumbering downstream references."""

    def fnRemap(iStepNumber):
        if iStepNumber >= iPosition + 1:
            return iStepNumber + 1
        return iStepNumber

    fnRenumberAllReferences(dictWorkflow, fnRemap)
    dictWorkflow["listSteps"].insert(iPosition, dictStep)


def fnUpdateStep(dictWorkflow, iStepIndex, dictUpdates):
    """Update step at iStepIndex with dictUpdates."""
    if iStepIndex < 0 or iStepIndex >= len(dictWorkflow["listSteps"]):
        raise IndexError(f"Step index {iStepIndex} out of range")
    dictStep = dictWorkflow["listSteps"][iStepIndex]
    for sKey, value in dictUpdates.items():
        dictStep[sKey] = value


def fnDeleteStep(dictWorkflow, iStepIndex):
    """Remove step at iStepIndex, renumbering references."""
    if iStepIndex < 0 or iStepIndex >= len(dictWorkflow["listSteps"]):
        raise IndexError(f"Step index {iStepIndex} out of range")
    iDeletedNumber = iStepIndex + 1

    def fnRemap(iStepNumber):
        if iStepNumber > iDeletedNumber:
            return iStepNumber - 1
        return iStepNumber

    dictWorkflow["listSteps"].pop(iStepIndex)
    fnRenumberAllReferences(dictWorkflow, fnRemap)


def _fnValidateReorderIndices(iFromIndex, iToIndex, iMaxIndex):
    """Raise IndexError if either reorder index is out of range."""
    if iFromIndex < 0 or iFromIndex > iMaxIndex:
        raise IndexError(f"From index {iFromIndex} out of range")
    if iToIndex < 0 or iToIndex > iMaxIndex:
        raise IndexError(f"To index {iToIndex} out of range")


def _fiRemapReorder(iStepNumber, iFromNumber, iFromIndex, iToIndex):
    """Return the remapped step number for a reorder operation."""
    if iStepNumber == iFromNumber:
        return iToIndex + 1
    if iFromIndex < iToIndex:
        if iFromNumber < iStepNumber <= iToIndex + 1:
            return iStepNumber - 1
    elif iFromIndex > iToIndex:
        if iToIndex + 1 <= iStepNumber < iFromNumber:
            return iStepNumber + 1
    return iStepNumber


def fnReorderStep(dictWorkflow, iFromIndex, iToIndex):
    """Move a step from iFromIndex to iToIndex, renumbering references."""
    listSteps = dictWorkflow["listSteps"]
    _fnValidateReorderIndices(iFromIndex, iToIndex, len(listSteps) - 1)
    iFromNumber = iFromIndex + 1

    def fnRemap(iStepNumber):
        return _fiRemapReorder(
            iStepNumber, iFromNumber, iFromIndex, iToIndex
        )

    dictStep = listSteps.pop(iFromIndex)
    listSteps.insert(iToIndex, dictStep)
    fnRenumberAllReferences(dictWorkflow, fnRemap)


def fnAttachComputedTrackedPaths(dictWorkflow):
    """Attach derived saStepScripts and saTestStandards to each step.

    Both arrays carry repo-relative paths produced by the canonical
    ``stateContract`` helpers, the same lists that drive
    ``flistCanonicalTrackedFiles`` and the badge dictionary keys. The
    frontend can then render per-file remote-sync badges whose lookup
    keys match the backend's. The fields are transient and are
    stripped on save by ``_fdictStripComputedFields``.

    ``stateContract`` imports ``workflowManager`` at module level, so
    the import must be deferred to break the cycle.
    """
    from . import stateContract
    for dictStep in dictWorkflow.get("listSteps", []):
        dictStep["saStepScripts"] = list(
            stateContract._flistStepScriptRepoPaths(dictStep)
        )
        dictStep["saTestStandards"] = list(
            stateContract._flistStepStandardsRepoPaths(dictStep)
        )


def _fdictStripComputedFields(dictWorkflow):
    """Return a deep copy with transient fields removed from steps.

    ``dictStateLoadNotice`` is a one-shot toast payload attached
    during the connect-time recovery path; it must not leak into
    the persisted workflow.json or state.json. ``saStepScripts`` and
    ``saTestStandards`` are derived per-step badge-rendering caches
    and are also non-persistent.
    """
    dictClean = copy.deepcopy(dictWorkflow)
    dictClean.pop("dictStateLoadNotice", None)
    for dictStep in dictClean.get("listSteps", []):
        dictStep.pop("saSourceCodeDeps", None)
        dictStep.pop("saStepScripts", None)
        dictStep.pop("saTestStandards", None)
    return dictClean


def fnSaveWorkflowToContainer(
    connectionDocker, sContainerId, dictWorkflow, sWorkflowPath=None
):
    """Serialize the merged workflow dict and persist it.

    Splits the in-memory dict between ``workflow.json`` (declarative
    fields) and ``.vaibify/state.json`` (per-machine runtime state)
    before writing. Callers continue to mutate one merged dict; the
    split is invisible upstream.
    """
    from .pipelineUtils import fnAttachStepLabels
    if sWorkflowPath is None:
        raise ValueError("sWorkflowPath is required for saving")
    fnAttachStepLabels(dictWorkflow)
    workflowMigrations.fnStampCurrentVersion(dictWorkflow)
    dictClean = _fdictStripComputedFields(dictWorkflow)
    dictDeclarative, dictState = stateManager.ftSplitMergedDict(
        dictClean,
    )
    sJson = json.dumps(dictDeclarative, indent=2) + "\n"
    connectionDocker.fnWriteFile(
        sContainerId, sWorkflowPath, sJson.encode("utf-8")
    )
    sRepoPath = fsDeriveProjectRepoPathFromWorkflow(sWorkflowPath)
    sStatePath = stateManager.fsStatePathFromRepo(sRepoPath)
    if sStatePath:
        stateManager.fnSaveStateToContainer(
            connectionDocker, sContainerId, sStatePath, dictState,
        )
        stateManager.fnEnsureVaibifyGitignore(
            connectionDocker, sContainerId, sRepoPath,
        )


def fsetExtractStepReferences(sText):
    """Return all {StepNN.variable} tokens found in sText as tuples."""
    return set(re.findall(S_STEP_REF_PATTERN, sText))


def fdictBuildStemRegistry(dictWorkflow):
    """Map each StepNN.stem to the step that produces it."""
    dictRegistry = {}
    for iIndex, dictStep in enumerate(dictWorkflow["listSteps"]):
        iNumber = iIndex + 1
        listAllOutputs = (
            dictStep.get("saDataFiles", [])
            + dictStep.get("saPlotFiles", [])
            + dictStep.get("saOutputFiles", [])
        )
        for sOutputFile in listAllOutputs:
            sBasename = posixpath.basename(sOutputFile)
            sStem = posixpath.splitext(sBasename)[0]
            dictRegistry[f"Step{iNumber:02d}.{sStem}"] = iNumber
    return dictRegistry


def flistCollectReferenceStrings(dictStep):
    """Return all strings that may contain {StepNN.variable} references."""
    listStrings = []
    for sKey in ("saDataCommands", "saTestCommands", "saPlotCommands",
                 "saSetupCommands", "saCommands"):
        listStrings.extend(dictStep.get(sKey, []))
    listStrings.extend(dictStep.get("saDependencies", []))
    return listStrings


def flistValidateReferences(dictWorkflow):
    """Return a list of warnings about cross-step reference problems."""
    dictRegistry = fdictBuildStemRegistry(dictWorkflow)
    listWarnings = []

    for iIndex, dictStep in enumerate(dictWorkflow["listSteps"]):
        iNumber = iIndex + 1
        sStepLabel = f"Step{iNumber:02d}"

        for sText in flistCollectReferenceStrings(dictStep):
            _fnCheckCommandReferences(
                sText, sStepLabel, iNumber,
                dictWorkflow, dictRegistry, listWarnings,
            )

    return listWarnings


def _fsClassifyReference(iRefNumber, sRefKey, iNumber, iStepCount, dictRegistry):
    """Return a warning suffix string or empty string if reference is valid."""
    if iRefNumber > iStepCount:
        return "points beyond the last step"
    if sRefKey not in dictRegistry:
        return f"has no matching output file in Step{iRefNumber:02d}"
    if iRefNumber >= iNumber:
        return "points to a later step (circular dependency)"
    return ""


def _fnCheckCommandReferences(
    sCommand, sStepLabel, iNumber,
    dictWorkflow, dictRegistry, listWarnings,
):
    """Append warnings for invalid references in a single command."""
    iStepCount = len(dictWorkflow["listSteps"])
    for sRefNumber, sRefVariable in fsetExtractStepReferences(sCommand):
        iRefNumber = int(sRefNumber)
        sRefKey = f"Step{iRefNumber:02d}.{sRefVariable}"
        sSuffix = _fsClassifyReference(
            iRefNumber, sRefKey, iNumber, iStepCount, dictRegistry
        )
        if sSuffix:
            listWarnings.append(
                f"{sStepLabel}: reference {{{sRefKey}}} {sSuffix}"
            )


def fdictBuildStepVariables(dictWorkflow, dictGlobalVars):
    """Map StepNN.stem to resolved absolute output paths."""
    dictStepVars = {}
    for iIndex, dictStep in enumerate(dictWorkflow["listSteps"]):
        iNumber = iIndex + 1
        sStepDirectory = dictStep.get("sDirectory", "")
        listAllOutputs = (
            dictStep.get("saDataFiles", [])
            + dictStep.get("saPlotFiles", [])
            + dictStep.get("saOutputFiles", [])
        )
        for sOutputFile in listAllOutputs:
            sResolved = fsResolveVariables(sOutputFile, dictGlobalVars)
            sAbsPath = _fsResolveStepOutputPath(
                sResolved, sStepDirectory, dictGlobalVars
            )
            sStem = posixpath.splitext(posixpath.basename(sAbsPath))[0]
            dictStepVars[f"Step{iNumber:02d}.{sStem}"] = sAbsPath
    return dictStepVars


def _fsResolveStepOutputPath(sResolvedFile, sStepDirectory, dictGlobalVars):
    """Return an absolute path for a step output file."""
    if posixpath.isabs(sResolvedFile):
        return sResolvedFile
    sResolvedDir = fsResolveVariables(sStepDirectory, dictGlobalVars)
    sRepoRoot = dictGlobalVars.get("sRepoRoot", "")
    return posixpath.join(sRepoRoot, sResolvedDir, sResolvedFile)


def fsResolveCommand(sCommand, dictVariables):
    """Resolve template variables in a command string."""
    return fsResolveVariables(sCommand, dictVariables)


def fsResolveStepWorkdir(sStepDirectory, dictVariables):
    """Return absolute container workdir for a step's sDirectory."""
    if not sStepDirectory or posixpath.isabs(sStepDirectory):
        return sStepDirectory
    sRepoRoot = dictVariables.get("sRepoRoot", "") if dictVariables else ""
    if not sRepoRoot:
        return sStepDirectory
    return posixpath.join(sRepoRoot, sStepDirectory)


def flistFilterFigureFiles(listOutputPaths):
    """Return only paths ending in figure extensions."""
    setFigureExtensions = {".pdf", ".png", ".jpg", ".jpeg", ".svg"}
    listFigures = []
    for sPath in listOutputPaths:
        sExtension = posixpath.splitext(sPath)[1].lower()
        if sExtension in setFigureExtensions:
            listFigures.append(sPath)
    return listFigures


def flistExtractOutputFiles(dictStep):
    """Return list of output file paths for a step."""
    return list(dictStep.get("saPlotFiles", []))


# ---------------------------------------------------------------------------
# File categorization — archive (necessary) vs. supporting
# ---------------------------------------------------------------------------


def fsGetFileCategory(dictStep, sFilePath):
    """Return 'archive' or 'supporting' for a data or plot file."""
    dictPlot = dictStep.get("dictPlotFileCategories", {})
    if sFilePath in dictPlot:
        return dictPlot[sFilePath]
    dictData = dictStep.get("dictDataFileCategories", {})
    if sFilePath in dictData:
        return dictData[sFilePath]
    return "archive"


def fsGetPlotCategory(dictStep, sFilePath):
    """Return 'archive' or 'supporting' for a plot file."""
    dictCategories = dictStep.get("dictPlotFileCategories", {})
    return dictCategories.get(sFilePath, "archive")


def flistCollectArchiveFiles(dictWorkflow, sArrayKey):
    """Return files categorized as archive from a given array key."""
    listArchive = []
    for dictStep in dictWorkflow.get("listSteps", []):
        for sFile in dictStep.get(sArrayKey, []):
            if fsGetFileCategory(dictStep, sFile) == "archive":
                listArchive.append(sFile)
    return listArchive


def flistCollectArchivePlots(dictWorkflow):
    """Return all plot files categorized as archive."""
    return flistCollectArchiveFiles(dictWorkflow, "saPlotFiles")


def flistCollectArchiveDataFiles(dictWorkflow):
    """Return all data files categorized as archive."""
    return flistCollectArchiveFiles(dictWorkflow, "saDataFiles")


def flistCollectSupportingFiles(dictWorkflow, sArrayKey):
    """Return files categorized as supporting from a given array key."""
    listSupporting = []
    for dictStep in dictWorkflow.get("listSteps", []):
        for sFile in dictStep.get(sArrayKey, []):
            if fsGetFileCategory(dictStep, sFile) == "supporting":
                listSupporting.append(sFile)
    return listSupporting


def flistCollectSupportingPlots(dictWorkflow):
    """Return all plot files categorized as supporting."""
    return flistCollectSupportingFiles(dictWorkflow, "saPlotFiles")


def flistCollectSupportingDataFiles(dictWorkflow):
    """Return all data files categorized as supporting."""
    return flistCollectSupportingFiles(dictWorkflow, "saDataFiles")


# ---------------------------------------------------------------------------
# Step-to-directory mapping
# ---------------------------------------------------------------------------


def fsCamelCaseDirectory(sStepName):
    """Convert a step name to a camelCase directory name."""
    import re
    listWords = sStepName.split()
    listCapitalized = [
        sWord.capitalize() for sWord in listWords
    ]
    sJoined = "".join(listCapitalized)
    return re.sub(r"[^a-zA-Z0-9]", "", sJoined)


def flistExtractStepScripts(dictStep):
    """Extract .py script tokens from data and plot commands.

    Delegates to ``manifestPaths.flistExtractStepScripts`` so the
    extractor is shared across the manifest writer, the canonical
    tracked-files set, the Zenodo scripts archive flow, and the
    scripts-route response. Previously this returned ``listTokens[1]``
    unconditionally, so ``python -u foo.py`` would surface ``-u`` as a
    "script" — breaking syncDispatcher's per-file copy commands and
    leaking ``-u`` into scriptRoutes' GUI list.
    """
    from vaibify.reproducibility.manifestPaths import (
        flistExtractStepScripts as _flist,
    )
    return _flist(dictStep)


def fdictAutoDetectScripts(listFileNames):
    """Classify files by data*/plot* prefix convention."""
    listDataScripts = []
    listPlotScripts = []
    for sName in listFileNames:
        sLower = sName.lower()
        if not sLower.endswith(".py"):
            continue
        sBase = os.path.basename(sName)
        if sBase.lower().startswith("data"):
            listDataScripts.append(sName)
        elif sBase.lower().startswith("plot"):
            listPlotScripts.append(sName)
    return {
        "listDataScripts": listDataScripts,
        "listPlotScripts": listPlotScripts,
    }


def fdictBuildStepDirectoryMap(dictWorkflow):
    """Map step indices to camelCase directory names."""
    dictMap = {}
    for iIndex, dictStep in enumerate(
        dictWorkflow.get("listSteps", [])
    ):
        sName = dictStep.get("sName", f"Step{iIndex + 1}")
        dictMap[iIndex] = fsCamelCaseDirectory(sName)
    return dictMap


# ---------------------------------------------------------------------------
# Sync status tracking
# ---------------------------------------------------------------------------


def fdictGetSyncStatus(dictWorkflow):
    """Return the sync status dict, defaulting to empty."""
    return dictWorkflow.get("dictSyncStatus", {})


def fdictInitializeSyncEntry():
    """Return a fresh sync entry with all services unsynced.

    GitHub sync state is derived from git itself (commit + push),
    so no separate ``sGithubLastPushedDigest`` field is stored.
    Overleaf and Zenodo hold their own last-pushed digests since
    git is not the transport for those services.
    """
    return {
        "bOverleaf": False, "sOverleafTimestamp": "",
        "sOverleafLastPushedDigest": "",
        "bGithub": False, "sGithubTimestamp": "",
        "bZenodo": False, "sZenodoTimestamp": "",
        "sZenodoLastPushedDigest": "",
        "sZenodoLastPushedEndpoint": "",
    }


def fsToSyncStatusKey(sPath, sProjectRepoPath):
    """Normalize a file path to the repo-relative form used as dictSyncStatus key."""
    if not sPath:
        return sPath
    if not sProjectRepoPath:
        return sPath
    sPrefix = sProjectRepoPath.rstrip("/") + "/"
    if sPath.startswith(sPrefix):
        return sPath[len(sPrefix):]
    return sPath


def fdictLookupSyncEntry(dictSyncStatus, sPath, sProjectRepoPath=""):
    """Find a sync entry, tolerating historical path-shape drift.

    ``dictSyncStatus`` keys should be repo-relative going forward, but
    legacy workflows may hold container-absolute or project-rooted
    paths. This helper tries each plausible shape before giving up.
    """
    dictSync = dictSyncStatus or {}
    if sPath in dictSync:
        return dictSync[sPath]
    sContainerAbs = "/workspace/" + sPath
    if sContainerAbs in dictSync:
        return dictSync[sContainerAbs]
    sLeadingSlash = "/" + sPath
    if sLeadingSlash in dictSync:
        return dictSync[sLeadingSlash]
    if sProjectRepoPath:
        sProjectAbs = sProjectRepoPath.rstrip("/") + "/" + sPath
        if sProjectAbs in dictSync:
            return dictSync[sProjectAbs]
    return {}


def _fnUpdateServiceDigests(
    dictWorkflow, sService, dictPathToDigest,
    sProjectRepoPath=None, sEndpoint=None,
):
    """Write per-file last-pushed digests for one service.

    When ``sEndpoint`` is supplied, also stamp
    ``s{Service}LastPushedEndpoint`` so the badge layer can detect
    pushes that originated from a different remote (today only the
    Zenodo production / sandbox split needs this).
    """
    if "dictSyncStatus" not in dictWorkflow:
        dictWorkflow["dictSyncStatus"] = {}
    if sProjectRepoPath is None:
        sProjectRepoPath = dictWorkflow.get("sProjectRepoPath", "")
    for sPath, sDigest in (dictPathToDigest or {}).items():
        if not sDigest:
            continue
        sKey = fsToSyncStatusKey(sPath, sProjectRepoPath)
        _fnWriteServiceDigestEntry(
            dictWorkflow["dictSyncStatus"], sKey, sService,
            sDigest, sEndpoint,
        )


def _fnWriteServiceDigestEntry(
    dictSyncStatus, sKey, sService, sDigest, sEndpoint,
):
    """Stamp one sync entry with a digest and optional endpoint."""
    if sKey not in dictSyncStatus:
        dictSyncStatus[sKey] = fdictInitializeSyncEntry()
    dictSyncStatus[sKey][f"s{sService}LastPushedDigest"] = sDigest
    if sEndpoint is not None:
        dictSyncStatus[sKey][f"s{sService}LastPushedEndpoint"] = sEndpoint


def fnSetServiceTracking(
    dictWorkflow, sPath, sService, bTrack, sProjectRepoPath=None,
):
    """Toggle whether a single file is tracked for one remote service.

    ``sService`` is one of ``"Overleaf"`` / ``"Zenodo"`` / ``"Github"``.
    The flag maps onto ``dictSyncStatus[key]['b{Service}']``. Setting a
    flag to ``False`` does not clear the digest — a later re-opt-in
    can still compare against the historical push.
    """
    if "dictSyncStatus" not in dictWorkflow:
        dictWorkflow["dictSyncStatus"] = {}
    if sProjectRepoPath is None:
        sProjectRepoPath = dictWorkflow.get("sProjectRepoPath", "")
    sKey = fsToSyncStatusKey(sPath, sProjectRepoPath)
    if sKey not in dictWorkflow["dictSyncStatus"]:
        dictWorkflow["dictSyncStatus"][sKey] = (
            fdictInitializeSyncEntry()
        )
    dictWorkflow["dictSyncStatus"][sKey][f"b{sService}"] = bool(bTrack)


def fnUpdateOverleafDigests(
    dictWorkflow, dictPathToDigest, sProjectRepoPath=None,
):
    """Persist post-push Overleaf blob digests into ``dictSyncStatus``.

    ``dictPathToDigest`` maps local path to the git blob SHA observed
    in the Overleaf mirror after the push completed. Files missing
    from the digest map are left untouched (graceful degradation when
    the mirror refresh didn't surface the expected path). Keys are
    normalized to repo-relative form using ``sProjectRepoPath`` (falls
    back to ``dictWorkflow['sProjectRepoPath']`` when omitted) so
    badge lookups find the entry regardless of the caller's path
    shape.
    """
    _fnUpdateServiceDigests(
        dictWorkflow, "Overleaf", dictPathToDigest, sProjectRepoPath,
    )


def fnUpdateZenodoDigests(
    dictWorkflow, dictPathToDigest,
    sProjectRepoPath=None, sZenodoService=None,
):
    """Persist post-archive Zenodo blob digests into ``dictSyncStatus``.

    The digest is the file's git blob SHA at the moment of the
    archive; Zenodo deposits are immutable, so this snapshot is the
    authoritative "what was published" state. Keys are normalized the
    same way as Overleaf digests.

    ``sZenodoService`` records which Zenodo endpoint the push targeted
    (``"zenodo"`` or ``"sandbox"``). When supplied, it is written to
    ``sZenodoLastPushedEndpoint`` so the badge layer can flag a file
    as drifted after the workflow flips between production and sandbox.
    The argument defaults to ``dictWorkflow['sZenodoService']`` so
    legacy callers that omit it still record the right endpoint.
    """
    if sZenodoService is None:
        sZenodoService = dictWorkflow.get("sZenodoService", "") or None
    _fnUpdateServiceDigests(
        dictWorkflow, "Zenodo", dictPathToDigest, sProjectRepoPath,
        sEndpoint=sZenodoService,
    )


_TUPLE_ZENODO_LICENSE_CHOICES = (
    "CC-BY-4.0", "CC0-1.0", "MIT", "Apache-2.0",
    "GPL-3.0-or-later", "BSD-3-Clause",
)


def fdictInitializeZenodoMetadata():
    """Return a fresh dictZenodoMetadata block with sensible defaults."""
    return {
        "sTitle": "",
        "sDescription": "",
        "listCreators": [
            {"sName": "", "sAffiliation": "", "sOrcid": ""},
        ],
        "sLicense": "CC-BY-4.0",
        "listKeywords": [],
        "sRelatedGithubUrl": "",
    }


def fdictGetZenodoMetadata(dictWorkflow):
    """Return the workflow's Zenodo metadata, initialized if absent."""
    dictStored = dictWorkflow.get("dictZenodoMetadata")
    if not dictStored:
        return fdictInitializeZenodoMetadata()
    return dictStored


def fnSetZenodoMetadata(dictWorkflow, dictMetadata):
    """Validate a metadata dict and write it to the workflow."""
    _fnValidateZenodoMetadata(dictMetadata)
    dictWorkflow["dictZenodoMetadata"] = _fdictNormalizeZenodoMetadata(
        dictMetadata
    )


def _fnValidateZenodoMetadata(dictMetadata):
    """Raise ValueError on missing required fields or malformed input."""
    if not (dictMetadata.get("sTitle") or "").strip():
        raise ValueError("Title is required")
    listCreators = dictMetadata.get("listCreators") or []
    if not any(
        (c.get("sName") or "").strip() for c in listCreators
    ):
        raise ValueError(
            "At least one creator with a name is required"
        )
    if not (dictMetadata.get("sLicense") or "").strip():
        raise ValueError("License is required")
    sUrl = (dictMetadata.get("sRelatedGithubUrl") or "").strip()
    if sUrl and not sUrl.startswith(("http://", "https://")):
        raise ValueError(
            "Related URL must start with http:// or https://"
        )


def _fdictNormalizeZenodoMetadata(dictMetadata):
    """Return a cleaned copy with stripped strings and dropped empties."""
    return {
        "sTitle": (dictMetadata.get("sTitle") or "").strip(),
        "sDescription": (
            dictMetadata.get("sDescription") or ""
        ).strip(),
        "listCreators": _flistNormalizeCreators(
            dictMetadata.get("listCreators") or []
        ),
        "sLicense": (
            dictMetadata.get("sLicense") or "CC-BY-4.0"
        ).strip(),
        "listKeywords": _flistNormalizeKeywords(
            dictMetadata.get("listKeywords") or []
        ),
        "sRelatedGithubUrl": (
            dictMetadata.get("sRelatedGithubUrl") or ""
        ).strip(),
    }


def _flistNormalizeCreators(listCreators):
    """Drop creators with empty names; strip each field."""
    listOut = []
    for dictCreator in listCreators:
        sName = (dictCreator.get("sName") or "").strip()
        if not sName:
            continue
        listOut.append({
            "sName": sName,
            "sAffiliation": (
                dictCreator.get("sAffiliation") or ""
            ).strip(),
            "sOrcid": (dictCreator.get("sOrcid") or "").strip(),
        })
    return listOut


def _flistNormalizeKeywords(listKeywords):
    """Drop empty keywords; strip each."""
    listOut = []
    for sKeyword in listKeywords:
        if not isinstance(sKeyword, str):
            continue
        sClean = sKeyword.strip()
        if sClean:
            listOut.append(sClean)
    return listOut


def fsetExtractUpstreamIndices(sText):
    """Return set of step indices (0-based) referenced by {StepNN.} tokens."""
    return set(int(s) - 1 for s in re.findall(r"\{Step(\d+)\.", sText))


def _flistResolveOutputPaths(dictStep):
    """Return normalized paths for non-template output files."""
    sDirectory = dictStep.get("sDirectory", "")
    if not sDirectory:
        return []
    listPaths = []
    for sKey in ("saDataFiles", "saPlotFiles", "saOutputFiles"):
        for sFile in dictStep.get(sKey, []):
            if "{" in sFile:
                continue
            listPaths.append(
                posixpath.normpath(posixpath.join(sDirectory, sFile))
            )
    return listPaths


def fdictBuildImplicitDependencies(dictWorkflow):
    """Detect deps where an earlier step outputs into a later step's dir."""
    listSteps = dictWorkflow.get("listSteps", [])
    dictImplicit = {}
    for iIndex, dictStep in enumerate(listSteps):
        sDirectory = dictStep.get("sDirectory", "")
        if not sDirectory:
            continue
        sPrefix = posixpath.normpath(sDirectory) + "/"
        for iOther in range(iIndex):
            for sPath in _flistResolveOutputPaths(listSteps[iOther]):
                if sPath.startswith(sPrefix) or sPath == sPrefix[:-1]:
                    dictImplicit.setdefault(iOther, set()).add(iIndex)
                    break
    return dictImplicit


def _fnMergeImplicitDependencies(dictDirect, dictWorkflow):
    """Merge directory-overlap deps into dictDirect."""
    dictImplicit = fdictBuildImplicitDependencies(dictWorkflow)
    for iUpstream, setDownstream in dictImplicit.items():
        dictDirect.setdefault(iUpstream, set()).update(setDownstream)


def fdictBuildDirectDependencies(dictWorkflow):
    """Return {iUpstreamIndex: set(iDirectDownstreamIndices)}."""
    dictDirect = {}
    for iIndex, dictStep in enumerate(dictWorkflow["listSteps"]):
        setUpstream = set()
        for sKey in ("saDataCommands", "saPlotCommands",
                     "saTestCommands", "saDataFiles", "saPlotFiles",
                     "saDependencies", "saSetupCommands",
                     "saCommands", "saOutputFiles"):
            for sItem in dictStep.get(sKey, []):
                setUpstream |= fsetExtractUpstreamIndices(sItem)
        for iUpstream in setUpstream:
            if iUpstream not in dictDirect:
                dictDirect[iUpstream] = set()
            dictDirect[iUpstream].add(iIndex)
    _fnMergeImplicitDependencies(dictDirect, dictWorkflow)
    return dictDirect


def fdictBuildDownstreamMap(dictWorkflow):
    """Return {iStepIndex: set(all downstream indices)} via BFS."""
    from collections import deque
    dictDirect = fdictBuildDirectDependencies(dictWorkflow)
    iStepCount = len(dictWorkflow["listSteps"])
    dictDownstream = {}
    for iIndex in range(iStepCount):
        setVisited = set()
        dequeQueue = deque(dictDirect.get(iIndex, set()))
        while dequeQueue:
            iCurrent = dequeQueue.popleft()
            if iCurrent in setVisited:
                continue
            setVisited.add(iCurrent)
            dequeQueue.extend(dictDirect.get(iCurrent, set()))
        dictDownstream[iIndex] = setVisited
    return dictDownstream


def fnUpdateSyncStatus(
    dictWorkflow, listFilePaths, sService, sProjectRepoPath=None,
):
    """Mark files as synced to sService with current timestamp.

    Keys are normalized to repo-relative form so per-file badges can
    resolve them back from the canonical repo-relative path list.
    """
    from datetime import datetime, timezone

    if "dictSyncStatus" not in dictWorkflow:
        dictWorkflow["dictSyncStatus"] = {}
    if sProjectRepoPath is None:
        sProjectRepoPath = dictWorkflow.get("sProjectRepoPath", "")
    sTimestamp = datetime.now(timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )
    sBoolKey = f"b{sService}"
    sTimeKey = f"s{sService}Timestamp"
    for sPath in listFilePaths:
        sKey = fsToSyncStatusKey(sPath, sProjectRepoPath)
        if sKey not in dictWorkflow["dictSyncStatus"]:
            dictWorkflow["dictSyncStatus"][sKey] = (
                fdictInitializeSyncEntry()
            )
        dictWorkflow["dictSyncStatus"][sKey][sBoolKey] = True
        dictWorkflow["dictSyncStatus"][sKey][sTimeKey] = sTimestamp


def flistBuildTestCommands(dictStep):
    """Aggregate all test commands from dictTests into a flat list."""
    dictTests = dictStep.get("dictTests", {})
    listCommands = []
    for sKey in ("dictQualitative", "dictQuantitative", "dictIntegrity"):
        dictCategory = dictTests.get(sKey, {})
        listCommands.extend(dictCategory.get("saCommands", []))
    return listCommands


def flistResolveTestCommands(dictStep):
    """Return test commands from structured tests or legacy list."""
    if "dictTests" in dictStep:
        return flistBuildTestCommands(dictStep)
    return dictStep.get("saTestCommands", [])


def fsTestsDirectory(sStepDirectory):
    """Return the tests subdirectory path for a step."""
    return posixpath.join(sStepDirectory, "tests")


