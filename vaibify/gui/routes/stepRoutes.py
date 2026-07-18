"""Step CRUD route handlers."""

__all__ = ["fnRegisterAll"]

import asyncio
import posixpath

from fastapi import HTTPException

from .. import stepRename, workflowManager
from ..actionCatalog import fnAgentAction
from ..fileStatusManager import fnMaybeAutoArchive
from vaibify.reproducibility.levelGates import fiAICSLevel
from ..routeContext import ffilesForWorkflow
from ..pipelineServer import (
    InputDataAddRequest,
    ReorderRequest,
    StepCreateRequest,
    StepRenameRequest,
    StepUpdateRequest,
    _fbRefuseWhilePipelineTaskLive,
    fdictFilterNonNone,
    fdictRequireWorkflow,
    fdictStepFromRequest,
)
from ..pipelineUtils import (
    fdictStepWithLabel,
    flistStepsWithLabels,
    fbStepDirectoryConforms,
    fnRequireUniqueStepSlug,
    fsSlugFromStepName,
)


_I_STEP_COUNT_WARNING = 100
_I_STEP_COUNT_MAX = 500


def _fnRaiseIfAtStepCap(dictWorkflow):
    """Reject step adds once the workflow has hit the hard cap."""
    if len(dictWorkflow["listSteps"]) >= _I_STEP_COUNT_MAX:
        raise HTTPException(
            status_code=400,
            detail="Workflow cannot exceed 500 steps.",
        )


def _fbShouldWarnHundred(dictWorkflow, iCount):
    """Return True iff the workflow just crossed the warning threshold."""
    return (
        iCount >= _I_STEP_COUNT_WARNING
        and not dictWorkflow.get("bWarnedHundredSteps")
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
            ) + workflowManager.flistDirectoryContractWarnings(
                dictWorkflow
            )
        }

    @app.get("/api/steps/{sContainerId}/resolve-commands")
    @fnAgentAction("resolve-commands")
    async def fnResolveCommands(sContainerId: str):
        dictWorkflow = fdictRequireWorkflow(
            dictCtx["workflows"], sContainerId)
        return workflowManager.fdictResolveWorkflowCommands(
            dictWorkflow, dictCtx["variables"](sContainerId),
        )

    @app.get("/api/steps/{sContainerId}/by-label/{sLabel}")
    async def fnResolveStepLabel(sContainerId: str, sLabel: str):
        from ..pipelineUtils import fiStepIndexFromLabel
        dictWorkflow = fdictRequireWorkflow(
            dictCtx["workflows"], sContainerId)
        try:
            iIndex = fiStepIndexFromLabel(dictWorkflow, sLabel)
        except ValueError as error:
            raise HTTPException(404, str(error))
        return {"iStepIndex": iIndex, "sLabel": sLabel}


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
            dictDecorated = fdictStepWithLabel(
                dictWorkflow, iStepIndex,
            )
            dictDecorated["saResolvedOutputFiles"] = (
                workflowManager.flistResolveOutputFiles(
                    dictStep,
                    dictCtx["variables"](sContainerId),
                )
            )
            return dictDecorated
        except IndexError as error:
            raise HTTPException(404, str(error))


def _fdictStepFromRequestChecked(dictWorkflow, request):
    """Build the new step, mapping contract violations to HTTP 400.

    The slug contract (2026-07-18): the name's alphabet is validated,
    the directory's final component is derived from the name, and the
    resulting slug must be unique in the project (case-insensitive).
    """
    try:
        dictStep = fdictStepFromRequest(request)
        fnRequireUniqueStepSlug(dictWorkflow, -1, dictStep["sName"])
        return dictStep
    except ValueError as error:
        raise HTTPException(400, str(error))


def _fnRegisterStepCreate(app, dictCtx):
    """Register POST /api/steps/{id}/create route."""

    @fnAgentAction("create-step")
    @app.post("/api/steps/{sContainerId}/create")
    async def fnCreateStep(
        sContainerId: str, request: StepCreateRequest
    ):
        dictCtx["require"]()
        dictWorkflow = fdictRequireWorkflow(
            dictCtx["workflows"], sContainerId)
        _fnRaiseIfAtStepCap(dictWorkflow)
        dictStep = _fdictStepFromRequestChecked(dictWorkflow, request)
        dictWorkflow["listSteps"].append(dictStep)
        dictCtx["save"](sContainerId, dictWorkflow)
        iIndex = len(dictWorkflow["listSteps"]) - 1
        iCount = len(dictWorkflow["listSteps"])
        bShouldWarn = _fbShouldWarnHundred(dictWorkflow, iCount)
        if bShouldWarn:
            dictWorkflow["bWarnedHundredSteps"] = True
            dictCtx["save"](sContainerId, dictWorkflow)
        return {
            "iIndex": iIndex,
            "dictStep": fdictStepWithLabel(dictWorkflow, iIndex),
            "bShouldWarnHundredSteps": bShouldWarn,
        }


def _fnRegisterStepInsert(app, dictCtx):
    """Register POST /api/steps/{id}/insert route."""

    @fnAgentAction("insert-step")
    @app.post("/api/steps/{sContainerId}/insert/{iPosition}")
    async def fnInsertStep(
        sContainerId: str, iPosition: int,
        request: StepCreateRequest,
    ):
        dictCtx["require"]()
        dictWorkflow = fdictRequireWorkflow(
            dictCtx["workflows"], sContainerId)
        _fnRaiseIfAtStepCap(dictWorkflow)
        dictStep = _fdictStepFromRequestChecked(dictWorkflow, request)
        workflowManager.fnInsertStep(
            dictWorkflow, iPosition, dictStep)
        dictCtx["save"](sContainerId, dictWorkflow)
        iCount = len(dictWorkflow["listSteps"])
        bShouldWarn = _fbShouldWarnHundred(dictWorkflow, iCount)
        if bShouldWarn:
            dictWorkflow["bWarnedHundredSteps"] = True
            dictCtx["save"](sContainerId, dictWorkflow)
        return {
            "iIndex": iPosition,
            "dictStep": fdictStepWithLabel(dictWorkflow, iPosition),
            "listSteps": flistStepsWithLabels(dictWorkflow),
            "bShouldWarnHundredSteps": bShouldWarn,
        }


def _fnRegisterStepUpdate(app, dictCtx):
    """Register PUT /api/steps/{id}/{index} route."""

    @fnAgentAction("update-step")
    @app.put("/api/steps/{sContainerId}/{iStepIndex}")
    async def fnUpdateStep(
        sContainerId: str, iStepIndex: int,
        request: StepUpdateRequest,
    ):
        dictCtx["require"]()
        dictWorkflow = fdictRequireWorkflow(
            dictCtx["workflows"], sContainerId)
        _fnRequireFingerprintMatch(dictWorkflow, request.sBaseFingerprint)
        dictUpdates = _fdictExtractStepUpdates(request)
        _fnRejectContractBreakingUpdates(
            dictWorkflow, iStepIndex, dictUpdates,
        )
        _fnRequireDestructiveConfirm(
            dictWorkflow, iStepIndex, dictUpdates,
            request.bConfirmDestructive,
        )
        iLevelBefore = fiAICSLevel(
            dictWorkflow,
            ffilesForWorkflow(dictCtx, sContainerId, dictWorkflow),
        )
        try:
            workflowManager.fnUpdateStep(
                dictWorkflow, iStepIndex, dictUpdates,
            )
        except IndexError as error:
            raise HTTPException(404, str(error))
        dictCtx["save"](sContainerId, dictWorkflow)
        await fnMaybeAutoArchive(
            dictCtx["docker"], sContainerId, dictWorkflow,
            iStepIndex, iLevelBefore,
        )
        dictResult = fdictStepWithLabel(dictWorkflow, iStepIndex)
        dictResult["sWorkflowFingerprint"] = (
            workflowManager.fsComputeWorkflowFingerprint(dictWorkflow)
        )
        return dictResult


def _fdictExtractStepUpdates(request):
    """Return the non-None update dict with control fields stripped."""
    dictRaw = request.model_dump()
    dictRaw.pop("bConfirmDestructive", None)
    dictRaw.pop("sBaseFingerprint", None)
    return fdictFilterNonNone(dictRaw)


def _fnRejectContractBreakingUpdates(
    dictWorkflow, iStepIndex, dictUpdates,
):
    """Refuse edits that would break the name<->directory contract.

    A name change through the generic edit path would leave the
    directory, marker, and manifest behind — that is exactly what the
    rename cascade exists to keep together, so renames are 400'd
    toward it. A directory edit may move the parent path but its
    final component must stay the name's slug (templated directories
    are exempt, mirroring ``fbStepDirectoryConforms``).
    """
    listSteps = dictWorkflow.get("listSteps", [])
    if not 0 <= iStepIndex < len(listSteps):
        return
    dictStep = listSteps[iStepIndex]
    sCurrentName = dictStep.get("sName") or ""
    if "sName" in dictUpdates \
            and dictUpdates["sName"] != sCurrentName:
        raise HTTPException(
            400, "Renaming a step goes through the rename action "
            "(right-click → Rename, or the rename-step agent "
            "action) so its directory, verification marker, and "
            "manifest follow the name.")
    if "sDirectory" in dictUpdates:
        sDirectory = (dictUpdates["sDirectory"] or "").strip("/")
        sSlug = fsSlugFromStepName(sCurrentName)
        if sDirectory and "{" not in sDirectory \
                and posixpath.basename(sDirectory) != sSlug:
            raise HTTPException(
                400, f"The directory's final component must be "
                f"'{sSlug}' (derived from the step name); only the "
                "parent path is free.")


def _fnRequireFingerprintMatch(dictWorkflow, sBaseFingerprint):
    """Reject a stale compare-and-swap edit with 409 Conflict.

    A ``None`` fingerprint opts out (unconditional write, the legacy
    behavior). When supplied, it must equal the workflow's current
    fingerprint — otherwise a concurrent writer (the dashboard or
    another agent) has moved the workflow since the caller read it,
    and applying the edit would silently clobber that change.
    """
    if sBaseFingerprint is None:
        return
    sCurrent = workflowManager.fsComputeWorkflowFingerprint(dictWorkflow)
    if sBaseFingerprint != sCurrent:
        raise HTTPException(
            status_code=409,
            detail=(
                "Workflow changed since you read it "
                f"(expected {sBaseFingerprint[:12]}…, now "
                f"{sCurrent[:12]}…). Re-read and retry."
            ),
        )


def _fnRequireDestructiveConfirm(
    dictWorkflow, iStepIndex, dictUpdates, bConfirm,
):
    """Refuse edits that empty destructive-to-lose lists unless confirmed.

    Emptying ``saInputDataFiles`` silently disables input-staleness
    detection, the same hazard class as emptying the other two.
    """
    if bConfirm:
        return
    listSteps = dictWorkflow.get("listSteps", [])
    if iStepIndex < 0 or iStepIndex >= len(listSteps):
        return
    dictStep = listSteps[iStepIndex]
    for sKey in ("saTestCommands", "saOutputDataFiles", "saInputDataFiles"):
        listNew = dictUpdates.get(sKey)
        if listNew is None or listNew:
            continue
        if dictStep.get(sKey):
            raise HTTPException(
                400,
                f"Refusing to empty {sKey} without "
                f"bConfirmDestructive=true",
            )


def _fnRegisterStepDelete(app, dictCtx):
    """Register DELETE /api/steps/{id}/{index} route."""

    @fnAgentAction("delete-step")
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
            "listSteps": flistStepsWithLabels(dictWorkflow),
        }


def _fnRegisterStepReorder(app, dictCtx):
    """Register POST /api/steps/{id}/reorder route."""

    @fnAgentAction("reorder-steps")
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
        return {"listSteps": flistStepsWithLabels(dictWorkflow)}


def _fnRegisterInputDataAdd(app, dictCtx):
    """Register POST /api/steps/{id}/{index}/input-data route."""

    @fnAgentAction("add-input-data-file")
    @app.post("/api/steps/{sContainerId}/{iStepIndex}/input-data")
    async def fnAddInputDataFile(
        sContainerId: str, iStepIndex: int,
        request: InputDataAddRequest,
    ):
        dictCtx["require"]()
        dictWorkflow = fdictRequireWorkflow(
            dictCtx["workflows"], sContainerId)
        listSteps = dictWorkflow.get("listSteps", [])
        if not 0 <= iStepIndex < len(listSteps):
            raise HTTPException(404, f"Step {iStepIndex} out of range")
        sPath = (request.sPath or "").strip()
        sWarning = workflowManager._fsCheckInputPathBoundary(
            sPath, f"Step{iStepIndex + 1:02d}", "saInputDataFiles",
        )
        if not sPath or sWarning:
            raise HTTPException(400, sWarning or "sPath is required")
        dictStep = listSteps[iStepIndex]
        listInputs = dictStep.setdefault("saInputDataFiles", [])
        bAdded = sPath not in listInputs
        if bAdded:
            listInputs.append(sPath)
            dictCtx["save"](sContainerId, dictWorkflow)
        return {
            "bAdded": bAdded,
            "dictStep": fdictStepWithLabel(dictWorkflow, iStepIndex),
        }


def _fnRegisterStepRename(app, dictCtx):
    """Register POST /api/steps/{id}/{index}/rename route."""

    @fnAgentAction("rename-step")
    @app.post("/api/steps/{sContainerId}/{iStepIndex}/rename")
    async def fnRenameStep(
        sContainerId: str, iStepIndex: int,
        request: StepRenameRequest,
    ):
        dictCtx["require"]()
        dictWorkflow = fdictRequireWorkflow(
            dictCtx["workflows"], sContainerId)
        if _fbRefuseWhilePipelineTaskLive(
            dictCtx["pipelineTasks"], sContainerId,
        ):
            raise HTTPException(
                409, "A pipeline action is running in this "
                "container — wait for it to finish before renaming "
                "a step.")
        _fnRequireFingerprintMatch(
            dictWorkflow, request.sBaseFingerprint)
        try:
            dictPlan = stepRename.fdictPlanStepRename(
                dictWorkflow, iStepIndex, request.sNewName)
        except IndexError as error:
            raise HTTPException(404, str(error))
        except ValueError as error:
            raise HTTPException(400, str(error))
        if request.bDryRun:
            dictPlan["listScriptWarnings"] = await asyncio.to_thread(
                stepRename.flistScanScriptsForOldName,
                dictCtx["docker"], sContainerId, dictWorkflow,
                dictPlan,
            )
            return dictPlan
        filesRepo = ffilesForWorkflow(
            dictCtx, sContainerId, dictWorkflow)
        try:
            dictReport = await asyncio.to_thread(
                stepRename.fdictApplyStepRename,
                dictCtx["docker"], sContainerId, filesRepo,
                dictWorkflow, iStepIndex, dictPlan,
                dictWorkflow.get("sPath", ""),
            )
        except ValueError as error:
            raise HTTPException(409, str(error))
        except RuntimeError as error:
            raise HTTPException(500, str(error))
        dictCtx["save"](sContainerId, dictWorkflow)
        dictReport["dictStep"] = fdictStepWithLabel(
            dictWorkflow, iStepIndex)
        dictReport["sWorkflowFingerprint"] = (
            workflowManager.fsComputeWorkflowFingerprint(dictWorkflow)
        )
        return dictReport


def _fnRegisterAlignDirectories(app, dictCtx):
    """Register POST /api/steps/{id}/align-directories route."""

    @fnAgentAction("align-step-directories")
    @app.post("/api/steps/{sContainerId}/align-directories")
    async def fnAlignStepDirectories(sContainerId: str):
        """Migrate every nonconforming step to the slug contract.

        Each step runs the full rename cascade (git mv, marker,
        manifest, path rewrites) with its name unchanged. Steps whose
        names violate the contract's alphabet are reported skipped —
        they need a rename first — rather than failing the batch.
        """
        dictCtx["require"]()
        dictWorkflow = fdictRequireWorkflow(
            dictCtx["workflows"], sContainerId)
        if _fbRefuseWhilePipelineTaskLive(
            dictCtx["pipelineTasks"], sContainerId,
        ):
            raise HTTPException(
                409, "A pipeline action is running in this "
                "container — wait for it to finish before aligning "
                "directories.")
        filesRepo = ffilesForWorkflow(
            dictCtx, sContainerId, dictWorkflow)
        listAligned, listSkipped = [], []
        for iIndex, dictStep in enumerate(
            dictWorkflow.get("listSteps", [])
        ):
            if fbStepDirectoryConforms(dictStep):
                continue
            sLabel = dictStep.get("sLabel") or f"step {iIndex}"
            try:
                dictPlan = stepRename.fdictPlanDirectoryAlignment(
                    dictWorkflow, iIndex)
                if not dictPlan["bDirectoryRenamed"]:
                    continue
                await asyncio.to_thread(
                    stepRename.fdictApplyStepRename,
                    dictCtx["docker"], sContainerId, filesRepo,
                    dictWorkflow, iIndex, dictPlan,
                    dictWorkflow.get("sPath", ""),
                )
                listAligned.append({
                    "sLabel": sLabel,
                    "sOldDirectory": dictPlan["sOldDirectory"],
                    "sNewDirectory": dictPlan["sNewDirectory"],
                })
            except (ValueError, RuntimeError) as error:
                listSkipped.append({
                    "sLabel": sLabel, "sReason": str(error),
                })
        if listAligned:
            dictCtx["save"](sContainerId, dictWorkflow)
        return {
            "listAligned": listAligned,
            "listSkipped": listSkipped,
            "listSteps": flistStepsWithLabels(dictWorkflow),
            "sWorkflowFingerprint":
                workflowManager.fsComputeWorkflowFingerprint(
                    dictWorkflow),
        }


def _fnRegisterDeclareNoInputData(app, dictCtx):
    """Register POST /api/steps/{id}/declare-no-input-data route."""

    @fnAgentAction("declare-no-input-data")
    @app.post("/api/steps/{sContainerId}/declare-no-input-data")
    async def fnDeclareNoInputData(sContainerId: str):
        dictCtx["require"]()
        dictWorkflow = fdictRequireWorkflow(
            dictCtx["workflows"], sContainerId)
        listDeclared = []
        for iIndex, dictStep in enumerate(
            dictWorkflow.get("listSteps", [])
        ):
            if dictStep.get("saInputDataFiles"):
                continue
            if dictStep.get("bNoInputData"):
                continue
            dictStep["bNoInputData"] = True
            listDeclared.append(iIndex)
        if listDeclared:
            dictCtx["save"](sContainerId, dictWorkflow)
        return {"listDeclaredStepIndices": listDeclared}


def fnRegisterAll(app, dictCtx):
    """Register all step CRUD routes."""
    _fnRegisterStepsList(app, dictCtx)
    _fnRegisterStepGet(app, dictCtx)
    _fnRegisterStepCreate(app, dictCtx)
    _fnRegisterStepInsert(app, dictCtx)
    _fnRegisterInputDataAdd(app, dictCtx)
    _fnRegisterDeclareNoInputData(app, dictCtx)
    _fnRegisterStepRename(app, dictCtx)
    _fnRegisterAlignDirectories(app, dictCtx)
    _fnRegisterStepUpdate(app, dictCtx)
    _fnRegisterStepDelete(app, dictCtx)
    _fnRegisterStepReorder(app, dictCtx)
