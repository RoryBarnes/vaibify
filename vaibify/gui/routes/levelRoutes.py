"""AICS level readiness route handlers.

Exposes the per-workflow Level 2 readiness rollup that the AICS tab
consumes, the AI Declaration starter-template generator that the
"Generate template" button on the new step kind invokes, and the
AI Declaration add-step route that appends the interactive
declaration step to the end of the active workflow.

All three endpoints are agent-safe: ``check-l2-readiness`` is
read-only, ``generate-ai-declaration-template`` only writes a new
file (it refuses to overwrite an existing one, so it cannot lose
researcher content), and ``add-ai-declaration-step`` refuses when a
declaration step already exists. Committing the declaration remains
a user-only action via the standard ``sUser`` badge on the step.
"""

__all__ = ["fnRegisterAll"]

import os

from fastapi import HTTPException
from pydantic import BaseModel
from typing import Optional

from ..actionCatalog import fnAgentAction
from ..pipelineServer import (
    _fsSanitizeServerError,
    fdictRequireWorkflow,
)
from ..routeContext import ffilesForWorkflow
from ...reproducibility.aiDeclarationStep import (
    S_DEFAULT_DECLARATION_DIRECTORY,
    S_DEFAULT_DECLARATION_FILENAME,
    fbDeclarationFileExists,
    fbStepIsAiDeclaration,
    fdictBuildAiDeclarationStep,
    fnWriteDeclarationTemplate,
)
from ...reproducibility.levelGates import (
    fdictLevel2Gaps,
    fiAICSLevel,
)


class AiDeclarationTemplateRequest(BaseModel):
    """Body for the generate-template route.

    ``sRelativePath`` is optional; when absent the default
    ``AI_USAGE.md`` at the project repo root is used.
    """
    sRelativePath: Optional[str] = None


class AiDeclarationAddStepRequest(BaseModel):
    """Body for the add-step route; every override is optional.

    Defaults come from ``fdictBuildAiDeclarationStep``: sName
    "AI Declaration", sDirectory "aiDeclaration", sDeclarationFile
    "AI_USAGE.md".
    """
    sName: Optional[str] = None
    sDirectory: Optional[str] = None
    sDeclarationFile: Optional[str] = None


def _fnRejectEscapingPath(sCleanPath, sFieldName):
    """Raise 400 when a path is absolute or contains a ``..`` segment.

    Rejecting these keeps a malicious agent invocation from writing
    outside the project repo. The check is symmetric with the
    workflow-file path validation already enforced at load time.
    """
    if os.path.isabs(sCleanPath):
        raise HTTPException(
            400, f"{sFieldName} must be repo-relative",
        )
    listParts = sCleanPath.replace("\\", "/").split("/")
    if any(sPart == ".." for sPart in listParts):
        raise HTTPException(
            400, f"{sFieldName} may not contain '..'",
        )


def _fsValidateRelativePath(sRelativePath):
    """Return a sanitized, non-escaping repo-relative path or raise 400."""
    sClean = (sRelativePath or "").strip()
    if not sClean:
        return S_DEFAULT_DECLARATION_FILENAME
    _fnRejectEscapingPath(sClean, "sRelativePath")
    return sClean


def _fsValidateNewStepDirectory(dictWorkflow, sDirectory):
    """Return a validated, unique step directory or raise 400/409.

    Mirrors the load-time step-directory boundary rules (repo-relative,
    no ``..`` escape) and additionally requires uniqueness among the
    workflow's existing step directories so per-step state keys in
    state.json cannot collide.
    """
    sClean = (sDirectory or "").strip() or S_DEFAULT_DECLARATION_DIRECTORY
    _fnRejectEscapingPath(sClean, "sDirectory")
    setExistingDirectories = {
        (dictStep.get("sDirectory") or "").strip()
        for dictStep in dictWorkflow.get("listSteps", []) or []
        if isinstance(dictStep, dict)
    }
    if sClean in setExistingDirectories:
        raise HTTPException(
            409,
            f"Step directory '{sClean}' is already used by another "
            f"step; choose a unique sDirectory.",
        )
    return sClean


def _fsRequireProjectRepo(dictWorkflow):
    """Return the workflow's project repo path or raise 409."""
    sProjectRepo = (
        dictWorkflow.get("sProjectRepoPath") or ""
    ).strip()
    if not sProjectRepo:
        raise HTTPException(
            409,
            "Workflow has no project repo; initialize one before "
            "writing canonical artifacts.",
        )
    return sProjectRepo


def _fnRegisterLevel2Readiness(app, dictCtx):
    """Register GET /api/workflow/{sContainerId}/level2/readiness."""

    @fnAgentAction("check-l2-readiness")
    @app.get(
        "/api/workflow/{sContainerId}/level2/readiness"
    )
    async def fnLevel2Readiness(sContainerId: str):
        dictCtx["require"]()
        dictWorkflow = fdictRequireWorkflow(
            dictCtx["workflows"], sContainerId,
        )
        filesRepo = ffilesForWorkflow(
            dictCtx, sContainerId, dictWorkflow,
        )
        dictGaps = fdictLevel2Gaps(dictWorkflow, filesRepo)
        return {
            "iAICSLevel": fiAICSLevel(dictWorkflow, filesRepo),
            "dictLevel2Gaps": dictGaps,
        }


def _fnRegisterGenerateTemplate(app, dictCtx):
    """Register POST /api/workflow/{sContainerId}/ai-declaration/generate-template."""

    @fnAgentAction("generate-ai-declaration-template")
    @app.post(
        "/api/workflow/{sContainerId}"
        "/ai-declaration/generate-template"
    )
    async def fnGenerateTemplate(
        sContainerId: str,
        request: AiDeclarationTemplateRequest,
    ):
        dictCtx["require"]()
        dictWorkflow = fdictRequireWorkflow(
            dictCtx["workflows"], sContainerId,
        )
        _fsRequireProjectRepo(dictWorkflow)
        filesRepo = ffilesForWorkflow(
            dictCtx, sContainerId, dictWorkflow,
        )
        sRelative = _fsValidateRelativePath(request.sRelativePath)
        if fbDeclarationFileExists(filesRepo, sRelative):
            raise HTTPException(
                409,
                f"Declaration file already exists at '{sRelative}'; "
                f"edit it in place rather than regenerating.",
            )
        try:
            sAbsolute = fnWriteDeclarationTemplate(
                filesRepo, sRelative,
            )
        except (OSError, ValueError) as error:
            raise HTTPException(
                500,
                f"Template generation failed: "
                f"{_fsSanitizeServerError(str(error))}",
            )
        return {
            "bSuccess": True,
            "sRelativePath": sRelative,
            "sAbsolutePath": sAbsolute,
        }


def _fnRefuseDuplicateAiDeclarationStep(dictWorkflow):
    """Raise 409 when the workflow already has an ai-declaration step."""
    for dictStep in dictWorkflow.get("listSteps", []) or []:
        if fbStepIsAiDeclaration(dictStep):
            raise HTTPException(
                409,
                "Workflow already has an AI Declaration step; edit "
                "the existing step instead of adding another.",
            )


def _fdictBuildStepFromAddRequest(dictWorkflow, request):
    """Translate the optional add-step body into a validated new step."""
    sDirectory = _fsValidateNewStepDirectory(
        dictWorkflow, request.sDirectory,
    )
    sDeclarationFile = _fsValidateRelativePath(
        request.sDeclarationFile,
    )
    return fdictBuildAiDeclarationStep(
        sName=request.sName,
        sDeclarationFile=sDeclarationFile,
        sDirectory=sDirectory,
    )


def _fnRegisterAddStep(app, dictCtx):
    """Register POST /api/workflow/{sContainerId}/ai-declaration/add-step."""

    @fnAgentAction("add-ai-declaration-step")
    @app.post(
        "/api/workflow/{sContainerId}"
        "/ai-declaration/add-step"
    )
    async def fnAddAiDeclarationStep(
        sContainerId: str,
        request: AiDeclarationAddStepRequest,
    ):
        dictCtx["require"]()
        dictWorkflow = fdictRequireWorkflow(
            dictCtx["workflows"], sContainerId,
        )
        _fnRefuseDuplicateAiDeclarationStep(dictWorkflow)
        dictStep = _fdictBuildStepFromAddRequest(dictWorkflow, request)
        dictWorkflow.setdefault("listSteps", []).append(dictStep)
        dictCtx["save"](sContainerId, dictWorkflow)
        return {
            "iIndex": len(dictWorkflow["listSteps"]) - 1,
            "dictStep": dictStep,
        }


def fnRegisterAll(app, dictCtx):
    """Register the AICS level readiness routes."""
    _fnRegisterLevel2Readiness(app, dictCtx)
    _fnRegisterGenerateTemplate(app, dictCtx)
    _fnRegisterAddStep(app, dictCtx)
