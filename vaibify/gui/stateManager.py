"""Load, save, split, and merge ``.vaibify/state.json`` — per-machine runtime state.

Vaibify's project.json is the declarative source of truth: step
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
    "S_QUARANTINE_LIST_KEY",
    "S_VAIBIFY_GITIGNORE_RELATIVE",
    "S_WORKFLOW_STATE_KEY",
    "T_STATEFUL_STEP_FIELDS",
    "T_STATEFUL_TOP_FIELDS",
    "fbRatchetLevelHighWater",
    "fdictBootstrapStateFromMarkers",
    "fdictBuildEmptyState",
    "fdictInstallWorkflowSection",
    "fbDocumentNeedsMigration",
    "fdictMigrateStateDocument",
    "fnAppendQuarantineRecord",
    "fdictSectionForWorkflow",
    "fdictLoadStateFromContainer",
    "fdictMergeRunResultsIntoState",
    "fnEnsureVaibifyGitignore",
    "fnMergeStateIntoWorkflow",
    "fnSaveStateToContainer",
    "fsGitignorePathFromRepo",
    "fsStatePathFromRepo",
    "fsWorkflowKeyFromPath",
    "ftSplitMergedDict",
    "ftLoadStateWithStatus",
]


# Schema v2 adds the add-only PROOF level high-water fields
# (``dictLevelHighWater`` per step, ``dictWorkflowLevelHighWater`` at
# the top level). Version 1 files load unchanged: the tuple-generic
# merge/split copies only keys that are present, so an absent
# high-water dict simply means the level was never attained. No
# migration code exists or is needed in either direction.
# Schema v3 namespaces per-workflow state by the project file's
# repo-relative path. state.json is repo-scoped and a repo may hold
# several projects, but v2 held one flat ``dictStepState`` keyed by
# directory and every save rebuilt the document from the ONE workflow
# being saved — so saving project A discarded project B's verification
# and run statistics outright, with no run involved and no directory
# overlap needed.
#
# Identity is the PATH, not an id stored inside project.json: two files
# in a directory cannot share a name, so uniqueness is free, whereas an
# id is copied when a researcher duplicates a project to start a
# variant — and two projects claiming one identity reintroduces exactly
# the clobbering this replaces. The cost is that a rename outside
# vaibify orphans the state, which shows unverified and is recoverable
# by re-verifying. That failure is loud and conservative; the id one is
# silent and asserts something false. The marker subsystem already
# keys on the workflow file for the same reason.
I_CURRENT_STATE_SCHEMA_VERSION = 3
S_WORKFLOW_STATE_KEY = "dictWorkflowState"
S_QUARANTINE_LIST_KEY = "listQuarantinedState"
S_STATE_FILE_RELATIVE = ".vaibify/state.json"
S_VAIBIFY_GITIGNORE_RELATIVE = ".vaibify/.gitignore"
S_TEST_MARKERS_RELATIVE = ".vaibify/test_markers"
S_VAIBIFY_GITIGNORE_BODY = (
    "# Auto-managed by vaibify. Do not edit by hand.\n"
    "state.json\n"
    # The pulled Overleaf manuscript is a read-only convenience copy
    # for the in-container agent, never a canonical project artifact.
    "manuscript/\n"
)

T_STATEFUL_STEP_FIELDS = (
    "dictVerification", "dictRunStats", "dictLevelHighWater",
    "dictDefinitionProducers",
)
# The definition-sensitive results (spec §4.4): each carries, in
# dictDefinitionProducers, the semantic fingerprint its producer acted
# under, and is revalidated on every state->workflow merge. Absent
# producer = unattested (R8) — never backfilled with the current
# fingerprint, because attribution proves an owner, never a
# definition. dictLevelHighWater is deliberately NOT here: the
# high-water history is an add-only ratchet recording what was
# attained when, and invalidating history is falsifying it.
T_ATTESTED_STEP_FIELDS = ("dictVerification", "dictRunStats")
T_STATEFUL_TOP_FIELDS = (
    "bArchiveTrackingMigrated", "iProofLevel",
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
    """Return a fresh, empty per-workflow state SECTION.

    Still the v1/v2 shape, deliberately: this is one project's slice,
    which schema v3 stores under its workflow key rather than at the
    document root. Splitting and merging continue to work in sections;
    only installation into the shared document is namespaced.
    """
    return {
        "iStateSchemaVersion": I_CURRENT_STATE_SCHEMA_VERSION,
        "sLastUpdated": _fsCurrentUtcIso(),
        "dictStepState": {},
        "bWarnedHundredSteps": False,
    }


def fsWorkflowKeyFromPath(sWorkflowPath, sRepoPath):
    """Return the repo-relative project path used as the state key.

    Empty when either input is missing, or when the workflow does not
    sit under the repo — callers treat an empty key as "cannot
    attribute", which fails conservative rather than writing into a
    namespace that may belong to somebody else.
    """
    if not sWorkflowPath or not sRepoPath:
        return ""
    sNormalizedRepo = sRepoPath.rstrip("/") + "/"
    if not sWorkflowPath.startswith(sNormalizedRepo):
        return ""
    return sWorkflowPath[len(sNormalizedRepo):]


def fdictSectionForWorkflow(dictDocument, sWorkflowKey):
    """Return one workflow's state section, or None when absent."""
    if not dictDocument or not sWorkflowKey:
        return None
    dictSections = dictDocument.get(S_WORKFLOW_STATE_KEY)
    if not isinstance(dictSections, dict):
        return None
    dictSection = dictSections.get(sWorkflowKey)
    return dictSection if isinstance(dictSection, dict) else None


def fdictInstallWorkflowSection(dictDocument, sWorkflowKey, dictSection):
    """Return the document with one workflow's section replaced.

    Every other workflow's section is carried through untouched, which
    is the whole point: the v2 writer rebuilt the document from the
    workflow being saved and dropped the rest.
    """
    dictResult = copy.deepcopy(dictDocument) if dictDocument else {}
    dictResult["iStateSchemaVersion"] = I_CURRENT_STATE_SCHEMA_VERSION
    dictResult["sLastUpdated"] = _fsCurrentUtcIso()
    dictSections = dictResult.get(S_WORKFLOW_STATE_KEY)
    if not isinstance(dictSections, dict):
        dictSections = {}
    if sWorkflowKey:
        dictSections[sWorkflowKey] = dictSection
    dictResult[S_WORKFLOW_STATE_KEY] = dictSections
    # Legacy roots are QUARANTINED, never dropped. Dropping them looked
    # safe because the load path migrates before anything reads them —
    # but migration transforms only the in-memory dict, so the document
    # on disk stays v2 until something rewrites it. An ordinary save
    # that re-read that v2 document and deleted its roots destroyed the
    # very data the ambiguous-attribution branch exists to preserve.
    # The writer therefore has to be safe on its own, without relying
    # on a loader having run first.
    dictLegacy = {
        sLegacyKey: dictResult.pop(sLegacyKey)
        for sLegacyKey in ("dictStepState",) + T_STATEFUL_TOP_FIELDS
        if sLegacyKey in dictResult
    }
    fnAppendQuarantineRecord(dictResult, dictLegacy)
    return dictResult


def fnAppendQuarantineRecord(dictDocument, dictPayload):
    """Retain one ambiguous payload; never overwrite, never merge.

    Quarantine is a LIST because a repo can present ambiguous legacy
    state more than once — a second pre-namespace document arriving
    after one was already rescued, most obviously. A single slot got
    this wrong twice: keyed on a non-empty ``dictStepState`` it dropped
    workflow-level fields (``iProofLevel``,
    ``dictWorkflowLevelHighWater``) whenever the step map was empty,
    and refusing to overwrite an existing rescue discarded the NEW
    payload it had already removed from the document.

    Merging the payloads instead would be worse: their step keys are
    directories, they can collide, and a merge would silently pick a
    winner between two bodies of state nobody can attribute.

    A payload with nothing meaningful in it is not recorded — every
    value empty means there is nothing to lose, and an empty record per
    save would bury the real ones.
    """
    if not any(dictPayload.values()):
        return
    listQuarantine = dictDocument.get(S_QUARANTINE_LIST_KEY)
    if isinstance(listQuarantine, dict):
        # The single-slot shape this replaced; carry it in as record 0.
        listQuarantine = [listQuarantine]
    elif not isinstance(listQuarantine, list):
        listQuarantine = []
    dictRecord = dict(dictPayload)
    dictRecord["sQuarantinedUtc"] = _fsCurrentUtcIso()
    listQuarantine.append(dictRecord)
    dictDocument[S_QUARANTINE_LIST_KEY] = listQuarantine


def fbDocumentNeedsMigration(dictDocument):
    """True when a loaded document predates the workflow namespace.

    Callers use this to avoid the repo scan on the normal path: only a
    legacy document needs to know how many projects share the repo,
    and that scan is a general exec, which is both a cost on every
    load and refusable under an enforced mutation lane.
    """
    return (
        isinstance(dictDocument, dict)
        and S_WORKFLOW_STATE_KEY not in dictDocument
    )


def fdictMigrateStateDocument(dictDocument, listWorkflowKeys):
    """Return a v3 document, attributing legacy state only when provable.

    ``listWorkflowKeys`` is every project file in the repo, or None
    when the caller could not establish it. Legacy state carries no
    owner, so it can be attributed only when the repo holds exactly
    ONE project. Anything else is QUARANTINED: kept in the document
    under ``dictQuarantinedState`` so nothing is destroyed, but
    attributed to nobody, so the affected steps read unverified until
    the researcher re-verifies.

    Guessing is the alternative, and it is worse. In a repo with
    several projects the surviving v2 document is not merely unlabelled
    — it is the residue of whichever project was saved LAST, because
    every earlier save destroyed the others. Attributing that to a
    project by directory match can report one project's step as
    verified on the strength of a result a different project produced.
    A researcher cannot detect that; losing a badge they can.
    """
    if not isinstance(dictDocument, dict):
        return dictDocument
    if S_WORKFLOW_STATE_KEY in dictDocument:
        return dictDocument
    dictSection = {
        sKey: dictDocument[sKey]
        for sKey in ("dictStepState",) + T_STATEFUL_TOP_FIELDS
        if sKey in dictDocument
    }
    if not dictSection:
        return fdictInstallWorkflowSection(dictDocument, "", {})
    bSoleOccupant = (
        isinstance(listWorkflowKeys, (list, tuple))
        and len(listWorkflowKeys) == 1
    )
    # The legacy roots are consumed here, so they must not also reach
    # the writer's own rescue path — an attributed document would
    # otherwise carry its state twice, once owned and once quarantined,
    # and read as though nobody could account for it.
    dictStripped = {
        sKey: dictValue for sKey, dictValue in dictDocument.items()
        if sKey not in ("dictStepState",) + T_STATEFUL_TOP_FIELDS
    }
    if bSoleOccupant:
        return fdictInstallWorkflowSection(
            dictStripped, listWorkflowKeys[0], dictSection,
        )
    dictResult = fdictInstallWorkflowSection(dictStripped, "", {})
    fnAppendQuarantineRecord(dictResult, dictSection)
    return dictResult


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
    sPrimaryStatus, dictPrimary = _ftTryLoadStateFile(
        connectionDocker, sContainerId, sStatePath,
    )
    if sPrimaryStatus == "parsed":
        return dictPrimary, "loaded"
    bQuarantined = _fbQuarantineIfCorrupt(
        sPrimaryStatus, connectionDocker, sContainerId, sStatePath,
    )
    sBakPath = _fsBakPathFor(sStatePath)
    sBakStatus, dictBak = _ftTryLoadStateFile(
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


def _ftTryLoadStateFile(connectionDocker, sContainerId, sPath):
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
    """Return a WRITER-UNIQUE sibling ``.tmp`` path for the atomic write.

    The name used to be the state path plus ``.tmp``, which two
    concurrent savers share. A step edit saves under the drain while
    the file poll saves from the event loop and the run saves from its
    own thread; whichever renamed first consumed the other's temp file,
    and the loser's ``mv`` failed against a path that no longer
    existed. On the host leg that surfaced as a 500 on a step edit and
    then a quarantined project, because a half-finished write poisons
    the journal record — correctly, for a write that really did fail.
    """
    from .pipelineUtils import fsBuildUniqueTemporaryPath
    return fsBuildUniqueTemporaryPath(sStatePath)


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
    sWorkflowKey="",
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
    from .stateWriteLock import fcontextHoldStateWriteLock
    if not sStatePath:
        return
    if sWorkflowKey:
        # Read-modify-write under the cross-process write lock, not
        # replace: the document is shared with every other project in
        # this repo, and rebuilding it from the workflow being saved
        # is what erased them. The lock is held from the read through
        # the rename so a concurrent cooperative writer (another save,
        # a completion merge, the CLI) cannot land between them and
        # have its section dropped by this writer's stale read.
        with fcontextHoldStateWriteLock(sContainerId, sStatePath):
            dictExisting, _sStatus = ftLoadStateWithStatus(
                connectionDocker, sContainerId, sStatePath,
            )
            dictPersisted = fdictInstallWorkflowSection(
                dictExisting, sWorkflowKey, dict(dictState),
            )
            _fnPersistStateDocument(
                connectionDocker, sContainerId, sStatePath, dictPersisted,
            )
        return
    _fnPersistStateDocument(
        connectionDocker, sContainerId, sStatePath, dict(dictState),
    )


def _fnPersistStateDocument(
    connectionDocker, sContainerId, sStatePath, dictPersisted,
):
    """Write a full state document atomically with a checkpoint.

    The shared write tail of :func:`fnSaveStateToContainer` and
    :func:`fdictMergeRunResultsIntoState` — the second reads the
    document itself before merging, and re-reading it inside the save
    would widen the window in which a concurrent writer's section is
    read stale.
    """
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


def fdictMergeRunResultsIntoState(
    connectionDocker, sContainerId, sStatePath, sWorkflowKey,
    dictRunDeltaByStepId, dictStepIdToDirectory,
    sRunDefinitionFingerprint="",
):
    """Merge a run's per-step results into a freshly loaded document.

    The completion writer for D2: the run's in-memory workflow is a
    SNAPSHOT from dispatch time, so nothing from it may be written
    wholesale — a researcher's mid-run edit or attestation would be
    destroyed. Instead the run's delta — ``{sStepId: dictRunStats}``
    for the steps that actually EXECUTED — is applied entry-by-entry
    into the document read from disk NOW. Within each entry only
    ``dictRunStats`` is replaced and the run-invalidated modification
    flags are cleared; a ``dictVerification`` the researcher updated
    mid-run survives untouched.

    ``dictStepIdToDirectory`` maps each delta id to the run's directory
    for that step, so an entry persisted under the pre-id directory key
    is migrated to its id key rather than forked.

    Returns ``{"bPersisted": bool, "sDetail": str}``. Refusals name
    their reason; the caller surfaces it on the terminal event rather
    than raising, because this runs inside a carrier worker where an
    expected failure must not poison the journal record.
    """
    from .pipelineUtils import T_RUN_CLEARED_VERIFICATION_FLAGS
    if not sStatePath:
        return {
            "bPersisted": False,
            "sDetail": "no project repo; run results were not recorded",
        }
    if not sWorkflowKey:
        return {
            "bPersisted": False,
            "sDetail": (
                "cannot attribute run results: the project file is "
                "not under its project repo"
            ),
        }
    from .stateWriteLock import fcontextHoldStateWriteLock
    with fcontextHoldStateWriteLock(sContainerId, sStatePath):
        dictDocument, _sStatus = ftLoadStateWithStatus(
            connectionDocker, sContainerId, sStatePath,
        )
        dictSection = fdictSectionForWorkflow(dictDocument, sWorkflowKey)
        if dictSection is None:
            dictSection = fdictBuildEmptyState()
        dictStepMap = dictSection.setdefault("dictStepState", {})
        for sStepId, dictRunStats in dictRunDeltaByStepId.items():
            dictEntry = dictStepMap.get(sStepId)
            if dictEntry is None:
                sDirectoryKey = dictStepIdToDirectory.get(sStepId, "")
                if sDirectoryKey and sDirectoryKey in dictStepMap:
                    dictEntry = dictStepMap.pop(sDirectoryKey)
                else:
                    dictEntry = {}
                dictStepMap[sStepId] = dictEntry
            dictEntry["dictRunStats"] = dictRunStats
            if sRunDefinitionFingerprint:
                # The producer stamp (§4.4): these stats were made
                # under the run's DISPATCH-TIME definition. A mid-run
                # definition edit makes this differ from the current
                # fingerprint, and the next merge marks the stats
                # superseded instead of silently reattaching them —
                # the cross-file race becomes conservative
                # invalidation.
                dictEntry.setdefault(
                    "dictDefinitionProducers", {},
                )["dictRunStats"] = sRunDefinitionFingerprint
            dictVerification = dictEntry.get("dictVerification")
            if isinstance(dictVerification, dict):
                for sFlag in T_RUN_CLEARED_VERIFICATION_FLAGS:
                    dictVerification.pop(sFlag, None)
        dictPersisted = fdictInstallWorkflowSection(
            dictDocument, sWorkflowKey, dictSection,
        )
        _fnPersistStateDocument(
            connectionDocker, sContainerId, sStatePath, dictPersisted,
        )
    return {"bPersisted": True, "sDetail": ""}


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
    """POSIX-atomic rename of the temp file over state.json.

    A failed rename discards the temp file in the SAME command, and
    keeps the rename's own exit code for the diagnosis. The temp name
    is unique per writer, so nothing reclaims an abandoned one the way
    the next save used to overwrite the old fixed name — and this is
    the only path that can abandon one.
    """
    from .pipelineUtils import fsShellQuote
    sQuotedTempPath = fsShellQuote(sTempPath)
    sCommand = (
        f"mv -f {sQuotedTempPath} {fsShellQuote(sStatePath)} || "
        f"{{ iStatus=$?; rm -f {sQuotedTempPath}; exit $iStatus; }}"
    )
    iExit, sOutput = connectionDocker.ftResultExecuteCommand(
        sContainerId, sCommand,
    )
    if iExit != 0:
        raise OSError(
            f"Atomic rename of {sTempPath} to {sStatePath} "
            f"failed (exit {iExit}): {sOutput}"
        )


S_REMOTE_MARKER_ROOT_KEY = "dictRemoteDataMarkers"


def fdictPublishRemoteDataMarker(
    connectionDocker, sContainerId, sStatePath, sWorkflowKey,
    sStepId, listExpectedPaths,
):
    """Publish the durable pre-execution marker for one step's pull.

    Spec §4.5 condition 1: before a step declaring ``listRemoteData``
    runs, this marker says "remote data may arrive that is not yet
    documented" — durably, so a crash mid-step cannot leave pulled
    bytes on disk with no trace that a pull was even in flight.

    Stored at the DOCUMENT ROOT, workflow-namespaced, and that level
    is a constraint, not a preference: a sibling project's sequential
    save carries unknown root keys through
    (:func:`fdictInstallWorkflowSection` copies the document), while a
    field inside the saving project's own section is REBUILT from the
    in-memory workflow on every ordinary save and silently vanishes.

    Fails closed: a marker that cannot be written — or cannot be READ
    BACK after writing, which is what "durably acknowledged" means —
    refuses the step. A missing state.json bootstraps one; a marker
    with no attributable home (no repo, no key) refuses.
    """
    if not sStatePath or not sWorkflowKey or not sStepId:
        return {
            "bPublished": False,
            "sDetail": (
                "the pull marker has no durable home (project repo, "
                "workflow key and step id are all required); the "
                "step is refused rather than pulling undocumented "
                "data"
            ),
        }
    from .stateWriteLock import fcontextHoldStateWriteLock
    try:
        with fcontextHoldStateWriteLock(sContainerId, sStatePath):
            dictDocument, _sStatus = ftLoadStateWithStatus(
                connectionDocker, sContainerId, sStatePath,
            )
            if not isinstance(dictDocument, dict):
                dictDocument = {}
            dictDocument.setdefault(
                "iStateSchemaVersion", I_CURRENT_STATE_SCHEMA_VERSION,
            )
            dictDocument.setdefault(
                S_REMOTE_MARKER_ROOT_KEY, {},
            ).setdefault(sWorkflowKey, {})[sStepId] = {
                "sStepId": sStepId,
                "listExpectedPaths": sorted(listExpectedPaths or []),
                "sPublishedUtc": _fsCurrentUtcIso(),
            }
            _fnPersistStateDocument(
                connectionDocker, sContainerId, sStatePath,
                dictDocument,
            )
            dictReread, _sRereadStatus = ftLoadStateWithStatus(
                connectionDocker, sContainerId, sStatePath,
            )
            if fdictReadRemoteDataMarker(
                dictReread, sWorkflowKey, sStepId,
            ) is None:
                return {
                    "bPublished": False,
                    "sDetail": (
                        "the pull marker was written but could not "
                        "be read back; the step is refused because "
                        "its guarantee never became durable"
                    ),
                }
        return {"bPublished": True, "sDetail": ""}
    except Exception as errorPublish:
        return {
            "bPublished": False,
            "sDetail": f"pull marker publish failed: {errorPublish}",
        }


def fdictClearRemoteDataMarker(
    connectionDocker, sContainerId, sStatePath, sWorkflowKey, sStepId,
):
    """Clear one step's pull marker after its records reconciled.

    Callers may only clear when every declared file was examined and
    the record merge committed — that judgement lives at the call
    site, next to the evidence. Clearing an absent marker is True
    (idempotent recovery); a failed clear reports False and the
    marker stays, which is the correct failure direction.
    """
    if not sStatePath or not sWorkflowKey or not sStepId:
        return {"bCleared": False, "sDetail": "marker home incomplete"}
    from .stateWriteLock import fcontextHoldStateWriteLock
    try:
        with fcontextHoldStateWriteLock(sContainerId, sStatePath):
            dictDocument, _sStatus = ftLoadStateWithStatus(
                connectionDocker, sContainerId, sStatePath,
            )
            if fdictReadRemoteDataMarker(
                dictDocument, sWorkflowKey, sStepId,
            ) is None:
                return {"bCleared": True, "sDetail": ""}
            dictAllMarkers = dictDocument[S_REMOTE_MARKER_ROOT_KEY]
            del dictAllMarkers[sWorkflowKey][sStepId]
            if not dictAllMarkers[sWorkflowKey]:
                del dictAllMarkers[sWorkflowKey]
            _fnPersistStateDocument(
                connectionDocker, sContainerId, sStatePath,
                dictDocument,
            )
        return {"bCleared": True, "sDetail": ""}
    except Exception as errorClear:
        return {
            "bCleared": False,
            "sDetail": f"pull marker clear failed: {errorClear}",
        }


def fdictReadRemoteDataMarker(dictDocument, sWorkflowKey, sStepId):
    """Return one step's pull marker, or None."""
    if not isinstance(dictDocument, dict):
        return None
    dictForWorkflow = (
        dictDocument.get(S_REMOTE_MARKER_ROOT_KEY) or {}
    ).get(sWorkflowKey)
    if not isinstance(dictForWorkflow, dict):
        return None
    dictMarker = dictForWorkflow.get(sStepId)
    return dictMarker if isinstance(dictMarker, dict) else None


def flistUnresolvedRemoteDataStepIds(dictDocument, sWorkflowKey):
    """Return the step ids whose pull markers are still set.

    A non-empty answer means remote data may sit on disk without a
    committed record — the reproducibility level gates on it (§4.5
    condition 2), and the dashboard names the steps.
    """
    if not isinstance(dictDocument, dict) or not sWorkflowKey:
        return []
    dictForWorkflow = (
        dictDocument.get(S_REMOTE_MARKER_ROOT_KEY) or {}
    ).get(sWorkflowKey)
    if not isinstance(dictForWorkflow, dict):
        return []
    return sorted(dictForWorkflow)


def fnMergeStateIntoWorkflow(
    dictWorkflow, dictState, sWorkflowKey="",
    sCurrentSemanticFingerprint="",
):
    """Copy this workflow's state fields back into the workflow dict.

    No-op when ``dictState`` is None. Steps without a matching
    ``dictStepState`` entry keep whatever stateful fields the loaded
    project.json happened to carry — the project-schema v2→v3
    migration owns the one-shot extraction; this routine is the
    steady-state merger.

    With a workflow key, only that project's section is read, so one
    project can no longer pick up another's verification results. An
    absent section yields nothing, which is the correct reading of
    "this project has no recorded state" — including after a rename
    orphaned it, or after an ambiguous legacy document was
    quarantined.

    The keyless call is the pre-namespace shape and reads the document
    root. It survives for the bootstrap path, which builds a section
    from markers and merges it before any key exists.

    ``sCurrentSemanticFingerprint`` is the CURRENT definition's
    attestation identity; when given, every attested field is
    revalidated as it merges (spec §4.4: a check made only at
    completion protects one write — the next reload would reattach
    old results to a new definition). The verdicts land in the
    computed ``dictStaleResultFields`` (``"superseded"`` /
    ``"unattested"``), which never persists.
    """
    if dictState is None:
        return
    if sWorkflowKey:
        dictSection = fdictSectionForWorkflow(dictState, sWorkflowKey)
        dictState = dictSection if dictSection is not None else {}
    dictStepState = dictState.get("dictStepState", {}) or {}
    for dictStep in dictWorkflow.get("listSteps", []):
        # Sections are keyed by the stable step id; a directory key is
        # the pre-id shape, still readable so state written before the
        # id keying survives a load. The id is tried first because a
        # rename changes the directory and must not orphan the entry.
        dictForStep = dictStepState.get(
            dictStep.get("sStepId", ""),
            dictStepState.get(dictStep.get("sDirectory", ""), {}),
        )
        for sKey in T_STATEFUL_STEP_FIELDS:
            if sKey in dictForStep:
                dictStep[sKey] = dictForStep[sKey]
        if sCurrentSemanticFingerprint:
            _fnMarkStaleResultFields(
                dictStep, dictForStep, sCurrentSemanticFingerprint,
            )
    for sKey in T_STATEFUL_TOP_FIELDS:
        if sKey in dictState:
            dictWorkflow[sKey] = dictState[sKey]


def _fnMarkStaleResultFields(
    dictStep, dictForStep, sCurrentSemanticFingerprint,
):
    """Revalidate one step's attested fields against the definition.

    A field whose recorded producer fingerprint differs from the
    current definition is ``"superseded"``; a non-empty field with no
    recorded producer is ``"unattested"`` (R8's legacy answer, never
    upgraded). An empty or absent field claims nothing and is not
    marked. The verdict is computed, per load, and stripped on save.
    """
    dictProducers = dictForStep.get("dictDefinitionProducers") or {}
    dictStale = {}
    for sAttestedKey in T_ATTESTED_STEP_FIELDS:
        if not dictForStep.get(sAttestedKey):
            continue
        sProducerFingerprint = dictProducers.get(sAttestedKey, "")
        if not sProducerFingerprint:
            dictStale[sAttestedKey] = "unattested"
        elif sProducerFingerprint != sCurrentSemanticFingerprint:
            dictStale[sAttestedKey] = "superseded"
    if dictStale:
        dictStep["dictStaleResultFields"] = dictStale
    else:
        dictStep.pop("dictStaleResultFields", None)


def ftSplitMergedDict(dictWorkflow):
    """Return ``(declarativeDict, stateDict)`` from a merged workflow.

    The declarative dict is what gets written to ``project.json``
    (no per-step ``dictVerification`` / ``dictRunStats``, no
    ``bArchiveTrackingMigrated``, no transient ``sLabel``). The state
    dict is what gets written to ``state.json``.
    """
    dictDeclarative = copy.deepcopy(dictWorkflow)
    dictDeclarative.pop("sProjectRepoPath", None)
    dictStepState = {}
    for dictStep in dictDeclarative.get("listSteps", []):
        # Keyed by the stable step id so a rename mid-run cannot fork
        # the entry; the directory is the pre-id fallback for a step
        # that somehow has no id (the save path ensures ids first).
        sStateKey = dictStep.get("sStepId") or dictStep.get(
            "sDirectory", "",
        )
        dictExtracted = {}
        for sKey in T_STATEFUL_STEP_FIELDS:
            if sKey in dictStep:
                dictExtracted[sKey] = dictStep.pop(sKey)
        dictStep.pop("sLabel", None)
        # Computed per load by the merge's revalidation; persisting it
        # would freeze a verdict the next definition edit must change.
        dictStep.pop("dictStaleResultFields", None)
        if dictExtracted and sStateKey:
            dictStepState[sStateKey] = dictExtracted
    dictState = fdictBuildEmptyState()
    dictState["dictStepState"] = dictStepState
    for sKey in T_STATEFUL_TOP_FIELDS:
        if sKey in dictDeclarative:
            dictState[sKey] = dictDeclarative.pop(sKey)
    return dictDeclarative, dictState


def fbRatchetLevelHighWater(
    dictWorkflow, dictStepLevelStates, dictWorkflowScopeStates,
):
    """Stamp first-attainment timestamps for newly attained PROOF levels.

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
    for dictStep, dictMarker in listMarkers:
        if dictMarker is None:
            continue
        dictVerification = _fdictVerificationFromMarker(
            dictMarker, dictOnDiskHashes,
        )
        sStateKey = dictStep.get("sStepId") or dictStep.get(
            "sDirectory", "",
        )
        dictStepState[sStateKey] = {
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
    """Return ``[(dictStep, dictMarker_or_None), ...]`` for every step.

    Marker filenames use the canonical ``fsMarkerNameFromStepDirectory``
    encoding (slashes → underscores) so a nested step directory like
    ``Step01/sub`` resolves to the same ``Step01_sub.json`` the conftest
    writes — never a literal ``Step01/sub.json``. The step dict itself
    rides along because the caller keys the synthesized state by the
    step's stable id, while the marker file on disk is named by
    directory.
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
        listResult.append((dictStep, dictMarker))
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
