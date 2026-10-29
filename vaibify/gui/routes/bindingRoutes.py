"""The researcher's confirmation of which remote a workflow's tokens may act on.

A token-bearing action (an Overleaf push or pull, a Zenodo deposit,
publish or Make Permanent) reads its target from the host-side binding
record (:mod:`vaibify.config.remoteBindings`), never from the
``project.json`` the in-container agent can edit. These two routes are
how the record is read and written: the GET shows, per service, what
``project.json`` names, what the researcher confirmed, and what the host
fetched about the named target with the researcher's own token; the
POST records the researcher's confirmation. The POST is researcher-only
twice over, in the catalog and in the handler.
"""

__all__ = ["fnRegisterAll"]

import asyncio
from typing import Optional

from fastapi import HTTPException, Request
from pydantic import BaseModel

from vaibify.config import remoteBindings

from ..actionCatalog import ffnAgentAction
from ..pipelineServer import fdictRequireWorkflow
from ..remoteBindingContext import fdictProjectTargets, ftBindingScope
from ..routeContext import fnRejectAgentTokenLane
from ..routeScope import (
    S_CARRIER_SEPARATE_AUTHORITY, S_CARRIER_TYPED_READ, ffnDeclareCarrierMode,
)


class BindingConfirmRequest(BaseModel):
    sService: str
    dictTarget: Optional[dict] = None
    sCarryFromWorkflowKey: str = ""


def _fdictFetchOverleafEvidence(dictCtx, sContainerId, dictTarget):
    """Return what the host can say about an Overleaf project with its token."""
    del dictCtx, sContainerId
    from .. import syncDispatcher
    sProjectId = dictTarget.get("sProjectId", "")
    if not sProjectId:
        return {"sState": "no target"}
    tRefresh = syncDispatcher.ftRefreshOverleafMirror(sProjectId)
    if not tRefresh[0]:
        return {"sState": "unreachable", "sError": str(tRefresh[1])}
    return {"sState": "reachable", **dict(tRefresh[1])}


def _fdictFetchZenodoEvidence(dictCtx, sContainerId, dictTarget):
    """Return the title and owner Zenodo reports for the bound record.

    Fetched with the project's ACTIVE token for the instance; a pending
    migrated token is not spent here, because it is not yet the
    researcher's to spend.
    """
    from vaibify.reproducibility import zenodoClient
    from .. import zenodoTokenAccess
    sInstance = dictTarget.get("sInstance", "")
    if not sInstance:
        return {"sState": "no target"}
    sToken = zenodoTokenAccess.fsActiveZenodoToken(
        dictCtx["docker"], sContainerId, sInstance,
    )
    if not sToken:
        return {"sState": "no active host token for this instance"}
    try:
        clientZenodo = zenodoClient.ZenodoClient(sInstance, sToken=sToken)
        if dictTarget.get("sDepositionId"):
            dictDeposit = clientZenodo.fdictGetDeposit(
                int(dictTarget["sDepositionId"]),
            )
        elif dictTarget.get("sConceptDoi"):
            dictDeposit = clientZenodo.fdictFetchPublishedRecord(
                zenodoClient.fsExtractRecordIdFromDoi(dictTarget["sConceptDoi"]),
            )
        else:
            return {"sState": "no record yet; the first deposit mints one"}
    except Exception as error:  # noqa: BLE001 -- the fetch's own words are the evidence
        return {"sState": "unreachable", "sError": f"{type(error).__name__}: {error}"}
    return {
        "sState": "fetched",
        "sTitle": str((dictDeposit.get("metadata") or {}).get("title") or ""),
        "sOwner": str(dictDeposit.get("owner") or dictDeposit.get("owners") or ""),
    }


_DICT_EVIDENCE_FETCHERS = {
    remoteBindings.S_SERVICE_OVERLEAF: _fdictFetchOverleafEvidence,
    remoteBindings.S_SERVICE_ZENODO: _fdictFetchZenodoEvidence,
}


def _fdictDescribeBindings(dictCtx, sContainerId, dictWorkflow):
    """Return, per service, the project's target, the binding and the evidence."""
    sProjectUuid, sWorkflowKey, sWorkflowName = ftBindingScope(
        dictCtx, sContainerId, dictWorkflow,
    )
    dictTargets = fdictProjectTargets(dictWorkflow)
    dictServices = {}
    for sService in remoteBindings.T_BINDING_SERVICES:
        dictBound = remoteBindings.fdictConfirmedBinding(
            sProjectUuid, sWorkflowKey, sService,
        )
        dictServices[sService] = {
            "dictProjectTarget": dictTargets[sService],
            "dictBinding": dictBound,
            "dictFetched": _DICT_EVIDENCE_FETCHERS[sService](
                dictCtx, sContainerId, dictTargets[sService],
            ),
            "bAgrees": _fbTargetsAgree(sService, dictTargets[sService], dictBound),
        }
    return {
        "sProjectUuid": sProjectUuid, "sWorkflowKey": sWorkflowKey,
        "sWorkflowName": sWorkflowName, "dictServices": dictServices,
    }


def _fbTargetsAgree(sService, dictProjectTarget, dictBound):
    if dictBound is None:
        return False
    return all(
        not sValue or dictBound["dictTarget"].get(sField) == sValue
        for sField, sValue in dictProjectTarget.items()
    )


def _fnRegisterRead(app, dictCtx):
    """Register GET /api/bindings/{sContainerId}."""

    @app.get("/api/bindings/{sContainerId}")
    @ffnDeclareCarrierMode(S_CARRIER_TYPED_READ)
    async def fdictDescribeRemoteBindings(sContainerId: str, requestHttp: Request):
        fnRejectAgentTokenLane(requestHttp)
        dictCtx["require"](sContainerId)
        dictWorkflow = fdictRequireWorkflow(dictCtx["workflows"], sContainerId)
        return await asyncio.to_thread(
            _fdictDescribeBindings, dictCtx, sContainerId, dictWorkflow,
        )


def _fnRegisterConfirm(app, dictCtx):
    """Register POST /api/bindings/{sContainerId}/confirm."""

    @ffnAgentAction("confirm-remote-binding")
    @app.post("/api/bindings/{sContainerId}/confirm")
    # separate-authority: the write is a host file under ~/.vaibify, and
    # the container is neither read nor written.
    @ffnDeclareCarrierMode(S_CARRIER_SEPARATE_AUTHORITY)
    async def fdictConfirmBinding(
        sContainerId: str, request: BindingConfirmRequest,
        requestHttp: Request,
    ):
        fnRejectAgentTokenLane(requestHttp)
        dictCtx["require"](sContainerId)
        dictWorkflow = fdictRequireWorkflow(dictCtx["workflows"], sContainerId)
        if request.sService not in remoteBindings.T_BINDING_SERVICES:
            raise HTTPException(400, (
                f"{request.sService!r} is not a bindable service; "
                f"the services are {list(remoteBindings.T_BINDING_SERVICES)}"
            ))
        dictTarget = request.dictTarget or fdictProjectTargets(
            dictWorkflow,
        )[request.sService]
        sProjectUuid, sWorkflowKey, sWorkflowName = ftBindingScope(
            dictCtx, sContainerId, dictWorkflow,
        )
        dictEvidence = await asyncio.to_thread(
            _DICT_EVIDENCE_FETCHERS[request.sService], dictCtx, sContainerId,
            dictTarget,
        )
        try:
            remoteBindings.fnConfirmBinding(
                sProjectUuid, sWorkflowKey, sWorkflowName, request.sService,
                dictTarget, "researcher", dictEvidence=dictEvidence,
                sCarryFromWorkflowKey=request.sCarryFromWorkflowKey,
            )
        except ValueError as error:
            raise HTTPException(400, str(error)) from error
        return {
            "bConfirmed": True, "sWorkflowKey": sWorkflowKey,
            "dictTarget": remoteBindings.fdictConfirmedBinding(
                sProjectUuid, sWorkflowKey, request.sService,
            )["dictTarget"],
            "dictFetched": dictEvidence,
        }


def fnRegisterAll(app, dictCtx):
    """Register the remote-binding routes."""
    _fnRegisterRead(app, dictCtx)
    _fnRegisterConfirm(app, dictCtx)
