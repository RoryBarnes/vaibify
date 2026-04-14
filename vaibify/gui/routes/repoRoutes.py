"""HTTP routes for the toolkit Repos panel.

Provides discovery, tracking, and per-repo git push endpoints for
the Repos panel.  These routes operate independently of any loaded
workflow: they only require a connected container.
"""

__all__ = ["fnRegisterAll"]

import asyncio
import re
from typing import List

from fastapi import HTTPException
from pydantic import BaseModel

from .. import syncDispatcher, trackedReposManager


_PATTERN_REPO_NAME = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.-]*$")


class PushStagedRequest(BaseModel):
    sCommitMessage: str = "[vaibify] Update repository"


class PushFilesRequest(BaseModel):
    sCommitMessage: str = "[vaibify] Update repository"
    listFilePaths: List[str]


def _fbValidateRepoName(sRepoName):
    """Return True if sRepoName is a safe repository basename."""
    if not sRepoName or len(sRepoName) > 255:
        return False
    if "/" in sRepoName or ".." in sRepoName:
        return False
    if sRepoName.startswith("."):
        return False
    return bool(_PATTERN_REPO_NAME.match(sRepoName))


def _fnRequireValidRepoName(sRepoName):
    """Raise HTTPException 400 if the repo name fails validation."""
    if not _fbValidateRepoName(sRepoName):
        raise HTTPException(
            400, f"Invalid repository name: {sRepoName}"
        )


def _fdictLoadSidecar(connectionDocker, sContainerId):
    """Load the sidecar, returning an empty initial state on miss."""
    dictSidecar = trackedReposManager.fdictReadSidecar(
        connectionDocker, sContainerId
    )
    if dictSidecar is None:
        return trackedReposManager.fdictBuildInitialState([])
    return dictSidecar


def _fnRequireTracked(connectionDocker, sContainerId, sRepoName):
    """Raise HTTPException 400 if sRepoName is not in listTracked."""
    dictSidecar = _fdictLoadSidecar(connectionDocker, sContainerId)
    if not trackedReposManager.fbIsTracked(dictSidecar, sRepoName):
        raise HTTPException(
            400,
            f"Repository '{sRepoName}' is not tracked",
        )


def _fdictBuildTrackedEntry(
    dictStored, dictStatus, bDiscovered
):
    """Merge stored sUrl with live status dict for a tracked repo."""
    sStoredUrl = dictStored.get("sUrl")
    sLiveUrl = dictStatus.get("sUrl")
    return {
        "sName": dictStored.get("sName"),
        "sUrl": sStoredUrl if sStoredUrl else sLiveUrl,
        "sBranch": dictStatus.get("sBranch"),
        "bDirty": dictStatus.get("bDirty", False),
        "bMissing": not bDiscovered,
    }


def _flistBuildTrackedEntries(
    connectionDocker, sContainerId, dictSidecar, setDiscovered
):
    """Build the listTracked response entries from sidecar + disk."""
    listStored = [
        d for d in dictSidecar.get("listTracked", [])
        if d.get("sName")
    ]
    listNames = [d["sName"] for d in listStored]
    listStatuses = trackedReposManager.flistBatchComputeRepoStatus(
        connectionDocker, sContainerId, listNames
    )
    return _flistMergeTrackedWithStatus(
        listStored, listStatuses, setDiscovered)


def _flistMergeTrackedWithStatus(
    listStored, listStatuses, setDiscovered
):
    """Merge sidecar entries with batch-computed status dicts."""
    listResult = []
    for iIdx, dictStored in enumerate(listStored):
        dictStatus = listStatuses[iIdx] if iIdx < len(
            listStatuses) else {}
        bDiscovered = dictStored["sName"] in setDiscovered
        listResult.append(
            _fdictBuildTrackedEntry(
                dictStored, dictStatus, bDiscovered))
    return listResult


def _flistBuildIgnoredNames(dictSidecar):
    """Return ignored repo names as plain strings."""
    listNames = []
    for dictEntry in dictSidecar.get("listIgnored", []):
        sName = dictEntry.get("sName")
        if sName:
            listNames.append(sName)
    return listNames


def _flistBuildUndecided(
    setDiscovered, dictSidecar, listIgnoredNames
):
    """Return undecided repo entries as {sName: ...} dicts."""
    setTracked = set(
        trackedReposManager.flistGetTrackedNames(dictSidecar)
    )
    setIgnored = set(listIgnoredNames)
    listResult = []
    for sName in sorted(setDiscovered):
        if sName in setTracked or sName in setIgnored:
            continue
        listResult.append({"sName": sName})
    return listResult


def _fdictAssembleStatusPayload(
    connectionDocker, sContainerId, dictSidecar, setDiscovered
):
    """Build the {listTracked, listIgnored, listUndecided} payload dict."""
    listTracked = _flistBuildTrackedEntries(
        connectionDocker, sContainerId, dictSidecar, setDiscovered
    )
    listIgnored = _flistBuildIgnoredNames(dictSidecar)
    listUndecided = _flistBuildUndecided(
        setDiscovered, dictSidecar, listIgnored
    )
    return {
        "listTracked": listTracked,
        "listIgnored": listIgnored,
        "listUndecided": listUndecided,
    }


def _fdictBuildStatusResponse(connectionDocker, sContainerId):
    """Assemble the full GET /status response payload."""
    dictSidecar = trackedReposManager.fdictReadOrSeedSidecar(
        connectionDocker, sContainerId
    )
    listDiscovered = trackedReposManager.flistDiscoverGitDirs(
        connectionDocker, sContainerId
    )
    setDiscovered = set(listDiscovered)
    return _fdictAssembleStatusPayload(
        connectionDocker, sContainerId, dictSidecar, setDiscovered
    )


def _fnRegisterStatus(app, dictCtx):
    """Register GET /api/repos/{id}/status route."""

    @app.get("/api/repos/{sContainerId}/status")
    async def fnRepoStatus(sContainerId: str):
        dictCtx["require"]()
        return _fdictBuildStatusResponse(
            dictCtx["docker"], sContainerId
        )


def _fnDoTrackRepo(dictCtx, sContainerId, sRepoName):
    """Validate and add sRepoName to the tracked sidecar list."""
    _fnRequireValidRepoName(sRepoName)
    dictStatus = trackedReposManager.fdictComputeRepoStatus(
        dictCtx["docker"], sContainerId, sRepoName
    )
    if dictStatus.get("bMissing"):
        raise HTTPException(
            404, f"Repository not found: {sRepoName}"
        )
    trackedReposManager.fnAddTracked(
        dictCtx["docker"], sContainerId, sRepoName,
        dictStatus.get("sUrl"),
    )
    return {"bSuccess": True}


def _fnRegisterTrack(app, dictCtx):
    """Register POST /api/repos/{id}/{name}/track route."""

    @app.post("/api/repos/{sContainerId}/{sRepoName}/track")
    async def fnTrackRepo(sContainerId: str, sRepoName: str):
        dictCtx["require"]()
        return _fnDoTrackRepo(dictCtx, sContainerId, sRepoName)


def _fnRegisterIgnore(app, dictCtx):
    """Register POST /api/repos/{id}/{name}/ignore route."""

    @app.post("/api/repos/{sContainerId}/{sRepoName}/ignore")
    async def fnIgnoreRepo(sContainerId: str, sRepoName: str):
        dictCtx["require"]()
        _fnRequireValidRepoName(sRepoName)
        trackedReposManager.fnAddIgnored(
            dictCtx["docker"], sContainerId, sRepoName
        )
        return {"bSuccess": True}


def _fnRegisterUntrack(app, dictCtx):
    """Register POST /api/repos/{id}/{name}/untrack route."""

    @app.post("/api/repos/{sContainerId}/{sRepoName}/untrack")
    async def fnUntrackRepo(sContainerId: str, sRepoName: str):
        dictCtx["require"]()
        _fnRequireValidRepoName(sRepoName)
        trackedReposManager.fnRemoveTracked(
            dictCtx["docker"], sContainerId, sRepoName
        )
        return {"bSuccess": True}


async def _fdictDoPushStaged(
    dictCtx, sContainerId, sRepoName, sCommitMessage
):
    """Validate, then push staged changes for sRepoName to GitHub."""
    _fnRequireValidRepoName(sRepoName)
    _fnRequireTracked(dictCtx["docker"], sContainerId, sRepoName)
    sWorkdir = "/workspace/" + sRepoName
    iExit, sOut = await asyncio.to_thread(
        syncDispatcher.ftResultPushStagedToGithub,
        dictCtx["docker"], sContainerId,
        sCommitMessage, sWorkdir,
    )
    return syncDispatcher.fdictSyncResult(iExit, sOut)


def _fnRegisterPushStaged(app, dictCtx):
    """Register POST /api/repos/{id}/{name}/push-staged route."""

    @app.post("/api/repos/{sContainerId}/{sRepoName}/push-staged")
    async def fnPushStaged(
        sContainerId: str, sRepoName: str,
        request: PushStagedRequest,
    ):
        dictCtx["require"]()
        return await _fdictDoPushStaged(
            dictCtx, sContainerId, sRepoName, request.sCommitMessage
        )


def _fnRegisterPushFiles(app, dictCtx):
    """Register POST /api/repos/{id}/{name}/push-files route."""

    @app.post("/api/repos/{sContainerId}/{sRepoName}/push-files")
    async def fnPushFiles(
        sContainerId: str, sRepoName: str,
        request: PushFilesRequest,
    ):
        dictCtx["require"]()
        _fnRequireValidRepoName(sRepoName)
        _fnRequireTracked(
            dictCtx["docker"], sContainerId, sRepoName)
        iExit, sOut = await asyncio.to_thread(
            syncDispatcher.ftResultPushToGithub,
            dictCtx["docker"], sContainerId,
            request.listFilePaths, request.sCommitMessage,
            "/workspace/" + sRepoName)
        return syncDispatcher.fdictSyncResult(iExit, sOut)


def _fnRegisterDirtyFiles(app, dictCtx):
    """Register GET /api/repos/{id}/{name}/dirty-files route."""

    @app.get("/api/repos/{sContainerId}/{sRepoName}/dirty-files")
    async def fnDirtyFiles(sContainerId: str, sRepoName: str):
        dictCtx["require"]()
        _fnRequireValidRepoName(sRepoName)
        _fnRequireTracked(
            dictCtx["docker"], sContainerId, sRepoName
        )
        sWorkdir = "/workspace/" + sRepoName
        listDirty = syncDispatcher.flistGetDirtyFiles(
            dictCtx["docker"], sContainerId, sWorkdir
        )
        return {"listDirtyFiles": listDirty}


def fnRegisterAll(app, dictCtx):
    """Register every route exposed by the Repos panel."""
    _fnRegisterStatus(app, dictCtx)
    _fnRegisterTrack(app, dictCtx)
    _fnRegisterIgnore(app, dictCtx)
    _fnRegisterUntrack(app, dictCtx)
    _fnRegisterPushStaged(app, dictCtx)
    _fnRegisterPushFiles(app, dictCtx)
    _fnRegisterDirtyFiles(app, dictCtx)
