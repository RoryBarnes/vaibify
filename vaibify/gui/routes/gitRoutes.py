"""Git-aware dashboard endpoints: status, badges, and manifest check.

Exposes:
- ``GET /api/git/{id}/status``                repo-level porcelain state
- ``GET /api/git/{id}/badges``                per-file badge triple
- ``GET /api/git/{id}/manifest-check``        uncommitted canonical files
- ``POST /api/git/{id}/commit-canonical``     commit canonical files
- ``POST /api/git/{id}/fetch-project-repo``   refresh remote-tracking refs
- ``POST /api/git/{id}/pull-project-repo``    fast-forward to origin

All git execution runs inside the container via ``docker exec`` — the
default vaibify workspace is a Docker-managed named volume whose
source path lives in the Docker Desktop VM on macOS/Windows and isn't
reachable from the host.

Every endpoint resolves the authoritative git target per request by
reading ``dictWorkflow['sProjectRepoPath']`` — the project-repo
subdirectory auto-detected from the active workflow's ``workflow.json``
location. If no project repo is attached (workflow not inside a git
work tree), each endpoint surfaces a clear error rather than silently
reporting "not a git repository" against the wrong root.
"""

__all__ = ["fnRegisterAll"]

import asyncio
import datetime
import time

from fastapi import HTTPException
from pydantic import BaseModel

from .. import (
    badgeState,
    containerGit,
    gitStatus,
    manifestCheck,
    stateContract,
)
from ..actionCatalog import fnAgentAction
from ..pipelineServer import fdictRequireWorkflow


F_FETCH_CACHE_SECONDS = 30.0
# Canonical state vocabulary emitted by ``gitStatus._fsStateFromXy`` and
# the porcelain parser is {"committed", "uncommitted", "dirty",
# "untracked", "ignored", "conflict"}. ``uncommitted`` covers index-only
# changes (added/staged/deleted-but-staged); ``dirty`` covers any
# worktree change (modified/typechange/deleted-from-worktree). Untracked
# and ignored files do not block ``git pull --ff-only``, matching git's
# native behavior, so they are intentionally absent here.
SET_TRACKED_CHANGE_STATES = {"dirty", "uncommitted", "conflict"}
_DICT_LAST_FETCH = {}


class CommitCanonicalRequest(BaseModel):
    """Body for ``POST /api/git/{id}/commit-canonical``."""
    sCommitMessage: str = ""


class FetchProjectRepoRequest(BaseModel):
    """Body for ``POST /api/git/{id}/fetch-project-repo``."""
    bForce: bool = False


def _fsRequireProjectRepo(dictWorkflow):
    """Return the active workflow's project repo path or raise 404.

    The empty-string sentinel means the workflow loaded successfully
    but is not inside a git work tree (legacy ``workflow.json`` at
    ``/workspace``). Callers must surface the missing-repo state to
    the client rather than falling back to the workspace root.
    """
    sPath = dictWorkflow.get("sProjectRepoPath", "")
    if not sPath:
        return ""
    return sPath


def _fdictNoProjectRepoResponse():
    """Return the status payload for a workflow not under version control."""
    dictEmpty = gitStatus.fdictEmptyStatus(
        "Workflow is not in a git repository",
    )
    return {
        "dictGit": dictEmpty,
        "dictBadges": {},
        "listTracked": [],
    }


def _fbArxivConfiguredFor(dictWorkflow):
    """Return True when the workflow has an arxiv remote configured."""
    dictRemotes = dictWorkflow.get("dictRemotes") or {}
    dictArxiv = dictRemotes.get("arxiv") or {}
    return bool(dictArxiv.get("sArxivId"))


def _fdictLoadCachedArxivStatus(sProjectRepoPath):
    """Return the cached arxiv verify report from ``syncStatus.json``."""
    from vaibify.reproducibility import scheduledReverify
    return scheduledReverify.fdictReadCachedSyncStatus(
        sProjectRepoPath, "arxiv",
    )


def _flistCanonicalFromContainer(
    docker, sContainerId, dictWorkflow, sProjectRepoPath,
):
    """Return canonical tracked paths using one docker exec per scan."""
    listVaibify = containerGit.flistListContainerFiles(
        docker, sContainerId, [
            stateContract.S_VAIBIFY_WORKFLOWS_GLOB,
            stateContract.S_VAIBIFY_MARKERS_GLOB,
            stateContract.S_VAIBIFY_ZENODO_REFS,
        ],
        sWorkspace=sProjectRepoPath,
    )
    listRoot = containerGit.flistListContainerFiles(
        docker, sContainerId,
        list(stateContract.TUPLE_ROOT_CONFIG_FILES),
        sWorkspace=sProjectRepoPath,
    )
    return stateContract.flistCanonicalTrackedFilesFromScans(
        dictWorkflow, listVaibify, listRoot,
    )


def _fnRegisterGitStatus(app, dictCtx):
    """Register GET /api/git/{sContainerId}/status."""

    @app.get("/api/git/{sContainerId}/status")
    async def fnGitStatus(sContainerId: str):
        dictCtx["require"]()
        dictWorkflow = fdictRequireWorkflow(
            dictCtx["workflows"], sContainerId,
        )
        sRepo = _fsRequireProjectRepo(dictWorkflow)
        if not sRepo:
            return gitStatus.fdictEmptyStatus(
                "Workflow is not in a git repository",
            )
        return await asyncio.to_thread(
            containerGit.fdictGitStatusInContainer,
            dictCtx["docker"], sContainerId, sWorkspace=sRepo,
        )


def _fnRegisterGitBadges(app, dictCtx):
    """Register GET /api/git/{sContainerId}/badges."""

    @app.get("/api/git/{sContainerId}/badges")
    async def fnGitBadges(sContainerId: str):
        dictCtx["require"]()
        dictWorkflow = fdictRequireWorkflow(
            dictCtx["workflows"], sContainerId,
        )
        sRepo = _fsRequireProjectRepo(dictWorkflow)
        if not sRepo:
            return _fdictNoProjectRepoResponse()
        docker = dictCtx["docker"]
        dictGit = await asyncio.to_thread(
            containerGit.fdictGitStatusInContainer,
            docker, sContainerId, sWorkspace=sRepo,
        )
        listTracked = await asyncio.to_thread(
            _flistCanonicalFromContainer,
            docker, sContainerId, dictWorkflow, sRepo,
        )
        dictHashes = await asyncio.to_thread(
            containerGit.fdictComputeBlobShasInContainer,
            docker, sContainerId, listTracked, sWorkspace=sRepo,
        )
        sRemoteUrl = await asyncio.to_thread(
            containerGit.fsRemoteUrlInContainer,
            docker, sContainerId, sRepo,
        )
        dictSync = dictWorkflow.get("dictSyncStatus", {}) or {}
        bArxivConfigured = _fbArxivConfiguredFor(dictWorkflow)
        dictArxivStatus = await asyncio.to_thread(
            _fdictLoadCachedArxivStatus, sRepo,
        )
        dictBadges = badgeState.fdictBadgeStateFromHashes(
            listTracked, dictGit, dictSync, dictHashes,
            sProjectRepoPath=sRepo,
            sZenodoService=dictWorkflow.get(
                "sZenodoService", "sandbox",
            ),
            dictArxivStatus=dictArxivStatus,
            bArxivConfigured=bArxivConfigured,
        )
        return {
            "dictGit": {
                "bIsRepo": dictGit.get("bIsRepo", False),
                "sBranch": dictGit.get("sBranch", ""),
                "sHeadSha": dictGit.get("sHeadSha", ""),
                "iAhead": dictGit.get("iAhead", 0),
                "iBehind": dictGit.get("iBehind", 0),
                "sRefreshedAt": dictGit.get("sRefreshedAt", ""),
                "sReason": dictGit.get("sReason", ""),
                "sRemoteUrl": sRemoteUrl,
            },
            "dictBadges": dictBadges,
            "listTracked": listTracked,
        }


def _fnRegisterManifestCheck(app, dictCtx):
    """Register GET /api/git/{sContainerId}/manifest-check."""

    @app.get("/api/git/{sContainerId}/manifest-check")
    async def fnManifestCheck(
        sContainerId: str, sService: str = "",
    ):
        dictCtx["require"]()
        dictWorkflow = fdictRequireWorkflow(
            dictCtx["workflows"], sContainerId,
        )
        sRepo = _fsRequireProjectRepo(dictWorkflow)
        if not sRepo:
            return manifestCheck.fdictBuildManifestReportFromStatus(
                gitStatus.fdictEmptyStatus(
                    "Workflow is not in a git repository",
                ),
                [],
            )
        docker = dictCtx["docker"]
        dictGit = await asyncio.to_thread(
            containerGit.fdictGitStatusInContainer,
            docker, sContainerId, sWorkspace=sRepo,
        )
        if dictGit.get("bIsRepo"):
            listTracked = await asyncio.to_thread(
                _flistCanonicalFromContainer,
                docker, sContainerId, dictWorkflow, sRepo,
            )
        else:
            listTracked = []
        listScoped = manifestCheck.flistScopeCanonicalToService(
            listTracked, dictWorkflow, sService,
        )
        return manifestCheck.fdictBuildManifestReportFromStatus(
            dictGit, listScoped,
        )


def _fnRegisterCommitCanonical(app, dictCtx):
    """Register POST /api/git/{sContainerId}/commit-canonical."""

    @fnAgentAction("commit-canonical")
    @app.post("/api/git/{sContainerId}/commit-canonical")
    async def fnCommitCanonical(
        sContainerId: str, request: CommitCanonicalRequest,
    ):
        dictCtx["require"]()
        dictWorkflow = fdictRequireWorkflow(
            dictCtx["workflows"], sContainerId,
        )
        sRepo = _fsRequireProjectRepo(dictWorkflow)
        if not sRepo:
            raise HTTPException(
                status_code=409,
                detail=(
                    "Project repo not detected for the active "
                    "workflow."
                ),
            )
        docker = dictCtx["docker"]
        dictGit = await asyncio.to_thread(
            containerGit.fdictGitStatusInContainer,
            docker, sContainerId, sWorkspace=sRepo,
        )
        if not dictGit.get("bIsRepo"):
            raise HTTPException(
                status_code=409,
                detail="Workspace is not a git repository.",
            )
        listTracked = await asyncio.to_thread(
            _flistCanonicalFromContainer,
            docker, sContainerId, dictWorkflow, sRepo,
        )
        dictReport = manifestCheck.fdictBuildManifestReportFromStatus(
            dictGit, listTracked,
        )
        listNeedsCommit = [
            dictEntry["sPath"]
            for dictEntry in dictReport["listNeedsCommit"]
        ]
        if not listNeedsCommit:
            return {
                "bSuccess": True,
                "sCommitHash": dictReport["sHeadSha"],
                "iFilesCommitted": 0,
            }
        sMessage = request.sCommitMessage or _fsDefaultCommitMessage()
        iExit, sOut = await asyncio.to_thread(
            containerGit.ftResultGitAddInContainer,
            docker, sContainerId, listNeedsCommit, sWorkspace=sRepo,
        )
        if iExit != 0:
            raise HTTPException(
                status_code=500,
                detail="git add failed: " + (sOut or "").strip(),
            )
        iExit, sOut = await asyncio.to_thread(
            containerGit.ftResultGitCommitInContainer,
            docker, sContainerId, sMessage, sWorkspace=sRepo,
        )
        if iExit != 0:
            raise HTTPException(
                status_code=500,
                detail="git commit failed: " + (sOut or "").strip(),
            )
        sCommitHash = await asyncio.to_thread(
            containerGit.fsGitHeadShaInContainer,
            docker, sContainerId, sWorkspace=sRepo,
        )
        return {
            "bSuccess": True,
            "sCommitHash": sCommitHash,
            "iFilesCommitted": len(listNeedsCommit),
        }


def _fsDefaultCommitMessage():
    """Return a default commit message stamped with the current time."""
    sNow = datetime.datetime.now(
        datetime.timezone.utc,
    ).strftime("%Y-%m-%dT%H:%M:%SZ")
    return "[vaibify] workspace state at " + sNow


def _flistTrackedDirtyPaths(dictGit):
    """Return paths in tracked-change states that block a fast-forward."""
    dictFileStates = dictGit.get("dictFileStates", {}) or {}
    return sorted(
        sPath for sPath, sState in dictFileStates.items()
        if sState in SET_TRACKED_CHANGE_STATES
    )


def _fbFetchCacheIsFresh(sContainerId, bForce):
    """Return True when the last fetch for sContainerId is within the TTL."""
    if bForce:
        return False
    fLast = _DICT_LAST_FETCH.get(sContainerId)
    if fLast is None:
        return False
    return (time.time() - fLast) < F_FETCH_CACHE_SECONDS


def _fnRecordFetchTime(sContainerId):
    """Record the wall-clock time of a successful fetch."""
    _DICT_LAST_FETCH[sContainerId] = time.time()


def _fnRegisterFetchProjectRepo(app, dictCtx):
    """Register POST /api/git/{sContainerId}/fetch-project-repo."""

    @fnAgentAction("fetch-project-repo")
    @app.post("/api/git/{sContainerId}/fetch-project-repo")
    async def fnFetchProjectRepo(
        sContainerId: str,
        request: FetchProjectRepoRequest = FetchProjectRepoRequest(),
    ):
        dictCtx["require"]()
        dictWorkflow = fdictRequireWorkflow(
            dictCtx["workflows"], sContainerId,
        )
        sRepo = _fsRequireProjectRepo(dictWorkflow)
        if not sRepo:
            raise HTTPException(
                status_code=409,
                detail=(
                    "Project repo not detected for the active "
                    "workflow."
                ),
            )
        docker = dictCtx["docker"]
        bCacheUsed = _fbFetchCacheIsFresh(sContainerId, request.bForce)
        if not bCacheUsed:
            iExit, sOut = await asyncio.to_thread(
                containerGit.ftResultGitFetchInContainer,
                docker, sContainerId, sWorkspace=sRepo,
            )
            if iExit != 0:
                raise HTTPException(
                    status_code=502,
                    detail="git fetch failed: " + (sOut or "").strip(),
                )
            _fnRecordFetchTime(sContainerId)
        dictGit = await asyncio.to_thread(
            containerGit.fdictGitStatusInContainer,
            docker, sContainerId, sWorkspace=sRepo,
        )
        return {
            "bIsRepo": dictGit.get("bIsRepo", False),
            "sBranch": dictGit.get("sBranch", ""),
            "iAhead": dictGit.get("iAhead", 0),
            "iBehind": dictGit.get("iBehind", 0),
            "sHeadSha": dictGit.get("sHeadSha", ""),
            "bCacheUsed": bCacheUsed,
        }


def _fnRegisterPullProjectRepo(app, dictCtx):
    """Register POST /api/git/{sContainerId}/pull-project-repo."""

    @fnAgentAction("pull-project-repo")
    @app.post("/api/git/{sContainerId}/pull-project-repo")
    async def fnPullProjectRepo(sContainerId: str):
        dictCtx["require"]()
        dictWorkflow = fdictRequireWorkflow(
            dictCtx["workflows"], sContainerId,
        )
        sRepo = _fsRequireProjectRepo(dictWorkflow)
        if not sRepo:
            raise HTTPException(
                status_code=409,
                detail=(
                    "Project repo not detected for the active "
                    "workflow."
                ),
            )
        docker = dictCtx["docker"]
        dictGit = await asyncio.to_thread(
            containerGit.fdictGitStatusInContainer,
            docker, sContainerId, sWorkspace=sRepo,
        )
        listDirty = _flistTrackedDirtyPaths(dictGit)
        if listDirty:
            return {
                "bSuccess": False,
                "sRefusal": "dirty-working-tree",
                "listDirtyFiles": listDirty,
                "sBranch": dictGit.get("sBranch", ""),
                "iBehind": dictGit.get("iBehind", 0),
            }
        iExit, sOut = await asyncio.to_thread(
            containerGit.ftResultGitPullFastForwardInContainer,
            docker, sContainerId, sWorkspace=sRepo,
        )
        if iExit != 0:
            raise HTTPException(
                status_code=502,
                detail="git pull --ff-only failed: " + (sOut or "").strip(),
            )
        _fnRecordFetchTime(sContainerId)
        sNewHead = await asyncio.to_thread(
            containerGit.fsGitHeadShaInContainer,
            docker, sContainerId, sWorkspace=sRepo,
        )
        dictGitAfter = await asyncio.to_thread(
            containerGit.fdictGitStatusInContainer,
            docker, sContainerId, sWorkspace=sRepo,
        )
        return {
            "bSuccess": True,
            "sNewHeadSha": sNewHead,
            "sBranch": dictGitAfter.get("sBranch", ""),
            "iBehind": dictGitAfter.get("iBehind", 0),
            "iAhead": dictGitAfter.get("iAhead", 0),
        }


def fnRegisterAll(app, dictCtx):
    """Register all git-status dashboard routes."""
    _fnRegisterGitStatus(app, dictCtx)
    _fnRegisterGitBadges(app, dictCtx)
    _fnRegisterManifestCheck(app, dictCtx)
    _fnRegisterCommitCanonical(app, dictCtx)
    _fnRegisterFetchProjectRepo(app, dictCtx)
    _fnRegisterPullProjectRepo(app, dictCtx)
