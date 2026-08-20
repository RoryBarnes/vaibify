"""API routes for the global project registry."""

__all__ = [
    "AddProjectRequest",
    "CreateProjectRequest",
    "ConvertToContainerRequest",
    "PromoteHostProjectRequest",
    "ContainerSettingsRequest",
    "CreateHostDirectoryRequest",
    "fnRegisterRegistryRoutes",
    "flistQueryHostDirectory",
    "fbDirectoryHasConfig",
]

import asyncio
import logging
import math
import os
import re

from fastapi import HTTPException, Request
from pydantic import BaseModel
from typing import List, Optional

from vaibify.gui import buildRoutes
from vaibify.gui.routeContext import (
    fnRefuseContainerOnlyForHostProject,
    fnRefuseHostOnlyForContainerProject,
)
from vaibify.gui.routeScope import (
    S_CARRIER_LIFECYCLE_TRANSACTION,
    S_CARRIER_SEPARATE_AUTHORITY,
    ffnDeclareCarrierMode,
    fsLeaseFromRequest,
)

logger = logging.getLogger("vaibify")

_RE_FOLDER_NAME = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.\- ]*$")

# What marks a container as one of vaibify's. Deliberately a directory
# the container CARRIES, not a Docker label: a label is applied by one
# creation path, so keying on it silently unrecognizes every container
# made before that path existed or by any other route.
S_VAIBIFY_MARKER_DIRECTORY = "/workspace/.vaibify"

_I_MAXIMUM_CPU_LIMIT = 1024
_F_MINIMUM_MEMORY_LIMIT_GIGABYTES = 0.25
_F_MAXIMUM_MEMORY_LIMIT_GIGABYTES = 1024.0

_T_AGENT_SETTINGS = (
    ("claude", "bClaude", "bClaudeAutoUpdate", "Claude Code"),
    ("codex", "bCodex", "bCodexAutoUpdate", "Codex"),
    ("gemini", "bGemini", "bGeminiAutoUpdate", "Gemini CLI"),
    ("opencode", "bOpenCode", "bOpenCodeAutoUpdate", "OpenCode"),
    ("cline", "bCline", "bClineAutoUpdate", "Cline"),
    ("openhands", "bOpenHands", "bOpenHandsAutoUpdate", "OpenHands"),
    ("pi", "bPi", "bPiAutoUpdate", "Pi"),
)


class AddProjectRequest(BaseModel):
    """Body for ``POST /api/registry``.

    ``sMode`` decides which leg the connection router sends this
    project's work to for the rest of its life, so it is recorded at
    registration and never inferred later. Absent means container,
    which is what every entry written before host mode existed meant.
    """
    sDirectory: str
    sMode: str = "container"


class CreateProjectRequest(BaseModel):
    # A host project skips every container field below it -- there is
    # no image to build, no packages to install into one, and no
    # resource limits to set on one. They stay on the model with their
    # defaults rather than being split into a second request shape:
    # the create WIZARD is what hides them, and a second model would be
    # a second place for the two flows to drift apart.
    sMode: str = "container"
    sDirectory: str
    sProjectName: str
    sTemplateName: str
    sPythonVersion: str = "3.12"
    listRepositories: List[str] = []
    listFeatures: List[str] = []
    bUseGithubAuth: bool = True
    bNeverSleep: bool = False
    bNetworkIsolation: bool = False
    bClaudeAutoUpdate: bool = True
    bCodexAutoUpdate: bool = True
    bGeminiAutoUpdate: bool = True
    bOpenCodeAutoUpdate: bool = True
    bClineAutoUpdate: bool = True
    bOpenHandsAutoUpdate: bool = True
    bPiAutoUpdate: bool = True
    listSystemPackages: List[str] = []
    listPythonPackages: List[str] = []
    listCondaPackages: List[str] = []
    sPackageManager: str = "pip"
    sPipInstallFlags: str = ""
    sContainerUser: str = "researcher"
    sBaseImage: str = "ubuntu:24.04"
    sWorkspaceRoot: str = "/workspace"
    iCpuLimit: int = 0
    fMemoryLimitGigabytes: float = 0.0


class ConvertToContainerRequest(BaseModel):
    # The container-only subset of CreateProjectRequest. It deliberately
    # omits sDirectory, sTemplateName and sMode: the directory already
    # exists and is scaffolded (re-scaffolding would clobber the
    # researcher's files), and the target mode is always "container". A
    # separate model rather than a reused CreateProjectRequest, so a
    # field that does not apply to a conversion cannot arrive at all.
    sProjectName: str
    sPythonVersion: str = "3.12"
    listRepositories: List[str] = []
    listFeatures: List[str] = []
    bUseGithubAuth: bool = True
    bNeverSleep: bool = False
    bNetworkIsolation: bool = False
    bClaudeAutoUpdate: bool = True
    bCodexAutoUpdate: bool = True
    bGeminiAutoUpdate: bool = True
    bOpenCodeAutoUpdate: bool = True
    bClineAutoUpdate: bool = True
    bOpenHandsAutoUpdate: bool = True
    bPiAutoUpdate: bool = True
    listSystemPackages: List[str] = []
    listPythonPackages: List[str] = []
    listCondaPackages: List[str] = []
    sPackageManager: str = "pip"
    sPipInstallFlags: str = ""
    sContainerUser: str = "researcher"
    sBaseImage: str = "ubuntu:24.04"
    sWorkspaceRoot: str = "/workspace"
    iCpuLimit: int = 0
    fMemoryLimitGigabytes: float = 0.0


class PromoteHostProjectRequest(BaseModel):
    # Promotion to a host-based Project collects nothing but the name:
    # sMode stays "host", so there is no container to configure, no image
    # to build, and no resource limit to set. A separate model rather
    # than a reused ConvertToContainerRequest, so a container field that
    # does not apply to a host Project cannot arrive at all.
    sProjectName: str


class ContainerSettingsRequest(BaseModel):
    bNeverSleep: Optional[bool] = None
    bClaudeAutoUpdate: Optional[bool] = None
    bCodexAutoUpdate: Optional[bool] = None
    bGeminiAutoUpdate: Optional[bool] = None
    bOpenCodeAutoUpdate: Optional[bool] = None
    bClineAutoUpdate: Optional[bool] = None
    bOpenHandsAutoUpdate: Optional[bool] = None
    bPiAutoUpdate: Optional[bool] = None
    iCpuLimit: Optional[int] = None
    fMemoryLimitGigabytes: Optional[float] = None


class CreateHostDirectoryRequest(BaseModel):
    sParentPath: str
    sFolderName: str


def fnRegisterRegistryRoutes(app, dictCtx):
    """Register all registry and container lifecycle routes."""
    _fnRegisterGetRegistry(app, dictCtx)
    _fnRegisterAddProject(app, dictCtx)
    _fnRegisterRemoveProject(app, dictCtx)
    buildRoutes.fnRegisterAll(app, dictCtx)
    _fnRegisterStartContainer(app, dictCtx)
    _fnRegisterStopContainer(app, dictCtx)
    _fnRegisterContainerSettings(app, dictCtx)
    _fnRegisterHostDirectories(app, dictCtx)
    _fnRegisterGetTemplates(app, dictCtx)
    _fnRegisterGetTemplateConfig(app, dictCtx)
    _fnRegisterCreateProject(app, dictCtx)
    _fnRegisterConvertToContainer(app, dictCtx)
    _fnRegisterPromoteToHostProject(app, dictCtx)
    _fnRegisterCreateHostDirectory(app, dictCtx)
    _fnRegisterClaimContainer(app, dictCtx)
    _fnRegisterReleaseContainer(app, dictCtx)
    _fnRegisterQuarantineDetail(app, dictCtx)
    _fnRegisterReconcileQuarantine(app, dictCtx)


def _fnRegisterGetRegistry(app, dictCtx):
    """Register GET /api/registry — list all projects with status.

    Merges registered projects with auto-discovered running
    containers so the landing page shows everything.
    """

    @app.get("/api/registry")
    async def fdictGetRegistry(request: Request):
        from vaibify.config.registryManager import (
            flistGetAllProjectsWithStatus,
        )
        # The caller's lease rides the X-Vaibify-Lease header (never a query
        # param, which would leak into logs); it is used only to grey tiles
        # another session holds, never as authorization.
        sLeaseId = fsLeaseFromRequest(request)
        _fnReapStaleContainerClaims(dictCtx)
        listRegistered = flistGetAllProjectsWithStatus()
        listVaibify, listUnrecognized = (
            _ftDiscoverAllContainers(dictCtx)
        )
        listContainers = _flistMergeProjectsAndContainers(
            listRegistered, listVaibify,
        )
        _fnAnnotateLockState(listContainers, dictCtx)
        _fnAnnotateOwnershipState(
            listContainers, app.state.dictContainerOwners, sLeaseId,
        )
        return {
            "listContainers": listContainers,
            "listUnrecognized": listUnrecognized,
        }


def _fnReapStaleContainerClaims(dictCtx):
    """Reap claims whose holder PID is dead before listing containers.

    Runs on every GET /api/registry (the container-picker refresh
    path) so a claim orphaned by a killed vaibify server is released
    without restarting the hub. Also runs the operation journal's
    automatic probe tier (with the live Docker connection, so exec and
    start records can be verified rather than failing closed).
    """
    from vaibify.config.containerLock import fnReapStaleContainerLocks
    fnReapStaleContainerLocks(connectionDocker=dictCtx.get("docker"))


def _fnAnnotateLockState(listContainers, dictCtx):
    """Populate bLocked / iLockedBy* / journal fields on each container."""
    from vaibify.config.containerLock import fdictReadLockHolder
    for dictContainer in listContainers:
        sName = dictContainer.get("sName")
        if not sName:
            dictContainer["bLocked"] = False
            continue
        dictHolder = fdictReadLockHolder(sName)
        if dictHolder:
            dictContainer["bLocked"] = True
            dictContainer["iLockedByPid"] = dictHolder.get("iPid")
            dictContainer["iLockedByPort"] = dictHolder.get("iPort")
        else:
            dictContainer["bLocked"] = False
        _fnAnnotateJournalState(dictContainer, sName, dictCtx)


def _fnAnnotateJournalState(dictContainer, sName, dictCtx):
    """Surface an unsettled operation journal on the registry listing.

    A quarantined container must never render as available: the tile
    is marked ``bQuarantined`` with ``sJournalState`` QUARANTINED, and
    ``bLocked`` is forced True so every existing consumer of the
    listing also refuses it. The resolution here is read-only — the
    probe-and-persist pass already ran in the reaper on this request.
    """
    from vaibify.config import operationJournal
    from vaibify.config.containerLock import fbIsValidProjectName
    if not fbIsValidProjectName(sName):
        return
    dictResolution = operationJournal.fdictResolveContainerJournal(
        sName, dictCtx.get("docker"), bPersistResolution=False,
    )
    sResolution = dictResolution["sResolution"]
    dictContainer["sJournalState"] = sResolution
    dictContainer["bQuarantined"] = (
        sResolution == operationJournal.S_RESOLUTION_QUARANTINED
    )
    if sResolution != operationJournal.S_RESOLUTION_SETTLED:
        dictContainer["bLocked"] = True


def _fnAnnotateOwnershipState(
    listContainers, dictContainerOwners, sCallerLease,
):
    """Flag containers owned by a different browser session on this hub.

    ``bOwnedByOtherSession`` is True when an in-process owner record
    exists whose lease differs from the caller's, so the picker can grey
    a tile that another tab already holds. The owner's lease is never
    echoed; only the boolean leaves the process.
    """
    for dictContainer in listContainers:
        recordOwner = dictContainerOwners.get(dictContainer.get("sName"))
        dictContainer["bOwnedByOtherSession"] = bool(
            recordOwner is not None
            and recordOwner.sLeaseId != sCallerLease
        )
        # Honest surfacing of a force-abandoned (poisoned) owner: the
        # journal annotation already renders the durable quarantine
        # mirror; this adds the live in-process axis (design §2.1).
        dictContainer["bPoisoned"] = bool(
            recordOwner is not None
            and getattr(recordOwner, "poison", None) is not None
        )


def _fnRejectInvalidProjectName(sName):
    """Raise HTTP 400 when sName is unsafe for lock operations."""
    from vaibify.config.containerLock import fbIsValidProjectName
    if not fbIsValidProjectName(sName):
        raise HTTPException(
            status_code=400,
            detail=f"Invalid container name: {sName!r}",
        )


def _fnRegisterQuarantineDetail(app, dictCtx):
    """Register GET /api/registry/{sName}/quarantine.

    The registry listing already flags ``bQuarantined``; this surfaces
    the WHY, so a researcher whose tile is refused does not have to
    reach an agent to learn there are unsettled operations (e.g. a
    terminal whose process group could not be proven empty) holding the
    container. Read-only and allowlisted: it echoes only the fields
    ``flistDescribeJournalRecords`` already exposes to the CLI, plus the
    exact host command that clears the quarantine.
    """
    from vaibify.config import operationJournal
    from vaibify.config.reconciliation import (
        ReconciliationRefusedError,
        flistDescribeJournalRecords,
    )

    @app.get("/api/registry/{sName}/quarantine")
    async def fdictExplainQuarantine(sName: str):
        from vaibify.config.registryManager import fbIsHostProject
        _fnRejectInvalidProjectName(sName)
        dictResolution = operationJournal.fdictResolveContainerJournal(
            sName, dictCtx.get("docker"), bPersistResolution=False,
        )
        sResolution = dictResolution["sResolution"]
        try:
            listRecords = flistDescribeJournalRecords(sName)
            sReadState = "valid"
        except ReconciliationRefusedError as errorRefused:
            listRecords = []
            sReadState = errorRefused.sReadState or "malformed"
        return {
            "sName": sName,
            "sJournalState": sResolution,
            "bQuarantined": (
                sResolution == operationJournal.S_RESOLUTION_QUARANTINED
            ),
            "sReadState": sReadState,
            "listRecords": listRecords,
            "sRemedy": f"vaibify reconcile {sName}",
            # A VALID journal with records can be reconciled from the
            # modal; a malformed one cannot — its exits (break-glass,
            # abandon) are destructive judgements that stay on the
            # host CLI. The host flag decides whether the modal may
            # offer the container-only stop-and-certify escalation.
            "bReconcilableHere": (
                sReadState == "valid" and bool(listRecords)
            ),
            "bHostProject": fbIsHostProject(sName),
        }


class ReconcileQuarantineRequest(BaseModel):
    """Body for ``POST /api/registry/{sName}/reconcile``.

    The expected ids are the concurrency guard the CLI carries too: the
    modal reconciles the records it SHOWED, so a record that appeared
    after the researcher read the explanation is never cleared blind.
    """

    listExpectedOperationIds: List[str]


def _fnRegisterReconcileQuarantine(app, dictCtx):
    """Register POST /api/registry/{sName}/reconcile.

    The dashboard face of ``vaibify reconcile``, restricted to what a
    reconcile can do NON-destructively: prove every shown record
    settled and clear the quarantine, or refuse with the reason. The
    proving transaction is the same one the CLI reaches — the held-hub
    core when this hub holds the container's flock, the crash-time
    transaction when nothing does — so the two lanes cannot diverge.
    The destructive exits (break-glass on a malformed journal,
    force-abandon, abandon-host-journal) deliberately stay on the host
    CLI: those are trust judgements about possibly-corrupt state, and
    design §8's reason for keeping them a deliberate host-side act is
    untouched by making the PROVE reachable from the modal.
    """
    from vaibify.config import reconciliation
    from vaibify.gui import hostControlChannel

    @app.post("/api/registry/{sName}/reconcile")
    @ffnDeclareCarrierMode(S_CARRIER_SEPARATE_AUTHORITY)
    async def fdictReconcileQuarantine(
        sName: str, request: ReconcileQuarantineRequest,
    ):
        _fnRejectInvalidProjectName(sName)
        listExpectedIds = [
            sId for sId in request.listExpectedOperationIds
            if isinstance(sId, str) and sId
        ]
        if not listExpectedIds:
            raise HTTPException(
                400, "listExpectedOperationIds must name the journal "
                "record(s) being reconciled",
            )
        if hostControlChannel.fbHubHoldsContainerFlockForName(
            app.state, sName,
        ):
            dictOutcome = await hostControlChannel.fdictReconcileHeldContainer(
                app, dictCtx, sName, listExpectedIds,
            )
            if not dictOutcome.get("bAccepted"):
                raise HTTPException(
                    409, dictOutcome.get("sError", "reconciliation refused"),
                )
            return {
                "bReconciled": True,
                "listRecordNotes": dictOutcome.get("listRecordNotes", []),
            }
        try:
            dictProven = await asyncio.to_thread(
                reconciliation.fdictReconcileCrashTimeJournal,
                sName, dictCtx.get("docker"), set(listExpectedIds),
            )
        except reconciliation.ReconciliationRefusedError as errorRefused:
            raise HTTPException(409, str(errorRefused))
        return {
            "bReconciled": True,
            "listRecordNotes": dictProven["listRecordNotes"],
        }


def _fnRegisterClaimContainer(app, dictCtx):
    """Register POST /api/registry/{sName}/claim."""

    @app.post("/api/registry/{sName}/claim")
    async def fdictClaimContainer(request: Request, sName: str):
        from vaibify.gui import browserSession, sessionLifecycle
        _fnRejectInvalidProjectName(sName)
        # The re-claim lease rides the X-Vaibify-Lease header so a reloaded
        # tab re-asserting its sessionStorage lease is idempotent without the
        # lease ever appearing in a query string.
        sLeaseId = fsLeaseFromRequest(request)
        iPort = getattr(app.state, "iHubPort", 0)
        sContainerId = _fsResolveContainerId(dictCtx, sName)
        # Bind the lease to the claiming browser session. A shared-token
        # (transitional) request resolves to '' and records no binding.
        sBrowserSessionId = browserSession.fsSessionIdForCredential(
            getattr(app.state, "dictBrowserSessions", {}),
            request.headers.get("x-session-token", ""),
        )
        # The commit goes through the sessionLifecycle authority (never
        # containerOwnership.ftClaim directly), whose canonical lock
        # order makes the one-container-per-session read-check-write
        # atomic across concurrent claims on different containers.
        iStatusCode, dictPayload = (
            await sessionLifecycle.ftClaimWithCardinality(
                app.state, sName, sLeaseId, iPort,
                sContainerId=sContainerId,
                fbPipelineRunning=lambda sOwned: _fbNameHasRunningPipeline(
                    dictCtx, app.state, sOwned,
                ),
                sBrowserSessionId=sBrowserSessionId,
                connectionDocker=dictCtx.get("docker"),
            )
        )
        if iStatusCode != 200:
            raise HTTPException(status_code=iStatusCode, detail=dictPayload)
        return dictPayload


def _fsResolveContainerId(dictCtx, sName):
    """Return the running Docker id for a project name, or '' when absent.

    Stored on the owner record at claim time so the per-container agent
    token can be scoped to this exact container without a Docker call on
    every request. A host project's resource id IS its registry name
    (host-mode plan §9), so the Docker query is skipped entirely — a
    claim on a host project must succeed with no daemon at all.
    """
    from vaibify.config.registryManager import fbIsHostProject
    if fbIsHostProject(sName):
        return sName
    connectionDocker = dictCtx.get("docker")
    from vaibify.config.connectionAvailability import (
        fbDockerReachable,
    )
    if not fbDockerReachable(connectionDocker):
        return ""
    try:
        for dictRow in connectionDocker.flistGetRunningContainers():
            if dictRow.get("sName") == sName:
                return dictRow.get("sContainerId", "")
    except Exception:
        return ""
    return ""


def _fbNameHasRunningPipeline(dictCtx, appState, sName):
    """Return True when an owned resource's pipeline is mid-run.

    Used by the claim arbiter's take-over veto so a foreign claim never
    evicts an owner whose run is still live. A host project is asked
    through the host busy oracle (durable task + journaled process
    group) — its truth is never in Docker. A Docker outage fails safe
    to busy (``True``), keeping the existing owner in place.
    """
    from vaibify.config.registryManager import fbIsHostProject
    if fbIsHostProject(sName):
        from .hostBusyOracle import fbHostProjectHasLiveRun
        return fbHostProjectHasLiveRun(appState, sName)
    connectionDocker = dictCtx.get("docker")
    from vaibify.config.connectionAvailability import (
        fbDockerReachable,
    )
    if not fbDockerReachable(connectionDocker):
        return False
    try:
        from .fileStatusManager import _fbPipelineIsRunning
        for dictRow in connectionDocker.flistGetRunningContainers():
            if dictRow.get("sName") == sName:
                return _fbPipelineIsRunning(
                    dictCtx, dictRow.get("sContainerId", ""),
                )
    except Exception:
        return True
    return False


def _fnRegisterReleaseContainer(app, dictCtx):
    """Register POST /api/registry/{sName}/release.

    A BUSY refusal answers 409 with the record retained (design §10),
    not a 200 carrying ``bReleased: false``: the tab must keep its
    lease, because it still owns the container. The optional
    ``bForce`` body field overrides the agent-liveness refusal only —
    the lifecycle authority refuses a live run either way.
    """
    del dictCtx

    @app.post("/api/registry/{sName}/release")
    async def fdictReleaseContainer(request: Request, sName: str):
        from fastapi.responses import JSONResponse
        from vaibify.gui import browserSession, sessionLifecycle
        _fnRejectInvalidProjectName(sName)
        # The owning lease rides the X-Vaibify-Lease header; release only
        # succeeds when the presenting browser session is the one bound to
        # that lease, so a second tab that copied the lease value cannot
        # drop the true owner's record. The commit goes through the
        # sessionLifecycle authority, never the ownership primitive.
        sLeaseId = fsLeaseFromRequest(request)
        sBrowserSessionId = browserSession.fsSessionIdForCredential(
            getattr(app.state, "dictBrowserSessions", {}),
            request.headers.get("x-session-token", ""),
        )
        sOutcome, dictPayload = await sessionLifecycle.ftReleaseExplicit(
            app.state, sName, sLeaseId,
            sBrowserSessionId=sBrowserSessionId,
            bForce=await _fbReadForceFlag(request),
        )
        dictBody = dict(dictPayload, sName=sName, bReleased=(
            sOutcome == sessionLifecycle.S_RELEASE_RELEASED
        ))
        if sOutcome == sessionLifecycle.S_RELEASE_BUSY:
            # The refusal reason rides `detail`, the shape the client's
            # error extractor reads — the same one the claim 409 uses.
            return JSONResponse(status_code=409, content=dict(
                dictBody, detail={"sMessage": dictPayload["sMessage"]},
            ))
        return dictBody


async def _fbReadForceFlag(request):
    """Return the request's ``bForce`` flag, tolerating no body at all.

    A release may legitimately arrive with no body at all, so a missing
    or malformed one must read as "not forced" rather than fail the
    release. Failing CLOSED is the point: an unreadable body can never
    become a force, and force overrides ONLY the agent-liveness refusal
    -- a live run, a live guarded mutation, and a poisoned record all
    still refuse.
    """
    try:
        dictBody = await request.json()
    except Exception:  # noqa: BLE001 — an absent body is simply not forced
        return False
    return bool((dictBody or {}).get("bForce"))


def _fnRegisterAddProject(app, dictCtx):
    """Register POST /api/registry — add a project directory."""

    @app.post("/api/registry")
    async def fdictAddProject(request: AddProjectRequest):
        from vaibify.config.registryManager import (
            fnAddProject, fdictGetProject,
        )
        try:
            fnAddProject(request.sDirectory, sMode=request.sMode)
        except FileNotFoundError as error:
            raise HTTPException(404, str(error))
        except ValueError as error:
            # Also the unknown-mode refusal: fnAddProject validates the
            # vocabulary, so a typo in sMode is a 409 naming it rather
            # than a project silently registered as a container.
            raise HTTPException(409, str(error))
        sName = _fsProjectNameForDirectory(request.sDirectory)
        return fdictGetProject(sName)


def _fsProjectNameForDirectory(sDirectory):
    """Load config from directory and return project name."""
    from vaibify.config.registryManager import (
        fsDiscoverConfigInDirectory,
    )
    from vaibify.cli.configLoader import fconfigLoadFromPath
    sConfigPath = fsDiscoverConfigInDirectory(sDirectory)
    configProject = fconfigLoadFromPath(sConfigPath)
    return configProject.sProjectName


def _fnRegisterRemoveProject(app, dictCtx):
    """Register DELETE /api/registry/{sName}."""

    @app.delete("/api/registry/{sName}")
    async def fdictRemoveProject(sName: str):
        from vaibify.config.registryManager import (
            fnRemoveProject,
        )
        try:
            fnRemoveProject(sName)
        except KeyError as error:
            raise HTTPException(404, str(error))
        return {"bSuccess": True}


class StartContainerRequest(BaseModel):
    """The optional body of a start request.

    ``sAcknowledgeReservationId`` names the FAILED start whose outcome
    the client has actually read. It is the explicit new-attempt action
    of design §10b: a client that never polled cannot name it, so a
    silent automatic retry cannot clear a failure the researcher never
    saw.
    """

    sAcknowledgeReservationId: str = ""


def _fnRegisterStartContainer(app, dictCtx):
    """Register POST /api/containers/{sName}/start and its status poll.

    Start is no longer request-scoped (design §10b). It arbitrates
    ownership, mints a server-owned reservation under the host flock and
    the cardinality lock, and answers ``202`` with a status-poll
    location — never a lease, because nothing is running yet to
    authorize. The outcome is delivered ONLY by the poll.
    """

    @app.post("/api/containers/{sName}/start")
    async def fresponseStartContainer(
        request: Request, sName: str,
        requestStart: Optional[StartContainerRequest] = None,
    ):
        from fastapi.responses import JSONResponse
        from vaibify.gui import startReservation
        # Ahead of the daemon check: a host-only hub has no daemon, and
        # "Docker is unavailable" is the wrong answer to give about a
        # project that has no container by design.
        fnRefuseContainerOnlyForHostProject(sName, "Starting a container")
        dictCtx["require"]()
        _fnRejectInvalidProjectName(sName)
        dictProject = _fdictRequireProject(sName)
        iStatusCode, dictBody = await startReservation.ftBeginStart(
            app.state, sName, _fsBrowserSessionFor(app, request),
            _fconfigLoadForProject(dictProject),
            getattr(app.state, "iHubPort", 0),
            connectionDocker=dictCtx.get("docker"),
            sAcknowledgeReservationId=(
                requestStart.sAcknowledgeReservationId
                if requestStart else ""
            ),
        )
        if iStatusCode == 409:
            return JSONResponse(status_code=409, content=dict(
                dictBody, detail={"sMessage": dictBody.get("sMessage", "")},
            ))
        return JSONResponse(status_code=iStatusCode, content=dictBody)

    # lifecycle-transaction: unlike the stop below, this one HAS the
    # authority the declaration names, and it is not this handler's.
    # `startReservation.ftCancelStart` holds the reservation lock across
    # the authorize-and-flag step, marks the START record
    # CANCEL_REQUESTED rather than writing a record of its own, and a
    # transfer arriving mid-cancel ADOPTS the reservation. The handler
    # is a pass-through to that transaction and must not open a second
    # one: minting a container admission here would mean two authorities
    # over one cancel, which is worse than none.
    @app.post("/api/containers/{sName}/start/cancel")
    @ffnDeclareCarrierMode(S_CARRIER_LIFECYCLE_TRANSACTION)
    async def fresponseHandleCancelStartContainer(
        request: Request, sName: str, sReservationId: str = "",
    ):
        from fastapi.responses import JSONResponse
        from vaibify.gui import startReservation
        fnRefuseContainerOnlyForHostProject(
            sName, "Cancelling a container start",
        )
        _fnRejectInvalidProjectName(sName)
        iStatusCode, dictBody = await startReservation.ftCancelStart(
            app.state, sName, _fsBrowserSessionFor(app, request),
            sReservationId=sReservationId,
        )
        if iStatusCode != 200:
            return JSONResponse(status_code=iStatusCode, content=dict(
                dictBody, detail={"sMessage": dictBody.get("sMessage", "")},
            ))
        return dictBody

    @app.get("/api/containers/{sName}/start-status")
    async def fresponseHandleGetStartStatus(request: Request, sName: str):
        from fastapi.responses import JSONResponse
        from vaibify.gui import startReservation
        _fnRejectInvalidProjectName(sName)
        iStatusCode, dictBody = startReservation.ftPollStartStatus(
            app.state, sName, _fsBrowserSessionFor(app, request),
        )
        if iStatusCode != 200:
            return JSONResponse(status_code=iStatusCode, content=dict(
                dictBody, detail={"sMessage": dictBody.get("sMessage", "")},
            ))
        return dictBody


def _fsBrowserSessionFor(app, request):
    """Resolve the requesting browser session id, or '' when unknown."""
    from vaibify.gui import browserSession
    return browserSession.fsSessionIdForCredential(
        getattr(app.state, "dictBrowserSessions", {}),
        request.headers.get("x-session-token", ""),
    )


def _fconfigLoadForProject(dictProject):
    """Load the validated project config the start will launch from."""
    from vaibify.cli.configLoader import fconfigLoadFromPath
    return fconfigLoadFromPath(dictProject["sConfigPath"])


def _fnRegisterStopContainer(app, dictCtx):
    """Register POST /api/containers/{sName}/stop."""

    # lifecycle-transaction, and the audit behind it is a FINDING rather
    # than a label. As measured: this route holds NO container mutation
    # lock, writes NO journal record of its own, and a hand-over
    # arriving mid-stop does not see it.
    #
    # The authority is real but it is not the carrier's. The stop runs
    # through `containerManager`, the lifecycle gateway, which contains
    # no `mutationAdmission` call anywhere -- so there is no gated
    # primitive on this path for a carrier to admit, and adding one
    # would declare an authority that never engages. That is what
    # `lifecycle-transaction` means here: the gateway is answerable for
    # the container's existence, and the commit carrier is answerable
    # for writes INTO a container that exists.
    #
    # Two consequences, stated rather than hidden. `container-lifecycle`
    # is unioned into `_SET_AUTHORIZED_CONTAINER_SCOPES` WITHOUT joining
    # `_SET_LEASE_ENFORCED_SCOPES`, so a stop stays answerable for a
    # container nobody owns -- which is the recovery path for a stuck
    # container and must not be traded away for a bookkeeping entry. And
    # a stop CAN still interleave with an in-flight guarded write; the
    # write's supervisor will find the container gone. Closing that
    # needs a force-stop path for the unowned case, which is a product
    # decision, not a migration one.
    #
    # An earlier version of this comment claimed a carrier here would
    # refuse the unowned stop. That was wrong -- it named the lease as
    # the obstacle when the real answer is that nothing on this path is
    # gated at all. The test written to prove it passed trivially and
    # was removed rather than kept.
    @app.post("/api/containers/{sName}/stop")
    @ffnDeclareCarrierMode(S_CARRIER_LIFECYCLE_TRANSACTION)
    async def fdictStopContainer(sName: str):
        fnRefuseContainerOnlyForHostProject(sName, "Stopping a container")
        dictCtx["require"]()
        dictProject = _fdictRequireProject(sName)
        sContainerName = dictProject["sContainerName"]
        try:
            await asyncio.to_thread(
                _fnExecuteStop, sContainerName,
            )
        except Exception as error:
            logger.error("Stop failed for %s: %s", sName, error)
            raise HTTPException(500, f"Stop failed: {error}")
        return {"bSuccess": True}


def _fnExecuteStop(sContainerName):
    """Stop and remove a running container (idempotent).

    The stop goes through ``containerManager``, the lifecycle gateway,
    rather than through a ``docker stop`` this module assembles itself.
    The local copy this replaced was a verbatim duplicate of the gateway
    primitive's first half, so a route module held a raw container
    mutation for no reason but that nobody had reconciled the two --
    which is the shape R4 exists to forbid.

    ``fdictStopContainer`` stops AND removes, so the removal is called here
    only on the branch that does not stop.
    """
    from vaibify.docker import containerManager
    from vaibify.config.keepAliveManager import fnStopKeepAlive
    dictStatus = containerManager.fdictGetContainerStatus(sContainerName)
    if not dictStatus["bExists"]:
        fnStopKeepAlive(sContainerName)
        return
    if dictStatus["bRunning"]:
        containerManager.fnStopContainer(sContainerName)
    else:
        containerManager.fnRemoveStopped(sContainerName)
    fnStopKeepAlive(sContainerName)


def _fnRegisterContainerSettings(app, dictCtx):
    """Register GET and POST /api/containers/{sName}/settings."""

    @app.get("/api/containers/{sName}/settings")
    async def fdictGetContainerSettings(sName: str):
        dictProject = _fdictRequireProject(sName)
        from vaibify.config.projectConfig import fconfigLoadFromFile
        configProject = fconfigLoadFromFile(
            dictProject["sConfigPath"]
        )
        dictResult = {
            "bNeverSleep": configProject.bNeverSleep,
            "iCpuLimit": configProject.iCpuLimit,
            "fMemoryLimitGigabytes":
                configProject.fMemoryLimitGigabytes,
        }
        for _, sEnabledField, sAutoUpdateField, _ in _T_AGENT_SETTINGS:
            bInstalled = getattr(configProject.features, sEnabledField)
            dictResult[f"{sEnabledField}Installed"] = bInstalled
            if bInstalled:
                dictResult[sAutoUpdateField] = getattr(
                    configProject.features, sAutoUpdateField
                )
        return dictResult

    # separate-authority, not typed-read. Every write here lands in the
    # project's own `vaibify.yml` on the researcher's machine through
    # `_fnUpdateYamlBoolField` / `_fnUpdateYamlNumberField`; the route
    # opens no container connection at all. `typed-read` would be
    # literally true of its container reach and would still be the wrong
    # record, because a reader takes it to mean the route writes
    # nothing. What governs it is `_fdictRequireProject`, which binds
    # the name to a registered project and its config path, and
    # `_fnRequireValidResourceLimits`. Ruling 2026-08-05.
    @app.post("/api/containers/{sName}/settings")
    @ffnDeclareCarrierMode(S_CARRIER_SEPARATE_AUTHORITY)
    async def fdictSetContainerSettings(
        sName: str, request: ContainerSettingsRequest
    ):
        dictProject = _fdictRequireProject(sName)
        _fnRequireValidResourceLimits(
            request.iCpuLimit, request.fMemoryLimitGigabytes,
        )
        bRestartRequired = False
        if request.bNeverSleep is not None:
            _fnUpdateYamlBoolField(
                dictProject["sConfigPath"], "neverSleep",
                request.bNeverSleep,
            )
        for sAgent, _, sAutoUpdateField, _ in _T_AGENT_SETTINGS:
            bAutoUpdate = getattr(request, sAutoUpdateField)
            if bAutoUpdate is not None:
                bRestartRequired = _fbApplyAgentAutoUpdate(
                    dictProject["sConfigPath"], sAgent, bAutoUpdate,
                ) or bRestartRequired
        if request.iCpuLimit is not None:
            _fnUpdateYamlNumberField(
                dictProject["sConfigPath"], "cpuLimit",
                request.iCpuLimit,
            )
            bRestartRequired = True
        if request.fMemoryLimitGigabytes is not None:
            _fnUpdateYamlNumberField(
                dictProject["sConfigPath"], "memoryLimitGigabytes",
                request.fMemoryLimitGigabytes,
            )
            bRestartRequired = True
        return {
            "bSuccess": True,
            "bRestartRequired": bRestartRequired,
        }


def _fbApplyAgentAutoUpdate(sConfigPath, sAgent, bNewValue):
    """Apply an installed agent's update preference; return bChanged."""
    from vaibify.config.projectConfig import fconfigLoadFromFile
    configProject = fconfigLoadFromFile(sConfigPath)
    dictFields = {tFields[0]: tFields[1:] for tFields in _T_AGENT_SETTINGS}
    sEnabledField, sAutoUpdateField, sDisplayName = dictFields[sAgent]
    if not getattr(configProject.features, sEnabledField):
        raise HTTPException(
            409,
            f"{sDisplayName} is not installed in this project.",
        )
    if getattr(configProject.features, sAutoUpdateField) == bNewValue:
        return False
    _fnUpdateFeaturesBoolField(
        sConfigPath, f"{sAgent}AutoUpdate", bNewValue,
    )
    return True


def _fnUpdateFeaturesBoolField(sConfigPath, sKey, bValue):
    """Update a nested features.<key> bool in a YAML file."""
    import yaml
    with open(sConfigPath, "r") as fileHandle:
        dictConfig = yaml.safe_load(fileHandle) or {}
    dictConfig.setdefault("features", {})
    dictConfig["features"][sKey] = bValue
    with open(sConfigPath, "w") as fileHandle:
        yaml.safe_dump(
            dictConfig, fileHandle,
            default_flow_style=False, sort_keys=False,
        )


def _fnUpdateYamlBoolField(sConfigPath, sKey, bValue):
    """Update or append a top-level boolean key in a YAML file."""
    _fnUpdateYamlScalarField(
        sConfigPath, sKey, "true" if bValue else "false",
    )


def _fnUpdateYamlNumberField(sConfigPath, sKey, numberValue):
    """Update or append a top-level numeric key in a YAML file."""
    _fnUpdateYamlScalarField(sConfigPath, sKey, f"{numberValue:g}")


def _fnUpdateYamlScalarField(sConfigPath, sKey, sRenderedValue):
    """Rewrite one top-level ``key: value`` line, preserving the rest.

    Line-based on purpose: a YAML round-trip would drop the comments
    and ordering of a hand-edited vaibify.yml.
    """
    with open(sConfigPath, "r") as fileHandle:
        listLines = fileHandle.readlines()
    bFound = False
    for iIndex, sLine in enumerate(listLines):
        if sLine.startswith(f"{sKey}:") or sLine.startswith(
            f"{sKey} :"
        ):
            listLines[iIndex] = f"{sKey}: {sRenderedValue}\n"
            bFound = True
            break
    if not bFound:
        if listLines and not listLines[-1].endswith("\n"):
            listLines[-1] += "\n"
        listLines.append(f"{sKey}: {sRenderedValue}\n")
    with open(sConfigPath, "w") as fileHandle:
        fileHandle.writelines(listLines)


def _fnRequireValidResourceLimits(iCpuLimit, fMemoryLimitGigabytes):
    """400 when a requested CPU or memory cap is out of range.

    Mirrors projectConfig._fbValidateResourceLimits so a bad value is
    rejected at the API boundary instead of being written to
    vaibify.yml and failing every subsequent config load.
    """
    _fnRequireLimitWithinRange(
        "iCpuLimit", iCpuLimit, 1, _I_MAXIMUM_CPU_LIMIT,
    )
    _fnRequireLimitWithinRange(
        "fMemoryLimitGigabytes", fMemoryLimitGigabytes,
        _F_MINIMUM_MEMORY_LIMIT_GIGABYTES,
        _F_MAXIMUM_MEMORY_LIMIT_GIGABYTES,
    )


def _fnRequireLimitWithinRange(
    sFieldName, numberValue, numberMinimum, numberMaximum,
):
    """400 unless the limit is 0 (no limit) or finite and in range.

    ``_fnUpdateYamlNumberField`` renders with ``%g``, and PyYAML reads
    ``nan``, ``inf``, and any ``1e+06``-style rendering back as a
    *string* -- which fails every later load of that vaibify.yml,
    naming no field, until a human hand-edits it.
    """
    if numberValue is None or numberValue == 0:
        return
    if isinstance(numberValue, float) and not math.isfinite(
        numberValue
    ):
        raise HTTPException(
            400, f"{sFieldName} must be a finite number",
        )
    if not numberMinimum <= numberValue <= numberMaximum:
        raise HTTPException(
            400,
            f"{sFieldName} must be 0 (no limit) or between "
            f"{numberMinimum} and {numberMaximum}",
        )


def _fdictRequireProject(sName):
    """Look up a project in the registry or raise 404."""
    from vaibify.config.registryManager import fdictGetProject
    dictProject = fdictGetProject(sName)
    if dictProject is None:
        raise HTTPException(
            404, f"Project '{sName}' not found in registry"
        )
    return dictProject


def _ftDiscoverAllContainers(dictCtx):
    """Query Docker for all running containers.

    Returns
    -------
    tuple
        (listVaibify, listUnrecognized) where vaibify containers
        have ``.vaibify/`` inside and unrecognized do not.
    """
    connectionDocker = dictCtx.get("docker")
    from vaibify.config.connectionAvailability import (
        fbDockerReachable,
    )
    if not fbDockerReachable(connectionDocker):
        return [], []
    try:
        listContainers = connectionDocker.flistGetRunningContainers()
    except Exception:
        return [], []
    _fnSweepSiblingContainerCaches(dictCtx, listContainers)
    return _ftSplitContainers(connectionDocker, listContainers)


def _fnSweepSiblingContainerCaches(dictCtx, listContainers):
    """Evict the file-status and state-lock caches alongside docker's own.

    ``DockerConnection.flistGetRunningContainers`` already sweeps its
    instance + module caches; this hook lets the sibling caches
    (parent-mtime, pipeline-state lock dict) see the same snapshot in
    the same tick. Imported lazily so this route module stays
    light-weight when no GUI features are exercised.
    """
    try:
        from .fileStatusManager import fsetSweepAllContainerCaches
    except ImportError:
        return
    listIds = [d.get("sContainerId", "") for d in listContainers]
    fsetSweepAllContainerCaches(dictCtx, [s for s in listIds if s])


def _ftSplitContainers(connectionDocker, listContainers):
    """Split containers into vaibify and unrecognized lists."""
    listVaibify = []
    listUnrecognized = []
    for dictContainer in listContainers:
        if _fbIsVaibifyContainer(connectionDocker, dictContainer):
            listVaibify.append(
                _fdictContainerToProject(
                    connectionDocker, dictContainer,
                )
            )
        else:
            listUnrecognized.append(dictContainer)
    return listVaibify, listUnrecognized


def _fbIsVaibifyContainer(connectionDocker, dictContainer):
    """Return True if the container has a .vaibify directory.

    Through the TYPED READ, never an arbitrary exec. Arbitrary command
    execution is always treated as mutating -- the primitive cannot
    know whether the text it was handed reads a file or deletes a
    workspace -- so a `test -d` sent through
    ``ftResultExecuteCommand`` is refused on any enforced request lane,
    which this read-only listing is. The refusal then met the broad
    ``except`` below, and every container was quietly reclassified as
    unrecognized: a registered project got no ``sContainerId``, and its
    tile did nothing when clicked.

    A control-plane refusal is re-raised rather than swallowed. It
    means the read is not permitted HERE, which is an architectural
    fact about this call site, and answering "not a vaibify container"
    to it is the silent misclassification that hid this defect. An
    ordinary failure -- container gone mid-listing, daemon hiccup --
    still answers False, because that genuinely is not an answer of
    "yes".
    """
    from vaibify.config.mutationAdmission import ControlPlaneRefusalError
    try:
        listExists = connectionDocker.flistContainerPathsExist(
            dictContainer["sContainerId"], [S_VAIBIFY_MARKER_DIRECTORY],
        )
        return bool(listExists) and bool(listExists[0])
    except ControlPlaneRefusalError:
        raise
    except Exception:
        return False


def _fdictContainerToProject(connectionDocker, dictContainer):
    """Convert a Docker container dict to project-like dict."""
    return {
        "sName": dictContainer["sName"],
        "sDirectory": "",
        "sConfigPath": "",
        "sContainerName": dictContainer["sName"],
        "sContainerId": dictContainer["sContainerId"],
        "bImageExists": True,
        "bRunning": True,
        "sStatus": "running",
        "bDiscovered": True,
    }


def _flistMergeProjectsAndContainers(
    listRegistered, listDiscovered,
):
    """Merge registry entries with discovered containers.

    Discovered containers that match a registered project by
    name get their ``sContainerId`` added to the registry
    entry. Discovered containers with no registry match appear
    as separate entries.
    """
    setRegisteredNames = {
        dictProject["sContainerName"]
        for dictProject in listRegistered
    }
    for dictRegistered in listRegistered:
        _fnEnrichWithContainerId(dictRegistered, listDiscovered)
    listNew = [
        dictContainer for dictContainer in listDiscovered
        if dictContainer["sName"] not in setRegisteredNames
    ]
    return listRegistered + listNew


def _fnEnrichWithContainerId(dictRegistered, listDiscovered):
    """Add sContainerId to a registry entry if running."""
    sContainerName = dictRegistered["sContainerName"]
    for dictContainer in listDiscovered:
        if dictContainer["sName"] == sContainerName:
            dictRegistered["sContainerId"] = (
                dictContainer["sContainerId"]
            )
            return


def _fnRegisterHostDirectories(app, dictCtx):
    """Register GET /api/host-directories for browsing host dirs."""

    @app.get("/api/host-directories")
    async def fdictGetHostDirectories(
        sPath: Optional[str] = None,
        bIncludeFiles: bool = False,
    ):
        sAbsPath = sPath or os.path.expanduser("~")
        _fnValidateHostPath(sAbsPath)
        listEntries = flistQueryHostDirectory(
            sAbsPath, bIncludeFiles=bIncludeFiles,
        )
        return {
            "sCurrentPath": sAbsPath,
            "bHasConfig": fbDirectoryHasConfig(sAbsPath),
            "listEntries": listEntries,
        }


def _fnRegisterCreateHostDirectory(app, dictCtx):
    """Register POST /api/host-directories/create."""

    @app.post("/api/host-directories/create")
    async def fdictCreateHostDirectory(request: CreateHostDirectoryRequest):
        _fnValidateHostPath(request.sParentPath)
        _fnValidateFolderName(request.sFolderName)
        sNewPath = _fsCreateHostFolder(
            request.sParentPath, request.sFolderName,
        )
        return {"sNewPath": sNewPath}


def _fnValidateFolderName(sFolderName):
    """Raise HTTPException if folder name is unsafe or malformed."""
    sStripped = (sFolderName or "").strip()
    if not sStripped:
        raise HTTPException(400, "Folder name is required")
    if "/" in sStripped or "\\" in sStripped:
        raise HTTPException(400, "Folder name cannot contain slashes")
    if sStripped in (".", ".."):
        raise HTTPException(400, "Invalid folder name")
    if sStripped.startswith("."):
        raise HTTPException(400, "Folder name cannot start with a dot")
    if not _RE_FOLDER_NAME.match(sStripped):
        raise HTTPException(400, "Invalid folder name")


def _fsCreateHostFolder(sParentPath, sFolderName):
    """Create a new directory under sParentPath; return the new path."""
    sNewPath = os.path.join(sParentPath, sFolderName.strip())
    if os.path.exists(sNewPath):
        raise HTTPException(409, "Directory already exists")
    try:
        os.makedirs(sNewPath, exist_ok=False)
    except (PermissionError, OSError):
        raise HTTPException(403, "Cannot create directory")
    return sNewPath


def _fnValidateHostPath(sPath):
    """Raise HTTPException if the path is invalid or escapes home."""
    if not os.path.isabs(sPath):
        raise HTTPException(400, "Path must be absolute")
    sHome = os.path.expanduser("~")
    sResolved = os.path.realpath(sPath)
    if sResolved != sHome and not sResolved.startswith(sHome + os.sep):
        raise HTTPException(403, "Path is outside allowed root")
    if not os.path.isdir(sResolved):
        raise HTTPException(404, "Directory not found")


def flistQueryHostDirectory(sAbsPath, bIncludeFiles=False):
    """List subdirectories (and optionally files) on the host.

    Returns the same entry shape as the container directory
    listing (``sName``, ``sPath``, ``bIsDirectory``) plus
    ``bHasConfig`` indicating a vaibify project. File entries are
    included only when ``bIncludeFiles`` is set (import pickers);
    symlinked files are skipped the same way symlinked directories
    are.

    Parameters
    ----------
    sAbsPath : str
        Absolute path to list.
    bIncludeFiles : bool
        Include regular files alongside directories.

    Returns
    -------
    list
        Sorted list of directory entry dicts.
    """
    listEntries = []
    try:
        for entry in os.scandir(sAbsPath):
            if entry.is_dir(follow_symlinks=False):
                listEntries.append(_fdictBuildHostEntry(entry))
            elif bIncludeFiles and entry.is_file(follow_symlinks=False):
                listEntries.append(_fdictBuildHostFileEntry(entry))
    except PermissionError:
        raise HTTPException(403, "Permission denied")
    return _flistSortDirectoryEntries(listEntries)


def _fdictBuildHostEntry(entry):
    """Build a directory entry dict from an os.DirEntry."""
    return {
        "sName": entry.name,
        "sPath": entry.path,
        "bIsDirectory": True,
        "bHasConfig": fbDirectoryHasConfig(entry.path),
    }


def _fdictBuildHostFileEntry(entry):
    """Build a file entry dict for pickers that import host files."""
    return {
        "sName": entry.name,
        "sPath": entry.path,
        "bIsDirectory": False,
        "bHasConfig": False,
    }


def fbDirectoryHasConfig(sDirectoryPath):
    """Return True if the directory contains vaibify.yml."""
    sConfigPath = os.path.join(sDirectoryPath, "vaibify.yml")
    return os.path.isfile(sConfigPath)


def _flistSortDirectoryEntries(listEntries):
    """Sort entries: non-hidden alphabetically, then hidden."""
    listVisible = [
        e for e in listEntries if not e["sName"].startswith(".")
    ]
    listHidden = [
        e for e in listEntries if e["sName"].startswith(".")
    ]
    listVisible.sort(key=lambda e: e["sName"].lower())
    listHidden.sort(key=lambda e: e["sName"].lower())
    return listVisible + listHidden


def _fnRegisterGetTemplates(app, dictCtx):
    """Register GET /api/setup/templates."""

    @app.get("/api/setup/templates")
    async def fdictGetTemplates():
        from vaibify.config.templateManager import (
            flistAvailableTemplates,
        )
        try:
            listTemplates = flistAvailableTemplates()
        except FileNotFoundError as error:
            raise HTTPException(404, str(error))
        return {"listTemplates": listTemplates}


def _fnRegisterGetTemplateConfig(app, dictCtx):
    """Register GET /api/setup/templates/{sName}."""

    @app.get("/api/setup/templates/{sName}")
    async def fdictGetTemplateConfig(sName: str):
        from vaibify.config.templateManager import (
            fdictLoadTemplateConfig,
        )
        try:
            dictConfig = fdictLoadTemplateConfig(sName)
        except FileNotFoundError as error:
            raise HTTPException(404, str(error))
        return dictConfig


def _fnRegisterCreateProject(app, dictCtx):
    """Register POST /api/projects/create."""

    @app.post("/api/projects/create")
    async def fdictCreateProject(request: CreateProjectRequest):
        _fnValidateCreateDirectory(request.sDirectory)
        _fnRequireValidProjectName(request.sProjectName, request.sMode)
        _fnRequireValidResourceLimits(
            request.iCpuLimit, request.fMemoryLimitGigabytes,
        )
        _fnRejectUninstallablePackages(request.listCondaPackages)
        _fnRejectDuplicateProjectName(request.sProjectName)
        _fnScaffoldProject(request)
        _fnWriteProjectConfig(request)
        _fnRegisterNewProject(request.sDirectory, request.sMode)
        return {"bSuccess": True, "sDirectory": request.sDirectory}


def _fnRegisterConvertToContainer(app, dictCtx):
    """Register POST /api/registry/{sName}/convert-to-container.

    The one path that changes a registered project's mode after
    creation: a host sandbox becomes a containerized project under a new
    Docker-safe name. It rewrites the project's own vaibify.yml on the
    host and re-registers it in the host registry -- it opens NO
    container connection, because there is no container yet -- so it
    declares ``separate-authority`` and is governed by
    ``_fdictRequireProject`` plus the name/limits/package validators,
    the same disposition the container-settings POST carries. It does
    NOT build; it returns the build hand-off and the frontend kicks the
    already-audited build route.
    """

    # separate-authority: every write lands in the project's own
    # vaibify.yml and in the host registry; no container is touched.
    @app.post("/api/registry/{sName}/convert-to-container")
    @ffnDeclareCarrierMode(S_CARRIER_SEPARATE_AUTHORITY)
    async def fdictConvertToContainer(
        requestHttp: Request, sName: str,
        request: ConvertToContainerRequest,
    ):
        from vaibify.config import registryManager
        _fnRejectInvalidProjectName(sName)
        dictProject = _fdictRequireProject(sName)
        # Refuse a non-host project (409). This also makes the route
        # idempotent: a repeat call after a successful conversion sees a
        # container project and is refused here, never re-registered.
        fnRefuseHostOnlyForContainerProject(
            sName, "Converting to a container",
        )
        _fnRequireValidProjectName(request.sProjectName, "container")
        _fnRejectUninstallablePackages(request.listCondaPackages)
        _fnRequireValidResourceLimits(
            request.iCpuLimit, request.fMemoryLimitGigabytes,
        )
        # Duplicate-name check after the cheap local validators: it is
        # the one validator that reaches `docker ps`. It skips the
        # project being converted, so a host sandbox whose basename is
        # already Docker-safe may keep its name.
        _fnRejectDuplicateForConversion(request.sProjectName, sName)
        # Every validator runs BEFORE the caller's own session is
        # released: a refused name must never cost the researcher the
        # project view they are converting from.
        await _fnReleaseCallerOwnedSessionForConversion(
            app, sName, requestHttp,
        )
        _fnRefuseBusyProjectForConversion(app, sName, dictCtx)
        # Config file FIRST, registry entry SECOND. If the registry write
        # then fails, the config names a container but the entry is still
        # host, so re-running is safe; the reverse order would drift
        # sName from the config's projectName.
        _fnRewriteConfigForConversion(
            dictProject["sConfigPath"], request,
        )
        try:
            registryManager.fnConvertProjectToContainer(
                sName, request.sProjectName,
            )
        except KeyError as error:
            raise HTTPException(404, str(error))
        except ValueError as error:
            raise HTTPException(409, str(error))
        return _fdictConversionResult(request.sProjectName)


async def _fnReleaseCallerOwnedSessionForConversion(app, sName, requestHttp):
    """Release the caller's own open session on the project, if any.

    Conversion renames the lock/lease/journal key, so it must run only
    when nobody holds the project. Refusing the researcher's OWN open
    tab made promoting from inside the browser impossible -- the toast
    said "close it, then convert it", which only the command line could
    finish. Instead, when the presenting lease and browser session are
    the very ones holding the project, the session is released through
    the single lifecycle authority (terminals drained and proven,
    channels closed, flock freed) before the conversion proceeds; the
    frontend then re-enters under the new name. A caller presenting no
    lease, or a lease a DIFFERENT session bound, releases nothing and
    falls through to the busy refusal. A busy own session -- a live
    run, a live guarded mutation, a live in-container agent -- is
    refused with the lifecycle's own reason, session retained.
    """
    from vaibify.gui import browserSession, sessionLifecycle
    if not app.state.dictContainerOwners.get(sName):
        return
    sLeaseId = fsLeaseFromRequest(requestHttp)
    if not sLeaseId:
        return
    sBrowserSessionId = browserSession.fsSessionIdForCredential(
        getattr(app.state, "dictBrowserSessions", {}),
        requestHttp.headers.get("x-session-token", ""),
    )
    sOutcome, dictPayload = await sessionLifecycle.ftReleaseExplicit(
        app.state, sName, sLeaseId, sBrowserSessionId=sBrowserSessionId,
    )
    if sOutcome == sessionLifecycle.S_RELEASE_BUSY:
        raise HTTPException(409, detail={"sMessage": dictPayload["sMessage"]})


def _fnRefuseBusyProjectForConversion(app, sName, dictCtx):
    """409 when the project is open, locked, or has unsettled operations.

    Conversion renames the lock/lease/journal key, so it must run only
    when nobody holds the project. The caller's own open session has
    already been released by
    :func:`_fnReleaseCallerOwnedSessionForConversion`, so an owner
    record surviving to this check belongs to some OTHER session -- the
    refusal tells the researcher to close it there. All three axes are
    checked: the in-process owner map (this hub's live session), the
    host flock (another vaibify process), and the operation journal (an
    unsettled or quarantined operation on the current name).
    """
    from vaibify.config import operationJournal
    from vaibify.config.containerLock import fdictReadLockHolder
    if app.state.dictContainerOwners.get(sName):
        raise HTTPException(409, detail={"sMessage": (
            f"'{sName}' is open in a browser session right now. Close "
            "it, then convert it."
        )})
    if fdictReadLockHolder(sName):
        raise HTTPException(409, detail={"sMessage": (
            f"'{sName}' is in use by another vaibify session. Close it "
            "there, then convert it."
        )})
    dictResolution = operationJournal.fdictResolveContainerJournal(
        sName, dictCtx.get("docker"), bPersistResolution=False,
    )
    if dictResolution["sResolution"] != (
        operationJournal.S_RESOLUTION_SETTLED
    ):
        raise HTTPException(409, detail={"sMessage": (
            f"'{sName}' has operations that are not settled and cannot "
            "be converted until it is reconciled."
        )})


def _fnRejectDuplicateForConversion(sNewName, sOldName):
    """409 when the new container name collides with a DIFFERENT project.

    The entry being converted (``sOldName``) is skipped, so a host
    sandbox whose basename is already Docker-safe may keep its name --
    the writer replaces the entry in place. A Docker container already
    bearing the new name is still a hard collision: a host project has
    none, but a stale one from a prior life must not be silently reused.
    """
    from vaibify.config.registryManager import flistGetAllProjects
    for dictProject in flistGetAllProjects():
        if dictProject["sName"] == sOldName:
            continue
        if dictProject["sName"] == sNewName:
            raise HTTPException(
                409,
                f"A project named '{sNewName}' is already registered "
                f"at {dictProject['sDirectory']}",
            )
    if _fbDockerContainerExists(sNewName):
        raise HTTPException(
            409,
            f"A Docker container named '{sNewName}' already exists on "
            "this host",
        )


def _fnRewriteConfigForConversion(sConfigPath, request):
    """Overlay the request's container fields onto the host vaibify.yml.

    The merged config is validated before it is written, so an unsafe
    repository or package list can never strand a vaibify.yml that no
    later load can open. The request never carries a path -- the config
    path is the already-registered, home-rooted one -- so this adds no
    traversal vector.
    """
    from vaibify.config.projectConfig import (
        fbValidateConfig,
        fconfigFromYamlDict,
        fnSaveToFile,
    )
    dictMerged = _fdictOverlayContainerFieldsOntoHostConfig(
        sConfigPath, request,
    )
    if not fbValidateConfig(dictMerged):
        raise HTTPException(
            400,
            "The converted configuration is invalid; check the "
            "repositories and package lists.",
        )
    fnSaveToFile(fconfigFromYamlDict(dictMerged), sConfigPath)


def _fdictConversionResult(sNewName):
    """Return the converted registry entry plus the build hand-off."""
    from vaibify.config.registryManager import fdictGetProject
    dictProject = fdictGetProject(sNewName) or {}
    return dict(
        dictProject,
        bBuildRequired=True,
        sBuildPath=f"/api/containers/{sNewName}/build",
    )


def _fnRegisterPromoteToHostProject(app, dictCtx):
    """Register POST /api/registry/{sName}/promote-to-host-project.

    The host twin of the convert route: a host sandbox becomes a named
    host-based Project under a new host-safe name, while ``sMode`` STAYS
    ``"host"`` -- no container is configured and no image is built. It
    rewrites the project's own vaibify.yml on the host and re-registers
    it in the host registry; it opens NO container connection, so it
    declares ``separate-authority`` and is governed by
    ``_fdictRequireProject`` plus the storage-safe name and duplicate
    validators, the same disposition the convert route carries. It
    returns the promoted entry with ``bBuildRequired`` False -- there is
    no build hand-off.
    """

    # separate-authority: every write lands in the project's own
    # vaibify.yml and in the host registry; no container is touched.
    @app.post("/api/registry/{sName}/promote-to-host-project")
    @ffnDeclareCarrierMode(S_CARRIER_SEPARATE_AUTHORITY)
    async def fdictPromoteToHostProject(
        requestHttp: Request, sName: str,
        request: PromoteHostProjectRequest,
    ):
        from vaibify.config import registryManager
        _fnRejectInvalidProjectName(sName)
        dictProject = _fdictRequireProject(sName)
        # Refuse a container project (409): a container is already a
        # Project, and there is nothing to promote.
        fnRefuseHostOnlyForContainerProject(
            sName, "Promoting to a host Project",
        )
        # Idempotency: a host sandbox already promoted is refused (409)
        # rather than re-registered a second time.
        _fnRefuseAlreadyHostProject(dictProject)
        # Host-safe name: spaces allowed, path separators and control
        # chars forbidden. Docker-safety is NOT required -- the name
        # never becomes a Docker object in host mode.
        _fnRequireValidProjectName(request.sProjectName, "host")
        _fnRejectDuplicateForConversion(request.sProjectName, sName)
        # Every validator runs BEFORE the caller's own session is
        # released: a refused name must never cost the researcher the
        # project view they are promoting from.
        await _fnReleaseCallerOwnedSessionForConversion(
            app, sName, requestHttp,
        )
        _fnRefuseBusyProjectForConversion(app, sName, dictCtx)
        # Config file FIRST, registry entry SECOND (the convert route's
        # ordering rationale): a failed registry write then leaves a host
        # sandbox whose config names the new name but whose entry is
        # unchanged, and re-running is safe.
        _fnRewriteConfigForPromotion(
            dictProject["sConfigPath"], request.sProjectName,
        )
        try:
            registryManager.fnPromoteHostProject(
                sName, request.sProjectName,
            )
        except KeyError as error:
            raise HTTPException(404, str(error))
        except ValueError as error:
            raise HTTPException(409, str(error))
        return _fdictPromotionResult(request.sProjectName)


def _fnRefuseAlreadyHostProject(dictProject):
    """409 when the host project has already been promoted to a Project.

    Read from the same predicate the whole codebase uses; a container
    project is caught earlier by the host-only guard, so any entry
    reaching here is host mode and ``fbIsProject`` reflects its stored
    promotion flag.
    """
    from vaibify.config.registryManager import fbIsProject
    if fbIsProject(dictProject):
        raise HTTPException(409, detail={"sMessage": (
            f"'{dictProject['sName']}' is already a Project."
        )})


def _fnRewriteConfigForPromotion(sConfigPath, sNewName):
    """Set the host vaibify.yml's projectName to the promoted name.

    A host Project stays host mode, so promotion touches exactly one
    config field -- the name -- and preserves every other field the
    researcher's vaibify.yml carried by loading it and overlaying only
    ``projectName``. The merged config is validated before it is written,
    so an already-broken file cannot be stranded under a new name. The
    request never carries a path -- the config path is the
    already-registered, home-rooted one -- so this adds no traversal
    vector.
    """
    import yaml
    from vaibify.config.projectConfig import (
        fbValidateConfig,
        fconfigFromYamlDict,
        fnSaveToFile,
    )
    with open(sConfigPath, "r") as fileHandle:
        dictExisting = yaml.safe_load(fileHandle) or {}
    dictMerged = dict(dictExisting)
    dictMerged["projectName"] = sNewName
    if not fbValidateConfig(dictMerged):
        raise HTTPException(
            400,
            "The promoted configuration is invalid; the project name "
            "must be a valid host-safe name.",
        )
    fnSaveToFile(fconfigFromYamlDict(dictMerged), sConfigPath)


def _fdictPromotionResult(sNewName):
    """Return the promoted registry entry with no build hand-off."""
    from vaibify.config.registryManager import fdictGetProject
    dictProject = fdictGetProject(sNewName) or {}
    return dict(dictProject, bBuildRequired=False)


def _fnValidateCreateDirectory(sDirectory):
    """Validate directory path for project creation."""
    if not os.path.isabs(sDirectory):
        raise HTTPException(400, "Directory must be an absolute path")
    sHome = os.path.expanduser("~")
    sResolved = os.path.realpath(sDirectory)
    if sResolved != sHome and not sResolved.startswith(sHome + os.sep):
        raise HTTPException(403, "Path is outside allowed root")


def _fnRequireValidProjectName(sProjectName, sMode):
    """Reject a project name the config loader would later refuse.

    The create route used to persist vaibify.yml without checking the
    name, so a name the loader rejects stranded a file that could never
    be opened — the researcher saw only the generic "Configuration
    validation failed" on the next load, with no field named. Validate
    here, before anything is written, naming the rule. A host sandbox
    may carry spaces; a container project may not, because the name
    becomes its Docker container, image and volume name.
    """
    from vaibify.config.projectConfig import (
        fbIsDockerSafeName,
        fbIsStorageSafeName,
    )
    if not fbIsStorageSafeName(sProjectName):
        raise HTTPException(
            400,
            f"Project name '{sProjectName}' is not allowed. Use letters, "
            "digits, spaces, '.', '_' or '-', starting with a letter or "
            "digit, no leading or trailing space, at most 63 characters.",
        )
    if sMode != "host" and not fbIsDockerSafeName(sProjectName):
        raise HTTPException(
            400,
            f"Project name '{sProjectName}' cannot contain spaces in "
            "container mode: it becomes the Docker container, image and "
            "volume name. Remove the spaces, or create a host-mode "
            "sandbox, which allows them.",
        )


def _fnRejectUninstallablePackages(listCondaPackages):
    """Raise 400 for conda packages, which the image build ignores.

    The Dockerfile installs Miniforge for a non-pip package manager but
    never runs ``conda install``, and no build argument carries the
    list, so accepting the field created a container without the
    requested packages and said nothing. Refusing is the honest
    behaviour until that wiring exists.
    """
    if not listCondaPackages:
        return
    raise HTTPException(
        400,
        "condaPackages is not supported yet: the image build has no "
        "conda install step, so these packages would be recorded in "
        "vaibify.yml and never installed. List them under "
        "pythonPackages, or install them inside the container.",
    )


def _fnRejectDuplicateProjectName(sProjectName):
    """Raise 409 if project name conflicts with registry or Docker."""
    from vaibify.config.registryManager import flistGetAllProjects
    for dictProject in flistGetAllProjects():
        if dictProject["sName"] == sProjectName:
            raise HTTPException(
                409,
                f"A project named '{sProjectName}' is already "
                f"registered at {dictProject['sDirectory']}",
            )
    if _fbDockerContainerExists(sProjectName):
        raise HTTPException(
            409,
            f"A Docker container named '{sProjectName}' already "
            f"exists on this host",
        )


def _fbDockerContainerExists(sContainerName):
    """Return True if a Docker container with this name exists."""
    import subprocess
    try:
        processResult = subprocess.run(
            ["docker", "ps", "-a", "--format", "{{.Names}}",
             "--filter", f"name=^{sContainerName}$"],
            capture_output=True, text=True, timeout=5,
        )
        return sContainerName in processResult.stdout.split()
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


def _fnScaffoldProject(request):
    """Create directory and copy template files."""
    from vaibify.config.templateManager import fnCopyTemplate
    try:
        fnCopyTemplate(request.sTemplateName, request.sDirectory)
    except FileNotFoundError as error:
        raise HTTPException(404, str(error))


def _fnWriteProjectConfig(request):
    """Write vaibify.yml with project settings."""
    from vaibify.config.projectConfig import (
        fconfigFromYamlDict,
        fnSaveToFile,
    )
    sConfigPath = os.path.join(request.sDirectory, "vaibify.yml")
    dictYaml = _fdictBuildYamlFromRequest(request)
    configProject = fconfigFromYamlDict(dictYaml)
    fnSaveToFile(configProject, sConfigPath)


def _fdictBuildYamlFromRequest(request):
    """Translate a CreateProjectRequest into a camelCase YAML dict."""
    dictFeatures = _fdictFeaturesFromList(request.listFeatures)
    for sAgent, _, sAutoUpdateField, _ in _T_AGENT_SETTINGS:
        dictFeatures[f"{sAgent}AutoUpdate"] = getattr(
            request, sAutoUpdateField
        )
    dictYaml = {
        "projectName": request.sProjectName,
        "containerUser": request.sContainerUser,
        "pythonVersion": request.sPythonVersion,
        "baseImage": request.sBaseImage,
        "workspaceRoot": request.sWorkspaceRoot,
        "packageManager": request.sPackageManager,
        "repositories": _flistRepositoriesFromUrls(
            request.listRepositories
        ),
        "features": dictFeatures,
        "secrets": _flistSecretsFromAuthFlag(request.bUseGithubAuth),
        "neverSleep": request.bNeverSleep,
        "networkIsolation": request.bNetworkIsolation,
    }
    _fnAttachOptionalPackages(dictYaml, request)
    _fnAttachResourceLimits(dictYaml, request)
    return dictYaml


def _fnAttachResourceLimits(dictYaml, request):
    """Attach the CPU and memory caps when the wizard set them."""
    if request.iCpuLimit > 0:
        dictYaml["cpuLimit"] = request.iCpuLimit
    if request.fMemoryLimitGigabytes > 0:
        dictYaml["memoryLimitGigabytes"] = (
            request.fMemoryLimitGigabytes
        )


def _fnAttachOptionalPackages(dictYaml, request):
    """Attach package and pip-flag fields when present."""
    if request.listSystemPackages:
        dictYaml["systemPackages"] = list(request.listSystemPackages)
    if request.listPythonPackages:
        dictYaml["pythonPackages"] = list(request.listPythonPackages)
    if request.sPipInstallFlags:
        dictYaml["pipInstallFlags"] = request.sPipInstallFlags


def _fdictOverlayContainerFieldsOntoHostConfig(sConfigPath, request):
    """Return a host vaibify.yml overlaid with a request's container fields.

    Conversion rewrites an EXISTING config rather than scaffolding a new
    one, so it loads what the researcher's host project already carried
    and overlays the same container-field translation the create flow
    uses (``_fdictBuildYamlFromRequest``) onto it. Fields that
    translation does not manage — ``reproducibility``, ``bindMounts``,
    ``ports``, ``binaries`` and anything else the host config held — are
    absent from the overlay, so the loaded copy preserves them. The
    result is a plain camelCase YAML dict, ready for
    ``fconfigFromYamlDict`` / ``fbValidateConfig``.
    """
    import yaml
    with open(sConfigPath, "r") as fileHandle:
        dictExisting = yaml.safe_load(fileHandle) or {}
    dictMerged = dict(dictExisting)
    dictMerged.update(_fdictBuildYamlFromRequest(request))
    return dictMerged


_LIST_FEATURE_NAMES = [
    "jupyter", "rLanguage", "julia", "database",
    "dvc", "latex", "claude", "codex", "gemini", "opencode",
    "cline", "openhands", "pi", "gpu",
]


def _fdictFeaturesFromList(listFeatures):
    """Convert a feature-name list into the boolean YAML dict."""
    setEnabled = set(listFeatures or [])
    return {sName: sName in setEnabled
            for sName in _LIST_FEATURE_NAMES}


def _flistSecretsFromAuthFlag(bUseGithubAuth):
    """Return secret entries that match the GitHub auth toggle."""
    if not bUseGithubAuth:
        return []
    return [{"name": "gh_token", "method": "gh_auth"}]


def _flistRepositoriesFromUrls(listUrls):
    """Convert a list of git URL strings to vaibify.yml repository dicts."""
    listRepositories = []
    for sUrl in listUrls:
        listRepositories.append({
            "name": _fsRepositoryNameFromUrl(sUrl),
            "url": sUrl,
            "branch": "main",
            "installMethod": "pip_editable",
        })
    return listRepositories


def _fsRepositoryNameFromUrl(sUrl):
    """Extract a repository name from a git URL."""
    sName = sUrl.rstrip("/").rsplit("/", 1)[-1]
    if sName.endswith(".git"):
        sName = sName[:-4]
    return sName


def _fnRegisterNewProject(sDirectory, sMode="container"):
    """Register the newly created project in the requested mode."""
    from vaibify.config.registryManager import fnAddProject
    try:
        fnAddProject(sDirectory, sMode=sMode)
    except ValueError as error:
        raise HTTPException(409, str(error))
