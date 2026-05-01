"""Schema versioning and migrations for workflow.json.

Each persisted workflow file carries an integer version under the
``iWorkflowSchemaVersion`` top-level key. ``fnApplyMigrations`` runs
the registered migrators in order until the dict is at
``I_CURRENT_WORKFLOW_VERSION``. Migrations are pure transformations of
the in-memory dict and need not be idempotent — the version field
gates execution.

Files written before this scheme existed have no version field and
are treated as version 0; the v0→v1 migrator folds in the legacy
migration functions that previously ran unconditionally on every
load. Adding a new migration is two steps: write the migrator and
append it to ``T_MIGRATORS``, bump ``I_CURRENT_WORKFLOW_VERSION``.

This module imports only from ``pathContract`` (also a leaf), so
workflowManager and director can both depend on it without forming a
cycle.
"""

import contextlib
import posixpath
import re

from .pathContract import flistNormalizeModifiedFiles


__all__ = [
    "I_CURRENT_WORKFLOW_VERSION",
    "S_VERSION_KEY",
    "T_MIGRATORS",
    "fbWorkflowNeedsMigration",
    "fdictMigrateTestFormat",
    "fiGetSchemaVersion",
    "fnApplyMigrations",
    "fnMigrateAbsoluteContainerPaths",
    "fnMigrateArchiveToTracking",
    "fnMigrateRunEnabledKey",
    "fnNormalizeSceneReferences",
    "fbMigrateModifiedFilesToRepoRelative",
    "fnStampCurrentVersion",
]


I_CURRENT_WORKFLOW_VERSION = 3
S_VERSION_KEY = "iWorkflowSchemaVersion"


def fiGetSchemaVersion(dictWorkflow):
    """Return the persisted schema version, defaulting to 0."""
    iVersion = dictWorkflow.get(S_VERSION_KEY, 0)
    try:
        return int(iVersion)
    except (TypeError, ValueError):
        return 0


def fbWorkflowNeedsMigration(dictWorkflow):
    """Return True when the dict's version is below the current."""
    return fiGetSchemaVersion(dictWorkflow) < I_CURRENT_WORKFLOW_VERSION


def fnStampCurrentVersion(dictWorkflow):
    """Set the schema version field to the current value."""
    dictWorkflow[S_VERSION_KEY] = I_CURRENT_WORKFLOW_VERSION


def fnApplyMigrations(dictWorkflow, sProjectRepoPath=""):
    """Run every needed migration in order; stamp the new version.

    ``sProjectRepoPath`` provides container-side context for path
    rewrites. May be empty; migrators that need it will skip when it
    is missing.
    """
    iVersion = fiGetSchemaVersion(dictWorkflow)
    for iFromVersion, fnMigrator in T_MIGRATORS:
        if iVersion <= iFromVersion:
            fnMigrator(dictWorkflow, sProjectRepoPath)
            iVersion = iFromVersion + 1
            dictWorkflow[S_VERSION_KEY] = iVersion
    fnStampCurrentVersion(dictWorkflow)
    return fiGetSchemaVersion(dictWorkflow)


def fnMigrateRunEnabledKey(dictWorkflow):
    """Rewrite legacy ``bEnabled`` step field to ``bRunEnabled``.

    Older workflow.json files used ``bEnabled`` as the run-scope
    flag. The field was renamed to keep run scope and verification
    scope unambiguous. Idempotent on already-migrated steps.
    """
    for dictStep in dictWorkflow.get("listSteps", []):
        if "bEnabled" not in dictStep:
            continue
        if "bRunEnabled" not in dictStep:
            dictStep["bRunEnabled"] = dictStep["bEnabled"]
        dictStep.pop("bEnabled", None)


def fdictMigrateTestFormat(dictStep):
    """Migrate old saTestCommands format to new dictTests structure."""
    if "dictTests" in dictStep:
        return dictStep
    listOldCommands = dictStep.get("saTestCommands", [])
    dictStep["dictTests"] = {
        "dictQualitative": {"saCommands": [], "sFilePath": ""},
        "dictQuantitative": {
            "saCommands": [], "sFilePath": "", "sStandardsPath": "",
        },
        "dictIntegrity": {
            "saCommands": list(listOldCommands), "sFilePath": "",
        },
        "listUserTests": [],
    }
    dictVerification = dictStep.setdefault("dictVerification", {})
    dictVerification.setdefault("sQualitative", "untested")
    dictVerification.setdefault("sQuantitative", "untested")
    dictVerification.setdefault("sIntegrity", "untested")
    return dictStep


def fnNormalizeSceneReferences(dictStep):
    """Replace deprecated {SceneNN.var} tokens with {StepNN.var}."""
    for sKey in (
        "saDataCommands", "saPlotCommands", "saTestCommands",
        "saSetupCommands", "saCommands", "saDependencies",
        "saDataFiles", "saPlotFiles", "saOutputFiles",
    ):
        listValues = dictStep.get(sKey)
        if not listValues:
            continue
        dictStep[sKey] = [
            re.sub(r"\{Scene(\d+)\.", r"{Step\1.", s)
            for s in listValues
        ]


def fnMigrateArchiveToTracking(dictWorkflow):
    """One-shot: promote legacy 'archive' categories to tracking flags.

    Before the badge rework, each output file carried an "archive"
    vs. "supporting" designation in ``dictPlotFileCategories`` /
    ``dictDataFileCategories``. Archive files were the ones pushed
    to Overleaf and Zenodo in batch operations. This function seeds
    ``dictSyncStatus`` entries with ``bOverleaf=True`` and
    ``bZenodo=True`` for each previously-archive file so badges
    render in the expected "tracked" state after the upgrade. Runs
    at most once per workflow (guarded by ``bArchiveTrackingMigrated``
    on the top-level dict). Returns True when a migration ran.
    """
    if dictWorkflow.get("bArchiveTrackingMigrated"):
        return False
    if "dictSyncStatus" not in dictWorkflow:
        dictWorkflow["dictSyncStatus"] = {}
    sRepoRoot = dictWorkflow.get("sProjectRepoPath", "")
    for dictStep in dictWorkflow.get("listSteps", []):
        sStepDir = dictStep.get("sDirectory", "")
        for sArrayKey in ("saDataFiles", "saPlotFiles"):
            for sFile in dictStep.get(sArrayKey, []):
                if _fsGetFileCategory(dictStep, sFile) != "archive":
                    continue
                sRepoRel = _fsJoinRepoRelPath(sStepDir, sFile)
                sKey = _fsToSyncStatusKey(sRepoRel, sRepoRoot)
                if sKey not in dictWorkflow["dictSyncStatus"]:
                    dictWorkflow["dictSyncStatus"][sKey] = (
                        _fdictInitializeSyncEntry()
                    )
                dictWorkflow["dictSyncStatus"][sKey]["bOverleaf"] = True
                dictWorkflow["dictSyncStatus"][sKey]["bZenodo"] = True
    dictWorkflow["bArchiveTrackingMigrated"] = True
    return True


def fbMigrateModifiedFilesToRepoRelative(dictWorkflow):
    """Normalize legacy abs paths in dictVerification.listModifiedFiles.

    Older workflow.json files stored absolute container paths in
    ``dictVerification['listModifiedFiles']``. The wire-format contract
    is now repo-relative. This migration rewrites each step's list in
    place and returns True if any change was made. Idempotent: a
    workflow already in repo-relative form is unchanged.
    """
    sRepoRoot = dictWorkflow.get("sProjectRepoPath", "")
    bChanged = False
    for dictStep in dictWorkflow.get("listSteps", []):
        dictVerify = dictStep.get("dictVerification", {})
        listExisting = dictVerify.get("listModifiedFiles")
        if not listExisting:
            continue
        listNormalized = flistNormalizeModifiedFiles(
            listExisting, sRepoRoot,
        )
        if listNormalized != listExisting:
            dictVerify["listModifiedFiles"] = listNormalized
            dictStep["dictVerification"] = dictVerify
            bChanged = True
    return bChanged


def fnMigrateAbsoluteContainerPaths(dictWorkflow, sProjectRepoPath):
    """Strip absolute container prefixes from sDirectory and output paths.

    Backfills workflows authored before vaibify enforced
    repo-relative paths in step ``sDirectory``, ``saOutputFiles``,
    ``saDataFiles``, and ``saPlotFiles``. Falls back to inferring the
    project repo prefix from each step's existing ``sDirectory`` when
    ``sProjectRepoPath`` is empty (the legacy shape was always
    ``/workspace/<repo>/<stepDir>``).

    Output paths are relative to ``sDirectory`` once normalized, so
    legacy absolute entries that point inside the step directory are
    rewritten as bare filenames. Entries that point outside the step
    (rare, but possible — e.g., a shared figure dir) are rewritten
    relative to the repo root and left for the path-boundary check
    to flag if the result still escapes.
    """
    sRoot = (sProjectRepoPath or "").rstrip("/")
    for dictStep in dictWorkflow.get("listSteps", []):
        sLegacyDirectory = dictStep.get("sDirectory", "")
        sStepRoot = sRoot or _fsInferRepoRootFromAbsoluteDir(
            sLegacyDirectory
        )
        if not sStepRoot:
            continue
        sStepRoot = sStepRoot.rstrip("/")
        sLegacyDirectoryNoSlash = sLegacyDirectory.rstrip("/")
        dictStep["sDirectory"] = _fsStripRoot(
            sLegacyDirectory, sStepRoot,
        )
        for sArrayKey in (
            "saOutputFiles", "saDataFiles", "saPlotFiles",
        ):
            listExisting = dictStep.get(sArrayKey)
            if not listExisting:
                continue
            dictStep[sArrayKey] = [
                _fsStripStepOrRoot(
                    sPath, sLegacyDirectoryNoSlash, sStepRoot,
                )
                for sPath in listExisting
            ]


def _fsStripStepOrRoot(sPath, sLegacyDirectory, sRepoRoot):
    """Strip the most-specific prefix from an output-file entry.

    Tries the step directory first (yields a bare filename), then
    falls back to the repo root (yields a repo-relative path).
    Template-bearing and already-relative entries pass through.
    """
    if not sPath or "{" in sPath or not posixpath.isabs(sPath):
        return sPath
    if sLegacyDirectory:
        sStepPrefix = sLegacyDirectory.rstrip("/") + "/"
        if sPath.startswith(sStepPrefix):
            return sPath[len(sStepPrefix):]
    return _fsStripRoot(sPath, sRepoRoot)


def _fsInferRepoRootFromAbsoluteDir(sDirectory):
    """Guess the project repo root from a legacy absolute step dir.

    Legacy shape was ``/workspace/<repo>/<stepDir>``. Returns the
    inferred ``/workspace/<repo>`` prefix, or ``""`` if the directory
    is not in that form.
    """
    if not sDirectory or not posixpath.isabs(sDirectory):
        return ""
    listParts = sDirectory.split("/")
    if len(listParts) < 4 or listParts[1] != "workspace":
        return ""
    return "/" + posixpath.join(listParts[1], listParts[2])


def _fsStripRoot(sPath, sRoot):
    """Strip the ``sRoot/`` prefix from ``sPath`` if present.

    Leaves template-bearing paths (containing ``{``) and
    already-relative paths untouched.
    """
    if not sPath or "{" in sPath:
        return sPath
    if not posixpath.isabs(sPath):
        return sPath
    sPrefix = sRoot.rstrip("/") + "/"
    if sPath.startswith(sPrefix):
        return sPath[len(sPrefix):]
    if sPath == sRoot.rstrip("/"):
        return ""
    return sPath


def _fsGetFileCategory(dictStep, sFilePath):
    """Return 'archive' or 'supporting' for a data or plot file."""
    dictPlot = dictStep.get("dictPlotFileCategories", {})
    if sFilePath in dictPlot:
        return dictPlot[sFilePath]
    dictData = dictStep.get("dictDataFileCategories", {})
    if sFilePath in dictData:
        return dictData[sFilePath]
    return "archive"


def _fsToSyncStatusKey(sPath, sProjectRepoPath):
    """Normalize a file path to the repo-relative dictSyncStatus key."""
    if not sPath:
        return sPath
    if not sProjectRepoPath:
        return sPath
    sPrefix = sProjectRepoPath.rstrip("/") + "/"
    if sPath.startswith(sPrefix):
        return sPath[len(sPrefix):]
    return sPath


def _fdictInitializeSyncEntry():
    """Return a fresh sync entry with all services unsynced."""
    return {
        "bOverleaf": False, "sOverleafTimestamp": "",
        "sOverleafLastPushedDigest": "",
        "bGithub": False, "sGithubTimestamp": "",
        "bZenodo": False, "sZenodoTimestamp": "",
        "sZenodoLastPushedDigest": "",
    }


def _fsJoinRepoRelPath(sStepDir, sFile):
    """Join a step dir and a filename into a repo-relative path."""
    if not sStepDir:
        return sFile
    if posixpath.isabs(sFile):
        return sFile
    return posixpath.join(sStepDir, sFile)


@contextlib.contextmanager
def _fnTemporaryProjectRepoPath(dictWorkflow, sProjectRepoPath):
    """Inject ``sProjectRepoPath`` into the dict for the duration of a block.

    Restores the prior key state on exit. A no-op when the caller did
    not supply a path or when the dict already has a non-empty value.
    """
    if not sProjectRepoPath:
        yield
        return
    bHadKey = "sProjectRepoPath" in dictWorkflow
    sOriginal = dictWorkflow.get("sProjectRepoPath", "")
    if not sOriginal:
        dictWorkflow["sProjectRepoPath"] = sProjectRepoPath
    try:
        yield
    finally:
        if not bHadKey:
            dictWorkflow.pop("sProjectRepoPath", None)
        else:
            dictWorkflow["sProjectRepoPath"] = sOriginal


def _fnMigrateV0ToV1(dictWorkflow, sProjectRepoPath):
    """Apply the legacy unconditional migrations.

    The two legacy helpers that use the project repo root
    (``fnMigrateArchiveToTracking``, ``fbMigrateModifiedFilesToRepoRelative``)
    historically read it from ``dictWorkflow["sProjectRepoPath"]``.
    During load that key is not yet populated; ``fnApplyMigrations``
    threads the root in via ``sProjectRepoPath`` instead, so this stage
    sets it on the dict for the duration of the legacy calls and
    restores the prior value afterwards.
    """
    fnMigrateRunEnabledKey(dictWorkflow)
    with _fnTemporaryProjectRepoPath(dictWorkflow, sProjectRepoPath):
        fnMigrateArchiveToTracking(dictWorkflow)
        fbMigrateModifiedFilesToRepoRelative(dictWorkflow)
    for dictStep in dictWorkflow.get("listSteps", []):
        fdictMigrateTestFormat(dictStep)
        fnNormalizeSceneReferences(dictStep)


def _fnMigrateV1ToV2(dictWorkflow, sProjectRepoPath):
    """Strip absolute container paths from step directories and outputs."""
    fnMigrateAbsoluteContainerPaths(dictWorkflow, sProjectRepoPath)


def _fnMigrateV2ToV3(dictWorkflow, sProjectRepoPath):
    """Mark the workflow as ready for the workflow/state file split.

    No in-memory transformation is needed: the stateful fields stay
    on the merged dict so route handlers and tests keep seeing the
    historical shape. The next save splits ``dictVerification``,
    ``dictRunStats``, ``bArchiveTrackingMigrated``, and the transient
    ``sLabel`` out into ``.vaibify/state.json``. The dict's transient
    ``sProjectRepoPath`` field is dropped — it's recomputed at load
    time and was never authoritative.
    """
    dictWorkflow.pop("sProjectRepoPath", None)


T_MIGRATORS = (
    (0, _fnMigrateV0ToV1),
    (1, _fnMigrateV1ToV2),
    (2, _fnMigrateV2ToV3),
)
