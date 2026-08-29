"""The gates every Agent Council route passes through.

Two route modules serve the council — ``routes/councilRoutes.py`` (the
campaign lifecycle) and ``routes/councilChatRoutes.py`` (the
ask-the-chairbot conversation lane) — and both must resolve the same
principal, match the same campaign identity, pass the same credential
gate and map controller refusals onto HTTP identically. Route modules
may not import each other (sibling imports are banned by
``testRouteModulesDoNotImportSiblings``), so the shared guards live
here, one level up: one copy of each gate, two thin HTTP skins over it.

Nothing in this module registers a route or owns state. Every function
either reads app-owned state handed to it, or refuses with an
``HTTPException`` whose text names the remedy.
"""

__all__ = [
    "S_COUNCIL_CAPABILITY",
    "I_MAX_RESPONSE_LENGTH",
    "fdictCampaignStore",
    "fdictCouncilRegistry",
    "fdictControllerState",
    "flistTrackedDirectoryNames",
    "fsResolveDominantRepositoryPath",
    "fsRepositoryBoundToCampaign",
    "ftResolveCouncilPrincipal",
    "fjsonRequireCampaign",
    "fgenericSubmitMapped",
    "fnRefuseRunnerBackendUnlessEnabled",
    "fnRefuseStartWithoutAProjectLogin",
    "ffnBuildImageResolver",
    "ffnBuildCredentialStager",
]

import asyncio
import posixpath

from fastapi import HTTPException

from . import agentCouncilCampaign
from . import agentCouncilController
from . import agentCouncilRegistry
from . import agentCouncilStore
from .pipelineServer import (
    WORKSPACE_ROOT,
    fsContainerNameForId,
    fsValidatePathWithinRoot,
)
from .routeContext import (
    fnRefuseContainerOnlyForHostProject,
    fnRejectAgentTokenLane,
)

# The capability name the refusal reads back to the researcher, in their
# words (section 21). One constant so every route names it identically.
S_COUNCIL_CAPABILITY = "Convening a council"

# The bound shared by every free-text researcher answer: a gate
# response, a rejection reason, an ask-the-chairbot message.
I_MAX_RESPONSE_LENGTH = 20000


def fdictCampaignStore(requestHttp):
    """Return the app-owned campaign store from ``app.state``."""
    return getattr(
        requestHttp.app.state,
        agentCouncilStore.S_COUNCIL_CAMPAIGN_STORE_STATE_KEY,
    )


def fdictCouncilRegistry(requestHttp):
    """Return the app-owned council registry from ``app.state``."""
    return getattr(
        requestHttp.app.state,
        agentCouncilRegistry.S_COUNCIL_REGISTRY_STATE_KEY,
    )


def fdictControllerState(requestHttp):
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


def ftResolveCouncilPrincipal(dictCtx, requestHttp, sContainerId,
                              sChosenDirectory="", sCampaignId=""):
    """Guard the route and resolve the (resource name, project repo) pair.

    The canonical identity a campaign is bound to and matched against
    (remediation R2): the lease principal is the container NAME, and the
    repo is one validated project repo — a container can host several,
    and a campaign belongs to exactly one.

    ``sCampaignId`` is what makes that second half a LOOKUP rather than a
    guess where it would otherwise be a REFUSAL, and every campaign-scoped
    route passes it. A campaign records the repository it was convened
    against, so when the project cannot say which repo is meant, the
    campaign can: on a container tracking nine directories with no
    workflow open, "Accept and save plan" answered "a council needs to be
    told which one it is about" for a council that had deliberated about
    one of them for an hour (reported live 2026-08-29). Convene passes no
    campaign id, because at convene the directory genuinely is a choice.

    It is consulted AFTER the open workflow, not before, and that order
    is a contract rather than a preference: with a workflow open, the
    repository the researcher has open is the scope, and a campaign
    belonging to a sibling repository in the same container stays
    unreachable — ``testCouncilCampaignIdentity.py::
    test_second_repo_in_same_container_cannot_reach_campaign`` is the
    executable statement of that, and reordering these two branches
    fails it. So the record answers only the question the project
    leaves open.

    ``sChosenDirectory`` is accepted by EVERY campaign-scoped route,
    not only by start. The 2026-08-24 fix widened the READ routes and
    stopped there, so on a project tracking several directories with no
    workflow open a researcher could watch a council perfectly well and
    not answer it: respond, stop, accept, reject, delete and the
    ask-the-chairbot lane all still refused with "a council needs to be
    told which one it is about". Reported live on 2026-08-25 against the
    chat's open, which was simply the first of the ten anyone clicked.
    Fixing the instance is not fixing the class.

    It cannot re-point a campaign. The value is validated against the
    tracked set exactly as start validates it, and every one of these
    routes then matches the resolved principal against the STORED
    campaign — so a wrong directory answers 404, never a rebinding.

    The original reason the read routes needed it: a toolkit container
    tracks several repositories, so with no workflow open the resolver
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
    sBoundRepoPath = fsRepositoryBoundToCampaign(
        fdictCampaignStore(requestHttp), sContainerId, sCampaignId)
    if sBoundRepoPath:
        return sName, sBoundRepoPath
    return sName, fsResolveDominantRepositoryPath(
        dictCtx, sContainerId, sChosenDirectory)


def fsRepositoryBoundToCampaign(dictStore, sContainerId, sCampaignId):
    """Return the repo a STORED campaign records, or "" when it has none.

    The record's own answer, so a campaign-scoped action is never
    REFUSED a fact its campaign already carries. It does not override an
    open workflow — see ``ftResolveCouncilPrincipal`` for why that order
    is load-bearing — it answers where the project cannot.

    Empty is returned — never raised — for an unknown id and for a
    record predating the identity block, so those keep resolving exactly
    as they did: the caller falls through to the open workflow and then
    to the tracked-set derivation, and an unknown id then meets the same
    404 ``fjsonRequireCampaign`` has always given it. Refusing here
    instead would answer 409 for a mistyped id and leak that the
    resolution got further than an unknown campaign should.

    The recorded path is still VALIDATED before it is returned, for the
    same reason the chosen directory is: it becomes a container path.
    The guard differs because the input does — a query parameter is
    attacker-supplied and must name a tracked directory, whereas this
    was validated once at convene and may legitimately be a repo the
    researcher has since untracked, or a nested path an open workflow
    supplied. So the check is containment in the project root, which is
    the traversal guard itself and nothing weaker.
    """
    if not sCampaignId:
        return ""
    from . import projectRoots
    jsonCampaign = agentCouncilStore.fjsonGetCampaignRecord(
        dictStore, sCampaignId)
    sRecorded = ((jsonCampaign or {}).get("dictProjectIdentity")
                 or {}).get("sProjectRepoPath", "")
    if not sRecorded:
        return ""
    sRoot = posixpath.normpath(projectRoots.fsResolveProjectRoot(
        sContainerId, WORKSPACE_ROOT))
    sNormalized = fsValidatePathWithinRoot(sRecorded, sRoot)
    return "" if sNormalized == sRoot else sNormalized


def fsResolveDominantRepositoryPath(dictCtx, sContainerId,
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
    from . import projectRoots
    listTracked = flistTrackedDirectoryNames(dictCtx, sContainerId)
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


def flistTrackedDirectoryNames(dictCtx, sContainerId):
    """Return the basenames the tracked-repos sidecar records."""
    from . import trackedReposManager
    dictSidecar = trackedReposManager.fdictReadOrSeedSidecar(
        dictCtx["docker"], sContainerId)
    return [dictEntry.get("sName", "")
            for dictEntry in (dictSidecar or {}).get("listTracked", [])
            if dictEntry.get("sName")]


def fjsonRequireCampaign(dictStore, sCampaignId, sResourceName,
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


async def fgenericSubmitMapped(dictControllerState, sCampaignId,
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


def fnRefuseRunnerBackendUnlessEnabled(sImageIdentity):
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
    from . import agentCouncilCredentialGate
    dictEnablement = (
        agentCouncilCredentialGate.fdictEvaluateCredentialEnablement(
            "claude", sImageIdentity))
    if not dictEnablement["bEnabled"]:
        raise HTTPException(409, dictEnablement["sReason"])


def fnRefuseStartWithoutAProjectLogin(dictCtx, sContainerId,
                                      fTurnWallClockSeconds=0.0):
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
    from . import agentCouncilProviders
    from . import projectRoots
    sWorkspaceRoot = projectRoots.fsResolveProjectRoot(
        sContainerId, WORKSPACE_ROOT)
    sUnusable = agentCouncilProviders.fsExplainUnusableRunnerCredential(
        dictCtx["docker"], sContainerId,
        agentCouncilProviders.fsComposeCredentialContainerPath(
            sWorkspaceRoot),
        fTurnWallClockSeconds)
    if sUnusable:
        raise HTTPException(409, sUnusable)


def ffnBuildImageResolver(dictCtx, sContainerId):
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


def ffnBuildCredentialStager(dictCtx, sContainerId):
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
    from . import agentCouncilProviders
    from . import projectRoots

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
