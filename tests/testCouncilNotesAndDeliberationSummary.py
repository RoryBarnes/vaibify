"""Falsification tests for charter 1.7.0's two additions.

Both exist because a council had no structure for something it kept
needing to say.

The NOTES channel: charter 1.5.0 told participants in prose that a
finding worth the researcher's attention but not their decision belongs
in the evidence and the plan document, "never raised as a question". A
live gate under 1.6.0 then carried four items whose text literally opens
"Emphasis, not a decision: ..." — the agents had understood the rule
well enough to say so in their own words and raised them as questions
anyway, because prose named no field they could put one in. These tests
defend the field, its validation, its compatibility with campaigns
convened before it existed, and its arrival beside the gate rather than
inside it.

The DELIBERATION SUMMARY: a council that exhausted its rounds without
converging used to simply stop, handing the researcher an objection list
and nothing that said what the argument had been about. These tests
defend that the closing turn happens, that it is never filed or named as
a plan, that a council which DID converge grows no such round, and that
the researcher's exits survive the summary turn failing.
"""

import pytest

from tests.agentCouncilHarness import (
    fdictDecideCompleted,
    fdictDecideRaise,
    fdictMakeDeliberationSummaryResult,
    fdictMakeTurnResult,
    fixtureBuildCouncil,
)
from vaibify.gui import agentCouncilCharter
from vaibify.gui.agentCouncilCharter import (
    S_CHARTER_TEXT,
    S_CHARTER_VERSION,
    S_NOTED_FINDINGS_KEY,
    S_PHASE_DELIBERATION_SUMMARY,
    fbCharterAsksForNotedFindings,
    fdictValidateTurnResult,
)
from vaibify.gui.agentCouncilResolution import flistDescribeNotedFindings

LIST_TWO_SPECS = [
    {"sHandle": "chair", "sProvider": "prov-a", "sRequestedModel": "model-a"},
    {"sHandle": "peer", "sProvider": "prov-b", "sRequestedModel": "model-b"},
]

S_NOTE_TEXT = ("the tolerance constant is duplicated in two modules; "
               "resolved by inspection, recorded so it is not forgotten")


def _fdictDecideWithNotes(sHandle, dictTurnRequest):
    """Every turn accepts and carries one noted finding."""
    if dictTurnRequest["sPhase"] == S_PHASE_DELIBERATION_SUMMARY:
        return fdictDecideCompleted(fdictMakeDeliberationSummaryResult(
            sVerdict="needsHuman", listNotedFindings=[S_NOTE_TEXT]))
    return fdictDecideCompleted(fdictMakeTurnResult(
        sVerdict="accept", listNotedFindings=[S_NOTE_TEXT]))


# ----- the notes channel ---------------------------------------------

def testTheCharterNamesTheDestinationClauseSixUsedToLack():
    """Prose failed twice; the clause must now name a field.

    Kills: bumping the version without changing the clause, or adding
    the schema key without telling participants it exists.
    """
    assert S_CHARTER_VERSION == "1.7.0"
    assert S_NOTED_FINDINGS_KEY in S_CHARTER_TEXT
    # Clause 6 must point AT the field, not merely mention it somewhere
    # in the appended schema template.
    iClauseSix = S_CHARTER_TEXT.find("6. Escalate genuine judgment calls")
    iClauseSeven = S_CHARTER_TEXT.find("7. Structured output")
    assert 0 < iClauseSix < iClauseSeven
    sClauseSix = S_CHARTER_TEXT[iClauseSix:iClauseSeven]
    assert S_NOTED_FINDINGS_KEY in sClauseSix, sClauseSix
    assert "never in a question" in sClauseSix


@pytest.mark.falsification
def testAMalformedNoteIsRefusedNotSilentlyDropped():
    """A note the reader cannot read must fail the turn, loudly.

    The whole point of a structured channel is that the researcher sees
    what a participant put in it. A note returned as a number, or the
    field returned as a bare string, would be dropped by any renderer
    and the finding would vanish with nothing reporting the loss —
    which is the exact failure mode the question channel had.

    Kills: accepting the key without shape-checking it.
    """
    dictBareString = fdictMakeTurnResult()
    dictBareString[S_NOTED_FINDINGS_KEY] = "one note, unwrapped"
    dictOutcome = fdictValidateTurnResult(dictBareString, bRequireNotes=True)
    assert dictOutcome["bValid"] is False
    assert any(S_NOTED_FINDINGS_KEY in sProblem
               for sProblem in dictOutcome["listProblems"])

    dictNonString = fdictMakeTurnResult(listNotedFindings=[])
    dictNonString[S_NOTED_FINDINGS_KEY] = [17]
    assert fdictValidateTurnResult(
        dictNonString, bRequireNotes=True)["bValid"] is False

    # And the shape check does not depend on the field being REQUIRED:
    # a campaign convened before the field existed can still return a
    # malformed one, and it is refused there too.
    assert fdictValidateTurnResult(
        dictNonString, bRequireNotes=False)["bValid"] is False


@pytest.mark.falsification
def testACampaignConvenedUnderAnOlderCharterKeepsWorking():
    """A turn is judged against the contract it was HANDED.

    A campaign persists the exact charter text its participants
    received. Requiring a key that text never named would fail every
    turn of a resumed council — and the failure would look like a
    misbehaving model, not like a hub that changed the rules under it.

    The pair is symmetric on purpose: the same result object is INVALID
    under a charter that asks for the field and VALID under one that
    does not, so a mutation making the key unconditionally required or
    unconditionally optional kills exactly one of the two assertions.

    Kills: adding listNotedFindings to LIST_TURN_RESULT_ARRAY_KEYS.
    """
    dictWithoutNotes = fdictMakeTurnResult()
    del dictWithoutNotes[S_NOTED_FINDINGS_KEY]

    dictCurrent = {"sCharterText": S_CHARTER_TEXT}
    assert fbCharterAsksForNotedFindings(dictCurrent) is True
    assert fdictValidateTurnResult(
        dictWithoutNotes,
        bRequireNotes=fbCharterAsksForNotedFindings(dictCurrent),
    )["bValid"] is False

    dictOlder = {"sCharterText": "COUNCIL CHARTER (version 1.6.0)\n1. ..."}
    assert fbCharterAsksForNotedFindings(dictOlder) is False
    assert fdictValidateTurnResult(
        dictWithoutNotes,
        bRequireNotes=fbCharterAsksForNotedFindings(dictOlder),
    )["bValid"] is True


@pytest.mark.falsification
def testAnOlderCharterCouncilStillReachesAPlan():
    """The compatibility claim, driven through the real engine.

    A validator unit test proves the predicate; this proves the engine
    consults it. The campaign's recorded charter is replaced with a
    pre-1.7.0 one and every participant returns the schema THAT charter
    asked for — no notes key anywhere — and the council must still
    reach a plan rather than failing every turn.

    Kills: passing a constant True for bRequireNotes at the call site.
    """
    def _fdictDecideOldSchema(sHandle, dictTurnRequest):
        dictResult = fdictMakeTurnResult(sVerdict="accept")
        del dictResult[S_NOTED_FINDINGS_KEY]
        return fdictDecideCompleted(dictResult)

    fixtureCouncil = fixtureBuildCouncil(
        LIST_TWO_SPECS, _fdictDecideOldSchema, sChairbotHandle="chair")
    fixtureCouncil.dictCampaign["sCharterText"] = (
        "COUNCIL CHARTER (version 1.6.0) — the pre-notes contract")
    fixtureCouncil.dictCampaign["sCharterVersion"] = "1.6.0"

    dictOut = fixtureCouncil.fdictDrive()

    assert dictOut["sState"] == "planReady", dictOut["sState"]
    for dictRound in dictOut["listRounds"]:
        for listTurns in dictRound["dictTurnsByPhase"].values():
            for dictTurn in listTurns:
                assert dictTurn["sStatus"] == "completed", dictTurn


@pytest.mark.falsification
def testNotesReachTheResearcherWithoutBecomingQuestions():
    """A note is read beside the gate and asked as nothing.

    The defect this replaces: a participant with a finding and no field
    for it put the finding in listOpenQuestions, and the researcher was
    asked to decide something nobody needed decided.

    Kills: dropping the notes derivation, or routing notes into the
    gate's question list.
    """
    def _fdictDecide(sHandle, dictTurnRequest):
        if dictTurnRequest["sPhase"] == "veto" and sHandle == "peer":
            return fdictDecideCompleted(fdictMakeTurnResult(
                sVerdict="needsHuman",
                listNotedFindings=[S_NOTE_TEXT],
                listOpenQuestions=["Which tolerance is authoritative?"]))
        return _fdictDecideWithNotes(sHandle, dictTurnRequest)

    fixtureCouncil = fixtureBuildCouncil(
        LIST_TWO_SPECS, _fdictDecide, sChairbotHandle="chair")
    dictOut = fixtureCouncil.fdictDrive()

    assert dictOut["sState"] == "needsHuman"
    listNoted = flistDescribeNotedFindings(dictOut)
    assert [dictNote["sNoteText"] for dictNote in listNoted] == [S_NOTE_TEXT]
    assert listNoted[0]["sRaisedByParticipantId"] in {
        dictParticipant["sParticipantId"]
        for dictParticipant in dictOut["listParticipants"]}

    listQuestionTexts = [
        dictQuestion["sQuestionText"]
        for dictQuestion in dictOut["dictPendingHumanGate"]["listQuestions"]]
    assert S_NOTE_TEXT not in listQuestionTexts, (
        "a noted finding leaked into the channel that asks for answers")


def testTheSameNoteFromSeveralTurnsIsReadOnce():
    """Deduplication, so a note repeated each round is not a wall.

    Every participant in this council returns the same note on every
    turn; the researcher must see it once.
    """
    fixtureCouncil = fixtureBuildCouncil(
        LIST_TWO_SPECS, _fdictDecideWithNotes, sChairbotHandle="chair")
    dictOut = fixtureCouncil.fdictDrive()

    assert len(flistDescribeNotedFindings(dictOut)) == 1


def testACampaignWithNoNotesOffersNoNotesPanel():
    """The other half of the pair: nothing to say renders nothing."""
    fixtureCouncil = fixtureBuildCouncil(
        LIST_TWO_SPECS,
        lambda sHandle, dictRequest: fdictDecideCompleted(
            fdictMakeTurnResult(sVerdict="accept")),
        sChairbotHandle="chair")

    assert flistDescribeNotedFindings(fixtureCouncil.fdictDrive()) == []


def testTheNotedFindingsReachThePlanDocument():
    """Clause 6's other named destination.

    The gate is transient; the plan document outlives it, and the
    charter promises the note lands in both.
    """
    from vaibify.gui import agentCouncilController

    sMarkdown = agentCouncilController.fsComposePlanMarkdown(
        {"sQuestion": "q", "dictProjectIdentity": {},
         "listParticipants": [], "listRounds": [],
         "listResearcherDecisions": []},
        {"dictResult": {"sSummary": "the summary",
                        "listNotedFindings": [S_NOTE_TEXT]}})

    assert "Noted findings" in sMarkdown
    assert "not decided" in sMarkdown
    assert S_NOTE_TEXT in sMarkdown


# ----- how a council ends --------------------------------------------

def testAConvergingCouncilWritesAPlanAndNoDeliberationSummary():
    """The convergent ending, CHECKED rather than assumed.

    A council that reaches consensus inside its budget ends with the
    chairbot holding the pen: the candidate the voters then accept
    carries the chairbot's authorship, and no summary round is opened
    — a summary is what a council writes when it has no plan, and one
    written beside an accepted plan would say the opposite of the
    truth.

    Deliberately NOT marked falsification, and the distinction is the
    point. The summary round is reachable only from the exhausted
    branch of ``_fdictEnsureOpenRound``, and a converged council leaves
    the walk at planReady before that branch is ever consulted again —
    so no mutation of the summary machinery makes this test fail, and
    calling it a falsification would be claiming a guard it does not
    hold. It is a PIN on the convergent ending: it records what the
    engine does today so a future change to the ending has to change
    this file too.

    One caveat this test states rather than hides: the chairbot is the
    last AUTHOR, not the last turn. The veto turns run after it — they
    judge the candidate and write nothing — so "the chairbot goes last"
    is true of authorship and false of chronology.
    """
    fixtureCouncil = fixtureBuildCouncil(
        LIST_TWO_SPECS,
        lambda sHandle, dictRequest: fdictDecideCompleted(
            fdictMakeTurnResult(sVerdict="accept")),
        sChairbotHandle="chair")
    dictOut = fixtureCouncil.fdictDrive()

    assert dictOut["sState"] == "planReady"
    assert dictOut.get("dictDeliberationSummary") is None
    assert dictOut["dictCandidatePlan"]["sSynthesisAuthorId"] == (
        fixtureCouncil.dictHandleToId["chair"])
    assert dictOut["dictCandidatePlan"]["bChairbotSubstituted"] is False
    # Last AUTHOR, not last turn: the frozen voters veto after the pen
    # is put down, and they author nothing.
    assert fixtureCouncil.recorder.listOrderLog[-1][1] == "veto"
    assert fixtureCouncil.flistOrderHandles("synthesis") == ["chair"]
    for dictRound in dictOut["listRounds"]:
        assert not dictRound.get("bDeliberationSummaryRound"), dictRound
        assert S_PHASE_DELIBERATION_SUMMARY not in dictRound[
            "dictTurnsByPhase"]


@pytest.mark.falsification
def testAnExhaustedCouncilEndsWithAChairbotDeliberationSummary():
    """The non-convergent ending: a summary, then the researcher's exits.

    Before this, a council whose rounds ran out simply stopped at the
    exhausted gate with an objection list and no account of the
    argument that produced it.

    Kills: removing the summary round, or opening the exhausted gate
    without running it.
    """
    def _fdictDecide(sHandle, dictTurnRequest):
        if dictTurnRequest["sPhase"] == S_PHASE_DELIBERATION_SUMMARY:
            return fdictDecideCompleted(fdictMakeDeliberationSummaryResult(
                sVerdict="needsHuman",
                sSummary="the council split on the tolerance"))
        if dictTurnRequest["sPhase"] == "veto" and sHandle == "peer":
            return fdictDecideCompleted(fdictMakeTurnResult(
                sVerdict="blockingObjection",
                listBlockingObjections=["the prior is unjustified"]))
        return fdictDecideCompleted(fdictMakeTurnResult(sVerdict="accept"))

    fixtureCouncil = fixtureBuildCouncil(
        LIST_TWO_SPECS, _fdictDecide, dictSettings={"iMaximumRounds": 1},
        sChairbotHandle="chair")
    dictOut = fixtureCouncil.fdictDrive()

    assert dictOut["sState"] == "needsHuman"
    assert dictOut["dictPendingHumanGate"]["sGateKind"] == "exhaustedRounds"
    assert dictOut["dictPendingHumanGate"]["listUnresolvedObjections"]

    dictSummary = dictOut["dictDeliberationSummary"]
    assert dictSummary is not None, "the council stopped without a summary"
    assert dictSummary["sAuthorParticipantId"] == (
        fixtureCouncil.dictHandleToId["chair"])
    assert dictSummary["dictResult"]["sSummary"] == (
        "the council split on the tolerance")
    assert dictSummary["dictResult"]["listPointsOfDisagreement"]

    dictSummaryRound = dictOut["listRounds"][-1]
    assert dictSummaryRound["bDeliberationSummaryRound"] is True
    assert dictSummaryRound["sResolution"] == "deliberationSummarised"
    assert fixtureCouncil.flistOrderHandles(
        S_PHASE_DELIBERATION_SUMMARY) == ["chair"]


@pytest.mark.falsification
def testTheSummaryIsNeverFiledAsACandidatePlan():
    """It is a deliberation summary; no reader may take it for a plan.

    Filing it in dictCandidatePlan would make the Plan tab, the plan.md
    composer and every veto quote present a consensus that was never
    reached — the single most damaging thing this feature could do.

    Kills: writing the summary result into dictCandidatePlan.
    """
    def _fdictDecide(sHandle, dictTurnRequest):
        if dictTurnRequest["sPhase"] == S_PHASE_DELIBERATION_SUMMARY:
            return fdictDecideCompleted(fdictMakeDeliberationSummaryResult(
                sVerdict="needsHuman", sSummary="THE SUMMARY TEXT"))
        if dictTurnRequest["sPhase"] == "synthesis":
            return fdictDecideCompleted(fdictMakeTurnResult(
                sVerdict="accept", sSummary="THE CANDIDATE TEXT"))
        if dictTurnRequest["sPhase"] == "veto" and sHandle == "peer":
            return fdictDecideCompleted(fdictMakeTurnResult(
                sVerdict="blockingObjection",
                listBlockingObjections=["unresolved"]))
        return fdictDecideCompleted(fdictMakeTurnResult(sVerdict="accept"))

    fixtureCouncil = fixtureBuildCouncil(
        LIST_TWO_SPECS, _fdictDecide, dictSettings={"iMaximumRounds": 1},
        sChairbotHandle="chair")
    dictOut = fixtureCouncil.fdictDrive()

    assert dictOut["dictCandidatePlan"]["dictResult"]["sSummary"] == (
        "THE CANDIDATE TEXT")
    assert dictOut["dictDeliberationSummary"]["dictResult"]["sSummary"] == (
        "THE SUMMARY TEXT")


@pytest.mark.falsification
def testAFailedSummaryStillLeavesTheResearcherTheirExits():
    """The exits must not depend on one more provider turn succeeding.

    The summary runs on a council that has already spent its budget,
    which is precisely the moment a provider turn is most likely to
    fail. A researcher whose exhausted gate never opened because the
    closing turn died would have a council that simply stopped — the
    defect this feature exists to remove, reintroduced by its own fix.

    It also asserts nobody was RETIRED: every other phase retires a
    participant whose turn fails, and doing that here emptied the
    roster, so the final veto the researcher may then request met a
    quorum shortfall instead of voters.

    Kills: transitioning to failed when no author can summarise, and
    marking the summary's authors bFailed.
    """
    def _fdictDecide(sHandle, dictTurnRequest):
        if dictTurnRequest["sPhase"] == S_PHASE_DELIBERATION_SUMMARY:
            return fdictDecideRaise("the closing turn died")
        if dictTurnRequest["sPhase"] == "veto" and sHandle == "peer":
            return fdictDecideCompleted(fdictMakeTurnResult(
                sVerdict="blockingObjection",
                listBlockingObjections=["unresolved"]))
        return fdictDecideCompleted(fdictMakeTurnResult(sVerdict="accept"))

    fixtureCouncil = fixtureBuildCouncil(
        LIST_TWO_SPECS, _fdictDecide, dictSettings={"iMaximumRounds": 1},
        sChairbotHandle="chair")
    dictOut = fixtureCouncil.fdictDrive()

    assert dictOut["sState"] == "needsHuman", dictOut["sState"]
    assert dictOut["dictPendingHumanGate"]["sGateKind"] == "exhaustedRounds"
    assert dictOut.get("dictDeliberationSummary") is None
    assert dictOut["listRounds"][-1]["sResolution"] == (
        "deliberationSummaryUnavailable")
    for dictParticipant in dictOut["listParticipants"]:
        assert dictParticipant["bFailed"] is False, dictParticipant


@pytest.mark.falsification
def testTheSummaryRoundDoesNotSpendTheGrantedBudget():
    """A granted round must buy DELIBERATION, not another summary.

    The summary round is a real entry in listRounds. Counted against
    the budget it would consume the grant the researcher just made, and
    the council would answer "grant me a round" by writing a second
    summary and stopping again.

    Kills: counting summary rounds in the round budget.
    """
    def _fdictDecide(sHandle, dictTurnRequest):
        if dictTurnRequest["sPhase"] == S_PHASE_DELIBERATION_SUMMARY:
            return fdictDecideCompleted(fdictMakeDeliberationSummaryResult(
                sVerdict="needsHuman"))
        if dictTurnRequest["sPhase"] == "veto" and sHandle == "peer":
            return fdictDecideCompleted(fdictMakeTurnResult(
                sVerdict="blockingObjection",
                listBlockingObjections=["unresolved"]))
        return fdictDecideCompleted(fdictMakeTurnResult(sVerdict="accept"))

    fixtureCouncil = fixtureBuildCouncil(
        LIST_TWO_SPECS, _fdictDecide, dictSettings={"iMaximumRounds": 1},
        sChairbotHandle="chair")
    fixtureCouncil.fdictDrive()

    dictOut = fixtureCouncil.fdictGrantResolutionRound(1)

    listDeliberating = [dictRound for dictRound in dictOut["listRounds"]
                        if not dictRound.get("bDeliberationSummaryRound")]
    assert len(listDeliberating) == 2, (
        "the grant bought no deliberating round")
    # And the second exhaustion earns its OWN summary of the argument
    # as it then stands, rather than reusing the first.
    listSummaryRounds = [dictRound for dictRound in dictOut["listRounds"]
                         if dictRound.get("bDeliberationSummaryRound")]
    assert len(listSummaryRounds) == 2, listSummaryRounds
    assert dictOut["sState"] == "needsHuman"


def testTheSummaryPhaseIsToldItIsNotWritingAPlan():
    """The instruction channel carries the ruling, not just the code."""
    sInstruction = agentCouncilCharter.fsComposeTurnInstruction(
        {"sCharterText": S_CHARTER_TEXT, "dictProjectIdentity": {}},
        {"sRole": ""}, S_PHASE_DELIBERATION_SUMMARY)

    assert "DELIBERATION SUMMARY" in sInstruction
    assert "never a plan" in sInstruction
    for sKeyName in agentCouncilCharter.LIST_SUMMARY_RESULT_ARRAY_KEYS:
        assert sKeyName in sInstruction, sKeyName


def testTheSummaryAuthorSeesTheWholeDeliberation():
    """A summary of an argument it was not shown would be invented.

    The summary round holds no turns of its own, so a per-round quote
    would hand the pen-holder an empty page — and an invented account
    of a deliberation reads exactly like a real one.
    """
    listSeen = []

    def _fdictDecide(sHandle, dictTurnRequest):
        if dictTurnRequest["sPhase"] == S_PHASE_DELIBERATION_SUMMARY:
            listSeen.append(dictTurnRequest["listQuotedMaterial"])
            return fdictDecideCompleted(fdictMakeDeliberationSummaryResult(
                sVerdict="needsHuman"))
        if dictTurnRequest["sPhase"] == "veto" and sHandle == "peer":
            return fdictDecideCompleted(fdictMakeTurnResult(
                sVerdict="blockingObjection",
                listBlockingObjections=["THE OBJECTION"]))
        return fdictDecideCompleted(fdictMakeTurnResult(
            sVerdict="accept", sSummary="MY POSITION FROM " + sHandle))

    fixtureCouncil = fixtureBuildCouncil(
        LIST_TWO_SPECS, _fdictDecide, dictSettings={"iMaximumRounds": 1},
        sChairbotHandle="chair")
    fixtureCouncil.fdictDrive()

    assert listSeen, "the summary turn never ran"
    sQuoted = "".join(dictEntry["sContent"] for dictEntry in listSeen[0])
    assert "MY POSITION FROM peer" in sQuoted
    assert "THE OBJECTION" in sQuoted
