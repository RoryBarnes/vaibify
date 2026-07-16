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
    "fnEnsureStepIds",
    "fnRewritePositionalToSymbolic",
    "fnMigrateAbsoluteContainerPaths",
    "fnMigrateAbsoluteTestPaths",
    "fnMigrateArchiveToTracking",
    "fnMigrateRunEnabledKey",
    "fnNormalizeSceneReferences",
    "fbMigrateModifiedFilesToRepoRelative",
    "fnStampCurrentVersion",
]


I_CURRENT_WORKFLOW_VERSION = 8
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


def _fsSlugFromStepName(sName):
    """Return a kebab-case ASCII slug from a step name, or empty string."""
    sLower = (sName or "").strip().lower()
    return re.sub(r"[^a-z0-9]+", "-", sLower).strip("-")


def fnEnsureStepIds(dictWorkflow):
    """Assign a stable ``sStepId`` to any step lacking one; keep existing.

    ``sStepId`` is the identity primitive behind symbolic cross-step
    references (``{step:<id>.<stem>}``). Unlike ``sLabel`` — which is a
    derived, per-type-sequential field recomputed on every load — an
    id is assigned ONCE, persisted in ``workflow.json``, and NEVER
    regenerated, so a rename, insertion, or reorder leaves every
    reference intact. The id is a readable kebab slug of the step name,
    disambiguated with a numeric suffix on collision, falling back to
    ``step-<n>`` for a nameless step. Idempotent and non-destructive:
    a step that already carries an ``sStepId`` is untouched, so this is
    safe to call on both the load and save paths.
    """
    listSteps = dictWorkflow.get("listSteps", []) or []
    setUsed = {
        dictStep["sStepId"] for dictStep in listSteps
        if isinstance(dictStep, dict) and dictStep.get("sStepId")
    }
    for iIndex, dictStep in enumerate(listSteps):
        if not isinstance(dictStep, dict) or dictStep.get("sStepId"):
            continue
        sBase = _fsSlugFromStepName(dictStep.get("sName")) or f"step-{iIndex + 1}"
        sCandidate = sBase
        iSuffix = 2
        while sCandidate in setUsed:
            sCandidate = f"{sBase}-{iSuffix}"
            iSuffix += 1
        dictStep["sStepId"] = sCandidate
        setUsed.add(sCandidate)


def fnNormalizeSceneReferences(dictStep):
    """Replace deprecated {SceneNN.var} tokens with {StepNN.var}.

    Runs during the v0->v1 stage, before the v7->v8 key rename, so it
    scans the historical ``saDataFiles`` key alongside the current
    ``saOutputDataFiles`` name; absent keys are skipped.
    """
    for sKey in (
        "saDataCommands", "saPlotCommands", "saTestCommands",
        "saSetupCommands", "saCommands", "saDependencies",
        "saDataFiles", "saOutputDataFiles", "saPlotFiles",
        "saOutputFiles",
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
    ``dictOutputDataFileCategories``. Archive files were the ones pushed
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
        for sArrayKey in ("saDataFiles", "saOutputDataFiles", "saPlotFiles"):
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
    ``saOutputDataFiles``, and ``saPlotFiles``. Falls back to inferring the
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
            "saOutputFiles", "saDataFiles", "saOutputDataFiles",
            "saPlotFiles",
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


def fnMigrateAbsoluteTestPaths(dictWorkflow, sProjectRepoPath):
    """Strip absolute container prefixes from ``dictTests`` paths.

    Test generation resolves the step directory to container-absolute
    form for Docker file writes; before this migration the resulting
    ``sFilePath``/``sStandardsPath`` values were persisted verbatim,
    so workflows carry a mix of repo-relative and container-absolute
    test paths. One convention (repo-relative) keeps the manifest
    envelope, the canonical tracked-files set, and sync keys in
    agreement. Also invoked outside the versioned ladder after test
    generation, so it must stay idempotent.
    """
    sRoot = (sProjectRepoPath or "").rstrip("/")
    for dictStep in dictWorkflow.get("listSteps", []):
        sStepRoot = sRoot or _fsInferRepoRootFromAbsoluteDir(
            dictStep.get("sDirectory", "")
        )
        if not sStepRoot:
            continue
        dictTests = dictStep.get("dictTests", {})
        if not isinstance(dictTests, dict):
            continue
        for dictCategory in dictTests.values():
            if not isinstance(dictCategory, dict):
                continue
            for sKey in ("sFilePath", "sStandardsPath"):
                sPath = dictCategory.get(sKey, "")
                if isinstance(sPath, str) and sPath:
                    dictCategory[sKey] = _fsStripRoot(sPath, sStepRoot)


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
    """Return 'archive' or 'supporting' for a data or plot file.

    Reads the historical ``dictDataFileCategories`` key alongside the
    current name because this runs during the v0->v1 stage, before
    the v7->v8 key rename.
    """
    dictPlot = dictStep.get("dictPlotFileCategories", {})
    if sFilePath in dictPlot:
        return dictPlot[sFilePath]
    for sCategoriesKey in (
        "dictDataFileCategories", "dictOutputDataFileCategories",
    ):
        dictData = dictStep.get(sCategoriesKey, {})
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
        "sZenodoLastPushedEndpoint": "",
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


def _fnMigrateV3ToV4(dictWorkflow, sProjectRepoPath):
    """Replace the legacy ``bVaibified`` flag with the AICS ladder.

    Drops any persisted ``bVaibified`` key (it was historically derived
    on the frontend and never authoritative; forked or hand-edited
    workflows occasionally carry the field anyway). Drops any
    pre-existing ``iAICSLevel`` so the post-load derivation hook in
    ``workflowManager.fdictLoadWorkflowFromContainer`` recomputes the
    integer against the current per-step verification state rather
    than trusting a stale value. The derivation itself runs after
    state.json is merged in, so this migrator must not try to compute
    the level here.
    """
    dictWorkflow.pop("bVaibified", None)
    dictWorkflow.pop("iAICSLevel", None)


def _fnMigrateV4ToV5(dictWorkflow, sProjectRepoPath):
    """Normalize container-absolute ``dictTests`` paths to repo-relative.

    Cleans documents written while test generation persisted the
    absolute form (see ``fnMigrateAbsoluteTestPaths``); the generator
    now normalizes at save time, so migrated documents stay clean.
    """
    fnMigrateAbsoluteTestPaths(dictWorkflow, sProjectRepoPath)


def _fnMigrateV5ToV6(dictWorkflow, sProjectRepoPath):
    """Assign a stable ``sStepId`` to every step lacking one.

    Introduces the identity primitive behind symbolic cross-step
    references. One-shot for existing documents; new steps acquire an
    id at creation and via the save-path safety net.
    """
    fnEnsureStepIds(dictWorkflow)


_T_REFERENCE_BEARING_STEP_FIELDS = (
    "saDataCommands", "saTestCommands", "saPlotCommands", "saPlotFiles",
    "saDependencies", "saSetupCommands", "saCommands", "saOutputFiles",
)


def fnRewritePositionalToSymbolic(dictWorkflow):
    """Rewrite deprecated positional ``{StepNN.stem}`` cross-step tokens
    to the canonical symbolic ``{step:<sStepId>.stem}`` form.

    Idempotent: symbolic tokens contain no ``{StepNN.`` match and are
    untouched. A positional token whose target index is out of range,
    or whose target step has no ``sStepId``, is left as-is — there is
    nothing to resolve it to — so this never fabricates an id. Callers
    must run :func:`fnEnsureStepIds` first so every in-range target
    carries an id.
    """
    listSteps = dictWorkflow.get("listSteps", []) or []

    def fnReplace(resultMatch):
        iIndex = int(resultMatch.group(1)) - 1
        sVariable = resultMatch.group(2)
        if 0 <= iIndex < len(listSteps):
            dictTarget = listSteps[iIndex]
            sId = (
                dictTarget.get("sStepId")
                if isinstance(dictTarget, dict) else None
            )
            if sId:
                return "{step:" + sId + "." + sVariable + "}"
        return resultMatch.group(0)

    for dictStep in listSteps:
        if not isinstance(dictStep, dict):
            continue
        for sKey in _T_REFERENCE_BEARING_STEP_FIELDS:
            listValues = dictStep.get(sKey)
            if not listValues:
                continue
            dictStep[sKey] = [
                re.sub(r"\{Step(\d+)\.([^}]+)\}", fnReplace, s)
                if isinstance(s, str) else s
                for s in listValues
            ]


def _fnMigrateV6ToV7(dictWorkflow, sProjectRepoPath):
    """Rewrite positional cross-step tokens to the symbolic form.

    Deprecates ``{StepNN.stem}`` in favor of ``{step:<id>.stem}``.
    Depends on ``sStepId`` being assigned, which the v5->v6 migrator
    guarantees by running first.
    """
    fnRewritePositionalToSymbolic(dictWorkflow)


def _fnMigrateV7ToV8(dictWorkflow, sProjectRepoPath):
    """Rename the output-data key and retire the legacy outputs bucket.

    ``saDataFiles`` becomes ``saOutputDataFiles`` for symmetry with
    the input-data declaration, and the legacy general-outputs bucket
    ``saOutputFiles`` is merged into it (deduplicated, order
    preserved) and removed. ``dictDataFileCategories`` becomes
    ``dictOutputDataFileCategories``.
    """
    for dictStep in dictWorkflow.get("listSteps", []):
        if not isinstance(dictStep, dict):
            continue
        listMerged = list(dictStep.get("saOutputDataFiles", []) or [])
        for sLegacyKey in ("saDataFiles", "saOutputFiles"):
            for sPath in dictStep.pop(sLegacyKey, []) or []:
                if sPath not in listMerged:
                    listMerged.append(sPath)
        dictStep["saOutputDataFiles"] = listMerged
        if "dictDataFileCategories" in dictStep:
            dictStep["dictOutputDataFileCategories"] = dictStep.pop(
                "dictDataFileCategories"
            )


T_MIGRATORS = (
    (0, _fnMigrateV0ToV1),
    (1, _fnMigrateV1ToV2),
    (2, _fnMigrateV2ToV3),
    (3, _fnMigrateV3ToV4),
    (4, _fnMigrateV4ToV5),
    (5, _fnMigrateV5ToV6),
    (6, _fnMigrateV6ToV7),
    (7, _fnMigrateV7ToV8),
)
