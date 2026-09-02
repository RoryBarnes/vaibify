"""Falsification tests for the durable phase-attempt record.

The phase key in ``dictTurnsByPhase`` is written by the FIRST turn to
settle, not at phase end, so a hub killed after turn one of a
five-participant phase leaves a durable record in which every recorded
turn is terminal and the phase key exists — a resume predicate built on
turn statuses calls that resumable and runs the next phase over a
fraction of the deliberation (continuation plan 2.1). The attempt
record is what recovery reads instead: ``running`` refuses,
``turnsSettled`` replays settlement deterministically, and
``outcomeSettled`` names the recovery action.

Every test here reads the record AS A RESTARTED HUB WOULD — from a
specific checkpointed version, via ``VersionRecordingCheckpoint`` —
never from the live engine's memory. "What would recovery see if the
process died here" is the only question these tests ask.
"""

import copy

import pytest

from tests.agentCouncilHarness import (
    CouncilEvidenceLedger,
    VersionRecordingCheckpoint,
    fdictDecideCompleted,
    fdictDecideRaise,
    fdictMakeTurnResult,
    ffnDecideAllAccept,
    fixtureBuildCouncil,
)
from vaibify.gui.agentCouncil import CouncilEngine
from vaibify.gui.agentCouncilCampaign import (
    CouncilProtocolError,
    fdictRestoreCampaignFromMetadata,
)
from vaibify.gui.agentCouncilResolution import fdictDescribeStoppingPoint

LIST_TWO_SPECS = [
    {"sHandle": "alpha", "sProvider": "fake", "sRequestedModel": "model-a"},
    {"sHandle": "beta", "sProvider": "fake", "sRequestedModel": "model-b"},
]
LIST_THREE_SPECS = LIST_TWO_SPECS + [
    {"sHandle": "gamma", "sProvider": "fake", "sRequestedModel": "model-c"},
]


def _fdictBuildRecordedCouncil(listSpecs, ffnDecide, dictSettings=None):
    """Return (fixture, checkpointRecorder) wired for version capture."""
    checkpointRecorder = VersionRecordingCheckpoint()
    fixtureCouncil = fixtureBuildCouncil(
        listSpecs, ffnDecide, dictSettings=dictSettings,
        sChairbotHandle="alpha", checkpoint=checkpointRecorder)
    return fixtureCouncil, checkpointRecorder


def _fdictNormalizeMintedIds(dictCampaign):
    """Deep-copy with every minted question id replaced positionally."""
    dictNormalized = copy.deepcopy(dictCampaign)

    def _fnWalk(jsonValue):
        if isinstance(jsonValue, dict):
            if "sQuestionId" in jsonValue:
                jsonValue["sQuestionId"] = "normalized"
            for jsonChild in jsonValue.values():
                _fnWalk(jsonChild)
        elif isinstance(jsonValue, list):
            for jsonChild in jsonValue:
                _fnWalk(jsonChild)

    _fnWalk(dictNormalized)
    return dictNormalized


def _fdictAttemptOf(dictVersion):
    listRounds = dictVersion.get("listRounds") or []
    return (listRounds[-1] if listRounds else {}).get("dictPhaseAttempt")


def _fdictRebuildEngineAround(dictVersion):
    """Build an engine over a restored checkpoint, with inert turns.

    Replay must need NOTHING but the record: connections that refuse to
    run are the honest stand-in, and a replay that tried to launch a
    turn would raise instead of silently spending provider work.
    """
    dictRestored = fdictRestoreCampaignFromMetadata(dictVersion)

    class _RefusingConnection:
        async def fdictPrepareImmutableContext(self, dictTurnRequest):
            raise AssertionError("replay must not prepare a turn")

        async def fnStartTurn(self, dictTurnRequest):
            raise AssertionError("replay must not launch a turn")

    return CouncilEngine(
        dictRestored,
        {dictParticipant["sParticipantId"]: _RefusingConnection()
         for dictParticipant in dictRestored["listParticipants"]},
        lambda dictEvent: None,
        CouncilEvidenceLedger(65536, 262144).fdictRecordEvidence,
        lambda dictCampaign: None,
        lambda dictRequest: {"sSnapshotHash": "replay", "iExitCode": 0,
                             "sExecutionImageIdentity": "img",
                             "sOutputDigest": "digest"})


@pytest.mark.falsification
def testOneSettledWaveOfAMultiWavePhaseIsRefused():
    """The section-2.1 hazard, pinned: a phase key is not completion.

    With one-turn waves, the checkpoint after the first proposal
    settles shows a phase key whose every recorded turn is terminal —
    exactly what a turn-status scan calls resumable. The attempt
    record says ``running``, and the descriptor refuses.

    Kills: the allEligible completion rule degrading to phase-key
    presence.
    """
    fixtureCouncil, checkpointRecorder = _fdictBuildRecordedCouncil(
        LIST_THREE_SPECS, ffnDecideAllAccept,
        dictSettings={"iMaximumConcurrentTurns": 1})
    fixtureCouncil.fdictDrive()

    listMidWave = [
        dictVersion for dictVersion in checkpointRecorder.listVersions
        if len(dictVersion["listRounds"]) == 1
        and len(dictVersion["listRounds"][-1]["dictTurnsByPhase"].get(
            "independentProposals", [])) == 1
        and dictVersion["listRounds"][-1]["dictTurnsByPhase"][
            "independentProposals"][0]["sStatus"] == "completed"]
    assert listMidWave, "no checkpoint landed between the waves"
    dictCrashed = listMidWave[-1]

    assert _fdictAttemptOf(dictCrashed)["sAttemptState"] == "running"
    dictStopping = fdictDescribeStoppingPoint(dictCrashed)
    assert dictStopping["bResumable"] is False
    assert "still running" in dictStopping["sBlockedReason"]


@pytest.mark.falsification
def testACrashBetweenSynthesisAuthorsLeavesARunningAttempt():
    """Synthesis has no fixed expected set; exhaustion is the rule.

    The configured chairbot fails and the fallback author succeeds. At
    the checkpoint where only the failed author's turn is recorded,
    one eligible author has a terminal turn — which satisfies every
    naive rule and must not satisfy firstAuthorOrExhaustion.

    Kills: the synthesis completion rule treating any terminal turn as
    settlement.
    """
    def _ffnDecideChairbotFails(sHandle, dictTurnRequest):
        if (dictTurnRequest["sPhase"] == "synthesis"
                and sHandle == "alpha"):
            return fdictDecideRaise("scriptedSynthesisFailure")
        return fdictDecideCompleted(fdictMakeTurnResult(sVerdict="accept"))

    fixtureCouncil, checkpointRecorder = _fdictBuildRecordedCouncil(
        LIST_TWO_SPECS, _ffnDecideChairbotFails)
    dictOut = fixtureCouncil.fdictDrive()
    assert dictOut["listRounds"][0]["bChairbotSubstituted"] is True

    listMidSynthesis = [
        dictVersion for dictVersion in checkpointRecorder.listVersions
        if len(dictVersion["listRounds"]) == 1
        and [dictTurn["sStatus"] for dictTurn in
             dictVersion["listRounds"][-1]["dictTurnsByPhase"].get(
                 "synthesis", [])] == ["failed"]]
    assert listMidSynthesis, "no checkpoint landed between the authors"
    dictCrashed = listMidSynthesis[-1]

    assert _fdictAttemptOf(dictCrashed)["sAttemptState"] == "running"
    assert _fdictAttemptOf(dictCrashed)["sCompletionRule"] == (
        "firstAuthorOrExhaustion")
    dictStopping = fdictDescribeStoppingPoint(dictCrashed)
    assert dictStopping["bResumable"] is False


@pytest.mark.falsification
def testAGateAndItsSettledAttemptLandInOneCheckpoint():
    """Atomicity (2.3): the gate and the settled attempt, or neither.

    A crash between "attempt settled" and "gate open" would be a new
    ambiguous durable state — the defect class this design removes,
    reintroduced one level down. So no checkpointed version may ever
    hold a pending gate above an unsettled attempt.

    Kills: settling the attempt AFTER the gate-opening call whose
    transition checkpoints.
    """
    def _ffnDecideNeedsHuman(sHandle, dictTurnRequest):
        if dictTurnRequest["sPhase"] == "synthesis":
            return fdictDecideCompleted(fdictMakeTurnResult(
                sVerdict="needsHuman",
                listOpenQuestions=["Which policy should the cache use?"]))
        return fdictDecideCompleted(fdictMakeTurnResult(sVerdict="accept"))

    fixtureCouncil, checkpointRecorder = _fdictBuildRecordedCouncil(
        LIST_TWO_SPECS, _ffnDecideNeedsHuman)
    dictOut = fixtureCouncil.fdictDrive()
    assert dictOut["sState"] == "needsHuman"

    listGateVersions = [
        dictVersion for dictVersion in checkpointRecorder.listVersions
        if dictVersion.get("dictPendingHumanGate")]
    assert listGateVersions, "no checkpoint carries the gate"
    for dictVersion in listGateVersions:
        dictAttempt = _fdictAttemptOf(dictVersion)
        assert dictAttempt["sAttemptState"] == "outcomeSettled", (
            "a checkpoint holds an open gate above an unsettled attempt")
        assert dictAttempt["sOutcome"] == "gateOpened"


@pytest.mark.falsification
def testAnIndeterminateOutcomeSettlesWithItsTransition():
    """Crash point 3: interrupted and the settled attempt travel together.

    The INTERRUPTED transition fires before the phase's questions are
    collected, and the indeterminacy lives in sCompletion, not sStatus
    — no turn-status scan can see it. The attempt record can: every
    checkpointed interrupted version carries transitioned:interrupted,
    and the abandoned questions are NOT pretended handled (the phase's
    own questions stay uncollected; re-running the phase regenerates
    them).

    Kills: transitioning to interrupted before the attempt settles.
    """
    def _ffnDecideIndeterminate(sHandle, dictTurnRequest):
        if dictTurnRequest["sPhase"] == "crossReview" and sHandle == "beta":
            return fdictDecideCompleted(
                fdictMakeTurnResult(
                    sVerdict="accept",
                    listOpenQuestions=["is the boundary observable?"]),
                sCompletion="indeterminate")
        return fdictDecideCompleted(fdictMakeTurnResult(sVerdict="accept"))

    fixtureCouncil, checkpointRecorder = _fdictBuildRecordedCouncil(
        LIST_TWO_SPECS, _ffnDecideIndeterminate)
    dictOut = fixtureCouncil.fdictDrive()
    assert dictOut["sState"] == "interrupted"

    listInterrupted = [
        dictVersion for dictVersion in checkpointRecorder.listVersions
        if dictVersion["sState"] == "interrupted"]
    assert listInterrupted, "no checkpoint carries the interruption"
    for dictVersion in listInterrupted:
        dictAttempt = _fdictAttemptOf(dictVersion)
        assert dictAttempt["sAttemptState"] == "outcomeSettled"
        assert dictAttempt["sOutcome"] == "transitioned:interrupted"
        # The abandoned questions were not collected into a gate.
        assert dictVersion.get("dictPendingHumanGate") is None
        assert dictVersion["listRounds"][-1].get(
            "listDeferredQuestions") == []


@pytest.mark.falsification
def testSettlementReplayIsDeterministicOverTheRecord():
    """turnsSettled is recoverable BECAUSE settlement is a pure replay.

    Two engines rebuilt over the same turnsSettled checkpoint must
    settle to byte-identical outcomes; the researcher's "all agents
    completed the step" is honored without re-running anything.

    Kills: the replay consulting anything but the durable record.
    """
    def _ffnDecideNeedsHuman(sHandle, dictTurnRequest):
        if dictTurnRequest["sPhase"] == "synthesis":
            return fdictDecideCompleted(fdictMakeTurnResult(
                sVerdict="needsHuman",
                listOpenQuestions=["Which policy should the cache use?"]))
        return fdictDecideCompleted(fdictMakeTurnResult(sVerdict="accept"))

    fixtureCouncil, checkpointRecorder = _fdictBuildRecordedCouncil(
        LIST_TWO_SPECS, _ffnDecideNeedsHuman)
    fixtureCouncil.fdictDrive()

    listSettledUnjudged = [
        dictVersion for dictVersion in checkpointRecorder.listVersions
        if (_fdictAttemptOf(dictVersion) or {}).get(
            "sAttemptState") == "turnsSettled"
        and _fdictAttemptOf(dictVersion)["sPhase"] == "synthesis"]
    assert listSettledUnjudged, (
        "no checkpoint landed between turns settling and settlement")
    dictCrashed = listSettledUnjudged[-1]

    dictFirst = _fdictRebuildEngineAround(
        dictCrashed).fdictReplaySettlementFromTurnRecords()
    dictSecond = _fdictRebuildEngineAround(
        dictCrashed).fdictReplaySettlementFromTurnRecords()

    # Deterministic up to SERVER-MINTED identifiers: a question id is
    # minted fresh at gate creation (only one replay ever happens in a
    # real recovery), so the pinned claim is that everything the
    # researcher acts on — state, gate, question texts, raisers,
    # resolution, attempt outcome — replays identically.
    assert _fdictNormalizeMintedIds(dictFirst) == (
        _fdictNormalizeMintedIds(dictSecond))
    assert dictFirst["sState"] == "needsHuman"
    dictAttempt = dictFirst["listRounds"][-1]["dictPhaseAttempt"]
    assert dictAttempt["sAttemptState"] == "outcomeSettled"
    assert dictAttempt["sOutcome"] == "gateOpened"


@pytest.mark.falsification
def testVetoReplayReproducesTheResolutionFromTheRecordAlone():
    """Crash point 5, and its twin in one journey.

    Veto settles OUTSIDE _fnSettlePhaseOutcome: the resolution is
    written later by round termination, and the crash window between
    them is survivable only because classification is a pure function
    of the durable turn records. The twin mutates one veto turn record
    and requires the replayed resolution to CHANGE — a replay that
    trusts the crashed run's partial writes passes the first half and
    fails this one.

    Kills: the replay skipping re-classification of the veto records.
    """
    fixtureCouncil, checkpointRecorder = _fdictBuildRecordedCouncil(
        LIST_TWO_SPECS, ffnDecideAllAccept)
    dictOut = fixtureCouncil.fdictDrive()
    assert dictOut["sState"] == "planReady"

    listVetoSettled = [
        dictVersion for dictVersion in checkpointRecorder.listVersions
        if (_fdictAttemptOf(dictVersion) or {}).get(
            "sAttemptState") == "turnsSettled"
        and _fdictAttemptOf(dictVersion)["sPhase"] == "veto"
        and dictVersion["sState"] == "planning"]
    assert listVetoSettled, "no checkpoint landed before round resolution"
    dictCrashed = listVetoSettled[-1]

    dictReplayed = _fdictRebuildEngineAround(
        dictCrashed).fdictReplaySettlementFromTurnRecords()
    assert dictReplayed["sState"] == "planReady"
    assert dictReplayed["listRounds"][-1]["sResolution"] == "planReady"

    # The twin: a different record must produce a different resolution.
    dictMutated = copy.deepcopy(dictCrashed)
    dictVetoTurn = dictMutated["listRounds"][-1]["dictTurnsByPhase"][
        "veto"][0]
    dictVetoTurn["dictResult"]["sVerdict"] = "blockingObjection"
    dictVetoTurn["dictResult"]["listBlockingObjections"] = [
        "the cache key ignores the compiler version"]
    dictDiverged = _fdictRebuildEngineAround(
        dictMutated).fdictReplaySettlementFromTurnRecords()
    assert dictDiverged["sState"] == "planning"
    assert dictDiverged["listRounds"][-1]["sResolution"] == (
        "objectionsOutstanding")


@pytest.mark.falsification
def testARunningAttemptRefusesReplay():
    """The exhaustive other half of 2.4: running is unresumable.

    Kills: the replay accepting an attempt whose turns never all
    settled, which runs synthesis over a fraction of the deliberation
    and presents it as clean continuation.
    """
    fixtureCouncil, checkpointRecorder = _fdictBuildRecordedCouncil(
        LIST_THREE_SPECS, ffnDecideAllAccept,
        dictSettings={"iMaximumConcurrentTurns": 1})
    fixtureCouncil.fdictDrive()
    listMidWave = [
        dictVersion for dictVersion in checkpointRecorder.listVersions
        if (_fdictAttemptOf(dictVersion) or {}).get(
            "sAttemptState") == "running"
        and dictVersion["listRounds"][-1]["dictTurnsByPhase"].get(
            "independentProposals")]
    assert listMidWave, "no mid-wave checkpoint"

    with pytest.raises(CouncilProtocolError):
        _fdictRebuildEngineAround(
            listMidWave[-1]).fdictReplaySettlementFromTurnRecords()
