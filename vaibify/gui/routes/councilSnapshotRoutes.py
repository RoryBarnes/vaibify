"""Council snapshot-scope routes: the omission pages and the scope choice.

When a project is too large to copy in full, the size modal offers the
git-tracked files instead and lists what would be missing. The list can
run to hundreds of thousands of names, so the capabilities read returns
only a bounded summary and an observation id; these routes page that
ONE observation, and record the researcher's choice as the project's
visible default.

Both routes carry the four layers every council consent route carries
(see ``councilCredentialRoutes``): the browser credential, the lease
through the ``{sContainerId}`` path, the agent-lane exclusion for the
mutation, and the handler's own agent refusal — reads included, because
omitted file names can be sensitive.
"""

__all__ = ["fnRegisterAll"]

import asyncio

from fastapi import HTTPException, Request
from pydantic import BaseModel, Field

from .. import agentCouncilSnapshotScope
from .. import councilRouteGuards
from ..routeScope import (
    ffnDeclareCarrierMode,
    S_CARRIER_SEPARATE_AUTHORITY,
)

S_EXPIRED_INVENTORY_REMEDY = (
    "This list of missing files is out of date — the project was measured "
    "again, or more than an hour has passed. Close this window and click "
    "the council button to measure it again.")


class SnapshotScopeRequest(BaseModel):
    """Body for choosing a project's snapshot scope."""

    sScope: str = Field(min_length=1, max_length=32)
    sProjectDirectory: str = Field(default="", max_length=255)


def _fnRegisterOmissionPage(app, dictCtx):
    """Register GET .../omissions/{sObservationId}."""

    @app.get("/api/council-snapshots/{sContainerId}/omissions/"
             "{sObservationId}")
    @ffnDeclareCarrierMode(S_CARRIER_SEPARATE_AUTHORITY)
    async def fdictServeOmissionPage(
        sContainerId: str, sObservationId: str, requestHttp: Request,
        sDirectory: str = "", sReason: str = "", iOffset: int = 0,
        iLimit: int = agentCouncilSnapshotScope.I_DEFAULT_PAGE_SIZE,
    ):
        councilRouteGuards.fsGuardCouncilRoute(
            dictCtx, requestHttp, sContainerId)
        try:
            return await asyncio.to_thread(
                agentCouncilSnapshotScope.fdictReadOmissionPage,
                sObservationId, sContainerId, sDirectory, sReason, iOffset,
                iLimit)
        except agentCouncilSnapshotScope.OmissionInventoryExpiredError:
            raise HTTPException(410, S_EXPIRED_INVENTORY_REMEDY)


def _fnRegisterChooseScope(app, dictCtx):
    """Register POST .../scope (remember the researcher's choice)."""

    @app.post("/api/council-snapshots/{sContainerId}/scope")
    @ffnDeclareCarrierMode(S_CARRIER_SEPARATE_AUTHORITY)
    async def fdictChooseSnapshotScope(
        sContainerId: str, request: SnapshotScopeRequest,
        requestHttp: Request,
    ):
        sName, sProjectRepoPath = councilRouteGuards.ftResolveCouncilPrincipal(
            dictCtx, requestHttp, sContainerId, request.sProjectDirectory)
        try:
            dictScope = agentCouncilSnapshotScope.fdictComposeSnapshotScope(
                request.sScope)
        except agentCouncilSnapshotScope.SnapshotScopeError as error:
            raise HTTPException(400, str(error))
        await asyncio.to_thread(
            agentCouncilSnapshotScope.fnRememberScope, sName,
            sProjectRepoPath, dictScope)
        return {"dictSnapshotScope": dictScope}


def fnRegisterAll(app, dictCtx):
    """Register every council snapshot-scope route."""
    _fnRegisterOmissionPage(app, dictCtx)
    _fnRegisterChooseScope(app, dictCtx)
