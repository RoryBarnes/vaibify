"""The Agent Council campaign domain model and state vocabulary.

Phase 1 of the Agent Council (design/agentCouncil.md section 4). This
module owns what a campaign *is* — the canonical state, verdict, claim,
completion, execution, gate and exit vocabularies (section 4.5), the
bounded settings surface, the durable campaign and participant records,
the single state-transition authority, and crash-restoration validation
— together with the provider connection seam (section 9.8) the engine
drives. It is separated from the protocol engine because the record
shape and its legal states change for data-model reasons, on a different
cadence than the orchestration that walks a campaign through them.

It is pure: no Docker, no routes, no filesystem, no wall clock. The
versioned charter text it stamps into each campaign lives in
``agentCouncilCharter``; nothing here composes a turn or drives a
provider.
"""

import copy

from .agentCouncilCharter import (
    S_CHARTER_TEXT,
    S_CHARTER_VERSION,
    _fsMintIdentifier,
)

__all__ = [
    "CouncilConfigurationError",
    "CouncilProtocolError",
    "CouncilProviderConnection",
    "DICT_DEFAULT_SETTINGS",
    "DICT_EMPTY_PROJECT_IDENTITY",
    "LIST_CAMPAIGN_REQUIRED_KEYS",
    "LIST_EXHAUSTED_ROUND_EXITS",
    "LIST_PROJECT_IDENTITY_KEYS",
    "S_CLAIM_ASSERTED",
    "S_CLAIM_BLOCKED",
    "S_CLAIM_CONFIRMED",
    "S_CLAIM_SOURCE_SUPPORTED",
    "S_COMPLETION_INDETERMINATE",
    "S_COMPLETION_TERMINAL",
    "S_EXECUTION_FULL_SANDBOX",
    "S_EXECUTION_READ_ONLY",
    "S_EXIT_GRANT_RESOLUTION_ROUND",
    "S_EXIT_REJECT_OR_ARCHIVE",
    "S_EXIT_RESOLVE_OR_OVERRIDE",
    "S_GATE_BLOCKING_QUESTION",
    "S_GATE_EXHAUSTED_ROUNDS",
    "S_GATE_QUORUM_SHORTFALL",
    "S_STATE_ARCHIVED",
    "S_STATE_AWAITING_IMPLEMENTATION",
    "S_STATE_DRAFT",
    "S_STATE_FAILED",
    "S_STATE_INTERRUPTED",
    "S_STATE_NEEDS_HUMAN",
    "S_STATE_PLANNING",
    "S_STATE_PLAN_ACCEPTED",
    "S_STATE_PLAN_READY",
    "S_VERDICT_ACCEPT",
    "S_VERDICT_BLOCKING_OBJECTION",
    "S_VERDICT_NEEDS_HUMAN",
    "S_VERDICT_UNDETERMINED",
    "SET_CAMPAIGN_STATES",
    "SET_RECOGNIZED_VETO_VERDICTS",
    "fbCampaignMatchesPrincipal",
    "fdictCreateCampaign",
    "fdictCreateParticipant",
    "fdictRestoreCampaignFromMetadata",
    "fnTransitionCampaignState",
]

# --- Canonical state vocabulary (section 4.5, MVP subset). Review-only
# states (reviewing, changesRequested, reviewPassed, contaminated) are
# documented in the design, not pre-built here.
S_STATE_DRAFT = "draft"
S_STATE_PLANNING = "planning"
S_STATE_NEEDS_HUMAN = "needsHuman"
S_STATE_PLAN_READY = "planReady"
S_STATE_PLAN_ACCEPTED = "planAccepted"
S_STATE_AWAITING_IMPLEMENTATION = "awaitingImplementation"
S_STATE_FAILED = "failed"
S_STATE_INTERRUPTED = "interrupted"
S_STATE_ARCHIVED = "archived"

SET_CAMPAIGN_STATES = {
    S_STATE_DRAFT, S_STATE_PLANNING, S_STATE_NEEDS_HUMAN,
    S_STATE_PLAN_READY, S_STATE_PLAN_ACCEPTED,
    S_STATE_AWAITING_IMPLEMENTATION, S_STATE_FAILED,
    S_STATE_INTERRUPTED, S_STATE_ARCHIVED,
}

S_VERDICT_ACCEPT = "accept"
S_VERDICT_BLOCKING_OBJECTION = "blockingObjection"
S_VERDICT_NEEDS_HUMAN = "needsHuman"
# Engine-assigned only: a frozen required voter whose veto is missing,
# failed, or unrecognizable. Never acceptance, never absence of
# objection (section 5.1).
S_VERDICT_UNDETERMINED = "undetermined"

SET_RECOGNIZED_VETO_VERDICTS = {
    S_VERDICT_ACCEPT, S_VERDICT_BLOCKING_OBJECTION, S_VERDICT_NEEDS_HUMAN,
}

S_CLAIM_CONFIRMED = "confirmed"
S_CLAIM_SOURCE_SUPPORTED = "supportedBySourceInspection"
S_CLAIM_ASSERTED = "asserted"
S_CLAIM_BLOCKED = "blockedForWantOfEvidence"

S_COMPLETION_TERMINAL = "terminal"
S_COMPLETION_INDETERMINATE = "indeterminate"

S_EXECUTION_FULL_SANDBOX = "fullSandbox"
S_EXECUTION_READ_ONLY = "readOnly"

S_GATE_BLOCKING_QUESTION = "blockingQuestion"
S_GATE_EXHAUSTED_ROUNDS = "exhaustedRounds"
S_GATE_QUORUM_SHORTFALL = "quorumShortfall"

S_EXIT_GRANT_RESOLUTION_ROUND = "grantBoundedResolutionRound"
S_EXIT_RESOLVE_OR_OVERRIDE = "resolveOrOverrideThenFinalVeto"
S_EXIT_REJECT_OR_ARCHIVE = "rejectOrArchiveCandidate"
LIST_EXHAUSTED_ROUND_EXITS = [
    S_EXIT_GRANT_RESOLUTION_ROUND,
    S_EXIT_RESOLVE_OR_OVERRIDE,
    S_EXIT_REJECT_OR_ARCHIVE,
]

DICT_DEFAULT_SETTINGS = {
    "bPeerAnonymity": True,
    "sEffortPerParticipant": "standard",
    "sExecutionPermission": S_EXECUTION_FULL_SANDBOX,
    "iMinimumRounds": 1,
    "iMaximumRounds": 3,
    "iMaximumConcurrentTurns": 2,
    "iMaximumOutputBytesPerTurn": 262144,
}

LIST_CAMPAIGN_REQUIRED_KEYS = [
    "sCampaignId", "sState", "sQuestion", "listParticipants",
    "sChairbotParticipantId", "sCharterVersion", "sCharterText",
    "dictSettings", "listRounds", "iGrantedAdditionalRounds",
    "dictCandidatePlan", "dictPendingHumanGate", "listResearcherDecisions",
    "listResearcherResponses", "listStateTransitions", "bStopRequested",
    "iObjectionCounter", "iClaimCounter", "dictProjectIdentity",
]

# The canonical identity triple a campaign is bound to (remediation R2):
# the container name that is the lease principal (the owner map is keyed
# by NAME, never the raw docker id), the validated project-repo path the
# campaign deliberates over (one container can host several repos), and
# the snapshot identity recorded when the immutable context is captured.
LIST_PROJECT_IDENTITY_KEYS = [
    "sResourceName", "sProjectRepoPath", "sSnapshotIdentity",
]

DICT_EMPTY_PROJECT_IDENTITY = {
    "sResourceName": "",
    "sProjectRepoPath": "",
    "sSnapshotIdentity": "",
}


class CouncilProtocolError(Exception):
    """A caller asked the protocol for a transition it does not offer."""


class CouncilConfigurationError(ValueError):
    """A campaign or engine was configured outside the bounded surface."""


def fdictCreateParticipant(sProvider, sRequestedModel, sRole=""):
    """Create one participant record (section 4.4)."""
    if not sProvider or not sRequestedModel:
        raise CouncilConfigurationError(
            "a participant needs a provider and a requested model")
    return {
        "sParticipantId": _fsMintIdentifier("participant"),
        "sProvider": sProvider,
        "sRequestedModel": sRequestedModel,
        "sReportedModel": "",
        "sRole": sRole,
        "bFreshReviewer": False,
        "bFailed": False,
        "sFailureReason": "",
    }


def _fdictValidateSettings(dictRequestedSettings):
    dictSettings = dict(DICT_DEFAULT_SETTINGS)
    for sSettingName, jsonValue in (dictRequestedSettings or {}).items():
        if sSettingName not in DICT_DEFAULT_SETTINGS:
            raise CouncilConfigurationError(
                f"unknown council setting '{sSettingName}' — the settings "
                "surface is deliberately bounded and the consensus rule "
                "is not a setting")
        dictSettings[sSettingName] = jsonValue
    if dictSettings["iMinimumRounds"] < 1:
        raise CouncilConfigurationError("iMinimumRounds must be at least 1")
    if dictSettings["iMaximumRounds"] < dictSettings["iMinimumRounds"]:
        raise CouncilConfigurationError(
            "iMaximumRounds cannot be below iMinimumRounds")
    if dictSettings["iMaximumConcurrentTurns"] < 1:
        raise CouncilConfigurationError(
            "iMaximumConcurrentTurns must be at least 1")
    if dictSettings["sExecutionPermission"] not in (
            S_EXECUTION_FULL_SANDBOX, S_EXECUTION_READ_ONLY):
        raise CouncilConfigurationError("unknown execution permission")
    return dictSettings


def _fdictValidateProjectIdentity(dictProjectIdentity):
    """Validate the identity triple, or default to the unbound triple."""
    if dictProjectIdentity is None:
        return dict(DICT_EMPTY_PROJECT_IDENTITY)
    if not isinstance(dictProjectIdentity, dict):
        raise CouncilConfigurationError(
            "the campaign project identity must be a mapping")
    if sorted(dictProjectIdentity) != sorted(LIST_PROJECT_IDENTITY_KEYS):
        raise CouncilConfigurationError(
            "the campaign project identity must carry exactly "
            f"{LIST_PROJECT_IDENTITY_KEYS}")
    for sIdentityKey in LIST_PROJECT_IDENTITY_KEYS:
        if not isinstance(dictProjectIdentity[sIdentityKey], str):
            raise CouncilConfigurationError(
                f"campaign identity '{sIdentityKey}' must be a string")
    return dict(dictProjectIdentity)


def fbCampaignMatchesPrincipal(dictCampaign, sResourceName,
                               sProjectRepoPath):
    """Report whether a campaign is bound to this principal and repo.

    The cross-project refusal predicate (remediation R2). An unbound
    identity — empty resource name or repo — matches NO principal, so a
    record predating the identity binding is unreachable rather than
    world-readable.
    """
    dictIdentity = dictCampaign.get("dictProjectIdentity") or {}
    if not sResourceName or not sProjectRepoPath:
        return False
    return (dictIdentity.get("sResourceName") == sResourceName
            and dictIdentity.get("sProjectRepoPath") == sProjectRepoPath)


def fdictCreateCampaign(sQuestion, listParticipants, dictSettings=None,
                        sChairbotParticipantId="", dictProjectIdentity=None):
    """Create the durable campaign record in state draft.

    Requires at least two participants covering two distinct
    (provider, model) pairs. The chairbot defaults to the first
    configured participant (section 6.3.1); the effective charter
    version and text are recorded immutably (section 5.5). The project
    identity triple binds the campaign to its lease principal and repo
    (remediation R2); the engine never reads it, the routes always do.
    """
    if not sQuestion:
        raise CouncilConfigurationError("the council question is required")
    if len(listParticipants) < 2:
        raise CouncilConfigurationError(
            "a council needs at least two participants")
    setModelPairs = {(dictParticipant["sProvider"],
                      dictParticipant["sRequestedModel"])
                     for dictParticipant in listParticipants}
    if len(setModelPairs) < 2:
        raise CouncilConfigurationError(
            "a council needs at least two distinct models — a one-model "
            "council is not a council")
    listKnownIds = [dictParticipant["sParticipantId"]
                    for dictParticipant in listParticipants]
    sChairbotId = sChairbotParticipantId or listKnownIds[0]
    if sChairbotId not in listKnownIds:
        raise CouncilConfigurationError(
            "the chairbot must be one of the configured participants")
    return {
        "sCampaignId": _fsMintIdentifier("campaign"),
        "sState": S_STATE_DRAFT,
        "dictProjectIdentity": _fdictValidateProjectIdentity(
            dictProjectIdentity),
        "sQuestion": sQuestion,
        "listParticipants": copy.deepcopy(listParticipants),
        "sChairbotParticipantId": sChairbotId,
        "sCharterVersion": S_CHARTER_VERSION,
        "sCharterText": S_CHARTER_TEXT,
        "dictSettings": _fdictValidateSettings(dictSettings),
        "listRounds": [],
        "iGrantedAdditionalRounds": 0,
        "dictCandidatePlan": None,
        "dictPendingHumanGate": None,
        "listResearcherDecisions": [],
        "listResearcherResponses": [],
        "listStateTransitions": [],
        "bStopRequested": False,
        "iObjectionCounter": 0,
        "iClaimCounter": 0,
    }


def fnTransitionCampaignState(dictCampaign, sNewState, sReason):
    """The single state-transition authority (section 4.5)."""
    if sNewState not in SET_CAMPAIGN_STATES:
        raise CouncilProtocolError(f"unknown campaign state '{sNewState}'")
    dictCampaign["listStateTransitions"].append({
        "sFromState": dictCampaign["sState"],
        "sToState": sNewState,
        "sReason": sReason,
    })
    dictCampaign["sState"] = sNewState


def fdictRestoreCampaignFromMetadata(dictMetadata):
    """Rebuild an engine-ready campaign record from checkpointed metadata.

    Validates the state vocabulary and record shape; returns a deep
    copy so the caller's metadata stays untouched (section 15.1: state
    restoration from accepted campaign metadata).
    """
    if not isinstance(dictMetadata, dict):
        raise CouncilProtocolError("campaign metadata must be a mapping")
    for sRequiredKey in LIST_CAMPAIGN_REQUIRED_KEYS:
        if sRequiredKey not in dictMetadata:
            raise CouncilProtocolError(
                f"campaign metadata is missing '{sRequiredKey}'")
    if dictMetadata["sState"] not in SET_CAMPAIGN_STATES:
        raise CouncilProtocolError(
            f"campaign metadata carries unknown state "
            f"'{dictMetadata['sState']}'")
    return copy.deepcopy(dictMetadata)


class CouncilProviderConnection:
    """The provider connection seam (section 9.8), driven by the engine.

    Phase 2 supplies the disposable-runner and API-transport
    implementations; Phase 1 tests drive fakes. The deliberation output
    is the structured result this seam returns — never a file path the
    engine watches (section 8.5). The model-accessible script tool is
    deliberately absent from this seam: it is API-backend-only and not
    part of the turn-driving interface (section 9.6).
    """

    async def fdictPrepareImmutableContext(self, dictTurnRequest):
        """Prepare the immutable per-turn context; return its identity."""
        raise NotImplementedError

    async def fnStartTurn(self, dictTurnRequest):
        """Begin the bounded provider turn."""
        raise NotImplementedError

    def fiterStreamNormalizedEvents(self):
        """Return an async iterator of normalized display events."""
        raise NotImplementedError

    async def fdictCollectStructuredResult(self):
        """Collect the turn's final structured result."""
        raise NotImplementedError

    async def fsReportCompletion(self):
        """Report 'terminal' or 'indeterminate' — never inferred quiet."""
        raise NotImplementedError
