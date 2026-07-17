"""AICS Level 1-3 gate functions.

Single source of truth for the ``iAICSLevel`` integer that drives the
dashboard theme. ``fiAICSLevel`` short-circuits up the ladder. Phase 1
shipped L1; Phase 2 fills in L2 (Publication) here. L3 remains stubbed
``return False`` until Phase 3 (Reproducibility) lands.

Per-step L1 predicates live in ``stepPredicates`` (pure leaf module);
L2 predicates are split across this module and ``scheduledReverify``
(sync status cache) / ``aiDeclarationStep`` (step-kind predicate) so
each concern has one owner. The composition lives here so the level
decision lives in one module.
"""

import hashlib
import json
import threading
from collections import OrderedDict
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from . import scheduledReverify
from .aiDeclarationStep import fbStepIsAiDeclaration
from .dependencyPinning import (
    S_LOCK_TOOL_INSTALL_HINT,
    flistVerifyRequirementsLock,
)
from .dockerfileLint import flistLintDockerfile
from .determinismGate import (
    fbWorkflowDeclaresDeterminism,
    flistAuditWorkflow,
)
from .environmentSnapshot import fbEnvironmentDigestPinned
from .l3Attestation import (
    fbL3AttestationCurrent,
    fsCurrentManifestDigest,
)
from .manifestWriter import (
    flistDeclaredButMissingFromManifest,
    flistParseManifestLines,
)
from .repoFiles import ffilesEnsureRepoFiles, fsRepoRootOf
from .reproduceScriptGenerator import S_REPRODUCE_SCRIPT_FILENAME
from .stepPredicates import (
    _T_GREEN_VERIF_VALUES,
    _T_TEST_VERIF_KEYS,
    fbStepTestsPassing,
    fbStepTimingClean,
    fbStepUserApproved,
)

__all__ = [
    "F_MAX_STALE_HOURS",
    "TUPLE_COMMON_SCIENTIFIC_BINARIES",
    "fbAtLeastLevel1",
    "fbAtLeastLevel2",
    "fbAtLeastLevel3",
    "fbL3ReadinessOK",
    "fbStepIsAtLeastLevel1",
    "fbVerifyDependencyLock",
    "fbVerifyDeterminismDeclared",
    "fbVerifyDockerfilePinned",
    "fbVerifyEnvironmentSnapshot",
    "fbVerifyManifestComplete",
    "fbVerifyReproduceScript",
    "fbWorkflowDeclaresBinaries",
    "fbWorkflowFullySyncedWithArxiv",
    "flistStepDependedBinaryPaths",
    "flistWorkflowBinaryPaths",
    "fbWorkflowHasArxivConnection",
    "fbWorkflowFullySyncedWithGithub",
    "fbWorkflowFullySyncedWithZenodo",
    "fbWorkflowAiDeclarationAttested",
    "fbWorkflowHasAiDeclarationStep",
    "fbWorkflowHasOverleafBinding",
    "fbWorkflowHasProjectRepo",
    "fdictBinaryStaleByStep",
    "fdictComputeStepLevelStates",
    "fdictComputeStepLevelWarnings",
    "fdictComputeWorkflowScopeLevelStates",
    "fdictL3ReadinessGaps",
    "fdictLevel2Gaps",
    "fiAICSLevel",
    "fiLowestNonAttainedLevel",
    "fiStepAICSLevel",
    "flistLevel1Blockers",
    "flistLevel2Blockers",
    "flistLevel3Blockers",
    "fnClearLevelBlockerCache",
    "fnLevelComputationContext",
]


# L2/L3 blocker-list namespace reservation.
#
# Part C of the L1 honest-rendering work introduces ``flistLevel1Blockers``
# as the per-step diagnostic surface that drives the dashboard's check
# rendering, banner glyphs, and file/edge glyphs. The same shape is
# expected at the higher levels:
#
#   def flistLevel2Blockers(dictWorkflow, dictNewModTimes, sProjectRepoPath):
#       """Return per-step L2 blockers (publication-gate criteria).
#
#       Reserved for the Phase 2 follow-up. Will surface gaps in the
#       GitHub mirror / Zenodo deposit / AI-declaration step the same
#       way L1 surfaces upstream-modified / axis-not-green / user-not-
#       approved. NOT IMPLEMENTED IN THIS PHASE.
#       """
#
#   def flistLevel3Blockers(dictWorkflow, dictNewModTimes, sProjectRepoPath):
#       """Return per-step L3 blockers (reproducibility-gate criteria).
#
#       Reserved for the Phase 3 follow-up. Will surface gaps in the
#       manifest / dependency lock / environment snapshot / Dockerfile /
#       reproduce script / determinism declaration the same way L1
#       surfaces its three criteria. NOT IMPLEMENTED IN THIS PHASE.
#       """


F_MAX_STALE_HOURS = 24.0


# Per-call memoization scope for the L1/L2/L3 chain.
#
# ``fiAICSLevel`` evaluates L1, then L2 (which internally calls L1),
# then L3 (which internally calls L2, which calls L1). At N=100 steps
# the inner L1 calls iterate the verifications three times even though
# the answer is identical. ``fnLevelComputationContext`` activates a
# thread-local memo dict that ``fbAtLeastLevel1`` / ``fbAtLeastLevel2``
# consult before recomputing. The memo lives only for the lifetime of
# the context — no cross-poll state, no stale-cache risk.
_THREAD_LOCAL = threading.local()


@contextmanager
def fnLevelComputationContext():
    """Activate a per-call memo for ``fbAtLeastLevel{1,2}``.

    Use inside ``fiAICSLevel`` or any other path that drives the L1/L2/L3
    chain repeatedly on the same workflow + project-repo pair. Outside
    the context, the gates fall back to uncached evaluation so individual
    callers (e.g. the auto-archive envelope-refresh hook) keep their
    existing behavior.
    """
    _THREAD_LOCAL.dictMemo = {}
    try:
        yield _THREAD_LOCAL.dictMemo
    finally:
        _THREAD_LOCAL.dictMemo = None


def _fdictActiveLevelMemo():
    """Return the active per-call memo, or None when no context is active."""
    return getattr(_THREAD_LOCAL, "dictMemo", None)


# Cross-poll blocker-list memo.
#
# Each poll calls ``flistLevel1Blockers``, ``flistLevel2Blockers``, and
# ``flistLevel3Blockers`` once. Each call walks every step (and per-step
# scripts/manifests/sync caches) even when nothing about the workflow or
# the on-disk inputs has changed. The memo here keys on a fingerprint
# of (workflow-relevant-content, mod-time-vector, repo-root,
# script-status) so a re-poll on identical inputs returns the previous
# list without re-walking. Bounded LRU keeps the working set small;
# ``fnClearLevelBlockerCache`` is the test/invalidation hook.
_I_BLOCKER_CACHE_MAX_ENTRIES = 8
_DICT_BLOCKER_CACHE = OrderedDict()


def fnClearLevelBlockerCache():
    """Discard every cached blocker list (tests + invalidation hooks)."""
    _DICT_BLOCKER_CACHE.clear()


def _fnBlockerCacheStore(tCacheKey, listResult):
    """Insert listResult under tCacheKey with LRU eviction."""
    if tCacheKey in _DICT_BLOCKER_CACHE:
        _DICT_BLOCKER_CACHE.move_to_end(tCacheKey)
        _DICT_BLOCKER_CACHE[tCacheKey] = listResult
        return
    _DICT_BLOCKER_CACHE[tCacheKey] = listResult
    while len(_DICT_BLOCKER_CACHE) > _I_BLOCKER_CACHE_MAX_ENTRIES:
        _DICT_BLOCKER_CACHE.popitem(last=False)


def _flistBlockerCacheLookup(tCacheKey):
    """Return the cached list for tCacheKey, or None on miss."""
    listCached = _DICT_BLOCKER_CACHE.get(tCacheKey)
    if listCached is None:
        return None
    _DICT_BLOCKER_CACHE.move_to_end(tCacheKey)
    return listCached


def _fsWorkflowBlockerFingerprint(dictWorkflow):
    """Return a SHA256 over workflow fields that influence blocker lists."""
    listEntries = []
    for dictStep in (dictWorkflow or {}).get("listSteps", []) or []:
        if not isinstance(dictStep, dict):
            listEntries.append(None)
            continue
        listEntries.append(_fdictBlockerRelevantStep(dictStep))
    dictTopLevel = _fdictWorkflowTopLevelFingerprint(dictWorkflow)
    sCanonical = json.dumps(
        {"listSteps": listEntries, "dictTop": dictTopLevel},
        sort_keys=True, default=str,
    )
    return hashlib.sha256(sCanonical.encode("utf-8")).hexdigest()


def _fdictBlockerRelevantStep(dictStep):
    """Capture the per-step fields that determine blocker output."""
    return {sKey: dictStep.get(sKey) for sKey in (
        "sName", "sDirectory", "sLabel", "sStepKind",
        "saOutputDataFiles", "saPlotFiles",
        "saInputDataFiles", "bNoInputData",
        "saDataCommands", "saPlotCommands", "saTestCommands",
        "saSetupCommands", "saCommands", "saDependencies",
        "dictVerification", "dictTests", "sLastUserUpdate",
        "bUnseededRandomnessWarning", "bInteractive",
    )}


def _fdictWorkflowTopLevelFingerprint(dictWorkflow):
    """Capture top-level workflow keys that influence blocker output.

    ``dictRemotes`` matters here: the arXiv criteria are keyed on the
    recorded connection, so adding or removing a remote must bust the
    cached L2 blocker list on the next poll.
    """
    return {sKey: (dictWorkflow or {}).get(sKey) for sKey in (
        "sPlotDirectory", "sProjectRepoPath", "sFigureType",
        "listDeclaredBinaries", "bNoStandaloneBinaries",
        "dictDeterminism", "dictRemotes", "dictAttestation",
    )}


def _fsModTimesFingerprint(dictNewModTimes):
    """Return a SHA256 over the per-step mod-time vector."""
    if not dictNewModTimes:
        return "empty"
    listPairs = sorted(
        (str(sKey), value) for sKey, value in dictNewModTimes.items()
    )
    sCanonical = json.dumps(listPairs, sort_keys=True, default=str)
    return hashlib.sha256(sCanonical.encode("utf-8")).hexdigest()


def _fsScriptStatusFingerprint(dictScriptStatus):
    """Return a SHA256 over the script-status dict, or ``"none"``."""
    if not dictScriptStatus:
        return "none"
    sCanonical = json.dumps(
        dictScriptStatus, sort_keys=True, default=str,
    )
    return hashlib.sha256(sCanonical.encode("utf-8")).hexdigest()


def _fsSyncStatusFingerprint(filesRepo):
    """SHA over GitHub + Zenodo sync caches so verify-completion busts L2/L3.

    The previous L2/L3 cache keys ignored
    ``scheduledReverify.fdictReadCachedSyncStatus`` results, so a GitHub
    or Zenodo verify-completion that did not change the workflow content
    left blocker lists stale on the dashboard. Including the cache
    contents in the fingerprint forces a re-compute the next poll after
    any verify writes a fresh ``sLastVerified`` / ``sLastSha`` / status
    field. Returns ``"none"`` when the repo has no sync cache yet.
    """
    listEntries = []
    for sService in ("github", "zenodo"):
        try:
            dictStatus = scheduledReverify.fdictReadCachedSyncStatus(
                filesRepo, sService,
            )
        except Exception:
            dictStatus = None
        listEntries.append((sService, dictStatus or {}))
    if not any(d for _s, d in listEntries):
        return "none"
    sCanonical = json.dumps(listEntries, sort_keys=True, default=str)
    return hashlib.sha256(sCanonical.encode("utf-8")).hexdigest()


def _fsRepoFingerprint(filesRepo):
    """Return a stable identifier for the repo adapter or path."""
    sRepoRoot = fsRepoRootOf(filesRepo)
    return sRepoRoot if isinstance(sRepoRoot, str) else "unknown"


def fiAICSLevel(dictWorkflow, filesRepo, dictScriptStatus=None):
    """Return the integer AICS level (0..3) for a workflow.

    Short-circuits up the ladder so each gate runs at most once. Wraps
    the L1/L2/L3 chain in ``fnLevelComputationContext`` so the inner
    recursive calls (L2 -> L1, L3 -> L2 -> L1) hit a memo instead of
    re-iterating every step. ``dictScriptStatus`` threads through to
    L1 so callers with mtime info honor the script-stale criterion.
    ``filesRepo`` is a project-repo path string (host clone) or a
    ``repoFiles`` adapter (container or poll snapshot).
    """
    filesRepo = ffilesEnsureRepoFiles(filesRepo)
    with fnLevelComputationContext():
        if not fbAtLeastLevel1(
            dictWorkflow, filesRepo, dictScriptStatus,
        ):
            return 0
        if not fbAtLeastLevel2(dictWorkflow, filesRepo):
            return 1
        if not fbAtLeastLevel3(dictWorkflow, filesRepo):
            return 2
        return 3


def fbAtLeastLevel1(dictWorkflow, filesRepo, dictScriptStatus=None):
    """Return True iff the workflow meets the L1 Self-Consistent gate.

    L1 requires four criteria, all enforced per-step: workflow lives
    in a git project repo, every step is user-approved, every step is
    timing-clean (no upstream-modified flag, no outstanding modified
    files), and every step's defined test categories are green. When
    ``dictScriptStatus`` is provided, the script-stale criterion also
    blocks the gate; callers without script-status info preserve the
    historical truth-table.
    """
    dictMemo = _fdictActiveLevelMemo()
    if dictMemo is not None and "bL1" in dictMemo:
        return dictMemo["bL1"]
    bResult = _fbComputeLevel1(
        dictWorkflow, filesRepo, dictScriptStatus,
    )
    if dictMemo is not None:
        dictMemo["bL1"] = bResult
    return bResult


def _fbComputeLevel1(dictWorkflow, filesRepo, dictScriptStatus=None):
    """Uncached L1 evaluation — the body of the original gate.

    Delegates to ``flistLevel1Blockers`` so the boolean gate and the
    per-step diagnostic surface share one implementation: no blockers
    means L1 is clean. Preserves the historical contract — an empty
    workflow or a missing project repo still returns False.
    """
    if not fbWorkflowHasProjectRepo(filesRepo):
        return False
    listSteps = dictWorkflow.get("listSteps", []) or []
    if not listSteps:
        return False
    listBlockers = flistLevel1Blockers(
        dictWorkflow, {}, filesRepo, dictScriptStatus,
    )
    return len(listBlockers) == 0


def flistLevel1Blockers(
    dictWorkflow, dictNewModTimes, filesRepo,
    dictScriptStatus=None,
):
    """Return per-step L1 blockers with per-file granularity.

    Each entry uses the unified blocker schema (Section A of the
    AICS-ladder plan)::

        {"iLevel": 1,
         "iStepIndex": int,
         "sStepLabel": str,
         "sScope": "step",
         "sCriterion": str,
         "listOffendingFiles": [repo-relative paths],
         "listOffendingUpstreamSteps": [0-based step indices],
         "sRemediationHint": str}

    ``sCriterion`` is one of ``"input-data-undeclared"``,
    ``"user-not-approved"``,
    ``"upstream-modified"``, ``"script-stale"``, ``"axis-not-green"``,
    or ``"attestation-stale"``. ``input-data-undeclared`` fires when a
    step neither lists ``saInputDataFiles`` nor carries the explicit
    ``bNoInputData`` declaration — a Project whose input contract is
    unstated is not self-consistent. ``script-stale`` fires when the
    step's script has been edited after its declared outputs landed;
    suppressed when the outputs' hashes still match ``MANIFEST.sha256``
    (fresh clones). Priority order is ``input-data-undeclared`` >
    ``upstream-modified`` > ``script-stale``
    > ``axis-not-green`` > ``attestation-stale`` > ``user-not-approved``.
    The list is sorted by ``iStepIndex`` so rendering order is
    deterministic. Returns ``[]`` for an L1-clean workflow or one with
    no project repo.

    Entries may also carry an optional ``dictOffendingFileHints``
    field, ``{sRawPath: sHint}``, keyed by paths exactly as they
    appear in ``listOffendingFiles``, holding a per-file remediation
    tooltip. Emitted by ``upstream-modified`` (names the modified
    upstream steps), ``attestation-stale`` (output changed after the
    researcher attested), and ``axis-not-green`` when the non-green
    cause is marker drift (``outputs-changed``). Consumers must
    tolerate its absence — older payloads do not carry it.

    ``axis-not-green`` entries additionally carry ``sSubState``, one
    of ``"failed"`` / ``"outputs-missing"`` / ``"outputs-changed"`` /
    ``"untested"`` — the machine-readable cause behind the prose hint
    (same priority ladder, via ``_fsAxisNotGreenSubState``).

    Entries may carry an optional ``dictOffendingFileMarks`` field,
    ``{sRawPath: "stale" | "failed" | "missing"}``, whose keys exactly
    mirror ``listOffendingFiles``. Emitted by ``axis-not-green``
    (drift files ``"stale"``, ``outputs-missing`` ``"missing"``,
    ``failed`` ``"failed"``; the ``untested`` sub-state attaches no
    marks), ``upstream-modified`` (all ``"stale"``), ``script-stale``
    (all ``"stale"``), and ``attestation-stale`` (all ``"stale"``).
    Consumers must tolerate its absence.
    """
    filesRepo = ffilesEnsureRepoFiles(filesRepo)
    tCacheKey = (
        "L1",
        _fsWorkflowBlockerFingerprint(dictWorkflow),
        _fsModTimesFingerprint(dictNewModTimes),
        _fsRepoFingerprint(filesRepo),
        _fsScriptStatusFingerprint(dictScriptStatus),
    )
    listCached = _flistBlockerCacheLookup(tCacheKey)
    if listCached is not None:
        return listCached
    listResult = _flistComputeLevel1Blockers(
        dictWorkflow, dictNewModTimes, filesRepo, dictScriptStatus,
    )
    _fnBlockerCacheStore(tCacheKey, listResult)
    return listResult


def _flistComputeLevel1Blockers(
    dictWorkflow, dictNewModTimes, filesRepo, dictScriptStatus,
):
    """Uncached L1-blocker evaluation — the body of the original gate."""
    if not fbWorkflowHasProjectRepo(filesRepo):
        return []
    listSteps = dictWorkflow.get("listSteps", []) or []
    if not listSteps:
        return []
    dictUpstreamByStep = _fdictUpstreamStepsByConsumer(dictWorkflow)
    listBlockers = []
    for iStepIndex, dictStep in enumerate(listSteps):
        dictBlocker = _fdictBuildStepBlocker(
            dictWorkflow, iStepIndex, dictStep,
            dictNewModTimes, dictUpstreamByStep,
            dictScriptStatus, filesRepo,
        )
        if dictBlocker is not None:
            listBlockers.append(dictBlocker)
    return sorted(listBlockers, key=lambda dictEntry: dictEntry["iStepIndex"])


def _fdictBuildStepBlocker(
    dictWorkflow, iStepIndex, dictStep,
    dictNewModTimes, dictUpstreamByStep,
    dictScriptStatus=None, filesRepo=None,
):
    """Return the single dominant blocker dict for a step, or None.

    Priority: ``input-data-undeclared`` > ``upstream-modified`` >
    ``script-stale`` > ``axis-not-green`` > ``attestation-stale`` >
    ``user-not-approved``. The declaration criterion leads because it
    is a contract gap, not a freshness signal — until the researcher
    states what raw data the step consumes (or that it consumes
    none), no freshness verdict about the step is meaningful.
    The first applicable criterion wins so a step never emits two
    blockers; the dashboard's banner glyph therefore has a deterministic
    single source. Corrupt step entries (``None``, non-dict, missing
    ``dictVerification``) cannot satisfy any criterion and are surfaced
    as ``user-not-approved`` so the L1 gate matches its historical
    defensive contract. AI-declaration steps emit no L1 blocker at
    all: the declaration is a publication artifact, so its sign-off
    is enforced by the LEVEL 2 gate (``ai-declaration-unattested``).
    """
    if not isinstance(dictStep, dict):
        return _fdictUserNotApprovedBlocker(dictWorkflow, iStepIndex)
    if fbStepIsAiDeclaration(dictStep):
        return None
    if _fbStepInputDataUndeclared(dictStep):
        return _fdictInputUndeclaredBlocker(dictWorkflow, iStepIndex)
    if not fbStepTimingClean(dictStep):
        return _fdictUpstreamModifiedBlocker(
            dictWorkflow, iStepIndex, dictStep,
            dictNewModTimes, dictUpstreamByStep,
        )
    if _fbStepScriptStale(
        iStepIndex, dictStep, dictScriptStatus, filesRepo,
    ):
        return _fdictScriptStaleBlocker(
            dictWorkflow, iStepIndex, dictStep,
        )
    if not fbStepTestsPassing(dictStep):
        return _fdictAxisNotGreenBlocker(
            dictWorkflow, iStepIndex, dictStep, filesRepo,
        )
    return _fdictUserDispositionBlocker(
        dictWorkflow, iStepIndex, dictStep,
    )


def _fdictUserDispositionBlocker(dictWorkflow, iStepIndex, dictStep):
    """Return the user-axis blocker for a step, or None when approved.

    Discriminates between ``attestation-stale`` (researcher attested,
    outputs changed since) and ``user-not-approved`` (never attested)
    by inspecting ``sUser`` and ``sLastUserUpdate``. Returns None when
    ``fbStepUserApproved`` is True so the L1 boolean gate is preserved.
    """
    if fbStepUserApproved(dictStep):
        return None
    dictV = dictStep.get("dictVerification", {})
    bStale = (
        dictV.get("sUser") == "stale"
        and dictV.get("sLastUserUpdate") is not None
    )
    if bStale:
        return _fdictAttestationStaleBlocker(
            dictWorkflow, iStepIndex, dictStep,
        )
    return _fdictUserNotApprovedBlocker(dictWorkflow, iStepIndex)


def _fbStepInputDataUndeclared(dictStep):
    """Return True when the step's input contract is unstated.

    Declared means either at least one ``saInputDataFiles`` entry or
    the explicit ``bNoInputData`` flag. Both absent is the third
    state — *undeclared* — and an undeclared step cannot be
    self-consistent: nothing distinguishes "verified there are no raw
    inputs" from "nobody looked."
    """
    return (
        not dictStep.get("saInputDataFiles")
        and not dictStep.get("bNoInputData")
    )


def _fdictInputUndeclaredBlocker(dictWorkflow, iStepIndex):
    """Build the ``input-data-undeclared`` blocker entry for one step."""
    return {
        "iLevel": 1,
        "iStepIndex": iStepIndex,
        "sStepLabel": _fsLabelForStep(dictWorkflow, iStepIndex),
        "sScope": "step",
        "sCriterion": "input-data-undeclared",
        "listOffendingFiles": [],
        "listOffendingUpstreamSteps": [],
        "sRemediationHint": (
            "Declare the step's raw input data files in its Input "
            "Data block, or check 'No input data needed' — Level 1 "
            "requires an explicit declaration"
        ),
    }


def _fdictUpstreamModifiedBlocker(
    dictWorkflow, iStepIndex, dictStep,
    dictNewModTimes, dictUpstreamByStep,
):
    """Build the ``upstream-modified`` blocker entry for one step."""
    listUpstreamIndices = sorted(_flistOffendingUpstream(
        iStepIndex, dictNewModTimes, dictUpstreamByStep,
    ))
    listOffendingFiles = _flistStepOutputFiles(dictStep)
    dictBlocker = {
        "iLevel": 1,
        "iStepIndex": iStepIndex,
        "sStepLabel": _fsLabelForStep(dictWorkflow, iStepIndex),
        "sScope": "step",
        "sCriterion": "upstream-modified",
        "listOffendingFiles": listOffendingFiles,
        "listOffendingUpstreamSteps": listUpstreamIndices,
        "sRemediationHint":
            "Re-run step to clear stale outputs",
    }
    _fnAttachUpstreamFileHints(
        dictBlocker, dictWorkflow, listUpstreamIndices,
    )
    _fnAttachOffendingFileMarks(dictBlocker, "stale")
    return dictBlocker


def _fnAttachUpstreamFileHints(
    dictBlocker, dictWorkflow, listUpstreamIndices,
):
    """Attach per-file hints naming the modified upstream steps."""
    if not listUpstreamIndices or not dictBlocker["listOffendingFiles"]:
        return
    sLabels = ", ".join(
        _fsLabelForStep(dictWorkflow, iUpstream)
        for iUpstream in listUpstreamIndices
    )
    sHint = (
        f"Upstream step {sLabels} modified after this output was "
        "produced — re-run this step"
    )
    dictBlocker["dictOffendingFileHints"] = {
        sPath: sHint for sPath in dictBlocker["listOffendingFiles"]
    }


_S_MARKER_DRIFT_HINT = (
    "Output changed since the last test run (file newer than its "
    "verification marker) — re-run tests, then verify"
)


_T_AXIS_CATEGORY_NAMES = (
    ("sUnitTest", "unit"),
    ("sIntegrity", "integrity"),
    ("sQualitative", "qualitative"),
    ("sQuantitative", "quantitative"),
)


def _flistNonGreenAxes(dictStep):
    """Return ``[(sCategoryName, sValue)]`` for every non-green test axis."""
    dictV = dictStep.get("dictVerification", {}) or {}
    listResult = []
    for sAxisKey, sCategoryName in _T_AXIS_CATEGORY_NAMES:
        if sAxisKey not in dictV:
            continue
        sValue = dictV[sAxisKey]
        if sValue in _T_GREEN_VERIF_VALUES:
            continue
        listResult.append((sCategoryName, sValue))
    return listResult


def _fsJoinAxisNames(listNonGreenAxes, sTargetValue):
    """Join category names matching a value; drop the redundant aggregate."""
    listNames = [
        sName for sName, sValue in listNonGreenAxes
        if sValue == sTargetValue
    ]
    if len(listNames) > 1 and "unit" in listNames:
        listNames = [sName for sName in listNames if sName != "unit"]
    return ", ".join(listNames)


def _fsAxisNotGreenSubState(dictStep):
    """Return the dominant non-green axis state for a step.

    One of ``"failed"``, ``"outputs-missing"``, ``"outputs-changed"``,
    or ``"untested"``, in that priority order — the same ladder the
    remediation hint has always used, extracted so the blocker dict
    can carry the machine-readable cause alongside the prose.
    """
    listValues = [
        sValue for _, sValue in _flistNonGreenAxes(dictStep)
    ]
    for sCandidate in ("failed", "outputs-missing", "outputs-changed"):
        if sCandidate in listValues:
            return sCandidate
    return "untested"


def _fsAxisNotGreenHint(dictStep):
    """Return the state-aware remediation hint for ``axis-not-green``.

    A lookup over :func:`_fsAxisNotGreenSubState` so the hint and the
    blocker's ``sSubState`` field can never disagree about the cause.
    """
    sSubState = _fsAxisNotGreenSubState(dictStep)
    listNonGreenAxes = _flistNonGreenAxes(dictStep)
    if sSubState == "failed":
        sFailedNames = _fsJoinAxisNames(listNonGreenAxes, "failed")
        return f"Re-run failing tests ({sFailedNames}), then verify"
    if sSubState == "outputs-missing":
        return "Declared output missing — re-run step, then verify"
    if sSubState == "outputs-changed":
        return _S_MARKER_DRIFT_HINT
    sUntestedNames = _fsJoinAxisNames(listNonGreenAxes, "untested")
    if sUntestedNames:
        return (
            f"Test category never run ({sUntestedNames}) — "
            "run tests, then verify"
        )
    return "Re-run failing tests, then verify"


_DICT_AXIS_SUBSTATE_FILE_MARKS = {
    "failed": "failed",
    "outputs-missing": "missing",
    "outputs-changed": "stale",
}


def _fnAttachOffendingFileMarks(dictBlocker, sMark):
    """Attach ``dictOffendingFileMarks`` mirroring ``listOffendingFiles``.

    Every key is exactly one of the blocker's offending-file paths and
    every value is one of ``"stale"`` / ``"failed"`` / ``"missing"``.
    A falsy ``sMark`` (e.g. the ``untested`` axis sub-state, whose
    files are not wrong, merely unexercised) attaches nothing so the
    dashboard never paints a false defect on an untested file.
    """
    if not sMark:
        return
    dictBlocker["dictOffendingFileMarks"] = {
        sPath: sMark for sPath in dictBlocker["listOffendingFiles"]
    }


def _fbAxisDriftIsRootCause(dictStep):
    """Return True iff marker drift, not failure, explains axis-not-green."""
    listValues = [sValue for _, sValue in _flistNonGreenAxes(dictStep)]
    if "failed" in listValues or "outputs-missing" in listValues:
        return False
    return "outputs-changed" in listValues


def _flistMarkerDriftFiles(dictStep, filesRepo, listDeclared):
    """Narrow declared outputs to those listed in ``listModifiedFiles``.

    ``listModifiedFiles`` holds repo-relative paths from the marker
    hash comparison while ``listOffendingFiles`` carries the raw
    declared paths, so each declared path is mapped to its
    repo-relative form (same resolution as
    ``_flistStepOutputsRepoRelative``) before matching. Falls back to
    the full declared list when nothing matches, so the blocker never
    under-reports.
    """
    listModified = (dictStep.get("dictVerification", {}) or {}).get(
        "listModifiedFiles", []) or []
    if not listModified:
        return listDeclared
    setModified = set(listModified)
    dictRelativeByRaw = _fdictRepoRelativeByRawPath(
        dictStep, filesRepo, listDeclared,
    )
    listNarrowed = [
        sRaw for sRaw in listDeclared
        if sRaw in setModified or dictRelativeByRaw.get(sRaw) in setModified
    ]
    return listNarrowed or listDeclared


def _fdictRepoRelativeByRawPath(dictStep, filesRepo, listDeclared):
    """Map each raw declared output path to its repo-relative form."""
    sRepoRoot = fsRepoRootOf(filesRepo)
    if not sRepoRoot:
        return {}
    from vaibify.gui.fileStatusManager import _fsResolveStepFilePath
    from vaibify.gui.pathContract import fsAbsToRepoRelative
    sStepDir = dictStep.get("sDirectory", "") or ""
    dictResult = {}
    for sRaw in listDeclared:
        sAbs = _fsResolveStepFilePath(
            sRaw, sStepDir, {"sRepoRoot": sRepoRoot},
        )
        dictResult[sRaw] = fsAbsToRepoRelative(sAbs, sRepoRoot)
    return dictResult


def _fdictAxisNotGreenBlocker(
    dictWorkflow, iStepIndex, dictStep, filesRepo=None,
):
    """Build the ``axis-not-green`` blocker entry for one step.

    The remediation hint is state-aware (failed vs. marker drift vs.
    never run). For pure marker drift the offending files narrow to
    the drifted outputs and each carries a per-file hint in
    ``dictOffendingFileHints``. The file marks attach after the drift
    narrowing so their keys mirror the final offending-file list.
    """
    sSubState = _fsAxisNotGreenSubState(dictStep)
    dictBlocker = {
        "iLevel": 1,
        "iStepIndex": iStepIndex,
        "sStepLabel": _fsLabelForStep(dictWorkflow, iStepIndex),
        "sScope": "step",
        "sCriterion": "axis-not-green",
        "sSubState": sSubState,
        "listOffendingFiles": _flistStepOutputFiles(dictStep),
        "listOffendingUpstreamSteps": [],
        "sRemediationHint": _fsAxisNotGreenHint(dictStep),
    }
    _fnDecorateAxisNotGreenBlocker(
        dictBlocker, dictStep, filesRepo, sSubState,
    )
    return dictBlocker


def _fnDecorateAxisNotGreenBlocker(
    dictBlocker, dictStep, filesRepo, sSubState,
):
    """Apply drift narrowing, then attach the per-file marks.

    Order matters: marks must mirror the *narrowed* offending files
    when marker drift is the root cause.
    """
    if _fbAxisDriftIsRootCause(dictStep):
        _fnAttachMarkerDriftFileHints(
            dictBlocker, dictStep, filesRepo,
        )
    _fnAttachOffendingFileMarks(
        dictBlocker, _DICT_AXIS_SUBSTATE_FILE_MARKS.get(sSubState),
    )


def _fnAttachMarkerDriftFileHints(dictBlocker, dictStep, filesRepo):
    """Narrow offending files to drifted outputs and add per-file hints."""
    listNarrowed = _flistMarkerDriftFiles(
        dictStep, filesRepo, dictBlocker["listOffendingFiles"],
    )
    dictBlocker["listOffendingFiles"] = listNarrowed
    dictBlocker["dictOffendingFileHints"] = {
        sPath: _S_MARKER_DRIFT_HINT for sPath in listNarrowed
    }


def _fdictUserNotApprovedBlocker(dictWorkflow, iStepIndex):
    """Build the ``user-not-approved`` blocker entry for one step."""
    return {
        "iLevel": 1,
        "iStepIndex": iStepIndex,
        "sStepLabel": _fsLabelForStep(dictWorkflow, iStepIndex),
        "sScope": "step",
        "sCriterion": "user-not-approved",
        "listOffendingFiles": [],
        "listOffendingUpstreamSteps": [],
        "sRemediationHint":
            "Step has never been verified — click verify when satisfied",
    }


def _fdictAttestationStaleBlocker(dictWorkflow, iStepIndex, dictStep):
    """Build the ``attestation-stale`` blocker entry for one step.

    Fires when the researcher previously attested (``sLastUserUpdate``
    present) but the outputs changed since, flipping ``sUser`` to
    ``stale``. ``listOffendingFiles`` projects the step's declared
    outputs so the dashboard can mark them red with the
    *re-verify-or-re-run* remediation; each offending file also
    carries the same fact in ``dictOffendingFileHints``.
    """
    listOffendingFiles = _flistStepOutputFiles(dictStep)
    sFileHint = "This output changed after you verified — re-verify or re-run"
    dictBlocker = {
        "iLevel": 1,
        "iStepIndex": iStepIndex,
        "sStepLabel": _fsLabelForStep(dictWorkflow, iStepIndex),
        "sScope": "step",
        "sCriterion": "attestation-stale",
        "listOffendingFiles": listOffendingFiles,
        "listOffendingUpstreamSteps": [],
        "sRemediationHint":
            "Outputs changed since you verified — re-verify or re-run",
        "dictOffendingFileHints": {
            sPath: sFileHint for sPath in listOffendingFiles
        },
    }
    _fnAttachOffendingFileMarks(dictBlocker, "stale")
    return dictBlocker


def _fbStepScriptStale(
    iStepIndex, dictStep, dictScriptStatus, filesRepo,
):
    """Return True iff the step's script is newer than its outputs.

    Fires when ``_fdictBuildScriptStatus`` reports ``sStatus='modified'``
    for this step and the outputs' content does not still match
    ``MANIFEST.sha256``. The manifest short-circuit prevents a fresh
    git clone — where every mtime is "now" — from tripping the
    criterion before the workflow has been re-run. When the caller
    does not supply ``dictScriptStatus`` (legacy paths) the criterion
    is silently skipped so the historical truth-table is preserved.
    """
    if not dictScriptStatus:
        return False
    dictEntry = dictScriptStatus.get(iStepIndex)
    if not dictEntry or dictEntry.get("sStatus") != "modified":
        return False
    return not _fbStepHashesMatchManifest(
        dictStep, filesRepo,
    )


def _fbStepHashesMatchManifest(dictStep, filesRepo):
    """Return True iff every declared output's hash matches MANIFEST.sha256.

    Delegates to ``hashStaleness`` for the manifest read and the
    per-output content comparison so the suppression rule has the
    same authority the file-status manager uses. Conservative on every
    error path: missing repo, missing manifest, no declared outputs,
    or any drifted entry returns False so the script-stale criterion
    remains visible.
    """
    if not fsRepoRootOf(filesRepo):
        return False
    listRelPaths = _flistStepOutputsRepoRelative(
        dictStep, filesRepo,
    )
    if not listRelPaths:
        return False
    from vaibify.gui import hashStaleness
    if not hashStaleness.fbManifestExists(filesRepo):
        return False
    dictEntries = hashStaleness._fdictReadManifestEntries(filesRepo)
    if not dictEntries:
        return False
    if _fbAnyOutputMissingFromManifest(listRelPaths, dictEntries):
        return False
    setStale = hashStaleness.fsetStaleOutputsAgainstManifest(
        filesRepo, listRelPaths, {},
    )
    return len(setStale) == 0


def _fbAnyOutputMissingFromManifest(listRelPaths, dictEntries):
    """Return True iff any declared output is absent from the manifest."""
    for sRelPath in listRelPaths:
        if sRelPath not in dictEntries:
            return True
    return False


def _flistStepOutputsRepoRelative(dictStep, filesRepo):
    """Return repo-relative output paths declared on a step.

    Resolves each ``saOutputDataFiles``/``saPlotFiles`` entry against the
    step directory the same way ``_fsResolveStepFilePath`` does, then
    strips the repo root so the result lines up with manifest keys.
    Lazily imports the GUI helper so the reproducibility leaf stays
    importable without GUI side effects at module load.
    """
    from vaibify.gui.fileStatusManager import _fsResolveStepFilePath
    from vaibify.gui.pathContract import fsAbsToRepoRelative
    sRepoRoot = fsRepoRootOf(filesRepo)
    sStepDir = dictStep.get("sDirectory", "") or ""
    listRelative = []
    for sFile in (dictStep.get("saOutputDataFiles", []) or []) + (
        dictStep.get("saPlotFiles", []) or []
    ):
        if not sFile:
            continue
        sAbs = _fsResolveStepFilePath(
            sFile, sStepDir, {"sRepoRoot": sRepoRoot},
        )
        listRelative.append(
            fsAbsToRepoRelative(sAbs, sRepoRoot),
        )
    return listRelative


def _fdictScriptStaleBlocker(dictWorkflow, iStepIndex, dictStep):
    """Build the ``script-stale`` blocker entry for one step.

    Fires when the step's script mtime is newer than its outputs,
    indicating the researcher edited the producer without re-running.
    ``listOffendingFiles`` projects the step's declared outputs so the
    dashboard can mark them with the *re-run-to-clear* remediation.
    Conforms to the unified blocker schema (Section A of the
    AICS-ladder plan): every L1 entry carries ``iLevel``, ``sScope``,
    and a non-empty ``sRemediationHint`` so the Section G tooltip
    pipeline can read it directly.
    """
    dictBlocker = {
        "iLevel": 1,
        "iStepIndex": iStepIndex,
        "sStepLabel": _fsLabelForStep(dictWorkflow, iStepIndex),
        "sScope": "step",
        "sCriterion": "script-stale",
        "listOffendingFiles": _flistStepOutputFiles(dictStep),
        "listOffendingUpstreamSteps": [],
        "sRemediationHint":
            "Script edited after output — re-run step to clear blocker",
    }
    _fnAttachOffendingFileMarks(dictBlocker, "stale")
    return dictBlocker


def _flistStepOutputFiles(dictStep):
    """Return repo-relative data + plot file paths declared on a step."""
    listFiles = []
    for sKey in ("saOutputDataFiles", "saPlotFiles"):
        for sPath in dictStep.get(sKey, []) or []:
            if isinstance(sPath, str) and sPath:
                listFiles.append(sPath)
    return listFiles


def _flistOffendingUpstream(
    iConsumerIndex, dictNewModTimes, dictUpstreamByStep,
):
    """Return upstream indices whose max mtime exceeds the consumer's."""
    iConsumerMtime = _fiStepMaxMtime(iConsumerIndex, dictNewModTimes)
    listOffending = []
    for iUpstreamIndex in dictUpstreamByStep.get(iConsumerIndex, []):
        iUpstreamMtime = _fiStepMaxMtime(iUpstreamIndex, dictNewModTimes)
        if iUpstreamMtime > iConsumerMtime:
            listOffending.append(iUpstreamIndex)
    return listOffending


def _fiStepMaxMtime(iStepIndex, dictNewModTimes):
    """Return integer max mtime for a step, 0 if missing or malformed."""
    sValue = (dictNewModTimes or {}).get(str(iStepIndex))
    if sValue is None:
        return 0
    try:
        return int(sValue)
    except (TypeError, ValueError):
        return 0


def _fdictUpstreamStepsByConsumer(dictWorkflow):
    """Return ``{iConsumerIndex: [iUpstreamIndex, ...]}`` for declared edges.

    Inverts ``fdictBuildDirectDependencies``'s
    ``{iUpstream: set(iDownstream)}`` representation. Lazily imports
    workflowManager so the reproducibility leaf does not take a
    load-time dependency on the GUI subtree. Catches the broad family
    of attribute / type / lookup errors raised by the upstream parser
    when a workflow carries corrupt ``listSteps`` entries (``None``,
    string, missing key) so a malformed workflow degrades to "no
    declared edges" rather than crashing the L1 gate.
    """
    from vaibify.gui.workflowManager import fdictBuildDirectDependencies
    try:
        dictDirect = fdictBuildDirectDependencies(dictWorkflow)
    except (AttributeError, KeyError, TypeError):
        return {}
    dictResult = {}
    for iUpstream, setDownstream in (dictDirect or {}).items():
        for iDownstream in setDownstream:
            dictResult.setdefault(iDownstream, []).append(iUpstream)
    return dictResult


def _fsLabelForStep(dictWorkflow, iStepIndex):
    """Return ``sLabel`` for a step, falling back to the canonical generator.

    Falls back to a numeric ``"NN"`` label when the canonical generator
    would dereference a corrupt step entry (``None`` / non-dict); the
    label is purely diagnostic and never load-bearing for routing.
    """
    listSteps = dictWorkflow.get("listSteps", []) or []
    if 0 <= iStepIndex < len(listSteps):
        dictStored = listSteps[iStepIndex]
        if isinstance(dictStored, dict):
            sStoredLabel = dictStored.get("sLabel")
            if isinstance(sStoredLabel, str) and sStoredLabel:
                return sStoredLabel
    from vaibify.gui.pipelineUtils import fsLabelFromStepIndex
    try:
        return fsLabelFromStepIndex(dictWorkflow, iStepIndex)
    except (AttributeError, TypeError):
        return f"{iStepIndex + 1:02d}"


def fbStepIsAtLeastLevel1(
    dictStep, dictScriptStatus=None, iStepIndex=None,
):
    """Return True iff a single step meets the L1 per-step criteria.

    Thin composition of the orthogonal predicates so callers can ask
    "is this step contributing to L1" without re-implementing the rule
    (e.g. file-status badges, auto-archive transition). When
    ``dictScriptStatus`` is supplied, a ``sStatus='modified'`` entry
    for ``iStepIndex`` also blocks the step; legacy callers that omit
    these parameters preserve the historical truth-table.
    """
    if not isinstance(dictStep, dict):
        return False
    if not fbStepIsAiDeclaration(dictStep) and (
        _fbStepInputDataUndeclared(dictStep)
    ):
        # A step whose input contract is unstated is not
        # self-consistent — the same rule the L1 blocker and cell
        # enforce. ai-declaration steps are L1-not-applicable.
        return False
    if not fbStepUserApproved(dictStep):
        return False
    if not fbStepTimingClean(dictStep):
        return False
    if not fbStepTestsPassing(dictStep):
        return False
    if dictScriptStatus and iStepIndex is not None:
        dictEntry = dictScriptStatus.get(iStepIndex) or {}
        if dictEntry.get("sStatus") == "modified":
            return False
    return True


def fbWorkflowHasProjectRepo(filesRepo):
    """Return True iff the workflow has a non-empty project repo path.

    L1's "under git control" criterion is the existence of the repo
    discovery itself — the load-time auto-detector only populates
    ``sProjectRepoPath`` when the workflow.json lives inside a git
    work tree. Tracked-and-matched semantics belong to L2. Accepts a
    path string or a ``repoFiles`` adapter (whose root is consulted).
    """
    return bool(fsRepoRootOf(filesRepo))


def fbAtLeastLevel2(dictWorkflow, filesRepo):
    """Return True iff the workflow meets the L2 Publication gate.

    L2 builds on L1 with three additional criteria: every canonical
    file's hash matches the GitHub mirror at a recently-verified
    SHA, every Zenodo-published file's hash matches at a known DOI on
    the workflow's configured endpoint, and the workflow contains an
    attested AI Declaration step (the declaration only has meaning at
    publication, so its sign-off lives here, not at L1).
    """
    dictMemo = _fdictActiveLevelMemo()
    if dictMemo is not None and "bL2" in dictMemo:
        return dictMemo["bL2"]
    bResult = _fbComputeLevel2(dictWorkflow, filesRepo)
    if dictMemo is not None:
        dictMemo["bL2"] = bResult
    return bResult


def _fbComputeLevel2(dictWorkflow, filesRepo):
    """Uncached L2 evaluation — the body of the original gate.

    The arXiv conjunct is opt-in: recording an arXiv ID claims
    correspondence with the posted e-print, so the claim is checked.
    A workflow with no recorded arXiv submission (whether or not an
    Overleaf manuscript is bound) leaves the conjunct trivially True —
    manuscript posting happens outside vaibify on its own timeline and
    must not block publication of the code and data.
    """
    filesRepo = ffilesEnsureRepoFiles(filesRepo)
    if not fbAtLeastLevel1(dictWorkflow, filesRepo):
        return False
    if not fbWorkflowFullySyncedWithGithub(
        dictWorkflow, filesRepo,
    ):
        return False
    if not fbWorkflowFullySyncedWithZenodo(
        dictWorkflow, filesRepo,
    ):
        return False
    if not fbWorkflowAiDeclarationAttested(dictWorkflow):
        return False
    if not fbWorkflowFullySyncedWithArxiv(
        dictWorkflow, filesRepo,
    ):
        return False
    return True


def fbAtLeastLevel3(dictWorkflow, filesRepo):
    """Return True iff the workflow meets the L3 Reproducibility gate.

    L3 requires L2 plus a green readiness check (six orthogonal
    verifiers) plus a non-stale, ``passed`` L3 attestation on file.
    The expensive rebuild that produces the attestation is the
    only L3 criterion that touches a multi-hour operation; the
    other five are cheap and re-evaluated on every level recompute.
    """
    filesRepo = ffilesEnsureRepoFiles(filesRepo)
    if not fbAtLeastLevel2(dictWorkflow, filesRepo):
        return False
    if not fbL3ReadinessOK(dictWorkflow, filesRepo):
        return False
    if not fbL3AttestationCurrent(filesRepo):
        return False
    return True


def fbL3ReadinessOK(dictWorkflow, filesRepo):
    """Return True iff every cheap L3 readiness verifier passes.

    The composition is intentionally short: each verifier owns its
    own gap surface so the dashboard can render per-criterion fix
    links. ``fbL3AttestationCurrent`` is *not* part of readiness —
    readiness answers "is the envelope coherent enough to bother
    attempting a rebuild?", attestation answers "has that rebuild
    actually been done and verified?".
    """
    filesRepo = ffilesEnsureRepoFiles(filesRepo)
    if not fbWorkflowHasProjectRepo(filesRepo):
        return False
    return (
        fbVerifyManifestComplete(filesRepo, dictWorkflow)
        and fbVerifyDependencyLock(filesRepo)
        and fbVerifyEnvironmentSnapshot(filesRepo)
        and fbVerifyDockerfilePinned(filesRepo)
        and fbVerifyReproduceScript(filesRepo, dictWorkflow)
        and fbVerifyDeterminismDeclared(filesRepo, dictWorkflow)
        and fbWorkflowDeclaresBinaries(dictWorkflow)
    )


def fbWorkflowDeclaresBinaries(dictWorkflow):
    """Return True iff the workflow has a coherent binary-declaration state.

    Exactly one of two states is valid:

    * Waiver: ``bNoStandaloneBinaries`` is True AND
      ``listDeclaredBinaries`` is empty.
    * Declaration: ``bNoStandaloneBinaries`` is False AND
      ``listDeclaredBinaries`` is a non-empty list of entries with
      string ``sBinaryPath``, ``sPurpose``, and ``sExpectedVersion``.

    Any other shape — waiver with a non-empty declaration list, an
    unset waiver with no declaration, or malformed entries — fails
    so the L3 gate cannot close on a half-answered question.
    """
    if not isinstance(dictWorkflow, dict):
        return False
    bWaiver = bool(dictWorkflow.get("bNoStandaloneBinaries", False))
    listDeclared = dictWorkflow.get("listDeclaredBinaries") or []
    if not isinstance(listDeclared, list):
        return False
    if bWaiver:
        return len(listDeclared) == 0
    if not listDeclared:
        return False
    return all(_fbBinaryDeclarationEntryValid(e) for e in listDeclared)


def _fbBinaryDeclarationEntryValid(dictEntry):
    """Return True iff a single declared-binary entry has all three fields."""
    if not isinstance(dictEntry, dict):
        return False
    for sKey in ("sBinaryPath", "sPurpose", "sExpectedVersion"):
        sValue = dictEntry.get(sKey)
        if not isinstance(sValue, str) or not sValue.strip():
            return False
    return True


def fbVerifyManifestComplete(filesRepo, dictWorkflow):
    """Return True iff every workflow-declared path is in the manifest.

    A missing manifest is treated as failure (no envelope at all),
    a populated manifest with zero declared-but-missing entries is
    a pass. The check delegates to ``manifestWriter`` so the
    completeness rule stays in one place.
    """
    try:
        listMissing = flistDeclaredButMissingFromManifest(
            filesRepo, dictWorkflow,
        )
    except FileNotFoundError:
        return False
    except (OSError, ValueError):
        return False
    return not listMissing


def fbVerifyDependencyLock(filesRepo):
    """Return True iff ``requirements.lock`` exists and every entry is hashed."""
    listIssues = flistVerifyRequirementsLock(filesRepo)
    return not listIssues


def fbVerifyEnvironmentSnapshot(filesRepo):
    """Return True iff ``.vaibify/environment.json`` records a sha256 digest."""
    return fbEnvironmentDigestPinned(filesRepo)


def fbVerifyDockerfilePinned(filesRepo):
    """Return True iff the Dockerfile passes the L3 pin lint."""
    listIssues = flistLintDockerfile(filesRepo)
    return not listIssues


def fbVerifyReproduceScript(filesRepo, dictWorkflow):
    """Return True iff ``reproduce.sh`` exists and is in MANIFEST.sha256.

    Presence-on-disk alone is insufficient: an unhashed copy could
    be tampered with. The script's repo-relative path must appear
    in the parsed manifest entries so a downstream consumer's
    ``sha256sum -c`` would detect drift.
    """
    filesRepo = ffilesEnsureRepoFiles(filesRepo)
    if not filesRepo.fbIsFile(S_REPRODUCE_SCRIPT_FILENAME):
        return False
    try:
        listEntries = flistParseManifestLines(filesRepo)
    except (FileNotFoundError, OSError, ValueError):
        return False
    setPaths = {dictEntry["sPath"] for dictEntry in listEntries}
    return S_REPRODUCE_SCRIPT_FILENAME in setPaths


def fbVerifyDeterminismDeclared(filesRepo, dictWorkflow):
    """Return True iff no step warns about unseeded RNG and BLAS is declared.

    The check rejects any step carrying ``bUnseededRandomnessWarning``
    in addition to requiring the workflow-level
    ``dictDeterminism`` block (or its waiver). ``filesRepo`` is
    accepted for symmetry with the other verifiers; the audit is
    workflow-level so the repo is only used by future per-script
    extensions.
    """
    del filesRepo  # noqa: F841 — reserved for future per-script audit
    listIssues = flistAuditWorkflow(dictWorkflow)
    return not listIssues


def _fdictCollectL3ReadinessFlags(dictWorkflow, filesRepo, bRepo):
    """Return the per-verifier booleans that gate L3 readiness."""
    return {
        "bManifestComplete": bRepo and fbVerifyManifestComplete(
            filesRepo, dictWorkflow,
        ),
        "bDependencyLockHashed": bRepo and fbVerifyDependencyLock(
            filesRepo,
        ),
        "bEnvironmentDigestPinned": bRepo and fbVerifyEnvironmentSnapshot(
            filesRepo,
        ),
        "bDockerfilePinned": bRepo and fbVerifyDockerfilePinned(
            filesRepo,
        ),
        "bReproduceScriptPinned": bRepo and fbVerifyReproduceScript(
            filesRepo, dictWorkflow,
        ),
        "bDeterminismDeclared": bRepo and fbVerifyDeterminismDeclared(
            filesRepo, dictWorkflow,
        ),
        "bBinariesDeclaredOrWaived": bRepo and fbWorkflowDeclaresBinaries(
            dictWorkflow,
        ),
    }


def fdictL3ReadinessGaps(dictWorkflow, filesRepo):
    """Return per-verifier pass/fail for the L3 readiness card.

    The shape matches what the AICS tab's L3 readiness card binds
    against; missing entries are explicit so the rendering code can
    iterate keys directly. The ``bL3AttestationCurrent`` entry is a
    separate read so the UI can render the "Verify L3 Reproducibility"
    button state independently of the readiness verifiers.
    """
    filesRepo = ffilesEnsureRepoFiles(filesRepo)
    bRepo = fbWorkflowHasProjectRepo(filesRepo)
    dictFlags = _fdictCollectL3ReadinessFlags(
        dictWorkflow, filesRepo, bRepo,
    )
    bAllReadiness = all(dictFlags.values())
    dictResult = {sKey: bool(bValue) for sKey, bValue in dictFlags.items()}
    dictResult["bL3AttestationCurrent"] = (
        fbL3AttestationCurrent(filesRepo) if bRepo else False
    )
    dictResult["bL3ReadinessOK"] = bool(bAllReadiness)
    dictResult["sManifestDigest"] = (
        fsCurrentManifestDigest(filesRepo) if bRepo else ""
    )
    return dictResult


def fbWorkflowHasAiDeclarationStep(dictWorkflow):
    """Return True iff the workflow lists at least one ai-declaration step."""
    if not isinstance(dictWorkflow, dict):
        return False
    for dictStep in dictWorkflow.get("listSteps", []) or []:
        if fbStepIsAiDeclaration(dictStep):
            return True
    return False


def fbWorkflowAiDeclarationAttested(dictWorkflow):
    """Return True iff an ai-declaration step exists and is attested.

    The declaration only has meaning at publication, so its
    researcher sign-off is a LEVEL 2 requirement (ruling 2026-07-02).
    AI-declaration steps are excluded from the L1 gate entirely and
    their L1 cell reads not-applicable.
    """
    if not isinstance(dictWorkflow, dict):
        return False
    bFound = False
    for dictStep in dictWorkflow.get("listSteps", []) or []:
        if not fbStepIsAiDeclaration(dictStep):
            continue
        bFound = True
        if not fbStepUserApproved(dictStep):
            return False
    return bFound


def _fbCachedSyncStatusFresh(dictStatus, fMaxStaleHours):
    """Return True iff the cached verify timestamp is within the budget.

    Treats a missing or malformed timestamp as stale so the gate
    cannot be lit by a never-verified cache file. Tolerates the ISO
    ``Z`` suffix used by ``_fsBuildIsoTimestamp``.
    """
    sLastVerified = (dictStatus or {}).get("sLastVerified")
    if not sLastVerified:
        return False
    try:
        sNormalized = sLastVerified.replace("Z", "+00:00")
        dtVerified = datetime.fromisoformat(sNormalized)
    except (TypeError, ValueError):
        return False
    if dtVerified.tzinfo is None:
        dtVerified = dtVerified.replace(tzinfo=timezone.utc)
    fDeltaHours = (
        datetime.now(timezone.utc) - dtVerified
    ).total_seconds() / 3600.0
    return fDeltaHours <= fMaxStaleHours


def _fbCachedSyncStatusFullMatch(dictStatus):
    """Return True iff every manifest file matched the remote."""
    if not dictStatus:
        return False
    iTotal = dictStatus.get("iTotalFiles", 0) or 0
    if iTotal == 0:
        return False
    if dictStatus.get("iMatching") != iTotal:
        return False
    if dictStatus.get("listDiverged"):
        return False
    return True


def fbWorkflowFullySyncedWithGithub(
    dictWorkflow, filesRepo,
):
    """Return True iff every manifest file matches the GitHub mirror.

    The check is the conjunction of: a fresh cached verify
    (``sLastVerified`` within :data:`F_MAX_STALE_HOURS`), every file
    matched, and the verification captured the commit SHA that the
    project repo is currently at. The last clause prevents an old
    successful verify from lighting the gate after the researcher has
    made (but not yet pushed) new commits.
    """
    dictStatus = scheduledReverify.fdictReadCachedSyncStatus(
        filesRepo, "github",
    )
    if not _fbCachedSyncStatusFullMatch(dictStatus):
        return False
    if not _fbCachedSyncStatusFresh(dictStatus, F_MAX_STALE_HOURS):
        return False
    return _fbGithubHeadMatchesVerifiedSha(
        dictWorkflow, dictStatus,
    )


def _fbGithubHeadMatchesVerifiedSha(dictWorkflow, dictStatus):
    """Return True iff the workflow's GitHub config records the verified SHA.

    The verified SHA is captured during ``fdictVerifyRemoteService``
    from the workflow's ``dictRemotes['github']['sCommittedSha']``;
    L2 requires the live workflow config to still carry the same value
    (i.e., the researcher has not committed-and-not-pushed since the
    last verify). When the workflow has no SHA at all, we treat the
    check as permissive — verify will have run against the branch HEAD
    and the cache being fresh is the strongest signal we have.
    """
    sVerifiedSha = dictStatus.get("sCommittedShaVerified") or ""
    dictRemotes = (dictWorkflow or {}).get("dictRemotes") or {}
    dictGithub = dictRemotes.get("github") or {}
    sLiveSha = dictGithub.get("sCommittedSha") or ""
    if not sVerifiedSha and not sLiveSha:
        return True
    return sVerifiedSha == sLiveSha


def fbWorkflowFullySyncedWithZenodo(
    dictWorkflow, filesRepo,
):
    """Return True iff every manifest file matches the Zenodo deposit.

    In addition to the freshness + full-match check, the workflow must
    list a non-empty DOI and the cached verification must have run
    against the same endpoint (``sandbox`` vs production) that the
    workflow is currently configured for. Endpoint mismatch indicates
    the researcher published to one service and verified against the
    other.
    """
    dictStatus = scheduledReverify.fdictReadCachedSyncStatus(
        filesRepo, "zenodo",
    )
    if not _fbCachedSyncStatusFullMatch(dictStatus):
        return False
    if not _fbCachedSyncStatusFresh(dictStatus, F_MAX_STALE_HOURS):
        return False
    if not (dictStatus.get("sZenodoDoi") or ""):
        return False
    return _fbZenodoEndpointMatches(dictWorkflow, dictStatus)


def _fbZenodoEndpointMatches(dictWorkflow, dictStatus):
    """Return True iff the verified endpoint matches the workflow's config."""
    sVerifiedEndpoint = (
        dictStatus.get("sEndpointVerified") or ""
    )
    dictRemotes = (dictWorkflow or {}).get("dictRemotes") or {}
    dictZenodo = dictRemotes.get("zenodo") or {}
    sLiveEndpoint = dictZenodo.get("sService") or (
        dictWorkflow.get("sZenodoService") or "sandbox"
    )
    if not sVerifiedEndpoint:
        return False
    return sVerifiedEndpoint == sLiveEndpoint


def fbWorkflowHasOverleafBinding(dictWorkflow):
    """Return True iff the workflow has a non-empty Overleaf binding.

    The per-step ``figure-not-frozen`` criterion is suppressed when
    this returns False: a data-only workflow is L2-publishable without
    a manuscript. The L2 arXiv criteria key on
    :func:`fbWorkflowHasArxivConnection` instead — binding a
    manuscript for figure tracking must not force an arXiv submission.
    """
    dictRemotes = (dictWorkflow or {}).get("dictRemotes") or {}
    dictOverleaf = dictRemotes.get("overleaf") or {}
    return bool(dictOverleaf.get("sProjectId") or "")


def fbWorkflowHasArxivConnection(dictWorkflow):
    """Return True iff the workflow records an arXiv submission ID.

    This is the opt-in trigger for every L2 arXiv criterion: recording
    an ID claims correspondence with the posted e-print, so the claim
    is checked; a workflow without one is "not tracked" — neutral,
    never a gap. arXiv verification is only possible after a posting
    exists, so an unconfigured connection must not block L2.
    """
    return bool(_fdictArxivConfig(dictWorkflow).get("sArxivId") or "")


def fbWorkflowFullySyncedWithArxiv(dictWorkflow, filesRepo):
    """Return True iff the workflow's arXiv submission matches the manuscript.

    True trivially when no arXiv submission is recorded — the arXiv
    criterion is an opt-in claim, not a publication prerequisite. With
    a recorded ``sArxivId``, requires every Overleaf-pushed figure's
    arXiv-tarball hash to equal the hash of its current local content
    (the same live authority the L2 verifies use — the L3 manifest
    plays no role at this level) and an ``sArxivVersion`` equal to
    the latest arXiv advertises. ``ArxivError`` from the client is
    treated as "not synced".
    """
    if not fbWorkflowHasArxivConnection(dictWorkflow):
        return True
    dictArxiv = _fdictArxivConfig(dictWorkflow)
    if not _fbArxivTarballMatchesPushManifest(
        dictWorkflow, filesRepo,
    ):
        return False
    return _fbArxivVersionCurrent(dictArxiv)


def _fdictArxivConfig(dictWorkflow):
    """Return the workflow's ``dictRemotes.arxiv`` config, or an empty dict."""
    dictRemotes = (dictWorkflow or {}).get("dictRemotes") or {}
    dictArxiv = dictRemotes.get("arxiv") or {}
    return dictArxiv if isinstance(dictArxiv, dict) else {}


def _fbArxivTarballMatchesPushManifest(dictWorkflow, filesRepo):
    """Return True iff the e-print matches every pushed figure's content."""
    from .overleafSync import flistOverleafPushedFiguresAt
    sCommit = _fsOverleafRecordedCommit(dictWorkflow)
    listPushed = flistOverleafPushedFiguresAt(
        filesRepo, sCommit,
    )
    if not listPushed:
        return False
    return _fbArxivHashesCoverPushList(
        dictWorkflow, filesRepo, listPushed,
    )


def _fbArxivHashesCoverPushList(
    dictWorkflow, filesRepo, listPushed,
):
    """Return True iff the e-print matches every pushed figure's live hash.

    The expected side is each pushed figure's CURRENT local content —
    the same authority the L2 verifies use — so the gate demands
    content equality with the figures as they exist now, never
    name-level presence and never the L3 manifest's possibly-lagging
    pins. Conservative on every error path: an unhashable or missing
    local figure, or any client failure, returns False.
    """
    from . import arxivClient
    dictExpected = _fdictLiveHashesOrNone(filesRepo, listPushed)
    if dictExpected is None:
        return False
    dictArxiv = _fdictArxivConfig(dictWorkflow)
    sArxivId = dictArxiv.get("sArxivId") or ""
    dictPathMap = dictArxiv.get("dictPathMap") or None
    sCacheDir = scheduledReverify.fsArxivCacheDir(filesRepo)
    try:
        dictHashes = arxivClient.fdictFetchRemoteHashes(
            sArxivId, listPushed,
            dictPathMap=dictPathMap, sCacheDir=sCacheDir,
        )
    except arxivClient.ArxivError:
        return False
    return all(
        dictExpected.get(sPath)
        and dictHashes.get(sPath) == dictExpected.get(sPath)
        for sPath in listPushed
    )


def _fdictLiveHashesOrNone(filesRepo, listRelPaths):
    """Hash the current local content of the paths, or None on error.

    Ensures the adapter BEFORE the guarded call: callers hand in raw
    path strings on some routes, and letting the resulting
    ``AttributeError`` disappear into the conservative None would
    silently fail every gate evaluation on those routes.
    """
    filesRepo = ffilesEnsureRepoFiles(filesRepo)
    try:
        dictEntries = filesRepo.fdictHashFiles(listRelPaths)
    except Exception:
        return None
    return {
        sPath: dictEntry.get("sSha256")
        for sPath, dictEntry in dictEntries.items()
        if isinstance(dictEntry, dict) and dictEntry.get("sSha256")
    }


def _fbArxivVersionCurrent(dictArxiv):
    """Return True iff the recorded ``sArxivVersion`` is the latest published."""
    from . import arxivClient
    sRecorded = dictArxiv.get("sArxivVersion") or ""
    if not sRecorded:
        return False
    try:
        sLatest = arxivClient.fsResolveLatestVersion(
            dictArxiv.get("sArxivId") or "",
        )
    except arxivClient.ArxivError:
        return False
    return sRecorded == sLatest


def _fsOverleafRecordedCommit(dictWorkflow):
    """Return the git commit hash recorded for the last Overleaf push."""
    dictRemotes = (dictWorkflow or {}).get("dictRemotes") or {}
    dictOverleaf = dictRemotes.get("overleaf") or {}
    return dictOverleaf.get("sLastPushCommit") or ""


def fdictLevel2Gaps(dictWorkflow, filesRepo):
    """Return per-criterion pass/fail for the L2 readiness card.

    Returned shape::

        {
            "bAtLeastLevel1": bool,
            "bGithubFullySynced": bool,
            "bZenodoFullySynced": bool,
            "bArxivFullySynced": bool,
            "bAiDeclarationAttested": bool,
            "bAtLeastLevel2": bool,
        }

    The frontend AICS tab consumes this dict directly; each False
    entry maps to a red row with a "fix here" link. ``bArxivFullySynced``
    is True trivially when the workflow records no arXiv submission so
    an untracked manuscript does not surface a fake gap.
    ``bAiDeclarationAttested`` requires the step to exist AND carry
    the researcher's sign-off — the declaration is a publication
    artifact, so both halves are L2 requirements.
    """
    bL1 = fbAtLeastLevel1(dictWorkflow, filesRepo)
    bGithub = fbWorkflowFullySyncedWithGithub(
        dictWorkflow, filesRepo,
    )
    bZenodo = fbWorkflowFullySyncedWithZenodo(
        dictWorkflow, filesRepo,
    )
    bArxiv = fbWorkflowFullySyncedWithArxiv(
        dictWorkflow, filesRepo,
    )
    bDecl = fbWorkflowAiDeclarationAttested(dictWorkflow)
    return {
        "bAtLeastLevel1": bL1,
        "bGithubFullySynced": bGithub,
        "bZenodoFullySynced": bZenodo,
        "bArxivFullySynced": bArxiv,
        "bAiDeclarationAttested": bDecl,
        "bAtLeastLevel2":
            bL1 and bGithub and bZenodo and bArxiv and bDecl,
    }


# ------------------------------------------------------------------------
# L2 per-step blocker surface (Stage 3 of the AICS-ladder plan).
#
# ``flistLevel2Blockers`` mirrors ``flistLevel1Blockers`` but for the
# Publication gate. It does NOT change the boolean ``_fbComputeLevel2``;
# its purpose is *visibility* — the dashboard's banner glyphs and per-step
# rows want per-step granularity for "your A09 output diverged from the
# Zenodo deposit" without re-running the boolean gate.
#
# Per-step criteria emitted here:
#   - ``not-in-github-mirror`` — any output file of the step appears in
#     the cached GitHub-sync ``listDiverged``.
#   - ``not-in-zenodo-deposit`` — same, against the Zenodo cache.
#
# Workflow-scope criteria (``iStepIndex=-1``, ``sScope="workflow"``):
#   - ``github-verify-stale`` / ``zenodo-verify-stale`` — cached
#     ``sLastVerified`` older than ``F_MAX_STALE_HOURS``. When firing,
#     suppresses the per-step ``not-in-*`` row for that endpoint
#     (single root cause; the per-step rows would be misleading because
#     the cache itself is untrustworthy).
#   - ``missing-ai-declaration-step`` — workflow has no
#     ``sType: ai-declaration`` step. Reuses
#     ``fbWorkflowHasAiDeclarationStep``.
# ------------------------------------------------------------------------


_S_WORKFLOW_SCOPE_LABEL = "(workflow)"


def flistLevel2Blockers(dictWorkflow, filesRepo):
    """Return per-step + workflow-scope L2 blockers, unified schema.

    The list is sorted by ``iStepIndex`` (workflow-scope entries with
    ``iStepIndex=-1`` sort to the front). Returns ``[]`` when the
    workflow has no project repo. Stage 4 adds the Overleaf
    ``figure-not-frozen`` per-step criterion (suppressed when the
    workflow has no Overleaf binding — data-only workflows pass) and
    the workflow-scope arXiv criteria (``arxiv-mismatch`` /
    ``arxiv-version-stale``, suppressed when no arXiv submission is
    recorded — the arXiv claim is opt-in).
    """
    filesRepo = ffilesEnsureRepoFiles(filesRepo)
    tCacheKey = (
        "L2",
        _fsWorkflowBlockerFingerprint(dictWorkflow),
        _fsRepoFingerprint(filesRepo),
        _fsSyncStatusFingerprint(filesRepo),
    )
    listCached = _flistBlockerCacheLookup(tCacheKey)
    if listCached is not None:
        return listCached
    listResult = _flistComputeLevel2Blockers(dictWorkflow, filesRepo)
    _fnBlockerCacheStore(tCacheKey, listResult)
    return listResult


def _flistComputeLevel2Blockers(dictWorkflow, filesRepo):
    """Uncached L2-blocker evaluation — the body of the original gate."""
    if not fbWorkflowHasProjectRepo(filesRepo):
        return []
    listBlockers = []
    listBlockers.extend(_flistGithubLevel2Blockers(
        dictWorkflow, filesRepo,
    ))
    listBlockers.extend(_flistZenodoLevel2Blockers(
        dictWorkflow, filesRepo,
    ))
    listBlockers.extend(_flistAiDeclarationLevel2Blockers(
        dictWorkflow,
    ))
    listBlockers.extend(_flistOverleafLevel2Blockers(
        dictWorkflow, filesRepo,
    ))
    listBlockers.extend(_flistArxivLevel2Blockers(
        dictWorkflow, filesRepo,
    ))
    return sorted(
        listBlockers, key=lambda dictEntry: dictEntry["iStepIndex"],
    )


def _flistGithubLevel2Blockers(dictWorkflow, filesRepo):
    """Return github-related L2 blockers (workflow stale suppresses per-step)."""
    dictStatus = scheduledReverify.fdictReadCachedSyncStatus(
        filesRepo, "github",
    )
    if _fbSyncCacheStale(dictStatus):
        return [_fdictGithubVerifyStaleBlocker()]
    return _flistPerStepSyncBlockers(
        dictWorkflow, dictStatus,
        sCriterion="not-in-github-mirror",
        sRemediationHint=(
            "Outputs differ from GitHub mirror — push to clear blocker"
        ),
    )


def _flistZenodoLevel2Blockers(dictWorkflow, filesRepo):
    """Return zenodo-related L2 blockers (workflow stale suppresses per-step)."""
    dictStatus = scheduledReverify.fdictReadCachedSyncStatus(
        filesRepo, "zenodo",
    )
    if _fbSyncCacheStale(dictStatus):
        return [_fdictZenodoVerifyStaleBlocker()]
    return _flistPerStepSyncBlockers(
        dictWorkflow, dictStatus,
        sCriterion="not-in-zenodo-deposit",
        sRemediationHint=(
            "Outputs differ from Zenodo deposit — archive to clear blocker"
        ),
    )


def _flistAiDeclarationLevel2Blockers(dictWorkflow):
    """Return the ai-declaration L2 blocker, or empty list.

    Two failure modes: no ai-declaration step at all (workflow-scope,
    re-homed to the ghost row), or the step exists but the researcher
    has not attested it (per-step, lands on the declaration step's
    own row — the declaration only has meaning at publication, so the
    sign-off is enforced here rather than at L1).
    """
    if not fbWorkflowHasAiDeclarationStep(dictWorkflow):
        return [{
            "iLevel": 2,
            "iStepIndex": -1,
            "sStepLabel": _S_WORKFLOW_SCOPE_LABEL,
            "sScope": "workflow",
            "sCriterion": "missing-ai-declaration-step",
            "listOffendingFiles": [],
            "listOffendingUpstreamSteps": [],
            "sRemediationHint":
                "Add an AI declaration step to record agent involvement",
        }]
    if fbWorkflowAiDeclarationAttested(dictWorkflow):
        return []
    return [
        {
            "iLevel": 2,
            "iStepIndex": iStepIndex,
            "sStepLabel": _fsLabelForStep(dictWorkflow, iStepIndex),
            "sScope": "step",
            "sCriterion": "ai-declaration-unattested",
            "listOffendingFiles": [],
            "listOffendingUpstreamSteps": [],
            "sRemediationHint":
                "Attest the AI Declaration step — open it and "
                "verify the declaration",
        }
        for iStepIndex, dictStep in enumerate(
            (dictWorkflow or {}).get("listSteps") or [],
        )
        if fbStepIsAiDeclaration(dictStep)
        and not fbStepUserApproved(dictStep)
    ]


def _fbSyncCacheStale(dictStatus):
    """Return True iff the cached verify is too old to trust per-step rows.

    A never-verified cache (``sLastVerified`` is None / absent) counts
    as stale for blocker-surfacing purposes: the dashboard should tell
    the researcher to verify, not silently emit per-step rows from
    empty divergence data.
    """
    return not _fbCachedSyncStatusFresh(dictStatus, F_MAX_STALE_HOURS)


def _flistPerStepSyncBlockers(
    dictWorkflow, dictStatus, sCriterion, sRemediationHint,
):
    """Project the divergence list onto each step's declared outputs."""
    setDiverged = _fsetDivergedPaths(dictStatus)
    if not setDiverged:
        return []
    listSteps = dictWorkflow.get("listSteps", []) or []
    listBlockers = []
    for iStepIndex, dictStep in enumerate(listSteps):
        if not isinstance(dictStep, dict):
            continue
        listOffending = _flistStepDivergedFiles(dictStep, setDiverged)
        if not listOffending:
            continue
        listBlockers.append(_fdictBuildSyncStepBlocker(
            dictWorkflow, iStepIndex,
            listOffending, sCriterion, sRemediationHint,
        ))
    return listBlockers


def _fsetDivergedPaths(dictStatus):
    """Return the set of repo-relative paths in ``listDiverged``."""
    setPaths = set()
    for dictEntry in (dictStatus or {}).get("listDiverged", []) or []:
        if isinstance(dictEntry, dict):
            sPath = dictEntry.get("sPath")
            if isinstance(sPath, str) and sPath:
                setPaths.add(sPath)
    return setPaths


def _flistStepDivergedFiles(dictStep, setDiverged):
    """Return the step's output files that intersect ``setDiverged``."""
    listFiles = _flistStepOutputFiles(dictStep)
    return [sPath for sPath in listFiles if sPath in setDiverged]


def _fdictBuildSyncStepBlocker(
    dictWorkflow, iStepIndex,
    listOffendingFiles, sCriterion, sRemediationHint,
):
    """Build a per-step L2 blocker entry for a sync-divergence criterion."""
    return {
        "iLevel": 2,
        "iStepIndex": iStepIndex,
        "sStepLabel": _fsLabelForStep(dictWorkflow, iStepIndex),
        "sScope": "step",
        "sCriterion": sCriterion,
        "listOffendingFiles": listOffendingFiles,
        "listOffendingUpstreamSteps": [],
        "sRemediationHint": sRemediationHint,
    }


def _fdictGithubVerifyStaleBlocker():
    """Build the workflow-scope ``github-verify-stale`` blocker entry."""
    return {
        "iLevel": 2,
        "iStepIndex": -1,
        "sStepLabel": _S_WORKFLOW_SCOPE_LABEL,
        "sScope": "workflow",
        "sCriterion": "github-verify-stale",
        "listOffendingFiles": [],
        "listOffendingUpstreamSteps": [],
        "sRemediationHint":
            "GitHub sync check is stale — re-verify to refresh status",
    }


def _fdictZenodoVerifyStaleBlocker():
    """Build the workflow-scope ``zenodo-verify-stale`` blocker entry."""
    return {
        "iLevel": 2,
        "iStepIndex": -1,
        "sStepLabel": _S_WORKFLOW_SCOPE_LABEL,
        "sScope": "workflow",
        "sCriterion": "zenodo-verify-stale",
        "listOffendingFiles": [],
        "listOffendingUpstreamSteps": [],
        "sRemediationHint":
            "Zenodo sync check is stale — re-verify to refresh status",
    }


# ----------------------------------------------------------------------
# L2 Overleaf + arXiv blocker surfaces (Stage 4 of the AICS-ladder plan).
#
# The Overleaf helper emits per-step ``figure-not-frozen`` blockers,
# suppressed when the workflow has no Overleaf binding — a data-only
# workflow is L2-publishable without a manuscript. The arXiv helper
# emits workflow-scope ``arxiv-mismatch`` and ``arxiv-version-stale``
# blockers, suppressed when no arXiv submission is recorded — the
# arXiv criteria are an opt-in claim, so an unconfigured connection
# is neutral ("not tracked"), never a gap.
# ----------------------------------------------------------------------


def _flistOverleafLevel2Blockers(dictWorkflow, filesRepo):
    """Return per-step ``figure-not-frozen`` blockers, or empty list."""
    if not fbWorkflowHasOverleafBinding(dictWorkflow):
        return []
    setPushed = _fsetPushedFigurePaths(dictWorkflow, filesRepo)
    listSteps = (dictWorkflow or {}).get("listSteps", []) or []
    listBlockers = []
    for iStepIndex, dictStep in enumerate(listSteps):
        if not isinstance(dictStep, dict):
            continue
        listOffending = _flistStepFiguresNotFrozen(dictStep, setPushed)
        if listOffending:
            listBlockers.append(_fdictFigureNotFrozenBlocker(
                dictWorkflow, iStepIndex, listOffending,
            ))
    return listBlockers


def _fsetPushedFigurePaths(dictWorkflow, filesRepo):
    """Return the set of repo-relative figure paths in the push manifest."""
    from .overleafSync import flistOverleafPushedFiguresAt
    sCommit = _fsOverleafRecordedCommit(dictWorkflow)
    return set(flistOverleafPushedFiguresAt(filesRepo, sCommit))


def _flistStepFiguresNotFrozen(dictStep, setPushed):
    """Return declared plot paths absent from the Overleaf push manifest."""
    listOffending = []
    for sPath in dictStep.get("saPlotFiles", []) or []:
        if isinstance(sPath, str) and sPath and sPath not in setPushed:
            listOffending.append(sPath)
    return listOffending


def _fdictFigureNotFrozenBlocker(
    dictWorkflow, iStepIndex, listOffendingFiles,
):
    """Build one per-step ``figure-not-frozen`` blocker entry."""
    return {
        "iLevel": 2,
        "iStepIndex": iStepIndex,
        "sStepLabel": _fsLabelForStep(dictWorkflow, iStepIndex),
        "sScope": "step",
        "sCriterion": "figure-not-frozen",
        "listOffendingFiles": listOffendingFiles,
        "listOffendingUpstreamSteps": [],
        "sRemediationHint":
            "Plot not pushed to Overleaf at recorded commit — "
            "push manuscript figures",
    }


def _flistArxivLevel2Blockers(dictWorkflow, filesRepo):
    """Return workflow-scope arXiv L2 blockers, or empty list.

    Suppressed entirely when no arXiv submission is recorded — an
    unconfigured connection is neutral, not a gap, so it must not
    paint a workflow-scope warning.
    """
    if not fbWorkflowHasArxivConnection(dictWorkflow):
        return []
    dictArxiv = _fdictArxivConfig(dictWorkflow)
    listBlockers = []
    if not _fbArxivTarballMatchesPushManifest(
        dictWorkflow, filesRepo,
    ):
        listBlockers.append(_fdictArxivMismatchBlocker())
    if not _fbArxivVersionCurrent(dictArxiv):
        listBlockers.append(_fdictArxivVersionStaleBlocker())
    return listBlockers


def _fdictArxivMismatchBlocker():
    """Build the workflow-scope ``arxiv-mismatch`` blocker entry."""
    return {
        "iLevel": 2,
        "iStepIndex": -1,
        "sStepLabel": _S_WORKFLOW_SCOPE_LABEL,
        "sScope": "workflow",
        "sCriterion": "arxiv-mismatch",
        "listOffendingFiles": [],
        "listOffendingUpstreamSteps": [],
        "sRemediationHint":
            "arXiv tarball doesn't match Overleaf push at recorded "
            "commit",
    }


def _fdictArxivVersionStaleBlocker():
    """Build the workflow-scope ``arxiv-version-stale`` blocker entry."""
    return {
        "iLevel": 2,
        "iStepIndex": -1,
        "sStepLabel": _S_WORKFLOW_SCOPE_LABEL,
        "sScope": "workflow",
        "sCriterion": "arxiv-version-stale",
        "listOffendingFiles": [],
        "listOffendingUpstreamSteps": [],
        "sRemediationHint":
            "arXiv has a newer version — update sArxivVersion or "
            "re-submit",
    }


# ----------------------------------------------------------------------
# L3 per-step blockers + workflow-scope blockers (Stage 5).
# ----------------------------------------------------------------------

# Allowlist of common scientific binaries scanned for heuristically in
# step commands. The plan calls these out by name as the realistic
# false-waiver defenders; full static parsing of arbitrary commands is
# explicitly out of scope (see plan section E).
TUPLE_COMMON_SCIENTIFIC_BINARIES = (
    "vplanet", "vconverge", "multiplanet", "bigplanet", "vspace",
)


def flistLevel3Blockers(dictWorkflow, filesRepo):
    """Return per-step + workflow-scope L3 blockers with the unified schema.

    Each entry has ``iLevel=3``, ``iStepIndex`` (-1 for workflow scope),
    ``sStepLabel`` ("(workflow)" for workflow scope), ``sCriterion``,
    ``listOffendingFiles``, ``listOffendingUpstreamSteps``, ``sScope``,
    and ``sRemediationHint``. Returns an empty list when the workflow
    has no project repo so the caller treats missing repo the same as
    L1 does.
    """
    filesRepo = ffilesEnsureRepoFiles(filesRepo)
    tCacheKey = (
        "L3",
        _fsWorkflowBlockerFingerprint(dictWorkflow),
        _fsRepoFingerprint(filesRepo),
        _fsSyncStatusFingerprint(filesRepo),
        _fsBinaryStateFingerprint(dictWorkflow, filesRepo),
    )
    listCached = _flistBlockerCacheLookup(tCacheKey)
    if listCached is not None:
        return listCached
    listResult = _flistComputeLevel3Blockers(dictWorkflow, filesRepo)
    _fnBlockerCacheStore(tCacheKey, listResult)
    return listResult


def _fsBinaryStateFingerprint(dictWorkflow, filesRepo):
    """SHA over each declared binary's live hash + its env.json capture.

    The L3 blocker cache is keyed by workflow content, which does not
    change when a binary is rebuilt. Without this component a
    ``binary-drifted`` transition (or its clearing) would be masked by
    a stale cache — the same class of bug the sync-status fingerprint
    fixes for the remote caches. Reads the snapshot's pre-fetched
    hashes, so on the poll path it costs no extra exec. Returns
    ``"none"`` when the workflow declares no binaries.
    """
    listPaths = flistWorkflowBinaryPaths(dictWorkflow)
    if not listPaths:
        return "none"
    dictCaptured = _fdictCapturedBinaryHashes(filesRepo)
    try:
        dictLive = filesRepo.fdictHashAbsolutePaths(listPaths)
    except Exception:
        dictLive = {}
    listEntries = [
        (sPath, dictLive.get(sPath), dictCaptured.get(sPath))
        for sPath in listPaths
    ]
    sCanonical = json.dumps(listEntries, sort_keys=True, default=str)
    return hashlib.sha256(sCanonical.encode("utf-8")).hexdigest()


def _flistComputeLevel3Blockers(dictWorkflow, filesRepo):
    """Uncached L3-blocker evaluation — the body of the original gate."""
    if not fbWorkflowHasProjectRepo(filesRepo):
        return []
    listBlockers = []
    listBlockers.extend(
        _flistL3WorkflowScopeBlockers(dictWorkflow, filesRepo),
    )
    listBlockers.extend(
        _flistL3PerStepBlockers(dictWorkflow, filesRepo),
    )
    return listBlockers


def _flistL3WorkflowScopeBlockers(dictWorkflow, filesRepo):
    """Return the workflow-scope L3 blocker entries."""
    dictChecks = _fdictL3WorkflowChecks(dictWorkflow, filesRepo)
    listBlockers = []
    for sCriterion, bPassed in dictChecks.items():
        if not bPassed:
            listBlockers.append(
                _fdictBuildL3WorkflowBlocker(sCriterion),
            )
    return listBlockers


def _fdictL3WorkflowChecks(dictWorkflow, filesRepo):
    """Return ``{sCriterion: bPassed}`` for every workflow-scope L3 check."""
    return {
        "dockerfile-not-pinned": fbVerifyDockerfilePinned(filesRepo),
        "dependency-lock-missing": fbVerifyDependencyLock(filesRepo),
        "environment-snapshot-missing": fbVerifyEnvironmentSnapshot(
            filesRepo,
        ),
        "reproduce-script-missing": fbVerifyReproduceScript(
            filesRepo, dictWorkflow,
        ),
        "l3-attestation-stale": fbL3AttestationCurrent(filesRepo),
        "binaries-not-declared-or-waived": fbWorkflowDeclaresBinaries(
            dictWorkflow,
        ),
    }


_DICT_L3_REMEDIATION_HINTS = {
    "dockerfile-not-pinned":
        "Pin every FROM line to '@sha256:...' in the Dockerfile.",
    "dependency-lock-missing":
        "requirements.lock with hash pins is missing or unhashed. "
        + S_LOCK_TOOL_INSTALL_HINT,
    "environment-snapshot-missing":
        "Capture the container image digest into "
        ".vaibify/environment.json.",
    "reproduce-script-missing":
        "Generate reproduce.sh and pin it in MANIFEST.sha256.",
    "l3-attestation-stale":
        "Re-run the L3 verification to refresh the attestation.",
    "binaries-not-declared-or-waived":
        "Open 'Declare standalone binaries' and either waive or list "
        "each binary with sBinaryPath / sPurpose / sExpectedVersion.",
    "missing-from-manifest":
        "Run 'vaibify manifest refresh' so every declared output, "
        "script, and standard appears in MANIFEST.sha256.",
    "script-not-pinned":
        "Step script changed since manifest write — rerun the step "
        "or regenerate the manifest.",
    "nondeterminism-undeclared":
        "Seed every RNG explicitly or extend dictDeterminism to "
        "cover the offending step.",
    "binary-not-declared":
        "Step invokes an external binary missing from "
        "listDeclaredBinaries — open the binary-declaration modal.",
    "binary-not-captured":
        "Declared binary lacks an environment.json entry — click "
        "'Capture version + SHA' next to it.",
    "binary-drifted":
        "The binary on disk no longer matches the hash captured in "
        ".vaibify/environment.json — it was rebuilt or replaced after "
        "the outputs were produced. Re-run the step with the current "
        "binary and re-capture, or restore the published binary.",
}


def _fdictBuildL3WorkflowBlocker(sCriterion):
    """Build one workflow-scope L3 blocker entry."""
    return {
        "iLevel": 3,
        "iStepIndex": -1,
        "sStepLabel": "(workflow)",
        "sScope": "workflow",
        "sCriterion": sCriterion,
        "listOffendingFiles": [],
        "listOffendingUpstreamSteps": [],
        "sRemediationHint": _DICT_L3_REMEDIATION_HINTS.get(
            sCriterion, "",
        ),
    }


def _flistL3PerStepBlockers(dictWorkflow, filesRepo):
    """Return per-step L3 blockers, one dominant criterion per step."""
    listSteps = (dictWorkflow or {}).get("listSteps", []) or []
    dictContext = _fdictL3PerStepContext(
        dictWorkflow, filesRepo,
    )
    listBlockers = []
    for iStepIndex, dictStep in enumerate(listSteps):
        dictEntry = _fdictBuildL3StepBlocker(
            dictWorkflow, iStepIndex, dictStep, dictContext,
        )
        if dictEntry is not None:
            listBlockers.append(dictEntry)
    return listBlockers


def _fdictL3PerStepContext(dictWorkflow, filesRepo):
    """Pre-compute manifest + environment state shared across all steps.

    Every step script is hashed in ONE adapter batch
    (``dictScriptHashesOnDisk``) so the per-step drift check is a pure
    dict lookup rather than a per-file IO call.
    """
    filesRepo = ffilesEnsureRepoFiles(filesRepo)
    dictManifestHashes = _fdictReadManifestPathHashes(filesRepo)
    return {
        "dictManifestPathHashes": dictManifestHashes,
        "setManifestPaths": set(dictManifestHashes.keys()),
        "setNondeterministicSteps": _fsetNondeterministicSteps(
            dictWorkflow,
        ),
        "dictEnvironment": _fdictReadEnvironmentForL3(filesRepo),
        "dictScriptHashesOnDisk": filesRepo.fdictHashFiles(
            _flistAllStepScriptPaths(dictWorkflow),
        ),
        "listDeclaredBinaries": _flistDeclaredBinariesNormalized(
            dictWorkflow,
        ),
        "setDriftedBinaryPaths": _fsetDriftedBinaryPaths(
            dictWorkflow, filesRepo,
        ),
        "bWaiver": bool(
            (dictWorkflow or {}).get("bNoStandaloneBinaries", False),
        ),
    }


def _fsetDriftedBinaryPaths(dictWorkflow, filesRepo):
    """Return declared-binary paths whose live hash != the env snapshot.

    "Reproducible" (L3) means a third party rebuilding gets the same
    bytes, so the binary present must match the hash captured in
    ``environment.json``. This recomputes each declared binary's live
    hash (via the poll snapshot's pre-fetched batch, or a live adapter
    off the poll path) and compares. A binary with no captured hash is
    NOT reported here — ``binary-not-captured`` owns that gap; drift is
    only meaningful against a real captured hash. On a fresh clone this
    doubles as a build-reproducibility test: a rebuilt binary that does
    not match the published hash correctly drifts.
    """
    listPaths = flistWorkflowBinaryPaths(dictWorkflow)
    if not listPaths:
        return set()
    dictCaptured = _fdictCapturedBinaryHashes(filesRepo)
    if not dictCaptured:
        return set()
    try:
        dictLive = filesRepo.fdictHashAbsolutePaths(listPaths)
    except Exception:
        return set()
    setDrifted = set()
    for sPath in listPaths:
        sCaptured = dictCaptured.get(sPath)
        sLive = dictLive.get(sPath)
        if sCaptured and sLive and sLive != sCaptured:
            setDrifted.add(sPath)
    return setDrifted


def _fdictCapturedBinaryHashes(filesRepo):
    """Return ``{sBinaryPath: sSha256}`` recorded in environment.json."""
    from .environmentSnapshot import (
        _flistResolveCapturedBinaries,
        fdictReadEnvironmentJson,
    )
    try:
        dictEnv = fdictReadEnvironmentJson(filesRepo) or {}
    except Exception:
        return {}
    dictResult = {}
    for dictCapture in _flistResolveCapturedBinaries(dictEnv):
        if not isinstance(dictCapture, dict):
            continue
        sPath = dictCapture.get("sBinaryPath") or ""
        sSha = dictCapture.get("sSha256") or ""
        if sPath and sSha:
            dictResult[sPath] = sSha
    return dictResult


def _flistAllStepScriptPaths(dictWorkflow):
    """Return the deduplicated script paths across every workflow step."""
    from .manifestPaths import flistStepScriptRepoPaths
    setPaths = set()
    for dictStep in (dictWorkflow or {}).get("listSteps", []) or []:
        if isinstance(dictStep, dict):
            setPaths.update(flistStepScriptRepoPaths(dictStep))
    return sorted(sPath for sPath in setPaths if sPath)


def _fdictReadManifestPathHashes(filesRepo):
    """Return ``{sPath: sExpected}`` for every manifest entry, or {}."""
    try:
        listEntries = flistParseManifestLines(filesRepo)
    except (FileNotFoundError, OSError, ValueError):
        return {}
    return {d["sPath"]: d["sExpected"] for d in listEntries}


def _fsetReadManifestPaths(filesRepo):
    """Return the set of paths declared in MANIFEST.sha256."""
    return set(_fdictReadManifestPathHashes(filesRepo).keys())


def _fsetNondeterministicSteps(dictWorkflow):
    """Return the set of 0-based step indices flagged for unseeded RNG."""
    setIndices = set()
    listSteps = (dictWorkflow or {}).get("listSteps", []) or []
    for iIndex, dictStep in enumerate(listSteps):
        if not isinstance(dictStep, dict):
            continue
        if dictStep.get("bUnseededRandomnessWarning") is True:
            setIndices.add(iIndex)
    return setIndices


def _fdictReadEnvironmentForL3(filesRepo):
    """Return the parsed environment.json or an empty dict."""
    from .environmentSnapshot import fdictReadEnvironmentJson
    dictEnv = fdictReadEnvironmentJson(filesRepo)
    return dictEnv if isinstance(dictEnv, dict) else {}


def _flistDeclaredBinariesNormalized(dictWorkflow):
    """Return the declared-binaries list filtered to well-formed entries."""
    listRaw = (dictWorkflow or {}).get("listDeclaredBinaries") or []
    if not isinstance(listRaw, list):
        return []
    return [d for d in listRaw if _fbBinaryDeclarationEntryValid(d)]


def _fdictBuildL3StepBlocker(
    dictWorkflow, iStepIndex, dictStep, dictContext,
):
    """Return the dominant L3 blocker entry for one step, or None.

    Priority order: ``missing-from-manifest`` > ``script-not-pinned`` >
    ``nondeterminism-undeclared`` > ``binary-not-declared`` >
    ``binary-not-captured``. The first failing criterion wins so a
    single dashboard glyph has a deterministic source; the entry also
    carries ``listFailingCriteria`` (every failing criterion, in
    priority order) so the level-cell projection can count each unmet
    requirement instead of just the dominant one.
    """
    if not isinstance(dictStep, dict):
        return None
    listFailures = _flistL3StepFailures(
        iStepIndex, dictStep, dictContext,
    )
    if not listFailures:
        return None
    sCriterion, listOffenders = listFailures[0]
    dictEntry = _fdictBuildL3StepEntry(
        dictWorkflow, iStepIndex, sCriterion, listOffenders,
    )
    dictEntry["listFailingCriteria"] = [
        sFailing for sFailing, _ in listFailures
    ]
    return dictEntry


def _flistL3StepFailures(iStepIndex, dictStep, dictContext):
    """Return every failing ``(sCriterion, listOffendingFiles)`` pair.

    Evaluates ALL five criteria — no early return — in priority order,
    so callers see the complete failure set, not just the dominant one.
    """
    listFailures = []
    listMissing = _flistStepPathsMissingFromManifest(dictStep, dictContext)
    if listMissing:
        listFailures.append(("missing-from-manifest", listMissing))
    listDrifted = _flistStepScriptsDriftedFromManifest(
        dictStep, dictContext,
    )
    if listDrifted:
        listFailures.append(("script-not-pinned", listDrifted))
    if iStepIndex in dictContext["setNondeterministicSteps"]:
        listFailures.append(("nondeterminism-undeclared", []))
    listUndeclared = _flistUndeclaredBinaryInvocations(
        dictStep, dictContext,
    )
    if listUndeclared:
        listFailures.append(("binary-not-declared", listUndeclared))
    listUncaptured = _flistDeclaredBinariesNotCaptured(
        dictStep, dictContext,
    )
    if listUncaptured:
        listFailures.append(("binary-not-captured", listUncaptured))
    listDriftedBinaries = _flistStepDriftedBinaries(dictStep, dictContext)
    if listDriftedBinaries:
        listFailures.append(("binary-drifted", listDriftedBinaries))
    return listFailures


def _flistStepDriftedBinaries(dictStep, dictContext):
    """Return the step's depended-on binaries that drifted from the snapshot."""
    setDrifted = dictContext.get("setDriftedBinaryPaths") or set()
    if not setDrifted:
        return []
    listDepended = flistStepDependedBinaryPaths(
        dictStep, dictContext["listDeclaredBinaries"],
    )
    return [sPath for sPath in listDepended if sPath in setDrifted]


def _fdictBuildL3StepEntry(
    dictWorkflow, iStepIndex, sCriterion, listOffenders,
):
    """Build one per-step L3 blocker entry from criterion + offender list."""
    return {
        "iLevel": 3,
        "iStepIndex": iStepIndex,
        "sStepLabel": _fsLabelForStep(dictWorkflow, iStepIndex),
        "sScope": "step",
        "sCriterion": sCriterion,
        "listOffendingFiles": listOffenders,
        "listOffendingUpstreamSteps": [],
        "sRemediationHint": _DICT_L3_REMEDIATION_HINTS.get(
            sCriterion, "",
        ),
    }


def _flistStepDeclaredPaths(dictStep):
    """Return repo-relative outputs + scripts + standards for a step."""
    from .manifestPaths import (
        flistStepDeclarationRepoPaths,
        flistStepScriptRepoPaths,
        flistStepStandardsRepoPaths,
    )
    listPaths = list(_flistStepOutputFiles(dictStep))
    listPaths.extend(flistStepScriptRepoPaths(dictStep))
    listPaths.extend(flistStepStandardsRepoPaths(dictStep))
    listPaths.extend(flistStepDeclarationRepoPaths(dictStep))
    return [sPath for sPath in listPaths if sPath]


def _flistStepPathsMissingFromManifest(dictStep, dictContext):
    """Return step-declared paths absent from MANIFEST.sha256."""
    setManifest = dictContext["setManifestPaths"]
    listMissing = []
    for sPath in _flistStepDeclaredPaths(dictStep):
        if sPath not in setManifest:
            listMissing.append(sPath)
    return listMissing


def _flistStepScriptsDriftedFromManifest(dictStep, dictContext):
    """Return step scripts whose on-disk hash differs from MANIFEST.sha256."""
    from .manifestPaths import flistStepScriptRepoPaths
    dictHashes = dictContext["dictManifestPathHashes"]
    dictOnDisk = dictContext.get("dictScriptHashesOnDisk") or {}
    listDrifted = []
    for sScriptPath in flistStepScriptRepoPaths(dictStep):
        sExpected = dictHashes.get(sScriptPath)
        if sExpected is None:
            continue
        if _fbScriptHashMatches(dictOnDisk, sScriptPath, sExpected):
            continue
        listDrifted.append(sScriptPath)
    return listDrifted


def _fbScriptHashMatches(dictOnDisk, sScriptPath, sExpected):
    """Return True iff the batched on-disk hash equals ``sExpected``.

    Pure dict lookup against the per-context batch; a missing or
    unhashable script (``sSha256`` of ``None``) counts as drifted so
    the blocker stays visible.
    """
    sActual = (dictOnDisk.get(sScriptPath) or {}).get("sSha256")
    return bool(sActual) and sActual == sExpected


def _flistStepCommandStrings(dictStep):
    """Return the concatenated data + plot command strings for a step."""
    listCommands = []
    for sKey in ("saDataCommands", "saPlotCommands"):
        for sCmd in dictStep.get(sKey, []) or []:
            if isinstance(sCmd, str) and sCmd:
                listCommands.append(sCmd)
    return listCommands


def _flistUndeclaredBinaryInvocations(dictStep, dictContext):
    """Return common-scientific-binary names invoked but not declared.

    Matches the basename or absolute path of each entry in
    :data:`TUPLE_COMMON_SCIENTIFIC_BINARIES` against the step's data
    and plot commands via a word-boundary regex. Suppresses any name
    whose absolute path is already in ``listDeclaredBinaries``.
    """
    setDeclaredBasenames = _fsetDeclaredBasenames(
        dictContext["listDeclaredBinaries"],
    )
    listCommands = _flistStepCommandStrings(dictStep)
    if not listCommands:
        return []
    listOffenders = []
    for sBinary in TUPLE_COMMON_SCIENTIFIC_BINARIES:
        if sBinary in setDeclaredBasenames:
            continue
        if _fbCommandsInvokeBinary(listCommands, sBinary):
            listOffenders.append(sBinary)
    return listOffenders


def _fsetDeclaredBasenames(listDeclared):
    """Return the set of basenames recorded in listDeclaredBinaries."""
    setBasenames = set()
    for dictEntry in listDeclared:
        sPath = dictEntry.get("sBinaryPath", "")
        if not sPath:
            continue
        setBasenames.add(Path(sPath).name)
    return setBasenames


def _fbCommandsInvokeBinary(listCommands, sBinary):
    """Return True iff ``sBinary`` appears as a word in any command.

    Match is case-sensitive: POSIX binary names on Linux/macOS are
    case-sensitive, and matching ``VPLANET`` against ``vplanet`` would
    produce false positives on case-preserving file systems.
    """
    import re as _re
    regexBinary = _re.compile(r"\b" + _re.escape(sBinary) + r"\b")
    for sCommand in listCommands:
        if regexBinary.search(sCommand):
            return True
    return False


def _flistDeclaredBinariesNotCaptured(dictStep, dictContext):
    """Return declared binary paths referenced by step but missing from env."""
    from .environmentSnapshot import fbBinaryCaptured
    listDeclared = dictContext["listDeclaredBinaries"]
    if not listDeclared:
        return []
    listCommands = _flistStepCommandStrings(dictStep)
    if not listCommands:
        return []
    dictEnv = dictContext["dictEnvironment"]
    listUncaptured = []
    for dictEntry in listDeclared:
        sPath = dictEntry.get("sBinaryPath") or ""
        if not _fbStepReferencesDeclaredBinary(listCommands, sPath):
            continue
        if not fbBinaryCaptured(dictEnv, sPath):
            listUncaptured.append(sPath)
    return listUncaptured


def _fbStepReferencesDeclaredBinary(listCommands, sBinaryPath):
    """Return True iff a step command mentions the binary (basename or path)."""
    if not sBinaryPath:
        return False
    sBasename = Path(sBinaryPath).name
    for sCommand in listCommands:
        if sBinaryPath in sCommand:
            return True
        if _fbCommandsInvokeBinary([sCommand], sBasename):
            return True
    return False


def flistWorkflowBinaryPaths(dictWorkflow):
    """Return every declared binary's absolute path for a workflow.

    The poll hashes this set in its single exec so the L3 drift check
    can compare the live binary against the environment snapshot. A
    workflow that declares no binaries yields ``[]`` — the poll then
    hashes nothing extra.
    """
    listPaths = []
    for dictEntry in _flistDeclaredBinariesNormalized(dictWorkflow):
        sPath = dictEntry.get("sBinaryPath") or ""
        if sPath:
            listPaths.append(sPath)
    return sorted(set(listPaths))


def flistStepDependedBinaryPaths(dictStep, listDeclaredBinaries):
    """Return the declared-binary paths a single step depends on.

    A step depends on a declared binary when EITHER its command
    strings invoke it (the historical command scan) OR its explicit
    ``saBinaryDependencies`` list names the binary by path or
    basename. The explicit list is the authority for IMPLICIT
    dependencies — e.g. a step whose command runs ``maxlev`` while
    ``maxlev`` invokes ``vplanet`` internally, which no command scan
    can surface. Shared by the L1 warning and the L3 drift criterion
    so both attribute the same binaries to the same steps.
    """
    if not isinstance(dictStep, dict):
        return []
    listCommands = _flistStepCommandStrings(dictStep)
    setDeclared = {
        (dictEntry.get("sBinaryPath") or "")
        for dictEntry in (listDeclaredBinaries or [])
    }
    setExplicit = {
        str(sItem) for sItem in
        (dictStep.get("saBinaryDependencies") or [])
        if isinstance(sItem, str) and sItem
    }
    listResult = []
    for sPath in setDeclared:
        if not sPath:
            continue
        if _fbStepReferencesDeclaredBinary(listCommands, sPath):
            listResult.append(sPath)
        elif sPath in setExplicit or Path(sPath).name in setExplicit:
            listResult.append(sPath)
    return sorted(set(listResult))


# ----------------------------------------------------------------------
# Per-step and workflow-scope level-state projection (independent levels).
#
# These functions are PURE projections of the already-computed blocker
# lists plus the step dicts — they never re-evaluate a gate or touch
# the repo. Each level cell is computed INDEPENDENTLY per level: a
# blocked lower level never propagates upward, so a step may honestly
# report L1 attained, L2 partial, and L3 attained-with-regression at
# the same time.
#
# Wire cell shape (one per step per level, and per workflow per level)::
#
#     {"sState": "not-started" | "none" | "partial"
#                | "attained" | "unknown" | "not-applicable",
#      "iSatisfied": int,   # requirements of that level currently met
#      "iTotal": int,       # requirements of that level applicable
#      "bRegression": bool} # high-water stamp exists but not attained
#
# ``not-started`` — the step has no activity at all (no run stats,
# every present test axis untested, never attested) AND none of its
# declared outputs exist on disk; all three of its cells read
# not-started. ``unassessed`` — the same total absence of recorded
# activity, but at least one declared output (``saOutputDataFiles`` /
# ``saPlotFiles``) exists on disk: material is present, assessment has
# not begun. The split keeps hours of compute performed outside the
# dashboard visible as progress without ever claiming verification —
# unassessed sits below ``none`` on the ladder because it asserts only
# existence, never quality. ``none`` — activity exists but zero
# requirements satisfied. ``partial`` — some but not all. ``attained``
# — all satisfied. ``unknown`` — ONLY for per-step L2 when the
# github/zenodo verify cache is stale: the per-step sync truth is
# unavailable and must never render as attained from a stale cache.
# ``not-applicable`` — ONLY for per-step L3 when no criterion has a
# domain on the step (no declared paths, scripts, binaries, or
# randomness flag): nothing to reproduce must never render as a
# vacuous attainment.
#
# NOTE the workflow header row is NOT an aggregate of the step rows.
# Its cells cover only the requirements that attach to the workflow
# as a whole (L1: project repo present; L2: sync-verify freshness +
# arXiv; L3: the envelope artifacts). The all-steps aggregate is the
# scalar ``fiAICSLevel`` gate rendered by the AICS chip. A workflow
# L1 check above red step rows is therefore a consistent display.
# ----------------------------------------------------------------------


_T_TIMING_BLOCKER_CRITERIA = (
    "upstream-modified", "script-stale", "attestation-stale",
)

_T_STEP_LEVEL3_CRITERIA = (
    "missing-from-manifest", "script-not-pinned",
    "nondeterminism-undeclared", "binary-not-declared",
    "binary-not-captured", "binary-drifted",
)

_T_WORKFLOW_LEVEL2_BASE_CRITERIA = (
    "github-verify-stale", "zenodo-verify-stale",
)

_T_WORKFLOW_LEVEL2_ARXIV_CRITERIA = (
    "arxiv-mismatch", "arxiv-version-stale",
)

_T_WORKFLOW_LEVEL3_CRITERIA = (
    "dockerfile-not-pinned", "dependency-lock-missing",
    "environment-snapshot-missing", "reproduce-script-missing",
    "l3-attestation-stale", "binaries-not-declared-or-waived",
)


def fdictComputeStepLevelStates(
    dictWorkflow, listLevel1Blockers,
    listLevel2Blockers, listLevel3Blockers,
    dictMaxMtimeByStep=None,
):
    """Return ``{iStepIndex: {"s1": dictCell, "s2": ..., "s3": ...}}``.

    Each cell carries the independent-level wire shape documented in
    the section header above. Per-step requirement sets:

    * L1 — each PRESENT test axis green (``passed`` /
      ``passed-from-marker`` / ``unnecessary``), user attestation
      (``sUser`` is ``passed``), and timing clean (no
      upstream-modified / script-stale / attestation-stale signal).
    * L2 — github mirror match, zenodo deposit match, and (when the
      workflow has an Overleaf binding and the step declares plots)
      figure frozen. A stale verify cache makes the cell ``unknown``.
    * L3 — the APPLICABLE per-step criteria from
      :data:`_T_STEP_LEVEL3_CRITERIA`, one requirement each; a step
      to which none applies reads ``not-applicable``.

    ``bRegression`` reads the step's ``dictLevelHighWater`` stamps:
    True when the level was attained before but is not attained now.
    A workflow with no project repo zeroes L2/L3 satisfaction — sync
    and manifest truth cannot exist without a repo.

    ``dictMaxMtimeByStep`` is the poll's ``{sStepIndex: sMaxMtime}``
    over each step's declared outputs — an entry exists only when at
    least one declared output is on disk. It discriminates
    ``unassessed`` (material present) from ``not-started`` (nothing
    yet) for steps with no recorded activity; callers without a poll
    snapshot may omit it, collapsing the split to ``not-started``.
    """
    dictContext = _fdictStepProjectionContext(
        dictWorkflow, listLevel1Blockers,
        listLevel2Blockers, listLevel3Blockers,
    )
    dictContext["setStepsWithOutputsOnDisk"] = {
        int(sIndex) for sIndex in (dictMaxMtimeByStep or {})
        if str(sIndex).isdigit()
    }
    dictResult = {}
    listSteps = (dictWorkflow or {}).get("listSteps", []) or []
    for iStepIndex, dictStep in enumerate(listSteps):
        dictResult[iStepIndex] = _fdictOneStepLevelCells(
            iStepIndex, dictStep, dictContext,
        )
    return dictResult


def _fdictStepProjectionContext(
    dictWorkflow, listLevel1Blockers,
    listLevel2Blockers, listLevel3Blockers,
):
    """Pre-compute the shared lookups the per-step cell builders read."""
    dictContext = _fdictBuildPerLevelCriteriaLookups(
        listLevel1Blockers, listLevel2Blockers, listLevel3Blockers,
    )
    dictContext["bGithubCacheStale"] = _fbAnyWorkflowCriterion(
        listLevel2Blockers, "github-verify-stale",
    )
    dictContext["bZenodoCacheStale"] = _fbAnyWorkflowCriterion(
        listLevel2Blockers, "zenodo-verify-stale",
    )
    dictContext["bOverleafBound"] = fbWorkflowHasOverleafBinding(
        dictWorkflow,
    )
    dictContext["bHasRepo"] = fbWorkflowHasProjectRepo(
        (dictWorkflow or {}).get("sProjectRepoPath") or "",
    )
    dictContext["listDeclaredBinaries"] = (
        _flistDeclaredBinariesNormalized(dictWorkflow)
    )
    return dictContext


def _fdictBuildPerLevelCriteriaLookups(
    listLevel1Blockers, listLevel2Blockers, listLevel3Blockers,
):
    """Index each level's per-step blocker criteria by step index."""
    return {
        "dictLevel1CriteriaByStep": _fdictCriteriaByStep(
            listLevel1Blockers,
        ),
        "dictLevel2CriteriaByStep": _fdictCriteriaByStep(
            listLevel2Blockers,
        ),
        "dictLevel3FailingByStep": _fdictLevel3FailingCriteriaByStep(
            listLevel3Blockers,
        ),
    }


def _fdictLevel3FailingCriteriaByStep(listLevel3Blockers):
    """Index each step's FULL failing-criteria set from its L3 entry.

    Reads ``listFailingCriteria`` (every failing criterion, not just
    the dominant glyph source); falls back to the dominant
    ``sCriterion`` for entries minted before that field existed, e.g.
    a warm blocker cache.
    """
    dictResult = {}
    for dictEntry in listLevel3Blockers or []:
        iStepIndex = dictEntry.get("iStepIndex", -1)
        if not (isinstance(iStepIndex, int) and iStepIndex >= 0):
            continue
        listFailing = dictEntry.get("listFailingCriteria") or [
            dictEntry.get("sCriterion"),
        ]
        dictResult.setdefault(iStepIndex, set()).update(
            sCriterion for sCriterion in listFailing if sCriterion
        )
    return dictResult


def _fdictCriteriaByStep(listBlockers):
    """Return ``{iStepIndex: set(sCriterion)}`` for per-step entries.

    Workflow-scope entries (``iStepIndex`` of -1, or absent, treated
    the same defensively) are excluded.
    """
    dictResult = {}
    for dictEntry in listBlockers or []:
        iStepIndex = dictEntry.get("iStepIndex", -1)
        if isinstance(iStepIndex, int) and iStepIndex >= 0:
            dictResult.setdefault(iStepIndex, set()).add(
                dictEntry.get("sCriterion"),
            )
    return dictResult


def _fbAnyWorkflowCriterion(listBlockers, sCriterion):
    """Return True iff any blocker entry carries ``sCriterion``."""
    for dictEntry in listBlockers or []:
        if dictEntry.get("sCriterion") == sCriterion:
            return True
    return False


def _fdictOneStepLevelCells(iStepIndex, dictStep, dictContext):
    """Return the three INDEPENDENT level cells for one step."""
    sInactivityState = _fsStepInactivityState(
        iStepIndex, dictStep, dictContext,
    )
    dictHighWater = _fdictGetStepLevelHighWater(dictStep)
    dictCountsByLevel = _fdictCountStepLevelRequirements(
        iStepIndex, dictStep, dictContext,
    )
    dictResult = {}
    for sLevel in ("1", "2", "3"):
        iSatisfied, iTotal, bUnknown = dictCountsByLevel[sLevel]
        dictResult["s" + sLevel] = _fdictBuildLevelCell(
            iSatisfied, iTotal, sInactivityState, bUnknown,
            sLevel in dictHighWater,
        )
    return dictResult


def _fsStepInactivityState(iStepIndex, dictStep, dictContext):
    """Return the step's inactivity override, or None when active.

    ``not-started`` — no recorded activity and no declared output on
    disk. ``unassessed`` — no recorded activity but material present:
    the step's declared outputs exist, so work was performed outside
    the dashboard (or before state tracking); assessment has not
    begun. Any recorded activity returns None and the count-derived
    states take over.
    """
    if not _fbStepHasNoActivity(dictStep):
        return None
    setOnDisk = dictContext.get("setStepsWithOutputsOnDisk") or set()
    if iStepIndex in setOnDisk:
        return "unassessed"
    return "not-started"


def _fdictGetStepLevelHighWater(dictStep):
    """Return the step's first-attainment stamps; corrupt steps have none."""
    if isinstance(dictStep, dict):
        return dictStep.get("dictLevelHighWater") or {}
    return {}


def _fdictCountStepLevelRequirements(iStepIndex, dictStep, dictContext):
    """Return ``{sLevel: (iSatisfied, iTotal, bUnknown)}`` for one step."""
    iSatisfiedOne, iTotalOne = _ftStepLevel1Counts(
        dictStep,
        dictContext["dictLevel1CriteriaByStep"].get(iStepIndex, set()),
    )
    iSatisfiedTwo, iTotalTwo, bUnknownTwo = _ftStepLevel2Counts(
        dictStep,
        dictContext["dictLevel2CriteriaByStep"].get(iStepIndex, set()),
        dictContext,
    )
    iSatisfiedThree, iTotalThree = _ftStepLevel3Counts(
        dictStep,
        dictContext["dictLevel3FailingByStep"].get(iStepIndex, set()),
        dictContext,
    )
    return {
        "1": (iSatisfiedOne, iTotalOne, False),
        "2": (iSatisfiedTwo, iTotalTwo, bUnknownTwo),
        "3": (iSatisfiedThree, iTotalThree, False),
    }


def _fbStepHasNoActivity(dictStep):
    """Return True iff the step has never run, tested, or been attested.

    The not-started discriminator: no run stats, every present test
    axis still ``untested``, ``sUser`` never moved off ``untested``,
    and no attestation timestamp. A corrupt (non-dict) step entry has
    no readable activity and reads as not-started.
    """
    if not isinstance(dictStep, dict):
        return True
    if dictStep.get("dictRunStats"):
        return False
    dictV = dictStep.get("dictVerification") or {}
    if not isinstance(dictV, dict):
        dictV = {}
    if dictV.get("sLastUserUpdate"):
        return False
    if dictV.get("sUser") not in (None, "untested"):
        return False
    for sAxisKey in _T_TEST_VERIF_KEYS:
        if sAxisKey in dictV and dictV[sAxisKey] != "untested":
            return False
    return True


def _ftCountGreenAxes(dictStep):
    """Return ``(iGreen, iPresent)`` over the step's present test axes."""
    dictV = {}
    if isinstance(dictStep, dict):
        dictV = dictStep.get("dictVerification") or {}
    if not isinstance(dictV, dict):
        dictV = {}
    iGreen, iPresent = 0, 0
    for sAxisKey in _T_TEST_VERIF_KEYS:
        if sAxisKey not in dictV:
            continue
        iPresent += 1
        if dictV[sAxisKey] in _T_GREEN_VERIF_VALUES:
            iGreen += 1
    return (iGreen, iPresent)


def _ftStepLevel1Counts(dictStep, setCriteria):
    """Return ``(iSatisfied, iTotal)`` over the step's L1 requirements.

    Requirements: one per PRESENT test axis, plus user attestation,
    plus timing cleanliness, plus an explicit input-data declaration
    (files listed in ``saInputDataFiles`` or the ``bNoInputData``
    flag) — so ``iTotal`` is axis count + 3. The declaration
    requirement is counted directly from the step, not from
    ``setCriteria``, so the dominant-blocker masking (which now
    ranks ``input-data-undeclared`` above the timing criteria)
    cannot hide it. An ai-declaration step has NO L1 requirements
    (``(0, 0)`` renders not-applicable): the declaration is a
    publication artifact, so its sign-off is a Level 2 requirement.
    """
    if fbStepIsAiDeclaration(dictStep):
        return (0, 0)
    iGreen, iPresent = _ftCountGreenAxes(dictStep)
    iSatisfied = iGreen
    if fbStepUserApproved(dictStep):
        iSatisfied += 1
    if _fbStepTimingRequirementMet(dictStep, setCriteria):
        iSatisfied += 1
    if not _fbStepInputDataUndeclared(dictStep):
        iSatisfied += 1
    return (iSatisfied, iPresent + 3)


def _fbStepTimingRequirementMet(dictStep, setCriteria):
    """Return True iff no timestamp-out-of-order signal hits the step.

    Combines the step-local timing flags (``bUpstreamModified``,
    ``listModifiedFiles``, attestation gone ``stale``) with the
    blocker-only criteria (``script-stale`` needs the poll's script
    status; the dominant-blocker masking cannot hide it because
    every masking criterion is itself a timing criterion).
    """
    if not fbStepTimingClean(dictStep):
        return False
    if set(setCriteria) & set(_T_TIMING_BLOCKER_CRITERIA):
        return False
    return not _fbAttestationStaleOnStep(dictStep)


def _fbAttestationStaleOnStep(dictStep):
    """Return True iff the researcher attested but outputs changed since."""
    if not isinstance(dictStep, dict):
        return False
    dictV = dictStep.get("dictVerification") or {}
    if not isinstance(dictV, dict):
        return False
    return (
        dictV.get("sUser") == "stale"
        and dictV.get("sLastUserUpdate") is not None
    )


def _ftStepLevel2Counts(dictStep, setCriteria, dictContext):
    """Return ``(iSatisfied, iTotal, bUnknown)`` for one step's L2 cell.

    Applicable criteria: github mirror match, zenodo deposit match,
    and figure frozen when the workflow has an Overleaf binding and
    the step declares plot files. A stale verify cache makes the
    matching criterion unknowable: it still counts in ``iTotal`` but
    never in ``iSatisfied``, and the cell state is ``unknown``. A
    missing project repo zeroes satisfaction — there is no sync truth
    to satisfy.
    """
    if not dictContext["bHasRepo"]:
        return (0, 2, False)
    bGithubStale = dictContext["bGithubCacheStale"]
    bZenodoStale = dictContext["bZenodoCacheStale"]
    iSatisfied = _fiCountSyncCriteriaSatisfied(
        setCriteria, bGithubStale, bZenodoStale,
    )
    iTotal = 2
    if _fbFigureFreezeApplicable(dictStep, dictContext):
        iTotal += 1
        if "figure-not-frozen" not in setCriteria:
            iSatisfied += 1
    if fbStepIsAiDeclaration(dictStep):
        # The declaration's researcher sign-off is a Level 2
        # requirement (its L1 cell reads not-applicable).
        iTotal += 1
        if "ai-declaration-unattested" not in setCriteria:
            iSatisfied += 1
    return (iSatisfied, iTotal, bGithubStale or bZenodoStale)


def _fiCountSyncCriteriaSatisfied(setCriteria, bGithubStale, bZenodoStale):
    """Count the github/zenodo criteria that are known to be satisfied."""
    iSatisfied = 0
    if not bGithubStale and "not-in-github-mirror" not in setCriteria:
        iSatisfied += 1
    if not bZenodoStale and "not-in-zenodo-deposit" not in setCriteria:
        iSatisfied += 1
    return iSatisfied


def _fbFigureFreezeApplicable(dictStep, dictContext):
    """Return True iff ``figure-not-frozen`` applies to this step."""
    if not dictContext["bOverleafBound"]:
        return False
    if not isinstance(dictStep, dict):
        return False
    for sPath in dictStep.get("saPlotFiles", []) or []:
        if isinstance(sPath, str) and sPath:
            return True
    return False


def _ftStepLevel3Counts(dictStep, setFailing, dictContext):
    """Return ``(iSatisfied, iTotal)`` over the APPLICABLE L3 criteria.

    ``iTotal`` counts only the criteria whose domain is non-empty on
    this step (unioned with the blocker-reported failures,
    defensively), so a step failing every applicable criterion reads
    zero satisfied — never a flattering near-complete count — and a
    step with nothing to reproduce reads ``(0, 0)``, which the cell
    builder renders as ``not-applicable`` rather than a vacuous
    attainment. A missing project repo zeroes satisfaction over the
    full criteria tuple.
    """
    if not dictContext["bHasRepo"]:
        return (0, len(_T_STEP_LEVEL3_CRITERIA))
    setApplicable = _fsetStepApplicableLevel3Criteria(
        dictStep, dictContext["listDeclaredBinaries"],
    )
    setApplicable |= set(setFailing) & set(_T_STEP_LEVEL3_CRITERIA)
    iTotal = len(setApplicable)
    return (iTotal - len(setApplicable & set(setFailing)), iTotal)


def _fsetStepApplicableLevel3Criteria(dictStep, listDeclaredBinaries):
    """Return the L3 criteria with a non-empty domain on this step.

    A criterion applies only when the step owns something it can fail
    on: declared paths for the manifest, scripts for pinning, an
    unseeded-randomness flag for determinism, and binary invocations
    for declaration + capture + drift. Interactive or attestation-only
    steps typically return an empty set. ``binary-drifted`` applies to
    any step that depends on a declared binary — including an IMPLICIT
    dependency named only in ``saBinaryDependencies`` (e.g.
    maxlev->vplanet), which the command scan cannot see.
    """
    from .manifestPaths import flistStepScriptRepoPaths
    if not isinstance(dictStep, dict):
        return set()
    setApplicable = set()
    if _flistStepDeclaredPaths(dictStep):
        setApplicable.add("missing-from-manifest")
    if flistStepScriptRepoPaths(dictStep):
        setApplicable.add("script-not-pinned")
    if dictStep.get("bUnseededRandomnessWarning") is True:
        setApplicable.add("nondeterminism-undeclared")
    listCommands = _flistStepCommandStrings(dictStep)
    if _fbStepInvokesAnyKnownBinary(listCommands, listDeclaredBinaries):
        setApplicable.add("binary-not-declared")
    if _fbStepReferencesAnyDeclaredBinary(
        listCommands, listDeclaredBinaries,
    ):
        setApplicable.add("binary-not-captured")
    if flistStepDependedBinaryPaths(dictStep, listDeclaredBinaries):
        setApplicable.add("binary-drifted")
    return setApplicable


def _fbStepInvokesAnyKnownBinary(listCommands, listDeclaredBinaries):
    """Return True iff a command invokes any recognizable binary."""
    if not listCommands:
        return False
    setNames = set(TUPLE_COMMON_SCIENTIFIC_BINARIES)
    setNames |= _fsetDeclaredBasenames(listDeclaredBinaries)
    return any(
        _fbCommandsInvokeBinary(listCommands, sName)
        for sName in setNames
    )


def _fbStepReferencesAnyDeclaredBinary(listCommands, listDeclaredBinaries):
    """Return True iff a command references any declared binary."""
    if not listCommands:
        return False
    return any(
        _fbStepReferencesDeclaredBinary(
            listCommands, dictEntry.get("sBinaryPath") or "",
        )
        for dictEntry in listDeclaredBinaries
    )


def _fdictBuildLevelCell(
    iSatisfied, iTotal, sInactivityState, bUnknown, bStamped,
):
    """Return one wire cell with state, counts, and regression flag.

    State precedence: the inactivity override (``not-started`` /
    ``unassessed``) > ``not-applicable`` > ``unknown`` > the
    count-derived states. ``bRegression`` is True when the level holds
    a high-water stamp (the add-only ratchet recorded a first
    attainment) but the current state is not ``attained``; a
    ``not-applicable`` cell never regresses — there is no requirement
    to fall behind on (a stray stamp from the era when such cells read
    as vacuously attained stays inert).
    """
    sState = _fsLevelCellState(
        iSatisfied, iTotal, sInactivityState, bUnknown,
    )
    return {
        "sState": sState,
        "iSatisfied": int(iSatisfied),
        "iTotal": int(iTotal),
        "bRegression": bool(
            bStamped and sState not in ("attained", "not-applicable")
        ),
    }


def _fsLevelCellState(iSatisfied, iTotal, sInactivityState, bUnknown):
    """Map counts plus the override flags onto the seven-state wire.

    ``sInactivityState`` (``not-started`` / ``unassessed`` / None) is
    the no-recorded-activity override and wins outright — a step that
    was never assessed must not render a count-derived judgment.
    ``iTotal`` of 0 means no requirement applies at this level for
    this scope: per-step L3 on a step with nothing to reproduce, and
    per-step L1 on an ai-declaration step (whose sign-off is a Level
    2 requirement). It renders as ``not-applicable`` so an empty
    requirement set never reads as a vacuous attainment.
    """
    if sInactivityState:
        return sInactivityState
    if iTotal == 0:
        return "not-applicable"
    if bUnknown:
        return "unknown"
    if iTotal > 0 and iSatisfied >= iTotal:
        return "attained"
    if iSatisfied > 0:
        return "partial"
    return "none"


def fiStepAICSLevel(dictStepStates):
    """Return the highest contiguous attained level (0-3) for one step.

    Contiguity matters: an attained L3 above a partial L2 is level 1
    — a gap in the ladder caps the climb at the rung below it, even
    though the cells themselves are computed independently. A
    ``not-applicable`` rung counts as satisfied: a step with no
    requirements at a level cannot be blocked by it.
    """
    iLevel = 0
    for iCandidate in (1, 2, 3):
        dictCell = (dictStepStates or {}).get(f"s{iCandidate}")
        if not isinstance(dictCell, dict):
            break
        if dictCell.get("sState") not in ("attained", "not-applicable"):
            break
        iLevel = iCandidate
    return iLevel


def fiLowestNonAttainedLevel(dictStepStates):
    """Return the lowest level (1-3) whose cell is not attained, or 4.

    4 means every level is attained — there is no warning anchor.
    """
    return fiStepAICSLevel(dictStepStates) + 1


_S_BINARY_STALE_WARNING_HINT = (
    "A binary this step depends on was modified after the step's "
    "outputs were produced — re-run the step to confirm the results "
    "still hold"
)


def fdictBinaryStaleByStep(
    dictWorkflow, dictBinaryMtimes, dictMaxMtimeByStep,
):
    """Return ``{iStepIndex: bool}`` — a depended binary is newer than
    the step's own outputs.

    A NON-gating Level 1 signal feeding the regression-column warning.
    ``dictBinaryMtimes`` is the poll's absolute-path→mtime map (declared
    binaries live outside the repo, so their keys are absolute);
    ``dictMaxMtimeByStep`` is the per-step newest OUTPUT mtime
    (string-keyed by step index). Mtime only — the authoritative hash
    comparison is the L3 ``binary-drifted`` criterion.
    """
    listDeclared = _flistDeclaredBinariesNormalized(dictWorkflow)
    dictMtimes = dictBinaryMtimes or {}
    dictMaxByStep = dictMaxMtimeByStep or {}
    dictResult = {}
    listSteps = (dictWorkflow or {}).get("listSteps", []) or []
    for iStepIndex, dictStep in enumerate(listSteps):
        dictResult[iStepIndex] = _fbStepBinaryNewerThanOutputs(
            dictStep, listDeclared, dictMtimes,
            dictMaxByStep.get(str(iStepIndex)),
        )
    return dictResult


def _fbStepBinaryNewerThanOutputs(
    dictStep, listDeclared, dictBinaryMtimes, sOutputMtime,
):
    """Return True iff a depended binary's mtime exceeds the step's
    newest output mtime.

    No outputs yet (``sOutputMtime`` is None) → not stale: there is
    nothing produced to be out of date against. A binary absent from
    the mtime map (never built, or the poll could not stat it) is
    skipped rather than treated as stale.
    """
    if not isinstance(dictStep, dict) or sOutputMtime is None:
        return False
    try:
        iOutputMtime = int(sOutputMtime)
    except (TypeError, ValueError):
        return False
    for sPath in flistStepDependedBinaryPaths(dictStep, listDeclared):
        sBinMtime = dictBinaryMtimes.get(sPath)
        if sBinMtime is None:
            continue
        try:
            if int(sBinMtime) > iOutputMtime:
                return True
        except (TypeError, ValueError):
            continue
    return False


def fdictComputeStepLevelWarnings(
    dictWorkflow, dictStepStates, listLevel1Blockers,
    dictBinaryStaleByStep=None,
):
    """Return ``{iStepIndex: dictWarning}`` for the regression column.

    Per-step wire shape::

        {"iLowestNonAttainedLevel": 1-4,
         "iWarningLevel": int or None,
         "sWarningSeverity": "red" | "orange" | None,
         "sWarningHint": str}

    A gate-level warning fires when, AT the lowest non-attained level,
    the cell carries ``bRegression`` or — level 1 only — a
    timestamp-out-of-order blocker (upstream-modified / script-stale /
    attestation-stale) applies. A regression strictly above the lowest
    non-attained level emits no warning: the researcher's next action
    lives at the lower rung. Severity is ``red`` when the cause
    includes failed tests at that level, ``orange`` for pure
    staleness or regression.

    ``dictBinaryStaleByStep`` (``{iStepIndex: bool}``) adds a NON-gating
    orange Level 1 warning for a step whose depended binary is newer
    than its outputs — surfaced only when no gate-level warning already
    speaks for the step. It never drops the L1 gate (mtime cannot prove
    drift; the L3 ``binary-drifted`` hash check is the authority).
    """
    dictCriteriaByStep = _fdictCriteriaByStep(listLevel1Blockers)
    dictBinaryStale = dictBinaryStaleByStep or {}
    dictResult = {}
    listSteps = (dictWorkflow or {}).get("listSteps", []) or []
    for iStepIndex, dictStep in enumerate(listSteps):
        dictResult[iStepIndex] = _fdictOneStepWarning(
            dictStep,
            (dictStepStates or {}).get(iStepIndex) or {},
            dictCriteriaByStep.get(iStepIndex, set()),
            bool(dictBinaryStale.get(iStepIndex)),
        )
    return dictResult


def _fdictOneStepWarning(
    dictStep, dictStates, setLevel1Criteria, bBinaryStale=False,
):
    """Return one step's consolidated warning dict (or the no-warning shape).

    The gate-level warning (regression / timing at the lowest
    non-attained level) takes precedence. When the gate levels are
    clean, a depended binary newer than the step's outputs raises a
    NON-gating orange Level 1 warning. It is suppressed once every
    level is attained (``iLowest > 3``): an attained L3 means the hash
    already matched, so a newer mtime would be a false alarm.
    """
    iLowest = fiLowestNonAttainedLevel(dictStates)
    dictGateWarning = _fdictGateLevelWarning(
        dictStep, dictStates, setLevel1Criteria, iLowest,
    )
    if dictGateWarning is not None:
        return dictGateWarning
    if bBinaryStale and iLowest <= 3:
        return {
            "iLowestNonAttainedLevel": iLowest,
            "iWarningLevel": 1,
            "sWarningSeverity": "orange",
            "sWarningHint": _S_BINARY_STALE_WARNING_HINT,
        }
    return _fdictNoWarning(iLowest)


def _fdictGateLevelWarning(dictStep, dictStates, setLevel1Criteria, iLowest):
    """Return the regression/timing warning at the lowest non-attained
    level, or None when the gate levels are clean."""
    if iLowest > 3:
        return None
    dictCell = dictStates.get(f"s{iLowest}") or {}
    bTimingBlocker = iLowest == 1 and bool(
        set(setLevel1Criteria) & set(_T_TIMING_BLOCKER_CRITERIA),
    )
    if not (dictCell.get("bRegression") or bTimingBlocker):
        return None
    bFailedTests = iLowest == 1 and _fbStepHasFailedAxis(dictStep)
    sSeverity = "red" if bFailedTests else "orange"
    return {
        "iLowestNonAttainedLevel": iLowest,
        "iWarningLevel": iLowest,
        "sWarningSeverity": sSeverity,
        "sWarningHint": _fsWarningHint(
            iLowest, sSeverity, bTimingBlocker,
        ),
    }


def _fdictNoWarning(iLowestNonAttainedLevel):
    """Return the warning dict for a step with nothing to flag."""
    return {
        "iLowestNonAttainedLevel": iLowestNonAttainedLevel,
        "iWarningLevel": None,
        "sWarningSeverity": None,
        "sWarningHint": "",
    }


def _fbStepHasFailedAxis(dictStep):
    """Return True iff any present test axis on the step reads ``failed``."""
    if not isinstance(dictStep, dict):
        return False
    dictV = dictStep.get("dictVerification") or {}
    if not isinstance(dictV, dict):
        return False
    return any(
        dictV.get(sAxisKey) == "failed"
        for sAxisKey in _T_TEST_VERIF_KEYS
    )


def _fsWarningHint(iWarningLevel, sSeverity, bTimingBlocker):
    """Return the prose remediation hint for one step warning."""
    if sSeverity == "red":
        return (
            "Tests failed at Level 1 — re-run the failing tests, "
            "then verify"
        )
    if bTimingBlocker:
        return (
            "Outputs are out of timestamp order — re-run the step "
            "to clear it"
        )
    return (
        f"Level {iWarningLevel} was previously attained and has "
        "regressed"
    )


def fdictComputeWorkflowScopeLevelStates(
    dictWorkflow, listLevel2Blockers, listLevel3Blockers,
):
    """Return the header-row ``{"s1","s2","s3"}`` workflow-scope cells.

    Same independent-cell wire shape as the per-step projection, over
    the workflow-scope requirement sets: L1 — project repo present
    (one requirement); L2 — github/zenodo verify freshness plus the
    two arXiv criteria when an arXiv submission is recorded, EXCLUDING
    ``missing-ai-declaration-step`` (re-homed to the ghost
    AI-declaration step row); L3 — the six workflow-scope checks in
    :data:`_T_WORKFLOW_LEVEL3_CRITERIA`. Workflow cells never report
    ``not-started`` or ``unknown``: at this scope a stale verify cache
    is itself the unsatisfied requirement, not missing information.
    ``bRegression`` reads ``dictWorkflowLevelHighWater``. A missing
    repo zeroes satisfaction at every level — the blocker lists are
    empty in that case and must not be mistaken for attainment.
    """
    bHasRepo = fbWorkflowHasProjectRepo(
        (dictWorkflow or {}).get("sProjectRepoPath") or "",
    )
    dictCountsByLevel = {
        "1": (1 if bHasRepo else 0, 1),
        "2": _ftWorkflowLevel2Counts(
            dictWorkflow, listLevel2Blockers, bHasRepo,
        ),
        "3": _ftWorkflowLevel3Counts(listLevel3Blockers, bHasRepo),
    }
    return _fdictBuildWorkflowScopeCells(dictWorkflow, dictCountsByLevel)


def _fdictBuildWorkflowScopeCells(dictWorkflow, dictCountsByLevel):
    """Assemble the three workflow-scope cells from their counts."""
    dictHighWater = (
        (dictWorkflow or {}).get("dictWorkflowLevelHighWater") or {}
    )
    dictResult = {}
    for sLevel in ("1", "2", "3"):
        iSatisfied, iTotal = dictCountsByLevel[sLevel]
        dictResult["s" + sLevel] = _fdictBuildLevelCell(
            iSatisfied, iTotal, None, False, sLevel in dictHighWater,
        )
    return dictResult


def _ftWorkflowLevel2Counts(dictWorkflow, listLevel2Blockers, bHasRepo):
    """Return ``(iSatisfied, iTotal)`` for the workflow-scope L2 cell."""
    tApplicable = _T_WORKFLOW_LEVEL2_BASE_CRITERIA
    if fbWorkflowHasArxivConnection(dictWorkflow):
        tApplicable = tApplicable + _T_WORKFLOW_LEVEL2_ARXIV_CRITERIA
    return _ftCountWorkflowCriteria(
        listLevel2Blockers, tApplicable, bHasRepo,
    )


def _ftWorkflowLevel3Counts(listLevel3Blockers, bHasRepo):
    """Return ``(iSatisfied, iTotal)`` for the workflow-scope L3 cell."""
    return _ftCountWorkflowCriteria(
        listLevel3Blockers, _T_WORKFLOW_LEVEL3_CRITERIA, bHasRepo,
    )


def _ftCountWorkflowCriteria(listBlockers, tApplicable, bHasRepo):
    """Count the applicable workflow-scope criteria not currently blocked."""
    iTotal = len(tApplicable)
    if not bHasRepo:
        return (0, iTotal)
    setBlocked = (
        _fsetWorkflowScopeCriteria(listBlockers) & set(tApplicable)
    )
    return (iTotal - len(setBlocked), iTotal)


def _fsetWorkflowScopeCriteria(listBlockers):
    """Return distinct criteria on workflow-scope blocker entries."""
    setCriteria = set()
    for dictEntry in listBlockers or []:
        iStepIndex = dictEntry.get("iStepIndex", -1)
        if isinstance(iStepIndex, int) and iStepIndex >= 0:
            continue
        setCriteria.add(dictEntry.get("sCriterion"))
    return setCriteria
