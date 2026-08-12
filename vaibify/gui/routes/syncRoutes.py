"""Sync, reproducibility, and DAG route handlers."""

__all__ = ["fnRegisterAll"]

import asyncio
import copy
import hashlib
import logging
import os
import posixpath
import re
import threading
import time

from fastapi import HTTPException, Request
from fastapi.responses import Response

from vaibify.config.mutationAdmission import fnReRaiseControlPlaneRefusal
from .. import containerGit, workflowManager
from ..actionCatalog import ffnAgentAction
from ..pipelineRunner import fsShellQuote
from ..routeContext import (
    fdictCarryARefusalBackInsteadOfRaising,
    fdictRequireLaneTupleForCommit,
    fdictRunRemoteVerifyBlocking,
    ffilesForWorkflow,
    fdictCommitWorkflowSave,
    fnRejectAgentTokenLane,
    fgenericRunWorkerUnderTheDrain,
    fsRefreshVerifyCacheAfterPush,
)
from ..routeScope import (
    S_CARRIER_MODE_A_SYNCHRONOUS,
    S_CARRIER_MODE_B_LOCK_HELD,
    S_CARRIER_SEPARATE_AUTHORITY,
    ffnDeclareCarrierMode,
)
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
    fsValidatePathWithinRoot,
)
from ..projectRoots import fsResolveProjectRoot
from .scriptRoutes import _fnStoreCommitHash

logger = logging.getLogger("vaibify")

# In-memory deduplication cache for the github push pipeline. Keys are
# ``(sContainerId, sCommitSha, sPayloadHash)``. Values are
# ``(fExpiryEpoch, dictResult)``. A second call with the same key
# inside the TTL window returns the cached result, so a vaibify-do
# retry over a network flake never re-runs the pre-push validation or
# bumps iSyncEpoch twice.
_DICT_RECENT_PUSH_RESULTS = {}
_LOCK_RECENT_PUSHES = threading.Lock()
_F_RECENT_PUSH_TTL_SECONDS = 30.0


def _fsHashPushPayload(listFilePaths):
    """Return a stable digest of the file list for the dedupe key."""
    listSorted = sorted(listFilePaths or [])
    sJoined = "\n".join(listSorted)
    return hashlib.sha256(sJoined.encode("utf-8")).hexdigest()


def _fnEvictExpiredPushResults(fNow):
    """Drop cache entries whose TTL has elapsed; runs under the lock."""
    listExpired = [
        tKey for tKey, (fExpiry, _result) in _DICT_RECENT_PUSH_RESULTS.items()
        if fExpiry <= fNow
    ]
    for tKey in listExpired:
        _DICT_RECENT_PUSH_RESULTS.pop(tKey, None)


def _fdictLookupRecentPush(tKey, fNow):
    """Return the cached result for tKey, or None when expired/absent."""
    with _LOCK_RECENT_PUSHES:
        _fnEvictExpiredPushResults(fNow)
        tEntry = _DICT_RECENT_PUSH_RESULTS.get(tKey)
        if tEntry is None:
            return None
        fExpiry, dictResult = tEntry
        if fExpiry <= fNow:
            _DICT_RECENT_PUSH_RESULTS.pop(tKey, None)
            return None
        return copy.deepcopy(dictResult)


def _fnRecordRecentPush(tKey, dictResult, fNow):
    """Persist a successful push result under tKey with the TTL stamped."""
    with _LOCK_RECENT_PUSHES:
        _DICT_RECENT_PUSH_RESULTS[tKey] = (
            fNow + _F_RECENT_PUSH_TTL_SECONDS,
            copy.deepcopy(dictResult),
        )


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


def _fnValidateOverleafFilePaths(listFilePaths, sContainerId):
    """Reject any path outside this resource's root, or with NUL bytes.

    Raises HTTP 400 when a caller submits a path that would exfiltrate
    host files (e.g. ``/etc/passwd``) through the push or diff flow.
    The existing HTTP 403 from ``fsValidatePathWithinRoot`` is
    translated to 400 here so the GUI treats the request as
    input-validation error and surfaces a clear message.

    The root is the resource's own. Measured against ``/workspace`` a
    host project's every legitimate path is outside it, so the guard
    refused the whole feature rather than an attack -- which is how
    the GitHub push below was found to answer 400 for every host
    project, before any git ran.
    """
    if listFilePaths is None:
        return
    sProjectRoot = fsResolveProjectRoot(sContainerId, WORKSPACE_ROOT)
    for sFilePath in listFilePaths:
        _fnRefuseUnusablePathText(sFilePath)
        _fnRefusePathOutsideRoot(sFilePath, sProjectRoot)


def _fnValidateGithubPushPaths(listFilePaths, sWorkdir, sContainerId):
    """Validate paths submitted to the GitHub push endpoint.

    Accepts workdir-relative paths (the common case) and absolute
    paths. Each is resolved against sWorkdir before being checked
    against the resource's own root, so a payload like
    ``{"listFilePaths": ["../../etc/passwd"]}`` is rejected at the
    route layer, before any git subprocess runs.
    """
    if listFilePaths is None:
        return
    sProjectRoot = fsResolveProjectRoot(sContainerId, WORKSPACE_ROOT)
    for sFilePath in listFilePaths:
        _fnRefuseUnusablePathText(sFilePath)
        _fnRefusePathOutsideRoot(
            sFilePath if sFilePath.startswith("/") else posixpath.normpath(
                posixpath.join(sWorkdir or sProjectRoot, sFilePath),
            ),
            sProjectRoot,
        )


def _fnRefuseUnusablePathText(sFilePath):
    """Refuse a path that is not usable text at all."""
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


def _fnRefusePathOutsideRoot(sFilePath, sProjectRoot):
    """Refuse a path outside the root, as a 400 rather than a 403."""
    try:
        fsValidatePathWithinRoot(sFilePath, sProjectRoot)
    except HTTPException as error:
        raise HTTPException(
            status_code=400,
            detail="File path must be within the project root.",
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
    from vaibify.reproducibility import overleafMirror, overleafSync
    dictRemoteBlobs = overleafMirror.fdictIndexMirrorBlobs(sProjectId)
    dictDigests = {}
    for sLocalPath in listLocalPaths:
        sRemotePath = overleafSync.fsOverleafRemotePathFor(
            sLocalPath, sTargetDirectory,
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
    listFilePaths, sMirrorSha, dictOverleafArgs, requestHttp=None,
):
    """Run the blocking Overleaf push under carrier mode (b) (design §8).

    The push is an irreversible sync commit that crosses a worker-
    thread ``await``, so on the production route (``requestHttp``
    present) it runs as a lock-held async mutation: a shielded
    supervisor holds the container's mutation drain until the worker
    thread actually terminates, and the write-ahead journal records
    the operation before it launches. The worker's bound is the Docker
    client's socket deadline
    (``dockerConnection.I_DOCKER_CLIENT_TIMEOUT_SECONDS``). Direct
    library/test callers that pass no request run the legacy
    ``to_thread`` path — an unenforced lane, named in the carrier's
    documented remainder, never a pretend-guarded one.
    """
    def ftPushWorker(supervisor=None):
        del supervisor
        return syncDispatcher.ftResultPushToOverleaf(
            connectionDocker, sContainerId,
            listFilePaths, sMirrorSha=sMirrorSha,
            **dictOverleafArgs,
        )

    if requestHttp is None:
        return await asyncio.to_thread(ftPushWorker)
    from .. import commitCarrier
    appState = requestHttp.app.state
    dictLaneTuple = commitCarrier.fdictBuildLaneTupleFromRequest(
        appState, sContainerId, requestHttp,
    )
    if dictLaneTuple is None:
        raise HTTPException(
            403, "The Overleaf push cannot be bound to this "
            "container's owner record; claim or connect first.")
    dictCommit = await commitCarrier.fdictRunLockHeldMutation(
        appState, dictLaneTuple["sContainerName"], sContainerId,
        dictLaneTuple, "helper", "overleaf-push", ftPushWorker,
    )
    return dictCommit["result"]


async def _fnFinalizeOverleafPush(
    dictCtx, sContainerId, dictWorkflow, sProjectId,
    listFilePaths, sTargetDirectory, requestHttp,
):
    """Run the post-push bookkeeping: sync status, digests, provenance, save.

    Two carriers. The digest refresh and the provenance record share
    ONE mode-(b) drain because the provenance manifest is what the L2
    figure-freeze blockers read and the digests are what the Overleaf
    cells compare against: a hand-over landing between them would leave
    the successor with figures recorded as frozen at a commit whose
    content fingerprints were never written. The digest half runs on
    the researcher's own machine (it refreshes the host mirror clone),
    the provenance half reaches the container -- the drain covers both
    because they are one bookkeeping act, not because both mutate.

    The ``project.json`` save is then mode (a): one write, no ``await``
    between the workflow's last in-memory edit and the bytes landing,
    and the drain the pair above held was already released.
    """
    workflowManager.fnUpdateSyncStatus(
        dictWorkflow, listFilePaths, "Overleaf")

    def fdictRecordTheBookkeeping(supervisor=None):
        del supervisor
        return fdictCarryARefusalBackInsteadOfRaising(
            lambda: _fnPersistDigestsThenProvenance(
                dictCtx, sContainerId, dictWorkflow, sProjectId,
                listFilePaths, sTargetDirectory,
            ),
        )

    await fgenericRunWorkerUnderTheDrain(
        sContainerId, fdictRecordTheBookkeeping, "overleaf-push-provenance",
        requestHttp,
    )
    fdictCommitWorkflowSave(
        dictCtx, sContainerId, dictWorkflow, requestHttp,
        "The Overleaf push bookkeeping save",
    )


def _fnRecordPushProvenance(
    dictCtx, sContainerId, dictWorkflow,
    listFilePaths, sTargetDirectory,
):
    """Record which figures this push froze, keyed by the repo's HEAD.

    Writes the push manifest (local→remote path map at the current
    project-repo commit) and stamps ``dictRemotes.overleaf
    .sLastPushCommit``. These are the authority behind the L2
    figure-freeze blockers, the arXiv correspondence gate, and the
    Overleaf/arXiv verify scope — a push that skips this record is
    invisible to all three. Best-effort like the digest persistence:
    a provenance failure must never fail a push that already landed —
    the honest degradation is figures reading not-frozen until the
    next successful push records them.
    """
    from .. import containerGit
    from vaibify.reproducibility import overleafSync
    try:
        sRepo = dictWorkflow.get("sProjectRepoPath", "")
        if not sRepo:
            return
        sHeadSha = containerGit.fsGitHeadShaInContainer(
            dictCtx["docker"], sContainerId, sWorkspace=sRepo,
        )
        if not sHeadSha:
            return
        listRepoRelative = [
            workflowManager.fsToSyncStatusKey(sPath, sRepo)
            for sPath in listFilePaths
        ]
        overleafSync.fnRecordOverleafPushManifest(
            ffilesForWorkflow(dictCtx, sContainerId, dictWorkflow),
            sHeadSha, listRepoRelative, sTargetDirectory,
        )
        dictRemotes = dictWorkflow.setdefault("dictRemotes", {})
        dictOverleaf = dictRemotes.setdefault("overleaf", {})
        dictOverleaf.setdefault(
            "sProjectId", dictWorkflow.get("sOverleafProjectId", ""),
        )
        dictOverleaf["sLastPushCommit"] = sHeadSha
    except Exception as error:
        fnReRaiseControlPlaneRefusal(error)
        logger.warning(
            "Overleaf push provenance recording failed for "
            "container %s; figures will read not-frozen until the "
            "next successful push",
            sContainerId, exc_info=True,
        )


def _fnPersistDigestsThenProvenance(
    dictCtx, sContainerId, dictWorkflow, sProjectId,
    listFilePaths, sTargetDirectory,
):
    """Refresh the mirror digests, then record the push provenance.

    The two halves of the Overleaf push's bookkeeping, in the order
    they must run, called from one carrier worker. Synchronous because
    a mode-(b) worker runs in a thread and cannot await: the two
    ``to_thread`` hops became direct calls, which is the same work on
    the same thread rather than two round-trips onto two of them.
    """
    _fnPersistPostPushDigests(
        dictWorkflow, sProjectId, listFilePaths, sTargetDirectory,
    )
    _fnRecordPushProvenance(
        dictCtx, sContainerId, dictWorkflow,
        listFilePaths, sTargetDirectory,
    )


async def _fdictRunOverleafPushFlow(
    syncDispatcher, dictCtx, sContainerId, dictWorkflow, request,
    requestHttp=None,
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
        requestHttp=requestHttp,
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
    syncDispatcher, dictCtx, sContainerId, request, requestHttp=None,
):
    """End-to-end Overleaf push: flow + post-push bookkeeping."""
    dictCtx["require"](sContainerId)
    _fnRequireNetworkAccess(sContainerId)
    _fnValidateOverleafFilePaths(request.listFilePaths, sContainerId)
    _fnValidateOverleafTargetDirectory(
        getattr(request, "sTargetDirectory", None)
    )
    dictWorkflow = fdictRequireWorkflow(
        dictCtx["workflows"], sContainerId)
    dictResult = await _fdictRunOverleafPushFlow(
        syncDispatcher, dictCtx, sContainerId, dictWorkflow, request,
        requestHttp=requestHttp,
    )
    sProjectId = dictResult.pop("_sProjectId", "")
    sTargetDirectory = dictResult.pop("_sTargetDirectory", "")
    if not dictResult["bSuccess"]:
        return dictResult
    await _fnFinalizeOverleafPush(
        dictCtx, sContainerId, dictWorkflow, sProjectId,
        request.listFilePaths, sTargetDirectory, requestHttp,
    )
    # AFTER finalize: the verify's comparison set is the push manifest
    # + sLastPushCommit that finalize just recorded. Same shared hop
    # as the GitHub push routes — the requirement row reads the verify
    # cache, which would otherwise stay stale until the next sweep.
    sVerifyWarning = await fsRefreshVerifyCacheAfterPush(
        dictCtx, sContainerId, dictWorkflow, "overleaf", requestHttp,
    )
    if sVerifyWarning:
        dictResult["sPostPushVerifyWarning"] = sVerifyWarning
    return dictResult


_T_MANUSCRIPT_EXTENSIONS = (".tex", ".bib", ".bbl", ".sty", ".cls")


def _flistManuscriptMirrorPaths(syncDispatcher, sProjectId):
    """Return the mirror-listed manuscript source paths for a project.

    Refreshes an absent mirror once. Only blob entries with manuscript
    extensions qualify — the pull list derives entirely from the
    mirror listing, never from request input, so a caller cannot
    steer the container-side pull at arbitrary paths.
    """
    from vaibify.reproducibility import overleafMirror
    listEntries = overleafMirror.flistListMirrorTree(sProjectId)
    if not listEntries:
        bRefreshed, _resultDetail = (
            syncDispatcher.ftRefreshOverleafMirror(sProjectId)
        )
        if bRefreshed:
            listEntries = overleafMirror.flistListMirrorTree(sProjectId)
    return [
        dictEntry["sPath"] for dictEntry in listEntries
        if dictEntry.get("sType") == "blob"
        and str(dictEntry.get("sPath", "")).lower().endswith(
            _T_MANUSCRIPT_EXTENSIONS,
        )
    ]


async def _fdictHandlePullManuscript(
    syncDispatcher, dictCtx, sContainerId, requestHttp,
):
    """Pull the Overleaf manuscript sources into the project repo.

    Lands them in ``<sProjectRepoPath>/.vaibify/manuscript/`` — a
    read-only convenience copy for the in-container agent (the
    read-manuscript skill), never a canonical artifact. A
    self-ignoring ``.gitignore`` is written into the target so the
    pull can never dirty the project repo, even in containers whose
    ``.vaibify/.gitignore`` predates the ``manuscript/`` entry.
    """
    dictCtx["require"](sContainerId)
    _fnRequireNetworkAccess(sContainerId)
    dictWorkflow = fdictRequireWorkflow(
        dictCtx["workflows"], sContainerId,
    )
    sProjectId = dictWorkflow.get("sOverleafProjectId", "")
    if not sProjectId:
        raise HTTPException(
            status_code=409,
            detail="No Overleaf project is bound to this project.",
        )
    sRepo = dictWorkflow.get("sProjectRepoPath", "")
    if not sRepo:
        raise HTTPException(
            status_code=409,
            detail="The project has no repository path.",
        )
    listPullPaths = await asyncio.to_thread(
        _flistManuscriptMirrorPaths, syncDispatcher, sProjectId,
    )
    if not listPullPaths:
        raise HTTPException(
            status_code=409,
            detail=(
                "The Overleaf mirror lists no manuscript sources "
                "(.tex/.bib/.bbl). Refresh the mirror from the Repos "
                "panel, or check the project binding."
            ),
        )
    sTargetDirectory = posixpath.join(sRepo, ".vaibify", "manuscript")
    return await _fdictPullManuscriptUnderTheDrain(
        syncDispatcher, dictCtx, sContainerId, sProjectId,
        listPullPaths, sTargetDirectory, requestHttp,
    )


async def _fdictPullManuscriptUnderTheDrain(
    syncDispatcher, dictCtx, sContainerId, sProjectId,
    listPullPaths, sTargetDirectory, requestHttp,
):
    """Pull the sources and write their ``.gitignore`` under one drain.

    Two container mutations, one carrier: the pull writes the manuscript
    files and the ``.gitignore`` that keeps them out of the researcher's
    commits is what makes the pull safe. Splitting them across two
    carriers would let a hand-over land between the files arriving and
    the ignore rule that hides them, and the successor's first ``git
    status`` would show the whole manuscript as untracked changes.

    The mirror LISTING that chose ``listPullPaths`` stays outside: it
    reads the host-side clone and touches no container state.

    A non-zero exit is carried back rather than raised. The pull ran to
    completion and reported its own failure — a bad token, an
    unreachable remote — so the container's state is known, and a raise
    would settle through the failure path and quarantine the container
    over a network blip. That is the same judgement the git panel makes
    about a failed ``git fetch``, which is why 502 is named here.
    """
    def fdictPullTheManuscript(supervisor=None):
        del supervisor
        return fdictCarryARefusalBackInsteadOfRaising(
            lambda: _fdictPullManuscriptBlocking(
                syncDispatcher, dictCtx, sContainerId, sProjectId,
                listPullPaths, sTargetDirectory,
            ),
            setAlsoCarriedStatusCodes=frozenset({502}),
        )

    return await fgenericRunWorkerUnderTheDrain(
        sContainerId, fdictPullTheManuscript, "overleaf-pull-manuscript",
        requestHttp,
    )


def _fdictPullManuscriptBlocking(
    syncDispatcher, dictCtx, sContainerId, sProjectId,
    listPullPaths, sTargetDirectory,
):
    """Run the pull and write the self-ignoring ``.gitignore``.

    Synchronous because a mode-(b) worker runs in a thread and cannot
    await: the two ``to_thread`` hops became direct calls, which is the
    same work on the same thread rather than two round-trips onto two
    of them.
    """
    iExitCode, sOutput = syncDispatcher.ftResultPullFromOverleaf(
        dictCtx["docker"], sContainerId, sProjectId,
        listPullPaths, sTargetDirectory,
    )
    if iExitCode != 0:
        raise HTTPException(
            status_code=502,
            detail=(
                "Manuscript pull failed: "
                + _fsRedactRemoteError((sOutput or "")[-400:])
            ),
        )
    dictCtx["docker"].fnWriteFile(
        sContainerId,
        posixpath.join(sTargetDirectory, ".gitignore"),
        b"*\n",
    )
    return {
        "bSuccess": True,
        "sManuscriptDirectory": sTargetDirectory,
        "listPulledFiles": listPullPaths,
    }


def _fnRegisterPullManuscript(app, dictCtx):
    """Register POST /api/overleaf/{id}/pull-manuscript."""
    from .. import syncDispatcher

    @ffnAgentAction("pull-manuscript")
    @app.post("/api/overleaf/{sContainerId}/pull-manuscript")
    @ffnDeclareCarrierMode(S_CARRIER_MODE_B_LOCK_HELD)
    async def fdictHandlePullManuscript(
        sContainerId: str, requestHttp: Request,
    ):
        return await _fdictHandlePullManuscript(
            syncDispatcher, dictCtx, sContainerId, requestHttp,
        )


def _fnRegisterOverleafPush(app, dictCtx):
    """Register POST /api/overleaf/{id}/push endpoint."""
    from .. import syncDispatcher

    @ffnAgentAction("push-to-overleaf")
    @app.post("/api/overleaf/{sContainerId}/push")
    @ffnDeclareCarrierMode(
        S_CARRIER_MODE_A_SYNCHRONOUS, S_CARRIER_MODE_B_LOCK_HELD,
    )
    async def fdictOverleafPush(
        sContainerId: str, request: SyncPushRequest,
        requestHttp: Request,
    ):
        return await _fdictHandleOverleafPushRequest(
            syncDispatcher, dictCtx, sContainerId, request,
            requestHttp=requestHttp,
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

    @ffnAgentAction("publish-to-zenodo")
    @app.post("/api/zenodo/{sContainerId}/archive")
    @ffnDeclareCarrierMode(
        S_CARRIER_MODE_A_SYNCHRONOUS, S_CARRIER_MODE_B_LOCK_HELD,
    )
    async def fdictZenodoArchive(
        sContainerId: str, request: SyncPushRequest,
        requestHttp: Request,
    ):
        dictCtx["require"](sContainerId)
        _fnRequireNetworkAccess(sContainerId)
        dictWorkflow = fdictRequireWorkflow(
            dictCtx["workflows"], sContainerId,
        )
        dictResult, sZenodoService = await _ftPerformZenodoArchive(
            syncDispatcher, dictCtx, sContainerId, dictWorkflow,
            request, requestHttp,
        )
        if not dictResult["bSuccess"]:
            return dictResult
        await _fnPersistZenodoArchiveSuccess(
            dictCtx, sContainerId, dictWorkflow, request,
            dictResult, sZenodoService, requestHttp,
        )
        return dictResult


async def _ftPerformZenodoArchive(
    syncDispatcher, dictCtx, sContainerId, dictWorkflow, request,
    requestHttp,
):
    """Upload to Zenodo under the drain and parse the deposit response.

    Returns ``(dictResult, sZenodoService)``. On failure the parsed
    Zenodo metadata is not merged into ``dictResult`` so callers can
    short-circuit before persisting.

    Mode (b): the upload streams the researcher's selected outputs to a
    remote archive from inside the container, for as long as the files
    and the network take. Holding the drain for the worker's life is
    what keeps an ownership hand-over from landing while a deposit is
    half-published — Zenodo mints a DOI at the end, so a container that
    changed hands mid-upload would leave the successor unable to say
    whether the archive exists.

    Nothing here raises for a failed upload: the dispatcher reports its
    exit code, the route turns it into ``bSuccess: False``, and a worker
    that raised would quarantine the container over a rejected token.
    """
    sZenodoService = dictWorkflow.get("sZenodoService", "sandbox")
    dictMetadata = _fdictResolveZenodoMetadataForArchive(dictWorkflow)
    iParentDepositId = _fiReadParentDepositId(dictWorkflow)

    def fdictArchiveToZenodo(supervisor=None):
        del supervisor
        return fdictCarryARefusalBackInsteadOfRaising(
            lambda: syncDispatcher.ftResultArchiveToZenodo(
                dictCtx["docker"], sContainerId, sZenodoService,
                request.listFilePaths, dictMetadata, iParentDepositId,
            ),
        )

    iExit, sOut = await fgenericRunWorkerUnderTheDrain(
        sContainerId, fdictArchiveToZenodo, "zenodo-archive", requestHttp,
    )
    dictResult = syncDispatcher.fdictSyncResult(iExit, sOut)
    if dictResult["bSuccess"]:
        dictResult.update(_fdictParseZenodoResult(sOut))
    return dictResult, sZenodoService


async def _fnPersistZenodoArchiveSuccess(
    dictCtx, sContainerId, dictWorkflow, request,
    dictResult, sZenodoService, requestHttp,
):
    """Persist the publish record, refresh digests, save the workflow.

    The digest pass is its own mode-(b) invocation rather than sharing
    the upload's: the upload's supervisor released the drain when its
    worker terminated, which is the property that makes mode (b) worth
    having. The save that follows is a mode-(a) synchronous commit
    whose bytes the journal can adjudicate.
    """
    _fnPersistZenodoPublishRecord(dictWorkflow, dictResult)
    workflowManager.fnUpdateSyncStatus(
        dictWorkflow, request.listFilePaths, "Zenodo",
    )

    def fdictComputeTheDigests(supervisor=None):
        del supervisor
        return fdictCarryARefusalBackInsteadOfRaising(
            lambda: _fdictComputePostArchiveZenodoDigests(
                dictCtx, sContainerId, dictWorkflow,
                request.listFilePaths,
            ),
        )

    dictDigests = await fgenericRunWorkerUnderTheDrain(
        sContainerId, fdictComputeTheDigests, "zenodo-archive-digests",
        requestHttp,
    )
    workflowManager.fnUpdateZenodoDigests(
        dictWorkflow, dictDigests, sZenodoService=sZenodoService,
    )
    fdictCommitWorkflowSave(
        dictCtx, sContainerId, dictWorkflow, requestHttp,
        "The Zenodo archive record",
    )


def _fnRegisterZenodoDeposit(app, dictCtx):
    """Register GET /api/zenodo/{id}/deposit endpoint."""

    @app.get("/api/zenodo/{sContainerId}/deposit")
    async def fdictGetZenodoDeposit(sContainerId: str):
        dictCtx["require"](sContainerId)
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
    async def fdictHandleGetZenodoMetadata(sContainerId: str):
        dictCtx["require"](sContainerId)
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

    @ffnAgentAction("set-zenodo-metadata")
    @app.post("/api/zenodo/{sContainerId}/metadata")
    @ffnDeclareCarrierMode(S_CARRIER_MODE_A_SYNCHRONOUS)
    async def fdictSetZenodoMetadata(
        sContainerId: str, request: ZenodoMetadataRequest,
        requestHttp: Request,
    ):
        dictCtx["require"](sContainerId)
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
        fdictCommitWorkflowSave(
            dictCtx, sContainerId, dictWorkflow, requestHttp,
            "The Zenodo metadata save",
        )
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
    used the project.json's parent directory, which now lands inside
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
        fnAssertTokenOwnerBinding,
    )
    # Deliberate reuse of githubMirror's hardened resolver rather than
    # a bare fsKeyringSlotFor/fsResolveToken pair: it degrades to an
    # empty token with a WARNING instead of escaping to the generic
    # 500 handler, so an unresolvable credential is reported as the
    # 409 the user can act on.
    from vaibify.reproducibility.githubMirror import _fsResolveTokenSafely
    sRemoteUrl = containerGit.fsRemoteUrlInContainer(
        connectionDocker, sContainerId, sProjectRepoPath,
    )
    sOwner, sRepo = ftParseOwnerRepoFromRemoteUrl(sRemoteUrl)
    if not sOwner or not sRepo:
        return
    sToken = _fsResolveTokenSafely(sOwner, sRepo)
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


def _fdictProbeAfterRaisedPushExec(
    dictCtx, sContainerId, sWorkdir, sOperation,
):
    """Log a raised push exec and resolve the outcome via the repo probe.

    Synchronous because both callers are mode-(b) workers running in a
    thread, which cannot await. The ``logger.error`` runs here rather
    than around a ``to_thread`` hop so ``sys.exc_info()`` is still
    populated and the traceback on this rare path is not dropped.
    """
    logger.error(
        "GitHub %s exec raised for container %s; probing outcome",
        sOperation, sContainerId, exc_info=True,
    )
    return _fdictResolveInterruptedPush(dictCtx, sContainerId, sWorkdir)


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
    requestHttp,
):
    """Record sync status and commit hash; never fail the push response.

    The push itself already landed, so an exception here must not
    convert success into a 500. Returns "" on success or a warning
    string for the response's ``sBookkeepingWarning`` field.

    Carrier mode (a): the save is one ``project.json`` write with no
    ``await`` between the workflow's last in-memory edit and the bytes
    landing, so it commits synchronously rather than holding a drain
    the push's own worker already released.

    A refusal is re-raised out of the broad handler rather than
    absorbed into the warning. ``MutationNotAdmittedError`` means the
    carrier call was forgotten, and reporting that as "badges may lag"
    would hide the migration's only proof behind a toast.
    """
    try:
        workflowManager.fnUpdateSyncStatus(
            dictWorkflow, listFilePaths, "Github")
        _fnStoreCommitHash(dictWorkflow, listFilePaths, sCommitHash)
        fdictCommitWorkflowSave(
            dictCtx, sContainerId, dictWorkflow, requestHttp,
            "The GitHub push bookkeeping save",
        )
        return ""
    except Exception as error:
        fnReRaiseControlPlaneRefusal(error)
        logger.error(
            "GitHub push bookkeeping failed for container %s",
            sContainerId, exc_info=True,
        )
        return (
            "Push succeeded, but recording the sync status locally "
            "failed; badges may lag until the next refresh."
        )


def _fdictRunGithubPushBlocking(
    dictCtx, sContainerId, sWorkdir, request,
):
    """Run the push and verify its commit state; never raise for a refusal.

    Synchronous because a mode-(b) worker runs in a thread and cannot
    await: the chain's three ``to_thread`` hops became direct calls,
    which is the same work on the same thread rather than three
    round-trips onto three of them.

    A failed push comes back as ``bSuccess: False`` and a raised exec
    is resolved by the repository probe, so the worker never poisons
    its journal record for an outcome the researcher can read.
    """
    from .. import syncDispatcher
    try:
        iExit, sOut = syncDispatcher.ftResultPushToGithub(
            dictCtx["docker"], sContainerId,
            request.listFilePaths, request.sCommitMessage, sWorkdir,
        )
        dictResult = syncDispatcher.fdictSyncResult(iExit, sOut)
    except Exception:  # noqa: BLE001 — resolved by probe, never raised
        dictResult = _fdictProbeAfterRaisedPushExec(
            dictCtx, sContainerId, sWorkdir, "push",
        )
    if not dictResult["bSuccess"]:
        return _fdictLogIncompletePush(sContainerId, dictResult)
    dictResult = _fdictAttachCommitStateToResult(
        dictCtx, sContainerId, sWorkdir, dictResult,
    )
    logger.info(
        "GitHub push succeeded: container=%s commit=%s",
        sContainerId, dictResult.get("sCommitHash", "") or "<unknown>",
    )
    return dictResult


def _fsGitHeadShaForDedupeKey(dictCtx, sContainerId, sWorkdir):
    """Return the pre-push HEAD sha used in the dedupe key, or "".

    A missing or unreadable HEAD degrades to an empty string so the
    dedupe key is still well-formed; the cache lookup will simply
    miss and the push runs as if uncached. The probe itself is one
    cheap docker exec.
    """
    try:
        return containerGit.fsGitHeadShaInContainer(
            dictCtx["docker"], sContainerId, sWorkspace=sWorkdir,
        )
    except Exception as error:
        fnReRaiseControlPlaneRefusal(error)
        logger.info(
            "pre-push HEAD probe failed for %s; skipping push dedupe",
            sContainerId, exc_info=True,
        )
        return ""


def _fdictPushToGithubBlocking(
    dictCtx, sContainerId, sWorkdir, request, sPayloadHash, fNow,
):
    """Probe, dedupe, bind the token to the remote, then push.

    One worker for the whole sequence because every step of it reaches
    the container and the push is the irreversible one: splitting the
    dedupe probe or the token binding into their own carriers would
    open a window where an ownership hand-over lands between the sha
    the dedupe key was built from and the push that key is meant to
    suppress.

    Returns ``{"tDedupeKey", "bDeduped", "dictResult"}`` so the caller
    can tell a replayed result from a fresh push without re-deriving
    the key on the event loop.
    """
    sCommitSha = _fsGitHeadShaForDedupeKey(
        dictCtx, sContainerId, sWorkdir,
    )
    tDedupeKey = (sContainerId, sCommitSha, sPayloadHash)
    dictCached = _fdictLookupRecentPush(tDedupeKey, fNow)
    if dictCached is not None:
        logger.info(
            "GitHub push dedupe HIT: container=%s commit=%s",
            sContainerId, sCommitSha or "<unknown>",
        )
        dictCached["bDedupedFromRecent"] = True
        return {
            "tDedupeKey": tDedupeKey,
            "bDeduped": True,
            "dictResult": dictCached,
        }
    _fnAssertGithubTokenBoundToRemote(
        dictCtx["docker"], sContainerId, sWorkdir,
    )
    logger.info(
        "GitHub push requested: container=%s files=%d",
        sContainerId, len(request.listFilePaths or []),
    )
    return {
        "tDedupeKey": tDedupeKey,
        "bDeduped": False,
        "dictResult": _fdictRunGithubPushBlocking(
            dictCtx, sContainerId, sWorkdir, request,
        ),
    }


async def _fdictPushToGithubUnderTheDrain(
    dictCtx, sContainerId, sWorkdir, request, sPayloadHash, fNow,
    requestHttp,
):
    """Run the whole push sequence holding the container's drain.

    Mode (b) rather than mode (a): the chain stages files, contacts a
    remote, and then probes the repository, so it runs for as long as
    the network takes and belongs in a worker thread. Holding the drain
    for the WORKER's life is what makes an ownership hand-over or a Run
    Step arriving mid-push refuse and say what is running, instead of
    landing underneath a git process that is still writing.

    Only the token-owner binding raises here, and it raises 409 with
    the container untouched — it read the remote URL and asked GitHub
    who the token belongs to, nothing more — so the default 4xx
    carry-back is the whole judgement and no 5xx is named. A raise from
    inside the worker would settle through the failure path and
    quarantine a working container over a mismatched credential.
    """
    def fdictHandlePushToGithub(supervisor=None):
        del supervisor
        return fdictCarryARefusalBackInsteadOfRaising(
            lambda: _fdictPushToGithubBlocking(
                dictCtx, sContainerId, sWorkdir, request,
                sPayloadHash, fNow,
            ),
        )

    return await fgenericRunWorkerUnderTheDrain(
        sContainerId, fdictHandlePushToGithub, "github-push", requestHttp,
    )


def _fnRegisterGithubPush(app, dictCtx):
    """Register POST /api/github/{id}/push endpoint.

    A repeat call inside ``_F_RECENT_PUSH_TTL_SECONDS`` with the same
    (container, pre-push HEAD sha, file-list digest) returns the
    cached result so a vaibify-do retry across a transient network
    flake does not re-run pre-push validation, re-stage files, or
    bump iSyncEpoch a second time. A differing payload bypasses the
    cache automatically because the digest changes.
    """

    @ffnAgentAction("push-to-github")
    @app.post("/api/github/{sContainerId}/push")
    @ffnDeclareCarrierMode(
        S_CARRIER_MODE_A_SYNCHRONOUS, S_CARRIER_MODE_B_LOCK_HELD,
    )
    async def fdictGithubPush(
        sContainerId: str, request: SyncPushRequest,
        requestHttp: Request,
    ):
        dictCtx["require"](sContainerId)
        _fnRequireNetworkAccess(sContainerId)
        dictWorkflow = fdictRequireWorkflow(
            dictCtx["workflows"], sContainerId)
        sWorkdir = _fsRequireProjectRepoForGit(dictWorkflow)
        _fnValidateGithubPushPaths(
            request.listFilePaths, sWorkdir, sContainerId,
        )
        fNow = time.monotonic()
        dictPushed = await _fdictPushToGithubUnderTheDrain(
            dictCtx, sContainerId, sWorkdir, request,
            _fsHashPushPayload(request.listFilePaths), fNow, requestHttp,
        )
        dictResult = dictPushed["dictResult"]
        if dictPushed["bDeduped"]:
            return dictResult
        if dictResult.get("bSuccess"):
            sBookkeepingWarning = _fsApplyPushBookkeeping(
                dictCtx, sContainerId, dictWorkflow,
                request.listFilePaths,
                dictResult.get("sCommitHash", ""), requestHttp,
            )
            if sBookkeepingWarning:
                dictResult["sBookkeepingWarning"] = sBookkeepingWarning
            sVerifyWarning = await fsRefreshVerifyCacheAfterPush(
                dictCtx, sContainerId, dictWorkflow, "github",
                requestHttp,
            )
            if sVerifyWarning:
                dictResult["sPostPushVerifyWarning"] = sVerifyWarning
            # Record AFTER attaching: the dedupe cache deep-copies,
            # and a replayed response must carry the same warning.
            _fnRecordRecentPush(dictPushed["tDedupeKey"], dictResult, fNow)
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

    @ffnAgentAction("set-git-identity")
    @app.post("/api/github/{sContainerId}/identity")
    @ffnDeclareCarrierMode(S_CARRIER_MODE_B_LOCK_HELD)
    async def fdictGithubIdentity(
        sContainerId: str, request: GitIdentityRequest,
        requestHttp: Request,
    ):
        dictCtx["require"](sContainerId)
        dictWorkflow = fdictRequireWorkflow(
            dictCtx["workflows"], sContainerId)
        sWorkdir = _fsRequireProjectRepoForGit(dictWorkflow)
        _fnValidateGitIdentity(request.sName, request.sEmail)
        iExit, sOut = await _ftWriteGitIdentityUnderTheDrain(
            dictCtx, sContainerId, sWorkdir,
            request.sName.strip(), request.sEmail.strip(), requestHttp,
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


async def _ftWriteGitIdentityUnderTheDrain(
    dictCtx, sContainerId, sWorkdir, sName, sEmail, requestHttp,
):
    """Rewrite the project repo's git identity holding the drain.

    Mode (b) rather than mode (a) for the reason the route already
    used ``to_thread``: the write is a container round-trip, and mode
    (a) would run it on the event loop. The identity it writes is what
    every subsequent commit in this repository is attributed to, so a
    hand-over landing between the ``user.name`` and ``user.email``
    halves of the one command would leave the successor committing
    under a half-changed identity.

    The exec's non-zero exit is returned, never raised: the route turns
    it into a 502, and a worker that raised would settle through the
    failure path and quarantine the container for a plain ``git
    config`` refusal.
    """
    from .. import commitCarrier
    dictLaneTuple = fdictRequireLaneTupleForCommit(
        requestHttp, sContainerId, "The git identity change",
    )

    def ftWriteTheIdentity(supervisor=None):
        del supervisor
        return _ftWriteGitIdentity(
            dictCtx["docker"], sContainerId, sWorkdir, sName, sEmail,
        )

    dictOutcome = await commitCarrier.fdictRunLockHeldMutation(
        requestHttp.app.state, dictLaneTuple["sContainerName"],
        sContainerId, dictLaneTuple, "helper", "git-identity",
        ftWriteTheIdentity,
    )
    return dictOutcome["result"]


def _fdictRunGithubAddFileBlocking(
    dictCtx, sContainerId, sWorkdir, request,
):
    """Run the single-file push, resolving exec failures honestly.

    Synchronous because a mode-(b) worker runs in a thread and cannot
    await: the chain's three ``to_thread`` hops became direct calls,
    which is the same work on the same thread rather than three
    round-trips onto three of them.

    Nothing here raises for an expected refusal -- a failed push comes
    back as ``bSuccess: False``, and the probe answers "indeterminate"
    rather than throwing -- so the worker never poisons its journal
    record for an outcome the researcher can simply read.
    """
    from .. import syncDispatcher
    try:
        iExit, sOut = syncDispatcher.ftResultAddFileToGithub(
            dictCtx["docker"], sContainerId,
            request.sFilePath, request.sCommitMessage, sWorkdir,
        )
        dictResult = syncDispatcher.fdictSyncResult(iExit, sOut)
    except Exception:  # noqa: BLE001 — resolved by probe, never raised
        dictResult = _fdictProbeAfterRaisedPushExec(
            dictCtx, sContainerId, sWorkdir, "add-file",
        )
    if not dictResult["bSuccess"]:
        return _fdictLogIncompletePush(sContainerId, dictResult)
    return _fdictAttachCommitStateToResult(
        dictCtx, sContainerId, sWorkdir, dictResult,
    )


def _fnRegisterGithubAddFile(app, dictCtx):
    """Register POST /api/github/{id}/add-file endpoint."""

    @ffnAgentAction("add-file-to-github")
    @app.post("/api/github/{sContainerId}/add-file")
    @ffnDeclareCarrierMode(S_CARRIER_MODE_B_LOCK_HELD)
    async def fdictGithubAddFile(
        sContainerId: str, request: GitAddFileRequest,
        requestHttp: Request,
    ):
        dictCtx["require"](sContainerId)
        dictWorkflow = fdictRequireWorkflow(
            dictCtx["workflows"], sContainerId)
        sWorkdir = _fsRequireProjectRepoForGit(dictWorkflow)
        fsValidatePathWithinRoot(
            posixpath.normpath(
                posixpath.join(sWorkdir, request.sFilePath)
            ),
            fsResolveProjectRoot(sContainerId, WORKSPACE_ROOT),
        )
        logger.info(
            "GitHub add-file requested: container=%s", sContainerId,
        )
        dictResult = await _fdictRunAddFileUnderTheDrain(
            dictCtx, sContainerId, sWorkdir, request, requestHttp,
        )
        fnBumpSyncEpoch(dictCtx, sContainerId)
        return dictResult


async def _fdictRunAddFileUnderTheDrain(
    dictCtx, sContainerId, sWorkdir, request, requestHttp,
):
    """Commit and push one file holding the container's mutation drain.

    Mode (b) rather than mode (a): the chain commits, contacts a
    remote, and then probes the repository, so it runs for as long as
    the network takes and belongs in a worker thread. Holding the drain
    for the WORKER's life is what makes an ownership hand-over or a Run
    Step arriving mid-push refuse and say what is running, instead of
    landing underneath a git process that is still writing.
    """
    from .. import commitCarrier
    dictLaneTuple = fdictRequireLaneTupleForCommit(
        requestHttp, sContainerId, "The add-file push",
    )

    def fdictAddTheFile(supervisor=None):
        del supervisor
        return _fdictRunGithubAddFileBlocking(
            dictCtx, sContainerId, sWorkdir, request,
        )

    dictOutcome = await commitCarrier.fdictRunLockHeldMutation(
        requestHttp.app.state, dictLaneTuple["sContainerName"],
        sContainerId, dictLaneTuple, "helper", "github-add-file",
        fdictAddTheFile,
    )
    return dictOutcome["result"]


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


def _ftRunOverleafValidation(
    syncDispatcher, connectionDocker, sContainerId, sProjectId,
):
    """Validate the stored Overleaf credential against the project.

    Returns ``(bSuccess, sStderr)`` so the caller can surface the
    underlying git message in the remediation toast.

    Synchronous because the whole setup flow now runs inside a mode-(b)
    worker, which is already off the event loop and cannot await: the
    ``to_thread`` hop this replaced would have been a second thread
    doing the same work.
    """
    if not sProjectId:
        return (False, "")
    return syncDispatcher.fbValidateOverleafCredentials(
        connectionDocker, sContainerId, sProjectId,
    )


def _ftRunServiceValidation(
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
        bPass = syncDispatcher.fbValidateZenodoToken(
            connectionDocker, sContainerId, sZenodoService,
        )
        return (bPass, "")
    if sService == "overleaf":
        return _ftRunOverleafValidation(
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
        syncDispatcher.fnDeleteCredentialForProject(
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
    syncDispatcher.fnStoreCredentialForProject(
        dictCtx["docker"], sContainerId, sTokenName, sToken,
    )


def _fdictStoreValidateCredential(
    dictCtx, sContainerId, sService, sToken, sProjectId,
    sZenodoInstance="",
):
    """Store credential, verify connectivity, validate; roll back on failure.

    Stage-validate-commit: the previously stored credential (when the
    service keeps one on the host) is captured before the new token
    overwrites it, and a validation failure RESTORES it instead of
    deleting — a mistyped token or a transient network failure must
    never destroy a credential that worked an hour earlier. Only when
    no previous credential existed does the failure path delete the
    freshly staged token. The response message states which happened
    so the researcher is never left guessing what survived.
    """
    from .. import syncDispatcher
    sPreviousToken = _fsFetchPreviousHostCredential(sService)
    tSlots = _ftSnapshotContainerCredential(
        syncDispatcher, dictCtx, sContainerId, sService,
        sZenodoInstance,
    )
    dictStoreFail = _fdictStoreCredentialSafely(
        syncDispatcher, dictCtx, sContainerId, sService, sToken,
        sZenodoInstance,
    )
    if dictStoreFail is not None:
        _fnDropContainerSnapshot(
            syncDispatcher, dictCtx, sContainerId, tSlots[1],
        )
        return dictStoreFail
    dictResult = _fdictValidateStoredCredential(
        dictCtx, sContainerId, sService, sProjectId,
        sZenodoInstance,
    )
    if not dictResult["bConnected"]:
        _fnRollBackFailedCredential(
            syncDispatcher, dictCtx, sContainerId, sService,
            sZenodoInstance, sPreviousToken, dictResult, tSlots,
        )
    else:
        _fnDropContainerSnapshot(
            syncDispatcher, dictCtx, sContainerId, tSlots[1],
        )
    return dictResult


def _ftSnapshotContainerCredential(
    syncDispatcher, dictCtx, sContainerId, sService, sZenodoInstance,
):
    """Copy the container token slot aside before a new token lands.

    Returns ``(sPrimarySlot, sBackupSlot)``. ``sBackupSlot`` is None
    when the service keeps no container-side credential (Overleaf's
    token is host-side), when no previous token existed, or when the
    snapshot attempt failed — the caller then falls back to the
    historical delete-on-failure behavior for that attempt. The copy
    runs entirely inside the container so the token value never
    crosses the docker-exec boundary.
    """
    if sService != "zenodo":
        return (None, None)
    sPrimarySlot = syncDispatcher.fsZenodoTokenNameForInstance(
        sZenodoInstance or "sandbox",
    )
    sBackupSlot = sPrimarySlot + "_backup"
    try:
        bCopied = syncDispatcher.fbCopyCredentialForProject(
            dictCtx["docker"], sContainerId, sPrimarySlot, sBackupSlot,
        )
    except Exception:
        return (sPrimarySlot, None)
    return (sPrimarySlot, sBackupSlot if bCopied else None)


def _fnDropContainerSnapshot(
    syncDispatcher, dictCtx, sContainerId, sBackupSlot,
):
    """Best-effort removal of a no-longer-needed snapshot slot."""
    if not sBackupSlot:
        return
    try:
        syncDispatcher.fnDeleteCredentialForProject(
            dictCtx["docker"], sContainerId, sBackupSlot,
        )
    except Exception:
        pass


def _fbRestoreContainerSnapshot(
    syncDispatcher, dictCtx, sContainerId, tSlots,
):
    """Copy the snapshot back over the failed token; drop the snapshot."""
    sPrimarySlot, sBackupSlot = tSlots
    if not sBackupSlot:
        return False
    try:
        bRestored = syncDispatcher.fbCopyCredentialForProject(
            dictCtx["docker"], sContainerId, sBackupSlot, sPrimarySlot,
        )
    except Exception:
        logger.warning(
            "Failed to restore the previous container token after a "
            "validation failure", exc_info=True,
        )
        return False
    _fnDropContainerSnapshot(
        syncDispatcher, dictCtx, sContainerId, sBackupSlot,
    )
    return bRestored


def _fsFetchPreviousHostCredential(sService):
    """Return the currently stored host-keyring token, or None.

    Only Overleaf keeps its token in the host keyring; the container-
    side services (Zenodo) would require echoing the secret through a
    ``docker exec`` round trip to capture it, so they keep the
    historical delete-on-failure behavior. Any read error is treated
    as "nothing stored" so a broken keyring cannot block the connect
    flow.
    """
    if sService != "overleaf":
        return None
    from vaibify.config.secretManager import fsRetrieveSecret
    try:
        return fsRetrieveSecret("overleaf_token", "keyring")
    except Exception:
        return None


def _fnRollBackFailedCredential(
    syncDispatcher, dictCtx, sContainerId, sService,
    sZenodoInstance, sPreviousToken, dictResult, tSlots=(None, None),
):
    """Undo a failed-validation store: restore the previous token or delete.

    Overleaf restores from the host keyring capture; container-side
    services (Zenodo) restore from the in-container snapshot slot.
    Appends the disposition to the result message so the dashboard
    states plainly whether a previously saved token survived.
    """
    if sPreviousToken is not None and sService == "overleaf":
        from vaibify.config.secretManager import fnStoreSecret
        try:
            fnStoreSecret("overleaf_token", sPreviousToken, "keyring")
            _fnAppendTokenDisposition(dictResult, bRestored=True)
            return
        except Exception:
            logger.warning(
                "Failed to restore the previous %s token after a "
                "validation failure", sService, exc_info=True,
            )
    if _fbRestoreContainerSnapshot(
        syncDispatcher, dictCtx, sContainerId, tSlots,
    ):
        _fnAppendTokenDisposition(dictResult, bRestored=True)
        return
    _fnCleanupCredential(
        syncDispatcher, dictCtx["docker"],
        sContainerId, sService, sZenodoInstance,
    )
    _fnAppendTokenDisposition(dictResult, bRestored=False)


def _fnAppendTokenDisposition(dictResult, bRestored):
    """State what happened to the stored token after a failed validation."""
    sDisposition = (
        " — the entered token was not saved; your previously saved "
        "token was restored"
        if bRestored
        else " — the entered token was not saved"
    )
    dictResult["sMessage"] = (
        dictResult.get("sMessage") or "Validation failed"
    ) + sDisposition


def _fdictValidateStoredCredential(
    dictCtx, sContainerId, sService, sProjectId,
    sZenodoInstance="",
):
    """Validate an already-stored credential without deleting it on failure."""
    from .. import syncDispatcher
    dictResult = syncDispatcher.fdictCheckConnectivity(
        dictCtx["docker"], sContainerId, sService)
    if not dictResult["bConnected"]:
        return dictResult
    bValid, sDetail = _ftRunServiceValidation(
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
    async def fdictHandleGetSyncStatus(sContainerId: str):
        dictWorkflow = fdictRequireWorkflow(
            dictCtx["workflows"], sContainerId)
        return workflowManager.fdictGetSyncStatus(dictWorkflow)

    @app.get("/api/sync/{sContainerId}/files")
    async def flistGetSyncFiles(
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
    @ffnDeclareCarrierMode(
        S_CARRIER_MODE_A_SYNCHRONOUS, S_CARRIER_MODE_B_LOCK_HELD,
    )
    async def fdictSetupConnection(
        sContainerId: str, request: SyncSetupRequest,
        requestHttp: Request,
    ):
        dictCtx["require"](sContainerId)
        syncDispatcher.fnValidateServiceName(request.sService)
        dictResult = await _fdictRunSetupUnderTheDrain(
            dictCtx, sContainerId, request, requestHttp,
        )
        if dictResult.get("bConnected"):
            _fnPersistServiceSettings(
                dictCtx, sContainerId, request, requestHttp,
            )
        return dictResult

    async def _fdictRunSetupUnderTheDrain(
        dictCtx, sContainerId, request, requestHttp,
    ):
        """Store and validate the credential holding the drain.

        Mode (b), and the drain is the point rather than a formality.
        Storing a token is a stage-validate-commit sequence over a
        SHARED slot: the previous credential is snapshotted, the new
        one overwrites it, a remote is contacted, and a failure
        restores the snapshot. A second session's setup interleaving
        between the snapshot and the restore would swap the two
        researchers' tokens, and neither would be told.

        The journal target is the compile-time constant below. Nothing
        derived from the request reaches it -- not the token, not the
        project id, not the service name -- because a journal record
        outlives the request and is read back by ``vaibify
        reconcile``.
        """
        def fdictRunTheSetup(supervisor=None):
            del supervisor
            return fdictCarryARefusalBackInsteadOfRaising(
                lambda: _fdictRunSetupBlocking(
                    dictCtx, sContainerId, request,
                ),
            )

        return await fgenericRunWorkerUnderTheDrain(
            sContainerId, fdictRunTheSetup, "sync-credential-setup",
            requestHttp,
        )

    def _fdictRunSetupBlocking(dictCtx, sContainerId, request):
        sZenodoInstance = _fsResolveZenodoInstance(request)
        if request.sToken:
            return _fdictStoreValidateCredential(
                dictCtx, sContainerId, request.sService,
                request.sToken, request.sProjectId or "",
                sZenodoInstance,
            )
        if _fbServiceHasStoredCredential(request.sService):
            return _fdictValidateStoredCredential(
                dictCtx, sContainerId, request.sService,
                request.sProjectId or "",
                sZenodoInstance,
            )
        return syncDispatcher.fdictCheckConnectivity(
            dictCtx["docker"], sContainerId, request.sService)

    def _fnPersistServiceSettings(
        dictCtx, sContainerId, request, requestHttp,
    ):
        if request.sService == "overleaf" and request.sProjectId:
            dictWorkflow = fdictRequireWorkflow(
                dictCtx["workflows"], sContainerId)
            dictWorkflow["sOverleafProjectId"] = request.sProjectId
            fdictCommitWorkflowSave(
                dictCtx, sContainerId, dictWorkflow, requestHttp,
                "The Overleaf project binding",
            )
            return
        if request.sService == "zenodo":
            _fnPersistZenodoService(
                dictCtx, sContainerId, request, requestHttp,
            )

    @app.get("/api/sync/{sContainerId}/check/{sService}")
    async def fdictCheckConnection(
        sContainerId: str, sService: str,
    ):
        dictCtx["require"](sContainerId)
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
    async def fdictHasCredential(
        sContainerId: str, sService: str, requestHttp: Request,
    ):
        fnRejectAgentTokenLane(requestHttp)
        dictCtx["require"](sContainerId)
        syncDispatcher.fnValidateServiceName(sService)
        return {
            "bHasCredential": _fbServiceHasStoredCredential(sService),
        }

    @app.post("/api/sync/{sContainerId}/track")
    @ffnDeclareCarrierMode(S_CARRIER_MODE_A_SYNCHRONOUS)
    async def fdictSetTracking(
        sContainerId: str, request: SyncTrackingRequest,
        requestHttp: Request,
    ):
        dictCtx["require"](sContainerId)
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
        fdictCommitWorkflowSave(
            dictCtx, sContainerId, dictWorkflow, requestHttp,
            "The sync-tracking change",
        )
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
        processResult = subprocess.run(
            ["git", "config", "--global", "user.name"],
            capture_output=True, text=True, timeout=5,
        )
        sName = (processResult.stdout or "").strip()
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


def _fnPersistZenodoService(
    dictCtx, sContainerId, request, requestHttp,
):
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
    fdictCommitWorkflowSave(
        dictCtx, sContainerId, dictWorkflow, requestHttp,
        "The Zenodo instance selection",
    )


def _fnRegisterDag(app, dictCtx):
    """Register DAG visualization endpoint."""
    from .. import syncDispatcher

    @app.get("/api/workflow/{sContainerId}/dag")
    async def fresponseHandleGetDag(sContainerId: str):
        dictCtx["require"](sContainerId)
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
    async def fresponseHandleExportDag(
        sContainerId: str, sFormat: str = "svg",
    ):
        dictCtx["require"](sContainerId)
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

    # NOT MIGRATED, and deliberately so (2026-08-06). This route calls
    # ``syncDispatcher.ftResultDownloadDataset``, which exists NOWHERE
    # in the repository: every call raises ``AttributeError`` and
    # answers 500. Its two tests patch the name into being with
    # ``create=True``, so the suite exercises a function the product
    # does not have. The route is advertised to the in-container agent
    # as ``download-zenodo-dataset`` with ``bAgentSafe: True``.
    #
    # Migrating it would make that WORSE rather than better: inside a
    # carrier the AttributeError settles through the failure path,
    # poisons the journal record and QUARANTINES the container until
    # the researcher runs ``vaibify reconcile`` -- so a broken button
    # would take a working container out of service. It keeps the
    # legacy ambient mint until the dispatcher exists and the route can
    # be migrated against behaviour somebody has actually run.
    @ffnAgentAction("download-zenodo-dataset")
    @app.post("/api/zenodo/{sContainerId}/download")
    async def fdictDownloadDataset(
        sContainerId: str, request: DatasetDownloadRequest,
    ):
        dictCtx["require"](sContainerId)
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
        fsValidatePathWithinRoot(sCandidate, sProjectRepoPath)


def _fnRegisterOverleafMirrorRefresh(app, dictCtx):
    """Register POST /api/overleaf/{id}/mirror/refresh endpoint."""
    from .. import syncDispatcher

    @ffnAgentAction("refresh-overleaf-mirror")
    # separate-authority, not a carrier mode. The refresh fetches into
    # the HOST-side partial clone and writes nothing inside the
    # container -- ``ftRefreshOverleafMirror`` takes no docker
    # connection at all. What governs it is the project-id validation
    # that bounds the mirror path, plus the host keyring lookup for the
    # token; the commit carrier governs container state, which this
    # never touches. Ruling 2026-08-05.
    @app.post("/api/overleaf/{sContainerId}/mirror/refresh")
    @ffnDeclareCarrierMode(S_CARRIER_SEPARATE_AUTHORITY)
    async def fdictHandleRefreshMirror(sContainerId: str):
        dictCtx["require"](sContainerId)
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
    async def fdictGetMirrorTree(sContainerId: str):
        dictCtx["require"](sContainerId)
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
    @ffnDeclareCarrierMode(S_CARRIER_MODE_B_LOCK_HELD)
    async def fdictOverleafDiff(
        sContainerId: str, request: OverleafDiffRequest,
        requestHttp: Request,
    ):
        dictCtx["require"](sContainerId)
        _fnRequireNetworkAccess(sContainerId)
        _fnValidateOverleafFilePaths(request.listFilePaths, sContainerId)
        _fnValidateOverleafTargetDirectory(request.sTargetDirectory)
        sProjectId = _fsRequireOverleafProjectId(
            dictCtx, sContainerId)
        await asyncio.to_thread(
            syncDispatcher.ftRefreshOverleafMirror, sProjectId,
        )
        return await _fdictBuildDiffUnderTheDrain(
            dictCtx, sContainerId, sProjectId, request, requestHttp,
        )


async def _fdictBuildDiffUnderTheDrain(
    dictCtx, sContainerId, sProjectId, request, requestHttp,
):
    """Compute the push preview holding the container's mutation drain.

    The preview looks like a read and is not one at the boundary that
    matters: ``fdictDiffOverleafPush`` digests the selected files by
    running a ``python3 -c`` script inside the container through
    ``ftResultExecuteCommand``, a GENERAL exec. The primitive cannot
    know that particular text only hashes, so it is admitted as a
    mutation or refused — there is no third answer, and the typed-read
    carve-out is reserved for commands its adapter BUILDS.

    Mode (b) rather than (a) because the work is a container round-trip
    over the researcher's whole selection plus host-side git object
    reads, neither of which belongs on the event loop.

    The mirror refresh that precedes this in the handler stays outside
    the carrier deliberately: it is a host-side fetch that touches no
    container state, so wrapping it would journal a container operation
    that never happens.
    """
    def fdictBuildTheDiff(supervisor=None):
        del supervisor
        return fdictCarryARefusalBackInsteadOfRaising(
            lambda: _fdictBuildDiffResult(
                dictCtx, sContainerId, sProjectId, request,
            ),
        )

    return await fgenericRunWorkerUnderTheDrain(
        sContainerId, fdictBuildTheDiff, "overleaf-diff", requestHttp,
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

    @ffnAgentAction("delete-overleaf-mirror")
    # separate-authority, not a carrier mode. The mirror is a partial
    # clone under the researcher's own ``~/.vaibify`` mirror root, on
    # the HOST; this route reaches the container not at all. What
    # governs it is ``overleafMirror.fnValidateOverleafProjectId``,
    # which bounds the id to the shape a directory name may take before
    # ``_fsMirrorPath`` joins it under the mirror root, so no request
    # value can steer the ``rmtree`` outside that tree. Ruling
    # 2026-08-05, same reasoning as fileRoutes' host pull.
    @app.delete("/api/overleaf/{sContainerId}/mirror")
    @ffnDeclareCarrierMode(S_CARRIER_SEPARATE_AUTHORITY)
    async def fdictDeleteMirror(sContainerId: str):
        dictCtx["require"](sContainerId)
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


def _fnRaiseVerifyError(errorAny, sService):
    """Translate verify exceptions to HTTPException with redacted detail.

    Status mapping:

    * 409 — preconditions not met (manifest absent, workflow config
      missing for the service, dictPathMap references a path absent
      from the e-print, or a basename match is ambiguous and no
      dictPathMap entry disambiguates it).
    * 422 — manifest is corrupt or remote config in project.json is
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


async def _fdictVerifyRemoteUnderTheDrain(
    dictWorkflow, sService, filesRepo, sContainerId, requestHttp,
):
    """Verify one remote and rewrite its cache holding the drain.

    The verify contacts a remote and then REWRITES ``syncStatus.json``
    inside the project repo, so it is a container mutation whose length
    is the network's to decide -- mode (b), for the same reasons the
    post-push verify in ``routeContext`` uses it.

    EVERY failure is carried back as a value rather than raised,
    including an ``HTTPException`` the verify chain raises itself. The
    route turns a remote's failure into a 4xx (a bad arXiv id, a path
    map that matches nothing) or a 502, and a worker that raised would
    settle through the failure path, mark its journal record NEEDS
    RECONCILIATION, and quarantine the container -- so an unreachable
    remote would cost the researcher their container until they ran
    ``vaibify reconcile``. The caller re-raises outside the carrier,
    after the supervisor has settled its record normally.
    """
    from .. import commitCarrier
    dictLaneTuple = fdictRequireLaneTupleForCommit(
        requestHttp, sContainerId, f"The {sService} verify",
    )

    def fdictVerifyTheRemote(supervisor=None):
        del supervisor
        try:
            return {
                "dictStatus": fdictRunRemoteVerifyBlocking(
                    dictWorkflow, sService, filesRepo,
                ),
                "errorRemote": None,
            }
        except Exception as errorAny:  # noqa: BLE001 — carried, not raised
            return {"dictStatus": None, "errorRemote": errorAny}

    dictOutcome = await commitCarrier.fdictRunLockHeldMutation(
        requestHttp.app.state, dictLaneTuple["sContainerName"],
        sContainerId, dictLaneTuple, "helper",
        "remote-verify " + sService, fdictVerifyTheRemote,
    )
    return dictOutcome["result"]


def _fnRegisterRemoteVerify(app, dictCtx):
    """Register POST /api/sync/{id}/{sService}/verify endpoint.

    A completed verify rewrites the cached remote status the Level-2
    cells and per-file badges read, so it bumps the sync epoch — the
    dashboard's only poll-free invalidation signal. Without the bump
    the one action that reconciles the screen with the remote is also
    the one action that leaves the screen un-repainted.
    """

    @ffnAgentAction("verify-remote")
    @app.post("/api/sync/{sContainerId}/{sService}/verify")
    @ffnDeclareCarrierMode(S_CARRIER_MODE_B_LOCK_HELD)
    async def fdictHandleVerifyRemote(
        sContainerId: str, sService: str, requestHttp: Request,
    ):
        dictCtx["require"](sContainerId)
        _fnValidateVerifyService(sService)
        _fnRequireNetworkAccess(sContainerId)
        dictWorkflow = fdictRequireWorkflow(
            dictCtx["workflows"], sContainerId)
        filesRepo = ffilesForWorkflow(
            dictCtx, sContainerId, dictWorkflow,
        )
        dictCarried = await _fdictVerifyRemoteUnderTheDrain(
            dictWorkflow, sService, filesRepo, sContainerId, requestHttp,
        )
        if isinstance(dictCarried["errorRemote"], HTTPException):
            raise dictCarried["errorRemote"]
        if dictCarried["errorRemote"] is not None:
            _fnRaiseVerifyError(dictCarried["errorRemote"], sService)
        fnBumpSyncEpoch(dictCtx, sContainerId)
        return dictCarried["dictStatus"]


def _fnRegisterRemoteVerifyStatus(app, dictCtx):
    """Register GET /api/sync/{id}/{sService}/status endpoint."""
    from vaibify.reproducibility import scheduledReverify

    @app.get("/api/sync/{sContainerId}/{sService}/status")
    async def fdictGetRemoteVerifyStatus(
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


def _fnRegisterReverifySchedule(app, dictCtx):
    """Register GET /api/sync/{sContainerId}/reverify-schedule.

    Exposes when the background re-verify loop last completed a pass.
    Read-only, and honest about never having run: without it a stale
    per-service age is indistinguishable from a cache the loop is
    keeping current.
    """
    from vaibify.reproducibility import scheduledReverify

    @app.get("/api/sync/{sContainerId}/reverify-schedule")
    async def fdictGetReverifySchedule(sContainerId: str):
        return await asyncio.to_thread(
            scheduledReverify.fdictDescribeReverifySchedule,
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


def _fnPersistArxivConfig(
    dictCtx, sContainerId, dictWorkflow, dictConfig, requestHttp,
):
    """Write the new arxiv config into dictWorkflow and save."""
    dictRemotes = dictWorkflow.setdefault("dictRemotes", {})
    if dictConfig is None:
        dictRemotes.pop("arxiv", None)
    else:
        dictRemotes["arxiv"] = dictConfig
    fdictCommitWorkflowSave(
        dictCtx, sContainerId, dictWorkflow, requestHttp,
        "The arXiv configuration",
    )


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


def _fsClearArxivSyncCache(filesRepo):
    """Drop the cached arXiv verify result; return an error string or "".

    Removing the connection must also remove the last verify report,
    or the requirements panel keeps rendering a ghost divergence count
    for a remote the workflow no longer tracks. Best-effort: a cache
    that cannot be cleared must not block the removal itself, so the
    error is surfaced on the response instead of raised.
    """
    from vaibify.reproducibility import scheduledReverify
    try:
        scheduledReverify.fnDeleteSyncStatus(filesRepo, "arxiv")
        return ""
    except Exception as errorAny:
        return str(errorAny)


async def _fgenericRunArxivCacheWorkUnderTheDrain(
    sContainerId, requestHttp, sOperationTarget, fnEffect,
):
    """Run one arXiv sync-cache rewrite holding the container's drain.

    Both callers rewrite ``syncStatus.json`` inside the project repo --
    one by verifying against arXiv and writing the report, the other by
    deleting it -- so both are container mutations that the enforced
    branch refuses without a carrier. They share this rather than each
    growing a copy, because they differ only in the closure.

    Neither effect raises: ``_fdictRunArxivVerifyAfterConfig`` and
    ``_fsClearArxivSyncCache`` already return their errors as values,
    deliberately, so that a remote that will not answer does not block
    the configuration change. That property is what lets the worker run
    without a refusal-carrying wrapper -- if either ever starts raising,
    it needs one, or a failed verify will quarantine the container.
    """
    from .. import commitCarrier
    dictLaneTuple = fdictRequireLaneTupleForCommit(
        requestHttp, sContainerId, "The arXiv configuration",
    )

    def fgenericRunTheEffect(supervisor=None):
        del supervisor
        return fnEffect()

    dictOutcome = await commitCarrier.fdictRunLockHeldMutation(
        requestHttp.app.state, dictLaneTuple["sContainerName"],
        sContainerId, dictLaneTuple, "helper", sOperationTarget,
        fgenericRunTheEffect,
    )
    return dictOutcome["result"]


def _fnRegisterArxivConfigure(app, dictCtx):
    """Register POST /api/sync/{id}/arxiv/configure endpoint."""

    @ffnAgentAction("configure-arxiv")
    @app.post("/api/sync/{sContainerId}/arxiv/configure")
    @ffnDeclareCarrierMode(
        S_CARRIER_MODE_A_SYNCHRONOUS, S_CARRIER_MODE_B_LOCK_HELD,
    )
    async def fdictConfigureArxiv(
        sContainerId: str, request: ArxivConfigureRequest,
        requestHttp: Request,
    ):
        dictCtx["require"](sContainerId)
        dictWorkflow = fdictRequireWorkflow(
            dictCtx["workflows"], sContainerId)
        if request.bRemove:
            _fnPersistArxivConfig(
                dictCtx, sContainerId, dictWorkflow, None, requestHttp)
            sClearError = await _fgenericRunArxivCacheWorkUnderTheDrain(
                sContainerId, requestHttp, "arxiv-cache-clear",
                lambda: _fsClearArxivSyncCache(
                    ffilesForWorkflow(
                        dictCtx, sContainerId, dictWorkflow),
                ),
            )
            return {"dictArxivConfig": {}, "sVerifyError": sClearError}
        _fnValidateArxivId(request.sArxivId)
        _fnValidateArxivPathMap(request.dictPathMap)
        dictConfig = _fdictBuildArxivConfig(request)
        _fnPersistArxivConfig(
            dictCtx, sContainerId, dictWorkflow, dictConfig, requestHttp)
        dictVerify = await _fgenericRunArxivCacheWorkUnderTheDrain(
            sContainerId, requestHttp, "arxiv-verify",
            lambda: _fdictRunArxivVerifyAfterConfig(
                dictWorkflow,
                ffilesForWorkflow(dictCtx, sContainerId, dictWorkflow),
            ),
        )
        return {
            "dictArxivConfig": dictConfig,
            "dictArxivStatus": dictVerify["dictArxivStatus"],
            "sVerifyError": dictVerify["sVerifyError"],
        }


def fnRegisterAll(app, dictCtx):
    """Register all sync and reproducibility routes."""
    _fnRegisterOverleafPush(app, dictCtx)
    _fnRegisterPullManuscript(app, dictCtx)
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
    _fnRegisterReverifySchedule(app, dictCtx)
    _fnRegisterArxivConfigure(app, dictCtx)
    _fnRegisterScheduledReverify(app, dictCtx)
    _fnRegisterEphemeralSecretSweep(app, dictCtx)


def _fnRegisterScheduledReverify(app, dictCtx):
    """Attach the periodic re-verify task to the FastAPI lifespan."""
    from vaibify.reproducibility import scheduledReverify
    scheduledReverify.fnScheduleReverify(app, dictCtx)


def _fnRegisterEphemeralSecretSweep(app, dictCtx):
    """Retire unreachable host credential files once, at hub startup.

    The GitHub, Overleaf and Zenodo flows registered above all drop
    live tokens into ``~/.vaibify/tmp``, and the container-mount path
    cannot unlink at the point of use, so a periodic sweep is the only
    mechanism available.

    Age alone does not make a file garbage. A mounted secret lives as
    long as the container that mounts it, which outlives any number of
    hub restarts -- so the sweep first asks the daemon what is still
    mounted and spares those paths. Deleting one leaves the container
    permanently unstartable, which is the failure this sweep exists to
    avoid causing.
    """
    from vaibify.config.ephemeralStore import fnSweepStaleEphemeralFiles

    def fnSweepAtStartup(_app):
        setMounted = _fsetMountedHostPaths(dictCtx)
        if setMounted is None:
            # Enumeration failed: we cannot tell which files a live
            # container still bind-mounts, so deleting any of them could
            # leave that container permanently unstartable (Docker fails
            # the mount and stubs a directory where the file was). Forbid
            # the sweep entirely rather than proceed with nothing
            # protected -- an empty protected set is the DESTRUCTIVE
            # direction, not the safe one.
            return
        fnSweepStaleEphemeralFiles(setProtectedPaths=setMounted)

    app.state.listLifespanStartup.append(fnSweepAtStartup)


def _fsetMountedHostPaths(dictCtx):
    """Return every host path bind-mounted by any container, or None.

    Includes stopped containers: a stopped container is restartable, and
    its mounts are re-resolved at start. An unreachable daemon returns
    ``None`` -- deliberately distinct from an empty set (enumerated, no
    mounts) -- so the caller forbids the sweep entirely rather than
    proceeding with nothing protected, which would delete files a live
    container still mounts. Age is not evidence of garbage; reachability
    is, and an unreachable daemon means reachability is unknown.
    """
    setPaths = set()
    try:
        for container in dictCtx["docker"].containers.list(all=True):
            for dictMount in container.attrs.get("Mounts", []) or []:
                sSource = dictMount.get("Source") or ""
                if sSource:
                    setPaths.add(sSource)
    except Exception:  # noqa: BLE001 — never block hub startup
        return None
    return setPaths
