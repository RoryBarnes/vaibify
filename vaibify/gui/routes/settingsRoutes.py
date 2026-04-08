"""Settings and log route handlers."""

__all__ = ["fnRegisterAll"]

import posixpath

from fastapi import HTTPException
from fastapi.responses import Response

from .. import workflowManager
from ..pipelineServer import (
    WORKSPACE_ROOT,
    WorkflowSettingsRequest,
    fdictExtractSettings,
    fdictFilterNonNone,
    fdictRequireWorkflow,
    flistQueryDirectory,
    fnValidatePathWithinRoot,
    _fsSanitizeServerError,
)


def _fnRegisterSettingsGet(app, dictCtx):
    """Register GET /api/settings route."""

    @app.get("/api/settings/{sContainerId}")
    async def fnGetSettings(sContainerId: str):
        return fdictExtractSettings(
            fdictRequireWorkflow(
                dictCtx["workflows"], sContainerId)
        )


def _fnRegisterSettingsPut(app, dictCtx):
    """Register PUT /api/settings route."""

    @app.put("/api/settings/{sContainerId}")
    async def fnUpdateSettings(
        sContainerId: str,
        request: WorkflowSettingsRequest,
    ):
        dictCtx["require"]()
        dictWorkflow = fdictRequireWorkflow(
            dictCtx["workflows"], sContainerId)
        for sKey, value in fdictFilterNonNone(
            request.model_dump()
        ).items():
            dictWorkflow[sKey] = value
        dictCtx["save"](sContainerId, dictWorkflow)
        return fdictExtractSettings(dictWorkflow)


def _fnRegisterLogRoutes(app, dictCtx):
    """Register log listing and fetching routes."""

    @app.get("/api/logs/{sContainerId}")
    async def fnListLogs(sContainerId: str):
        dictCtx["require"]()
        sLogsDir = posixpath.join(
            WORKSPACE_ROOT, workflowManager.VAIBIFY_LOGS_DIR
        )
        listEntries = flistQueryDirectory(
            dictCtx["docker"], sContainerId, sLogsDir
        )
        listLogs = [
            e["sName"] for e in listEntries
            if e["sName"].endswith(".log")
        ]
        return sorted(listLogs, reverse=True)

    @app.get("/api/logs/{sContainerId}/{sLogFilename}")
    async def fnGetLogContent(
        sContainerId: str, sLogFilename: str
    ):
        dictCtx["require"]()
        sLogsDir = posixpath.join(
            WORKSPACE_ROOT, workflowManager.VAIBIFY_LOGS_DIR
        )
        sLogPath = posixpath.join(sLogsDir, sLogFilename)
        fnValidatePathWithinRoot(sLogPath, sLogsDir)
        try:
            baContent = dictCtx["docker"].fbaFetchFile(
                sContainerId, sLogPath
            )
            return Response(
                content=baContent, media_type="text/plain"
            )
        except Exception as error:
            raise HTTPException(
                404, f"Log not found: "
                f"{_fsSanitizeServerError(str(error))}")


def fnRegisterAll(app, dictCtx):
    """Register all settings and log routes."""
    _fnRegisterSettingsGet(app, dictCtx)
    _fnRegisterSettingsPut(app, dictCtx)
    _fnRegisterLogRoutes(app, dictCtx)
