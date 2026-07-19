"""HTTP routes for the Replay axis: AI-model declarations.

The Replay axis records the provenance of the development process.
Phase one is the model declaration: every AI model used on the project
is declared with vendor, model identifier, and date range of use —
open-weights models additionally declare their weights source and
revision hash. Undeclared is the only failing state of the criterion.

Declarations live in ``dictWorkflow["dictAiProvenance"]`` (see
:mod:`vaibify.reproducibility.replayGate`), validated here at the
write routes like every other project-scope declaration block.
"""

__all__ = ["fnRegisterAll"]

from datetime import datetime

from fastapi import HTTPException

from ..actionCatalog import fnAgentAction
from ..pipelineServer import fdictRequireWorkflow
from ...reproducibility.replayGate import (
    S_AI_PROVENANCE_KEY,
    S_DECLARED_MODELS_KEY,
    flistDescribeModelDeclarationGaps,
)


_LIST_MODEL_FIELDS = [
    "sVendor", "sModelId", "sUseStartDate", "sUseEndDate",
    "bOpenWeights", "sWeightsSource", "sWeightsRevisionHash",
]
_LIST_DATE_FIELDS = ["sUseStartDate", "sUseEndDate"]


def _fbDateIsIsoFormat(sDate):
    """Return True iff the value parses as a YYYY-MM-DD date."""
    try:
        datetime.strptime(str(sDate), "%Y-%m-%d")
    except ValueError:
        return False
    return True


def _fdictValidateModelBody(request):
    """Return the sanitized model declaration or raise HTTP 400."""
    if not isinstance(request, dict):
        raise HTTPException(400, "Model declaration must be an object.")
    dictModel = {
        sField: request[sField]
        for sField in _LIST_MODEL_FIELDS
        if sField in request
    }
    listGaps = flistDescribeModelDeclarationGaps(dictModel)
    if listGaps:
        raise HTTPException(
            400, "Model declaration is missing: " + ", ".join(listGaps),
        )
    for sField in _LIST_DATE_FIELDS:
        if not _fbDateIsIsoFormat(dictModel.get(sField)):
            raise HTTPException(
                400, f"{sField} must be a YYYY-MM-DD date.",
            )
    return dictModel


def _flistUpsertModel(listModels, dictModel):
    """Replace the (vendor, model id) entry or append a new one."""
    tKey = (dictModel.get("sVendor"), dictModel.get("sModelId"))
    listUpdated = [
        dictExisting
        for dictExisting in listModels
        if (dictExisting.get("sVendor"), dictExisting.get("sModelId")) != tKey
    ]
    listUpdated.append(dictModel)
    return listUpdated


def _fdictProvenanceOf(dictWorkflow):
    """Return the workflow's mutable AI-provenance block, creating it."""
    dictProvenance = dict(dictWorkflow.get(S_AI_PROVENANCE_KEY) or {})
    dictWorkflow[S_AI_PROVENANCE_KEY] = dictProvenance
    return dictProvenance


def _fnRegisterDeclareAiModel(app, dictCtx):
    """Register POST /api/workflow/{sContainerId}/ai-models/declare."""

    @fnAgentAction("declare-ai-model")
    @app.post("/api/workflow/{sContainerId}/ai-models/declare")
    async def fnDeclareAiModel(sContainerId: str, request: dict):
        dictCtx["require"]()
        dictWorkflow = fdictRequireWorkflow(
            dictCtx["workflows"], sContainerId,
        )
        dictModel = _fdictValidateModelBody(request)
        dictProvenance = _fdictProvenanceOf(dictWorkflow)
        dictProvenance[S_DECLARED_MODELS_KEY] = _flistUpsertModel(
            list(dictProvenance.get(S_DECLARED_MODELS_KEY) or []),
            dictModel,
        )
        dictCtx["save"](sContainerId, dictWorkflow)
        return {
            "listDeclaredModels": dictProvenance[S_DECLARED_MODELS_KEY],
        }


def _fnRegisterRemoveAiModel(app, dictCtx):
    """Register POST /api/workflow/{sContainerId}/ai-models/remove."""

    @fnAgentAction("remove-ai-model")
    @app.post("/api/workflow/{sContainerId}/ai-models/remove")
    async def fnRemoveAiModel(sContainerId: str, request: dict):
        dictCtx["require"]()
        dictWorkflow = fdictRequireWorkflow(
            dictCtx["workflows"], sContainerId,
        )
        tKey = (request.get("sVendor"), request.get("sModelId"))
        dictProvenance = _fdictProvenanceOf(dictWorkflow)
        listModels = list(dictProvenance.get(S_DECLARED_MODELS_KEY) or [])
        listRemaining = [
            dictModel
            for dictModel in listModels
            if (dictModel.get("sVendor"), dictModel.get("sModelId")) != tKey
        ]
        if len(listRemaining) == len(listModels):
            raise HTTPException(404, "No such declared model.")
        dictProvenance[S_DECLARED_MODELS_KEY] = listRemaining
        dictCtx["save"](sContainerId, dictWorkflow)
        return {"listDeclaredModels": listRemaining}


def fnRegisterAll(app, dictCtx):
    """Register all Replay-axis routes."""
    _fnRegisterDeclareAiModel(app, dictCtx)
    _fnRegisterRemoveAiModel(app, dictCtx)
