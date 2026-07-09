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
import logging
import posixpath


logger = logging.getLogger(__name__)


__all__ = [
    "I_CURRENT_STATE_SCHEMA_VERSION",
    "S_STATE_FILE_RELATIVE",
    "S_VAIBIFY_GITIGNORE_BODY",
    "S_VAIBIFY_GITIGNORE_RELATIVE",
    "T_STATEFUL_STEP_FIELDS",
    "T_STATEFUL_TOP_FIELDS",
    "fbRatchetLevelHighWater",
    "fdictBootstrapStateFromMarkers",
    "fdictBuildEmptyState",
    "fdictLoadStateFromContainer",
    "fnEnsureVaibifyGitignore",
    "fnMergeStateIntoWorkflow",
    "fnSaveStateToContainer",
    "fsGitignorePathFromRepo",
    "fsStatePathFromRepo",
    "ftSplitMergedDict",
    "ftLoadStateWithStatus",
]


# Schema v2 adds the add-only AICS level high-water fields
# (``dictLevelHighWater`` per step, ``dictWorkflowLevelHighWater`` at
# the top level). Version 1 files load unchanged: the tuple-generic
# merge/split copies only keys that are present, so an absent
# high-water dict simply means the level was never attained. No
# migration code exists or is needed in either direction.
I_CURRENT_STATE_SCHEMA_VERSION = 2
S_STATE_FILE_RELATIVE = ".vaibify/state.json"
S_VAIBIFY_GITIGNORE_RELATIVE = ".vaibify/.gitignore"
S_TEST_MARKERS_RELATIVE = ".vaibify/test_markers"
S_VAIBIFY_GITIGNORE_BODY = (
    "# Auto-managed by vaibify. Do not edit by hand.\n"
    "state.json\n"
)

T_STATEFUL_STEP_FIELDS = (
    "dictVerification", "dictRunStats", "dictLevelHighWater",
)
T_STATEFUL_TOP_FIELDS = (
    "bArchiveTrackingMigrated", "iAICSLevel",
    "dictWorkflowLevelHighWater", "bWarnedHundredSteps",
)


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
        "bWarnedHundredSteps": False,
    }


def fdictLoadStateFromContainer(
    connectionDocker, sContainerId, sStatePath,
):
    """Read state.json with .bak fallback and corrupt-file quarantine.

    Returns the parsed state dict, or ``None`` when both the primary
    file and its sibling ``.bak`` checkpoint are missing or
    unparseable. A primary file that fails to parse is renamed to
    ``state.json.corrupted-<timestamp>`` before falling back so a
    human can hand-recover its contents — silently overwriting via
    bootstrap would be unrecoverable data loss.

    See :func:`ftLoadStateWithStatus` for callers that need to
    distinguish the recovery path from a clean load.
    """
    dictState, _sStatus = ftLoadStateWithStatus(
        connectionDocker, sContainerId, sStatePath,
    )
    return dictState


def _fbQuarantineIfCorrupt(
    sStatus, connectionDocker, sContainerId, sPath,
):
    """Quarantine a corrupt state file; return True if a quarantine occurred."""
    if sStatus != "corrupt":
        return False
    _fnQuarantineCorruptStateFile(
        connectionDocker, sContainerId, sPath,
    )
    return True


def ftLoadStateWithStatus(
    connectionDocker, sContainerId, sStatePath,
):
    """Return ``(dictState_or_None, sStatus)``.

    ``sStatus`` is one of:
    - ``"loaded"``: the primary state.json parsed cleanly.
    - ``"loaded-from-bak"``: primary missing or corrupt; ``.bak``
      was used. Caller should warn the user that their last save
      did not land cleanly.
    - ``"missing"``: neither file present; caller should bootstrap
      and save (this is the fresh-checkout case).
    - ``"corrupted"``: at least one file failed to parse and was
      quarantined; if ``dictState`` is None the caller is forced to
      bootstrap, but the user has already been warned and the
      corrupted bytes are still on disk for recovery.
    """
    if not sStatePath:
        return fdictBuildEmptyState(), "loaded"
    sPrimaryStatus, dictPrimary = _ftupleTryLoadStateFile(
        connectionDocker, sContainerId, sStatePath,
    )
    if sPrimaryStatus == "parsed":
        return dictPrimary, "loaded"
    bQuarantined = _fbQuarantineIfCorrupt(
        sPrimaryStatus, connectionDocker, sContainerId, sStatePath,
    )
    sBakPath = _fsBakPathFor(sStatePath)
    sBakStatus, dictBak = _ftupleTryLoadStateFile(
        connectionDocker, sContainerId, sBakPath,
    )
    if sBakStatus == "parsed":
        if bQuarantined:
            logger.warning(
                "state.json was corrupt; recovered from %s", sBakPath,
            )
        return dictBak, "loaded-from-bak"
    bBakQuarantined = _fbQuarantineIfCorrupt(
        sBakStatus, connectionDocker, sContainerId, sBakPath,
    )
    bQuarantined = bQuarantined or bBakQuarantined
    if bQuarantined:
        return None, "corrupted"
    return None, "missing"


def _ftupleTryLoadStateFile(connectionDocker, sContainerId, sPath):
    """Return ``(sStatus, dictParsedOrNone)`` for a single file.

    ``sStatus`` is ``"missing"``, ``"corrupt"``, or ``"parsed"``.
    The corrupt branch separates a present-but-broken file (which
    needs quarantine) from a simply absent one (which does not).
    """
    try:
        baContent = connectionDocker.fbaFetchFile(sContainerId, sPath)
    except FileNotFoundError:
        return ("missing", None)
    try:
        return ("parsed", json.loads(baContent.decode("utf-8")))
    except (ValueError, UnicodeDecodeError):
        return ("corrupt", None)


def _fsBakPathFor(sStatePath):
    """Return the sibling ``.bak`` checkpoint path for state.json."""
    return sStatePath + ".bak"


def _fsTmpPathFor(sStatePath):
    """Return the sibling ``.tmp`` path used during atomic write."""
    return sStatePath + ".tmp"


def _fnQuarantineCorruptStateFile(
    connectionDocker, sContainerId, sPath,
):
    """Rename a corrupt state file out of the way for human recovery.

    The destination is ``<sPath>.corrupted-<UTC ISO timestamp>`` so
    repeated quarantines never collide. Failure is logged and
    swallowed — the bootstrap path must still proceed even when the
    container shell rejects the rename.
    """
    from .pipelineUtils import fsShellQuote
    sStamp = datetime.datetime.now(
        datetime.timezone.utc,
    ).strftime("%Y%m%dT%H%M%SZ")
    sQuarantine = f"{sPath}.corrupted-{sStamp}"
    sCommand = (
        f"mv {fsShellQuote(sPath)} {fsShellQuote(sQuarantine)}"
    )
    try:
        iExit, sOutput = connectionDocker.ftResultExecuteCommand(
            sContainerId, sCommand,
        )
    except Exception as error:
        logger.warning(
            "Quarantine of %s failed (%s); bootstrap will proceed.",
            sPath, error,
        )
        return
    if iExit != 0:
        logger.warning(
            "Quarantine of %s exited %d: %s",
            sPath, iExit, sOutput,
        )
        return
    logger.warning(
        "Corrupt state file %s quarantined to %s; "
        "by-eye verifications and other state were rebuilt.",
        sPath, sQuarantine,
    )


def fnSaveStateToContainer(
    connectionDocker, sContainerId, sStatePath, dictState,
):
    """Serialize and persist the state dict atomically with a checkpoint.

    A naive overwrite leaves a torn file on the disk if the host
    crashes mid-write — exactly the failure mode that wiped sUser
    values for marker-tested steps when a system crash truncated
    state.json. This routine:

    1. Writes the serialized state to a sibling ``.tmp`` file.
    2. Best-effort copies the prior ``state.json`` to ``state.json.bak``
       so a checkpoint is preserved.
    3. Atomically renames the ``.tmp`` over ``state.json``.

    The order matters: copy must precede the rename, otherwise
    ``state.json.bak`` would only ever reflect the just-written state
    and provide no fallback. If step 3 fails, the prior ``state.json``
    is intact and the next save retries cleanly.
    """
    if not sStatePath:
        return
    dictPersisted = dict(dictState)
    dictPersisted["sLastUpdated"] = _fsCurrentUtcIso()
    sJson = json.dumps(dictPersisted, indent=2) + "\n"
    sTempPath = _fsTmpPathFor(sStatePath)
    sBakPath = _fsBakPathFor(sStatePath)
    connectionDocker.fnWriteFile(
        sContainerId, sTempPath, sJson.encode("utf-8"),
    )
    _fnCheckpointPriorState(
        connectionDocker, sContainerId, sStatePath, sBakPath,
    )
    _fnAtomicInstallTempFile(
        connectionDocker, sContainerId, sTempPath, sStatePath,
    )


def _fnCheckpointPriorState(
    connectionDocker, sContainerId, sStatePath, sBakPath,
):
    """Copy the current state.json to state.json.bak if it exists.

    Best-effort: a missing primary (first save on a fresh checkout)
    is silently skipped. A failed copy is logged but does not abort
    the save — the primary write still proceeds, the next save will
    refresh the checkpoint.
    """
    from .pipelineUtils import fsShellQuote
    sCommand = (
        f"if [ -f {fsShellQuote(sStatePath)} ]; "
        f"then cp -f {fsShellQuote(sStatePath)} "
        f"{fsShellQuote(sBakPath)}; fi"
    )
    try:
        iExit, sOutput = connectionDocker.ftResultExecuteCommand(
            sContainerId, sCommand,
        )
    except Exception as error:
        logger.warning(
            "state.json checkpoint copy failed (%s); "
            "next save will retry.", error,
        )
        return
    if iExit != 0:
        logger.warning(
            "state.json checkpoint copy exited %d: %s",
            iExit, sOutput,
        )


def _fnAtomicInstallTempFile(
    connectionDocker, sContainerId, sTempPath, sStatePath,
):
    """POSIX-atomic rename of state.json.tmp over state.json."""
    from .pipelineUtils import fsShellQuote
    sCommand = (
        f"mv -f {fsShellQuote(sTempPath)} "
        f"{fsShellQuote(sStatePath)}"
    )
    iExit, sOutput = connectionDocker.ftResultExecuteCommand(
        sContainerId, sCommand,
    )
    if iExit != 0:
        raise OSError(
            f"Atomic rename of {sTempPath} to {sStatePath} "
            f"failed (exit {iExit}): {sOutput}"
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


def fbRatchetLevelHighWater(
    dictWorkflow, dictStepLevelStates, dictWorkflowScopeStates,
):
    """Stamp first-attainment timestamps for newly attained AICS levels.

    ``dictStepLevelStates`` maps ``iStepIndex`` to
    ``{"s1": dictCell, "s2": dictCell, "s3": dictCell}`` where each
    cell carries ``sState`` in ``("not-started", "unassessed",
    "none", "partial", "attained", "unknown")``;
    ``dictWorkflowScopeStates`` is one such
    cell dict for the workflow header row. The ratchet is ADD-ONLY:
    a level that regresses never loses its recorded first-attainment
    timestamp — regression memory is the feature. ONLY ``attained``
    stamps; every other state (including ``unknown`` and ``partial``)
    stamps nothing. Returns True iff any timestamp was newly recorded.
    """
    sNow = _fsCurrentUtcIso()
    bChanged = False
    listSteps = dictWorkflow.get("listSteps", []) or []
    for iStepIndex, dictStep in enumerate(listSteps):
        if not isinstance(dictStep, dict):
            continue
        dictStates = (dictStepLevelStates or {}).get(iStepIndex) or {}
        bChanged = _fbStampAttainedLevels(
            dictStep, "dictLevelHighWater", dictStates, sNow,
        ) or bChanged
    bChanged = _fbStampAttainedLevels(
        dictWorkflow, "dictWorkflowLevelHighWater",
        dictWorkflowScopeStates or {}, sNow,
    ) or bChanged
    return bChanged


def _fbStampAttainedLevels(dictHolder, sFieldKey, dictLevelStates, sNow):
    """Record ``sNow`` for each newly attained level; never overwrite.

    Levels already carrying a timestamp keep it (re-attainment is not
    a new event); non-``attained`` states stamp nothing. The holder's
    high-water dict is created lazily so an all-grey step never gains
    an empty field.
    """
    bChanged = False
    for sLevel in ("1", "2", "3"):
        if _fsLevelCellState(dictLevelStates.get("s" + sLevel)) != (
            "attained"
        ):
            continue
        dictHighWater = dictHolder.setdefault(sFieldKey, {})
        if sLevel in dictHighWater:
            continue
        dictHighWater[sLevel] = sNow
        bChanged = True
    return bChanged


def _fsLevelCellState(dictCell):
    """Return a level cell's ``sState``; tolerate the legacy string form."""
    if isinstance(dictCell, dict):
        return dictCell.get("sState")
    return dictCell


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
    from .fileStatusManager import fsWorkflowSlugFromPath
    if not sProjectRepoPath:
        return fdictBuildEmptyState()
    sWorkflowSlug = fsWorkflowSlugFromPath(
        dictWorkflow.get("sPath", ""),
    )
    if not sWorkflowSlug:
        return fdictBuildEmptyState()
    listSteps = dictWorkflow.get("listSteps", []) or []
    listMarkers = _flistFetchMarkers(
        connectionDocker, sContainerId, sProjectRepoPath,
        sWorkflowSlug, listSteps,
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
    connectionDocker, sContainerId, sProjectRepoPath,
    sWorkflowSlug, listSteps,
):
    """Return ``[(sDirectory, dictMarker_or_None), ...]`` for every step.

    Marker filenames use the canonical ``fsMarkerNameFromStepDirectory``
    encoding (slashes → underscores) so a nested step directory like
    ``Step01/sub`` resolves to the same ``Step01_sub.json`` the conftest
    writes — never a literal ``Step01/sub.json``.
    """
    from .fileStatusManager import fsMarkerNameFromStepDirectory
    listResult = []
    for dictStep in listSteps:
        sDirectory = dictStep.get("sDirectory", "")
        if not sDirectory:
            continue
        sMarkerPath = posixpath.join(
            sProjectRepoPath, S_TEST_MARKERS_RELATIVE,
            sWorkflowSlug,
            fsMarkerNameFromStepDirectory(sDirectory),
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
    """Return a synthesized dictVerification for one step.

    Thin delegate to the canonical truth-derivation module. Lives
    here as a back-compat seam: this is the historical name the
    bootstrap path uses. New callers should reach for
    ``truthDerivation.fdictComputeTestAxes`` directly.
    """
    from . import truthDerivation
    listCategories = [s for s, _ in truthDerivation.T_TEST_CATEGORY_AXIS_KEYS]
    return truthDerivation.fdictComputeTestAxes(
        dictMarker, dictOnDiskHashes, listCategories,
    )


def _fsCurrentUtcIso():
    """Return the current UTC timestamp in ISO-8601 with seconds precision."""
    return datetime.datetime.now(
        datetime.timezone.utc,
    ).strftime("%Y-%m-%dT%H:%M:%SZ")
