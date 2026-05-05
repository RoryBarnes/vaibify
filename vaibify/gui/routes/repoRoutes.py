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
from ..actionCatalog import fnAgentAction
from ..pipelineRunner import fsShellQuote


_PATTERN_REPO_NAME = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.-]*$")


class PushStagedRequest(BaseModel):
    sCommitMessage: str = "[vaibify] Update repository"


class PushFilesRequest(BaseModel):
    sCommitMessage: str = "[vaibify] Update repository"
    listFilePaths: List[str]


class InitRepoRequest(BaseModel):
    sDirectory: str
    bCreateIfMissing: bool = False


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


def _flistBuildNonRepoDirs(
    connectionDocker, sContainerId, dictSidecar, setDiscovered
):
    """Return non-git /workspace dirs not already in tracked or ignored."""
    listAll = trackedReposManager.flistDiscoverNonGitDirs(
        connectionDocker, sContainerId,
    )
    setTracked = set(
        trackedReposManager.flistGetTrackedNames(dictSidecar)
    )
    setIgnored = set(_flistBuildIgnoredNames(dictSidecar))
    listResult = []
    for sName in listAll:
        if sName in setDiscovered or sName in setTracked:
            continue
        if sName in setIgnored:
            continue
        listResult.append({"sName": sName})
    return listResult


def _fdictAssembleStatusPayload(
    connectionDocker, sContainerId, dictSidecar, setDiscovered
):
    """Build the status payload with tracked/ignored/undecided/non-repo lists."""
    listTracked = _flistBuildTrackedEntries(
        connectionDocker, sContainerId, dictSidecar, setDiscovered
    )
    listIgnored = _flistBuildIgnoredNames(dictSidecar)
    listUndecided = _flistBuildUndecided(
        setDiscovered, dictSidecar, listIgnored
    )
    listNonRepoDirs = _flistBuildNonRepoDirs(
        connectionDocker, sContainerId, dictSidecar, setDiscovered
    )
    return {
        "listTracked": listTracked,
        "listIgnored": listIgnored,
        "listUndecided": listUndecided,
        "listNonRepoDirs": listNonRepoDirs,
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
        return await asyncio.to_thread(
            _fdictBuildStatusResponse,
            dictCtx["docker"], sContainerId,
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


def _fbDirectoryExists(connectionDocker, sContainerId, sFullPath):
    """Return True if sFullPath is an existing directory in the container."""
    iExitCode, _ = connectionDocker.ftResultExecuteCommand(
        sContainerId, f"test -d {fsShellQuote(sFullPath)}",
    )
    return iExitCode == 0


def _fbDirectoryIsGitRepo(connectionDocker, sContainerId, sFullPath):
    """Return True if sFullPath contains a .git/ subdirectory."""
    sGitPath = sFullPath.rstrip("/") + "/.git"
    iExitCode, _ = connectionDocker.ftResultExecuteCommand(
        sContainerId, f"test -e {fsShellQuote(sGitPath)}",
    )
    return iExitCode == 0


def _fnEnsureInitTargetDirectory(
    connectionDocker, sContainerId, sFullPath, bCreateIfMissing,
):
    """Ensure target directory exists, creating it when permitted."""
    bExists = _fbDirectoryExists(
        connectionDocker, sContainerId, sFullPath
    )
    if bExists and bCreateIfMissing:
        raise HTTPException(
            409,
            f"Directory '{sFullPath}' already exists. "
            f"Pick it from the list instead of creating a new one.",
        )
    if bExists:
        return
    if not bCreateIfMissing:
        raise HTTPException(
            404, f"Directory not found: {sFullPath}"
        )
    iExitCode, sOutput = connectionDocker.ftResultExecuteCommand(
        sContainerId, f"mkdir -p {fsShellQuote(sFullPath)}",
    )
    if iExitCode != 0:
        raise HTTPException(
            500, f"Failed to create directory: {sOutput.strip()}"
        )


def _fnRunGitInitWithEmptyCommit(
    connectionDocker, sContainerId, sFullPath,
):
    """Run git init + an empty initial commit at sFullPath."""
    sQuotedPath = fsShellQuote(sFullPath)
    sCommand = (
        f"git -C {sQuotedPath} -c init.defaultBranch=main init && "
        f"git -C {sQuotedPath} "
        f"-c user.email=vaibify@local -c user.name=vaibify "
        f"commit --allow-empty -m 'Initialize vaibify project repo'"
    )
    iExitCode, sOutput = connectionDocker.ftResultExecuteCommand(
        sContainerId, sCommand,
    )
    if iExitCode != 0:
        raise HTTPException(
            500, f"git init failed: {sOutput.strip()}"
        )


def _fnDoInitProjectRepo(
    connectionDocker, sContainerId, sDirectory, bCreateIfMissing,
):
    """Validate and initialize /workspace/<sDirectory> as a git repo."""
    _fnRequireValidRepoName(sDirectory)
    sFullPath = "/workspace/" + sDirectory
    _fnEnsureInitTargetDirectory(
        connectionDocker, sContainerId, sFullPath, bCreateIfMissing,
    )
    if _fbDirectoryIsGitRepo(
        connectionDocker, sContainerId, sFullPath
    ):
        raise HTTPException(
            409, f"Directory '{sFullPath}' is already a git repository"
        )
    _fnRunGitInitWithEmptyCommit(
        connectionDocker, sContainerId, sFullPath,
    )
    return {"sDirectory": sDirectory, "sFullPath": sFullPath}


def _fnRegisterInit(app, dictCtx):
    """Register POST /api/repos/{id}/init route."""

    @fnAgentAction("init-project-repo")
    @app.post("/api/repos/{sContainerId}/init")
    async def fnInitProjectRepo(
        sContainerId: str, request: InitRepoRequest,
    ):
        dictCtx["require"]()
        return await asyncio.to_thread(
            _fnDoInitProjectRepo,
            dictCtx["docker"], sContainerId,
            request.sDirectory, request.bCreateIfMissing,
        )


def _fnRegisterTrack(app, dictCtx):
    """Register POST /api/repos/{id}/{name}/track route."""

    @app.post("/api/repos/{sContainerId}/{sRepoName}/track")
    async def fnTrackRepo(sContainerId: str, sRepoName: str):
        dictCtx["require"]()
        return await asyncio.to_thread(
            _fnDoTrackRepo, dictCtx, sContainerId, sRepoName,
        )


def _fnRegisterIgnore(app, dictCtx):
    """Register POST /api/repos/{id}/{name}/ignore route."""

    @app.post("/api/repos/{sContainerId}/{sRepoName}/ignore")
    async def fnIgnoreRepo(sContainerId: str, sRepoName: str):
        dictCtx["require"]()
        _fnRequireValidRepoName(sRepoName)
        await asyncio.to_thread(
            trackedReposManager.fnAddIgnored,
            dictCtx["docker"], sContainerId, sRepoName,
        )
        return {"bSuccess": True}


def _fnRegisterUntrack(app, dictCtx):
    """Register POST /api/repos/{id}/{name}/untrack route."""

    @app.post("/api/repos/{sContainerId}/{sRepoName}/untrack")
    async def fnUntrackRepo(sContainerId: str, sRepoName: str):
        dictCtx["require"]()
        _fnRequireValidRepoName(sRepoName)
        await asyncio.to_thread(
            trackedReposManager.fnRemoveTracked,
            dictCtx["docker"], sContainerId, sRepoName,
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
    _fnRegisterInit(app, dictCtx)
    _fnRegisterTrack(app, dictCtx)
    _fnRegisterIgnore(app, dictCtx)
    _fnRegisterUntrack(app, dictCtx)
    _fnRegisterPushStaged(app, dictCtx)
    _fnRegisterPushFiles(app, dictCtx)
    _fnRegisterDirtyFiles(app, dictCtx)
