"""Git-aware dashboard endpoints: status, badges, and manifest check.

Exposes:
- ``GET /api/git/{id}/status``            repo-level porcelain state
- ``GET /api/git/{id}/badges``            per-file badge triple
- ``GET /api/git/{id}/manifest-check``    uncommitted canonical files
- ``POST /api/git/{id}/commit-canonical`` commit canonical files

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

import datetime

from fastapi import HTTPException
from pydantic import BaseModel

from .. import (
    badgeState,
    containerGit,
    gitStatus,
    manifestCheck,
    stateContract,
)
from ..pipelineServer import fdictRequireWorkflow


class CommitCanonicalRequest(BaseModel):
    """Body for ``POST /api/git/{id}/commit-canonical``."""
    sCommitMessage: str = ""


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
        return containerGit.fdictGitStatusInContainer(
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
        dictGit = containerGit.fdictGitStatusInContainer(
            docker, sContainerId, sWorkspace=sRepo,
        )
        listTracked = _flistCanonicalFromContainer(
            docker, sContainerId, dictWorkflow, sRepo,
        )
        dictHashes = containerGit.fdictComputeBlobShasInContainer(
            docker, sContainerId, listTracked, sWorkspace=sRepo,
        )
        dictSync = dictWorkflow.get("dictSyncStatus", {}) or {}
        dictBadges = badgeState.fdictBadgeStateFromHashes(
            listTracked, dictGit, dictSync, dictHashes,
            sProjectRepoPath=sRepo,
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
        dictGit = containerGit.fdictGitStatusInContainer(
            docker, sContainerId, sWorkspace=sRepo,
        )
        listTracked = _flistCanonicalFromContainer(
            docker, sContainerId, dictWorkflow, sRepo,
        ) if dictGit.get("bIsRepo") else []
        listScoped = manifestCheck.flistScopeCanonicalToService(
            listTracked, dictWorkflow, sService,
        )
        return manifestCheck.fdictBuildManifestReportFromStatus(
            dictGit, listScoped,
        )


def _fnRegisterCommitCanonical(app, dictCtx):
    """Register POST /api/git/{sContainerId}/commit-canonical."""

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
        dictGit = containerGit.fdictGitStatusInContainer(
            docker, sContainerId, sWorkspace=sRepo,
        )
        if not dictGit.get("bIsRepo"):
            raise HTTPException(
                status_code=409,
                detail="Workspace is not a git repository.",
            )
        listTracked = _flistCanonicalFromContainer(
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
        iExit, sOut = containerGit.ftResultGitAddInContainer(
            docker, sContainerId, listNeedsCommit, sWorkspace=sRepo,
        )
        if iExit != 0:
            raise HTTPException(
                status_code=500,
                detail="git add failed: " + (sOut or "").strip(),
            )
        iExit, sOut = containerGit.ftResultGitCommitInContainer(
            docker, sContainerId, sMessage, sWorkspace=sRepo,
        )
        if iExit != 0:
            raise HTTPException(
                status_code=500,
                detail="git commit failed: " + (sOut or "").strip(),
            )
        sCommitHash = containerGit.fsGitHeadShaInContainer(
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


def fnRegisterAll(app, dictCtx):
    """Register all git-status dashboard routes."""
    _fnRegisterGitStatus(app, dictCtx)
    _fnRegisterGitBadges(app, dictCtx)
    _fnRegisterManifestCheck(app, dictCtx)
    _fnRegisterCommitCanonical(app, dictCtx)
