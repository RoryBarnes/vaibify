"""Falsification tests for grouping a gate into decision points.

Twenty-one questions in one flat list, several of them the same question
asked twice, is a gate a researcher cannot answer. The grouping turns
them into DECISIONS, ordered by how much of the council raised each one.

The tier is computed here, never asked of a model: a model asked "did
every agent raise this?" can answer wrong about its own peers and
nothing could check it, whereas a distinct-author count is a fact. These
tests assert that computation against rosters where the counts differ,
so a classifier that ignored the roster could not pass.
"""

from vaibify.gui.agentCouncilResolution import (
    flistGroupGateQuestionsIntoDecisions,
)


def _fdictBuildQuestion(sQuestionId, sParticipantId, sText):
    return {"sQuestionId": sQuestionId,
            "sRaisedByParticipantId": sParticipantId,
            "sQuestionText": sText}


def _fdictBuildCampaign(listQuestions, listPlanItems, iParticipants=2,
                        listSynthesisQuestions=None):
    """A campaign at a blocking-question gate.

    Participant ids are deliberately unlike the question ids and unlike
    their display order: a grouping that keyed on position rather than
    identity would pass a fixture where those agree.
    """
    return {
        "listParticipants": [
            {"sParticipantId": "participant-%s" % ("abcdef"[iIndex] * 4)}
            for iIndex in range(iParticipants)],
        "dictPendingHumanGate": {
            "sGateKind": "blockingQuestion",
            "iRoundNumber": 1,
            "listQuestions": listQuestions,
        },
        "dictCandidatePlan": {"dictResult": {"listPlanItems": listPlanItems}},
        "listRounds": [{
            "iRoundNumber": 1,
            "dictTurnsByPhase": {"synthesis": [{"dictResult": {
                "listOpenQuestions": listSynthesisQuestions or []}}]},
        }],
    }


def testTwoAgentsAskingOneThingBecomeOneDecision():
    """A question the pen-holder placed on one item is asked once.

    Kills: emitting one decision per question. The researcher then
    answers the same Euler question twice, which is the flat gate this
    grouping replaces.
    """
    listQuestions = [
        _fdictBuildQuestion("question-aaa", "participant-aaaa",
                            "delete it, or discourage it?"),
        _fdictBuildQuestion("question-bbb", "participant-bbbb",
                            "hard-remove, or one release of warning?"),
    ]
    listDecisions = flistGroupGateQuestionsIntoDecisions(
        _fdictBuildCampaign(
            listQuestions,
            ["PHASE 8 — disposition. question-aaa question-bbb"]))

    assert len(listDecisions) == 1
    assert len(listDecisions[0]["listQuestions"]) == 2
    assert listDecisions[0]["listPlanItemIndexes"] == [0]
    assert listDecisions[0]["sTier"] == "raisedByAll"


def testTheTierComesFromTheRosterNotFromTheQuestionCount():
    """Two authors is ALL of two agents and SEVERAL of three.

    Kills: classifying on the number of questions in a group, or on any
    fixed threshold. The group is identical in both campaigns here — only
    the roster differs — so a classifier that never reads the roster
    returns the same tier twice and fails.
    """
    listQuestions = [
        _fdictBuildQuestion("question-aaa", "participant-aaaa", "which?"),
        _fdictBuildQuestion("question-bbb", "participant-bbbb", "which?"),
    ]
    listPlanItems = ["PHASE 1 question-aaa question-bbb"]

    [dictAmongTwo] = flistGroupGateQuestionsIntoDecisions(
        _fdictBuildCampaign(listQuestions, listPlanItems, iParticipants=2))
    [dictAmongThree] = flistGroupGateQuestionsIntoDecisions(
        _fdictBuildCampaign(listQuestions, listPlanItems, iParticipants=3))

    assert dictAmongTwo["sTier"] == "raisedByAll"
    assert dictAmongThree["sTier"] == "raisedBySeveral"


def testThePenHoldersOwnQuestionsSortLastAndAreNamedAsIts():
    """A question raised writing the plan is tier 4, however many agents.

    Kills: testing "raised by the chairbot" instead of "raised during
    synthesis". The chairbot also proposes and cross-reviews, so the
    same participant id appears in both tiers here — a chairbot-identity
    test puts the proposal question in tier 4 and fails.
    """
    listQuestions = [
        _fdictBuildQuestion("question-aaa", "participant-aaaa",
                            "asked while proposing"),
        _fdictBuildQuestion("question-zzz", "participant-aaaa",
                            "asked while writing the plan"),
    ]
    listDecisions = flistGroupGateQuestionsIntoDecisions(
        _fdictBuildCampaign(
            listQuestions, ["PHASE 1 question-aaa"],
            listSynthesisQuestions=["asked while writing the plan"]))

    assert [dictDecision["sTier"] for dictDecision in listDecisions] == [
        "raisedByOne", "raisedDuringSynthesis"]


def testAQuestionNoPlanItemMentionsIsStillPresented():
    """An unplaceable question is shown unplaced, never dropped.

    Kills: building the decision list from the plan items instead of
    from the questions. A question the pen-holder forgot to anchor then
    vanishes — and a question missing from the gate is one the
    researcher was never asked.
    """
    listQuestions = [
        _fdictBuildQuestion("question-aaa", "participant-aaaa", "anchored"),
        _fdictBuildQuestion("question-ccc", "participant-bbbb", "orphaned"),
    ]
    listDecisions = flistGroupGateQuestionsIntoDecisions(
        _fdictBuildCampaign(listQuestions, ["PHASE 1 question-aaa"]))

    listTexts = [dictQuestion["sQuestionText"]
                 for dictDecision in listDecisions
                 for dictQuestion in dictDecision["listQuestions"]]
    assert sorted(listTexts) == ["anchored", "orphaned"]
    [dictOrphan] = [dictDecision for dictDecision in listDecisions
                    if not dictDecision["listPlanItemIndexes"]]
    assert dictOrphan["listQuestions"][0]["sQuestionText"] == "orphaned"


def testGroupingIsTransitiveAcrossPlanItems():
    """Items {a,b} and {b,c} are one decision of three, not two of two.

    Kills: grouping per plan item independently, which asks the
    researcher about question b twice under two different headings.
    """
    listQuestions = [
        _fdictBuildQuestion("question-aaa", "participant-aaaa", "a"),
        _fdictBuildQuestion("question-bbb", "participant-bbbb", "b"),
        _fdictBuildQuestion("question-ccc", "participant-aaaa", "c"),
    ]
    listDecisions = flistGroupGateQuestionsIntoDecisions(
        _fdictBuildCampaign(
            listQuestions,
            ["PHASE 1 question-aaa question-bbb",
             "PHASE 2 question-bbb question-ccc"]))

    assert len(listDecisions) == 1
    assert len(listDecisions[0]["listQuestions"]) == 3
    assert listDecisions[0]["listPlanItemIndexes"] == [0, 1]


def testANonQuestionGateIsLeftAlone():
    """An exhausted-round gate has no questions to group.

    Kills: grouping on any gate kind. The exhausted gate carries
    unresolved OBJECTIONS with their own three-exit renderer, and a
    decision list built there would put a second answer control on it.
    """
    dictCampaign = _fdictBuildCampaign([], [])
    dictCampaign["dictPendingHumanGate"]["sGateKind"] = "exhaustedRounds"
    assert flistGroupGateQuestionsIntoDecisions(dictCampaign) == []
