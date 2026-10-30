"""The researcher's decisions about Zenodo tokens moved out of the container.

A token that lived in the container keyring is staged on the host as
PENDING by :mod:`vaibify.gui.zenodoTokenMigration` and can be spent by
nothing until the researcher confirms it (ruling D6, 2026-10-28: the
container keyring is agent-writable, so the token may not be the
researcher's). These routes are that confirmation: the GET shows each
slot's state, how the host-side validation went and what Zenodo reports
about the token; the POSTs record a decision -- confirm, decline, or
resolve a conflict -- and run a migration pass. Every one of them is
researcher-only twice over, in the catalog and in the handler.
"""

__all__ = ["fnRegisterAll"]

import asyncio

from fastapi import HTTPException, Request
from pydantic import BaseModel

from vaibify.config.zenodoTokenSlots import TokenStoreUnreadable

from .. import zenodoTokenMigration
from ..actionCatalog import ffnAgentAction
from ..routeContext import (
    fdictCarryARefusalBackInsteadOfRaising, fgenericRunWorkerUnderTheDrain,
    fnRejectAgentTokenLane,
)
from ..routeScope import (
    S_CARRIER_MODE_B_LOCK_HELD, S_CARRIER_TYPED_READ, ffnDeclareCarrierMode,
)


class ZenodoTokenDecisionRequest(BaseModel):
    sSlot: str
    sDecision: str
    sInstance: str = ""
    bReplaceActive: bool = False


def _fnRegisterDescribe(app, dictCtx):
    """Register GET /api/zenodo-tokens/{sContainerId}."""

    @app.get("/api/zenodo-tokens/{sContainerId}")
    @ffnDeclareCarrierMode(S_CARRIER_TYPED_READ)
    async def fdictDescribeZenodoTokens(sContainerId: str, requestHttp: Request):
        fnRejectAgentTokenLane(requestHttp)
        dictCtx["require"](sContainerId)
        try:
            return await asyncio.to_thread(
                zenodoTokenMigration.fdictDescribeMigration,
                dictCtx["docker"], sContainerId,
            )
        except TokenStoreUnreadable as error:
            raise HTTPException(409, str(error)) from error


async def _fdictRunUnderTheDrain(
    dictCtx, sContainerId, requestHttp, fnEffect,
):
    """Run a migration effect as a mode-(b) worker; carry a refusal back.

    The pass may delete a container keyring slot, which is an exec, so
    it runs under the carrier's admission; a ``ValueError`` from the
    decision is the researcher's 409, never a poisoned record.
    """
    def fdictRunTheEffect(supervisor=None):
        del supervisor
        return fdictCarryARefusalBackInsteadOfRaising(fnEffect)

    try:
        return await fgenericRunWorkerUnderTheDrain(
            sContainerId, fdictRunTheEffect, "zenodo-token-migration",
            requestHttp,
        )
    except (ValueError, TokenStoreUnreadable) as error:
        raise HTTPException(409, str(error)) from error


def _fnRegisterDecide(app, dictCtx):
    """Register POST /api/zenodo-tokens/{sContainerId}/decide."""

    @ffnAgentAction("decide-migrated-zenodo-token")
    @app.post("/api/zenodo-tokens/{sContainerId}/decide")
    @ffnDeclareCarrierMode(S_CARRIER_MODE_B_LOCK_HELD)
    async def fdictDecideZenodoToken(
        sContainerId: str, request: ZenodoTokenDecisionRequest,
        requestHttp: Request,
    ):
        fnRejectAgentTokenLane(requestHttp)
        dictCtx["require"](sContainerId)
        if request.sDecision not in zenodoTokenMigration.T_DECISIONS:
            raise HTTPException(400, (
                f"{request.sDecision!r} is not a decision; the decisions "
                f"are {list(zenodoTokenMigration.T_DECISIONS)}"
            ))
        return await _fdictRunUnderTheDrain(
            dictCtx, sContainerId, requestHttp,
            lambda: zenodoTokenMigration.fdictDecideMigratedToken(
                dictCtx["docker"], sContainerId, request.sSlot,
                request.sDecision, request.sInstance, request.bReplaceActive,
            ),
        )


def _fnRegisterMigrate(app, dictCtx):
    """Register POST /api/zenodo-tokens/{sContainerId}/migrate."""

    @ffnAgentAction("retry-zenodo-token-migration")
    @app.post("/api/zenodo-tokens/{sContainerId}/migrate")
    @ffnDeclareCarrierMode(S_CARRIER_MODE_B_LOCK_HELD)
    async def fdictRetryZenodoTokenMigration(
        sContainerId: str, requestHttp: Request,
    ):
        fnRejectAgentTokenLane(requestHttp)
        dictCtx["require"](sContainerId)
        return await _fdictRunUnderTheDrain(
            dictCtx, sContainerId, requestHttp,
            lambda: zenodoTokenMigration.fdictRunMigrationPass(
                dictCtx["docker"], sContainerId,
            ),
        )


def fnRegisterAll(app, dictCtx):
    """Register the Zenodo token migration routes."""
    _fnRegisterDescribe(app, dictCtx)
    _fnRegisterDecide(app, dictCtx)
    _fnRegisterMigrate(app, dictCtx)
