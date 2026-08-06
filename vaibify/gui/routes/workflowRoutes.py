"""Workflow management route handlers."""

__all__ = ["fnRegisterAll"]

import json
import posixpath
import re

from fastapi import HTTPException, Request
from typing import Optional

from .. import browserSession
from .. import containerOwnership
from .. import workflowManager
from ..actionCatalog import fnAgentAction
from ..routeScope import fnRouteScope, fsLeaseFromRequest, S_SCOPE_OWNER_ESTABLISHING
from ..pipelineRunner import fsShellQuote
from ..pipelineServer import (
    CreateWorkflowRequest,
    RequestProjectCreationRequest,
    fdictHandleConnect,
    fsContainerNameForId,
    _fsSanitizeServerError,
)


_PATTERN_WORKFLOW_FILENAME = re.compile(
    r"^[A-Za-z0-9_][A-Za-z0-9_.-]*$"
)


def _fsValidateAndNormalizeFileName(sFileName):
    """Validate sFileName and return its normalized .json basename.

    Strips whitespace and a trailing ``.json`` so callers may pre-append
    it. Rejects empty, leading dot, slashes, ``..``, and anything
    outside ``[A-Za-z0-9_.-]``. Length-capped at 200 pre-suffix.
    """
    sClean = sFileName.strip()
    if sClean.endswith(".json"):
        sClean = sClean[:-5]
    if not sClean or len(sClean) > 200:
        raise HTTPException(
            400, "sFileName must be 1-200 characters",
        )
    if "/" in sClean or ".." in sClean:
        raise HTTPException(
            400, "sFileName may not contain '/' or '..'",
        )
    if not _PATTERN_WORKFLOW_FILENAME.match(sClean):
        raise HTTPException(
            400,
            "sFileName must match [A-Za-z0-9_][A-Za-z0-9_.-]*",
        )
    return sClean + ".json"


def _fbIsContainerStopped(error):
    """Return True if the error indicates a stopped container."""
    sMessage = str(error).lower()
    return "409" in sMessage and "conflict" in sMessage


def _fnRejectDuplicateWorkflowName(
    connectionDocker, sContainerId, sWorkflowName
):
    """Raise 409 if another workflow in container uses this name."""
    listExisting = workflowManager.flistFindWorkflowsInContainer(
        connectionDocker, sContainerId
    )
    for dictWorkflow in listExisting:
        if dictWorkflow["sName"] == sWorkflowName:
            raise HTTPException(
                409,
                f"A project named '{sWorkflowName}' already "
                f"exists at {dictWorkflow['sPath']}",
            )


def _fsValidateRepoDirectory(
    connectionDocker, sContainerId, sRepoDirectory
):
    """Validate the directory exists under /workspace/ and is a git repo."""
    sClean = sRepoDirectory.strip().strip("/")
    if not sClean:
        raise HTTPException(
            400, "sRepoDirectory is required"
        )
    if ".." in sClean.split("/"):
        raise HTTPException(
            400, "sRepoDirectory may not contain '..'"
        )
    sFullPath = posixpath.join("/workspace", sClean)
    iExitCode, _ = connectionDocker.ftResultExecuteCommand(
        sContainerId,
        f"test -d {fsShellQuote(sFullPath)}",
    )
    if iExitCode != 0:
        raise HTTPException(
            404,
            f"Repo directory not found: {sFullPath}",
        )
    iGitCode, _ = connectionDocker.ftResultExecuteCommand(
        sContainerId,
        f"git -C {fsShellQuote(sFullPath)} "
        f"rev-parse --show-toplevel 2>/dev/null",
    )
    if iGitCode != 0:
        raise HTTPException(
            400,
            f"Directory '{sFullPath}' is not a git repository. "
            f"Run 'git init' inside the container at that path "
            f"first.",
        )
    return sFullPath


def _fnRegisterWorkflowSearch(app, dictCtx):
    """Register GET /api/workflows route."""

    @app.get("/api/workflows/{sContainerId}")
    async def fnFindWorkflows(sContainerId: str):
        dictCtx["require"]()
        try:
            return workflowManager.flistFindWorkflowsInContainer(
                dictCtx["docker"], sContainerId
            )
        except Exception as error:
            if _fbIsContainerStopped(error):
                raise HTTPException(
                    409, "Container is not running. "
                    "Start it before selecting a project.")
            raise HTTPException(
                500, f"Search failed: "
                f"{_fsSanitizeServerError(str(error))}")


# New projects start with a four-hour advisory runtime limit per step
# (the dashboard flags a longer-running step as possibly hung; the run
# is never stopped). Existing projects keep their recorded value —
# absent means no limit.
F_NEW_PROJECT_RUNTIME_LIMIT_SECONDS = 14400.0


def _fdictBlankWorkflowContent(request):
    """Return the minimum-viable project.json dict for a fresh create."""
    return {
        "sWorkflowName": request.sWorkflowName,
        "sPlotDirectory": "Plot",
        "sFigureType": "pdf",
        "iNumberOfCores": -1,
        "fDefaultWallClockBudgetSeconds":
            F_NEW_PROJECT_RUNTIME_LIMIT_SECONDS,
        "listSteps": [],
    }


def _fsEnsureWorkflowDir(connectionDocker, sContainerId, sRepoDirectory):
    """``mkdir -p`` the Projects directory inside the repo and return its path.

    New Projects are written under the canonical ``.vaibify/projects``;
    the legacy ``.vaibify/workflows`` is still read at discovery time.
    """
    sWorkflowDir = posixpath.join(
        sRepoDirectory, workflowManager.VAIBIFY_PROJECTS_DIR,
    )
    connectionDocker.ftResultExecuteCommand(
        sContainerId, f"mkdir -p {fsShellQuote(sWorkflowDir)}",
    )
    return sWorkflowDir


def _fnAssertWorkflowAbsent(connectionDocker, sContainerId, sFullPath):
    """Raise HTTP 409 when ``sFullPath`` already exists inside the container."""
    iExitCode, _ = connectionDocker.ftResultExecuteCommand(
        sContainerId, f"test -e {fsShellQuote(sFullPath)}",
    )
    if iExitCode == 0:
        raise HTTPException(
            409, f"A project file already exists at {sFullPath}",
        )


def _fdictCreateWorkflowResponse(sFullPath, sWorkflowName):
    """Return the JSON body for a successful workflow create."""
    return {
        "sPath": sFullPath,
        "sName": sWorkflowName,
        "sSource": "vaibify",
    }


def _fnRegisterWorkflowCreate(app, dictCtx):
    """Register POST /api/workflows/{id}/create route."""

    @app.post("/api/workflows/{sContainerId}/create")
    async def fnCreateWorkflow(
        sContainerId: str, request: CreateWorkflowRequest
    ):
        dictCtx["require"]()
        _fnRejectDuplicateWorkflowName(
            dictCtx["docker"], sContainerId, request.sWorkflowName,
        )
        sRepoDirectory = _fsValidateRepoDirectory(
            dictCtx["docker"], sContainerId, request.sRepoDirectory,
        )
        sFileName = _fsValidateAndNormalizeFileName(request.sFileName)
        sWorkflowDir = _fsEnsureWorkflowDir(
            dictCtx["docker"], sContainerId, sRepoDirectory,
        )
        sFullPath = posixpath.join(sWorkflowDir, sFileName)
        _fnAssertWorkflowAbsent(dictCtx["docker"], sContainerId, sFullPath)
        sContent = json.dumps(
            _fdictBlankWorkflowContent(request), indent=2,
        ) + "\n"
        dictCtx["docker"].fnWriteFile(
            sContainerId, sFullPath, sContent.encode("utf-8"),
        )
        return _fdictCreateWorkflowResponse(
            sFullPath, request.sWorkflowName,
        )


def _fnRegisterWorkflowCreationRequest(app, dictCtx):
    """Register POST /api/workflows/{id}/request-creation route.

    Project creation is a researcher-only decision, so the in-container
    agent never creates a project.json directly. This route records the
    agent's request; the browser's discovery poll picks it up within
    one tick and opens the New Project wizard, prefilled with the
    suggested name, for the researcher to review and confirm.
    """

    @fnAgentAction("create-project")
    @app.post("/api/workflows/{sContainerId}/request-creation")
    async def fnRequestProjectCreation(
        sContainerId: str, request: RequestProjectCreationRequest
    ):
        dictCtx["require"]()
        dictCtx["dictProjectCreationRequests"][sContainerId] = {
            "sSuggestedName":
                (request.sWorkflowName or "").strip()[:200],
            "sSuggestedDirectory":
                (request.sRepoDirectory or "").strip()[:200],
        }
        return {
            "bCreated": False,
            "sMessage": (
                "Project creation is a researcher-only action. The "
                "New Project dialog has been opened in the "
                "researcher's browser; ask them to review and "
                "confirm it there."
            ),
        }


def _fnRequireOwningLeaseForConnect(dictCtx, sContainerId, requestHttp):
    """Refuse a connect that does not present the owning session's lease.

    ``docs/architecture.md`` names the owner-of-record map the sole
    authority that claim, connect, and both WebSocket gates consult, but
    connect had no gate at all: a second browser tab could skip the
    claim route's 409 and take the workflow, the project-repo path, and
    the container's agent session out from under the owning session.
    The check here reads the ``X-Vaibify-Lease`` header (the HTTP lease
    transport), resolves the caller's browser session, and consults the
    session-bound owner predicate
    (:func:`containerOwnership.fbBrowserSessionOwnsLease`) — the same
    strong principal the WebSocket gates and the container-owner HTTP
    boundary use, so the lanes can never drift apart. A copied lease
    VALUE presented by a second browser session is therefore refused, not
    merely a forged one. A pre-binding owner record (a transitional claim
    that recorded no session) carries ``sBrowserSessionId == ""``; there
    the matching lease value alone admits, the same allowance the
    claim-reclaim branch makes.

    An unowned container is left open ONLY in the single-container
    viewer, whose bootstrap that is: the viewer has no claim route and
    mints its lease inside the connect handler, handing it back on the
    response. The hub must NOT extend that exception. In the hub an
    empty owner record does not mean "free to take" — it can mean
    another hub process holds the host flock — and connect runs a write
    path (it caches the workflow and pushes an agent session, which with
    no local owner record writes an empty agent token that clobbers the
    real owner's in-container credential). So a hub client must claim
    first; the claim route is what mints the owner record this gate then
    checks. Restricting the ownerless exception to the viewer is the fix
    for that cross-hub exclusivity hole.
    """
    dictContainerOwners = dictCtx.get("dictContainerOwners") or {}
    sName = fsContainerNameForId(dictCtx.get("docker"), sContainerId)
    if dictContainerOwners.get(sName) is None:
        if dictCtx.get("bIsHub"):
            raise HTTPException(
                409,
                "Claim this container before connecting to it.",
            )
        return
    sLeaseId = fsLeaseFromRequest(requestHttp)
    sBrowserSessionId = _fsResolveBrowserSessionId(dictCtx, requestHttp)
    _fnRefuseConnectWhileStarting(
        dictContainerOwners[sName], sName, sBrowserSessionId,
    )
    if containerOwnership.fbBrowserSessionOwnsLease(
        dictContainerOwners, sName, sBrowserSessionId, sLeaseId,
    ):
        return
    recordOwner = dictContainerOwners.get(sName)
    if recordOwner.sBrowserSessionId == "" and bool(sLeaseId) and (
        recordOwner.sLeaseId == sLeaseId
    ):
        return
    raise HTTPException(409, "In use in another browser session")


def _fnRefuseConnectWhileStarting(recordOwner, sName, sBrowserSessionId):
    """Tell the initiating session the truth: the start is still running.

    Design §10b: a connect by the session that requested the start, while
    the container is not yet running, must get a truthful pending refusal
    — never a lease, because a workflow cannot be loaded from a container
    that does not exist yet. Another session's connect falls through to
    the ordinary in-use refusal, which says nothing it should not.
    """
    if getattr(recordOwner, "reservation", None) is None:
        return
    if recordOwner.sBrowserSessionId not in ("", sBrowserSessionId):
        return
    raise HTTPException(409, (
        f"Container '{sName}' is still starting. Poll its start status; "
        "connect once it reports the container running."
    ))


def _fnRegisterConnect(app, dictCtx):
    """Register POST /api/connect route."""

    @app.post("/api/connect/{sContainerId}")
    @fnRouteScope(S_SCOPE_OWNER_ESTABLISHING, "sContainerId", "id")
    async def fnConnect(
        requestHttp: Request,
        sContainerId: str,
        sWorkflowPath: Optional[str] = None,
    ):
        dictCtx["require"]()
        _fnRequireOwningLeaseForConnect(
            dictCtx, sContainerId, requestHttp)
        sBrowserSessionId = _fsResolveBrowserSessionId(dictCtx, requestHttp)
        # Connect writes into the container (the per-container agent
        # session file) after arbitrating ownership itself, so it holds
        # the carrier's owner-establishing admission for the handler's
        # duration (design §8) — the one lane where the owner record
        # may be created DURING the handler (viewer first connect).
        from .. import commitCarrier
        tAdmissionTokens = commitCarrier.ftOpenEstablishingAdmission(
            _fsResolveOwnedNameForContainerId(dictCtx, sContainerId),
            sContainerId,
        )
        try:
            return await fdictHandleConnect(
                dictCtx, sContainerId, sWorkflowPath, sBrowserSessionId)
        finally:
            commitCarrier.fnCloseRequestAdmission(tAdmissionTokens)


def _fsResolveOwnedNameForContainerId(dictCtx, sContainerId):
    """Return the owner-map key serving a Docker id, or the id itself.

    The hub keys its owner map by project name and records the Docker
    id at claim time; the viewer keys it by the container id directly
    (and may not have recorded it yet on first connect).
    """
    dictContainerOwners = dictCtx.get("dictContainerOwners", {}) or {}
    for sName, recordOwner in dictContainerOwners.items():
        if recordOwner.sContainerId == sContainerId or sName == sContainerId:
            return sName
    return sContainerId


def _fsResolveBrowserSessionId(dictCtx, requestHttp):
    """Resolve the connecting browser session id, or '' when none.

    The viewer's first connect binds ownership to this session; a
    transitional shared-token request carries no credential and resolves
    to '', leaving the viewer's owner record unbound.
    """
    return browserSession.fsSessionIdForCredential(
        dictCtx.get("dictBrowserSessions") or {},
        requestHttp.headers.get("x-session-token", ""),
    )


def fnRegisterAll(app, dictCtx):
    """Register all workflow management routes."""
    _fnRegisterWorkflowSearch(app, dictCtx)
    _fnRegisterWorkflowCreate(app, dictCtx)
    _fnRegisterWorkflowCreationRequest(app, dictCtx)
    _fnRegisterConnect(app, dictCtx)
