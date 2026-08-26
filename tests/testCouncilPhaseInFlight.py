"""Falsification tests for reporting the phase a council is running.

A turn record exists only once its turn has SETTLED. Every view of a
live council was built from those records, so the whole of cross-review
was invisible while it ran: the newest records were the two finished
proposals, and a researcher watching correctly read it as a hang
(live report, 2026-08-25).

The engine therefore RECORDS what it is running rather than leaving a
reader to infer it. These tests drive the real engine over a fake
connection that reports what the record said WHILE its turn was in
flight — the only moment the claim can be checked, and one no assertion
made after the run can reach.
"""

import pytest

from tests.agentCouncilHarness import (
    fdictDecideCompleted,
    fdictMakeTurnResult,
    fixtureBuildCouncil,
)
from vaibify.gui.agentCouncilCampaign import (
    S_STATE_INTERRUPTED,
    S_STATE_PLANNING,
)
from vaibify.gui.agentCouncilResolution import fdictDescribeActivePhase


def _fdictBuildRound(iRoundNumber=1, sResolution=""):
    return {"iRoundNumber": iRoundNumber, "sResolution": sResolution,
            "dictTurnsByPhase": {}}


def _fdictBuildCampaign(dictPhaseInFlight, sState=S_STATE_PLANNING,
                        listRounds=None):
    return {
        "sState": sState,
        "listRounds": [_fdictBuildRound()] if listRounds is None
        else listRounds,
        "dictPhaseInFlight": dictPhaseInFlight,
    }


DICT_RUNNING_SYNTHESIS = {
    "sPhase": "synthesis",
    "iRoundNumber": 1,
    "listRunningParticipantIds": ["participant-bbbb"],
}


def testTheRunningPhaseIsReportedWithWhoIsRunningIt():
    """The reader is told the phase AND the agent, not just the phase.

    Kills: reporting the phase alone. Synthesis runs one author picked
    by a fallback chain, so a display that filled in the configured
    chairbot would name the wrong agent exactly when a substitution had
    happened — the case where being told is worth most.
    """
    dictActive = fdictDescribeActivePhase(
        _fdictBuildCampaign(DICT_RUNNING_SYNTHESIS))

    assert dictActive["sPhase"] == "synthesis"
    assert dictActive["listRunningParticipantIds"] == ["participant-bbbb"]


def testARecordFromABygoneRoundIsNotReportedAsRunning():
    """A phase record naming a resolved round is stale by construction.

    Kills: returning the stored record unchecked. A hub that died
    mid-phase leaves the record on the checkpoint, and a later hub would
    then report "synthesizing" about a council with no runner at all,
    indefinitely.
    """
    dictCampaign = _fdictBuildCampaign(
        DICT_RUNNING_SYNTHESIS,
        listRounds=[_fdictBuildRound(1, sResolution="planReady"),
                    _fdictBuildRound(2)])

    assert fdictDescribeActivePhase(dictCampaign) is None


@pytest.mark.falsification
def testAResolvedRoundIsNeverReportedAsRunningEvenAtItsOwnNumber():
    """A round that has RESOLVED is finished, whatever the record says.

    Kills: guarding on the round number alone. The bygone-round case
    above is caught by the number check as well, so removing the
    resolution check changes nothing there and the guard would sit
    undefended — which is how a guard silently goes vacuous. Here the
    number MATCHES and only the resolution says otherwise.

    Today's engine clears the record before a round can resolve, so this
    state is one it does not produce. The contract is pinned anyway: the
    clearing is what makes it unreachable, and a future engine that
    resolved a round on a different path would otherwise report a
    finished round as live with nothing to catch it.

    Kills: the reader ignoring a resolved round.
    """
    dictCampaign = _fdictBuildCampaign(
        DICT_RUNNING_SYNTHESIS,
        listRounds=[_fdictBuildRound(1, sResolution="planReady")])

    assert fdictDescribeActivePhase(dictCampaign) is None


@pytest.mark.falsification
def testAnInterruptedCampaignReportsNothingRunning():
    """Leaving the planning state falsifies the record without rewriting it.

    Kills: guarding on the round alone. The interrupted transition
    happens with the round still open and still current, so a
    round-number check passes and the dead campaign keeps claiming work.

    Kills: the reader ignoring the campaign state, so a crashed hub's
    record reads as live work.
    """
    dictCampaign = _fdictBuildCampaign(DICT_RUNNING_SYNTHESIS,
                                       sState=S_STATE_INTERRUPTED)

    assert fdictDescribeActivePhase(dictCampaign) is None


def testACampaignFromAnEarlierHubHasNoRecordAndDoesNotRaise():
    """The key is absent from every campaign checkpointed before today.

    Kills: reading dictCampaign["dictPhaseInFlight"] directly. It is
    deliberately not a required key — adding one strands checkpointed
    campaigns at restore — so every read must tolerate its absence.
    """
    dictCampaign = _fdictBuildCampaign(None)
    del dictCampaign["dictPhaseInFlight"]

    assert fdictDescribeActivePhase(dictCampaign) is None


def _tBuildCouncilObservingItselfMidTurn():
    """A real engine whose every turn reports the record from inside it.

    The claim under test is only true DURING a turn, so it is only
    checkable from inside one. An assertion made after the engine
    returns is made against a cleared record, and would pass equally for
    an engine that never wrote one — which is the shape of the bug this
    replaces.
    """
    dictHolder = {}
    listObservations = []

    def ffnDecideAndObserve(sHandle, dictTurnRequest):
        listObservations.append({
            "sParticipantId": dictTurnRequest["sParticipantId"],
            "sPhase": dictTurnRequest["sPhase"],
            "dictActive": fdictDescribeActivePhase(
                dictHolder["fixture"].dictCampaign),
        })
        return fdictDecideCompleted(fdictMakeTurnResult(sVerdict="accept"))

    dictHolder["fixture"] = fixtureBuildCouncil(
        [{"sHandle": "alpha", "sProvider": "fake",
          "sRequestedModel": "model-a"},
         {"sHandle": "beta", "sProvider": "fake",
          "sRequestedModel": "model-b"}],
        ffnDecideAndObserve, sChairbotHandle="alpha")
    return dictHolder["fixture"], listObservations


@pytest.mark.falsification
def testEveryTurnSeesItsOwnPhaseAndItsOwnNameInTheRecord():
    """Observed from inside each turn, not asserted after the run.

    Kills: writing the phase but never the running participant; writing
    either only once the phase settles; and writing the phase for the
    barrier phases but not for synthesis, whose author is chosen by a
    fallback chain rather than by the loop.

    Kills: the engine never recording the phase it runs, and never
    recording who is running it.
    """
    fixtureCouncil, listObservations = (
        _tBuildCouncilObservingItselfMidTurn())

    fixtureCouncil.fdictDrive()

    assert listObservations, "no turn ran"
    for dictObservation in listObservations:
        dictActive = dictObservation["dictActive"]
        assert dictActive is not None, (
            "nothing was recorded as running during a %s turn"
            % dictObservation["sPhase"])
        assert dictActive["sPhase"] == dictObservation["sPhase"]
        assert (dictObservation["sParticipantId"]
                in dictActive["listRunningParticipantIds"])
    assert {dictObservation["sPhase"]
            for dictObservation in listObservations} >= {
        "independentProposals", "crossReview", "synthesis"}


@pytest.mark.falsification
def testTheRecordIsClearedOnceNothingIsRunning():
    """A settled council must not keep claiming a phase.

    Kills: setting the record and never clearing it. The staleness
    guards above would not catch this one: a campaign that reached a
    human gate has an OPEN round with the current number, so only the
    clearing makes it honest.

    Kills: the engine never clearing the in-flight record.
    """
    fixtureCouncil, _ = _tBuildCouncilObservingItselfMidTurn()

    fixtureCouncil.fdictDrive()

    assert fixtureCouncil.dictCampaign["dictPhaseInFlight"] is None


@pytest.mark.parametrize("sPhase", [
    "independentProposals", "crossReview", "synthesis", "veto"])
def testEveryPhaseTheEngineRunsHasAReadableName(sPhase):
    """A phase with no label renders as its raw identifier.

    Kills: adding a phase to the protocol and not to the display map.
    The frontend falls back to the raw name, so this is a readability
    check, not a correctness one — but the raw names are camel-case
    internals a researcher should never be shown.
    """
    sScript = open(
        "vaibify/gui/static/scriptAgentCouncil.js", encoding="utf-8").read()
    iActivity = sScript.index("var DICT_PHASE_ACTIVITY")
    assert sPhase in sScript[iActivity:iActivity + 400]
