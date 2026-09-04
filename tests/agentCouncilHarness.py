"""Deterministic fake-provider harness for the Agent Council engine.

Phase 1 of the Agent Council (design/agentCouncil.md sections 9.8 and
15.1). The engine is pure and async-first; these fakes let a test script
each turn's structured result, completion and failures, and observe the
exact order turns ran and the exact request each one received — so a
falsification test can prove the barrier held, a peer never saw an
unsubmitted proposal, and a missing veto was never counted as assent.

Nothing here runs a real provider, container, or wall clock: the fake
connection returns a scripted result the moment it is driven, and the
recorder captures execution order and per-turn requests for assertion.
"""

import asyncio
import copy

from vaibify.gui.agentCouncil import CouncilEngine
from vaibify.gui.agentCouncilCampaign import (
    fdictCreateCampaign,
    fdictCreateParticipant,
)
from vaibify.gui.agentCouncilCharter import (
    S_PHASE_CROSS_REVIEW,
    S_PHASE_PROPOSAL,
    S_PHASE_SYNTHESIS,
    S_PHASE_VETO,
)
from vaibify.gui.agentCouncilStore import (
    CouncilEventRing,
    CouncilEvidenceLedger,
    InMemoryCampaignCheckpoint,
)


def fdictMakePatchTurnResult(sPatchUnifiedDiff="--- a/f\n+++ b/f\n",
                             **dictOverrides):
    """A schema-valid PATCH turn result: base result + the patch keys."""
    dictResult = fdictMakeTurnResult(**dictOverrides)
    dictResult["sPatchUnifiedDiff"] = sPatchUnifiedDiff
    dictResult["listFilesTouched"] = ["f"]
    dictResult["listDeviationsFromPlan"] = []
    return dictResult


def fdictMakeDeliberationSummaryResult(**dictOverrides):
    """A schema-valid DELIBERATION SUMMARY result: base + summary keys."""
    dictResult = fdictMakeTurnResult(**dictOverrides)
    dictResult["listPositionsProposed"] = ["keep the integrator"]
    dictResult["listPointsOfDisagreement"] = ["whether the cost is bounded"]
    dictResult["listEvidenceBehindEachPosition"] = ["asserted: a benchmark"]
    return dictResult


def fdictMakeTurnResult(sVerdict="accept", listBlockingObjections=None,
                        listOpenQuestions=None, listEvidence=None,
                        listPlanItems=None, sSummary="a plausible summary",
                        listRejectedAlternatives=None,
                        listVerificationRequirements=None,
                        listNotedFindings=None,
                        listStopConditions=None):
    """Build one schema-valid structured turn result (section 8.5).

    Every array in ``LIST_TURN_RESULT_ARRAY_KEYS`` appears, so this
    factory stays the definition of "schema-valid": a key added to the
    schema without a key added here fails validation loudly rather
    than letting the fake lanes pass a shape production would reject.
    """
    return {
        "sSummary": sSummary,
        "sVerdict": sVerdict,
        "listAssumptions": [],
        "listEvidence": list(listEvidence or []),
        "listMathematicalClaims": [],
        "listArchitectureClaims": [],
        "listSecurityRisks": [],
        "listCounterexamplesAttempted": [],
        "listPlanItems": list(listPlanItems or []),
        "listRejectedAlternatives": list(listRejectedAlternatives or []),
        "listVerificationRequirements": list(
            listVerificationRequirements or []),
        "listStopConditions": list(listStopConditions or []),
        "listNotedFindings": list(listNotedFindings or []),
        "listOpenQuestions": list(listOpenQuestions or []),
        "listBlockingObjections": list(listBlockingObjections or []),
    }


def fdictDecideCompleted(dictResult, sCompletion="terminal"):
    """A decision that completes the turn with a scripted result."""
    return {"sOutcome": "completed", "dictResult": dictResult,
            "sCompletion": sCompletion}


def fdictDecideRaise(sError="scriptedTurnFailure"):
    """A decision that makes the driven connection raise (a failed turn)."""
    return {"sOutcome": "raise", "sError": sError}


class CouncilRecorder:
    """Shared record of turn execution order and per-turn requests."""

    def __init__(self):
        self.listOrderLog = []
        self.listRequestLog = []


class FakeCouncilConnection:
    """One participant's scripted, observable provider connection.

    Implements the section-9.8 seam without inheriting the abstract base,
    so a missing method surfaces as a real failure rather than a silent
    ``NotImplementedError`` the engine swallows.
    """

    def __init__(self, sHandle, ffnDecide, recorder, sRequestedModel=None,
                 sResolvedModel=None):
        self.sHandle = sHandle
        self.ffnDecide = ffnDecide
        self.recorder = recorder
        sEffectiveRequestedModel = sRequestedModel or sHandle
        sEffectiveResolvedModel = (
            sEffectiveRequestedModel
            if sResolvedModel is None else sResolvedModel)
        self.dictModelIdentity = {
            "sRequestedModel": sEffectiveRequestedModel,
            "sResolvedModel": sEffectiveResolvedModel,
            "dictUsage": {},
            "dictModelUsage": {},
        }
        self._dictPending = None

    async def fdictPrepareImmutableContext(self, dictTurnRequest):
        return {"sContextIdentity": f"context-{dictTurnRequest['sTurnId']}"}

    async def fnStartTurn(self, dictTurnRequest):
        self.recorder.listOrderLog.append((
            self.sHandle, dictTurnRequest["sPhase"],
            dictTurnRequest["iRoundNumber"],
            dictTurnRequest["bRepairRequest"]))
        self.recorder.listRequestLog.append(copy.deepcopy(dictTurnRequest))
        self._dictPending = self.ffnDecide(self.sHandle, dictTurnRequest)
        if self._dictPending["sOutcome"] == "raise":
            raise RuntimeError(self._dictPending["sError"])

    async def fiterStreamNormalizedEvents(self):
        for dictEvent in self._dictPending.get("listStreamEvents", []):
            yield dictEvent

    async def fdictCollectStructuredResult(self):
        return copy.deepcopy(self._dictPending["dictResult"])

    async def fsReportCompletion(self):
        return self._dictPending.get("sCompletion", "terminal")


class CouncilTestFixture:
    """A built engine plus every recorder a falsification test asserts on."""

    def __init__(self, dictCampaign, engine, recorder, dictHandleToId,
                 dictIdToHandle, listEvents, ledger, checkpoint,
                 listBaselineCalls):
        self.dictCampaign = dictCampaign
        self.engine = engine
        self.recorder = recorder
        self.dictHandleToId = dictHandleToId
        self.dictIdToHandle = dictIdToHandle
        self.listEvents = listEvents
        self.ledger = ledger
        self.checkpoint = checkpoint
        self.listBaselineCalls = listBaselineCalls

    def fsHandleForId(self, sParticipantId):
        return self.dictIdToHandle[sParticipantId]

    def flistOrderHandles(self, sPhase):
        return [tEntry[0] for tEntry in self.recorder.listOrderLog
                if tEntry[1] == sPhase]

    def flistRequestsFor(self, sHandle, sPhase):
        sParticipantId = self.dictHandleToId[sHandle]
        return [dictRequest for dictRequest in self.recorder.listRequestLog
                if dictRequest["sParticipantId"] == sParticipantId
                and dictRequest["sPhase"] == sPhase]

    def fdictDrive(self):
        return asyncio.run(self.engine.fdictRunUntilBlocked())

    def fdictContinue(self, sResponseText, listDecisionAnswers=None,
                      sResearcherComment=""):
        return asyncio.run(
            self.engine.fdictContinueAfterResearcherResponse(
                sResponseText, listDecisionAnswers, sResearcherComment))

    def fdictGrantResolutionRound(self, iRounds):
        return asyncio.run(self.engine.fdictGrantResolutionRound(iRounds))

    def fdictResolveObjections(self, dictDispositions):
        return asyncio.run(
            self.engine.fdictResolveObjectionsAndRequestFinalVeto(
                dictDispositions))


class VersionRecordingCheckpoint:
    """A checkpoint that keeps EVERY version, in order.

    The crash-point falsifications inspect the durable record as it
    stood at a precise checkpoint — "what would a restarted hub read
    if the process died here" — which the latest-only checkpoint
    cannot answer.
    """

    def __init__(self):
        self.listVersions = []

    def fnCheckpointCampaign(self, dictCampaign):
        self.listVersions.append(copy.deepcopy(dictCampaign))


def fixtureBuildCouncil(listSpecs, ffnDecide, dictSettings=None,
                        sChairbotHandle="", ledger=None, listEventRing=None,
                        ffnBaselineExecute=None, checkpoint=None,
                        sCampaignKind="planning", sSeedPlanDocument=""):
    """Assemble a campaign, fake connections and a wired engine.

    ``listSpecs`` is a list of dicts each carrying ``sHandle``,
    ``sProvider``, ``sRequestedModel`` and an optional ``sRole``.
    ``ffnDecide(sHandle, dictTurnRequest)`` returns a decision built by
    :func:`fdictDecideCompleted` or :func:`fdictDecideRaise`.
    """
    listParticipants = []
    dictHandleToId = {}
    dictIdToHandle = {}
    for dictSpec in listSpecs:
        dictParticipant = fdictCreateParticipant(
            dictSpec["sProvider"], dictSpec["sRequestedModel"],
            dictSpec.get("sRole", ""))
        listParticipants.append(dictParticipant)
        dictHandleToId[dictSpec["sHandle"]] = dictParticipant["sParticipantId"]
        dictIdToHandle[dictParticipant["sParticipantId"]] = dictSpec["sHandle"]
    sChairbotId = dictHandleToId.get(sChairbotHandle, "")
    dictCampaign = fdictCreateCampaign(
        "How should the change be implemented?", listParticipants,
        dictSettings=dictSettings, sChairbotParticipantId=sChairbotId,
        sCampaignKind=sCampaignKind,
        sSeedPlanDocument=sSeedPlanDocument)
    recorder = CouncilRecorder()
    dictSpecByHandle = {
        dictSpec["sHandle"]: dictSpec for dictSpec in listSpecs}
    dictConnections = {}
    for dictParticipant in listParticipants:
        sHandle = dictIdToHandle[dictParticipant["sParticipantId"]]
        dictSpec = dictSpecByHandle[sHandle]
        dictConnections[dictParticipant["sParticipantId"]] = (
            FakeCouncilConnection(
                sHandle, ffnDecide, recorder,
                dictParticipant["sRequestedModel"],
                dictSpec.get("sResolvedModel",
                             dictParticipant["sRequestedModel"])))
    listEvents = []
    if listEventRing is None:
        def fnAppendEvent(dictEvent):
            listEvents.append(dictEvent)
    else:
        def fnAppendEvent(dictEvent):
            listEventRing.fdictAppendEvent(dictEvent)
    ledgerForEngine = ledger or CouncilEvidenceLedger(65536, 262144)
    checkpoint = checkpoint or InMemoryCampaignCheckpoint()
    listBaselineCalls = []

    def fdictExecuteBaselineEvidence(dictRequest):
        listBaselineCalls.append(copy.deepcopy(dictRequest))
        if ffnBaselineExecute is not None:
            return ffnBaselineExecute(dictRequest)
        return {"sSnapshotHash": "baseline-snapshot-hash-0001",
                "sExecutionImageIdentity": "image-sha256-abc",
                "iExitCode": 0, "sOutputDigest": "digest-0001"}

    engine = CouncilEngine(
        dictCampaign, dictConnections, fnAppendEvent,
        ledgerForEngine.fdictRecordEvidence, checkpoint.fnCheckpointCampaign,
        fdictExecuteBaselineEvidence)
    return CouncilTestFixture(
        dictCampaign, engine, recorder, dictHandleToId, dictIdToHandle,
        listEvents, ledgerForEngine, checkpoint, listBaselineCalls)


def ffnDecideAllAccept(sHandle, dictTurnRequest):
    """Every turn completes with an accepting, objection-free result."""
    return fdictDecideCompleted(fdictMakeTurnResult(sVerdict="accept"))


LIST_STANDARD_PHASES = [
    S_PHASE_PROPOSAL, S_PHASE_CROSS_REVIEW, S_PHASE_SYNTHESIS, S_PHASE_VETO]
