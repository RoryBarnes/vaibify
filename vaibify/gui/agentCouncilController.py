"""Serialized command authority for Agent Council campaigns (R1).

The controller is the SOLE writer of campaign state. Routes never
mutate a campaign directly: they submit one of the bounded commands
below onto the per-campaign serialization primitive, and the commands
drain strictly in submission order. Two commands for the same campaign
can never interleave; commands for different campaigns are independent.

This module owns the substrate — the command vocabulary, the
per-campaign locks, the observable command log — and the campaign
runtime the controller drives real deliberation through: on start it
captures the immutable snapshot under the project's reconciliation
lock, builds the ``CouncilEngine`` with real provider connections, and
drives the campaign on a background task that is the sole writer while
it lives. Continuation commands refuse while that task is live; a stop
against a REAL engine is cooperative (the engine admits no later turn
and settles at the next boundary — the record says ``bStopRequested``
until then, never a fabricated terminal state), while a stop against a
runtime with no engine (a crash leftover, or a test double) cancels the
task and transitions honestly to interrupted. It is deliberately free
of route imports: the routes call down into it, never the reverse.

Why a lock and not a queue-with-worker: an ``asyncio.Lock`` wakes its
waiters first-in-first-out on one event loop, which IS a per-campaign
command queue, without a worker task whose crash/restart semantics
would need their own recovery story. The lock lives in ``app.state``
(via the controller state dict registered in ``appFactory``), so its
lifetime is the hub process — exactly the lifetime of the in-memory
campaign state it serializes.
"""

import asyncio
import logging
import os

from . import agentCouncilCampaign
from . import agentCouncilRegistry
from . import agentCouncilStore

logger = logging.getLogger("vaibify")

__all__ = [
    "CouncilCommandError",
    "LIST_CONTROLLER_COMMANDS",
    "S_COMMAND_ACCEPT_PLAN",
    "S_COMMAND_DELETE",
    "S_COMMAND_GRANT_RESOLUTION_ROUND",
    "S_COMMAND_REJECT_CANDIDATE",
    "S_COMMAND_RESOLVE_OBJECTIONS",
    "S_COMMAND_RESPOND",
    "S_COMMAND_REQUEST_STOP",
    "S_COMMAND_START",
    "S_COUNCIL_CONTROLLER_STATE_KEY",
    "fbCampaignDriveIsLive",
    "fdictCreateCouncilControllerState",
    "fdictLaunchCampaignDeliberation",
    "fdictRequestCampaignStop",
    "fdictAcceptCampaignPlan",
    "fdictContinueCampaignAfterResponse",
    "fdictGrantCampaignResolutionRound",
    "fdictRejectCampaignCandidate",
    "fdictResolveCampaignObjections",
    "fsComposePlanMarkdown",
    "fgenericSubmitCampaignCommand",
    "fiClassifyInterruptedCampaignsOnStartup",
    "flistReadCampaignCommandLog",
    "fnDrainControllerForResource",
    "fnDrainControllerOnShutdown",
]

# The single ``app.state`` attribute the routes reach the controller
# state through, beside the registry and the campaign store.
S_COUNCIL_CONTROLLER_STATE_KEY = "dictCouncilControllerState"

# The bounded command vocabulary (remediation R1). A route may submit
# exactly these; anything else is a programming error and refuses loudly.
S_COMMAND_START = "start"
S_COMMAND_RESPOND = "respond"
S_COMMAND_REQUEST_STOP = "requestStop"
S_COMMAND_ACCEPT_PLAN = "acceptPlan"
S_COMMAND_DELETE = "delete"
S_COMMAND_GRANT_RESOLUTION_ROUND = "grantResolutionRound"
S_COMMAND_RESOLVE_OBJECTIONS = "resolveObjectionsThenFinalVeto"
S_COMMAND_REJECT_CANDIDATE = "rejectCandidate"

LIST_CONTROLLER_COMMANDS = [
    S_COMMAND_START,
    S_COMMAND_RESPOND,
    S_COMMAND_REQUEST_STOP,
    S_COMMAND_ACCEPT_PLAN,
    S_COMMAND_DELETE,
    S_COMMAND_GRANT_RESOLUTION_ROUND,
    S_COMMAND_RESOLVE_OBJECTIONS,
    S_COMMAND_REJECT_CANDIDATE,
]

# The command log is an observability convenience for the serialization
# proofs and the UI, never an authority; bounded so a long-lived campaign
# cannot grow it without limit.
I_MAX_COMMAND_LOG_ENTRIES = 200


class CouncilCommandError(Exception):
    """A caller submitted a command outside the bounded vocabulary."""


def fdictCreateCouncilControllerState():
    """Create the empty app-owned controller state.

    A plain dict driven by module functions, the same shape the council
    registry and campaign store use: ``app.state`` owns one value and no
    class-instance identity threads through the protocol records.
    ``dictCampaignRuntime`` holds each campaign's live driving state
    (engine, connections, live-turn task) once the controller launches
    real deliberation; the substrate only reserves the slot.
    """
    return {
        "dictCampaignLocks": {},
        "dictCommandLogByCampaign": {},
        "dictCampaignRuntime": {},
    }


def _flockForCampaign(dictControllerState, sCampaignId):
    """Return (creating on first use) the campaign's serialization lock."""
    lockCampaign = dictControllerState["dictCampaignLocks"].get(sCampaignId)
    if lockCampaign is None:
        lockCampaign = asyncio.Lock()
        dictControllerState["dictCampaignLocks"][sCampaignId] = lockCampaign
    return lockCampaign


def _fnRecordCommandEvent(dictControllerState, sCampaignId, sCommandKind,
                          sStage):
    """Append one bounded command-log row for the serialization proofs."""
    listLog = dictControllerState["dictCommandLogByCampaign"].setdefault(
        sCampaignId, [])
    listLog.append({"sCommandKind": sCommandKind, "sStage": sStage})
    del listLog[:-I_MAX_COMMAND_LOG_ENTRIES]


def flistReadCampaignCommandLog(dictControllerState, sCampaignId):
    """Return a copy of one campaign's command log, submission order."""
    return [dict(dictEntry) for dictEntry in
            dictControllerState["dictCommandLogByCampaign"].get(
                sCampaignId, [])]


async def fgenericSubmitCampaignCommand(dictControllerState, sCampaignId,
                                        sCommandKind, ffnExecuteCommand):
    """Run one bounded command under the campaign's serialization lock.

    Commands for one campaign execute strictly in submission order
    (``asyncio.Lock`` wakes waiters first-in-first-out on one event
    loop); commands for different campaigns are independent. The
    command's return value is the caller's — an ``HTTPException`` raised
    inside the executor propagates unchanged, so a route's refusal
    semantics survive the serialization. The started/settled stages are
    recorded even when the executor raises, so the log shows a failed
    command as settled rather than vanished.
    """
    if sCommandKind not in LIST_CONTROLLER_COMMANDS:
        raise CouncilCommandError(
            f"unknown council command {sCommandKind!r}; the vocabulary "
            f"is {LIST_CONTROLLER_COMMANDS}")
    _fnRecordCommandEvent(
        dictControllerState, sCampaignId, sCommandKind, "submitted")
    async with _flockForCampaign(dictControllerState, sCampaignId):
        _fnRecordCommandEvent(
            dictControllerState, sCampaignId, sCommandKind, "started")
        try:
            return await ffnExecuteCommand()
        finally:
            _fnRecordCommandEvent(
                dictControllerState, sCampaignId, sCommandKind, "settled")


# ---------------------------------------------------------------------
# The campaign runtime: real deliberation on a background drive task.
# ---------------------------------------------------------------------


def fconnectionBuildParticipantConnection(dictRuntime, dictParticipant):
    """Build one participant's provider connection (Claude runner backend).

    The controller's provider seam: the integration tests substitute a
    deterministic fake here (module-level, so one patch covers every
    campaign), and the live lane substitutes a real connection whose
    CLI program is a scripted fake INSIDE a real runner. The default is
    the production Claude runner connection over the campaign's
    gateway.
    """
    from . import agentCouncilProviders
    return agentCouncilProviders.ClaudeRunnerConnection(
        _fdictEnsureRuntimeGateway(dictRuntime),
        dictRuntime["sCampaignId"],
        dictRuntime["sImageReference"],
        dictRuntime["baSnapshotTar"],
        dictParticipant["sRequestedModel"],
    )


def _fdictEnsureRuntimeGateway(dictRuntime):
    """Create (once) and return the campaign runtime's Docker gateway."""
    if dictRuntime.get("dictGateway") is None:
        from . import agentCouncilDockerGateway
        dictRuntime["dictGateway"] = (
            agentCouncilDockerGateway.fdictCreateCouncilDockerGateway(
                agentCouncilDockerGateway.fdockerCreateCouncilClient(),
                dictRuntime["dictRegistry"]))
    return dictRuntime["dictGateway"]


def _fdictExecuteBaselineEvidenceLazily(dictRuntime, dictRequest):
    """Run the mandatory baseline executor, building it on first use.

    The executor needs a Docker gateway, but a campaign whose turns
    never confirm a claim never needs one — and the fake-provider
    integration lane must be able to run with no daemon at all. So the
    real executor is assembled on the first confirmed claim, not at
    campaign start.
    """
    from . import agentCouncilProviders
    if dictRuntime.get("fdictExecuteBaselineEvidence") is None:
        dictRuntime["fdictExecuteBaselineEvidence"] = (
            agentCouncilProviders.ffnBuildBaselineEvidenceExecutor(
                _fdictEnsureRuntimeGateway(dictRuntime),
                dictRuntime["sCampaignId"],
                dictRuntime["sImageReference"],
                dictRuntime["sSnapshotIdentity"],
                dictRuntime["baSnapshotTar"]))
    return dictRuntime["fdictExecuteBaselineEvidence"](dictRequest)


def _fdictBuildCampaignRuntime(dictControllerState, dictStore, dictRegistry,
                               sCampaignId, dictCampaign,
                               sImageReference, baSnapshotTar):
    """Assemble one campaign's live runtime: engine, connections, task slot.

    The engine drives the SAME dict the runtime holds; the store's
    record advances only through the checkpoint callback, so a reader
    between checkpoints sees the last settled state — the honest
    answer, never a torn intermediate one.
    """
    from .agentCouncil import CouncilEngine
    dictRuntime = {
        "sCampaignId": sCampaignId,
        "dictCampaign": dictCampaign,
        "dictStore": dictStore,
        "dictRegistry": dictRegistry,
        "sImageReference": sImageReference,
        "baSnapshotTar": baSnapshotTar,
        "sSnapshotIdentity": (
            dictCampaign["dictProjectIdentity"]["sSnapshotIdentity"]),
        "dictGateway": None,
        "fdictExecuteBaselineEvidence": None,
        "taskDrive": None,
        "sTurnId": "",
    }

    def _fnAppendEngineEvent(dictEvent):
        agentCouncilStore.fdictAppendCampaignEvent(
            dictStore, sCampaignId, dictEvent)

    def _fdictRecordEvidence(dictEntry):
        return agentCouncilStore.fdictRecordCampaignEvidence(
            dictStore, sCampaignId, dictEntry)

    def _fnCheckpoint(dictCampaignSettled):
        agentCouncilStore.fnCheckpointStoredCampaign(
            dictStore, sCampaignId, dictCampaignSettled)

    def _fdictBaseline(dictRequest):
        return _fdictExecuteBaselineEvidenceLazily(dictRuntime, dictRequest)

    dictConnections = {
        dictParticipant["sParticipantId"]:
            fconnectionBuildParticipantConnection(
                dictRuntime, dictParticipant)
        for dictParticipant in dictCampaign["listParticipants"]}
    dictRuntime["engineCouncil"] = CouncilEngine(
        dictCampaign, dictConnections, _fnAppendEngineEvent,
        _fdictRecordEvidence, _fnCheckpoint, _fdictBaseline)
    dictControllerState["dictCampaignRuntime"][sCampaignId] = dictRuntime
    return dictRuntime


def fbCampaignDriveIsLive(dictControllerState, sCampaignId):
    """Report whether a campaign's drive task is currently running."""
    dictRuntime = dictControllerState["dictCampaignRuntime"].get(sCampaignId)
    if dictRuntime is None or dictRuntime.get("taskDrive") is None:
        return False
    return not dictRuntime["taskDrive"].done()


async def _fnDriveCampaignToSettlement(dictRuntime, ffnAdvanceEngine):
    """Drive the engine until it settles; record failure honestly.

    The drive task is the sole campaign-state writer while it lives. An
    unexpected fault — a gateway refusal, a daemon fall-over, a
    programming error — transitions the campaign to ``failed`` (unless
    the engine already reached a terminal or suspended state), and the
    checkpoint lands before the turn retires, so a crash between the
    two still leaves a discoverable record. The turn-in-flight record
    retires in ``finally``: the idle watchdog must keep vetoing
    self-exit for exactly as long as the drive lives.
    """
    dictStore = dictRuntime["dictStore"]
    dictRegistry = dictRuntime["dictRegistry"]
    sCampaignId = dictRuntime["sCampaignId"]
    try:
        await ffnAdvanceEngine()
    except asyncio.CancelledError:
        raise
    except Exception as error:
        logger.exception(
            "Council campaign %s deliberation failed", sCampaignId)
        dictCampaign = dictRuntime["dictCampaign"]
        if dictCampaign["sState"] == agentCouncilCampaign.S_STATE_PLANNING:
            agentCouncilCampaign.fnTransitionCampaignState(
                dictCampaign, agentCouncilCampaign.S_STATE_FAILED,
                f"deliberationFaulted: {type(error).__name__}: {error}")
        agentCouncilStore.fnCheckpointStoredCampaign(
            dictStore, sCampaignId, dictCampaign)
    finally:
        agentCouncilStore.fnCheckpointStoredCampaign(
            dictStore, sCampaignId, dictRuntime["dictCampaign"])
        agentCouncilRegistry.fnRetireTurnInFlight(
            dictRegistry, sCampaignId, dictRuntime["sTurnId"])


def _fsSpawnDriveTask(dictRuntime, ffnAdvanceEngine):
    """Register the turn and start the background drive task."""
    sTurnId = agentCouncilStore.fsMintNextTurnId(
        dictRuntime["dictStore"], dictRuntime["sCampaignId"])
    agentCouncilRegistry.fbRegisterTurnInFlight(
        dictRuntime["dictRegistry"], dictRuntime["sCampaignId"], sTurnId)
    dictRuntime["sTurnId"] = sTurnId
    dictRuntime["taskDrive"] = asyncio.create_task(
        _fnDriveCampaignToSettlement(dictRuntime, ffnAdvanceEngine))
    return sTurnId


def _fnRefuseWhileDriveIsLive(dictControllerState, sCampaignId, sAction):
    """Refuse a continuation that would race the live drive task."""
    if fbCampaignDriveIsLive(dictControllerState, sCampaignId):
        raise CouncilCommandError(
            f"cannot {sAction} while the council is deliberating; wait "
            "for the current turn to settle or request a stop")


async def fdictLaunchCampaignDeliberation(
        dictControllerState, dictStore, dictRegistry, sCampaignId,
        ffnCaptureSnapshot, ffnResolveImageReference):
    """Capture the snapshot, build the runtime, and start deliberation.

    Runs inside the submitted ``start`` command, so it is serialized
    with every other command for this campaign. ``ffnCaptureSnapshot``
    is the route-supplied closure that captures the immutable context
    under the project's reconciliation lock and returns the manifest;
    ``ffnResolveImageReference`` resolves the project container's image
    for the runners. Both arrive as closures because the controller
    must not import the route context. The snapshot identity lands in
    the campaign's identity triple BEFORE the first turn launches, and
    the record checkpoints in between, so a crash at any point leaves
    either a draft with no snapshot or a planning record whose snapshot
    is sealed — never a turn over an unrecorded context.
    """
    dictCampaign = agentCouncilStore.fjsonGetCampaignRecord(
        dictStore, sCampaignId)
    if dictCampaign is None:
        raise CouncilCommandError(
            f"no stored campaign {sCampaignId!r} to deliberate")
    _fnRefuseWhileDriveIsLive(dictControllerState, sCampaignId, "start")
    dictManifest = await ffnCaptureSnapshot()
    dictCampaign["dictProjectIdentity"]["sSnapshotIdentity"] = (
        dictManifest["sSnapshotSha256"])
    agentCouncilStore.fnCheckpointStoredCampaign(
        dictStore, sCampaignId, dictCampaign)
    baSnapshotTar = _fbaReadSealedSnapshot(dictStore, sCampaignId)
    dictRuntime = _fdictBuildCampaignRuntime(
        dictControllerState, dictStore, dictRegistry, sCampaignId,
        dictCampaign, await ffnResolveImageReference(), baSnapshotTar)
    sTurnId = _fsSpawnDriveTask(
        dictRuntime,
        dictRuntime["engineCouncil"].fdictRunUntilBlocked)
    return {"sTurnId": sTurnId}


def _fbaReadSealedSnapshot(dictStore, sCampaignId):
    """Read the sealed snapshot tarball beside the campaign record."""
    sSnapshotPath = os.path.join(
        dictStore["sDurableStoreRoot"], sCampaignId, "snapshot",
        "snapshot.tar")
    with open(sSnapshotPath, "rb") as fileSnapshot:
        return fileSnapshot.read()


def _fdictRequireContinuationRuntime(dictControllerState, sCampaignId,
                                     sAction):
    """Return the runtime a continuation drives, or refuse honestly."""
    _fnRefuseWhileDriveIsLive(dictControllerState, sCampaignId, sAction)
    dictRuntime = dictControllerState["dictCampaignRuntime"].get(sCampaignId)
    if dictRuntime is None:
        raise CouncilCommandError(
            "this campaign has no live deliberation to continue — the "
            "hub restarted since it ran; convene a fresh council")
    return dictRuntime


def _fnRequireHumanGate(dictRuntime, sExpectedGateKind=""):
    """Refuse a continuation whose gate the engine would refuse.

    The engine enforces the same rule inside the drive task, but a
    refusal must land as the ROUTE'S 409 — not as a 200 whose turn
    then quietly faults — so the gate is checked before the task
    spawns. The engine's own check stays as the second lock.
    """
    dictCampaign = dictRuntime["dictCampaign"]
    if dictCampaign["sState"] != agentCouncilCampaign.S_STATE_NEEDS_HUMAN:
        raise CouncilCommandError(
            "the campaign is not waiting on the researcher")
    dictGate = dictCampaign["dictPendingHumanGate"] or {}
    sGateKind = dictGate.get("sGateKind", "")
    if sExpectedGateKind and sGateKind != sExpectedGateKind:
        raise CouncilCommandError(
            f"this action answers a {sExpectedGateKind} gate, not "
            f"{sGateKind}")
    if not sExpectedGateKind and sGateKind == (
            agentCouncilCampaign.S_GATE_EXHAUSTED_ROUNDS):
        raise CouncilCommandError(
            "the round budget is exhausted; choose one of the three "
            "exits — a plain response does not restart the loop")


async def fdictContinueCampaignAfterResponse(
        dictControllerState, dictStore, dictRegistry, sCampaignId,
        sResponseText):
    """Answer a blocking question and relaunch deliberation.

    Refuses while a drive task is live, when the campaign has no
    runtime to continue (a hub restart classified it interrupted; a
    fresh council is the honest path — the engine is never resumed
    over runners nobody can account for), and when the pending gate is
    the exhausted-rounds one, whose only continuations are its three
    exits.
    """
    dictRuntime = _fdictRequireContinuationRuntime(
        dictControllerState, sCampaignId, "respond")
    _fnRequireHumanGate(dictRuntime)
    sTurnId = _fsSpawnDriveTask(
        dictRuntime,
        lambda: dictRuntime["engineCouncil"]
        .fdictContinueAfterResearcherResponse(sResponseText))
    return {"sTurnId": sTurnId}


async def fdictGrantCampaignResolutionRound(
        dictControllerState, dictStore, dictRegistry, sCampaignId,
        iGrantedRounds):
    """Exhausted-round exit 1: grant a bounded resolution round."""
    dictRuntime = _fdictRequireContinuationRuntime(
        dictControllerState, sCampaignId, "grant a resolution round")
    _fnRequireHumanGate(
        dictRuntime, agentCouncilCampaign.S_GATE_EXHAUSTED_ROUNDS)
    if iGrantedRounds < 1:
        raise CouncilCommandError(
            "a resolution round grant must be at least one round")
    sTurnId = _fsSpawnDriveTask(
        dictRuntime,
        lambda: dictRuntime["engineCouncil"]
        .fdictGrantResolutionRound(iGrantedRounds))
    return {"sTurnId": sTurnId}


async def fdictResolveCampaignObjections(
        dictControllerState, dictStore, dictRegistry, sCampaignId,
        dictDispositionByObjectionId):
    """Exhausted-round exit 2: dispose every objection, one final veto."""
    dictRuntime = _fdictRequireContinuationRuntime(
        dictControllerState, sCampaignId, "resolve objections")
    _fnRequireHumanGate(
        dictRuntime, agentCouncilCampaign.S_GATE_EXHAUSTED_ROUNDS)
    sTurnId = _fsSpawnDriveTask(
        dictRuntime,
        lambda: dictRuntime["engineCouncil"]
        .fdictResolveObjectionsAndRequestFinalVeto(
            dictDispositionByObjectionId))
    return {"sTurnId": sTurnId}


async def fdictRejectCampaignCandidate(dictControllerState, dictStore,
                                       dictRegistry, sCampaignId,
                                       sReasonText):
    """Reject/archive the candidate — exhausted exit 3, or at planReady.

    Synchronous: rejection drives no provider turn, so with no live
    runtime the engine is rebuilt around the restored record with inert
    connections, exactly as acceptance does. The engine's own guard
    decides which states may reject.
    """
    from .agentCouncil import CouncilEngine
    _fnRefuseWhileDriveIsLive(dictControllerState, sCampaignId, "reject")
    dictRuntime = dictControllerState["dictCampaignRuntime"].get(sCampaignId)
    if dictRuntime is not None:
        dictHolder = dictRuntime
    else:
        dictCampaign = agentCouncilCampaign.fdictRestoreCampaignFromMetadata(
            agentCouncilStore.fjsonGetCampaignRecord(dictStore, sCampaignId))

        def _fdictRefuseBaseline(dictRequest):
            raise CouncilCommandError("rejection drives no baseline evidence")

        dictHolder = {"dictCampaign": dictCampaign}
        dictHolder["engineCouncil"] = CouncilEngine(
            dictCampaign,
            {dictParticipant["sParticipantId"]:
                agentCouncilCampaign.CouncilProviderConnection()
             for dictParticipant in dictCampaign["listParticipants"]},
            lambda dictEvent: agentCouncilStore.fdictAppendCampaignEvent(
                dictStore, sCampaignId, dictEvent),
            lambda dictEntry: agentCouncilStore.fdictRecordCampaignEvidence(
                dictStore, sCampaignId, dictEntry),
            lambda dictSettled: agentCouncilStore.fnCheckpointStoredCampaign(
                dictStore, sCampaignId, dictSettled),
            _fdictRefuseBaseline)
    try:
        dictHolder["engineCouncil"].fdictRejectCandidate(sReasonText)
    except agentCouncilCampaign.CouncilProtocolError as error:
        raise CouncilCommandError(str(error))
    return {"bRejected": True,
            "dictCampaign": agentCouncilStore.fjsonGetCampaignRecord(
                dictStore, sCampaignId)}


async def fdictRequestCampaignStop(dictControllerState, dictStore,
                                   dictRegistry, sCampaignId,
                                   ffnDrainRegistryWork):
    """Stop a campaign: cooperative for a real engine, honest otherwise.

    With a live drive task and a real engine, the stop is a REQUEST:
    the engine admits no later turn and settles at the next boundary;
    the record carries ``bStopRequested`` until then and the state is
    still the truth (planning). With no live drive — a suspended gate,
    a crash leftover, or a runtime-less record — the stop settles
    immediately: registry work drains and the campaign transitions to
    interrupted. A live drive with NO engine (only a test double can
    produce one) is cancelled outright, then settled the immediate way.
    """
    dictRuntime = dictControllerState["dictCampaignRuntime"].get(sCampaignId)
    dictCampaign = agentCouncilStore.fjsonGetCampaignRecord(
        dictStore, sCampaignId)
    if dictCampaign is None:
        raise CouncilCommandError(f"no stored campaign {sCampaignId!r}")
    if fbCampaignDriveIsLive(dictControllerState, sCampaignId):
        if dictRuntime.get("engineCouncil") is not None:
            dictRuntime["engineCouncil"].fnRequestStopAfterCurrentTurn()
            agentCouncilStore.fnCheckpointStoredCampaign(
                dictStore, sCampaignId, dictRuntime["dictCampaign"])
            return {"bStopRequested": True, "bSettled": False,
                    "dictCampaign": agentCouncilStore.fjsonGetCampaignRecord(
                        dictStore, sCampaignId)}
        dictRuntime["taskDrive"].cancel()
        agentCouncilRegistry.fnRetireTurnInFlight(
            dictRegistry, sCampaignId, dictRuntime["sTurnId"])
    ffnDrainRegistryWork()
    dictLive = (dictRuntime["dictCampaign"] if dictRuntime is not None
                else dictCampaign)
    dictLive["bStopRequested"] = True
    if dictLive["sState"] not in (
            agentCouncilCampaign.S_STATE_PLAN_ACCEPTED,
            agentCouncilCampaign.S_STATE_AWAITING_IMPLEMENTATION,
            agentCouncilCampaign.S_STATE_ARCHIVED,
            agentCouncilCampaign.S_STATE_FAILED):
        agentCouncilCampaign.fnTransitionCampaignState(
            dictLive, agentCouncilCampaign.S_STATE_INTERRUPTED,
            "researcher requested a stop")
    agentCouncilStore.fnCheckpointStoredCampaign(
        dictStore, sCampaignId, dictLive)
    return {"bStopRequested": True, "bSettled": True,
            "dictCampaign": agentCouncilStore.fjsonGetCampaignRecord(
                dictStore, sCampaignId)}


def fsComposePlanMarkdown(dictCandidatePlan):
    """Render the council's own candidate into the plan.md text.

    The acceptance artifact is composed from the SERVER-held candidate
    (remediation R3) — the synthesis result's summary, plan items and
    open questions, plus the objection provenance the protocol
    recorded — never from caller-supplied text.
    """
    dictResult = dictCandidatePlan.get("dictResult") or {}
    listLines = ["# Council plan", "", dictResult.get("sSummary", ""), ""]
    listPlanItems = dictResult.get("listPlanItems") or []
    if listPlanItems:
        listLines.append("## Plan")
        listLines.extend(
            f"{iIndex + 1}. {sItem}"
            for iIndex, sItem in enumerate(
                str(jsonItem) for jsonItem in listPlanItems))
        listLines.append("")
    listOpenQuestions = dictResult.get("listOpenQuestions") or []
    if listOpenQuestions:
        listLines.append("## Open questions")
        listLines.extend(f"- {jsonItem}" for jsonItem in listOpenQuestions)
        listLines.append("")
    for sProvenanceKey, sHeading in (
            ("listCouncilClearedObjections", "Objections cleared in review"),
            ("listResearcherResolvedObjections",
             "Objections resolved by the researcher"),
            ("listResearcherOverriddenObjections",
             "Objections OVERRIDDEN by the researcher")):
        listObjections = dictCandidatePlan.get(sProvenanceKey) or []
        if listObjections:
            listLines.append(f"## {sHeading}")
            listLines.extend(
                "- " + str(dictObjection.get("sObjectionText", ""))
                for dictObjection in listObjections)
            listLines.append("")
    return "\n".join(listLines)


async def fdictAcceptCampaignPlan(dictControllerState, dictStore,
                                  dictRegistry, sCampaignId):
    """Accept through the engine's consensus gate; write the candidate.

    Acceptance requires ``planReady`` — the engine's own guard decides,
    never the route (remediation R3) — and what lands in ``plan.md`` is
    the council's server-held candidate. With no live runtime (a hub
    restart landed the record at planReady), the engine is rebuilt
    around the restored record with inert connections: acceptance
    drives no provider turn, so a connection that refuses to run is the
    honest stand-in.
    """
    from .agentCouncil import CouncilEngine
    _fnRefuseWhileDriveIsLive(dictControllerState, sCampaignId, "accept")
    dictRuntime = dictControllerState["dictCampaignRuntime"].get(sCampaignId)
    if dictRuntime is not None:
        dictHolder = dictRuntime
    else:
        dictCampaign = agentCouncilCampaign.fdictRestoreCampaignFromMetadata(
            agentCouncilStore.fjsonGetCampaignRecord(dictStore, sCampaignId))

        def _fdictRefuseBaseline(dictRequest):
            raise CouncilCommandError(
                "acceptance drives no baseline evidence")

        dictHolder = {"dictCampaign": dictCampaign}
        dictHolder["engineCouncil"] = CouncilEngine(
            dictCampaign,
            {dictParticipant["sParticipantId"]:
                agentCouncilCampaign.CouncilProviderConnection()
             for dictParticipant in dictCampaign["listParticipants"]},
            lambda dictEvent: agentCouncilStore.fdictAppendCampaignEvent(
                dictStore, sCampaignId, dictEvent),
            lambda dictEntry: agentCouncilStore.fdictRecordCampaignEvidence(
                dictStore, sCampaignId, dictEntry),
            lambda dictSettled: agentCouncilStore.fnCheckpointStoredCampaign(
                dictStore, sCampaignId, dictSettled),
            _fdictRefuseBaseline)
    try:
        dictAccepted = dictHolder["engineCouncil"].fdictAcceptPlan()
    except agentCouncilCampaign.CouncilProtocolError as error:
        raise CouncilCommandError(str(error))
    sLocalPlanPath = agentCouncilStore.fsAcceptCampaignPlanLocally(
        dictStore, sCampaignId,
        fsComposePlanMarkdown(dictAccepted["dictCandidatePlan"] or {}))
    return {"bAccepted": True, "sLocalPlanPath": sLocalPlanPath,
            "dictCampaign": agentCouncilStore.fjsonGetCampaignRecord(
                dictStore, sCampaignId)}


def fiClassifyInterruptedCampaignsOnStartup(dictStore):
    """Classify reloaded mid-turn campaigns as interrupted, never resumed.

    A campaign checkpointed in ``planning`` at a hub crash had a turn
    with no terminal record; a restarted hub cannot account for that
    turn's runners (the labelled-runner reconcile destroys or
    quarantines them separately), so the record says interrupted.
    Returns how many were classified.
    """
    iClassified = 0
    for sCampaignId in list(dictStore["listInsertionOrder"]):
        dictCampaign = agentCouncilStore.fjsonGetCampaignRecord(
            dictStore, sCampaignId)
        if dictCampaign is None or dictCampaign["sState"] != (
                agentCouncilCampaign.S_STATE_PLANNING):
            continue
        agentCouncilCampaign.fnTransitionCampaignState(
            dictCampaign, agentCouncilCampaign.S_STATE_INTERRUPTED,
            "hubRestartedWhileATurnHadNoTerminalRecord")
        agentCouncilStore.fnCheckpointStoredCampaign(
            dictStore, sCampaignId, dictCampaign)
        iClassified += 1
    return iClassified


def _fnRequestRuntimeStopQuietly(dictRuntime):
    """Ask one runtime's live drive to stop; cancel an engine-less one."""
    if dictRuntime.get("engineCouncil") is not None:
        dictRuntime["engineCouncil"].fnRequestStopAfterCurrentTurn()
    taskDrive = dictRuntime.get("taskDrive")
    if taskDrive is not None and not taskDrive.done() and (
            dictRuntime.get("engineCouncil") is None):
        taskDrive.cancel()


def fnDrainControllerForResource(dictControllerState, sResourceName):
    """Stop every live drive whose campaign is bound to one resource.

    The release path: no council deliberation may keep running against
    a project whose lease has just been released. Cooperative for real
    engines (turns settle and runners are destroyed by the connection's
    own completion path); the registry's reservations remain the
    accounting authority either way.
    """
    for dictRuntime in list(
            dictControllerState["dictCampaignRuntime"].values()):
        dictIdentity = dictRuntime["dictCampaign"].get(
            "dictProjectIdentity") or {}
        if dictIdentity.get("sResourceName") == sResourceName:
            _fnRequestRuntimeStopQuietly(dictRuntime)


def fnDrainControllerOnShutdown(dictControllerState):
    """Stop every live drive at hub shutdown, before the registry drain."""
    for dictRuntime in list(
            dictControllerState["dictCampaignRuntime"].values()):
        _fnRequestRuntimeStopQuietly(dictRuntime)
