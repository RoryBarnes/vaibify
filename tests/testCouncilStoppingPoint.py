"""Falsification tests for the stopping-point descriptor.

The descriptor is the listing's answer to "what was this doing when it
stopped, and can it go on" (continuation plan sections 0.3 and 4.1).
Each wrong answer here has already cost a researcher something real: an
accepted campaign reading as resumable offers continuation of a council
that is finished; a fixed-order failed-phase scan blamed a tolerated
proposal failure for a death synthesis caused; and a phase-order mirror
nobody checks is a second authority waiting to drift.
"""

import pytest

from tests.agentCouncilHarness import (
    fdictDecideCompleted,
    fdictMakeTurnResult,
    fixtureBuildCouncil,
)
from vaibify.gui.agentCouncilResolution import (
    LIST_FIRST_ROUND_PHASES,
    LIST_LATER_ROUND_PHASES,
    fdictDescribeStoppingPoint,
)

LIST_TWO_SPECS = [
    {"sHandle": "alpha", "sProvider": "fake", "sRequestedModel": "model-a"},
    {"sHandle": "beta", "sProvider": "fake", "sRequestedModel": "model-b"},
]


def _ffnDecideAllAccept(sHandle, dictTurnRequest):
    return fdictDecideCompleted(fdictMakeTurnResult(sVerdict="accept"))


def _fdictDriveToAccepted():
    """Drive a real council to acceptance and return its durable record."""
    fixtureCouncil = fixtureBuildCouncil(
        LIST_TWO_SPECS, _ffnDecideAllAccept, sChairbotHandle="alpha")
    fixtureCouncil.fdictDrive()
    dictAccepted = fixtureCouncil.engine.fdictAcceptPlan()
    # Make the record resumable-shaped in every OTHER respect, so the
    # only thing standing between an accepted campaign and a Resume
    # button is the terminal-state check this test defends.
    dictAccepted.setdefault("dictProjectIdentity", {})[
        "sSnapshotIdentity"] = "sealed-content-identity-0001"
    return dictAccepted


@pytest.mark.falsification
def testAnAcceptedCampaignIsFinishedNotResumable():
    """Acceptance is terminal BY CHOICE; the listing must say so.

    Acceptance transitions planAccepted -> awaitingImplementation
    inside one call, so the PERSISTED state of every accepted campaign
    is the successor state — a terminal set holding only planAccepted
    matches no accepted campaign that ever reached disk, and every one
    of them read as resumable.

    The record is driven through the real engine to acceptance, never
    hand-built, and the assertion names the terminal reason: the
    snapshot and coherence guards below the terminal check also answer
    "not resumable", so bResumable alone cannot identify this cause.

    Kills: dropping awaitingImplementation from SET_TERMINAL_BY_CHOICE.
    """
    dictStopping = fdictDescribeStoppingPoint(_fdictDriveToAccepted())

    assert dictStopping["sState"] == "awaitingImplementation"
    assert dictStopping["bResumable"] is False
    assert "finished" in dictStopping["sBlockedReason"]


@pytest.mark.falsification
def testTheDescriptorBlamesNoPhaseItCannotProve():
    """A turn-record scan cannot say which phase KILLED a campaign.

    A participant failing during proposals is tolerated — marked
    bFailed, dropped from the active set, council continues — so when
    synthesis later kills the campaign, a fixed-order scan of the turn
    records blames proposals. Until the durable phase-attempt record
    exists (continuation plan section 2), the descriptor must report
    nothing it would have to guess: no failed-phase field at all.

    Kills: reintroducing a failed-phase attribution into the
    descriptor.
    """
    dictRecord = {
        "sState": "failed",
        "dictProjectIdentity": {
            "sSnapshotIdentity": "sealed-content-identity-0001"},
        "listRounds": [{
            "iRoundNumber": 1,
            "sResolution": "",
            "dictTurnsByPhase": {
                # The tolerated failure a fixed-order scan blames:
                "independentProposals": [
                    {"sStatus": "failed"}, {"sStatus": "completed"}],
                "crossReview": [{"sStatus": "completed"}],
                # The failure that actually killed the campaign:
                "synthesis": [{"sStatus": "failed"}],
            },
        }],
    }

    dictStopping = fdictDescribeStoppingPoint(dictRecord)

    assert "sFailedPhase" not in dictStopping
    assert "bRequiresRetry" not in dictStopping


@pytest.mark.falsification
def testAFailedCampaignIsNotOfferedAnActionTheRouteRefuses():
    """The listing offers only what the resume route would admit.

    A failed campaign's attempt record is coherent (outcomeSettled,
    transitioned:failed), so every record-level check passes — but its
    recovery action is Retry, which is not built, and the resume route
    refuses non-planning states. A listing that says "Can be
    continued" over a route that answers 409 is the
    answer-box-over-a-dead-runtime defect generalized.

    Kills: the descriptor skipping the state admission and deriving
    resumability from record coherence alone.
    """
    dictRecord = {
        "sState": "failed",
        "dictProjectIdentity": {
            "sSnapshotIdentity": "sealed-content-identity-0001"},
        "listRounds": [{
            "iRoundNumber": 1,
            "sResolution": "synthesisFailed",
            "dictTurnsByPhase": {
                "independentProposals": [{"sStatus": "completed"}],
                "crossReview": [{"sStatus": "completed"}],
                "synthesis": [{"sStatus": "failed"}],
            },
            "dictPhaseAttempt": {
                "sPhase": "synthesis", "iRoundNumber": 1,
                "iAttemptNumber": 1,
                "listEligibleParticipantIds": ["participant-a"],
                "sCompletionRule": "firstAuthorOrExhaustion",
                "sAttemptState": "outcomeSettled",
                "sOutcome": "transitioned:failed",
                "dictPrePhaseState": {},
            },
        }],
    }

    dictStopping = fdictDescribeStoppingPoint(dictRecord)

    assert dictStopping["bResumable"] is False
    assert "not yet available" in dictStopping["sBlockedReason"]


@pytest.mark.falsification
def testAMidPhaseRecordIsNotResumable():
    """A turn the record shows launched but never settled blocks resume.

    A hub killed after turn one of a five-participant phase leaves a
    record whose phase key exists and whose recorded turns are all
    terminal-looking — except the one that was running. Handing that
    record back to the engine would run the next phase over a fraction
    of the deliberation and present it as clean continuation (the
    section 2.1 hazard).

    Kills: dropping the incoherent-turn guard from the descriptor.
    """
    dictRecord = {
        "sState": "planning",
        "dictProjectIdentity": {
            "sSnapshotIdentity": "sealed-content-identity-0001"},
        "listRounds": [{
            "iRoundNumber": 1,
            "sResolution": "",
            "dictTurnsByPhase": {
                "independentProposals": [
                    {"sStatus": "completed"}, {"sStatus": "running"}],
            },
        }],
    }

    dictStopping = fdictDescribeStoppingPoint(dictRecord)

    assert dictStopping["bResumable"] is False
    assert "still running" in dictStopping["sBlockedReason"]


@pytest.mark.falsification
def testTheStoppingPointMirrorsTheEnginesPhaseOrder():
    """The mirror the module comment promises, made checkable.

    ``LIST_FIRST_ROUND_PHASES`` exists so a reader can be told what
    would run next WITHOUT constructing an engine; the engine's
    ``_fsNextPhaseForRound`` stays the authority. A mirror nobody
    checks is a second authority, so this drives BOTH over the same
    open rounds and requires identical answers.

    Kills: reordering the mirrored phase lists.
    """
    from vaibify.gui.agentCouncilResolution import _fsFindNextPhase

    fixtureCouncil = fixtureBuildCouncil(
        LIST_TWO_SPECS, _ffnDecideAllAccept, sChairbotHandle="alpha")
    engine = fixtureCouncil.engine

    listRoundShapes = []
    for iRoundNumber in (1, 2):
        listPhases = (LIST_FIRST_ROUND_PHASES if iRoundNumber == 1
                      else LIST_LATER_ROUND_PHASES)
        for iSettledCount in range(len(listPhases) + 1):
            dictTurnsByPhase = {
                sPhase: [{"sStatus": "completed"}]
                for sPhase in listPhases[:iSettledCount]}
            listRoundShapes.append({
                "iRoundNumber": iRoundNumber,
                "bFinalVetoRound": False,
                "bSynthesisSettled": "synthesis" in dictTurnsByPhase,
                "sResolution": "",
                "dictTurnsByPhase": dictTurnsByPhase,
            })
    listRoundShapes.append({
        "iRoundNumber": 3, "bFinalVetoRound": True,
        "bSynthesisSettled": False, "sResolution": "",
        "dictTurnsByPhase": {}})

    for dictRound in listRoundShapes:
        sEngineAnswer = engine._fsNextPhaseForRound(dictRound) or ""
        sMirrorAnswer = _fsFindNextPhase(dictRound)
        assert sMirrorAnswer == sEngineAnswer, dictRound
