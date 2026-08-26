"""Git-aware dashboard endpoints: status, badges, and manifest check.

Exposes:
- ``GET /api/git/{id}/status``                repo-level porcelain state
- ``GET /api/git/{id}/badges``                per-file badge triple
- ``GET /api/git/{id}/manifest-check``        uncommitted canonical files
- ``POST /api/git/{id}/commit-canonical``     commit canonical files
- ``POST /api/git/{id}/fetch-project-repo``   refresh remote-tracking refs
- ``POST /api/git/{id}/pull-project-repo``    fast-forward to origin
- ``POST /api/git/{id}/refresh-remotes``      fetch + remote-heads view
- ``POST /api/git/{id}/reconcile-remote-state`` repaint after an
  out-of-band push

All git execution runs inside the container via ``docker exec`` — the
default vaibify workspace is a Docker-managed named volume whose
source path lives in the Docker Desktop VM on macOS/Windows and isn't
reachable from the host.

Every endpoint resolves the authoritative git target per request by
reading ``dictWorkflow['sProjectRepoPath']`` — the project-repo
subdirectory auto-detected from the active workflow's ``project.json``
location. If no project repo is attached (workflow not inside a git
work tree), each endpoint surfaces a clear error rather than silently
reporting "not a git repository" against the wrong root.
"""

__all__ = ["fnRegisterAll"]

import asyncio
import datetime
import logging
import posixpath
import time

from typing import List, Optional

from fastapi import HTTPException, Request
from pydantic import BaseModel

from .. import (
    badgeState,
    containerGit,
    gitStatus,
    manifestCheck,
    stateContract,
    workflowManager,
)
from ..actionCatalog import ffnAgentAction
from ..pipelineServer import fdictRequireWorkflow, fnBumpSyncEpoch
from ..routeContext import (
    fdictCarryARefusalBackInsteadOfRaising,
    ffilesForWorkflow,
    fdictCommitWorkflowSave,
    fdictRunAutomaticReadUnderTheDrain,
    fgenericRunWorkerUnderTheDrain,
    fsRefreshVerifyCacheAfterPush,
)
from ..routeScope import (
    S_CARRIER_MODE_A_SYNCHRONOUS,
    S_CARRIER_MODE_B_LOCK_HELD,
    ffnDeclareCarrierMode,
)
from ...config.mutationAdmission import fnReRaiseControlPlaneRefusal
from ...reproducibility.manifestPaths import flistStepDeclarationRepoPaths

logger = logging.getLogger("vaibify")


F_FETCH_CACHE_SECONDS = 30.0
# The 5xx this panel's carrier workers carry BACK as a value rather than
# raise. Every route here answers 502 when ``git fetch`` or ``git pull``
# reports a non-zero exit, which is almost always an unreachable remote
# -- and a 5xx raised inside a carrier worker poisons its journal record
# and QUARANTINES the container until the researcher runs ``vaibify
# reconcile``. A network blip must not cost anybody their container. The
# refusal is safe to carry because the git process ran to completion and
# reported its own failure: the repository's state is known, which is
# exactly what the default 4xx/5xx split cannot express.
_SET_GIT_REMOTE_REFUSAL_STATUSES = frozenset({502})
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
    "project.json (per workflow, repo-relative)",
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
    but is not inside a git work tree (legacy ``project.json`` at
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
            stateContract.S_VAIBIFY_PROJECTS_GLOB,
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
    async def fdictHandleGitStatus(sContainerId: str):
        dictCtx["require"](sContainerId)
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


def _ftCollectGitBadgeInputs(
    docker, sContainerId, dictWorkflow, sRepo, filesRepo,
):
    """Gather every badge input in one pass, in one carrier's worker.

    The four probes ran CONCURRENTLY through ``asyncio.gather`` before
    the carrier migration, and are serialized here deliberately. Each
    reaches the general exec primitive, which the gate treats as
    mutating, so on the enforced branch each needs a live admission —
    and a badge refresh is one logical operation, not four. Three
    concurrent mode-(b) acquisitions of the same container's drain
    would be three carriers where the coherent refresh wanted one, and
    the second and third would wait on the first. The cost is stated:
    three sequential round-trips instead of one, on a route the
    dashboard fires when a workflow opens and when the sync epoch
    bumps, never on the five-second poll.

    The cached arXiv status joins them because it is part of the same
    snapshot; it is a typed read (``fbaFetchFile``), so it needs no
    admission of its own and takes one only by being here.
    """
    dictGit = containerGit.fdictGitStatusInContainer(
        docker, sContainerId, sWorkspace=sRepo,
    )
    listTracked = _flistCanonicalFromContainer(
        docker, sContainerId, dictWorkflow, sRepo,
    )
    sRemoteUrl = containerGit.fsRemoteUrlInContainer(
        docker, sContainerId, sRepo,
    )
    dictHashes = containerGit.fdictComputeBlobShasInContainer(
        docker, sContainerId, listTracked, sWorkspace=sRepo,
    )
    return (
        dictGit, listTracked, dictHashes, sRemoteUrl,
        _fdictLoadCachedArxivStatus(filesRepo),
        _fsetSelectMissingPaths(docker, sContainerId, listTracked, sRepo),
        # The GitHub badge became a real remote comparison on
        # 2026-08-25, so it reads the same cached verify the Level 2
        # cells do rather than local porcelain. Loaded here with the
        # other probes because it is part of the same snapshot; it is
        # a file read, so it needs no admission of its own.
        _fdictLoadCachedGithubStatus(filesRepo),
    )


def _fdictLoadCachedGithubStatus(filesRepo):
    """Return the cached GitHub verify report, or an empty one."""
    from ...reproducibility import scheduledReverify
    try:
        return scheduledReverify.fdictReadCachedSyncStatus(
            filesRepo, "github",
        )
    except (OSError, ValueError):
        # A cache that cannot be read is not a claim that nothing
        # matches: an empty status has no sLastVerified, so every
        # badge reads unknown rather than borrowing a verdict.
        return {}


def _fsetSelectMissingPaths(docker, sContainerId, listTracked, sRepo):
    """Return the repo-relative tracked paths that are not on disk.

    A typed read, batched, and a fifth round trip on a route that
    already makes four. It is here because no other input answers the
    question: porcelain omits a file it has nothing to say about, and
    the blob-sha map omits a file it could not open AND every file
    when the probe itself fails. Asked directly, a failed probe raises
    instead of quietly emptying the repository's badges.
    """
    listAbsolute = [
        posixpath.join(sRepo, sRelPath) for sRelPath in listTracked
    ]
    listExists = docker.flistContainerPathsExist(
        sContainerId, listAbsolute,
    )
    return {
        sRelPath for sRelPath, bExists
        in zip(listTracked, listExists) if not bExists
    }


def _fdictBadgeRefreshPaused(sPausedBy):
    """Return the typed payload for a refresh the container is too busy for.

    Deliberately carries NO badge map. A paused response that answered
    with an empty one would render as "this repository has no remote
    state" — a claim about the researcher's repository, made because
    something else was running. The absence of the key is what makes an
    unguarded consumer visibly wrong rather than quietly wrong.
    """
    return {
        "bRefreshPaused": True,
        "sPausedBy": sPausedBy or "another operation",
    }


def _fnRegisterGitBadges(app, dictCtx):
    """Register GET /api/git/{sContainerId}/badges."""

    @app.get("/api/git/{sContainerId}/badges")
    @ffnDeclareCarrierMode(S_CARRIER_MODE_B_LOCK_HELD)
    async def fdictGitBadges(sContainerId: str, requestHttp: Request):
        dictCtx["require"](sContainerId)
        dictWorkflow = fdictRequireWorkflow(
            dictCtx["workflows"], sContainerId,
        )
        sRepo = _fsRequireProjectRepo(dictWorkflow)
        if not sRepo:
            return _fdictNoProjectRepoResponse()
        docker = dictCtx["docker"]
        filesRepo = ffilesForWorkflow(dictCtx, sContainerId, dictWorkflow)
        dictRead = await fdictRunAutomaticReadUnderTheDrain(
            sContainerId,
            lambda supervisor=None: _ftCollectGitBadgeInputs(
                docker, sContainerId, dictWorkflow, sRepo, filesRepo,
            ),
            "git-badges", requestHttp,
        )
        if dictRead["bPaused"]:
            return _fdictBadgeRefreshPaused(dictRead["sPausedBy"])
        (
            dictGit, listTracked, dictHashes, sRemoteUrl, dictArxivStatus,
            setMissing, dictGithubStatus,
        ) = dictRead["objResult"]
        dictBadges = badgeState.fdictBadgeStateFromHashes(
            listTracked, dictGit,
            dictWorkflow.get("dictSyncStatus", {}) or {},
            dictHashes, setMissing,
            sProjectRepoPath=sRepo,
            sZenodoService=dictWorkflow.get(
                "sZenodoService", "sandbox",
            ),
            dictArxivStatus=dictArxivStatus,
            bArxivConfigured=_fbArxivConfiguredFor(dictWorkflow),
            dictGithubStatus=dictGithubStatus,
        )
        return {
            "dictGit": _fdictProjectGitView(dictGit, sRemoteUrl),
            "dictBadges": dictBadges,
            "listTracked": listTracked,
        }


def _fnRegisterManifestCheck(app, dictCtx):
    """Register GET /api/git/{sContainerId}/manifest-check."""

    @app.get("/api/git/{sContainerId}/manifest-check")
    async def fdictManifestCheck(
        sContainerId: str, sService: str = "",
    ):
        dictCtx["require"](sContainerId)
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


async def _fgenericRunGitWorkerUnderTheDrain(
    sContainerId, fnEffect, sOperationTarget, requestHttp,
):
    """Run one git-panel mutation under the drain; re-raise its refusal.

    Mode (b) rather than mode (a) for every route in this panel. Each is
    a sequence of git commands against a remote or an index, any of
    which can run for as long as the network takes, and mode (a) runs
    its effect on the event loop. More importantly the drain has to be
    held for the WORKER's life, so an ownership hand-over or a Run Step
    arriving mid-fetch is refused and told what is running rather than
    landing underneath a git process that keeps writing.

    The refusal is re-raised OUTSIDE the carrier deliberately: by then
    the supervisor has settled its journal record normally, so the
    researcher gets their 409 or their 502 and their container stays
    usable.

    ``sOperationTarget`` is a compile-time constant at every call site
    below, never a remote URL or anything else derived from the request,
    so nothing this writes into the journal needs redacting.

    The settle-then-raise ordering lives in ``routeContext``; what stays
    here is the reason THIS panel is mode (b) and the status set only
    this panel carries.
    """
    def fdictRunTheEffect(supervisor=None):
        del supervisor
        return fdictCarryARefusalBackInsteadOfRaising(
            fnEffect, _SET_GIT_REMOTE_REFUSAL_STATUSES,
        )

    return await fgenericRunWorkerUnderTheDrain(
        sContainerId, fdictRunTheEffect, sOperationTarget, requestHttp,
    )


def _fnRegisterCommitCanonical(app, dictCtx):
    """Register POST /api/git/{sContainerId}/commit-canonical."""

    @ffnAgentAction("commit-canonical")
    @app.post("/api/git/{sContainerId}/commit-canonical")
    @ffnDeclareCarrierMode(S_CARRIER_MODE_B_LOCK_HELD)
    async def fdictHandleCommitCanonical(
        sContainerId: str, request: CommitCanonicalRequest,
        requestHttp: Request,
    ):
        dictCtx["require"](sContainerId)
        dictWorkflow = fdictRequireWorkflow(
            dictCtx["workflows"], sContainerId,
        )
        # Resolved out here because it reaches no container at all: a
        # workflow outside a git work tree is a 409 without a journal
        # record ever existing.
        sRepo = _fsRequireProjectRepoOrFail(dictWorkflow)
        dictResponse = await _fgenericRunGitWorkerUnderTheDrain(
            sContainerId,
            lambda: _fdictScanThenCommitCanonical(
                dictCtx["docker"], sContainerId, dictWorkflow, sRepo,
                request,
            ),
            "commit-canonical", requestHttp,
        )
        if dictResponse["iFilesCommitted"]:
            fnBumpSyncEpoch(dictCtx, sContainerId)
        return dictResponse


def _fdictScanThenCommitCanonical(
    docker, sContainerId, dictWorkflow, sRepo, request,
):
    """Scan the repo for canonical changes and commit them, in one worker.

    The scan reaches the general exec primitive three times over -- a
    porcelain status, two glob listings, a head-sha read -- which the
    gate treats as mutating because a primitive handed command text
    cannot know what the text does. A scan left outside the carrier
    would be refused on the enforced branch, and it belongs inside the
    SAME held drain as the commit in any case: with the lock dropped
    between "these are the files that need committing" and the commit
    itself, another session's write lands in the gap and rides into a
    commit that never inspected it.
    """
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
    listNeedsCommit = _flistNarrowToRequestedPaths(
        dictReport["listNeedsCommit"], request.listOnlyPaths,
    )
    if not listNeedsCommit:
        return _fdictCommitCanonicalSuccess(dictReport["sHeadSha"], 0)
    _fnApplyCanonicalGitAddCommit(
        docker, sContainerId, sRepo, listNeedsCommit,
        request.sCommitMessage or _fsDefaultCommitMessage(),
    )
    return _fdictCommitCanonicalSuccess(
        containerGit.fsGitHeadShaInContainer(
            docker, sContainerId, sWorkspace=sRepo,
        ),
        len(listNeedsCommit),
    )


def _flistNarrowToRequestedPaths(listNeedsCommit, listOnlyPaths):
    """Return the needs-commit paths, narrowed to a requested subset.

    The server-derived list stays authoritative: a requested path
    outside it is ignored, so the filter can narrow the commit but
    never widen it.
    """
    listPaths = [dictEntry["sPath"] for dictEntry in listNeedsCommit]
    if listOnlyPaths is None:
        return listPaths
    setOnly = set(listOnlyPaths)
    return [sPath for sPath in listPaths if sPath in setOnly]


def _fnApplyCanonicalGitAddCommit(
    docker, sContainerId, sRepo, listNeedsCommit, sMessage,
):
    """Run git add + commit, raising HTTPException on either failure.

    The commit is restricted to the curated path list (project.json,
    .vaibify/test_markers/*, MANIFEST.sha256, requirements.lock, and
    other explicit canonical entries) so any pre-staged user files are
    not swept into the canonical commit. See TUPLE_CURATED_COMMIT_KINDS
    for the contract.

    Both failures are 500 and are therefore NOT carried back: a ``git
    add`` that failed partway leaves an index nobody has inspected, and
    that unknown state is exactly what the quarantine exists for.
    """
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

    @ffnAgentAction("untrack-ai-declaration")
    @app.post("/api/git/{sContainerId}/untrack-ai-declaration")
    @ffnDeclareCarrierMode(S_CARRIER_MODE_B_LOCK_HELD)
    async def fdictUntrackAiDeclaration(
        sContainerId: str, request: UntrackAiDeclarationRequest,
        requestHttp: Request,
    ):
        dictCtx["require"](sContainerId)
        dictWorkflow = fdictRequireWorkflow(
            dictCtx["workflows"], sContainerId,
        )
        # Both guards read the workflow dict alone and reach no
        # container, so they answer before any journal record exists.
        sRepo = _fsRequireProjectRepoOrFail(dictWorkflow)
        _fnRequireDeclarationPath(dictWorkflow, request.sPath)
        dictResponse = await _fgenericRunGitWorkerUnderTheDrain(
            sContainerId,
            lambda: _fdictRemoveDeclarationFromTheIndex(
                dictCtx["docker"], sContainerId, sRepo, request.sPath,
            ),
            "untrack-ai-declaration", requestHttp,
        )
        fnBumpSyncEpoch(dictCtx, sContainerId)
        return dictResponse


def _fdictRemoveDeclarationFromTheIndex(
    docker, sContainerId, sRepo, sPath,
):
    """Refuse a dirty index, untrack the declaration, commit the removal.

    One worker rather than four because the sequence is a
    read-modify-write over the git index: the dirty-index refusal is
    what makes the bare commit below safe, and with the drain dropped
    between the two, another session stages a change in the gap and it
    rides into this commit.

    The removal is committed WITHOUT a pathspec: ``git commit --
    <path>`` records the path's WORKING-TREE content, not the staged
    deletion — on a clean file it fails with "nothing to commit", and
    on a modified file it silently commits the file instead of removing
    it (found by adversarial review against real git, 2026-07-03).
    """
    iExit, sOut = containerGit.ftResultGitDiffCachedQuietInContainer(
        docker, sContainerId, sWorkspace=sRepo,
    )
    if iExit != 0:
        raise HTTPException(
            status_code=409,
            detail="Other changes are already staged in the "
                   "repo — commit or unstage them first, then "
                   "retry the removal.",
        )
    iExit, sOut = containerGit.ftResultGitRemoveCachedInContainer(
        docker, sContainerId, [sPath], sWorkspace=sRepo,
    )
    if iExit != 0:
        raise HTTPException(
            status_code=409,
            detail="git rm --cached failed: " + (sOut or "").strip(),
        )
    iExit, sOut = containerGit.ftResultGitCommitInContainer(
        docker, sContainerId,
        "[vaibify] remove AI declaration from the repo",
        sWorkspace=sRepo,
    )
    if iExit != 0:
        containerGit.ftResultGitRestoreStagedInContainer(
            docker, sContainerId, [sPath], sWorkspace=sRepo,
        )
        raise HTTPException(
            status_code=500,
            detail="git commit failed: " + (sOut or "").strip(),
        )
    return {
        "bSuccess": True,
        "sCommitHash": containerGit.fsGitHeadShaInContainer(
            docker, sContainerId, sWorkspace=sRepo,
        ),
    }


def _fnRequireDeclarationPath(dictWorkflow, sPath):
    """Raise 403 unless ``sPath`` is a step's declared AI declaration.

    The declaration paths come from the same helper that feeds the
    canonical tracked-file set, so the endpoint's scope can never
    widen past what the workflow itself declares. A leading ``:`` is
    rejected outright: git treats ``:``-prefixed pathspecs as magic
    (``:(glob)**`` matches every tracked file), and the membership
    check alone cannot catch it because a hostile project.json can
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


def _fbProjectRepoHasAnOrigin(docker, sContainerId, sRepo):
    """Return True when the project repo has an ``origin`` to fetch from.

    ``git fetch --no-tags origin`` NAMES the remote, so in a repository
    that has none it exits 128 with "'origin' does not appear to be a
    git repository" — and this route turned that into a 502 on every
    workflow open. A repository with no remote is not an error; it is a
    researcher who has not pushed anywhere yet, which is the ordinary
    state of a brand-new project and the near-universal state of a host
    project, since host mode exists to get somebody working in minutes.

    Pre-existing and mode-independent: a containerized local-only repo
    502'd identically. It surfaced here because this is the first
    journey that ever opened a workflow in a repository with no remote.

    One extra read on a path that is TTL-cached and about to run git
    anyway, and it stays inside the caller's carrier.
    """
    return bool(
        containerGit.fsRemoteUrlInContainer(
            docker, sContainerId, sRepo,
        ),
    )


def _fnRunGitFetchOrFail(docker, sContainerId, sRepo):
    """Run ``git fetch`` in the container, raising HTTP 502 on failure.

    The failure detail is scrubbed of URL userinfo because git's
    "unable to access" errors echo the remote URL verbatim, which
    would leak an embedded credential to the client and the log. That
    scrubbing is why the 502 is safe to carry back through the carrier:
    the message a researcher sees is the same one that would have
    reached the journal, minus the credential either way.
    """
    iExit, sOut = containerGit.ftResultGitFetchInContainer(
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

    @ffnAgentAction("fetch-project-repo")
    @app.post("/api/git/{sContainerId}/fetch-project-repo")
    @ffnDeclareCarrierMode(S_CARRIER_MODE_B_LOCK_HELD)
    async def fdictHandleFetchProjectRepo(
        sContainerId: str, requestHttp: Request,
        request: FetchProjectRepoRequest = FetchProjectRepoRequest(),
    ):
        dictCtx["require"](sContainerId)
        dictWorkflow = fdictRequireWorkflow(
            dictCtx["workflows"], sContainerId,
        )
        sRepo = _fsRequireProjectRepoOrFail(dictWorkflow)
        bCacheUsed = _fbFetchCacheIsFresh(sContainerId, request.bForce)
        return await _fgenericRunGitWorkerUnderTheDrain(
            sContainerId,
            lambda: _fdictFetchThenReadStatus(
                dictCtx, sContainerId, sRepo, bCacheUsed,
            ),
            "git-fetch", requestHttp,
        )


def _fdictFetchThenReadStatus(dictCtx, sContainerId, sRepo, bCacheUsed):
    """Fetch unless the TTL is still warm, then read the repo status.

    The status read is inside the carrier with the fetch because it is
    an ordinary container exec, and on the enforced branch an exec left
    outside a carrier is refused at the primitive. Both host-side
    bookkeeping calls stay in their original order relative to the git
    commands, so a fetch that succeeded and a status read that then
    failed still records the fetch — as it did before this migration.
    """
    docker = dictCtx["docker"]
    if not bCacheUsed and _fbProjectRepoHasAnOrigin(
        docker, sContainerId, sRepo,
    ):
        _fnRunGitFetchOrFail(docker, sContainerId, sRepo)
        _fnRecordFetchTime(sContainerId)
        fnBumpSyncEpoch(dictCtx, sContainerId)
    return _fdictFetchStatusView(
        containerGit.fdictGitStatusInContainer(
            docker, sContainerId, sWorkspace=sRepo,
        ),
        bCacheUsed,
    )


def _fdictCollectRefreshRemotesView(
    docker, sContainerId, sRepo, bCacheUsed,
):
    """Gather remote heads, repo status, and remote URL after a fetch."""
    dictRemoteHeads = containerGit.fdictRemoteHeadsInContainer(
        docker, sContainerId, sWorkspace=sRepo,
    )
    dictGit = containerGit.fdictGitStatusInContainer(
        docker, sContainerId, sWorkspace=sRepo,
    )
    sRemoteUrl = containerGit.fsRemoteUrlInContainer(
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

    @ffnAgentAction("refresh-remotes")
    @app.post("/api/git/{sContainerId}/refresh-remotes")
    @ffnDeclareCarrierMode(S_CARRIER_MODE_B_LOCK_HELD)
    async def fdictRefreshRemotes(
        sContainerId: str, requestHttp: Request,
        request: RefreshRemotesRequest = RefreshRemotesRequest(),
    ):
        dictCtx["require"](sContainerId)
        dictWorkflow = fdictRequireWorkflow(
            dictCtx["workflows"], sContainerId,
        )
        sRepo = _fsRequireProjectRepoOrFail(dictWorkflow)
        bCacheUsed = _fbFetchCacheIsFresh(sContainerId, request.bForce)
        dictResponse = await _fgenericRunGitWorkerUnderTheDrain(
            sContainerId,
            lambda: _fdictFetchThenCollectRemotes(
                dictCtx["docker"], sContainerId, sRepo, bCacheUsed,
            ),
            "git-fetch", requestHttp,
        )
        if not bCacheUsed:
            fnBumpSyncEpoch(dictCtx, sContainerId)
        return dictResponse


def _fdictFetchThenCollectRemotes(
    docker, sContainerId, sRepo, bCacheUsed,
):
    """Fetch unless the TTL is warm, then read the remote-heads view.

    Bumping the sync epoch is left to the caller, because the two
    routes sharing this worker bump at different points: refresh-remotes
    bumps only when it actually fetched, while reconcile bumps once at
    the very end, after its verify and its bookkeeping save. Bumping
    here would give reconcile two epochs for one action.
    """
    if not bCacheUsed:
        _fnRunGitFetchOrFail(docker, sContainerId, sRepo)
        _fnRecordFetchTime(sContainerId)
    return _fdictCollectRefreshRemotesView(
        docker, sContainerId, sRepo, bCacheUsed,
    )


def _fdictDirtyRefusalResponse(dictGit, listDirty):
    """Build the pull refusal payload sent when the working tree is dirty."""
    return {
        "bSuccess": False,
        "sRefusal": "dirty-working-tree",
        "listDirtyFiles": listDirty,
        "sBranch": dictGit.get("sBranch", ""),
        "iBehind": dictGit.get("iBehind", 0),
    }


def _fnRunGitPullFastForwardOrFail(docker, sContainerId, sRepo):
    """Run ``git pull --ff-only`` in the container, raising HTTP 502 on failure."""
    iExit, sOut = containerGit.ftResultGitPullFastForwardInContainer(
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

    @ffnAgentAction("pull-project-repo")
    @app.post("/api/git/{sContainerId}/pull-project-repo")
    @ffnDeclareCarrierMode(S_CARRIER_MODE_B_LOCK_HELD)
    async def fdictHandlePullProjectRepo(sContainerId: str, requestHttp: Request):
        dictCtx["require"](sContainerId)
        dictWorkflow = fdictRequireWorkflow(
            dictCtx["workflows"], sContainerId,
        )
        sRepo = _fsRequireProjectRepoOrFail(dictWorkflow)
        return await _fgenericRunGitWorkerUnderTheDrain(
            sContainerId,
            lambda: _fdictCheckCleanThenFastForward(
                dictCtx, sContainerId, sRepo,
            ),
            "git-pull", requestHttp,
        )


def _fdictCheckCleanThenFastForward(dictCtx, sContainerId, sRepo):
    """Refuse a dirty work tree, then fast-forward and re-read the state.

    The dirty check and the pull share one held drain because they are
    a check-then-act pair: with the lock dropped between them, a write
    lands in the gap and ``git pull --ff-only`` clobbers or refuses
    against a tree the check said was clean.

    The dirty refusal is an ordinary 200 body rather than an exception,
    so it needs no carry-back — it is simply this worker's return value.
    """
    docker = dictCtx["docker"]
    dictGit = containerGit.fdictGitStatusInContainer(
        docker, sContainerId, sWorkspace=sRepo,
    )
    listDirty = _flistTrackedDirtyPaths(dictGit)
    if listDirty:
        return _fdictDirtyRefusalResponse(dictGit, listDirty)
    _fnRunGitPullFastForwardOrFail(docker, sContainerId, sRepo)
    _fnRecordFetchTime(sContainerId)
    fnBumpSyncEpoch(dictCtx, sContainerId)
    sNewHead = containerGit.fsGitHeadShaInContainer(
        docker, sContainerId, sWorkspace=sRepo,
    )
    dictGitAfter = containerGit.fdictGitStatusInContainer(
        docker, sContainerId, sWorkspace=sRepo,
    )
    return {
        "bSuccess": True,
        "sNewHeadSha": sNewHead,
        "sBranch": dictGitAfter.get("sBranch", ""),
        "iBehind": dictGitAfter.get("iBehind", 0),
        "iAhead": dictGitAfter.get("iAhead", 0),
    }


def _flistProvenGithubSyncedPaths(dictWorkflow, dictStatus):
    """Return the canonical paths this GitHub verify proved match origin.

    Returns ``[]`` unless the verify covered every declared canonical
    path — ``iTotalFiles`` counts only the declared paths that existed
    locally, so a short count means the verify never looked at some of
    them, and a file it never looked at cannot be recorded as synced.
    """
    from vaibify.reproducibility import manifestWriter
    if not (dictStatus or {}).get("sLastVerified"):
        return []
    listCanonical = manifestWriter.flistCollectCanonicalRepoPaths(
        dictWorkflow,
    )
    if dictStatus.get("iTotalFiles") != len(listCanonical):
        return []
    setDiverged = {
        (dictEntry or {}).get("sPath")
        for dictEntry in dictStatus.get("listDiverged") or []
    }
    return [
        sPath for sPath in listCanonical if sPath not in setDiverged
    ]


def _fdictReconcileSyncStatusFromVerify(
    dictCtx, sContainerId, dictWorkflow, requestHttp,
):
    """Record the refreshed GitHub verify into the workflow's sync status.

    The verify cache is the only evidence an out-of-band push leaves
    behind, so ``dictSyncStatus`` is updated from it and from nothing
    else. Bookkeeping failures are logged rather than raised: the
    remote state was still reconciled, and turning that into a 500
    would hide the reconciliation that did happen.

    The cache READ is a typed read (``fbIsFile`` then a base64 fetch),
    which the audited-read carve-out exempts, so it needs no carrier.
    The SAVE is a container write and gets mode (a), the same carrier
    every other ``project.json`` save in the hub uses.

    A carrier refusal is re-raised rather than swallowed. The broad
    handler exists to keep a bookkeeping failure from hiding a
    reconciliation that happened — but a refusal is not a bookkeeping
    failure, and letting this swallow one would delete the migration's
    only proof: forget the carrier and nothing would raise at all.
    """
    from vaibify.reproducibility import scheduledReverify
    filesRepo = ffilesForWorkflow(dictCtx, sContainerId, dictWorkflow)
    dictStatus = scheduledReverify.fdictReadCachedSyncStatus(
        filesRepo, "github",
    )
    listProven = _flistProvenGithubSyncedPaths(dictWorkflow, dictStatus)
    if not listProven:
        return dictStatus
    try:
        workflowManager.fnUpdateSyncStatus(
            dictWorkflow, listProven, "Github",
        )
        fdictCommitWorkflowSave(
            dictCtx, sContainerId, dictWorkflow, requestHttp,
            "The reconcile bookkeeping save",
        )
    except Exception as error:
        fnReRaiseControlPlaneRefusal(error)
        logger.warning(
            "Reconcile bookkeeping failed for container %s; the "
            "remote state was refreshed but dictSyncStatus lags.",
            sContainerId, exc_info=True,
        )
    return dictStatus


def _fnRegisterReconcileRemoteState(app, dictCtx):
    """Register POST /api/git/{sContainerId}/reconcile-remote-state.

    The single action that repairs the dashboard after work that
    bypassed vaibify — an agent or a researcher typing ``git push`` in
    the container terminal. It re-fetches origin, re-runs the GitHub
    verify that the Level-2 cells read, records what the verify
    proved, and bumps the sync epoch so every open tab repaints once.
    """

    @ffnAgentAction("reconcile-remote-state")
    @app.post("/api/git/{sContainerId}/reconcile-remote-state")
    @ffnDeclareCarrierMode(
        S_CARRIER_MODE_A_SYNCHRONOUS, S_CARRIER_MODE_B_LOCK_HELD,
    )
    async def fdictReconcileRemoteState(
        sContainerId: str, requestHttp: Request,
    ):
        dictCtx["require"](sContainerId)
        dictWorkflow = fdictRequireWorkflow(
            dictCtx["workflows"], sContainerId,
        )
        sRepo = _fsRequireProjectRepoOrFail(dictWorkflow)
        # Three mutations, deliberately SEQUENTIAL rather than nested:
        # the fetch and the verify each take the container's mutation
        # drain, and a mode-(b) carrier opened inside another's held
        # lock would deadlock on it. Each completes before the next
        # begins.
        dictResponse = await _fgenericRunGitWorkerUnderTheDrain(
            sContainerId,
            lambda: _fdictFetchThenCollectRemotes(
                dictCtx["docker"], sContainerId, sRepo, False,
            ),
            "git-fetch", requestHttp,
        )
        dictResponse["sVerifyWarning"] = (
            await fsRefreshVerifyCacheAfterPush(
                dictCtx, sContainerId, dictWorkflow, "github",
                requestHttp,
            )
        )
        dictResponse["dictVerifyStatus"] = (
            _fdictReconcileSyncStatusFromVerify(
                dictCtx, sContainerId, dictWorkflow, requestHttp,
            )
        )
        fnBumpSyncEpoch(dictCtx, sContainerId)
        return dictResponse


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
    _fnRegisterReconcileRemoteState(app, dictCtx)
