"""Sync, reproducibility, and DAG route handlers."""

__all__ = ["fnRegisterAll"]

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


_S_ZENODO_REMEDIATION = (
    "Token stored but validation failed. "
    "Check that the token has deposit scopes."
)
_S_OVERLEAF_REMEDIATION = (
    "Overleaf rejected the token or project ID. Check that the "
    "project ID matches the one in your Overleaf URL, and that "
    "the saved git authentication token (Account Settings -> "
    "Git integration on overleaf.com) has push access to this "
    "project. Use the Sync menu to replace the saved token if "
    "needed."
)
_I_OVERLEAF_STDERR_MAX = 200


async def _fbRunOverleafValidation(
    syncDispatcher, connectionDocker, sContainerId, sProjectId,
):
    """Run Overleaf credential validation in a worker thread.

    Returns ``(bSuccess, sStderr)`` so the caller can surface the
    underlying git message in the remediation toast.
    """
    if not sProjectId:
        return (False, "")
    return await asyncio.to_thread(
        syncDispatcher.fbValidateOverleafCredentials,
        connectionDocker, sContainerId, sProjectId,
    )


async def _ftRunServiceValidation(
    syncDispatcher, sService, connectionDocker,
    sContainerId, sProjectId,
):
    """Dispatch to service-specific validator.

    Returns ``(bPass, sDetail)`` where ``sDetail`` is an optional
    service-supplied error fragment (empty for services that don't
    capture one).
    """
    if sService == "zenodo":
        bPass = await asyncio.to_thread(
            syncDispatcher.fbValidateZenodoToken,
            connectionDocker, sContainerId,
        )
        return (bPass, "")
    if sService == "overleaf":
        return await _fbRunOverleafValidation(
            syncDispatcher, connectionDocker,
            sContainerId, sProjectId,
        )
    return (True, "")


def _fsOverleafRemediation(sStderrFragment):
    """Embed a trimmed git error into the Overleaf remediation text."""
    sTrimmed = (sStderrFragment or "").strip()
    if not sTrimmed:
        return _S_OVERLEAF_REMEDIATION
    if len(sTrimmed) > _I_OVERLEAF_STDERR_MAX:
        sTrimmed = sTrimmed[:_I_OVERLEAF_STDERR_MAX].rstrip() + "..."
    return (
        f"Overleaf rejected the token: {sTrimmed}. "
        "On overleaf.com, open Account Settings and find the Git "
        "integration section to generate a git authentication token "
        "(not your login password). Paste that token above."
    )


def _fsServiceRemediation(sService, sDetail=""):
    """Return the user-facing remediation message for a service."""
    if sService == "overleaf":
        return _fsOverleafRemediation(sDetail)
    return _S_ZENODO_REMEDIATION


def _fnCleanupCredential(
    syncDispatcher, connectionDocker, sContainerId, sService,
):
    """Delete a just-stored credential after validation failure."""
    sTokenName = f"{sService}_token"
    if sService == "overleaf":
        _fnCleanupOverleafHostCredential(sTokenName)
        return
    try:
        syncDispatcher.fnDeleteCredentialFromContainer(
            connectionDocker, sContainerId, sTokenName,
        )
    except Exception:
        pass


def _fnCleanupOverleafHostCredential(sTokenName):
    """Remove the Overleaf token from the host keyring."""
    from vaibify.config.secretManager import fnDeleteSecret
    try:
        fnDeleteSecret(sTokenName, "keyring")
    except Exception:
        pass


def _fdictStoreCredentialSafely(
    syncDispatcher, dictCtx, sContainerId, sService, sToken,
):
    """Try to store; return a failure dict or None on success."""
    try:
        _fnDispatchStore(
            syncDispatcher, dictCtx, sContainerId, sService, sToken,
        )
    except Exception as error:
        return {
            "bConnected": False,
            "sMessage": f"Failed to store credentials: {error}",
        }
    return None


def _fnDispatchStore(
    syncDispatcher, dictCtx, sContainerId, sService, sToken,
):
    """Route Overleaf to the host keyring; others to the container."""
    if sService == "overleaf":
        from vaibify.config.secretManager import fnStoreSecret
        fnStoreSecret("overleaf_token", sToken, "keyring")
        return
    syncDispatcher.fnStoreCredentialInContainer(
        dictCtx["docker"], sContainerId,
        f"{sService}_token", sToken,
    )


async def _fdictStoreValidateCredential(
    dictCtx, sContainerId, sService, sToken, sProjectId,
):
    """Store credential, verify connectivity, validate; clean up on failure."""
    from .. import syncDispatcher
    dictStoreFail = _fdictStoreCredentialSafely(
        syncDispatcher, dictCtx, sContainerId, sService, sToken,
    )
    if dictStoreFail is not None:
        return dictStoreFail
    dictResult = await _fdictValidateStoredCredential(
        dictCtx, sContainerId, sService, sProjectId,
    )
    if not dictResult["bConnected"]:
        _fnCleanupCredential(
            syncDispatcher, dictCtx["docker"],
            sContainerId, sService,
        )
    return dictResult


async def _fdictValidateStoredCredential(
    dictCtx, sContainerId, sService, sProjectId,
):
    """Validate an already-stored credential without deleting it on failure."""
    from .. import syncDispatcher
    dictResult = syncDispatcher.fdictCheckConnectivity(
        dictCtx["docker"], sContainerId, sService)
    if not dictResult["bConnected"]:
        return dictResult
    bValid, sDetail = await _ftRunServiceValidation(
        syncDispatcher, sService, dictCtx["docker"],
        sContainerId, sProjectId,
    )
    if bValid:
        return {"bConnected": True, "sMessage": "Connected"}
    return {
        "bConnected": False,
        "sMessage": _fsServiceRemediation(sService, sDetail),
    }


def _fnRegisterSyncRoutes(app, dictCtx):
    """Register sync status, file list, setup, and check routes."""
    from .. import syncDispatcher

    @app.get("/api/sync/{sContainerId}/status")
    async def fnGetSyncStatus(sContainerId: str):
        dictWorkflow = fdictRequireWorkflow(
            dictCtx["workflows"], sContainerId)
        return workflowManager.fdictGetSyncStatus(dictWorkflow)

    @app.get("/api/sync/{sContainerId}/files")
    async def fnGetSyncFiles(
        sContainerId: str, sService: str = "",
    ):
        dictWorkflow = fdictRequireWorkflow(
            dictCtx["workflows"], sContainerId)
        dictSync = workflowManager.fdictGetSyncStatus(
            dictWorkflow)
        dictVars = dictCtx["variables"](sContainerId)
        sWorkflowRoot = dictCtx["workflowDir"](sContainerId)
        return syncDispatcher.flistCollectOutputFiles(
            dictWorkflow, dictSync, dictVars,
            sService or None, sWorkflowRoot,
        )

    @app.post("/api/sync/{sContainerId}/setup")
    async def fnSetupConnection(
        sContainerId: str, request: SyncSetupRequest,
    ):
        dictCtx["require"]()
        syncDispatcher.fnValidateServiceName(request.sService)
        dictResult = await _fdictRunSetup(
            dictCtx, sContainerId, request,
        )
        if dictResult.get("bConnected"):
            _fnPersistServiceSettings(
                dictCtx, sContainerId, request,
            )
        return dictResult

    async def _fdictRunSetup(dictCtx, sContainerId, request):
        if request.sToken:
            return await _fdictStoreValidateCredential(
                dictCtx, sContainerId, request.sService,
                request.sToken, request.sProjectId or "",
            )
        if _fbServiceHasStoredCredential(request.sService):
            return await _fdictValidateStoredCredential(
                dictCtx, sContainerId, request.sService,
                request.sProjectId or "",
            )
        return syncDispatcher.fdictCheckConnectivity(
            dictCtx["docker"], sContainerId, request.sService)

    def _fnPersistServiceSettings(dictCtx, sContainerId, request):
        if request.sService != "overleaf" or not request.sProjectId:
            return
        dictWorkflow = fdictRequireWorkflow(
            dictCtx["workflows"], sContainerId)
        dictWorkflow["sOverleafProjectId"] = request.sProjectId
        dictCtx["save"](sContainerId, dictWorkflow)

    @app.get("/api/sync/{sContainerId}/check/{sService}")
    async def fnCheckConnection(
        sContainerId: str, sService: str,
    ):
        dictCtx["require"]()
        syncDispatcher.fnValidateServiceName(sService)
        dictResult = syncDispatcher.fdictCheckConnectivity(
            dictCtx["docker"], sContainerId, sService)
        if dictResult["bConnected"] and sService == "overleaf":
            dictResult = _fdictRequireOverleafProjectId(
                dictCtx, sContainerId, dictResult,
            )
        return dictResult

    def _fdictRequireOverleafProjectId(
        dictCtx, sContainerId, dictResult,
    ):
        dictWorkflow = fdictRequireWorkflow(
            dictCtx["workflows"], sContainerId)
        if not dictWorkflow.get("sOverleafProjectId"):
            return {
                "bConnected": False,
                "sMessage":
                    "Overleaf project ID not set. Enter the "
                    "project ID to connect.",
            }
        return dictResult

    @app.get("/api/sync/{sContainerId}/has-credential/{sService}")
    async def fnHasCredential(sContainerId: str, sService: str):
        dictCtx["require"]()
        syncDispatcher.fnValidateServiceName(sService)
        return {
            "bHasCredential": _fbServiceHasStoredCredential(sService),
        }


def _fbServiceHasStoredCredential(sService):
    """Return True when the host keyring already has this service's token."""
    from vaibify.config.secretManager import fbSecretExists
    if sService != "overleaf":
        return False
    return fbSecretExists("overleaf_token", "keyring")


def _fnRegisterDag(app, dictCtx):
    """Register DAG visualization endpoint."""
    from .. import syncDispatcher

    @app.get("/api/workflow/{sContainerId}/dag")
    async def fnGetDag(sContainerId: str):
        dictCtx["require"]()
        dictWorkflow = fdictRequireWorkflow(
            dictCtx["workflows"], sContainerId
        )
        dictCachedDeps = dictCtx.get(
            "sourceCodeDeps", {}).get(sContainerId)
        iExit, result = await asyncio.to_thread(
            syncDispatcher.ftResultGenerateDagSvg,
            dictCtx["docker"], sContainerId, dictWorkflow,
            dictCachedDeps,
        )
        if iExit != 0:
            raise HTTPException(500, f"DAG failed: {result}")
        return Response(
            content=result, media_type="image/svg+xml")


def _fnRegisterDagExport(app, dictCtx):
    """Register DAG export endpoint in configurable format."""
    from .. import syncDispatcher

    @app.get("/api/workflow/{sContainerId}/dag/export")
    async def fnExportDag(
        sContainerId: str, sFormat: str = "svg",
    ):
        dictCtx["require"]()
        dictWorkflow = fdictRequireWorkflow(
            dictCtx["workflows"], sContainerId
        )
        dictCachedDeps = dictCtx.get(
            "sourceCodeDeps", {}).get(sContainerId)
        iExit, result = await asyncio.to_thread(
            syncDispatcher.ftResultExportDag,
            dictCtx["docker"], sContainerId,
            dictWorkflow, sFormat, dictCachedDeps,
        )
        if iExit != 0:
            raise HTTPException(500, f"DAG export failed: {result}")
        sMediaType = syncDispatcher.DICT_DAG_MEDIA_TYPES.get(
            sFormat.lower().lstrip("."), "application/octet-stream"
        )
        sFilename = f"dag.{sFormat.lower().lstrip('.')}"
        return Response(
            content=result,
            media_type=sMediaType,
            headers={
                "Content-Disposition": (
                    f'attachment; filename="{sFilename}"'
                )
            },
        )


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
    _fnRegisterDagExport(app, dictCtx)
    _fnRegisterDatasetDownload(app, dictCtx)
