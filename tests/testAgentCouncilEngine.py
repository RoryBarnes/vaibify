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


def testProposalQuestionIsHeldUntilSynthesisSoTheGateCarriesAPlan():
    """A question raised before synthesis does not stop the round where
    it is raised: it is held until the pen-holder has folded a plan, so
    the researcher reads it against a document instead of against
    nothing.

    Kills: opening the gate at the proposal settle (the ordering before
    2026-08-25). That gated with dictCandidatePlan still None, so the
    Plan tab was empty and a question citing "phase 2" cited a document
    living only inside one participant's own answer.
    """
    ffnDecide = lambda sHandle, dictRequest: (
        fdictDecideCompleted(fdictMakeTurnResult(
            "needsHuman",
            listOpenQuestions=["delete the integrator, or discourage it?"]))
        if sHandle == "B" and dictRequest["sPhase"] == S_PROPOSAL
        else fdictDecideCompleted(fdictMakeTurnResult("accept")))
    fixture = fixtureBuildCouncil(LIST_TWO_SPECS, ffnDecide,
                                  sChairbotHandle="A")
    dictOut = fixture.fdictDrive()

    assert dictOut["sState"] == "needsHuman"
    dictGate = dictOut["dictPendingHumanGate"]
    assert dictGate["sOriginPhase"] == S_SYNTHESIS, (
        "the gate opened before the pen-holder had written anything")
    assert dictGate["bPlanAvailable"] is True
    assert dictOut["dictCandidatePlan"] is not None, (
        "needsHuman with no candidate plan is the empty Plan tab")
    [dictQuestion] = dictGate["listQuestions"]
    assert dictQuestion["sQuestionText"] == (
        "delete the integrator, or discourage it?")
    assert dictQuestion["sQuestionId"].startswith("question-"), (
        "a question with no stable id cannot be paired with its answer")


def testTheChairbotIsShownTheHeldQuestionsWithTheirIdentifiers():
    """The pen-holder cannot anchor a question it was never handed.

    Kills: quoting the peers' results alone. Their text carries the
    question wording but no id, so the chairbot could only paraphrase to
    refer to one — and a paraphrase cannot be matched back to the
    researcher's answer.
    """
    ffnDecide = lambda sHandle, dictRequest: (
        fdictDecideCompleted(fdictMakeTurnResult(
            "needsHuman", listOpenQuestions=["which trade-off?"]))
        if sHandle == "B" and dictRequest["sPhase"] == S_PROPOSAL
        else fdictDecideCompleted(fdictMakeTurnResult("accept")))
    fixture = fixtureBuildCouncil(LIST_TWO_SPECS, ffnDecide,
                                  sChairbotHandle="A")
    fixture.fdictDrive()

    listSynthesisRequests = fixture.flistRequestsFor("A", S_SYNTHESIS)
    assert listSynthesisRequests, "synthesis never ran"
    listHeld = [
        dictQuoted
        for dictQuoted in listSynthesisRequests[0]["listQuotedMaterial"]
        if dictQuoted["sSourceKind"] == "heldQuestion"]
    assert len(listHeld) == 1, listSynthesisRequests[0]["listQuotedMaterial"]
    assert "which trade-off?" in listHeld[0]["sContent"]
    assert "question-" in listHeld[0]["sContent"]


def testTheAnswerReachesTheNextRoundBesideTheQuestionsItAnswered():
    """An answer alone is unreadable to an agent that did not ask.

    The gate is discarded the moment a response is recorded, so unless
    its questions are captured first they are gone: the next round is
    handed bare prose, and one text box may be answering a dozen
    questions at once.

    Kills: appending {"sText": ...} alone (the shape before 2026-08-25),
    and quoting dictResponse["sText"] directly instead of composing it
    beside the questions.
    """
    fixture = fixtureBuildCouncil(
        LIST_THREE_SPECS,
        _ffnDecideVetoVerdict("B", "needsHuman", iRoundLimit=1,
                              listOpenQuestions=["delete it, or keep it?"]),
        sChairbotHandle="A")
    fixture.fdictDrive()
    dictOut = fixture.fdictContinue("keep it, but discourage it")

    [dictResponse] = dictOut["listResearcherResponses"]
    [dictAnswered] = dictResponse["listAnsweredQuestions"]
    assert dictAnswered["sQuestionText"] == "delete it, or keep it?"

    listSecondRound = fixture.flistRequestsFor("C", S_VETO)
    assert len(listSecondRound) > 1, "the continuation round never ran"
    listResponseQuotes = [
        dictQuoted for dictQuoted in listSecondRound[-1]["listQuotedMaterial"]
        if dictQuoted["sSourceKind"] == "researcherResponse"]
    assert listResponseQuotes, "the answer never reached the next round"
    sQuoted = listResponseQuotes[-1]["sContent"]
    assert "keep it, but discourage it" in sQuoted
    assert "delete it, or keep it?" in sQuoted, (
        "the answer arrived without the question it answers")
    assert dictAnswered["sQuestionId"] in sQuoted, (
        "the answer arrived without a handle the plan can anchor to")


def testPerDecisionAnswersAreComposedByTheServerNotTheCaller():
    """The prose is composed FROM the per-decision answers.

    Otherwise the readable record and the machine-readable one can
    describe different answers, and nothing would ever reconcile them —
    the caller sends both and only one is displayed.

    Kills: recording the caller's sResponseText when decision answers
    are present, and any unbound-name slip on this path (it was
    `agentCouncilCharter.fsComposeDecisionAnswers` with the module name
    never imported, which no other test executed).
    """
    fixture = fixtureBuildCouncil(
        LIST_THREE_SPECS,
        _ffnDecideVetoVerdict("B", "needsHuman", iRoundLimit=1,
                              listOpenQuestions=["delete it, or keep it?"]),
        sChairbotHandle="A")
    dictOut = fixture.fdictDrive()
    sQuestionId = (
        dictOut["dictPendingHumanGate"]["listQuestions"][0]["sQuestionId"])

    dictOut = fixture.fdictContinue(
        "IGNORED — the caller does not get to write the record",
        [{"sDecisionId": "decision-" + sQuestionId,
          "listQuestionIds": [sQuestionId],
          "sAnswerText": "keep it, but discourage it"}])

    [dictResponse] = dictOut["listResearcherResponses"]
    assert "IGNORED" not in dictResponse["sText"]
    assert "delete it, or keep it?" in dictResponse["sText"]
    assert "keep it, but discourage it" in dictResponse["sText"]
    assert dictResponse["listDecisionAnswers"][0]["sAnswerText"] == (
        "keep it, but discourage it")

    listSecondRound = fixture.flistRequestsFor("C", S_VETO)
    sQuoted = [dictQuoted
               for dictQuoted in listSecondRound[-1]["listQuotedMaterial"]
               if dictQuoted["sSourceKind"] == "researcherResponse"][-1][
                   "sContent"]
    assert "delete it, or keep it?" in sQuoted
    assert sQuoted.count("delete it, or keep it?") == 1, (
        "the question is stated twice: once by the composer and once by "
        "the flat heading it should have replaced")


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
            "sEmptyResultReason": "killedAtTurnWallClockBudget",
            "dictEventTypeCounts": {"assistant": 52}})
        if sHandle == "B" and dictRequest["sPhase"] == S_PROPOSAL
        else fdictDecideCompleted(fdictMakeTurnResult("accept")))
    fixture = fixtureBuildCouncil(LIST_THREE_SPECS, ffnDecide,
                                  sChairbotHandle="A")
    dictOut = fixture.fdictDrive()
    dictRecord = _fdictProposalRecordOf(fixture, dictOut, "B")

    assert dictRecord["sStatus"] == "failed"
    sReason = dictRecord["sFailureReason"]
    assert "time budget ran out" in sReason, sReason
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
                "sEmptyResultReason": "killedAtTurnWallClockBudget"})
        return fdictDecideCompleted(fdictMakeTurnResult("accept"))
    fixture = fixtureBuildCouncil(LIST_THREE_SPECS, ffnDecide,
                                  sChairbotHandle="A")
    fixture.fdictDrive()
    assert len(listAttempts) == 1, (
        f"the empty turn was retried {len(listAttempts)} times")


# ----- the implementation-council walk (2026-08-28) ---------------------


def testAnImplementationCouncilWalksPenReviewReviseVetoToPlanReady():
    """The implementation walk end to end, with the patch as candidate.

    Round 1 runs implementation (single author — the chairbot),
    conformance review by every participant, a synthesis revision, and
    the veto; a clean run lands planReady with the candidate carrying
    the patch keys. The phase order is the 2026-08-25 settled design;
    the researcher-facing button rests on this walk.
    """
    from tests.agentCouncilHarness import fdictMakePatchTurnResult

    def _ffnDecide(sHandle, dictTurnRequest):
        sPhase = dictTurnRequest["sPhase"]
        if sPhase in ("implementation", "synthesis"):
            return fdictDecideCompleted(fdictMakePatchTurnResult(
                sPatchUnifiedDiff="--- a/src/f.c\n+++ b/src/f.c\n@@ -1 +1 @@\n-x\n+y\n"))
        return fdictDecideCompleted(fdictMakeTurnResult(sVerdict="accept"))

    fixtureCouncil = fixtureBuildCouncil(
        LIST_TWO_SPECS, _ffnDecide, sChairbotHandle="alpha",
        sCampaignKind="implementation",
        sSeedPlanDocument="THE ACCEPTED PLAN: change x to y in src/f.c")
    dictSettled = fixtureCouncil.fdictDrive()

    assert dictSettled["sState"] == "planReady"
    dictRoundOne = dictSettled["listRounds"][0]
    listWalked = [sPhase for sPhase in (
        "implementation", "conformanceReview", "synthesis", "veto")
        if sPhase in dictRoundOne["dictTurnsByPhase"]
        or (sPhase == "synthesis" and dictRoundOne["bSynthesisSettled"])]
    assert listWalked == [
        "implementation", "conformanceReview", "synthesis", "veto"]
    dictCandidate = dictSettled["dictCandidatePlan"]["dictResult"]
    assert dictCandidate["sPatchUnifiedDiff"].startswith("--- a/src/f.c")
    assert dictCandidate["listFilesTouched"] == ["f"]
    # The implementation phase ran exactly one author: the chairbot.
    listImplementationTurns = dictRoundOne["dictTurnsByPhase"][
        "implementation"]
    assert len(listImplementationTurns) == 1


def testAPlanningTurnReturningPatchKeysIsRejected():
    """The patch keys stay UNKNOWN outside an implementation council.

    A planning turn that smuggles a diff must fail validation — the
    per-phase schema widens for the pen phases of an implementation
    council and nowhere else.
    """
    from vaibify.gui.agentCouncilCharter import fdictValidateTurnResult
    from tests.agentCouncilHarness import fdictMakePatchTurnResult
    dictValidation = fdictValidateTurnResult(fdictMakePatchTurnResult())
    assert not dictValidation["bValid"]
    assert any("unknown keys" in sProblem
               for sProblem in dictValidation["listProblems"])
    dictWidened = fdictValidateTurnResult(
        fdictMakePatchTurnResult(), bRequirePatch=True)
    assert dictWidened["bValid"]
    # And a patch phase REQUIRES the diff: the base shape alone fails.
    dictMissing = fdictValidateTurnResult(
        fdictMakeTurnResult(), bRequirePatch=True)
    assert not dictMissing["bValid"]


# ----- a killed runner names the exit code (2026-08-28) -----------------


def testAKilledRunnerIsNamedAsAnEnvironmentFaultNotASilentModel():
    """SIGKILL with every council bound clean is an environment fault.

    Two live round-4 synthesis turns died at 42s and 92s with exit 137
    and every bound false; the card said only "the cause is outside
    what the turn can see" while the record held the exit code all
    along. The card now states the ACQUITTAL — no council bound fired
    — and never guesses a culprit the record cannot prove.
    """
    from vaibify.gui.agentCouncil import _fsExplainEmptyTurn
    sExplanation = _fsExplainEmptyTurn("noResultEvent", {
        "jsonExitCode": 137, "bWallClockExceeded": False,
        "bOutputCapExceeded": False, "bOomKilled": False,
        "dictEventTypeCounts": {"assistant": 15}})
    assert "exit 137" in sExplanation
    assert "none of the council's own bounds fired" in sExplanation
    assert "environment fault" in sExplanation
    # It had produced work before dying — the progress line survives.
    assert "15 messages" in sExplanation


def testABoundThatDidFireKeepsItsOwnExplanationAndRemedy():
    """A wall-clock or cap kill is OURS, and must keep its remedy.

    Both exit 137. Letting the environment wording win would tell a
    researcher to fix their Docker daemon when the answer is to raise
    the turn budget.
    """
    from vaibify.gui.agentCouncil import _fsExplainEmptyTurn
    sWallClock = _fsExplainEmptyTurn("killedAtTurnWallClockBudget", {
        "jsonExitCode": 137, "bWallClockExceeded": True,
        "dictEventTypeCounts": {}})
    assert "time budget" in sWallClock
    assert "environment fault" not in sWallClock

    sOutOfMemory = _fsExplainEmptyTurn("runnerOutOfMemory", {
        "jsonExitCode": 137, "bOomKilled": True,
        "dictEventTypeCounts": {}})
    assert "ran out of memory" in sOutOfMemory
    assert "environment fault" not in sOutOfMemory


def testAnOrdinarySilentStopIsNotCalledAKill():
    """No SIGKILL, no kill wording — the classes stay distinct."""
    from vaibify.gui.agentCouncil import _fsExplainEmptyTurn
    sSilent = _fsExplainEmptyTurn("noResultEvent", {
        "jsonExitCode": 0, "dictEventTypeCounts": {}})
    assert "stopped without returning an answer" in sSilent
    assert "exit 137" not in sSilent
