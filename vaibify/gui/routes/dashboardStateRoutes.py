"""The saved dashboard layout: how a researcher last arranged the screen.

Every route here is the researcher's alone. Each is excluded from the
agent catalog (``actionCatalog.SET_INTENTIONALLY_EXCLUDED_PATHS``) and
each handler also refuses the agent token lane by name, so an agent can
neither read how the researcher works nor rewrite it. The records live
on the host (:mod:`vaibify.config.dashboardRecords`), never in the
container, and reach no container at all.

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
import posixpath

from fastapi import HTTPException, Request
from pydantic import BaseModel

from vaibify.config import dashboardRecords

from ..pipelineServer import fsContainerNameForId
from ..routeContext import fnRejectAgentTokenLane
from ..routeScope import (
    S_CARRIER_SEPARATE_AUTHORITY, ffnDeclareCarrierMode,
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


def fnRegisterAll(app, dictCtx):
    """Register the saved-layout routes."""
    _fnRegisterEnvironmentRecord(app, dictCtx)
    _fnRegisterProjectRecord(app, dictCtx)
