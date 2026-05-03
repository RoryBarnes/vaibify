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
import json
import os
from datetime import datetime, timezone
from pathlib import Path

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


class ReverifyConfigError(ValueError):
    """Raised when a workflow has missing/invalid remote configuration."""


def fdictLoadManifestExpectedHashes(sProjectRepo):
    """Return ``{relpath: sha256_hex}`` parsed from MANIFEST.sha256.

    Raises ``FileNotFoundError`` when the manifest does not exist so
    callers can map the failure to a 409 response.
    """
    pathManifest = Path(sProjectRepo) / S_MANIFEST_FILENAME
    if not pathManifest.is_file():
        raise FileNotFoundError(
            f"manifest not found: '{pathManifest}'"
        )
    dictExpected = {}
    with open(pathManifest, "r", encoding="utf-8") as fileHandle:
        for sLine in fileHandle:
            tParsed = _tParseManifestLine(sLine)
            if tParsed is not None:
                sHash, sRelativePath = tParsed
                dictExpected[sRelativePath] = sHash
    return dictExpected


def _tParseManifestLine(sLine):
    """Return ``(hash, relpath)`` or ``None`` for blank/comment lines."""
    sStripped = sLine.rstrip("\n")
    if not sStripped or sStripped.startswith("#"):
        return None
    sSeparator = "  "
    iSplit = sStripped.find(sSeparator)
    if iSplit < 0:
        return None
    return (sStripped[:iSplit], sStripped[iSplit + len(sSeparator):])


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
    """Persist a per-service status entry to syncStatus.json atomically."""
    sService = dictStatus["sService"]
    sPath = _fsSyncStatusPath(sProjectRepo)
    os.makedirs(os.path.dirname(sPath), exist_ok=True)
    dictAll = _fdictReadAllStatuses(sPath)
    dictAll[sService] = dictStatus
    sTempPath = sPath + ".tmp"
    with open(sTempPath, "w", encoding="utf-8") as fileHandle:
        json.dump(dictAll, fileHandle, indent=2, sort_keys=True)
    os.replace(sTempPath, sPath)


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


def _ffResolveCadenceHours(dictWorkflow, fHoursDefault):
    """Return the per-workflow cadence override or the default."""
    fOverride = dictWorkflow.get("fReverifyHoursCadence")
    if isinstance(fOverride, (int, float)) and fOverride > 0:
        return float(fOverride)
    return float(fHoursDefault)


def _flistEnumerateWorkflows(dictCtx):
    """Pull every loaded workflow from the route context."""
    dictWorkflows = dictCtx.get("workflows") or {}
    return list(dictWorkflows.values())


async def _fnReverifyLoop(dictCtx, fHoursCadence):
    """Forever loop: sleep first, then run one verify pass."""
    fSeconds = max(float(fHoursCadence), 0.0) * 3600.0
    while True:
        try:
            await asyncio.sleep(fSeconds)
        except asyncio.CancelledError:
            return
        listWorkflows = _flistEnumerateWorkflows(dictCtx)
        try:
            fnRunReverifyOnce(dictCtx, listWorkflows)
        except Exception:
            continue


def fnScheduleReverify(app, dictCtx, fHoursCadence=_F_DEFAULT_CADENCE_HOURS):
    """Register a recurring re-verify task on the FastAPI lifespan.

    Cadence default is 6 hours. The first iteration runs ``fHoursCadence``
    after startup, never immediately, so a server restart cannot trigger
    a fresh round of network calls every reload. Per-workflow overrides
    are read by :func:`fdictRunReverifyForWorkflow` from
    ``dictWorkflow.get('fReverifyHoursCadence', fHoursCadence)`` —
    individual workflows can opt into a faster cadence than the global
    default.
    """

    @app.on_event("startup")
    async def fnStartReverifyTask():
        taskReverify = asyncio.create_task(
            _fnReverifyLoop(dictCtx, fHoursCadence)
        )
        app.state.taskScheduledReverify = taskReverify

    @app.on_event("shutdown")
    async def fnStopReverifyTask():
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
