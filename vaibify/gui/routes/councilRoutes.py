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
from pydantic import BaseModel, Field, field_validator

from vaibify.docker import dockerConnection

from .. import agentCouncilCampaign
from .. import agentCouncilController
from .. import agentCouncilDockerGateway
from .. import agentCouncilRegistry
from .. import agentCouncilResolution
from .. import agentCouncilStore
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

# The capability name the refusal reads back to the researcher, in their
# words (section 21). One constant so every route names it identically.
S_COUNCIL_CAPABILITY = "Convening a council"

# The provider vocabulary a start request may name. Claude ONLY
# (remediation R7/R9): Codex has no reviewed adapter — its feasibility
# work is a separate follow-up — and advertising an adapter-less
# provider convenes a campaign that can never run. An unrecognised
# provider is refused at validation.
SET_ALLOWED_PROVIDERS = frozenset({"claude"})

I_MAX_QUESTION_LENGTH = 20000
I_MAX_MODEL_LENGTH = 200
I_MAX_ROLE_LENGTH = 2000
I_MAX_RESPONSE_LENGTH = 20000
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


# Acceptance takes NO body (remediation R3): what lands in plan.md is
# the council's own server-held candidate, accepted through the
# engine's planReady gate — caller-supplied plan text was the accept
# bypass the review flagged, and the review gate is the researcher
# READING the candidate, not retyping it.


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


def _ftResolveCouncilPrincipal(dictCtx, requestHttp, sContainerId,
                               sChosenDirectory=""):
    """Guard the route and resolve the (resource name, project repo) pair.

    The canonical identity a campaign is bound to and matched against
    (remediation R2): the lease principal is the container NAME, and the
    repo is one validated project repo — a container can host several,
    and a campaign belongs to exactly one.

    ``sChosenDirectory`` is accepted by the READ routes too, not only
    by start (2026-08-24). It has to be: a toolkit container tracks
    several repositories, so with no workflow open the resolver
    legitimately refuses to guess — and every poll route then answered
    409 forever, which froze a live researcher's panel for a whole
    deliberation. The value is validated against the tracked set
    exactly as start validates it, so nothing is trusted that was not
    trusted before, and the repo half of the principal is still
    enforced: a campaign bound to another repo in the same container
    stays unreachable.

    That invariant is UNCHANGED by the Blank Project work (2026-08-22).
    What widened is only where the repo half is resolved FROM. It used
    to come exclusively from an open workflow, which refused a project
    with no steps defined yet — and a project at that stage is arguably
    the one a planning council helps most. When no workflow is open the
    repo is resolved from the tracked-repos sidecar instead, which is
    the researcher's own already-recorded statement about which
    directories matter.
    """
    sName = _fsGuardCouncilRoute(dictCtx, requestHttp, sContainerId)
    dictWorkflow = (dictCtx.get("workflows") or {}).get(sContainerId) or {}
    sProjectRepoPath = dictWorkflow.get("sProjectRepoPath", "")
    if sProjectRepoPath:
        return sName, sProjectRepoPath
    return sName, _fsResolveDominantRepositoryPath(
        dictCtx, sContainerId, sChosenDirectory)


def _fsResolveDominantRepositoryPath(dictCtx, sContainerId,
                                     sChosenDirectory=""):
    """Return the Blank Project's directory, or refuse and say why.

    A Blank Project — no steps defined yet — is still tied to a
    directory, so this answers "which one" rather than inventing a
    repo-less campaign kind. Two flavours of Blank Project both land
    here and neither needs special handling: a true greenfield
    directory snapshots to almost nothing, and a slightly-brownfield
    one (files, no steps) snapshots like any other repo.

    The tracked-repos sidecar is REUSED rather than a council-specific
    setting added: it already records which directories the researcher
    considers part of this project, it is already editable in the Repos
    panel, and a second copy of that judgement would be one more thing
    to disagree with the first.

    Exactly one tracked repo is unambiguous. Several is a real CHOICE,
    and the researcher makes it: a toolkit container tracks many
    repositories by design — one live project tracks nine — so the
    first version of this, which refused until only one was tracked,
    was telling researchers to break the Repos panel's actual purpose.
    The candidates are offered at convene time instead. Still never
    guessed: silently picking one would snapshot the wrong codebase and
    every participant would reason about the wrong thing.
    """
    from .. import projectRoots
    listTracked = _flistTrackedDirectoryNames(dictCtx, sContainerId)
    sRoot = projectRoots.fsResolveProjectRoot(
        sContainerId, WORKSPACE_ROOT).rstrip("/")
    if sChosenDirectory:
        # Validated against the tracked set, never trusted: the value
        # becomes a container path, so a basename this project does not
        # track is refused rather than joined onto the workspace root.
        if sChosenDirectory not in listTracked:
            raise HTTPException(
                400, f"{sChosenDirectory!r} is not one of this project's "
                "tracked directories")
        return posixpath.join(sRoot, sChosenDirectory)
    if len(listTracked) == 1:
        return posixpath.join(sRoot, listTracked[0])
    if not listTracked:
        raise HTTPException(
            409, "this project has no tracked directory for a council to "
            "reason about. Track the directory this project lives in "
            "from the Repos panel, then convene.")
    raise HTTPException(
        409, "this project tracks several directories ("
        + ", ".join(sorted(listTracked))
        + "), so a council needs to be told which one it is about. "
        "Choose it when you convene, or open the workflow you mean.")


def _flistTrackedDirectoryNames(dictCtx, sContainerId):
    """Return the basenames the tracked-repos sidecar records."""
    from .. import trackedReposManager
    dictSidecar = trackedReposManager.fdictReadOrSeedSidecar(
        dictCtx["docker"], sContainerId)
    return [dictEntry.get("sName", "")
            for dictEntry in (dictSidecar or {}).get("listTracked", [])
            if dictEntry.get("sName")]


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


async def _fgenericSubmitMapped(dictControllerState, sCampaignId,
                                sCommandKind, ffnExecuteCommand):
    """Submit a controller command, mapping its refusals onto HTTP.

    A ``CouncilCommandError`` is a serialization or lifecycle refusal —
    the campaign is deliberating, has nothing to continue, or was asked
    for a command outside the vocabulary — and answers 409 with the
    controller's own words. Everything else propagates untouched.
    """
    try:
        return await agentCouncilController.fgenericSubmitCampaignCommand(
            dictControllerState, sCampaignId, sCommandKind,
            ffnExecuteCommand)
    except agentCouncilController.CouncilCommandError as error:
        raise HTTPException(409, str(error))


def _fnRefuseRunnerBackendUnlessEnabled(sImageIdentity):
    """Refuse paid runner work unless the credential gate enables it.

    The gate defaults OFF (remediation R10): starting a council is paid
    provider work over a copied credential, and nothing but the
    maintainer's recorded live check enables that. The start path
    resolves the project image FIRST and passes it here, so the
    evidence record's image pin is ALWAYS compared before a campaign
    is registered — an evidence record verified in a different image
    refuses at start, not at the first burned runner. The refusal
    carries the gate's own reason so the researcher reads why, not a
    bare 409.
    """
    from .. import agentCouncilCredentialGate
    dictEnablement = (
        agentCouncilCredentialGate.fdictEvaluateCredentialEnablement(
            "claude", sImageIdentity))
    if not dictEnablement["bEnabled"]:
        raise HTTPException(409, dictEnablement["sReason"])


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
                            _fdictCampaignStore(requestHttp)[
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


def _ffnBuildImageResolver(dictCtx, sContainerId):
    """Build the closure resolving the project container's IMMUTABLE image.

    The content-addressed image id, never the display tag: a tag can be
    repointed at a different image (a different CLI) without the
    evidence record's key changing, so both the credential gate's
    comparison and the runners' launches ride the id — which also
    closes the tag-resolution race between the gate check and the
    runner create.
    """

    async def _fsResolveRunnerImage():
        listContainers = await asyncio.to_thread(
            dictCtx["docker"].flistGetRunningContainers)
        for dictContainer in listContainers:
            if dictContainer["sContainerId"] == sContainerId:
                sImageIdentity = dictContainer.get("sImageIdentity")
                if sImageIdentity:
                    return sImageIdentity
                break
        raise HTTPException(
            502, "cannot resolve the project container's immutable "
            "image identity for the council runners")

    return _fsResolveRunnerImage


def _fnRefuseStartWithoutAProjectLogin(dictCtx, sContainerId):
    """Refuse a launch when the project holds no copyable provider token.

    R10's live PRESENCE probe, at the cheapest correct point: the
    credential gate says the maintainer's evidence record permits paid
    work in this image, and this says the project actually has a login
    the runner lane could copy. It proves presence, never usability —
    a token that no longer authenticates is only discoverable by
    spending a turn, and the first turn's authentication-classified
    failure is what reports that. It runs before the campaign
    registers and before any runner exists; the per-turn extraction
    would otherwise discover an absent login only after a runner had
    been created and destroyed, and the researcher would read a failed
    turn instead of "log in". The token is discarded inside the probe;
    only a boolean returns.
    """
    from .. import agentCouncilProviders
    from .. import projectRoots
    sWorkspaceRoot = projectRoots.fsResolveProjectRoot(
        sContainerId, WORKSPACE_ROOT)
    sUnusable = agentCouncilProviders.fsExplainUnusableRunnerCredential(
        dictCtx["docker"], sContainerId,
        agentCouncilProviders.fsComposeCredentialContainerPath(
            sWorkspaceRoot))
    if sUnusable:
        raise HTTPException(409, sUnusable)


def _ffnBuildCredentialStager(dictCtx, sContainerId):
    """Build the closure that stages the runner's host credential copy.

    Invoked in the launch worker thread by the controller's PRODUCTION
    connection factory on the first connection build — a patched fake
    seam never lands there, so no fake lane needs a persisted login.
    Extraction reads the narrowest authenticating field from the login
    the project container already persists (a file fetch, never a
    container command) and materializes it as an ephemeral mode-600
    host file; the workspace root goes through ``projectRoots``, never
    a ``/workspace`` literal.
    """
    from .. import agentCouncilProviders
    from .. import projectRoots

    def _fsStageRunnerCredential():
        sWorkspaceRoot = projectRoots.fsResolveProjectRoot(
            sContainerId, WORKSPACE_ROOT)
        dictCredential = agentCouncilProviders.fdictExtractRunnerCredential(
            dictCtx["docker"], sContainerId,
            agentCouncilProviders.fsComposeCredentialContainerPath(
                sWorkspaceRoot))
        return agentCouncilProviders.fsStageRunnerCredentialFile(
            dictCredential["sAccessToken"], dictCredential["listScopes"])

    return _fsStageRunnerCredential


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
            dictSettings=request.dictSettings or None,
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
            _flistTrackedDirectoryNames(dictCtx, sContainerId))
        if len(listCandidates) > 1:
            dictCapabilities["listCandidateDirectories"] = listCandidates
            return
        try:
            sProjectRepoPath = _fsResolveDominantRepositoryPath(
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
        sProjectRepoPath = _fsResolveDominantRepositoryPath(
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
        sImageIdentity = await _ffnBuildImageResolver(
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
        sName, sProjectRepoPath = _ftResolveCouncilPrincipal(
            dictCtx, requestHttp, sContainerId, sProjectDirectory)
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
        sProjectDirectory: str = "",
    ):
        sName, sProjectRepoPath = _ftResolveCouncilPrincipal(
            dictCtx, requestHttp, sContainerId, sProjectDirectory)
        dictStore = _fdictCampaignStore(requestHttp)
        jsonCampaign = _fjsonRequireCampaign(
            dictStore, sCampaignId, sName, sProjectRepoPath)
        jsonCampaign.update(await asyncio.to_thread(
            _fdictComputeBaselineStaleness, dictCtx, dictStore,
            sContainerId, sProjectRepoPath, sCampaignId))
        # The "runner may exist" surface (remediation R4): quarantined
        # reservations are read through the gateway's registry-only
        # view — no daemon is consulted on this read path.
        dictGatewayView = (
            agentCouncilDockerGateway.fdictCreateCouncilDockerGateway(
                None, _fdictCouncilRegistry(requestHttp)))
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
        sName, sProjectRepoPath = _ftResolveCouncilPrincipal(
            dictCtx, requestHttp, sContainerId, sProjectDirectory)
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
        sName, sProjectRepoPath = _ftResolveCouncilPrincipal(
            dictCtx, requestHttp, sContainerId, request.sProjectDirectory)
        dictStore = _fdictCampaignStore(requestHttp)
        dictRegistry = _fdictCouncilRegistry(requestHttp)
        dictCampaign = _fdictCreateCampaignFromRequest(request, {
            **agentCouncilCampaign.DICT_EMPTY_PROJECT_IDENTITY,
            "sResourceName": sName,
            "sProjectRepoPath": sProjectRepoPath,
        })

        dictControllerState = _fdictControllerState(requestHttp)
        sCampaignId = dictCampaign["sCampaignId"]

        async def _fdictExecuteStart():
            # The image resolves BEFORE the credential gate so the
            # evidence record's image pin is always compared, and both
            # run before the campaign registers — a refusal here leaves
            # no record at all (the launch itself is transactional
            # about the record it registers below).
            sImageReference = await _ffnBuildImageResolver(
                dictCtx, sContainerId)()
            _fnRefuseRunnerBackendUnlessEnabled(sImageReference)
            await asyncio.to_thread(
                _fnRefuseStartWithoutAProjectLogin, dictCtx, sContainerId)
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
                    fsStageRunnerCredential=_ffnBuildCredentialStager(
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

        return await _fgenericSubmitMapped(
            dictControllerState, sCampaignId,
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

        dictControllerState = _fdictControllerState(requestHttp)

        async def _fdictExecuteRespond():
            _fjsonRequireCampaign(
                dictStore, sCampaignId, sName, sProjectRepoPath)
            dictContinued = (
                await agentCouncilController
                .fdictContinueCampaignAfterResponse(
                    dictControllerState, dictStore, dictRegistry,
                    sCampaignId, request.sResponseText,
                    [dictAnswer.model_dump()
                     for dictAnswer in request.listDecisionAnswers]))
            agentCouncilStore.fdictAppendCampaignEvent(
                dictStore, sCampaignId,
                _fdictBuildEvent("researcherResponded",
                                 dictContinued["sTurnId"]))
            return {"sTurnId": dictContinued["sTurnId"],
                    "dictCampaign": agentCouncilStore.fjsonGetCampaignRecord(
                        dictStore, sCampaignId)}

        return await _fgenericSubmitMapped(
            dictControllerState, sCampaignId,
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

        dictControllerState = _fdictControllerState(requestHttp)

        async def _fdictExecuteRequestStop():
            _fjsonRequireCampaign(
                dictStore, sCampaignId, sName, sProjectRepoPath)
            dictStopped = await agentCouncilController.fdictRequestCampaignStop(
                dictControllerState, dictStore, dictRegistry, sCampaignId,
                lambda: _fnDrainCampaignWork(dictRegistry, sCampaignId))
            agentCouncilStore.fdictAppendCampaignEvent(
                dictStore, sCampaignId, _fdictBuildEvent("stopRequested"))
            return dictStopped

        return await _fgenericSubmitMapped(
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
    ):
        sName, sProjectRepoPath = _ftResolveCouncilPrincipal(
            dictCtx, requestHttp, sContainerId)
        dictStore = _fdictCampaignStore(requestHttp)
        dictRegistry = _fdictCouncilRegistry(requestHttp)
        dictControllerState = _fdictControllerState(requestHttp)

        async def _fdictExecuteGrant():
            _fjsonRequireCampaign(
                dictStore, sCampaignId, sName, sProjectRepoPath)
            return await (
                agentCouncilController.fdictGrantCampaignResolutionRound(
                    dictControllerState, dictStore, dictRegistry,
                    sCampaignId, request.iGrantedRounds))

        return await _fgenericSubmitMapped(
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
    ):
        sName, sProjectRepoPath = _ftResolveCouncilPrincipal(
            dictCtx, requestHttp, sContainerId)
        dictStore = _fdictCampaignStore(requestHttp)
        dictRegistry = _fdictCouncilRegistry(requestHttp)
        dictControllerState = _fdictControllerState(requestHttp)

        async def _fdictExecuteResolve():
            _fjsonRequireCampaign(
                dictStore, sCampaignId, sName, sProjectRepoPath)
            return await (
                agentCouncilController.fdictResolveCampaignObjections(
                    dictControllerState, dictStore, dictRegistry,
                    sCampaignId,
                    {sObjectionId: dictDisposition.model_dump()
                     for sObjectionId, dictDisposition
                     in request.dictDispositionByObjectionId.items()}))

        return await _fgenericSubmitMapped(
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
    ):
        sName, sProjectRepoPath = _ftResolveCouncilPrincipal(
            dictCtx, requestHttp, sContainerId)
        dictStore = _fdictCampaignStore(requestHttp)
        dictRegistry = _fdictCouncilRegistry(requestHttp)
        dictControllerState = _fdictControllerState(requestHttp)

        async def _fdictExecuteReject():
            _fjsonRequireCampaign(
                dictStore, sCampaignId, sName, sProjectRepoPath)
            dictRejected = (
                await agentCouncilController.fdictRejectCampaignCandidate(
                    dictControllerState, dictStore, dictRegistry,
                    sCampaignId, request.sReasonText))
            agentCouncilStore.fdictAppendCampaignEvent(
                dictStore, sCampaignId,
                _fdictBuildEvent("candidateRejected"))
            return dictRejected

        return await _fgenericSubmitMapped(
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
    ):
        sName, sProjectRepoPath = _ftResolveCouncilPrincipal(
            dictCtx, requestHttp, sContainerId)
        dictStore = _fdictCampaignStore(requestHttp)
        dictRegistry = _fdictCouncilRegistry(requestHttp)
        dictControllerState = _fdictControllerState(requestHttp)

        async def _fdictExecuteAcceptPlan():
            _fjsonRequireCampaign(
                dictStore, sCampaignId, sName, sProjectRepoPath)
            dictAccepted = (
                await agentCouncilController.fdictAcceptCampaignPlan(
                    dictControllerState, dictStore, dictRegistry,
                    sCampaignId))
            agentCouncilStore.fdictAppendCampaignEvent(
                dictStore, sCampaignId, _fdictBuildEvent("planAccepted"))
            return dictAccepted

        return await _fgenericSubmitMapped(
            dictControllerState, sCampaignId,
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
            # The controller half first: refuse a live/launching drive
            # the registry cannot see, release the campaign's egress
            # boundary, and drop the runtime — durable deletion must
            # not strand either.
            await agentCouncilController.fdictDisposeCampaignRuntime(
                _fdictControllerState(requestHttp), sCampaignId)
            agentCouncilStore.fbDeleteStoredCampaign(dictStore, sCampaignId)
            return {"bDeleted": True, "sCampaignId": sCampaignId}

        return await _fgenericSubmitMapped(
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
    _fnRegisterSnapshotFeasibility(app, dictCtx)
    _fnRegisterListCouncils(app, dictCtx)
    _fnRegisterStartCouncil(app, dictCtx)
    _fnRegisterPollEvents(app, dictCtx)
    _fnRegisterRespond(app, dictCtx)
    _fnRegisterRequestStop(app, dictCtx)
    _fnRegisterExhaustedRoundExits(app, dictCtx)
    _fnRegisterAcceptPlan(app, dictCtx)
    _fnRegisterDeleteCouncil(app, dictCtx)
    _fnRegisterGetCouncil(app, dictCtx)
