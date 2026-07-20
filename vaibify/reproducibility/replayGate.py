"""Replay-axis verdicts: AI-provenance state for a workflow.

The Replay axis measures the provenance of the development process —
which AI models did the work, under what standing instructions, and
whether the development dialogue is preserved — as distinct from the
AICS ladder, which measures the state of the artifact. Axis states in
ascending order: ``untracked`` (nothing declared), ``declared`` (every
model used is declared), ``recorded`` (the Prompt Record is enabled and
its first capture reviewed), ``supervised`` (the attribution watchdog
is enabled). A project is "Replayable" at ``recorded`` or better.

All verdicts read ``dictWorkflow["dictAiProvenance"]`` defensively;
like ``dictDeterminism``, the block is validated at its write routes,
never centrally. Opt-in sub-features follow the arXiv rule: an
unconfigured opt-in is trivially passing, never a gap.
"""

__all__ = [
    "S_AI_PROVENANCE_KEY",
    "S_DECLARED_MODELS_KEY",
    "S_PERSONAL_LAYER_KEY",
    "S_PROMPT_RECORD_KEY",
    "S_SUPERVISION_KEY",
    "SET_PERSONAL_LAYER_STATUSES",
    "flistDescribeModelDeclarationGaps",
    "fbModelDeclarationValid",
    "fbWorkflowDeclaresAiModels",
    "fbWorkflowDeclaresPersonalLayer",
    "fbPromptRecordCurrent",
    "fbSupervisionClean",
    "fsReplayAxisState",
]


S_AI_PROVENANCE_KEY = "dictAiProvenance"
S_DECLARED_MODELS_KEY = "listDeclaredModels"
S_PERSONAL_LAYER_KEY = "dictPersonalLayer"
S_PROMPT_RECORD_KEY = "dictPromptRecord"
S_SUPERVISION_KEY = "dictSupervision"

SET_PERSONAL_LAYER_STATUSES = frozenset({
    "none", "declared-private", "included",
})

_LIST_REQUIRED_MODEL_FIELDS = [
    "sVendor",
    "sModelId",
    "sUseStartDate",
    "sUseEndDate",
]
_LIST_OPEN_WEIGHTS_FIELDS = ["sWeightsSource", "sWeightsRevisionHash"]


def _fdictAiProvenance(dictWorkflow):
    """Return the workflow's AI-provenance block, empty dict if absent."""
    dictProvenance = (dictWorkflow or {}).get(S_AI_PROVENANCE_KEY)
    return dictProvenance if isinstance(dictProvenance, dict) else {}


def flistDescribeModelDeclarationGaps(dictModel):
    """Return the missing-field names that keep one declaration invalid.

    A closed-weights declaration requires vendor, model identifier, and
    the date range of use. An open-weights declaration additionally
    requires the weights source and revision hash — the strictly
    stronger branch. An empty list means the declaration is valid.
    """
    if not isinstance(dictModel, dict):
        return list(_LIST_REQUIRED_MODEL_FIELDS)
    listRequired = list(_LIST_REQUIRED_MODEL_FIELDS)
    if dictModel.get("bOpenWeights") is True:
        listRequired.extend(_LIST_OPEN_WEIGHTS_FIELDS)
    return [
        sField
        for sField in listRequired
        if not str(dictModel.get(sField) or "").strip()
    ]


def fbModelDeclarationValid(dictModel):
    """Return True iff one model declaration has every required field."""
    return not flistDescribeModelDeclarationGaps(dictModel)


def fbWorkflowDeclaresAiModels(dictWorkflow):
    """Return True iff at least one model is declared and all are valid.

    Undeclared is the only failing state of the criterion: closed- and
    open-weights declarations both pass, differing only in how much
    they declare.
    """
    listModels = _fdictAiProvenance(dictWorkflow).get(S_DECLARED_MODELS_KEY)
    if not isinstance(listModels, list) or not listModels:
        return False
    return all(fbModelDeclarationValid(dictModel) for dictModel in listModels)


def fbWorkflowDeclaresPersonalLayer(dictWorkflow):
    """Return True iff the personal-layer question has been answered.

    The personal layer is the researcher's private host-side agent
    configuration (global instruction file, personal skills, memory,
    hooks). Any of the three statuses — ``none``, ``declared-private``,
    ``included`` — satisfies the criterion: the gate requires the
    question answered, never the content revealed. ``declared-private``
    with zero hash commitments is a fully valid answer; unanswered is
    the only failing state.
    """
    dictLayer = _fdictAiProvenance(dictWorkflow).get(S_PERSONAL_LAYER_KEY)
    if not isinstance(dictLayer, dict):
        return False
    return dictLayer.get("sStatus") in SET_PERSONAL_LAYER_STATUSES


def fbPromptRecordCurrent(dictWorkflow):
    """Return True unless an enabled Prompt Record is missing its review.

    Opt-in semantics: not configured or not enabled is trivially True
    (never blocks anything). Once enabled, the record is current only
    after the researcher has reviewed and approved the first sanitized
    capture.
    """
    dictRecord = _fdictAiProvenance(dictWorkflow).get(S_PROMPT_RECORD_KEY) or {}
    if dictRecord.get("bEnabled") is not True:
        return True
    return dictRecord.get("bFirstCaptureReviewed") is True


def fbSupervisionClean(dictWorkflow):
    """Return True unless supervision is enabled with outstanding flags.

    Opt-in semantics mirror :func:`fbPromptRecordCurrent`. The flag
    count is threaded in by the caller via the workflow dict's
    ``dictSupervision`` summary; this gate never reads files itself.
    """
    dictSupervision = _fdictAiProvenance(dictWorkflow).get(S_SUPERVISION_KEY) or {}
    if dictSupervision.get("bEnabled") is not True:
        return True
    return int(dictSupervision.get("iUnattributedFlagCount") or 0) == 0


def fsReplayAxisState(dictWorkflow):
    """Return the Replay-axis state: untracked/declared/recorded/supervised.

    Each state requires all lower states: recording without a model
    declaration is still ``untracked`` because the transcript of an
    undeclared agent is not honest provenance. ``declared`` requires
    the whole declaration, models AND the personal prompt layer
    (ruling 2026-07-19): a declared model with an unaccounted
    instruction stack is not a complete answer to "what governed the
    AI's contributions."
    """
    if not fbWorkflowDeclaresAiModels(dictWorkflow):
        return "untracked"
    if not fbWorkflowDeclaresPersonalLayer(dictWorkflow):
        return "untracked"
    dictProvenance = _fdictAiProvenance(dictWorkflow)
    dictRecord = dictProvenance.get(S_PROMPT_RECORD_KEY) or {}
    bRecorded = (
        dictRecord.get("bEnabled") is True
        and dictRecord.get("bFirstCaptureReviewed") is True
    )
    if not bRecorded:
        return "declared"
    dictSupervision = dictProvenance.get(S_SUPERVISION_KEY) or {}
    if dictSupervision.get("bEnabled") is True:
        return "supervised"
    return "recorded"
