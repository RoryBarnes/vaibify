"""Scan each step's scripts for reads of other steps' declared outputs.

The scan is READ-ONLY: it fetches scripts (a typed read), finds load
calls, and matches each loaded file against the output declarations of
the steps before it. Its results are hub memory, filed by step id under
the workflow file they came from, and ride on each step as the
transient ``dictDependencyScan`` annotation (``dependencyFindings``
reads them for Level 1). Nothing is written into the project.

Each finding carries what it was found against -- the script's bytes
(SHA-256) and the upstream output declaration it matched -- and each
scan entry records every script it read (an absent one as "") and a
fingerprint of what it matched them against, so a later reader can tell
a scan, a clean one included, that still describes the files from one
that no longer does. A step whose scan raised is recorded FAILED with the
reason, which clears its findings: a failed scan says nothing about the
step, and keeping the old findings would let old data pass or fail it.
"""

import hashlib
import logging
import posixpath

from vaibify.reproducibility.dependencyFindings import (
    S_INPUTS_FINGERPRINT_KEY,
    S_SCAN_ANNOTATION_KEY,
    S_SCRIPT_HASHES_KEY,
    S_STATE_FAILED,
    S_STATE_SCANNED,
    fsScanInputsFingerprint,
)
from vaibify.reproducibility.manifestPaths import fsResolveStepPathToRepoPath


__all__ = [
    "fdictScanWorkflowDependencies",
    "fnApplyScanToWorkflow",
    "fnStoreScanResults",
    "fdictCachedScanByStepId",
    "fdictDeriveIndexDependencies",
    "fnReapplyCachedScan",
    "fdictFailEveryStep",
]

logger = logging.getLogger("vaibify")

I_ERROR_TEXT_MAX_CHARACTERS = 300


def _fsWorkflowCacheKey(dictWorkflow):
    from . import workflowManager
    return (dictWorkflow or {}).get(workflowManager.S_LOADED_FROM_KEY, "")


def _fbaFetchScriptBytes(connectionDocker, sContainerId, sAbsoluteScriptPath):
    """Read one script's bytes through the typed-read primitive."""
    return connectionDocker.fbaFetchFile(sContainerId, sAbsoluteScriptPath)


async def _ftReadScript(dictCtx, sContainerId, sAbsoluteScriptPath):
    """Return (text, sha256) of a script, or (None, '') when absent.

    Any other read failure propagates, so the step is recorded FAILED
    rather than scanned clean.
    """
    import asyncio
    try:
        baContent = await asyncio.to_thread(
            _fbaFetchScriptBytes, dictCtx["docker"], sContainerId,
            sAbsoluteScriptPath)
    except FileNotFoundError:
        return None, ""
    return (baContent.decode("utf-8", errors="replace"),
            hashlib.sha256(baContent).hexdigest())


def _fsMatchedOutputDeclaration(dictUpstreamStep, sScannedPath):
    """Return the upstream declaration whose stem the scanned file shares."""
    sStem = posixpath.splitext(posixpath.basename(sScannedPath))[0]
    for sDeclared in (list(dictUpstreamStep.get("saOutputDataFiles", []) or [])
                      + list(dictUpstreamStep.get("saPlotFiles", []) or [])):
        if posixpath.splitext(posixpath.basename(sDeclared))[0] == sStem:
            return sDeclared
    return ""


async def _flistScanOneCommand(dictCtx, sContainerId, dictWorkflow, iStep,
                               sCommand, dictScriptSha256):
    """Return the findings one command's script yields; record its hash."""
    from .commandUtilities import ftExtractScriptPathForLanguage
    from .dependencyScanner import flistScanForLoadCalls
    from .routes.scriptRoutes import (
        _fdictCrossReferenceFiles, _fsAbsoluteStepDirectory,
        _fsJoinStepPath, _fsResolveLanguage,
    )
    dictStep = dictWorkflow["listSteps"][iStep]
    sScript, sLanguage = ftExtractScriptPathForLanguage(sCommand)
    if not sScript:
        return []
    sScriptRepoPath = fsResolveStepPathToRepoPath(
        sScript, dictStep.get("sDirectory", "") or "")
    sText, sSha256 = await _ftReadScript(dictCtx, sContainerId, _fsJoinStepPath(
        _fsAbsoluteStepDirectory(dictStep, dictWorkflow), sScript))
    dictScriptSha256[sScriptRepoPath] = sSha256
    if sText is None:
        return []
    sLanguage = _fsResolveLanguage(sLanguage, sScript, sCommand, sText)
    if sLanguage == "unknown":
        return []
    dictMatches = _fdictCrossReferenceFiles(
        flistScanForLoadCalls(sText, sLanguage), dictWorkflow, iStep)
    return [_fdictBuildFinding(dictWorkflow, dictMatch, sScriptRepoPath,
                               sSha256)
            for dictMatch in dictMatches.get("listSuggestions", [])]


def _fdictBuildFinding(dictWorkflow, dictMatch, sScriptRepoPath, sSha256):
    """Return one finding, keyed by the upstream step's id."""
    dictUpstream = dictWorkflow["listSteps"][dictMatch["iSourceStep"] - 1]
    return {
        "sUpstreamStepId": dictUpstream.get("sStepId", ""),
        "sMatchedOutput": _fsMatchedOutputDeclaration(
            dictUpstream, dictMatch.get("sFileName", "")),
        "sScannedPath": dictMatch.get("sFileName", ""),
        "sScriptPath": sScriptRepoPath,
        "sScriptSha256": sSha256,
        "iLineNumber": dictMatch.get("iLineNumber", 0),
        "sLoadFunction": dictMatch.get("sLoadFunction", ""),
    }


async def _fdictScanOneStep(dictCtx, sContainerId, dictWorkflow, iStep):
    """Return one step's scan entry: its findings, or why the scan failed."""
    from .routes.scriptRoutes import _flistCollectAllCommands
    from vaibify.config.mutationAdmission import fnReRaiseControlPlaneRefusal
    dictStep = dictWorkflow["listSteps"][iStep]
    sInputsFingerprint = fsScanInputsFingerprint(dictWorkflow, dictStep)
    dictScriptSha256 = {}
    try:
        listFindings = []
        for sCommand in _flistCollectAllCommands(dictStep):
            listFindings.extend(await _flistScanOneCommand(
                dictCtx, sContainerId, dictWorkflow, iStep, sCommand,
                dictScriptSha256))
    except Exception as error:
        fnReRaiseControlPlaneRefusal(error)
        return {"sState": S_STATE_FAILED, "listFindings": [],
                "sError": str(error)[:I_ERROR_TEXT_MAX_CHARACTERS]}
    listUsable = [dictFinding for dictFinding in listFindings
                  if dictFinding["sUpstreamStepId"]
                  and dictFinding["sMatchedOutput"]]
    return {"sState": S_STATE_SCANNED, "listFindings": listUsable,
            "sError": "", S_SCRIPT_HASHES_KEY: dictScriptSha256,
            S_INPUTS_FINGERPRINT_KEY: sInputsFingerprint}


async def fdictScanWorkflowDependencies(dictCtx, sContainerId, dictWorkflow):
    """Return ``{sStepId: scan entry}`` for every step that has an id."""
    dictScanByStepId = {}
    for iStep, dictStep in enumerate(dictWorkflow.get("listSteps", []) or []):
        if isinstance(dictStep, dict) and dictStep.get("sStepId"):
            dictScanByStepId[dictStep["sStepId"]] = await _fdictScanOneStep(
                dictCtx, sContainerId, dictWorkflow, iStep)
    return dictScanByStepId


def fdictFailEveryStep(dictWorkflow, sError):
    """Return a scan in which every step failed, for a scan that died."""
    return {dictStep["sStepId"]: {
        "sState": S_STATE_FAILED, "listFindings": [],
        "sError": str(sError)[:I_ERROR_TEXT_MAX_CHARACTERS]}
        for dictStep in dictWorkflow.get("listSteps", []) or []
        if isinstance(dictStep, dict) and dictStep.get("sStepId")}


def fnApplyScanToWorkflow(dictWorkflow, dictScanByStepId):
    """Annotate each step with its scan entry and its scanned upstream ids.

    ``saSourceCodeDeps`` holds step IDS, so reordering steps can never
    redraw an edge onto the wrong step.
    """
    for dictStep in dictWorkflow.get("listSteps", []) or []:
        if not isinstance(dictStep, dict):
            continue
        dictEntry = (dictScanByStepId or {}).get(dictStep.get("sStepId"))
        if dictEntry is None:
            dictStep.pop(S_SCAN_ANNOTATION_KEY, None)
            dictStep.pop("saSourceCodeDeps", None)
            continue
        dictStep[S_SCAN_ANNOTATION_KEY] = dictEntry
        dictStep["saSourceCodeDeps"] = sorted({
            dictFinding["sUpstreamStepId"]
            for dictFinding in dictEntry.get("listFindings", [])})


def fnStoreScanResults(dictCtx, sContainerId, dictWorkflow, dictScanByStepId):
    """File the scan under the workflow it read, and annotate that workflow."""
    dictCtx["sourceCodeDeps"].setdefault(sContainerId, {})[
        _fsWorkflowCacheKey(dictWorkflow)] = dictScanByStepId
    fnApplyScanToWorkflow(dictWorkflow, dictScanByStepId)


def fnReapplyCachedScan(dictCtx, sContainerId, dictWorkflow):
    """Re-annotate a freshly loaded workflow from the last scan of it.

    The annotation is transient, so a reload from disk drops it; without
    this, a reload made every step read "never scanned" until the next
    connect, and the Level 1 dependency check went quiet.
    """
    dictScan = fdictCachedScanByStepId(dictCtx, sContainerId, dictWorkflow)
    if dictScan is not None:
        fnApplyScanToWorkflow(dictWorkflow, dictScan)


def fdictCachedScanByStepId(dictCtx, sContainerId, dictWorkflow):
    """Return the last scan of THIS workflow, or None if not scanned."""
    dictByWorkflow = (dictCtx.get("sourceCodeDeps") or {}).get(
        sContainerId) or {}
    return dictByWorkflow.get(_fsWorkflowCacheKey(dictWorkflow))


def fdictDeriveIndexDependencies(dictWorkflow, dictScanByStepId):
    """Return ``{iUpstream: set(iDownstream)}`` at the CURRENT step order."""
    dictIndexById = {
        dictStep.get("sStepId"): iIndex for iIndex, dictStep in enumerate(
            dictWorkflow.get("listSteps", []) or [])
        if isinstance(dictStep, dict) and dictStep.get("sStepId")}
    dictDeps = {}
    for sStepId, dictEntry in (dictScanByStepId or {}).items():
        if sStepId not in dictIndexById:
            continue
        for dictFinding in dictEntry.get("listFindings", []):
            iUpstream = dictIndexById.get(dictFinding.get("sUpstreamStepId"))
            if iUpstream is not None:
                dictDeps.setdefault(iUpstream, set()).add(
                    dictIndexById[sStepId])
    return dictDeps
