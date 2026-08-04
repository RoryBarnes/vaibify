"""Settings and log route handlers."""

__all__ = ["fnRegisterAll"]

import asyncio
import posixpath

from fastapi import HTTPException, Request
from fastapi.responses import Response

from .. import workflowManager
from ..routeContext import fdictRequireLaneTupleForCommit
from ..routeScope import (
    S_CARRIER_MODE_A_SYNCHRONOUS,
    fnDeclareCarrierMode,
)
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
    @fnDeclareCarrierMode(S_CARRIER_MODE_A_SYNCHRONOUS)
    async def fnUpdateSettings(
        sContainerId: str,
        request: WorkflowSettingsRequest,
        requestHttp: Request,
    ):
        dictCtx["require"]()
        dictWorkflow = fdictRequireWorkflow(
            dictCtx["workflows"], sContainerId)
        _fnCommitSettingsUpdate(
            dictCtx, sContainerId, dictWorkflow,
            fdictFilterNonNone(request.model_dump()), requestHttp,
        )
        return fdictExtractSettings(dictWorkflow)


def _fnCommitSettingsUpdate(
    dictCtx, sContainerId, dictWorkflow, dictUpdates, requestHttp,
):
    """Commit the settings save through carrier mode (a) (design §8).

    The synchronous-commit mode: the write-ahead ``file-write`` record
    carries the pre-update and intended post-update fingerprints of
    ``project.json`` (the save path's own serialization authority), so
    a crash inside the commit window is provable afterwards — the
    probe either finds the prior bytes (nothing landed), the expected
    bytes (the write landed; a pending migration may shift them, which
    reads as quarantine-until-reconciled, never as a silent guess).
    """
    from .. import commitCarrier
    appState = requestHttp.app.state
    dictLaneTuple = fdictRequireLaneTupleForCommit(
        requestHttp, sContainerId, "The settings save",
    )
    sPriorFingerprint = workflowManager.fsComputeWorkflowFingerprint(
        dictWorkflow)
    for sKey, value in dictUpdates.items():
        dictWorkflow[sKey] = value
    commitCarrier.fdictCommitSynchronousMutation(
        appState, dictLaneTuple["sContainerName"], sContainerId,
        dictLaneTuple, "file-write",
        dictCtx["paths"].get(sContainerId, "") or "project.json",
        lambda: dictCtx["save"](sContainerId, dictWorkflow),
        {
            "sDockerContainerId": sContainerId,
            "sExpectedSha256": (
                workflowManager.fsComputeWorkflowFingerprint(dictWorkflow)
            ),
            "sPriorSha256": sPriorFingerprint,
        },
    )


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
            baContent = await asyncio.to_thread(
                dictCtx["docker"].fbaFetchFile,
                sContainerId, sLogPath, iMaxBytes=None,
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
