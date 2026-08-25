"""Falsification tests for the Standard planning protocol engine.

Phase 1 of the Agent Council (design/agentCouncil.md section 5.1 and the
section-15.1 checklist). Every test drives the real ``CouncilEngine``
over controllable fake provider connections and asserts a protocol
property in a way that FAILS if the property is broken — the phase
barrier, the round loop, the frozen veto set, the two-distinct-models
quorum floor, the exhausted-round gate with exactly three exits, chairbot
fallback, single-repair, stop-after-current-turn, human-pause
continuation, and state restoration.

The barrier and no-false-consensus properties are asserted the way the
repository's epistemics section demands: by observing the exact order
turns ran and the exact material each received, and by making a
participant fail and checking its absence is never counted as agreement —
not by trusting that a green pass means the guarantee holds.
"""

from vaibify.gui.agentCouncil import fdictRestoreCampaignFromMetadata
from vaibify.gui.agentCouncilCampaign import CouncilProtocolError

from tests.agentCouncilHarness import (
    fdictDecideCompleted,
    fdictDecideRaise,
    fdictMakeTurnResult,
    ffnDecideAllAccept,
    fixtureBuildCouncil,
)

import pytest

LIST_TWO_SPECS = [
    {"sHandle": "A", "sProvider": "prov-a", "sRequestedModel": "model-a"},
    {"sHandle": "B", "sProvider": "prov-b", "sRequestedModel": "model-b"},
]
LIST_THREE_SPECS = LIST_TWO_SPECS + [
    {"sHandle": "C", "sProvider": "prov-c", "sRequestedModel": "model-c"}]

S_PROPOSAL = "independentProposals"
S_CROSS_REVIEW = "crossReview"
S_SYNTHESIS = "synthesis"
S_VETO = "veto"


def _ffnDecideVetoVerdict(sVoterHandle, sVerdict, iRoundLimit=999,
                          listObjections=None, listOpenQuestions=None):
    """A decider where one voter returns a chosen verdict up to a round."""
    def ffnDecide(sHandle, dictTurnRequest):
        bMatch = (sHandle == sVoterHandle
                  and dictTurnRequest["sPhase"] == S_VETO
                  and dictTurnRequest["iRoundNumber"] <= iRoundLimit)
        if bMatch:
            return fdictDecideCompleted(fdictMakeTurnResult(
                sVerdict, listBlockingObjections=listObjections,
                listOpenQuestions=listOpenQuestions))
        return fdictDecideCompleted(fdictMakeTurnResult("accept"))
    return ffnDecide


# ----- phase transitions ----------------------------------------------

def testStandardPhaseTransitionsRunInOrderToPlanReady():
    """A clean campaign walks proposal -> cross-review -> synthesis ->
    veto and reaches planReady (section 5.1)."""
    fixture = fixtureBuildCouncil(LIST_TWO_SPECS, ffnDecideAllAccept,
                                  sChairbotHandle="A")
    dictOut = fixture.fdictDrive()
    assert dictOut["sState"] == "planReady"
    listPhaseSequence = [tEntry[1] for tEntry in fixture.recorder.listOrderLog]
    assert listPhaseSequence == [
        S_PROPOSAL, S_PROPOSAL, S_CROSS_REVIEW, S_CROSS_REVIEW,
        S_SYNTHESIS, S_VETO]
    listStates = [dictTransition["sToState"]
                  for dictTransition in dictOut["listStateTransitions"]]
    assert listStates == ["planning", "planReady"]


# ----- the phase barrier ----------------------------------------------

def testBarrierRevealsEveryProposalBeforeAnyCrossReview():
    """No cross-review turn runs until every proposal has settled, and
    each cross-review sees every peer's proposal (section 5.1)."""
    fixture = fixtureBuildCouncil(LIST_THREE_SPECS, ffnDecideAllAccept,
                                  sChairbotHandle="A")
    fixture.fdictDrive()
    listOrder = fixture.recorder.listOrderLog
    iLastProposal = max(iIndex for iIndex, tEntry in enumerate(listOrder)
                        if tEntry[1] == S_PROPOSAL)
    iFirstReview = min(iIndex for iIndex, tEntry in enumerate(listOrder)
                       if tEntry[1] == S_CROSS_REVIEW)
    assert iLastProposal < iFirstReview
    for sHandle in ("A", "B", "C"):
        dictRequest = fixture.flistRequestsFor(sHandle, S_CROSS_REVIEW)[0]
        listPeerAuthors = {dictQuoted["sAuthorIdentity"]
                           for dictQuoted in dictRequest["listQuotedMaterial"]
                           if dictQuoted["sSourceKind"] == "peerProposal"}
        assert len(listPeerAuthors) == 2


def testFailedProposerDoesNotStartReviewEarlyNorCountAsAgreement():
    """A participant whose proposal fails is recorded absent, never
    dropped silently and never treated as agreement; the barrier still
    holds for the survivors (section 5.1)."""
    ffnDecide = lambda sHandle, dictRequest: (
        fdictDecideRaise("proposalFailed")
        if sHandle == "C" and dictRequest["sPhase"] == S_PROPOSAL
        else fdictDecideCompleted(fdictMakeTurnResult("accept")))
    fixture = fixtureBuildCouncil(LIST_THREE_SPECS, ffnDecide,
                                  sChairbotHandle="A")
    dictOut = fixture.fdictDrive()
    listOrder = fixture.recorder.listOrderLog
    iLastProposal = max(iIndex for iIndex, tEntry in enumerate(listOrder)
                        if tEntry[1] == S_PROPOSAL)
    iFirstReview = min(iIndex for iIndex, tEntry in enumerate(listOrder)
                       if tEntry[1] == S_CROSS_REVIEW)
    assert iLastProposal < iFirstReview
    assert dictOut["listParticipants"][2]["bFailed"] is True
    dictReviewRequest = fixture.flistRequestsFor("A", S_CROSS_REVIEW)[0]
    listKinds = [dictQuoted["sSourceKind"]
                 for dictQuoted in dictReviewRequest["listQuotedMaterial"]]
    assert "absenceNote" in listKinds
    listPeerAuthors = {dictQuoted["sAuthorIdentity"]
                       for dictQuoted in dictReviewRequest["listQuotedMaterial"]
                       if dictQuoted["sSourceKind"] == "peerProposal"}
    assert len(listPeerAuthors) == 1
    assert "C" not in fixture.flistOrderHandles(S_CROSS_REVIEW)


# ----- the round loop -------------------------------------------------

def testBlockingObjectionOpensNewRoundAgainstTheCurrentCandidate():
    """With rounds remaining, a blocking objection starts another round
    that re-reviews the current candidate — not fresh proposals — and the
    chairbot keeps the pen (section 5.1)."""
    fixture = fixtureBuildCouncil(
        LIST_THREE_SPECS,
        _ffnDecideVetoVerdict("B", "blockingObjection", iRoundLimit=1,
                              listObjections=["a real gap"]),
        sChairbotHandle="A")
    dictOut = fixture.fdictDrive()
    assert dictOut["sState"] == "planReady"
    assert len(dictOut["listRounds"]) == 2
    listRoundTwoPhases = [tEntry[1] for tEntry in fixture.recorder.listOrderLog
                          if tEntry[2] == 2]
    assert S_PROPOSAL not in listRoundTwoPhases
    dictReviewRequest = fixture.flistRequestsFor("B", S_CROSS_REVIEW)[1]
    listKinds = {dictQuoted["sSourceKind"]
                 for dictQuoted in dictReviewRequest["listQuotedMaterial"]}
    assert "candidatePlan" in listKinds
    assert "peerProposal" not in listKinds
    assert fixture.fsHandleForId(
        dictOut["listRounds"][1]["sSynthesisAuthorId"]) == "A"


# ----- the frozen veto set --------------------------------------------

def testSynthesisAuthorNeverVotesOnItsOwnPlan():
    """The frozen voter set excludes the synthesis author (section
    5.1)."""
    fixture = fixtureBuildCouncil(LIST_THREE_SPECS, ffnDecideAllAccept,
                                  sChairbotHandle="A")
    dictOut = fixture.fdictDrive()
    sAuthorId = dictOut["listRounds"][0]["sSynthesisAuthorId"]
    listFrozen = dictOut["listRounds"][0]["listFrozenVoterIds"]
    assert sAuthorId not in listFrozen
    assert set(listFrozen) == (
        set(fixture.dictHandleToId.values()) - {sAuthorId})


def testPlanReadyRequiresEveryFrozenVetoToAccept():
    """One frozen voter's blocking objection blocks planReady on that
    round (section 5.1)."""
    fixture = fixtureBuildCouncil(
        LIST_THREE_SPECS,
        _ffnDecideVetoVerdict("B", "blockingObjection",
                              listObjections=["persistent gap"]),
        dictSettings={"iMaximumRounds": 1}, sChairbotHandle="A")
    dictOut = fixture.fdictDrive()
    assert dictOut["sState"] != "planReady"
    assert dictOut["sState"] == "needsHuman"


def testFrozenVoterVanishingBetweenSynthesisAndVoteIsUndetermined():
    """A frozen voter whose veto fails is undetermined, kept in the set,
    never dropped (section 5.1)."""
    fixture = fixtureBuildCouncil(
        LIST_THREE_SPECS,
        _fFailVetoOf("B", iRoundLimit=1),
        sChairbotHandle="A")
    dictOut = fixture.fdictDrive()
    dictVerdicts = dictOut["listRounds"][0]["dictVetoVerdicts"]
    sVoterB = fixture.dictHandleToId["B"]
    assert sVoterB in dictVerdicts
    assert dictVerdicts[sVoterB]["sVerdict"] == "undetermined"


def testMissingVetoIsUndeterminedAndNeverAbsenceOfObjection():
    """An undetermined veto raises an unresolved objection worded as
    neither acceptance nor absence of objection (section 5.1)."""
    fixture = fixtureBuildCouncil(
        LIST_THREE_SPECS, _fFailVetoOf("B"),
        dictSettings={"iMaximumRounds": 1}, sChairbotHandle="A")
    dictOut = fixture.fdictDrive()
    assert dictOut["sState"] != "planReady"
    listObjectionTexts = [
        dictObjection["sObjectionText"]
        for dictRound in dictOut["listRounds"]
        for dictObjection in dictRound["listUnresolvedObjections"]]
    assert any("undetermined" in sText and "not acceptance" in sText
               for sText in listObjectionTexts)


# ----- the two-distinct-models quorum floor ---------------------------

def testTwoParticipantChairbotFailureCannotReachPlanReady():
    """When the chairbot fails and the fallback author leaves no
    independent veto, the round enters needsHuman, never planReady
    (section 5.1 quorum floor). This is the case that infinite-looped
    until the empty-phase key was recorded."""
    ffnDecide = lambda sHandle, dictRequest: (
        fdictDecideRaise("chairbotSynthesisFailed")
        if sHandle == "A" and dictRequest["sPhase"] == S_SYNTHESIS
        else fdictDecideCompleted(fdictMakeTurnResult("accept")))
    fixture = fixtureBuildCouncil(LIST_TWO_SPECS, ffnDecide,
                                  sChairbotHandle="A")
    dictOut = fixture.fdictDrive()
    assert dictOut["sState"] == "needsHuman"
    assert dictOut["dictPendingHumanGate"]["sGateKind"] == "quorumShortfall"
    assert dictOut["listRounds"][0]["listFrozenVoterIds"] == []


def testNoSubstantiveWorkSurvivingFailsRatherThanReady():
    """If every participant fails before any turn completes, the campaign
    fails, never reaches a ready plan (section 5.1)."""
    fixture = fixtureBuildCouncil(
        LIST_TWO_SPECS,
        lambda sHandle, dictRequest: fdictDecideRaise("everyoneFailed"),
        sChairbotHandle="A")
    dictOut = fixture.fdictDrive()
    assert dictOut["sState"] == "failed"


# ----- exhausted round budget -----------------------------------------

def testExhaustedRoundBudgetOpensNeedsHumanWithExactlyThreeExits():
    """A budget exhausted with objections outstanding enters needsHuman
    presenting the candidate, the objections, and exactly the three exits
    — never an ambiguous ready-with-objections (section 5.1)."""
    fixture = fixtureBuildCouncil(
        LIST_THREE_SPECS,
        _ffnDecideVetoVerdict("B", "blockingObjection",
                              listObjections=["unresolved cost"]),
        dictSettings={"iMaximumRounds": 2}, sChairbotHandle="A")
    dictOut = fixture.fdictDrive()
    assert dictOut["sState"] == "needsHuman"
    dictGate = dictOut["dictPendingHumanGate"]
    assert dictGate["sGateKind"] == "exhaustedRounds"
    assert dictGate["listExitActions"] == [
        "grantBoundedResolutionRound", "resolveOrOverrideThenFinalVeto",
        "rejectOrArchiveCandidate"]
    assert len(dictGate["listUnresolvedObjections"]) >= 1


def testPlainResponseIsRefusedAtTheExhaustedGate():
    """A plain researcher response never silently relaunches the spent
    budget (section 5.1)."""
    fixture = _fixtureAtExhaustedGate()
    with pytest.raises(CouncilProtocolError):
        fixture.fdictContinue("just keep going")


def testGrantResolutionRoundReopensAnExplicitBudget():
    """Exit 1 grants a fresh, explicitly-sized budget and the loop
    resumes (section 5.1)."""
    fixture = fixtureBuildCouncil(
        LIST_THREE_SPECS,
        _ffnDecideVetoVerdict("B", "blockingObjection", iRoundLimit=2,
                              listObjections=["fixable"]),
        dictSettings={"iMaximumRounds": 2}, sChairbotHandle="A")
    fixture.fdictDrive()
    dictOut = fixture.fdictGrantResolutionRound(1)
    assert dictOut["sState"] == "planReady"
    assert dictOut["iGrantedAdditionalRounds"] == 1
    listDecisionKinds = [dictDecision["sDecisionKind"]
                         for dictDecision in dictOut["listResearcherDecisions"]]
    assert "resolutionRoundGranted" in listDecisionKinds


def testOverrideIsRecordedAsDecisionNotLaunderedIntoCouncilAccept():
    """Exit 2's override is a recorded researcher decision, kept out of
    the council-cleared list, so provenance stays honest (section 5.1)."""
    fixture = fixtureBuildCouncil(
        LIST_THREE_SPECS,
        _ffnDecideVetoVerdict("B", "blockingObjection", iRoundLimit=2,
                              listObjections=["a risk the researcher owns"]),
        dictSettings={"iMaximumRounds": 2}, sChairbotHandle="A")
    dictOut = fixture.fdictDrive()
    sObjectionId = dictOut["dictPendingHumanGate"][
        "listUnresolvedObjections"][0]["sObjectionId"]
    dictOut = fixture.fdictResolveObjections(
        {sObjectionId: {"sAction": "override", "sText": "I accept the risk"}})
    assert dictOut["sState"] == "planReady"
    dictPlan = dictOut["dictCandidatePlan"]
    listOverriddenIds = [dictObjection["sObjectionId"] for dictObjection in
                         dictPlan["listResearcherOverriddenObjections"]]
    listClearedIds = [dictObjection["sObjectionId"] for dictObjection in
                      dictPlan["listCouncilClearedObjections"]]
    assert sObjectionId in listOverriddenIds
    assert sObjectionId not in listClearedIds
    listDecisionKinds = [dictDecision["sDecisionKind"]
                         for dictDecision in dictOut["listResearcherDecisions"]]
    assert "objectionOverride" in listDecisionKinds


def testRejectCandidateEndsTheCampaignWithNoPlan():
    """Exit 3 archives with no accepted plan (section 5.1)."""
    fixture = _fixtureAtExhaustedGate()
    dictOut = fixture.engine.fdictRejectCandidate("not worth it")
    assert dictOut["sState"] == "archived"


# ----- chairbot selection and fallback --------------------------------

def testChairbotDefaultsToFirstConfiguredParticipant():
    """The default chairbot is the first participant, a structural choice
    (section 6.3.1)."""
    fixture = fixtureBuildCouncil(LIST_THREE_SPECS, ffnDecideAllAccept)
    assert fixture.dictCampaign["sChairbotParticipantId"] == (
        fixture.dictHandleToId["A"])
    dictOut = fixture.fdictDrive()
    assert fixture.fsHandleForId(
        dictOut["listRounds"][0]["sSynthesisAuthorId"]) == "A"


def testExplicitChairbotChoiceIsHonored():
    fixture = fixtureBuildCouncil(LIST_THREE_SPECS, ffnDecideAllAccept,
                                  sChairbotHandle="B")
    dictOut = fixture.fdictDrive()
    assert fixture.fsHandleForId(
        dictOut["listRounds"][0]["sSynthesisAuthorId"]) == "B"


def testFailedChairbotFallsBackWithSubstitutionRecorded():
    """A failed chairbot synthesis hands the pen to the next participant
    and records the substitution — never a plan with no chairbot (section
    6.3.1)."""
    ffnDecide = lambda sHandle, dictRequest: (
        fdictDecideRaise("chairbotFailed")
        if sHandle == "A" and dictRequest["sPhase"] == S_SYNTHESIS
        else fdictDecideCompleted(fdictMakeTurnResult("accept")))
    fixture = fixtureBuildCouncil(LIST_THREE_SPECS, ffnDecide,
                                  sChairbotHandle="A")
    dictOut = fixture.fdictDrive()
    assert dictOut["sState"] == "planReady"
    assert fixture.fsHandleForId(
        dictOut["listRounds"][0]["sSynthesisAuthorId"]) == "B"
    assert dictOut["listRounds"][0]["bChairbotSubstituted"] is True
    assert dictOut["dictCandidatePlan"]["sSynthesisAuthorId"] == (
        fixture.dictHandleToId["B"])
    listSubstitutionEvents = [dictEvent for dictEvent in fixture.listEvents
                              if dictEvent["sEventKind"] == (
                                  "chairbotSubstituted")]
    assert len(listSubstitutionEvents) == 1


# ----- minimum rounds -------------------------------------------------

def testMinimumRoundsForcesAnAdversarialRoundBeforePlanReady():
    """A minimum-rounds floor holds a would-be-ready round and forces at
    least one more adversarial round (section 6.3.2)."""
    fixture = fixtureBuildCouncil(LIST_THREE_SPECS, ffnDecideAllAccept,
                                  dictSettings={"iMinimumRounds": 2},
                                  sChairbotHandle="A")
    dictOut = fixture.fdictDrive()
    assert dictOut["sState"] == "planReady"
    assert len(dictOut["listRounds"]) == 2
    listFloorEvents = [dictEvent for dictEvent in fixture.listEvents
                       if dictEvent["sEventKind"] == "minimumRoundsFloorHeld"]
    assert len(listFloorEvents) == 1


# ----- human pause and continuation -----------------------------------

def testIndeterminateTurnSettlesToInterruptedNotNeedsHuman():
    """An indeterminate completion becomes interrupted and never
    masquerades as a clean human pause (section 5.4)."""
    ffnDecide = lambda sHandle, dictRequest: (
        fdictDecideCompleted(fdictMakeTurnResult("accept"),
                             sCompletion="indeterminate")
        if sHandle == "A" and dictRequest["sPhase"] == S_PROPOSAL
        else fdictDecideCompleted(fdictMakeTurnResult("accept")))
    fixture = fixtureBuildCouncil(LIST_TWO_SPECS, ffnDecide,
                                  sChairbotHandle="A")
    dictOut = fixture.fdictDrive()
    assert dictOut["sState"] == "interrupted"


def testVetoNeedsHumanEntersGateAfterAllVetoTurnsSettle():
    """A needsHuman veto opens the blocking-question gate only after the
    veto phase has fully settled (section 5.4)."""
    fixture = fixtureBuildCouncil(
        LIST_THREE_SPECS,
        _ffnDecideVetoVerdict("B", "needsHuman", iRoundLimit=1,
                              listOpenQuestions=["which trade-off?"]),
        sChairbotHandle="A")
    dictOut = fixture.fdictDrive()
    assert dictOut["sState"] == "needsHuman"
    assert dictOut["dictPendingHumanGate"]["sGateKind"] == "blockingQuestion"
    listVetoRecords = dictOut["listRounds"][0]["dictTurnsByPhase"][S_VETO]
    assert all(dictRecord["sStatus"] in ("completed", "failed", "notStarted")
               for dictRecord in listVetoRecords)
    assert dictOut["dictPendingHumanGate"]["listQuestions"]


def testContinuationAfterResearcherResponseReconstructsContext():
    """A researcher response is recorded and the continuation rebuilds
    context from the record — the response reappears as quoted material in
    the next round (section 5.4)."""
    fixture = fixtureBuildCouncil(
        LIST_THREE_SPECS,
        _ffnDecideVetoVerdict("B", "needsHuman", iRoundLimit=1,
                              listOpenQuestions=["which trade-off?"]),
        sChairbotHandle="A")
    fixture.fdictDrive()
    dictOut = fixture.fdictContinue("prefer the conservative option")
    assert dictOut["sState"] == "planReady"
    listResponseTexts = [dictResponse["sText"]
                         for dictResponse in dictOut["listResearcherResponses"]]
    assert "prefer the conservative option" in listResponseTexts
    dictRoundTwoRequest = fixture.flistRequestsFor("A", S_CROSS_REVIEW)[1]
    listResponseQuotes = [
        dictQuoted for dictQuoted in dictRoundTwoRequest["listQuotedMaterial"]
        if dictQuoted["sSourceKind"] == "researcherResponse"]
    assert any("conservative option" in dictQuoted["sContent"]
               for dictQuoted in listResponseQuotes)


# ----- one failure, no false consensus --------------------------------

def testOneParticipantFailureReachesPlanReadyWithoutFalseConsensus():
    """A single failure does not manufacture consensus: the failed member
    is excluded from the veto set and planReady rests on a surviving
    member's real accept (section 5.1)."""
    ffnDecide = lambda sHandle, dictRequest: (
        fdictDecideRaise("droppedOut")
        if sHandle == "C" and dictRequest["sPhase"] == S_CROSS_REVIEW
        else fdictDecideCompleted(fdictMakeTurnResult("accept")))
    fixture = fixtureBuildCouncil(LIST_THREE_SPECS, ffnDecide,
                                  sChairbotHandle="A")
    dictOut = fixture.fdictDrive()
    assert dictOut["sState"] == "planReady"
    sVoterC = fixture.dictHandleToId["C"]
    assert sVoterC not in dictOut["listRounds"][0]["listFrozenVoterIds"]
    assert dictOut["listParticipants"][2]["bFailed"] is True


# ----- structured-output repair ---------------------------------------

def testInvalidStructuredOutputIsRepairedExactlyOnce():
    """An invalid result triggers exactly one repair; a valid repair
    completes the turn (section 8.5)."""
    ffnDecide = lambda sHandle, dictRequest: (
        fdictDecideCompleted({})
        if (sHandle == "B" and dictRequest["sPhase"] == S_PROPOSAL
            and not dictRequest["bRepairRequest"])
        else fdictDecideCompleted(fdictMakeTurnResult("accept")))
    fixture = fixtureBuildCouncil(LIST_TWO_SPECS, ffnDecide,
                                  sChairbotHandle="A")
    dictOut = fixture.fdictDrive()
    listAttemptsForB = [tEntry for tEntry in fixture.recorder.listOrderLog
                        if tEntry[0] == "B" and tEntry[1] == S_PROPOSAL]
    assert len(listAttemptsForB) == 2
    assert listAttemptsForB[0][3] is False
    assert listAttemptsForB[1][3] is True
    dictRecord = _fdictProposalRecordOf(fixture, dictOut, "B")
    assert dictRecord["bRepairAttempted"] is True
    assert dictRecord["sStatus"] == "completed"


def testTwiceInvalidOutputFailsVisiblyNeverSilentAgreement():
    """A second invalid result fails the turn visibly rather than being
    replaced with an empty agreement (section 8.5)."""
    ffnDecide = lambda sHandle, dictRequest: (
        fdictDecideCompleted({})
        if sHandle == "B" and dictRequest["sPhase"] == S_PROPOSAL
        else fdictDecideCompleted(fdictMakeTurnResult("accept")))
    fixture = fixtureBuildCouncil(LIST_THREE_SPECS, ffnDecide,
                                  sChairbotHandle="A")
    dictOut = fixture.fdictDrive()
    listAttemptsForB = [tEntry for tEntry in fixture.recorder.listOrderLog
                        if tEntry[0] == "B" and tEntry[1] == S_PROPOSAL]
    assert len(listAttemptsForB) == 2
    dictRecord = _fdictProposalRecordOf(fixture, dictOut, "B")
    assert dictRecord["sStatus"] == "failed"
    assert "invalidStructuredResultAfterRepair" in dictRecord[
        "sFailureReason"]
    assert dictOut["listParticipants"][1]["bFailed"] is True


def testAFailedTurnRecordsWhatTheParticipantActuallyReturned():
    """A list of absent fields is not a diagnosis.

    Kills: discarding the rejected payload on an invalid turn.

    A live council failed with every schema field reported missing, and
    the record could not distinguish "the model formatted its answer
    badly" from "the model said nothing at all" — which is what had
    happened: an expired token, a CLI that never called the API, and a
    usage block of zeroes. The adapter produced the raw text under
    sRawResultText and the engine dropped it, so the one field that
    would have named the cause was the one not kept (2026-08-24).
    """
    ffnDecide = lambda sHandle, dictRequest: (
        fdictDecideCompleted({"sRawResultText": ""})
        if sHandle == "B" and dictRequest["sPhase"] == S_PROPOSAL
        else fdictDecideCompleted(fdictMakeTurnResult("accept")))
    fixture = fixtureBuildCouncil(LIST_THREE_SPECS, ffnDecide,
                                  sChairbotHandle="A")
    dictOut = fixture.fdictDrive()
    dictRecord = _fdictProposalRecordOf(fixture, dictOut, "B")
    assert dictRecord["sStatus"] == "failed"
    assert "sRawResultText" in dictRecord["sRejectedPayload"], (
        "the rejected payload was not recorded, so a turn that said "
        f"nothing is indistinguishable from a malformed one: {dictRecord}")


def testTheRejectedPayloadIsBoundedRatherThanStoredWhole():
    """A diagnostic must not become an unbounded field in the record.

    The campaign record is written to disk on every checkpoint, so an
    enormous rejected payload would be paid for repeatedly for the life
    of the campaign.
    """
    sHuge = "x" * 50000
    ffnDecide = lambda sHandle, dictRequest: (
        fdictDecideCompleted({"sRawResultText": sHuge})
        if sHandle == "B" and dictRequest["sPhase"] == S_PROPOSAL
        else fdictDecideCompleted(fdictMakeTurnResult("accept")))
    fixture = fixtureBuildCouncil(LIST_THREE_SPECS, ffnDecide,
                                  sChairbotHandle="A")
    dictOut = fixture.fdictDrive()
    dictRecord = _fdictProposalRecordOf(fixture, dictOut, "B")
    sPayload = dictRecord["sRejectedPayload"]
    assert len(sPayload) < 3000, len(sPayload)
    assert "truncated from" in sPayload, (
        "the payload was cut without saying so, which reads as a short "
        "answer rather than a long one")


# ----- output cap ------------------------------------------------------

def testOutputByteCapFailsTheTurnVisibly():
    """A turn whose result exceeds the byte budget fails visibly (section
    8.5)."""
    def ffnDecide(sHandle, dictRequest):
        if sHandle == "A" and dictRequest["sPhase"] == S_PROPOSAL:
            dictResult = fdictMakeTurnResult("accept")
            dictResult["sSummary"] = "x" * 5000
            return fdictDecideCompleted(dictResult)
        return fdictDecideCompleted(fdictMakeTurnResult("accept"))
    fixture = fixtureBuildCouncil(
        LIST_THREE_SPECS, ffnDecide,
        dictSettings={"iMaximumOutputBytesPerTurn": 1000},
        sChairbotHandle="B")
    dictOut = fixture.fdictDrive()
    dictRecord = _fdictProposalRecordOf(fixture, dictOut, "A")
    assert dictRecord["sStatus"] == "failed"
    assert dictRecord["sFailureReason"] == "outputByteBudgetExceeded"
    assert dictOut["listParticipants"][0]["bFailed"] is True


# ----- stop after current turn ----------------------------------------

def testStopAfterCurrentTurnAdmitsNoLaterTurns():
    """A stop requested during a turn lets the in-flight wave settle and
    launches nothing later (section 9.4)."""
    dictHolder = {}

    def ffnDecide(sHandle, dictRequest):
        if sHandle == "A" and dictRequest["sPhase"] == S_PROPOSAL:
            dictHolder["engine"].fnRequestStopAfterCurrentTurn()
        return fdictDecideCompleted(fdictMakeTurnResult("accept"))
    fixture = fixtureBuildCouncil(
        LIST_THREE_SPECS, ffnDecide,
        dictSettings={"iMaximumConcurrentTurns": 1}, sChairbotHandle="A")
    dictHolder["engine"] = fixture.engine
    dictOut = fixture.fdictDrive()
    assert dictOut["sState"] == "archived"
    listProposalHandles = fixture.flistOrderHandles(S_PROPOSAL)
    assert listProposalHandles == ["A"]
    listProposalRecords = (
        dictOut["listRounds"][0]["dictTurnsByPhase"][S_PROPOSAL])
    dictStatusByHandle = {
        fixture.fsHandleForId(dictRecord["sParticipantId"]):
            dictRecord["sStatus"]
        for dictRecord in listProposalRecords}
    assert dictStatusByHandle["A"] == "completed"
    assert dictStatusByHandle["B"] == "notStarted"
    assert dictStatusByHandle["C"] == "notStarted"


def testStopRequestedBeforeAnyTurnArchivesWithoutLaunching():
    fixture = fixtureBuildCouncil(LIST_TWO_SPECS, ffnDecideAllAccept,
                                  sChairbotHandle="A")
    fixture.engine.fnRequestStopAfterCurrentTurn()
    dictOut = fixture.fdictDrive()
    assert dictOut["sState"] == "archived"
    assert fixture.recorder.listOrderLog == []


# ----- acceptance and state restoration -------------------------------

def testAcceptPlanTransitionsThroughAcceptedToAwaitingImplementation():
    """Only a planReady campaign can be accepted, and acceptance records
    the two-step transition (section 6.6)."""
    fixture = fixtureBuildCouncil(LIST_TWO_SPECS, ffnDecideAllAccept,
                                  sChairbotHandle="A")
    fixture.fdictDrive()
    dictOut = fixture.engine.fdictAcceptPlan()
    assert dictOut["sState"] == "awaitingImplementation"
    listStates = [dictTransition["sToState"]
                  for dictTransition in dictOut["listStateTransitions"]]
    assert listStates[-2:] == ["planAccepted", "awaitingImplementation"]


def testStateRestorationFromAcceptedCampaignMetadata():
    """A checkpointed campaign restores to an equal, independent record;
    a missing required key is rejected (section 15.1)."""
    fixture = fixtureBuildCouncil(LIST_TWO_SPECS, ffnDecideAllAccept,
                                  sChairbotHandle="A")
    fixture.fdictDrive()
    dictAccepted = fixture.engine.fdictAcceptPlan()
    dictRestored = fdictRestoreCampaignFromMetadata(dictAccepted)
    assert dictRestored == dictAccepted
    assert dictRestored is not dictAccepted
    assert dictRestored["sState"] == "awaitingImplementation"
    dictMissingKey = dict(dictAccepted)
    del dictMissingKey["iClaimCounter"]
    with pytest.raises(CouncilProtocolError):
        fdictRestoreCampaignFromMetadata(dictMissingKey)


# ----- baseline-evidence executor seam --------------------------------

def testBaselineEvidenceExecutorIsDrivenServerSideForBaselineClaims():
    """The engine drives the injected baseline-evidence callback — the
    server, not the model — for a baseline-confirmed claim (section
    7.4)."""
    listEvidence = [{"sStatus": "confirmed", "sStateForm": "baseline",
                     "sCommandText": "run the check"}]

    def ffnDecide(sHandle, dictRequest):
        if sHandle == "A" and dictRequest["sPhase"] == S_PROPOSAL:
            return fdictDecideCompleted(
                fdictMakeTurnResult("accept", listEvidence=listEvidence))
        return fdictDecideCompleted(fdictMakeTurnResult("accept"))
    fixture = fixtureBuildCouncil(LIST_TWO_SPECS, ffnDecide,
                                  sChairbotHandle="A")
    fixture.fdictDrive()
    assert len(fixture.listBaselineCalls) == 1
    assert fixture.listBaselineCalls[0]["sCommandText"] == "run the check"


# ----- shared helpers -------------------------------------------------

def _fFailVetoOf(sVoterHandle, iRoundLimit=999):
    def ffnDecide(sHandle, dictTurnRequest):
        bMatch = (sHandle == sVoterHandle
                  and dictTurnRequest["sPhase"] == S_VETO
                  and dictTurnRequest["iRoundNumber"] <= iRoundLimit)
        if bMatch:
            return fdictDecideRaise("vetoTurnFailed")
        return fdictDecideCompleted(fdictMakeTurnResult("accept"))
    return ffnDecide


def _fdictProposalRecordOf(fixture, dictOut, sHandle):
    for dictRound in dictOut["listRounds"]:
        for dictRecord in dictRound["dictTurnsByPhase"].get(S_PROPOSAL, []):
            if fixture.fsHandleForId(dictRecord["sParticipantId"]) == sHandle:
                return dictRecord
    raise AssertionError(f"no proposal record for {sHandle}")


def _fixtureAtExhaustedGate():
    fixture = fixtureBuildCouncil(
        LIST_THREE_SPECS,
        _ffnDecideVetoVerdict("B", "blockingObjection",
                              listObjections=["unresolved"]),
        dictSettings={"iMaximumRounds": 2}, sChairbotHandle="A")
    fixture.fdictDrive()
    return fixture


def testAnEmptyTurnIsExplainedNotSchemaValidated():
    """The diagnosis must not be buried inside the error it explains.

    Kills: running the schema validator over an empty result.

    A researcher read this on screen: fifteen "must be an array" lines,
    then "unknown keys are not part of the schema:
    ['bResultEventReportedError', 'dictEventTypeCounts', ...]" — the
    diagnostic fields themselves, reported as violations, with the
    actual cause nowhere in sight. An empty result is not a malformed
    answer; it is the absence of one (2026-08-24).
    """
    ffnDecide = lambda sHandle, dictRequest: (
        fdictDecideCompleted({
            "sRawResultText": "",
            "sEmptyResultReason": "rateLimitedBeforeAnyResult",
            "dictEventTypeCounts": {"assistant": 52}})
        if sHandle == "B" and dictRequest["sPhase"] == S_PROPOSAL
        else fdictDecideCompleted(fdictMakeTurnResult("accept")))
    fixture = fixtureBuildCouncil(LIST_THREE_SPECS, ffnDecide,
                                  sChairbotHandle="A")
    dictOut = fixture.fdictDrive()
    dictRecord = _fdictProposalRecordOf(fixture, dictOut, "B")

    assert dictRecord["sStatus"] == "failed"
    sReason = dictRecord["sFailureReason"]
    assert "rate-limited" in sReason, sReason
    assert "52 messages" in sReason, sReason
    assert "must be an array" not in sReason, (
        f"an absent answer was described as a malformed one: {sReason}")
    assert "unknown keys" not in sReason, sReason
    assert dictRecord["sRejectedPayload"], (
        "the raw payload was dropped for the empty case")


def testARepairIsNotSpentOnATurnThatReturnedNothing():
    """A second turn against a throttling provider is one thrown away.

    Kills: routing an empty result through the repair path.
    """
    listAttempts = []
    def ffnDecide(sHandle, dictRequest):
        if sHandle == "B" and dictRequest["sPhase"] == S_PROPOSAL:
            listAttempts.append(sHandle)
            return fdictDecideCompleted({
                "sRawResultText": "",
                "sEmptyResultReason": "rateLimitedBeforeAnyResult"})
        return fdictDecideCompleted(fdictMakeTurnResult("accept"))
    fixture = fixtureBuildCouncil(LIST_THREE_SPECS, ffnDecide,
                                  sChairbotHandle="A")
    fixture.fdictDrive()
    assert len(listAttempts) == 1, (
        f"the empty turn was retried {len(listAttempts)} times")
