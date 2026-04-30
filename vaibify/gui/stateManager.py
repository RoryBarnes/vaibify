"""Load, save, split, and merge ``.vaibify/state.json`` — per-machine runtime state.

Vaibify's workflow.json is the declarative source of truth: step
structure, paths, commands, sync metadata. Run-time results
(``dictVerification``, ``dictRunStats``, the
``bArchiveTrackingMigrated`` flag) are inherently per-machine and
were producing merge conflicts whenever the same workflow was driven
from more than one host. This module is the home for that state.

State lives at ``<sProjectRepoPath>/.vaibify/state.json``, gitignored
via an auto-managed ``<sProjectRepoPath>/.vaibify/.gitignore``. The
in-memory dict the dashboard works with is the *merged* shape — load
reads both files and merges the state back into the workflow dict so
existing route handlers and the frontend keep seeing one shape. Save
splits them again before persisting.

When ``state.json`` is missing on a fresh checkout, ``fdictBootstrapStateFromMarkers``
synthesizes verification badges from the committed test-markers
directory: a step with a marker whose ``dictOutputHashes`` match the
on-disk file hashes is reported as ``passed-from-marker``; mismatches
are flagged as ``outputs-changed``; missing outputs as
``outputs-missing``. ``sUser`` always starts empty — user attestation
is explicitly per-machine.
"""

import copy
import datetime
import json
import posixpath


__all__ = [
    "I_CURRENT_STATE_SCHEMA_VERSION",
    "S_STATE_FILE_RELATIVE",
    "S_VAIBIFY_GITIGNORE_BODY",
    "S_VAIBIFY_GITIGNORE_RELATIVE",
    "T_STATEFUL_STEP_FIELDS",
    "T_STATEFUL_TOP_FIELDS",
    "fdictBootstrapStateFromMarkers",
    "fdictBuildEmptyState",
    "fdictLoadStateFromContainer",
    "fnEnsureVaibifyGitignore",
    "fnMergeStateIntoWorkflow",
    "fnSaveStateToContainer",
    "fsGitignorePathFromRepo",
    "fsStatePathFromRepo",
    "ftSplitMergedDict",
]


I_CURRENT_STATE_SCHEMA_VERSION = 1
S_STATE_FILE_RELATIVE = ".vaibify/state.json"
S_VAIBIFY_GITIGNORE_RELATIVE = ".vaibify/.gitignore"
S_TEST_MARKERS_RELATIVE = ".vaibify/test_markers"
S_VAIBIFY_GITIGNORE_BODY = (
    "# Auto-managed by vaibify. Do not edit by hand.\n"
    "state.json\n"
)

T_STATEFUL_STEP_FIELDS = ("dictVerification", "dictRunStats")
T_STATEFUL_TOP_FIELDS = ("bArchiveTrackingMigrated",)


def fsStatePathFromRepo(sProjectRepoPath):
    """Return the absolute container path of state.json for a project repo."""
    if not sProjectRepoPath:
        return ""
    return posixpath.join(sProjectRepoPath, S_STATE_FILE_RELATIVE)


def fsGitignorePathFromRepo(sProjectRepoPath):
    """Return the absolute container path of .vaibify/.gitignore."""
    if not sProjectRepoPath:
        return ""
    return posixpath.join(
        sProjectRepoPath, S_VAIBIFY_GITIGNORE_RELATIVE,
    )


def fdictBuildEmptyState():
    """Return a fresh, empty state dict at the current schema version."""
    return {
        "iStateSchemaVersion": I_CURRENT_STATE_SCHEMA_VERSION,
        "sLastUpdated": _fsCurrentUtcIso(),
        "dictStepState": {},
    }


def fdictLoadStateFromContainer(
    connectionDocker, sContainerId, sStatePath,
):
    """Read state.json from the container; return None when absent.

    Returning ``None`` (rather than an empty dict) is the signal that
    the caller should bootstrap from markers before merging.
    """
    if not sStatePath:
        return fdictBuildEmptyState()
    try:
        baContent = connectionDocker.fbaFetchFile(
            sContainerId, sStatePath,
        )
    except FileNotFoundError:
        return None
    try:
        return json.loads(baContent.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return None


def fnSaveStateToContainer(
    connectionDocker, sContainerId, sStatePath, dictState,
):
    """Serialize the state dict and write it to the container."""
    if not sStatePath:
        return
    dictPersisted = dict(dictState)
    dictPersisted["sLastUpdated"] = _fsCurrentUtcIso()
    sJson = json.dumps(dictPersisted, indent=2) + "\n"
    connectionDocker.fnWriteFile(
        sContainerId, sStatePath, sJson.encode("utf-8"),
    )


def fnMergeStateIntoWorkflow(dictWorkflow, dictState):
    """Copy state.json fields back into the in-memory workflow dict.

    No-op when ``dictState`` is None. Steps without a matching
    ``dictStepState`` entry keep whatever stateful fields the loaded
    workflow.json happened to carry — the migration v2→v3 owns the
    one-shot extraction; this routine is the steady-state merger.
    """
    if dictState is None:
        return
    dictStepState = dictState.get("dictStepState", {}) or {}
    for dictStep in dictWorkflow.get("listSteps", []):
        sDirectory = dictStep.get("sDirectory", "")
        dictForStep = dictStepState.get(sDirectory, {})
        for sKey in T_STATEFUL_STEP_FIELDS:
            if sKey in dictForStep:
                dictStep[sKey] = dictForStep[sKey]
    for sKey in T_STATEFUL_TOP_FIELDS:
        if sKey in dictState:
            dictWorkflow[sKey] = dictState[sKey]


def ftSplitMergedDict(dictWorkflow):
    """Return ``(declarativeDict, stateDict)`` from a merged workflow.

    The declarative dict is what gets written to ``workflow.json``
    (no per-step ``dictVerification`` / ``dictRunStats``, no
    ``bArchiveTrackingMigrated``, no transient ``sLabel``). The state
    dict is what gets written to ``state.json``.
    """
    dictDeclarative = copy.deepcopy(dictWorkflow)
    dictDeclarative.pop("sProjectRepoPath", None)
    dictStepState = {}
    for dictStep in dictDeclarative.get("listSteps", []):
        sDirectory = dictStep.get("sDirectory", "")
        dictExtracted = {}
        for sKey in T_STATEFUL_STEP_FIELDS:
            if sKey in dictStep:
                dictExtracted[sKey] = dictStep.pop(sKey)
        dictStep.pop("sLabel", None)
        if dictExtracted and sDirectory:
            dictStepState[sDirectory] = dictExtracted
    dictState = fdictBuildEmptyState()
    dictState["dictStepState"] = dictStepState
    for sKey in T_STATEFUL_TOP_FIELDS:
        if sKey in dictDeclarative:
            dictState[sKey] = dictDeclarative.pop(sKey)
    return dictDeclarative, dictState


def fnEnsureVaibifyGitignore(
    connectionDocker, sContainerId, sProjectRepoPath,
):
    """Write ``.vaibify/.gitignore`` when missing so state.json is local-only."""
    sPath = fsGitignorePathFromRepo(sProjectRepoPath)
    if not sPath:
        return
    try:
        connectionDocker.fbaFetchFile(sContainerId, sPath)
        return
    except FileNotFoundError:
        pass
    connectionDocker.fnWriteFile(
        sContainerId, sPath, S_VAIBIFY_GITIGNORE_BODY.encode("utf-8"),
    )


def fdictBootstrapStateFromMarkers(
    connectionDocker, sContainerId, dictWorkflow, sProjectRepoPath,
):
    """Synthesize state from committed test-markers and on-disk hashes.

    Run only when ``state.json`` is absent on a fresh checkout.
    Produces three new ``dictVerification`` values per category:
    ``passed-from-marker`` when marker hashes match the on-disk
    files, ``outputs-changed`` when at least one hash differs, and
    ``outputs-missing`` when expected outputs aren't on disk. ``sUser``
    is always empty — verification by-eye is per-machine.
    """
    if not sProjectRepoPath:
        return fdictBuildEmptyState()
    listSteps = dictWorkflow.get("listSteps", []) or []
    listMarkers = _flistFetchMarkers(
        connectionDocker, sContainerId, sProjectRepoPath, listSteps,
    )
    listAllOutputs = _flistAllMarkerOutputs(listMarkers)
    dictOnDiskHashes = _fdictHashOnDiskOutputs(
        connectionDocker, sContainerId,
        listAllOutputs, sProjectRepoPath,
    )
    dictStepState = {}
    for sDirectory, dictMarker in listMarkers:
        if dictMarker is None:
            continue
        dictVerification = _fdictVerificationFromMarker(
            dictMarker, dictOnDiskHashes,
        )
        dictStepState[sDirectory] = {
            "dictVerification": dictVerification,
            "dictRunStats": {},
        }
    dictState = fdictBuildEmptyState()
    dictState["dictStepState"] = dictStepState
    return dictState


def _flistFetchMarkers(
    connectionDocker, sContainerId, sProjectRepoPath, listSteps,
):
    """Return ``[(sDirectory, dictMarker_or_None), ...]`` for every step."""
    listResult = []
    for dictStep in listSteps:
        sDirectory = dictStep.get("sDirectory", "")
        if not sDirectory:
            continue
        sMarkerPath = posixpath.join(
            sProjectRepoPath, S_TEST_MARKERS_RELATIVE,
            sDirectory + ".json",
        )
        dictMarker = _fdictReadMarker(
            connectionDocker, sContainerId, sMarkerPath,
        )
        listResult.append((sDirectory, dictMarker))
    return listResult


def _fdictReadMarker(connectionDocker, sContainerId, sMarkerPath):
    """Parse one marker file; return None when missing or malformed."""
    try:
        baContent = connectionDocker.fbaFetchFile(
            sContainerId, sMarkerPath,
        )
    except FileNotFoundError:
        return None
    try:
        return json.loads(baContent.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return None


def _flistAllMarkerOutputs(listMarkers):
    """Flatten all marker dictOutputHashes paths into one ordered list."""
    listResult = []
    setSeen = set()
    for _, dictMarker in listMarkers:
        if dictMarker is None:
            continue
        for sPath in dictMarker.get("dictOutputHashes", {}) or {}:
            if sPath in setSeen:
                continue
            setSeen.add(sPath)
            listResult.append(sPath)
    return listResult


def _fdictHashOnDiskOutputs(
    connectionDocker, sContainerId,
    listRepoRelPaths, sProjectRepoPath,
):
    """Compute on-disk SHAs via the existing container-side helper.

    The helper is in ``containerGit``; we import lazily so this
    module remains a leaf module from the dashboard's perspective.
    """
    if not listRepoRelPaths:
        return {}
    from . import containerGit
    return containerGit.fdictComputeBlobShasInContainer(
        connectionDocker, sContainerId, listRepoRelPaths,
        sWorkspace=sProjectRepoPath,
    )


def _fdictVerificationFromMarker(dictMarker, dictOnDiskHashes):
    """Return a synthesized dictVerification for one step."""
    dictExpected = dictMarker.get("dictOutputHashes", {}) or {}
    sHashStatus = _fsStatusFromHashes(dictExpected, dictOnDiskHashes)
    listChanged = _flistChangedOutputs(dictExpected, dictOnDiskHashes)
    iExitStatus = dictMarker.get("iExitStatus", 0)
    dictVerification = {
        "sUser": "",
        "sLastTestRun": dictMarker.get("sRunAtUtc", ""),
        "listModifiedFiles": listChanged,
    }
    dictCategories = dictMarker.get("dictCategories", {}) or {}
    for sCategory, dictCounts in dictCategories.items():
        sKey = "s" + sCategory[:1].upper() + sCategory[1:]
        dictVerification[sKey] = _fsCategoryStatus(
            sHashStatus, iExitStatus, dictCounts or {},
        )
    if "sUnitTest" not in dictVerification:
        dictVerification["sUnitTest"] = _fsCategoryStatus(
            sHashStatus, iExitStatus, {},
        )
    return dictVerification


def _fsCategoryStatus(sHashStatus, iExitStatus, dictCounts):
    """Combine hash status, marker exit code, and per-category counts.

    Hash mismatches (``outputs-missing`` / ``outputs-changed``) win
    because they signal that the marker no longer describes the on-disk
    state. Otherwise a non-zero ``iExitStatus`` or any ``iFailed`` in the
    category demotes the badge to ``failed`` rather than letting a
    failed run masquerade as ``passed-from-marker``.
    """
    if sHashStatus in ("outputs-missing", "outputs-changed"):
        return sHashStatus
    if sHashStatus == "untested":
        return "untested"
    if iExitStatus != 0:
        return "failed"
    if int(dictCounts.get("iFailed", 0) or 0) > 0:
        return "failed"
    return "passed-from-marker"


def _fsStatusFromHashes(dictExpected, dictOnDisk):
    """Classify a step as passed-from-marker / outputs-changed / outputs-missing."""
    if not dictExpected:
        return "untested"
    bAnyMissing = False
    bAnyChanged = False
    for sPath, sExpectedSha in dictExpected.items():
        sActual = dictOnDisk.get(sPath, "")
        if not sActual:
            bAnyMissing = True
            continue
        if sActual != sExpectedSha:
            bAnyChanged = True
    if bAnyMissing:
        return "outputs-missing"
    if bAnyChanged:
        return "outputs-changed"
    return "passed-from-marker"


def _flistChangedOutputs(dictExpected, dictOnDisk):
    """Return repo-relative paths whose on-disk hash differs from the marker."""
    listResult = []
    for sPath, sExpectedSha in dictExpected.items():
        sActual = dictOnDisk.get(sPath, "")
        if sActual and sActual != sExpectedSha:
            listResult.append(sPath)
    return sorted(listResult)


def _fsCurrentUtcIso():
    """Return the current UTC timestamp in ISO-8601 with seconds precision."""
    return datetime.datetime.now(
        datetime.timezone.utc,
    ).strftime("%Y-%m-%dT%H:%M:%SZ")
