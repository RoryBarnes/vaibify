"""Git-aware dashboard endpoints: status, badges, and manifest check.

Phase 3+4+5 of the workspace-as-git-repo plan. Exposes:
- ``GET /api/git/{id}/status``            repo-level porcelain state
- ``GET /api/git/{id}/badges``            per-file badge triple
- ``GET /api/git/{id}/manifest-check``    uncommitted canonical files
- ``POST /api/git/{id}/commit-canonical`` commit canonical files

All git execution is **container-side** via docker exec, because the
default vaibify workspace is a Docker-managed named volume — its
source path is inside the Docker Desktop VM on macOS/Windows and
cannot be reached from the host's Python. Running git inside the
container makes the routes behave identically on every host OS.
"""

__all__ = ["fnRegisterAll"]

import datetime

from fastapi import HTTPException
from pydantic import BaseModel

from .. import (
    badgeState,
    containerGit,
    manifestCheck,
    stateContract,
)
from ..pipelineServer import fdictRequireWorkflow


class CommitCanonicalRequest(BaseModel):
    """Body for ``POST /api/git/{id}/commit-canonical``."""
    sCommitMessage: str = ""


def _flistCanonicalFromContainer(docker, sContainerId, dictWorkflow):
    """Return canonical tracked paths using one docker exec per scan."""
    listVaibify = containerGit.flistListContainerFiles(
        docker, sContainerId, [
            stateContract.S_VAIBIFY_WORKFLOWS_GLOB,
            stateContract.S_VAIBIFY_MARKERS_GLOB,
            stateContract.S_VAIBIFY_ZENODO_REFS,
        ],
    )
    listRoot = containerGit.flistListContainerFiles(
        docker, sContainerId,
        list(stateContract.TUPLE_ROOT_CONFIG_FILES),
    )
    return stateContract.flistCanonicalTrackedFilesFromScans(
        dictWorkflow, listVaibify, listRoot,
    )


def _fnRegisterGitStatus(app, dictCtx):
    """Register GET /api/git/{sContainerId}/status."""

    @app.get("/api/git/{sContainerId}/status")
    async def fnGitStatus(sContainerId: str):
        dictCtx["require"]()
        return containerGit.fdictGitStatusInContainer(
            dictCtx["docker"], sContainerId,
        )


def _fnRegisterGitBadges(app, dictCtx):
    """Register GET /api/git/{sContainerId}/badges."""

    @app.get("/api/git/{sContainerId}/badges")
    async def fnGitBadges(sContainerId: str):
        dictCtx["require"]()
        dictWorkflow = fdictRequireWorkflow(
            dictCtx["workflows"], sContainerId,
        )
        docker = dictCtx["docker"]
        dictGit = containerGit.fdictGitStatusInContainer(
            docker, sContainerId,
        )
        listTracked = _flistCanonicalFromContainer(
            docker, sContainerId, dictWorkflow,
        )
        dictHashes = containerGit.fdictComputeBlobShasInContainer(
            docker, sContainerId, listTracked,
        )
        dictSync = dictWorkflow.get("dictSyncStatus", {}) or {}
        dictBadges = badgeState.fdictBadgeStateFromHashes(
            listTracked, dictGit, dictSync, dictHashes,
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
    async def fnManifestCheck(sContainerId: str):
        dictCtx["require"]()
        dictWorkflow = fdictRequireWorkflow(
            dictCtx["workflows"], sContainerId,
        )
        docker = dictCtx["docker"]
        dictGit = containerGit.fdictGitStatusInContainer(
            docker, sContainerId,
        )
        listTracked = _flistCanonicalFromContainer(
            docker, sContainerId, dictWorkflow,
        ) if dictGit.get("bIsRepo") else []
        return manifestCheck.fdictBuildManifestReportFromStatus(
            dictGit, listTracked,
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
        docker = dictCtx["docker"]
        dictGit = containerGit.fdictGitStatusInContainer(
            docker, sContainerId,
        )
        if not dictGit.get("bIsRepo"):
            raise HTTPException(
                status_code=409,
                detail="Workspace is not a git repository.",
            )
        listTracked = _flistCanonicalFromContainer(
            docker, sContainerId, dictWorkflow,
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
            docker, sContainerId, listNeedsCommit,
        )
        if iExit != 0:
            raise HTTPException(
                status_code=500,
                detail="git add failed: " + (sOut or "").strip(),
            )
        iExit, sOut = containerGit.ftResultGitCommitInContainer(
            docker, sContainerId, sMessage,
        )
        if iExit != 0:
            raise HTTPException(
                status_code=500,
                detail="git commit failed: " + (sOut or "").strip(),
            )
        sCommitHash = containerGit.fsGitHeadShaInContainer(
            docker, sContainerId,
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
