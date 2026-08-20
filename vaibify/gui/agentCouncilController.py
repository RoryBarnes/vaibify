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
    "fbControllerHasLiveDriveForResource",
    "fgenericSubmitCampaignCommand",
    "fiClassifyInterruptedCampaignsOnStartup",
    "flistReadCampaignCommandLog",
    "fnAwaitControllerSettleOnShutdown",
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
    gateway, wearing the campaign's runner access: the per-campaign
    egress boundary (internal network + allowlisting CONNECT proxy)
    and the staged host credential copy, provisioned once per campaign
    on the first production build. A patched fake seam never invokes
    the provisioner, so the fake-provider lanes need no daemon and no
    persisted login.
    """
    from . import agentCouncilProviders
    dictAccess = _fdictProvisionRunnerAccessOnce(dictRuntime)
    return agentCouncilProviders.ClaudeRunnerConnection(
        _fdictEnsureRuntimeGateway(dictRuntime),
        dictRuntime["sCampaignId"],
        dictRuntime["sImageReference"],
        dictRuntime["baSnapshotTar"],
        dictParticipant["sRequestedModel"],
        dictEgress=dictAccess["dictEgress"],
        sHostCredentialPath=dictAccess["sHostCredentialPath"],
    )


def _fdictProvisionRunnerAccessOnce(dictRuntime):
    """Provision (once) the campaign's egress boundary and credential.

    A production runner is useless without both halves: the internal
    network plus allowlisting proxy are the only path a runner may
    speak to the provider through, and the staged access-token copy is
    the only login it holds (design section 9.7). Memoized on the
    runtime so every participant of a campaign shares one boundary,
    and executed in the launch worker thread, never on the event loop
    — the proxy launch blocks until its listening line appears. A
    fault after the network exists tears the egress resources back
    down before propagating, so a half-provisioned boundary never
    outlives its failed launch.
    """
    if dictRuntime.get("dictRunnerAccess") is not None:
        return dictRuntime["dictRunnerAccess"]
    fsStageRunnerCredential = dictRuntime.get("fsStageRunnerCredential")
    if fsStageRunnerCredential is None:
        raise CouncilCommandError(
            "no credential stager was supplied at launch; a production "
            "runner connection cannot be built without one")
    from . import agentCouncilDockerGateway
    from . import agentCouncilEgress
    from . import agentCouncilProviders
    dictGateway = _fdictEnsureRuntimeGateway(dictRuntime)
    sCampaignId = dictRuntime["sCampaignId"]
    sNetworkName = agentCouncilDockerGateway.fsCreateCampaignInternalNetwork(
        dictGateway, sCampaignId)
    try:
        sProxyInternalAddress = (
            agentCouncilDockerGateway.fsLaunchAllowlistProxy(
                dictGateway, sCampaignId,
                [agentCouncilProviders.S_ANTHROPIC_API_HOSTNAME]))
        sHostCredentialPath = fsStageRunnerCredential()
    except BaseException:
        agentCouncilDockerGateway.fdictRemoveCampaignEgressResources(
            dictGateway, sCampaignId)
        raise
    dictRuntime["dictRunnerAccess"] = {
        "dictEgress": {
            "sNetworkName": sNetworkName,
            "sProxyInternalAddress": sProxyInternalAddress,
            "iProxyPort": agentCouncilEgress.I_PROXY_LISTEN_PORT,
        },
        "sHostCredentialPath": sHostCredentialPath,
    }
    return dictRuntime["dictRunnerAccess"]


def _fnReleaseRunnerAccessResources(dictRuntime):
    """Tear down a campaign's provisioned egress and staged credential.

    Idempotent: a runtime that never provisioned (every patched-fake
    lane) returns at once. The staged host login is removed through the
    secret manager's cleanup, and the egress network and proxy are
    removed with absence proven by the gateway — an indeterminate
    answer is logged by name, never silently swallowed. Called when a
    campaign reaches a state that drives no further provider turn, and
    for every runtime at hub shutdown.
    """
    dictAccess = dictRuntime.get("dictRunnerAccess")
    if dictAccess is None:
        return
    from . import agentCouncilDockerGateway
    from ..config import secretManager
    if dictAccess.get("sHostCredentialPath"):
        secretManager.fnCleanupSecretFiles(
            [dictAccess["sHostCredentialPath"]])
    dictRemoved = (
        agentCouncilDockerGateway.fdictRemoveCampaignEgressResources(
            _fdictEnsureRuntimeGateway(dictRuntime),
            dictRuntime["sCampaignId"]))
    if dictRemoved["saIndeterminateResources"]:
        logger.warning(
            "council campaign %s egress teardown left indeterminate "
            "resources: %s", dictRuntime["sCampaignId"],
            dictRemoved["saIndeterminateResources"])
    dictRuntime["dictRunnerAccess"] = None


# The states in which a campaign will drive no further provider turn,
# so its provisioned runner access (egress boundary, staged credential)
# has nothing left to serve.
LIST_NO_FURTHER_TURN_STATES = [
    agentCouncilCampaign.S_STATE_PLAN_ACCEPTED,
    agentCouncilCampaign.S_STATE_AWAITING_IMPLEMENTATION,
    agentCouncilCampaign.S_STATE_ARCHIVED,
    agentCouncilCampaign.S_STATE_FAILED,
    agentCouncilCampaign.S_STATE_INTERRUPTED,
]


async def _fnReleaseRunnerAccessIfSettled(dictRuntime):
    """Release runner access once the campaign can drive no more turns."""
    if dictRuntime["dictCampaign"]["sState"] in LIST_NO_FURTHER_TURN_STATES:
        await asyncio.to_thread(_fnReleaseRunnerAccessResources, dictRuntime)


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
                               sImageReference, baSnapshotTar,
                               fsStageRunnerCredential=None):
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
        "fsStageRunnerCredential": fsStageRunnerCredential,
        "dictRunnerAccess": None,
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

    # Registered BEFORE the connections build so a failure inside the
    # production factory's provisioning leaves a runtime the launch's
    # failure handler can find, release, and unregister.
    dictControllerState["dictCampaignRuntime"][sCampaignId] = dictRuntime
    dictConnections = {
        dictParticipant["sParticipantId"]:
            fconnectionBuildParticipantConnection(
                dictRuntime, dictParticipant)
        for dictParticipant in dictCampaign["listParticipants"]}
    dictRuntime["engineCouncil"] = CouncilEngine(
        dictCampaign, dictConnections, _fnAppendEngineEvent,
        _fdictRecordEvidence, _fnCheckpoint, _fdictBaseline)
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
        await _fnReleaseRunnerAccessIfSettled(dictRuntime)


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
        ffnCaptureSnapshot, sImageReference,
        fsStageRunnerCredential=None):
    """Capture the snapshot, build the runtime, and start deliberation.

    Runs inside the submitted ``start`` command, so it is serialized
    with every other command for this campaign. ``ffnCaptureSnapshot``
    is the route-supplied closure that captures the immutable context
    under the project's reconciliation lock and returns the manifest;
    ``sImageReference`` is the project container's image, resolved by
    the route BEFORE the credential gate so the evidence record's image
    pin is always compared; ``fsStageRunnerCredential`` is the
    route-supplied closure the production connection factory stages the
    runner's host credential copy through. Closures, because the
    controller must not import the route context. The snapshot identity
    lands in the campaign's identity triple BEFORE the first turn
    launches, and the record checkpoints in between, so a crash at any
    point leaves either a draft with no snapshot or a planning record
    whose snapshot is sealed — never a turn over an unrecorded context.

    The launch is TRANSACTIONAL about the record it leaves behind: a
    registered ``planning`` campaign whose capture, runtime build, or
    provisioning fails transitions to ``failed`` with the fault named
    and checkpoints before the exception propagates — a phantom
    planning record with no drive task never survives a failed start.
    """
    dictCampaign = agentCouncilStore.fjsonGetCampaignRecord(
        dictStore, sCampaignId)
    if dictCampaign is None:
        raise CouncilCommandError(
            f"no stored campaign {sCampaignId!r} to deliberate")
    _fnRefuseWhileDriveIsLive(dictControllerState, sCampaignId, "start")
    try:
        dictManifest = await ffnCaptureSnapshot()
        dictCampaign["dictProjectIdentity"]["sSnapshotIdentity"] = (
            dictManifest["sSnapshotSha256"])
        agentCouncilStore.fnCheckpointStoredCampaign(
            dictStore, sCampaignId, dictCampaign)
        baSnapshotTar = _fbaReadSealedSnapshot(dictStore, sCampaignId)
        dictRuntime = await asyncio.to_thread(
            _fdictBuildCampaignRuntime, dictControllerState, dictStore,
            dictRegistry, sCampaignId, dictCampaign, sImageReference,
            baSnapshotTar, fsStageRunnerCredential)
        sTurnId = _fsSpawnDriveTask(
            dictRuntime,
            dictRuntime["engineCouncil"].fdictRunUntilBlocked)
    except asyncio.CancelledError:
        raise
    except BaseException as error:
        dictBuiltRuntime = dictControllerState["dictCampaignRuntime"].pop(
            sCampaignId, None)
        if dictBuiltRuntime is not None:
            await asyncio.to_thread(
                _fnReleaseRunnerAccessResources, dictBuiltRuntime)
        if dictCampaign["sState"] == agentCouncilCampaign.S_STATE_PLANNING:
            agentCouncilCampaign.fnTransitionCampaignState(
                dictCampaign, agentCouncilCampaign.S_STATE_FAILED,
                f"launchFailedBeforeDeliberation: {type(error).__name__}")
            agentCouncilStore.fnCheckpointStoredCampaign(
                dictStore, sCampaignId, dictCampaign)
        raise
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
    if dictRuntime is not None:
        await _fnReleaseRunnerAccessIfSettled(dictRuntime)
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
    if dictRuntime is not None:
        await _fnReleaseRunnerAccessIfSettled(dictRuntime)
    return {"bStopRequested": True, "bSettled": True,
            "dictCampaign": agentCouncilStore.fjsonGetCampaignRecord(
                dictStore, sCampaignId)}


def fsComposePlanMarkdown(dictCampaign, dictCandidatePlan):
    """Render the council's own candidate into the plan.md text.

    The acceptance artifact is composed from the SERVER-held record
    (remediation R3) — never from caller-supplied text — and carries
    everything a later reader needs to weigh the plan without the
    dashboard: the question, the sealed baseline snapshot identity the
    council reviewed, the participant roster, the synthesis result's
    summary, plan items, security risks, counterexamples attempted and
    open questions, plus the objection provenance the protocol
    recorded (researcher overrides stated as overrides, loudly).
    """
    dictResult = dictCandidatePlan.get("dictResult") or {}
    dictIdentity = dictCampaign.get("dictProjectIdentity") or {}
    listLines = ["# Council plan", "",
                 f"**Question.** {dictCampaign.get('sQuestion', '')}", ""]
    sSnapshotIdentity = dictIdentity.get("sSnapshotIdentity", "")
    if sSnapshotIdentity:
        listLines.extend([
            "**Reviewed baseline.** Sealed snapshot "
            f"`{sSnapshotIdentity}` — the plan speaks about the "
            "repository as it stood at capture, not as it stands now.",
            ""])
    listParticipants = dictCampaign.get("listParticipants") or []
    if listParticipants:
        listLines.append("## Participants")
        for dictParticipant in listParticipants:
            sRoleSuffix = (f" — {dictParticipant['sRole']}"
                           if dictParticipant.get("sRole") else "")
            listLines.append(
                f"- {dictParticipant.get('sProvider', '')} "
                f"(requested model {dictParticipant.get('sRequestedModel', '')})"
                f"{sRoleSuffix}")
        listLines.append("")
    listLines.extend([dictResult.get("sSummary", ""), ""])
    for sResultKey, sHeading, bNumbered in (
            ("listPlanItems", "Plan", True),
            ("listSecurityRisks", "Security risks the council weighed",
             False),
            ("listCounterexamplesAttempted", "Counterexamples attempted",
             False),
            ("listOpenQuestions", "Open questions", False)):
        listItems = dictResult.get(sResultKey) or []
        if listItems:
            listLines.append(f"## {sHeading}")
            if bNumbered:
                listLines.extend(
                    f"{iIndex + 1}. {jsonItem}"
                    for iIndex, jsonItem in enumerate(listItems))
            else:
                listLines.extend(f"- {jsonItem}" for jsonItem in listItems)
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
    if dictRuntime is not None:
        await _fnReleaseRunnerAccessIfSettled(dictRuntime)
    sLocalPlanPath = agentCouncilStore.fsAcceptCampaignPlanLocally(
        dictStore, sCampaignId,
        fsComposePlanMarkdown(dictHolder["dictCampaign"],
                              dictAccepted["dictCandidatePlan"] or {}))
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


def fbControllerHasLiveDriveForResource(dictControllerState, sResourceName):
    """Report whether any campaign bound to one resource is deliberating.

    The release path's busy predicate: a live drive is paid provider
    work the researcher may not know is running, so a lease release
    REFUSES while one lives (the same shape as the live-run refusal)
    rather than silently asking it to stop underneath them.
    """
    for sCampaignId, dictRuntime in list(
            dictControllerState["dictCampaignRuntime"].items()):
        dictIdentity = dictRuntime["dictCampaign"].get(
            "dictProjectIdentity") or {}
        if dictIdentity.get("sResourceName") == sResourceName and (
                fbCampaignDriveIsLive(dictControllerState, sCampaignId)):
            return True
    return False


def fnDrainControllerForResource(dictControllerState, sResourceName):
    """Stop every live drive whose campaign is bound to one resource.

    The release path's second belt: the release route refuses while a
    drive is LIVE (``fbControllerHasLiveDriveForResource``), so by the
    time this runs the remaining stops are requests against runtimes
    between turns — no council deliberation may keep running against a
    project whose lease has just been released. Cooperative for real
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


async def fnAwaitControllerSettleOnShutdown(dictControllerState,
                                            fDeadlineSeconds=2.0):
    """Await live drives briefly at shutdown, then release runner access.

    The stop requests ``fnDrainControllerOnShutdown`` sends are
    cooperative, and a turn mid-CLI can outlive any reasonable shutdown
    window — so the wait is BOUNDED and short, never a settlement
    proof: it exists only to let a drive already at its turn boundary
    settle cleanly (its runner destroyed by the connection's own
    completion path). A drive that misses the deadline is CANCELLED so
    the loop can close, and the registry drain that runs next
    (admission closed) destroys whatever runners remain and records
    what it could not prove gone. What this DOES settle is the
    provisioned runner access: every campaign's egress boundary and
    staged host credential are released here regardless of whether its
    drive made the deadline, so a hub shutdown never strands a
    mode-600 token copy or a council network on the researcher's
    machine.
    """
    listLiveDriveTasks = [
        dictRuntime["taskDrive"]
        for dictRuntime in dictControllerState["dictCampaignRuntime"].values()
        if dictRuntime.get("taskDrive") is not None
        and not dictRuntime["taskDrive"].done()]
    if listLiveDriveTasks:
        await asyncio.wait(listLiveDriveTasks, timeout=fDeadlineSeconds)
        for taskDrive in listLiveDriveTasks:
            if not taskDrive.done():
                taskDrive.cancel()
    for dictRuntime in list(
            dictControllerState["dictCampaignRuntime"].values()):
        await asyncio.to_thread(_fnReleaseRunnerAccessResources, dictRuntime)
