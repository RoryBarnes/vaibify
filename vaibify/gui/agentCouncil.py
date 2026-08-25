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
from .agentCouncilEvidence import EvidenceDisciplineMixin
from .agentCouncilResolution import RoundResolutionMixin

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
    "noResultEvent":
        "the assistant stopped without returning an answer. Nothing in "
        "the stream reported an error, so the cause is outside what the "
        "turn can see.",
    "resultEventCarriedNoText":
        "the assistant finished but its answer was empty.",
}


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
    return "emptyTurn: " + DICT_EMPTY_TURN_EXPLANATIONS.get(
        sEmptyReason, f"the turn returned nothing ({sEmptyReason}).",
    ) + sProgress


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
            dictRound = self._fdictEnsureOpenRound()
            if dictRound is None:
                continue
            sPhase = self._fsNextPhaseForRound(dictRound)
            if sPhase is None:
                self._fnResolveRoundTermination(dictRound)
                continue
            await self._fnRunPhase(dictRound, sPhase)
            self._fnSettlePhaseOutcome(dictRound, sPhase)
        return copy.deepcopy(self.dictCampaign)

    def _fdictEnsureOpenRound(self):
        listRounds = self.dictCampaign["listRounds"]
        if listRounds and not listRounds[-1]["sResolution"]:
            return listRounds[-1]
        iOpenedRounds = len([dictRound for dictRound in listRounds
                             if not dictRound["bFinalVetoRound"]])
        if listRounds and iOpenedRounds >= self._fiRoundBudget():
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
            "sResolution": "",
        }
        listRounds.append(dictRound)
        self._fnEmitEvent("roundOpened",
                          {"iRoundNumber": dictRound["iRoundNumber"]})
        return dictRound

    def _fsNextPhaseForRound(self, dictRound):
        if dictRound["bFinalVetoRound"]:
            listPhaseOrder = [S_PHASE_VETO]
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
        self._fnRecordPhaseInFlight(dictRound, sPhase)
        if sPhase == S_PHASE_SYNTHESIS:
            await self._fnRunSynthesisPhase(dictRound)
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

    def _fnSettlePhaseOutcome(self, dictRound, sPhase):
        """Order matters (section 5.4): an indeterminate settle becomes
        interrupted and never masquerades as a clean human pause."""
        if self.dictCampaign["sState"] != S_STATE_PLANNING:
            return
        for dictTurnRecord in dictRound["dictTurnsByPhase"].get(sPhase, []):
            if dictTurnRecord["sCompletion"] == S_COMPLETION_INDETERMINATE:
                self._fnTransition(S_STATE_INTERRUPTED,
                                   "turnSettledIndeterminately")
                return
        if sPhase == S_PHASE_VETO:
            return
        listQuestions = self._flistCollectNeedsHumanQuestions(
            dictRound["dictTurnsByPhase"].get(sPhase, []))
        if sPhase != S_PHASE_SYNTHESIS:
            self.fnDeferQuestionsUntilSynthesis(dictRound, listQuestions)
            return
        listQuestions = (
            dictRound.get("listDeferredQuestions", []) + listQuestions)
        if listQuestions:
            self._fnOpenQuestionGate(dictRound, sPhase, listQuestions)

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
            dictTurnRecord["sRejectedPayload"] = dictAttempt.get(
                "sRejectedPayload", "")
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
                    "sFailureReason": f"turnRaised: {error}"}
        iResultBytes = len(json.dumps(dictRawResult, default=str)
                           .encode("utf-8"))
        iOutputBudget = self.dictCampaign["dictSettings"][
            "iMaximumOutputBytesPerTurn"]
        if iResultBytes > iOutputBudget:
            return {"sOutcome": "overBudget", "sCompletion": sCompletion,
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
                "sFailureReason": _fsExplainEmptyTurn(
                    sEmptyReason, dictRawResult),
                "sRejectedPayload": _fsSummarizeRejectedPayload(
                    dictRawResult),
            }
        dictValidation = fdictValidateTurnResult(dictRawResult)
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
        self._fnTransition(S_STATE_FAILED, "noParticipantCouldSynthesize")

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
        for sPhase in (S_PHASE_PROPOSAL, S_PHASE_CROSS_REVIEW):
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
