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
import re

from .agentCouncilCharter import (
    S_CAMPAIGN_KIND_IMPLEMENTATION,
    S_CAMPAIGN_KIND_PLANNING,
    S_CHARTER_TEXT,
    S_CHARTER_VERSION,
    _fsMintIdentifier,
)

__all__ = [
    "CouncilConfigurationError",
    "CouncilProtocolError",
    "CouncilProviderConnection",
    "DICT_DEFAULT_SETTINGS",
    "I_MINIMUM_TURN_WALL_CLOCK_SECONDS",
    "I_MAXIMUM_TURN_WALL_CLOCK_SECONDS",
    "DICT_EMPTY_PROJECT_IDENTITY",
    "LIST_CAMPAIGN_REQUIRED_KEYS",
    "fsComposeUniqueCampaignName",
    "fsValidateCampaignName",
    "LIST_EXHAUSTED_ROUND_EXITS",
    "LIST_PROJECT_IDENTITY_KEYS",
    "SET_RETRYABLE_TURN_FAILURE_REASONS",
    "S_CAMPAIGN_KIND_PLANNING",
    "S_CAMPAIGN_KIND_IMPLEMENTATION",
    "TUPLE_CAMPAIGN_KINDS",
    "fsReadCampaignKind",
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
    "flistReinstateTransientlyFailedParticipants",
    "fsFindLatestFailureClassForParticipant",
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
    # Seconds one turn may run before its container is destroyed. A
    # SETTING rather than only a constant, because the right value is a
    # property of the question being asked: a repository audit with
    # dozens of tool calls is not the same shape of work as a
    # single-shot opinion, and the researcher is the one who knows
    # which they are convening.
    "iTurnWallClockSeconds": 3600,
}

# The bounds on that setting. The ceiling is not a judgement about
# model behaviour — it is the point past which a turn holding a runner,
# a snapshot copy and an egress lease stops being a turn and becomes an
# abandoned container.
I_MINIMUM_TURN_WALL_CLOCK_SECONDS = 60
I_MAXIMUM_TURN_WALL_CLOCK_SECONDS = 43200

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
# ``sSnapshotScopeNote`` is empty for a whole-repository snapshot and
# carries a server-composed sentence when the researcher excluded named
# oversized files. It lives in the project IDENTITY because that is
# what it is: two councils given different subsets of the same
# repository at the same commit were not shown the same thing, and a
# participant that is not told so will reason about an absent file as
# though it does not exist.
LIST_PROJECT_IDENTITY_KEYS = [
    "sResourceName", "sProjectRepoPath", "sSnapshotIdentity",
    "sSnapshotScopeNote",
    # Pinned at launch, compared at resume (continuation plan 4.2,
    # researcher ruling 3): the immutable image id the runners actually
    # executed in, and the byte digest of the sealed snapshot archive.
    # sSnapshotIdentity is a CONTENT identity over sorted manifest rows
    # -- not a tar-byte digest -- so archive validation needs its own.
    "sImageIdentity", "sSnapshotArchiveSha256",
]

DICT_EMPTY_PROJECT_IDENTITY = {
    "sResourceName": "",
    "sProjectRepoPath": "",
    "sSnapshotIdentity": "",
    "sImageIdentity": "",
    "sSnapshotArchiveSha256": "",
    "sSnapshotScopeNote": "",
}


# A campaign's researcher-facing NAME. Not an identifier the server
# keys on — the minted sCampaignId stays the only identity — but the one
# thing that tells two campaigns apart in a listing. A researcher
# iterating on one development prompt gets a list where every row is the
# same sentence, and picking the wrong row cost a live 13-question gate
# (2026-08-25). The contract is deliberately the campaign's OWN, not
# the step-name contract in pipelineUtils: a step name becomes a
# directory basename, so it strips only and allows 100 characters; a
# campaign name is display-only, so whitespace collapses, 80 characters
# fit a list row, and the first character must be alphanumeric so
# every name sorts and renders predictably.
# Turn-failure reasons a retry may honestly re-run (continuation plan
# 2.6): a rate limit, a wall-clock kill, and the transient
# transport/CLI classes fail differently on a second attempt; an
# authentication failure or a schema-invalid answer fails identically
# and would spend the researcher's subscription proving it. Mirrors
# the provider classification constants in ``agentCouncilProviders``
# (S_FAILURE_*), pinned by testTheRetryWhitelistMirrorsTheProvider
# Vocabulary — this module stays pure, so the strings live here.
# The kind vocabulary lives in the charter (the import leaf) and is
# re-exported here as domain vocabulary. Old records carry no kind and
# read as planning.
TUPLE_CAMPAIGN_KINDS = (
    S_CAMPAIGN_KIND_PLANNING, S_CAMPAIGN_KIND_IMPLEMENTATION)


def fsReadCampaignKind(dictCampaign):
    """Return the campaign's protocol kind, defaulting old records."""
    return dictCampaign.get("sCampaignKind") or S_CAMPAIGN_KIND_PLANNING


SET_RETRYABLE_TURN_FAILURE_REASONS = frozenset({
    "rateLimit",
    "killedNoExitCode",
    "noResultEvent",
    "cleanExit",
    "nonZeroExit",
    "killedAtTurnWallClockBudget",
    # An exception inside the connection layer: the in-process
    # transport class, transient by nature.
    "turnRaised",
    # The CLI could not reach the provider — a refused connection, a
    # dead proxy, a mid-restart Docker VM. The network healing is
    # exactly the case a re-run serves (2026-08-27).
    "networkUnreachable",
})

I_MAX_CAMPAIGN_NAME_LENGTH = 80
I_CAMPAIGN_NAME_WORDS_FROM_QUESTION = 6
_RE_CAMPAIGN_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 \-]*$")


def fsValidateCampaignName(sCampaignName):
    """Return a validated campaign name, or refuse and say why.

    Shape only. Uniqueness needs the other campaigns bound to the same
    project and is settled by :func:`fsComposeUniqueCampaignName`, which
    is where the caller has them.
    """
    sTrimmed = " ".join((sCampaignName or "").split())
    if not sTrimmed:
        raise CouncilConfigurationError("a council name must not be empty")
    if len(sTrimmed) > I_MAX_CAMPAIGN_NAME_LENGTH:
        raise CouncilConfigurationError(
            f"a council name is at most {I_MAX_CAMPAIGN_NAME_LENGTH} "
            f"characters; got {len(sTrimmed)}")
    if not _RE_CAMPAIGN_NAME.match(sTrimmed):
        raise CouncilConfigurationError(
            "a council name may hold letters, digits, spaces and hyphens, "
            f"and must start with a letter or digit; got {sCampaignName!r}")
    return sTrimmed


def fsComposeUniqueCampaignName(sRequestedName, sQuestion, saExistingNames):
    """Return the name to store: the researcher's, or one derived.

    A blank request derives from the question's opening words, which is
    better than nothing and worse than a name the researcher chose — the
    convene form should ask. A name colliding with ``saExistingNames``
    gains a numeric suffix. That is BEST-EFFORT disambiguation, not a
    uniqueness guarantee: the caller reads the existing names and then
    creates, so two concurrent starts can both pass the scan and store
    the same name. Nothing downstream keys on the name —
    ``sCampaignId`` is the only identity — so a duplicate costs a
    researcher a moment of confusion, never a misdirected action.
    """
    sBase = (fsValidateCampaignName(sRequestedName) if (sRequestedName or "").strip()
             else _fsDeriveNameFromQuestion(sQuestion))
    setTaken = {sName.casefold() for sName in saExistingNames or []}
    if sBase.casefold() not in setTaken:
        return sBase
    for iSuffix in range(2, 1000):
        sCandidate = f"{sBase} {iSuffix}"[:I_MAX_CAMPAIGN_NAME_LENGTH].strip()
        if sCandidate.casefold() not in setTaken:
            return sCandidate
    raise CouncilConfigurationError(
        f"too many councils are already named like {sBase!r}")


def _fsDeriveNameFromQuestion(sQuestion):
    """Derive a fallback name from the question's opening words."""
    saWords = [
        "".join(sCharacter for sCharacter in sWord
                if sCharacter.isalnum() or sCharacter == "-")
        for sWord in (sQuestion or "").split()
    ]
    saKept = [sWord for sWord in saWords if sWord][
        :I_CAMPAIGN_NAME_WORDS_FROM_QUESTION]
    sDerived = " ".join(saKept)[:I_MAX_CAMPAIGN_NAME_LENGTH].strip()
    return sDerived if sDerived and _RE_CAMPAIGN_NAME.match(sDerived) \
        else "Council"


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


def fsFindLatestFailureClassForParticipant(dictCampaign, sParticipantId):
    """Return the machine class of a participant's most recent failure.

    Read from the durable TURN records rather than the participant,
    because that is where the class lives and where it lives for
    campaigns checkpointed before participants carried one. Falls back
    to the reason's own leading class token, which is how a record
    written before the class field carries it.
    """
    sLatestClass = ""
    for dictRound in dictCampaign.get("listRounds") or []:
        for listTurns in (dictRound.get("dictTurnsByPhase") or {}).values():
            for dictTurn in listTurns:
                if dictTurn.get("sParticipantId") != sParticipantId:
                    continue
                if dictTurn.get("sStatus") != "failed":
                    continue
                sLatestClass = (
                    dictTurn.get("sFailureClass")
                    or str(dictTurn.get("sFailureReason") or "").split(
                        ":", 1)[0].strip()
                    or sLatestClass)
    return sLatestClass


def flistReinstateTransientlyFailedParticipants(dictCampaign):
    """Return participants retired by a TRANSIENT failure to the roster.

    A participant is retired the moment any turn of theirs fails, and
    nothing ever cleared the flag — so one rate limit removed a model
    for the life of the campaign, and with two participants the next
    blip collapsed the council into a quorum shortfall (live,
    2026-08-28: a spend limit in round 4 retired one of two models and
    the round's veto then had no voters at all).

    The retry whitelist already knows which failures do not repeat, so
    it is the authority here too: a rate limit, a killed runner, a
    transport fault come back; an authentication failure or a
    schema-invalid answer stays retired, because those fail identically
    and a reinstated participant would only fail again.

    The failed TURN records are untouched — they are the provenance
    that this participant missed work, and a reader must always be
    able to see it. Returns the reinstated participants so the caller
    can record what it did.
    """
    listReinstated = []
    for dictParticipant in dictCampaign.get("listParticipants") or []:
        if not dictParticipant.get("bFailed"):
            continue
        sFailureClass = fsFindLatestFailureClassForParticipant(
            dictCampaign, dictParticipant["sParticipantId"])
        if sFailureClass not in SET_RETRYABLE_TURN_FAILURE_REASONS:
            continue
        dictParticipant["bFailed"] = False
        dictParticipant["sFailureReason"] = ""
        listReinstated.append(dictParticipant)
    return listReinstated


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
    iWallClock = dictSettings["iTurnWallClockSeconds"]
    if not isinstance(iWallClock, int) or not (
            I_MINIMUM_TURN_WALL_CLOCK_SECONDS <= iWallClock
            <= I_MAXIMUM_TURN_WALL_CLOCK_SECONDS):
        raise CouncilConfigurationError(
            "iTurnWallClockSeconds must be an integer between "
            f"{I_MINIMUM_TURN_WALL_CLOCK_SECONDS} and "
            f"{I_MAXIMUM_TURN_WALL_CLOCK_SECONDS} seconds")
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
    # Missing keys are BACK-FILLED empty, not refused (the
    # sCampaignName precedent): the identity vocabulary grew on
    # 2026-08-26 (sImageIdentity, sSnapshotArchiveSha256, both pinned
    # at launch), and demanding the full set at creation would strand
    # every caller and record built against the four-key shape. An
    # empty value means unbound, which is already the vocabulary's
    # honest default; an UNKNOWN key still refuses.
    dictBackFilled = {**DICT_EMPTY_PROJECT_IDENTITY, **dictProjectIdentity}
    if sorted(dictBackFilled) != sorted(LIST_PROJECT_IDENTITY_KEYS):
        raise CouncilConfigurationError(
            "the campaign project identity must carry only "
            f"{LIST_PROJECT_IDENTITY_KEYS}")
    for sIdentityKey in LIST_PROJECT_IDENTITY_KEYS:
        if not isinstance(dictBackFilled[sIdentityKey], str):
            raise CouncilConfigurationError(
                f"campaign identity '{sIdentityKey}' must be a string")
    return dictBackFilled


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
                        sChairbotParticipantId="", dictProjectIdentity=None,
                        sCampaignName="", sCampaignKind="planning",
                        sSeedPlanDocument="", sSourceCampaignId=""):
    """Create the durable campaign record in state draft.

    Requires at least two participants covering two distinct
    (provider, model) pairs. The chairbot defaults to the first
    configured participant (section 6.3.1); the effective charter
    version and text are recorded immutably (section 5.5). The project
    identity triple binds the campaign to its lease principal and repo
    (remediation R2); the engine never reads it, the routes always do.

    ``sCampaignKind`` selects the protocol walk: a PLANNING council
    deliberates a plan; an IMPLEMENTATION council produces a reviewed
    PATCH against the sealed snapshot, seeded with an accepted plan
    (``sSeedPlanDocument``, loaded server-side from the source
    campaign named by ``sSourceCampaignId`` — never client-supplied
    text). Ruling (design review question 19): no runner ever holds a
    writable path to the live project; the patch is applied by the
    researcher's own hand or not at all.
    """
    if not sQuestion:
        raise CouncilConfigurationError("the council question is required")
    if sCampaignKind not in TUPLE_CAMPAIGN_KINDS:
        raise CouncilConfigurationError(
            f"unknown campaign kind '{sCampaignKind}'; one of "
            f"{list(TUPLE_CAMPAIGN_KINDS)}")
    if sCampaignKind == S_CAMPAIGN_KIND_IMPLEMENTATION and not (
            sSeedPlanDocument):
        raise CouncilConfigurationError(
            "an implementation council needs the accepted plan it "
            "implements; convene it from a completed planning council")
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
        "sCampaignName": sCampaignName or "Council",
        # Not in LIST_CAMPAIGN_REQUIRED_KEYS: campaigns checkpointed by
        # an earlier hub carry no kind and must restore as planning —
        # every read goes through .get with the planning default.
        "sCampaignKind": sCampaignKind,
        "sSeedPlanDocument": sSeedPlanDocument,
        "sSourceCampaignId": sSourceCampaignId,
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
        # What a council that never converged wrote instead of a plan
        # (charter 1.7.0). Deliberately its OWN key: filing it beside
        # the candidate would let every reader downstream present a
        # consensus that was never reached. Not in
        # LIST_CAMPAIGN_REQUIRED_KEYS — campaigns checkpointed by an
        # earlier hub carry no such key — so every read uses .get.
        "dictDeliberationSummary": None,
        "dictPendingHumanGate": None,
        "listResearcherDecisions": [],
        "listResearcherResponses": [],
        "listStateTransitions": [],
        "bStopRequested": False,
        "iObjectionCounter": 0,
        "iClaimCounter": 0,
        # What the engine is running RIGHT NOW, or None between phases.
        # A turn record only exists once its turn has settled, so a
        # record alone cannot say that a phase is under way — a reader
        # watching only settled turns sees the whole of cross-review as
        # "nothing has happened since the proposals", which reads as a
        # hung council. Not in LIST_CAMPAIGN_REQUIRED_KEYS: campaigns
        # checkpointed by an earlier hub carry no such key and must
        # still restore, so every read of it goes through .get.
        "dictPhaseInFlight": None,
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
    dictRestored = copy.deepcopy(dictMetadata)
    # Back-filled, NOT required. LIST_CAMPAIGN_REQUIRED_KEYS is checked
    # strictly above, so adding the name there would strand every
    # campaign checkpointed before it existed — including, when this
    # landed, a live 13-question gate the researcher was waiting on.
    if not dictRestored.get("sCampaignName"):
        dictRestored["sCampaignName"] = _fsDeriveNameFromQuestion(
            dictRestored.get("sQuestion", ""))
    return dictRestored


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
