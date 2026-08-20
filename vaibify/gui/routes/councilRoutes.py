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

from fastapi import HTTPException, Request
from pydantic import BaseModel, Field, field_validator

from .. import agentCouncilCampaign
from .. import agentCouncilController
from .. import agentCouncilDockerGateway
from .. import agentCouncilRegistry
from .. import agentCouncilStore
from ..pipelineServer import fdictRequireWorkflow, fsContainerNameForId
from ..routeContext import (
    fnRefuseContainerOnlyForHostProject,
    fnRejectAgentTokenLane,
    S_UNAVAILABLE_IN_HOST_MODE,
)
from ..routeScope import ffnDeclareCarrierMode, S_CARRIER_SEPARATE_AUTHORITY

# The capability name the refusal reads back to the researcher, in their
# words (section 21). One constant so every route names it identically.
S_COUNCIL_CAPABILITY = "Convening a council"

# The provider vocabulary a start request may name. Claude first, Codex
# second (section 8.1); an unrecognised provider is refused at validation
# rather than carried into a campaign that can never run.
SET_ALLOWED_PROVIDERS = frozenset({"claude", "codex"})

I_MAX_QUESTION_LENGTH = 20000
I_MAX_MODEL_LENGTH = 200
I_MAX_ROLE_LENGTH = 2000
I_MAX_RESPONSE_LENGTH = 20000
I_MAX_PLAN_LENGTH = 200000
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
    """Body for convening a planning council (section 6.3)."""

    sQuestion: str = Field(min_length=1, max_length=I_MAX_QUESTION_LENGTH)
    listParticipants: list[CouncilParticipantRequest] = Field(
        min_length=I_MIN_PARTICIPANTS, max_length=I_MAX_PARTICIPANTS)
    iChairbotIndex: int = Field(default=0, ge=0, lt=I_MAX_PARTICIPANTS)


class CouncilRespondRequest(BaseModel):
    """Body for answering a council's blocking question (section 6.5)."""

    sResponseText: str = Field(min_length=1, max_length=I_MAX_RESPONSE_LENGTH)


class CouncilAcceptPlanRequest(BaseModel):
    """Body for accepting a plan; the researcher supplies the final text.

    The text is required rather than read from the candidate plan
    because acceptance is a review gate (section 6.6): the researcher
    confirms the exact words that land, and the UI pre-fills them from
    the candidate plan.
    """

    sPlanText: str = Field(min_length=1, max_length=I_MAX_PLAN_LENGTH)


def _fdictCampaignStore(requestHttp):
    """Return the app-owned campaign store from ``app.state``."""
    return getattr(
        requestHttp.app.state,
        agentCouncilStore.S_COUNCIL_CAMPAIGN_STORE_STATE_KEY,
    )


def _fdictCouncilRegistry(requestHttp):
    """Return the app-owned council registry from ``app.state``."""
    return getattr(
        requestHttp.app.state,
        agentCouncilRegistry.S_COUNCIL_REGISTRY_STATE_KEY,
    )


def _fdictControllerState(requestHttp):
    """Return the app-owned controller state from ``app.state``."""
    return getattr(
        requestHttp.app.state,
        agentCouncilController.S_COUNCIL_CONTROLLER_STATE_KEY,
    )


def _fsGuardCouncilRoute(dictCtx, requestHttp, sContainerId):
    """Reject the agent lane, refuse a host project, require the daemon.

    The container lease is already enforced by ``ContainerAwareRoute``
    before this runs, so this is steps two and three of the ordering
    (section 21): the browser-only refusal (council reads and actions
    both expose researcher deliberation), then the mode branch, then the
    daemon requirement. Returns the resolved resource name.
    """
    fnRejectAgentTokenLane(requestHttp)
    sName = fsContainerNameForId(dictCtx.get("docker"), sContainerId)
    fnRefuseContainerOnlyForHostProject(sName, S_COUNCIL_CAPABILITY)
    dictCtx["require"](sContainerId)
    return sName


def _ftResolveCouncilPrincipal(dictCtx, requestHttp, sContainerId):
    """Guard the route and resolve the (resource name, project repo) pair.

    The canonical identity a campaign is bound to and matched against
    (remediation R2): the lease principal is the container NAME, and the
    repo is the open workflow's validated project repo — one container
    can host several repos, and a campaign belongs to exactly one.
    """
    sName = _fsGuardCouncilRoute(dictCtx, requestHttp, sContainerId)
    dictWorkflow = fdictRequireWorkflow(dictCtx["workflows"], sContainerId)
    sProjectRepoPath = dictWorkflow.get("sProjectRepoPath", "")
    if not sProjectRepoPath:
        raise HTTPException(
            409, "the open workflow is not inside a project repository; "
            "a council campaign is scoped to one")
    return sName, sProjectRepoPath


def _fdictBuildEvent(sKind, sTurnId="", sDetail=""):
    """Build a display event; the store stamps its sequence number."""
    return {"sKind": sKind, "sTurnId": sTurnId, "sDetail": sDetail}


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


def _fsLaunchCampaignTurn(dictRegistry, dictStore, sCampaignId):
    """Mint and register one turn-in-flight, refusing a duplicate launch.

    One stable registry record per turn (section 15.3): the turn id is
    minted from a per-campaign counter so a second launch while a turn is
    already live is refused, not silently doubled. The actual paid turn
    is driven by the registry and its runner lifecycle at integration;
    Phase 3 records the turn so the idle-watchdog veto holds and the UI
    can show a live council.
    """
    if _fbCampaignHasLiveWork(dictRegistry, sCampaignId):
        raise HTTPException(
            409, "a turn is already in flight for this council")
    sTurnId = agentCouncilStore.fsMintNextTurnId(dictStore, sCampaignId)
    agentCouncilRegistry.fbRegisterTurnInFlight(
        dictRegistry, sCampaignId, sTurnId)
    return sTurnId


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


def _fdictCreateCampaignFromRequest(request, dictProjectIdentity):
    """Build a draft campaign record from a validated start request."""
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
            sChairbotParticipantId=sChairbotId,
            dictProjectIdentity=dictProjectIdentity,
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
        return _fdictContainerCapabilities()


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


def _fdictContainerCapabilities():
    """Report the council as available, with the runner-backed providers."""
    return {
        "bAvailable": True,
        "sUnavailableIn": "",
        "listProviders": [
            {"sProvider": sProvider, "sBackend": "runner"}
            for sProvider in sorted(SET_ALLOWED_PROVIDERS)
        ],
    }


def _fnRegisterListCouncils(app, dictCtx):
    """Register GET /api/agent-councils/{sContainerId}."""

    @app.get("/api/agent-councils/{sContainerId}")
    @ffnDeclareCarrierMode(S_CARRIER_SEPARATE_AUTHORITY)
    async def fdictListCouncils(sContainerId: str, requestHttp: Request):
        sName, sProjectRepoPath = _ftResolveCouncilPrincipal(
            dictCtx, requestHttp, sContainerId)
        return {
            "listCampaigns": agentCouncilStore.flistSummariseCampaigns(
                _fdictCampaignStore(requestHttp),
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
    ):
        sName, sProjectRepoPath = _ftResolveCouncilPrincipal(
            dictCtx, requestHttp, sContainerId)
        jsonCampaign = _fjsonRequireCampaign(
            _fdictCampaignStore(requestHttp), sCampaignId, sName,
            sProjectRepoPath)
        # The "runner may exist" surface (remediation R4): quarantined
        # reservations are read through the gateway's registry-only
        # view — no daemon is consulted on this read path.
        dictGatewayView = (
            agentCouncilDockerGateway.fdictCreateCouncilDockerGateway(
                None, _fdictCouncilRegistry(requestHttp)))
        return {
            "dictCampaign": jsonCampaign,
            "listQuarantinedRunners":
                agentCouncilDockerGateway
                .flistDescribeQuarantinedReservations(
                    dictGatewayView, sCampaignId),
        }


def _fnRegisterPollEvents(app, dictCtx):
    """Register GET /api/agent-councils/{sContainerId}/{sCampaignId}/events."""

    @app.get("/api/agent-councils/{sContainerId}/{sCampaignId}/events")
    @ffnDeclareCarrierMode(S_CARRIER_SEPARATE_AUTHORITY)
    async def fdictPollEvents(
        sContainerId: str, sCampaignId: str, requestHttp: Request,
        iAfter: int = 0,
    ):
        sName, sProjectRepoPath = _ftResolveCouncilPrincipal(
            dictCtx, requestHttp, sContainerId)
        if iAfter < 0:
            raise HTTPException(400, "iAfter must not be negative")
        _fjsonRequireCampaign(
            _fdictCampaignStore(requestHttp), sCampaignId, sName,
            sProjectRepoPath)
        dictEvents = agentCouncilStore.fdictCollectCampaignEvents(
            _fdictCampaignStore(requestHttp), sCampaignId, iAfter)
        if dictEvents is None:
            raise HTTPException(404, f"no council campaign '{sCampaignId}'")
        return dictEvents


def _fnRegisterStartCouncil(app, dictCtx):
    """Register POST /api/agent-councils/{sContainerId}/start."""

    @app.post("/api/agent-councils/{sContainerId}/start")
    @ffnDeclareCarrierMode(S_CARRIER_SEPARATE_AUTHORITY)
    async def fdictStartCouncil(
        sContainerId: str, request: CouncilStartRequest,
        requestHttp: Request,
    ):
        sName, sProjectRepoPath = _ftResolveCouncilPrincipal(
            dictCtx, requestHttp, sContainerId)
        dictStore = _fdictCampaignStore(requestHttp)
        dictRegistry = _fdictCouncilRegistry(requestHttp)
        dictCampaign = _fdictCreateCampaignFromRequest(request, {
            "sResourceName": sName,
            "sProjectRepoPath": sProjectRepoPath,
            "sSnapshotIdentity": "",
        })

        async def _fdictExecuteStart():
            agentCouncilCampaign.fnTransitionCampaignState(
                dictCampaign, agentCouncilCampaign.S_STATE_PLANNING,
                "researcher convened the council")
            agentCouncilStore.fdictRegisterStartedCampaign(
                dictStore, dictCampaign)
            sTurnId = _fsLaunchCampaignTurn(
                dictRegistry, dictStore, dictCampaign["sCampaignId"])
            agentCouncilStore.fdictAppendCampaignEvent(
                dictStore, dictCampaign["sCampaignId"],
                _fdictBuildEvent("campaignStarted", sTurnId))
            return {
                "sCampaignId": dictCampaign["sCampaignId"],
                "sTurnId": sTurnId,
                "dictCampaign": agentCouncilStore.fjsonGetCampaignRecord(
                    dictStore, dictCampaign["sCampaignId"]),
            }

        return await agentCouncilController.fgenericSubmitCampaignCommand(
            _fdictControllerState(requestHttp), dictCampaign["sCampaignId"],
            agentCouncilController.S_COMMAND_START, _fdictExecuteStart)


def _fnRegisterRespond(app, dictCtx):
    """Register POST /api/agent-councils/{sContainerId}/{sCampaignId}/respond."""

    @app.post("/api/agent-councils/{sContainerId}/{sCampaignId}/respond")
    @ffnDeclareCarrierMode(S_CARRIER_SEPARATE_AUTHORITY)
    async def fdictRespond(
        sContainerId: str, sCampaignId: str,
        request: CouncilRespondRequest, requestHttp: Request,
    ):
        sName, sProjectRepoPath = _ftResolveCouncilPrincipal(
            dictCtx, requestHttp, sContainerId)
        dictStore = _fdictCampaignStore(requestHttp)
        dictRegistry = _fdictCouncilRegistry(requestHttp)

        async def _fdictExecuteRespond():
            dictCampaign = _fjsonRequireCampaign(
                dictStore, sCampaignId, sName, sProjectRepoPath)
            dictCampaign["listResearcherResponses"].append(
                {"sResponseText": request.sResponseText})
            sTurnId = _fsLaunchCampaignTurn(
                dictRegistry, dictStore, sCampaignId)
            agentCouncilStore.fnCheckpointStoredCampaign(
                dictStore, sCampaignId, dictCampaign)
            agentCouncilStore.fdictAppendCampaignEvent(
                dictStore, sCampaignId,
                _fdictBuildEvent("researcherResponded", sTurnId))
            return {"sTurnId": sTurnId, "dictCampaign": dictCampaign}

        return await agentCouncilController.fgenericSubmitCampaignCommand(
            _fdictControllerState(requestHttp), sCampaignId,
            agentCouncilController.S_COMMAND_RESPOND, _fdictExecuteRespond)


def _fnRegisterRequestStop(app, dictCtx):
    """Register POST .../{sCampaignId}/request-stop."""

    @app.post(
        "/api/agent-councils/{sContainerId}/{sCampaignId}/request-stop")
    @ffnDeclareCarrierMode(S_CARRIER_SEPARATE_AUTHORITY)
    async def fdictRequestStop(
        sContainerId: str, sCampaignId: str, requestHttp: Request,
    ):
        sName, sProjectRepoPath = _ftResolveCouncilPrincipal(
            dictCtx, requestHttp, sContainerId)
        dictStore = _fdictCampaignStore(requestHttp)
        dictRegistry = _fdictCouncilRegistry(requestHttp)

        async def _fdictExecuteRequestStop():
            dictCampaign = _fjsonRequireCampaign(
                dictStore, sCampaignId, sName, sProjectRepoPath)
            _fnDrainCampaignWork(dictRegistry, sCampaignId)
            dictCampaign["bStopRequested"] = True
            agentCouncilCampaign.fnTransitionCampaignState(
                dictCampaign, agentCouncilCampaign.S_STATE_INTERRUPTED,
                "researcher requested a stop")
            agentCouncilStore.fnCheckpointStoredCampaign(
                dictStore, sCampaignId, dictCampaign)
            agentCouncilStore.fdictAppendCampaignEvent(
                dictStore, sCampaignId, _fdictBuildEvent("stopRequested"))
            return {"dictCampaign": dictCampaign}

        return await agentCouncilController.fgenericSubmitCampaignCommand(
            _fdictControllerState(requestHttp), sCampaignId,
            agentCouncilController.S_COMMAND_REQUEST_STOP,
            _fdictExecuteRequestStop)


def _fnRegisterAcceptPlan(app, dictCtx):
    """Register POST .../{sCampaignId}/accept-plan."""

    @app.post(
        "/api/agent-councils/{sContainerId}/{sCampaignId}/accept-plan")
    @ffnDeclareCarrierMode(S_CARRIER_SEPARATE_AUTHORITY)
    async def fdictAcceptPlan(
        sContainerId: str, sCampaignId: str,
        request: CouncilAcceptPlanRequest, requestHttp: Request,
    ):
        sName, sProjectRepoPath = _ftResolveCouncilPrincipal(
            dictCtx, requestHttp, sContainerId)
        dictStore = _fdictCampaignStore(requestHttp)

        async def _fdictExecuteAcceptPlan():
            dictCampaign = _fjsonRequireCampaign(
                dictStore, sCampaignId, sName, sProjectRepoPath)
            sLocalPlanPath = agentCouncilStore.fsAcceptCampaignPlanLocally(
                dictStore, sCampaignId, request.sPlanText)
            dictCampaign["listResearcherDecisions"].append(
                {"sDecision": "acceptPlan"})
            agentCouncilCampaign.fnTransitionCampaignState(
                dictCampaign, agentCouncilCampaign.S_STATE_PLAN_ACCEPTED,
                "researcher accepted the plan")
            agentCouncilStore.fnCheckpointStoredCampaign(
                dictStore, sCampaignId, dictCampaign)
            agentCouncilStore.fdictAppendCampaignEvent(
                dictStore, sCampaignId, _fdictBuildEvent("planAccepted"))
            return {"bAccepted": True, "sLocalPlanPath": sLocalPlanPath}

        return await agentCouncilController.fgenericSubmitCampaignCommand(
            _fdictControllerState(requestHttp), sCampaignId,
            agentCouncilController.S_COMMAND_ACCEPT_PLAN,
            _fdictExecuteAcceptPlan)


def _fnRegisterDeleteCouncil(app, dictCtx):
    """Register DELETE /api/agent-councils/{sContainerId}/{sCampaignId}."""

    @app.delete("/api/agent-councils/{sContainerId}/{sCampaignId}")
    @ffnDeclareCarrierMode(S_CARRIER_SEPARATE_AUTHORITY)
    async def fdictDeleteCouncil(
        sContainerId: str, sCampaignId: str, requestHttp: Request,
    ):
        sName, sProjectRepoPath = _ftResolveCouncilPrincipal(
            dictCtx, requestHttp, sContainerId)
        dictStore = _fdictCampaignStore(requestHttp)
        dictRegistry = _fdictCouncilRegistry(requestHttp)

        async def _fdictExecuteDelete():
            # Identity resolves BEFORE the live-work refusal: a foreign
            # caller must see the same 404 as an unknown id, never a 409
            # that leaks that the campaign exists and is running.
            _fjsonRequireCampaign(dictStore, sCampaignId, sName,
                                  sProjectRepoPath)
            if _fbCampaignHasLiveWork(dictRegistry, sCampaignId):
                raise HTTPException(
                    409, "stop the council before deleting its campaign")
            agentCouncilStore.fbDeleteStoredCampaign(dictStore, sCampaignId)
            return {"bDeleted": True, "sCampaignId": sCampaignId}

        return await agentCouncilController.fgenericSubmitCampaignCommand(
            _fdictControllerState(requestHttp), sCampaignId,
            agentCouncilController.S_COMMAND_DELETE, _fdictExecuteDelete)


def _fjsonRequireCampaign(dictStore, sCampaignId, sResourceName,
                          sProjectRepoPath):
    """Return a campaign bound to this principal, or raise 404.

    A campaign bound to another resource or another repo answers with
    the SAME 404 an unknown id gets (remediation R2): a foreign caller
    learns nothing about whether the id exists.
    """
    jsonCampaign = agentCouncilStore.fjsonGetCampaignRecord(
        dictStore, sCampaignId)
    if jsonCampaign is None or not (
            agentCouncilCampaign.fbCampaignMatchesPrincipal(
                jsonCampaign, sResourceName, sProjectRepoPath)):
        raise HTTPException(404, f"no council campaign '{sCampaignId}'")
    return jsonCampaign


def fnRegisterAll(app, dictCtx):
    """Register every agent-council route.

    Capabilities and the reads are registered before the parameterized
    ``{sCampaignId}`` routes so ``/capabilities`` is never captured as a
    campaign id.
    """
    _fnRegisterCapabilities(app, dictCtx)
    _fnRegisterListCouncils(app, dictCtx)
    _fnRegisterStartCouncil(app, dictCtx)
    _fnRegisterPollEvents(app, dictCtx)
    _fnRegisterRespond(app, dictCtx)
    _fnRegisterRequestStop(app, dictCtx)
    _fnRegisterAcceptPlan(app, dictCtx)
    _fnRegisterDeleteCouncil(app, dictCtx)
    _fnRegisterGetCouncil(app, dictCtx)
