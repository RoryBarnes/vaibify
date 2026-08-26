"""Agent Council HTTP routes: campaign lifecycle, persistence, event polling.

Phase 3 of the Agent Council (design/agentCouncil.md sections 7, 10, 11,
16, 21). These routes convene, observe, answer, stop, accept and delete
planning councils. They own no execution: the paid runner turn is driven
by the council registry (a separate authority, section 9.3), and the
campaign record is persisted to host application-data OUTSIDE the
repository (section 7.3). Every route here is therefore governed by those
app-owned authorities, never the commit carrier — which is exactly what
the ``separate-authority`` carrier declaration records.

THREE THINGS THIS MODULE ENFORCES, each an invariant named in section 21:

- **Container-only.** A council needs a container to build a runner from,
  so every route refuses a host project through
  ``fnRefuseContainerOnlyForHostProject`` (409 with the ``host-mode``
  marker), keyed on ``fbIsHostProject`` and NEVER ``fbIsProject`` — a
  promoted host Project has no container. The ordering is the contract:
  the container lease is enforced first by ``ContainerAwareRoute`` before
  the handler runs, then the handler branches on the mode, then requires
  the daemon. ``terminalRoutes.py`` is the worked example.
- **Browser-only.** Starting paid work, answering a council, stopping,
  accepting a plan and deleting a campaign are human decisions, and the
  reads expose researcher prompts and private deliberation, so every
  route rejects the in-container agent token lane explicitly. The
  mutating routes are additionally listed in
  ``SET_INTENTIONALLY_EXCLUDED_PATHS`` so the fail-closed agent gate
  refuses them by catalog too.
- **Server-minted identity.** Campaign ids, participant ids, event
  sequence numbers and storage paths are minted by the server; the
  request models carry only the researcher's question, participants and
  answers, with explicit length and enumeration limits.
"""

__all__ = ["fnRegisterAll"]

import asyncio
import posixpath

from fastapi import HTTPException, Request
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field, field_validator

from vaibify.docker import dockerConnection

from .. import agentCouncilCampaign
from .. import agentCouncilController
from .. import agentCouncilDockerGateway
from .. import agentCouncilRegistry
from .. import agentCouncilResolution
from .. import agentCouncilStore
from ..councilRouteGuards import (
    I_MAX_RESPONSE_LENGTH,
    S_COUNCIL_CAPABILITY,
    fdictCampaignStore,
    flistTrackedDirectoryNames,
    fsResolveDominantRepositoryPath,
    fdictControllerState,
    fdictCouncilRegistry,
    ffnBuildCredentialStager,
    ffnBuildImageResolver,
    fgenericSubmitMapped,
    fjsonRequireCampaign,
    fnRefuseRunnerBackendUnlessEnabled,
    fnRefuseStartWithoutAProjectLogin,
    ftResolveCouncilPrincipal,
)
from ..pipelineServer import (
    WORKSPACE_ROOT,
    fdictRequireWorkflow,
    fsContainerNameForId,
)
from ..routeContext import (
    fnRefuseContainerOnlyForHostProject,
    fnRejectAgentTokenLane,
    S_UNAVAILABLE_IN_HOST_MODE,
    S_UNAVAILABLE_UNTIL_CREDENTIAL_EVIDENCE,
    S_UNAVAILABLE_SNAPSHOT_TOO_LARGE,
    S_UNAVAILABLE_NO_DOMINANT_DIRECTORY,
)
from ..routeScope import (
    ffnDeclareCarrierMode,
    S_CARRIER_MODE_B_LOCK_HELD,
    S_CARRIER_SEPARATE_AUTHORITY,
)

# The provider vocabulary a start request may name. Claude ONLY
# (remediation R7/R9): Codex has no reviewed adapter — its feasibility
# work is a separate follow-up — and advertising an adapter-less
# provider convenes a campaign that can never run. An unrecognised
# provider is refused at validation.
SET_ALLOWED_PROVIDERS = frozenset({"claude"})

I_MAX_QUESTION_LENGTH = 20000
I_MAX_MODEL_LENGTH = 200
I_MAX_ROLE_LENGTH = 2000
I_MAX_PARTICIPANTS = 8
I_MIN_PARTICIPANTS = 2


class CouncilParticipantRequest(BaseModel):
    """One participant's provider, requested model and optional role."""

    sProvider: str = Field(min_length=1, max_length=64)
    sRequestedModel: str = Field(min_length=1, max_length=I_MAX_MODEL_LENGTH)
    sRole: str = Field(default="", max_length=I_MAX_ROLE_LENGTH)

    @field_validator("sProvider")
    @classmethod
    def fsValidateProvider(cls, sProvider):
        """Refuse a provider outside the reviewed capability vocabulary."""
        if sProvider not in SET_ALLOWED_PROVIDERS:
            raise ValueError(
                f"provider '{sProvider}' has no reviewed council adapter")
        return sProvider


class CouncilStartRequest(BaseModel):
    """Body for convening a planning council (section 6.3).

    ``dictSettings`` carries the convene form's council settings; the
    campaign module's bounded settings validator is the authority (an
    unknown key or an out-of-range value answers 400), so the route
    adds no second vocabulary.
    """

    sQuestion: str = Field(min_length=1, max_length=I_MAX_QUESTION_LENGTH)
    # The researcher's own name for this council. Blank derives one
    # from the question; a collision with an existing name gains a
    # numeric suffix as best-effort disambiguation (the store does not
    # enforce uniqueness — sCampaignId stays the only identity).
    sCampaignName: str = Field(
        default="",
        max_length=agentCouncilCampaign.I_MAX_CAMPAIGN_NAME_LENGTH)
    listParticipants: list[CouncilParticipantRequest] = Field(
        min_length=I_MIN_PARTICIPANTS, max_length=I_MAX_PARTICIPANTS)
    iChairbotIndex: int = Field(default=0, ge=0, lt=I_MAX_PARTICIPANTS)
    dictSettings: dict = Field(default_factory=dict)
    # Which tracked directory this council is about, when the project
    # tracks several and no workflow pins one. A BASENAME, validated
    # against the tracked set server-side; never a path from a client.
    sProjectDirectory: str = Field(default="", max_length=255)
    # Oversized files the researcher reviewed and chose to leave out of
    # the snapshot. Bounded by the number the pre-flight can name, and
    # honoured by the capture ONLY for a member that would otherwise
    # have refused, so this list can never quietly curate a repository.
    listExcludedPaths: list[str] = Field(
        default_factory=list,
        max_length=dockerConnection.I_REPOSITORY_WEIGHT_LARGEST_FILES)


class CouncilDecisionAnswer(BaseModel):
    """One answer to one decision point of a blocking-question gate."""

    sDecisionId: str = Field(min_length=1, max_length=128)
    listQuestionIds: list[str] = Field(default_factory=list, max_length=64)
    sAnswerText: str = Field(min_length=1, max_length=I_MAX_RESPONSE_LENGTH)


class CouncilRespondRequest(BaseModel):
    """Body for answering a council's blocking question (section 6.5).

    ``sResponseText`` remains the whole answer as prose and is what a
    flat gate sends. ``listDecisionAnswers`` is the per-decision form:
    when present the SERVER composes the prose from it, so the two can
    never disagree about what the researcher said.
    """

    sResponseText: str = Field(min_length=1, max_length=I_MAX_RESPONSE_LENGTH)
    listDecisionAnswers: list[CouncilDecisionAnswer] = Field(
        default_factory=list, max_length=128)


class CouncilGrantRoundRequest(BaseModel):
    """Body for the exhausted-round exit that grants a fresh budget."""

    iGrantedRounds: int = Field(default=1, ge=1, le=10)


class CouncilObjectionDisposition(BaseModel):
    """One objection's researcher disposition: resolve or override."""

    sAction: str = Field(pattern="^(resolve|override)$")
    sText: str = Field(default="", max_length=I_MAX_RESPONSE_LENGTH)


class CouncilResolveObjectionsRequest(BaseModel):
    """Body for the exhausted-round exit that disposes every objection."""

    dictDispositionByObjectionId: dict[str, CouncilObjectionDisposition] = (
        Field(default_factory=dict))


class CouncilRejectRequest(BaseModel):
    """Body for rejecting/archiving the candidate."""

    sReasonText: str = Field(default="", max_length=I_MAX_RESPONSE_LENGTH)


class CouncilResumeRequest(BaseModel):
    """Body for resuming a crashed deliberation.

    ``bClearStopRequest`` is the researcher's explicit answer to the
    one choice resume surfaces (continuation plan 4.2.5): a stop
    requested before the crash was a decision about THAT run, and a
    resumed record that kept the flag would archive itself instantly.
    The clear is recorded as a researcher decision, never silent.
    """

    bClearStopRequest: bool = False


# Acceptance takes NO body (remediation R3): what lands in plan.md is
# the council's own server-held candidate, accepted through the
# engine's planReady gate — caller-supplied plan text was the accept
# bypass the review flagged, and the review gate is the researcher
# READING the candidate, not retyping it.


def _fdictBuildEvent(sEventKind, sTurnId="", sDetail=""):
    """Build a display event; the store stamps its sequence number.

    ``sEventKind`` because the ENGINE'S shape is the authority
    (remediation R6): the ring holds engine events and route events in
    one stream, and the frontend reads a single field name.
    """
    return {"sEventKind": sEventKind, "sTurnId": sTurnId,
            "sDetail": sDetail}


def _fbCampaignHasLiveWork(dictRegistry, sCampaignId):
    """Report whether a campaign has any live turn, reservation or request.

    Reads the registry dict directly, the same way the idle watchdog
    reads ``dictContainerOwners`` — the registry is a plain dict, and a
    per-campaign view of it is a read, not a second authority.
    """
    if any(sCid == sCampaignId
           for sCid, _ in dictRegistry["setTurnsInFlight"]):
        return True
    if any(dictReservation["sCampaignId"] == sCampaignId
           and dictReservation["sStatus"]
           in agentCouncilRegistry.SET_LIVE_RESERVATION_STATUSES
           for dictReservation
           in dictRegistry["dictReservationsById"].values()):
        return True
    return any(
        dictRequest["sCampaignId"] == sCampaignId
        and dictRequest["sStatus"] == agentCouncilRegistry.S_API_REQUEST_ACTIVE
        for dictRequest in dictRegistry["dictApiRequestsById"].values())


def _fnRefuseLaunchWhileCampaignBusy(dictControllerState, dictRegistry,
                                     sCampaignId):
    """Refuse a deliberation launch while the campaign has live work."""
    if agentCouncilController.fbCampaignDriveIsLive(
            dictControllerState, sCampaignId) or _fbCampaignHasLiveWork(
            dictRegistry, sCampaignId):
        raise HTTPException(
            409, "a turn is already in flight for this council")


def _ffnBuildSnapshotCapture(dictCtx, requestHttp, sContainerId,
                             sProjectRepoPath, sCampaignId, sName,
                             listExcludedPaths=None):
    """Build the closure that captures the snapshot under the project lock.

    The bounded project lock (design section 9.2) is the commit
    carrier's mode-(b) drain: the capture window holds the container's
    mutation lock, so the snapshot never races a run or a sync the
    researcher just launched — and the capture's git identity reads run
    ADMITTED in the worker thread rather than tripping the enforced
    lane's refusal (the live controller lane caught exactly that). A
    coherence refusal is an EXPECTED answer decided before anything
    was half-written, so it is carried back as a value and re-raised
    outside the carrier — never through the failed-worker settlement
    that would quarantine a working container.
    """
    from .. import agentCouncilCapacity
    from .. import agentCouncilContext
    from .. import commitCarrier

    # Resolved ONCE, here, so the capture enforces exactly the bounds
    # the pre-flight advertised. Resolving it again inside the worker
    # would let a daemon restart between the two make the button's
    # promise and the capture's refusal disagree.
    dictBounds = agentCouncilCapacity.fdictResolveCouncilCapacity(
        dictCtx.get("docker"))

    async def _fdictCaptureUnderProjectLock():
        dictLaneTuple = commitCarrier.fdictBuildLaneTupleFromRequest(
            requestHttp.app.state, sContainerId, requestHttp)
        if dictLaneTuple is None:
            raise HTTPException(
                403, "the snapshot capture could not be bound to the "
                "container's owner record")

        def _fdictCaptureWorker(supervisor):
            try:
                return {
                    "bRefused": False,
                    "dictManifest":
                        agentCouncilContext.fdictCaptureProjectContextSnapshot(
                            dictCtx["docker"], sContainerId,
                            sProjectRepoPath, sCampaignId,
                            fdictCampaignStore(requestHttp)[
                                "sDurableStoreRoot"],
                            dictBounds=dictBounds,
                            listExcludedPaths=listExcludedPaths),
                }
            except agentCouncilContext.SnapshotRefusedError as error:
                return {"bRefused": True, "sRefusalReason": str(error)}

        dictOutcome = await commitCarrier.fdictRunLockHeldMutation(
            requestHttp.app.state, sName, sContainerId, dictLaneTuple,
            "helper", "council-snapshot-capture", _fdictCaptureWorker)
        dictCaptured = dictOutcome["result"]
        if dictCaptured["bRefused"]:
            raise HTTPException(409, dictCaptured["sRefusalReason"])
        return dictCaptured["dictManifest"]

    return _fdictCaptureUnderProjectLock


def _fnDrainCampaignWork(dictRegistry, sCampaignId):
    """Retire every live turn and interrupt every active request for a campaign.

    The human-pause guarantee (section 15.3): a stop leaves no live turn
    or active API request behind. Runner reservations are destroyed
    through the registry's shutdown drain and the integration stop path;
    Phase 3 creates no runner, so retiring the turn-in-flight record is
    what a stop must undo here.
    """
    for tTurnKey in list(dictRegistry["setTurnsInFlight"]):
        if tTurnKey[0] == sCampaignId:
            agentCouncilRegistry.fnRetireTurnInFlight(
                dictRegistry, sCampaignId, tTurnKey[1])
    for dictRequest in dictRegistry["dictApiRequestsById"].values():
        if (dictRequest["sCampaignId"] == sCampaignId
                and dictRequest["sStatus"]
                == agentCouncilRegistry.S_API_REQUEST_ACTIVE):
            agentCouncilRegistry.fnSettleApiRequest(
                dictRegistry, dictRequest["sRequestId"],
                agentCouncilRegistry.S_API_REQUEST_INTERRUPTED)


def _fsResolveChairbotId(listParticipants, iChairbotIndex):
    """Return the chosen chairbot's minted id, or refuse an out-of-range index."""
    if iChairbotIndex >= len(listParticipants):
        raise HTTPException(
            400, "the chairbot index is outside the participant list")
    return listParticipants[iChairbotIndex]["sParticipantId"]


def _fdictCreateCampaignFromRequest(request, dictProjectIdentity,
                                    saExistingNames=None):
    """Build a draft campaign record from a validated start request.

    ``saExistingNames`` are the names already taken in this project, so
    the composer can guarantee the listing shows no two rows a person
    cannot tell apart. Passed in rather than read here: the store is the
    route's to reach, and this stays a pure record builder.
    """
    listParticipants = [
        agentCouncilCampaign.fdictCreateParticipant(
            requestParticipant.sProvider,
            requestParticipant.sRequestedModel,
            requestParticipant.sRole,
        )
        for requestParticipant in request.listParticipants
    ]
    sChairbotId = _fsResolveChairbotId(
        listParticipants, request.iChairbotIndex)
    try:
        return agentCouncilCampaign.fdictCreateCampaign(
            request.sQuestion, listParticipants,
            dictSettings=request.dictSettings or None,
            sChairbotParticipantId=sChairbotId,
            dictProjectIdentity=dictProjectIdentity,
            sCampaignName=agentCouncilCampaign.fsComposeUniqueCampaignName(
                request.sCampaignName, request.sQuestion, saExistingNames),
        )
    except agentCouncilCampaign.CouncilConfigurationError as error:
        raise HTTPException(400, str(error))


def _fnRegisterCapabilities(app, dictCtx):
    """Register GET /api/agent-councils/{sContainerId}/capabilities."""

    @app.get("/api/agent-councils/{sContainerId}/capabilities")
    @ffnDeclareCarrierMode(S_CARRIER_SEPARATE_AUTHORITY)
    async def fdictReportCapabilities(
        sContainerId: str, requestHttp: Request,
    ):
        fnRejectAgentTokenLane(requestHttp)
        sName = fsContainerNameForId(dictCtx.get("docker"), sContainerId)
        from vaibify.config.registryManager import fbIsHostProject
        if fbIsHostProject(sName):
            return _fdictHostModeCapabilities()
        dictCtx["require"](sContainerId)
        dictCapabilities = await _fdictContainerCapabilities(
            dictCtx, sContainerId)
        # The snapshot pre-flight runs LAST and only when everything
        # else already permits a council: it costs a metadata walk, and
        # there is no sense weighing a repository for a project whose
        # runner backend is disabled anyway.
        if dictCapabilities["bAvailable"]:
            _fnApplySnapshotFeasibility(
                dictCtx, requestHttp, sContainerId, dictCapabilities)
        return dictCapabilities


def _fnApplySnapshotFeasibility(dictCtx, requestHttp, sContainerId,
                                dictCapabilities):
    """Downgrade capabilities when the repo could never be snapshotted.

    Reported through the SAME bAvailable/sReason pair every other
    refusal uses, so the toolbar explains it with the machinery it
    already has rather than growing a second unavailable-shaped
    concept. A probe failure is NOT a refusal — the authoritative
    bounds still run at capture — so an unreadable repo leaves the
    capability as it was rather than blocking a council over a probe.
    """
    from .. import agentCouncilContext
    # Resolving WHICH repository is not part of the probe, so its
    # refusals are not swallowed with the probe's. "I cannot tell which
    # directory this project is about" must reach the toolbar — caught
    # here, it left the button enabled and the researcher discovered it
    # at convene, which is the failure this whole pre-flight exists to
    # prevent.
    sProjectRepoPath = (dictCtx.get("workflows") or {}).get(
        sContainerId, {}).get("sProjectRepoPath", "")
    if not sProjectRepoPath:
        # Several tracked directories is a QUESTION, not a refusal: the
        # candidates are published so the convene form can ask. The
        # pre-flight is skipped in that case because there is no single
        # repository to weigh yet.
        listCandidates = sorted(
            flistTrackedDirectoryNames(dictCtx, sContainerId))
        if len(listCandidates) > 1:
            dictCapabilities["listCandidateDirectories"] = listCandidates
            return
        try:
            sProjectRepoPath = fsResolveDominantRepositoryPath(
                dictCtx, sContainerId)
        except HTTPException as errorRefusal:
            dictCapabilities["bAvailable"] = False
            dictCapabilities["sUnavailableIn"] = (
                S_UNAVAILABLE_NO_DOMINANT_DIRECTORY)
            dictCapabilities["sReason"] = str(errorRefusal.detail)
            return
    try:
        dictFeasibility = agentCouncilContext.fdictAssessSnapshotFeasibility(
            dictCtx["docker"], sContainerId, sProjectRepoPath)
    except (OSError, ValueError, KeyError):
        return
    dictCapabilities["dictSnapshotFeasibility"] = dictFeasibility
    # A repository whose ONLY problem is named oversized files is not
    # unavailable — it is a choice the researcher has not made yet, and
    # blocking the button would hide the modal that offers the choice.
    # The count and total bounds stay hard: no per-file decision helps
    # a repository that is simply the wrong shape for a council.
    if not dictFeasibility["bFits"] and not dictFeasibility[
            "bResolvableByExcludingFiles"]:
        dictCapabilities["bAvailable"] = False
        dictCapabilities["sUnavailableIn"] = S_UNAVAILABLE_SNAPSHOT_TOO_LARGE
        dictCapabilities["sReason"] = dictFeasibility["sReason"]


def _fnRegisterSnapshotFeasibility(app, dictCtx):
    """Register GET /api/agent-councils/{sContainerId}/snapshot-feasibility."""

    @app.get("/api/agent-councils/{sContainerId}/snapshot-feasibility")
    @ffnDeclareCarrierMode(S_CARRIER_SEPARATE_AUTHORITY)
    async def fdictReportSnapshotFeasibility(
        sContainerId: str, requestHttp: Request, sProjectDirectory: str = "",
    ):
        """Weigh ONE candidate directory, on demand.

        Separate from the capabilities poll on purpose. A toolkit
        container tracks many repositories — one live project tracks
        nine — and weighing all of them on every poll would spend a
        metadata walk per repository per few seconds to answer a
        question nobody asked. The convene form asks this once per
        candidate when it opens, so the cost is paid where the
        researcher is actually choosing.
        """
        fnRejectAgentTokenLane(requestHttp)
        sName = fsContainerNameForId(dictCtx.get("docker"), sContainerId)
        from vaibify.config.registryManager import fbIsHostProject
        if fbIsHostProject(sName):
            raise HTTPException(
                409, "host projects have no container to snapshot")
        dictCtx["require"](sContainerId)
        from .. import agentCouncilContext
        sProjectRepoPath = fsResolveDominantRepositoryPath(
            dictCtx, sContainerId, sProjectDirectory)
        try:
            return agentCouncilContext.fdictAssessSnapshotFeasibility(
                dictCtx["docker"], sContainerId, sProjectRepoPath)
        except (OSError, ValueError, KeyError) as errorProbe:
            # A probe failure is NOT a refusal — the authoritative
            # bounds still run at capture — so it answers "unknown"
            # rather than blocking a council over an unreadable walk.
            raise HTTPException(
                503, "this directory could not be weighed "
                f"({errorProbe.__class__.__name__}); the snapshot bounds "
                "are still enforced when you convene") from errorProbe


def _fdictHostModeCapabilities():
    """Report the council as unavailable in host mode, with the marker.

    Reports rather than refuses (section 10.1) so the toolbar can explain
    itself instead of failing on click; the machine-readable marker is
    the same one the container-only refusal carries.
    """
    return {
        "bAvailable": False,
        "sUnavailableIn": S_UNAVAILABLE_IN_HOST_MODE,
        "sReason": (
            f"{S_COUNCIL_CAPABILITY} applies only to containerized "
            "projects. This project runs directly on this machine and "
            "has no container to build a runner from."
        ),
        "listProviders": [],
    }


async def _fdictContainerCapabilities(dictCtx, sContainerId):
    """Report the real runner-backend availability (remediation R7/R10).

    ``bAvailable`` is the credential-enablement evaluation AGAINST the
    project's resolved immutable image identity — the same comparison
    start makes — never an unconditional True and never an image-blind
    optimism that start would immediately contradict. A project whose
    image cannot be resolved reports disabled with that reason. The
    reason travels with the answer so the toolbar can explain itself
    instead of failing on click. Only Claude is advertised — no
    adapter-less provider appears at all.
    """
    from .. import agentCouncilCredentialGate
    try:
        sImageIdentity = await ffnBuildImageResolver(
            dictCtx, sContainerId)()
        dictEnablement = (
            agentCouncilCredentialGate.fdictEvaluateCredentialEnablement(
                "claude", sImageIdentity))
    except HTTPException as error:
        dictEnablement = {
            "bEnabled": False,
            "sReason": str(error.detail),
            "dictRecord": None,
        }
    # The adapter's own capability contract carries the model
    # discovery the picker reads (design section 8.2). For the
    # SUBSCRIPTION runner backend there is no API key to enumerate
    # with and enumeration would otherwise cost a paid turn, so the
    # payload is the CLI-accepted alias set, carried with
    # ``bVerified`` False and its source named — a labelled
    # un-verified list, never a discovered one. The design amendment
    # recording that is in section 8.2.
    from .. import agentCouncilProviders
    dictContract = agentCouncilProviders.fdictClaudeCapabilityContract(
        bRunnerBackendEnabled=dictEnablement["bEnabled"])
    return {
        "bAvailable": dictEnablement["bEnabled"],
        # A shut gate the researcher can OPEN, marked as such. The two
        # mode markers mean "never here"; this one means "here is the
        # thing to go and do", and only that third case earns
        # instructions in the toolbar's explanation.
        "sUnavailableIn": (
            "" if dictEnablement["bEnabled"]
            else S_UNAVAILABLE_UNTIL_CREDENTIAL_EVIDENCE),
        "sReason": dictEnablement["sReason"],
        "listProviders": [
            {"sProvider": "claude", "sBackend": "runner",
             "bAvailable": dictEnablement["bEnabled"],
             "sReason": dictEnablement["sReason"],
             "dictModelDiscovery": dictContract["dictModelDiscovery"]},
        ],
    }


def _fnRegisterListCouncils(app, dictCtx):
    """Register GET /api/agent-councils/{sContainerId}."""

    @app.get("/api/agent-councils/{sContainerId}")
    @ffnDeclareCarrierMode(S_CARRIER_SEPARATE_AUTHORITY)
    async def fdictListCouncils(sContainerId: str, requestHttp: Request,
                                sProjectDirectory: str = ""):
        sName, sProjectRepoPath = ftResolveCouncilPrincipal(
            dictCtx, requestHttp, sContainerId, sProjectDirectory)
        return {
            "listCampaigns": agentCouncilStore.flistSummariseCampaigns(
                fdictCampaignStore(requestHttp),
                fbSelectCampaign=lambda dictCampaign:
                    agentCouncilCampaign.fbCampaignMatchesPrincipal(
                        dictCampaign, sName, sProjectRepoPath)),
        }


def _fnRegisterGetCouncil(app, dictCtx):
    """Register GET /api/agent-councils/{sContainerId}/{sCampaignId}."""

    @app.get("/api/agent-councils/{sContainerId}/{sCampaignId}")
    @ffnDeclareCarrierMode(S_CARRIER_SEPARATE_AUTHORITY)
    async def fdictGetCouncil(
        sContainerId: str, sCampaignId: str, requestHttp: Request,
        sProjectDirectory: str = "",
    ):
        sName, sProjectRepoPath = ftResolveCouncilPrincipal(
            dictCtx, requestHttp, sContainerId, sProjectDirectory)
        dictStore = fdictCampaignStore(requestHttp)
        jsonCampaign = fjsonRequireCampaign(
            dictStore, sCampaignId, sName, sProjectRepoPath)
        jsonCampaign.update(await asyncio.to_thread(
            _fdictComputeBaselineStaleness, dictCtx, dictStore,
            sContainerId, sProjectRepoPath, sCampaignId))
        # The "runner may exist" surface (remediation R4): quarantined
        # reservations are read through the gateway's registry-only
        # view — no daemon is consulted on this read path.
        dictGatewayView = (
            agentCouncilDockerGateway.fdictCreateCouncilDockerGateway(
                None, fdictCouncilRegistry(requestHttp)))
        listQuarantined = (
            agentCouncilDockerGateway.flistDescribeQuarantinedReservations(
                dictGatewayView, sCampaignId))
        jsonCampaign["listQuarantinedRunners"] = listQuarantined
        # Derived on READ, never stored on the gate. The grouping is a
        # pure function of the questions, the plan and the roster, so
        # recomputing it cannot go stale — and it applies to a campaign
        # already sitting at a gate, which a value written at gate-open
        # would have missed.
        jsonCampaign["listGateDecisions"] = (
            agentCouncilResolution.flistGroupGateQuestionsIntoDecisions(
                jsonCampaign))
        jsonCampaign["listHeldQuestions"] = (
            agentCouncilResolution.flistDescribeHeldQuestions(jsonCampaign))
        # What the record supports (the stopping point) and what is
        # actually live in this process — the pair the panel needs to
        # tell "deliberating" from "crashed and resumable" without
        # guessing. The listing shows the first; only the hub knows
        # the second.
        jsonCampaign["dictStoppingPoint"] = (
            agentCouncilResolution.fdictDescribeStoppingPoint(jsonCampaign))
        dictControllerState = fdictControllerState(requestHttp)
        jsonCampaign["bDeliberationLive"] = (
            sCampaignId in dictControllerState["dictCampaignRuntime"]
            or agentCouncilController.fbCampaignDriveIsLive(
                dictControllerState, sCampaignId))
        # Overwrites the engine's raw record deliberately: the stored key
        # says what the engine believed, this says what the reader is
        # entitled to believe, and only the second should reach a screen.
        jsonCampaign["dictPhaseInFlight"] = (
            agentCouncilResolution.fdictDescribeActivePhase(jsonCampaign))
        return {
            "dictCampaign": jsonCampaign,
            "listQuarantinedRunners": listQuarantined,
        }


def _fdictComputeBaselineStaleness(dictCtx, dictStore, sContainerId,
                                   sProjectRepoPath, sCampaignId):
    """Compare the project's CURRENT identity to the sealed snapshot's.

    The real stale-baseline producer (remediation R12): the snapshot
    manifest recorded the typed-read head sha and porcelain digest at
    capture (the SAME ``gitWorktreeIdentities`` observation the
    coherence check took), and this re-runs that declared read against
    the live repository. A typed read is the only container touch this
    poll-path comparison may make — every council route is a DECLARED
    carrier lane, so a general git exec here would (rightly) refuse at
    the mutation funnel. Three honest answers — fresh (False), stale
    (True, with what moved), and UNKNOWN (None) when there is no
    manifest, the manifest predates the baseline fields, or the
    repository cannot be read; unknown is never dressed up as fresh.
    """
    import json as moduleJson
    import os
    sManifestPath = os.path.join(
        dictStore["sDurableStoreRoot"], sCampaignId, "snapshot",
        "manifest.json")
    if not os.path.isfile(sManifestPath):
        return {"bPlanningBaselineStale": None,
                "sPlanningBaselineSummary": "no sealed snapshot manifest"}
    try:
        with open(sManifestPath, encoding="utf-8") as fileManifest:
            jsonManifest = moduleJson.load(fileManifest)
        if not jsonManifest.get("sBaselineHeadSha") and not (
                jsonManifest.get("sBaselinePorcelainDigest")):
            return {"bPlanningBaselineStale": None,
                    "sPlanningBaselineSummary":
                        "the sealed manifest predates the baseline "
                        "identity fields"}
        dictObservation = dictCtx["docker"].fdictFetchWorktreeIdentities(
            sContainerId, sProjectRepoPath)
        if not dictObservation.get("bSuccess"):
            raise RuntimeError(
                dictObservation.get("sReason") or "observation failed")
    except Exception as error:
        return {"bPlanningBaselineStale": None,
                "sPlanningBaselineSummary":
                    "the baseline comparison could not run "
                    f"({type(error).__name__})"}
    bCommitMoved = (dictObservation.get("sHeadSha", "")
                    != jsonManifest.get("sBaselineHeadSha"))
    bTreeMoved = (dictObservation.get("sPorcelainDigest", "")
                  != jsonManifest.get("sBaselinePorcelainDigest"))
    # The porcelain digest never hashes worktree bytes, so a dirty
    # file whose CONTENT changed again moves only the per-path
    # identity digest; compared whenever the manifest recorded one.
    from .. import agentCouncilContext
    sBaselineContentDigest = jsonManifest.get(
        "sBaselinePathIdentitiesDigest")
    bContentMoved = bool(sBaselineContentDigest) and (
        agentCouncilContext.fsComputePathIdentitiesDigest(
            dictObservation.get("dictPathIdentities") or {})
        != sBaselineContentDigest)
    if not bCommitMoved and not bTreeMoved and not bContentMoved:
        return {"bPlanningBaselineStale": False,
                "sPlanningBaselineSummary": ""}
    listMoved = []
    if bCommitMoved:
        listMoved.append(
            "the commit moved from "
            f"{jsonManifest.get('sBaselineHeadSha')} to "
            f"{dictObservation.get('sHeadSha') or '(none)'}")
    if bTreeMoved:
        listMoved.append("the working tree changed")
    if bContentMoved and not bTreeMoved:
        listMoved.append("file contents changed")
    return {"bPlanningBaselineStale": True,
            "sPlanningBaselineSummary": "; ".join(listMoved)}


def _fnRegisterPollEvents(app, dictCtx):
    """Register GET /api/agent-councils/{sContainerId}/{sCampaignId}/events."""

    @app.get("/api/agent-councils/{sContainerId}/{sCampaignId}/events")
    @ffnDeclareCarrierMode(S_CARRIER_SEPARATE_AUTHORITY)
    async def fdictPollEvents(
        sContainerId: str, sCampaignId: str, requestHttp: Request,
        iAfter: int = 0, sProjectDirectory: str = "",
    ):
        sName, sProjectRepoPath = ftResolveCouncilPrincipal(
            dictCtx, requestHttp, sContainerId, sProjectDirectory)
        if iAfter < 0:
            raise HTTPException(400, "iAfter must not be negative")
        fjsonRequireCampaign(
            fdictCampaignStore(requestHttp), sCampaignId, sName,
            sProjectRepoPath)
        dictEvents = agentCouncilStore.fdictCollectCampaignEvents(
            fdictCampaignStore(requestHttp), sCampaignId, iAfter)
        if dictEvents is None:
            raise HTTPException(404, f"no council campaign '{sCampaignId}'")
        return dictEvents


def _fnRegisterStartCouncil(app, dictCtx):
    """Register POST /api/agent-councils/{sContainerId}/start."""

    @app.post("/api/agent-councils/{sContainerId}/start")
    @ffnDeclareCarrierMode(S_CARRIER_SEPARATE_AUTHORITY,
                           S_CARRIER_MODE_B_LOCK_HELD)
    async def fdictStartCouncil(
        sContainerId: str, request: CouncilStartRequest,
        requestHttp: Request,
    ):
        # The ONLY route that passes a chosen directory: convening is
        # where the campaign's repo is decided. Every other council
        # route resolves the same principal to MATCH an existing
        # campaign, and must not be able to re-point one.
        sName, sProjectRepoPath = ftResolveCouncilPrincipal(
            dictCtx, requestHttp, sContainerId, request.sProjectDirectory)
        dictStore = fdictCampaignStore(requestHttp)
        dictRegistry = fdictCouncilRegistry(requestHttp)
        dictCampaign = _fdictCreateCampaignFromRequest(
            request,
            {
                **agentCouncilCampaign.DICT_EMPTY_PROJECT_IDENTITY,
                "sResourceName": sName,
                "sProjectRepoPath": sProjectRepoPath,
            },
            saExistingNames=[
                dictSummary.get("sCampaignName", "")
                for dictSummary
                in agentCouncilStore.flistSummariseCampaigns(
                    dictStore,
                    fbSelectCampaign=lambda dictOther:
                        agentCouncilCampaign.fbCampaignMatchesPrincipal(
                            dictOther, sName, sProjectRepoPath))])

        dictControllerState = fdictControllerState(requestHttp)
        sCampaignId = dictCampaign["sCampaignId"]

        async def _fdictExecuteStart():
            # The image resolves BEFORE the credential gate so the
            # evidence record's image pin is always compared, and both
            # run before the campaign registers — a refusal here leaves
            # no record at all (the launch itself is transactional
            # about the record it registers below).
            sImageReference = await ffnBuildImageResolver(
                dictCtx, sContainerId)()
            fnRefuseRunnerBackendUnlessEnabled(sImageReference)
            await asyncio.to_thread(
                fnRefuseStartWithoutAProjectLogin, dictCtx, sContainerId)
            _fnRefuseLaunchWhileCampaignBusy(
                dictControllerState, dictRegistry, sCampaignId)
            agentCouncilCampaign.fnTransitionCampaignState(
                dictCampaign, agentCouncilCampaign.S_STATE_PLANNING,
                "researcher convened the council")
            agentCouncilStore.fdictRegisterStartedCampaign(
                dictStore, dictCampaign)
            dictLaunched = (
                await agentCouncilController.fdictLaunchCampaignDeliberation(
                    dictControllerState, dictStore, dictRegistry,
                    sCampaignId,
                    _ffnBuildSnapshotCapture(
                        dictCtx, requestHttp, sContainerId,
                        sProjectRepoPath, sCampaignId, sName,
                        request.listExcludedPaths),
                    sImageReference,
                    fsStageRunnerCredential=ffnBuildCredentialStager(
                        dictCtx, sContainerId)))
            agentCouncilStore.fdictAppendCampaignEvent(
                dictStore, sCampaignId,
                _fdictBuildEvent("campaignStarted", dictLaunched["sTurnId"]))
            return {
                "sCampaignId": sCampaignId,
                "sTurnId": dictLaunched["sTurnId"],
                "dictCampaign": agentCouncilStore.fjsonGetCampaignRecord(
                    dictStore, sCampaignId),
            }

        return await fgenericSubmitMapped(
            dictControllerState, sCampaignId,
            agentCouncilController.S_COMMAND_START, _fdictExecuteStart)


async def _fdictBuildRebuildMaterials(dictCtx, dictControllerState,
                                      sContainerId, sCampaignId):
    """Build runtime-rebuild materials when the hub restarted, else None.

    While a runtime is live the common case pays nothing. When it died
    with the hub, continuing IS paid provider work relaunching, so the
    rebuild passes the SAME gates start passes, in the same order: the
    immutable image resolves first so the evidence record's pin is
    always compared, then the credential gate, then the login-presence
    probe. The check is advisory — commands serialize per campaign, so
    a runtime appearing between this check and execution just means the
    materials go unused.
    """
    if dictControllerState["dictCampaignRuntime"].get(
            sCampaignId) is not None:
        return None
    sImageReference = await ffnBuildImageResolver(dictCtx, sContainerId)()
    fnRefuseRunnerBackendUnlessEnabled(sImageReference)
    await asyncio.to_thread(
        fnRefuseStartWithoutAProjectLogin, dictCtx, sContainerId)
    return {"sImageReference": sImageReference,
            "fsStageRunnerCredential": ffnBuildCredentialStager(
                dictCtx, sContainerId)}


def _fnRegisterResume(app, dictCtx):
    """Register POST /api/agent-councils/{sContainerId}/{sCampaignId}/resume.

    The explicit researcher resume (continuation plan section 4; spec
    amendment 2026-08-26): never unattended, always from a boundary the
    durable attempt record proves coherent. The listing's
    dictStoppingPoint advertised only what the record supports; this
    route re-derives that answer and adds the dynamic refusals the
    listing cannot promise — unsettled reservations, a changed image, a
    corrupt archive, a live peer hub.
    """

    @app.post("/api/agent-councils/{sContainerId}/{sCampaignId}/resume")
    @ffnDeclareCarrierMode(S_CARRIER_SEPARATE_AUTHORITY)
    async def fdictResumeCouncil(
        sContainerId: str, sCampaignId: str,
        request: CouncilResumeRequest, requestHttp: Request,
        sProjectDirectory: str = "",
    ):
        sName, sProjectRepoPath = ftResolveCouncilPrincipal(
            dictCtx, requestHttp, sContainerId, sProjectDirectory)
        dictStore = fdictCampaignStore(requestHttp)
        dictRegistry = fdictCouncilRegistry(requestHttp)
        dictControllerState = fdictControllerState(requestHttp)

        async def _fdictExecuteResume():
            fjsonRequireCampaign(
                dictStore, sCampaignId, sName, sProjectRepoPath)
            sImageReference = await ffnBuildImageResolver(
                dictCtx, sContainerId)()
            fnRefuseRunnerBackendUnlessEnabled(sImageReference)
            await asyncio.to_thread(
                fnRefuseStartWithoutAProjectLogin, dictCtx, sContainerId)
            dictResumed = (
                await agentCouncilController.fdictResumeCampaignDeliberation(
                    dictControllerState, dictStore, dictRegistry,
                    sCampaignId, sImageReference,
                    fsStageRunnerCredential=ffnBuildCredentialStager(
                        dictCtx, sContainerId),
                    bClearStopRequest=request.bClearStopRequest))
            agentCouncilStore.fdictAppendCampaignEvent(
                dictStore, sCampaignId,
                _fdictBuildEvent("campaignResumed",
                                 dictResumed.get("sTurnId", "")))
            return dictResumed

        return await fgenericSubmitMapped(
            dictControllerState, sCampaignId,
            agentCouncilController.S_COMMAND_RESUME, _fdictExecuteResume)


def _fnRegisterRespond(app, dictCtx):
    """Register POST /api/agent-councils/{sContainerId}/{sCampaignId}/respond."""

    @app.post("/api/agent-councils/{sContainerId}/{sCampaignId}/respond")
    @ffnDeclareCarrierMode(S_CARRIER_SEPARATE_AUTHORITY)
    async def fdictRespond(
        sContainerId: str, sCampaignId: str,
        request: CouncilRespondRequest, requestHttp: Request,
        sProjectDirectory: str = "",
    ):
        sName, sProjectRepoPath = ftResolveCouncilPrincipal(
            dictCtx, requestHttp, sContainerId, sProjectDirectory)
        dictStore = fdictCampaignStore(requestHttp)
        dictRegistry = fdictCouncilRegistry(requestHttp)

        dictControllerState = fdictControllerState(requestHttp)

        async def _fdictExecuteRespond():
            fjsonRequireCampaign(
                dictStore, sCampaignId, sName, sProjectRepoPath)
            dictContinued = (
                await agentCouncilController
                .fdictContinueCampaignAfterResponse(
                    dictControllerState, dictStore, dictRegistry,
                    sCampaignId, request.sResponseText,
                    [dictAnswer.model_dump()
                     for dictAnswer in request.listDecisionAnswers],
                    dictRebuildMaterials=await _fdictBuildRebuildMaterials(
                        dictCtx, dictControllerState, sContainerId,
                        sCampaignId)))
            agentCouncilStore.fdictAppendCampaignEvent(
                dictStore, sCampaignId,
                _fdictBuildEvent("researcherResponded",
                                 dictContinued["sTurnId"]))
            return {"sTurnId": dictContinued["sTurnId"],
                    "dictCampaign": agentCouncilStore.fjsonGetCampaignRecord(
                        dictStore, sCampaignId)}

        return await fgenericSubmitMapped(
            dictControllerState, sCampaignId,
            agentCouncilController.S_COMMAND_RESPOND, _fdictExecuteRespond)


def _fnRegisterRequestStop(app, dictCtx):
    """Register POST .../{sCampaignId}/request-stop."""

    @app.post(
        "/api/agent-councils/{sContainerId}/{sCampaignId}/request-stop")
    @ffnDeclareCarrierMode(S_CARRIER_SEPARATE_AUTHORITY)
    async def fdictRequestStop(
        sContainerId: str, sCampaignId: str, requestHttp: Request,
        sProjectDirectory: str = "",
    ):
        sName, sProjectRepoPath = ftResolveCouncilPrincipal(
            dictCtx, requestHttp, sContainerId, sProjectDirectory)
        dictStore = fdictCampaignStore(requestHttp)
        dictRegistry = fdictCouncilRegistry(requestHttp)

        dictControllerState = fdictControllerState(requestHttp)

        async def _fdictExecuteRequestStop():
            fjsonRequireCampaign(
                dictStore, sCampaignId, sName, sProjectRepoPath)
            dictStopped = await agentCouncilController.fdictRequestCampaignStop(
                dictControllerState, dictStore, dictRegistry, sCampaignId,
                lambda: _fnDrainCampaignWork(dictRegistry, sCampaignId))
            agentCouncilStore.fdictAppendCampaignEvent(
                dictStore, sCampaignId, _fdictBuildEvent("stopRequested"))
            return dictStopped

        return await fgenericSubmitMapped(
            dictControllerState, sCampaignId,
            agentCouncilController.S_COMMAND_REQUEST_STOP,
            _fdictExecuteRequestStop)


def _fnRegisterExhaustedRoundExits(app, dictCtx):
    """Register the three exhausted-round exit routes (section 5.1).

    Each posts one of the ENGINE'S exit transitions (remediation R6):
    a granted resolution round, a full objection disposition followed
    by one final veto, or rejection. The reject route also serves the
    planReady rejection — the engine's own guard decides which states
    may take it.
    """

    @app.post(
        "/api/agent-councils/{sContainerId}/{sCampaignId}"
        "/grant-resolution-round")
    @ffnDeclareCarrierMode(S_CARRIER_SEPARATE_AUTHORITY)
    async def fdictGrantResolutionRound(
        sContainerId: str, sCampaignId: str,
        request: CouncilGrantRoundRequest, requestHttp: Request,
        sProjectDirectory: str = "",
    ):
        sName, sProjectRepoPath = ftResolveCouncilPrincipal(
            dictCtx, requestHttp, sContainerId, sProjectDirectory)
        dictStore = fdictCampaignStore(requestHttp)
        dictRegistry = fdictCouncilRegistry(requestHttp)
        dictControllerState = fdictControllerState(requestHttp)

        async def _fdictExecuteGrant():
            fjsonRequireCampaign(
                dictStore, sCampaignId, sName, sProjectRepoPath)
            return await (
                agentCouncilController.fdictGrantCampaignResolutionRound(
                    dictControllerState, dictStore, dictRegistry,
                    sCampaignId, request.iGrantedRounds,
                    dictRebuildMaterials=await _fdictBuildRebuildMaterials(
                        dictCtx, dictControllerState, sContainerId,
                        sCampaignId)))

        return await fgenericSubmitMapped(
            dictControllerState, sCampaignId,
            agentCouncilController.S_COMMAND_GRANT_RESOLUTION_ROUND,
            _fdictExecuteGrant)

    @app.post(
        "/api/agent-councils/{sContainerId}/{sCampaignId}"
        "/resolve-objections")
    @ffnDeclareCarrierMode(S_CARRIER_SEPARATE_AUTHORITY)
    async def fdictResolveObjections(
        sContainerId: str, sCampaignId: str,
        request: CouncilResolveObjectionsRequest, requestHttp: Request,
        sProjectDirectory: str = "",
    ):
        sName, sProjectRepoPath = ftResolveCouncilPrincipal(
            dictCtx, requestHttp, sContainerId, sProjectDirectory)
        dictStore = fdictCampaignStore(requestHttp)
        dictRegistry = fdictCouncilRegistry(requestHttp)
        dictControllerState = fdictControllerState(requestHttp)

        async def _fdictExecuteResolve():
            fjsonRequireCampaign(
                dictStore, sCampaignId, sName, sProjectRepoPath)
            return await (
                agentCouncilController.fdictResolveCampaignObjections(
                    dictControllerState, dictStore, dictRegistry,
                    sCampaignId,
                    {sObjectionId: dictDisposition.model_dump()
                     for sObjectionId, dictDisposition
                     in request.dictDispositionByObjectionId.items()},
                    dictRebuildMaterials=await _fdictBuildRebuildMaterials(
                        dictCtx, dictControllerState, sContainerId,
                        sCampaignId)))

        return await fgenericSubmitMapped(
            dictControllerState, sCampaignId,
            agentCouncilController.S_COMMAND_RESOLVE_OBJECTIONS,
            _fdictExecuteResolve)

    @app.post(
        "/api/agent-councils/{sContainerId}/{sCampaignId}"
        "/reject-candidate")
    @ffnDeclareCarrierMode(S_CARRIER_SEPARATE_AUTHORITY)
    async def fdictRejectCandidate(
        sContainerId: str, sCampaignId: str,
        request: CouncilRejectRequest, requestHttp: Request,
        sProjectDirectory: str = "",
    ):
        sName, sProjectRepoPath = ftResolveCouncilPrincipal(
            dictCtx, requestHttp, sContainerId, sProjectDirectory)
        dictStore = fdictCampaignStore(requestHttp)
        dictRegistry = fdictCouncilRegistry(requestHttp)
        dictControllerState = fdictControllerState(requestHttp)

        async def _fdictExecuteReject():
            fjsonRequireCampaign(
                dictStore, sCampaignId, sName, sProjectRepoPath)
            dictRejected = (
                await agentCouncilController.fdictRejectCampaignCandidate(
                    dictControllerState, dictStore, dictRegistry,
                    sCampaignId, request.sReasonText))
            agentCouncilStore.fdictAppendCampaignEvent(
                dictStore, sCampaignId,
                _fdictBuildEvent("candidateRejected"))
            return dictRejected

        return await fgenericSubmitMapped(
            dictControllerState, sCampaignId,
            agentCouncilController.S_COMMAND_REJECT_CANDIDATE,
            _fdictExecuteReject)


def _fnRegisterAcceptPlan(app, dictCtx):
    """Register POST .../{sCampaignId}/accept-plan."""

    @app.post(
        "/api/agent-councils/{sContainerId}/{sCampaignId}/accept-plan")
    @ffnDeclareCarrierMode(S_CARRIER_SEPARATE_AUTHORITY)
    async def fdictAcceptPlan(
        sContainerId: str, sCampaignId: str, requestHttp: Request,
        sProjectDirectory: str = "",
    ):
        sName, sProjectRepoPath = ftResolveCouncilPrincipal(
            dictCtx, requestHttp, sContainerId, sProjectDirectory)
        dictStore = fdictCampaignStore(requestHttp)
        dictRegistry = fdictCouncilRegistry(requestHttp)
        dictControllerState = fdictControllerState(requestHttp)

        async def _fdictExecuteAcceptPlan():
            fjsonRequireCampaign(
                dictStore, sCampaignId, sName, sProjectRepoPath)
            dictAccepted = (
                await agentCouncilController.fdictAcceptCampaignPlan(
                    dictControllerState, dictStore, dictRegistry,
                    sCampaignId))
            agentCouncilStore.fdictAppendCampaignEvent(
                dictStore, sCampaignId, _fdictBuildEvent("planAccepted"))
            return dictAccepted

        return await fgenericSubmitMapped(
            dictControllerState, sCampaignId,
            agentCouncilController.S_COMMAND_ACCEPT_PLAN,
            _fdictExecuteAcceptPlan)


def _fsDescribeBaselineStaleness(dictStaleness):
    """Turn the staleness producer's verdict into one document sentence."""
    bStale = dictStaleness.get("bPlanningBaselineStale")
    sSummary = dictStaleness.get("sPlanningBaselineSummary", "")
    if bStale is True:
        return ("the repository has CHANGED since this council's sealed "
                f"baseline ({sSummary}); the plan speaks about the "
                "baseline, not the tree as it stands now")
    if bStale is None:
        return ("the baseline comparison could not run "
                f"({sSummary or 'no verdict'}); treat the repository as "
                "possibly changed since capture")
    return ""


def _fnRegisterPlanMarkdown(app, dictCtx):
    """Register GET /api/agent-councils/{sContainerId}/{sCampaignId}/plan.md.

    The deliverable, always available (continuation plan section 5): a
    council that died at a gate still yields its candidate. The bytes
    come from the ONE composer acceptance uses — for an ACCEPTED
    campaign they are exactly the sealed artifact's bytes, so the
    planArtifactSealed sha256 still identifies what this route serves,
    which is why no staleness sentence may be appended there; an
    unaccepted candidate instead carries the DRAFT watermark and the
    staleness statement in its own text. No candidate answers 404,
    never an empty document.
    """

    @app.get("/api/agent-councils/{sContainerId}/{sCampaignId}/plan.md")
    @ffnDeclareCarrierMode(S_CARRIER_SEPARATE_AUTHORITY)
    async def fresponseReadCouncilPlanMarkdown(
        sContainerId: str, sCampaignId: str, requestHttp: Request,
        sProjectDirectory: str = "",
    ):
        sName, sProjectRepoPath = ftResolveCouncilPrincipal(
            dictCtx, requestHttp, sContainerId, sProjectDirectory)
        dictStore = fdictCampaignStore(requestHttp)
        jsonCampaign = fjsonRequireCampaign(
            dictStore, sCampaignId, sName, sProjectRepoPath)
        if not jsonCampaign.get("dictCandidatePlan"):
            raise HTTPException(
                404, "this council holds no candidate plan; there is "
                "nothing to render yet")
        bAccepted = jsonCampaign.get("sState") in (
            "planAccepted", "awaitingImplementation")
        sStalenessStatement = ""
        if not bAccepted:
            sStalenessStatement = _fsDescribeBaselineStaleness(
                await asyncio.to_thread(
                    _fdictComputeBaselineStaleness, dictCtx, dictStore,
                    sContainerId, sProjectRepoPath, sCampaignId))
        return PlainTextResponse(
            agentCouncilController.fsComposePlanMarkdown(
                jsonCampaign, jsonCampaign["dictCandidatePlan"],
                sStalenessStatement=sStalenessStatement),
            media_type="text/markdown; charset=utf-8")


def _fnRegisterDeleteCouncil(app, dictCtx):
    """Register DELETE /api/agent-councils/{sContainerId}/{sCampaignId}."""

    @app.delete("/api/agent-councils/{sContainerId}/{sCampaignId}")
    @ffnDeclareCarrierMode(S_CARRIER_SEPARATE_AUTHORITY)
    async def fdictDeleteCouncil(
        sContainerId: str, sCampaignId: str, requestHttp: Request,
        sProjectDirectory: str = "",
    ):
        sName, sProjectRepoPath = ftResolveCouncilPrincipal(
            dictCtx, requestHttp, sContainerId, sProjectDirectory)
        dictStore = fdictCampaignStore(requestHttp)
        dictRegistry = fdictCouncilRegistry(requestHttp)

        async def _fdictExecuteDelete():
            # Identity resolves BEFORE the live-work refusal: a foreign
            # caller must see the same 404 as an unknown id, never a 409
            # that leaks that the campaign exists and is running.
            fjsonRequireCampaign(dictStore, sCampaignId, sName,
                                  sProjectRepoPath)
            if _fbCampaignHasLiveWork(dictRegistry, sCampaignId):
                raise HTTPException(
                    409, "stop the council before deleting its campaign")
            # The controller half first: refuse a live/launching drive
            # the registry cannot see, release the campaign's egress
            # boundary, and drop the runtime — durable deletion must
            # not strand either.
            await agentCouncilController.fdictDisposeCampaignRuntime(
                fdictControllerState(requestHttp), sCampaignId)
            agentCouncilStore.fbDeleteStoredCampaign(dictStore, sCampaignId)
            return {"bDeleted": True, "sCampaignId": sCampaignId}

        return await fgenericSubmitMapped(
            fdictControllerState(requestHttp), sCampaignId,
            agentCouncilController.S_COMMAND_DELETE, _fdictExecuteDelete)


def fnRegisterAll(app, dictCtx):
    """Register every agent-council route.

    Capabilities and the reads are registered before the parameterized
    ``{sCampaignId}`` routes so ``/capabilities`` is never captured as a
    campaign id.
    """
    _fnRegisterCapabilities(app, dictCtx)
    _fnRegisterSnapshotFeasibility(app, dictCtx)
    _fnRegisterListCouncils(app, dictCtx)
    _fnRegisterStartCouncil(app, dictCtx)
    _fnRegisterPollEvents(app, dictCtx)
    _fnRegisterRespond(app, dictCtx)
    _fnRegisterResume(app, dictCtx)
    _fnRegisterRequestStop(app, dictCtx)
    _fnRegisterExhaustedRoundExits(app, dictCtx)
    _fnRegisterAcceptPlan(app, dictCtx)
    _fnRegisterPlanMarkdown(app, dictCtx)
    _fnRegisterDeleteCouncil(app, dictCtx)
    _fnRegisterGetCouncil(app, dictCtx)
