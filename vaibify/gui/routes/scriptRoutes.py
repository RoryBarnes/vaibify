"""Script detection and dependency scanning route handlers."""

__all__ = ["fnRegisterAll", "fdictScanAllDependencies"]

import asyncio
import os
import posixpath

from fastapi import HTTPException

from .. import workflowManager
from ..pipelineRunner import fsShellQuote
from ..pipelineServer import (
    DependencyScanRequest,
    fdictRequireWorkflow,
)


def _fnRegisterScriptRoutes(app, dictCtx):
    """Register script listing and scanning routes."""

    @app.get("/api/sync/{sContainerId}/scripts")
    async def fnGetScripts(sContainerId: str):
        dictCtx["require"]()
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
    async def fnScanScripts(
        sContainerId: str, iStepIndex: int
    ):
        dictCtx["require"]()
        dictWorkflow = fdictRequireWorkflow(
            dictCtx["workflows"], sContainerId)
        dictStep = dictWorkflow["listSteps"][iStepIndex]
        sDirectory = _fsAbsoluteStepDirectory(
            dictStep, dictWorkflow,
        )
        iExit, sOutput = await asyncio.to_thread(
            dictCtx["docker"].ftResultExecuteCommand,
            sContainerId,
            f"find {fsShellQuote(sDirectory)} -maxdepth 1"
            f" -name '*.py' "
            f"-printf '%f\\n' 2>/dev/null || "
            f"ls {fsShellQuote(sDirectory)}/*.py 2>/dev/null"
            f" | xargs -n1 basename 2>/dev/null",
        )
        listFiles = [
            s.strip() for s in sOutput.strip().splitlines()
            if s.strip()
        ] if iExit == 0 and sOutput.strip() else []
        return workflowManager.fdictAutoDetectScripts(
            listFiles)

    @app.post(
        "/api/steps/{sContainerId}/{iStepIndex}"
        "/scan-dependencies"
    )
    async def fnScanDependencies(
        sContainerId: str,
        iStepIndex: int,
        request: DependencyScanRequest,
    ):
        dictCtx["require"]()
        dictWorkflow = fdictRequireWorkflow(
            dictCtx["workflows"], sContainerId)
        return await _fdictScanDependencies(
            dictCtx, sContainerId, iStepIndex,
            request.saDataCommands, dictWorkflow,
        )


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
    """Collect saDataFiles entries from steps preceding iStepIndex."""
    listUpstream = []
    listSteps = dictWorkflow.get("listSteps", [])
    for iIndex in range(min(iStepIndex, len(listSteps))):
        dictStep = listSteps[iIndex]
        sStepName = dictStep.get(
            "sName", f"Step {iIndex + 1}")
        iStepNumber = iIndex + 1
        for sFileName in dictStep.get("saDataFiles", []):
            sStem = os.path.splitext(
                os.path.basename(sFileName))[0]
            sTemplateVariable = (
                "{" + f"Step{iStepNumber:02d}.{sStem}" + "}"
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
    for sFile in dictStep.get("saDataFiles", []):
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
    for sKey, iStepNumber in dictStemRegistry.items():
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
    """Store the git commit hash in sync status for each file."""
    dictSync = dictWorkflow.get("dictSyncStatus", {})
    for sPath in listFilePaths:
        if sPath in dictSync:
            dictSync[sPath]["sGithubCommit"] = sCommitHash


def fnRegisterAll(app, dictCtx):
    """Register all script detection routes."""
    _fnRegisterScriptRoutes(app, dictCtx)
