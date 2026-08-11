"""HTTP routes for the toolkit Repos panel.

Provides discovery, tracking, and per-repo git push endpoints for
the Repos panel.  These routes operate independently of any loaded
workflow: they only require a connected container.
"""

__all__ = ["fnRegisterAll"]

import asyncio
import re
from typing import List

from fastapi import HTTPException, Request
from pydantic import BaseModel

from vaibify.reproducibility.credentialRedactor import fsRedactCredentials

from .. import syncDispatcher, trackedReposManager
from ..actionCatalog import ffnAgentAction
from ..pipelineRunner import fsShellQuote
from ..pipelineServer import WORKSPACE_ROOT, fnBumpSyncEpoch
from ..projectRoots import fsResolveProjectRoot
from ..routeContext import (
    fdictCarryARefusalBackInsteadOfRaising,
    fdictRequireLaneTupleForCommit,
    fgenericRunWorkerUnderTheDrain,
    fsRefreshVerifyCacheAfterPush,
)
from ..routeScope import (
    S_CARRIER_MODE_B_LOCK_HELD,
    S_CARRIER_TYPED_READ,
    ffnDeclareCarrierMode,
)


_PATTERN_REPO_NAME = re.compile(r"^\.?[A-Za-z0-9_][A-Za-z0-9_.-]*$")


class PushStagedRequest(BaseModel):
    sCommitMessage: str = "[vaibify] Update repository"


class PushFilesRequest(BaseModel):
    sCommitMessage: str = "[vaibify] Update repository"
    listFilePaths: List[str]


class InitRepoRequest(BaseModel):
    sDirectory: str
    bCreateIfMissing: bool = False


def _fbValidateRepoName(sRepoName):
    """Return True if sRepoName is a safe repository basename.

    A single leading dot is legal: discovery surfaces hidden git
    directories (e.g. a personal ``.claude`` clone), so the Track and
    Ignore actions the prompt offers must accept them — rejecting the
    name made the prompt unanswerable and re-ask every session.
    Separators, traversal, and vaibify's own system directories stay
    banned; the pattern requires a word character after the optional
    dot, so ``.`` and ``..`` can never match.
    """
    if not sRepoName or len(sRepoName) > 255:
        return False
    if "/" in sRepoName or ".." in sRepoName:
        return False
    if sRepoName.startswith(".vaibify"):
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


def _fnRequireTrackedInSidecar(dictSidecar, sRepoName):
    """Raise HTTPException 400 if sRepoName is absent from an already-read sidecar.

    Split from :func:`_fnRequireTracked` so the push routes can ask this
    question of the sidecar they have ALREADY read inside their carrier
    worker, rather than paying a second container round-trip for the
    same file. One implementation of the refusal, so the two callers
    cannot answer a researcher differently.
    """
    if not trackedReposManager.fbIsTracked(dictSidecar, sRepoName):
        raise HTTPException(
            400,
            f"Repository '{sRepoName}' is not tracked",
        )


def _fnRequireTracked(connectionDocker, sContainerId, sRepoName):
    """Raise HTTPException 400 if sRepoName is not in listTracked."""
    _fnRequireTrackedInSidecar(
        _fdictLoadSidecar(connectionDocker, sContainerId), sRepoName,
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
        connectionDocker, sContainerId, listNames,
        fsResolveProjectRoot(sContainerId, WORKSPACE_ROOT),
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

    # `typed-read` in the strong form: every container primitive this
    # route now reaches is a declared typed read -- the sidecar fetch,
    # the two discovery reads, and the repository-status batch that
    # used to be an assembled shell script. Nothing here needs an
    # admission, which is the whole point: the Repositories panel polls
    # every five seconds, and a route on a timer that took the mutation
    # drain would make Run Step refuse at random for as long as the
    # panel stayed open.
    @app.get("/api/repos/{sContainerId}/status")
    @ffnDeclareCarrierMode(S_CARRIER_TYPED_READ)
    async def fdictHandleRepoStatus(sContainerId: str):
        dictCtx["require"](sContainerId)
        return await asyncio.to_thread(
            _fdictBuildStatusResponse,
            dictCtx["docker"], sContainerId,
        )


def _fdictDoTrackRepo(dictCtx, sContainerId, sRepoName):
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


S_INITIAL_GITIGNORE_CONTENT = (
    "# Python\n"
    "__pycache__/\n"
    "*.pyc\n"
    "\n"
    "# Editor and OS artifacts\n"
    ".DS_Store\n"
)


def _fbWriteInitialGitignoreIfAbsent(
    connectionDocker, sContainerId, sFullPath,
):
    """Write the starter .gitignore; return True when one was written.

    A pre-existing .gitignore (a non-repo directory that already has
    files) is never overwritten — the researcher's content wins.
    """
    sGitignorePath = sFullPath + "/.gitignore"
    iExitCode, _ = connectionDocker.ftResultExecuteCommand(
        sContainerId, f"test -e {fsShellQuote(sGitignorePath)}",
    )
    if iExitCode == 0:
        return False
    connectionDocker.fnWriteFile(
        sContainerId, sGitignorePath,
        S_INITIAL_GITIGNORE_CONTENT.encode("utf-8"),
    )
    return True


def _fnRunGitInitWithInitialCommit(
    connectionDocker, sContainerId, sFullPath, bCommitGitignore,
):
    """Run git init + the initial commit at sFullPath.

    The initial commit exists so downstream diff/marker logic has a
    parent; it carries the starter .gitignore when this init created
    one, and is otherwise empty.
    """
    sQuotedPath = fsShellQuote(sFullPath)
    sStageCommand = (
        f"git -C {sQuotedPath} add .gitignore && "
        if bCommitGitignore else ""
    )
    sCommand = (
        f"git -C {sQuotedPath} -c init.defaultBranch=main init && "
        f"{sStageCommand}"
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


def _fdictDoInitProjectRepo(
    connectionDocker, sContainerId, sDirectory, bCreateIfMissing,
):
    """Validate and initialize /workspace/<sDirectory> as a git repo.

    Creating a NEW project repo stays restricted to visible
    directories: hidden names are only accepted by the shared
    validator so that already-existing hidden repos, which discovery
    surfaces, can be tracked or ignored — a project repo authored
    through vaibify should never be born hidden.
    """
    _fnRequireValidRepoName(sDirectory)
    if sDirectory.startswith("."):
        raise HTTPException(
            400, f"Project repositories must be visible "
            f"directories; refusing hidden '{sDirectory}'"
        )
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
    bWroteGitignore = _fbWriteInitialGitignoreIfAbsent(
        connectionDocker, sContainerId, sFullPath,
    )
    _fnRunGitInitWithInitialCommit(
        connectionDocker, sContainerId, sFullPath, bWroteGitignore,
    )
    return {"sDirectory": sDirectory, "sFullPath": sFullPath}


def _fnRegisterInit(app, dictCtx):
    """Register POST /api/repos/{id}/init route."""

    @ffnAgentAction("init-project-repo")
    @app.post("/api/repos/{sContainerId}/init")
    @ffnDeclareCarrierMode(S_CARRIER_MODE_B_LOCK_HELD)
    async def fdictHandleInitProjectRepo(
        sContainerId: str, request: InitRepoRequest,
        requestHttp: Request,
    ):
        dictCtx["require"](sContainerId)
        return await _fgenericRunRepoWorkerUnderTheDrain(
            sContainerId,
            lambda: _fdictDoInitProjectRepo(
                dictCtx["docker"], sContainerId,
                request.sDirectory, request.bCreateIfMissing,
            ),
            "init-project-repo", requestHttp,
        )


def _fnRegisterTrack(app, dictCtx):
    """Register POST /api/repos/{id}/{name}/track route."""

    @app.post("/api/repos/{sContainerId}/{sRepoName}/track")
    @ffnDeclareCarrierMode(S_CARRIER_MODE_B_LOCK_HELD)
    async def fdictHandleTrackRepo(
        sContainerId: str, sRepoName: str, requestHttp: Request,
    ):
        dictCtx["require"](sContainerId)
        return await _fgenericRunRepoWorkerUnderTheDrain(
            sContainerId,
            lambda: _fdictDoTrackRepo(dictCtx, sContainerId, sRepoName),
            "track-repository", requestHttp,
        )


async def _fgenericRunRepoWorkerUnderTheDrain(
    sContainerId, fnEffect, sOperationTarget, requestHttp,
):
    """Run one repo mutation under the drain; re-raise a 4xx refusal here.

    Carries no extra 5xx status: every 5xx reachable from a tracked-repo
    mutation is a sidecar write that failed partway, which is exactly
    the unknown state the quarantine exists for.
    """
    def fdictHandleRunTheEffect(supervisor=None):
        del supervisor
        return fdictCarryARefusalBackInsteadOfRaising(fnEffect)

    return await fgenericRunWorkerUnderTheDrain(
        sContainerId, fdictHandleRunTheEffect, sOperationTarget, requestHttp,
    )


async def _fdictRewriteTheSidecarUnderTheDrain(
    dictCtx, sContainerId, sRepoName, fnRewrite, sOperationTarget,
    requestHttp,
):
    """Rewrite the tracked-repos sidecar holding the container's drain.

    Track, ignore and untrack are all one shape: read the sidecar, edit
    the two lists, write it back. That is a read-modify-write across two
    container round-trips, so an ownership hand-over landing between the
    read and the write would let the FORMER owner's write clobber the
    successor's, and the losing edit would be invisible -- the sidecar
    would simply be wrong about which repositories the researcher tracks.

    Mode (b) rather than mode (a) because the rewrite belongs in a
    worker thread. Mode (a) runs its effect on the calling thread, which
    is the event loop's: two blocking docker round-trips there stall
    every other request the hub is serving. The drain is the same drain
    either way; what mode (b) adds is that it is held for the WORKER's
    life rather than the requesting coroutine's.

    ``fnRewrite`` must not raise for an expected refusal. A worker that
    raises is settled through the failure path, which marks the journal
    record NEEDS RECONCILIATION and QUARANTINES the container -- correct
    for an effect whose state nobody knows, and badly wrong for a "no
    such repository". Validation therefore happens in the handler,
    before the carrier.

    Journalled as ``helper`` and not ``file-write`` even though a file
    is what changes. A ``file-write`` record is probed by comparing the
    target's hash against ``sExpectedSha256``, and the sidecar's bytes
    are computed inside the manager from state only it has read -- so
    the record would carry no expected hash, and a crashed one would
    probe as "missing its expected hash" while looking like it had a
    postcondition. A ``helper`` record claims only what is true here.
    """
    from .. import commitCarrier
    dictLaneTuple = fdictRequireLaneTupleForCommit(
        requestHttp, sContainerId, sOperationTarget,
    )

    def fgenericRewriteTheSidecar(supervisor=None):
        del supervisor
        return fnRewrite(dictCtx["docker"], sContainerId, sRepoName)

    return await commitCarrier.fdictRunLockHeldMutation(
        requestHttp.app.state, dictLaneTuple["sContainerName"],
        sContainerId, dictLaneTuple, "helper", sOperationTarget,
        fgenericRewriteTheSidecar,
    )


def _fnRegisterIgnore(app, dictCtx):
    """Register POST /api/repos/{id}/{name}/ignore route."""

    @app.post("/api/repos/{sContainerId}/{sRepoName}/ignore")
    @ffnDeclareCarrierMode(S_CARRIER_MODE_B_LOCK_HELD)
    async def fdictIgnoreRepo(
        sContainerId: str, sRepoName: str, requestHttp: Request,
    ):
        dictCtx["require"](sContainerId)
        _fnRequireValidRepoName(sRepoName)
        await _fdictRewriteTheSidecarUnderTheDrain(
            dictCtx, sContainerId, sRepoName,
            trackedReposManager.fnAddIgnored,
            "ignore-repository", requestHttp,
        )
        return {"bSuccess": True}


def _fnRegisterUntrack(app, dictCtx):
    """Register POST /api/repos/{id}/{name}/untrack route."""

    @app.post("/api/repos/{sContainerId}/{sRepoName}/untrack")
    @ffnDeclareCarrierMode(S_CARRIER_MODE_B_LOCK_HELD)
    async def fdictUntrackRepo(
        sContainerId: str, sRepoName: str, requestHttp: Request,
    ):
        dictCtx["require"](sContainerId)
        _fnRequireValidRepoName(sRepoName)
        await _fdictRewriteTheSidecarUnderTheDrain(
            dictCtx, sContainerId, sRepoName,
            trackedReposManager.fnRemoveTracked,
            "untrack-repository", requestHttp,
        )
        return {"bSuccess": True}


def _fsStoredRemoteUrl(dictSidecar, sRepoName):
    """Return the tracked repository's recorded origin URL, or ``''``."""
    for dictEntry in dictSidecar.get("listTracked", []):
        if dictEntry.get("sName") == sRepoName:
            return dictEntry.get("sUrl") or ""
    return ""


def _fsDescribePushTarget(sRepoName, sRemoteUrl):
    """Return a push's operation description, with credentials removed.

    This string reaches two places a credential must never reach: the
    operation journal on disk, which outlives the process, and the
    refusal a second session or an arriving Run Step is shown while the
    push holds the container. Naming the remote is what makes that
    refusal actionable — a researcher told "a github-push to
    github.com/owner/repo holds this container" knows which of their
    pushes is running, where "a guarded operation" tells them nothing —
    and the remote is also exactly where a token hides: a
    token-authenticated clone's origin reads
    ``https://x-access-token:<token>@github.com/owner/repo.git``, and
    vaibify stores that string verbatim in the tracked-repos sidecar
    because it is what ``git config --get remote.origin.url`` returns.

    So the URL is redacted HERE, at the single point it enters the
    record, through the shared redactor rather than a second copy of
    its rules.
    """
    if not sRemoteUrl:
        return "github-push " + sRepoName
    return (
        "github-push " + sRepoName + " -> "
        + fsRedactCredentials(sRemoteUrl)
    )


def _fdictResolveRemoteThenPush(
    dictCtx, sContainerId, sRepoName, fnPush, supervisor,
):
    """The push worker: confirm the repo is tracked, name it, then push.

    The tracked check reads the sidecar from the container, so it runs
    INSIDE the worker rather than before it: a migrated route is served
    on the enforced branch, which mints no admission, and a sidecar read
    outside the carrier is refused at the primitive. Reading it here
    also resolves the remote, which is what lets the lock holder refine
    its description from the bare repository name to the redacted URL —
    the supervisor's ``sTarget`` is mutable for exactly this, and a busy
    refusal reads it live.

    Refusals are carried back as values, never raised: a worker that
    raises is settled through the failure path, which marks its journal
    record NEEDS RECONCILIATION and quarantines the container until the
    researcher runs ``vaibify reconcile``. "That repository is not
    tracked" must not cost anybody their container.
    """
    dictSidecar = _fdictLoadSidecar(dictCtx["docker"], sContainerId)

    def fgenericCheckTrackedThenPush():
        _fnRequireTrackedInSidecar(dictSidecar, sRepoName)
        if supervisor is not None:
            supervisor.sTarget = _fsDescribePushTarget(
                sRepoName, _fsStoredRemoteUrl(dictSidecar, sRepoName),
            )
        return fnPush()

    return fdictCarryARefusalBackInsteadOfRaising(fgenericCheckTrackedThenPush)


async def _fdictPushRepositoryUnderTheDrain(
    dictCtx, sContainerId, sRepoName, fnPush, requestHttp,
):
    """Run one Repos-panel push holding the container's mutation drain.

    Mode (b) rather than mode (a): a push commits, contacts a remote,
    and can run for as long as the network takes, all in a worker
    thread. Mode (a) would run it on the event loop and stall every
    other request; more importantly the drain has to be held for the
    WORKER's life, so an ownership hand-over or a Run Step arriving
    mid-push is refused and told what is running rather than landing
    underneath a git process that keeps writing.

    The 4xx refusal is re-raised out HERE, after the supervisor has
    settled its journal record normally, so the researcher gets their
    400 and their container stays usable.
    """
    from .. import commitCarrier
    dictLaneTuple = fdictRequireLaneTupleForCommit(
        requestHttp, sContainerId, f"The push of '{sRepoName}'",
    )

    def fdictPushUnderTheSupervisor(supervisor=None):
        return _fdictResolveRemoteThenPush(
            dictCtx, sContainerId, sRepoName, fnPush, supervisor,
        )

    dictOutcome = await commitCarrier.fdictRunLockHeldMutation(
        requestHttp.app.state, dictLaneTuple["sContainerName"],
        sContainerId, dictLaneTuple, "helper",
        _fsDescribePushTarget(sRepoName, ""), fdictPushUnderTheSupervisor,
    )
    dictCarried = dictOutcome["result"]
    if dictCarried["errorRefused"] is not None:
        raise dictCarried["errorRefused"]
    return dictCarried["objResult"]


async def _fdictFinishRepoPush(
    dictCtx, sContainerId, sRepoName, dictResult, requestHttp,
):
    """Bump the badge epoch and refresh the verify cache after a push.

    The epoch bump fires even on a FAILED push, because push-staged can
    land its commit and then fail the push, and the badges must repaint
    to the post-commit truth.
    """
    fnBumpSyncEpoch(dictCtx, sContainerId)
    if not dictResult.get("bSuccess"):
        return dictResult
    sWarning = await _fsAfterRepoPushSuccess(
        dictCtx, sContainerId, sRepoName, requestHttp,
    )
    if sWarning:
        dictResult["sPostPushVerifyWarning"] = sWarning
    return dictResult


async def _fsAfterRepoPushSuccess(
    dictCtx, sContainerId, sRepoName, requestHttp=None,
):
    """Refresh caches after a successful Repos-panel push.

    When the pushed repo is the active workflow's project repo,
    re-verifies GitHub so the L2 cells clear their stale unknown
    without a manual refresh-remotes click (same contract as the
    GitHub sync push route). The sync-epoch bump lives in the routes,
    not here: it must fire even on a FAILED push, because push-staged
    can land its commit and then fail the push, and the badges must
    repaint to the post-commit truth.
    """
    dictWorkflow = (dictCtx.get("workflows") or {}).get(sContainerId)
    if not dictWorkflow:
        return ""
    sRepoPath = (dictWorkflow.get("sProjectRepoPath") or "").rstrip("/")
    if sRepoPath != "/workspace/" + sRepoName:
        return ""
    return await fsRefreshVerifyCacheAfterPush(
        dictCtx, sContainerId, dictWorkflow, "github",
        requestHttp=requestHttp,
    )


def _fnRegisterPushStaged(app, dictCtx):
    """Register POST /api/repos/{id}/{name}/push-staged route."""

    @app.post("/api/repos/{sContainerId}/{sRepoName}/push-staged")
    @ffnDeclareCarrierMode(S_CARRIER_MODE_B_LOCK_HELD)
    async def fdictPushStaged(
        sContainerId: str, sRepoName: str,
        request: PushStagedRequest, requestHttp: Request,
    ):
        dictCtx["require"](sContainerId)
        _fnRequireValidRepoName(sRepoName)
        dictResult = await _fdictPushRepositoryUnderTheDrain(
            dictCtx, sContainerId, sRepoName,
            lambda: syncDispatcher.fdictSyncResult(
                *syncDispatcher.ftResultPushStagedToGithub(
                    dictCtx["docker"], sContainerId,
                    request.sCommitMessage, "/workspace/" + sRepoName,
                )
            ),
            requestHttp,
        )
        return await _fdictFinishRepoPush(
            dictCtx, sContainerId, sRepoName, dictResult, requestHttp,
        )


def _fnRegisterPushFiles(app, dictCtx):
    """Register POST /api/repos/{id}/{name}/push-files route."""

    @app.post("/api/repos/{sContainerId}/{sRepoName}/push-files")
    @ffnDeclareCarrierMode(S_CARRIER_MODE_B_LOCK_HELD)
    async def fdictPushFiles(
        sContainerId: str, sRepoName: str,
        request: PushFilesRequest, requestHttp: Request,
    ):
        dictCtx["require"](sContainerId)
        _fnRequireValidRepoName(sRepoName)
        dictResult = await _fdictPushRepositoryUnderTheDrain(
            dictCtx, sContainerId, sRepoName,
            lambda: syncDispatcher.fdictSyncResult(
                *syncDispatcher.ftResultPushToGithub(
                    dictCtx["docker"], sContainerId,
                    request.listFilePaths, request.sCommitMessage,
                    "/workspace/" + sRepoName,
                )
            ),
            requestHttp,
        )
        return await _fdictFinishRepoPush(
            dictCtx, sContainerId, sRepoName, dictResult, requestHttp,
        )


def _fnRegisterDirtyFiles(app, dictCtx):
    """Register GET /api/repos/{id}/{name}/dirty-files route."""

    @app.get("/api/repos/{sContainerId}/{sRepoName}/dirty-files")
    async def fdictDirtyFiles(sContainerId: str, sRepoName: str):
        dictCtx["require"](sContainerId)
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
