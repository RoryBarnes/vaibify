"""Sync, reproducibility, and DAG route handlers."""

import asyncio
import posixpath

from fastapi import HTTPException
from fastapi.responses import Response

from .. import workflowManager
from ..pipelineRunner import fsShellQuote
from ..pipelineServer import (
    DatasetDownloadRequest,
    GitAddFileRequest,
    SyncPushRequest,
    SyncSetupRequest,
    WORKSPACE_ROOT,
    fdictRequireWorkflow,
    fnValidatePathWithinRoot,
)
from .scriptRoutes import _fnStoreCommitHash


def _fdictBuildOverleafArgs(dictWorkflow):
    """Extract Overleaf push arguments from workflow settings."""
    return {
        "sProjectId": dictWorkflow.get(
            "sOverleafProjectId", ""),
        "sTargetDirectory": dictWorkflow.get(
            "sOverleafFigureDirectory", "figures"),
        "dictWorkflow": dictWorkflow,
        "sGithubBaseUrl": dictWorkflow.get(
            "sGithubBaseUrl", ""),
        "sDoi": dictWorkflow.get("sZenodoDoi", ""),
        "sTexFilename": dictWorkflow.get(
            "sTexFilename", "main.tex"),
    }


def _fnRegisterOverleafPush(app, dictCtx):
    """Register POST /api/overleaf/{id}/push endpoint."""
    from .. import syncDispatcher

    @app.post("/api/overleaf/{sContainerId}/push")
    async def fnOverleafPush(
        sContainerId: str, request: SyncPushRequest,
    ):
        dictCtx["require"]()
        dictWorkflow = fdictRequireWorkflow(
            dictCtx["workflows"], sContainerId)
        dictOverleafArgs = _fdictBuildOverleafArgs(dictWorkflow)
        iExit, sOut = await asyncio.to_thread(
            syncDispatcher.ftResultPushToOverleaf,
            dictCtx["docker"], sContainerId,
            request.listFilePaths, **dictOverleafArgs,
        )
        dictResult = syncDispatcher.fdictSyncResult(iExit, sOut)
        if not dictResult["bSuccess"]:
            return dictResult
        workflowManager.fnUpdateSyncStatus(
            dictWorkflow, request.listFilePaths, "Overleaf")
        dictCtx["save"](sContainerId, dictWorkflow)
        return dictResult


def _fnRegisterZenodoArchive(app, dictCtx):
    """Register POST /api/zenodo/{id}/archive endpoint."""
    from .. import syncDispatcher

    @app.post("/api/zenodo/{sContainerId}/archive")
    async def fnZenodoArchive(
        sContainerId: str, request: SyncPushRequest,
    ):
        dictCtx["require"]()
        dictWorkflow = fdictRequireWorkflow(
            dictCtx["workflows"], sContainerId)
        iExit, sOut = await asyncio.to_thread(
            syncDispatcher.ftResultArchiveToZenodo,
            dictCtx["docker"], sContainerId,
            "zenodo", request.listFilePaths,
        )
        dictResult = syncDispatcher.fdictSyncResult(iExit, sOut)
        if not dictResult["bSuccess"]:
            return dictResult
        workflowManager.fnUpdateSyncStatus(
            dictWorkflow, request.listFilePaths, "Zenodo")
        dictCtx["save"](sContainerId, dictWorkflow)
        return dictResult


def _fnRegisterGithubPush(app, dictCtx):
    """Register POST /api/github/{id}/push endpoint."""
    from .. import syncDispatcher

    @app.post("/api/github/{sContainerId}/push")
    async def fnGithubPush(
        sContainerId: str, request: SyncPushRequest,
    ):
        dictCtx["require"]()
        dictWorkflow = fdictRequireWorkflow(
            dictCtx["workflows"], sContainerId)
        sWorkdir = posixpath.dirname(
            dictCtx["paths"].get(sContainerId, ""))
        iExit, sOut = await asyncio.to_thread(
            syncDispatcher.ftResultPushToGithub,
            dictCtx["docker"], sContainerId,
            request.listFilePaths, request.sCommitMessage,
            sWorkdir,
        )
        dictResult = syncDispatcher.fdictSyncResult(iExit, sOut)
        if not dictResult["bSuccess"]:
            return dictResult
        sCommitHash = (
            sOut.strip().splitlines()[-1] if sOut else "")
        workflowManager.fnUpdateSyncStatus(
            dictWorkflow, request.listFilePaths, "Github")
        _fnStoreCommitHash(
            dictWorkflow, request.listFilePaths, sCommitHash)
        dictCtx["save"](sContainerId, dictWorkflow)
        dictResult["sCommitHash"] = sCommitHash
        return dictResult


def _fnRegisterGithubAddFile(app, dictCtx):
    """Register POST /api/github/{id}/add-file endpoint."""
    from .. import syncDispatcher

    @app.post("/api/github/{sContainerId}/add-file")
    async def fnGithubAddFile(
        sContainerId: str, request: GitAddFileRequest,
    ):
        dictCtx["require"]()
        dictWorkflow = fdictRequireWorkflow(
            dictCtx["workflows"], sContainerId)
        sWorkdir = posixpath.dirname(
            dictCtx["paths"].get(sContainerId, ""))
        fnValidatePathWithinRoot(
            posixpath.normpath(
                posixpath.join(sWorkdir, request.sFilePath)
            ),
            WORKSPACE_ROOT,
        )
        iExit, sOut = await asyncio.to_thread(
            syncDispatcher.ftResultAddFileToGithub,
            dictCtx["docker"], sContainerId,
            request.sFilePath, request.sCommitMessage,
            sWorkdir,
        )
        dictResult = syncDispatcher.fdictSyncResult(iExit, sOut)
        if dictResult["bSuccess"]:
            sHash = (
                sOut.strip().splitlines()[-1] if sOut else "")
            dictResult["sCommitHash"] = sHash
        return dictResult


def _fnRegisterSyncRoutes(app, dictCtx):
    """Register sync status, file list, setup, and check routes."""
    from .. import syncDispatcher

    @app.get("/api/sync/{sContainerId}/status")
    async def fnGetSyncStatus(sContainerId: str):
        dictWorkflow = fdictRequireWorkflow(
            dictCtx["workflows"], sContainerId)
        return workflowManager.fdictGetSyncStatus(dictWorkflow)

    @app.get("/api/sync/{sContainerId}/files")
    async def fnGetSyncFiles(sContainerId: str):
        dictWorkflow = fdictRequireWorkflow(
            dictCtx["workflows"], sContainerId)
        dictSync = workflowManager.fdictGetSyncStatus(
            dictWorkflow)
        return syncDispatcher.flistCollectOutputFiles(
            dictWorkflow, dictSync)

    @app.post("/api/sync/{sContainerId}/setup")
    async def fnSetupConnection(
        sContainerId: str, request: SyncSetupRequest,
    ):
        dictCtx["require"]()
        syncDispatcher.fnValidateServiceName(request.sService)
        if request.sToken:
            sTokenName = f"{request.sService}_token"
            try:
                syncDispatcher.fnStoreCredentialInContainer(
                    dictCtx["docker"], sContainerId,
                    sTokenName, request.sToken,
                )
            except Exception as error:
                return {
                    "bConnected": False,
                    "sMessage":
                        f"Failed to store credentials: {error}",
                }
        dictResult = syncDispatcher.fdictCheckConnectivity(
            dictCtx["docker"], sContainerId, request.sService)
        if (dictResult["bConnected"]
                and request.sService == "zenodo"):
            bValid = await asyncio.to_thread(
                syncDispatcher.fbValidateZenodoToken,
                dictCtx["docker"], sContainerId,
            )
            if not bValid:
                dictResult["bConnected"] = False
                dictResult["sMessage"] = (
                    "Token stored but validation failed. "
                    "Check that the token has deposit scopes."
                )
        return dictResult

    @app.get("/api/sync/{sContainerId}/check/{sService}")
    async def fnCheckConnection(
        sContainerId: str, sService: str,
    ):
        dictCtx["require"]()
        syncDispatcher.fnValidateServiceName(sService)
        return syncDispatcher.fdictCheckConnectivity(
            dictCtx["docker"], sContainerId, sService)


def _fnRegisterDag(app, dictCtx):
    """Register DAG visualization endpoint."""
    from .. import syncDispatcher

    @app.get("/api/workflow/{sContainerId}/dag")
    async def fnGetDag(sContainerId: str):
        dictCtx["require"]()
        dictWorkflow = fdictRequireWorkflow(
            dictCtx["workflows"], sContainerId
        )
        iExit, result = await asyncio.to_thread(
            syncDispatcher.ftResultGenerateDagSvg,
            dictCtx["docker"], sContainerId, dictWorkflow,
        )
        if iExit != 0:
            raise HTTPException(500, f"DAG failed: {result}")
        return Response(
            content=result, media_type="image/svg+xml")


def _fnRegisterDatasetDownload(app, dictCtx):
    """Register Zenodo dataset download endpoint."""
    from .. import syncDispatcher

    @app.post("/api/zenodo/{sContainerId}/download")
    async def fnDownloadDataset(
        sContainerId: str, request: DatasetDownloadRequest,
    ):
        dictCtx["require"]()
        iExit, sOut = await asyncio.to_thread(
            syncDispatcher.ftResultDownloadDataset,
            dictCtx["docker"], sContainerId,
            "zenodo", request.iRecordId,
            request.sFileName, request.sDestination,
        )
        if iExit != 0:
            raise HTTPException(
                500, f"Download failed: {sOut}")
        return {"bSuccess": True}


def fnRegisterAll(app, dictCtx):
    """Register all sync and reproducibility routes."""
    _fnRegisterOverleafPush(app, dictCtx)
    _fnRegisterZenodoArchive(app, dictCtx)
    _fnRegisterGithubPush(app, dictCtx)
    _fnRegisterGithubAddFile(app, dictCtx)
    _fnRegisterSyncRoutes(app, dictCtx)
    _fnRegisterDag(app, dictCtx)
    _fnRegisterDatasetDownload(app, dictCtx)
