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
    S_STATE_PLANNING,
    S_STATE_PLAN_READY,
    S_VERDICT_ACCEPT,
    S_VERDICT_BLOCKING_OBJECTION,
    S_VERDICT_NEEDS_HUMAN,
    S_VERDICT_UNDETERMINED,
)
from .agentCouncilCharter import (
    S_PHASE_CROSS_REVIEW,
    S_PHASE_PROPOSAL,
    S_PHASE_SYNTHESIS,
    S_PHASE_VETO,
    _fsMintIdentifier,
)

__all__ = [
    "RoundResolutionMixin",
    "TUPLE_DECISION_TIER_ORDER",
    "fdictDescribeActivePhase",
    "flistDescribeHeldQuestions",
    "flistGroupGateQuestionsIntoDecisions",
]

S_TIER_ALL = "raisedByAll"
S_TIER_SEVERAL = "raisedBySeveral"
S_TIER_ONE = "raisedByOne"
S_TIER_SYNTHESIS = "raisedDuringSynthesis"
# Most-shared first: a question every agent independently raised is the
# one whose answer unblocks the most work, and the pen-holder's own
# questions come last because it wrote the plan the others are about.
TUPLE_DECISION_TIER_ORDER = (
    S_TIER_ALL, S_TIER_SEVERAL, S_TIER_ONE, S_TIER_SYNTHESIS)


def _fdictMapQuestionIdToPlanItems(listQuestions, listPlanItems):
    """Return {question id: [plan item index]} by scanning item text.

    The anchor exists only as an id the pen-holder wrote into prose, so
    finding it is a text scan — the same shape as a cross-step reference
    hidden in a script literal. A question the scan cannot place keeps an
    EMPTY list and is presented as unplaced; it is never dropped, because
    a question silently missing from the gate is a question the
    researcher was never asked.
    """
    dictByQuestionId = {}
    for dictQuestion in listQuestions:
        sQuestionId = dictQuestion["sQuestionId"]
        dictByQuestionId[sQuestionId] = [
            iIndex for iIndex, jsonItem in enumerate(listPlanItems)
            if sQuestionId in str(jsonItem)]
    return dictByQuestionId


def _flistFindCoAnchoredGroups(listQuestions, dictItemsByQuestionId):
    """Group questions that any one plan item anchors together.

    Two questions the pen-holder placed on the same item are one
    decision — that is the judgement it already made by placing them,
    and the reason the gate must not ask them twice. Grouping is
    transitive: items {q1,q2} and {q2,q3} make one decision of three.
    """
    dictGroupIndexByQuestionId = {}
    listGroups = []
    for dictQuestion in listQuestions:
        dictGroupIndexByQuestionId[dictQuestion["sQuestionId"]] = len(
            listGroups)
        listGroups.append([dictQuestion])
    setAnchoringItems = {iItem
                         for listItems in dictItemsByQuestionId.values()
                         for iItem in listItems}
    for iItemIndex in sorted(setAnchoringItems):
        listShared = [sQuestionId
                      for sQuestionId, listItems
                      in dictItemsByQuestionId.items()
                      if iItemIndex in listItems]
        for sOtherId in listShared[1:]:
            iInto = dictGroupIndexByQuestionId[listShared[0]]
            iFrom = dictGroupIndexByQuestionId[sOtherId]
            if iInto == iFrom:
                continue
            listGroups[iInto].extend(listGroups[iFrom])
            for dictMoved in listGroups[iFrom]:
                dictGroupIndexByQuestionId[dictMoved["sQuestionId"]] = iInto
            listGroups[iFrom] = []
    return [listGroup for listGroup in listGroups if listGroup]


def _fsetFindSynthesisQuestionTexts(dictCampaign, iRoundNumber):
    """Return the question texts the pen-holder raised writing the plan.

    Derived from the round's own turn records rather than stamped on the
    question when it is minted, so it reads correctly for campaigns
    already checkpointed by an earlier hub — including one sitting at a
    gate right now. "Raised by the chairbot" is NOT the same test: the
    chairbot also proposes and cross-reviews, and those questions belong
    with its peers'.
    """
    for dictRound in dictCampaign.get("listRounds", []):
        if dictRound.get("iRoundNumber") != iRoundNumber:
            continue
        return {
            str(sText)
            for dictTurnRecord in dictRound.get(
                "dictTurnsByPhase", {}).get(S_PHASE_SYNTHESIS, [])
            for sText in (dictTurnRecord.get("dictResult") or {}).get(
                "listOpenQuestions", []) or []}
    return set()


def _fsClassifyDecisionTier(listGroup, iParticipantCount, setSynthesisTexts):
    if listGroup and all(dictQuestion["sQuestionText"] in setSynthesisTexts
                         for dictQuestion in listGroup):
        return S_TIER_SYNTHESIS
    iDistinctAuthors = len({dictQuestion["sRaisedByParticipantId"]
                            for dictQuestion in listGroup})
    if iParticipantCount and iDistinctAuthors >= iParticipantCount:
        return S_TIER_ALL
    return S_TIER_SEVERAL if iDistinctAuthors > 1 else S_TIER_ONE


def flistDescribeHeldQuestions(dictCampaign):
    """Return questions held for a gate that never opened.

    A question raised before synthesis waits for the plan it is about.
    If a LATER phase then settles indeterminately the campaign becomes
    interrupted, and that transition happens before any gate can open —
    so the questions sit on the round, real work nobody can read. They
    are returned here for any campaign not currently at a gate, because
    the researcher's own next step is to carry them into a fresh
    council: the deliberation that produced them is not recoverable, but
    the questions are.
    """
    if (dictCampaign.get("dictPendingHumanGate") or {}).get("listQuestions"):
        return []
    listRounds = dictCampaign.get("listRounds") or []
    if not listRounds:
        return []
    return list(listRounds[-1].get("listDeferredQuestions") or [])


def fdictDescribeActivePhase(dictCampaign):
    """Return the phase running right now, or None.

    The record the engine writes is what an engine BELIEVES it is doing;
    this is what a reader is entitled to believe. Two things can falsify
    the record without anyone rewriting it — the campaign left the
    planning state, or a hub died mid-phase and a later hub restored the
    checkpoint — and both would leave a dead council reporting
    "synthesizing" indefinitely. So the state is re-checked here, and the
    round number must still be the OPEN round: a record naming a round
    that has since resolved is stale by construction.

    A live hub cannot outrun this. It clears the record before it settles
    a phase, so there is no window in which the round resolves while the
    record still names it.
    """
    dictInFlight = dictCampaign.get("dictPhaseInFlight")
    if not dictInFlight or dictCampaign.get("sState") != S_STATE_PLANNING:
        return None
    listRounds = dictCampaign.get("listRounds") or []
    if not listRounds or listRounds[-1].get("sResolution"):
        return None
    if listRounds[-1].get("iRoundNumber") != dictInFlight.get("iRoundNumber"):
        return None
    return copy.deepcopy(dictInFlight)


def flistGroupGateQuestionsIntoDecisions(dictCampaign):
    """Return the gate's questions as decision points, most-shared first.

    One DECISION, not one question: two agents that asked the same thing
    are answered once, under the plan item the pen-holder placed them
    on. The tier is COMPUTED from who raised what — never asked of a
    model, which could answer wrong about its own peers with nothing
    able to check it.

    Returns an empty list for any gate that is not a blocking-question
    gate, so a caller can render the flat list unchanged.
    """
    dictGate = dictCampaign.get("dictPendingHumanGate") or {}
    if dictGate.get("sGateKind") != S_GATE_BLOCKING_QUESTION:
        return []
    listQuestions = [dictQuestion
                     for dictQuestion in dictGate.get("listQuestions", [])
                     if dictQuestion.get("sQuestionId")]
    if not listQuestions:
        return []
    dictPlan = dictCampaign.get("dictCandidatePlan") or {}
    listPlanItems = (dictPlan.get("dictResult") or {}).get(
        "listPlanItems", []) or []
    dictItemsByQuestionId = _fdictMapQuestionIdToPlanItems(
        listQuestions, listPlanItems)
    setSynthesisTexts = _fsetFindSynthesisQuestionTexts(
        dictCampaign, dictGate.get("iRoundNumber"))
    iParticipantCount = len(dictCampaign.get("listParticipants", []))
    listDecisions = []
    for listGroup in _flistFindCoAnchoredGroups(
            listQuestions, dictItemsByQuestionId):
        listItemIndexes = sorted({
            iItem for dictQuestion in listGroup
            for iItem in dictItemsByQuestionId[dictQuestion["sQuestionId"]]})
        listDecisions.append({
            # Deterministic, because this is recomputed on every read:
            # a minted id would change under a researcher mid-answer.
            "sDecisionId": "decision-" + listGroup[0]["sQuestionId"],
            "sTier": _fsClassifyDecisionTier(
                listGroup, iParticipantCount, setSynthesisTexts),
            "listQuestions": listGroup,
            "listPlanItemIndexes": listItemIndexes,
            "listPlanItemTexts": [str(listPlanItems[iItem])
                                  for iItem in listItemIndexes],
        })
    listDecisions.sort(key=lambda dictDecision: (
        TUPLE_DECISION_TIER_ORDER.index(dictDecision["sTier"]),
        dictDecision["listPlanItemIndexes"] or [len(listPlanItems)]))
    return listDecisions


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
                    # Minted from a uuid rather than a campaign counter
                    # like sObjectionId: a counter is a new REQUIRED
                    # campaign key, and fdictRestoreCampaignFromMetadata
                    # refuses a record missing one — which would strand
                    # every campaign already checkpointed on disk.
                    "sQuestionId": _fsMintIdentifier("question"),
                    "sQuestionText": str(sQuestionText),
                    "sRaisedByParticipantId":
                        dictTurnRecord["sParticipantId"]})
        return listQuestions

    def fnDeferQuestionsUntilSynthesis(self, dictRound, listQuestions):
        """Park pre-synthesis questions instead of gating on them.

        A question raised in proposal or cross-review is a question about
        a plan the researcher cannot read yet: the chairbot has not
        folded the proposals together, so the Plan tab is empty and a
        question citing "phase 2" cites a document that exists only
        inside one agent's own answer. Parking lets synthesis run first,
        so the gate can present the questions against a plan.
        """
        dictRound.setdefault("listDeferredQuestions", []).extend(
            listQuestions)

    def _fnOpenQuestionGate(self, dictRound, sOriginPhase, listQuestions):
        """Enter needsHuman (section 5.4): every turn in the phase has
        already settled terminally before this is reached."""
        self.dictCampaign["dictPendingHumanGate"] = {
            "sGateKind": S_GATE_BLOCKING_QUESTION,
            "iRoundNumber": dictRound["iRoundNumber"],
            "sOriginPhase": sOriginPhase,
            # Whether the questions arrive WITH the plan they are about.
            # False when synthesis produced nothing — a failed chairbot
            # turn must not swallow the questions, so the gate still
            # opens and says plainly that it is un-consolidated.
            "bPlanAvailable": self.dictCampaign["dictCandidatePlan"]
            is not None,
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
