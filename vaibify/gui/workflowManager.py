"""Load, validate, and CRUD operations on workflow.json."""

import hashlib
import json
import os
import posixpath
import re
import shlex
from collections import OrderedDict

from . import stateManager, workflowMigrations
from .workflowMigrations import (
    fbMigrateModifiedFilesToRepoRelative,
    fdictMigrateTestFormat,
    fnMigrateArchiveToTracking,
    fnMigrateRunEnabledKey,
    fnNormalizeSceneReferences,
)

S_STEP_REF_PATTERN = r"\{Step(\d+)\.([^}]+)\}"
# Symbolic cross-step reference: {step:<sStepId>.<stem>}. This is the
# canonical form; the positional S_STEP_REF_PATTERN above is deprecated
# and normalized to symbolic on load (migration) and save. The two are
# unambiguous — positional is capitalized "Step" + digits, symbolic is
# lowercase "step:" + a kebab id.
S_STEP_SYMBOLIC_PATTERN = r"\{step:([a-z0-9][a-z0-9-]*)\.([^}]+)\}"
S_VAIBIFY_PROJECTS_SUFFIX = "/.vaibify/projects/"
S_VAIBIFY_WORKFLOWS_SUFFIX = "/.vaibify/workflows/"
# Both the canonical and the legacy suffix, longest first, so path
# derivation and validation accept a Project file in either directory.
T_VAIBIFY_PROJECT_SUFFIXES = (
    S_VAIBIFY_PROJECTS_SUFFIX,
    S_VAIBIFY_WORKFLOWS_SUFFIX,
)


def fdictStepIdToIndex(dictWorkflow):
    """Return ``{sStepId: iStepIndex}`` (0-based) for the workflow.

    The resolution table for symbolic cross-step references. Steps
    without an id (only possible transiently, before
    ``fnEnsureStepIds`` runs) are omitted.
    """
    dictOut = {}
    for iIndex, dictStep in enumerate(dictWorkflow.get("listSteps", []) or []):
        if isinstance(dictStep, dict) and dictStep.get("sStepId"):
            dictOut[dictStep["sStepId"]] = iIndex
    return dictOut

__all__ = [
    "fbDeriveUnnecessaryVerification",
    "fbStepRequiresTests",
    "fbValidateWorkflow",
    "fsDescribeValidationFailure",
    "fsComputeWorkflowFingerprint",
    "fsDeriveProjectRepoPathFromWorkflow",
    "fnAttachComputedTrackedPaths",
    "fdictAutoDetectScripts",
    "fdictBuildDirectDependencies",
    "fdictBuildImplicitDependencies",
    "fdictBuildDownstreamMap",
    "fnClearDepGraphCache",
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
    "fnMigrateLegacyRemotes",
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
    "flistResolveStepScratchDirs",
    "flistValidateOutputFilePaths",
    "fnCleanStepScratchDirs",
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
    "ffResolveStepWallClockBudget",
    "fsResolveStepWorkdir",
    "fsResolveVariables",
    "fsTestsDirectory",
    "fsToSyncStatusKey",
]

DEFAULT_SEARCH_ROOT = "/workspace"

VAIBIFY_DIRECTORY = ".vaibify"
# On-disk home for Project definitions. The canonical directory is
# ``.vaibify/projects`` (a "Project" is what users see; the git repo
# that contains Projects is a "repository"). ``.vaibify/workflows`` is
# the legacy directory: discovery still reads it so existing repos keep
# loading, but new Projects are written under ``.vaibify/projects``.
VAIBIFY_PROJECTS_DIR = ".vaibify/projects"
VAIBIFY_WORKFLOWS_DIR = ".vaibify/workflows"
VAIBIFY_LOGS_DIR = ".vaibify/logs"

T_REQUIRED_WORKFLOW_KEYS = ("sPlotDirectory", "listSteps")
T_REQUIRED_STEP_KEYS = ("sName", "sDirectory", "saPlotCommands", "saPlotFiles")


def _flistDiscoverCandidatePaths(
    connectionDocker, sContainerId, sSearchRoot,
):
    """Run find inside the container, return candidate Project-file paths.

    Scans both the canonical ``.vaibify/projects`` directory and the
    legacy ``.vaibify/workflows`` directory so existing repos keep
    loading after the rename.
    """
    sCommand = (
        f"find {sSearchRoot} -maxdepth 4"
        f" \\( -path '*/.vaibify/projects/*.json'"
        f" -o -path '*/.vaibify/workflows/*.json' \\)"
        f" -type f 2>/dev/null"
    )
    _iExitCode, sOutput = connectionDocker.ftResultExecuteCommand(
        sContainerId, sCommand
    )
    return [
        sLine.strip() for sLine in sOutput.splitlines()
        if sLine.strip().endswith(".json")
    ]


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
    listCandidates = _flistDiscoverCandidatePaths(
        connectionDocker, sContainerId, sSearchRoot,
    )
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
        dictMeta = _fdictReadWorkflowMeta(
            connectionDocker, sContainerId, sPath,
        )
        listResults.append({
            "sPath": sPath,
            "sName": dictMeta["sName"],
            "iSizeBytes": dictMeta["iSizeBytes"],
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


def _fdictReadWorkflowMeta(connectionDocker, sContainerId, sPath):
    """Return ``{"sName", "iSizeBytes"}`` for a workflow JSON in the container.

    Size is the raw byte count of the JSON file; the frontend uses it
    to decide whether to surface a "loading large workflow" banner
    when the researcher selects the workflow.
    """
    try:
        baContent = connectionDocker.fbaFetchFile(sContainerId, sPath)
        dictWorkflow = json.loads(baContent.decode("utf-8"))
        return {
            "sName": dictWorkflow.get(
                "sWorkflowName", posixpath.basename(sPath),
            ),
            "iSizeBytes": len(baContent),
        }
    except Exception:
        return {
            "sName": posixpath.basename(sPath),
            "iSizeBytes": 0,
        }


def _fsResolveWorkflowPathOrDefault(
    connectionDocker, sContainerId, sWorkflowPath,
):
    """Return sWorkflowPath as-is, or the first discovered workflow path."""
    if sWorkflowPath is not None:
        return sWorkflowPath
    listWorkflows = flistFindWorkflowsInContainer(
        connectionDocker, sContainerId
    )
    if not listWorkflows:
        raise FileNotFoundError(
            "No project file found under search root"
        )
    return listWorkflows[0]["sPath"]


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
    sWorkflowPath = _fsResolveWorkflowPathOrDefault(
        connectionDocker, sContainerId, sWorkflowPath,
    )
    baContent = connectionDocker.fbaFetchFile(sContainerId, sWorkflowPath)
    dictWorkflow = json.loads(baContent.decode("utf-8"))
    # Fingerprint of the exact bytes read, so the reload detector's
    # self-write baseline can be seeded race-free from the very
    # content this load put into the cache (migrations below may
    # diverge the cache from the file; the baseline tracks the file).
    dictWorkflow["_sSourceFingerprint"] = hashlib.sha256(
        baContent,
    ).hexdigest()
    sRepoPath = fsDeriveProjectRepoPathFromWorkflow(sWorkflowPath)
    workflowMigrations.fnApplyMigrations(
        dictWorkflow, sProjectRepoPath=sRepoPath,
    )
    fnMigrateLegacyRemotes(dictWorkflow)
    sFailure = fsDescribeValidationFailure(dictWorkflow)
    if sFailure:
        raise ValueError(
            f"Invalid workflow.json at {sWorkflowPath}: {sFailure}"
        )
    _fnLoadAndMergeState(
        connectionDocker, sContainerId, dictWorkflow, sRepoPath,
    )
    fbDeriveUnnecessaryVerification(dictWorkflow)
    workflowMigrations.fnEnsureStepIds(dictWorkflow)
    fnAttachStepLabels(dictWorkflow)
    fnAttachComputedTrackedPaths(dictWorkflow)
    _fnDeriveAICSLevel(dictWorkflow, _ffilesContainerRepo(
        connectionDocker, sContainerId, sRepoPath,
    ))
    return dictWorkflow


def fnMigrateLegacyRemotes(dictWorkflow):
    """Populate ``dictRemotes`` entries from legacy top-level keys.

    Recomputed on every load and save, like ``fnAttachStepLabels``.
    Idempotent and non-destructive: an existing explicit
    ``dictRemotes`` entry is never overwritten, legacy keys are never
    removed, and verify-produced fields (e.g. ``sCommittedSha``,
    ``sLastPushCommit``) are never invented.
    """
    dictDerived = {
        "overleaf": _fdictLegacyOverleafEntry(dictWorkflow),
        "zenodo": _fdictLegacyZenodoEntry(dictWorkflow),
        "github": _fdictLegacyGithubEntry(dictWorkflow),
    }
    dictRemotes = dictWorkflow.get("dictRemotes") or {}
    for sService, dictEntry in dictDerived.items():
        if dictEntry and sService not in dictRemotes:
            dictRemotes[sService] = dictEntry
    if dictRemotes:
        dictWorkflow["dictRemotes"] = dictRemotes


def _fdictLegacyOverleafEntry(dictWorkflow):
    """Build ``dictRemotes.overleaf`` from legacy ``sOverleafProjectId``."""
    sProjectId = dictWorkflow.get("sOverleafProjectId") or ""
    if not sProjectId:
        return {}
    return {"sProjectId": sProjectId}


def _fdictLegacyZenodoEntry(dictWorkflow):
    """Build ``dictRemotes.zenodo`` from legacy DOI/deposit/service keys."""
    sDoi = (
        dictWorkflow.get("sZenodoDoi")
        or dictWorkflow.get("sZenodoLatestDoi") or ""
    )
    sRecordId = _fsLegacyZenodoRecordId(dictWorkflow, sDoi)
    if not sDoi and not sRecordId:
        return {}
    dictEntry = {}
    if sRecordId:
        dictEntry["sRecordId"] = sRecordId
    if sDoi:
        dictEntry["sDoi"] = sDoi
    sService = dictWorkflow.get("sZenodoService") or ""
    if sService:
        dictEntry["sService"] = sService
    return dictEntry


def _fsLegacyZenodoRecordId(dictWorkflow, sDoi):
    """Return the Zenodo record id from legacy keys, or an empty string.

    The DOI fallback only fires for genuine Zenodo DOIs, whose suffix
    is always ``/zenodo.NNN``; foreign DOIs whose suffix merely ends
    in ``zenodo.NNN`` (e.g. ``10.9999/notzenodo.123``) must not have
    a record id invented from them.
    """
    sDepositionId = str(dictWorkflow.get("sZenodoDepositionId") or "")
    if sDepositionId and sDepositionId != "0":
        return sDepositionId
    matchDoi = re.search(r"/zenodo\.(\d+)$", sDoi)
    return matchDoi.group(1) if matchDoi else ""


def _fdictLegacyGithubEntry(dictWorkflow):
    """Build ``dictRemotes.github`` from legacy ``sGithubBaseUrl``.

    Only the owner/repository binding is derivable from the URL;
    ``sBranch`` falls back to the verifier's default and
    ``sCommittedSha`` is left for verify to record.
    """
    from vaibify.reproducibility.githubAuth import (
        ftParseOwnerRepoFromRemoteUrl,
    )
    sBaseUrl = dictWorkflow.get("sGithubBaseUrl") or ""
    sOwner, sRepo = ftParseOwnerRepoFromRemoteUrl(sBaseUrl)
    if not sOwner or not sRepo:
        return {}
    return {"sOwner": sOwner, "sRepo": sRepo}


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

    By contract every Project file lives at
    ``<sProjectRepoPath>/.vaibify/projects/<name>.json`` (or the legacy
    ``.vaibify/workflows/`` directory). Stripping that suffix yields the
    repo root. Returns ``""`` when the path does not match (callers
    should treat that as no migration context, not an error).
    """
    if not sWorkflowPath:
        return ""
    for sSuffix in T_VAIBIFY_PROJECT_SUFFIXES:
        iSplit = sWorkflowPath.find(sSuffix)
        if iSplit > 0:
            return sWorkflowPath[:iSplit]
    return ""


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

    Scans ``saOutputDataFiles``, ``saPlotFiles``, and
    ``saScratchDirs`` on every step plus ``listDatasets[].sDestination``
    and the workflow-level ``sPlotDirectory``. Absolute paths and
    ``..``-escaping paths are flagged; template-bearing paths
    (containing ``{``) are skipped because they are resolved against
    the global variables dict at run time.
    """
    listWarnings = []
    for iIndex, dictStep in enumerate(dictWorkflow.get("listSteps", [])):
        sLabel = f"Step{iIndex + 1:02d}"
        sDirectory = dictStep.get("sDirectory", "")
        for sKey in (
            "saOutputDataFiles", "saPlotFiles", "saScratchDirs",
        ):
            for sPath in dictStep.get(sKey, []):
                sWarning = _fsCheckOutputPathBoundary(
                    sPath, sDirectory, sLabel, sKey,
                )
                if sWarning:
                    listWarnings.append(sWarning)
    listWarnings.extend(_flistValidateDatasetDestinations(dictWorkflow))
    listWarnings.extend(_flistValidateInputDataFilePaths(dictWorkflow))
    sPlotWarning = _fsCheckPlotDirectoryBoundary(
        dictWorkflow.get("sPlotDirectory", ""),
    )
    if sPlotWarning:
        listWarnings.append(sPlotWarning)
    return listWarnings


def _flistValidateInputDataFilePaths(dictWorkflow):
    """Return warnings for input-data declarations that break the contract.

    ``saInputDataFiles`` entries and ``listRemoteData[].sPath`` entries
    are repo-relative raw-data paths, so they get the same boundary
    check as dataset destinations. Cross-step products are forbidden
    in ``saInputDataFiles`` — a step token there would hide a
    dependency edge the command parser cannot see.
    """
    listWarnings = []
    for iIndex, dictStep in enumerate(dictWorkflow.get("listSteps", [])):
        sLabel = f"Step{iIndex + 1:02d}"
        for sPath in dictStep.get("saInputDataFiles", []) or []:
            sWarning = _fsCheckInputPathBoundary(
                sPath, sLabel, "saInputDataFiles",
            )
            if sWarning:
                listWarnings.append(sWarning)
        for dictRemote in dictStep.get("listRemoteData", []) or []:
            if not isinstance(dictRemote, dict):
                listWarnings.append(
                    f"{sLabel}: listRemoteData entries must be objects "
                    f"with an sPath field"
                )
                continue
            sWarning = _fsCheckInputPathBoundary(
                dictRemote.get("sPath", ""), sLabel, "listRemoteData",
            )
            if sWarning:
                listWarnings.append(sWarning)
    return listWarnings


def _fsCheckInputPathBoundary(sPath, sLabel, sKey):
    """Return a warning for one repo-relative input path, or ''."""
    if not isinstance(sPath, str) or not sPath:
        return ""
    if "{Step" in sPath or "{step:" in sPath:
        return (
            f"{sLabel}: {sKey} '{sPath}' must not reference a step "
            f"product — inputs are raw data no step produces; declare "
            f"cross-step files as tokens in commands instead"
        )
    if "{" in sPath:
        return ""
    if posixpath.isabs(sPath):
        return (
            f"{sLabel}: {sKey} '{sPath}' must be repo-relative, "
            f"not absolute"
        )
    sNorm = posixpath.normpath(sPath)
    if sNorm == ".." or sNorm.startswith("../"):
        return (
            f"{sLabel}: {sKey} '{sPath}' escapes the project repo "
            f"(resolves to '{sNorm}')"
        )
    return ""


def flistStepRemoteDataPaths(dictStep):
    """Return the repo-relative paths of a step's remote-pulled files.

    Single accessor for the ``listRemoteData`` provenance records so
    the gate, the provenance recorder, and any future reader agree on
    the shape. Boundary-violating and template-bearing entries are
    excluded — callers stat and hash these paths.
    """
    listPaths = []
    for dictRemote in dictStep.get("listRemoteData", []) or []:
        if not isinstance(dictRemote, dict):
            continue
        sPath = dictRemote.get("sPath", "")
        if not isinstance(sPath, str) or not sPath or "{" in sPath:
            continue
        if _fsCheckInputPathBoundary(sPath, "", "listRemoteData"):
            continue
        listPaths.append(sPath)
    return listPaths


def _fsCheckPlotDirectoryBoundary(sPlotDirectory):
    """Return a warning for sPlotDirectory leaving the project repo, or ''."""
    if not isinstance(sPlotDirectory, str) or not sPlotDirectory:
        return ""
    if "{" in sPlotDirectory:
        return ""
    if posixpath.isabs(sPlotDirectory):
        return (
            f"sPlotDirectory '{sPlotDirectory}' must be repo-relative, "
            f"not absolute"
        )
    sNorm = posixpath.normpath(sPlotDirectory)
    if sNorm == ".." or sNorm.startswith("../"):
        return (
            f"sPlotDirectory '{sPlotDirectory}' escapes the project "
            f"repo (resolves to '{sNorm}')"
        )
    return ""


def _flistValidateDatasetDestinations(dictWorkflow):
    """Return warnings for listDatasets[].sDestination boundary violations."""
    listWarnings = []
    for iIndex, dictDataset in enumerate(
        dictWorkflow.get("listDatasets", []),
    ):
        sLabel = f"Dataset{iIndex + 1:02d}"
        sDestination = dictDataset.get("sDestination", "")
        sWarning = _fsCheckDatasetDestinationBoundary(
            sDestination, sLabel,
        )
        if sWarning:
            listWarnings.append(sWarning)
    return listWarnings


def _fsCheckDatasetDestinationBoundary(sDestination, sLabel):
    """Return a warning for one sDestination leaving the project repo, or ''."""
    if not isinstance(sDestination, str) or not sDestination:
        return ""
    if "{" in sDestination:
        return ""
    if posixpath.isabs(sDestination):
        return (
            f"{sLabel}: sDestination '{sDestination}' must be "
            f"repo-relative, not absolute"
        )
    sNorm = posixpath.normpath(sDestination)
    if sNorm == ".." or sNorm.startswith("../"):
        return (
            f"{sLabel}: sDestination '{sDestination}' escapes the "
            f"project repo (resolves to '{sNorm}')"
        )
    return ""


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
    for sPath in dictStep.get("saOutputDataFiles", []):
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


T_VERIFICATION_STATES = (
    "untested", "passed", "failed", "error", "unnecessary",
)


_T_VERIFICATION_CATEGORY_KEYS = (
    ("dictIntegrity", "sIntegrity"),
    ("dictQualitative", "sQualitative"),
    ("dictQuantitative", "sQuantitative"),
)


def fbStepRequiresTests(dictStep):
    """Return True when data commands exist but no test commands."""
    bHasData = len(dictStep.get("saDataCommands", [])) > 0
    bHasTests = len(dictStep.get("saTestCommands", [])) > 0
    return bHasData and not bHasTests


def _fbApplyUnnecessaryUnitTest(dictVerify, dictTests):
    """Mark sUnitTest unnecessary when no category defines commands.

    Aggregate stays "unnecessary" only when every per-category command
    list is empty. Idempotent. Returns True when the field changed.
    """
    bAnyCommands = False
    for sCategory, _sVerifyKey in _T_VERIFICATION_CATEGORY_KEYS:
        if dictTests.get(sCategory, {}).get("saCommands"):
            bAnyCommands = True
            break
    if bAnyCommands:
        return False
    if dictVerify.get("sUnitTest") != "untested":
        return False
    dictVerify["sUnitTest"] = "unnecessary"
    return True


def _fbApplyUnnecessaryToStep(dictStep):
    """Rewrite empty-command verification fields to "unnecessary".

    Only flips a field that is currently "untested" — preserves any
    pass/fail/error result already recorded. Returns True when any
    field changed.
    """
    dictVerify = dictStep.setdefault("dictVerification", {})
    dictTests = dictStep.get("dictTests", {}) or {}
    bChanged = False
    for sCategory, sVerifyKey in _T_VERIFICATION_CATEGORY_KEYS:
        if dictTests.get(sCategory, {}).get("saCommands"):
            continue
        if dictVerify.get(sVerifyKey) != "untested":
            continue
        dictVerify[sVerifyKey] = "unnecessary"
        bChanged = True
    if _fbApplyUnnecessaryUnitTest(dictVerify, dictTests):
        bChanged = True
    return bChanged


def fbDeriveUnnecessaryVerification(dictWorkflow):
    """Rewrite "untested" → "unnecessary" for every command-free category.

    The single source of truth for the "no tests defined here" state.
    Idempotent — safe to call on load and on save. Returns True when
    any step changed so callers can persist.
    """
    bAnyChanged = False
    for dictStep in dictWorkflow.get("listSteps", []) or []:
        if not isinstance(dictStep, dict):
            continue
        if _fbApplyUnnecessaryToStep(dictStep):
            bAnyChanged = True
    return bAnyChanged


def _fnDeriveAICSLevel(dictWorkflow, filesRepo):
    """Compute and persist the workflow's current AICS level.

    Writes ``iAICSLevel`` on the dict. Called from the load-after-merge
    path and from ``fnSaveWorkflowToContainer`` so the integer is
    always current with the per-step verification state. The level
    is not authoritative — the derivation is; treat the persisted
    value as a cache invalidated on every load and save.
    ``filesRepo`` is a ``repoFiles`` adapter (container) so the L2/L3
    conjuncts read container truth; a raw path string keeps host-clone
    semantics for legacy callers.
    """
    from vaibify.reproducibility.levelGates import fiAICSLevel
    dictWorkflow["iAICSLevel"] = fiAICSLevel(
        dictWorkflow, filesRepo,
    )


def _ffilesContainerRepo(connectionDocker, sContainerId, sRepoPath):
    """Return the container repo-file adapter for the project repo.

    ``sRepoPath`` is a *container* path, so a ``ContainerRepoFiles``
    over the live docker connection is the only honest reader for the
    level derivation performed on every load and save.
    """
    from vaibify.reproducibility.repoFiles import ContainerRepoFiles
    return ContainerRepoFiles(connectionDocker, sContainerId, sRepoPath)


def fdictCreateStep(
    sName,
    sDirectory,
    bPlotOnly=True,
    bInteractive=False,
    saDataCommands=None,
    saOutputDataFiles=None,
    saTestCommands=None,
    saPlotCommands=None,
    saPlotFiles=None,
    saInputDataFiles=None,
):
    """Return a new step dictionary with validated fields."""
    return {
        "sName": sName,
        "sDirectory": sDirectory,
        "bRunEnabled": True,
        "bPlotOnly": bPlotOnly,
        "bInteractive": bInteractive,
        "saDataCommands": saDataCommands if saDataCommands else [],
        "saOutputDataFiles": saOutputDataFiles if saOutputDataFiles else [],
        "saTestCommands": saTestCommands if saTestCommands else [],
        "saPlotCommands": saPlotCommands if saPlotCommands else [],
        "saPlotFiles": saPlotFiles if saPlotFiles else [],
        "saInputDataFiles": saInputDataFiles if saInputDataFiles else [],
        "bNoInputData": False,
        "listRemoteData": [],
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
                     "saCommands"):
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


_T_TRANSIENT_STEP_KEYS = (
    "saSourceCodeDeps",
    "saStepScripts",
    "saTestStandards",
)


def _fdictStripComputedFields(dictWorkflow):
    """Return a shallow copy with transient fields removed from steps.

    ``dictStateLoadNotice`` is a one-shot toast payload attached
    during the connect-time recovery path; it must not leak into
    the persisted workflow.json or state.json. ``saStepScripts`` and
    ``saTestStandards`` are derived per-step badge-rendering caches
    and are also non-persistent. Only the spine and the steps that
    actually carry a transient key are copied — the historical
    ``copy.deepcopy`` cloned every nested list and dict on every
    save, which dominated round-trip cost at N >= 100.
    """
    dictClean = dict(dictWorkflow)
    dictClean.pop("dictStateLoadNotice", None)
    dictClean.pop("_sSourceFingerprint", None)
    dictClean["listSteps"] = [
        _fdictStripStepTransientKeys(dictStep)
        for dictStep in dictWorkflow.get("listSteps", [])
    ]
    return dictClean


def _fdictStripStepTransientKeys(dictStep):
    """Return a shallow copy of dictStep with transient keys removed.

    Always shallow-copies (vs. the prior optimization that returned
    the source dict by reference when no transient keys were present);
    callers expect to mutate the returned step without leaking into
    the in-memory workflow before serialization. The savings are
    minor anyway — most steps carry at least one transient key.
    """
    dictCopy = dict(dictStep)
    for sKey in _T_TRANSIENT_STEP_KEYS:
        dictCopy.pop(sKey, None)
    return dictCopy


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
    workflowMigrations.fnEnsureStepIds(dictWorkflow)
    workflowMigrations.fnRewritePositionalToSymbolic(dictWorkflow)
    fnAttachStepLabels(dictWorkflow)
    fnMigrateLegacyRemotes(dictWorkflow)
    fbDeriveUnnecessaryVerification(dictWorkflow)
    sRepoPath = fsDeriveProjectRepoPathFromWorkflow(sWorkflowPath)
    _fnDeriveAICSLevel(dictWorkflow, _ffilesContainerRepo(
        connectionDocker, sContainerId, sRepoPath,
    ))
    workflowMigrations.fnStampCurrentVersion(dictWorkflow)
    sJson, dictState = _ftSplitAndSerializeWorkflow(dictWorkflow)
    connectionDocker.fnWriteFile(
        sContainerId, sWorkflowPath, sJson.encode("utf-8")
    )
    sStatePath = stateManager.fsStatePathFromRepo(sRepoPath)
    if sStatePath:
        stateManager.fnSaveStateToContainer(
            connectionDocker, sContainerId, sStatePath, dictState,
        )
        stateManager.fnEnsureVaibifyGitignore(
            connectionDocker, sContainerId, sRepoPath,
        )


def _ftSplitAndSerializeWorkflow(dictWorkflow):
    """Return ``(sDeclarativeJson, dictState)`` for the merged dict.

    The single serialization authority for workflow.json bytes: both
    the save path and :func:`fsComputeWorkflowFingerprint` go through
    it, so the fingerprint recorded after a save is byte-identical to
    what a later ``sha256sum`` of the file inside the container
    reports.
    """
    dictClean = _fdictStripComputedFields(dictWorkflow)
    dictDeclarative, dictState = stateManager.ftSplitMergedDict(
        dictClean,
    )
    sJson = json.dumps(dictDeclarative, indent=2) + "\n"
    return sJson, dictState


def fsComputeWorkflowFingerprint(dictWorkflow):
    """Return the sha256 hex digest of the workflow's on-disk bytes.

    Computed host-side from the exact serialization the save path
    writes, so recording it as the self-write baseline has no window
    in which an in-container edit can be misattributed to the host —
    unlike the previous whole-second mtime baseline, which swallowed
    any agent edit landing in the same second as a backend save.
    """
    sJson, _dictState = _ftSplitAndSerializeWorkflow(dictWorkflow)
    return hashlib.sha256(sJson.encode("utf-8")).hexdigest()


def fsetExtractStepReferences(sText):
    """Return all {StepNN.variable} tokens found in sText as tuples."""
    return set(re.findall(S_STEP_REF_PATTERN, sText))


def fdictBuildStemRegistry(dictWorkflow):
    """Map each StepNN.stem to the step that produces it.

    Colliding basenames within a step register only under qualified
    stems (``pipelineUtils.fdictMapOutputTokenStems``), so standard
    scientific output filenames never need renaming for tokens.
    """
    from .pipelineUtils import fdictMapOutputTokenStems
    dictRegistry = {}
    for iIndex, dictStep in enumerate(dictWorkflow["listSteps"]):
        iNumber = iIndex + 1
        sStepId = dictStep.get("sStepId")
        dictTokenStems = fdictMapOutputTokenStems(
            _flistStepDeclaredOutputs(dictStep))
        for sStem in dictTokenStems:
            dictRegistry[f"Step{iNumber:02d}.{sStem}"] = iNumber
            if sStepId:
                dictRegistry[f"step:{sStepId}.{sStem}"] = iNumber
    return dictRegistry


def _flistStepDeclaredOutputs(dictStep):
    """Return every declared output file path for a step."""
    return (
        dictStep.get("saOutputDataFiles", [])
        + dictStep.get("saPlotFiles", [])
    )


def flistCollectReferenceStrings(dictStep):
    """Return all strings that may contain {StepNN.variable} references."""
    listStrings = []
    for sKey in ("saDataCommands", "saTestCommands", "saPlotCommands",
                 "saSetupCommands", "saCommands"):
        listStrings.extend(dictStep.get(sKey, []))
    listStrings.extend(dictStep.get("saDependencies", []))
    return listStrings


def flistValidateReferences(dictWorkflow):
    """Return a list of warnings about cross-step reference problems.

    Validates both cross-step reference forms: the canonical symbolic
    ``{step:<id>.<stem>}`` and the deprecated positional
    ``{StepNN.<stem>}`` (which additionally earns a migration warning).
    """
    dictRegistry = fdictBuildStemRegistry(dictWorkflow)
    dictIdToIndex = fdictStepIdToIndex(dictWorkflow)
    listWarnings = []

    for iIndex, dictStep in enumerate(dictWorkflow["listSteps"]):
        iNumber = iIndex + 1
        sStepLabel = f"Step{iNumber:02d}"

        for sText in flistCollectReferenceStrings(dictStep):
            _fnCheckCommandReferences(
                sText, sStepLabel, iNumber,
                dictWorkflow, dictRegistry, listWarnings,
            )
            _fnCheckSymbolicReferences(
                sText, sStepLabel, iNumber,
                dictRegistry, dictIdToIndex, listWarnings,
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
    """Append warnings for invalid/deprecated positional references."""
    iStepCount = len(dictWorkflow["listSteps"])
    for sRefNumber, sRefVariable in fsetExtractStepReferences(sCommand):
        iRefNumber = int(sRefNumber)
        sRefKey = f"Step{iRefNumber:02d}.{sRefVariable}"
        listWarnings.append(
            f"{sStepLabel}: reference {{{sRefKey}}} uses the deprecated "
            f"positional form — migrate to the symbolic "
            f"{{step:<id>.{sRefVariable}}} form"
        )
        sSuffix = _fsClassifyReference(
            iRefNumber, sRefKey, iNumber, iStepCount, dictRegistry
        )
        if sSuffix:
            listWarnings.append(
                f"{sStepLabel}: reference {{{sRefKey}}} {sSuffix}"
            )


def _fnCheckSymbolicReferences(
    sCommand, sStepLabel, iNumber,
    dictRegistry, dictIdToIndex, listWarnings,
):
    """Append warnings for invalid symbolic ``{step:<id>.<stem>}`` refs."""
    for sId, sVariable in re.findall(S_STEP_SYMBOLIC_PATTERN, sCommand):
        sRefKey = f"step:{sId}.{sVariable}"
        if sId not in dictIdToIndex:
            listWarnings.append(
                f"{sStepLabel}: reference {{{sRefKey}}} names no step id"
            )
            continue
        if sRefKey not in dictRegistry:
            listWarnings.append(
                f"{sStepLabel}: reference {{{sRefKey}}} has no matching "
                f"output file in that step"
            )
            continue
        if dictIdToIndex[sId] + 1 >= iNumber:
            listWarnings.append(
                f"{sStepLabel}: reference {{{sRefKey}}} points to a later "
                f"step (circular dependency)"
            )


def fdictBuildStepVariables(dictWorkflow, dictGlobalVars):
    """Map StepNN.stem to resolved absolute output paths.

    Token stems come from the declared (unresolved) entries via
    ``fdictMapOutputTokenStems`` so collision qualification matches
    ``fdictBuildStemRegistry`` exactly.
    """
    from .pipelineUtils import fdictMapOutputTokenStems
    dictStepVars = {}
    for iIndex, dictStep in enumerate(dictWorkflow["listSteps"]):
        iNumber = iIndex + 1
        sStepId = dictStep.get("sStepId")
        sStepDirectory = dictStep.get("sDirectory", "")
        dictTokenStems = fdictMapOutputTokenStems(
            _flistStepDeclaredOutputs(dictStep))
        for sStem, sOutputFile in dictTokenStems.items():
            sResolved = fsResolveVariables(sOutputFile, dictGlobalVars)
            sAbsPath = _fsResolveStepOutputPath(
                sResolved, sStepDirectory, dictGlobalVars
            )
            dictStepVars[f"Step{iNumber:02d}.{sStem}"] = sAbsPath
            if sStepId:
                dictStepVars[f"step:{sStepId}.{sStem}"] = sAbsPath
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


_T_COMMAND_FIELDS = (
    "saDataCommands", "saPlotCommands", "saTestCommands",
    "saSetupCommands", "saCommands",
)


def flistResidualStepTokens(sResolvedCommand):
    """Return cross-step tokens that a substitution failed to resolve.

    After :func:`fsResolveCommand`, a resolved token becomes a path, so
    any cross-step token still present names an output that does not
    exist (unknown step id / number, or an undeclared stem).
    """
    listTokens = []
    for sNum, sVar in re.findall(S_STEP_REF_PATTERN, sResolvedCommand):
        listTokens.append(f"{{Step{sNum}.{sVar}}}")
    for sId, sVar in re.findall(S_STEP_SYMBOLIC_PATTERN, sResolvedCommand):
        listTokens.append(f"{{step:{sId}.{sVar}}}")
    return listTokens


def fdictResolveWorkflowCommands(dictWorkflow, dictGlobalVars):
    """Return a dry-run report of every step command, without running.

    The workflow's ``make -n``: each command is substituted against the
    live graph, so an agent (or the dashboard) can verify a rewire —
    every token resolves, no dangling references — in the time of a
    dict build instead of an actual step run. Per command it reports
    the original text, the fully substituted text, and any residual
    cross-step tokens that failed to resolve; the top-level
    ``listWarnings`` carries :func:`flistValidateReferences` (including
    the positional-form deprecation nudge).
    """
    dictAllVars = dict(dictGlobalVars or {})
    dictAllVars.update(fdictBuildStepVariables(dictWorkflow, dictGlobalVars))
    listStepReports = []
    for iIndex, dictStep in enumerate(
        dictWorkflow.get("listSteps", []) or [],
    ):
        listCommands = []
        for sField in _T_COMMAND_FIELDS:
            for sCommand in dictStep.get(sField, []) or []:
                sResolved = fsResolveCommand(sCommand, dictAllVars)
                listCommands.append({
                    "sField": sField,
                    "sOriginal": sCommand,
                    "sResolved": sResolved,
                    "listUnresolvedTokens": flistResidualStepTokens(
                        sResolved,
                    ),
                })
        listStepReports.append({
            "iStepIndex": iIndex,
            "sStepId": dictStep.get("sStepId"),
            "sName": dictStep.get("sName"),
            "listCommands": listCommands,
        })
    return {
        "sWorkflowFingerprint": fsComputeWorkflowFingerprint(dictWorkflow),
        "listSteps": listStepReports,
        "listWarnings": flistValidateReferences(dictWorkflow),
    }


def _ffCoerceWallClockBudget(value):
    """Coerce a budget field to a non-negative float (0.0 on bad input)."""
    try:
        fValue = float(value)
    except (TypeError, ValueError):
        return 0.0
    return fValue if fValue > 0 else 0.0


def ffResolveStepWallClockBudget(dictWorkflow, dictStep):
    """Return the resolved wall-clock budget in seconds for a step.

    A step's own ``fWallClockBudgetSeconds`` wins; if absent or
    non-positive, the workflow-level ``fDefaultWallClockBudgetSeconds``
    applies; if that is also absent/non-positive the step has no budget
    (``0.0``) and is never flagged over-budget. The feature is opt-in: a
    workflow that declares no budgets behaves exactly as before. There
    is deliberately no built-in default — a legitimate forward-model run
    can take seconds or days, so guessing a ceiling would spuriously
    flag honest long runs, the very dashboard-dishonesty this avoids.
    """
    fStep = _ffCoerceWallClockBudget(dictStep.get("fWallClockBudgetSeconds"))
    if fStep > 0:
        return fStep
    return _ffCoerceWallClockBudget(
        dictWorkflow.get("fDefaultWallClockBudgetSeconds"),
    )


def fsResolveStepWorkdir(sStepDirectory, dictVariables):
    """Return absolute container workdir for a step's sDirectory."""
    if not sStepDirectory or posixpath.isabs(sStepDirectory):
        return sStepDirectory
    sRepoRoot = dictVariables.get("sRepoRoot", "") if dictVariables else ""
    if not sRepoRoot:
        return sStepDirectory
    return posixpath.join(sRepoRoot, sStepDirectory)


# ---------------------------------------------------------------------------
# audit MEDIUM #16: generic scratch-cleanup hook
# ---------------------------------------------------------------------------


def _fsResolveOneScratchDir(sPath, sStepWorkdir, dictVariables):
    """Resolve one scratch path; return empty string when invalid.

    Rejects absolute paths and ``..``-escaping paths after template
    expansion. Without the post-expansion check a template like
    ``{sUserVar}`` could resolve to ``../../etc/something`` and the
    runner's ``rm -rf`` would escape the step directory (the
    CLAUDE.md path-traversal threat).
    """
    sResolved = fsResolveVariables(sPath, dictVariables or {})
    if not sResolved or posixpath.isabs(sResolved):
        return ""
    sJoined = posixpath.normpath(
        posixpath.join(sStepWorkdir, sResolved),
    )
    sWorkdirNorm = posixpath.normpath(sStepWorkdir) + "/"
    if not (sJoined + "/").startswith(sWorkdirNorm):
        return ""
    return sJoined


def flistResolveStepScratchDirs(dictStep, dictVariables):
    """Return absolute container paths to delete before a step runs.

    Each entry in ``saScratchDirs`` is a repo-relative path joined to
    the step's ``sDirectory``. Template-bearing entries are resolved
    against ``dictVariables``. Returns an empty list when
    ``saScratchDirs`` is absent or empty.
    """
    listRaw = dictStep.get("saScratchDirs", []) or []
    sStepWorkdir = fsResolveStepWorkdir(
        dictStep.get("sDirectory", ""), dictVariables,
    )
    listResolved = []
    for sPath in listRaw:
        sAbsolute = _fsResolveOneScratchDir(
            sPath, sStepWorkdir, dictVariables,
        )
        if sAbsolute:
            listResolved.append(sAbsolute)
    return listResolved


def _fnRmRfDirectory(connectionDocker, sContainerId, sAbsPath):
    """rm -rf one absolute path inside the container; return exit code."""
    sShellSafe = sAbsPath.replace("'", "'\"'\"'")
    sCommand = f"rm -rf -- '{sShellSafe}'"
    iExitCode, _sOutput = connectionDocker.ftResultExecuteCommand(
        sContainerId, sCommand,
    )
    return iExitCode


def fnCleanStepScratchDirs(
    connectionDocker, sContainerId, dictStep, dictVariables,
):
    """Recursively delete a step's saScratchDirs in the container.

    Contract for audit MEDIUM #16: the runner calls this at step-start,
    before invoking the step's commands, so per-run scratch state from
    an earlier crashed attempt cannot poison the next run. Each
    deletion is one ``rm -rf`` via ``ftResultExecuteCommand``. Returns
    a list of (sAbsPath, iExitCode) tuples; missing dirs surface as
    non-zero exits and are never raised — that is the normal
    pre-clean case. Runner integration can land later.
    """
    listAbsPaths = flistResolveStepScratchDirs(dictStep, dictVariables)
    return [
        (sAbsPath, _fnRmRfDirectory(
            connectionDocker, sContainerId, sAbsPath,
        ))
        for sAbsPath in listAbsPaths
    ]


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
    dictData = dictStep.get("dictOutputDataFileCategories", {})
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
    return flistCollectArchiveFiles(dictWorkflow, "saOutputDataFiles")


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
    return flistCollectSupportingFiles(dictWorkflow, "saOutputDataFiles")


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
    """Persist post-archive Zenodo blob digests + endpoint into dictSyncStatus.

    ``sZenodoService`` defaults to ``dictWorkflow['sZenodoService']``
    so the badge layer can flag a file as drifted when the workflow
    flips between production and sandbox.
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


def fsetExtractUpstreamIndices(sText, dictIdToIndex=None):
    """Return 0-based step indices referenced by cross-step tokens.

    Resolves both the deprecated positional ``{StepNN.}`` form (index
    from the number) and the canonical symbolic ``{step:<id>.}`` form
    (index from ``dictIdToIndex``, when supplied). A symbolic id absent
    from the map contributes no edge — the same tolerant behavior the
    positional path has for an out-of-range number.
    """
    setIndices = set(
        int(s) - 1 for s in re.findall(r"\{Step(\d+)\.", sText)
    )
    if dictIdToIndex:
        for sId in re.findall(r"\{step:([a-z0-9][a-z0-9-]*)\.", sText):
            if sId in dictIdToIndex:
                setIndices.add(dictIdToIndex[sId])
    return setIndices


def _flistResolveOutputPaths(dictStep):
    """Return normalized paths for non-template output files."""
    sDirectory = dictStep.get("sDirectory", "")
    if not sDirectory:
        return []
    listPaths = []
    for sKey in ("saOutputDataFiles", "saPlotFiles"):
        for sFile in dictStep.get(sKey, []):
            if "{" in sFile:
                continue
            listPaths.append(
                posixpath.normpath(posixpath.join(sDirectory, sFile))
            )
    return listPaths


_I_DEP_CACHE_MAX_ENTRIES = 16
_DICT_DEP_CACHE = OrderedDict()


def _fsWorkflowDepCacheKey(dictWorkflow):
    """Return a SHA256 key over the dep-graph-relevant workflow fields.

    Only fields that influence the dependency graph participate so
    edits to unrelated metadata (toasts, badge caches, run stats) do
    not bust the cache. Steps contribute their command/file lists,
    their saDependencies escape hatch, and their sDirectory.
    """
    listEntries = []
    for dictStep in dictWorkflow.get("listSteps", []) or []:
        if not isinstance(dictStep, dict):
            listEntries.append(None)
            continue
        dictRelevant = {sKey: dictStep.get(sKey, []) for sKey in (
            "saDataCommands", "saPlotCommands", "saTestCommands",
            "saOutputDataFiles", "saPlotFiles",
            "saSetupCommands", "saCommands",
        )}
        # saDependencies is a set semantically; sort so reorderings
        # within the list don't bust the cache (Review B observation).
        listDeps = dictStep.get("saDependencies", []) or []
        dictRelevant["saDependencies"] = sorted(
            [str(s) for s in listDeps if s is not None],
        )
        dictRelevant["sDirectory"] = dictStep.get("sDirectory", "")
        listEntries.append(dictRelevant)
    sCanonical = json.dumps(listEntries, sort_keys=True, default=str)
    return hashlib.sha256(sCanonical.encode("utf-8")).hexdigest()


def _fdepCacheGet(sKey, sField):
    """Return the cached field value for sKey, or None on miss."""
    dictEntry = _DICT_DEP_CACHE.get(sKey)
    if dictEntry is None:
        return None
    _DICT_DEP_CACHE.move_to_end(sKey)
    return dictEntry.get(sField)


def _fnDepCacheSet(sKey, sField, value):
    """Store value at (sKey, sField) and evict the oldest entry if needed."""
    dictEntry = _DICT_DEP_CACHE.get(sKey)
    if dictEntry is None:
        dictEntry = {}
        _DICT_DEP_CACHE[sKey] = dictEntry
    dictEntry[sField] = value
    _DICT_DEP_CACHE.move_to_end(sKey)
    while len(_DICT_DEP_CACHE) > _I_DEP_CACHE_MAX_ENTRIES:
        _DICT_DEP_CACHE.popitem(last=False)


def fnClearDepGraphCache():
    """Discard every cached dep graph (tests + invalidation hooks)."""
    _DICT_DEP_CACHE.clear()


def _flistDirectoryAncestors(sPath):
    """Yield every ancestor directory of sPath from longest to shortest.

    ``a/b/c.csv`` yields ``a/b``, ``a``. Stops before the empty root
    so a step whose ``sDirectory`` is empty cannot match every output.
    """
    listAncestors = []
    sCurrent = posixpath.dirname(sPath)
    while sCurrent and sCurrent != "/":
        listAncestors.append(sCurrent)
        sCurrent = posixpath.dirname(sCurrent)
    return listAncestors


def _fdictIndexStepDirectories(listSteps):
    """Return ``{normalizedDirectory: iStepIndex}`` for non-empty dirs."""
    dictByDirectory = {}
    for iIndex, dictStep in enumerate(listSteps):
        sDirectory = dictStep.get("sDirectory", "")
        if not sDirectory:
            continue
        sNormalized = posixpath.normpath(sDirectory)
        dictByDirectory.setdefault(sNormalized, iIndex)
    return dictByDirectory


def fdictBuildImplicitDependencies(dictWorkflow):
    """Detect deps where an earlier step outputs into a later step's dir.

    Algorithmic shape is O(N + total-outputs * depth): one pre-scan
    builds ``{normalizedDirectory: iStepIndex}``, then each output
    path probes itself and its ancestor directories against that
    index. The old O(N**2 * M) nested scan dominated workflow-load
    cost at N >= 100.
    """
    listSteps = dictWorkflow.get("listSteps", [])
    dictByDirectory = _fdictIndexStepDirectories(listSteps)
    dictImplicit = {}
    for iProducer, dictStep in enumerate(listSteps):
        for sPath in _flistResolveOutputPaths(dictStep):
            for sCandidate in [sPath] + _flistDirectoryAncestors(sPath):
                iConsumer = dictByDirectory.get(sCandidate)
                if iConsumer is None or iConsumer <= iProducer:
                    continue
                dictImplicit.setdefault(iProducer, set()).add(iConsumer)
    return dictImplicit


def _fnMergeImplicitDependencies(dictDirect, dictWorkflow):
    """Merge directory-overlap deps into dictDirect."""
    dictImplicit = fdictBuildImplicitDependencies(dictWorkflow)
    for iUpstream, setDownstream in dictImplicit.items():
        dictDirect.setdefault(iUpstream, set()).update(setDownstream)


def _fdictComputeDirectDependencies(dictWorkflow):
    """Uncached direct-dependency computation; see fdictBuildDirectDependencies."""
    dictIdToIndex = fdictStepIdToIndex(dictWorkflow)
    dictDirect = {}
    for iIndex, dictStep in enumerate(dictWorkflow["listSteps"]):
        setUpstream = set()
        for sKey in ("saDataCommands", "saPlotCommands",
                     "saTestCommands", "saOutputDataFiles", "saPlotFiles",
                     "saDependencies", "saSetupCommands",
                     "saCommands"):
            for sItem in dictStep.get(sKey, []):
                setUpstream |= fsetExtractUpstreamIndices(
                    sItem, dictIdToIndex,
                )
        for iUpstream in setUpstream:
            dictDirect.setdefault(iUpstream, set()).add(iIndex)
    _fnMergeImplicitDependencies(dictDirect, dictWorkflow)
    return dictDirect


def fdictBuildDirectDependencies(dictWorkflow):
    """Return {iUpstreamIndex: set(iDirectDownstreamIndices)}.

    Cached against the SHA256 of the dep-graph-relevant workflow
    fields so repeated polls on an unchanged workflow re-use the
    computed graph. The cache is module-level LRU bounded to
    ``_I_DEP_CACHE_MAX_ENTRIES`` entries.
    """
    sKey = _fsWorkflowDepCacheKey(dictWorkflow)
    dictCached = _fdepCacheGet(sKey, "dictDirect")
    if dictCached is not None:
        return dictCached
    dictDirect = _fdictComputeDirectDependencies(dictWorkflow)
    _fnDepCacheSet(sKey, "dictDirect", dictDirect)
    return dictDirect


def _fdictComputeDownstreamMap(dictWorkflow):
    """Single O(N+E) reverse-topological closure over the direct graph."""
    dictDirect = fdictBuildDirectDependencies(dictWorkflow)
    iStepCount = len(dictWorkflow["listSteps"])
    listOrder = _flistReverseTopologicalOrder(iStepCount, dictDirect)
    dictDownstream = {iIndex: set() for iIndex in range(iStepCount)}
    for iIndex in listOrder:
        setOut = dictDownstream[iIndex]
        for iChild in dictDirect.get(iIndex, ()):  # noqa: PLR1714
            setOut.add(iChild)
            setOut |= dictDownstream[iChild]
    return dictDownstream


def _flistReverseTopologicalOrder(iStepCount, dictDirect):
    """Return step indices in reverse topological order (Kahn's algorithm).

    Cycles in the declared dep graph are tolerated — any node never
    dequeued stays last in the order. Callers that care about cycles
    detect them separately; the transitive closure remains correct
    over the acyclic portion and bounded by the visited set on the
    cyclic remainder.
    """
    from collections import deque
    listInDegree = [0] * iStepCount
    for setChildren in dictDirect.values():
        for iChild in setChildren:
            if 0 <= iChild < iStepCount:
                listInDegree[iChild] += 1
    dequeReady = deque(
        iIndex for iIndex in range(iStepCount)
        if listInDegree[iIndex] == 0
    )
    listTopological = []
    while dequeReady:
        iCurrent = dequeReady.popleft()
        listTopological.append(iCurrent)
        for iChild in dictDirect.get(iCurrent, ()):  # noqa: PLR1714
            if 0 <= iChild < iStepCount:
                listInDegree[iChild] -= 1
                if listInDegree[iChild] == 0:
                    dequeReady.append(iChild)
    setRemaining = set(range(iStepCount)) - set(listTopological)
    listTopological.extend(sorted(setRemaining))
    return list(reversed(listTopological))


def fdictBuildDownstreamMap(dictWorkflow):
    """Return {iStepIndex: set(all downstream indices)}.

    Replaces the historical per-step BFS (O(N*(N+E))) with one
    O(N+E) reverse-topological-closure pass; both the direct graph
    and the closure are cached under the same workflow-hash key.
    """
    sKey = _fsWorkflowDepCacheKey(dictWorkflow)
    dictCached = _fdepCacheGet(sKey, "dictDownstream")
    if dictCached is not None:
        return dictCached
    dictDownstream = _fdictComputeDownstreamMap(dictWorkflow)
    _fnDepCacheSet(sKey, "dictDownstream", dictDownstream)
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


