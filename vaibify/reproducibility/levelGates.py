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

import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from . import scheduledReverify
from .aiDeclarationStep import fbStepIsAiDeclaration
from .dependencyPinning import flistVerifyRequirementsLock
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
from .reproduceScriptGenerator import S_REPRODUCE_SCRIPT_FILENAME
from .stepPredicates import (
    fbStepTestsPassing,
    fbStepTimingClean,
    fbStepUserApproved,
)

__all__ = [
    "F_MAX_STALE_HOURS",
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
    "fbWorkflowFullySyncedWithGithub",
    "fbWorkflowFullySyncedWithZenodo",
    "fbWorkflowHasAiDeclarationStep",
    "fbWorkflowHasProjectRepo",
    "fdictL3ReadinessGaps",
    "fdictLevel2Gaps",
    "fiAICSLevel",
    "flistLevel1Blockers",
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


def fiAICSLevel(dictWorkflow, sProjectRepoPath):
    """Return the integer AICS level (0..3) for a workflow.

    Short-circuits up the ladder so each gate runs at most once. Wraps
    the L1/L2/L3 chain in ``fnLevelComputationContext`` so the inner
    recursive calls (L2 -> L1, L3 -> L2 -> L1) hit a memo instead of
    re-iterating every step.
    """
    with fnLevelComputationContext():
        if not fbAtLeastLevel1(dictWorkflow, sProjectRepoPath):
            return 0
        if not fbAtLeastLevel2(dictWorkflow, sProjectRepoPath):
            return 1
        if not fbAtLeastLevel3(dictWorkflow, sProjectRepoPath):
            return 2
        return 3


def fbAtLeastLevel1(dictWorkflow, sProjectRepoPath):
    """Return True iff the workflow meets the L1 Self-Consistent gate.

    L1 requires four criteria, all enforced per-step: workflow lives
    in a git project repo, every step is user-approved, every step is
    timing-clean (no upstream-modified flag, no outstanding modified
    files), and every step's defined test categories are green.
    """
    dictMemo = _fdictActiveLevelMemo()
    if dictMemo is not None and "bL1" in dictMemo:
        return dictMemo["bL1"]
    bResult = _fbComputeLevel1(dictWorkflow, sProjectRepoPath)
    if dictMemo is not None:
        dictMemo["bL1"] = bResult
    return bResult


def _fbComputeLevel1(dictWorkflow, sProjectRepoPath):
    """Uncached L1 evaluation — the body of the original gate.

    Delegates to ``flistLevel1Blockers`` so the boolean gate and the
    per-step diagnostic surface share one implementation: no blockers
    means L1 is clean. Preserves the historical contract — an empty
    workflow or a missing project repo still returns False.
    """
    if not fbWorkflowHasProjectRepo(sProjectRepoPath):
        return False
    listSteps = dictWorkflow.get("listSteps", []) or []
    if not listSteps:
        return False
    listBlockers = flistLevel1Blockers(
        dictWorkflow, {}, sProjectRepoPath,
    )
    return len(listBlockers) == 0


def flistLevel1Blockers(dictWorkflow, dictNewModTimes, sProjectRepoPath):
    """Return per-step L1 blockers with per-file granularity.

    Each entry has the shape::

        {"iStepIndex": int, "sStepLabel": str, "sCriterion": str,
         "listOffendingFiles": [repo-relative paths],
         "listOffendingUpstreamSteps": [0-based step indices]}

    ``sCriterion`` is one of ``"user-not-approved"``,
    ``"upstream-modified"``, ``"axis-not-green"``, or
    ``"attestation-stale"``. ``attestation-stale`` fires when the
    researcher *did* attest (``sLastUserUpdate`` is present) but the
    outputs were rewritten after the attestation, flipping
    ``sUser`` to ``"stale"``; ``user-not-approved`` fires when the
    step was never attested. When both ``bUpstreamModified`` and an
    axis-untested condition fire on the same step only
    ``upstream-modified`` is emitted (root cause; the axis-untested
    condition is its downstream effect after the L1 invalidation
    cascade). The list is sorted by ``iStepIndex`` so rendering order
    is deterministic. Returns ``[]`` for an L1-clean workflow or one
    with no project repo.
    """
    if not fbWorkflowHasProjectRepo(sProjectRepoPath):
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
        )
        if dictBlocker is not None:
            listBlockers.append(dictBlocker)
    return sorted(listBlockers, key=lambda dictEntry: dictEntry["iStepIndex"])


def _fdictBuildStepBlocker(
    dictWorkflow, iStepIndex, dictStep,
    dictNewModTimes, dictUpstreamByStep,
):
    """Return the single dominant blocker dict for a step, or None.

    Priority: ``upstream-modified`` > ``axis-not-green`` >
    ``attestation-stale`` > ``user-not-approved``. The first
    applicable criterion wins so a step never emits two blockers; the
    dashboard's banner glyph therefore has a deterministic single
    source. Corrupt step entries (``None``, non-dict, missing
    ``dictVerification``) cannot satisfy any criterion and are
    surfaced as ``user-not-approved`` so the L1 gate matches its
    historical defensive contract.
    """
    if not isinstance(dictStep, dict):
        return _fdictUserNotApprovedBlocker(dictWorkflow, iStepIndex)
    if not fbStepTimingClean(dictStep):
        return _fdictUpstreamModifiedBlocker(
            dictWorkflow, iStepIndex, dictStep,
            dictNewModTimes, dictUpstreamByStep,
        )
    if not fbStepTestsPassing(dictStep):
        return _fdictAxisNotGreenBlocker(
            dictWorkflow, iStepIndex, dictStep,
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


def _fdictUpstreamModifiedBlocker(
    dictWorkflow, iStepIndex, dictStep,
    dictNewModTimes, dictUpstreamByStep,
):
    """Build the ``upstream-modified`` blocker entry for one step."""
    listUpstreamIndices = sorted(_flistOffendingUpstream(
        iStepIndex, dictNewModTimes, dictUpstreamByStep,
    ))
    listOffendingFiles = _flistStepOutputFiles(dictStep)
    return {
        "iStepIndex": iStepIndex,
        "sStepLabel": _fsLabelForStep(dictWorkflow, iStepIndex),
        "sCriterion": "upstream-modified",
        "listOffendingFiles": listOffendingFiles,
        "listOffendingUpstreamSteps": listUpstreamIndices,
    }


def _fdictAxisNotGreenBlocker(dictWorkflow, iStepIndex, dictStep):
    """Build the ``axis-not-green`` blocker entry for one step."""
    return {
        "iStepIndex": iStepIndex,
        "sStepLabel": _fsLabelForStep(dictWorkflow, iStepIndex),
        "sCriterion": "axis-not-green",
        "listOffendingFiles": _flistStepOutputFiles(dictStep),
        "listOffendingUpstreamSteps": [],
    }


def _fdictUserNotApprovedBlocker(dictWorkflow, iStepIndex):
    """Build the ``user-not-approved`` blocker entry for one step."""
    return {
        "iStepIndex": iStepIndex,
        "sStepLabel": _fsLabelForStep(dictWorkflow, iStepIndex),
        "sCriterion": "user-not-approved",
        "listOffendingFiles": [],
        "listOffendingUpstreamSteps": [],
    }


def _fdictAttestationStaleBlocker(dictWorkflow, iStepIndex, dictStep):
    """Build the ``attestation-stale`` blocker entry for one step.

    Fires when the researcher previously attested (``sLastUserUpdate``
    present) but the outputs changed since, flipping ``sUser`` to
    ``stale``. ``listOffendingFiles`` projects the step's declared
    outputs so the dashboard can mark them red with the
    *re-verify-or-re-run* remediation.
    """
    return {
        "iStepIndex": iStepIndex,
        "sStepLabel": _fsLabelForStep(dictWorkflow, iStepIndex),
        "sCriterion": "attestation-stale",
        "listOffendingFiles": _flistStepOutputFiles(dictStep),
        "listOffendingUpstreamSteps": [],
    }


def _flistStepOutputFiles(dictStep):
    """Return repo-relative data + plot file paths declared on a step."""
    listFiles = []
    for sKey in ("saDataFiles", "saPlotFiles"):
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


def fbStepIsAtLeastLevel1(dictStep):
    """Return True iff a single step meets the L1 per-step criteria.

    Thin composition of the three orthogonal predicates so callers
    can ask "is this step contributing to L1" without re-implementing
    the rule (e.g. file-status badges, auto-archive transition).
    """
    if not isinstance(dictStep, dict):
        return False
    if not fbStepUserApproved(dictStep):
        return False
    if not fbStepTimingClean(dictStep):
        return False
    if not fbStepTestsPassing(dictStep):
        return False
    return True


def fbWorkflowHasProjectRepo(sProjectRepoPath):
    """Return True iff the workflow has a non-empty project repo path.

    L1's "under git control" criterion is the existence of the repo
    discovery itself — the load-time auto-detector only populates
    ``sProjectRepoPath`` when the workflow.json lives inside a git
    work tree. Tracked-and-matched semantics belong to L2.
    """
    return bool(sProjectRepoPath)


def fbAtLeastLevel2(dictWorkflow, sProjectRepoPath):
    """Return True iff the workflow meets the L2 Publication gate.

    L2 builds on L1 with three additional criteria: every canonical
    file's hash matches the GitHub mirror at a recently-verified
    SHA, every Zenodo-published file's hash matches at a known DOI on
    the workflow's configured endpoint, and the workflow contains an
    AI Declaration step (which L1 already requires to be user-attested).
    """
    dictMemo = _fdictActiveLevelMemo()
    if dictMemo is not None and "bL2" in dictMemo:
        return dictMemo["bL2"]
    bResult = _fbComputeLevel2(dictWorkflow, sProjectRepoPath)
    if dictMemo is not None:
        dictMemo["bL2"] = bResult
    return bResult


def _fbComputeLevel2(dictWorkflow, sProjectRepoPath):
    """Uncached L2 evaluation — the body of the original gate."""
    if not fbAtLeastLevel1(dictWorkflow, sProjectRepoPath):
        return False
    if not fbWorkflowFullySyncedWithGithub(
        dictWorkflow, sProjectRepoPath,
    ):
        return False
    if not fbWorkflowFullySyncedWithZenodo(
        dictWorkflow, sProjectRepoPath,
    ):
        return False
    if not fbWorkflowHasAiDeclarationStep(dictWorkflow):
        return False
    return True


def fbAtLeastLevel3(dictWorkflow, sProjectRepoPath):
    """Return True iff the workflow meets the L3 Reproducibility gate.

    L3 requires L2 plus a green readiness check (six orthogonal
    verifiers) plus a non-stale, ``passed`` L3 attestation on file.
    The expensive rebuild that produces the attestation is the
    only L3 criterion that touches a multi-hour operation; the
    other five are cheap and re-evaluated on every level recompute.
    """
    if not fbAtLeastLevel2(dictWorkflow, sProjectRepoPath):
        return False
    if not fbL3ReadinessOK(dictWorkflow, sProjectRepoPath):
        return False
    if not fbL3AttestationCurrent(sProjectRepoPath):
        return False
    return True


def fbL3ReadinessOK(dictWorkflow, sProjectRepoPath):
    """Return True iff every cheap L3 readiness verifier passes.

    The composition is intentionally short: each verifier owns its
    own gap surface so the dashboard can render per-criterion fix
    links. ``fbL3AttestationCurrent`` is *not* part of readiness —
    readiness answers "is the envelope coherent enough to bother
    attempting a rebuild?", attestation answers "has that rebuild
    actually been done and verified?".
    """
    if not sProjectRepoPath:
        return False
    return (
        fbVerifyManifestComplete(sProjectRepoPath, dictWorkflow)
        and fbVerifyDependencyLock(sProjectRepoPath)
        and fbVerifyEnvironmentSnapshot(sProjectRepoPath)
        and fbVerifyDockerfilePinned(sProjectRepoPath)
        and fbVerifyReproduceScript(sProjectRepoPath, dictWorkflow)
        and fbVerifyDeterminismDeclared(sProjectRepoPath, dictWorkflow)
    )


def fbVerifyManifestComplete(sProjectRepoPath, dictWorkflow):
    """Return True iff every workflow-declared path is in the manifest.

    A missing manifest is treated as failure (no envelope at all),
    a populated manifest with zero declared-but-missing entries is
    a pass. The check delegates to ``manifestWriter`` so the
    completeness rule stays in one place.
    """
    try:
        listMissing = flistDeclaredButMissingFromManifest(
            sProjectRepoPath, dictWorkflow,
        )
    except FileNotFoundError:
        return False
    except (OSError, ValueError):
        return False
    return not listMissing


def fbVerifyDependencyLock(sProjectRepoPath):
    """Return True iff ``requirements.lock`` exists and every entry is hashed."""
    listIssues = flistVerifyRequirementsLock(sProjectRepoPath)
    return not listIssues


def fbVerifyEnvironmentSnapshot(sProjectRepoPath):
    """Return True iff ``.vaibify/environment.json`` records a sha256 digest."""
    return fbEnvironmentDigestPinned(sProjectRepoPath)


def fbVerifyDockerfilePinned(sProjectRepoPath):
    """Return True iff the Dockerfile passes the L3 pin lint."""
    listIssues = flistLintDockerfile(sProjectRepoPath)
    return not listIssues


def fbVerifyReproduceScript(sProjectRepoPath, dictWorkflow):
    """Return True iff ``reproduce.sh`` exists and is in MANIFEST.sha256.

    Presence-on-disk alone is insufficient: an unhashed copy could
    be tampered with. The script's repo-relative path must appear
    in the parsed manifest entries so a downstream consumer's
    ``sha256sum -c`` would detect drift.
    """
    pathScript = Path(sProjectRepoPath) / S_REPRODUCE_SCRIPT_FILENAME
    if not pathScript.is_file():
        return False
    try:
        listEntries = flistParseManifestLines(sProjectRepoPath)
    except (FileNotFoundError, OSError, ValueError):
        return False
    setPaths = {dictEntry["sPath"] for dictEntry in listEntries}
    return S_REPRODUCE_SCRIPT_FILENAME in setPaths


def fbVerifyDeterminismDeclared(sProjectRepoPath, dictWorkflow):
    """Return True iff no step warns about unseeded RNG and BLAS is declared.

    The check rejects any step carrying ``bUnseededRandomnessWarning``
    in addition to requiring the workflow-level
    ``dictDeterminism`` block (or its waiver). ``sProjectRepoPath`` is
    accepted for symmetry with the other verifiers; the audit is
    workflow-level so the path is only used by future per-script
    extensions.
    """
    del sProjectRepoPath  # noqa: F841 — reserved for future per-script audit
    listIssues = flistAuditWorkflow(dictWorkflow)
    return not listIssues


def _fdictCollectL3ReadinessFlags(dictWorkflow, sProjectRepoPath, bRepo):
    """Return the six per-verifier booleans that gate L3 readiness."""
    return {
        "bManifestComplete": bRepo and fbVerifyManifestComplete(
            sProjectRepoPath, dictWorkflow,
        ),
        "bDependencyLockHashed": bRepo and fbVerifyDependencyLock(
            sProjectRepoPath,
        ),
        "bEnvironmentDigestPinned": bRepo and fbVerifyEnvironmentSnapshot(
            sProjectRepoPath,
        ),
        "bDockerfilePinned": bRepo and fbVerifyDockerfilePinned(
            sProjectRepoPath,
        ),
        "bReproduceScriptPinned": bRepo and fbVerifyReproduceScript(
            sProjectRepoPath, dictWorkflow,
        ),
        "bDeterminismDeclared": bRepo and fbVerifyDeterminismDeclared(
            sProjectRepoPath, dictWorkflow,
        ),
    }


def fdictL3ReadinessGaps(dictWorkflow, sProjectRepoPath):
    """Return per-verifier pass/fail for the L3 readiness card.

    The shape matches what the AICS tab's L3 readiness card binds
    against; missing entries are explicit so the rendering code can
    iterate keys directly. The ``bL3AttestationCurrent`` entry is a
    separate read so the UI can render the "Verify L3 Reproducibility"
    button state independently of the readiness verifiers.
    """
    bRepo = fbWorkflowHasProjectRepo(sProjectRepoPath)
    dictFlags = _fdictCollectL3ReadinessFlags(
        dictWorkflow, sProjectRepoPath, bRepo,
    )
    bAllReadiness = all(dictFlags.values())
    dictResult = {sKey: bool(bValue) for sKey, bValue in dictFlags.items()}
    dictResult["bL3ReadinessOK"] = bool(bAllReadiness)
    dictResult["bL3AttestationCurrent"] = (
        fbL3AttestationCurrent(sProjectRepoPath) if bRepo else False
    )
    dictResult["sManifestDigest"] = (
        fsCurrentManifestDigest(sProjectRepoPath) if bRepo else ""
    )
    return dictResult


def fbWorkflowHasAiDeclarationStep(dictWorkflow):
    """Return True iff the workflow lists at least one ai-declaration step.

    The step's own ``sUser`` attestation is enforced by L1's per-step
    gate, so this predicate only needs to confirm the step kind is
    present somewhere in the list.
    """
    if not isinstance(dictWorkflow, dict):
        return False
    for dictStep in dictWorkflow.get("listSteps", []) or []:
        if fbStepIsAiDeclaration(dictStep):
            return True
    return False


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
    dictWorkflow, sProjectRepoPath,
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
        sProjectRepoPath, "github",
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
    dictWorkflow, sProjectRepoPath,
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
        sProjectRepoPath, "zenodo",
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


def fdictLevel2Gaps(dictWorkflow, sProjectRepoPath):
    """Return per-criterion pass/fail for the L2 readiness card.

    Returned shape::

        {
            "bAtLeastLevel1": bool,
            "bGithubFullySynced": bool,
            "bZenodoFullySynced": bool,
            "bAiDeclarationStepPresent": bool,
            "bAtLeastLevel2": bool,
        }

    The frontend AICS tab consumes this dict directly; each False
    entry maps to a red row with a "fix here" link.
    """
    bL1 = fbAtLeastLevel1(dictWorkflow, sProjectRepoPath)
    bGithub = fbWorkflowFullySyncedWithGithub(
        dictWorkflow, sProjectRepoPath,
    )
    bZenodo = fbWorkflowFullySyncedWithZenodo(
        dictWorkflow, sProjectRepoPath,
    )
    bDecl = fbWorkflowHasAiDeclarationStep(dictWorkflow)
    return {
        "bAtLeastLevel1": bL1,
        "bGithubFullySynced": bGithub,
        "bZenodoFullySynced": bZenodo,
        "bAiDeclarationStepPresent": bDecl,
        "bAtLeastLevel2": bL1 and bGithub and bZenodo and bDecl,
    }
