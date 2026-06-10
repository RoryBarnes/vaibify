"""AICS level readiness route handlers.

Exposes the per-workflow Level 2 readiness rollup that the AICS tab
consumes, and the AI Declaration starter-template generator that the
"Generate template" button on the new step kind invokes.

Both endpoints are agent-safe: ``check-l2-readiness`` is read-only,
and ``generate-ai-declaration-template`` only writes a new file (it
refuses to overwrite an existing one, so it cannot lose researcher
content). Committing the declaration remains a user-only action via
the standard ``sUser`` badge on the step.
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
    S_DEFAULT_DECLARATION_FILENAME,
    fbDeclarationFileExists,
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


def _fsValidateRelativePath(sRelativePath):
    """Return a sanitized, non-escaping repo-relative path or raise 400.

    Rejects absolute paths, ``..`` segments, and empty values so a
    malicious agent invocation cannot write outside the project repo.
    The check is symmetric with the workflow-file path validation
    already enforced at load time.
    """
    sClean = (sRelativePath or "").strip()
    if not sClean:
        return S_DEFAULT_DECLARATION_FILENAME
    if os.path.isabs(sClean):
        raise HTTPException(
            400, "sRelativePath must be repo-relative",
        )
    listParts = sClean.replace("\\", "/").split("/")
    if any(sPart == ".." for sPart in listParts):
        raise HTTPException(
            400, "sRelativePath may not contain '..'",
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


def fnRegisterAll(app, dictCtx):
    """Register the AICS level readiness routes."""
    _fnRegisterLevel2Readiness(app, dictCtx)
    _fnRegisterGenerateTemplate(app, dictCtx)
