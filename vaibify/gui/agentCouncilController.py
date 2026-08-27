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
import hashlib
import logging

from . import agentCouncilCampaign
from . import agentCouncilCharter
from . import agentCouncilRegistry
from . import agentCouncilRunner
from . import agentCouncilStore

logger = logging.getLogger("vaibify")

__all__ = [
    "CouncilCommandError",
    "LIST_CONTROLLER_COMMANDS",
    "S_COMMAND_ACCEPT_PLAN",
    "S_COMMAND_ASK_CHAIRBOT",
    "S_COMMAND_CLOSE_CHAT",
    "S_COMMAND_DELETE",
    "S_COMMAND_OPEN_CHAT",
    "S_COMMAND_GRANT_RESOLUTION_ROUND",
    "S_COMMAND_REJECT_CANDIDATE",
    "S_COMMAND_RESOLVE_OBJECTIONS",
    "S_COMMAND_RESPOND",
    "S_COMMAND_RESUME",
    "S_COMMAND_RETRY",
    "S_COMMAND_REQUEST_STOP",
    "S_COMMAND_START",
    "S_COUNCIL_CONTROLLER_STATE_KEY",
    "fbCampaignDriveIsLive",
    "fdictCreateCouncilControllerState",
    "fdictLaunchCampaignDeliberation",
    "fdictRequestCampaignStop",
    "fdictAcceptCampaignPlan",
    "fdictContinueCampaignAfterResponse",
    "fdictResumeCampaignDeliberation",
    "fdictRetryCampaignFailedPhase",
    "fdictGrantCampaignResolutionRound",
    "fdictRejectCampaignCandidate",
    "fdictResolveCampaignObjections",
    "fsComposePlanMarkdown",
    "fbCloseResourceAdmission",
    "fbControllerHasLiveDriveForResource",
    "fbResourceAdmissionIsClosed",
    "fdictDisposeCampaignRuntime",
    "fgenericSubmitCampaignCommand",
    "fnReopenResourceAdmission",
    "fiClassifyInterruptedCampaignsOnStartup",
    "flistReadCampaignCommandLog",
    "fnAwaitControllerSettleOnShutdown",
    "fdictDrainControllerForResource",
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
# The ask-the-chairbot lane. These drive no engine and write no campaign
# state, but they are submitted here so a conversation's lifecycle is
# serialized against the campaign's: an open must not race a delete, and
# a close must not race a drain.
S_COMMAND_OPEN_CHAT = "openChat"
S_COMMAND_ASK_CHAIRBOT = "askChairbot"
S_COMMAND_CLOSE_CHAT = "closeChat"
# Explicit researcher resume of a crashed deliberation (continuation
# plan section 4): never unattended, always from a boundary the durable
# phase-attempt record proves coherent.
S_COMMAND_RESUME = "resume"
# Retire the terminating attempt and re-run its phase
# (continuation plan 2.5/2.6).
S_COMMAND_RETRY = "retryFailedPhase"

LIST_CONTROLLER_COMMANDS = [
    S_COMMAND_START,
    S_COMMAND_RESPOND,
    S_COMMAND_REQUEST_STOP,
    S_COMMAND_ACCEPT_PLAN,
    S_COMMAND_DELETE,
    S_COMMAND_GRANT_RESOLUTION_ROUND,
    S_COMMAND_RESOLVE_OBJECTIONS,
    S_COMMAND_REJECT_CANDIDATE,
    S_COMMAND_OPEN_CHAT,
    S_COMMAND_ASK_CHAIRBOT,
    S_COMMAND_CLOSE_CHAT,
    S_COMMAND_RESUME,
    S_COMMAND_RETRY,
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
    from . import agentCouncilChat
    return {
        "dictCampaignLocks": {},
        "dictCommandLogByCampaign": {},
        "dictCampaignRuntime": {},
        # The ask-the-chairbot conversations, keyed by campaign id and
        # owned by ``agentCouncilChat``. They live here because they
        # share this dict's lifetime and its release semantics: what
        # settles a container's live council state has to settle its
        # conversations too, and a second app-state key would be a
        # second thing to forget.
        agentCouncilChat.S_CHAT_SESSIONS_KEY: {},
        # Resource names whose council admission the release authority
        # has CLOSED: no launch and no turn-driving continuation may
        # pass while a name is here. Closed atomically before the
        # lease is dropped; reopened on a successful claim (or when a
        # refused release aborts).
        "setClosedResourceAdmissions": set(),
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
        fsStageRunnerCredential=dictRuntime["fsStageRunnerCredential"],
        # The campaign's own budget, not the module default. Without
        # this the setting is a number in a record that governs nothing
        # — the shape of bAgentSafe before it was enforced.
        fWallClockSeconds=float(
            (dictRuntime.get("dictCampaign") or {}).get("dictSettings", {})
            .get("iTurnWallClockSeconds")
            or agentCouncilRunner.F_DEFAULT_TURN_WALL_CLOCK_SECONDS),
    )


def _fdictProvisionRunnerAccessOnce(dictRuntime):
    """Provision (once) the campaign's egress boundary.

    A production runner is useless without its two halves: the
    internal network plus allowlisting proxy provisioned here are the
    only path a runner may speak to the provider through, and the
    credential is staged PER TURN by the connection itself (staged at
    runner creation, host file deleted the moment its tarball is
    built) so no token copy sits at rest while a campaign waits on the
    researcher. Memoized on the runtime so every participant of a
    campaign shares one boundary, and executed in the launch worker
    thread, never on the event loop — the proxy launch blocks until
    its listening line appears. A fault after the network exists tears
    the egress resources back down before propagating, so a
    half-provisioned boundary never outlives its failed launch.
    """
    if dictRuntime.get("dictRunnerAccess") is not None:
        return dictRuntime["dictRunnerAccess"]
    if dictRuntime.get("fsStageRunnerCredential") is None:
        raise CouncilCommandError(
            "no credential stager was supplied at launch; a production "
            "runner connection cannot be built without one")
    from . import agentCouncilDockerGateway
    from . import agentCouncilEgress
    from . import agentCouncilProviders
    dictGateway = _fdictEnsureRuntimeGateway(dictRuntime)
    sCampaignId = dictRuntime["sCampaignId"]
    # The tombstone is recorded BEFORE anything is created: a fault
    # midway through provisioning leaves ``dictRunnerAccess`` in place
    # unless the in-line cleanup POSITIVELY proved absence, so an
    # indeterminate answer keeps its retry state exactly like a failed
    # teardown after a successful launch — the earlier shape cleaned
    # up in-line, ignored the settlement, and let the outer handler
    # read the still-None access as "nothing to release".
    dictEgress = {
        "sNetworkName": "",
        "sProxyInternalAddress": "",
        "iProxyPort": agentCouncilEgress.I_PROXY_LISTEN_PORT,
    }
    dictRuntime["dictRunnerAccess"] = {"dictEgress": dictEgress}
    try:
        dictEgress["sNetworkName"] = (
            agentCouncilDockerGateway.fsCreateCampaignInternalNetwork(
                dictGateway, sCampaignId))
        dictEgress["sProxyInternalAddress"] = (
            agentCouncilDockerGateway.fsLaunchAllowlistProxy(
                dictGateway, sCampaignId,
                [agentCouncilProviders.S_ANTHROPIC_API_HOSTNAME]))
    except BaseException:
        dictRemoved = (
            agentCouncilDockerGateway.fdictRemoveCampaignEgressResources(
                dictGateway, sCampaignId))
        if not dictRemoved["saIndeterminateResources"]:
            dictRuntime["dictRunnerAccess"] = None
        else:
            logger.warning(
                "council campaign %s half-provisioned egress teardown "
                "left indeterminate resources (tombstone kept): %s",
                sCampaignId, dictRemoved["saIndeterminateResources"])
        raise
    return dictRuntime["dictRunnerAccess"]


def _fbReleaseRunnerAccessResources(dictRuntime):
    """Tear down a campaign's egress boundary; report whether it SETTLED.

    Idempotent: a runtime that never provisioned (every patched-fake
    lane) settles at once. The egress network and proxy are removed
    with absence proven by the gateway; an INDETERMINATE answer keeps
    ``dictRunnerAccess`` in place and answers False — the retry state
    is the record that something may still exist, and every caller
    must treat False as "not done": the release drain retains the
    runtime, delete REFUSES to drop the durable record (the startup
    sweep composes names from stored campaign ids, so deleting the
    record would orphan the very resource nobody proved gone). No
    credential file is handled here: the login copy is staged per turn
    and deleted the moment its delivery tarball is built.
    """
    dictAccess = dictRuntime.get("dictRunnerAccess")
    if dictAccess is None:
        return True
    from . import agentCouncilDockerGateway
    dictRemoved = (
        agentCouncilDockerGateway.fdictRemoveCampaignEgressResources(
            _fdictEnsureRuntimeGateway(dictRuntime),
            dictRuntime["sCampaignId"]))
    if dictRemoved["saIndeterminateResources"]:
        logger.warning(
            "council campaign %s egress teardown left indeterminate "
            "resources (kept for retry; the startup egress sweep is "
            "the durable backstop): %s", dictRuntime["sCampaignId"],
            dictRemoved["saIndeterminateResources"])
        return False
    dictRuntime["dictRunnerAccess"] = None
    return True


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
    """Release runner access once the campaign can drive no more turns.

    An indeterminate teardown keeps the runtime's retry state; the
    runtime itself stays registered on these paths, so the state
    survives for a later attempt and the startup sweep remains the
    durable backstop.
    """
    if dictRuntime["dictCampaign"]["sState"] in LIST_NO_FURTHER_TURN_STATES:
        await asyncio.to_thread(_fbReleaseRunnerAccessResources, dictRuntime)


def _fdictEnsureRuntimeGateway(dictRuntime):
    """Create (once) and return the campaign runtime's Docker gateway.

    The gateway carries the campaign's project container name, so every
    container it creates is stamped with the owner a peer hub's startup
    reconcile has to recognise.
    """
    if dictRuntime.get("dictGateway") is None:
        from . import agentCouncilDockerGateway
        dictRuntime["dictGateway"] = (
            agentCouncilDockerGateway.fdictCreateCouncilDockerGateway(
                agentCouncilDockerGateway.fdockerCreateCouncilClient(),
                dictRuntime["dictRegistry"],
                (dictRuntime["dictCampaign"].get("dictProjectIdentity")
                 or {}).get("sResourceName", "")))
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
        # True from registration until the first drive task is spawned:
        # the provisioning window is live work the busy predicates must
        # see, or a lease release could pass while the proxy is being
        # created (taskDrive is still None then).
        "bLaunchInProgress": True,
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
    """Report whether a campaign is driving or still launching.

    The launch window counts: between runtime registration and the
    first drive-task spawn the campaign is provisioning its egress
    boundary, which is live work exactly like a turn — a release that
    passed inside that window would pull the lease out from under a
    proxy mid-creation.
    """
    dictRuntime = dictControllerState["dictCampaignRuntime"].get(sCampaignId)
    if dictRuntime is None:
        return False
    if dictRuntime.get("bLaunchInProgress"):
        return True
    if dictRuntime.get("taskDrive") is None:
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


async def _fnSettleChatConversationBeforeCampaignWork(
        dictControllerState, sCampaignId, sAction):
    """Rest this campaign's chairbot conversation so its runner settles.

    The conversation's runner holds a live reservation on the campaign
    (one runner per conversation), so an open chat turned every
    "Record decision" into the unsettled-work refusal and sent the
    researcher to reconcile over a perfectly healthy conversation
    (2026-08-27). The conversation is RESTED here, not closed: the
    runner is destroyed with absence proven — which is all the
    unsettled gate needs — while the transcript stays, and the
    researcher's next question wakes it. An answer still in flight is
    paid provider work and refuses with the real reason instead of
    being destroyed mid-turn. A teardown the daemon could not prove
    leaves the reservation quarantined, and the unsettled gate that
    follows then refuses honestly.
    """
    from . import agentCouncilChat
    if agentCouncilChat.fbChatAnswerInFlightForCampaign(
            dictControllerState, sCampaignId):
        raise CouncilCommandError(
            f"cannot {sAction}: the chairbot is still answering a "
            "conversation message; wait for its answer (or close the "
            "conversation), then retry.")
    await agentCouncilChat.fdictRestChatConversation(
        dictControllerState, sCampaignId)


def _fnRefuseUnsettledCampaignWork(dictRegistry, dictCampaign,
                                   sCampaignId, sAction):
    """Refuse over ANY unsettled reservation, request, or peer claim.

    Not only quarantined (continuation plan 4.2.1): pending and live
    reservations equally represent runners the hub cannot prove gone,
    an active API request is spend nobody accounted for, and a
    campaign held by a live peer hub is not this hub's to drive.
    """
    listUnsettled = [
        dictReservation for dictReservation
        in dictRegistry["dictReservationsById"].values()
        if dictReservation["sCampaignId"] == sCampaignId
        and dictReservation["sStatus"]
        in agentCouncilRegistry.SET_LIVE_RESERVATION_STATUSES]
    if listUnsettled or any(
            sCid == sCampaignId
            for sCid, _ in dictRegistry["setTurnsInFlight"]) or any(
            dictRequest["sCampaignId"] == sCampaignId
            and dictRequest["sStatus"]
            == agentCouncilRegistry.S_API_REQUEST_ACTIVE
            for dictRequest in dictRegistry["dictApiRequestsById"].values()):
        raise CouncilCommandError(
            f"cannot {sAction}: this campaign still has unsettled "
            "runner reservations or requests nobody proved gone. Run "
            "vaibify reconcile, then retry.")
    if agentCouncilRegistry.fbCampaignBelongsToALivePeerHub(dictCampaign):
        raise CouncilCommandError(
            f"cannot {sAction}: another live hub holds this campaign's "
            "project; resume it from that hub, or release it there "
            "first.")


def _fbaAdmitRuntimeRebuild(dictStore, dictCampaign, sCampaignId,
                            sImageReference, sAction):
    """Admit a runtime rebuild, or refuse naming what changed.

    Returns the validated sealed snapshot bytes. Two identity checks,
    both against values PINNED at launch (researcher ruling 3): the
    execution image — a resume that would change it is refused with no
    override, the remedy is a fresh council — and the sealed archive's
    byte digest, because ``sSnapshotIdentity`` is a content identity
    over manifest rows and validates nothing about the tar the runners
    would be handed.
    """
    dictIdentity = dictCampaign.get("dictProjectIdentity") or {}
    sRecordedImage = dictIdentity.get("sImageIdentity", "")
    if not sRecordedImage:
        raise CouncilCommandError(
            f"cannot {sAction}: this campaign recorded no execution "
            "image identity (it predates the pin), so image sameness "
            "cannot be proven; convene a fresh council")
    if sRecordedImage != sImageReference:
        raise CouncilCommandError(
            f"cannot {sAction}: the project container's image changed "
            f"since this council ran (recorded {sRecordedImage}, "
            f"current {sImageReference}). The council's reasoning is "
            "bound to the image its runners executed in; convene a "
            "fresh council.")
    sRecordedDigest = dictIdentity.get("sSnapshotArchiveSha256", "")
    if not sRecordedDigest:
        raise CouncilCommandError(
            f"cannot {sAction}: this campaign recorded no snapshot "
            "archive digest (it predates the pin), so the sealed "
            "baseline cannot be validated; convene a fresh council")
    baSnapshotTar = _fbaReadSealedSnapshot(dictStore, sCampaignId)
    sCurrentDigest = hashlib.sha256(baSnapshotTar).hexdigest()
    if sCurrentDigest != sRecordedDigest:
        raise CouncilCommandError(
            f"cannot {sAction}: the sealed snapshot archive on disk "
            "does not match the digest recorded at launch — the "
            "baseline the council reasoned about cannot be "
            "reconstructed. Convene a fresh council.")
    return baSnapshotTar


async def _fdictRebuildRuntimeNonDestructively(
        dictControllerState, dictStore, dictRegistry, sCampaignId,
        dictCampaign, sImageReference, fsStageRunnerCredential):
    """Rebuild a campaign runtime without ever touching the record.

    The launch path is transactional the OTHER way — a build fault
    moves planning to failed and checkpoints before re-raising. A
    rebuild needs the opposite (continuation plan 4.3): the record a
    researcher is rescuing must be byte-identical after a failed
    rebuild, or the rescue destroys the gate it exists to reach. So
    resources are released on failure exactly as the launch does, but
    no transition is written and no checkpoint runs.
    """
    if agentCouncilStore.fbCampaignProvenanceUnavailable(
            dictStore, sCampaignId):
        raise CouncilCommandError(
            "cannot resume: this campaign's provenance sidecar is "
            "missing or unreadable, so its evidence history and turn "
            "identifiers cannot be trusted; convene a fresh council")
    baSnapshotTar = _fbaAdmitRuntimeRebuild(
        dictStore, dictCampaign, sCampaignId, sImageReference, "resume")
    taskBuild = asyncio.ensure_future(asyncio.to_thread(
        _fdictBuildCampaignRuntime, dictControllerState, dictStore,
        dictRegistry, sCampaignId, dictCampaign, sImageReference,
        baSnapshotTar, fsStageRunnerCredential))
    try:
        dictRuntime = await asyncio.shield(taskBuild)
    except BaseException:
        await _fnAwaitBuildWorkerCompletion(taskBuild)
        dictBuiltRuntime = dictControllerState["dictCampaignRuntime"].get(
            sCampaignId)
        if dictBuiltRuntime is not None:
            dictBuiltRuntime["bLaunchInProgress"] = False
            bAccessSettled = await asyncio.to_thread(
                _fbReleaseRunnerAccessResources, dictBuiltRuntime)
            if bAccessSettled:
                dictControllerState["dictCampaignRuntime"].pop(
                    sCampaignId, None)
        raise
    dictRuntime["bLaunchInProgress"] = False
    return dictRuntime


async def fdictResumeCampaignDeliberation(
        dictControllerState, dictStore, dictRegistry, sCampaignId,
        sImageReference, fsStageRunnerCredential=None,
        bClearStopRequest=False):
    """Resume a crashed deliberation from its proven boundary.

    The recovery actions are keyed by the durable attempt record
    (continuation plan 2.5): ``turnsSettled`` replays settlement first
    and then acts on the resulting outcome; ``advancedToNextPhase`` and
    ``roundResolved`` rebuild the runtime and let the engine continue
    the walk. ``running`` and a record with no attempt refuse — the
    stopping-point descriptor already said so in the listing, and the
    route re-derives the same answer here at the click.
    """
    dictCampaign = agentCouncilStore.fjsonGetCampaignRecord(
        dictStore, sCampaignId)
    if dictCampaign is None:
        raise CouncilCommandError(
            f"no stored campaign {sCampaignId!r} to resume")
    dictCampaign = agentCouncilCampaign.fdictRestoreCampaignFromMetadata(
        dictCampaign)
    _fnRefuseWhileDriveIsLive(dictControllerState, sCampaignId, "resume")
    if dictControllerState["dictCampaignRuntime"].get(
            sCampaignId) is not None:
        raise CouncilCommandError(
            "this campaign already has a live runtime; there is "
            "nothing to resume")
    _fnRefuseWhenResourceAdmissionClosed(
        dictControllerState, dictCampaign, "resume")
    await _fnSettleChatConversationBeforeCampaignWork(
        dictControllerState, sCampaignId, "resume")
    _fnRefuseUnsettledCampaignWork(
        dictRegistry, dictCampaign, sCampaignId, "resume")
    if dictCampaign["sState"] != agentCouncilCampaign.S_STATE_PLANNING:
        raise CouncilCommandError(
            "only a campaign stopped mid-deliberation can be resumed; "
            f"this one is {dictCampaign['sState']!r} — answer its gate, "
            "review its plan, or convene a fresh council")
    listRounds = dictCampaign.get("listRounds") or []
    dictAttempt = (listRounds[-1] if listRounds else {}).get(
        "dictPhaseAttempt")
    if dictAttempt is None:
        raise CouncilCommandError(
            "this campaign was checkpointed by an earlier hub version "
            "that recorded no phase attempts, so where it stopped "
            "cannot be proven; convene a fresh council")
    sAttemptState = dictAttempt.get("sAttemptState", "")
    if sAttemptState == "running":
        raise CouncilCommandError(
            "a phase attempt was still running when this council "
            "stopped — its launched runners cannot be proven gone. "
            "Run vaibify reconcile, then retry the phase.")
    if sAttemptState == "outcomeSettled" and dictAttempt.get(
            "sOutcome") not in ("advancedToNextPhase", "roundResolved"):
        raise CouncilCommandError(
            "this campaign's last settled outcome "
            f"({dictAttempt.get('sOutcome') or 'unrecorded'}) is not a "
            "resumable boundary")
    if dictCampaign.get("bStopRequested"):
        if not bClearStopRequest:
            raise CouncilCommandError(
                "a stop was requested before this council stopped, and "
                "resuming would archive it immediately. Resume with the "
                "stop cleared to continue deliberating, or convene a "
                "fresh council if the stop should stand.")
        # An explicit, recorded researcher decision — never a silent
        # clear (continuation plan 4.2.5). The decision rides the
        # runtime's record and reaches disk with the next checkpoint.
        dictCampaign["bStopRequested"] = False
        dictCampaign.setdefault("listResearcherDecisions", []).append(
            {"sDecisionKind": "stopRequestClearedOnResume"})
    dictRuntime = await _fdictRebuildRuntimeNonDestructively(
        dictControllerState, dictStore, dictRegistry, sCampaignId,
        dictCampaign, sImageReference, fsStageRunnerCredential)
    if sAttemptState == "turnsSettled":
        dictReplayed = dictRuntime[
            "engineCouncil"].fdictReplaySettlementFromTurnRecords()
        if dictReplayed["sState"] != agentCouncilCampaign.S_STATE_PLANNING:
            # The replayed settlement reached a gate or a terminal
            # state: the record says so durably and there is no walk to
            # continue. A gate KEEPS the rebuilt runtime — its answer
            # drives it next — but a terminal state must release the
            # runner access this resume just provisioned, or the proxy
            # and network leak and the read route reports a dead
            # campaign as live deliberation. Same discriminating
            # helper the drive-settlement path uses.
            await _fnReleaseRunnerAccessIfSettled(dictRuntime)
            if dictReplayed["sState"] in LIST_NO_FURTHER_TURN_STATES:
                dictControllerState["dictCampaignRuntime"].pop(
                    sCampaignId, None)
            return {"bResumed": True, "sTurnId": "",
                    "sState": dictReplayed["sState"]}
    sTurnId = _fsSpawnDriveTask(
        dictRuntime, dictRuntime["engineCouncil"].fdictRunUntilBlocked)
    return {"bResumed": True, "sTurnId": sTurnId,
            "sState": dictRuntime["dictCampaign"]["sState"]}


async def fdictRetryCampaignFailedPhase(
        dictControllerState, dictStore, dictRegistry, sCampaignId,
        sImageReference, fsStageRunnerCredential=None,
        bClearStopRequest=False):
    """Retire the terminating attempt and re-run its phase (2.5/2.6).

    The retry target is the LAST attempt — the one whose settled
    outcome terminated the campaign — never a phase-order inference.
    Admission mirrors resume: the same unsettled-work refusal is what
    makes "Reconcile, then Retry" real for an interrupted campaign
    (a reservation nobody proved gone refuses right here), the same
    identity pins, the same non-destructive rebuild. The whitelist
    refuses failures that would repeat identically, with the reason
    named. Retirement itself runs INSIDE the engine so the restored
    pre-phase state and the planning transition land in one
    checkpoint; the evidence marking precedes it, so a crash between
    the two leaves entries marked for an attempt that is still
    terminal — re-running retry is idempotent about that.
    """
    from . import agentCouncilResolution
    dictCampaign = agentCouncilStore.fjsonGetCampaignRecord(
        dictStore, sCampaignId)
    if dictCampaign is None:
        raise CouncilCommandError(
            f"no stored campaign {sCampaignId!r} to retry")
    dictCampaign = agentCouncilCampaign.fdictRestoreCampaignFromMetadata(
        dictCampaign)
    _fnRefuseWhileDriveIsLive(dictControllerState, sCampaignId, "retry")
    if dictControllerState["dictCampaignRuntime"].get(
            sCampaignId) is not None:
        raise CouncilCommandError(
            "this campaign already has a live runtime; there is "
            "nothing to retry")
    _fnRefuseWhenResourceAdmissionClosed(
        dictControllerState, dictCampaign, "retry")
    await _fnSettleChatConversationBeforeCampaignWork(
        dictControllerState, sCampaignId, "retry")
    _fnRefuseUnsettledCampaignWork(
        dictRegistry, dictCampaign, sCampaignId, "retry")
    if agentCouncilStore.fbCampaignProvenanceUnavailable(
            dictStore, sCampaignId):
        raise CouncilCommandError(
            "cannot retry: this campaign's provenance sidecar is "
            "missing or unreadable; convene a fresh council")
    if dictCampaign["sState"] not in (
            agentCouncilCampaign.S_STATE_FAILED,
            agentCouncilCampaign.S_STATE_INTERRUPTED):
        raise CouncilCommandError(
            "only a failed or interrupted campaign can retry a phase; "
            f"this one is {dictCampaign['sState']!r}")
    listRounds = dictCampaign.get("listRounds") or []
    dictRound = listRounds[-1] if listRounds else None
    dictAttempt = (dictRound or {}).get("dictPhaseAttempt")
    sRetryRefusal = agentCouncilResolution.fsClassifyRetryEligibility(
        dictRound, dictAttempt)
    if sRetryRefusal:
        raise CouncilCommandError(f"cannot retry: {sRetryRefusal}")
    if dictCampaign.get("bStopRequested"):
        # The same choice resume surfaces (4.2.5), for the same reason
        # sharpened: retirement transitions to planning and spawns the
        # drive, whose FIRST act on a set flag is to archive — so a
        # kept flag turns "retry" into "silently destroy". Never
        # silently cleared, never silently kept.
        if not bClearStopRequest:
            raise CouncilCommandError(
                "a stop was requested before this council stopped, and "
                "retrying would archive it immediately. Retry with the "
                "stop cleared to re-run the phase, or convene a fresh "
                "council if the stop should stand.")
        dictCampaign["bStopRequested"] = False
        dictCampaign.setdefault("listResearcherDecisions", []).append(
            {"sDecisionKind": "stopRequestClearedOnRetry"})
    baSnapshotTar = _fbaAdmitRuntimeRebuild(
        dictStore, dictCampaign, sCampaignId, sImageReference, "retry")
    taskBuild = asyncio.ensure_future(asyncio.to_thread(
        _fdictBuildCampaignRuntime, dictControllerState, dictStore,
        dictRegistry, sCampaignId, dictCampaign, sImageReference,
        baSnapshotTar, fsStageRunnerCredential))
    try:
        dictRuntime = await asyncio.shield(taskBuild)
    except BaseException:
        await _fnAwaitBuildWorkerCompletion(taskBuild)
        dictBuiltRuntime = dictControllerState["dictCampaignRuntime"].get(
            sCampaignId)
        if dictBuiltRuntime is not None:
            dictBuiltRuntime["bLaunchInProgress"] = False
            bAccessSettled = await asyncio.to_thread(
                _fbReleaseRunnerAccessResources, dictBuiltRuntime)
            if bAccessSettled:
                dictControllerState["dictCampaignRuntime"].pop(
                    sCampaignId, None)
        raise
    dictRuntime["bLaunchInProgress"] = False
    agentCouncilStore.fnMarkEvidenceRetiredForAttempt(
        dictStore, sCampaignId,
        f"{dictAttempt['sPhase']}#{dictAttempt['iAttemptNumber']}")
    dictRuntime["engineCouncil"].fdictRetireTerminalAttempt()
    sTurnId = _fsSpawnDriveTask(
        dictRuntime, dictRuntime["engineCouncil"].fdictRunUntilBlocked)
    return {"bRetried": True, "sTurnId": sTurnId,
            "sRetriedPhase": dictAttempt["sPhase"],
            "iRetiredAttemptNumber": dictAttempt["iAttemptNumber"]}


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
    _fnRefuseWhenResourceAdmissionClosed(
        dictControllerState, dictCampaign, "start")
    taskBuild = None
    try:
        dictManifest = await ffnCaptureSnapshot()
        dictCampaign["dictProjectIdentity"]["sSnapshotIdentity"] = (
            dictManifest["sSnapshotSha256"])
        dictCampaign["dictProjectIdentity"]["sSnapshotScopeNote"] = (
            agentCouncilCharter.fsDescribeSnapshotScope(
                dictManifest.get("listResearcherExcludedPaths") or []))
        baSnapshotTar = _fbaReadSealedSnapshot(dictStore, sCampaignId)
        # Pinned before the first turn, checkpointed with the snapshot
        # identity: the image the runners execute in and the sealed
        # archive's BYTE digest (sSnapshotSha256 is a content identity
        # over manifest rows, which validates nothing about the tar a
        # resume would hand back to the runners). Resume compares both
        # and refuses on difference (continuation plan 4.2).
        dictCampaign["dictProjectIdentity"]["sImageIdentity"] = (
            sImageReference)
        dictCampaign["dictProjectIdentity"]["sSnapshotArchiveSha256"] = (
            hashlib.sha256(baSnapshotTar).hexdigest())
        agentCouncilStore.fnCheckpointStoredCampaign(
            dictStore, sCampaignId, dictCampaign)
        # SHIELDED: cancelling this await cancels only the awaiting
        # future, never the worker thread, which would otherwise keep
        # registering the runtime and provisioning egress AFTER the
        # cleanup below had already run and found nothing. The failure
        # handler waits the thread out before cleaning, so cleanup
        # always sees what the build actually built.
        taskBuild = asyncio.ensure_future(asyncio.to_thread(
            _fdictBuildCampaignRuntime, dictControllerState, dictStore,
            dictRegistry, sCampaignId, dictCampaign, sImageReference,
            baSnapshotTar, fsStageRunnerCredential))
        dictRuntime = await asyncio.shield(taskBuild)
        sTurnId = _fsSpawnDriveTask(
            dictRuntime,
            dictRuntime["engineCouncil"].fdictRunUntilBlocked)
        dictRuntime["bLaunchInProgress"] = False
    except BaseException as error:
        await _fnAwaitBuildWorkerCompletion(taskBuild)
        # Cancellation takes this SAME settlement path before
        # re-raising: skipping it left a runtime permanently
        # bLaunchInProgress (forever "busy" to every predicate) with
        # possibly-live egress nobody would release. A cancelled
        # coroutine may still await — the cleanup below runs before
        # the CancelledError propagates.
        dictBuiltRuntime = dictControllerState["dictCampaignRuntime"].get(
            sCampaignId)
        if dictBuiltRuntime is not None:
            dictBuiltRuntime["bLaunchInProgress"] = False
            bAccessSettled = await asyncio.to_thread(
                _fbReleaseRunnerAccessResources, dictBuiltRuntime)
            if bAccessSettled:
                dictControllerState["dictCampaignRuntime"].pop(
                    sCampaignId, None)
        if dictCampaign["sState"] == agentCouncilCampaign.S_STATE_PLANNING:
            bCancelled = isinstance(error, asyncio.CancelledError)
            agentCouncilCampaign.fnTransitionCampaignState(
                dictCampaign,
                agentCouncilCampaign.S_STATE_INTERRUPTED if bCancelled
                else agentCouncilCampaign.S_STATE_FAILED,
                "launchCancelledBeforeDeliberation" if bCancelled
                else f"launchFailedBeforeDeliberation: "
                     f"{type(error).__name__}")
            agentCouncilStore.fnCheckpointStoredCampaign(
                dictStore, sCampaignId, dictCampaign)
        raise
    return {"sTurnId": sTurnId}


async def _fnAwaitBuildWorkerCompletion(taskBuild):
    """Wait the runtime-build worker thread out, absorbing every outcome.

    A worker thread cannot be interrupted, so the launch's failure
    handler must not clean up until the thread has actually finished —
    otherwise the thread registers the runtime and provisions egress
    AFTER cleanup found nothing (reproduced by review: the runtime
    appeared after the handler completed). Repeated cancellation of
    this wait is tolerated: the shield is re-entered until the future
    settles, and the worker's own exception (already handled by the
    caller) is swallowed here.
    """
    if taskBuild is None:
        return
    while not taskBuild.done():
        try:
            await asyncio.shield(taskBuild)
        except asyncio.CancelledError:
            continue
        except BaseException:
            break


def _fbaReadSealedSnapshot(dictStore, sCampaignId):
    """Read the sealed snapshot tarball beside the campaign record."""
    from . import agentCouncilContext
    return agentCouncilContext.fbaReadSealedSnapshotArchive(
        dictStore["sDurableStoreRoot"], sCampaignId)


async def _fdictRequireOrRebuildRuntime(
        dictControllerState, dictStore, dictRegistry, sCampaignId,
        sAction, dictRebuildMaterials=None):
    """Return the continuation runtime, rebuilding it when the hub died.

    The original live failure (continuation plan 0.1): a needsHuman
    campaign survives on disk, the panel shows its gate and an answer
    box, and the answer is then refused because the in-process runtime
    died with the hub. The gate is live in the record, so the honest
    behaviour is ruling 2.5's — the runtime is rebuilt when the answer
    is SUBMITTED, under the same admissions a resume passes, and a
    failed rebuild leaves the record byte-identical (4.3): the
    13-question gate this path exists to rescue is exactly what a
    transactional launch-style failure handler would destroy.
    """
    _fnRefuseWhileDriveIsLive(dictControllerState, sCampaignId, sAction)
    dictRuntime = dictControllerState["dictCampaignRuntime"].get(sCampaignId)
    if dictRuntime is not None:
        _fnRefuseWhenResourceAdmissionClosed(
            dictControllerState, dictRuntime["dictCampaign"], sAction)
        return dictRuntime
    if dictRebuildMaterials is None:
        raise CouncilCommandError(
            "this campaign has no live deliberation to continue — the "
            "hub restarted since it ran; convene a fresh council")
    dictCampaign = agentCouncilStore.fjsonGetCampaignRecord(
        dictStore, sCampaignId)
    if dictCampaign is None:
        raise CouncilCommandError(
            f"no stored campaign {sCampaignId!r} to continue")
    dictCampaign = agentCouncilCampaign.fdictRestoreCampaignFromMetadata(
        dictCampaign)
    if dictCampaign["sState"] != agentCouncilCampaign.S_STATE_NEEDS_HUMAN:
        raise CouncilCommandError(
            "this campaign has no live deliberation to continue — the "
            "hub restarted since it ran; convene a fresh council")
    _fnRefuseWhenResourceAdmissionClosed(
        dictControllerState, dictCampaign, sAction)
    await _fnSettleChatConversationBeforeCampaignWork(
        dictControllerState, sCampaignId, sAction)
    _fnRefuseUnsettledCampaignWork(
        dictRegistry, dictCampaign, sCampaignId, sAction)
    return await _fdictRebuildRuntimeNonDestructively(
        dictControllerState, dictStore, dictRegistry, sCampaignId,
        dictCampaign,
        dictRebuildMaterials["sImageReference"],
        dictRebuildMaterials.get("fsStageRunnerCredential"))


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
        sResponseText, listDecisionAnswers=None,
        dictRebuildMaterials=None):
    """Answer a blocking question and relaunch deliberation.

    Refuses while a drive task is live, when the campaign has no
    runtime to continue (a hub restart classified it interrupted; a
    fresh council is the honest path — the engine is never resumed
    over runners nobody can account for), and when the pending gate is
    the exhausted-rounds one, whose only continuations are its three
    exits.
    """
    dictRuntime = await _fdictRequireOrRebuildRuntime(
        dictControllerState, dictStore, dictRegistry, sCampaignId,
        "respond", dictRebuildMaterials)
    _fnRequireHumanGate(dictRuntime)
    sTurnId = _fsSpawnDriveTask(
        dictRuntime,
        lambda: dictRuntime["engineCouncil"]
        .fdictContinueAfterResearcherResponse(
            sResponseText, listDecisionAnswers))
    return {"sTurnId": sTurnId}


async def fdictGrantCampaignResolutionRound(
        dictControllerState, dictStore, dictRegistry, sCampaignId,
        iGrantedRounds, dictRebuildMaterials=None):
    """Exhausted-round exit 1: grant a bounded resolution round."""
    dictRuntime = await _fdictRequireOrRebuildRuntime(
        dictControllerState, dictStore, dictRegistry, sCampaignId,
        "grant a resolution round", dictRebuildMaterials)
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
        dictDispositionByObjectionId, dictRebuildMaterials=None):
    """Exhausted-round exit 2: dispose every objection, one final veto."""
    dictRuntime = await _fdictRequireOrRebuildRuntime(
        dictControllerState, dictStore, dictRegistry, sCampaignId,
        "resolve objections", dictRebuildMaterials)
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


def _fdictCollectResolvedModelsByParticipant(dictCampaign):
    """Map participant id → last mechanically-recorded resolved model.

    Read from the turn records the engine stamped (design 13.2); a
    participant whose stream never reported a resolution stays absent,
    so the renderer says "unrecorded" rather than laundering the
    requested alias into a resolved identity.
    """
    dictResolvedModels = {}
    for dictRound in dictCampaign.get("listRounds") or []:
        for listTurns in (dictRound.get("dictTurnsByPhase") or {}).values():
            for dictTurnRecord in listTurns:
                sResolvedModel = (dictTurnRecord.get("dictModelIdentity")
                                  or {}).get("sResolvedModel")
                if sResolvedModel:
                    dictResolvedModels[
                        dictTurnRecord["sParticipantId"]] = sResolvedModel
    return dictResolvedModels


def fsComposePlanMarkdown(dictCampaign, dictCandidatePlan,
                          sStalenessStatement=""):
    """Render the council's own candidate into the plan.md text.

    The acceptance artifact is composed from the SERVER-held record
    (remediation R3) — never from caller-supplied text — and carries
    everything a later reader needs to weigh the plan without the
    dashboard: the question, the sealed baseline snapshot identity the
    council reviewed, the participant roster, the synthesis result's
    summary, plan items, security risks, counterexamples attempted and
    open questions, plus the objection provenance the protocol
    recorded (researcher overrides stated as overrides, loudly).

    This is the ONLY composer — the plan.md read route serves these
    same bytes, so acceptance and the HTTP read can never disagree. An
    unaccepted candidate says so in the document's own text, because
    the file outlives the page it was downloaded from; the optional
    ``sStalenessStatement`` (route-computed, since staleness needs a
    live repository read this pure function must not make) rides the
    same principle.
    """
    dictResult = dictCandidatePlan.get("dictResult") or {}
    dictIdentity = dictCampaign.get("dictProjectIdentity") or {}
    listLines = ["# Council plan", ""]
    if dictCampaign.get("sState") not in ("planAccepted",
                                          "awaitingImplementation"):
        listLines.extend([
            "**DRAFT — this candidate was never accepted.** The council "
            "that produced it stopped at "
            f"`{dictCampaign.get('sState') or 'an unrecorded state'}`; "
            "read it as the council's working position, not a decision.",
            ""])
    if sStalenessStatement:
        listLines.extend(
            [f"**Baseline staleness.** {sStalenessStatement}", ""])
    listLines.extend(
        [f"**Question.** {dictCampaign.get('sQuestion', '')}", ""])
    sSnapshotIdentity = dictIdentity.get("sSnapshotIdentity", "")
    if sSnapshotIdentity:
        listLines.extend([
            "**Reviewed baseline.** Sealed snapshot "
            f"`{sSnapshotIdentity}` — the plan speaks about the "
            "repository as it stood at capture, not as it stands now.",
            ""])
    dictResolvedModels = _fdictCollectResolvedModelsByParticipant(
        dictCampaign)
    listParticipants = dictCampaign.get("listParticipants") or []
    if listParticipants:
        listLines.append("## Participants")
        for dictParticipant in listParticipants:
            sRoleSuffix = (f" — {dictParticipant['sRole']}"
                           if dictParticipant.get("sRole") else "")
            sResolvedModel = dictResolvedModels.get(
                dictParticipant.get("sParticipantId", ""))
            sResolvedSuffix = (
                f", resolved {sResolvedModel}" if sResolvedModel
                else ", resolved identity unrecorded")
            listLines.append(
                f"- {dictParticipant.get('sProvider', '')} "
                f"(requested model {dictParticipant.get('sRequestedModel', '')}"
                f"{sResolvedSuffix}){sRoleSuffix}")
        listLines.append("")
    listLines.extend([dictResult.get("sSummary", ""), ""])
    for sResultKey, sHeading, bNumbered in (
            ("listPlanItems", "Plan", True),
            ("listAssumptions", "Assumptions", False),
            ("listMathematicalClaims", "Mathematical claims", False),
            ("listArchitectureClaims", "Architectural surfaces affected",
             False),
            ("listRejectedAlternatives", "Rejected alternatives, and why",
             False),
            ("listVerificationRequirements",
             "Required verification, automated and manual", False),
            ("listStopConditions",
             "Stop conditions — halt and return to the council", False),
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
    listDecisions = dictCampaign.get("listResearcherDecisions") or []
    if listDecisions:
        listLines.append("## Researcher decisions during deliberation")
        for dictDecision in listDecisions:
            sDetailSuffix = (
                f": {dictDecision['sText']}"
                if dictDecision.get("sText") else "")
            listLines.append(
                f"- {dictDecision.get('sDecisionKind', '')}{sDetailSuffix}")
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
    sPlanMarkdown = fsComposePlanMarkdown(
        dictHolder["dictCampaign"], dictAccepted["dictCandidatePlan"] or {})
    sLocalPlanPath = agentCouncilStore.fsAcceptCampaignPlanLocally(
        dictStore, sCampaignId, sPlanMarkdown)
    # The artifact's content identity, sealed into the event stream at
    # acceptance: a later reader can prove the plan.md they hold is the
    # one the researcher accepted, byte for byte.
    sPlanSha256 = hashlib.sha256(sPlanMarkdown.encode("utf-8")).hexdigest()
    agentCouncilStore.fdictAppendCampaignEvent(
        dictStore, sCampaignId,
        {"sEventKind": "planArtifactSealed", "sTurnId": "",
         "sDetail": f"sha256:{sPlanSha256}"})
    return {"bAccepted": True, "sLocalPlanPath": sLocalPlanPath,
            "sPlanSha256": sPlanSha256,
            "dictCampaign": agentCouncilStore.fjsonGetCampaignRecord(
                dictStore, sCampaignId)}


def fiClassifyInterruptedCampaignsOnStartup(dictStore):
    """Classify UNPROVEN mid-turn campaigns as interrupted on startup.

    The original rule rewrote EVERY reloaded ``planning`` campaign,
    on the premise that planning-at-crash meant a turn with no
    terminal record. The durable phase-attempt record (continuation
    plan section 2) made that premise checkable — and often false: a
    hub killed BETWEEN phases leaves ``outcomeSettled`` or
    ``turnsSettled``, a boundary the researcher may explicitly resume
    from. Classifying those interrupted made the resume feature
    unreachable on any real restart, which the 2026-08-27 review
    caught: the tests planted records without running this
    lifecycle.

    So the classifier now consults the record: an attempt still
    ``running`` — launched runners nobody can account for (the
    labelled-runner reconcile settles whatever they left) — or no
    attempt record at all (a pre-feature checkpoint, never assumed
    settled) classifies as interrupted; a proven boundary is left in
    ``planning`` for the researcher's explicit resume. Nothing is
    EVER relaunched here — startup classifies, only the researcher
    resumes (amended §3).

    A campaign whose project is held by a LIVE PEER hub is left alone.
    The durable store is machine-wide, so this hub reloads a peer's
    in-flight campaign beside its own leftovers — and "planning with no
    runner I can see" is true of both a crash and a council another hub
    is running right now. Classifying the second one rewrites a working
    council's checkpoint to interrupted.
    """
    iClassified = 0
    for sCampaignId in list(dictStore["listInsertionOrder"]):
        dictCampaign = agentCouncilStore.fjsonGetCampaignRecord(
            dictStore, sCampaignId)
        if dictCampaign is None or dictCampaign["sState"] != (
                agentCouncilCampaign.S_STATE_PLANNING):
            continue
        if agentCouncilRegistry.fbCampaignBelongsToALivePeerHub(dictCampaign):
            continue
        if _fbCampaignStoppedAtAProvenBoundary(dictCampaign):
            continue
        agentCouncilCampaign.fnTransitionCampaignState(
            dictCampaign, agentCouncilCampaign.S_STATE_INTERRUPTED,
            "hubRestartedWhileATurnHadNoTerminalRecord")
        agentCouncilStore.fnCheckpointStoredCampaign(
            dictStore, sCampaignId, dictCampaign)
        iClassified += 1
    return iClassified


def _fbCampaignStoppedAtAProvenBoundary(dictCampaign):
    """Report whether the durable attempt record proves a clean stop.

    ``outcomeSettled`` at a walk-continuing outcome and
    ``turnsSettled`` (recoverable by deterministic settlement replay)
    are the boundaries resume admits (continuation plan 2.4); the
    startup classifier must leave exactly those in ``planning`` or
    the admission is unreachable. ``running`` and a missing record
    stay classifiable — turns nobody can account for.
    """
    listRounds = dictCampaign.get("listRounds") or []
    if not listRounds:
        return False
    dictAttempt = listRounds[-1].get("dictPhaseAttempt")
    if not dictAttempt:
        return False
    sAttemptState = dictAttempt.get("sAttemptState", "")
    if sAttemptState == "turnsSettled":
        return True
    return sAttemptState == "outcomeSettled" and dictAttempt.get(
        "sOutcome") in ("advancedToNextPhase", "roundResolved")


def _fnRequestRuntimeStopQuietly(dictRuntime):
    """Ask one runtime's live drive to stop; cancel an engine-less one."""
    if dictRuntime.get("engineCouncil") is not None:
        dictRuntime["engineCouncil"].fnRequestStopAfterCurrentTurn()
    taskDrive = dictRuntime.get("taskDrive")
    if taskDrive is not None and not taskDrive.done() and (
            dictRuntime.get("engineCouncil") is None):
        taskDrive.cancel()


def fbCloseResourceAdmission(dictControllerState, sResourceName):
    """Close council admission for a resource; report the close CLEAN.

    Called by the release authority under the container-mutation lock,
    in the same synchronous stretch as its re-check of live drives —
    close first, re-check after. On one event loop that ordering is
    what makes close-then-release atomic: a respond whose admission
    check passed before this close has already spawned its drive, so
    the re-check sees it and the release refuses; one that arrives
    after the close is refused at the command gate. Returns True when
    no drive is live after the close (the release may proceed), False
    when one is (the caller reopens and refuses).
    """
    from . import agentCouncilChat
    dictControllerState.setdefault(
        "setClosedResourceAdmissions", set()).add(sResourceName)
    # A chat message in flight is checked here too, and for the same
    # reason a drive is: it is paid provider work over the copied
    # login, started under the lease that is about to be dropped.
    return not (
        fbControllerHasLiveDriveForResource(
            dictControllerState, sResourceName)
        or agentCouncilChat.fbResourceHasChatMessageInFlight(
            dictControllerState, sResourceName))


def fnReopenResourceAdmission(dictControllerState, sResourceName):
    """Reopen council admission: a claim succeeded or a release aborted."""
    dictControllerState.setdefault(
        "setClosedResourceAdmissions", set()).discard(sResourceName)


def fbResourceAdmissionIsClosed(dictControllerState, sResourceName):
    """Report whether council admission is closed for one container.

    Public because the chat lane is not a controller command and must
    still honour the same gate: a conversation started after the lease
    was released would be paid provider work over a project this hub no
    longer owns.
    """
    return sResourceName in dictControllerState.get(
        "setClosedResourceAdmissions", set())


def _fnRefuseWhenResourceAdmissionClosed(dictControllerState, dictCampaign,
                                         sAction):
    """Refuse a turn-driving command for a resource whose lease is gone."""
    sResourceName = (dictCampaign.get("dictProjectIdentity")
                     or {}).get("sResourceName")
    if fbResourceAdmissionIsClosed(dictControllerState, sResourceName):
        raise CouncilCommandError(
            f"cannot {sAction}: the project lease was released; claim "
            "the project again and convene a fresh council")


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


async def fdictDrainControllerForResource(dictControllerState, sResourceName):
    """Settle every runtime whose campaign is bound to one resource.

    The release path's second belt: the release route refuses while a
    drive is LIVE (``fbControllerHasLiveDriveForResource``), so what
    this meets are PAUSED runtimes — a campaign waiting on the
    researcher, or a settled one nobody deleted. Those are not merely
    asked to stop (a stop request against a runtime with no future
    drive settles nothing): each is transitioned to interrupted,
    checkpointed, its egress boundary released, and its runtime
    dropped, because a campaign cannot continue against a project
    whose lease is gone — respond-after-release must answer "convene
    a fresh council", and no proxy or network may outlive the lease.
    A drive that slipped live between the busy check and this drain
    still gets the cooperative stop as the last belt. The
    ask-the-chairbot conversations bound to the resource are closed
    first, on the same terms and for the same reason — an idle
    conversation is not busy enough to refuse a release, but its runner
    and proxy must not outlive the lease either.

    Returns ``{bAllSettled, listUnsettledCampaignIds}``. ``bAllSettled``
    False means at least one campaign's egress boundary could not be
    PROVEN gone, and the caller must not complete the release: a
    lease dropped over an unproven proxy hands the container to the
    next session while a council network may still be dialling out.
    Retaining the runtime alone was not enough — it kept the retry
    state but told the release authority nothing, so the release
    proceeded anyway.
    """
    from . import agentCouncilChat
    listUnsettledCampaignIds = list(
        await agentCouncilChat.flistCloseChatSessionsForResource(
            dictControllerState, sResourceName))
    for sCampaignId, dictRuntime in list(
            dictControllerState["dictCampaignRuntime"].items()):
        dictIdentity = dictRuntime["dictCampaign"].get(
            "dictProjectIdentity") or {}
        if dictIdentity.get("sResourceName") != sResourceName:
            continue
        if fbCampaignDriveIsLive(dictControllerState, sCampaignId):
            _fnRequestRuntimeStopQuietly(dictRuntime)
            listUnsettledCampaignIds.append(sCampaignId)
            continue
        dictCampaign = dictRuntime["dictCampaign"]
        if dictCampaign["sState"] not in LIST_NO_FURTHER_TURN_STATES:
            agentCouncilCampaign.fnTransitionCampaignState(
                dictCampaign, agentCouncilCampaign.S_STATE_INTERRUPTED,
                "the project lease was released while the council "
                "was paused")
            agentCouncilStore.fnCheckpointStoredCampaign(
                dictRuntime["dictStore"], sCampaignId, dictCampaign)
        bAccessSettled = await asyncio.to_thread(
            _fbReleaseRunnerAccessResources, dictRuntime)
        if bAccessSettled:
            dictControllerState["dictCampaignRuntime"].pop(sCampaignId, None)
        else:
            # An unsettled teardown KEEPS the runtime — it is the
            # in-process retry state — AND reports itself, so the
            # release refuses rather than dropping the lease over a
            # boundary nobody proved gone.
            listUnsettledCampaignIds.append(sCampaignId)
    return {"bAllSettled": not listUnsettledCampaignIds,
            "listUnsettledCampaignIds": listUnsettledCampaignIds}


async def fdictDisposeCampaignRuntime(dictControllerState, sCampaignId):
    """Release a campaign's runtime and egress before its deletion.

    The delete route's controller half: durable storage removal must
    not strand the in-process runtime or its provisioned boundary.
    Refuses while the drive is live or launching (the route's registry
    check cannot see either), and REFUSES when the egress teardown
    cannot prove absence — the startup sweep composes leftover names
    from STORED campaign ids, so deleting the record while the daemon
    answers indeterminately would orphan the very network or proxy
    nobody proved gone. A quiet no-op for a campaign that never had a
    runtime this process lifetime.

    An open ask-the-chairbot conversation is closed here too, and an
    unproven close refuses the delete on exactly the terms an unproven
    egress teardown does: the conversation's runner and proxy are named
    from the campaign id, so removing the record would leave the
    startup sweep unable to compose the names of whatever survived.
    """
    from . import agentCouncilChat
    _fnRefuseWhileDriveIsLive(dictControllerState, sCampaignId, "delete")
    dictChatSettled = await agentCouncilChat.fdictCloseChatSession(
        dictControllerState, sCampaignId)
    if not dictChatSettled["bSettled"]:
        raise CouncilCommandError(
            "the chairbot conversation's resources could not be proven "
            f"gone ({dictChatSettled['sReason']}); retry the delete — "
            "removing the record now would orphan what may still exist")
    dictRuntime = dictControllerState["dictCampaignRuntime"].get(sCampaignId)
    if dictRuntime is None:
        return {"bDisposed": False}
    bAccessSettled = await asyncio.to_thread(
        _fbReleaseRunnerAccessResources, dictRuntime)
    if not bAccessSettled:
        raise CouncilCommandError(
            "the campaign's egress resources could not be proven gone "
            "(the daemon answered indeterminately); retry the delete — "
            "removing the record now would orphan what may still exist")
    dictControllerState["dictCampaignRuntime"].pop(sCampaignId, None)
    return {"bDisposed": True}


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
    machine. Every open ask-the-chairbot conversation is closed for the
    same reason and with the same honesty: its runner holds a copied
    login, so a hub that exits leaving one running has widened the
    credential window past its own lifetime.
    """
    from . import agentCouncilChat
    for sCampaignId in list(dictControllerState.get(
            agentCouncilChat.S_CHAT_SESSIONS_KEY, {})):
        await agentCouncilChat.fdictCloseChatSession(
            dictControllerState, sCampaignId)
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
        await asyncio.to_thread(_fbReleaseRunnerAccessResources, dictRuntime)
