"""The saved dashboard layout: how a researcher last arranged the screen.

Every route here is the researcher's alone. Each is excluded from the
agent catalog (``actionCatalog.SET_INTENTIONALLY_EXCLUDED_PATHS``) and
each handler also refuses the agent token lane by name, so an agent can
neither read how the researcher works nor rewrite it. The records live
on the host (:mod:`vaibify.config.dashboardRecords`), never in the
container. Two routes reach where the work runs. ``terminal-chats`` is
one batched typed read of the page's own terminals' lifecycle records
(:mod:`vaibify.gui.agentChatLifecycle`), naming paths the hub derives
from validated terminal ids. ``chat-catalog`` (POST, because it is an
exec) runs one constant program under a mode-b carrier that lists the
agent CLIs' transcripts (:mod:`vaibify.gui.agentChatCatalog`) and
answers each chat's resume commands and permission verdict
(:mod:`vaibify.gui.agentChatResume`). A listed chat is discovered
conversation data, never verified.

The environment is named from the container id on the server, never by
the request. A project record is keyed by the project repository and
project file the request names; the key is captured by the page when it
schedules a save, so a save delayed past a project switch still lands
on the project it describes. A write carries ``iBaseRevision`` and is
refused ``DASHBOARD-RECORD-CONFLICT`` (409, with the stored revision)
when another window saved first.
"""

__all__ = ["fnRegisterAll"]

import asyncio
import json
import posixpath

from fastapi import HTTPException, Request
from pydantic import BaseModel

from vaibify.config import dashboardRecords

from .. import (
    agentChatCatalog, agentChatLifecycle, agentChatResume, projectRoots,
)
from ..pipelineServer import WORKSPACE_ROOT, fsContainerNameForId
from ..routeContext import (
    fdictRequireLaneTupleForCommit, fnRejectAgentTokenLane,
)
from ..routeScope import (
    S_CARRIER_MODE_B_LOCK_HELD, S_CARRIER_SEPARATE_AUTHORITY,
    S_CARRIER_TYPED_READ, ffnDeclareCarrierMode,
)
from ..stateManager import fsWorkflowKeyFromPath

S_RECORD_CONFLICT = "DASHBOARD-RECORD-CONFLICT"
S_RECORD_REFUSED = "DASHBOARD-RECORD-REFUSED"


class EnvironmentRecordRequest(BaseModel):
    """A save of the environment record."""

    iBaseRevision: int
    dictState: dict


class ProjectRecordRequest(BaseModel):
    """A save of one project file's record."""

    sProjectRepoPath: str
    sWorkflowPath: str
    iBaseRevision: int
    dictState: dict


def _fsEnvironmentName(dictCtx, sContainerId):
    """Return the environment's name, or 409 when it cannot be a key."""
    sName = fsContainerNameForId(dictCtx.get("docker"), sContainerId)
    try:
        dashboardRecords.fsEnvironmentDirectoryFor(sName)
    except ValueError as error:
        raise HTTPException(409, detail={
            "sError": S_RECORD_REFUSED, "sMessage": str(error),
        }) from error
    return sName


def _ftProjectRecordPath(dictCtx, sContainerId, sProjectRepoPath,
                         sWorkflowPath):
    """Return the project record's path, or 400 naming what is wrong."""
    if (not isinstance(sWorkflowPath, str)
            or posixpath.normpath(sWorkflowPath or ".") != sWorkflowPath):
        raise HTTPException(400, detail={
            "sError": S_RECORD_REFUSED,
            "sMessage": "the project file is not a normalized path",
        })
    sWorkflowKey = fsWorkflowKeyFromPath(sWorkflowPath, sProjectRepoPath)
    try:
        return dashboardRecords.fsProjectRecordPath(
            _fsEnvironmentName(dictCtx, sContainerId), sProjectRepoPath,
            sWorkflowKey,
        ), sWorkflowKey
    except dashboardRecords.RecordContentError as error:
        raise HTTPException(400, detail={
            "sError": S_RECORD_REFUSED, "sMessage": str(error),
        }) from error


async def _fdictWriteOrRefuse(sPath, sScope, request, dictIdentity):
    """Compare-and-write in a worker; translate a refusal to its status."""
    try:
        return await asyncio.to_thread(
            dashboardRecords.fdictWriteRecordIfCurrent, sPath, sScope,
            request.iBaseRevision, request.dictState, dictIdentity,
        )
    except dashboardRecords.RecordRevisionConflictError as error:
        raise HTTPException(409, detail={
            "sError": S_RECORD_CONFLICT, "sMessage": str(error),
            "iRevision": error.iStoredRevision,
        }) from error
    except dashboardRecords.RecordContentError as error:
        raise HTTPException(400, detail={
            "sError": S_RECORD_REFUSED, "sMessage": str(error),
        }) from error
    except OSError as error:
        raise HTTPException(500, detail={
            "sError": S_RECORD_REFUSED,
            "sMessage": f"The layout could not be saved: {error}",
        }) from error


def _fnRegisterEnvironmentRecord(app, dictCtx):
    """Register GET and PUT /api/dashboard-state/{sContainerId}/environment."""

    @app.get("/api/dashboard-state/{sContainerId}/environment")
    # separate-authority: a host file under ~/.vaibify; the container is
    # neither read nor written.
    @ffnDeclareCarrierMode(S_CARRIER_SEPARATE_AUTHORITY)
    async def fdictReadEnvironmentRecord(
        sContainerId: str, requestHttp: Request,
    ):
        fnRejectAgentTokenLane(requestHttp)
        sName = _fsEnvironmentName(dictCtx, sContainerId)
        return await asyncio.to_thread(
            dashboardRecords.fdictReadRecord,
            dashboardRecords.fsEnvironmentRecordPath(sName),
        )

    @app.put("/api/dashboard-state/{sContainerId}/environment")
    @ffnDeclareCarrierMode(S_CARRIER_SEPARATE_AUTHORITY)
    async def fdictWriteEnvironmentRecord(
        sContainerId: str, request: EnvironmentRecordRequest,
        requestHttp: Request,
    ):
        fnRejectAgentTokenLane(requestHttp)
        sName = _fsEnvironmentName(dictCtx, sContainerId)
        return await _fdictWriteOrRefuse(
            dashboardRecords.fsEnvironmentRecordPath(sName),
            dashboardRecords.S_SCOPE_ENVIRONMENT, request,
            {"sEnvironmentName": sName},
        )


def _fnRegisterProjectRecord(app, dictCtx):
    """Register GET and PUT /api/dashboard-state/{sContainerId}/project."""

    @app.get("/api/dashboard-state/{sContainerId}/project")
    # separate-authority: a host file under ~/.vaibify; the container is
    # neither read nor written.
    @ffnDeclareCarrierMode(S_CARRIER_SEPARATE_AUTHORITY)
    async def fdictReadProjectRecord(
        sContainerId: str, sProjectRepoPath: str, sWorkflowPath: str,
        requestHttp: Request,
    ):
        fnRejectAgentTokenLane(requestHttp)
        sPath, sWorkflowKey = _ftProjectRecordPath(
            dictCtx, sContainerId, sProjectRepoPath, sWorkflowPath,
        )
        dictRecord = await asyncio.to_thread(
            dashboardRecords.fdictReadRecord, sPath,
        )
        dictRecord["sWorkflowKey"] = sWorkflowKey
        return dictRecord

    @app.put("/api/dashboard-state/{sContainerId}/project")
    @ffnDeclareCarrierMode(S_CARRIER_SEPARATE_AUTHORITY)
    async def fdictWriteProjectRecord(
        sContainerId: str, request: ProjectRecordRequest,
        requestHttp: Request,
    ):
        fnRejectAgentTokenLane(requestHttp)
        sPath, sWorkflowKey = _ftProjectRecordPath(
            dictCtx, sContainerId, request.sProjectRepoPath,
            request.sWorkflowPath,
        )
        return await _fdictWriteOrRefuse(
            sPath, dashboardRecords.S_SCOPE_PROJECT, request, {
                "sProjectRepoPath": request.sProjectRepoPath,
                "sWorkflowKey": sWorkflowKey,
            },
        )


def _flistRequestedTerminalIds(sTerminalIds):
    """Return the ids asked about, or 400 when any is not a terminal id."""
    listIds = [sId for sId in (sTerminalIds or "").split(",") if sId]
    if len(listIds) > agentChatLifecycle.I_MAXIMUM_TERMINAL_IDS or not all(
            agentChatLifecycle.fbIsTerminalId(sId) for sId in listIds):
        raise HTTPException(400, detail={
            "sError": S_RECORD_REFUSED,
            "sMessage": "sTerminalIds names something that is not a "
                        "terminal of this page",
        })
    return listIds


def _fnRegisterTerminalChats(app, dictCtx):
    """Register GET /api/dashboard-state/{sContainerId}/terminal-chats."""

    @app.get("/api/dashboard-state/{sContainerId}/terminal-chats")
    # typed-read: one batched small-file read of the terminals' own
    # lifecycle records; no exec of anything a caller supplies.
    @ffnDeclareCarrierMode(S_CARRIER_TYPED_READ)
    async def fdictAnswerTerminalChats(
        sContainerId: str, sTerminalIds: str, requestHttp: Request,
    ):
        fnRejectAgentTokenLane(requestHttp)
        listIds = _flistRequestedTerminalIds(sTerminalIds)
        if not listIds:
            return {"dictChats": {}}
        dictCtx["require"](sContainerId)
        sWorkspaceRoot = projectRoots.fsResolveProjectRoot(
            sContainerId, WORKSPACE_ROOT,
        )
        dictChats = await asyncio.to_thread(
            agentChatLifecycle.fdictReadTerminalChats, dictCtx["docker"],
            sContainerId, listIds, sWorkspaceRoot,
        )
        return {"dictChats": dictChats}


class ChatCatalogRequest(BaseModel):
    """Which project's chats to list, and the saved chats to confirm."""

    sProjectRepoPath: str
    listRequested: list = []


def _flistValidRequestedChats(listRequested, sWorkspaceRoot):
    """The saved chats asked about, each in the shape the lifecycle emits."""
    listValid = []
    for dictChat in listRequested[:agentChatCatalog.I_MAXIMUM_REQUESTED_CHATS]:
        if not isinstance(dictChat, dict):
            continue
        dictRecord = {
            "sEvent": "start", "sProvider": dictChat.get("sProvider"),
            "sSessionId": dictChat.get("sSessionId"), "bSwitchHook": False,
            "sCwd": dictChat.get("sCwd"),
            "listArguments": dictChat.get("listArguments") or [],
            "iStartedEpoch": 0,
        }
        dictValid = agentChatLifecycle.fdictCurrentChatFromRecord(
            (json.dumps(dictRecord) + "\n").encode(), sWorkspaceRoot)
        if dictValid is not None and dictValid["sSessionId"]:
            listValid.append(dictValid)
    return listValid


def _fdictPlanOrNone(dictChat, dictConfig, sRecordedMode):
    try:
        return agentChatResume.fdictBuildResumePlan(
            dictChat, dictConfig, sRecordedMode)
    except ValueError:
        return None


def _fdictComposeCatalogAnswer(dictListing, listRequested):
    """Attach resume plans: catalog chats replay no flags, saved ones theirs."""
    dictConfig = dictListing["dictConfig"]
    dictByKey = {d["sProvider"] + ":" + d["sSessionId"]: d
                 for d in dictListing["listChats"]}
    listCatalog = []
    for dictChat in dictListing["listChats"]:
        if not dictChat["bInProject"]:
            continue
        dictEntry = dict(dictChat, listArguments=[])
        dictEntry["dictPlan"] = _fdictPlanOrNone(
            dictEntry, dictConfig, dictChat["sRecordedMode"])
        listCatalog.append(dictEntry)
    dictResolved = {}
    for dictChat in listRequested:
        sKey = dictChat["sProvider"] + ":" + dictChat["sSessionId"]
        dictFound = dictByKey.get(sKey)
        dictResolved[sKey] = {
            "bTranscriptFound": dictFound is not None,
            "sFirstPrompt": (dictFound or {}).get("sFirstPrompt", ""),
            "iModifiedEpoch": (dictFound or {}).get("iModifiedEpoch", 0),
            "dictPlan": _fdictPlanOrNone(
                dictChat, dictConfig, (dictFound or {}).get("sRecordedMode", "")),
        }
    return {"listCatalog": listCatalog, "dictResolved": dictResolved,
            "dictConfig": dictConfig}


async def _fdictListChatsUnderTheDrain(dictCtx, sContainerId, requestHttp,
                                       sProjectRepoPath, listRequested):
    """Run the catalog program under a mode-b carrier; never raise inside it.

    A failed listing is a failed READ, so the worker carries it back as a
    value: raising inside the carrier would poison its journal record
    and quarantine the container over a list nobody wrote.
    """
    from .. import commitCarrier
    dictLaneTuple = fdictRequireLaneTupleForCommit(
        requestHttp, sContainerId, "Listing agent chats")

    def fdictWorker(supervisor=None):
        del supervisor
        try:
            return {"dictListing": agentChatCatalog.fdictListAgentChats(
                dictCtx["docker"], sContainerId, sProjectRepoPath,
                listRequested)}
        except (RuntimeError, ValueError, OSError) as error:
            return {"sFailure": str(error)}

    dictOutcome = await commitCarrier.fdictRunLockHeldMutation(
        requestHttp.app.state, dictLaneTuple["sContainerName"], sContainerId,
        dictLaneTuple, "helper", "agent-chat-catalog", fdictWorker,
    )
    return dictOutcome["result"]


def _fnRegisterChatCatalog(app, dictCtx):
    """Register POST /api/dashboard-state/{sContainerId}/chat-catalog."""

    @app.post("/api/dashboard-state/{sContainerId}/chat-catalog")
    # mode-b: listing the transcripts runs one constant program where
    # they live, under the drain, as the Prompt Record capture does.
    @ffnDeclareCarrierMode(S_CARRIER_MODE_B_LOCK_HELD)
    async def fdictAnswerChatCatalog(
        sContainerId: str, request: ChatCatalogRequest, requestHttp: Request,
    ):
        fnRejectAgentTokenLane(requestHttp)
        dictCtx["require"](sContainerId)
        sWorkspaceRoot = projectRoots.fsResolveProjectRoot(
            sContainerId, WORKSPACE_ROOT)
        listRequested = _flistValidRequestedChats(
            request.listRequested, sWorkspaceRoot)
        try:
            agentChatCatalog.flistCatalogArguments(
                request.sProjectRepoPath, listRequested)
        except ValueError as error:
            raise HTTPException(400, detail={
                "sError": S_RECORD_REFUSED, "sMessage": str(error)}) from error
        dictResult = await _fdictListChatsUnderTheDrain(
            dictCtx, sContainerId, requestHttp, request.sProjectRepoPath,
            listRequested)
        if "sFailure" in dictResult:
            raise HTTPException(502, detail={
                "sError": "AGENT-CHATS-UNLISTED",
                "sMessage": dictResult["sFailure"]})
        return _fdictComposeCatalogAnswer(
            dictResult["dictListing"], listRequested)


def fnRegisterAll(app, dictCtx):
    """Register the saved-layout routes."""
    _fnRegisterEnvironmentRecord(app, dictCtx)
    _fnRegisterProjectRecord(app, dictCtx)
    _fnRegisterTerminalChats(app, dictCtx)
    _fnRegisterChatCatalog(app, dictCtx)
