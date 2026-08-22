"""Round termination, the quorum floor, and the human gates.

Phase 1 of the Agent Council (design/agentCouncil.md section 5.1 step 5
and section 5.4). This mixin decides a settled round's fate — the
epistemic core of the protocol — and is deliberately separated from the
orchestration that runs the phases, because the termination rules change
for governance reasons (what ``planReady`` requires, the two-distinct-
models quorum floor, the three exhausted-round exits) on a different
cadence than the barrier-and-wave machinery that produces the turns.

The rules it enforces: ``planReady`` only when every frozen required
veto returned ``accept``; a missing, failed or unrecognizable veto is
``undetermined`` — not acceptance and not absence of objection; fewer
than two distinct surviving models is a quorum shortfall, never a ready
plan; and an exhausted round budget with anything unresolved opens the
``needsHuman`` gate presenting exactly three exits, never an ambiguous
ready-with-objections state.

The mixin reads ``self.dictCampaign`` and calls the engine's
``_fnEmitEvent`` and ``_fnTransition`` (which also checkpoints), plus its
own sibling methods here — all resolved on the concrete engine through
the method-resolution order.
"""

import copy

from .agentCouncilCampaign import (
    LIST_EXHAUSTED_ROUND_EXITS,
    S_GATE_BLOCKING_QUESTION,
    S_GATE_EXHAUSTED_ROUNDS,
    S_GATE_QUORUM_SHORTFALL,
    S_STATE_FAILED,
    S_STATE_NEEDS_HUMAN,
    S_STATE_PLAN_READY,
    S_VERDICT_ACCEPT,
    S_VERDICT_BLOCKING_OBJECTION,
    S_VERDICT_NEEDS_HUMAN,
    S_VERDICT_UNDETERMINED,
)
from .agentCouncilCharter import (
    S_PHASE_CROSS_REVIEW,
    S_PHASE_PROPOSAL,
    S_PHASE_VETO,
)

__all__ = ["RoundResolutionMixin"]


class RoundResolutionMixin:
    """Resolve a round from its frozen veto set under the quorum floor."""

    def _fiCountSurvivingQuorumModels(self):
        """Distinct (provider, model) pairs among SURVIVING participants
        that completed a proposal and a review or veto this campaign."""
        dictPhasesByParticipant = {}
        for dictRound in self.dictCampaign["listRounds"]:
            for sPhase, listTurnRecords in (
                    dictRound["dictTurnsByPhase"].items()):
                for dictTurnRecord in listTurnRecords:
                    if dictTurnRecord["sStatus"] == "completed":
                        dictPhasesByParticipant.setdefault(
                            dictTurnRecord["sParticipantId"],
                            set()).add(sPhase)
        setQualifyingModels = set()
        for dictParticipant in self._flistActiveParticipants():
            setPhases = dictPhasesByParticipant.get(
                dictParticipant["sParticipantId"], set())
            if S_PHASE_PROPOSAL in setPhases and (
                    S_PHASE_CROSS_REVIEW in setPhases
                    or S_PHASE_VETO in setPhases):
                setQualifyingModels.add((dictParticipant["sProvider"],
                                         dictParticipant["sRequestedModel"]))
        return len(setQualifyingModels)

    def _fbAnyCompletedTurnExists(self):
        for dictRound in self.dictCampaign["listRounds"]:
            for listTurnRecords in dictRound["dictTurnsByPhase"].values():
                for dictTurnRecord in listTurnRecords:
                    if dictTurnRecord["sStatus"] == "completed":
                        return True
        return False

    def _fnResolveRoundTermination(self, dictRound):
        dictVerdicts = dictRound["dictVetoVerdicts"]
        if (self._fiCountSurvivingQuorumModels() < 2
                or not dictVerdicts):
            self._fnResolveQuorumShortfall(dictRound)
            return
        listNeedsHumanIds = [
            sVoterId for sVoterId, dictVerdict in dictVerdicts.items()
            if dictVerdict["sVerdict"] == S_VERDICT_NEEDS_HUMAN]
        if listNeedsHumanIds:
            dictRound["sResolution"] = "needsHuman"
            self._fnOpenQuestionGate(
                dictRound, S_PHASE_VETO,
                self._flistCollectNeedsHumanQuestions(
                    dictRound["dictTurnsByPhase"].get(S_PHASE_VETO, [])))
            return
        if all(dictVerdict["sVerdict"] == S_VERDICT_ACCEPT
               for dictVerdict in dictVerdicts.values()):
            self._fnResolveAllAccepted(dictRound)
            return
        dictRound["listUnresolvedObjections"] = (
            self._flistCollectUnresolvedObjections(dictRound))
        dictRound["sResolution"] = "objectionsOutstanding"
        if dictRound["bFinalVetoRound"]:
            self._fnOpenExhaustedGate()

    def _fnResolveQuorumShortfall(self, dictRound):
        dictRound["sResolution"] = "quorumShortfall"
        if not self._fbAnyCompletedTurnExists():
            self._fnTransition(S_STATE_FAILED, "noSubstantiveWorkSurvived")
            return
        self.dictCampaign["dictPendingHumanGate"] = {
            "sGateKind": S_GATE_QUORUM_SHORTFALL,
            "iRoundNumber": dictRound["iRoundNumber"],
            "sOriginPhase": S_PHASE_VETO,
            "listQuestions": [{
                "sQuestionText": (
                    "fewer than two distinct models completed substantive "
                    "roles; a legitimate council result needs two"),
                "sRaisedByParticipantId": "server"}],
        }
        self._fnEmitEvent("humanGateOpened",
                          {"sGateKind": S_GATE_QUORUM_SHORTFALL})
        self._fnTransition(S_STATE_NEEDS_HUMAN, "quorumShortfall")

    def _fnResolveAllAccepted(self, dictRound):
        iCompletedRounds = len([
            dictClosedRound
            for dictClosedRound in self.dictCampaign["listRounds"]
            if not dictClosedRound["bFinalVetoRound"]])
        iMinimumRounds = self.dictCampaign["dictSettings"]["iMinimumRounds"]
        if (not dictRound["bFinalVetoRound"]
                and iCompletedRounds < iMinimumRounds):
            dictRound["sResolution"] = "minimumRoundsFloor"
            self._fnEmitEvent("minimumRoundsFloorHeld", {
                "iCompletedRounds": iCompletedRounds,
                "iMinimumRounds": iMinimumRounds})
            return
        dictRound["sResolution"] = "planReady"
        self.dictCampaign["dictCandidatePlan"][
            "listCouncilClearedObjections"] = (
            self._flistHistoricalObjectionTexts())
        self._fnTransition(S_STATE_PLAN_READY, "everyRequiredVetoAccepted")

    def _flistHistoricalObjectionTexts(self):
        listCleared = []
        setOverriddenIds = {
            dictObjection["sObjectionId"] for dictObjection in
            (self.dictCampaign["dictCandidatePlan"] or {}).get(
                "listResearcherOverriddenObjections", [])}
        for dictRound in self.dictCampaign["listRounds"]:
            for dictObjection in dictRound["listUnresolvedObjections"]:
                if dictObjection["sObjectionId"] not in setOverriddenIds:
                    listCleared.append(dict(dictObjection))
        return listCleared

    def _flistCollectUnresolvedObjections(self, dictRound):
        listUnresolved = []
        dictRecordByVoter = {
            dictTurnRecord["sParticipantId"]: dictTurnRecord
            for dictTurnRecord in
            dictRound["dictTurnsByPhase"].get(S_PHASE_VETO, [])}
        for sVoterId, dictVerdict in dictRound["dictVetoVerdicts"].items():
            if dictVerdict["sVerdict"] == S_VERDICT_BLOCKING_OBJECTION:
                dictTurnRecord = dictRecordByVoter.get(sVoterId)
                listTexts = (dictTurnRecord["dictResult"]
                             ["listBlockingObjections"]
                             if dictTurnRecord else [])
                for sObjectionText in listTexts or [
                        "blocking objection without stated text"]:
                    listUnresolved.append(self._fdictMintObjection(
                        str(sObjectionText), sVoterId))
            elif dictVerdict["sVerdict"] == S_VERDICT_UNDETERMINED:
                listUnresolved.append(self._fdictMintObjection(
                    "required veto undetermined "
                    f"({dictVerdict['sReason']}) — not acceptance and "
                    "not absence of objection", sVoterId))
        return listUnresolved

    def _fdictMintObjection(self, sObjectionText, sRaisedByParticipantId):
        self.dictCampaign["iObjectionCounter"] += 1
        return {
            "sObjectionId":
                f"objection-{self.dictCampaign['iObjectionCounter']}",
            "sObjectionText": sObjectionText,
            "sRaisedByParticipantId": sRaisedByParticipantId,
        }

    def _flistCollectNeedsHumanQuestions(self, listTurnRecords):
        listQuestions = []
        for dictTurnRecord in listTurnRecords:
            if dictTurnRecord["sStatus"] != "completed":
                continue
            if dictTurnRecord["dictResult"]["sVerdict"] != (
                    S_VERDICT_NEEDS_HUMAN):
                continue
            listOpenQuestions = (
                dictTurnRecord["dictResult"]["listOpenQuestions"]
                or ["blocking question without stated text"])
            for sQuestionText in listOpenQuestions:
                listQuestions.append({
                    "sQuestionText": str(sQuestionText),
                    "sRaisedByParticipantId":
                        dictTurnRecord["sParticipantId"]})
        return listQuestions

    def _fnOpenQuestionGate(self, dictRound, sOriginPhase, listQuestions):
        """Enter needsHuman (section 5.4): every turn in the phase has
        already settled terminally before this is reached."""
        self.dictCampaign["dictPendingHumanGate"] = {
            "sGateKind": S_GATE_BLOCKING_QUESTION,
            "iRoundNumber": dictRound["iRoundNumber"],
            "sOriginPhase": sOriginPhase,
            "listQuestions": listQuestions,
        }
        self._fnEmitEvent("humanGateOpened",
                          {"sGateKind": S_GATE_BLOCKING_QUESTION})
        self._fnTransition(S_STATE_NEEDS_HUMAN, "blockingQuestion")

    def _fnOpenExhaustedGate(self):
        """Exhausted round budget with unresolved objections: present
        the candidate and exactly three exits — never an ambiguous
        ready-with-objections state, never a silent relaunch."""
        listUnresolved = []
        for dictRound in reversed(self.dictCampaign["listRounds"]):
            if dictRound["listUnresolvedObjections"]:
                listUnresolved = copy.deepcopy(
                    dictRound["listUnresolvedObjections"])
                break
        self.dictCampaign["dictPendingHumanGate"] = {
            "sGateKind": S_GATE_EXHAUSTED_ROUNDS,
            "iRoundNumber": len(self.dictCampaign["listRounds"]),
            "sOriginPhase": S_PHASE_VETO,
            "listUnresolvedObjections": listUnresolved,
            "listExitActions": list(LIST_EXHAUSTED_ROUND_EXITS),
        }
        self._fnEmitEvent("humanGateOpened",
                          {"sGateKind": S_GATE_EXHAUSTED_ROUNDS})
        self._fnTransition(S_STATE_NEEDS_HUMAN, "roundBudgetExhausted")
