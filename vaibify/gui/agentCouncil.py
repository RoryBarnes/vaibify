"""Agent Council pure planning engine (Phase 1, design/agentCouncil.md).

This module owns the Standard planning protocol (section 5.1): the
phase-synchronous barrier with bounded concurrency, the round loop,
chairbot synthesis with a deterministic fallback, the required voter set
frozen at synthesis, per-turn execution over the provider connection
seam with a single bounded repair attempt, and the researcher-facing
transitions (human pause and continuation, the three exhausted-round
exits, acceptance, rejection, and stop-after-current-turn).

The engine is composed from four cohesive modules kept separate along
real seams (section 12):

- ``agentCouncilCharter`` — the server-owned instruction contract and
  turn schema (sections 5.5-5.6, 8.5): what a turn receives and returns;
- ``agentCouncilCampaign`` — the campaign domain records, the state
  vocabulary, the lifecycle, and the provider connection seam;
- ``agentCouncilResolution`` — the round-termination, quorum-floor and
  human-gate rules (section 5.1 step 5, section 5.4);
- ``agentCouncilEvidence`` — evidence discipline: settling a turn's
  confirmed claims against the ledger or reverting them (section 7.4).

The commonly-used public symbols of the domain and charter modules are
re-exported here so ``vaibify.gui.agentCouncil`` remains the single
import point for the pure engine.

It is deliberately pure: no Docker, no routes, no filesystem, no wall
clock. Execution reaches it only through the provider connection seam
(section 9.8) and four injected callbacks — event append, evidence
ledger, checkpoint, and the baseline-evidence executor. State
transitions belong to this engine alone; callers never mutate
``sState`` directly.
"""

import asyncio
import copy
import json
import logging

from .agentCouncilCampaign import (
    CouncilConfigurationError,
    CouncilProtocolError,
    CouncilProviderConnection,
    LIST_EXHAUSTED_ROUND_EXITS,
    S_CLAIM_ASSERTED,
    S_CLAIM_BLOCKED,
    S_CLAIM_CONFIRMED,
    S_CLAIM_SOURCE_SUPPORTED,
    S_COMPLETION_INDETERMINATE,
    S_COMPLETION_TERMINAL,
    S_EXECUTION_FULL_SANDBOX,
    S_EXECUTION_READ_ONLY,
    S_EXIT_GRANT_RESOLUTION_ROUND,
    S_EXIT_REJECT_OR_ARCHIVE,
    S_EXIT_RESOLVE_OR_OVERRIDE,
    S_GATE_BLOCKING_QUESTION,
    S_GATE_EXHAUSTED_ROUNDS,
    S_GATE_QUORUM_SHORTFALL,
    S_STATE_ARCHIVED,
    S_STATE_AWAITING_IMPLEMENTATION,
    S_STATE_DRAFT,
    S_STATE_FAILED,
    S_STATE_INTERRUPTED,
    S_STATE_NEEDS_HUMAN,
    S_STATE_PLANNING,
    S_STATE_PLAN_ACCEPTED,
    S_STATE_PLAN_READY,
    S_VERDICT_ACCEPT,
    S_VERDICT_BLOCKING_OBJECTION,
    S_VERDICT_NEEDS_HUMAN,
    S_VERDICT_UNDETERMINED,
    SET_CAMPAIGN_STATES,
    SET_RECOGNIZED_VETO_VERDICTS,
    fdictCreateCampaign,
    fdictCreateParticipant,
    fdictRestoreCampaignFromMetadata,
    fnTransitionCampaignState,
)
from .agentCouncilCharter import (
    DICT_PHASE_INSTRUCTIONS,
    LIST_TURN_RESULT_ARRAY_KEYS,
    LIST_TURN_RESULT_STRING_KEYS,
    S_CHARTER_TEXT,
    S_CHARTER_VERSION,
    S_PHASE_CROSS_REVIEW,
    S_PHASE_PROPOSAL,
    S_PHASE_SYNTHESIS,
    S_PHASE_VETO,
    S_QUOTED_MATERIAL_LABEL,
    fdictBuildQuotedEntry,
    fdictComposeTurnRequest,
    fdictValidateTurnResult,
    flistBlindQuotedMaterial,
    fsComposeDecisionAnswers,
    flistBuildQuotedMaterial,
    fsComposeTurnInstruction,
)
from .agentCouncilCharter import (
    S_PHASE_CONFORMANCE_REVIEW,
    S_PHASE_DELIBERATION_SUMMARY,
    S_PHASE_IMPLEMENTATION,
    fbCharterAsksForNotedFindings,
    fbIsImplementationCampaign,
    fbTurnRequiresPatchSchema,
    fbTurnRequiresSummarySchema,
)
from .agentCouncilEvidence import EvidenceDisciplineMixin
from .agentCouncilResolution import RoundResolutionMixin

logger = logging.getLogger("vaibify")

__all__ = [
    "CouncilConfigurationError",
    "CouncilEngine",
    "CouncilProtocolError",
    "CouncilProviderConnection",
    "DICT_PHASE_INSTRUCTIONS",
    "LIST_EXHAUSTED_ROUND_EXITS",
    "LIST_TURN_RESULT_ARRAY_KEYS",
    "LIST_TURN_RESULT_STRING_KEYS",
    "S_CHARTER_TEXT",
    "S_CHARTER_VERSION",
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
    "S_PHASE_CROSS_REVIEW",
    "S_PHASE_DELIBERATION_SUMMARY",
    "S_PHASE_PROPOSAL",
    "S_PHASE_SYNTHESIS",
    "S_PHASE_VETO",
    "S_QUOTED_MATERIAL_LABEL",
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
    "fdictBuildQuotedEntry",
    "fdictComposeTurnRequest",
    "fdictCreateCampaign",
    "fdictCreateParticipant",
    "fdictRestoreCampaignFromMetadata",
    "fdictValidateTurnResult",
    "flistBlindQuotedMaterial",
    "flistBuildQuotedMaterial",
    "fnTransitionCampaignState",
    "fsComposeTurnInstruction",
]

# A rejected payload is a diagnostic, not evidence, so it is bounded
# hard. The failures worth diagnosing are short — an empty result, a
# CLI error line, a refusal sentence — and a model that returned
# megabytes of prose already has its own recorded byte-budget failure.
I_MAX_REJECTED_PAYLOAD_CHARACTERS = 2000


DICT_EMPTY_TURN_EXPLANATIONS = {
    "runnerOutOfMemory":
        "this agent's container ran out of memory and the kernel "
        "stopped it. Raising the runner's memory limit is the remedy; "
        "the work it had done is lost.",
    "killedAtTurnOutputCap":
        "this agent produced more output than one turn is allowed to "
        "carry, so its container was stopped and its work was lost. "
        "Agents that narrate heavily or invoke many tools hit this "
        "before they hit the time limit; raising the per-turn output "
        "cap is the remedy.",
    "killedAtTurnWallClockBudget":
        "this agent was still working when the turn's time budget ran "
        "out, so its container was stopped and it never returned an "
        "answer. Agents that explore the repository with many tool "
        "calls hit this; a single-shot answer does not. Raising the "
        "per-turn budget is the remedy, not a change to the question.",
    "killedAfterProducingNothing":
        "this agent produced nothing at all for long enough that its "
        "turn was treated as stopped, and its container was stopped "
        "with it. This is what a dead provider connection, a wedged "
        "CLI, or a container that lost its network looks like from "
        "outside — NOT a slow answer, because a working agent emits "
        "something as it goes. Retrying is the right response; if it "
        "recurs immediately, the fault is in the environment rather "
        "than the question.",
    "noResultEvent":
        "the assistant stopped without returning an answer. Nothing in "
        "the stream reported an error, so the cause is outside what the "
        "turn can see.",
    # Composed rather than fixed: the SIGKILL case earns the exit code
    # and the acquittal of the council's own bounds. See
    # _fsExplainKilledRunner.
    "runnerKilledByTheEnvironment": "",
    "resultEventCarriedNoText":
        "the assistant finished but its answer was empty.",
    "rateLimit":
        "the provider refused this turn at a usage, spend, or rate "
        "limit (rateLimit). Nothing is wrong with the council; retry "
        "the failed phase once the limit's window resets.",
    "authenticationFailure":
        "the provider refused the runner's login "
        "(authenticationFailure). Re-run the project's login check, "
        "then retry the failed phase.",
    "cliReportedErrorResult":
        "the CLI reported an error instead of an answer "
        "(cliReportedErrorResult).",
    "networkUnreachable":
        "the runner could not reach the provider (networkUnreachable) "
        "— a refused connection or dead proxy, commonly a Docker "
        "daemon or VM mid-restart. Retry once the environment "
        "settles.",
}


# SIGKILL: 128 + 9. Docker reports it for a kernel OOM kill, a
# daemon-side kill, and a `docker kill` alike, so the code alone never
# names a culprit — which is exactly why the card must say what it
# has RULED OUT rather than guess.
I_EXIT_CODE_SIGKILL = 137


def _fsExplainKilledRunner(dictRawResult):
    """Explain a runner the environment killed, or "" when it did not.

    A turn that ends with no result event and a SIGKILL exit is not a
    model that stopped talking: something destroyed its container. The
    record already carried the exit code and every bound's flag, and
    the card said only "the cause is outside what the turn can see" —
    true, and twenty minutes of reading JSON short of useful (live,
    2026-08-28: two round-4 synthesis turns killed at 42s and 92s with
    every council bound clean).

    Only claims the ACQUITTAL, never a culprit: the council's own
    limits did not fire. Which outside agent did the killing — a
    kernel OOM the daemon failed to flag, a daemon fault, a VM under
    pressure — is not something this record can prove, and a card that
    guessed would send a researcher to the wrong fix.
    """
    if dictRawResult.get("jsonExitCode") != I_EXIT_CODE_SIGKILL:
        return ""
    if (dictRawResult.get("bWallClockExceeded")
            or dictRawResult.get("bOutputCapExceeded")
            or dictRawResult.get("bOomKilled")):
        # A bound DID fire; its own explanation is the honest one and
        # already carries the remedy.
        return ""
    return (
        f"the runner was killed (exit {I_EXIT_CODE_SIGKILL}, SIGKILL) "
        "part-way through its turn, and none of the council's own "
        "bounds fired — not the turn's time budget, not its output "
        "cap, and the container reported no out-of-memory kill. "
        "Something outside the council stopped it, so this is an "
        "environment fault (a Docker daemon or VM problem), not a "
        "council one. Retrying is reasonable once the environment is "
        "healthy."
    )


def _fsExplainEmptyTurn(sEmptyReason, dictRawResult):
    """Render an empty turn in words a researcher can act on.

    The schema validator's output is the wrong vocabulary here: fifteen
    "must be an array" lines describe the SHAPE of an answer that does
    not exist, and say nothing about why.
    """
    dictTally = (dictRawResult or {}).get("dictEventTypeCounts") or {}
    iAssistant = dictTally.get("assistant", 0)
    sProgress = (
        f" It had produced {iAssistant} messages before stopping."
        if iAssistant else "")
    # The CLI's own words, when it reported the failure itself — a
    # usage-limit message names the reset time, which is the one fact
    # the researcher plans around.
    sCliError = (dictRawResult or {}).get("sCliErrorText") or ""
    sCliSentence = f" The CLI said: {sCliError}" if sCliError else ""
    # The kill explanation REPLACES the class's own wording: "the
    # assistant stopped without returning an answer" describes a model
    # that fell silent, which is the wrong story for a container that
    # was destroyed under it.
    sKilled = _fsExplainKilledRunner(dictRawResult or {})
    sExplanation = sKilled or DICT_EMPTY_TURN_EXPLANATIONS.get(
        sEmptyReason, f"the turn returned nothing ({sEmptyReason}).")
    return "emptyTurn: " + sExplanation + sProgress + sCliSentence


def _fsSummarizeRejectedPayload(dictRawResult):
    """Render what a participant returned, for a turn that was rejected.

    Serialized rather than stored as a mapping because the point is to
    show the SHAPE the validator saw — ``{}`` and
    ``{"sRawResultText": ""}`` are different diagnoses (nothing parsed
    versus nothing said) and a record that normalized them would lose
    the distinction that matters.
    """
    try:
        sPayload = json.dumps(dictRawResult, default=str, sort_keys=True)
    except (TypeError, ValueError) as errorSerialize:
        sPayload = f"<unserializable: {errorSerialize}>"
    if len(sPayload) <= I_MAX_REJECTED_PAYLOAD_CHARACTERS:
        return sPayload
    return (sPayload[:I_MAX_REJECTED_PAYLOAD_CHARACTERS]
            + f"… [truncated from {len(sPayload)} characters]")


class CouncilEngine(RoundResolutionMixin, EvidenceDisciplineMixin):
    """Drives the Standard planning protocol over one campaign record.

    All continuity lives in the campaign record (section 5.0):
    ``fdictRunUntilBlocked`` inspects the record and continues from the
    next unfinished phase, which is also what makes crash restoration
    and human-pause continuation the same code path. The termination
    rules come from ``RoundResolutionMixin`` and the evidence discipline
    from ``EvidenceDisciplineMixin``; this class owns the orchestration
    that produces the turns those rules judge.
    """

    def __init__(self, dictCampaign, dictConnections, fnAppendEvent,
                 fdictRecordEvidence, fnCheckpointCampaign,
                 fdictExecuteBaselineEvidence):
        for dictParticipant in dictCampaign["listParticipants"]:
            if dictParticipant["sParticipantId"] not in dictConnections:
                raise CouncilConfigurationError(
                    "every participant needs a provider connection")
        for fnRequiredCallback in (fnAppendEvent, fdictRecordEvidence,
                                   fnCheckpointCampaign,
                                   fdictExecuteBaselineEvidence):
            if not callable(fnRequiredCallback):
                raise CouncilConfigurationError(
                    "engine callbacks must be callable")
        self.dictCampaign = dictCampaign
        self.dictConnections = dictConnections
        self.fnAppendEvent = fnAppendEvent
        self.fdictRecordEvidence = fdictRecordEvidence
        self.fnCheckpointCampaign = fnCheckpointCampaign
        self.fdictExecuteBaselineEvidence = fdictExecuteBaselineEvidence

    # ----- record helpers ------------------------------------------------

    def _fnEmitEvent(self, sEventKind, dictDetail):
        dictEvent = {"sEventKind": sEventKind,
                     "sCampaignId": self.dictCampaign["sCampaignId"]}
        dictEvent.update(dictDetail)
        self.fnAppendEvent(dictEvent)

    def _fnTransition(self, sNewState, sReason):
        fnTransitionCampaignState(self.dictCampaign, sNewState, sReason)
        self._fnEmitEvent("stateTransition",
                          {"sToState": sNewState, "sReason": sReason})
        self.fnCheckpointCampaign(self.dictCampaign)

    def _fdictFindParticipant(self, sParticipantId):
        for dictParticipant in self.dictCampaign["listParticipants"]:
            if dictParticipant["sParticipantId"] == sParticipantId:
                return dictParticipant
        raise CouncilProtocolError(f"unknown participant {sParticipantId}")

    def _flistActiveParticipants(self):
        return [dictParticipant
                for dictParticipant in self.dictCampaign["listParticipants"]
                if not dictParticipant["bFailed"]]

    def _fiRoundBudget(self):
        return (self.dictCampaign["dictSettings"]["iMaximumRounds"]
                + self.dictCampaign["iGrantedAdditionalRounds"])

    # ----- the protocol loop --------------------------------------------

    async def fdictRunUntilBlocked(self):
        """Advance the campaign until it terminates, pauses, or stops."""
        if self.dictCampaign["sState"] == S_STATE_DRAFT:
            self._fnTransition(S_STATE_PLANNING, "campaignLaunched")
        while self.dictCampaign["sState"] == S_STATE_PLANNING:
            if self.dictCampaign["bStopRequested"]:
                self._fnTransition(S_STATE_ARCHIVED, "stopAfterCurrentTurn")
                break
            if self.dictCampaign.get("bPauseRequested"):
                self._fnStandDownAtPhaseBoundary()
                break
            dictRound = self._fdictEnsureOpenRound()
            if dictRound is None:
                continue
            sPhase = self._fsNextPhaseForRound(dictRound)
            if sPhase is None:
                self._fnResolveRoundTermination(dictRound)
                continue
            await self._fnRunPhase(dictRound, sPhase)
            self._fnSettlePhaseOutcome(dictRound, sPhase)
        self._fnRetireAPauseTheWalkOutran()
        return copy.deepcopy(self.dictCampaign)

    def _fnStandDownAtPhaseBoundary(self):
        """Honour a pause where the record already proves a boundary.

        Checked at the TOP of the walk, so the phase that was running
        has fully settled: its attempt record reads ``outcomeSettled``
        or ``turnsSettled``, which is exactly what a hub killed between
        phases leaves behind — and exactly what the resume admission
        already recognises. Nothing transitions: the campaign IS still
        planning, and minting a paused state would be a second
        authority on what may be continued, disagreeing with the first
        the day one of them changed.
        """
        self._fnEmitEvent("campaignPaused", {
            "iRoundNumber": len(self.dictCampaign["listRounds"])})
        self.fnCheckpointCampaign(self.dictCampaign)

    def _fnRetireAPauseTheWalkOutran(self):
        """Clear a pause request the walk left the planning state before.

        A pause admitted while the last phase ran is OVERTAKEN when
        that phase opens a human gate, readies a plan, or fails: the
        council stopped for its own reason and nothing was paused. The
        flag must not survive that, or the next continuation spawns a
        drive whose first act is to stand down again — the researcher
        clicks Answer and watches nothing happen.
        """
        if not self.dictCampaign.get("bPauseRequested"):
            return
        if self.dictCampaign["sState"] == S_STATE_PLANNING:
            return
        self.dictCampaign["bPauseRequested"] = False
        self._fnEmitEvent("pauseOutrunByOutcome",
                          {"sState": self.dictCampaign["sState"]})
        self.fnCheckpointCampaign(self.dictCampaign)

    def _fdictEnsureOpenRound(self):
        listRounds = self.dictCampaign["listRounds"]
        if listRounds and not listRounds[-1]["sResolution"]:
            return listRounds[-1]
        iOpenedRounds = len([
            dictRound for dictRound in listRounds
            if not dictRound["bFinalVetoRound"]
            and not dictRound.get("bDeliberationSummaryRound")])
        if listRounds and iOpenedRounds >= self._fiRoundBudget():
            dictSummaryRound = self._fdictOpenDeliberationSummaryRound()
            if dictSummaryRound is not None:
                return dictSummaryRound
            self._fnOpenExhaustedGate()
            return None
        dictRound = {
            "iRoundNumber": len(listRounds) + 1,
            "bFinalVetoRound": False,
            "dictTurnsByPhase": {},
            "bSynthesisSettled": False,
            "sSynthesisAuthorId": "",
            "bChairbotSubstituted": False,
            "listFrozenVoterIds": None,
            "dictVetoVerdicts": {},
            "listUnresolvedObjections": [],
            # Questions raised before synthesis, held so the gate can
            # present them against the plan they are about. Read with
            # .get elsewhere: rounds checkpointed by an earlier hub carry
            # no such key and must still resolve. The final veto round
            # has no key at all — it runs only the veto phase, which
            # settles before any deferral can happen.
            "listDeferredQuestions": [],
            # The durable phase-attempt record (continuation plan
            # section 2): what recovery reads. The live walk keeps
            # reading _fsNextPhaseForRound; only one authority is ever
            # consulted per code path.
            "dictPhaseAttempt": None,
            "listRetiredAttempts": [],
            "sResolution": "",
        }
        listRounds.append(dictRound)
        self._fnEmitEvent("roundOpened",
                          {"iRoundNumber": dictRound["iRoundNumber"]})
        return dictRound

    def _fdictOpenDeliberationSummaryRound(self):
        """Open the closing summary round, or None if one already ran.

        A council that ran out of rounds without converging used to
        simply stop, handing the researcher an objection list and
        nothing that said what the argument had been about (researcher
        direction 2026-08-29). It now spends one more chairbot turn
        writing a DELIBERATION SUMMARY first.

        The round is excluded from the budget for the same reason the
        final veto round is: counting it would consume the grant the
        researcher may be about to make. It is keyed to the budget it
        closes, so granting more rounds and exhausting them again earns
        a fresh summary of the argument as it then stands, while a
        re-entry into an already-summarised exhaustion does not loop.
        """
        iBudget = self._fiRoundBudget()
        for dictRound in self.dictCampaign["listRounds"]:
            if (dictRound.get("bDeliberationSummaryRound")
                    and dictRound.get("iRoundBudgetAtSummary") == iBudget):
                return None
        dictRound = {
            "iRoundNumber": len(self.dictCampaign["listRounds"]) + 1,
            "bFinalVetoRound": False,
            "bDeliberationSummaryRound": True,
            "iRoundBudgetAtSummary": iBudget,
            "dictTurnsByPhase": {},
            "bSynthesisSettled": True,
            "sSynthesisAuthorId": "",
            "bChairbotSubstituted": False,
            # None, so _fnOpenFinalVetoRound keeps reading the last
            # round that actually froze a voter set.
            "listFrozenVoterIds": None,
            "dictVetoVerdicts": {},
            "listUnresolvedObjections": [],
            "listDeferredQuestions": [],
            "dictPhaseAttempt": None,
            "listRetiredAttempts": [],
            "sResolution": "",
        }
        self.dictCampaign["listRounds"].append(dictRound)
        self._fnEmitEvent("deliberationSummaryRoundOpened",
                          {"iRoundNumber": dictRound["iRoundNumber"]})
        return dictRound

    def _fsNextPhaseForRound(self, dictRound):
        bImplementationWalk = fbIsImplementationCampaign(self.dictCampaign)
        if dictRound.get("bDeliberationSummaryRound"):
            listPhaseOrder = [S_PHASE_DELIBERATION_SUMMARY]
        elif dictRound["bFinalVetoRound"]:
            listPhaseOrder = [S_PHASE_VETO]
        elif bImplementationWalk and dictRound["iRoundNumber"] == 1:
            listPhaseOrder = [S_PHASE_IMPLEMENTATION,
                              S_PHASE_CONFORMANCE_REVIEW,
                              S_PHASE_SYNTHESIS, S_PHASE_VETO]
        elif bImplementationWalk:
            listPhaseOrder = [S_PHASE_CONFORMANCE_REVIEW,
                              S_PHASE_SYNTHESIS, S_PHASE_VETO]
        elif dictRound["iRoundNumber"] == 1:
            listPhaseOrder = [S_PHASE_PROPOSAL, S_PHASE_CROSS_REVIEW,
                              S_PHASE_SYNTHESIS, S_PHASE_VETO]
        else:
            listPhaseOrder = [S_PHASE_CROSS_REVIEW, S_PHASE_SYNTHESIS,
                              S_PHASE_VETO]
        for sPhase in listPhaseOrder:
            if sPhase == S_PHASE_SYNTHESIS:
                if not dictRound["bSynthesisSettled"]:
                    return sPhase
            elif sPhase not in dictRound["dictTurnsByPhase"]:
                return sPhase
        return None

    async def _fnRunPhase(self, dictRound, sPhase):
        self._fnEmitEvent("phaseStarted", {
            "sPhase": sPhase, "iRoundNumber": dictRound["iRoundNumber"]})
        self._fnBeginPhaseAttempt(dictRound, sPhase)
        self._fnRecordPhaseInFlight(dictRound, sPhase)
        if sPhase == S_PHASE_SYNTHESIS:
            await self._fnRunSynthesisPhase(dictRound)
        elif sPhase == S_PHASE_DELIBERATION_SUMMARY:
            await self._fnRunDeliberationSummaryPhase(dictRound)
        elif sPhase == S_PHASE_IMPLEMENTATION:
            await self._fnRunImplementationPhase(dictRound)
        elif sPhase == S_PHASE_VETO:
            await self._fnRunVetoPhase(dictRound)
        else:
            listParticipants = self._flistActiveParticipants()
            await self._fnRunTurnsWithBarrier(dictRound, sPhase,
                                              listParticipants)
        if sPhase != S_PHASE_SYNTHESIS:
            # A phase with zero turns (an empty frozen veto set, or a
            # round whose participants all failed) still completed: record
            # its key so ``_fsNextPhaseForRound`` reads it as done rather
            # than re-running it forever.
            dictRound["dictTurnsByPhase"].setdefault(sPhase, [])
        self._fnMarkAttemptTurnsSettled(dictRound)
        self._fnRecordPhaseInFlight(None, "")
        self._fnEmitEvent("phaseSettled", {
            "sPhase": sPhase, "iRoundNumber": dictRound["iRoundNumber"]})

    def _fnRecordPhaseInFlight(self, dictRound, sPhase):
        """Record the phase the engine is about to run, or clear it.

        Checkpointed, because a poll reads the checkpointed record: an
        event would be the more natural home, but the event ring evicts
        under load and a display that loses the eviction loses the only
        statement that a phase is running at all.
        """
        self.dictCampaign["dictPhaseInFlight"] = None if sPhase == "" else {
            "sPhase": sPhase,
            "iRoundNumber": dictRound["iRoundNumber"],
            "listRunningParticipantIds": [],
        }
        self.fnCheckpointCampaign(self.dictCampaign)

    def _fnRecordParticipantRunning(self, sParticipantId, bRunning):
        """Add or remove one participant from the in-flight phase.

        Which agent is working is not derivable from the phase: synthesis
        runs ONE author, chosen by a fallback chain, and a display that
        guessed the configured chairbot would name the wrong agent
        exactly when a substitution had happened. The engine knows, so
        it says.
        """
        dictInFlight = self.dictCampaign.get("dictPhaseInFlight")
        if dictInFlight is None:
            return
        listRunning = dictInFlight["listRunningParticipantIds"]
        if bRunning and sParticipantId not in listRunning:
            listRunning.append(sParticipantId)
        elif not bRunning and sParticipantId in listRunning:
            listRunning.remove(sParticipantId)
        self.fnCheckpointCampaign(self.dictCampaign)

    def _fnBeginPhaseAttempt(self, dictRound, sPhase):
        """Open the durable attempt record BEFORE the first launch.

        The eligible set and completion rule are fixed here, up front,
        because "every expected participant has a terminal turn" is
        only checkable against a set recorded before any turn could
        fail out of it — and synthesis has NO fixed expected set (it
        tries authors sequentially and stops at the first success), so
        its rule is recorded as firstAuthorOrExhaustion instead.
        ``dictPrePhaseState`` is what a retirement restores. The
        following ``_fnRecordPhaseInFlight`` checkpoints, so the
        running attempt is durable before the first provider request.
        """
        listEligibleIds, sCompletionRule = (
            self._ftDescribeAttemptEligibility(dictRound, sPhase))
        iPriorAttempts = len([
            dictRetired
            for dictRetired in dictRound.get("listRetiredAttempts") or []
            if dictRetired.get("sPhase") == sPhase])
        dictRound["dictPhaseAttempt"] = {
            "sPhase": sPhase,
            "iRoundNumber": dictRound["iRoundNumber"],
            "iAttemptNumber": iPriorAttempts + 1,
            "listEligibleParticipantIds": listEligibleIds,
            "sCompletionRule": sCompletionRule,
            "sAttemptState": "running",
            "sOutcome": "",
            "dictPrePhaseState": self._fdictCapturePrePhaseState(dictRound),
        }

    def _ftDescribeAttemptEligibility(self, dictRound, sPhase):
        """Return (ordered eligible participant ids, completion rule)."""
        if sPhase in (S_PHASE_SYNTHESIS, S_PHASE_DELIBERATION_SUMMARY):
            sChairbotId = self.dictCampaign["sChairbotParticipantId"]
            listAuthorOrder = sorted(
                self._flistActiveParticipants(),
                key=lambda dictParticipant:
                    dictParticipant["sParticipantId"] != sChairbotId)
            return ([dictParticipant["sParticipantId"]
                     for dictParticipant in listAuthorOrder],
                    "firstAuthorOrExhaustion")
        if sPhase == S_PHASE_VETO:
            return (list(dictRound["listFrozenVoterIds"] or []),
                    "allEligible")
        return ([dictParticipant["sParticipantId"]
                 for dictParticipant in self._flistActiveParticipants()],
                "allEligible")

    def _fdictCapturePrePhaseState(self, dictRound):
        """Snapshot the derived state a retirement must restore.

        A phase settles more than turns: retirement of a failed attempt
        restores all of this as one checkpoint, so a re-run starts from
        the state the attempt found, not the state it half-wrote.
        """
        return copy.deepcopy({
            "bSynthesisSettled": dictRound.get("bSynthesisSettled", False),
            "sSynthesisAuthorId": dictRound.get("sSynthesisAuthorId", ""),
            "bChairbotSubstituted": dictRound.get(
                "bChairbotSubstituted", False),
            "listFrozenVoterIds": dictRound.get("listFrozenVoterIds"),
            "listDeferredQuestions": dictRound.get(
                "listDeferredQuestions") or [],
            "dictVetoVerdicts": dictRound.get("dictVetoVerdicts") or {},
            "listUnresolvedObjections": dictRound.get(
                "listUnresolvedObjections") or [],
            "dictCandidatePlan": self.dictCampaign.get("dictCandidatePlan"),
            "dictParticipantFailures": {
                dictParticipant["sParticipantId"]: {
                    "bFailed": dictParticipant["bFailed"],
                    "sFailureReason": dictParticipant.get(
                        "sFailureReason", ""),
                }
                for dictParticipant in self.dictCampaign[
                    "listParticipants"]},
        })

    def _fnMarkAttemptTurnsSettled(self, dictRound):
        """Record that the attempt's completion rule is met.

        This is what "all agents completed the step" means, made
        checkable: allEligible needs a terminal turn from every
        eligible participant; firstAuthorOrExhaustion needs one
        completed author or every author failed. The caller's next
        checkpoint carries it, so a crash after the last turn but
        before settlement leaves ``turnsSettled`` — recoverable by
        settlement replay — never a phase key masquerading as done.
        """
        dictAttempt = dictRound.get("dictPhaseAttempt")
        if dictAttempt is None or dictAttempt["sAttemptState"] != "running":
            return
        listTurnRecords = dictRound["dictTurnsByPhase"].get(
            dictAttempt["sPhase"], [])
        dictStatusById = {}
        for dictTurnRecord in listTurnRecords:
            dictStatusById.setdefault(
                dictTurnRecord["sParticipantId"], set()).add(
                dictTurnRecord["sStatus"])
        listEligibleIds = dictAttempt["listEligibleParticipantIds"]
        if dictAttempt["sCompletionRule"] == "firstAuthorOrExhaustion":
            bMet = any("completed" in dictStatusById.get(sEligibleId, set())
                       for sEligibleId in listEligibleIds) or (
                bool(listEligibleIds)
                and all("failed" in dictStatusById.get(sEligibleId, set())
                        for sEligibleId in listEligibleIds))
        else:
            bMet = all(
                dictStatusById.get(sEligibleId, set())
                & {"completed", "failed"}
                for sEligibleId in listEligibleIds)
        if bMet:
            dictAttempt["sAttemptState"] = "turnsSettled"

    def _fsDescribeCurrentAttemptBinding(self):
        """Name the live attempt an evidence entry is written under.

        Bound at WRITE time (continuation plan 2.6) so a later
        retirement can mark exactly this attempt's entries retired --
        excluded from the active history, never deleted.
        """
        listRounds = self.dictCampaign.get("listRounds") or []
        dictAttempt = (listRounds[-1] if listRounds else {}).get(
            "dictPhaseAttempt") or {}
        if not dictAttempt:
            return ""
        return (f"{dictAttempt['sPhase']}"
                f"#{dictAttempt['iAttemptNumber']}")

    def fdictRetireTerminalAttempt(self):
        """Retire the terminating attempt and restore its pre-phase state.

        The researcher's ruling (continuation plan 2.5/2.6): a failure
        re-runs the phase that failed, and the failed attempt is
        retired into the record, never erased. Retirement is a
        TRANSACTION over derived state -- a phase settles more than
        turns -- restored from the snapshot the attempt captured
        before its first launch, and the whole restoration rides the
        planning transition's checkpoint atomically. The retired
        attempt keeps its turns: a plan that reached consensus after
        three re-rolls is not the same artifact as one that reached it
        first time, and a reader must be able to tell.
        """
        dictRound = self.dictCampaign["listRounds"][-1]
        dictAttempt = dictRound.get("dictPhaseAttempt")
        if (not dictAttempt
                or dictAttempt.get("sAttemptState") != "outcomeSettled"
                or dictAttempt.get("sOutcome") not in (
                    "transitioned:failed", "transitioned:interrupted")):
            raise CouncilProtocolError(
                "only an attempt whose settled outcome terminated the "
                "campaign can be retired")
        dictPre = dictAttempt.get("dictPrePhaseState") or {}
        dictRound["bSynthesisSettled"] = dictPre.get(
            "bSynthesisSettled", False)
        dictRound["sSynthesisAuthorId"] = dictPre.get(
            "sSynthesisAuthorId", "")
        dictRound["bChairbotSubstituted"] = dictPre.get(
            "bChairbotSubstituted", False)
        dictRound["listFrozenVoterIds"] = copy.deepcopy(
            dictPre.get("listFrozenVoterIds"))
        dictRound["listDeferredQuestions"] = copy.deepcopy(
            dictPre.get("listDeferredQuestions") or [])
        dictRound["dictVetoVerdicts"] = copy.deepcopy(
            dictPre.get("dictVetoVerdicts") or {})
        dictRound["listUnresolvedObjections"] = copy.deepcopy(
            dictPre.get("listUnresolvedObjections") or [])
        self.dictCampaign["dictCandidatePlan"] = copy.deepcopy(
            dictPre.get("dictCandidatePlan"))
        for dictParticipant in self.dictCampaign["listParticipants"]:
            dictFailure = (dictPre.get("dictParticipantFailures")
                           or {}).get(dictParticipant["sParticipantId"])
            if dictFailure is not None:
                dictParticipant["bFailed"] = dictFailure["bFailed"]
                dictParticipant["sFailureReason"] = dictFailure[
                    "sFailureReason"]
        listRetiredTurns = dictRound["dictTurnsByPhase"].pop(
            dictAttempt["sPhase"], [])
        dictRound.setdefault("listRetiredAttempts", []).append({
            **copy.deepcopy(dictAttempt),
            "listRetiredTurnRecords": listRetiredTurns,
        })
        dictRound["dictPhaseAttempt"] = None
        dictRound["sResolution"] = ""
        # The abandoned-questions rule (2.5): an interrupted attempt's
        # questions were never collected, and retirement must not
        # pretend they were handled -- re-running the phase
        # regenerates them. No gate survives a retirement.
        self.dictCampaign["dictPendingHumanGate"] = None
        self._fnRecordResearcherDecision({
            "sDecisionKind": "phaseRetried",
            "sText": (f"attempt {dictAttempt['iAttemptNumber']} of "
                      f"{dictAttempt['sPhase']} retired for re-run; "
                      "its turns are preserved in the round's retired "
                      "attempts"),
        })
        self._fnTransition(S_STATE_PLANNING, "phaseRetriedByResearcher")
        return copy.deepcopy(self.dictCampaign)

    def _fnSettleAttemptOutcome(self, dictRound, sOutcome):
        """Settle the attempt's outcome, BEFORE the call that checkpoints.

        The rule (continuation plan 2.3): mutate the attempt fields
        first, then make the transition or gate call whose checkpoint
        carries them — never checkpoint for the attempt alone between
        the two. A crash before the combined checkpoint leaves
        ``turnsSettled`` (replayable); after it, a settled attempt.
        There is no third state.
        """
        dictAttempt = (dictRound or {}).get("dictPhaseAttempt")
        if dictAttempt is None or (
                dictAttempt["sAttemptState"] == "outcomeSettled"):
            return
        dictAttempt["sAttemptState"] = "outcomeSettled"
        dictAttempt["sOutcome"] = sOutcome

    def fdictReplaySettlementFromTurnRecords(self):
        """Replay the settlement a crash interrupted (recovery, 2.4).

        Settlement is deterministic — question collection, veto
        classification and round resolution are pure functions of the
        durable turn records — so a record at ``turnsSettled`` is
        recovered by replaying it and checkpointing the result, then
        proceeding as if the crash had not happened. Anything else is
        refused: ``running`` means launched runners nobody proved
        gone, and no record at all means a pre-feature hub wrote the
        checkpoint.
        """
        dictRound = self.dictCampaign["listRounds"][-1]
        dictAttempt = dictRound.get("dictPhaseAttempt")
        if not dictAttempt or dictAttempt["sAttemptState"] != "turnsSettled":
            raise CouncilProtocolError(
                "only an attempt whose turns all settled can replay "
                "its settlement")
        sPhase = dictAttempt["sPhase"]
        if sPhase == S_PHASE_VETO:
            # Re-derived from the turn records rather than trusted:
            # classification being a pure function of durable records
            # is the property the replay claim rests on, so replay
            # exercises it instead of assuming the crashed run's
            # partial writes.
            self._fnClassifyVetoVerdicts(dictRound)
        self._fnSettlePhaseOutcome(dictRound, sPhase)
        if (sPhase == S_PHASE_VETO
                and self.dictCampaign["sState"] == S_STATE_PLANNING
                and dictAttempt["sAttemptState"] != "outcomeSettled"):
            self._fnResolveRoundTermination(dictRound)
        if dictAttempt["sAttemptState"] != "outcomeSettled":
            self._fnSettleAttemptOutcome(dictRound, "advancedToNextPhase")
        self.fnCheckpointCampaign(self.dictCampaign)
        return copy.deepcopy(self.dictCampaign)

    def _fnSettlePhaseOutcome(self, dictRound, sPhase):
        """Order matters (section 5.4): an indeterminate settle becomes
        interrupted and never masquerades as a clean human pause."""
        if self.dictCampaign["sState"] != S_STATE_PLANNING:
            return
        for dictTurnRecord in dictRound["dictTurnsByPhase"].get(sPhase, []):
            if dictTurnRecord["sCompletion"] == S_COMPLETION_INDETERMINATE:
                # The attempt settles BEFORE the transition whose
                # checkpoint carries it (2.3) — and the abandoned
                # questions stay uncollected on purpose: retirement of
                # this attempt must not pretend they were handled, and
                # re-running the phase regenerates them.
                self._fnSettleAttemptOutcome(
                    dictRound, "transitioned:interrupted")
                self._fnTransition(S_STATE_INTERRUPTED,
                                   "turnSettledIndeterminately")
                return
        if sPhase == S_PHASE_DELIBERATION_SUMMARY:
            # The closing turn of a council that never converged: the
            # gate opens HERE rather than inside the phase, so the
            # crashed-mid-settlement recovery path
            # (fdictReplaySettlementFromTurnRecords) opens it too, and
            # a hub that died between the summary turn and the gate
            # does not resume into a round with nothing left to run.
            self._fnSettleAttemptOutcome(dictRound, "gateOpened")
            self._fnOpenExhaustedGate()
            return
        if sPhase == S_PHASE_VETO:
            # The veto attempt's outcome is the ROUND's resolution,
            # written by _fnResolveRoundTermination when the walk
            # exhausts. The crash window between the two is exactly
            # what turnsSettled + deterministic replay covers.
            return
        listQuestions = self._flistCollectNeedsHumanQuestions(
            dictRound["dictTurnsByPhase"].get(sPhase, []))
        if sPhase != S_PHASE_SYNTHESIS:
            self.fnDeferQuestionsUntilSynthesis(dictRound, listQuestions)
            self._fnSettleAttemptOutcome(dictRound, "advancedToNextPhase")
            self.fnCheckpointCampaign(self.dictCampaign)
            return
        listQuestions = (
            dictRound.get("listDeferredQuestions", []) + listQuestions)
        if listQuestions:
            self._fnSettleAttemptOutcome(dictRound, "gateOpened")
            self._fnOpenQuestionGate(dictRound, sPhase, listQuestions)
            return
        self._fnSettleAttemptOutcome(dictRound, "advancedToNextPhase")
        self.fnCheckpointCampaign(self.dictCampaign)

    # ----- turn execution ------------------------------------------------

    async def _fnRunTurnsWithBarrier(self, dictRound, sPhase,
                                     listParticipants):
        """Run one phase's turns in bounded waves behind the barrier.

        No participant's result is revealed to another until the phase
        settles: every request is composed before any result lands, and
        the next phase is only composed after all turns settle.
        """
        listPlannedRequests = [
            (dictParticipant,
             flistBuildQuotedMaterial(
                 self.dictCampaign, dictRound, sPhase,
                 dictParticipant["sParticipantId"]))
            for dictParticipant in listParticipants]
        iWaveSize = self.dictCampaign["dictSettings"][
            "iMaximumConcurrentTurns"]
        iNextIndex = 0
        while iNextIndex < len(listPlannedRequests):
            if self.dictCampaign["bStopRequested"]:
                self._fnRecordUnlaunchedTurns(
                    dictRound, sPhase, listPlannedRequests[iNextIndex:])
                return
            listWave = listPlannedRequests[
                iNextIndex:iNextIndex + iWaveSize]
            listCoroutines = [
                self._fdictExecuteTurn(dictRound, dictParticipant, sPhase,
                                       listQuotedMaterial)
                for dictParticipant, listQuotedMaterial in listWave]
            await asyncio.gather(*listCoroutines)
            iNextIndex += iWaveSize

    def _fnRecordUnlaunchedTurns(self, dictRound, sPhase, listRemaining):
        for dictParticipant, _ in listRemaining:
            dictRound["dictTurnsByPhase"].setdefault(sPhase, []).append({
                "sTurnId": "",
                "sParticipantId": dictParticipant["sParticipantId"],
                "sPhase": sPhase,
                "iRoundNumber": dictRound["iRoundNumber"],
                "sStatus": "notStarted",
                "sCompletion": "",
                "bRepairAttempted": False,
                "sFailureReason": "stopRequested",
                "dictResult": None,
            })

    async def _fdictExecuteTurn(self, dictRound, dictParticipant, sPhase,
                                listQuotedMaterial):
        dictRequest = fdictComposeTurnRequest(
            self.dictCampaign, dictParticipant, sPhase,
            dictRound["iRoundNumber"], listQuotedMaterial)
        sParticipantId = dictParticipant["sParticipantId"]
        self._fnRecordParticipantRunning(sParticipantId, True)
        try:
            dictAttempt = await self._fdictDriveConnection(dictParticipant,
                                                           dictRequest)
            bRepairAttempted = False
            if dictAttempt["sOutcome"] == "invalid":
                bRepairAttempted = True
                dictRepairRequest = fdictComposeTurnRequest(
                    self.dictCampaign, dictParticipant, sPhase,
                    dictRound["iRoundNumber"], listQuotedMaterial,
                    bRepairRequest=True,
                    listSchemaProblems=dictAttempt["listProblems"])
                dictAttempt = await self._fdictDriveConnection(
                    dictParticipant, dictRepairRequest)
        finally:
            # In a finally because a turn that raises out of here still
            # is not running: an agent left marked would read as working
            # for the rest of the phase, which is the false-progress
            # claim this whole record exists to avoid.
            self._fnRecordParticipantRunning(sParticipantId, False)
        dictTurnRecord = self._fdictBuildTurnRecord(
            dictRequest, dictRound, dictParticipant, sPhase, dictAttempt,
            bRepairAttempted)
        # Requested-versus-resolved model identity, recorded
        # mechanically from what the connection extracted out of the
        # provider stream (design 13.2) — empty when the adapter
        # reported none, never an alias echoed as a resolution.
        dictTurnRecord["dictModelIdentity"] = dict(getattr(
            self.dictConnections[dictParticipant["sParticipantId"]],
            "dictModelIdentity", None) or {})
        if dictTurnRecord["sStatus"] == "failed":
            # A failed CLOSING turn retires nobody. Every other phase
            # retires the participant because the council still has
            # work for it and a broken model must not carry that work;
            # the deliberation summary is the last turn of a spent
            # council, and retiring its authors emptied the roster —
            # so the researcher's own next move, a final veto on the
            # candidate they chose to override, met a quorum shortfall
            # instead of voters. The failed TURN record stays, as it
            # always does.
            if sPhase != S_PHASE_DELIBERATION_SUMMARY:
                dictParticipant["bFailed"] = True
                dictParticipant["sFailureReason"] = (
                    dictTurnRecord["sFailureReason"])
        elif dictTurnRecord["dictResult"] is not None:
            self._fnProcessEvidenceClaims(dictTurnRecord)
        dictRound["dictTurnsByPhase"].setdefault(sPhase, []).append(
            dictTurnRecord)
        self._fnEmitEvent("turnSettled", {
            "sParticipantId": dictParticipant["sParticipantId"],
            "sPhase": sPhase, "sStatus": dictTurnRecord["sStatus"],
            "bRepairAttempted": bRepairAttempted})
        self.fnCheckpointCampaign(self.dictCampaign)
        return dictTurnRecord

    def _fdictBuildTurnRecord(self, dictRequest, dictRound, dictParticipant,
                              sPhase, dictAttempt, bRepairAttempted):
        dictTurnRecord = {
            "sTurnId": dictRequest["sTurnId"],
            "sParticipantId": dictParticipant["sParticipantId"],
            "sPhase": sPhase,
            "iRoundNumber": dictRound["iRoundNumber"],
            "sStatus": "failed",
            "sCompletion": dictAttempt.get("sCompletion", ""),
            "bRepairAttempted": bRepairAttempted,
            "sFailureReason": "",
            "dictResult": None,
        }
        if dictAttempt["sOutcome"] == "completed":
            dictTurnRecord["sStatus"] = "completed"
            dictTurnRecord["dictResult"] = dictAttempt["dictResult"]
        elif dictAttempt["sOutcome"] == "invalid":
            dictTurnRecord["sFailureReason"] = (
                "invalidStructuredResultAfterRepair: "
                + "; ".join(dictAttempt["listProblems"]))
            # WHAT the participant actually returned, not merely which
            # fields were missing. Without it a failed turn records a
            # list of absent keys and nothing else, which reads as "the
            # model formatted its answer badly" for the case that
            # matters most: a model that said nothing at all. A live
            # council failed with every field listed as missing and
            # zero input AND output tokens — the CLI never called the
            # API — and the record could not distinguish the two
            # (2026-08-24).
            dictTurnRecord["sRejectedPayload"] = dictAttempt.get(
                "sRejectedPayload", "")
        else:
            dictTurnRecord["sFailureReason"] = dictAttempt["sFailureReason"]
            dictTurnRecord["sFailureClass"] = dictAttempt.get(
                "sFailureClass", "")
            dictTurnRecord["sRejectedPayload"] = dictAttempt.get(
                "sRejectedPayload", "")
        if dictTurnRecord["sStatus"] == "failed":
            logger.warning(
                "COUNCIL turn failed in campaign %s: participant %s "
                "(model %r), phase %s, round %d: %s",
                self.dictCampaign["sCampaignId"],
                dictParticipant["sParticipantId"],
                dictParticipant.get("sRequestedModel", ""),
                sPhase, dictRound["iRoundNumber"],
                dictTurnRecord["sFailureReason"],
            )
        return dictTurnRecord

    async def _fdictDriveConnection(self, dictParticipant, dictRequest):
        connectionForTurn = self.dictConnections[
            dictParticipant["sParticipantId"]]
        try:
            await connectionForTurn.fdictPrepareImmutableContext(dictRequest)
            await connectionForTurn.fnStartTurn(dictRequest)
            async for dictEvent in (
                    connectionForTurn.fiterStreamNormalizedEvents()):
                self._fnEmitEvent("providerEvent", {
                    "sParticipantId": dictParticipant["sParticipantId"],
                    "dictProviderEvent": dictEvent})
            dictRawResult = await (
                connectionForTurn.fdictCollectStructuredResult())
            sCompletion = await connectionForTurn.fsReportCompletion()
        except Exception as error:
            return {"sOutcome": "raised",
                    "sFailureClass": "turnRaised",
                    "sFailureReason": f"turnRaised: {error}"}
        iResultBytes = len(json.dumps(dictRawResult, default=str)
                           .encode("utf-8"))
        iOutputBudget = self.dictCampaign["dictSettings"][
            "iMaximumOutputBytesPerTurn"]
        if iResultBytes > iOutputBudget:
            return {"sOutcome": "overBudget", "sCompletion": sCompletion,
                    "sFailureClass": "outputByteBudgetExceeded",
                    "sFailureReason": "outputByteBudgetExceeded"}
        # An EMPTY result is not a schema problem, and running the
        # validator over it produces the worst of both: fifteen "must
        # be an array" complaints plus a list of the diagnostic fields
        # themselves as "unknown keys", with the actual cause buried in
        # the middle. A researcher read exactly that and could not tell
        # a rate-limited model from a badly formatted one (2026-08-24).
        # Repair is skipped too: there is nothing to repair, and a
        # second turn against a provider that just throttled us is a
        # second turn thrown away.
        sEmptyReason = (dictRawResult or {}).get("sEmptyResultReason")
        if sEmptyReason:
            return {
                "sOutcome": "empty", "sCompletion": sCompletion,
                # The MACHINE class beside the prose: the retry
                # whitelist must tell a rate-limited empty turn from an
                # authentication-failed one, and the explanation text
                # buries the classifier's answer in a sentence.
                "sFailureClass": sEmptyReason,
                "sFailureReason": _fsExplainEmptyTurn(
                    sEmptyReason, dictRawResult),
                "sRejectedPayload": _fsSummarizeRejectedPayload(
                    dictRawResult),
            }
        dictValidation = fdictValidateTurnResult(
            dictRawResult,
            bRequirePatch=fbTurnRequiresPatchSchema(
                self.dictCampaign, dictRequest["sPhase"]),
            bRequireNotes=fbCharterAsksForNotedFindings(self.dictCampaign),
            bRequireSummary=fbTurnRequiresSummarySchema(
                dictRequest["sPhase"]))
        if not dictValidation["bValid"]:
            return {"sOutcome": "invalid", "sCompletion": sCompletion,
                    "listProblems": dictValidation["listProblems"],
                    "sRejectedPayload": _fsSummarizeRejectedPayload(
                        dictRawResult)}
        return {"sOutcome": "completed", "sCompletion": sCompletion,
                "dictResult": copy.deepcopy(dictRawResult)}

    # ----- synthesis and veto -------------------------------------------

    async def _fnRunSynthesisPhase(self, dictRound):
        """Chairbot synthesis with the deterministic fallback chain
        (section 6.3.1): the next configured participant takes the pen,
        and the substitution is recorded — never a silent stall."""
        sChairbotId = self.dictCampaign["sChairbotParticipantId"]
        listAuthorOrder = sorted(
            self._flistActiveParticipants(),
            key=lambda dictParticipant:
                dictParticipant["sParticipantId"] != sChairbotId)
        for dictParticipant in listAuthorOrder:
            listQuotedMaterial = flistBuildQuotedMaterial(
                self.dictCampaign, dictRound, S_PHASE_SYNTHESIS,
                dictParticipant["sParticipantId"])
            dictTurnRecord = await self._fdictExecuteTurn(
                dictRound, dictParticipant, S_PHASE_SYNTHESIS,
                listQuotedMaterial)
            if dictTurnRecord["sStatus"] == "completed":
                bSubstituted = (dictParticipant["sParticipantId"]
                                != sChairbotId)
                if bSubstituted:
                    self._fnEmitEvent("chairbotSubstituted", {
                        "sConfiguredChairbotId": sChairbotId,
                        "sSubstituteAuthorId":
                            dictParticipant["sParticipantId"]})
                dictRound["sSynthesisAuthorId"] = (
                    dictParticipant["sParticipantId"])
                dictRound["bChairbotSubstituted"] = bSubstituted
                dictRound["bSynthesisSettled"] = True
                self._fnAdoptCandidatePlan(dictRound, dictTurnRecord)
                self._fnFreezeRequiredVoters(dictRound)
                return
        dictRound["bSynthesisSettled"] = True
        dictRound["sResolution"] = "synthesisFailed"
        self._fnMarkAttemptTurnsSettled(dictRound)
        self._fnSettleAttemptOutcome(dictRound, "transitioned:failed")
        self._fnTransition(S_STATE_FAILED, "noParticipantCouldSynthesize")

    async def _fnRunDeliberationSummaryPhase(self, dictRound):
        """The chairbot's closing turn on a council that never converged.

        Same pen and same fallback chain as synthesis, and deliberately
        NOT stored in ``dictCandidatePlan``: what it writes is a
        deliberation summary, and a record that filed it as a candidate
        plan would make every reader downstream — the Plan tab, the
        plan.md composer, the veto quotes — present a consensus that
        does not exist.

        A summary that cannot be written is recorded and the walk goes
        on: the researcher's exits out of an exhausted council must not
        depend on one more provider turn succeeding, which is exactly
        the turn most likely to fail on a council that has already
        spent its budget.
        """
        sChairbotId = self.dictCampaign["sChairbotParticipantId"]
        listAuthorOrder = sorted(
            self._flistActiveParticipants(),
            key=lambda dictParticipant:
                dictParticipant["sParticipantId"] != sChairbotId)
        for dictParticipant in listAuthorOrder:
            listQuotedMaterial = flistBuildQuotedMaterial(
                self.dictCampaign, dictRound, S_PHASE_DELIBERATION_SUMMARY,
                dictParticipant["sParticipantId"])
            dictTurnRecord = await self._fdictExecuteTurn(
                dictRound, dictParticipant, S_PHASE_DELIBERATION_SUMMARY,
                listQuotedMaterial)
            if dictTurnRecord["sStatus"] != "completed":
                continue
            bSubstituted = dictParticipant["sParticipantId"] != sChairbotId
            if bSubstituted:
                self._fnEmitEvent("chairbotSubstituted", {
                    "sConfiguredChairbotId": sChairbotId,
                    "sSubstituteAuthorId":
                        dictParticipant["sParticipantId"]})
            dictRound["sSynthesisAuthorId"] = (
                dictParticipant["sParticipantId"])
            dictRound["bChairbotSubstituted"] = bSubstituted
            self.dictCampaign["dictDeliberationSummary"] = {
                "iRoundNumber": dictRound["iRoundNumber"],
                "sAuthorParticipantId": dictParticipant["sParticipantId"],
                "bChairbotSubstituted": bSubstituted,
                "dictResult": copy.deepcopy(dictTurnRecord["dictResult"]),
            }
            dictRound["sResolution"] = "deliberationSummarised"
            self._fnEmitEvent("deliberationSummaryWritten", {
                "iRoundNumber": dictRound["iRoundNumber"],
                "sAuthorParticipantId": dictParticipant["sParticipantId"]})
            return
        dictRound["sResolution"] = "deliberationSummaryUnavailable"
        self._fnEmitEvent("deliberationSummaryUnavailable", {
            "iRoundNumber": dictRound["iRoundNumber"]})

    async def _fnRunImplementationPhase(self, dictRound):
        """Single-author patch production with the synthesis fallback
        chain: the chairbot holds the pen; on its failure the next
        configured participant takes it, recorded, never a silent
        stall. The completed patch becomes the candidate the
        conformance review quotes. bSynthesisSettled stays False —
        the round still revises the patch after review."""
        sChairbotId = self.dictCampaign["sChairbotParticipantId"]
        listAuthorOrder = sorted(
            self._flistActiveParticipants(),
            key=lambda dictParticipant:
                dictParticipant["sParticipantId"] != sChairbotId)
        for dictParticipant in listAuthorOrder:
            listQuotedMaterial = flistBuildQuotedMaterial(
                self.dictCampaign, dictRound, S_PHASE_IMPLEMENTATION,
                dictParticipant["sParticipantId"])
            dictTurnRecord = await self._fdictExecuteTurn(
                dictRound, dictParticipant, S_PHASE_IMPLEMENTATION,
                listQuotedMaterial)
            if dictTurnRecord["sStatus"] == "completed":
                bSubstituted = (dictParticipant["sParticipantId"]
                                != sChairbotId)
                if bSubstituted:
                    self._fnEmitEvent("chairbotSubstituted", {
                        "sConfiguredChairbotId": sChairbotId,
                        "sSubstituteAuthorId":
                            dictParticipant["sParticipantId"]})
                dictRound["sSynthesisAuthorId"] = (
                    dictParticipant["sParticipantId"])
                dictRound["bChairbotSubstituted"] = bSubstituted
                self._fnAdoptCandidatePlan(dictRound, dictTurnRecord)
                return
        dictRound["sResolution"] = "implementationFailed"
        self._fnMarkAttemptTurnsSettled(dictRound)
        self._fnSettleAttemptOutcome(dictRound, "transitioned:failed")
        self._fnTransition(S_STATE_FAILED, "noParticipantCouldImplement")

    def _fnAdoptCandidatePlan(self, dictRound, dictTurnRecord):
        dictPrevious = self.dictCampaign["dictCandidatePlan"] or {}
        self.dictCampaign["dictCandidatePlan"] = {
            "iRoundNumber": dictRound["iRoundNumber"],
            "sSynthesisAuthorId": dictRound["sSynthesisAuthorId"],
            "bChairbotSubstituted": dictRound["bChairbotSubstituted"],
            "dictResult": copy.deepcopy(dictTurnRecord["dictResult"]),
            "listCouncilClearedObjections": list(
                dictPrevious.get("listCouncilClearedObjections", [])),
            "listResearcherOverriddenObjections": list(
                dictPrevious.get("listResearcherOverriddenObjections", [])),
            "listResearcherResolvedObjections": list(
                dictPrevious.get("listResearcherResolvedObjections", [])),
        }

    def _fnFreezeRequiredVoters(self, dictRound):
        """Freeze the required voter set when synthesis settles
        (section 5.1): every surviving participant that completed a
        substantive role this round, minus the synthesis author. A
        frozen voter that then vanishes is undetermined, never dropped."""
        setSubstantiveIds = set()
        for sPhase in (S_PHASE_PROPOSAL, S_PHASE_CROSS_REVIEW,
                       S_PHASE_IMPLEMENTATION, S_PHASE_CONFORMANCE_REVIEW):
            for dictTurnRecord in dictRound["dictTurnsByPhase"].get(
                    sPhase, []):
                if dictTurnRecord["sStatus"] == "completed":
                    setSubstantiveIds.add(dictTurnRecord["sParticipantId"])
        dictRound["listFrozenVoterIds"] = [
            dictParticipant["sParticipantId"]
            for dictParticipant in self.dictCampaign["listParticipants"]
            if dictParticipant["sParticipantId"] in setSubstantiveIds
            and not dictParticipant["bFailed"]
            and dictParticipant["sParticipantId"]
            != dictRound["sSynthesisAuthorId"]]
        self._fnEmitEvent("requiredVotersFrozen", {
            "iRoundNumber": dictRound["iRoundNumber"],
            "listFrozenVoterIds": list(dictRound["listFrozenVoterIds"])})

    async def _fnRunVetoPhase(self, dictRound):
        listVoters = [self._fdictFindParticipant(sVoterId)
                      for sVoterId in dictRound["listFrozenVoterIds"] or []]
        await self._fnRunTurnsWithBarrier(dictRound, S_PHASE_VETO,
                                          listVoters)
        self._fnClassifyVetoVerdicts(dictRound)

    def _fnClassifyVetoVerdicts(self, dictRound):
        """Classify every frozen voter's verdict from the turn records.

        A pure function of durable records — the property the
        settlement-replay claim rests on, exercised by both the live
        veto run and the recovery replay so it can never silently
        become accidental.
        """
        dictRecordByVoter = {
            dictTurnRecord["sParticipantId"]: dictTurnRecord
            for dictTurnRecord in
            dictRound["dictTurnsByPhase"].get(S_PHASE_VETO, [])}
        for sVoterId in dictRound["listFrozenVoterIds"] or []:
            dictTurnRecord = dictRecordByVoter.get(sVoterId)
            dictRound["dictVetoVerdicts"][sVoterId] = (
                self._fdictClassifyVeto(dictTurnRecord))

    def _fdictClassifyVeto(self, dictTurnRecord):
        if dictTurnRecord is None or (
                dictTurnRecord["sStatus"] != "completed"):
            return {"sVerdict": S_VERDICT_UNDETERMINED,
                    "sReason": "vetoTurnMissingOrFailed"}
        sVerdict = dictTurnRecord["dictResult"]["sVerdict"]
        if sVerdict not in SET_RECOGNIZED_VETO_VERDICTS:
            return {"sVerdict": S_VERDICT_UNDETERMINED,
                    "sReason": f"unrecognizedVerdict: {sVerdict}"}
        return {"sVerdict": sVerdict, "sReason": ""}

    # ----- researcher actions -------------------------------------------

    def _fdictRequireGate(self, sExpectedGateKind=""):
        if self.dictCampaign["sState"] != S_STATE_NEEDS_HUMAN:
            raise CouncilProtocolError(
                "the campaign is not waiting on the researcher")
        dictGate = self.dictCampaign["dictPendingHumanGate"]
        if sExpectedGateKind and dictGate["sGateKind"] != sExpectedGateKind:
            raise CouncilProtocolError(
                f"this action answers a {sExpectedGateKind} gate, not "
                f"{dictGate['sGateKind']}")
        return dictGate

    def _fnRecordResearcherDecision(self, dictDecision):
        self.dictCampaign["listResearcherDecisions"].append(dictDecision)
        self._fnEmitEvent("researcherDecisionRecorded", dict(dictDecision))

    async def fdictContinueAfterResearcherResponse(
            self, sResponseText, listDecisionAnswers=None):
        """Answer a blocking question and launch the continuation.

        Refused at an exhausted-round gate: a plain response never
        silently relaunches the spent budget (section 5.1)."""
        dictGate = self._fdictRequireGate()
        if dictGate["sGateKind"] == S_GATE_EXHAUSTED_ROUNDS:
            raise CouncilProtocolError(
                "the round budget is exhausted; choose one of the three "
                "exits — a plain response does not restart the loop")
        listDecisionAnswers = list(listDecisionAnswers or [])
        if listDecisionAnswers:
            # The prose is COMPOSED from the per-decision answers rather
            # than taken from the caller, so the readable record and the
            # machine-readable one cannot describe different answers.
            sResponseText = fsComposeDecisionAnswers(
                listDecisionAnswers, dictGate.get("listQuestions", []))
        self._fnRecordResearcherDecision({
            "sDecisionKind": "researcherResponse", "sText": sResponseText})
        # Capture the questions this answers BEFORE the gate is
        # discarded on the next line. Without them the answer reaches the
        # next round as unattached prose: an agent is handed "prefer the
        # conservative option" with no record of what was asked, and one
        # text box may be answering a dozen questions at once. The
        # researcher typed a single reply, so this records the questions
        # it responded to — never a per-question mapping the UI did not
        # collect.
        self.dictCampaign["listResearcherResponses"].append({
            "sText": sResponseText,
            "listAnsweredQuestions": copy.deepcopy(
                dictGate.get("listQuestions", [])),
            "listDecisionAnswers": copy.deepcopy(listDecisionAnswers),
        })
        self.dictCampaign["dictPendingHumanGate"] = None
        self._fnTransition(S_STATE_PLANNING, "researcherResponded")
        return await self.fdictRunUntilBlocked()

    async def fdictGrantResolutionRound(self, iGrantedRounds):
        """Exhausted-round exit 1: a fresh, explicitly-sized budget."""
        self._fdictRequireGate(S_GATE_EXHAUSTED_ROUNDS)
        if iGrantedRounds < 1:
            raise CouncilProtocolError(
                "a resolution round grant must be at least one round")
        self.dictCampaign["iGrantedAdditionalRounds"] += iGrantedRounds
        self._fnRecordResearcherDecision({
            "sDecisionKind": "resolutionRoundGranted",
            "iGrantedRounds": iGrantedRounds})
        self.dictCampaign["dictPendingHumanGate"] = None
        self._fnTransition(S_STATE_PLANNING, "resolutionRoundGranted")
        return await self.fdictRunUntilBlocked()

    async def fdictResolveObjectionsAndRequestFinalVeto(
            self, dictDispositionByObjectionId):
        """Exhausted-round exit 2: record a decision on every named
        objection, then one final veto on the candidate as amended. An
        override stays a researcher decision — it is never laundered
        into a council accept."""
        dictGate = self._fdictRequireGate(S_GATE_EXHAUSTED_ROUNDS)
        self._fnApplyObjectionDispositions(dictGate,
                                           dictDispositionByObjectionId)
        self.dictCampaign["dictPendingHumanGate"] = None
        self._fnTransition(S_STATE_PLANNING,
                           "objectionsAddressedFinalVetoRequested")
        self._fnOpenFinalVetoRound()
        return await self.fdictRunUntilBlocked()

    def _fnApplyObjectionDispositions(self, dictGate,
                                      dictDispositionByObjectionId):
        dictCandidatePlan = self.dictCampaign["dictCandidatePlan"]
        for dictObjection in dictGate["listUnresolvedObjections"]:
            sObjectionId = dictObjection["sObjectionId"]
            dictDisposition = dictDispositionByObjectionId.get(sObjectionId)
            if dictDisposition is None or dictDisposition.get(
                    "sAction") not in ("resolve", "override"):
                raise CouncilProtocolError(
                    f"objection {sObjectionId} needs a disposition of "
                    "'resolve' or 'override'")
            sAction = dictDisposition["sAction"]
            self._fnRecordResearcherDecision({
                "sDecisionKind": f"objection{sAction.capitalize()}",
                "sObjectionId": sObjectionId,
                "sObjectionText": dictObjection["sObjectionText"],
                "sText": dictDisposition.get("sText", "")})
            sProvenanceKey = ("listResearcherOverriddenObjections"
                              if sAction == "override"
                              else "listResearcherResolvedObjections")
            dictCandidatePlan[sProvenanceKey].append({
                "sObjectionId": sObjectionId,
                "sObjectionText": dictObjection["sObjectionText"],
                "sResearcherText": dictDisposition.get("sText", "")})

    def _fnOpenFinalVetoRound(self):
        listPriorRounds = [dictRound
                           for dictRound in self.dictCampaign["listRounds"]
                           if dictRound["listFrozenVoterIds"] is not None]
        dictLastFrozenRound = listPriorRounds[-1]
        self.dictCampaign["listRounds"].append({
            "iRoundNumber": len(self.dictCampaign["listRounds"]) + 1,
            "bFinalVetoRound": True,
            "dictTurnsByPhase": {},
            "bSynthesisSettled": True,
            "sSynthesisAuthorId": dictLastFrozenRound["sSynthesisAuthorId"],
            "bChairbotSubstituted":
                dictLastFrozenRound["bChairbotSubstituted"],
            "listFrozenVoterIds": list(
                dictLastFrozenRound["listFrozenVoterIds"]),
            "dictVetoVerdicts": {},
            "listUnresolvedObjections": [],
            "dictPhaseAttempt": None,
            "listRetiredAttempts": [],
            "sResolution": "",
        })
        self._fnEmitEvent("finalVetoRoundOpened", {
            "iRoundNumber": len(self.dictCampaign["listRounds"])})

    def fdictRejectCandidate(self, sReasonText=""):
        """Exhausted-round exit 3, also offered at planReady: end the
        campaign with no accepted plan."""
        if self.dictCampaign["sState"] not in (S_STATE_NEEDS_HUMAN,
                                               S_STATE_PLAN_READY):
            raise CouncilProtocolError(
                "there is no candidate to reject or archive")
        self._fnRecordResearcherDecision({
            "sDecisionKind": "candidateRejected", "sText": sReasonText})
        self.dictCampaign["dictPendingHumanGate"] = None
        self._fnTransition(S_STATE_ARCHIVED, "candidateRejectedByResearcher")
        return copy.deepcopy(self.dictCampaign)

    def fdictAcceptPlan(self):
        """Researcher acceptance: planAccepted, then
        awaitingImplementation (section 6.6). Only reachable from
        planReady — a council never accepts its own plan."""
        if self.dictCampaign["sState"] != S_STATE_PLAN_READY:
            raise CouncilProtocolError(
                "only a planReady campaign can be accepted")
        self._fnRecordResearcherDecision({"sDecisionKind": "planAccepted"})
        self._fnTransition(S_STATE_PLAN_ACCEPTED, "researcherAcceptedPlan")
        self._fnTransition(S_STATE_AWAITING_IMPLEMENTATION,
                           "acceptedPlanRecorded")
        return copy.deepcopy(self.dictCampaign)

    def fnRequestStopAfterCurrentTurn(self):
        """Admit no later provider turns (section 9.4). Turns already
        dispatched settle normally; nothing new is launched."""
        self.dictCampaign["bStopRequested"] = True
        self._fnEmitEvent("stopRequested", {})

    def fnRequestPauseAfterCurrentPhase(self):
        """Stand down at the next phase boundary, still resumable.

        Deliberately coarser than the stop it sits beside: a stop
        admits no further TURN and may leave a phase half-run, which is
        acceptable for a campaign about to be archived and wrong for
        one meant to continue. The pause is read only where the walk
        proves a whole phase settled, so what the researcher comes back
        to is a coherent record rather than a phase with unlaunched
        participants in it.
        """
        self.dictCampaign["bPauseRequested"] = True
        self._fnEmitEvent("pauseRequested", {})
