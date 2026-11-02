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
from ..remoteBindingContext import (
    S_BINDING_RECORD_INVALID, fdictProjectTargets, fnRaiseForBindingError,
    fsDeclaredZenodoInstance, ftBindingScope,
)
from ..routeContext import fnRejectAgentTokenLane
from ..routeScope import (
    S_CARRIER_SEPARATE_AUTHORITY, S_CARRIER_TYPED_READ, ffnDeclareCarrierMode,
)


class BindingConfirmRequest(BaseModel):
    sService: str
    sRecordKind: str = ""
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

# Every entry a workflow's record can hold, in the order the dashboard
# lists them: (service, record kind, instance).
_T_ENTRY_ADDRESSES = (
    (remoteBindings.S_SERVICE_OVERLEAF, "", ""),
) + tuple(
    (remoteBindings.S_SERVICE_ZENODO, sRecordKind, sInstance)
    for sRecordKind in remoteBindings.T_ZENODO_RECORD_KINDS
    for sInstance in remoteBindings.T_ZENODO_INSTANCES
)


def _fdictProjectTargetFor(dictWorkflow, sService, sRecordKind, sInstance):
    """Return what ``project.json`` says about one entry, or None when it says nothing.

    ``project.json`` names the Overleaf project, and the project record
    on the instance it declares. It says nothing about the other
    instance's project record, nor about the environment archive.
    """
    dictTargets = fdictProjectTargets(dictWorkflow)
    if sService == remoteBindings.S_SERVICE_OVERLEAF:
        return dictTargets[sService]
    if sRecordKind != remoteBindings.S_RECORD_KIND_PROJECT or (
        sInstance != fsDeclaredZenodoInstance(dictWorkflow)
    ):
        return None
    return dict(dictTargets[sService], sInstance=sInstance)


def _fsAgreementState(dictProjectTarget, dictConfirmed):
    """Return ``unconfirmed``, ``confirmed``, ``agrees`` or ``differs``."""
    if dictConfirmed is None:
        return "unconfirmed"
    if dictProjectTarget is None:
        return "confirmed"
    bAgrees = all(
        not sValue or dictConfirmed["dictTarget"].get(sField) == sValue
        for sField, sValue in dictProjectTarget.items()
    )
    return "agrees" if bAgrees else "differs"


def _fdictDescribeEntry(dictCtx, sContainerId, dictWorkflow, dictServices, tAddress):
    sService, sRecordKind, sInstance = tAddress
    dictProjectTarget = _fdictProjectTargetFor(dictWorkflow, *tAddress)
    dictConfirmed = remoteBindings.fdictLookUpEntry(dictServices, *tAddress)
    dictEvidenceTarget = (dictConfirmed or {}).get("dictTarget") or dictProjectTarget
    return {
        "sService": sService, "sRecordKind": sRecordKind, "sInstance": sInstance,
        "dictProjectTarget": dictProjectTarget, "dictConfirmed": dictConfirmed,
        "sState": _fsAgreementState(dictProjectTarget, dictConfirmed),
        "dictFetched": _DICT_EVIDENCE_FETCHERS[sService](
            dictCtx, sContainerId, dictEvidenceTarget or {"sInstance": sInstance},
        ),
    }


def _fdictDescribeBindings(dictCtx, sContainerId, dictWorkflow):
    """Return the record's state and every entry, with what project.json names."""
    sProjectUuid, sWorkflowKey, sWorkflowName = ftBindingScope(
        dictCtx, sContainerId, dictWorkflow,
    )
    dictDescribed = {
        "sProjectUuid": sProjectUuid, "sWorkflowKey": sWorkflowKey,
        "sWorkflowName": sWorkflowName, "listEntries": [],
        "dictRecordState": {"bValid": True},
    }
    try:
        dictRecord = remoteBindings.fdictReadBindings(sProjectUuid)
    except remoteBindings.BindingRecordInvalidError as error:
        dictDescribed["dictRecordState"] = {
            "bValid": False, "sError": S_BINDING_RECORD_INVALID,
            "sReason": error.sReason, "sMessage": str(error),
        }
        return dictDescribed
    dictServices = (
        dictRecord["dictWorkflows"].get(sWorkflowKey) or {}
    ).get("dictServices") or {}
    dictDescribed["listEntries"] = [
        _fdictDescribeEntry(dictCtx, sContainerId, dictWorkflow, dictServices, tAddress)
        for tAddress in _T_ENTRY_ADDRESSES
    ]
    return dictDescribed


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


def _fdictDefaultTarget(dictWorkflow, sService, sRecordKind):
    """Return what a confirmation with no target names: project.json's own."""
    if sService == remoteBindings.S_SERVICE_OVERLEAF:
        return fdictProjectTargets(dictWorkflow)[sService]
    sInstance = fsDeclaredZenodoInstance(dictWorkflow)
    return _fdictProjectTargetFor(dictWorkflow, sService, sRecordKind, sInstance) or {
        "sInstance": sInstance,
    }


def _fdictRecordConfirmation(dictCtx, sContainerId, dictWorkflow, request):
    """Fetch the evidence and write ONE entry; return the response."""
    sProjectUuid, sWorkflowKey, sWorkflowName = ftBindingScope(
        dictCtx, sContainerId, dictWorkflow,
    )
    dictTarget = request.dictTarget or _fdictDefaultTarget(
        dictWorkflow, request.sService, request.sRecordKind,
    )
    dictEvidence = _DICT_EVIDENCE_FETCHERS[request.sService](
        dictCtx, sContainerId, dictTarget,
    )
    remoteBindings.fnConfirmBinding(
        sProjectUuid, sWorkflowKey, sWorkflowName, request.sService,
        request.sRecordKind, dictTarget, "researcher", dictEvidence=dictEvidence,
        sCarryFromWorkflowKey=request.sCarryFromWorkflowKey,
    )
    return {
        "bConfirmed": True, "sWorkflowKey": sWorkflowKey,
        "dictTarget": remoteBindings.fdictConfirmedBinding(
            sProjectUuid, sWorkflowKey, request.sService, request.sRecordKind,
            str(dictTarget.get("sInstance") or ""),
        )["dictTarget"],
        "dictFetched": dictEvidence,
    }


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
        try:
            remoteBindings.ftTargetFields(request.sService, request.sRecordKind)
        except ValueError as error:
            raise HTTPException(400, str(error)) from error
        try:
            return await asyncio.to_thread(
                _fdictRecordConfirmation, dictCtx, sContainerId, dictWorkflow,
                request,
            )
        except remoteBindings.BindingRecordInvalidError as error:
            fnRaiseForBindingError(error, request.sService, request.sRecordKind)
        except ValueError as error:
            raise HTTPException(400, str(error)) from error


def fnRegisterAll(app, dictCtx):
    """Register the remote-binding routes."""
    _fnRegisterRead(app, dictCtx)
    _fnRegisterConfirm(app, dictCtx)
