"""Step CRUD route handlers."""

__all__ = ["fnRegisterAll"]

import posixpath

from fastapi import HTTPException, Request

from .. import stepRename, workflowManager
from ..actionCatalog import ffnAgentAction
from ..fileStatusManager import fbMaybeAutoArchive
from vaibify.reproducibility.levelGates import fiProofLevel
from ..routeContext import (
    fdictCarryARefusalBackInsteadOfRaising,
    fdictRequireLaneTupleForCommit,
    ffilesForWorkflow,
    fdictCommitWorkflowSave,
    fgenericRunWorkerUnderTheDrain,
)
from ..routeScope import (
    S_CARRIER_MODE_A_SYNCHRONOUS,
    S_CARRIER_MODE_B_LOCK_HELD,
    ffnDeclareCarrierMode,
)
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
    async def flistGetSteps(sContainerId: str):
        return workflowManager.flistExtractStepNames(
            fdictRequireWorkflow(
                dictCtx["workflows"], sContainerId)
        )

    @app.get("/api/steps/{sContainerId}/validate")
    async def fdictValidateReferences(sContainerId: str):
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
    @ffnAgentAction("resolve-commands")
    async def fdictResolveCommands(sContainerId: str):
        dictWorkflow = fdictRequireWorkflow(
            dictCtx["workflows"], sContainerId)
        return workflowManager.fdictResolveWorkflowCommands(
            dictWorkflow, dictCtx["variables"](sContainerId),
        )

    @app.get("/api/steps/{sContainerId}/by-label/{sLabel}")
    async def fdictResolveStepLabel(sContainerId: str, sLabel: str):
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
    async def fdictHandleGetStep(sContainerId: str, iStepIndex: int):
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

    @ffnAgentAction("create-step")
    @app.post("/api/steps/{sContainerId}/create")
    @ffnDeclareCarrierMode(S_CARRIER_MODE_A_SYNCHRONOUS)
    async def fdictHandleCreateStep(
        sContainerId: str, request: StepCreateRequest,
        requestHttp: Request,
    ):
        dictCtx["require"](sContainerId)
        dictWorkflow = fdictRequireWorkflow(
            dictCtx["workflows"], sContainerId)
        _fnRaiseIfAtStepCap(dictWorkflow)
        dictStep = _fdictStepFromRequestChecked(dictWorkflow, request)
        dictWorkflow["listSteps"].append(dictStep)
        fdictCommitWorkflowSave(
            dictCtx, sContainerId, dictWorkflow, requestHttp,
            "The step creation",
        )
        iIndex = len(dictWorkflow["listSteps"]) - 1
        iCount = len(dictWorkflow["listSteps"])
        bShouldWarn = _fbShouldWarnHundred(dictWorkflow, iCount)
        if bShouldWarn:
            dictWorkflow["bWarnedHundredSteps"] = True
            fdictCommitWorkflowSave(
                dictCtx, sContainerId, dictWorkflow, requestHttp,
                "The hundred-step warning flag",
            )
        return {
            "iIndex": iIndex,
            "dictStep": fdictStepWithLabel(dictWorkflow, iIndex),
            "bShouldWarnHundredSteps": bShouldWarn,
        }


def _fnRegisterStepInsert(app, dictCtx):
    """Register POST /api/steps/{id}/insert route."""

    @ffnAgentAction("insert-step")
    @app.post("/api/steps/{sContainerId}/insert/{iPosition}")
    @ffnDeclareCarrierMode(S_CARRIER_MODE_A_SYNCHRONOUS)
    async def fdictInsertStep(
        sContainerId: str, iPosition: int,
        request: StepCreateRequest, requestHttp: Request,
    ):
        dictCtx["require"](sContainerId)
        dictWorkflow = fdictRequireWorkflow(
            dictCtx["workflows"], sContainerId)
        _fnRaiseIfAtStepCap(dictWorkflow)
        dictStep = _fdictStepFromRequestChecked(dictWorkflow, request)
        workflowManager.fnInsertStep(
            dictWorkflow, iPosition, dictStep)
        fdictCommitWorkflowSave(
            dictCtx, sContainerId, dictWorkflow, requestHttp,
            "The step insertion",
        )
        iCount = len(dictWorkflow["listSteps"])
        bShouldWarn = _fbShouldWarnHundred(dictWorkflow, iCount)
        if bShouldWarn:
            dictWorkflow["bWarnedHundredSteps"] = True
            fdictCommitWorkflowSave(
                dictCtx, sContainerId, dictWorkflow, requestHttp,
                "The hundred-step warning flag",
            )
        return {
            "iIndex": iPosition,
            "dictStep": fdictStepWithLabel(dictWorkflow, iPosition),
            "listSteps": flistStepsWithLabels(dictWorkflow),
            "bShouldWarnHundredSteps": bShouldWarn,
        }


def _fnRegisterStepUpdate(app, dictCtx):
    """Register PUT /api/steps/{id}/{index} route."""

    @ffnAgentAction("update-step")
    @app.put("/api/steps/{sContainerId}/{iStepIndex}")
    @ffnDeclareCarrierMode(
        S_CARRIER_MODE_A_SYNCHRONOUS, S_CARRIER_MODE_B_LOCK_HELD,
    )
    async def fdictUpdateStep(
        sContainerId: str, iStepIndex: int,
        request: StepUpdateRequest, requestHttp: Request,
    ):
        dictCtx["require"](sContainerId)
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
        await _fnUpdateThenArchiveUnderTheDrain(
            dictCtx, sContainerId, dictWorkflow, iStepIndex,
            dictUpdates, requestHttp,
        )
        dictResult = fdictStepWithLabel(dictWorkflow, iStepIndex)
        dictResult["sWorkflowFingerprint"] = (
            workflowManager.fsComputeWorkflowFingerprint(dictWorkflow)
        )
        # Post-save exact-source fingerprint: the client adopts it as
        # its acknowledged value, so its own edit never trips the
        # dispatch freshness gate.
        dictResult["sExactSourceFingerprint"] = dictWorkflow.get(
            "_sSourceFingerprint", "",
        )
        return dictResult


async def _fnUpdateThenArchiveUnderTheDrain(
    dictCtx, sContainerId, dictWorkflow, iStepIndex, dictUpdates,
    requestHttp,
):
    """Read the level, apply the edit, save, and auto-archive as one.

    Three container-reaching operations that only mean anything
    together. ``fiProofLevel`` runs the L1/L2/L3 gates, which hash the
    repo through the container once a workflow is L2, so the
    level-BEFORE read is itself a guarded operation and not a free
    lookup. ``fbMaybeAutoArchive`` then reads the level AGAIN and
    archives only on the before/after transition across 1 — so the two
    readings must see the same world apart from this edit. Dropping the
    drain between them lets another session's write move the level in
    the gap, and the promotion is then detected, or missed, for a change
    the researcher never made.

    So the whole sequence is one mode-(b) worker. The SAVE inside it
    still goes through ``fdictCommitWorkflowSave`` rather than a bare
    write: that is what records the ``file-write`` journal entry whose
    expected and prior hashes let the probe prove afterwards whether the
    bytes landed, and losing it would leave a crash mid-save
    unresolvable. Its mode-(a) record nests inside the held drain, which
    is why this route declares both modes.
    """
    def fnUpdateSaveAndArchive(supervisor=None):
        del supervisor
        iLevelBefore = fiProofLevel(
            dictWorkflow,
            ffilesForWorkflow(dictCtx, sContainerId, dictWorkflow),
        )
        try:
            workflowManager.fnUpdateStep(
                dictWorkflow, iStepIndex, dictUpdates,
            )
        except IndexError as error:
            raise HTTPException(404, str(error)) from error
        fdictCommitWorkflowSave(
            dictCtx, sContainerId, dictWorkflow, requestHttp,
            "The step update",
        )
        fbMaybeAutoArchive(
            dictCtx["docker"], sContainerId, dictWorkflow,
            iStepIndex, iLevelBefore,
        )

    def fdictRunTheUpdate(supervisor=None):
        del supervisor
        return fdictCarryARefusalBackInsteadOfRaising(
            fnUpdateSaveAndArchive,
        )

    await fgenericRunWorkerUnderTheDrain(
        sContainerId, fdictRunTheUpdate, "update-step", requestHttp,
    )


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
    _fnRequireTestCategoryConfirm(dictStep, dictUpdates, bConfirm)


def _flistSelectDroppedTestCategories(dictStep, dictNewTests):
    """Return the category keys whose commands this update would erase.

    ``fnUpdateStep`` assigns ``dictTests`` wholesale, so a caller that
    sends one category to declare it silently deletes the others. The
    loss does not surface as an error either: the aggregators read a
    missing category with ``.get(sKey, {})``, and the derivation then
    marks the vanished axis ``unnecessary`` -- which counts GREEN. A
    destructive edit that reads as a pass is exactly the one that has
    to be confirmed rather than inferred.
    """
    dictOldTests = dictStep.get("dictTests", {})
    listDropped = []
    for sKey, _sVerificationKey in workflowManager.T_STRUCTURED_TEST_GROUPS:
        if not dictOldTests.get(sKey, {}).get("saCommands"):
            continue
        if not dictNewTests.get(sKey, {}).get("saCommands"):
            listDropped.append(sKey)
    return listDropped


def _fnRequireTestCategoryConfirm(dictStep, dictUpdates, bConfirm):
    """Refuse a dictTests edit that drops a category's commands."""
    if bConfirm:
        return
    dictNewTests = dictUpdates.get("dictTests")
    if dictNewTests is None:
        return
    listDropped = _flistSelectDroppedTestCategories(dictStep, dictNewTests)
    if not listDropped:
        return
    raise HTTPException(
        400,
        "Refusing to drop test commands for "
        + ", ".join(sorted(listDropped))
        + " without bConfirmDestructive=true. dictTests is replaced "
        "wholesale, not merged, so send every category you mean to "
        "keep in the same update.",
    )


def _fnRegisterStepDelete(app, dictCtx):
    """Register DELETE /api/steps/{id}/{index} route."""

    @ffnAgentAction("delete-step")
    @app.delete("/api/steps/{sContainerId}/{iStepIndex}")
    @ffnDeclareCarrierMode(S_CARRIER_MODE_A_SYNCHRONOUS)
    async def fdictDeleteStep(
        sContainerId: str, iStepIndex: int, requestHttp: Request,
    ):
        dictCtx["require"](sContainerId)
        dictWorkflow = fdictRequireWorkflow(
            dictCtx["workflows"], sContainerId)
        try:
            workflowManager.fnDeleteStep(
                dictWorkflow, iStepIndex)
        except IndexError as error:
            raise HTTPException(404, str(error))
        fdictCommitWorkflowSave(
            dictCtx, sContainerId, dictWorkflow, requestHttp,
            "The step deletion",
        )
        return {
            "bSuccess": True,
            "listSteps": flistStepsWithLabels(dictWorkflow),
        }


def _fnRegisterStepReorder(app, dictCtx):
    """Register POST /api/steps/{id}/reorder route."""

    @ffnAgentAction("reorder-steps")
    @app.post("/api/steps/{sContainerId}/reorder")
    @ffnDeclareCarrierMode(S_CARRIER_MODE_A_SYNCHRONOUS)
    async def fdictReorderSteps(
        sContainerId: str, request: ReorderRequest,
        requestHttp: Request,
    ):
        dictCtx["require"](sContainerId)
        dictWorkflow = fdictRequireWorkflow(
            dictCtx["workflows"], sContainerId)
        try:
            workflowManager.fnReorderStep(
                dictWorkflow,
                request.iFromIndex, request.iToIndex,
            )
        except IndexError as error:
            raise HTTPException(400, str(error))
        fdictCommitWorkflowSave(
            dictCtx, sContainerId, dictWorkflow, requestHttp,
            "The step reorder",
        )
        return {"listSteps": flistStepsWithLabels(dictWorkflow)}


def _fnRegisterInputDataAdd(app, dictCtx):
    """Register POST /api/steps/{id}/{index}/input-data route."""

    @ffnAgentAction("add-input-data-file")
    @app.post("/api/steps/{sContainerId}/{iStepIndex}/input-data")
    @ffnDeclareCarrierMode(S_CARRIER_MODE_A_SYNCHRONOUS)
    async def fdictAddInputDataFile(
        sContainerId: str, iStepIndex: int,
        request: InputDataAddRequest, requestHttp: Request,
    ):
        dictCtx["require"](sContainerId)
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
            fdictCommitWorkflowSave(
                dictCtx, sContainerId, dictWorkflow, requestHttp,
                "The input-data declaration",
            )
        return {
            "bAdded": bAdded,
            "dictStep": fdictStepWithLabel(dictWorkflow, iStepIndex),
        }


def _fnRegisterStepRename(app, dictCtx):
    """Register POST /api/steps/{id}/{index}/rename route."""

    @ffnAgentAction("rename-step")
    @app.post("/api/steps/{sContainerId}/{iStepIndex}/rename")
    @ffnDeclareCarrierMode(S_CARRIER_MODE_B_LOCK_HELD)
    async def fdictRenameStep(
        sContainerId: str, iStepIndex: int,
        request: StepRenameRequest, requestHttp: Request,
    ):
        dictCtx["require"](sContainerId)
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
            dictPlan["listScriptWarnings"] = (
                await _flistScanScriptsUnderTheDrain(
                    dictCtx, sContainerId, dictWorkflow, dictPlan,
                    requestHttp,
                )
            )
            return dictPlan
        dictReport = await _fdictApplyRenameUnderTheDrain(
            dictCtx, sContainerId, dictWorkflow, iStepIndex, dictPlan,
            requestHttp,
        )
        dictReport["dictStep"] = fdictStepWithLabel(
            dictWorkflow, iStepIndex)
        dictReport["sExactSourceFingerprint"] = dictWorkflow.get(
            "_sSourceFingerprint", "",
        )
        dictReport["sWorkflowFingerprint"] = (
            workflowManager.fsComputeWorkflowFingerprint(dictWorkflow)
        )
        return dictReport


async def _flistScanScriptsUnderTheDrain(
    dictCtx, sContainerId, dictWorkflow, dictPlan, requestHttp,
):
    """Run the dry run's script scan under the drain.

    A preview, and still a carrier: the scan greps every declared script
    for the old directory name, one general exec per script, and the gate
    treats an exec as mutating because a primitive handed command text
    cannot know what the text does. Left outside a carrier the preview
    would be refused on the enforced branch, and the researcher would be
    unable to look before leaping. Mode (b) rather than (a) because the
    loop is one container round-trip per declared script.
    """
    def fdictScanTheScripts(supervisor=None):
        del supervisor
        return fdictCarryARefusalBackInsteadOfRaising(
            lambda: stepRename.flistScanScriptsForOldName(
                dictCtx["docker"], sContainerId, dictWorkflow, dictPlan,
            ),
        )

    return await fgenericRunWorkerUnderTheDrain(
        sContainerId, fdictScanTheScripts, "rename-step-preview", requestHttp,
    )


async def _fdictApplyRenameUnderTheDrain(
    dictCtx, sContainerId, dictWorkflow, iStepIndex, dictPlan,
    requestHttp,
):
    """Run the rename cascade and its save under one held drain.

    Mode (b), and the whole cascade in ONE worker, because the cascade is
    already a transaction: the directory moves, then the marker and the
    manifest follow, and a failure in either is undone by moving the
    bytes back. Dropping the drain anywhere inside that would let another
    session's write land between the move and its undo, so the undo would
    put back a directory that is no longer what it moved.

    Which failures are carried and which poison is decided from
    ``fdictApplyStepRename``'s source, not from the shape of its
    exceptions:

    * Every ``ValueError`` reaching the 409 is a refusal DECIDED with the
      container untouched. Three of them fire before anything moves (no
      project repo, no workflow slug, the target directory already
      exists); the fourth -- an unreadable verification marker -- fires
      after the move but is reached only through the cascade's own
      rollback, which puts the directory back before the error escapes.
      So a researcher who picks a taken name gets a 409 and a working
      container, not a quarantine and an instruction to run ``vaibify
      reconcile``.
    * Every 500 is genuinely mid-effect -- a ``git mv`` that exited
      non-zero, or a manifest rewrite that failed after the marker had
      already moved -- so it propagates and poisons, which is what the
      quarantine is for.
    * ``StepRenameSplitError`` is the case that needs both. The bytes
      moved and could not be put back, so the workflow now records where
      they actually are and that MUST be persisted or the nonconforming
      warning that leads the researcher to the repair is lost on reload.
      The save therefore runs inside the worker, under this same
      admission, and only then does the error escape to poison the
      record. Save first, poison second -- in that order, or the
      researcher loses the only pointer to the split.
    """
    filesRepo = ffilesForWorkflow(dictCtx, sContainerId, dictWorkflow)

    def fdictRenameThenSave():
        try:
            dictReport = stepRename.fdictApplyStepRename(
                dictCtx["docker"], sContainerId, filesRepo,
                dictWorkflow, iStepIndex, dictPlan,
                dictCtx["paths"].get(sContainerId, ""),
            )
        except stepRename.StepRenameSplitError as error:
            dictCtx["save"](sContainerId, dictWorkflow)
            raise HTTPException(500, str(error)) from error
        except ValueError as error:
            raise HTTPException(409, str(error)) from error
        except RuntimeError as error:
            raise HTTPException(500, str(error)) from error
        dictCtx["save"](sContainerId, dictWorkflow)
        return dictReport

    def fdictApplyTheRename(supervisor=None):
        del supervisor
        return fdictCarryARefusalBackInsteadOfRaising(fdictRenameThenSave)

    return await fgenericRunWorkerUnderTheDrain(
        sContainerId, fdictApplyTheRename, "rename-step", requestHttp,
    )


def _fnRegisterAlignDirectories(app, dictCtx):
    """Register POST /api/steps/{id}/align-directories route."""

    @ffnAgentAction("align-step-directories")
    @app.post("/api/steps/{sContainerId}/align-directories")
    @ffnDeclareCarrierMode(S_CARRIER_MODE_B_LOCK_HELD)
    async def fdictHandleAlignStepDirectories(
        sContainerId: str, requestHttp: Request,
    ):
        """Migrate every nonconforming step to the slug contract.

        Each step runs the full rename cascade (git mv, marker,
        manifest, path rewrites) with its name unchanged. Steps whose
        names violate the contract's alphabet are reported skipped —
        they need a rename first — rather than failing the batch.
        """
        dictCtx["require"](sContainerId)
        dictWorkflow = fdictRequireWorkflow(
            dictCtx["workflows"], sContainerId)
        if _fbRefuseWhilePipelineTaskLive(
            dictCtx["pipelineTasks"], sContainerId,
        ):
            raise HTTPException(
                409, "A pipeline action is running in this "
                "container — wait for it to finish before aligning "
                "directories.")
        dictBatch = await _fdictAlignDirectoriesUnderTheDrain(
            dictCtx, sContainerId, dictWorkflow, requestHttp,
        )
        return {
            "listAligned": dictBatch["listAligned"],
            "listSkipped": dictBatch["listSkipped"],
            "listSteps": flistStepsWithLabels(dictWorkflow),
            "sWorkflowFingerprint":
                workflowManager.fsComputeWorkflowFingerprint(
                    dictWorkflow),
            "sExactSourceFingerprint": dictWorkflow.get(
                "_sSourceFingerprint", "",
            ),
        }


async def _fdictAlignDirectoriesUnderTheDrain(
    dictCtx, sContainerId, dictWorkflow, requestHttp,
):
    """Run the whole alignment batch under ONE held drain.

    One worker for the batch, not one per step, and the deciding reason
    is the shared workflow dict. Every iteration rewrites the SAME
    in-memory workflow and the batch ends in a single save, so a
    per-step carrier would drop the drain between two renames of one
    workflow: another session's save landing in a gap would be silently
    overwritten by the save at the end, which by then describes a
    workflow assembled across the gap. The alternative — saving after
    every step — would multiply the container writes by the number of
    nonconforming steps to protect against a hand-over the drain
    already prevents.

    It also matches what the batch already is. Each iteration carries
    its own recovery (a failed step is reported skipped and the batch
    continues), so per-step records would buy N journal entries for one
    logical migration and no extra recoverability.

    Nothing is carried back, because nothing refuses: the loop catches
    ``ValueError``, ``RuntimeError`` and ``StepRenameSplitError`` itself
    and reports them per step. Anything that escapes it is unexpected,
    leaves the batch half-applied, and poisons — which is correct, and
    is why this calls the carrier directly rather than wrapping a
    carry-back that could never fire.
    """
    from .. import commitCarrier
    dictLaneTuple = fdictRequireLaneTupleForCommit(
        requestHttp, sContainerId, "Aligning the step directories",
    )

    def fdictAlignEveryStep(supervisor=None):
        del supervisor
        return _fdictAlignEveryNonconformingStep(
            dictCtx, sContainerId, dictWorkflow,
        )

    dictOutcome = await commitCarrier.fdictRunLockHeldMutation(
        requestHttp.app.state, dictLaneTuple["sContainerName"],
        sContainerId, dictLaneTuple, "helper", "align-step-directories",
        fdictAlignEveryStep,
    )
    return dictOutcome["result"]


def _fdictAlignEveryNonconformingStep(dictCtx, sContainerId, dictWorkflow):
    """Apply the alignment cascade to each nonconforming step, then save.

    Synchronous by carrier requirement: mode (b) runs its worker in a
    thread, which cannot await, so the cascade is called directly where
    the route used to wrap each iteration in ``asyncio.to_thread``.
    """
    filesRepo = ffilesForWorkflow(dictCtx, sContainerId, dictWorkflow)
    listAligned, listSkipped = [], []
    bSplitRecorded = False
    for iIndex, dictStep in enumerate(dictWorkflow.get("listSteps", [])):
        if fbStepDirectoryConforms(dictStep):
            continue
        sLabel = dictStep.get("sLabel") or f"step {iIndex}"
        try:
            dictPlan = stepRename.fdictPlanDirectoryAlignment(
                dictWorkflow, iIndex)
            if not dictPlan["bDirectoryRenamed"]:
                continue
            stepRename.fdictApplyStepRename(
                dictCtx["docker"], sContainerId, filesRepo,
                dictWorkflow, iIndex, dictPlan,
                dictCtx["paths"].get(sContainerId, ""),
            )
            listAligned.append({
                "sLabel": sLabel,
                "sOldDirectory": dictPlan["sOldDirectory"],
                "sNewDirectory": dictPlan["sNewDirectory"],
            })
        except stepRename.StepRenameSplitError as error:
            # The batch continues, but this step's workflow entry
            # was rewritten to match disk and must be saved.
            bSplitRecorded = True
            listSkipped.append({"sLabel": sLabel, "sReason": str(error)})
        except (ValueError, RuntimeError) as error:
            listSkipped.append({"sLabel": sLabel, "sReason": str(error)})
    if listAligned or bSplitRecorded:
        dictCtx["save"](sContainerId, dictWorkflow)
    return {"listAligned": listAligned, "listSkipped": listSkipped}


def _fnRegisterDeclareNoInputData(app, dictCtx):
    """Register POST /api/steps/{id}/declare-no-input-data route."""

    @ffnAgentAction("declare-no-input-data")
    @app.post("/api/steps/{sContainerId}/declare-no-input-data")
    @ffnDeclareCarrierMode(S_CARRIER_MODE_A_SYNCHRONOUS)
    async def fdictDeclareNoInputData(
        sContainerId: str, requestHttp: Request,
    ):
        dictCtx["require"](sContainerId)
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
            fdictCommitWorkflowSave(
                dictCtx, sContainerId, dictWorkflow, requestHttp,
                "The no-input-data declaration",
            )
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
