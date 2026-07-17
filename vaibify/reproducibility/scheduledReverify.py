"""Periodic re-verification of workflow manifests against remote mirrors.

This module is the single source of truth for the AICS Level 3
"authoritative re-verify" loop. The HTTP routes
``POST /api/sync/{sId}/{sService}/verify`` and the FastAPI lifespan
scheduler both delegate here so the verify logic is described once and
covered by one set of tests.

Verification hashes the workflow's declared canonical files AS THEY
EXIST at verify time and compares them against the hash of the same
path served by the remote mirror (GitHub, Overleaf, Zenodo, arXiv).
``MANIFEST.sha256`` plays no role here — the manifest is the L3
reproducibility-envelope artifact (a published fingerprint file for
third parties), while the L2 claim these verifies evidence is "the
files I have right now match the published copies". The result is
persisted to ``<sProjectRepo>/.vaibify/syncStatus.json`` keyed by
service so the dashboard can show cached state without re-running the
network round trip on every poll.

The scheduled-loop entry point :func:`fnScheduleReverify` registers an
``asyncio`` task on the FastAPI lifespan that walks every loaded
workflow at a configurable cadence. The first iteration is delayed by
a full cadence interval (not ``0``) to avoid hammering remotes on
every server restart. :func:`fnRunReverifyOnce` is a pure function of
its inputs so tests can drive a single iteration without touching the
event loop.
"""

import asyncio
import hashlib
import json
import os
import posixpath
from datetime import datetime, timezone

from vaibify.reproducibility import (
    arxivClient,
    githubMirror,
    manifestWriter,
    overleafMirror,
    overleafSync,
    zenodoClient,
)
from vaibify.reproducibility.repoFiles import ffilesEnsureRepoFiles


__all__ = [
    "S_SYNC_STATUS_FILENAME",
    "S_MANIFEST_FILENAME",
    "ReverifyConfigError",
    "fdictComputeLiveExpectedHashes",
    "fdictLoadManifestExpectedHashes",
    "fdictVerifyRemoteService",
    "fdictReadCachedSyncStatus",
    "fnDeleteSyncStatus",
    "fnWriteSyncStatus",
    "fdictRunReverifyForWorkflow",
    "fnRunReverifyOnce",
    "fnScheduleReverify",
    "fsArxivCacheDir",
]


S_MANIFEST_FILENAME = "MANIFEST.sha256"
S_SYNC_STATUS_FILENAME = "syncStatus.json"
S_VAIBIFY_DIRECTORY = ".vaibify"
LIST_SUPPORTED_SERVICES = ("github", "overleaf", "zenodo", "arxiv")
S_ARXIV_CACHE_DIRECTORY = "arxivCache"
_F_DEFAULT_CADENCE_HOURS = 6.0


class ReverifyConfigError(ValueError):
    """Raised when a workflow has missing/invalid remote configuration."""


def fdictLoadManifestExpectedHashes(filesRepo):
    """Return ``{relpath: sha256_hex}`` parsed from MANIFEST.sha256.

    Delegates to :func:`manifestWriter.flistParseManifestLines` so all
    callers share one strict parser (including GNU-escape handling for
    paths with embedded newlines or backslashes). Raises
    ``FileNotFoundError`` when the manifest does not exist so callers
    can map the failure to a 409 response. Re-raises ``ValueError``
    from the parser so corrupt manifests surface as 5xx rather than
    being silently treated as empty.
    """
    listEntries = manifestWriter.flistParseManifestLines(filesRepo)
    return {
        dictEntry["sPath"]: dictEntry["sExpected"]
        for dictEntry in listEntries
    }


def _fdictRequireServiceConfig(dictWorkflow, sService):
    """Return the per-service config dict from dictWorkflow or raise."""
    if sService not in LIST_SUPPORTED_SERVICES:
        raise ReverifyConfigError(
            f"Unsupported service '{sService}'."
        )
    dictRemotes = dictWorkflow.get("dictRemotes") or {}
    dictConfig = dictRemotes.get(sService)
    if not dictConfig:
        raise ReverifyConfigError(
            f"Remote not configured: configure {sService} in vaibify.yml"
        )
    return dictConfig


def _fdictFetchHashesForService(
    sService, dictConfig, listRelPaths, filesRepo,
):
    """Dispatch to the right mirror module for one service."""
    if sService == "github":
        return _fdictFetchGithubHashes(dictConfig, listRelPaths)
    if sService == "overleaf":
        return _fdictFetchOverleafHashes(
            dictConfig, listRelPaths, filesRepo,
        )
    if sService == "arxiv":
        return _fdictFetchArxivHashes(
            dictConfig, listRelPaths, filesRepo,
        )
    return _fdictFetchZenodoHashes(dictConfig, listRelPaths)


def fsArxivCacheDir(filesRepo):
    """Return the host directory caching downloaded arXiv tarballs.

    The arXiv tarball cache is genuinely host network + disk work, so
    it must live on the host even when the project repo lives in a
    container. A host-rooted adapter keeps the historical
    ``<repo>/.vaibify/arxivCache`` location; a container-rooted
    adapter maps to a per-repo directory under ``~/.vaibify`` keyed by
    a hash of the container root path.
    """
    filesRepo = ffilesEnsureRepoFiles(filesRepo)
    sLocalRoot = filesRepo.fsLocalRootOrNone()
    if sLocalRoot is not None:
        return os.path.join(
            sLocalRoot, S_VAIBIFY_DIRECTORY, S_ARXIV_CACHE_DIRECTORY,
        )
    sRootKey = hashlib.sha256(
        (filesRepo.sRootPath or "").encode("utf-8"),
    ).hexdigest()[:16]
    return os.path.join(
        os.path.expanduser("~"), S_VAIBIFY_DIRECTORY,
        S_ARXIV_CACHE_DIRECTORY, sRootKey,
    )


def _fdictFetchArxivHashes(dictConfig, listRelPaths, filesRepo):
    """Fetch arXiv hashes; require sArxivId."""
    sArxivId = dictConfig.get("sArxivId") or ""
    if not sArxivId:
        raise ReverifyConfigError(
            "Remote not configured: configure arxiv in vaibify.yml"
        )
    dictPathMap = dictConfig.get("dictPathMap") or None
    return arxivClient.fdictFetchRemoteHashes(
        sArxivId, listRelPaths,
        dictPathMap=dictPathMap, sCacheDir=fsArxivCacheDir(filesRepo),
    )


def _fdictFetchGithubHashes(dictConfig, listRelPaths):
    """Fetch GitHub hashes; require sOwner, sRepo, sBranch."""
    sOwner = dictConfig.get("sOwner") or ""
    sRepo = dictConfig.get("sRepo") or ""
    sBranch = dictConfig.get("sBranch") or "main"
    if not sOwner or not sRepo:
        raise ReverifyConfigError(
            "Remote not configured: configure github in vaibify.yml"
        )
    return githubMirror.fdictFetchRemoteHashes(
        sOwner, sRepo, sBranch, listRelPaths,
    )


def _fdictFetchOverleafHashes(dictConfig, listRelPaths, filesRepo):
    """Fetch Overleaf hashes at the pushed remote paths, keyed by local path.

    The push flattens each figure into ``<target-directory>/<basename>``
    inside the Overleaf project, so the project clone must be hashed
    at the remote paths the push manifest recorded — a lookup at the
    local repo-relative path can never hit. The result is re-keyed to
    local paths so the divergence comparator lines up with the
    manifest's expected hashes.
    """
    sProjectId = dictConfig.get("sProjectId") or ""
    if not sProjectId:
        raise ReverifyConfigError(
            "Remote not configured: configure overleaf in vaibify.yml"
        )
    dictRemoteByLocal = overleafSync.fdictOverleafRemotePathsAt(
        filesRepo, dictConfig.get("sLastPushCommit") or "",
    )
    listRemotePaths = [
        dictRemoteByLocal.get(sLocal) or sLocal
        for sLocal in listRelPaths
    ]
    dictRemoteHashes = overleafMirror.fdictFetchRemoteHashes(
        sProjectId, listRemotePaths,
    )
    return {
        sLocal: dictRemoteHashes.get(
            dictRemoteByLocal.get(sLocal) or sLocal,
        )
        for sLocal in listRelPaths
    }


def _fdictFetchZenodoHashes(dictConfig, listRelPaths):
    """Fetch Zenodo hashes; require sRecordId."""
    sRecordId = dictConfig.get("sRecordId") or ""
    if not sRecordId:
        raise ReverifyConfigError(
            "Remote not configured: configure zenodo in vaibify.yml"
        )
    sService = dictConfig.get("sService") or "sandbox"
    return zenodoClient.fdictFetchRemoteHashes(
        sRecordId, listRelPaths=listRelPaths, sService=sService,
    )


def fdictComputeLiveExpectedHashes(filesRepo, dictWorkflow):
    """Hash the workflow's declared canonical files as they exist now.

    The expected side of every L2 remote comparison. Computing it
    from the working tree at verify time means no artifact can be
    stale between the researcher and a verification — the claim and
    the evidence are the same bytes. Declared paths that do not exist
    locally are excluded (their remediation surface is L1's
    outputs-missing criterion); an entirely empty result raises
    :class:`ReverifyConfigError` so a verify can never record a
    vacuous "0 of 0 matching".
    """
    filesRepo = ffilesEnsureRepoFiles(filesRepo)
    listPaths = manifestWriter.flistCollectCanonicalRepoPaths(
        dictWorkflow,
    )
    dictEntries = filesRepo.fdictHashFiles(listPaths)
    dictExpected = {
        sPath: dictEntry.get("sSha256")
        for sPath, dictEntry in dictEntries.items()
        if isinstance(dictEntry, dict) and dictEntry.get("sSha256")
    }
    if not dictExpected:
        raise ReverifyConfigError(
            "No declared workflow outputs exist locally to compare "
            "against the remote — run the workflow first"
        )
    return dictExpected


_T_PUSHED_FIGURE_SCOPED_SERVICES = ("overleaf", "arxiv")


def _fdictNarrowExpectedToPushedFigures(
    dictExpected, dictWorkflow, filesRepo, sService,
):
    """Restrict expected hashes to the Overleaf-pushed figure list.

    A manuscript remote (the Overleaf project or an arXiv e-print)
    carries only the pushed figures, so comparing the full manifest
    would report every data file and script as diverged. The Overleaf
    push manifest is the same authority the L2 gate uses
    (``levelGates._fbArxivTarballMatchesPushManifest``). Raises
    :class:`ReverifyConfigError` when no push is recorded — without a
    pushed-figure list there is no honest comparison set, and a
    vacuous "0 of 0 matching" must not render as synced.
    """
    dictRemotes = dictWorkflow.get("dictRemotes") or {}
    dictOverleaf = dictRemotes.get("overleaf") or {}
    sCommit = dictOverleaf.get("sLastPushCommit") or ""
    listPushed = overleafSync.flistOverleafPushedFiguresAt(
        filesRepo, sCommit,
    )
    if not listPushed:
        raise ReverifyConfigError(
            "No Overleaf-pushed figures recorded — push manuscript "
            f"figures to Overleaf before verifying against {sService}"
        )
    setPushed = set(listPushed)
    dictNarrowed = {
        sPath: sHash for sPath, sHash in dictExpected.items()
        if sPath in setPushed
    }
    if not dictNarrowed:
        raise ReverifyConfigError(
            f"None of the {len(listPushed)} pushed figures are "
            "among the workflow's declared outputs on disk — check "
            "the step's saPlotFiles declarations. A verify with no "
            'expected hashes would record a vacuous "0 of 0 '
            'matching".'
        )
    return dictNarrowed


def _flistBuildDivergenceList(dictExpected, dictActual):
    """Return ``[{sPath, sExpected, sActual}, ...]`` for mismatches."""
    listDiverged = []
    for sRelativePath, sExpectedHash in sorted(dictExpected.items()):
        sActualHash = dictActual.get(sRelativePath)
        if sActualHash != sExpectedHash:
            listDiverged.append({
                "sPath": sRelativePath,
                "sExpected": sExpectedHash,
                "sActual": sActualHash,
            })
    return listDiverged


def _fsBuildIsoTimestamp():
    """Return the current UTC time formatted as ISO-8601 with Z suffix."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def fdictVerifyRemoteService(
    filesRepo, dictWorkflow, sService, sNowIso=None,
):
    """Compare manifest hashes to the remote and return a status dict.

    The returned dict matches the schema persisted to syncStatus.json
    and returned by the verify route. Raises
    :class:`ReverifyConfigError` if the workflow lacks remote
    configuration for ``sService`` or no comparable local files
    exist. The expected side is computed by hashing the workflow's
    declared canonical files AS THEY EXIST NOW — the L2 claim is
    "the files I have right now match the published copies", so it
    never reads ``MANIFEST.sha256``, which is the L3 envelope
    artifact and may legitimately lag the working tree. For the
    manuscript services (``overleaf`` and ``arxiv``) the comparison
    set narrows to the Overleaf-pushed figures — the only files a
    manuscript remote can carry.

    Phase 2 extension: captures the per-service identifier the
    verification actually ran against (``sCommittedShaVerified`` for
    GitHub, ``sZenodoDoi`` + ``sEndpointVerified`` for Zenodo) so the
    L2 readiness check can detect when the live workflow state has
    drifted away from the last successful verification.
    """
    filesRepo = ffilesEnsureRepoFiles(filesRepo)
    dictConfig = _fdictRequireServiceConfig(dictWorkflow, sService)
    dictExpected = fdictComputeLiveExpectedHashes(
        filesRepo, dictWorkflow,
    )
    if sService in _T_PUSHED_FIGURE_SCOPED_SERVICES:
        dictExpected = _fdictNarrowExpectedToPushedFigures(
            dictExpected, dictWorkflow, filesRepo, sService,
        )
    listRelPaths = sorted(dictExpected.keys())
    dictActual = _fdictFetchHashesForService(
        sService, dictConfig, listRelPaths, filesRepo,
    )
    listDiverged = _flistBuildDivergenceList(dictExpected, dictActual)
    iTotal = len(dictExpected)
    dictStatus = {
        "sService": sService,
        "sLastVerified": sNowIso or _fsBuildIsoTimestamp(),
        "iTotalFiles": iTotal,
        "iMatching": iTotal - len(listDiverged),
        "listDiverged": listDiverged,
    }
    _fnAttachServiceIdentityFields(dictStatus, sService, dictConfig)
    return dictStatus


def _fnAttachServiceIdentityFields(dictStatus, sService, dictConfig):
    """Stamp service-identity fields onto the status dict.

    Splits per-service identifier capture out of
    :func:`fdictVerifyRemoteService` so the 20-line cap holds and the
    per-service branches can grow independently if more identity
    fields are needed later.
    """
    if sService == "github":
        dictStatus["sCommittedShaVerified"] = (
            dictConfig.get("sCommittedSha") or None
        )
    elif sService == "zenodo":
        dictStatus["sZenodoDoi"] = dictConfig.get("sDoi") or None
        dictStatus["sEndpointVerified"] = (
            dictConfig.get("sService") or "sandbox"
        )


def _fsSyncStatusRelativePath():
    """Return the repo-relative path of the workflow's syncStatus.json."""
    return posixpath.join(S_VAIBIFY_DIRECTORY, S_SYNC_STATUS_FILENAME)


def fdictReadCachedSyncStatus(filesRepo, sService):
    """Return the cached status for sService or an empty default.

    Backfills the Phase-2 service-identity fields with ``None`` when a
    pre-Phase-2 file omits them, so callers can ``.get(...)`` against
    a stable shape without separately probing for the upgrade marker.
    """
    dictAll = _fdictReadAllStatuses(filesRepo)
    dictEntry = dictAll.get(sService)
    if not isinstance(dictEntry, dict):
        return _fdictEmptyServiceStatus(sService)
    _fnBackfillServiceIdentityFields(dictEntry, sService)
    return dictEntry


def _fnBackfillServiceIdentityFields(dictEntry, sService):
    """Ensure the Phase-2 identity fields exist on a cached entry."""
    if sService == "github":
        dictEntry.setdefault("sCommittedShaVerified", None)
    elif sService == "zenodo":
        dictEntry.setdefault("sZenodoDoi", None)
        dictEntry.setdefault("sEndpointVerified", None)


def _fdictEmptyServiceStatus(sService):
    """Return the default status for a service that has never verified.

    Service-identity fields (``sCommittedShaVerified``, ``sZenodoDoi``,
    ``sEndpointVerified``) default to ``None`` so callers reading a
    pre-Phase-2 cache file or a never-verified service see the same
    shape they would after a real verify ran without identity capture.
    """
    dictEmpty = {
        "sService": sService,
        "sLastVerified": None,
        "iTotalFiles": 0,
        "iMatching": 0,
        "listDiverged": [],
    }
    if sService == "github":
        dictEmpty["sCommittedShaVerified"] = None
    elif sService == "zenodo":
        dictEmpty["sZenodoDoi"] = None
        dictEmpty["sEndpointVerified"] = None
    return dictEmpty


def fnWriteSyncStatus(filesRepo, dictStatus):
    """Persist a per-service status entry to syncStatus.json atomically.

    Holds the adapter's write lock across the full read-modify-write
    critical section so concurrent writes for different services
    cannot lose each other's updates. Host adapters use an advisory
    ``fcntl`` lock with a bounded retry budget (raising
    ``RuntimeError`` on exhaustion); container adapters use a
    process-local lock keyed by container + path, which is honest
    because every container-path writer (verify route, scheduled
    loop, UI button) lives in the single FastAPI process.
    """
    filesRepo = ffilesEnsureRepoFiles(filesRepo)
    sService = dictStatus["sService"]
    sRelPath = _fsSyncStatusRelativePath()
    with filesRepo.fnWithLock(sRelPath):
        dictAll = _fdictReadAllStatuses(filesRepo)
        dictAll[sService] = dictStatus
        filesRepo.fnWriteJsonAtomic(sRelPath, dictAll)


def fnDeleteSyncStatus(filesRepo, sService):
    """Remove one service's entry from syncStatus.json atomically.

    Called when a remote connection is removed from the workflow so
    the dashboard cannot keep rendering a ghost verify result for a
    connection that no longer exists. Holds the same write lock as
    :func:`fnWriteSyncStatus` so a concurrent verify for another
    service cannot be lost. A missing file or absent entry is a no-op.
    """
    filesRepo = ffilesEnsureRepoFiles(filesRepo)
    sRelPath = _fsSyncStatusRelativePath()
    with filesRepo.fnWithLock(sRelPath):
        dictAll = _fdictReadAllStatuses(filesRepo)
        if sService not in dictAll:
            return
        del dictAll[sService]
        filesRepo.fnWriteJsonAtomic(sRelPath, dictAll)


def _fdictReadAllStatuses(filesRepo):
    """Return the full syncStatus.json dict, or an empty dict on error."""
    filesRepo = ffilesEnsureRepoFiles(filesRepo)
    sRelPath = _fsSyncStatusRelativePath()
    if not filesRepo.fbIsFile(sRelPath):
        return {}
    try:
        dictAll = json.loads(filesRepo.fsReadText(sRelPath))
    except (OSError, ValueError):
        return {}
    return dictAll if isinstance(dictAll, dict) else {}


def fdictRunReverifyForWorkflow(dictWorkflow, sNowIso=None, filesRepo=None):
    """Verify every configured remote for one workflow; never raises.

    Returns a list-of-dicts result block ``{sWorkflowId, listResults}``
    suitable for inclusion in the scheduled-loop summary. Each service
    failure is captured as an entry with ``sStatus='error'`` so a
    single bad remote does not abort the workflow's other services.
    ``filesRepo`` defaults to a host adapter rooted at the workflow's
    ``sProjectRepoPath``; the GUI scheduler passes the container
    adapter for that workflow instead.
    """
    sWorkflowId = dictWorkflow.get("sWorkflowId") or ""
    if filesRepo is None:
        filesRepo = dictWorkflow.get("sProjectRepoPath") or ""
    filesRepo = ffilesEnsureRepoFiles(filesRepo)
    dictRemotes = dictWorkflow.get("dictRemotes") or {}
    listResults = []
    for sService in LIST_SUPPORTED_SERVICES:
        if sService not in dictRemotes:
            continue
        dictResult = _fdictAttemptOneVerify(
            filesRepo, dictWorkflow, sService, sNowIso,
        )
        dictResult["sWorkflowId"] = sWorkflowId
        listResults.append(dictResult)
    return {"sWorkflowId": sWorkflowId, "listResults": listResults}


def _fdictAttemptOneVerify(
    filesRepo, dictWorkflow, sService, sNowIso,
):
    """Run one verify; return ok/error dict; never raises."""
    try:
        dictStatus = fdictVerifyRemoteService(
            filesRepo, dictWorkflow, sService, sNowIso=sNowIso,
        )
    except Exception as errorAny:
        return {
            "sService": sService,
            "sStatus": "error",
            "sError": _fsRedactError(str(errorAny)),
        }
    try:
        fnWriteSyncStatus(filesRepo, dictStatus)
    except OSError as errorOs:
        return {
            "sService": sService,
            "sStatus": "error",
            "sError": _fsRedactError(str(errorOs)),
        }
    return {"sService": sService, "sStatus": "ok"}


def _fsRedactError(sMessage):
    """Apply both mirror-modules' redaction filters to an error string."""
    sRedacted = githubMirror.fsRedactStderr(sMessage or "")
    return overleafMirror.fsRedactStderr(sRedacted)


def fnRunReverifyOnce(dictCtx, listWorkflows, sNowIso=None):
    """Run one scheduler iteration; return the aggregated report.

    ``listWorkflows`` entries are either bare workflow dicts (legacy
    callers and tests — verified through the host fallback) or
    ``(sContainerId, dictWorkflow)`` tuples from
    ``_flistEnumerateWorkflows``, in which case the context's
    ``files`` callable supplies the container adapter so the verify
    reads the manifest where it actually lives.
    """
    sIso = sNowIso or _fsBuildIsoTimestamp()
    listResults = []
    for entryWorkflow in listWorkflows:
        dictWorkflow, filesRepo = _ftResolveWorkflowEntry(
            dictCtx, entryWorkflow,
        )
        dictWorkflowReport = fdictRunReverifyForWorkflow(
            dictWorkflow, sNowIso=sIso, filesRepo=filesRepo,
        )
        for dictResult in dictWorkflowReport["listResults"]:
            listResults.append(dictResult)
    return {"sNowIso": sIso, "listResults": listResults}


def _ftResolveWorkflowEntry(dictCtx, entryWorkflow):
    """Return ``(dictWorkflow, filesRepo_or_None)`` for one loop entry."""
    if isinstance(entryWorkflow, dict):
        return entryWorkflow, None
    sContainerId, dictWorkflow = entryWorkflow
    fnFiles = dictCtx.get("files") if dictCtx else None
    if fnFiles is None:
        return dictWorkflow, None
    return dictWorkflow, fnFiles(sContainerId)


def _flistEnumerateWorkflows(dictCtx):
    """Pull ``(sContainerId, dictWorkflow)`` pairs from the route context."""
    dictWorkflows = dictCtx.get("workflows") or {}
    return list(dictWorkflows.items())


async def _fnReverifyLoop(dictCtx, fHoursCadence):
    """Forever loop: sleep first, then run one verify pass.

    The verify pass itself is synchronous and performs blocking network
    I/O via ``requests``. It is dispatched through ``asyncio.to_thread``
    so the FastAPI event loop continues serving HTTP requests while the
    reverify pass runs.
    """
    fSeconds = max(float(fHoursCadence), 0.0) * 3600.0
    while True:
        try:
            await asyncio.sleep(fSeconds)
        except asyncio.CancelledError:
            return
        listWorkflows = _flistEnumerateWorkflows(dictCtx)
        try:
            await asyncio.to_thread(
                fnRunReverifyOnce, dictCtx, listWorkflows,
            )
        except Exception:
            continue


def fnScheduleReverify(app, dictCtx, fHoursCadence=_F_DEFAULT_CADENCE_HOURS):
    """Register a recurring re-verify task on the FastAPI lifespan.

    Cadence default is 6 hours. The first iteration runs ``fHoursCadence``
    after startup, never immediately, so a server restart cannot trigger
    a fresh round of network calls every reload.

    Per-workflow cadence override (``fReverifyHoursCadence``) is
    reserved for a future commit; currently the global cadence applies
    to every workflow.

    Hooks are appended to the app's lifespan startup/shutdown lists
    (the modern FastAPI pattern); the deprecated ``@app.on_event``
    decorator is no longer used.
    """

    async def fnStartReverifyTask(app):
        taskReverify = asyncio.create_task(
            _fnReverifyLoop(dictCtx, fHoursCadence)
        )
        app.state.taskScheduledReverify = taskReverify

    async def fnStopReverifyTask(app):
        taskReverify = getattr(
            app.state, "taskScheduledReverify", None,
        )
        if taskReverify is None:
            return
        taskReverify.cancel()
        try:
            await taskReverify
        except (asyncio.CancelledError, Exception):
            pass

    app.state.listLifespanStartup.append(fnStartReverifyTask)
    app.state.listLifespanShutdown.append(fnStopReverifyTask)
