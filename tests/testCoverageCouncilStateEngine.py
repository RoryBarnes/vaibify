"""The council engine refuses what its protocol does not offer.

The engine's deliberation walks are pinned by the harness-driven engine
suite. These tests reach the protocol's refusals: an engine wired
without a connection or a callable, a researcher action taken against a
gate the campaign is not waiting on (or the wrong kind of gate), an
exhausted-round exit given an unusable answer, and a retirement asked
of an attempt that did not terminate the campaign. Each refusal must
leave the campaign record exactly as it was.
"""

import asyncio
import copy

import pytest

from vaibify.gui import agentCouncil
from vaibify.gui.agentCouncil import CouncilEngine
from vaibify.gui.agentCouncilCampaign import (
    CouncilConfigurationError,
    CouncilProtocolError,
    S_GATE_BLOCKING_QUESTION,
    S_GATE_EXHAUSTED_ROUNDS,
    S_STATE_NEEDS_HUMAN,
)
from tests.agentCouncilHarness import ffnDecideAllAccept, fixtureBuildCouncil


LIST_TWO_SPECS = [
    {"sHandle": "alpha", "sProvider": "claude", "sRequestedModel": "modelOne"},
    {"sHandle": "beta", "sProvider": "claude", "sRequestedModel": "modelTwo"},
]


def fixtureBuildIdle():
    return fixtureBuildCouncil(LIST_TWO_SPECS, ffnDecideAllAccept)


def fnParkAtGate(fixtureCouncil, dictGate):
    """Put the campaign in needsHuman behind the given gate."""
    fixtureCouncil.dictCampaign["sState"] = S_STATE_NEEDS_HUMAN
    fixtureCouncil.dictCampaign["dictPendingHumanGate"] = dictGate


# ----- construction -------------------------------------------------------


def testAnEngineRefusesAParticipantWithNoConnection():
    fixtureCouncil = fixtureBuildIdle()
    dictConnections = dict(fixtureCouncil.engine.dictConnections)
    dictConnections.pop(next(iter(dictConnections)))
    with pytest.raises(CouncilConfigurationError) as excInfo:
        CouncilEngine(fixtureCouncil.dictCampaign, dictConnections,
                      lambda dictEvent: None, lambda dictEntry: {},
                      lambda dictCampaign: None, lambda dictRequest: {})
    assert "every participant needs a provider connection" in str(
        excInfo.value)


@pytest.mark.parametrize("iMissingIndex", [0, 1, 2, 3])
def testAnEngineRefusesANonCallableCallback(iMissingIndex):
    fixtureCouncil = fixtureBuildIdle()
    listCallbacks = [lambda dictEvent: None, lambda dictEntry: {},
                     lambda dictCampaign: None, lambda dictRequest: {}]
    listCallbacks[iMissingIndex] = "not callable"
    with pytest.raises(CouncilConfigurationError) as excInfo:
        CouncilEngine(fixtureCouncil.dictCampaign,
                      fixtureCouncil.engine.dictConnections, *listCallbacks)
    assert str(excInfo.value) == "engine callbacks must be callable"


def testAnUnknownParticipantIdIsAProtocolError():
    fixtureCouncil = fixtureBuildIdle()
    with pytest.raises(CouncilProtocolError) as excInfo:
        fixtureCouncil.engine._fdictFindParticipant("participant-stranger")
    assert "unknown participant participant-stranger" in str(excInfo.value)


# ----- rejected-payload summaries ------------------------------------------


def testAnUnserializableRejectedPayloadIsDescribedRatherThanRaised():
    dictMixedKeys = {1: "numeric key", "sVerdict": "accept"}
    sSummary = agentCouncil._fsSummarizeRejectedPayload(dictMixedKeys)
    assert sSummary.startswith("<unserializable: ")


def testAnOversizedRejectedPayloadIsTruncatedAndSaysFromWhat():
    dictLarge = {"sRawResultText": "x" * 5000}
    sSummary = agentCouncil._fsSummarizeRejectedPayload(dictLarge)
    iLimit = agentCouncil.I_MAX_REJECTED_PAYLOAD_CHARACTERS
    assert sSummary.startswith('{"sRawResultText": "xxx')
    assert sSummary.endswith(" characters]")
    assert len(sSummary) < iLimit + 60


# ----- veto classification -------------------------------------------------


def testAMissingOrFailedVetoTurnIsNeverCountedAsAVerdict():
    fixtureCouncil = fixtureBuildIdle()
    for dictTurnRecord in (None, {"sStatus": "failed", "dictResult": {}}):
        assert fixtureCouncil.engine._fdictClassifyVeto(dictTurnRecord) == {
            "sVerdict": agentCouncil.S_VERDICT_UNDETERMINED,
            "sReason": "vetoTurnMissingOrFailed"}


def testAnUnrecognizedVetoVerdictIsUndeterminedAndNamed():
    fixtureCouncil = fixtureBuildIdle()
    dictClassified = fixtureCouncil.engine._fdictClassifyVeto({
        "sStatus": "completed", "dictResult": {"sVerdict": "probablyFine"}})
    assert dictClassified == {
        "sVerdict": agentCouncil.S_VERDICT_UNDETERMINED,
        "sReason": "unrecognizedVerdict: probablyFine"}


# ----- researcher actions against the wrong gate ----------------------------


def testRespondingWhenNothingIsAskedIsRefused():
    fixtureCouncil = fixtureBuildIdle()
    dictBefore = copy.deepcopy(fixtureCouncil.dictCampaign)
    with pytest.raises(CouncilProtocolError) as excInfo:
        fixtureCouncil.fdictContinue("Prefer the conservative option.")
    assert "not waiting on the researcher" in str(excInfo.value)
    assert fixtureCouncil.dictCampaign == dictBefore


def testGrantingRoundsAtAQuestionGateNamesBothGates():
    fixtureCouncil = fixtureBuildIdle()
    fnParkAtGate(fixtureCouncil, {"sGateKind": S_GATE_BLOCKING_QUESTION,
                                  "listQuestions": []})
    with pytest.raises(CouncilProtocolError) as excInfo:
        fixtureCouncil.fdictGrantResolutionRound(1)
    assert str(excInfo.value) == (
        f"this action answers a {S_GATE_EXHAUSTED_ROUNDS} gate, not "
        f"{S_GATE_BLOCKING_QUESTION}")
    assert fixtureCouncil.dictCampaign["iGrantedAdditionalRounds"] == 0


@pytest.mark.parametrize("iGrantedRounds", [0, -2])
def testAGrantOfFewerThanOneRoundIsRefused(iGrantedRounds):
    fixtureCouncil = fixtureBuildIdle()
    fnParkAtGate(fixtureCouncil, {"sGateKind": S_GATE_EXHAUSTED_ROUNDS,
                                  "listUnresolvedObjections": []})
    with pytest.raises(CouncilProtocolError) as excInfo:
        fixtureCouncil.fdictGrantResolutionRound(iGrantedRounds)
    assert "at least one round" in str(excInfo.value)
    assert fixtureCouncil.dictCampaign["sState"] == S_STATE_NEEDS_HUMAN
    assert fixtureCouncil.dictCampaign["listResearcherDecisions"] == []


@pytest.mark.parametrize("dictDispositions", [
    {}, {"objection-7": {"sAction": "ignore"}}, {"objection-7": {}}])
def testEveryUnresolvedObjectionNeedsAResolveOrOverride(dictDispositions):
    fixtureCouncil = fixtureBuildIdle()
    fnParkAtGate(fixtureCouncil, {
        "sGateKind": S_GATE_EXHAUSTED_ROUNDS,
        "listUnresolvedObjections": [{"sObjectionId": "objection-7"}]})
    with pytest.raises(CouncilProtocolError) as excInfo:
        fixtureCouncil.fdictResolveObjections(dictDispositions)
    assert "objection objection-7 needs a disposition" in str(excInfo.value)
    assert fixtureCouncil.dictCampaign["sState"] == S_STATE_NEEDS_HUMAN
    assert fixtureCouncil.dictCampaign["dictPendingHumanGate"] is not None


# ----- retirement ------------------------------------------------------------


@pytest.mark.parametrize("dictAttempt", [
    None,
    {"sAttemptState": "turnsSettled", "sOutcome": ""},
    {"sAttemptState": "outcomeSettled", "sOutcome": "advancedToNextPhase"},
])
def testOnlyACampaignTerminatingAttemptCanBeRetired(dictAttempt):
    fixtureCouncil = fixtureBuildIdle()
    fixtureCouncil.dictCampaign["listRounds"] = [
        {"iRoundNumber": 1, "dictPhaseAttempt": dictAttempt}]
    dictBefore = copy.deepcopy(fixtureCouncil.dictCampaign)
    with pytest.raises(CouncilProtocolError) as excInfo:
        fixtureCouncil.engine.fdictRetireTerminalAttempt()
    assert "only an attempt whose settled outcome terminated" in str(
        excInfo.value)
    assert fixtureCouncil.dictCampaign == dictBefore


def testTheEngineWalkStillReachesAPlanAfterTheseRefusals():
    """A control: the refusals above are refusals, not a broken engine."""
    fixtureCouncil = fixtureBuildIdle()
    dictResult = asyncio.run(fixtureCouncil.engine.fdictRunUntilBlocked())
    assert dictResult["sState"] in ("planReady", "needsHuman")
