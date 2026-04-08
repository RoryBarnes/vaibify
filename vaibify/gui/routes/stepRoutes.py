"""Step CRUD route handlers."""

from fastapi import HTTPException

from .. import workflowManager
from ..pipelineServer import (
    ReorderRequest,
    StepCreateRequest,
    StepUpdateRequest,
    fdictFilterNonNone,
    fdictRequireWorkflow,
    fdictStepFromRequest,
)


def _fnRegisterStepsList(app, dictCtx):
    """Register GET /api/steps and validate routes."""

    @app.get("/api/steps/{sContainerId}")
    async def fnGetSteps(sContainerId: str):
        return workflowManager.flistExtractStepNames(
            fdictRequireWorkflow(
                dictCtx["workflows"], sContainerId)
        )

    @app.get("/api/steps/{sContainerId}/validate")
    async def fnValidateReferences(sContainerId: str):
        dictWorkflow = fdictRequireWorkflow(
            dictCtx["workflows"], sContainerId)
        return {
            "listWarnings": workflowManager.flistValidateReferences(
                dictWorkflow
            )
        }


def _fnRegisterStepGet(app, dictCtx):
    """Register GET /api/steps/{id}/{index} route."""

    @app.get("/api/steps/{sContainerId}/{iStepIndex}")
    async def fnGetStep(sContainerId: str, iStepIndex: int):
        dictWorkflow = fdictRequireWorkflow(
            dictCtx["workflows"], sContainerId)
        try:
            dictStep = workflowManager.fdictGetStep(
                dictWorkflow, iStepIndex
            )
            dictStep["saResolvedOutputFiles"] = (
                workflowManager.flistResolveOutputFiles(
                    dictStep,
                    dictCtx["variables"](sContainerId),
                )
            )
            return dictStep
        except IndexError as error:
            raise HTTPException(404, str(error))


def _fnRegisterStepCreate(app, dictCtx):
    """Register POST /api/steps/{id}/create route."""

    @app.post("/api/steps/{sContainerId}/create")
    async def fnCreateStep(
        sContainerId: str, request: StepCreateRequest
    ):
        dictCtx["require"]()
        dictWorkflow = fdictRequireWorkflow(
            dictCtx["workflows"], sContainerId)
        dictStep = fdictStepFromRequest(request)
        dictWorkflow["listSteps"].append(dictStep)
        dictCtx["save"](sContainerId, dictWorkflow)
        return {
            "iIndex": len(dictWorkflow["listSteps"]) - 1,
            "dictStep": dictStep,
        }


def _fnRegisterStepInsert(app, dictCtx):
    """Register POST /api/steps/{id}/insert route."""

    @app.post("/api/steps/{sContainerId}/insert/{iPosition}")
    async def fnInsertStep(
        sContainerId: str, iPosition: int,
        request: StepCreateRequest,
    ):
        dictCtx["require"]()
        dictWorkflow = fdictRequireWorkflow(
            dictCtx["workflows"], sContainerId)
        dictStep = fdictStepFromRequest(request)
        workflowManager.fnInsertStep(
            dictWorkflow, iPosition, dictStep)
        dictCtx["save"](sContainerId, dictWorkflow)
        return {
            "iIndex": iPosition,
            "dictStep": dictStep,
            "listSteps": dictWorkflow["listSteps"],
        }


def _fnRegisterStepUpdate(app, dictCtx):
    """Register PUT /api/steps/{id}/{index} route."""

    @app.put("/api/steps/{sContainerId}/{iStepIndex}")
    async def fnUpdateStep(
        sContainerId: str, iStepIndex: int,
        request: StepUpdateRequest,
    ):
        dictCtx["require"]()
        dictWorkflow = fdictRequireWorkflow(
            dictCtx["workflows"], sContainerId)
        try:
            workflowManager.fnUpdateStep(
                dictWorkflow, iStepIndex,
                fdictFilterNonNone(request.model_dump()),
            )
        except IndexError as error:
            raise HTTPException(404, str(error))
        dictCtx["save"](sContainerId, dictWorkflow)
        return dictWorkflow["listSteps"][iStepIndex]


def _fnRegisterStepDelete(app, dictCtx):
    """Register DELETE /api/steps/{id}/{index} route."""

    @app.delete("/api/steps/{sContainerId}/{iStepIndex}")
    async def fnDeleteStep(sContainerId: str, iStepIndex: int):
        dictCtx["require"]()
        dictWorkflow = fdictRequireWorkflow(
            dictCtx["workflows"], sContainerId)
        try:
            workflowManager.fnDeleteStep(
                dictWorkflow, iStepIndex)
        except IndexError as error:
            raise HTTPException(404, str(error))
        dictCtx["save"](sContainerId, dictWorkflow)
        return {
            "bSuccess": True,
            "listSteps": dictWorkflow["listSteps"],
        }


def _fnRegisterStepReorder(app, dictCtx):
    """Register POST /api/steps/{id}/reorder route."""

    @app.post("/api/steps/{sContainerId}/reorder")
    async def fnReorderSteps(
        sContainerId: str, request: ReorderRequest
    ):
        dictCtx["require"]()
        dictWorkflow = fdictRequireWorkflow(
            dictCtx["workflows"], sContainerId)
        try:
            workflowManager.fnReorderStep(
                dictWorkflow,
                request.iFromIndex, request.iToIndex,
            )
        except IndexError as error:
            raise HTTPException(400, str(error))
        dictCtx["save"](sContainerId, dictWorkflow)
        return {"listSteps": dictWorkflow["listSteps"]}


def fnRegisterAll(app, dictCtx):
    """Register all step CRUD routes."""
    _fnRegisterStepsList(app, dictCtx)
    _fnRegisterStepGet(app, dictCtx)
    _fnRegisterStepCreate(app, dictCtx)
    _fnRegisterStepInsert(app, dictCtx)
    _fnRegisterStepUpdate(app, dictCtx)
    _fnRegisterStepDelete(app, dictCtx)
    _fnRegisterStepReorder(app, dictCtx)
