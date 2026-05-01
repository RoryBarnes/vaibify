"""Sync, reproducibility, and DAG route handlers."""

__all__ = ["fnRegisterAll"]

import asyncio
import os
import posixpath

from fastapi import HTTPException
from fastapi.responses import Response

from .. import workflowManager
from ..actionCatalog import fnAgentAction
from ..pipelineRunner import fsShellQuote
from ..pipelineServer import (
    DatasetDownloadRequest,
    GitAddFileRequest,
    OverleafDiffRequest,
    SyncPushRequest,
    SyncSetupRequest,
    SyncTrackingRequest,
    WORKSPACE_ROOT,
    ZenodoMetadataRequest,
    fdictRequireWorkflow,
    fnValidatePathWithinRoot,
)
from .scriptRoutes import _fnStoreCommitHash


_S_ISOLATION_BLOCK_ERROR = "isolation-mode-blocks-network"
_S_ISOLATION_BLOCK_MESSAGE = (
    "Container is in isolation mode (no network). "
    "Disable in vaibify.yml: networkIsolation: false, then rebuild."
)


def _fdictIsolationBlockedResponse():
    """Return the structured response for an isolation-blocked call."""
    return {
        "sError": _S_ISOLATION_BLOCK_ERROR,
        "sMessage": _S_ISOLATION_BLOCK_MESSAGE,
    }


def _fnRequireNetworkAccess(sContainerId):
    """Raise HTTP 409 when the container is running with --network none.

    Network-isolated containers cannot reach Overleaf, Zenodo, or any
    other external API. Without this guard, the user clicks a sync
    button and waits 30 seconds for a DNS timeout before seeing a
    generic error. Audit finding F-R-08.
    """
    from vaibify.docker.containerManager import (
        fbContainerIsNetworkIsolated,
    )
    if fbContainerIsNetworkIsolated(sContainerId):
        raise HTTPException(
            status_code=409,
            detail=_fdictIsolationBlockedResponse(),
        )


def _fnValidateOverleafFilePaths(listFilePaths):
    """Reject any file path outside WORKSPACE_ROOT or with NUL bytes.

    Raises HTTP 400 when a caller submits a path that would exfiltrate
    host files (e.g. ``/etc/passwd``) through the push or diff flow.
    The existing HTTP 403 from ``fnValidatePathWithinRoot`` is
    translated to 400 here so the GUI treats the request as
    input-validation error and surfaces a clear message.
    """
    if listFilePaths is None:
        return
    for sFilePath in listFilePaths:
        if not isinstance(sFilePath, str) or sFilePath == "":
            raise HTTPException(
                status_code=400,
                detail="File path must be a non-empty string.",
            )
        if "\x00" in sFilePath:
            raise HTTPException(
                status_code=400,
                detail="File path must not contain null bytes.",
            )
        try:
            fnValidatePathWithinRoot(sFilePath, WORKSPACE_ROOT)
        except HTTPException as error:
            raise HTTPException(
                status_code=400,
                detail="File path must be within workspace root.",
            ) from error


def _fnValidateGithubPushPaths(listFilePaths, sWorkdir):
    """Validate paths submitted to the GitHub push endpoint.

    Accepts workdir-relative paths (the common case) and absolute
    paths. Each is resolved against sWorkdir before being checked
    against WORKSPACE_ROOT so a payload like
    ``{"listFilePaths": ["../../etc/passwd"]}`` is rejected at the
    route layer, before any git subprocess runs.
    """
    if listFilePaths is None:
        return
    for sFilePath in listFilePaths:
        if not isinstance(sFilePath, str) or sFilePath == "":
            raise HTTPException(
                status_code=400,
                detail="File path must be a non-empty string.",
            )
        if "\x00" in sFilePath:
            raise HTTPException(
                status_code=400,
                detail="File path must not contain null bytes.",
            )
        if sFilePath.startswith("/"):
            sAbs = sFilePath
        else:
            sAbs = posixpath.normpath(
                posixpath.join(sWorkdir or WORKSPACE_ROOT, sFilePath)
            )
        try:
            fnValidatePathWithinRoot(sAbs, WORKSPACE_ROOT)
        except HTTPException as error:
            raise HTTPException(
                status_code=400,
                detail="File path must be within workspace root.",
            ) from error


def _fnValidateOverleafTargetDirectory(sTargetDirectory):
    """Reject target directories that escape the Overleaf repo root.

    Mirrors ``overleafSync.fnValidateTargetDirectory`` so a malicious
    diff or push request fails at the HTTP layer before any token is
    fetched or container script runs. ``None`` is tolerated because
    the push endpoint's field is optional.
    """
    if sTargetDirectory is None:
        return
    if sTargetDirectory == "":
        return
    if "\x00" in sTargetDirectory:
        raise HTTPException(
            status_code=400,
            detail="Target directory must not contain null bytes.",
        )
    sFirst = sTargetDirectory[0]
    if sFirst == "/" or sFirst == "\\":
        raise HTTPException(
            status_code=400,
            detail="Target directory must not start with a slash.",
        )
    for sSegment in sTargetDirectory.split("/"):
        if sSegment == "..":
            raise HTTPException(
                status_code=400,
                detail="Target directory must not contain '..' segments.",
            )


def _fdictBuildOverleafArgs(dictWorkflow, sTargetDirectory):
    """Extract Overleaf push arguments from workflow settings."""
    return {
        "sProjectId": dictWorkflow.get(
            "sOverleafProjectId", ""),
        "sTargetDirectory": sTargetDirectory,
        "dictWorkflow": dictWorkflow,
        "sGithubBaseUrl": dictWorkflow.get(
            "sGithubBaseUrl", ""),
        "sDoi": dictWorkflow.get("sZenodoDoi", ""),
        "sTexFilename": dictWorkflow.get(
            "sTexFilename", "main.tex"),
    }


def _fsResolveTargetDirectory(request, dictWorkflow):
    """Return the effective target dir, persisting a new selection."""
    sRequested = getattr(request, "sTargetDirectory", None)
    if sRequested:
        dictWorkflow["sOverleafFigureDirectory"] = sRequested
        return sRequested
    return dictWorkflow.get("sOverleafFigureDirectory", "figures")


def _fsCapturePreMirrorSha(sProjectId):
    """Return the mirror's HEAD SHA before the push, refreshing if absent."""
    if not sProjectId:
        return ""
    from ..syncDispatcher import ftRefreshOverleafMirror
    from vaibify.reproducibility import overleafMirror
    listEntries = overleafMirror.flistListMirrorTree(sProjectId)
    if not listEntries:
        bSuccess, _ = ftRefreshOverleafMirror(sProjectId)
        if not bSuccess:
            return ""
    return overleafMirror.fsReadMirrorHeadSha(sProjectId)


def _fdictCollectPostPushDigests(
    sProjectId, listLocalPaths, sTargetDirectory,
):
    """Map each local path to its post-push mirror digest."""
    from vaibify.reproducibility import overleafMirror
    dictRemoteBlobs = overleafMirror.fdictIndexMirrorBlobs(sProjectId)
    dictDigests = {}
    for sLocalPath in listLocalPaths:
        sBasename = os.path.basename(sLocalPath)
        sRemotePath = (
            posixpath.join(sTargetDirectory, sBasename)
            if sTargetDirectory else sBasename
        )
        sDigest = dictRemoteBlobs.get(sRemotePath, "")
        if sDigest:
            dictDigests[sLocalPath] = sDigest
    return dictDigests


def _fnPersistPostPushDigests(
    dictWorkflow, sProjectId, listLocalPaths, sTargetDirectory,
):
    """Refresh mirror, compute digests, write them to dictSyncStatus."""
    from ..syncDispatcher import ftRefreshOverleafMirror
    bSuccess, _ = ftRefreshOverleafMirror(sProjectId)
    if not bSuccess:
        return
    dictDigests = _fdictCollectPostPushDigests(
        sProjectId, listLocalPaths, sTargetDirectory,
    )
    workflowManager.fnUpdateOverleafDigests(dictWorkflow, dictDigests)


async def _ftRunOverleafPushCall(
    syncDispatcher, connectionDocker, sContainerId,
    listFilePaths, sMirrorSha, dictOverleafArgs,
):
    """Invoke the blocking Overleaf push dispatcher in a worker thread."""
    return await asyncio.to_thread(
        syncDispatcher.ftResultPushToOverleaf,
        connectionDocker, sContainerId,
        listFilePaths, sMirrorSha=sMirrorSha,
        **dictOverleafArgs,
    )


async def _fnFinalizeOverleafPush(
    dictCtx, sContainerId, dictWorkflow, sProjectId,
    listFilePaths, sTargetDirectory,
):
    """Run the post-push bookkeeping: sync status, digests, save."""
    workflowManager.fnUpdateSyncStatus(
        dictWorkflow, listFilePaths, "Overleaf")
    await asyncio.to_thread(
        _fnPersistPostPushDigests,
        dictWorkflow, sProjectId,
        listFilePaths, sTargetDirectory,
    )
    dictCtx["save"](sContainerId, dictWorkflow)


async def _fdictRunOverleafPushFlow(
    syncDispatcher, dictCtx, sContainerId, dictWorkflow, request,
):
    """Perform the Overleaf push itself; returns the sync result dict."""
    sTargetDirectory = _fsResolveTargetDirectory(request, dictWorkflow)
    sProjectId = dictWorkflow.get("sOverleafProjectId", "")
    sMirrorSha = await asyncio.to_thread(
        _fsCapturePreMirrorSha, sProjectId)
    dictOverleafArgs = _fdictBuildOverleafArgs(
        dictWorkflow, sTargetDirectory)
    iExit, sOut = await _ftRunOverleafPushCall(
        syncDispatcher, dictCtx["docker"], sContainerId,
        request.listFilePaths, sMirrorSha, dictOverleafArgs,
    )
    dictResult = syncDispatcher.fdictSyncResult(iExit, sOut)
    sPushStatus = syncDispatcher.fsParsePushStatusFromOutput(sOut)
    if dictResult["bSuccess"] and sPushStatus == "no-changes":
        dictResult["bSuccess"] = False
        dictResult["sErrorType"] = "noChanges"
        dictResult["sMessage"] = (
            "No changes were pushed to Overleaf. The selected files "
            "match what is already in the target directory, or were "
            "not found at the paths given. Verify the target "
            "directory and file selection."
        )
    dictResult["_sProjectId"] = sProjectId
    dictResult["_sTargetDirectory"] = sTargetDirectory
    return dictResult


async def _fdictHandleOverleafPushRequest(
    syncDispatcher, dictCtx, sContainerId, request,
):
    """End-to-end Overleaf push: flow + post-push bookkeeping."""
    dictCtx["require"]()
    _fnRequireNetworkAccess(sContainerId)
    _fnValidateOverleafFilePaths(request.listFilePaths)
    _fnValidateOverleafTargetDirectory(
        getattr(request, "sTargetDirectory", None)
    )
    dictWorkflow = fdictRequireWorkflow(
        dictCtx["workflows"], sContainerId)
    dictResult = await _fdictRunOverleafPushFlow(
        syncDispatcher, dictCtx, sContainerId, dictWorkflow, request,
    )
    sProjectId = dictResult.pop("_sProjectId", "")
    sTargetDirectory = dictResult.pop("_sTargetDirectory", "")
    if not dictResult["bSuccess"]:
        return dictResult
    await _fnFinalizeOverleafPush(
        dictCtx, sContainerId, dictWorkflow, sProjectId,
        request.listFilePaths, sTargetDirectory,
    )
    return dictResult


def _fnRegisterOverleafPush(app, dictCtx):
    """Register POST /api/overleaf/{id}/push endpoint."""
    from .. import syncDispatcher

    @fnAgentAction("push-to-overleaf")
    @app.post("/api/overleaf/{sContainerId}/push")
    async def fnOverleafPush(
        sContainerId: str, request: SyncPushRequest,
    ):
        return await _fdictHandleOverleafPushRequest(
            syncDispatcher, dictCtx, sContainerId, request,
        )


def _fdictComputePostArchiveZenodoDigests(
    dictCtx, sContainerId, dictWorkflow, listFilePaths,
):
    """Return {local-path: blob-sha} for each pushed file.

    Uses ``containerGit.fdictComputeBlobShasInContainer`` scoped to
    the workflow's project repo to capture the exact content that was
    archived to Zenodo.
    """
    from .. import containerGit
    sRepo = dictWorkflow.get("sProjectRepoPath", "")
    if not sRepo:
        return {}
    listRepoRel = [
        workflowManager.fsToSyncStatusKey(sPath, sRepo)
        for sPath in listFilePaths
    ]
    dictShas = containerGit.fdictComputeBlobShasInContainer(
        dictCtx["docker"], sContainerId, listRepoRel, sWorkspace=sRepo,
    )
    return {
        sPath: dictShas.get(
            workflowManager.fsToSyncStatusKey(sPath, sRepo), "",
        )
        for sPath in listFilePaths
    }


def _fnRegisterZenodoArchive(app, dictCtx):
    """Register POST /api/zenodo/{id}/archive endpoint."""
    from .. import syncDispatcher

    @fnAgentAction("publish-to-zenodo")
    @app.post("/api/zenodo/{sContainerId}/archive")
    async def fnZenodoArchive(
        sContainerId: str, request: SyncPushRequest,
    ):
        dictCtx["require"]()
        _fnRequireNetworkAccess(sContainerId)
        dictWorkflow = fdictRequireWorkflow(
            dictCtx["workflows"], sContainerId)
        sZenodoService = dictWorkflow.get(
            "sZenodoService", "sandbox",
        )
        dictMetadata = _fdictResolveZenodoMetadataForArchive(
            dictWorkflow,
        )
        iParentDepositId = _fiReadParentDepositId(dictWorkflow)
        iExit, sOut = await asyncio.to_thread(
            syncDispatcher.ftResultArchiveToZenodo,
            dictCtx["docker"], sContainerId,
            sZenodoService, request.listFilePaths, dictMetadata,
            iParentDepositId,
        )
        dictResult = syncDispatcher.fdictSyncResult(iExit, sOut)
        if not dictResult["bSuccess"]:
            return dictResult
        dictResult.update(_fdictParseZenodoResult(sOut))
        _fnPersistZenodoPublishRecord(dictWorkflow, dictResult)
        workflowManager.fnUpdateSyncStatus(
            dictWorkflow, request.listFilePaths, "Zenodo")
        dictDigests = await asyncio.to_thread(
            _fdictComputePostArchiveZenodoDigests,
            dictCtx, sContainerId, dictWorkflow,
            request.listFilePaths,
        )
        workflowManager.fnUpdateZenodoDigests(
            dictWorkflow, dictDigests)
        dictCtx["save"](sContainerId, dictWorkflow)
        return dictResult


def _fnRegisterZenodoDeposit(app, dictCtx):
    """Register GET /api/zenodo/{id}/deposit endpoint."""

    @app.get("/api/zenodo/{sContainerId}/deposit")
    async def fnGetZenodoDeposit(sContainerId: str):
        dictCtx["require"]()
        dictWorkflow = fdictRequireWorkflow(
            dictCtx["workflows"], sContainerId,
        )
        return _fdictBuildDepositSummary(dictWorkflow)


def _fdictBuildDepositSummary(dictWorkflow):
    """Return the Zenodo deposit summary stored on the workflow."""
    return {
        "sDepositionId": dictWorkflow.get(
            "sZenodoDepositionId", ""
        ),
        "sDoi": dictWorkflow.get("sZenodoLatestDoi", ""),
        "sConceptDoi": dictWorkflow.get("sZenodoConceptDoi", ""),
        "sHtmlUrl": dictWorkflow.get("sZenodoLatestUrl", ""),
        "sService": dictWorkflow.get("sZenodoService", ""),
    }


def _fnRegisterZenodoMetadata(app, dictCtx):
    """Register GET/POST /api/zenodo/{id}/metadata endpoints."""

    @app.get("/api/zenodo/{sContainerId}/metadata")
    async def fnGetZenodoMetadata(sContainerId: str):
        dictCtx["require"]()
        dictWorkflow = fdictRequireWorkflow(
            dictCtx["workflows"], sContainerId,
        )
        dictResponse = dict(
            workflowManager.fdictGetZenodoMetadata(dictWorkflow)
        )
        dictResponse["sDefaultCreatorName"] = (
            _fsReadHostGitUserName()
        )
        return dictResponse

    @fnAgentAction("set-zenodo-metadata")
    @app.post("/api/zenodo/{sContainerId}/metadata")
    async def fnSetZenodoMetadata(
        sContainerId: str, request: ZenodoMetadataRequest,
    ):
        dictCtx["require"]()
        dictWorkflow = fdictRequireWorkflow(
            dictCtx["workflows"], sContainerId,
        )
        dictMetadata = _fdictMetadataRequestToDict(request)
        try:
            workflowManager.fnSetZenodoMetadata(
                dictWorkflow, dictMetadata,
            )
        except ValueError as error:
            raise HTTPException(
                status_code=400, detail=str(error),
            )
        dictCtx["save"](sContainerId, dictWorkflow)
        return workflowManager.fdictGetZenodoMetadata(dictWorkflow)


def _fdictMetadataRequestToDict(request):
    """Convert a ``ZenodoMetadataRequest`` into the vaibify metadata dict."""
    return {
        "sTitle": request.sTitle,
        "sDescription": request.sDescription or "",
        "listCreators": [
            {
                "sName": dictC.sName,
                "sAffiliation": dictC.sAffiliation or "",
                "sOrcid": dictC.sOrcid or "",
            }
            for dictC in (request.listCreators or [])
        ],
        "sLicense": request.sLicense or "CC-BY-4.0",
        "listKeywords": list(request.listKeywords or []),
        "sRelatedGithubUrl": request.sRelatedGithubUrl or "",
    }


def _fnRegisterGithubPush(app, dictCtx):
    """Register POST /api/github/{id}/push endpoint."""
    from .. import syncDispatcher

    @fnAgentAction("push-to-github")
    @app.post("/api/github/{sContainerId}/push")
    async def fnGithubPush(
        sContainerId: str, request: SyncPushRequest,
    ):
        dictCtx["require"]()
        _fnRequireNetworkAccess(sContainerId)
        dictWorkflow = fdictRequireWorkflow(
            dictCtx["workflows"], sContainerId)
        sWorkdir = posixpath.dirname(
            dictCtx["paths"].get(sContainerId, ""))
        _fnValidateGithubPushPaths(request.listFilePaths, sWorkdir)
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

    @fnAgentAction("add-file-to-github")
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
    sContainerId, sProjectId, sZenodoInstance="",
):
    """Dispatch to service-specific validator.

    Returns ``(bPass, sDetail)`` where ``sDetail`` is an optional
    service-supplied error fragment (empty for services that don't
    capture one).
    """
    if sService == "zenodo":
        sZenodoService = syncDispatcher.fsZenodoInstanceToService(
            sZenodoInstance or "sandbox"
        )
        bPass = await asyncio.to_thread(
            syncDispatcher.fbValidateZenodoToken,
            connectionDocker, sContainerId, sZenodoService,
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
    sZenodoInstance="",
):
    """Delete a just-stored credential after validation failure."""
    sTokenName = f"{sService}_token"
    if sService == "overleaf":
        _fnCleanupOverleafHostCredential(sTokenName)
        return
    if sService == "zenodo" and sZenodoInstance:
        sTokenName = syncDispatcher.fsZenodoTokenNameForInstance(
            sZenodoInstance
        )
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
    sZenodoInstance="",
):
    """Try to store; return a failure dict or None on success."""
    try:
        _fnDispatchStore(
            syncDispatcher, dictCtx, sContainerId, sService, sToken,
            sZenodoInstance,
        )
    except Exception as error:
        return {
            "bConnected": False,
            "sMessage": f"Failed to store credentials: {error}",
        }
    return None


def _fnDispatchStore(
    syncDispatcher, dictCtx, sContainerId, sService, sToken,
    sZenodoInstance="",
):
    """Route Overleaf to the host keyring; others to the container."""
    if sService == "overleaf":
        from vaibify.config.secretManager import fnStoreSecret
        fnStoreSecret("overleaf_token", sToken, "keyring")
        return
    sTokenName = f"{sService}_token"
    if sService == "zenodo":
        sTokenName = syncDispatcher.fsZenodoTokenNameForInstance(
            sZenodoInstance or "sandbox"
        )
    syncDispatcher.fnStoreCredentialInContainer(
        dictCtx["docker"], sContainerId, sTokenName, sToken,
    )


async def _fdictStoreValidateCredential(
    dictCtx, sContainerId, sService, sToken, sProjectId,
    sZenodoInstance="",
):
    """Store credential, verify connectivity, validate; clean up on failure."""
    from .. import syncDispatcher
    dictStoreFail = _fdictStoreCredentialSafely(
        syncDispatcher, dictCtx, sContainerId, sService, sToken,
        sZenodoInstance,
    )
    if dictStoreFail is not None:
        return dictStoreFail
    dictResult = await _fdictValidateStoredCredential(
        dictCtx, sContainerId, sService, sProjectId,
        sZenodoInstance,
    )
    if not dictResult["bConnected"]:
        _fnCleanupCredential(
            syncDispatcher, dictCtx["docker"],
            sContainerId, sService, sZenodoInstance,
        )
    return dictResult


async def _fdictValidateStoredCredential(
    dictCtx, sContainerId, sService, sProjectId,
    sZenodoInstance="",
):
    """Validate an already-stored credential without deleting it on failure."""
    from .. import syncDispatcher
    dictResult = syncDispatcher.fdictCheckConnectivity(
        dictCtx["docker"], sContainerId, sService)
    if not dictResult["bConnected"]:
        return dictResult
    bValid, sDetail = await _ftRunServiceValidation(
        syncDispatcher, sService, dictCtx["docker"],
        sContainerId, sProjectId, sZenodoInstance,
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
        sZenodoInstance = _fsResolveZenodoInstance(request)
        if request.sToken:
            return await _fdictStoreValidateCredential(
                dictCtx, sContainerId, request.sService,
                request.sToken, request.sProjectId or "",
                sZenodoInstance,
            )
        if _fbServiceHasStoredCredential(request.sService):
            return await _fdictValidateStoredCredential(
                dictCtx, sContainerId, request.sService,
                request.sProjectId or "",
                sZenodoInstance,
            )
        return syncDispatcher.fdictCheckConnectivity(
            dictCtx["docker"], sContainerId, request.sService)

    def _fnPersistServiceSettings(dictCtx, sContainerId, request):
        if request.sService == "overleaf" and request.sProjectId:
            dictWorkflow = fdictRequireWorkflow(
                dictCtx["workflows"], sContainerId)
            dictWorkflow["sOverleafProjectId"] = request.sProjectId
            dictCtx["save"](sContainerId, dictWorkflow)
            return
        if request.sService == "zenodo":
            _fnPersistZenodoService(dictCtx, sContainerId, request)

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

    @app.post("/api/sync/{sContainerId}/track")
    async def fnSetTracking(
        sContainerId: str, request: SyncTrackingRequest,
    ):
        dictCtx["require"]()
        if request.sService not in ("Overleaf", "Zenodo", "Github"):
            raise HTTPException(
                400,
                "sService must be Overleaf, Zenodo, or Github",
            )
        dictWorkflow = fdictRequireWorkflow(
            dictCtx["workflows"], sContainerId)
        workflowManager.fnSetServiceTracking(
            dictWorkflow, request.sPath, request.sService,
            request.bTrack,
        )
        dictCtx["save"](sContainerId, dictWorkflow)
        return {"bSuccess": True}


def _fbServiceHasStoredCredential(sService):
    """Return True when the host keyring already has this service's token."""
    from vaibify.config.secretManager import fbSecretExists
    if sService != "overleaf":
        return False
    return fbSecretExists("overleaf_token", "keyring")


def _fdictParseZenodoResult(sOut):
    """Extract the ZENODO_RESULT=<json> line from the archive stdout."""
    import json
    for sLine in reversed((sOut or "").splitlines()):
        sStripped = sLine.strip()
        if sStripped.startswith("ZENODO_RESULT="):
            try:
                return json.loads(sStripped[len("ZENODO_RESULT="):])
            except ValueError:
                return {}
    return {}


def _fnPersistZenodoPublishRecord(dictWorkflow, dictResult):
    """Store deposit id + DOIs + HTML URL on the workflow."""
    if dictResult.get("iDepositId"):
        dictWorkflow["sZenodoDepositionId"] = str(
            dictResult["iDepositId"]
        )
    if dictResult.get("sDoi"):
        dictWorkflow["sZenodoLatestDoi"] = dictResult["sDoi"]
    if dictResult.get("sConceptDoi"):
        dictWorkflow["sZenodoConceptDoi"] = dictResult["sConceptDoi"]
    if dictResult.get("sHtmlUrl"):
        dictWorkflow["sZenodoLatestUrl"] = dictResult["sHtmlUrl"]


def _fsReadHostGitUserName():
    """Read the host user's global git user.name.

    The vaibify container has no user.name configured — only credential
    helpers — so reading from the container yields nothing. The user's
    actual identity lives in the host's global ``~/.gitconfig``. Falls
    back to ``"Vaibify User"`` when git is missing, times out, or the
    config is empty.
    """
    import subprocess
    try:
        resultProcess = subprocess.run(
            ["git", "config", "--global", "user.name"],
            capture_output=True, text=True, timeout=5,
        )
        sName = (resultProcess.stdout or "").strip()
    except (subprocess.SubprocessError, OSError):
        sName = ""
    if not sName:
        return "Vaibify User"
    sSanitized = sName.replace("'", "").replace("\\", "").strip()
    return sSanitized or "Vaibify User"


def _fsBuildZenodoTitle(dictWorkflow):
    """Pick a non-empty Zenodo deposition title from workflow fields.

    Fallback for workflows whose ``dictZenodoMetadata.sTitle`` is
    empty. Prefers the user-facing project title, then the workflow
    file's name, then a generic label. Base64-encoded transport means
    no character stripping is required.
    """
    return (
        dictWorkflow.get("sProjectTitle")
        or dictWorkflow.get("sWorkflowName")
        or "Vaibify archive"
    ).strip() or "Vaibify archive"


def _fiReadParentDepositId(dictWorkflow):
    """Return the previous deposit id as an int, or 0 if none.

    Triggers the Zenodo ``newversion`` flow in the dispatcher when
    positive. Non-numeric or absent values fall back to 0 (first
    publish) rather than raising, so workflows with corrupted state
    can still publish -- the next push chains off the resulting new
    deposit.
    """
    sRaw = dictWorkflow.get("sZenodoDepositionId") or ""
    try:
        iParent = int(sRaw)
    except (TypeError, ValueError):
        return 0
    return iParent if iParent > 0 else 0


def _fdictResolveZenodoMetadataForArchive(dictWorkflow):
    """Merge stored metadata with fallbacks needed to pass publish validation.

    Returns a metadata dict suitable for
    ``ftResultArchiveToZenodo``. When the user has not filled the
    metadata form, backfills the minimum required for a successful
    publish (title from the workflow name, creator from the host's
    git user.name) while preserving everything they did set.
    """
    dictStored = dict(workflowManager.fdictGetZenodoMetadata(dictWorkflow))
    if not (dictStored.get("sTitle") or "").strip():
        dictStored["sTitle"] = _fsBuildZenodoTitle(dictWorkflow)
    listCreators = dictStored.get("listCreators") or []
    if not any((c.get("sName") or "").strip() for c in listCreators):
        dictStored["listCreators"] = [{
            "sName": _fsReadHostGitUserName(),
            "sAffiliation": "",
            "sOrcid": "",
        }]
    return dictStored


def _fsResolveZenodoInstance(request):
    """Return the sZenodoInstance field when the request targets Zenodo."""
    if request.sService != "zenodo":
        return ""
    sRequested = getattr(request, "sZenodoInstance", None) or "sandbox"
    from .. import syncDispatcher
    if sRequested not in syncDispatcher.SET_VALID_ZENODO_INSTANCES:
        raise HTTPException(
            status_code=400,
            detail=(
                "sZenodoInstance must be 'sandbox' or 'production'."
            ),
        )
    return sRequested


def _fnPersistZenodoService(dictCtx, sContainerId, request):
    """Record which Zenodo service a successful setup chose."""
    from .. import syncDispatcher
    sInstance = _fsResolveZenodoInstance(request)
    if not sInstance:
        return
    dictWorkflow = fdictRequireWorkflow(
        dictCtx["workflows"], sContainerId)
    dictWorkflow["sZenodoService"] = (
        syncDispatcher.fsZenodoInstanceToService(sInstance)
    )
    dictCtx["save"](sContainerId, dictWorkflow)


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

    @fnAgentAction("download-zenodo-dataset")
    @app.post("/api/zenodo/{sContainerId}/download")
    async def fnDownloadDataset(
        sContainerId: str, request: DatasetDownloadRequest,
    ):
        dictCtx["require"]()
        _fnRequireNetworkAccess(sContainerId)
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


def _fnRegisterOverleafMirrorRefresh(app, dictCtx):
    """Register POST /api/overleaf/{id}/mirror/refresh endpoint."""
    from .. import syncDispatcher

    @fnAgentAction("refresh-overleaf-mirror")
    @app.post("/api/overleaf/{sContainerId}/mirror/refresh")
    async def fnRefreshMirror(sContainerId: str):
        dictCtx["require"]()
        _fnRequireNetworkAccess(sContainerId)
        sProjectId = _fsRequireOverleafProjectId(
            dictCtx, sContainerId)
        bSuccess, result = await asyncio.to_thread(
            syncDispatcher.ftRefreshOverleafMirror, sProjectId,
        )
        if not bSuccess:
            return {"bSuccess": False, "sMessage": str(result)}
        dictPayload = {"bSuccess": True}
        dictPayload.update(result)
        return dictPayload


def _fsReadMirrorRefreshedAt(sProjectId):
    """Return the ISO-8601 timestamp of the mirror's last fetch.

    Reads the mtime of ``.git/FETCH_HEAD`` (touched on every successful
    fetch) and falls back to ``.git/HEAD`` when no fetch has occurred
    yet (fresh clone). Returns an empty string when neither file
    exists (mirror not yet created).
    """
    from datetime import datetime, timezone
    from vaibify.reproducibility import overleafMirror
    sMirror = os.path.join(
        overleafMirror.fsGetMirrorRoot(), sProjectId,
    )
    sGitDir = os.path.join(sMirror, ".git")
    fMtime = _ffTryGetMtime(
        os.path.join(sGitDir, "FETCH_HEAD"))
    if fMtime is None:
        fMtime = _ffTryGetMtime(
            os.path.join(sGitDir, "HEAD"))
    if fMtime is None:
        return ""
    return datetime.fromtimestamp(
        fMtime, tz=timezone.utc,
    ).strftime("%Y-%m-%dT%H:%M:%SZ")


def _ffTryGetMtime(sPath):
    """Return the mtime of sPath as a float, or None when absent."""
    try:
        return os.path.getmtime(sPath)
    except OSError:
        return None


def _fnRegisterOverleafMirrorTree(app, dictCtx):
    """Register GET /api/overleaf/{id}/mirror/tree endpoint."""
    from .. import syncDispatcher

    @app.get("/api/overleaf/{sContainerId}/mirror/tree")
    async def fnGetMirrorTree(sContainerId: str):
        dictCtx["require"]()
        sProjectId = _fsRequireOverleafProjectId(
            dictCtx, sContainerId)
        listEntries = await asyncio.to_thread(
            syncDispatcher.flistListOverleafTree, sProjectId,
        )
        from vaibify.reproducibility import overleafMirror
        sHeadSha = await asyncio.to_thread(
            overleafMirror.fsReadMirrorHeadSha, sProjectId,
        )
        sRefreshedAt = await asyncio.to_thread(
            _fsReadMirrorRefreshedAt, sProjectId,
        )
        return {
            "listEntries": listEntries,
            "sHeadSha": sHeadSha,
            "sRefreshedAt": sRefreshedAt,
        }


def _fnRegisterOverleafDiff(app, dictCtx):
    """Register POST /api/overleaf/{id}/diff endpoint."""
    from .. import syncDispatcher

    @app.post("/api/overleaf/{sContainerId}/diff")
    async def fnOverleafDiff(
        sContainerId: str, request: OverleafDiffRequest,
    ):
        dictCtx["require"]()
        _fnRequireNetworkAccess(sContainerId)
        _fnValidateOverleafFilePaths(request.listFilePaths)
        _fnValidateOverleafTargetDirectory(request.sTargetDirectory)
        sProjectId = _fsRequireOverleafProjectId(
            dictCtx, sContainerId)
        await asyncio.to_thread(
            syncDispatcher.ftRefreshOverleafMirror, sProjectId,
        )
        return await asyncio.to_thread(
            _fdictBuildDiffResult,
            dictCtx, sContainerId, sProjectId, request,
        )


def _fdictBuildDiffResult(
    dictCtx, sContainerId, sProjectId, request,
):
    """Compose the diff + conflict payload returned by the diff endpoint."""
    from .. import syncDispatcher
    from vaibify.reproducibility import overleafMirror
    dictWorkflow = fdictRequireWorkflow(
        dictCtx["workflows"], sContainerId)
    dictSync = workflowManager.fdictGetSyncStatus(dictWorkflow)
    dictDiff = syncDispatcher.fdictDiffOverleafPush(
        sProjectId, request.listFilePaths, request.sTargetDirectory,
        connectionDocker=dictCtx["docker"], sContainerId=sContainerId,
    )
    listConflicts = syncDispatcher.flistCheckOverleafConflicts(
        sProjectId, request.listFilePaths,
        request.sTargetDirectory, dictSync,
    )
    listCaseCollisions = syncDispatcher.flistDetectOverleafCaseCollisions(
        sProjectId, request.listFilePaths, request.sTargetDirectory,
    )
    sHeadSha = overleafMirror.fsReadMirrorHeadSha(sProjectId)
    dictDiff["listConflicts"] = listConflicts
    dictDiff["listCaseCollisions"] = listCaseCollisions
    dictDiff["sSuggestedTargetDirectory"] = _fsSuggestCanonicalTarget(
        listCaseCollisions, request.sTargetDirectory,
    )
    dictDiff["sMirrorHeadSha"] = sHeadSha
    return dictDiff


def _fsSuggestCanonicalTarget(listCaseCollisions, sTypedTarget):
    """Return an unambiguous canonical target directory, or empty.

    The suggestion is only populated when every case-collision's
    canonical remote path shares the same parent directory, and that
    canonical directory differs from the one the user typed. Any
    disagreement across files yields an empty suggestion so the UI
    falls back to a generic warning.
    """
    if not listCaseCollisions:
        return ""
    setCanonicalDirs = set()
    for dictCollision in listCaseCollisions:
        sCanonical = dictCollision.get("sCanonicalRemotePath", "")
        sParent = posixpath.dirname(sCanonical)
        setCanonicalDirs.add(sParent)
    if len(setCanonicalDirs) != 1:
        return ""
    sCanonicalDir = next(iter(setCanonicalDirs))
    if sCanonicalDir == (sTypedTarget or ""):
        return ""
    return sCanonicalDir


def _fnRegisterOverleafMirrorDelete(app, dictCtx):
    """Register DELETE /api/overleaf/{id}/mirror endpoint."""

    @fnAgentAction("delete-overleaf-mirror")
    @app.delete("/api/overleaf/{sContainerId}/mirror")
    async def fnDeleteMirror(sContainerId: str):
        dictCtx["require"]()
        sProjectId = _fsRequireOverleafProjectId(
            dictCtx, sContainerId)
        from vaibify.reproducibility import overleafMirror
        await asyncio.to_thread(
            overleafMirror.fnDeleteMirror, sProjectId,
        )
        return {"bSuccess": True}


def _fsRequireOverleafProjectId(dictCtx, sContainerId):
    """Return sOverleafProjectId or raise HTTP 400 with a hint."""
    dictWorkflow = fdictRequireWorkflow(
        dictCtx["workflows"], sContainerId)
    sProjectId = dictWorkflow.get("sOverleafProjectId", "")
    if not sProjectId:
        raise HTTPException(
            status_code=400,
            detail="Overleaf project ID not set for this container.",
        )
    return sProjectId


def fnRegisterAll(app, dictCtx):
    """Register all sync and reproducibility routes."""
    _fnRegisterOverleafPush(app, dictCtx)
    _fnRegisterOverleafMirrorRefresh(app, dictCtx)
    _fnRegisterOverleafMirrorTree(app, dictCtx)
    _fnRegisterOverleafDiff(app, dictCtx)
    _fnRegisterOverleafMirrorDelete(app, dictCtx)
    _fnRegisterZenodoArchive(app, dictCtx)
    _fnRegisterZenodoMetadata(app, dictCtx)
    _fnRegisterZenodoDeposit(app, dictCtx)
    _fnRegisterGithubPush(app, dictCtx)
    _fnRegisterGithubAddFile(app, dictCtx)
    _fnRegisterSyncRoutes(app, dictCtx)
    _fnRegisterDag(app, dictCtx)
    _fnRegisterDagExport(app, dictCtx)
    _fnRegisterDatasetDownload(app, dictCtx)
