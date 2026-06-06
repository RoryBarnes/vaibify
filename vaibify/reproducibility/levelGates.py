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
    "fbWorkflowFullySyncedWithGithub",
    "fbWorkflowFullySyncedWithZenodo",
    "fbWorkflowHasAiDeclarationStep",
    "fbWorkflowHasProjectRepo",
    "fdictL3ReadinessGaps",
    "fdictLevel2Gaps",
    "fiAICSLevel",
    "flistLevel1Blockers",
    "flistLevel2Blockers",
    "flistLevel3Blockers",
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


def fiAICSLevel(dictWorkflow, sProjectRepoPath, dictScriptStatus=None):
    """Return the integer AICS level (0..3) for a workflow.

    Short-circuits up the ladder so each gate runs at most once. Wraps
    the L1/L2/L3 chain in ``fnLevelComputationContext`` so the inner
    recursive calls (L2 -> L1, L3 -> L2 -> L1) hit a memo instead of
    re-iterating every step. ``dictScriptStatus`` threads through to
    L1 so callers with mtime info honor the script-stale criterion.
    """
    with fnLevelComputationContext():
        if not fbAtLeastLevel1(
            dictWorkflow, sProjectRepoPath, dictScriptStatus,
        ):
            return 0
        if not fbAtLeastLevel2(dictWorkflow, sProjectRepoPath):
            return 1
        if not fbAtLeastLevel3(dictWorkflow, sProjectRepoPath):
            return 2
        return 3


def fbAtLeastLevel1(dictWorkflow, sProjectRepoPath, dictScriptStatus=None):
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
        dictWorkflow, sProjectRepoPath, dictScriptStatus,
    )
    if dictMemo is not None:
        dictMemo["bL1"] = bResult
    return bResult


def _fbComputeLevel1(dictWorkflow, sProjectRepoPath, dictScriptStatus=None):
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
        dictWorkflow, {}, sProjectRepoPath, dictScriptStatus,
    )
    return len(listBlockers) == 0


def flistLevel1Blockers(
    dictWorkflow, dictNewModTimes, sProjectRepoPath,
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

    ``sCriterion`` is one of ``"user-not-approved"``,
    ``"upstream-modified"``, ``"script-stale"``, ``"axis-not-green"``,
    or ``"attestation-stale"``. ``script-stale`` fires when the step's
    script has been edited after its declared outputs landed; suppressed
    when the outputs' hashes still match ``MANIFEST.sha256`` (fresh
    clones). Priority order is ``upstream-modified`` > ``script-stale``
    > ``axis-not-green`` > ``attestation-stale`` > ``user-not-approved``.
    The list is sorted by ``iStepIndex`` so rendering order is
    deterministic. Returns ``[]`` for an L1-clean workflow or one with
    no project repo.
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
            dictScriptStatus, sProjectRepoPath,
        )
        if dictBlocker is not None:
            listBlockers.append(dictBlocker)
    return sorted(listBlockers, key=lambda dictEntry: dictEntry["iStepIndex"])


def _fdictBuildStepBlocker(
    dictWorkflow, iStepIndex, dictStep,
    dictNewModTimes, dictUpstreamByStep,
    dictScriptStatus=None, sProjectRepoPath="",
):
    """Return the single dominant blocker dict for a step, or None.

    Priority: ``upstream-modified`` > ``script-stale`` >
    ``axis-not-green`` > ``attestation-stale`` > ``user-not-approved``.
    The first applicable criterion wins so a step never emits two
    blockers; the dashboard's banner glyph therefore has a deterministic
    single source. Corrupt step entries (``None``, non-dict, missing
    ``dictVerification``) cannot satisfy any criterion and are surfaced
    as ``user-not-approved`` so the L1 gate matches its historical
    defensive contract.
    """
    if not isinstance(dictStep, dict):
        return _fdictUserNotApprovedBlocker(dictWorkflow, iStepIndex)
    if not fbStepTimingClean(dictStep):
        return _fdictUpstreamModifiedBlocker(
            dictWorkflow, iStepIndex, dictStep,
            dictNewModTimes, dictUpstreamByStep,
        )
    if _fbStepScriptStale(
        iStepIndex, dictStep, dictScriptStatus, sProjectRepoPath,
    ):
        return _fdictScriptStaleBlocker(
            dictWorkflow, iStepIndex, dictStep,
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


def _fdictAxisNotGreenBlocker(dictWorkflow, iStepIndex, dictStep):
    """Build the ``axis-not-green`` blocker entry for one step."""
    return {
        "iLevel": 1,
        "iStepIndex": iStepIndex,
        "sStepLabel": _fsLabelForStep(dictWorkflow, iStepIndex),
        "sScope": "step",
        "sCriterion": "axis-not-green",
        "listOffendingFiles": _flistStepOutputFiles(dictStep),
        "listOffendingUpstreamSteps": [],
        "sRemediationHint":
            "Re-run failing tests then verify",
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
    *re-verify-or-re-run* remediation.
    """
    return {
        "iLevel": 1,
        "iStepIndex": iStepIndex,
        "sStepLabel": _fsLabelForStep(dictWorkflow, iStepIndex),
        "sScope": "step",
        "sCriterion": "attestation-stale",
        "listOffendingFiles": _flistStepOutputFiles(dictStep),
        "listOffendingUpstreamSteps": [],
        "sRemediationHint":
            "Outputs changed since you verified — re-verify or re-run",
    }


def _fbStepScriptStale(
    iStepIndex, dictStep, dictScriptStatus, sProjectRepoPath,
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
        dictStep, sProjectRepoPath,
    )


def _fbStepHashesMatchManifest(dictStep, sProjectRepoPath):
    """Return True iff every declared output's hash matches MANIFEST.sha256.

    Delegates to ``hashStaleness`` for the manifest read and the
    per-output content comparison so the suppression rule has the
    same authority the file-status manager uses. Conservative on every
    error path: missing repo, missing manifest, no declared outputs,
    or any drifted entry returns False so the script-stale criterion
    remains visible.
    """
    if not sProjectRepoPath:
        return False
    listRelPaths = _flistStepOutputsRepoRelative(
        dictStep, sProjectRepoPath,
    )
    if not listRelPaths:
        return False
    from vaibify.gui import hashStaleness
    if not hashStaleness.fbManifestExists(sProjectRepoPath):
        return False
    dictEntries = hashStaleness._fdictReadManifestEntries(sProjectRepoPath)
    if not dictEntries:
        return False
    if _fbAnyOutputMissingFromManifest(listRelPaths, dictEntries):
        return False
    setStale = hashStaleness.fsetStaleOutputsAgainstManifest(
        sProjectRepoPath, listRelPaths, {},
    )
    return len(setStale) == 0


def _fbAnyOutputMissingFromManifest(listRelPaths, dictEntries):
    """Return True iff any declared output is absent from the manifest."""
    for sRelPath in listRelPaths:
        if sRelPath not in dictEntries:
            return True
    return False


def _flistStepOutputsRepoRelative(dictStep, sProjectRepoPath):
    """Return repo-relative output paths declared on a step.

    Resolves each ``saDataFiles``/``saPlotFiles`` entry against the
    step directory the same way ``_fsResolveStepFilePath`` does, then
    strips the repo root so the result lines up with manifest keys.
    Lazily imports the GUI helper so the reproducibility leaf stays
    importable without GUI side effects at module load.
    """
    from vaibify.gui.fileStatusManager import _fsResolveStepFilePath
    from vaibify.gui.pathContract import fsAbsToRepoRelative
    sStepDir = dictStep.get("sDirectory", "") or ""
    listRelative = []
    for sFile in (dictStep.get("saDataFiles", []) or []) + (
        dictStep.get("saPlotFiles", []) or []
    ):
        if not sFile:
            continue
        sAbs = _fsResolveStepFilePath(
            sFile, sStepDir, {"sRepoRoot": sProjectRepoPath},
        )
        listRelative.append(
            fsAbsToRepoRelative(sAbs, sProjectRepoPath),
        )
    return listRelative


def _fdictScriptStaleBlocker(dictWorkflow, iStepIndex, dictStep):
    """Build the ``script-stale`` blocker entry for one step.

    Fires when the step's script mtime is newer than its outputs,
    indicating the researcher edited the producer without re-running.
    ``listOffendingFiles`` projects the step's declared outputs so the
    dashboard can mark them with the *re-run-to-clear* remediation.
    """
    return {
        "iStepIndex": iStepIndex,
        "sStepLabel": _fsLabelForStep(dictWorkflow, iStepIndex),
        "sCriterion": "script-stale",
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
    """Return the per-verifier booleans that gate L3 readiness."""
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
        "bBinariesDeclaredOrWaived": bRepo and fbWorkflowDeclaresBinaries(
            dictWorkflow,
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


def flistLevel2Blockers(dictWorkflow, sProjectRepoPath):
    """Return per-step + workflow-scope L2 blockers, unified schema.

    The list is sorted by ``iStepIndex`` (workflow-scope entries with
    ``iStepIndex=-1`` sort to the front). Returns ``[]`` when the
    workflow has no project repo. This function adds visibility only;
    the boolean L2 gate (``_fbComputeLevel2``) is unchanged.
    """
    if not fbWorkflowHasProjectRepo(sProjectRepoPath):
        return []
    listBlockers = []
    listBlockers.extend(_flistGithubLevel2Blockers(
        dictWorkflow, sProjectRepoPath,
    ))
    listBlockers.extend(_flistZenodoLevel2Blockers(
        dictWorkflow, sProjectRepoPath,
    ))
    listBlockers.extend(_flistAiDeclarationLevel2Blockers(
        dictWorkflow,
    ))
    return sorted(
        listBlockers, key=lambda dictEntry: dictEntry["iStepIndex"],
    )


def _flistGithubLevel2Blockers(dictWorkflow, sProjectRepoPath):
    """Return github-related L2 blockers (workflow stale suppresses per-step)."""
    dictStatus = scheduledReverify.fdictReadCachedSyncStatus(
        sProjectRepoPath, "github",
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


def _flistZenodoLevel2Blockers(dictWorkflow, sProjectRepoPath):
    """Return zenodo-related L2 blockers (workflow stale suppresses per-step)."""
    dictStatus = scheduledReverify.fdictReadCachedSyncStatus(
        sProjectRepoPath, "zenodo",
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
    """Return the workflow-scope ai-declaration blocker, or empty list."""
    if fbWorkflowHasAiDeclarationStep(dictWorkflow):
        return []
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
# L3 per-step blockers + workflow-scope blockers (Stage 5).
# ----------------------------------------------------------------------

# Allowlist of common scientific binaries scanned for heuristically in
# step commands. The plan calls these out by name as the realistic
# false-waiver defenders; full static parsing of arbitrary commands is
# explicitly out of scope (see plan section E).
TUPLE_COMMON_SCIENTIFIC_BINARIES = (
    "vplanet", "vconverge", "multiplanet", "bigplanet", "vspace",
)


def flistLevel3Blockers(dictWorkflow, sProjectRepoPath):
    """Return per-step + workflow-scope L3 blockers with the unified schema.

    Each entry has ``iLevel=3``, ``iStepIndex`` (-1 for workflow scope),
    ``sStepLabel`` ("(workflow)" for workflow scope), ``sCriterion``,
    ``listOffendingFiles``, ``listOffendingUpstreamSteps``, ``sScope``,
    and ``sRemediationHint``. Returns an empty list when the workflow
    has no project repo so the caller treats missing repo the same as
    L1 does.
    """
    if not fbWorkflowHasProjectRepo(sProjectRepoPath):
        return []
    listBlockers = []
    listBlockers.extend(
        _flistL3WorkflowScopeBlockers(dictWorkflow, sProjectRepoPath),
    )
    listBlockers.extend(
        _flistL3PerStepBlockers(dictWorkflow, sProjectRepoPath),
    )
    return listBlockers


def _flistL3WorkflowScopeBlockers(dictWorkflow, sProjectRepoPath):
    """Return the workflow-scope L3 blocker entries."""
    dictChecks = _fdictL3WorkflowChecks(dictWorkflow, sProjectRepoPath)
    listBlockers = []
    for sCriterion, bPassed in dictChecks.items():
        if not bPassed:
            listBlockers.append(
                _fdictBuildL3WorkflowBlocker(sCriterion),
            )
    return listBlockers


def _fdictL3WorkflowChecks(dictWorkflow, sProjectRepoPath):
    """Return ``{sCriterion: bPassed}`` for every workflow-scope L3 check."""
    return {
        "dockerfile-not-pinned": fbVerifyDockerfilePinned(sProjectRepoPath),
        "dependency-lock-missing": fbVerifyDependencyLock(sProjectRepoPath),
        "environment-snapshot-missing": fbVerifyEnvironmentSnapshot(
            sProjectRepoPath,
        ),
        "reproduce-script-missing": fbVerifyReproduceScript(
            sProjectRepoPath, dictWorkflow,
        ),
        "l3-attestation-stale": fbL3AttestationCurrent(sProjectRepoPath),
        "binaries-not-declared-or-waived": fbWorkflowDeclaresBinaries(
            dictWorkflow,
        ),
    }


_DICT_L3_REMEDIATION_HINTS = {
    "dockerfile-not-pinned":
        "Pin every FROM line to '@sha256:...' in the Dockerfile.",
    "dependency-lock-missing":
        "Generate requirements.lock with --require-hashes.",
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


def _flistL3PerStepBlockers(dictWorkflow, sProjectRepoPath):
    """Return per-step L3 blockers, one dominant criterion per step."""
    listSteps = (dictWorkflow or {}).get("listSteps", []) or []
    dictContext = _fdictL3PerStepContext(
        dictWorkflow, sProjectRepoPath,
    )
    listBlockers = []
    for iStepIndex, dictStep in enumerate(listSteps):
        dictEntry = _fdictBuildL3StepBlocker(
            dictWorkflow, iStepIndex, dictStep, dictContext,
        )
        if dictEntry is not None:
            listBlockers.append(dictEntry)
    return listBlockers


def _fdictL3PerStepContext(dictWorkflow, sProjectRepoPath):
    """Pre-compute manifest + environment state shared across all steps."""
    return {
        "dictManifestPathHashes": _fdictReadManifestPathHashes(
            sProjectRepoPath,
        ),
        "setManifestPaths": _fsetReadManifestPaths(sProjectRepoPath),
        "setNondeterministicSteps": _fsetNondeterministicSteps(
            dictWorkflow,
        ),
        "dictEnvironment": _fdictReadEnvironmentForL3(sProjectRepoPath),
        "sProjectRepoPath": sProjectRepoPath,
        "listDeclaredBinaries": _flistDeclaredBinariesNormalized(
            dictWorkflow,
        ),
        "bWaiver": bool(
            (dictWorkflow or {}).get("bNoStandaloneBinaries", False),
        ),
    }


def _fdictReadManifestPathHashes(sProjectRepoPath):
    """Return ``{sPath: sExpected}`` for every manifest entry, or {}."""
    try:
        listEntries = flistParseManifestLines(sProjectRepoPath)
    except (FileNotFoundError, OSError, ValueError):
        return {}
    return {d["sPath"]: d["sExpected"] for d in listEntries}


def _fsetReadManifestPaths(sProjectRepoPath):
    """Return the set of paths declared in MANIFEST.sha256."""
    return set(_fdictReadManifestPathHashes(sProjectRepoPath).keys())


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


def _fdictReadEnvironmentForL3(sProjectRepoPath):
    """Return the parsed environment.json or an empty dict."""
    from .environmentSnapshot import fdictReadEnvironmentJson
    dictEnv = fdictReadEnvironmentJson(sProjectRepoPath)
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
    ``binary-not-captured``. The first applicable criterion wins so a
    single dashboard glyph has a deterministic source.
    """
    if not isinstance(dictStep, dict):
        return None
    sCriterion, listOffenders = _ftL3StepCriterion(
        iStepIndex, dictStep, dictContext,
    )
    if sCriterion is None:
        return None
    return _fdictBuildL3StepEntry(
        dictWorkflow, iStepIndex, sCriterion, listOffenders,
    )


def _ftL3StepCriterion(iStepIndex, dictStep, dictContext):
    """Return ``(sCriterion, listOffendingFiles)`` or ``(None, [])``."""
    listMissing = _flistStepPathsMissingFromManifest(dictStep, dictContext)
    if listMissing:
        return ("missing-from-manifest", listMissing)
    listDrifted = _flistStepScriptsDriftedFromManifest(
        dictStep, dictContext,
    )
    if listDrifted:
        return ("script-not-pinned", listDrifted)
    if iStepIndex in dictContext["setNondeterministicSteps"]:
        return ("nondeterminism-undeclared", [])
    listUndeclared = _flistUndeclaredBinaryInvocations(
        dictStep, dictContext,
    )
    if listUndeclared:
        return ("binary-not-declared", listUndeclared)
    listUncaptured = _flistDeclaredBinariesNotCaptured(
        dictStep, dictContext,
    )
    if listUncaptured:
        return ("binary-not-captured", listUncaptured)
    return (None, [])


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
        flistStepScriptRepoPaths,
        flistStepStandardsRepoPaths,
    )
    listPaths = list(_flistStepOutputFiles(dictStep))
    listPaths.extend(flistStepScriptRepoPaths(dictStep))
    listPaths.extend(flistStepStandardsRepoPaths(dictStep))
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
    from .provenanceTracker import fsComputeFileHash
    dictHashes = dictContext["dictManifestPathHashes"]
    sRepoRoot = dictContext["sProjectRepoPath"]
    listDrifted = []
    for sScriptPath in flistStepScriptRepoPaths(dictStep):
        sExpected = dictHashes.get(sScriptPath)
        if sExpected is None:
            continue
        if _fbScriptHashMatches(
            sRepoRoot, sScriptPath, sExpected, fsComputeFileHash,
        ):
            continue
        listDrifted.append(sScriptPath)
    return listDrifted


def _fbScriptHashMatches(
    sRepoRoot, sScriptPath, sExpected, fnHash,
):
    """Return True iff ``sScriptPath`` on disk hashes to ``sExpected``."""
    pathFile = Path(sRepoRoot) / sScriptPath
    if not pathFile.is_file():
        return False
    try:
        return fnHash(str(pathFile)) == sExpected
    except (OSError, ValueError):
        return False


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
    """Return True iff ``sBinary`` appears as a word in any command."""
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
