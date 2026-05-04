"""Periodic re-verification of workflow manifests against remote mirrors.

This module is the single source of truth for the AICS Level 3
"authoritative re-verify" loop. The HTTP routes
``POST /api/sync/{sId}/{sService}/verify`` and the FastAPI lifespan
scheduler both delegate here so the verify logic is described once and
covered by one set of tests.

Verification compares the SHA-256 hash of every file recorded in
``<sProjectRepo>/MANIFEST.sha256`` against the hash of the same path
served by the remote mirror (GitHub, Overleaf, Zenodo). The result is
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
import fcntl
import json
import os
import time
from datetime import datetime, timezone

from vaibify.reproducibility import (
    githubMirror,
    manifestWriter,
    overleafMirror,
    zenodoClient,
)


__all__ = [
    "S_SYNC_STATUS_FILENAME",
    "S_MANIFEST_FILENAME",
    "ReverifyConfigError",
    "fdictLoadManifestExpectedHashes",
    "fdictVerifyRemoteService",
    "fdictReadCachedSyncStatus",
    "fnWriteSyncStatus",
    "fdictRunReverifyForWorkflow",
    "fnRunReverifyOnce",
    "fnScheduleReverify",
]


S_MANIFEST_FILENAME = "MANIFEST.sha256"
S_SYNC_STATUS_FILENAME = "syncStatus.json"
S_VAIBIFY_DIRECTORY = ".vaibify"
LIST_SUPPORTED_SERVICES = ("github", "overleaf", "zenodo")
_F_DEFAULT_CADENCE_HOURS = 6.0
_I_LOCK_RETRY_MAX = 30
_F_LOCK_RETRY_SLEEP = 0.05


class ReverifyConfigError(ValueError):
    """Raised when a workflow has missing/invalid remote configuration."""


def fdictLoadManifestExpectedHashes(sProjectRepo):
    """Return ``{relpath: sha256_hex}`` parsed from MANIFEST.sha256.

    Delegates to :func:`manifestWriter.flistParseManifestLines` so all
    callers share one strict parser (including GNU-escape handling for
    paths with embedded newlines or backslashes). Raises
    ``FileNotFoundError`` when the manifest does not exist so callers
    can map the failure to a 409 response. Re-raises ``ValueError``
    from the parser so corrupt manifests surface as 5xx rather than
    being silently treated as empty.
    """
    listEntries = manifestWriter.flistParseManifestLines(sProjectRepo)
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


def _fdictFetchHashesForService(sService, dictConfig, listRelPaths):
    """Dispatch to the right mirror module for one service."""
    if sService == "github":
        return _fdictFetchGithubHashes(dictConfig, listRelPaths)
    if sService == "overleaf":
        return _fdictFetchOverleafHashes(dictConfig, listRelPaths)
    return _fdictFetchZenodoHashes(dictConfig, listRelPaths)


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


def _fdictFetchOverleafHashes(dictConfig, listRelPaths):
    """Fetch Overleaf hashes; require sProjectId."""
    sProjectId = dictConfig.get("sProjectId") or ""
    if not sProjectId:
        raise ReverifyConfigError(
            "Remote not configured: configure overleaf in vaibify.yml"
        )
    return overleafMirror.fdictFetchRemoteHashes(sProjectId, listRelPaths)


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
    sProjectRepo, dictWorkflow, sService, sNowIso=None,
):
    """Compare manifest hashes to the remote and return a status dict.

    The returned dict matches the schema persisted to syncStatus.json
    and returned by the verify route. Raises ``FileNotFoundError`` if
    the manifest is missing and :class:`ReverifyConfigError` if the
    workflow lacks remote configuration for ``sService``.
    """
    dictExpected = fdictLoadManifestExpectedHashes(sProjectRepo)
    dictConfig = _fdictRequireServiceConfig(dictWorkflow, sService)
    listRelPaths = sorted(dictExpected.keys())
    dictActual = _fdictFetchHashesForService(
        sService, dictConfig, listRelPaths,
    )
    listDiverged = _flistBuildDivergenceList(dictExpected, dictActual)
    iTotal = len(dictExpected)
    return {
        "sService": sService,
        "sLastVerified": sNowIso or _fsBuildIsoTimestamp(),
        "iTotalFiles": iTotal,
        "iMatching": iTotal - len(listDiverged),
        "listDiverged": listDiverged,
    }


def _fsSyncStatusPath(sProjectRepo):
    """Return the absolute path of the workflow's syncStatus.json."""
    return os.path.join(
        sProjectRepo, S_VAIBIFY_DIRECTORY, S_SYNC_STATUS_FILENAME,
    )


def fdictReadCachedSyncStatus(sProjectRepo, sService):
    """Return the cached status for sService or an empty default."""
    sPath = _fsSyncStatusPath(sProjectRepo)
    if not os.path.isfile(sPath):
        return _fdictEmptyServiceStatus(sService)
    try:
        with open(sPath, "r", encoding="utf-8") as fileHandle:
            dictAll = json.load(fileHandle)
    except (OSError, ValueError):
        return _fdictEmptyServiceStatus(sService)
    dictEntry = dictAll.get(sService)
    if not isinstance(dictEntry, dict):
        return _fdictEmptyServiceStatus(sService)
    return dictEntry


def _fdictEmptyServiceStatus(sService):
    """Return the default status for a service that has never verified."""
    return {
        "sService": sService,
        "sLastVerified": None,
        "iTotalFiles": 0,
        "iMatching": 0,
        "listDiverged": [],
    }


def fnWriteSyncStatus(sProjectRepo, dictStatus):
    """Persist a per-service status entry to syncStatus.json atomically.

    Holds an advisory ``fcntl`` lock on a sibling lock file across the
    full read-modify-write critical section so concurrent writes for
    different services cannot lose each other's updates. The lock is
    acquired non-blocking with a short bounded retry loop to avoid
    deadlocking on a stale lock holder; on retry exhaustion a
    ``RuntimeError`` is raised so the caller surfaces the problem
    rather than silently overwriting a peer's entry.
    """
    sService = dictStatus["sService"]
    sPath = _fsSyncStatusPath(sProjectRepo)
    os.makedirs(os.path.dirname(sPath), exist_ok=True)
    sLockPath = sPath + ".lock"
    with _fnAcquireSyncStatusLock(sLockPath):
        _fnPersistSyncStatusEntry(sPath, sService, dictStatus)


def _fnPersistSyncStatusEntry(sPath, sService, dictStatus):
    """Read, mutate, and atomically rewrite the syncStatus.json file."""
    dictAll = _fdictReadAllStatuses(sPath)
    dictAll[sService] = dictStatus
    sTempPath = sPath + ".tmp"
    with open(sTempPath, "w", encoding="utf-8") as fileHandle:
        json.dump(dictAll, fileHandle, indent=2, sort_keys=True)
    os.replace(sTempPath, sPath)


class _SyncStatusLockHolder:
    """Context manager wrapping the open file descriptor + flock release."""

    def __init__(self, iFileDescriptor):
        self.iFileDescriptor = iFileDescriptor

    def __enter__(self):
        return self

    def __exit__(self, classExc, valueExc, traceback):
        try:
            fcntl.flock(self.iFileDescriptor, fcntl.LOCK_UN)
        finally:
            os.close(self.iFileDescriptor)


def _fnAcquireSyncStatusLock(sLockPath):
    """Return a context manager holding an exclusive flock on sLockPath.

    Retries up to ``_I_LOCK_RETRY_MAX`` times with ``_F_LOCK_RETRY_SLEEP``
    second sleeps in between (≈ 1.5 s budget). The previous 250 ms
    budget was insufficient when three legitimate writers contend for
    the lock simultaneously: the verify route, the scheduled loop, and
    a manual UI button can all fire within a single user interaction
    and a JSON read-modify-write of a many-service status file can
    plausibly take 100 ms each on a busy host. Closes the file
    descriptor in every exit path so a stale ``open`` does not leak
    when retries are exhausted.
    """
    iFileDescriptor = os.open(
        sLockPath, os.O_WRONLY | os.O_CREAT, 0o600,
    )
    for _iAttempt in range(_I_LOCK_RETRY_MAX):
        try:
            fcntl.flock(
                iFileDescriptor, fcntl.LOCK_EX | fcntl.LOCK_NB,
            )
            return _SyncStatusLockHolder(iFileDescriptor)
        except BlockingIOError:
            time.sleep(_F_LOCK_RETRY_SLEEP)
    os.close(iFileDescriptor)
    raise RuntimeError(
        f"could not acquire syncStatus lock at '{sLockPath}' "
        f"after {_I_LOCK_RETRY_MAX} attempts"
    )


def _fdictReadAllStatuses(sPath):
    """Return the full syncStatus.json dict, or an empty dict on error."""
    if not os.path.isfile(sPath):
        return {}
    try:
        with open(sPath, "r", encoding="utf-8") as fileHandle:
            dictAll = json.load(fileHandle)
    except (OSError, ValueError):
        return {}
    return dictAll if isinstance(dictAll, dict) else {}


def fdictRunReverifyForWorkflow(dictWorkflow, sNowIso=None):
    """Verify every configured remote for one workflow; never raises.

    Returns a list-of-dicts result block ``{sWorkflowId, listResults}``
    suitable for inclusion in the scheduled-loop summary. Each service
    failure is captured as an entry with ``sStatus='error'`` so a
    single bad remote does not abort the workflow's other services.
    """
    sWorkflowId = dictWorkflow.get("sWorkflowId") or ""
    sProjectRepo = dictWorkflow.get("sProjectRepoPath") or ""
    dictRemotes = dictWorkflow.get("dictRemotes") or {}
    listResults = []
    for sService in LIST_SUPPORTED_SERVICES:
        if sService not in dictRemotes:
            continue
        dictResult = _fdictAttemptOneVerify(
            sProjectRepo, dictWorkflow, sService, sNowIso,
        )
        dictResult["sWorkflowId"] = sWorkflowId
        listResults.append(dictResult)
    return {"sWorkflowId": sWorkflowId, "listResults": listResults}


def _fdictAttemptOneVerify(
    sProjectRepo, dictWorkflow, sService, sNowIso,
):
    """Run one verify; return ok/error dict; never raises."""
    try:
        dictStatus = fdictVerifyRemoteService(
            sProjectRepo, dictWorkflow, sService, sNowIso=sNowIso,
        )
    except Exception as errorAny:
        return {
            "sService": sService,
            "sStatus": "error",
            "sError": _fsRedactError(str(errorAny)),
        }
    try:
        fnWriteSyncStatus(sProjectRepo, dictStatus)
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

    ``dictCtx`` is accepted so the function signature matches the
    scheduled task. The current implementation does not consult the
    context — every input the worker needs lives on the workflow
    dicts — but accepting it keeps callsites uniform with other
    long-running tasks in the codebase.
    """
    sIso = sNowIso or _fsBuildIsoTimestamp()
    listResults = []
    for dictWorkflow in listWorkflows:
        dictWorkflowReport = fdictRunReverifyForWorkflow(
            dictWorkflow, sNowIso=sIso,
        )
        for dictResult in dictWorkflowReport["listResults"]:
            listResults.append(dictResult)
    return {"sNowIso": sIso, "listResults": listResults}


def _flistEnumerateWorkflows(dictCtx):
    """Pull every loaded workflow from the route context."""
    dictWorkflows = dictCtx.get("workflows") or {}
    return list(dictWorkflows.values())


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
