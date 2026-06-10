"""Sync, reproducibility, and DAG route handlers."""

__all__ = ["fnRegisterAll"]

import asyncio
import logging
import os
import posixpath
import re

from fastapi import HTTPException
from fastapi.responses import Response

from .. import containerGit, workflowManager
from ..actionCatalog import fnAgentAction
from ..pipelineRunner import fsShellQuote
from ..routeContext import ffilesForWorkflow
from ..pipelineServer import (
    ArxivConfigureRequest,
    DatasetDownloadRequest,
    GitAddFileRequest,
    GitIdentityRequest,
    OverleafDiffRequest,
    SyncPushRequest,
    SyncSetupRequest,
    SyncTrackingRequest,
    WORKSPACE_ROOT,
    ZenodoMetadataRequest,
    fdictRequireWorkflow,
    fnBumpSyncEpoch,
    fnValidatePathWithinRoot,
)
from .scriptRoutes import _fnStoreCommitHash

logger = logging.getLogger("vaibify")


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
            dictCtx["workflows"], sContainerId,
        )
        dictResult, sZenodoService = await _ftPerformZenodoArchive(
            syncDispatcher, dictCtx, sContainerId, dictWorkflow, request,
        )
        if not dictResult["bSuccess"]:
            return dictResult
        await _fnPersistZenodoArchiveSuccess(
            dictCtx, sContainerId, dictWorkflow, request,
            dictResult, sZenodoService,
        )
        return dictResult


async def _ftPerformZenodoArchive(
    syncDispatcher, dictCtx, sContainerId, dictWorkflow, request,
):
    """Upload to Zenodo and parse the per-deposit response.

    Returns ``(dictResult, sZenodoService)``. On failure the parsed
    Zenodo metadata is not merged into ``dictResult`` so callers can
    short-circuit before persisting.
    """
    sZenodoService = dictWorkflow.get("sZenodoService", "sandbox")
    dictMetadata = _fdictResolveZenodoMetadataForArchive(dictWorkflow)
    iParentDepositId = _fiReadParentDepositId(dictWorkflow)
    iExit, sOut = await asyncio.to_thread(
        syncDispatcher.ftResultArchiveToZenodo,
        dictCtx["docker"], sContainerId, sZenodoService,
        request.listFilePaths, dictMetadata, iParentDepositId,
    )
    dictResult = syncDispatcher.fdictSyncResult(iExit, sOut)
    if dictResult["bSuccess"]:
        dictResult.update(_fdictParseZenodoResult(sOut))
    return dictResult, sZenodoService


async def _fnPersistZenodoArchiveSuccess(
    dictCtx, sContainerId, dictWorkflow, request,
    dictResult, sZenodoService,
):
    """Persist the publish record, refresh digests, save the workflow."""
    _fnPersistZenodoPublishRecord(dictWorkflow, dictResult)
    workflowManager.fnUpdateSyncStatus(
        dictWorkflow, request.listFilePaths, "Zenodo",
    )
    dictDigests = await asyncio.to_thread(
        _fdictComputePostArchiveZenodoDigests,
        dictCtx, sContainerId, dictWorkflow, request.listFilePaths,
    )
    workflowManager.fnUpdateZenodoDigests(
        dictWorkflow, dictDigests, sZenodoService=sZenodoService,
    )
    dictCtx["save"](sContainerId, dictWorkflow)


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


def _fsRequireProjectRepoForGit(dictWorkflow):
    """Return the workflow's project repo path or raise HTTP 409.

    The GitHub push and add-file routes need to ``cd`` into the project
    repo before running ``git add``. The old workspace-as-repo model
    used the workflow.json's parent directory, which now lands inside
    ``.vaibify/workflows/`` rather than at the repo root — every git
    add then fails with "no such directory" because step paths are
    repo-relative, not workflow-relative.
    """
    sPath = dictWorkflow.get("sProjectRepoPath") or ""
    if not sPath:
        raise HTTPException(
            status_code=409,
            detail=(
                "Workflow is not inside a git repository. "
                "GitHub sync requires the workflow's parent directory "
                "to be a git work tree."
            ),
        )
    return sPath


def _fnAssertGithubTokenBoundToRemote(
    connectionDocker, sContainerId, sProjectRepoPath,
):
    """Confirm the resolved GitHub token's login owns the configured remote.

    Reads the project repo's origin URL inside the container, parses
    owner/repo, resolves the host-side token, and asks GitHub's
    ``/user`` endpoint who that token belongs to. Raises HTTP 409 on
    any mismatch so the push never reaches ``git push`` with the wrong
    credential.
    """
    from .. import containerGit
    from vaibify.reproducibility.githubAuth import (
        ftParseOwnerRepoFromRemoteUrl,
        fsKeyringSlotFor,
        fsResolveToken,
        fnAssertTokenOwnerBinding,
    )
    sRemoteUrl = containerGit.fsRemoteUrlInContainer(
        connectionDocker, sContainerId, sProjectRepoPath,
    )
    sOwner, sRepo = ftParseOwnerRepoFromRemoteUrl(sRemoteUrl)
    if not sOwner or not sRepo:
        return
    sSlot = fsKeyringSlotFor(sOwner, sRepo)
    sToken = fsResolveToken(sSlot)
    try:
        fnAssertTokenOwnerBinding(sToken, sOwner)
    except ValueError as errorBinding:
        raise HTTPException(status_code=409, detail=str(errorBinding))


_S_INDETERMINATE_PUSH_MESSAGE = (
    "The push was interrupted before its outcome could be "
    "confirmed; it may still have completed on GitHub. Use "
    "'Refresh from GitHub' to reconcile the dashboard."
)


def _fdictIndeterminatePushResult():
    """Build the honest result for an unverifiable push outcome."""
    return {
        "bSuccess": False,
        "sErrorType": "indeterminate",
        "sMessage": _S_INDETERMINATE_PUSH_MESSAGE,
    }


def _fdictResolveInterruptedPush(dictCtx, sContainerId, sWorkdir):
    """Probe the repo after a push exec raised; never fabricate success.

    Returns a result shaped like ``fdictSyncResult``: ``bSuccess`` is
    True only when the probe verifies the upstream already holds the
    local HEAD; otherwise the outcome is reported as indeterminate so
    the user can refresh instead of receiving a bare 500.
    """
    try:
        dictProbe = containerGit.fdictProbePushOutcome(
            dictCtx["docker"], sContainerId, sWorkspace=sWorkdir,
        )
    except Exception:
        logger.error("Push outcome probe failed for container %s",
                     sContainerId, exc_info=True)
        return _fdictIndeterminatePushResult()
    if not dictProbe.get("bPushLanded"):
        return _fdictIndeterminatePushResult()
    logger.info("GitHub push confirmed by probe after transport "
                "interruption: container=%s", sContainerId)
    return {
        "bSuccess": True,
        "sOutput": "Push confirmed by repository probe after a "
                   "transport interruption.",
    }


async def _fdictHandlePushExecFailure(
    dictCtx, sContainerId, sWorkdir, sOperation,
):
    """Log the raised exec and resolve the outcome via the repo probe."""
    logger.error(
        "GitHub %s exec raised for container %s; probing outcome",
        sOperation, sContainerId, exc_info=True,
    )
    return await asyncio.to_thread(
        _fdictResolveInterruptedPush, dictCtx, sContainerId, sWorkdir,
    )


def _fdictLogIncompletePush(sContainerId, dictResult):
    """Log a non-success push result and pass it through unchanged."""
    logger.info(
        "GitHub push did not complete: container=%s sErrorType=%s",
        sContainerId, dictResult.get("sErrorType", ""),
    )
    return dictResult


def _fsFetchCommitHashAfterPush(dictCtx, sContainerId, sWorkdir):
    """Return the post-push HEAD sha via git, or "" when the lookup fails.

    Replaces the old splitlines()[-1] parse of merged stdout+stderr,
    which captured git push's stderr noise instead of the hash.
    """
    try:
        return containerGit.fsGitHeadShaInContainer(
            dictCtx["docker"], sContainerId, sWorkspace=sWorkdir,
        )
    except Exception:
        logger.warning(
            "Post-push HEAD sha lookup failed for container %s",
            sContainerId, exc_info=True,
        )
        return ""


def _fdictRemoteStateAfterPush(dictCtx, sContainerId, sWorkdir):
    """Return the post-push remote summary, or None when unavailable."""
    try:
        dictGit = containerGit.fdictGitStatusInContainer(
            dictCtx["docker"], sContainerId, sWorkspace=sWorkdir,
        )
    except Exception:
        logger.warning(
            "Post-push remote state lookup failed for container %s",
            sContainerId, exc_info=True,
        )
        return None
    return {
        "sHeadSha": dictGit.get("sHeadSha", ""),
        "sBranch": dictGit.get("sBranch", ""),
        "iAhead": dictGit.get("iAhead", 0),
        "iBehind": dictGit.get("iBehind", 0),
        "sRefreshedAt": dictGit.get("sRefreshedAt", ""),
    }


def _fdictAttachCommitStateToResult(
    dictCtx, sContainerId, sWorkdir, dictResult,
):
    """Stamp the verified commit hash and remote state onto a success."""
    dictResult["sCommitHash"] = _fsFetchCommitHashAfterPush(
        dictCtx, sContainerId, sWorkdir,
    )
    dictRemoteState = _fdictRemoteStateAfterPush(
        dictCtx, sContainerId, sWorkdir,
    )
    if dictRemoteState is not None:
        dictResult["dictRemoteState"] = dictRemoteState
    return dictResult


def _fsApplyPushBookkeeping(
    dictCtx, sContainerId, dictWorkflow, listFilePaths, sCommitHash,
):
    """Record sync status and commit hash; never fail the push response.

    The push itself already landed, so an exception here must not
    convert success into a 500. Returns "" on success or a warning
    string for the response's ``sBookkeepingWarning`` field.
    """
    try:
        workflowManager.fnUpdateSyncStatus(
            dictWorkflow, listFilePaths, "Github")
        _fnStoreCommitHash(dictWorkflow, listFilePaths, sCommitHash)
        dictCtx["save"](sContainerId, dictWorkflow)
        return ""
    except Exception:
        logger.error(
            "GitHub push bookkeeping failed for container %s",
            sContainerId, exc_info=True,
        )
        return (
            "Push succeeded, but recording the sync status locally "
            "failed; badges may lag until the next refresh."
        )


def _fdictFinishSuccessfulPush(
    dictCtx, sContainerId, dictWorkflow, listFilePaths, sWorkdir,
    dictResult,
):
    """Attach commit hash, remote state, and non-fatal bookkeeping."""
    dictResult = _fdictAttachCommitStateToResult(
        dictCtx, sContainerId, sWorkdir, dictResult,
    )
    sWarning = _fsApplyPushBookkeeping(
        dictCtx, sContainerId, dictWorkflow, listFilePaths,
        dictResult.get("sCommitHash", ""),
    )
    if sWarning:
        dictResult["sBookkeepingWarning"] = sWarning
    logger.info(
        "GitHub push succeeded: container=%s commit=%s",
        sContainerId, dictResult.get("sCommitHash", "") or "<unknown>",
    )
    return dictResult


async def _fdictRunGithubPush(
    dictCtx, sContainerId, dictWorkflow, sWorkdir, request,
):
    """Run the push, resolving exec failures into honest results."""
    from .. import syncDispatcher
    try:
        iExit, sOut = await asyncio.to_thread(
            syncDispatcher.ftResultPushToGithub,
            dictCtx["docker"], sContainerId,
            request.listFilePaths, request.sCommitMessage, sWorkdir,
        )
        dictResult = syncDispatcher.fdictSyncResult(iExit, sOut)
    except Exception:
        dictResult = await _fdictHandlePushExecFailure(
            dictCtx, sContainerId, sWorkdir, "push",
        )
    if not dictResult["bSuccess"]:
        return _fdictLogIncompletePush(sContainerId, dictResult)
    return await asyncio.to_thread(
        _fdictFinishSuccessfulPush, dictCtx, sContainerId,
        dictWorkflow, request.listFilePaths, sWorkdir, dictResult,
    )


def _fnRegisterGithubPush(app, dictCtx):
    """Register POST /api/github/{id}/push endpoint."""

    @fnAgentAction("push-to-github")
    @app.post("/api/github/{sContainerId}/push")
    async def fnGithubPush(
        sContainerId: str, request: SyncPushRequest,
    ):
        dictCtx["require"]()
        _fnRequireNetworkAccess(sContainerId)
        dictWorkflow = fdictRequireWorkflow(
            dictCtx["workflows"], sContainerId)
        sWorkdir = _fsRequireProjectRepoForGit(dictWorkflow)
        _fnValidateGithubPushPaths(request.listFilePaths, sWorkdir)
        await asyncio.to_thread(
            _fnAssertGithubTokenBoundToRemote,
            dictCtx["docker"], sContainerId, sWorkdir,
        )
        logger.info(
            "GitHub push requested: container=%s files=%d",
            sContainerId, len(request.listFilePaths or []),
        )
        dictResult = await _fdictRunGithubPush(
            dictCtx, sContainerId, dictWorkflow, sWorkdir, request,
        )
        fnBumpSyncEpoch(dictCtx, sContainerId)
        return dictResult


_RE_GIT_EMAIL = re.compile(r"^[^\s@<>]+@[^\s@<>]+\.[^\s@<>]+$")


def _fnValidateGitIdentity(sName, sEmail):
    """Reject obviously malformed git identity strings before shelling out."""
    if not isinstance(sName, str) or sName.strip() == "":
        raise HTTPException(
            status_code=400, detail="sName must be a non-empty string.",
        )
    if not isinstance(sEmail, str) or sEmail.strip() == "":
        raise HTTPException(
            status_code=400, detail="sEmail must be a non-empty string.",
        )
    for sField, sValue in (("sName", sName), ("sEmail", sEmail)):
        if "\x00" in sValue or "\n" in sValue or "\r" in sValue:
            raise HTTPException(
                status_code=400,
                detail=f"{sField} must not contain control characters.",
            )
    if not _RE_GIT_EMAIL.match(sEmail.strip()):
        raise HTTPException(
            status_code=400, detail="sEmail is not a valid email address.",
        )


def _fnRegisterGithubIdentity(app, dictCtx):
    """Register POST /api/github/{id}/identity endpoint."""

    @fnAgentAction("set-git-identity")
    @app.post("/api/github/{sContainerId}/identity")
    async def fnGithubIdentity(
        sContainerId: str, request: GitIdentityRequest,
    ):
        dictCtx["require"]()
        dictWorkflow = fdictRequireWorkflow(
            dictCtx["workflows"], sContainerId)
        sWorkdir = _fsRequireProjectRepoForGit(dictWorkflow)
        _fnValidateGitIdentity(request.sName, request.sEmail)
        iExit, sOut = await asyncio.to_thread(
            _ftWriteGitIdentity,
            dictCtx["docker"], sContainerId, sWorkdir,
            request.sName.strip(), request.sEmail.strip(),
        )
        if iExit != 0:
            raise HTTPException(
                status_code=502,
                detail=f"git config failed: {sOut[:400]}",
            )
        return {"bSuccess": True}


def _ftWriteGitIdentity(
    connectionDocker, sContainerId, sWorkdir, sName, sEmail,
):
    """Run git config user.name and user.email inside the project repo."""
    sCommand = (
        f"cd {fsShellQuote(sWorkdir)} && "
        f"git config user.name {fsShellQuote(sName)} && "
        f"git config user.email {fsShellQuote(sEmail)}"
    )
    return connectionDocker.ftResultExecuteCommand(
        sContainerId, sCommand,
    )


async def _fdictRunGithubAddFile(
    dictCtx, sContainerId, sWorkdir, request,
):
    """Run the single-file push, resolving exec failures honestly."""
    from .. import syncDispatcher
    try:
        iExit, sOut = await asyncio.to_thread(
            syncDispatcher.ftResultAddFileToGithub,
            dictCtx["docker"], sContainerId,
            request.sFilePath, request.sCommitMessage, sWorkdir,
        )
        dictResult = syncDispatcher.fdictSyncResult(iExit, sOut)
    except Exception:
        dictResult = await _fdictHandlePushExecFailure(
            dictCtx, sContainerId, sWorkdir, "add-file",
        )
    if not dictResult["bSuccess"]:
        return _fdictLogIncompletePush(sContainerId, dictResult)
    return await asyncio.to_thread(
        _fdictAttachCommitStateToResult,
        dictCtx, sContainerId, sWorkdir, dictResult,
    )


def _fnRegisterGithubAddFile(app, dictCtx):
    """Register POST /api/github/{id}/add-file endpoint."""

    @fnAgentAction("add-file-to-github")
    @app.post("/api/github/{sContainerId}/add-file")
    async def fnGithubAddFile(
        sContainerId: str, request: GitAddFileRequest,
    ):
        dictCtx["require"]()
        dictWorkflow = fdictRequireWorkflow(
            dictCtx["workflows"], sContainerId)
        sWorkdir = _fsRequireProjectRepoForGit(dictWorkflow)
        fnValidatePathWithinRoot(
            posixpath.normpath(
                posixpath.join(sWorkdir, request.sFilePath)
            ),
            WORKSPACE_ROOT,
        )
        logger.info(
            "GitHub add-file requested: container=%s", sContainerId,
        )
        dictResult = await _fdictRunGithubAddFile(
            dictCtx, sContainerId, sWorkdir, request,
        )
        fnBumpSyncEpoch(dictCtx, sContainerId)
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
        dictWorkflow = fdictRequireWorkflow(
            dictCtx["workflows"], sContainerId,
        )
        _fnValidateZenodoDestination(
            request.sDestination, dictWorkflow,
        )
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


def _fnValidateZenodoDestination(sDestination, dictWorkflow):
    """Refuse absolute or ..-escaping destinations; scope to project repo."""
    if "\x00" in (sDestination or ""):
        raise HTTPException(400, "sDestination contains null byte")
    if posixpath.isabs(sDestination):
        raise HTTPException(
            400, "sDestination must be repo-relative, not absolute")
    sNorm = posixpath.normpath(sDestination)
    if sNorm == ".." or sNorm.startswith("../"):
        raise HTTPException(
            400, "sDestination must not escape the project repo")
    sProjectRepoPath = dictWorkflow.get("sProjectRepoPath", "")
    if sProjectRepoPath:
        sCandidate = posixpath.join(sProjectRepoPath, sNorm)
        fnValidatePathWithinRoot(sCandidate, sProjectRepoPath)


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


_LIST_VERIFY_REMOTE_SERVICES = ("github", "overleaf", "zenodo", "arxiv")


def _fnValidateVerifyService(sService):
    """Raise HTTP 400 when sService is not a supported verify target."""
    if sService not in _LIST_VERIFY_REMOTE_SERVICES:
        raise HTTPException(
            status_code=400,
            detail=(
                "sService must be one of: "
                + ", ".join(_LIST_VERIFY_REMOTE_SERVICES)
            ),
        )


def _fdictRunRemoteVerifyBlocking(dictWorkflow, sService, filesRepo):
    """Run the synchronous verify call against the remote and return status."""
    from vaibify.reproducibility import scheduledReverify
    dictStatus = scheduledReverify.fdictVerifyRemoteService(
        filesRepo, dictWorkflow, sService,
    )
    scheduledReverify.fnWriteSyncStatus(filesRepo, dictStatus)
    return dictStatus


def _fnRaiseVerifyError(errorAny, sService):
    """Translate verify exceptions to HTTPException with redacted detail.

    Status mapping:

    * 409 — preconditions not met (manifest absent, workflow config
      missing for the service, dictPathMap references a path absent
      from the e-print, or a basename match is ambiguous and no
      dictPathMap entry disambiguates it).
    * 422 — manifest is corrupt or remote config in workflow.json is
      shape-invalid (e.g. a non-conforming GitHub owner string).
    * 502 — remote service failure (network, auth, rate limit, etc.).

    ``ValueError`` is treated as 422 because every ``ValueError`` raised
    by the verify path comes from input-shape validation (the manifest
    parser, GitHub owner/repo regex, Overleaf project-id regex). The
    detail string is redacted before being returned.
    """
    from vaibify.reproducibility import arxivClient, scheduledReverify
    if isinstance(errorAny, FileNotFoundError):
        raise HTTPException(
            status_code=409,
            detail=(
                "MANIFEST.sha256 is missing. Run the workflow to "
                "regenerate the manifest before verifying."
            ),
        ) from errorAny
    if isinstance(errorAny, scheduledReverify.ReverifyConfigError):
        raise HTTPException(
            status_code=409, detail=str(errorAny),
        ) from errorAny
    if isinstance(
        errorAny,
        (arxivClient.ArxivPathMapError,
         arxivClient.ArxivAmbiguousMatchError),
    ):
        raise HTTPException(
            status_code=409, detail=str(errorAny),
        ) from errorAny
    if isinstance(errorAny, ValueError):
        sRedacted = _fsRedactRemoteError(str(errorAny))
        raise HTTPException(
            status_code=422,
            detail=(
                f"Verify input invalid for {sService}: {sRedacted}"
            ),
        ) from errorAny
    sRedacted = _fsRedactRemoteError(str(errorAny))
    raise HTTPException(
        status_code=502,
        detail=f"Remote verify failed for {sService}: {sRedacted}",
    ) from errorAny


def _fsRedactRemoteError(sMessage):
    """Apply both mirror modules' redactors to a remote error message."""
    from vaibify.reproducibility import (
        githubMirror as ghMirror,
        overleafMirror as olMirror,
    )
    return olMirror.fsRedactStderr(ghMirror.fsRedactStderr(sMessage or ""))


def _fnRegisterRemoteVerify(app, dictCtx):
    """Register POST /api/sync/{id}/{sService}/verify endpoint."""

    @fnAgentAction("verify-remote")
    @app.post("/api/sync/{sContainerId}/{sService}/verify")
    async def fnVerifyRemote(sContainerId: str, sService: str):
        dictCtx["require"]()
        _fnValidateVerifyService(sService)
        _fnRequireNetworkAccess(sContainerId)
        dictWorkflow = fdictRequireWorkflow(
            dictCtx["workflows"], sContainerId)
        filesRepo = ffilesForWorkflow(
            dictCtx, sContainerId, dictWorkflow,
        )
        try:
            return await asyncio.to_thread(
                _fdictRunRemoteVerifyBlocking, dictWorkflow, sService,
                filesRepo,
            )
        except HTTPException:
            raise
        except Exception as errorAny:
            _fnRaiseVerifyError(errorAny, sService)


def _fnRegisterRemoteVerifyStatus(app, dictCtx):
    """Register GET /api/sync/{id}/{sService}/status endpoint."""
    from vaibify.reproducibility import scheduledReverify

    @app.get("/api/sync/{sContainerId}/{sService}/status")
    async def fnGetRemoteVerifyStatus(
        sContainerId: str, sService: str,
    ):
        _fnValidateVerifyService(sService)
        dictWorkflow = fdictRequireWorkflow(
            dictCtx["workflows"], sContainerId)
        filesRepo = ffilesForWorkflow(
            dictCtx, sContainerId, dictWorkflow,
        )
        return await asyncio.to_thread(
            scheduledReverify.fdictReadCachedSyncStatus,
            filesRepo, sService,
        )


_RE_ARXIV_ID = re.compile(
    r"^(?:\d{4}\.\d{4,5}(?:v\d+)?|[a-z-]+\/\d{7}(?:v\d+)?)$"
)


def _fnValidateArxivId(sArxivId):
    """Reject arXiv IDs that do not match the modern or legacy format."""
    if not isinstance(sArxivId, str) or sArxivId == "":
        raise HTTPException(
            status_code=400,
            detail="sArxivId must be a non-empty string.",
        )
    if not _RE_ARXIV_ID.match(sArxivId):
        raise HTTPException(
            status_code=400,
            detail=(
                "sArxivId must look like '2401.12345' (with optional "
                "'v2' suffix) or 'astro-ph/0601001'."
            ),
        )


def _fnValidateArxivPathMap(dictPathMap):
    """Reject path-map keys/values that are empty, null-byte, or escape ``..``."""
    if not isinstance(dictPathMap, dict):
        raise HTTPException(
            status_code=400,
            detail="dictPathMap must be a JSON object of string keys to string values.",
        )
    for sLocal, sTarball in dictPathMap.items():
        _fnValidateArxivPathSegment(sLocal, "dictPathMap key")
        _fnValidateArxivPathSegment(sTarball, "dictPathMap value")


def _fnRaiseArxivSegment(sFieldLabel, sReason):
    """Raise HTTP 400 with the standard arxiv path-segment error shape."""
    raise HTTPException(
        status_code=400,
        detail=f"{sFieldLabel} must {sReason}.",
    )


def _fnValidateArxivPathSegment(sSegment, sFieldLabel):
    """Reject one path-map string for empty/null-byte/parent-escape problems."""
    if not isinstance(sSegment, str) or sSegment == "":
        _fnRaiseArxivSegment(sFieldLabel, "be a non-empty string")
    if "\x00" in sSegment:
        _fnRaiseArxivSegment(sFieldLabel, "not contain null bytes")
    if sSegment.startswith("/"):
        _fnRaiseArxivSegment(
            sFieldLabel, "not be absolute (leading '/')")
    for sPart in sSegment.split("/"):
        if sPart == "..":
            _fnRaiseArxivSegment(
                sFieldLabel, "not contain '..' segments")
        if sPart.startswith("~"):
            _fnRaiseArxivSegment(
                sFieldLabel, "not contain '~' segments")


def _fdictBuildArxivConfig(request):
    """Translate a configure-request body into the dictRemotes.arxiv entry."""
    dictConfig = {"sArxivId": request.sArxivId}
    if request.dictPathMap:
        dictConfig["dictPathMap"] = dict(request.dictPathMap)
    return dictConfig


def _fnPersistArxivConfig(dictCtx, sContainerId, dictWorkflow, dictConfig):
    """Write the new arxiv config into dictWorkflow and save."""
    dictRemotes = dictWorkflow.setdefault("dictRemotes", {})
    if dictConfig is None:
        dictRemotes.pop("arxiv", None)
    else:
        dictRemotes["arxiv"] = dictConfig
    dictCtx["save"](sContainerId, dictWorkflow)


def _fdictRunArxivVerifyAfterConfig(dictWorkflow, filesRepo):
    """Run a best-effort verify after a save; capture errors on the response."""
    from vaibify.reproducibility import scheduledReverify
    try:
        dictStatus = scheduledReverify.fdictVerifyRemoteService(
            filesRepo, dictWorkflow, "arxiv",
        )
        scheduledReverify.fnWriteSyncStatus(filesRepo, dictStatus)
        return {"dictArxivStatus": dictStatus, "sVerifyError": ""}
    except Exception as errorAny:
        return {"dictArxivStatus": None, "sVerifyError": str(errorAny)}


def _fnRegisterArxivConfigure(app, dictCtx):
    """Register POST /api/sync/{id}/arxiv/configure endpoint."""

    @fnAgentAction("configure-arxiv")
    @app.post("/api/sync/{sContainerId}/arxiv/configure")
    async def fnConfigureArxiv(
        sContainerId: str, request: ArxivConfigureRequest,
    ):
        dictCtx["require"]()
        dictWorkflow = fdictRequireWorkflow(
            dictCtx["workflows"], sContainerId)
        if request.bRemove:
            _fnPersistArxivConfig(
                dictCtx, sContainerId, dictWorkflow, None)
            return {"dictArxivConfig": {}, "sVerifyError": ""}
        _fnValidateArxivId(request.sArxivId)
        _fnValidateArxivPathMap(request.dictPathMap)
        dictConfig = _fdictBuildArxivConfig(request)
        _fnPersistArxivConfig(
            dictCtx, sContainerId, dictWorkflow, dictConfig)
        dictVerify = await asyncio.to_thread(
            _fdictRunArxivVerifyAfterConfig, dictWorkflow,
            ffilesForWorkflow(dictCtx, sContainerId, dictWorkflow),
        )
        return {
            "dictArxivConfig": dictConfig,
            "dictArxivStatus": dictVerify["dictArxivStatus"],
            "sVerifyError": dictVerify["sVerifyError"],
        }


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
    _fnRegisterGithubIdentity(app, dictCtx)
    _fnRegisterSyncRoutes(app, dictCtx)
    _fnRegisterDag(app, dictCtx)
    _fnRegisterDagExport(app, dictCtx)
    _fnRegisterDatasetDownload(app, dictCtx)
    _fnRegisterRemoteVerify(app, dictCtx)
    _fnRegisterRemoteVerifyStatus(app, dictCtx)
    _fnRegisterArxivConfigure(app, dictCtx)
    _fnRegisterScheduledReverify(app, dictCtx)


def _fnRegisterScheduledReverify(app, dictCtx):
    """Attach the periodic re-verify task to the FastAPI lifespan."""
    from vaibify.reproducibility import scheduledReverify
    scheduledReverify.fnScheduleReverify(app, dictCtx)
