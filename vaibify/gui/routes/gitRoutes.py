"""Git-aware dashboard endpoints: status, badges, and manifest check.

Exposes:
- ``GET /api/git/{id}/status``                repo-level porcelain state
- ``GET /api/git/{id}/badges``                per-file badge triple
- ``GET /api/git/{id}/manifest-check``        uncommitted canonical files
- ``POST /api/git/{id}/commit-canonical``     commit canonical files
- ``POST /api/git/{id}/fetch-project-repo``   refresh remote-tracking refs
- ``POST /api/git/{id}/pull-project-repo``    fast-forward to origin
- ``POST /api/git/{id}/refresh-remotes``      fetch + remote-heads view

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

from typing import List, Optional

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
from ..pipelineServer import fdictRequireWorkflow, fnBumpSyncEpoch
from ..routeContext import ffilesForWorkflow
from ...reproducibility.manifestPaths import flistStepDeclarationRepoPaths


F_FETCH_CACHE_SECONDS = 30.0
# Canonical state vocabulary emitted by ``gitStatus._fsStateFromXy`` and
# the porcelain parser is {"committed", "uncommitted", "dirty",
# "untracked", "ignored", "conflict"}. ``uncommitted`` covers index-only
# changes (added/staged/deleted-but-staged); ``dirty`` covers any
# worktree change (modified/typechange/deleted-from-worktree). Untracked
# and ignored files do not block ``git pull --ff-only``, matching git's
# native behavior, so they are intentionally absent here.
SET_TRACKED_CHANGE_STATES = {"dirty", "uncommitted", "conflict"}
# Curated path-kind contract for ``commit-canonical``: only these
# vaibify-managed artifacts may flow through the agent-invokable
# commit endpoint. ``flistCanonicalTrackedFilesFromScans`` builds the
# concrete path list from these globs and the active workflow's
# manifest entries; the commit step then passes that explicit list
# into ``git commit -- <paths>`` so any pre-staged user files in the
# index are left untouched. Never replace this with ``git add -A``.
TUPLE_CURATED_COMMIT_KINDS = (
    "workflow.json (per workflow, repo-relative)",
    ".vaibify/test_markers/*/*.json",
    ".vaibify/zenodo-refs.json",
    "MANIFEST.sha256 (when present at repo root)",
    "requirements.lock (when present at repo root)",
    "reproduce.sh (when present at repo root)",
    "requirements.txt / environment.yml / Dockerfile / pyproject.toml",
    "explicit canonical entries enumerated by stateContract",
)
_DICT_LAST_FETCH = {}


class CommitCanonicalRequest(BaseModel):
    """Body for ``POST /api/git/{id}/commit-canonical``.

    ``listOnlyPaths`` optionally narrows the commit to a subset of
    the canonical needs-commit list (e.g. the AI declaration file's
    dedicated button). The server-derived canonical list stays
    authoritative: requested paths outside it are ignored, so the
    filter can narrow the commit but never widen it.
    """
    sCommitMessage: str = ""
    listOnlyPaths: Optional[List[str]] = None


class UntrackAiDeclarationRequest(BaseModel):
    """Body for ``POST /api/git/{id}/untrack-ai-declaration``.

    ``sPath`` must be a declaration file declared by an ai-declaration
    step in the active workflow — the endpoint refuses every other
    path, so it can remove the declaration from the published record
    but can never untrack arbitrary repo content.
    """
    sPath: str


class FetchProjectRepoRequest(BaseModel):
    """Body for ``POST /api/git/{id}/fetch-project-repo``."""
    bForce: bool = False


class RefreshRemotesRequest(BaseModel):
    """Body for ``POST /api/git/{id}/refresh-remotes``."""
    bForce: bool = True


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


def _fsRequireProjectRepoOrFail(dictWorkflow):
    """Return the project repo path or raise HTTP 409 when none is configured.

    Centralizes the duplicated "Project repo not detected" guard that
    state-mutating git routes share so the error message stays in lockstep.
    """
    sRepo = _fsRequireProjectRepo(dictWorkflow)
    if not sRepo:
        raise HTTPException(
            status_code=409,
            detail=(
                "Project repo not detected for the active "
                "workflow."
            ),
        )
    return sRepo


def _fbArxivConfiguredFor(dictWorkflow):
    """Return True when the workflow has an arxiv remote configured."""
    dictRemotes = dictWorkflow.get("dictRemotes") or {}
    dictArxiv = dictRemotes.get("arxiv") or {}
    return bool(dictArxiv.get("sArxivId"))


def _fdictLoadCachedArxivStatus(filesRepo):
    """Return the cached arxiv verify report from ``syncStatus.json``."""
    from vaibify.reproducibility import scheduledReverify
    return scheduledReverify.fdictReadCachedSyncStatus(
        filesRepo, "arxiv",
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


def _fdictProjectGitView(dictGit, sRemoteUrl):
    """Pack the slim dictGit subset returned to the badge dashboard."""
    return {
        "bIsRepo": dictGit.get("bIsRepo", False),
        "sBranch": dictGit.get("sBranch", ""),
        "sHeadSha": dictGit.get("sHeadSha", ""),
        "iAhead": dictGit.get("iAhead", 0),
        "iBehind": dictGit.get("iBehind", 0),
        "sRefreshedAt": dictGit.get("sRefreshedAt", ""),
        "sReason": dictGit.get("sReason", ""),
        "sRemoteUrl": sRemoteUrl,
    }


async def _tCollectGitBadgeInputs(docker, sContainerId, dictWorkflow, sRepo):
    """Gather badge inputs: three independent execs run concurrently,
    then blob hashing runs against the resolved tracked-file list.

    The docker SDK is synchronous but multiple ``asyncio.to_thread``
    calls dispatched together hit the docker daemon over independent
    HTTP requests — concurrent exec_create on one connection is safe.
    """
    dictGit, listTracked, sRemoteUrl = await asyncio.gather(
        asyncio.to_thread(
            containerGit.fdictGitStatusInContainer,
            docker, sContainerId, sWorkspace=sRepo,
        ),
        asyncio.to_thread(
            _flistCanonicalFromContainer,
            docker, sContainerId, dictWorkflow, sRepo,
        ),
        asyncio.to_thread(
            containerGit.fsRemoteUrlInContainer,
            docker, sContainerId, sRepo,
        ),
    )
    dictHashes = await asyncio.to_thread(
        containerGit.fdictComputeBlobShasInContainer,
        docker, sContainerId, listTracked, sWorkspace=sRepo,
    )
    return dictGit, listTracked, dictHashes, sRemoteUrl


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
        dictGit, listTracked, dictHashes, sRemoteUrl = (
            await _tCollectGitBadgeInputs(
                docker, sContainerId, dictWorkflow, sRepo,
            )
        )
        dictArxivStatus = await asyncio.to_thread(
            _fdictLoadCachedArxivStatus,
            ffilesForWorkflow(dictCtx, sContainerId, dictWorkflow),
        )
        dictBadges = badgeState.fdictBadgeStateFromHashes(
            listTracked, dictGit,
            dictWorkflow.get("dictSyncStatus", {}) or {},
            dictHashes,
            sProjectRepoPath=sRepo,
            sZenodoService=dictWorkflow.get(
                "sZenodoService", "sandbox",
            ),
            dictArxivStatus=dictArxivStatus,
            bArxivConfigured=_fbArxivConfiguredFor(dictWorkflow),
        )
        return {
            "dictGit": _fdictProjectGitView(dictGit, sRemoteUrl),
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
        sRepo = _fsRequireProjectRepoOrFail(dictWorkflow)
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
        if request.listOnlyPaths is not None:
            setOnly = set(request.listOnlyPaths)
            listNeedsCommit = [
                sPath for sPath in listNeedsCommit if sPath in setOnly
            ]
        if not listNeedsCommit:
            return _fdictCommitCanonicalSuccess(
                dictReport["sHeadSha"], 0,
            )
        sMessage = request.sCommitMessage or _fsDefaultCommitMessage()
        await _fnApplyCanonicalGitAddCommit(
            docker, sContainerId, sRepo, listNeedsCommit, sMessage,
        )
        sCommitHash = await asyncio.to_thread(
            containerGit.fsGitHeadShaInContainer,
            docker, sContainerId, sWorkspace=sRepo,
        )
        fnBumpSyncEpoch(dictCtx, sContainerId)
        return _fdictCommitCanonicalSuccess(
            sCommitHash, len(listNeedsCommit),
        )


async def _fnApplyCanonicalGitAddCommit(
    docker, sContainerId, sRepo, listNeedsCommit, sMessage,
):
    """Run git add + commit, raising HTTPException on either failure.

    The commit is restricted to the curated path list (workflow.json,
    .vaibify/test_markers/*, MANIFEST.sha256, requirements.lock, and
    other explicit canonical entries) so any pre-staged user files are
    not swept into the canonical commit. See TUPLE_CURATED_COMMIT_KINDS
    for the contract.
    """
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
        listFilePaths=listNeedsCommit,
    )
    if iExit != 0:
        raise HTTPException(
            status_code=500,
            detail="git commit failed: " + (sOut or "").strip(),
        )


def _fdictCommitCanonicalSuccess(sCommitHash, iFilesCommitted):
    """Build the success response for the commit-canonical endpoint."""
    return {
        "bSuccess": True,
        "sCommitHash": sCommitHash,
        "iFilesCommitted": iFilesCommitted,
    }


def _fsDefaultCommitMessage():
    """Return a default commit message stamped with the current time."""
    sNow = datetime.datetime.now(
        datetime.timezone.utc,
    ).strftime("%Y-%m-%dT%H:%M:%SZ")
    return "[vaibify] workspace state at " + sNow


def _fnRegisterUntrackAiDeclaration(app, dictCtx):
    """Register POST /api/git/{sContainerId}/untrack-ai-declaration."""

    @fnAgentAction("untrack-ai-declaration")
    @app.post("/api/git/{sContainerId}/untrack-ai-declaration")
    async def fnUntrackAiDeclaration(
        sContainerId: str, request: UntrackAiDeclarationRequest,
    ):
        dictCtx["require"]()
        dictWorkflow = fdictRequireWorkflow(
            dictCtx["workflows"], sContainerId,
        )
        sRepo = _fsRequireProjectRepoOrFail(dictWorkflow)
        _fnRequireDeclarationPath(dictWorkflow, request.sPath)
        docker = dictCtx["docker"]
        # The removal is committed WITHOUT a pathspec: `git commit --
        # <path>` records the path's WORKING-TREE content, not the
        # staged deletion — on a clean file it fails with "nothing to
        # commit", and on a modified file it silently commits the
        # file instead of removing it (found by adversarial review
        # against real git, 2026-07-03). A bare commit is safe only
        # because an already-dirty index is refused first.
        iExit, sOut = await asyncio.to_thread(
            containerGit.ftResultGitDiffCachedQuietInContainer,
            docker, sContainerId, sWorkspace=sRepo,
        )
        if iExit != 0:
            raise HTTPException(
                status_code=409,
                detail="Other changes are already staged in the "
                       "repo — commit or unstage them first, then "
                       "retry the removal.",
            )
        iExit, sOut = await asyncio.to_thread(
            containerGit.ftResultGitRemoveCachedInContainer,
            docker, sContainerId, [request.sPath], sWorkspace=sRepo,
        )
        if iExit != 0:
            raise HTTPException(
                status_code=409,
                detail="git rm --cached failed: " + (sOut or "").strip(),
            )
        iExit, sOut = await asyncio.to_thread(
            containerGit.ftResultGitCommitInContainer,
            docker, sContainerId,
            "[vaibify] remove AI declaration from the repo",
            sWorkspace=sRepo,
        )
        if iExit != 0:
            await asyncio.to_thread(
                containerGit.ftResultGitRestoreStagedInContainer,
                docker, sContainerId, [request.sPath],
                sWorkspace=sRepo,
            )
            raise HTTPException(
                status_code=500,
                detail="git commit failed: " + (sOut or "").strip(),
            )
        sCommitHash = await asyncio.to_thread(
            containerGit.fsGitHeadShaInContainer,
            docker, sContainerId, sWorkspace=sRepo,
        )
        fnBumpSyncEpoch(dictCtx, sContainerId)
        return {"bSuccess": True, "sCommitHash": sCommitHash}


def _fnRequireDeclarationPath(dictWorkflow, sPath):
    """Raise 403 unless ``sPath`` is a step's declared AI declaration.

    The declaration paths come from the same helper that feeds the
    canonical tracked-file set, so the endpoint's scope can never
    widen past what the workflow itself declares. A leading ``:`` is
    rejected outright: git treats ``:``-prefixed pathspecs as magic
    (``:(glob)**`` matches every tracked file), and the membership
    check alone cannot catch it because a hostile workflow.json can
    declare the magic string as its own sDeclarationFile.
    """
    listDeclared = []
    for dictStep in (dictWorkflow or {}).get("listSteps") or []:
        listDeclared.extend(flistStepDeclarationRepoPaths(dictStep))
    if sPath.startswith(":") or sPath not in listDeclared:
        raise HTTPException(
            status_code=403,
            detail="Only an AI declaration file can be untracked "
                   "through this endpoint.",
        )


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


async def _fnRunGitFetchOrFail(docker, sContainerId, sRepo):
    """Run ``git fetch`` in the container, raising HTTP 502 on failure.

    The failure detail is scrubbed of URL userinfo because git's
    "unable to access" errors echo the remote URL verbatim, which
    would leak an embedded credential to the client and the log.
    """
    iExit, sOut = await asyncio.to_thread(
        containerGit.ftResultGitFetchInContainer,
        docker, sContainerId, sWorkspace=sRepo,
    )
    if iExit != 0:
        raise HTTPException(
            status_code=502,
            detail="git fetch failed: "
            + containerGit._fsStripUrlUserinfo((sOut or "").strip()),
        )


def _fdictFetchStatusView(dictGit, bCacheUsed):
    """Pack the fetch-project-repo response body."""
    return {
        "bIsRepo": dictGit.get("bIsRepo", False),
        "sBranch": dictGit.get("sBranch", ""),
        "iAhead": dictGit.get("iAhead", 0),
        "iBehind": dictGit.get("iBehind", 0),
        "sHeadSha": dictGit.get("sHeadSha", ""),
        "bCacheUsed": bCacheUsed,
    }


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
        sRepo = _fsRequireProjectRepoOrFail(dictWorkflow)
        docker = dictCtx["docker"]
        bCacheUsed = _fbFetchCacheIsFresh(sContainerId, request.bForce)
        if not bCacheUsed:
            await _fnRunGitFetchOrFail(
                docker, sContainerId, sRepo,
            )
            _fnRecordFetchTime(sContainerId)
            fnBumpSyncEpoch(dictCtx, sContainerId)
        dictGit = await asyncio.to_thread(
            containerGit.fdictGitStatusInContainer,
            docker, sContainerId, sWorkspace=sRepo,
        )
        return _fdictFetchStatusView(dictGit, bCacheUsed)


async def _fdictCollectRefreshRemotesView(
    docker, sContainerId, sRepo, bCacheUsed,
):
    """Gather remote heads, repo status, and remote URL after a fetch."""
    dictRemoteHeads = await asyncio.to_thread(
        containerGit.fdictRemoteHeadsInContainer,
        docker, sContainerId, sWorkspace=sRepo,
    )
    dictGit = await asyncio.to_thread(
        containerGit.fdictGitStatusInContainer,
        docker, sContainerId, sWorkspace=sRepo,
    )
    sRemoteUrl = await asyncio.to_thread(
        containerGit.fsRemoteUrlInContainer,
        docker, sContainerId, sRepo,
    )
    return {
        "bSuccess": True,
        "bCacheUsed": bCacheUsed,
        "dictRemoteHeads": dictRemoteHeads,
        "dictGit": _fdictProjectGitView(dictGit, sRemoteUrl),
    }


def _fnRegisterRefreshRemotes(app, dictCtx):
    """Register POST /api/git/{sContainerId}/refresh-remotes."""

    @fnAgentAction("refresh-remotes")
    @app.post("/api/git/{sContainerId}/refresh-remotes")
    async def fnRefreshRemotes(
        sContainerId: str,
        request: RefreshRemotesRequest = RefreshRemotesRequest(),
    ):
        dictCtx["require"]()
        dictWorkflow = fdictRequireWorkflow(
            dictCtx["workflows"], sContainerId,
        )
        sRepo = _fsRequireProjectRepoOrFail(dictWorkflow)
        docker = dictCtx["docker"]
        bCacheUsed = _fbFetchCacheIsFresh(sContainerId, request.bForce)
        if not bCacheUsed:
            await _fnRunGitFetchOrFail(docker, sContainerId, sRepo)
            _fnRecordFetchTime(sContainerId)
        dictResponse = await _fdictCollectRefreshRemotesView(
            docker, sContainerId, sRepo, bCacheUsed,
        )
        if not bCacheUsed:
            fnBumpSyncEpoch(dictCtx, sContainerId)
        return dictResponse


def _fdictDirtyRefusalResponse(dictGit, listDirty):
    """Build the pull refusal payload sent when the working tree is dirty."""
    return {
        "bSuccess": False,
        "sRefusal": "dirty-working-tree",
        "listDirtyFiles": listDirty,
        "sBranch": dictGit.get("sBranch", ""),
        "iBehind": dictGit.get("iBehind", 0),
    }


async def _fnRunGitPullFastForwardOrFail(docker, sContainerId, sRepo):
    """Run ``git pull --ff-only`` in the container, raising HTTP 502 on failure."""
    iExit, sOut = await asyncio.to_thread(
        containerGit.ftResultGitPullFastForwardInContainer,
        docker, sContainerId, sWorkspace=sRepo,
    )
    if iExit != 0:
        raise HTTPException(
            status_code=502,
            detail="git pull --ff-only failed: "
            + containerGit._fsStripUrlUserinfo((sOut or "").strip()),
        )


def _fnRegisterPullProjectRepo(app, dictCtx):
    """Register POST /api/git/{sContainerId}/pull-project-repo."""

    @fnAgentAction("pull-project-repo")
    @app.post("/api/git/{sContainerId}/pull-project-repo")
    async def fnPullProjectRepo(sContainerId: str):
        dictCtx["require"]()
        dictWorkflow = fdictRequireWorkflow(
            dictCtx["workflows"], sContainerId,
        )
        sRepo = _fsRequireProjectRepoOrFail(dictWorkflow)
        docker = dictCtx["docker"]
        dictGit = await asyncio.to_thread(
            containerGit.fdictGitStatusInContainer,
            docker, sContainerId, sWorkspace=sRepo,
        )
        listDirty = _flistTrackedDirtyPaths(dictGit)
        if listDirty:
            return _fdictDirtyRefusalResponse(dictGit, listDirty)
        await _fnRunGitPullFastForwardOrFail(
            docker, sContainerId, sRepo,
        )
        _fnRecordFetchTime(sContainerId)
        fnBumpSyncEpoch(dictCtx, sContainerId)
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
    _fnRegisterUntrackAiDeclaration(app, dictCtx)
    _fnRegisterFetchProjectRepo(app, dictCtx)
    _fnRegisterPullProjectRepo(app, dictCtx)
    _fnRegisterRefreshRemotes(app, dictCtx)
