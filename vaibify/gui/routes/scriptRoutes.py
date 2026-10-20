"""Script detection and dependency scanning route handlers."""

__all__ = ["fnRegisterAll", "fdictScanAllDependencies"]

import asyncio
import os
import posixpath

from fastapi import HTTPException, Request
from pydantic import BaseModel

from .. import workflowManager
from ..actionCatalog import ffnAgentAction
from ..pipelineServer import (
    DependencyScanRequest,
    fdictRequireWorkflow,
)
from ..routeContext import fdictCommitWorkflowSave, ffilesForWorkflow
from ..routeScope import (
    S_CARRIER_MODE_A_SYNCHRONOUS,
    S_CARRIER_TYPED_READ,
    ffnDeclareCarrierMode,
)
from ...reproducibility.dependencyFindings import (
    S_DEPENDENCY_NOT_CURRENT,
    S_DISMISSALS_KEY,
    S_SCAN_ANNOTATION_KEY,
)


class DependencyDismissalRequest(BaseModel):
    """Which finding the researcher dismisses: the read, and whose output."""

    sScannedPath: str
    sUpstreamStepId: str


def _fnRegisterScriptRoutes(app, dictCtx):
    """Register script listing and scanning routes."""

    @app.get("/api/sync/{sContainerId}/scripts")
    async def flistGetScripts(sContainerId: str):
        dictCtx["require"](sContainerId)
        dictWorkflow = fdictRequireWorkflow(
            dictCtx["workflows"], sContainerId)
        dictDirMap = workflowManager.fdictBuildStepDirectoryMap(
            dictWorkflow)
        listGroups = []
        for iStep, dictStep in enumerate(
            dictWorkflow.get("listSteps", [])
        ):
            listScripts = (
                workflowManager.flistExtractStepScripts(
                    dictStep)
            )
            if listScripts:
                listGroups.append({
                    "sStepName": dictStep.get("sName", ""),
                    "sCamelCaseDir": dictDirMap.get(iStep, ""),
                    "listScripts": listScripts,
                })
        return listGroups

    @app.post(
        "/api/steps/{sContainerId}/{iStepIndex}/scan-scripts"
    )
    @ffnDeclareCarrierMode(S_CARRIER_TYPED_READ)
    async def fdictScanScripts(
        sContainerId: str, iStepIndex: int
    ):
        dictCtx["require"](sContainerId)
        dictWorkflow = fdictRequireWorkflow(
            dictCtx["workflows"], sContainerId)
        dictStep = dictWorkflow["listSteps"][iStepIndex]
        sDirectory = _fsAbsoluteStepDirectory(
            dictStep, dictWorkflow,
        )
        listFiles = await asyncio.to_thread(
            _flistPythonScriptsInDirectory,
            dictCtx["docker"], sContainerId, sDirectory,
        )
        return workflowManager.fdictAutoDetectScripts(
            listFiles)

    @app.post(
        "/api/steps/{sContainerId}/{iStepIndex}"
        "/scan-dependencies"
    )
    @ffnDeclareCarrierMode(S_CARRIER_TYPED_READ)
    async def fdictHandleScanDependencies(
        sContainerId: str,
        iStepIndex: int,
        request: DependencyScanRequest,
    ):
        dictCtx["require"](sContainerId)
        dictWorkflow = fdictRequireWorkflow(
            dictCtx["workflows"], sContainerId)
        return await _fdictScanDependencies(
            dictCtx, sContainerId, iStepIndex,
            request.saDataCommands, dictWorkflow,
        )


def _flistPythonScriptsInDirectory(
    connectionDocker, sContainerId, sDirectory,
):
    """Return the ``.py`` names directly inside a step directory.

    A TYPED READ, where the panel used to send
    ``find … -printf … || ls …/*.py | xargs -n1 basename`` through the
    general exec primitive. That pipeline was a directory listing
    written as arbitrary command text, so the primitive — which cannot
    tell a listing from a delete — had to treat "scan this step for
    scripts" as a mutation; and it silently listed nothing for any step
    directory containing a space, because the ``ls`` branch was globbed
    by the shell after the quoting was lost.

    A directory that does not exist answers "no scripts", which is the
    answer the panel already showed for that case.
    """
    if not sDirectory:
        return []
    try:
        listEntries = connectionDocker.flistDirectoryEntries(
            sContainerId, sDirectory,
        )
    except FileNotFoundError:
        return []
    return [
        sEntry for sEntry in listEntries if sEntry.endswith(".py")
    ]


async def _fdictScanDependencies(
    dictCtx, sContainerId, iStepIndex,
    saDataCommands, dictWorkflow,
):
    """Scan commands for file loads and cross-reference outputs."""
    listSteps = dictWorkflow.get("listSteps", [])
    dictStep = listSteps[iStepIndex] \
        if iStepIndex < len(listSteps) else {}
    sStepDirectory = _fsAbsoluteStepDirectory(
        dictStep, dictWorkflow,
    )
    listAllDetected = await _flistDetectLoadsInCommands(
        dictCtx, sContainerId, saDataCommands, sStepDirectory,
    )
    dictResult = _fdictCrossReferenceFiles(
        listAllDetected, dictWorkflow, iStepIndex
    )
    if not dictResult.get("listSuggestions") and iStepIndex > 0:
        dictResult["listUpstreamOutputs"] = (
            _flistCollectUpstreamOutputs(
                dictWorkflow, iStepIndex,
            )
        )
    return dictResult


def _flistCollectAllCommands(dictStep):
    """Gather all command fields from a single step."""
    listCommands = []
    for sKey in ("saDataCommands", "saPlotCommands",
                 "saSetupCommands", "saCommands"):
        listCommands.extend(dictStep.get(sKey, []))
    return listCommands


def _fsetExtractUpstreamIndices(dictResult):
    """Return set of 0-based upstream step indices from suggestions."""
    setIndices = set()
    for dictSuggestion in dictResult.get("listSuggestions", []):
        iStepNumber = dictSuggestion.get("iSourceStep", 0)
        if iStepNumber > 0:
            setIndices.add(iStepNumber - 1)
    return setIndices


async def fdictScanAllDependencies(
    dictCtx, sContainerId, dictWorkflow,
):
    """Scan all steps for source-code file dependencies."""
    dictDeps = {}
    listSteps = dictWorkflow.get("listSteps", [])
    for iStep, dictStep in enumerate(listSteps):
        listCommands = _flistCollectAllCommands(dictStep)
        if not listCommands:
            continue
        setUpstream = await _fsetScanOneStep(
            dictCtx, sContainerId, dictWorkflow,
            iStep, listCommands,
        )
        for iUpstream in setUpstream:
            dictDeps.setdefault(iUpstream, set()).add(iStep)
    return dictDeps


async def _fsetScanOneStep(
    dictCtx, sContainerId, dictWorkflow,
    iStep, listCommands,
):
    """Scan one step's commands and return upstream indices."""
    listDetected = await _flistDetectLoadsInCommands(
        dictCtx, sContainerId, listCommands,
        dictWorkflow.get("listSteps", [])[iStep].get(
            "sDirectory", ""),
    )
    dictResult = _fdictCrossReferenceFiles(
        listDetected, dictWorkflow, iStep,
    )
    return _fsetExtractUpstreamIndices(dictResult)


async def _flistDetectLoadsInCommands(
    dictCtx, sContainerId, saDataCommands, sStepDirectory,
):
    """Scan each command's script for file-load calls."""
    listAllDetected = []
    for sCommand in saDataCommands:
        listDetected = await _flistDetectLoadsInOneCommand(
            dictCtx, sContainerId, sCommand, sStepDirectory,
        )
        listAllDetected.extend(listDetected)
    return listAllDetected


async def _flistDetectLoadsInOneCommand(
    dictCtx, sContainerId, sCommand, sStepDirectory,
):
    """Return detected load calls from a single command."""
    from ..commandUtilities import ftExtractScriptPathForLanguage
    from ..dependencyScanner import flistScanForLoadCalls

    sScriptPath, sLanguage = ftExtractScriptPathForLanguage(
        sCommand)
    if not sScriptPath:
        return []
    sAbsScriptPath = _fsJoinStepPath(
        sStepDirectory, sScriptPath)
    sSourceCode = await _fsReadContainerFile(
        dictCtx, sContainerId, sAbsScriptPath,
    )
    if sSourceCode is None:
        return []
    sLanguage = _fsResolveLanguage(
        sLanguage, sScriptPath, sCommand, sSourceCode,
    )
    if sLanguage == "unknown":
        return []
    listDetected = flistScanForLoadCalls(
        sSourceCode, sLanguage)
    for dictItem in listDetected:
        dictItem["sFoundInScript"] = sScriptPath
    return listDetected


def _fsResolveLanguage(
    sLanguage, sScriptPath, sCommand, sSourceCode,
):
    """Detect language from source if not already known."""
    from ..dependencyScanner import fsDetectLanguage
    if sLanguage != "unknown":
        return sLanguage
    sFirstLine = sSourceCode.split("\n", 1)[0]
    return fsDetectLanguage(sScriptPath, sCommand, sFirstLine)


def _flistCollectUpstreamOutputs(dictWorkflow, iStepIndex):
    """Collect saOutputDataFiles entries from steps preceding iStepIndex."""
    listUpstream = []
    listSteps = dictWorkflow.get("listSteps", [])
    for iIndex in range(min(iStepIndex, len(listSteps))):
        dictStep = listSteps[iIndex]
        sStepName = dictStep.get(
            "sName", f"Step {iIndex + 1}")
        iStepNumber = iIndex + 1
        for sFileName in dictStep.get("saOutputDataFiles", []):
            sStem = os.path.splitext(
                os.path.basename(sFileName))[0]
            sStepId = dictStep.get("sStepId")
            sTemplateVariable = (
                "{" + f"step:{sStepId}.{sStem}" + "}" if sStepId
                else "{" + f"Step{iStepNumber:02d}.{sStem}" + "}"
            )
            listUpstream.append({
                "sFileName": sFileName,
                "sSourceStepName": sStepName,
                "iSourceStep": iStepNumber,
                "sTemplateVariable": sTemplateVariable,
            })
    return listUpstream


def _fsAbsoluteStepDirectory(dictStep, dictWorkflow):
    """Return container-absolute path to the step's directory.

    Step ``sDirectory`` values are repo-relative; Docker exec / fetch
    APIs need absolute paths, so join with ``sProjectRepoPath`` at
    this boundary. Idempotent on already-absolute values.
    """
    sDirectory = dictStep.get("sDirectory", "")
    sRepoRoot = dictWorkflow.get("sProjectRepoPath", "")
    if not sDirectory or posixpath.isabs(sDirectory) or not sRepoRoot:
        return sDirectory
    return posixpath.join(sRepoRoot, sDirectory)


def _fsJoinStepPath(sStepDirectory, sScriptPath):
    """Join a step directory with a script path when relative."""
    if os.path.isabs(sScriptPath) or not sStepDirectory:
        return sScriptPath
    return os.path.join(sStepDirectory, sScriptPath)


async def _fsReadContainerFile(
    dictCtx, sContainerId, sFilePath,
):
    """Fetch a file from the container as a string."""
    try:
        baContent = await asyncio.to_thread(
            dictCtx["docker"].fbaFetchFile,
            sContainerId, sFilePath,
        )
        return baContent.decode("utf-8")
    except Exception:
        return None


def _fsetCollectCurrentStepOutputs(
    dictWorkflow, iCurrentStep,
):
    """Return basenames of outputs produced by the current step."""
    listSteps = dictWorkflow.get("listSteps", [])
    if iCurrentStep >= len(listSteps):
        return set()
    dictStep = listSteps[iCurrentStep]
    setOutputs = set()
    for sFile in dictStep.get("saOutputDataFiles", []):
        setOutputs.add(os.path.basename(sFile))
    for sFile in dictStep.get("saPlotFiles", []):
        setOutputs.add(os.path.basename(sFile))
    return setOutputs


def _flistFilterOwnOutputs(listDetected, setOwnOutputs):
    """Remove detected files matching a current step output."""
    listFiltered = []
    for dictItem in listDetected:
        sBasename = os.path.basename(dictItem["sFileName"])
        if sBasename not in setOwnOutputs:
            listFiltered.append(dictItem)
    return listFiltered


def _fdictCrossReferenceFiles(
    listDetected, dictWorkflow, iCurrentStep,
):
    """Match detected filenames against upstream step outputs."""
    dictStemRegistry = workflowManager.fdictBuildStemRegistry(
        dictWorkflow)
    setOwnOutputs = _fsetCollectCurrentStepOutputs(
        dictWorkflow, iCurrentStep,
    )
    listDetected = _flistFilterOwnOutputs(
        listDetected, setOwnOutputs)
    listSuggestions = []
    listUnmatchedFiles = []
    for dictItem in listDetected:
        _fnClassifyDetectedItem(
            dictItem, dictStemRegistry, dictWorkflow,
            iCurrentStep, listSuggestions, listUnmatchedFiles,
        )
    return {
        "listSuggestions": listSuggestions,
        "listUnmatchedFiles": listUnmatchedFiles,
    }


def _fnClassifyDetectedItem(
    dictItem, dictStemRegistry, dictWorkflow,
    iCurrentStep, listSuggestions, listUnmatchedFiles,
):
    """Sort a detected file into suggestions or unmatched."""
    sFileName = dictItem["sFileName"]
    sStem = os.path.splitext(os.path.basename(sFileName))[0]
    dictMatch = _fdictFindStemMatch(
        sStem, dictStemRegistry, dictWorkflow, iCurrentStep
    )
    if dictMatch:
        dictMatch.update({
            "sFileName": sFileName,
            "sFoundInScript": dictItem.get(
                "sFoundInScript", ""),
            "sLoadFunction": dictItem["sLoadFunction"],
            "iLineNumber": dictItem["iLineNumber"],
        })
        listSuggestions.append(dictMatch)
    else:
        listUnmatchedFiles.append({
            "sFileName": sFileName,
            "sLoadFunction": dictItem["sLoadFunction"],
            "iLineNumber": dictItem["iLineNumber"],
            "sFoundInScript": dictItem.get(
                "sFoundInScript", ""),
        })


def _fdictFindStemMatch(
    sStem, dictStemRegistry, dictWorkflow, iCurrentStep,
):
    """Return match dict if sStem maps to upstream step output."""
    # Symbolic {step:<id>.<stem>} keys first: the positional form is
    # deprecated, and a suggestion becomes the token the researcher saves.
    for sKey, iStepNumber in sorted(
            dictStemRegistry.items(),
            key=lambda tItem: not tItem[0].startswith("step:")):
        iStepIndex = iStepNumber - 1
        if iStepIndex >= iCurrentStep:
            continue
        sRegistryStem = (
            sKey.split(".", 1)[1] if "." in sKey else "")
        if sRegistryStem == sStem:
            sStepName = dictWorkflow[
                "listSteps"][iStepIndex].get(
                "sName", f"Step {iStepNumber}"
            )
            return {
                "iSourceStep": iStepNumber,
                "sSourceStepName": sStepName,
                "sTemplateVariable": "{" + sKey + "}",
                "sResolvedPath": sKey,
            }
    return None


def _fnStoreCommitHash(
    dictWorkflow, listFilePaths, sCommitHash,
):
    """Store the git commit hash in sync status for each file.

    ``dictSyncStatus`` keys are repo-relative (see
    ``workflowManager.fnUpdateSyncStatus``), so each incoming path is
    normalized with ``fsToSyncStatusKey`` before lookup; raw absolute
    container paths would otherwise silently miss every entry.
    """
    dictSync = dictWorkflow.get("dictSyncStatus", {})
    sProjectRepoPath = dictWorkflow.get("sProjectRepoPath", "")
    for sPath in listFilePaths:
        sKey = workflowManager.fsToSyncStatusKey(
            sPath, sProjectRepoPath,
        )
        if sKey in dictSync:
            dictSync[sKey]["sGithubCommit"] = sCommitHash


def _fnRegisterDependencyReconciliation(app, dictCtx):
    """Register the dependency rescan and the researcher's dismissal."""

    @ffnAgentAction("rescan-dependencies")
    @app.post("/api/workflow/{sContainerId}/dependencies/rescan")
    @ffnDeclareCarrierMode(S_CARRIER_TYPED_READ)
    async def fdictRescanDependencies(sContainerId: str):
        """Scan every step's scripts again; read-only, results in hub memory."""
        from ..pipelineServer import _fnScanDependenciesBackground
        dictCtx["require"](sContainerId)
        dictWorkflow = fdictRequireWorkflow(
            dictCtx["workflows"], sContainerId)
        await _fnScanDependenciesBackground(
            dictCtx, sContainerId, dictWorkflow)
        return {"dictSteps": {
            dictStep.get("sStepId"): {
                "sState": dictStep[S_SCAN_ANNOTATION_KEY]["sState"],
                "iFindings": len(
                    dictStep[S_SCAN_ANNOTATION_KEY]["listFindings"]),
                "sError": dictStep[S_SCAN_ANNOTATION_KEY]["sError"],
            }
            for dictStep in dictWorkflow.get("listSteps", []) or []
            if isinstance(dictStep, dict)
            and S_SCAN_ANNOTATION_KEY in dictStep}}

    @ffnAgentAction("dismiss-dependency-finding")
    @app.post(
        "/api/steps/{sContainerId}/{iStepIndex}/dependencies/dismiss")
    @ffnDeclareCarrierMode(S_CARRIER_MODE_A_SYNCHRONOUS)
    async def fdictDismissDependencyFinding(
        sContainerId: str, iStepIndex: int,
        request: DependencyDismissalRequest, requestHttp: Request,
    ):
        """Record that the researcher reviewed a finding and it is no edge."""
        dictCtx["require"](sContainerId)
        dictWorkflow = fdictRequireWorkflow(
            dictCtx["workflows"], sContainerId)
        dictStep = _fdictRequireStep(dictWorkflow, iStepIndex)
        dictFinding = _fdictRequireCurrentFinding(
            dictCtx, sContainerId, dictWorkflow, iStepIndex, dictStep,
            request)
        listDismissals = [
            dictEntry for dictEntry in dictStep.get(S_DISMISSALS_KEY, [])
            or [] if not (
                dictEntry.get("sScannedPath") == request.sScannedPath
                and dictEntry.get("sUpstreamStepId")
                == request.sUpstreamStepId)]
        listDismissals.append({
            sKey: dictFinding[sKey] for sKey in (
                "sScannedPath", "sUpstreamStepId", "sScriptPath",
                "sScriptSha256")})
        dictStep[S_DISMISSALS_KEY] = listDismissals
        fdictCommitWorkflowSave(
            dictCtx, sContainerId, dictWorkflow, requestHttp,
            "The dependency dismissal",
        )
        return {S_DISMISSALS_KEY: listDismissals}


def _fdictRequireStep(dictWorkflow, iStepIndex):
    """Return the step, or 404 by index."""
    listSteps = dictWorkflow.get("listSteps", []) or []
    if iStepIndex < 0 or iStepIndex >= len(listSteps):
        raise HTTPException(404, f"Step index {iStepIndex} out of range")
    return listSteps[iStepIndex]


def _fdictRequireCurrentFinding(
    dictCtx, sContainerId, dictWorkflow, iStepIndex, dictStep, request,
):
    """Return the finding being dismissed, refusing one that is not current.

    A dismissal binds the script hash it was made against, so it may
    only be made against a finding that still describes the script:
    dismissing a stale finding would record a review of code nobody saw.
    """
    from ...reproducibility.levelGates import _fdictDependencyStatesByStep
    listFindings = (dictStep.get(S_SCAN_ANNOTATION_KEY) or {}).get(
        "listFindings", []) or []
    dictFinding = next((
        dictEntry for dictEntry in listFindings
        if dictEntry.get("sScannedPath") == request.sScannedPath
        and dictEntry.get("sUpstreamStepId") == request.sUpstreamStepId),
        None)
    if dictFinding is None:
        raise HTTPException(
            404, f"No dependency finding for {request.sScannedPath} from "
            f"step '{request.sUpstreamStepId}' on this step. Rescan "
            "dependencies, then dismiss what the scan reports.")
    dictStates = _fdictDependencyStatesByStep(
        dictWorkflow, ffilesForWorkflow(dictCtx, sContainerId, dictWorkflow))
    if (dictStates.get(iStepIndex) or {}).get("sState") == (
            S_DEPENDENCY_NOT_CURRENT):
        raise HTTPException(
            409, "The dependency scan of this step is not current ("
            + dictStates[iStepIndex]["sReason"] + "). Rescan dependencies "
            "before dismissing a finding.")
    return dictFinding


def fnRegisterAll(app, dictCtx):
    """Register all script detection routes."""
    _fnRegisterScriptRoutes(app, dictCtx)
    _fnRegisterDependencyReconciliation(app, dictCtx)
